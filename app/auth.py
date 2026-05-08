"""GitHub OAuth + session auth (FR-07).

The OAuth client is split into a small protocol so tests can swap in a
stub. The default implementation hits GitHub's REST API; everything that
matters for the access decision (org membership, fork ownership) is decided
in pure Python from the data already in the application database.
"""
from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import HTMLResponse, RedirectResponse


PUBLIC_PATHS: tuple[str, ...] = (
    "/auth/login",
    "/auth/callback",
    "/auth/logout",
    "/login",
    "/healthz",
    "/static/",
    "/webhooks/",
)


@dataclass(frozen=True)
class GitHubUser:
    login: str


class OAuthBackend(Protocol):
    """Pluggable OAuth backend."""
    def authorize_url(self, state: str, scope: str) -> str: ...
    def exchange_code(self, code: str) -> str: ...
    def get_user(self, token: str) -> GitHubUser: ...
    def is_org_member(self, token: str, org: str, login: str) -> bool: ...


class GitHubOAuth:
    """Real-world implementation against api.github.com."""

    def __init__(self, client_id: str, client_secret: str,
                 callback_url: str, http=None) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.callback_url = callback_url
        self._http = http

    def _client(self):
        if self._http is None:
            import httpx
            self._http = httpx.Client(timeout=30.0)
        return self._http

    def authorize_url(self, state: str, scope: str) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.callback_url,
            "scope": scope,
            "state": state,
            "allow_signup": "false",
        }
        return f"https://github.com/login/oauth/authorize?{urlencode(params)}"

    def exchange_code(self, code: str) -> str:
        resp = self._client().post(
            "https://github.com/login/oauth/access_token",
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "redirect_uri": self.callback_url,
            },
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"oauth token exchange failed: {resp.status_code}")
        payload = resp.json()
        token = payload.get("access_token")
        if not token:
            raise RuntimeError(f"oauth response missing access_token: {payload}")
        return token

    def get_user(self, token: str) -> GitHubUser:
        resp = self._client().get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.github+json"},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"github /user returned {resp.status_code}")
        return GitHubUser(login=resp.json()["login"])

    def is_org_member(self, token: str, org: str, login: str) -> bool:
        # GET /orgs/{org}/members/{login} returns 204 if member, 404 if not,
        # 302 if not visible to caller. See ASSUMPTION-011.
        resp = self._client().get(
            f"https://api.github.com/orgs/{org}/members/{login}",
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.github+json"},
            follow_redirects=False,
        )
        return resp.status_code == 204


# --- access decisions ------------------------------------------------------

def is_fork_owner(conn: sqlite3.Connection, login: str) -> bool:
    """Case-insensitive lookup against tracked forks' owner field."""
    if not login:
        return False
    row = conn.execute(
        "SELECT 1 FROM forks WHERE LOWER(owner) = LOWER(?) LIMIT 1",
        (login,),
    ).fetchone()
    return row is not None


def has_dashboard_access(
    conn: sqlite3.Connection,
    login: str,
    *,
    org: str | None,
    org_member: bool,
) -> bool:
    """Per FR-07: access if org member OR fork owner. If no org configured,
    only fork owners are admitted."""
    if org and org_member:
        return True
    return is_fork_owner(conn, login)


# --- session helpers -------------------------------------------------------

def session_user(request: Request) -> dict | None:
    return request.session.get("user")


def is_public_path(path: str) -> bool:
    for p in PUBLIC_PATHS:
        if p.endswith("/"):
            if path.startswith(p):
                return True
        elif path == p:
            return True
    return False


def login_redirect(target: str = "/login") -> RedirectResponse:
    return RedirectResponse(url=target, status_code=303)


# --- routes ----------------------------------------------------------------

router = APIRouter()


@router.get("/healthz")
def healthz():
    return {"ok": True}


@router.get("/login")
def login_page(request: Request) -> HTMLResponse:
    return HTMLResponse(
        '<!doctype html><html><body>'
        '<h1>Sign in</h1>'
        '<p><a href="/auth/login">Continue with GitHub</a></p>'
        '</body></html>'
    )


@router.get("/auth/login")
def auth_login(request: Request) -> RedirectResponse:
    backend: OAuthBackend = request.app.state.oauth
    state = secrets.token_hex(16)
    request.session["oauth_state"] = state
    return RedirectResponse(backend.authorize_url(state, scope="read:org"))


@router.get("/auth/callback")
def auth_callback(request: Request, code: str, state: str):
    expected = request.session.pop("oauth_state", None)
    if not expected or state != expected:
        raise HTTPException(status_code=400, detail="invalid state")
    backend: OAuthBackend = request.app.state.oauth
    token = backend.exchange_code(code)
    user = backend.get_user(token)
    org = request.app.state.config.github_org
    org_member = backend.is_org_member(token, org, user.login) if org else False

    conn = request.app.state.db
    if not has_dashboard_access(conn, user.login, org=org, org_member=org_member):
        return HTMLResponse(
            f'<!doctype html><html><body><h1>Access denied</h1>'
            f'<p>{user.login} is not a member of the configured GitHub '
            f'organisation and does not own a tracked fork.</p>'
            f'<p><a href="/auth/logout">Sign out</a></p>'
            f'</body></html>',
            status_code=403,
        )
    request.session["user"] = {"login": user.login,
                               "org_member": bool(org_member)}
    return RedirectResponse(url="/", status_code=303)


@router.get("/auth/logout")
def auth_logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


# --- middleware ------------------------------------------------------------

async def require_session(request: Request, call_next):
    """Middleware: redirect unauthenticated users to /login for any path
    not in PUBLIC_PATHS. The webhook endpoint lives under /webhooks/* and
    is therefore exempt — FR-08 verifies signatures in lieu of session auth.
    """
    if is_public_path(request.url.path):
        return await call_next(request)
    if session_user(request) is None:
        return login_redirect()
    return await call_next(request)
