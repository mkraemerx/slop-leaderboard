"""Tests for the logging setup that survives uvicorn's startup config."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Config
from app.main import APP_LOGGERS, _configure_logging

from tests.test_ui import _build_app, _login


def _cfg(tmp_path: Path) -> Config:
    return Config(
        data_dir=tmp_path,
        db_path=tmp_path / "db.sqlite3",
        repos_dir=tmp_path / "repos",
        github_token=None, root_repo_url=None,
        github_client_id="cid", github_client_secret="cs",
        github_callback_url="http://x/cb",
        github_org=None,
        secret_key="x" * 32, sync_interval_minutes=60,
        github_webhook_secret=None,
    )


def test_configure_logging_attaches_handlers_to_each_app_logger(tmp_path):
    cfg = _cfg(tmp_path)
    _configure_logging(cfg)
    for name in APP_LOGGERS:
        logger = logging.getLogger(name)
        # Handlers we control should be present
        slop = [h for h in logger.handlers if getattr(h, "_slop", False)]
        assert slop, f"{name} has no slop handlers"
        # propagate disabled so uvicorn's root reconfig can't eat us
        assert logger.propagate is False


def test_configure_logging_writes_to_a_file(tmp_path):
    cfg = _cfg(tmp_path)
    log_path = _configure_logging(cfg)
    logging.getLogger("analysis").info("hello from a test")
    for h in logging.getLogger("analysis").handlers:
        h.flush()
    assert log_path.exists()
    text = log_path.read_text()
    assert "hello from a test" in text


def test_configure_logging_is_idempotent(tmp_path):
    cfg = _cfg(tmp_path)
    _configure_logging(cfg)
    before = len(logging.getLogger("analysis").handlers)
    _configure_logging(cfg)
    after = len(logging.getLogger("analysis").handlers)
    assert before == after, "re-calling should not duplicate handlers"


def test_debug_log_page_shows_recent_lines(tmp_path):
    app = _build_app(tmp_path)
    log_path = app.state.log_path
    logging.getLogger("analysis").error("synthetic test marker LINE1")
    logging.getLogger("scheduler").info("synthetic test marker LINE2")
    for name in ("analysis", "scheduler"):
        for h in logging.getLogger(name).handlers:
            h.flush()

    with TestClient(app) as client:
        _login(client)
        resp = client.get("/debug/log")
        assert resp.status_code == 200
        assert "synthetic test marker LINE1" in resp.text
        assert "synthetic test marker LINE2" in resp.text
        assert str(log_path) in resp.text


def test_debug_log_page_handles_missing_file(tmp_path):
    """If the log file hasn't been created yet (e.g. cold start), the
    page still renders rather than 500-ing."""
    app = _build_app(tmp_path)
    # Wipe the log
    Path(app.state.log_path).unlink(missing_ok=True)
    with TestClient(app) as client:
        _login(client)
        resp = client.get("/debug/log")
        assert resp.status_code == 200
        assert "No log lines yet" in resp.text


def test_debug_jobs_page_shows_log_path(tmp_path):
    app = _build_app(tmp_path)
    with TestClient(app) as client:
        _login(client)
        resp = client.get("/debug/jobs")
        assert resp.status_code == 200
        assert str(app.state.log_path) in resp.text
