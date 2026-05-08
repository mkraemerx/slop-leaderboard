"""Filesystem layout for cloned repos. Keeps QR-05 path-traversal rule in
one place: every path returned by `repo_path()` is verified to live under the
configured base directory.
"""
from __future__ import annotations

import re
from pathlib import Path


_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


def slugify(owner: str, name: str) -> str:
    """Build a filesystem-safe directory name for a repo.

    Both segments are stripped of anything outside `[A-Za-z0-9._-]`. Two
    different repos never collide because the separator `__` is also removed
    from each segment.
    """
    safe_owner = _SLUG_RE.sub("-", owner).strip("-.")
    safe_name = _SLUG_RE.sub("-", name).strip("-.")
    if not safe_owner or not safe_name:
        raise ValueError(f"cannot slugify owner={owner!r} name={name!r}")
    return f"{safe_owner}__{safe_name}"


def repo_path(base: Path, owner: str, name: str) -> Path:
    """Return the local path for a repo, guaranteed to live under `base`."""
    base = base.resolve()
    candidate = (base / slugify(owner, name)).resolve()
    # `is_relative_to` available on Python 3.9+; we target 3.13.
    if not candidate.is_relative_to(base):
        raise ValueError(f"path escapes base: {candidate} not under {base}")
    return candidate
