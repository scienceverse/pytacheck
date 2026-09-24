"""run_session() memoisation and pc.use()."""

from __future__ import annotations

import pandas as pd
import pytest

import pytacheck as pc
from pytacheck.module import ModuleError, module, module_run, run_session, use, use_setting

CALLS: list[dict] = []


@module(title="Counted", description="d")
def counted(paper, k=1, opts=None):
    CALLS.append({"paper": paper.paper_id if isinstance(paper, pc.Paper) else "list", "k": k})
    return {
        "summary_text": f"k={k}",
        "table": pd.DataFrame({"k": [k]}),
        "summary_table": pd.DataFrame({"paper_id": [getattr(paper, "paper_id", None)], "k": [k]}),
    }


FAIL_NEXT = {"n": 1}


@module(title="Flaky", description="d")
def flaky(paper):
    CALLS.append({"flaky": True})
    if FAIL_NEXT["n"] > 0:
        FAIL_NEXT["n"] -= 1
        raise RuntimeError("transient")
    return {"summary_text": "fine"}


@pytest.fixture(autouse=True)
def _reset() -> None:
    CALLS.clear()
    FAIL_NEXT["n"] = 1


def test_outside_a_session_nothing_is_memoised(paper) -> None:
    module_run(paper, counted)
    module_run(paper, counted)
    assert len(CALLS) == 2


def test_hits_misses_and_bound_defaults(paper) -> None:
    with run_session() as session:
        a = module_run(paper, counted)
        b = module_run(paper, counted, k=1)  # spelled-out default: same entry
        c = module_run(paper, counted, k=2)
        d = module_run(paper, counted, opts={"x": [1, 2]})
        e = module_run(paper, counted, opts={"x": [1, 2]})
        f = module_run(paper, counted, opts={"x": [1, 3]})
    assert len(CALLS) == 4
    assert (session.hits, session.misses) == (2, 4)
    assert b is not a and b.summary_text == a.summary_text == "k=1"
    assert b.table is not a.table and b.table.equals(a.table)
    assert c.summary_text == "k=2" and d is not e and f.summary_text == "k=1"
    assert b.provenance == a.provenance and b.provenance is not a.provenance


def test_hit_copies_do_not_leak(paper) -> None:
    with run_session():
        a = module_run(paper, counted)
        a.table.loc[0, "k"] = 99  # mutating an output must not poison the cache
        a.extras["new"] = 1
        b = module_run(paper, counted)
    assert b.table.loc[0, "k"] == 1
    assert "new" not in b.extras


def test_types_are_not_conflated(paper) -> None:
    with run_session():
        module_run(paper, counted, k=1)
        module_run(paper, counted, k=True)
        module_run(paper, counted, k=1.0)
    assert len(CALLS) == 3


def test_paper_mutation_invalidates(paper) -> None:
    with run_session() as session:
        module_run(paper, counted)
        paper["info"] = paper["info"].assign(title="changed")
        module_run(paper, counted)
        paper.paper_id = "renamed"
        out = module_run(paper, counted)
        other = paper.copy()
        module_run(other, counted)
    assert len(CALLS) == 4 and session.hits == 0
    assert out.summary_table["paper_id"].tolist() == ["renamed"]


def test_paperlists_are_memoised_by_members(paper) -> None:
    plist = pc.PaperList([paper, pc.test_paper("another")])
    with run_session() as session:
        module_run(plist, counted)
        module_run(plist, counted)
        plist[1]["text"] = plist[1]["text"]
        module_run(plist, counted)
    assert len(CALLS) == 2 and session.hits == 1


def test_chained_outputs_and_labels(paper) -> None:
    with run_session() as session:
        first = module_run(paper, counted)
        module_run(first, counted)  # chained input: never memoised
        module_run(first, counted)
    assert len(CALLS) == 3 and session.hits == 0


def test_failures_are_not_cached(paper) -> None:
    with run_session() as session:
        with pytest.raises(ModuleError, match="transient"):
            module_run(paper, flaky)
        assert module_run(paper, flaky).summary_text == "fine"
        assert module_run(paper, flaky).summary_text == "fine"
    assert len(CALLS) == 2 and session.hits == 1


def test_nested_sessions_share_and_end(paper) -> None:
    with run_session() as outer:
        module_run(paper, counted)
        with run_session() as inner:
            assert inner is outer
            module_run(paper, counted)
    module_run(paper, counted)
    assert len(CALLS) == 2 and outer.hits == 1


def test_builtin_module_memo_by_file(paper) -> None:
    with run_session() as session:
        a = module_run(paper, "marginal")
        b = module_run(paper, "metacheck::marginal")
        c = module_run(paper, "marginal")
    assert session.hits == 2  # same label, same module identity
    assert a.module == b.module == c.module == "marginal"
    pd.testing.assert_frame_equal(a.table, c.table)


def test_local_file_reexecuted_but_memoised(ms, paper) -> None:
    from tests.modsys.helpers import mod_src

    (ms.work / "loc.py").write_text(mod_src("loc", "loc"))
    with run_session() as session:
        module_run(paper, "loc")
        module_run(paper, "loc")
    assert session.hits == 1


def test_use_is_scoped() -> None:
    assert use_setting("preset") is None
    with use("a", offline=True):
        assert use_setting("preset") == "a" and use_setting("offline") is True
        with use(allow_local=False):
            assert use_setting("preset") == "a" and use_setting("allow_local") is False
        assert use_setting("allow_local") is None
    assert use_setting("offline") is None
    assert pc.use is use and pc.run_session is run_session
