"""Acceptance tests for FR-04 Exercises."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pygit2

from app import analysis, exercises, jobs
from app.repos import add_fork_manual, set_root_repo

from tests.git_helpers import init_repo, make_local_sync, write_and_commit


def _setup_root_and_fork(
    db, tmp_path: Path,
    fork_branches: dict[str, list[tuple[str, str]]] | None = None,
    fork_owner: str = "alice",
    fork_email: str = "alice@example.com",
):
    """Build a root + one fork. `fork_branches` maps branch name to a list
    of (filename, content) commits. The root has only `main`.

    Returns (root_path, fork_path, fork_id, fork_sync).
    """
    root_path = tmp_path / "root"
    init_repo(root_path)
    root_repo = pygit2.Repository(str(root_path))
    write_and_commit(root_repo, {"README.md": "root\n"}, message="init",
                     author_name="Instructor", author_email="ins@example.com",
                     when=datetime(2026, 1, 1, tzinfo=timezone.utc))

    # Fork starts as a copy of root and grows new branches.
    fork_path = tmp_path / "fork"
    init_repo(fork_path)
    fork_repo = pygit2.Repository(str(fork_path))
    write_and_commit(fork_repo, {"README.md": "root\n"}, message="init",
                     author_name="Instructor", author_email="ins@example.com",
                     when=datetime(2026, 1, 1, tzinfo=timezone.utc))

    fork_branches = fork_branches or {}
    for branch, commits in fork_branches.items():
        if branch != "main":
            base = fork_repo.references["refs/heads/main"].target
            fork_repo.create_branch(branch, fork_repo.get(base))
        for i, (path, content) in enumerate(commits):
            write_and_commit(
                fork_repo, {path: content}, message=f"{branch} step {i}",
                author_name=fork_owner.title(), author_email=fork_email,
                when=datetime(2026, 5, 1 + i, tzinfo=timezone.utc),
                branch=branch,
            )

    set_root_repo(db, "https://github.com/acme/root")
    fork = add_fork_manual(db, f"https://github.com/{fork_owner}/root")
    job = jobs.claim_next(db)
    jobs.mark_done(db, job.id)

    base_dir = tmp_path / "data" / "repos"

    def _ack(_url: str, dest: Path) -> None:
        # route to root or fork by destination directory name
        if "acme" in dest.name:
            return make_local_sync(root_path)("ignored", dest)
        return make_local_sync(fork_path)("ignored", dest)

    analysis.analyze_fork(db, fork.id, base_dir, sync=_ack)
    exercises.refresh_root(db, base_dir, sync=_ack)
    return root_path, fork_path, fork.id, _ack


def test_exercise_is_a_branch_in_fork_but_not_in_root(db, tmp_path: Path):
    _setup_root_and_fork(db, tmp_path, fork_branches={
        "main": [],
        "exercise-01": [("solution.py", "print('a')\n")],
    })
    found = exercises.list_exercises(db)
    names = {e.name for e in found}
    assert "exercise-01" in names
    assert "main" not in names  # main is in root → not an exercise


def test_fork_count_per_exercise_aggregates(db, tmp_path: Path):
    """An exercise with two forks shows fork_count = 2."""
    base_dir = tmp_path / "data" / "repos"
    root_path = tmp_path / "root"
    init_repo(root_path)
    root_repo = pygit2.Repository(str(root_path))
    write_and_commit(root_repo, {"README.md": "r\n"}, message="init",
                     when=datetime(2026, 1, 1, tzinfo=timezone.utc))

    set_root_repo(db, "https://github.com/acme/root")
    set_root_repo  # noqa
    forks_data = []
    for owner in ("alice", "bob"):
        fork_path = tmp_path / f"fork-{owner}"
        init_repo(fork_path)
        fr = pygit2.Repository(str(fork_path))
        write_and_commit(fr, {"README.md": "r\n"}, message="init",
                         when=datetime(2026, 1, 1, tzinfo=timezone.utc))
        base = fr.references["refs/heads/main"].target
        fr.create_branch("exercise-01", fr.get(base))
        write_and_commit(fr, {"x.py": f"# {owner}\n"}, message="solve",
                         author_name=owner.title(),
                         author_email=f"{owner}@example.com",
                         when=datetime(2026, 5, 2, tzinfo=timezone.utc),
                         branch="exercise-01")
        f = add_fork_manual(db, f"https://github.com/{owner}/root")
        job = jobs.claim_next(db)
        jobs.mark_done(db, job.id)
        forks_data.append((f.id, fork_path))

    def router(_url: str, dest: Path) -> None:
        if "acme" in dest.name:
            return make_local_sync(root_path)("x", dest)
        for fid, fp in forks_data:
            owner = fp.name.split("-")[1]
            if owner in dest.name:
                return make_local_sync(fp)("x", dest)
        raise AssertionError(f"unrouted dest {dest}")

    for fid, _ in forks_data:
        analysis.analyze_fork(db, fid, base_dir, sync=router)
    exercises.refresh_root(db, base_dir, sync=router)

    [ex] = [e for e in exercises.list_exercises(db) if e.name == "exercise-01"]
    assert ex.fork_count == 2


def test_first_author_is_earliest_committer_on_exercise(db, tmp_path: Path):
    _setup_root_and_fork(db, tmp_path, fork_branches={
        "main": [],
        "exercise-01": [("a.py", "x\n"), ("a.py", "x\ny\n")],
    }, fork_owner="alice", fork_email="alice@example.com")

    found = {e.name: e for e in exercises.list_exercises(db)}
    assert found["exercise-01"].first_author_email == "alice@example.com"
    assert found["exercise-01"].first_commit_time is not None


def test_tag_present_in_fork_but_not_root_is_an_exercise(db, tmp_path: Path):
    root_path = tmp_path / "root"
    init_repo(root_path)
    rr = pygit2.Repository(str(root_path))
    write_and_commit(rr, {"README.md": "r\n"}, message="init",
                     when=datetime(2026, 1, 1, tzinfo=timezone.utc))

    fork_path = tmp_path / "fork"
    init_repo(fork_path)
    fr = pygit2.Repository(str(fork_path))
    write_and_commit(fr, {"README.md": "r\n"}, message="init",
                     when=datetime(2026, 1, 1, tzinfo=timezone.utc))
    write_and_commit(fr, {"x.py": "1\n"}, message="solve",
                     author_name="Alice", author_email="alice@example.com",
                     when=datetime(2026, 5, 2, tzinfo=timezone.utc))
    tip = fr.references["refs/heads/main"].target
    fr.create_reference("refs/tags/submission-1", tip)

    set_root_repo(db, "https://github.com/acme/root")
    fork = add_fork_manual(db, "https://github.com/alice/root")
    jobs.mark_done(db, jobs.claim_next(db).id)

    def router(_url: str, dest: Path) -> None:
        if "acme" in dest.name:
            return make_local_sync(root_path)("x", dest)
        return make_local_sync(fork_path)("x", dest)

    base_dir = tmp_path / "data" / "repos"
    analysis.analyze_fork(db, fork.id, base_dir, sync=router)
    exercises.refresh_root(db, base_dir, sync=router)

    found = exercises.list_exercises(db)
    tag_exercise = [e for e in found if e.ref_type == "tag"]
    assert any(e.name == "submission-1" for e in tag_exercise)


def test_exercise_disappears_when_no_fork_retains_it(db, tmp_path: Path):
    """FR-04 AC4: deleting the branch upstream removes the exercise."""
    root_path, fork_path, fork_id, sync = _setup_root_and_fork(
        db, tmp_path, fork_branches={
            "main": [],
            "exercise-01": [("a.py", "1\n")],
        })
    base_dir = tmp_path / "data" / "repos"

    # Delete exercise-01 from the fork.
    fr = pygit2.Repository(str(fork_path))
    fr.references.delete("refs/heads/exercise-01")
    analysis.analyze_fork(db, fork_id, base_dir, sync=sync)
    exercises.refresh_root(db, base_dir, sync=sync)

    names = {e.name for e in exercises.list_exercises(db)}
    assert "exercise-01" not in names


def test_branch_added_to_root_drops_off_exercise_list(db, tmp_path: Path):
    """If the instructor pushes the same branch name to the root repo, the
    fork's branch is no longer an exercise."""
    root_path, fork_path, fork_id, sync = _setup_root_and_fork(
        db, tmp_path, fork_branches={
            "main": [],
            "shared-branch": [("a.py", "1\n")],
        })
    base_dir = tmp_path / "data" / "repos"

    rr = pygit2.Repository(str(root_path))
    rr.create_branch("shared-branch", rr.get(rr.head.target))
    exercises.refresh_root(db, base_dir, sync=sync)

    names = {e.name for e in exercises.list_exercises(db)}
    assert "shared-branch" not in names
