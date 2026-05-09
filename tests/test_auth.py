"""Acceptance tests for FR-07 Authentication."""
from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from app import auth as auth_mod
from app.config import Config
from app.main import create_app
from app.repos import add_fork_manual, set_root_repo


@dataclass
class FakeOAuth:
    """Test stub: deterministic state, configurable user + org membership."""
    user_login: str = "alice"
    org_membership: dict[str, bool] | None = None
    granted_token: str = "fake-token"
    last_seen_state: str | None = None

    def authorize_url(self, state: str, scope: str) -> str:
        self.last_seen_state = state
        return f"https://example.test/oauth?state={state}&scope={scope}"

    def exchange_code(self, code: str) -> str:
        assert code == "good-code"
        return self.granted_token

    def get_user(self, token: str) -> auth_mod.GitHubUser:
        assert token == self.granted_token
        return auth_mod.GitHubUser(login=self.user_login)

    def is_org_member(self, token: str, org: str, login: str) -> bool:
        return (self.org_membership or {}).get((org, login), False)


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(
        data_dir=tmp_path,
        db_path=tmp_path / "db.sqlite3",
        repos_dir=tmp_path / "repos",
        github_token=None,
        root_repo_url=None,
        github_client_id="cid",
        github_client_secret="csecret",
        github_callback_url="http://test/auth/callback",
        github_org="acme",
        secret_key="x" * 32,
        sync_interval_minutes=60,
        github_webhook_secret=None,
        log_level="INFO",
    )


def make_app(cfg: Config, fake_oauth: FakeOAuth):
    from app import db as dbmod
    def _factory():
        conn = dbmod.connect(cfg.db_path, check_same_thread=False)
        dbmod.init_schema(conn)
        return conn
    return create_app(config=cfg, oauth=fake_oauth,
                      connection_factory=_factory, start_scheduler=False)


def test_unauthenticated_user_redirected_to_login(cfg):
    app = make_app(cfg, FakeOAuth())
    with TestClient(app, follow_redirects=False) as client:
        resp = client.get("/")
        assert resp.status_code == 303
        assert resp.headers["location"] == "/login"


def test_login_page_is_public(cfg):
    app = make_app(cfg, FakeOAuth())
    with TestClient(app) as client:
        resp = client.get("/login")
        assert resp.status_code == 200
        assert "Continue with GitHub" in resp.text


def test_oauth_login_redirects_to_authorization_url(cfg):
    fake = FakeOAuth()
    app = make_app(cfg, fake)
    with TestClient(app, follow_redirects=False) as client:
        resp = client.get("/auth/login")
        assert resp.status_code in (302, 307)
        assert resp.headers["location"].startswith("https://example.test/oauth")
        assert fake.last_seen_state is not None


def test_callback_grants_access_to_org_member(cfg):
    fake = FakeOAuth(
        user_login="alice",
        org_membership={("acme", "alice"): True},
    )
    app = make_app(cfg, fake)

    # Pre-populate the db with no fork — only org membership grants access.
    with TestClient(app, follow_redirects=False) as client:
        # First start the OAuth flow so the state lands in the session
        login = client.get("/auth/login")
        # Extract the state from the redirect URL
        from urllib.parse import urlparse, parse_qs
        state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]

        cb = client.get(f"/auth/callback?code=good-code&state={state}")
        assert cb.status_code == 303
        assert cb.headers["location"] == "/"

        # Now an authenticated request to the dashboard is NOT redirected
        # to /login. (The home route may redirect onward to /leaderboard.)
        home = client.get("/")
        assert home.headers.get("location") != "/login"


def test_callback_grants_access_to_fork_owner_when_not_org_member(cfg):
    fake = FakeOAuth(
        user_login="alice",
        org_membership={("acme", "alice"): False},
    )
    app = make_app(cfg, fake)
    set_root_repo(app.state.db, "https://github.com/acme/root")
    add_fork_manual(app.state.db, "https://github.com/alice/root")

    with TestClient(app, follow_redirects=False) as client:
        login = client.get("/auth/login")
        from urllib.parse import urlparse, parse_qs
        state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
        cb = client.get(f"/auth/callback?code=good-code&state={state}")
        assert cb.status_code == 303


def test_callback_denies_access_for_neither_org_nor_fork(cfg):
    fake = FakeOAuth(
        user_login="randomperson",
        org_membership={("acme", "randomperson"): False},
    )
    app = make_app(cfg, fake)
    set_root_repo(app.state.db, "https://github.com/acme/root")
    # randomperson is not a fork owner and not an org member

    with TestClient(app, follow_redirects=False) as client:
        login = client.get("/auth/login")
        from urllib.parse import urlparse, parse_qs
        state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
        cb = client.get(f"/auth/callback?code=good-code&state={state}")
        assert cb.status_code == 403
        assert "Access denied" in cb.text


def test_callback_rejects_state_mismatch(cfg):
    fake = FakeOAuth()
    app = make_app(cfg, fake)
    with TestClient(app, follow_redirects=False) as client:
        client.get("/auth/login")
        bad = client.get("/auth/callback?code=good-code&state=tampered")
        assert bad.status_code == 400


def test_logout_clears_session(cfg):
    fake = FakeOAuth(
        user_login="alice",
        org_membership={("acme", "alice"): True},
    )
    app = make_app(cfg, fake)

    with TestClient(app, follow_redirects=False) as client:
        login = client.get("/auth/login")
        from urllib.parse import urlparse, parse_qs
        state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
        client.get(f"/auth/callback?code=good-code&state={state}")

        # Confirm we're in: home redirects but NOT to /login
        first = client.get("/")
        assert first.headers.get("location") != "/login"

        # Log out
        out = client.get("/auth/logout")
        assert out.status_code == 303
        assert out.headers["location"] == "/login"

        # Subsequent request to / redirects to /login again
        again = client.get("/")
        assert again.status_code == 303
        assert again.headers["location"] == "/login"


def test_webhook_path_is_exempt_from_session_auth(cfg):
    """The webhook endpoint exists in app.webhooks; the auth middleware
    must NOT redirect requests to it. Whatever status code the real
    handler returns, it must not be 303 → /login.
    """
    fake = FakeOAuth()
    app = make_app(cfg, fake)
    with TestClient(app, follow_redirects=False) as client:
        resp = client.post("/webhooks/github")
        assert resp.status_code != 303
        assert resp.headers.get("location") != "/login"


def _make_no_org_cfg(tmp_path) -> Config:
    return Config(
        data_dir=tmp_path, db_path=tmp_path / "db.sqlite3",
        repos_dir=tmp_path / "repos",
        github_token=None, root_repo_url=None,
        github_client_id="cid", github_client_secret="csecret",
        github_callback_url="http://test/auth/callback",
        github_org=None,                     # <-- no org configured
        secret_key="x" * 32, sync_interval_minutes=60,
        github_webhook_secret=None,
        log_level="INFO",
    )


def test_no_org_configured_fork_owner_admitted(tmp_path):
    cfg = _make_no_org_cfg(tmp_path)
    fake = FakeOAuth(user_login="alice", org_membership=None)
    app = make_app(cfg, fake)
    set_root_repo(app.state.db, "https://github.com/acme/root")
    add_fork_manual(app.state.db, "https://github.com/alice/root")

    with TestClient(app, follow_redirects=False) as client:
        login = client.get("/auth/login")
        from urllib.parse import urlparse, parse_qs
        state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
        cb = client.get(f"/auth/callback?code=good-code&state={state}")
        assert cb.status_code == 303


def test_no_org_configured_non_fork_owner_denied(tmp_path):
    cfg = _make_no_org_cfg(tmp_path / "second")
    fake = FakeOAuth(user_login="bob", org_membership=None)
    app = make_app(cfg, fake)
    set_root_repo(app.state.db, "https://github.com/acme/root")
    add_fork_manual(app.state.db, "https://github.com/alice/root")
    # bob is not a fork owner, no org check available → denied

    with TestClient(app, follow_redirects=False) as client:
        login = client.get("/auth/login")
        from urllib.parse import urlparse, parse_qs
        state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
        cb = client.get(f"/auth/callback?code=good-code&state={state}")
        assert cb.status_code == 403


def test_is_public_path_recognises_known_endpoints():
    assert auth_mod.is_public_path("/auth/login")
    assert auth_mod.is_public_path("/login")
    assert auth_mod.is_public_path("/healthz")
    assert auth_mod.is_public_path("/static/style.css")
    assert auth_mod.is_public_path("/webhooks/github")
    assert not auth_mod.is_public_path("/")
    assert not auth_mod.is_public_path("/dashboard")
