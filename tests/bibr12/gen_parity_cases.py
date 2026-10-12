"""Generate parity/cases/bibr12.yaml (the bibr export schema 12.x cases).

    python tests/bibr12/gen_parity_cases.py
    python -m parity generate --area bibr12

Each case's R side is metacheck code; ``$expr`` cases pair inline R with the
helpers in tests/bibr12/_parity_helpers.py.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
F12 = "upstream/metacheck/tests/testthat/fixtures/bibr12"
DEMO = "upstream/metacheck/inst/demos/to_err_is_human.json"
FIX = {
    "PMC4383902": f"{F12}/PMC4383902.json",
    "full": f"{F12}/full.json",
    "preprint": f"{F12}/preprint.json",
    "probe_docx": f"{F12}/probe_docx.json",
    "probe_html": f"{F12}/probe_html.json",
    "pyfull": "tests/fixtures/bibr_v12_full.json",
    "inspect": "tests/fixtures/bibr_v12_inspect.json",
}
# a paper list without duplicate paper_ids
LIST = [FIX["PMC4383902"], FIX["full"], FIX["preprint"], FIX["probe_html"], FIX["inspect"]]
MIXED = [DEMO, FIX["full"], FIX["preprint"]]
LIST_SMALL = [FIX["full"], FIX["probe_html"], FIX["inspect"]]
TABLES = [
    "info",
    "author",
    "text",
    "section",
    "url",
    "bib",
    "xref",
    "figure",
    "table",
    "eq",
    "affiliation",
    "funding",
    "footnote",
    "info_match",
    "affiliation_match",
    "funding_match",
    "bib_match",
]
H = "__import__('tests.bibr12._parity_helpers', fromlist=['x'])"
# an existing temporary directory (paper_write()'s normalizePath() warns, with the
# random path, about one that does not exist yet, which would make goldens vary)
SAVE_DIR_R = "local({d <- tempfile('pc_bibr12_'); dir.create(d); d})"
B = "__import__('metacheck.io.bibr12', fromlist=['x'])"

cases = []
comments = {}


def add(case, comment=None):
    if comment:
        comments[case["id"]] = comment
    cases.append(case)


def expr(r, py):
    return {"$expr": {"r": r, "py": py}}


def rread(path, images=False):
    return f"read('{path}'{', include_images = TRUE' if images else ''})"


def pyread(path, images=False):
    return f"pc.read({H}._path('{path}'){', include_images=True' if images else ''})"


def r_lines(paper_r, file_name=None):
    fn = "NULL" if file_name is None else f"'{file_name}'"
    return (
        "local({f <- paper_write(" + paper_r + ", " + fn + ", save_path = " + SAVE_DIR_R + ", "
        "schema_version = '12.0'); x <- readLines(f, encoding = 'UTF-8'); "
        "i <- which(trimws(x) == '\"converter\": {'); x[i + 1] <- '<converter name>'; "
        "x[i + 2] <- '<converter version>'; x})"
    )


def py_lines(paper_py, file_name=None):
    return f"{H}.written_lines({paper_py}, {file_name!r})"


def r_roundtrip(paper_r, file_name=None):
    fn = "NULL" if file_name is None else f"'{file_name}'"
    return (
        "read(paper_write(" + paper_r + ", " + fn + ", save_path = " + SAVE_DIR_R + ", "
        "schema_version = '12.0'))"
    )


def py_roundtrip(paper_py, file_name=None):
    return f"{H}.write_read({paper_py}, {file_name!r})"


def identity(case_id, r, py, **extra):
    c = {
        "id": case_id,
        "r": "identity",
        "py": "tests.bibr12._parity_helpers.identity",
        "args": {"x": expr(r, py)},
    }
    c.update(extra)
    return c


# ---------------------------------------------------------------------------
# read() of every fixture: the whole paper object
first = True
for name, path in FIX.items():
    add(
        {
            "id": f"read.{name}",
            "r": "read",
            "py": "metacheck.read",
            "args": {"file_path": {"$file": path}},
        },
        "read(): every 12.x fixture, the whole paper object" if first else None,
    )
    first = False
add(
    {
        "id": "read.probe_docx.images",
        "r": "read",
        "py": "metacheck.read",
        "args": {"file_path": {"$file": FIX["probe_docx"]}, "include_images": True},
    }
)
add(
    {
        "id": ".read_bibr12.probe_html",
        "r": "metacheck:::.read_bibr12",
        "py": "metacheck.io.bibr12.read_bibr12",
        "args": {"file_path": {"$file": FIX["probe_html"]}},
    }
)
add(
    {
        "id": ".read_bibr.full",
        "r": "metacheck:::.read_bibr",
        "py": "metacheck.papers.io.read_bibr",
        "args": {"file_path": {"$file": FIX["full"]}},
    }
)
add(
    {
        "id": "read.list",
        "r": "read",
        "py": "metacheck.read",
        "args": {
            "file_path": expr(
                "c(" + ", ".join(f"'{p}'" for p in LIST_SMALL) + ")",
                "[" + ", ".join(f"{H}._path('{p}')" for p in LIST_SMALL) + "]",
            )
        },
    }
)

# versions: a later 12.x reads, other versions stop
add(
    {
        "id": "read.later_12x",
        "r": "read",
        "py": "metacheck.read",
        "args": {
            "file_path": expr(
                "local({json <- jsonlite::read_json('"
                + FIX["probe_html"]
                + "'); json$schema_version <- '12.1'; "
                "json$new_table <- list(list(new_id = 1L)); json$metadata$new_field <- 'new'; "
                "json$text[[1]]$new_field <- 'new'; json$extraction$new_block <- list(new_field = 'new'); "
                "path <- file.path(tempfile('pc_bibr12_'), 'probe_html.json'); dir.create(dirname(path)); "
                "jsonlite::write_json(json, path, auto_unbox = TRUE, null = 'null', digits = NA); path})",
                f"{H}.later_12x('{FIX['probe_html']}')",
            )
        },
    },
    "schema versions: a later 12.x reads (unknown keys ignored); 11.x, 13.x and non-12 versions stop",
)
for tag, ver_r, ver_py in [
    ("v11", "'11.0'", "'11.0'"),
    ("v13", "'13.0'", "'13.0'"),
    ("v12_number", "12", "12"),
    ("v10_root", "'10.3'", "'10.3'"),
]:
    add(
        {
            "id": f".read_bibr.error.{tag}",
            "r": "metacheck:::.read_bibr",
            "py": "metacheck.papers.io.read_bibr",
            "args": {
                "file_path": expr(
                    "local({json <- jsonlite::read_json('" + FIX["probe_html"] + "'); "
                    f"json$schema_version <- {ver_r}; "
                    "path <- file.path(tempfile('pc_bibr12_'), 'v.json'); dir.create(dirname(path)); "
                    "jsonlite::write_json(json, path, auto_unbox = TRUE, null = 'null', digits = NA); path})",
                    f"{H}.json_variant('{FIX['probe_html']}', 'v.json', schema_version={ver_py})",
                )
            },
        }
    )
add(
    {
        "id": "read.dir.v11_and_v12",
        "r": "read",
        "py": "metacheck.read",
        "args": {
            "file_path": expr(
                "local({d <- tempfile('pc_bibr12_'); dir.create(d); json <- jsonlite::read_json('"
                + FIX["probe_html"]
                + "'); jsonlite::write_json(json, file.path(d, 'a_v12.json'), "
                "auto_unbox = TRUE, null = 'null', digits = NA); json$schema_version <- '11.0'; "
                "jsonlite::write_json(json, file.path(d, 'b_v11.json'), auto_unbox = TRUE, null = 'null', "
                "digits = NA); d})",
                f"{H}.mixed_dir()",
            )
        },
        "known_divergence": {
            "kind": "deliberate",
            "ref": "D6",
            "reason": (
                "metacheck's read() of several files fails as soon as one cannot be read "
                "(U18, U21: read() returns NULL for it and paperlist() rejects the NULL), so a "
                "directory holding one bibr 11.x file cannot be read at all; pytacheck logs and "
                "skips the file and returns the 12.0 paper"
            ),
        },
    },
    "read() of a directory with a bibr 11.0 file and a 12.0 file",
)

# ---------------------------------------------------------------------------
# paper_table() of every table
first = True
for tbl in TABLES:
    add(
        {
            "id": f"paper_table.list.{tbl}",
            "r": "paper_table",
            "py": "metacheck.paper_table",
            "args": {"paper": {"$read": LIST}, "table": tbl},
        },
        "paper_table() of every table, over a list of 12.x papers" if first else None,
    )
    first = False
first = True
for tbl in TABLES:
    add(
        {
            "id": f"paper_table.probe_docx.{tbl}",
            "r": "paper_table",
            "py": "metacheck.paper_table",
            "args": {"paper": {"$paper": FIX["probe_docx"]}, "table": tbl},
        },
        "... of a single paper (images dropped)" if first else None,
    )
    first = False
first = True
for tbl in [
    "info",
    "author",
    "text",
    "section",
    "url",
    "bib",
    "xref",
    "figure",
    "table",
    "eq",
    "bib_match",
    "info_match",
    "footnote",
]:
    add(
        {
            "id": f"paper_table.mixed.{tbl}",
            "r": "paper_table",
            "py": "metacheck.paper_table",
            "args": {"paper": {"$read": MIXED}, "table": tbl},
        },
        "... of a list mixing an older paper (the demo) and 12.x papers" if first else None,
    )
    first = False
add(
    {
        "id": "paper_table.list.xref.cols",
        "r": "paper_table",
        "py": "metacheck.paper_table",
        "args": {
            "paper": {"$read": LIST},
            "table": "xref",
            "cols": {"$chr": ["xref_id", "target_id", "xref_type"]},
        },
    }
)

# ---------------------------------------------------------------------------
# validation, reference table, text search
first = True
for name, path in FIX.items():
    add(
        {
            "id": f"paper_validate.{name}",
            "r": "paper_validate",
            "py": "metacheck.paper_validate",
            "args": {"paper": {"$paper": path}},
        },
        "paper_validate() and ref_table()" if first else None,
    )
    first = False
add(
    {
        "id": "paper_validate.demo",
        "r": "paper_validate",
        "py": "metacheck.paper_validate",
        "args": {"paper": {"$paper": "demo"}},
    }
)
for name, path in FIX.items():
    add(
        {
            "id": f"ref_table.{name}",
            "r": "ref_table",
            "py": "metacheck.ref_table",
            "args": {"paper": {"$paper": path}},
        }
    )
add(
    {
        "id": "ref_table.list",
        "r": "ref_table",
        "py": "metacheck.ref_table",
        "args": {"paper": {"$read": LIST}},
    }
)
add(
    {
        "id": "ref_table.mixed",
        "r": "ref_table",
        "py": "metacheck.ref_table",
        "args": {"paper": {"$read": MIXED}},
    }
)
add(
    {
        "id": "text_search.PMC4383902.footnote",
        "r": "text_search",
        "py": "metacheck.text_search",
        "args": {
            "paper": {"$paper": FIX["PMC4383902"]},
            "pattern": "list of authors of the Europe PMC Consortium",
        },
    },
    "captions and footnotes are text rows, so text_search() finds them",
)
add(
    {
        "id": "text_search.full.caption",
        "r": "text_search",
        "py": "metacheck.text_search",
        "args": {"paper": {"$paper": FIX["full"]}, "pattern": "Figure|Table"},
    }
)

# ---------------------------------------------------------------------------
# internal helpers
add(
    {
        "id": ".is_bibr12.full",
        "r": "metacheck:::.is_bibr12",
        "py": "metacheck.io.bibr12._is_bibr12",
        "args": {"paper": {"$paper": FIX["full"]}},
    },
    "import-bibr12.R helpers",
)
add(
    {
        "id": ".is_bibr12.demo",
        "r": "metacheck:::.is_bibr12",
        "py": "metacheck.io.bibr12._is_bibr12",
        "args": {"paper": {"$paper": "demo"}},
    }
)
add(
    {
        "id": ".bibr12_paper_ids.mixed",
        "r": "metacheck:::.bibr12_paper_ids",
        "py": "metacheck.io.bibr12._bibr12_paper_ids",
        "args": {"paper": {"$read": MIXED}},
    }
)
add(
    {
        "id": ".bibr12_paper_ids.paper",
        "r": "metacheck:::.bibr12_paper_ids",
        "py": "metacheck.io.bibr12._bibr12_paper_ids",
        "args": {"paper": {"$paper": FIX["full"]}},
    }
)
add(
    {
        "id": ".bibr12_doi",
        "r": "metacheck:::.bibr12_doi",
        "py": "metacheck.io.bibr12._bibr12_doi",
        "args": {
            "x": {
                "$chr": [
                    " https://doi.org/10.1234/ABC ",
                    "HTTP://DX.DOI.ORG/10.1000/xyz",
                    "doi:10.5555/a b",
                    "doi: 10.5555/ab",
                    "DOI:10.5555/ab",
                    "10.123/short",
                    None,
                    "",
                    "10.12345/x",
                    "https://doi.org/10.1234/a\tb",
                ]
            }
        },
    }
)
add(
    {
        "id": ".bibr12_bib_type",
        "r": "metacheck:::.bibr12_bib_type",
        "py": "metacheck.io.bibr12._bibr12_bib_type",
        "args": {
            "x": {
                "$chr": [
                    "article",
                    "journal-article",
                    "book",
                    "phdthesis",
                    "misc",
                    None,
                    "thesis",
                    "posted-content",
                    "Article",
                    "incollection",
                    "proceedings-article",
                    "dataset",
                    "",
                ]
            }
        },
    }
)
add(
    {
        "id": ".bibr12_df.types",
        "r": "identity",
        "py": "tests.bibr12._parity_helpers.identity",
        "args": {
            "x": expr(
                "metacheck:::.bibr12_df(list(a = list(1L, NULL, 'x'), b = list('x', 2.5, TRUE), "
                "c = list(list('a', 'b'), NULL, list()), d = list(list(list('A', 'B'), list(1L)), NULL, list()), "
                "e = list(list(x = 1), NULL, 'j'), g = list(TRUE, 0L, NULL), h = list(3L, 1.5, NULL)), "
                "c(a = 'int', b = 'chr', c = 'chr[]', d = 'chr[][]', e = 'json', f = 'lgl', g = 'lgl', h = 'num'))",
                f"{B}._bibr12_df({{'a': [1, None, 'x'], 'b': ['x', 2.5, True], 'c': [['a', 'b'], None, []], "
                "'d': [[['A', 'B'], [1]], None, []], 'e': [{'x': 1}, None, 'j'], 'g': [True, 0, None], "
                "'h': [3, 1.5, None]}, {'a': 'int', 'b': 'chr', 'c': 'chr[]', 'd': 'chr[][]', 'e': 'json', "
                "'f': 'lgl', 'g': 'lgl', 'h': 'num'})",
            )
        },
    }
)
add(
    {
        "id": ".bibr12_df.empty",
        "r": "identity",
        "py": "tests.bibr12._parity_helpers.identity",
        "args": {
            "x": expr(
                "metacheck:::.bibr12_df(list(), metacheck:::.bibr12_cols$table)",
                f"{B}._bibr12_df({{}}, {B}.BIBR12_COLS['table'])",
            )
        },
    }
)
add(
    {
        "id": ".bibr12_rows",
        "r": "identity",
        "py": "tests.bibr12._parity_helpers.identity",
        "args": {
            "x": expr(
                "metacheck:::.bibr12_rows(list(list(a = 1L, b = 'x'), list(b = 'y')), c(a = 'int', b = 'chr', c = 'chr'))",
                f"{B}._bibr12_rows([{{'a': 1, 'b': 'x'}}, {{'b': 'y'}}], {{'a': 'int', 'b': 'chr', 'c': 'chr'}})",
            )
        },
    }
)
add(
    {
        "id": ".bibr12_sha256",
        "r": "metacheck:::.bibr12_sha256",
        "py": "metacheck.io.bibr12._bibr12_sha256",
        "args": {"path": {"$file": FIX["full"]}},
    }
)
add(
    {
        "id": ".bibr12_sha256.missing",
        "r": "metacheck:::.bibr12_sha256",
        "py": "metacheck.io.bibr12._bibr12_sha256",
        "args": {"path": {"$file": "no/such/file.pdf"}},
    }
)
add(
    {
        "id": ".paper_schema_bibr12",
        "r": "identity",
        "py": "tests.bibr12._parity_helpers.identity",
        "args": {
            "x": expr(
                "local({s <- metacheck:::.paper_schema_bibr12(); defs <- lapply(s$`$defs`, function(d) "
                "lapply(d$properties, function(p) if (is.null(p$type)) NULL else p$type[[1]])); enums <- list(); "
                "for (nm in names(s$`$defs`)) for (col in names(s$`$defs`[[nm]]$properties)) { "
                "e <- s$`$defs`[[nm]]$properties[[col]]$enum; if (!is.null(e)) enums[[paste0(nm, '.', col)]] <- e }; "
                "list(properties = names(s$properties), required = s$required, description = s$description, "
                "defs = defs, enums = enums)})",
                f"{H}.schema_summary()",
            )
        },
    }
)

# ---------------------------------------------------------------------------
# paper_write(schema_version = "12.0"): the written file, line by line
first = True
for name, path in FIX.items():
    fn = "inspect" if name == "inspect" else None  # its paper_id is a DOI (a path with a slash)
    add(
        identity(f"paper_write.text.{name}", r_lines(rread(path), fn), py_lines(pyread(path), fn)),
        'paper_write(schema_version = "12.0"): the file jsonlite writes, line by line '
        "(the converter's name and version are blanked: metacheck vs pytacheck)"
        if first
        else None,
    )
    first = False
add(
    identity(
        "paper_write.text.probe_docx.images",
        r_lines(rread(FIX["probe_docx"], True)),
        py_lines(pyread(FIX["probe_docx"], True)),
    )
)
first = True
for name, path in FIX.items():
    if name == "preprint":
        continue  # large: its text case checks the file, pytest the round trip
    fn = "inspect" if name == "inspect" else None
    add(
        identity(
            f"paper_write.roundtrip.{name}",
            r_roundtrip(rread(path), fn),
            py_roundtrip(pyread(path), fn),
            compare={"ignore": ["extraction.converter"]},
        ),
        "... and read back: the same paper" if first else None,
    )
    first = False
add(
    identity(
        "paper_write.roundtrip.probe_docx.images",
        r_roundtrip(rread(FIX["probe_docx"], True)),
        py_roundtrip(pyread(FIX["probe_docx"], True)),
        compare={"ignore": ["extraction.converter"]},
    )
)
add(
    identity(
        "paper_write.text.add_bib_match",
        r_lines(
            "local({p <- read('" + FIX["full"] + "'); p$bib_match <- data.frame(bib_id = 1L, "
            "service = 'crossref', service_id = NA_character_, score = 61.7, bib_type = 'article', "
            "doi = '10.1234/PRIOR', title = 'A Prior Study', publisher = NA_character_, "
            "year = 2020L, date = NA_character_, container = 'Journal of Things'); "
            "p$bib_match$authors <- list(data.frame(given = 'Jane', family = 'Smith')); "
            "p$bib_match$editors <- list(data.frame(given = character(0), family = character(0))); p})"
        ),
        py_lines(f"{H}.add_bib_match_rows()"),
    ),
    "match rows made by add_bib_match() are converted to 12.0 columns",
)
add(
    identity(
        "paper_write.text.match_variants",
        r_lines(
            "local({p <- read('"
            + FIX["full"]
            + "'); p$bib_match <- data.frame(bib_id = c(1L, 1L, 1L, 1L), "
            "service = c('crossref', 'pubmed', NA, 'ror'), score = c(0.5, -1, 100, NA), "
            "bib_type = c('inproceedings', 'posted-content', 'weird', NA), "
            "doi = c(' https://doi.org/10.1234/ABC ', 'doi: 10.5555/x.y', 'not a doi', NA), "
            "title = c('A', 'B', 'C', 'D'), year = c(2020L, NA, 2021L, 2022L), "
            "date = c('2020-05', 'May 2020', NA, '2022-01-02')); "
            "p$bib_match$authors <- list(data.frame(given = c('Jane', NA), family = c('Smith', 'Doe')), "
            "NULL, data.frame(given = character(0), family = character(0)), "
            "data.frame(given = NA_character_, family = NA_character_)); "
            "p$info_match$score <- 99; p})"
        ),
        py_lines(f"{H}.match_rows_variants()"),
    )
)
add(
    identity(
        "paper_write.default.probe_html",
        "local({p <- read('"
        + FIX["probe_html"]
        + "'); f <- paper_write(p, save_path = "
        + SAVE_DIR_R
        + "); "
        "json <- jsonlite::read_json(f); list(names = names(json), title = json$info[[1]]$title, "
        "schema_version = json$schema_version)})",
        f"{H}.legacy_summary({pyread(FIX['probe_html'])})",
    ),
    "paper_write() without schema_version (R's default; pytacheck's schema_version=None) "
    "saves the paper object",
)
add(
    {
        "id": "paper_write.paperlist",
        "r": "identity",
        "py": "tests.bibr12._parity_helpers.identity",
        "args": {
            "x": expr(
                "unname(basename(paper_write(read(c('"
                + FIX["full"]
                + "', '"
                + FIX["preprint"]
                + "')), "
                "save_path = " + SAVE_DIR_R + ", schema_version = '12.0')))",
                f"{H}.paperlist_write(['{FIX['full']}', '{FIX['preprint']}'])",
            )
        },
    }
)
# refusals
add(
    {
        "id": "paper_write.error.older_format",
        "r": "paper_write",
        "py": "metacheck.paper_write",
        "args": {
            "paper": {"$paper": "demo"},
            "save_path": expr(SAVE_DIR_R, f"str({H}._tempdir())"),
            "schema_version": "12.0",
        },
    },
    "refusals: an older paper, a later 12.x paper, an unknown schema_version",
)
add(
    {
        "id": "paper_write.error.later_12x",
        "r": "paper_write",
        "py": "metacheck.paper_write",
        "args": {
            "paper": expr(
                "local({p <- read('"
                + FIX["probe_html"]
                + "'); p$info$schema_version <- '12.1'; p})",
                f"{H}.with_schema_version('{FIX['probe_html']}', '12.1')",
            ),
            "save_path": expr(SAVE_DIR_R, f"str({H}._tempdir())"),
            "schema_version": "12.0",
        },
    }
)
add(
    {
        "id": "paper_write.error.version",
        "r": "paper_write",
        "py": "metacheck.paper_write",
        "args": {
            "paper": {"$paper": FIX["probe_html"]},
            "save_path": expr(SAVE_DIR_R, f"str({H}._tempdir())"),
            "schema_version": "11.0",
        },
    }
)

# ---------------------------------------------------------------------------
# match-table.R: 12.x tables carry their own caption
first = True
for name in ["full", "probe_html", "probe_docx", "inspect"]:
    add(
        {
            "id": f".table_tests.{name}",
            "r": "metacheck:::.table_tests",
            "py": "metacheck.statout.match_table._table_tests",
            "args": {"paper": {"$paper": FIX[name]}},
        },
        ".table_tests(): 12.x tables use their own caption (not the text of their section)"
        if first
        else None,
    )
    first = False
add(
    {
        "id": ".table_tests.correlation_caption",
        "r": "metacheck:::.table_tests",
        "py": "metacheck.statout.match_table._table_tests",
        "args": {
            "paper": expr(
                "local({p <- read('"
                + FIX["full"]
                + "'); p$table$contents <- list(list(c('', '1.', '2.'), "
                "c('1. Anxiety', '-', ''), c('2. Depression', '.45*', '-'))); "
                "p$table$caption <- 'Table 1. Correlations between measures'; p})",
                f"{H}.correlation_table()",
            )
        },
    }
)
add(
    {
        "id": ".table_tests.correlation_caption.older",
        "r": "metacheck:::.table_tests",
        "py": "metacheck.statout.match_table._table_tests",
        "args": {
            "paper": expr(
                "local({p <- read('"
                + FIX["full"]
                + "'); p$table$contents <- list(list(c('', '1.', '2.'), "
                "c('1. Anxiety', '-', ''), c('2. Depression', '.45*', '-'))); "
                "p$table$caption <- 'Table 1. Correlations between measures'; p$info$schema_version <- NA_character_; p})",
                f"{H}.correlation_table(None)",
            )
        },
    }
)

R_DESC = (
    "local({p <- read('" + FIX["full"] + "'); p$table$contents <- list(list(c('', '1', '2'), "
    "c('Condition A', '1.93', '-0.15'))); p$table$caption <- 'Table 1. Descriptive statistics'; p})"
)
R_DESC_OLD = R_DESC.replace("; p})", "; p$info$schema_version <- NA_character_; p})")
R_JASP = (
    "metacheck::stat_results_long(metacheck::read_stat_tables("
    "'upstream/metacheck/tests/testthat/fixtures/formats/sample.jasp'), source_file = 'sample.jasp')"
)
for tag, r_paper, py_paper in [
    ("", R_DESC, f"{H}.descriptive_table()"),
    (".older", R_DESC_OLD, f"{H}.descriptive_table(None)"),
]:
    add(
        identity(
            f"match_reported_output.descriptive_caption{tag}",
            f"metacheck::match_reported_output({r_paper}, {R_JASP}, include_tables = TRUE, min_components = 1L)",
            f"{H}.match_jasp({py_paper})",
        ),
        "match_reported_output(include_tables = TRUE): a 12.x table's caption names its statistics"
        if not tag
        else None,
    )
    add(
        identity(
            f"match_reported_output.descriptive_caption{tag}.summary",
            f"attr(metacheck::match_reported_output({r_paper}, {R_JASP}, include_tables = TRUE, "
            "min_components = 1L), 'summary')",
            f"{H}.match_jasp({py_paper}, 'summary')",
        )
    )
add(
    {
        "id": ".table_tests.descriptive_caption",
        "r": "metacheck:::.table_tests",
        "py": "metacheck.statout.match_table._table_tests",
        "args": {"paper": expr(R_DESC, f"{H}.descriptive_table()")},
    }
)

# ---------------------------------------------------------------------------
# modules on 12.x papers (every built-in module that needs no network or LLM)
MODULES = [
    "all_urls",
    "coi_check",
    "coi_check_oi",
    "ethics_check",
    "funding_check",
    "funding_check_oi",
    "open_practices",
    "all_p_values",
    "marginal",
    "stat_check",
    "stat_effect_size",
    "stat_p_exact",
    "stat_p_nonsig",
    "ref_accuracy",
    "ref_consistency",
    "ref_miscitation",
    "ref_replication",
    "ref_retraction",
    "ref_summary",
    "power",
]
first = True
for mod in MODULES:
    for name in ["preprint", "PMC4383902", "full"]:
        add(
            {"id": f"module.{mod}.{name}", "module": mod, "args": {"paper": {"$paper": FIX[name]}}},
            "module_run() of every built-in module that needs no network or LLM" if first else None,
        )
        first = False
    if mod.startswith("ref_"):
        add({"id": f"module.{mod}.list", "module": mod, "args": {"paper": {"$read": LIST}}})
    add({"id": f"module.{mod}.mixed", "module": mod, "args": {"paper": {"$read": MIXED}}})
# the 12.x branches of the reference modules
add(
    {
        "id": "module.ref_accuracy.nodoi",
        "module": "ref_accuracy",
        "args": {
            "paper": expr(
                "local({p <- read('"
                + FIX["full"]
                + "'); p$bib$doi <- NA_character_; p$bib$text_id <- 2L; "
                "p$bib_match$title <- NA_character_; p})",
                f"{H}.full_nodoi()",
            )
        },
    },
    "ref_accuracy compares the 0-1 match scores of 12.x papers with suggest_score / 100",
)
add(
    {
        "id": "module.ref_accuracy.nodoi.older",
        "module": "ref_accuracy",
        "args": {
            "paper": expr(
                "local({p <- read('"
                + FIX["full"]
                + "'); p$bib$doi <- NA_character_; p$bib$text_id <- 2L; "
                "p$bib_match$title <- NA_character_; p$info$schema_version <- NA_character_; p})",
                f"{H}.full_nodoi(None)",
            )
        },
    }
)
add(
    {
        "id": "module.ref_accuracy.nodoi.suggest_score",
        "module": "ref_accuracy",
        "args": {
            "paper": expr(
                "local({p <- read('"
                + FIX["full"]
                + "'); p$bib$doi <- NA_character_; p$bib$text_id <- 2L; "
                "p$bib_match$title <- NA_character_; p})",
                f"{H}.full_nodoi()",
            ),
            "suggest_score": 99,
        },
    }
)
add(
    {
        "id": "module.ref_miscitation.full.db",
        "module": "ref_miscitation",
        "args": {
            "paper": {"$paper": FIX["full"]},
            "db": {
                "$df": {
                    "doi": ["10.1234/prior"],
                    "reftext": ["A prior study"],
                    "warning": ["Often miscited"],
                }
            },
        },
    },
    'ref_miscitation and ref_consistency follow 12.x citations (target_id of "bib" xrefs)',
)
add(
    {
        "id": "module.ref_miscitation.preprint.db",
        "module": "ref_miscitation",
        "args": {
            "paper": {"$paper": FIX["preprint"]},
            "db": {
                "$df": {
                    "doi": ["10.18637/jss.v082.i13", "10.1037/met0000210"],
                    "reftext": ["lmerTest", "other"],
                    "warning": ["Check it", "Check that"],
                }
            },
        },
    }
)
add(
    {
        "id": "module.ref_miscitation.mixed.db",
        "module": "ref_miscitation",
        "args": {
            "paper": {"$read": MIXED},
            "db": {
                "$df": {
                    "doi": ["10.1234/prior", "10.18637/jss.v082.i13"],
                    "reftext": ["A prior study", "lmerTest"],
                    "warning": ["W1", "W2"],
                }
            },
        },
    }
)

# ---------------------------------------------------------------------------
header = """# bibr export schema 12.x: read and write natively (R/import-bibr12.R; metacheck PR #423).
# Written by tests/bibr12/gen_parity_cases.py; goldens: python -m parity generate --area bibr12
#
# Fixtures: metacheck's tests/testthat/fixtures/bibr12 exports (PMC4383902, full, preprint,
# probe_docx, probe_html) and pytacheck's tests/fixtures/bibr_v12_full.json (= full.json) and
# bibr_v12_inspect.json (optional keys and tables missing).
# $expr helpers: tests/bibr12/_parity_helpers.py (Python) mirror the inline R code.
area: "bibr12"
cases:
"""


def dump_value(v):
    return json.dumps(v, ensure_ascii=False)


lines = [header.rstrip("\n")]
for c in cases:
    cm = comments.get(c["id"])
    if cm:
        lines.append(f"# {cm}")
    first_key = True
    for k, v in c.items():
        prefix = "- " if first_key else "  "
        first_key = False
        if k == "args":
            lines.append(f'{prefix}"args":')
            for ak, av in v.items():
                lines.append(f"    {dump_value(ak)}: {dump_value(av)}")
        else:
            lines.append(f"{prefix}{dump_value(k)}: {dump_value(v)}")
out = ROOT / "parity" / "cases" / "bibr12.yaml"
out.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(len(cases), "cases ->", out)
