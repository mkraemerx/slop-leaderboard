"""Lightweight in-memory fakes for the GitHub HTTP client."""
from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class FakeResponse:
    status_code: int
    _payload: object
    text: str = ""

    def json(self):
        return self._payload


class FakeHttp:
    """Minimal stand-in for httpx.Client.

    `pages` is a list-of-lists; each inner list is one /forks response.
    """

    def __init__(self, pages: list[list[dict]] | None = None,
                 status_code: int = 200, error: Exception | None = None) -> None:
        self.pages = pages or []
        self.status_code = status_code
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, *, headers=None, params=None):
        if self.error is not None:
            raise self.error
        self.calls.append((url, dict(params or {})))
        if self.status_code != 200:
            return FakeResponse(self.status_code, [], text="boom")
        page_idx = (params or {}).get("page", 1) - 1
        if page_idx < 0 or page_idx >= len(self.pages):
            return FakeResponse(200, [])
        return FakeResponse(200, self.pages[page_idx])


class FakeGitHub:
    """Duck-typed stand-in for `GitHubClient`, used by FR-01 discovery tests.

    `discover_forks` only needs `list_org_repos` and `repo_contains_commit`, so
    we model exactly those two. `contains` is the set of "owner/name" repos that
    DO contain the template root commit; `None` means every repo shares it.
    """

    def __init__(self, org_repos=None, contains=None) -> None:
        self.org_repos = list(org_repos or [])
        self._contains = contains
        self.contains_calls: list[tuple[str, str, str]] = []

    def list_org_repos(self, org: str):
        return list(self.org_repos)

    def repo_contains_commit(self, owner: str, name: str, sha: str) -> bool:
        self.contains_calls.append((owner, name, sha))
        if self._contains is None:
            return True
        return f"{owner}/{name}" in self._contains
