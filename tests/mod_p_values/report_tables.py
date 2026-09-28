"""Python twin of ``tests/mod_p_values/report_tables.R`` (report-table parity cases).

The report prose comparison skips table widgets, so the data of a module's
report tables is otherwise never compared with R. This returns every
:class:`~pytacheck.report.blocks.ReportTable` in a module's report with its
``report_table()`` arguments, in report order.
"""

from __future__ import annotations

from typing import Any


def _blocks(x: Any) -> list[Any]:
    if isinstance(x, list | tuple):
        return [b for el in x for b in _blocks(el)]
    return [x]


def mp_report_tables(paper: Any, module: str) -> list[dict[str, Any]]:
    from pytacheck.module import module_run
    from pytacheck.report.blocks import ReportTable

    mo = module_run(paper, module)
    return [
        {
            "table": b.data.reset_index(drop=True),
            "colwidths": list(b.colwidths)
            if isinstance(b.colwidths, list | tuple)
            else b.colwidths,
            "maxrows": b.maxrows,
            "escape": b.escape,
        }
        for b in _blocks(mo["report"])
        if isinstance(b, ReportTable)
    ]


def identity(x: Any) -> Any:
    """R ``identity()``: the case value is built by the ``$expr`` argument."""
    return x
