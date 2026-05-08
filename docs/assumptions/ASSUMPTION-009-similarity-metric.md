---
ID: ASSUMPTION-009
Category: Features
Status: open
Created: 2026-05-08
Context: FR-06 Solution Comparison — approach similarity
Level: 1
---

# Structural similarity is measured by Jaccard overlap of changed file sets

## Assumption

"Same approach vs. different approach" (FR-06) is determined by comparing the *set of files changed* in each fork's exercise branch relative to the root, using Jaccard similarity:

```
similarity(A, B) = |changed_files(A) ∩ changed_files(B)| / |changed_files(A) ∪ changed_files(B)|
```

A score of 1.0 means both forks modified exactly the same files; 0.0 means no overlap at all.

## Rationale

The requirement asks whether solutions are "basically running the same approach or completely different" — which is inherently ambiguous at the semantic level. File-set overlap is a practical proxy: authors solving a problem the same way typically touch the same files. It requires no language parsing, works across all languages, and is fast to compute from already-stored data.

## What this does NOT capture
- Two forks that modified the same files but with completely different logic
- Different file naming conventions for functionally equivalent files
- Semantic equivalence (e.g., recursive vs iterative solution in the same file)

## Alternatives Considered

- **Line-level diff similarity**: More precise but expensive and fragile across reformatting.
- **AST-based comparison**: Language-specific, high complexity, out of scope.
- **LLM-based comparison**: Interesting v2 option but out of scope for v1.

## Notes

- The similarity score should be displayed as a percentage in the UI.
- A threshold for "similar" vs "different" (e.g., ≥ 60% = similar) may need tuning; it should be configurable or at least easy to change.
