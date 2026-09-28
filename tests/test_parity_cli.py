"""The parity command line: ``--area`` lists, ``check --strict`` and the
reviewed re-lock (``lock`` prints its changes and changes an entry only with
``--reviewed``)."""

from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path

import pytest

from parity import __main__ as parity_main
from parity import canonical as pcanonical
from parity import cases as pcases
from parity import lockfile
from parity.cases import UnknownAreaError, case_path, load_cases, load_quarantine, parse_areas
from parity.lockfile import Fingerprint, entry_diff

#: what the tree fixture replaces: the real check of the checkout's setup
_ENVIRONMENT_PROBLEMS = parity_main.environment_problems
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
    monkeypatch.setattr(parity_main, "environment_problems", lambda: [])  # not this checkout's
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


# -- quarantine -------------------------------------------------------------------------

#: the number of cases parity/quarantine.yaml lists. Adding a case means raising this
#: in the same reviewed change; it may otherwise only go down.
QUARANTINE_CEILING = 0
_WHY = "needs a tool that cannot be installed here"


def _quarantine(tmp_path: Path, monkeypatch, *keys: str) -> Path:
    path = tmp_path / "quarantine.yaml"
    listed = "".join(f"  - {{case: {k}, reason: {_WHY!r}}}\n" for k in keys)
    path.write_text(
        f"max_cases: {len(keys)}\ncases:\n{listed}" if keys else "max_cases: 0\ncases: []\n"
    )
    monkeypatch.setattr(pcases, "QUARANTINE_FILE", path)
    return path


def test_the_quarantine_file_holds_what_the_ceiling_says() -> None:
    quarantined = load_quarantine()
    assert len(quarantined) == QUARANTINE_CEILING
    assert not set(quarantined) - {c.key for c in load_cases()}


def test_a_quarantined_case_never_fails_the_run(tree, tmp_path, monkeypatch, capsys) -> None:
    tree.area("beta", _case("same", "b"))  # differs from R's golden
    status, by_case = _report(tmp_path, "--area", "beta")
    assert (status, by_case["beta/same"]["status"]) == (1, "fail")
    _quarantine(tmp_path, monkeypatch, "beta/same")
    for extra in ([], ["--strict"]):
        capsys.readouterr()
        status, by_case = _report(tmp_path, "--area", "beta", *extra)
        got = by_case["beta/same"]
        assert (status, got["status"], got["failing"]) == (0, "quarantined", False)
        assert got["problems"][0] == f"quarantined: {_WHY}"
        assert len(got["problems"]) > 1  # how it differs from R is kept
        out = capsys.readouterr().out
        assert f"1 quarantined (parity/quarantine.yaml; not failures):\n  beta/same: {_WHY}" in out
    # in worker processes too
    if "fork" in multiprocessing.get_all_start_methods():
        monkeypatch.setattr(parity_main.sys, "platform", "linux")
        many = parity_main.run_cases(load_cases("beta"), jobs=2, strict=True, quarantine=True)
        assert [(r.status, r.failing) for r in many] == [("quarantined", False)]


def test_a_quarantined_change_does_not_fail_under_strict(tree, tmp_path, monkeypatch) -> None:
    assert parity_main.main(["lock"]) == 0
    tree.area("alpha", _case("same", "a"), _case("marked", "c", mark=True))
    status, by_case = _report(tmp_path, "--area", "alpha", "--strict")
    assert (status, by_case["alpha/marked"]["status"]) == (1, "py_changed")
    _quarantine(tmp_path, monkeypatch, "alpha/marked")
    status, by_case = _report(tmp_path, "--area", "alpha", "--strict")
    assert (status, by_case["alpha/marked"]["status"]) == (0, "quarantined")
    assert by_case["alpha/marked"]["failing"] is False


def test_a_quarantined_case_that_passes_is_a_pass(tree, tmp_path, monkeypatch) -> None:
    _quarantine(tmp_path, monkeypatch, "beta/same")
    status, by_case = _report(tmp_path, "--area", "beta")
    assert (status, by_case["beta/same"]["status"]) == (0, "pass")


def test_every_failing_status_can_be_quarantined(tree, tmp_path, monkeypatch) -> None:
    def quarantined(case: str) -> None:
        area = case.partition("/")[0]
        _quarantine(tmp_path, monkeypatch)
        status, by_case = _report(tmp_path, "--area", area)
        wanted = by_case[case]["status"]
        assert (status, wanted) == (1, wanted) and wanted in ("xpass", "unlocked", "missing")
        _quarantine(tmp_path, monkeypatch, case)
        status, by_case = _report(tmp_path, "--area", area)
        assert (status, by_case[case]["status"]) == (0, "quarantined")

    # xpass: an expected failure that now matches R
    tree.area("alpha", _case("same", "a"), _case("marked", "a", mark=True))
    quarantined("alpha/marked")
    # unlocked: marked, and without a lock entry
    tree.area("alpha", _case("same", "a"), _case("marked", "b", mark=True))
    quarantined("alpha/marked")
    # missing: no golden
    (tree.golden / "beta" / "same.json").unlink()
    quarantined("beta/same")


def test_a_quarantine_entry_naming_no_case_fails_check(tree, tmp_path, monkeypatch, capsys) -> None:
    _quarantine(tmp_path, monkeypatch, "beta/gone")
    assert _report(tmp_path)[0] == 1
    assert "1 quarantine entries name no case: beta/gone" in capsys.readouterr().out
    # a selection that may leave out cases cannot tell
    assert _report(tmp_path, "--area", "beta", "-k", "same")[0] == 0
    # the entries of areas that were not selected are not judged
    assert _report(tmp_path, "--area", "alpha_review")[0] == 0
    assert _report(tmp_path, "--area", "beta")[0] == 1


def test_lock_ignores_the_quarantine(tree, tmp_path, monkeypatch, capsys) -> None:
    # a quarantined marked case is still locked ...
    _quarantine(tmp_path, monkeypatch, "alpha/marked")
    assert parity_main.main(["lock"]) == 0
    assert "(1 new, 0 changed, 0 removed)" in capsys.readouterr().out
    assert set(tree.entries("alpha")) == {"marked"}
    # ... and its entry removed when the mark is stale
    tree.area("alpha", _case("same", "a"), _case("marked", "a", mark=True))
    assert parity_main.main(["lock"]) == 1
    out = capsys.readouterr().out
    assert "[XPASS     ] alpha/marked" in out and "  removed  alpha/marked" in out
    assert tree.entries("alpha") == {}


def test_pytest_skips_a_quarantined_case(tree, tmp_path, monkeypatch) -> None:
    from tests.test_parity import test_parity

    tree.area("beta", _case("same", "b"))  # differs from R's golden
    (case,) = load_cases("beta")
    with pytest.raises(AssertionError, match="differs from R"):
        test_parity(case)
    _quarantine(tmp_path, monkeypatch, "beta/same")
    with pytest.raises(pytest.skip.Exception, match=f"quarantined: {_WHY}"):
        test_parity(case)


def test_the_quarantine_file_is_checked(tmp_path) -> None:
    def load(text: str) -> dict[str, str]:
        path = tmp_path / "q.yaml"
        path.write_text(text)
        return load_quarantine(path)

    good = "max_cases: 1\ncases:\n  - {case: a/b, reason: why}\n"
    assert load(good) == {"a/b": "why"}
    assert load("max_cases: 0\ncases: []\n") == {}
    # the ratchet: the count is the number of cases listed, so it goes down with them
    with pytest.raises(ValueError, match=r"max_cases is 2 but 1 cases are listed"):
        load(good.replace("max_cases: 1", "max_cases: 2"))
    with pytest.raises(ValueError, match=r"max_cases is 0 but 1 cases are listed"):
        load(good.replace("max_cases: 1", "max_cases: 0"))
    with pytest.raises(ValueError, match="no reason"):
        load("max_cases: 1\ncases:\n  - {case: a/b, reason: ' '}\n")
    with pytest.raises(ValueError, match="listed twice"):
        load("max_cases: 2\ncases:\n  - {case: a/b, reason: x}\n  - {case: a/b, reason: y}\n")
    with pytest.raises(ValueError, match="keys max_cases and cases"):
        load("cases: []\n")
    with pytest.raises(ValueError, match=r"a case \(area/id\) and a reason"):
        load("max_cases: 1\ncases:\n  - {case: a/b}\n")
    for empty in ("reason:", "reason: ~", "reason: 3"):  # not a text
        with pytest.raises(ValueError, match=r"a case \(area/id\) and a reason"):
            load(f"max_cases: 1\ncases:\n  - case: a/b\n    {empty}\n")
    with pytest.raises(ValueError, match="max_cases is a number"):
        load(good.replace("max_cases: 1", "max_cases: true"))
    # no file, no quarantine
    assert load_quarantine(tmp_path / "none.yaml") == {}
    # a rewrite is seen, even when the file's time stamp does not move
    path = tmp_path / "q.yaml"
    stamp = path.stat().st_mtime_ns
    path.write_text("max_cases: 1\ncases:\n  - {case: c/d, reason: why}\n")
    os.utime(path, ns=(stamp, stamp))
    assert load_quarantine(path) == {"c/d": "why"}


# -- a fresh worktree -------------------------------------------------------------------


def test_check_and_lock_stop_when_the_checkout_lacks_its_setup(
    tree, tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(parity_main, "environment_problems", _ENVIRONMENT_PROBLEMS)
    monkeypatch.setattr(parity_main, "ROOT", tmp_path)  # no upstream/metacheck here
    monkeypatch.setattr(parity_main.importlib.util, "find_spec", lambda name: None)
    for command in ("check", "lock"):
        assert parity_main.main([command]) == 2
        err = capsys.readouterr().err
        assert "upstream/metacheck is empty" in err
        assert "git submodule update --init" in err
        assert "pyreadstat, xlrd, snowballstemmer not installed" in err
        assert "uv sync --locked --all-extras" in err
    assert not (tmp_path / "parity").exists()  # nothing ran
    (tmp_path / "upstream" / "metacheck").mkdir(parents=True)
    (tmp_path / "upstream" / "metacheck" / "DESCRIPTION").write_text("Package: metacheck\n")
    monkeypatch.setattr(parity_main.importlib.util, "find_spec", lambda name: object())
    assert parity_main.environment_problems() == []


def test_the_checkout_is_repo_however_it_is_reached(tmp_path, monkeypatch) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("no symlinks here")
    spellings = pcanonical.checkout_spellings(real, link, tmp_path / "elsewhere", "")
    assert {os.path.realpath(real), str(link)} <= set(spellings)
    assert str(tmp_path / "elsewhere") not in spellings
    assert spellings == tuple(sorted(spellings, key=len, reverse=True))
    spelled = pcanonical.Spellings.of(real, link, tmp_path / "elsewhere", "")
    assert os.path.realpath(real) in spelled.roots and spelled.aliases[0] == str(link)
    monkeypatch.setattr(pcanonical, "_SPELLINGS", spelled)
    value = {"t": "chr", "v": [f"{link}/a.csv", f"{os.path.realpath(real)}/b.csv"]}
    assert pcanonical.portable(value) == {"t": "chr", "v": ["<repo>/a.csv", "<repo>/b.csv"]}
    assert pcanonical.portable(f"{link}/a.csv") == "<repo>/a.csv"
    # a link's spelling is a whole path: not part of a longer name or of a URL
    for other in (f"{link}2/a.csv", f"https://example.org{link}/a.csv"):
        assert pcanonical.portable(other) == other
        assert pcanonical.portable({"t": "chr", "v": [other]}) == {"t": "chr", "v": [other]}
    assert pcanonical.portable({f"{link}/k": f"x\n{link}"}) == {"<repo>/k": "x\n<repo>"}


def test_the_checkout_is_repo_in_json_with_backslashes(monkeypatch) -> None:
    # a Windows path, as the OS writes it and with "/" as R writes it
    spelled = pcanonical.Spellings(("C:\\w\\repo", "C:/w/repo"), ("D:\\r",), ())
    monkeypatch.setattr(pcanonical, "_SPELLINGS", spelled)
    value = {"t": "chr", "v": ["C:\\w\\repo\\a.csv", "C:/w/repo/b.csv"]}
    want = {"t": "chr", "v": ["<repo>\\a.csv", "<repo>/b.csv"]}
    assert pcanonical.portable(value) == want
    # and as reached through a symlink
    value["v"].append("D:\\r\\c.csv")
    want["v"].append("<repo>\\c.csv")
    assert pcanonical.portable(value) == want


def test_case_paths_stay_inside_the_checkout(tmp_path, monkeypatch) -> None:
    root, outside = tmp_path / "root", tmp_path / "outside"
    (root / "fixtures").mkdir(parents=True)
    outside.mkdir()
    (root / "fixtures" / "a.txt").write_text("a")
    try:
        (root / "inside").symlink_to(root / "fixtures", target_is_directory=True)
        (root / "leak").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("no symlinks here")
    monkeypatch.setattr(pcases, "ROOT", root)
    assert case_path("fixtures/a.txt") == root / "fixtures" / "a.txt"
    # a symlink to somewhere else in the checkout keeps the spelling the case gave
    assert case_path("inside/a.txt") == root / "inside" / "a.txt"
    for bad in ("leak/a.txt", "../outside/a.txt", str(outside / "a.txt")):
        with pytest.raises(ValueError, match="only files inside the checkout"):
            case_path(bad)
    assert pcases.decode({"$file": "fixtures/a.txt"}) == (root / "fixtures" / "a.txt").as_posix()
    for key in ("$paper", "$file"):
        with pytest.raises(ValueError, match="inside the checkout"):
            pcases.decode({key: "leak/paper.json"})
    with pytest.raises(ValueError, match="inside the checkout"):
        pcases.decode({"$read": ["fixtures/a.txt", "leak/paper.json"]})


def _linked_checkout(tmp_path: Path) -> tuple[Path, Path]:
    """A checkout whose upstream/metacheck is a symlink to another checkout's."""
    root, shared = tmp_path / "root", tmp_path / "shared" / "metacheck"
    (shared / "tests").mkdir(parents=True)
    (shared / "tests" / "a.txt").write_text("a")
    (root / "upstream").mkdir(parents=True)
    try:
        (root / "upstream" / "metacheck").symlink_to(shared, target_is_directory=True)
    except OSError:
        pytest.skip("no symlinks here")
    return root, shared


def test_a_symlinked_submodule_is_repo_too(tmp_path, monkeypatch) -> None:
    root, shared = _linked_checkout(tmp_path)
    real = os.path.realpath(shared)
    assert pcanonical.linked_trees(root) == ((real, "upstream/metacheck"),)
    assert pcanonical.linked_trees(tmp_path / "shared") == ()  # nothing linked
    spelled = pcanonical.Spellings.of(root)
    assert dict(spelled.links) == dict.fromkeys(
        (real, Path(real).as_posix()), "<repo>/upstream/metacheck"
    )
    monkeypatch.setattr(pcanonical, "_SPELLINGS", spelled)
    value = {"t": "chr", "v": [f"{real}/tests/a.txt"]}
    assert pcanonical.portable(value) == {
        "t": "chr",
        "v": ["<repo>/upstream/metacheck/tests/a.txt"],
    }
    assert pcanonical.portable(f"{real}/tests/a.txt") == "<repo>/upstream/metacheck/tests/a.txt"
    assert pcanonical.portable("/elsewhere/a.txt") == "/elsewhere/a.txt"


def test_case_paths_may_go_through_a_symlinked_submodule(tmp_path, monkeypatch) -> None:
    root, _ = _linked_checkout(tmp_path)
    (root / "leak").symlink_to(tmp_path, target_is_directory=True)
    monkeypatch.setattr(pcases, "ROOT", root)
    assert case_path("upstream/metacheck/tests/a.txt") == root / "upstream/metacheck/tests/a.txt"
    with pytest.raises(ValueError, match="inside the checkout"):
        case_path("leak/other.txt")  # a link that is not the submodule


def test_cc_norm_writes_a_symlinked_submodule_under_root(tmp_path, monkeypatch) -> None:
    from dataclasses import dataclass, field

    import pandas as pd

    from tests.mod_code import helpers

    root, shared = _linked_checkout(tmp_path)
    monkeypatch.setattr(helpers, "ROOT", root)

    @dataclass(frozen=True)
    class Output:
        table: pd.DataFrame
        extras: dict = field(default_factory=dict)

    where = f"{os.path.realpath(shared)}/tests/a.txt"
    table = pd.DataFrame({"file_location": [where, f"{root}/b.txt"]})
    got = helpers.cc_norm(Output(table)).table["file_location"].tolist()
    assert got == ["<ROOT>/upstream/metacheck/tests/a.txt", "<ROOT>/b.txt"]


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
