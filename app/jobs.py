"""Persistent job queue for fork analysis (FR-02 + QR-02).

A job is just a row in `analysis_jobs`. The queue is backed by SQLite so the
queue survives process restarts. On startup, any rows still marked `running`
are reset to `queued` so they will be re-executed.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .db import transaction


@dataclass(frozen=True)
class Job:
    id: int
    fork_id: int
    kind: str
    status: str
    error: str | None


def enqueue_analysis(conn: sqlite3.Connection, fork_id: int, *, kind: str = "sync") -> int:
    """Insert a queued analysis job and return its id."""
    if kind not in ("sync", "full"):
        raise ValueError(f"invalid job kind: {kind!r}")
    with transaction(conn):
        cur = conn.execute(
            "INSERT INTO analysis_jobs (fork_id, kind, status) VALUES (?, ?, 'queued')",
            (fork_id, kind),
        )
        return int(cur.lastrowid)


def claim_next(conn: sqlite3.Connection) -> Job | None:
    """Atomically transition the oldest queued job to `running` and return it.

    SQLite serialises writers, so the BEGIN IMMEDIATE/UPDATE pattern is safe
    even with multiple background workers (per ASSUMPTION-007 we only run one,
    but the operation is still race-free).
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            """
            SELECT id, fork_id, kind FROM analysis_jobs
            WHERE status = 'queued'
            ORDER BY id
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            conn.execute("ROLLBACK")
            return None
        conn.execute(
            """
            UPDATE analysis_jobs
            SET status = 'running', started_at = datetime('now'), error = NULL
            WHERE id = ?
            """,
            (row["id"],),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return Job(id=row["id"], fork_id=row["fork_id"], kind=row["kind"],
               status="running", error=None)


def mark_done(conn: sqlite3.Connection, job_id: int) -> None:
    with transaction(conn):
        conn.execute(
            """
            UPDATE analysis_jobs
            SET status = 'done', finished_at = datetime('now'), error = NULL
            WHERE id = ?
            """,
            (job_id,),
        )


def mark_failed(conn: sqlite3.Connection, job_id: int, error: str) -> None:
    with transaction(conn):
        conn.execute(
            """
            UPDATE analysis_jobs
            SET status = 'failed', finished_at = datetime('now'), error = ?
            WHERE id = ?
            """,
            (error, job_id),
        )


def requeue_orphans(conn: sqlite3.Connection) -> int:
    """Reset jobs that were `running` at shutdown back to `queued`.

    Called on application startup. Returns the count of rows affected.
    Required by QR-02 (jobs running at shutdown must be re-executed) and safe
    to call repeatedly because INSERTs of new commits use INSERT OR IGNORE.
    """
    with transaction(conn):
        cur = conn.execute(
            """
            UPDATE analysis_jobs
            SET status = 'queued', started_at = NULL, error = NULL
            WHERE status = 'running'
            """
        )
        return int(cur.rowcount or 0)


def get_job(conn: sqlite3.Connection, job_id: int) -> Job | None:
    row = conn.execute(
        "SELECT id, fork_id, kind, status, error FROM analysis_jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    if row is None:
        return None
    return Job(id=row["id"], fork_id=row["fork_id"], kind=row["kind"],
               status=row["status"], error=row["error"])


def jobs_for_fork(conn: sqlite3.Connection, fork_id: int) -> list[Job]:
    rows = conn.execute(
        """
        SELECT id, fork_id, kind, status, error FROM analysis_jobs
        WHERE fork_id = ?
        ORDER BY id
        """,
        (fork_id,),
    ).fetchall()
    return [Job(id=r["id"], fork_id=r["fork_id"], kind=r["kind"],
                status=r["status"], error=r["error"]) for r in rows]


@dataclass(frozen=True)
class FailedJobRow:
    """A failed analysis job joined with its fork's identity (for /debug/jobs)."""
    job_id: int
    fork_id: int
    fork_owner: str
    fork_name: str
    fork_url: str
    kind: str
    error: str
    started_at: str | None
    finished_at: str | None


def recent_failed_jobs(conn: sqlite3.Connection, limit: int = 100,
                        ) -> list[FailedJobRow]:
    """Most recent `limit` failed jobs joined with fork identity.

    Used by the /debug/jobs page so the operator can see full tracebacks
    without scraping the uvicorn console.
    """
    rows = conn.execute(
        """
        SELECT j.id AS job_id, j.fork_id, j.kind, j.error,
               j.started_at, j.finished_at,
               f.owner AS fork_owner, f.name AS fork_name, f.url AS fork_url
        FROM analysis_jobs j
        JOIN forks f ON f.id = j.fork_id
        WHERE j.status = 'failed'
        ORDER BY j.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        FailedJobRow(
            job_id=r["job_id"], fork_id=r["fork_id"],
            fork_owner=r["fork_owner"], fork_name=r["fork_name"],
            fork_url=r["fork_url"], kind=r["kind"],
            error=r["error"] or "",
            started_at=r["started_at"], finished_at=r["finished_at"],
        )
        for r in rows
    ]
