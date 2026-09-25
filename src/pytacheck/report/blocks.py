"""Report building blocks used by modules (port of ``R/report-helpers.R``).

A module's ``report`` is a list of blocks: markdown strings (Quarto
flavoured, e.g. the ``:::`` callouts made by :func:`collapse_section`) and
:class:`ReportTable` objects made by :func:`scroll_table`. metacheck emits
an R code chunk for each table and relies on Quarto + DT to render it;
pytacheck keeps the table as data and renders it itself (see
:mod:`pytacheck.report.render`), so reports need neither R nor Quarto.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from pytacheck._r.base import plural

__all__ = [
    "ReportTable",
    "cap_gate_count",
    "collapse_section",
    "format_ref",
    "link",
    "plural",
    "report_table",
    "scroll_table",
]


@dataclass
class ReportTable:
    """A table block in a module report (what ``scroll_table()`` produces)."""

    data: pd.DataFrame
    colwidths: Any = "auto"
    maxrows: int = 2
    escape: bool = False
    column: str = "body"
    options: dict[str, Any] = field(default_factory=dict)

    def to_canonical(self) -> dict[str, Any]:
        """Parity encoding: report prose comparison skips table widgets."""
        return {"t": "report_table", "nrow": len(self.data)}

    def column_defs(self) -> list[dict[str, Any]]:
        """DataTables ``columnDefs`` for the widths (``report_table()``)."""
        widths = self.colwidths
        if isinstance(widths, str) and widths == "auto":
            return []
        if not isinstance(widths, list | tuple):
            widths = [widths]
        from pytacheck.report.render import _column_defs

        return _column_defs(widths)

    def __bool__(self) -> bool:
        return len(self.data) > 0 and self.data.shape[1] > 0


def scroll_table(
    table: Any,
    colwidths: Any = "auto",
    maxrows: int = 2,
    escape: bool = False,
    column: str = "body",
) -> ReportTable | str:
    """A scrollable/paginated table block for a report.

    Vectors become a one-column table with an empty header; an empty table
    gives ``""`` (as in R). Line breaks in text cells become ``<br>``.
    """
    if table is None:
        return ""
    if not isinstance(table, pd.DataFrame):
        values = (
            [table] if isinstance(table, str) or not isinstance(table, Iterable) else list(table)
        )
        table = pd.DataFrame({"": values})
    if len(table) == 0 or table.shape[1] == 0:
        return ""
    table = table.copy()
    for i in range(table.shape[1]):
        # every text column, factors included (metacheck's `table[[col]]` loop
        # skipped blank names, i.e. a vector's column, repeated names and
        # factors; U131)
        s = table.iloc[:, i]
        if isinstance(s.dtype, pd.CategoricalDtype):
            cats = s.cat.categories
            if cats.dtype == object or pd.api.types.is_string_dtype(cats.dtype):
                new_cats = [c.replace("\n", "<br>") if isinstance(c, str) else c for c in cats]
                if len(set(new_cats)) == len(new_cats):
                    table.isetitem(i, s.cat.rename_categories(new_cats))
                else:
                    values = [v.replace("\n", "<br>") if isinstance(v, str) else v for v in s]
                    table.isetitem(i, pd.Series(values, index=s.index, dtype="category"))
            continue
        if pd.api.types.is_string_dtype(s.dtype) or s.dtype == object:
            values = [v.replace("\n", "<br>") if isinstance(v, str) else v for v in s.tolist()]
            keep = s.dtype if pd.api.types.is_string_dtype(s.dtype) and s.dtype != object else None
            table.isetitem(i, pd.Series(values, index=s.index, dtype=keep))
    return ReportTable(table, colwidths=colwidths, maxrows=maxrows, escape=escape, column=column)


_CALLOUTS = ("tip", "note", "warning", "important", "caution")


def collapse_section(
    text: str | Sequence[Any] | ReportTable,
    title: str = "Learn More",
    callout: str = "tip",
    collapse: bool = True,
) -> str | list[Any]:
    """A collapsible Quarto callout block around *text* (paragraphs joined).

    When *text* contains table blocks (``collapse_section(scroll_table(x))``
    in R), the callout is returned as a list of blocks — its opening fence,
    the items, and its closing fence — which joined with blank lines is the
    text R produces.
    """
    if callout not in _CALLOUTS:
        raise ValueError(f"'arg' should be one of {', '.join(repr(c) for c in _CALLOUTS)}")
    head = (
        f'::: {{.callout-{callout} title="{title}" collapse="{"true" if collapse else "false"}"}}'
    )
    items = _flat_blocks(text)
    if any(isinstance(t, ReportTable) for t in items):
        return [head, *items, ":::\n"]
    body = "\n\n".join(_as_text(t) for t in items)
    return f"{head}\n\n{body}\n\n:::\n"


def _flat_blocks(x: Any) -> list[Any]:
    if isinstance(x, str | ReportTable) or not isinstance(x, Iterable):
        return [x]
    out: list[Any] = []
    for item in x:
        out.extend(_flat_blocks(item))
    return out


def _as_text(x: Any) -> str:
    from pytacheck._r.base import as_character

    if x is None:
        return "NA"
    if isinstance(x, str):
        return x
    value = as_character(x)
    return "NA" if value is None else str(value)


def link(url: Any, text: Any = None, new_window: bool = True, type: str = "") -> Any:
    """HTML link(s) (``link()``); vectorised over *url*/*text*; ``NA`` urls give ``None``."""
    from pytacheck._r.regex import gsub

    scalar = isinstance(url, str) or url is None
    urls = [url] if scalar else list(url)
    if type == "doi":
        # a missing DOI stays missing (metacheck's sprintf() linked it to
        # "https://doi.org/NA"; U8)
        urls = [
            None if _is_na(u) else "https://doi.org/" + gsub(r"https?://doi.org/", "", u)
            for u in urls
        ]
    # R's default `text = url` is only evaluated now, after the doi rewrite
    texts = urls if text is None else ([text] if isinstance(text, str) else list(text))
    nw = " target='_blank'" if new_window else ""
    out = []
    n = max(len(urls), len(texts))
    for i in range(n):
        u = urls[i % len(urls)]
        t = texts[i % len(texts)]
        if _is_na(u):
            out.append(None)
            continue
        shown = gsub(r"^https?://", "", "NA" if _is_na(t) else str(t))
        out.append(f"<a href='{u}'{nw}>{shown}</a>")
    return out[0] if scalar and len(out) == 1 else out


def format_ref(bib: str | Sequence[str]) -> str | list[str]:
    """Formatted reference(s).

    metacheck formats R ``bibentry`` objects with R's own bibstyle engine.
    pytacheck modules store the HTML R produced for each fixed reference
    (so reports match metacheck exactly) and pass those strings here; the
    result is tidied the same way (newlines/``<p>`` removed, trimmed).
    """

    def tidy(s: str) -> str:
        return s.replace("\n", " ").replace("<p>", " ").replace("</p>", " ").strip()

    if isinstance(bib, str):
        return tidy(bib)
    return [tidy(b) for b in bib]


def _is_na(x: Any) -> bool:
    return x is None or x is pd.NA or (isinstance(x, float) and math.isnan(x))


def _cap_num(x: float | None) -> str:
    """Port of ``.cap_num()``: a number for a cap message (never scientific)."""
    if _is_na(x):
        return "unknown"
    v = float(x)  # type: ignore[arg-type]
    if math.isinf(v):
        return "Inf"
    # isTRUE(all.equal(x, round(x))): mean relative difference <= 1.5e-8
    # (absolute when |x| itself is that small)
    r = round(v)
    diff = abs(v - r)
    if (diff / abs(v) if abs(v) > 1.5e-8 else diff) <= 1.5e-8:
        return str(int(r))
    return f"{v:.1f}"


def cap_gate_count(
    n_needed: int | None,
    param: str,
    current: float,
    unit: str = "item",
    context: str | None = None,
    action: str = "process",
) -> str | None:
    """Explain that *n_needed* items exceed the ``param`` cap, or ``None`` when within it."""
    if (
        n_needed is None
        or (isinstance(n_needed, float) and math.isnan(n_needed))
        or n_needed <= current
    ):
        return None
    where = f" for {context}" if context else ""
    return (
        f"{n_needed:d} {unit}{plural(n_needed)}{where} exceed{'s' if n_needed == 1 else ''} "
        f"the `{param}` cap of {_cap_num(current)}. "
        f"Set `{param} >= {n_needed:d}` to {action} them; {context if context else 'this unit'} was skipped."
    )


def report_table(
    table: Any, colwidths: Any = "auto", maxrows: int = 2, escape: bool = False
) -> Any:
    """Port of ``report_table()``: the table widget of a report (a :class:`~pytacheck.report.render.DataTable`)."""
    from pytacheck.report.render import report_table as _report_table

    return _report_table(table, colwidths=colwidths, maxrows=maxrows, escape=escape)
