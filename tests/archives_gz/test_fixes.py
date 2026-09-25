"""metacheck bugs fixed in the GitHub and Zenodo ports (docs/UPSTREAM_ISSUES.md).

The parity cases of these fixes are marked ``known_divergence``; these tests
pin the fixed behaviour.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx

from pytacheck.archives import github
from pytacheck.archives.github import github_files, github_languages
from pytacheck.archives.zenodo import _zenodo_info

JSON = {"Content-Type": "application/json"}


@pytest.fixture
def gh(monkeypatch: pytest.MonkeyPatch) -> Iterator[respx.MockRouter]:
    """GitHub answered by respx; every repository exists, no token."""
    monkeypatch.setattr(github, "_github_token", lambda: None)
    with respx.mock(assert_all_called=False) as router:
        router.head(url__startswith="https://github.com/").mock(return_value=httpx.Response(200))
        yield router


def _entry(path: str, kind: str = "file") -> dict[str, Any]:
    return {
        "name": path.rsplit("/", 1)[-1],
        "path": path,
        "type": kind,
        "size": 10,
        "download_url": None if kind == "dir" else f"https://raw.example/{path}",
    }


def test_github_files_passes_dir_on_for_several_repositories(gh: respx.MockRouter) -> None:
    # U48: metacheck lists each repository from its root
    api = "https://api.github.com/repos"
    gh.get(f"{api}/o/a/contents/data").mock(
        return_value=httpx.Response(200, json=[_entry("data/x.csv")], headers=JSON)
    )
    gh.get(f"{api}/o/b/contents/data").mock(
        return_value=httpx.Response(200, json=[_entry("data/y.csv")], headers=JSON)
    )
    gh.route().mock(return_value=httpx.Response(404, json={"message": "Not Found"}, headers=JSON))
    out = github_files(["o/a", "o/b"], dir="data")
    assert out["path"].tolist() == ["data/x.csv", "data/y.csv"]
    # none can be listed: None (metacheck's join fails)
    assert github_files(["o/c", "o/d"]) is None


def test_github_files_single_file_and_empty_listings(gh: respx.MockRouter) -> None:
    # U48: a file path is listed as one object and an empty folder as [];
    # metacheck fails on both
    api = "https://api.github.com/repos/o/a/contents"
    gh.get(f"{api}/README.md").mock(
        return_value=httpx.Response(200, json=_entry("README.md"), headers=JSON)
    )
    gh.get(f"{api}/empty").mock(return_value=httpx.Response(200, json=[], headers=JSON))
    one = github_files("o/a", dir="README.md")
    assert one["path"].tolist() == ["README.md"]
    assert one["type"].tolist() == ["text"]
    empty = github_files("o/a", dir="empty")
    assert len(empty) == 0
    assert "path" in empty.columns


def test_github_languages_error_body_is_no_languages(gh: respx.MockRouter) -> None:
    # U48: metacheck reads the error body as languages named after its fields
    api = "https://api.github.com/repos"
    gh.get(f"{api}/o/a/languages").mock(
        return_value=httpx.Response(
            403,
            json={"message": "API rate limit exceeded", "documentation_url": "x"},
            headers=JSON,
        )
    )
    gh.get(f"{api}/o/b/languages").mock(
        return_value=httpx.Response(200, json={"R": 1200, "Python": 30}, headers=JSON)
    )
    assert github_languages("o/a") is None
    both = github_languages(["o/a", "o/b"])
    assert both["language"].tolist() == ["R", "Python"]


def test_zenodo_string_license() -> None:
    # U34: older records give the licence as a plain string; metacheck's
    # `metadata$license$id` fails on it
    body = {
        "id": 42,
        "doi": "10.5281/zenodo.42",
        "metadata": {"title": "Old record", "license": "cc-by"},
        "files": [],
    }
    resp = httpx.Response(
        200,
        json=body,
        headers=JSON,
        request=httpx.Request("GET", "https://zenodo.org/api/records/42"),
    )
    info = _zenodo_info("42", resp=resp)
    assert info["license"].tolist() == ["cc-by"]
    assert info["title"].tolist() == ["Old record"]
