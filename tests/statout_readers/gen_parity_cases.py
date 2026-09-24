"""Generate ``parity/cases/statout_readers.yaml`` (every string quoted).

Run ``python tests/statout_readers/gen_parity_cases.py`` after changing the cases,
then ``python -m parity generate --area statout_readers``.
"""

import json
from pathlib import Path

import yaml

FX = "tests/statout_readers/fixtures"
UP = "upstream/metacheck/tests/testthat/fixtures/formats"
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


# ---------------------------------------------------------------- SPV
for name in ["modern", "legacy", "charts", "logs_only", "empty", "notzip"]:
    fn_case(
        f"import_spv.{name}",
        "import_spv",
        "pytacheck.statout.spv.import_spv",
        {"path": {"$file": f"{FX}/spv/{name}.spv"}},
    )
fn_case(
    "import_spv.missing",
    "import_spv",
    "pytacheck.statout.spv.import_spv",
    {"path": {"$file": f"{FX}/spv/does_not_exist.spv"}},
)
fn_case(
    "import_spv.wrong_ext",
    "import_spv",
    "pytacheck.statout.spv.import_spv",
    {"path": {"$file": f"{UP}/sample.jasp"}},
)

ATTR_R = (
    'lapply(metacheck::import_spv(rpath("{p}")), function(t) {{ a <- attributes(t$data); '
    "if (isTRUE(t$is_chart)) {{ fits <- a$spv_chart_fits %||% list(); "
    "list(type = a$spv_chart_type, title = a$spv_chart_title, xlab = a$spv_chart_xlab, "
    'ylab = a$spv_chart_ylab, fit_names = vapply(fits, function(f) as.character(f$name), ""), '
    'fit_exprs = vapply(fits, function(f) f$expr, "")) }} else '
    "list(title = a$spv_title, rows = a$spv_row_dims, cols = a$spv_col_dims, layers = a$spv_layer_dims, "
    'footnotes = vapply(a$spv_footnotes %||% list(), function(f) paste(f$text, f$marker, sep = "|"), "")) }})'
)
for name in ["modern", "legacy", "charts"]:
    expr_case(
        f"import_spv.attrs.{name}",
        ATTR_R.format(p=f"{FX}/spv/{name}.spv"),
        f"{PH}.spv_attrs",
        {"path": f"{FX}/spv/{name}.spv"},
    )

EXPORT_R = (
    '{{f <- tempfile(fileext = ".html"); metacheck::{fn}(rpath("{p}"), f); '
    "x <- readLines(f, warn = FALSE); {post}}}"
)
IMG = 'gsub("data:image/[a-z+]+;base64,[A-Za-z0-9+/=]+", "data:IMG", x)'
for name in ["modern", "legacy", "charts", "logs_only"]:
    images = name == "charts"
    expr_case(
        f"export_spv_html.{name}",
        EXPORT_R.format(
            fn="export_spv_html", p=f"{FX}/spv/{name}.spv", post=IMG if images else "x"
        ),
        f"{PH}.export_lines",
        {
            "fn": "pytacheck.statout.spv.export_spv_html",
            "path": f"{FX}/spv/{name}.spv",
            "images": images,
        },
    )
fn_case(
    "export_spv_html.notzip",
    "export_spv_html",
    "pytacheck.statout.spv.export_spv_html",
    {
        "path": {"$file": f"{FX}/spv/notzip.spv"},
        "out": {"$expr": {"r": "tempfile()", "py": "__import__('tempfile').mktemp()"}},
    },
)

SYNTAX_R = (
    '{{d <- tempfile(); dir.create(d); file.copy(rpath("{p}"), d); '
    'p <- metacheck:::{fn}(file.path(d, basename("{p}"))); '
    "if (is.na(p)) NULL else list(path = substring(p, nchar(d) + 1), lines = readLines(p, warn = FALSE))}}"
)
for name in ["modern", "legacy", "logs_only", "empty"]:
    expr_case(
        f"spv_export_syntax.{name}",
        SYNTAX_R.format(fn=".spv_export_syntax", p=f"{FX}/spv/{name}.spv"),
        f"{PH}.export_syntax",
        {"fn": "pytacheck.statout.spv._spv_export_syntax", "path": f"{FX}/spv/{name}.spv"},
    )

STRUCT_R = (
    '{{d <- tempfile(); dir.create(d); utils::unzip(rpath("{p}"), exdir = d); '
    "rows <- metacheck:::.spv_read_structure(d); if (!length(rows)) NULL else "
    "do.call(rbind, lapply(rows, function(r) as.data.frame(r, stringsAsFactors = FALSE)))}}"
)
for name in ["modern", "legacy", "charts", "logs_only"]:
    expr_case(
        f"spv_read_structure.{name}",
        STRUCT_R.format(p=f"{FX}/spv/{name}.spv"),
        f"{PH}.spv_structure",
        {"path": f"{FX}/spv/{name}.spv"},
    )

LEG_R = (
    '{{d <- tempfile(); dir.create(d); utils::unzip(rpath("{p}"), exdir = d); '
    'f <- file.path(d, "{m}"); src <- metacheck:::.spv_decode_legacy_data(readBin(f, "raw", file.size(f))); '
    "if (is.null(src)) NULL else do.call(rbind, lapply(src, function(s) do.call(rbind, lapply(s$variables, "
    "function(v) data.frame(source = s$source_name, var = v$var_name, k = seq_along(v$values), "
    'd = vapply(v$values, function(e) as.character(e$d), ""), s = vapply(v$values, function(e) as.character(e$s), ""), '
    "stringsAsFactors = FALSE)))))}}"
)
for name, member in [
    ("legacy", "0000000003_tableData.bin"),
    ("charts", "0000000003_chartData.bin"),
    ("legacy", "0000000005_tableData.bin"),
]:
    expr_case(
        f"spv_decode_legacy_data.{name}.{member[:10]}",
        LEG_R.format(p=f"{FX}/spv/{name}.spv", m=member),
        f"{PH}.legacy_data",
        {"path": f"{FX}/spv/{name}.spv", "member": member},
    )

LIGHT_R = (
    '{{d <- tempfile(); dir.create(d); utils::unzip(rpath("{p}"), exdir = d); '
    'f <- file.path(d, "{m}"); metacheck:::.spv_decode_light_table(readBin(f, "raw", file.size(f)))}}'
)
for member in [
    "0000000004_lightTableData.bin",
    "0000000005_lightTableData.bin",
    "0000000006_lightTableData.bin",
    "0000000012_lightTableData.bin",
    "0000000014_lightTableData.bin",
    "0000000015_lightTableData.bin",
]:
    expr_case(
        f"spv_decode_light_table.{member[:10]}",
        LIGHT_R.format(p=f"{FX}/spv/modern.spv", m=member),
        f"{PH}.light_table",
        {"path": f"{FX}/spv/modern.spv", "member": member},
    )

# spv_assemble_table called directly
ASM_R = (
    'metacheck:::spv_assemble_table(dims = list(list(name = "Stat", leaves = list("0" = "t", "1" = c("Grp", "df")), n_leaves = 2L), '
    'list(name = "", leaves = list("0" = "a"), n_leaves = 0L), list(name = "Stat", leaves = list(), n_leaves = 0L)), '
    "axes = list(layers = integer(0), rows = c(1L, 5L), columns = 0L), "
    'cells = list(list(index = 0, value = list(type = "numeric", x = 2.5)), list(index = 1, value = list(type = "string", s = "x")), '
    'list(index = 3, value = list(type = "numeric", x = NaN)), list(index = 2, value = list(type = "text")), '
    'list(index = 100000, value = list(type = "numeric", x = 1e15))), title = "T")'
)
ASM_PY = (
    'pc.statout.spv.spv_assemble_table(dims=[{"name": "Stat", "leaves": {"0": ["t"], "1": ["Grp", "df"]}, "n_leaves": 2}, '
    '{"name": "", "leaves": {"0": ["a"]}, "n_leaves": 0}, {"name": "Stat", "leaves": {}, "n_leaves": 0}], '
    'axes={"layers": [], "rows": [1, 5], "columns": [0]}, '
    'cells=[{"index": 0.0, "value": {"type": "numeric", "x": 2.5}}, {"index": 1.0, "value": {"type": "string", "s": "x"}}, '
    '{"index": 3.0, "value": {"type": "numeric", "x": float("nan")}}, {"index": 2.0, "value": {"type": "text"}}, '
    '{"index": 100000.0, "value": {"type": "numeric", "x": 1e15}}], title="T")'
)
add(
    id="spv_assemble_table.direct",
    r="identity",
    py="copy.copy",
    args={"x": {"$expr": {"r": ASM_R, "py": ASM_PY}}},
)
add(
    id="spv_assemble_table.empty",
    r="metacheck:::spv_assemble_table",
    py="pytacheck.statout.spv.spv_assemble_table",
    args={"dims": {"$list": []}, "axes": {"$list": []}, "cells": {"$list": []}},
)

DISPLAY = [
    "0.840583589880873",
    "397",
    "4.7e-108",
    "-0.0001",
    "1e20",
    "1e+05",
    "abc",
    "12.5",
    ".5",
    "1.",
    "-3",
    "+2.25",
    "1.2.3",
    "",
    "NA",
    "123456789012.25",
    "-0",
    "0.0005",
    "0.0004999",
]
add(
    id="spv_display_value.vector",
    r="identity",
    py="copy.copy",
    args={
        "x": {
            "$expr": {
                "r": f'c(vapply({{v <- c({", ".join(q(s) for s in DISPLAY)}, NA); v}}, metacheck:::.spv_display_value, "", USE.NAMES = FALSE), '
                "metacheck:::.spv_display_value(0.5), metacheck:::.spv_display_value(3), metacheck:::.spv_display_value(NaN))",
                "py": f'[pc.statout.spv._spv_display_value(v) for v in [{", ".join(q(s) for s in DISPLAY)}, None, 0.5, 3.0, float("nan")]]',
            }
        }
    },
)
add(
    id="spv_html_escape.vector",
    r="identity",
    py="copy.copy",
    args={
        "x": {
            "$expr": {
                "r": 'c(metacheck:::.spv_html_escape("a < b & c > d"), metacheck:::.spv_html_escape(NULL), metacheck:::.spv_html_escape(1.5))',
                "py": '[pc.statout.spv._spv_html_escape("a < b & c > d"), pc.statout.spv._spv_html_escape(None), pc.statout.spv._spv_html_escape(1.5)]',
            }
        }
    },
)

TABLE_HTML_R = (
    '{{df <- data.frame(G = c("g1", "g1", "g2", NA, "g2"), S = c("N", "Mean", "N", "N", "N"), '
    'L = c("x", "x", "y", "y", "z"), value = c("12", "3.14159", "4.7e-108", NA, "5"), stringsAsFactors = FALSE); '
    'attr(df, "spv_row_dims") <- {rows}; attr(df, "spv_col_dims") <- {cols}; metacheck:::.spv_table_html(df)}}'
)
TABLE_HTML_PY = (
    '(lambda df: (df.attrs.update({{"spv_row_dims": {rows}, "spv_col_dims": {cols}}}), pc.statout.spv._spv_table_html(df))[1])'
    '(pd.DataFrame({{"G": pd.array(["g1", "g1", "g2", None, "g2"], dtype="string"), "S": pd.array(["N", "Mean", "N", "N", "N"], dtype="string"), '
    '"L": pd.array(["x", "x", "y", "y", "z"], dtype="string"), "value": pd.array(["12", "3.14159", "4.7e-108", None, "5"], dtype="string")}}))'
)
for tag, rows_r, cols_r, rows_py, cols_py in [
    ("pivot", 'c("G")', 'c("S")', '["G"]', '["S"]'),
    ("pivot2", 'c("G", "L")', 'c("S")', '["G", "L"]', '["S"]'),
    ("colsonly", "character(0)", 'c("S", "G")', "[]", '["S", "G"]'),
    ("rowsonly", 'c("S")', "character(0)", '["S"]', "[]"),
    ("flat", 'c("G", "S", "L")', 'c("value")', '["G", "S", "L"]', '["value"]'),
    ("unknown", 'c("nope")', "NULL", '["nope"]', "None"),
]:
    add(
        id=f"spv_table_html.{tag}",
        r="identity",
        py="copy.copy",
        args={
            "x": {
                "$expr": {
                    "r": TABLE_HTML_R.format(rows=rows_r, cols=cols_r),
                    "py": TABLE_HTML_PY.format(rows=rows_py, cols=cols_py),
                }
            }
        },
    )
# the col key with a trailing empty label: R's strsplit() drops it -> subscript error
add(
    id="spv_table_html.trailing_empty",
    r="identity",
    py="copy.copy",
    args={
        "x": {
            "$expr": {
                "r": '{df <- data.frame(A = c("a", "b"), B = c("", "x"), value = c("1", "2"), stringsAsFactors = FALSE); '
                'attr(df, "spv_col_dims") <- c("A", "B"); metacheck:::.spv_table_html(df)}',
                "py": '(lambda df: (df.attrs.update({"spv_col_dims": ["A", "B"]}), pc.statout.spv._spv_table_html(df))[1])'
                '(pd.DataFrame({"A": pd.array(["a", "b"], dtype="string"), "B": pd.array(["", "x"], dtype="string"), '
                '"value": pd.array(["1", "2"], dtype="string")}))',
            }
        }
    },
)

# ---------------------------------------------------------------- JASP
for id_, p in [
    ("sample", f"{UP}/sample.jasp"),
    ("sqlite", f"{FX}/archives/sqlite.jasp"),
    ("binary", f"{FX}/archives/binary.jasp"),
]:
    fn_case(
        f"import_jasp.{id_}",
        "import_jasp",
        "pytacheck.statout.jasp.import_jasp",
        {"path": {"$file": p}},
    )
    LAB_R = (
        f'lapply(metacheck::import_jasp(rpath("{p}"))$data, function(col) list(label = {{l <- attr(col, "label", exact = TRUE); if (is.numeric(l)) list(codes = unname(l), labels = names(l)) else l}}, '
        'codes = unname(attr(col, "labels")), labels = names(attr(col, "labels"))))'
    )
    expr_case(
        f"import_jasp.labels.{id_}",
        LAB_R,
        f"{PH}.data_labels",
        {"fn": "pytacheck.statout.jasp.import_jasp", "path": p},
    )
for id_, p in [
    ("notzip", f"{FX}/archives/notzip.jasp"),
    ("nodata", f"{FX}/archives/nodata.jasp"),
    ("wrong_ext", f"{FX}/spv/modern.spv"),
    ("missing", f"{FX}/archives/none.jasp"),
]:
    fn_case(
        f"import_jasp.{id_}",
        "import_jasp",
        "pytacheck.statout.jasp.import_jasp",
        {"path": {"$file": p}},
    )
expr_case(
    "jasp_analyses_summary.sample",
    f'metacheck:::.jasp_analyses_summary(metacheck::import_jasp(rpath("{UP}/sample.jasp"))$analyses)',
    f"{PH}.jasp_summary",
    {"path": f"{UP}/sample.jasp"},
)
SUMMARY_CASES = [
    ("null", "NULL", "None"),
    (
        "nested",
        'list(analyses = list(list(title = "T-Test", module = "jasp"), list(name = "anova"), list(), list(title = 3)))',
        '{"analyses": [{"title": "T-Test", "module": "jasp"}, {"name": "anova"}, {}, {"title": 3}]}',
    ),
    (
        "flat",
        'list(list(titles = "partial", mod = "m"), "plain", 2.5, TRUE)',
        '[{"titles": "partial", "mod": "m"}, "plain", 2.5, True]',
    ),
    ("empty", "list(analyses = list())", '{"analyses": []}'),
]
for tag, r_val, py_val in SUMMARY_CASES:
    add(
        id=f"jasp_analyses_summary.{tag}",
        r="identity",
        py="copy.copy",
        args={
            "x": {
                "$expr": {
                    "r": f"metacheck:::.jasp_analyses_summary({r_val})",
                    "py": f"pc.statout.jasp._jasp_analyses_summary({py_val})",
                }
            }
        },
    )
LABELS_R = (
    '{l <- metacheck:::.jasp_binary_labels(list(name = "g", labels = list()), list(g = list(labels = '
    'list(list(1, "a"), list("2", "b"), list("x", "c"), list(4, ""), list(5.5, "e"))))); '
    "list(codes = unname(l), labels = names(l))}"
)
LABELS_PY = (
    'pc.statout.jasp._jasp_binary_labels({"name": "g", "labels": []}, {"g": {"labels": '
    '[[1, "a"], ["2", "b"], ["x", "c"], [4, ""], [5.5, "e"]]}})'
)
add(
    id="jasp_binary_labels.direct",
    r="identity",
    py=f"{PH}.labels_pairs",
    args={"x": {"$expr": {"r": LABELS_R, "py": "None"}}},
    py_drop=["x"],
    py_args={"labs": {"$expr": {"r": "NULL", "py": LABELS_PY}}},
)
for id_, p in [("sample", f"{UP}/sample.jasp"), ("binary", f"{FX}/archives/binary.jasp")]:
    expr_case(
        f"export_jasp_html.{id_}",
        EXPORT_R.format(fn="export_jasp_html", p=p, post="x"),
        f"{PH}.export_lines",
        {"fn": "pytacheck.statout.jasp.export_jasp_html", "path": p},
    )
fn_case(
    "export_jasp_html.noindex",
    "export_jasp_html",
    "pytacheck.statout.jasp.export_jasp_html",
    {
        "path": {"$file": f"{FX}/archives/sqlite.jasp"},
        "out": {"$expr": {"r": "tempfile()", "py": "__import__('tempfile').mktemp()"}},
    },
)

# ---------------------------------------------------------------- OMV
for id_, p in [("sample", f"{UP}/sample.omv"), ("fixture", f"{FX}/archives/fixture.omv")]:
    fn_case(
        f"import_omv.{id_}",
        "import_omv",
        "pytacheck.statout.omv.import_omv",
        {"path": {"$file": p}},
    )
    LAB_R = (
        f'lapply(metacheck::import_omv(rpath("{p}"))$data, function(col) list(label = {{l <- attr(col, "label", exact = TRUE); if (is.numeric(l)) list(codes = unname(l), labels = names(l)) else l}}, '
        'codes = unname(attr(col, "labels")), labels = names(attr(col, "labels"))))'
    )
    expr_case(
        f"import_omv.labels.{id_}",
        LAB_R,
        f"{PH}.data_labels",
        {"fn": "pytacheck.statout.omv.import_omv", "path": p},
    )
    expr_case(
        f"export_omv_html.{id_}",
        EXPORT_R.format(fn="export_omv_html", p=p, post="x"),
        f"{PH}.export_lines",
        {"fn": "pytacheck.statout.omv.export_omv_html", "path": p},
    )
for id_, p in [
    ("notzip", f"{FX}/archives/notzip.omv"),
    ("nodata", f"{FX}/archives/nodata.omv"),
    ("wrong_ext", f"{UP}/sample.jasp"),
    ("missing", f"{FX}/archives/none.omv"),
]:
    fn_case(
        f"import_omv.{id_}",
        "import_omv",
        "pytacheck.statout.omv.import_omv",
        {"path": {"$file": p}},
    )
OMV_SYNTAX = [
    "R\x01jmv::ttestIS(vars = vars(score), group = grp)",
    "xx pkg.name::fn_1 (a, (b), c) trailing ) more",
    "no call here",
    "::bad(",
    "a::b(unbalanced",
    "  jmv::anova(\t formula =   y ~ x,\n data = d)  ",
]
add(
    id="omv_extract_syntax.vector",
    r="identity",
    py="copy.copy",
    args={
        "x": {
            "$expr": {
                "r": f'vapply(c({", ".join(q(s) for s in OMV_SYNTAX)}), metacheck:::.omv_extract_syntax, "", USE.NAMES = FALSE)',
                "py": f"[pc.statout.omv._omv_extract_syntax(s) for s in [{', '.join(q(s) for s in OMV_SYNTAX)}]]",
            }
        }
    },
)

# ---------------------------------------------------------------- Stata
for name in ["analysis", "nocommands"]:
    fn_case(
        f"import_stata_smcl.{name}",
        "import_stata_smcl",
        "pytacheck.statout.stata.import_stata_smcl",
        {"path": {"$file": f"{FX}/text/{name}.smcl"}},
    )
    expr_case(
        f"export_stata_smcl_html.{name}",
        EXPORT_R.format(fn="export_stata_smcl_html", p=f"{FX}/text/{name}.smcl", post="x"),
        f"{PH}.export_lines",
        {"fn": "pytacheck.statout.stata.export_stata_smcl_html", "path": f"{FX}/text/{name}.smcl"},
    )
    expr_case(
        f"smcl_export_syntax.{name}",
        SYNTAX_R.format(fn=".smcl_export_syntax", p=f"{FX}/text/{name}.smcl"),
        f"{PH}.export_syntax",
        {"fn": "pytacheck.statout.stata._smcl_export_syntax", "path": f"{FX}/text/{name}.smcl"},
    )
fn_case(
    "import_stata_smcl.missing",
    "import_stata_smcl",
    "pytacheck.statout.stata.import_stata_smcl",
    {"path": {"$file": f"{FX}/text/none.smcl"}},
)
fn_case(
    "import_stata_smcl.wrong_ext",
    "import_stata_smcl",
    "pytacheck.statout.stata.import_stata_smcl",
    {"path": {"$file": f"{FX}/text/twolevel.out"}},
)
expr_case(
    "smcl_render.analysis",
    f'metacheck:::.smcl_render(readLines(rpath("{FX}/text/analysis.smcl"), warn = FALSE, encoding = "UTF-8"))',
    f"{PH}.smcl_render_file",
    {"path": f"{FX}/text/analysis.smcl"},
)
expr_case(
    "smcl_command_chunks.analysis",
    f'metacheck:::.smcl_command_chunks(metacheck:::.smcl_render(readLines(rpath("{FX}/text/analysis.smcl"), warn = FALSE, encoding = "UTF-8")))',
    f"{PH}.smcl_chunks",
    {"path": f"{FX}/text/analysis.smcl"},
)
SMCL_LINES = [
    "{txt}{space 7}price {c |}{res}{col 20}     74    6165.257{hline 5}{c +}{hline}",
    "a{dup 3:ab}b{char 65}{c 0x41}{ralign 8:xy}|{center 7:ab}|{lalign 5:z}|{res:-1.5}{it:x}{* comment}{bf}{.-}",
    "{col abc}x{hline 10}{space -3}{col 2}",
    "{dup 99999999999:a}q{res:x}{space 2}{char 66}{.-}{ralign 4:y}",
    "unmatched { brace {res:{bf:x}} y",
    "{c TLC}{c TT}{c TRC}{c LT}{c RT}{c BLC}{c BT}{c BRC}{c -(}{c )-}{c S|}",
    "{rcenter:mid}{center 2:long text}{ralign 3:}{lalign:q}{char 0}{char 300}{hline -2}",
    "",
    "no markup at all",
]
add(
    id="smcl_render_line.vector",
    r="identity",
    py="copy.copy",
    args={
        "x": {
            "$expr": {
                "r": f"metacheck:::.smcl_render(c({', '.join(q(s) for s in SMCL_LINES)}))",
                "py": f"pc.statout.stata._smcl_render([{', '.join(q(s) for s in SMCL_LINES)}])",
            }
        }
    },
)
for tag, line in [
    ("na_col", "{dup 99999999999:a}q{col 2}"),
    ("na_hline", "{dup 99999999999:a}{hline}"),
]:
    add(
        id=f"smcl_render_line.{tag}",
        r="identity",
        py="copy.copy",
        args={
            "x": {
                "$expr": {
                    "r": f"metacheck:::.smcl_render_line({q(line)})",
                    "py": f"pc.statout.stata._smcl_render_line({q(line)})",
                }
            }
        },
    )
NUMLIKE = [
    "12",
    "-3.5",
    "1,234",
    "1e5",
    "1E-3",
    "< .001",
    ">0.5",
    "Inf",
    "-inf",
    "NA",
    "nan",
    ".",
    "abc",
    "",
    " 7 ",
    "1.2.3",
    "(1)",
]
add(
    id="stata_is_numlike.vector",
    r="identity",
    py="copy.copy",
    args={
        "x": {
            "$expr": {
                "r": f"metacheck:::.stata_is_numlike(c({', '.join(q(s) for s in NUMLIKE)}, NA))",
                "py": f"pc.statout.stata._stata_is_numlike([{', '.join(q(s) for s in NUMLIKE)}, None])",
            }
        }
    },
)
BLOCK = ["  Name   | Obs  Mean", "\tx | 12  3.5", "long_name|  7  -0.25", "", "   z   "]
add(
    id="stata_split_block.vector",
    r="identity",
    py="copy.copy",
    args={
        "x": {
            "$expr": {
                "r": f"metacheck:::.stata_split_block(c({', '.join(q(s) for s in BLOCK)}))",
                "py": f"pc.statout.stata._stata_split_block([{', '.join(q(s) for s in BLOCK)}])",
            }
        }
    },
)
add(
    id="stata_split_block.blank",
    r="identity",
    py="copy.copy",
    args={
        "x": {
            "$expr": {
                "r": 'metacheck:::.stata_split_block(c("   ", " "))',
                "py": 'pc.statout.stata._stata_split_block(["   ", " "])',
            }
        }
    },
)
ONELINE = [
    "Iteration 2: log pseudolikelihood = -640.68206  chi2(2) = 12.5, Prob > chi2 = 0.0021",
    "nothing to see",
    "R2 = .25 and R2 = 0.5",
    "t(12) < .001 p = 3e-4",
]
add(
    id="stata_output_oneline.vector",
    r="identity",
    py="copy.copy",
    args={
        "x": {
            "$expr": {
                "r": f'metacheck:::.stata_output_oneline(c({", ".join(q(s) for s in ONELINE)}), "cmd")',
                "py": f'pc.statout.stata._stata_output_oneline([{", ".join(q(s) for s in ONELINE)}], "cmd")',
            }
        }
    },
)
TABLE_LINES = [
    "",
    "  Group |  Mean   SD",
    "--------+------------",
    "  a     |  1.5   0.2",
    "  b     |  2.5   0.3",
    "--------+------------",
    "text only",
    "-------",
    "no header above",
    "",
    "-----",
    "   x   y",
    "   1   2",
    "  words | more",
    "--------+----",
    "  label | text",
    "",
    "Head",
    "---",
    "only |",
    "--",
]
add(
    id="stata_output_tables.vector",
    r="identity",
    py="copy.copy",
    args={
        "x": {
            "$expr": {
                "r": f"metacheck:::.stata_output_tables(c({', '.join(q(s) for s in TABLE_LINES)}))",
                "py": f"pc.statout.stata._stata_output_tables([{', '.join(q(s) for s in TABLE_LINES)}])",
            }
        }
    },
)
RULES = ["-----", "  ---+---  ", "───", "--┬--┴", "", "   ", "-- x", "+", "|---"]
add(
    id="stata_is_rule_line.vector",
    r="identity",
    py="copy.copy",
    args={
        "x": {
            "$expr": {
                "r": f"vapply(c({', '.join(q(s) for s in RULES)}), metacheck:::.stata_is_rule_line, TRUE, USE.NAMES = FALSE)",
                "py": f"[pc.statout.stata._stata_is_rule_line(s) for s in [{', '.join(q(s) for s in RULES)}]]",
            }
        }
    },
)

# ---------------------------------------------------------------- Mplus
for name in ["twolevel", "adjacent", "nosections", "compiler"]:
    fn_case(
        f"import_mplus_output.{name}",
        "import_mplus_output",
        "pytacheck.statout.mplus.import_mplus_output",
        {"path": {"$file": f"{FX}/text/{name}.out"}},
    )
fn_case(
    "import_mplus_output.wrong_ext",
    "import_mplus_output",
    "pytacheck.statout.mplus.import_mplus_output",
    {"path": {"$file": f"{FX}/text/analysis.smcl"}},
)
fn_case(
    "import_mplus_output.missing",
    "import_mplus_output",
    "pytacheck.statout.mplus.import_mplus_output",
    {"path": {"$file": f"{FX}/text/none.out"}},
)
for name in ["twolevel", "adjacent", "nosections"]:
    expr_case(
        f"export_mplus_html.{name}",
        EXPORT_R.format(fn="export_mplus_html", p=f"{FX}/text/{name}.out", post="x"),
        f"{PH}.export_lines",
        {"fn": "pytacheck.statout.mplus.export_mplus_html", "path": f"{FX}/text/{name}.out"},
    )
for name in ["twolevel", "adjacent", "nosections", "compiler"]:
    expr_case(
        f"mplus_export_syntax.{name}",
        SYNTAX_R.format(fn=".mplus_export_syntax", p=f"{FX}/text/{name}.out"),
        f"{PH}.export_syntax",
        {"fn": "pytacheck.statout.mplus._mplus_export_syntax", "path": f"{FX}/text/{name}.out"},
    )
for name in ["twolevel", "adjacent"]:
    expr_case(
        f"mplus_sections.{name}",
        f'metacheck:::.mplus_sections(readLines(rpath("{FX}/text/{name}.out"), warn = FALSE, encoding = "UTF-8"))',
        f"{PH}.mplus_sections",
        {"path": f"{FX}/text/{name}.out"},
    )
    expr_case(
        f"mplus_syntax_lines.{name}",
        f'metacheck:::.mplus_syntax_lines(readLines(rpath("{FX}/text/{name}.out"), warn = FALSE, encoding = "UTF-8"))',
        f"{PH}.mplus_syntax",
        {"path": f"{FX}/text/{name}.out"},
    )
SEC_R = (
    '{{secs <- metacheck:::.mplus_sections(readLines(rpath("{p}"), warn = FALSE, encoding = "UTF-8")); '
    'sec <- Filter(function(s) identical(s$title, "{t}"), secs)[[1]]; tabs <- metacheck:::.mplus_output_tables(sec$lines); '
    'cons <- attr(tabs, "consumed"); list(tables = lapply(tabs, function(t) t$data), consumed = cons, '
    'labelvalue = lapply(metacheck:::.mplus_output_labelvalue(sec$lines[!cons], "{t}"), function(b) b$data))}}'
)
for title in [
    "SUMMARY OF ANALYSIS",
    "UNIVARIATE SAMPLE STATISTICS",
    "MODEL FIT INFORMATION",
    "MODEL RESULTS",
    "STANDARDIZED MODEL RESULTS",
    "R-SQUARE",
    "SAMPLE STATISTICS",
    "TECHNICAL 1 OUTPUT",
    "MODEL MODIFICATION INDICES",
]:
    tag = title.lower().replace(" ", "_").replace("-", "_")
    expr_case(
        f"mplus_output_tables.{tag}",
        SEC_R.format(p=f"{FX}/text/twolevel.out", t=title),
        f"{PH}.mplus_section_tables",
        {"path": f"{FX}/text/twolevel.out", "title": title},
    )
fn_case(
    "mplus_is_genuine_output.yes",
    "metacheck:::.mplus_is_genuine_output",
    "pytacheck.statout.mplus._mplus_is_genuine_output",
    {"path": {"$file": f"{FX}/text/twolevel.out"}},
)
fn_case(
    "mplus_is_genuine_output.no",
    "metacheck:::.mplus_is_genuine_output",
    "pytacheck.statout.mplus._mplus_is_genuine_output",
    {"path": {"$file": f"{FX}/text/compiler.out"}},
)
fn_case(
    "mplus_is_genuine_output.missing",
    "metacheck:::.mplus_is_genuine_output",
    "pytacheck.statout.mplus._mplus_is_genuine_output",
    {"path": {"$file": f"{FX}/text/none.out"}},
)
MP_LINES = [
    "",
    "   Observed   Variable   Estimate",
    "   Y   0.5  0.1",
    "",
    "Within Level",
    "   X   1.5  0.2",
    "   Observed",
    "  Expected    Observed  Percentile",
    "  0.1   0.2   0.3",
    "  Estimate   S.E.",
    "  Two-Tailed P-Value",
    "Group G1",
    "  a  1  2",
    "",
    "",
    "  b  3  4",
]
add(
    id="mplus_line_predicates.vector",
    r="identity",
    py="copy.copy",
    args={
        "x": {
            "$expr": {
                "r": f"{{x <- c({', '.join(q(s) for s in MP_LINES)}, NA); list(stat = vapply(x, metacheck:::.mplus_is_stat_header_line, TRUE, USE.NAMES = FALSE), "
                "looks = vapply(x, metacheck:::.mplus_looks_header, TRUE, USE.NAMES = FALSE), "
                "level = vapply(x, metacheck:::.mplus_is_level_label, TRUE, USE.NAMES = FALSE), "
                "group = vapply(x, metacheck:::.mplus_is_group_label, TRUE, USE.NAMES = FALSE), "
                "stats = vapply(x, metacheck:::.mplus_is_stats_header_line, TRUE, USE.NAMES = FALSE), "
                "section = vapply(x, metacheck:::.mplus_is_section_header, TRUE, USE.NAMES = FALSE), "
                "numlike = metacheck:::.mplus_is_numlike(x))}",
                "py": f'(lambda x: {{"stat": [pc.statout.mplus._mplus_is_stat_header_line(v) for v in x], '
                '"looks": [pc.statout.mplus._mplus_looks_header(v) for v in x], '
                '"level": [pc.statout.mplus._mplus_is_level_label(v) for v in x], '
                '"group": [pc.statout.mplus._mplus_is_group_label(v) for v in x], '
                '"stats": [pc.statout.mplus._mplus_is_stats_header_line(v) for v in x], '
                '"section": [pc.statout.mplus._mplus_is_section_header(v) for v in x], '
                f'"numlike": pc.statout.mplus._mplus_is_numlike(x)}})([{", ".join(q(s) for s in MP_LINES)}, None])',
            }
        }
    },
)
add(
    id="mplus_output_tables.synthetic",
    r="identity",
    py="copy.copy",
    args={
        "x": {
            "$expr": {
                "r": f'{{t <- metacheck:::.mplus_output_tables(c({", ".join(q(s) for s in MP_LINES)})); list(tables = lapply(t, function(b) b$data), consumed = attr(t, "consumed"))}}',
                "py": f'(lambda t: {{"tables": [b["data"] for b in t], "consumed": t.consumed}})(pc.statout.mplus._mplus_output_tables([{", ".join(q(s) for s in MP_LINES)}]))',
            }
        }
    },
)


class QuotedDumper(yaml.SafeDumper):
    pass


def _str(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')


QuotedDumper.add_representer(str, _str)
out = {"area": "statout_readers", "cases": cases}
with open(Path(__file__).resolve().parents[2] / "parity/cases/statout_readers.yaml", "w") as fh:
    fh.write(
        "# Parity cases for the statistics-output readers (R/spv.R, R/jasp.R, R/omv.R,\n"
        "# R/stata.R, R/mplus.R). Generated by tests/statout_readers/gen_parity_cases.py.\n"
    )
    yaml.dump(out, fh, Dumper=QuotedDumper, sort_keys=False, allow_unicode=True, width=10000)
print(len(cases))
