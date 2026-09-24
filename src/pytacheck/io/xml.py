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
from os import PathLike
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pytacheck._r.regex import gsub, sub

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
    "xml_text",
]

XmlLike = Any  # lxml element / element tree / list of elements / None (xml_missing)

_XML_NS = "{http://www.w3.org/XML/1998/namespace}"


# ---------------------------------------------------------------------------
# parsers (one per option set, created on first use)
# ---------------------------------------------------------------------------


@functools.cache
def _xml_parser() -> etree.XMLParser:
    """``read_xml(options = "NOBLANKS")`` with an explicit UTF-8 encoding."""
    from lxml import etree

    return etree.XMLParser(
        encoding="utf-8",
        remove_blank_text=True,
        resolve_entities=False,
        strip_cdata=False,
        no_network=True,
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

    Parse failures raise :class:`XmlParseError` with xml2's message, e.g.
    ``"Extra content at the end of the document [5]"``.
    """
    from lxml import etree

    try:
        root = etree.fromstring(text.encode("utf-8", "surrogateescape"), _xml_parser())
    except etree.XMLSyntaxError as exc:
        entries = list(exc.error_log)
        last = entries[-1] if entries else None
        msg = f"{last.message} [{last.type}]" if last is not None else str(exc)
        raise XmlParseError(msg) from exc
    return root.getroottree()


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


def _xpath(node: Any, xpath: str) -> list[Any]:
    res = node.xpath(xpath)
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
    """``xml2::xml_find_first()`` for one node: the first match or ``None`` (missing)."""
    if x is None:
        return None
    hits = _xpath(x, xpath)
    return hits[0] if hits else None


def _node_text(node: Any) -> str:
    from lxml import etree

    if isinstance(node, str):  # an XPath string result
        return str(node)
    if isinstance(node, etree._ElementTree):
        node = node.getroot()
    return etree.tostring(node, method="text", encoding="unicode", with_tail=False)


def _trim_text(s: str) -> str:
    """xml2's ``trim_text()``: strip ``[[:space:]\\u00a0]`` from both ends."""
    s = sub("^[[:space:]\u00a0]+", "", s)
    return sub("[[:space:]\u00a0]+$", "", s)


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
    # lxml copies every ancestor namespace declaration onto a serialised
    # sub-element; libxml2's xmlSaveTree (xml2) does not. Serialising a copy
    # carries only the namespaces the subtree itself uses.
    node = x if x.getparent() is None else copy.deepcopy(x)
    out = etree.tostring(node, encoding="unicode", pretty_print=True, with_tail=False)
    return out[:-1] if out.endswith("\n") else out


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
    text: list[str] = [gsub(" +", " ", xml_text(n, trim=True)) for n in nodes]
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
