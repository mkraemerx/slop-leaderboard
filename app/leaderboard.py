"""FR-05 Leaderboard: per-author aggregations with optional time and
per-exercise filters.

The score formula (`commits × 10 + insertions × 0.01 + active_days × 50`)
matches ASSUMPTION-002. Merge commits are excluded from every aggregation
(FR-02 AC4).

Authors are grouped by `author_email` per ASSUMPTION-003. Commits that
appear in multiple forks (e.g. the instructor's initial commit cloned into
every fork) are deduplicated by SHA so they count once per author.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal


Window = Literal["all", "7d", "30d", "90d"]


@dataclass(frozen=True)
class Row:
    rank: int
    author_name: str
    author_email: str
    commits: int
    insertions: int
    deletions: int
    lines_changed: int
    tests_added: int
    refactor_ratio: float        # 0.0 .. 1.0
    exercise_breadth: int
    first_submissions: int
    active_days: int
    score: int
    category_lines: dict[str, int]   # {"code": ins+del, "tests": ..., ...}


WINDOW_DAYS: dict[str, int | None] = {
    "all": None,
    "7d": 7,
    "30d": 30,
    "90d": 90,
}


def compute_leaderboard(
    conn: sqlite3.Connection,
    *,
    window: Window = "all",
    exercise: tuple[str, str] | None = None,   # (ref_name, ref_type)
) -> list[Row]:
    """Return one row per author, ordered by score descending (ties broken
    by commit count). Excludes merge commits unconditionally.
    """
    if window not in WINDOW_DAYS:
        raise ValueError(f"unknown window: {window!r}")

    # Always exclude:
    # - merge commits (FR-02 AC4)
    # - commits inherited from the configured root repo (so the
    #   leaderboard reflects participant work, not the starter material
    #   the root was forked from).
    where = [
        "c.is_merge = 0",
        "c.sha NOT IN (SELECT sha FROM root_commits)",
    ]
    params: list[object] = []
    days = WINDOW_DAYS[window]
    if days is not None:
        where.append("c.author_time >= datetime('now', ?)")
        params.append(f"-{days} days")

    if exercise is not None:
        ref_name, ref_type = exercise
        # Restrict to commits reachable from the exercise ref in any fork.
        where.append("""
            EXISTS (
                SELECT 1 FROM commit_refs cr
                WHERE cr.fork_id = c.fork_id
                  AND cr.commit_sha = c.sha
                  AND cr.ref_name = ?
                  AND cr.ref_type = ?
            )
        """)
        params.extend([ref_name, ref_type])

    where_sql = " AND ".join(f"({w})" for w in where)

    # Stage 1: dedupe by SHA so cross-fork copies aren't double-counted.
    sql = f"""
    WITH scoped AS (
        SELECT
            c.sha,
            -- pick a stable representative row per SHA
            MIN(c.id) AS id,
            c.author_email,
            c.author_name,
            c.author_time,
            c.insertions, c.deletions,
            c.code_insertions, c.code_deletions,
            c.tests_insertions, c.tests_deletions,
            c.docs_insertions, c.docs_deletions,
            c.config_insertions, c.config_deletions
        FROM commits c
        WHERE {where_sql}
        GROUP BY c.sha
    )
    SELECT
        author_email,
        MAX(author_name) AS author_name,
        COUNT(*) AS commits,
        COALESCE(SUM(insertions), 0) AS insertions,
        COALESCE(SUM(deletions), 0) AS deletions,
        COALESCE(SUM(tests_insertions - tests_deletions), 0) AS tests_added,
        COUNT(DISTINCT date(author_time)) AS active_days,
        COALESCE(SUM(code_insertions + code_deletions), 0) AS code_lines,
        COALESCE(SUM(tests_insertions + tests_deletions), 0) AS tests_lines,
        COALESCE(SUM(docs_insertions + docs_deletions), 0) AS docs_lines,
        COALESCE(SUM(config_insertions + config_deletions), 0) AS config_lines
    FROM scoped
    GROUP BY author_email
    """
    rows = conn.execute(sql, params).fetchall()

    # Auxiliary lookups for breadth & first submissions need ALL non-root
    # exercise refs, not just the scoped ones.
    breadth_by_email = _exercise_breadth(conn)
    first_by_email = _first_submissions(conn)

    out = []
    for r in rows:
        ins = int(r["insertions"]); dele = int(r["deletions"])
        commits = int(r["commits"])
        lines_changed = ins + dele
        active_days = int(r["active_days"])
        refactor = (dele / lines_changed) if lines_changed > 0 else 0.0
        score = round(commits * 10 + ins * 0.01 + active_days * 50)
        out.append(Row(
            rank=0,  # filled below
            author_name=r["author_name"] or r["author_email"],
            author_email=r["author_email"],
            commits=commits,
            insertions=ins,
            deletions=dele,
            lines_changed=lines_changed,
            tests_added=int(r["tests_added"]),
            refactor_ratio=refactor,
            exercise_breadth=breadth_by_email.get(r["author_email"], 0),
            first_submissions=first_by_email.get(r["author_email"], 0),
            active_days=active_days,
            score=score,
            category_lines={
                "code": int(r["code_lines"]),
                "tests": int(r["tests_lines"]),
                "docs": int(r["docs_lines"]),
                "config": int(r["config_lines"]),
            },
        ))

    # Tie-break: score desc, then commits desc, then email asc for stability.
    out.sort(key=lambda r: (-r.score, -r.commits, r.author_email))
    return [Row(**{**row.__dict__, "rank": i + 1}) for i, row in enumerate(out)]


def _exercise_breadth(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT c.author_email,
               COUNT(DISTINCT cr.ref_name || '|' || cr.ref_type) AS breadth
        FROM commits c
        JOIN commit_refs cr
          ON cr.fork_id = c.fork_id AND cr.commit_sha = c.sha
        WHERE c.is_merge = 0
          AND NOT EXISTS (
              SELECT 1 FROM root_refs r
              WHERE r.ref_name = cr.ref_name AND r.ref_type = cr.ref_type
          )
          AND c.sha NOT IN (SELECT sha FROM root_commits)
        GROUP BY c.author_email
        """
    ).fetchall()
    return {r["author_email"]: int(r["breadth"]) for r in rows}


def _first_submissions(conn: sqlite3.Connection) -> dict[str, int]:
    """Count exercises where this email is the first non-root committer.

    We replay the exercises.first_author logic in pure SQL so the leaderboard
    can be computed without going through the Python layer.
    """
    rows = conn.execute(
        """
        WITH first_per_exercise AS (
            SELECT cr.ref_name, cr.ref_type,
                   c.author_email,
                   ROW_NUMBER() OVER (
                       PARTITION BY cr.ref_name, cr.ref_type
                       ORDER BY c.author_time ASC, c.id ASC
                   ) AS rn
            FROM commit_refs cr
            JOIN commits c
              ON c.fork_id = cr.fork_id AND c.sha = cr.commit_sha
            WHERE NOT EXISTS (
                SELECT 1 FROM root_refs r
                WHERE r.ref_name = cr.ref_name AND r.ref_type = cr.ref_type
            )
            AND c.sha NOT IN (SELECT sha FROM root_commits)
            AND c.is_merge = 0
        )
        SELECT author_email, COUNT(*) AS first_count
        FROM first_per_exercise
        WHERE rn = 1
        GROUP BY author_email
        """
    ).fetchall()
    return {r["author_email"]: int(r["first_count"]) for r in rows}
