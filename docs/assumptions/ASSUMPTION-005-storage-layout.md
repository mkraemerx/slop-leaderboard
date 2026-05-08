---
ID: ASSUMPTION-005
Category: Operations
Status: open
Created: 2026-05-07
Context: Initial requirements definition — data persistence
Level: 1
---

# All persistent data lives under ./data/; SQLite is the only storage engine

## Assumption

- `./data/db.sqlite3` — application database (repos, commits, scheduler jobs)
- `./data/repos/<repo-slug>/` — cloned Git repositories
- No external database, message broker, or cache is required.

## Rationale

The spec explicitly calls for SQLite and a single-container deployment. Keeping everything under one bind-mountable directory makes backup, restore, and migration trivial.

## Risk

- **Concurrent writes**: SQLite's write serialisation is fine for the expected load (one background worker writing commits, one web process reading). WAL mode will be enabled to allow concurrent reads alongside the writer.
- **Disk growth**: Cloned repos can be large. The `data/repos/` directory is not automatically pruned when a repo is deleted — the deletion handler must remove it explicitly.
- **No replication**: A single SQLite file is a single point of failure. Acceptable for v1 (team dashboard, not critical infrastructure).

## Notes

- The `./data/` directory should be excluded from the Docker image and bind-mounted at runtime.
- A `VACUUM` or `ANALYZE` job should be considered for long-running deployments.
