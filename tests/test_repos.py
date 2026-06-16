"""Acceptance tests for FR-01 Template & Participant Repository Tracking."""
from __future__ import annotations

import pytest

from app.aliases import add_alias, list_aliases
from app.github import RepoRef
from app.repos import (
    add_fork_manual, discover_forks, get_root_repo, list_forks,
    remove_root_repo, reset_all, set_root_repo, update_sync_status,
)
from tests.fakes import FakeGitHub


def _ref(owner: str, name: str, **kw) -> RepoRef:
    return RepoRef(owner=owner, name=name,
                   url=f"https://github.com/{owner}/{name}", **kw)


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


# AC2: discovers participant clones in the template's org automatically
def test_discover_forks_adds_org_repos_that_share_the_root(db):
    set_root_repo(db, "https://github.com/acme/root")
    gh = FakeGitHub(org_repos=[
        _ref("acme", "root"),          # the template itself — must be skipped
        _ref("acme", "alice-clone"),
        _ref("acme", "bob-clone"),
    ])

    added = discover_forks(db, gh, root_shas={"abc"})

    assert {f.name for f in added} == {"alice-clone", "bob-clone"}
    forks = list_forks(db)
    assert {f.name for f in forks} == {"alice-clone", "bob-clone"}
    assert all(f.discovered_via == "api" for f in forks)


def test_discover_skips_template_excluded_and_unrelated(db):
    set_root_repo(db, "https://github.com/acme/root")
    gh = FakeGitHub(
        org_repos=[
            _ref("acme", "other-template", is_template=True),  # flagged template
            _ref("acme", "infra"),                              # on exclude list
            _ref("acme", "stranger"),                           # no shared root
            _ref("acme", "clone"),                              # genuine clone
        ],
        contains={"acme/infra", "acme/other-template", "acme/clone"},
    )

    added = discover_forks(db, gh, root_shas={"abc"}, exclude={"infra"})

    assert {f.name for f in added} == {"clone"}


def test_discover_forks_is_idempotent(db):
    set_root_repo(db, "https://github.com/acme/root")
    org = [_ref("acme", "alice-clone")]

    discover_forks(db, FakeGitHub(org_repos=org), root_shas={"abc"})
    added_again = discover_forks(db, FakeGitHub(org_repos=org), root_shas={"abc"})

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


# Mixed-source: org discovery + manual addition can coexist
def test_api_and_manual_forks_can_coexist(db):
    set_root_repo(db, "https://github.com/acme/root")
    gh = FakeGitHub(org_repos=[_ref("acme", "alice-clone")])
    discover_forks(db, gh, root_shas={"abc"})

    add_fork_manual(db, "https://github.com/private/clone")

    by_owner = {f.owner: f for f in list_forks(db)}
    assert by_owner["acme"].discovered_via == "api"
    assert by_owner["private"].discovered_via == "manual"


def test_adding_fork_without_root_repo_fails(db):
    with pytest.raises(RuntimeError):
        add_fork_manual(db, "https://github.com/alice/root")


# Manual add applies the same shared-root-commit check when verification is on
def test_manual_add_rejects_repo_not_sharing_template_history(db):
    set_root_repo(db, "https://github.com/acme/root")
    gh = FakeGitHub(contains=set())  # contains nothing → no shared root

    with pytest.raises(ValueError):
        add_fork_manual(db, "https://github.com/x/unrelated",
                        gh=gh, root_shas={"abc"})
    assert list_forks(db) == []


def test_manual_add_accepts_repo_sharing_template_history(db):
    set_root_repo(db, "https://github.com/acme/root")
    gh = FakeGitHub(contains={"x/clone"})

    fork = add_fork_manual(db, "https://github.com/x/clone",
                           gh=gh, root_shas={"abc"})

    assert fork.owner == "x"
    assert fork.discovered_via == "manual"


# Reset: wipe the setup for reuse, keep cohort-independent identity data
def test_reset_all_wipes_setup_but_preserves_aliases(db, tmp_path):
    set_root_repo(db, "https://github.com/acme/root")
    add_fork_manual(db, "https://github.com/alice/root")
    db.execute("INSERT INTO root_refs (ref_name, ref_type) VALUES ('main', 'branch')")
    db.execute("INSERT INTO root_commits (sha) VALUES ('deadbeef')")
    add_alias(db, "alias@x.com", "canonical@x.com")
    repos_dir = tmp_path / "repos"
    clone = repos_dir / "acme__root"
    clone.mkdir(parents=True)
    (clone / "HEAD").write_text("ref: refs/heads/main\n")

    reset_all(db, repos_dir)

    assert get_root_repo(db) is None
    assert list_forks(db) == []
    assert db.execute("SELECT COUNT(*) FROM root_refs").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM root_commits").fetchone()[0] == 0
    assert not clone.exists()
    assert len(list_aliases(db)) == 1  # preserved across reset
