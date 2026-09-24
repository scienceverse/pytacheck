"""module_find() search order, qualified refs, labels and allow_local."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import pytacheck as pc
from pytacheck.module import ModuleError, module, module_find, module_run, use
from pytacheck.packs.manifest import PackError
from tests.modsys.helpers import mod_src


def test_builtin_wins_over_everything(ms, paper) -> None:
    (ms.work / "marginal.py").write_text(mod_src("marginal", "local file"))
    ms.pin("demo", {"path": str(ms.pack(ms.root / "p", "demo", {"marginal": mod_src("marginal")}))})
    spec = module_find("marginal")
    assert spec.pack == "metacheck"
    assert spec.func.__module__ == "pytacheck.modules.marginal"
    assert module_find("metacheck::marginal") is spec
    assert module_run(paper, "metacheck::marginal").module == "marginal"


def test_spec_and_function_are_used_as_is() -> None:
    @module(title="Inline", description="d")
    def inline(paper):
        return {}

    assert module_find(inline) is inline.__pytacheck_module__
    assert module_find(inline.__pytacheck_module__) is inline.__pytacheck_module__


def test_legacy_order_entry_point_then_files_then_packs_then_paths(ms, monkeypatch) -> None:
    import pytacheck.module as m

    folder = ms.pack(ms.root / "p", "demo", {n: mod_src(n, f"pack {n}") for n in "abcde"})
    ms.pin("demo", {"path": str(folder)})
    (ms.work / "modules").mkdir()
    for n in "abc":
        (ms.work / "modules" / f"{n}.py").write_text(mod_src(n, f"modules/{n}"))
    for n in "ab":
        (ms.work / f"{n}.py").write_text(mod_src(n, f"./{n}"))
    (ms.work / "e").write_text(mod_src("e", "bare file"))  # an existing path named like a module

    class EP:
        def load(self):
            @module(title="EP", description="entry point")
            def a(paper):
                return {"summary_text": "entry point"}

            return a

    monkeypatch.setattr(m, "_entry_point_specs", lambda: {"a": EP()})
    text = {n: module_run(pc.test_paper("x"), n).summary_text for n in "abcde"}
    assert text == {
        "a": "entry point",
        "b": "./b",
        "c": "modules/c",
        "d": "pack d",
        "e": "pack e",  # packs are searched before explicit paths
    }
    (ms.work / "sub").mkdir()
    (ms.work / "sub" / "e.py").write_text(mod_src("e", "path"))
    assert module_run(pc.test_paper("x"), "sub/e.py").summary_text == "path"
    assert module_run(pc.test_paper("x"), str(ms.work / "b.py")).summary_text == "./b"


def test_path_labels_are_unchanged(ms, paper) -> None:
    (ms.work / "sub").mkdir()
    (ms.work / "sub" / "mine.py").write_text(mod_src("mine"))
    out = module_run(paper, "sub/mine.py")
    assert out.module == "sub/mine.py"  # the string as given, as before
    out = module_run(paper, Path("sub/mine.py"))
    assert out.module == "mine"


def test_unknown_module_message_is_unchanged(ms) -> None:
    with pytest.raises(ModuleError, match=r"^There were no modules that matched nope\n"):
        module_find("nope")


def test_unknown_pack_and_module(ms) -> None:
    ms.pin("demo", {"path": str(ms.pack(ms.root / "p", "demo", {"alpha": mod_src("alpha")}))})
    with pytest.raises(PackError, match=r"no active pack named 'nopack'.*metacheck, demo"):
        module_find("nopack::x")
    with pytest.raises(
        ModuleError, match=r"pack 'demo' has no module 'beta'\. Its modules are: alpha"
    ):
        module_find("demo::beta")
    with pytest.raises(ModuleError, match="pack 'metacheck' has no module 'nope'"):
        module_find("metacheck::nope")


def test_qualified_label_chaining_and_suffix(ms, paper) -> None:
    alpha = mod_src(
        "alpha",
        body='return {"summary_text": "A", "summary_table": pd.DataFrame({"paper_id": [paper.paper_id], "n": [1]})}',
        header="import pandas as pd",
    )
    beta = mod_src(
        "beta",
        body=(
            'return {"summary_text": get_prev_outputs("alpha", "summary_text") + "B", '
            '"summary_table": pd.DataFrame({"paper_id": [paper.paper_id], "n": [2]})}'
        ),
        header="import pandas as pd",
    )
    folder = ms.pack(ms.root / "p", "demo", {"alpha": alpha, "beta": beta})
    ms.pin("demo", {"path": str(folder)})
    first = module_run(paper, "demo::alpha")
    assert first.module == "alpha"
    second = module_run(first, "demo::beta")
    assert second.module == "beta"
    assert second.summary_text == "AB"
    assert list(second.prev_outputs) == ["alpha"]
    assert list(second.summary_table.columns) == ["paper_id", "n", "n.beta"]
    # the same chain with bare names gives the same labels and table
    bare = module_run(module_run(paper, "alpha"), "beta")
    assert bare.module == "beta" and bare.summary_text == "AB"
    pd.testing.assert_frame_equal(bare.summary_table, second.summary_table)


def test_ambiguous_name_lists_qualified_refs(ms, paper) -> None:
    ms.pin("one", {"path": str(ms.pack(ms.root / "one", "one", {"gamma": mod_src("gamma", "1")}))})
    ms.pin("two", {"path": str(ms.pack(ms.root / "two", "two", {"gamma": mod_src("gamma", "2")}))})
    with pytest.raises(ModuleError, match='use one of "one::gamma", "two::gamma" instead'):
        module_find("gamma")
    assert module_run(paper, "two::gamma").summary_text == "2"


def test_allow_local_false(ms, paper) -> None:
    (ms.work / "localmod.py").write_text(mod_src("localmod"))
    (ms.work / "modules").mkdir()
    (ms.work / "modules" / "inmodules.py").write_text(mod_src("inmodules"))
    ms.pin("dev", {"path": str(ms.pack(ms.root / "dev", "dev", {"devmod": mod_src("devmod")}))})
    ms.install("store", {"storemod": mod_src("storemod", "stored")})
    for ref in ("localmod", "inmodules", "devmod", "localmod.py"):
        assert module_find(ref)
    with use(allow_local=False):
        for ref in ("localmod", "inmodules", "devmod", "localmod.py", str(ms.work / "localmod.py")):
            with pytest.raises(ModuleError):
                module_find(ref)
        with pytest.raises(PackError, match="allow_local=False"):
            module_find("dev::devmod")
        with pytest.raises(ModuleError, match="allow_local=False"):
            pc.module_list(ms.work)
        assert module_run(paper, "storemod").summary_text == "stored"
        assert module_run(paper, "marginal").module == "marginal"
        assert "dev" not in set(pc.module_list(pack="*")["pack"])
        with use(offline=True):  # nested use() keeps the outer allow_local
            with pytest.raises(ModuleError):
                module_find("localmod")
    assert module_find("localmod")


def test_load_file_module_name_is_deterministic(ms) -> None:
    import sys

    (ms.work / "det.py").write_text(mod_src("det"))
    spec = module_find("det")
    name = spec.func.__module__
    assert name.startswith("pytacheck_user_module_det_") and name in sys.modules
    assert module_find("det").func.__module__ == name  # re-executed, same name


def test_pinned_but_not_installed(ms) -> None:
    ms.pin("ghost", {"source": {"github": "x/ghost"}, "rev": "c" * 40, "store": "pytacheck"})
    with pytest.raises(
        PackError,
        match=r"pinned in .*config.json \(rev cccccccccccc\) but not installed.*pytacheck pack install",
    ):
        module_find("ghost::thing")
    with pytest.raises(ModuleError, match="Some configured packs are unavailable"):
        module_find("thing")
    # installing it (same pin) is picked up without refresh()
    ms.install("ghost", {"thing": mod_src("thing", "here")}, rev="c" * 40)
    assert module_run(pc.test_paper("x"), "ghost::thing").summary_text == "here"
