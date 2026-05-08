"""FastAPI app factory."""
from __future__ import annotations

import sqlite3
from typing import Callable

from fastapi import FastAPI, Request
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import HTMLResponse

from . import auth, db, webhooks
from .config import Config, load_config


def create_app(
    *,
    config: Config | None = None,
    oauth: auth.OAuthBackend | None = None,
    connection_factory: Callable[[], sqlite3.Connection] | None = None,
) -> FastAPI:
    """Build a FastAPI app. Tests inject a fake oauth and an in-memory db
    connection factory; production passes neither and uses the real impls.
    """
    cfg = config or load_config()
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

    @app.get("/")
    def home(request: Request):
        user = auth.session_user(request)
        return HTMLResponse(
            f"<h1>Welcome, {user['login']}</h1>"
            f"<p><a href='/auth/logout'>Sign out</a></p>"
        )

    return app
