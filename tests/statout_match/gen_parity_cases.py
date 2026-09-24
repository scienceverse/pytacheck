"""Generate ``parity/cases/statout_match.yaml`` (every string quoted).

Run ``python tests/statout_match/gen_parity_cases.py`` after changing the cases,
then ``python -m parity generate --area statout_match``.

Each case evaluates an R expression (``identity(x = <expr>)``) on the R side and
a helper from ``tests/statout_match/_helpers.py`` on the Python side, both built
here from the same data. ``NA`` is spelled ``"__NA__"`` in the data.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

H = "tests.statout_match._helpers"
NA = "__NA__"
JASP = "upstream/metacheck/tests/testthat/fixtures/formats/sample.jasp"
OMV = "upstream/metacheck/tests/testthat/fixtures/formats/sample.omv"
PROBLEM_XML = "upstream/metacheck/tests/testthat/fixtures/problems/0956797615569889.xml"
PSYCHSCI = "upstream/metacheck/tests/testthat/fixtures/psychsci"
cases: list[dict[str, Any]] = []


# ---------------------------------------------------------------- R literals


def rs(x: Any) -> str:
    """An R scalar literal."""
    if x is None or x == NA:
        return "NA"
    if isinstance(x, bool):
        return "TRUE" if x else "FALSE"
    if isinstance(x, int):
        return f"{x}L"
    if isinstance(x, float):
        return repr(x)
    return json.dumps(x, ensure_ascii=True)


def rchr(xs: list[Any]) -> str:
    """A character vector literal."""
    if not xs:
        return "character(0)"
    return "as.character(c(" + ", ".join(rs(x) for x in xs) + "))"


def rlist(xs: list[Any]) -> str:
    """An unnamed list of scalars (``NULL`` for None)."""
    return "list(" + ", ".join("NULL" if x is None else rs(x) for x in xs) + ")"


def rcontents(rows: list[list[Any]] | None) -> str:
    if rows is None:
        return "NULL"
    return "list(" + ", ".join(rchr(r) for r in rows) + ")"


def r_long(spec: dict[str, Any]) -> str:
    kind = spec["kind"]
    if kind == "file":
        code = (
            f"metacheck::stat_results_long(metacheck::read_stat_tables({rs(spec['path'])}), "
            f"source_file = {rs(spec['source_file'])})"
        )
    elif kind == "rout":
        code = (
            "metacheck::stat_results_long(metacheck::read_r_output("
            f"readLines({rs(spec['path'])}), source_label = {rs(spec['source_file'])}, "
            f"code_lines = readLines({rs(spec['code'])})), source_file = {rs(spec['source_file'])})"
        )
    elif kind == "df":
        types = spec.get("types", {})
        cols = ", ".join(
            f"{json.dumps(k)} = {'as.numeric(' + rchr(v) + ')' if types.get(k) == 'dbl' else rchr(v)}"
            for k, v in spec["cols"].items()
        )
        code = f"data.frame({cols}, stringsAsFactors = FALSE, check.names = FALSE)"
    else:
        raise ValueError(kind)
    if spec.get("drop"):
        code = f"local({{x <- {code}; x[setdiff(names(x), {rchr(spec['drop'])})]}})"
    return code


def r_output(spec: Any) -> str:
    if spec is None:
        return "NULL"
    if isinstance(spec, list):
        parts = [f"list(file = {rs(s['source_file'])}, long = {r_long(s)})" for s in spec]
        return "list(" + ", ".join(parts) + ")"
    return r_long(spec)


def r_paper(spec: dict[str, Any]) -> str:
    kind = spec["kind"]
    if kind == "demo":
        return "metacheck::demopaper()"
    if kind == "texts":
        return f"metacheck::test_paper({rchr(spec['texts'])})"
    if kind in ("read", "paperlist"):
        return f"metacheck::read({rs(spec['path'])})"
    if kind == "eq":
        return f"metacheck::extract_eq(metacheck::test_paper({rchr(spec['texts'])}))"
    if kind == "extract_tests":
        return f"metacheck::extract_tests(metacheck::test_paper({rchr(spec['texts'])}))"
    if kind == "null":
        return "NULL"
    if kind == "table_paper":
        tabs = spec["tables"]
        tid = "c(" + ", ".join(rs(int(t["table_id"])) for t in tabs) + ")"
        sid = (
            "as.integer(c("
            + ", ".join("NA" if t.get("section_id") in (None, NA) else str(t["section_id"]) for t in tabs)
            + "))"
        )
        contents = "list(" + ", ".join(rcontents(t.get("contents")) for t in tabs) + ")"
        return (
            "local({"
            f"p <- metacheck::test_paper({rchr(spec['texts'])}); "
            f"p$text$section_id <- as.numeric({rchr(spec['section_ids'])}); "
            f"p$table <- data.frame(table_id = {tid}, section_id = {sid}, html = NA_character_, "
            "caption = NA_character_, page_number = NA_integer_); "
            f"p$table$contents <- {contents}; p}})"
        )
    raise ValueError(kind)


# ---------------------------------------------------------------- case builders


def expr_case(id_: str, r_code: str, py_fn: str, py_args: dict[str, Any], **extra: Any) -> None:
    cases.append(
        {
            "id": id_,
            "r": "identity",
            "py": f"{H}.{py_fn}",
            "args": {"x": {"$expr": {"r": r_code, "py": "None"}}},
            "py_drop": ["x"],
            "py_args": py_args,
            **extra,
        }
    )


def fn_case(id_: str, r: str, py: str, args: dict[str, Any], **extra: Any) -> None:
    cases.append({"id": id_, "r": r, "py": py, "args": args, **extra})


# ================================================================ stato-map

for name in ["_STATO_LABELS", "_MC_STAT_LABELS", "_MC_STAT_MAP", "_STATO_MAP"]:
    rname = "." + name.lstrip("_")
    expr_case(
        f"stato_map.vocab.{name.lstrip('_')}",
        f"local({{v <- metacheck:::{rname}; v <- v[!duplicated(names(v))]; "
        "list(names = names(v), values = unname(v))})",
        "vocab",
        {"name": name},
    )
expr_case(
    "stato_map.vocab.STATO_BY_CALL",
    "lapply(metacheck:::.STATO_BY_CALL, function(v) list(names = names(v), values = unname(v)))",
    "vocab",
    {"name": "_STATO_BY_CALL"},
)
expr_case("stato_map.vocab.MC_STAT_NS", "metacheck:::.MC_STAT_NS", "vocab", {"name": "_MC_STAT_NS"})

KEYS_R = "c(names(metacheck:::.STATO_MAP), names(metacheck:::.MC_STAT_MAP))"
expr_case(
    "stato_type_column.vocab_keys",
    f"lapply({KEYS_R}, metacheck::stato_type_column)",
    "stato_batch",
    {"vocab": "keys"},
)
expr_case(
    "stato_type_column.vocab_upper",
    f'lapply(paste0("  ", toupper({KEYS_R}), " "), metacheck::stato_type_column)',
    "stato_batch",
    {"vocab": "upper"},
)
expr_case(
    "stato_type_column.vocab_variant",
    f'lapply(paste0({KEYS_R}, "[gg]"), metacheck::stato_type_column)',
    "stato_batch",
    {"vocab": "variant"},
)
TRICKY = [
    "t", "T", " F ", "Mean in group control", "mean of x", "mean difference",
    "sample.size.1", "pearson_estimate.2", "df1", "df2", "bf10", "quart1", "mean (y)",
    "sd (score)", "prob (f-statistic)", "Prob (F-statistic)", "stat[stud]", "stat[welc]",
    "stat[mann]", "stat[bf]", "stat", "F[gg]", "p[hf]", "df[none]", "ss[gg]", "gg", "hf",
    "", "   ", NA, "Unknown Column", "  Custom Label  ", "χ²", "Χ²",
    "η²", "ω²", "BF₁₀", "BF₀₁", "Pr(>|t|)",
    "t.ratio", "Adj. R²", "adj. r-squared", "W", "V", "S", "statistic", "d", "cohen.d",
    "x.1", "a.b.2", "(x).3", "mean in group ", "lower.cl", "Error %", "error", "Sum of Sq",
    "prop 1", "n / valid", "hypothesis test / wald chi-square", "some (thing) else",
    "abc (d)", "ABC (D)", "p.value.3", "Df", "Resid. Df", "Pr(>F)", "\tp\n",
]  # fmt: skip
expr_case(
    "stato_type_column.tricky",
    f"lapply({rchr(TRICKY)}, metacheck::stato_type_column)",
    "stato_batch",
    {"headers": TRICKY},
)
fn_case(
    "stato_type_column.null",
    "stato_type_column",
    "pytacheck.statout.stato_map.stato_type_column",
    {"header": {"$null": True}},
)
CALL_PAIRS = [
    ("W", "shapiro.test"), ("w", "wilcox.test"), ("V", "wilcox.test"),
    ("statistic", "wilcox.test"), ("S", "cor.test"), ("t", "cor.test"), ("z", "cor.test"),
    ("W", "SHAPIRO.TEST"), ("statistic", "t.test"), ("statistic", "TtestResult"),
    ("statistic", "MannwhitneyuResult"), ("statistic", "KruskalResult"),
    ("statistic", "WilcoxonResult"), ("statistic", "KstestResult"),
    ("d", "ks.test"), ("d", "cohen.d"), ("D", "cohens_d"), ("d", "repeated_measures_d"),
    ("W", "unknown.fn"), ("W", ""), ("W", NA), ("t", NA),
    ("Kruskal-Wallis chi-squared", "kruskal.test"), ("X-squared", "chisq.test"),
    ("statistic", NA), ("statistic", "lm"), ("F", "var.test"), ("mean in group a", "t.test"),
    ("W[gg]", "shapiro.test"), ("Bartlett's K-squared", "bartlett.test"),
    ("McNemar's chi-squared", "mcnemar.test"), ("Friedman chi-squared", "friedman.test"),
    ("X-squared", "prop.test"), ("statistic", "Ttest_indResult"), ("", "t.test"),
]  # fmt: skip
expr_case(
    "stato_type_column.call_fn",
    f"mapply(metacheck::stato_type_column, {rchr([h for h, _ in CALL_PAIRS])}, "
    f"{rchr([c for _, c in CALL_PAIRS])}, SIMPLIFY = FALSE, USE.NAMES = FALSE)",
    "stato_pairs",
    {"headers": [h for h, _ in CALL_PAIRS], "calls": [c for _, c in CALL_PAIRS]},
)
fn_case(
    "stato_type_column.call_null",
    "stato_type_column",
    "pytacheck.statout.stato_map.stato_type_column",
    {"header": "W", "call_fn": {"$null": True}},
)
SPV = [
    "Sig. (2-tailed)", "Type III Sum of Squares", "Type II Sum of Squares",
    "type i sum of squares", "Type  III   Sum of Squares", "Mean Square", "F", "Sig.",
    "-2 Log Likelihood Reduced", "Bootstrap / Std. Error",
    "Bootstrap / BCa 95% Confidence Interval / Lower",
    "Bootstrap / BCa 95% Confidence Interval / Upper", "Bootstrap / Bias",
    "t-test for Equality... / Std. Error Diff...", "Sig(2-tailed)...",
    "Change Statistics / F Change", "95% Confidence Interval for Exp(B) / Lower Bound",
    "Collinearity Statistics / VIF", "Error([%1:*NRHand, posture:]1)", "Error",
    "Error(within)", "errors", "n / valid", "N / Missing", "Unstandardized Coefficients / B",
    "Standardized Coefficients / Beta", "Paired Differences / Mean", "Something Unknown",
    "", "  ", NA, "Hypothesis Test / Wald Chi-Square", "Partial Eta Squared",
    "Noncent. Parameter", "Observed Power", "Cronbach's Alpha", "Std. Error of the Estimate",
    "Frequency", "Valid Percent", "Wald", "Score", "a / b / c", "x /y", "F[gg]",
    "Mean in group a",
]  # fmt: skip
expr_case(
    "spv_stato_type_label.labels",
    f"lapply({rchr(SPV)}, metacheck:::.spv_stato_type_label)",
    "spv_batch",
    {"labels": SPV},
)
expr_case(
    "spv_stato_type_label.vocab_upper",
    f'lapply(paste0("  ", toupper({KEYS_R}), " "), metacheck:::.spv_stato_type_label)',
    "spv_batch",
    {"vocab": "upper"},
)
fn_case(
    "spv_stato_type_label.null",
    "metacheck:::.spv_stato_type_label",
    "pytacheck.statout.stato_map._spv_stato_type_label",
    {"label": {"$null": True}},
)
BY_CALL = [
    ("w", "shapiro.test"), ("statistic", "KSTESTRESULT"), ("x", "t.test"), ("t", ""),
    ("t", NA), ("w", "nope"), ("W", "shapiro.test"), (NA, "t.test"),
    ("kruskal-wallis chi-squared", "Kruskal.Test"),
]  # fmt: skip
expr_case(
    "stato_by_call.pairs",
    f"mapply(metacheck:::.stato_by_call, {rchr([k for k, _ in BY_CALL])}, "
    f"{rchr([c for _, c in BY_CALL])}, SIMPLIFY = FALSE, USE.NAMES = FALSE)",
    "by_call_pairs",
    {"keys": [k for k, _ in BY_CALL], "calls": [c for _, c in BY_CALL]},
)
fn_case(
    "stato_by_call.null_call",
    "metacheck:::.stato_by_call",
    "pytacheck.statout.stato_map._stato_by_call",
    {"key": "w", "call_fn": {"$null": True}},
)
STRIP = ["f[gg]", "p[hf]", "stat[stud]", "a[b][c]", "x[]", "noop", NA, "[x]", "a[b]c", ""]
expr_case(
    "stato_strip_variant.vector",
    f"metacheck:::.stato_strip_variant({rchr(STRIP)})",
    "strip_variant",
    {"key": STRIP},
)

# ================================================================ match-reported internals

NORM_VALUES = [
    ".06", "< .001", "<.001", "> .05", ">0.05", "1,234.5", "-.5", "1.5e-05", "2.3E+02",
    "abc", "", NA, "2.20", "183.5", "12 345", "0.6810", "–0.5", "−0.5", " 7 ",
    "3.14abc", "1.2.3", "1e", "p", ".", "-", "+.5", "0", "00012", "4. Neuroticism",
    "[.16, .29]", "28)", "1e400", "< -2", ">  .5e1", "0.00", "<", "1.000", 0.681, 100000.0, 28,
    1e-05, 123456789.123, -0.5, 0.1 + 0.2,
]  # fmt: skip
expr_case(
    "norm_value.batch",
    f"lapply({rlist(NORM_VALUES)}, metacheck:::.norm_value)",
    "batch",
    {"fn": "match_reported._norm_value", "x": NORM_VALUES},
)
fn_case(
    "norm_value.null",
    "metacheck:::.norm_value",
    "pytacheck.statout.match_reported._norm_value",
    {"x": {"$null": True}},
)
INTERVALS = [
    "[.16, .29]", "[.16; .29]", "[.16-.29]", "[.16–.29]", "[.16—.29]",
    "[-0.16, -.29]", "[-.16 - -.29]", "[-.16--.29]", "[1, 2, 3]", "[.16,]", "[,.29]",
    "[a, b]", "(.16, .29)", NA, "[ .16 , .29 ]", "[< .001, .05]", "[1.2 – 3.4]", "[]",
    "[.16 .29]", "[1e-3, 2e-3]", " [0.5, 0.9] ", "[0.5, 0.9] extra", "[1,234.5; 2,000]",
    "[.16, .29", "", "[-1 - 2]",
]  # fmt: skip
expr_case(
    "norm_interval.batch",
    f"lapply({rchr(INTERVALS)}, metacheck:::.norm_interval)",
    "batch",
    {"fn": "match_reported._norm_interval", "x": INTERVALS},
)
DFS = [
    "(28)", "(2, 57)", "(1, 2, 3)", "28", NA, "", "()", "(a)", "(2, b)", "(97.7)", " (28) ",
    "(1,234)", "((3))", "(2,)", "(,2)", "(2 , 2159)", "(< 5)",
]  # fmt: skip
expr_case(
    "norm_df.batch",
    f"lapply({rchr(DFS)}, metacheck:::.norm_df)",
    "batch",
    {"fn": "match_reported._norm_df", "x": DFS},
)
fn_case(
    "norm_df.null",
    "metacheck:::.norm_df",
    "pytacheck.statout.match_reported._norm_df",
    {"x": {"$null": True}},
)
FAMILIES = [
    "p", "P-value", "Pr(>F)", "Pr(>|t|)", "pval", "p[stud]", "stat[stud]", "stat[welc]",
    "stat[mann]", "stat[bf]", "es[stud]", "cil[stud]", "ciles", "ciues", "t", "t value",
    "t-test", "Student's t", "student t", "F", "F change", "ANOVA", "manova", "f-test",
    "χ²", "chi-square", "X-squared", "x²", "Z", "z value", "W", "Wilcoxon",
    "Mann-Whitney U", "U", "Q", "Cohen's d", "d", "ds", "es", "effect size", "Hedges' g",
    "Mean Difference", "md", "median", "med", "η²", "ηp²", "eta",
    "partial eta squared", "beta", "meta", "ω²", "omega", "odds ratio", "rb",
    "rank-biserial", "BF10", "bayes factor", "r", "Correlation", "pearson", "rs", "rho",
    "ρ", "Spearman", "β", "Beta", "standardized", "stdcoef", "std.coef", "b",
    "Estimate", "unstandardized", "SE", "Std. Error", "standard error", "M", "mean", "SD",
    "Std. Deviation", "N", "ns", "sample size", "α", "alpha", "Cronbach's alpha", "R2",
    "R2m", "R2c", "Rsq", "R-squared", "r squared", "adj. r sq", "LCL", "ci_lower",
    "ci.lower", "conf.low", "ci.lb", "cilow", "UCL", "ci_upper", "conf.high", "ci.ub",
    "cihig", "cihigh", "95% CI", "lower", "upper", "interval", "df", "df1", "df2", "Df",
    "  multiple   spaces  ", "", NA, "Condition", "age", "dof", "T", "t(28)", "d[stud]",
    "Mean\tDifference", "stat [stud]",
]  # fmt: skip
expr_case(
    "stat_family.names",
    f"metacheck:::.stat_family({rchr(FAMILIES)})",
    "stat_family",
    {"x": FAMILIES},
)
expr_case(
    "stat_family.vocab_keys",
    f"metacheck:::.stat_family({KEYS_R})",
    "stat_family",
    {"vocab": "keys"},
)
fn_case(
    "stat_family.null",
    "metacheck:::.stat_family",
    "pytacheck.statout.match_reported._stat_family",
    {"name": {"$null": True}},
)
SHARE = [
    ("compassion_mindset", "compassion_mindset_1"), ("emo_total_unlimited", "emo_total_limited"),
    ("a_b", "a_b"), ("", "x"), (NA, "x"), ("x", NA), ("Group A", "group_a"),
    ("abcd", "ABCD"), ("abc", "abc"), ("wellbeing-score", "score of wellbeing"),
    ("été_score", "score"), ("ab12cd", "ab12cd"), ("Illusion_score_sync", "illusion"),
]  # fmt: skip
expr_case(
    "sites_share_variable.pairs",
    f"mapply(metacheck:::.sites_share_variable, {rchr([a for a, _ in SHARE])}, "
    f"{rchr([b for _, b in SHARE])}, SIMPLIFY = FALSE, USE.NAMES = FALSE)",
    "share_pairs",
    {"a": [a for a, _ in SHARE], "b": [b for _, b in SHARE]},
)

TEXTS = [
    "The illusion was stronger in the synchronous condition, t(8) = 6.90, p < .001, "
    "d = 2.30, 95% CI = [1.38, 2.77].",
    "Descriptives: M = 1.93, SD = 0.86, N = 9.",
    "Another test t(8) = 5.05, p = .001, d = 1.68 and a fake t(8) = 9.99, p = .5.",
    "We found M = 1.93, SD = 0.76, W = 183.5, p = .791, rb = -0.16.",
    "An ANOVA, F(2, 57) = 4.21, p = .020, ηp² = .13, and r = -.27.",
    "No statistics in this sentence.",
]
expr_case(
    "recompose_eq.demo",
    "unname(metacheck:::.recompose_eq(metacheck::extract_eq(metacheck::demopaper())))",
    "recompose",
    {"source": "demo"},
)
expr_case(
    "recompose_eq.texts",
    f"unname(metacheck:::.recompose_eq(metacheck::extract_eq(metacheck::test_paper({rchr(TEXTS)}))))",
    "recompose",
    {"source": "texts", "texts": TEXTS},
)
EQ_COLS = {
    "text_id": [3, 3, 3, 3, 1, 1, 12, 12, 2, 2, 2],
    "grp_id": [1.0, 1.0, 1.0, 1.0, 2.0, 2.0, 1.0, 1.0, 5.0, 5.0, 5.0],
    "lhs": ["t", "p", "95% CI", "Condition", "F", "p", "r", "BF10", "χ²", "junk", NA],
    "df": ["(28)", NA, NA, NA, "(2, 57)", NA, "(1, 2, 3)", NA, "(3)", NA, NA],
    "comp": ["=", "<", "=", "=", "=", ">", "=", "=", "=", "=", "="],
    "rhs": ["2.20", ".05", "[.16, .29]", "3", "4.21", ".05", "-.27", "abc", "12.5", "1", "2"],
}
EQ_TYPES = {"text_id": "int", "grp_id": "dbl"}


def r_eq_df(cols: dict[str, list[Any]], types: dict[str, str]) -> str:
    parts = []
    for k, v in cols.items():
        t = types.get(k, "chr")
        if t == "int":
            parts.append(f"{k} = as.integer(c({', '.join(rs(x) if x != NA else 'NA' for x in v)}))")
        elif t == "dbl":
            parts.append(f"{k} = as.numeric(c({', '.join('NA' if x == NA else repr(float(x)) for x in v)}))")
        else:
            parts.append(f"{k} = {rchr(v)}")
    return f"data.frame({', '.join(parts)}, stringsAsFactors = FALSE)"


expr_case(
    "recompose_eq.df",
    f"unname(metacheck:::.recompose_eq({r_eq_df(EQ_COLS, EQ_TYPES)}))",
    "recompose",
    {"source": "df", "cols": EQ_COLS, "types": EQ_TYPES},
)
EQ_MIN = {"text_id": [5, 5, 4], "lhs": ["t", "p", "d"], "rhs": ["3.1", "< .01", ".4"]}
expr_case(
    "recompose_eq.no_grp_comp_df",
    f"unname(metacheck:::.recompose_eq({r_eq_df(EQ_MIN, {'text_id': 'int'})}))",
    "recompose",
    {"source": "df", "cols": EQ_MIN, "types": {"text_id": "int"}},
)
expr_case(
    "recompose_eq.null",
    "unname(metacheck:::.recompose_eq(NULL))",
    "recompose",
    {"source": "null"},
)
expr_case(
    "tests_from_extract.texts",
    f"metacheck:::.tests_from_extract(metacheck::extract_tests(metacheck::test_paper({rchr(TEXTS)})))",
    "tests_from_extract",
    {"texts": TEXTS},
)
expr_case(
    "tests_from_extract.demo",
    "metacheck:::.tests_from_extract(metacheck::extract_tests(metacheck::demopaper()))",
    "tests_from_extract",
    {"source": "demo"},
)

# ================================================================ match_reported_output

LONG_JASP = {"kind": "file", "path": JASP, "source_file": "sample.jasp"}
LONG_OMV = {"kind": "file", "path": OMV, "source_file": "sample.omv"}
LONG_MODEL = {
    "kind": "rout",
    "path": "tests/statout_core/data/model_chain.Rout",
    "code": "tests/statout_core/data/model_chain.R",
    "source_file": "model_chain.R",
}
JASP_TEXTS = [
    "The illusion was stronger in the synchronous condition, t(8) = 6.90, p < .001, "
    "d = 2.30, 95% CI = [1.38, 2.77].",
    "Descriptives: M = 1.93, SD = 0.86, N = 9.",
    "Another test t(8) = 5.05, p = .001, d = 1.68 and a fake t(8) = 9.99, p = .5.",
    "The asynchronous condition was t(8) = 5.05, p = .010, d = 2.00.",
    "A lone correlation r = .43 and a lone d = 2.3.",
    "Descriptives: M = -0.15, SD = 1.19; M = 0.70, SD = 0.96.",
    "Mean difference md = 2.07, t(8) = 6.9.",
    "Both effects held, t(8) = 6.90, p < .001, and t(8) = 5.05, p < .001.",
]

# synthetic output: residual union, row-label plausibility, fallbacks, censoring
SYN = {
    "source_file": ["syn.omv"] * 7 + ["syn.omv"] * 2 + ["syn.omv"] * 3 + ["syn.omv"] * 3
    + ["syn.omv"] * 2 + ["script.R"] * 4 + ["meta.R"] * 5 + ["chi.R"] * 3 + ["tt.R"] * 3
    + ["misc.R"] + ["mm.R"] * 2 + ["syn.omv"] * 2,
    "test_id": ["an_a92_gender"] * 4 + ["an_a92_residuals"] * 1 + ["an_a92_female_male"] * 2
    + ["rel_compassion_mindset_1", "rel_other"]
    + ["desc_compassion_mindset"] * 3 + ["desc_happiness"] * 3
    + ["an_b1_x_y"] * 2
    + ["reg_x"] * 4 + ["meta_1"] * 5 + ["chi_1"] * 3 + ["tt_ns"] * 3 + ["misc_1"]
    + ["r_l3_1", "r_l5_1"] + ["an_c1_z", "an_b1_residuals"],
    "analysis": ["ANOVA"] * 7 + ["Reliability"] * 2 + ["Descriptives"] * 6 + ["ANOVA"] * 2
    + ["Coefficients", "Coefficients", "Coefficients", "Coefficients"]
    + ["Model Results"] * 5 + ["Pearson's Chi-squared test"] * 3 + ["Welch"] * 3 + [NA]
    + [NA, NA] + ["ANOVA", "ANOVA"],
    "row_label": ["gender"] * 4 + ["residuals"] + ["female_male"] * 2
    + ["compassion_mindset_1", "grit"] + ["compassion_mindset"] * 3 + ["happiness"] * 3
    + ["x_y"] * 2 + ["x"] * 4 + [NA] * 5 + [NA] * 3 + [NA] * 3 + [NA] + ["m1", "m1"]
    + ["z", "residuals"],
    "statistic": ["F", "df", "p", "etaSq", "df", "t", "p", "alpha", "alpha",
                  "mean", "LCL", "UCL", "mean", "LCL", "UCL", "F", "p",
                  "Estimate", "Std. Error", "t value", "Pr(>|t|)",
                  "estimate", "se", "pval", "ci.lb", "ci.ub",
                  "X-squared", "df", "p-value", "t", "df", "p-value", "foo",
                  "Estimate", "R2m", "F", "df"],
    "value": ["6.76", "2", "0.0012", "0.006", "2159", "3.10", "0.002", "0.861", "0.79",
              "3.28", "3.18", "3.38", "4.10", "3.95", "4.25", "9.40", "0.004",
              "0.35", "0.05", "7.00", "1e-10",
              "0.40", "0.08", "<.0001", "0.24", "0.56",
              "12.5", "3", "0.0058", "1.2", "30", "0.24", "42.0",
              "1.23", "0.31", "5.5", "40"],
    "model_ref": [NA] * 33 + ["m1", "m1", NA, NA],
}  # fmt: skip
LONG_SYN = {"kind": "df", "cols": SYN}
# an NA test_id: dropped by split() -- but next to a residuals row it becomes an
# NA-named site whose val_in() errors (R bug, reproduced)
SYN_NA = {k: list(v) for k, v in SYN.items()}
SYN_NA["test_id"][35] = NA
LONG_SYN_NA = {"kind": "df", "cols": SYN_NA}
_resid = [i for i, t in enumerate(SYN["test_id"]) if t.endswith("_residuals")]
SYN_NA_NORESID = {k: [x for i, x in enumerate(v) if i not in _resid] for k, v in SYN_NA.items()}
LONG_SYN_NA_NORESID = {"kind": "df", "cols": SYN_NA_NORESID}
SYN_TEXTS = [
    "A one-way ANOVA showed an effect of gender, F(2, 2159) = 6.76, p = .001, "
    "η² = .006.",
    "Women scored higher than men, t(2159) = 3.10, p = .002.",
    "Participants reported M = 3.28, 95% CI = [3.18, 3.38], and the scale was reliable, "
    "Cronbach's alpha = .86.",
    "Happiness was high, M = 4.10, 95% CI = [3.95, 4.25], with alpha = .79.",
    "The standardized effect was beta = 0.35, SE = 0.05, t = 7.00, p < .001.",
    "The pooled effect was d = 0.40, SE = 0.08, p < .001, 95% CI = [0.24, 0.56].",
    "A chi-square test was significant, χ²(3) = 12.50, p = .006.",
    "The fabricated result was t(42) = 9.99, p = .001.",
    "The effect was not significant, t(30) = 1.20, p > .05.",
    "The mixed model showed b = 1.23, R2 = .31.",
    "The interaction was F(1, 40) = 9.40, p = .004.",
    "Nothing matched here, t(11) = 0.51, p = .62, d = 0.15.",
]
MODEL_TEXTS = [
    "The groups differed, F(1, 30) = 16.86, p < .001.",
    "The coefficient was b = 7.24, SE = 1.76, t = 4.11, p < .001.",
    "The intercept was b = 17.15, SE = 1.13, t = 15.25.",
    "Weight and mileage correlated, r = -.87, t(30) = -9.56, p < .001.",
]

TABLE_TEXTS = [
    "Results are shown in the tables.",
    "Table 1. Correlations among the study variables.",
    "Table 2. Regression coefficients predicting wellbeing.",
    "The effect of age was b = 0.05, SE = 0.02, t = 2.50, p = .013.",
]
TABLE_SECTIONS = ["1", "2", "3", "1"]
T1 = [
    ["Variable", "α", "M", "SD", "1.", "2.", "Correlations / 3."],
    ["1. Depression", ".86", "2.10", "0.55", "—", "", ""],
    ["2. Anxiety", ".81", "1.95", "0.60", ".45", "—", ""],
    ["3. Stress", ".79", "2.40", "0.71", ".38", ".52**", "—"],
    ["Note. * p < .05", "", "", "", "", "", ""],
]
T2 = [
    ["Predictor", "b", "SE", "β", "t", "p", "95% CI"],
    ["Intercept", "2.31", "0.12", "", "19.25", "< .001", "[2.07, 2.55]"],
    ["Age", "0.05", "0.02", ".21", "2.50", ".013", "[0.01, 0.09]"],
    ["Gender", "4 females", "n/a", "-", "1.2a", "0.20", "[.1; .3]"],
    ["Short", "1.5"],
]
T3 = [
    ["Measure", "Association with controllability-reappraisal slope / β11 (SE)",
     "Range / Actual", "", "3"],
    ["Wellbeing", "0.12 (0.04)", "1-7", "5.5", "7"],
    ["Stress", "-0.08 (0.03)", "2-6", NA, ""],
]  # fmt: skip
T4 = [["Only", "a", "header"]]
TABLE_PAPER = {
    "kind": "table_paper",
    "texts": TABLE_TEXTS,
    "section_ids": TABLE_SECTIONS,
    "tables": [
        {"table_id": 1, "section_id": 2, "contents": T1},
        {"table_id": 2, "section_id": 3, "contents": T2},
        {"table_id": 3, "section_id": NA, "contents": T3},
        {"table_id": 4, "section_id": 1, "contents": T4},
        {"table_id": 5, "section_id": 9, "contents": None},
    ],
}
TABLE_LONG = {
    "kind": "df",
    "cols": {
        "source_file": ["t.R"] * 12,
        "test_id": ["corr_1_2", "corr_2_3", "rel_dep", "rel_dep", "reg_age", "reg_age",
                    "reg_age", "reg_age", "reg_age", "reg_int", "reg_int", "reg_int"],
        "analysis": ["cor"] * 2 + ["alpha"] * 2 + ["lm"] * 8,
        "row_label": ["dep_anx", "anx_stress", "depression", "depression", "age", "age",
                      "age", "age", "age", "(Intercept)", "(Intercept)", "(Intercept)"],
        "statistic": ["r", "r", "alpha", "mean", "Estimate", "Std. Error", "beta",
                      "t value", "Pr(>|t|)", "Estimate", "Std. Error", "t value"],
        "value": ["0.452", "0.518", "0.862", "2.1", "0.0512", "0.0204", "0.21", "2.50",
                  "0.0131", "2.311", "0.118", "19.25"],
    },
}  # fmt: skip


def match_case(
    id_: str,
    paper: dict[str, Any],
    output: Any,
    include_tables: bool = False,
    min_components: int = 1,
    summary: bool = True,
    **extra: Any,
) -> None:
    call = (
        f"metacheck::match_reported_output({r_paper(paper)}, {r_output(output)}, "
        f"include_tables = {rs(include_tables)}, min_components = {rs(min_components)})"
    )
    args = {
        "paper": paper,
        "output": output,
        "include_tables": include_tables,
        "min_components": min_components,
    }
    expr_case(f"match_reported_output.{id_}", call, "match", args, **extra)
    if summary:
        expr_case(
            f"match_reported_output.{id_}.summary",
            f'attr({call}, "summary")',
            "match",
            {**args, "what": "summary"},
        )


match_case("demo_jasp", {"kind": "demo"}, LONG_JASP)
match_case("demo_jasp.tables", {"kind": "demo"}, LONG_JASP, include_tables=True)
match_case("texts_jasp.min3", {"kind": "texts", "texts": JASP_TEXTS}, LONG_JASP, min_components=3)
match_case(
    "texts_list_with_empty",
    {"kind": "texts", "texts": JASP_TEXTS},
    [{"kind": "df", "cols": {"test_id": [], "statistic": [], "value": []}, "source_file": "e.R"},
     LONG_JASP],
)
NUMERIC_LONG = {
    "kind": "df",
    "cols": {
        "test_id": ["a", "a", "a", "b", "b", "b"],
        "statistic": ["t", "df", "p", "t", "df", "p"],
        "value": ["6.9", "8", "0.000124", "n/a", "\u2014", "0.5"],
    },
}
match_case("texts_messy_values", {"kind": "texts", "texts": JASP_TEXTS}, NUMERIC_LONG)
match_case(
    "texts_numeric_values",
    {"kind": "texts", "texts": JASP_TEXTS},
    {
        "kind": "df",
        "types": {"value": "dbl"},
        "cols": {
            "test_id": ["a", "a", "a", "a", "b", "b"],
            "statistic": ["t", "df", "p", "d", "M", "SD"],
            "value": ["6.89966035949175", "8", "0.000124571109365704", "2.29988678649725",
                      "1.925925926", "0.862454149896004"],
        },
    },
)
match_case("texts_jasp", {"kind": "texts", "texts": JASP_TEXTS}, LONG_JASP)
match_case("texts_jasp.min2", {"kind": "texts", "texts": JASP_TEXTS}, LONG_JASP, min_components=2)
match_case(
    "texts_stat_output_list",
    {"kind": "texts", "texts": JASP_TEXTS},
    [LONG_JASP, LONG_OMV],
)
match_case("texts_omv", {"kind": "texts", "texts": JASP_TEXTS}, LONG_OMV)
match_case("eq_df_jasp", {"kind": "eq", "texts": JASP_TEXTS}, LONG_JASP)
match_case("extract_tests_df_jasp", {"kind": "extract_tests", "texts": JASP_TEXTS}, LONG_JASP)
match_case("null_output", {"kind": "texts", "texts": JASP_TEXTS}, None)
match_case("empty_list_output", {"kind": "texts", "texts": JASP_TEXTS}, [])
match_case("no_tests", {"kind": "texts", "texts": ["No numbers here.", "None here."]}, LONG_JASP)
match_case("null_paper", {"kind": "null"}, LONG_JASP)
match_case("synthetic", {"kind": "texts", "texts": SYN_TEXTS}, LONG_SYN)
match_case(
    "synthetic.no_test_id",
    {"kind": "texts", "texts": SYN_TEXTS},
    {**LONG_SYN, "drop": ["test_id"]},
)
match_case(
    "synthetic.no_model_ref",
    {"kind": "texts", "texts": SYN_TEXTS},
    {**LONG_SYN, "drop": ["model_ref", "row_label", "analysis", "source_file"]},
)
match_case("synthetic.eq", {"kind": "eq", "texts": SYN_TEXTS}, LONG_SYN)
match_case("synthetic.na_test_id", {"kind": "texts", "texts": SYN_TEXTS}, LONG_SYN_NA, summary=False)
match_case(
    "synthetic.na_test_id_censored_only",
    {"kind": "texts", "texts": ["The effect was t < 1.5, p > .05."]},
    LONG_SYN_NA,
)
match_case(
    "synthetic.na_test_id_no_residuals",
    {"kind": "texts", "texts": SYN_TEXTS},
    LONG_SYN_NA_NORESID,
)
match_case("model_chain", {"kind": "texts", "texts": MODEL_TEXTS}, LONG_MODEL)
# the testthat reproducibility_check() examples (console output of the repro
# fixtures ok.R / model_object.R, captured with source(echo = TRUE))
for name, texts in [
    ("ok", ["t(4.96) = -4.04, p = .010."]),
    ("model_object", ["The model F(1, 5) = 15.00, p = .012, b = 3.50, SE = 0.90, t = 3.87."]),
]:
    match_case(
        f"repro_{name}",
        {"kind": "texts", "texts": texts},
        {
            "kind": "rout",
            "path": f"tests/statout_match/data/{name}.Rout",
            "code": f"tests/statout_match/data/{name}.R",
            "source_file": f"{name}.R",
        },
    )
match_case("tables", TABLE_PAPER, TABLE_LONG, include_tables=True)
match_case("tables.off", TABLE_PAPER, TABLE_LONG, include_tables=False)
match_case("problem_xml.tables", {"kind": "read", "path": PROBLEM_XML}, LONG_JASP, include_tables=True)
match_case("psychsci_first", {"kind": "read", "path": f"{PSYCHSCI}/0956797613520608.json"}, LONG_JASP)
match_case(
    "psychsci_first.tables",
    {"kind": "read", "path": f"{PSYCHSCI}/0956797613520608.json"},
    LONG_JASP,
    include_tables=True,
)
match_case("paperlist_error", {"kind": "paperlist", "path": PSYCHSCI}, LONG_JASP, summary=False)

# ================================================================ match-table

CAPTIONS = [
    "Table 1. Correlations among variables", "Regression coefficients", "Fixed effects",
    "Fixed-effect estimates", "Reliability of the scales", "Cronbach's alpha values",
    "Internal consistency", "Descriptive statistics", "Means and standard deviations",
    "Mean and Standard Deviation", "Sample characteristics", "", NA, "CORRELATION MATRIX",
]  # fmt: skip
expr_case(
    "table_caption_family.batch",
    f"lapply({rchr(CAPTIONS)}, metacheck:::.table_caption_family)",
    "batch",
    {"fn": "match_table._table_caption_family", "x": CAPTIONS},
)
fn_case(
    "table_caption_family.null",
    "metacheck:::.table_caption_family",
    "pytacheck.statout.match_table._table_caption_family",
    {"caption": {"$null": True}},
)
HEADERS = ["", " ", "1.", "12", "3.", "1..", "-", "---", "- -", "M", "Range", NA, "1. Depression", "a1"]
expr_case(
    "table_header_ambiguous.batch",
    f"lapply({rchr(HEADERS)}, metacheck:::.table_header_ambiguous)",
    "batch",
    {"fn": "match_table._table_header_ambiguous", "x": HEADERS},
)
for name, content, caption in [
    ("t1", T1, "Table 1. Correlations among the study variables."),
    ("t1.no_caption", T1, NA),
    ("t2", T2, "Table 2. Regression coefficients predicting wellbeing."),
    ("t3", T3, "Descriptive statistics"),
    ("t4", T4, NA),
    ("empty_rows", [["a", "b"], []], NA),
]:
    expr_case(
        f"table_typed_cells.{name}",
        f"metacheck:::.table_typed_cells({rcontents(content)}, {rs(caption)})",
        "typed_cells",
        {"content": content, "caption": caption},
    )
    expr_case(
        f"table_matrix_cols.{name}",
        f"metacheck:::.table_matrix_cols({rcontents(content)})",
        "matrix_cols",
        {"content": content},
    )
    expr_case(
        f"table_tests_one.{name}",
        f"metacheck:::.table_tests_one(7L, {rcontents(content)}, {rs(caption)})",
        "tests_one",
        {"table_id": 7, "content": content, "caption": caption},
    )
expr_case(
    "table_matrix_cols.one_numbered",
    f"metacheck:::.table_matrix_cols({rcontents([['Var', '1.', 'M'], ['a', '.3', '2']])})",
    "matrix_cols",
    {"content": [["Var", "1.", "M"], ["a", ".3", "2"]]},
)
expr_case(
    "table_typed_cells.null",
    "metacheck:::.table_typed_cells(NULL, NA)",
    "typed_cells",
    {"content": None, "caption": NA},
)
expr_case(
    "table_tests.paper",
    f"metacheck:::.table_tests({r_paper(TABLE_PAPER)})",
    "table_tests",
    {"paper": TABLE_PAPER},
)
expr_case(
    "table_tests.demo",
    "metacheck:::.table_tests(metacheck::demopaper())",
    "table_tests",
    {"paper": {"kind": "demo"}},
)
expr_case(
    "table_tests.problem_xml",
    f"metacheck:::.table_tests(metacheck::read({rs(PROBLEM_XML)}))",
    "table_tests",
    {"paper": {"kind": "read", "path": PROBLEM_XML}},
)
expr_case(
    "table_caption.paper",
    f"lapply(c(2L, 3L, 1L, 9L, NA), function(s) metacheck:::.table_caption({r_paper(TABLE_PAPER)}, s))",
    "table_caption",
    {"paper": TABLE_PAPER, "section_ids": [2, 3, 1, 9, NA]},
)
expr_case(
    "table_caption.demo",
    "lapply(c(10L, 1L), function(s) metacheck:::.table_caption(metacheck::demopaper(), s))",
    "table_caption",
    {"paper": {"kind": "demo"}, "section_ids": [10, 1]},
)


class QuotedDumper(yaml.SafeDumper):
    pass


def _str(dumper: yaml.SafeDumper, data: str) -> yaml.Node:
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')


QuotedDumper.add_representer(str, _str)

if __name__ == "__main__":
    out = {"area": "statout_match", "cases": cases}
    path = Path(__file__).resolve().parents[2] / "parity/cases/statout_match.yaml"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(
            "# Parity cases for match_reported_output() and the STATO mapping\n"
            "# (R/match-reported.R, R/match-table.R, R/stato-map.R).\n"
            "# Generated by tests/statout_match/gen_parity_cases.py -- do not edit by hand.\n"
        )
        yaml.dump(out, fh, Dumper=QuotedDumper, sort_keys=False, allow_unicode=True, width=10000)
    print(len(cases))
