"""XML utilities (port of ``R/svutils-xml.R``) and the xml2 idioms metacheck uses.

metacheck parses Grobid TEI with the R package xml2, which wraps libxml2;
pytacheck uses lxml, which wraps the same library, so parsing, XPath and
serialisation agree byte for byte when the same libxml2 calls are made.
This module holds the thin layer that makes the lxml calls match xml2's:

* ``xml_find_all()`` / ``xml_find_first()`` accept an element, a document,
  a node set (list) or a missing node (``None``, xml2's ``xml_missing``);
* ``xml_text()`` is ``xmlNodeGetContent`` with xml2's optional trimming;
* ``xml_attr()`` matches an attribute by *local* name (xml2 uses
  ``xmlHasProp``, so ``"id"`` finds ``xml:id``);
* ``as_character()`` is ``as.character(<xml_node>)``: ``xmlSaveTree`` with
  ``XML_SAVE_FORMAT``, without the ancestor namespace declarations lxml
  would otherwise copy onto a serialised sub-element;
* ``html_text()`` is ``xml_text(read_html(x))`` (libxml2's HTML parser).

lxml is imported lazily: ``import pytacheck`` must stay fast.
"""

from __future__ import annotations

import copy
import functools
import re
import warnings
from os import PathLike
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pytacheck._r.regex import compile_r, gsub

if TYPE_CHECKING:
    from lxml import etree

__all__ = [
    "XmlParseError",
    "as_character",
    "html_text",
    "read_xml",
    "xml_attr",
    "xml_find_all",
    "xml_find_first",
    "xml_ns",
    "xml_text",
]

XmlLike = Any  # lxml element / element tree / list of elements / None (xml_missing)

_XML_NS = "{http://www.w3.org/XML/1998/namespace}"


# ---------------------------------------------------------------------------
# parsers (one per option set, created on first use)
# ---------------------------------------------------------------------------


@functools.cache
def _xml_parser(recover: bool = False) -> etree.XMLParser:
    """``read_xml(options = "NOBLANKS")`` with an explicit UTF-8 encoding."""
    from lxml import etree

    return etree.XMLParser(
        encoding="utf-8",
        remove_blank_text=True,
        resolve_entities=False,
        strip_cdata=False,
        no_network=True,
        recover=recover,
    )


@functools.cache
def _html_parser(noblanks: bool) -> etree.HTMLParser:
    """``read_html()``: RECOVER, NOERROR, HUGE (and NOBLANKS by default)."""
    from lxml import etree

    return etree.HTMLParser(
        encoding="utf-8",
        recover=True,
        remove_blank_text=noblanks,
        huge_tree=True,
        no_network=True,
    )


class XmlParseError(ValueError):
    """An XML document libxml2 cannot parse (message as xml2 reports it)."""


def read_xml(text: str) -> etree._ElementTree:
    """``xml2::read_xml()`` of a string (parsed as UTF-8, blank nodes dropped).

    As in xml2, only a fatal libxml2 error fails: it raises
    :class:`XmlParseError` with xml2's message for the first fatal error,
    e.g. ``"Extra content at the end of the document [5]"``. Non-fatal
    errors (a duplicated ``xml:id``, an undeclared namespace prefix, ...)
    are warnings, and the document is kept.
    """
    from lxml import etree

    data = text.encode("utf-8", "surrogateescape")
    parser = _xml_parser()
    try:
        root = etree.fromstring(data, parser)
    except etree.XMLSyntaxError as exc:
        entries = list(parser.error_log)
        fatal = [e for e in entries if e.level >= 3]
        if fatal or not entries:
            msg = f"{fatal[0].message} [{fatal[0].type}]" if fatal else str(exc)
            raise XmlParseError(msg) from exc
        # lxml rejects any ERROR-level message; libxml2 (and xml2) keep the
        # document, which a recovering parse rebuilds unchanged
        _warn_parse_errors(entries)
        root = etree.fromstring(data, _xml_parser(recover=True))
    else:
        _warn_parse_errors(list(parser.error_log))
    if root is None:
        raise XmlParseError("Document is empty [4]")
    return root.getroottree()


def _warn_parse_errors(entries: list[Any]) -> None:
    for e in entries:
        warnings.warn(f"{e.message} [{e.type}]", stacklevel=4)


def read_html(text: str, noblanks: bool = True) -> etree._Element:
    """``xml2::read_html()`` of a string; returns the ``<html>`` root element."""
    from lxml import etree

    root = etree.fromstring(text.encode("utf-8", "surrogateescape"), _html_parser(noblanks))
    if root is None:  # an empty document
        raise ValueError("Document is empty")
    return root


# ---------------------------------------------------------------------------
# xml2 idioms
# ---------------------------------------------------------------------------


# a QName prefix in an XPath (``ns:name``, ``ns:*``); axes (``::``) excluded
_PREFIX_RX = re.compile(r"(?<![\w.:-])[A-Za-z_][\w.-]*:(?=[A-Za-z_*])")
_STRING_LITERAL_RX = re.compile(r"'[^']*'|\"[^\"]*\"")


def _make_unique(names: list[str], sep: str = "") -> list[str]:
    """R's ``make.unique(names, sep)``."""
    taken = set(names)
    firsts: set[str] = set()
    counts: dict[str, int] = {}
    out: list[str] = []
    for name in names:
        if name not in firsts:
            firsts.add(name)
            out.append(name)
            continue
        cnt = counts.get(name, 1)
        while f"{name}{sep}{cnt}" in taken:
            cnt += 1
        new = f"{name}{sep}{cnt}"
        taken.add(new)
        counts[name] = cnt + 1
        out.append(new)
    return out


def xml_ns(x: XmlLike) -> dict[str, str]:
    """``xml2::xml_ns()``: every namespace declared in the document of *x*.

    Declarations are listed stably sorted by prefix; default namespaces are
    named ``d1``, ``d2``, ... and repeated prefixes made unique as R's
    ``make.unique(sep = "")`` does (``a``, ``a1``, ...).
    """
    from lxml import etree

    if isinstance(x, list | tuple):
        return xml_ns(x[0]) if x else {}
    if x is None:
        return {}
    import io

    root = x.getroot() if isinstance(x, etree._ElementTree) else x.getroottree().getroot()
    # every element's own declarations, in document order: the root's
    # serialisation writes each element's declarations as they are
    entries: list[tuple[str, str]] = [
        (prefix or "", uri)
        for _, (prefix, uri) in etree.iterparse(
            io.BytesIO(etree.tostring(root)), events=("start-ns",), huge_tree=True
        )
    ]
    entries.sort(key=lambda e: e[0].encode("utf-8"))
    names = [p for p, _ in entries]
    k = 0
    for i, p in enumerate(names):
        if p == "":
            k += 1
            names[i] = f"d{k}"
    return dict(zip(_make_unique(names), (u for _, u in entries), strict=True))


def _xpath(node: Any, xpath: str) -> list[Any]:
    """Evaluate *xpath* as xml2's ``xpath_search()`` does.

    The document's namespaces are registered (``ns = xml_ns(x)``), and an
    invalid expression gives a warning and no nodes instead of an error.
    """
    from lxml import etree

    ns = None
    if _PREFIX_RX.search(_STRING_LITERAL_RX.sub("''", xpath)):
        ns = {k: v for k, v in xml_ns(node).items() if k != "xml"} or None
    try:
        res = node.xpath(xpath, namespaces=ns)
    except etree.XPathError as exc:
        entries = list(getattr(exc, "error_log", []) or [])
        msg = entries[-1].message if entries else str(exc)
        code = entries[-1].type if entries else 0
        warnings.warn(f"{msg} [{code}]", stacklevel=3)
        return []
    return res if isinstance(res, list) else [res]


def xml_find_all(x: XmlLike, xpath: str) -> list[Any]:
    """``xml2::xml_find_all()``: matching nodes, in document order, without duplicates."""
    if x is None:
        return []
    if isinstance(x, list | tuple):
        if len(x) == 1:
            return xml_find_all(x[0], xpath)
        out: list[Any] = []
        seen: set[int] = set()
        for node in x:
            for hit in xml_find_all(node, xpath):
                if id(hit) not in seen:
                    seen.add(id(hit))
                    out.append(hit)
        return out
    return _xpath(x, xpath)


def xml_find_first(x: XmlLike, xpath: str) -> Any:
    """``xml2::xml_find_first()``: the first match or ``None`` (missing).

    For a node set (list), a list of the first match of every node.
    """
    if x is None:
        return None
    if isinstance(x, list | tuple):
        return [xml_find_first(n, xpath) for n in x]
    hits = _xpath(x, xpath)
    return hits[0] if hits else None


def _node_text(node: Any) -> str:
    from lxml import etree

    if isinstance(node, str):  # an XPath string result
        return str(node)
    if isinstance(node, etree._ElementTree):
        node = node.getroot()
    return etree.tostring(node, method="text", encoding="unicode", with_tail=False)


@functools.cache
def _rx(pattern: str) -> Any:
    """A compiled TRE pattern (greedy matching equals leftmost-longest for these)."""
    return compile_r(pattern, posix=False)


def _trim_text(s: str) -> str:
    """xml2's ``trim_text()``: strip ``[[:space:]\\u00a0]`` from both ends."""
    s = _rx("^[[:space:]\u00a0]+").sub("", s, count=1)
    return _rx("[[:space:]\u00a0]+$").sub("", s, count=1)


def xml_text(x: XmlLike, trim: bool = False) -> Any:
    """``xml2::xml_text()``: ``None`` for a missing node, a list for a node set."""
    if isinstance(x, list | tuple):
        return [xml_text(n, trim) for n in x]
    if x is None:
        return None
    text = _node_text(x)
    return _trim_text(text) if trim else text


def xml_attr(x: XmlLike, attr: str) -> Any:
    """``xml2::xml_attr()``: the first attribute with this local name (``None`` if absent)."""
    if isinstance(x, list | tuple):
        return [xml_attr(n, attr) for n in x]
    if x is None or not hasattr(x, "attrib"):
        return None
    for key, value in x.attrib.items():
        local = key.rsplit("}", 1)[-1] if key.startswith("{") else key
        if local == attr:
            return value
    return None


def as_character(x: XmlLike) -> Any:
    """``as.character()`` of an xml2 node: the node serialised with formatting.

    A missing node gives ``None`` (``NA``); a list gives a list. A document
    gives the XML declaration plus the formatted tree, as ``xmlSaveDoc``.
    """
    from lxml import etree

    if isinstance(x, list | tuple):
        return [as_character(n) for n in x]
    if x is None:
        return None
    if isinstance(x, str):
        return x
    if isinstance(x, etree._ElementTree):
        body = etree.tostring(x, encoding="unicode", pretty_print=True)
        return '<?xml version="1.0" encoding="UTF-8"?>\n' + body
    parent = x.getparent()
    if parent is None:
        out = etree.tostring(x, encoding="unicode", pretty_print=True, with_tail=False)
        return out[:-1] if out.endswith("\n") else out
    # lxml copies every ancestor namespace declaration onto a serialised
    # sub-element; libxml2's xmlNodeDump (xml2) writes only the node's own
    # declarations. A copy carries the node's own declarations plus the
    # ancestor ones its subtree uses (appended after them); the latter are
    # removed from the start tag again.
    node = copy.deepcopy(x)
    out = etree.tostring(node, encoding="unicode", pretty_print=True, with_tail=False)
    if out.endswith("\n"):
        out = out[:-1]
    parent_ns = parent.nsmap
    added = [
        (prefix, uri)
        for prefix, uri in node.nsmap.items()
        if parent_ns.get(prefix) == uri and x.nsmap.get(prefix) == uri
    ]
    if added:
        out = _drop_ns_decls(out, added)
    return out


def _drop_ns_decls(out: str, decls: list[tuple[str | None, str]]) -> str:
    """Remove namespace declarations from the first start tag of serialised XML."""
    end = out.find(">")
    if end < 0:
        return out
    tag = out[:end]
    for prefix, uri in decls:
        name = "xmlns" if prefix is None else f"xmlns:{prefix}"
        esc = uri.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        for quoted in (f'"{esc}"', f"'{esc}'"):
            decl = f" {name}={quoted}"
            if decl in tag:
                tag = tag.replace(decl, "", 1)
                break
    return tag + out[end:]


def html_text(text: str, noblanks: bool = False) -> str:
    """``xml2::xml_text(xml2::read_html(text))`` (the text of the ``<html>`` root)."""
    return _node_text(read_html(text, noblanks))


# ---------------------------------------------------------------------------
# R/svutils-xml.R
# ---------------------------------------------------------------------------


def _xml_find_text(xml: XmlLike, xpath: str, join: str | None = None) -> Any:
    """Port of ``R/svutils-xml.R::.xml_find_text()``.

    The trimmed text of every node matching *xpath*, with runs of spaces
    collapsed; joined with *join* when given (a string), otherwise a list
    (``[""]`` when nothing matches).
    """
    nodes = xml_find_all(xml, xpath)
    spaces = _rx(" +")
    text: list[str] = [spaces.sub(" ", xml_text(n, trim=True)) for n in nodes]
    if join is not None:
        return join.join(text)
    if not text:
        text = [""]
    return text


def _xml_find1_text(xml: XmlLike, xpath: str) -> str:
    """Port of ``R/svutils-xml.R::.xml_find1_text()``: the first match's text (or ``""``)."""
    return str(_xml_find_text(xml, xpath)[0])


# Operators used to glue a number to the operator after it (as in extract_eq()).
_OPERATORS = "=<>~\u2248\u2260\u2264\u2265\u226a\u226b"

# The TEI clean-up replacements of .xml_read_grobid(), in order: (TRE pattern,
# replacement). Patterns are the R regexes after string-literal unescaping.
_GROBID_FIXES: tuple[tuple[str, str], ...] = (
    ("\\s+([\u00b20-9.]+\\s*[" + _OPERATORS + "])", "\\1"),
    ("r\\s*p\\s*2", "rp\u00b2"),
    # R writes "\u03C\\s*p..." intending omega (U+03C9), but R reads "\u03C"
    # as U+003C ("<"), so the pattern really starts with "<" (reproduced).
    ("<\\s*p\\s*2", "<p\u00b2"),
    ("\u03b7\\s*p\\s*[2\u00b2]", "\u03b7p\u00b2"),
    ("\u03b7\\s*G\\s*[2\u00b2]", "\u03b7G\u00b2"),
    ("\u03b7\\s*[2\u00b2]", "\u03b7\u00b2"),
    ("\u03c4\\s*[2\u00b2]", "\u03c4\u00b2"),
    ("\\br\\s*[2\u00b2](\\s*[=><])", "r\u00b2\\1"),
    ("\\bR\\s*[2\u00b2]\\s+M\\b", "R\u00b2M"),
    ("\\bR\\s*[2\u00b2]\\b", "R\u00b2"),
    ("\\bI\\s*[2\u00b2]\\b", "I\u00b2"),
    ("\u03a7\\s*[2\u00b2]", "\u03a7\u00b2"),
    ("\\bf\\s*[2\u00b2](\\s*[=><])", "f\u00b2\\1"),
    ("\u03a7[2\u00b2]\\s*\\((\\s*\\d+)\\s*\\)", "\u03a7\u00b2(\\1)"),
    ("\\br\\s*\\((\\s*\\d+)\\s*\\)", "r(\\1)"),
    ("\\bd\\s+z\\b", "dz"),
    # "\\g" is not an escape in TRE: this matches "dgz", "dggz", ...
    ("\\bd\\g+z\\b", "gz"),
    ("\\bBF\\s+([10]{2})\\b", "BF\\1"),
    ("(https?://)\\s+", "\\1"),
    ("(\\d\\.)\\s+(\\d)", "\\1\\2"),
    ("\\b[Ff]ig\\. (\\D?\\d)", "Fig \\1"),
    ("\\b[Ff]igure\\. (\\d)", "Figure \\1"),
    ("\\b[Tt]ab\\. (\\d)", "Tab \\1"),
    ("\\b[Tt]able\\. (\\d)", "Table \\1"),
)


def _read_lines_joined(path: Path) -> str:
    """``paste(readLines(path, warn = FALSE), collapse = "\\n")``."""
    text = path.read_bytes().decode("utf-8", "surrogateescape")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text[:-1] if text.endswith("\n") else text


def _strip_default_namespaces(tree: etree._ElementTree) -> None:
    """``xml2::xml_ns_strip()``: drop every default (unprefixed) namespace.

    Elements in a default namespace lose it; prefixed namespace declarations
    stay where they are, as in xml2.
    """
    from lxml import etree

    root = tree.getroot()
    default_ns: set[str] = set()
    prefixes: set[str] = set()
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        for prefix, uri in (el.nsmap or {}).items():
            if prefix is None:
                default_ns.add(uri)
            else:
                prefixes.add(prefix)
    if not default_ns:
        return
    for el in root.iter():
        tag = el.tag
        if isinstance(tag, str) and tag.startswith("{"):
            uri, local = tag[1:].split("}", 1)
            if uri in default_ns and el.prefix is None:
                el.tag = local
    etree.cleanup_namespaces(tree, keep_ns_prefixes=sorted(prefixes))


def _xml_read_grobid(path: str | PathLike[str]) -> etree._ElementTree:
    """Port of ``R/svutils-xml.R::.xml_read_grobid()``.

    Reads a Grobid/TEI XML file, applies metacheck's text fixes for common
    mangled statistics (``"XX  2 = ?"`` -> ``"XX2 = ?"``, ``"r p 2"`` ->
    ``"rp²"``, ``"Fig. 1"`` -> ``"Fig 1"``, ...) to the raw file text, parses
    it (blank text nodes dropped) and strips the TEI default namespace so
    XPath needs no prefixes.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError("The XML file does not exist.")
    text = _read_lines_joined(p)
    for pattern, replacement in _GROBID_FIXES:
        text = gsub(pattern, replacement, text)
    text = text.replace("</ref><ref", "</ref> <ref")
    tree = read_xml(text)
    _strip_default_namespaces(tree)
    return tree
