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

import pytacheck as pc
from tests.mod_repo_check.parity_support import FIXTURES, ROOT, mocked, tp

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

HERE = Path(__file__).resolve().parent
CODE_FILES = ROOT / "upstream" / "metacheck" / "tests" / "testthat" / "fixtures" / "code_files"
BIBR12 = ROOT / "upstream" / "metacheck" / "tests" / "testthat" / "fixtures" / "bibr12"
NO_REPOS = (
    "We found no links to repositories on the Open Science Framework, Github, ResearchBox, "
    "DSpace, Zenodo, Dataverse, Figshare, Dryad, ReShare, 4TU.ResearchData, Mendeley Data, "
    "or DataONE."
)
SUMMARY_COLS = [
    "paper_id",
    "repo_n",
    "files_n",
    "files_data",
    "files_code",
    "files_readme",
    "files_zip",
    "files_unknown",
    "naming_issues",
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
    from pytacheck.module import module_find

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
        "files_n": 0,
        "files_data": 0,
        "files_code": 0,
        "files_readme": 0,
        "files_zip": 0,
        "files_unknown": 0,
        "naming_issues": 0,
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
        "files_n": 2,
        "files_data": 0,
        "files_code": 0,
        "files_readme": 1,
        "files_zip": 1,
        "files_unknown": 0,
        "naming_issues": 0,
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
        "files_n": 4,
        "files_data": 1,
        "files_code": 2,
        "files_readme": 0,
        "files_zip": 1,
        "files_unknown": 1,
        "naming_issues": 1,
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
    from pytacheck.archives import local

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
    from pytacheck.io.bibr12 import is_bibr12

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
    assert gated["repo_error"].tolist() == ["invalid or inaccessible OSF link"]


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
        assert gated["repo_error"].tolist() == ["invalid or inaccessible OSF link"]


def test_failed_registration_source_keeps_the_registration() -> None:
    # U122: following a registration to its source project fails (the source
    # lookup is not recorded): the registration is reported with the files
    # listed for it and the error, not "no repositories found"
    mo = run(tp(["https://osf.io/jqkg7"], "p_reg"))
    assert mo.traffic_light == "yellow"
    assert set(mo.table["repo_url"]) == {"https://osf.io/jqkg7"}
    assert mo.summary_table["files_n"].tolist() == [11]
    assert mo["gated_repos"]["repo_url"].tolist() == ["https://osf.io/jqkg7"]


def test_private_osf_file_rows_are_excluded_per_row() -> None:
    # U123: a file marked private (public FALSE) is left out wherever it sits
    # in the listing (R's !isFALSE(public) tests the whole column at once)
    from pytacheck.modules._repo_check import _osf_files

    info = pd.DataFrame(
        {
            "kind": ["file", "file", "folder", "file"],
            "public": [True, False, None, None],
            "name": ["a", "private", "dir", "b"],
        }
    )
    assert _osf_files(info)["name"].tolist() == ["a", "b"]
    assert len(_osf_files(pd.DataFrame({"kind": ["file"], "public": [False]}))) == 0


def test_local_archives_are_counted(tmp_path: Path) -> None:
    # U123: a local file file_category() cannot place takes its type from the
    # extension, as an online file does: a local .tar.gz/.zip is an archive
    for name in ("results.tar.gz", "bundle.zip", "notes.txt", "analysis.R"):
        (tmp_path / name).write_text("x\n", encoding="utf-8")
    mo = pc.module_run(pc.test_paper(), "repo_check", local_path=str(tmp_path))
    types = dict(zip(mo.table["file_name"], mo.table["file_type"], strict=True))
    assert types == {
        "analysis.R": "code",
        "bundle.zip": "archive",
        "notes.txt": "text",
        "results.tar.gz": "archive",
    }
    assert mo.summary_table["files_zip"].tolist() == [2]


def test_does_not_mutate_paper() -> None:
    paper = tp(["https://osf.io/629bx"], "p_mut")
    before = paper.url.copy()
    run(paper, local_path=str(FIXTURES / "tidy"))
    pd.testing.assert_frame_equal(paper.url, before)


def test_format_object_size() -> None:
    from pytacheck.modules._repo_check import format_object_size, size_label

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
