---
ID: ASSUMPTION-015
Category: Architectural
Status: open
Created: 2026-05-08
Context: FR-03 implementation — clone layout & metrics columns
Level: 1
---

# Bare mirror clones; per-category metrics stored as columns; commit_refs rebuilt each sync

## Assumptions

1. **Bare clones with mirror refspecs.** `app.git_ops.clone_or_fetch` creates a bare clone and explicitly fetches with `+refs/heads/*:refs/heads/*` and `+refs/tags/*:refs/tags/*`, so every remote branch and tag becomes a local ref. We never need a working tree (analysis is pure object-store reads), and `list_refs` can simply iterate `refs/heads/*` + `refs/tags/*` without juggling remote-tracking refs.
2. **Per-category metrics live in columns on `commits`** (`code_insertions`, `code_deletions`, `tests_insertions`, ..., `config_*`). We considered a separate `commit_categories` table; the column-based approach is simpler to query for the leaderboard (FR-05 needs sums by category) and keeps each commit's row self-contained for caching.
3. **`commit_refs` is rebuilt on every analysis run.** Branches and tags can be deleted upstream (FR-04 explicitly relies on this — an exercise disappears when no fork retains its branch). Re-walking from each ref tip is O(reachable commits × refs); for the QR-01 budget this is fast enough that diff-based maintenance would be premature.
4. **`other` files are absorbed into commit totals only.** Lines that classify as `other` (binaries, lock files, unrecognised extensions) increment `insertions`/`deletions` but no category-specific column. The leaderboard "Lines Changed" hover (FR-05) therefore shows category percentages over the *known* categories — they may sum to less than 100%, which is the intended behaviour.

## Notes

- If commit volume grows past the QR-01 budget, consider switching `commit_refs` rebuild to a delta strategy or denormalising "first ref the commit appeared on" onto `commits` directly.
- The mirror refspec is per-fetch (passed via `Remote.fetch(refspecs=...)`) rather than written to git config; that makes the choice explicit at every call site and avoids any drift caused by a default refspec being replaced.
