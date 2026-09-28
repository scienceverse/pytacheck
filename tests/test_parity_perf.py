"""The work counters and CPU per (module, input) of parity/perf.py (for ``parity bench``)."""

from __future__ import annotations

import copy
import heapq
import itertools
import json
import os
import re
import sys
import textwrap
import time
import types
from pathlib import Path
from typing import Any

import pytest
import regex

import pytacheck as pc
import pytacheck.module as mod
import pytacheck.text.search as ts
from parity import perf
from pytacheck.datacheck import _files_readers as readers
from pytacheck.module import ModuleError, module, module_run, run_session

ROOT = Path(__file__).resolve().parent.parent
SPREADSHEETS = ROOT / "tests" / "mod_data_check" / "fixtures" / "repos" / "spreadsheets" / "data"
BASIC = ROOT / "tests" / "mod_data_check" / "fixtures" / "repos" / "basic"

_UNIQUE = itertools.count()


def unique() -> str:
    """A pattern no other test compiles."""
    return f"perf-{os.getpid()}-{next(_UNIQUE)}-[a-z]+"


@module(title="Works", description="d")
def works(paper, pattern="x"):
    pc.paper_table(paper, "text")
    copy.deepcopy({"a": [1, {"b": [2, 3]}]})  # one top-level call, several nested ones
    regex.compile(pattern)
    return {"summary_text": "ok"}


@module(title="Noop", description="d")
def noop(paper):
    return {}


@module(title="Outer", description="d")
def outer(paper):
    module_run(paper, works, pattern=unique())
    return {}


@module(title="Busy", description="d")
def busy(paper, fail=False):
    start = time.process_time()
    while time.process_time() - start < 0.05:
        pass
    if fail:
        raise RuntimeError("failed")
    return {}


@pytest.fixture(
    params=[
        pytest.param(
            True,
            id="sys.monitoring",
            marks=pytest.mark.skipif(sys.version_info < (3, 12), reason="new in 3.12"),
        ),
        pytest.param(False, id="rebinding"),
    ]
)
def monitoring(request: pytest.FixtureRequest) -> bool:
    return bool(request.param)


@pytest.fixture
def paper() -> pc.Paper:
    return pc.test_paper(["A sentence.", "Another sentence."])


def _counts(probe: perf.Probe, key: tuple[str | None, str | None]) -> dict[str, int]:
    [row] = [r for r in probe.rows() if (r["module"], r["input"]) == key]
    counts: dict[str, int] = row["counts"]
    return counts


def _without_timing(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """*rows* minus what varies between runs: CPU, recompiles, temporary files' names."""
    return [
        {
            **r,
            "cpu_ms": None,
            "counts": {c: n for c, n in r["counts"].items() if c != "regex_recompiles"},
            "files": {c: sorted(f.values()) for c, f in r["files"].items()},
        }
        for r in rows
    ]


def test_counts_and_cpu_per_module_and_input(monitoring: bool, paper: pc.Paper) -> None:
    with perf.Probe(monitoring=monitoring) as probe:
        assert probe.mechanism == ("sys.monitoring" if monitoring else "rebinding")
        module_run(paper, works)  # warm up
        module_run(paper, noop)
        probe.reset()
        module_run(paper, noop)
        probe.input = "b.xml"
        module_run(paper, works, pattern=unique())
        module_run(paper, works, pattern=unique())
        module_run(paper, busy)
    assert probe.missing == []
    rows = {(r["module"], r["input"]): r for r in probe.rows()}
    assert set(rows) == {("noop", paper.paper_id), ("works", "b.xml"), ("busy", "b.xml")}
    base = rows[("noop", paper.paper_id)]["counts"]  # what module_run itself does
    two = rows[("works", "b.xml")]
    assert two["runs"] == 2
    extra = {c: two["counts"][c] - 2 * base[c] for c in probe.counters}
    assert extra == {
        "code_decodes": 0,
        "csv_parses": 0,
        "deepcopy": 2,
        "file_opens": 0,
        "paper_table": 2,
        "regex_compiles": 2,
        "regex_recompiles": 0,
        "table_builds": 0,
        "text_search": 0,
        "workbook_parses": 0,
    }
    assert rows[("busy", "b.xml")]["cpu_ms"] >= 50
    total = probe.total()
    assert total.runs == 4
    assert total.cpu == pytest.approx(sum(u.cpu for u in probe.usage.values()))
    json.dumps(probe.rows())


def test_a_run_inside_a_run_counts_towards_the_outer_one(monitoring: bool, paper: pc.Paper) -> None:
    with perf.Probe(monitoring=monitoring) as probe:
        module_run(paper, works)
        probe.reset()
        module_run(paper, outer)
    [row] = probe.rows()
    assert (row["module"], row["runs"]) == ("outer", 1)
    assert row["counts"]["paper_table"] == 1 and row["counts"]["regex_compiles"] == 1


def test_a_failed_run_is_timed_and_the_next_one_is_top_level(
    monitoring: bool, paper: pc.Paper
) -> None:
    with perf.Probe(monitoring=monitoring) as probe:
        with pytest.raises(ModuleError):
            module_run(paper, busy, fail=True)
        module_run(paper, noop)
    rows = {r["module"]: r for r in probe.rows()}
    assert rows["busy"]["runs"] == 1 and rows["busy"]["cpu_ms"] >= 50
    assert rows["noop"]["runs"] == 1


def test_runs_in_a_session_and_on_outputs_keep_their_input(
    monitoring: bool, paper: pc.Paper
) -> None:
    with perf.Probe(monitoring=monitoring) as probe, run_session():
        out = module_run(paper, noop)
        module_run(out, works)  # a chain: the input is the output's paper
        module_run(paper, noop)  # a memo hit is a run too
    assert {(r["module"], r["input"], r["runs"]) for r in probe.rows()} == {
        ("noop", paper.paper_id, 2),
        ("works", paper.paper_id, 1),
    }


def test_work_outside_a_run_goes_to_module_none(monitoring: bool, paper: pc.Paper) -> None:
    with perf.Probe(monitoring=monitoring) as probe:
        ts.text_search(paper, "sentence")
        probe.input = "x"
        ts.text_search(paper, "sentence")
        ts.search_text(paper, "sentence")  # an alias of the same function
    assert _counts(probe, (None, None))["text_search"] == 1
    assert _counts(probe, (None, "x"))["text_search"] == 2


def test_only_top_level_deepcopies_count(monitoring: bool) -> None:
    nested: Any = {"a": [[1, 2], {"b": (3, [4])}]}
    with perf.Probe(monitoring=monitoring) as probe:
        copy.deepcopy(nested)
        copy.deepcopy(nested, {})  # a memo: part of a copy in progress
        copy.deepcopy(nested, memo=None)
    assert probe.total().counts["deepcopy"] == 2


def test_lazy_tables_count_once_when_built(monitoring: bool, fixtures_dir: Path) -> None:
    lazy = pc.read(fixtures_dir / "psychsci" / "0956797614527830.json")
    assert isinstance(lazy, pc.Paper) and lazy._raw_records("text") is not None
    with perf.Probe(monitoring=monitoring) as probe:
        first, second = lazy.text, lazy.text
    assert first is second
    assert probe.total().counts["table_builds"] == 1


def test_compiles_and_recompiles(monitoring: bool) -> None:
    a, b, c = unique(), unique(), unique()
    regex.compile(c)  # cached when the probe starts
    with perf.Probe(monitoring=monitoring) as probe:
        regex.compile(a)
        regex.compile(a)  # from regex's cache
        re.compile(b)
        re.compile(b)
        regex.purge()
        re.purge()
        regex.compile(a)
        re.compile(b)
        regex.compile(c)
    counts = probe.total().counts
    assert (counts["regex_compiles"], counts["regex_recompiles"]) == (2, 3)
    with perf.Probe(monitoring=monitoring) as probe:  # compiled under an earlier probe
        regex.purge()
        regex.compile(a)
    assert probe.total().counts["regex_recompiles"] == 1


def test_readers_count_per_file(monitoring: bool, tmp_path: Path) -> None:
    # through their modules: a function imported before the probe is not rebound
    from pytacheck.codecheck import core
    from pytacheck.datacheck import _files_fread, _files_readtable

    csv = tmp_path / "data.csv"
    csv.write_text("a,b\n1,2\n", encoding="utf-8")
    script = tmp_path / "analysis.R"
    script.write_text("x <- 1\n", encoding="utf-8")
    with perf.Probe(monitoring=monitoring) as probe:
        _files_fread.fread(str(csv), ",", True)
        _files_readtable.read_table(csv, ",", True)
        core.code_read(str(script))
        readers.read_excel(SPREADSHEETS / "clean.xlsx")
        readers.read_ods(SPREADSHEETS / "clean.ods")
    total = probe.total()
    assert dict(total.files["csv_parses"]) == {str(csv): 2}
    assert dict(total.files["code_decodes"]) == {str(script): 1}
    spreadsheets = "tests/mod_data_check/fixtures/repos/spreadsheets/data"
    assert dict(total.files["workbook_parses"]) == {
        f"{spreadsheets}/clean.xlsx": 1,
        f"{spreadsheets}/clean.ods": 1,
    }
    assert total.files["file_opens"][str(csv)] == 2
    assert total.files["file_opens"][f"{spreadsheets}/clean.xlsx"] >= 1


def test_both_ways_read_an_argument_alike(monitoring: bool) -> None:
    targets = [
        perf.Target("wrap", "textwrap:wrap", per_file="width"),
        perf.Target("merge", "heapq:merge", per_file="reverse"),  # keyword-only, after *args
    ]
    with perf.Probe(targets, opens=False, monitoring=monitoring) as probe:
        textwrap.wrap("a b", 5)
        textwrap.wrap("a b")  # left out: its default
        list(heapq.merge([1], [2], [3]))
    files = probe.total().files
    assert dict(files["wrap"]) == {"5": 1, "70": 1}
    assert dict(files["merge"]) == {"False": 1}


def test_file_opens_come_from_the_audit_hook(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("x", encoding="utf-8")
    with perf.Probe() as probe:
        path.read_bytes()
        with open(path, "rb"):
            pass
        os.close(os.open(path, os.O_RDONLY))  # not seen
    assert dict(probe.total().files["file_opens"]) == {str(path): 2}
    with perf.Probe(opens=False) as probe:
        path.read_bytes()
    assert "file_opens" not in probe.counters
    assert not probe.total().files


def test_rebinding_is_undone_everywhere(paper: pc.Paper) -> None:
    before = (ts.text_search, copy.deepcopy, mod.module_run, readers._XlsxBook.__init__)
    late = types.ModuleType("perf_late_import")
    with perf.Probe(monitoring=False) as probe:
        wrapped = ts.text_search
        assert wrapped is not before[0] and pc.text_search is wrapped
        assert readers._XlsxBook.__init__ is not before[3]
        late.text_search = ts.text_search  # a module imported meanwhile
        sys.modules[late.__name__] = late
    try:
        assert (ts.text_search, copy.deepcopy, mod.module_run, readers._XlsxBook.__init__) == (
            before
        )
        assert pc.text_search is before[0] and late.text_search is before[0]
        with perf.Probe(monitoring=False) as again:
            wrapped(paper, "sentence")  # a stale wrapper only calls through
        assert probe.total().counts["text_search"] == 0
        assert again.total().counts["text_search"] == 0
    finally:
        del sys.modules[late.__name__]


@pytest.mark.skipif(sys.version_info < (3, 12), reason="sys.monitoring is new in 3.12")
def test_monitoring_frees_its_tool_and_falls_back_when_none_is_free() -> None:
    mon = sys.monitoring
    free = [i for i in (3, 4) if mon.get_tool(i) is None]
    with perf.Probe() as probe:
        assert probe.mechanism == "sys.monitoring"
    assert [i for i in (3, 4) if mon.get_tool(i) is None] == free
    for i in free:
        mon.use_tool_id(i, "test")
    try:
        with perf.Probe() as probe:
            assert probe.mechanism == "rebinding"
            copy.deepcopy([1])
        assert probe.total().counts["deepcopy"] == 1
    finally:
        for i in free:
            mon.free_tool_id(i)


def test_targets_that_do_not_resolve_are_listed_and_their_counters_left_out() -> None:
    targets = [
        perf.Target("a", "no_such_module_here:f"),
        perf.Target("b", "copy:no_such_function"),
        perf.Target("c", "copy:deepcopy", per_file="no_such_argument"),
        perf.Target("b", "copy:copy"),  # resolves, but "b" would count part of its work
        perf.Target("d", "copy:deepcopy"),
    ]
    with perf.Probe(targets, opens=False) as probe:
        copy.copy([1])
        copy.deepcopy([1])
    assert probe.missing == [t.where for t in targets[:3]]
    assert probe.counters == ("d",)
    assert "b" not in probe.total().counts
    assert probe.total().counts["d"] >= 1


def test_one_probe_at_a_time() -> None:
    with perf.Probe() as probe:
        with pytest.raises(RuntimeError, match="another Probe"):
            perf.Probe().__enter__()
        copy.deepcopy([1])
    assert probe.total().counts["deepcopy"] == 1


def test_warm_counts_repeat_on_real_modules(monitoring: bool, fixtures_dir: Path) -> None:
    paper = pc.read(fixtures_dir / "psychsci" / "0956797614527830.json")
    assert isinstance(paper, pc.Paper)
    dict(paper.items())

    def run() -> None:
        for name in ("marginal", "stat_p_exact"):
            module_run(paper, name)
        probe.input = "basic"
        for name in ("code_check", "data_check"):
            module_run(pc.demopaper(), name, local_path=str(BASIC), local_only=True)
        probe.input = None

    with perf.Probe(monitoring=monitoring) as probe:
        run()
        probe.reset()
        run()
        first = probe.rows()
        probe.reset()
        run()
        second = probe.rows()
    assert _without_timing(first) == _without_timing(second)
    rows = {(r["module"], r["input"]): r for r in first}
    assert rows[("marginal", paper.paper_id)]["counts"]["text_search"] > 0
    assert rows[("stat_p_exact", paper.paper_id)]["counts"]["regex_compiles"] == 0
    data = rows[("data_check", "basic")]["files"]
    assert data["csv_parses"]["tests/mod_data_check/fixtures/repos/basic/data/study.csv"] >= 1
    code = rows[("code_check", "basic")]["files"]
    assert code["code_decodes"]["tests/mod_data_check/fixtures/repos/basic/analysis.R"] == 1
