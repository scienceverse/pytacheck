"""Generate ``parity/cases/mod_ethics_review.yaml`` (review cases for ethics_check).

Branches the first pass did not cover: a text table without a ``text``
column, papers without an ``info`` row that still have text, the module
function's own return value (plain lists, named lists, invalid inputs),
duplicated section rows, unsorted and missing ``text_id`` values, whitespace
inside sentences, and a corpus of every readable fixture paper.

Run ``python tests/mod_ethics/make_review_cases.py``, then
``python -m parity generate --area mod_ethics_review``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

try:  # run as a script (python tests/mod_ethics/make_review_cases.py)
    from make_parity_cases import FIX, HELPERS_R, SUPPORT, Dumper, py_list, r_chr
except ImportError:  # imported as tests.mod_ethics.make_review_cases
    from tests.mod_ethics.make_parity_cases import (
        FIX,
        HELPERS_R,
        SUPPORT,
        Dumper,
        py_list,
        r_chr,
    )

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "parity" / "cases" / "mod_ethics_review.yaml"
S = SUPPORT

CASES: list[dict[str, Any]] = []


def expr(r: str, py: str) -> dict[str, Any]:
    return {"$expr": {"r": HELPERS_R + r, "py": py}}


def module_case(id_: str, r: str, py: str, **extra: Any) -> None:
    CASES.append({"id": id_, "module": "ethics_check", "args": {"paper": expr(r, py)}, **extra})


def direct_case(id_: str, r: str, py: str, **extra: Any) -> None:
    """The module function's own return value: ``ethics_check(paper)`` without module_run()."""
    CASES.append(
        {
            "id": id_,
            "r": "identity",
            "py": "tests.mod_ethics.parity_support.identity",
            "args": {"x": expr(f"ec_direct({r})", f"{S}.ec_direct({py})")},
            **extra,
        }
    )


def rp(text: list[str | None], id_: str = "p1") -> str:
    """R: ``ec_paper(text, id)``."""
    return f"ec_paper({r_chr(text)}, id = {json.dumps(id_)})"


def pp(text: list[str | None], id_: str = "p1") -> str:
    """Python: ``ec_paper(text, id)``."""
    return f"{S}.ec_paper({py_list(text)}, id={id_!r})"


def r_sel(paper: str, table: str, cols: list[str]) -> str:
    return f"ec_select({paper}, {json.dumps(table)}, {r_chr(list(cols))})"


def py_sel(paper: str, table: str, cols: list[str]) -> str:
    return f"{S}.ec_select({paper}, {table!r}, {py_list(cols)})"


def r_rows(paper: str, table: str, rows: list[int]) -> str:
    r_rows_vec = "integer(0)" if not rows else "c(" + ", ".join(f"{i}L" for i in rows) + ")"
    return f"ec_rows({paper}, {json.dumps(table)}, {r_rows_vec})"


def py_rows(paper: str, table: str, rows: list[int]) -> str:
    return f"{S}.ec_rows({paper}, {table!r}, {py_list(rows)})"


IRB = "The IRB approved it."
LIVE = "Participants were recruited."
NO_TEXT_COLS = ["text_id", "section_id", "paragraph_id", "formatted"]
FORMATTED_FIRST = ["formatted", "text_id", "section_id", "paragraph_id"]


def add_cases() -> None:
    # -- a text table without a `text` column ---------------------------------
    # text_search() searches the first column and drops `text`; the module adds
    # an empty `text` column after `ethics`
    module_case(
        "ethics_check.no_text_column",
        r_sel(rp([IRB, LIVE]), "text", NO_TEXT_COLS),
        py_sel(pp([IRB, LIVE]), "text", NO_TEXT_COLS),
    )
    # the first column is a string column that matches: R errors assigning
    # character(0) to a table with rows (ethics) / subsetting graphics::text (live)
    module_case(
        "ethics_check.no_text_column_ethics_match",
        r_sel(rp([IRB, LIVE]), "text", FORMATTED_FIRST),
        py_sel(pp([IRB, LIVE]), "text", FORMATTED_FIRST),
    )
    module_case(
        "ethics_check.no_text_column_live_match",
        r_sel(rp(["Nothing.", LIVE]), "text", FORMATTED_FIRST),
        py_sel(pp(["Nothing.", LIVE]), "text", FORMATTED_FIRST),
    )
    # in a list the other paper's text column is kept (NA for this paper)
    module_case(
        "ethics_check.no_text_column_in_list",
        f"paperlist({r_sel(rp([IRB, LIVE], 'a'), 'text', NO_TEXT_COLS)}, {rp([LIVE, IRB], 'b')})",
        f"pc.PaperList([{py_sel(pp([IRB, LIVE], 'a'), 'text', NO_TEXT_COLS)}, "
        f"{pp([LIVE, IRB], 'b')}])",
    )

    # -- papers without an info row but with text -----------------------------
    # their sentences have no paper_id level (NA paper_id rows, sorted last); the
    # list gets a single-paper report and module_run() adds the paper back
    no_info_r = r_rows(rp([IRB, LIVE], "p1"), "info", [])
    no_info_py = py_rows(pp([IRB, LIVE], "p1"), "info", [])
    module_case(
        "ethics_check.no_info_member_with_text",
        f"paperlist({no_info_r}, {rp(['Participants were recruited online.'], 'p2')})",
        f"pc.PaperList([{no_info_py}, {pp(['Participants were recruited online.'], 'p2')}])",
    )
    direct_case(
        "ethics_check.direct_no_info_member_with_text",
        f"paperlist({rp(['The ethics committee approved it.'], 'p2')}, {no_info_r})",
        f"pc.PaperList([{pp(['The ethics committee approved it.'], 'p2')}, {no_info_py}])",
    )
    module_case("ethics_check.no_info_single_with_text", no_info_r, no_info_py)

    # -- the module function's own return value -------------------------------
    direct_case("ethics_check.direct_single_green", rp([LIVE, IRB]), pp([LIVE, IRB]))
    direct_case("ethics_check.direct_single_na", rp(["Nothing."]), pp(["Nothing."]))
    direct_case(
        "ethics_check.direct_paperlist",
        f"paperlist({rp([IRB], 'a')}, {rp([LIVE], 'b')})",
        f"pc.PaperList([{pp([IRB], 'a')}, {pp([LIVE], 'b')}])",
    )
    # R's .is_paper_list(): any list made only of papers
    direct_case(
        "ethics_check.direct_plain_list",
        f"list({rp([IRB], 'a')}, {rp([LIVE], 'b')})",
        f"[{pp([IRB], 'a')}, {pp([LIVE], 'b')}]",
    )
    direct_case(
        "ethics_check.direct_plain_list_one",
        f"list({rp([LIVE, IRB], 'a')})",
        f"[{pp([LIVE, IRB], 'a')}]",
    )
    direct_case(
        "ethics_check.direct_named_list",
        f"list(x = {rp([LIVE], 'b')}, y = {rp([IRB, LIVE], 'a')})",
        f"{{'x': {pp([LIVE], 'b')}, 'y': {pp([IRB, LIVE], 'a')}}}",
    )
    # errors: an empty list (join by a missing paper_id column), a character
    # vector (bind_rows() of character vectors), a table (paper_id()), NULL
    direct_case("ethics_check.direct_empty_list", "list()", "[]")
    direct_case("ethics_check.direct_character", json.dumps(IRB), repr(IRB))
    direct_case("ethics_check.direct_table", f"{rp([IRB])}$text", f"{pp([IRB])}.text")
    direct_case("ethics_check.direct_null", "NULL", "None")

    # -- table order and duplicates -------------------------------------------
    # duplicated section rows: every sentence is joined twice (headers A and B);
    # the statements are unique, the report quotes every ethics row
    dup_r = f'ec_set({r_rows(rp([LIVE, IRB, IRB]), "section", [1, 1])}, "section", "header", c("A", "B"))'
    dup_py = f"{S}.ec_set({py_rows(pp([LIVE, IRB, IRB]), 'section', [1, 1])}, 'section', 'header', ['A', 'B'])"
    module_case("ethics_check.duplicated_sections", dup_r, dup_py)
    direct_case("ethics_check.direct_duplicated_sections", dup_r, dup_py)
    # rows are re-ordered by text_id
    unsorted = [IRB, LIVE, "Ethics approval was obtained.", "We recruited students."]
    module_case(
        "ethics_check.unsorted_text_ids",
        r_rows(rp(unsorted), "text", [4, 2, 3, 1]),
        py_rows(pp(unsorted), "text", [4, 2, 3, 1]),
    )
    # a missing text_id sorts last
    na_ids = [IRB, LIVE, "Ethics approval was obtained.", "Mice were housed in cages."]
    module_case(
        "ethics_check.missing_text_id",
        f'ec_set({rp(na_ids)}, "text", "text_id", c(NA, 3L, 1L, NA))',
        f"{S}.ec_set({pp(na_ids)}, 'text', 'text_id', [None, 3, 1, None])",
    )
    # ties in arrange(paper_id, text_id) keep the order of unique(bind_rows()) of the
    # per-pattern searches: pattern order, not sentence order
    tied = [
        "The experiment followed the Declaration of Helsinki.",
        "An institutional review board approved it.",
        "Ethics approval was obtained.",
        "We recruited students via Prolific.",
        "Participants were recruited.",
    ]
    module_case(
        "ethics_check.tied_text_ids",
        f'ec_set({rp(tied)}, "text", "text_id", c(1L, 1L, 1L, 1L, 1L))',
        f"{S}.ec_set({pp(tied)}, 'text', 'text_id', [1, 1, 1, 1, 1])",
    )
    # the same ethics sentence twice, no live data: the report quotes both rows
    module_case(
        "ethics_check.approved_no_need_repeated",
        rp([IRB, "Nothing here.", IRB]),
        pp([IRB, "Nothing here.", IRB]),
    )
    # the same sentences in two papers (list order not alphabetical)
    module_case(
        "ethics_check.same_text_two_papers",
        f"paperlist({rp([IRB, LIVE], 'b')}, {rp([IRB, LIVE], 'a')})",
        f"pc.PaperList([{pp([IRB, LIVE], 'b')}, {pp([IRB, LIVE], 'a')}])",
    )

    # -- text details ----------------------------------------------------------
    # newlines/tabs inside sentences: matched raw, reported with single spaces
    ws = [
        "This study was approved by the\nethics committee.",
        "Participants\twere   recruited.",
        "Mice were\nhoused in cages.",
    ]
    module_case("ethics_check.whitespace_in_sentences", rp(ws), pp(ws))
    # Unicode in quoted statements
    uni = [
        "Le Comité d’Éthique a approuvé l’étude (n° 2019-É1).",
        "Participants were recruited in Zürich — “volunteers”.",
    ]
    module_case("ethics_check.unicode_statements", rp(uni), pp(uni))
    # live data only in the references: not needed, approved
    CASES.append(
        {
            "id": "ethics_check.live_data_in_references",
            "module": "ethics_check",
            "args": {
                "paper": expr(
                    f'ec_paper({r_chr([LIVE, IRB])}, section_type = c("references", "method"))',
                    f"{S}.ec_paper({py_list([LIVE, IRB])}, section_type=['references', 'method'])",
                )
            },
        }
    )

    # -- every readable fixture paper as one list ------------------------------
    # (unique paper IDs: probe_html.json and 203020.xml repeat probe_docx.json's
    # and 203020.json's IDs, which R refuses as duplicated factor levels)
    corpus = [
        f"{FIX}/{name}"
        for name in (
            "bibr12/PMC4383902.json",
            "bibr12/full.json",
            "bibr12/preprint.json",
            "bibr12/probe_docx.json",
            "formats/apa.xml",
            "formats/preprint.cermine.xml",
            "formats/preprint.pdf.tei.xml",
            "formats/published.cermine.xml",
            "formats/published.pdf.tei.xml",
            "problems/0956797615569889.xml",
            "problems/0956797617737129.xml",
            "problems/203020.json",
        )
    ]
    CASES.append(
        {
            "id": "ethics_check.fixture_corpus",
            "module": "ethics_check",
            "args": {"paper": {"$read": corpus}},
        }
    )


def main() -> None:
    CASES.clear()
    add_cases()
    doc = {"area": "mod_ethics_review", "cases": CASES}
    header = (
        "# Review cases for the ethics_check module (inst/modules/ethics_check.R).\n"
        "# Generated by tests/mod_ethics/make_review_cases.py -- edit that file instead.\n"
    )
    with OUT.open("w", encoding="utf-8") as fh:
        fh.write(header)
        yaml.dump(doc, fh, Dumper=Dumper, sort_keys=False, allow_unicode=True, width=10_000)


if __name__ == "__main__":
    main()
