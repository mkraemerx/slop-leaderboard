"""Tests for the runtime wiring: root-repo seeding, scheduler ticks,
and the pygit2 credentials callback."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app import jobs, scheduler
from app.config import Config
from app.db import connect, init_schema
from app.git_ops import _token_callbacks
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


# --- credentials callback -------------------------------------------------

def test_token_callbacks_returns_none_when_no_token():
    """No token → no callback object; libgit2 will treat the fetch as
    anonymous (which is right for public repos)."""
    assert _token_callbacks(None) is None
    assert _token_callbacks("") is None


def test_token_callbacks_supplies_userpass_when_token_set():
    """The credentials() hook returns a pygit2 UserPass with
    `x-access-token` as the username — the documented GitHub form for
    PATs and OAuth tokens used as git basic auth."""
    import pygit2
    cb = _token_callbacks("ghp_xyz")
    assert cb is not None
    cred = cb.credentials(
        "https://github.com/acme/root", None,
        pygit2.CredentialType.USERPASS_PLAINTEXT,
    )
    assert isinstance(cred, pygit2.UserPass)
    # UserPass exposes its tuple via .credential_tuple
    assert cred.credential_tuple == ("x-access-token", "ghp_xyz")


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

def test_discover_tick_without_token_still_enqueues_resync(tmp_path: Path):
    """Without a token we skip API discovery but still resync existing forks
    so errored forks get retried."""
    cfg = _cfg(tmp_path, token=None)
    conn = connect(cfg.db_path)
    init_schema(conn)
    set_root_repo(conn, "https://github.com/acme/root")
    fork = add_fork_manual(conn, "https://github.com/alice/root")
    # Drain the auto-enqueued 'full' job so the fork has no pending work.
    j = jobs.claim_next(conn); jobs.mark_failed(conn, j.id, "earlier failure")

    sched = scheduler.Scheduler(cfg)
    sched._discover_tick()

    queue = [j for j in jobs.jobs_for_fork(conn, fork.id) if j.status == "queued"]
    assert len(queue) == 1
    assert queue[0].kind == "sync"


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


# --- periodic resync ------------------------------------------------------

def test_resync_enqueues_sync_for_errored_fork(tmp_path: Path):
    """A fork whose last job failed gets a new 'sync' job on the next tick."""
    cfg = _cfg(tmp_path)
    conn = connect(cfg.db_path)
    init_schema(conn)
    set_root_repo(conn, "https://github.com/acme/root")
    fork = add_fork_manual(conn, "https://github.com/alice/root")
    j = jobs.claim_next(conn); jobs.mark_failed(conn, j.id, "boom")

    sched = scheduler.Scheduler(cfg)
    enqueued = sched._enqueue_periodic_resync(conn)

    assert enqueued == 1
    new_queued = [j for j in jobs.jobs_for_fork(conn, fork.id) if j.status == "queued"]
    assert len(new_queued) == 1
    assert new_queued[0].kind == "sync"


def test_resync_skips_fork_with_already_queued_work(tmp_path: Path):
    """A fork that still has a queued job is left alone — no duplicates."""
    cfg = _cfg(tmp_path)
    conn = connect(cfg.db_path)
    init_schema(conn)
    set_root_repo(conn, "https://github.com/acme/root")
    fork = add_fork_manual(conn, "https://github.com/alice/root")
    # The auto-enqueued 'full' job is already queued.

    sched = scheduler.Scheduler(cfg)
    enqueued = sched._enqueue_periodic_resync(conn)

    assert enqueued == 0
    queued = [j for j in jobs.jobs_for_fork(conn, fork.id) if j.status == "queued"]
    assert len(queued) == 1  # still just the original


def test_resync_skips_fork_with_running_job(tmp_path: Path):
    cfg = _cfg(tmp_path)
    conn = connect(cfg.db_path)
    init_schema(conn)
    set_root_repo(conn, "https://github.com/acme/root")
    fork = add_fork_manual(conn, "https://github.com/alice/root")
    jobs.claim_next(conn)  # transitions the auto-enqueued job to running

    sched = scheduler.Scheduler(cfg)
    enqueued = sched._enqueue_periodic_resync(conn)
    assert enqueued == 0


def test_resync_enqueues_after_successful_completion(tmp_path: Path):
    """A fork whose last job finished successfully also gets resynced
    periodically — keeps the leaderboard fresh."""
    cfg = _cfg(tmp_path)
    conn = connect(cfg.db_path)
    init_schema(conn)
    set_root_repo(conn, "https://github.com/acme/root")
    fork = add_fork_manual(conn, "https://github.com/alice/root")
    j = jobs.claim_next(conn); jobs.mark_done(conn, j.id)

    sched = scheduler.Scheduler(cfg)
    enqueued = sched._enqueue_periodic_resync(conn)

    assert enqueued == 1


def test_resync_handles_multiple_forks(tmp_path: Path):
    cfg = _cfg(tmp_path)
    conn = connect(cfg.db_path)
    init_schema(conn)
    set_root_repo(conn, "https://github.com/acme/root")
    f1 = add_fork_manual(conn, "https://github.com/alice/root")
    f2 = add_fork_manual(conn, "https://github.com/bob/root")
    f3 = add_fork_manual(conn, "https://github.com/charlie/root")

    # Drain initial jobs with mixed outcomes.
    jobs.mark_done(conn, jobs.claim_next(conn).id)
    jobs.mark_failed(conn, jobs.claim_next(conn).id, "x")
    jobs.mark_done(conn, jobs.claim_next(conn).id)

    sched = scheduler.Scheduler(cfg)
    enqueued = sched._enqueue_periodic_resync(conn)
    assert enqueued == 3


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
