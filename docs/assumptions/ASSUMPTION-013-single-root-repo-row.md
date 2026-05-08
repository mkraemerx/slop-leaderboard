---
ID: ASSUMPTION-013
Category: Architectural
Status: open
Created: 2026-05-08
Context: FR-01 implementation — root repo persistence
Level: 1
---

# A single `root_repo` row enforced via `CHECK (id = 1)`

## Assumption

The `root_repo` table is constrained to at most one row by `PRIMARY KEY CHECK (id = 1)`. `set_root_repo()` performs an `UPSERT` on `id = 1`, so reconfiguring the root repo replaces the existing row in place. The `forks` foreign key uses `ON DELETE CASCADE`, so deleting the row removes all dependent forks (FR-01 AC5).

## Rationale

The "Out of Scope" section of `requirements.md` explicitly excludes tracking more than one root repository at a time. Modelling the singleton with a row-level CHECK keeps schema changes minimal if multi-root is added later — at that point the constraint is dropped and a new column is added.

## Notes

- Replacing the root repo currently leaves no audit trail. If that becomes a concern the UPSERT can be changed to DELETE-then-INSERT in a transaction with an `archived_root_repos` table.
- The CASCADE delete also takes commit/exercise data with it once those tables are added (FR-02+).
