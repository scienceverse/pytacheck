"""The pytacheck names that packs, saved files and scripts written for 0.4.0a1 still use.

The rename script leaves this file alone (``tests/*test_pytacheck_*.py``), so the
old spellings below stay as they are. Each name that is written as well as read is
written in its old form only and read in both forms, old first, except where 0.4.0a1
meets the new form cleanly: run records, saved tables and repo-info cache entries are
written in the new form (0.4.0a1 refuses, skips or misses them) and read in both.
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
from metacheck import utils
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


def test_saved_tables_name_dataclasses_as_metacheck() -> None:
    """New tables name their types ``metacheck.*``, under the new format id, so a
    0.4.0a1 reader skips the file instead of reading those types as plain dicts."""
    from metacheck.report.blocks import ReportTable

    obj = ReportTable(**_fields(ReportTable))  # type: ignore[arg-type]
    encoded = tables._encode(obj)
    assert encoded["$dataclass"]["type"] == "metacheck.report.blocks:ReportTable"
    assert type(tables._decode(encoded)) is ReportTable


#: a table file as 0.4.0a1's ``capture_module_tables()`` wrote it (one module, whose
#: report is a ReportTable)
_TABLES_0_4_0A1 = (
    b'{"format": "pytacheck.module_tables", "version": 1, "paper_id": "p", '
    b'"generated": "2026-09-30T14:11:31+0200", "summary_table": null, '
    b'"modules": {"m": {"table": {"$df": {"names": ["x"], '
    b'"columns": [{"dtype": "int64", "data": [1, 2]}], "nrow": 2, "index": null, '
    b'"attrs": null}}, "report": {"$dataclass": {"type": "pytacheck.report.blocks:ReportTable", '
    b'"fields": {"data": {"$df": {"names": ["x"], '
    b'"columns": [{"dtype": "int64", "data": [1, 2]}], "nrow": 2, "index": null, '
    b'"attrs": null}}, "colwidths": "auto", "maxrows": 2, "escape": false, "column": "body", '
    b'"options": {}}}}, "traffic_light": "info", "summary_text": "s", "summary_table": null}}}'
)


def test_saved_tables_of_0_4_0a1_are_read_with_their_dataclasses(tmp_path: Path) -> None:
    import pandas as pd

    from metacheck.report.blocks import ReportTable

    (tmp_path / "p.json").write_bytes(_TABLES_0_4_0A1)
    loaded = tables._load_module_tables(tmp_path, "p", None)
    assert loaded is not None and loaded.module == "m"
    assert type(loaded.report) is ReportTable
    pd.testing.assert_frame_equal(loaded.report.data, pd.DataFrame({"x": [1, 2]}))
    pd.testing.assert_frame_equal(loaded.table, pd.DataFrame({"x": [1, 2]}))


def test_saved_tables_are_written_under_the_new_id(tmp_path: Path) -> None:
    import pandas as pd

    from metacheck.module import ModuleOutput
    from metacheck.report.blocks import ReportTable

    df = pd.DataFrame({"x": [1, 2]})
    mo = ModuleOutput(
        module="m", title="m", section="results", table=df,
        report=ReportTable(data=df), traffic_light="info", summary_text="s",
    )  # fmt: skip
    raw = json.loads(Path(tables.capture_module_tables([mo], tmp_path, paper_id="p")).read_bytes())
    assert (raw["format"], raw["version"]) == ("metacheck.module_tables", 1)
    report = raw["modules"]["m"]["report"]["$dataclass"]
    assert report["type"] == "metacheck.report.blocks:ReportTable"
    assert type(tables._load_module_tables(tmp_path, "p", None).report) is ReportTable


# --- the repo-info cache --------------------------------------------------------

#: an entry as 0.4.0a1's ``_repo_info_cache_put()`` wrote it
_REPO_INFO_0_4_0A1 = (
    b'{"format":"pytacheck.repo_info_cache","version":1,"value":{"$df":{"names":["name","size"],'
    b'"columns":[{"dtype":"str","data":["a.csv"]},{"dtype":"int64","data":[3]}],"nrow":1,'
    b'"index":null,"attrs":null}}}'
)


def test_repo_info_cache_entries_written_by_0_4_0a1_are_hits(tmp_path: Path) -> None:
    import pandas as pd

    from metacheck.archives.info_cache import _repo_info_cache_get, _repo_info_cache_path
    from metacheck.utils import local_options

    with local_options({"metacheck.repo_info_cache.dir": str(tmp_path / "info")}):
        Path(_repo_info_cache_path("osf", "old")).write_bytes(
            b'{"format": "pytacheck.repo_info_cache", "version": 1, "value": 1}'
        )
        assert _repo_info_cache_get("osf", "old") == 1
        Path(_repo_info_cache_path("osf", "listing")).write_bytes(_REPO_INFO_0_4_0A1)
        pd.testing.assert_frame_equal(
            _repo_info_cache_get("osf", "listing"),
            pd.DataFrame({"name": ["a.csv"], "size": [3]}),
        )


def test_repo_info_cache_entries_of_another_version_are_misses(tmp_path: Path) -> None:
    from metacheck.archives.info_cache import _repo_info_cache_get, _repo_info_cache_path
    from metacheck.utils import local_options

    with local_options({"metacheck.repo_info_cache.dir": str(tmp_path / "info")}):
        for fmt in ("pytacheck.repo_info_cache", "metacheck.repo_info_cache"):
            Path(_repo_info_cache_path("osf", "old")).write_bytes(
                json.dumps({"format": fmt, "version": 0, "value": 1}).encode()
            )
            assert _repo_info_cache_get("osf", "old") is None, fmt


def test_repo_info_cache_entries_are_written_under_the_new_id(tmp_path: Path) -> None:
    import pandas as pd

    from metacheck.archives.info_cache import (
        _repo_info_cache_get,
        _repo_info_cache_path,
        _repo_info_cache_put,
    )
    from metacheck.utils import local_options

    value = pd.DataFrame({"name": ["a.csv"], "size": [3]})
    with local_options({"metacheck.repo_info_cache.dir": str(tmp_path / "info")}):
        _repo_info_cache_put("osf", "new", value)
        raw = json.loads(Path(_repo_info_cache_path("osf", "new")).read_bytes())
        assert (raw["format"], raw["version"]) == ("metacheck.repo_info_cache", 1)
        pd.testing.assert_frame_equal(_repo_info_cache_get("osf", "new"), value)


# --- run records --------------------------------------------------------------

#: a run record exactly as 0.4.0a1 wrote it (``RunRecord.write`` after
#: ``run_modules(paper, ["marginal"])``)
_RUN_0_4_0A1 = b"""{
  "schema": "pytacheck.run/1",
  "created": "2026-09-30T12:10:56Z",
  "pytacheck": "0.4.0a1",
  "metacheck": {
    "version": "0.3.1",
    "commit": "b239264f6b"
  },
  "python": "3.12.14",
  "platform": "linux",
  "preset": null,
  "preset_source": null,
  "offline": false,
  "dropped": [],
  "papers": [
    "4a3f4986edd80c"
  ],
  "modules": [
    {
      "id": "metacheck::marginal",
      "name": "marginal",
      "pack": "metacheck",
      "version": "0.4.0a1",
      "trust": "builtin",
      "reviewed": null,
      "source": {
        "builtin": "pytacheck 0.4.0a1",
        "upstream": "0.3.1@b239264f6b"
      },
      "sha256": "7e59f50cc70de4a3235677ecc30bc2be0a0e8ca53c1cc499d3cb4a485262d30d",
      "modified": false,
      "args": {},
      "requires": [],
      "status": "ok"
    }
  ],
  "environment": {
    "bibr": "0.5.1"
  }
}
"""

#: the same record as 0.4.0a1's ``RunRecord.to_html()`` embedded it in a page
_RUN_0_4_0A1_HTML = (
    b"<html><body><p>report</p>"
    b'<script type="application/json" id="pytacheck-run">{"schema": "pytacheck.run/1", '
    b'"created": "2026-09-30T12:10:56Z", "pytacheck": "0.4.0a1", '
    b'"metacheck": {"version": "0.3.1", "commit": "b239264f6b"}, "python": "3.12.14", '
    b'"platform": "linux", "preset": null, "preset_source": null, "offline": false, '
    b'"dropped": [], "papers": ["4a3f4986edd80c"], '
    b'"modules": [{"id": "metacheck::marginal", "name": "marginal", "pack": "metacheck", '
    b'"version": "0.4.0a1", "trust": "builtin", "reviewed": null, '
    b'"source": {"builtin": "pytacheck 0.4.0a1", "upstream": "0.3.1@b239264f6b"}, '
    b'"sha256": "7e59f50cc70de4a3235677ecc30bc2be0a0e8ca53c1cc499d3cb4a485262d30d", '
    b'"modified": false, "args": {}, "requires": [], "status": "ok"}], '
    b'"environment": {"bibr": "0.5.1"}}</script>'
    b"</body></html>\n"
)


@pytest.mark.parametrize(
    ("name", "data"),
    [("run.json", _RUN_0_4_0A1), ("report.html", _RUN_0_4_0A1_HTML)],
    ids=["json", "html"],
)
def test_run_records_of_0_4_0a1_are_read_and_replayed(
    tmp_path: Path, paper, name: str, data: bytes
) -> None:
    from metacheck.provenance import RUN_SCHEMA, RunRecord, rerun

    path = tmp_path / name
    path.write_bytes(data)
    record = RunRecord.read(path)
    assert record.schema == RUN_SCHEMA
    assert record.version == "0.4.0a1"
    assert record.r_reference == {"version": "0.3.1", "commit": "b239264f6b"}
    assert [m["id"] for m in record.modules] == ["metacheck::marginal"]
    assert RunRecord.read(path.read_text(encoding="utf-8")) == record
    with pytest.warns(UserWarning, match="the record used 0.4.0a1"):
        chain = rerun(path, paper)
    assert [o.module for o in chain] == ["marginal"]
    assert chain.run_record is not None and chain.run_record.schema == RUN_SCHEMA


def test_run_records_of_0_4_0a1_are_upgraded(tmp_path: Path) -> None:
    from metacheck.provenance import RUN_SCHEMAS, RunRecord

    assert RUN_SCHEMAS == ("metacheck.run/2", "pytacheck.run/1")
    data = json.loads(_RUN_0_4_0A1)
    record = RunRecord.read(data)
    assert data["schema"] == "pytacheck.run/1" and "pytacheck" in data, "the input is not changed"
    d = record.to_dict()
    assert d["schema"] == "metacheck.run/2"
    assert list(d)[:5] == ["schema", "created", "version", "r_reference", "python"]
    assert "pytacheck" not in d and "metacheck" not in d
    assert (d["version"], d["r_reference"]) == (data["pytacheck"], data["metacheck"])
    # written again, it is a new record (never an old label over the new keys)
    written = json.loads(record.write(tmp_path / "again.json").read_text(encoding="utf-8"))
    assert written["schema"] == "metacheck.run/2" and "pytacheck" not in written
    assert RunRecord.read(tmp_path / "again.json") == record


def test_run_records_keep_the_old_field_names_read_only() -> None:
    from metacheck.provenance import RunRecord

    record = RunRecord.read(json.loads(_RUN_0_4_0A1))
    assert record.pytacheck == record.version == "0.4.0a1"
    assert record.metacheck == record.r_reference == {"version": "0.3.1", "commit": "b239264f6b"}
    with pytest.raises(AttributeError):
        record.pytacheck = "1.0"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        record.metacheck = {}  # type: ignore[misc]


def test_run_records_with_old_and_new_keys_take_the_new_ones() -> None:
    from metacheck.provenance import RunRecord

    data = json.loads(_RUN_0_4_0A1)
    both = {**data, "version": "9.9", "r_reference": {"version": "x", "commit": "y"}}
    record = RunRecord.read(both)
    assert (record.version, record.r_reference) == ("9.9", {"version": "x", "commit": "y"})
    # a new record's unknown keys are ignored, the old names included
    new = {**record.to_dict(), "pytacheck": "0.0.1", "metacheck": {}}
    assert RunRecord.read(new) == record


def test_run_records_never_write_the_old_schema_id(tmp_path: Path) -> None:
    """Whatever ``schema`` a record object holds, the new keys go out under the new id."""
    import dataclasses

    from metacheck.provenance import RunRecord

    record = dataclasses.replace(RunRecord.read(json.loads(_RUN_0_4_0A1)), schema="pytacheck.run/1")
    assert record.to_dict()["schema"] == "metacheck.run/2"
    assert json.loads(record.to_json())["schema"] == "metacheck.run/2"
    assert '"schema": "metacheck.run/2"' in record.to_html()
    written = json.loads(record.write(tmp_path / "run.json").read_text(encoding="utf-8"))
    assert written["schema"] == "metacheck.run/2" and "version" in written
    assert RunRecord.read(tmp_path / "run.json").version == "0.4.0a1"


@pytest.mark.parametrize("schema", ["pytacheck.run/2", "metacheck.run/1", "other", None])
def test_run_records_of_another_schema_are_refused(schema: str | None) -> None:
    from metacheck.module import ModuleError
    from metacheck.provenance import RunRecord

    data = {**json.loads(_RUN_0_4_0A1), "schema": schema}
    with pytest.raises(
        ModuleError,
        match=r"^Not a metacheck run record \(schema metacheck\.run/2 or pytacheck\.run/1\)$",
    ):
        RunRecord.read(data)
    del data["schema"]
    with pytest.raises(ModuleError, match="Not a metacheck run record"):
        RunRecord.read(data)


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


# --- the option keys ----------------------------------------------------------

OPTION_PAIRS = [
    ("pytacheck.careless", "metacheck.careless"),
    ("pytacheck.r_serialize_version", "metacheck.r_serialize_version"),
    ("pytacheck.llm.workers", "metacheck.llm.workers"),
]
OPTION_IDS = [new for _, new in OPTION_PAIRS]


@pytest.fixture
def clean_options():
    """Leave the option store as found, whatever a test sets."""
    names = [n for pair in OPTION_PAIRS for n in pair]
    with utils._options_lock:
        saved = {n: utils._options[n] for n in names if n in utils._options}
        for n in names:
            utils._options.pop(n, None)
    yield
    with utils._options_lock:
        for n in names:
            utils._options.pop(n, None)
        utils._options.update(saved)


def test_the_option_aliases_are_exactly_the_three_python_only_options() -> None:
    assert dict(OPTION_PAIRS) == utils._OPTION_ALIASES
    assert utils._canon("metacheck.osf.api") == "metacheck.osf.api"


@pytest.mark.parametrize(("old", "new"), OPTION_PAIRS, ids=OPTION_IDS)
def test_an_old_option_spelling_sets_and_gets_the_new_option(old, new, clean_options) -> None:
    utils.options({old: "x"})
    assert utils.get_option(old) == "x"
    assert utils.get_option(new) == "x"
    assert utils._options[new] == "x"
    assert old not in utils._options  # one stored name, never both
    utils.options({new: "y"})
    assert utils.get_option(old) == "y"
    assert utils.get_option(new) == "y"
    utils.options({old: None})  # NULL through the old spelling removes it
    assert utils.get_option(new) is None
    assert utils.get_option(old) is None


@pytest.mark.parametrize(("old", "new"), OPTION_PAIRS, ids=OPTION_IDS)
def test_an_option_default_reaches_both_spellings(old, new, clean_options) -> None:
    assert utils.get_option(old, "dflt") == "dflt"
    assert utils.get_option(new, "dflt") == "dflt"
    utils.options({old: False})  # a falsy value is still a value, not the default
    assert utils.get_option(new, "dflt") is False
    assert utils.get_option(old, "dflt") is False


@pytest.mark.parametrize(("old", "new"), OPTION_PAIRS, ids=OPTION_IDS)
def test_options_returns_the_old_values_keyed_as_spelled(old, new, clean_options) -> None:
    assert utils.options({old: 1}) == {old: None}
    assert utils.options({new: 2}) == {new: 1}  # set as old, asked as new
    assert utils.options({old: 3}) == {old: 2}  # set as new, asked as old
    assert utils.options({old: None}) == {old: 3}
    assert utils.options({new: 4}) == {new: None}


def test_options_with_both_spellings_in_one_call_reports_the_values_before_it(
    clean_options,
) -> None:
    utils.options({"metacheck.careless": "a"})
    old = utils.options({"pytacheck.careless": "b", "metacheck.careless": "c"})
    assert old == {"pytacheck.careless": "a", "metacheck.careless": "a"}
    assert utils.get_option("metacheck.careless") == "c"


@pytest.mark.parametrize(("old", "new"), OPTION_PAIRS, ids=OPTION_IDS)
@pytest.mark.parametrize(("setter", "restorer"), [(0, 0), (0, 1), (1, 0), (1, 1)])
def test_local_options_restores_under_either_spelling(
    old, new, setter, restorer, clean_options
) -> None:
    """Set with one spelling inside, and have it restored by the same or the other:
    an option that was unset goes back to unset, and a value comes back."""
    spell = (old, new)
    for before in (None, "kept"):
        if before is not None:
            utils.options({spell[restorer]: before})
        with utils.local_options({spell[setter]: "inside"}):
            assert utils.get_option(old) == "inside"
            assert utils.get_option(new) == "inside"
        assert utils.get_option(old) == before
        assert utils.get_option(new) == before
        assert old not in utils._options
        utils.options({new: None})


@pytest.mark.parametrize(("old", "new"), OPTION_PAIRS, ids=OPTION_IDS)
def test_local_options_nests_across_spellings(old, new, clean_options) -> None:
    with utils.local_options({old: 1}):
        with utils.local_options({new: 2}):
            assert utils.get_option(old) == 2
        assert utils.get_option(new) == 1
    assert utils.get_option(old) is None


def test_local_options_restores_when_the_body_raises(clean_options) -> None:
    utils.options({"metacheck.careless": True})
    with pytest.raises(RuntimeError), utils.local_options({"pytacheck.careless": False}):
        assert utils.get_option("metacheck.careless") is False
        raise RuntimeError
    assert utils.get_option("pytacheck.careless") is True


def test_the_readers_see_an_option_set_under_the_old_spelling(clean_options, monkeypatch) -> None:
    """The three readers ask for the new names; the old spelling reaches them."""
    from metacheck._env import env_names
    from metacheck.llm import _rds
    from metacheck.llm.core import _llm_workers
    from metacheck.modules._data_check import _careless_available

    assert _careless_available() is True
    with utils.local_options({"pytacheck.careless": False}):
        assert _careless_available() is False
    with utils.local_options({"metacheck.careless": False}):
        assert _careless_available() is False

    with utils.local_options({"pytacheck.llm.workers": 4}):
        assert _llm_workers() == 4
    with utils.local_options({"metacheck.llm.workers": 3}):
        assert _llm_workers() == 3

    for name in env_names("R_SERIALIZE_VERSION"):
        monkeypatch.delenv(name, raising=False)
    v430 = 4 * 65536 + 3 * 256
    assert _rds._r_version_int() != v430
    with utils.local_options({"pytacheck.r_serialize_version": "4.3.0"}):
        assert _rds._r_version_int() == v430
    with utils.local_options({"metacheck.r_serialize_version": "4.3.0"}):
        assert _rds._r_version_int() == v430


# --- the project file -------------------------------------------------------------


def test_a_pytacheck_json_alone_is_found_and_edited_in_place(tmp_path: Path, monkeypatch) -> None:
    """A committed pytacheck.json keeps working, and is never renamed (teammates on 0.4.0a1)."""
    from metacheck import config
    from metacheck._env import env_names

    root = tmp_path / "home"
    project = root / "proj"
    cwd = project / "sub"
    cwd.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setenv("USERPROFILE", str(root))
    monkeypatch.setattr(config, "user_config_path", lambda: tmp_path / "no-user.json")
    for name in env_names("CONFIG"):
        monkeypatch.delenv(name, raising=False)
    old = project / "pytacheck.json"
    old.write_text(json.dumps({"preset": "a", "packs": {"x": False}}))
    for where in (project, cwd):
        monkeypatch.chdir(where)
        assert config.project_config_path() == old
        assert config.config_files() == [("project", old)]
        assert config.load_config().preset == "a"
        assert config.load_config().source("preset") == ("project", str(old))
        assert config.config_path("project") == old
    path = config.update_config("project", lambda c: c.update(preset="b"))
    assert path == old
    assert json.loads(old.read_text()) == {"preset": "b", "packs": {"x": False}}
    assert config.load_config().preset == "b"
    assert sorted(p.name for p in project.iterdir()) == ["pytacheck.json", "sub"]
    assert list(cwd.iterdir()) == []


# --- the command --------------------------------------------------------------


@pytest.mark.parametrize("name", ["pytacheck", "metacheck"])
def test_python_m_help_names_pytacheck_for_both_commands(name: str) -> None:
    """Help text is unchanged by the rename (RENAME-4 decides it)."""
    out = subprocess.run(
        [sys.executable, "-m", name, "--help"], capture_output=True, text=True, check=True
    ).stdout
    assert out.startswith("usage: pytacheck")
