"""Tests for the runtime wiring: root-repo seeding, scheduler ticks,
and tokenised git URL rewriting."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app import jobs, scheduler
from app.config import Config
from app.db import connect, init_schema
from app.git_ops import _with_token
from app.repos import add_fork_manual, get_root_repo, set_root_repo


def _cfg(tmp_path: Path, *, root_url: str | None = None,
         token: str | None = None) -> Config:
    return Config(
        data_dir=tmp_path,
        db_path=tmp_path / "db.sqlite3",
        repos_dir=tmp_path / "repos",
        github_token=token,
        root_repo_url=root_url,
        github_client_id="cid", github_client_secret="cs",
        github_callback_url="http://x/cb",
        github_org=None,
        secret_key="x" * 32, sync_interval_minutes=60,
        github_webhook_secret=None,
    )


# --- seed_root_if_configured ----------------------------------------------

def test_seed_root_uses_env_when_db_empty(db, tmp_path: Path):
    cfg = _cfg(tmp_path, root_url="https://github.com/acme/root")
    seeded = scheduler.seed_root_if_configured(cfg, db)
    assert seeded is True
    root = get_root_repo(db)
    assert root is not None
    assert root.url == "https://github.com/acme/root"


def test_seed_root_is_noop_when_root_already_set(db, tmp_path: Path):
    set_root_repo(db, "https://github.com/old/root")
    cfg = _cfg(tmp_path, root_url="https://github.com/new/root")

    seeded = scheduler.seed_root_if_configured(cfg, db)

    assert seeded is False
    assert get_root_repo(db).url == "https://github.com/old/root"


def test_seed_root_does_nothing_when_env_unset(db, tmp_path: Path):
    cfg = _cfg(tmp_path, root_url=None)
    assert scheduler.seed_root_if_configured(cfg, db) is False
    assert get_root_repo(db) is None


# --- _with_token URL rewriting --------------------------------------------

def test_with_token_injects_creds_on_github_https():
    out = _with_token("https://github.com/acme/root", "ghp_xyz")
    assert out == "https://x-access-token:ghp_xyz@github.com/acme/root"


def test_with_token_passthrough_when_no_token():
    assert _with_token("https://github.com/acme/root", None) == \
        "https://github.com/acme/root"
    assert _with_token("https://github.com/acme/root", "") == \
        "https://github.com/acme/root"


def test_with_token_skips_non_github_hosts():
    """We don't want to leak a GitHub token to other hosts."""
    out = _with_token("https://gitlab.com/acme/root", "ghp_xyz")
    assert "x-access-token" not in out
    assert out == "https://gitlab.com/acme/root"


def test_with_token_skips_ssh_urls():
    out = _with_token("git@github.com:acme/root.git", "ghp_xyz")
    assert "ghp_xyz" not in out


# --- Scheduler analysis tick ----------------------------------------------

def test_analysis_tick_drains_queued_jobs(tmp_path: Path):
    cfg = _cfg(tmp_path)
    conn = connect(cfg.db_path)
    init_schema(conn)
    set_root_repo(conn, "https://github.com/acme/root")
    fork = add_fork_manual(conn, "https://github.com/alice/root")
    # The auto-enqueued 'full' job is already there.
    queued_before = [j for j in jobs.jobs_for_fork(conn, fork.id)
                     if j.status == "queued"]
    assert len(queued_before) == 1

    # Run the tick with a fake sync so we don't hit the network.
    sched = scheduler.Scheduler(cfg)
    with patch("app.scheduler.clone_or_fetch") as mock_clone:
        def fake_sync(url, dest, *, token=None):
            # create a tiny empty repo so analyze_fork can read refs
            import pygit2
            pygit2.init_repository(str(dest), bare=True)
        mock_clone.side_effect = fake_sync
        sched._analysis_tick()

    after = [j for j in jobs.jobs_for_fork(conn, fork.id)
             if j.status in ("queued", "running")]
    assert after == [], f"queue still has work: {after}"


def test_analysis_tick_passes_token_to_clone(tmp_path: Path):
    cfg = _cfg(tmp_path, token="ghp_secret")
    conn = connect(cfg.db_path)
    init_schema(conn)
    set_root_repo(conn, "https://github.com/acme/root")
    add_fork_manual(conn, "https://github.com/alice/root")

    sched = scheduler.Scheduler(cfg)
    with patch("app.scheduler.clone_or_fetch") as mock_clone:
        def fake_sync(url, dest, *, token=None):
            assert token == "ghp_secret"
            import pygit2
            pygit2.init_repository(str(dest), bare=True)
        mock_clone.side_effect = fake_sync
        sched._analysis_tick()
    assert mock_clone.called


# --- Scheduler discover tick ----------------------------------------------

def test_discover_tick_noop_without_token(tmp_path: Path):
    cfg = _cfg(tmp_path, token=None)
    sched = scheduler.Scheduler(cfg)
    # Should return cleanly without doing anything.
    sched._discover_tick()


def test_discover_tick_calls_discover_forks_with_token(tmp_path: Path):
    cfg = _cfg(tmp_path, token="ghp_xyz")
    conn = connect(cfg.db_path)
    init_schema(conn)
    set_root_repo(conn, "https://github.com/acme/root")

    sched = scheduler.Scheduler(cfg)
    with patch("app.scheduler.repos.discover_forks") as mock_disc:
        mock_disc.return_value = []
        sched._discover_tick()
    assert mock_disc.called


def test_discover_tick_swallows_github_errors(tmp_path: Path):
    from app.github import GitHubError
    cfg = _cfg(tmp_path, token="ghp_xyz")
    conn = connect(cfg.db_path)
    init_schema(conn)
    set_root_repo(conn, "https://github.com/acme/root")

    sched = scheduler.Scheduler(cfg)
    with patch("app.scheduler.repos.discover_forks",
               side_effect=GitHubError("rate limited")):
        # Must not raise
        sched._discover_tick()


# --- start / shutdown lifecycle -------------------------------------------

def test_scheduler_starts_and_shuts_down_cleanly(tmp_path: Path):
    cfg = _cfg(tmp_path)
    sched = scheduler.Scheduler(cfg)
    sched.start()
    assert sched._started
    sched.shutdown()
    assert not sched._started
    # second shutdown is a no-op
    sched.shutdown()


def test_scheduler_start_is_idempotent(tmp_path: Path):
    cfg = _cfg(tmp_path)
    sched = scheduler.Scheduler(cfg)
    sched.start()
    sched.start()  # no-op
    sched.shutdown()
