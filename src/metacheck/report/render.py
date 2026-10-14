"""Rendering reports without R or Quarto.

metacheck writes a Quarto document whose tables are R code chunks
(``scroll_table()``) that call ``report_table()`` (a DT widget), and asks
Quarto to render it. pytacheck keeps tables as data (:class:`ReportTable`)
and renders the whole report itself:

* :func:`table_chunk` writes a table into a ``.qmd`` as a raw HTML block (the
  table metacheck's R chunk renders to), so Quarto renders the document
  without R;
* :func:`report_table` is the DT table: a self-contained, paginated HTML
  table honouring ``maxrows``, ``colwidths`` and ``escape``;
* :func:`markdown_to_html` renders the Quarto-flavoured markdown modules
  write (``:::`` callouts as collapsible ``<details>``, fenced divs,
  tabsets, ``{#id .class}`` attributes, smart typography) with markdown-it;
* :func:`markdown_to_gfm` turns the same markdown into plain
  (GitHub-flavoured) Markdown;
* :func:`html_page` assembles the self-contained HTML report page
  (inline CSS/JS) laid out like the Quarto template.
"""

from __future__ import annotations

import html as _html
import math
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from typing import TYPE_CHECKING, Any

from metacheck._r.base import as_character
from metacheck._values import is_missing

if TYPE_CHECKING:
    import pandas as pd

    from metacheck.report.blocks import ReportTable

__all__ = [
    "STATIC_MAX_ROWS",
    "DataTable",
    "html_page",
    "markdown_to_gfm",
    "markdown_to_html",
    "report_table",
    "scroll_table_qmd",
    "simple_page",
    "static_markdown",
    "static_table_chunk",
    "table_chunk",
    "table_gfm",
]

# ---------------------------------------------------------------------------
# tables in the .qmd: raw HTML blocks
# ---------------------------------------------------------------------------


def table_chunk(block: ReportTable, static: bool = False) -> str:
    """A table block as it goes into a ``.qmd``: a raw HTML block (D75).

    metacheck writes an R chunk that calls ``report_table()``; the HTML is the
    table that chunk renders to, so Quarto needs no R to render the document.
    ``static`` writes the plain table of a simple report (every row, no
    pagination).
    """
    return f"\n```{{=html}}\n{_table_html(block, static)}\n```\n"


def static_table_chunk(block: ReportTable) -> str:
    """:func:`table_chunk` for a simple (static) report."""
    return table_chunk(block, static=True)


def scroll_table_qmd(
    table: Any,
    colwidths: Any = "auto",
    maxrows: int = 2,
    escape: bool = False,
    column: str = "body",
) -> str:
    """``scroll_table()``'s return value: the ``.qmd`` text for a table.

    :func:`metacheck.report.scroll_table` returns the table as data (a
    :class:`ReportTable` block); this gives the text pytacheck puts in the
    ``.qmd`` for it, a raw HTML block (:func:`table_chunk`; metacheck writes
    an R chunk), or ``""`` for an empty table.
    """
    from metacheck.report.blocks import ReportTable, scroll_table

    block = scroll_table(table, colwidths=colwidths, maxrows=maxrows, escape=escape, column=column)
    if isinstance(block, ReportTable):
        return table_chunk(block)
    return block


# ---------------------------------------------------------------------------
# report_table(): the DT table, as self-contained HTML
# ---------------------------------------------------------------------------


def _width(x: Any) -> str | None:
    if is_missing(x):
        return None
    if isinstance(x, int | float) and not isinstance(x, bool):
        if x > 1:
            return f"{as_character(float(x))}px"
        return f"{as_character(float(x) * 100)}%"
    return str(x)


def _column_defs(colwidths: Any) -> list[dict[str, Any]]:
    if isinstance(colwidths, str) and colwidths == "auto":
        return []
    widths = [colwidths] if isinstance(colwidths, str | int | float) else list(colwidths)
    defs = []
    for i, w in enumerate(widths):
        width = _width(w)
        if width is not None:
            defs.append({"targets": i, "width": width})
    return defs


def _js_number(f: float) -> str:
    """JavaScript ``Number#toString()`` of a finite double (ECMAScript 7.1.12.1)."""
    if f == 0:
        return "0"
    if f < 0:
        return "-" + _js_number(-f)
    from decimal import Decimal

    # shortest round-trip digits (as JavaScript uses) and the decimal exponent n
    _, digit_tuple, exp = Decimal(repr(f)).normalize().as_tuple()
    digits = "".join(map(str, digit_tuple))
    k = len(digits)
    n = k + int(exp)
    if k <= n <= 21:
        return digits + "0" * (n - k)
    if 0 < n <= 21:
        return digits[:n] + "." + digits[n:]
    if -6 < n <= 0:
        return "0." + "0" * (-n) + digits
    e = n - 1
    sign = "+" if e >= 0 else "-"
    mantissa = digits if k == 1 else digits[0] + "." + digits[1:]
    return f"{mantissa}e{sign}{abs(e)}"


def _cell_text(v: Any) -> str:
    """How DT shows a value (JSON -> JavaScript ``toString``)."""
    import numpy as np

    if is_missing(v):
        return ""
    if isinstance(v, bool | np.bool_):
        return "true" if v else "false"
    if isinstance(v, int | np.integer):
        return str(int(v))
    if isinstance(v, float | np.floating):
        f = float(v)
        if math.isinf(f):
            return ""  # jsonlite writes Inf as null
        # htmlwidgets serialises doubles to 16 significant digits; the browser
        # shows the parsed number with JavaScript's Number#toString
        return _js_number(float(f"{f:.16g}"))
    if isinstance(v, list | tuple | np.ndarray):
        return ",".join(_cell_text(e) for e in v)
    return str(v)


#: Port of ``.report_table_simple_max_rows``: the most rows one table of a simple
#: report shows (it has no pagination to hide rows behind), so that one huge
#: table cannot make the file unboundedly large.
STATIC_MAX_ROWS = 500


@dataclass
class DataTable:
    """What ``report_table()`` returns: a table plus its DataTables options.

    ``data`` has line breaks as ``<br>`` and column names broken at ``_``
    (``_<wbr>``), as in R; ``options`` are the DataTables options R passes
    (``dom``, ``autoWidth``, ``ordering``, ``pageLength``, ``columnDefs``).
    :meth:`to_html` renders it as a self-contained paginated HTML table.
    """

    data: pd.DataFrame
    options: dict[str, Any] = field(default_factory=dict)
    escape: bool = False
    column: str = "body"
    #: a simple report's table: plain HTML, every row, no pagination (and no script)
    static: bool = False

    def to_html(self) -> str:
        """The table as HTML (pagination is done by the report's inline script).

        A static table (``report_type("simple")``) is a plain ``<table
        class="dt-static">`` with every row, in a box that scrolls when the
        table has more than ``pageLength`` rows; past
        :data:`STATIC_MAX_ROWS` rows it is cut, with a note.
        """
        df = self.data
        page_length = self.options.get("pageLength", 2)
        paged = self.options.get("dom") != "t"
        esc = self.escape is True
        widths = {
            d["targets"]: d["width"] for d in self.options.get("columnDefs", []) if "width" in d
        }
        right: set[int] = set()
        for d in self.options.get("columnDefs", []):
            if d.get("className") == "dt-right":
                t = d["targets"]
                right.update(t if isinstance(t, list | tuple) else [t])
        numeric = [i in right for i in range(df.shape[1])]
        cols = []
        for i, name in enumerate(df.columns):
            # R builds the header as gsub("_", "_<wbr>", name) and DT escapes it
            # when escape = TRUE (showing a literal "<wbr>"); escape the name
            # first so the soft break still works.
            label = str(name).replace("_<wbr>", "_")
            label = _html.escape(label, quote=False) if esc else label
            label = label.replace("_", "_<wbr>")
            cls = ' class="dt-right"' if numeric[i] else ""
            cols.append(f"<th{cls}>{label}</th>")
        colgroup = ""
        if widths:
            colgroup = (
                "<colgroup>"
                + "".join(
                    f'<col style="width:{_html.escape(widths[i])}">' if i in widths else "<col>"
                    for i in range(df.shape[1])
                )
                + "</colgroup>"
            )
        if self.static:
            return self._static_html(cols, colgroup, numeric, int(page_length))
        rows = []
        columns = [df.iloc[:, i].tolist() for i in range(df.shape[1])]
        for r in range(len(df)):
            cells = []
            for i, col in enumerate(columns):
                text = _cell_text(col[r])
                if esc:
                    text = _html.escape(text, quote=False)
                cls = ' class="dt-right"' if numeric[i] else ""
                cells.append(f"<td{cls}>{text}</td>")
            parity = "odd" if r % 2 == 0 else "even"
            rows.append(f'<tr class="{parity}">' + "".join(cells) + "</tr>")
        wrapper_cls = "datatables" + (f" column-{self.column}" if self.column != "body" else "")
        attrs = f' data-page-length="{int(page_length)}"' if paged else ""
        pager = (
            '<div class="dt-top"><nav class="dt-paging" aria-label="pages"></nav></div>'
            if paged
            else ""
        )
        return (
            f'<div class="{wrapper_cls}"{attrs}>{pager}<div class="dt-scroll">'
            f'<table class="dataTable display">{colgroup}<thead><tr>{"".join(cols)}</tr></thead>'
            f"<tbody>{''.join(rows)}</tbody></table></div></div>"
        )

    def _static_html(
        self, header: list[str], colgroup: str, numeric: list[bool], page_length: int
    ) -> str:
        """Port of ``.report_table_static()``: the table of a simple report."""
        df = self.data
        n_total = len(df)
        shown = df.iloc[:STATIC_MAX_ROWS] if n_total > STATIC_MAX_ROWS else df
        esc = self.escape is True
        columns = [shown.iloc[:, i].tolist() for i in range(shown.shape[1])]
        rows = []
        for r in range(len(shown)):
            cells = []
            for i, col in enumerate(columns):
                text = _cell_text(col[r])
                if esc:
                    text = _html.escape(text, quote=False)
                cls = ' class="dt-right"' if numeric[i] else ""
                cells.append(f"<td{cls}>{text}</td>")
            rows.append("<tr>" + "".join(cells) + "</tr>")
        table = (
            f'<table class="dt-static">{colgroup}<thead><tr>{"".join(header)}</tr></thead>'
            f"<tbody>{''.join(rows)}</tbody></table>"
        )
        if n_total > page_length:
            # a box about `maxrows` rows high (2.5em a row, plus the header) that scrolls
            height = f"{(page_length + 1) * 2.5:.0f}em"
            table = f'<div class="dt-static-scroll" style="max-height:{height};overflow:auto;">{table}</div>'
        if n_total > STATIC_MAX_ROWS:
            table += f"<p><em>Showing the first {STATIC_MAX_ROWS} of {n_total} rows.</em></p>"
        cls = "dt-static-wrap" + (f" column-{self.column}" if self.column != "body" else "")
        return f'<div class="{cls}">{table}</div>'

    def _repr_html_(self) -> str:
        return f"<style>{_asset('report.css')}</style>{self.to_html()}<script>{_asset('report.js')}</script>"


def _is_numeric(s: pd.Series) -> bool:
    import pandas as pd

    dtype = s.dtype
    return bool(
        (pd.api.types.is_integer_dtype(dtype) or pd.api.types.is_float_dtype(dtype))
        and not pd.api.types.is_bool_dtype(dtype)
    )


def report_table(
    table: pd.DataFrame,
    colwidths: Any = "auto",
    maxrows: int = 2,
    escape: bool = False,
    static: bool | None = None,
) -> DataTable:
    """Port of ``report_table()``: display a table in a report.

    Column widths are pixels for numbers > 1, fractions of the width for
    numbers <= 1, or CSS strings (``"3em"``); ``None``/``NaN`` leaves a
    column automatic. Tables longer than ``maxrows`` are paginated. A static
    table (the default under ``report_type("simple")`` or
    ``"simple_brief"``) shows every row instead, in a box about ``maxrows``
    rows high that scrolls, with no script (see :meth:`DataTable.to_html`).
    """
    import pandas as pd

    from metacheck.report.blocks import _report_is_static, report_type

    if static is None:
        static = _report_is_static(report_type())

    if not isinstance(table, pd.DataFrame):
        raise TypeError("'data' must be 2-dimensional (e.g. data frame or matrix)")
    cd = _column_defs(colwidths)
    data = table.copy()
    seen: set[str] = set()
    for col in range(data.shape[1]):
        # R: `for (col in names(table)) if (is.character(table[[col]]))`
        s = data.iloc[:, col]
        name = str(data.columns[col])
        if name == "" or name in seen or isinstance(s.dtype, pd.CategoricalDtype):
            continue
        seen.add(name)
        if pd.api.types.is_string_dtype(s.dtype) or s.dtype == object:
            values = [v.replace("\n", "<br>") if isinstance(v, str) else v for v in s.tolist()]
            # pandas-stubs omits Series from isetitem's accepted values
            data.isetitem(col, pd.Series(values, index=s.index, dtype=s.dtype))  # type: ignore[arg-type]
    data.columns = [str(c).replace("_", "_<wbr>") for c in data.columns]
    # DT::datatable() adds a right-alignment class for numeric columns and a
    # name for every column to the columnDefs
    numeric = [i for i in range(data.shape[1]) if _is_numeric(data.iloc[:, i])]
    if numeric:
        cd.append({"className": "dt-right", "targets": numeric})
    cd.extend({"name": str(name), "targets": i} for i, name in enumerate(data.columns))
    options = {
        "dom": "<'top' p>" if len(data) > maxrows else "t",
        "autoWidth": True,
        "ordering": False,
        "pageLength": maxrows,
        "columnDefs": cd,
    }
    return DataTable(data, options, escape=escape is True, static=static)


def _table_html(block: ReportTable, static: bool = False) -> str:
    dt = report_table(block.data, block.colwidths, block.maxrows, block.escape, static=static)
    dt.column = block.column
    return dt.to_html()


def table_gfm(block: ReportTable) -> str:
    """A table block as a GitHub-flavoured Markdown pipe table (all rows)."""
    df = block.data
    esc = block.escape is True

    def cell(v: Any) -> str:
        text = _cell_text(v).replace("\r\n", "<br>").replace("\n", "<br>")
        if esc:
            text = _html.escape(text, quote=False)
        return text.replace("|", "\\|")

    names = [cell(c) for c in df.columns]
    numeric = [_is_numeric(df.iloc[:, i]) for i in range(df.shape[1])]
    lines = [
        "| " + " | ".join(names) + " |",
        "| " + " | ".join("--:" if n else "---" for n in numeric) + " |",
    ]
    columns = [df.iloc[:, i].tolist() for i in range(df.shape[1])]
    lines.extend("| " + " | ".join(cell(col[r]) for col in columns) + " |" for r in range(len(df)))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Quarto-flavoured markdown
# ---------------------------------------------------------------------------

_CALLOUT_TITLES = {
    "note": "Note",
    "tip": "Tip",
    "warning": "Warning",
    "important": "Important",
    "caution": "Caution",
}
_FENCE_OPEN = re.compile(r"^ {0,3}(:{3,})\s*(?:\{([^{}]*)\}|([^\s{}:]+))\s*:*\s*$")
_FENCE_CLOSE = re.compile(r"^ {0,3}(:{3,})\s*$")
_CODE_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_HEADING = re.compile(r"^ {0,3}(#{1,6})(\s+.*?)?\s*$")
_ATTR_TOKEN = re.compile(
    r"""#([^\s}#.]+)|\.([^\s}#.=]+)|([\w:-]+)=(?:"([^"]*)"|'([^']*)'|([^\s}]+))"""
)
_TRAILING_ATTRS = re.compile(r"\s*\{([^{}]*)\}\s*$")
_LINK_ATTRS = re.compile(r"(\]\([^()\s]*(?:\s+\"[^\"]*\")?\))\{[^{}]*\}")


@dataclass
class _Attrs:
    id: str | None = None
    classes: list[str] = field(default_factory=list)
    kv: dict[str, str] = field(default_factory=dict)


def _parse_attrs(text: str) -> _Attrs:
    attrs = _Attrs()
    for m in _ATTR_TOKEN.finditer(text):
        if m.group(1):
            attrs.id = m.group(1)
        elif m.group(2):
            attrs.classes.append(m.group(2))
        elif m.group(3):
            value = next((g for g in m.group(4, 5, 6) if g is not None), "")
            attrs.kv[m.group(3)] = value
    return attrs


@dataclass
class _Div:
    attrs: _Attrs
    children: list[Any] = field(default_factory=list)


def _fence_step(line: str, fence: str | None) -> tuple[bool, str | None]:
    """Track fenced code blocks: (is this line code/fence?, the open fence after it)."""
    m = _CODE_FENCE.match(line)
    if fence is None:
        return (True, m.group(1)) if m else (False, None)
    closes = m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence)
    if closes and line.strip() == m.group(1):  # type: ignore[union-attr]
        return True, None
    return True, fence


def _parse_divs(text: str) -> list[Any]:
    """Split markdown into lines and nested fenced divs (``::: {.x}`` ... ``:::``)."""
    root: list[Any] = []
    stack: list[_Div] = []
    fence: str | None = None
    for line in text.split("\n"):
        target = stack[-1].children if stack else root
        is_code, fence = _fence_step(line, fence)
        if is_code:
            target.append(line)
            continue
        if stack and _FENCE_CLOSE.match(line):
            stack.pop()
            continue
        m = _FENCE_OPEN.match(line)
        if m:
            attrs = (
                _parse_attrs(m.group(2)) if m.group(2) is not None else _Attrs(classes=[m.group(3)])
            )
            div = _Div(attrs)
            target.append(div)
            stack.append(div)
            continue
        target.append(line)
    return root


def _is_heading(line: str) -> re.Match[str] | None:
    m = _HEADING.match(line)
    return m if m and line.lstrip().startswith("#") else None


def _callout_type(attrs: _Attrs) -> str | None:
    for c in attrs.classes:
        if c.startswith("callout-") and c[8:] in _CALLOUT_TITLES:
            return c[8:]
    return None


def _unlisted(line: str) -> str:
    """Mark a heading inside a div so it stays out of the table of contents."""
    m = _TRAILING_ATTRS.search(line)
    if m:
        return line[: m.start()] + " {" + m.group(1).strip() + " .unlisted}"
    return line.rstrip() + " {.unlisted}"


def _mark_headings(nodes: list[Any]) -> list[Any]:
    out: list[Any] = []
    fence: str | None = None
    for node in nodes:
        if isinstance(node, _Div):
            out.append(node)
            continue
        is_code, fence = _fence_step(node, fence)
        out.append(node if is_code or not _is_heading(node) else _unlisted(node))
    return out


def _split_tabs(nodes: list[Any]) -> tuple[list[Any], list[tuple[str, list[Any]]]]:
    """Split a tabset's content at its first-level headings: (preamble, [(label, body)])."""
    heading: list[re.Match[str] | None] = []
    fence: str | None = None
    for node in nodes:
        if isinstance(node, _Div):
            heading.append(None)
            continue
        is_code, fence = _fence_step(node, fence)
        heading.append(None if is_code else _is_heading(node))
    levels = [len(h.group(1)) for h in heading if h is not None]
    if not levels:
        return nodes, []
    level = min(levels)
    pre: list[Any] = []
    tabs: list[tuple[str, list[Any]]] = []
    for node, h in zip(nodes, heading, strict=True):
        if h is not None and len(h.group(1)) == level:
            label = _TRAILING_ATTRS.sub("", (h.group(2) or "").strip())
            tabs.append((label, []))
            continue
        (tabs[-1][1] if tabs else pre).append(node)
    return pre, tabs


def _emit_html(nodes: list[Any], inline: Callable[[str], str], static: bool = False) -> list[str]:
    out: list[str] = []
    for node in nodes:
        if not isinstance(node, _Div):
            out.append(node)
            continue
        attrs = node.attrs
        callout = _callout_type(attrs)
        children = _mark_headings(node.children)
        if callout is not None and static:
            # a simple report's callout: always open, its title a bold line
            # (opening and closing one needs a script; R: collapse_section())
            title = attrs.kv.get("title")
            cls = " ".join(["callout", *attrs.classes])
            id_attr = f' id="{_html.escape(attrs.id)}"' if attrs.id else ""
            head = ["", f"**{title}**", ""] if title else []
            out += ["", f'<div{id_attr} class="{_html.escape(cls)}">', *head]
            out += ["", *_emit_html(children, inline, static), "", "</div>", ""]
        elif "panel-tabset" in attrs.classes and static:
            # a simple report's tabs: one heading per tab, one after the other
            pre, tabs = _split_tabs(node.children)
            out += _emit_html(_mark_headings(pre), inline, static)
            for label, body in tabs:
                out += ["", f"##### {label} {{.unlisted}}", ""]
                out += _emit_html(_mark_headings(body), inline, static)
        elif callout is not None:
            title = attrs.kv.get("title") or _CALLOUT_TITLES[callout]
            title_html = inline(title)
            collapse = attrs.kv.get("collapse")
            extra = " ".join(c for c in attrs.classes if not c.startswith("callout-"))
            cls = f"callout callout-{callout}" + (f" {extra}" if extra else "")
            id_attr = f' id="{_html.escape(attrs.id)}"' if attrs.id else ""
            if collapse is not None:
                is_open = " open" if collapse.lower() == "false" else ""
                open_html = (
                    f'<div{id_attr} class="{cls}"><details class="callout-details"{is_open}>'
                    f'<summary class="callout-title">{title_html}</summary><div class="callout-body">'
                )
                close_html = "</div></details></div>"
            else:
                open_html = (
                    f'<div{id_attr} class="{cls}"><div class="callout-title">{title_html}</div>'
                    '<div class="callout-body">'
                )
                close_html = "</div></div>"
            out += ["", open_html, "", *_emit_html(children, inline, static), "", close_html, ""]
        elif "panel-tabset" in attrs.classes:
            pre, tabs = _split_tabs(node.children)
            out += ["", '<div class="panel-tabset">', ""]
            out += _emit_html(_mark_headings(pre), inline, static)
            if tabs:
                buttons = "".join(
                    f'<button type="button" role="tab" class="tab-btn{" active" if i == 0 else ""}">'
                    f"{inline(label)}</button>"
                    for i, (label, _) in enumerate(tabs)
                )
                out += ["", f'<div class="tab-nav" role="tablist">{buttons}</div>', ""]
                for i, (_, body) in enumerate(tabs):
                    active = " active" if i == 0 else ""
                    out += ["", f'<div class="tab-pane{active}" role="tabpanel">', ""]
                    out += _emit_html(_mark_headings(body), inline, static)
                    out += ["", "</div>", ""]
            out += ["", "</div>", ""]
        else:
            id_attr = f' id="{_html.escape(attrs.id)}"' if attrs.id else ""
            cls = f' class="{_html.escape(" ".join(attrs.classes))}"' if attrs.classes else ""
            kv = "".join(
                f' data-{_html.escape(k)}="{_html.escape(v)}"'
                for k, v in attrs.kv.items()
                if re.fullmatch(r"[\w-]+", k)
            )
            out += [
                "",
                f"<div{id_attr}{cls}{kv}>",
                "",
                *_emit_html(children, inline, static),
                "",
                "</div>",
                "",
            ]
    return out


def _attr_text(attrs: _Attrs) -> str:
    """Fenced-div attributes written back as ``{#id .class key="value"}``."""
    parts = [f"#{attrs.id}"] if attrs.id else []
    parts += [f".{c}" for c in attrs.classes]
    parts += [f'{k}="{v}"' for k, v in attrs.kv.items()]
    return "{" + " ".join(parts) + "}"


def _emit_static_md(nodes: list[Any]) -> list[str]:
    out: list[str] = []
    for node in nodes:
        if not isinstance(node, _Div):
            out.append(node)
            continue
        attrs = node.attrs
        callout = _callout_type(attrs)
        if callout is not None:
            title = attrs.kv.get("title")
            plain = _Attrs(attrs.id, attrs.classes, {})
            head = ["", f"**{title}**"] if title else []
            out += ["", f"::: {_attr_text(plain)}", *head, "", *_emit_static_md(node.children)]
            out += ["", ":::", ""]
        elif "panel-tabset" in attrs.classes:
            pre, tabs = _split_tabs(node.children)
            out += _emit_static_md(pre)
            for label, body in tabs:
                out += ["", f"##### {label}", "", *_emit_static_md(body)]
        else:
            out += ["", f"::: {_attr_text(attrs)}", "", *_emit_static_md(node.children)]
            out += ["", ":::", ""]
    return out


def static_markdown(text: str) -> str:
    """Quarto-flavoured markdown as a simple (static) report writes it.

    Callouts lose their ``title``/``collapse`` attributes (opening one needs
    a script), their title becoming a bold first line, and each tabset
    becomes its tabs one after the other under ``#####`` headings: what
    metacheck's ``collapse_section()``, ``file_tabset()`` and
    ``codebook_file_tabset()`` write under ``report_type("simple")``.
    """
    return re.sub(r"\n{3,}", "\n\n", "\n".join(_emit_static_md(_parse_divs(text))))


def _emit_gfm(nodes: list[Any]) -> list[str]:
    out: list[str] = []
    for node in nodes:
        if not isinstance(node, _Div):
            out.append(node)
            continue
        attrs = node.attrs
        callout = _callout_type(attrs)
        if callout is not None:
            title = attrs.kv.get("title") or _CALLOUT_TITLES[callout]
            is_open = " open" if attrs.kv.get("collapse", "true").lower() == "false" else ""
            out += ["", f"<details{is_open}>", f"<summary>{title}</summary>", ""]
            out += _emit_gfm(node.children)
            out += ["", "</details>", ""]
        elif "panel-tabset" in attrs.classes:
            out += ["", *_emit_gfm(node.children), ""]
        else:
            id_attr = f' id="{_html.escape(attrs.id)}"' if attrs.id else ""
            cls = f' class="{_html.escape(" ".join(attrs.classes))}"' if attrs.classes else ""
            out += ["", f"<div{id_attr}{cls}>", "", *_emit_gfm(node.children), "", "</div>", ""]
    return out


def _slugify(text: str) -> str:
    """Pandoc's automatic heading identifier."""
    kept = "".join(c for c in text if c.isalnum() or c in "_-. " or c.isspace())
    kept = "".join("-" if c.isspace() else c.lower() for c in kept.strip())
    i = 0
    while i < len(kept) and not kept[i].isalpha():
        i += 1
    return kept[i:] or "section"


def _inline_text(token: Any) -> str:
    return "".join(
        child.content for child in token.children or [] if child.type in ("text", "code_inline")
    )


def _attrs_rule(state: Any) -> None:
    """Pandoc attributes on headings/links, auto ids, external links, dashes."""
    tokens = state.tokens
    used: dict[str, int] = {}
    for i, tok in enumerate(tokens):
        if tok.type == "inline" and tok.children:
            _inline_attrs(tok.children)
        if tok.type != "heading_open" or i + 1 >= len(tokens):
            continue
        inline = tokens[i + 1]
        children = inline.children or []
        attrs = _Attrs()
        if children and children[-1].type == "text":
            last = children[-1]
            m = _TRAILING_ATTRS.search(last.content)
            if m:
                attrs = _parse_attrs(m.group(1))
                last.content = last.content[: m.start()].rstrip()
                inline.content = _TRAILING_ATTRS.sub("", inline.content)
        ident = attrs.id or _slugify(_inline_text(inline))
        if ident in used and not attrs.id:
            used[ident] += 1
            ident = f"{ident}-{used[ident]}"
        else:
            used.setdefault(ident, 0)
        tok.attrSet("id", ident)
        if attrs.classes:
            tok.attrSet("class", " ".join(attrs.classes))


_SMART = (("---", "\u2014"), ("--", "\u2013"), ("...", "\u2026"))


def _link_attrs(children: list[Any], j: int) -> None:
    """External links open in a new window; ``[text](url){.class}`` sets the link's class."""
    link = children[j]
    href = link.attrGet("href") or ""
    if re.match(r"^https?://", str(href)):
        link.attrSet("target", "_blank")
        link.attrSet("rel", "noopener")
    depth = 0
    for k in range(j, len(children)):
        if children[k].type == "link_open":
            depth += 1
        elif children[k].type == "link_close":
            depth -= 1
            if depth == 0:
                nxt = children[k + 1] if k + 1 < len(children) else None
                m = (
                    re.match(r"^\{([^{}]*)\}", nxt.content)
                    if nxt is not None and nxt.type == "text"
                    else None
                )
                if m is not None and nxt is not None:
                    attrs = _parse_attrs(m.group(1))
                    if attrs.classes:
                        link.attrSet("class", " ".join(attrs.classes))
                    if attrs.id:
                        link.attrSet("id", attrs.id)
                    nxt.content = nxt.content[m.end() :]
                return


def _inline_attrs(children: list[Any]) -> None:
    link_depth = 0
    for j, child in enumerate(children):
        if child.type == "link_open":
            _link_attrs(children, j)
            link_depth += 1
        elif child.type == "link_close":
            link_depth -= 1
        elif child.type == "text" and link_depth == 0:
            # Pandoc's smart typography (quotes are done by markdown-it)
            content = child.content
            for a, b in _SMART:
                content = content.replace(a, b)
            child.content = content


@cache
def _markdown() -> Any:
    from markdown_it import MarkdownIt

    md = MarkdownIt("commonmark", {"html": True, "typographer": True})
    md.enable(["table", "strikethrough", "smartquotes"])
    md.core.ruler.push("pytacheck_attrs", _attrs_rule)
    return md


def _render_inline(text: str) -> str:
    return str(_markdown().renderInline(text))


def markdown_to_html(text: str, static: bool = False) -> str:
    """Render Quarto-flavoured markdown (as metacheck modules write it) to HTML.

    ``static`` renders it for a simple report, which needs no script: callouts
    open, with their title as a bold line, and tabsets as headings.
    """
    nodes = _parse_divs(text)
    md = "\n".join(_emit_html(nodes, _render_inline, static))
    return str(_markdown().render(md))


def markdown_to_gfm(text: str) -> str:
    """Turn Quarto-flavoured markdown into plain (GitHub-flavoured) Markdown.

    Callouts become ``<details>`` blocks, other fenced divs ``<div>``s,
    heading ids ``<a id>`` anchors, and link attributes are dropped.
    """
    lines = _emit_gfm(_parse_divs(text))
    out: list[str] = []
    fence: str | None = None
    for line in lines:
        is_code, fence = _fence_step(line, fence)
        if not is_code:
            if _is_heading(line):
                am = _TRAILING_ATTRS.search(line)
                if am:
                    attrs = _parse_attrs(am.group(1))
                    line = line[: am.start()].rstrip()
                    if attrs.id:
                        out += [f'<a id="{_html.escape(attrs.id)}"></a>', ""]
            line = _LINK_ATTRS.sub(r"\1", line)
        out.append(line)
    text = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text)


# ---------------------------------------------------------------------------
# The report page
# ---------------------------------------------------------------------------


@cache
def _asset(name: str) -> str:
    return (
        resources.files("metacheck.report").joinpath("templates", name).read_text(encoding="utf-8")
    )


_TABLE_MARK = "<!--pytacheck-table-{}-->"
_TABLE_MARK_RE = re.compile(r"<!--pytacheck-table-(\d+)-->")


def render_blocks(
    blocks: Iterable[Any], table: Callable[[ReportTable], str], sep: str = "\n\n"
) -> str:
    """Join report blocks, turning each table block into text with *table*."""
    from metacheck.report.blocks import ReportTable

    parts = []
    for b in blocks:
        if isinstance(b, ReportTable):
            parts.append(table(b))
        else:
            parts.append("NA" if b is None else str(b))
    return sep.join(parts)


class TableSlots:
    """Placeholders for tables while markdown is rendered, then their HTML
    (``static``: a simple report's plain tables)."""

    def __init__(self, static: bool = False) -> None:
        self.tables: list[ReportTable] = []
        self.static = static

    def __call__(self, block: ReportTable) -> str:
        self.tables.append(block)
        return "\n" + _TABLE_MARK.format(len(self.tables) - 1) + "\n"

    def fill(self, html: str) -> str:
        """Replace the placeholders in *html* with the tables (one pass)."""
        if not self.tables:
            return html
        return _TABLE_MARK_RE.sub(
            lambda m: _table_html(self.tables[int(m.group(1))], self.static), html
        )


def _toc(body_html: str) -> str:
    """A nested table of contents from the h2/h3 headings of the report body."""
    heads = re.findall(r"<h([23])([^>]*)>(.*?)</h\1>", body_html, flags=re.S)
    items: list[tuple[int, str, str]] = []
    for level, attrs, inner in heads:
        if "unlisted" in attrs:
            continue
        m = re.search(r'id="([^"]*)"', attrs)
        if not m:
            continue
        items.append((int(level), m.group(1), inner))
    out = ["<ul>"]
    open_sub = False
    for level, ident, inner in items:
        if level == 2:
            if open_sub:
                out.append("</ul></li>")
                open_sub = False
            elif len(out) > 1:
                out.append("</li>")
            out.append(f'<li><a href="#{ident}">{inner}</a>')
        else:
            if not open_sub:
                out.append("<ul>")
                open_sub = True
            out.append(f'<li><a href="#{ident}">{inner}</a></li>')
    if open_sub:
        out.append("</ul></li>")
    elif len(out) > 1:
        out.append("</li>")
    out.append("</ul>")
    return "".join(out)


_SIDEBAR = """  <nav id="TOC" class="toc" role="doc-toc">
    <ul class="emoji-key">
      <li class="red">⚠️ possible problems detected;</li>
      <li class="yellow">🔍 something to check;</li>
      <li class="green">✅️ no problems detected;</li>
      <li class="info">ℹ️ informational only;</li>
      <li class="na">⬜ not applicable;</li>
      <li class="fail">☠️ check failed</li>
    </ul>
    <hr>
    <p class="toc-title"><strong>Table of Contents</strong></p>
{toc}
  </nav>"""


def html_page(
    *,
    title: str,
    subtitle: str,
    intro_md: str,
    body_md: str,
    tables: TableSlots,
    generator: str,
    toc: bool = True,
) -> str:
    """The self-contained HTML report page (layout of ``inst/templates/_report.qmd``).

    ``toc=False`` leaves out the table of contents and the legend beside it (a
    brief report has its legend at the top, and only one section).
    """
    intro_html = tables.fill(markdown_to_html(intro_md))
    body_html = tables.fill(markdown_to_html(body_md))
    page_title = f"{title} \u2013 {subtitle}" if subtitle else title
    template = _asset("report.html")
    values = {
        "lang": "en",
        "generator": _html.escape(generator),
        "page_title": _html.escape(page_title),
        "title": _html.escape(title),
        "subtitle": _html.escape(subtitle),
        "css": _asset("report.css"),
        "js": _asset("report.js"),
        "intro": intro_html,
        "body": body_html,
        "sidebar": _SIDEBAR.replace("{toc}", _toc(body_html)) if toc else "",
        "layout_class": "" if toc else " no-toc",
    }
    return re.sub(r"\{\{(\w+)\}\}", lambda m: values.get(m.group(1), m.group(0)), template)


def simple_page(*, title: str, css: str, body_md: str, tables: TableSlots, generator: str) -> str:
    """The HTML page of a simple report: static, no script (``_report_simple.Rmd``).

    *body_md* is the whole document after the template's ``<style>`` block
    (banner, introduction, summary and module sections). A ``<!-- TOC -->``
    line in it becomes the table of contents of the headings after it.
    """
    body_html = tables.fill(markdown_to_html(body_md, static=True))
    if "<!-- TOC -->" in body_html:
        before, after = body_html.split("<!-- TOC -->", 1)
        body_html = f'{before}<div id="TOC" class="toc">{_toc(after)}</div>{after}'
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<meta name="generator" content="{_html.escape(generator)}">\n'
        f"<title>{_html.escape(title)}</title>\n<style>\n{css}\n</style>\n</head>\n"
        f"<body>\n{body_html}\n</body>\n</html>\n"
    )
