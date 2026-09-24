"""Rendering reports without R or Quarto.

metacheck writes a Quarto document whose tables are R code chunks
(``scroll_table()``) that call ``report_table()`` (a DT widget), and asks
Quarto to render it. pytacheck keeps tables as data (:class:`ReportTable`)
and renders the whole report itself:

* :func:`deparse` / :func:`table_chunk` reproduce the R chunk
  ``scroll_table()`` writes, so a ``.qmd`` report is the same document
  metacheck writes (and renders identically with Quarto + R);
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
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from itertools import pairwise
from typing import TYPE_CHECKING, Any

from pytacheck._r.base import as_character

if TYPE_CHECKING:
    import pandas as pd

    from pytacheck.report.blocks import ReportTable

__all__ = [
    "DataTable",
    "deparse",
    "html_page",
    "markdown_to_gfm",
    "markdown_to_html",
    "report_table",
    "scroll_table_qmd",
    "table_chunk",
    "table_gfm",
]

# ---------------------------------------------------------------------------
# R deparse() (for the table data in scroll_table()'s R chunk)
# ---------------------------------------------------------------------------

_RESERVED = frozenset(
    {
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
        "NA_complex_",
        "in",
    }
)
_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\n": "\\n",
    "\t": "\\t",
    "\r": "\\r",
    "\a": "\\a",
    "\b": "\\b",
    "\f": "\\f",
    "\v": "\\v",
}
_NEEDS_ESCAPE = re.compile(r'[\\"\x00-\x1f\x7f-\x9f]')
#: Assigned in Unicode 15.1 (R 4.5's character tables) but not in Unicode 15.0
#: (Python 3.12's ``unicodedata``): R prints them as they are.
_UNICODE_15_1 = ((0x2EBF0, 0x2EE5D), (0x2FFC, 0x2FFF), (0x31EF, 0x31EF))


def _r_printable(ch: str) -> bool:
    """R's ``iswprint()`` for a non-ASCII character (UTF-8 locale).

    R escapes control characters, line/paragraph separators and code points
    unassigned in its Unicode tables (15.1); format characters, private-use
    characters and everything else assigned print as they are.
    """
    cat = unicodedata.category(ch)
    if cat in ("Cc", "Zl", "Zp", "Cs"):
        return False
    if cat == "Cn":
        code = ord(ch)
        return any(lo <= code <= hi for lo, hi in _UNICODE_15_1)
    return True


def _encode_string(s: str) -> str:
    """R ``EncodeString(s, quote = '"')`` in a UTF-8 locale."""
    if _NEEDS_ESCAPE.search(s) is None and (s.isascii() or all(map(_r_printable, s))):
        return f'"{s}"'
    out = []
    for ch in s:
        esc = _ESCAPES.get(ch)
        if esc is not None:
            out.append(esc)
            continue
        code = ord(ch)
        if code < 0x20 or code == 0x7F:
            out.append(f"\\{code:03o}")
        elif code < 0x80 or _r_printable(ch):
            out.append(ch)
        elif code > 0xFFFF:
            out.append(f"\\U{{{code:06x}}}")
        else:
            out.append(f"\\u{code:04x}")
    return '"' + "".join(out) + '"'


def _is_valid_name(name: str) -> bool:
    """R ``isValidName()`` (a syntactic name that needs no quoting)."""
    if not name:
        return False
    if name == "...":
        return True
    first = name[0]
    if first != "." and not first.isalpha():
        return False
    if first == "." and len(name) > 1 and name[1] in "0123456789":
        return False
    if any(not (c.isalnum() or c in "._") for c in name):
        return False
    return name not in _RESERVED


def _is_missing(x: Any) -> bool:
    if x is None:
        return True
    try:
        import pandas as pd

        if x is pd.NA or x is pd.NaT:
            return True
    except ImportError:  # pragma: no cover
        pass
    return isinstance(x, float) and math.isnan(x)


class _Deparser:
    """R's deparse buffer (``src/main/deparse.c``): 60-byte cutoff, 4-space tabs."""

    def __init__(self, cutoff: int = 60) -> None:
        self.cutoff = cutoff
        self.lines: list[str] = []
        self.buf: list[str] = []
        self.len = 0
        self.indent = 0
        self.startline = True

    def put(self, s: str) -> None:
        if self.startline:
            self.startline = False
            for i in range(1, self.indent + 1):
                tab = "    " if i <= 4 else "  "
                self.buf.append(tab)
                self.len += len(tab)
        self.buf.append(s)
        self.len += len(s.encode("utf-8"))

    def writeline(self) -> None:
        self.lines.append("".join(self.buf))
        self.buf = []
        self.len = 0
        self.startline = True

    def linebreak(self, state: list[bool]) -> None:
        if self.len > self.cutoff:
            if not state[0]:
                state[0] = True
                self.indent += 1
            self.writeline()

    # -- values ------------------------------------------------------------------

    def value(self, x: Any) -> None:
        import numpy as np
        import pandas as pd

        if isinstance(x, _Vec):
            self.vector_or_list(x.kind, x.values)
        elif isinstance(x, _Factor):
            self.factor(x)
        elif x is None:
            self.put("NULL")
        elif isinstance(x, pd.DataFrame):
            self.frame(x)
        elif isinstance(x, pd.Series):
            self.value(_column_value(x))
        elif isinstance(x, Mapping):
            self.put("list(")
            self.elements([(str(k), v) for k, v in x.items()], do_names=True)
            self.put(")")
        elif isinstance(x, list | tuple | np.ndarray):
            items = list(x)
            kind = _infer_kind(items)
            self.vector_or_list(kind, items)
        else:
            kind = _infer_kind([x])
            self.vector_or_list(kind, [x])

    def vector_or_list(self, kind: str, values: list[Any]) -> None:
        if kind == "list":
            self.put("list(")
            self.elements([(None, v) for v in values], do_names=False)
            self.put(")")
        else:
            self.vector(kind, values)

    def elements(self, items: list[tuple[str | None, Any]], do_names: bool) -> None:
        """``vec2buff()``: list elements, breaking lines between them."""
        state = [False]
        for i, (name, v) in enumerate(items):
            if i > 0:
                self.put(", ")
            self.linebreak(state)
            if do_names and name:
                self.put(name if _is_valid_name(name) else _encode_string(name))
                self.put(" = ")
            self.value(v)
        if state[0]:
            self.indent -= 1

    def vector(self, kind: str, values: list[Any]) -> None:
        """``vector2buff()``: an atomic vector (``c(...)``, ``a:b``, typed NAs)."""
        n = len(values)
        if n == 0:
            self.put(
                {"chr": "character(0)", "int": "integer(0)", "dbl": "numeric(0)"}.get(
                    kind, "logical(0)"
                )
            )
            return
        missing = [_is_missing(v) for v in values]
        if kind == "int" and n > 1 and not any(missing):
            ints = [int(v) for v in values]
            step = ints[1] - ints[0]
            if abs(step) == 1 and all(b - a == step for a, b in pairwise(ints)):
                self.put(f"{ints[0]}:{ints[-1]}")
                return
        all_na = all(missing)
        if n > 1:
            self.put("c(")
        for i, v in enumerate(values):
            self.put(_encode_element(kind, v, missing[i], all_na))
            if i < n - 1:
                self.put(", ")
            if n > 1 and self.len > self.cutoff:
                self.writeline()
        if n > 1:
            self.put(")")

    def factor(self, f: _Factor) -> None:
        """A factor: ``structure(<codes>, levels = <levels>, class = "factor")``."""
        self.put("structure(")
        self.vector("int", f.codes)
        self.put(", levels = ")
        self.vector("chr", f.levels)
        self.put(', class = c("ordered", "factor"))' if f.ordered else ', class = "factor")')

    def frame(self, df: pd.DataFrame, row_names_first: bool = False) -> None:
        names = [str(c) for c in df.columns]
        all_blank = bool(names) and all(n == "" for n in names)
        self.put("structure(list(")
        cols = [(name, _column_value(df.iloc[:, i])) for i, name in enumerate(names)]
        self.elements(cols, do_names=not all_blank)
        self.put(")")
        if all_blank:
            self.put(", names = ")
            self.vector("chr", names)
        if row_names_first:
            self.put(", row.names = ")
            self._row_names(len(df))
            self.put(', class = "data.frame")')
        else:
            self.put(', class = "data.frame", row.names = ')
            self._row_names(len(df))
            self.put(")")

    def _row_names(self, nrow: int) -> None:
        if nrow == 0:
            self.put("integer(0)")
        else:
            self.vector("int", [None, -nrow])


@dataclass
class _Vec:
    kind: str
    values: list[Any]


@dataclass
class _Factor:
    """An R factor: 1-based codes (``None`` for NA), levels, ``ordered``."""

    codes: list[int | None]
    levels: list[str]
    ordered: bool = False


def _column_value(s: pd.Series) -> _Vec | _Factor:
    """A pandas column as the R vector it stands for (categoricals are factors)."""
    import pandas as pd

    if isinstance(s.dtype, pd.CategoricalDtype):
        codes = [None if c < 0 else int(c) + 1 for c in s.cat.codes.tolist()]
        levels = [str(v) for v in s.cat.categories.tolist()]
        return _Factor(codes, levels, bool(s.cat.ordered))
    kind, values = _series_vector(s)
    return _Vec(kind, values)


def _encode_element(kind: str, v: Any, missing: bool, all_na: bool) -> str:
    if kind == "lgl":
        return "NA" if missing else ("TRUE" if bool(v) else "FALSE")
    if kind == "int":
        if missing:
            return "NA_integer_" if all_na else "NA"
        return f"{int(v)}L"
    if kind == "dbl":
        if missing:
            return "NA_real_" if all_na else "NA"
        f = float(v)
        if math.isinf(f):
            return "Inf" if f > 0 else "-Inf"
        return str(as_character(f))
    if missing:
        return "NA_character_" if all_na else "NA"
    return _encode_string(str(v))


def _infer_kind(values: Sequence[Any]) -> str:
    """The R vector type a list of Python scalars becomes (``c(...)``)."""
    import numpy as np

    kinds = set()
    for v in values:
        if _is_missing(v):
            continue
        if isinstance(v, bool | np.bool_):
            kinds.add("lgl")
        elif isinstance(v, int | np.integer):
            kinds.add("int")
        elif isinstance(v, float | np.floating):
            kinds.add("dbl")
        elif isinstance(v, str):
            kinds.add("chr")
        else:
            return "list"
    if not kinds:
        return "lgl"
    if kinds == {"lgl"}:
        return "lgl"
    if kinds <= {"int", "lgl"}:
        return "int"
    if kinds <= {"int", "dbl", "lgl"}:
        return "dbl"
    return "chr"


def _series_vector(s: pd.Series) -> tuple[str, list[Any]]:
    """An R vector (type, values) for a pandas column."""
    import pandas as pd

    dtype = s.dtype
    if isinstance(dtype, pd.CategoricalDtype):
        return "chr", [None if _is_missing(v) else str(v) for v in s.tolist()]
    if pd.api.types.is_bool_dtype(dtype):
        return "lgl", s.tolist()
    if pd.api.types.is_integer_dtype(dtype):
        return "int", s.tolist()
    if pd.api.types.is_float_dtype(dtype):
        return "dbl", s.tolist()
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return "chr", [None if _is_missing(v) else str(v) for v in s.tolist()]
    values = s.tolist()
    if pd.api.types.is_string_dtype(dtype) and not pd.api.types.is_object_dtype(dtype):
        return "chr", values
    kind = _infer_kind(values)
    if kind == "list":
        return "list", values
    return kind, values


def _is_character(s: pd.Series) -> bool:
    """R ``is.character()`` of a column (factors are not character)."""
    import pandas as pd

    if isinstance(s.dtype, pd.CategoricalDtype):
        return False
    return _series_vector(s)[0] == "chr"


def _deparse_table(df: pd.DataFrame) -> list[str]:
    """``deparse(table)`` inside ``scroll_table()``.

    Replacing line breaks in a character column (``table[[col]] <- gsub(...)``)
    makes R re-set the data frame's class after its row names, so tables with
    a named character column deparse with ``row.names`` before ``class``.
    """
    has_chr = any(
        str(name) != "" and _is_character(df.iloc[:, i]) for i, name in enumerate(df.columns)
    )
    p = _Deparser(60)
    p.frame(df, row_names_first=has_chr)
    p.writeline()
    return p.lines


def deparse(expr: Any, width_cutoff: int = 60) -> list[str]:
    """R ``deparse(x)`` for data: data frames, vectors, lists and scalars.

    Returns the lines R would return. Data frames are written as R's
    ``data.frame()`` builds them (``names``, ``class``, automatic
    ``row.names``): pandas has no row names or attribute order to carry, so
    tables R subset with base R (keeping row names) or built with dplyr
    (``tbl_df``) deparse slightly differently in R.
    """
    p = _Deparser(width_cutoff)
    p.value(expr)
    p.writeline()
    return p.lines


# ---------------------------------------------------------------------------
# scroll_table()'s R chunk
# ---------------------------------------------------------------------------


def _colwidths_value(colwidths: Any) -> Any:
    """``colwidths`` as the R value a module author writes (numbers are doubles)."""
    if isinstance(colwidths, str) or colwidths is None:
        return colwidths
    if isinstance(colwidths, int | float) and not isinstance(colwidths, bool):
        return _Vec("dbl", [float(colwidths)])
    values = list(colwidths)
    if any(isinstance(v, str) for v in values):
        return _Vec(
            "chr",
            [
                None if _is_missing(v) else (v if isinstance(v, str) else as_character(v))
                for v in values
            ],
        )
    if all(_is_missing(v) for v in values):
        return _Vec("lgl", [None] * len(values))
    return _Vec("dbl", [None if _is_missing(v) else float(v) for v in values])


def table_chunk(block: ReportTable) -> str:
    """The R chunk ``scroll_table()`` returns for a table block."""
    column_loc = "" if block.column == "body" else f"#| column: {block.column}"
    tbl_code = "\n".join(_deparse_table(block.data))
    colwidths_code = "\n".join(deparse(_colwidths_value(block.colwidths)))
    maxrows = block.maxrows
    # sprintf("%s", maxrows): module authors write R doubles (1e5 prints "1e+05")
    maxrows_txt = str(
        as_character(float(maxrows))
        if isinstance(maxrows, int) and not isinstance(maxrows, bool)
        else as_character(maxrows)
    )
    escape = "TRUE" if block.escape is True else "FALSE"
    return (
        f"\n```{{r}}\n#| echo: false\n{column_loc}\n\n"
        f"# table data --------------------------------------\ntable <- {tbl_code}\n\n"
        f"# display table -----------------------------------\n"
        f"metacheck::report_table(table, {colwidths_code}, {maxrows_txt}, {escape})\n```\n"
    )


def scroll_table_qmd(
    table: Any,
    colwidths: Any = "auto",
    maxrows: int = 2,
    escape: bool = False,
    column: str = "body",
) -> str:
    """Port of ``scroll_table()``'s return value: the markdown R chunk for a table.

    :func:`pytacheck.report.scroll_table` returns the table as data (a
    :class:`ReportTable` block); this gives the text metacheck puts in the
    ``.qmd`` for it (``""`` for an empty table).
    """
    from pytacheck.report.blocks import ReportTable, scroll_table

    block = scroll_table(table, colwidths=colwidths, maxrows=maxrows, escape=escape, column=column)
    if isinstance(block, ReportTable):
        return table_chunk(block)
    return block


# ---------------------------------------------------------------------------
# report_table(): the DT table, as self-contained HTML
# ---------------------------------------------------------------------------


def _width(x: Any) -> str | None:
    if _is_missing(x):
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

    if _is_missing(v):
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

    def to_html(self) -> str:
        """The table as HTML (pagination is done by the report's inline script)."""
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
    table: pd.DataFrame, colwidths: Any = "auto", maxrows: int = 2, escape: bool = False
) -> DataTable:
    """Port of ``report_table()``: display a table in a report.

    Column widths are pixels for numbers > 1, fractions of the width for
    numbers <= 1, or CSS strings (``"3em"``); ``None``/``NaN`` leaves a
    column automatic. Tables longer than ``maxrows`` are paginated.
    """
    import pandas as pd

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
            data.isetitem(col, pd.Series(values, index=s.index, dtype=s.dtype))
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
    return DataTable(data, options, escape=escape is True)


def _table_html(block: ReportTable) -> str:
    dt = report_table(block.data, block.colwidths, block.maxrows, block.escape)
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


def _emit_html(nodes: list[Any], inline: Callable[[str], str]) -> list[str]:
    out: list[str] = []
    for node in nodes:
        if not isinstance(node, _Div):
            out.append(node)
            continue
        attrs = node.attrs
        callout = _callout_type(attrs)
        children = _mark_headings(node.children)
        if callout is not None:
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
            out += ["", open_html, "", *_emit_html(children, inline), "", close_html, ""]
        elif "panel-tabset" in attrs.classes:
            pre, tabs = _split_tabs(node.children)
            out += ["", '<div class="panel-tabset">', ""]
            out += _emit_html(_mark_headings(pre), inline)
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
                    out += _emit_html(_mark_headings(body), inline)
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
                *_emit_html(children, inline),
                "",
                "</div>",
                "",
            ]
    return out


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


def markdown_to_html(text: str) -> str:
    """Render Quarto-flavoured markdown (as metacheck modules write it) to HTML."""
    nodes = _parse_divs(text)
    md = "\n".join(_emit_html(nodes, _render_inline))
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
        resources.files("pytacheck.report").joinpath("templates", name).read_text(encoding="utf-8")
    )


_TABLE_MARK = "<!--pytacheck-table-{}-->"
_TABLE_MARK_RE = re.compile(r"<!--pytacheck-table-(\d+)-->")


def render_blocks(
    blocks: Iterable[Any], table: Callable[[ReportTable], str], sep: str = "\n\n"
) -> str:
    """Join report blocks, turning each table block into text with *table*."""
    from pytacheck.report.blocks import ReportTable

    parts = []
    for b in blocks:
        if isinstance(b, ReportTable):
            parts.append(table(b))
        else:
            parts.append("NA" if b is None else str(b))
    return sep.join(parts)


class TableSlots:
    """Placeholders for tables while markdown is rendered, then their HTML."""

    def __init__(self) -> None:
        self.tables: list[ReportTable] = []

    def __call__(self, block: ReportTable) -> str:
        self.tables.append(block)
        return "\n" + _TABLE_MARK.format(len(self.tables) - 1) + "\n"

    def fill(self, html: str) -> str:
        """Replace the placeholders in *html* with the tables (one pass)."""
        if not self.tables:
            return html
        return _TABLE_MARK_RE.sub(lambda m: _table_html(self.tables[int(m.group(1))]), html)


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


def html_page(
    *,
    title: str,
    subtitle: str,
    intro_md: str,
    body_md: str,
    tables: TableSlots,
    generator: str,
) -> str:
    """The self-contained HTML report page (layout of ``inst/templates/_report.qmd``)."""
    intro_html = tables.fill(markdown_to_html(intro_md))
    body_html = tables.fill(markdown_to_html(body_md))
    toc = _toc(body_html)
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
        "toc": toc,
    }
    return re.sub(r"\{\{(\w+)\}\}", lambda m: values.get(m.group(1), m.group(0)), template)
