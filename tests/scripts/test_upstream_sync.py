"""scripts/upstream_sync.py: follow metacheck dev, and a pinned pull request until it is merged.

Each test builds a small metacheck-like repository (a ``dev`` branch and GitHub's
``refs/pull/<n>/head`` refs) and a pytacheck-like tree whose submodule clones it,
so status/prepare run on real git history without the network.
"""

from __future__ import annotations

import functools
import importlib.util
import json
import shutil
import subprocess
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "upstream_sync.py"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

TOML = """\
# The metacheck commit pytacheck is verified against. Updated by the
# upstream-sync workflow together with the upstream/metacheck submodule.
[upstream]
repository = "https://github.com/scienceverse/metacheck"
branch = "dev"
commit = "{commit}"
# dev plus pull request #7 (a feature), which pytacheck targets ahead of its merge.
pull_request = 7
base_commit = "{base}"
version = "0.3.1"
"""

VERSION_PY = '''\
"""Version information."""

__version__ = "0.0.0"

#: The metacheck commit this release is verified against (see parity/UPSTREAM.toml).
UPSTREAM = {{
    "repository": "https://github.com/scienceverse/metacheck",
    "branch": "dev",
    "commit": "{commit}",
    "version": "0.3.1",
    # dev plus scienceverse/metacheck#7 (a feature), not yet merged
    "pull_request": 7,
    "base_commit": "{base}",
}}
'''


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=T",
            "-c",
            "user.email=t@example.org",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class Upstream:
    """metacheck's repository: dev, a feature branch and pull request #7."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.mkdir()
        git(path, "init", "-q", "-b", "dev")
        self.commit(
            {"DESCRIPTION": "Version: 0.3.1\n", "R/foo.R": "foo <- function() 1\n"}, "Start"
        )
        self.base = self.commit({"R/foo.R": "foo <- function() 2\n"}, "Change foo")
        git(path, "checkout", "-q", "-b", "feature")
        self.pr = self.commit({"R/bar.R": "bar <- function() 1\n"}, "Add bar")
        self.set_pr(self.pr)
        git(path, "checkout", "-q", "dev")

    def commit(self, files: dict[str, str], message: str) -> str:
        for name, text in files.items():
            (self.path / name).parent.mkdir(parents=True, exist_ok=True)
            (self.path / name).write_text(text, encoding="utf-8")
        git(self.path, "add", "-A")
        git(self.path, "commit", "-q", "-m", message)
        return git(self.path, "rev-parse", "HEAD")

    def on(self, branch: str) -> Upstream:
        git(self.path, "checkout", "-q", branch)
        return self

    def set_pr(self, sha: str) -> None:
        git(self.path, "update-ref", "refs/pull/7/head", sha)

    def head(self, ref: str = "dev") -> str:
        return git(self.path, "rev-parse", ref)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[ModuleType, Upstream, Path]:
    up = Upstream(tmp_path / "metacheck")
    root = tmp_path / "pytacheck"
    sub = root / "upstream" / "metacheck"
    sub.parent.mkdir(parents=True)
    git(root.parent, "clone", "-q", str(up.path), str(sub))
    git(sub, "checkout", "-q", up.pr)
    (root / "parity").mkdir()
    (root / "parity" / "UPSTREAM.toml").write_text(TOML.format(commit=up.pr, base=up.base))
    (root / "src" / "metacheck").mkdir(parents=True)
    (root / "src" / "metacheck" / "_version.py").write_text(
        VERSION_PY.format(commit=up.pr, base=up.base)
    )
    (root / "porting" / "map").mkdir(parents=True)
    (root / "porting" / "map" / "x.toml").write_text(
        '[files]\n"R/bar.R" = ["src/metacheck/bar.py"]\n'
    )
    (root / "porting" / "symbols.json").write_text(
        json.dumps({"bar": {"python": "metacheck.x:bar"}})
    )

    spec = importlib.util.spec_from_file_location("upstream_sync_under_test", SCRIPT)
    assert spec and spec.loader
    us = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(us)
    monkeypatch.setattr(us, "ROOT", root)
    monkeypatch.setattr(us, "SUB", sub)
    monkeypatch.setattr(us, "UPSTREAM_TOML", root / "parity" / "UPSTREAM.toml")
    monkeypatch.setattr(us, "VERSION_PY", root / "src" / "metacheck" / "_version.py")
    monkeypatch.setattr(us, "BRIEF_DIR", root / ".upstream-sync")
    return us, up, root


def status(us: ModuleType, capsys: pytest.CaptureFixture[str], *args: str) -> dict[str, Any]:
    assert us.main(["status", *args]) == 0
    return json.loads(capsys.readouterr().out)


def prepare(us: ModuleType, capsys: pytest.CaptureFixture[str], *args: str) -> dict[str, Any]:
    assert us.main(["prepare", *args]) == 0
    return json.loads(capsys.readouterr().out)


def pin_files(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    toml = tomllib.loads((root / "parity" / "UPSTREAM.toml").read_text())["upstream"]
    ns: dict[str, Any] = {}
    exec((root / "src" / "metacheck" / "_version.py").read_text(), ns)
    return toml, ns["UPSTREAM"]


def assert_pin(root: Path, commit: str, pull_request: int | None, base: str | None) -> None:
    toml, py = pin_files(root)
    for pin in (toml, py):
        assert pin["commit"] == commit
        assert pin["branch"] == "dev"
        assert pin.get("pull_request") == pull_request
        assert pin.get("base_commit") == base
    assert toml == py


# --- the pull request is open --------------------------------------------------------


def test_open_pull_request_unchanged_is_not_synced(world, capsys) -> None:
    us, up, _ = world
    out = status(us, capsys)
    assert out["mode"] == "pull_request"
    assert out["head"] == up.pr
    assert out["sync"] is False and out["behind"] == 0
    assert out["pr_merged"] is None and out["not_in_pr"] == 0
    capsys.readouterr()
    us.main(["prepare"])
    assert capsys.readouterr().out.startswith("already at")


def test_dev_commits_not_in_the_pull_request_are_reported(world, capsys) -> None:
    us, up, _ = world
    up.on("dev").commit({"R/foo.R": "foo <- function() 3\n"}, "Fix foo (#8)")
    out = status(us, capsys)
    # the pull request did not move: nothing to sync, but dev is ahead of it
    assert out["mode"] == "pull_request" and out["head"] == up.pr
    assert out["sync"] is False
    assert out["not_in_pr"] == 1
    assert out["branch_head"] == up.head()


def test_new_push_to_the_pull_request_is_synced(world, capsys) -> None:
    us, up, root = world
    fix = up.on("dev").commit({"R/foo.R": "foo <- function() 3\n"}, "Fix foo (#8)")
    new = up.on("feature").commit({"R/bar.R": "bar <- function() 2\nbaz <- function() 1\n"}, "baz")
    up.set_pr(new)
    out = status(us, capsys)
    assert out["mode"] == "pull_request"
    assert out["head"] == new and out["sync"] is True and out["behind"] == 1
    assert out["not_in_pr"] == 1

    res = prepare(us, capsys, "--to", out["head"])
    assert res["mode"] == "pull_request" and res["to"] == new and res["not_in_pr"] == 1
    assert git(us.SUB, "rev-parse", "HEAD") == new
    assert_pin(root, new, 7, up.base)
    brief = (root / ".upstream-sync" / "brief.md").read_text()
    assert "plus pull request #7, which is not merged yet" in brief
    assert "`dev` commits not in pull request #7" in brief
    assert f"- {fix[:7]} Fix foo (#8)" in brief
    assert "**added** `baz` in `R/bar.R`" in brief
    assert "changed `bar` (`R/bar.R`) -> `metacheck.x:bar`" in brief
    meta = json.loads((root / ".upstream-sync" / "meta.json").read_text())
    assert meta["title"] == f"Sync with metacheck dev + #7 {new[:10]} (0.3.1)"
    assert meta["pull_request"] == 7 and meta["base_commit"] == up.base
    assert meta["warning"].startswith("> [!WARNING]")
    assert "Fix foo (#8)" in meta["warning"]


def test_pull_request_rebased_onto_newer_dev_moves_the_base(world, capsys) -> None:
    us, up, root = world
    fix = up.on("dev").commit({"R/foo.R": "foo <- function() 3\n"}, "Fix foo (#8)")
    git(up.path, "checkout", "-q", "feature")
    git(up.path, "rebase", "-q", "dev")
    rebased = up.head("feature")
    up.set_pr(rebased)
    out = status(us, capsys)
    assert out["sync"] is True and out["head"] == rebased and out["not_in_pr"] == 0
    prepare(us, capsys)
    assert_pin(root, rebased, 7, fix)
    brief = (root / ".upstream-sync" / "brief.md").read_text()
    assert "not in pull request" not in brief


# --- the pull request is merged --------------------------------------------------------


@pytest.mark.parametrize("how", ["merge", "squash", "rebase"])
def test_merged_pull_request_goes_back_to_dev(world, capsys, how: str) -> None:
    us, up, root = world
    up.on("dev").commit({"R/foo.R": "foo <- function() 3\n"}, "Fix foo")
    if how == "merge":
        git(
            up.path,
            "merge",
            "-q",
            "--no-ff",
            "-m",
            "Merge pull request #7 from x/feature",
            "feature",
        )
    elif how == "squash":
        up.commit({"R/bar.R": "bar <- function() 1\n"}, "Add bar (#7)")
    else:
        git(up.path, "cherry-pick", up.pr)
    head = up.head()
    out = status(us, capsys)
    assert out["mode"] == "merged"
    assert out["pr_merged"].split()[0] == how
    assert out["head"] == head and out["sync"] is True

    res = prepare(us, capsys)
    assert res["mode"] == "merged" and res["to"] == head
    assert git(us.SUB, "rev-parse", "HEAD") == head
    assert_pin(root, head, None, None)
    text = (root / "parity" / "UPSTREAM.toml").read_text()
    assert "#7" not in text and "pull request" not in text
    assert "#7" not in (root / "src" / "metacheck" / "_version.py").read_text()
    brief = (root / ".upstream-sync" / "brief.md").read_text()
    assert "Pull request #7 was merged into `dev`" in brief
    meta = json.loads((root / ".upstream-sync" / "meta.json").read_text())
    assert meta["title"].endswith("(0.3.1; #7 merged)")
    assert meta["pull_request"] is None and meta["warning"] == ""

    # afterwards the pin tracks dev alone
    up.commit({"R/foo.R": "foo <- function() 4\n"}, "More")
    out = status(us, capsys)
    assert out["mode"] == "branch" and out["head"] == up.head() and out["behind"] == 1


def test_fast_forwarded_pull_request_only_drops_it_from_the_pin(world, capsys) -> None:
    us, up, root = world
    git(up.path, "merge", "-q", "--ff-only", "feature")
    out = status(us, capsys)
    assert out["mode"] == "merged" and out["head"] == up.pr
    assert out["behind"] == 0 and out["sync"] is True
    prepare(us, capsys)
    assert_pin(root, up.pr, None, None)


# --- no pull request --------------------------------------------------------------------


def test_drop_pr_syncs_to_dev(world, capsys) -> None:
    us, up, root = world
    out = status(us, capsys, "--drop-pr")
    assert out["mode"] == "branch" and out["head"] == up.base and out["sync"] is True
    prepare(us, capsys, "--drop-pr")
    assert_pin(root, up.base, None, None)


def test_plain_dev_pin(world, capsys) -> None:
    us, up, root = world
    us.write_pin(up.base, "0.3.1", "dev")
    git(us.SUB, "checkout", "-q", up.base)
    assert status(us, capsys)["sync"] is False
    new = up.on("dev").commit({"R/foo.R": "foo <- function() 3\n"}, "Fix foo")
    out = status(us, capsys)
    assert out == {**out, "mode": "branch", "head": new, "behind": 1, "sync": True}
    assert out["pull_request"] is None
    prepare(us, capsys)
    assert_pin(root, new, None, None)


# --- the pin files ----------------------------------------------------------------------


def test_write_pin_adds_updates_and_drops_the_pull_request(world) -> None:
    us, _, root = world
    us.write_pin("a" * 40, "0.3.2", "dev", 7, "b" * 40)
    assert_pin(root, "a" * 40, 7, "b" * 40)
    # the same pull request keeps its descriptive comment
    assert "(a feature)" in (root / "parity" / "UPSTREAM.toml").read_text()
    us.write_pin("c" * 40, "0.3.2", "dev")
    assert_pin(root, "c" * 40, None, None)
    us.write_pin("d" * 40, "0.3.3", "dev", 9, "e" * 40)
    assert_pin(root, "d" * 40, 9, "e" * 40)
    toml = (root / "parity" / "UPSTREAM.toml").read_text()
    assert "# dev plus pull request #9" in toml and "(a feature)" not in toml
    assert pin_files(root)[0]["version"] == "0.3.3"
    with pytest.raises(ValueError, match="go together"):
        us.write_pin("d" * 40, "0.3.3", "dev", 9, None)


def test_repository_pin_files_agree() -> None:
    """parity/UPSTREAM.toml and metacheck._version.UPSTREAM name the same upstream."""
    toml = tomllib.loads((REPO / "parity" / "UPSTREAM.toml").read_text())["upstream"]
    ns: dict[str, Any] = {}
    exec((REPO / "src" / "metacheck" / "_version.py").read_text(), ns)
    assert toml == ns["UPSTREAM"]
    assert ("pull_request" in toml) == ("base_commit" in toml)


def test_write_pin_is_stable_on_the_repository_pin(tmp_path, monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location("upstream_sync_pin", SCRIPT)
    assert spec and spec.loader
    us = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(us)
    for name, src in (
        ("UPSTREAM_TOML", REPO / "parity" / "UPSTREAM.toml"),
        ("VERSION_PY", REPO / "src" / "metacheck" / "_version.py"),
    ):
        dst = tmp_path / src.name
        dst.write_text(src.read_text())
        monkeypatch.setattr(us, name, dst)
    pin = us.pinned()
    us.write_pin(
        pin["commit"],
        pin["version"],
        pin["branch"],
        pin.get("pull_request"),
        pin.get("base_commit"),
    )
    assert (tmp_path / "UPSTREAM.toml").read_text() == (
        REPO / "parity" / "UPSTREAM.toml"
    ).read_text()
    assert (tmp_path / "_version.py").read_text() == (
        REPO / "src" / "metacheck" / "_version.py"
    ).read_text()


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("upstream_sync_brief_under_test", SCRIPT)
    assert spec and spec.loader
    us = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(us)
    return us


def _accuracy(**over: Any) -> dict[str, Any]:
    report = {
        "passed": True,
        "outputs": 3,
        "differences": [
            {
                "module": "m",
                "input": "a.xml",
                "field": "run",
                "level": "values",
                "detail": "only R fails",
                "explained_by": None,
            },
            {
                "module": "m",
                "input": "b.xml",
                "field": "report",
                "level": "wording",
                "detail": "R='likley'",
                "explained_by": 0,
            },
        ],
        "stale": [],
        "warnings": [],
    }
    return {**report, **over}


def test_brief_lists_changed_goldens_xpasses_and_unexplained_accuracy() -> None:
    us = _load_script()
    check = [
        {
            "case": "a/x",
            "tier": 1,
            "status": "r_changed",
            "failing": True,
            "kind": "r_bug_fixed",
            "ref": "U1",
            "problems": ["R's golden changed"],
        },
        {
            "case": "a/y",
            "tier": 2,
            "status": "xpass",
            "failing": True,
            "kind": "c_quirk",
            "ref": None,
            "problems": ["matches R"],
        },
        {
            "case": "a/z",
            "tier": 1,
            "status": "fail",
            "failing": True,
            "kind": None,
            "ref": None,
            "problems": ["x | y"],
        },
        {"case": "a/w", "tier": 1, "status": "pass", "failing": False, "problems": []},
    ]
    text = "\n".join(us.parity_brief(check, _accuracy()))
    assert "4 cases: 1 fail, 1 pass, 1 r_changed, 1 xpass" in text
    assert "golden changed" in text and "`a/x` | 1 | r_bug_fixed U1" in text
    assert "now match R" in text and "`a/y`" in text
    assert "Failing cases (1)" in text and "x \\| y" in text
    assert "1 not explained" in text and "only R fails" in text and "likley" not in text


def test_review_needs_a_human_for_tier1_marks_and_accuracy_warnings(monkeypatch) -> None:
    us = _load_script()
    monkeypatch.setattr(us, "tier1_marks", lambda: {"a/x": {"kind": "r_bug_fixed", "ref": "U1"}})
    monkeypatch.setattr(us, "git", lambda *args, **kw: "")
    same = {"a/x": {"kind": "r_bug_fixed", "ref": "U1"}}
    assert us.review_reasons(same, _accuracy()) == []
    reasons = us.review_reasons({}, _accuracy(warnings=["m: below 90%"], passed=False))
    assert reasons[0].startswith("1 tier-1 (realistic) marks")
    assert "the accuracy gate fails" in reasons
    assert "accuracy: m: below 90%" in reasons
    assert us.review_reasons(same, None) == ["the accuracy report did not run"]

    def git(*args: str, **kw: Any) -> str:
        if args[:2] == ("diff", "--name-only"):
            return f"{args[-1]}\n"
        return "+| D30 | new deliberate difference |\n"

    monkeypatch.setattr(us, "git", git)
    assert us.review_reasons(same, _accuracy()) == [
        "parity/accuracy/expected.yaml changed",
        "parity/accuracy/matrix.toml changed",
        "parity/corpus.toml changed",
        "D-entries of docs/UPSTREAM_ISSUES.md changed (1 lines)",
    ]


def test_review_needs_a_human_when_a_case_leaves_tier1(monkeypatch) -> None:
    """A case moved to tier 2 (or deleted) may then carry any mark: a human looks.
    A new unmarked tier-1 case needs no review."""
    us = _load_script()
    monkeypatch.setattr(us, "git", lambda *args, **kw: "")
    monkeypatch.setattr(us, "tier1_marks", lambda: {"a/y": None, "a/new": None})
    (reason,) = us.review_reasons({"a/x": None, "a/y": None}, _accuracy())
    assert "no longer tier 1" in reason and reason.endswith(": a/x")


def test_review_compares_files_with_the_base_commit(tmp_path, monkeypatch) -> None:
    """A change the porting agent committed still needs review: files are compared
    with the commit the sync branched from, not with HEAD."""
    us = _load_script()
    run = functools.partial(subprocess.run, cwd=tmp_path, check=True, capture_output=True)
    run(["git", "init", "-q"])
    run(["git", "config", "user.email", "t@example.org"])
    run(["git", "config", "user.name", "t"])
    expected = tmp_path / "parity" / "accuracy" / "expected.yaml"
    issues = tmp_path / "docs" / "UPSTREAM_ISSUES.md"
    expected.parent.mkdir(parents=True)
    issues.parent.mkdir()
    expected.write_text("differences: []\n")
    (expected.parent / "matrix.toml").write_text("")
    issues.write_text("| D1 | a | b | c |\n")
    run(["git", "add", "-A"])
    run(["git", "commit", "-q", "-m", "base"])
    base = run(["git", "rev-parse", "HEAD"], text=True).stdout.strip()
    expected.write_text("differences: [{module: m}]\n")
    issues.write_text("| D1 | a | b | c |\n| D2 | d | e | f |\n")
    run(["git", "commit", "-q", "-am", "the agent commits"])
    monkeypatch.setattr(us, "ROOT", tmp_path)
    monkeypatch.setattr(us, "tier1_marks", dict)
    assert us.review_reasons({}, _accuracy()) == []  # against HEAD: nothing
    assert us.review_reasons({}, _accuracy(), base) == [
        "parity/accuracy/expected.yaml changed",
        "D-entries of docs/UPSTREAM_ISSUES.md changed (1 lines)",
    ]
