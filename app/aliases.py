"""Author identity normalisation (per ASSUMPTION-003 follow-through).

Git author identity is wild: the same person commits as different emails
(work vs personal, noreply formats), and automation accounts (CI bots,
Claude bot) show up alongside real participants. This module manages two
small tables:

- `author_aliases`: maps an *alias* email to a *canonical* email (and an
  optional display name override). Used by the leaderboard's GROUP BY and
  the exercise first-author logic.
- `ignored_authors`: addresses to drop from every aggregation entirely.

Both tables are case-insensitive (COLLATE NOCASE on the email column).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .db import transaction


@dataclass(frozen=True)
class Alias:
    alias_email: str
    canonical_email: str
    display_name: str | None


def add_alias(conn: sqlite3.Connection, alias_email: str,
              canonical_email: str, display_name: str | None = None,
              ) -> None:
    """Idempotent: re-inserting the same alias updates the target."""
    if not alias_email or not canonical_email:
        raise ValueError("both emails must be non-empty")
    if alias_email.strip().lower() == canonical_email.strip().lower():
        raise ValueError("alias and canonical cannot be the same address")
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO author_aliases (alias_email, canonical_email, display_name)
            VALUES (?, ?, ?)
            ON CONFLICT(alias_email) DO UPDATE SET
                canonical_email = excluded.canonical_email,
                display_name = excluded.display_name
            """,
            (alias_email.strip(), canonical_email.strip(),
             display_name.strip() if display_name else None),
        )


def remove_alias(conn: sqlite3.Connection, alias_email: str) -> None:
    with transaction(conn):
        conn.execute(
            "DELETE FROM author_aliases WHERE alias_email = ?",
            (alias_email,),
        )


def list_aliases(conn: sqlite3.Connection) -> list[Alias]:
    rows = conn.execute(
        """
        SELECT alias_email, canonical_email, display_name
        FROM author_aliases
        ORDER BY canonical_email COLLATE NOCASE,
                 alias_email    COLLATE NOCASE
        """
    ).fetchall()
    return [
        Alias(alias_email=r["alias_email"],
              canonical_email=r["canonical_email"],
              display_name=r["display_name"])
        for r in rows
    ]


def ignore_author(conn: sqlite3.Connection, email: str) -> None:
    if not email or not email.strip():
        raise ValueError("email must be non-empty")
    with transaction(conn):
        conn.execute(
            "INSERT OR IGNORE INTO ignored_authors (email) VALUES (?)",
            (email.strip(),),
        )


def unignore_author(conn: sqlite3.Connection, email: str) -> None:
    with transaction(conn):
        conn.execute("DELETE FROM ignored_authors WHERE email = ?", (email,))


def list_ignored(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT email FROM ignored_authors ORDER BY email COLLATE NOCASE"
    ).fetchall()
    return [r["email"] for r in rows]


@dataclass(frozen=True)
class EmailStat:
    email: str
    name_sample: str
    commits: int


def distinct_commit_emails(conn: sqlite3.Connection) -> list[EmailStat]:
    """Every author_email seen in the commits table, with a representative
    author_name and the total commit count (post-dedup by SHA).

    The /admin/aliases page uses this so the operator can see which raw
    addresses are most worth consolidating.
    """
    rows = conn.execute(
        """
        WITH unique_commits AS (
            SELECT MIN(id) AS id, sha, author_email, MAX(author_name) AS name
            FROM commits
            WHERE is_merge = 0
            GROUP BY sha
        )
        SELECT author_email,
               MAX(name) AS name_sample,
               COUNT(*) AS commits
        FROM unique_commits
        GROUP BY LOWER(author_email)
        ORDER BY commits DESC, author_email COLLATE NOCASE
        """
    ).fetchall()
    return [
        EmailStat(email=r["author_email"],
                  name_sample=r["name_sample"] or r["author_email"],
                  commits=int(r["commits"]))
        for r in rows
    ]
