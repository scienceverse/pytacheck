"""Generate ``parity/cases/mod_funding_review.yaml`` (the adversarial review's cases).

Run ``python tests/mod_funding/make_review_cases.py``, then
``python -m parity generate --area mod_funding_review``.

The cases target branches the first round of ``mod_funding`` cases did not
reach: PCRE caseless matching of non-ASCII letters (Turkish dotted/dotless
i, long s, the Kelvin sign) through the locators' shared column cache, the
100-sentence window and 10-sentence gap of the acknowledgements fallback,
duplicate paper ids, empty paper lists, blank and missing sentences, the
row order of ``funding_check_oi`` and its section preference in paper lists.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_parity_cases import (
    ENV_R,
    IGNORE_IDS,
    LOCATORS,
    NEGATORS,
    SUPPORT,
    WHERE,
    Dumper,
    paper_expr,
    py_list,
    py_list_list,
    q,
    r_chr,
    r_list_chr,
    sectioned,
    test_paper,
)

OUT = Path(__file__).resolve().parents[2] / "parity" / "cases" / "mod_funding_review.yaml"
CASES: list[dict[str, Any]] = []
KELVIN = "K"


def expr_case(id_: str, r: str, py: str, **extra: Any) -> None:
    CASES.append(
        {
            "id": id_,
            "r": "identity",
            "py": "copy.copy",
            "args": {"x": {"$expr": {"r": r, "py": py}}},
            **extra,
        }
    )


def module_case(id_: str, module: str, paper: Any, **extra: Any) -> None:
    CASES.append({"id": id_, "module": module, "args": {"paper": paper}, **extra})


# ---------------------------------------------------------------------------
# PCRE caseless matching of non-ASCII letters
# ---------------------------------------------------------------------------

#: Each article depends on how PCRE2 treats U+0130/U+0131/U+017F/U+212A under
#: (?i) or ignore.case = TRUE; the ones marked * give other results when
#: `regex`'s own case folding is used unchanged.
UNICODE_ARTICLES: list[tuple[str, list[str | None]]] = [
    ("fund_title_dotted", ["FUNDİNG", "The NIH provided money."]),
    ("fund_title_dotless", ["FUNDıNG:", "We were paid."]),
    ("financial_title_dotted", ["FİNANCIAL SUPPORT", "None."]),  # *
    (
        "acknow_financial_aid",  # *
        ["Acknowledgements", "Financial aİd from the NIH.", "References", "1. Smith J (2020)."],
    ),
    ("grant_long_s", ["Aſ grant support: NIH R01"]),  # *
    ("grant_dotted", ["Bİ grant sponsor: NIH"]),
    ("grant_kelvin", ["Awor" + KELVIN + " grant support: NIH"]),  # *
    ("grant_ascii", ["Awork grant support: NIH"]),
    ("grant_title_long_s", ["Grant ſupport: NIH"]),
    ("fund_acknow_dotted", ["This study was fİnanced by NIH."]),  # *
    ("acknow_nih", ["Acknowledgments", "NİH R01 grant was awarded bY them.", "REFERENCES"]),
    ("statement", ["FUNDING", "Nothing.", "Funding Statement İ"]),
]


def add_unicode_cases() -> None:
    fns = [*LOCATORS, *WHERE, *NEGATORS]
    fn_vec = "c(" + ", ".join(q(f) for f in fns) + ")"
    fn_list = "[" + ", ".join(q(f) for f in fns) + "]"
    for id_, article in UNICODE_ARTICLES:
        expr_case(
            f"unicode.locate.{id_}",
            ENV_R + f"fc_apply({fn_vec}, {r_chr(article)})",
            f"{SUPPORT}.apply({fn_list}, {py_list(article)})",
        )
    articles = [a for _, a in UNICODE_ARTICLES]
    expr_case(
        "unicode.funding",
        ENV_R + f"fc_funding({r_list_chr(articles)})",
        f"{SUPPORT}.funding({py_list_list(articles)})",
    )
    # the caseless constructs themselves, through the locators' column cache
    x = ["a", "A", "s", "S", "ſ", KELVIN, "İ", "ı", "1", "_", ".", "é", "k"]
    pats = [
        "(?i)\\w",
        "(?i)^\\W$",
        "(?i)[[:alnum:]]",
        "(?i)[[:alpha:]]",
        "(?i)[[:upper:]]",
        "(?i)[[:lower:]]",
        "(?i)[^[:upper:]]",
        "(?i)[^[:lower:]]",
        "(?i)[[:upper:][:digit:]]",
        "(?i)[^[:alnum:]]",
        "(?i)[[:punct:]]",
        "(?i)[a-zA-Z0-9\\s,()\\[\\]/:-]",
        "(?i)[^a-z]",
        "(?i)[\\w]",
        "(?i)[^\\W]",
        "(?i)\\bs\\b",
        "(?i)[Kk]",
        "(?i)[Ii]",
        "(?i)i",
        "(?i).",
        "(?i)\\W",
        "(?i)[\\W]",
        "(?i)[^\\S]",
        "(?i)[\\S]",
        "(?i)[^\\D]",
        "(?i)[\\d]",
        "(?i)[Ss]",
        "s",
        "[a-z]",
    ]
    pat_vec = "c(" + ", ".join(q(p) for p in pats) + ")"
    pat_list = "[" + ", ".join(q(p) for p in pats) + "]"
    for ic in (False, True):
        expr_case(
            f"unicode.classes.{'ignore_case' if ic else 'inline'}",
            f"lapply({pat_vec}, function(p) grepl(p, {r_chr(x)}, perl = TRUE, "
            f"ignore.case = {'TRUE' if ic else 'FALSE'}))",
            f"{SUPPORT}.masks({pat_list}, {py_list(x)}, ignore_case={ic})",
        )
    # a longer vector: prefilter candidates mixed with rows holding U+0130
    words = ["FİNANCIAL SUPPORT", "Financial support", "fInAnCiAl SuPpOrT", "Financial"]
    column = [f"{w}{sep}" for w in words for sep in ("", ":", ".", " x")] + [None, ""]
    title = "(^F(?i)inancial support(|s)(?-i)(|:|\\.)$)"
    fund = "([Ff]unded\\b|[Ff]inancial support(|s)\\b|NIH (|\\()(?:(R|P))[0-9]{2}|awarded by)"
    for ic in (False, True):
        expr_case(
            f"unicode.column.{'ignore_case' if ic else 'case'}",
            f"lapply(c({q(title)}, {q(fund)}), function(p) grepl(p, {r_chr(column)}, "
            f"perl = TRUE, ignore.case = {'TRUE' if ic else 'FALSE'}))",
            f"{SUPPORT}.masks([{q(title)}, {q(fund)}], {py_list(column)}, ignore_case={ic})",
        )
    for id_ in ("acknow_financial_aid", "grant_long_s", "grant_kelvin", "fund_acknow_dotted"):
        article = dict(UNICODE_ARTICLES)[id_]
        for mod in ("funding_check", "funding_check_oi"):
            module_case(f"{mod}.unicode_{id_}", mod, test_paper(article), compare=IGNORE_IDS)


# ---------------------------------------------------------------------------
# rtransparent_funding(): acknowledgements window and gap boundaries
# ---------------------------------------------------------------------------

FUNDED = "The pilot was funded partly by a charity."
ABSENT = "Financial information: No information about funding was received."


def filler(n: int, tag: str = "Filler") -> list[str]:
    return [f"{tag} sentence number {i}." for i in range(n)]


def window(k: int) -> list[str | None]:
    """Acknowledgements at 1, the references title at k + 2: diff = to - from = k."""
    return ["Acknowledgements", *filler(k - 1), FUNDED, "References"]


def refs_first(pos: int, n: int) -> list[str | None]:
    """References before the acknowledgements (to - from < 0): the funded
    sentence at 1-based position *pos* of an *n*-sentence article."""
    body = ["References", "Acknowledgements", *filler(n - 2)]
    body[pos - 1] = FUNDED
    return body


def gap(g: int) -> list[str | None]:
    """An acknowledgements title at 1 and a negated "Financial information"
    title *g* sentences later (from = min if the gap is <= 10, else max)."""
    return [
        "Acknowledgements",
        "Filler one.",
        FUNDED,
        *filler(g - 3),
        ABSENT,
        "Other text.",
        *filler(3, "Later"),
        "References",
    ]


BOUNDARY_ARTICLES: list[tuple[str, list[str | None]]] = [
    ("window_99", window(99)),
    ("window_100", window(100)),
    ("window_101", window(101)),
    ("refs_first_102_of_120", refs_first(102, 120)),
    ("refs_first_103_of_120", refs_first(103, 120)),
    ("refs_first_last_of_50", refs_first(50, 50)),
    ("refs_first_blank", ["References", "Acknowledgements", "", FUNDED]),
    ("gap_10", gap(10)),
    ("gap_11", gap(11)),
    ("acknow_is_last", ["Intro.", FUNDED, "References", "Acknowledgements"]),
    ("refs_is_acknow", ["Acknowledgements", FUNDED, "Acknowledgements"]),
    ("funding_title_na_next", ["Intro.", "Funding", None, "Text."]),
    ("funding_title_blank_last", ["Intro.", "Funding:", ""]),
    ("acknow_1_blank_na", ["Acknowledgements and funding", "", None]),
    (
        "disclosure_blank_next",
        ["Disclosure", "", "The work was supported by the NIH.", "Disclosures:", "No grants."],
    ),
]


def add_boundary_cases() -> None:
    articles = [a for _, a in BOUNDARY_ARTICLES]
    expr_case(
        "rtransparent_funding.boundaries",
        ENV_R + f"fc_funding({r_list_chr(articles)})",
        f"{SUPPORT}.funding({py_list_list(articles)})",
    )
    fns = [".where_acknows_txt", ".where_refs_txt", "get_disclosure_1", "get_acknow_1"]
    fn_vec = "c(" + ", ".join(q(f) for f in fns) + ")"
    fn_list = "[" + ", ".join(q(f) for f in fns) + "]"
    for id_, article in BOUNDARY_ARTICLES:
        expr_case(
            f"locate.boundary.{id_}",
            ENV_R + f"fc_apply({fn_vec}, {r_chr(article)})",
            f"{SUPPORT}.apply({fn_list}, {py_list(article)})",
        )


# ---------------------------------------------------------------------------
# PCRE resource limits
# ---------------------------------------------------------------------------


def add_limit_cases() -> None:
    long = "No information of funding was " * 20 + ". No information of funding was received"
    expr_case(
        "negate_absence_1.match_limit",
        ENV_R
        + f"fc_env()$negate_absence_1(c({q(long)}, 'No information of funding was received'))",
        f"{SUPPORT}.call('negate_absence_1', [{q(long)}, 'No information of funding was received'])",
        known_divergence=(
            "PCRE2 resource limit in R: on this 640-character sentence grepl(perl = TRUE) stops "
            "with 'match limit exceeded' (a warning) and returns FALSE; the regex engine has no "
            "such limit and finds the match. Only reached by pathologically repetitive text "
            "(no metacheck fixture sentence reaches the limit with any funding pattern)."
        ),
    )


# ---------------------------------------------------------------------------
# Modules: paper lists, ids, blanks, row order, sections
# ---------------------------------------------------------------------------

DUP_R = (
    "local({{p1 <- test_paper({a}); p2 <- test_paper({b}); "
    "p2$paper_id <- p1$paper_id; p2$text$paper_id <- p1$paper_id; paperlist(p1, p2)}})"
)
DUP_PY = (
    "(lambda p1, p2: (p2.__setitem__('paper_id', p1.paper_id), "
    "p2.__setitem__('text', p2['text'].assign(paper_id=p1.paper_id)), "
    "pc.PaperList([p1, p2]))[-1])(pc.test_paper({a}), pc.test_paper({b}))"
)

BLANKS = ["This research was funded by NIH.", None, "", "Funding", "", "We thank NIH."]

TRE_CASELESS = [
    "The fellowſhip was funded.",
    "Our wor" + KELVIN + " was funded.",
    "ſupport came from the study.",
    "1 supports)/:-] fellowſhipsZ/:-]reported",
    "FUNDİNG for the STUDY.",
    "Support came from the study.",
]

ORDER = [
    "The project was funded by X.",
    "We thank the funder for support of this work.",
    "Support for this study came from Y.",
    "The project was funded by X.",
    "Grant funding: none declared by the authors.",
    "Financed by the article processing fund.",
    "Nothing relevant here.",
    "Funding was received for the program.",
]


def add_module_cases() -> None:
    for mod in ("funding_check", "funding_check_oi"):
        a = ["This research was funded by NIH.", "Funding", "", "We thank NIH."]
        b = ["Our study was supported by the ERC."]
        module_case(
            f"{mod}.duplicate_ids",
            mod,
            paper_expr(
                DUP_R.format(a=r_chr(a), b=r_chr(b)), DUP_PY.format(a=py_list(a), b=py_list(b))
            ),
            compare=IGNORE_IDS,
        )
        module_case(f"{mod}.paperlist_empty", mod, paper_expr("paperlist()", "pc.PaperList([])"))
        module_case(f"{mod}.blanks", mod, test_paper(BLANKS), compare=IGNORE_IDS)
        module_case(f"{mod}.order", mod, test_paper(ORDER), compare=IGNORE_IDS)
        module_case(f"{mod}.all_blank", mod, test_paper(["", None, " ", ""]), compare=IGNORE_IDS)
        # text_search()'s TRE ignore.case matches a letter's towupper/towlower
        # forms only: not the long s for "s" or the Kelvin sign for "k"
        module_case(f"{mod}.tre_caseless", mod, test_paper(TRE_CASELESS), compare=IGNORE_IDS)
    sentences = [
        "This work was supported by grant 1.",
        "Our study was funded by the ERC.",
        "Unrelated support for the theory in this paper.",
        "The authors declare no funding.",
    ]
    for id_, types in [
        ("acknowledgement", ["intro", "acknowledgement", "discussion", "acknowledgment"]),
        ("annex", ["annex", "intro", None, "results"]),
        ("funding_na", ["funding", None, None, "funding"]),
        ("references", ["references", "funding", "references", "intro"]),
    ]:
        module_case(
            f"funding_check_oi.sectioned_{id_}",
            "funding_check_oi",
            sectioned(sentences, types),
            compare=IGNORE_IDS,
        )
        module_case(
            f"funding_check.sectioned_{id_}",
            "funding_check",
            sectioned(sentences, types),
            compare=IGNORE_IDS,
        )
    # the section preference applies paper by paper
    t1, t2 = ["intro", "funding", "discussion", "intro"], ["intro", "discussion", "results", None]
    r = (
        ENV_R + f"paperlist(fc_sectioned({r_chr(sentences)}, {r_chr(t1)}), "
        f"fc_sectioned({r_chr(sentences[::-1])}, {r_chr(t2)}))"
    )
    py = (
        f"pc.PaperList([{SUPPORT}.sectioned({py_list(sentences)}, {py_list(t1)}), "
        f"{SUPPORT}.sectioned({py_list(sentences[::-1])}, {py_list(t2)})])"
    )
    for mod in ("funding_check", "funding_check_oi"):
        module_case(f"{mod}.sectioned_paperlist", mod, paper_expr(r, py), compare=IGNORE_IDS)


def main() -> None:
    add_unicode_cases()
    add_boundary_cases()
    add_limit_cases()
    add_module_cases()
    doc = {"area": "mod_funding_review", "cases": CASES}
    with OUT.open("w", encoding="utf-8") as fh:
        fh.write("# Generated by tests/mod_funding/make_review_cases.py -- edit that file.\n")
        yaml.dump(doc, fh, Dumper=Dumper, sort_keys=False, allow_unicode=True, width=10_000)


if __name__ == "__main__":
    main()
