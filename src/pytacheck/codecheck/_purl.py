"""``knitr::purl(text = x, documentation = d, quiet = TRUE)`` in Python.

Port of the tangling path of knitr 1.52 (``knit()`` with ``tangle = TRUE``:
``detect_pattern()``, ``split_file()``/``group_indices()``, ``parse_block()``,
``tangle_block()``/``tangle_inline()``, ``label_code()``, ``comment_out()``,
``strip_white()``, ``knit_params()``) and of the xfun 0.60 helpers it relies on
(``split_lines()``, ``csv_options()``, ``divide_chunk()``). Chunk options are
parsed with :mod:`._rparse` (R's own grammar) the way ``csv_options()`` does
(``alist(...)``).

knitr *evaluates* the ``purl``, ``eval`` and ``child`` options (an error
drops the chunk). Here they are evaluated by a small R evaluator covering
constants, ``T``/``F``, ``!``, ``&&``, ``||``, comparisons, ``c()``,
``isTRUE()``/``isFALSE()``/``is.null()``/``identical()`` and ``interactive()``;
anything else (an undefined variable such as ``params``) is an error, as it
is in a fresh R session. YAML ``params`` are written with a port of
``dput()`` for literal values; ``!r`` expressions are evaluated with the same
small evaluator.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pytacheck._r.base import trimws
from pytacheck._r.regex import compile_r, grepl, gsub, regextract, sub
from pytacheck.codecheck._reval import NA, EvalError, is_false, r_eval
from pytacheck.codecheck._rparse import (
    MISSING,
    NULL,
    Const,
    Lang,
    RParseError,
    Sym,
    parse_exprs,
)

__all__ = ["purl"]

_WIDTH = 80  # getOption("width") in Rscript

# ---------------------------------------------------------------------------
# patterns (knitr all_patterns)
# ---------------------------------------------------------------------------

_MD = {
    "chunk.begin": r"^[	 >]*```+\s*\{([a-zA-Z0-9_]+( *[ ,].*)?)\}\s*$",
    "chunk.end": r"^[	 >]*```+\s*$",
    "ref.chunk": r"^\s*<<(.+)>>\s*$",
    "inline.code": r"(?<!(^``))(?<!(\n``))`r[ #]([^`]+)\s*`",
}
_PATTERNS: dict[str, dict[str, str]] = {
    "rnw": {
        "chunk.begin": r"^\s*<<(.*)>>=.*$",
        "chunk.end": r"^\s*@\s*(%+.*|)$",
        "inline.code": r"\\Sexpr\{([^}]+)\}",
        "inline.comment": r"^\s*%.*",
        "ref.chunk": r"^\s*<<(.+)>>\s*$",
        "header.begin": "(^|\n)\\s*\\\\documentclass[^}]+\\}",
        "document.begin": r"\s*\\begin\{document\}",
    },
    "brew": {"inline.code": r"<%[=]{0,1}\s+([^%]+)\s+[-]*%>"},
    "tex": {
        "chunk.begin": r"^\s*%+\s*begin.rcode\s*(.*)",
        "chunk.end": r"^\s*%+\s*end.rcode",
        "chunk.code": r"^\s*%+",
        "ref.chunk": r"^%+\s*<<(.+)>>\s*$",
        "inline.comment": r"^\s*%.*",
        "inline.code": r"\\rinline\{([^}]+)\}",
        "header.begin": "(^|\n)\\s*\\\\documentclass[^}]+\\}",
        "document.begin": r"\s*\\begin\{document\}",
    },
    "html": {
        "chunk.begin": r"^\s*<!--\s*begin.rcode\s*(.*)",
        "chunk.end": r"^\s*end.rcode\s*-->",
        "ref.chunk": r"^\s*<<(.+)>>\s*$",
        "inline.code": r"<!--\s*rinline(.+?)-->",
        "header.begin": r"\s*<head>",
    },
    "md": _MD,
    "rst": {
        "chunk.begin": r"^\s*[.][.]\s+\{r(.*)\}\s*$",
        "chunk.end": r"^\s*[.][.]\s+[.][.]\s*$",
        "chunk.code": r"^\s*[.][.]",
        "ref.chunk": r"^\.*\s*<<(.+)>>\s*$",
        "inline.code": r":r:`([^`]+)`",
    },
    "asciidoc": {
        "chunk.begin": r"^//\s*begin[.]rcode(.*)$",
        "chunk.end": r"^//\s*end[.]rcode\s*$",
        "chunk.code": r"^//+",
        "ref.chunk": r"^\s*<<(.+)>>\s*$",
        "inline.code": r"`r +([^`]+)\s*`|[+]r +([^+]+)\s*[+]",
        "inline.comment": r"^//.*",
    },
    "textile": {
        "chunk.begin": r"^###[.]\s+begin[.]rcode(.*)$",
        "chunk.end": r"^###[.]\s+end[.]rcode\s*$",
        "ref.chunk": r"^\s*<<(.+)>>\s*$",
        "inline.code": r"@r +([^@]+)\s*@",
        "inline.comment": r"^###[.].*",
    },
    "typst": dict(_MD),
}
_OUT_FORMAT = {
    "rnw": "latex",
    "tex": "latex",
    "html": "html",
    "md": "markdown",
    "rst": "rst",
    "brew": "brew",
    "asciidoc": "asciidoc",
    "textile": "textile",
    "typst": "typst",
}


class PurlError(ValueError):
    """An error knitr raises while tangling (the R call fails)."""


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def split_lines(x: Sequence[str]) -> list[str]:
    """``xfun::split_lines()``."""
    if not any("\n" in s for s in x):
        return list(x)
    out: list[str] = []
    for s in x:
        s = s[:-1] + "\n\n" if s.endswith("\n") else s
        if s == "":
            s = "\n"
        pieces = [p[:-1] if p.endswith("\r") else p for p in s.split("\n")]
        # R's strsplit() drops the empty string after a final separator
        if pieces and pieces[-1] == "":
            pieces.pop()
        out.extend(pieces)
    return out


def _is_blank(x: Sequence[str]) -> bool:
    """knitr ``is_blank()``."""
    return all(grepl(r"^\s*$", list(x))) if len(x) else True


def _strip_white(x: list[Any]) -> list[Any]:
    """knitr ``strip_white()`` on a vector of strings."""
    while x and _is_blank([x[0]]):
        x = x[1:]
    while x and _is_blank([x[-1]]):
        x = x[:-1]
    return x


def _one_string(x: Sequence[str]) -> str:
    return "\n".join(x)


def _line_prompt(x: list[str], prompt: str) -> list[str]:
    """knitr ``line_prompt()``: *prompt* before every line."""
    return [prompt + gsub(r"(?<=\n)(?=.|\n)", prompt, s, perl=True) for s in x]


def _comment_out(x: list[str] | None, prefix: Any = "##", newline: bool = True) -> list[str]:
    """knitr ``comment_out()`` (``which = TRUE``)."""
    lines = [] if x is None else list(x)
    lines = list(gsub("[\n]{2,}$", "\n", lines))
    if newline:
        lines = list(gsub("([^\n]|^)$", "\\1\n", lines))
    if prefix is None or (isinstance(prefix, float) and math.isnan(prefix)) or prefix == "":
        return lines
    pre = f"{prefix} "
    lines = list(gsub(" +([\n]*)$", "\\1", lines))
    if not lines:
        # character(0)[TRUE] is NA: the result is "<prefix> NA"
        return [f"{pre}NA"]
    return _line_prompt(lines, pre)


def _eval_lang(v: Any) -> Any:
    """knitr ``eval_lang()``: calls and symbols are evaluated, values kept."""
    if isinstance(v, Sym | Lang | Const):
        return r_eval(v)
    return v


# ---------------------------------------------------------------------------
# xfun::csv_options() and divide_chunk()
# ---------------------------------------------------------------------------


def _quote_label(x: str) -> str:
    x = sub(r"^\s*,?", "", x)
    if grepl("^\\s*[^'\"](,|\\s*$)", x):
        x = gsub("^\\s*([^'\"])(,|\\s*$)", "'\\1'\\2", x)
    elif grepl("^\\s*[^'\"](,|[^=]*(,|\\s*$))", x):
        x = gsub("^\\s*([^'\"][^=]*)(,|\\s*$)", "'\\1'\\2", x)
    return x


def _deparse_expr(x: Any) -> str:
    """``as.character(as.expression(x))`` for a label expression."""
    if isinstance(x, Sym):
        return x.name
    if isinstance(x, Const):
        if x.na:
            return "NA"
        if x.kind == "logical":
            return "TRUE" if x.value else "FALSE"
        if x.kind == "character":
            return x.value
        return _deparse_scalar(x)
    if isinstance(x, Lang):
        fun = _deparse_expr(x.fun)
        args = ", ".join(
            (f"{a[0].name} = " if a[0] is not None else "") + _deparse_expr(a[1]) for a in x.args
        )
        if fun in ("-", "+", "!") and len(x.args) == 1:
            return f"{fun}{_deparse_expr(x.args[0][1])}"
        if len(x.args) == 2 and fun in ("+", "-", "*", "/", ":", "^", "$", "@", "::", ":::"):
            sep = "" if fun in (":", "^", "$", "@", "::", ":::") else " "
            return f"{_deparse_expr(x.args[0][1])}{sep}{fun}{sep}{_deparse_expr(x.args[1][1])}"
        return f"{fun}({args})"
    return str(x)


def _deparse_scalar(x: Const) -> str:
    from pytacheck._r.base import as_character

    if x.kind == "integer":
        return f"{x.value}L"
    return str(as_character(x.value))


def csv_options(x: str) -> dict[str, Any]:
    """``xfun::csv_options()``: chunk options as a dict of R syntax trees."""
    src = f"alist({_quote_label(x)})"
    try:
        exprs = parse_exprs([src])
    except RParseError as exc:
        raise PurlError(f"Invalid syntax for chunk options:\n{x}\n{exc}") from exc
    if len(exprs) != 1 or not isinstance(exprs[0], Lang):
        raise PurlError(f"Invalid syntax for chunk options:\n{x}")
    items = [[a[0].name if a[0] is not None else "", a[1]] for a in exprs[0].args]
    # remove empty (missing) unnamed options
    items = [it for it in items if not (it[0] == "" and it[1] is MISSING)]
    unnamed = [i for i, it in enumerate(items) if it[0] == ""]
    if len(unnamed) > 1:
        raise PurlError(
            f"Invalid chunk options: {x}\n\n"
            "All options must be of the form 'tag=value' except for the chunk label."
        )
    res: dict[str, Any] = {}
    names = [it[0] for it in items]
    if "label" not in names and unnamed:
        items[unnamed[0]][0] = "label"
    for name, value in items:
        if name not in res:
            res[name] = value
    label = res.get("label")
    if label is None:
        res["label"] = ""
        label = ""
    if isinstance(label, Const) and label.kind == "character" and not label.na:
        label = label.value
    elif not isinstance(label, str):
        label = _deparse_expr(label).replace(" ", "")
    res["label"] = label
    if label == "":
        del res["label"]
    return res


_COMMENT_CHARS: dict[str, list[str]] = {}
for _chars, _langs in {
    "#": [
        "awk",
        "bash",
        "coffee",
        "gawk",
        "julia",
        "octave",
        "perl",
        "powershell",
        "python",
        "r",
        "ruby",
        "sed",
        "stan",
    ],
    "//": [
        "asy",
        "cc",
        "csharp",
        "d3",
        "dot",
        "fsharp",
        "go",
        "groovy",
        "java",
        "js",
        "node",
        "ojs",
        "Rcpp",
        "sass",
        "scss",
        "scala",
    ],
    "%%": ["mermaid"],
    "%": ["matlab", "tikz"],
    "/* */": ["c", "css"],
    "* ;": ["sas"],
    "--": ["haskell", "lua", "mysql", "psql", "sql"],
    "!": ["fortran", "fortran95"],
    "*": ["stata"],
}.items():
    for _lang in _langs:
        _COMMENT_CHARS[_lang] = _chars.split(" ")
_COMMENT_CHARS["apl"] = ["\u235d"]


def _yaml_chunk_options(meta: list[str]) -> dict[str, Any] | None:
    """``xfun::yaml_load(envir = FALSE)`` of ``#|`` options (values as R trees)."""
    import yaml

    from pytacheck.codecheck.core import _yaml_loader

    loader = _yaml_loader()

    def expr(ld: Any, node: Any) -> Any:
        src = ld.construct_scalar(node)
        try:
            exprs = parse_exprs([src])
        except RParseError as exc:
            raise PurlError(str(exc)) from exc
        return exprs[0] if len(exprs) == 1 else Lang(Sym("expression"), [[None, e] for e in exprs])

    loader.add_constructor("!expr", expr)
    loader.add_constructor("!r", expr)
    try:
        data = yaml.load("\n".join(meta), Loader=loader)  # noqa: S506 - SafeLoader subclass
    except PurlError:
        raise
    except Exception as exc:
        raise PurlError(str(exc)) from exc
    if not isinstance(data, dict) or not data:
        return None
    return {str(k): _yaml_to_r(v) for k, v in data.items()}


def _yaml_to_r(v: Any) -> Any:
    if isinstance(v, Sym | Lang | Const):
        return v
    if isinstance(v, bool):
        return Const(v, "logical")
    if isinstance(v, int):
        return Const(v, "integer")
    if isinstance(v, float):
        return Const(v, "double")
    if isinstance(v, str):
        return Const(v, "character")
    if v is None:
        return NULL
    return v


def divide_chunk(
    engine: str, code: list[str]
) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    """``xfun::divide_chunk(engine, code, strict = FALSE)``: (options, src, code)."""
    if not code:
        return None, [], code
    chars = _COMMENT_CHARS.get(engine, ["#"])
    s1 = f"{chars[0]}| "
    s2 = chars[1] if len(chars) > 1 else ""
    i1 = [c.startswith(s1) for c in code]
    if not i1[0] and s1 != "#| ":
        i1 = [c.startswith("#| ") for c in code]
        s1, s2 = "#| ", ""
    if not i1[0]:
        return None, [], code
    if s2 == "":
        i2 = [True] * len(code)
        n2 = len(code) if all(i1) else i1.index(False)
    else:
        i2 = [str(trimws(c, "right")).endswith(s2) for c in code]
        # which.min()/which.max() of a logical vector: first FALSE/TRUE, else 1
        n2 = (
            (i2.index(False) + 1 if False in i2 else 1) - 1
            if i2[0]
            else (i2.index(True) + 1 if True in i2 else 1)
        )
    if n2 == 0:  # 1:0 is c(1, 0): the first line only
        src, rest = code[:1], code[1:]
    else:
        src, rest = code[:n2], code[n2:]
    meta = []
    for k, line in enumerate(src):
        c1 = len(s1) * i1[k]
        c2 = len(line) - (len(s2) * i2[k] if s2 else 0)
        meta.append(line[c1:c2] if c2 > c1 else "")
    if grepl(r"^[^ :]+:($|\s)", meta[0]):
        opts = _yaml_chunk_options(meta) or {}
    else:
        opts = csv_options("\n".join(meta))
    label = opts.get("label", opts.get("id"))
    opts.pop("id", None)
    if label is not None:
        if isinstance(label, Const) and label.kind == "character":
            label = label.value
        opts["label"] = label
    elif "label" in opts:
        del opts["label"]
    if rest and _is_blank([rest[0]]):
        rest = rest[1:]
        src = [*src, ""]
    return opts, src, rest


# ---------------------------------------------------------------------------
# parsing a document
# ---------------------------------------------------------------------------


@dataclass
class _Block:
    params: dict[str, Any]
    params_src: str
    params_chunk: list[str]


@dataclass
class _Inline:
    input: str
    code: list[str] = field(default_factory=list)


@dataclass
class _State:
    patterns: dict[str, str]
    markdown: bool
    documentation: int
    counter: int = 1
    knit_code: dict[str, list[str]] = field(default_factory=dict)

    def unnamed_chunk(self, prefix: str = "unnamed-chunk") -> str:
        i = self.counter
        self.counter += 1
        return f"{prefix}-{i}"


def detect_pattern(text: list[str]) -> str | None:
    """knitr ``detect_pattern()`` without a file extension."""
    for name, pats in _PATTERNS.items():
        for key in ("chunk.begin", "inline.code"):
            pat = pats.get(key)
            if pat is not None and any(grepl(pat, text, perl=True)):
                return name
    return None


def _group_indices(state: _State, lines: list[str]) -> list[int]:
    begin = grepl(state.patterns["chunk.begin"], lines)
    end = grepl(state.patterns["chunk.end"], lines)
    md = state.markdown
    in_chunk = False
    pattern_end: str | None = None
    b = 0
    g = 0
    out: list[int] = []
    for i, (is_begin, is_end, line) in enumerate(zip(begin, end, lines, strict=True), start=1):
        if i == 1:
            if is_begin:
                in_chunk = True
                b = i
                g = 0
            else:
                g = 1
            out.append(g)
            continue
        if in_chunk and is_begin:
            if not md or _match_chunk_begin(pattern_end, line):
                g += 2
                if md:
                    b = i
            out.append(g)
            continue
        if in_chunk and is_end and _match_chunk_end(pattern_end, line, i, b, lines):
            in_chunk = False
            g += 1
            out.append(g - 1)
            continue
        if not in_chunk and is_begin:
            in_chunk = True
            if md:
                pattern_end = sub("(^[\t >]*```+).*", "^\\1\\\\s*$", line)
                b = i
            g = g + 2 - g % 2
        out.append(g)
    return out


def _match_chunk_begin(pattern_end: str | None, x: str | list[str], pat: str = "^\\1\\\\{") -> Any:
    if pattern_end is None:  # grepl(NA, x) is NA, and if (NA) fails
        raise PurlError("missing value where TRUE/FALSE needed")
    return grepl(sub("^([^`]*`+).*", pat, pattern_end), x)


def _match_chunk_end(pattern: str | None, line: str, i: int, b: int, lines: list[str]) -> bool:
    if pattern is None or grepl(pattern, line):
        return True
    n = len(lines)
    if i < n:
        later = grepl(pattern, lines[i:])
        k = next((j + 1 for j, hit in enumerate(later) if hit), None)
        if k is not None:
            if k == 1:
                return False
            between = lines[i : i + k - 1]
            if not any(_match_chunk_begin(pattern, between, "^\\1`*\\\\{")):
                return False
    fence = gsub(r"\^(\s*`+).*", r"\1", pattern)
    raise PurlError(
        f'The closing fence on line {i} ("{line}") does not match the opening fence '
        f'"{fence}" on line {b}.'
    )


def _split_file(state: _State, lines: list[str]) -> list[Any]:
    pats = state.patterns
    if "chunk.begin" not in pats or "chunk.end" not in pats:
        return [_parse_inline(lines, pats)]
    groups: dict[int, list[str]] = {}
    for idx, line in zip(_group_indices(state, lines), lines, strict=True):
        groups.setdefault(idx, []).append(line)
    out: list[Any] = []
    begin_rx = compile_r(pats["chunk.begin"])
    for key in sorted(groups):
        g = groups[key]
        if begin_rx.search(g[0]) is not None:
            n = len(g)
            if n >= 2 and grepl(pats["chunk.end"], g[-1]):
                g = g[:-1]
            g = _strip_block(g, pats.get("chunk.code"))
            params_src = str(gsub(pats["chunk.begin"], "\\1", g[0])).strip(" \t\r\n")
            out.append(_parse_block(state, g[1:], g[0], params_src))
        else:
            out.append(_parse_inline(g, pats))
    return out


def _strip_block(x: list[str], prefix: str | None) -> list[str]:
    if prefix is None or len(x) <= 1:
        return x
    rest = list(sub(prefix, "", x[1:]))
    spaces = min(len(regextract("^ *", s) or "") for s in rest)
    if spaces > 0:
        rest = [s[spaces:] for s in rest]
    return [x[0], *rest]


def _parse_block(state: _State, code: list[str], header: str, params_src: str) -> _Block:
    params = params_src
    engine = "r"
    if state.markdown:
        engine = str(sub(r"^([a-zA-Z0-9_]+).*$", "\\1", params))
        params = str(sub(r"^([a-zA-Z0-9_]+)", "", params))
    params = str(gsub(r"^\s*,*\s*|\s*,*\s*$", "", params))
    if engine.lower() != "r":
        params = f'{params}, engine="{engine}"'
        params = str(gsub(r"^\s*,\s*", "", params))
    params_src = params
    opts = csv_options(params)
    spaces = str(gsub("^([\t >]*).*", "\\1", header))
    if spaces:
        opts["indent"] = Const(spaces, "character")
        code = list(gsub(f"^{spaces}", "", code))
        trimmed = sub(r"\s+$", "", spaces)
        code = list(gsub(f"^{trimmed}", "", code))
    part_opts, part_src, code = divide_chunk(engine, code)
    if part_opts:
        opts.update(part_opts)
    label = opts.get("label")
    if label is None:
        label = state.unnamed_chunk()
        opts["label"] = label
    if code or opts.get("file") is not None or opts.get("code") is not None:
        if label in state.knit_code:
            label = state.unnamed_chunk(label)
            opts["label"] = label
        state.knit_code[label] = list(code)
    return _Block(opts, params_src, part_src)


def _parse_inline(lines: list[str], pats: dict[str, str]) -> _Inline:
    lines = list(lines)
    comment = pats.get("inline.comment")
    code_pat = pats.get("inline.code")
    if comment is not None and code_pat is not None:
        hits = grepl(comment, lines)
        lines = [
            str(gsub(code_pat, "\\1", s)) if h else s for s, h in zip(lines, hits, strict=True)
        ]
    return _Inline(_one_string(lines))


# ---------------------------------------------------------------------------
# tangling
# ---------------------------------------------------------------------------


def _parse_chunk(state: _State, x: list[str]) -> list[str]:
    """knitr ``parse_chunk()``: expand ``<<label>>`` references."""
    rc = state.patterns.get("ref.chunk")
    if not x or rc is None:
        return x
    hits = grepl(rc, x)
    if not any(hits):
        return x
    out: list[str] = []
    for line, hit in zip(x, hits, strict=True):
        if hit:
            label = str(sub(rc, "\\1", line))
            if label in state.knit_code:
                indent = str(gsub(r"^(\s*).*", "\\1", line))
                block = _parse_chunk(state, state.knit_code[label])
                if not block or not any(block):
                    out.extend([indent] * len(block))
                elif indent == "":
                    out.extend(block)
                else:
                    out.extend(_line_prompt(block, indent))
                continue
        out.append(line)
    return out


def _tangle_block(state: _State, x: _Block) -> str:
    params: dict[str, Any] = {
        "eval": True,
        "comment": "##",
        "error": True,
        "child": None,
        "engine": "R",
        "purl": True,
    }
    params.update(x.params)
    for o in ("purl", "eval", "child"):
        try:
            params[o] = _eval_lang(params[o])
        except EvalError:
            params["purl"] = False
    if is_false(params["purl"]):
        return ""
    label = params["label"]
    ev = params["eval"]
    engine = params["engine"]
    if isinstance(engine, Const | Sym | Lang):
        try:
            engine = r_eval(engine)
        except EvalError as exc:
            raise PurlError(str(exc)) from exc
    if engine != "R":
        comment = params["comment"]
        if isinstance(comment, Const | Sym | Lang):
            try:
                comment = r_eval(comment)
            except EvalError as exc:
                raise PurlError(str(exc)) from exc
            comment = math.nan if comment is NA else comment
        return _one_string(_comment_out(state.knit_code.get(label), comment, newline=False))
    if not is_false(ev) and params["child"] is not None:
        code: list[str] | None = _knit_children(state, params["child"])
    else:
        code = state.knit_code.get(label)
    code = _parse_chunk(state, code) if code else code
    if is_false(ev):
        code = _comment_out(code, "#", newline=False)
    error = x.params.get("error")
    if isinstance(error, Const) and error.kind == "logical" and error.value is True:
        code = ["try({", *(code or []), "})"]
    if state.documentation == 0:
        return _one_string(code or [])
    return _label_code(code or [], x)


def _knit_children(state: _State, child: Any) -> list[str]:
    files = child if isinstance(child, list) else [child]
    if len(files) == 1 and isinstance(files[0], str):
        files = [f.strip() for f in files[0].replace(";", ",").split(",")]
    out: list[str] = []
    for f in files:
        try:
            with open(str(f), encoding="utf-8") as fh:
                text = fh.read().split("\n")
        except OSError as exc:
            raise PurlError(f"cannot open file '{f}': {exc}") from exc
        if text and text[-1] == "":
            text.pop()
        out.append(purl_text(text, state.documentation, child=True))
    return ["\n".join(out)]


def _label_code(code: list[str], x: _Block) -> str:
    src = x.params_src
    dashes = "-" * max(_WIDTH - 11 - len(src), 0)
    return _one_string([f"## ----{src}{dashes}----", *x.params_chunk, *code, ""])


def _tangle_inline(state: _State, x: _Inline) -> str:
    if state.documentation == 2:
        return "#' " + x.input.replace("\n", "\n#' ")
    return ""


# ---------------------------------------------------------------------------
# YAML params and dput()
# ---------------------------------------------------------------------------


def _front_matter_params(lines: list[str]) -> list[str] | None:
    """knitr ``knit_params()`` + ``dput(flatten_params(.))`` lines, or None."""
    delims = [i for i, h in enumerate(grepl(r"^(---|\.\.\.)\s*$", lines)) if h]
    if len(delims) < 2 or delims[1] - delims[0] <= 1:
        return None
    if not (delims[0] == 0 or _is_blank(lines[: delims[0]])):
        return None
    if not grepl(r"^---\s*$", lines[delims[0]]):
        return None
    fm = lines[delims[0] : delims[1] + 1]
    if len(fm) <= 2:
        return None
    fm = fm[1:-1]
    if not any(grepl("^params:", fm)):
        return None
    text = "\n".join(fm)
    if grepl(r":\s*$", text):
        return None
    doc = _load_params_yaml(text)
    if not isinstance(doc, dict) or doc.get("params") is None:
        return None
    params = doc["params"]
    if not isinstance(params, dict) or not params:
        return None
    flat: list[tuple[str, Any]] = []
    for name, param in params.items():
        if isinstance(param, _RExpr):
            value = param.value
        elif isinstance(param, dict):
            if "value" not in param:
                raise PurlError(f"no value field specified for YAML parameter '{name}'")
            value = param["value"]
            if isinstance(value, _RExpr):
                value = value.value
        else:
            value = param
        if value is not None:  # res[[name]] = NULL adds nothing
            flat.append((str(name), value))
    return ["params <-", *_dput(_RNamedList(flat)), ""]


class _RExpr:
    def __init__(self, value: Any) -> None:
        self.value = value


class _RNamedList(list):  # type: ignore[type-arg]
    pass


def _atomic(v: Any) -> Any:
    """An evaluated ``c(...)`` of scalars is an atomic vector (a tuple for dput)."""
    if isinstance(v, list) and v and all(isinstance(e, str | int | float | bool) for e in v):
        kinds = {bool} if all(isinstance(e, bool) for e in v) else {type(e) for e in v}
        if kinds <= {int, float} or len(kinds) == 1:
            return tuple(float(e) if float in kinds and not isinstance(e, bool) else e for e in v)
    return v


def _load_params_yaml(text: str) -> Any:
    """``yaml.load(handlers = knit_params_handlers())``."""
    import yaml

    from pytacheck.codecheck.core import _r_yaml, _yaml_loader

    loader = _yaml_loader()

    def expr(ld: Any, node: Any) -> _RExpr:
        src = ld.construct_scalar(node)
        try:
            exprs = parse_exprs([src])
            value = r_eval(exprs[-1]) if exprs else None
        except (RParseError, EvalError) as exc:
            raise PurlError(str(exc)) from exc
        return _RExpr(_atomic(value))

    def boolean(ld: Any, node: Any) -> Any:
        # knitr's `bool#yes`/`bool#no` handlers keep "y"/"n" as strings
        value = str(ld.construct_scalar(node))
        if value.lower() in ("y", "n"):
            return value
        return value.lower() in ("yes", "true", "on")

    loader.add_constructor("tag:yaml.org,2002:bool", boolean)
    loader.add_constructor("!r", expr)
    loader.add_constructor("!expr", expr)
    try:
        return _r_yaml(yaml.load(text, Loader=loader))  # noqa: S506 - SafeLoader subclass
    except PurlError:
        raise
    except Exception as exc:  # yaml.load() errors abort knit()
        raise PurlError(str(exc)) from exc


def _is_syntactic(name: str) -> bool:
    import regex

    reserved = {
        "if",
        "else",
        "repeat",
        "while",
        "function",
        "for",
        "next",
        "break",
        "TRUE",
        "FALSE",
        "NULL",
        "Inf",
        "NaN",
        "NA",
        "NA_integer_",
        "NA_real_",
        "NA_character_",
        "in",
    }
    if name in reserved:
        return False
    return regex.fullmatch(r"(?:[\p{L}.][\p{L}\p{N}._]*)", name) is not None and not regex.match(
        r"^\.[0-9]", name
    )


def _encode_string(s: str) -> str:
    """R's ``EncodeString(s, quote = '"')`` in a UTF-8 locale."""
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\a":
            out.append("\\a")
        elif ch == "\b":
            out.append("\\b")
        elif ch == "\f":
            out.append("\\f")
        elif ch == "\v":
            out.append("\\v")
        elif o < 0x20 or o == 0x7F:
            out.append(f"\\{o:03o}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


class _Deparser:
    """The subset of R's ``deparse2buff()`` that ``dput()`` needs for params."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.buf = ""
        self.len = 0
        self.indent = 0
        self.startline = True
        self.cutoff = 60

    def put(self, s: str) -> None:
        if self.startline:
            self.startline = False
            for i in range(1, self.indent + 1):
                self.put("    " if i <= 4 else "  ")
        self.buf += s
        self.len += len(s.encode("utf-8"))

    def writeline(self) -> None:
        self.lines.append(self.buf)
        self.buf = ""
        self.len = 0
        self.startline = True

    def linebreak(self, state: list[bool]) -> None:
        if self.len > self.cutoff:
            if not state[0]:
                state[0] = True
                self.indent += 1
            self.writeline()

    def name(self, n: str) -> None:
        self.put(n if _is_syntactic(n) else f"`{n}`")
        self.put(" = ")

    def value(self, v: Any) -> None:
        if v is None:
            self.put("NULL")
        elif isinstance(v, _RNamedList | dict):
            items = list(v.items()) if isinstance(v, dict) else list(v)
            self.put("list(")
            lb = [False]
            for i, (k, x) in enumerate(items):
                if i > 0:
                    self.put(", ")
                self.linebreak(lb)
                self.name(str(k))
                self.value(x)
            if lb[0]:
                self.indent -= 1
            self.put(")")
        elif isinstance(v, tuple):
            self.atomic(list(v))
        elif isinstance(v, list):
            self.put("list(")
            lb = [False]
            for i, x in enumerate(v):
                if i > 0:
                    self.put(", ")
                self.linebreak(lb)
                self.value(x)
            if lb[0]:
                self.indent -= 1
            self.put(")")
        else:
            self.atomic([v])

    def atomic(self, vals: list[Any]) -> None:
        from pytacheck._r.base import as_character

        if all(isinstance(x, bool) for x in vals):
            enc = ["TRUE" if x else "FALSE" for x in vals]
        elif all(isinstance(x, int) and not isinstance(x, bool) for x in vals):
            if (
                len(vals) > 1
                and abs(vals[1] - vals[0]) == 1
                and all(vals[i] - vals[i - 1] == vals[1] - vals[0] for i in range(2, len(vals)))
            ):
                self.put(f"{vals[0]}:{vals[-1]}")
                return
            enc = [f"{x}L" for x in vals]
        elif all(isinstance(x, int | float) and not isinstance(x, bool) for x in vals):
            enc = []
            for x in vals:
                if isinstance(x, float) and math.isnan(x):
                    enc.append("NaN")
                elif isinstance(x, float) and math.isinf(x):
                    enc.append("Inf" if x > 0 else "-Inf")
                else:
                    enc.append(str(as_character(float(x))))
        else:
            enc = [_encode_string(str(x)) for x in vals]
        need_c = len(vals) > 1
        if need_c:
            self.put("c(")
        for i, e in enumerate(enc):
            self.put(e)
            if i < len(enc) - 1:
                self.put(", ")
            if len(enc) > 1 and self.len > self.cutoff:
                self.writeline()
        if need_c:
            self.put(")")


def _dput(v: Any) -> list[str]:
    d = _Deparser()
    d.value(v)
    d.writeline()
    return d.lines


# ---------------------------------------------------------------------------
# purl()
# ---------------------------------------------------------------------------


def purl_text(lines: Sequence[str], documentation: int = 1, child: bool = False) -> str:
    """The tangled R script as one string (knit()'s ``res``), or ``""``."""
    text = split_lines(list(lines))
    if not text:
        return ""
    name = detect_pattern(text)
    if name is None:
        return ""
    state = _State(
        patterns=_PATTERNS[name],
        markdown=_PATTERNS[name] == _MD,  # identical(patterns, all_patterns$md)
        documentation=int(documentation),
    )
    params = None
    if _OUT_FORMAT[name] == "markdown":
        if child:
            if grepl(r"^---\s*$", text[0]):
                idx = [i for i, h in enumerate(grepl(r"^---\s*$", text)) if h]
                if len(idx) >= 2:
                    text = [""] * (idx[1] + 1) + text[idx[1] + 1 :]
        else:
            params = _front_matter_params(text)
    groups = _split_file(state, text)
    res = [
        _tangle_block(state, g) if isinstance(g, _Block) else _tangle_inline(state, g)
        for g in groups
    ]
    res = _strip_white(res)
    out = _one_string(res)
    if child:
        return out
    return "\n".join([*(params or []), out])


def purl(lines: Sequence[str], documentation: int = 0) -> str:
    """The file ``knitr::purl(text = lines, output = f)`` writes (``""`` = empty file)."""
    text = split_lines(list(lines))
    if not text:
        return ""
    return purl_text(text, documentation) + "\n"
