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
        defs = []
        for i, w in enumerate(widths):
            if w is None or (isinstance(w, float) and math.isnan(w)):
                continue
            if isinstance(w, int | float):
                w = f"{w}px" if w > 1 else f"{w * 100:g}%"
            defs.append({"targets": i, "width": w})
        return defs

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
    for col in table.columns:
        if pd.api.types.is_string_dtype(table[col]) or table[col].dtype == object:
            table[col] = [
                v.replace("\n", "<br>") if isinstance(v, str) else v for v in table[col].tolist()
            ]
    return ReportTable(table, colwidths=colwidths, maxrows=maxrows, escape=escape, column=column)


_CALLOUTS = ("tip", "note", "warning", "important", "caution")


def collapse_section(
    text: str | Sequence[str],
    title: str = "Learn More",
    callout: str = "tip",
    collapse: bool = True,
) -> str:
    """A collapsible Quarto callout block around *text* (paragraphs joined)."""
    if callout not in _CALLOUTS:
        raise ValueError(f"'arg' should be one of {', '.join(repr(c) for c in _CALLOUTS)}")
    body = text if isinstance(text, str) else "\n\n".join(str(t) for t in text)
    return (
        f'::: {{.callout-{callout} title="{title}" collapse="{"true" if collapse else "false"}"}}'
        f"\n\n{body}\n\n:::\n"
    )


def link(url: Any, text: Any = None, new_window: bool = True, type: str = "") -> Any:
    """HTML link(s) (``link()``); vectorised over *url*/*text*; ``NA`` urls give ``None``."""
    from pytacheck._r.regex import gsub

    scalar = isinstance(url, str) or url is None
    urls = [url] if scalar else list(url)
    texts = urls if text is None else ([text] if isinstance(text, str) else list(text))
    if type == "doi":
        urls = [
            None if u is None else "https://doi.org/" + gsub(r"https?://doi.org/", "", u)
            for u in urls
        ]
    nw = " target='_blank'" if new_window else ""
    out = []
    n = max(len(urls), len(texts))
    for i in range(n):
        u = urls[i % len(urls)]
        t = texts[i % len(texts)]
        if u is None or (isinstance(u, float) and math.isnan(u)):
            out.append(None)
            continue
        shown = gsub(r"^https?://", "", "NA" if t is None else str(t))
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


def _cap_num(x: float | None) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "unknown"
    if isinstance(x, float) and math.isinf(x):
        return "Inf"
    if float(x) == round(float(x)):
        return str(round(float(x)))
    return f"{float(x):.1f}"


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
