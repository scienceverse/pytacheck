"""Every parity case must match its R golden (see parity/ and docs/PARITY.md),
and the harness itself (encoder, comparator, runners) behaves as R does."""

from __future__ import annotations

import json
import math
import os
import shutil
import struct
import subprocess
import time
from pathlib import Path

import pandas as pd
import pytest

from parity import cases as pcases
from parity.__main__ import check_case
from parity.canonical import canonical, portable
from parity.cases import Case, load_cases, parity_id
from parity.compare import Options, compare, error_matches, normalize_error, summarize

CASES = load_cases()
ROOT = Path(__file__).resolve().parent.parent
#: `pytest -m "parity and tier1"` runs the cases on the realistic corpus
_TIER_MARKS = {1: pytest.mark.tier1, 2: pytest.mark.tier2}


@pytest.mark.parity
@pytest.mark.parametrize(
    "case", [pytest.param(c, marks=_TIER_MARKS[c.tier], id=c.key) for c in CASES]
)
def test_parity(case) -> None:
    status, problems, _ = check_case(case)
    if status == "missing":
        pytest.fail(f"no golden for {case.key}: run `python -m parity generate --area {case.area}`")
    if status == "skip":
        pytest.skip(problems[0])
    if status == "xfail":
        pytest.xfail(_mark_text(case.spec["known_divergence"]))
    assert status == "pass", f"{case.key} differs from R:\n{summarize(problems)}"


def _mark_text(div: object) -> str:
    """A ``known_divergence`` as the one-line reason pytest reports for an xfail."""
    if not isinstance(div, dict):
        return str(div)
    head = " ".join(str(div[k]) for k in ("kind", "ref") if div.get(k))
    return f"{head}: {div.get('reason', '')}" if head else str(div.get("reason", ""))


def test_xfail_reasons_are_text() -> None:
    div = {"kind": "r_bug_fixed", "ref": "U84", "reason": "a missing title is skipped"}
    assert _mark_text(div) == "r_bug_fixed U84: a missing title is skipped"
    assert _mark_text("R bug: typed columns") == "R bug: typed columns"


# -- the harness ---------------------------------------------------------------


def test_divergence_registry_names_cases() -> None:
    by_key = {c.key: c for c in CASES}
    registry = pcases.read_divergences()
    stale = sorted(key for key in registry.exact if key not in by_key)
    assert stale == [], "parity/divergences entries that name no case"
    for key, mark in registry.exact.items():
        assert by_key[key].spec["known_divergence"] == mark.div
    for rx, mark in (e for entries in registry.globs.values() for e in entries):
        assert any(rx.match(key) for key in by_key), f"{mark.file}: {mark.key} matches no case"


def test_every_mark_is_classified() -> None:
    marked = [c for c in CASES if c.spec.get("known_divergence") is not None]
    assert marked
    refs = pcases.upstream_refs()
    for case in marked:
        assert pcases.check_mark(case.spec["known_divergence"], case.tier, refs) == [], case.key


def test_tiers_split_the_cases() -> None:
    tiers = {c.tier for c in CASES}
    assert tiers == {1, 2}
    realistic = [c for c in CASES if c.tier == 1]
    # review and regex-emulation areas are synthetic unless a case says otherwise
    assert not [c.key for c in realistic if c.area.endswith("_review") and "tier" not in c.spec]
    assert {"mod_marginal/marginal.demo", "text/text_search.demo.significant"} <= {
        c.key for c in realistic
    }


def _reference_r() -> str:
    rscript = pcases.reference_r()
    if rscript is None:
        pytest.skip("needs the reference R (PYTACHECK_RSCRIPT, R >= 4.5)")
    return rscript


def _run_r(code: str, **kw) -> str:
    env = {**os.environ, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC"}
    out = subprocess.run(
        [_reference_r(), "--vanilla", "-e", code],
        capture_output=True,
        text=True,
        check=True,
        env=env,
        **kw,
    )
    return out.stdout


def _golden_case(tmp_path, monkeypatch, golden: dict, spec: dict) -> Case:
    """A case with the given golden, in a throwaway golden directory."""
    monkeypatch.setattr(pcases, "GOLDEN_DIR", tmp_path)
    (tmp_path / "harness").mkdir(parents=True)
    (tmp_path / "harness" / "c.json").write_text(json.dumps(golden))
    return Case(area="harness", id="c", spec={"id": "c", **spec}, file=tmp_path / "x.yaml")


# F17: a list is a vector only when its elements share an R type ----------------


def test_canonical_mixed_list_keeps_element_types() -> None:
    assert canonical(["a", 1, None]) == {
        "t": "list",
        "names": None,
        "v": [{"t": "chr", "v": ["a"]}, {"t": "int", "v": [1]}, {"t": "null"}],
    }
    assert canonical([1, 2.5, None]) == {"t": "dbl", "v": [1.0, 2.5, None]}
    assert canonical([True, None]) == {"t": "lgl", "v": [True, None]}
    assert canonical([None, None]) == {"t": "lgl", "v": [None, None]}


def test_canonical_records_frame_only_for_typed_columns() -> None:
    assert canonical([{"given": "s"}, {"given": "t"}])["t"] == "df"
    mixed = canonical([{"given": "s"}, {"given": 5}])
    assert mixed["t"] == "list"
    assert mixed["v"][1] == {"t": "list", "names": ["given"], "v": [{"t": "int", "v": [5]}]}
    # an object column mixing types is a list column, as in R
    col = canonical(pd.DataFrame({"a": ["x", 1]}))["v"][0]
    assert col["t"] == "list"


def test_mixed_types_no_longer_hide_divergences() -> None:
    # R list(list(given = "s"), list(given = "5")) vs Python given=5
    r = {"t": "list", "names": None, "v": [
        {"t": "list", "names": ["given"], "v": [{"t": "chr", "v": ["s"]}]},
        {"t": "list", "names": ["given"], "v": [{"t": "chr", "v": ["5"]}]},
    ]}  # fmt: skip
    assert compare(r, canonical([{"given": "s"}, {"given": 5}]), Options())
    assert compare(r, canonical([{"given": "s"}, {"given": "5"}]), Options()) == []
    assert compare({"t": "chr", "v": ["a", "1"]}, canonical(["a", 1]), Options())


# F61: complex and raw vectors --------------------------------------------------


def test_canonical_complex_and_raw() -> None:
    na = complex(math.nan, math.nan)  # pytacheck's missing complex
    assert canonical(pd.Series([1 + 2j, na])) == {"t": "cplx", "v": [[1.0, 2.0], None]}
    assert canonical([0.5 - 1j]) == {"t": "cplx", "v": [[0.5, -1.0]]}
    assert canonical([complex(math.inf, math.nan)]) == {"t": "cplx", "v": [["Inf", "NaN"]]}
    assert canonical(b"\x00\x0f\xff") == {"t": "raw", "v": ["00", "0f", "ff"]}
    r_na = struct.unpack("<d", struct.pack("<Q", 0x7FF00000000007A2))[0]  # NA_real_
    assert canonical([complex(r_na, 1.0)]) == {"t": "lgl", "v": [None]}


def test_complex_pairs_compare_numerically() -> None:
    # parity/r/canonical.R writes 15 significant digits
    r = {"t": "cplx", "v": [[0.333333333333333, 1e20], None, ["NaN", 1], [1, 2]]}
    p = canonical([complex(1 / 3, 1e20), complex(math.nan, math.nan), complex(math.nan, 1), 1 + 2j])
    assert compare(r, p, Options()) == []
    assert compare(r, canonical([1 / 3 + 1e20j, None, complex(math.nan, 1), 1 - 2j]), Options())
    # R's NaN in both parts is missing, as NaN is NA for doubles
    assert compare({"t": "cplx", "v": [["NaN", "NaN"]]}, canonical([None]), Options()) == []


@pytest.mark.r
def test_r_encodes_complex_as_pairs() -> None:
    out = _run_r(
        f'source("{ROOT / "parity" / "r" / "canonical.R"}"); '
        "cat(pc_to_json(pc_canonical(c(1+2i, NA, complex(real = NaN, imaginary = 1), "
        "complex(real = Inf, imaginary = -Inf), NA_complex_))))"
    )
    assert json.loads(out) == {
        "t": "cplx",
        "v": [[1, 2], None, ["NaN", 1], ["Inf", "-Inf"], None],
    }


# F62: repeated names -------------------------------------------------------------


def _named(names: list[str], values: list[int]) -> dict:
    return {"t": "list", "names": names, "v": [{"t": "int", "v": [v]} for v in values]}


def test_repeated_names_are_compared_by_position() -> None:
    r = _named(["a", "b", "b"], [1, 2, 3])
    assert compare(r, canonical({"a": 1, "b": 3}), Options())  # a dict collapses them
    assert compare(r, _named(["a", "b", "b"], [1, 2, 3]), Options()) == []
    assert compare(r, _named(["a", "b", "b"], [1, 3, 2]), Options())


def test_strict_names_compares_the_order() -> None:
    r = _named(["a", "b"], [1, 2])
    p = canonical({"b": 2, "a": 1})
    assert compare(r, p, Options()) == []
    assert compare(r, p, Options.from_case({"strict_names": True}))


# F63: a 0x0 data frame is empty ----------------------------------------------------


def test_zero_by_zero_frame_equals_empty_list() -> None:
    r = {"t": "df", "nrow": 0, "names": [], "v": []}
    assert compare(r, canonical([]), Options()) == []
    assert compare({"t": "list", "names": None, "v": [r]}, canonical([[]]), Options()) == []
    with_cols = {"t": "df", "nrow": 0, "names": ["given"], "v": [{"t": "chr", "v": []}]}
    assert compare(with_cols, canonical([1]), Options())


# F18: error messages ------------------------------------------------------------


def test_normalize_error() -> None:
    msg = "Join columns in `y` must be present.\n✖ Problem with `doi`.\nℹ Hint.\n! More."
    assert (
        normalize_error(msg)
        == "Join columns in `y` must be present. Problem with `doi`. Hint. More."
    )
    assert normalize_error("Error in f(x) : boom") == "boom"
    assert (
        normalize_error("x must be y\n!is.na(x) is not TRUE") == "x must be y !is.na(x) is not TRUE"
    )


@pytest.mark.parametrize(
    ("r", "py", "mode", "ok"),
    [
        ("boom", "boom", "exact", True),
        ("boom\n✖ detail", "boom detail", "exact", True),
        ("boom", "Running the module 'x' produced errors: boom", "exact", False),
        ("boom", "Running the module 'x' produced errors: boom", "contains", True),
        (
            "Join columns must be present.\n✖ Problem with `doi`.",
            "Join columns must be present.",
            "contains",
            True,
        ),
        ("wrong thing", "other thing", "contains", False),
        ("some message", "", "contains", False),
        ("wrong thing", "other thing", "any", True),
    ],
)
def test_error_matches(r: str, py: str, mode: str, ok: bool) -> None:
    assert error_matches(r, py, mode) is ok


def test_check_case_compares_error_messages(tmp_path, monkeypatch) -> None:
    golden = {"ok": False, "error": "argument is of length zero", "value": None}
    spec = {
        "py": "parity.pyhelpers.github_readme_probe",
        "args": {"repo": {"$expr": {"py": "1/0"}}},
    }
    case = _golden_case(tmp_path, monkeypatch, golden, spec)
    # by default both sides must fail; pytacheck's message is its own
    assert check_case(case)[0] == "pass"
    case.spec["compare"] = {"error": "contains"}
    status, problems, _ = check_case(case)
    assert status == "fail"
    assert "ZeroDivisionError" in problems[0]
    case.spec["known_divergence"] = {"kind": "r_bug_fixed", "reason": "documented"}
    assert check_case(case)[0] == "xfail"
    golden["error"] = "division by zero"
    case = _golden_case(
        tmp_path / "2", monkeypatch, golden, {**spec, "compare": {"error": "exact"}}
    )
    assert check_case(case)[0] == "pass"


# F16: deterministic paper ids, portable paths -------------------------------------


def test_parity_ids_match_r() -> None:
    # R: substr(tools::md5sum(bytes = charToRaw("pytacheck-parity-1")), 1, 14)
    assert parity_id(1) == "6c4258abe0fec3"


def test_deterministic_ids_number_papers_per_case() -> None:
    import pytacheck as pc

    with pcases.deterministic_ids():
        a, b = pc.test_paper(["x"]), pc.test_paper(["y"])
    assert (a.paper_id, b.paper_id) == (parity_id(1), parity_id(2))
    assert a.info["file_hash"].tolist() == [parity_id(1)]
    with pcases.deterministic_ids():
        assert pc.test_paper().paper_id == parity_id(1)
    assert pc.test_paper().paper_id not in (parity_id(1), parity_id(2), parity_id(3))


def test_portable_paths() -> None:
    p = str(ROOT / "tests" / "x.csv")
    rel = p[len(str(ROOT)) :]
    assert portable(p) == "<repo>" + rel
    assert canonical({"file": [p, None]})["v"][0]["v"] == ["<repo>" + rel, None]
    assert canonical(pd.DataFrame({p: [1]}))["names"] == ["<repo>" + rel]
    # undecodable bytes (surrogateescape) cannot go through JSON
    assert canonical([p + "\udcff"])["v"] == ["<repo>" + rel + "\udcff"]


@pytest.mark.r
def test_r_runner_ids_paths_and_yaml_floats(tmp_path: Path) -> None:
    """run_cases.R: deterministic test_paper() ids, placeholders for the checkout,
    R's temporary files and the time of the run, and quoted ``!!float``
    infinities (F16, F66)."""
    root = tmp_path / "repo"
    (root / "parity" / "cases").mkdir(parents=True)
    (root / "parity" / "r").symlink_to(ROOT / "parity" / "r")
    root = root.resolve()
    cases = root / "parity" / "cases" / "harness.yaml"
    cases.write_text(
        "area: harness\ncases:\n"
        "  - id: ids\n    r: identity\n"
        '    args: {x: {$expr: {r: "c(test_paper()$paper_id, test_paper()$info$file_hash)"}}}\n'
        '  - id: path\n    r: identity\n    args: {x: {$file: "a/b.txt"}}\n'
        "  - id: volatile\n    r: identity\n"
        "    args: {x: {$expr: {r: \"local({f <- tempfile(fileext = '.xml'); "
        "c(f, basename(f), file.path(tempdir(), 'data'), "
        "format(Sys.time(), '%Y-%m-%dT%H:%M:%SZ', tz = 'UTC'), '2020-01-02T03:04:05Z')})\"}}}\n"
        "  - id: floats\n    r: c\n"
        '    args: {a: !!float ".inf", b: !!float "-.inf", c: .inf, d: !!float "1e-3", e: 1.5}\n'
    )
    env = {**os.environ, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC"}
    subprocess.run(
        [_reference_r(), str(ROOT / "parity" / "r" / "run_cases.R"), str(root), str(cases)],
        check=True,
        capture_output=True,
        env=env,
        cwd=root,
    )
    out = root / "parity" / "golden" / "harness"
    ids = json.loads((out / "ids.json").read_text())["value"]["v"]
    assert ids == [parity_id(1), parity_id(2)]
    assert json.loads((out / "path.json").read_text())["value"]["v"] == ["<repo>/a/b.txt"]
    assert json.loads((out / "volatile.json").read_text())["value"]["v"] == [
        "<tempdir>/<tempfile>.xml",
        "<tempfile>.xml",
        "<tempdir>/data",
        "<now>",
        "2020-01-02T03:04:05Z",
    ]
    floats = json.loads((out / "floats.json").read_text())
    assert floats["value"]["v"] == ["Inf", "-Inf", "Inf", 0.001, 1.5]
    assert floats["warnings"] == []


# F65, F67: metacheck's defaults and UTC -------------------------------------------


def test_metacheck_defaults_include_paper_write() -> None:
    import inspect

    import pytacheck as pc

    assert inspect.signature(pc.paper_write).parameters["schema_version"].default == "auto"
    with pcases.metacheck_defaults():
        assert inspect.signature(pc.paper_write).parameters["schema_version"].default is None
    assert inspect.signature(pc.paper_write).parameters["schema_version"].default == "auto"


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="time.tzset() is POSIX only")
def test_cases_run_in_utc(monkeypatch) -> None:
    monkeypatch.setenv("TZ", "Pacific/Auckland")
    time.tzset()
    try:
        with pcases.utc():
            assert time.localtime(0).tm_gmtoff == 0
        assert os.environ["TZ"] == "Pacific/Auckland"
    finally:
        monkeypatch.undo()
        time.tzset()


# F81: cases whose Python side runs R -----------------------------------------------


@pytest.fixture
def no_reference_r(monkeypatch):
    monkeypatch.delenv("PYTACHECK_RSCRIPT", raising=False)
    pcases.reference_r.cache_clear()
    yield
    pcases.reference_r.cache_clear()


def _run_rscript(catch: bool = False) -> str:
    """What a pytacheck function running R does: find Rscript, start it."""
    try:
        cmd = [shutil.which("Rscript") or "Rscript", "-e", "cat(R.version$minor)"]
        return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    except Exception as exc:
        if not catch:
            raise
        return f"R failed: {exc}"


def test_starting_r_without_reference_is_a_skip(tmp_path, monkeypatch, no_reference_r) -> None:
    from parity import pyhelpers

    monkeypatch.setattr(pyhelpers, "_run_rscript", _run_rscript, raising=False)
    golden = {"ok": True, "error": None, "value": {"t": "chr", "v": ["5.3"]}}
    case = _golden_case(tmp_path, monkeypatch, golden, {"py": "parity.pyhelpers._run_rscript"})
    status, problems, _ = check_case(case)
    assert status == "skip"
    assert "PYTACHECK_RSCRIPT" in problems[0]
    # an attempt the code catches is still a skip, not a wrong result
    case.spec["args"] = {"catch": True}
    assert check_case(case)[0] == "skip"
    # needs_r skips before running anything
    case.spec["py"] = "copy.copy"
    case.spec["args"] = {"x": "5.3"}
    assert check_case(case)[0] == "pass"
    case.spec["needs_r"] = True
    assert check_case(case)[0] == "skip"


@pytest.mark.r
def test_reference_r_runs_r_cases(tmp_path, monkeypatch) -> None:
    _reference_r()
    from parity import pyhelpers

    monkeypatch.setattr(pyhelpers, "_run_rscript", _run_rscript, raising=False)
    minor = _run_r("cat(R.version$minor)")
    golden = {"ok": True, "error": None, "value": {"t": "chr", "v": [minor]}}
    spec = {"py": "parity.pyhelpers._run_rscript", "needs_r": True}
    monkeypatch.setenv("PATH", str(Path(pcases.reference_r()).parent), prepend=os.pathsep)
    assert check_case(_golden_case(tmp_path, monkeypatch, golden, spec))[0] == "pass"


def test_other_programs_still_run(tmp_path, monkeypatch, no_reference_r) -> None:
    golden = {"ok": True, "error": None, "value": {"t": "chr", "v": ["ok"]}}
    code = (
        "__import__('subprocess').run([__import__('sys').executable, '-c', 'print(\"ok\")'], "
        "capture_output=True, text=True, check=True).stdout.strip()"
    )
    spec = {"py": "copy.copy", "args": {"x": {"$expr": {"py": code}}}}
    assert check_case(_golden_case(tmp_path, monkeypatch, golden, spec))[0] == "pass"


# r_text: a known_divergence that only corrects R's text ------------------------------


def _chr_golden(*values: str) -> dict:
    return {"ok": True, "error": None, "value": {"t": "chr", "v": list(values)}}


def _copy_case(value: list[str], mark: dict | None) -> dict:
    spec: dict = {"py": "copy.copy", "args": {"x": {"$chr": value}}}
    if mark is not None:
        spec["known_divergence"] = mark
    return spec


def test_parse_r_text() -> None:
    from parity.compare import TextSub, parse_r_text

    assert parse_r_text(None) == []
    assert parse_r_text({"kind": "r_bug_fixed", "reason": "x"}) == []
    assert parse_r_text({"r_text": [["a", "b"], ["c$", "d", "regex"]]}) == [
        TextSub("a", "b"),
        TextSub("c$", "d", regex=True),
    ]
    for bad in (
        [],
        [["a"]],
        [["a", "b", "re"]],
        [["", "b"]],
        [["a", 1]],
        "a",
        [["(", "b", "regex"]],
    ):
        with pytest.raises(ValueError, match="r_text"):
            parse_r_text({"r_text": bad})


def test_rewrite_r_text_touches_string_values_only() -> None:
    from parity.compare import TextSub, rewrite_r_text

    golden = {
        "t": "list",
        "names": ["likley", "n"],
        "v": [
            {"t": "chr", "v": ["is likley", None, "likley likley"]},
            {"t": "df", "nrow": 1, "names": ["likley"], "v": [{"t": "chr", "v": ["likley"]}]},
        ],
    }
    used = [False, False]
    subs = [TextSub("likley", "likely"), TextSub(r"^is (\w+)$", r"was \1", regex=True)]
    out = rewrite_r_text(golden, subs, used)
    assert out["names"] == ["likley", "n"]
    assert out["v"][0]["v"] == ["was likely", None, "likely likely"]
    assert out["v"][1]["names"] == ["likley"]
    assert out["v"][1]["v"][0]["v"] == ["likely"]
    assert used == [True, True]
    assert golden["v"][0]["v"][0] == "is likley"  # the golden itself is left alone
    assert rewrite_r_text("an likley error", subs, [False, False]) == "an likely error"


def test_r_text_mark_passes_on_corrected_text_only(tmp_path, monkeypatch) -> None:
    mark = {"kind": "r_bug_fixed", "ref": "U83", "reason": "typo", "r_text": [["likley", "likely"]]}
    golden = _chr_golden("likley", "same")
    case = _golden_case(tmp_path, monkeypatch, golden, _copy_case(["likely", "same"], mark))
    assert check_case(case)[0] == "pass"
    # any other difference fails: the mark describes the whole difference
    case.spec = _copy_case(["likely", "other"], mark)
    status, problems, _ = check_case(case)
    assert status == "fail"
    assert "other" in problems[0]
    # ... unless the case also differs for another reason
    case.spec = _copy_case(["likely", "other"], {**mark, "xfail": True})
    assert check_case(case)[0] == "xfail"
    # a substitution that changes nothing is a stale mark
    stale = {**mark, "r_text": [["likley", "likely"], ["reconized", "recognized"]]}
    case.spec = _copy_case(["likely", "same"], stale)
    status, problems, _ = check_case(case)
    assert status == "fail"
    assert "reconized" in problems[0]
    # without r_text, a mark is an expected failure as before
    plain = {"kind": "r_bug_fixed", "ref": "U83", "reason": "typo"}
    case.spec = _copy_case(["likely", "same"], plain)
    assert check_case(case)[0] == "xfail"


def test_r_text_rewrites_r_error_messages(tmp_path, monkeypatch) -> None:
    golden = {"ok": False, "error": "no such paper: likley", "value": None}
    spec = {
        "py": "parity.pyhelpers.github_readme_probe",
        "args": {
            "repo": {"$expr": {"py": "(_ for _ in ()).throw(ValueError('no such paper: likely'))"}}
        },
        "compare": {"error": "exact"},
        "known_divergence": {
            "kind": "r_bug_fixed",
            "ref": "U83",
            "reason": "typo",
            "r_text": [["likley", "likely"]],
        },
    }
    case = _golden_case(tmp_path, monkeypatch, golden, spec)
    assert check_case(case)[0] == "pass"
    # with error: any the message is not compared, so the mark is not needed
    case.spec["compare"] = {"error": "any"}
    status, problems, _ = check_case(case)
    assert status == "fail"
    assert "without its r_text" in problems[0]


def test_r_text_on_skipped_text_is_a_stale_mark(tmp_path, monkeypatch) -> None:
    # the substitution changes R's golden, but only where the comparison does
    # not look (an ignored element), so the case passes without the mark
    golden = {
        "ok": True,
        "error": None,
        "value": {
            "t": "list",
            "names": ["a", "b"],
            "v": [{"t": "chr", "v": ["same"]}, {"t": "chr", "v": ["likley"]}],
        },
    }
    mark = {"kind": "r_bug_fixed", "ref": "U83", "reason": "typo", "r_text": [["likley", "likely"]]}
    spec = {
        "py": "copy.copy",
        "args": {"x": {"$expr": {"py": "{'a': 'same', 'b': 'likely'}"}}},
        "known_divergence": mark,
    }
    case = _golden_case(tmp_path, monkeypatch, golden, spec)
    assert check_case(case)[0] == "pass"
    case.spec["compare"] = {"ignore": ["b"]}
    status, problems, _ = check_case(case)
    assert status == "fail"
    assert "without its r_text" in problems[0]


def test_malformed_marks_are_refused(tmp_path, monkeypatch) -> None:
    (tmp_path / "x.yaml").write_text(
        '"a/b": {kind: r_bug_fixed, ref: U83, reason: typo, r_text: [["x"]]}\n'
    )
    monkeypatch.setattr(pcases, "DIVERGENCES_DIR", tmp_path)
    with pytest.raises(ValueError, match="r_text"):
        pcases.load_divergences()
    (tmp_path / "x.yaml").write_text(
        '"a/b": {kind: r_bug_fixed, ref: U83, reason: typo, xfail: true}\n'
    )
    with pytest.raises(ValueError, match="xfail belongs to a mark with r_text"):
        pcases.load_divergences()


# marks: kinds, refs and tiers ----------------------------------------------------------

_REFS = {"U1": "fixed", "U2": "kept", "U3": None, "D1": None}


@pytest.mark.parametrize(
    ("mark", "tier", "problem"),
    [
        ({"kind": "r_bug_fixed", "ref": "U1", "reason": "x"}, 1, None),
        ({"kind": "r_bug_fixed", "ref": "U3", "reason": "x"}, 1, None),  # no status column
        ({"kind": "better_logic", "ref": "D1", "reason": "x"}, 1, None),
        ({"kind": "deliberate", "ref": "D1", "reason": "x"}, 1, None),
        ({"kind": "r_nondeterministic", "reason": "x"}, 1, None),
        ({"kind": "c_quirk", "reason": "x"}, 2, None),
        ({"kind": "c_quirk", "ref": "D1", "reason": "x"}, 2, None),
        ({"kind": "type_detail", "ref": "U1", "reason": "x"}, 2, None),
        ("R does it differently", 2, "a mark is a mapping"),
        ({"kind": "unclassified", "reason": "x"}, 2, "is not one of"),
        ({"kind": "r_bug_fixed", "reason": "x"}, 2, "needs a ref to a U-entry"),
        ({"kind": "r_bug_fixed", "ref": "D1", "reason": "x"}, 2, "needs a U-entry, not D1"),
        ({"kind": "better_logic", "ref": "U1", "reason": "x"}, 2, "needs a D-entry, not U1"),
        ({"kind": "deliberate", "reason": "x"}, 2, "needs a ref to a D-entry"),
        ({"kind": "r_bug_fixed", "ref": "U99", "reason": "x"}, 2, "U99 is not an entry"),
        ({"kind": "r_bug_fixed", "ref": "U1, U3", "reason": "x"}, 2, "is not U<n> or D<n>"),
        ({"kind": "r_bug_fixed", "ref": "U2", "reason": "x"}, 2, "whose status is 'kept'"),
        ({"kind": "c_quirk", "reason": " "}, 2, "needs a reason"),
        ({"kind": "type_detail"}, 2, "needs a reason"),
        ({"kind": "c_quirk", "reason": "x"}, 1, "tier-1 (realistic) case cannot be marked c_quirk"),
        ({"kind": "type_detail", "reason": "x"}, 1, "cannot be marked type_detail"),
        ({"kind": "c_quirk", "reason": "x", "note": "y"}, 2, "unknown keys ['note']"),
        ({"kind": "r_bug_fixed", "ref": "U1", "reason": "x", "xfail": True}, 2, "xfail belongs"),
    ],
)
def test_check_mark(mark, tier: int, problem: str | None) -> None:
    problems = pcases.check_mark(mark, tier, _REFS)
    if problem is None:
        assert problems == []
    else:
        assert any(problem in p for p in problems), problems


def test_upstream_refs_reads_ids_and_status(tmp_path) -> None:
    doc = tmp_path / "UPSTREAM_ISSUES.md"
    doc.write_text(
        "# Upstream issues\n\n| # | behaviour | why |\n|---|---|---|\n| D1 | a \\| b | c |\n\n"
        "| # | status | where | issue |\n|---|---|---|---|\n"
        "| U1 | **Fixed** | `f()` | x |\n| U2 | kept | g | y |\n| U10 | partly fixed | h | z |\n"
        "Some text mentioning | U3 | in a paragraph.\n"
    )
    assert pcases.upstream_refs(doc) == {
        "D1": None,
        "U1": "fixed",
        "U2": "kept",
        "U10": "partly fixed",
    }
    real = pcases.upstream_refs()
    assert {"U1", "U79", "D1", "D28"} <= set(real)


# tiers ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pattern", "path", "hit"),
    [
        ("a/b/**", "a/b", True),
        ("a/b/**", "a/b/c/d.txt", True),
        ("a/b/**", "a/bc", False),
        ("a/*/fixtures/**", "a/x/fixtures/f.csv", True),
        ("a/*/fixtures/**", "a/x/y/fixtures/f.csv", False),
        ("**/review/**", "tests/x/fixtures/review/f.csv", True),
        ("**/review/**", "review/f.csv", True),
        ("**/review/**", "tests/x/fixtures/reviewed.csv", False),
        ("**/review_*/**", "tests/repos/review_types/data/a.csv", True),
        ("x/to_err.*", "x/to_err.xml", True),
        ("x/to_err.*", "x/to_err/y.xml", False),
        ("x/a?c", "x/abc", True),
    ],
)
def test_corpus_globs(pattern: str, path: str, hit: bool) -> None:
    import re

    assert bool(re.fullmatch(pcases._path_glob(pattern), path)) is hit


@pytest.fixture
def corpus(tmp_path):
    toml = tmp_path / "corpus.toml"
    toml.write_text(
        "[corpus]\n"
        'papers = ["upstream/metacheck/inst/demos/to_err_is_human.*", "up/papers/**"]\n'
        'files = ["tests/*/fixtures/**"]\n'
        'mocks = ["upstream/metacheck/tests/testthat/apis*/**"]\n'
        'exclude = ["**/review/**", "**/*fuzz*/**"]\n'
        "[[override]]\ntier = 2\n"
        'reason = "synthetic mocks"\ncases = ["area/mocked.*"]\n'
    )
    return pcases.load_corpus(toml)


@pytest.mark.parametrize(
    ("area", "spec", "tier"),
    [
        ("text", {"args": {"paper": {"$paper": "demo"}}}, 1),
        ("text", {"args": {"paper": {"$paper": "up/papers/a.xml"}, "pattern": "x"}}, 1),
        ("text", {"args": {"paper": {"$read": ["up/papers/a.xml", "up/papers/b.xml"]}}}, 1),
        ("text", {"args": {"path": "tests/codecheck/fixtures/a.R"}}, 1),
        ("text", {"args": {"path": {"$file": "tests/codecheck/fixtures/review/a.R"}}}, 2),
        ("text", {"args": {"path": "tests/db/data/doi_fuzz.json"}}, 2),
        ("text", {"args": {"paper": {"$paper": "demo"}, "x": "tests/other/data.csv"}}, 2),
        ("text_review", {"args": {"paper": {"$paper": "demo"}}}, 2),
        ("rcompat_regex", {"args": {"x": "tests/a/fixtures/b.txt"}}, 2),
        ("text", {"args": {"pattern": "x"}}, 2),  # no input at all
        ("text", {"args": {"paper": {"$test_paper": {"text": ["a"]}}}}, 2),
        ("text", {"args": {"paper": {"$paper": "demo"}, "p": {"$chr": ["a", "b"]}}}, 2),
        ("text", {"args": {"x": {"$df": {"a": [1]}}, "p": {"$paper": "demo"}}}, 2),
        ("text", {"args": {"x": {"$expr": {"r": "demopaper()", "py": "pc.demopaper()"}}}}, 1),
        # helper scripts are not inputs; an $expr naming no corpus path is synthetic
        ("text", {"args": {"x": {"$expr": {"r": "source('tests/mod_x/helpers.R'); f(1)"}}}}, 2),
        (
            "text",
            {"args": {"x": {"$expr": {"r": "source('tests/mod_x/h.R'); f('tests/y/fixtures/a')"}}}},
            1,
        ),
        (
            "text",
            {"args": {"x": {"$call": {"r": "read", "args": {"f": {"$paper": "demo"}}}}}},
            1,
        ),
        ("text", {"mock_dir": "apis", "args": {"paper": {"$paper": "demo"}}}, 1),
        ("text", {"mock_dir": "tests/llm/mocks", "args": {"paper": {"$paper": "demo"}}}, 2),
        (
            "text",
            {
                "args": {
                    "x": {
                        "$expr": {
                            "r": 'with_mock_dir("upstream/metacheck/tests/'
                            'testthat/apis", f(tp("https://osf.io/x")))'
                        }
                    }
                }
            },
            1,
        ),
        # explicit tiers
        ("area", {"id": "mocked.x", "args": {"paper": {"$paper": "demo"}}}, 2),
        ("text_review", {"id": "y", "args": {}, "tier": {"level": 1, "reason": "real"}}, 1),
    ],
)
def test_classify_tier(corpus, area: str, spec: dict, tier: int) -> None:
    assert pcases.classify_tier(area, {"id": "c", **spec}, corpus) == tier


def test_explicit_tiers_need_a_reason(corpus) -> None:
    for bad in (1, {"level": 1}, {"level": 3, "reason": "x"}, {"level": 1, "reason": ""}):
        with pytest.raises(ValueError, match="tier is"):
            pcases.classify_tier("text", {"id": "c", "tier": bad}, corpus)
    # a file's tier applies to its cases without their own, below corpus.toml overrides
    assert pcases.classify_tier("text", {"id": "c"}, corpus, file_tier=1) == 1
    assert pcases.classify_tier("area", {"id": "mocked.x"}, corpus, file_tier=1) == 2


# loading: marks from parity/divergences, globs, one mark per case ------------------------


@pytest.fixture
def harness_dirs(tmp_path, monkeypatch, corpus):
    """Empty case and divergences directories, and the test corpus."""
    cases, divs = tmp_path / "cases", tmp_path / "divergences"
    cases.mkdir()
    divs.mkdir()
    monkeypatch.setattr(pcases, "CASES_DIR", cases)
    monkeypatch.setattr(pcases, "DIVERGENCES_DIR", divs)
    monkeypatch.setattr(pcases, "load_corpus", lambda: corpus)
    monkeypatch.setattr(pcases, "upstream_refs", lambda: _REFS)
    (cases / "gen.yaml").write_text(
        "area: gen\ncases:\n"
        "  - {id: real.a, py: copy.copy, args: {x: {$paper: demo}}}\n"
        "  - {id: edge.a, py: copy.copy, args: {x: {$chr: [a]}}}\n"
        "  - {id: edge.b, py: copy.copy, args: {x: {$chr: [b]}}}\n"
    )
    return cases, divs


def _marks(cases: list[Case]) -> dict:
    return {c.key: (c.tier, c.spec.get("known_divergence")) for c in cases}


def test_glob_marks_apply_to_tier_2_cases(harness_dirs) -> None:
    _, divs = harness_dirs
    (divs / "lane.yaml").write_text('"gen/edge.*": {kind: c_quirk, reason: fuzzed input}\n')
    got = _marks(load_cases())
    quirk = {"kind": "c_quirk", "reason": "fuzzed input"}
    assert got == {"gen/real.a": (1, None), "gen/edge.a": (2, quirk), "gen/edge.b": (2, quirk)}
    assert [c.key for c in load_cases(tier=1)] == ["gen/real.a"]
    # a glob may not reach a tier-1 case, even with a kind tier 1 allows
    (divs / "lane.yaml").write_text('"gen/*": {kind: r_bug_fixed, ref: U1, reason: fixed}\n')
    with pytest.raises(ValueError, match=r"matches the tier-1 case gen/real\.a"):
        load_cases()


def test_tier_1_cases_take_realistic_kinds_only(harness_dirs) -> None:
    _, divs = harness_dirs
    (divs / "lane.yaml").write_text('"gen/real.a": {kind: type_detail, reason: int vs dbl}\n')
    with pytest.raises(ValueError, match="cannot be marked type_detail"):
        load_cases()
    (divs / "lane.yaml").write_text('"gen/real.a": {kind: r_bug_fixed, ref: U1, reason: x}\n')
    assert _marks(load_cases())["gen/real.a"][1]["ref"] == "U1"


def test_a_case_is_marked_once(harness_dirs) -> None:
    cases_dir, divs = harness_dirs
    (divs / "a.yaml").write_text('"gen/edge.a": {kind: c_quirk, reason: x}\n')
    (divs / "b.yaml").write_text('"gen/edge.*": {kind: c_quirk, reason: y}\n')
    with pytest.raises(ValueError, match=r"gen/edge\.a is marked more than once"):
        load_cases()
    (divs / "b.yaml").write_text('"gen/edge.a": {kind: c_quirk, reason: y}\n')
    with pytest.raises(ValueError, match=r"already marked in a\.yaml"):
        load_cases()
    (divs / "b.yaml").unlink()
    (cases_dir / "gen.yaml").write_text(
        "area: gen\ncases:\n  - id: edge.a\n    py: copy.copy\n    args: {x: {$chr: [a]}}\n"
        "    known_divergence: {kind: c_quirk, reason: inline}\n"
    )
    with pytest.raises(ValueError, match=r"marked both in its case file and in a\.yaml"):
        load_cases()


def test_all_invalid_marks_are_reported_together(harness_dirs) -> None:
    cases_dir, divs = harness_dirs
    (divs / "lane.yaml").write_text(
        '"gen/edge.a": {kind: r_bug_fixed, reason: x}\n"gen/edge.b": R bug\n'
    )
    (cases_dir / "hand.yaml").write_text(
        "area: hand\ntier: {level: 1, reason: real inputs built by hand}\ncases:\n"
        "  - id: c\n    py: copy.copy\n    args: {x: 1}\n"
        "    known_divergence: {kind: c_quirk, reason: inline}\n"
    )
    with pytest.raises(ValueError) as exc:
        load_cases()
    text = str(exc.value)
    assert "gen/edge.a: r_bug_fixed needs a ref" in text
    assert "gen/edge.b: a mark is a mapping" in text
    assert "hand/c: a tier-1 (realistic) case cannot be marked c_quirk" in text


def test_yaml_loader_is_libyaml_with_the_same_values() -> None:
    import yaml

    assert pcases.YAML_LOADER is getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    for f in [
        ROOT / "parity" / "divergences" / "prose.yaml",
        ROOT / "parity" / "cases" / "core.yaml",
    ]:
        text = f.read_text(encoding="utf-8")
        assert yaml.load(text, Loader=pcases.YAML_LOADER) == yaml.safe_load(text)
