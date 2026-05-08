"""Acceptance tests for FR-05 Leaderboard."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pygit2
import pytest

from app import analysis, exercises, jobs, leaderboard
from app.repos import add_fork_manual, set_root_repo

from tests.git_helpers import init_repo, make_local_sync, write_and_commit


def _seed_with_two_authors(db, tmp_path: Path):
    """Build a root + two forks. Alice does 3 days of work on `exercise-01`,
    Bob does 1 commit on `exercise-02`. Both inherit the root's `init`
    commit on main.

    Time stamps are set explicitly so window filtering is deterministic.
    """
    root_path = tmp_path / "root"
    init_repo(root_path)
    rr = pygit2.Repository(str(root_path))
    write_and_commit(rr, {"README.md": "r\n"}, message="init",
                     author_name="Instructor",
                     author_email="ins@example.com",
                     when=datetime(2026, 1, 1, tzinfo=timezone.utc))

    alice_path = tmp_path / "alice"
    init_repo(alice_path)
    ar = pygit2.Repository(str(alice_path))
    write_and_commit(ar, {"README.md": "r\n"}, message="init",
                     author_name="Instructor",
                     author_email="ins@example.com",
                     when=datetime(2026, 1, 1, tzinfo=timezone.utc))
    base = ar.references["refs/heads/main"].target
    ar.create_branch("exercise-01", ar.get(base))
    for i in range(3):
        write_and_commit(
            ar, {f"src/step_{i}.py": f"x = {i}\ny = {i}\n"},
            message=f"alice step {i}",
            author_name="Alice", author_email="alice@example.com",
            when=datetime(2026, 5, 1 + i, tzinfo=timezone.utc),
            branch="exercise-01",
        )

    bob_path = tmp_path / "bob"
    init_repo(bob_path)
    br = pygit2.Repository(str(bob_path))
    write_and_commit(br, {"README.md": "r\n"}, message="init",
                     author_name="Instructor",
                     author_email="ins@example.com",
                     when=datetime(2026, 1, 1, tzinfo=timezone.utc))
    base = br.references["refs/heads/main"].target
    br.create_branch("exercise-02", br.get(base))
    write_and_commit(
        br, {"answer.py": "print('bob')\n"}, message="bob solo",
        author_name="Bob", author_email="bob@example.com",
        when=datetime(2026, 5, 5, tzinfo=timezone.utc),
        branch="exercise-02",
    )

    set_root_repo(db, "https://github.com/acme/root")
    alice = add_fork_manual(db, "https://github.com/alice/root")
    jobs.mark_done(db, jobs.claim_next(db).id)
    bob = add_fork_manual(db, "https://github.com/bob/root")
    jobs.mark_done(db, jobs.claim_next(db).id)

    base_dir = tmp_path / "data" / "repos"
    sync_map = {"acme": root_path, "alice": alice_path, "bob": bob_path}

    def router(_url: str, dest: Path) -> None:
        for owner, src in sync_map.items():
            if owner in dest.name:
                return make_local_sync(src)("x", dest)
        raise AssertionError(f"unrouted dest {dest}")

    analysis.analyze_fork(db, alice.id, base_dir, sync=router)
    analysis.analyze_fork(db, bob.id, base_dir, sync=router)
    exercises.refresh_root(db, base_dir, sync=router)
    return alice.id, bob.id


def test_columns_match_acceptance_criteria(db, tmp_path: Path):
    """Every required column is populated per author."""
    _seed_with_two_authors(db, tmp_path)
    rows = leaderboard.compute_leaderboard(db)

    by_email = {r.author_email: r for r in rows}
    assert "alice@example.com" in by_email
    assert "bob@example.com" in by_email

    alice = by_email["alice@example.com"]
    assert alice.commits == 3
    assert alice.insertions >= 6   # 3 commits × 2 lines each
    assert alice.lines_changed == alice.insertions + alice.deletions
    assert alice.active_days == 3   # three distinct dates
    # tests/docs/config aren't touched here; tests_added stays at 0
    assert alice.tests_added == 0
    # exercise-01 is reached, instructor-init shouldn't count
    assert alice.exercise_breadth == 1
    # alice was first to push to exercise-01
    assert alice.first_submissions == 1

    # Refactor ratio = deletions / lines_changed
    assert 0.0 <= alice.refactor_ratio <= 1.0


def test_score_formula_is_applied(db, tmp_path: Path):
    """ASSUMPTION-002: score = commits×10 + insertions×0.01 + active_days×50."""
    _seed_with_two_authors(db, tmp_path)
    rows = leaderboard.compute_leaderboard(db)
    alice = next(r for r in rows if r.author_email == "alice@example.com")

    expected = round(alice.commits * 10 + alice.insertions * 0.01
                     + alice.active_days * 50)
    assert alice.score == expected


def test_ranking_orders_by_score_then_commits(db, tmp_path: Path):
    _seed_with_two_authors(db, tmp_path)
    rows = leaderboard.compute_leaderboard(db)
    # Alice has more commits + more active days, should rank above Bob
    by_rank = sorted(rows, key=lambda r: r.rank)
    assert by_rank[0].author_email == "alice@example.com"
    assert by_rank[0].rank == 1


def test_tie_break_is_commit_count(db, tmp_path: Path):
    """Authors with equal scores are ordered by commit count."""
    # Build two authors with engineered identical scores but different
    # commit counts. Easiest: use the same set of tests but override commit
    # counts via direct DB inserts.
    set_root_repo(db, "https://github.com/acme/root")
    fork1 = add_fork_manual(db, "https://github.com/x/root")
    jobs.mark_done(db, jobs.claim_next(db).id)
    fork2 = add_fork_manual(db, "https://github.com/y/root")
    jobs.mark_done(db, jobs.claim_next(db).id)

    # author X: 5 commits, 0 insertions, 0 active days (all same date)
    # author Y: 1 commit, 4000 insertions, 0 active days (same date)
    # X: 5*10=50; Y: 1*10 + 4000*0.01 = 50. Tie. X has more commits → ranks higher.
    base_t = "2026-05-01T12:00:00Z"
    db.execute(
        """INSERT INTO commits (fork_id, sha, author_name, author_email,
                                author_time, is_merge, parent_count,
                                files_changed, insertions, deletions)
           VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0, 0)""",
        (fork1.id, "x" * 40, "X", "x@example.com", base_t),
    )
    for i in range(4):
        db.execute(
            """INSERT INTO commits (fork_id, sha, author_name, author_email,
                                    author_time, is_merge, parent_count,
                                    files_changed, insertions, deletions)
               VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0, 0)""",
            (fork1.id, ("x" + str(i)) * 20, "X", "x@example.com", base_t),
        )
    db.execute(
        """INSERT INTO commits (fork_id, sha, author_name, author_email,
                                author_time, is_merge, parent_count,
                                files_changed, insertions, deletions)
           VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0, 0)""",
        (fork2.id, "y" * 40, "Y", "y@example.com", base_t),
    )
    # adjust insertions so Y's score equals X's
    db.execute("UPDATE commits SET insertions = 4000 WHERE author_email = 'y@example.com'")

    rows = leaderboard.compute_leaderboard(db)
    by_email = {r.author_email: r for r in rows}
    assert by_email["x@example.com"].score == by_email["y@example.com"].score
    assert by_email["x@example.com"].rank < by_email["y@example.com"].rank


def test_window_filter_restricts_to_last_n_days(db, tmp_path: Path):
    """The 7d window only includes commits in the last 7 days."""
    set_root_repo(db, "https://github.com/acme/root")
    fork = add_fork_manual(db, "https://github.com/alice/root")
    jobs.mark_done(db, jobs.claim_next(db).id)

    # Recent commit (today) and an old commit (one year ago)
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=400)).isoformat().replace("+00:00", "Z")
    new = now.isoformat().replace("+00:00", "Z")
    for sha, when in (("a" * 40, old), ("b" * 40, new)):
        db.execute(
            """INSERT INTO commits (fork_id, sha, author_name, author_email,
                                    author_time, is_merge, parent_count,
                                    files_changed, insertions, deletions)
               VALUES (?, ?, 'Alice', 'alice@example.com', ?, 0, 0, 0, 1, 0)""",
            (fork.id, sha, when),
        )

    all_rows = leaderboard.compute_leaderboard(db, window="all")
    seven = leaderboard.compute_leaderboard(db, window="7d")

    [a] = [r for r in all_rows if r.author_email == "alice@example.com"]
    [b] = [r for r in seven if r.author_email == "alice@example.com"]
    assert a.commits == 2
    assert b.commits == 1


def test_per_exercise_scope_only_counts_that_branch(db, tmp_path: Path):
    """Scoping to exercise-01 must not include exercise-02's commits."""
    _seed_with_two_authors(db, tmp_path)

    rows = leaderboard.compute_leaderboard(
        db, exercise=("exercise-01", "branch")
    )
    by_email = {r.author_email: r for r in rows}
    assert "alice@example.com" in by_email
    assert "bob@example.com" not in by_email
    # The instructor's init commit is in root_commits, so it gets excluded.
    assert "ins@example.com" not in by_email


def test_merge_commits_are_excluded(db, tmp_path: Path):
    """FR-02 AC4: merge commits don't count toward score."""
    set_root_repo(db, "https://github.com/acme/root")
    fork = add_fork_manual(db, "https://github.com/alice/root")
    jobs.mark_done(db, jobs.claim_next(db).id)

    db.execute(
        """INSERT INTO commits (fork_id, sha, author_name, author_email,
                                author_time, is_merge, parent_count,
                                files_changed, insertions, deletions)
           VALUES (?, ?, 'Alice', 'alice@example.com',
                   '2026-05-01T12:00:00Z', 1, 2, 1, 100, 0)""",
        (fork.id, "m" * 40),
    )

    rows = leaderboard.compute_leaderboard(db)
    assert rows == []


def test_category_breakdown_provided_for_hover(db, tmp_path: Path):
    """FR-05 hover: category breakdown over Lines Changed."""
    _seed_with_two_authors(db, tmp_path)
    rows = leaderboard.compute_leaderboard(db)
    alice = next(r for r in rows if r.author_email == "alice@example.com")

    # alice's commits all touch src/*.py → code category
    assert alice.category_lines["code"] >= 6
    assert sum(alice.category_lines.values()) <= alice.lines_changed


def test_default_window_is_all_time(db):
    """AC: default leaderboard covers all commits across all forks."""
    # Empty DB still returns an empty list, not an error.
    assert leaderboard.compute_leaderboard(db) == []


def test_unknown_window_raises(db):
    with pytest.raises(ValueError):
        leaderboard.compute_leaderboard(db, window="2d")  # type: ignore[arg-type]


def test_commit_seen_in_two_forks_only_counted_once(db, tmp_path: Path):
    """Cross-fork copies of the same commit (e.g. instructor's init)
    must not inflate the author's stats."""
    _seed_with_two_authors(db, tmp_path)
    rows = leaderboard.compute_leaderboard(db)
    # instructor's init commit appears in root, alice, bob — but is in
    # root_commits when filtering by exercise. For all-time leaderboard
    # without exercise scope, the instructor will be present once with
    # commits=1, not 3.
    instructor = next((r for r in rows
                       if r.author_email == "ins@example.com"), None)
    assert instructor is not None
    assert instructor.commits == 1
