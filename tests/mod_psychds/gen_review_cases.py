"""Write the psychds_check *review* parity fixtures and cases.

Adversarial-review cases for branches the first case set did not reach:
missing file names (with and without a file path), R's error order when the
``file_name`` column is absent, extension/slug edge cases, odd study group
names, the exactly-five-moves boundary, duplicate targets, non-logical
``group_no_evidence`` values and ``psychds_tree_html()`` on NA notes and NA
paths. Writes ``tests/mod_psychds/fixtures/review_chains.json`` (read by
``fake_chain.R`` and ``parity_support.py`` with ``file = "review_chains.json"``)
and ``parity/cases/mod_psychds_review.yaml``. Run from the repository root::

    .venv/bin/python tests/mod_psychds/gen_review_cases.py
    .venv/bin/python -m parity generate --area mod_psychds_review
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
FILE = "review_chains.json"

# (file_name, file_path, data_type, doc_role, group)
Row = tuple[str | None, str | None, str | None, str | None, str | None]


def files(rows: list[Row], drop: tuple[str, ...] = ()) -> dict[str, list[Any]]:
    cols = ("file_name", "file_path", "data_type", "doc_role", "group")
    out = {c: [r[i] for r in rows] for i, c in enumerate(cols)}
    return {k: v for k, v in out.items() if k not in drop}


CHAINS: dict[str, dict[str, Any]] = {
    # a missing file name is harmless when the file path is known
    "na_name_code_path": {
        "structure": files(
            [
                (None, "x/y.R", "code", None, "ex1"),
                ("b.R", "analysis/b.R", "code", None, "ex1"),
                (None, "old/notes.txt", "documentation", None, "ex1"),
            ]
        ),
        "columns": 1,
    },
    # ...but with no path either the current path is NA: `if (n_misplaced > 0)` fails
    "na_name_code_nopath": {
        "structure": files(
            [(None, None, "code", None, "ex1"), ("b.R", None, "code", None, "ex1")],
            drop=("file_path",),
        ),
        "columns": 1,
    },
    # an NA readme name gives README.NA, then the NA current path fails
    "na_name_readme_na_path": {
        "structure": files([(None, None, "documentation", "readme", None)]),
        "columns": 1,
    },
    # a data file with no name has an NA target: the tree lookup fails
    "na_name_data_path": {
        "structure": files(
            [("a.R", "a.R", "code", None, "ex1"), (None, "raw/x.csv", "data", None, "ex1")]
        ),
        "columns": 1,
    },
    # no file_name column: R's error depends on the first row that needs the name
    "no_name_readme": {
        "structure": files(
            [
                (None, "x/README.md", "documentation", "readme", None),
                (None, "a.R", "code", None, "ex1"),
            ],
            drop=("file_name",),
        ),
        "columns": 1,
    },
    "no_name_license_second": {
        "structure": files(
            [
                (None, "a.R", "code", None, "ex1"),
                (None, "LICENSE", "documentation", "license", None),
            ],
            drop=("file_name",),
        ),
        "columns": 1,
    },
    "no_name_na_type_first": {
        "structure": files(
            [(None, "a.R", None, None, "ex1"), (None, "b.csv", "data", None, "ex1")],
            drop=("file_name",),
        ),
        "columns": 1,
    },
    "no_name_no_path": {
        "structure": files(
            [(None, None, "code", None, "ex1"), (None, None, "documentation", None, "ex1")],
            drop=("file_name", "file_path"),
        ),
        "columns": 1,
    },
    "no_name_no_path_no_type": {
        "structure": files(
            [(None, None, None, None, "ex1"), (None, None, None, None, "ex1")],
            drop=("file_name", "file_path", "data_type"),
        ),
        "columns": 1,
    },
    # extensions, slugs and conversions at the edges
    "ext_edge": {
        "structure": files(
            [
                ("README.café", "docs/README.café", "documentation", "readme", "ex1"),
                ("LICENSE.v2-final", "LICENSE.v2-final", "documentation", "license", None),
                ("README.md~", "README.md~", "documentation", "readme", None),
                ("data.tar.gz", "data/data.tar.gz", "data", None, "ex1"),
                (".hidden", ".hidden", "data", None, "ex1"),
                ("Book1.XLSX", "Book1.XLSX", "data", None, "ex1"),
                ("x.RData", "x.RData", "data", None, "ex1"),
                ("t.TSV", "data/t.TSV", "data", None, "ex1"),
                ("~$Book1.xlsx", "~$Book1.xlsx", "data", None, "ex1"),
                ("", "", "data", None, "ex1"),
                ("dataset_description.JSON", "dataset_description.JSON", "code", None, "ex1"),
                ("CHANGES", "old/CHANGES", "documentation", "supplemental", "ex1"),
                ("dir/", "dir/", "materials", None, "ex1"),
                ("Ärger Daten-2.csv", "x/Ärger Daten-2.csv", "data", None, "ex1"),
                ("readme", "readme", "documentation", "readme", None),
                ("x.csv.bak", "x.csv.bak", "data", None, "ex1"),
                ("CHANGELOG.md", "CHANGELOG.md", "documentation", None, "ex1"),
                ("Changes.txt", "a/Changes.txt", "documentation", None, "ex1"),
            ]
        ),
        "columns": 3,
        # more documented columns than columns: no "Document" suggestion
        "labels": ["labelled", "labelled", "llm", "labelled"],
        "group_no_evidence": True,
    },
    # empty-string and nested study group names
    "multi_odd_groups": {
        "structure": files(
            [
                ("s.csv", "s.csv", "data", None, ""),
                ("t.csv", "a/b/t.csv", "data", None, "a/b"),
                ("u.R", "u.R", "code", None, "a/b"),
                ("README", "README", "documentation", "notes", None),
                ("v.R", "x/v.R", "code", None, "x"),
                ("w.pdf", "w.pdf", "materials", None, None),
            ]
        ),
        "columns": 2,
        "labels": ["unlabelled", "llm"],
    },
    # exactly five moves: no "...and N more" line; integer group_no_evidence is not TRUE
    "exactly_five": {
        "structure": files(
            [
                ("a.R", "a.R", "code", None, "ex1"),
                ("b.R", "b.R", "code", None, "ex1"),
                ("c.R", "c.R", "code", None, "ex1"),
                ("d.pdf", "d.pdf", "output", None, "ex1"),
                ("e.png", "e.png", "materials", None, "ex1"),
                ("f.txt", "documentation/f.txt", "documentation", None, "ex1"),
                (
                    "dataset_description.json",
                    "dataset_description.json",
                    "documentation",
                    None,
                    None,
                ),
            ]
        ),
        "columns": 2,
        "labels": ["unlabelled", "missing"],
        "group_no_evidence": 1,
    },
    # a single file; a length-2 group_no_evidence is not TRUE either
    "one_file": {
        "structure": files([("only.csv", "only.csv", "data", None, "g1")]),
        "columns": 1,
        "group_no_evidence": [True, True],
    },
    # several files share a target: the tree keeps the first one's status
    "dup_targets": {
        "structure": files(
            [
                ("a.R", "analysis/a.R", "code", None, "ex1"),
                ("a.R", "old/a.R", "code", None, "ex1"),
                ("b.R", "old/b.R", "code", None, "ex1"),
                ("b.R", "analysis/b.R", "code", None, "ex1"),
                ("m.csv", "data/m.csv", "data", None, "ex1"),
                ("m.xlsx", "data/m.xlsx", "data", None, "ex1"),
                ("README.md", "README.md", "documentation", "readme", None),
                ("readme.MD", "docs/readme.MD", "documentation", "readme", "ex1"),
            ]
        ),
        "columns": 4,
        "labels": ["labelled"],
        "group_no_evidence": False,
    },
    # a data file whose name is only an extension, a dotted stem and a slug to "file<i>"
    "slug_edge": {
        "structure": files(
            [
                ("dataset_description.json", "dataset_description.json", "code", None, None),
                (".csv", "data/.csv", "data", None, "ex1"),
                ("a.b.c.csv", "a.b.c.csv", "data", None, "ex1"),
                ("--.sav", "--.sav", "data", None, "ex1"),
                ("日本語.xlsx", "日本語.xlsx", "data", None, "ex1"),
            ]
        ),
        "columns": 5,
        "labels": ["labelled", "llm", "labelled", "labelled", "labelled"],
    },
    # non-ASCII names in the tree: ICU collation of lower-cased names
    "icu_names": {
        "structure": files(
            [
                ("øre.R", "øre.R", "code", None, "ex1"),
                ("ore.R", "ore.R", "code", None, "ex1"),
                ("ōre.R", "ōre.R", "code", None, "ex1"),
                ("æble.R", "æble.R", "code", None, "ex1"),
                ("afble.R", "afble.R", "code", None, "ex1"),
                ("łódź.R", "łódź.R", "code", None, "ex1"),
                ("Straße.pdf", "Straße.pdf", "materials", None, "ex1"),
                ("strasse.pdf", "strasse.pdf", "materials", None, "ex1"),
                ("strast.pdf", "strast.pdf", "materials", None, "ex1"),
                ("Ødegaard.csv", "data/Ødegaard.csv", "data", None, "ex1"),
                ("ｆile.R", "ｆile.R", "code", None, "ex1"),
                ("file².R", "file².R", "code", None, "ex1"),
                ("file2.R", "file2.R", "code", None, "ex1"),
            ]
        ),
        "columns": 2,
    },
}

TREES: dict[str, dict[str, Any]] = {
    # a move with an NA note still prints "(NA)" (nzchar(NA) is TRUE)
    "na_note": {
        "nodes": {"path": ["a/x.R", "b.R"], "status": ["move", "move"], "note": [None, "n"]}
    },
    # an NA path cannot be looked up: subscript out of bounds
    "na_path_last": {
        "nodes": {"path": ["a/x.R", None], "status": ["move", "present"], "note": ["q", ""]}
    },
    "na_path_first": {
        "nodes": {"path": [None, "a/x.R"], "status": ["move", "present"], "note": ["q", ""]}
    },
    # ICU collation of lower-cased names; directories first
    "collation": {
        "nodes": {
            "path": [
                "a-b.R",
                "a_b.R",
                "a.b.R",
                "ab.R",
                "A.R",
                "a.R",
                "b 1.R",
                "b1.R",
                "B.R",
                "Ä.R",
                "z.R",
                "é",
                "~x",
                "1.R",
                "01.R",
                "#x",
                "(x)",
                "x!",
                "a",
                "Ab",
                "B/x",
                "a/y",
                "_d/z",
                "Ä/q",
                "ß",
                "ss",
                "İx",
                "ix",
            ],
            "status": ["present"] * 28,
            "note": [""] * 28,
        }
    },
    # ICU root collation beyond code points: letters without a canonical
    # decomposition, expansions, compatibility forms and accent order
    "collation_icu": {
        "nodes": {
            "path": [
                "øre.csv",
                "ore.csv",
                "öre.csv",
                "ōre.csv",
                "pre.csv",
                "æble.csv",
                "aeble.csv",
                "afble.csv",
                "œuvre.R",
                "oeuvre.R",
                "łódź.txt",
                "lodz.txt",
                "mz.txt",
                "đak.txt",
                "ðak.txt",
                "dak.txt",
                "ħal.R",
                "hal.R",
                "straße.R",
                "strasse.R",
                "strast.R",
                "ıx.R",
                "iy.R",
                "jx.R",
                "ŋa.R",
                "nb.R",
                "oa.R",
                "þa.R",
                "zz.R",
                "ｄata.csv",
                "data².csv",
                "data2.csv",
                "ﬁle.R",
                "file.R",
                "étude.R",
                "ètude.R",
                "êtude.R",
                "études/x.R",
                "Øvelse/y.R",
                "ovelse/z.R",
                "ÅR.txt",
                "ÄR.txt",
                "AR.txt",
            ],
            "status": ["present"] * 43,
            "note": [""] * 43,
        }
    },
    # backslash and slash spellings of one path, a node that is also a directory
    "slash_dupes": {
        "nodes": {
            "path": ["a\\b", "a/b", "a/b/c", "a\\b", "d/e/", "d/e/f", "<x>/&y"],
            "status": ["move", "missing", "present", "missing", "move", "present", "move"],
            "note": ["from \\old", "", "", "", "e & e", "", "<from>"],
        }
    },
}

H = "__import__('tests.mod_psychds.parity_support', fromlist=['_'])"
SRC = "source(rpath('tests/mod_psychds/fake_chain.R'), local = TRUE)"


def chain_case(name: str) -> dict[str, Any]:
    return {
        "id": f"psychds_check.review.{name}",
        "module": "psychds_check",
        "args": {
            "paper": {
                "$expr": {
                    "r": f"local({{{SRC}; psychds_fake_chain('{name}', '{FILE}')}})",
                    "py": f"{H}.fake_chain('{name}', file='{FILE}')",
                }
            }
        },
    }


def via_case(name: str, module: str) -> dict[str, Any]:
    """psychds_check chained after *module*, which ran on the fake data_check output."""
    py_run = "__import__('metacheck.module', fromlist=['_']).module_run"
    return {
        "id": f"psychds_check.review.{name}.via_{module}",
        "module": "psychds_check",
        "args": {
            "paper": {
                "$expr": {
                    "r": f"local({{{SRC}; module_run(psychds_fake_chain('{name}', '{FILE}'), "
                    f"'{module}')}})",
                    "py": f"{py_run}({H}.fake_chain('{name}', file='{FILE}'), '{module}')",
                }
            }
        },
    }


def llm_case(name: str, use: bool) -> dict[str, Any]:
    flag = "TRUE" if use else "FALSE"
    return {
        "id": f"psychds_check.review.{name}.llm_use_{flag.lower()}",
        "r": "base::identity",
        "py": "tests.mod_psychds.parity_support.identity",
        "args": {
            "x": {
                "$expr": {
                    "r": f"local({{{SRC}; psychds_run_llm('{name}', {flag}, '{FILE}')}})",
                    "py": f"{H}.run_llm('{name}', {use}, file='{FILE}')",
                }
            }
        },
    }


def tree_case(name: str) -> dict[str, Any]:
    return {
        "id": f"psychds_tree_html.review.{name}",
        "r": "base::identity",
        "py": "tests.mod_psychds.parity_support.identity",
        "args": {
            "x": {
                "$expr": {
                    "r": f"local({{{SRC}; psychds_tree('{name}', '{FILE}')}})",
                    "py": f"{H}.tree('{name}', file='{FILE}')",
                }
            }
        },
    }


def main() -> None:
    (HERE / "fixtures" / FILE).write_text(
        json.dumps({"chains": CHAINS, "trees": TREES}, indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    cases: list[dict[str, Any]] = [chain_case(name) for name in CHAINS]
    cases += [via_case("exactly_five", "marginal")]
    cases += [llm_case("ext_edge", True), llm_case("ext_edge", False)]
    cases += [tree_case(name) for name in TREES]
    header = (
        "# psychds_check review parity cases (inst/modules/psychds_check.R), generated by\n"
        "# tests/mod_psychds/gen_review_cases.py -- edit that script, not this file.\n"
        "# Each case runs the module on a fake data_check chain built from\n"
        "# tests/mod_psychds/fixtures/review_chains.json (R: tests/mod_psychds/fake_chain.R,\n"
        "# Python: tests/mod_psychds/parity_support.py).\n"
    )
    body = yaml.safe_dump(
        {"area": "mod_psychds_review", "cases": cases},
        sort_keys=False,
        allow_unicode=True,
        default_style='"',
        width=1000,
    )
    (ROOT / "parity" / "cases" / "mod_psychds_review.yaml").write_text(
        header + body, encoding="utf-8"
    )


if __name__ == "__main__":
    main()
