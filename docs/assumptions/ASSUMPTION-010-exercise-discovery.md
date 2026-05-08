---
ID: ASSUMPTION-010
Category: Features
Status: open
Created: 2026-05-08
Context: FR-04 Exercises — auto-discovery
Level: 1
---

# Exercises are any branch or tag present in a fork but absent from the root

## Assumption

The system treats every branch and tag name that occurs in at least one fork but does not exist in the root repository as an exercise. No instructor configuration is required; discovery is a side-effect of the regular fork sync.

## Rationale

The user asked whether exercises could be discovered this way rather than defined manually. In the training context the root repo contains the starting material (e.g., only `main`), and authors create their own branches for each exercise. The set-difference between fork branches and root branches is therefore a reliable proxy for "things authors did on their own."

## Edge cases to be aware of

- **Personal housekeeping branches**: An author might create a branch like `fix-typo` that is not an exercise. These would appear in the exercise list. In practice this is unlikely to be noisy enough to matter, but a future "hide exercise" feature may be warranted.
- **Root repo adds a branch later**: If the instructor later pushes a branch to the root (e.g., a reference solution), that branch will disappear from the exercise list on the next sync. This is correct behaviour.
- **Tag vs branch**: Both are included. Tags typically mark specific submission points; branches represent ongoing work. The UI should distinguish them visually.

## Notes

- The comparison of fork branches against root branches is done using the local clone; no extra API calls are needed once clones are up to date.
