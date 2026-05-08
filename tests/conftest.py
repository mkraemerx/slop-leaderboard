from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.db import connect, init_schema


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "test.sqlite3")
    init_schema(conn)
    yield conn
    conn.close()
