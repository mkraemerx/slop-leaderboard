"""Acceptance tests for FR-06 Solution Comparison."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pygit2
import pytest

from app import analysis, comparison, exercises, jobs
from app.repos import add_fork_manual, set_root_repo

from tests.git_helpers import init_repo, make_local_sync, write_and_commit


def _build_fork(tmp_path: Path, owner: str, files_per_branch: dict[str, dict[str, str]]):
    """Build a small fork. The fork inherits a single 'init' commit on main
    from the (implicit) root, then optionally creates exercise branches."""
    fork_path = tmp_path / owner
    init_repo(fork_path)
    fr = pygit2.Repository(str(fork_path))
    write_and_commit(fr, {"README.md": "r\n"}, message="init",
                     author_name="Instructor",
                     author_email="ins@example.com",
                     when=datetime(2026, 1, 1, tzinfo=timezone.utc))
    for branch, files in files_per_branch.items():
        if branch != "main":
            base = fr.references["refs/heads/main"].target
            fr.create_branch(branch, fr.get(base))
        write_and_commit(
            fr, files, message=f"{owner} solves {branch}",
            author_name=owner.title(),
            author_email=f"{owner}@example.com",
            when=datetime(2026, 5, 5, tzinfo=timezone.utc),
            branch=branch,
        )
    return fork_path


def _setup(db, tmp_path: Path,
           per_fork: dict[str, dict[str, dict[str, str]]]):
    """Build root + per_fork mapping, register all forks, sync, refresh root."""
    root_path = tmp_path / "root"
    init_repo(root_path)
    rr = pygit2.Repository(str(root_path))
    write_and_commit(rr, {"README.md": "r\n"}, message="init",
                     author_name="Instructor",
                     author_email="ins@example.com",
                     when=datetime(2026, 1, 1, tzinfo=timezone.utc))

    set_root_repo(db, "https://github.com/acme/root")
    fork_paths: dict[str, Path] = {}
    fork_ids: dict[str, int] = {}
    for owner, branches in per_fork.items():
        fork_paths[owner] = _build_fork(tmp_path, owner, branches)
        f = add_fork_manual(db, f"https://github.com/{owner}/root")
        jobs.mark_done(db, jobs.claim_next(db).id)
        fork_ids[owner] = f.id

    base_dir = tmp_path / "data" / "repos"

    def router(_url: str, dest: Path) -> None:
        if "acme" in dest.name:
            return make_local_sync(root_path)("x", dest)
        for owner, p in fork_paths.items():
            if owner in dest.name:
                return make_local_sync(p)("x", dest)
        raise AssertionError(f"unrouted dest {dest}")

    for fid in fork_ids.values():
        analysis.analyze_fork(db, fid, base_dir, sync=router)
    exercises.refresh_root(db, base_dir, sync=router)
    return base_dir, fork_ids


def test_comparison_lists_forks_with_the_exercise(db, tmp_path: Path):
    base_dir, fork_ids = _setup(db, tmp_path, {
        "alice": {
            "main": {},  # avoided: exists
            "exercise-01": {"a.py": "alice\n"},
        },
        "bob": {
            "exercise-01": {"a.py": "bob\n", "b.py": "bob extra\n"},
        },
        "charlie": {
            # Charlie does NOT have exercise-01
            "exercise-02": {"c.py": "c\n"},
        },
    })

    comp = comparison.compute_comparison(db, ("exercise-01", "branch"), base_dir)

    owners = {s.owner for s in comp.solutions}
    assert owners == {"alice", "bob"}
    assert all(s.insertions > 0 for s in comp.solutions)


def test_diff_size_and_files_relative_to_root(db, tmp_path: Path):
    base_dir, fork_ids = _setup(db, tmp_path, {
        "alice": {
            "exercise-01": {"a.py": "x = 1\ny = 2\nz = 3\n"},
        },
    })
    comp = comparison.compute_comparison(db, ("exercise-01", "branch"), base_dir)
    [sol] = comp.solutions
    assert sol.owner == "alice"
    assert "a.py" in sol.files_changed
    assert sol.insertions == 3
    assert sol.deletions == 0


def test_jaccard_similarity_for_same_files(db, tmp_path: Path):
    """Two forks editing the same file set get similarity 1.0."""
    base_dir, fork_ids = _setup(db, tmp_path, {
        "alice": {"exercise-01": {"shared.py": "alice\n"}},
        "bob": {"exercise-01": {"shared.py": "bob\n"}},
    })

    comp = comparison.compute_comparison(db, ("exercise-01", "branch"), base_dir)
    [pair] = comp.similarities
    assert pair.similarity == pytest.approx(1.0)


def test_jaccard_similarity_for_disjoint_files(db, tmp_path: Path):
    base_dir, fork_ids = _setup(db, tmp_path, {
        "alice": {"exercise-01": {"a.py": "x\n"}},
        "bob": {"exercise-01": {"b.py": "y\n"}},
    })

    comp = comparison.compute_comparison(db, ("exercise-01", "branch"), base_dir)
    [pair] = comp.similarities
    assert pair.similarity == pytest.approx(0.0)


def test_partial_overlap_jaccard(db, tmp_path: Path):
    """Two of three files shared → similarity = 2/4 = 0.5."""
    base_dir, fork_ids = _setup(db, tmp_path, {
        "alice": {"exercise-01": {
            "common1.py": "a\n", "common2.py": "a\n", "alice_only.py": "a\n",
        }},
        "bob": {"exercise-01": {
            "common1.py": "b\n", "common2.py": "b\n", "bob_only.py": "b\n",
        }},
    })

    comp = comparison.compute_comparison(db, ("exercise-01", "branch"), base_dir)
    [pair] = comp.similarities
    # 2 shared / 4 total = 0.5
    assert pair.similarity == pytest.approx(0.5)


def test_comparison_works_for_tag_exercises(db, tmp_path: Path):
    """Tags should be supported, not just branches."""
    base_dir, fork_ids = _setup(db, tmp_path, {
        "alice": {"exercise-01": {"a.py": "x\n"}},
    })
    # Add a tag pointing to alice's exercise tip.
    fr = pygit2.Repository(str(tmp_path / "alice"))
    tip = fr.references["refs/heads/exercise-01"].target
    fr.create_reference("refs/tags/submission-1", tip)

    # Re-analyse so the new tag is captured.
    analysis.analyze_fork(db, fork_ids["alice"], base_dir,
                          sync=make_local_sync(tmp_path / "alice"))

    comp = comparison.compute_comparison(db, ("submission-1", "tag"), base_dir)
    assert len(comp.solutions) == 1
    assert comp.solutions[0].owner == "alice"


def test_no_solutions_when_no_fork_has_the_ref(db, tmp_path: Path):
    base_dir, fork_ids = _setup(db, tmp_path, {
        "alice": {"exercise-01": {"a.py": "x\n"}},
    })
    comp = comparison.compute_comparison(db, ("nonexistent", "branch"), base_dir)
    assert comp.solutions == ()
    assert comp.similarities == ()
