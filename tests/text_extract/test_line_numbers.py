"""A manuscript's line numbers are removed when a paper is read (metacheck 0.4.4.4, #648).

Port of the new tests of ``tests/testthat/test-import-read.R`` and ``test-stat-tables.R``.
"""

from __future__ import annotations

import pandas as pd

import metacheck as pc
from metacheck.io.read import _line_step_ok, _paper_repair_text, _repair_line_numbers
from metacheck.statout.r_output import _r_output_oneline, read_r_output
from metacheck.statout.stat_output import stat_results_long

LINE = "the participants in this study read each of the short texts on the screen and"


def _numbered_lines(a: int, b: int) -> list[str]:
    """A manuscript's text with a line number after every 80 characters or so."""
    return [f"{LINE} {k}" for k in range(a, b + 1)]


def test_a_manuscripts_line_numbers_are_removed() -> None:
    s = [
        " ".join(_numbered_lines(100, 120)),
        "The effect was large, t(118) = 7.35, p < 121 .001, in the 250 children.",
        " ".join(_numbered_lines(122, 140)),
    ]
    out = _repair_line_numbers(s)
    import re

    assert not any(re.search(r"\b1[0-4]\d\b", o) for i, o in enumerate(out) if i != 1)
    assert out[1] == "The effect was large, t(118) = 7.35, p < .001, in the 250 children."
    assert out[0] == " ".join([LINE] * 21)
    # NA stays NA
    assert _repair_line_numbers([*s, None])[3] is None
    # the last line of a paragraph can be short
    s = [
        " ".join(_numbered_lines(100, 120)),
        "and at the end. 121",
        "122 The next paragraph",
        " ".join(_numbered_lines(123, 140)),
    ]
    out = _repair_line_numbers(s)
    assert out[1:3] == ["and at the end.", "The next paragraph"]


def test_line_step_ok_takes_a_lines_distance_and_one_short_line() -> None:
    got = [
        _line_step_ok(dv, ch)
        for dv, ch in zip([1, 1, 1, 2, 2, 3], [12, 80, 200, 30, 160, 600], strict=True)
    ]
    assert got == [True, True, False, False, True, False]


def test_runs_and_the_line_numbers_beside_them_are_removed() -> None:
    s = [
        "There was no main effect of 918 919 920 921 922 cue type, F(2, 80) = 0.01.",
        f"{LINE} 923 {LINE} 924 the end.",
        # not a line number: a table row, an amount, and numbers far off
        f"Age 25-34 1169 925 21.4 and {LINE} 926 an estimated 927 million people.",
        "Tables 1 2 3 4 5 and the years 2011 2012 2013 2014 2015 stay.",
    ]
    out = _repair_line_numbers(s)
    assert out[0] == "There was no main effect of cue type, F(2, 80) = 0.01."
    assert out[1] == f"{LINE} {LINE} the end."
    assert out[2] == f"Age 25-34 1169 925 21.4 and {LINE} an estimated 927 million people."
    assert out[3] == s[3]


def test_a_text_without_line_numbers_is_left_alone() -> None:
    s = [
        "We tested 120 children and 130 adults in 2020.",
        "Of these, 140 children and 150 adults came back, and 160 did not.",
        "Table 2 shows the 170 tests, and Study 3 the other 180.",
        *_numbered_lines(1, 20),
    ]
    assert _repair_line_numbers(s) == s
    assert _repair_line_numbers([]) == []


def test_positions_move_after_a_removed_line_number() -> None:
    p = pc.test_paper("t")
    s = [
        " ".join(_numbered_lines(100, 125)),
        "As shown in the earlier studies of reading with the same kind of short 126 texts (Smith, 2020).",
    ]
    p["text"] = pd.DataFrame({"text": s, "text_id": [1, 2]})
    at = s[1].index("Smith")
    p["xref"] = pd.DataFrame({"xref_id": [1], "text_id": [2], "start": [at], "end": [at + 11]})
    q = _paper_repair_text(p)
    new = q["text"]["text"].tolist()[1]
    assert (
        new
        == "As shown in the earlier studies of reading with the same kind of short texts (Smith, 2020)."
    )
    x = q["xref"]
    assert new[int(x["start"].iloc[0]) : int(x["end"].iloc[0])] == "Smith, 2020"


def test_code_settings_and_row_numbers_are_not_stored_as_statistics() -> None:
    r = _r_output_oneline(
        [
            "t = 2.34, df = 48, p-value = 0.02",
            "SD = 0.41, SE = 0.05",
            "scale1 = 10000; width = 1480, res = 300, size = 3",
        ],
        "s.R",
    )
    nm = list(r[0]["data"].columns)
    assert {"t", "df", "p-value", "SD", "SE"} <= set(nm)
    assert not ({"scale1", "width", "res", "size"} & set(nm))
    # a column of row numbers under a default name is not a statistic; nor, since metacheck
    # 0.4.4.8 (#648), is any column without a header (V1, V3) or with a number as its header,
    # unless that names a statistic
    tb = [
        {
            "analysis": "a",
            "title": None,
            "data": pd.DataFrame({"grp": ["Human", "AI"], "V1": ["1", "2"], "M": ["3.36", "4.08"]}),
        },
        {
            "analysis": "b",
            "title": None,
            "data": pd.DataFrame({"V1": ["5", "7"], "t": ["2.1", "3.3"], "0.61": ["1", "2"]}),
        },
    ]
    long = stat_results_long(tb)
    assert not ((long["analysis"] == "a") & (long["statistic"] == "V1")).any()
    assert not long["statistic"].isin(["V1", "0.61"]).any()
    assert long.loc[(long["analysis"] == "b") & (long["statistic"] == "t"), "value"].tolist() == [
        "2.1",
        "3.3",
    ]


def _rd(x: list[str]) -> pd.DataFrame:
    return stat_results_long(read_r_output(x))


def test_kable_tables_metafors_marked_columns_and_lavaans_blocks_are_read() -> None:
    pipe = [
        "|term      | estimate| p.value|",
        "|:---------|--------:|-------:|",
        "|condition |   -0.231|   0.008|",
        "|age       |    0.044|   0.000|",
    ]
    simple = [
        "term         estimate   p.value",
        "----------  ---------  --------",
        "condition       -0.231     0.008",
        "age              0.044     0.000",
    ]
    for x in (pipe, simple):
        long = _rd(x)
        sel = (long["row_label"] == "condition") & (long["statistic"] == "estimate")
        assert long.loc[sel, "value"].tolist() == ["-0.231"]
        assert sorted(set(long["statistic"])) == ["estimate", "p.value"]
    # metafor's superscript mark keeps the columns in place
    mf = [
        "Model Results:",
        "",
        "estimate      se    tval  df    pval¹   ci.lb¹   ci.ub¹      ",
        "  0.3201  0.0482  6.6466  11  <.0001  0.2141  0.4261  *** ",
    ]
    long = _rd(mf)
    assert long.loc[long["statistic"] == "ci.lb", "value"].tolist() == ["0.2141"]
    from metacheck.statout.match_reported import _stat_family

    assert _stat_family("tval") == "t"
    # lavaan: a block of "label  value" lines, and a table with group lines
    lv = [
        "Model Test User Model:",
        "                                                      ",
        "  Test statistic                                24.361",
        "  Degrees of freedom                                 8",
        "  P-value (Chi-square)                           0.002",
        "",
        "User Model versus Baseline Model:",
        "",
        "  Comparative Fit Index (CFI)                    0.975",
        "",
        "Latent Variables:",
        "                   Estimate  Std.Err  z-value  P(>|z|)",
        "  visual =~                                           ",
        "    x1                1.000                           ",
        "    x2                0.559    0.105    5.301    0.000",
    ]
    long = _rd(lv)
    assert long.loc[long["statistic"] == "Chi-square", "value"].tolist() == ["24.361"]
    sel = (long["statistic"] == "p") & (long["analysis"] == "Model Test User Model")
    assert long.loc[sel, "value"].tolist() == ["0.002"]
    assert long.loc[long["statistic"] == "CFI", "value"].tolist() == ["0.975"]
    sel = (long["row_label"] == "x2") & (long["statistic"] == "z-value")
    assert long.loc[sel, "value"].tolist() == ["5.301"]
    # a printed table's rows are no "label  value" lines
    t = _rd(["   ", "     0  1", "  a 13  7", "  b 11  9"])
    assert not t["statistic"].astype(str).str.startswith("a").any()
