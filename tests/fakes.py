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
