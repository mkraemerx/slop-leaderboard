---
ID: ASSUMPTION-006
Category: UI
Status: open
Created: 2026-05-07
Context: Initial requirements definition — frontend scope
Level: 1
---

# Chart.js visualisations deferred to v2

## Assumption

The v1 dashboard uses only tabular data (leaderboard table, repo list). Chart.js is listed in the tech stack but no charts are implemented in v1.

## Rationale

The user's spec included Chart.js "for visualisations" without specifying what charts are required. Implementing charts without clear requirements risks building the wrong thing. The table-first approach delivers the core value (who contributed most) immediately.

## Candidate charts for v2
- Commit activity over time per author (line chart)
- Insertions/deletions ratio per author (bar chart)
- Per-repo contribution breakdown (stacked bar or pie)
- Score trend over time

## Notes

- Chart.js will still be included in the base template via CDN so it is available for incremental addition without layout changes.
