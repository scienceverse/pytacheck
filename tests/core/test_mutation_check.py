"""The CI mutation check: a table edited in place after its Doc was built raises.

With ``METACHECK_CHECK_MUTATION=1`` a trusted scope fingerprints the text and
section tables of each paper it builds or serves a Doc for, and raises
``StaleDocumentError`` when a fingerprint changes. Off, nothing is checked (the
documented contract: modules must not edit papers in place, and the modules after
one that does keep using the Doc of the old values).
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest

import metacheck as pc
from metacheck.core import scope
from metacheck.core.doc import Doc
from metacheck.core.errors import StaleDocumentError
from metacheck.core.scope import trusted_scope
from metacheck.module import module
from metacheck.provenance import run_modules
from metacheck.report.report import report_module_run
from metacheck.text.search import text_search

SEEN: list[int] = []

SENTENCES = ["The data were simulated here.", "A power analysis was run.", "More simulated text."]


@module(title="Probe", description="counts what text_search finds")
def probe(paper: Any) -> dict[str, Any]:
    SEEN.append(len(text_search(paper, "simulated")))
    return {}


@module(title="Scrub", description="edits the text table in place")
def scrub(paper: Any) -> dict[str, Any]:
    paper.text.loc[:, "text"] = paper.text["text"].str.replace("simulated", "xxxxxxxxx")
    return {}


@module(title="Tidy", description="copies the paper before it edits it")
def tidy(paper: Any) -> dict[str, Any]:
    p = paper.copy()
    p.text.loc[:, "text"] = p.text["text"].str.replace("simulated", "xxxxxxxxx")
    return {"summary_text": str(len(text_search(p, "simulated")))}


@pytest.fixture(autouse=True)
def _quiet() -> Any:
    SEEN.clear()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        yield


@pytest.fixture
def check_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METACHECK_CHECK_MUTATION", "1")


@pytest.fixture
def check_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("METACHECK_CHECK_MUTATION", raising=False)
    monkeypatch.delenv("PYTACHECK_CHECK_MUTATION", raising=False)


def _paper() -> pc.Paper:
    p = pc.test_paper(SENTENCES)
    assert p._raw_records("text") is None  # materialised
    return p


def test_a_module_that_edits_a_paper_in_place_raises_at_the_next_module(check_on: None) -> None:
    with pytest.raises(StaleDocumentError, match="'scrub' edited"):
        run_modules(_paper(), [probe, scrub, probe])
    assert SEEN == [2]  # the second probe never ran


def test_the_last_module_is_named_too(check_on: None) -> None:
    with pytest.raises(StaleDocumentError, match="'scrub' edited"):
        run_modules(_paper(), [probe, scrub])


def test_the_pytacheck_name_is_read_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("METACHECK_CHECK_MUTATION", raising=False)
    monkeypatch.setenv("PYTACHECK_CHECK_MUTATION", "1")
    with pytest.raises(StaleDocumentError):
        run_modules(_paper(), [probe, scrub, probe])


def test_report_module_run_checks_too(check_on: None) -> None:
    with pytest.raises(StaleDocumentError, match="'scrub' edited"):
        report_module_run(_paper(), [probe, scrub, probe])


def test_without_the_check_the_later_module_keeps_the_old_values(check_off: None) -> None:
    chain = run_modules(_paper(), [probe, scrub, probe])
    assert [o.traffic_light for o in chain].count("fail") == 0
    assert SEEN == [2, 2]  # the documented contract: edits in place are not seen


@pytest.mark.parametrize("value", ["0", "off", "", "no"])
def test_other_values_leave_the_check_off(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("METACHECK_CHECK_MUTATION", value)
    monkeypatch.delenv("PYTACHECK_CHECK_MUTATION", raising=False)
    run_modules(_paper(), [probe, scrub, probe])
    assert SEEN == [2, 2]


def test_a_module_that_copies_first_passes(check_on: None) -> None:
    chain = run_modules(_paper(), [probe, tidy, probe])
    assert SEEN == [2, 2]
    assert chain[1].summary_text == "0"


def test_a_raw_paper_is_never_edited_in_place(check_on: None, fixtures_dir: Any) -> None:
    paper = pc.read(fixtures_dir / "psychsci/0956797614527830.json")
    assert paper._raw_records("text") is not None
    run_modules(paper, [probe, probe])
    assert paper._raw_records("text") is not None


def test_a_search_after_an_edit_raises_and_names_the_module(check_on: None) -> None:
    paper = _paper()
    with pytest.raises(StaleDocumentError, match="edited in place"), trusted_scope():
        text_search(paper, "simulated")
        paper.text.loc[:, "text"] = paper.text["text"].str.replace("simulated", "xxxxxxxxx")
        text_search(paper, "simulated")


def test_an_edit_of_the_section_table_raises(check_on: None) -> None:
    paper = _paper()
    with pytest.raises(StaleDocumentError), trusted_scope():
        text_search(paper, "simulated")
        paper.section.loc[:, "header"] = "Changed"
        text_search(paper, "simulated")


def test_an_edit_before_the_first_search_is_not_stale(check_on: None) -> None:
    paper = _paper()
    paper.text.loc[:, "text"] = paper.text["text"].str.replace("simulated", "xxxxxxxxx")
    with trusted_scope():
        assert len(text_search(paper, "simulated")) == 0


def test_replacing_a_table_is_not_an_edit(check_on: None) -> None:
    paper = _paper()
    with trusted_scope():
        assert len(text_search(paper, "simulated")) == 2
        paper.text = paper.text.assign(text=paper.text["text"].str.replace("simulated", "x"))
        assert len(text_search(paper, "simulated")) == 0


def test_the_scope_checks_when_it_ends(check_on: None) -> None:
    paper = _paper()
    with pytest.raises(StaleDocumentError), trusted_scope():
        text_search(paper, "simulated")
        paper.text.loc[:, "text"] = paper.text["text"].str.replace("simulated", "xxxxxxxxx")


def test_an_error_in_the_block_is_not_masked(check_on: None) -> None:
    paper = _paper()
    with pytest.raises(KeyError), trusted_scope():
        text_search(paper, "simulated")
        paper.text.loc[:, "text"] = paper.text["text"].str.replace("simulated", "xxxxxxxxx")
        raise KeyError("first")


def test_outside_a_scope_nothing_is_checked(check_on: None) -> None:
    paper = _paper()
    assert len(text_search(paper, "simulated")) == 2
    paper.text.loc[:, "text"] = paper.text["text"].str.replace("simulated", "xxxxxxxxx")
    assert len(text_search(paper, "simulated")) == 0  # rebuilt: a Doc outside a scope is not kept


def test_a_check_that_is_off_keeps_nothing(check_off: None) -> None:
    paper = _paper()
    with trusted_scope():
        text_search(paper, "simulated")
        assert Doc.of(paper) is Doc.of(paper)
        assert scope._WATCH.get() is None
