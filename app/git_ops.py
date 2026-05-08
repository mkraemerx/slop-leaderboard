"""pygit2-based git operations.

QR-05 requires all git interaction to go through library APIs (no shelling
out with user-supplied strings). pygit2 is a libgit2 binding and satisfies
that constraint.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from .classifier import CATEGORIES, classify


@dataclass(frozen=True)
class CategoryStats:
    insertions: int = 0
    deletions: int = 0


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
    by_category: dict[str, CategoryStats] = field(default_factory=dict)


@dataclass(frozen=True)
class RefInfo:
    name: str          # e.g. "main", "exercise-01", "v1.0"
    ref_type: str      # "branch" | "tag"
    tip_sha: str


_MIRROR_REFSPECS = [
    "+refs/heads/*:refs/heads/*",
    "+refs/tags/*:refs/tags/*",
]


def clone_or_fetch(url: str, dest: Path) -> None:
    """Clone `url` to `dest` as a bare mirror, or fetch into an existing
    clone. We use bare clones + explicit mirror refspecs so every branch and
    tag becomes a local ref — FR-03/FR-04 walk those directly.
    """
    import pygit2

    if dest.exists() and _is_repo(dest):
        repo = pygit2.Repository(str(dest))
        remote = next((r for r in repo.remotes if r.name == "origin"), None)
        if remote is None:
            remote = repo.remotes.create("origin", url)
        remote.fetch(refspecs=_MIRROR_REFSPECS,
                     callbacks=None, prune=pygit2.GIT_FETCH_PRUNE)
        return

    dest.parent.mkdir(parents=True, exist_ok=True)
    pygit2.clone_repository(url, str(dest), bare=True)
    repo = pygit2.Repository(str(dest))
    repo.remotes["origin"].fetch(refspecs=_MIRROR_REFSPECS,
                                 callbacks=None,
                                 prune=pygit2.GIT_FETCH_PRUNE)


def _is_repo(path: Path) -> bool:
    """True for both regular and bare clones we own."""
    return (path / "HEAD").exists() or (path / ".git").exists()


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
    if parent_count == 0:
        diff = commit.tree.diff_to_tree(swap=True)
    elif parent_count == 1:
        diff = repo.diff(commit.parents[0], commit)
    else:
        diff = repo.diff(commit.parents[0], commit)
    stats = diff.stats
    by_category = _diff_by_category(diff)
    author = commit.author
    return CommitInfo(
        sha=str(commit.id),
        author_name=author.name,
        author_email=author.email,
        author_time=_author_dt(author),
        parent_count=parent_count,
        is_merge=is_merge,
        files_changed=stats.files_changed,
        insertions=stats.insertions,
        deletions=stats.deletions,
        by_category=by_category,
    )


def _diff_by_category(diff) -> dict[str, CategoryStats]:
    """Sum insertions/deletions per category by classifying each patch's
    file path. Renames are counted under the new path's category."""
    sums: dict[str, list[int]] = {c: [0, 0] for c in CATEGORIES}
    for patch in diff:
        delta = patch.delta
        path = delta.new_file.path or delta.old_file.path
        category = classify(path)
        line_stats = patch.line_stats  # (context, additions, deletions)
        sums[category][0] += line_stats[1]
        sums[category][1] += line_stats[2]
    return {c: CategoryStats(insertions=ins, deletions=dele)
            for c, (ins, dele) in sums.items() if ins or dele}


def list_refs(repo_path: Path) -> list[RefInfo]:
    """Enumerate every local branch and tag. Lightweight tags and annotated
    tags are both reduced to their target commit via `peel(Commit)`."""
    import pygit2

    repo = pygit2.Repository(str(repo_path))
    out: list[RefInfo] = []
    for ref_name in repo.references:
        ref = repo.references[ref_name]
        try:
            tip = ref.peel(pygit2.Commit)
        except (KeyError, pygit2.GitError, ValueError):
            continue
        if ref_name.startswith("refs/heads/"):
            out.append(RefInfo(name=ref_name[len("refs/heads/"):],
                               ref_type="branch", tip_sha=str(tip.id)))
        elif ref_name.startswith("refs/tags/"):
            out.append(RefInfo(name=ref_name[len("refs/tags/"):],
                               ref_type="tag", tip_sha=str(tip.id)))
    return out


def commits_reachable_from(repo_path: Path, tip_sha: str) -> set[str]:
    """SHAs of every commit reachable from `tip_sha` (inclusive)."""
    import pygit2

    repo = pygit2.Repository(str(repo_path))
    out: set[str] = set()
    for commit in repo.walk(pygit2.Oid(hex=tip_sha), pygit2.GIT_SORT_NONE):
        out.add(str(commit.id))
    return out


def _author_dt(sig) -> str:
    from datetime import datetime, timezone
    return (
        datetime.fromtimestamp(sig.time, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
