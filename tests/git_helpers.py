"""Tiny helpers to build real Git repositories on disk for tests.

Using pygit2 directly keeps tests fast and avoids subprocess assumptions.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pygit2


def make_local_sync(origin: Path) -> Callable[[str, Path], None]:
    """Return a `clone_or_fetch`-shaped callable that clones from `origin`,
    ignoring the URL argument entirely. Use as the `sync=` injection in tests
    so analysis logic can run without network access.
    """
    from app.git_ops import _MIRROR_REFSPECS, _is_repo

    def _sync(_url: str, dest: Path) -> None:
        if dest.exists() and _is_repo(dest):
            repo = pygit2.Repository(str(dest))
            remote = next((r for r in repo.remotes if r.name == "origin"), None)
            if remote is None:
                remote = repo.remotes.create("origin", str(origin))
            remote.fetch(refspecs=_MIRROR_REFSPECS,
                         callbacks=None, prune=pygit2.GIT_FETCH_PRUNE)
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        pygit2.clone_repository(str(origin), str(dest), bare=True)
        repo = pygit2.Repository(str(dest))
        repo.remotes["origin"].fetch(refspecs=_MIRROR_REFSPECS,
                                     callbacks=None,
                                     prune=pygit2.GIT_FETCH_PRUNE)
    return _sync


def init_repo(path: Path) -> pygit2.Repository:
    path.mkdir(parents=True, exist_ok=True)
    return pygit2.init_repository(str(path), bare=False)


def write_and_commit(
    repo: pygit2.Repository,
    files: dict[str, str],
    *,
    message: str,
    author_name: str = "Alice",
    author_email: str = "alice@example.com",
    branch: str = "main",
    parents: list[str] | None = None,
    when: datetime | None = None,
) -> str:
    workdir = Path(repo.workdir)
    for rel, content in files.items():
        target = workdir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        repo.index.add(rel)
    repo.index.write()
    tree_oid = repo.index.write_tree()

    timestamp = int((when or datetime.now(timezone.utc)).timestamp())
    sig = pygit2.Signature(author_name, author_email, timestamp, 0)

    parent_oids: list[pygit2.Oid] = []
    if parents is None:
        # If there's a tip on the branch already, use it as parent.
        ref_name = f"refs/heads/{branch}"
        if ref_name in repo.references:
            parent_oids = [repo.references[ref_name].target]
    else:
        parent_oids = [pygit2.Oid(hex=p) for p in parents]

    commit_oid = repo.create_commit(
        f"refs/heads/{branch}", sig, sig, message, tree_oid, parent_oids
    )
    repo.set_head(f"refs/heads/{branch}")
    return str(commit_oid)


def make_merge_commit(
    repo: pygit2.Repository,
    *,
    branch: str,
    parent_branch: str,
    other_branch: str,
    message: str = "merge",
) -> str:
    """Create a merge commit on `branch` whose tree is the same as
    `parent_branch`'s tip but with two parents."""
    p1 = repo.references[f"refs/heads/{parent_branch}"].target
    p2 = repo.references[f"refs/heads/{other_branch}"].target
    sig = pygit2.Signature("Merger", "merger@example.com",
                           int(datetime.now(timezone.utc).timestamp()), 0)
    # Use the parent_branch's tree to keep things deterministic.
    tree_oid = repo.get(p1).tree.id
    commit_oid = repo.create_commit(
        f"refs/heads/{branch}", sig, sig, message, tree_oid, [p1, p2]
    )
    return str(commit_oid)
