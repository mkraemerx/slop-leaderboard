"""Tests for FR-03 file classification (per ASSUMPTION-012)."""
from __future__ import annotations

import pytest

from app.classifier import CATEGORIES, classify


@pytest.mark.parametrize("path,expected", [
    # tests rules win over extension
    ("tests/conftest.py", "tests"),
    ("test/foo.go", "tests"),
    ("__tests__/widget.tsx", "tests"),
    ("spec/models/user_spec.rb", "tests"),
    ("test_utils.py", "tests"),
    ("server_test.go", "tests"),
    ("widget.test.ts", "tests"),
    ("widget.spec.ts", "tests"),
    # docs
    ("README.md", "docs"),
    ("docs/install.rst", "docs"),
    ("CHANGELOG.adoc", "docs"),
    ("notes.txt", "docs"),
    # config
    (".github/workflows/ci.yml", "config"),
    ("Dockerfile", "config"),
    ("Dockerfile.prod", "config"),
    ("pyproject.toml", "config"),
    (".env.example", "config"),
    ("ci/build.sh", "config"),
    ("deploy/k8s.yaml", "config"),
    # code
    ("src/main.py", "code"),
    ("server/index.js", "code"),
    ("Main.java", "code"),
    ("foo.rs", "code"),
    # other
    ("data/seed.csv", "other"),
    ("logo.png", "other"),
    ("package-lock.json", "other"),  # not in our config exts; lands in other
])
def test_classify(path, expected):
    assert classify(path) == expected


def test_test_path_wins_over_extension():
    """A `.py` file under `tests/` is tests, not code."""
    assert classify("tests/utils.py") == "tests"


def test_classify_handles_windows_separators():
    assert classify(r"docs\\install.md") == "docs"


def test_categories_constant_is_complete():
    # Every classifier output is in CATEGORIES.
    paths = ["a.py", "tests/x.py", "README.md", "ci/x.sh", "data/x.csv"]
    for p in paths:
        assert classify(p) in CATEGORIES


def test_classify_empty_returns_other():
    assert classify("") == "other"
