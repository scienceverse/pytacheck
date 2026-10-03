"""Loading pack modules: synthetic packages, helpers, reloads, revisions, integrity, dist packs."""

from __future__ import annotations

import os
import sys
import warnings

import pytest

import metacheck as pc
from metacheck.module import module_find, module_list, module_run
from metacheck.packs.manifest import PackError
from metacheck.packs.registry import active_packs, get_pack, load_module, refresh, registry
from tests.modsys.helpers import REV_A, REV_B, mod_src


def _bump(path, text: str) -> None:
    """Rewrite a file and move its mtime forward (coarse clocks must not hide the edit)."""
    st = path.stat()
    path.write_text(text)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))


def test_relative_helper_imports(ms, paper) -> None:
    helper = "VALUE = 'from helper'\n"
    src = mod_src(
        "uses_helper", header="from ._util import VALUE", body='return {"summary_text": VALUE}'
    )
    ms.install(
        "demo", {"uses_helper": src}, files={"_util.py": helper, "sub/__init__.py": "X = 1\n"}
    )
    assert module_run(paper, "demo::uses_helper").summary_text == "from helper"
    name = module_find("demo::uses_helper").func.__module__
    assert name == f"pytacheck_packs.demo_{REV_A[:12]}.uses_helper"
    assert f"pytacheck_packs.demo_{REV_A[:12]}._util" in sys.modules
    assert get_pack("demo").modules() == ["uses_helper"]  # helpers are not modules


def test_hyphenated_pack_name(ms, paper) -> None:
    src = mod_src("hy", header="from ._h import T", body='return {"summary_text": T}')
    folder = ms.pack(ms.root / "hy", "my-pack", {"hy": src}, files={"_h.py": "T = 'hyphen ok'\n"})
    ms.pin("my-pack", {"path": str(folder)})
    assert module_run(paper, "my-pack::hy").summary_text == "hyphen ok"


def test_path_pack_reloads_on_edit_without_bytecode(ms, paper) -> None:
    folder = ms.pack(
        ms.root / "dev",
        "dev",
        {"live": mod_src("live", header="from ._h import T", body='return {"summary_text": T}')},
        files={"_h.py": "T = 'v1'\n"},
    )
    ms.pin("dev", {"path": str(folder)})
    assert module_run(paper, "live").summary_text == "v1"
    assert module_run(paper, "live").summary_text == "v1"
    _bump(folder / "_h.py", "T = 'v2 (helper edited)'\n")
    assert module_run(paper, "live").summary_text == "v2 (helper edited)"
    _bump(folder / "live.py", mod_src("live", "v3"))
    assert module_run(paper, "dev::live").summary_text == "v3"
    assert not list(folder.rglob("__pycache__")), "path packs must not write bytecode"
    # a new module file is found immediately
    (folder / "fresh.py").write_text(mod_src("fresh", "new"))
    assert module_run(paper, "fresh").summary_text == "new"


def test_two_revisions_side_by_side(ms, paper) -> None:
    pin_a = ms.install("demo", {"which": mod_src("which", "rev A")}, rev=REV_A)
    assert module_run(paper, "demo::which").summary_text == "rev A"
    spec_a = module_find("demo::which")
    pack_a = get_pack("demo")
    ms.install("demo", {"which": mod_src("which", "rev B")}, rev=REV_B)  # re-pin (hot swap)
    assert module_run(paper, "demo::which").summary_text == "rev B"
    spec_b = module_find("demo::which")
    assert spec_a.func is not spec_b.func
    assert spec_a.func.__module__ != spec_b.func.__module__
    assert {f"pytacheck_packs.demo_{r[:12]}.which" for r in (REV_A, REV_B)} <= set(sys.modules)
    # the old revision still loads and runs on its own
    assert module_run(paper, load_module(pack_a, "which")).summary_text == "rev A"
    assert module_run(paper, spec_a).provenance["source"]["rev"] == pin_a["rev"]
    assert module_run(paper, spec_b).provenance["source"]["rev"] == REV_B


def test_installed_file_modified_warns_and_flags(ms, paper) -> None:
    ms.install("demo", {"tamper": mod_src("tamper", "original")}, files={"_h.py": "Z = 1\n"})
    out = module_run(paper, "demo::tamper")
    assert out.provenance["modified"] is False
    (get_pack("demo").root / "_h.py").write_text("Z = 2\n")
    with pytest.warns(UserWarning, match=r"'demo'.*modified after install: _h\.py"):
        out = module_run(paper, "demo::tamper")
    assert out.provenance["modified"] is True


def test_install_record_must_match_pin(ms) -> None:
    pin = ms.install("demo", {"m": mod_src("m")})
    ms.pin("demo", {**pin, "tree_sha256": "0" * 64})
    with pytest.raises(PackError, match="does not match its pin"):
        get_pack("demo")
    assert "demo" in registry().problems


def test_config_false_hides_and_null_removes(ms, paper, monkeypatch, tmp_path) -> None:
    # a dist pack: an importable package holding pack.json, registered by entry point
    pkg = tmp_path / "site" / "dist_demo"
    ms.pack(
        pkg, "distpack", {"distmod": mod_src("distmod", "from dist")}, files={"__init__.py": ""}
    )
    monkeypatch.syspath_prepend(str(tmp_path / "site"))

    class EP:
        name, module, value, dist = "distpack", "dist_demo", "dist_demo", None

    import metacheck.packs.registry as reg

    monkeypatch.setattr(reg, "_dist_entry_points", lambda: [EP()])
    refresh()
    assert get_pack("distpack").trust == "dist"
    out = module_run(paper, "distmod")
    assert out.summary_text == "from dist"
    assert out.provenance["pack"] == "distpack" and out.provenance["trust"] == "dist"
    ms.pin("distpack", False)
    assert "distpack" not in active_packs()
    with pytest.raises(PackError, match="hidden with false"):
        get_pack("distpack")
    ms.config({"packs": {"distpack": None}})
    assert "distpack" in active_packs()


def test_module_list_default_unchanged_and_pack_column(ms) -> None:
    before = module_list()
    ms.pin("demo", {"path": str(ms.pack(ms.root / "p", "demo", {"zeta": mod_src("zeta")}))})
    after = module_list()
    assert list(after.columns) == ["name", "title", "description", "section", "path"]
    assert after.equals(before)  # packs never change the plain listing
    builtin = module_list(pack="metacheck")
    assert list(builtin.columns) == [*before.columns, "pack"]
    assert builtin.drop(columns="pack").equals(before)
    everything = module_list(pack="*")
    # metacheck registers its own datapackage pack (an entry point in pyproject.toml)
    assert set(everything["pack"]) == {"metacheck", "datapackage", "demo"}
    assert everything.loc[everything["pack"] == "demo", "name"].tolist() == ["zeta"]
    assert module_list(pack="demo")["name"].tolist() == ["zeta"]
    with pytest.raises(ValueError, match="not both"):
        module_list(ms.work, pack="demo")


def test_refresh_reimports(ms, paper) -> None:
    ms.install("demo", {"r": mod_src("r", "one")})
    first = module_find("demo::r")
    refresh()
    assert module_find("demo::r").func is not first.func
    assert pc.refresh is refresh


def test_import_error_is_a_module_error(ms, paper) -> None:
    folder = ms.pack(ms.root / "bad", "bad", {"broken": "raise RuntimeError('boom')\n"})
    ms.pin("bad", {"path": str(folder)})
    with pytest.raises(pc.ModuleError, match=r"The module 'bad::broken' has errors: boom"):
        module_run(paper, "broken")
    # fixing the file is picked up
    _bump(folder / "broken.py", mod_src("broken", "fixed"))
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert module_run(paper, "broken").summary_text == "fixed"
