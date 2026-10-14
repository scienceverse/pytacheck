"""The R engine: R metacheck's checks in an Rscript subprocess, decoded into ModuleOutputs.

Most tests use the stand-in Rscript of conftest.py; those marked ``r`` run R metacheck itself
and are skipped where R cannot run it.
"""

from __future__ import annotations

import math
import os
import time
from pathlib import Path

import pandas as pd
import pytest

import metacheck as pc
from metacheck import rengine
from metacheck.module import ModuleOutput
from metacheck.rengine._decode import decode, module_output
from metacheck.report.blocks import ReportTable

ROOT = Path(__file__).resolve().parents[2]


def chr_(*v: object) -> dict:
    return {"t": "chr", "v": list(v)}


# -- the encoder R uses is the parity tooling's -------------------------------------


def test_the_shipped_encoder_is_the_parity_encoder() -> None:
    shipped = (ROOT / "src" / "metacheck" / "rengine" / "canonical.R").read_bytes()
    assert shipped == (ROOT / "parity" / "r" / "canonical.R").read_bytes()


# -- decoding ------------------------------------------------------------------------


def test_vectors() -> None:
    assert decode({"t": "null"}) is None
    assert decode(chr_("a")) == "a"  # R has no scalars: a vector of one is its value
    assert decode(chr_("a", None)) == ["a", None]
    assert decode({"t": "int", "v": [1, 2], "names": ["x", "y"]}) == {"x": 1, "y": 2}
    nan, inf, minus, missing = decode({"t": "dbl", "v": ["NaN", "Inf", "-Inf", None]})
    assert math.isnan(nan) and inf == math.inf and minus == -math.inf and missing is None
    assert decode({"t": "lgl", "v": [True, None]}) == [True, None]
    assert decode({"t": "list", "names": None, "v": [chr_("a"), {"t": "null"}]}) == ["a", None]


def test_frames_keep_r_types() -> None:
    df = decode(
        {
            "t": "df",
            "nrow": 2,
            "names": ["s", "n", "i", "j", "b", "l"],
            "v": [
                chr_("a", None),
                {"t": "dbl", "v": [1.5, None]},
                {"t": "int", "v": [1, None]},
                {"t": "int", "v": [1, 2]},
                {"t": "lgl", "v": [True, None]},
                {"t": "list", "names": None, "v": [chr_("x", "y"), {"t": "null"}]},
            ],
        }
    )
    assert list(df.columns) == ["s", "n", "i", "j", "b", "l"]
    assert df["s"].tolist() == ["a", None]
    assert df["n"].dtype == "float64" and math.isnan(df["n"][1])
    assert str(df["i"].dtype) == "Int64" and df["i"][1] is pd.NA
    assert df["j"].dtype == "int64"  # whole numbers stay whole numbers in the report
    assert str(df["b"].dtype) == "boolean"
    assert df["l"].tolist() == [["x", "y"], None]
    empty = decode({"t": "df", "nrow": 0, "names": [], "v": []})
    assert empty.shape == (0, 0)


def test_module_output_with_a_table_in_its_report() -> None:
    table = decode({"t": "df", "nrow": 1, "names": ["x"], "v": [chr_("cell")]})
    out = module_output(
        {
            "t": "module_output",
            "names": [
                "module",
                "title",
                "section",
                "report",
                "traffic_light",
                "summary_text",
                "summary_table",
                "na_replace",
                "paper",
            ],
            "v": [
                chr_("marginal"),
                chr_("Marginal"),
                chr_("results"),
                chr_("Intro\n<!-- pytacheck-r-table 1 -->\n", "More text"),
                chr_("red"),
                chr_("2 found"),
                {"t": "df", "nrow": 1, "names": ["paper_id"], "v": [chr_("p")]},
                {"t": "dbl", "v": [0]},
                {"t": "paper", "names": [], "v": []},
            ],
        },
        [{"table": table, "colwidths": "auto", "maxrows": 2, "escape": False, "column": "body"}],
    )
    assert isinstance(out, ModuleOutput)
    assert (out.module, out.title, out.section, out.traffic_light) == (
        "marginal",
        "Marginal",
        "results",
        "red",
    )
    intro, block, more = out.report
    assert intro == "Intro" and more == "More text"
    assert isinstance(block, ReportTable) and block.data["x"].tolist() == ["cell"]
    assert out.summary_table["paper_id"].tolist() == ["p"]
    assert out.extras == {"na_replace": 0.0}  # the paper is not an element


def test_a_failed_module_has_no_section() -> None:
    out = module_output(
        {
            "t": "module_output",
            "names": ["module", "title", "report", "summary_text", "traffic_light"],
            "v": [
                chr_("nope"),
                chr_("nope"),
                chr_("no such module"),
                chr_("This module failed to run"),
                chr_("fail"),
            ],
        }
    )
    assert out.section is None and out.traffic_light == "fail" and out.table is None


# -- running Rscript -----------------------------------------------------------------


def test_info(fake_r: Path) -> None:
    found = rengine.info()
    assert found.rscript == str(fake_r)
    assert (found.metacheck_version, found.r_version) == ("9.9.9", "4.5.3")
    assert found.modules == ("marginal", "stat_check", "data_check")
    assert rengine.available()


def test_no_r(fake_r: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_R_MODE", "crash")
    with pytest.raises(rengine.REngineUnavailable, match="no package called 'metacheck'"):
        rengine.info()
    monkeypatch.setenv("FAKE_R_MODE", "ok")
    assert not rengine.available()  # the answer is kept ...
    assert rengine.info(refresh=True).metacheck_version == "9.9.9"  # ... until asked again
    missing = str(fake_r.parent / "nowhere" / "Rscript")
    with pytest.raises(rengine.REngineUnavailable, match="could not be started"):
        rengine.info(missing)


def test_run(fake_r: Path) -> None:
    r_run = rengine.run(pc.demofile("json"), ["marginal", "stat_check"])
    assert list(r_run.outputs) == ["marginal", "stat_check"]
    out = r_run.outputs["stat_check"]
    assert out.summary_text == "stat_check ran on to_err_is_human.json"  # R reads the file
    assert out.table["text"].tolist() == ["a", None]
    assert [type(b) for b in out.report] == [str, ReportTable, str]
    assert r_run.metacheck_version == "9.9.9" and r_run.r_version == "4.5.3"
    assert "9.9.9" in rengine.report_version(r_run.metacheck_version, r_run.r_version)


def test_run_errors(fake_r: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_R_MODE", "error")
    with pytest.raises(rengine.REngineError, match="cannot open file"):
        rengine.run(pc.demofile("json"), ["marginal"])
    monkeypatch.setenv("FAKE_R_MODE", "crash")
    with pytest.raises(rengine.REngineError, match=r"exit status 1\).*no package called"):
        rengine.run(pc.demofile("json"), ["marginal"])


def _gone(pidfile: Path) -> bool:
    pid = int(pidfile.read_text())
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    # a zombie is gone too: it was reaped by nobody only because it is not our child
    stat = Path(f"/proc/{pid}/stat")
    return stat.exists() and stat.read_text().split()[2] == "Z"


def test_a_run_ends_at_its_time_limit(fake_r: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_R_MODE", "hang")
    start = time.monotonic()
    with pytest.raises(rengine.REngineTimeout):
        rengine.run(pc.demofile("json"), ["marginal"], timeout=1)
    assert time.monotonic() - start < 10
    assert _gone(fake_r.parent / "pid")


def test_a_run_ends_when_stopped(fake_r: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_R_MODE", "hang")
    pidfile = fake_r.parent / "pid"
    with pytest.raises(rengine.REngineStopped):
        rengine.run(pc.demofile("json"), ["marginal"], stop=pidfile.exists)
    assert _gone(pidfile)


def test_paper_file(tmp_path: Path) -> None:
    path = rengine.paper_file(pc.demopaper(), tmp_path)
    assert path.name == "paper.json"
    assert pc.read(path).info["title"].tolist() == pc.demopaper().info["title"].tolist()


# -- R metacheck itself --------------------------------------------------------------


@pytest.mark.r
def test_r_metacheck_on_the_demo_paper() -> None:
    if not rengine.available():
        pytest.skip("R with the metacheck R package is not available")
    from metacheck.report.report import render_module_outputs

    r_run = rengine.run(pc.demofile("json"), ["stat_check", "marginal", "no_such_module"])
    assert list(r_run.outputs)[-1] == "no_such_module"
    assert r_run.outputs["no_such_module"].traffic_light == "fail"
    marginal = r_run.outputs["marginal"]
    assert marginal.traffic_light == "red" and marginal.section == "results"
    assert any(isinstance(b, ReportTable) for b in marginal.report)
    page = render_module_outputs(r_run.outputs, pc.demopaper(), version="R test")
    assert "```{r}" not in page and "pytacheck-r-table" not in page
    assert "R test" in page


@pytest.mark.r
def test_r_reads_the_papers_pytacheck_writes(tmp_path: Path) -> None:
    if not rengine.available():
        pytest.skip("R with the metacheck R package is not available")
    r_run = rengine.run(rengine.paper_file(pc.demopaper(), tmp_path), ["marginal"])
    assert r_run.outputs["marginal"].traffic_light == "red"
