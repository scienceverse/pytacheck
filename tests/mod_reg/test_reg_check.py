"""Tests for the reg_check module (port of tests/testthat/test-module-reg_check.R).

Every test runs offline: OSF/AsPredicted requests replay metacheck's recorded
responses (``tests/mod_prereg/parity_support.mocked``) and the RegCheck
server is faked (``tests/mod_reg/parity_support.fake_regcheck``), as the R
tests do with ``local_mocked_bindings(regcheck_compare = ...)``.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any
from unittest import mock as umock

import pandas as pd
import pytest

import pytacheck as pc
from pytacheck.modules.reg_check import prereg_row_text
from pytacheck.report.blocks import ReportTable
from tests.mod_prereg.parity_support import mocked, tp
from tests.mod_reg.parity_support import MOCK_TABLE, env, fake_regcheck, frame, run_reg

GUID = "5xysn"


@contextlib.contextmanager
def recorded(fake: str = "table") -> Iterator[list[int]]:
    """Recorded OSF/AsPredicted responses and a fake RegCheck server."""
    with (
        mocked(),
        umock.patch("pytacheck.utils.online", return_value=True),
        env(REGCHECK_API_TOKEN="", REGCHECK_BASE_URL=""),
        fake_regcheck(fake) as calls,
    ):
        yield calls


@contextlib.contextmanager
def verbose_on() -> Iterator[None]:
    from pytacheck.config import verbose

    old = verbose()
    verbose(True)
    try:
        yield
    finally:
        verbose(old)


def _report_text(mo: Any) -> list[str]:
    return [b for b in mo.report if isinstance(b, str)]


# --- the testthat tests -------------------------------------------------------------


def test_module_exists() -> None:
    mods = pc.module_list()
    assert "reg_check" in mods["name"].tolist()


def test_no_preregistrations() -> None:
    paper = pc.test_paper("There are no links here.")
    mo = pc.module_run(paper, "reg_check")

    assert mo.traffic_light == "na"
    assert "No preregistrations" in mo.summary_text
    assert mo.summary_table["regcheck_deviations"].tolist() == [0]
    assert mo.table is None


def test_standalone_run() -> None:
    paper = pc.test_paper(url=[f"https://osf.io/{GUID}"])
    with recorded("table"):
        mo = pc.module_run(paper, "reg_check")

    # one row per compared dimension, labelled with prereg and paper ids
    assert len(mo.table) == 3
    assert {"dimension", "deviation_judgement", "prereg_id", "paper_id"} <= set(mo.table.columns)
    assert mo.table["prereg_id"].unique().tolist() == [GUID]
    assert mo.table["paper_id"].unique().tolist() == [paper.paper_id]

    # judgement counts in the summary table
    assert mo.summary_table["regcheck_deviations"].tolist() == [1]
    assert mo.summary_table["regcheck_consistent"].tolist() == [1]
    assert mo.summary_table["regcheck_unclear"].tolist() == [1]

    # presented in the report with the LLM disclaimer
    text = _report_text(mo)
    assert any("RegCheck" in t for t in text)
    assert any("potential deviation" in t for t in text)
    assert any("large language model" in t for t in text)
    assert "flagged 1 potential deviation" in mo.summary_text

    # traffic light stays informational (no automated evaluation)
    assert mo.traffic_light == "info"


def test_chained_after_prereg_check() -> None:
    paper = pc.demopaper()
    with recorded("table"):
        mo = pc.module_run(pc.module_run(paper, "prereg_check"), "reg_check")

    # demopaper has 2 preregistrations -> one comparison per prereg
    assert len(mo.table) == 6
    assert set(mo.table["prereg_id"]) == {"48ncu", "by8i8v"}
    assert mo.summary_table["regcheck_deviations"].tolist() == [2]
    assert mo.traffic_light == "info"
    # the chained summary table keeps prereg_check's columns
    assert list(mo.summary_table.columns[:2]) == ["paper_id", "preregistration"]


def test_duplicate_prereg_links_are_compared_once() -> None:
    paper = pc.test_paper(url=[f"https://osf.io/{GUID}"])
    with recorded("table") as calls:
        prereg_out = pc.module_run(paper, "prereg_check")
        # simulate a paper that links the same preregistration twice
        prereg_out.table = pd.concat([prereg_out.table, prereg_out.table], ignore_index=True)
        mo = pc.module_run(prereg_out, "reg_check")

    assert calls[0] == 1
    assert len(mo.table) == 3
    assert mo.table["prereg_id"].unique().tolist() == [GUID]


def test_prereg_row_text_flattens_a_prereg_row() -> None:
    row = pd.DataFrame(
        {
            "paper_id": ["p1"],
            "id": ["abc123"],
            "link": ["https://osf.io/abc123"],
            "ia_url": ["https://web.archive.org/abc"],
            "hypotheses": ["We predict A > B."],
            "sample_size": ["N = 100"],
        }
    )
    txt = prereg_row_text(row)

    # paper_id, link, ia_url are excluded
    assert "paper_id" not in txt
    assert "osf.io" not in txt
    assert "web.archive" not in txt

    # content fields appear with their name as a header
    assert "hypotheses" in txt
    assert "We predict A > B." in txt
    assert "sample_size" in txt
    assert "N = 100" in txt
    assert txt == "id\nabc123\n\nhypotheses\nWe predict A > B.\n\nsample_size\nN = 100"


def test_prereg_row_text_skips_na_and_empty_fields() -> None:
    row = pd.DataFrame(
        {
            "paper_id": ["p1"],
            "link": pd.Series([None], dtype="string"),
            "ia_url": [""],
            "hypotheses": pd.Series([None], dtype="string"),
            "sample_size": ["   "],
        }
    )
    assert prereg_row_text(row) == ""


def test_all_comparisons_failing_returns_an_error_light(capsys: pytest.CaptureFixture) -> None:
    paper = pc.test_paper(url=[f"https://osf.io/{GUID}"])
    with recorded("error"), verbose_on():
        capsys.readouterr()
        mo = pc.module_run(paper, "reg_check")
    err = capsys.readouterr().err
    assert "RegCheck comparison failed" in err
    assert f"RegCheck comparison failed for preregistration {GUID}: RegCheck server" in err

    assert mo.traffic_light == "error"
    assert "RegCheck comparison failed" in mo.summary_text
    assert mo.summary_table["regcheck_deviations"].isna().all()
    # no na_replace: a failed check is unknown, not zero
    assert mo.get("na_replace") is None


def test_partial_failure_still_returns_successful_comparisons(
    capsys: pytest.CaptureFixture,
) -> None:
    paper = pc.demopaper()  # has 2 preregistrations: 48ncu and by8i8v
    with recorded("partial"), verbose_on():
        capsys.readouterr()
        mo = pc.module_run(pc.module_run(paper, "prereg_check"), "reg_check")
    assert "RegCheck comparison failed" in capsys.readouterr().err

    # one succeeded, one failed -> results from the successful one are returned
    assert mo.traffic_light == "info"
    assert len(mo.table) == 3
    assert mo.table["prereg_id"].nunique() == 1


# --- beyond the testthat tests --------------------------------------------------------


def test_metadata() -> None:
    spec = pc.module_info("reg_check")
    assert spec.title == "RegCheck Preregistration Comparison"
    assert spec.section == "method"
    assert set(spec.requires) == {"network", "llm"}
    assert list(spec.arg_defaults) == ["paper", "client", "base_url", "dimensions"]
    assert spec.arg_defaults["client"] == "ollama"


def test_output_elements_in_r_order() -> None:
    paper = tp([f"https://osf.io/{GUID}"], "p_order")
    with recorded("table"):
        mo = pc.module_run(paper, "reg_check")
    assert list(mo.table.columns) == [*MOCK_TABLE, "prereg_id", "paper_id"]
    assert list(mo.summary_table.columns) == [
        "paper_id",
        "regcheck_deviations",
        "regcheck_consistent",
        "regcheck_unclear",
    ]
    assert mo.na_replace == 0
    assert isinstance(mo.table.index, pd.RangeIndex)


def test_arguments_are_passed_to_regcheck_compare() -> None:
    paper = tp([f"https://osf.io/{GUID}"], "p_args", ["First sentence.", "Second sentence."])
    dims = pd.DataFrame({"dimension": ["Sample size"], "definition": ["N"]})
    seen: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def fake(*args: Any, **kwargs: Any) -> pd.DataFrame:
        seen.append((args, kwargs))
        return frame(MOCK_TABLE)

    with recorded("real"), umock.patch("pytacheck.db.regcheck.regcheck_compare", fake):
        pc.module_run(paper, "reg_check", client="groq", base_url="http://x", dimensions=dims)

    assert len(seen) == 1
    (full_text, prereg_text), kwargs = seen[0]
    assert full_text == "First sentence. Second sentence."
    assert prereg_text.startswith("template_name\nOpen-Ended Registration")
    assert "\n\nid\n5xysn" in prereg_text
    assert kwargs["client"] == "groq"
    assert kwargs["base_url"] == "http://x"
    assert kwargs["dimensions"] is dims


def test_inputs_are_not_mutated() -> None:
    paper = tp([f"https://osf.io/{GUID}"], "p_mut")
    shared = frame(MOCK_TABLE)
    with (
        recorded("real"),
        umock.patch("pytacheck.db.regcheck.regcheck_compare", return_value=shared),
    ):
        prereg_out = pc.module_run(paper, "prereg_check")
        prereg_out.table = pd.concat([prereg_out.table, prereg_out.table], ignore_index=True)
        before = prereg_out.table.copy()
        pc.module_run(prereg_out, "reg_check")
    pd.testing.assert_frame_equal(prereg_out.table, before)
    # the comparison result is copied before prereg_id/paper_id are added
    assert list(shared.columns) == list(MOCK_TABLE)


def test_paperlist_papers_without_preregistrations_get_zero() -> None:
    mo = run_reg(
        papers=[
            {"url": ["https://osf.io/48ncu"], "id": "paper_a"},
            {"url": [], "id": "paper_b", "text": ["No links."]},
        ]
    )
    st = mo.summary_table.set_index("paper_id")
    assert st.loc["paper_a", "regcheck_deviations"] == 1
    assert st.loc["paper_b", "regcheck_deviations"] == 0
    assert st.loc["paper_b", "regcheck_unclear"] == 0


def test_each_prereg_is_compared_with_its_own_paper() -> None:
    mo = run_reg(
        papers=[
            {"url": ["https://osf.io/48ncu"], "id": "paper_a", "text": ["Text of A."]},
            {"url": ["https://osf.io/5xysn"], "id": "paper_b", "text": ["Text of B."]},
        ],
        fake="echo",
    )
    echoed = mo.table[mo.table["dimension"] == "Paper text"]
    assert dict(zip(echoed["paper_id"], echoed["paper_summary"], strict=True)) == {
        "paper_a": "Text of A.",
        "paper_b": "Text of B.",
    }


def test_odd_judgements() -> None:
    mo = run_reg(papers=[{"url": ["https://osf.io/5xysn"], "id": "p_odd"}], fake="odd")
    # an NA judgement is left out of the counts and reported as such (R: every
    # count is NA -- "flagged NA potential deviationNA" -- and na_replace
    # turns it into 0, U120)
    assert mo.summary_table["regcheck_deviations"].tolist() == [1]
    assert mo.summary_table["regcheck_consistent"].tolist() == [0]
    assert mo.summary_table["regcheck_unclear"].tolist() == [1]
    assert mo.summary_text == (
        "RegCheck compared the paper with 1 preregistration on 4 dimensions, and flagged "
        "1 potential deviation (1 dimension not specified in the preregistration). "
        "RegCheck gave no judgement for 1 dimension."
    )
    judgement = next(b for b in mo.report if isinstance(b, ReportTable)).data
    assert judgement["judgement"].tolist()[:1] == ["deviation"]
    assert judgement["judgement"].tolist()[2:] == ["Partially", "unclear"]
    assert pd.isna(judgement["judgement"].tolist()[1])
    assert judgement["explanation"].tolist()[:3] == ["a", "b", "c [lower]"]


def test_report_structure() -> None:
    mo = run_reg(demo=True)
    blocks = mo.report
    assert blocks[0].startswith("RegCheck compared the paper with 2 preregistrations")
    assert blocks[1] == "Comparison with preregistration 48ncu:"
    assert isinstance(blocks[2], ReportTable)
    assert blocks[3].startswith('::: {.callout-tip title="RegCheck evidence for 48ncu"')
    assert isinstance(blocks[4], ReportTable)
    assert blocks[4].maxrows == 5
    assert blocks[5] == ":::\n"
    assert blocks[6] == "Comparison with preregistration by8i8v:"
    assert blocks[-1].startswith('::: {.callout-tip title="Learn More"')
    assert len(blocks) == 12


def test_chained_prereg_without_table_runs_prereg_check_again() -> None:
    paper = tp([], "p_none", ["No links here."])
    prereg_out = pc.module_run(paper, "prereg_check")
    assert prereg_out.table is None
    with umock.patch(
        "pytacheck.module.module_run", wraps=pc.module_run
    ) as spy:  # reg_check imports module_run lazily from pytacheck.module
        mo = pc.module_run(prereg_out, "reg_check")
    assert [c.args[1] for c in spy.call_args_list] == ["prereg_check"]
    assert mo.traffic_light == "na"


def test_empty_paperlist_errors() -> None:
    # R: "Running the module 'reg_check' produced errors: Running the module
    # 'prereg_check' produced errors: arguments imply differing number of rows: 0, 1"
    with pytest.raises(pc.ModuleError, match="Running the module 'prereg_check' produced errors"):
        pc.module_run(pc.PaperList([]), "reg_check")


def test_tables_via_run_reg() -> None:
    tables = run_reg(demo=True, tables=True)
    assert len(tables) == 4
    assert list(tables[0].columns) == ["dimension", "judgement", "explanation"]
    assert list(tables[1].columns) == [
        "dimension",
        "paper_summary",
        "prereg_summary",
        "paper_quotes",
        "prereg_quotes",
    ]
    # strip_refs() removes the [PAPER_0001] quote references
    assert tables[1]["paper_quotes"].tolist() == [" quote"] * 3


# --- review: divergences found after the port --------------------------------------


def test_tolower_is_rs_per_character_mapping() -> None:
    from pytacheck.modules.reg_check import _tolower

    # R (glibc towlower): U+0130 -> "i" and no final-sigma rule
    assert _tolower("MİSSİNG") == "missing"
    assert _tolower("ΑΣ") == "ασ"
    assert _tolower(" YES ") == " yes "


def test_judgement_with_dotted_capital_i_counts_as_missing() -> None:
    mo = run_reg(**_OER, pre=["prereg_check"], fake="http:success")
    assert mo.traffic_light == "info"
    # MİSSİNG -> missing; " yes " -> yes; "Yes " is not trimmed (not "yes")
    assert mo.summary_text == (
        "RegCheck compared the paper with 1 preregistration on 6 dimensions, and flagged "
        "1 potential deviation (1 dimension not specified in the preregistration)."
    )
    tables = run_reg(**_OER, pre=["prereg_check"], fake="http:success", tables=True)
    assert tables[0]["judgement"].tolist() == [
        "unclear",
        "consistent",
        "",
        "ΑΣ",
        "Yes ",
        "deviation",
    ]


_OER: dict[str, Any] = {
    "papers": [
        {
            "url": ["https://osf.io/5xysn"],
            "id": "p_oer",
            "text": ["We preregistered this study.", "It had 120 participants."],
        }
    ]
}
_TWO: dict[str, Any] = {
    "papers": [
        {
            "url": ["https://osf.io/5xysn", "https://osf.io/48ncu"],
            "id": "p_two",
            "text": ["Two preregistrations."],
        }
    ]
}


def test_na_prereg_id_has_its_own_section() -> None:
    # each section holds its own rows: an NA prereg_id is a section of its own
    # (R's regcheck_table[prereg_id == rid, ] adds a row of NAs to every
    # section for each NA prereg_id, U120)
    tables = run_reg(**_TWO, pre=["prereg_check"], na_id=[0], tables=True)
    na_section, other_section = tables[0], tables[2]
    assert na_section["dimension"].tolist() == MOCK_TABLE["dimension"]
    assert other_section["dimension"].tolist() == MOCK_TABLE["dimension"]
    assert not other_section.isna().any().any()


def test_shared_prereg_sections_name_the_paper() -> None:
    # U120: two papers linking one preregistration get a section each, named
    # after the paper (R: one section mixing both papers' rows, unlabelled)
    mo = run_reg(
        papers=[
            {"url": ["https://osf.io/5xysn"], "id": "pa", "text": ["Paper A."]},
            {
                "url": ["https://osf.io/5xysn", "https://osf.io/48ncu"],
                "id": "pb",
                "text": ["Paper B."],
            },
        ]
    )
    headings = [b for b in mo.report if isinstance(b, str) and b.startswith("Comparison")]
    assert headings == [
        "Comparison of paper pa with preregistration 5xysn:",
        "Comparison of paper pb with preregistration 5xysn:",
        "Comparison with preregistration 48ncu:",
    ]
    tables = [b.data for b in mo.report if isinstance(b, ReportTable)]
    assert [len(t) for t in tables] == [len(MOCK_TABLE["dimension"])] * 6


def test_all_na_prereg_ids_are_duplicates() -> None:
    mo = run_reg(**_TWO, pre=["prereg_check"], na_id=[0, 1])
    # duplicated() treats (p_two, NA) twice as a duplicate: one comparison
    assert len(mo.table) == 3
    assert mo.table["prereg_id"].isna().all()


@pytest.mark.parametrize(
    ("fake", "lengths"), [("nodim", "0, 3"), ("noinfo", "3, 0"), ("noquotes", "3, 0")]
)
def test_missing_regcheck_columns_raise_like_data_frame(fake: str, lengths: str) -> None:
    msg = run_reg(**_OER, fake=fake, catch=True)
    assert msg == (
        "Running the module 'reg_check' produced errors: "
        f"arguments imply differing number of rows: {lengths}"
    )


def test_data_frame_recycles_and_checks_lengths() -> None:
    from pytacheck.modules.reg_check import _data_frame

    df = _data_frame({"a": ["x", "y"], "b": ["z"]})
    assert df["b"].tolist() == ["z", "z"]
    with pytest.raises(ValueError, match="differing number of rows: 3, 2"):
        _data_frame({"a": ["x", "y", "z"], "b": ["u", "v"]})


def test_http_errors_become_the_error_summary() -> None:
    mo = run_reg(**_OER, pre=["prereg_check"], fake="http:http404")
    assert mo.traffic_light == "error"
    assert mo.summary_text == (
        "We found 1 preregistration, but the RegCheck comparison failed: HTTP 404 Not Found.."
    )
    assert mo.summary_table["regcheck_deviations"].isna().all()


def test_empty_chained_table_means_no_preregistrations() -> None:
    mo = run_reg(**_OER, pre=["prereg_check"], empty_table=True, fake="error")
    assert mo.traffic_light == "na"
    assert mo.summary_table["regcheck_deviations"].tolist() == [0]


def test_full_texts_are_sent() -> None:
    mo = run_reg(**_OER, fake="fulltext")
    sent = mo.table["paper_summary"].tolist()[0]
    assert sent == "We preregistered this study. It had 120 participants."
    assert mo.table["prereg_summary"].tolist()[0].startswith("template_name\n")
