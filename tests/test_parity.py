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
import warnings
from pathlib import Path

import pandas as pd
import pytest

from parity import __main__ as parity_main
from parity import cases as pcases
from parity import lockfile
from parity.__main__ import check_case, run_case, stale_lock_entries
from parity.canonical import canonical, portable
from parity.cases import Case, load_cases, parity_id
from parity.compare import Options, comparable, compare, option_problems, summarize
from parity.lockfile import R_ERROR_PY_VALUE, Fingerprint, LockWarning

CASES = load_cases()
ROOT = Path(__file__).resolve().parent.parent
#: `pytest -m "parity and tier1"` runs the cases on the realistic corpus
_TIER_MARKS = {1: pytest.mark.tier1, 2: pytest.mark.tier2}


@pytest.mark.parity
@pytest.mark.parametrize(
    "case", [pytest.param(c, marks=_TIER_MARKS[c.tier], id=c.key) for c in CASES]
)
def test_parity(case) -> None:
    res = run_case(case)
    if res.status == "missing":
        pytest.fail(f"no golden for {case.key}: run `python -m parity generate --area {case.area}`")
    if res.status == "skip":
        pytest.skip(res.problems[0])
    if res.warning:  # a tier-2 marked case that changed since it was locked
        warnings.warn(f"{case.key}: {res.status}\n{summarize(res.problems)}", LockWarning, 1)
    if res.status == "xfail" or res.warning:
        pytest.xfail(_mark_text(case.spec["known_divergence"]))
    what = "differs from R" if res.status in ("fail", "error") else res.status
    assert res.status == "pass", f"{case.key} {what}:\n{summarize(res.problems)}"


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
    # every input of a tier-1 case is a file of the corpus that exists
    corpus = pcases.load_corpus()
    for case in realistic:
        if "tier" not in case.spec and corpus.override(case.key) is None:
            inputs = pcases.case_inputs(case.spec, corpus)
            assert inputs.paths and not inputs.synthetic, case.key
            assert all((ROOT / pcases._normpath(p)).exists() for p in inputs.paths), case.key


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


# errors: both sides failing is a match, texts are never compared -----------------


def test_errors_are_never_compared(tmp_path, monkeypatch, lock_dir) -> None:
    golden = {"ok": False, "error": "argument is of length zero", "value": None}
    raising = {
        "py": "parity.pyhelpers.github_readme_probe",
        "args": {"repo": {"$expr": {"py": "1/0"}}},
    }
    case = _golden_case(tmp_path, monkeypatch, golden, raising)
    assert check_case(case)[0] == "pass"  # R's message and Python's differ
    # R fails and Python returns a value: a failure until it is marked and locked
    returning = {"py": "parity.pyhelpers.r_sort", "args": {"x": {"$chr": ["b", "a"]}}}
    case = _golden_case(tmp_path / "2", monkeypatch, golden, returning)
    status, problems, _ = check_case(case)
    assert status == "fail"
    assert "argument is of length zero" in problems[0]  # R's text, to say why it failed
    case.spec["known_divergence"] = _MARK
    assert _lock(case).diff == (R_ERROR_PY_VALUE,)
    assert check_case(case)[0] == "xfail"


def test_compare_options_are_checked() -> None:
    assert option_problems(None) == []
    assert option_problems({"ignore": ["a.b"], "presence": ["repo_error"], "tol": 1e-6}) == []
    assert "error texts are never compared" in option_problems({"error": "exact"})[0]
    assert "unknown options ['ignroe']" in option_problems({"ignroe": ["x"]})[0]
    assert "presence is a list" in option_problems({"presence": "repo_error"})[0]
    with pytest.raises(ValueError, match="error texts are never compared"):
        Options.from_case({"error": "contains"})


def test_case_files_reject_bad_compare_options(tmp_path, monkeypatch) -> None:
    (tmp_path / "harness.yaml").write_text(
        "area: harness\ncases:\n  - id: c\n    r: identity\n    args: {x: 1}\n"
        "    compare: {error: exact}\n"
    )
    monkeypatch.setattr(pcases, "CASES_DIR", tmp_path)
    with pytest.raises(ValueError, match=r"harness\.yaml: harness/c: compare: error is gone"):
        load_cases("harness")


def test_catch_constructor() -> None:
    from parity.cases import decode

    assert decode({"$catch": {"$expr": {"py": "1/0"}}}) == {"error": True}
    assert decode({"$catch": {"$chr": ["a"]}}) == ["a"]
    # errors inside a list are caught one by one
    both = decode({"$list": [{"$catch": {"$expr": {"py": "int('x')"}}}, {"$catch": 2}]})
    assert both == [{"error": True}, 2]
    # what R's pc_catch() gives: list(error = TRUE)
    r_caught = {"t": "list", "names": ["error"], "v": [{"t": "lgl", "v": [True]}]}
    assert compare(r_caught, canonical({"error": True}), Options()) == []
    # the inputs inside a $catch count for the tier
    spec = {"args": {"x": {"$catch": {"$paper": "demo"}}}}
    assert pcases.case_inputs(spec).paths == [pcases.DEMO_PAPER]


def test_catch_leaves_skips_and_network_use_alone(tmp_path, monkeypatch) -> None:
    from parity.pyhelpers import catch

    def start_r() -> None:
        raise pcases.RWithoutReference("Rscript")

    with pytest.raises(pcases.RWithoutReference):
        catch(start_r)
    golden = {"ok": True, "error": None, "value": canonical({"error": True})}
    spec = {
        "py": "parity.pyhelpers.github_readme_probe",
        "args": {
            "repo": {"$catch": {"$expr": {"py": "__import__('socket').getaddrinfo('x.org', 443)"}}}
        },
    }
    status, problems, _ = check_case(_golden_case(tmp_path, monkeypatch, golden, spec))
    assert status == "error"
    assert "used the network" in problems[0]


@pytest.mark.r
def test_r_runner_catch(tmp_path: Path) -> None:
    """run_cases.R: ``$catch`` gives the value, or ``list(error = TRUE)`` without the
    message; an uncaught error is the golden's error, not compared."""
    root = tmp_path / "repo"
    (root / "parity" / "cases").mkdir(parents=True)
    (root / "parity" / "r").symlink_to(ROOT / "parity" / "r")
    root = root.resolve()
    cases = root / "parity" / "cases" / "harness.yaml"
    cases.write_text(
        "area: harness\ncases:\n"
        "  - id: caught\n    r: identity\n"
        "    args: {x: {$list: [{$catch: {$expr: {r: \"stop('boom')\"}}}, {$catch: {$chr: [a]}}]}}\n"
        "  - id: uncaught\n    r: identity\n"
        "    args: {x: {$expr: {r: \"stop('boom')\"}}}\n"
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
    caught = json.loads((out / "caught.json").read_text())
    assert caught["ok"] is True
    assert compare(caught["value"], canonical([{"error": True}, "a"]), Options()) == []
    assert "boom" not in json.dumps(caught)
    uncaught = json.loads((out / "uncaught.json").read_text())
    assert (uncaught["ok"], uncaught["error"]) == (False, "boom")


# presence: fields of free error text ----------------------------------------------


def _frame(**cols: list) -> dict:
    return canonical(pd.DataFrame(cols))


def test_presence_compares_only_whether_values_are_there() -> None:
    r = {
        "t": "list",
        "names": ["repos"],
        "v": [
            _frame(
                url=["a", "b", "c"], repo_error=["ℹ In argument: `x`.\nCaused by error", None, ""]
            )
        ],
    }
    same = {
        "t": "list",
        "names": ["repos"],
        "v": [
            _frame(url=["a", "b", "c"], repo_error=["invalid or inaccessible OSF link", None, None])
        ],
    }
    missing = {
        "t": "list",
        "names": ["repos"],
        "v": [_frame(url=["a", "b", "c"], repo_error=[None, "x", None])],
    }
    assert compare(r, same, Options())  # the texts differ
    for presence in (["repo_error"], ["repos.repo_error"]):
        options = Options.from_case({"presence": presence})
        assert compare(r, same, options) == []
        problems = compare(r, missing, options)
        assert problems == [
            "repos.repo_error[0]: present in R, missing in Python (presence)",
            "repos.repo_error[1]: missing in R, present in Python (presence)",
        ]
        # the lock fingerprints the presence, not the text
        assert comparable(same, options) == comparable(r, options)
        assert comparable(missing, options) != comparable(r, options)
    # other columns are compared as always, and so is a column of another name
    other = {
        "t": "list",
        "names": ["repos"],
        "v": [_frame(url=["a", "x", "c"], repo_error=["?", None, None])],
    }
    assert compare(r, other, Options.from_case({"presence": ["repo_error"]})) == [
        "repos.url[1]: R='b' py='x'"
    ]
    assert compare(r, same, Options.from_case({"presence": ["error"]}))


def test_presence_of_list_elements() -> None:
    r = canonical({"value": 1, "error_msg": "HTTP 401 Unauthorized.\nℹ Invalid API Key"})
    options = Options.from_case({"presence": ["error_msg"]})
    assert compare(r, canonical({"value": 1, "error_msg": "unauthorized"}), options) == []
    assert compare(r, canonical({"value": 1, "error_msg": None}), options) == [
        "error_msg[0]: present in R, missing in Python (presence)"
    ]
    assert compare(canonical({"error_msg": None}), canonical({"error_msg": []}), options) == []


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


def test_r_text_mark_passes_on_corrected_text_only(tmp_path, monkeypatch, lock_dir) -> None:
    mark = {"kind": "r_bug_fixed", "ref": "U83", "reason": "typo", "r_text": [["likley", "likely"]]}
    golden = _chr_golden("likley", "same")
    case = _golden_case(tmp_path, monkeypatch, golden, _copy_case(["likely", "same"], mark))
    assert check_case(case)[0] == "pass"
    # any other difference fails: the mark describes the whole difference
    case.spec = _copy_case(["likely", "other"], mark)
    status, problems, _ = check_case(case)
    assert status == "fail"
    assert "other" in problems[0]
    # ... unless the case also differs for another reason (locked as any mark),
    # where the lock records the paths that still differ once R's text is corrected
    case.spec = _copy_case(["likely", "other"], {**mark, "xfail": True})
    assert check_case(case)[0] == "unlocked"
    assert _lock(case).diff == ("[]",)
    assert check_case(case)[0] == "xfail"
    # it is still compared: another difference is a change
    case.spec = _copy_case(["likely", "else"], {**mark, "xfail": True})
    assert check_case(case)[0] == "py_changed"
    # a substitution that changes nothing is a stale mark
    stale = {**mark, "r_text": [["likley", "likely"], ["reconized", "recognized"]]}
    case.spec = _copy_case(["likely", "same"], stale)
    status, problems, _ = check_case(case)
    assert status == "fail"
    assert "reconized" in problems[0]
    # without r_text, a mark is an expected failure as before
    plain = {"kind": "r_bug_fixed", "ref": "U83", "reason": "typo"}
    case.spec = _copy_case(["likely", "same"], plain)
    _lock(case)
    assert check_case(case)[0] == "xfail"
    # an r_text mark without xfail is compared, never locked
    case.spec = _copy_case(["likely", "same"], mark)
    assert check_case(case)[0] == "pass"
    assert stale_lock_entries([case]) == ["harness/c"]


def test_r_text_on_an_r_error_is_a_stale_mark(tmp_path, monkeypatch) -> None:
    # R's error text is never compared, so there is nothing for r_text to correct
    golden = {"ok": False, "error": "no such paper: likley", "value": None}
    spec = {
        "py": "parity.pyhelpers.github_readme_probe",
        "args": {
            "repo": {"$expr": {"py": "(_ for _ in ()).throw(ValueError('no such paper: likely'))"}}
        },
        "known_divergence": {
            "kind": "r_bug_fixed",
            "ref": "U83",
            "reason": "typo",
            "r_text": [["likley", "likely"]],
        },
    }
    status, problems, _ = check_case(_golden_case(tmp_path, monkeypatch, golden, spec))
    assert status == "fail"
    assert "changes nothing in R's golden" in problems[0]


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
    return pcases.load_corpus(toml, root=None)  # the globs alone: no file need exist


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
        # a constant or a temporary path is an argument, not an input
        ("text", {"args": {"p": {"$paper": "demo"}, "n": {"$expr": {"r": "Inf"}}}}, 1),
        ("text", {"args": {"p": {"$paper": "demo"}, "f": {"$expr": {"r": "tempfile()"}}}}, 1),
        ("text", {"args": {"p": {"$paper": "demo"}, "n": {"$expr": {"r": "Inf + x"}}}}, 2),
        # a test paper is synthetic, whatever real inputs the code also names
        (
            "text",
            {"args": {"x": {"$expr": {"r": "f(demopaper(), test_paper('a', 'https://x'))"}}}},
            2,
        ),
        # explicit tiers
        ("area", {"id": "mocked.x", "args": {"paper": {"$paper": "demo"}}}, 2),
        ("text_review", {"id": "y", "args": {}, "tier": {"level": 1, "reason": "real"}}, 1),
    ],
)
def test_classify_tier(corpus, area: str, spec: dict, tier: int) -> None:
    assert pcases.classify_tier(area, {"id": "c", **spec}, corpus) == tier


def test_corpus_inputs_must_exist(tmp_path) -> None:
    (tmp_path / "corpus.toml").write_text('[corpus]\nfiles = ["data/**"]\n')
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "real.csv").write_text("a\n1\n")
    corpus = pcases.load_corpus(tmp_path / "corpus.toml", root=tmp_path)
    assert "data/real.csv" in corpus
    assert "data/does_not_exist.csv" not in corpus  # a missing-file case is an edge case
    spec = {"id": "c", "args": {"a": {"$file": "data/real.csv"}}}
    assert pcases.classify_tier("x", spec, corpus) == 1
    spec["args"]["b"] = {"$file": "data/does_not_exist.csv"}
    assert pcases.classify_tier("x", spec, corpus) == 2


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


# -- the divergence lock (parity/lock/<area>.json) ----------------------------------------


@pytest.fixture
def lock_dir(tmp_path, monkeypatch) -> Path:
    """A throwaway parity/lock directory."""
    folder = tmp_path / "lock"
    monkeypatch.setattr(lockfile, "LOCK_DIR", folder)
    return folder


def _lock(case: Case) -> Fingerprint:
    """Lock *case* as ``python -m parity lock`` would; its fingerprint."""
    res = run_case(case, {})
    assert res.status == "unlocked", res.problems
    assert res.fingerprint is not None
    lockfile.write_lock(case.area, {**lockfile.read_lock(case.area), case.id: res.fingerprint})
    return res.fingerprint


_MARK = {"kind": "r_bug_fixed", "ref": "U83", "reason": "fixed"}


def test_lock_statuses(tmp_path, monkeypatch, lock_dir) -> None:
    case = _golden_case(tmp_path, monkeypatch, _chr_golden("a"), _copy_case(["b"], _MARK))
    res = run_case(case)
    assert (res.status, res.failing) == ("unlocked", True)
    assert "python -m parity lock -k c" in res.problems[0]
    fp = _lock(case)
    assert fp.py != fp.r and fp.diff == ("[]",)
    assert check_case(case)[0] == "xfail"

    # Python's result changed: a warning for a tier-2 case, a failure for a tier-1 one
    case.spec = _copy_case(["c"], _MARK)
    res = run_case(case)
    assert (res.status, res.failing, res.warning) == ("py_changed", False, True)
    assert any("Python's result changed" in p for p in res.problems)
    case.tier = 1
    assert (run_case(case).status, run_case(case).failing) == ("py_changed", True)
    # ... and for any case when a value turns into an exception
    case.tier = 2
    case.spec = {**_copy_case(["b"], _MARK), "args": {"x": {"$expr": {"py": "1/0"}}}}
    assert (run_case(case).status, run_case(case).failing) == ("py_changed", True)

    # R's golden changed
    case.spec = _copy_case(["b"], _MARK)
    (tmp_path / "harness" / "c.json").write_text(json.dumps(_chr_golden("z")))
    res = run_case(case)
    assert (res.status, res.failing) == ("r_changed", False)
    assert "R's golden changed" in res.problems[0]
    case.tier = 1
    assert (run_case(case).status, run_case(case).failing) == ("r_changed", True)

    # Python matches R now: the mark is stale
    case.spec = _copy_case(["z"], _MARK)
    res = run_case(case)
    assert (res.status, res.failing) == ("xpass", True)


def test_lock_pins_values_where_r_fails(tmp_path, monkeypatch, lock_dir) -> None:
    golden = {"ok": False, "error": "subscript out of bounds", "value": None}
    case = _golden_case(tmp_path, monkeypatch, golden, _copy_case(["fixed"], _MARK))
    fp = _lock(case)
    assert fp.diff == (lockfile.R_ERROR_PY_VALUE,)
    assert check_case(case)[0] == "xfail"
    # the value Python returns instead of R's error is pinned
    case.spec = _copy_case(["other"], _MARK)
    assert check_case(case)[0] == "py_changed"
    # R's error text is not: pytacheck does not compare it
    case.spec = _copy_case(["fixed"], _MARK)
    golden["error"] = "another message"
    (tmp_path / "harness" / "c.json").write_text(json.dumps(golden))
    assert check_case(case)[0] == "xfail"
    # a value where R fails is a documented fix or decision: a quirk or a type
    # detail cannot explain it, and it is not locked
    for kind in ("c_quirk", "type_detail", "r_nondeterministic"):
        case.spec = _copy_case(["fixed"], {"kind": kind, "reason": "no"})
        res = run_case(case, {})
        assert (res.status, res.fingerprint) == ("fail", None)
        assert f"not {kind}" in res.problems[0]
    # Python raising where R returned a value is locked as the exception type
    golden = _chr_golden("a")
    spec = {**_copy_case(["b"], _MARK), "args": {"x": {"$expr": {"py": "{}['k']"}}}}
    case = _golden_case(tmp_path / "2", monkeypatch, golden, spec)
    fp = _lock(case)
    assert (fp.py, fp.diff, fp.raises) == ("raises:KeyError", (lockfile.R_VALUE_PY_ERROR,), True)
    assert check_case(case)[0] == "xfail"


def test_lock_file_format(lock_dir) -> None:
    entries = {
        "b.2": Fingerprint("r2", "py2", ("x[]", "y")),
        "a.1": Fingerprint("r1", "raises:ValueError", (lockfile.R_VALUE_PY_ERROR,)),
    }
    lockfile.write_lock("area", entries)
    text = (lock_dir / "area.json").read_text()
    lines = text.splitlines()
    # one sorted line per case, so lanes locking different cases merge cleanly
    assert lines[0] == "{" and lines[-1] == "}"
    assert [line.split('"')[1] for line in lines[1:-1]] == ["a.1", "b.2"]
    assert json.loads(text)["b.2"] == {"r": "r2", "py": "py2", "diff": ["x[]", "y"]}
    assert lockfile.read_lock("area") == entries
    assert lockfile.locked_areas() == ["area"]
    lockfile.write_lock("area", {})
    assert not (lock_dir / "area.json").exists()
    (lock_dir / "bad.json").write_text('{"a.1": {"r": "x", "py": "y"}}')
    with pytest.raises(ValueError, match="a lock entry is"):
        lockfile.read_lock("bad")


def test_digests_leave_out_what_differs_from_run_to_run(monkeypatch) -> None:
    import tempfile

    digest = lockfile.digest
    # doubles to 10 significant digits: the comparison allows a relative 1e-9
    assert digest({"v": [1.0 + 1e-13]}) == digest({"v": [1.0]})
    assert digest({"v": [1.0 + 1e-6]}) != digest({"v": [1.0]})
    assert digest({"v": [-0.0]}) == digest({"v": [0.0]})

    def made() -> tuple[str, lockfile.Run]:
        with lockfile.watch_run() as run:
            folder = tempfile.mkdtemp(prefix="case-")
            stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        shutil.rmtree(folder)
        name = Path(folder).name
        text = f"{folder}/data.csv, {name}.zip, retrieved {stamp}, analysis.R"
        return text, run

    (a, run_a), (b, run_b) = made(), made()
    assert a != b
    assert digest(a, run_a) == digest(b, run_b)
    steady = lockfile.steady(a, run_a)
    assert "analysis.R" in steady and "<tmp>/case-<tempfile>/data.csv" in steady
    assert "case-<tempfile>.zip" in steady and "retrieved <now>" in steady
    # a stamp from another time, and /tmp inside another path, are data
    old = "created 2020-01-01T00:00:00Z at file://localhost/opt/grobid/grobid-home/tmp/osf.io/x"
    assert lockfile.steady(old, run_a) == old
    assert lockfile.steady("file:///tmp/x", run_a) == "file://<tmp>/x"
    # the run's data and cache directories, wherever they are
    monkeypatch.setenv("PYTACHECK_CACHE_DIR", "/home/me/cache")
    with lockfile.watch_run() as run:
        pass
    assert lockfile.steady("/home/me/cache/llm/x.json", run) == "<cache>/llm/x.json"
    # R's golden is its ok flag and value, never its error text
    r = {"ok": False, "error": "boom", "value": None}
    assert lockfile.r_digest(r) == lockfile.r_digest({**r, "error": "bang"})


def test_stale_lock_entries(tmp_path, monkeypatch, lock_dir) -> None:
    case = _golden_case(tmp_path, monkeypatch, _chr_golden("a"), _copy_case(["b"], _MARK))
    _lock(case)
    assert stale_lock_entries([case]) == []
    del case.spec["known_divergence"]
    assert stale_lock_entries([case]) == ["harness/c"]
    # a lock file of an area without cases
    assert stale_lock_entries([]) == ["harness/c"]
    assert stale_lock_entries([], areas=[]) == []


def test_every_mark_is_locked_and_every_entry_is_a_mark() -> None:
    expected = [c for c in CASES if pcases.expected_to_fail(c.spec)]
    assert [c.key for c in expected if c.id not in lockfile.read_lock(c.area)] == []
    assert stale_lock_entries(CASES) == []
    for area in lockfile.locked_areas():
        text = lockfile.lock_path(area).read_text(encoding="utf-8")
        assert text == lockfile.format_lock(lockfile.read_lock(area)), f"{area}.json"


def _harness_cases(tmp_path, monkeypatch, specs: dict[str, tuple[dict, dict]]) -> list[Case]:
    """Cases of an area ``harness`` with the given goldens, which the CLI loads."""
    monkeypatch.setattr(pcases, "GOLDEN_DIR", tmp_path / "golden")
    (tmp_path / "golden" / "harness").mkdir(parents=True)
    cases = []
    for case_id, (golden, spec) in specs.items():
        (tmp_path / "golden" / "harness" / f"{case_id}.json").write_text(json.dumps(golden))
        cases.append(
            Case(area="harness", id=case_id, spec={"id": case_id, **spec}, file=tmp_path / "x.yaml")
        )

    def load(area=None, tier=None):
        return [c for c in cases if tier is None or c.tier == tier]

    monkeypatch.setattr(parity_main, "load_cases", load)
    return cases


def test_lock_and_check_commands(tmp_path, monkeypatch, lock_dir, capsys) -> None:
    specs = {
        "same": (_chr_golden("a"), _copy_case(["a"], None)),
        "marked": (_chr_golden("a"), _copy_case(["b"], _MARK)),
        "crash": (
            {"ok": False, "error": "Error in x[[1]]: subscript out of bounds", "value": None},
            _copy_case(["value"], None),
        ),
    }
    cases = _harness_cases(tmp_path, monkeypatch, specs)
    report = tmp_path / "report.json"
    check = ["check", "--area", "harness", "--report", str(report)]
    assert parity_main.main(check) == 1  # marked, not locked; crash unmarked
    by_case = {r["case"]: r for r in json.loads(report.read_text())}
    assert by_case["harness/marked"]["status"] == "unlocked"
    assert by_case["harness/crash"]["status"] == "fail"
    assert by_case["harness/same"] == {**by_case["harness/same"], "status": "pass", "tier": 2}

    capsys.readouterr()
    assert parity_main.main(["lock", "--area", "harness", "--suggest"]) == 0
    out = capsys.readouterr().out
    assert "locked 1 cases in 1 areas (1 new, 0 changed, 0 removed)" in out
    # --suggest proposes a mark for the R crash, not for anything else
    assert '"harness/crash": {kind: r_bug_fixed, ref: U?' in out
    assert "harness/same" not in out and '"harness/marked"' not in out
    text = (lock_dir / "harness.json").read_text()
    assert list(json.loads(text)) == ["marked"]

    # locking again changes nothing; a check passes once the crash is marked
    assert parity_main.main(["lock", "--area", "harness"]) == 0
    assert (lock_dir / "harness.json").read_text() == text
    cases[2].spec["known_divergence"] = _MARK
    assert parity_main.main(["lock", "-k", "crash"]) == 0
    md = tmp_path / "summary.md"
    assert parity_main.main([*check, "--md", str(md)]) == 0
    summary = md.read_text()
    assert "| 2 | 3 | 1 | 2 |" in summary  # tier 2: 3 cases, 1 pass, 2 xfail
    assert "| 2 | r_bug_fixed | U83 | 2 | 2 |" in summary

    # a mark removed: its entry is stale until the next lock
    del cases[1].spec["known_divergence"]
    cases[1].spec["args"] = {"x": {"$chr": ["a"]}}
    assert parity_main.main(check) == 1
    assert "1 lock entries name no marked case" in capsys.readouterr().out
    assert parity_main.main(["lock", "--area", "harness"]) == 0
    assert list(json.loads((lock_dir / "harness.json").read_text())) == ["crash"]


def test_suggest_recognises_r_crashes() -> None:
    crashes = [
        "subscript out of bounds",
        "argument is of length zero",
        "missing value where TRUE/FALSE needed",
        "$ operator is invalid for atomic vectors",
        "Can't combine `..1$x` <character> and `..2$x` <double>.",
        "Join columns in `y` must be present in the data.",
        "object 'doi' not found",
    ]
    for message in crashes:
        assert parity_main.R_CRASH.search(message), message
    for message in ("The file does not exist", "paper must be a paper object", "HTTP 404"):
        assert not parity_main.R_CRASH.search(message), message


# -- the runner --------------------------------------------------------------------


@pytest.mark.parametrize("platform", ["linux", "darwin"])  # forked / spawned workers
def test_run_cases_in_processes_matches_one_process(platform, monkeypatch) -> None:
    by_key = {c.key: c for c in CASES}
    marked = [c for c in CASES if c.area == "text" and pcases.expected_to_fail(c.spec)][:3]
    picked = [by_key["text/text_search.demo.significant"], *marked]
    picked += [c for c in CASES if c.area == "rcompat_regex"][:20]
    one = parity_main.run_cases(picked, jobs=1)
    monkeypatch.setattr(parity_main.sys, "platform", platform)
    many = parity_main.run_cases(picked, jobs=3)
    assert [r.key for r in many] == [c.key for c in picked]
    assert [(r.status, r.fingerprint) for r in many] == [(r.status, r.fingerprint) for r in one]
    assert {r.status for r in one} <= {"pass", "xfail"}


def test_run_cases_keeps_what_a_case_prints(tmp_path, monkeypatch, capfd) -> None:
    code = "(print('from python'), __import__('os').system('echo from a program'), 'a')[-1]"
    spec = {"py": "copy.copy", "args": {"x": {"$expr": {"py": code}}}}
    case = _golden_case(tmp_path, monkeypatch, _chr_golden("a"), spec)
    (res,) = parity_main.run_cases([case])
    assert res.status == "pass"
    out, err = capfd.readouterr()
    assert "from" not in out + err
    assert "from python" in res.output and "from a program" in res.output
    assert "output" not in res.as_json()  # kept in the report for failing cases only


def test_reports_are_per_run() -> None:
    a = parity_main._default_report()
    assert a.parent == parity_main.OUT_DIR and a.name.startswith("report-")
    assert str(os.getpid()) in a.name


# -- no network in a case ------------------------------------------------------------


def test_network_use_is_an_error(tmp_path, monkeypatch) -> None:
    import socket

    connect = socket.socket.connect
    for code in (
        "__import__('socket').create_connection(('192.0.2.1', 80), timeout=1)",
        "__import__('socket').getaddrinfo('example.org', 443)",
        # an attempt the code catches is still an error
        "__import__('pytacheck.http', fromlist=['_']).request('GET', 'https://example.org/')",
    ):
        spec = {"py": "copy.copy", "args": {"x": {"$expr": {"py": code}}}}
        case = _golden_case(tmp_path / str(len(code)), monkeypatch, _chr_golden("a"), spec)
        status, problems, _ = check_case(case)
        assert status == "error", code
        assert "used the network" in problems[0]
    assert socket.socket.connect is connect  # the guard is gone after the case


def test_no_network_refuses_proxies_and_local_ports(monkeypatch) -> None:
    import socket

    from tests.httpmock import NetworkUse, no_network

    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    with no_network() as tried:
        with pytest.raises(NetworkUse), socket.socket() as s:
            s.connect(("127.0.0.1", 9))  # the proxy: the internet
        with pytest.raises(ConnectionRefusedError), socket.socket() as s:
            s.connect(("127.0.0.1", 10))  # another local port: closed, not network use
        assert socket.getaddrinfo("localhost", 80)
        with pytest.raises(NetworkUse), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(b"\0" * 12, ("192.0.2.1", 53))  # a DNS query of its own
    assert tried == [
        "connect to ('127.0.0.1', 9) (a proxy)",
        "send a datagram to ('192.0.2.1', 53)",
    ]
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:  # restored
        assert s.sendto(b"x", ("127.0.0.1", 9)) == 1


def test_expr_imports_the_modules_it_names() -> None:
    """``pc.statout.jasp`` resolves in a fresh process, whatever ran before
    (``check --jobs`` and ``pytest -n`` give each worker other cases first)."""
    import sys

    code = (
        "import sys; from parity.cases import decode\n"
        "import pytacheck as pc; read = pc.read\n"
        "f, g = decode({'$expr': {'py': '(pc.statout.jasp._jasp_analyses_summary, pc.read)'}})\n"
        "assert f.__module__ == 'pytacheck.statout.jasp' and g is read is pc.read\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True, cwd=ROOT)
