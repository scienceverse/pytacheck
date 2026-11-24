"""data_check, zip-peek, file tree, file naming, codebook_check and psychds_check synchronised with
metacheck 0.4.4.30 to 0.4.4.35 (#695, #700, #702, #703, #706).

Mirrors the new tests of tests/testthat/test-data-checks.R, test-zip-peek.R, test-file-tree.R,
test-file_category.R, test-keys.R, test-module-codebook_check.R and test-psychds-file-plan.R.
"""

from __future__ import annotations

import math
import time
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import metacheck as pc
from metacheck.archives.zip_peek import _archive_keep, _expand_zip, _unzip_failed_text
from metacheck.datacheck.files import _data_group_from_path, data_read_head
from metacheck.fileinfo.naming import check_file_naming
from metacheck.module import ModuleOutput, module_run
from metacheck.modules import _codebook as cbc
from metacheck.modules._count_refused import (
    _count_refused_add,
    _count_refused_note,
    _repo_count_message,
    _repos_over_count,
)
from metacheck.report.file_tree import (
    _FILE_TREE_MAX_LINES,
    _path_ancestors,
    file_tree_block,
)
from metacheck.utils import _lfs_pointer_size, _lfs_pointer_text, local_options


def _run_local(d: Path, module: str = "data_check", **kw: Any) -> Any:
    with local_options({"metacheck.llm.use": False}):
        return module_run(pc.demopaper(), module, local_path=str(d), local_only=True, **kw)


# -- #703: an archive that cannot be opened ----------------------------------------------------


def test_data_check_finishes_when_the_only_file_is_an_archive_that_cannot_be_opened(
    tmp_path: Path,
) -> None:
    d = tmp_path / "d"
    d.mkdir()
    full = tmp_path / "full.zip"
    with zipfile.ZipFile(full, "w") as z:
        z.writestr("a.csv", ",".join(str(i) for i in range(1, 5001)))
    # cut off: no central directory, as the zip of 10.5061/dryad.2959s
    (d / "data and scripts.zip").write_bytes(full.read_bytes()[:300])
    mo = _run_local(d)
    assert len(mo.table) == 0
    assert mo.traffic_light == "yellow"
    assert "1 archive in the repository could not be opened" in mo.summary_text
    assert "We found no files to analyse." in mo.summary_text
    assert any(
        "`data and scripts.zip` could not be opened (it is damaged or incomplete)" in str(r)
        for r in mo.report
    )
    assert len(mo.summary_table) == 1


def test_an_archive_that_cannot_be_opened_is_named_by_unzip_failed_text(tmp_path: Path) -> None:
    bad = tmp_path / "bad.zip"
    bad.write_bytes(bytes([0x50, 0x4B, 0x03, 0x04] + [0] * 40))
    row = pd.DataFrame({"file_name": ["bad.zip"], "file_path": ["bad.zip"], "repo_url": ["r"]})
    out = _expand_zip(str(bad), row)
    assert len(out) == 0
    uf = out.attrs["unpack_failed"]
    assert uf["archive"].tolist() == ["bad.zip"]
    assert pd.isna(uf["file_name"].iloc[0])
    both = pd.concat(
        [
            uf,
            pd.DataFrame({"archive": ["b.zip"], "file_name": ["x.csv"], "reason": ["encrypted"]}),
        ],
        ignore_index=True,
    )
    txt = _unzip_failed_text(both)
    assert txt["summary"].startswith(
        "1 archive in the repository could not be opened, so the checks did not see its files; "
        "1 file in the repository's archives could not be unpacked"
    )
    assert "- `bad.zip` could not be opened (it is damaged or incomplete)." in txt["report"]
    assert _unzip_failed_text(None)["report"] is None
    assert _unzip_failed_text(uf.iloc[0:0])["summary"] is None


# -- #702: files per repository ---------------------------------------------------------------


def test_data_check_refuses_a_repository_over_max_files_per_repo(tmp_path: Path) -> None:
    d = tmp_path / "d"
    d.mkdir()
    for i in range(1, 6):
        (d / f"d{i}.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (d / "a.R").write_text("x <- 1\n", encoding="utf-8")
    dc = _run_local(d, max_files_per_repo=3)
    assert len(dc.table) == 0
    assert dc.traffic_light == "yellow"
    assert "holds 6 files, exceeding the 3-file cap" in dc.summary_text
    assert len(dc.count_refused) == 1
    # under the default (100,000) nothing is refused
    dc = _run_local(d)
    assert len(dc.table) > 0
    assert len(dc.count_refused) == 0
    assert len(_repos_over_count(pd.DataFrame({"repo_url": ["a", "a", "b"]}), 1)) == 1
    assert len(_repos_over_count(pd.DataFrame({"repo_url": ["a"]}), math.inf)) == 0


def _refused_chain() -> tuple[Any, pd.DataFrame]:
    p = pc.demopaper()
    repo = "https://doi.org/10.5281/zenodo.3826426"
    msg = _repo_count_message(repo, 369548, 100000)
    refused = pd.DataFrame({"repo_url": [repo], "n_files": [369548], "message": [msg]})
    none = pd.DataFrame(
        {
            c: pd.Series([], dtype="string")
            for c in (
                "paper_id",
                "file_name",
                "file_path",
                "repo_url",
                "file_url",
                "file_location",
            )
        }
    )
    none["file_size"] = pd.Series([], dtype="float64")
    dc = ModuleOutput(
        module="data_check",
        title="Data Check",
        section="results",
        table=none,
        traffic_light="yellow",
        summary_table=pd.DataFrame({"paper_id": [p.paper_id]}),
        paper=p,
        extras={"structure": none, "count_refused": refused},
    )
    return dc, refused


def test_the_checks_after_data_check_name_a_repository_it_refused() -> None:
    dc, refused = _refused_chain()
    with local_options({"metacheck.llm.use": False}):
        cb = module_run(dc, "codebook_check")
        ps = module_run(dc, "psychds_check")
    for out in (cb, ps):
        assert out.traffic_light == "yellow"
        assert "holds 369548 files, exceeding the 100000-file cap" in out.summary_text
    # nothing refused: as before
    assert _count_refused_note("x", refused.iloc[0:0]) is None
    added = _count_refused_add(refused, refused)
    assert added is not None and len(added) == 1
    none = _count_refused_add(refused.iloc[0:0], None)
    assert none is not None and len(none) == 0


# -- study groups, trailing blank columns --------------------------------------------------------


def test_data_group_from_path_reads_only_paths_that_can_hold_a_study_label() -> None:
    p = [
        "Study 1/data.csv",
        "pilot/x.csv",
        "Pilot Study 1A/a.csv",
        "exp2/b.csv",
        "text1.csv",
        "study2_2019.csv",
        "a/b/c",
        None,
        "",
        "index2.csv",
        "Study 1/data.csv",
    ]
    assert _data_group_from_path(p) == [
        "ex1",
        "pilot1",
        "pilot1a",
        "ex2",
        None,
        "ex2",
        None,
        None,
        None,
        None,
        "ex1",
    ]
    assert _data_group_from_path([]) == []


def test_unnamed_empty_columns_at_the_right_end_of_a_table_are_left_out(tmp_path: Path) -> None:
    from metacheck.datacheck.files import _drop_trailing_blank_cols

    d = pd.DataFrame(
        {
            "a": [1, 2, 3],
            "b": ["x", None, ""],
            "V3": [None, None, None],
            "V4": ["", " ", None],
        }
    )
    out = _drop_trailing_blank_cols(d)
    assert list(out.columns) == ["a", "b"]
    assert out.attrs["blank_cols_dropped"] == 2
    # one with a name or a value stops it; so does a column in the middle
    mid = pd.DataFrame({"a": [1, 2, 3], "V2": [None] * 3, "keep": [1, 2, 3], "V4": [None] * 3})
    assert list(_drop_trailing_blank_cols(mid).columns) == ["a", "V2", "keep"]
    named = pd.DataFrame({"a": [1, 2, 3], "named_empty": [None] * 3})
    assert list(_drop_trailing_blank_cols(named).columns) == ["a", "named_empty"]
    # read from a file, as a spreadsheet export with every column gives it
    f = tmp_path / "wide.csv"
    pad = "," * 50
    f.write_text(f"id,score{pad}\n1,3{pad}\n2,4{pad}\n", encoding="utf-8")
    df = data_read_head(f, n_rows=math.inf)
    assert df is not None and df.shape[1] == 2
    # the head alone cannot show a column is empty
    head = data_read_head(f, n_rows=2)
    assert head is not None and head.shape[1] > 2


# -- #700: Git LFS pointers, zips inside zips -----------------------------------------------------


def test_data_check_names_git_lfs_pointers_and_opens_zips_inside_zips(tmp_path: Path) -> None:
    d = tmp_path / "d"
    d.mkdir()
    (d / "large_data.csv").write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:ab\nsize 734003200\n",
        encoding="utf-8",
    )
    # nested.zip holds level1.csv and level1.zip, which holds level2.csv
    inner = tmp_path / "level1.zip"
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("level2.csv", "a,b\n1,2\n")
    with zipfile.ZipFile(d / "nested.zip", "w") as z:
        z.writestr("level1.csv", "c,d\n3,4\n")
        z.write(inner, "level1.zip")
    dc = _run_local(d)
    assert "1 file is a Git LFS pointer" in dc.summary_text
    assert dc.traffic_light == "yellow"
    assert {"level1.csv", "level2.csv"} <= set(dc.structure["file_name"])
    assert any("#### Files kept in Git LFS" in str(r) for r in dc.report)


def test_lfs_pointer_text_and_size(tmp_path: Path) -> None:
    f = tmp_path / "p.csv"
    f.write_text("version https://git-lfs.github.com/spec/v1\noid sha256:ab\nsize 1024\n")
    g = tmp_path / "q.csv"
    g.write_text("a,b\n1,2\n")
    assert _lfs_pointer_size([str(f)]) == [1024.0]
    assert math.isnan(_lfs_pointer_size([str(g)])[0])
    report, summary = _lfs_pointer_text(["p.csv"], [1024.0])
    assert report is not None and report[0] == "#### Files kept in Git LFS"
    assert summary is not None and summary.startswith("1 file is a Git LFS pointer")
    assert _lfs_pointer_text([], []) == (None, None)


def test_an_archive_inside_an_archive_is_kept() -> None:
    keep = _archive_keep(["a.zip", "b.tar.gz", "c.gz", "d.png", "e.csv"], "materials")
    assert keep == [True, True, True, False, True]


# -- file tree -------------------------------------------------------------------------------------


def test_the_tree_of_a_large_repository_is_drawn_quickly_and_cut() -> None:
    n = 20000
    p = [f"site{(i % 200) + 1:03d}/plot{(i % 20) + 1:02d}/day{i + 1:06d}.csv" for i in range(n)]
    files = pd.DataFrame(
        {
            "repo_url": "https://zenodo.org/records/1",
            "file_path": p,
            "file_name": [x.rsplit("/", 1)[-1] for x in p],
            "data_type": "data",
            "doc_role": None,
        }
    )
    t0 = time.time()
    block = file_tree_block(files)
    assert block is not None
    assert time.time() - t0 < 30
    box = next(b for b in block if b.startswith(":::"))
    lines = box.split("\n")
    assert sum(1 for ln in lines if not ln.startswith((":::", "```"))) == _FILE_TREE_MAX_LINES + 1
    import re

    assert re.match(r"^\(and [0-9,]+ more files and folders, not shown\)$", lines[-3])


def test_a_repository_whose_files_are_all_inside_a_junk_folder_gets_a_tree() -> None:
    junk = pd.DataFrame(
        {
            "repo_url": ["https://osf.io/abcde"] * 2,
            "file_path": ["__MACOSX/._a.csv", "__MACOSX/._b.csv"],
        }
    )
    block = file_tree_block(None, None, junk)
    assert block is not None
    import re

    assert any(re.search(r"__MACOSX/\s+junk: remove \(2 files\)", b) for b in block)


def test_path_ancestors_gives_every_folder_above_each_path() -> None:
    i, prefix = _path_ancestors(["a/b/c.csv", "x.csv", "a/", "/r/s"])
    assert i == [0, 0, 3, 3]
    assert prefix == ["a", "a/b", "", "/r"]


# -- file naming -----------------------------------------------------------------------------------


def test_check_file_naming_names_paths_that_differ_only_in_capital_letters() -> None:
    p = ["names/case/Data.csv", "names/case/data.csv", "b.csv"]
    out = check_file_naming([x.rsplit("/", 1)[-1] for x in p], p, repo="r")
    clash = out[out["rule"] == "case-clash"]
    assert clash["file_name"].tolist() == p[:2]
    assert set(clash["severity"]) == {"bad"}
    assert "differs only in capital letters from names/case/data.csv" in clash["detail"].iloc[0]
    # in two repositories they do not clash
    out = check_file_naming(["a.csv", "A.csv"], repo=["r", "s"])
    assert "case-clash" not in set(out["rule"])


# -- codebook_check, psychds_check ------------------------------------------------------------------


def test_the_models_match_to_a_codebook_variable_without_a_label_does_not_label_a_column(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from metacheck.llm import core as llm_core

    def fake_llm(*_a: Any, **_k: Any) -> Any:
        r = pd.DataFrame({"column_name": ["q1", "q2"], "codebook_variable": ["Q_one", "Q_two"]})
        r.attrs["llm"] = {"model": "fake"}
        return r

    monkeypatch.setattr(llm_core, "llm", fake_llm)
    monkeypatch.setattr(cbc, "llm", fake_llm, raising=False)
    labels_df = pd.DataFrame(
        {
            "paper_id": ["p", "p"],
            "source_file": ["d.csv", "d.csv"],
            "column_name": ["q1", "q2"],
            "label": [None, None],
            "codebook_variable": [None, None],
            "label_source": [None, None],
            "label_status": ["unlabelled", "unlabelled"],
            "label_method": [None, None],
        }
    )
    cb = pd.DataFrame(
        {
            "paper_id": ["p", "p"],
            "codebook_variable": ["Q_one", "Q_two"],
            "label": ["First question", None],
            "codebook_source": ["cb.csv", "cb.csv"],
        }
    )
    with local_options({"metacheck.llm.use": True}):
        out = cbc.codebook_match_llm(labels_df, pd.DataFrame(), cb, model="x", params={})[
            "labels_df"
        ]
    assert out["label_status"].tolist() == ["llm", "unlabelled"]
    assert out["label"].tolist()[0] == "First question"


def test_psychds_data_item_needs_a_data_file_under_data() -> None:
    p = pc.demopaper()

    def required_missing(path: str) -> float:
        s = pd.DataFrame(
            {
                "paper_id": [p.paper_id],
                "file_name": [path.rsplit("/", 1)[-1]],
                "file_path": [path],
                "data_type": ["data"],
                "doc_role": [None],
                "group": [None],
            }
        )
        fake = ModuleOutput(
            module="data_check",
            title="Data Check",
            section="results",
            table=pd.DataFrame(
                {"paper_id": [p.paper_id], "source_file": [path], "column_name": ["x"]}
            ),
            summary_table=pd.DataFrame({"paper_id": [p.paper_id]}),
            paper=p,
            extras={"structure": s},
        )
        with local_options({"metacheck.llm.use": False}):
            summary = module_run(fake, "psychds_check").summary_table
        assert summary is not None
        return float(summary["required_missing"][0])

    # dataset_description.json is missing in each, so 1 is all items but that
    assert required_missing("raw.csv") == 2
    assert required_missing("data/raw.csv") == 1
    assert required_missing("project-v1/data/raw.csv") == 1
    assert required_missing("a/b/data/raw.csv") == 2
