"""The parity command line: ``--area`` lists, ``check --strict`` and the
reviewed re-lock (``lock`` prints its changes and changes an entry only with
``--reviewed``)."""

from __future__ import annotations

import json
import multiprocessing
from pathlib import Path

import pytest

from parity import __main__ as parity_main
from parity import cases as pcases
from parity import lockfile
from parity.cases import UnknownAreaError, load_cases, parse_areas
from parity.lockfile import Fingerprint, entry_diff

_MARK = "known_divergence: {kind: r_bug_fixed, ref: U1, reason: fixed}"


def _case(case_id: str, value: str, mark: bool = False) -> str:
    """A case whose Python side returns ``[value]``; R's golden is ``["a"]``."""
    return f"  - id: {case_id}\n    py: copy.copy\n    args: {{x: {{$chr: [{value}]}}}}\n" + (
        f"    {_MARK}\n" if mark else ""
    )


class Tree:
    """Throwaway case, golden, divergences and lock directories."""

    def __init__(self, root: Path) -> None:
        self.cases, self.golden, self.lock = root / "cases", root / "golden", root / "lock"
        self.cases.mkdir()

    def area(self, area: str, *cases: str) -> None:
        (self.cases / f"{area}.yaml").write_text(f"area: {area}\ncases:\n" + "".join(cases))
        (self.golden / area).mkdir(parents=True, exist_ok=True)
        for line in "".join(cases).splitlines():
            if line.startswith("  - id: "):
                golden = {"ok": True, "error": None, "value": {"t": "chr", "v": ["a"]}}
                (self.golden / area / f"{line[8:]}.json").write_text(json.dumps(golden))

    def entries(self, area: str) -> dict[str, Fingerprint]:
        return lockfile.read_lock(area)


@pytest.fixture
def tree(tmp_path, monkeypatch) -> Tree:
    """Areas alpha, alpha_review and beta, each with a passing case; alpha has a
    marked (tier-2) case too."""
    t = Tree(tmp_path)
    (tmp_path / "divergences").mkdir()
    (tmp_path / "corpus.toml").write_text("[corpus]\n")
    corpus = pcases.load_corpus(tmp_path / "corpus.toml", root=None)
    monkeypatch.setattr(pcases, "CASES_DIR", t.cases)
    monkeypatch.setattr(pcases, "GOLDEN_DIR", t.golden)
    monkeypatch.setattr(pcases, "DIVERGENCES_DIR", tmp_path / "divergences")
    monkeypatch.setattr(pcases, "load_corpus", lambda: corpus)
    monkeypatch.setattr(pcases, "upstream_refs", lambda: {"U1": "fixed"})
    monkeypatch.setattr(lockfile, "LOCK_DIR", t.lock)
    t.area("alpha", _case("same", "a"), _case("marked", "b", mark=True))
    t.area("alpha_review", _case("same", "a"))
    t.area("beta", _case("same", "a"))
    return t


def _report(tmp_path: Path, *argv: str) -> tuple[int, dict[str, dict]]:
    """Run ``check`` with *argv*; its exit status and its report by case."""
    path = tmp_path / "report.json"
    status = parity_main.main(["check", *argv, "--report", str(path)])
    return status, {r["case"]: r for r in json.loads(path.read_text())}


# -- --area -----------------------------------------------------------------------------


def test_parse_areas() -> None:
    assert parse_areas("core") == ["core"]
    assert parse_areas(" core, text ,") == ["core", "text"]
    assert parse_areas("core,text_extract+review") == [
        "core",
        "text_extract",
        "text_extract_review",
    ]
    for bad in ("", ",", " , ", "text+extra", "+review"):
        with pytest.raises(ValueError, match="area"):
            parse_areas(bad)


def test_load_cases_takes_several_areas(tree) -> None:
    assert {c.area for c in load_cases()} == {"alpha", "alpha_review", "beta"}
    assert [c.key for c in load_cases("beta")] == ["beta/same"]
    assert {c.area for c in load_cases(["beta", "alpha_review"])} == {"alpha_review", "beta"}
    # a name without a case file is an error, not an empty selection
    with pytest.raises(UnknownAreaError, match="gamma"):
        load_cases(["beta", "gamma"])


def test_check_takes_a_list_of_areas(tree, tmp_path, capsys) -> None:
    status, by_case = _report(tmp_path, "--area", "alpha+review", "--area", "beta")
    assert status == 1  # alpha/marked is not locked yet
    assert set(by_case) == {"alpha/same", "alpha/marked", "alpha_review/same", "beta/same"}
    status, by_case = _report(tmp_path, "--area", "beta,alpha_review")
    assert status == 0
    assert set(by_case) == {"alpha_review/same", "beta/same"}
    capsys.readouterr()
    assert parity_main.main(["check", "--area", "beta,gamma"]) == 2
    assert "no case file parity/cases/<area>.yaml for gamma" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        parity_main.main(["check", "--area", "beta+extra"])
    assert "only +review may follow" in capsys.readouterr().err


def test_list_and_generate_take_a_list_of_areas(tree, monkeypatch, capsys) -> None:
    assert parity_main.main(["list", "--area", "beta,alpha_review"]) == 0
    listed = [line.split()[0] for line in capsys.readouterr().out.splitlines()]
    assert listed == ["alpha_review/same", "beta/same"]
    calls: list[list[str]] = []
    monkeypatch.setattr(parity_main, "_rscript", lambda explicit: "Rscript")
    monkeypatch.setattr(parity_main.subprocess, "call", lambda cmd, **kw: calls.append(cmd) or 0)
    assert parity_main.main(["generate", "--area", "beta,alpha+review"]) == 0
    # one R session per case file, as for a full run
    assert [Path(cmd[-1]).name for cmd in calls] == ["alpha.yaml", "alpha_review.yaml", "beta.yaml"]


# -- check --strict ---------------------------------------------------------------------


def test_strict_fails_a_changed_tier_2_case(tree, tmp_path, monkeypatch, capsys) -> None:
    assert parity_main.main(["lock"]) == 0
    tree.area("alpha", _case("same", "a"), _case("marked", "c", mark=True))
    status, by_case = _report(tmp_path, "--area", "alpha")
    marked = by_case["alpha/marked"]
    assert (status, marked["status"], marked["failing"]) == (0, "py_changed", False)
    assert "1 tier-2 marked cases changed since they were locked" in capsys.readouterr().out
    assert any("lock -k marked --reviewed" in p for p in marked["problems"])
    status, by_case = _report(tmp_path, "--area", "alpha", "--strict")
    marked = by_case["alpha/marked"]
    assert (status, marked["status"], marked["failing"]) == (1, "py_changed", True)
    # in worker processes too: forked ones, which see the throwaway directories
    if "fork" not in multiprocessing.get_all_start_methods():
        return
    monkeypatch.setattr(parity_main.sys, "platform", "linux")
    cases = load_cases("alpha")
    many = parity_main.run_cases(cases, jobs=2, strict=True)
    assert [(r.status, r.failing) for r in many] == [("pass", False), ("py_changed", True)]
    assert not parity_main.run_cases(cases, jobs=2)[1].failing


# -- lock -------------------------------------------------------------------------------


def test_lock_prints_its_changes_and_needs_review_to_change_an_entry(
    tree, tmp_path, capsys
) -> None:
    lock_file = tree.lock / "alpha.json"
    assert parity_main.main(["lock"]) == 0
    out = capsys.readouterr().out
    assert "locked 1 cases in 1 areas (1 new, 0 changed, 0 removed)" in out
    first = tree.entries("alpha")["marked"]
    assert f"  new      alpha/marked (tier 2; r_bug_fixed U1)\n      r    {first.r}\n" in out
    assert "differs from R:\n        - [0]: R='a' py='b'" in out
    text = lock_file.read_text()

    # locking again changes nothing and prints no change
    assert parity_main.main(["lock", "--area", "alpha"]) == 0
    out = capsys.readouterr().out
    assert "(0 new, 0 changed, 0 removed)" in out and "alpha/marked" not in out
    assert lock_file.read_text() == text

    # Python's result changed: the change is printed but not written
    tree.area("alpha", _case("same", "a"), _case("marked", "c", mark=True))
    assert parity_main.main(["lock", "--area", "alpha"]) == 1
    out = capsys.readouterr().out
    assert lock_file.read_text() == text
    assert "  changed  alpha/marked (tier 2; r_bug_fixed U1; not written: needs --reviewed)" in out
    assert f"      r    {first.r} (same)\n      py   {first.py} -> " in out
    assert "- [0]: R='a' py='c'" in out
    assert "1 locked entries changed and were not written" in out

    # ... and written once reviewed, with a Markdown table for the pull request
    md = tmp_path / "lock.md"
    assert parity_main.main(["lock", "--area", "alpha", "--reviewed", "--md", str(md)]) == 0
    second = tree.entries("alpha")["marked"]
    assert second.py != first.py and second.r == first.r
    table = md.read_text()
    assert "0 new, 1 changed, 0 removed." in table
    assert (
        f"| `alpha/marked` | changed | tier 2; r_bug_fixed U1 | {first.r} (same) | "
        f"{first.py} -> {second.py} | [] (same) | [0]: R='a' py='c' |"
    ) in table
    assert parity_main.main(["check", "--area", "alpha", "--report", str(tmp_path / "r")]) == 0

    # a mark removed: its entry goes without a review, since the check only gets stricter
    tree.area("alpha", _case("same", "a"), _case("marked", "a"))
    capsys.readouterr()
    assert parity_main.main(["lock"]) == 0
    out = capsys.readouterr().out
    assert "  removed  alpha/marked (no longer an expected failure)" in out
    assert not lock_file.exists()


def test_lock_removes_the_entry_of_a_case_that_matches_r(tree, capsys) -> None:
    assert parity_main.main(["lock"]) == 0
    tree.area("alpha", _case("same", "a"), _case("marked", "a", mark=True))
    capsys.readouterr()
    assert parity_main.main(["lock", "-k", "marked"]) == 1  # the mark is stale (xpass)
    out = capsys.readouterr().out
    assert "  removed  alpha/marked (tier 2; r_bug_fixed U1; it matches R now)" in out
    assert "[XPASS     ] alpha/marked" in out
    assert tree.entries("alpha") == {}


def test_entry_diff() -> None:
    old = Fingerprint("r1", "py1", ("a[]", "b"))
    assert entry_diff(None, old) == [("r", "r1"), ("py", "py1"), ("diff", "a[], b")]
    assert entry_diff(old, None) == entry_diff(None, old)
    assert entry_diff(old, Fingerprint("r2", "py1", ("b", "c"))) == [
        ("r", "r1 -> r2"),
        ("py", "py1 (same)"),
        ("diff", "+c, -a[]"),
    ]
    assert entry_diff(old, Fingerprint("r1", "raises:KeyError", ("a[]", "b")))[1:] == [
        ("py", "py1 -> raises:KeyError"),
        ("diff", "a[], b (same)"),
    ]


def test_lock_markdown_escapes_cells() -> None:
    res = parity_main.CaseResult("x/y", "x", "y", 2, kind="deliberate", ref="D1")
    res.differences = ["a|b: R='1\n2'", "second", "third", "fourth"]
    change = parity_main.LockChange("x/y", None, Fingerprint("r", "p", ("a|b",)), res)
    table = parity_main.lock_markdown([change])
    assert "1 new, 0 changed, 0 removed." in table
    row = table.splitlines()[-1]
    assert row.startswith("| `x/y` | new | tier 2; deliberate D1 | r | p | a\\|b |")
    assert row.endswith("a\\|b: R='1 2'<br>second<br>third<br>... and 1 more |")
    assert parity_main.lock_markdown([]).endswith("0 new, 0 changed, 0 removed.\n")
