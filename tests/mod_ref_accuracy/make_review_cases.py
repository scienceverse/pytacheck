"""Write ``parity/cases/mod_ref_accuracy_review.yaml`` (the adversarial-review cases).

Each case edits the demo paper with the helpers in ``tests/mod_ref_accuracy/edits.R``
(R) and ``tests/mod_ref_accuracy/edits.py`` (Python, bound to ``E`` below), aimed at
branches the first round of cases did not reach: character years parsed by
``as.numeric()``, character author records, extreme argument values, DOI URLs,
missing record DOIs, line breaks in report tables, incompatible join keys (a dplyr
error), unsorted paper lists and odd cross-references.

    .venv/bin/python tests/mod_ref_accuracy/make_review_cases.py
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "parity" / "cases" / "mod_ref_accuracy_review.yaml"

R_PRE = 'source(file.path(root, "tests/mod_ref_accuracy/edits.R")); '
PY_WRAP = '(lambda E: {})(__import__("tests.mod_ref_accuracy.edits", fromlist=["x"]))'

SUGGEST_R = 'ra_demo(list(list("bib", 0L, "doi", ""), list("bib", 4L, "doi", ""), list("bib_match", 3L, "title", "")))'
SUGGEST_PY = (
    "E.ra_demo([('bib', 0, 'doi', ''), ('bib', 4, 'doi', ''), ('bib_match', 3, 'title', '')])"
)

# (id, module, R paper expression, Python paper expression, extra args, report tables?)
CASES: list[tuple[str, str, str, str, dict[str, Any], bool]] = [
    # ---- ref_accuracy -------------------------------------------------------
    (
        "ref_accuracy.year.character",
        "ref_accuracy",
        'ra_setcol(ra_demo(), "bib", "year", c("2025", "１９９０", "0x7DE", "0x1.f8p10", " 2021\\n"))',
        "E.ra_setcol(E.ra_demo(), 'bib', 'year', ['2025', '１９９０', '0x7DE', '0x1.f8p10', ' 2021\\n'], 'string')",
        {},
        True,
    ),
    (
        "ref_accuracy.year.character_ws",
        "ref_accuracy",
        'ra_setcol(ra_demo(), "bib", "year", c("2025", "1990 ", " 2014", "2018\\f", "in press"))',
        "E.ra_setcol(E.ra_demo(), 'bib', 'year', ['2025', '1990 ', ' 2014', '2018\\f', 'in press'], 'string')",
        {},
        False,
    ),
    (
        "ref_accuracy.authors.character_column",
        "ref_accuracy",
        'ra_setcol(ra_demo(), "bib_match", "authors", c("Eagly, A; Wood, W", "Nobody, X", NA))',
        "E.ra_setcol(E.ra_demo(), 'bib_match', 'authors', ['Eagly, A; Wood, W', 'Nobody, X', None], 'string')",
        {},
        True,
    ),
    (
        "ref_accuracy.min_mismatches_0",
        "ref_accuracy",
        "ra_demo()",
        "E.ra_demo()",
        {"min_mismatches": 0},
        True,
    ),
    (
        "ref_accuracy.max_authors_negative",
        "ref_accuracy",
        'ra_demo(list(list("bib", 1L, "authors", "Nobody, X")))',
        "E.ra_demo([('bib', 1, 'authors', 'Nobody, X')])",
        {"max_authors": -1},
        True,
    ),
    (
        "ref_accuracy.max_authors_0",
        "ref_accuracy",
        'ra_demo(list(list("bib", 1L, "authors", "Nobody, X")))',
        "E.ra_demo([('bib', 1, 'authors', 'Nobody, X')])",
        {"max_authors": 0},
        False,
    ),
    (
        "ref_accuracy.title_similarity_1",
        "ref_accuracy",
        "ra_demo()",
        "E.ra_demo()",
        {"title_similarity": 1.0},
        True,
    ),
    (
        "ref_accuracy.year_tolerance_negative",
        "ref_accuracy",
        "ra_demo()",
        "E.ra_demo()",
        {"year_tolerance": -1},
        True,
    ),
    (
        "ref_accuracy.suggest.boundary",
        "ref_accuracy",
        SUGGEST_R,
        SUGGEST_PY,
        {"suggest_score": 61.8},
        True,
    ),
    (
        "ref_accuracy.suggest.zero",
        "ref_accuracy",
        'ra_demo(list(list("bib", 0L, "doi", ""), list("bib", 4L, "doi", ""), list("bib_match", 3L, "title", ""), list("bib_match", 3L, "doi", "")))',
        "E.ra_demo([('bib', 0, 'doi', ''), ('bib', 4, 'doi', ''), ('bib_match', 3, 'title', ''), ('bib_match', 3, 'doi', '')])",
        {"suggest_score": 0},
        True,
    ),
    (
        "ref_accuracy.doi.url",
        "ref_accuracy",
        'ra_demo(list(list("bib", 0L, "doi", "https://doi.org/10.5281/zenodo.2669586"), list("bib", 2L, "doi", "HTTPS://doi.org/10.1177/0956797614520714")))',
        "E.ra_demo([('bib', 0, 'doi', 'https://doi.org/10.5281/zenodo.2669586'), ('bib', 2, 'doi', 'HTTPS://doi.org/10.1177/0956797614520714')])",
        {},
        True,
    ),
    (
        "ref_accuracy.doi.record_missing",
        "ref_accuracy",
        'ra_demo(list(list("bib_match", 2L, "doi", NA), list("bib", 2L, "title", "Zzz totally different"), list("bib_match", 3L, "doi", ""), list("bib", 3L, "doi", "10.1177/2515245918770963"), list("bib", 3L, "year", 2010L)))',
        "E.ra_demo([('bib_match', 2, 'doi', None), ('bib', 2, 'title', 'Zzz totally different'), ('bib_match', 3, 'doi', ''), ('bib', 3, 'doi', '10.1177/2515245918770963'), ('bib', 3, 'year', 2010)])",
        {},
        True,
    ),
    (
        "ref_accuracy.report.line_breaks",
        "ref_accuracy",
        'ra_text(ra_text(ra_demo(list(list("bib", 2L, "title", "Zzz"), list("bib", 4L, "doi", ""))), 35L, "Gino, F.\\nEvil genius.\\n"), 37L, "Line one\\nline two")',
        "E.ra_text(E.ra_text(E.ra_demo([('bib', 2, 'title', 'Zzz'), ('bib', 4, 'doi', '')]), 35, 'Gino, F.\\nEvil genius.\\n'), 37, 'Line one\\nline two')",
        {},
        True,
    ),
    (
        "ref_accuracy.authors.metacharacters",
        "ref_accuracy",
        'ra_demo(list(list("bib_match", 1L, "authors", data.frame(given = c("A", "B", "C", "D", "E"), family = c("O\'Brien (Jr.)", "Smith+Jones", "Ng.", "[x]", "-"))), list("bib", 1L, "authors", "O’Brien (Jr.), A; Smith+Jones, B; Ng., C; [x], D"), list("bib_match", 2L, "authors", data.frame(given = c("F", "S"), family = c("Gino", "Wilter.muth")))))',
        "E.ra_demo([('bib_match', 1, 'authors', [{'given': g, 'family': f} for g, f in zip('ABCDE', [\"O'Brien (Jr.)\", 'Smith+Jones', 'Ng.', '[x]', '-'])]), ('bib', 1, 'authors', 'O’Brien (Jr.), A; Smith+Jones, B; Ng., C; [x], D'), ('bib_match', 2, 'authors', [{'given': 'F', 'family': 'Gino'}, {'given': 'S', 'family': 'Wilter.muth'}])])",
        {},
        True,
    ),
    (
        "ref_accuracy.journal.whitespace",
        "ref_accuracy",
        'ra_demo(list(list("bib", 1L, "container", "\\t"), list("bib", 2L, "container", " "), list("bib_match", 3L, "container", "Psychological Science"), list("bib", 3L, "doi", "10.1177/2515245918770963"), list("bib", 3L, "container", "Adv. Methods  Pract.\\nPsychol. Sci.")))',
        "E.ra_demo([('bib', 1, 'container', '\\t'), ('bib', 2, 'container', ' '), ('bib_match', 3, 'container', 'Psychological Science'), ('bib', 3, 'doi', '10.1177/2515245918770963'), ('bib', 3, 'container', 'Adv. Methods  Pract.\\nPsychol. Sci.')])",
        {},
        False,
    ),
    (
        "ref_accuracy.title.empty_after_normalising",
        "ref_accuracy",
        'ra_demo(list(list("bib", 1L, "title", "—?!"), list("bib_match", 2L, "title", "<i></i>"), list("bib", 3L, "doi", "10.1177/2515245918770963"), list("bib_match", 3L, "title", "<b>")))',
        "E.ra_demo([('bib', 1, 'title', '—?!'), ('bib_match', 2, 'title', '<i></i>'), ('bib', 3, 'doi', '10.1177/2515245918770963'), ('bib_match', 3, 'title', '<b>')])",
        {},
        False,
    ),
    (
        "ref_accuracy.authors.cited_missing",
        "ref_accuracy",
        'ra_demo(list(list("bib", 1L, "authors", NA), list("bib", 2L, "authors", "   ")))',
        "E.ra_demo([('bib', 1, 'authors', None), ('bib', 2, 'authors', '   ')])",
        {},
        True,
    ),
    (
        "ref_accuracy.paperlist.unsorted",
        "ref_accuracy",
        'paperlist(ra_demo(paper_id = "zeta"), ra_demo(list(list("bib", 1L, "container", "Other Journal")), paper_id = "Alpha"), ra_test_paper("No refs.", "beta"), ra_demo(drop = "bib_match", paper_id = "_first"))',
        "pc.PaperList([E.ra_demo(paper_id='zeta'), E.ra_demo([('bib', 1, 'container', 'Other Journal')], paper_id='Alpha'), E.ra_test_paper('No refs.', 'beta'), E.ra_demo(drop=['bib_match'], paper_id='_first')])",
        {},
        True,
    ),
    (
        "ref_accuracy.bib_match.extra_rows",
        "ref_accuracy",
        "ra_match_copy(ra_match_copy(ra_demo(), 1L, 99L), 2L, NA_integer_)",
        "E.ra_match_copy(E.ra_match_copy(E.ra_demo(), 1, 99), 2, None)",
        {},
        False,
    ),
    (
        "ref_accuracy.bib_match.double_key",
        "ref_accuracy",
        'ra_setcol(ra_demo(), "bib_match", "bib_id", c(1, 2, 3))',
        "E.ra_setcol(E.ra_demo(), 'bib_match', 'bib_id', [1.0, 2.0, 3.0], 'float64')",
        {},
        False,
    ),
    (
        "ref_accuracy.error.no_score",
        "ref_accuracy",
        'ra_setcol(ra_demo(), "bib_match", "score", NULL)',
        "E.ra_setcol(E.ra_demo(), 'bib_match', 'score', None)",
        {},
        False,
    ),
    (
        "ref_accuracy.error.character_key",
        "ref_accuracy",
        'ra_setcol(ra_demo(), "bib_match", "bib_id", c("1", "2", "3"))',
        "E.ra_setcol(E.ra_demo(), 'bib_match', 'bib_id', ['1', '2', '3'], 'string')",
        {},
        False,
    ),
    (
        "ref_accuracy.error.no_text",
        "ref_accuracy",
        'ra_demo(drop = "text")',
        "E.ra_demo(drop=['text'])",
        {},
        False,
    ),
    # ---- ref_consistency ----------------------------------------------------
    (
        "ref_consistency.error.character_xref_id",
        "ref_consistency",
        'ra_setcol(ra_demo(), "xref", "xref_id", c("2", "0", "0", NA, "1"))',
        "E.ra_setcol(E.ra_demo(), 'xref', 'xref_id', ['2', '0', '0', None, '1'], 'string')",
        {},
        False,
    ),
    (
        "ref_consistency.error.character_text_id",
        "ref_consistency",
        'ra_setcol(ra_demo(), "xref", "text_id", c("4", "8", "17", "25", "25"))',
        "E.ra_setcol(E.ra_demo(), 'xref', 'text_id', ['4', '8', '17', '25', '25'], 'string')",
        {},
        False,
    ),
    (
        "ref_consistency.error.no_xref",
        "ref_consistency",
        'ra_demo(drop = "xref")',
        "E.ra_demo(drop=['xref'])",
        {},
        False,
    ),
    (
        "ref_consistency.error.no_text",
        "ref_consistency",
        'ra_demo(drop = "text")',
        "E.ra_demo(drop=['text'])",
        {},
        False,
    ),
    (
        "ref_consistency.double_key",
        "ref_consistency",
        'ra_setcol(ra_demo(), "xref", "xref_id", c(2, 0, 0, NA, 1))',
        "E.ra_setcol(E.ra_demo(), 'xref', 'xref_id', [2.0, 0.0, 0.0, None, 1.0], 'float64')",
        {},
        True,
    ),
    (
        "ref_consistency.na_with_xrefs",
        "ref_consistency",
        'ra_xrefs(c(NA, 1L), c("(Ghost 2020)", "(Eagly 1999)"), paper = ra_demo(empty = "bib"))',
        "E.ra_xrefs([None, 1], ['(Ghost 2020)', '(Eagly 1999)'], paper=E.ra_demo(empty=['bib']))",
        {},
        True,
    ),
    (
        "ref_consistency.contents_na",
        "ref_consistency",
        "ra_xrefs(c(0L, NA), c(NA, NA))",
        "E.ra_xrefs([0, None], [None, None])",
        {},
        True,
    ),
    (
        "ref_consistency.xref_type_na",
        "ref_consistency",
        'ra_xrefs(c(NA, NA), c("(X)", "(Y)"), xref_type = NA)',
        "E.ra_xrefs([None, None], ['(X)', '(Y)'], xref_type=None)",
        {},
        False,
    ),
    (
        "ref_consistency.text_missing",
        "ref_consistency",
        'ra_xrefs(c(NA), c("(Ghost)"), text_id = 9999L)',
        "E.ra_xrefs([None], ['(Ghost)'], text_id=9999)",
        {},
        True,
    ),
    (
        "ref_consistency.paperlist.unsorted",
        "ref_consistency",
        'paperlist(ra_xrefs(c(NA), c("(Ghost)"), paper = ra_demo(paper_id = "zeta")), ra_demo(paper_id = "Alpha"), ra_demo(drop = "bib_match", paper_id = "beta"), ra_demo(paper_id = "_first"))',
        "pc.PaperList([E.ra_xrefs([None], ['(Ghost)'], paper=E.ra_demo(paper_id='zeta')), E.ra_demo(paper_id='Alpha'), E.ra_demo(drop=['bib_match'], paper_id='beta'), E.ra_demo(paper_id='_first')])",
        {},
        True,
    ),
    (
        "ref_consistency.paperlist.no_bib_match",
        "ref_consistency",
        'paperlist(ra_demo(drop = "bib_match", paper_id = "zeta"), ra_xrefs(c(NA), c("(Ghost)"), paper = ra_demo(drop = "bib_match", paper_id = "Alpha")))',
        "pc.PaperList([E.ra_demo(drop=['bib_match'], paper_id='zeta'), E.ra_xrefs([None], ['(Ghost)'], paper=E.ra_demo(drop=['bib_match'], paper_id='Alpha'))])",
        {},
        False,
    ),
    (
        "ref_consistency.duplicate_match",
        "ref_consistency",
        "ra_demo(dup_match = c(2L, 3L))",
        "E.ra_demo(dup_match=[2, 3])",
        {},
        False,
    ),
    (
        "ref_consistency.paperlist.all_empty_bib",
        "ref_consistency",
        'paperlist(ra_demo(empty = "bib", paper_id = "a"), ra_xrefs(c(NA), c("(Ghost)"), paper = ra_demo(empty = "bib", paper_id = "b")))',
        "pc.PaperList([E.ra_demo(empty=['bib'], paper_id='a'), E.ra_xrefs([None], ['(Ghost)'], paper=E.ra_demo(empty=['bib'], paper_id='b'))])",
        {},
        False,
    ),
]


_FIX = "upstream/metacheck/tests/testthat/fixtures/"
_XML = [
    _FIX + "debruine/" + f
    for f in ("debruine-child.xml", "debruine-fret.xml", "debruine-sex.xml", "debruine-tnl.xml")
] + [
    _FIX + "formats/" + f
    for f in (
        "apa.xml",
        "preprint.pdf.tei.xml",
        "published.pdf.tei.xml",
        "preprint.cermine.xml",
        "published.cermine.xml",
    )
]

# cases on real imported papers (no edits)
PLAIN_CASES: list[dict[str, Any]] = [
    {
        "id": "ref_consistency.xml_fixtures",
        "module": "ref_consistency",
        "args": {"paper": {"$read": _XML}},
    },
    {
        "id": "ref_accuracy.xml_fixtures",
        "module": "ref_accuracy",
        "args": {"paper": {"$read": _XML}},
    },
    {
        "id": "ref_accuracy.problems_203020",
        "module": "ref_accuracy",
        "args": {"paper": {"$paper": _FIX + "problems/203020.json"}},
    },
    {
        "id": "ref_consistency.problems_203020",
        "module": "ref_consistency",
        "args": {"paper": {"$paper": _FIX + "problems/203020.json"}},
    },
] + [
    # module_help() text: title, usage with defaults, @param texts (R keeps
    # their roxygen line breaks), details
    {
        "id": f"{m}.module_help",
        "r": "identity",
        "py": "copy.copy",
        "args": {
            "x": {
                "$expr": {
                    "r": f'paste(capture.output(print(module_help("{m}"))), collapse = "\\n")',
                    "py": f'pc.module_help("{m}")',
                }
            }
        },
    }
    for m in ("ref_accuracy", "ref_consistency")
]


def _r_args(args: dict[str, Any]) -> str:
    """Extra module arguments as R code (``, name = value``)."""
    out = []
    for k, v in args.items():
        out.append(f", {k} = {v}L" if isinstance(v, int) else f", {k} = {v!r}")
    return "".join(out)


def build() -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for cid, module, r_code, py_code, args, tables in CASES:
        cases.append(
            {
                "id": cid,
                "module": module,
                "args": {
                    "paper": {"$expr": {"r": R_PRE + r_code, "py": PY_WRAP.format(py_code)}},
                    **args,
                },
            }
        )
        if tables:
            py_args = "".join(f", {k}={v!r}" for k, v in args.items())
            cases.append(
                {
                    "id": cid.replace(module + ".", module + ".report_tables.", 1),
                    "r": "identity",
                    "py": "copy.copy",
                    "args": {
                        "x": {
                            "$expr": {
                                "r": R_PRE
                                + f'ra_report_tables(module_run({r_code}, "{module}"{_r_args(args)}))',
                                "py": PY_WRAP.format(
                                    f"E.ra_report_tables(pc.module_run({py_code}, "
                                    f"{module!r}{py_args}))"
                                ),
                            }
                        }
                    },
                }
            )
    return {"area": "mod_ref_accuracy_review", "cases": cases + PLAIN_CASES}


def main() -> None:
    header = (
        "# Adversarial-review parity cases for the ref_accuracy and ref_consistency modules.\n"
        "# Generated by tests/mod_ref_accuracy/make_review_cases.py (edit that, not this).\n"
    )
    body = yaml.dump(build(), default_style='"', allow_unicode=True, sort_keys=False, width=100000)
    OUT.write_text(header + body, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(build()['cases'])} cases)")


if __name__ == "__main__":
    main()
