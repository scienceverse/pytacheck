"""Generate ``parity/cases/statout_readers_review.yaml`` (every string quoted).

Review cases for the statistics-output readers: they target branches the first
round of cases did not reach (see ``make_review_fixtures.py``). Run
``python tests/statout_readers/make_review_fixtures.py``, then this script,
then ``python -m parity generate --area statout_readers_review``.
"""

import json
from pathlib import Path

import yaml

FX = "tests/statout_readers/fixtures/review"
PH = "tests.statout_readers.parity_helpers"
cases = []


def q(s):
    return json.dumps(s, ensure_ascii=False)


def add(**kw):
    cases.append(kw)


def fn_case(id_, r, py, args, **extra):
    add(id=id_, r=r, py=py, args=args, **extra)


def expr_case(id_, r_code, py_fn, py_args, **extra):
    add(
        id=id_,
        r="identity",
        py=py_fn,
        args={"x": {"$expr": {"r": r_code, "py": "None"}}},
        py_drop=["x"],
        py_args=py_args,
        **extra,
    )


def vec_case(id_, r_code, py_code, **extra):
    add(
        id=id_,
        r="identity",
        py="copy.copy",
        args={"x": {"$expr": {"r": r_code, "py": py_code}}},
        **extra,
    )


EXPORT_R = (
    '{{f <- tempfile(fileext = ".html"); metacheck::{fn}(rpath("{p}"), f); '
    "x <- readLines(f, warn = FALSE); {post}}}"
)
IMG = 'gsub("data:image/[a-z+]+;base64,[A-Za-z0-9+/=]+", "data:IMG", x)'
SYNTAX_R = (
    '{{d <- tempfile(); dir.create(d); file.copy(rpath("{p}"), d); '
    'p <- metacheck:::{fn}(file.path(d, basename("{p}"))); '
    "if (is.na(p)) NULL else list(path = substring(p, nchar(d) + 1), lines = readLines(p, warn = FALSE))}}"
)

# ---------------------------------------------------------------- SPV
for name in ["spv_strings", "spv_charts", "spv_charts_ok", "spv_box_na"]:
    p = f"{FX}/{name}.spv"
    fn_case(
        f"import_spv.{name}",
        "import_spv",
        "pytacheck.statout.spv.import_spv",
        {"path": {"$file": p}},
    )
    expr_case(
        f"import_spv.attrs.{name}",
        f'lapply(metacheck::import_spv(rpath("{p}")), function(t) {{ a <- attributes(t$data); '
        "if (isTRUE(t$is_chart)) { fits <- a$spv_chart_fits %||% list(); "
        "list(type = a$spv_chart_type, title = a$spv_chart_title, xlab = a$spv_chart_xlab, "
        'ylab = a$spv_chart_ylab, fit_names = vapply(fits, function(f) as.character(f$name), ""), '
        'fit_exprs = vapply(fits, function(f) f$expr, "")) } else '
        "list(title = a$spv_title, rows = a$spv_row_dims, cols = a$spv_col_dims, layers = a$spv_layer_dims, "
        'footnotes = vapply(a$spv_footnotes %||% list(), function(f) paste(f$text, f$marker, sep = "|"), "")) })',
        f"{PH}.spv_attrs",
        {"path": p},
    )
    expr_case(
        f"export_spv_html.{name}",
        EXPORT_R.format(fn="export_spv_html", p=p, post=IMG),
        f"{PH}.export_lines",
        {"fn": "pytacheck.statout.spv.export_spv_html", "path": p, "images": True},
    )
    expr_case(
        f"spv_read_structure.{name}",
        f'{{d <- tempfile(); dir.create(d); utils::unzip(rpath("{p}"), exdir = d); '
        "rows <- metacheck:::.spv_read_structure(d); if (!length(rows)) NULL else "
        "do.call(rbind, lapply(rows, function(r) as.data.frame(r, stringsAsFactors = FALSE)))}",
        f"{PH}.spv_structure",
        {"path": p},
    )
expr_case(
    "spv_export_syntax.spv_strings",
    SYNTAX_R.format(fn=".spv_export_syntax", p=f"{FX}/spv_strings.spv"),
    f"{PH}.export_syntax",
    {"fn": "pytacheck.statout.spv._spv_export_syntax", "path": f"{FX}/spv_strings.spv"},
)
for member in ["t2_lightTableData.bin", "t3_lightTableData.bin"]:
    expr_case(
        f"spv_decode_light_table.{member[:2]}",
        f'{{d <- tempfile(); dir.create(d); utils::unzip(rpath("{FX}/spv_strings.spv"), exdir = d); '
        f'f <- file.path(d, "{member}"); metacheck:::.spv_decode_light_table(readBin(f, "raw", file.size(f)))}}',
        f"{PH}.light_table",
        {"path": f"{FX}/spv_strings.spv", "member": member},
    )

DISPLAY = ["1e5", "1E5", " 1.5", "1.5 ", "1,5", "Inf", "-Inf", "NaN", "0x10", "1e-400", "5e-4", "-5e-4",
           "0.00049", "0.0005000001", "2.0005", "-2.5", "1e15", "1e16", "123456789.1239", "99.9995",
           "+", "-", ".", "..", "1e", "e5", "TRUE", "0.1234567890123456789"]  # fmt: skip
vec_case(
    "spv_display_value.more",
    f'c(vapply(c({", ".join(q(s) for s in DISPLAY)}), metacheck:::.spv_display_value, "", USE.NAMES = FALSE), '
    "metacheck:::.spv_display_value(1e5), metacheck:::.spv_display_value(0.1 + 0.2), "
    "metacheck:::.spv_display_value(-0.0004), metacheck:::.spv_display_value(123456.7), "
    "metacheck:::.spv_display_value(TRUE), metacheck:::.spv_display_value(7L), "
    "metacheck:::.spv_display_value(Inf), metacheck:::.spv_display_value(1e300))",
    f"[pc.statout.spv._spv_display_value(v) for v in [{', '.join(q(s) for s in DISPLAY)}]] + "
    "[pc.statout.spv._spv_display_value(v) for v in [1e5, 0.1 + 0.2, -0.0004, 123456.7, True, 7, "
    'float("inf"), 1e300]]',
)
vec_case(
    "spv_html_escape.more",
    "c(metacheck:::.spv_html_escape(0.1 + 0.2), metacheck:::.spv_html_escape(1e5), "
    'metacheck:::.spv_html_escape(100000L), metacheck:::.spv_html_escape(TRUE), metacheck:::.spv_html_escape("&amp;"))',
    "[pc.statout.spv._spv_html_escape(v) for v in [0.1 + 0.2, 1e5, 100000, True, '&amp;']]",
)
# pivot with a numeric value column and NA dimension labels
vec_case(
    "spv_table_html.numeric_values",
    '{df <- data.frame(G = c("g1", NA, "g1"), S = c("N", "N", "M"), value = c(0.1 + 0.2, NA, 1e5), '
    'stringsAsFactors = FALSE); attr(df, "spv_row_dims") <- "G"; attr(df, "spv_col_dims") <- "S"; '
    "metacheck:::.spv_table_html(df)}",
    '(lambda df: (df.attrs.update({"spv_row_dims": ["G"], "spv_col_dims": ["S"]}), '
    "pc.statout.spv._spv_table_html(df))[1])(pd.DataFrame({"
    '"G": pd.array(["g1", None, "g1"], dtype="string"), "S": pd.array(["N", "N", "M"], dtype="string"), '
    '"value": [0.1 + 0.2, float("nan"), 1e5]}))',
)
vec_case(
    "spv_table_html.flat_numeric",
    '{df <- data.frame(a = c(1.5, NA), b = c(NA, "x"), c = c(2L, NA), stringsAsFactors = FALSE); '
    "metacheck:::.spv_table_html(df)}",
    'pc.statout.spv._spv_table_html(pd.DataFrame({"a": [1.5, float("nan")], '
    '"b": pd.array([None, "x"], dtype="string"), "c": pd.array([2, None], dtype="Int64")}))',
)
vec_case(
    "spv_table_html.empty_row_key",
    '{df <- data.frame(G = c("", "a"), S = c("s", "s"), value = c("1", "2"), stringsAsFactors = FALSE); '
    'attr(df, "spv_row_dims") <- "G"; attr(df, "spv_col_dims") <- "S"; metacheck:::.spv_table_html(df)}',
    '(lambda df: (df.attrs.update({"spv_row_dims": ["G"], "spv_col_dims": ["S"]}), '
    "pc.statout.spv._spv_table_html(df))[1])(pd.DataFrame({"
    '"G": pd.array(["", "a"], dtype="string"), "S": pd.array(["s", "s"], dtype="string"), '
    '"value": pd.array(["1", "2"], dtype="string")}))',
)
vec_case(
    "spv_table_html.numeric_dims",
    '{df <- data.frame(G = c(1, 2.5, NA, 1e5), S = c("a", "a", "b", "b"), value = c("1", "2", "3", "4"), '
    'stringsAsFactors = FALSE); attr(df, "spv_row_dims") <- "G"; attr(df, "spv_col_dims") <- "S"; '
    "metacheck:::.spv_table_html(df)}",
    '(lambda df: (df.attrs.update({"spv_row_dims": ["G"], "spv_col_dims": ["S"]}), '
    "pc.statout.spv._spv_table_html(df))[1])(pd.DataFrame({"
    '"G": [1.0, 2.5, float("nan"), 1e5], "S": pd.array(["a", "a", "b", "b"], dtype="string"), '
    '"value": pd.array(["1", "2", "3", "4"], dtype="string")}))',
)
# spv_assemble_table called directly: missing n_leaves, a zero axis index
vec_case(
    "spv_assemble_table.loose",
    'metacheck:::spv_assemble_table(dims = list(list(name = "A", leaves = list("0" = "a", "1" = "b")), '
    'list(leaves = list("0" = "z"), n_leaves = 1L)), axes = list(rows = c(0L, 1L)), '
    'cells = list(list(index = 1, value = list(type = "numeric", x = -0)), '
    'list(index = 0, value = list(type = "template", s = "^1"))))',
    'pc.statout.spv.spv_assemble_table(dims=[{"name": "A", "leaves": {"0": ["a"], "1": ["b"]}}, '
    '{"leaves": {"0": ["z"]}, "n_leaves": 1}], axes={"rows": [0, 1]}, '
    'cells=[{"index": 1.0, "value": {"type": "numeric", "x": -0.0}}, '
    '{"index": 0.0, "value": {"type": "template", "s": "^1"}}])',
)

# ---------------------------------------------------------------- OMV / JASP
LAB_R = (
    'lapply(metacheck::{fn}(rpath("{p}"))$data, function(col) list(label = {{l <- attr(col, "label", exact = TRUE); '
    "if (is.numeric(l)) list(codes = unname(l), labels = names(l)) else l}}, "
    'codes = unname(attr(col, "labels")), labels = names(attr(col, "labels"))))'
)
for fn, mod, ext, names in [
    ("import_omv", "omv", "omv", ["review", "empty_index", "fields_object", "nameless"]),
    (
        "import_jasp",
        "jasp",
        "jasp",
        ["review", "short", "sqlite_review", "sqlite_nulltype", "sqlite_types"],
    ),
]:
    for name in names:
        p = f"{FX}/{name}.{ext}"
        if name == "sqlite_types":
            # the canonical encoder writes bit64::integer64 as raw bits: compare as.numeric()
            expr_case(
                f"{fn}.{name}",
                f'{{r <- metacheck::{fn}(rpath("{p}")); r$data[] <- lapply(r$data, function(c) '
                'if (inherits(c, "integer64")) as.numeric(c) else c); r}',
                f"{PH}.call",
                {"fn": f"pytacheck.statout.{mod}.{fn}", "path": {"$file": p}},
            )
        else:
            fn_case(f"{fn}.{name}", fn, f"pytacheck.statout.{mod}.{fn}", {"path": {"$file": p}})
        if name not in ("empty_index", "short", "sqlite_nulltype", "nameless"):
            expr_case(
                f"{fn}.labels.{name}",
                LAB_R.format(fn=fn, p=p),
                f"{PH}.data_labels",
                {"fn": f"pytacheck.statout.{mod}.{fn}", "path": p},
            )
for fn, mod, name in [
    ("export_omv_html", "omv", "review.omv"),
    ("export_omv_html", "omv", "trailing_pct.omv"),
    ("export_omv_html", "omv", "empty_index.omv"),
    ("export_jasp_html", "jasp", "review.jasp"),
]:
    expr_case(
        f"{fn}.{name.split('.')[0]}",
        EXPORT_R.format(fn=fn, p=f"{FX}/{name}", post="x"),
        f"{PH}.export_lines",
        {"fn": f"pytacheck.statout.{mod}.{fn}", "path": f"{FX}/{name}"},
    )
for fn, mod, name in [("export_omv_html", "omv", "badurl.omv")]:
    fn_case(
        f"{fn}.{name.split('.')[0]}",
        fn,
        f"pytacheck.statout.{mod}.{fn}",
        {
            "path": {"$file": f"{FX}/{name}"},
            "out": {"$expr": {"r": "tempfile()", "py": "__import__('tempfile').mktemp()"}},
        },
    )
expr_case(
    "jasp_analyses_summary.review",
    f'metacheck:::.jasp_analyses_summary(metacheck::import_jasp(rpath("{FX}/review.jasp"))$analyses)',
    f"{PH}.jasp_summary",
    {"path": f"{FX}/review.jasp"},
)
OMV_SYNTAX = [
    "R jmv::fn\\(x) jmv::ok(y)",
    "R jmv::fn\\(x)",
    "R pkg::fn\t(a)",
    "R pkg::fn \t (a)",
    "R pkg::t\\t(a)",
    "::fn(a)",
    "x.y2::f_1.2(a, (b), c)",
    "R pkg::fn(a))",
    "R pkg::fn((a)",
    "R pkg::fn( a\n\t b )",
    "",
]
vec_case(
    "omv_extract_syntax.more",
    f'vapply(c({", ".join(q(s) for s in OMV_SYNTAX)}), metacheck:::.omv_extract_syntax, "", USE.NAMES = FALSE)',
    f"[pc.statout.omv._omv_extract_syntax(s) for s in [{', '.join(q(s) for s in OMV_SYNTAX)}]]",
)
URLS = ["a%20b.png", "a%", "a%2", "a%2%41", "%41%62c", "a+b.png", "%C3%A9", "%2F%2e"]
vec_case(
    "url_decode.vector",
    f'vapply(c({", ".join(q(s) for s in URLS)}), function(u) suppressWarnings(utils::URLdecode(u)), "", USE.NAMES = FALSE)',
    f"[pc.statout.jasp._url_decode(s) for s in [{', '.join(q(s) for s in URLS)}]]",
)

# ---------------------------------------------------------------- Stata
fn_case(
    "import_stata_smcl.nul",
    "import_stata_smcl",
    "pytacheck.statout.stata.import_stata_smcl",
    {"path": {"$file": f"{FX}/nul.smcl"}},
)
p = f"{FX}/tricky.smcl"
fn_case(
    "import_stata_smcl.tricky",
    "import_stata_smcl",
    "pytacheck.statout.stata.import_stata_smcl",
    {"path": {"$file": p}},
)
expr_case(
    "export_stata_smcl_html.tricky",
    EXPORT_R.format(fn="export_stata_smcl_html", p=p, post="x"),
    f"{PH}.export_lines",
    {"fn": "pytacheck.statout.stata.export_stata_smcl_html", "path": p},
)
expr_case(
    "smcl_export_syntax.tricky",
    SYNTAX_R.format(fn=".smcl_export_syntax", p=p),
    f"{PH}.export_syntax",
    {"fn": "pytacheck.statout.stata._smcl_export_syntax", "path": p},
)
expr_case(
    "smcl_render.tricky",
    f'metacheck:::.smcl_render(readLines(rpath("{p}"), warn = FALSE, encoding = "UTF-8"))',
    f"{PH}.smcl_render_file",
    {"path": p},
)
expr_case(
    "smcl_command_chunks.tricky",
    f'metacheck:::.smcl_command_chunks(metacheck:::.smcl_render(readLines(rpath("{p}"), warn = FALSE, encoding = "UTF-8")))',
    f"{PH}.smcl_chunks",
    {"path": p},
)
LATIN = (
    "latin1 bytes: R's regex functions reject the invalid UTF-8 strings readLines() returns "
    '("input string N is invalid UTF-8"), so metacheck cannot read a latin1 log at all; '
    "pytacheck decodes it with replacement characters instead of failing"
)
expr_case(
    "import_stata_smcl.latin1",
    f'tryCatch(metacheck::import_stata_smcl(rpath("{FX}/latin1.smcl")), error = function(e) "error")',
    f"{PH}.call",
    {"fn": "pytacheck.statout.stata.import_stata_smcl", "path": {"$file": f"{FX}/latin1.smcl"}},
    known_divergence=LATIN,
)
SMCL_LINES = [
    "{res:{it:x}} tail",
    "{ralign 8:abc}|{center 7:ab}|{center 6:ab}|{rcenter:ab}|{lalign 5:toolongtext}|{lalign 0:x}",
    "{col 5}a{col 3}b{col x}c{col 10.9}d{col  12}e",
    "{space 1e1}|{space 0x3}|{space 2.9}|{hline 2.5}|{hline 1e1}|{hline x}",
    "abc{.-}",
    "{c 0x41}{c 0x4}{char 65.5}{char -1}{char 0x41}{char 256}",
    "{dup 0:x}|{dup 2:ab}|{dup x:y}|{dup 2}",
    "{",
    "a{b",
    "}}",
    "{c |}{c +}{c -}{c TT}{c BT}{c LT}{c RT}{c TLC}{c TRC}{c BLC}{c BRC}{c ??}",
    "{help summarize:clicking}{bf:bold}{*comment}{p_end}",
    "\t{col 12}tab",
]
vec_case(
    "smcl_render_line.more",
    f"metacheck:::.smcl_render(c({', '.join(q(s) for s in SMCL_LINES)}))",
    f"pc.statout.stata._smcl_render([{', '.join(q(s) for s in SMCL_LINES)}])",
)
CHUNK_LINES = [". a", "  3. b", "> c", "out", ".  ", ". ", "x", "> d", ". e", "  12. f"]
vec_case(
    "smcl_command_chunks.vector",
    f"metacheck:::.smcl_command_chunks(c({', '.join(q(s) for s in CHUNK_LINES)}))",
    f"pc.statout.stata._smcl_command_chunks([{', '.join(q(s) for s in CHUNK_LINES)}])",
)
ONELINE = [
    "Iteration 0:   log likelihood = -45.03321",
    "chi2(2) = 5.21  Prob > chi2 = 0.0739",
    "R-squared = 0.25, Adj R-squared = 0.2, R-squared = 0.3",
    "F(1, 72) = 20.26 p < .001",
    "βeta = 1 ²x = 2 x² = 3",
    "a=1b=2",
    "N = 1e5, M = 2E5, d = -1.5e-3",
]
vec_case(
    "stata_output_oneline.more",
    f'metacheck:::.stata_output_oneline(c({", ".join(q(s) for s in ONELINE)}), "cmd")',
    f'pc.statout.stata._stata_output_oneline([{", ".join(q(s) for s in ONELINE)}], "cmd")',
)

# ---------------------------------------------------------------- Mplus
p = f"{FX}/tricky.out"
fn_case(
    "import_mplus_output.tricky",
    "import_mplus_output",
    "pytacheck.statout.mplus.import_mplus_output",
    {"path": {"$file": p}},
)
expr_case(
    "export_mplus_html.tricky",
    EXPORT_R.format(fn="export_mplus_html", p=p, post="x"),
    f"{PH}.export_lines",
    {"fn": "pytacheck.statout.mplus.export_mplus_html", "path": p},
)
expr_case(
    "mplus_export_syntax.tricky",
    SYNTAX_R.format(fn=".mplus_export_syntax", p=p),
    f"{PH}.export_syntax",
    {"fn": "pytacheck.statout.mplus._mplus_export_syntax", "path": p},
)
expr_case(
    "mplus_sections.tricky",
    f'metacheck:::.mplus_sections(readLines(rpath("{p}"), warn = FALSE, encoding = "UTF-8"))',
    f"{PH}.mplus_sections",
    {"path": p},
)
expr_case(
    "mplus_syntax_lines.tricky",
    f'metacheck:::.mplus_syntax_lines(readLines(rpath("{p}"), warn = FALSE, encoding = "UTF-8"))',
    f"{PH}.mplus_syntax",
    {"path": p},
)
SEC_R = (
    '{{secs <- metacheck:::.mplus_sections(readLines(rpath("{p}"), warn = FALSE, encoding = "UTF-8")); '
    'sec <- Filter(function(s) identical(s$title, "{t}"), secs)[[1]]; tabs <- metacheck:::.mplus_output_tables(sec$lines); '
    'cons <- attr(tabs, "consumed"); list(tables = lapply(tabs, function(t) t$data), consumed = cons, '
    'labelvalue = lapply(metacheck:::.mplus_output_labelvalue(sec$lines[!cons], "{t}"), function(b) b$data))}}'
)
for title in [
    "summary of analysis",
    "MODEL RESULTS",
    "UNIVARIATE HIGHER-ORDER MOMENT DESCRIPTIVE STATISTICS",
    "SAMPLE STATISTICS",
]:
    expr_case(
        f"mplus_output_tables.tricky.{title.lower().replace(' ', '_')[:24]}",
        SEC_R.format(p=p, t=title),
        f"{PH}.mplus_section_tables",
        {"path": p, "title": title},
    )
LV = [
    "Number of observations  2  45",
    "  P-Value 0.05",
    "  Loglikelihood  H0 Value   -1234.5",
    "  Estimate  0.100D-05",
    "  Estimate  0.100D05",
    "  Chi  1.5*",
    "  Chi  1.5**",
    "  a  -",
    "  a  1.",
    "  a  .5",
    "  a  1.2.3",
    "  (x)  1",
    "  A b  c  1",
    "  a\t\t1",
    "  a \t1",
    "Z  9  ",
]
vec_case(
    "mplus_output_labelvalue.vector",
    f'lapply(metacheck:::.mplus_output_labelvalue(c({", ".join(q(s) for s in LV)}, NA), "T"), function(b) b$data)',
    f'[b["data"] for b in pc.statout.mplus._mplus_output_labelvalue([{", ".join(q(s) for s in LV)}, None], "T")]',
)
expr_case(
    "import_mplus_output.latin1",
    f'tryCatch(metacheck::import_mplus_output(rpath("{FX}/latin1.out")), error = function(e) "error")',
    f"{PH}.call",
    {"fn": "pytacheck.statout.mplus.import_mplus_output", "path": {"$file": f"{FX}/latin1.out"}},
    known_divergence=LATIN,
)


def _str(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')


yaml.add_representer(str, _str)
out = Path(__file__).resolve().parents[2] / "parity" / "cases" / "statout_readers_review.yaml"
out.write_text(
    "# Review parity cases for the statistics-output readers (targeted branches).\n"
    "# Generated by tests/statout_readers/gen_review_cases.py.\n"
    + yaml.dump(
        {"area": "statout_readers_review", "cases": cases},
        allow_unicode=True,
        sort_keys=False,
        width=100000,
    ),
    encoding="utf-8",
)
print(f"{len(cases)} cases -> {out}")
