"""Acceptance tests for FR-03 Commit Metrics."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app import analysis, jobs
from app.repos import add_fork_manual, set_root_repo

from tests.git_helpers import init_repo, make_local_sync, write_and_commit


def _seed_fork(db) -> int:
    set_root_repo(db, "https://github.com/acme/root")
    fork = add_fork_manual(db, "https://github.com/alice/root")
    job = jobs.claim_next(db)
    jobs.mark_done(db, job.id)
    return fork.id


def test_commit_metrics_capture_all_required_fields(db, tmp_path: Path):
    """FR-03: per-commit author, email, timestamp, lines, files, merge flag."""
    origin = tmp_path / "origin"
    repo = init_repo(origin)
    write_and_commit(
        repo, {"src/app.py": "x = 1\ny = 2\n"},
        message="initial",
        author_name="Alice", author_email="alice@example.com",
        when=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
    )

    fork_id = _seed_fork(db)
    analysis.analyze_fork(db, fork_id, tmp_path / "data" / "repos",
                          sync=make_local_sync(origin))

    [row] = db.execute(
        "SELECT author_name, author_email, author_time, files_changed, "
        "       insertions, deletions, is_merge "
        "FROM commits WHERE fork_id = ?", (fork_id,)
    ).fetchall()
    assert row["author_name"] == "Alice"
    assert row["author_email"] == "alice@example.com"
    assert "2026-05-01" in row["author_time"]
    assert row["files_changed"] == 1
    assert row["insertions"] == 2
    assert row["deletions"] == 0
    assert row["is_merge"] == 0


def test_per_category_breakdown_is_stored(db, tmp_path: Path):
    """FR-03: lines added/removed broken down by category."""
    origin = tmp_path / "origin"
    repo = init_repo(origin)
    files = {
        "src/main.py": "def f():\n    return 1\n",         # code: 2 ins
        "tests/test_main.py": "def test_f():\n    pass\n",  # tests: 2 ins
        "README.md": "# hi\n",                                # docs: 1 ins
        ".github/workflows/ci.yml": "name: ci\n",             # config: 1 ins
    }
    write_and_commit(repo, files, message="seed",
                     when=datetime(2026, 5, 1, tzinfo=timezone.utc))

    fork_id = _seed_fork(db)
    analysis.analyze_fork(db, fork_id, tmp_path / "data" / "repos",
                          sync=make_local_sync(origin))

    [row] = db.execute(
        """
        SELECT code_insertions, tests_insertions,
               docs_insertions, config_insertions, insertions
        FROM commits WHERE fork_id = ?
        """, (fork_id,),
    ).fetchall()
    assert row["code_insertions"] == 2
    assert row["tests_insertions"] == 2
    assert row["docs_insertions"] == 1
    assert row["config_insertions"] == 1
    # Sum of category insertions ≤ total (some files may classify as 'other')
    cat_sum = (row["code_insertions"] + row["tests_insertions"]
               + row["docs_insertions"] + row["config_insertions"])
    assert cat_sum == row["insertions"]


def test_branches_and_tags_containing_each_commit(db, tmp_path: Path):
    """FR-03: which branches and tags contain that commit at analysis time."""
    origin = tmp_path / "origin"
    repo = init_repo(origin)
    base = write_and_commit(repo, {"a.txt": "1\n"}, message="base")
    write_and_commit(repo, {"a.txt": "1\n2\n"}, message="more on main")
    # branch off base
    repo.create_branch("exercise-01", repo.get(base))
    write_and_commit(repo, {"b.txt": "1\n"}, message="ex1 work",
                     branch="exercise-01")
    # tag on main HEAD
    main_tip = repo.references["refs/heads/main"].target
    repo.create_reference("refs/tags/v1", main_tip)

    fork_id = _seed_fork(db)
    analysis.analyze_fork(db, fork_id, tmp_path / "data" / "repos",
                          sync=make_local_sync(origin))

    rows = db.execute(
        """
        SELECT commit_sha, ref_name, ref_type
        FROM commit_refs
        WHERE fork_id = ?
        ORDER BY commit_sha, ref_name
        """, (fork_id,),
    ).fetchall()
    by_sha: dict[str, set[tuple[str, str]]] = {}
    for r in rows:
        by_sha.setdefault(r["commit_sha"], set()).add((r["ref_name"], r["ref_type"]))

    # `base` is reachable from main, exercise-01, AND tag v1
    assert ("main", "branch") in by_sha[base]
    assert ("exercise-01", "branch") in by_sha[base]
    assert ("v1", "tag") in by_sha[base]


def test_commit_refs_are_rebuilt_when_branch_deleted(db, tmp_path: Path):
    """If a branch disappears between syncs, commit_refs no longer lists it."""
    origin = tmp_path / "origin"
    repo = init_repo(origin)
    base = write_and_commit(repo, {"a.txt": "1\n"}, message="base")
    repo.create_branch("temp", repo.get(base))

    fork_id = _seed_fork(db)
    analysis.analyze_fork(db, fork_id, tmp_path / "data" / "repos",
                          sync=make_local_sync(origin))
    refs_before = {r["ref_name"] for r in db.execute(
        "SELECT ref_name FROM commit_refs WHERE fork_id = ? AND commit_sha = ?",
        (fork_id, base)).fetchall()}
    assert "temp" in refs_before

    # Delete the branch and re-sync.
    import pygit2
    repo = pygit2.Repository(str(origin))
    repo.references.delete("refs/heads/temp")
    analysis.analyze_fork(db, fork_id, tmp_path / "data" / "repos",
                          sync=make_local_sync(origin))

    refs_after = {r["ref_name"] for r in db.execute(
        "SELECT ref_name FROM commit_refs WHERE fork_id = ? AND commit_sha = ?",
        (fork_id, base)).fetchall()}
    assert "temp" not in refs_after
    assert "main" in refs_after  # main is still there


def test_renamed_file_categorised_under_new_path(db, tmp_path: Path):
    """A move shows up as code-deletion + tests-insertion (rename detection
    disabled by default in pygit2's diff stats)."""
    origin = tmp_path / "origin"
    repo = init_repo(origin)
    write_and_commit(repo, {"util.py": "x = 1\n"}, message="add util",
                     when=datetime(2026, 5, 1, tzinfo=timezone.utc))
    # Move util.py into tests/, with an extra line so the diff isn't pure
    # rename even if rename detection is enabled later.
    workdir = Path(repo.workdir)
    (workdir / "util.py").unlink()
    (workdir / "tests").mkdir(exist_ok=True)
    (workdir / "tests" / "util.py").write_text("x = 1\ny = 2\n")
    repo.index.remove("util.py")
    repo.index.add("tests/util.py")
    repo.index.write()
    write_and_commit(repo, {}, message="move into tests",
                     when=datetime(2026, 5, 2, tzinfo=timezone.utc))

    fork_id = _seed_fork(db)
    analysis.analyze_fork(db, fork_id, tmp_path / "data" / "repos",
                          sync=make_local_sync(origin))

    rows = db.execute(
        "SELECT files_changed, code_insertions, tests_insertions, "
        "       code_deletions, tests_deletions FROM commits "
        "WHERE fork_id = ? ORDER BY author_time", (fork_id,)
    ).fetchall()
    # commits are time-ordered: row 0 is 'add util', row 1 is 'move into tests'
    # The move has both an addition (in tests/) and a deletion (in code).
    move = rows[1]
    assert move["tests_insertions"] >= 1
    assert move["code_deletions"] >= 1
