"""Generate parity/cases/mod_prereg_review.yaml (the review cases of prereg_check).

python tests/mod_prereg/make_review_mocks.py   # synthetic recordings, if changed
python tests/mod_prereg/make_review_cases.py
python -m parity generate --area mod_prereg_review

Same case shape as make_cases.py (module_run() on recorded responses, R under
httptest2::with_mock_dir()), plus ``tables`` cases that compare the data of
the report's tables, which the report prose comparison skips.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]

APIS = "upstream/metacheck/tests/testthat/apis"
LOCAL = "tests/mod_prereg/mocks"
AP1 = "https://aspredicted.org/by8i8v.pdf"
AP2 = "https://aspredicted.org/ve2qn.pdf"


def rstr(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def rvec(xs):
    return "c(" + ", ".join(rstr(x) for x in xs) + ")" if xs else "character(0)"


def r_paper(spec):
    parts = []
    if spec.get("demo"):
        parts.append("demopaper()")
    for p in spec.get("papers", []):
        text = f", {rvec(p['text'])}" if p.get("text") else ""
        parts.append(f"tp({rvec(p.get('url', []))}, {rstr(p['id'])}{text})")
    if spec.get("paperlist") or len(parts) > 1:
        return "paperlist(" + ", ".join(parts) + ")"
    return parts[0]


# the report's scroll_table() chunks, evaluated back into data frames
R_TABLES = (
    "(function(mo) { rep <- unlist(mo$report); "
    'm <- regmatches(rep, regexpr("(?s)(?<=\\ntable <- ).*?(?=\\n\\n# display table)", '
    "rep, perl = TRUE)); "
    "lapply(m, function(s) as.data.frame(eval(parse(text = s)))) })"
)


def r_expr(spec):
    d = LOCAL if spec.get("mock") == "local" else APIS
    run = f'module_run({r_paper(spec)}, "prereg_check")'
    if spec.get("tables"):
        run = f"{R_TABLES}({run})"
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


def one(url, pid, text=None, **extra):
    p = {"url": url if isinstance(url, list) else [url], "id": pid}
    if text:
        p["text"] = text
    return {"papers": [p], **extra}


def papers(*specs, **extra):
    return {"papers": [{"url": u, "id": i} for u, i in specs], **extra}


CASES = [
    # --- joining registrations back to papers
    (
        "ap.same_link_two_papers",
        "one AsPredicted link cited by two papers: its row joins to both",
        papers(([AP1, *osf("48ncu")], "paper_a"), ([AP1], "paper_b")),
    ),
    (
        "ap.same_link_twice",
        "the same AsPredicted link twice in one paper (url rows are not deduplicated)",
        one([AP1, AP1, *osf("48ncu")], "p_ap_twice"),
    ),
    (
        "reg_in_two_papers",
        "one registration cited by two papers of a list",
        papers((osf("48ncu"), "paper_a"), (osf("48ncu", "5xysn"), "paper_b")),
    ),
    (
        "unjoined.paperlist",
        "a link that does not join back: NA paper_id is dropped by module_run()",
        papers((["http://osf.io/48ncu"], "paper_a"), (osf("5xysn"), "paper_b")),
    ),
    # --- inaccessible links with other findings
    (
        "ap.with_inaccessible",
        "an AsPredicted page and an inaccessible OSF link",
        one([AP1, *osf("3cz2e")], "p_ap_inacc"),
    ),
    (
        "ap.with_fetch_fails",
        "an AsPredicted page and a registration that cannot be fetched",
        one([AP2, *osf("8c3kb")], "p_ap_fetch"),
    ),
    (
        "inaccessible.twice",
        "an inaccessible link as a hyperlink and in the text: named twice",
        one(osf("3cz2e"), "p_inacc2", ["Registered at osf.io/3cz2e."]),
    ),
    (
        "inaccessible.all_kinds",
        "every link inaccessible: one by osf_type(), one on fetching",
        one(osf("3cz2e", "8c3kb"), "p_inacc_all"),
    ),
    (
        "inaccessible.paperlist",
        "inaccessible links in a paper list (the summary counts only accessible ones)",
        papers((osf("3cz2e"), "paper_a"), (osf("48ncu", "8c3kb"), "paper_b"), ([], "paper_c")),
    ),
    # --- links that are not OSF ids
    (
        "invalid_only",
        "OSF links without a valid id (generic and too short)",
        one(["https://osf.io", "https://osf.io/abc"], "p_invalid"),
    ),
    # --- synthetic recordings (tests/mod_prereg/mocks)
    (
        "synthetic.empty_data",
        "a registration whose data is empty (R errors in dplyr::count())",
        one(osf("emptd"), "p_emptd", mock="local"),
    ),
    (
        "synthetic.empty_data.with_other",
        "an empty registration next to a readable one",
        one(osf("emptd", "blkss"), "p_emptd2", mock="local"),
    ),
    (
        "synthetic.paper_id_field",
        "a schema field slugged to paper_id (R errors in dplyr::count())",
        one(osf("pidsl"), "p_pidsl", mock="local"),
    ),
    (
        "synthetic.arrays",
        "answers jsonlite simplifies into arrays and matrices",
        one(osf("arrys"), "p_arrys", mock="local"),
    ),
    (
        "synthetic.prsp_deparse",
        "prsp fields whose values as.character() deparses",
        one(osf("prspz"), "p_prspz", mock="local"),
    ),
    (
        "synthetic.label_case",
        "labels needing R's tolower()/trimws() (non-ASCII, tabs, no-break space)",
        one(osf("unicd"), "p_unicd", mock="local"),
    ),
    (
        "synthetic.pages_title_length",
        "pages titles at the 40-character boundary and a repeated qid",
        one(osf("pgbnd"), "p_pgbnd", mock="local"),
    ),
    (
        "synthetic.view_only",
        "a view-only registration link (the token stays in the id)",
        one(["https://osf.io/vwonl/?view_only=abc123"], "p_vwonl", mock="local"),
    ),
    (
        "synthetic.view_only.paperlist",
        "a view-only link in a paper list",
        papers(
            (["https://osf.io/vwonl/?view_only=abc123"], "paper_a"),
            (osf("blkss"), "paper_b"),
            mock="local",
        ),
    ),
    # --- the tables in the report (compared cell by cell)
    ("tables.demo", "report tables: demo paper", {"demo": True, "tables": True}),
    (
        "tables.many",
        "report tables: many registrations (all-NA fields dropped)",
        one(
            osf("bdvxs", "wrh4x", "trwb4", "z2bsa", "jez3g", "9bg3z", "7qcxa", "a6y7r"),
            "p_many",
            tables=True,
        ),
    ),
    (
        "tables.mixed",
        "report tables: accessible and inaccessible links",
        one(osf("48ncu", "3cz2e", "8c3kb"), "p_mixed", tables=True),
    ),
    (
        "tables.aspredicted",
        "report tables: AsPredicted pages collapsed into one row",
        one([AP1, AP2, *osf("5xysn")], "p_ap_tables", tables=True),
    ),
    (
        "tables.no_sample_size",
        "report tables: no sample size field",
        one(osf("5xysn"), "p_oer", tables=True),
    ),
    (
        "tables.duplicates",
        "report tables: a registration joined to two papers",
        {**papers((osf("48ncu"), "paper_a"), (osf("48ncu"), "paper_b")), "tables": True},
    ),
    (
        "tables.synthetic",
        "report tables: synthetic registrations (line breaks, NA fields)",
        {
            **papers(
                (osf("blkss", "wdrwn"), "p_one"),
                (osf("pagss", "prspx"), "p_two"),
                mock="local",
            ),
            "tables": True,
        },
    ),
    (
        "tables.synthetic.prsp_deparse",
        "report tables: deparsed prsp values",
        one(osf("prspz", "arrys"), "p_prspz", mock="local", tables=True),
    ),
    # --- second review pass
    (
        "report.no_sample_size",
        "module_report() without a sample size: scroll_table(NULL) leaves an empty block",
        one(osf("5xysn"), "p_oer", report=True),
    ),
    (
        "empty_paperlist",
        "an empty paper list (R errors in data.frame(): paper_id() is NULL)",
        {"papers": [], "paperlist": True},
    ),
    (
        "ap.with_invalid_osf",
        "an AsPredicted page next to an OSF link without a valid id (osf_type(NA))",
        one([AP1, "https://osf.io/abc"], "p_ap_invalid"),
    ),
    (
        "ap.no_registration",
        "one AsPredicted page next to an OSF link that is not a registration",
        one([AP1, *osf("pngda")], "p_ap_node"),
    ),
    (
        "synthetic.deparse_escapes",
        "deparsed prsp values with control, separator and unassigned characters",
        one(osf("escps"), "p_escps", mock="local"),
    ),
    (
        "tables.synthetic.deparse_escapes",
        "report tables: deparsed prsp values with escapes",
        one(osf("escps"), "p_escps", mock="local", tables=True),
    ),
    (
        "synthetic.label_scalars",
        "blocks schema labels that are numbers or logicals (as.character())",
        one(osf("lblsc"), "p_lblsc", mock="local"),
    ),
    (
        "synthetic.label_nonchar",
        "a schema label with U+FFFF: R's tolower() errors (utf8towcs)",
        one(osf("lblnc"), "p_lblnc", mock="local"),
    ),
    (
        "synthetic.title_scalars",
        "pages schema titles that are numbers or logicals (as.character())",
        one(osf("lblpg"), "p_lblpg", mock="local"),
    ),
]

cases = []
for cid, note, spec in CASES:
    py_args = {k: v for k, v in spec.items() if k not in ("papers", "tables")}
    if spec.get("papers"):
        py_args["papers"] = spec["papers"]
    fn = "run_prereg_tables" if spec.get("tables") else "run_prereg"
    cases.append(
        {
            "id": f"prereg_check.{cid}",
            "note": note,
            "r": "identity",
            "py": f"tests.mod_prereg.parity_support.{fn}",
            "args": {"x": {"$expr": {"r": r_expr(spec), "py": "None"}}},
            "py_drop": ["x"],
            "py_args": py_args,
        }
    )

header = (
    "# Review parity cases for the prereg_check module (inst/modules/prereg_check.R).\n"
    "# Generated by tests/mod_prereg/make_review_cases.py; same shape as mod_prereg.yaml,\n"
    "# plus cases comparing the data of the report's tables.\n"
)
with open(ROOT / "parity" / "cases" / "mod_prereg_review.yaml", "w", encoding="utf-8") as f:
    f.write(header)

    class Dumper(yaml.SafeDumper):
        def ignore_aliases(self, data):
            return True

    Dumper.add_representer(
        str, lambda d, v: d.represent_scalar("tag:yaml.org,2002:str", v, style='"')
    )
    yaml.dump(
        {"area": "mod_prereg_review", "cases": cases},
        f,
        Dumper=Dumper,
        sort_keys=False,
        allow_unicode=True,
        width=10000,
    )
print(len(cases))
