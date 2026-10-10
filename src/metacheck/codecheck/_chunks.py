"""R code from R Markdown, Quarto and Sweave documents, read without R.

metacheck's ``code_extract_r()`` runs ``knitr::purl()``, which evaluates some
chunk options in R. This extractor reads the chunk delimiters and chunk
options as text and never evaluates R (UPSTREAM_ISSUES D80):

* **Format.** R Markdown/Quarto when a line opens a ```` ```{engine ...} ````
  chunk; else the first of knitr's other syntaxes with a chunk (Sweave
  ``<<...>>=``, LaTeX ``% begin.rcode``, HTML ``<!-- begin.rcode``,
  reStructuredText ``.. {r}``, AsciiDoc ``// begin.rcode``, Textile
  ``###. begin.rcode``); else R Markdown without chunks when the text has
  inline `` `r ...` `` code. Anything else has no R code.
* **Chunks.** A Markdown chunk ends at a fence of the same indentation and
  length, at the next opening fence of that length, or at the end of the
  document. A fence of another length is code when the matching fence
  follows before the next chunk, and ends the chunk otherwise. Other chunks
  end at their end marker (``@`` in Sweave) or at the next chunk. Code under
  an indented or quoted (``>``) header loses that prefix.
* **Options** come from the header (``label, name = value, ...``) and from
  the ``#|`` lines that open the chunk (YAML, or the same list), later ones
  winning. A value is *literal* when it is a quoted string, a number,
  ``TRUE``/``FALSE``/``T``/``F``, ``NA`` or ``NULL``, or a plain YAML value.
  **Any other value is an R expression, which is not evaluated: the option
  counts as not given**, so a chunk with ``eval = params$run`` is extracted as
  code.
* **What the options do.** ``purl = FALSE`` drops the chunk; ``eval = FALSE``
  comments its code out (``# ``); ``error = TRUE`` wraps it in
  ``try({ ... })``; a chunk whose engine is not R (the header's, else
  ``engine = "..."``) is commented out with its ``comment`` prefix (``##``
  by default). ``child`` documents are not read.
* **References.** A ``<<label>>`` line in an R chunk is replaced by the code
  of the chunk with that label, indented like the reference and expanded in
  turn; an unknown or circular reference stays as written.
* *documentation* 1 writes each R chunk's header as a ``## ----`` comment
  followed by its ``#|`` lines; 2 also writes the text between chunks as
  ``#'`` comments.
* YAML ``params`` in the front matter of an R Markdown/Quarto document are
  written first, as ``params <-`` and a ``list(...)`` line; a ``!r`` value is
  written as its R source.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

__all__ = ["extract_r"]

_MD_BEGIN = re.compile(r"^([\t >]*)(```+)\s*\{([a-zA-Z0-9_]+)((?: *[ ,].*)?)\}\s*$")
_MD_END = re.compile(r"^[\t >]*```+\s*$")
_MD_INLINE = re.compile(r"`r[ #][^`]+`")
#: knitr's other document syntaxes: Sweave, LaTeX, HTML, reStructuredText,
#: AsciiDoc and Textile (chunk start, chunk end, prefix of each code line)
_FORMATS = [
    (re.compile(begin), re.compile(end), strip)
    for begin, end, strip in (
        (r"^\s*<<(.*)>>=.*$", r"^\s*@\s*(%+.*|)$", None),
        (r"^\s*%+\s*begin.rcode\s*(.*)", r"^\s*%+\s*end.rcode", r"^\s*%+"),
        (r"^\s*<!--\s*begin.rcode\s*(.*)", r"^\s*end.rcode\s*-->", None),
        (r"^\s*[.][.]\s+\{r(.*)\}\s*$", r"^\s*[.][.]\s+[.][.]\s*$", r"^\s*[.][.]"),
        (r"^//\s*begin[.]rcode(.*)$", r"^//\s*end[.]rcode\s*$", r"^//+"),
        (r"^###[.]\s+begin[.]rcode(.*)$", r"^###[.]\s+end[.]rcode\s*$", None),
    )
]
_REF = re.compile(r"^(\s*)<<(.+)>>\s*$")
_BLANK = re.compile(r"\s*")
_NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?L?")

#: the comment that starts a ``#|``-style option line in a chunk of each engine
_COMMENTS = {
    "//": "asy cc csharp d3 dot fsharp go groovy java js node ojs Rcpp sass scss scala",
    "--": "haskell lua mysql psql sql",
    "!": "fortran fortran95",
    "%%": "mermaid",
    "%": "matlab tikz",
    "*": "sas stata",
    "\u235d": "apl",
}
_OPTION_COMMENT = {engine: c for c, engines in _COMMENTS.items() for engine in engines.split()}


class _Expr(str):
    """An option value that is an R expression (its source text)."""

    __slots__ = ()


@dataclass
class _Chunk:
    options_text: str  # the header's options as written (knitr's params.src)
    options: dict[str, Any]
    option_lines: list[str]  # the leading ``#|`` lines (and a blank after them)
    code: list[str]


# ---------------------------------------------------------------------------
# option values
# ---------------------------------------------------------------------------


def _split_top(text: str, seps: str) -> list[str]:
    """*text* split at the characters in *seps* outside quotes and brackets."""
    parts: list[str] = []
    depth = 0
    quote = ""
    start = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = ""
        elif ch in "\"'`":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(depth - 1, 0)
        elif ch in seps and depth == 0:
            parts.append(text[start:i])
            start = i + 1
        i += 1
    parts.append(text[start:])
    return parts


def _unquote(text: str) -> str | None:
    """The string a quoted R literal stands for, or None."""
    if len(text) < 2 or text[0] not in "\"'" or text[-1] != text[0]:
        return None
    body = text[1:-1]
    if re.search(rf"(?<!\\)(?:\\\\)*{text[0]}", body):  # two strings, not one
        return None
    try:
        out = json.loads('"' + body.replace('"', '\\"').replace("\\'", "'") + '"')
    except ValueError:
        return body
    return str(out)


def _value(text: str) -> Any:
    """An option value: a Python literal, or :class:`_Expr` when it is R code."""
    text = text.strip()
    if text in ("TRUE", "T"):
        return True
    if text in ("FALSE", "F"):
        return False
    if text in ("NA", "NULL"):
        return None
    quoted = _unquote(text)
    if quoted is not None:
        return quoted
    if _NUMBER.fullmatch(text):
        number = text.removesuffix("L")
        return int(number) if re.fullmatch(r"[-+]?\d+", number) else float(number)
    return _Expr(text)


def _parse_options(text: str) -> dict[str, Any]:
    """``label, name = value, ...`` as a dict (the label under ``"label"``)."""
    out: dict[str, Any] = {}
    label = None
    for part in _split_top(text, ",\n"):
        if not part.strip():
            continue
        eq = _split_top(part, "=")
        name = eq[0].strip().strip("`\"'")
        if len(eq) > 1 and re.fullmatch(r"[\w.]+", name) and not eq[1].startswith("="):
            out[name] = _value("=".join(eq[1:]))
        elif label is None:
            label = part.strip()
    if label is not None and "label" not in out:
        quoted = _unquote(label)
        out["label"] = label if quoted is None else quoted
    return out


def _yaml_options(text: str) -> dict[str, Any]:
    """YAML chunk options (``#| eval: false``); ``!expr`` values are R code."""
    try:
        data = _load_yaml(text)
    except Exception:
        return {}
    return {str(k): v for k, v in data.items()} if isinstance(data, dict) else {}


def _load_yaml(text: str) -> Any:
    import yaml

    class Loader(yaml.SafeLoader):
        pass

    # R's yaml package keeps dates as text
    Loader.yaml_implicit_resolvers = {
        k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:timestamp"]
        for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }
    for tag in ("!r", "!expr"):
        Loader.add_constructor(tag, lambda ld, node: _Expr(ld.construct_scalar(node)))
    return yaml.load(text, Loader=Loader)  # noqa: S506 - a SafeLoader subclass


def _text(value: Any) -> str:
    """An option value as text (a chunk label, an engine, a comment prefix)."""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        return _number(value)
    if isinstance(value, list):
        return _text(value[0]) if value else ""
    if isinstance(value, _Expr):
        return re.sub(r"\s", "", value)
    return str(value)


# ---------------------------------------------------------------------------
# reading the chunks
# ---------------------------------------------------------------------------


def _split_lines(lines: Sequence[str | None]) -> list[str]:
    out: list[str] = []
    for line in lines:
        if line is None:
            out.append("NA")
            continue
        out.extend(p.removesuffix("\r") for p in line.split("\n"))
    return out


def _md_closes(lines: list[str], j: int, fence: str) -> bool:
    """Whether line *j*, a fence of another length, ends a chunk opened by *fence*."""
    exact = re.compile(re.escape(fence) + r"\s*")
    begin = re.compile(re.escape(fence) + r"`*\{")
    for k in range(j + 1, len(lines)):
        if exact.fullmatch(lines[k]):
            return False
        if begin.match(lines[k]):
            return True
    return True


def _md_groups(lines: list[str]) -> list[_Chunk | list[str]]:
    groups: list[_Chunk | list[str]] = []
    text: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        m = _MD_BEGIN.match(lines[i])
        if m is None:
            text.append(lines[i])
            i += 1
            continue
        if text:
            groups.append(text)
            text = []
        prefix, ticks = m.group(1), m.group(2)
        fence = prefix + ticks
        exact = re.compile(re.escape(fence) + r"\s*")
        j = i + 1
        while j < n:
            line = lines[j]
            if line.startswith(fence + "{") and _MD_BEGIN.match(line):
                break  # the next chunk starts: this one has no closing fence
            if exact.fullmatch(line) or (_MD_END.match(line) and _md_closes(lines, j, fence)):
                break
            j += 1
        engine = m.group(3)
        options_text = re.sub(r"^\s*,*\s*|\s*,*\s*$", "", m.group(4))
        groups.append(_chunk(lines[i], engine, options_text, lines[i + 1 : j]))
        i = j + 1 if j < n and not _MD_BEGIN.match(lines[j]) else j
    if text:
        groups.append(text)
    return groups


def _delimited_groups(
    lines: list[str], begin: re.Pattern[str], end: re.Pattern[str], strip: str | None
) -> list[_Chunk | list[str]]:
    """The chunks of a Sweave-like document (*strip*: a prefix on every code line)."""
    groups: list[_Chunk | list[str]] = []
    text: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        m = begin.match(lines[i])
        if m is None:
            text.append(lines[i])
            i += 1
            continue
        if text:
            groups.append(text)
            text = []
        j = i + 1
        while j < n and not end.match(lines[j]) and not begin.match(lines[j]):
            j += 1
        code = lines[i + 1 : j]
        if strip is not None and code:
            code = [re.sub(strip, "", c, count=1) for c in code]
            spaces = min(len(c) - len(c.lstrip(" ")) for c in code)
            code = [c[spaces:] for c in code]
        options_text = re.sub(r"^\s*,*\s*|\s*,*\s*$", "", m.group(1))
        groups.append(_chunk(lines[i], "r", options_text, code))
        i = j + 1 if j < n and end.match(lines[j]) else j
    if text:
        groups.append(text)
    return groups


def _chunk(header: str, engine: str, options_text: str, code: list[str]) -> _Chunk:
    options = _parse_options(options_text)
    if engine.lower() != "r":
        options["engine"] = engine
    prefix = re.match(r"[\t >]*", header).group()  # type: ignore[union-attr]
    if prefix:
        code = [c.removeprefix(prefix).removeprefix(prefix.rstrip()) for c in code]
    found, option_lines, code = _chunk_options(str(options.get("engine", "r")), code)
    options.update(found)
    return _Chunk(options_text, options, option_lines, code)


def _chunk_options(engine: str, code: list[str]) -> tuple[dict[str, Any], list[str], list[str]]:
    """The ``#|`` option lines that open a chunk: (options, the lines, the code)."""
    lead = f"{_OPTION_COMMENT.get(engine, '#')}| "
    if not code or not code[0].startswith(lead):
        lead = "#| "
    n = 0
    while n < len(code) and code[n].startswith(lead):
        n += 1
    if n == 0:
        return {}, [], code
    meta = "\n".join(line[len(lead) :] for line in code[:n])
    if re.match(r"[^ :]+:($|\s)", meta):
        options = _yaml_options(meta)
    else:
        options = _parse_options(meta)
    label = options.pop("label", None)
    ident = options.pop("id", None)
    if label is not None or ident is not None:
        options["label"] = label if label is not None else ident
    option_lines, code = code[:n], code[n:]
    if code and _BLANK.fullmatch(code[0]):
        option_lines, code = [*option_lines, ""], code[1:]
    return options, option_lines, code


# ---------------------------------------------------------------------------
# writing the code
# ---------------------------------------------------------------------------


def _comment_out(code: list[str], prefix: str | None) -> list[str]:
    if not prefix:
        return code
    return [f"{prefix} {re.sub(' +$', '', line)}" for line in code]


def _expand(code: list[str], chunks: dict[str, list[str]], seen: tuple[str, ...]) -> list[str]:
    """*code* with each ``<<label>>`` line replaced by that chunk's code."""
    out: list[str] = []
    for line in code:
        m = _REF.match(line)
        label = m.group(2) if m else None
        if m is None or label not in chunks or label in seen:
            out.append(line)
            continue
        indent = m.group(1)
        out.extend(indent + c for c in _expand(chunks[label], chunks, (*seen, label)))
    return out


def _tangle(chunk: _Chunk, chunks: dict[str, list[str]], documentation: int) -> str:
    options = chunk.options
    if options.get("purl") is False:
        return ""
    engine = options.get("engine", "R")
    if not isinstance(engine, _Expr) and _text(engine).lower() != "r":
        comment = options.get("comment", "##")
        prefix = None if comment is None else "##" if isinstance(comment, _Expr) else _text(comment)
        return "\n".join(_comment_out(chunk.code, prefix))
    code = _expand(chunk.code, chunks, ())
    if options.get("eval") is False:
        code = _comment_out(code, "#")
    if options.get("error") is True:
        code = ["try({", *code, "})"]
    if documentation == 0:
        return "\n".join(code)
    head = chunk.options_text
    dashes = "-" * max(69 - len(head), 0)
    return "\n".join([f"## ----{head}{dashes}----", *chunk.option_lines, *code, ""])


# ---------------------------------------------------------------------------
# YAML params
# ---------------------------------------------------------------------------


def _number(x: float) -> str:
    from metacheck._r.base import as_character

    if math.isnan(x):
        return "NaN"
    if math.isinf(x):
        return "Inf" if x > 0 else "-Inf"
    return str(as_character(x))


def _r_name(name: str) -> str:
    reserved = {"if", "else", "repeat", "while", "function", "for", "next", "break", "in"}
    reserved |= {"TRUE", "FALSE", "NULL", "Inf", "NaN", "NA"}
    syntactic = re.fullmatch(r"(?:[^\W\d_]|\.(?![0-9]))[\w.]*|\.", name) is not None
    return name if syntactic and name not in reserved else "`" + name.replace("`", "\\`") + "`"


def _r_code(v: Any) -> str:
    """R source for a YAML value (a sequence of one kind is an atomic vector)."""
    if isinstance(v, _Expr):
        return v.strip()
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, int):
        return f"{v}L"
    if isinstance(v, float):
        return _number(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, dict):
        return "list(" + ", ".join(f"{_r_name(str(k))} = {_r_code(x)}" for k, x in v.items()) + ")"
    if isinstance(v, list):
        kinds = {bool if isinstance(x, bool) else type(x) for x in v}
        atomic = (
            v and (len(kinds) == 1 or kinds == {int, float}) and kinds <= {str, int, float, bool}
        )
        if atomic and float in kinds:
            v = [float(x) for x in v]
        return ("c(" if atomic else "list(") + ", ".join(_r_code(x) for x in v) + ")"
    return json.dumps(str(v), ensure_ascii=False)


def _params(lines: list[str]) -> list[str]:
    """``params <-`` and the list of the front matter's YAML params, or nothing."""
    start = next((i for i, line in enumerate(lines) if not _BLANK.fullmatch(line)), None)
    if start is None or not re.fullmatch(r"---\s*", lines[start]):
        return []
    end = next(
        (k for k in range(start + 1, len(lines)) if re.fullmatch(r"(---|\.\.\.)\s*", lines[k])),
        None,
    )
    if end is None:
        return []
    try:
        data = _load_yaml("\n".join(lines[start + 1 : end]))
    except Exception:
        return []
    params = data.get("params") if isinstance(data, dict) else None
    if not isinstance(params, dict):
        return []
    items = []
    for name, param in params.items():
        value = param.get("value") if isinstance(param, dict) else param
        if value is not None:
            items.append(f"{_r_name(str(name))} = {_r_code(value)}")
    return ["params <-", f"list({', '.join(items)})", ""] if items else []


# ---------------------------------------------------------------------------
# the document
# ---------------------------------------------------------------------------


def extract_r(lines: Sequence[str | None], documentation: int = 0) -> list[str]:
    """The R code of an R Markdown, Quarto or Sweave document, as lines.

    *documentation* is 0 (code only), 1 (chunk headers as comments) or 2 (the
    text between chunks as ``#'`` comments too). ``None`` lines read as
    ``"NA"``.
    """
    text = _split_lines(lines)
    params: list[str] = []
    if any(_MD_BEGIN.match(line) for line in text):
        groups = _md_groups(text)
        params = _params(text)
    elif syntax := next((f for f in _FORMATS if any(f[0].match(line) for line in text)), None):
        groups = _delimited_groups(text, *syntax)
    elif any(_MD_INLINE.search(line) for line in text):
        groups = [text]
        params = _params(text)
    else:
        return []
    chunks: dict[str, list[str]] = {}
    for g in groups:
        if isinstance(g, _Chunk) and g.code and "label" in g.options:
            chunks.setdefault(_text(g.options["label"]), g.code)
    res = [
        _tangle(g, chunks, documentation)
        if isinstance(g, _Chunk)
        else ("#' " + "\n#' ".join(g) if documentation == 2 else "")
        for g in groups
    ]
    while res and _BLANK.fullmatch(res[0]):
        res.pop(0)
    while res and _BLANK.fullmatch(res[-1]):
        res.pop()
    out = "\n".join([*params, "\n".join(res)])
    return [] if out == "" else out.split("\n")
