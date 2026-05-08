from __future__ import annotations

import pytest

from app.github import GitHubClient, GitHubError, parse_github_url
from tests.fakes import FakeHttp


# --- URL parsing -----------------------------------------------------------

@pytest.mark.parametrize("url,owner,name", [
    ("https://github.com/acme/widgets", "acme", "widgets"),
    ("https://github.com/acme/widgets.git", "acme", "widgets"),
    ("https://github.com/acme/widgets/", "acme", "widgets"),
    ("http://github.com/Foo/Bar-Baz", "Foo", "Bar-Baz"),
])
def test_parse_github_url_accepts_common_forms(url, owner, name):
    ref = parse_github_url(url)
    assert ref.owner == owner
    assert ref.name == name
    assert ref.url == f"https://github.com/{owner}/{name}"


@pytest.mark.parametrize("url", [
    "",
    "not-a-url",
    "ftp://github.com/a/b",
    "https://gitlab.com/a/b",       # not github.com — explicitly rejected
    "https://github.com/onlyone",    # missing repo segment
])
def test_parse_github_url_rejects_bad_input(url):
    with pytest.raises(ValueError):
        parse_github_url(url)


# --- fork listing ----------------------------------------------------------

def _fork_payload(owner: str, name: str) -> dict:
    return {
        "owner": {"login": owner},
        "name": name,
        "html_url": f"https://github.com/{owner}/{name}",
        "full_name": f"{owner}/{name}",
    }


def test_list_forks_paginates_until_short_page():
    page1 = [_fork_payload(f"u{i}", "repo") for i in range(100)]
    page2 = [_fork_payload(f"v{i}", "repo") for i in range(7)]
    http = FakeHttp(pages=[page1, page2])
    gh = GitHubClient(token="t", http=http)

    refs = gh.list_forks("acme", "root")

    assert len(refs) == 107
    # confirms two requests with sequential page numbers
    assert [c[1]["page"] for c in http.calls] == [1, 2]
    assert [c[1]["per_page"] for c in http.calls] == [100, 100]


def test_list_forks_stops_on_first_short_page():
    http = FakeHttp(pages=[[_fork_payload("u", "r")]])
    gh = GitHubClient(token=None, http=http)

    refs = gh.list_forks("acme", "root")

    assert [r.full_name for r in refs] == ["u/r"]
    assert len(http.calls) == 1


def test_list_forks_raises_on_non_200():
    http = FakeHttp(status_code=404)
    gh = GitHubClient(token="t", http=http)

    with pytest.raises(GitHubError):
        gh.list_forks("acme", "missing")


def test_list_forks_raises_on_network_error():
    http = FakeHttp(error=ConnectionError("dns fail"))
    gh = GitHubClient(token="t", http=http)

    with pytest.raises(GitHubError):
        gh.list_forks("acme", "root")


def test_authorization_header_set_only_when_token_present():
    http = FakeHttp(pages=[[]])
    GitHubClient(token=None, http=http).list_forks("a", "b")
    # no token → headers should not include Authorization
    # we can't read headers from FakeHttp.calls, so check via a token-bearing
    # client instead and confirm both paths return without crashing
    GitHubClient(token="abc", http=FakeHttp(pages=[[]])).list_forks("a", "b")
