---
ID: ASSUMPTION-003
Category: Features
Status: open
Created: 2026-05-07
Context: Initial requirements definition — leaderboard author grouping
Level: 1
---

# Authors identified by email address, no identity merging

## Assumption

Each unique `author_email` from Git commits is treated as a distinct person. There is no mechanism to merge multiple emails belonging to the same developer (e.g. `alice@work.com` and `alice@personal.com`).

## Rationale

Identity merging (`.mailmap`-style) adds significant complexity. For an initial leaderboard within a controlled team, most developers use a single consistent email. The requirement document does not mention this concern.

## Risk

A developer who commits with different email addresses (e.g., on different machines or before/after a GitHub account rename) will appear as multiple entries on the leaderboard, splitting their score.

## Notes

- Git's `.mailmap` file could be read from each tracked repository as a v2 enhancement.
- Alternatively, an admin UI for manual email → display name mapping could be added.
