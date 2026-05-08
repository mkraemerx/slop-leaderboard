"""GitHub REST API client used for fork discovery (FR-01).

Only the small subset needed by the dashboard is implemented; we deliberately
avoid pulling in PyGithub to keep the dependency surface small.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Protocol
from urllib.parse import urlparse


GITHUB_REPO_URL_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[^/\s]+)/(?P<name>[^/\s]+?)(?:\.git)?/?$"
)


class GitHubError(RuntimeError):
    """Wraps any failure from the GitHub REST API."""


@dataclass(frozen=True)
class RepoRef:
    owner: str
    name: str
    url: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


def parse_github_url(url: str) -> RepoRef:
    """Parse a GitHub repo URL into owner/name. Raises ValueError on bad input.

    Only `github.com` HTTPS URLs are accepted; we explicitly reject other hosts
    so a malformed configuration does not silently target the wrong service.
    """
    if not isinstance(url, str) or not url:
        raise ValueError("url must be a non-empty string")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"unsupported scheme: {parsed.scheme!r}")
    if parsed.netloc.lower() != "github.com":
        raise ValueError(f"not a github.com URL: {url!r}")
    m = GITHUB_REPO_URL_RE.match(url)
    if not m:
        raise ValueError(f"could not parse github repo URL: {url!r}")
    owner = m.group("owner")
    name = m.group("name")
    canonical = f"https://github.com/{owner}/{name}"
    return RepoRef(owner=owner, name=name, url=canonical)


class HttpClient(Protocol):
    def get(self, url: str, *, headers: dict[str, str] | None = None,
            params: dict[str, str | int] | None = None): ...


class GitHubClient:
    """Tiny wrapper around the REST API. Sync-only; suitable for use in
    background workers and FastAPI sync routes (see ASSUMPTION-007).
    """

    BASE_URL = "https://api.github.com"

    def __init__(self, token: str | None, http: HttpClient | None = None) -> None:
        self._token = token
        self._http = http  # injected in tests; created lazily otherwise

    def _client(self) -> HttpClient:
        if self._http is None:
            import httpx  # local import keeps tests light
            self._http = httpx.Client(timeout=30.0)
        return self._http

    def _headers(self) -> dict[str, str]:
        h = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "slop-leaderboard",
        }
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def list_forks(self, owner: str, name: str) -> list[RepoRef]:
        """Return every fork of `owner/name`, paginated until exhausted.

        GitHub returns at most 100 per page; we keep going until a short page.
        """
        results: list[RepoRef] = []
        page = 1
        while True:
            url = f"{self.BASE_URL}/repos/{owner}/{name}/forks"
            try:
                resp = self._client().get(
                    url,
                    headers=self._headers(),
                    params={"per_page": 100, "page": page},
                )
            except Exception as exc:  # network failure
                raise GitHubError(f"github request failed: {exc}") from exc
            if resp.status_code != 200:
                raise GitHubError(
                    f"github responded {resp.status_code} for {url}: {resp.text[:200]}"
                )
            payload = resp.json()
            if not isinstance(payload, list):
                raise GitHubError(f"unexpected forks payload type: {type(payload)}")
            for item in payload:
                results.append(_repo_ref_from_api(item))
            if len(payload) < 100:
                break
            page += 1
        return results


def _repo_ref_from_api(item: dict) -> RepoRef:
    """Build a RepoRef from a /repos/{}/{}/forks list item."""
    owner_obj = item.get("owner") or {}
    owner = owner_obj.get("login") or item.get("full_name", "/").split("/")[0]
    name = item.get("name")
    html_url = item.get("html_url") or f"https://github.com/{owner}/{name}"
    if not owner or not name:
        raise GitHubError(f"fork list item missing owner/name: {item!r}")
    return RepoRef(owner=owner, name=name, url=html_url)


def dedupe_refs(refs: Iterable[RepoRef]) -> list[RepoRef]:
    seen: set[str] = set()
    out: list[RepoRef] = []
    for ref in refs:
        key = ref.url.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(ref)
    return out
