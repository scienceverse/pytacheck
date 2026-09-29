"""Adversarial-review tests for the psychds_check port.

The parity harness only checks that both sides raise; these tests pin R's
exact error messages (and so R's order of failures), plus behaviour found in
review: NA notes in the tree, NA file names, ICU collation of non-ASCII names
in the target tree, and the tree's cost on large flat directories.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
import pytest

from metacheck._r import r_sort_key
from metacheck.modules.psychds_check import psychds_tree_html
from tests.mod_psychds import parity_support as ps

ROOT = Path(__file__).resolve().parents[2]
AREAS = ("mod_psychds", "mod_psychds_review")


def _error_cases() -> list[tuple[str, str]]:
    from parity.cases import load_cases

    out = []
    for area in AREAS:
        for case in load_cases(area):
            if not case.golden_path.exists():
                continue
            golden = json.loads(case.golden_path.read_text(encoding="utf-8"))
            # errors pytacheck fixes (known divergences) are not reproduced
            if not golden["ok"] and not case.spec.get("known_divergence"):
                out.append((area, case.id))
    return out


@pytest.mark.parametrize(("area", "case_id"), _error_cases())
def test_error_messages_match_r(area: str, case_id: str) -> None:
    from parity.cases import load_cases, run_python

    (case,) = [c for c in load_cases(area) if c.id == case_id]
    expected = json.loads(case.golden_path.read_text(encoding="utf-8"))["error"]
    with pytest.raises(Exception) as err:
        run_python(case)
    assert str(err.value) == expected


def _nodes(**cols: list[str | None]) -> pd.DataFrame:
    return pd.DataFrame({k: pd.array(v, dtype="string") for k, v in cols.items()})


def test_tree_move_with_na_note_prints_na() -> None:
    # nzchar(NA) is TRUE, and sprintf("%s", NA) is "NA"
    html = psychds_tree_html(
        _nodes(path=["a/x.R", "b.R"], status=["move", "move"], note=[None, "n"])
    )
    assert '└── x.R <span style="color:#b9770e">(NA)</span>' in html
    assert '└── b.R <span style="color:#b9770e">(n)</span>' in html


@pytest.mark.parametrize("paths", [["a/x.R", None], [None, "a/x.R"], ["a.txt", ""], ["/"]])
def test_tree_na_or_empty_path_fails_like_r(paths: list[str | None]) -> None:
    nodes = _nodes(path=paths, status=["present"] * len(paths), note=[""] * len(paths))
    with pytest.raises(IndexError, match="subscript out of bounds"):
        psychds_tree_html(nodes)


def test_na_file_name_falls_back_to_the_file_path() -> None:
    # U113: R targets "analysis/NA"
    out = ps.run_chain("na_name_code_path", file="review_chains.json")
    assert out.table["target_path"].tolist() == [
        "analysis/y.R",
        "analysis/b.R",
        "documentation/notes.txt",
    ]
    assert out.table["file_name"].tolist() == ["y.R", "b.R", "notes.txt"]
    assert out.table["status"].tolist() == ["move", "present", "move"]


@pytest.mark.parametrize(
    "name",
    [
        "na_name_code_nopath",
        "na_name_data_path",
        "na_name_readme_na_path",
        "no_name_readme",
        "no_name_license_second",
        "no_name_na_type_first",
        "no_name_no_path",
        "no_name_no_path_no_type",
    ],
)
def test_missing_names_do_not_stop_the_module(name: str) -> None:
    # U113: R stops with "missing value where TRUE/FALSE needed", "subscript
    # out of bounds", "argument is of length zero", ...; a file with neither
    # name nor path is listed as excluded and left out of the tree
    out = ps.run_chain(name, file="review_chains.json")
    tbl = out.table
    unnamed = tbl["file_name"].isna() & tbl["current_path"].isna()
    assert (tbl.loc[unnamed, "status"] == "excluded").all()
    assert tbl.loc[unnamed, "target_path"].isna().all()
    assert tbl.loc[~unnamed, "target_path"].notna().all()
    assert out.traffic_light in ("green", "yellow", "red")


# R 4.5 (ICU) order(tolower(x)) of these names, from the reference Rscript
ICU_ORDER = [
    "1",
    "½",
    "ae.R",
    "æ.R",
    "af.R",
    "d.R",
    "đ.R",
    "data 1.csv",
    "data_final.csv",
    "data-final.csv",
    "data.final.csv",
    "data(1).csv",
    "data1.csv",
    "h.R",
    "ħ.R",
    "ⅰ",
    "i.R",
    "ı.R",
    "l.R",
    "ł.R",
    "n.R",
    "ŋ.R",
    "o.R",
    "ø.R",
    "œ.R",
    "p.R",
    "ss.R",
    "ß.R",
    "st.R",
    "ｘ",
    "x2",
    "x²",
    "z.R",
    "þ.R",
]


def test_r_sort_key_follows_icu_root_collation() -> None:
    from metacheck.modules.psychds_check import _tolower

    shuffled = sorted(ICU_ORDER, key=lambda s: (len(s), s[::-1]))
    assert sorted(shuffled, key=lambda s: r_sort_key(_tolower(s))) == ICU_ORDER
    # accents in ICU order (acute < grave < circumflex; ring < diaeresis), not code points
    assert sorted(["è", "ê", "é"], key=r_sort_key) == ["é", "è", "ê"]
    assert sorted(["ä", "å"], key=r_sort_key) == ["å", "ä"]


def test_tree_scales_linearly_on_flat_directories() -> None:
    n = 20000
    nodes = _nodes(path=[f"f{i}.R" for i in range(n)], status=["present"] * n, note=[""] * n)
    start = time.perf_counter()
    html = psychds_tree_html(nodes)
    # the old per-head rescan of the whole directory was quadratic (minutes here)
    assert time.perf_counter() - start < 20
    assert html.count("\n") == n + 1
