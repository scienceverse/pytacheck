"""Generate parity/cases/grobid12_review.yaml (adversarial review of grobid12).

    python tests/grobid12/gen_review_cases.py
    python -m parity generate --area grobid12_review

Branches of R/import-grobid-bibr12.R the grobid12 cases do not reach, on the
TEI fixtures tests/grobid12/fixtures/review_*.tei.xml:

* review_urls: URL link text replaced in every row (bib and caption rows too,
  and inside earlier replacements; pytacheck replaces it in its own row only,
  U28), hrefs with spaces and a final ".", a URL
  in a caption and a footnote, a <ref> without a type, targets without "#",
  with a leading space, of the wrong float type, duplicated xml:ids (the
  first wins), a head-less and a p-less div, a p equal to its header,
  comparators (<, ≈, ≠, ~), back <div type="foot"> / "annex" (section types),
  a footnote in the back, the second of two <application>s (GROBID) with a
  "+02:00" offset and fractional seconds, page coords " 3,..", "2.9,..",
  "0,..", "1e1,..", "-1,..", caption labels (lower-case head, non-English
  head, roman numerals, "Table2", "tab 1"), a <row/> without cells;
* review_url_na: a <ref type="url"> without a target (in metacheck every row
  that prints its link text becomes NA; pytacheck leaves them alone, U28);
* review_header: header edge cases (ORCIDs, repeated/missing affiliations,
  an author without persName, an upper-case DOI URL, an invalid <application
  when>, ident "Bibr" -- bibr_version is then set, a blank keyword, an
  abstract without <p>);
* review_noapp: no <application> (producer "unknown"), keywords with an
  empty term, abstract <p>s with a blank one, more than three bad DOIs;
* review_inline: a footnote and nested figures inside body paragraphs (they
  are removed from the body, the text around them kept), a div holding only
  a float, an empty div, a head with a <ref>, JSON escapes (quotes,
  backslash, tab and newline in the title and keywords, DEL, "</", a non-BMP
  character);
* review_nobody: no <body>; a back footnote citing a reference and itself;
* review_unicode: captions and heads with no-break, thin, ideographic and
  zero-width spaces, NEL and U+2028 (TRE's \\s vs trimws()), non-ASCII heads
  (tolower() of É and İ), "Figures 9", "Fig.S11", "Table ABC1";
* review_sentences: Grobid's sentence segmentation (<s> in <p>, <figDesc>
  <div><p><s>, footnote <p><s>) under xml:space="preserve": the formatted
  re-parse of the document (read_xml(as.character(xml))) adds indentation
  (pytacheck unwraps the <s> tags, U28).

For each: the whole paper, the written 12.0 file line by line, and read() of
it. The converter (metacheck vs pytacheck) is masked, and completed_at when
it is the time of the conversion. Also: the error messages of a corrupt file,
an empty URL link text and a missing file (the harness only checks that both
sides fail), and the deterministic reference/URL/statistics modules on a list
of the review fixtures.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIX = "tests/grobid12/fixtures"
H = "__import__('tests.grobid12._parity_helpers', fromlist=['x'])"
SAVE_DIR_R = "local({d <- tempfile('pc_grobid12_'); dir.create(d); d})"

# fixture -> whether its TEI says when Grobid ran (a valid <application when>)
FIXTURES = {
    "review_urls": True,
    "review_url_na": False,
    "review_header": False,
    "review_noapp": False,
    "review_inline": True,
    "review_nobody": True,
    "review_unicode": True,
    "review_sentences": True,
}


def expr(r, py):
    return {"$expr": {"r": r, "py": py}}


def ignore(when):
    return ["extraction.converter"] + ([] if when else ["extraction.completed_at"])


def r_written(path, completed_at):
    mask = "x[grepl('^ *\"completed_at\": ', x)] <- '<completed_at>'; " if completed_at else ""
    return (
        f"local({{f <- grobid_to_bibr('{path}', {SAVE_DIR_R}, schema_version = '12.0'); "
        "x <- readLines(f, encoding = 'UTF-8'); i <- which(trimws(x) == '\"converter\": {'); "
        "x[i + 1] <- '<converter name>'; x[i + 2] <- '<converter version>'; "
        f"{mask}c(basename(f), x)}})"
    )


cases = []
for name, when in FIXTURES.items():
    path = f"{FIX}/{name}.tei.xml"
    cases.append(
        {
            "id": f".grobid_to_bibr.{name}",
            "r": ".grobid_to_bibr",
            "py": "pytacheck.io.grobid._grobid_to_bibr",
            "args": {"xml_path": {"$file": path}, "schema_version": "12.0"},
            "compare": {"ignore": ignore(when)},
        }
    )
    cases.append(
        {
            "id": f"written.{name}",
            "r": "identity",
            "py": "tests.grobid12._parity_helpers.identity",
            "args": {
                "x": expr(r_written(path, not when), f"{H}.written_lines({path!r}, {not when})")
            },
        }
    )
    cases.append(
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
            "compare": {"ignore": ignore(when)},
        }
    )

# conversions that fail in R (Python must fail too, whatever its message)
ERRORS = {
    "corrupt": "upstream/metacheck/tests/testthat/fixtures/problems/corrupt.xml",
    "url_empty": "tests/io/fixtures/url_empty.tei.xml",
    "missing_file": "tests/grobid12/fixtures/no_such_file.tei.xml",
}
for name, path in ERRORS.items():
    cases.append(
        {
            "id": f".grobid_to_bibr.error_message.{name}",
            "r": "identity",
            "py": "tests.grobid12._parity_helpers.identity",
            "args": {
                "x": expr(
                    f".grobid_to_bibr('{path}', schema_version = '12.0')",
                    f"__import__('pytacheck.io.grobid', fromlist=['x'])"
                    f"._grobid_to_bibr(str({H}._path({path!r})), None, '12.0')",
                )
            },
        }
    )

# the deterministic modules that read references, cross-references, URLs and
# statistics, on a list of the review fixtures (odd xrefs: NA, duplicated and
# wrong-type targets, multi-target refs; URLs replaced everywhere)
MODULE_FILES = [
    f"{FIX}/{n}.tei.xml"
    for n in ("review_urls", "review_url_na", "review_noapp", "review_inline", "review_sentences")
] + [f"{FIX}/v12_edges.tei.xml"]
r_files = "c(" + ", ".join(f"'{f}'" for f in MODULE_FILES) + ")"
papers = {
    "$call": {
        "r": "grobid_to_bibr",
        "py": "pytacheck.io.grobid.grobid_to_bibr",
        "args": {
            "xml_path": expr(f"file.path(getwd(), {r_files})", f"{H}.paths({MODULE_FILES!r})"),
            "save_path": {"$null": True},
            "schema_version": "12.0",
        },
    }
}
for mod in (
    "all_urls",
    "all_p_values",
    "marginal",
    "stat_check",
    "ref_consistency",
    "ref_miscitation",
    "ref_summary",
    "ref_replication",
    "ref_retraction",
    "ref_accuracy",
):
    cases.append({"id": f"module.{mod}.review", "module": mod, "args": {"paper": papers}})

header = """# Adversarial review of the Grobid TEI -> bibr export schema 12.0 conversion
# (R/import-grobid-bibr12.R; metacheck PR #423): branches the grobid12 cases do not reach.
# Written by tests/grobid12/gen_review_cases.py (see its docstring for what each fixture covers);
# goldens: python -m parity generate --area grobid12_review
area: "grobid12_review"
cases:
"""


def dump(v):
    return json.dumps(v, ensure_ascii=False)


lines = [header.rstrip("\n")]
for c in cases:
    first = True
    for k, v in c.items():
        prefix = "- " if first else "  "
        first = False
        if k == "args":
            lines.append(f'{prefix}"args":')
            for ak, av in v.items():
                lines.append(f"    {dump(ak)}: {dump(av)}")
        else:
            lines.append(f"{prefix}{dump(k)}: {dump(v)}")
out = ROOT / "parity" / "cases" / "grobid12_review.yaml"
out.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(len(cases), "cases ->", out)
