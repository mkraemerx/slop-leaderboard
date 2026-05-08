"""Business logic for FR-01: root repo + fork tracking.

The web layer (app.web) and any future background workers call into these
functions; SQL lives here so the boundaries from QR-04 stay clean.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterable

from .db import transaction
from .github import GitHubClient, GitHubError, RepoRef, parse_github_url


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


def add_fork_manual(conn: sqlite3.Connection, url: str) -> Fork:
    """Add a fork by URL (used when API discovery is unavailable or for
    private forks the platform API does not return)."""
    ref = parse_github_url(url)
    return _insert_fork(conn, ref, discovered_via="manual")


def discover_forks(conn: sqlite3.Connection, gh: GitHubClient) -> list[Fork]:
    """Call the platform API and add any forks not already tracked.

    Returns the list of *newly* added forks. Existing forks are left intact —
    this is safe to call repeatedly.

    Raises GitHubError if the API call fails; the caller decides whether to
    surface the error or fall back to manual addition.
    """
    root = get_root_repo(conn)
    if root is None:
        raise RuntimeError("no root repo configured")
    refs = gh.list_forks(root.owner, root.name)
    added: list[Fork] = []
    for ref in refs:
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
    "get_root_repo", "set_root_repo", "remove_root_repo",
    "list_forks", "add_fork_manual", "discover_forks", "update_sync_status",
]
