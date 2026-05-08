from __future__ import annotations

from pathlib import Path

import pytest

from app.storage import repo_path, slugify


def test_slugify_replaces_unsafe_characters():
    assert slugify("alice", "my-repo") == "alice__my-repo"
    assert slugify("acme/sub", "weird name") == "acme-sub__weird-name"


@pytest.mark.parametrize("owner,name", [
    ("../../etc", "passwd"),
    ("a", "../b"),
])
def test_repo_path_blocks_traversal(tmp_path: Path, owner, name):
    base = tmp_path / "repos"
    base.mkdir()
    target = repo_path(base, owner, name)
    assert target.is_relative_to(base)
    # The slug strips leading dots so traversal cannot escape.
    assert ".." not in target.relative_to(base).parts


def test_repo_path_rejects_pure_dot_segments(tmp_path: Path):
    base = tmp_path / "repos"
    base.mkdir()
    with pytest.raises(ValueError):
        repo_path(base, "..", "..")


def test_repo_path_returns_consistent_value(tmp_path: Path):
    base = tmp_path / "repos"
    base.mkdir()
    p1 = repo_path(base, "alice", "root")
    p2 = repo_path(base, "alice", "root")
    assert p1 == p2
    assert p1.parent == base.resolve()


def test_slugify_rejects_empty_segments():
    with pytest.raises(ValueError):
        slugify("", "name")
    with pytest.raises(ValueError):
        slugify("owner", "..")
