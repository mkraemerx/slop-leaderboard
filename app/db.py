from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA_VERSION = 3


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS root_repo (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    url TEXT NOT NULL,
    platform TEXT NOT NULL DEFAULT 'github',
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS forks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    root_repo_id INTEGER NOT NULL REFERENCES root_repo(id) ON DELETE CASCADE,
    url TEXT NOT NULL UNIQUE,
    owner TEXT NOT NULL,
    name TEXT NOT NULL,
    discovered_via TEXT NOT NULL CHECK (discovered_via IN ('api', 'manual')),
    sync_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (sync_status IN ('pending', 'running', 'ok', 'error')),
    sync_error TEXT,
    last_analysed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_forks_root_repo ON forks(root_repo_id);

CREATE TABLE IF NOT EXISTS commits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fork_id INTEGER NOT NULL REFERENCES forks(id) ON DELETE CASCADE,
    sha TEXT NOT NULL,
    author_name TEXT NOT NULL,
    author_email TEXT NOT NULL,
    author_time TEXT NOT NULL,
    is_merge INTEGER NOT NULL DEFAULT 0,
    parent_count INTEGER NOT NULL DEFAULT 0,
    files_changed INTEGER NOT NULL DEFAULT 0,
    insertions INTEGER NOT NULL DEFAULT 0,
    deletions INTEGER NOT NULL DEFAULT 0,
    code_insertions INTEGER NOT NULL DEFAULT 0,
    code_deletions INTEGER NOT NULL DEFAULT 0,
    tests_insertions INTEGER NOT NULL DEFAULT 0,
    tests_deletions INTEGER NOT NULL DEFAULT 0,
    docs_insertions INTEGER NOT NULL DEFAULT 0,
    docs_deletions INTEGER NOT NULL DEFAULT 0,
    config_insertions INTEGER NOT NULL DEFAULT 0,
    config_deletions INTEGER NOT NULL DEFAULT 0,
    UNIQUE (fork_id, sha)
);

CREATE INDEX IF NOT EXISTS idx_commits_fork ON commits(fork_id);
CREATE INDEX IF NOT EXISTS idx_commits_email ON commits(author_email);

-- Branches/tags that contain each commit, refreshed on every analysis run.
CREATE TABLE IF NOT EXISTS commit_refs (
    fork_id INTEGER NOT NULL REFERENCES forks(id) ON DELETE CASCADE,
    commit_sha TEXT NOT NULL,
    ref_name TEXT NOT NULL,
    ref_type TEXT NOT NULL CHECK (ref_type IN ('branch', 'tag')),
    PRIMARY KEY (fork_id, commit_sha, ref_name)
);

CREATE INDEX IF NOT EXISTS idx_commit_refs_lookup ON commit_refs(fork_id, ref_name);

CREATE TABLE IF NOT EXISTS analysis_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fork_id INTEGER NOT NULL REFERENCES forks(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'sync'
        CHECK (kind IN ('sync', 'full')),
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'done', 'failed')),
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    started_at TEXT,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON analysis_jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_fork ON analysis_jobs(fork_id);
"""


def connect(db_path: Path, *, check_same_thread: bool = True
            ) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None,
                            check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    cur = conn.execute("SELECT version FROM schema_version LIMIT 1")
    row = cur.fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    else:
        # Idempotent bump: schema files use IF NOT EXISTS, so a higher
        # in-code version simply records that the live schema now matches.
        conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
