"""Tests for git_ops helpers not already exercised via analysis/exercises."""
from __future__ import annotations

from pathlib import Path

import pygit2

from app.git_ops import root_commit_shas
from app.repos import RootRepo, template_root_shas
from app.storage import repo_path
from tests.git_helpers import init_repo, write_and_commit


def _build_history(path: Path) -> str:
    """Two commits on main + a side branch. Returns the root (first) SHA."""
    init_repo(path)
    repo = pygit2.Repository(str(path))
    root_sha = write_and_commit(repo, {"README.md": "init\n"}, message="init")
    second = write_and_commit(repo, {"a.txt": "a\n"}, message="second")
    # Branch off `second` so the side branch shares the same root commit.
    write_and_commit(repo, {"b.txt": "b\n"}, message="branch tip",
                     branch="feature", parents=[second])
    return root_sha


def test_root_commit_shas_returns_the_parentless_commit(tmp_path: Path):
    root_sha = _build_history(tmp_path / "repo")

    shas = root_commit_shas(tmp_path / "repo")

    assert shas == {root_sha}


def test_template_root_shas_reads_clone_under_repos_dir(tmp_path: Path):
    repos_dir = tmp_path / "repos"
    root = RootRepo(id=1, url="https://github.com/acme/tmpl",
                    platform="github", owner="acme", name="tmpl")
    local = repo_path(repos_dir, root.owner, root.name)
    root_sha = _build_history(local)

    assert template_root_shas(repos_dir, root) == {root_sha}


def test_template_root_shas_empty_when_not_cloned(tmp_path: Path):
    root = RootRepo(id=1, url="https://github.com/acme/tmpl",
                    platform="github", owner="acme", name="tmpl")
    # repos_dir exists but the template was never fetched
    assert template_root_shas(tmp_path / "repos", root) == set()
