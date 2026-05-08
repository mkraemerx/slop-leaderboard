"""Acceptance tests for FR-02 Git Analysis."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import analysis, jobs
from app.git_ops import iter_new_commits
from app.repos import add_fork_manual, list_forks, set_root_repo, update_sync_status

from tests.git_helpers import init_repo, make_local_sync, make_merge_commit, write_and_commit


def _seed_fork(db, owner: str = "alice") -> int:
    set_root_repo(db, "https://github.com/acme/root")
    fork = add_fork_manual(db, f"https://github.com/{owner}/root")
    # consume the auto-enqueued job so individual tests aren't surprised
    jobs.claim_next(db)
    jobs.mark_done(db, jobs.get_job(db, 1).id)
    return fork.id


def _build_origin(tmp_path: Path) -> Path:
    """Make a tiny throwaway repo with two commits on `main`."""
    origin = tmp_path / "origin"
    repo = init_repo(origin)
    write_and_commit(repo, {"README.md": "hello\n"}, message="init",
                     when=datetime(2026, 5, 1, tzinfo=timezone.utc))
    write_and_commit(repo, {"src/main.py": "print('hi')\n"}, message="add main",
                     when=datetime(2026, 5, 2, tzinfo=timezone.utc))
    return origin


def test_full_analysis_imports_all_commits(db, tmp_path: Path):
    origin = _build_origin(tmp_path)
    fork_id = _seed_fork(db)

    inserted = analysis.analyze_fork(db, fork_id, tmp_path / "data" / "repos",
                                     sync=make_local_sync(origin))

    assert inserted == 2
    rows = db.execute(
        "SELECT sha, author_email, insertions, deletions, files_changed, is_merge "
        "FROM commits WHERE fork_id = ? ORDER BY author_time", (fork_id,)
    ).fetchall()
    assert len(rows) == 2
    assert all(r["author_email"] == "alice@example.com" for r in rows)
    assert all(r["is_merge"] == 0 for r in rows)
    # the second commit added a file with one line
    assert rows[1]["insertions"] >= 1
    assert rows[1]["files_changed"] >= 1


def test_subsequent_sync_only_processes_new_commits(db, tmp_path: Path):
    origin = _build_origin(tmp_path)
    fork_id = _seed_fork(db)
    sync = make_local_sync(origin)

    first = analysis.analyze_fork(db, fork_id, tmp_path / "data" / "repos",
                                  sync=sync)
    assert first == 2

    # Add a third commit and re-sync.
    import pygit2
    repo = pygit2.Repository(str(origin))
    write_and_commit(repo, {"src/main.py": "print('hi')\nprint('again')\n"},
                     message="extend",
                     when=datetime(2026, 5, 3, tzinfo=timezone.utc))

    second = analysis.analyze_fork(db, fork_id, tmp_path / "data" / "repos",
                                   sync=sync)
    assert second == 1, "incremental sync must skip already-stored commits"

    [(total,)] = db.execute(
        "SELECT COUNT(*) FROM commits WHERE fork_id = ?", (fork_id,)
    ).fetchall()
    assert total == 3


def test_merge_commit_is_stored_and_marked(db, tmp_path: Path):
    """FR-02 AC4: merge commits are stored but flagged so leaderboard can skip."""
    origin = tmp_path / "origin"
    repo = init_repo(origin)
    write_and_commit(repo, {"a.txt": "1\n"}, message="a1")
    # branch 'feature' off main
    head = repo.references["refs/heads/main"].target
    repo.create_branch("feature", repo.get(head))
    write_and_commit(repo, {"b.txt": "1\n"}, message="b1", branch="feature")
    write_and_commit(repo, {"a.txt": "1\n2\n"}, message="a2", branch="main")
    merge_sha = make_merge_commit(repo, branch="main",
                                  parent_branch="main",
                                  other_branch="feature")

    fork_id = _seed_fork(db)
    analysis.analyze_fork(db, fork_id, tmp_path / "data" / "repos",
                          sync=make_local_sync(origin))

    row = db.execute(
        "SELECT is_merge, parent_count FROM commits WHERE sha = ?", (merge_sha,)
    ).fetchone()
    assert row is not None, "merge commit must be stored"
    assert row["is_merge"] == 1
    assert row["parent_count"] == 2


def test_failure_during_sync_leaves_existing_data_intact(db, tmp_path: Path):
    """FR-02 AC3: a failed sync sets error state and does not lose data."""
    origin = _build_origin(tmp_path)
    fork_id = _seed_fork(db)

    # First successful run
    analysis.analyze_fork(db, fork_id, tmp_path / "data" / "repos",
                          sync=make_local_sync(origin))
    [(before,)] = db.execute(
        "SELECT COUNT(*) FROM commits WHERE fork_id = ?", (fork_id,)
    ).fetchall()
    assert before == 2

    # Inject a failing sync callable into run_one_job
    def failing_sync(url: str, dest: Path) -> None:
        raise RuntimeError("network down")

    jobs.enqueue_analysis(db, fork_id, kind="sync")
    finished = analysis.run_one_job(db, tmp_path / "data" / "repos",
                                    sync=failing_sync)
    assert finished.status == "failed"
    assert "network down" in (finished.error or "")

    # Existing rows untouched
    [(after,)] = db.execute(
        "SELECT COUNT(*) FROM commits WHERE fork_id = ?", (fork_id,)
    ).fetchall()
    assert after == before

    # Fork is in error state
    [fork] = list_forks(db)
    assert fork.sync_status == "error"
    assert fork.sync_error is not None and "network down" in fork.sync_error


def test_run_one_job_marks_done_on_success(db, tmp_path: Path):
    origin = _build_origin(tmp_path)
    fork_id = _seed_fork(db)
    jobs.enqueue_analysis(db, fork_id, kind="sync")

    finished = analysis.run_one_job(db, tmp_path / "data" / "repos",
                                    sync=make_local_sync(origin))

    assert finished.status == "done"
    [fork] = list_forks(db)
    assert fork.sync_status == "ok"
    assert fork.last_analysed_at is not None


def test_run_one_job_returns_none_when_queue_empty(db, tmp_path: Path):
    assert analysis.run_one_job(db, tmp_path / "data" / "repos") is None


def test_store_commits_is_idempotent_on_replay(db, tmp_path: Path):
    """QR-02: restarting mid-analysis must not duplicate commit rows."""
    origin = _build_origin(tmp_path)
    fork_id = _seed_fork(db)

    analysis.analyze_fork(db, fork_id, tmp_path / "data" / "repos",
                          sync=make_local_sync(origin))
    # Force a replay: pretend nothing was stored.
    seen = analysis.known_commit_shas(db, fork_id)
    assert len(seen) == 2

    # Re-importing the *same* commits must INSERT OR IGNORE.
    new_commits = list(iter_new_commits(tmp_path / "data" / "repos" / "alice__root", set()))
    inserted = analysis.store_commits(db, fork_id, new_commits)
    assert inserted == 0  # all already there

    [(total,)] = db.execute(
        "SELECT COUNT(*) FROM commits WHERE fork_id = ?", (fork_id,)
    ).fetchall()
    assert total == 2
