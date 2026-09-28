"""The accuracy report's scorer, expectations and matrix (parity/accuracy.py), and
the wrapper that runs the reference R in its Docker image (parity/r/docker).

The report itself runs in CI as ``python -m parity accuracy --gate``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from parity import accuracy as acc
from parity.canonical import canonical
from parity.cases import ROOT


def _chr(*v: str | None) -> dict:
    return {"t": "chr", "v": list(v)}


def _output(**elements: object) -> dict:
    return canonical(dict(elements))


def _diffs(r: dict, p: dict, module: str = "m", inp: str = "x.xml") -> list[acc.Difference]:
    o = acc.Output(module, inp, "paper")
    return acc.score_output(o, {"ok": True, "value": r}, {"ok": True, "value": p}, acc.Scores())


@pytest.mark.parametrize(
    ("r", "p", "level"),
    [
        ("p = .05", "p  =  .05 ", None),  # runs of whitespace are the margin
        ("p =0.152", "p = 0.152", "whitespace"),
        ("likley to be", "likely to be", "wording"),
        ("We found 36 p-values", "We found 37 p-values", "values"),
        ("t(20) = -2.3", "t(20) = 2.3", "values"),  # a sign flip
        ("r(68) = -.58", "r(68) = .58", "values"),  # also without a leading zero
        ("see Fig.3", "see Figure 3", "wording"),
        ("t(20) = \u22122.3", "t(20) = -2.3", "wording"),  # a typographic minus
        ("COVID-19 in 2020-2021", "COVID\u201319 in 2020\u20132021", "wording"),  # dashes
        ("", "Text", "wording"),
    ],
)
def test_text_level(r: str, p: str, level: str | None) -> None:
    assert acc.text_level(r, p) == level


def test_cell_level_numbers_to_relative_1e9() -> None:
    assert acc.cell_level(1.0, 1.0 + 1e-12) is None
    assert acc.cell_level(3, 3.0) is None
    assert acc.cell_level(1.0, 1.001) == "values"
    assert acc.cell_level(None, 0) == "values"
    assert acc.cell_level("a  b", "a b") is None
    # NA, logicals and NaN are values: a change to one is never 'wording'
    assert acc.cell_level(None, "Author Notes") == "values"
    assert acc.cell_level(True, False) == "values"
    assert acc.cell_level("NaN", "Inf") == "values"
    assert acc.cell_level("a b", "ab") == "whitespace"


@pytest.mark.parametrize(
    ("r", "p", "level"),
    [
        (["p =.05", None], ["p = .05", None], "whitespace"),
        (["Python"], ["R"], "wording"),
        ([None, "x"], ["Author Notes", "x"], "values"),  # a value added where R has NA
        ([False, None], [None, False], None),  # the same cells, other rows
        ([False], [None], "values"),  # a logical lost
        ([True, False], [True, True], "values"),
        (["n = 12"], ["n = 13"], "values"),
    ],
)
def test_column_level(r: list, p: list, level: str | None) -> None:
    assert acc._column_level(r, p) == level


def test_rows_that_pair_cells_differently_are_a_difference() -> None:
    import pandas as pd

    r = _output(table=pd.DataFrame({"text": ["a", "b"], "p": [0.01, 0.2]}))
    p = _output(table=pd.DataFrame({"text": ["a", "b"], "p": [0.2, 0.01]}))
    assert [(d.field, d.level) for d in _diffs(r, p)] == [("table", "values")]
    same_rows = _output(table=pd.DataFrame({"text": ["b", "a"], "p": [0.2, 0.01]}))
    assert _diffs(r, same_rows) == []  # the row order is not compared


def test_row_f1_is_a_multiset_score() -> None:
    r = acc.Frame(["a", "b"], 3, [[1, 1, 2], ["x", "x", "y"]])
    assert acc.row_f1(r, r) == 1.0
    p = acc.Frame(["a", "b"], 2, [[1, 2], ["x", "y "]])  # one duplicate row fewer
    assert acc.row_f1(r, p) == pytest.approx(2 * 2 / 5)
    assert acc.row_f1(acc.Frame([], 0, []), acc.Frame([], 0, [])) == 1.0


def test_score_output_fields() -> None:
    import pandas as pd

    r = _output(
        traffic_light="green",
        summary_text="We found 2 p-values",
        summary_table=pd.DataFrame({"paper_id": ["a"], "n": [2]}),
        table=pd.DataFrame({"text": ["p =.05", "p = .01"], "p": [0.05, 0.01]}),
        report=["Some  text."],
    )
    same = _diffs(r, r)
    assert same == []
    p = _output(
        traffic_light="yellow",
        summary_text="We found 3 p-values",
        summary_table=pd.DataFrame({"paper_id": ["a"], "n": [3], "extra": [1]}),
        table=pd.DataFrame({"text": ["p = .05", "p = .01"], "p": [0.05, 0.01]}),
        report=["Some text."],
    )
    found = {(d.field, d.level) for d in _diffs(r, p)}
    assert found == {
        ("traffic_light", "values"),
        ("summary_text", "values"),
        ("summary_table", "values"),  # a column only Python has
        ("summary_table.n", "values"),
        ("table.text", "whitespace"),
    }


def test_run_differences_and_row_counts() -> None:
    import pandas as pd

    o = acc.Output("m", "x.xml", "paper")
    scores = acc.Scores()
    failed = acc.score_output(o, {"ok": False, "error": "boom"}, {"ok": True}, scores)
    assert [(d.field, d.level) for d in failed] == [("run", "values")]
    assert acc.score_output(o, {"ok": False}, {"ok": False}, scores) == []
    assert scores.agree["run"] == 1 and scores.total["run"] == 2
    r = _output(table=pd.DataFrame({"x": [1, 2]}))
    p = _output(table=pd.DataFrame({"x": [1]}))
    assert [(d.field, d.detail) for d in _diffs(r, p)] == [("table", "rows R=2 py=1")]


def _expected(tmp_path: Path, text: str) -> acc.Expected:
    path = tmp_path / "expected.yaml"
    path.write_text(text, encoding="utf-8")
    return acc.load_expected(path)


def test_entries_explain_up_to_their_level(tmp_path: Path) -> None:
    exp = _expected(
        tmp_path,
        """
differences:
  - {module: [m, n], input: "*.xml", field: "table.*", match: wording,
     kind: r_bug_fixed, ref: U16, reason: r}
""",
    )
    (entry,) = exp.entries
    d = acc.Difference("m", "upstream/a/b.xml", "table.text", "whitespace", "")
    assert entry.explains(d)
    assert not entry.explains(acc.Difference("m", "a/b.xml", "table.text", "values", ""))
    assert not entry.explains(acc.Difference("o", "a/b.xml", "table.text", "wording", ""))
    assert not entry.explains(acc.Difference("m", "a/b.json", "table.text", "wording", ""))
    # the short name of an input matches too
    short = "upstream/metacheck/tests/testthat/fixtures/problems/1.xml"
    by_short = _expected(
        tmp_path,
        """
differences:
  - {module: m, input: problems/1.xml, field: run, match: values,
     kind: better_logic, ref: D6, reason: r}
""",
    ).entries[0]
    assert by_short.explains(acc.Difference("m", short, "run", "values", ""))


@pytest.mark.parametrize(
    ("entry", "problem"),
    [
        ("{module: m, input: i, field: f, match: values, kind: r_bug_fixed, reason: r}", "U-entry"),
        ("{module: m, input: i, field: f, match: values, kind: c_quirk, reason: r}", "tier-1"),
        (
            "{module: m, input: i, field: f, match: some, kind: r_bug_fixed, ref: U16, reason: r}",
            "match is one of",
        ),
        ("{module: m, field: f, match: values, kind: r_bug_fixed, ref: U16, reason: r}", "input"),
        (
            "{module: m, input: i, field: f, match: values, kind: r_bug_fixed, ref: U99999, "
            "reason: r}",
            "not an entry",
        ),
        (
            "{module: m, input: i, field: f, match: values, kind: r_bug_fixed, ref: U16, "
            "reason: r, extra: 1}",
            "unknown keys",
        ),
    ],
)
def test_expected_entries_are_validated(tmp_path: Path, entry: str, problem: str) -> None:
    with pytest.raises(ValueError, match=problem):
        _expected(tmp_path, f"differences:\n  - {entry}\n")


def test_floors_and_stale_entries(tmp_path: Path) -> None:
    exp = _expected(
        tmp_path,
        """
differences:
  - {module: m, input: "*", field: traffic_light, match: values,
     kind: r_bug_fixed, ref: U30, reason: r}
floors:
  - {module: m, traffic_light: 0.4, kind: r_bug_fixed, ref: U30, reason: r}
  - {module: n, traffic_light: 0.5, kind: r_bug_fixed, ref: U30, reason: r}
""",
    )
    scores = {"m": acc.Scores(), "n": acc.Scores()}
    scores["m"].add("traffic_light", 1, 2)  # 50%: above m's floor
    scores["n"].add("traffic_light", 1, 1)  # n does not need its floor
    rep = acc.Report(2, scores, [], exp, [], partial=False, seconds={})
    assert rep.warnings == []
    assert len(rep.stale) == 2  # the unused entry and n's floor
    assert not rep.passed
    scores["m"].add("traffic_light", 0, 2)  # 25%: below the floor
    assert rep.warnings and "needs human review" in rep.warnings[0]
    partial = acc.Report(2, scores, [], exp, [], partial=True, seconds={})
    assert partial.stale == []


def test_floors_must_be_below_the_default(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="share below"):
        _expected(
            tmp_path,
            "floors:\n  - {module: m, traffic_light: 0.95, kind: r_bug_fixed, ref: U30, "
            "reason: r}\n",
        )
    floor = "  - {module: m, traffic_light: 0.5, kind: r_bug_fixed, ref: U30, reason: r}\n"
    with pytest.raises(ValueError, match="already has a floor"):
        _expected(tmp_path, "floors:\n" + floor + floor)


def test_committed_matrix_expected_and_goldens() -> None:
    outputs = acc.load_matrix()
    modules = acc.by_module(outputs)
    assert len(outputs) == sum(len(v) for v in modules.values())
    assert len({o.id for o in outputs}) == len(outputs)
    acc.load_expected()  # validates every entry against docs/UPSTREAM_ISSUES.md
    for module, outs in modules.items():
        golden = acc.read_golden(module)
        assert golden is not None, f"no accuracy golden for {module}"
        assert set(golden["outputs"]) == {o.input for o in outs}, module
    assert {p.name for p in acc.GOLDEN_DIR.glob("*.json.gz")} == {
        acc.golden_path(m).name for m in modules
    }


def test_left_over_goldens_are_problems(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A golden of a module, or an output, that the matrix no longer has fails the gate."""
    monkeypatch.setattr(acc, "GOLDEN_DIR", tmp_path)
    failed = {"ok": False, "error": "x"}
    o = acc.Output("m", "a.xml", "paper")
    acc.write_golden("m", {"module": "m", "outputs": {"a.xml": failed, "b.xml": failed}})
    acc.write_golden("gone", {"module": "gone", "outputs": {}})
    rep = acc.score([o], {o: failed}, acc.Expected([], {}), partial=False)
    assert rep.problems == [
        "m: R's golden has inputs not in the matrix: ['b.xml']",
        "golden gone.json.gz: no module of the matrix has it: delete it",
    ]
    assert not rep.passed
    partial = acc.score([o], {o: failed}, acc.Expected([], {}), partial=True)
    assert len(partial.problems) == 1  # -m runs check only the modules they run


def test_an_unchanged_golden_keeps_its_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """gzip's bytes depend on the Python (3.13 writes another OS byte in the header)
    and on its zlib, so regenerating the same outputs leaves the file as it is."""
    monkeypatch.setattr(acc, "GOLDEN_DIR", tmp_path)
    data = {"module": "m", "outputs": {"a.xml": {"ok": False, "error": "x"}}}
    acc.write_golden("m", data)
    path = acc.golden_path("m")
    other = bytearray(path.read_bytes())
    other[9] = 7 if other[9] != 7 else 3  # the OS byte
    path.write_bytes(other)
    acc.write_golden("m", data)
    assert path.read_bytes() == other
    changed = {"module": "m", "outputs": {}}
    acc.write_golden("m", changed)
    assert acc.read_golden("m") == changed
    path.write_bytes(b"not gzip")
    acc.write_golden("m", data)
    assert acc.read_golden("m") == data


def test_matrix_inputs_must_be_in_the_corpus(tmp_path: Path) -> None:
    path = tmp_path / "matrix.toml"
    path.write_text('[papers]\nmodules = ["marginal"]\ninputs = ["tests/nope.xml"]\n')
    with pytest.raises(ValueError, match="not an input of the realistic corpus"):
        acc.load_matrix(path)


def test_run_cases_reads_a_paper_once_and_keeps_its_warnings(tmp_path: Path) -> None:
    """``run_cases.R --out`` writes there, and a paper read once per session still
    gives every case the warnings its read raised (the goldens list them)."""
    import json
    import os
    import subprocess

    import yaml

    from parity.cases import ROOT, reference_r

    rscript = reference_r()
    if rscript is None:
        pytest.skip("needs the reference R (PYTACHECK_RSCRIPT, R >= 4.5)")
    paper = {"$paper": "tests/bibr12/fixtures/edge_types.json"}  # its read warns
    cases = tmp_path / "cases.yaml"
    spec = {
        "area": "once",
        "cases": [{"id": i, "module": "all_p_values", "args": {"paper": paper}} for i in "ab"],
    }
    cases.write_text(yaml.safe_dump(spec), encoding="utf-8")
    env = {**os.environ, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC"}
    script = str(ROOT / "parity" / "r" / "run_cases.R")
    cmd = [rscript, script, str(ROOT), str(cases), "--out", str(tmp_path / "out")]
    subprocess.run(cmd, check=True, capture_output=True, env=env, cwd=ROOT)
    a, b = (json.loads((tmp_path / "out" / "once" / f"{i}.json").read_text()) for i in "ab")
    assert a["warnings"] and a["warnings"] == b["warnings"]
    assert a["value"] == b["value"]


def _r_prints(
    monkeypatch: pytest.MonkeyPatch, stdout: str, status: int = 0, stderr: str = ""
) -> list[list[str]]:
    """R, as ``subprocess.run`` in parity.accuracy sees it: the commands it was given."""
    calls: list[list[str]] = []

    def run(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        return subprocess.CompletedProcess(args, status, stdout, stderr)

    monkeypatch.setattr(acc.subprocess, "run", run)
    return calls


def test_suggests_library_is_checked_in_r(monkeypatch: pytest.MonkeyPatch) -> None:
    """R reports the library and whether careless loads from it: R's paths need not
    be the host's (parity/r/docker runs R in a container)."""
    calls = _r_prints(monkeypatch, "/opt/r/lib/R/suggests\nTRUE")
    assert acc.suggests_library("Rscript") == "/opt/r/lib/R/suggests"
    assert 'requireNamespace("careless", lib.loc = lib' in calls[0][-1]
    _r_prints(monkeypatch, "/opt/r/lib/R/suggests\nFALSE")
    with pytest.raises(SystemExit, match="careless does not load from /opt/r/lib/R/suggests"):
        acc.suggests_library("Rscript")
    _r_prints(monkeypatch, "", status=2, stderr="Error: no R here\n")
    with pytest.raises(SystemExit, match=r"exit status 2\): Error: no R here$"):
        acc.suggests_library("Rscript")


def test_suggests_library_of_the_reference_r() -> None:
    from parity.cases import reference_r

    rscript = reference_r()
    if rscript is None:
        pytest.skip("needs the reference R (PYTACHECK_RSCRIPT, R >= 4.5)")
    assert Path(acc.suggests_library(rscript)).name == "suggests"


# -- the R image's wrapper (parity/r/docker/Rscript), with a docker that prints its arguments

_WRAPPER = ROOT / "parity" / "r" / "docker" / "Rscript"
needs_bash = pytest.mark.skipif(
    os.name == "nt"
    or not (shutil.which("bash") and shutil.which("git"))
    or not (ROOT / ".git").exists(),
    reason="the wrapper is a bash script that asks git about the checkout",
)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def _wrapper(
    tmp_path: Path, cwd: Path, image_exists: bool = True, **env: str
) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    docker = bin_dir / "docker"
    exists = 0 if image_exists else 1
    docker.write_text(f'#!/bin/sh\n[ "$1" = image ] && exit {exists}\nprintf "%s\\n" "$@"\n')
    docker.chmod(0o755)
    base = {k: v for k, v in os.environ.items() if k not in ("PYTACHECK_R_IMAGE", "PWD")}
    # git must not find a repository above tmp_path
    base.update(PATH=f"{bin_dir}{os.pathsep}{base['PATH']}", GIT_CEILING_DIRECTORIES=str(tmp_path))
    return subprocess.run(
        [str(_WRAPPER), "--vanilla", "-e", "1"],
        cwd=cwd,
        env={**base, **env},
        capture_output=True,
        text=True,
        check=False,
    )


def _after(args: list[str], flag: str) -> set[str]:
    return {args[i + 1] for i, a in enumerate(args[:-1]) if a == flag}


@needs_bash
def test_r_image_wrapper_mounts_the_checkout_and_keeps_r_libs(tmp_path: Path) -> None:
    run = _wrapper(tmp_path, ROOT, R_LIBS="/opt/r/lib/R/suggests", R_LIBS_USER="/home/u/R")
    assert run.returncode == 0, run.stderr
    args = run.stdout.splitlines()
    top = _git(ROOT, "rev-parse", "--show-toplevel")
    common = _git(ROOT, "rev-parse", "--path-format=absolute", "--git-common-dir")
    assert {f"{p}:{p}" for p in ("/tmp", top, common)} <= _after(args, "-v")
    assert args[args.index("-w") + 1] == str(ROOT)
    assert "--rm" in args and _after(args, "--pull") == {"never"}
    assert _after(args, "--network") == {"host"}
    assert _after(args, "--user") == {f"{os.getuid()}:{os.getgid()}"}
    env = _after(args, "-e")
    assert {"R_LIBS", "HOME=/home/r"} <= env
    assert not env & {"R_LIBS_USER", "PATH", "HOME", "PWD"}
    # HOME is private to the run (/tmp is shared, and anyone can write there)
    uid, gid = os.getuid(), os.getgid()
    assert _after(args, "--tmpfs") == {f"/home/r:uid={uid},gid={gid},mode=0700"}
    # the image of the metacheck commit the checkout has
    sub = ROOT / "upstream" / "metacheck"
    if (sub / ".git").exists():
        rev = _git(sub, "rev-parse", "HEAD")
    else:
        rev = _git(ROOT, "rev-parse", ":upstream/metacheck")
    assert args[-5:] == [f"pytacheck-r-reference:{rev[:8]}", "Rscript", "--vanilla", "-e", "1"]


@needs_bash
def test_r_image_wrapper_outside_a_checkout(tmp_path: Path) -> None:
    """Called from elsewhere, it mounts that folder and its own checkout."""
    work = tmp_path / "work"
    work.mkdir()
    run = _wrapper(tmp_path, work, PYTACHECK_R_IMAGE="r:test")
    assert run.returncode == 0, run.stderr
    args = run.stdout.splitlines()
    top = _git(ROOT, "rev-parse", "--show-toplevel")
    assert {f"{work}:{work}", f"{top}:{top}"} <= _after(args, "-v")
    assert args[-5] == "r:test"


@needs_bash
def test_r_image_wrapper_mounts_tmpdir(tmp_path: Path) -> None:
    """Python's temporary files (the accuracy report's cases) are in $TMPDIR."""
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    run = _wrapper(tmp_path, ROOT, TMPDIR=f"{tmp}/", PYTACHECK_R_IMAGE="r:test")
    assert run.returncode == 0, run.stderr
    assert f"{tmp}:{tmp}" in _after(run.stdout.splitlines(), "-v")
    # docker would make a missing folder, as root
    run = _wrapper(tmp_path, ROOT, TMPDIR=str(tmp_path / "gone"), PYTACHECK_R_IMAGE="r:test")
    assert run.returncode == 0, run.stderr
    assert not [v for v in _after(run.stdout.splitlines(), "-v") if "gone" in v]


@needs_bash
def test_r_image_wrapper_without_the_image(tmp_path: Path) -> None:
    run = _wrapper(tmp_path, ROOT, image_exists=False, PYTACHECK_R_IMAGE="r:missing")
    assert run.returncode == 125
    assert "no Docker image r:missing: build it with" in run.stderr
    assert run.stderr.strip().endswith("parity/r/docker/build.sh")
    assert not run.stdout


def _set_cell(p) -> None:
    p.text.loc[0, "text"] = "edited"


# how each stub module changes the paper it is given
_EDITS = {
    "reads": lambda p: p.text,
    "list_cell": lambda p: p.info["keywords"].iat[0].append("b"),
    "nested_extra": lambda p: p.extra["nested"]["a"].append(2),
    "cell": _set_cell,
    "api": lambda p: p.__setitem__("info", p.info.copy()),
}


def test_run_python_shares_each_paper_and_catches_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every output of an input gets the same paper, read once; an output that
    changes it through the API is flagged, and a change of its content anywhere
    (a list inside a cell, a nested ``extra`` entry) flags every output of it."""
    import pandas as pd

    import pytacheck as pc
    import pytacheck.module as mod

    reads: list[str] = []
    given: dict[str, set[int]] = {}

    def read(path: Path) -> pc.Paper:
        reads.append(Path(path).name)
        p = pc.test_paper(["A sentence.", "Another one."])
        info = p.info
        p.info = info.assign(keywords=pd.Series([["a"]] * len(info), index=info.index))
        p.extra["nested"] = {"a": [1]}
        return p

    def module_run(paper: pc.Paper, module: str, **_: object) -> dict:
        given.setdefault(module, set()).add(id(paper))
        _EDITS[module](paper)
        return {"summary_text": module}

    monkeypatch.setenv("PYTACHECK_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(pc, "read", read)
    monkeypatch.setattr(mod, "module_run", module_run)
    outputs = [acc.Output(m, m, "paper") for m in _EDITS]
    outputs.append(acc.Output("reads", "list_cell", "paper"))  # after list_cell changed it
    results = acc.run_python(outputs)
    assert all(r["ok"] for r in results.values())
    problems = {(o.module, o.input): r.get("problem") for o, r in results.items()}
    changed = "a module changed this paper (modules must not modify their input)"
    assert problems == {
        ("reads", "reads"): None,
        ("list_cell", "list_cell"): changed,
        ("nested_extra", "nested_extra"): changed,
        ("cell", "cell"): changed,
        ("api", "api"): "changed its paper (modules must not modify their input)",
        ("reads", "list_cell"): changed,
    }
    assert sorted(reads) == sorted(_EDITS)  # each input read once
    assert len(given["reads"]) == 2  # the reads of two inputs, two papers
