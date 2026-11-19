"""data_check helpers synchronised with metacheck 0.4.4.18 (R/data_check_helpers.R at 8b1d22a).

The expected values follow the R tests (``test-data-checks.R``, ``test-codebook-helpers.R``,
``test-data-read.R``); the parity cases in ``parity/cases/`` hold the values R produced.
"""

from __future__ import annotations

import math
import warnings
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from metacheck.datacheck import files as F
from metacheck.datacheck._colattrs import col_attrs_at
from metacheck.datacheck.files import data_read_head

DATA = Path(__file__).parent / "data" / "plain_table"


# ---------------------------------------------------------------------------
# data_classify_files(): shell scripts, documents, licences, code by others
# ---------------------------------------------------------------------------


def test_shell_scripts_are_code() -> None:
    names = ["run.sh", "go.bash", "x.zsh", "build.bat", "a.cmd", "setup.ps1"]
    assert F.data_classify_files(names) == ["code"] * 6


def test_a_web_page_or_markdown_file_with_data_in_its_name_is_a_document() -> None:
    assert F.data_classify_files(["data_report.html", "data_notes.md", "data.csv"]) == [
        "documentation",
        "documentation",
        "data",
    ]


def test_a_licence_that_names_what_it_covers_is_documentation() -> None:
    names = ["LICENSE_code_MIT.txt", "license_data.txt", "LICENSE-data.md", "license_code.R"]
    # only as a text document: a script keeps its type
    assert F.data_classify_files(names) == ["documentation"] * 3 + ["code"]


def test_code_in_a_release_folder_is_by_others_only_beside_code_outside_it() -> None:
    paths = ["jspsych-6.3.1/plugin.js", "analysis/run.R"]
    names = ["plugin.js", "run.R"]
    assert F.data_classify_files(names, paths) == ["materials", "code"]
    # the authors' own release alone is theirs (a repository with no code outside it)
    assert F.data_classify_files(["run.R"], ["analysis-v2.1.0/run.R"]) == ["code"]
    # one repository each: the library sits beside code in its own repository only
    out = F.data_classify_files(
        ["plugin.js", "run.R", "run2.R"],
        ["lib-1.2.3/plugin.js", "run.R", "run2.R"],
        group=["a", "b", "b"],
    )
    assert out == ["code", "code", "code"]
    assert F.data_classify_files(
        ["plugin.js", "run.R"], ["lib-1.2.3/plugin.js", "run.R"], group=["a", "a"]
    ) == ["materials", "code"]
    # data in such a folder stays data
    assert F.data_classify_files(["x.csv", "run.R"], ["lib-1.2.3/x.csv", "run.R"]) == [
        "data",
        "code",
    ]


def test_is_others_code_path_folders_and_minified_scripts() -> None:
    p = ["node_modules/a/b.js", "vendor/x.php", "ados/a.ado", "app.min.js", "src/own.js", "a.R"]
    assert F._is_others_code_path(p) == [True, True, True, True, False, False]


def test_a_package_alone_in_a_subfolder_is_the_deposit() -> None:
    # #505: a paper's own package one folder down, with nothing beside it
    paths = ["good 2/DESCRIPTION", "good 2/NAMESPACE", "good 2/R/f.R", "good 2/man/f.Rd"]
    assert F._is_r_package_file(paths) == [False] * 4
    # real data or code outside every package: the packages are left out
    more = [*paths, "analysis.R"]
    assert F._is_r_package_file(more) == [True, True, True, True, False]
    # a README (also README.Rmd, which classifies as code) is not content
    readme = [*paths, "README.Rmd", "README.md"]
    assert F._is_r_package_file(readme) == [False] * 6
    # a package at the root with nothing else around it is the deposit too
    assert F._is_r_package_file(["DESCRIPTION", "NAMESPACE", "R/a.R"]) == [False] * 3


# ---------------------------------------------------------------------------
# a study number in a path ends there (#648, K7)
# ---------------------------------------------------------------------------


def test_a_study_number_in_a_path_ends_there_and_a_pilot_study_is_a_pilot() -> None:
    p = [
        "study104/a.csv", "data/study03032026.csv", "STUDY-20-00338.pdf", "Pilot Study 1A/x.csv",
        "pilot2_data.csv", "pilot/x.csv", "Study 2/a.csv", "study2a_data.csv",
        "experiment3explicit.csv", "Exp1/raw.csv", "study2_2019.csv", "study1_2.csv",
        "index1.csv", "study1_20230512.csv", "Study2_15032021_data.csv", "study3_12345678.csv",
    ]  # fmt: skip
    assert F._data_group_from_path(p) == [
        None, None, None, "pilot1a", "pilot2", "pilot1", "ex2", "ex2a", "ex3", "ex1", "ex2",
        "ex1", None, "ex1", "ex2", None,
    ]  # fmt: skip


# ---------------------------------------------------------------------------
# .llm_classify_batched() asks again for a cut-off answer (#673)
# ---------------------------------------------------------------------------


def _frame(cols: dict[str, list[Any]]) -> pd.DataFrame:
    df = pd.DataFrame(cols)
    df.attrs["llm"] = {"model": "mock"}
    return df


def test_llm_classify_batched_counts_what_no_request_answered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake(text: pd.DataFrame, text_col: str = "text", **_k: Any) -> pd.DataFrame:
        n = len(text[text_col].iloc[0].split("\n"))
        return _frame({"results.index": list(range(1, n + 1)), "results.value": ["banana"] * n})

    monkeypatch.setattr(F, "_llm", fake)
    with pytest.warns(UserWarning, match="2 of 2 items got no usable answer"):
        res = F._llm_classify_batched(
            ["a", "b"], system_prompt="p", value_desc="v", valid=["continuous", "text"]
        )
    assert res.isna().all()
    assert res.attrs["llm_unanswered"] == [1, 2]


@pytest.fixture
def cut_off(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """An llm() that cuts a batch of more than 25 items off, as the mode says."""
    st: dict[str, Any] = {"mode": "limit", "calls": []}

    def fake(text: pd.DataFrame, text_col: str = "text", params: Any = None, **_k: Any) -> Any:
        listing = text[text_col].iloc[0]
        n = len(listing.split("\n"))
        mt = (params or {}).get("max_tokens")
        st["calls"].append((n, mt))
        fail = {
            "limit": (mt or 4096) <= 4096 and n > 25,
            "long": n > 25,
            "always": True,
        }[st["mode"]]
        if fail:
            warnings.warn(
                "Note (not fatal): 1 of 1 LLM extraction failed and was left blank", stacklevel=2
            )
            return _frame({
                "text": [listing],
                ".error": [True],
                ".error_msg": ["Response was truncated because it hit the `max_tokens` limit."],
            })  # fmt: skip
        return _frame({"results.index": list(range(1, n + 1)), "results.value": ["text"] * n})

    monkeypatch.setattr(F, "_llm", fake)
    monkeypatch.setattr("metacheck.llm.core.llm_max_tokens", lambda n=None: None)
    return st


def _classify(**kw: Any) -> pd.Series:
    return F._llm_classify_batched(
        ["item"] * 120, system_prompt="p", value_desc="v", valid=["text"], batch_size=50, **kw
    )


def test_a_batch_cut_off_at_the_limit_is_asked_again_with_four_times_it(
    cut_off: dict[str, Any],
) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # llm()'s note is not shown
        res = _classify()
    assert (res == "text").all()
    assert res.attrs["llm_unanswered"] == []
    assert [c[0] for c in cut_off["calls"]] == [50, 50, 50, 50, 20]
    assert [c[1] for c in cut_off["calls"]] == [None, 4 * 4096, None, 4 * 4096, None]


def test_a_callers_own_limit_is_not_raised(cut_off: dict[str, Any]) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        res = _classify(params={"max_tokens": 2000})
    assert (res == "text").all()
    assert all(c[1] == 2000 for c in cut_off["calls"])


def test_a_batch_too_long_at_any_limit_is_answered_in_halves(cut_off: dict[str, Any]) -> None:
    cut_off["mode"] = "long"
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        res = _classify()
    assert (res == "text").all()
    assert [c[0] for c in cut_off["calls"]] == [50, 50, 25, 25, 50, 50, 25, 25, 20]


def test_never_answered_items_are_all_counted_with_one_warning(cut_off: dict[str, Any]) -> None:
    cut_off["mode"] = "always"
    with pytest.warns(UserWarning, match="120 of 120 items got no usable answer"):
        res = _classify()
    assert res.isna().all()
    assert res.attrs["llm_unanswered"] == list(range(1, 121))


# ---------------------------------------------------------------------------
# a table from an .rds or .RData file is a plain data frame (0.4.4.15)
# ---------------------------------------------------------------------------


def test_a_matrix_classed_column_is_its_plain_numbers() -> None:
    df = data_read_head(str(DATA / "loo.rds"), n_rows=math.inf)
    assert df is not None
    assert df["elpd_diff"].tolist() == [0.0, -1.1]
    assert not col_attrs_at(df, 1)


def test_a_data_table_date_is_a_date() -> None:
    df = data_read_head(str(DATA / "idate.rds"), n_rows=math.inf)
    assert df is not None
    assert [str(v) for v in df["day"]] == ["2022-01-08", "2022-01-09", "2022-01-10"]
    assert col_attrs_at(df, 1)["class"] == ["Date"]
    assert len(df.head(100)) == 3


@pytest.mark.parametrize("name", ["predictions.rds", "predictions.RData"])
def test_the_model_a_table_carries_is_left_out(name: str) -> None:
    df = data_read_head(str(DATA / name), n_rows=math.inf)
    assert df is not None
    assert df["y"].tolist() == [0.1, 0.2, 0.3]
    assert list(df.attrs) == []  # no "model", and no class of the table


def test_columns_of_other_classes_become_rs_own_or_plain_values() -> None:
    df = data_read_head(str(DATA / "mixed.rds"), n_rows=math.inf)
    assert df is not None
    assert col_attrs_at(df, 1)["class"] == ["factor"]
    assert df["f"].astype(str).tolist() == ["a", "b"]
    assert col_attrs_at(df, 2) == {"class": ["POSIXct", "POSIXt"], "tzone": "UTC"}
    # any other class: its plain values, with its label
    assert col_attrs_at(df, 3) == {"label": "Length"}


def test_spss_codes_for_missing_values_survive_an_rds_tibble() -> None:
    df = data_read_head(str(DATA / "spss.rds"), n_rows=2)
    assert df is not None
    a = col_attrs_at(df, 0)
    assert "haven_labelled_spss" in a["class"]
    assert a["na_values"] == 99
    assert a["label"] == "Question"
    assert len(a["labels"]) == 3


# ---------------------------------------------------------------------------
# labels in another encoding (an older Stata or SPSS file)
# ---------------------------------------------------------------------------


def test_utf8_repair_df_repairs_variable_and_value_labels() -> None:
    from metacheck.datacheck._colattrs import ColAttrs

    def raw(b: bytes) -> str:
        return b.decode("utf-8", "surrogateescape")

    df = pd.DataFrame({"q": [1.0, 2.0], "s": ["a", "b"]})
    df.attrs["col_attrs"] = ColAttrs(
        ["q", "s"],
        [
            {
                "label": raw(b"Gem\xfcse"),
                "labels": [(raw(b"gr\xfcn"), 1.0), ("rot", 2.0)],
                "class": ["haven_labelled"],
            },
            {"labels": [("A", "a"), ("B", raw(b"\xfcb"))]},
        ],
    )
    r = F._utf8_repair_df(df)
    assert r is not None
    q = col_attrs_at(r, 0)
    assert q["label"] == "Gemüse"
    assert [n for n, _ in q["labels"]] == ["grün", "rot"]
    assert [c for _, c in q["labels"]] == [1.0, 2.0]
    assert q["class"] == ["haven_labelled"]
    # a value-label code that is text
    assert [c for _, c in col_attrs_at(r, 1)["labels"]] == ["a", "üb"]


# ---------------------------------------------------------------------------
# an .xlsx with text cells that hold no value (PsyArXiv mqyu2_v3)
# ---------------------------------------------------------------------------


def test_an_xlsx_with_text_cells_without_a_value_reads_them_as_empty(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    ok = tmp_path / "ok.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["a", "b"])
    for row in [(1, "p"), (2, "q"), (3, "r")]:
        ws.append(row)
    wb.save(ok)
    bad = tmp_path / "bad.xlsx"
    with zipfile.ZipFile(ok) as src, zipfile.ZipFile(bad, "w", zipfile.ZIP_DEFLATED) as out:
        for name in src.namelist():
            blob = src.read(name)
            if name == "xl/worksheets/sheet1.xml":
                s = blob.decode()
                # a cell marked as an entry of the table of texts that holds no value: only
                # a formula, or nothing (as LIONESS writes them)
                s = s.replace(
                    '<c r="B3" t="inlineStr"><is><t>q</t></is></c>', '<c r="B3" t="s"><f>A3</f></c>'
                )
                s = s.replace('<c r="B4" t="inlineStr"><is><t>r</t></is></c>', '<c r="B4" t="s"/>')
                assert 't="s"><f>A3</f></c>' in s and '<c r="B4" t="s"/>' in s
                blob = s.encode()
            out.writestr(name, blob)
    df = data_read_head(str(bad), n_rows=math.inf)
    assert df is not None
    assert df["a"].tolist() == [1, 2, 3]
    assert df["b"].tolist()[0] == "p"
    assert df["b"].isna().tolist() == [False, True, True]
