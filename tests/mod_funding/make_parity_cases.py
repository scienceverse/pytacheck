"""Generate ``parity/cases/mod_funding.yaml`` (R and Python snippets side by side).

Run ``python tests/mod_funding/make_parity_cases.py``, then
``python -m parity generate --area mod_funding``.

Internal helpers of ``inst/modules/funding_check.R`` are reached through
``tests/mod_funding/funding_env.R`` (R) and ``tests/mod_funding/parity_support.py``
(Python).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

OUT = Path(__file__).resolve().parents[2] / "parity" / "cases" / "mod_funding.yaml"
SUPPORT = "__import__('tests.mod_funding.parity_support', fromlist=['x'])"
ENV_R = 'source(file.path(root, "tests/mod_funding/funding_env.R")); '
FIXTURES = "upstream/metacheck/tests/testthat/fixtures"
IGNORE_IDS = {"ignore": ["paper_id", "table.paper_id", "summary_table.paper_id"]}


class Dumper(yaml.SafeDumper):
    pass


def _str(d: yaml.SafeDumper, s: str) -> yaml.Node:
    return d.represent_scalar("tag:yaml.org,2002:str", s, style='"')


Dumper.add_representer(str, _str)


def q(s: str | None) -> str:
    """A string literal valid in both R and Python (JSON escapes)."""
    return "NA" if s is None else json.dumps(s)


def r_chr(values: list[str | None]) -> str:
    if not values:
        return "character(0)"
    return "c(" + ", ".join(q(v) for v in values) + ")"


def py_list(values: list[Any]) -> str:
    return "[" + ", ".join("None" if v is None else q(v) for v in values) + "]"


def r_list_chr(articles: list[list[str | None]]) -> str:
    return "list(" + ", ".join(r_chr(a) for a in articles) + ")"


def py_list_list(articles: list[list[str | None]]) -> str:
    return "[" + ", ".join(py_list(a) for a in articles) + "]"


CASES: list[dict[str, Any]] = []


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


def paper_expr(r: str, py: str) -> dict[str, Any]:
    return {"$expr": {"r": r, "py": py}}


# ---------------------------------------------------------------------------
# helpers: pattern construction (every pattern must be byte-identical to R's)
# ---------------------------------------------------------------------------

LOCATORS = [
    "get_support_1",
    "get_support_3",
    "get_support_4",
    "get_support_5",
    "get_support_6",
    "get_support_7",
    "get_support_8",
    "get_support_9",
    "get_support_10",
    "get_developed_1",
    "get_received_1",
    "get_received_2",
    "get_recipient_1",
    "get_authors_1",
    "get_authors_2",
    "get_thank_1",
    "get_thank_2",
    "get_fund_1",
    "get_fund_2",
    "get_fund_3",
    "get_fund_acknow",
    "get_supported_1",
    "get_financial_1",
    "get_financial_2",
    "get_financial_3",
    "get_disclosure_1",
    "get_disclosure_2",
    "get_grant_1",
    "get_french_1",
    "get_project_acknow",
    "get_common_1",
    "get_common_2",
    "get_common_3",
    "get_common_4",
    "get_common_5",
    "get_acknow_1",
    "get_acknow_2",
]
# the rtransparent helpers funding_check() uses (get_support_2, get_fund_acknow_new,
# negate_disclosure_*, negate_conflict_1, .where_methods_txt and the obliterate_*
# cleaners are not called, and not ported)
NEGATORS = ["negate_absence_1"]
WHERE = [".where_refs_txt", ".where_acknows_txt"]
PATTERN_FNS = [*LOCATORS, *NEGATORS, *WHERE]


def add_pattern_cases() -> None:
    calls = ", ".join(
        f"{q(fn)} = {r_chr(['Disclosure', 'funding'] if fn == 'get_disclosure_1' else ['x'])}"
        for fn in PATTERN_FNS
    )
    expr_case(
        "helpers.patterns",
        ENV_R + f"fc_patterns(list({calls}))",
        f"{SUPPORT}.patterns({py_list(PATTERN_FNS)})",
    )
    expr_case(
        "helpers.create_synonyms",
        ENV_R + "fc_env()$.create_synonyms()",
        f"{SUPPORT}.call('.create_synonyms')",
    )
    small = [
        ("encase", ".encase", r_chr(["a", "b|c", "d"]), py_list(["a", "b|c", "d"])),
        ("encase_one", ".encase", q("x"), py_list(["x"])),
        ("bound_end", ".bound", r_chr(["a", "b"]), py_list(["a", "b"])),
        ("bound_both", ".bound", r_chr(["a", "b"]) + ', "both"', py_list(["a", "b"]) + ', "both"'),
        ("bound_start", ".bound", r_chr(["a"]) + ', "start"', py_list(["a"]) + ', "start"'),
        ("bound_null", ".bound", "NULL", "[]"),
        ("bound_null_both", ".bound", 'NULL, "both"', '[], "both"'),
        ("max_words", ".max_words", q("(a|b)"), q("(a|b)")),
        (
            "max_words_before",
            ".max_words",
            q(" ") + ", n_max = 5, space_first = FALSE",
            q(" ") + ", n_max=5, space_first=False",
        ),
        ("title", ".title", r_chr(["A", "B"]), py_list(["A", "B"])),
        ("title_within", ".title", q("A") + ", within_text = TRUE", q("A") + ", within_text=True"),
    ]
    for id_, fn, r_args, py_args in small:
        expr_case(
            f"helpers.{id_}",
            ENV_R + f"fc_env()${fn}({r_args})",
            f"{SUPPORT}.call({q(fn)}, {py_args})",
        )
    for id_, fn, r_args, py_args in [
        ("bound_unknown", ".bound", '"a", "middle"', '["a"], "middle"'),
        ("not_character", "get_support_1", "1", "[1]"),
        ("not_character_null", "rtransparent_funding", "NULL", "None"),
    ]:
        expr_case(
            f"helpers.error.{id_}",
            ENV_R + f"fc_env()${fn}({r_args})",
            f"{SUPPORT}.call({q(fn)}, {py_args})",
        )


# ---------------------------------------------------------------------------
# locators on sentence batteries
# ---------------------------------------------------------------------------

BATTERY = [
    "This study was funded by the Wellcome Trust.",
    "Funding for this study came from the ESRC.",
    "Our research was supported by the Leverhulme Trust.",
    "The work was financed by a grant from the ERC.",
    "Supported by the National Institutes of Health.",
    "We acknowledge support from the Royal Society.",
    "Support from the University is gratefully acknowledged.",
    "Support was provided by the Mind Foundation.",
    "The Council provided funding for this research.",
    "The Trust funded this project in full.",
    "We thank the Academy for funding this research.",
    "This research was developed by the Institute of Psychiatry.",
    "She received funding from the ESRC.",
    "Received funding from the Medical Research Council.",
    "JMS is a recipient of a fellowship from the Royal Society.",
    "A specialty fellowship was held by nobody.",
    "The authors received funding from the ERC.",
    "The authors have no funding to declare.",
    "We thank the ERC for financial support.",
    "We thank the council for grant 12345.",
    "Funding for this research was received from the ERC.",
    "Funding This work was supported by the ESRC.",
    "Funding: none.",
    "Financial support: none.",
    "Financial support None.",
    "Financial support for this study came from the NSF.",
    "Funding disclosure: the authors received grants.",
    "Grant support: NIH.",
    "Cette \u00e9tude a \u00e9t\u00e9 financ\u00e9e par l'INSERM.",
    "Supported under project no. 4.",
    "No funding was received for this study.",
    "No external funding was received.",
    "Authors were required to disclose conflicts and disclosed none.",
    "No specific funding was given for this study.",
    "There were no sources of funding.",
    "Acknowledgements and funding",
    "The lab members helped.",
    "Disclosure: the authors have no conflicts of interest and were not funded.",
    "Disclosure: None.",
    "Competing interests",
    "No information about funding was received.",
    "The NIH R01 award supported the pilot.",
    "Self-funded work by the aid of friends.",
    "Methods and more",
    "2. Methods",
    "References",
]

BATTERY_2 = [
    "These studies were funded by the NIH.",
    "Our projects were supported by the MRC.",
    "The authors thank the funders.",
    "Methods: we tested people",
    "SUPPORTED BY THE ERC.",
    "funding for this research was received",
    "1. Smith J (2020). A paper.",
    "",
    "Supported by  two spaces.",
    "The money was granted by the Swiss National Science Foundation (SNSF) for this work.",
    "Researchers were funded by grants from the ERC and the NIH.",
]


def add_locator_cases() -> None:
    fns = [*LOCATORS, *NEGATORS, *WHERE]
    for id_, article in [("battery", BATTERY), ("battery_2", BATTERY_2), ("single", ["x"])]:
        expr_case(
            f"locate.{id_}",
            ENV_R + f"fc_apply({r_chr(fns)}, {r_chr(article)})",
            f"{SUPPORT}.apply({py_list(fns)}, {py_list(article)})",
        )
    expr_case(
        "locate.empty",
        ENV_R + f"fc_apply({r_chr(fns)}, character(0))",
        f"{SUPPORT}.apply({py_list(fns)}, [])",
    )
    expr_case(
        "locate.na",
        ENV_R + f"fc_apply({r_chr(fns)}, c(NA, 'This study was funded by the ERC.', NA))",
        f"{SUPPORT}.apply({py_list(fns)}, [None, 'This study was funded by the ERC.', None])",
    )

    title_fns = [
        "get_fund_2",
        "get_financial_1",
        "get_grant_1",
        "get_acknow_1",
        "get_acknow_2",
        "get_disclosure_1",
        ".where_acknows_txt",
        ".where_refs_txt",
    ]
    titles: list[tuple[str, list[str]]] = [
        ("funding_blank", ["Funding", "", "This work was supported by X."]),
        ("funding_last", ["Intro.", "Funding"]),
        ("funding_blank_last", ["Intro.", "Funding:", ""]),
        ("funding_statement", ["Funding statement.", "The ERC paid."]),
        ("financial", ["Financial support", "The ERC paid."]),
        ("financial_blank", ["Financial assistance:", "", "The ERC paid."]),
        ("grant", ["Grants", "", "NIH R01."]),
        (
            "grant_sponsor",
            ["Intro.", "Contract grant sponsor: NIH.", "Contract grant sponsor: EU."],
        ),
        ("grant_line", ["Nsf grant sponsor", "text"]),
        ("acknow", ["Acknowledgements and funding", "We thank the ERC."]),
        ("acknow_blank", ["ACKNOWLEDGMENT OF SUPPORT", ""]),
        ("acknow_2", ["Acknowledgments", "x", "acknowledgement", "Acknowledgement of funding"]),
        ("disclosure", ["Disclosure", "", "Funding was from the ERC.", "Declaration", "Nothing."]),
        ("disclosure_last", ["Disclosures:", "Declaration."]),
        ("disclosure_next", ["Declarations", "The grant was from the ERC."]),
        (
            "acknows_far",
            ["Acknowledgements", *[f"Filler {i}." for i in range(12)], "Funding"],
        ),
        ("acknows_near", ["Acknowledgements", "Filler.", "Financial support:", "The ERC."]),
        ("refs_next", ["Text.", "References 1. Smith", "More.", "Literature:"]),
        ("refs_tre", ["Text.", "1 Smith J.", "1. Jones", "10 x"]),
        ("citations", ["Citations", "Text"]),
    ]
    for id_, article in titles:
        expr_case(
            f"locate.titles.{id_}",
            ENV_R + f"fc_apply({r_chr(title_fns)}, {r_chr(article)})",
            f"{SUPPORT}.apply({py_list(title_fns)}, {py_list(article)})",
        )
    errors = [
        ("fund_2", "get_fund_2", ["Funding", "Funding."]),
        ("financial_1", "get_financial_1", ["Financial support", "x", "Financial aid:"]),
        ("grant_1", "get_grant_1", ["Grant", "Grants:"]),
        (
            "acknow_1",
            "get_acknow_1",
            ["Acknowledgments and funding", "x", "acknowledgement of support from X"],
        ),
        (
            "acknow_1_no_space",
            "get_acknow_1",
            ["Acknowledgments and funding", "x", "acknowledgement of support"],
        ),
        ("where_acknows", ".where_acknows_txt", ["Grant", "Grants:"]),
    ]
    for id_, fn, article in errors:
        expr_case(
            f"locate.error.{id_}",
            ENV_R + f"fc_apply({q(fn)}, {r_chr(article)})",
            f"{SUPPORT}.apply([{q(fn)}], {py_list(article)})",
        )


# ---------------------------------------------------------------------------
# rtransparent_funding()
# ---------------------------------------------------------------------------

FILLER = [f"Filler sentence number {i}." for i in range(110)]
ARTICLES: list[tuple[str, list[str | None]]] = [
    ("support", ["This study was funded by the Wellcome Trust.", "Other."]),
    ("several", ["Intro.", "Supported by the ERC.", "Text.", "We thank the NIH for grant 12345."]),
    ("absence", ["No information about funding was received.", "Nothing else."]),
    (
        "absence_mixed",
        [
            "No information about funding was received.",
            "The authors received funding from the ERC.",
        ],
    ),
    ("no_funding", ["No funding was received for this study."]),
    (
        "acknow_fallback",
        [
            "Intro text.",
            "Acknowledgements",
            "Thanks to the lab; this was funded partly by a charity.",
            "Project number 5 is ours.",
            "References",
        ],
    ),
    (
        "acknow_refs_before",
        [
            "References",
            "Acknowledgements",
            "Thanks to all; this was funded partly by a charity.",
        ],
    ),
    (
        "acknow_too_far",
        ["Acknowledgements", "Thanks, this was funded partly by a charity.", *FILLER, "References"],
    ),
    (
        "acknow_refs_before_far",
        ["References", "Acknowledgements", *FILLER, "This was funded partly by a charity."],
    ),
    (
        "acknow_tre_refs",
        ["Acknowledgements", "The pilot was funded partly by a charity.", "1. Smith J. A paper."],
    ),
    (
        "acknow_from_max",
        [
            "Acknowledgements",
            "Filler one.",
            "Filler two.",
            "The pilot was funded partly by a charity.",
            *[f"More filler {i}." for i in range(10)],
            "Financial information: No information about funding was received.",
            "Other text.",
            "References",
        ],
    ),
    ("acknow_no_refs", ["Acknowledgements", "The pilot was funded partly by a charity."]),
    ("na_index", ["Funding", ""]),
    ("blank_next", ["Funding", "", "Money came from the ERC."]),
    ("empty", []),
    ("nothing", ["Nothing to see.", "Still nothing."]),
    ("battery", BATTERY),
    ("battery_2", BATTERY_2),
    ("na_text", [None, "This study was funded by the ERC.", None]),
]


def add_funding_cases() -> None:
    articles = [a for _, a in ARTICLES]
    expr_case(
        "rtransparent_funding.articles",
        ENV_R + f"fc_funding({r_list_chr(articles)})",
        f"{SUPPORT}.funding({py_list_list(articles)})",
    )
    for id_, article in ARTICLES:
        expr_case(
            f"rtransparent_funding.{id_}",
            ENV_R + f"fc_env()$rtransparent_funding({r_chr(article)})",
            f"{SUPPORT}.call('rtransparent_funding', {py_list(article)})",
        )
    expr_case(
        "rtransparent_funding.error",
        ENV_R + "fc_env()$rtransparent_funding(c('Funding', 'Funding:'))",
        f"{SUPPORT}.call('rtransparent_funding', ['Funding', 'Funding:'])",
    )


# ---------------------------------------------------------------------------
# modules
# ---------------------------------------------------------------------------

PSYCHSCI = [
    f"{FIXTURES}/psychsci/0956797613520608.json",
    f"{FIXTURES}/psychsci/0956797614522816.json",
    f"{FIXTURES}/psychsci/0956797614527830.json",
    "upstream/metacheck/inst/demos/to_err_is_human.json",
]
XML_FIXTURES = [
    f"{FIXTURES}/formats/preprint.pdf.tei.xml",
    f"{FIXTURES}/formats/published.pdf.tei.xml",
    f"{FIXTURES}/problems/203020.xml",
    f"{FIXTURES}/problems/0956797615569889.xml",
    f"{FIXTURES}/problems/0956797617737129.xml",
    f"{FIXTURES}/formats/apa.xml",
]
MORE_FIXTURES = [
    f"{FIXTURES}/debruine/debruine-child.xml",
    f"{FIXTURES}/debruine/debruine-fret.xml",
    f"{FIXTURES}/debruine/debruine-sex.xml",
    f"{FIXTURES}/debruine/debruine-tnl.xml",
    f"{FIXTURES}/formats/preprint.cermine.xml",
    f"{FIXTURES}/formats/published.cermine.xml",
]
FOUND = "This research was funded by UKRI grant #202020."
NONE = "The funding for arts is not great."


def test_paper(text: list[str | None]) -> dict[str, Any]:
    return {"$test_paper": {"text": text}}


def paperlist(texts: list[list[str]]) -> dict[str, Any]:
    r = "paperlist(" + ", ".join(f"test_paper({r_chr(t)})" for t in texts) + ")"
    py = "pc.PaperList([" + ", ".join(f"pc.test_paper({py_list(t)})" for t in texts) + "])"
    return paper_expr(r, py)


def sectioned(text: list[str], types: list[str | None]) -> dict[str, Any]:
    return paper_expr(
        ENV_R + f"fc_sectioned({r_chr(text)}, {r_chr(types)})",
        f"{SUPPORT}.sectioned({py_list(text)}, {py_list(types)})",
    )


ACK_PAPER = [
    "We studied things.",
    "Acknowledgements",
    "Thanks to the lab; this was funded partly by a charity.",
    "Project number 5 is ours.",
    "References",
]


def add_module_cases() -> None:
    for mod in ("funding_check", "funding_check_oi"):
        module_case(f"{mod}.demo", mod, {"$paper": "demo"})
        module_case(f"{mod}.psychsci", mod, {"$read": PSYCHSCI})
        module_case(f"{mod}.xml_fixtures", mod, {"$read": XML_FIXTURES})
        module_case(f"{mod}.more_fixtures", mod, {"$read": MORE_FIXTURES})
        module_case(f"{mod}.single_json", mod, {"$paper": f"{FIXTURES}/problems/203020.json"})
        module_case(
            f"{mod}.paper_empty", mod, paper_expr("paper()", "pc.paper()"), compare=IGNORE_IDS
        )
        module_case(f"{mod}.none", mod, test_paper([NONE]), compare=IGNORE_IDS)
        module_case(f"{mod}.found", mod, test_paper([FOUND]), compare=IGNORE_IDS)
        module_case(f"{mod}.paperlist", mod, paperlist([[FOUND], [NONE]]), compare=IGNORE_IDS)
        module_case(
            f"{mod}.paperlist_mixed",
            mod,
            paperlist([[NONE], [FOUND, "Our study was supported by the ERC."], [], ACK_PAPER]),
            compare=IGNORE_IDS,
        )
        module_case(f"{mod}.empty", mod, test_paper([]), compare=IGNORE_IDS)
        module_case(f"{mod}.battery", mod, test_paper(BATTERY), compare=IGNORE_IDS)
        module_case(f"{mod}.ack_fallback", mod, test_paper(ACK_PAPER), compare=IGNORE_IDS)
    module_case(
        "funding_check.error", "funding_check", test_paper(["Funding", "Funding:"]),
        compare=IGNORE_IDS,
    )  # fmt: skip
    module_case(
        "funding_check.na_index", "funding_check", test_paper(["Funding", ""]), compare=IGNORE_IDS
    )
    module_case(
        "funding_check.absence_only",
        "funding_check",
        test_paper(["No information about funding was received.", "Nothing else."]),
        compare=IGNORE_IDS,
    )
    sentences = [
        "This work was supported by grant 1.",
        "Our study was funded by the ERC.",
        "Unrelated support for the theory in this paper.",
        "Nothing here.",
    ]
    module_case(
        "funding_check_oi.sectioned_funding",
        "funding_check_oi",
        sectioned(sentences, ["intro", "funding", "discussion", "funding"]),
        compare=IGNORE_IDS,
    )
    module_case(
        "funding_check_oi.sectioned_none",
        "funding_check_oi",
        sectioned(sentences, ["intro", "acknowledgment", "discussion", None]),
        compare=IGNORE_IDS,
    )
    module_case(
        "funding_check_oi.sectioned_na",
        "funding_check_oi",
        sectioned(sentences, [None, None, "funding", "funding"]),
        compare=IGNORE_IDS,
    )
    module_case(
        "funding_check.sectioned_references",
        "funding_check",
        sectioned(
            ["Intro.", "This study was funded by the ERC.", "Smith (2020) was funded by the NIH."],
            ["intro", "references", "references"],
        ),
        compare=IGNORE_IDS,
    )


def main() -> None:
    add_pattern_cases()
    add_locator_cases()
    add_funding_cases()
    add_module_cases()
    doc = {"area": "mod_funding", "cases": CASES}
    with OUT.open("w", encoding="utf-8") as fh:
        fh.write("# Generated by tests/mod_funding/make_parity_cases.py -- edit that file.\n")
        yaml.dump(doc, fh, Dumper=Dumper, sort_keys=False, allow_unicode=True, width=10_000)


if __name__ == "__main__":
    main()
