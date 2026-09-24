"""Regressions from the module system v2 review: one test (or a few) per fixed finding.

No network: respx serves GitHub, config and data dirs are per test.
"""

from __future__ import annotations

import copy
import json
import os
import py_compile
import re
import stat
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest
import respx

import pytacheck as pc
from pytacheck.cli import main
from pytacheck.config import ConfigError, config_files, load_config
from pytacheck.module import ModuleError, module, module_find, module_run, run_session
from pytacheck.packs.build import store_build
from pytacheck.packs.install import pack_install
from pytacheck.packs.manifest import PackError
from pytacheck.packs.registry import refresh
from pytacheck.packs.stores import validate_index
from pytacheck.presets import select
from pytacheck.provenance import json_safe, module_provenance, rerun, run_modules
from tests.modsys.helpers import mod_src
from tests.modsys.storekit import (
    INDEX_URL,
    REV_C,
    STORE_REPO,
    STORE_URL,
    FakeStore,
    codeload,
    dir_files,
    entry_for,
    tarball,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def store(ms, monkeypatch):
    monkeypatch.setenv("PYTACHECK_STORE_URL", STORE_URL)
    with respx.mock(assert_all_called=False) as router:
        yield FakeStore(router, ms)


@pytest.fixture
def demo_json() -> str:
    return str(pc.demofile("json"))


@pytest.fixture
def scoped(ms, tmp_path, monkeypatch):
    """Separate user and project configs (``ms`` names one file for every scope)."""
    user = tmp_path / "userconf" / "config.json"
    user.parent.mkdir()
    monkeypatch.setattr("pytacheck.config.user_config_path", lambda: user)
    monkeypatch.delenv("PYTACHECK_CONFIG")
    project = ms.work / "proj"
    project.mkdir()
    monkeypatch.chdir(project)
    refresh()
    return user, project


def _lab(ms, **mods: str) -> Path:
    folder = ms.pack(ms.root / "lab", "lab", mods)
    ms.pin("lab", {"path": str(folder)})
    return folder


# --- cli run: failures (exit status, error text, -a typos) -------------------------------


def test_run_exits_1_says_why_and_runs_the_rest(ms, capsys, demo_json) -> None:
    _lab(
        ms,
        first=mod_src("first", "one"),
        failing=mod_src("failing", body='raise ValueError("boom")', args=""),
        foo=mod_src("foo", "two"),
    )
    argv = ["run", demo_json, "-m", "first", "-m", "failing", "-m", "foo"]
    capsys.readouterr()
    assert main(argv) == 1
    cap = capsys.readouterr()
    assert "boom" in cap.out and "two" in cap.out  # the error is shown and foo still ran
    assert "1 module(s) failed to run" in cap.err and "failing" in cap.err
    assert main([*argv, "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert [o["module"] for o in out] == ["first", "failing", "foo"]
    assert "boom" in out[1]["error"] and "error" not in out[0] and "error" not in out[2]


def test_unported_metacheck_modules_say_so(ms, capsys, demo_json) -> None:
    from pytacheck.module import _builtin_names
    from pytacheck.presets import _declared_builtin

    unported = sorted(set(_declared_builtin()) - set(_builtin_names()))
    if not unported:
        pytest.skip("every metacheck module is ported")
    capsys.readouterr()
    assert main(["run", demo_json, "-m", "marginal", "-m", unported[0], "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out[1]["error"] == f"metacheck's '{unported[0]}' is not ported to pytacheck yet"


def test_per_module_arg_typos_are_errors(ms, demo_json) -> None:
    with pytest.raises(SystemExit, match="'margnal' is not among the selected modules"):
        main(["run", demo_json, "-m", "marginal", "-a", "margnal.foo=1"])
    with pytest.raises(SystemExit, match="has no argument 'foo'"):
        main(["run", demo_json, "-m", "marginal", "-a", "marginal.foo=1"])


def test_missing_paper_file_is_one_line(ms, capsys) -> None:
    assert main(["run", "nosuch.json", "-m", "marginal"]) == 1
    err = capsys.readouterr().err
    assert "Cannot read nosuch.json" in err and "Traceback" not in err


def test_pack_new_hint_is_a_path(ms, capsys) -> None:
    assert main(["pack", "new", "my-pack"]) == 0
    assert "pytacheck pack install ./my-pack" in capsys.readouterr().out


# --- run sessions: memo keys and copies ---------------------------------------------------


def test_memo_tells_apart_functions_sharing_a_name_and_file(paper) -> None:
    def make(t: float):
        @module(name="thresh", title=f"thresh {t}")
        def thresh(paper):
            return {"summary_text": f"threshold={t}"}

        return thresh

    spec = module_find("marginal")
    other = replace(spec, func=lambda paper: {"summary_text": "other"})
    with run_session():
        a = module_run(paper, make(0.05))
        b = module_run(paper, make(0.01))
        c = module_run(paper, spec)
        d = module_run(paper, other)
    assert (a.summary_text, b.summary_text, b.title) == (
        "threshold=0.05",
        "threshold=0.01",
        "thresh 0.01",
    )
    assert c.summary_text != "other" and d.summary_text == "other"


@module(title="Counts", description="What the paper looks like now")
def _state(paper):
    from pytacheck.llm.core import llm_use

    return {
        "summary_text": f"n={len(paper.text)} flag={paper.extra.get('flag')} llm={llm_use()}",
        "extras_list": [1],
    }


def test_memo_sees_replaced_tables_extra_and_llm_options() -> None:
    from pytacheck.llm.core import llm_use

    p = pc.test_paper([f"Sentence {i}." for i in range(10)])
    was = llm_use()
    try:
        with run_session() as session:
            assert module_run(p, _state).summary_text == f"n=10 flag=None llm={was}"
            assert module_run(p, _state).summary_text == f"n=10 flag=None llm={was}"
            assert session.hits == 1
            q = copy.copy(p)  # shares the table dict with p
            q["text"] = p.text.head(3)
            assert module_run(p, _state).summary_text == f"n=3 flag=None llm={was}"
            p.extra["flag"] = True
            assert module_run(p, _state).summary_text == f"n=3 flag=True llm={was}"
            llm_use(not was)
            assert module_run(p, _state).summary_text == f"n=3 flag=True llm={not was}"
    finally:
        llm_use(was)


def test_memo_hits_do_not_share_containers(paper) -> None:
    with run_session():
        o1 = module_run(paper, _state)
        o1.extras["extras_list"].append(99)
        o2 = module_run(paper, _state)
        o2.extras["extras_list"].append(7)
        o3 = module_run(paper, _state)
    assert o3.extras["extras_list"] == [1]


# --- resolution ---------------------------------------------------------------------------


def test_file_paths_containing_double_colons_still_load(ms) -> None:
    (ms.work / "a::b.py").write_text(mod_src("colon"))
    assert module_find("a::b.py").title == "Colon"
    folder = ms.work / "proj::v2"
    folder.mkdir()
    (folder / "mod.py").write_text(mod_src("inner"))
    assert module_find(str(folder / "mod.py")).title == "Inner"
    assert module_find("proj::v2/mod.py").title == "Inner"
    with pytest.raises(ModuleError, match="no active pack named 'nopack'"):
        module_find("nopack::thing")


def test_a_malformed_config_does_not_change_the_not_found_error(ms) -> None:
    ms.config_file.write_text("{not json")
    with pytest.raises(ConfigError):
        load_config()
    assert module_find("marginal").name == "marginal"
    with pytest.raises(ModuleError, match="no modules that matched not_a_module") as info:
        module_find("not_a_module")
    assert "not valid JSON" in str(info.value)
    with pytest.raises(ModuleError, match="Cannot resolve lab::x"):
        module_find("lab::x")


# --- provenance ---------------------------------------------------------------------------


def test_a_module_element_called_provenance_wins(paper) -> None:
    @module(title="P")
    def provmod(paper):
        return {"summary_text": "x", "provenance": {"source": "osf.io/abc"}}

    out = module_run(paper, provmod)
    assert out.provenance == {"source": "osf.io/abc"} == out["provenance"]
    assert out.run_provenance["name"] == "provmod"
    keys = out.keys()  # the elements (ModuleOutput has no __contains__)
    assert "provenance" in keys and "run_provenance" not in keys
    plain = module_run(paper, "marginal")
    assert plain.provenance is plain.run_provenance and plain.provenance["pack"] == "metacheck"


def test_provenance_summarises_large_arguments(paper) -> None:
    df = pd.DataFrame({"x": range(200_000)})
    assert json_safe(df) == "<DataFrame 200000x1>"
    assert json_safe(paper) == f"<Paper {paper.paper_id}>"
    assert json_safe({"a": [1, 2.5, None]}) == {"a": [1, 2.5, None]}
    assert len(json_safe("y" * 5000)) == 5000  # strings are JSON, kept whole
    assert len(json_safe(type("Big", (), {"__repr__": lambda s: "z" * 5000})())) == 1000
    prov = module_provenance(module_find("marginal"), {"data": df})
    assert prov["args"] == {"data": "<DataFrame 200000x1>"}


def test_parity_cli_removes_its_data_dir(monkeypatch) -> None:
    from parity import __main__ as parity_cli

    monkeypatch.setenv("PYTACHECK_CONFIG", "none")
    monkeypatch.delenv("PYTACHECK_DATA_DIR", raising=False)
    monkeypatch.setattr(parity_cli, "_DATA_DIR", None)
    parity_cli._hermetic_env()
    folder = Path(os.environ["PYTACHECK_DATA_DIR"])
    assert folder.is_dir() and folder.name.startswith("pytacheck-parity-data-")
    parity_cli._DATA_DIR.cleanup()  # what atexit runs
    assert not folder.exists()


# --- installing: bytecode, native code and path sources ----------------------------------


def _pyc(ms, code: str) -> bytes:
    src = ms.root / "pyc_src" / "_helper.py"
    src.parent.mkdir(exist_ok=True)
    src.write_text(code)
    out = py_compile.compile(str(src), cfile=str(src.with_suffix(".pyc")), doraise=True)
    return Path(out).read_bytes()


def test_bytecode_in_a_pack_tarball_is_never_installed_or_imported(store, ms, paper) -> None:
    marker = ms.root / "marker"
    pyc = _pyc(ms, f"open({str(marker)!r}, 'w').write('ran')\n")
    src = ms.pack(
        ms.root / "src" / "evilpack",
        "evilpack",
        {"m": mod_src("m", header="from . import _helper")},
    )
    rev = "e" * 40
    files = dir_files(src, "packs/evilpack/")
    files["packs/evilpack/_helper.pyc"] = pyc
    files["packs/evilpack/__pycache__/_helper.cpython-312.pyc"] = pyc
    files["packs/evilpack/.git/hooks/post-checkout"] = "#!/bin/sh\n"
    store.router.get(codeload(STORE_REPO, rev)).respond(content=tarball(files))
    source = {"github": STORE_REPO, "rev": rev, "subdir": "packs/evilpack"}
    store.entries["evilpack"] = entry_for(src, source, reviewed="2026-09-01")
    store.publish()
    pack = pack_install("evilpack", yes=True)  # the tree hash still matches: .pyc is not hashed
    on_disk = sorted(p.relative_to(pack.root).as_posix() for p in pack.root.rglob("*"))
    assert on_disk == [".pytacheck-install.json", "m.py", "pack.json"]
    with pytest.raises(ModuleError, match="_helper"):
        module_run(paper, "evilpack::m")
    assert not marker.exists()


def test_native_extension_modules_are_refused(store, ms) -> None:
    so = "_fast.cpython-312-x86_64-linux-gnu.so"
    src = ms.pack(ms.root / "src" / "nat", "nat", {"m": mod_src("m")}, files={so: "\x7fELF"})
    files = dir_files(src, "packs/nat/")  # the index's tree hash includes it
    store.router.get(codeload(STORE_REPO, REV_C)).respond(content=tarball(files))
    source = {"github": STORE_REPO, "rev": REV_C, "subdir": "packs/nat"}
    store.entries["nat"] = entry_for(src, source)
    store.publish()
    with pytest.raises(PackError, match="compiled extension module"):
        pack_install("nat", yes=True)
    assert not (ms.data / "packs" / "nat").exists()


def test_pack_code_never_imports_bytecode_or_namespace_packages(ms, paper) -> None:
    marker = ms.root / "marker"
    folder = ms.pack(
        ms.root / "dev",
        "dev",
        {
            "m": mod_src("m", header="from . import _helper"),
            "n": mod_src("n", header="from . import sub"),
        },
    )
    (folder / "_helper.pyc").write_bytes(_pyc(ms, f"open({str(marker)!r}, 'w').write('ran')\n"))
    (folder / "sub").mkdir()
    (folder / "sub" / "x.txt").write_text("a namespace package, not source")
    ms.pin("dev", {"path": str(folder)})
    for ref, name in (("dev::m", "_helper"), ("dev::n", "sub")):
        with pytest.raises(ModuleError, match=f"cannot import name '{name}'"):
            module_run(paper, ref)
    assert not marker.exists()


def test_a_remote_index_cannot_list_local_folders(store, ms, tmp_path) -> None:
    evil = ms.pack(tmp_path / "shared" / "evil", "abs", {"m": mod_src("m")})
    rel = ms.pack(tmp_path / "shared" / "packs" / "rel", "rel", {"m": mod_src("m")})
    index = {
        "schema": 1,
        "name": "pytacheck",
        "_location": str(tmp_path / "shared"),
        "packs": [
            entry_for(evil, {"path": str(evil)}, reviewed="2026-09-01"),
            {**entry_for(rel, {"path": "packs/rel"}), "_location": str(tmp_path / "shared")},
        ],
    }
    store.router.get(INDEX_URL, name="index").respond(json=index)
    cleaned = validate_index(index, "test")
    assert "_location" not in cleaned and "_location" not in cleaned["packs"][1]
    for name in ("abs", "rel"):
        with pytest.raises(PackError, match="local-folder source"):
            pack_install(name, yes=True)
    assert not (ms.data / "packs").exists()


def test_a_local_store_cannot_point_outside_its_folder(ms, tmp_path, monkeypatch) -> None:
    root = tmp_path / "localstore"
    ms.pack(root / "packs" / "inside", "inside", {"m": mod_src("m")})
    outside = ms.pack(tmp_path / "elsewhere", "outside", {"m": mod_src("m")})
    index, _ = store_build(root)
    index["packs"].append(entry_for(outside, {"path": str(outside)}))
    index["packs"].append({**entry_for(outside, {"path": "../elsewhere"}), "name": "dotdot"})
    (root / "index.json").write_text(json.dumps(index))
    monkeypatch.setenv("PYTACHECK_STORE_URL", str(root))
    assert pack_install("inside", yes=True).trust == "store"
    for name in ("outside", "dotdot"):
        with pytest.raises(PackError, match="outside the store's folder"):
            pack_install(name, yes=True)


# --- store build: reviews are tied to the files that were read ---------------------------


def test_a_review_holds_only_for_the_tree_it_was_given_for(ms) -> None:
    root = ms.root / "store"
    ms.pack(root / "packs" / "psych", "psych", {"m": mod_src("m")})
    index, _ = store_build(root)
    tree = index["packs"][0]["tree_sha256"]
    entry = root / "packs" / "psych.json"
    entry.write_text(json.dumps({"reviewed": "2026-09-01", "reviewed_tree_sha256": tree}))
    index, issues = store_build(root)
    assert index["packs"][0]["reviewed"] == "2026-09-01" and not _review_issues(issues)
    (root / "packs" / "psych" / "m.py").write_text(mod_src("m", "changed"))
    index, issues = store_build(root)
    assert index["packs"][0]["reviewed"] is None
    assert json.loads((root / "index.json").read_text())["packs"][0]["reviewed"] is None
    (issue,) = _review_issues(issues)
    assert issue.level == "warning" and "changed since it was reviewed" in issue.message
    _, issues = store_build(root, check=True)
    assert [i.level for i in _review_issues(issues)] == ["error"]  # CI fails until re-reviewed
    assert main(["store", "build", str(root), "--check"]) == 1
    # a date that does not say which files it was for is not a review either
    entry.write_text(json.dumps({"reviewed": "2026-09-02"}))
    index, issues = store_build(root)
    assert index["packs"][0]["reviewed"] is None
    assert "reviewed_tree_sha256" in _review_issues(issues)[0].message


def _review_issues(issues):
    return [i for i in issues if "review" in i.message]


def test_pack_check_of_an_external_entry_fetches_and_checks_it(store, ms, capsys) -> None:
    src = ms.pack(
        ms.root / "src" / "ext",
        "ext",
        {"m": mod_src("m", header="import httpx")},  # undeclared network use
    )
    store.router.get(codeload("someone/ext", REV_C)).respond(
        content=tarball(dir_files(src), top="ext-top")
    )
    entry = ms.root / "packs" / "ext.json"
    entry.parent.mkdir()
    entry.write_text(json.dumps({"name": "ext", "source": {"github": "someone/ext", "rev": REV_C}}))
    capsys.readouterr()
    main(["pack", "check", str(entry)])
    out = capsys.readouterr().out
    assert "network" in out


# --- project config: trust ----------------------------------------------------------------


def _evil_project(base: Path, marker: Path) -> Path:
    shared = base / "shared"
    evil = shared / "evil"
    evil.mkdir(parents=True)
    (evil / "pack.json").write_text(
        json.dumps({"schema": 1, "name": "evil", "presets": {"default": {"modules": ["grab"]}}})
    )
    (evil / "grab.py").write_text(
        f"open({str(marker)!r}, 'w').write('ran')\n" + mod_src("grab").split("\n", 1)[1]
    )
    (shared / "pytacheck.json").write_text(
        json.dumps({"preset": "evil::default", "packs": {"evil": {"path": "./evil"}}})
    )
    victim = shared / "victim" / "project"
    victim.mkdir(parents=True)
    return victim


def test_a_project_config_path_pack_waits_for_trust(scoped, ms, monkeypatch, capsys) -> None:
    marker = ms.root / "marker"
    victim = _evil_project(ms.root / "s", marker)
    monkeypatch.chdir(victim)
    refresh()
    cfg = load_config()
    assert "evil" not in cfg.packs and "packs.evil" in cfg.untrusted
    paper_file = str(pc.demofile("json"))
    assert main(["run", paper_file]) == 1
    assert "evil" in capsys.readouterr().err
    assert main(["modules", "--all"]) == 0
    assert not marker.exists()
    # `pack install` shows the code and asks; agreeing trusts it
    assert main(["pack", "install", "--yes"]) == 0
    assert "evil" in load_config().packs
    main(["run", paper_file, "-m", "evil::grab"])
    assert marker.exists()


def test_writable_or_foreign_project_configs_are_ignored(scoped, ms, monkeypatch) -> None:
    victim = _evil_project(ms.root / "s", ms.root / "marker")
    shared = victim.parents[1]
    monkeypatch.chdir(victim)
    assert [s for s, _ in config_files()] == ["project"]
    shared.chmod(0o777)
    try:
        with pytest.warns(UserWarning, match="writable by everyone"):
            assert config_files() == []
        shared.chmod(0o1777)  # sticky, like /tmp: others cannot replace the file
        assert [s for s, _ in config_files()] == ["project"]
    finally:
        shared.chmod(0o755)
    if os.geteuid() == 0:
        os.chown(shared / "pytacheck.json", 12345, -1)
        with pytest.warns(UserWarning, match="owned by another user"):
            assert config_files() == []
        os.chown(shared / "pytacheck.json", 0, -1)
    monkeypatch.setenv("HOME", str(victim.parent))  # the search stops at the home folder
    assert config_files() == []


# --- init --project and pack install --project write a working lock file ----------------


def test_init_project_pins_packs_already_installed_for_the_user(
    store, scoped, ms, monkeypatch, tmp_path
) -> None:
    user, project = scoped
    store.add("fields", presets={"psychology": {"modules": ["marginal"]}})
    assert main(["init", "--preset", "fields::psychology", "--yes"]) == 0
    assert "fields" in json.loads(user.read_text())["packs"]
    assert main(["init", "--preset", "fields::psychology", "--project", "--yes"]) == 0
    lock = json.loads((project / "pytacheck.json").read_text())
    assert lock["preset"] == "fields::psychology"
    assert lock["packs"]["fields"] == json.loads(user.read_text())["packs"]["fields"]
    # a teammate: no user config, an empty data dir, the same project folder
    user.unlink()
    monkeypatch.setenv("PYTACHECK_DATA_DIR", str(tmp_path / "teammate"))
    refresh()
    assert main(["pack", "install", "--yes"]) == 0
    assert "fields" in pc.pack_list()["name"].tolist()
    assert [m for m, _ in select(use_config=True)] == ["marginal"]


def test_project_path_packs_are_pinned_relative(scoped, ms) -> None:
    _user, project = scoped
    author = ms.pack(project.parent / "author" / "docpack", "docpack", {"m": mod_src("m")})
    assert main(["pack", "install", "../author/docpack", "--project", "--yes"]) == 0
    lock = json.loads((project / "pytacheck.json").read_text())
    assert lock["packs"]["docpack"] == {"path": "../author/docpack"}
    assert load_config().packs["docpack"]["path"] == str(author.resolve())


# --- rerun ----------------------------------------------------------------------------------


def test_rerun_replays_modules_that_never_resolved(ms, paper, tmp_path, capsys, demo_json) -> None:
    with pytest.warns(UserWarning, match="Error in not_a_module"):
        chain = run_modules(paper, ["marginal", "not_a_module"], record=tmp_path / "r.json")
    assert chain.run_record.modules[1]["status"] == "fail"
    with pytest.warns(UserWarning) as caught:
        again = rerun(tmp_path / "r.json", paper)
    messages = [str(w.message) for w in caught]
    assert any("still cannot be run" in m for m in messages)
    assert "Error in not_a_module" in messages
    assert [o.module for o in again] == ["marginal", "not_a_module"]
    assert again[1].traffic_light == "fail" and again[0].traffic_light != "fail"
    # the CLI round trip documented in MODULES.md, with a metacheck module not ported yet
    from pytacheck.module import _builtin_names
    from pytacheck.presets import _declared_builtin

    unported = sorted(set(_declared_builtin()) - set(_builtin_names()))
    if not unported:
        return
    record = tmp_path / "cli.json"
    argv = ["run", demo_json, "-m", "marginal", "-m", unported[0], "--record", str(record)]
    assert main(argv) == 1 and record.is_file()
    capsys.readouterr()
    assert main(["rerun", str(record), demo_json, "--json"]) == 1  # it failed again
    out = json.loads(capsys.readouterr().out)
    assert [o["module"] for o in out] == ["marginal", unported[0]]
    assert "not ported" in out[1]["error"]


def test_rerun_checks_files_before_importing_them(store, ms, paper, tmp_path) -> None:
    marker = tmp_path / "marker"
    store.add("psy", {"m": mod_src("m")})
    pack = pack_install("psy", yes=True)
    record = run_modules(paper, ["psy::m"]).run_record
    target = pack.root / "m.py"
    target.chmod(stat.S_IWUSR | stat.S_IRUSR)
    target.write_text(f"open({str(marker)!r}, 'w').write('ran')\n" + target.read_text())
    refresh()  # a new process would import the file afresh
    with pytest.raises(ModuleError, match="was modified"):
        rerun(record, paper)
    assert not marker.exists()
    # a crafted record naming an arbitrary file, with a wrong hash
    outside = tmp_path / "elsewhere" / "x.py"
    outside.parent.mkdir()
    outside.write_text(f"open({str(marker)!r}, 'w').write('ran')\n" + mod_src("x"))
    crafted = record.to_dict()
    crafted["modules"] = [
        {"id": "x", "name": "x", "pack": None, "source": {"path": str(outside)},
         "sha256": "0" * 64, "status": "ok", "args": {}},
    ]  # fmt: skip
    with pytest.raises(ModuleError, match=r"Not running .*x\.py from the run record"):
        rerun(crafted, paper)  # no terminal: nobody agreed to run it
    with pytest.raises(ModuleError, match="differs from the recorded one"):
        rerun(crafted, paper, yes=True)
    assert not marker.exists()


# --- presets, docs and the contrib store --------------------------------------------------


def test_as_r_leaves_out_modules_r_cannot_run(ms) -> None:
    from pytacheck.presets import preset_as_r

    _lab(ms, py_only=mod_src("py_only"))
    text = preset_as_r([("marginal", {}), ("lab::py_only", {"x": 2})])
    assert "py_only" not in text.splitlines()[-2] + text.splitlines()[-1]
    assert text.startswith("# left out (no metacheck .R version): lab::py_only")


def test_pack_template_installs_what_the_store_ci_installs(ms) -> None:
    from pytacheck.packs.scaffold import INSTALL_SPEC, pack_new

    root = pack_new("tmpl", ms.work)
    workflow = (root / ".github" / "workflows" / "pytacheck.yml").read_text()
    assert f'pip install "{INSTALL_SPEC}"' in workflow and "{{" not in workflow
    ci = ROOT / "contrib" / "pytacheck-modules" / ".github" / "workflows" / "check.yml"
    if ci.is_file():
        spec = re.search(r'PYTACHECK_SPEC: "([^"]+)"', ci.read_text())
        assert spec is not None and spec.group(1) == INSTALL_SPEC


def test_modules_md_does_not_promise_html_run_records() -> None:
    text = (ROOT / "docs" / "MODULES.md").read_text()
    assert 'RunRecord.read("report.html")  #' not in text
    assert "HTML reports do not embed the record" in text


def test_report_record_keeps_the_run_order(ms, capsys, demo_json) -> None:
    try:
        from pytacheck.report import report as report_mod

        report_mod.report  # noqa: B018
    except (ImportError, AttributeError):
        pytest.skip("report rendering is not available")
    _lab(ms, zzz=mod_src("zzz", keywords=("intro",)))
    record = ms.work / "rr.json"
    argv = ["report", demo_json, "-m", "marginal", "-m", "lab::zzz", "-f", "md",
            "-o", str(ms.work / "r.md"), "--record", str(record)]  # fmt: skip
    assert main(argv) == 0
    assert [m["name"] for m in json.loads(record.read_text())["modules"]] == ["marginal", "zzz"]


def test_contrib_store_note_stays_until_the_index_has_commits() -> None:
    seed = ROOT / "contrib" / "pytacheck-modules"
    if not seed.is_dir():
        pytest.skip("no contrib seed")
    index = json.loads((seed / "index.json").read_text())
    if any("path" in (p.get("source") or {}) for p in index["packs"]):
        assert "Before the first CI build" in (seed / "README.md").read_text()
