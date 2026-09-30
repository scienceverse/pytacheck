"""The pytacheck names that packs, saved files and scripts written for 0.4.0a1 still use.

The rename script leaves this file alone (``tests/*test_pytacheck_*.py``), so the
old spellings below stay as they are. Each name that is written as well as read is
written in its old form only and read in both forms, old first.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import types
from pathlib import Path

import pytest

from metacheck import module as modmod
from metacheck.packs import install, registry, scan, tree
from metacheck.repro import tables
from tests.modsys.helpers import REV_A, mod_src

# --- the module spec attribute ------------------------------------------------


def test_decorated_function_carries_the_spec_under_the_old_name_only() -> None:
    from metacheck.modules.marginal import marginal as fn

    spec = modmod.spec_of(fn)
    assert spec is not None and spec.name == "marginal"
    assert fn.__pytacheck_module__ is spec
    assert not hasattr(fn, "__metacheck_module__")


def test_spec_of_reads_the_new_attribute_alone() -> None:
    spec = modmod.module_find("marginal")

    def new() -> None: ...

    new.__metacheck_module__ = spec  # type: ignore[attr-defined]
    assert modmod.spec_of(new) is spec
    assert modmod.module_find(new) is spec


def test_legacy_code_that_sets_the_old_attribute_wins() -> None:
    """Code written for 0.4.0a1 may set __pytacheck_module__ on a decorated function."""
    first = modmod.module_find("marginal")
    other = modmod.module_find("all_p_values")

    @modmod.module(title="x")
    def fn(paper: object) -> None: ...

    fn.__metacheck_module__ = first  # type: ignore[attr-defined]
    fn.__pytacheck_module__ = other  # type: ignore[attr-defined]
    assert modmod.spec_of(fn) is other


def test_spec_of_ignores_other_values() -> None:
    def fn() -> None: ...

    fn.__pytacheck_module__ = "not a spec"  # type: ignore[attr-defined]
    assert modmod.spec_of(fn) is None


# --- the pack import root and the pack attribute -------------------------------


def test_pack_packages_carry_the_old_pack_attribute_only(ms) -> None:
    ms.install("demo", {"hello": mod_src("hello")})
    spec = modmod.module_find("demo::hello")
    assert spec.pack == "demo"
    pkg = sys.modules[f"pytacheck_packs.demo_{REV_A[:12]}"]
    assert pkg.__pytacheck_pack__ == "demo"
    assert not hasattr(pkg, "__metacheck_pack__")


@pytest.mark.parametrize("root", ["pytacheck_packs", "metacheck_packs"])
@pytest.mark.parametrize("attr", ["__pytacheck_pack__", "__metacheck_pack__"])
def test_pack_of_reads_both_roots_and_both_attributes(
    monkeypatch: pytest.MonkeyPatch, root: str, attr: str
) -> None:
    pkg = types.ModuleType(f"{root}.x_abc")
    setattr(pkg, attr, "x")
    monkeypatch.setitem(sys.modules, pkg.__name__, pkg)
    assert modmod._pack_of(f"{root}.x_abc.m") == "x"


def test_pack_roots_serve_the_old_root() -> None:
    assert registry.PACK_ROOTS == ("pytacheck_packs", "metacheck_packs")
    assert registry.PACKAGE_ROOT == "pytacheck_packs"


# --- the install record -------------------------------------------------------


@pytest.mark.parametrize("name", [".metacheck-install.json", ".pytacheck-install.json"])
def test_install_record_is_read_under_either_name(tmp_path: Path, name: str) -> None:
    (tmp_path / name).write_text(json.dumps({"tree_sha256": "x"}), encoding="utf-8")
    assert tree.read_install_record(tmp_path) == {"tree_sha256": "x"}


def test_the_written_install_record_name_wins(tmp_path: Path) -> None:
    (tmp_path / ".metacheck-install.json").write_text('{"a": 1}', encoding="utf-8")
    (tmp_path / ".pytacheck-install.json").write_text('{"a": 2}', encoding="utf-8")
    assert tree.read_install_record(tmp_path) == {"a": 2}


def test_install_records_are_not_hashed(tmp_path: Path) -> None:
    (tmp_path / "pack.json").write_text("{}", encoding="utf-8")
    before = tree.tree_sha256(tmp_path)
    for name in tree.INSTALL_RECORDS:
        (tmp_path / name).write_text("{}", encoding="utf-8")
    tree.clear_cache()
    assert tree.tree_sha256(tmp_path) == before


def test_install_still_writes_the_old_record_name() -> None:
    assert tree.INSTALL_RECORD == ".pytacheck-install.json"
    assert tree.INSTALL_RECORDS[0] == tree.INSTALL_RECORD


# --- the pack scanner ---------------------------------------------------------


@pytest.mark.parametrize("root", ["pytacheck", "metacheck"])
@pytest.mark.parametrize(
    "source",
    [
        "import {r}.http",
        "from {r}.http import get",
        "from {r} import http",
        "import {r} as pc\npc.http.get('x')",
        "import {r}.io\n{r}.http.get('x')",  # import X.io binds X as well
    ],
)
def test_http_is_flagged_in_both_forms(tmp_path: Path, root: str, source: str) -> None:
    f = tmp_path / "m.py"
    f.write_text(source.format(r=root) + "\n", encoding="utf-8")
    result = scan.scan_file(f)
    assert any("network" in why for _, why in result.risky), result
    assert scan.network_imports(result)


@pytest.mark.parametrize("root", ["pytacheck", "metacheck"])
@pytest.mark.parametrize("helper", ["db", "archives", "llm"])
def test_helper_imports_are_kept_in_both_forms(tmp_path: Path, root: str, helper: str) -> None:
    f = tmp_path / "m.py"
    f.write_text(f"from {root} import {helper}\n", encoding="utf-8")
    assert f"{root}.{helper}" in scan.scan_file(f).imports


@pytest.mark.parametrize("root", ["pytacheck", "metacheck"])
def test_helpers_reached_through_a_dotted_import_are_kept(tmp_path: Path, root: str) -> None:
    f = tmp_path / "m.py"
    f.write_text(f"import {root}.io\n{root}.db.crossref('x')\n", encoding="utf-8")
    assert f"{root}.db" in scan.scan_file(f).imports


def test_plain_first_party_imports_are_not_risky(tmp_path: Path) -> None:
    f = tmp_path / "m.py"
    f.write_text("from pytacheck.module import module\nimport pytacheck.text\n", encoding="utf-8")
    assert scan.scan_file(f).risky == ()


# --- requires -----------------------------------------------------------------


@pytest.mark.parametrize("name", ["pytacheck", "metacheck", "Pytacheck", "py_tacheck"])
def test_requires_names_this_package(name: str) -> None:
    ok = name.lower() in ("pytacheck", "metacheck")
    unmet = install._requires_ok({name: ">=0.0"})
    assert (unmet == []) is ok


def test_requires_metacheck_wins_over_pytacheck() -> None:
    assert install._requires_ok({"metacheck": ">=0.0", "pytacheck": ">=999"}) == []


def test_unmet_requires_keeps_the_name_the_pack_used() -> None:
    (msg,) = install._requires_ok({"pytacheck": ">=999"})
    assert msg.startswith("pytacheck>=999")


def test_pip_hint_installs_metacheck() -> None:
    assert install._pip_command(["pytacheck>=0.3", "numpy"]) == "pip install 'metacheck>=0.3' numpy"


def test_pack_check_warns_when_requires_names_both() -> None:
    from metacheck.packs import check

    issues = check._manifest_issues({"requires": {"metacheck": ">=0.0", "pytacheck": ">=0.0"}})
    assert any("only metacheck is checked" in i.message for i in issues)
    issues = check._manifest_issues({"requires": {"pytacheck": ">=0.0"}})
    assert not any("only metacheck" in i.message for i in issues)


# --- saved module tables ------------------------------------------------------


def _fields(cls: type) -> dict[str, object]:
    import dataclasses

    out: dict[str, object] = {}
    for f in dataclasses.fields(cls):
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
            out[f.name] = None
    return out


@pytest.mark.parametrize("root", ["pytacheck", "metacheck"])
def test_saved_tables_rebuild_dataclasses_under_either_name(root: str) -> None:
    from metacheck.report.blocks import ReportTable

    spec = {"type": f"{root}.report.blocks:ReportTable", "fields": _fields(ReportTable)}
    assert type(tables._decode_dataclass(spec)) is ReportTable


def test_saved_tables_of_0_4_0a1_decode_without_the_old_name() -> None:
    """An old ``pytacheck.*`` type is rebuilt from ``metacheck.*``, with no import of the
    alias, so a package attribute such as ``metacheck.stats.statcheck`` stays as it is."""
    code = (
        "import sys\n"
        "import metacheck.stats\n"
        "from metacheck.repro import tables\n"
        "f = metacheck.stats.statcheck\n"
        "print(type(tables._decode_dataclass("
        "{'type': 'pytacheck.report.blocks:ReportTable', 'fields': {}})).__name__)\n"
        "tables._decode_dataclass({'type': 'pytacheck.stats.statcheck:x', 'fields': {}})\n"
        "print(metacheck.stats.statcheck is f, [m for m in sys.modules if m.startswith('pytacheck')])\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.split("\n")[1:] == ["True []", ""], out.stdout


def test_saved_tables_name_dataclasses_as_0_4_0a1_did() -> None:
    """A 0.4.0a1 reader rebuilds only pytacheck.* types, so that is what is written."""
    from metacheck.report.blocks import ReportTable

    obj = ReportTable(**_fields(ReportTable))  # type: ignore[arg-type]
    encoded = tables._encode(obj)
    assert encoded["$dataclass"]["type"] == "pytacheck.report.blocks:ReportTable"
    assert type(tables._decode(encoded)) is ReportTable


# --- the repo-info cache --------------------------------------------------------


def test_repo_info_cache_entries_written_by_0_4_0a1_are_hits(tmp_path: Path) -> None:
    from metacheck.archives.info_cache import _repo_info_cache_get, _repo_info_cache_path
    from metacheck.utils import local_options

    with local_options({"metacheck.repo_info_cache.dir": str(tmp_path / "info")}):
        Path(_repo_info_cache_path("osf", "old")).write_bytes(
            b'{"format": "pytacheck.repo_info_cache", "version": 1, "value": 1}'
        )
        assert _repo_info_cache_get("osf", "old") == 1


# --- the loggers ----------------------------------------------------------------


def test_a_handler_on_metacheck_receives_the_pytacheck_records() -> None:
    """Records keep their old names, and the package's loggers sit under ``metacheck``."""
    import metacheck._logging  # noqa: F401 -- puts the link in place

    seen: list[logging.LogRecord] = []

    class Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(record)

    top, old, api = (logging.getLogger(n) for n in ("metacheck", "pytacheck", "pytacheck.api"))
    handler = Collect()
    levels = {logger: logger.level for logger in (top, old, api)}
    top.addHandler(handler)
    try:
        assert old.parent is top
        assert api.parent is old
        old.warning("from the root")
        api.warning("from the api")
        assert [(r.name, r.getMessage()) for r in seen] == [
            ("pytacheck", "from the root"),
            ("pytacheck.api", "from the api"),
        ]

        # a level set on the old logger still applies, to it and to its children
        seen.clear()
        old.setLevel(logging.ERROR)
        old.warning("dropped")
        api.warning("dropped too")
        api.error("kept")
        assert [r.getMessage() for r in seen] == ["kept"]

        # and a level set on metacheck applies where the old loggers set none
        seen.clear()
        old.setLevel(logging.NOTSET)
        top.setLevel(logging.DEBUG)
        api.debug("debug")
        assert [r.getMessage() for r in seen] == ["debug"]
    finally:
        top.removeHandler(handler)
        for logger, level in levels.items():
            logger.setLevel(level)


# --- the command --------------------------------------------------------------


@pytest.mark.parametrize("name", ["pytacheck", "metacheck"])
def test_python_m_help_names_pytacheck_for_both_commands(name: str) -> None:
    """Help text is unchanged by the rename (RENAME-4 decides it)."""
    out = subprocess.run(
        [sys.executable, "-m", name, "--help"], capture_output=True, text=True, check=True
    ).stdout
    assert out.startswith("usage: pytacheck")
