"""Generate parity/cases/grobid12.yaml (Grobid TEI -> bibr export schema 12.0).

    python tests/grobid12/gen_parity_cases.py
    python -m parity generate --area grobid12

Each case's R side is metacheck code (``grobid_to_bibr(..., schema_version =
"12.0")``); ``$expr`` cases pair inline R with the helpers in
tests/grobid12/_parity_helpers.py. Every conversion passes schema_version
explicitly: the Python side of the harness runs with metacheck's defaults.
"""

import json
from pathlib import Path

import regex

ROOT = Path(__file__).resolve().parents[2]
UP = "upstream/metacheck"
FIX = f"{UP}/tests/testthat/fixtures"
TUE = f"{UP}/tests/testthat/apis/grobid.hti.ieis.tue.nl/api"
HF = f"{UP}/tests/testthat/apis/grobidorg-grobid.hf.space/api"
IO = "tests/io/fixtures"
H = "__import__('tests.grobid12._parity_helpers', fromlist=['x'])"

# every Grobid TEI file in metacheck (demo, fixtures, recorded Grobid responses)
UPSTREAM = {
    "demo": f"{UP}/inst/demos/to_err_is_human.xml",
    "debruine-child": f"{FIX}/debruine/debruine-child.xml",
    "debruine-fret": f"{FIX}/debruine/debruine-fret.xml",
    "debruine-sex": f"{FIX}/debruine/debruine-sex.xml",
    "debruine-tnl": f"{FIX}/debruine/debruine-tnl.xml",
    "apa": f"{FIX}/formats/apa.xml",
    "preprint.cermine": f"{FIX}/formats/preprint.cermine.xml",
    "preprint": f"{FIX}/formats/preprint.pdf.tei.xml",
    "published.cermine": f"{FIX}/formats/published.cermine.xml",
    "published": f"{FIX}/formats/published.pdf.tei.xml",
    "0956797615569889": f"{FIX}/problems/0956797615569889.xml",
    "0956797617737129": f"{FIX}/problems/0956797617737129.xml",
    "203020": f"{FIX}/problems/203020.xml",
    "corrupt": f"{FIX}/problems/corrupt.xml",
}
for h in ("06d588", "1181af", "479883", "4b4691", "619fd9", "9c61e4", "d2308a", "d67d70"):
    UPSTREAM[f"apis.tue.{h}"] = f"{TUE}/processFulltextDocument-{h}-POST.xml"
for h in ("06d588", "1181af", "9c61e4", "d2308a", "d67d70"):
    UPSTREAM[f"apis.hf.{h}"] = f"{HF}/processFulltextDocument-{h}-POST.xml"
# pytacheck's TEI edge cases
IO_TEI = {
    f"io.{p.name.removesuffix('.tei.xml')}": f"{IO}/{p.name}"
    for p in sorted((ROOT / IO).glob("*.tei.xml"))
}
# the 12.0 conversion's own edge cases (multi-target and unmapped refs, bad DOIs,
# off-vocabulary comparators, caption labels, page coords, back-matter types)
EDGES = {"edges": "tests/grobid12/fixtures/v12_edges.tei.xml"}
ALL = {**UPSTREAM, **IO_TEI, **EDGES}
# a paper list of every upstream TEI file that converts (the hf responses
# repeat the tue ones' paper_ids)
LIST_UP = [p for n, p in UPSTREAM.items() if not n.startswith("apis.hf.") and n != "corrupt"]
LIST_IO = [*IO_TEI.values(), *EDGES.values()]
LIST_DEBRUINE = [UPSTREAM[f"debruine-{n}"] for n in ("child", "fret", "sex", "tnl")]
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
_WHEN = regex.compile(r"<application[^>]*\swhen=")


def has_when(path):
    """Whether the TEI header says when Grobid extracted the content."""
    return bool(_WHEN.search((ROOT / path).read_text(encoding="utf-8", errors="replace")))


def paper_id(path):
    """The paper_id of a TEI file's 12.0 conversion: its source (the PDF next to
    it, else the TEI) without the extension."""
    xml = ROOT / path
    stem = regex.sub(r"(?i)\.tei\.xml$", "", xml.name)
    pdfs = [xml.parent / stem, xml.parent / regex.sub(r"(?i)\.xml$", ".pdf", xml.name)]
    pdfs = [f for f in pdfs if f.name.lower().endswith(".pdf") and f.exists()]
    name = (pdfs[0] if pdfs else xml).name
    return regex.sub(r"([^.]+)\.[[:alnum:]]+$", r"\1", name)


def ignore(path, prefix=""):
    """Volatile fields: the converter (metacheck vs pytacheck), and completed_at
    when it is the time of the conversion."""
    fields = [f"{prefix}extraction.converter"]
    if not has_when(path):
        fields.append(f"{prefix}extraction.completed_at")
    return fields


def ignore_list(files):
    """ignore() for each paper of a paper list (named by paper_id)."""
    return [f for path in files for f in ignore(path, f"{paper_id(path)}.")]


cases = []
comments = {}


def add(case, comment=None):
    if comment:
        comments[case["id"]] = comment
    cases.append(case)


def expr(r, py):
    return {"$expr": {"r": r, "py": py}}


def r_vec(files):
    return "c(" + ", ".join(f"'{f}'" for f in files) + ")"


def convert_call(files):
    """``$call`` of grobid_to_bibr(xml_path, NULL, schema_version = "12.0")."""
    xml_path = (
        {"$file": files}
        if isinstance(files, str)
        else expr(f"file.path(getwd(), {r_vec(files)})", f"{H}.paths({files!r})")
    )
    return {
        "$call": {
            "r": "grobid_to_bibr",
            "py": "metacheck.io.grobid.grobid_to_bibr",
            "args": {"xml_path": xml_path, "save_path": {"$null": True}, "schema_version": "12.0"},
        }
    }


SAVE_DIR_R = "local({d <- tempfile('pc_grobid12_'); dir.create(d); d})"


def r_written(path, completed_at):
    mask = "x[grepl('^ *\"completed_at\": ', x)] <- '<completed_at>'; " if completed_at else ""
    return (
        f"local({{f <- grobid_to_bibr('{path}', {SAVE_DIR_R}, schema_version = '12.0'); "
        "x <- readLines(f, encoding = 'UTF-8'); i <- which(trimws(x) == '\"converter\": {'); "
        "x[i + 1] <- '<converter name>'; x[i + 2] <- '<converter version>'; "
        f"{mask}c(basename(f), x)}})"
    )


# ---------------------------------------------------------------------------
# .grobid_to_bibr(schema_version = "12.0"): every TEI file, the whole paper
first = True
for name, path in ALL.items():
    add(
        {
            "id": f".grobid_to_bibr.{name}",
            "r": ".grobid_to_bibr",
            "py": "metacheck.io.grobid._grobid_to_bibr",
            "args": {"xml_path": {"$file": path}, "schema_version": "12.0"},
            "compare": {"ignore": ignore(path)},
        },
        ".grobid_to_bibr(schema_version = '12.0') of every TEI file: the whole paper "
        "(converter ignored; completed_at too when the TEI does not say when Grobid ran)"
        if first
        else None,
    )
    first = False

# grobid_to_bibr(): one file, several (with failures), a directory, bad schema_version
add(
    {
        "id": "grobid_to_bibr.null_save.demo",
        "r": "grobid_to_bibr",
        "py": "metacheck.io.grobid.grobid_to_bibr",
        "args": {
            "xml_path": {"$file": UPSTREAM["demo"]},
            "save_path": {"$null": True},
            "schema_version": "12.0",
        },
        "compare": {"ignore": ignore(UPSTREAM["demo"])},
    },
    "grobid_to_bibr(schema_version = '12.0')",
)
multi = [UPSTREAM["preprint"], UPSTREAM["corrupt"], IO_TEI["io.edge_body"]]
add(
    {
        "id": "grobid_to_bibr.null_save.multiple_with_failure",
        "r": "grobid_to_bibr",
        "py": "metacheck.io.grobid.grobid_to_bibr",
        "args": {
            "xml_path": expr(f"file.path(getwd(), {r_vec(multi)})", f"{H}.paths({multi!r})"),
            "save_path": {"$null": True},
            "schema_version": "12.0",
        },
        "compare": {"ignore": ignore_list([f for f in multi if f != UPSTREAM["corrupt"]])},
    }
)
add(
    {
        "id": "grobid_to_bibr.null_save.dir",
        "r": "grobid_to_bibr",
        "py": "metacheck.io.grobid.grobid_to_bibr",
        "args": {
            "xml_path": {"$file": f"{FIX}/debruine"},
            "save_path": {"$null": True},
            "schema_version": "12.0",
        },
        "compare": {"ignore": ignore_list(LIST_DEBRUINE)},
    }
)
for tag, value in (("11", "11"), ("number", 12.0), ("vector", ["12.0", "12.0"])):
    add(
        {
            "id": f"grobid_to_bibr.error.schema_version_{tag}",
            "r": "grobid_to_bibr",
            "py": "metacheck.io.grobid.grobid_to_bibr",
            "args": {
                "xml_path": {"$file": UPSTREAM["demo"]},
                "save_path": {"$null": True},
                "schema_version": value,
            },
        },
        "schema_version must be NULL or '12.0'" if tag == "11" else None,
    )
add(
    {
        "id": ".grobid_to_bibr.error.schema_version_11",
        "r": ".grobid_to_bibr",
        "py": "metacheck.io.grobid._grobid_to_bibr",
        "args": {"xml_path": {"$file": UPSTREAM["demo"]}, "schema_version": "11"},
    }
)

# pytacheck's read(schema_version = "12.0") is grobid_to_bibr(schema_version = "12.0")
for name in ("demo", "published", "apis.tue.479883"):
    path = UPSTREAM[name]
    add(
        {
            "id": f"read.v12.{name}",
            "r": "identity",
            "py": "tests.grobid12._parity_helpers.identity",
            "args": {
                "x": expr(
                    f"grobid_to_bibr('{path}', NULL, schema_version = '12.0')",
                    f"pc.read({H}._path('{path}'), schema_version='12.0')",
                )
            },
            "compare": {"ignore": ignore(path)},
        },
        "pytacheck's read() of a TEI file (schema_version = '12.0', its default) is "
        "metacheck's grobid_to_bibr(save_path = NULL, schema_version = '12.0')"
        if name == "demo"
        else None,
    )

# the written 12.0 file, line by line, and read back
first = True
for name, path in {**UPSTREAM, **EDGES}.items():
    if name == "corrupt" or name.startswith("apis.hf."):
        continue
    volatile = not has_when(path)
    add(
        {
            "id": f"written.{name}",
            "r": "identity",
            "py": "tests.grobid12._parity_helpers.identity",
            "args": {
                "x": expr(r_written(path, volatile), f"{H}.written_lines({path!r}, {volatile})")
            },
        },
        "grobid_to_bibr(save_path, schema_version = '12.0'): the file's name and lines "
        "(converter blanked: metacheck vs pytacheck; completed_at when it is the conversion time)"
        if first
        else None,
    )
    first = False
for name in ("demo", "preprint", "published", "apa", "0956797617737129", "edges"):
    path = {**UPSTREAM, **EDGES}[name]
    add(
        {
            "id": f"write_read.{name}",
            "r": "identity",
            "py": "tests.grobid12._parity_helpers.identity",
            "args": {
                "x": expr(
                    f"read(grobid_to_bibr('{path}', {SAVE_DIR_R}, schema_version = '12.0'))",
                    f"{H}.write_read({path!r})",
                )
            },
            "compare": {"ignore": ignore(path)},
        },
        "read() of the written 12.0 file" if name == "demo" else None,
    )

# paper_table() of every table, over lists of 12.0 conversions
for tag, files in (("upstream", LIST_UP), ("io", LIST_IO)):
    for t in TABLES:
        add(
            {
                "id": f"paper_table.{tag}.{t}",
                "r": "paper_table",
                "py": "metacheck.paper_table",
                "args": {"paper": convert_call(files), "table": t},
            },
            f"paper_table() of every table over the 12.0 conversions of the {tag} TEI files"
            if t == TABLES[0]
            else None,
        )

# modules on 12.0 Grobid papers (every built-in module that needs no network or LLM)
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
MODULE_PAPERS = {
    "demo": UPSTREAM["demo"],
    "preprint": UPSTREAM["preprint"],
    "published": UPSTREAM["published"],
    "0956797615569889": UPSTREAM["0956797615569889"],
    "debruine": LIST_DEBRUINE,
}
first = True
for mod in MODULES:
    for pname, files in MODULE_PAPERS.items():
        add(
            {
                "id": f"module.{mod}.{pname}",
                "module": mod,
                "args": {"paper": convert_call(files)},
            },
            "module_run() of every built-in module that needs no network or LLM, on 12.0 "
            "Grobid papers"
            if first
            else None,
        )
        first = False

# .bibr12_utc(): Grobid's <application when> as bibr 12.x's UTC time
UTC_INPUTS = [
    "2026-05-27T08:58+0000",
    "2026-05-27T08:58:30.7+02:00",
    "2026-05-27T08:58:30Z",
    "2026-05-27",
    None,
    "",
    "2026-02-30T08:58+0000",
    "2024-02-29T08:58+0000",
    "2026-5-7T8:5+0000",
    " 2026-05-27T08:58+0000xyz",
    "2026-05-27T08:58+01",
    "2026-05-27T08:58:60+0000",
    "2026-05-27T08:58:61+0000",
    "2026-05-27T24:00+0000",
    "2026-05-27T24:30+0000",
    "2026-05-27T08:58 +0000",
    "2026-05-27T08:58:+0000",
    "2026-05-27T08:58:1e1+0000",
    "2026-05-27T08:58-0130",
    "2026-05-27T08:58+1400",
    "2026-05-27T08:58+1500",
    "2026-05-27T08:58+0099",
    "12026-05-27T08:58+0000",
    "2026-05-27T08:58:30.999+0000",
    "2026-05-27 T08:58+0000",
    "0026-05-27T08:58+0000",
    "0000-01-01T00:00+0000",
    "2026-12-31T23:59-0100",
    "2026-05-27T08:58:-1+0000",
    "2026-05-27T08:58: 5+0000",
    "2026-05-27T08:58:NA+0000",
    "2026-05-27T08:58:Inf+0000",
    "2026-05-27T08:58:0x1A+0000",
    "2026-05-27T08:58:.5+0000",
    "2026-05-27T08:58:5.+0000",
    "2026-05-27T08:60+0000",
    "2026-05-27T08:58:30z",
    "2026-05-27T08:58+0000Z",
    "2026-05-27t08:58+0000",
    "2026-05-27T08:58+ 0000",
    "2026-01-40T00:00+0000",
    "2026-011-01T00:00+0000",
    "2026-05-27T08:58:30.5e1+0000",
    "2026-05-27T08:58:1e+0000",
    "2026-05-27T08:58:1e-1+0000",
    "2026-05-27T08:58:59.9999999+0000",
    "2026-05-27T08:58:59.9999+0000",
    "2026-05-27T08:58:59.99999999+0000",
    "1969-12-31T23:59:59.5+0000",
    "0000-01-01T00:00:30.5+0100",
]
r_inputs = "c(" + ", ".join("NA" if v is None else json.dumps(v) for v in UTC_INPUTS) + ")"
add(
    {
        "id": ".bibr12_utc.inputs",
        "r": "identity",
        "py": "tests.grobid12._parity_helpers.bibr12_utc",
        "args": {
            "x": expr(
                f"unname(vapply({r_inputs}, metacheck:::.bibr12_utc, ''))",
                repr(UTC_INPUTS),
            )
        },
    },
    ".bibr12_utc(): Grobid's <application when> as bibr 12.x's UTC time (R's strptime())",
)

# ---------------------------------------------------------------------------
header = """# Grobid TEI -> bibr export schema 12.0 (R/import-grobid-bibr12.R; metacheck PR #423).
# Written by tests/grobid12/gen_parity_cases.py; goldens: python -m parity generate --area grobid12
#
# Fixtures: every Grobid TEI file in metacheck (the demo, tests/testthat/fixtures and the
# recorded Grobid responses in tests/testthat/apis) and pytacheck's TEI edge cases
# (tests/io/fixtures/*.tei.xml, tests/grobid12/fixtures/v12_edges.tei.xml). Every conversion passes schema_version = "12.0" on both
# sides (the Python side of the harness otherwise runs with metacheck's defaults).
# $expr helpers: tests/grobid12/_parity_helpers.py (Python) mirror the inline R code.
area: "grobid12"
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
out = ROOT / "parity" / "cases" / "grobid12.yaml"
out.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(len(cases), "cases ->", out)
