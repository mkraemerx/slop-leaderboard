"""Tests for the /debug/jobs page and recent_failed_jobs helper."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import analysis, jobs
from app.repos import add_fork_manual, set_root_repo

from tests.test_ui import _build_app, _login


def test_recent_failed_jobs_returns_failures_in_reverse_chronological_order(db):
    set_root_repo(db, "https://github.com/acme/root")
    fork = add_fork_manual(db, "https://github.com/alice/root")
    # drain the auto-enqueued 'full' job
    j = jobs.claim_next(db); jobs.mark_done(db, j.id)

    # Two failures, two successes; only failures should be listed.
    jobs.enqueue_analysis(db, fork.id, kind="sync")
    j1 = jobs.claim_next(db); jobs.mark_failed(db, j1.id, "first failure\nstack...")
    jobs.enqueue_analysis(db, fork.id, kind="sync")
    j2 = jobs.claim_next(db); jobs.mark_done(db, j2.id)
    jobs.enqueue_analysis(db, fork.id, kind="sync")
    j3 = jobs.claim_next(db); jobs.mark_failed(db, j3.id, "second failure\nstack...")

    rows = jobs.recent_failed_jobs(db)
    assert [r.job_id for r in rows] == [j3.id, j1.id]
    assert rows[0].error.startswith("second failure")
    assert rows[1].error.startswith("first failure")
    # Fork identity joined in
    assert rows[0].fork_owner == "alice"
    assert rows[0].fork_url == "https://github.com/alice/root"


def test_recent_failed_jobs_respects_limit(db):
    set_root_repo(db, "https://github.com/acme/root")
    fork = add_fork_manual(db, "https://github.com/alice/root")
    jobs.mark_done(db, jobs.claim_next(db).id)

    for _ in range(150):
        jobs.enqueue_analysis(db, fork.id, kind="sync")
        j = jobs.claim_next(db)
        jobs.mark_failed(db, j.id, "oops")

    assert len(jobs.recent_failed_jobs(db, limit=100)) == 100
    assert len(jobs.recent_failed_jobs(db, limit=5)) == 5


def test_recent_failed_jobs_excludes_running_and_done(db):
    set_root_repo(db, "https://github.com/acme/root")
    fork = add_fork_manual(db, "https://github.com/alice/root")
    # Auto-enqueued full job, leave it queued.
    rows = jobs.recent_failed_jobs(db)
    assert rows == []


def test_debug_jobs_page_renders_for_authed_user(tmp_path):
    app = _build_app(tmp_path)
    add_fork_manual(app.state.db, "https://github.com/alice/root")
    j = jobs.claim_next(app.state.db)
    jobs.mark_failed(app.state.db, j.id,
                     "RuntimeError: clone failed\n\nTraceback (most recent...)")

    with TestClient(app) as client:
        _login(client)
        resp = client.get("/debug/jobs")
        assert resp.status_code == 200
        # The fork identity and the traceback both appear in the page body
        assert "alice" in resp.text
        assert "RuntimeError: clone failed" in resp.text
        assert "Traceback" in resp.text


def test_debug_jobs_page_requires_auth(tmp_path):
    app = _build_app(tmp_path)
    with TestClient(app, follow_redirects=False) as client:
        resp = client.get("/debug/jobs")
        assert resp.status_code == 303
        assert resp.headers["location"] == "/login"


def test_failed_run_one_job_stores_full_traceback(db, tmp_path):
    """analysis.run_one_job stores the full traceback in analysis_jobs.error
    while keeping the short message on forks.sync_error."""
    set_root_repo(db, "https://github.com/acme/root")
    fork = add_fork_manual(db, "https://github.com/alice/root")
    # drain auto-enqueued
    j0 = jobs.claim_next(db); jobs.mark_done(db, j0.id)

    jobs.enqueue_analysis(db, fork.id, kind="sync")

    def boom(url, dest):
        raise ConnectionError("dns unreachable")

    finished = analysis.run_one_job(db, tmp_path / "repos", sync=boom)
    assert finished is not None
    assert finished.status == "failed"

    # job.error has full traceback (includes file paths)
    full = jobs.get_job(db, finished.id).error
    assert "ConnectionError: dns unreachable" in (full or "")
    assert "Traceback" in (full or "")

    # fork.sync_error is just the short message (no traceback)
    from app.repos import list_forks
    [f] = list_forks(db)
    assert "ConnectionError: dns unreachable" in (f.sync_error or "")
    assert "Traceback" not in (f.sync_error or "")
