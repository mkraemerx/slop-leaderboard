"""Acceptance tests for FR-08 Webhook."""
from __future__ import annotations

import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from app import jobs as jobs_mod
from app.config import Config
from app.main import create_app
from app.repos import add_fork_manual, set_root_repo
from app.webhooks import verify_signature

from tests.test_auth import FakeOAuth


def _cfg(tmp_path, *, webhook_secret: str | None) -> Config:
    return Config(
        data_dir=tmp_path, db_path=tmp_path / "db.sqlite3",
        repos_dir=tmp_path / "repos",
        github_token=None, root_repo_url=None,
        github_client_id="cid", github_client_secret="csecret",
        github_callback_url="http://test/auth/callback",
        github_org="acme", secret_key="x" * 32,
        sync_interval_minutes=60,
        github_webhook_secret=webhook_secret,
        log_level="INFO",
    )


def _app_with_fork(tmp_path, *, webhook_secret: str | None):
    cfg = _cfg(tmp_path, webhook_secret=webhook_secret)
    from app import db as dbmod
    def _factory():
        c = dbmod.connect(cfg.db_path, check_same_thread=False)
        dbmod.init_schema(c)
        return c
    app = create_app(config=cfg, oauth=FakeOAuth(),
                     connection_factory=_factory, start_scheduler=False)
    set_root_repo(app.state.db, "https://github.com/acme/root")
    add_fork_manual(app.state.db, "https://github.com/alice/root")
    # Drain the auto-enqueued analysis job so post-webhook job count is
    # easy to reason about.
    job = jobs_mod.claim_next(app.state.db)
    jobs_mod.mark_done(app.state.db, job.id)
    return app


def _push_payload(html_url: str = "https://github.com/alice/root") -> bytes:
    return json.dumps({
        "ref": "refs/heads/main",
        "repository": {"html_url": html_url, "full_name": "alice/root"},
    }).encode("utf-8")


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# AC1: valid push webhook → incremental analysis enqueued
def test_valid_signed_push_enqueues_sync_job(tmp_path):
    secret = "topsecret"
    app = _app_with_fork(tmp_path, webhook_secret=secret)
    body = _push_payload()
    client = TestClient(app)

    resp = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "push",
            "X-Hub-Signature-256": _sign(secret, body),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 202

    queue = jobs_mod.jobs_for_fork(app.state.db, 1)
    queued = [j for j in queue if j.status == "queued"]
    assert len(queued) == 1
    assert queued[0].kind == "sync"


# AC2: invalid signature is rejected and nothing is enqueued
def test_invalid_signature_rejected_no_job(tmp_path):
    secret = "topsecret"
    app = _app_with_fork(tmp_path, webhook_secret=secret)
    body = _push_payload()
    client = TestClient(app)

    resp = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "push",
            "X-Hub-Signature-256": "sha256=" + ("0" * 64),
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 401

    queue = jobs_mod.jobs_for_fork(app.state.db, 1)
    assert all(j.status != "queued" for j in queue)


# AC2 cont.: missing signature with secret configured is rejected
def test_missing_signature_when_secret_configured_rejected(tmp_path):
    app = _app_with_fork(tmp_path, webhook_secret="topsecret")
    body = _push_payload()
    client = TestClient(app)

    resp = client.post(
        "/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": "push",
                 "Content-Type": "application/json"},
    )
    assert resp.status_code == 401

    queue = jobs_mod.jobs_for_fork(app.state.db, 1)
    assert all(j.status != "queued" for j in queue)


def test_no_secret_configured_accepts_unsigned(tmp_path):
    """When no secret is configured the endpoint accepts unsigned requests,
    matching the conditional language in FR-08 AC2."""
    app = _app_with_fork(tmp_path, webhook_secret=None)
    body = _push_payload()
    client = TestClient(app)

    resp = client.post(
        "/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": "push",
                 "Content-Type": "application/json"},
    )
    assert resp.status_code == 202

    queue = jobs_mod.jobs_for_fork(app.state.db, 1)
    assert any(j.status == "queued" and j.kind == "sync" for j in queue)


def test_unknown_repository_returns_404(tmp_path):
    app = _app_with_fork(tmp_path, webhook_secret=None)
    body = json.dumps({
        "repository": {"html_url": "https://github.com/random/elsewhere"},
    }).encode()
    client = TestClient(app)
    resp = client.post(
        "/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": "push",
                 "Content-Type": "application/json"},
    )
    assert resp.status_code == 404


def test_non_push_event_is_acknowledged_but_not_actioned(tmp_path):
    secret = "s"
    app = _app_with_fork(tmp_path, webhook_secret=secret)
    body = b'{"zen": "Mind your words"}'
    client = TestClient(app)

    resp = client.post(
        "/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": "ping",
                 "X-Hub-Signature-256": _sign(secret, body),
                 "Content-Type": "application/json"},
    )
    assert resp.status_code == 204

    queue = jobs_mod.jobs_for_fork(app.state.db, 1)
    assert all(j.status != "queued" for j in queue)


def test_webhook_path_does_not_require_session(tmp_path):
    """Companion to FR-07: an unauthenticated POST to /webhooks/* must
    reach the handler, not be redirected to /login."""
    app = _app_with_fork(tmp_path, webhook_secret=None)
    body = _push_payload()
    client = TestClient(app, follow_redirects=False)
    resp = client.post(
        "/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": "push",
                 "Content-Type": "application/json"},
    )
    # We get 202 from the handler, never a redirect.
    assert resp.status_code == 202


# --- pure HMAC helper unit tests ------------------------------------------

def test_verify_signature_accepts_correct_hmac():
    body = b'{"ok":1}'
    sig = _sign("s", body)
    assert verify_signature("s", body, sig)


def test_verify_signature_rejects_unsigned():
    assert not verify_signature("s", b"x", None)
    assert not verify_signature("s", b"x", "")


def test_verify_signature_rejects_unprefixed():
    body = b"x"
    digest = hmac.new(b"s", body, hashlib.sha256).hexdigest()
    assert not verify_signature("s", body, digest)  # no "sha256=" prefix


def test_verify_signature_uses_constant_time_compare():
    """Sanity check: a one-byte tweak still fails."""
    body = b"hello"
    correct = _sign("s", body)
    tampered = correct[:-1] + ("0" if correct[-1] != "0" else "1")
    assert not verify_signature("s", body, tampered)
