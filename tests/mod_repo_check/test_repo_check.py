"""Tests for the repo_check module (port of tests/testthat/test-module-repo_check.R).

Network tests replay recorded responses (metacheck's ``apis`` recordings and
this area's synthetic ones, see ``parity_support.py``); nothing reaches the
network.
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import pytest

import metacheck as pc
from tests.mod_repo_check.parity_support import FIXTURES, ROOT, mocked, tp

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

HERE = Path(__file__).resolve().parent
CODE_FILES = ROOT / "upstream" / "metacheck" / "tests" / "testthat" / "fixtures" / "code_files"
BIBR12 = ROOT / "upstream" / "metacheck" / "tests" / "testthat" / "fixtures" / "bibr12"
NO_REPOS = (
    "We found no links to repositories on the Open Science Framework, Github, GitLab, "
    "ResearchBox, DSpace, EPrints (ReShare and others), Invenio (Zenodo and others), "
    "Dataverse, Figshare, Dryad, 4TU.ResearchData, Mendeley Data, DataONE, LDbase, or CKAN "
    "data portals."
)
SUMMARY_COLS = [
    "paper_id",
    "repo_n",
    "repo_unchecked",
    "files_n",
    "files_data",
    "files_code",
    "files_readme",
    "files_zip",
    "files_unknown",
    "naming_issues",
    "repo_view_only",
    "roster_mismatch",
]


def run(paper: object, **kwargs: object) -> pc.ModuleOutput:
    with mocked():
        return pc.module_run(paper, "repo_check", **kwargs)


def summary_row(mo: pc.ModuleOutput) -> dict[str, object]:
    st = mo.summary_table
    assert list(st.columns) == SUMMARY_COLS
    assert len(st) == 1
    return {
        c: (None if pd.isna(v) else v.item() if hasattr(v, "item") else v)
        for c, v in st.iloc[0].items()
    }


# -- repo_check offline ----------------------------------------------------------------


def test_repo_check_offline() -> None:
    assert "repo_check" in pc.module_list()["name"].tolist()
    paper = pc.test_paper("No repos")
    mo = pc.module_run(paper, "repo_check")
    assert mo.traffic_light == "na"
    assert mo.table is None
    st = mo.summary_table
    assert list(st.columns) == [
        "paper_id",
        "repo_n",
        "files_n",
        "files_data",
        "files_code",
        "files_readme",
        "files_zip",
    ]
    assert st["paper_id"].tolist() == [paper.paper_id]
    assert st["repo_n"].tolist() == [0]
    assert (
        st[["files_n", "files_data", "files_code", "files_readme", "files_zip"]].isna().all().all()
    )
    assert mo.summary_text == NO_REPOS
    assert mo.report == NO_REPOS


def test_module_metadata() -> None:
    from metacheck.module import module_find

    spec = module_find("repo_check")
    assert spec.title == "Repository Check"
    assert "network" in spec.requires


# -- OSF (recorded) ----------------------------------------------------------------------


def test_osf_view_only_link() -> None:
    osf_url = "https://osf.io/t9j8e/? view_only=f171281f212f4435917b16a9e581a73b"
    mo = run(pc.test_paper(url=osf_url))
    # the view-only listing is not recorded: an empty table, the URL gated
    assert mo.table is not None and len(mo.table) == 0
    gated = mo["gated_repos"]
    assert gated["repo_url"].tolist() == [osf_url.replace(" ", "")]


def test_osf_no_files() -> None:
    paper = pc.test_paper()
    paper.url = pd.DataFrame({"href": ["https://osf.io/y6a34"], "text_id": [1]})
    mo = run(paper)
    assert mo.traffic_light == "yellow"
    assert summary_row(mo) == {
        "paper_id": paper.paper_id,
        "repo_n": 1,
        "repo_unchecked": 0,
        "files_n": 0,
        "files_data": 0,
        "files_code": 0,
        "files_readme": 0,
        "files_zip": 0,
        "files_unknown": 0,
        "naming_issues": 0,
        "repo_view_only": 0,
        "roster_mismatch": False,
    }
    assert " 0 files " in mo.summary_text


def test_no_code_files() -> None:
    paper = pc.test_paper()
    paper.url = pd.DataFrame({"href": ["https://osf.io/m4nbv"], "text_id": [1]})
    mo = run(paper)
    assert "We found 2 files " in mo.summary_text
    # README.md is the readme; "code.zip" is data_type "code" by name, but an
    # archive by file type
    assert summary_row(mo) == {
        "paper_id": paper.paper_id,
        "repo_n": 1,
        "repo_unchecked": 0,
        "files_n": 2,
        "files_data": 0,
        "files_code": 0,
        "files_readme": 1,
        "files_zip": 1,
        "files_unknown": 0,
        "naming_issues": 0,
        "repo_view_only": 0,
        "roster_mismatch": False,
    }


def test_osf() -> None:
    paper = pc.test_paper()
    paper.url = pd.DataFrame({"href": ["https://osf.io/629bx"], "text_id": [1]})
    mo = run(paper)
    assert "We found 4 files " in mo.summary_text
    assert summary_row(mo) == {
        "paper_id": paper.paper_id,
        "repo_n": 1,
        "repo_unchecked": 0,
        "files_n": 4,
        "files_data": 1,
        "files_code": 2,
        "files_readme": 0,
        "files_zip": 1,
        "files_unknown": 1,
        "naming_issues": 1,
        "repo_view_only": 0,
        "roster_mismatch": False,
    }


def test_osf_and_github() -> None:
    paper = pc.test_paper()
    paper.url = pd.DataFrame(
        {"href": ["osf.io/629bx", "github.com/scienceverse/demo"], "text_id": [1, 2]}
    )
    mo = run(paper)
    assert mo.traffic_light == "yellow"
    assert {"bad.R", "bad.Rmd", "good-example.R"} <= set(mo.table["file_name"])
    st = mo.summary_table
    assert st["paper_id"].tolist() == [paper.paper_id]
    assert st["repo_n"].tolist() == [2]
    assert st["files_n"].tolist() == [len(mo.table)]
    assert st["files_data"].iloc[0] >= 1
    assert st["files_code"].iloc[0] >= 1
    assert st["files_zip"].iloc[0] >= 1


def test_researchbox() -> None:
    # R goes to the live site; here the box page and its zip are recorded
    from tests.mod_repo_check.parity_support import MOCKS

    paper = pc.test_paper()
    paper.url = pd.DataFrame({"href": ["https://researchbox.org/4377"], "text_id": [1]})
    with mocked(HERE / "mocks_researchbox", MOCKS, "apis"):
        mo = pc.module_run(paper, "repo_check")
    assert "Study 1.r" in mo.table["file_name"].tolist()
    assert "Code/Study 1.r" in mo.table["file_path"].tolist()
    st = mo.summary_table
    assert st["repo_n"].tolist() == [1]
    assert st["files_data"].iloc[0] >= 1
    assert st["files_code"].iloc[0] >= 1


def test_zenodo() -> None:
    mo = run(pc.test_paper(url="https://zenodo.org/records/17754445"))
    assert mo.table["file_name"].tolist() == ["ResearchBox_4377.zip"]
    assert mo.summary_table["files_zip"].tolist() == [1]

    papers = pc.PaperList(
        [
            pc.test_paper(url="https://zenodo.org/records/17754445"),
            pc.test_paper(url="https://zenodo.org/records/123456789"),
        ]
    )
    mo = run(papers)
    assert mo.summary_table["files_n"].tolist() == [1, 1]
    assert mo.summary_table["files_zip"].tolist() == [1, 0]


# -- repo_check() + local_path -------------------------------------------------------------


def test_unknown_file_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A file without a size is listed, and shown as "—" in the report."""
    from metacheck.archives import local

    (tmp_path / "analysis.R").write_text("x <- 1\n")
    real = local.local_files

    def no_sizes(path: object, recursive: bool = False) -> pd.DataFrame:
        df = real(path, recursive=recursive)
        df["file_size"] = float("nan")
        return df

    monkeypatch.setattr(local, "local_files", no_sizes)
    mo = pc.module_run(pc.test_paper(), "repo_check", local_path=str(tmp_path))
    assert math.isnan(mo.table["file_size"].iloc[0])
    tables = [t for t in _tables(mo) if "Size" in t.columns]
    assert tables and tables[0]["Size"].tolist() == ["—"]


def _tables(mo: pc.ModuleOutput) -> list[pd.DataFrame]:
    from tests.mod_repo_check.parity_support import report_tables

    return report_tables(mo)


def test_vector_of_local_paths() -> None:
    local_path = [str(CODE_FILES / "analysis.R"), str(CODE_FILES / "README.md")]
    mo = pc.module_run(pc.test_paper(), "repo_check", local_path=local_path)
    assert mo.summary_table["repo_n"].tolist() == [2]
    assert "analysis.R" in mo.table["file_name"].tolist()
    assert "README.md" in mo.table["file_name"].tolist()


def test_local_path_only() -> None:
    mo = pc.module_run(pc.test_paper(), "repo_check", local_path=str(CODE_FILES))
    row = summary_row(mo)
    assert row["repo_n"] == 1
    assert row["files_n"] == 7
    assert row["files_code"] == 5  # .do is "stats" type, not "code"
    assert row["files_data"] == 1
    assert row["files_readme"] == 1
    assert row["files_zip"] == 0
    assert "analysis.R" in mo.table["file_name"].tolist()
    assert "README.md" in mo.table["file_name"].tolist()
    assert mo.traffic_light == "green"


def test_paper_and_local_path() -> None:
    paper = pc.test_paper(url="https://osf.io/629bx")
    mo = run(paper, local_path=str(CODE_FILES))
    row = summary_row(mo)
    assert row["repo_n"] == 2
    assert row["files_n"] == 11
    assert row["files_code"] == 7
    assert row["files_readme"] == 1
    assert row["files_zip"] == 1
    names = mo.table["file_name"].tolist()
    assert "README.md" in names and "analysis.R" in names
    assert any(n.lower().endswith("bad.r") for n in names)
    assert mo.traffic_light == "yellow"


# -- repo_check() + R package exclusion ------------------------------------------------------


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def test_excludes_r_package_tree(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "DESCRIPTION": "Package: mypkg\nVersion: 0.1.0\n",
            "NAMESPACE": "export(foo)\n",
            "R/foo.R": "foo <- function() 1\n",
            "man/foo.Rd": "\\name{foo}\n",
            "analysis.R": "x <- 1\n",
            "data.csv": "a,b\n1,2\n",
        },
    )
    mo = pc.module_run(pc.test_paper(), "repo_check", local_path=str(tmp_path))
    names = mo.table["file_name"].tolist()
    assert not {"DESCRIPTION", "NAMESPACE", "foo.R", "foo.Rd"} & set(names)
    assert "analysis.R" in names and "data.csv" in names
    assert mo.summary_table["files_n"].tolist() == [2]


def test_keeps_description_without_namespace(tmp_path: Path) -> None:
    _write(tmp_path, {"DESCRIPTION": "Package: mypkg\nVersion: 0.1.0\n", "analysis.R": "x <- 1\n"})
    mo = pc.module_run(pc.test_paper(), "repo_check", local_path=str(tmp_path))
    names = mo.table["file_name"].tolist()
    assert "DESCRIPTION" in names and "analysis.R" in names


def test_keeps_root_package_that_is_the_deposit(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "DESCRIPTION": "Package: mypkg\nVersion: 0.1.0\n",
            "NAMESPACE": "export(foo)\n",
            "R/foo.R": "foo <- function() 1\n",
            "man/foo.Rd": "\\name{foo}\n",
            "data/study_data.rda": "RDX3\n",
            ".Rbuildignore": "\n",
            "LICENSE": "MIT\n",
            "README.md": "# mypkg\n",
        },
    )
    mo = pc.module_run(pc.test_paper(), "repo_check", local_path=str(tmp_path))
    names = mo.table["file_name"].tolist()
    assert {"DESCRIPTION", "NAMESPACE", "foo.R", "study_data.rda"} <= set(names)


def test_excludes_nested_vendored_package(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "vendored_pkg/DESCRIPTION": "Package: vendoredpkg\nVersion: 0.1.0\n",
            "vendored_pkg/NAMESPACE": "export(bar)\n",
            "vendored_pkg/R/bar.R": "bar <- function() 1\n",
            "analysis.R": "x <- 1\n",
            "data.csv": "a,b\n1,2\n",
        },
    )
    mo = pc.module_run(pc.test_paper(), "repo_check", local_path=str(tmp_path))
    names = mo.table["file_name"].tolist()
    assert not {"DESCRIPTION", "NAMESPACE", "bar.R"} & set(names)
    assert "analysis.R" in names and "data.csv" in names


# -- repo_check() + local_only -------------------------------------------------------------------


def test_local_only_ignores_online_repos() -> None:
    paper = pc.test_paper(url="https://osf.io/629bx")
    mo = run(paper, local_path=str(CODE_FILES), local_only=True)
    assert mo.summary_table["repo_n"].tolist() == [1]
    assert mo.summary_table["files_n"].tolist() == [7]
    assert not mo.table["repo_url"].str.contains("osf.io", case=False).any()
    assert {"analysis.R", "README.md"} <= set(mo.table["file_name"])


def test_local_only_without_local_path_is_na() -> None:
    mo = pc.module_run(pc.test_paper(), "repo_check", local_only=True)
    assert mo.traffic_light == "na"
    assert mo.summary_table["repo_n"].tolist() == [0]


def test_local_only_with_online_urls_is_na() -> None:
    mo = pc.module_run(pc.test_paper(url="https://osf.io/629bx"), "repo_check", local_only=True)
    assert mo.traffic_light == "na"
    assert mo.summary_table["repo_n"].tolist() == [0]


def test_local_only_false_is_default() -> None:
    paper = pc.test_paper()
    a = run(paper, local_path=str(CODE_FILES))
    b = run(paper, local_path=str(CODE_FILES), local_only=False)
    pd.testing.assert_frame_equal(a.summary_table, b.summary_table)
    pd.testing.assert_frame_equal(a.table, b.table)
    assert a.traffic_light == b.traffic_light


def test_local_only_skips_several_repo_types() -> None:
    paper = pc.test_paper()
    paper.url = pd.DataFrame(
        {
            "href": [
                "osf.io/629bx",
                "github.com/scienceverse/demo",
                "https://researchbox.org/4377",
            ],
            "text_id": [1, 2, 3],
        }
    )
    mo = pc.module_run(paper, "repo_check", local_only=True)
    assert mo.traffic_light == "na"
    assert mo.summary_table["repo_n"].tolist() == [0]


# -- beyond the testthat file ----------------------------------------------------------------------


def test_bibr12_paper_with_osf_project() -> None:
    """A native bibr export schema 12.0 paper (pytacheck's default format)."""
    from metacheck.io.bibr12 import is_bibr12

    paper = pc.read(BIBR12 / "preprint.json")
    assert is_bibr12(paper)
    mo = run(paper)
    assert mo.summary_table["paper_id"].tolist() == [paper.paper_id]
    assert mo.summary_table["repo_n"].tolist() == [1]
    assert set(mo.table["repo_url"]) == {"https://osf.io/n9uvj"}
    assert set(mo.table["paper_id"]) == {paper.paper_id}
    assert len(mo.table) == 4


def test_bibr12_paper_list_with_local_folder() -> None:
    papers = pc.read([BIBR12 / f for f in ("preprint.json", "probe_docx.json", "full.json")])
    mo = run(papers, local_path=str(FIXTURES / "tidy"))
    st = mo.summary_table
    assert st["paper_id"].tolist() == papers.names
    # the local folder is attributed to the first paper of the list
    assert st["repo_n"].tolist() == [2, 1, 0]
    assert st["files_n"].tolist() == [7, 0, 0]
    # "osf.io/abcd" is not a valid OSF id: its repository is kept and flagged
    # (R drops it silently beside the valid OSF link, U122)
    gated = mo["gated_repos"]
    assert gated["repo_url"].tolist() == ["https://osf.io/abcd"]
    assert gated["repo_error"].tolist() == ["not a valid OSF link"]


def test_green_tidy_folder() -> None:
    mo = pc.module_run(pc.test_paper(), "repo_check", local_path=str(FIXTURES / "tidy"))
    assert mo.traffic_light == "green"
    assert mo.summary_text == (
        "\n-  We found 3 files in 1 repository."
        "\n-  We found 1 README file and 0 repositories without READMEs."
    )


def test_messy_folder_reports() -> None:
    text = ["In Study 1 participants rated faces.", "Study 2 replicated it."]
    mo = pc.module_run(tp([], "p_messy", text), "repo_check", local_path=str(FIXTURES / "messy"))
    assert mo.traffic_light == "yellow"
    report = "\n".join(b for b in mo.report if isinstance(b, str))
    for heading in (
        "#### README Files",
        "#### Proprietary E-Prime Files",
        "#### Unclassified Files",
        "#### Study/Repository Mismatch",
        "#### File Naming",
        "#### File Classification",
    ):
        assert heading in report
    assert "the repository separates out a study (ex3) not named in the manuscript" in report
    # the .txt export covers task.edat but not stimuli.edat2 / merged.emrg2
    assert "2 of them have no matching plain-text export" in report
    row = summary_row(mo)
    assert row["roster_mismatch"] is True
    assert row["files_unknown"] == 1
    assert row["naming_issues"] > 0
    naming = mo["naming_issues"]
    assert set(naming["paper_id"]) == {"p_messy"}
    # root-level readme/licence files are never assigned a study
    lic = mo.table[mo.table["file_name"] == "LICENSE.txt"]
    assert lic["group"].isna().all()


def test_restricted_and_closed_sources() -> None:
    urls = [
        "https://bonndoc.ulb.uni-bonn.de/xmlui/handle/20.500.11811/5678",
        "https://osf.io/regc1",
        "https://osf.io/regc2",
        "https://osf.io/privn",
    ]
    mo = run(tp(urls, "p_closed"))
    errors = dict(zip(mo["gated_repos"]["repo_url"], mo["gated_repos"]["repo_error"], strict=True))
    assert errors["https://bonndoc.ulb.uni-bonn.de/xmlui/handle/20.500.11811/5678"] == (
        "restricted access"
    )
    assert errors["https://osf.io/parc1"] == (
        "closed registration source (files retrieved from registration: https://osf.io/regc1)"
    )
    assert errors["https://osf.io/parc2"] == "closed registration source"
    assert errors["https://osf.io/privn"] == "private"
    # the mirrored files are listed under the closed project's URL, not the registration's
    assert "https://osf.io/parc1" in set(mo.table["repo_url"])
    assert "https://osf.io/regc1" not in set(mo.table["repo_url"])
    assert mo.traffic_light == "yellow"
    assert "Registration Points to a Closed Project" in "\n".join(
        b for b in mo.report if isinstance(b, str)
    )


def test_platform_listing_and_metadata() -> None:
    mo = run(
        tp(
            [
                "https://github.com/gzorg/treerepo",
                "https://zenodo.org/records/5550001",
                "https://doi.org/10.5061/dryad.j1fd7",
            ],
            "p_meta",
        )
    )
    meta = mo["repo_metadata"]
    assert list(meta.columns) == ["repo_url", "doi", "license", "paper_id"]
    lic = dict(zip(meta["repo_url"], meta["license"], strict=True))
    assert lic["https://github.com/gzorg/treerepo"] == "MIT"
    assert set(meta["paper_id"]) == {"p_meta"}


def test_zip_peek_replaces_the_archive_row() -> None:
    mo = run(tp(["https://zenodo.org/records/5559001"], "p_zip"))
    t = mo.table
    members = t[t["archive_url"].notna()]
    assert set(members["archive_url"]) == {"https://mock.example.org/zips/stimuli.zip"}
    assert members["file_url"].isna().all()
    assert all(p.startswith("stimuli.zip/") for p in members["file_path"])
    # the non-zip ".zip" could not be read: it stays one archive row
    assert "notzip.zip" in t["file_name"].tolist()
    no_peek = run(tp(["https://zenodo.org/records/5559001"], "p_zip"), peek_zips=False)
    assert "archive_url" not in no_peek.table.columns
    assert "stimuli.zip" in no_peek.table["file_name"].tolist()


def test_zip_peek_gets_cache_and_skip_on_api_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    # metacheck bec46c66: repo_check() forwards cache and skip_on_api_limit
    # to zip_peek()
    from metacheck.archives import zip_peek as zp

    seen: list[dict[str, object]] = []

    def fake(url: str, *args: object, **kwargs: object) -> None:
        seen.append({"url": url, **kwargs})
        return None

    monkeypatch.setattr(zp, "zip_peek", fake)
    paper = tp(["https://zenodo.org/records/5559001"], "p_zip")
    run(paper, skip_on_api_limit=True)
    assert seen and all(s["cache"] is False and s["skip_on_api_limit"] is True for s in seen), seen
    seen.clear()
    run(paper)
    assert seen and all(s["skip_on_api_limit"] is False for s in seen)


def test_zip_peek_reads_a_zip_on_disk_and_carries_the_archive_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # metacheck d52b3fd: a zip inside a ResearchBox has the box's web page as file_url
    # and its unpacked copy as file_location; it is listed from the file (the page
    # answers a range request with 406), and the listed size reaches zip_peek()
    import zipfile

    from metacheck.archives import zip_peek as zp
    from metacheck.modules.repo_check import _peek_zips

    path = tmp_path / "inner.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("data/study.csv", "id,x\n1,2\n")
        zf.writestr("analysis.R", "x <- 1\n")
    files = pd.DataFrame(
        {
            "repo_url": ["https://researchbox.org/123", "https://zenodo.org/records/1"],
            "file_name": ["inner.zip", "remote.zip"],
            "file_path": ["inner.zip", "remote.zip"],
            "file_url": ["https://researchbox.org/123", "https://zenodo.org/r/remote.zip"],
            "file_location": [str(path), None],
            "file_size": [float(path.stat().st_size), 999.0],
            "file_type": ["archive", "archive"],
        }
    )
    real = zp.zip_peek
    seen: list[tuple[str, object]] = []

    def peek(url: str, *args: object, size: object = None, **kwargs: object) -> object:
        seen.append((url, size))
        return real(url, size=size) if url == str(path) else None

    monkeypatch.setattr(zp, "zip_peek", peek)
    out, listed_tars = _peek_zips(files)
    assert listed_tars == []
    assert seen == [
        (str(path), float(path.stat().st_size)),
        ("https://zenodo.org/r/remote.zip", 999.0),
    ]
    members = out[out["archive_url"].notna()]
    assert sorted(members["archive_member"]) == ["analysis.R", "data/study.csv"]
    assert set(members["archive_url"]) == {str(path)}
    assert set(members["archive_size"]) == {float(path.stat().st_size)}
    # a member has no copy of its own yet: the download step extracts it
    assert members["file_location"].isna().all()
    assert "remote.zip" in out["file_name"].tolist()  # not listable: kept as it was


def test_a_zip_inside_a_researchbox_is_listed_from_its_local_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # test-module-repo_check.R (metacheck d52b3fd): the box's web page is the
    # row's file_url and answers a range request with 406; the unpacked copy is read
    import zipfile

    from metacheck.archives import researchbox

    inner = tmp_path / "inner.zip"
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("Data/study1.csv", "id,x\n1,2\n")
        zf.writestr("analysis.R", "x <- 1\n")
    box = "https://researchbox.org/99999"
    monkeypatch.setattr(
        researchbox,
        "rbox_file_download",
        lambda rb_url, pb=None, **kwargs: pd.DataFrame(
            {
                "rb_url": [box],
                "name": ["ResearchBox 99999/inner.zip"],
                "file_location": [str(inner)],
                "size": [float(inner.stat().st_size)],
                "type": ["archive"],
                "isdir": [False],
            }
        ),
    )
    mo = run(tp([box], "p_rbox_zip"))  # an unmocked request would fail the test
    assert sorted(mo.table["file_name"]) == ["analysis.R", "study1.csv"]
    assert set(mo.table["archive_url"]) == {str(inner)}
    assert mo.table["file_location"].isna().all()


def test_nameless_file_is_listed_without_a_naming_check() -> None:
    # a listed file without a name has no name to check (R's
    # check_file_naming() fails and the module errors, U122)
    mo = run(tp(["https://zenodo.org/records/5559002"], "p_nameless"))
    assert mo.table["file_name"].isna().tolist() == [False, True]
    assert mo.summary_table["files_unknown"].tolist() == [1]
    assert mo.summary_table["naming_issues"].tolist() == [0]


def test_invalid_osf_link_is_flagged() -> None:
    # U122: an OSF link OSF does not know is kept and flagged, alone or beside
    # a valid one (R: a dplyr "'match' requires vector arguments" error, or
    # dropped silently)
    for urls, n in (
        (["https://osf.io/abc"], 1),
        (["https://osf.io/abc", "https://osf.io/629bx"], 2),
    ):
        mo = run(tp(urls, "p_bad"))
        assert mo.summary_table["repo_n"].tolist() == [n]
        gated = mo["gated_repos"]
        assert gated["repo_url"].tolist() == ["https://osf.io/abc"]
        assert gated["repo_error"].tolist() == ["not a valid OSF link"]


def test_failed_registration_source_keeps_the_registration() -> None:
    # U122: following a registration to its source project fails (the source
    # lookup is not recorded): the registration is reported with the files
    # listed for it and the error, not "no repositories found"
    mo = run(tp(["https://osf.io/jqkg7"], "p_reg"))
    assert mo.traffic_light == "yellow"
    assert set(mo.table["repo_url"]) == {"https://osf.io/jqkg7"}
    assert mo.summary_table["files_n"].tolist() == [11]
    assert mo["gated_repos"]["repo_url"].tolist() == ["https://osf.io/jqkg7"]


def test_failed_registration_source_restores_only_registrations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # U122: when following a registration fails, the registration comes back
    # with the error; a link removed as no storage location at all (a user
    # page) stays removed
    from metacheck.modules import _repo_check as rc

    reg, user = "https://osf.io/reg12", "https://osf.io/usr12"
    listing = pd.DataFrame(
        {
            "osf_url": [reg, user],
            "osf_type": ["registrations", "users"],
            "parent": ["par12", None],
            "repo_name": [reg, user],
        }
    )

    def fake_listing(urls: list[str], pb: object, cache: bool) -> pd.DataFrame:
        if urls == [reg, user]:
            return listing
        raise RuntimeError("source lookup failed")

    monkeypatch.setattr(rc, "_osf_listing", fake_listing)
    repos = rc.Repos(rc.repo_rows("p1", [reg, user], "osf", rc.NA_SCALAR))
    rc.list_osf(repos, [reg, user], "p1", None, False)
    assert repos.df["repo_url"].tolist() == [reg]
    assert repos.df["repo_error"].tolist() == ["source lookup failed"]


def test_private_project_read_through_a_view_only_link_is_named_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # metacheck 0.4.3.15 (8dn4g_v1): the paper links osf.io/j9sdn plainly and with the
    # view-only key; the plain link is not "could not check ... (private)"
    from metacheck.modules import _repo_check as rc

    key = "0123456789abcdef0123456789abcdef"
    vo = f"https://osf.io/j9sdn/overview?view_only={key}"
    plain = "https://osf.io/j9sdn"
    state = {"public": False}

    def info(url: str) -> pd.DataFrame:
        if "view_only=" in url:
            return pd.DataFrame(
                {
                    "osf_url": [url, url],
                    "osf_id": [f"j9sdn?view_only={key}"] * 2,
                    "osf_type": ["nodes", "files"],
                    "parent": [None, None],
                    "kind": [None, "file"],
                    "public": pd.array([state["public"], None], dtype="boolean"),
                    "name": ["A study", "data.csv"],
                    "path": [None, "/data.csv"],
                    "download_url": [None, "https://osf.io/download/abcde"],
                    "size": [None, 10],
                    "filetype": [None, "data"],
                    "provider": [None, "osfstorage"],
                }
            )
        return pd.DataFrame(
            {
                "osf_url": [url],
                "osf_id": ["j9sdn"],
                "osf_type": ["private"],
                "public": pd.array([False], dtype="boolean"),
            }
        )

    def fake_listing(urls: list[str], pb: object, cache: bool) -> pd.DataFrame:
        parts = []
        for u in urls:
            f = info(u)
            f["repo_name"] = u
            parts.append(f)
        return pd.concat(parts, ignore_index=True)

    monkeypatch.setattr(rc, "_osf_listing", fake_listing)
    for both in (True, False):
        paper = pc.test_paper(
            text="Data: https://osf.io/j9sdn" if both else "Data: see the link.", url=vo
        )
        mo = run(paper, peek_zips=False)
        assert len(mo["gated_repos"]) == 0, both
        st = summary_row(mo)
        assert st["repo_n"] == 1 and st["repo_unchecked"] == 0 and st["repo_view_only"] == 1
        assert mo["view_only"]["project"].tolist() == [plain]
        assert (
            f"The OSF project {plain} is private. Metacheck examined the files through the "
            "view_only link."
        ) in mo.summary_text
        assert (
            "Private repositories checked through a view-only or peer-review link"
            in pc.module_report(mo)
        )
        assert mo.traffic_light == "yellow"
    # a public project read through a view-only link is not named
    state["public"] = True
    mo = run(pc.test_paper(text="Data: see the link.", url=vo), peek_zips=False)
    assert len(mo["view_only"]) == 0
    assert "view_only link" not in mo.summary_text


def test_private_osf_file_rows_are_excluded_per_row() -> None:
    # U123: a file marked private (public FALSE) is left out wherever it sits
    # in the listing (R's !isFALSE(public) tests the whole column at once)
    from metacheck.modules._repo_check import _osf_files

    info = pd.DataFrame(
        {
            "kind": ["file", "file", "folder", "file"],
            "public": [True, False, None, None],
            "name": ["a", "private", "dir", "b"],
        }
    )
    assert _osf_files(info)["name"].tolist() == ["a", "b"]
    assert len(_osf_files(pd.DataFrame({"kind": ["file"], "public": [False]}))) == 0


def test_naming_table_shows_display_severities(tmp_path: Path) -> None:
    # metacheck 8c1f6c1: the report's naming table says "Required"/"Suggestion";
    # naming_issues keeps "bad"/"suggestion", which the counts use. An 8-digit
    # run that is not a date is not reported (the date-format rule is gone).
    from metacheck.report import ReportTable

    for name in ("my data.csv", "data_2.csv", "data_11.csv", "id_1234567890123456.csv"):
        (tmp_path / name).write_text("x\n", encoding="utf-8")
    mo = pc.module_run(pc.test_paper(), "repo_check", local_path=str(tmp_path))
    tables = [b.data for b in mo.report if isinstance(b, ReportTable)]
    naming = next(t for t in tables if list(t.columns) == ["File", "Rule", "Severity", "Detail"])
    shown = dict(zip(naming["Rule"], naming["Severity"], strict=True))
    assert shown["spaces"] == "Required"
    assert shown["zero-padding"] == "Suggestion"
    assert "date-format" not in shown
    assert "We found 1 naming problem that should be fixed" in "\n".join(
        b for b in mo.report if isinstance(b, str)
    )


def test_local_archives_are_counted(tmp_path: Path) -> None:
    # U123: a local file file_category() cannot place takes its type from the
    # extension, as an online file does: a local .tar.gz/.zip is an archive.
    # file_types has one type per extension since metacheck d52b3fd, so .json
    # is data and .html web (D89); neither is counted as code.
    names = ("results.tar.gz", "bundle.zip", "notes.txt", "analysis.R", "report.html", "cfg.json")
    for name in names:
        (tmp_path / name).write_text("x\n", encoding="utf-8")
    mo = pc.module_run(pc.test_paper(), "repo_check", local_path=str(tmp_path))
    types = dict(zip(mo.table["file_name"], mo.table["file_type"], strict=True))
    assert {k: None if pd.isna(v) else v for k, v in types.items()} == {
        "analysis.R": "code",
        "bundle.zip": "archive",
        "cfg.json": "data",
        "notes.txt": "text",
        "report.html": "web",
        "results.tar.gz": "archive",
    }
    assert mo.summary_table["files_zip"].tolist() == [2]
    assert mo.summary_table["files_code"].tolist() == [1]


def test_does_not_mutate_paper() -> None:
    paper = tp(["https://osf.io/629bx"], "p_mut")
    before = paper.url.copy()
    run(paper, local_path=str(FIXTURES / "tidy"))
    pd.testing.assert_frame_equal(paper.url, before)


def test_format_object_size() -> None:
    from metacheck.modules._repo_check import format_object_size, size_label

    cases = {
        0: "0 B",
        1: "1 B",
        999: "999 B",
        1000: "1 kB",
        1234: "1.2 kB",
        99999: "100 kB",
        999999: "1000 kB",
        1e6: "1 MB",
        1.5e9: "1.5 GB",
        6410: "6.4 kB",
        31324: "31.3 kB",
        1e15: "1 PB",
        999.95: "1000 B",
        1e21: "1 ZB",
        1e30: "1 QB",
    }
    for x, expected in cases.items():
        assert format_object_size(x) == expected, x
    assert size_label(float("nan")) == "—"
    assert size_label(-1) == "—"
    assert size_label(float("inf")) == "—"


def test_unrecorded_request_is_a_listing_error() -> None:
    """An unrecorded request fails like httptest2's (never the network): the
    OSF block's error handler flags the repository with the message."""
    mo = run(tp(["https://osf.io/zzzzq"], "p_unrec"))
    errors = mo["gated_repos"]["repo_error"]
    assert len(errors) == 1
    assert errors.iloc[0].startswith("An unexpected request was made:\nGET ")


def test_dryad_rows_carry_the_dataset_size(monkeypatch: pytest.MonkeyPatch) -> None:
    # metacheck d52b3fd: Dryad's own total (storageSize) is carried as
    # repo_size, which download_repo_files() sizes the dataset's zip with
    from metacheck.archives import dryad

    real = dryad.dryad_info

    def with_size(*args: object, **kwargs: object) -> pd.DataFrame:
        info = real(*args, **kwargs)  # type: ignore[arg-type]
        info["storage_size"] = 123456.0
        return info

    monkeypatch.setattr(dryad, "dryad_info", with_size)
    mo = run(
        tp(["https://doi.org/10.5061/dryad.j1fd7", "https://zenodo.org/records/5550001"], "p_size")
    )
    t = mo.table
    is_dryad = t["repo_url"] == "https://doi.org/10.5061/dryad.j1fd7"
    assert is_dryad.any() and (~is_dryad).any()
    assert (t.loc[is_dryad, "repo_size"] == 123456.0).all()
    assert t.loc[~is_dryad, "repo_size"].isna().all()


# -- private ResearchBox boxes (metacheck 0.4.3.23, #621) ------------------------------------

_BOX_NOT_PUBLIC = (
    "HTTP 404: This box either does not exist or is not public; web page: "
    "not public: ResearchBox asks for a captcha instead of showing the box; "
    "a peer-review link with its passcode is needed"
)


def _mock_box(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status: str | None, license_: str | None
) -> None:
    from metacheck.archives import researchbox

    csv = tmp_path / "study1.csv"
    csv.write_text("id,x\n1,2\n")
    monkeypatch.setattr(
        researchbox,
        "rbox_file_download",
        lambda rb_url, pb=None, **kwargs: pd.DataFrame(
            {
                "rb_url": list(rb_url) if isinstance(rb_url, list) else [rb_url],
                "name": ["Data/study1.csv"],
                "file_location": [str(csv)],
                "size": [9.0],
                "type": ["data"],
                "isdir": [False],
            }
        ),
    )
    monkeypatch.setattr(
        researchbox,
        "_rbox_box_meta",
        lambda rb_url, cache=False: {"RB_license": license_, "RB_status": status},
    )


def test_a_box_readers_cannot_open_is_advised_on(monkeypatch: pytest.MonkeyPatch) -> None:
    from metacheck.archives import researchbox

    box = "https://researchbox.org/8285"

    def no_meta(*args: object, **kwargs: object) -> dict[str, str | None]:
        raise AssertionError("no licence request expected")

    monkeypatch.setattr(researchbox, "_rbox_box_meta", no_meta)
    for err in (
        _BOX_NOT_PUBLIC,
        "not public: ResearchBox asks for a captcha instead of showing the box",
    ):
        monkeypatch.setattr(
            researchbox,
            "rbox_file_download",
            lambda rb_url, pb=None, err=err, **kwargs: researchbox._rbox_no_files(
                researchbox._failed_frame([{"rb_url": rb_url[0], "error": err}])
            ),
        )
        mo = run(tp([box], "p_rbox_np"))
        assert (
            "The ResearchBox box https://researchbox.org/8285 does not exist or is not public, so "
            "readers who follow the link cannot open it. As the preprint is public, consider "
            "making the box public. If the box has to stay private for now, link to its full "
            "peer-review link (https://researchbox.org/8285&PEER_REVIEW_passcode=...), so that "
            "the link itself opens the box."
        ) in mo.summary_text
        assert "Repositories that readers cannot open" in "\n".join(map(str, mo.report))
        # still a repository that could not be checked
        assert mo.summary_table["repo_unchecked"].tolist() == [1]
        assert mo.summary_table["repo_view_only"].tolist() == [0]
        assert mo.traffic_light == "yellow"

    # not for a box that was not served for another reason, nor for a link with its passcode
    for url, err in (
        (box, "API rate limit exhausted: try later"),
        (box + "&PEER_REVIEW_passcode=NNONJR", _BOX_NOT_PUBLIC),
    ):
        monkeypatch.setattr(
            researchbox,
            "rbox_file_download",
            lambda rb_url, pb=None, err=err, **kwargs: researchbox._rbox_no_files(
                researchbox._failed_frame([{"rb_url": rb_url[0], "error": err}])
            ),
        )
        mo = run(tp([url], "p_rbox_np2"))
        assert "We could not check 1 repository" in mo.summary_text
        assert "readers who follow the link cannot open it" not in mo.summary_text


def test_a_public_box_read_through_its_plain_link_is_not_private(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    box = "https://researchbox.org/99999"
    _mock_box(monkeypatch, tmp_path, "public", "CC-BY-4.0")
    mo = run(tp([box], "p_rbox_pub"), peek_zips=False)
    assert mo["repo_metadata"]["license"].tolist() == ["CC-BY-4.0"]
    assert len(mo["view_only"]) == 0
    assert mo.summary_table["repo_view_only"].tolist() == [0]


@pytest.mark.parametrize("status", ["private", None, "public"])
def test_a_private_box_read_through_its_peer_review_link_is_named(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status: str | None
) -> None:
    link = "https://researchbox.org/8285&PEER_REVIEW_passcode=NNONJR"
    _mock_box(monkeypatch, tmp_path, status, "CC-BY-4.0")
    mo = run(tp([link], "p_rbox_vo"), peek_zips=False)
    assert mo.summary_table["files_n"].tolist() == [1]
    if status == "public":
        # the box is public: the passcode in the link does no harm
        assert len(mo["view_only"]) == 0
        assert mo.summary_table["repo_view_only"].tolist() == [0]
        assert "is private" not in mo.summary_text
    else:
        # private, or not known (no ResearchBox key): the passcode shows it is a link meant for
        # peer review
        assert mo["view_only"]["project"].tolist() == ["https://researchbox.org/8285"]
        assert mo.summary_table["repo_view_only"].tolist() == [1]
        assert (
            "The ResearchBox box https://researchbox.org/8285 is private. Metacheck examined the "
            "files through the peer-review link. As the preprint is public, consider making the "
            "box public."
        ) in mo.summary_text
        assert "Private repositories checked through a view-only or peer-review link" in "\n".join(
            map(str, mo.report)
        )
        assert mo.traffic_light == "yellow"


# -- publications are not deposits (metacheck 0.4.4.1, #636, #637) ---------------------------


def _fake_zenodo(
    monkeypatch: pytest.MonkeyPatch, records: dict[str, tuple[str | None, list[str]]]
) -> None:
    """Stand in for ``zenodo_info()``: ``{url: (resource_type, file names)}``."""
    from metacheck.archives import zenodo

    def info(urls: object, pb: object = None, cache: bool = False) -> pd.DataFrame:
        rows = []
        for u in urls:  # type: ignore[attr-defined]
            rtype, names = records[u]
            rows.append(
                {
                    "zenodo_url": u,
                    "zenodo_id": u.rsplit("/", 1)[-1],
                    "title": "t",
                    "doi": None,
                    "license": None,
                    "error": None,
                    "resource_type": rtype,
                    "files": [
                        {"key": n, "size": 10, "links": {"self": f"https://zenodo.org/f/{n}"}}
                        for n in names
                    ],
                }
            )
        return pd.DataFrame(rows)

    monkeypatch.setattr(zenodo, "zenodo_info", info)


def test_a_publication_without_data_is_not_a_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://zenodo.org/records/9000001"
    _fake_zenodo(monkeypatch, {url: ("publication", ["paper.pdf"])})
    mo = run(pc.test_paper(url=url))
    assert mo.summary_text == NO_REPOS
    assert mo.traffic_light == "na"


def test_a_publication_with_its_data_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://zenodo.org/records/9000002"
    _fake_zenodo(monkeypatch, {url: ("publication-preprint", ["paper.pdf", "data.csv"])})
    mo = run(pc.test_paper(url=url))
    assert mo.summary_table["repo_n"].tolist() == [1]
    assert set(mo.table["file_name"]) == {"paper.pdf", "data.csv"}


def test_a_dataset_is_kept_without_data_files(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://zenodo.org/records/9000003"
    _fake_zenodo(monkeypatch, {url: ("dataset", ["readme.txt"])})
    mo = run(pc.test_paper(url=url))
    assert mo.summary_table["repo_n"].tolist() == [1]


def test_a_record_without_a_type_is_ignored_only_when_it_lists_files_but_no_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    only_pdf = "https://zenodo.org/records/9000004"
    empty = "https://zenodo.org/records/9000005"
    with_data = "https://zenodo.org/records/9000006"
    _fake_zenodo(
        monkeypatch,
        {only_pdf: (None, ["paper.pdf"]), empty: (None, []), with_data: (None, ["d.csv"])},
    )
    mo = run(pc.test_paper(["See " + " ".join([only_pdf, empty, with_data])]))
    assert set(mo.table["repo_url"]) == {with_data}
    # the pdf-only record is gone from the metadata too; the empty one is kept
    assert set(mo["repo_metadata"]["repo_url"]) == {empty, with_data}
