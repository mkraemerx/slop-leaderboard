from __future__ import annotations

from app import jobs
from app.repos import add_fork_manual, set_root_repo


def _seed_fork(db) -> int:
    set_root_repo(db, "https://github.com/acme/root")
    fork = add_fork_manual(db, "https://github.com/alice/root")
    return fork.id


def test_adding_a_fork_enqueues_an_analysis_job(db):
    """FR-02 AC1: newly added fork is enqueued for full analysis immediately."""
    fork_id = _seed_fork(db)
    queue = jobs.jobs_for_fork(db, fork_id)
    assert len(queue) == 1
    assert queue[0].status == "queued"
    assert queue[0].kind == "full"


def test_claim_next_returns_jobs_in_fifo_order(db):
    f1 = _seed_fork(db)
    fork2 = add_fork_manual(db, "https://github.com/bob/root")
    j1 = jobs.claim_next(db)
    j2 = jobs.claim_next(db)
    assert j1.fork_id == f1
    assert j2.fork_id == fork2.id
    assert jobs.claim_next(db) is None


def test_claim_next_marks_running_and_records_started_at(db):
    _seed_fork(db)
    job = jobs.claim_next(db)
    [(status, started_at)] = db.execute(
        "SELECT status, started_at FROM analysis_jobs WHERE id = ?", (job.id,)
    ).fetchall()
    assert status == "running"
    assert started_at is not None


def test_mark_done_and_failed_update_terminal_state(db):
    _seed_fork(db)
    job = jobs.claim_next(db)
    jobs.mark_done(db, job.id)
    refreshed = jobs.get_job(db, job.id)
    assert refreshed.status == "done"
    assert refreshed.error is None

    # Now a failure path
    fork2 = add_fork_manual(db, "https://github.com/bob/root")
    j2 = jobs.claim_next(db)
    jobs.mark_failed(db, j2.id, "boom")
    assert jobs.get_job(db, j2.id).status == "failed"
    assert jobs.get_job(db, j2.id).error == "boom"


def test_requeue_orphans_resets_running_jobs(db):
    """QR-02: jobs running at shutdown must be re-executed on restart."""
    _seed_fork(db)
    job = jobs.claim_next(db)
    assert job.status == "running"

    # Simulate a crash & restart: orphan recovery.
    affected = jobs.requeue_orphans(db)
    assert affected == 1

    refreshed = jobs.get_job(db, job.id)
    assert refreshed.status == "queued"
    assert jobs.claim_next(db).id == job.id  # re-claimable


def test_requeue_orphans_is_idempotent_on_clean_state(db):
    _seed_fork(db)
    assert jobs.requeue_orphans(db) == 0


def test_enqueue_analysis_rejects_invalid_kind(db):
    fork_id = _seed_fork(db)
    import pytest
    with pytest.raises(ValueError):
        jobs.enqueue_analysis(db, fork_id, kind="bogus")
