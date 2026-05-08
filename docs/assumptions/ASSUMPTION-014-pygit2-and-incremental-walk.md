---
ID: ASSUMPTION-014
Category: Architectural
Status: open
Created: 2026-05-08
Context: FR-02 implementation — git library + incremental walk
Level: 1
---

# pygit2 (libgit2) is the only git interface; incremental walks compare against stored SHAs

## Assumption

Git operations (clone, fetch, log/diff) go through `pygit2`. No `subprocess`/`git` shell-out is performed anywhere in the application. Incremental analysis is implemented by:

1. Reading the set of `commits.sha` already stored for a fork.
2. Walking *every* local ref (branches and tags) via `Repository.references` and yielding each reachable commit whose SHA is not already stored.
3. Inserting the new commits with `INSERT OR IGNORE (fork_id, sha)`.

Walking from every ref — not just `HEAD` — is required for FR-04 because exercises may live on tags or non-default branches.

## Rationale

- **QR-05** mandates "library APIs only" for git work; pygit2 is a libgit2 binding and avoids the shell entirely.
- The `INSERT OR IGNORE` + UNIQUE `(fork_id, sha)` index gives idempotency for free, satisfying QR-02 ("restarting mid-analysis does not produce duplicate records") at near-zero cost.
- The set-difference walk is O(commits) per fork; for the QR-01 budget (≤ ~100k commits in total across all forks) it completes in well under a second on a developer laptop.

## Risks / known limitations

- `pygit2` requires a libgit2 native dep. We accept that in exchange for not shell-ing out. The Docker image must `apt-get install libgit2-dev` (or use `pygit2`'s prebuilt wheels — preferred).
- We do a full (non-bare) clone. Disk-cheap for small fork counts; for v2 we should switch to `--bare` plus a treeish checkout when diffs are needed.
- The walker uses `GIT_SORT_TIME`. If a contributor backdates a commit drastically the walk order may be surprising, but the *set* of stored commits is unaffected.
