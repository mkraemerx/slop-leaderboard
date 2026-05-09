"""High-level fork analysis pipeline (FR-02).

This module is the orchestration layer that ties storage, git, and the
job queue together. It is intentionally synchronous so it can run inside an
APScheduler thread (ASSUMPTION-007).
"""
from __future__ import annotations

import logging
import sqlite3
import traceback
from pathlib import Path
from typing import Callable

from . import jobs
from .db import transaction
from .git_ops import (
    CommitInfo, clone_or_fetch, commits_reachable_from,
    iter_new_commits, list_refs,
)
from .repos import update_sync_status
from .storage import repo_path


log = logging.getLogger("analysis")


# Type alias for the git callable so tests can swap in a fake.
SyncCallable = Callable[[str, Path], None]
IterCallable = Callable[[Path, set[str]], "list[CommitInfo] | "  # iterable
                                          "tuple[CommitInfo, ...]"]


def store_commits(conn: sqlite3.Connection, fork_id: int,
                  commits: list[CommitInfo]) -> int:
    """INSERT OR IGNORE every commit. Returns rows actually inserted.

    `INSERT OR IGNORE` makes the operation idempotent under restart: a commit
    inserted in a previous run is silently skipped on a retry, satisfying
    QR-02 ("restarting mid-analysis does not produce duplicate records").
    """
    if not commits:
        return 0
    inserted = 0
    with transaction(conn):
        for c in commits:
            cat = c.by_category
            def s(name: str, attr: str) -> int:
                v = cat.get(name)
                return getattr(v, attr) if v else 0
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO commits (
                    fork_id, sha, author_name, author_email, author_time,
                    is_merge, parent_count, files_changed, insertions, deletions,
                    code_insertions, code_deletions,
                    tests_insertions, tests_deletions,
                    docs_insertions, docs_deletions,
                    config_insertions, config_deletions
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fork_id, c.sha, c.author_name, c.author_email, c.author_time,
                    1 if c.is_merge else 0, c.parent_count,
                    c.files_changed, c.insertions, c.deletions,
                    s("code", "insertions"), s("code", "deletions"),
                    s("tests", "insertions"), s("tests", "deletions"),
                    s("docs", "insertions"), s("docs", "deletions"),
                    s("config", "insertions"), s("config", "deletions"),
                ),
            )
            inserted += cur.rowcount or 0
    return inserted


def rebuild_commit_refs(conn: sqlite3.Connection, fork_id: int,
                       local_path: Path) -> int:
    """Replace the (fork_id, commit, ref) mapping for `fork_id` based on the
    current state of the local clone. Returns the row count.

    Refs change between syncs (branches deleted, tags added), so we rebuild
    rather than diff. Done in a single transaction so concurrent readers see
    a consistent view.
    """
    refs = list_refs(local_path)
    rows: list[tuple[int, str, str, str]] = []
    for ref in refs:
        for sha in commits_reachable_from(local_path, ref.tip_sha):
            rows.append((fork_id, sha, ref.name, ref.ref_type))
    with transaction(conn):
        conn.execute("DELETE FROM commit_refs WHERE fork_id = ?", (fork_id,))
        if rows:
            conn.executemany(
                "INSERT OR IGNORE INTO commit_refs "
                "(fork_id, commit_sha, ref_name, ref_type) VALUES (?, ?, ?, ?)",
                rows,
            )
    return len(rows)


def known_commit_shas(conn: sqlite3.Connection, fork_id: int) -> set[str]:
    rows = conn.execute(
        "SELECT sha FROM commits WHERE fork_id = ?", (fork_id,)
    ).fetchall()
    return {r["sha"] for r in rows}


def analyze_fork(
    conn: sqlite3.Connection,
    fork_id: int,
    base_repos_dir: Path,
    *,
    sync: SyncCallable = clone_or_fetch,
    iter_commits=iter_new_commits,
) -> int:
    """Run a full analysis cycle for one fork.

    Returns the number of newly inserted commits. Raises any exception from
    the sync/iter callables; the caller is responsible for catching it and
    marking the fork's sync status accordingly (so existing data is left
    intact on failure — FR-02 AC3).
    """
    import time
    row = conn.execute(
        "SELECT id, url, owner, name FROM forks WHERE id = ?", (fork_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"no such fork: {fork_id}")

    label = f"{row['owner']}/{row['name']}"
    log.debug("analyzing fork %d (%s)", fork_id, label)
    started = time.monotonic()
    update_sync_status(conn, fork_id, "running")

    local = repo_path(base_repos_dir, row["owner"], row["name"])
    sync(row["url"], local)

    seen = known_commit_shas(conn, fork_id)
    new_commits = list(iter_commits(local, seen))
    inserted = store_commits(conn, fork_id, new_commits)
    # Refs are recomputed every sync so additions/deletions of branches/tags
    # are reflected (FR-03 + needed by FR-04).
    rebuild_commit_refs(conn, fork_id, local)
    log.debug("analyzed fork %d (%s): +%d commit(s) in %.1fs",
              fork_id, label, inserted, time.monotonic() - started)
    return inserted


def run_one_job(
    conn: sqlite3.Connection,
    base_repos_dir: Path,
    *,
    sync: SyncCallable = clone_or_fetch,
    iter_commits=iter_new_commits,
) -> jobs.Job | None:
    """Pop the next queued job, run it, and update fork + job status.

    Returns the (now finished) Job, or None if there was nothing to run.
    """
    job = jobs.claim_next(conn)
    if job is None:
        return None
    try:
        analyze_fork(conn, job.fork_id, base_repos_dir,
                     sync=sync, iter_commits=iter_commits)
    except Exception as exc:  # noqa: BLE001 — surface any failure as error
        short = f"{type(exc).__name__}: {exc}"
        full = f"{short}\n\n{traceback.format_exc()}"
        # Short message on the fork row (tooltip on /forks); full traceback
        # on the job row (rendered on /debug/jobs).
        update_sync_status(conn, job.fork_id, "error", error=short)
        jobs.mark_failed(conn, job.id, full)
        log.error("analysis failed for fork %d: %s", job.fork_id, short,
                  exc_info=exc)
        return jobs.get_job(conn, job.id)
    update_sync_status(conn, job.fork_id, "ok", mark_analysed=True)
    jobs.mark_done(conn, job.id)
    return jobs.get_job(conn, job.id)
