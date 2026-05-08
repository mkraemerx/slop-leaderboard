"""FR-04 Exercises — branches/tags found in any fork but absent from root.

Discovery is a side-effect of the regular fork+root sync (ASSUMPTION-010).
This module is the read side: business logic that the web layer queries to
render the exercise list.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import git_ops
from .db import transaction
from .git_ops import commits_reachable_from, list_refs
from .repos import get_root_repo
from .storage import repo_path


@dataclass(frozen=True)
class Exercise:
    name: str
    ref_type: str          # "branch" | "tag"
    fork_count: int        # how many tracked forks have this ref
    first_author_name: str | None
    first_author_email: str | None
    first_commit_time: str | None


def refresh_root(conn: sqlite3.Connection, base_repos_dir: Path,
                 *, sync=git_ops.clone_or_fetch) -> int:
    """Clone/fetch the root repo and populate `root_refs` + `root_commits`.

    Must run before exercise queries become meaningful — without it every
    fork ref looks like an exercise.
    """
    root = get_root_repo(conn)
    if root is None:
        raise RuntimeError("no root repo configured")
    local = repo_path(base_repos_dir, root.owner, root.name)
    sync(root.url, local)

    refs = list_refs(local)
    all_shas: set[str] = set()
    for ref in refs:
        all_shas |= commits_reachable_from(local, ref.tip_sha)

    with transaction(conn):
        conn.execute("DELETE FROM root_refs")
        if refs:
            conn.executemany(
                "INSERT OR IGNORE INTO root_refs (ref_name, ref_type) "
                "VALUES (?, ?)",
                [(r.name, r.ref_type) for r in refs],
            )
        conn.execute("DELETE FROM root_commits")
        if all_shas:
            conn.executemany(
                "INSERT OR IGNORE INTO root_commits (sha) VALUES (?)",
                [(s,) for s in all_shas],
            )
    return len(refs)


def list_exercises(conn: sqlite3.Connection) -> list[Exercise]:
    """Return every (ref_name, ref_type) present in any fork but absent in
    root, with fork count and first-author attribution."""
    # Fork-side ref counts, with the root refs subtracted.
    rows = conn.execute(
        """
        SELECT cr.ref_name, cr.ref_type,
               COUNT(DISTINCT cr.fork_id) AS fork_count
        FROM commit_refs cr
        WHERE NOT EXISTS (
            SELECT 1 FROM root_refs r
            WHERE r.ref_name = cr.ref_name AND r.ref_type = cr.ref_type
        )
        GROUP BY cr.ref_name, cr.ref_type
        ORDER BY cr.ref_type, cr.ref_name
        """
    ).fetchall()

    out: list[Exercise] = []
    for r in rows:
        first = _first_author(conn, r["ref_name"], r["ref_type"])
        out.append(Exercise(
            name=r["ref_name"],
            ref_type=r["ref_type"],
            fork_count=int(r["fork_count"]),
            first_author_name=first[0] if first else None,
            first_author_email=first[1] if first else None,
            first_commit_time=first[2] if first else None,
        ))
    return out


def _first_author(conn: sqlite3.Connection, ref_name: str, ref_type: str
                  ) -> tuple[str, str, str] | None:
    """The author of the earliest non-root commit reachable from this ref in
    any tracked fork. Returns (name, email, time) or None if every commit is
    shared with root (which would indicate a degenerate setup)."""
    row = conn.execute(
        """
        SELECT c.author_name, c.author_email, c.author_time
        FROM commit_refs cr
        JOIN commits c
          ON c.fork_id = cr.fork_id AND c.sha = cr.commit_sha
        WHERE cr.ref_name = ?
          AND cr.ref_type = ?
          AND c.sha NOT IN (SELECT sha FROM root_commits)
        ORDER BY c.author_time ASC, c.id ASC
        LIMIT 1
        """,
        (ref_name, ref_type),
    ).fetchone()
    if row is None:
        return None
    return (row["author_name"], row["author_email"], row["author_time"])
