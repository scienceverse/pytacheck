"""Recognise HTML files that are rendered statistical output (port of ``R/html-output.R``).

A ``.html`` file in a research repository may be a rendered R Markdown /
Quarto analysis (code plus printed results), a translated Stata log, an
experiment's runner page or a documentation site. :func:`_html_sniff_kind`
tells the first two apart from everything else by content, and
:func:`_html_export_r_source` recovers the R code of a knitted document.
metacheck's (and pytacheck's) own reports carry metacheck's contact address
in their fixed introduction and are never mistaken for deposited output.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from pytacheck._r.regex import grepl

__all__ = ["_html_export_r_source", "_html_sniff_kind"]

_LINE_END = re.compile(r"\r\n|\r|\n")


def _read_lines(path: str, n: int) -> list[str]:
    """R ``readLines(path, n = n, warn = FALSE, encoding = "UTF-8")``."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return []
    text = raw.decode("utf-8", errors="replace")
    lines = _LINE_END.split(text)
    if lines and lines[-1] == "":
        lines.pop()
    return lines[:n]


def _is_na(x: Any) -> bool:
    return x is None or (isinstance(x, float) and x != x)


def _html_sniff_kind(path: str | os.PathLike[str] | None) -> str | None:
    """Port of ``.html_sniff_kind()``: which tool rendered a local HTML file.

    Returns ``"rmd"`` (R Markdown/Quarto via pandoc), ``"stata"`` (a Stata
    log rendered to HTML; provisional) or ``None`` when neither fingerprint
    is found or the file cannot be read.
    """
    if _is_na(path):
        return None
    path = os.fspath(path)  # type: ignore[arg-type]
    if not path or not os.path.exists(path):
        return None
    head_lines = _read_lines(path, 500)
    if not head_lines:
        return None
    head_txt = "\n".join(head_lines)
    is_pandoc_head = bool(
        grepl(
            r"""generator["']?\s*content\s*=\s*["'](pandoc|quarto)""",
            head_txt,
            ignore_case=True,
            perl=True,
        )
    )
    body_lines = _read_lines(path, 20000)
    body_txt = "\n".join(body_lines)
    if "metacheck@scienceverse.org" in body_txt:
        return None
    if is_pandoc_head or grepl(r'<pre class="r"><code>|<pre class="sourceCode r">', body_txt, perl=True):
        return "rmd"
    if any(grepl(r"^\.\s+[a-z][a-z0-9_]*\b", body_lines, perl=True)):
        return "stata"
    return None


def _file_path_sans_ext(name: str) -> str:
    """R ``tools::file_path_sans_ext()``."""
    return re.sub(r"([^.]+)\.[A-Za-z0-9]+$", r"\1", name)


def _html_export_r_source(html_path: str | os.PathLike[str], code_dir_name: str = "code") -> str | None:
    """Port of ``.html_export_r_source()``: recover the R code of a knitted HTML file.

    Every input chunk (``<pre class="r"><code>`` and the syntax-highlighted
    ``<pre class="sourceCode r"><code>``) is written, separated by blank
    lines, to ``<dir>/<code_dir_name>/<name>.R``. Returns that path, or
    ``None`` when the document cannot be read or has no R chunk.
    """
    import lxml.html

    html_path = os.fspath(html_path)
    if not os.path.exists(html_path):
        raise FileNotFoundError(f"File not found: {html_path}")
    try:
        with open(html_path, "rb") as fh:
            raw = fh.read()
        doc = lxml.html.document_fromstring(
            raw, parser=lxml.html.HTMLParser(encoding="utf-8", recover=True)
        )
    except (OSError, ValueError, lxml.etree.ParserError):  # type: ignore[attr-defined]
        return None
    classic = doc.xpath("//pre[@class='r']/code")
    source_div_code = doc.xpath(
        "//pre[contains(concat(' ', @class, ' '), ' sourceCode ')"
        " and contains(concat(' ', @class, ' '), ' r ')]/code"
    )
    chunks = [*classic, *source_div_code]
    if not chunks:
        return None
    chunk_text = [node.text_content() for node in chunks]
    chunk_text = [t for t in chunk_text if t.strip(" \t\r\n")]
    if not chunk_text:
        return None
    code_dir = os.path.join(os.path.dirname(html_path) or ".", code_dir_name)
    Path(code_dir).mkdir(parents=True, exist_ok=True)
    out_path = os.path.join(code_dir, _file_path_sans_ext(os.path.basename(html_path)) + ".R")
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n\n".join(chunk_text) + "\n")
    return out_path
