"""Repository parts of metacheck 0.4.4.30 to 0.4.4.35: ports of the R tests added in that range.

test-repo-download.R (case-clash names, symbolic links, a path that leaves its folder),
test-archive-figshare.R (a record that came back empty), test-archive-aspredicted.R and the
symbolic-links and project-or-registration parts of repo_check.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import pytest
import respx

import metacheck as pc
from metacheck.archives.aspredicted import aspredicted_links
from metacheck.archives.download import _case_clash_rename, download_repo_files
from metacheck.archives.figshare import _figshare_info
from metacheck.archives.github import _repo_links, github_tree_files
from metacheck.archives.info_cache import _repo_info_ok
from metacheck.modules.repo_check import _symlinks_text


def test_paths_that_differ_only_in_capital_letters_get_names_of_their_own() -> None:
    rel = [
        "names/case/Data.csv",
        "names/case/data.csv",
        "names/case/DATA.CSV",
        "a.R",
        "Dir/x",
        "dir/x",
    ]
    assert _case_clash_rename(["r"] * 6, rel) == [
        "names/case/Data~2.csv",
        "names/case/data~3.csv",
        "names/case/DATA.CSV",
        "a.R",
        "Dir/x",
        "dir/x~2",
    ]
    # the same name in every call: chosen from the full listing
    listing = pd.DataFrame(
        {"repo_url": "r", "file_path": rel, "file_name": [p.split("/")[-1] for p in rel]}
    )
    assert _case_clash_rename(["r"], ["names/case/data.csv"], listing) == ["names/case/data~3.csv"]
    # paths of different repositories do not clash
    assert _case_clash_rename(["r", "s"], ["a.csv", "A.csv"]) == ["a.csv", "A.csv"]
    assert _case_clash_rename([], []) == []


def test_download_repo_files_stores_files_that_differ_only_in_capital_letters_apart(
    tmp_path: Path,
) -> None:
    (tmp_path / "u.csv").write_text("upper\n")
    (tmp_path / "l.csv").write_text("lower\n")
    files = pd.DataFrame(
        {
            "repo_url": "https://example.org/case700",
            "file_name": ["Data.csv", "data.csv"],
            "file_path": ["Data.csv", "data.csv"],
            "file_size": [(tmp_path / "u.csv").stat().st_size, (tmp_path / "l.csv").stat().st_size],
            "file_url": [
                "file:///" + str(tmp_path / "u.csv"),
                "file:///" + str(tmp_path / "l.csv"),
            ],
            "file_location": pd.Series([None, None], dtype="string"),
        }
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        dl = download_repo_files(files)
    assert dl is not None
    first, second = dl["file_location"].tolist()
    assert first != second
    assert Path(first).read_text().strip() == "upper"
    assert Path(second).read_text().strip() == "lower"


def test_repo_links_reads_a_links_target_from_its_folder_and_marks_broken_links() -> None:
    links = _repo_links(
        ["links/to_small.csv", "links/broken.R", "top.R", "links/up.R"],
        all_paths=["data/small.csv", "links", "data"],
        raw_urls=[None] * 4,  # type: ignore[list-item]
        targets=["../data/small.csv", "missing.R", None, "../../x.R"],
    )
    assert [None if pd.isna(b) else bool(b) for b in links["broken"]] == [False, True, None, True]
    assert links["target"].iloc[0] == "../data/small.csv"


def test_github_tree_files_leaves_symbolic_links_out_of_the_files() -> None:
    tree = {
        "truncated": False,
        "tree": [
            {"path": "data", "type": "tree", "mode": "040000"},
            {"path": "data/small.csv", "type": "blob", "mode": "100644", "size": 12},
            {"path": "links", "type": "tree", "mode": "040000"},
            {"path": "links/small.csv", "type": "blob", "mode": "120000", "size": 17},
        ],
    }
    with respx.mock(assert_all_called=False) as router:
        router.get("https://api.github.com/repos/o/r").mock(
            return_value=httpx.Response(200, json={"default_branch": "main", "full_name": "o/r"})
        )
        router.get("https://api.github.com/repos/o/r/git/trees/main?recursive=1").mock(
            return_value=httpx.Response(200, json=tree)
        )
        router.get("https://raw.githubusercontent.com/o/r/main/links/small.csv").mock(
            return_value=httpx.Response(200, text="../data/small.csv\n")
        )
        out: dict[str, Any] = github_tree_files("https://github.com/o/r")
    assert out["gated"] is False
    assert out["files"]["path"].tolist() == ["data/small.csv"]
    links = out["links"]
    assert links["path"].tolist() == ["links/small.csv"]
    assert links["target"].tolist() == ["../data/small.csv"]
    assert links["broken"].tolist() == [False]


def test_github_tree_files_has_no_links_without_symbolic_links() -> None:
    tree = {
        "truncated": False,
        "tree": [{"path": "a.R", "type": "blob", "mode": "100644", "size": 1}],
    }
    with respx.mock(assert_all_called=False) as router:
        router.get("https://api.github.com/repos/o/r").mock(
            return_value=httpx.Response(200, json={"default_branch": "main"})
        )
        router.get("https://api.github.com/repos/o/r/git/trees/main?recursive=1").mock(
            return_value=httpx.Response(200, json=tree)
        )
        out = github_tree_files("https://github.com/o/r")
    assert out["links"] is None
    assert out["files"]["path"].tolist() == ["a.R"]


def test_symlinks_are_reported_with_their_targets() -> None:
    assert _symlinks_text(None) == (None, [])
    links = pd.DataFrame(
        {
            "repo_url": ["https://github.com/o/r"] * 3,
            "path": ["links/a.csv", "links/b.csv", "c.csv"],
            "target": ["../data/a.csv", "missing.csv", None],
            "broken": pd.array([False, True, None], dtype="boolean"),
        }
    )
    summary, report = _symlinks_text(links)
    assert summary is not None and summary.startswith("We left out 3 symbolic links:")
    assert report[0] == "#### Symbolic links"
    assert report[1].startswith(summary)
    assert report[1].endswith("Put the file itself in the repository instead of a link to it.")
    assert report[3:] == [
        "- `links/a.csv` (https://github.com/o/r) points to `../data/a.csv`",
        "- `links/b.csv` (https://github.com/o/r) points to `missing.csv`, which is not in the "
        "repository",
        "- `c.csv` (https://github.com/o/r) points to a file we could not read",
    ]


def test_a_file_whose_path_leaves_its_folder_is_named_among_the_files_left_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import metacheck.archives.zip_peek as zp

    def fake_fetch(url: str, names: list[str], dest: str, **kw: Any) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "name": names,
                "path": [None] * len(names),
                "ok": [False] * len(names),
                "error": ["entry path escapes the archive (path traversal)"] * len(names),
            }
        )

    monkeypatch.setattr(zp, "_zip_fetch_members", fake_fetch)
    files = pd.DataFrame(
        {
            "repo_url": ["https://osf.io/abcde"],
            "file_name": ["escape.txt"],
            "file_path": ["x.zip/../escape.txt"],
            "file_url": [None],
            "file_size": [10.0],
            "archive_url": ["https://files.example.org/x.zip"],
            "archive_member": ["../escape.txt"],
        }
    )
    dl = download_repo_files(files)
    assert dl is not None
    assert dl["file_location"].isna().all()
    assert dl.attrs["failed"].empty
    refused = [v for v in dl.attrs["limit_refused"].tolist() if not pd.isna(v)]
    assert refused == ["it was not unpacked, because its path leads outside its folder"]


def test_a_figshare_answer_without_title_doi_or_files_is_a_failed_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import metacheck.archives.figshare as fs

    empty = httpx.Response(
        200, headers={"Content-Type": "application/json"}, content=b'{"files": [], "authors": []}'
    )
    monkeypatch.setattr(fs, "_query", lambda *a, **k: empty)
    with pytest.warns(UserWarning, match="answered without the record's title, DOI or files"):
        info = _figshare_info("14199176", host="data.4tu.nl")
    assert info["error"].tolist() == ["empty_response"]
    assert _repo_info_ok(info) is False


def test_a_listing_without_title_doi_and_files_is_not_kept_in_the_cache() -> None:
    blank = pd.DataFrame(
        {"figshare_id": ["1"], "title": [pd.NA], "doi": [pd.NA], "files": [pd.DataFrame()]}
    )
    assert _repo_info_ok(blank) is False
    assert _repo_info_ok(blank.assign(title="A data set")) is True
    with_files = blank.assign(files=[pd.DataFrame({"name": ["a.csv"]})])  # type: ignore[list-item]
    assert _repo_info_ok(with_files) is True
    assert _repo_info_ok(pd.DataFrame({"title": ["x"], "error": ["unfound"]})) is False
    assert _repo_info_ok(pd.DataFrame({"id": [1]})) is True
    assert _repo_info_ok(None) is False


def test_aspredicted_links_reads_links_written_in_the_text() -> None:
    p = pc.test_paper(
        [
            "See aspredicted.org/abc_de and www.aspredicted.org/ABC_DE/ and, later, https://aspredicted.org/xyz_12."
        ]
    )
    assert aspredicted_links(p)["href"].tolist() == [
        "https://aspredicted.org/abc_de",
        "https://aspredicted.org/xyz_12",
    ]
    # a link both marked and written in the text is listed once
    marked = pc.test_paper(["See aspredicted.org/abc_de."], url=["https://aspredicted.org/abc_de"])
    assert aspredicted_links(marked)["href"].tolist() == ["https://aspredicted.org/abc_de"]


def test_the_demo_paper_lists_its_aspredicted_link_once() -> None:
    assert len(aspredicted_links(pc.demopaper())["href"]) == 1


def _osf_listing_frame(url: str, otype: str, files: list[str], parent: str | None = None) -> Any:
    rows: list[dict[str, Any]] = [
        {"osf_url": url, "osf_type": otype, "osf_id": url.rsplit("/", 1)[-1], "parent": parent}
    ]
    for f in files:
        rows.append(
            {
                "osf_url": url,
                "kind": "file",
                "name": f,
                "path": "/" + f,
                "download_url": f"{url}/download/{f}",
                "size": 10.0,
                "filetype": "data",
                "public": True,
                "provider": "osfstorage",
                "project": url.rsplit("/", 1)[-1],
            }
        )
    return pd.DataFrame(rows)


def _list_osf_with(
    monkeypatch: pytest.MonkeyPatch, links: list[str], answers: dict[str, Any]
) -> tuple[Any, Any]:
    import metacheck.archives.osf as osf_mod
    from metacheck.modules import _repo_check as rc

    monkeypatch.setattr(
        osf_mod,
        "osf_info",
        lambda x, **k: answers.get(str(x).lower().replace("/?", "?"), pd.DataFrame()),
    )
    repos = rc.Repos(df=rc.repo_rows("p1", links, "osf", rc.NA_SCALAR))
    files, _ = rc.list_osf(repos, links, "p1", None, False)
    return repos, files


def test_a_project_and_its_registration_are_listed_once_with_the_larger_group_of_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # (metacheck #695, point 27) mp20233960 linked the project and its registration
    links = ["https://osf.io/62NXB", "https://osf.io/9nzqg"]
    answers = {
        "https://osf.io/62nxb": _osf_listing_frame(
            "https://osf.io/62NXB", "nodes", ["a.csv", "b.csv"]
        ),
        "https://osf.io/9nzqg": _osf_listing_frame(
            "https://osf.io/9nzqg", "registrations", ["a.csv", "b.csv", "c.csv"], parent="62nxb"
        ),
    }
    repos, files = _list_osf_with(monkeypatch, links, answers)
    # the registration's files are put under the project as the paper links it ("62NXB"), not
    # under a second spelling of it, and only the larger group is kept
    assert set(files["repo_url"]) == {"https://osf.io/62NXB"}
    assert sorted(files["file_name"]) == ["a.csv", "b.csv", "c.csv"]
    assert "reg_source" not in files.columns
    assert repos.df["repo_url"].tolist() == ["https://osf.io/62NXB"]


def test_on_a_tie_the_project_is_kept_and_not_its_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    links = ["https://osf.io/62nxb", "https://osf.io/9nzqg"]
    answers = {
        "https://osf.io/62nxb": _osf_listing_frame(
            "https://osf.io/62nxb", "nodes", ["a.csv", "b.csv"]
        ),
        "https://osf.io/9nzqg": _osf_listing_frame(
            "https://osf.io/9nzqg", "registrations", ["x.csv", "y.csv"], parent="62nxb"
        ),
    }
    _, files = _list_osf_with(monkeypatch, links, answers)
    assert sorted(files["file_name"]) == ["a.csv", "b.csv"]
