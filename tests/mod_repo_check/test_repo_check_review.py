"""Review tests for the repo_check module: progress output, listing internals, R quirks.

The review parity cases (``parity/cases/mod_repo_check_review.yaml``, made by
``make_review_cases.py``) compare whole module outputs with R; these tests pin
down the pieces the parity harness cannot see (progress output) and the R
behaviours worth stating explicitly.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd
import pytest

import pytacheck as pc
from pytacheck.modules import _repo_check as rc
from tests.mod_repo_check.parity_support import FIXTURES, in_root, mocked, tp

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

REVIEW = FIXTURES / "review"


class RecordingBar:
    """A stand-in for ``pytacheck.utils.pb()`` that records what is shown."""

    def __init__(self, log: list[tuple[str, Any]], total: Any, fmt: str) -> None:
        self.log = log
        log.append(("new", (total, fmt)))

    def tick(self, len: float = 1, tokens: Mapping[str, Any] | None = None) -> None:
        self.log.append(("tick", (len, dict(tokens or {}))))

    def terminate(self) -> None:
        self.log.append(("terminate", None))


@pytest.fixture
def progress(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    log: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        "pytacheck.utils.pb", lambda total, format="": RecordingBar(log, total, format)
    )
    return log


def whats(log: list[tuple[str, Any]]) -> list[str]:
    return [t[1].get("what") for kind, t in log if kind == "tick" and t[1].get("what")]


def test_progress_spinner_brackets_the_run(progress: list[tuple[str, Any]]) -> None:
    # R: pb(NA, "(:spin) :what"), "Starting Repo Check", and on.exit() "Repo Check Complete"
    with mocked():
        mo = pc.module_run(tp(["https://github.com/gzorg/bigrepo"], "p_pb"), "repo_check")
    assert mo.traffic_light == "yellow"
    assert progress[0] == ("new", (None, "(:spin) :what"))
    shown = whats(progress)
    assert shown[0] == "Starting Repo Check"
    assert shown[-1] == "Repo Check Complete"
    assert any(
        s.startswith("Skipping GitHub repo (GitHub repo tree truncated")
        and s.endswith("): https://github.com/gzorg/bigrepo")
        for s in shown
    )
    assert progress[-1] == ("terminate", None)


def test_progress_spinner_finishes_on_early_return(progress: list[tuple[str, Any]]) -> None:
    mo = pc.module_run(tp([], "p_none"), "repo_check")
    assert mo.traffic_light == "na"
    assert whats(progress) == ["Starting Repo Check", "Repo Check Complete"]
    assert progress[-1] == ("terminate", None)


def test_zip_peek_progress_bar(progress: list[tuple[str, Any]]) -> None:
    with mocked():
        mo = pc.module_run(tp(["https://zenodo.org/records/5559001"], "p_zip_pb"), "repo_check")
    assert "archive_url" in mo.table.columns
    zip_bar = [i for i, e in enumerate(progress) if e == ("new", (2, ZIP_FORMAT))]
    assert len(zip_bar) == 1
    # one tick per archive, then the bar is terminated
    after = progress[zip_bar[0] + 1 :]
    assert after[:3] == [("tick", (1, {})), ("tick", (1, {})), ("terminate", None)]


ZIP_FORMAT = "Reading zip contents [:bar] :current/:total"


def test_listing_rows_read_record_columns_once() -> None:
    info = pd.DataFrame(
        {
            "dataverse_url": ["https://dv/a", "https://dv/b"],
            "dataverse_host": ["dv.example.org", None],
            "files": [
                [
                    {"label": "a.csv", "dataFile": {"id": 7, "filesize": 10}},
                    {"label": "b.R", "dataFile": {"filesize": 3}},
                ],
                [{"label": "c.txt", "dataFile": {"id": 8}}],
            ],
        }
    )
    got = rc._file_rows(info, "dataverse_url", rc.dataverse_row)
    assert got["repo_url"].tolist() == ["https://dv/a", "https://dv/a", "https://dv/b"]
    assert got["file_url"].tolist()[0] == "https://dv.example.org/api/access/datafile/7"
    assert pd.isna(got["file_url"].tolist()[1])
    # R: sprintf() of an NA host gives "NA"
    assert got["file_url"].tolist()[2] == "https://NA/api/access/datafile/8"


def test_listing_without_its_url_column_fails_like_r() -> None:
    # R: `.zenodo_info$zenodo_url[[i]]` of a missing column is NULL[[i]]
    repos = rc.Repos(rc.repo_rows("p", ["https://zenodo.org/records/1"], "zenodo", rc.NA_SCALAR))
    info = pd.DataFrame({"files": [[{"key": "a.csv", "size": 1}]], "doi": ["x"]})
    files, meta = rc._api_listing(
        repos,
        ["https://zenodo.org/records/1"],
        lambda: info,
        "zenodo_url",
        rc.zenodo_row,
        col_chr=True,
    )
    assert repos.df["repo_error"].tolist() == ["subscript out of bounds"]
    assert list(files.columns) == ["repo_name"]
    # the metadata was extracted before the file rows failed (.col_chr(): NA url)
    assert meta["repo_url"].isna().tolist() == [True]
    assert meta["doi"].tolist() == ["x"]


def test_listing_metadata_without_doi_or_license_is_na() -> None:
    # U122: a listing without doi/license columns (a Figshare private share
    # link's) has NA metadata; R's data.frame() refuses the zero-length
    # as.character(NULL) and flags every URL of the platform, overwriting an
    # error already recorded
    url = "https://doi.org/10.5061/dryad.x"
    repos = rc.Repos(rc.repo_rows("p", [url, "https://doi.org/10.5061/dryad.y"], "dryad", "known"))
    repos.df = repos.df.assign(repo_error=pd.Series(["known", None], dtype="string"))
    info = pd.DataFrame({"dryad_url": [url], "files": [[{"path": "a.csv"}]]})
    files, meta = rc._api_listing(repos, [url], lambda: info, "dryad_url", rc.dryad_row)
    assert meta["repo_url"].tolist() == [url]
    assert meta["doi"].isna().all() and meta["license"].isna().all()
    assert files["file_name"].tolist() == ["a.csv"]
    assert repos.df["repo_error"].tolist()[0] == "known"

    # a failing block keeps an error already recorded and flags the others
    def fail() -> pd.DataFrame:
        raise RuntimeError("boom")

    urls = [url, "https://doi.org/10.5061/dryad.y"]
    rc._api_listing(repos, urls, fail, "dryad_url", rc.dryad_row)
    assert repos.df["repo_error"].tolist() == ["known", "boom"]


def test_dataone_rows_need_the_host_column() -> None:
    info = pd.DataFrame(
        {"dataone_url": ["https://knb/x"], "files": [[{"key": "a.csv", "pid": "p1"}]]}
    )
    with pytest.raises(rc.RError, match="subscript out of bounds"):
        rc._file_rows(info, "dataone_url", rc.dataone_row)


def test_registration_sources_belong_to_the_first_osf_paper() -> None:
    # R quirk: rows added for registration source projects (and orphan
    # registrations) all carry the paper_id of the first OSF row in `repos`,
    # so in a paper list another paper's registration files are credited to it
    papers = pc.PaperList(
        [
            tp(["https://github.com/gzorg/treerepo"], "p_gh_only"),
            tp(["https://osf.io/regc1"], "p_reg_b"),
            tp(["https://osf.io/regp3", "https://osf.io/regor"], "p_reg_a"),
        ]
    )
    with mocked():
        mo = pc.module_run(papers, "repo_check")
    owners = dict(zip(mo.table["repo_url"], mo.table["paper_id"], strict=True))
    assert owners["https://osf.io/parp3"] == "p_reg_b"
    assert owners["https://osf.io/regor"] == "p_reg_b"
    st = mo.summary_table.set_index("paper_id")
    assert st.loc["p_reg_a", "repo_n"] == 0
    assert st.loc["p_reg_b", "repo_n"] == 3


def test_two_local_folders_keep_their_same_named_files() -> None:
    # same-named files in two folders are distinct files: both folders are
    # listed in full (R de-duplicates on duplicated(file_url) &
    # duplicated(file_path), each on its own, and drops dup_b's README.md and
    # data.csv, and with them dup_b, U121)
    with in_root():
        mo = pc.module_run(
            tp([], "p_dup"),
            "repo_check",
            local_path=[str(REVIEW / "dup_a"), str(REVIEW / "dup_b")],
        )
    by_repo = mo.table.groupby("repo_url")["file_path"].apply(sorted).to_dict()
    assert by_repo[str(REVIEW / "dup_b")] == ["README.md", "data.csv"]
    assert {"README.md", "data.csv", "only_a.R"} <= set(by_repo[str(REVIEW / "dup_a")])
    assert mo.summary_table["repo_n"].tolist() == [2]
    # the same folder given twice is listed once
    with in_root():
        mo = pc.module_run(
            tp([], "p_dup"),
            "repo_check",
            local_path=[str(REVIEW / "dup_b"), str(REVIEW / "dup_b")],
        )
    assert sorted(mo.table["file_path"]) == ["README.md", "data.csv"]


def test_dedup_is_per_file() -> None:
    # U121: a file is dropped only when the same file (URL and path) was
    # listed before -- not when its URL and its path each occurred in other rows
    from pytacheck.modules.repo_check import _prepare_files

    files = pd.DataFrame(
        {
            "repo_url": ["r1", "r1", "r2", "r2"],
            "file_name": ["a.csv", "b.csv", "b.csv", "a.csv"],
            "file_path": ["a.csv", "b.csv", "b.csv", "a.csv"],
            "file_url": ["u1", "u2", "u2", "u2"],
            "file_location": [None, None, None, None],
        }
    ).astype({"file_location": object})
    repos = rc.Repos(rc.repo_rows("p", ["r1", "r2"], "osf", rc.NA_SCALAR))
    out, _ = _prepare_files(files, repos)
    # row 3 (u2, b.csv) repeats row 2; row 4 (u2, a.csv) is a different file
    assert list(zip(out["repo_url"], out["file_name"], strict=True)) == [
        ("r1", "a.csv"),
        ("r1", "b.csv"),
        ("r2", "a.csv"),
    ]


def test_peeked_zip_members_are_listed_but_not_readmes() -> None:
    # is_readme is decided before zips are peeked: a README inside a zip is
    # listed, typed by its extension, and not counted as a README
    with mocked():
        mo = pc.module_run(tp(["https://zenodo.org/records/5559101"], "p_zip"), "repo_check")
    t = mo.table
    inner = t[t["archive_url"].notna()]
    assert "deposit.zip/README.md" in set(inner["file_path"])
    assert "deposit.zip/Study 1/analysis.R" in set(inner["file_path"])
    assert not any(p.endswith("/") for p in inner["file_path"])
    assert mo.summary_table["files_readme"].tolist() == [0]
    assert "We found 0 README files and 1 repository without READMEs." in mo.summary_text
