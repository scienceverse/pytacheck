"""module_run()'s na_replace touches only the module's own summary columns (U77)."""

from __future__ import annotations

import pandas as pd

import pytacheck as pc
from pytacheck.module import module_run
from pytacheck.papers.model import PaperList

_FIRST = """
import pandas as pd
from pytacheck.module import module


@module(title="First", description="d", keywords=["general"])
def nr_first(paper):
    return {
        "summary_table": pd.DataFrame(
            {
                "paper_id": ["p1"],
                "flag": pd.Series([None], dtype="boolean"),
                "found": [None],
                "shared": pd.Series([None], dtype="Float64"),
            }
        ),
    }
"""

_SECOND = """
import pandas as pd
from pytacheck.module import module


@module(title="Second", description="d", keywords=["general"])
def nr_second(paper, na_replace=0):
    return {
        "summary_table": pd.DataFrame(
            {
                "paper_id": ["p1"],
                "n": pd.Series([None], dtype="Float64"),
                "shared": pd.Series([None], dtype="Float64"),
            }
        ),
        "na_replace": na_replace,
    }
"""


def _papers() -> PaperList:
    p1 = pc.test_paper(["x"])
    p1.paper_id = "p1"
    p2 = pc.test_paper(["y"])
    p2.paper_id = "p2"
    return PaperList([p1, p2])


def _chain(tmp_path, na_replace):
    first = tmp_path / "nr_first.py"
    first.write_text(_FIRST, encoding="utf-8")
    second = tmp_path / "nr_second.py"
    second.write_text(_SECOND, encoding="utf-8")
    op = module_run(_papers(), str(first))
    return module_run(op, str(second), na_replace=na_replace).summary_table


def test_unnamed_na_replace_leaves_earlier_modules_alone(tmp_path) -> None:
    # R recycles the value over every column of the chained table: the first
    # module's logical `flag` became numeric 0 and its `found` cells 0
    st = _chain(tmp_path, 0)
    assert list(st.columns) == ["paper_id", "flag", "found", "shared", "n", "shared.nr_second"]
    assert st["flag"].isna().all() and str(st["flag"].dtype) == "boolean"
    assert st["found"].isna().all()
    assert st["shared"].isna().all()
    assert st["n"].tolist() == [0, 0]
    assert st["shared.nr_second"].tolist() == [0, 0]


def test_named_na_replace_finds_the_module_column_under_its_suffix(tmp_path) -> None:
    st = _chain(tmp_path, {"shared": 5, "flag": True})
    assert st["shared"].isna().all()  # the first module's column
    assert st["shared.nr_second"].tolist() == [5, 5]
    assert st["flag"].isna().all()  # not the second module's column
    assert st["n"].isna().all()


def test_first_module_in_a_chain_is_filled_as_before() -> None:
    st = module_run(_papers(), "marginal").summary_table
    assert isinstance(st, pd.DataFrame)
    assert st["marginal"].tolist() == [0, 0]
