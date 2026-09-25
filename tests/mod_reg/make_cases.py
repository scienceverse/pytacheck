"""Generate parity/cases/mod_reg.yaml (run from anywhere; then regenerate goldens).

python tests/mod_reg/make_cases.py
python -m parity generate --area mod_reg

Each case runs ``module_run(<paper>, "reg_check")`` on metacheck's recorded
OSF/AsPredicted responses with the RegCheck server faked; the R fakes written
here are twins of the Python ones in ``tests/mod_reg/parity_support.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]

APIS = "upstream/metacheck/tests/testthat/apis"
PSY = "upstream/metacheck/tests/testthat/fixtures/psychsci/"
PSYCHSCI = [
    PSY + "0956797613520608.json",
    PSY + "0956797614522816.json",
    PSY + "0956797614527830.json",
]

import sys

sys.path.insert(0, str(ROOT))
from tests.mod_reg.parity_support import MOCK_TABLE, ODD_TABLE


def rstr(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def rvec(xs: list[Any]) -> str:
    if not xs:
        return "character(0)"
    return "c(" + ", ".join("NA_character_" if x is None else rstr(x) for x in xs) + ")"


def r_frame(data: dict[str, list[Any]]) -> str:
    cols = ", ".join(f"{k} = {rvec(v)}" for k, v in data.items())
    return f"data.frame({cols})"


def r_paper(spec: dict[str, Any]) -> str:
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


R_FAKES = (
    ".calls <- 0; "
    f".tbl <- {r_frame(MOCK_TABLE)}; "
    f".odd <- {r_frame(ODD_TABLE)}; "
    ".fake_table <- function(...) { .calls <<- .calls + 1; .tbl }; "
    ".fake_odd <- function(...) { .calls <<- .calls + 1; .odd }; "
    ".fake_empty <- function(...) { .calls <<- .calls + 1; regcheck_tidy(list(items = list())) }; "
    '.fake_error <- function(...) { .calls <<- .calls + 1; stop("RegCheck server unreachable") }; '
    ".fake_partial <- function(...) { .calls <<- .calls + 1; "
    'if (.calls == 1) .tbl else stop("second prereg failed") }; '
    ".cap <- new.env(); .cap$n <- 0; "
    ".submit <- function(url, api_token, paper_text, prereg_text = NULL, "
    'registration_id = NULL, client = "ollama", dimensions = NULL, '
    'reasoning_effort = "medium") { .cap$n <- .cap$n + 1; '
    ".cap$paper_text <- paper_text; .cap$prereg_text <- prereg_text; .cap$client <- client; "
    '.cap$dims <- if (is.null(dimensions)) "default" else '
    'paste(dimensions$dimension, collapse = "|"); '
    'list(task_id = paste0("task-", .cap$n)) }; '
    ".poll <- function(url, api_token, task_id, poll_interval = 5, timeout = 3600) { "
    "list(items = list("
    'list(dimension = "Paper text", deviation_judgement = " Yes ", '
    "paper_content_summary = substr(.cap$paper_text, 1, 300), "
    "registration_content_summary = as.character(nchar(.cap$paper_text)), "
    'deviation_information = paste("client:", .cap$client, "url:", url, "task:", task_id, '
    '"token:", api_token, "dims:", .cap$dims, "[PAPER_0001, REG_0002]"), '
    'paper_content_quotes = "[PAPER_0001] first; [PAPER_0002] second", '
    'registration_content_quotes = "[REG_0001] q"), '
    'list(dimension = "Preregistration text", deviation_judgement = "Partially", '
    'paper_content_summary = "p [PAPER_0003]", '
    "registration_content_summary = .cap$prereg_text, "
    'deviation_information = "Details [REG_0001; REG_0002] and [not a ref]."), '
    'list(dimension = "Exclusions", deviation_judgement = "MISSING", '
    'paper_content_summary = "none", registration_content_summary = "none"))) }; '
)

R_HTTR2 = {
    "connfail": 'rlang::abort("Could not connect to server", class = c("httr2_failure", "error"))',
    "unauthorized": 'rlang::abort("HTTP 401 Unauthorized", '
    'class = c("httr2_http_401", "httr2_http", "error"))',
}


def r_args(spec: dict[str, Any]) -> str:
    args = [f"{k} = {rstr(v)}" for k, v in (spec.get("args") or {}).items()]
    if spec.get("dims") is not None:
        d = rvec(spec["dims"])
        args.append(
            f'dimensions = data.frame(dimension = {d}, definition = paste("Definition of", {d}))'
        )
    return "".join(", " + a for a in args)


def r_expr(spec: dict[str, Any]) -> str:
    fake = spec.get("fake", "table")
    run = f'module_run(x, "reg_check"{r_args(spec)})'
    if fake == "echo":
        run = (
            f"httptest2::with_mock_dir({rstr(APIS)}, testthat::with_mocked_bindings({run}, "
            '.regcheck_submit = .submit, .regcheck_poll = .poll, .package = "metacheck"))'
        )
    elif fake in R_HTTR2:
        if "prereg_check" not in spec.get("pre", []):
            raise ValueError(f"fake {fake!r} needs a chained prereg_check")
        run = (
            f"testthat::with_mocked_bindings({run}, "
            f'req_perform = function(...) {R_HTTR2[fake]}, .package = "httr2")'
        )
    elif fake == "real":
        run = f"httptest2::with_mock_dir({rstr(APIS)}, {run})"
    else:
        run = (
            f"httptest2::with_mock_dir({rstr(APIS)}, testthat::with_mocked_bindings({run}, "
            f'regcheck_compare = .fake_{fake}, .package = "metacheck"))'
        )
    steps = [f"x <- {r_paper(spec)}"]
    for m in spec.get("pre", []):
        steps.append(f"x <- httptest2::with_mock_dir({rstr(APIS)}, module_run(x, {rstr(m)}))")
    if spec.get("double"):
        steps.append("x$table <- rbind(x$table, x$table)")
    if spec.get("na_paper_id"):
        steps.append("x$table$paper_id <- NA_character_")
    steps.append(f"mo <- {run}")
    if spec.get("tables"):
        steps.append(
            'chunks <- Filter(function(s) grepl("```{r}", s, fixed = TRUE), mo$report); '
            "mo <- lapply(chunks, function(s) { "
            'code <- sub("{r}", "", strsplit(s, "```", fixed = TRUE)[[1]][2], fixed = TRUE); '
            "e <- new.env(); eval(parse(text = code)[[1]], e); as.data.frame(e$table) })"
        )
    if spec.get("report"):
        steps.append(
            r'mo <- gsub("(?s)\n```\\{r\\}.*?\n```\n", "\n<R-CHUNK>\n", '
            "module_report(mo), perl = TRUE)"
        )
    envvars = {"TESTTHAT": "true", "REGCHECK_API_TOKEN": "", "REGCHECK_BASE_URL": ""}
    envvars.update(spec.get("envvars") or {})
    env = ", ".join(f"{k} = {rstr(v)}" for k, v in envvars.items())
    return (
        "(function() { .r <- httptest2::get_current_redactor(); "
        "httptest2::set_redactor(function(req) { req$url <- "
        'gsub("[?&]page(%5[Bb]size%5[Dd]|\\\\[size\\\\])=100", "", req$url); req }); '
        "on.exit(httptest2::set_redactor(.r)); "
        f"withr::local_envvar(c({env})); "
        "tp <- function(url, id, text = LETTERS) { p <- test_paper(text, url); "
        "p$paper_id <- id; p }; "
        f"{R_FAKES}" + "; ".join(steps) + "; mo })()"
    )


def osf(*ids: str) -> list[str]:
    return [f"https://osf.io/{i}" for i in ids]


def one(url: list[str] | str, pid: str, text: list[str] | None = None) -> dict[str, Any]:
    p: dict[str, Any] = {"url": url if isinstance(url, list) else [url], "id": pid}
    if text:
        p["text"] = text
    return {"papers": [p]}


OER = one(osf("5xysn"), "p_oer", ["We preregistered this study.", "It had 120 participants."])
CHAIN = {"pre": ["prereg_check"]}

CASES: list[tuple[str, str, dict[str, Any]]] = [
    # --- the testthat scenarios
    ("standalone.oer", "prereg_check run internally (testthat mock table)", OER),
    ("standalone.demo", "the demo paper's two preregistrations, standalone", {"demo": True}),
    ("chained.demo", "chained after prereg_check", {"demo": True, **CHAIN}),
    (
        "chained.duplicates",
        "the same preregistration twice in the chained table: compared once",
        {**OER, **CHAIN, "double": True},
    ),
    ("all_fail", "every comparison fails: error light", {**OER, "fake": "error"}),
    (
        "partial_fail",
        "one of two comparisons fails: the other is still reported",
        {"demo": True, **CHAIN, "fake": "partial"},
    ),
    # --- more branches
    (
        "empty_result",
        "RegCheck returns no dimensions: error light without a reason",
        {**OER, "fake": "empty"},
    ),
    (
        "odd_judgements",
        "judgements with spaces, capitals, unknown labels and NA; quote references",
        {**OER, "fake": "odd"},
    ),
    (
        "duplicate_links",
        "a paper linking the same registration twice (standalone)",
        one(osf("48ncu"), "p_dup", ["The preregistration is at osf.io/48ncu."]),
    ),
    (
        "unjoined_links",
        "links that do not join back to the paper: NA paper_id",
        {**one(["http://osf.io/48ncu", "https://OSF.io/48NCU/"], "p_http"), "fake": "echo"},
    ),
    (
        "chained.na_paper_id",
        "chained table without paper ids: the first paper is used",
        {"demo": True, **CHAIN, "na_paper_id": True},
    ),
    (
        "chained.no_links",
        "chained after prereg_check without links: prereg_check runs again",
        {**one([], "p_none", ["No links here."]), **CHAIN},
    ),
    (
        "chained.other_module",
        "chained after another module: prereg_check runs internally",
        {**OER, "pre": ["marginal"]},
    ),
    (
        "chained.both",
        "prereg_check and another module before reg_check",
        {"demo": True, "pre": ["prereg_check", "marginal"]},
    ),
    # --- the real regcheck_compare() with faked server calls
    (
        "echo.demo",
        "paper and prereg text sent to RegCheck (demo, ollama)",
        {"demo": True, "fake": "echo"},
    ),
    (
        "echo.groq_dimensions",
        "hosted client with a token and custom dimensions",
        {
            **OER,
            "fake": "echo",
            "args": {"client": "groq"},
            "dims": ["Sample size", "Hypotheses"],
            "envvars": {"REGCHECK_API_TOKEN": "tok"},
        },
    ),
    (
        "echo.base_url",
        "an explicit base_url (trailing slash stripped)",
        {**OER, "fake": "echo", "args": {"base_url": "http://localhost:9999/"}},
    ),
    (
        "echo.env_base_url",
        "REGCHECK_BASE_URL overrides the client default",
        {**OER, "fake": "echo", "envvars": {"REGCHECK_BASE_URL": "https://env.server/"}},
    ),
    (
        "echo.symbols",
        "Greek letters and symbols are transliterated before sending",
        {
            **one(
                osf("5xysn"),
                "p_sym",
                [
                    "We found α = .05 and η² = 0.15.",
                    "The effect was ≥ 0.2 – “large” … ±1 × 2 中文.",
                ],
            ),
            "fake": "echo",
        },
    ),
    (
        "echo.paperlist",
        "a paper list: each preregistration is compared with its own paper",
        {
            "papers": [
                {"url": osf("48ncu"), "id": "paper_a", "text": ["Paper A text.", "More of A."]},
                {"url": osf("5xysn"), "id": "paper_b", "text": ["Paper B text."]},
                {"url": [], "id": "paper_c", "text": ["No links in C."]},
            ],
            "fake": "echo",
        },
    ),
    (
        "paperlist.shared_prereg",
        "two papers linking the same preregistration: compared for each paper",
        {
            "papers": [
                {"url": osf("5xysn"), "id": "pa", "text": ["Paper A."]},
                {"url": osf("5xysn"), "id": "pb", "text": ["Paper B."]},
            ],
        },
    ),
    (
        "paperlist.aspredicted",
        "AsPredicted links of two papers collapse into one row with an NA paper_id",
        {
            "papers": [
                {"url": ["https://aspredicted.org/by8i8v.pdf", *osf("48ncu")], "id": "paper_a"},
                {"url": ["https://aspredicted.org/ve2qn.pdf", *osf("5xysn")], "id": "paper_b"},
            ],
            "fake": "echo",
        },
    ),
    (
        "paperlist.psychsci_demo",
        "a read paper list where only the demo paper has preregistrations",
        {"read": [*PSYCHSCI, "upstream/metacheck/inst/demos/to_err_is_human.json"]},
    ),
    (
        "paperlist.all_fail",
        "a paper list where every comparison fails: NA for every paper",
        {
            "papers": [
                {"url": osf("48ncu"), "id": "paper_a"},
                {"url": [], "id": "paper_b", "text": ["Nothing."]},
            ],
            "fake": "error",
        },
    ),
    # --- errors raised by regcheck_compare() itself
    ("unknown_client", "an unknown client", {**OER, "fake": "real", "args": {"client": "foo"}}),
    (
        "groq_no_token",
        "a hosted client without REGCHECK_API_TOKEN",
        {**OER, "fake": "real", "args": {"client": "groq"}},
    ),
    (
        "empty_text",
        "a paper without text",
        {**one(osf("5xysn"), "p_blank", [" ", ""]), "fake": "real"},
    ),
    (
        "connfail",
        "the local RegCheck server is not running",
        {**OER, **CHAIN, "fake": "connfail"},
    ),
    (
        "unauthorized",
        "the local RegCheck server rejects the token",
        {**OER, **CHAIN, "fake": "unauthorized"},
    ),
    # --- the report's tables and module_report()
    ("tables.demo", "report tables (demo)", {"demo": True, "tables": True}),
    ("tables.odd", "report tables with odd judgements", {**OER, "fake": "odd", "tables": True}),
    (
        "tables.echo",
        "report tables of the echoed texts",
        {"demo": True, "fake": "echo", "tables": True},
    ),
    (
        "tables.shared_prereg",
        "one section per preregistration, across papers",
        {
            "papers": [
                {"url": osf("5xysn"), "id": "pa", "text": ["Paper A."]},
                {"url": osf("5xysn", "48ncu"), "id": "pb", "text": ["Paper B."]},
            ],
            "tables": True,
        },
    ),
    ("report.demo", "module_report() of the demo paper", {"demo": True, "report": True}),
    (
        "report.all_fail",
        "module_report() when RegCheck fails",
        {**OER, "fake": "error", "report": True},
    ),
    (
        "report.no_links",
        "module_report() without preregistrations",
        {**one([], "p_none", ["No links here."]), "report": True},
    ),
]


def py_expr(code: str) -> dict[str, Any]:
    return {"$expr": {"r": code, "py": "None"}}


ROW_TEXT = [
    (
        "testthat",
        "paper_id, link and ia_url are skipped",
        'data.frame(paper_id = "p1", id = "abc123", link = "https://osf.io/abc123", '
        'ia_url = "https://web.archive.org/abc", hypotheses = "We predict A > B.", '
        'sample_size = "N = 100")',
        'pd.DataFrame({"paper_id": ["p1"], "id": ["abc123"], "link": ["https://osf.io/abc123"], '
        '"ia_url": ["https://web.archive.org/abc"], "hypotheses": ["We predict A > B."], '
        '"sample_size": ["N = 100"]})',
    ),
    (
        "empty",
        "NA and blank fields are skipped",
        'data.frame(paper_id = "p1", link = NA_character_, ia_url = "", '
        'hypotheses = NA_character_, sample_size = "   ")',
        'pd.DataFrame({"paper_id": ["p1"], "link": pd.Series([None], dtype="string"), '
        '"ia_url": [""], "hypotheses": pd.Series([None], dtype="string"), '
        '"sample_size": ["   "]})',
    ),
    (
        "types",
        "numbers, logicals, tabs and newlines",
        'data.frame(n = 1/3, big = 1e5, flag = TRUE, int = 7L, txt = "a\\nb", tab = "\\t", '
        'pad = "  keep  ", stringsAsFactors = FALSE)',
        'pd.DataFrame({"n": [1/3], "big": [1e5], "flag": [True], '
        '"int": pd.Series([7], dtype="Int64"), "txt": ["a\\nb"], "tab": ["\\t"], '
        '"pad": ["  keep  "]})',
    ),
    (
        "first_row",
        "only the first row is used",
        'data.frame(id = c("a", "b"), hypotheses = c(NA, "second"))',
        'pd.DataFrame({"id": ["a", "b"], "hypotheses": pd.Series([None, "second"], '
        'dtype="string")})',
    ),
    (
        "no_rows",
        "a table without rows",
        "data.frame(id = character(0), hypotheses = character(0))",
        'pd.DataFrame({"id": pd.Series([], dtype="string"), '
        '"hypotheses": pd.Series([], dtype="string")})',
    ),
]

SRC = (
    '(function(row) { e <- new.env(parent = asNamespace("metacheck")); '
    'source(system.file("modules", "reg_check.R", package = "metacheck"), local = e); '
    "e$prereg_row_text(row) })"
)
PYFN = '__import__("pytacheck.modules.reg_check", fromlist=["_"]).prereg_row_text'


cases: list[dict[str, Any]] = [
    # plain module cases (no HTTP: papers without links return before any request)
    {
        "id": "reg_check.module.no_links",
        "note": "module_run() directly: a test paper without links",
        "module": "reg_check",
        "args": {
            "paper": {
                "$expr": {
                    "r": '(function() { p <- test_paper("There are no links here."); '
                    'p$paper_id <- "p_plain"; p })()',
                    "py": '__import__("tests.mod_prereg.parity_support", fromlist=["_"])'
                    '.tp([], "p_plain", ["There are no links here."])',
                }
            }
        },
    },
    {
        "id": "reg_check.module.empty_paperlist",
        "note": "module_run() directly: an empty paper list (prereg_check errors)",
        "module": "reg_check",
        "args": {"paper": {"$expr": {"r": "paperlist()", "py": "pc.PaperList([])"}}},
    },
    {
        "id": "reg_check.module.no_links_paperlist",
        "note": "module_run() directly: a paper list without links",
        "module": "reg_check",
        "args": {"paper": {"$read": PSYCHSCI}},
    },
]
for cid, note, spec in CASES:
    py_args = {k: v for k, v in spec.items() if k != "papers"}
    if spec.get("papers"):
        py_args["papers"] = spec["papers"]
    cases.append(
        {
            "id": f"reg_check.{cid}",
            "note": note,
            "r": "identity",
            "py": "tests.mod_reg.parity_support.run_reg",
            "args": {"x": py_expr(r_expr(spec))},
            "py_drop": ["x"],
            "py_args": py_args,
        }
    )
for cid, note, r_row, py_row in ROW_TEXT:
    cases.append(
        {
            "id": f"prereg_row_text.{cid}",
            "note": note,
            "r": "identity",
            "py": "tests.mod_reg.parity_support.identity",
            "args": {"x": {"$expr": {"r": f"{SRC}({r_row})", "py": f"{PYFN}({py_row})"}}},
        }
    )

HEADER = """\
# Parity cases for the reg_check module (inst/modules/reg_check.R).
# Generated by tests/mod_reg/make_cases.py; each module case runs
# module_run(<paper>, "reg_check") on metacheck's recorded OSF/AsPredicted
# responses with the RegCheck server faked (testthat::with_mocked_bindings in R,
# unittest.mock in Python: tests/mod_reg/parity_support.py::run_reg).
"""


def _quote_all(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')


class _Dumper(yaml.SafeDumper):
    pass


_Dumper.add_representer(str, _quote_all)

out = ROOT / "parity" / "cases" / "mod_reg.yaml"
out.write_text(
    HEADER
    + yaml.dump(
        {"area": "mod_reg", "cases": cases},
        Dumper=_Dumper,
        sort_keys=False,
        allow_unicode=True,
        width=10_000,
    ),
    encoding="utf-8",
)
print(f"wrote {len(cases)} cases to {out.relative_to(ROOT)}")
