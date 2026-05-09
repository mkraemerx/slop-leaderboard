"""Background scheduler that ties the runtime pieces together.

Two interval jobs run inside a single APScheduler BackgroundScheduler:

- `_analysis_tick`: drains the analysis_jobs queue by calling
  `analysis.run_one_job` until it returns None. Runs every 30 seconds.
- `_discover_tick`: invokes `repos.discover_forks` to pick up any new
  forks of the root repo. Runs every `SYNC_INTERVAL_MINUTES`.

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
        if self.cfg.github_token:
            self._scheduler.add_job(
                self._discover_tick, "interval",
                minutes=self.cfg.sync_interval_minutes,
                id="discover", max_instances=1, coalesce=True,
                next_run_time=None,  # don't run immediately on start
            )
            # Kick off one discovery now so the operator doesn't have to
            # wait an hour to see anything.
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

        with self._open_conn() as conn:
            while True:
                result = analysis.run_one_job(
                    conn, self.cfg.repos_dir, sync=sync,
                )
                if result is None:
                    return

    def _discover_tick(self) -> None:
        token = self.cfg.github_token
        if not token:
            return
        with self._open_conn() as conn:
            gh = GitHubClient(token)
            try:
                added = repos.discover_forks(conn, gh)
            except GitHubError as exc:
                log.warning("fork discovery failed: %s", exc)
                return
        if added:
            log.info("discovered %d new fork(s)", len(added))

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
