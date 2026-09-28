"""Read Stata Markup and Control Language (``.smcl``) output logs.

Port of ``R/stata.R``. A ``.smcl`` log is plain text with brace-delimited
markup directives (``{txt}``, ``{col 40}``, ``{hline 20}``, ...). Each line is
rendered to the plain text Stata's Results window would show, the transcript
is split into one chunk per ``. <command>`` echo, and fixed-width result tables
(bounded by ``{hline}`` rules) and one-line ``name = value`` statistics are
extracted from each chunk's output.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from functools import cache
from typing import TYPE_CHECKING, Any

from pytacheck._r import grepl, regexec, regextract_all, sub, trimws
from pytacheck.statout.spv import (
    _as_integer,
    _file_path,
    _file_path_sans_ext,
    _html_page,
    _make_unique,
    _r_dirname,
    _read_lines,
    _spv_html_escape,
    _spv_table_html,
    _write_lines,
)

if TYPE_CHECKING:
    import pandas as pd

__all__ = ["export_stata_smcl_html", "import_stata_smcl"]

# Zero-width style/mode directives (documentation only, as in R: every
# payload-free directive is dropped by the renderer anyway).
_SMCL_ZERO_WIDTH = (
    "txt", "text", "res", "inp", "cmd", "com", "err", "sf", "bf", "it", "smcl",
    "ul off", "ul on", "hilite", "hi", "reset",
)  # fmt: skip

# {c NAME} box-drawing / literal-brace codes.
_SMCL_C_CODES: dict[str, str] = {
    "|": "|",
    "+": "+",
    "-": "-",
    "TT": "┬",
    "BT": "┴",
    "LT": "├",
    "RT": "┤",
    "TLC": "┌",
    "TRC": "┐",
    "BLC": "└",
    "BRC": "┘",
    "-(": "{",
    ")-": "}",
}

# placeholders for the literal braces of {c -(} / {c )-} during the walk
_BRACE_HOLD = {"{": "\ue000", "}": "\ue001"}

_ECHO_RE = r"^(\. |> |\s*[0-9]+\. )"
_ALIGN_RE = r"^(lalign|ralign|center|rcenter) ?([0-9]*):(.*)$"


def _smcl_render_line(line: str) -> str:
    """Port of R/stata.R::.smcl_render_line(): one SMCL line as plain text.

    Three differences from metacheck (UPSTREAM_ISSUES U146): ``{c 0xNN}`` is
    read as hexadecimal (R reads it as decimal: ``{c 0x41}`` rendered ``)``);
    ``{c -(}`` and ``{c )-}`` give literal braces (the ``{c NAME}`` codes are
    substituted before the walk, as in R, but the two braces as placeholders:
    R's walk re-parses and drops them); and a ``{dup N:...}`` whose count is
    not a usable integer is left out (R renders ``NA``, after which any column
    directive fails the whole read).
    """
    out = line
    for code, rep in _SMCL_C_CODES.items():
        out = out.replace("{c " + code + "}", _BRACE_HOLD.get(rep, rep))
    result: list[str] = []
    pos = 0
    i = 1
    n = len(out)
    while i <= n:
        ch = out[i - 1]
        if ch != "{":
            result.append(ch)
            pos += 1
            i += 1
            continue
        close = out.find("}", i - 1) - (i - 1) + 1
        if close <= 0:
            result.append(ch)
            pos += 1
            i += 1
            continue
        directive = out[i : i + close - 2]
        i += close

        if directive.startswith("col "):
            target = _as_integer(directive[4:])
            if target is not None and target > pos:
                result.append(" " * (target - pos))
                pos = target
            continue
        if directive.startswith("space "):
            k = _as_integer(directive[6:])
            if k is not None and k > 0:
                result.append(" " * k)
                pos += k
            continue
        if directive == "hline" or directive.startswith("hline "):
            k = _as_integer(str(sub("^hline ?", "", directive)))
            if k is None:
                k = 78 - pos
            if k > 0:
                result.append("-" * k)
                pos += k
            continue
        if directive == ".-":
            result.append("-" * max(1, 78 - pos))
            pos = 78
            continue
        if directive.startswith("dup "):
            m = regexec("^dup ([0-9]+):(.*)$", directive)
            if len(m) == 3:
                k = _as_integer(m[1])
                if k is not None:
                    txt = m[2] * k
                    result.append(txt)
                    pos += len(txt)
            continue
        if directive.startswith("char ") or directive.startswith("c 0x"):
            if directive.startswith("c 0x"):
                hexa = directive[4:]
                char_code = int(hexa, 16) if re.fullmatch("[0-9A-Fa-f]{1,2}", hexa) else None
            else:
                char_code = _as_integer(directive[5:])
            if char_code is not None and 0 <= char_code <= 255:
                result.append(chr(char_code) if char_code else "")
                pos += 1
            continue
        al = regexec(_ALIGN_RE, directive)
        if len(al) == 4 and al[0] != "":
            kind, width, txt = al[1], _as_integer(al[2]), al[3]
            if width is None or width <= len(txt):
                result.append(txt)
                pos += len(txt)
            else:
                pad = width - len(txt)
                if kind == "lalign":
                    padded = txt + " " * pad
                elif kind == "ralign":
                    padded = " " * pad + txt
                else:
                    padded = " " * (pad // 2) + txt + " " * (pad - pad // 2)
                result.append(padded)
                pos += width
            continue
        colon = directive.find(":")
        if colon >= 0:
            txt = directive[colon + 1 :]
            result.append(txt)
            pos += len(txt)
            continue
        # a bare directive (style marker, comment, unknown): zero-width
    return "".join(result).replace(_BRACE_HOLD["{"], "{").replace(_BRACE_HOLD["}"], "}")


def _smcl_render(lines: Sequence[str]) -> list[str]:
    """Port of R/stata.R::.smcl_render(): render every line."""
    return [_smcl_render_line(ln) for ln in lines]


def _smcl_command_chunks(rendered: Sequence[str]) -> list[dict[str, Any]]:
    """Port of R/stata.R::.smcl_command_chunks(): ``{"command", "output"}`` per command.

    A chunk's command is its ``". "`` line and the echo lines right after it
    (``"> "`` continuations, numbered loop lines). R counts the echo-looking
    lines anywhere in the chunk, so output rows such as ``list``'s
    ``"  1. | ... |"`` were taken as command text and dropped from the output
    (as ``.r_echo_chunks()`` does for R output, UPSTREAM_ISSUES U140).
    """
    rendered = list(rendered)
    is_echo = grepl(_ECHO_RE, rendered)
    if not any(is_echo):
        return []
    is_new = grepl(r"^\. ", rendered)
    starts = [i for i, v in enumerate(is_new) if v]
    if not starts:
        return []
    ends = [s - 1 for s in starts[1:]] + [len(rendered) - 1]
    out = []
    for s, e in zip(starts, ends, strict=True):
        seg = rendered[s : e + 1]
        echo_n = 0
        for v in grepl(_ECHO_RE, seg):
            if not v:
                break
            echo_n += 1
        cmd = trimws(sub(_ECHO_RE, "", seg[:echo_n]))
        output = seg[echo_n:] if echo_n < len(seg) else []
        out.append({"command": " ".join(cmd), "output": output})
    return out


def _stata_is_numlike(x: Any) -> Any:
    """Port of R/stata.R::.stata_is_numlike() (vectorised like ``grepl()``)."""
    t = trimws(x)
    a = grepl(r"^-?[0-9][0-9.,]*(e[-+]?[0-9]+)?$", t, ignore_case=True)
    b = grepl(r"^[<>]\s*-?[0-9.]+(e[-+]?[0-9]+)?$", t, ignore_case=True)
    c = grepl(r"(?i)^(inf|-?inf|na|nan|\.)$", t)
    if isinstance(a, bool):
        return a or b or c
    return [p or q or r for p, q, r in zip(a, b, c, strict=True)]


def _split_block(block: Sequence[str | None]) -> list[list[str]] | None:
    """Blank-column ("river") splitting shared by the Stata and Mplus readers.

    Port of R/stata.R::.stata_split_block() (and R/mplus.R's identical
    ``.mplus_split_block()``): lines are padded to a common width and cut at
    every run of columns that is blank in all lines. ``NA`` lines render as
    ``"NA"``, as ``formatC()`` does.
    """
    lines = ["NA" if b is None else b.replace("\t", " " * 8) for b in block]
    if not lines:
        return None
    w = max(len(b) for b in lines)
    padded = [b.ljust(w) for b in lines]
    nb = [set(col) != _BLANK for col in zip(*padded, strict=True)]
    if not any(nb):
        return None
    runs: list[tuple[int, int]] = []
    c = 0
    while c < w:
        if nb[c]:
            s = c
            while c < w and nb[c]:
                c += 1
            runs.append((s, c))
        else:
            c += 1
    return [[p[s:e].strip(_WS) for p in padded] for s, e in runs]


def _stata_split_block(block: Sequence[str]) -> list[list[str]] | None:
    """Port of R/stata.R::.stata_split_block()."""
    return _split_block(block)


_RULE_RE = "^[-┬┴├┤┌┐└┘+]+$"
_WS = " \t\r\n"  # trimws()'s default whitespace
_BLANK = {" "}


def _trim(x: str | None) -> str | None:
    """R ``trimws()`` of one value (``NA`` stays ``NA``)."""
    return None if x is None else x.strip(_WS)


def _stata_is_rule_line(line: str | None) -> bool:
    """Port of R/stata.R::.stata_is_rule_line(): an ``{hline}``-drawn rule."""
    tl = _trim(line)
    if tl is None:  # nzchar(NA) is TRUE; grepl(NA) is FALSE
        return False
    return tl != "" and _rule_re().search(tl) is not None


@cache
def _rule_re() -> Any:
    from pytacheck._r import compile_r

    return compile_r(_RULE_RE)


def _cols_names(cols: list[list[str]], n_header: int) -> list[str]:
    """Column names from the header rows: joined, trimmed, ``V<i>`` if blank, made unique."""
    nm = [" ".join(cl[:n_header]).strip(_WS) for cl in cols]
    return _make_unique([v if v != "" else f"V{i}" for i, v in enumerate(nm, 1)])


def _string_columns_frame(
    columns: Sequence[Sequence[str | None]], names: list[str]
) -> pd.DataFrame:
    """``as.data.frame(body)`` + ``names<-``: character columns built positionally."""
    import pandas as pd

    df = pd.DataFrame({i: pd.array(c, dtype="string") for i, c in enumerate(columns)})
    df.columns = list(names)
    return df


def _cols_to_frame(cols: list[list[str]], n_header: int) -> pd.DataFrame:
    """The header/body split shared by the fixed-width table readers."""
    return _string_columns_frame([cl[n_header:] for cl in cols], _cols_names(cols, n_header))


def _any_numlike(columns: Sequence[Sequence[str | None]], fn: Any) -> bool:
    """``any(vapply(df, function(c_) any(fn(c_)), logical(1)))`` on the raw columns."""
    return any(any(fn(col)) for col in columns if col)


def _stata_output_tables(lines: Sequence[str]) -> list[dict[str, Any]]:
    """Port of R/stata.R::.stata_output_tables(): ``{hline}``-bounded tables."""
    lines = list(lines)
    n = len(lines)
    i = 1
    tables: list[dict[str, Any]] = []
    while i <= n:
        if _stata_is_rule_line(lines[i - 1]):
            header_start = i - 1
            while (
                header_start >= 1
                and _trim(lines[header_start - 1]) != ""
                and not _stata_is_rule_line(lines[header_start - 1])
            ):
                header_start -= 1
            header_start += 1
            if header_start >= i:
                i += 1
                continue
            header_lines = lines[header_start - 1 : i - 1]
            j = i + 1
            data_lines: list[str] = []
            while j <= n and not _stata_is_rule_line(lines[j - 1]) and _trim(lines[j - 1]) != "":
                data_lines.append(lines[j - 1])
                j += 1
            if j <= n and _stata_is_rule_line(lines[j - 1]):
                j += 1
            if data_lines:
                cols = _stata_split_block(header_lines + data_lines)
                if cols is not None and len(cols) >= 2:
                    n_header = len(header_lines)
                    if _any_numlike([cl[n_header:] for cl in cols], _stata_is_numlike):
                        tables.append({"title": None, "data": _cols_to_frame(cols, n_header)})
            i = j
            continue
        i += 1
    return tables


@cache
def _stat_pattern() -> str:
    """The ``.r_stat_pattern()`` regex (R/stat_helpers.R), shared with R output."""
    try:
        from pytacheck.stats.helpers import _r_stat_pattern

        pp = _r_stat_pattern()
        pattern = pp["pattern"] if isinstance(pp, dict) else getattr(pp, "pattern", None)
        if isinstance(pattern, str):
            return pattern
    except ImportError:  # pragma: no cover - depends on a concurrently ported area
        pass
    op = "=<>~≈≠≤≥≪≫"
    gr = "Ͱ-Ͽ"
    return (
        f"([{gr}²a-zA-Z][{gr}²a-zA-Z0-9._-]*)\\s*"
        r"(\([^)]*\))?\s*"
        f"([{op}]{{1,3}})\\s*"
        r"(<\s*[.0-9]+|-?[0-9]+(?:\.[0-9]+)?(?:e[-+]?[0-9]+)?)"
    )


def _one_row_frame(stat: list[str], val: list[str]) -> pd.DataFrame:
    """``data.frame(as.list(setNames(val, make.unique(stat))), check.names = FALSE)``."""
    import pandas as pd

    df = pd.DataFrame({i: pd.array([v], dtype="string") for i, v in enumerate(val)})
    df.columns = _make_unique(stat)
    return df


def _stata_output_oneline(
    lines: Sequence[str],
    source_label: str | None = None,  # noqa: ARG001 - kept for R's signature
) -> list[dict[str, Any]]:
    """Port of R/stata.R::.stata_output_oneline(): ``name = value`` results per line."""
    pattern = _stat_pattern()
    results = []
    for ln in lines:
        fr = regextract_all(pattern, ln, perl=True)
        if not fr:
            continue
        stat: list[str] = []
        val: list[str] = []
        for f in fr:
            mm = regexec(pattern, f, perl=True)
            if len(mm) >= 5 and trimws(mm[1]) != "":
                stat.append(str(trimws(mm[1])))
                val.append(mm[4])
        if stat:
            results.append({"title": None, "data": _one_row_frame(stat, val)})
    return results


def import_stata_smcl(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Read a Stata Markup and Control Language (.smcl) output log.

    Port of R/stata.R::import_stata_smcl(). Renders the markup to plain text,
    splits it into one chunk per Stata command and extracts each chunk's
    result tables and one-line statistics.

    Args:
        path: path to a ``.smcl`` file.

    Returns:
        A list of result tables, each a dict ``analysis`` (the Stata command),
        ``title`` (``None``), ``data`` (a data frame of character columns),
        ``syntax`` (the command again) and ``table_index``. Empty when the
        file has no recoverable command output.
    """
    path = os.fspath(path)
    if not os.path.exists(path):
        raise FileNotFoundError(f"File not found: {path}")
    if not grepl(r"\.smcl$", path, ignore_case=True):
        raise ValueError(f"Not a .smcl file: {path}")
    rendered = _smcl_render(_read_lines(path))
    chunks = _smcl_command_chunks(rendered)
    out: list[dict[str, Any]] = []
    for ch in chunks:
        if not ch["output"]:
            continue
        blocks = _stata_output_tables(ch["output"]) + _stata_output_oneline(
            ch["output"], ch["command"]
        )
        for b in blocks:
            data = b.get("data")
            if data is None or len(data) == 0 or data.shape[1] == 0:
                continue
            out.append(
                {
                    "analysis": ch["command"],
                    "title": b.get("title"),
                    "data": data,
                    "syntax": ch["command"],
                }
            )
    for i, tb in enumerate(out, 1):
        tb["table_index"] = i
    return out


def export_stata_smcl_html(
    path: str | os.PathLike[str], out: str | os.PathLike[str] | None = None
) -> str:
    """Export a Stata (.smcl) output log as standalone HTML.

    Port of R/stata.R::export_stata_smcl_html(): one heading per Stata command
    and one ``<table>`` per result :func:`import_stata_smcl` recovers.

    Args:
        path: path to a ``.smcl`` file.
        out: path to write the HTML file to; defaults to ``path`` with its
            extension replaced by ``.html``.

    Returns:
        The path written to.
    """
    path = os.fspath(path)
    if not os.path.exists(path):
        raise FileNotFoundError(f"File not found: {path}")
    if not grepl(r"\.smcl$", path, ignore_case=True):
        raise ValueError(f"Not a .smcl file: {path}")
    out = str(sub(r"\.smcl$", ".html", path, ignore_case=True)) if out is None else os.fspath(out)
    tables = import_stata_smcl(path)
    if not tables:
        body = "<p>No result tables could be recovered from this .smcl file.</p>"
    else:
        sections = []
        last_cmd: object = None
        for k, tb in enumerate(tables):
            heading = ""
            if k == 0 or tb["analysis"] != last_cmd:
                heading = f"<h3><code>{_spv_html_escape(tb['analysis'])}</code></h3>"
                last_cmd = tb["analysis"]
            sections.append(heading + _spv_table_html(tb["data"]))
        body = "\n".join(sections)
    _write_lines(_html_page(os.path.basename(path), "h3", body), out)
    return out


def _smcl_export_syntax(
    smcl_path: str | os.PathLike[str], code_dir_name: str = "code"
) -> str | None:
    """Port of R/stata.R::.smcl_export_syntax().

    Writes the echoed Stata commands to ``<dir>/<code_dir_name>/<name>.do``;
    returns that path, or ``None`` when there are no command echoes.
    """
    smcl_path = os.fspath(smcl_path)
    if not os.path.exists(smcl_path):
        raise FileNotFoundError(f"File not found: {smcl_path}")
    try:
        raw_lines = _read_lines(smcl_path)
    except OSError:
        raw_lines = []
    if not raw_lines:
        return None
    chunks = _smcl_command_chunks(_smcl_render(raw_lines))
    commands = [c["command"] for c in chunks if trimws(c["command"]) != ""]
    if not commands:
        return None
    code_dir = _file_path(_r_dirname(smcl_path), code_dir_name)
    os.makedirs(code_dir, exist_ok=True)
    out_path = _file_path(code_dir, _file_path_sans_ext(os.path.basename(smcl_path)) + ".do")
    _write_lines(commands, out_path)
    return out_path
