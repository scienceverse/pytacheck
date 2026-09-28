"""The snapshot format, the cases, the fixtures, the oracle and the recorder (package SNAP).

The committed snapshots themselves are checked at the end: each set matches its
INDEX, and (slow, Python 3.12 on Linux only) still reproduces on this tree.
"""

from __future__ import annotations

import functools
import importlib.util
import json
import lzma
import socket
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd
import pytest

from parity import accuracy as A
from parity.canonical import portable
from tests.snapshots import inputs, oracle, store

SCRIPT = store.ROOT / "scripts" / "record_snapshots.py"
RECORDING_PLATFORM = sys.version_info[:2] == (3, 12) and sys.platform == "linux"


@functools.cache
def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("record_snapshots", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def script() -> ModuleType:
    return _script()


def _frame(**columns: object) -> pd.DataFrame:
    return pd.DataFrame(columns)


# -- records ----------------------------------------------------------------------------


def test_envelope_keeps_the_dtypes_the_encoding_drops() -> None:
    a = store.envelope(_frame(x=pd.array([1, 2], dtype="Int64")))
    b = store.envelope(_frame(x=np.array([1, 2], dtype="int64")))
    assert a["value"] == b["value"]
    assert a["frames"] == {"$": {"dtypes": ["Int64"]}}
    assert b["frames"] == {"$": {"dtypes": ["int64"]}}


def test_envelope_keeps_the_missing_value_of_object_columns() -> None:
    none = store.envelope(_frame(x=pd.Series(["a", None], dtype=object)))
    nan = store.envelope(_frame(x=pd.Series(["a", np.nan], dtype=object)))
    mixed = store.envelope(_frame(x=pd.Series([None, "a", np.nan, pd.NA], dtype=object)))
    assert none["value"] == nan["value"]
    assert none["frames"]["$"]["na"] == [["x", "None"]]
    assert nan["frames"]["$"]["na"] == [["x", "NaN"]]
    assert mixed["frames"]["$"]["na"] == [["x", ["None", "NaN", "NA"]]]


def test_envelope_keeps_an_index_other_than_the_default() -> None:
    assert "index" not in store.envelope(_frame(x=[1, 2]))["frames"]["$"]
    moved = store.envelope(_frame(x=[1, 2]).set_axis([5, 6]))["frames"]["$"]
    assert moved["index"] == {"dtype": "int64", "values": {"t": "int", "v": [5, 6]}}
    named = store.envelope(_frame(x=[1, 2]).rename_axis("row"))["frames"]["$"]
    assert named["index"]["names"] == ["row"]


def test_envelope_finds_the_frames_inside_module_outputs_and_lists() -> None:
    from pytacheck.module import ModuleOutput

    out = ModuleOutput("m", "M", "general", table=_frame(x=[1]), summary_table=_frame(id=["a"]))
    frames = store.envelope(out)["frames"]
    assert set(frames) == {"$.table", "$.summary_table"}
    series = store.envelope([pd.Series([1.5], name="s"), {"k": _frame(y=[True])}])["frames"]
    assert series == {"$[0]": {"dtype": "float64", "name": "s"}, "$[1].k": {"dtypes": ["bool"]}}


def test_a_value_without_frames_has_no_frames_entry() -> None:
    assert store.envelope({"a": [1, 2]}) == {
        "value": {"t": "list", "names": ["a"], "v": [{"t": "int", "v": [1, 2]}]}
    }


def test_an_error_is_recorded_with_the_checkout_written_repo() -> None:
    def fails() -> None:
        raise ValueError(f"no file {store.ROOT.as_posix()}/x.xml")

    assert store.outcome(fails) == {"ok": False, "error": "ValueError: no file <repo>/x.xml"}


# -- sets on disk -------------------------------------------------------------------------


def _records() -> dict[str, store.Record]:
    same = {"ok": True, "value": {"t": "chr", "v": ["é", "b"]}}
    return {
        "m.b": same,
        "m.a": same,
        "n.a": {"ok": False, "error": "ValueError: no"},
    }


GROUPS = {"m.a": "m", "m.b": "m", "n.a": "n"}


@pytest.mark.parametrize("layout", store.LAYOUTS)
def test_a_set_reads_back_as_written(tmp_path: Path, layout: str) -> None:
    store.write_set(tmp_path, "s", layout, _records(), GROUPS)
    assert store.read_set(tmp_path, "s") == _records()
    assert store.read_index(tmp_path, "s") == {i: store.digest(r) for i, r in _records().items()}
    assert store.committed_sets(tmp_path) == ["s"]


def test_the_index_lists_digest_size_and_id_sorted_by_id(tmp_path: Path) -> None:
    store.write_set(tmp_path, "s", "json", _records(), GROUPS)
    lines = (tmp_path / "s" / "INDEX").read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("#")
    rows = [line.split() for line in lines[1:]]
    assert [r[2] for r in rows] == ["m.a", "m.b", "n.a"]
    assert rows[0][0] == store.digest(_records()["m.a"])
    assert int(rows[0][1]) == len(store.dumps(_records()["m.a"]))


def test_the_json_layout_has_one_indented_file_per_group(tmp_path: Path) -> None:
    store.write_set(tmp_path, "s", "json", _records(), GROUPS)
    assert sorted(p.name for p in (tmp_path / "s").iterdir()) == ["INDEX", "m.json", "n.json"]
    text = (tmp_path / "s" / "m.json").read_text(encoding="utf-8")
    assert '\n    "é",\n' in text  # one scalar per line, not escaped
    assert list(json.loads(text)) == ["m.a", "m.b"]


def test_the_xz_layout_stores_each_distinct_record_once(tmp_path: Path) -> None:
    store.write_set(tmp_path, "s", "xz", _records(), GROUPS)
    assert sorted(p.name for p in (tmp_path / "s").iterdir()) == ["INDEX", store.XZ_FILE]
    lines = lzma.decompress((tmp_path / "s" / store.XZ_FILE).read_bytes()).splitlines()
    assert [json.loads(line)["digest"] for line in lines] == [
        store.digest(_records()["m.a"]),
        store.digest(_records()["n.a"]),
    ]


@pytest.mark.parametrize("layout", store.LAYOUTS)
def test_writing_is_byte_stable(tmp_path: Path, layout: str) -> None:
    store.write_set(tmp_path / "1", "s", layout, _records(), GROUPS)
    shuffled = dict(reversed(list(_records().items())))
    store.write_set(tmp_path / "2", "s", layout, shuffled, GROUPS)
    for name in ("INDEX", "m.json", store.XZ_FILE):
        one, two = tmp_path / "1" / "s" / name, tmp_path / "2" / "s" / name
        assert one.exists() == two.exists()
        if one.exists():
            assert one.read_bytes() == two.read_bytes()


def test_a_new_recording_removes_the_files_it_does_not_write(tmp_path: Path) -> None:
    store.write_set(tmp_path, "s", "json", _records(), GROUPS)
    (tmp_path / "s" / "notes.txt").write_text("kept", encoding="utf-8")
    only_m = {i: r for i, r in _records().items() if i.startswith("m.")}
    store.write_set(tmp_path, "s", "json", only_m, GROUPS)
    assert sorted(p.name for p in (tmp_path / "s").iterdir()) == ["INDEX", "m.json", "notes.txt"]
    store.write_set(tmp_path, "s", "xz", only_m, GROUPS)
    assert sorted(p.name for p in (tmp_path / "s").iterdir()) == [
        "INDEX",
        "notes.txt",
        store.XZ_FILE,
    ]


def test_a_hand_edited_record_does_not_load(tmp_path: Path) -> None:
    store.write_set(tmp_path, "s", "json", _records(), GROUPS)
    path = tmp_path / "s" / "n.json"
    path.write_text(
        path.read_text(encoding="utf-8").replace("ValueError: no", "ValueError: yes"),
        encoding="utf-8",
    )
    with pytest.raises(store.SnapshotError, match="do not match their INDEX"):
        store.read_set(tmp_path, "s")


def test_a_record_missing_from_the_files_does_not_load(tmp_path: Path) -> None:
    store.write_set(tmp_path, "s", "json", _records(), GROUPS)
    (tmp_path / "s" / "n.json").unlink()
    with pytest.raises(store.SnapshotError, match="list different cases"):
        store.read_set(tmp_path, "s")


def test_a_set_that_was_never_recorded_says_so(tmp_path: Path) -> None:
    with pytest.raises(store.SnapshotError, match="no snapshot set 'modules'"):
        store.read_set(tmp_path, "modules")


def test_a_lone_surrogate_survives_the_round_trip(tmp_path: Path) -> None:
    records = {
        "a": {"ok": True, "value": {"t": "chr", "v": [b"\xff".decode("utf-8", "surrogateescape")]}}
    }
    store.write_set(tmp_path, "s", "json", records, {"a": "g"})
    assert store.read_set(tmp_path, "s") == records


# -- the cases ---------------------------------------------------------------------------


def test_the_sets_have_the_pinned_number_of_cases() -> None:
    modules = inputs.SETS["modules"].cases()
    n_paper = len(inputs.PAPER_MODULES)
    assert (
        len(modules)
        == n_paper * len(inputs.PAPER_INPUTS)
        + len(inputs.REPOSITORY_MODULES) * len(inputs.REPOSITORY_INPUTS)
        + n_paper * 3
    )
    assert len(modules) == 496
    assert len(inputs.SETS["fixtures"].cases()) == 4 * n_paper == 76


def test_the_matrix_cases_carry_the_accuracy_reports_ids() -> None:
    ids = set(oracle.cases("modules"))
    assert "ethics_check.debruine--debruine-fret.xml" in ids
    assert "data_check.repos--basic" in ids
    assert {"ethics_check.demo", "ethics_check.psychsci_list", "ethics_check.list3"} <= ids
    assert oracle.cases("modules", module="code_check") == [
        A.Output("code_check", p, "repository").id for p in inputs.REPOSITORY_INPUTS
    ]


def test_the_pinned_inputs_exist(upstream_dir: Path) -> None:
    paths = [
        *inputs.PAPER_INPUTS,
        *inputs.REPOSITORY_INPUTS,
        *inputs.LIST3,
        inputs.PSYCHSCI_LIST,
        inputs.FRET,
        inputs.CHILD,
    ]
    assert [p for p in paths if not (store.ROOT / p).exists()] == []


def test_case_ids_must_be_unique_and_without_spaces() -> None:
    def call() -> None:
        return None

    twice = inputs.SnapshotSet("t", "json", lambda: [inputs.Case("a", "g", call)] * 2)
    spaced = inputs.SnapshotSet("t", "json", lambda: [inputs.Case("a b", "g", call)])
    for s in (twice, spaced):
        with pytest.raises(ValueError, match="repeated, empty or has spaces"):
            s.cases()


def test_an_unknown_case_is_a_key_error() -> None:
    with pytest.raises(KeyError, match="no case"):
        inputs.find("modules", "nope")
    with pytest.raises(KeyError, match="no snapshot set"):
        inputs.find("nope", "x")


def test_a_case_that_tries_the_network_is_marked_as_a_problem() -> None:
    case = inputs.Case("net", "g", lambda: socket.getaddrinfo("example.org", 443))
    record = inputs.record(case)
    assert record["ok"] is False
    assert record["problem"].startswith("used the network")


def _with_slashes(record: store.Record, path: str) -> store.Record:
    """*record* with the repository *path* written with "/", as the cases pass it: the
    accuracy report passes it as the OS writes it (with "\\" on Windows)."""
    as_given = json.dumps(portable(str(store.ROOT / path)))[1:-1]
    with_slashes = json.dumps(portable((store.ROOT / path).as_posix()))[1:-1]
    mapped: store.Record = json.loads(json.dumps(record).replace(as_given, with_slashes))
    return mapped


def test_repository_cases_pass_the_folder_with_slashes() -> None:
    case = inputs.find("modules", "data_check.repos--basic")
    assert isinstance(case.call, functools.partial)
    _module, _make, args = case.call.args
    assert (
        args["local_path"] == (store.ROOT / "tests/mod_data_check/fixtures/repos/basic").as_posix()
    )


@pytest.mark.slow
@pytest.mark.parametrize("module", [*inputs.PAPER_MODULES, *inputs.REPOSITORY_MODULES])
def test_the_matrix_cases_equal_the_accuracy_reports_outputs(
    upstream_dir: Path, module: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each case reads its own paper; ``parity accuracy`` shares one per input.
    Rows added to the matrix later are not cases (the inputs are pinned)."""
    monkeypatch.setenv("PYTACHECK_CACHE_DIR", str(tmp_path))  # a folder that exists
    pinned = set(oracle.cases("modules", module=module))
    outputs = [o for o in A.load_matrix() if o.module == module and o.id in pinned]
    assert outputs
    theirs = A.run_python(outputs)
    for o in outputs:
        mine = inputs.record(inputs.find("modules", o.id))
        expected = theirs[o]
        if o.kind == "repository":
            expected = _with_slashes(expected, o.input)
        assert mine["ok"] == expected["ok"], o.id
        assert mine.get("value") == expected.get("value"), o.id
        assert mine.get("error") == expected.get("error"), o.id


# -- the fixtures -------------------------------------------------------------------------


def test_the_same_id_fixture_names_two_distinct_papers_alike(upstream_dir: Path) -> None:
    with inputs.context([], []):
        same = inputs.same_id_list()
        resolved = inputs.same_id_list(("X", "X~2"))
        fret, child = inputs.read(inputs.FRET), inputs.read(inputs.CHILD)
    assert same.names == ["X", "X"]
    assert resolved.names == ["X", "X~2"]
    assert [p.title for p in same] == [fret.title, child.title]
    assert fret.title != child.title


def test_the_duplicate_paragraph_fixture_repeats_one_body_paragraph_exactly(
    upstream_dir: Path,
) -> None:
    with inputs.context([], []):
        plain = inputs.read(inputs.FRET)
        doubled = inputs.duplicate_paragraph(inputs.read(inputs.FRET))
    before, after = plain.text, doubled.text
    rows = before.index[(before["paragraph_id"] == inputs.DUPLICATE_PARAGRAPH).fillna(False)]
    assert len(after) == len(before) + len(rows)
    last = rows[-1]
    repeated = after.iloc[last + 1 : last + 1 + len(rows)].reset_index(drop=True)
    pd.testing.assert_frame_equal(repeated, before.loc[rows].reset_index(drop=True))
    pd.testing.assert_frame_equal(
        after.drop(index=range(last + 1, last + 1 + len(rows))).reset_index(drop=True), before
    )
    section = before.loc[rows[0], "section_id"]
    kinds = plain.section.set_index("section_id")["section_type"]
    assert kinds[section] == "results"  # a body section, not the references
    assert before.loc[rows, "text"].str.contains(r"\bp = ").any()
    for name in list(plain.keys()):
        if name != "text":
            pd.testing.assert_frame_equal(doubled[name], plain[name])


def test_duplicate_paragraph_rejects_a_paragraph_that_is_not_there(upstream_dir: Path) -> None:
    with inputs.context([], []), pytest.raises(ValueError, match="not one block"):
        inputs.duplicate_paragraph(inputs.read(inputs.FRET), 10_000)


def test_the_duplicate_paragraph_fixture_expects_the_plain_papers_output(
    upstream_dir: Path,
) -> None:
    expected = inputs.record(inputs.find("fixtures", "all_p_values.duplicate_paragraph"))
    plain = inputs.record(inputs.find("modules", "all_p_values.debruine--debruine-fret.xml"))
    assert expected == plain
    before = inputs.find("fixtures", "all_p_values.duplicate_paragraph.before")
    assert before.expect is None  # base's own output on the doubled paper


def test_the_same_id_fixture_expects_the_output_under_f6s_ids(upstream_dir: Path) -> None:
    expected = inputs.record(inputs.find("fixtures", "all_p_values.same_id"))
    before = inputs.record(inputs.find("fixtures", "all_p_values.same_id.before"))
    assert "X~2" in store.dumps(expected).decode()
    assert "X~2" not in store.dumps(before).decode()
    # what the rewrite runs is the list with the repeated id, in both cases
    case = inputs.find("fixtures", "all_p_values.same_id")
    assert inputs.run(case) == before


def test_fixture_gaps_read_the_resolved_id_as_the_repeated_one() -> None:
    def rec(*v: str) -> store.Record:
        return {"ok": True, "value": {"t": "chr", "v": list(v)}}

    records = {
        "all_p_values.same_id": rec("X", "X~2"),
        "all_p_values.same_id.before": rec("X", "X"),
        "ethics_check.same_id": rec("X", "X~2", "3 rows"),
        "ethics_check.same_id.before": rec("X", "X", "2 rows"),
        "marginal.duplicate_paragraph": rec("6"),
        "marginal.duplicate_paragraph.before": rec("7"),
    }
    assert inputs.fixture_gaps(records) == {
        "same_id": ["ethics_check"],
        "duplicate_paragraph": ["marginal"],
    }


# -- the oracle --------------------------------------------------------------------------


CASE = "all_urls.debruine--debruine-fret.xml"


@pytest.fixture
def recorded(tmp_path: Path, upstream_dir: Path) -> Path:
    """A snapshot root holding the modules set's CASE only."""
    case = inputs.find("modules", CASE)
    store.write_set(tmp_path, "modules", "json", {CASE: inputs.record(case)}, {CASE: case.group})
    return tmp_path


def _tampered(root: Path) -> None:
    path = root / "modules" / "all_urls.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    table = data[CASE]["value"]["v"][data[CASE]["value"]["names"].index("table")]
    table["v"][table["names"].index("text")]["v"][0] = "changed"
    store.write_set(root, "modules", "json", data, {CASE: "all_urls"})


def test_the_oracle_passes_the_same_tree(recorded: Path) -> None:
    oracle.check("modules", CASE, root=recorded)
    with inputs.context([], []):
        value = inputs.module_call("all_urls", functools.partial(inputs.read, inputs.FRET))()
    oracle.assert_equal("modules", CASE, value, root=recorded)
    assert oracle.load("modules", CASE, root=recorded)["ok"] is True


def test_the_oracle_names_the_changed_cell(recorded: Path) -> None:
    _tampered(recorded)
    with pytest.raises(AssertionError) as info:
        oracle.check("modules", CASE, root=recorded)
    message = str(info.value)
    assert message.startswith(f"modules: {CASE} differs from its snapshot (exact):")
    assert "value.table.text[0]: expected 'changed', got " in message


def test_the_oracle_compares_dtypes_too(recorded: Path) -> None:
    with inputs.context([], []):
        value = inputs.module_call("all_urls", functools.partial(inputs.read, inputs.FRET))()
    value.table = value.table.astype({"text_id": "float64"})
    with pytest.raises(
        AssertionError,
        match=r"frames\['\$\.table'\]\.dtypes\[\d+\]: expected 'Int64', got 'float64'",
    ):
        oracle.assert_equal("modules", CASE, value, root=recorded)


def test_canon_is_not_there_until_harness_v2_registers_it(recorded: Path) -> None:
    with pytest.raises(KeyError, match="H0-17"):
        oracle.check("modules", CASE, through="canon", root=recorded)


def test_a_registered_comparator_is_used_and_the_raw_differences_listed(
    recorded: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tampered(recorded)
    monkeypatch.setattr(oracle, "COMPARATORS", dict(oracle.COMPARATORS))
    oracle.register_comparator("lenient", lambda case, e, a: [])
    oracle.check("modules", CASE, through="lenient", root=recorded)
    oracle.register_comparator("strict", lambda case, e, a: [f"{case.module}: no"])
    with pytest.raises(AssertionError) as info:
        oracle.check("modules", CASE, through="strict", root=recorded)
    assert "all_urls: no" in str(info.value)
    assert "raw differences:\n  value.table.text[0]" in str(info.value)


def test_a_case_without_a_snapshot_is_a_key_error(recorded: Path) -> None:
    with pytest.raises(KeyError, match="no snapshot of"):
        oracle.load("modules", "all_urls.demo", root=recorded)


def _df(names: list[str], *columns: list[object]) -> dict[str, object]:
    return {
        "t": "df",
        "nrow": len(columns[0]) if columns else 0,
        "names": names,
        "v": [{"t": "chr", "v": c} for c in columns],
    }


@pytest.mark.parametrize(
    ("a", "b", "found"),
    [
        (_df(["x"], ["a", "b"]), _df(["x"], ["a", "c"]), ["x[1]: expected 'b', got 'c'"]),
        (
            _df(["x"], ["a"]),
            _df(["x"], ["a", "b"]),
            ["nrow: expected 1, got 2", "x: expected 1 items, got 2"],
        ),
        (
            _df(["x", "y"], ["a"], ["b"]),
            _df(["y"], ["c"]),
            ["(names): expected ['x', 'y'], got ['y']", "y[0]: expected 'b', got 'c'"],
        ),
        (
            {"t": "int", "v": [1]},
            {"t": "dbl", "v": [1.0]},
            ["(type): expected int, got dbl", "[0]: expected 1, got 1.0"],
        ),
        ({"ok": True}, {"ok": True, "frames": {}}, ["frames: unexpected, {}"]),
        ({"a b": 1}, {"a b": 2}, ["['a b']: expected 1, got 2"]),
    ],
)
def test_differences_are_paths_into_the_encoding(a: object, b: object, found: list[str]) -> None:
    assert [d.replace("the record", "").lstrip() for d in oracle.differences(a, b)] == found


def test_differences_stop_at_the_limit() -> None:
    a, b = {"t": "int", "v": list(range(50))}, {"t": "int", "v": [-1] * 50}
    found = oracle.differences(a, b, limit=5)
    assert len(found) == 6
    assert found[-1] == "..."


# -- the recorder -----------------------------------------------------------------------


def test_list_shows_every_set(script: ModuleType, capsys: pytest.CaptureFixture[str]) -> None:
    assert script.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "modules" in out
    assert "fixtures" in out
    assert "496 cases" in out


def test_the_recorder_writes_git_only_under_the_recording_python(
    script: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "SNAPSHOT_DIR", tmp_path)  # were it to write after all
    monkeypatch.setattr(script, "_on_recording_platform", lambda: False)
    with pytest.raises(SystemExit, match=r"uv run --python 3\.12"):
        script.main(["--only", "fixtures"])
    with pytest.raises(SystemExit, match="--cases records part of a set"):
        script.main(["--only", "fixtures", "--cases", "all_urls.*"])


def test_the_recorder_writes_git_only_with_every_extra_installed(
    script: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "SNAPSHOT_DIR", tmp_path)  # were it to write after all
    monkeypatch.setattr(script, "_on_recording_platform", lambda: True)
    monkeypatch.setattr(script, "_missing_extras", lambda: ["xlrd"])
    with pytest.raises(SystemExit, match=r"xlrd missing: run `uv run --python 3\.12 --all-extras"):
        script.main(["--only", "fixtures"])


def test_missing_extras_are_the_optional_dependencies_not_installed(
    script: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib.metadata

    requires = ["numpy>=1.26", "pytest>=8; extra == 'data'", "no-such-dist[x]>=1; extra == 'data'"]
    monkeypatch.setattr(importlib.metadata, "requires", lambda name: requires)
    assert script._missing_extras() == ["no-such-dist"]


def test_an_unknown_set_is_refused(script: ModuleType) -> None:
    with pytest.raises(SystemExit, match="no snapshot set nope"):
        script.main(["--list", "--only", "nope"])


def test_record_then_diff_shows_the_added_and_changed_cases(
    script: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    upstream_dir: Path,
) -> None:
    committed = tmp_path / "committed"
    monkeypatch.setattr(store, "SNAPSHOT_DIR", committed)
    selection = ["--only", "fixtures", "--cases", "all_urls.*"]
    assert script.main(["--out", str(tmp_path / "new"), *selection]) == 0
    out = capsys.readouterr().out
    assert "fixtures: 4 cases (0 raise)" in out
    assert "duplicate_paragraph: base differs from the expected output in 0 of 19 modules" in out
    assert store.read_index(tmp_path / "new", "fixtures").keys() == {
        "all_urls.duplicate_paragraph",
        "all_urls.duplicate_paragraph.before",
        "all_urls.same_id",
        "all_urls.same_id.before",
    }
    # nothing committed: every case is new
    assert script.main(["--diff", "--from", str(tmp_path / "new"), *selection]) == 1
    out = capsys.readouterr().out
    assert "fixtures: 4 cases, 4 added, 0 removed, 0 changed (not committed yet)" in out
    # committed, then one record changes
    records = store.read_set(tmp_path / "new", "fixtures")
    groups = {i: i.split(".")[1] for i in records}
    store.write_set(committed, "fixtures", "json", records, groups)
    assert script.main(["--diff", "--from", str(tmp_path / "new"), *selection]) == 0
    records["all_urls.same_id"] = {"ok": False, "error": "ValueError: changed"}
    store.write_set(tmp_path / "new", "fixtures", "json", records, groups)
    diff = ["--diff", "--from", str(tmp_path / "new"), "--max-lines", "1000"]
    assert script.main([*diff, *selection]) == 1
    out = capsys.readouterr().out
    assert "0 added, 0 removed, 1 changed" in out
    assert "~ all_urls.same_id" in out
    assert "ok: expected True, got False" in out
    assert '+ "error": "ValueError: changed",' in out


def test_diff_from_a_folder_without_the_sets_is_refused(
    script: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store, "SNAPSHOT_DIR", tmp_path / "committed")
    with pytest.raises(SystemExit, match="holds no recording of modules, fixtures"):
        script.main(["--diff", "--from", str(tmp_path / "typo")])
    records = {"a": {"ok": True, "value": {"t": "null"}}}
    store.write_set(tmp_path / "new", "fixtures", "json", records, {"a": "g"})
    with pytest.raises(SystemExit, match=r"holds no recording of modules$"):
        script.main(["--diff", "--from", str(tmp_path / "new"), "--only", "modules", "fixtures"])


def test_a_case_that_used_the_network_is_not_written(
    script: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = inputs.Case("net", "g", lambda: socket.getaddrinfo("example.org", 443))
    monkeypatch.setitem(inputs.SETS, "toy", inputs.SnapshotSet("toy", "json", lambda: [case]))
    assert script.main(["--out", str(tmp_path), "--only", "toy"]) == 1
    assert "toy: net: used the network" in capsys.readouterr().out
    assert not (tmp_path / "toy").exists()


@pytest.mark.slow
def test_check_records_twice_with_two_hash_seeds(
    script: ModuleType,
    capsys: pytest.CaptureFixture[str],
    upstream_dir: Path,
) -> None:
    assert script.main(["--check", "--only", "fixtures", "--cases", "all_urls.*"]) == 0
    out = capsys.readouterr().out
    assert "PYTHONHASHSEED 12345 and 777" in out
    assert "deterministic: 3 files and 4 cases byte-identical" in out


def test_check_reports_recordings_that_differ(
    script: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A stand-in for the two processes: the second writes another record."""

    class Done:
        returncode = 0

        def __init__(self, out: Path, seed: str) -> None:
            value = {"ok": True, "value": {"t": "chr", "v": [seed]}}
            store.write_set(out, "fixtures", "json", {"c": value}, {"c": "g"})

        def communicate(self) -> tuple[str, None]:
            return "", None

    monkeypatch.setattr(script, "_record_elsewhere", lambda out, seed, args: Done(out, seed))
    assert script.main(["--check", "--only", "fixtures"]) == 1
    out = capsys.readouterr().out
    assert "not deterministic: the two recordings differ in\n  fixtures/INDEX" in out
    assert "  fixtures: c" in out


def test_check_fails_when_the_recording_differs_from_the_committed_one(
    script: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The two recordings agree with each other, but not with the committed snapshots."""

    def records(**values: str) -> dict[str, store.Record]:
        return {i: {"ok": True, "value": {"t": "chr", "v": [v]}} for i, v in values.items()}

    class Done:
        returncode = 0

        def __init__(self, out: Path) -> None:
            store.write_set(out, "fixtures", "json", records(c="new"), {"c": "g"})

        def communicate(self) -> tuple[str, None]:
            return "", None

    committed = tmp_path / "committed"
    monkeypatch.setattr(store, "SNAPSHOT_DIR", committed)
    monkeypatch.setattr(script, "_record_elsewhere", lambda out, seed, args: Done(out))
    monkeypatch.setattr(script, "_on_recording_platform", lambda: True)
    check = ["--check", "--only", "fixtures"]
    store.write_set(committed, "fixtures", "json", records(c="new"), {"c": "g"})
    assert script.main(check) == 0
    assert "fixtures: equal to the committed snapshots" in capsys.readouterr().out
    for committed_records in (records(c="old"), records(c="new", d="gone")):
        store.write_set(committed, "fixtures", "json", committed_records, {"c": "g", "d": "g"})
        assert script.main(check) == 1
        assert "fixtures: 1 cases differ from the committed snapshots" in capsys.readouterr().out
    monkeypatch.setattr(script, "_on_recording_platform", lambda: False)
    assert script.main(check) == 0
    assert "not compared with the committed snapshots" in capsys.readouterr().out


# -- the committed snapshots -------------------------------------------------------------------


def test_the_committed_snapshots_match_their_index_and_cases() -> None:
    for name in store.committed_sets(store.SNAPSHOT_DIR):
        records = store.read_set(store.SNAPSHOT_DIR, name)
        assert name in inputs.SETS, f"{name} is committed but no longer defined"
        assert sorted(records) == sorted(oracle.cases(name)), f"{name}: re-record it"


@pytest.mark.slow
@pytest.mark.skipif(
    not RECORDING_PLATFORM, reason="the snapshots are recorded with Python 3.12 on Linux"
)
def test_the_committed_snapshots_reproduce(upstream_dir: Path) -> None:
    for name in store.committed_sets(store.SNAPSHOT_DIR):
        index = store.read_index(store.SNAPSHOT_DIR, name)
        changed = [
            c.id
            for c in inputs.SETS[name].cases()
            if store.digest(inputs.record(c)) != index.get(c.id)
        ]
        assert changed == [], (
            f"{name}: {len(changed)} cases changed; run scripts/record_snapshots.py --diff"
        )
