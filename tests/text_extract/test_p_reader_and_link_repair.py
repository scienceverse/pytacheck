"""The p-value reader and link repair of metacheck 0.4.4.3 (#642, #411, #458, #459)."""

from __future__ import annotations

import importlib

import pandas as pd
import pytest

from metacheck.stats import helpers as h
from metacheck.text import extract as ex

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


def _p(text: str) -> pd.DataFrame:
    return ex.extract_p_values(pd.DataFrame({"text": [text]}))


def test_a_correction_or_test_name_after_p_is_in_the_p_name_column() -> None:
    out = _p("The effect was significant (p_bonferroni = .03).")
    assert out["p_value"].tolist() == [0.03]
    assert out["p_comp"].tolist() == ["="]
    assert out["p_name"].tolist() == ["bonferroni"]
    assert ex._p_name(["pFWE < .05", "p adj = .03", "p = .05", "p_bonf = .1"]) == [
        "FWE",
        "adj",
        None,
        "bonf",
    ]


def test_p_values_in_scientific_notation_are_read() -> None:
    out = _p("p = 1e-5 and p=2.5e-3 and p-value = .04")
    assert out["p_value"].tolist() == [1e-05, 0.0025, 0.04]
    assert out["p_name"].isna().all()


def test_sci_text_writes_a_number_the_way_r_reads_it() -> None:
    assert h._sci_text(["1.5 x 10^-3", "1,5×10−3", "3e-05", "1.2e+3"]) == [
        "1.5e-3",
        "1,5e-3",
        "3e-05",
        "1.2e+3",
    ]
    assert h._sci_text(["10^-3"], ten_power=True) == ["1e-3"]


def test_reports_test_gives_one_answer_per_sentence() -> None:
    got = h._reports_test(["t(28) = 2.3, p = .03", "No numbers at all here."])
    assert len(got) == 2
    assert got[0] is True


@pytest.fixture(scope="module")
def read_module():  # type: ignore[no-untyped-def]
    return importlib.import_module("metacheck.io.read")


def test_a_link_split_by_a_space_is_joined(read_module) -> None:  # type: ignore[no-untyped-def]
    repair = read_module._repair_links
    assert repair("see https: //doi.org/10.5281/zenodo.123") == (
        "see https://doi.org/10.5281/zenodo.123"
    )
    out = repair(["plain text", None, "https: //osf.io/abcde"])
    assert out[0] == "plain text"
    assert out[1] is None
    assert out[2] == "https://osf.io/abcde"


def test_text_without_a_link_is_left_as_it_is(read_module) -> None:  # type: ignore[no-untyped-def]
    text = "We ran 24 trials per person; p = .03 (see Fig. 2)."
    assert read_module._repair_links(text) == text


def test_every_view_of_a_paper_read_from_tei_has_its_links_repaired() -> None:
    """The reference 'https: //doi.org/...' of the demo paper is joined in the records, the
    paper's table and the sentence table a search reads (which a Doc built before the repair
    must not feed)."""
    from pathlib import Path

    import metacheck as pc
    from metacheck.text.search import sentence_table

    xml = Path(__file__).resolve().parents[2] / "upstream/metacheck/inst/demos/to_err_is_human.xml"
    paper = pc.read(xml)
    raw = paper._raw_records("text")
    assert raw is not None
    assert not any("https: //" in (r.get("text") or "") for r in raw[0])
    assert not sentence_table(paper)[0]["text"].str.contains("https: //", regex=False).any()
    assert not paper.text["text"].str.contains("https: //", regex=False).any()
