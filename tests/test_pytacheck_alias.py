"""``pytacheck`` is the old import name: every ``pytacheck.<sub>`` is ``metacheck.<sub>`` itself."""

from __future__ import annotations

import gc
import json
import pickle
import re
import shutil
import subprocess
import sys
import textwrap
import weakref
from importlib import resources
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

import metacheck

SRC = Path(metacheck.__file__).parent
ROOT = SRC.parent.parent
ALIAS = ROOT / "src" / "pytacheck"

#: the committed alias package's __init__.py; a rebase that puts the old package's
#: __init__.py back in its place must fail here (the rename guard skips the alias)
ALIAS_INIT = '''\
"""pytacheck is the old import name of metacheck, kept so that existing code keeps working.

``import pytacheck.<sub>`` gives the ``metacheck.<sub>`` module itself, not a copy,
and the names of ``import pytacheck`` come from ``metacheck``. New code should
``import metacheck``.
"""

from metacheck._alias import install as _install

_install(__name__, "metacheck")
del _install
'''


def _modules() -> list[str]:
    names = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).with_suffix("")
        if "__pycache__" in rel.parts or rel.name == "__main__" or rel == Path("__init__"):
            continue
        parts = rel.parts[:-1] if rel.name == "__init__" else rel.parts
        names.append(".".join(parts))
    return names


def _run(code: str, *flags: str) -> str:
    out = subprocess.run(
        [sys.executable, *flags, "-c", textwrap.dedent(code)], capture_output=True, text=True
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


@pytest.mark.parametrize("first", ["pytacheck", "metacheck"])
def test_every_submodule_is_one_module_object(first: str) -> None:
    """In a fresh process, in either order, and never a second copy of a file."""
    code = f"""
        import importlib, sys
        names = {_modules()!r}
        second = "metacheck" if {first!r} == "pytacheck" else "pytacheck"
        bad = []
        for n in names:
            try:
                a = importlib.import_module({first!r} + "." + n)
            except ImportError:
                continue  # an optional extra that is not installed
            b = importlib.import_module(second + "." + n)
            if a is not b or a.__spec__.name != a.__name__:
                bad.append(n)
        mods = {{id(m): m for m in list(sys.modules.values())}}.values()  # both names, once
        files = [getattr(m, "__file__", None) for m in mods]
        files = [f for f in files if f]
        print(bad or len(files) - len(set(files)))
    """
    assert _run(code) == "0"


def test_importing_the_old_name_does_not_warn() -> None:
    assert _run("import pytacheck, pytacheck.io.convert; print('ok')", "-W", "error") == "ok"


def test_import_forms() -> None:
    import importlib

    import metacheck.io.convert
    import pytacheck.io.convert
    from pytacheck.io.convert import convert
    from pytacheck.papers import Paper

    assert pytacheck.io.convert is metacheck.io.convert
    assert importlib.import_module("pytacheck.io.convert") is metacheck.io.convert
    assert convert is metacheck.io.convert.convert
    assert Paper is metacheck.Paper
    assert pytacheck.__version__ == metacheck.__version__
    assert pytacheck.read is metacheck.read
    assert sorted(pytacheck.__all__) == sorted(metacheck.__all__)
    with pytest.raises(AttributeError, match=r"module 'pytacheck' has no attribute 'nope'"):
        pytacheck.nope  # noqa: B018
    with pytest.raises(ModuleNotFoundError, match=r"pytacheck\.nope"):
        import pytacheck.nope


def test_patches_through_either_name_reach_metacheck(monkeypatch: pytest.MonkeyPatch) -> None:
    import metacheck.stats
    import metacheck.utils
    import pytacheck

    with mock.patch("pytacheck.utils.online", return_value=False) as m:
        assert metacheck.utils.online is m
    with mock.patch("pytacheck.convert") as m:
        assert metacheck.convert is m
    assert pytacheck.convert is metacheck.convert
    before = metacheck.stats  # a submodule that is also a function name
    with mock.patch("pytacheck.stats"):
        pass
    assert metacheck.stats is before
    monkeypatch.setattr("pytacheck.text.causal.causal_relations", "patched")
    import metacheck.text.causal

    assert metacheck.text.causal.causal_relations == "patched"


def test_a_patch_after_a_direct_change_restores_the_direct_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pytacheck

    original = metacheck.demopaper
    monkeypatch.setattr(metacheck, "demopaper", original)  # undone after the test
    pytacheck.demopaper = "through the alias"
    assert metacheck.demopaper == "through the alias"
    metacheck.demopaper = "set directly"
    with mock.patch("pytacheck.demopaper") as m:
        assert metacheck.demopaper is m
    assert metacheck.demopaper == "set directly"
    del pytacheck.demopaper  # never written since the patch: deletes from metacheck
    assert "demopaper" not in vars(metacheck)


def test_nested_patches_through_the_alias_unwind_one_at_a_time() -> None:
    import pytacheck

    original = metacheck.convert
    with mock.patch("pytacheck.convert", "A"):
        with mock.patch("pytacheck.convert", "B"):
            assert metacheck.convert == "B"
        assert metacheck.convert == "A"
    assert metacheck.convert is original
    before = metacheck.demopaper
    with mock.patch("pytacheck.demopaper", "A"):
        with mock.patch.object(pytacheck, "demopaper", "B"):
            assert metacheck.demopaper == "B"
        assert metacheck.demopaper == "A"
    assert metacheck.demopaper is before
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pytacheck, "demopaper", "A")
        mp.setattr(pytacheck, "demopaper", "B")
    assert metacheck.demopaper is before


#: Patch sequences through the old name, run once through pytacheck and once with
#: both names bound to the plain module metacheck (pcn is put in front).
PATCH_SEQUENCES = """
    import importlib, json
    from unittest import mock
    import pytest
    import metacheck
    pc = importlib.import_module(pcn)
    orig, seen = object(), {}

    def start(case):
        name = f"_probe_{len(seen)}"
        setattr(metacheck, name, orig)
        return name, seen.setdefault(case, [])

    def state(name):
        value = vars(metacheck).get(name, "gone")
        return "orig" if value is orig else value

    n, s = start("create=True around a direct patch and a nested patch")
    with mock.patch(f"{pcn}.{n}", "A", create=True):
        with mock.patch(f"metacheck.{n}", "B"):
            with mock.patch(f"{pcn}.{n}", "C"):
                s.append(state(n))
            s.append(state(n))
        s.append(state(n))
    s.append(state(n))

    n, s = start("create=True with the value already there")
    with mock.patch(f"{pcn}.{n}", orig, create=True):
        s.append(state(n))
    s.append(state(n))

    # only the end: a delete through the old name undoes a write through it, as a
    # mock.patch(create=True) ends there, so metacheck holds orig until the undo
    n, s = start("monkeypatch.delattr of a name assigned through the old name, undone")
    setattr(pc, n, "S")
    mp = pytest.MonkeyPatch()
    mp.delattr(pc, n)
    mp.undo()
    s.append(state(n))

    n, s = start("a monkeypatch set and undone, then a del through the old name")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pc, n, "A")
    delattr(pc, n)
    s.append(state(n))

    n, s = start("a monkeypatch set and undone, then monkeypatch.delattr through the old name")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pc, n, "A")
    with pytest.MonkeyPatch.context() as mp:
        mp.delattr(pc, n)
        s.append(state(n))
    s.append(state(n))

    s = seen.setdefault("del of a submodule after its first import through the old name", [])
    importlib.import_module(f"{pcn}.utils")
    del pc.utils
    s.append("utils" in vars(metacheck))
    print(json.dumps(seen))
"""


def test_patches_through_the_alias_end_as_on_the_plain_module() -> None:
    alias, plain = (
        json.loads(_run(f"pcn = {pcn!r}\n" + textwrap.dedent(PATCH_SEQUENCES)))
        for pcn in ("pytacheck", "metacheck")
    )
    assert alias == plain


def test_assignments_through_the_alias_are_not_kept_forever(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a delete pops a remembered write: plain assignments must not pile up."""
    import pytacheck

    class Value:
        pass

    name = "_probe_many"
    monkeypatch.setattr(metacheck, name, None, raising=False)  # removed after the test
    first = Value()
    ref = weakref.ref(first)
    setattr(pytacheck, name, first)
    del first
    for _ in range(1000):
        setattr(pytacheck, name, Value())
    last = getattr(metacheck, name)
    gc.collect()
    assert ref() is None
    with mock.patch(f"pytacheck.{name}"):
        pass
    assert getattr(metacheck, name) is last


def test_an_old_name_import_keeps_the_package_attribute_of_the_same_name() -> None:
    """Importing ``pytacheck.io.read`` must not turn the function ``metacheck.io.read``
    into the module ``metacheck.io.read``: under the real name that never happens once
    the module is loaded, and it must not under the old name either."""
    code = """
        import sys, types
        from unittest import mock
        import metacheck, metacheck.io, metacheck.report, metacheck.stats, metacheck.text
        names = [(metacheck, "module"), (metacheck.io, "read"), (metacheck.stats, "statcheck"),
                 (metacheck.text, "extract_tests"), (metacheck.text, "json_expand"),
                 (metacheck.report, "emojis")]
        before = [getattr(p, n) for p, n in names]
        with mock.patch("pytacheck.io.read._read_xml"):
            pass
        import pytacheck.module, pytacheck.stats.statcheck, pytacheck.text.extract_tests
        import pytacheck.text.json_expand, pytacheck.report.emojis
        from pytacheck.module import ModuleOutput
        after = [getattr(p, n) for p, n in names]
        bad = [n for (p, n), a, b in zip(names, before, after) if a is not b]
        guards = [p.__name__ for p in (sys.modules["pytacheck"], *[p for p, _ in names])
                  if "_alias_pending" in vars(type(p))]
        import pytacheck.io.read, metacheck.io.read
        same = sys.modules["pytacheck.io.read"] is sys.modules["metacheck.io.read"]
        print(bad, guards, same, type(sys.modules["metacheck.io.read"]) is types.ModuleType)
    """
    assert _run(code, "-W", "error") == "[] [] True True"


def test_first_imports_in_many_threads_keep_the_package_attributes(tmp_path: Path) -> None:
    """The guard above holds while threads import submodules of one package at once,
    both under the alias top level and under a package that both names share."""
    new = tmp_path / "newpkg"
    (new / "sub").mkdir(parents=True)
    (tmp_path / "oldpkg").mkdir()
    for pkg in (new, new / "sub"):
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        for i in range(32):
            (pkg / f"m{i}.py").write_text("", encoding="utf-8")
    (tmp_path / "oldpkg" / "__init__.py").write_text(
        "from metacheck._alias import install\n\ninstall(__name__, 'newpkg')\n", encoding="utf-8"
    )
    code = f"""
        import importlib, sys, threading
        sys.path.insert(0, {str(tmp_path)!r})
        sys.setswitchinterval(1e-6)  # switch threads as often as possible
        import newpkg.sub, oldpkg.sub
        names = [f"m{{i}}" for i in range(32)]
        bad = 0
        for old, real in (("oldpkg", newpkg), ("oldpkg.sub", newpkg.sub)):
            for n in names:
                importlib.import_module(f"{{real.__name__}}.{{n}}")
            held = {{n: object() for n in names}}  # package attributes named as the submodules
            for _ in range(200):
                for n in names:
                    sys.modules.pop(f"{{old}}.{{n}}", None)
                    setattr(real, n, held[n])
                barrier = threading.Barrier(len(names))
                def work(n):
                    barrier.wait()
                    importlib.import_module(f"{{old}}.{{n}}")
                threads = [threading.Thread(target=work, args=(n,)) for n in names]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()
                bad += any(getattr(real, n) is not held[n] for n in names)
                bad += "_alias_pending" in vars(type(sys.modules[old]))  # a guard left armed
        print(bad)
    """
    assert _run(code) == "0"


def test_an_alias_module_executed_by_hand_arms_no_guard() -> None:
    """Only an import makes the parent assignment that the guard drops: after
    exec_module() by hand or by a LazyLoader, a deliberate one must stay."""
    code = """
        import importlib.util, sys
        import metacheck, pytacheck.text
        from metacheck._alias import AliasFinder
        finder = next(f for f in sys.meta_path if isinstance(f, AliasFinder))
        spec = finder.find_spec("pytacheck.text.extract_tests")
        by_hand = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(by_hand)
        spec = finder.find_spec("pytacheck.text.json_expand")
        spec.loader = importlib.util.LazyLoader(spec.loader)
        lazy = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = lazy
        spec.loader.exec_module(lazy)
        lazy.json_expand  # loads it
        armed = "_alias_pending" in vars(type(metacheck.text))
        metacheck.text.extract_tests = by_hand
        metacheck.text.json_expand = lazy
        print(armed, metacheck.text.extract_tests is by_hand, metacheck.text.json_expand is lazy)
    """
    assert _run(code) == "False True True"


def test_monkeypatch_through_the_alias_is_undone_exactly() -> None:
    import pytacheck

    before = metacheck.demofile
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(pytacheck, "demofile", "patched")
        assert metacheck.demofile == "patched"
    assert metacheck.demofile is before


def test_pickles_name_metacheck_and_old_pickles_load() -> None:
    import pytacheck

    paper = pytacheck.demopaper()
    data = pickle.dumps(paper)
    assert b"pytacheck" not in data
    old = data.replace(b"metacheck.papers", b"pytacheck.papers")  # as 0.4.0a1 wrote it
    assert type(pickle.loads(old)) is metacheck.Paper


def test_resources_and_the_command() -> None:
    assert str(resources.files("pytacheck.resources")) == str(
        resources.files("metacheck.resources")
    )
    out = subprocess.run(
        [sys.executable, "-m", "pytacheck", "--help"], capture_output=True, text=True
    )
    assert out.returncode == 0 and "usage" in out.stdout.lower()


def test_python_m_runs_a_submodule_through_the_old_name() -> None:
    """runpy asks the alias loader for the code of ``pytacheck.cli``."""
    out = subprocess.run(
        [sys.executable, "-m", "pytacheck.cli", "--help"], capture_output=True, text=True
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.startswith("usage: pytacheck")


def test_the_alias_loader_answers_for_the_real_module() -> None:
    import importlib.util

    import metacheck.cli

    spec = importlib.util.find_spec("pytacheck.cli")
    assert spec is not None and spec.loader is not None
    loader: Any = spec.loader
    assert loader.get_filename("pytacheck.cli") == metacheck.cli.__file__
    assert loader.is_package("pytacheck.cli") is False
    assert "def main" in loader.get_source("pytacheck.cli")
    assert loader.get_code("pytacheck.cli") is not None


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
@pytest.mark.skipif(not (ROOT / ".git").exists(), reason="needs a git checkout")
def test_the_alias_folder_holds_only_the_alias() -> None:
    """Anything else there would be imported as itself (the finder only skips __main__).

    Tracked files only: git keeps old sub-folders that hold an ignored __pycache__
    after a checkout across the rename, and those are never imported.
    """
    out = subprocess.run(
        ["git", "ls-files", "src/pytacheck"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    rel = {p.removeprefix("src/pytacheck/") for p in out}
    assert {"__init__.py", "__init__.pyi", "__main__.py", "py.typed"} <= rel
    assert all(r in {"__init__.py", "__main__.py", "py.typed"} or r.endswith(".pyi") for r in rel)


def test_the_alias_init_is_the_committed_alias() -> None:
    assert (ALIAS / "__init__.py").read_text(encoding="utf-8") == ALIAS_INIT


def test_the_alias_stubs_are_current() -> None:
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "alias_stubs.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stdout + out.stderr


PROBE = """\
import pytacheck
import pytacheck as pc
from pytacheck.module import module
from pytacheck.report import report_module_run
from pytacheck.text import text_search

reveal_type(pytacheck.read)
reveal_type(pytacheck.Paper)
reveal_type(pytacheck.module_run)
reveal_type(module)
reveal_type(report_module_run)


@pc.module(title="x")
def trial(paper: object) -> object:
    return text_search(paper, "NCT")


x: int = pc.module_run

from pytacheck.config import PROJECT_CONFIG
from pytacheck.http import skipping_api_limits
from pytacheck.module import _locate
"""


@pytest.mark.slow
def test_type_checkers_see_real_types_through_the_old_name(tmp_path: Path) -> None:
    api = pytest.importorskip("mypy.api")
    probe = tmp_path / "probe.py"
    probe.write_text(PROBE, encoding="utf-8")
    out, err, _ = api.run(
        [
            "--config-file=",
            "--strict",
            "--python-executable",
            sys.executable,
            "--cache-dir",
            str(tmp_path / "cache"),
            str(probe),
        ]
    )
    revealed = re.findall(r'Revealed type is "(.*)"', out)
    assert len(revealed) == 5, out + err
    assert not [t for t in revealed if t == "Any" or t.startswith("Any ")], out
    assert "metacheck.papers.model.Paper" in revealed[1], out
    assert "Untyped decorator" not in out, out
    assert "has no attribute" not in out, out  # names outside __all__ are Any, not errors
    assert re.search(r'probe\.py:19: error: Incompatible types in assignment .*"int"', out), out
