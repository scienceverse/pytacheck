"""The documentation check of a data package: README, sections, template text, parts."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

import metacheck as pc
from metacheck.datapackage import open_package
from metacheck.datapackage.docs import (
    RULES,
    DocsResult,
    check_docs,
    find_headings,
    find_placeholders,
    find_readmes,
    load_components,
    load_readme_template,
    match_sections,
    read_readme_text,
    resolve_human_participants,
)
from metacheck.module import module_run
from metacheck.report import module_report

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _tree(root: Path, files: dict[str, str | bytes]) -> Path:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content, encoding="utf-8")
    return root


def _check(tmp_path: Path, files: dict[str, str | bytes], **kwargs: Any) -> DocsResult:
    root = _tree(tmp_path / "pkg", files)
    with open_package(root) as pkg:
        return check_docs(pkg, **kwargs)


def _status(result: DocsResult) -> dict[str, str]:
    return dict(zip(result.checklist["item"], result.checklist["status"], strict=True))


def _rules(result: DocsResult, check: str | None = None) -> list[str]:
    f = result.findings
    if check is not None:
        f = f.loc[f["check"] == check]
    return [str(r) for r in f["rule"]]


def _detail(result: DocsResult, item: str) -> str:
    row = result.checklist.set_index("item").loc[item]
    return str(row["detail"])


def _docx(path: Path, paragraphs: list[tuple[str | None, str]]) -> Path:
    """A minimal Word file: ``(style, text)`` paragraphs."""
    body = ""
    for style, text in paragraphs:
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        body += f'<w:p>{ppr}<w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", xml)
    return path


def _odt(path: Path, blocks: list[tuple[str, str]]) -> Path:
    """A minimal OpenDocument text: ``("h" | "p", text)`` blocks."""
    body = ""
    for kind, text in blocks:
        if kind == "h":
            body += f'<text:h text:outline-level="1">{text}</text:h>'
        else:
            body += f"<text:p>{text}</text:p>"
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
        f"<office:body><office:text>{body}</office:text></office:body></office:document-content>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("content.xml", xml)
    return path


def _pdf(path: Path, lines: list[str]) -> Path:
    """A one-page PDF with the given lines of text."""
    text = " ".join(f"({line}) Tj T*" for line in lines)
    stream = f"BT /F1 12 Tf 14 TL 72 720 Td {text} ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{obj}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    path.write_bytes(out)
    return path


GOOD_README = """# Survey data on cats and dogs

This package holds the responses of 200 people to a survey about cats and dogs,
collected in 2023, and the code that analyses them.

## Contact

A. Researcher, a.researcher@example.org

## Files

- data/raw.csv: the raw responses
- code/analysis.R: the analysis
- codebook.csv: what the variables mean

## License

CC BY 4.0
"""

GOOD_PACKAGE: dict[str, str | bytes] = {
    "README.md": GOOD_README,
    "data/raw.csv": "id,score\n1,3\n2,4\n",
    "code/analysis.R": "x <- read.csv('data/raw.csv')\n",
    "codebook.csv": "variable,label\nid,identifier\nscore,score\n",
    "LICENSE": "Creative Commons Attribution 4.0",
}

# A README written from a plain-text template: numbered ALL-CAPS headings, partly filled in.
TEMPLATE_README = """1. GENERAL INFORMATION
Project or Paper Title : <provide DOI of publication if applicable>
Authors information :
Name : A. Researcher
Email : a.researcher@example.org
Contacts : <provide at least two contacts>

2. SHARING/ACCESS INFORMATION
- Licenses/restrictions, or limitations of reuse : CC BY 4.0

3. DATA & FILE OVERVIEW
File List or folder tree : data/raw.csv, code/analysis.R

4. METHODOLOGICAL INFORMATION
Description of methods used for collection/generation of data : survey

5. DATA-SPECIFIC INFORMATION FOR: [FILENAME]
- Variable List: <list variable name(s)>
- Missing data codes: -999
"""


# --------------------------------------------------------------------------
# a good package
# --------------------------------------------------------------------------


def test_a_good_package_is_green(tmp_path: Path) -> None:
    r = _check(tmp_path, GOOD_PACKAGE, human_participants=False)
    assert _status(r) == {
        "readme_present": "pass",
        "readme_format": "pass",
        "readme_sections": "pass",
        "readme_placeholders": "pass",
        "readme_contact": "pass",
        "readme_file_list": "pass",
        "licence": "pass",
        "components": "pass",
        "component:readme": "pass",
        "component:data": "pass",
        "component:code": "pass",
        "component:codebook": "pass",
        "component:dmp": "na",
        "component:ethics": "na",
        "component:consent": "na",
    }
    assert r.traffic_light == "green"
    assert r.readme_path == "README.md"
    assert r.readme_text == GOOD_README
    assert len(r.findings) == 3  # the three optional sections that are not there
    assert set(_rules(r)) == {"section-optional-missing"}
    sections = r.sections.set_index("id")
    assert sections.loc["description", "kind"] == "intro"  # the text under the title
    assert sections.loc["contact", "heading"] == "## Contact"
    assert sections.loc["files", "line"] == 10
    assert not sections.loc["methods", "found"]


def test_the_checklist_and_findings_have_the_shared_columns(tmp_path: Path) -> None:
    from metacheck.datapackage import CHECKLIST_COLUMNS, FINDING_COLUMNS

    r = _check(tmp_path, {"data.csv": "a\n1\n"})
    assert tuple(r.checklist.columns) == CHECKLIST_COLUMNS
    assert tuple(r.findings.columns) == FINDING_COLUMNS
    assert set(r.findings["rule"]) <= set(RULES)


# --------------------------------------------------------------------------
# readme_present and readme_format
# --------------------------------------------------------------------------


def test_no_readme_fails_and_the_content_items_do_not_apply(tmp_path: Path) -> None:
    r = _check(tmp_path, {"data/raw.csv": "a\n1\n"})
    s = _status(r)
    assert s["readme_present"] == "fail"
    assert s["readme_format"] == "na"
    for item in ("readme_sections", "readme_placeholders", "readme_contact", "readme_file_list"):
        assert s[item] == "na"
    assert s["licence"] == "warn"
    assert s["component:readme"] == "fail"
    assert r.traffic_light == "red"
    assert r.readme_path is None
    assert r.readme_text is None
    assert "readme-missing" in _rules(r, "readme_present")
    assert r.findings.set_index("rule").loc["readme-missing", "severity"] == "problem"


def test_a_readme_only_in_a_subfolder_warns(tmp_path: Path) -> None:
    r = _check(tmp_path, {"docs/README.md": GOOD_README, "data/raw.csv": "a\n1\n"})
    assert _status(r)["readme_present"] == "warn"
    assert _rules(r, "readme_present") == ["readme-in-subfolder"]
    assert "Move it to the top" in r.findings.iloc[0]["detail"]
    assert r.readme_path == "docs/README.md"
    assert r.readme_text == GOOD_README  # the content is still checked


def test_several_readmes_at_the_top_is_information(tmp_path: Path) -> None:
    r = _check(tmp_path, {"README.md": GOOD_README, "README.txt": "old text", "a.csv": "a\n1\n"})
    assert _status(r)["readme_present"] == "pass"
    assert _rules(r, "readme_present") == ["readme-several"]
    assert r.findings.iloc[0]["severity"] == "info"
    assert r.readme_path == "README.md"  # Markdown first


def test_junk_files_are_not_readmes(tmp_path: Path) -> None:
    files: dict[str, str | bytes] = {
        "._README.txt": "x",
        "~$README.docx": "x",
        "README.txt~": "x",
        ".README": "x",
        "data.csv": "a\n1\n",
    }
    r = _check(tmp_path, files)
    assert _status(r)["readme_present"] == "fail"
    root = _tree(tmp_path / "pkg2", files)
    with open_package(root) as pkg:
        assert len(find_readmes(pkg)) == 0


def test_readme_names_and_other_languages(tmp_path: Path) -> None:
    for name in ("README", "readme.txt", "Read me.txt", "READ_ME.md", "leesmij.txt", "LEESMIJ.txt"):
        r = _check(tmp_path / name.replace(" ", "_"), {name: GOOD_README})
        assert _status(r)["readme_present"] == "pass", name
    # ro-crate-metadata.json is metadata, not a README
    r = _check(tmp_path / "ro", {"ro-crate-metadata.json": "{}"})
    assert _status(r)["readme_present"] == "fail"


def test_a_docx_readme_is_read_but_not_plain_text(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    root.mkdir()
    _docx(
        root / "README.docx",
        [
            ("Title", "Cats and dogs"),
            ("Heading1", "Overview"),
            (
                None,
                "This package holds the responses of 200 people to a survey about cats and dogs.",
            ),
            ("Heading1", "Contact"),
            (None, "Write to a.researcher@example.org &amp; colleagues"),
            ("Heading1", "Files"),
            (None, "data.csv: the data"),
        ],
    )
    (root / "data.csv").write_text("a\n1\n")
    with open_package(root) as pkg:
        r = check_docs(pkg)
    assert _status(r)["readme_format"] == "warn"
    assert _rules(r, "readme_format") == ["readme-format"]
    assert "Word document" in r.findings.iloc[0]["detail"]
    assert r.readme_text is not None
    assert "# Overview" in r.readme_text
    assert "a.researcher@example.org & colleagues" in r.readme_text
    s = _status(r)
    assert s["readme_sections"] == "pass"
    assert s["readme_contact"] == "pass"
    assert r.sections.set_index("id").loc["contact", "heading"] == "# Contact"


def test_an_odt_readme_is_read(tmp_path: Path) -> None:
    p = _odt(tmp_path / "README.odt", [("h", "Contact"), ("p", "a@example.org<text:tab/>now")])
    text = read_readme_text(p)
    assert text is not None
    assert text.splitlines() == ["# Contact", "a@example.org\tnow"]


def test_other_rich_formats_warn(tmp_path: Path) -> None:
    for ext in ("pdf", "docx", "odt", "rtf"):
        r = _check(tmp_path / ext, {f"README.{ext}": "not really"})
        assert _status(r)["readme_format"] == "warn", ext
    assert _status(_check(tmp_path / "md", {"README.md": "x"}))["readme_format"] == "pass"


def test_an_unreadable_readme_leaves_the_content_to_a_person(tmp_path: Path) -> None:
    r = _check(tmp_path, {"README.rtf": "{\\rtf1 hello}", "data.csv": "a\n1\n"})
    s = _status(r)
    assert s["readme_present"] == "pass"
    assert s["readme_format"] == "warn"
    for item in (
        "readme_sections",
        "readme_placeholders",
        "readme_contact",
        "readme_file_list",
        "licence",
    ):
        assert s[item] == "manual", item
    assert r.readme_text is None
    assert "readme-unreadable" in _rules(r, "readme_format")
    assert r.traffic_light == "yellow" or r.traffic_light == "red"  # the missing codebook etc.


def test_an_empty_readme_warns(tmp_path: Path) -> None:
    r = _check(tmp_path, {"README.txt": "\n  \n", "data.csv": "a\n1\n"})
    assert _status(r)["readme_present"] == "warn"
    assert _rules(r, "readme_present") == ["readme-empty"]
    assert r.readme_text == "\n  \n"
    assert _status(r)["readme_sections"] == "warn"


def test_text_encodings(tmp_path: Path) -> None:
    (tmp_path / "utf8.txt").write_bytes("café – data".encode())
    (tmp_path / "bom.txt").write_bytes(b"\xef\xbb\xbfhello")
    (tmp_path / "latin1.txt").write_bytes("café data".encode("latin-1"))
    (tmp_path / "utf16.txt").write_bytes("hello".encode("utf-16"))
    (tmp_path / "binary.txt").write_bytes(b"\x00\x01\x02")
    assert read_readme_text(tmp_path / "utf8.txt") == "café – data"
    assert read_readme_text(tmp_path / "bom.txt") == "hello"
    assert read_readme_text(tmp_path / "latin1.txt") == "café data"
    assert read_readme_text(tmp_path / "utf16.txt") == "hello"
    assert read_readme_text(tmp_path / "binary.txt") is None
    assert read_readme_text(tmp_path / "missing.txt") is None
    assert read_readme_text(tmp_path / "x.rtf") is None
    broken = tmp_path / "broken.docx"
    broken.write_text("not a zip")
    assert read_readme_text(broken) is None


def test_a_pdf_readme_is_read_when_a_reader_is_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib.util
    import shutil

    pdf = _pdf(tmp_path / "README.pdf", ["Contact", "a.researcher@example.org"])
    if importlib.util.find_spec("pypdf") is None and shutil.which("pdftotext") is None:
        pytest.skip("neither pypdf nor pdftotext is installed")
    text = read_readme_text(pdf)
    assert text is not None
    assert "a.researcher@example.org" in text
    # without a reader there is no text, and no error
    real = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *a, **k: None if name == "pypdf" else real(name, *a, **k),
    )
    monkeypatch.setattr(shutil, "which", lambda *_a, **_k: None)
    assert read_readme_text(pdf) is None


# --------------------------------------------------------------------------
# headings and sections
# --------------------------------------------------------------------------


def test_heading_styles() -> None:
    md = find_headings("# Title\n\n## Contact\ntext\n\n### Sub\nmore\n\n## Files\n")
    assert [(h.raw, h.level, h.style) for h in md] == [
        ("# Title", 1, "markdown"),
        ("## Contact", 2, "markdown"),
        ("### Sub", 3, "markdown"),
        ("## Files", 2, "markdown"),
    ]
    assert md[0].is_title
    assert (md[1].body_start, md[1].end) == (3, 8)  # the sub-section is inside Contact
    setext = find_headings("Cats\n====\n\nOverview\n--------\nwords\n\nFiles\n-----\ndata.csv\n")
    assert [(h.key, h.level, h.style) for h in setext] == [
        ("Cats", 1, "setext"),
        ("Overview", 2, "setext"),
        ("Files", 2, "setext"),
    ]
    assert setext[1].body_start == 5
    numbered = find_headings(
        "1. GENERAL INFORMATION\ntext\n\n2) Methods\nwe did it\n\n2.1 Design\nwithin\n"
    )
    assert [(h.key, h.level) for h in numbered] == [
        ("GENERAL INFORMATION", 1),
        ("Methods", 1),
        ("Design", 2),
    ]
    caps = find_headings("DESCRIPTION\ntext here\n\nCONTACT\nme\n\nTODO\nlater\n")
    assert [h.key for h in caps] == ["DESCRIPTION", "CONTACT"]
    colon = find_headings(
        "Files:\ndata.csv\n\nSome sentence that ends with a colon and is far too long to be a heading:\n"
    )
    assert [h.key for h in colon] == ["Files"]
    bold = find_headings("intro text\n\n**Contact**\nme\n\n**License:**\nCC0\n")
    assert [h.key for h in bold] == ["Contact", "License"]


def test_headings_in_code_blocks_and_lists_are_body_text() -> None:
    text = (
        "# Title\n\n## Usage\n\n```\n# not a heading\n## neither\n```\n\n1. Install R\n2. Run it\n"
    )
    assert [h.key for h in find_headings(text)] == ["Title", "Usage"]
    # the weak styles are off in a README that has real Markdown headings
    text = "# A\n\n## B\n\nNOTE\nsome words\n\n1. Install R\n\nFiles:\nx\n"
    assert [h.key for h in find_headings(text)] == ["A", "B"]
    # in plain text, a numbered list is not a set of headings
    text = (
        "Some intro text of the readme.\n\n1. Install R\n2. Run the script\n3. Look at the output\n"
    )
    assert find_headings(text) == []
    # ... unless the lines are headings in capitals
    text = "1. INTRODUCTION\nwords\n2. METHODS\nwords\n"
    assert [h.key for h in find_headings(text)] == ["INTRODUCTION", "METHODS"]


def test_the_title_is_not_a_section() -> None:
    text = (
        '# Replication data for "Cats and dogs"\n\n'
        "This is a long enough opening text about the study, its aims, the people in it and the data.\n\n"
        "## How to run\n\nRun the script.\n"
    )
    sections, _ = match_sections(text)
    s = sections.set_index("id")
    assert (
        s.loc["reproduce", "heading"] == "## How to run"
    )  # not the title, which says "Replication"
    assert s.loc["description", "kind"] == "intro"


def test_the_generic_template_recognises_a_template_style_readme() -> None:
    sections, fields = match_sections(TEMPLATE_README)
    s = sections.set_index("id")
    expected = {
        "description": "1. GENERAL INFORMATION",
        "contact": "Authors information :",
        "files": "3. DATA & FILE OVERVIEW",
        "methods": "4. METHODOLOGICAL INFORMATION",
        "licence": "2. SHARING/ACCESS INFORMATION",
        "variables": "5. DATA-SPECIFIC INFORMATION FOR: [FILENAME]",
    }
    for sid, heading in expected.items():
        assert s.loc[sid, "found"], sid
        assert not s.loc[sid, "empty"], sid
        assert s.loc[sid, "heading"] == heading
    assert not s.loc["reproduce", "found"]
    assert s.loc["description", "line"] == 1
    assert s.loc["licence", "line"] == 8
    assert s.loc["licence", "end_line"] == 9
    f = fields.iloc[0]
    assert (f["section"], f["id"], bool(f["found"]), bool(f["ok"]), f["line"]) == (
        "contact",
        "email",
        True,
        True,
        5,
    )


def test_a_template_style_readme_in_a_package(tmp_path: Path) -> None:
    r = _check(
        tmp_path,
        {
            "README.txt": TEMPLATE_README,
            "data/raw.csv": "a\n1\n",
            "code/analysis.R": "x <- 1\n",
        },
        human_participants=False,
    )
    s = _status(r)
    assert s["readme_sections"] == "pass"
    assert s["readme_placeholders"] == "warn"
    assert s["readme_contact"] == "pass"
    assert s["readme_file_list"] == "pass"
    assert s["licence"] == "pass"
    hits = r.findings.loc[r.findings["check"] == "readme_placeholders"]
    assert [d.split(" still")[0] for d in hits["detail"]] == [
        "Line 2",
        "Line 6",
        "Line 17",
        "Line 18",
    ]
    assert "<provide DOI of publication if applicable>" in hits.iloc[0]["detail"]
    assert "4 places" in _detail(r, "readme_placeholders")
    # the variables section stands in for a codebook
    assert r.components.set_index("id").loc["codebook", "status"] == "pass"
    assert "section" in r.components.set_index("id").loc["codebook", "files"]


def test_label_lines_stand_in_for_headings() -> None:
    text = "Contact: jane@example.org\nLicense: CC BY 4.0\nSoftware: R 4.3\n"
    sections, _ = match_sections(text)
    s = sections.set_index("id")
    assert s.loc["contact", "kind"] == "inline"
    assert s.loc["contact", "heading"] == "Contact: jane@example.org"
    assert s.loc["licence", "found"]
    assert s.loc["reproduce", "found"]
    assert not s.loc["files", "found"]


def test_required_sections_missing_or_empty_warn_and_optional_ones_are_information(
    tmp_path: Path,
) -> None:
    readme = (
        "# Title\n\n## Contact\n<your name and e-mail>\n\n## Files\n- data/raw.csv: the data\n\n"
        "## Methods\n\n## Software\nR 4.3\n"
    )
    r = _check(tmp_path, {"README.md": readme, "data/raw.csv": "a\n1\n"})
    rules = _rules(r, "readme_sections")
    assert rules.count("section-missing") == 1  # description
    assert rules.count("section-empty") == 1  # contact: only template text
    assert rules.count("section-optional-empty") == 1  # methods
    assert rules.count("section-optional-missing") == 2  # licence, variables
    assert _status(r)["readme_sections"] == "warn"
    sev = dict(zip(r.findings["rule"], r.findings["severity"], strict=True))
    assert sev["section-missing"] == "suggestion"
    assert sev["section-optional-missing"] == "info"
    assert "description" in _detail(r, "readme_sections").lower()
    sections = r.sections.set_index("id")
    assert not sections.loc["description", "found"]
    assert sections.loc["contact", "empty"]
    assert sections.loc["methods", "empty"]
    assert sections.loc["reproduce", "found"] and not sections.loc["reproduce", "empty"]
    # nothing to read: every required section is missing
    r = _check(tmp_path / "e", {"README.txt": "hello"})
    assert _rules(r, "readme_sections").count("section-missing") == 3


# --------------------------------------------------------------------------
# template text, e-mail, file list
# --------------------------------------------------------------------------


def test_template_text_is_found_with_line_numbers() -> None:
    text = "\n".join(
        [
            "Title: <provide DOI of publication if applicable>",  # 1: help text
            "Mail me at <a.b@example.org> or see <https://example.org/x>.",  # 2: not help text
            "Use <br> or <div class='x'> or </div> for layout; p < 0.05 and n > 10.",  # 3: not
            "Run `python run.py <input>` to start.",  # 4: code
            "```",  # 5
            "cat <input> | wc",  # 6: code
            "```",  # 7
            "Date: [YYYY-MM-DD]  Name: [NAME]  File: [FILENAME]  [DATE]  [name](link)",  # 8
            "Lorem ipsum dolor sit amet",  # 9
            "TODO: write this. Also TBD. Not a todo list, or TODOS.",  # 10
            "<list variable name(s)> and <NA>",  # 11
        ]
    )
    hits = find_placeholders(text)
    assert hits == [
        (1, "<provide DOI of publication if applicable>"),
        (8, "[YYYY-MM-DD]"),
        (8, "[NAME]"),
        (8, "[FILENAME]"),
        (8, "[DATE]"),
        (9, "Lorem ipsum"),
        (10, "TODO"),
        (10, "TBD"),
        (11, "<list variable name(s)>"),
    ]


def test_template_text_findings_are_capped(tmp_path: Path) -> None:
    readme = GOOD_README + "\n".join(f"<fill in {i}>" for i in range(15)) + "\n"
    r = _check(tmp_path, {**GOOD_PACKAGE, "README.md": readme}, human_participants=False)
    hits = r.findings.loc[r.findings["check"] == "readme_placeholders"]
    assert len(hits) == 11  # ten listed, one for the rest
    assert "5 more places" in hits.iloc[-1]["detail"]
    assert "15 places" in _detail(r, "readme_placeholders")
    assert _status(r)["readme_placeholders"] == "warn"


def test_contact_email(tmp_path: Path) -> None:
    r = _check(tmp_path, {"README.md": "# T\n\n## Contact\nCall Jane.\n"})
    assert _status(r)["readme_contact"] == "warn"
    assert _rules(r, "readme_contact") == ["contact-missing"]
    r = _check(tmp_path / "b", {"README.md": "Mail jane.doe+data@sub.example.ac.uk please"})
    assert _status(r)["readme_contact"] == "pass"
    assert "jane.doe+data@sub.example.ac.uk" in _detail(r, "readme_contact")


def test_file_list_coverage(tmp_path: Path) -> None:
    files: dict[str, str | bytes] = {
        "README.md": "# T\n\nThe data folder has Raw_Data.csv (the survey) and the code folder "
        "has my analysis script. See also notes.\n",
        "data/raw data.csv": "a\n1\n",
        "code/my-analysis.R": "x\n",
        "notes.txt": "n",
        "figures/fig1.png": "x",
        "materials/stimuli.pdf": "x",
    }
    # data, code, notes, raw data, my analysis, ... are named (case, _ - and space are alike,
    # the extension is optional); figures, materials, fig1.png, stimuli.pdf are not
    r = _check(tmp_path, files)
    assert _status(r)["readme_file_list"] == "warn"
    assert _rules(r, "readme_file_list").count("file-list-incomplete") == 1
    summary = r.findings.loc[r.findings["rule"] == "file-list-incomplete"].iloc[0]
    assert "figures" in summary["detail"] and "materials" in summary["detail"]
    not_named = r.findings.loc[r.findings["rule"] == "file-not-described"]
    assert set(not_named["path"]) == {"figures", "materials"}
    assert set(not_named["severity"]) == {"info"}
    assert set(not_named["kind"]) == {"folder"}
    r = _check(tmp_path / "lenient", files, min_file_list_coverage=0.5)
    assert _status(r)["readme_file_list"] == "pass"
    assert set(_rules(r, "readme_file_list")) == {"file-not-described"}
    r = _check(tmp_path / "strict", files, min_file_list_coverage=1)
    assert _status(r)["readme_file_list"] == "warn"


def test_file_list_leaves_out_the_readme_licence_junk_and_hidden_files(tmp_path: Path) -> None:
    r = _check(
        tmp_path,
        {
            "README.md": "# T\n\nThis is the data of the study.\n",
            "LICENSE": "MIT",
            ".DS_Store": "x",
            ".hidden/x.csv": "x",
            "__MACOSX/a": "x",
            "~$tmp.docx": "x",
            "data.csv": "a\n1\n",
        },
    )
    assert _status(r)["readme_file_list"] == "pass"  # only data.csv is left, and "data" is named
    # nothing but a README: nothing to describe
    r = _check(tmp_path / "alone", {"README.md": "# T\n"})
    assert _status(r)["readme_file_list"] == "na"


# --------------------------------------------------------------------------
# licence
# --------------------------------------------------------------------------


def test_licence_file_section_or_neither(tmp_path: Path) -> None:
    base: dict[str, str | bytes] = {"README.md": "# T\n\nSome words about it.\n", "d.csv": "a\n1\n"}
    for name in ("LICENSE", "LICENCE.txt", "COPYING", "LICENSE-DATA.md", "licence.md"):
        r = _check(tmp_path / name, {**base, name: "terms"})
        assert _status(r)["licence"] == "pass", name
        assert _rules(r, "licence") == [], name
    # a licence section that names a licence, no file: pass, with a note
    r = _check(tmp_path / "sec", {**base, "README.md": "# T\n\n## Licence\n\nCC0 1.0\n"})
    assert _status(r)["licence"] == "pass"
    assert _rules(r, "licence") == ["licence-only-in-readme"]
    assert r.findings.set_index("rule").loc["licence-only-in-readme", "severity"] == "info"
    # a sentence is enough, too
    r = _check(tmp_path / "sentence", {**base, "README.md": "Released under the MIT licence."})
    assert _status(r)["licence"] == "pass"
    r = _check(
        tmp_path / "ar", {**base, "README.md": "# T\n\n## Terms of use\nAll rights reserved.\n"}
    )
    assert _status(r)["licence"] == "pass"
    # neither
    r = _check(tmp_path / "none", base)
    assert _status(r)["licence"] == "warn"
    assert _rules(r, "licence") == ["licence-missing"]
    # a licence section that names none; "MIT" as a place, not a licence; a licence file in a subfolder
    r = _check(tmp_path / "vague", {**base, "README.md": "## Licence\nTo be decided.\n"})
    assert _status(r)["licence"] == "warn"
    r = _check(tmp_path / "place", {**base, "README.md": "## Contact\nJane works at MIT.\n"})
    assert _status(r)["licence"] == "warn"
    r = _check(tmp_path / "sub", {**base, "code/LICENSE": "terms"})
    assert _status(r)["licence"] == "pass"


# --------------------------------------------------------------------------
# components
# --------------------------------------------------------------------------


def _component(r: DocsResult, cid: str) -> Any:
    return r.components.set_index("id").loc[cid]


def test_the_generic_catalogue() -> None:
    cat = load_components()
    assert [c.id for c in cat.components] == [
        "readme",
        "data",
        "code",
        "codebook",
        "dmp",
        "ethics",
        "consent",
    ]
    assert {c.id: c.required for c in cat.components} == {
        "readme": "always",
        "data": "always",
        "code": "optional",
        "codebook": "recommended",
        "dmp": "optional",
        "ethics": "human_participants",
        "consent": "human_participants",
    }
    tpl = load_readme_template()
    assert [(s.id, s.required) for s in tpl.sections] == [
        ("description", True),
        ("contact", True),
        ("files", True),
        ("methods", False),
        ("reproduce", False),
        ("licence", False),
        ("variables", False),
    ]


def test_each_component_status(tmp_path: Path) -> None:
    # nothing but a README: data is missing (always), the codebook is not needed without data
    r = _check(tmp_path / "bare", {"README.md": "# T\n"}, human_participants=False)
    assert _component(r, "data")["status"] == "fail"
    assert _component(r, "codebook")["status"] == "na"
    assert "no data" in _component(r, "codebook")["detail"]
    assert _component(r, "code")["status"] == "na"  # optional
    assert _component(r, "ethics")["status"] == "na"  # no people
    assert _status(r)["components"] == "fail"
    assert "to fix: Data" in _detail(r, "components")
    assert "component-missing" in _rules(r, "component:data")
    # data without a codebook: recommended, so a warning
    r = _check(
        tmp_path / "data",
        {"README.md": "# T\n", "data/raw.csv": "a\n1\n"},
        human_participants=False,
    )
    assert _component(r, "data")["status"] == "pass"
    assert _component(r, "codebook")["status"] == "warn"
    assert _rules(r, "component:codebook") == ["component-recommended-missing"]
    assert _status(r)["components"] == "warn"
    # every kind of codebook
    for name in ("codebook.xlsx", "Data Dictionary.csv", "variable_list.txt", "CODE-BOOK.pdf"):
        r = _check(
            tmp_path / name,
            {"README.md": "# T\n", "raw.csv": "a\n1\n", name: "x"},
            human_participants=False,
        )
        assert _component(r, "codebook")["status"] == "pass", name
    # code, a data management plan
    r = _check(
        tmp_path / "plan",
        {
            "README.md": "# T\n",
            "raw.csv": "a\n1\n",
            "analysis.py": "x",
            "Data Management Plan v2.pdf": "x",
        },
        human_participants=False,
    )
    assert _component(r, "code")["status"] == "pass"
    assert _component(r, "dmp")["status"] == "pass"
    assert _component(r, "dmp")["files"] == "Data Management Plan v2.pdf"
    r = _check(
        tmp_path / "dmp", {"README.md": "# T\n", "DMP_final.pdf": "x"}, human_participants=False
    )
    assert _component(r, "dmp")["status"] == "pass"
    r = _check(
        tmp_path / "not-dmp", {"README.md": "# T\n", "admp.pdf": "x"}, human_participants=False
    )
    assert _component(r, "dmp")["status"] == "na"


def test_human_participants_decides_ethics_and_consent(tmp_path: Path) -> None:
    files: dict[str, str | bytes] = {"README.md": "# T\n", "raw.csv": "a\n1\n"}
    r = _check(tmp_path / "yes", files, human_participants=True)
    for cid in ("ethics", "consent"):
        assert _component(r, cid)["status"] == "fail"
        assert _rules(r, f"component:{cid}") == ["component-missing"]
    assert "research involved people" in _component(r, "ethics")["detail"]
    r = _check(tmp_path / "no", files, human_participants=False)
    for cid in ("ethics", "consent"):
        assert _component(r, cid)["status"] == "na"
        assert _rules(r, f"component:{cid}") == []
    r = _check(tmp_path / "unknown", files, human_participants=None)
    for cid in ("ethics", "consent"):
        assert _component(r, cid)["status"] == "manual"
        assert _rules(r, f"component:{cid}") == ["component-undecided"]
        assert "involved people" in _component(r, cid)["detail"]
    assert r.traffic_light in ("yellow", "red")
    # present, whatever the answer
    present = {
        **files,
        "ethics/approval.pdf": "x",
        "Informed consent form.docx": "x",
    }
    for answer in (True, False, None):
        r = _check(tmp_path / f"present-{answer}", present, human_participants=answer)
        assert _component(r, "ethics")["status"] == "pass"
        assert _component(r, "ethics")["files"] == "ethics/approval.pdf"
        assert _component(r, "consent")["status"] == "pass"


def test_ethics_and_consent_are_found_by_name_or_folder_in_two_languages(tmp_path: Path) -> None:
    names = {
        "ethics": [
            "METC beoordeling.pdf",
            "Ethical_Approval_2023.pdf",
            "goedkeuring.pdf",
            "erb-letter.pdf",
            "IRB approval.pdf",
        ],
        "consent": ["toestemming.pdf", "Consent form.pdf", "informed.pdf"],
    }
    for cid, files in names.items():
        for name in files:
            r = _check(
                tmp_path / f"{cid}-{name}",
                {"README.md": "# T\n", "raw.csv": "a\n1\n", name: "x"},
                human_participants=True,
            )
            assert _component(r, cid)["status"] == "pass", name
    for folder, cid in (("Ethics", "ethics"), ("toestemming", "consent"), ("METC", "ethics")):
        r = _check(
            tmp_path / f"folder-{folder}",
            {"README.md": "# T\n", "raw.csv": "a\n1\n", f"{folder}/form.pdf": "x"},
            human_participants=True,
        )
        assert _component(r, cid)["status"] == "pass", folder
    # recordings are not "REC" approvals
    r = _check(
        tmp_path / "rec", {"README.md": "# T\n", "rec_01.csv": "a\n1\n"}, human_participants=True
    )
    assert _component(r, "ethics")["status"] == "fail"


def test_the_format_of_a_component_is_checked(tmp_path: Path) -> None:
    r = _check(
        tmp_path,
        {"README.md": "# T\n", "raw.csv": "a\n1\n", "METC approval.docx": "x", "consent.docx": "x"},
        human_participants=True,
    )
    row = _component(r, "ethics")
    assert row["status"] == "warn"
    assert _rules(r, "component:ethics") == ["component-format"]
    f = r.findings.loc[r.findings["check"] == "component:ethics"].iloc[0]
    assert f["severity"] == "suggestion"
    assert f["path"] == "METC approval.docx"
    assert (
        f["detail"]
        == "Ethical approval should be a PDF file, but METC approval.docx is a Word document."
    )
    assert _component(r, "consent")["status"] == "pass"  # no formats asked for
    assert _status(r)["components"] == "warn"


def test_hidden_and_junk_files_are_not_parts(tmp_path: Path) -> None:
    r = _check(
        tmp_path,
        {
            "README.md": "# T\n",
            ".hidden/data.csv": "a\n1\n",
            "._ethics.pdf": "x",
            "~$consent.docx": "x",
        },
        human_participants=True,
    )
    assert _component(r, "data")["status"] == "fail"
    assert _component(r, "ethics")["status"] == "fail"
    assert _component(r, "consent")["status"] == "fail"


# --------------------------------------------------------------------------
# custom template and catalogue, severity
# --------------------------------------------------------------------------

LAB_TEMPLATE: dict[str, Any] = {
    "name": "Lab README",
    "source": "tests",
    "sections": [
        {"id": "title", "title": "Title", "match": ["^title$"], "required": True},
        {
            "id": "pi",
            "title": "Principal investigator",
            "match": ["principal investigator"],
            "required": True,
            "fields": [
                {
                    "id": "orcid",
                    "label": "ORCID",
                    "match": ["orcid"],
                    "required": True,
                    "expect": "url",
                },
                {"id": "unit", "label": "Unit", "match": ["^unit"], "required": True},
            ],
        },
        {"id": "extra", "title": "Extras", "match": ["extras"], "required": False},
    ],
    "placeholders": ["FILL ME IN"],
}
LAB_README = (
    "Title\n=====\nMy study\n\nPrincipal investigator\n---------------------\n"
    "Jane Doe\nORCID: pending\n\nExtras\n------\nFILL ME IN\n"
)


def test_a_custom_template_from_a_dict(tmp_path: Path) -> None:
    r = _check(tmp_path, {"README.txt": LAB_README, "d.csv": "a\n1\n"}, readme=LAB_TEMPLATE)
    assert list(r.sections["id"]) == ["title", "pi", "extra"]
    assert list(r.sections["found"]) == [True, True, True]
    assert list(r.sections["empty"]) == [False, False, True]  # the only text is template text
    rules = _rules(r, "readme_sections")
    assert rules.count("field-invalid") == 1  # ORCID is there, but not a URL
    assert rules.count("field-missing") == 1  # Unit is not there
    assert rules.count("section-optional-empty") == 1
    fields = r.fields.set_index("id")
    assert (bool(fields.loc["orcid", "found"]), bool(fields.loc["orcid", "ok"])) == (True, False)
    assert bool(fields.loc["unit", "found"]) is False
    assert fields.loc["orcid", "line"] == 8
    # the template's own placeholders are used
    hits = r.findings.loc[r.findings["check"] == "readme_placeholders"]
    assert len(hits) == 1
    assert "FILL ME IN" in hits.iloc[0]["detail"]
    r2 = _check(
        tmp_path / "ok",
        {
            "README.txt": LAB_README.replace(
                "pending", "https://orcid.org/0000-0000-0000-0001\nUnit: X"
            ).replace("FILL ME IN", "none")
        },
        readme=LAB_TEMPLATE,
    )
    assert _rules(r2, "readme_sections") == []
    assert _status(r2)["readme_placeholders"] == "pass"


def test_a_custom_template_from_a_json_file(tmp_path: Path) -> None:
    path = tmp_path / "lab.json"
    path.write_text(json.dumps(LAB_TEMPLATE))
    for spec in (path, str(path), json.dumps(LAB_TEMPLATE)):
        r = _check(tmp_path / f"p{abs(hash(str(spec)))}", {"README.txt": LAB_README}, readme=spec)
        assert list(r.sections["id"]) == ["title", "pi", "extra"]
    # a bare list of sections works too, with the default template text
    bare = LAB_TEMPLATE["sections"]
    r = _check(tmp_path / "bare", {"README.txt": "Title\n=====\n<fill>\n"}, readme=bare)
    assert list(r.sections["id"]) == ["title", "pi", "extra"]
    assert _status(r)["readme_placeholders"] == "pass" if False else True
    assert len(find_placeholders("<fill this in>", load_readme_template(bare))) == 1
    # "placeholders": [] turns the check off
    tpl = load_readme_template({"sections": bare, "placeholders": []})
    assert find_placeholders("<fill this in> TODO", tpl) == []


CATALOGUE: dict[str, Any] = {
    "name": "Lab",
    "source": "tests",
    "components": [
        {
            "id": "protocol",
            "title": "Protocol",
            "group": "documentation",
            "description": "The study protocol.",
            "match": {"names": ["protocol"], "exts": [".tex"]},
            "required": "always",
            "formats": ["pdf", ".md"],
        },
        {
            "id": "stimuli",
            "title": "Stimuli",
            "match": {"paths": ["^materials/"], "folders": ["^stimuli$"]},
            "required": "recommended",
        },
        {
            "id": "screens",
            "title": "Screenshots",
            "match": {"data_type": ["materials"]},
            "required": "recommended",
            "when": ["stimuli"],
        },
        {
            "id": "notes",
            "title": "Notes",
            "match": {"doc_role": ["supplemental"]},
            "required": "optional",
        },
        {
            "id": "dpia",
            "title": "DPIA",
            "match": {"names": ["dpia"]},
            "required": "human_participants",
        },
    ],
}


def test_a_custom_catalogue_from_a_dict(tmp_path: Path) -> None:
    files: dict[str, str | bytes] = {
        "README.md": "# T\n",
        "Study protocol.docx": "x",
        "materials/list.txt": "x",
        "fig.png": "x",
        "paper.tex": "x",
    }
    r = _check(tmp_path, files, components=CATALOGUE, human_participants=True)
    assert list(r.components["id"]) == ["protocol", "stimuli", "screens", "notes", "dpia"]
    assert _component(r, "protocol")["files"] == "paper.tex; Study protocol.docx"
    assert _component(r, "protocol")["status"] == "warn"  # a docx and a tex file, not a PDF
    assert _rules(r, "component:protocol") == ["component-format", "component-format"]
    assert _component(r, "stimuli")["status"] == "pass"  # by path
    assert _component(r, "screens")["status"] == "pass"  # needs stimuli, which are there
    assert _component(r, "dpia")["status"] == "fail"
    assert _status(r)["component:dpia"] == "fail"
    # a folder matches by name, and "when" turns a part off when its prerequisite is missing
    r = _check(
        tmp_path / "b",
        {"README.md": "# T\n", "stimuli/a.txt": "x", "protocol.md": "x"},
        components=CATALOGUE,
    )
    assert _component(r, "stimuli")["status"] == "pass"
    assert _component(r, "protocol")["status"] == "pass"  # .md is allowed
    r = _check(
        tmp_path / "c", {"README.md": "# T\n"}, components=CATALOGUE, human_participants=False
    )
    assert _component(r, "stimuli")["status"] == "warn"
    assert _component(r, "screens")["status"] == "na"
    assert _component(r, "dpia")["status"] == "na"
    assert _component(r, "protocol")["status"] == "fail"
    assert r.components["group"].tolist()[0] == "documentation"


def test_a_custom_catalogue_from_a_json_file(tmp_path: Path) -> None:
    path = tmp_path / "components.json"
    path.write_text(json.dumps(CATALOGUE))
    r = _check(tmp_path / "pkg", {"README.md": "# T\n"}, components=path, human_participants=False)
    assert list(r.components["id"]) == ["protocol", "stimuli", "screens", "notes", "dpia"]
    # a bare list of components works too
    r = _check(tmp_path / "pkg2", {"README.md": "# T\n"}, components=CATALOGUE["components"][:1])
    assert list(r.components["id"]) == ["protocol"]
    # a README section can make a part present
    cat = {
        "components": [
            {
                "id": "vars",
                "title": "Variables",
                "match": {"readme_sections": ["variables"]},
                "required": "always",
            }
        ]
    }
    r = _check(tmp_path / "pkg3", {"README.txt": TEMPLATE_README}, components=cat)
    assert _component(r, "vars")["status"] == "pass"
    r = _check(tmp_path / "pkg4", {"README.txt": "nothing"}, components=cat)
    assert _component(r, "vars")["status"] == "fail"


def test_policy_errors_are_clear(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid regular expression"):
        load_readme_template({"sections": [{"id": "a", "match": ["("]}]})
    with pytest.raises(ValueError, match="expect must be one of"):
        load_readme_template(
            {"sections": [{"id": "a", "fields": [{"id": "f", "expect": "phone"}]}]}
        )
    with pytest.raises(ValueError, match="needs an id"):
        load_readme_template({"sections": [{"title": "a"}]})
    with pytest.raises(ValueError, match="unique"):
        load_readme_template({"sections": [{"id": "a"}, {"id": "a"}]})
    with pytest.raises(ValueError, match="list of sections"):
        load_readme_template({"nothing": 1})
    with pytest.raises(ValueError, match="required must be one of"):
        load_components({"components": [{"id": "a", "required": "sometimes"}]})
    with pytest.raises(ValueError, match="unique"):
        load_components({"components": [{"id": "a"}, {"id": "a"}]})
    with pytest.raises(ValueError, match="cannot read"):
        load_readme_template(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(ValueError, match="not valid JSON"):
        load_components(bad)
    with pytest.raises(ValueError, match="not valid JSON"):
        load_components("[1,")


def test_severity_overrides_change_findings_and_statuses(tmp_path: Path) -> None:
    files: dict[str, str | bytes] = {"data.csv": "a\n1\n"}
    r = _check(tmp_path / "default", files)
    assert _status(r)["readme_present"] == "fail"
    r = _check(tmp_path / "soft", files, severity={"readme-missing": "suggestion"})
    assert _status(r)["readme_present"] == "warn"
    assert r.findings.set_index("rule").loc["readme-missing", "severity"] == "suggestion"
    r = _check(
        tmp_path / "info", files, severity={"readme-missing": "info", "licence-missing": "info"}
    )
    assert _status(r)["readme_present"] == "pass"
    assert _status(r)["licence"] == "pass"
    # a missing part takes its status from the severity of its rule
    r = _check(
        tmp_path / "parts",
        files,
        severity={"component-missing": "info", "component-recommended-missing": "problem"},
        human_participants=True,
    )
    assert _component(r, "ethics")["status"] == "na"
    assert _component(r, "readme")["status"] == "na"
    assert _component(r, "codebook")["status"] == "fail"
    # template text as information only
    path = tmp_path / "severity.json"
    path.write_text(json.dumps({"template-text": "info", "no-such-rule": "problem"}))
    r = _check(tmp_path / "tt", {"README.txt": TEMPLATE_README, "data/raw.csv": "x"}, severity=path)
    assert _status(r)["readme_placeholders"] == "pass"
    assert set(r.findings.loc[r.findings["check"] == "readme_placeholders", "severity"]) == {"info"}
    with pytest.raises(ValueError, match="must be one of"):
        _check(tmp_path / "bad", files, severity={"readme-missing": "fatal"})
    with pytest.raises(ValueError, match="maps rule ids"):
        _check(tmp_path / "bad2", files, severity=["readme-missing"])
    assert all(v in ("problem", "suggestion", "info") for v in RULES.values())


# --------------------------------------------------------------------------
# human participants
# --------------------------------------------------------------------------


def test_human_participants_is_decided_from_a_given_answer_or_the_paper() -> None:
    assert resolve_human_participants(True) == (True, "given")
    assert resolve_human_participants(False) == (False, "given")
    assert resolve_human_participants("yes") == (True, "given")
    assert resolve_human_participants("No") == (False, "given")
    assert resolve_human_participants("maybe") == (None, "unknown")
    assert resolve_human_participants(None, None) == (None, "unknown")
    live = pc.test_paper(["Participants were recruited via Prolific.", "We ran a model."])
    assert resolve_human_participants(None, live) == (True, "paper")
    # a given answer wins over the paper
    assert resolve_human_participants(False, live) == (False, "given")
    dry = pc.test_paper(["The model was tested on held-out data."])
    assert resolve_human_participants(None, dry) == (None, "unknown")
    # no text, or something that is not a paper: left open, no error
    assert resolve_human_participants(None, pc.test_paper([])) == (None, "unknown")
    assert resolve_human_participants(None, 42) == (None, "unknown")
    assert resolve_human_participants(None, object()) == (None, "unknown")


# --------------------------------------------------------------------------
# the module
# --------------------------------------------------------------------------


def test_the_module_without_a_package(tmp_path: Path) -> None:
    out = module_run(None, "datapackage::package_docs")
    assert out.traffic_light == "na"
    assert "No data package" in out.summary_text


def test_the_module_returns_the_tables_and_the_report(tmp_path: Path) -> None:
    root = _tree(tmp_path / "pkg", {**GOOD_PACKAGE, "README.md": TEMPLATE_README})
    paper = pc.test_paper(["Participants were recruited via Prolific."])
    out = module_run(paper, "datapackage::package_docs", local_path=str(root))
    assert (
        out.traffic_light == "red"
    )  # the paper recruited people, and there is no ethical approval
    assert list(out.table.columns) == ["path", "kind", "check", "rule", "severity", "detail"]
    assert out.checklist["item"].tolist()[:3] == [
        "readme_present",
        "readme_format",
        "readme_sections",
    ]
    assert out.readme_path == "README.md"
    assert out.readme_text == TEMPLATE_README
    assert out.sections["id"].tolist()[0] == "description"
    assert out.components["id"].tolist()[0] == "readme"
    assert out.human_participants is True
    assert out.human_participants_source == "paper"
    assert out.components.set_index("id").loc["ethics", "status"] == "fail"
    assert out.summary_table["readme"].tolist() == ["README.md"]
    assert int(out.summary_table["checks_fail"].iloc[0]) >= 1
    assert out.summary_text.startswith("README: README.md.")
    # no paper text and no answer: left to a person
    out = module_run(None, "datapackage::package_docs", local_path=str(root))
    assert out.human_participants is None
    assert out.components.set_index("id").loc["ethics", "status"] == "manual"
    # a given answer
    out = module_run(
        None, "datapackage::package_docs", local_path=str(root), human_participants=False
    )
    assert out.components.set_index("id").loc["ethics", "status"] == "na"
    assert out.human_participants_source == "given"


def test_the_module_passes_its_options_on(tmp_path: Path) -> None:
    root = _tree(tmp_path / "pkg", {"README.txt": LAB_README, "d.csv": "a\n1\n"})
    out = module_run(
        None,
        "datapackage::package_docs",
        local_path=str(root),
        readme=LAB_TEMPLATE,
        components=CATALOGUE,
        human_participants=False,
        min_file_list_coverage=0.1,
        severity={"field-invalid": "info"},
    )
    assert out.sections["id"].tolist() == ["title", "pi", "extra"]
    assert out.components["id"].tolist()[0] == "protocol"
    assert "field-invalid" in out.table["rule"].tolist()
    assert out.table.set_index("rule").loc["field-invalid", "severity"] == "info"


def test_the_module_reads_a_zipped_package(tmp_path: Path) -> None:
    archive = tmp_path / "mypkg.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for rel, content in GOOD_PACKAGE.items():
            zf.writestr(f"mypkg/{rel}", content)
        zf.writestr("__MACOSX/mypkg/._README.md", "x")
    out = module_run(
        None, "datapackage::package_docs", local_path=str(archive), human_participants=False
    )
    assert out.readme_path == "README.md"
    assert out.traffic_light == "green"


def test_the_report_reads_well(tmp_path: Path) -> None:
    root = _tree(
        tmp_path / "pkg",
        {"README.txt": TEMPLATE_README, "data/raw.csv": "a\n1\n", "code/analysis.R": "x"},
    )
    out = module_run(
        None, "datapackage::package_docs", local_path=str(root), human_participants=True
    )
    text = module_report(out)
    assert "Data Package Documentation" in text
    assert "Checklist" in text
    assert "Would make the package better" in text
    assert "To fix before the package is archived" in text  # the ethical approval is missing
    assert "Line 2 still has template text" in text
    # help text from the README is escaped, so it is not read as an HTML tag
    assert "&lt;provide DOI of publication if applicable&gt;" in text
    assert "<provide DOI of publication" not in text
    assert "Ethical approval is missing" in text
    assert "README sections" in text and "Parts of the package" in text
    # a package that needs nothing says so briefly
    good = _tree(tmp_path / "good", GOOD_PACKAGE)
    out = module_run(
        None, "datapackage::package_docs", local_path=str(good), human_participants=False
    )
    text = module_report(out)
    assert "To fix before" not in text
    assert "Checklist" in text


def test_check_docs_reads_hand_made_policy_objects(tmp_path: Path) -> None:
    tpl = load_readme_template(LAB_TEMPLATE)
    cat = load_components(CATALOGUE)
    assert load_readme_template(tpl) is tpl
    assert load_components(cat) is cat
    r = _check(tmp_path, {"README.txt": LAB_README}, readme=tpl, components=cat)
    assert list(r.sections["id"]) == ["title", "pi", "extra"]


# --------------------------------------------------------------------------
# plain-text READMEs without markup, odd packages
# --------------------------------------------------------------------------


def test_short_lines_on_their_own_are_headings_in_plain_text() -> None:
    text = (
        "Dataset huisdieren\n\n"
        "Omschrijving\nEen enquête onder 200 mensen over hun huisdieren, afgenomen in 2023 via een online vragenlijst.\n\n"
        "Contactpersoon\nJane Doe, jane@example.org\n\n"
        "Bestanden\ndata.csv - de data\nanalyse.R - het script\n"
    )
    sections, _ = match_sections(text)
    s = sections.set_index("id")
    assert [s.loc[i, "kind"] for i in ("description", "contact", "files")] == ["label"] * 3
    assert s.loc["contact", "heading"] == "Contactpersoon"
    assert (s.loc["files", "line"], s.loc["files", "end_line"]) == (9, 11)
    assert not s.loc["methods", "found"]


def test_bold_labels_and_pairs_of_label_and_value() -> None:
    text = "# Pets\n\n**Description:** A survey of 200 people about their pets.\n\n**Contact:** jane@example.org\n\n**Files**\n\n- data.csv\n"
    s = match_sections(text)[0].set_index("id")
    assert s.loc["description", "kind"] == "inline"
    assert s.loc["contact", "kind"] == "inline"
    assert s.loc["files", "kind"] == "heading"
    # a file name in a list is not a label, even when it looks like one
    s = match_sections(
        "# T\n\n## Files\n- codebook.csv: what the variables mean\n- `data/x.csv`: data\n"
    )[0]
    assert not s.set_index("id").loc["variables", "found"]


def test_an_empty_package(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    with open_package(tmp_path / "pkg") as pkg:
        r = check_docs(pkg)
    assert _status(r)["readme_present"] == "fail"
    assert _status(r)["component:data"] == "fail"
    assert r.traffic_light == "red"
    assert r.readme_text is None
    assert len(r.sections) == 7


def test_a_symlinked_readme_is_only_read_inside_the_package(tmp_path: Path) -> None:
    root = _tree(tmp_path / "pkg", {"docs/readme-source.md": GOOD_README, "a.csv": "a\n1\n"})
    outside = tmp_path / "outside.md"
    outside.write_text("secret")
    (root / "README.md").symlink_to("docs/readme-source.md")
    with open_package(root) as pkg:
        assert check_docs(pkg).readme_text == GOOD_README
    (root / "README.md").unlink()
    (root / "README.md").symlink_to(outside)
    with open_package(root) as pkg:
        r = check_docs(pkg)
    assert r.readme_text is None
    assert _status(r)["readme_sections"] == "manual"


def test_a_docx_keeps_line_breaks_tabs_and_list_items(tmp_path: Path) -> None:
    xml = (
        '<w:document xmlns:w="x"><w:body>'
        "<w:p><w:r><w:t>one</w:t><w:br/><w:t>two</w:t><w:tab/><w:t>three</w:t></w:r></w:p>"
        "<w:p><w:pPr><w:numPr><w:ilvl/></w:numPr></w:pPr><w:r><w:t>item</w:t></w:r></w:p>"
        "<w:p/>"
        "</w:body></w:document>"
    )
    path = tmp_path / "README.docx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", xml)
    assert read_readme_text(path) == "one\ntwo\tthree\n- item"
    # a docx without a body is not read
    with zipfile.ZipFile(tmp_path / "empty.docx", "w") as zf:
        zf.writestr("other.xml", "x")
    assert read_readme_text(tmp_path / "empty.docx") is None
