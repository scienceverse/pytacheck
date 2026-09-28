"""Python side of the reproducibility_check review parity cases.

The regular cases (``parity/cases/mod_repro.yaml``) compare the report as
prose, which skips every table widget; :func:`rv_tables` collects the data
frame of each of the report's :class:`~pytacheck.report.blocks.ReportTable`
blocks so they can be compared with R's table chunks
(``tests/mod_repro/rv_helpers.R``).
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from tests.mod_repro import helpers


def rv_tables(mo: Any) -> list[pd.DataFrame]:
    """The data frames of the report's table blocks, in order ("Time (s)" zeroed)."""
    from pytacheck.report.blocks import ReportTable

    out: list[pd.DataFrame] = []

    def walk(x: Any) -> None:
        if isinstance(x, ReportTable):
            d = x.data.copy()
            if "Time (s)" in d.columns:
                d["Time (s)"] = 0.0
            out.append(d)
        elif isinstance(x, list | tuple):
            for v in x:
                walk(v)

    report = mo.report if hasattr(mo, "report") else mo.get("report")
    walk(report)
    return out


def _unroot_tables(mo: Any) -> Any:
    """Replace the kept sandbox's path by ``"<root>"`` in the report's table cells."""
    from pytacheck.report.blocks import ReportTable

    root = mo.get("sandbox")
    if root is None:
        return mo

    def fix(x: Any) -> Any:
        if isinstance(x, ReportTable):
            d = x.data.copy()
            for c in d.columns:
                if d[c].dtype == object or pd.api.types.is_string_dtype(d[c].dtype):
                    d[c] = [v.replace(root, "<root>") if isinstance(v, str) else v for v in d[c]]
            return ReportTable(d, x.colwidths, x.maxrows, x.escape, x.column, x.options)
        if isinstance(x, list):
            return [fix(v) for v in x]
        return x

    mo.report = fix(mo.report)
    return mo


def rv_run(name: str, **kwargs: Any) -> list[pd.DataFrame]:
    """The report tables of ``rc_run(name, ...)`` (R: ``rv_run()``)."""
    orig = helpers.rc_scrub
    try:
        helpers.rc_scrub = lambda mo: orig(_unroot_tables(mo))  # type: ignore[assignment]
        return rv_tables(helpers.rc_run(name, **kwargs))
    finally:
        helpers.rc_scrub = orig  # type: ignore[assignment]


def rv_run_mocked(name: str, **kwargs: Any) -> list[pd.DataFrame]:
    """The report tables of ``rc_run_mocked(name, ...)`` (R: ``rv_run_mocked()``)."""
    orig = helpers.rc_scrub
    try:
        helpers.rc_scrub = lambda mo: orig(_unroot_tables(mo))  # type: ignore[assignment]
        return rv_tables(helpers.rc_run_mocked(name, **kwargs))
    finally:
        helpers.rc_scrub = orig  # type: ignore[assignment]


def rv_run_saved(name: str, **kwargs: Any) -> Any:
    """The module with a committed metacheck ``.rds`` as *tables_dir* (R: ``rv_run_saved()``)."""
    import contextlib

    from pytacheck.module import module_run

    with contextlib.chdir(helpers.ROOT):
        paper = helpers.rc_paper(helpers.specs()["scenarios"][name])
        mo = module_run(
            paper,
            "reproducibility_check",
            tables_dir="tests/mod_repro/fixtures/review_tables",
            **kwargs,
        )
        return helpers.rc_scrub(mo)


def rv_chain(**kwargs: Any) -> Any:
    """A real piped chain on the local fixture project (R: ``rv_chain()``)."""
    import contextlib

    import pytacheck as pc
    from pytacheck.module import module_run

    proj = "tests/mod_repro/fixtures/project"
    with contextlib.chdir(helpers.ROOT):
        p = pc.test_paper("x")
        p.paper_id = "p1"
        x = module_run(p, "data_check", local_path=proj, local_only=True)
        x = module_run(x, "psychds_check", local_path=proj, local_only=True)
        x = module_run(x, "code_check", local_path=proj, local_only=True)
        return helpers.rc_scrub(module_run(x, "reproducibility_check", **kwargs))
