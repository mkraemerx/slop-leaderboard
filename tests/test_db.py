from __future__ import annotations

from app.db import SCHEMA_VERSION, init_schema


def test_init_schema_is_idempotent(db):
    init_schema(db)
    init_schema(db)
    [(version,)] = db.execute("SELECT version FROM schema_version").fetchall()
    assert version == SCHEMA_VERSION


def test_foreign_keys_are_enabled(db):
    [(fk,)] = db.execute("PRAGMA foreign_keys").fetchall()
    assert fk == 1


def test_journal_mode_is_wal(db):
    [(mode,)] = db.execute("PRAGMA journal_mode").fetchall()
    assert mode.lower() == "wal"
