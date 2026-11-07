"""Python side of the FAIR_repo review parity cases (twin of ``review_helpers.R``).

The internal helpers of ``FAIR_repo()``, called one by one. Both sides return the same names and
shapes; an input that is a table or a paper is built here from a ``kind``, in the same way on
both sides. Positions are not involved: the helpers take and return values.
"""

from __future__ import annotations

import datetime
import os
import shutil
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from tests.fair.parity_helpers import (
    ROOT,
    ff_clean,
    ff_fixture_dir,
    ff_paper,
    ff_test_paper,
)


def _md() -> Any:
    from metacheck.fair import metadata

    return metadata


def _rp() -> Any:
    from metacheck.fair import repo

    return repo


# ── repo.py ──────────────────────────────────────────────────────────────────


def fr_join_path(dir: Sequence[str], link: Sequence[str]) -> dict[str, Any]:
    f = _rp()._FAIR_repo_join_path
    return {"value": [f(d, lk) for d, lk in zip(dir, link, strict=True)]}


def fr_sheet_target(
    target: Sequence[str], sheet: Sequence[str], k: Sequence[int]
) -> dict[str, Any]:
    f = _rp()._FAIR_repo_sheet_target
    return {"value": [f(t, s, i) for t, s, i in zip(target, sheet, k, strict=True)]}


def fr_sheet_key() -> dict[str, Any]:
    f = _rp()._FAIR_repo_sheet_key
    cols = pd.DataFrame(
        {
            "source_file": ["a.xlsx [S1]", "a.xlsx [S2]", "b.xlsx", "c.xlsx"],
            "sheet": ["S1", "S2", "Only", "Z"],
        }
    )
    no_sheet = pd.DataFrame({"source_file": ["a.xlsx"]})
    return {
        "value": {
            "several": f("a.xlsx", "S2", cols),
            "single": f("b.xlsx", "Only", cols),
            "no_such_sheet": f("b.xlsx", "Other", cols),
            "wrong_sheet_of_a_single": f("c.xlsx", "S1", cols),
            "no_sheet_column": f("a.xlsx", "S1", no_sheet),
            "no_table": f("a.xlsx", "S1", None),
        }
    }


def fr_setwd(txt: Sequence[str], lang: str = "R", parse: bool = False) -> dict[str, Any]:
    out, n, paths = _rp()._FAIR_repo_neutralise_setwd(list(txt), lang, parse=parse)
    return {"txt": list(out), "n": int(n), "paths": list(paths)}


def fr_plan_paths(kind: str) -> pd.DataFrame:
    if kind == "basic":
        return pd.DataFrame(
            {
                "file_name": ["data/raw.csv", "results/table.csv", "analysis.R"],
                "target_path": [
                    "data/study-a_data.csv",
                    "outputs/table.csv",
                    "analysis/analysis.R",
                ],
                "convert": [False, False, False],
                "original_target": [None, None, None],
                "same_as": [None, None, None],
                "script_writes": [False, True, False],
            }
        )
    if kind == "convert":
        return pd.DataFrame(
            {
                "file_name": ["data/trial.xlsx", "analysis.R"],
                "target_path": ["data/study-a_data.csv", "analysis/analysis.R"],
                "convert": [True, False],
                "original_target": ["documentation/original/trial.xlsx", None],
                "same_as": [None, None],
                "script_writes": [True, False],
            }
        )
    if kind == "ambiguous":
        return pd.DataFrame(
            {
                "file_name": ["a/table.csv", "b/table.csv", "figure.png", "copy/figure.png"],
                "target_path": [
                    "outputs/a/table.csv",
                    "outputs/b/table.csv",
                    "outputs/figure.png",
                    "outputs/figure.png",
                ],
                "convert": [False] * 4,
                "original_target": [None] * 4,
                "same_as": [None, None, None, "outputs/figure.png"],
                "script_writes": [True] * 4,
            }
        )
    if kind == "backslash":
        return pd.DataFrame(
            {
                "file_name": ["results\\table.csv", "analysis.R"],
                "target_path": ["study-a/outputs/table.csv", "study-a/analysis/analysis.R"],
                "convert": [False, False],
                "original_target": [None, None],
                "same_as": [None, None],
                "script_writes": [True, False],
            }
        )
    raise ValueError("unknown kind")


def fr_point_paths(txt: Sequence[str], kind: str) -> dict[str, Any]:
    out, n = _rp()._FAIR_repo_point_to_new_paths(list(txt), fr_plan_paths(kind))
    return {"txt": list(out), "n": int(n)}


def fr_r_data_kind(rel: str) -> dict[str, Any]:
    path = os.path.join(str(ROOT), "tests", "fair", "fixtures", rel)
    return {"value": _rp()._FAIR_repo_r_data_kind(path)}


def fr_no_bom(kind: str) -> dict[str, Any]:
    bom = bytes([0xEF, 0xBB, 0xBF])
    cases = {
        "bom_text": bom + b"id,x\n1,2\n",
        "plain": b"id,x\n1,2\n",
        "empty": b"",
        "bom_only": bom,
        "partial_bom": bytes([0xEF, 0xBB]),
        "bom_in_the_middle": b"a" + bom + b"b",
        "latin1": bytes([0x6E, 0xE9, 0x6D, 0x0A]),
        "two_boms": bom + bom + b"x",
    }
    data = cases[kind]
    d = tempfile.mkdtemp(prefix="nb-")
    try:
        src = os.path.join(d, "in.csv")
        dest = os.path.join(d, "out.csv")
        Path(src).write_bytes(data)
        ok = _rp()._FAIR_repo_copy_no_bom(src, dest)
        hex_ = Path(dest).read_bytes().hex() if os.path.exists(dest) else None
        return {"ok": bool(ok), "hex": hex_}
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── metadata: small helpers ──────────────────────────────────────────────────


def fr_keywords(kind: str) -> dict[str, Any]:
    kinds: dict[str, Any] = {
        "list_cell": [["a", "b"]],
        "vector": ["a", "b"],
        "na_and_blank": ["a", None, "", "b"],
        "nested": ["a", ["b", "c"]],
        "single_string": "solo",
        "empty_chr": [],
        "null": None,
        "all_blank": ["", None],
        "numbers": [1, 2],
        "two_cells": ["a", "b"],
    }
    return {"value": _md()._FAIR_repo_keywords(kinds[kind])}


def fr_is_web(x: Sequence[Any]) -> dict[str, Any]:
    return {"value": [bool(v) for v in _md()._FAIR_repo_is_web(list(x))]}


def fr_encoding_format(x: Sequence[str]) -> dict[str, Any]:
    return {"value": [_md()._FAIR_repo_encoding_format(n) for n in x]}


def fr_software_for(x: Sequence[str]) -> dict[str, Any]:
    return {"value": [_md()._FAIR_repo_software_for(p) for p in x]}


def fr_credit_slug(x: Sequence[Any]) -> dict[str, Any]:
    return {"value": list(_md()._FAIR_repo_credit_slug(list(x)))}


def fr_ddi_array() -> dict[str, Any]:
    f = _md()._FAIR_repo_ddi_array
    return {
        "value": {
            "null": f(None),
            "empty": f([]),
            "na": f(None),
            "blank": f(""),
            "spaces": f("  "),
            "one": f("x"),
            "two": f(["a", "b"]),
            "first_na": f([None, "b"]),
        }
    }


def fr_lang_id(x: Sequence[str]) -> dict[str, Any]:
    return {"value": [_md()._FAIR_repo_lang_id(v) for v in x]}


def fr_id_label(grp: str | None) -> dict[str, Any]:
    md = _md()
    return {"id": md._FAIR_repo_id(grp), "label": md._FAIR_repo_label(grp)}


def fr_roots(kind: str) -> dict[str, Any]:
    written = {
        "none": ["data/x.csv", None],
        "one": ["study-a/data/x.csv", "study-a/analysis/z.R"],
        "two": [
            "study-b/data/x.csv",
            "study-a/data/y.csv",
            "study-a/analysis/z.R",
            None,
            "README.md",
        ],
        "study_word": ["study-guide/notes.txt", "studying/x.csv"],
    }[kind]
    plan = pd.DataFrame({"written": pd.Series(written, dtype=object)})
    return {"value": _md()._FAIR_repo_roots(plan)}


def fr_tree(paths: Sequence[str], max: int = 150) -> dict[str, Any]:
    return {"value": _md()._FAIR_repo_tree(list(paths), max=max)}


def fr_decode_missing(s: Sequence[Any]) -> dict[str, Any]:
    f = _md()._FAIR_repo_decode_missing
    out: list[Any] = []
    for x in s:
        v = f(x)
        out.append(None if v is None else [{"code": c, "reason": r} for c, r in v])
    return {"value": out}


def fr_missing_scheme(kind: str) -> dict[str, Any]:
    labels: pd.DataFrame | None = {
        "pooled": pd.DataFrame(
            {"missing_values": ['["-99","-98"]', '{"-99":"refused"}', None, '["-99"]']}
        ),
        "none_declared": pd.DataFrame({"missing_values": [None, ""]}),
        "no_column": pd.DataFrame({"label": ["x"]}),
        "not_a_table": None,
    }[kind]
    return {"value": _md()._FAIR_repo_missing_scheme_entity(labels)}


def fr_resolve_ref(ref: Sequence[str], group: Sequence[str | None]) -> dict[str, Any]:
    plan = pd.DataFrame(
        {
            "file_name": [
                "data/a.csv",
                "data/b.csv",
                "x/DATA.csv",
                "y/data.csv",
                "z/other.R",
                "w/gone.csv",
            ],
            "written": [
                "study-a/data/a.csv",
                "study-a/data/b.csv",
                "study-a/data/DATA.csv",
                "study-b/data/data.csv",
                "study-a/analysis/other.R",
                None,
            ],
            "group": ["a", "a", "a", "b", "a", "a"],
        }
    )
    f = _md()._FAIR_repo_resolve_ref
    return {"value": [f(r, g, plan) for r, g in zip(ref, group, strict=True)]}


def fr_reused_by_group(kind: str) -> dict[str, Any]:
    if kind == "two_groups":
        plan = pd.DataFrame({"written": ["study-a/data/x.csv", "study-a/data/y.csv", None]})
        plan["referenced_by"] = pd.Series([["b", "c"], ["b"], ["c"]], dtype=object)
    elif kind == "none_referenced":
        plan = pd.DataFrame({"written": ["data/x.csv", "data/y.csv"]})
        plan["referenced_by"] = pd.Series([[], None], dtype=object)
    elif kind == "no_column":
        plan = pd.DataFrame({"written": ["data/x.csv"]})
    else:
        raise ValueError("unknown kind")
    return {"value": _md()._FAIR_repo_reused_by_group(plan)}


# ── metadata: papers ─────────────────────────────────────────────────────────


def fr_paper(kind: str) -> Any:
    if kind == "ff":
        return ff_paper()
    from metacheck.papers.io import test_paper

    p = test_paper(["x"])
    if kind == "none":
        return p
    if kind == "blank":
        p.author = pd.DataFrame(
            {
                "given": pd.Series(["", "Ann", None], dtype="string"),
                "family": pd.Series([None, "", "Lee"], dtype="string"),
                "orcid": pd.Series(
                    ["0000-0002-1825-0097", "https://orcid.org/0000-0002-1825-0098", ""],
                    dtype="string",
                ),
                "email": pd.Series([" ", "e@x.org", None], dtype="string"),
            }
        )
        return p
    if kind == "corresponding":
        p.author = pd.DataFrame(
            {
                "given": pd.Series(["Ann", "Bob"], dtype="string"),
                "family": pd.Series(["Smith", "Jones"], dtype="string"),
                "corresponding": pd.Series([None, True], dtype="boolean"),
            }
        )
        return p
    raise ValueError("unknown kind")


def fr_authors(kind: str) -> dict[str, Any]:
    au = _md()._FAIR_repo_authors(fr_paper(kind))
    return {"value": au.reset_index(drop=True)}


def fr_info(kind: str) -> dict[str, Any]:
    from metacheck.papers.io import test_paper

    doi = {
        "plain": "10.1234/abc",
        "https": "https://doi.org/10.1234/abc",
        "dx": "http://dx.doi.org/10.1234/abc",
        "blank": "",
        "na": pd.NA,
        "absent": None,
    }[kind]
    p = test_paper(["x"])
    if doi is not None:
        p.info["doi"] = pd.Series([doi], dtype="string")
    md = _md()
    return {
        "doi": md._FAIR_repo_info(p, "doi"),
        "doi_url": md._FAIR_repo_doi_url(p),
        "missing_field": md._FAIR_repo_info(p, "no_such_field"),
    }


def fr_info_list() -> dict[str, Any]:
    p = ff_test_paper({"keywords": ["a", "b"], "title": "T"})
    md = _md()
    kw = md._FAIR_repo_info(p, "keywords")
    return {
        "value": {
            "keywords": kw,
            "title": md._FAIR_repo_info(p, "title"),
            "keywords_kept": md._FAIR_repo_keywords(kw),
        }
    }


def fr_dataset_description(kind: str) -> dict[str, Any]:
    from metacheck.papers.io import test_paper

    f = _md()._FAIR_repo_dataset_description
    if kind == "plain":
        d = f(test_paper(["x"]), "A title", "", [])
    elif kind == "study":
        d = f(
            ff_paper(),
            "A title",
            "Study EX1",
            [{"@type": "PropertyValue", "name": "x"}],
            reused=["study-a/data/x.csv", "study-a/data/y.csv"],
        )
    elif kind == "keywords":
        p = ff_test_paper({"title": "K", "doi": "https://doi.org/10.1/abc", "keywords": ["a", "b"]})
        d = f(p, "K", "", [])
    else:
        raise ValueError("unknown kind")
    return {"value": ff_clean(d, [(datetime.date.today().strftime("%Y-%m-%d"), "<date>")])}


def fr_abstract_text(paper: Any, max_chars: int = 1500) -> dict[str, Any]:
    return {"value": _md()._FAIR_repo_abstract_text(paper, max_chars=max_chars)}


def fr_still_unknown(kind: str) -> dict[str, Any]:
    def one_author(name: list[str], corresponding: list[bool], email: list[Any], roles: list[Any]):
        au = pd.DataFrame(
            {
                "name": pd.Series(name, dtype=object),
                "corresponding": pd.Series(corresponding, dtype=object),
                "email": pd.Series(email, dtype=object),
            }
        )
        au["credit"] = pd.Series(roles, dtype=object)
        return au

    if kind == "complete":
        info = {
            "authors": one_author(["Ann Smith"], [True], ["a@x.org"], [["Conceptualization"]]),
            "title_known": True,
            "doi_url": "https://doi.org/10.1/x",
            "licence": "LICENSE",
            "design": [{"dataCollectionMethodology": ["online"]}],
        }
    elif kind == "nothing":
        info = {
            "authors": one_author([], [], [], []),
            "title_known": False,
            "doi_url": None,
            "licence": None,
            "design": [{"dataCollectionMethodology": ["unknown"]}],
        }
    elif kind == "roles_missing":
        info = {
            "authors": one_author(
                ["Ann Smith", "Bob Jones"], [True, False], [None, "b@x.org"], [[], ["Software"]]
            ),
            "title_known": True,
            "doi_url": "https://doi.org/10.1/x",
            "licence": None,
            "design": [
                {"dataCollectionMethodology": ["online"]},
                {"dataCollectionMethodology": ["unknown"]},
            ],
        }
    else:
        raise ValueError("unknown kind")
    return {"value": _md()._FAIR_repo_still_unknown(info)}


def fr_drop_local(kind: str) -> dict[str, Any]:
    folder = "/home/u/proj"
    f = _md()._FAIR_repo_drop_local
    if kind == "list":
        x = f(
            {
                "file_location": "/home/u/proj/a.R",
                "repo_url": "/home/u/proj",
                "file_url": "https://osf.io/x",
                "sandbox": "/tmp/sbx",
                "note": "see /home/u/proj/data/a.csv",
                "n": 3,
                "nested": {
                    "file_location": "/x",
                    "version_pin": {"file_location": "/y", "pkg": "z"},
                },
                "report": ["ran /home/u/proj/a.R", "ok"],
            },
            folder=folder,
        )
    elif kind == "frame":
        x = f(
            pd.DataFrame(
                {
                    "file_location": ["/a", "/b"],
                    "repo_url": ["/local", "https://github.com/a"],
                    "text": ["x /home/u/proj/y", "z"],
                    "n": [1, 2],
                }
            ),
            folder=folder,
        )
    elif kind == "windows":
        x = f(
            ["C:\\Users\\u\\proj\\a.R and C:/Users/u/proj/b.R", "none"],
            folder="C:/Users/u/proj",
        )
    elif kind == "text":
        x = f(["a /home/u/proj/x /home/u/proj/y", None], folder=folder)
    elif kind == "no_folder":
        x = f({"file_location": "/a", "note": "/home/u/proj/x"}, folder=None)
    elif kind == "web_urls":
        x = f({"repo_url": "http://a.org/x", "file_url": ["https://b.org", "/c"]}, folder=folder)
    else:
        raise ValueError("unknown kind")
    return {"value": x}


def fr_variable_measured(kind: str) -> dict[str, Any]:
    f = _md()._FAIR_repo_variable_measured
    nan = None
    if kind == "numeric_and_empty":
        cols = pd.DataFrame(
            {
                "source_file": ["data/a.csv"] * 3,
                "column_name": ["age", "empty_col", "grp"],
                "quality": [nan, "empty", nan],
                "representation": ["numeric", nan, "text"],
                "measurement_level": ["ratio", nan, "nominal"],
                "min": [18.0, np.nan, np.nan],
                "max": [65.0, np.nan, np.nan],
                "n": [100.0, np.nan, 100.0],
                "n_missing": [0.0, np.nan, 0.0],
                "mean": [35.5, np.nan, np.nan],
                "sd": [10.25, np.nan, np.nan],
                "median": [34.0, np.nan, np.nan],
                "sample_values": [nan, nan, "a | b |  | a"],
            }
        )
        return {"value": f(cols, None, {"data/a.csv": "study-a/data/a.csv"})}
    if kind == "categorical":
        cols = pd.DataFrame(
            {
                "source_file": ["data/a.csv"] * 3,
                "column_name": ["cond", "wave", "code"],
                "representation": ["text", "text", "numeric"],
                "measurement_level": ["nominal", "ordinal", "nominal"],
                "sample_values": ["ctrl|treat|ctrl", " low | high |", "1|2"],
            }
        )
        return {"value": f(cols)}
    if kind == "labelled":
        cols = pd.DataFrame(
            {
                "source_file": ["data/a.csv"] * 4,
                "column_name": ["q0", "q1", "q2", "q3"],
                "representation": ["text", "numeric", "numeric", "text"],
                "measurement_level": ["nominal", "ordinal", "ordinal", nan],
                "min": [np.nan, 1.0, 1.0, np.nan],
                "max": [np.nan, 5.0, 5.0, np.nan],
                "mean": [np.nan, 3.0, 2.5, np.nan],
            }
        )
        labels = pd.DataFrame(
            {
                "source_file": ["data/a.csv"] * 4,
                "column_name": ["q2", "q1", "q3", "q1"],
                "label": ["Second", "First", "Third", "Duplicate"],
                "label_status": ["llm", "labelled", "none", "labelled"],
                "label_source": ["llm", "codebook", nan, "codebook"],
                "codebook_variable": ["Q2", "Q1", nan, "Q1"],
                "scale": ["Scale B", "Scale A", nan, nan],
                "scale_source": ["codebook", "codebook", nan, nan],
                "scale_confidence": ["high", "medium", nan, nan],
                "value_labels": [
                    '{"1":"disagree","5":"agree"}',
                    '{"1":"never","5":"always"}',
                    nan,
                    nan,
                ],
                "missing_values": ['["-99"]', '{"-99":"refused"}', nan, nan],
                "question": ["How often?", nan, nan, nan],
            }
        )
        return {"value": f(cols, labels)}
    raise ValueError("unknown kind")


# ── metadata: files ──────────────────────────────────────────────────────────


def fr_readme_title(written: Sequence[str] | str, fixture: str = "review_files") -> dict[str, Any]:
    w = [written] if isinstance(written, str) else list(written)
    return {"value": _md()._FAIR_repo_readme_title(w, str(ff_fixture_dir(fixture)))}


def fr_write_json(kind: str) -> dict[str, Any]:
    objs: dict[str, Any] = {
        "scalars": {
            "@id": "x",
            "n": 3,
            "ratio": 1.5,
            "ok": True,
            "nothing": None,
            "name": "\u00e9",
        },
        "numbers": {
            "a": 1 / 3,
            "b": 2 / 3,
            "c": 1e-5,
            "d": 1e5,
            "e": 123456789.0,
            "f": -0.5,
            "g": 1234567.891,
            "h": 100000,
            "i": 0.0,
            "j": 1e15,
            "k": 0.1 + 0.2,
            "l": 22 / 7 * 1000,
        },
        "nested": {
            "a": {"b": {"c": 1, "d": "x"}, "e": [1, 2, [3]]},
            "f": [{"g": 1}, {"g": 2}],
        },
        "empty_containers": {"a": [], "b": {}, "c": [], "d": [[]], "e": ""},
        "vectors": {"a": [1, 2, 3], "b": ["x", "y"], "c": "single", "d": ["x"], "e": [True, False]},
        "strings": {
            "tab": "a\tb",
            "quote": 'say "hi"',
            "back": "a\\b",
            "newline": "a\nb",
            "unicode": "\u00e9 \u00fc \u65e5\u672c",
            "slash": "a/b",
            "ctrl": "a\x01b",
            "emoji": "\U0001f600",
            "empty": "",
        },
        "na_values": {
            "a": None,
            "b": None,
            "c": None,
            "d": [1.0, None],
            "e": ["x", None],
            "f": [None],
        },
    }
    d = tempfile.mkdtemp(prefix="wj-")
    try:
        p = os.path.join(d, "sub", "x.json")
        _md()._FAIR_repo_write_json(objs[kind], p)
        raw = Path(p).read_bytes()
        text = raw.decode("utf-8")
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        return {"lines": lines, "size": len(raw)}
    finally:
        shutil.rmtree(d, ignore_errors=True)
