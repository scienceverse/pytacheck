"""Tests for the psychds_check module (port of ``inst/modules/psychds_check.R``).

metacheck has no testthat file for this module; these tests check the port
against the R goldens more strictly than the prose comparison of the parity
harness (report blocks exactly, the checklist table, the target tree), plus
behaviour that parity cases cannot reach: the unchained path that runs
``data_check`` itself, bibr export schema 12.x papers against their legacy
reading, input immutability, and (once ``data_check`` is ported) small
Psych-DS repositories on disk.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import pytacheck as pc
from pytacheck.module import ModuleOutput, module_find, module_run
from pytacheck.modules import psychds_check as mod
from pytacheck.report.blocks import ReportTable
from pytacheck.utils import local_options
from tests.mod_psychds import parity_support as ps

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "parity" / "golden" / "mod_psychds"
AREAS = ("mod_psychds", "mod_psychds_review")
REPOS = Path(__file__).resolve().parent / "fixtures" / "repos"


def _golden(case_id: str, area: str = "mod_psychds") -> dict[str, Any]:
    path = GOLDEN.parent / area / f"{case_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _element(value: dict[str, Any], name: str) -> Any:
    return value["v"][value["names"].index(name)]


def _module_cases() -> list[tuple[str, str]]:
    """``(area, case id)`` of every module-output golden of both case files.

    Cases marked as a known divergence (pytacheck fixes the R behaviour the
    golden records, parity/divergences/data.yaml) are left out.
    """
    from parity.cases import load_cases

    out = []
    for area in AREAS:
        for case in load_cases(area):
            path = case.golden_path
            if not path.exists() or case.spec.get("known_divergence"):
                continue
            golden = json.loads(path.read_text(encoding="utf-8"))
            if golden["ok"] and golden["value"].get("t") == "module_output":
                out.append((area, case.id))
    return out


def _r_vector(chunk: str, column: str) -> list[str]:
    """The quoted strings of ``column = c(...)`` in an R ``dput()`` table chunk."""
    start = chunk.index(f"{column} = c(") + len(f"{column} = c(")
    values: list[str] = []
    i, in_str, buf = start, False, []
    while i < len(chunk):
        ch = chunk[i]
        if in_str:
            if ch == "\\":
                buf.append(chunk[i + 1])
                i += 2
                continue
            if ch == '"':
                values.append("".join(buf))
                buf, in_str = [], False
            else:
                buf.append(ch)
        elif ch == '"':
            in_str = True
        elif ch == ")":
            break
        i += 1
    return values


def _run_case(case_id: str, area: str = "mod_psychds") -> ModuleOutput:
    from parity.cases import load_cases, run_python

    (case,) = [c for c in load_cases(area) if c.id == case_id]
    return run_python(case)  # type: ignore[no-any-return]


# -- the report, exactly --------------------------------------------------------------


@pytest.mark.parametrize(("area", "case_id"), _module_cases())
def test_report_blocks_match_r_exactly(area: str, case_id: str) -> None:
    """Every prose block (the tree's indentation too) and the checklist table."""
    golden = _golden(case_id, area)["value"]
    out = _run_case(case_id, area)
    r_report = _element(golden, "report")["v"]
    r_text = [s for s in r_report if not s.lstrip().startswith("```{r}")]
    report = out.report if isinstance(out.report, list) else [out.report]
    py_text = [b for b in report if isinstance(b, str)]
    assert py_text == r_text

    r_chunks = [s for s in r_report if s.lstrip().startswith("```{r}")]
    py_tables = [b for b in report if isinstance(b, ReportTable)]
    assert len(py_tables) == len(r_chunks)
    for chunk, table in zip(r_chunks, py_tables, strict=True):
        assert 'metacheck::report_table(table, "auto", 10, FALSE)' in chunk
        assert table.maxrows == 10
        assert list(table.data.columns) == ["Requirement", "Level", "Status", "Detail"]
        for col in table.data.columns:
            assert table.data[col].tolist() == _r_vector(chunk, col), col


def test_traffic_lights_cover_every_branch() -> None:
    lights = {
        name: _element(_golden(f"psychds_check.{name}")["value"], "traffic_light")["v"][0]
        for name in (
            "compliant",
            "green_partial",
            "yellow_many",
            "red_nodata",
            "red_nocols",
            "empty",
        )
    }
    assert lights == {
        "compliant": "green",
        "green_partial": "green",
        "yellow_many": "yellow",
        "red_nodata": "red",
        "red_nocols": "red",
        "empty": "na",
    }
    for name, light in lights.items():
        assert ps.run_chain(name).traffic_light == light


# -- behaviour -------------------------------------------------------------------------


def test_plan_table_targets() -> None:
    out = ps.run_chain("yellow_many")
    table = out.table
    assert list(table.columns) == [
        "file_name",
        "data_type",
        "group",
        "current_path",
        "target_path",
        "status",
        "convert",
        "original_target",
        "referenced_by",
    ]
    row = table.set_index("file_name")
    # converted (tabular, not CSV): _data.csv target plus the original alongside
    assert row.loc["Data Raw.xlsx", "target_path"] == "data/study-DataRaw_data.csv"
    assert row.loc["Data Raw.xlsx", "original_target"] == "data/Data Raw.xlsx"
    assert bool(row.loc["survey.sav", "convert"])
    # raw data keeps its own name and is in place (U112: R compared against the
    # _data.csv target and suggested "Move data/eeg.npy -> data/eeg.npy")
    assert row.loc["eeg.npy", "target_path"] == "data/eeg.npy"
    assert row.loc["eeg.npy", "current_path"] == "data/eeg.npy"
    assert row.loc["eeg.npy", "status"] == "present"
    # an empty keyword slug falls back to file<i>
    assert row.loc["___.csv", "target_path"] == "data/study-file5_data.csv"
    assert "`data/eeg.npy` → `data/eeg.npy`" not in "\n".join(
        b for b in out.report if isinstance(b, str)
    )
    assert out.summary_text.startswith("\n-  2 of 3 required Psych-DS items present; 1 missing.")


def test_multi_study_layout() -> None:
    out = ps.run_chain("multi")
    targets = dict(zip(out.table["current_path"], out.table["target_path"], strict=True))
    assert targets["README.md"] == "README.md"
    assert targets["study1/README.txt"] == "study-ex1/README.txt"
    assert targets["study2/s2.xlsx"] == "study-ex2/data/study-s2_data.csv"
    assert targets["LICENSE"] == "LICENSE"
    # U112: collection-level metadata stays at the root (R: analysis/ for a
    # "code"-typed .json, README.json for a readme-role one)
    assert targets["ro-crate-metadata.json"] == "ro-crate-metadata.json"
    assert out.table["referenced_by"].tolist()[5] == ["ex2", "pilot1"]
    assert "across 3 study groups" in out.report[0]
    tree = next(b for b in out.report if isinstance(b, str) and b.startswith("<pre"))
    assert "study-pilot1/" in tree
    assert "dataset_description.json  ← missing" in tree


def test_group_no_evidence_note_depends_on_llm_use() -> None:
    off = ps.run_llm("yellow_six", use=False)
    on = ps.run_llm("yellow_six", use=True)
    note_off = next(b for b in off.report if isinstance(b, str) and b.startswith("*Study"))
    note_on = next(b for b in on.report if isinstance(b, str) and b.startswith("*Study"))
    assert ", and no LLM was used" in note_off
    assert "or run with `llm_use(TRUE)`" in note_off
    assert ", and no LLM was used" not in note_on
    assert "llm_use(TRUE)" not in note_on
    assert pc.llm_use() is False  # restored


def test_paper_list_summary_is_per_paper() -> None:
    # without paper ids in the file table the counts belong to the first paper
    out = ps.run_chain("psychsci")
    st = out.summary_table
    assert st["paper_id"].tolist() == [
        "0956797613520608",
        "0956797614522816",
        "0956797614527830",
        "to_err_is_human",
    ]
    assert st["required_met"].tolist() == [3, 0, 0, 0]
    assert st["misplaced_n"].tolist() == [2, 0, 0, 0]
    # U113: files of two papers are counted per paper (R: one pooled row)
    st = ps.run_chain("psychsci_two_papers").summary_table.set_index("paper_id")
    assert st.loc["0956797613520608"].tolist() == [3, 0, 2, 1, 0]
    assert st.loc["to_err_is_human"].tolist() == [2, 1, 0, 3, 2]
    assert st.loc["0956797614522816"].tolist() == [0, 0, 0, 0, 0]


def test_pid_falls_back_to_table_paper_ids() -> None:
    empty = pc.PaperList([])
    s = pd.DataFrame({"paper_id": ["X", "Y", "X"]})
    assert mod._pid(empty, None, s) == "X"
    assert mod._pid(empty, None, None) is None
    assert mod._pid(pc.demopaper(), s) == "to_err_is_human"
    # U78: paper = None (local files only) uses the tables' ids (R: paper_id(NULL) stops)
    assert mod._pid(None, s) == "X"


def test_does_not_modify_its_inputs() -> None:
    chain = ps.fake_chain("multi")
    before = chain.get("structure").copy(deep=True)
    refs = [list(v) if v else v for v in before["referenced_by"]]
    out = module_run(chain, "psychds_check")
    pd.testing.assert_frame_equal(chain.get("structure"), before)
    assert [list(v) if v else v for v in chain.get("structure")["referenced_by"]] == refs
    # the plan's referenced_by lists are copies (R copies on modify)
    out.table["referenced_by"].tolist()[5].append("changed")
    assert chain.get("structure")["referenced_by"].tolist()[5] == ["ex2", "pilot1"]


def test_empty_structure_returns_na() -> None:
    out = ps.run_chain("empty")
    assert out.traffic_light == "na"
    assert out.table.shape == (0, 0)
    assert out.report == "We found no repository files to check for Psych-DS compliance."
    assert out.get("na_replace") == {
        "required_met": 0,
        "required_missing": 0,
        "recommended_met": 0,
        "recommended_missing": 0,
        "misplaced_n": 0,
    }


def test_incomplete_file_tables_do_not_stop_the_module() -> None:
    # U113: R stops on an NA data type, a missing data_type/file_name column
    na_type = ps.run_chain("na_type").table.set_index("file_name")
    assert na_type.loc["a.R", "target_path"] == "unknown/a.R"
    no_type = ps.run_chain("no_type_col").table
    assert no_type["target_path"].str.startswith("unknown/").all()
    no_name = ps.run_chain("no_name_col_data").table
    assert no_name["file_name"].tolist() == ["x.csv"]
    assert no_name["target_path"].tolist() == ["data/study-x_data.csv"]
    # U78: paper = None, documented as "check local files only"
    out = ps.run_chain("paper_none")
    assert out.summary_table["paper_id"].notna().all()
    assert out.traffic_light == "green"


def test_compliant_names_and_root_files_stay_in_place() -> None:
    # U112: a valid Psych-DS data file name (.csv or .tsv) is kept, raw data in
    # data/ is in place, LICENSE/CHANGES (classed "unknown") and
    # ro-crate-metadata.json belong at the root
    out = ps.run_chain("compliant_raw")
    assert out.table["status"].tolist() == ["present"] * 8
    assert out.table["convert"].tolist() == [False] * 8
    assert out.summary_table["misplaced_n"].tolist() == [0]
    assert out.traffic_light == "green"
    # a name that only looks like one is still renamed
    tgt = mod._assess(
        pd.DataFrame(
            {
                "file_name": ["Study-1_data.csv", "study-1_data.xlsx", "study-1.csv"],
                "data_type": ["data"] * 3,
            }
        ),
        None,
        None,
    )["target_path"]
    assert tgt == [
        "data/study-Study1data_data.csv",
        "data/study-study1data_data.csv",
        "data/study-study1_data.csv",
    ]


# -- the unchained path: psychds_check runs data_check itself ---------------------------


def _patched_data_check(monkeypatch: pytest.MonkeyPatch, chain: ModuleOutput) -> list[Any]:
    import pytacheck.module as module_mod

    calls: list[Any] = []

    def fake_module_run(paper: Any, module: Any, **kwargs: Any) -> ModuleOutput:
        calls.append((paper, module, kwargs))
        return chain

    monkeypatch.setattr(module_mod, "module_run", fake_module_run)
    return calls


def test_runs_data_check_when_not_chained(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = ps.run_chain("compliant")
    chain = ps.fake_chain("compliant")
    calls = _patched_data_check(monkeypatch, chain)
    paper = pc.demopaper()
    with local_options({"metacheck.llm.model": "groq/test-model"}):
        res = mod.psychds_check(paper, cache=True, skip_on_api_limit=True)
    ((called_paper, called_module, kwargs),) = calls
    assert called_paper is paper
    assert called_module == "data_check"
    # R: local_path is only passed when given; model defaults to llm_model()
    assert kwargs == {
        "local_only": False,
        "model": "groq/test-model",
        "params": {},
        "cache": True,
        "skip_on_api_limit": True,
    }
    assert res["traffic_light"] == expected.traffic_light
    pd.testing.assert_frame_equal(res["table"], expected.table)
    assert res["summary_text"] == expected.summary_text


def test_local_path_and_llm_args_are_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patched_data_check(monkeypatch, ps.fake_chain("empty"))
    res = mod.psychds_check(
        pc.demopaper(),
        local_path="some/dir",
        local_only=True,
        model="openai/x",
        params={"seed": 1},
    )
    (_, _, kwargs) = calls[0]
    assert kwargs == {
        "local_path": "some/dir",
        "local_only": True,
        "model": "openai/x",
        "params": {"seed": 1},
        "cache": False,
        "skip_on_api_limit": False,
    }
    assert res["traffic_light"] == "na"


def test_chained_data_check_is_not_rerun() -> None:
    chain = ps.fake_chain("compliant")
    out = module_run(chain, "psychds_check")  # data_check output comes from the chain
    assert out.traffic_light == "green"
    assert "data_check" in out.prev_outputs


# -- bibr export schema 12.x papers ------------------------------------------------------


def test_bibr12_paper_matches_its_legacy_reading(tmp_path: Path) -> None:
    """A native 12.x paper and the same paper written/read as legacy JSON agree."""
    paper12 = pc.read(ROOT / "tests" / "fixtures" / "bibr_v12_full.json")
    legacy_file = pc.paper_write(paper12, "legacy", tmp_path, schema_version=None)
    legacy = pc.read(legacy_file)
    assert pc.paper_id(paper12) == pc.paper_id(legacy) == ["demo"]
    for name in ("compliant", "yellow_many", "multi", "empty"):
        a = module_run(ps.fake_chain(name, paper=paper12), "psychds_check")
        b = module_run(ps.fake_chain(name, paper=legacy), "psychds_check")
        ra, rb = a.results(), b.results()
        assert ra.keys() == rb.keys()
        for key in ra:
            va, vb = ra[key], rb[key]
            if isinstance(va, pd.DataFrame):
                pd.testing.assert_frame_equal(va, vb)
            elif isinstance(va, list):
                assert [x.data.to_dict() if isinstance(x, ReportTable) else x for x in va] == [
                    x.data.to_dict() if isinstance(x, ReportTable) else x for x in vb
                ]
            else:
                assert va == vb, key


def test_bibr12_parity_goldens_agree_with_legacy_goldens() -> None:
    """R gives the 12.x demo paper the same plan and report as the legacy demo paper."""
    a = _golden("psychds_check.bibr12")["value"]
    b = _golden("psychds_check.compliant")["value"]
    for name in ("table", "traffic_light", "summary_text", "report", "na_replace"):
        assert _element(a, name) == _element(b, name), name


# -- metadata ----------------------------------------------------------------------------


def test_module_metadata() -> None:
    import inspect

    spec = module_find("psychds_check")
    assert spec.title == "Psych-DS Check"
    assert spec.section == "results"
    assert set(spec.requires) == {"network", "llm"}
    params = list(inspect.signature(spec.func).parameters)
    assert params == [
        "paper",
        "local_path",
        "local_only",
        "model",
        "params",
        "cache",
        "skip_on_api_limit",
    ]
    assert spec.arg_defaults["local_only"] is False
    assert spec.arg_defaults["cache"] is False


# -- psychds_tree_html -----------------------------------------------------------------


def test_tree_orders_directories_first_and_escapes() -> None:
    nodes = pd.DataFrame(
        {
            "path": ["b.txt", "A/x<1>.csv", "a2/deep/er/f.R", "C.txt"],
            "status": ["present", "move", "missing", "present"],
            "note": ["", "move from x&y", "", ""],
        }
    )
    html = mod.psychds_tree_html(nodes)
    assert html == (
        '<pre style="line-height:1.4">\n'
        "├── A/\n"
        '│   └── x&lt;1&gt;.csv <span style="color:#b9770e">(move from x&amp;y)</span>\n'
        "├── a2/\n"
        "│   └── deep/\n"
        "│       └── er/\n"
        '│           └── <span style="color:#c0392b">f.R  ← missing</span>\n'
        "├── b.txt\n"
        "└── C.txt\n"
        "</pre>"
    )


def test_tree_empty_inputs() -> None:
    assert mod.psychds_tree_html(None) == ""
    assert mod.psychds_tree_html(pd.DataFrame({"path": [], "status": [], "note": []})) == ""
    with pytest.raises(IndexError):
        mod.psychds_tree_html(pd.DataFrame({"path": [""], "status": ["present"]}))


# -- real repositories on disk (needs the data_check port) -------------------------------

REPO_EXPECTED = {
    # from metacheck: module_run(test_paper(), "psychds_check", local_path = ..., local_only = TRUE)
    # except "compliant" (U112): metacheck moves 3 files of this compliant layout
    # (misplaced_n 3: "unknown/CHANGES", "data/study-studyadata_data.csv",
    # "analysis/dataset_description.json"); every file is in place
    "compliant": (
        "green",
        [3, 0, 2, 1, 0],
        [
            "analysis/analysis.R",
            "CHANGES",
            "data/study-a_data.csv",
            "dataset_description.json",
            "README.md",
        ],
        True,
    ),
    "noncompliant": (
        "yellow",
        [2, 1, 0, 3, 3],
        ["analysis/analysis.R", "documentation/notes.txt", "data/study-survey_data.csv"],
        True,
    ),
    "multistudy": (
        "yellow",
        [2, 1, 1, 2, 2],
        [
            "README.md",
            "study-ex1/data/study-data_data.csv",
            "study-ex2/data/study-data_data.csv",
        ],
        False,
    ),
}


@pytest.mark.parametrize("repo", sorted(REPO_EXPECTED))
def test_local_repositories(repo: str) -> None:
    pytest.importorskip("pytacheck.modules.data_check", reason="data_check is not ported yet")
    light, counts, targets, note = REPO_EXPECTED[repo]
    with local_options({"metacheck.llm.use": False}):
        out = module_run(
            pc.test_paper(), "psychds_check", local_path=str(REPOS / repo), local_only=True
        )
    assert out.traffic_light == light
    assert out.summary_table is not None
    st = out.summary_table.iloc[0]
    assert [
        int(st[c])
        for c in (
            "required_met",
            "required_missing",
            "recommended_met",
            "recommended_missing",
            "misplaced_n",
        )
    ] == counts
    assert out.table["target_path"].tolist() == targets
    has_note = any(isinstance(b, str) and b.startswith("*Study subgrouping") for b in out.report)
    assert has_note is note
