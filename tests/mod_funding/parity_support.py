"""Python side of the ``mod_funding`` parity cases on the module's internal helpers.

metacheck's ``inst/modules/funding_check.R`` defines its helpers inside the
module file, so the R side of these cases sources that file
(``tests/mod_funding/funding_env.R``) and the Python side calls
:mod:`pytacheck.modules._funding`. Positions are reported 1-based, as R's
``grep()`` does.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from pytacheck.modules import _funding as F


def _title(words: str) -> list[str]:
    return list(F._patterns_title(words))


def _pattern_table() -> dict[str, Callable[[], list[str]]]:
    """The regex patterns each R function passes to grep()/grepl()/gsub(), in call order.

    Both branches of a two-step function are listed, as R uses both when
    nothing matches the first pattern.
    """
    return {
        "get_support_1": lambda: list(F._patterns_support_1()),
        "get_support_3": lambda: [F._pattern_support_3()],
        "get_support_4": lambda: [F._pattern_support_4()],
        "get_support_5": lambda: [F._pattern_support_5()],
        "get_support_6": lambda: list(F._patterns_support_6()),
        "get_support_7": lambda: [F._pattern_support_7()],
        "get_support_8": lambda: [F._pattern_support_8()],
        "get_support_9": lambda: ["upport", F._pattern_support_9()],
        "get_support_10": lambda: [F._pattern_support_10()],
        "get_developed_1": lambda: [F._pattern_developed_1()],
        "get_received_1": lambda: [F._pattern_received_1()],
        "get_received_2": lambda: [F._pattern_received_2()],
        "get_recipient_1": lambda: [F._pattern_recipient_1()],
        "get_authors_1": lambda: [F._pattern_authors_1()],
        "get_authors_2": lambda: [F._pattern_authors_2()],
        "get_thank_1": lambda: [F._pattern_thank_1()],
        "get_thank_2": lambda: [F._pattern_thank_2()],
        "get_fund_1": lambda: [F._pattern_fund_1()],
        "get_fund_2": lambda: _title("funding_title"),
        "get_fund_3": lambda: [F._pattern_fund_3()],
        "get_fund_acknow": lambda: [F._pattern_fund_acknow()],
        "get_supported_1": lambda: [F._pattern_supported_1()],
        "get_financial_1": lambda: _title("financial_title"),
        "get_financial_2": lambda: [F._pattern_financial_2()],
        "get_financial_3": lambda: [F._pattern_financial_3()],
        "get_disclosure_1": lambda: list(F._patterns_disclosure_1()),
        "get_disclosure_2": lambda: [F._pattern_disclosure_2()],
        "get_grant_1": lambda: [_title("grant_title")[0], F._pattern_grant_1_within()],
        "get_french_1": lambda: ["Cette.*tude.*financ.*par"],
        "get_project_acknow": lambda: ["project (no|num)"],
        "get_common_1": lambda: [F._pattern_common_1()],
        "get_common_2": lambda: [F._pattern_common_2()],
        "get_common_3": lambda: ["required to disclose.*disclosed none"],
        "get_common_4": lambda: [F._pattern_common_4()],
        "get_common_5": lambda: [F._pattern_common_5()],
        "negate_absence_1": lambda: [F._pattern_negate_absence_1()],
        "get_acknow_1": lambda: [F._ACKNOW_1],
        "get_acknow_2": lambda: [F._ACKNOW_2],
        ".where_refs_txt": lambda: [F._pattern_where_refs(), "^1(|\\.)\\s+[A-Z]"],
        ".where_acknows_txt": lambda: [
            F._ACKNOW_2,
            *_title("funding_title"),
            *_title("financial_title"),
            _title("grant_title")[0],
            F._pattern_grant_1_within(),
        ],
    }


def patterns(names: Sequence[str]) -> dict[str, list[str]]:
    """Python side of ``fc_patterns()``."""
    table = _pattern_table()
    return {name: table[name]() for name in names}


def _py_name(r_name: str) -> str:
    return "_" + r_name[1:] if r_name.startswith(".") else r_name


def apply(fns: Sequence[str], article: Any) -> dict[str, Any]:
    """Python side of ``fc_apply()``: positions are shifted to R's 1-based values."""
    out: dict[str, Any] = {}
    for fn in fns:
        value = getattr(F, _py_name(fn))(article)
        if fn.startswith(("get_", ".where_")):
            value = [i + 1 for i in value]
        out[fn] = value
    return out


def funding(articles: Sequence[Sequence[str]]) -> list[str]:
    """Python side of ``fc_funding()``."""
    return [F.rtransparent_funding(list(a)) for a in articles]


def call(fn: str, *args: Any, **kwargs: Any) -> Any:
    """Call a helper by its R name (``.encase`` is ``_encase``)."""
    return getattr(F, _py_name(fn))(*args, **kwargs)


def masks(patterns: Sequence[str], x: Sequence[str | None], ignore_case: bool = False) -> list[Any]:
    """``lapply(patterns, grepl, x, perl = TRUE, ignore.case = ...)`` through
    :class:`pytacheck.modules._funding._Article` (literal prefilter and shared
    column cache)."""
    art = F._Article([v if isinstance(v, str) else None for v in x])
    return [art.mask(p, ignore_case).tolist() for p in patterns]


def sectioned(text: Sequence[str], section_type: Sequence[str]) -> Any:
    """Python side of ``fc_sectioned()``: one section per sentence."""
    import pandas as pd

    import pytacheck as pc

    p = pc.test_paper(list(text))
    n = len(text)
    ids = [float(i) for i in range(n)]
    text_table = p.text.copy()
    text_table["section_id"] = ids
    p.text = text_table
    p.section = pd.DataFrame(
        {
            "section_id": pd.Series(ids, dtype="float64"),
            "header": pd.Series([f"Section {i + 1}" for i in range(n)], dtype="string"),
            "parent_section_id": pd.Series([None] * n, dtype="Int64"),
            "section_type": pd.Series(list(section_type), dtype="string"),
            "classification_score": pd.Series([0.0] * n, dtype="float64"),
        }
    )
    return p
