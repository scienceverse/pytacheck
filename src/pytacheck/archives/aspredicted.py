"""AsPredicted pre-registrations (port of ``R/archive-aspredicted.R``).

:func:`aspredicted_links` finds AsPredicted links in papers;
:func:`aspredicted_info` fetches each pre-registration page and splits it into
its standard questions (``AP_title``, ``AP_authors``, ``AP_hypotheses``...).
Page text is extracted with a port of ``rvest::html_text2()``
(:func:`_html_text2`), so line breaks fall where they do in R.
"""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING, Any

import pandas as pd

from pytacheck._r import gsub, is_na, strsplit, trimws

if TYPE_CHECKING:
    from lxml import html as lxml_html

__all__ = ["aspredicted_info", "aspredicted_links"]


def aspredicted_links(paper: Any) -> pd.DataFrame:
    """Port of R/archive-aspredicted.R::aspredicted_links(): AsPredicted links in papers.

    The rows of the paper's (or paper list's) ``url`` table whose ``href``
    mentions ``aspredicted.org``.
    """
    from pytacheck._r import grepl
    from pytacheck.papers.tables import paper_table

    urls = paper_table(paper, "url")
    if "href" not in urls.columns:
        # R: filter(grepl(..., href)) on the 0 x 0 url table of an empty paper
        # list sees `href <- NULL` and keeps the empty table
        return urls.copy()
    keep = pd.Series(
        [bool(v) for v in grepl(r"aspredicted\.org", urls["href"], ignore_case=True)],
        index=urls.index,
        dtype=bool,
    )
    return urls[keep].reset_index(drop=True)


def aspredicted_info(ap_url: Any, id_col: int | str = 1, wait: float = 1) -> pd.DataFrame:
    """Port of R/archive-aspredicted.R::aspredicted_info(): AsPredicted page contents.

    *ap_url* is a URL, a sequence of URLs, or a table whose *id_col* (R-style
    1-based position, or a name) holds them, e.g. from
    :func:`aspredicted_links`. Pages are fetched one at a time, *wait*
    seconds apart; retrieval stops at the first CAPTCHA page. Returns the
    input rows with the page's sections added.
    """
    from pytacheck import http, utils
    from pytacheck._r import as_character
    from pytacheck.archives import _message
    from pytacheck.utils import left_join

    if not utils.online("aspredicted.org"):
        raise RuntimeError("AsPredicted.org seems to be offline")

    if isinstance(ap_url, pd.DataFrame):
        table = ap_url
        if isinstance(id_col, int | float) and not isinstance(id_col, bool):
            id_col_name = str(table.columns[int(id_col) - 1])
        else:
            id_col_name = str(id_col)
        raw_urls = table[id_col_name].tolist()
    else:
        vals = [ap_url] if isinstance(ap_url, str) or ap_url is None else list(ap_url)
        uniq = list(dict.fromkeys(None if is_na(v) else v for v in vals))
        raw_urls = [v for v in uniq if v is not None]
        id_col_name = "ap_url"
        table = pd.DataFrame(
            {"ap_url": pd.Series([as_character(v) for v in raw_urls], dtype="string")}
        )

    valid_ids = list(dict.fromkeys(as_character(u) for u in raw_urls if not is_na(u)))
    if not valid_ids:
        _message("No valid AsPredicted links")
        return table

    n = len(valid_ids)
    _message(f"Starting AsPredicted retrieval for {n} file{'' if n == 1 else 's'}...")
    frames: list[pd.DataFrame] = []
    for url in valid_ids:
        ap = _aspredicted_info(url)
        frames.append(ap)
        http.sleep(wait)
        if (
            "error" in ap.columns
            and not is_na(ap["error"].iloc[0])
            and ap["error"].iloc[0] == "captcha"
        ):
            break

    from pytacheck._r import bind_rows

    info = bind_rows(frames)
    ids = pd.DataFrame({"ap_url": pd.Series(valid_ids, dtype="string")})
    info = left_join(info, ids, by="ap_url")
    data = left_join(table, info, by={id_col_name: "ap_url"}, suffix=("", ".ap"))
    _message("...AsPredicted retrieval complete!")
    return data


#: The page's section headings, in order (R: ``sections``).
_SECTIONS: list[tuple[str, str]] = [
    ("AP_authors", "Author(s)"),
    ("AP_created", "Pre-registered on"),
    ("AP_data", "1) Have any data been collected for this study already?"),
    (
        "AP_hypotheses",
        "2) What's the main question being asked or hypothesis being tested in this study?",
    ),
    (
        "AP_key_dv",
        "3) Describe the key dependent variable(s) specifying how they will be measured.",
    ),
    ("AP_conditions", "4) How many and which conditions will participants be assigned to?"),
    (
        "AP_analyses",
        "5) Specify exactly which analyses you will conduct to examine the main "
        "question/hypothesis.",
    ),
    (
        "AP_outliers",
        "6) Describe exactly how outliers will be defined and handled, and your precise rule(s) "
        "for excluding observations.",
    ),
    (
        "AP_sample_size",
        "7) How many observations will be collected or what will determine sample size?\nNo need "
        "to justify decision, but be precise about exactly how the number will be determined.",
    ),
    (
        "AP_anything_else",
        "8) Anything else you would like to pre-register?\n(e.g., secondary analyses, variables "
        "collected for exploratory purposes, unusual analyses planned?)",
    ),
    ("AP_version", "Version of AsPredicted Questions: "),
    ("bug", "Report a bug"),
]

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/137.0.0.0 Safari/537.36 scienceverse/metacheck"
)


def _aspredicted_info(ap_url: str) -> pd.DataFrame:
    """Port of R/archive-aspredicted.R::.aspredicted_info(): fetch and parse one page.

    A non-200 success status gives an ``error`` column; an HTTP error status
    raises (R ``httr2::req_perform()`` errors on it); a CAPTCHA page gives
    ``error = "captcha"``.
    """
    from pytacheck import http
    from pytacheck.archives import _message

    _message(f"* Retrieving info from {ap_url}...")
    resp = http.request(
        "GET",
        ap_url,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
            "image/webp,*/*;q=0.8",
            "User-Agent": _USER_AGENT,
        },
        max_tries=1,
    )
    if resp is None:
        raise RuntimeError(f"Failed to perform HTTP request: {ap_url}")
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code} {resp.reason_phrase}.")
    if resp.status_code != 200:
        warnings.warn(f"{ap_url} error: {resp.reason_phrase}", stacklevel=2)
        return pd.DataFrame(
            {
                "ap_url": pd.Series([ap_url], dtype="string"),
                "error": pd.Series([resp.reason_phrase], dtype="string"),
            }
        )
    return _aspredicted_parse(resp.text, ap_url)


def _aspredicted_parse(html_text: str, ap_url: str) -> pd.DataFrame:
    """The parsing half of ``.aspredicted_info()``: page HTML -> one-row table."""
    from lxml import html as lxml_html

    doc = lxml_html.document_fromstring(html_text)
    bodies = doc.xpath("//body")
    body = _html_text2(bodies[0]) if bodies else ""
    row: dict[str, Any] = {"ap_url": ap_url}
    if "CLICK after solving captcha" in body:
        warnings.warn("Log in to AsPredicted to bypass the CAPTCHA", stacklevel=3)
        row["error"] = "captcha"
        return pd.DataFrame({k: pd.Series([v], dtype="string") for k, v in row.items()})

    title = doc.xpath("//h3//b//i")
    row["AP_title"] = title[0].text_content() if title else None
    for i in range(11):
        name, start = _SECTIONS[i]
        end = _SECTIONS[i + 1][1]
        pieces = strsplit(body, start, fixed=True)
        if len(pieces) < 2:
            row[name] = None
            continue
        after = pieces[1]
        answer = strsplit(after, end, fixed=True)
        row[name] = trimws(answer[0]) if answer and answer[0] is not None else None
    return pd.DataFrame({k: pd.Series([v], dtype="string") for k, v in row.items()})


# ---------------------------------------------------------------------------
# rvest::html_text2()
# ---------------------------------------------------------------------------

_BLOCK_TAGS = frozenset(
    {
        "address", "article", "aside", "blockquote", "details", "dialog", "dd", "div", "dl",
        "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4",
        "h5", "h6", "header", "hgroup", "hr", "li", "main", "nav", "ol", "p", "pre",
        "section", "table", "ul", "caption",
    }
)  # fmt: skip
_TABLE_TAGS = frozenset({"tbody", "thead", "tfoot", "tr", "td", "th"})


def _collapse_whitespace(x: str, preserve_nbsp: bool = False) -> str:
    x = gsub(r"(^[ \t\n]+)|([ \t\n]+$)", "", x, perl=True)
    return gsub("[\t\n ]+" if preserve_nbsp else "[\t\n  ]+", " ", x, perl=True)


class _PaddedText:
    def __init__(self) -> None:
        self.text: list[str] = []
        self.lines = 0

    def add_margin(self, n: int) -> None:
        if not self.text:
            return
        self.lines = max(self.lines, n)

    def convert_breaks(self) -> None:
        if self.lines == 0:
            return
        self.text.append("\n" * self.lines)
        self.lines = 0

    def add_text(self, x: str) -> None:
        if x == "":
            return
        self.convert_breaks()
        self.text.append(x)

    def output(self) -> str:
        return "".join(self.text)


def _contents(node: Any) -> list[tuple[str, Any]]:
    """``xml2::xml_contents()``: child elements and text nodes, as (name, node|text)."""
    out: list[tuple[str, Any]] = []
    if node.text:
        out.append(("text", node.text))
    for child in node:
        tag = child.tag if isinstance(child.tag, str) else None
        if tag is None:  # comment / processing instruction
            name = "comment"
        else:
            name = tag.lower()
        out.append((name, child))
        if child.tail:
            out.append(("text", child.tail))
    return out


def _xml_text(item: tuple[str, Any]) -> str:
    name, node = item
    if name == "text":
        return str(node)
    if name == "comment":
        return str(node.text or "")
    return str(node.text_content())


def _is_inline(node: Any) -> bool:
    for child in node:
        if isinstance(child.tag, str) and child.tag.lower() in _BLOCK_TAGS | _TABLE_TAGS:
            return False
    return True


def _html_text_inline(node: Any, preserve_nbsp: bool) -> str:
    children = _contents(node)
    if not children:
        return ""
    texts = [_xml_text(c) for c in children]
    is_br = [c[0] == "br" for c in children]
    lines: list[str] = []
    has_br: list[bool] = []
    for i, (t, br) in enumerate(zip(texts, is_br, strict=True)):
        if i == 0 or is_br[i - 1]:
            lines.append(t)
            has_br.append(br)
        else:
            lines[-1] += t
            has_br[-1] = has_br[-1] or br
    if node.tag != "pre":
        lines = [_collapse_whitespace(line, preserve_nbsp) for line in lines]
    return "".join(line + ("\n" if br else "") for line, br in zip(lines, has_br, strict=True))


def _tag_margin(name: str) -> int:
    if name == "p":
        return 2
    return 1 if name in _BLOCK_TAGS else 0


def _html_text_block(item: tuple[str, Any], text: _PaddedText, preserve_nbsp: bool) -> None:
    name, node = item
    if name == "text":
        text.add_text(_collapse_whitespace(str(node), preserve_nbsp))
        return
    if name == "comment":
        return  # R: html_text_inline() of a comment has no contents
    if _is_inline(node):
        text.add_text(_html_text_inline(node, preserve_nbsp))
        return
    children = _contents(node)
    n = len(children)
    for i, child in enumerate(children, start=1):
        cname = child[0]
        margin = _tag_margin(cname)
        text.add_margin(margin)
        _html_text_block(child, text, preserve_nbsp)
        if cname == "tr" and i != n:
            text.add_text("\n")
        elif cname in ("th", "td") and i != n:
            text.add_text("\t")
        elif cname == "br":
            text.add_text("\n")
        text.add_margin(margin)


def _html_text2(x: str | lxml_html.HtmlElement | list[Any], preserve_nbsp: bool = False) -> Any:
    """Port of ``rvest::html_text2()``: an element's text as a browser would lay it out.

    Block elements start new lines (paragraphs get a blank line), ``<br>``
    breaks lines, table cells are tab-separated, and whitespace is collapsed.
    *x* is an element, a list of elements (gives a list), or an HTML
    document string (its ``<body>`` is used).
    """
    if isinstance(x, list):
        return [_html_text2(e, preserve_nbsp) for e in x]
    if isinstance(x, str):
        from lxml import html as lxml_html

        doc = lxml_html.document_fromstring(x)
        bodies = doc.xpath(".//body")
        if not bodies:
            return None
        x = bodies[0]
    text = _PaddedText()
    _html_text_block(
        (x.tag.lower() if isinstance(x.tag, str) else "comment", x), text, preserve_nbsp
    )
    return text.output()
