"""Write parity/cases/datacheck_columns.yaml (run from the repository root).

    python tests/datacheck_columns/gen_parity_cases.py

Every string is quoted in the YAML (``default_style='"'``) so R's YAML reader
never turns ``"1.0"``/``"yes"`` into numbers or booleans.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "parity" / "cases" / "datacheck_columns.yaml"
FIX = "tests/datacheck_columns/fixtures"
UP = "upstream/metacheck/tests/testthat"
PY = "pytacheck.datacheck.columns"

cases: list[dict[str, Any]] = []


def case(
    cid: str, fn: str, args: dict[str, Any], internal: bool | None = None, **extra: Any
) -> None:
    """Add a case calling R ``fn`` (``.x`` -> ``metacheck:::.x``) and ``columns.<py name>``."""
    internal = fn.startswith(".") if internal is None else internal
    r = f"metacheck:::{fn}" if internal else fn
    py = f"{PY}.{'_' + fn[1:] if fn.startswith('.') else fn}"
    spec: dict[str, Any] = {"id": cid, "r": r, "py": py, "args": args}
    spec.update(extra)
    cases.append(spec)


def chr_(*v: Any) -> dict[str, Any]:
    return {"$chr": list(v)}


def dbl(*v: Any) -> dict[str, Any]:
    return {"$dbl": list(v)}


def expr(r: str, py: str) -> dict[str, Any]:
    return {"$expr": {"r": r, "py": py}}


def fix(name: str) -> dict[str, str]:
    return {"$file": f"{FIX}/{name}"}


NULL = {"$null": True}
NA = {"$NA": True}

# ---------------------------------------------------------------- data_col_type
dct = [
    ("id", "subject_id", chr_("s01", "s02", "s03")),
    ("id_suffix", "participant.number", dbl(1, 2, 3, 4)),
    ("id_pp", "PP12", dbl(1, 2, 3)),
    ("id_sub_dash", "sub-01_rt", dbl(1.5, 2.5, 3.5)),
    ("id_bare", "ID", dbl(1, 2, 3)),
    ("not_id", "valid", dbl(1.5, 2.5, 3.5)),
    ("constant", "x", dbl(*[1] * 10)),
    ("binary", "cond", chr_("a", "b", "a", "b")),
    ("binary_lgl", "flag", {"$lgl": [True, False, None, True]}),
    ("continuous", "age", dbl(23.5, 45.1, 31.9, 29.2, 55.7)),
    ("empty", "e", dbl(None, None)),
    ("empty_chr", "e", chr_(None, None, None)),
    ("ambiguous_int", "rating", dbl(1, 2, 3, 4, 5, 3, 2, None)),
    ("ambiguous_integer_type", "trial", {"$int": list(range(1, 11))}),
    ("many_unique", "count", dbl(*range(1, 26))),
    ("date", "when", chr_("2020-01-01", "2020-02-03", "2021/5/6", "2021-13-01", "x")),
    ("date_mostly", "when", chr_("2020-01-01", "2020-02-03", "2021-05-06", "nope")),
    (
        "text",
        "comment",
        chr_(
            "This is a rather long free-text comment from participant one",
            "Another long free-text answer that exceeds forty characters",
            "A third long answer, also well over the forty character limit",
        ),
    ),
    (
        "comma_decimal",
        "price",
        chr_("1,50", "2,30", "4,10", "5,00", "3,25", "6,60", "2,10", "1,90"),
    ),
    (
        "outliers_excluded",
        "price",
        chr_("1,5", "2,3", "4,1", "5,0", "3,2", "6,6", "x", "2,1", "1,9", "8,1"),
    ),
    ("chr_ambiguous", "animal", chr_("cat", "dog", "bird", "cat", None)),
    ("numeric_strings", "val", chr_(" 1 ", "2e3", "0x1A", "Inf", "NA", "7", "8", "9", "10", "11")),
    ("inf", "val", dbl(1, 2, 3, "Inf")),
]
for cid, name, values in dct:
    case(f"data_col_type.{cid}", "data_col_type", {"col_name": name, "values": values})
case(
    "data_col_type.factor",
    "data_col_type",
    {
        "col_name": "level",
        "values": expr(
            'factor(c("lo", "hi", "mid", "lo"))',
            'pd.Series(pd.Categorical(["lo", "hi", "mid", "lo"]))',
        ),
    },
)

# ---------------------------------------------------------------- data_col_stats
normals = [0.52, -1.13, 0.27, 2.01, -0.44, 1.37, -0.08, 0.91, -1.72, 0.33, 0.65, -0.29,
           1.08, -0.51, 0.12, 2.48, -0.96, 0.04, 0.77, -1.25]  # fmt: skip
dcs = [
    ("basic", dbl(1, 2, 3, 4, 5), dbl(1, 2, 3, 4, 5)),
    ("null_stats", NULL, chr_("a", "b", None)),
    ("with_na", dbl(1, None, 3, 4, 10, 2.5), dbl(1, None, 3, 4, 10, 2.5)),
    ("single", dbl(5), dbl(5)),
    ("all_na", dbl(None, None), dbl(None, None)),
    ("character", chr_("1", "2", "x", None, "4.5"), chr_("1", "2", "x", None, "4.5")),
    ("three", dbl(1, 2, 10), dbl(1, 2, 10)),
    ("constant", dbl(3, 3, 3, 3), dbl(3, 3, 3, 3)),
    ("normal", dbl(*normals), dbl(*normals)),
    ("even_quartiles", dbl(7, 1, 3, 5, 9, 11, 2, 8), dbl(7, 1, 3, 5, 9, 11, 2, 8)),
    ("inf", dbl(1, 2, "Inf"), dbl(1, 2, "Inf")),
    ("raw_differs", dbl(1, 2, 3), chr_("a", "a", "b", None, None)),
    ("null_raw", dbl(1, 2), NULL),
]
for cid, x, raw in dcs:
    case(f"data_col_stats.{cid}", "data_col_stats", {"x_for_stats": x, "x_raw": raw})
case(
    "data_col_stats.df_column",
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
    "data_col_stats.list_column",
    "data_col_stats",
    {
        "x_for_stats": NULL,
        "x_raw": expr("list(1:2, 'a', NULL, list(x = 1))", "[[1, 2], 'a', None, {'x': 1}]"),
    },
)

# ---------------------------------------------------------------- normalisation
names_ = chr_(
    "SSS_total", "  Age.  ", "subj-id", "..x__y..", "A  B\tC", None, "", "Ünïcode_Name", "_lead"
)
case("normalize_varname.mixed", "normalize_varname", {"x": names_}, internal=True)
case("normalize_varname.empty", "normalize_varname", {"x": chr_()}, internal=True)
labels_ = chr_(
    "Participants' responses",
    "participant response",
    "The subject\u2019s ratings!",
    "Running   quickly",
    None,
    "",
    "generalization of RESULTS",
    "trees\u2018s  x",
)
case("normalize_label.mixed", "normalize_label", {"x": labels_}, internal=True)
case(
    ".normalize_header.mixed",
    ".normalize_header",
    {
        "x": chr_(
            "Variable_Name",
            " variable-name ",
            "Variable.Name",
            "VAR NAMES",
            None,
            "Ünïcode Label",
            "1st (col)",
        )
    },
)

hdrs = {
    "basic": chr_("varname", "description"),
    "names_label": chr_("Variable Names", "Label", "Scale"),
    "variable_label": chr_("Variable", "Variable label", "Values"),
    "full_name": chr_("Variable", "Full name", "Scale"),
    "question": chr_("item", "question"),
    "none": chr_("foo", "bar"),
    "only_var": chr_("variable", "type"),
    "na": chr_(None, "code", "definition"),
    "dup": chr_("id", "name", "desc", "text"),
    "empty": chr_(),
}
for cid, h in hdrs.items():
    case(f".find_codebook_cols.{cid}", ".find_codebook_cols", {"col_names": h})
case(".empty_codebook_vars", ".empty_codebook_vars", {})

for fn, variants in {
    ".find_missing_value_col": [
        chr_("variable", "Missing Values", "missing"),
        chr_("variable", "assigned missing values"),
        chr_("a", "b"),
    ],
    ".find_value_label_col": [
        chr_("variable", "Value Labels", "values"),
        chr_("Allowed_values", "x"),
        chr_("variable", "missing values"),
        chr_("code", "codes"),
    ],
    ".find_question_col": [
        chr_("var", "Question text", "question"),
        chr_("item wording"),
        chr_("prompt?", "x"),
    ],
    ".find_coding_instruction_col": [
        chr_("var", "Transformations"),
        chr_("how computed", "scoring"),
        chr_("coding"),
    ],
}.items():
    for k, v in enumerate(variants):
        case(f"{fn}.{k + 1}", fn, {"col_names": v})

# ---------------------------------------------------------------- OSD / scales
case(".osd_slug.name", ".osd_slug", {"name": "Positive and Negative Affect Schedule (PANAS)"})
case(".osd_slug.prefix", ".osd_slug", {"name": NA, "prefix": "resp_"})
case(".osd_slug.empty_name", ".osd_slug", {"name": "", "prefix": "BFI"})
case(".osd_slug.nothing", ".osd_slug", {})
case(".osd_slug.symbols", ".osd_slug", {"name": "!!!"})
case(
    ".osd_slug.long",
    ".osd_slug",
    {
        "name": "The Very Long Name Of An Instrument That Measures Many Different Things At Once In Detail"
    },
)
case(".osd_slug.long_noword", ".osd_slug", {"name": "a" * 70})
case(".osd_slug.max_chars", ".osd_slug", {"name": "alpha beta gamma delta", "max_chars": 12})
case(".osd_safe_code.basic", ".osd_safe_code", {"x": "panas short form (v2)"})
case(".osd_safe_code.null", ".osd_safe_code", {"x": NULL})
case(".osd_safe_code.symbols", ".osd_safe_code", {"x": "--!!--"})
case(
    ".osd_safe_code.long",
    ".osd_safe_code",
    {"x": "the items measure how anxious participants felt during the social evaluation task"},
)
case(".osd_safe_code.long_token", ".osd_safe_code", {"x": "x" * 45})
scale_dict = expr(
    'data.frame(name = c("Positive and Negative Affect Schedule", "Big Five Inventory", NA), '
    'code = c("PANAS", "", "X"), stringsAsFactors = FALSE)',
    "pd.DataFrame({'name': ['Positive and Negative Affect Schedule', 'Big Five Inventory', None], "
    "'code': ['PANAS', '', 'X']})",
)
for cid, scale, src in [
    ("dictionary", "positive and negative affect schedule", "manuscript"),
    ("self_generated", "Social anxiety", "self_generated"),
    ("unnamed_block", NA, "unnamed_block"),
    ("manuscript", "My Own Scale", NULL),
    ("curated", "Big Five Inventory", "self_generated"),
]:
    case(
        f".osd_code_and_provenance.{cid}",
        ".osd_code_and_provenance",
        {"scale": scale, "prefix": "resp", "scale_source": src, "dict": scale_dict},
    )
case(
    ".scale_ref_code.dict",
    ".scale_ref_code",
    {"scale": "Positive and Negative Affect Schedule", "dict": scale_dict},
)
case(".scale_ref_code.na", ".scale_ref_code", {"scale": NA, "dict": scale_dict})
case(".scale_reference.code", ".scale_reference", {"code": "PANAS"})
case(".scale_reference.null", ".scale_reference", {"code": NULL})
case(
    ".item_text_key.mixed",
    ".item_text_key",
    {
        "x": chr_(
            "I don't like <b>crowds</b>.", "I do not like crowds", "  Won't  STOP!! ", None, ""
        )
    },
)
case(".item_text_key.null", ".item_text_key", {"x": NULL})
reference = expr(
    'list(items = data.frame(item_id = c("A1", "A2", "A3", "A4"), '
    'text = c("I don\'t like crowds.", "I am the life of the party", "I worry", "I worry"), '
    'reverse = c(TRUE, FALSE, NA, FALSE), dimension = c("E", "E", "N", "N"), stringsAsFactors = FALSE))',
    "{'items': pd.DataFrame({'item_id': ['A1', 'A2', 'A3', 'A4'], "
    "'text': [\"I don't like crowds.\", 'I am the life of the party', 'I worry', 'I worry'], "
    "'reverse': pd.array([True, False, None, False], dtype='boolean'), 'dimension': ['E', 'E', 'N', 'N']})}",
)
wording = expr(
    'c(e1 = "I do not like crowds", e2 = "I am the LIFE of the party!", n1 = "I worry", x = NA, y = "", z = "Unrelated")',
    "{'e1': 'I do not like crowds', 'e2': 'I am the LIFE of the party!', 'n1': 'I worry', 'x': None, 'y': '', 'z': 'Unrelated'}",
)
case(".scale_match_items.basic", ".scale_match_items", {"wording": wording, "reference": reference})
case(".scale_match_items.no_ref", ".scale_match_items", {"wording": wording, "reference": NULL})
case(
    ".scale_match_items.no_wording",
    ".scale_match_items",
    {"wording": expr("c(a = NA_character_)", "{'a': None}"), "reference": reference},
)

# ---------------------------------------------------------------- value labels
enc = [
    ("basic", dbl(1, 2, -99), chr_("Male", "Female", "Refused")),
    ("na_blank", chr_("1", None, "3", "4"), chr_("a", "b", "  ", None)),
    ("all_blank", chr_("1"), chr_("")),
    ("dup_names", chr_("a", "", "a", "a.1", None), chr_("A", "B", "C", "D", "E")),
    ("recycle_codes", dbl(1, 2, 3), chr_("A", "B")),
    ("recycle_labels", dbl(1, 2), chr_("A", "B", "C")),
    ("numbers", dbl(1.5, 100000, 123456789012.5), dbl(3, 4.25, 1e-20)),
    ("escapes", dbl(1, 2), chr_('say "hi"\\now\n\ttab\u0001', "caf\u00e9 \u2028 /")),
    ("empty", chr_(), chr_()),
]
for cid, codes, labs in enc:
    case(f".encode_value_labels.{cid}", ".encode_value_labels", {"codes": codes, "labels": labs})
dec = [
    ("object", '{"1":"Male","2":"Female","-99":"Refused"}'),
    ("array", '["-99","-9"]'),
    ("mixed", '{"1":5,"2":"x","3":null}'),
    ("numeric", '{"1":5,"2":6.25}'),
    ("logical", '{"1":true}'),
    ("invalid", "{not json"),
    ("empty_obj", "{}"),
    ("empty_str", ""),
]
for cid, s in dec:
    case(f".decode_value_labels.{cid}", ".decode_value_labels", {"s": s})
case(".decode_value_labels.na", ".decode_value_labels", {"s": NA})
case(".decode_value_labels.null", ".decode_value_labels", {"s": NULL})
case(".encode_missing_values.codes", ".encode_missing_values", {"codes": dbl(-99, None, 9.5)})
case(".encode_missing_values.none", ".encode_missing_values", {"codes": dbl(None)})
case(
    ".encode_missing_values.reasons",
    ".encode_missing_values",
    {"codes": dbl(None, 1, 2), "reasons": chr_("x", "y", "z")},
)
for cid, labs in [
    ("short", chr_("Male", "Female", "Other", "Prefer not to say", "Unknown")),
    (
        "long",
        chr_(
            "I really enjoyed taking part in this study, it was very interesting",
            "no",
            "fine",
            "The second session was too long and I got a bit bored at the end",
            "ok",
            "this is a long response that describes the experience in detail!!",
        ),
    ),
    ("few", chr_("a very long label that is definitely more than forty characters", "b")),
    ("blank", chr_("", " ", "a", "b", "c", "d")),
    ("na", chr_("a", "b", "c", "d", None)),
]:
    case(f".looks_like_freetext_labels.{cid}", ".looks_like_freetext_labels", {"labs": labs})


def hv(cid: str, r: str, py: str) -> None:
    case(f".haven_value_labels.{cid}", ".haven_value_labels", {"col": expr(r, py)})


def series_attrs(values: str, attrs: str) -> str:
    return f"(lambda s: (s.attrs.update({attrs}), s)[1])(pd.Series({values}))"


hv(
    "spss",
    'haven::labelled_spss(c(1, 2, -99), labels = c(Male = 1, Female = 2, Refused = -99), na_values = -99, label = "Sex")',
    series_attrs(
        "[1.0, 2.0, -99.0]",
        "{'label': 'Sex', 'labels': {'Male': 1.0, 'Female': 2.0, 'Refused': -99.0}, 'na_values': [-99.0]}",
    ),
)
hv(
    "missing_names",
    "structure(c(1, 2), labels = c(Yes = 1, 'N/A' = 8, \"Don't know\" = 9, 'na czym polega' = 3, 'ID - N/A' = 4, 'Skipped question' = 7))",
    series_attrs(
        "[1.0, 2.0]",
        "{'labels': {'Yes': 1.0, 'N/A': 8.0, \"Don't know\": 9.0, 'na czym polega': 3.0, 'ID - N/A': 4.0, 'Skipped question': 7.0}}",
    ),
)
hv(
    "freetext",
    "structure(1:5, labels = c('I really enjoyed this study a lot, thank you so much' = 1, 'ok' = 2, 'The questions about my childhood were hard to answer' = 3, 'x' = 4, 'fine' = 5))",
    series_attrs(
        "[1, 2, 3, 4, 5]",
        "{'labels': {'I really enjoyed this study a lot, thank you so much': 1, 'ok': 2, 'The questions about my childhood were hard to answer': 3, 'x': 4, 'fine': 5}}",
    ),
)
hv(
    "na_range",
    "structure(c(1, 2), labels = c(Low = 1, High = 5), na_values = c(-9, -8), na_range = c(90, 99))",
    series_attrs(
        "[1.0, 2.0]",
        "{'labels': {'Low': 1.0, 'High': 5.0}, 'na_values': [-9.0, -8.0], 'na_range': [90.0, 99.0]}",
    ),
)
hv(
    "dup_missing",
    "structure(c(1, 2), labels = c(Refused = -99), na_values = c(-99, -98), na_range = c(-Inf, -100))",
    series_attrs(
        "[1.0, 2.0]",
        "{'labels': {'Refused': -99.0}, 'na_values': [-99.0, -98.0], 'na_range': [float('-inf'), -100.0]}",
    ),
)
hv(
    "character",
    "structure(c('M', 'F'), labels = c(Male = 'M', Female = 'F', Missing = 'X'))",
    series_attrs("['M', 'F']", "{'labels': {'Male': 'M', 'Female': 'F', 'Missing': 'X'}}"),
)
hv("none", "c(1, 2, 3)", "pd.Series([1.0, 2.0, 3.0])")

case(
    ".vl_is_numeric.mixed",
    ".vl_is_numeric",
    {"x": chr_("1", " -2 ", "3.5", "3.", ".5", "1e3", "a", None, "")},
)
vsp = [
    ("anchors", "1 (very negative) to 7 (very positive)"),
    ("anchors_dash", "-3 (disagree) - 0 (neutral) - 3(agree)"),
    ("comma_label", "Male = 1, Female = 2, Counselors, Social workers=21"),
    ("comma_code", "1: yes, 2: no, 3: maybe"),
    ("newline", "1-Male\n2-Female"),
    ("pipe", "0: no | 1: yes"),
    ("code_space", "1 Strongly disagree; 5 Strongly agree"),
    ("paren", "1) One; 2) Two"),
    ("prose", "High vs. Low"),
    ("bare", "1,2,3"),
    ("blank_parts", " ; ; "),
    ("single_anchor", "1 (only one)"),
]
for cid, s in vsp:
    case(f".vl_split_pairs.{cid}", ".vl_split_pairs", {"s": s})
pvl = [
    ("code_label", "1 = Male; 2 = Female"),
    ("colon_pipe", "0: no | 1: yes"),
    ("label_code", "Male = 1; Female = 2"),
    ("single", "1 = only"),
    ("empty", ""),
    ("text_text", "M = Male; F = Female"),
    ("mixed_dir", "1=Male; Female=2"),
    ("anchors", "1 (low) to 5 (high)"),
    ("missing", "1 = Yes; 2 = No; -99 = Refused; 98 = N/A"),
]
for cid, s in pvl:
    case(f".parse_value_label_text.{cid}", ".parse_value_label_text", {"s": s})
case(".parse_value_label_text.na", ".parse_value_label_text", {"s": NA})
case(
    ".parse_value_label_text.observed_lhs",
    ".parse_value_label_text",
    {"s": "M = Male; F = Female", "observed": chr_("M", "F", "M", None, " ")},
)
case(
    ".parse_value_label_text.observed_rhs",
    ".parse_value_label_text",
    {"s": "Male = M; Female = F", "observed": chr_("M", "F")},
)
case(
    ".parse_value_label_text.observed_tie",
    ".parse_value_label_text",
    {"s": "a = b; c = d", "observed": chr_("x")},
)
case(
    ".parse_value_label_text.observed_num",
    ".parse_value_label_text",
    {"s": "a = b; c = d", "observed": dbl(1, 2)},
)
for cid, s in [
    (
        "refused",
        '{"1":"Yes","-99":"Refused","-9":"N/A","98":"Don\'t know","4":"Declined to answer"}',
    ),
    ("prose_na", '{"1":"na czym polega","2":"ID - N/A","3":"N/A (lives alone)","4":"n/a."}'),
    ("none", '{"1":"Yes","2":"No"}'),
    ("invalid", "{bad"),
]:
    case(f".missing_from_value_labels.{cid}", ".missing_from_value_labels", {"vl_json": s})
case(".missing_from_value_labels.na", ".missing_from_value_labels", {"vl_json": NA})

# ---------------------------------------------------------------- layout detectors
case(
    ".looks_like_varnames.yes",
    ".looks_like_varnames",
    {"x": chr_("neo1", "BFI_3", "q07", "x.y", None, "")},
)
case(".looks_like_varnames.spaces", ".looks_like_varnames", {"x": chr_("neo 1", "BFI 3", "q07")})
case(".looks_like_varnames.dups", ".looks_like_varnames", {"x": chr_("a", "a", "a", "b")})
case(".looks_like_varnames.few", ".looks_like_varnames", {"x": chr_("a", "b")})
case(".looks_like_varnames.numeric", ".looks_like_varnames", {"x": dbl(1, 2, 3)})
case(
    ".looks_like_wording.yes",
    ".looks_like_wording",
    {"x": chr_("Worry about things", "Make friends", "Trust others")},
)
case(".looks_like_wording.short", ".looks_like_wording", {"x": chr_("a b", "c d", "e f")})
case(
    ".looks_like_wording.nospace",
    ".looks_like_wording",
    {"x": chr_("abcdefghij", "klmnopqrst", "u v w x y z")},
)
case(
    ".cb_is_definition_line.mixed",
    ".cb_is_definition_line",
    {
        "x": chr_(
            "age: participant age",
            "  sex = 1 male",
            "rt_mean   mean reaction time",
            "var\tlabel text",
            "no separator here at all",
            "x: ab",
            "123: numeric start",
            "a$b#c-d.e: odd name",
            None,
        )
    },
)
case(
    ".strip_rtf.basic",
    ".strip_rtf",
    {
        "text": "{\\rtf1\\ansi {\\fonttbl\\f0 Arial;}\\f0\\fs24 Hello\\par\n\\b bold\\b0 \\'e9 text\\~x \\{b\\}}"
    },
)

# ---------------------------------------------------------------- data-frame extractors
positional = {
    "a": ["IPIP", "neo1", "neo2", "neo3", "neo4", ""],
    "b": [
        None,
        "Worry about things",
        "Make friends easily",
        "Have a vivid imagination",
        "Trust others",
        "x",
    ],
    "c": ["", "1 - Very inaccurate", "1 - Very inaccurate", "1 = Very inaccurate", "nope", ""],
    "d": ["", "5 - Very accurate", "5 - Very accurate", "5. Very accurate", "5) Very accurate", ""],
}
case(
    ".extract_codebook_positional.anchors",
    ".extract_codebook_positional",
    {"df": {"$df": positional}, "src": "cb.xlsx [IPIP]"},
)
case(
    ".extract_codebook_positional.refused",
    ".extract_codebook_positional",
    {
        "df": {
            "$df": {
                "v": ["q1", "q2", "q3"],
                "w": ["How are you today", "What is your age", "Do you smoke"],
                "x": ["1 - Yes", "1 - Yes", "1 - Yes"],
                "y": ["99 - Refused", "99 - Refused", "2 - No"],
            }
        },
        "src": "s",
    },
)
case(
    ".extract_codebook_positional.single_anchor",
    ".extract_codebook_positional",
    {
        "df": {
            "$df": {
                "v": ["q1", "q2", "q3"],
                "w": ["How are you today", "What is your age", "Do you smoke"],
                "x": ["1 - Yes", None, "1 - Yes"],
            }
        },
        "src": "s",
    },
)
case(
    ".extract_codebook_positional.no_layout",
    ".extract_codebook_positional",
    {"df": {"$df": {"v": ["1", "2", "3"], "w": ["a", "b", "c"]}}, "src": "s"},
)
case(
    ".extract_codebook_positional.too_few",
    ".extract_codebook_positional",
    {"df": {"$df": {"v": ["q1", "q2"], "w": ["How are you", "What age"]}}, "src": "s"},
)
case(
    ".extract_codebook_positional.numeric_cols",
    ".extract_codebook_positional",
    {
        "df": {
            "$df": {
                "n": [1, 2, 3, 4],
                "v": ["q1", "q2", "q3", "q4"],
                "w": ["How are you today", "What is your age", "Do you smoke", "Where do you live"],
                "a": [1, 2, 3, 4],
            }
        },
        "src": "s",
    },
)
case(".extract_codebook_positional.null", ".extract_codebook_positional", {"df": NULL, "src": "s"})

structured = {
    "variable": ["sex", "age", "sex", "cond", "handed", "age", "", None, "score"],
    "label": ["Sex", "Age", "", "Condition", "Handedness", "", "blank var", "na var", None],
    "values": [
        "1 = Male; 2 = Female; -99 = Refused",
        "",
        "1=a;2=b",
        "M = Male; F = Female",
        "L = Left; R = Right",
        None,
        "",
        "",
        "1 = x; 2 = y",
    ],
    "question": ["What is your sex?", "  How old are you?  ", "", None, "", "", "", "", ""],
    "Missing codes": [
        "",
        "-9, -8",
        "",
        "",
        "-9 = not answered",
        "",
        "",
        "",
        "Refused = 99; 98 = DK",
    ],
    "Transformations": ["", "", "", "", "", "", "", "", "reverse: 6 - x"],
}
case(
    ".extract_structured_codebook.full",
    ".extract_structured_codebook",
    {"df": {"$df": structured}, "src": "cb.csv"},
)
case(
    ".extract_structured_codebook.observed",
    ".extract_structured_codebook",
    {
        "df": {"$df": structured},
        "src": "cb.csv",
        "observed": expr(
            'list(cond = c("M", "F", "M"), handed = c("Left", "Right"))',
            "{'cond': ['M', 'F', 'M'], 'handed': ['Left', 'Right']}",
        ),
    },
)
case(
    ".extract_structured_codebook.testthat",
    ".extract_structured_codebook",
    {
        "df": {
            "$df": {
                "variable": ["sex", "age"],
                "label": ["Sex", "Age"],
                "values": ["1 = Male; 2 = Female; -99 = Refused", ""],
                "question": ["What is your sex?", "How old are you?"],
            }
        },
        "src": "cb.csv",
    },
)
case(
    ".extract_structured_codebook.no_header",
    ".extract_structured_codebook",
    {"df": {"$df": {"a": ["x"], "b": ["y"]}}, "src": "cb.csv"},
)
case(
    ".extract_structured_codebook.numeric_var",
    ".extract_structured_codebook",
    {
        "df": {"$df": {"code": [1, 2, 2.5], "description": ["one", "two", "two and a half"]}},
        "src": "cb.csv",
    },
)
case(
    ".extract_structured_codebook.all_blank",
    ".extract_structured_codebook",
    {"df": {"$df": {"name": ["", " "], "label": ["a", "b"]}}, "src": "cb.csv"},
)

haven_df_r = (
    "local({ df <- data.frame(id = 1:3); "
    "df$sex <- haven::labelled_spss(c(1, 2, -99), labels = c(Male = 1, Female = 2, Refused = -99), na_values = -99, label = 'Sex'); "
    "df$age <- structure(c(23, 45, 31), label = '  Age in years '); "
    "df$q1 <- structure(c(1, 2, 3), labels = c('Strongly disagree' = 1, 'Strongly agree' = 5)); "
    "df$note <- structure(c('a', 'b', 'c'), label = ''); df })"
)
haven_df_py = (
    "(lambda df: (df.attrs.update({'col_attrs': {"
    "'sex': {'label': 'Sex', 'labels': {'Male': 1.0, 'Female': 2.0, 'Refused': -99.0}, 'na_values': [-99.0]}, "
    "'age': {'label': '  Age in years '}, "
    "'q1': {'labels': {'Strongly disagree': 1.0, 'Strongly agree': 5.0}}, "
    "'note': {'label': ''}}}), df)[1])("
    "pd.DataFrame({'id': [1, 2, 3], 'sex': [1.0, 2.0, -99.0], 'age': [23.0, 45.0, 31.0], 'q1': [1.0, 2.0, 3.0], 'note': ['a', 'b', 'c']}))"
)
case(
    ".extract_haven_labels.mixed",
    ".extract_haven_labels",
    {"df": expr(haven_df_r, haven_df_py), "src": "study.sav"},
)
case(
    ".extract_haven_labels.group",
    ".extract_haven_labels",
    {"df": expr(haven_df_r, haven_df_py), "src": "study.sav", "group": "ex2"},
)
case(
    ".extract_haven_labels.none",
    ".extract_haven_labels",
    {"df": {"$df": {"a": [1, 2], "b": ["x", "y"]}}, "src": "plain.sav"},
)

# ---------------------------------------------------------------- JSON helpers


def jl(s: str) -> dict[str, Any]:
    return expr(
        f"jsonlite::fromJSON({s!r}, simplifyVector = FALSE)",
        f"__import__('pytacheck.datacheck._columns_labels', fromlist=['x'])._json_loads({s!r})",
    )


for cid, s, want in [
    ("first", '{"Name": " sex ", "label": "Sex"}', ["name", "variable"]),
    ("skip_empty", '{"name": "", "variable": "age"}', ["name", "variable"]),
    ("number", '{"id": 3000000000, "code": 7}', ["id"]),
    ("list_value", '{"name": ["a", "b"], "column": true}', ["name", "column"]),
    ("null", '{"name": null}', ["name"]),
    ("dup", '{"name": "a", "NAME": "b"}', ["name"]),
    ("array", '["a", "b"]', ["name"]),
]:
    case(f".json_field.{cid}", ".json_field", {"entry": jl(s), "names_want": {"$chr": want}})
for cid, s in [
    (
        "objects",
        '[{"name": "1", "label": "Male"}, {"name": "2", "label": ""}, {"value": 9, "text": "Refused"}, {"label": "no code"}]',
    ),
    ("plain", '{"1": "Male", "2": "Female", "3": 7}'),
    ("plain_null", '{"1": "Male", "2": null, "3": "Other"}'),
    ("strings", '["1 = Male", "2 = Female", "junk"]'),
    ("scalar", '"1=yes;2=no"'),
    ("mixed_obj", '{"a": {"name": "1", "label": "One"}, "b": "2 = Two"}'),
    ("empty", "[]"),
]:
    case(f".json_value_labels.{cid}", ".json_value_labels", {"vl": jl(s)})
case(".json_value_labels.null", ".json_value_labels", {"vl": NULL})

for f in ["codebook.json", "schema.json", "not_codebook.json", "bad.json", "json_in.csv"]:
    case(f".extract_json_codebook.{f}", ".extract_json_codebook", {"path": fix(f), "src": f})
for f in ["readme.md", "prose.md"]:
    case(
        f".extract_markdown_codebook.{f}", ".extract_markdown_codebook", {"path": fix(f), "src": f}
    )

# ---------------------------------------------------------------- rich text / pdf
case(
    ".pdf_codebook_lines.codebook",
    ".pdf_codebook_lines",
    {"path": fix("codebook.pdf")},
    compare={"ws": True},
)
case(".pdf_codebook_lines.prose", ".pdf_codebook_lines", {"path": fix("prose.pdf")})
case(
    ".pdf_codebook_lines.max_pages",
    ".pdf_codebook_lines",
    {"path": fix("codebook.pdf"), "max_pages": 0},
)
case(
    ".pdf_codebook_lines.min_defs",
    ".pdf_codebook_lines",
    {"path": fix("codebook.pdf"), "min_defs": 7},
)
for f, ext in [
    ("codebook.docx", "docx"),
    ("codebook.rtf", "rtf"),
    ("codebook.odt", "odt"),
    ("codebook.xlsx", "docx"),
    ("readme.txt", "txt"),
]:
    case(f".extract_rich_text.{f}.{ext}", ".extract_rich_text", {"path": fix(f), "ext": ext})
case(
    ".extract_rich_text.codebook.pdf",
    ".extract_rich_text",
    {"path": fix("codebook.pdf"), "ext": "pdf"},
    compare={"ws": True},
)

# ---------------------------------------------------------------- parse_codebook
files = [
    "structured.csv",
    "semicolon_bom.csv",
    "title_rows.csv",
    "header_last.csv",
    "wide.csv",
    "wide_named.csv",
    "wide_transposed.csv",
    "latin1.csv",
    "tabbed.tsv",
    "no_header.csv",
    "json_in.csv",
    "readme.txt",
    "empty.csv",
    "codebook.json",
    "schema.json",
    "not_codebook.json",
    "bad.json",
    "readme.md",
    "prose.md",
    "codebook.xlsx",
    "codebook.ods",
    "labelled.sav",
    "labelled.dta",
    "codebook.docx",
    "codebook.rtf",
    "codebook.odt",
    "prose.pdf",
    "survey.qsf",
    "not_qsf.qsf",
    "no_sq.qsf",
    # U64: valid UTF-8 with an "NA" cell (R re-reads it as Latin-1: "Ã©")
    "utf8_na.csv",
]
for f in files:
    case(f"parse_codebook.{f}", "parse_codebook", {"path": fix(f)})
case(
    "parse_codebook.codebook.pdf",
    "parse_codebook",
    {"path": fix("codebook.pdf")},
    compare={"ws": True},
)
case("parse_codebook.missing_file", "parse_codebook", {"path": fix("does_not_exist.csv")})
case("parse_codebook.group", "parse_codebook", {"path": fix("structured.csv"), "group": "ex1"})
case(
    "parse_codebook.group_json_in_csv",
    "parse_codebook",
    {"path": fix("json_in.csv"), "group": "ex1"},
)
case("parse_codebook.group_qsf", "parse_codebook", {"path": fix("survey.qsf"), "group": "ex3"})
case(
    "parse_codebook.observed",
    "parse_codebook",
    {
        "path": fix("structured.csv"),
        "observed": expr('list(handed = c("L", "R", "L"))', "{'handed': ['L', 'R', 'L']}"),
    },
)
case(
    "parse_codebook.lookahead",
    "parse_codebook",
    {"path": fix("title_rows.csv"), "header_lookahead": 2},
)
case(
    "parse_codebook.jasp",
    "parse_codebook",
    {"path": {"$file": f"{UP}/fixtures/formats/sample.jasp"}},
)
case(
    "parse_codebook.omv", "parse_codebook", {"path": {"$file": f"{UP}/fixtures/formats/sample.omv"}}
)
for k, f in enumerate(
    ["Study 1.csv___CODEBOOK.csv", "Study 1 - csv - dataset 1.csv___CODEBOOK.csv"]
):
    case(
        f"parse_codebook.researchbox{k + 1}",
        "parse_codebook",
        {
            "path": {
                "$file": f"{UP}/.metacheck_repo_cache/researchbox.org_4377/unzipped/Codebook/{f}"
            }
        },
    )
case("parse_codebook.testthat_csv", "parse_codebook", {"path": fix("testthat_codebook.csv")})
case("parse_codebook.bom_qsf", "parse_codebook", {"path": fix("bom.qsf"), "group": "ex1"})
case("parse_codebook.directory", "parse_codebook", {"path": fix("")})
case(
    "parse_codebook.lookahead_zero",
    "parse_codebook",
    {"path": fix("structured.csv"), "header_lookahead": 0},
)
case(
    "parse_codebook.xlsx_lookahead",
    "parse_codebook",
    {"path": fix("codebook.xlsx"), "header_lookahead": 1},
)

# ---------------------------------------------------------------- QSF
for cid, x in [
    ("html", "<p>How <b>satisfied</b>&nbsp;are &amp; &lt;you&gt;?</p>\n  "),
    ("empty", "  <br> "),
    ("null", NULL),
    ("number", 5.5),
    ("na", NA),
]:
    case(f".qsf_strip_html.{cid}", ".qsf_strip_html", {"x": x})
case(".qsf_strip_html.list", ".qsf_strip_html", {"x": jl('["<i>a</i>", "b"]')})
for cid, s in [
    ("display", '{"Display": "<b>Yes</b>"}'),
    ("lower", '{"display": "no"}'),
    ("partial", '{"DisplayLogic": {"x": 1}}'),
    ("ambiguous", '{"DisplayA": "a", "DisplayB": "b"}'),
    ("list", '{"Display": ["first", "second"]}'),
    ("none", '{"Other": 1}'),
]:
    case(f".qsf_option_display.{cid}", ".qsf_option_display", {"opt": jl(s)})
case(".qsf_option_display.null", ".qsf_option_display", {"opt": NULL})
for cid, s in [
    ("object", '{"1": {"Display": "Disagree"}, "2": {"Display": "Agree"}, "3": {}}'),
    ("array", '[{"Display": "a"}, {"Display": "b"}]'),
    ("empty", "{}"),
]:
    case(f".qsf_value_labels.{cid}", ".qsf_value_labels", {"opts": jl(s)})
case(".qsf_value_labels.null", ".qsf_value_labels", {"opts": NULL})
for cid, tag, ct, code in [
    ("shared_stem", "SV", "SV_1", "1"),
    ("same", "q1", "Q1", "1"),
    ("own_stem", "POWER.PP1", "STATUS.PP1_8", "8"),
    ("suffix", "Q3", "12", "12"),
    ("reserved", "Q3", "TEXT", "4"),
    ("none", "Q3", NA, "4"),
    ("empty", "Q3", "", "4"),
    ("spaces", " Q3 ", " 2 ", "2"),
    ("na_tag", NA, "na", "1"),
    ("na_tag_prefix", NA, "NA_2", "1"),
    ("na_tag_none", NA, NA, "3"),
    ("case_insensitive", "q3", "Q3_7", "7"),
    ("own_dash", "Q3", "a-b", "1"),
    ("digits_dot", "Q3", "1.5", "1"),
]:
    case(f".qsf_export_col.{cid}", ".qsf_export_col", {"tag": tag, "choice_tag": ct, "code": code})
for f in [
    "survey.qsf",
    "not_qsf.qsf",
    "no_sq.qsf",
    "bad.json",
    "does_not_exist.qsf",
    "bom.qsf",
    "empty.csv",
]:
    case(f"parse_qsf.{f}", "parse_qsf", {"path": fix(f)})
case(
    "parse_qsf.scalar_json",
    "parse_qsf",
    {
        "path": expr(
            'local({ d <- tempfile(fileext = ".qsf"); writeLines("5", d); d })',
            "(lambda d: (open(d, 'w').write('5\\n'), d)[1])(__import__('tempfile').mkstemp(suffix='.qsf')[1])",
        )
    },
)

# ---------------------------------------------------------------- groups + matching
case(
    ".infer_group.mixed",
    ".infer_group",
    {
        "context_str": chr_(
            "Experiment 1",
            "Study 2a",
            "Pilot 1",
            "pilot3B",
            "",
            None,
            "STUDY 10",
            "Experiments 1",
            "the second study",
            "Experiment 1 and Pilot 2",
        )
    },
)
case(".infer_group.empty", ".infer_group", {"context_str": NULL})


def cols(**kw: Any) -> dict[str, Any]:
    return {"$df": kw}


def mcl(cid: str, columns: Any, cbk: Any, **extra: Any) -> None:
    case(
        f"match_column_labels.{cid}",
        "match_column_labels",
        {"columns_df": columns, "codebook_vars_df": cbk},
        **extra,
    )


mcl(
    "ddi",
    cols(paper_id=["p"], source_file=["d.csv"], column_name=["sex"]),
    cols(
        codebook_variable=["sex"],
        label=["Sex"],
        codebook_source=["cb.csv"],
        group=[None],
        value_labels=['{"1":"Male","2":"Female"}'],
        missing_values=[None],
        question=["What is your sex?"],
        coding_instructions=["recoded from raw gender field"],
        parse_method=["structured"],
    ),
)
mcl(
    "normalised",
    cols(
        paper_id=["p"] * 3, source_file=["d.csv"] * 3, column_name=["age", "SSS_total", "unmatched"]
    ),
    cols(
        codebook_variable=["age", "sss total"],
        label=["Age in years", "SSS total score"],
        codebook_source=["cb.csv", "cb.csv"],
        group=[None, None],
        parse_method=["structured"] * 2,
    ),
)
mcl(
    "conflicting",
    cols(paper_id=["p"], source_file=["d.csv"], column_name=["dv"]),
    cols(
        codebook_variable=["dv", "dv"],
        label=["reaction time", "accuracy"],
        codebook_source=["a.csv", "b.csv"],
        group=[None, None],
        parse_method=["structured"] * 2,
    ),
)
mcl(
    "no_codebook",
    cols(paper_id=["p", "p"], source_file=["d.csv"] * 2, column_name=["a", "b"]),
    {
        "$call": {
            "r": "metacheck:::.empty_codebook_vars",
            "py": f"{PY}._empty_codebook_vars",
            "args": {},
        }
    },
)
mcl(
    "null_codebook",
    cols(paper_id=["p"], source_file=["d.csv"], column_name=["a"], group=["ex1"]),
    NULL,
)
mcl(
    "empty_columns",
    {
        "$call": {
            "r": "metacheck:::.empty_codebook_vars",
            "py": f"{PY}._empty_codebook_vars",
            "args": {},
        }
    },
    cols(codebook_variable=["a"], label=["A"], codebook_source=["s"], group=[None]),
)
mcl(
    "null_columns",
    NULL,
    cols(codebook_variable=["a"], label=["A"], codebook_source=["s"], group=[None]),
)
scoped_cb = cols(
    codebook_variable=["age", "sex", "sex", "cond", "cond", "rt", "rt", "acc", "acc", "acc"],
    label=[
        "Age",
        "Sex",
        "Gender",
        "Condition A",
        "Condition B",
        "Reaction time",
        "reaction times",
        "accuracy",
        "Accuracy (prop)",
        "Accuracy",
    ],
    codebook_source=[
        "a.csv",
        "a.csv",
        "b.csv",
        "a.csv",
        "b.csv",
        "a.csv",
        "b.sav",
        "a.csv",
        "b.sav",
        "c.sav",
    ],
    group=["ex1", None, "ex2", "ex1", "ex2", None, None, None, None, None],
    parse_method=[
        "structured",
        "structured",
        "structured",
        "structured",
        "structured",
        "structured",
        "structured",
        "structured",
        "haven",
        "haven",
    ],
    value_labels=[None, '{"1":"M"}', '{"1":"F"}', None, None, "", None, None, None, '{"0":"no"}'],
)
mcl(
    "scoped",
    cols(
        paper_id=["p"] * 8,
        source_file=["d.csv"] * 8,
        column_name=["age", "age", "sex", "cond", "cond", "rt", "acc", "sex"],
        group=["ex1", "ex3", "ex2", "ex3", "ex1", "ex1", "ex1", "ex1"],
    ),
    scoped_cb,
)
mcl(
    "na_group",
    cols(paper_id=["p"] * 4, source_file=["d.csv"] * 4, column_name=["age", "sex", "cond", "rt"]),
    scoped_cb,
)
mcl(
    "experiment_group",
    cols(
        paper_id=["p"] * 2,
        source_file=["d.csv"] * 2,
        column_name=["age", "cond"],
        experiment_group=["ex1", "ex2"],
    ),
    scoped_cb,
)
mcl(
    "paper_scope",
    cols(paper_id=["p1", "p2", "p3", None], source_file=["d.csv"] * 4, column_name=["age"] * 4),
    cols(
        codebook_variable=["age", "age", "age"],
        label=["Age 1", "Age 2", "Age NA"],
        codebook_source=["a", "b", "c"],
        group=[None, None, None],
        paper_id=["p1", "p2", None],
    ),
)
mcl(
    "no_group_col",
    cols(paper_id=["p"], source_file=["d.csv"], column_name=["age"]),
    cols(codebook_variable=["age"], label=["Age"], codebook_source=["a"]),
)
mcl(
    "ranges",
    cols(
        paper_id=["p"] * 6,
        source_file=["d.csv"] * 6,
        column_name=["V1", "V3", "V4", "q2", "item_2", "x10"],
    ),
    cols(
        codebook_variable=["V1-V3", "q1 \u2013 q2", "item5-2", "x", "x9-10"],
        label=["Ratings", "Qs", "bad range", "X", "Tens"],
        codebook_source=["cb"] * 5,
        group=[None] * 5,
    ),
)
mcl(
    "ranges_none_valid",
    cols(paper_id=["p"], source_file=["d.csv"], column_name=["item5"]),
    cols(
        codebook_variable=["item5-2", "item5"],
        label=["bad", "Item five"],
        codebook_source=["cb"] * 2,
        group=[None] * 2,
    ),
)
mcl(
    "merged_rules",
    cols(paper_id=["p"], source_file=["d.csv"], column_name=["resp"]),
    cols(
        codebook_variable=["resp", "RESP"],
        label=["Participant response", "Participants' responses"],
        codebook_source=["a.csv", "b.csv"],
        group=[None, None],
        question=[None, "Q?"],
        coding_instructions=["", "sum"],
    ),
)
qsf_cb = cols(
    codebook_variable=["Q1914_4", "Q1914_5", "SAT", "POWER.PP1_1", "POWER", "other"],
    label=["Item 4", "Item 5", "Satisfied?", "powerful", "Power", "Other"],
    codebook_source=["s.qsf"] * 5 + ["cb.csv"],
    group=[None] * 6,
    value_labels=['{"1":"a"}', '{"1":"a"}', '{"1":"yes"}', None, None, None],
    missing_values=[None] * 6,
    question=["Text entry matrix", "Text entry matrix", None, "Rate", "Power q", "Other q"],
    coding_instructions=[None] * 6,
    scale_group=["Q1914", "Q1914", "SAT", "POWER.PP1", "POWER", "OTHER"],
    parse_method=["qsf"] * 5 + ["structured"],
)
mcl(
    "qsf_tags",
    cols(
        paper_id=["p"] * 9,
        source_file=["d.csv"] * 9,
        column_name=[
            "Q1914_1_1",
            "Q1914_2_1",
            "Q1914_First.Click",
            "Q1914_page.submit",
            "SAT",
            "SAT_TEXT",
            "POWER.PP1_7",
            "POWER_2",
            "OTHER_1",
        ],
    ),
    qsf_cb,
)
mcl(
    "qsf_no_method",
    cols(paper_id=["p"], source_file=["d.csv"], column_name=["OTHER_1"]),
    cols(
        codebook_variable=["x"],
        label=["X"],
        codebook_source=["cb"],
        group=[None],
        question=["Q"],
        scale_group=["OTHER"],
        value_labels=[None],
    ),
)
mcl(
    "qsf_no_source",
    cols(paper_id=["p"], source_file=["d.csv"], column_name=["OTHER_1"]),
    cols(
        codebook_variable=["x"],
        label=["X"],
        group=[None],
        question=["Q"],
        scale_group=["OTHER"],
        value_labels=[None],
    ),
)
mcl(
    "no_paper_col",
    cols(source_file=["d.csv"], column_name=["age"]),
    cols(codebook_variable=["age"], label=["Age"], codebook_source=["cb"], group=[None]),
)

mcl(
    "no_source_col",
    cols(paper_id=["p", "p"], column_name=["age", "x"]),
    cols(codebook_variable=["age"], label=["Age"], codebook_source=["cb"], group=[None]),
)
mcl("no_paper_col_empty_cb", cols(source_file=["d.csv", "e.csv"], column_name=["age", "x"]), NULL)
mcl("no_colname_qsf", cols(paper_id=["p"], source_file=["d.csv"]), qsf_cb)
mcl("na_colname_qsf", cols(paper_id=["p"], source_file=["d.csv"], column_name=[None]), qsf_cb)
mcl(
    "no_cbvar_col",
    cols(paper_id=["p"], source_file=["d.csv"], column_name=["age"]),
    cols(label=["Age"], codebook_source=["cb"], group=[None]),
)
mcl(
    "haven_all_na_labels",
    cols(paper_id=["p"], source_file=["d.csv"], column_name=["x"]),
    cols(
        codebook_variable=["x", "x"],
        label=["X one", "X two"],
        codebook_source=["a.sav", "b.csv"],
        group=[None, None],
        parse_method=["haven", "structured"],
    ),
)
mcl(
    "numeric_paper_ids",
    cols(paper_id=[1, 2], source_file=["d.csv"] * 2, column_name=["age", "age"]),
    cols(
        codebook_variable=["age"],
        label=["Age"],
        codebook_source=["cb"],
        group=[None],
        paper_id=["2"],
    ),
)
mcl(
    "carry_props",
    cols(paper_id=["p"], source_file=["d.csv"], column_name=["sex"]),
    cols(
        codebook_variable=["sex", "sex"],
        label=["Sex", "Sex"],
        codebook_source=["a", "b"],
        group=[None, None],
        value_labels=["", '{"1":"M"}'],
        missing_values=[None, '["-9"]'],
        question=["  ", "Q?"],
        coding_instructions=[None, None],
        scale_group=["S", None],
    ),
)
case(
    "normalize_label.stems",
    "normalize_label",
    {
        "x": chr_(
            "generalizations happily connected",
            "Relational ponies caresses",
            "agreed agreement",
            "conditional hopefulness",
            "The participants' ratings of attractiveness",
        )
    },
    internal=True,
)
case("normalize_label.empty", "normalize_label", {"x": chr_()}, internal=True)
case(
    "data_col_type.latin1_name",
    "data_col_type",
    {
        "col_name": expr(
            'paste0(rawToChar(as.raw(0xef)), "PeronalData_fullname")',
            "b'\\xefPeronalData_fullname'",
        ),
        "values": {"$int": list(range(1, 11))},
    },
)
case(
    "data_col_type.latin1_id_name",
    "data_col_type",
    {
        "col_name": expr('paste0("subj", rawToChar(as.raw(0xe9)), "_id")', "b'subj\\xe9_id'"),
        "values": dbl(1, 2, 3),
    },
)

# ---------------------------------------------------------------- .scale_typo_of
for cid, v, lo, hi in [
    ("repeat", 33, 1, 7),
    ("double", 25, 1, 7),
    ("sign", -3, 1, 7),
    ("decimal", 3.5, 1, 7),
    ("sci", 1e5, 1, 7),
    ("leading", 105, 0, 9),
    ("bipolar", 9, -3, 3),
    ("inside", 4, 1, 7),
    ("na", NA, 1, 7),
    ("large", 77, 1, 5),
    ("neg_bipolar", -33, -3, 3),
    ("inf", expr("Inf", "float('inf')"), 1, 7),
    ("desc_range", 3, 7, 1),
    ("frac_range", 2, 0.5, 3.5),
]:
    case(f".scale_typo_of.{cid}", ".scale_typo_of", {"v": v, "lo": lo, "hi": hi})


def main() -> None:
    header = (
        "# Parity cases for R/data_check_helpers.R part 2 (columns, codebooks, labels).\n"
        "# Generated by tests/datacheck_columns/gen_parity_cases.py -- edit that script, not this file.\n"
    )
    body = yaml.safe_dump(
        {"area": "datacheck_columns", "cases": cases},
        sort_keys=False,
        allow_unicode=True,
        default_style='"',
        width=100,
    )
    OUT.write_text(header + body, encoding="utf-8")
    print(f"wrote {len(cases)} cases to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
