"""``pack check``, ``pack_new`` scaffolds and ``module_template``."""

from __future__ import annotations

import json

import pytest

from pytacheck.cli import main
from pytacheck.module import module_run
from pytacheck.packs.check import CheckIssue, pack_check
from pytacheck.packs.scaffold import module_template, pack_new

VALIDATED = "<validation>Validated on 10 papers.</validation>"


def src(
    name: str,
    *lines: str,
    header: str = "",
    keywords: tuple[str, ...] = ("results",),
    requires: tuple[str, ...] = (),
    details: str = VALIDATED,
    title: str | None = None,
    func: str | None = None,
) -> str:
    """A module file whose function body is *lines* (default: a passing module)."""
    lines = lines or (
        "import pandas as pd",
        'ids = [p.paper_id for p in paper] if hasattr(paper, "names") else [paper.paper_id]',
        f'st = pd.DataFrame({{"paper_id": ids, "{name}": [1] * len(ids)}})',
        'return {"summary_table": st, "traffic_light": "green", "summary_text": "fine"}',
    )
    body = "\n".join(f"    {line}" for line in lines)
    return (
        f"from pytacheck.module import module\n{header}\n\n"
        f"@module(title={title if title is not None else name.title()!r}, "
        f"description='The {name} module', details={details!r}, "
        f"keywords={list(keywords)!r}, requires={list(requires)!r})\n"
        f"def {func or name}(paper):\n{body}\n"
    )


def good(name: str, **kwargs) -> str:
    return src(name, **kwargs)


def codes(issues: list[CheckIssue], level: str = "error") -> set[str]:
    return {i.code for i in issues if i.level == level}


def test_a_good_pack_passes(ms) -> None:
    folder = ms.pack(
        ms.root / "good",
        "good",
        {"fine": good("fine")},
        presets={"default": {"extends": ["metacheck::default"], "modules": ["fine"]}},
        license="MIT",
        title="Good",
        description="A good pack",
        files={"_helper.py": "X = 1\n", "tests/test_x.py": "def test(): pass\n"},
    )
    assert pack_check(folder) == []


def test_metadata_problems(ms) -> None:
    folder = ms.pack(
        ms.root / "bad",
        "bad",
        {
            "untitled": src("untitled", title=""),
            "misnamed": src("misnamed", func="other_name"),
            "nosection": src("nosection", keywords=("banana",)),
            "unvalidated": src("unvalidated", details=""),
            "weird_caps": good("weird_caps", requires=("gpu",)),
            "no_decorator": "def no_decorator(paper):\n    return {}\n",
            "broken": "def broken(:\n",
        },
    )
    issues = pack_check(folder)
    by_module = {(i.where, i.code) for i in issues if i.level == "error"}
    assert ("module untitled", "metadata") in by_module
    assert ("module misnamed", "name") in by_module
    assert ("module nosection", "section") in by_module
    assert ("module weird_caps", "requires") in by_module
    assert ("module no_decorator", "metadata") in by_module
    assert ("module broken", "syntax") in by_module
    assert ("module unvalidated", "validation") in {(i.where, i.code) for i in issues}
    assert "module unvalidated" not in {i.where for i in issues if i.level == "error"}


def test_run_problems(ms) -> None:
    mutates = src("mutates", 'paper.info["title"] = "changed"', "return {}")
    bad_light = src("bad_light", 'return {"traffic_light": "purple"}')
    bad_summary = src(
        "bad_summary", "import pandas as pd", 'return {"summary_table": pd.DataFrame({"x": [1]})}'
    )
    dup_summary = src(
        "dup_summary",
        "import pandas as pd",
        'pid = list(paper)[0].paper_id if hasattr(paper, "names") else paper.paper_id',
        'return {"summary_table": pd.DataFrame({"paper_id": [pid, pid]})}',
    )
    crashes = src("crashes", 'raise ValueError("boom")')
    folder = ms.pack(
        ms.root / "runs",
        "runs",
        {
            "mutates": mutates,
            "bad_light": bad_light,
            "bad_summary": bad_summary,
            "dup_summary": dup_summary,
            "crashes": crashes,
        },
    )
    issues = pack_check(folder)
    found = {(i.where, i.code) for i in issues if i.level == "error"}
    assert ("module mutates", "mutation") in found
    assert ("module bad_light", "traffic_light") in found
    assert ("module bad_summary", "summary_table") in found
    assert ("module dup_summary", "summary_table") in found
    assert ("module crashes", "run") in found
    assert any("boom" in i.message for i in issues if i.where == "module crashes")
    assert not codes(pack_check(folder, run=False)) & {"mutation", "run", "traffic_light"}


def test_network_use_must_be_declared(ms) -> None:
    undeclared = good("fetches", header="import httpx")
    declared = good("declared", header="from pytacheck import http", requires=("network",))
    folder = ms.pack(ms.root / "net", "net", {"fetches": undeclared, "declared": declared})
    issues = pack_check(folder)
    errors = {(i.where, i.code) for i in issues if i.level == "error"}
    assert ("module fetches", "network") in errors
    assert ("module declared", "network") not in errors
    skipped = [i for i in issues if i.where == "module declared" and i.code == "run"]
    assert skipped and skipped[0].level == "warning" and "requires network" in skipped[0].message


def test_presets_are_checked(ms) -> None:
    presets = {
        "ok": {"extends": ["metacheck::repository"], "modules": ["own"]},
        "unknown": {"modules": ["does_not_exist"]},
        "cycle": {"extends": ["cycle2"]},
        "cycle2": {"extends": ["cycle"]},
        "elsewhere": {"extends": ["otherpack::default"]},
    }
    folder = ms.pack(ms.root / "pp", "pp", {"own": good("own")}, presets=presets)
    issues = pack_check(folder)
    errors = {i.where for i in issues if i.level == "error" and i.code == "preset"}
    assert errors == {"preset unknown", "preset cycle", "preset cycle2"}
    warns = {i.where: i.message for i in issues if i.level == "warning" and i.code == "preset"}
    assert "qualify" in warns["preset unknown"]
    assert "cannot be checked here" in warns["preset elsewhere"]


def test_manifest_problems(ms) -> None:
    folder = ms.pack(ms.root / "rv", "rv", {"m": good("m")}, reviewed="2026-01-01")
    assert ("pack.json", "manifest") in {(i.where, i.code) for i in pack_check(folder)}
    empty = ms.root / "empty"
    empty.mkdir()
    assert codes(pack_check(empty)) == {"manifest"}
    assert codes(pack_check(ms.root / "missing")) == {"files"}
    link = ms.pack(ms.root / "linked", "linked", {"m": good("m")})
    (link / "evil.py").symlink_to("/etc/hostname")
    assert "files" in codes(pack_check(link))


def test_pack_new_scaffold_passes_pack_check(ms, paper) -> None:
    root = pack_new("my-field", ms.work)
    expected = {
        "pack.json",
        "my_field_example.py",
        "tests/test_my_field_example.py",
        "README.md",
        "LICENSE",
        "CITATION.cff",
        ".github/workflows/pytacheck.yml",
        ".gitignore",
    }
    assert {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()} == expected
    assert pack_check(root) == []
    manifest = json.loads((root / "pack.json").read_text())
    assert manifest["name"] == "my-field" and manifest["presets"]["only"]["modules"] == [
        "my_field_example"
    ]
    assert "<validation>" in (root / "my_field_example.py").read_text()
    assert "pytacheck pack check" in (root / ".github/workflows/pytacheck.yml").read_text()
    with pytest.raises(Exception, match="not empty"):
        pack_new("my-field", ms.work)
    ms.pin("my-field", {"path": str(root)})
    out = module_run(paper, "my-field::my_field_example")
    assert out.traffic_light == "info" and len(out.table) == 1


def test_module_template(ms, paper) -> None:
    path = module_template("my_check")
    assert path.resolve() == ms.work / "modules" / "my_check.py"
    text = path.read_text()
    assert "def my_check(paper" in text and "<validation>" in text and "module_name" not in text
    out = module_run(paper, "my_check")  # found in ./modules, like metacheck
    assert out.title == "Module Title" and out.traffic_light == "info"
    with pytest.raises(FileExistsError):
        module_template("my_check")
    module_template("my_check", overwrite=True)
    with pytest.raises(ValueError, match="only letters, numbers, and _"):
        module_template("my-check")
    with pytest.raises(ValueError, match="start with a letter"):
        module_template("1check")


def test_cli_pack_check_and_new(ms, capsys) -> None:
    assert main(["pack", "new", "clinic", "--path", str(ms.work)]) == 0
    assert "Created" in capsys.readouterr().out
    assert main(["pack", "check", str(ms.work / "clinic")]) == 0
    assert "0 error(s)" in capsys.readouterr().out
    bad = ms.pack(ms.root / "bad", "bad", {"m": src("m", keywords=("nope",))})
    assert main(["pack", "check", str(bad), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert {"level", "code", "where", "message"} <= set(payload["issues"][0])


@pytest.mark.parametrize(
    "built", ["0.5.0.dev1", "0.4.0.dev2", "0.4.0a1", "0.4.0rc1", "0.4.0", "0.4.2"]
)
def test_pack_new_requirement_is_met_by_the_build_that_wrote_it(
    ms, monkeypatch: pytest.MonkeyPatch, built: str
) -> None:
    from packaging.specifiers import SpecifierSet
    from packaging.version import Version

    from pytacheck.packs import scaffold

    monkeypatch.setattr(scaffold, "__version__", built)
    root = pack_new("dev-pack", ms.work)
    requires = json.loads((root / "pack.json").read_text())["requires"]["pytacheck"]
    assert SpecifierSet(requires).contains(Version(built), prereleases=True)
