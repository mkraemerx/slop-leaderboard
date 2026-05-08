"""pygit2-based git operations.

QR-05 requires all git interaction to go through library APIs (no shelling
out with user-supplied strings). pygit2 is a libgit2 binding and satisfies
that constraint.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class CommitInfo:
    sha: str
    author_name: str
    author_email: str
    author_time: str    # ISO-8601 UTC, e.g. "2026-05-08T12:34:56Z"
    parent_count: int
    is_merge: bool
    files_changed: int
    insertions: int
    deletions: int


def clone_or_fetch(url: str, dest: Path) -> None:
    """Clone `url` to `dest` as a bare-ish mirror, or fetch into an existing
    clone. We use `--mirror`-style refspecs so all branches and tags land
    locally — exercises (FR-04) need both.
    """
    import pygit2

    if dest.exists() and (dest / ".git").exists():
        repo = pygit2.Repository(str(dest))
        for remote in repo.remotes:
            if remote.name == "origin":
                remote.fetch(callbacks=None, prune=pygit2.GIT_FETCH_PRUNE)
                return
        # No origin? Add one and fetch.
        repo.remotes.create("origin", url)
        repo.remotes["origin"].fetch(callbacks=None,
                                     prune=pygit2.GIT_FETCH_PRUNE)
        return

    # Fresh clone. We don't need a working copy, but pygit2's
    # clone_repository does a full clone by default; that's fine for v1.
    dest.parent.mkdir(parents=True, exist_ok=True)
    pygit2.clone_repository(url, str(dest), bare=False)


def known_shas(repo_path: Path) -> set[str]:
    """All reachable commit SHAs across every local ref (branches and tags)."""
    import pygit2

    repo = pygit2.Repository(str(repo_path))
    walker = repo.walk(repo.head.target if not repo.head_is_unborn else None)
    seen: set[str] = set()

    # Walking from every ref guarantees we see commits reachable on any branch
    # or tag, not just HEAD.
    for ref_name in repo.references:
        try:
            ref = repo.references[ref_name]
            target = ref.peel(pygit2.Commit)
        except (KeyError, pygit2.GitError, ValueError):
            continue
        for commit in repo.walk(target.id, pygit2.GIT_SORT_NONE):
            seen.add(str(commit.id))
    return seen


def iter_new_commits(
    repo_path: Path, already_stored: set[str],
) -> Iterator[CommitInfo]:
    """Yield CommitInfo for every commit reachable from any ref that is not
    already in `already_stored`. Idempotent: a re-run with the same input
    will yield the same set.
    """
    import pygit2

    repo = pygit2.Repository(str(repo_path))
    seen_in_walk: set[str] = set()

    for ref_name in repo.references:
        try:
            ref = repo.references[ref_name]
            tip = ref.peel(pygit2.Commit)
        except (KeyError, pygit2.GitError, ValueError):
            continue
        for commit in repo.walk(tip.id, pygit2.GIT_SORT_TIME):
            sha = str(commit.id)
            if sha in seen_in_walk:
                continue
            seen_in_walk.add(sha)
            if sha in already_stored:
                continue
            yield _commit_info(repo, commit)


def _commit_info(repo, commit) -> CommitInfo:
    parent_count = len(commit.parents)
    is_merge = parent_count > 1
    insertions = deletions = files_changed = 0
    if parent_count == 0:
        # Root commit: diff against empty tree.
        diff = commit.tree.diff_to_tree(swap=True)
    elif parent_count == 1:
        diff = repo.diff(commit.parents[0], commit)
    else:
        # Merge commit: diff against first parent (matches `git log -m -1`).
        diff = repo.diff(commit.parents[0], commit)
    stats = diff.stats
    insertions = stats.insertions
    deletions = stats.deletions
    files_changed = stats.files_changed
    author = commit.author
    when_dt = _author_dt(author)
    return CommitInfo(
        sha=str(commit.id),
        author_name=author.name,
        author_email=author.email,
        author_time=when_dt,
        parent_count=parent_count,
        is_merge=is_merge,
        files_changed=files_changed,
        insertions=insertions,
        deletions=deletions,
    )


def _author_dt(sig) -> str:
    from datetime import datetime, timezone
    return (
        datetime.fromtimestamp(sig.time, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
