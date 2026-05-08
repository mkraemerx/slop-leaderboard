---
ID: ASSUMPTION-007
Category: Architectural
Status: open
Created: 2026-05-07
Context: Initial requirements definition — database access pattern
Level: 1
---

# Synchronous SQLite access in background jobs; async in web handlers

## Assumption

Background analysis jobs (APScheduler threads) use the standard `sqlite3` module or synchronous SQLAlchemy. FastAPI route handlers use `sqlite3` in sync routes (FastAPI runs sync handlers in a thread pool automatically).

No async ORM (SQLAlchemy async / aiosqlite) is used.

## Rationale

The application has low concurrency requirements: one writer (background job) and O(10) concurrent readers (dashboard users). The added complexity of an async DB layer (connection pool management, `asyncio` in tests, etc.) is not justified. FastAPI's thread pool execution of sync routes provides adequate throughput.

## Alternatives Considered

- **aiosqlite + async SQLAlchemy**: More idiomatic for async FastAPI, but significantly more boilerplate and harder to test.
- **Full SQLAlchemy ORM**: Adds abstraction that obscures the SQL queries, conflicting with the "explicit SQL" NFR.

## Notes

- SQLite WAL mode is enabled on startup to allow concurrent reads while a write is in progress.
- If write contention becomes an issue (many repos analysed simultaneously), the background worker concurrency should be limited to 1–2 threads.
