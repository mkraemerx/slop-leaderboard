"""Acceptance tests for FR-01 Root Repository & Fork Tracking."""
from __future__ import annotations

import pytest

from app.github import GitHubClient
from app.repos import (
    add_fork_manual, discover_forks, get_root_repo, list_forks,
    remove_root_repo, set_root_repo, update_sync_status,
)
from tests.fakes import FakeHttp


# AC1: root repo is configured by URL + platform API token
def test_root_repo_is_configured_by_url(db):
    root = set_root_repo(db, "https://github.com/acme/root")

    assert root.url == "https://github.com/acme/root"
    assert root.owner == "acme"
    assert root.name == "root"
    assert root.platform == "github"
    assert get_root_repo(db) == root


def test_setting_root_repo_replaces_existing_one(db):
    set_root_repo(db, "https://github.com/old/root")
    root2 = set_root_repo(db, "https://github.com/new/root")

    assert get_root_repo(db) == root2
    # only one row ever exists
    assert db.execute("SELECT COUNT(*) FROM root_repo").fetchone()[0] == 1


# AC2: discovers all forks of the root automatically and adds them
def test_discover_forks_adds_api_returned_forks(db):
    set_root_repo(db, "https://github.com/acme/root")
    payload = [
        {"owner": {"login": "alice"}, "name": "root",
         "html_url": "https://github.com/alice/root"},
        {"owner": {"login": "bob"}, "name": "root",
         "html_url": "https://github.com/bob/root"},
    ]
    gh = GitHubClient(token="t", http=FakeHttp(pages=[payload]))

    added = discover_forks(db, gh)

    assert {f.owner for f in added} == {"alice", "bob"}
    forks = list_forks(db)
    assert {f.owner for f in forks} == {"alice", "bob"}
    assert all(f.discovered_via == "api" for f in forks)


def test_discover_forks_is_idempotent(db):
    set_root_repo(db, "https://github.com/acme/root")
    payload = [{"owner": {"login": "alice"}, "name": "root",
                "html_url": "https://github.com/alice/root"}]
    gh = GitHubClient(token="t", http=FakeHttp(pages=[payload]))

    discover_forks(db, gh)
    # second run with the same payload must not duplicate
    gh2 = GitHubClient(token="t", http=FakeHttp(pages=[payload]))
    added_again = discover_forks(db, gh2)

    assert added_again == []
    assert len(list_forks(db)) == 1


# AC3: forks can also be added manually by URL
def test_add_fork_manual_registers_a_fork(db):
    set_root_repo(db, "https://github.com/acme/root")

    fork = add_fork_manual(db, "https://github.com/private-user/root.git")

    assert fork.owner == "private-user"
    assert fork.discovered_via == "manual"
    assert fork.url == "https://github.com/private-user/root"


def test_manual_fork_cannot_duplicate_existing_url(db):
    import sqlite3
    set_root_repo(db, "https://github.com/acme/root")
    add_fork_manual(db, "https://github.com/alice/root")

    with pytest.raises(sqlite3.IntegrityError):
        add_fork_manual(db, "https://github.com/alice/root")


def test_manual_fork_url_must_differ_from_root(db):
    set_root_repo(db, "https://github.com/acme/root")
    with pytest.raises(ValueError):
        add_fork_manual(db, "https://github.com/acme/root")


# AC4: tracked list shows owner, sync status, last analysed time
def test_listing_forks_shows_owner_status_and_last_analysed(db):
    set_root_repo(db, "https://github.com/acme/root")
    fork = add_fork_manual(db, "https://github.com/alice/root")

    listed = list_forks(db)
    [seen] = listed
    assert seen.owner == "alice"
    assert seen.sync_status == "pending"        # default for newly added
    assert seen.last_analysed_at is None

    update_sync_status(db, fork.id, "ok", mark_analysed=True)
    [updated] = list_forks(db)
    assert updated.sync_status == "ok"
    assert updated.last_analysed_at is not None
    assert updated.sync_error is None


def test_update_sync_status_records_error(db):
    set_root_repo(db, "https://github.com/acme/root")
    fork = add_fork_manual(db, "https://github.com/alice/root")

    update_sync_status(db, fork.id, "error", error="clone failed: timeout")

    [seen] = list_forks(db)
    assert seen.sync_status == "error"
    assert seen.sync_error == "clone failed: timeout"


def test_update_sync_status_rejects_unknown_state(db):
    set_root_repo(db, "https://github.com/acme/root")
    fork = add_fork_manual(db, "https://github.com/alice/root")
    with pytest.raises(ValueError):
        update_sync_status(db, fork.id, "bogus")


# AC5: removing the root repo removes all forks and their data
def test_removing_root_repo_cascades_forks(db):
    set_root_repo(db, "https://github.com/acme/root")
    add_fork_manual(db, "https://github.com/alice/root")
    add_fork_manual(db, "https://github.com/bob/root")

    remove_root_repo(db)

    assert get_root_repo(db) is None
    assert list_forks(db) == []


# Mixed-source: API discovery + manual addition can coexist
def test_api_and_manual_forks_can_coexist(db):
    set_root_repo(db, "https://github.com/acme/root")
    payload = [{"owner": {"login": "alice"}, "name": "root",
                "html_url": "https://github.com/alice/root"}]
    gh = GitHubClient(token="t", http=FakeHttp(pages=[payload]))
    discover_forks(db, gh)

    add_fork_manual(db, "https://github.com/private/root")

    forks = list_forks(db)
    by_owner = {f.owner: f for f in forks}
    assert by_owner["alice"].discovered_via == "api"
    assert by_owner["private"].discovered_via == "manual"


def test_adding_fork_without_root_repo_fails(db):
    with pytest.raises(RuntimeError):
        add_fork_manual(db, "https://github.com/alice/root")
