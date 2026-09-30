"""Saving and reloading module outputs (capture_module_tables() and friends, R/module.R)."""

from __future__ import annotations

import datetime as dt
import json
import shutil
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import metacheck as pc
from metacheck.module import ModuleOutput, get_prev_outputs, module, module_run
from metacheck.repro import tables
from metacheck.repro.tables import capture_module_tables, collect_module_tables


@pytest.fixture(scope="module")
def chain():
    return pc.report_module_run(pc.demopaper(), ["marginal", "all_p_values"])


def _output(name: str, **kw) -> ModuleOutput:
    base = {"title": name, "section": "general"}
    base.update(kw)
    return ModuleOutput(module=name, **base)


# the JSON encoding ------------------------------------------------------------------


def test_encoding_round_trips_frames() -> None:
    df = pd.DataFrame(
        {
            "s": pd.Series(["a", None, "é"], dtype="string"),
            "i": pd.Series([1, None, 3], dtype="Int64"),
            "f": [1.5, float("nan"), float("inf")],
            "b": pd.Series([True, None, False], dtype="boolean"),
            "l": pd.Series([["x", "y"], [], None], dtype=object),
            "c": pd.Categorical(["lo", "hi", "lo"], categories=["lo", "hi"], ordered=True),
            "t": pd.to_datetime(["2024-01-01 00:00", None, "2024-03-01 10:00"]),
            "o": pd.Series([{"k": 1}, (1, 2), np.int64(5)], dtype=object),
            "n": np.array([1, 2, 3], dtype="int64"),
        }
    )
    df.index = [10, 11, 12]
    df.attrs["cycle"] = ["a.R"]
    back = tables._decode(json.loads(json.dumps(tables._encode(df), allow_nan=False)))
    pd.testing.assert_frame_equal(back, df, check_index_type=False)
    assert back.attrs == {"cycle": ["a.R"]}
    assert back["l"].iloc[0] == ["x", "y"]
    assert back["o"].iloc[1] == (1, 2)


def test_encoding_round_trips_values() -> None:
    from metacheck.report.blocks import ReportTable

    value = {
        "nested": {1: "int key", "$x": [1, 2.5, None, pd.NA]},
        "when": dt.date(2024, 1, 2),
        "set": {3},
        "arr": np.arange(6).reshape(2, 3),
        "series": pd.Series([1.0, 2.0], name="v"),
        "block": ReportTable(pd.DataFrame({"a": [1]}), maxrows=5),
        "nan": float("nan"),
    }
    back = tables._decode(json.loads(json.dumps(tables._encode(value), allow_nan=False)))
    assert back["nested"][1] == "int key"
    assert back["nested"]["$x"][:3] == [1, 2.5, None] and back["nested"]["$x"][3] is pd.NA
    assert back["when"] == dt.date(2024, 1, 2)
    assert back["set"] == {3}
    np.testing.assert_array_equal(back["arr"], value["arr"])
    pd.testing.assert_series_equal(back["series"], value["series"])
    assert isinstance(back["block"], ReportTable) and back["block"].maxrows == 5
    assert np.isnan(back["nan"])


def test_unknown_objects_are_kept_as_repr() -> None:
    class Thing:
        def __repr__(self) -> str:
            return "Thing()"

    with pytest.warns(UserWarning, match="cannot be saved"):
        enc = tables._encode(Thing())
    assert tables._decode(enc) == "Thing()"


# capture_module_tables() -------------------------------------------------------------------


def test_capture_writes_json_not_pickle(chain, tmp_path: Path) -> None:
    path = capture_module_tables(chain, tmp_path)
    assert Path(path) == tmp_path / "to_err_is_human.json"  # joined with "/", as R does
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    assert raw["format"] == "metacheck.module_tables"
    assert raw["paper_id"] == "to_err_is_human"
    assert list(raw["modules"]) == ["marginal", "all_p_values"]
    assert set(raw["modules"]["all_p_values"]) >= {
        "table",
        "report",
        "traffic_light",
        "summary_text",
    }
    for plumbing in ("paper", "prev_outputs", "module", "title", "section"):
        assert plumbing not in raw["modules"]["marginal"]


def test_capture_paper_id_argument_and_fallback(tmp_path: Path) -> None:
    mo = _output("m1", table=pd.DataFrame({"x": [1]}), summary_table=None)
    assert capture_module_tables([mo], tmp_path, paper_id="given").endswith("given.json")
    assert capture_module_tables([mo], tmp_path).endswith("paper.json")


def test_capture_names_and_exclusions(tmp_path: Path) -> None:
    mods = [
        _output("dup", extras={"previews": {"a": 1}, "structure": pd.DataFrame({"f": ["a"]})}),
        _output("dup"),
        _output(""),
    ]
    path = capture_module_tables(mods, tmp_path, paper_id="p")
    raw = json.loads(Path(path).read_text())
    assert list(raw["modules"]) == ["dup", "module_2", "module_3"]
    assert "previews" not in raw["modules"]["dup"]
    assert "structure" in raw["modules"]["dup"]


def test_capture_single_output_stands_for_its_chain(chain, tmp_path: Path) -> None:
    out = module_run(module_run(pc.demopaper(), "marginal"), "all_p_values")
    raw = json.loads(Path(capture_module_tables(out, tmp_path)).read_text())
    assert list(raw["modules"]) == ["marginal", "all_p_values"]


def test_capture_keeps_widest_summary(tmp_path: Path) -> None:
    narrow = pd.DataFrame({"paper_id": ["p"], "a": [1]})
    wide = pd.DataFrame({"paper_id": ["p"], "a": [1], "b": [2]})
    mods = [_output("m1", summary_table=narrow), _output("m2", summary_table=wide), _output("m3")]
    path = capture_module_tables(mods, tmp_path)
    saved = tables._read_saved(path)
    assert saved is not None and saved["paper_id"] == "p"
    assert saved["summary_table"].columns.tolist() == ["paper_id", "a", "b"]


# collect_module_tables() ------------------------------------------------------------------------


def test_collect_across_json_and_rds(chain, area_fixtures: Path, tmp_path: Path) -> None:
    for f in (area_fixtures / "tables_rds").glob("*.rds"):
        shutil.copy(f, tmp_path / f.name.replace("to_err_is_human", "rds_copy"))
    capture_module_tables(chain, tmp_path)
    out = collect_module_tables(tmp_path, "all_p_values")
    assert out.columns[0] == "paper_id"
    # files in R's sort order: 0956797613520608.rds, rds_copy.rds, to_err_is_human.json
    assert out["paper_id"].drop_duplicates().tolist() == ["0956797613520608", "to_err_is_human"]
    by_file = out.groupby("paper_id", sort=False).size().to_dict()
    assert by_file["0956797613520608"] == 6
    assert by_file["to_err_is_human"] == 6  # the .rds copy and the .json capture


def test_collect_summary_table(area_fixtures: Path) -> None:
    out = collect_module_tables(area_fixtures / "tables_rds", "all_p_values", "summary_table")
    assert out["paper_id"].tolist() == ["0956797613520608", "to_err_is_human"]
    assert out["p_values"].tolist() == [6, 3]


def test_collect_warns_when_nothing(tmp_path: Path, area_fixtures: Path) -> None:
    with pytest.warns(UserWarning, match="No \\*.json or \\*.rds files found"):
        assert collect_module_tables(tmp_path, "marginal").shape == (0, 0)
    with pytest.warns(UserWarning, match='none had a "nope" module'):
        assert collect_module_tables(area_fixtures / "tables_rds", "nope").shape == (0, 0)


def test_collect_adds_paper_id(tmp_path: Path) -> None:
    capture_module_tables(
        [_output("m", table=pd.DataFrame({"x": [1, 2]}))], tmp_path, paper_id="pp"
    )
    out = collect_module_tables(tmp_path, "m")
    assert out.columns.tolist() == ["paper_id", "x"]
    assert out["paper_id"].tolist() == ["pp", "pp"]


def test_collect_ignores_foreign_json(tmp_path: Path) -> None:
    (tmp_path / "other.json").write_text('{"modules": {"m": {}}}')
    (tmp_path / "broken.json").write_text("{not json")
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        assert collect_module_tables(tmp_path, "m").shape == (0, 0)
    assert "2 *.json/*.rds file(s)" in str(w[0].message)


# _load_module_tables() ---------------------------------------------------------------------------


def test_load_round_trip_feeds_module_run(chain, tmp_path: Path) -> None:
    capture_module_tables(chain, tmp_path)
    paper = pc.demopaper()
    loaded = tables._load_module_tables(tmp_path, "to_err_is_human", paper)
    assert isinstance(loaded, ModuleOutput)
    assert loaded.module == "all_p_values" and loaded.title == "all_p_values"
    assert loaded.section == "general"
    assert list(loaded.prev_outputs) == ["marginal", "all_p_values"]
    assert loaded.paper is paper
    assert loaded.summary_table.columns.tolist() == ["paper_id", "marginal", "p_values"]
    pd.testing.assert_frame_equal(
        loaded.prev_outputs["all_p_values"].table.reset_index(drop=True),
        chain["all_p_values"].table.reset_index(drop=True),
    )

    @module(title="Probe", keywords=["general"])
    def probe(paper):
        t = get_prev_outputs("all_p_values", "table")
        return {"table": t, "summary_text": f"{len(t)} p-values"}

    out = module_run(loaded, probe)
    assert out.summary_text == "3 p-values"
    assert list(out.prev_outputs) == ["marginal", "all_p_values"]


def test_load_reads_metacheck_rds(area_fixtures: Path) -> None:
    loaded = tables._load_module_tables(area_fixtures / "tables_rds", "0956797613520608", None)
    assert loaded.module == "all_p_values"
    assert loaded.traffic_light == "info"
    assert loaded.extras == {"na_replace": 0.0}
    assert len(loaded.prev_outputs["all_p_values"].table) == 6
    assert loaded.prev_outputs["marginal"].traffic_light == "green"


def test_load_missing(tmp_path: Path) -> None:
    assert tables._load_module_tables(tmp_path, "nope", None) is None
    (tmp_path / "empty.json").write_text(
        json.dumps({"format": "metacheck.module_tables", "paper_id": "empty", "modules": {}})
    )
    assert tables._load_module_tables(tmp_path, "empty", None) is None
