"""Background scheduler that ties the runtime pieces together.

Two interval jobs run inside a single APScheduler BackgroundScheduler:

- `_analysis_tick`: drains the analysis_jobs queue by calling
  `analysis.run_one_job` until it returns None. Runs every 30 seconds.
- `_discover_tick`: every `SYNC_INTERVAL_MINUTES`,
  (1) calls `repos.discover_forks` to pick up new forks of the root,
  (2) enqueues a fresh `sync` job for every existing fork that has no
      queued or running work — this both keeps fresh forks fresh and
      retries forks stuck in `error` state.

Each tick opens its own SQLite connection — the web layer's connection on
`app.state.db` is reserved for request handlers, and scheduler threads must
not share a writer with the request path (transaction isolation).
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler

from . import analysis, jobs, repos
from .config import Config
from .db import connect, init_schema
from .git_ops import clone_or_fetch
from .github import GitHubClient, GitHubError


log = logging.getLogger("scheduler")


class Scheduler:
    """Owns the BackgroundScheduler. `start()` is safe to call once;
    `shutdown()` is idempotent."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._scheduler = BackgroundScheduler(timezone="UTC")
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        with self._open_conn() as conn:
            recovered = jobs.requeue_orphans(conn)
            if recovered:
                log.info("requeued %d job(s) running at last shutdown",
                         recovered)

        self._scheduler.add_job(
            self._analysis_tick, "interval", seconds=30,
            id="analysis", max_instances=1, coalesce=True,
        )
        # The discover tick handles both fork discovery (if a token is set)
        # and a periodic resync of all known forks. The resync part runs
        # regardless of token, so error-state forks always get a retry.
        self._scheduler.add_job(
            self._discover_tick, "interval",
            minutes=self.cfg.sync_interval_minutes,
            id="discover", max_instances=1, coalesce=True,
            next_run_time=None,
        )
        # Kick off one discover+resync now so the operator doesn't wait
        # SYNC_INTERVAL_MINUTES to see anything.
        self._scheduler.add_job(self._discover_tick, id="discover-once")
        self._scheduler.start()
        self._started = True
        log.info("scheduler started")

    def shutdown(self) -> None:
        if not self._started:
            return
        self._scheduler.shutdown(wait=False)
        self._started = False

    # --- ticks -------------------------------------------------------------

    def _analysis_tick(self) -> None:
        token = self.cfg.github_token

        def sync(url: str, dest: Path) -> None:
            clone_or_fetch(url, dest, token=token)

        processed = 0
        with self._open_conn() as conn:
            while True:
                result = analysis.run_one_job(
                    conn, self.cfg.repos_dir, sync=sync,
                )
                if result is None:
                    break
                processed += 1
        log.debug("analysis tick: processed %d job(s)", processed)

    def _discover_tick(self) -> None:
        token = self.cfg.github_token
        log.debug("discover tick: starting (token=%s)",
                  "set" if token else "absent")
        with self._open_conn() as conn:
            if token:
                gh = GitHubClient(token)
                try:
                    added = repos.discover_forks(conn, gh)
                except GitHubError as exc:
                    log.warning("fork discovery failed: %s", exc)
                else:
                    log.debug("fork discovery: %d new fork(s)", len(added))
            self._enqueue_periodic_resync(conn)
        log.debug("discover tick: done")

    def _enqueue_periodic_resync(self, conn: sqlite3.Connection) -> int:
        """For every fork that has no `queued` or `running` work in the
        analysis_jobs table, enqueue a fresh `sync` job. This retries
        error-state forks and keeps successful ones fresh.

        Returns the number of jobs enqueued.
        """
        rows = conn.execute(
            """
            SELECT f.id
            FROM forks f
            WHERE NOT EXISTS (
                SELECT 1 FROM analysis_jobs j
                WHERE j.fork_id = f.id
                  AND j.status IN ('queued', 'running')
            )
            """,
        ).fetchall()
        for r in rows:
            jobs.enqueue_analysis(conn, int(r["id"]), kind="sync")
        log.debug("periodic resync: enqueued %d fork(s)", len(rows))
        return len(rows)

    # --- helpers -----------------------------------------------------------

    def _open_conn(self) -> "_ConnCtx":
        return _ConnCtx(self.cfg.db_path)


class _ConnCtx:
    """Context manager that opens a fresh SQLite connection per tick."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.conn: sqlite3.Connection | None = None

    def __enter__(self) -> sqlite3.Connection:
        self.conn = connect(self.db_path)
        init_schema(self.conn)
        return self.conn

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None


def seed_root_if_configured(cfg: Config, conn: sqlite3.Connection) -> bool:
    """If `ROOT_REPO_URL` is set and no root exists yet, seed it.

    Returns True if a root was seeded by this call.
    """
    if not cfg.root_repo_url:
        return False
    if repos.get_root_repo(conn) is not None:
        return False
    repos.set_root_repo(conn, cfg.root_repo_url)
    log.info("seeded root repo from ROOT_REPO_URL: %s", cfg.root_repo_url)
    return True
