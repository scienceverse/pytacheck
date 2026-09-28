"""Tests for R/archive-gitlab.R's port (metacheck has no testthat file for it)."""

from __future__ import annotations

import json

import httpx
import pandas as pd
import pytest
import respx

import pytacheck as pc
from pytacheck.archives.gitlab import (
    _gitlab_blob_sizes,
    _gitlab_config,
    _gitlab_pat,
    _gitlab_project_id,
    _graphql_body,
    gitlab_links,
    gitlab_pat,
    gitlab_repo,
    gitlab_tree_files,
)

TREE_COLS = ["repo", "clean_repo", "name", "path", "download_url", "size", "type"]


def test_gitlab_links() -> None:
    paper = pc.test_paper(
        [
            "Code on gitlab at lab/project and more.",
            "Mirror at gitlab.com/x/y is here.",
            "Pages at user.gitlab.io/site with gitlab a/b",
        ],
        url=[
            "https://gitlab.com/gzorg/sub/glproj",
            "https://gitlab.com/a",
            "https://github.com/x/y",
        ],
    )
    links = gitlab_links(paper)
    assert list(links.columns) == ["href", "text_id", "paper_id"]
    assert links["href"].tolist() == ["https://gitlab.com/gzorg/sub/glproj", "lab/project"]
    assert links["text_id"].tolist() == [1, 1]


def test_gitlab_links_none() -> None:
    links = gitlab_links(pc.test_paper(["nothing"]))
    assert len(links) == 0


def test_gitlab_repo(mocks: object) -> None:
    assert gitlab_repo("https://gitlab.com/gzorg/sub/glproj.git/") == "gzorg/sub/glproj"
    assert gitlab_repo("HTTPS://WWW.GitLab.com/gzorg/bigproj/") == "gzorg/bigproj"
    assert gitlab_repo("gzorg/bigproj") == "gzorg/bigproj"
    assert gitlab_repo("gzorg/missing") is None
    assert gitlab_repo(["gzorg/bigproj", "gzorg/missing"]) == ["gzorg/bigproj", None]
    assert gitlab_repo([]) is None
    assert gitlab_repo(None) is None


@pytest.mark.parametrize("repo", ["justonepart", "a b/c", "", "  ", pd.NA])
def test_gitlab_repo_invalid_makes_no_request(repo: object) -> None:
    with respx.mock(assert_all_called=False) as router:
        assert gitlab_repo(repo) is None
        assert router.calls.call_count == 0


def test_gitlab_project_id() -> None:
    assert _gitlab_project_id("gzorg/sub/glproj") == "gzorg%2Fsub%2Fglproj"


def test_gitlab_pat(monkeypatch: pytest.MonkeyPatch) -> None:
    assert gitlab_pat() == ""
    monkeypatch.setenv("GITLAB_PAT", "env-token")
    assert gitlab_pat() == "env-token"
    assert _gitlab_config()["PRIVATE-TOKEN"] == "env-token"
    assert gitlab_pat("set-token") == "set-token"
    assert gitlab_pat() == "set-token"
    assert _gitlab_pat() == "set-token"
    with pytest.raises(ValueError, match="single string"):
        gitlab_pat(123)
    with pytest.raises(ValueError, match="single string"):
        gitlab_pat(["a", "b"])


def test_gitlab_config_without_token() -> None:
    assert _gitlab_config() == {"User-Agent": "scienceverse/metacheck"}


def test_gitlab_tree_files(mocks: object) -> None:
    res = gitlab_tree_files("https://gitlab.com/gzorg/sub/glproj")
    assert res["gated"] is False
    assert res["default_branch"] == "develop"
    assert res["license"] == "mit"
    files = res["files"]
    assert list(files.columns) == TREE_COLS
    # both tree pages were read; tree entries (folders) are not files
    # x.json is one row, though json has two file types (U46)
    assert files["path"].tolist() == ["README.md", "data/d1.csv", "src/run.py", "x.json"]
    assert files["download_url"].iloc[0] == (
        "https://gitlab.com/gzorg/sub/glproj/-/raw/develop/README.md"
    )
    # sizes from GraphQL (strings and numbers); x.json was not returned
    assert files["size"].tolist()[:3] == [1234.0, 56.0, 789.0]
    assert files["size"].iloc[3:].isna().all()


def test_gitlab_tree_files_graphql_batches(mocks: object) -> None:
    import respx as _respx

    assert isinstance(mocks, _respx.MockRouter)
    res = gitlab_tree_files("gzorg/bigproj")
    files = res["files"]
    assert len(files) == 95
    assert files["size"].tolist() == [float(i * 10) for i in range(1, 96)]
    graphql = [c for c in mocks.calls if c.request.url.path == "/api/graphql"]
    assert len(graphql) == 2
    sizes = [len(json.loads(c.request.content)["query"].split('","')) for c in graphql]
    assert sizes == [90, 5]
    assert res["license"] is None
    assert res["default_branch"] == "main"


def test_gitlab_tree_files_failures(mocks: object) -> None:
    gated = {
        "gated": True,
        "reason": "invalid or inaccessible GitLab repository",
        "files": None,
        "default_branch": None,
        "license": None,
    }
    assert gitlab_tree_files("gzorg/missing") == gated
    assert gitlab_tree_files("nothing") == gated
    assert gitlab_tree_files("gzorg/nometa") == gated

    notree = gitlab_tree_files("gzorg/notree")
    assert notree["gated"] is False
    assert notree["files"] is None
    assert notree["default_branch"] == "trunk"

    empty = gitlab_tree_files("gzorg/emptyproj")
    assert len(empty["files"]) == 0
    assert list(empty["files"].columns) == TREE_COLS
    assert empty["license"] is None


def test_gitlab_blob_sizes(mocks: object) -> None:
    assert len(_gitlab_blob_sizes("x/y", [])) == 0
    sizes = _gitlab_blob_sizes(
        "gzorg/sub/glproj", ["README.md", "data/d1.csv", "src/run.py", "x.json"]
    )
    assert sizes.to_dict() == {"README.md": 1234.0, "data/d1.csv": 56.0, "src/run.py": 789.0}


def test_gitlab_blob_sizes_failed_batch(respx_mock: respx.MockRouter) -> None:
    respx_mock.post("https://gitlab.com/api/graphql").mock(
        return_value=httpx.Response(
            200, json={"errors": [{"message": "Query has complexity of 202"}]}
        )
    )
    assert len(_gitlab_blob_sizes("x/y", ["a"])) == 0


def test_graphql_body_escapes_quotes() -> None:
    body = json.loads(_graphql_body("g/p", ['a"b.txt', "c"]))
    assert body == {
        "query": 'query { project(fullPath: "g/p") { repository { blobs(paths: '
        '["a\\"b.txt","c"]) { nodes { path size } } } } }'
    }
