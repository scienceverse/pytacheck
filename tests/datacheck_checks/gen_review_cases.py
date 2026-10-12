"""Generate ``parity/cases/datacheck_checks_review.yaml`` (area ``datacheck_checks_review``).

Cases from the adversarial review of the datacheck_checks port: one targeted
case per divergence found (and fixed), the error paths of the scalar-name
helpers, and seeded batteries -- one ``$expr`` evaluating many inputs on each
side -- that pin down the ``strptime()`` emulation and the value classifiers.
Run from the repository root::

    .venv/bin/python tests/datacheck_checks/gen_review_cases.py
    .venv/bin/python -m parity generate --area datacheck_checks_review
"""

from __future__ import annotations

import math
import random
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "parity" / "cases" / "datacheck_checks_review.yaml"
DATA = "tests/datacheck_checks/data"
CK = "metacheck.datacheck.checks."
PH = "tests.datacheck_checks.parity_helpers."
#: the checks module inside a Python ``$expr`` (only ``pc``/``pd``/``np`` are bound there)
C = "__import__('metacheck.datacheck.checks', fromlist=['C'])"

CASES: list[dict[str, Any]] = []


def add(cid: str, r: str, args: dict[str, Any], py: str | None = None, **extra: Any) -> None:
    if py is None:
        py = CK + (("_" + r[1:].replace(".", "_")) if r.startswith(".") else r)
    spec: dict[str, Any] = {"id": cid, "r": r, "py": py, "args": args}
    spec.update(extra)
    CASES.append(spec)


def ex(r: str, py: str) -> dict[str, Any]:
    return {"$expr": {"r": r, "py": py}}


def rstr(s: str) -> str:
    """An R string literal."""
    out = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
    return f'"{out}"'


def r_chr(vals: list[str | None]) -> str:
    if not vals:
        return "character(0)"
    return "c(" + ", ".join("NA_character_" if v is None else rstr(v) for v in vals) + ")"


def r_num(vals: list[float | None]) -> str:
    def one(v: float | None) -> str:
        if v is None:
            return "NA"
        if math.isinf(v):
            return "Inf" if v > 0 else "-Inf"
        return repr(float(v))

    return "as.double(c(" + ", ".join(one(v) for v in vals) + "))" if vals else "double(0)"


def r_int(vals: list[int | None]) -> str:
    if not vals:
        return "integer(0)"
    return "as.integer(c(" + ", ".join("NA" if v is None else f"{v}L" for v in vals) + "))"


def py_num(vals: list[float | None]) -> str:
    def one(v: float | None) -> str:
        if v is None:
            return "None"
        return f"float('{v}')" if math.isinf(v) else repr(float(v))

    return "pd.Series([" + ", ".join(one(v) for v in vals) + "], dtype='float64')"


def chr_vec(vals: list[str | None]) -> dict[str, Any]:
    return ex(r_chr(vals), f"pd.Series({vals!r}, dtype='string')")


# -- strptime(): R's get_number() reads up to the field width ------------------------------

DATE = ex("metacheck:::.DATE_FMTS", f"{C}._DATE_FMTS")
DATETIME = ex("metacheck:::.DATETIME_FMTS", f"{C}._DATETIME_FMTS")

add(
    ".parse_frac.review.day_overflow",
    ".parse_frac",
    {"v": chr_vec(["2020-05-45", "2020-05-19", "2020-05-46", "2020-05-47"]), "fmts": DATE},
)
add(
    ".parse_frac.review.minute_overflow",
    ".parse_frac",
    {
        "v": chr_vec(["2020-05-19 10:65", "2020-05-19 10:61", "2020-05-19 10:59"]),
        "fmts": {"$chr": ["%Y-%m-%d %H:%M"]},
    },
)
add(
    ".parse_frac.review.second_overflow",
    ".parse_frac",
    {
        "v": chr_vec(["2020-05-19 10:00:75", "2020-05-19 10:00:59", "2020-05-19 10:00:61"]),
        "fmts": {"$chr": ["%Y-%m-%d %H:%M:%S"]},
    },
)
add(
    ".parse_frac.review.compact_hhmm",
    ".parse_frac",
    {
        "v": chr_vec(["2022_Sep_08_523", "2022_Sep_08_1523", "2022_Sep_8_923", "2022_Sep_08_052"]),
        "fmts": {"$chr": ["%Y_%b_%d_%H%M"]},
    },
)
add(
    ".parse_frac.review.hour_overflow",
    ".parse_frac",
    {
        "v": chr_vec(["2020-05-19_31h20", "2020-05-19_1h20", "2020-05-19_16h65"]),
        "fmts": {"$chr": ["%Y-%m-%d_%Hh%M"]},
    },
)
add(
    ".parse_frac.review.two_digit_year_day",
    ".parse_frac",
    {"v": chr_vec(["5/45/20", "5/4/205", "12/31/99"]), "fmts": {"$chr": ["%m/%d/%y"]}},
)
add(
    "data_col_concept.review.date_day_overflow",
    "data_col_concept",
    {
        "col_name": "test_date",
        "x": chr_vec(["2020-05-45", "2020-05-46", "2020-05-47", "2020-05-19"]),
    },
)
add(
    "data_col_concept.review.timestamp_minute_overflow",
    "data_col_concept",
    {
        "col_name": "timestamp",
        "x": chr_vec(["2020-05-19 10:65", "2020-05-19 11:75", "2020-05-19 12:95"]),
    },
)
add(
    "data_col_facets.review.compact_datetime",
    "data_col_facets",
    {
        "col_name": "session_time",
        "values": chr_vec(["2022_Sep_08_523", "2022_Sep_09_845", "2022_Oct_01_959"]),
    },
)

# -- an hms / difftime column is not numeric in R: facets come from its text --------------
for cid, name, r_x, py_x, cls in [
    ("hms", "start_time", "hms::hms(c(3600, 3723, 7200, 10, NA, 59, 90000, 61.5))",
     "[3600, 3723, 7200, 10, None, 59, 90000, 61.5]", ["hms", "difftime"]),
    ("difftime", "rt", "as.difftime(c(350, 420.5, 515, 610, 380, 455), units = 'secs')",
     "[350, 420.5, 515, 610, 380, 455]", ["difftime"]),
]:  # fmt: skip
    add(
        f"data_col_facets.review.class_{cid}",
        "data_col_facets",
        {"col_name": name, "values": ex(r_x, f"pd.Series({py_x}, dtype='float64')")},
        py_args={"col_class": cls},
    )

# -- table() drops the string "NaN" from a character vector (exclude = c(NA, NaN)) ----------

add(
    "data_check_constant.review.nan_string",
    "data_check_constant",
    {"x": chr_vec(["a", "a", "NaN"])},
)
add(
    "data_check_constant.review.all_nan_strings",
    "data_check_constant",
    {"x": chr_vec(["NaN", "NaN"])},
)
add(
    "data_check_constant.review.nan_factor",
    "data_check_constant",
    {"x": ex('factor(c("NaN", "NaN", "a"))', "pd.Series(pd.Categorical(['NaN', 'NaN', 'a']))")},
)
add(
    "data_check_constant.review.na_string",
    "data_check_constant",
    {"x": chr_vec(["NA", "NA", "NA", "b"])},
)
add(
    "data_check_constant.review.nan_near",
    "data_check_constant",
    {"x": chr_vec(["x"] * 60 + ["NaN", "y"]), "threshold": ex("0.95", "0.95")},
)

# -- as.character(<POSIXct>): fractional seconds -----------------------------------------------

add(
    "data_check_constant.review.posixct_fraction",
    "data_check_constant",
    {
        "x": ex(
            '.POSIXct(rep(1577872809.25, 3), tz = "UTC")',
            "pd.Series(pd.to_datetime([1577872809.25] * 3, unit='s'))",
        )
    },
)
add(
    "data_check_constant.review.posixct_midnight",
    "data_check_constant",
    {
        "x": ex(
            '.POSIXct(c(1577836800, 1577836800, 1577923200.5), tz = "UTC")',
            "pd.Series(pd.to_datetime([1577836800, 1577836800, 1577923200.5], unit='s'))",
        ),
        "threshold": ex("0.5", "0.5"),
    },
)
add(
    "data_check_constant.review.posixct_levels",
    "data_check_constant",
    {
        "x": ex(
            '.POSIXct(c(1577923200.5, 1577836800, 1577872800.000001), tz = "UTC")',
            "pd.Series(pd.to_datetime([1577923200.5, 1577836800, 1577872800.000001], unit='s'))",
        ),
        "threshold": ex("0.3", "0.3"),
    },
)
add(
    ".parse_frac.review.posixct",
    ".parse_frac",
    {
        "v": ex(
            '.POSIXct(c(1577872800.25, 1577923200), tz = "UTC")',
            "pd.Series(pd.to_datetime([1577872800.25, 1577923200], unit='s'))",
        ),
        "fmts": DATETIME,
    },
)

# -- .scale_typo_of(): an integer's digits come from as.character(<integer>) ----------------

base = list(range(2, 11)) * 3
add(
    "data_check_scale_values.review.int_1e5",
    "data_check_scale_values",
    {
        "x": ex(r_int([*base, 100000]), f"pd.Series({[*base, 100000]!r}, dtype='Int64')"),
        "valid_range": {"$dbl": [2.0, 10.0]},
    },
)
add(
    "data_check_scale_values.review.dbl_1e5",
    "data_check_scale_values",
    {
        "x": ex(r_num([*base, 100000]), py_num([*base, 100000])),
        "valid_range": {"$dbl": [2.0, 10.0]},
    },
)
add(
    "data_check_scale_values.review.int_neg_1e5",
    "data_check_scale_values",
    {
        "x": ex(r_int([*base, -100000, 33]), f"[*{base!r}, -100000, 33]"),
        "valid_range": {"$dbl": [2.0, 10.0]},
    },
)
lik = list(range(1, 6)) * 20
add(
    "data_check_scale_values.review.int_inferred_1e6",
    "data_check_scale_values",
    {"x": ex(r_int([*lik, 1000000, 300000, 55]), f"[*{lik!r}, 1000000, 300000, 55]")},
)
add(
    "data_check_scale_values.review.dbl_inferred_1e6",
    "data_check_scale_values",
    {"x": ex(r_num([*lik, 1000000, 300000, 55]), py_num([*lik, 1000000, 300000, 55]))},
)

# -- error paths of the scalar-name helpers ---------------------------------------------------

add(
    "data_check_pii_name.review.vector",
    "data_check_pii_name",
    {"col_name": {"$chr": ["email", "phone"]}},
)
add(
    "data_check_pii_name.review.zero_length",
    "data_check_pii_name",
    {"col_name": ex("character(0)", "[]")},
)
add(
    "data_check_pii_geo.review.zero_length",
    "data_check_pii_geo",
    {"col_name": ex("character(0)", "[]"), "x": {"$dbl": [1.0, 2.0, 3.0]}},
)
add(
    "data_check_pii_geo.review.vector",
    "data_check_pii_geo",
    {"col_name": {"$chr": ["lat", "x"]}, "x": {"$dbl": [52.1, 4.3, 51.9]}},
)
add(".pii_split_name.review.zero_length", ".pii_split_name", {"x": ex("character(0)", "[]")})
add(
    ".concept_is_rt.review.vector_name",
    ".concept_is_rt",
    {"col_name": {"$chr": ["rt", "x"]}, "x": {"$dbl": [1.0, 2.0, 3.0]}},
)
add(
    ".concept_is_rt.review.zero_length_name",
    ".concept_is_rt",
    {"col_name": ex("character(0)", "[]"), "x": {"$dbl": [1.0, 2.0, 3.0]}},
)
add(
    ".concept_is_rt.review.na_name",
    ".concept_is_rt",
    {"col_name": {"$NA": True}, "x": {"$dbl": [1.0]}},
)
add(
    ".concept_is_accuracy.review.vector_name",
    ".concept_is_accuracy",
    {"col_name": {"$chr": ["acc", "x"]}, "x": {"$dbl": [0.0, 1.0, 1.0]}},
)
add(
    ".concept_is_condition.review.vector_name",
    ".concept_is_condition",
    {"col_name": {"$chr": ["cond", "x", "Group"]}, "x": {"$dbl": [1.0]}},
)
add(
    ".concept_is_condition.review.zero_length_name",
    ".concept_is_condition",
    {"col_name": ex("character(0)", "[]"), "x": {"$dbl": [1.0]}},
)
add(
    ".concept_is_timestamp.review.vector_name",
    ".concept_is_timestamp",
    {"col_name": {"$chr": ["time", "x"]}, "x": {"$chr": ["2020-01-01 10:00:00"]}},
)
add(
    ".concept_is_date.review.zero_length_name",
    ".concept_is_date",
    {"col_name": ex("character(0)", "[]"), "x": {"$chr": ["2020-01-01"]}},
)

# -- header rows: stripped rows as given; object columns typed from all their values --------

add(
    ".detect_header_row.review.numeric_stripped",
    ".detect_header_row",
    {
        "rows": ex(
            'list(c(1, NA, NA, NA), c("id", "a", "b", "c"), c(1, 2, 3, 4), c(5, 6, 7, 8))',
            "[[1.0, None, None, None], ['id', 'a', 'b', 'c'], [1.0, 2.0, 3.0, 4.0],"
            " [5.0, 6.0, 7.0, 8.0]]",
        )
    },
    py=PH + "detect_header_row",
)
na_head = {
    "...1": ["Participant", "1", "2", "3", "4", "5"],
    "...2": ["Score", "3", "4", "5", "6", "7"],
    "...3": [None, None, None, None, None, "z"],
}
add(
    "data_promote_header_row.review.na_leading_text_column",
    "data_promote_header_row",
    {"df": {"$df": na_head}},
)
add(
    "data_strip_qualtrics_header.review.na_leading_text_column",
    "data_strip_qualtrics_header",
    {
        "df": {
            "$df": {
                "StartDate": ["Start Date", "1", "2"],
                "EndDate": ["End Date", "3", "4"],
                "Q1": [None, None, "x"],
            }
        }
    },
)

two = {"a": ["x", "1", "2"], "b": ["y", "3", "4"]}
add(
    "data_strip_qualtrics_header.review.negative_max_strip",
    "data_strip_qualtrics_header",
    {"df": {"$df": two}, "max_strip": ex("-1L", "-1")},
)
add(
    "data_strip_qualtrics_header.review.zero_max_strip",
    "data_strip_qualtrics_header",
    {"df": {"$df": two}, "max_strip": ex("0L", "0")},
)
add(
    "data_promote_header_row.review.negative_max_scan",
    "data_promote_header_row",
    {"df": {"$df": two}, "max_scan": ex("-1L", "-1")},
)
add(
    "data_promote_header_row.review.negative_max_scan_raw",
    "data_promote_header_row",
    {
        "df": {"$df": two},
        "raw_rows": ex(
            'list(c("x", "y"), c("1", "3"), c("2", "4"))', "[['x', 'y'], ['1', '3'], ['2', '4']]"
        ),
        "max_scan": ex("-1L", "-1"),
    },
)

# -- read.csv(fileEncoding = "UTF-8-BOM") header sniff ------------------------------------------

for f in ("jspsych_bom_blank.csv", "behaverse_latin1.csv", "jspsych_latin1_body.csv"):
    add(
        f".bh_is_trial_level_file.review.{f}",
        ".bh_is_trial_level_file",
        {"path": {"$file": f"{DATA}/{f}"}},
    )
add(
    ".bh_is_trial_level_file.review.path_vector",
    ".bh_is_trial_level_file",
    {
        "path": ex(
            f'file.path(root, "{DATA}/jspsych.csv")',
            f"[str(__import__('parity.cases', fromlist=['ROOT']).ROOT / '{DATA}/jspsych.csv')]",
        )
    },
)

# -- counts instead of elements: duplicates, first-appearance order, weighted medians ----------

add(
    "data_check_constant.review.tie_numeric",
    "data_check_constant",
    {"x": {"$dbl": [2.0, 1.0, 1.0, 2.0]}, "threshold": ex("0.5", "0.5")},
)
add(
    "data_check_constant.review.tie_character",
    "data_check_constant",
    {"x": {"$chr": ["b", "B", "a", "A"]}, "threshold": ex("0.2", "0.2")},
)
add(
    "data_check_constant.review.tie_logical",
    "data_check_constant",
    {"x": {"$lgl": [True, False]}, "threshold": ex("0.5", "0.5")},
)
add(
    "data_check_constant.review.double_labels_merge",
    "data_check_constant",
    {
        "x": ex("c(0.1 + 0.2, 0.3, 0.3, 1 + 1e-15, 1)", "[0.1 + 0.2, 0.3, 0.3, 1 + 1e-15, 1.0]"),
        "threshold": ex("0.5", "0.5"),
    },
)
add(
    "data_check_numeric_in_text.review.duplicates_order",
    "data_check_numeric_in_text",
    {
        "x": {"$chr": ["1", "2", "n/a", "3", " 4 ", "?", "n/a", "5", "6,5", "7", "n/a"]},
        "threshold": ex("0.6", "0.6"),
    },
)
add(
    "data_check_pii_values.review.duplicates",
    "data_check_pii_values",
    {
        "x": {
            "$chr": [
                "a@b.com",
                "a@b.com",
                " a@b.com ",
                "x",
                "y",
                "4111 1111 1111 1111",
                "4111111111111111",
                "10.0.0.1",
            ]
        }
    },
)
add(
    "data_check_pii_freetext.review.duplicates_median",
    "data_check_pii_freetext",
    {
        "x": {
            "$chr": [
                "I really enjoyed the study and would take part again next time.",
                "The task was confusing at first but became clear to me later on.",
                "short",
                "short",
                "My name is Jane and I live near the central station here.",
                "It was fine, a bit long maybe but otherwise everything worked well.",
                "Another long answer that clearly reads like written prose here.",
            ]
        },
        "min_unique_frac": ex("0.5", "0.5"),
    },
)
add(
    "data_check_whitespace.review.duplicates_order",
    "data_check_whitespace",
    {"x": {"$chr": ["b ", "a", " c", "b ", " c", "\td", None, "  "]}},
)
add(
    "data_check_case_issues.review.duplicates_order",
    "data_check_case_issues",
    {"x": {"$chr": ["male", "Female", "MALE", "male", "female", "Male", None, " "]}},
)
add(
    "data_check_colname.review.multibyte_60_chars",
    "data_check_colname",
    {"col_name": "\u65e5\u672c" * 30},
)
add(
    "data_check_colname.review.multibyte_66_chars",
    "data_check_colname",
    {"col_name": "\u65e5\u672c" * 33},
)


# -- batteries ----------------------------------------------------------------------------------

rng = random.Random(20260924)
FMTS = ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%d-%m-%Y %H:%M:%S", "%Y-%m-%d_%Hh%M",
        "%Y_%b_%d_%H%M", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d-%m-%Y",
        "%m/%d/%y", "%d/%m/%y", "%d %b %Y", "%B %d, %Y"]  # fmt: skip
MONTHS = ["Jan", "jan", "JAN", "January", "february", "Feb", "Sep", "Sept", "September", "May",
          "Mayo", "Dec", "december", "Jun", "June", "Oct", "Mar", "MARCH"]  # fmt: skip
LIMITS = {"Y": (1900, 2100), "y": (0, 99), "m": (1, 12), "d": (1, 31), "H": (0, 24), "M": (0, 59),
          "S": (0, 61)}  # fmt: skip


def date_field(d: str) -> str:
    r = rng.random()
    if r < 0.15:
        return str(rng.randint(0, 9))
    if r < 0.3:
        return "".join(rng.choice("0123456789") for _ in range(rng.randint(1, 5)))
    if r < 0.35:
        return " " + str(rng.randint(0, 30))
    v = rng.randint(*LIMITS[d])
    width = 4 if d == "Y" else 2
    return f"{v:0{width}d}" if rng.random() < 0.6 else str(v)


def date_string() -> str:
    f = rng.choice(FMTS)
    out = ""
    i = 0
    while i < len(f):
        if f[i] == "%":
            d = f[i + 1]
            out += rng.choice(MONTHS) if d in "bB" else date_field(d)
            i += 2
            continue
        out += rng.choice(["", "  ", "-", "/"]) if rng.random() < 0.05 else f[i]
        i += 1
    if rng.random() < 0.15:
        out += rng.choice(["Z", ".123", "5", " extra", ":00", "0"])
    return out


strings = sorted({date_string() for _ in range(260)})
add(
    ".parse_frac.review.battery",
    "identity",
    {
        "x": ex(
            "unlist(lapply("
            + r_chr(strings)
            + ", function(s) vapply(c(metacheck:::.DATETIME_FMTS, "
            "metacheck:::.DATE_FMTS), function(f) metacheck:::.parse_frac(s, f), 0, USE.NAMES = FALSE)))",
            f"[{C}._parse_frac([s], [f]) for s in {strings!r} for f in "
            f"[*{C}._DATETIME_FMTS, *{C}._DATE_FMTS]]",
        )
    },
    py=PH + "identity",
)

# value classifiers over seeded vectors (lists of R / Python vector literals)
vec_r: list[str] = []
vec_py: list[str] = []
for _ in range(45):
    lo = rng.choice([0, 1])
    hi = lo + rng.choice([4, 6, 9, 10])
    vals: list[Any] = [rng.randint(lo, hi) for _ in range(rng.choice([12, 20, 40]))]
    for _ in range(rng.randint(1, 3)):
        vals[rng.randrange(len(vals))] = rng.choice(
            [
                33,
                55,
                11,
                44,
                -99,
                999,
                99,
                hi + 1,
                lo - 1,
                -3,
                100000,
                12345,
                22,
                105,
                77,
                8,
                200000,
            ]
        )
    if rng.random() < 0.1:
        vals[0] = None
    if rng.random() < 0.5:
        vec_r.append(r_int(vals))
        vec_py.append(f"pd.Series({vals!r}, dtype='Int64')")
    else:
        vec_r.append(r_num(vals))
        vec_py.append(py_num(vals))
add(
    "data_check_scale_values.review.battery",
    "identity",
    {
        "x": ex(
            "lapply(list(" + ", ".join(vec_r) + "), data_check_scale_values)",
            f"[{C}.data_check_scale_values(v) for v in [" + ", ".join(vec_py) + "]]",
        )
    },
    py=PH + "identity",
)

vec_r, vec_py = [], []
for _ in range(45):
    v0 = rng.choice(["a", "B", "1", "NaN", "NA", "x y", "2.5", "b", "A"])
    n = rng.choice([3, 8, 20, 60])
    p = rng.choice([0.9, 0.99, 1.0, 0.5])
    cvals: list[str | None] = [
        v0 if rng.random() < p else rng.choice(["a", "b", "A", "NaN", "z"]) for _ in range(n)
    ]
    if rng.random() < 0.2:
        cvals[-1] = None
    if all(v in (None, "NaN") for v in cvals):  # an all-"NaN" table errors: see nan cases
        cvals[0] = "a"
    vec_r.append(r_chr(cvals))
    vec_py.append(f"pd.Series({cvals!r}, dtype='string')")
add(
    "data_check_constant.review.battery",
    "identity",
    {
        "x": ex(
            "lapply(list(" + ", ".join(vec_r) + "), data_check_constant)",
            f"[{C}.data_check_constant(v) for v in [" + ", ".join(vec_py) + "]]",
        )
    },
    py=PH + "identity",
)

names_r, names_py, vals_r, vals_py = [], [], [], []
DATES = ["2020-05-19", "19/05/2020", "5/19/20", "2020-05-19 16:20:33", "2020-05-45", "19 May 2020",
         "May 19, 2020", "2022_Feb_08_1523", "2022_Feb_08_523", "2020-05-19T16:20:33Z",
         "2020-05-19_16h20.01.792", "31/12/1999", "12/31/1999", "2020/05/19 16:20:33", "2020-02-30",
         "2020-05-19 10:65", "2020-5-9", "1999-12-31 23:59:59", "01-02-2003 04:05:06"]  # fmt: skip
NAMES = ["date", "StartDate", "time", "dob", "day", "timestamp", "Onset", "RecordedDate", "EndDate",
         "birth date", "test_date", "DateTime", "session time", "dag", "Datum"]  # fmt: skip
for _ in range(60):
    base_d = rng.sample(DATES, k=rng.randint(1, 4))
    dvals = [
        rng.choice(base_d) if rng.random() < 0.8 else rng.choice(["x", "12", "", None])
        for _ in range(rng.choice([3, 6, 12]))
    ]
    names_py.append(rng.choice(NAMES))
    vals_r.append(r_chr(dvals))
    vals_py.append(repr(dvals))
add(
    "data_col_concept.review.date_battery",
    "identity",
    {
        "x": ex(
            "mapply(data_col_concept, "
            + r_chr(names_py)
            + ", list("
            + ", ".join(vals_r)
            + "), USE.NAMES = FALSE)",
            f"[{C}.data_col_concept(n, v) for n, v in zip({names_py!r}, ["
            + ", ".join(vals_py)
            + "])]",
        )
    },
    py=PH + "identity",
)


def main() -> None:
    doc = {"area": "datacheck_checks_review", "cases": CASES}
    header = (
        "# Review cases for the datacheck_checks port (divergences found and fixed, error\n"
        "# paths, seeded batteries). Generated by tests/datacheck_checks/gen_review_cases.py\n"
        "# -- edit that script, not this file.\n"
    )
    body = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, default_style='"', width=100)
    OUT.write_text(header + body, encoding="utf-8")
    print(f"{len(CASES)} cases -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
