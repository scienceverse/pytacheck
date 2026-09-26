"""Module reports: building blocks and rendering."""

from pytacheck.report import report  # the submodule; calling it calls report.report()
from pytacheck.report.blocks import (
    ReportTable,
    cap_gate_count,
    collapse_section,
    format_ref,
    link,
    plural,
    report_table,
    scroll_table,
)
from pytacheck.report.emojis import emojis
from pytacheck.report.report import (
    ReportList,
    ReportOutput,
    module_report,
    report_module_run,
    report_qmd,
    report_repository,
)

__all__ = [
    "ReportList",
    "ReportOutput",
    "ReportTable",
    "cap_gate_count",
    "collapse_section",
    "emojis",
    "format_ref",
    "link",
    "module_report",
    "plural",
    "report",
    "report_module_run",
    "report_qmd",
    "report_repository",
    "report_table",
    "scroll_table",
]


# mypy cannot see the base class through __import__()
class _CallableModule(__import__("types").ModuleType):  # type: ignore[misc]
    """``pytacheck.report`` is also :func:`report`, so ``pytacheck.report(paper)``
    works even once the subpackage has shadowed the top-level ``report`` export.

    Likewise ``pytacheck.report.report`` stays the submodule (so
    ``from pytacheck.report import report; report.report(...)`` works) and
    calling it calls the function."""

    def __call__(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return report.report(*args, **kwargs)


__import__("sys").modules[__name__].__class__ = _CallableModule
