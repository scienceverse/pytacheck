"""Generate parity/cases/mod_prereg.yaml (run from anywhere; then regenerate goldens).

python tests/mod_prereg/make_cases.py
python -m parity generate --area mod_prereg
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]

APIS = "upstream/metacheck/tests/testthat/apis"
LOCAL = "tests/mod_prereg/mocks"
PSY = "upstream/metacheck/tests/testthat/fixtures/psychsci/"
PSYCHSCI = [
    PSY + "0956797613520608.json",
    PSY + "0956797614522816.json",
    PSY + "0956797614527830.json",
]
DEMO_JSON = "upstream/metacheck/inst/demos/to_err_is_human.json"


def rstr(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def rvec(xs):
    return "c(" + ", ".join(rstr(x) for x in xs) + ")" if xs else "character(0)"


def r_paper(spec):
    parts = []
    if spec.get("demo"):
        parts.append("demopaper()")
    if spec.get("read"):
        parts.append(f"read({rvec(spec['read'])})")
    for p in spec.get("papers", []):
        text = f", {rvec(p['text'])}" if p.get("text") else ""
        parts.append(f"tp({rvec(p.get('url', []))}, {rstr(p['id'])}{text})")
    if spec.get("paperlist") or len(parts) > 1:
        return "paperlist(" + ", ".join(parts) + ")"
    return parts[0]


def r_expr(spec):
    d = LOCAL if spec.get("mock") == "local" else APIS
    run = f'module_run({r_paper(spec)}, "prereg_check")'
    if spec.get("report"):
        run = (
            r'gsub("(?s)\n```\\{r\\}.*?\n```\n", "\n<R-CHUNK>\n", '
            f"module_report({run}), perl = TRUE)"
        )
    return (
        "(function() { .r <- httptest2::get_current_redactor(); "
        "httptest2::set_redactor(function(req) { req$url <- "
        'gsub("[?&]page(%5[Bb]size%5[Dd]|\\\\[size\\\\])=100", "", req$url); req }); '
        'Sys.setenv(TESTTHAT = "true"); '
        'on.exit({ httptest2::set_redactor(.r); Sys.unsetenv("TESTTHAT") }); '
        "tp <- function(url, id, text = LETTERS) { p <- test_paper(text, url); p$paper_id <- id; p }; "
        f"httptest2::with_mock_dir({rstr(d)}, {run}) }})()"
    )


def osf(*ids):
    return [f"https://osf.io/{i}" for i in ids]


def one(url, pid, text=None):
    p = {"url": url if isinstance(url, list) else [url], "id": pid}
    if text:
        p["text"] = text
    return {"papers": [p]}


CASES = [
    # --- metacheck's recordings: every template in the testthat tests
    ("demo", "OSF Preregistration and AsPredicted links in the demo paper", {"demo": True}),
    ("oer", "Open-Ended Registration", one(osf("5xysn"), "p_oer")),
    ("prc", "Prereg Challenge (pages format)", one(osf("jez3g"), "p_prc")),
    (
        "osf_pr_28",
        "older OSF Preregistration (pages format, qN keys)",
        one(osf("g59u6"), "p_g59u6"),
    ),
    ("osf_pr_31", "OSF Preregistration with uploaded files", one(osf("7qcxa"), "p_7qcxa")),
    ("osf_pre", "OSF-Standard Pre-Data Collection Registration", one(osf("dr42m"), "p_dr42m")),
    ("prap", "AsPredicted-on-OSF template", one(osf("7v28u"), "p_7v28u")),
    ("rrbrandt", "Replication Recipe", one(osf("vzb48"), "p_vzb48")),
    ("prsp", "van 't Veer & Giner-Sorolla (dedicated extractor)", one(osf("r5bme"), "p_r5bme")),
    (
        "prsp_files",
        "van 't Veer & Giner-Sorolla with an uploaded file",
        one(osf("wrh4x"), "p_wrh4x"),
    ),
    ("blocks", "current OSF Preregistration (blocks format)", one(osf("9h2pj"), "p_9h2pj")),
    (
        "schema_unreachable",
        "registration whose schema cannot be fetched",
        one(osf("a6y7r"), "p_a6y7r"),
    ),
    (
        "many",
        "more than 10 registrations in one paper",
        one(
            osf(
                "bdvxs",
                "wrh4x",
                "trwb4",
                "z2bsa",
                "jez3g",
                "9bg3z",
                "7qcxa",
                "a6y7r",
                "4v3sg",
                "hwu9x",
                "yab8q",
            ),
            "p_many",
        ),
    ),
    (
        "paperlist",
        "a paper list: one OSF and one AsPredicted paper",
        {
            "papers": [
                {"url": osf("48ncu"), "id": "paper_a"},
                {"url": ["https://aspredicted.org/by8i8v.pdf"], "id": "paper_b"},
            ]
        },
    ),
    (
        "psychsci_demo",
        "a paper list where only some papers have links",
        {"read": [*PSYCHSCI, DEMO_JSON]},
    ),
    # --- traffic light "na"
    ("no_links", "no links at all", one([], "p_none", ["No links here.", "Nor here."])),
    ("no_links.paperlist", "no links in any paper of a list", {"read": PSYCHSCI}),
    ("no_registrations", "an OSF project link, not a registration", one(osf("pngda"), "p_node")),
    (
        "no_registrations.many",
        "OSF links of every other type (and an invalid one)",
        one([*osf("pngda", "629bx", "xp5cy", "75qgk", "4i578"), "https://osf.io/abc"], "p_types"),
    ),
    ("inaccessible", "an OSF link that cannot be read", one(osf("3cz2e"), "p_inacc")),
    (
        "fetch_fails",
        "a registration that type-checks but cannot be fetched",
        one(osf("8c3kb"), "p_fetch"),
    ),
    (
        "inaccessible.mixed",
        "one readable registration and two unreadable links",
        one(osf("48ncu", "3cz2e", "8c3kb"), "p_mixed"),
    ),
    # --- AsPredicted
    (
        "aspredicted_only",
        "only an AsPredicted link (R errors: osf_type(character(0)))",
        one(["https://aspredicted.org/by8i8v.pdf"], "p_ap"),
    ),
    (
        "aspredicted.multiple",
        "two AsPredicted links collapse into one row",
        one(
            [
                "https://aspredicted.org/by8i8v.pdf",
                "https://aspredicted.org/ve2qn.pdf",
                *osf("pngda"),
            ],
            "p_ap2",
        ),
    ),
    (
        "aspredicted.blind",
        "blinded and short AsPredicted links with an OSF registration",
        one(
            [
                "https://aspredicted.org/blind.php?x=nq4xa3",
                "https://aspredicted.org/Y2F_6B7",
                *osf("48ncu"),
            ],
            "p_ap3",
        ),
    ),
    (
        "aspredicted.unreachable",
        "an AsPredicted page that cannot be fetched (R errors)",
        one(["https://aspredicted.org/zzzzz.pdf", *osf("48ncu")], "p_ap4"),
    ),
    (
        "aspredicted.paperlist",
        "AsPredicted links of different papers collapse into one row too",
        {
            "papers": [
                {"url": ["https://aspredicted.org/by8i8v.pdf", *osf("48ncu")], "id": "paper_a"},
                {"url": ["https://aspredicted.org/ve2qn.pdf", *osf("5xysn")], "id": "paper_b"},
            ]
        },
    ),
    # --- links that do not join back to their paper
    (
        "duplicate_links",
        "the same registration as a link and in the text",
        one(osf("48ncu"), "p_dup", ["The preregistration is at osf.io/48ncu."]),
    ),
    (
        "unjoined_links",
        "http:// and upper-case links do not join back to the paper",
        one(["http://osf.io/48ncu", "https://OSF.io/48NCU/"], "p_http"),
    ),
    # --- synthetic recordings (tests/mod_prereg/mocks)
    (
        "synthetic.withdrawn",
        "a withdrawn registration",
        {**one(osf("wdrwn"), "p_wdrwn"), "mock": "local"},
    ),
    (
        "synthetic.blocks",
        "blocks schema: reserved/repeated/unlabelled fields, slugs, files",
        {**one(osf("blkss"), "p_blkss"), "mock": "local"},
    ),
    (
        "synthetic.pages",
        "pages schema: long titles, questions, sub-keys, nested answers",
        {**one(osf("pagss"), "p_pagss"), "mock": "local"},
    ),
    (
        "synthetic.prsp",
        "prsp: $ partial matching, vectors and data frames pasted together",
        {**one(osf("prspx"), "p_prspx"), "mock": "local"},
    ),
    (
        "synthetic.common_only",
        "no responses / no schema link: only the common fields",
        {**one(osf("emptr", "nosch"), "p_common"), "mock": "local"},
    ),
    (
        "synthetic.paperlist",
        "several synthetic registrations across a paper list",
        {
            "papers": [
                {"url": osf("blkss", "wdrwn"), "id": "p_one"},
                {"url": osf("pagss"), "id": "p_two"},
                {"url": ["https://example.org"], "id": "p_three"},
            ],
            "mock": "local",
        },
    ),
    # --- module_report() prose (R code chunks masked)
    ("report.demo", "module_report() of the demo paper", {"demo": True, "report": True}),
    (
        "report.mixed",
        "module_report() with inaccessible links",
        {**one(osf("48ncu", "3cz2e", "8c3kb"), "p_mixed"), "report": True},
    ),
    (
        "report.inaccessible",
        "module_report() when every link is inaccessible",
        {**one(osf("3cz2e"), "p_inacc"), "report": True},
    ),
    (
        "report.no_links",
        "module_report() without links",
        {**one([], "p_none", ["No links here."]), "report": True},
    ),
]

cases = [
    # plain module cases (no HTTP: papers without links return before any request)
    {
        "id": "prereg_check.module.no_links",
        "note": "module_run() directly: a paper list without links",
        "module": "prereg_check",
        "args": {"paper": {"$read": PSYCHSCI}},
    },
    {
        "id": "prereg_check.module.no_links_test_paper",
        "note": "module_run() directly: a test paper without links",
        "module": "prereg_check",
        # a fixed paper_id (test_paper() makes a random one) keeps the golden stable
        "args": {
            "paper": {
                "$expr": {
                    "r": '(function() { p <- test_paper(c("Nothing to see.", "No OSF here.")); '
                    'p$paper_id <- "p_plain"; p })()',
                    "py": '__import__("tests.mod_prereg.parity_support", fromlist=["_"])'
                    '.tp([], "p_plain", ["Nothing to see.", "No OSF here."])',
                }
            }
        },
    },
]
for cid, note, spec in CASES:
    py_args = {k: v for k, v in spec.items() if k != "papers"}
    if spec.get("papers"):
        py_args["papers"] = spec["papers"]
    cases.append(
        {
            "id": f"prereg_check.{cid}",
            "note": note,
            "r": "identity",
            "py": "tests.mod_prereg.parity_support.run_prereg",
            "args": {"x": {"$expr": {"r": r_expr(spec), "py": "None"}}},
            "py_drop": ["x"],
            "py_args": py_args,
        }
    )

header = (
    "# Parity cases for the prereg_check module (inst/modules/prereg_check.R).\n"
    '# Generated; each case runs module_run(<paper>, "prereg_check") on recorded API\n'
    "# responses: metacheck's (upstream .../testthat/apis, with its page[size] redactor)\n"
    "# or synthetic ones (tests/mod_prereg/mocks). Python side:\n"
    "# tests/mod_prereg/parity_support.py::run_prereg.\n"
)
with open(ROOT / "parity" / "cases" / "mod_prereg.yaml", "w", encoding="utf-8") as f:
    f.write(header)

    class Dumper(yaml.SafeDumper):
        def ignore_aliases(self, data):
            return True

    Dumper.add_representer(
        str, lambda d, v: d.represent_scalar("tag:yaml.org,2002:str", v, style='"')
    )
    yaml.dump(
        {"area": "mod_prereg", "cases": cases},
        f,
        Dumper=Dumper,
        sort_keys=False,
        allow_unicode=True,
        width=10000,
    )
print(len(cases))
