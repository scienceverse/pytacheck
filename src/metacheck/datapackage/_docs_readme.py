"""Read a README and find its sections, template text, e-mail addresses and licences.

See :mod:`metacheck.datapackage.docs`; everything public here is also available from there.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from metacheck.datapackage._docs_policy import (
    _EXPECT,
    ReadmeTemplate,
    SectionSpec,
    _Licence,
    load_readme_template,
)

#: Columns of the ``sections`` table (:func:`match_sections`).
SECTION_COLUMNS = (
    "id",  # the template's section id
    "title",  # the template's title for it
    "required",
    "found",  # the README has it
    "empty",  # found, but nothing in it (only whitespace and template text)
    "heading",  # the heading as written in the README ("" when not found)
    "line",  # its line number (1-based; missing when not found)
    "end_line",  # the last line of its text
    # "heading", "inline" (a "Label: value" line), "label" (a short line on its own), "intro" (the
    # text at the top) or ""
    "kind",
)

#: Columns of the ``fields`` table (:func:`match_sections`).
FIELD_COLUMNS = ("section", "id", "label", "required", "found", "ok", "line")

_MAX_TEXT_BYTES = 2_000_000
_MAX_XML_BYTES = 50_000_000
#: Extensions read as plain text (an empty one is a ``README`` with no extension).
PLAIN_TEXT_EXTS = frozenset({"", "txt", "md", "markdown", "mkd", "rst", "text"})


# --------------------------------------------------------------------------
# reading the README
# --------------------------------------------------------------------------


def read_readme_text(path: str | os.PathLike[str]) -> str | None:
    """The text of a README file, or ``None`` when it cannot be read.

    Text, Markdown and reStructuredText are read as UTF-8 (Latin-1 when that
    fails). Word (``.docx``) and OpenDocument (``.odt``) files are unzipped
    and their text taken from the XML, with headings written as Markdown
    headings. A PDF is read with ``pypdf`` when it is installed, else with
    poppler's ``pdftotext`` when that is on the PATH. Other formats give
    ``None``.
    """
    p = Path(path)
    ext = p.suffix.lower().lstrip(".")
    try:
        if ext in PLAIN_TEXT_EXTS:
            return _plain_text(p)
        if ext == "docx":
            return _docx_text(p)
        if ext == "odt":
            return _odt_text(p)
        if ext == "pdf":
            return _pdf_text(p)
    except Exception:  # a README that cannot be read must not stop the check
        return None
    return None


def _decode(data: bytes) -> str | None:
    if b"\x00" in data and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return None  # binary
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16", errors="replace")
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _plain_text(path: Path) -> str | None:
    with open(path, "rb") as fh:
        return _decode(fh.read(_MAX_TEXT_BYTES))


def _unescape(text: str) -> str:
    import html

    return html.unescape(text)


def _zip_member(path: Path, member: str) -> str | None:
    with zipfile.ZipFile(path) as zf:
        info = zf.getinfo(member)
        if info.file_size > _MAX_XML_BYTES:
            return None
        with zf.open(info) as fh:
            data = fh.read(_MAX_XML_BYTES + 1)  # the declared size may be wrong
        return None if len(data) > _MAX_XML_BYTES else data.decode("utf-8", errors="replace")


_W_PARA = re.compile(r"<w:p[ >].*?</w:p>", re.DOTALL)
_W_TOKEN = re.compile(
    r"<w:t(?:\s[^>]*)?>(.*?)</w:t>|(<w:tab\s*/>)|(<w:br\s*/>|<w:cr\s*/>)", re.DOTALL
)
_W_STYLE = re.compile(r'<w:pStyle\s+w:val="([^"]*)"')
_W_HEADING = re.compile(r"^(?:heading|kop|titel|title)\s*(\d)?$", re.IGNORECASE)


def _docx_text(path: Path) -> str | None:
    xml = _zip_member(path, "word/document.xml")
    if xml is None:
        return None
    lines = []
    for para in _W_PARA.findall(xml):
        parts = []
        for text, tab, brk in _W_TOKEN.findall(para):
            parts.append(_unescape(text) if text else "\t" if tab else "\n" if brk else "")
        line = "".join(parts)
        style = _W_STYLE.search(para)
        heading = _W_HEADING.match(style.group(1)) if style else None
        if heading and line.strip():
            line = "#" * int(heading.group(1) or 1) + " " + line.strip()
        elif "<w:numPr>" in para and line.strip():
            line = "- " + line
        lines.append(line)
    return "\n".join(lines)


_ODF_BLOCK = re.compile(r"<text:(h|p)\b([^>]*?)(?:/>|>(.*?)</text:\1>)", re.DOTALL)
_ODF_TOKEN = re.compile(
    r"<text:s(?:\s+text:c=\"(\d+)\")?\s*/>|(<text:tab\s*/>)|(<text:line-break\s*/>)|<[^>]+>"
)


def _odt_text(path: Path) -> str | None:
    xml = _zip_member(path, "content.xml")
    if xml is None:
        return None
    lines = []
    for kind, attrs, inner in _ODF_BLOCK.findall(xml):

        def token(m: re.Match[str]) -> str:
            if m.group(1) is not None or m.group(0).startswith("<text:s"):
                return " " * int(m.group(1) or 1)
            return "\t" if m.group(2) else "\n" if m.group(3) else ""

        line = _unescape(_ODF_TOKEN.sub(token, inner or ""))
        if kind == "h" and line.strip():
            level = re.search(r'text:outline-level="(\d+)"', attrs)
            line = "#" * int(level.group(1) if level else 1) + " " + line.strip()
        lines.append(line)
    return "\n".join(lines)


def _pdf_text(path: Path) -> str | None:
    import importlib.util

    if importlib.util.find_spec("pypdf") is not None:  # an optional dependency
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages[:100])
    exe = shutil.which("pdftotext")
    if exe is None:
        return None
    done = subprocess.run(  # noqa: S603
        [exe, "-layout", "-l", "100", str(path), "-"],
        capture_output=True,
        timeout=60,
        check=False,
    )
    if done.returncode != 0:
        return None
    return _decode(done.stdout)


# --------------------------------------------------------------------------
# headings and sections of a README
# --------------------------------------------------------------------------


@dataclass
class Heading:
    """A line that works as a heading in a README."""

    index: int  # 0-based line index of the heading text
    raw: str  # the line as written, trimmed
    key: str  # the text to match: no numbering, markup or trailing colon
    level: int  # 1 is the highest
    style: str  # "markdown", "setext", "numbered", "bold", "caps" or "colon"
    body_start: int = 0  # 0-based index of the first line after it (and its underline)
    end: int = 0  # 0-based index after its last line: up to the next heading of its level or higher
    is_title: bool = False  # the README's title: a first level-1 heading above other levels

    @property
    def line(self) -> int:
        """The 1-based line number."""
        return self.index + 1


_FENCE = re.compile(r"^ {0,3}(?:```|~~~)")
_ATX = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")
_SETEXT = re.compile(r"^ {0,3}(=+|-{3,})[ \t]*$")
_NUMBERED = re.compile(r"^\s{0,3}(\d{1,2}(?:\.\d{1,2})+\.?|\d{1,2}[.)])\s+(\S.*?)\s*$")
_BOLD = re.compile(r"^\s*(\*\*|__)\s*([^*_\n]{2,80}?)\s*:?\s*\1\s*:?\s*$")
_BULLET = re.compile(r"^\s*[-*+•–]\s+")
_RULE = re.compile(r"^\s*(?:[-=_*~#+.]\s*){3,}$")
_NUMBERING = re.compile(r"^\s*(?:\d{1,2}(?:\.\d{1,2})*[.)]?|[IVX]{1,4}[.)])\s+")
_NOT_HEADINGS = frozenset({"TODO", "TBD", "TBA", "N/A", "NA", "NONE"})


def _heading_key(text: str) -> str:
    text = re.sub(r"[*`#]+", "", text).replace("_", " ")
    text = _NUMBERING.sub("", text)
    return re.sub(r"\s+", " ", text.strip().rstrip(":")).strip()


def _heading_like(text: str, max_words: int = 12) -> bool:
    words = text.split()
    if not 1 <= len(words) <= max_words or len(text) > 100:
        return False
    if text.rstrip().endswith((".", ",", ";")):
        return False
    return text[0].isalpha() or text[0] in "[("


def _is_caps(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return len(letters) >= 3 and text == text.upper()


def _rank_weak(weak: list[Heading], base: int) -> None:
    """Give plain-text headings their levels: the first kind of heading line is the top.

    As in reStructuredText, a README's own order tells which kind of line is
    a section and which a part of one: in "GENERAL INFORMATION" followed by
    "1. Title of Dataset" the capitals are the sections, in "1. GENERAL
    INFORMATION" followed by "Authors information:" the numbers are. Numbered
    lines rank by their depth (1, 1.1, ...), and ``Label:`` lines always come
    last, since most of them are fields. *base* is the level of a Markdown
    heading above them, if any.
    """

    def kind(h: Heading) -> tuple[str, int]:
        return (h.style, h.level if h.style == "numbered" else 0)

    order: list[tuple[str, int]] = []
    for h in weak:
        if h.style != "colon" and kind(h) not in order:
            order.append(kind(h))
    for h in weak:
        h.level = base + 1 + (order.index(kind(h)) if h.style != "colon" else len(order))


def find_headings(text: str) -> list[Heading]:
    """The lines of *text* that work as headings, in order, each with the lines it covers.

    Candidates are Markdown headings (``#``), setext headings (underlined with
    ``===`` or ``---``), numbered lines (``1. GENERAL INFORMATION``,
    ``2) Methods``), a bold line, short ALL-CAPS lines and short lines ending
    in a colon. The last four only count when the text has fewer than two
    Markdown or setext headings, so numbered lists and bold text in a proper
    Markdown README stay body text; their levels follow the order in which
    each kind first appears. A heading's body runs to the next heading of the
    same or a higher level. A level-1 heading that opens the README and
    has deeper headings below it is its title (:attr:`Heading.is_title`), which
    names the project rather than a section.
    """
    lines = text.splitlines()
    n = len(lines)
    claimed: set[int] = set()  # headings and their underlines
    strong: list[Heading] = []
    fenced: set[int] = set()
    inside = False
    for i, line in enumerate(lines):
        if _FENCE.match(line):
            inside = not inside
            fenced.add(i)
        elif inside:
            fenced.add(i)

    def prev_blank(i: int) -> bool:
        return i == 0 or not lines[i - 1].strip() or bool(_RULE.match(lines[i - 1]))

    for i, line in enumerate(lines):
        if i in fenced or i in claimed or not line.strip():
            continue
        atx = _ATX.match(line)
        if atx:
            strong.append(
                Heading(
                    i,
                    line.strip(),
                    _heading_key(atx.group(2)),
                    len(atx.group(1)),
                    "markdown",
                    i + 1,
                )
            )
            claimed.add(i)
            continue
        under = _SETEXT.match(lines[i + 1]) if i + 1 < n and (i + 1) not in fenced else None
        if (
            under
            and prev_blank(i)
            and not _RULE.match(line)
            and not _BULLET.match(line)
            and len(line.strip()) <= 100
        ):
            level = 1 if under.group(1).startswith("=") else 2
            strong.append(Heading(i, line.strip(), _heading_key(line), level, "setext", i + 2))
            claimed.update((i, i + 1))

    weak: list[Heading] = []
    if len(strong) < 2:
        for i, line in enumerate(lines):
            if i in fenced or i in claimed or not line.strip() or _RULE.match(line):
                continue
            stripped = line.strip()
            numbered = _NUMBERED.match(line)
            if numbered:
                body = numbered.group(2)
                neighbour = any(
                    0 <= j < n and (_NUMBERED.match(lines[j]) or _BULLET.match(lines[j]))
                    for j in (i - 1, i + 1)
                )
                caps = _is_caps(body)
                if _heading_like(body) and (caps or (prev_blank(i) and not neighbour)):
                    depth = len(numbered.group(1).rstrip(".)").split("."))
                    weak.append(Heading(i, stripped, _heading_key(body), depth, "numbered", i + 1))
                    claimed.add(i)
                continue
            bold = _BOLD.match(line)
            if bold and prev_blank(i) and len(bold.group(2).split()) <= 8:
                weak.append(Heading(i, stripped, _heading_key(bold.group(2)), 4, "bold", i + 1))
                claimed.add(i)
                continue
            if (
                prev_blank(i)
                and 3 <= len(stripped) <= 60
                and _is_caps(stripped)
                and len(stripped.split()) <= 8
                and sum(c.isalpha() for c in stripped) / len(stripped) >= 0.5
                and not stripped.endswith((".", ",", ";", ":"))
                and not _BULLET.match(line)
                and stripped not in _NOT_HEADINGS
            ):
                weak.append(Heading(i, stripped, _heading_key(stripped), 2, "caps", i + 1))
                claimed.add(i)
                continue
            plain = _BULLET.sub("", stripped)
            if (
                plain.endswith(":")
                and 3 <= len(plain.rstrip(":").strip()) <= 70
                and len(plain.split()) <= 9
                and ":" not in plain[:-1]
                and not plain.rstrip(":").rstrip().endswith((".", ",", ";"))
            ):
                weak.append(Heading(i, stripped, _heading_key(plain), 7, "colon", i + 1))
                claimed.add(i)

    _rank_weak(weak, base=max((h.level for h in strong), default=0))
    headings = sorted([*strong, *weak], key=lambda h: h.index)
    for k, h in enumerate(headings):
        h.end = next((o.index for o in headings[k + 1 :] if o.level <= h.level), n)
    if (
        headings
        and headings[0].level == 1
        and headings[0].style in ("markdown", "setext")
        and any(h.level >= 2 for h in headings[1:])
    ):
        headings[0].is_title = True
    return headings


def _body_lines(
    lines: list[str], headings: list[Heading], h: Heading, template: ReadmeTemplate
) -> list[tuple[int, str]]:
    """The lines (index, text) in a heading's body, without the headings and underlines inside it.

    A heading inside it that carries its own value ("1. Licenses: CC BY 4.0")
    is kept, since the value is part of the section.
    """
    skip = {o.index for o in headings} | {o.body_start - 1 for o in headings if o.style == "setext"}
    for o in headings:
        value = _INLINE.match(lines[o.index]) if o.style in ("numbered", "colon") else None
        if value and _has_content(value.group(2), template):
            skip.discard(o.index)
    return [(i, lines[i]) for i in range(h.body_start, h.end) if i not in skip]


def _has_content(text: str, template: ReadmeTemplate) -> bool:
    """Whether *text* holds anything but whitespace, rules and template text."""
    cleaned = template.strip_placeholders(text)
    return bool(re.search(r"[^\W_]", cleaned))


def _words(text: str, template: ReadmeTemplate) -> int:
    return len(re.findall(r"\w+", template.strip_placeholders(text)))


_INLINE = re.compile(r"^\s*(?:[-*+•–]\s+)?([^:\n]{2,80}?)\s*:\s*(.*)$")
_INTRO_WORDS = 15


_FILE_EXT = re.compile(r"\.[A-Za-z0-9]{1,5}$")


def _file_like(label: str) -> bool:
    """A label that is a file or path (``data/raw.csv``, ```codebook.csv```), not a heading word."""
    label = label.strip()
    return "`" in label or (" " not in label and ("/" in label or bool(_FILE_EXT.search(label))))


def _inline_matches(
    lines: list[str], skip: set[int], patterns: Sequence[re.Pattern[str]]
) -> tuple[int, str, list[tuple[int, str]]] | None:
    """The first ``Label: value`` line whose label matches: (index, label as written, its text lines)."""
    for i, line in enumerate(lines):
        if i in skip or not line.strip():
            continue
        m = _INLINE.match(line.replace("**", "").replace("__", ""))
        if not m or "://" in m.group(1) or len(m.group(1).split()) > 10 or _file_like(m.group(1)):
            continue
        key = _heading_key(m.group(1))
        if not any(p.search(key) for p in patterns):
            continue
        body = [(i, m.group(2))]
        for j in range(i + 1, len(lines)):
            if not lines[j].strip() or j in skip or _INLINE.match(lines[j].replace("**", "")):
                break
            body.append((j, lines[j]))
        return i, line.strip(), body
    return None


def _label_matches(
    lines: list[str], skip: set[int], patterns: Sequence[re.Pattern[str]]
) -> tuple[int, str, list[tuple[int, str]]] | None:
    """The first short line on its own, in front of a paragraph, whose text matches.

    This is a heading in a plain-text README that has no markup: ``Contact`` or
    ``Bestanden``, a blank line before it and its text straight after it. The
    paragraph is its body.
    """
    for i, line in enumerate(lines):
        text = line.strip()
        if (
            i in skip
            or not 2 <= len(text) <= 60
            or len(text.split()) > 6
            or text.endswith((".", ",", ";", "!", "?"))
            or _BULLET.match(line)
            or _RULE.match(line)
            or (i > 0 and lines[i - 1].strip())
            or i + 1 >= len(lines)
            or not lines[i + 1].strip()
        ):
            continue
        if not any(p.search(_heading_key(text)) for p in patterns):
            continue
        body: list[tuple[int, str]] = []
        for j in range(i + 1, len(lines)):
            if not lines[j].strip() or j in skip:
                break
            body.append((j, lines[j]))
        return i, text, body
    return None


@dataclass
class _Found:
    """Where a README has a section: its first line, heading as written, kind, last line, text.

    ``body`` is its content; ``lines`` is every line in it, with the headings
    inside it, where its fields are looked for ("Methods for processing the
    data :" is a heading of its own when nothing follows the colon).
    """

    index: int
    heading: str
    kind: str
    end: int
    body: list[tuple[int, str]]
    lines: list[tuple[int, str]] = field(default_factory=list)


def _is_long_title(h: Heading) -> bool:
    """The README's title, when it is longer than a section heading (``Data and code for ...``)."""
    return h.is_title and len(h.key.split()) > 3


def _locate(
    spec: SectionSpec,
    lines: list[str],
    headings: list[Heading],
    skip: set[int],
    template: ReadmeTemplate,
) -> _Found | None:
    """Find a section of the template in the README: by heading, by label, or by the intro.

    Of several matching headings the highest-level one counts, so that
    "METHODOLOGICAL INFORMATION" is the methods section rather than a "Date of
    data collection" line inside another section.
    """
    matching = [
        h for h in headings if not _is_long_title(h) and any(p.search(h.key) for p in spec.match)
    ]
    if matching:
        h = min(matching, key=lambda m: m.level)
        body = _body_lines(lines, headings, h, template)
        underlines = {o.body_start - 1 for o in headings if o.style == "setext"}
        inside = [(i, lines[i]) for i in range(h.body_start, h.end) if i not in underlines]
        return _Found(
            h.index,
            h.raw,
            "heading",
            max([h.index, *(i for i, t in body if t.strip())]),
            body,
            inside,
        )
    inline = _inline_matches(lines, skip, spec.match)
    if inline is not None:
        i, raw, body = inline
        return _Found(i, raw, "inline", max([i, *(j for j, t in body if t.strip())]), body)
    label = _label_matches(lines, skip, spec.match)
    if label is not None:
        i, raw, body = label
        return _Found(i, raw, "label", body[-1][0], body)
    if not spec.intro:
        return None
    # the text at the top: up to the first heading, or, when the README opens with
    # its title, up to the heading after that
    first = headings[0] if headings else None
    titled = first is not None and first.is_title
    stop_at = headings[1] if titled and len(headings) > 1 else None if titled else first
    stop = len(lines) if stop_at is None else stop_at.index
    body = [(i, lines[i]) for i in range(stop) if i not in skip]
    if _words("\n".join(t for _, t in body), template) < _INTRO_WORDS:
        return None
    content = [i for i, t in body if t.strip()]
    return _Found(content[0], first.raw if titled and first else "", "intro", content[-1], body)


def match_sections(text: str, template: ReadmeTemplate | Any = None) -> tuple[Any, Any]:
    """Which sections of *template* a README has: the ``sections`` and ``fields`` tables.

    A section is found by its heading (see :func:`find_headings`), failing
    that by a ``Label: value`` line with a matching label, failing that by a
    short matching line on its own in front of a paragraph, failing that, for
    a section with ``"intro": true``, by the text at the top of the README.
    ``empty`` means that it holds nothing but whitespace and template text.
    The ``fields`` table says, for each field of each section, whether a line
    names it and whether what it expects (an e-mail address, ...) is there.
    """
    import pandas as pd

    template = load_readme_template(template)
    lines = text.splitlines()
    headings = find_headings(text)
    skip = {h.index for h in headings} | {h.body_start - 1 for h in headings if h.style == "setext"}
    rows: list[dict[str, Any]] = []
    field_rows: list[dict[str, Any]] = []
    for spec in template.sections:
        found = _locate(spec, lines, headings, skip, template)
        body_text = "" if found is None else "\n".join(t for _, t in found.body)
        rows.append(
            {
                "id": spec.id,
                "title": spec.title,
                "required": spec.required,
                "found": found is not None,
                "empty": found is not None and not _has_content(body_text, template),
                "heading": "" if found is None else found.heading,
                "line": pd.NA if found is None else found.index + 1,
                "end_line": pd.NA if found is None else found.end + 1,
                "kind": "" if found is None else found.kind,
            }
        )
        inside = [] if found is None else (found.lines or found.body)
        for fld in spec.fields:
            at = next(
                (k for k, (_, t) in enumerate(inside) if any(p.search(t) for p in fld.match)), None
            )
            hit = None if at is None else inside[at][0]
            value = "" if at is None else _field_value(inside, at, spec.fields)
            expected = fld.expect is None or bool(_EXPECT[fld.expect].search(value))
            field_rows.append(
                {
                    "section": spec.id,
                    "id": fld.id,
                    "label": fld.label,
                    "required": fld.required,
                    "found": hit is not None,
                    "ok": hit is not None and expected,
                    "line": pd.NA if hit is None else hit + 1,
                }
            )
    return _table(rows, _SECTION_DTYPES), _table(field_rows, _FIELD_DTYPES)


def _field_value(inside: list[tuple[int, str]], at: int, fields: Sequence[Any]) -> str:
    """What the field on line *at* of a section says: its line and the lines under it.

    The value runs to a blank line or the next field's line; a label with
    nothing after it ("Contacts :") may have its value in the paragraph below.
    """
    first = inside[at][1]
    out = [first]
    pair = _INLINE.match(first)
    label_only = pair is not None and not pair.group(2).strip()
    started = False
    for _, text in inside[at + 1 :]:
        if not text.strip():
            if label_only and not started:
                continue
            break
        if any(p.search(text) for f in fields for p in f.match):
            break
        out.append(text)
        started = True
    return "\n".join(out)


_SECTION_DTYPES = {
    "id": "string",
    "title": "string",
    "required": "boolean",
    "found": "boolean",
    "empty": "boolean",
    "heading": "string",
    "line": "Int64",
    "end_line": "Int64",
    "kind": "string",
}
_FIELD_DTYPES = {
    "section": "string",
    "id": "string",
    "label": "string",
    "required": "boolean",
    "found": "boolean",
    "ok": "boolean",
    "line": "Int64",
}


def _table(rows: list[dict[str, Any]], dtypes: dict[str, str]) -> Any:
    import pandas as pd

    return pd.DataFrame({c: pd.Series([r[c] for r in rows], dtype=t) for c, t in dtypes.items()})  # type: ignore[call-overload]


# --------------------------------------------------------------------------
# template text, e-mail addresses and licences in the README text
# --------------------------------------------------------------------------

_CODE_SPAN = re.compile(r"`[^`\n]*`")


def find_placeholders(text: str, template: ReadmeTemplate | Any = None) -> list[tuple[int, str]]:
    """Template text left in *text*: ``(line number, the text)`` for each hit, in order.

    Fenced code and inline code are skipped (``<input>`` in a usage example is
    not template text).
    """
    template = load_readme_template(template)
    hits: list[tuple[int, str]] = []
    inside = False
    for number, line in enumerate(text.splitlines(), start=1):
        if _FENCE.match(line):
            inside = not inside
            continue
        if inside:
            continue
        line = _CODE_SPAN.sub(lambda m: " " * len(m.group(0)), line)
        found = sorted(
            (m.start(), m.group(0).strip()) for p in template.placeholders for m in p.finditer(line)
        )
        hits.extend((number, value) for _, value in found)
    return hits


_EMAIL = _EXPECT["email"]


def _emails(text: str) -> list[str]:
    return list(dict.fromkeys(_EMAIL.findall(text)))


_LICENCE_CONTEXT = re.compile(
    r"licen[cs]|copyright|©|\brights\b|terms of use|re-?use|available under|released under|"
    r"distributed under|shared under|openly under",
    re.IGNORECASE,
)


def _licences_named(text: str, licences: Sequence[_Licence], *, context_needed: bool) -> list[str]:
    """Names of the licences in *text*; with *context_needed*, only on lines that talk about licensing."""
    found: list[str] = []
    for line in text.splitlines():
        talks = not context_needed or bool(_LICENCE_CONTEXT.search(line))
        for lic in licences:
            if (
                (talks or not lic.needs_context)
                and any(p.search(line) for p in lic.match)
                and lic.name not in found
            ):
                found.append(lic.name)
    return found
