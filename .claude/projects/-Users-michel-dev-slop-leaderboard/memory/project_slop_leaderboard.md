---
name: Slop Leaderboard project context
description: Core facts about the Git Repo Leaderboard Dashboard project — stack, key decisions, requirements location
type: project
---

# Git Repo Leaderboard Dashboard

**Goal**: Web dashboard showing leaderboard-style Git contribution metrics across multiple repos.

**Stack**: Python 3.13, FastAPI, PyDriller, SQLite, APScheduler, Jinja2, HTMX, Alpine.js, Tailwind+daisyUI (CDN), Chart.js (CDN, v2).

**Requirements**: `docs/requirements.md` — FRs, NFRs, and 30+ test criteria (TC-01 through TC-06).

**Assumptions documented**: `docs/assumptions/` — 7 files (ASSUMPTION-001 through 007).

## Key decisions made
- No auth in v1 (ASSUMPTION-001)
- Score = commits×10 + insertions×0.01 + active_days×50 (ASSUMPTION-002)
- Author identity = email address, no .mailmap merging (ASSUMPTION-003)
- Polling every 60 min is primary sync; webhooks are fast-path bonus (ASSUMPTION-004)
- All data under ./data/ — db.sqlite3 + repos/ subdirs (ASSUMPTION-005)
- Charts deferred to v2 (ASSUMPTION-006)
- Sync SQLite in both web handlers and background jobs; no async ORM (ASSUMPTION-007)

## Why:
Requirements and assumptions were written before implementation to ensure alignment with user before coding starts.

## How to apply:
Reference docs/requirements.md for FRs/NFRs and test criteria when implementing or reviewing features.
