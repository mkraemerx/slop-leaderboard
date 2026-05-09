"""Tests for author alias + ignored-author identity normalisation."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import aliases, leaderboard
from app.repos import add_fork_manual, set_root_repo
from app import jobs

from tests.test_ui import _build_app, _login


# --- aliases module --------------------------------------------------------

def test_add_alias_round_trips(db):
    aliases.add_alias(db, "a@personal", "a@work", "Alice")
    [row] = aliases.list_aliases(db)
    assert row.alias_email == "a@personal"
    assert row.canonical_email == "a@work"
    assert row.display_name == "Alice"


def test_add_alias_is_case_insensitive(db):
    aliases.add_alias(db, "A@Personal", "a@work")
    # Re-add with different case → updates, doesn't duplicate
    aliases.add_alias(db, "a@personal", "b@work")
    rows = aliases.list_aliases(db)
    assert len(rows) == 1
    assert rows[0].canonical_email == "b@work"


def test_add_alias_rejects_self_loop(db):
    with pytest.raises(ValueError):
        aliases.add_alias(db, "a@x", "a@x")


def test_add_alias_rejects_empty(db):
    with pytest.raises(ValueError):
        aliases.add_alias(db, "", "x@y")


def test_remove_alias_is_idempotent(db):
    aliases.add_alias(db, "a@b", "c@d")
    aliases.remove_alias(db, "a@b")
    aliases.remove_alias(db, "a@b")  # no error
    assert aliases.list_aliases(db) == []


def test_ignore_author_round_trips(db):
    aliases.ignore_author(db, "bot@example.com")
    assert aliases.list_ignored(db) == ["bot@example.com"]
    aliases.unignore_author(db, "bot@example.com")
    assert aliases.list_ignored(db) == []


def test_ignore_author_is_idempotent(db):
    aliases.ignore_author(db, "bot@example.com")
    aliases.ignore_author(db, "bot@example.com")  # no error
    assert len(aliases.list_ignored(db)) == 1


# --- leaderboard integration -----------------------------------------------

def _seed_commits(db, fork_id: int, entries: list[tuple[str, str, str]]):
    """Insert raw commits. Each entry is (sha, email, name)."""
    for sha, email, name in entries:
        db.execute(
            """INSERT INTO commits (fork_id, sha, author_name, author_email,
                                    author_time, is_merge, parent_count,
                                    files_changed, insertions, deletions)
               VALUES (?, ?, ?, ?, ?, 0, 1, 1, 10, 0)""",
            (fork_id, sha, name, email, "2026-05-01T12:00:00Z"),
        )


def test_aliases_merge_authors_on_leaderboard(db):
    set_root_repo(db, "https://github.com/acme/root")
    fork = add_fork_manual(db, "https://github.com/alice/root")
    jobs.mark_done(db, jobs.claim_next(db).id)
    _seed_commits(db, fork.id, [
        ("a" * 40, "alice@personal", "Alice"),
        ("b" * 40, "alice@work",     "Alice S"),
        ("c" * 40, "bob@x",          "Bob"),
    ])

    # Without alias: 3 distinct emails, so 3 leaderboard rows.
    rows = leaderboard.compute_leaderboard(db)
    assert {r.author_email for r in rows} == {
        "alice@personal", "alice@work", "bob@x",
    }

    # Add alias: alice@personal → alice@work
    aliases.add_alias(db, "alice@personal", "alice@work", "Alice")

    rows = leaderboard.compute_leaderboard(db)
    by_email = {r.author_email: r for r in rows}
    # Alice's two commits collapsed into the canonical address.
    assert "alice@personal" not in by_email
    assert by_email["alice@work"].commits == 2
    assert by_email["alice@work"].author_name == "Alice"
    # Insertions also summed: 10 + 10 = 20.
    assert by_email["alice@work"].insertions == 20


def test_ignored_authors_disappear_from_leaderboard(db):
    set_root_repo(db, "https://github.com/acme/root")
    fork = add_fork_manual(db, "https://github.com/alice/root")
    jobs.mark_done(db, jobs.claim_next(db).id)
    _seed_commits(db, fork.id, [
        ("a" * 40, "alice@work",  "Alice"),
        ("b" * 40, "bot@ci",      "ci-bot"),
    ])

    aliases.ignore_author(db, "bot@ci")
    rows = leaderboard.compute_leaderboard(db)
    emails = {r.author_email for r in rows}
    assert "bot@ci" not in emails
    assert "alice@work" in emails


def test_ignore_applies_to_canonical_email(db):
    """Ignoring the canonical email also hides commits authored under
    any of its aliases."""
    set_root_repo(db, "https://github.com/acme/root")
    fork = add_fork_manual(db, "https://github.com/alice/root")
    jobs.mark_done(db, jobs.claim_next(db).id)
    _seed_commits(db, fork.id, [
        ("a" * 40, "alice@personal", "Alice"),
        ("b" * 40, "alice@work",     "Alice"),
    ])
    aliases.add_alias(db, "alice@personal", "alice@work")
    aliases.ignore_author(db, "alice@work")

    rows = leaderboard.compute_leaderboard(db)
    assert rows == []


def test_distinct_commit_emails_counts_after_dedup(db):
    set_root_repo(db, "https://github.com/acme/root")
    fork1 = add_fork_manual(db, "https://github.com/a/root")
    jobs.mark_done(db, jobs.claim_next(db).id)
    fork2 = add_fork_manual(db, "https://github.com/b/root")
    jobs.mark_done(db, jobs.claim_next(db).id)

    # Same SHA in two forks (e.g. instructor's init) — should count once.
    _seed_commits(db, fork1.id, [
        ("a" * 40, "ins@example", "Instructor"),
        ("b" * 40, "ins@example", "Instructor"),
    ])
    _seed_commits(db, fork2.id, [
        ("a" * 40, "ins@example", "Instructor"),  # duplicate SHA
        ("c" * 40, "alice@x",     "Alice"),
    ])

    stats = aliases.distinct_commit_emails(db)
    by_email = {s.email: s for s in stats}
    assert by_email["ins@example"].commits == 2  # not 3
    assert by_email["alice@x"].commits == 1


# --- admin UI --------------------------------------------------------------

def test_admin_aliases_page_renders(tmp_path: Path):
    app = _build_app(tmp_path)
    aliases.add_alias(app.state.db, "alice@personal", "alice@work", "Alice")
    aliases.ignore_author(app.state.db, "bot@ci")
    with TestClient(app) as client:
        _login(client)
        resp = client.get("/admin/aliases")
        assert resp.status_code == 200
        body = resp.text
        assert "alice@personal" in body
        assert "alice@work" in body
        assert "bot@ci" in body


def test_admin_aliases_page_requires_auth(tmp_path: Path):
    app = _build_app(tmp_path)
    with TestClient(app, follow_redirects=False) as client:
        resp = client.get("/admin/aliases")
        assert resp.status_code == 303
        assert resp.headers["location"] == "/login"


def test_admin_can_add_alias_via_form(tmp_path: Path):
    app = _build_app(tmp_path)
    with TestClient(app, follow_redirects=False) as client:
        _login(client)
        resp = client.post(
            "/admin/aliases/add",
            data={"alias_email": "a@x", "canonical_email": "a@y",
                  "display_name": "Alice"},
        )
        assert resp.status_code == 303
    rows = aliases.list_aliases(app.state.db)
    assert len(rows) == 1
    assert rows[0].alias_email == "a@x"


def test_admin_can_delete_alias_via_form(tmp_path: Path):
    app = _build_app(tmp_path)
    aliases.add_alias(app.state.db, "a@x", "a@y")
    with TestClient(app, follow_redirects=False) as client:
        _login(client)
        client.post("/admin/aliases/delete",
                     data={"alias_email": "a@x"})
    assert aliases.list_aliases(app.state.db) == []


def test_admin_can_ignore_and_unignore_via_form(tmp_path: Path):
    app = _build_app(tmp_path)
    with TestClient(app, follow_redirects=False) as client:
        _login(client)
        client.post("/admin/aliases/ignore", data={"email": "bot@x"})
        assert "bot@x" in aliases.list_ignored(app.state.db)
        client.post("/admin/aliases/unignore", data={"email": "bot@x"})
        assert "bot@x" not in aliases.list_ignored(app.state.db)


def test_admin_add_alias_rejects_self_loop(tmp_path: Path):
    app = _build_app(tmp_path)
    with TestClient(app, follow_redirects=False) as client:
        _login(client)
        resp = client.post(
            "/admin/aliases/add",
            data={"alias_email": "a@x", "canonical_email": "a@x"},
        )
        assert resp.status_code == 400
