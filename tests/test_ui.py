"""Acceptance tests for FR-09 UI."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db as dbmod, jobs as jobs_mod
from app.config import Config
from app.main import create_app
from app.repos import add_fork_manual, set_root_repo

from tests.test_auth import FakeOAuth


def _cfg(tmp_path) -> Config:
    return Config(
        data_dir=tmp_path, db_path=tmp_path / "db.sqlite3",
        repos_dir=tmp_path / "repos",
        github_token=None, root_repo_url=None,
        github_client_id="cid", github_client_secret="csecret",
        github_callback_url="http://test/auth/callback",
        github_org="acme",
        secret_key="x" * 32, sync_interval_minutes=60,
        github_webhook_secret=None,
    )


def _build_app(tmp_path, *, with_root: bool = True):
    cfg = _cfg(tmp_path)
    def _factory():
        c = dbmod.connect(cfg.db_path, check_same_thread=False)
        dbmod.init_schema(c)
        return c
    fake = FakeOAuth(
        user_login="alice",
        org_membership={("acme", "alice"): True},
    )
    app = create_app(config=cfg, oauth=fake, connection_factory=_factory)
    if with_root:
        set_root_repo(app.state.db, "https://github.com/acme/root")
    return app


def _login(client: TestClient):
    """Run the OAuth dance to seed a session cookie."""
    from urllib.parse import urlparse, parse_qs
    login = client.get("/auth/login", follow_redirects=False)
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    client.get(f"/auth/callback?code=good-code&state={state}",
               follow_redirects=False)


# AC1: distinct pages for leaderboard, exercises, comparison, forks
@pytest.mark.parametrize("path", ["/leaderboard", "/exercises", "/forks"])
def test_distinct_pages_render(tmp_path, path):
    app = _build_app(tmp_path)
    with TestClient(app) as client:
        _login(client)
        resp = client.get(path)
        assert resp.status_code == 200
        # Each page identifies itself in the document body
        body = resp.text.lower()
        if path == "/leaderboard":
            assert "leaderboard" in body
        if path == "/exercises":
            assert "exercises" in body
        if path == "/forks":
            assert "forks" in body


def test_root_redirects_to_leaderboard(tmp_path):
    app = _build_app(tmp_path)
    with TestClient(app, follow_redirects=False) as client:
        _login(client)
        resp = client.get("/")
        assert resp.status_code == 303
        assert resp.headers["location"] == "/leaderboard"


def test_comparison_page_accessible_per_exercise(tmp_path):
    """AC: the comparison view is accessible per exercise from the
    exercise list (we link from exercises.html)."""
    app = _build_app(tmp_path)
    with TestClient(app) as client:
        _login(client)
        # The link target must render even if the exercise has no forks.
        resp = client.get("/exercises/branch/exercise-01/comparison")
        assert resp.status_code == 200


# AC2: filter changes update only the relevant section (HTMX partial)
def test_leaderboard_table_partial_returns_just_the_table(tmp_path):
    app = _build_app(tmp_path)
    with TestClient(app) as client:
        _login(client)
        resp = client.get("/leaderboard/table?window=all")
        assert resp.status_code == 200
        # No <html>, no <body>, but yes the leaderboard table marker
        assert "<html" not in resp.text.lower()
        assert "id=\"leaderboard-table\"" in resp.text


def test_filter_form_uses_htmx_attributes(tmp_path):
    """The filter form carries hx-get/hx-target so JS-enabled clients
    update only the table."""
    app = _build_app(tmp_path)
    with TestClient(app) as client:
        _login(client)
        body = client.get("/leaderboard").text
        assert "hx-get=\"/leaderboard/table\"" in body
        assert "hx-target=\"#leaderboard-table\"" in body


# AC3: every fork shows its sync state
def test_forks_table_shows_sync_state(tmp_path):
    app = _build_app(tmp_path)
    add_fork_manual(app.state.db, "https://github.com/alice/root")
    add_fork_manual(app.state.db, "https://github.com/bob/root")
    with TestClient(app) as client:
        _login(client)
        body = client.get("/forks").text
        # alice/root and bob/root should both show with their pending status
        assert "alice" in body and "bob" in body
        # Both forks come back as 'pending' until the worker runs
        assert body.count("status-pending") == 2


def test_forks_table_shows_error_state(tmp_path):
    from app.repos import update_sync_status
    app = _build_app(tmp_path)
    fork = add_fork_manual(app.state.db, "https://github.com/alice/root")
    update_sync_status(app.state.db, fork.id, "error",
                       error="fetch failed: remote unreachable")
    with TestClient(app) as client:
        _login(client)
        body = client.get("/forks").text
        assert "status-error" in body
        # Error message lives in a tooltip (title attribute) for hover
        assert "fetch failed: remote unreachable" in body


# AC4: works without JavaScript — forms submit normally (GET filter, POST add)
def test_no_js_submit_filter_via_get(tmp_path):
    """Without HTMX, the filter form falls back to a plain GET to
    /leaderboard. The response is still a full page."""
    app = _build_app(tmp_path)
    with TestClient(app) as client:
        _login(client)
        resp = client.get("/leaderboard?window=7d&exercise=")
        assert resp.status_code == 200
        # full-page response, not a partial
        assert "<html" in resp.text.lower()


def test_no_js_add_fork_via_form_post(tmp_path):
    app = _build_app(tmp_path)
    with TestClient(app, follow_redirects=False) as client:
        _login(client)
        resp = client.post("/forks", data={"url": "https://github.com/alice/root"})
        assert resp.status_code == 303
        assert resp.headers["location"] == "/forks"
        # Visit /forks and check the new row is there
        body = client.get("/forks").text
        assert "alice" in body


def test_no_js_sync_now_redirects_back_to_forks(tmp_path):
    app = _build_app(tmp_path)
    fork = add_fork_manual(app.state.db, "https://github.com/alice/root")
    with TestClient(app, follow_redirects=False) as client:
        _login(client)
        resp = client.post(f"/forks/{fork.id}/sync")
        assert resp.status_code == 303
        assert resp.headers["location"] == "/forks"
        # A 'sync' job is now queued
        queue = jobs_mod.jobs_for_fork(app.state.db, fork.id)
        assert any(j.kind == "sync" and j.status == "queued" for j in queue)


def test_htmx_sync_now_returns_partial(tmp_path):
    app = _build_app(tmp_path)
    fork = add_fork_manual(app.state.db, "https://github.com/alice/root")
    with TestClient(app) as client:
        _login(client)
        resp = client.post(
            f"/forks/{fork.id}/sync",
            headers={"hx-request": "true"},
        )
        assert resp.status_code == 200
        # Partial: no <html> wrapper but contains the forks-table id
        assert "<html" not in resp.text.lower()
        assert "id=\"forks-table\"" in resp.text


def test_unauthenticated_request_to_dashboard_redirects(tmp_path):
    """Sanity check that the FR-07 middleware still applies to UI routes."""
    app = _build_app(tmp_path)
    with TestClient(app, follow_redirects=False) as client:
        for path in ("/leaderboard", "/exercises", "/forks"):
            resp = client.get(path)
            assert resp.status_code == 303
            assert resp.headers["location"] == "/login"
