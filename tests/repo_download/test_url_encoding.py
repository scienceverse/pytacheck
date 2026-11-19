"""Addresses with a space in a file's path, and the files whose download failed (#667).

Port of metacheck's test-url-encoding.R. httpx percent-encodes an address on its own,
so the R test of ``.mc_request()`` becomes a test that the address sent is encoded and an
encoded one is left alone.
"""

from __future__ import annotations

import io
import re
import zipfile

import httpx
import pandas as pd
import respx

from metacheck import http
from metacheck.archives.download import _download_failed_text, _failed_counts
from metacheck.archives.github import github_tree_files
from metacheck.archives.zip_peek import _zip_fetch_members, zip_peek


def test_github_tree_files_encodes_each_part_of_a_files_path_in_its_address() -> None:
    # a GitHub answer with paths that have a space, "#", "?", "%" and "+"
    tree = {
        "truncated": False,
        "tree": [
            {"path": "data/screened/henklain et al 2019/Script.R", "type": "blob", "size": 10},
            {"path": "a#b/c?d%e+f.csv", "type": "blob", "size": 10},
            {"path": "plain/file.R", "type": "blob", "size": 10},
        ],
    }
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https://api\.github\.com/repos/owner/repo/git/trees/.*").mock(
            return_value=httpx.Response(200, json=tree)
        )
        router.get(url__regex=r"https://api\.github\.com/repos/owner/repo(\?.*)?$").mock(
            return_value=httpx.Response(200, json={"default_branch": "main"})
        )
        f = github_tree_files("owner/repo")["files"]
    base = "https://raw.githubusercontent.com/owner/repo/main/"
    assert f["download_url"].tolist() == [
        base + p
        for p in (
            "data/screened/henklain%20et%20al%202019/Script.R",
            "a%23b/c%3Fd%25e%2Bf.csv",
            "plain/file.R",
        )
    ]
    # the path and name stay as they are: the cache and the code's paths use them
    assert f["path"].iloc[0] == "data/screened/henklain et al 2019/Script.R"


def test_files_whose_download_failed_are_named_in_the_report_and_counted() -> None:
    f = pd.DataFrame(
        {
            "repo_url": ["https://osf.io/abcde"] * 2,
            "file_name": ["statcheck output.csv", "notes.txt"],
            "error": ["Failed to parse URL: Malformed input to a URL function\nmore", "HTTP 503"],
        }
    )
    x = _download_failed_text(f)
    assert x["report"][0] == "#### Files that could not be downloaded"
    assert "2 files could not be downloaded, so the checks did not see them" in x["report"][1]
    assert "Failed to parse URL: Malformed input to a URL function (1)" in x["report"][1]
    assert x["summary"] == "2 files could not be downloaded, so the checks did not see them."
    assert _download_failed_text(None) == {"report": None, "summary": None}
    assert _download_failed_text(f.iloc[0:0]) == {"report": None, "summary": None}


def test_the_reasons_are_grouped_and_a_file_that_failed_twice_counts_once() -> None:
    names = [f"f{i}.csv" for i in range(12)]
    f = pd.DataFrame(
        {
            "repo_url": ["r"] * 13,
            "file_name": [*names, "f0.csv"],
            "error": ["HTTP 503"] * 7
            + ["HTTP 404\nbody"] * 4
            + [
                "Failed to perform HTTP request.\nCaused by error in `curl()`:\n! Timeout was reached"
            ]
            + ["HTTP 503"],
        }
    )
    x = _download_failed_text(f)
    text = x["report"][1]
    assert text.startswith("12 files could not be downloaded, so the checks did not see them: ")
    # the first ten named, the rest counted
    assert "`f9.csv`, and 2 more." in text
    assert "The reasons given: HTTP 503 (7); HTTP 404 (4); Timeout was reached (1)." in text
    # the one file is a single file
    one = _download_failed_text(f.iloc[[0, 12]])
    assert one["summary"] == "1 file could not be downloaded, so the checks did not see it."


def test_failed_files_are_counted_per_paper() -> None:
    f = pd.DataFrame(
        {
            "repo_url": ["r", "r", "s", "r"],
            "file_name": ["a", "b", "a", "a"],
            "paper_id": ["p1", "p2", "p1", "p1"],
        }
    )
    assert _failed_counts(f, ["p1", "p2", "p3"]) == [2, 1, 0]
    assert _failed_counts(f, ["p1"]) == [3]
    assert _failed_counts(None, ["p1", "p2"]) == [0, 0]


def test_every_request_encodes_its_address_and_leaves_an_encoded_one_as_it_is() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200)

    urls = [
        "https://example.org/data raw.zip",
        "https://api.osf.io/v2/nodes/abcde/files/?page[size]=100&filter[name]=a",
        "https://example.org/data%20raw.zip",
    ]
    with respx.mock(assert_all_called=False) as router:
        router.route(host__regex=r".*").mock(side_effect=handler)
        for u in urls:
            http.request("GET", u)
    assert seen == [
        "https://example.org/data%20raw.zip",
        urls[1],
        "https://example.org/data%20raw.zip",
    ]


def test_members_of_a_zip_whose_address_has_spaces_are_fetched_by_byte_range(tmp_path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("a.csv", "id,x\n1,2\n" * 50)
        zf.writestr("b.csv", "id,y\n3,4\n" * 50)
    data = buf.getvalue()
    url = "https://raw.example.org/o/r/main/data/van der kaap/other/data raw.zip"
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        rng = request.headers.get("Range")
        if request.method == "HEAD":
            return httpx.Response(200, headers={"Content-Length": str(len(data))})
        if rng:
            m = re.fullmatch(r"bytes=(\d+)-(\d+)", rng)
            assert m is not None
            a, b = int(m.group(1)), int(m.group(2))
            return httpx.Response(206, content=data[a : b + 1])
        return httpx.Response(200, content=data)

    with respx.mock(assert_all_called=False) as router:
        router.route(host="raw.example.org").mock(side_effect=handler)
        cd = zip_peek(url)
        assert cd is not None
        r = _zip_fetch_members(url, names=cd["name"].tolist()[:2], dest=str(tmp_path))
    assert r["ok"].tolist() == [True, True]
    assert all("%20" in u and " " not in u for u in seen)


def test_a_failed_file_without_an_error_text_is_reported_with_a_reason() -> None:
    """metacheck stops on an empty error text and leaves the reason blank for a missing one (U256)."""
    f = pd.DataFrame(
        {"repo_url": ["r", "r"], "file_name": ["a.csv", "b.csv"], "error": ["", "HTTP 404"]}
    )
    text = _download_failed_text(f)["report"][1]
    assert "The reasons given: HTTP 404 (1); no reason given (1)." in text
    g = pd.DataFrame({"repo_url": ["r"], "file_name": ["a.csv"], "error": [None]})
    assert "The reason given: no reason given (1)." in _download_failed_text(g)["report"][1]


def test_code_check_names_a_code_file_that_could_not_be_downloaded() -> None:
    """A code file at an address that refuses connections is in the report and counted (#667)."""
    import metacheck as pc
    from metacheck.module import ModuleOutput, module_run

    p = pc.test_paper(["Some text."])
    table = pd.DataFrame(
        {
            "paper_id": [p.paper_id],
            "repo_url": ["https://example.org/owner/repo"],
            "repo_type": ["other"],
            "file_name": ["analysis.R"],
            "file_path": ["analysis.R"],
            "file_url": ["http://127.0.0.1:9/analysis.R"],
            "file_size": [100.0],
            "file_type": ["code"],
            "file_location": pd.Series([None], dtype="string"),
        }
    )
    prev = ModuleOutput(
        module="repo_check",
        title="Repository Check",
        section="general",
        table=table,
        report="",
        traffic_light="info",
        summary_text=None,
        summary_table=pd.DataFrame({"paper_id": [p.paper_id]}),
        paper=p,
        prev_outputs={},
        extras={},
    )
    mo = module_run(prev, "code_check")
    assert mo.summary_table["code_files_failed"].tolist() == [1]
    report = " ".join(b for b in mo.report if isinstance(b, str))
    assert "Files that could not be downloaded" in report
    assert "1 file could not be downloaded" in mo.summary_text
