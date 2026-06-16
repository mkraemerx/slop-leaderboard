"""Business logic for FR-01: template repo + participant-repo tracking.

The web layer (app.web) and any future background workers call into these
functions; SQL lives here so the boundaries from QR-04 stay clean.

Terminology: the single tracked "root_repo" row is the *template*; the
"forks" table holds the *participant repositories* (clones of that template,
bundled in one organisation). The schema keeps the older names; the concepts
map one-to-one.
"""
from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from . import git_ops, jobs
from .db import transaction
from .github import GitHubClient, GitHubError, RepoRef, parse_github_url
from .storage import repo_path


@dataclass(frozen=True)
class RootRepo:
    id: int
    url: str
    platform: str
    owner: str
    name: str


@dataclass(frozen=True)
class Fork:
    id: int
    root_repo_id: int
    url: str
    owner: str
    name: str
    discovered_via: str
    sync_status: str
    sync_error: str | None
    last_analysed_at: str | None


def get_root_repo(conn: sqlite3.Connection) -> RootRepo | None:
    row = conn.execute(
        "SELECT id, url, platform, owner, name FROM root_repo WHERE id = 1"
    ).fetchone()
    if row is None:
        return None
    return RootRepo(
        id=row["id"], url=row["url"], platform=row["platform"],
        owner=row["owner"], name=row["name"],
    )


def set_root_repo(conn: sqlite3.Connection, url: str) -> RootRepo:
    """Configure (or replace) the single tracked root repo."""
    ref = parse_github_url(url)
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO root_repo (id, url, platform, owner, name)
            VALUES (1, ?, 'github', ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                url = excluded.url,
                platform = excluded.platform,
                owner = excluded.owner,
                name = excluded.name
            """,
            (ref.url, ref.owner, ref.name),
        )
    root = get_root_repo(conn)
    assert root is not None
    return root


def remove_root_repo(conn: sqlite3.Connection) -> None:
    """Delete the root repo. Foreign-key cascade removes all forks."""
    with transaction(conn):
        conn.execute("DELETE FROM root_repo WHERE id = 1")


def template_root_shas(repos_dir: Path, root: "RootRepo") -> set[str]:
    """Root-commit SHAs of the locally cloned template, or an empty set if the
    template has not been fetched yet. Used as the lineage filter for both
    discovery and manual adds."""
    local = repo_path(repos_dir, root.owner, root.name)
    try:
        return git_ops.root_commit_shas(local)
    except Exception:  # noqa: BLE001 — missing/corrupt clone → "can't verify"
        return set()


def repo_shares_root(gh: GitHubClient, ref: RepoRef, root_shas: set[str]) -> bool:
    """True if `ref` contains any of the template's root commits (one API call
    per candidate SHA, short-circuiting on the first hit)."""
    for sha in root_shas:
        if gh.repo_contains_commit(ref.owner, ref.name, sha):
            return True
    return False


def reset_all(conn: sqlite3.Connection, repos_dir: Path) -> None:
    """Wipe everything tied to the current setup so the installation can be
    reused: the template, every participant repo and its analysis data
    (FK cascade), the root ref/commit caches, and the local clones on disk.

    Author aliases and the ignore list are deliberately *preserved* — they are
    cohort-independent identity data (clearing them is a separate action).
    """
    with transaction(conn):
        conn.execute("DELETE FROM root_repo WHERE id = 1")  # cascades forks → …
        conn.execute("DELETE FROM root_refs")
        conn.execute("DELETE FROM root_commits")
    if repos_dir.exists():
        for child in repos_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                try:
                    child.unlink()
                except OSError:
                    pass


def list_forks(conn: sqlite3.Connection) -> list[Fork]:
    rows = conn.execute(
        """
        SELECT id, root_repo_id, url, owner, name, discovered_via,
               sync_status, sync_error, last_analysed_at
        FROM forks
        ORDER BY owner COLLATE NOCASE, name COLLATE NOCASE
        """
    ).fetchall()
    return [_row_to_fork(r) for r in rows]


def add_fork_manual(
    conn: sqlite3.Connection, url: str, *,
    gh: GitHubClient | None = None, root_shas: set[str] | None = None,
) -> Fork:
    """Add a participant repo by URL (for repos the org listing misses, e.g.
    ones living outside the template's org).

    When `gh` and `root_shas` are both supplied, the repo must share the
    template's root commit or a ValueError is raised — the same lineage check
    discovery applies. Without them (no token configured) the repo is trusted
    as entered.
    """
    ref = parse_github_url(url)
    if gh is not None and root_shas:
        if not repo_shares_root(gh, ref, root_shas):
            raise ValueError(
                f"{ref.full_name} does not share the template's history"
            )
    return _insert_fork(conn, ref, discovered_via="manual")


def discover_forks(
    conn: sqlite3.Connection, gh: GitHubClient, root_shas: set[str], *,
    exclude: frozenset[str] | set[str] = frozenset(),
) -> list[Fork]:
    """List the template org's repositories and add any genuine participant
    clones not already tracked.

    A repo is added only if it is not the template itself, not flagged as a
    template, not on the `exclude` list, and shares the template's root commit
    (`root_shas`, verified via one API call per repo).

    Returns the list of *newly* added repos. Existing ones are left intact —
    safe to call repeatedly. Raises GitHubError if the org listing fails; the
    caller decides whether to surface the error.
    """
    root = get_root_repo(conn)
    if root is None:
        raise RuntimeError("no root repo configured")
    refs = gh.list_org_repos(root.owner)
    added: list[Fork] = []
    for ref in refs:
        if ref.url == root.url or ref.is_template or ref.name in exclude:
            continue
        if not repo_shares_root(gh, ref, root_shas):
            continue  # unrelated repo that merely lives in the same org
        try:
            fork = _insert_fork(conn, ref, discovered_via="api")
        except sqlite3.IntegrityError:
            # already present (manual add or earlier discovery) — skip
            continue
        added.append(fork)
    return added


def update_sync_status(
    conn: sqlite3.Connection,
    fork_id: int,
    status: str,
    *,
    error: str | None = None,
    mark_analysed: bool = False,
) -> None:
    if status not in ("pending", "running", "ok", "error"):
        raise ValueError(f"invalid sync status: {status!r}")
    fields = ["sync_status = ?", "sync_error = ?"]
    params: list[object] = [status, error]
    if mark_analysed:
        fields.append("last_analysed_at = datetime('now')")
    sql = f"UPDATE forks SET {', '.join(fields)} WHERE id = ?"
    params.append(fork_id)
    with transaction(conn):
        conn.execute(sql, params)


# ----- internals -----------------------------------------------------------

def _insert_fork(
    conn: sqlite3.Connection, ref: RepoRef, *, discovered_via: str,
) -> Fork:
    root = get_root_repo(conn)
    if root is None:
        raise RuntimeError("no root repo configured")
    if ref.url == root.url:
        raise ValueError("a fork URL must differ from the root repo URL")
    with transaction(conn):
        cur = conn.execute(
            """
            INSERT INTO forks (root_repo_id, url, owner, name, discovered_via)
            VALUES (?, ?, ?, ?, ?)
            """,
            (root.id, ref.url, ref.owner, ref.name, discovered_via),
        )
        fork_id = cur.lastrowid
    # FR-02 AC1: every newly registered fork is enqueued for analysis
    # immediately. The worker picks it up on its next tick.
    jobs.enqueue_analysis(conn, fork_id, kind="full")
    row = conn.execute(
        """
        SELECT id, root_repo_id, url, owner, name, discovered_via,
               sync_status, sync_error, last_analysed_at
        FROM forks WHERE id = ?
        """,
        (fork_id,),
    ).fetchone()
    return _row_to_fork(row)


def _row_to_fork(row: sqlite3.Row) -> Fork:
    return Fork(
        id=row["id"],
        root_repo_id=row["root_repo_id"],
        url=row["url"],
        owner=row["owner"],
        name=row["name"],
        discovered_via=row["discovered_via"],
        sync_status=row["sync_status"],
        sync_error=row["sync_error"],
        last_analysed_at=row["last_analysed_at"],
    )


__all__ = [
    "RootRepo", "Fork",
    "get_root_repo", "set_root_repo", "remove_root_repo", "reset_all",
    "template_root_shas", "repo_shares_root",
    "list_forks", "add_fork_manual", "discover_forks", "update_sync_status",
]
