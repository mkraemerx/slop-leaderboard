"""FastAPI app factory."""
from __future__ import annotations

import logging
import logging.handlers
import sqlite3
import sys
from pathlib import Path
from typing import Callable

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from . import auth, db, scheduler as _scheduler, web, webhooks
from .config import Config, load_config


# Names of every logger our app emits to. We configure these directly (not
# the root) so uvicorn's `logging.config.dictConfig` on startup — which
# replaces root handlers — can't take our logs out.
APP_LOGGERS = ("analysis", "scheduler", "webhooks", "auth")
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def _configure_logging(cfg: Config) -> Path:
    """Attach a StreamHandler (stderr) AND a rotating FileHandler to each
    of our app loggers. Idempotent; safe under uvicorn --reload.

    Returns the log file path so the operator knows where to tail.

    Why both:
    - StreamHandler: visible when uvicorn's logging doesn't eat it.
    - FileHandler: survives anything uvicorn does to the console.

    Why on named loggers (not root): uvicorn replaces the root logger's
    handlers during startup. Attaching to named loggers with
    `propagate = False` makes our output immune to that.
    """
    log_dir = cfg.data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "app.log"

    formatter = logging.Formatter(_LOG_FORMAT)

    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler._slop = True  # type: ignore[attr-defined]

    stream_handler = logging.StreamHandler(stream=sys.stderr)
    stream_handler.setFormatter(formatter)
    stream_handler._slop = True  # type: ignore[attr-defined]

    # LOG_LEVEL env var (default INFO) controls verbosity. Routine
    # per-fork and per-tick lines are at DEBUG, so set LOG_LEVEL=DEBUG to
    # see the full scheduler heartbeat; INFO keeps the file quiet, only
    # surfacing meaningful events (startup, errors).
    level = getattr(logging, cfg.log_level, logging.INFO)
    for name in APP_LOGGERS:
        logger = logging.getLogger(name)
        # Idempotent: replace any prior _slop handlers (e.g. --reload
        # re-imports the module).
        for h in list(logger.handlers):
            if getattr(h, "_slop", False):
                logger.removeHandler(h)
        logger.setLevel(level)
        logger.addHandler(file_handler)
        logger.addHandler(stream_handler)
        # propagate=False so messages don't ALSO hit root (which uvicorn
        # owns); we don't want duplicate or swallowed lines.
        logger.propagate = False

    # Announce the file path on stderr so the operator can find it.
    logging.getLogger("scheduler").info("logging to %s (level=%s)",
                                         log_file, cfg.log_level)
    return log_file


def create_app(
    *,
    config: Config | None = None,
    oauth: auth.OAuthBackend | None = None,
    connection_factory: Callable[[], sqlite3.Connection] | None = None,
    start_scheduler: bool = True,
) -> FastAPI:
    """Build a FastAPI app.

    Tests inject a fake oauth, an in-memory db connection factory, and pass
    `start_scheduler=False` so the background scheduler doesn't run in the
    test process. Production passes none of these.
    """
    cfg = config or load_config()
    log_path = _configure_logging(cfg)
    app = FastAPI(title="slop-leaderboard")
    app.state.config = cfg
    app.state.log_path = log_path

    if connection_factory is None:
        def connection_factory():
            # FastAPI runs sync handlers in a thread pool, so the connection
            # we hold on app.state must allow cross-thread use.
            conn = db.connect(cfg.db_path, check_same_thread=False)
            db.init_schema(conn)
            return conn
    app.state.db = connection_factory()

    # If ROOT_REPO_URL is set in env and the DB has no root yet, seed it.
    _scheduler.seed_root_if_configured(cfg, app.state.db)

    if oauth is None:
        if not (cfg.github_client_id and cfg.github_client_secret
                and cfg.github_callback_url):
            raise RuntimeError(
                "GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, GITHUB_CALLBACK_URL "
                "must all be configured"
            )
        oauth = auth.GitHubOAuth(
            cfg.github_client_id, cfg.github_client_secret,
            cfg.github_callback_url,
        )
    app.state.oauth = oauth

    # add_middleware adds *outer* layers; the auth check is registered first
    # so SessionMiddleware ends up wrapping it (request.session must exist
    # by the time the auth check reads it).
    app.middleware("http")(auth.require_session)
    app.add_middleware(
        SessionMiddleware,
        secret_key=cfg.secret_key or "dev-only-secret",
        same_site="lax",
        https_only=False,
    )

    app.include_router(auth.router)
    app.include_router(webhooks.router)
    app.include_router(web.router)
    web.mount_static(app)

    if start_scheduler:
        sched = _scheduler.Scheduler(cfg)
        sched.start()
        app.state.scheduler = sched

        @app.on_event("shutdown")
        def _stop_scheduler() -> None:
            sched.shutdown()

    return app


# PEP 562 module-level lazy attribute. `uvicorn app.main:app` triggers this;
# tests that import only `create_app` never construct a real app.
def __getattr__(name: str):
    if name == "app":
        return create_app()
    raise AttributeError(name)
