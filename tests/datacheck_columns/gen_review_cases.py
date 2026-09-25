"""Write parity/cases/datacheck_columns_review.yaml (run from the repository root).

    python tests/datacheck_columns/gen_review_cases.py

Adversarial-review cases for R/data_check_helpers.R part 2: the branches the
first round of cases (gen_parity_cases.py) did not reach -- R's long-double
statistics and overflow, R-vector coercions (factor, logical, Date, -0),
jsonlite's strictness, R's ``as.character()`` / deparse of JSON lists in
``.qsf`` files, scalar-condition errors, absent codebook columns, and file
layouts (BOM, ragged markdown tables, duplicate headers, numeric names,
duplicate haven label texts). Every string is quoted in the YAML.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "parity" / "cases" / "datacheck_columns_review.yaml"
FIX = "tests/datacheck_columns/fixtures"
REV = f"{FIX}/review"
PY = "pytacheck.datacheck.columns"

cases: list[dict[str, Any]] = []


def case(cid: str, fn: str, args: dict[str, Any], **extra: Any) -> None:
    """Add a case calling R ``fn`` (``.x`` -> ``metacheck:::.x``) and ``columns.<py name>``."""
    internal = fn.startswith(".")
    r = f"metacheck:::{fn}" if internal else fn
    py = f"{PY}.{'_' + fn[1:] if internal else fn}"
    spec: dict[str, Any] = {"id": cid, "r": r, "py": py, "args": args}
    spec.update(extra)
    cases.append(spec)


def chr_(*v: Any) -> dict[str, Any]:
    return {"$chr": list(v)}


def _num(v: Any) -> Any:
    """A double for ``$dbl``: infinities and NaN travel as R's spellings."""
    if isinstance(v, float) and v != v:
        return "NaN"
    if isinstance(v, float) and v in (float("inf"), float("-inf")):
        return "Inf" if v > 0 else "-Inf"
    return v


def dbl(*v: Any) -> dict[str, Any]:
    return {"$dbl": [_num(x) for x in v]}


def int_(*v: Any) -> dict[str, Any]:
    return {"$int": list(v)}


def lgl(*v: Any) -> dict[str, Any]:
    return {"$lgl": list(v)}


def expr(r: str, py: str) -> dict[str, Any]:
    return {"$expr": {"r": r, "py": py}}


def rev(name: str) -> dict[str, str]:
    return {"$file": f"{REV}/{name}"}


NULL = {"$null": True}
NA = {"$NA": True}

# ---------------------------------------------------------------- data_col_type
for cid, name, values in [
    ("neg_zero", "score", dbl(0.0, -0.0, 1.0)),
    ("neg_zero_many", "score", dbl(0.0, -0.0, 1.5, 2.5, 3.5)),
    (
        "factor",
        "cond",
        expr(
            "factor(c('lo', 'mid', 'hi', 'lo', NA))",
            "pd.Series(['lo', 'mid', 'hi', 'lo', None], dtype='category')",
        ),
    ),
    (
        "factor_numbers",
        "level",
        expr(
            "factor(c('1,5', '2,5', '3,5', '4,5'))",
            "pd.Series(['1,5', '2,5', '3,5', '4,5'], dtype='category')",
        ),
    ),
    (
        "dates_class",
        "when",
        expr(
            "as.Date(c('2020-01-01', '2020-02-01', NA, '2021-03-04'))",
            "pd.Series(pd.to_datetime(['2020-01-01', '2020-02-01', None, '2021-03-04']))",
        ),
    ),
    ("bad_dates", "when", chr_("2020-01-40", "2020-01-41", "2020-02-30", "2021-02-29")),
    ("date_trailing", "when", chr_("2020-01-01 junk", "2020/1/4x", "2020- 1- 1", "zzz")),
    ("hex_ws", "amount", chr_("0x1A", " 1,5 ", "2,5", "3e2", "Inf", "-4,25")),
    (
        "nan_strings",
        "amount",
        chr_("NaN", "1,5", "2,5", "3,5", "4,5", "5,5", "6,5", "7,5", "8,5", "9,5"),
    ),
    (
        "outliers",
        "amount",
        chr_("1,5", "2,5", "3,5", "4,5", "5,5", "6,5", "7,5", "8,5", "n/a", "x"),
    ),
    ("mostly_text", "amount", chr_("1,5", "2,5", "a", "b", "c")),
    ("nchar_40", "note", chr_("a" * 40, "b" * 40, "c" * 40)),
    ("nchar_41", "note", chr_("a" * 41, "b" * 41, "c" * 41)),
    ("inf_values", "rt", dbl(float("inf"), 1, 2, 3)),
    ("int_21", "n", int_(*range(1, 22))),
    ("int_20", "n", int_(*range(1, 21), 3)),
    ("big_ints", "n", dbl(1e5, 2e5, 3e5)),
    ("numeric_na_name", NA, dbl(1.5, 2.5, 3.5)),
    ("lgl_na_only", "flag", lgl(None, None)),
    ("unicode_id", "Participant_ID", chr_("é1", "é2", "é3")),
    ("sub_digit", "sub_1", dbl(1.5, 2.5, 3.5)),
    ("subject_n", "subject12", dbl(1.5, 2.5, 3.5)),
    ("not_id_suffix", "valid_ids", dbl(1.5, 2.5, 3.5)),
]:
    case(f"data_col_type.{cid}", "data_col_type", {"col_name": name, "values": values})

# --------------------------------------------------------------- data_col_stats
for cid, xs in [
    ("overflow_sq", [1e200, -1e200, 3.0, 5.0]),
    ("overflow_sum_sq", [1e154, -1e154, 1e154]),
    ("overflow_mean", [1e308, 1e308, -1e308]),
    ("overflow_cube", [1e103, 2.0, 3.0, 4.0]),
    ("inf", [float("inf"), 1.0, 2.0]),
    ("inf_both", [float("inf"), float("-inf"), 1.0]),
    ("tiny", [1e-300, 2e-300, 3e-300, 5e-300]),
    ("thirds", [0.1, 0.2, 0.3, 1 / 3, 2 / 3, 0.1, 0.7]),
    ("n1", [4.2]),
    ("n2", [4.2, 5.3]),
    ("n3", [4.2, 5.3, 1.1]),
    ("n4", [4.2, 5.3, 1.1, 9.9]),
    ("constant", [2.5, 2.5, 2.5, 2.5, 2.5]),
    ("big_offset", [1e8 + 0.1, 1e8 + 0.2, 1e8 + 0.3, 1e8 + 0.7]),
]:
    case(f"data_col_stats.{cid}", "data_col_stats", {"x_for_stats": dbl(*xs), "x_raw": dbl(*xs)})
case(
    "data_col_stats.chr_stats",
    "data_col_stats",
    {
        "x_for_stats": chr_("1.5", "x", None, " 2 ", "0x10", "1e3"),
        "x_raw": chr_("1.5", "x", None, " 2 ", "0x10", "1e3"),
    },
)
case(
    "data_col_stats.factor_stats",
    "data_col_stats",
    {
        "x_for_stats": expr(
            "factor(c('b', 'a', 'c', NA, 'a'))",
            "pd.Series(['b', 'a', 'c', None, 'a'], dtype='category')",
        ),
        "x_raw": expr(
            "factor(c('b', 'a', 'c', NA, 'a'))",
            "pd.Series(['b', 'a', 'c', None, 'a'], dtype='category')",
        ),
    },
)
case(
    "data_col_stats.lgl_stats",
    "data_col_stats",
    {"x_for_stats": lgl(True, False, True, None), "x_raw": lgl(True, False, True, None)},
)
case("data_col_stats.null_raw", "data_col_stats", {"x_for_stats": NULL, "x_raw": NULL})
case(
    "data_col_stats.null_stats_raw_na",
    "data_col_stats",
    {"x_for_stats": NULL, "x_raw": chr_("a", None, "a", "b")},
)
case(
    "data_col_stats.nan_raw",
    "data_col_stats",
    {
        "x_for_stats": dbl(1.0, float("nan"), 2.0, None, 3.0),
        "x_raw": dbl(1.0, float("nan"), 2.0, None, 3.0),
    },
)
case(
    "data_col_stats.neg_zero_unique",
    "data_col_stats",
    {"x_for_stats": dbl(0.0, -0.0, 1.0), "x_raw": dbl(0.0, -0.0, 1.0)},
)
case(
    "data_col_stats.nested_raw",
    "data_col_stats",
    {
        "x_for_stats": NULL,
        "x_raw": expr(
            "data.frame(a = 1:3, b = c('x', 'y', 'z'))",
            "pd.DataFrame({'a': [1, 2, 3], 'b': ['x', 'y', 'z']})",
        ),
    },
)
case(
    "data_col_stats.list_raw",
    "data_col_stats",
    {"x_for_stats": dbl(1.0, 2.0), "x_raw": expr("list(1, list(2, 3))", "[1, [2, 3]]")},
)

# ------------------------------------------------------ normalisation helpers
case(
    "normalize_label.edge",
    "normalize_label",
    {
        "x": chr_(
            "Participants' responses",
            "Participant’s Response",
            "",
            "   ",
            None,
            "NA",
            "Ábaco relational!",
            "running runs ran",
            "2nd-order effects (log)",
        )
    },
)
case(
    "normalize_varname.edge",
    "normalize_varname",
    {"x": chr_("..a..", "__x__y__", "A\tB", " Mixed_Case.Name. ", None, "", "É_x")},
)
case(
    ".normalize_header.edge",
    ".normalize_header",
    {"x": chr_("Variable.Name", " LABEL(S) ", "Ünïcode-Name", None, "", "var__name")},
)
case(
    ".infer_group.edge",
    ".infer_group",
    {
        "context_str": chr_(
            "Pilot 2B",
            "pilot12x",
            "Study 1a and Experiment 3",
            "EXPERIMENT 4",
            "",
            None,
            "studying 5",
            "Study IV",
        )
    },
)
case(".infer_group.numbers", ".infer_group", {"context_str": dbl(1, 2)})

# ------------------------------------------------- value labels / JSON (jsonlite)
for cid, s in [
    ("nan_literal", '{"1": NaN, "2": "b"}'),
    ("infinity_literal", "[Infinity, 1]"),
    ("mixed_types", '{"1": "a", "2": 3, "3": true, "4": null}'),
    ("dup_keys", '{"1": "a", "1": "b"}'),
    ("bool_num", '{"1": true, "2": 2.5}'),
    ("big_int", '{"1": 3000000000, "2": 1}'),
    ("scalar_string", '"just text"'),
    ("trailing_comma", '{"1": "a",}'),
]:
    case(f".decode_value_labels.{cid}", ".decode_value_labels", {"s": s})
for cid, codes, labels in [
    ("recycle_codes", dbl(1, 2), chr_("a", "b", "c", "d")),
    ("recycle_labels", dbl(1, 2, 3, 4, 5), chr_("a", "b")),
    ("na_code", dbl(1, None, 3), chr_("a", "b", "c")),
    ("blank_names", chr_("", "x", ""), chr_("a", "b", "c")),
    ("dup_names", chr_("1", "1", "1.1", "1"), chr_("a", "b", "c", "d")),
    ("sci_codes", dbl(1e5, 123456, 0.1 + 0.2, -99), chr_("a", "b", "c", "d")),
    ("ws_labels", dbl(1, 2, 3), chr_(" ", "\t", " ok ")),
    ("escapes", chr_("q", "b"), chr_('say "hi"\\now', "tab\there\u0001é")),
]:
    case(f".encode_value_labels.{cid}", ".encode_value_labels", {"codes": codes, "labels": labels})
case(
    ".encode_missing_values.reasons_recycled",
    ".encode_missing_values",
    {"codes": dbl(-99, None, -98), "reasons": chr_("refused", "dk", "na")},
)
case(".encode_missing_values.numbers", ".encode_missing_values", {"codes": dbl(-99, 1e5, 0.5)})
for cid, s in [
    ("from_nan", '{"1": NaN, "2": "Refused"}'),
    ("array", '["Refused", "N/A"]'),
    ("numbers", '{"-9": "No answer", "1": 5}'),
    ("dup_keys", '{"1": "Refused", "1": "N/A", "2": "x"}'),
]:
    case(f".missing_from_value_labels.{cid}", ".missing_from_value_labels", {"vl_json": s})

for cid, s in [
    ("anchor_decimals", "1.5 (low) to 7.5 (high)"),
    ("anchor_one", "1 (low) and nothing else = 2"),
    ("comma_label_first", "Counselors, Social workers=21, Teachers=22"),
    ("newline_pairs", "1 = a\n2 = b\r\n3 = c"),
    ("code_label_dash", "1-Male, 2-Female"),
    ("code_label_unicode", "1 – Low; 2 — High"),
    ("text_both", "M = Male; F = Female"),
    ("bare_numbers", "1, 2, 3"),
    ("pipe_mixed", "1=Yes | No=2 | 3=Maybe"),
    ("neg_codes", "-1 = disagree; 0 = neutral; 1 = agree"),
]:
    case(f".vl_split_pairs.{cid}", ".vl_split_pairs", {"s": s})
    case(f".parse_value_label_text.{cid}", ".parse_value_label_text", {"s": s})
case(
    ".parse_value_label_text.observed_tie",
    ".parse_value_label_text",
    {"s": "M = Male; F = Female", "observed": chr_("M", "Male")},
)
case(
    ".parse_value_label_text.observed_na",
    ".parse_value_label_text",
    {"s": "M = Male; F = Female", "observed": chr_(None, " F ", "", "M")},
)
case(".parse_value_label_text.numeric_s", ".parse_value_label_text", {"s": 5})

# ------------------------------------------------------------ haven labels
case(
    ".haven_value_labels.dup_texts",
    ".haven_value_labels",
    {
        "col": expr(
            "structure(c(1, 2), labels = c(Missing = -99, Yes = 1, Missing = -98), na_values = c(-98, -97), "
            "na_range = c(-Inf, -100))",
            "(lambda s: (s.attrs.update({'labels': [('Missing', -99.0), ('Yes', 1.0), ('Missing', -98.0)], "
            "'na_values': [-98.0, -97.0], 'na_range': [float('-inf'), -100.0]}), s)[1])(pd.Series([1.0, 2.0]))",
        )
    },
)
case(
    ".haven_value_labels.range_only",
    ".haven_value_labels",
    {
        "col": expr(
            "structure(c(1, 2), na_range = c(-9, -1))",
            "(lambda s: (s.attrs.update({'na_range': [-9.0, -1.0]}), s)[1])(pd.Series([1.0, 2.0]))",
        )
    },
)
case(
    ".looks_like_freetext_labels.na_long",
    ".looks_like_freetext_labels",
    {"labs": chr_("a" * 50, "b" * 50, "c", "d", None)},
)

# ------------------------------------------------------- codebook detection
for cid, names in [
    ("label_only_var", chr_("Variable Label", "Variable Name")),
    ("plural", chr_("Variable Names", "Labels")),
    ("question_as_label", chr_("item", "Question")),
    ("full_name", chr_("Variable", "Full name", "Scale")),
    ("na_names", chr_(None, "name", "description")),
    ("dup", chr_("name", "name", "label", "label")),
]:
    case(f".find_codebook_cols.{cid}", ".find_codebook_cols", {"col_names": names})
case(
    ".find_value_label_col.scoring",
    ".find_value_label_col",
    {"col_names": chr_("Scoring", "Values")},
)
case(
    ".find_coding_instruction_col.scoring",
    ".find_coding_instruction_col",
    {"col_names": chr_("Values", "Scoring")},
)
case(".looks_like_varnames.dups", ".looks_like_varnames", {"x": chr_("a1", "a1", "a2", "a3", "a4")})
case(".looks_like_varnames.numbers", ".looks_like_varnames", {"x": dbl(1, 2, 3)})
case(".looks_like_wording.short", ".looks_like_wording", {"x": chr_("a b", "c d", "e f g h i")})

case(
    ".extract_codebook_positional.last_col_label",
    ".extract_codebook_positional",
    {
        "df": {
            "$df": {
                "a": ["x1", "x2", "x3", ""],
                "b": ["Some wording here", "More wording here", "Even more wording", "x"],
            }
        },
        "src": "pos.csv",
    },
)
case(
    ".extract_codebook_positional.anchors_na",
    ".extract_codebook_positional",
    {
        "df": {
            "$df": {
                "a": ["x1", "x2", "x3"],
                "b": ["Some wording here", "More wording here", "Even more wording"],
                "c": ["1 = low", None, "1 = low"],
                "d": ["5) high", "5) high", None],
                "e": [1, 2, 3],
            }
        },
        "src": "pos.csv",
    },
)
case(
    ".extract_structured_codebook.numeric_cols",
    ".extract_structured_codebook",
    {
        "df": {
            "$df": {
                "name": [1, 2, 2, 3],
                "label": [10.5, None, None, 30],
                "values": ["1 = a; 2 = b", None, None, "x"],
            }
        },
        "src": "num.csv",
    },
)
case(
    ".extract_structured_codebook.na_var",
    ".extract_structured_codebook",
    {
        "df": {
            "$df": {
                "variable": ["a", None, " ", "a"],
                "label": ["A", "B", "C", ""],
                "missing values": ["-9", "", "-1 = no", "N/A"],
                "prompt": [" Q a ", "", None, "x"],
            }
        },
        "src": "na.csv",
    },
)

# ------------------------------------------------------------- OSD helpers
RNA = expr("NA", "float('nan')")  # an explicit NA (Python None is R's NULL here)
case(".osd_slug.na_prefix", ".osd_slug", {"name": NULL, "prefix": RNA})
case(".osd_slug.na_name_prefix", ".osd_slug", {"name": NA, "prefix": "Resp Block"})
case(".osd_slug.boundary", ".osd_slug", {"name": "a" * 30 + " " + "b" * 29, "prefix": NULL})
case(".osd_slug.boundary_over", ".osd_slug", {"name": "a" * 30 + " " + "b" * 30, "prefix": NULL})
case(".osd_slug.single_word", ".osd_slug", {"name": "x" * 70, "prefix": NULL})
case(".osd_slug.max_chars", ".osd_slug", {"name": "alpha beta gamma", "max_chars": 8})
case(".osd_safe_code.na", ".osd_safe_code", {"x": RNA})
case(".osd_safe_code.null", ".osd_safe_code", {"x": NULL})
case(
    ".osd_safe_code.long",
    ".osd_safe_code",
    {"x": "The quick brown fox jumps over the lazy dog again"},
)
case(
    ".osd_code_and_provenance.na_scale_na_prefix",
    ".osd_code_and_provenance",
    {
        "scale": RNA,
        "prefix": RNA,
        "scale_source": NULL,
        "dict": {"$df": {"name": ["PANAS"], "code": ["PANAS"]}},
    },
)
case(
    ".osd_code_and_provenance.case_insensitive",
    ".osd_code_and_provenance",
    {
        "scale": "panas",
        "prefix": "x",
        "scale_source": "self_generated",
        "dict": {"$df": {"name": ["PANAS"], "code": [""]}},
    },
)
case(
    ".item_text_key.edge",
    ".item_text_key",
    {"x": chr_("I <b>don't</b> like crowds.", "Isn't it?", "CAN'T", None, "")},
)

# ---------------------------------------------------------------- .qsf helpers


def jl(s: str) -> dict[str, Any]:
    return expr(
        f"jsonlite::fromJSON({s!r}, simplifyVector = FALSE)",
        f"__import__('pytacheck.datacheck._columns_labels', fromlist=['x'])._json_loads({s!r})",
    )


for cid, s in [
    ("null_list", "[null]"),
    ("nested", '[["a", 1]]'),
    ("object_first", '{"t": "Object <i>text</i>", "u": "x"}'),
    ("number", "12.5"),
    ("entity_chain", '"a &amp;lt; b&nbsp;&amp;amp;"'),
    (
        "deparse_names",
        '[{"": 1, "if": 2, "a.b": 3, "_x": 4, ".2a": 5, "b c": 6, "ok": true, "n": null}]',
    ),
    ("deparse_escapes", '[["q\\"\\\\\\n\\t\\u0001é"]]'),
    ("empty_object", "[{}]"),
    ("big_int", "[[3000000000]]"),
]:
    case(f".qsf_strip_html.{cid}", ".qsf_strip_html", {"x": jl(s)})
for cid, s in [
    ("displaylogic", '{"DisplayLogic": {"0": {"x": 1, "y": "z"}}}'),
    ("two_partials", '{"DisplayLogic": "a", "DisplayX": "b"}'),
    ("lgl_int", '{"Display": [true, 2]}'),
    ("lgl_dbl", '{"Display": [true, 2.5]}'),
    ("nested_nulls", '{"Display": [null, [null, "deep"]]}'),
    ("empty_list", '{"Display": []}'),
    ("lower", '{"display": "lower"}'),
    ("array_opt", '["x"]'),
]:
    case(f".qsf_option_display.{cid}", ".qsf_option_display", {"opt": jl(s)})
case(
    ".qsf_value_labels.array",
    ".qsf_value_labels",
    {"opts": jl('[{"Display": "a"}, {"Display": "b"}]')},
)
case(
    ".qsf_value_labels.null_opt",
    ".qsf_value_labels",
    {"opts": jl('{"1": null, "2": {"Display": "b"}, "": {"Display": "c"}}')},
)
case(".qsf_export_col.na_both", ".qsf_export_col", {"tag": NA, "choice_tag": NA, "code": "1"})
case(".qsf_export_col.numeric_tag", ".qsf_export_col", {"tag": 42, "choice_tag": 7, "code": "1"})
case(
    ".qsf_export_col.case_equal",
    ".qsf_export_col",
    {"tag": "Q1", "choice_tag": " q1 ", "code": "1"},
)

for name in ["edge.qsf", "tag_list.qsf", "tag_empty.qsf", "latin1.qsf"]:
    case(f"parse_qsf.{name}", "parse_qsf", {"path": rev(name)})

# ------------------------------------------------------------ parse_codebook
for name in [
    "nan_literal.json",
    "nan_literal.csv",
    "bom_json.csv",
    "bom_table.md",
    "multi_table.md",
    "missing_cols.csv",
    "numeric_vars.csv",
    "title_semicolon.csv",
    "values_shapes.json",
    "edge.qsf",
    "tag_list.qsf",
    "tag_empty.qsf",
    "dup_labels.sav",
    "tricky.xlsx",
    "numeric.ods",
    "handmade.ods",
    "latin1_table.md",
]:
    case(f"parse_codebook.{name}", "parse_codebook", {"path": rev(name)})
case(
    "parse_codebook.values_shapes.group",
    "parse_codebook",
    {"path": rev("values_shapes.json"), "group": "ex1"},
)
case(
    "parse_codebook.missing_cols.observed",
    "parse_codebook",
    {
        "path": rev("missing_cols.csv"),
        "observed": expr(
            "list(d = c('M', 'F', 'M'), a = 1:2)", "{'d': ['M', 'F', 'M'], 'a': [1, 2]}"
        ),
    },
)
case(
    "parse_codebook.tricky.lookahead0",
    "parse_codebook",
    {"path": rev("tricky.xlsx"), "header_lookahead": 1},
)
for name in ["run_order.docx", "tricky.docx", "orphan_no_table.docx", "escapes.rtf"]:
    ext = name.rsplit(".", 1)[1]
    case(f".extract_rich_text.{name}", ".extract_rich_text", {"path": rev(name), "ext": ext})
    case(f"parse_codebook.{name}", "parse_codebook", {"path": rev(name)})
# parse_codebook() of latin1.{rtf,json,qsf} falls back to the raw (invalid UTF-8)
# lines, which a golden cannot hold: tests/datacheck_columns/test_review.py
# checks those; here the extractors, whose results are valid.
case(
    ".extract_rich_text.latin1.rtf", ".extract_rich_text", {"path": rev("latin1.rtf"), "ext": "rtf"}
)
case(
    ".extract_json_codebook.latin1",
    ".extract_json_codebook",
    {"path": rev("latin1.json"), "src": "latin1.json"},
)
case(
    ".strip_rtf.invalid_utf8",
    ".strip_rtf",
    {"text": expr("'caf\\xe9 \\\\par'", "'caf\\udce9 \\\\par'")},
)
case(
    ".decode_value_labels.invalid_utf8",
    ".decode_value_labels",
    {"s": expr('\'{"1": "caf\\xe9"}\'', '\'{"1": "caf\\udce9"}\'')},
)
case(
    ".extract_markdown_codebook.multi",
    ".extract_markdown_codebook",
    {"path": rev("multi_table.md"), "src": "multi_table.md"},
)
case(
    ".extract_json_codebook.values_shapes",
    ".extract_json_codebook",
    {"path": rev("values_shapes.json"), "src": "v.json"},
)
case(
    ".extract_json_codebook.nan",
    ".extract_json_codebook",
    {"path": rev("nan_literal.json"), "src": "n.json"},
)
case(
    ".extract_haven_labels.sav",
    ".extract_haven_labels",
    {
        "df": expr(
            f"as.data.frame(haven::read_sav('{REV}/dup_labels.sav'))",
            f"__import__('pytacheck.datacheck._columns_codebook', fromlist=['x'])"
            f"._haven_frame('{REV}/dup_labels.sav', 'sav')",
        ),
        "src": "dup_labels.sav",
        "group": "g1",
    },
)

# ---------------------------------------------------------------- .scale_typo_of
for cid, v, lo, hi in [
    ("int_1e5", 100000, 2, 6),  # R integer: "100000" -> no candidate in 2..6
    ("dbl_1e5", 100000.0, 2, 6),  # double: "1e+05" -> 5
    ("int_neg", -33, -3, 3),
    ("int_big", 2147483647, 1, 7),
    ("dbl_1e15", 1e15, 1, 7),
    ("dbl_neg_zero_range", -0.0, 1, 7),
    ("dbl_half", 2.5, 3, 7),
]:
    case(f".scale_typo_of.{cid}", ".scale_typo_of", {"v": v, "lo": lo, "hi": hi})

# ---------------------------------------------------------- match_column_labels


def cols(**kw: Any) -> dict[str, Any]:
    return {"$df": kw}


def mcl(cid: str, columns: Any, cbk: Any, **extra: Any) -> None:
    case(
        f"match_column_labels.{cid}",
        "match_column_labels",
        {"columns_df": columns, "codebook_vars_df": cbk},
        **extra,
    )


base_cols = cols(
    paper_id=["p", "p", "p"], source_file=["d.csv"] * 3, column_name=["age", "sex", "x"]
)
mcl("no_label_col", base_cols, cols(codebook_variable=["age"], group=[None]))
mcl(
    "no_label_col_ambiguous",
    cols(paper_id=["p"], source_file=["d.csv"], column_name=["age"], group=["g3"]),
    cols(codebook_variable=["age", "age"], group=["g1", "g2"]),
)
mcl(
    "no_source_col",
    base_cols,
    cols(codebook_variable=["age", "age"], label=["Age", "AGE (years)"], group=[None, None]),
)
mcl(
    "numeric_group",
    cols(paper_id=["p", "p"], source_file=["d.csv"] * 2, column_name=["age", "age"], group=[1, 2]),
    cols(
        codebook_variable=["age", "age"],
        label=["Age one", "Age two"],
        codebook_source=["a", "b"],
        group=["1", "3"],
    ),
)
mcl(
    "na_group_scoped",
    cols(
        paper_id=["p", "p"],
        source_file=["d.csv"] * 2,
        column_name=["age", "sex"],
        group=[None, "g1"],
    ),
    cols(
        codebook_variable=["age", "age", "sex"],
        label=["Age", "Age", "Sex"],
        codebook_source=["a", "b", "c"],
        group=[None, "g1", "g2"],
    ),
)
mcl(
    "haven_all_na",
    cols(paper_id=["p"], source_file=["d.csv"], column_name=["age"]),
    cols(
        codebook_variable=["age", "age"],
        label=[None, "Age"],
        codebook_source=["a", "b"],
        group=[None, None],
        parse_method=["haven", "structured"],
    ),
)
mcl(
    "haven_longest",
    cols(paper_id=["p"], source_file=["d.csv"], column_name=["age"]),
    cols(
        codebook_variable=["Age", "age", "AGE"],
        label=["Age yrs", "Age in years", "age"],
        codebook_source=["a", "b", "a"],
        group=[None, None, None],
        parse_method=["haven", "haven", "structured"],
    ),
)
mcl(
    "merged_tie",
    cols(paper_id=["p"], source_file=["d.csv"], column_name=["resp"]),
    cols(
        codebook_variable=["resp", "resp"],
        label=["Responses", "Response!"],
        codebook_source=["a", "b"],
        group=[None, None],
    ),
)
mcl(
    "conflict_na_label",
    cols(paper_id=["p"], source_file=["d.csv"], column_name=["resp"]),
    cols(
        codebook_variable=["resp", "resp"],
        label=[None, "NA"],
        codebook_source=["a", "a"],
        group=[None, None],
    ),
)
mcl(
    "paper_numeric",
    cols(paper_id=[1, 2], source_file=["d.csv"] * 2, column_name=["age", "age"]),
    cols(
        codebook_variable=["age"],
        label=["Age"],
        codebook_source=["a"],
        group=[None],
        paper_id=["1"],
    ),
)
mcl(
    "ranges",
    cols(
        paper_id=["p"] * 6,
        source_file=["d.csv"] * 6,
        column_name=["V2", "Q11", "3", "item 2", "V10", "x05"],
    ),
    cols(
        codebook_variable=["V1 - 3", "Q10–12", "1-3", "item1-2", "V10-9", "x1-99999999999"],
        label=["Vs", "Qs", "Bare", "Items", "Backwards", "Huge"],
        codebook_source=["a"] * 6,
        group=[None] * 6,
    ),
)
mcl(
    "ranges_only_invalid",
    cols(paper_id=["p"], source_file=["d.csv"], column_name=["V10-9"]),
    cols(codebook_variable=["V10-9"], label=["Backwards"], codebook_source=["a"], group=[None]),
)
mcl(
    "qsf_fallback",
    cols(
        paper_id=["p"] * 6,
        source_file=["d.csv"] * 6,
        column_name=["Q1_1_1", "Q1_First Click", "Q1_2_Page.Submit", "Q12_3", "Q1", "Q1x"],
    ),
    cols(
        codebook_variable=["Q1_4", "Q12_1", "other"],
        label=["Item 4", "Item 1", "Other"],
        codebook_source=["s.qsf", "s.qsf", "cb.csv"],
        group=[None, None, None],
        value_labels=['{"1":"a"}', None, None],
        question=["Stem one", None, "q"],
        scale_group=["Q1", "Q12", "Q1"],
        parse_method=["qsf", "qsf", "structured"],
    ),
)
mcl(
    "qsf_no_parse_method",
    cols(paper_id=["p"], source_file=["d.csv"], column_name=["T_1"]),
    cols(
        codebook_variable=["T_9"],
        label=["Item"],
        codebook_source=["s.qsf"],
        group=[None],
        value_labels=[None],
        question=[None],
        scale_group=["T"],
    ),
)
mcl(
    "qsf_no_value_labels_col",
    cols(paper_id=["p"], source_file=["d.csv"], column_name=["T_1"]),
    cols(
        codebook_variable=["T_9"],
        label=["Item"],
        codebook_source=["s.qsf"],
        group=[None],
        question=["Stem"],
        scale_group=["T"],
    ),
)
mcl(
    "experiment_group_col",
    cols(paper_id=["p"], source_file=["d.csv"], column_name=["age"], experiment_group=["g1"]),
    cols(
        codebook_variable=["age", "age"],
        label=["Age A", "Age B"],
        codebook_source=["a", "b"],
        group=["g1", "g2"],
    ),
)


def main() -> None:
    header = (
        "# Adversarial-review parity cases for R/data_check_helpers.R part 2.\n"
        "# Generated by tests/datacheck_columns/gen_review_cases.py -- edit that script, not this file.\n"
    )
    body = yaml.safe_dump(
        {"area": "datacheck_columns_review", "cases": cases},
        sort_keys=False,
        allow_unicode=True,
        default_style='"',
        width=100,
    )
    OUT.write_text(header + body, encoding="utf-8")
    print(f"wrote {len(cases)} cases to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
