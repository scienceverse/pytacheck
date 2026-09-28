"""An in-place edit of ``p.text`` is seen by the next run (ARCHITECTURE.md §2.2, H0-9).

No cache may serve a paper's old text after an in-place edit, so a user who
edits a table in place and runs again gets fresh results. Inside ``run_session()`` the same holds
for ``text_search`` and for a module not run yet, while a repeated
``module_run`` returns its memo: edits made inside a table are not seen, as
``run_session``'s docstring and docs/MODULES.md say. The paper comes in three
forms: raw JSON records that the edit builds, a table built before the first
run, and the DataFrames that metacheck's older TEI conversion reads.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import pytacheck as pc
from parity.cases import metacheck_defaults
from pytacheck.module import module_run, run_session

NEW = "This study was approved by the institutional review board zzqq (https://osf.io/zzqq/)."
FORMS = ["raw", "materialised", "metacheck_defaults"]


@pytest.fixture(params=FORMS)
def paper(request: pytest.FixtureRequest, fixtures_dir: Path) -> pc.Paper:
    path = fixtures_dir / "debruine" / "debruine-fret.xml"
    if request.param == "metacheck_defaults":
        with metacheck_defaults():
            paper = pc.read(path)
    else:
        paper = pc.read(path)
        if request.param == "materialised":
            _ = paper.text
    # the form this case is about: raw records, or a DataFrame from the start
    assert (paper._raw_records("text") is not None) == (request.param == "raw")
    return paper


def edit(paper: pc.Paper) -> None:
    """Change one sentence in place (a raw paper builds its table first)."""
    paper.text.loc[paper.text.index[1], "text"] = NEW


def seen(paper: pc.Paper, module: str) -> bool:
    """Whether *module*'s table shows the edited sentence."""
    table = module_run(paper, module).table
    return bool(table["text"].str.contains("zzqq").any())


def test_module_run_and_text_search_see_the_edit(paper: pc.Paper) -> None:
    assert not seen(paper, "ethics_check")
    assert len(pc.text_search(paper, "zzqq")) == 0
    edit(paper)
    assert seen(paper, "ethics_check")
    assert len(pc.text_search(paper, "zzqq")) == 1


def test_run_session_sees_the_edit_except_in_its_memo(paper: pc.Paper) -> None:
    with run_session() as session:
        assert not seen(paper, "ethics_check")
        assert len(pc.text_search(paper, "zzqq")) == 0
        edit(paper)
        assert len(pc.text_search(paper, "zzqq")) == 1
        assert seen(paper, "all_urls")  # not run yet in this session
        # the documented contract: the repeated run is a memo hit and misses the edit
        assert not seen(paper, "ethics_check"), (
            "a repeated module_run inside run_session() saw an in-place edit; if that is "
            "intended, change run_session()'s docstring, docs/MODULES.md and this test together"
        )
        assert (session.hits, session.misses) == (1, 2)
    assert seen(paper, "ethics_check")


def test_run_session_sees_a_table_set_through_the_api(paper: pc.Paper) -> None:
    with run_session() as session:
        assert not seen(paper, "ethics_check")
        text = paper.text.copy()
        text.loc[text.index[1], "text"] = NEW
        paper.text = text
        assert seen(paper, "ethics_check")
        assert (session.hits, session.misses) == (0, 2)
