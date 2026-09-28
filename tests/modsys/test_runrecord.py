"""Run records (pytacheck.run/1), run_modules() failure handling, and rerun()."""

from __future__ import annotations

import json
import sys

import pytest
import respx

from pytacheck._version import UPSTREAM, __version__
from pytacheck.module import ModuleError, ModuleOutput
from pytacheck.packs.install import pack_remove
from pytacheck.presets import select
from pytacheck.provenance import RUN_SCHEMA, ModuleChain, RunRecord, rerun, run_modules
from tests.modsys.helpers import REV_A, mod_src
from tests.modsys.storekit import codeload, dir_files, tarball


@pytest.fixture
def lab(ms):
    mods = {
        "first": mod_src("first", "one", args="x=1"),
        "boom": mod_src("boom", body='raise ValueError("kaboom")', args=""),
        "last": mod_src(
            "last",
            body='return {"summary_text": str(get_prev_outputs("first", "summary_text"))}',
            args="",
        ),
        "online": mod_src("online", requires=("network",)),
    }
    folder = ms.pack(
        ms.root / "lab",
        "lab",
        mods,
        presets={"chain": {"modules": ["first", "boom", "last", "online"],
                           "args": {"first": {"x": 5}}}},
    )  # fmt: skip
    ms.pin("lab", {"path": str(folder)})
    return folder


def test_failure_becomes_a_fail_output_and_the_chain_goes_on(lab, paper) -> None:
    with pytest.warns(UserWarning, match="Error in boom"):
        chain = run_modules(paper, select(preset="lab::chain"))
    assert isinstance(chain, ModuleChain)
    assert [o.module for o in chain] == ["first", "boom", "last", "online"]
    fail = chain[1]
    assert isinstance(fail, ModuleOutput)
    assert (fail.module, fail.title, fail.section, fail.table) == ("boom", "boom", None, None)
    assert fail.traffic_light == "fail"
    assert fail.summary_text == "This module failed to run"
    assert "kaboom" in fail.report
    assert fail.paper is paper
    assert list(fail.prev_outputs) == ["first"]
    assert fail.summary_table is not None and fail.summary_table["paper_id"].tolist() == [
        paper.paper_id
    ]
    # the next module still sees earlier outputs, including the failed one
    assert chain[2].summary_text == "one"
    assert list(chain[2].prev_outputs) == ["first", "boom"]
    assert chain.last is chain[-1] and chain.summary_table is chain[-1].summary_table
    assert list(chain.outputs()) == ["first", "boom", "last", "online"]


def test_run_record_contents(lab, paper) -> None:
    sel = select(preset="lab::chain", offline=True)
    with pytest.warns(UserWarning):
        record = run_modules(paper, sel).run_record
    d = record.to_dict()
    assert list(d) == [
        "schema", "created", "pytacheck", "metacheck", "python", "platform", "preset",
        "preset_source", "offline", "dropped", "papers", "modules", "environment",
    ]  # fmt: skip
    assert d["schema"] == RUN_SCHEMA and d["pytacheck"] == __version__
    assert d["metacheck"] == {"version": UPSTREAM["version"], "commit": UPSTREAM["commit"][:10]}
    assert d["platform"] == sys.platform
    assert (d["preset"], d["preset_source"], d["offline"]) == ("lab::chain", "argument", True)
    assert d["dropped"] == ["lab::online"]
    assert d["papers"] == [paper.paper_id]
    assert "bibr" in d["environment"]
    first, boom, last = d["modules"]
    assert (first["id"], first["status"], first["args"]) == ("lab::first", "ok", {"x": 5})
    assert first["trust"] == "local" and first["sha256"]
    assert (boom["id"], boom["status"]) == ("lab::boom", "fail")
    assert "kaboom" in boom["error"]
    assert last["status"] == "ok"


def test_round_trips(lab, paper, tmp_path) -> None:
    with pytest.warns(UserWarning):
        record = run_modules(paper, select(preset="lab::chain")).run_record
    path = record.write(tmp_path / "run.json")
    assert json.loads(path.read_text())["schema"] == RUN_SCHEMA
    assert RunRecord.read(path) == record
    assert RunRecord.read(path.read_text()) == record
    assert RunRecord.read(record.to_dict()) == record
    html = f"<html><body><p>report</p>{record.to_html()}</body></html>"
    assert '<script type="application/json" id="pytacheck-run">' in html
    assert RunRecord.from_html(html) == record
    (tmp_path / "report.html").write_text(html)
    assert RunRecord.read(tmp_path / "report.html") == record
    with pytest.raises(ModuleError, match="Not a pytacheck run record"):
        RunRecord.read({"schema": "other"})
    with pytest.raises(ModuleError, match="no embedded"):
        RunRecord.from_html("<html></html>")


def test_record_escapes_script_endings(paper) -> None:
    record = RunRecord.build([], papers=paper)
    record.modules.append({"id": "x", "args": {"s": "</script><b>"}})
    assert "</script><b>" not in record.to_html()
    assert RunRecord.from_html(record.to_html()).modules[0]["args"]["s"] == "</script><b>"


def test_builtins_and_reports_without_provenance(paper) -> None:
    chain = run_modules(paper, ["marginal"])
    (entry,) = chain.run_record.modules
    assert (entry["id"], entry["pack"], entry["trust"], entry["status"]) == (
        "metacheck::marginal", "metacheck", "builtin", "ok",
    )  # fmt: skip
    # outputs made elsewhere (e.g. a report's failed module, which has no provenance)
    failed = ModuleOutput(
        "x", "x", None, traffic_light="fail", summary_text="This module failed to run"
    )  # type: ignore[arg-type]
    rec = RunRecord.build({"x": failed})
    assert rec.modules == [{"id": "x", "name": "x", "status": "fail", "error": ""}]


def test_rerun_replays_the_same_modules_and_args(lab, paper) -> None:
    with pytest.warns(UserWarning):
        first = run_modules(paper, select(preset="lab::chain", offline=True))
    with pytest.warns(UserWarning, match="Error in boom"):
        again = rerun(first.run_record, paper)
    assert [o.module for o in again] == ["first", "boom", "last"]
    assert again[0].extras["x"] == 5
    assert again.run_record.dropped == ["lab::online"]
    assert again.run_record.preset == "lab::chain"


def test_rerun_refuses_modified_local_code(lab, paper) -> None:
    chain = run_modules(paper, ["lab::first"])
    (lab / "first.py").write_text(mod_src("first", "edited", args="x=1"))
    with pytest.raises(ModuleError, match="differs from the recorded one"):
        rerun(chain.run_record, paper)
    with pytest.warns(UserWarning, match="differs"):
        assert rerun(chain.run_record, paper, allow_modified=True)[0].summary_text == "edited"


def test_rerun_refuses_a_modified_installed_pack(ms, paper) -> None:
    ms.install("inst", {"mod": mod_src("mod", "installed")})
    record = run_modules(paper, ["inst::mod"]).run_record
    assert record.modules[0]["trust"] == "store"
    assert rerun(record, paper)[0].summary_text == "installed"
    target = ms.data / "packs" / "inst" / REV_A[:12] / "_helper.py"
    target.write_text("x = 1\n")  # an unrecorded file: the pack was modified
    with pytest.raises(ModuleError, match="was modified"):
        rerun(record, paper)
    with pytest.warns(UserWarning, match="modified"):
        assert rerun(record, paper, allow_modified=True)[0].summary_text == "installed"


def test_rerun_can_install_the_recorded_revision(ms, paper) -> None:
    rev = "e" * 40
    ms.install("inst", {"mod": mod_src("mod", "recorded")}, rev=rev, store=None)
    record = run_modules(paper, ["inst::mod"]).run_record
    source = ms.data / "packs" / "inst" / rev[:12]
    files = dir_files(source)
    pack_remove("inst")  # unpinned and deleted
    assert not source.exists()
    record.modules[0]["source"] = {"github": "someone/inst", "rev": rev}
    with pytest.raises(ModuleError, match="not installed"):
        rerun(record, paper)
    with respx.mock() as router:
        router.get(codeload("someone/inst", rev)).respond(content=tarball(files))
        out = rerun(record, paper, install=True, yes=True)
    assert out[0].summary_text == "recorded"
    assert source.is_dir()
    assert "inst" not in json.loads(ms.config_file.read_text()).get("packs", {}), "not pinned"


def test_rerun_warns_about_another_pytacheck(paper) -> None:
    record = run_modules(paper, ["marginal"]).run_record
    record.modules[0]["version"] = "0.0.1"
    with pytest.warns(UserWarning, match="the record used 0.0.1"):
        rerun(record, paper)


def test_run_modules_accepts_simple_inputs(paper) -> None:
    assert [o.module for o in run_modules(paper, "marginal")] == ["marginal"]
    assert [o.module for o in run_modules(paper, [("marginal", {})])] == ["marginal"]
