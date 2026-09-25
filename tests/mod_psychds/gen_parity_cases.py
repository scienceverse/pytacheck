"""Write the psychds_check parity fixtures and cases.

Writes ``tests/mod_psychds/fixtures/chains.json`` (the fake data_check chains
and tree node tables both sides build, see ``fake_chain.R`` and
``parity_support.py``) and ``parity/cases/mod_psychds.yaml``. Run from the
repository root::

    .venv/bin/python tests/mod_psychds/gen_parity_cases.py
    .venv/bin/python -m parity generate --area mod_psychds
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent

# (file_name, file_path, data_type, doc_role, group, referenced_by)
File = tuple[str | None, str | None, str | None, str | None, str | None, list[str]]


def files(rows: list[File], drop: tuple[str, ...] = ()) -> dict[str, list[Any]]:
    cols = ("file_name", "file_path", "data_type", "doc_role", "group", "referenced_by")
    out = {c: [r[i] for r in rows] for i, c in enumerate(cols)}
    return {k: v for k, v in out.items() if k not in drop}


# a Psych-DS dataset that is already compliant (every recommended item too)
COMPLIANT = files(
    [
        ("analysis.R", "analysis/analysis.R", "code", None, "ex1", []),
        ("study-a_data.csv", "data/study-a_data.csv", "data", None, "ex1", []),
        ("dataset_description.json", "dataset_description.json", "code", None, "ex1", []),
        ("README.md", "README.md", "documentation", "readme", None, []),
        ("CHANGES", "CHANGES", "documentation", "supplemental", "ex1", []),
    ]
)

GREEN_PARTIAL = files(
    [
        ("raw.csv", "data/raw.csv", "data", None, "ex1", []),
        ("dataset_description.json", "dataset_description.json", "documentation", None, "ex1", []),
        ("run.py", "code/run.py", "code", None, "ex1", []),
    ]
)

# data in every format branch: converted (xlsx/sav/tsv), raw (npy), no extension,
# an empty keyword slug, an upper-case .CSV, non-ASCII names; 8 files to move
YELLOW_MANY = files(
    [
        ("Data Raw.xlsx", "raw/Data Raw.xlsx", "data", None, "ex1", []),
        ("survey.sav", "raw/survey.sav", "data", None, "ex1", []),
        ("eeg.npy", "data/eeg.npy", "data", None, "ex1", []),
        ("rawdata", "rawdata", "data", None, "ex1", []),
        ("___.csv", "data/___.csv", "data", None, "ex1", []),
        ("scores.CSV", "data/scores.CSV", "data", None, "ex1", []),
        ("café_ü.tsv", "data/café_ü.tsv", "data", None, "ex1", []),
        ("model.R", "model.R", "code", None, "ex1", []),
        ("README.md", "README.md", "documentation", "readme", None, []),
    ]
)

# exactly 6 files to move ("...and 1 more file to relocate.")
YELLOW_SIX = files(
    [
        ("a.csv", "a.csv", "data", None, "ex1", []),
        ("b.R", "b.R", "code", None, "ex1", []),
        ("c.R", "c.R", "code", None, "ex1", []),
        ("d.pdf", "d.pdf", "output", None, "ex1", []),
        ("e.png", "e.png", "materials", None, "ex1", []),
        ("f.txt", "f.txt", "documentation", None, "ex1", []),
        ("g.R", "analysis/g.R", "code", None, "ex1", []),
    ]
)

# no data at all; every non-data target: readme/licence with and without an
# extension, codebook documentation, materials, output, unknown, another type
RED_NODATA = files(
    [
        ("README", "README", "documentation", "readme", None, []),
        ("LICENSE.txt", "LICENSE.txt", "documentation", "license", None, []),
        ("LICENSE", "docs/LICENSE", "documentation", "license", None, []),
        ("codebook.xlsx", "codebook.xlsx", "documentation", "codebook", "ex1", []),
        ("stim.png", "stimuli/stim.png", "materials", None, "ex1", []),
        ("fig1.pdf", "outputs/fig1.pdf", "output", None, "ex1", []),
        ("notes.xyz", "notes.xyz", "unknown", None, "ex1", []),
        ("bundle.zip", "bundle.zip", "archive", None, "ex1", []),
        ("CHANGES.md", "CHANGES.md", "documentation", "supplemental", "ex1", []),
        ("x.R", "analysis/x.R", "code", None, "ex1", []),
    ]
)

RED_NOCOLS = files(
    [
        ("d.csv", "d.csv", "data", None, "ex1", []),
        ("dataset_description.json", "dataset_description.json", "documentation", None, "ex1", []),
    ]
)

# three studies (study-<group>/ layout), collection-level root files, a
# per-study readme and a file reused by another study
MULTI = files(
    [
        ("README.md", "README.md", "documentation", "readme", None, []),
        ("README.txt", "study1/README.txt", "documentation", "readme", "ex1", []),
        ("s1.csv", "study1/data/s1.csv", "data", None, "ex1", []),
        ("s2.xlsx", "study2/s2.xlsx", "data", None, "ex2", []),
        ("an1.R", "study1/an1.R", "code", None, "ex1", ["ex2"]),
        ("shared.pdf", "materials/shared.pdf", "materials", None, "ex1", ["ex2", "pilot1"]),
        ("LICENSE", "LICENSE", "documentation", "license", None, []),
        ("ro-crate-metadata.json", "ro-crate-metadata.json", "code", None, None, []),
        ("p.sps", "pilot/p.sps", "code", None, "pilot1", []),
    ]
)

SINGLE_NAMED = files(
    [
        ("p1.csv", "pilot1/p1.csv", "data", None, "pilot1", []),
        (
            "dataset_description.json",
            "dataset_description.json",
            "documentation",
            None,
            "pilot1",
            [],
        ),
    ]
)

# only file_name/data_type: current paths come from (backslashed) file names
MINIMAL_COLS = files(
    [
        ("sub\\dir\\script.R", None, "code", None, None, []),
        ("data\\x.csv", None, "data", None, None, []),
        ("dataset_description.json", None, "documentation", None, None, []),
        ("CHANGES.txt", None, "documentation", None, None, []),
        ("changes_v2.txt", None, "documentation", None, None, []),
        ("changesLOG", None, "documentation", None, None, []),
    ],
    drop=("file_path", "doc_role", "group", "referenced_by"),
)

# a missing file_path falls back to the raw (un-normalised) file name
PATH_NA = files(
    [
        ("a\\b.R", None, "code", None, "ex1", []),
        ("x.csv", "data/x.csv", "data", None, "ex1", []),
        ("dataset_description.json", None, "documentation", None, "ex1", []),
    ],
    drop=("referenced_by",),
)

# escaping, sorting (directories first, case-insensitive, accents), duplicate targets
TREE = files(
    [
        ("R&D <final>.docx", "docs/R&D <final>.docx", "documentation", None, "ex1", []),
        ("Beta.R", "Beta.R", "code", None, "ex1", []),
        ("alpha.R", "alpha.R", "code", None, "ex1", []),
        ("_setup.R", "_setup.R", "code", None, "ex1", []),
        ("Zeta.py", "analysis/Zeta.py", "code", None, "ex1", []),
        ("10_fit.R", "analysis/10_fit.R", "code", None, "ex1", []),
        ("2_fit.R", "analysis/2_fit.R", "code", None, "ex1", []),
        ("README.md", "README.md", "documentation", "readme", None, []),
        ("README.md", "old/README.md", "documentation", "readme", None, []),
        ("étude.csv", "étude.csv", "data", None, "ex1", []),
        ("Émile.txt", "Émile.txt", "documentation", None, "ex1", []),
        ("emile.txt", "emile.txt", "documentation", None, "ex1", []),
        ("Data", "materials/Data", "materials", None, "ex1", []),
        ("data.bin", "materials/data.bin", "materials", None, "ex1", []),
    ]
)

EMPTY: dict[str, list[Any]] = {"file_name": [], "data_type": []}

# a realistic listing classified by data_classify_files()/.data_doc_role()
CLASSIFIED = {
    "file_name": [
        "raw.sav", "model.R", "README.md", "LICENSE", "codebook.xlsx", "img1.png",
        "fig1.pdf", "notes.xyz", "CHANGES.md", "dataset_description.json",
        "scores.xlsx", "eeg.npy", "survey.qsf", "table.html", "codebook.csv",
    ],
    "file_path": [
        "data/raw.sav", "analysis/model.R", "README.md", "LICENSE", "docs/codebook.xlsx",
        "stimuli/img1.png", "results/fig1.pdf", "notes.xyz", "CHANGES.md",
        "dataset_description.json", "data/scores.xlsx", "data/eeg.npy",
        "materials/survey.qsf", "output/table.html", "data/codebook.csv",
    ],
    "group": [
        "ex1", "ex1", None, None, "ex1", "ex1", "ex1", "ex1", "ex1", "ex1", "ex1", "ex1",
        "ex1", "ex1", "ex1",
    ],
}  # fmt: skip

BIBR12 = "tests/fixtures/bibr_v12_full.json"
BIBR12_MINIMAL = "tests/bibr12/fixtures/edge_minimal.json"
PSYCHSCI_ONE = "upstream/metacheck/tests/testthat/fixtures/psychsci/0956797613520608.json"

CHAINS: dict[str, dict[str, Any]] = {
    "compliant": {
        "structure": COMPLIANT,
        "columns": 2,
        "labels": ["labelled", "llm"],
        "group_no_evidence": False,
    },
    "green_partial": {"structure": GREEN_PARTIAL, "columns": 1},
    "yellow_many": {
        "structure": YELLOW_MANY,
        "columns": 5,
        "labels": ["labelled", None, "unlabelled"],
        "group_no_evidence": True,
    },
    "yellow_six": {"structure": YELLOW_SIX, "columns": 3, "group_no_evidence": True},
    "red_nodata": {"structure": RED_NODATA, "columns": 0, "group_no_evidence": False},
    "red_nocols": {"structure": RED_NOCOLS},
    "multi": {
        "structure": MULTI,
        "columns": 4,
        "labels": ["labelled", "labelled", "llm", "labelled"],
        "group_no_evidence": False,
    },
    "single_named": {"structure": SINGLE_NAMED, "columns": 2, "labels": ["labelled", "llm"]},
    "minimal_cols": {
        "structure": MINIMAL_COLS,
        "columns": 1,
        "labels": ["labelled"],
        "labels_no_status": True,
        "group_no_evidence": "NA",
    },
    "path_na": {
        "structure": PATH_NA,
        "columns": 3,
        "labels": ["codebook", "conflicting_definition"],
    },
    "tree": {"structure": TREE, "columns": 1, "labels": ["labelled"], "group_no_evidence": False},
    "classified": {
        "structure": CLASSIFIED,
        "classify": True,
        "columns": 6,
        "labels": ["labelled", "labelled", "labelled"],
    },
    "empty": {"structure": EMPTY, "columns": 0},
    "psychsci": {"paper": "psychsci", "structure": GREEN_PARTIAL, "columns": 1},
    "psychsci_empty": {"paper": "psychsci", "structure": EMPTY},
    # bibr export schema 12.x papers (pytacheck's default reader) and a mixed list
    "bibr12": {
        "paper": [BIBR12],
        "structure": COMPLIANT,
        "columns": 2,
        "labels": ["labelled", "llm"],
        "group_no_evidence": False,
    },
    "bibr12_mixed": {
        "paper": [BIBR12_MINIMAL, BIBR12, PSYCHSCI_ONE],
        "structure": YELLOW_SIX,
        "columns": 3,
        "group_no_evidence": True,
    },
    "bibr12_empty": {"paper": [BIBR12], "structure": EMPTY},
    "test_paper": {"paper": "test", "structure": MULTI, "columns": 2, "labels": ["llm"]},
    "test_paper_empty": {"paper": "test", "structure": EMPTY, "columns": 3},
    "na_type": {
        "structure": {"file_name": ["a.R", "b.R"], "data_type": [None, "code"]},
        "columns": 1,
    },
    "no_type_col": {"structure": {"file_name": ["a.R", "b.csv"]}, "columns": 1},
    "no_name_col": {
        "structure": {"file_path": ["a/x.R", "b.txt"], "data_type": ["code", "documentation"]},
        "columns": 1,
    },
    "no_name_col_data": {
        "structure": {"file_path": ["a/x.csv"], "data_type": ["data"]},
        "columns": 1,
    },
    "paper_none": {"paper": "none", "structure": GREEN_PARTIAL, "columns": 1},
    "paper_none_empty": {"paper": "none", "structure": EMPTY},
    # U113: a paper list whose files belong to two of its papers; each paper
    # gets its own summary row (R: one row, the first paper's, pooled counts)
    "psychsci_two_papers": {
        "paper": "psychsci",
        "structure": {
            **{
                k: v + v2
                for (k, v), v2 in zip(COMPLIANT.items(), GREEN_PARTIAL.values(), strict=True)
            },
            "paper_id": ["0956797613520608"] * 5 + ["to_err_is_human"] * 3,
        },
        "columns": 2,
    },
    # U112: a Psych-DS layout with a raw (non-tabular) file in data/, a
    # LICENSE/CHANGES classed "unknown", a ro-crate file and a .tsv data file
    # that already follows the naming rule
    "compliant_raw": {
        "paper": "test",
        "structure": files(
            [
                (
                    "study-1_task-stroop_data.tsv",
                    "data/study-1_task-stroop_data.tsv",
                    "data",
                    None,
                    "ex1",
                    [],
                ),
                ("eeg.npy", "data/eeg.npy", "data", None, "ex1", []),
                ("dataset_description.json", "dataset_description.json", "code", None, "ex1", []),
                ("README.md", "README.md", "documentation", "readme", None, []),
                ("LICENSE", "LICENSE", "unknown", "license", None, []),
                ("CHANGES", "CHANGES", "unknown", None, "ex1", []),
                (
                    "ro-crate-metadata.json",
                    "ro-crate-metadata.json",
                    "documentation",
                    "readme",
                    None,
                    [],
                ),
                ("analysis.R", "analysis/analysis.R", "code", None, "ex1", []),
            ]
        ),
        "columns": 3,
        "labels": ["labelled", "labelled", "labelled"],
    },
}

TREES: dict[str, dict[str, Any]] = {
    "null": {"nodes": None},
    "empty": {"nodes": {"path": [], "status": [], "note": []}},
    "statuses": {
        "nodes": {
            "path": [
                "docs/x.md",
                "docs",
                "a/b/c.txt",
                "a/b/missing.txt",
                "a/moved.R",
                "a/moved_no_note.R",
                "scaffold/README",
                "/abs//double.txt",
                "win\\path\\f.txt",
                "docs/x.md",
                "Top.txt",
                "top2.txt",
            ],
            "status": [
                "present",
                "missing",
                "present",
                "missing",
                "move",
                "move",
                "present-scaffold",
                "present",
                "move",
                "missing",
                "present",
                None,
            ],
            "note": [
                "",
                "",
                "",
                "",
                "move from x/moved.R",
                "",
                "",
                "",
                "move from <old> & new",
                "",
                "",
                "",
            ],
        }
    },
    "no_note": {
        "nodes": {"path": ["b/x.csv", "a.txt", "b/y.csv"], "status": ["missing", "move", "present"]}
    },
    "empty_path": {
        "nodes": {"path": ["a.txt", ""], "status": ["present", "present"], "note": ["", ""]}
    },
}

H = "__import__('tests.mod_psychds.parity_support', fromlist=['_'])"
SRC = "source(rpath('tests/mod_psychds/fake_chain.R'), local = TRUE)"


def chain_case(name: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": f"psychds_check.{name}",
        "module": "psychds_check",
        "args": {
            "paper": {
                "$expr": {
                    "r": f"local({{{SRC}; psychds_fake_chain('{name}')}})",
                    "py": f"{H}.fake_chain('{name}')",
                }
            }
        },
        **extra,
    }


def llm_case(name: str, use: bool) -> dict[str, Any]:
    flag = "TRUE" if use else "FALSE"
    return {
        "id": f"psychds_check.{name}.llm_use_{flag.lower()}",
        "r": "base::identity",
        "py": "tests.mod_psychds.parity_support.identity",
        "args": {
            "x": {
                "$expr": {
                    "r": f"local({{{SRC}; psychds_run_llm('{name}', {flag})}})",
                    "py": f"{H}.run_llm('{name}', {use})",
                }
            }
        },
    }


def tree_case(name: str) -> dict[str, Any]:
    return {
        "id": f"psychds_tree_html.{name}",
        "r": "base::identity",
        "py": "tests.mod_psychds.parity_support.identity",
        "args": {
            "x": {
                "$expr": {
                    "r": f"local({{{SRC}; psychds_tree('{name}')}})",
                    "py": f"{H}.tree('{name}')",
                }
            }
        },
    }


def main() -> None:
    fixtures = HERE / "fixtures"
    fixtures.mkdir(exist_ok=True)
    (fixtures / "chains.json").write_text(
        json.dumps({"chains": CHAINS, "trees": TREES}, indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    cases: list[dict[str, Any]] = [chain_case(name) for name in CHAINS]
    cases += [llm_case("yellow_six", True), llm_case("yellow_many", False)]
    cases += [tree_case(name) for name in TREES]
    header = (
        "# psychds_check parity cases (inst/modules/psychds_check.R), generated by\n"
        "# tests/mod_psychds/gen_parity_cases.py -- edit that script, not this file.\n"
        "# Each case runs the module on a fake data_check chain built from\n"
        "# tests/mod_psychds/fixtures/chains.json (R: tests/mod_psychds/fake_chain.R,\n"
        "# Python: tests/mod_psychds/parity_support.py).\n"
    )
    body = yaml.safe_dump(
        {"area": "mod_psychds", "cases": cases},
        sort_keys=False,
        allow_unicode=True,
        default_style='"',
        width=1000,
    )
    (ROOT / "parity" / "cases" / "mod_psychds.yaml").write_text(header + body, encoding="utf-8")


if __name__ == "__main__":
    main()
