"""FastAPI app factory."""
from __future__ import annotations

import logging
import sqlite3
from typing import Callable

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from . import auth, db, scheduler as _scheduler, web, webhooks
from .config import Config, load_config


def _configure_logging() -> None:
    """Route the app's loggers to stdout once, so analysis failures and
    scheduler ticks show up in the uvicorn console. Idempotent — calling
    again is a no-op (uvicorn imports app.main repeatedly under --reload).
    """
    root = logging.getLogger()
    if any(getattr(h, "_slop_default", False) for h in root.handlers):
        return
    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    ))
    handler._slop_default = True  # type: ignore[attr-defined]
    # We add to the root logger so uvicorn's own logging still flows; we
    # only raise our app loggers to INFO.
    root.addHandler(handler)
    for name in ("analysis", "scheduler"):
        logging.getLogger(name).setLevel(logging.INFO)


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
    _configure_logging()
    app = FastAPI(title="slop-leaderboard")
    app.state.config = cfg

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
