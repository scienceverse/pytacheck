"""Port of metacheck's tests/testthat/test-archive-github.R (plus the Git Trees API)."""

from __future__ import annotations

import pandas as pd
import pytest

import pytacheck as pc
from pytacheck.archives import github
from pytacheck.archives.github import (
    _github_config,
    github_files,
    github_info,
    github_languages,
    github_links,
    github_readme,
    github_repo,
    github_tree_files,
)

FILE_COLS = ["repo", "clean_repo", "name", "path", "download_url", "size", "ext", "type"]
TREE_COLS = ["repo", "clean_repo", "name", "path", "download_url", "size", "type"]


def test_errors(apis: object) -> None:
    with pytest.raises(TypeError):
        github_links(1)

    repo = "scienceverse/norepo"
    assert github_repo(repo) is None
    assert github_info(repo) is None
    assert github_readme(repo) == ""
    assert github_languages(repo) is None
    assert github_files(repo) is None


def test_github_links() -> None:
    exp = [
        "github.com/a/b1",
        "http://github.com/a/b2",
        "https://github.com/a/b3",
        "https://github.com/a/b4.git",
        "https://github.com/a/b5/file",
        "https://github.com/a/b6",
        "https://github.com/a/b7",
        "https://github.com/a/b8",
        "a/b9",
    ]
    paper = pc.test_paper(["The github repo is a/b9"], url=exp[:8])
    obs = github_links(paper)
    assert set(obs["href"]) == set(exp)
    assert list(obs.columns) == ["href", "text_id", "paper_id"]


def test_github_links_bare_mentions() -> None:
    paper = pc.test_paper(
        [
            "See our github repo at scienceverse/metacheck.",
            "Docs at user.github.io/project and org/repo on github.",
            "Code is on GitHub (https://github.com/a/b) and elsewhere at c/d.",
            "Nothing here.",
        ]
    )
    obs = github_links(paper)
    assert obs["href"].tolist() == ["scienceverse/metacheck"]
    assert obs["text_id"].tolist() == [1]


def test_github_links_none() -> None:
    obs = github_links(pc.test_paper(["No links here."]))
    assert len(obs) == 0
    assert list(obs.columns) == ["href", "text_id", "paper_id"]


def test_github_config(monkeypatch: pytest.MonkeyPatch) -> None:
    h = _github_config()
    assert h["Accept"] == "application/vnd.github.v3+json"
    assert h["User-Agent"] == "scienceverse/metacheck"
    assert "Authorization" not in h

    monkeypatch.setitem(github._TOKEN_CACHE, "token", "abc")
    assert _github_config({"X": "1"}) == {
        "X": "1",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "scienceverse/metacheck",
        "Authorization": "token abc",
    }


def test_github_token_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(github._TOKEN_CACHE, "token")
    monkeypatch.setenv("GITHUB_PAT_GITHUB_COM", "from-env")
    assert github._github_token() == "from-env"
    assert github._TOKEN_CACHE["token"] == "from-env"


def test_github_repo(apis: object) -> None:
    urls = [
        "scienceverse/metacheck",
        "https://github.com/scienceverse/metacheck",
        "http://github.com/scienceverse/metacheck.git",
        "https://github.com/scienceverse/metacheck/index",
    ]
    for url in urls:
        assert github_repo(url) == "scienceverse/metacheck"

    # vectorised
    url = ["scienceverse/metacheck", "scienceverse/faux"]
    assert github_repo(url) == url

    # vectorised multiple
    url = ["scienceverse/metacheck", "scienceverse/faux", "scienceverse/metacheck.git"]
    assert github_repo(url) == [
        "scienceverse/metacheck",
        "scienceverse/faux",
        "scienceverse/metacheck",
    ]

    # a repository that does not exist, among ones that do
    assert github_repo(["scienceverse/metacheck", "scienceverse/norepo"]) == [
        "scienceverse/metacheck",
        None,
    ]
    assert github_repo([]) is None
    assert github_repo(None) is None
    assert github_repo("nothing") is None
    assert github_repo(pd.Series(["scienceverse/faux"])) == "scienceverse/faux"


def test_github_repo_needs_no_request_without_a_match(apis: object) -> None:
    import respx

    assert isinstance(apis, respx.MockRouter)
    assert github_repo("no slash here") is None
    assert apis.calls.call_count == 0


def test_github_readme(apis: object) -> None:
    readme = github_readme("scienceverse/metacheck")
    assert "# metacheck\n\n" in readme

    repo = ["scienceverse/metacheck", "scienceverse/typo", "scienceverse/faux"]
    readmes = github_readme(repo)
    assert readmes[0] == readme
    assert len(readmes) == 3
    assert readmes[1] == ""
    assert github_readme([]) == ""


def test_github_languages(apis: object) -> None:
    lang = github_languages("scienceverse/metacheck")
    assert "R" in lang["language"].tolist()
    assert list(lang.columns) == ["repo", "language", "bytes"]

    repo = ["scienceverse/metacheck", "scienceverse/typo", "scienceverse/faux"]
    langs = github_languages(repo)
    assert list(langs.columns) == ["repo", "language", "bytes"]
    assert repo[0] in langs["repo"].tolist()
    assert repo[1] not in langs["repo"].tolist()
    assert repo[2] in langs["repo"].tolist()


def test_github_languages_none_listed(respx_mock: object) -> None:
    import httpx
    import respx

    assert isinstance(respx_mock, respx.MockRouter)
    respx_mock.head("https://github.com/org/empty").mock(return_value=httpx.Response(200))
    respx_mock.get("https://api.github.com/repos/org/empty/languages").mock(
        return_value=httpx.Response(200, json={})
    )
    lang = github_languages("org/empty")
    assert lang["repo"].tolist() == ["org/empty"]
    assert lang["language"].isna().all()
    assert lang["bytes"].isna().all()


def test_github_files(apis: object) -> None:
    # repo name already clean
    repo = "scienceverse/metacheck"
    files = github_files(repo)
    assert list(files.columns) == FILE_COLS
    assert "README.md" in files["name"].tolist()
    assert set(files["repo"]) <= {repo}
    assert set(files["clean_repo"]) <= {repo}

    # repo name not clean
    files = github_files("https://scienceverse/metacheck")
    assert set(files["repo"]) == {"https://scienceverse/metacheck"}
    assert set(files["clean_repo"]) == {"scienceverse/metacheck"}

    # set dir
    tests = github_files("scienceverse/metacheck", "tests")
    assert tests["path"].tolist() == ["tests/testthat", "tests/testthat.R"]

    # recursive
    files_f = github_files("scienceverse/metacheck", ".github", recursive=False)
    files_t = github_files("scienceverse/metacheck", ".github", recursive=True)
    assert len(files_f) < len(files_t)
    assert "pkgdown.yaml" in files_t["name"].tolist()
    assert "pkgdown.yaml" not in files_f["name"].tolist()

    # vectorised
    repo2 = ["scienceverse/metacheck", "scienceverse/demo"]
    files = github_files(repo2)
    assert set(files["repo"]) <= set(repo2)
    assert set(files["clean_repo"]) <= set(repo2)


def test_github_files_types_and_order(apis: object) -> None:
    files = github_files("scienceverse/demo", recursive=True)
    # sorted by path within each directory listing, sub-directories appended
    assert files["path"].tolist()[:4] == ["code", "folder", "good-example.R", "README.md"]
    types = dict(zip(files["name"], files["type"], strict=True))
    assert types["code"] == "dir"
    assert types["good-example.R"] == "code"
    assert types["Archive.zip"] == "archive"
    assert files["download_url"].isna().tolist()[:2] == [True, True]


def test_github_files_duplicate_extensions(mocks: object) -> None:
    # metacheck::file_types lists html as both "code" and "web": the file is
    # repeated once per type, as dplyr::left_join() does
    files = github_files("gzorg/fallrepo")
    assert files["name"].tolist() == ["A.csv", "b.txt", "page.html", "page.html"]
    assert files["type"].tolist() == ["data", "text", "code", "web"]


def test_github_files_rate_limited(mocks: object) -> None:
    assert github_files("gzorg/limited") is None


def test_github_files_vector_keeps_input_rows(apis: object) -> None:
    files = github_files(["scienceverse/demo", None, "scienceverse/norepo"])
    assert files["repo"].isna().sum() == 1
    norepo = files[files["repo"] == "scienceverse/norepo"]
    assert len(norepo) == 1
    assert norepo["name"].isna().all()


def test_github_info(apis: object) -> None:
    repo = "scienceverse/metacheck"
    info = github_info(repo)
    assert list(info) == ["repo", "readme", "files", "languages"]

    # files
    assert list(info["files"].columns) == FILE_COLS
    assert "README.md" in info["files"]["name"].tolist()

    # readme
    assert "# metacheck" in info["readme"]

    # languages
    assert "R" in info["languages"]["language"].tolist()
    assert list(info["languages"].columns) == ["repo", "language", "bytes"]
    assert (info["languages"]["repo"] == repo).all()


def test_github_tree_files(mocks: object) -> None:
    res = github_tree_files("https://github.com/gzorg/treerepo")
    assert res["gated"] is False
    assert res["reason"] is None
    assert res["default_branch"] == "dev"
    assert res["license"] == "MIT"
    files = res["files"]
    assert list(files.columns) == TREE_COLS
    assert (
        files["download_url"].iloc[0]
        == "https://raw.githubusercontent.com/gzorg/treerepo/dev/README.md"
    )
    assert files["path"].tolist().count("code/config.json") == 2  # json: code and data
    assert pd.isna(files.loc[files["name"] == "weird.file-name", "size"]).all()


def test_github_tree_files_gated(mocks: object) -> None:
    big = github_tree_files("gzorg/bigrepo")
    assert big["gated"] is True
    assert "truncated" in big["reason"]
    assert big["files"] is None
    assert big["license"] is None

    bad = github_tree_files("nothing")
    assert bad == {
        "gated": True,
        "reason": "invalid or inaccessible GitHub repository",
        "files": None,
        "default_branch": None,
        "license": None,
    }


def test_github_tree_files_fallbacks(mocks: object) -> None:
    # no blobs at all
    empty = github_tree_files("gzorg/emptyrepo")
    assert empty["gated"] is False
    assert len(empty["files"]) == 0
    assert list(empty["files"].columns) == TREE_COLS
    assert empty["license"] is None

    # the metadata request fails: the recursive contents listing is used
    fall = github_tree_files("gzorg/fallrepo")
    assert fall["default_branch"] == "main"
    assert list(fall["files"].columns) == FILE_COLS

    # the tree request fails and the contents listing is empty too
    notree = github_tree_files("gzorg/notree")
    assert notree["files"] is None
    assert notree["license"] == "GPL-3.0"


def test_file_types_table() -> None:
    ft = github._file_types()
    assert list(ft.columns) == ["ext", "type"]
    assert len(ft) == 404
    assert ft.loc[ft["ext"] == "json", "type"].tolist() == ["code", "data"]
