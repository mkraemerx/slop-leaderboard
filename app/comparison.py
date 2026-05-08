"""FR-06 Solution Comparison.

Given an exercise (ref_name + ref_type) we walk every fork that has the ref,
diff the fork's exercise tip against the latest base commit shared with the
root repository, and report changed files + Jaccard similarity between
forks (ASSUMPTION-009).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pygit2

from .storage import repo_path


@dataclass(frozen=True)
class ForkSolution:
    fork_id: int
    owner: str
    name: str
    insertions: int
    deletions: int
    files_changed: tuple[str, ...]


@dataclass(frozen=True)
class SimilarityPair:
    fork_a_id: int
    fork_b_id: int
    similarity: float          # 0.0 .. 1.0


@dataclass(frozen=True)
class Comparison:
    exercise_name: str
    exercise_type: str
    solutions: tuple[ForkSolution, ...]
    similarities: tuple[SimilarityPair, ...]


def compute_comparison(
    conn: sqlite3.Connection,
    exercise: tuple[str, str],
    base_repos_dir: Path,
) -> Comparison:
    name, ref_type = exercise
    forks = _forks_with_ref(conn, name, ref_type)
    root_shas = _root_commit_shas(conn)

    solutions: list[ForkSolution] = []
    file_sets: dict[int, frozenset[str]] = {}

    for fork in forks:
        local = repo_path(base_repos_dir, fork["owner"], fork["name"])
        try:
            sol = _diff_against_root(local, name, ref_type, root_shas, fork)
        except FileNotFoundError:
            # The clone doesn't exist yet (initial sync still pending);
            # skip this fork rather than 500 the comparison view.
            continue
        if sol is None:
            continue
        solutions.append(sol)
        file_sets[sol.fork_id] = frozenset(sol.files_changed)

    similarities: list[SimilarityPair] = []
    fork_ids = sorted(file_sets.keys())
    for i, a in enumerate(fork_ids):
        for b in fork_ids[i + 1:]:
            sim = _jaccard(file_sets[a], file_sets[b])
            similarities.append(SimilarityPair(a, b, sim))

    return Comparison(
        exercise_name=name,
        exercise_type=ref_type,
        solutions=tuple(solutions),
        similarities=tuple(similarities),
    )


def _forks_with_ref(conn, ref_name: str, ref_type: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT DISTINCT f.id, f.owner, f.name, f.url
        FROM commit_refs cr
        JOIN forks f ON f.id = cr.fork_id
        WHERE cr.ref_name = ? AND cr.ref_type = ?
        ORDER BY f.owner, f.name
        """,
        (ref_name, ref_type),
    ).fetchall()


def _root_commit_shas(conn) -> set[str]:
    return {r["sha"] for r in conn.execute("SELECT sha FROM root_commits").fetchall()}


def _diff_against_root(
    local: Path, ref_name: str, ref_type: str,
    root_shas: set[str], fork_row,
) -> ForkSolution | None:
    if not local.exists():
        raise FileNotFoundError(local)
    repo = pygit2.Repository(str(local))
    full_ref = (f"refs/heads/{ref_name}" if ref_type == "branch"
                else f"refs/tags/{ref_name}")
    if full_ref not in repo.references:
        return None
    tip = repo.references[full_ref].peel(pygit2.Commit)

    base = _latest_root_ancestor(repo, tip, root_shas)
    if base is None:
        # No shared ancestor with root (very unusual). Skip — there's
        # nothing meaningful to diff against.
        return None

    diff = repo.diff(base, tip)
    files: set[str] = set()
    for patch in diff:
        delta = patch.delta
        # New path for additions/modifications, old path for deletions
        files.add(delta.new_file.path or delta.old_file.path)
    stats = diff.stats
    return ForkSolution(
        fork_id=fork_row["id"],
        owner=fork_row["owner"],
        name=fork_row["name"],
        insertions=int(stats.insertions),
        deletions=int(stats.deletions),
        files_changed=tuple(sorted(files)),
    )


def _latest_root_ancestor(repo, tip: "pygit2.Commit",
                          root_shas: set[str]) -> "pygit2.Commit | None":
    """The first commit reachable from `tip` (walking parents) whose SHA is
    in `root_shas`. This is the most recent commit shared with the root,
    which acts as the diff base for the exercise.
    """
    for commit in repo.walk(tip.id, pygit2.GIT_SORT_TOPOLOGICAL):
        if str(commit.id) in root_shas:
            return commit
    return None


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0
