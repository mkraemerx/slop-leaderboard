---
ID: ASSUMPTION-002
Category: Features
Status: open
Created: 2026-05-07
Context: Initial requirements definition — leaderboard scoring
Level: 1
---

# Score formula chosen without stakeholder input

## Assumption

The leaderboard score is calculated as:

```
score = commits × 10 + insertions × 0.01 + active_days × 50
```

Deletions are intentionally excluded from the score (they do not inflate rank). Merge commits are excluded from all calculations.

## Rationale

No scoring formula was specified by the user. The formula was chosen to:
- Reward consistency (`active_days × 50` dominates for sustained contributors)
- Reward volume of meaningful work (`commits × 10`)
- Give minor credit for large diffs (`insertions × 0.01`) without making LOC count gameable
- Not penalise cleanup/refactoring work (deletions not negative)

## Alternatives Considered

1. **LOC-only**: `insertions - deletions` — easily gamed, penalises cleanup.
2. **Commit-count only**: Incentivises tiny commits.
3. **Weighted with deletions**: `commits×10 + (insertions+deletions)×0.005` — acknowledges refactoring but conflates churn with effort.

## Notes

- The formula weights and the merge-commit exclusion rule should be reviewed with the actual users before v1 launch.
- The formula is hardcoded; a v2 enhancement could expose it as configuration.
