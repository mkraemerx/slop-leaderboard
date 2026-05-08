"""File-path classifier for FR-03 metrics (per ASSUMPTION-012).

The categories are an exhaustive partition: every path lands in exactly one
of `code`, `tests`, `docs`, `config`, or `other`. Lines in `other` files are
counted in commit totals but not shown in the category breakdown.

Priority order (first match wins):
    1. tests
    2. docs
    3. config
    4. code
    5. other
"""
from __future__ import annotations

import os
import re
from typing import Final


Category = str  # "code" | "tests" | "docs" | "config" | "other"


_TEST_FILENAME_RE: Final = re.compile(
    r"(?:^|[/\\])("
    r"test_[^/\\]+|"               # test_*.py
    r"[^/\\]+_test\.[^/\\]+|"      # *_test.go
    r"[^/\\]+_spec\.[^/\\]+|"      # *_spec.rb
    r"[^/\\]+\.test\.[^/\\]+|"     # *.test.ts
    r"[^/\\]+\.spec\.[^/\\]+"      # *.spec.ts
    r")$"
)
_TEST_PATH_SEGMENTS: Final = frozenset({"test", "tests", "__tests__", "spec"})

_DOC_EXTS: Final = frozenset({".md", ".rst", ".adoc", ".txt"})
_DOC_PATH_PREFIXES: Final = ("docs/", "doc/")

_CONFIG_EXTS: Final = frozenset({".yml", ".yaml", ".toml", ".ini", ".cfg"})
_CONFIG_FILENAME_PREFIXES: Final = ("Dockerfile", ".env")
_CONFIG_PATH_FRAGMENTS: Final = (".github/", "ci/", "deploy/", "infra/")

_CODE_EXTS: Final = frozenset({
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".java", ".go", ".rb", ".rs",
    ".c", ".h", ".cpp", ".hpp", ".cc", ".hh",
    ".cs", ".php", ".swift", ".kt", ".kts",
    ".scala", ".m", ".mm", ".sh", ".bash",
    ".pl", ".lua", ".dart", ".ex", ".exs",
    ".erl", ".clj", ".cljs", ".hs", ".elm",
})


def classify(path: str) -> Category:
    """Classify a single file path. Forward and back slashes both work."""
    if not path:
        return "other"
    norm = path.replace("\\", "/")
    while norm.startswith("./"):
        norm = norm[2:]
    parts = [p for p in norm.split("/") if p]
    filename = parts[-1] if parts else ""
    _, ext = os.path.splitext(filename)
    ext = ext.lower()

    # 1. Tests (path segment OR filename pattern)
    if any(seg in _TEST_PATH_SEGMENTS for seg in parts[:-1]):
        return "tests"
    if _TEST_FILENAME_RE.search(filename):
        return "tests"

    # 2. Docs
    if ext in _DOC_EXTS:
        return "docs"
    for prefix in _DOC_PATH_PREFIXES:
        if norm.startswith(prefix):
            return "docs"

    # 3. Config / infra
    if ext in _CONFIG_EXTS:
        return "config"
    for cf in _CONFIG_FILENAME_PREFIXES:
        if filename.startswith(cf):
            return "config"
    for frag in _CONFIG_PATH_FRAGMENTS:
        if frag in norm:
            return "config"

    # 4. Code
    if ext in _CODE_EXTS:
        return "code"

    return "other"


CATEGORIES: Final[tuple[str, ...]] = ("code", "tests", "docs", "config", "other")
