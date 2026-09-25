"""Every parity case must match its R golden (see parity/ and docs/PARITY.md),
and the harness itself (encoder, comparator, runners) behaves as R does."""

from __future__ import annotations

import json
import math
import os
import random
import shutil
import struct
import subprocess
import time
from pathlib import Path

import pandas as pd
import pytest

from parity import cases as pcases
from parity.__main__ import check_case
from parity.canonical import canonical, complex_as_character, portable
from parity.cases import Case, load_cases, parity_id
from parity.compare import Options, compare, error_matches, normalize_error, summarize

CASES = load_cases()
ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parity
@pytest.mark.parametrize("case", CASES, ids=[c.key for c in CASES])
def test_parity(case) -> None:
    status, problems, _ = check_case(case)
    if status == "missing":
        pytest.fail(f"no golden for {case.key}: run `python -m parity generate --area {case.area}`")
    if status == "skip":
        pytest.skip(problems[0])
    if status == "xfail":
        pytest.xfail(case.spec["known_divergence"])
    assert status == "pass", f"{case.key} differs from R:\n{summarize(problems)}"


# -- the harness ---------------------------------------------------------------


def test_divergence_registry_names_cases() -> None:
    keys = {c.key for c in CASES}
    registry = pcases.load_divergences()
    assert set(registry) <= keys, sorted(set(registry) - keys)
    for key in registry:
        assert next(c for c in CASES if c.key == key).spec.get("known_divergence")


def test_divergence_kinds_are_known() -> None:
    kinds = set(pcases.DIVERGENCE_KINDS) | {"unclassified"}
    for case in CASES:
        kind = pcases.divergence_kind(case.spec)
        assert kind is None or kind in kinds, (case.key, kind)


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

# (Re, Im, R's as.character()) from R 4.5.3
_R_COMPLEX = [
    ("0x1p+0", "0x1p+1", "1+2i"),
    ("0x1.5555555555555p-2", "0x1.5555555555555p-1", "0.333333333333333+0.666666666666667i"),
    ("0x1.2a05f2p+33", "-0x1.b7cdfd9d7bdbbp-34", "1e+10-1e-10i"),
    ("nan", "0x1p+0", "NaN+1i"),
    ("inf", "-inf", "Inf-Infi"),
    ("0x0p+0", "0x0p+0", "0+0i"),
    ("0x1.b69b4ba630f35p+56", "0x1.999999999999ap-4", "123456789012345680+0.1i"),
    ("-0x0p+0", "-0x0p+0", "0+0i"),
    ("0x1.c6bf52634p+49", "0x1.203af9ee75616p-50", "1e+15+1e-15i"),
    ("0x1.86ap+16", "0x1.86ap+16", "1e+05+1e+05i"),
    ("0x1.4f8b588e368f1p-17", "0x1.a36e2eb1c432dp-14", "1e-05+1e-04i"),
    ("0x1.930795fdc43d6p-38", "-0x1.9374bc6a7ef9ep-3", "5.72739555245840e-12-0.197i"),
    ("-0x1.8p+0", "-0x1.2p+1", "-1.5-2.25i"),
    ("0x1.7e43c8800759cp+996", "-0x1.56e1fc2f8f359p-997", "1e+300-1e-300i"),
    ("0x1.3333333333334p-2", "0x1.9p+6", "0.3+100i"),
]


def _hex(s: str) -> float:
    return float(s) if s.lstrip("-") in ("nan", "inf") else float.fromhex(s)


@pytest.mark.parametrize(("re_", "im", "expected"), _R_COMPLEX)
def test_complex_as_character(re_: str, im: str, expected: str) -> None:
    assert complex_as_character(complex(_hex(re_), _hex(im))) == expected


def test_canonical_complex_and_raw() -> None:
    na = complex(math.nan, math.nan)  # pytacheck's missing complex
    assert canonical(pd.Series([1 + 2j, na])) == {"t": "cplx", "v": ["1+2i", None]}
    assert canonical([0.5 - 1j]) == {"t": "cplx", "v": ["0.5-1i"]}
    assert canonical(b"\x00\x0f\xff") == {"t": "raw", "v": ["00", "0f", "ff"]}
    r_na = struct.unpack("<d", struct.pack("<Q", 0x7FF00000000007A2))[0]  # NA_real_
    assert canonical([complex(r_na, 1.0)]) == {"t": "lgl", "v": [None]}


@pytest.mark.r
def test_complex_as_character_fuzz_against_r(tmp_path: Path) -> None:
    """R's as.character() of 20,000 complex numbers, random bit patterns included."""
    rng = random.Random(20260925)

    def value() -> float:
        k = rng.random()
        if k < 0.3:
            while True:
                x = struct.unpack("<d", struct.pack("<Q", rng.getrandbits(64)))[0]
                if math.isfinite(x):
                    return x
        if k < 0.7:
            digits = rng.randint(1, 17)
            m = f"{rng.uniform(1, 10):.{digits - 1}f}e{rng.randint(-30, 30)}"
            return float(m) * rng.choice([1, -1])
        if k < 0.85:
            return rng.choice([0.0, -0.0, 0.1, 1e5, 1e15, 1e16, 99999.5, 1e22, 1e23, 1e27, 5e-324])
        return round(rng.uniform(-1e6, 1e6), rng.randint(0, 10))

    values = [(value(), value()) for _ in range(20000)]
    values += [(math.nan, 1.0), (math.inf, -math.inf), (1.0, -0.0)]
    inp = tmp_path / "in.tsv"
    inp.write_text("".join(f"{a.hex()}\t{b.hex()}\n" for a, b in values))
    out = tmp_path / "out.txt"
    _run_r(
        f'x <- read.delim("{inp}", header = FALSE, colClasses = "character"); '
        f"z <- complex(real = as.numeric(x[[1]]), imaginary = as.numeric(x[[2]])); "
        f'writeLines(as.character(z), "{out}")'
    )
    expected = out.read_text().split("\n")[:-1]
    got = [complex_as_character(complex(a, b)) for a, b in values]
    bad = [(a, b, e, g) for (a, b), e, g in zip(values, expected, got, strict=True) if e != g]
    assert bad == []


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
