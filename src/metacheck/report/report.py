"""Reports: run modules on a paper and write the results (port of ``R/report.R``).

metacheck builds a Quarto document from ``inst/templates/_report.qmd`` and
renders it with Quarto (and R, for the table chunks). pytacheck produces the
same document (:func:`report_qmd`) and renders it itself, so reports need
neither R nor Quarto:

* ``output_format="html"``: a self-contained HTML page (inline CSS/JS) laid
  out like the Quarto report (``renderer="quarto"`` renders the ``.qmd``
  with Quarto instead, when it is installed);
* ``output_format="qmd"``: the Quarto document metacheck writes, with each
  table as the R chunk ``scroll_table()`` writes;
* ``output_format="md"``: plain (GitHub-flavoured) Markdown.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import shutil
import subprocess
import sys
import tempfile
import warnings
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import cache
from importlib import resources
from pathlib import Path
from types import ModuleType
from typing import Any, cast

from metacheck._r.regex import gsub, regextract_all, sub
from metacheck.module import SECTION_LEVELS, ModuleOutput, module_find, module_info, module_run
from metacheck.papers.model import Paper, PaperList, is_paper_list
from metacheck.report.blocks import ReportTable, collapse_section
from metacheck.report.emojis import emojis
from metacheck.report.render import (
    TableSlots,
    html_page,
    markdown_to_gfm,
    render_blocks,
    table_chunk,
    table_gfm,
)

__all__ = [
    "DEFAULT_MODULES",
    "ReportList",
    "ReportOutput",
    "module_report",
    "render_module_outputs",
    "report",
    "report_module_run",
    "report_qmd",
    "report_repository",
]

#: The modules ``report()`` runs by default, in metacheck's order.
DEFAULT_MODULES: tuple[str, ...] = (
    "prereg_check",
    "funding_check",
    "coi_check",
    "power",
    "repo_check",
    "code_check",
    "stat_check",
    "stat_p_exact",
    "stat_p_nonsig",
    "stat_effect_size",
    "marginal",
    "ref_accuracy",
    "ref_replication",
    "ref_retraction",
    "ref_pubpeer",
    "ref_summary",
)

#: Every module metacheck ships (``inst/modules``). A module named here that
#: pytacheck has not ported yet is skipped with a warning instead of stopping
#: the whole report.
_UPSTREAM_MODULES = frozenset(
    {
        "all_p_values",
        "all_urls",
        "causal_claims",
        "code_check",
        "codebook_check",
        "coi_check",
        "coi_check_oi",
        "data_check",
        "ethics_check",
        "funding_check",
        "funding_check_oi",
        "marginal",
        "open_practices",
        "power",
        "prereg_check",
        "psychds_check",
        "ref_accuracy",
        "ref_consistency",
        "ref_miscitation",
        "ref_pubpeer",
        "ref_replication",
        "ref_retraction",
        "ref_summary",
        "reg_check",
        "repo_check",
        "reproducibility_check",
        "stat_check",
        "stat_effect_size",
        "stat_p_exact",
        "stat_p_nonsig",
    }
)

_OUTPUT_FORMATS = ("html", "qmd", "md")
_BAD_TRAFFIC_LIGHTS = ("na", "fail")


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------


class ReportOutput(dict[str, ModuleOutput]):
    """Module outputs of a report, by module (``report_module_run()``'s list).

    ``paper`` is the paper the modules ran on (R keeps it as an attribute)
    and ``save_path`` where :func:`report` wrote the report. ``str()`` and
    ``os.fspath()`` give the saved path.
    """

    def __init__(
        self,
        items: Iterable[tuple[str, ModuleOutput]] | Mapping[str, ModuleOutput] = (),
        paper: Any = None,
        save_path: str | None = None,
    ) -> None:
        super().__init__(items)
        self.paper = paper
        self.save_path = save_path

    def __str__(self) -> str:
        return self.save_path if self.save_path is not None else self.__repr__()

    def __fspath__(self) -> str:
        if self.save_path is None:
            raise TypeError("this report has not been saved")
        return self.save_path

    def __repr__(self) -> str:
        lines = [f"{m.title}: {m.summary_text}" for m in self.values()]
        where = f"\nsaved to {self.save_path}" if self.save_path else ""
        return "\n".join(lines) + where


class ReportList(dict[str, "ReportOutput | None"]):
    """Reports for a paper list, by paper ID (``None`` where a report failed).

    ``save_path`` maps paper IDs to the saved files; ``str()`` lists them.
    """

    @property
    def save_path(self) -> dict[str, str | None]:
        return {k: (v.save_path if v is not None else None) for k, v in self.items()}

    def __str__(self) -> str:
        return "\n".join(p for p in self.save_path.values() if p)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _flatten(report: Any) -> list[Any]:
    """A module's ``report`` as a flat list of blocks (R ``c()`` of a character vector)."""
    if report is None:
        return []
    if isinstance(report, str | ReportTable):
        return [report]
    if isinstance(report, Iterable):
        out: list[Any] = []
        for item in report:
            out.extend(_flatten(item) if not isinstance(item, str | ReportTable) else [item])
        return out
    return [str(report)]


def _block_text(block: Any) -> str:
    """A block as it appears in the ``.qmd`` (tables as R chunks)."""
    if isinstance(block, ReportTable):
        return table_chunk(block)
    return "NA" if block is None else str(block)


def _is_int_like(x: Any) -> bool:
    if isinstance(x, bool):
        return True
    if isinstance(x, int | float):
        return float(x).is_integer()
    return False


def _as_text(x: Any) -> str:
    """R ``paste()`` of a scalar (numbers as R prints them)."""
    from metacheck._r.base import as_character

    if isinstance(x, str):
        return x
    if isinstance(x, bool):
        return "TRUE" if x else "FALSE"
    value = as_character(x)
    return "NA" if value is None else str(value)


def _all_equal(summary: list[Any], blocks: list[Any], summary_is_null: bool) -> bool:
    """R ``all(module_output$summary_text == report)`` (vectors recycled).

    ``NULL == report`` is ``logical(0)``, and ``all()`` of that is ``TRUE``.
    """
    if summary_is_null or not summary or not blocks:
        return True
    a = [_block_text(b) for b in summary]
    b = [_block_text(x) for x in blocks]
    n = max(len(a), len(b))
    return all(a[i % len(a)] == b[i % len(b)] for i in range(n))


def _header_level(header: Any) -> int | None:
    """``header`` as a heading level (R compares numbers and number strings alike)."""
    if isinstance(header, str):
        return int(header) if header in {"0", "1", "2", "3", "4", "5", "6"} else None
    if _is_int_like(header):
        return int(header)
    return None


def _strip_email(author: str) -> str:
    """An author line without its emails: roxygen's ``Name (\\email{email})`` or ``Name <email>``.

    metacheck's greedy ``gsub("\\s*\\(.*email\\{.+\\})", "", author)`` removed
    everything from the first email on, so a line naming two authors
    (``stat_p_exact``) kept only the first name (U6); each email is removed
    on its own here.
    """
    a = gsub(r"\s*\([^()]*email\{[^{}]*\}\)", "", author)
    return re.sub(r"\s*<[^<>]*@[^<>]*>", "", a)


def _label(module: Any) -> str:
    """The name a module's output is filed under (R: the ``modules`` entry itself)."""
    if isinstance(module, str | os.PathLike):
        return os.fspath(module)
    spec = getattr(module, "__pytacheck_module__", module)
    return str(getattr(spec, "name", module))


def _module_output_list(module_output: Any) -> list[ModuleOutput]:
    if isinstance(module_output, ModuleOutput):
        return [module_output]
    if isinstance(module_output, Mapping):
        return list(module_output.values())
    return list(module_output)


# ---------------------------------------------------------------------------
# module_report()
# ---------------------------------------------------------------------------


def _how_it_works(module: Any) -> tuple[str | None, list[str]]:
    """The "How It Works" callout and validation blocks for a module."""
    validation: list[str] = []
    try:
        info = module_info(module)
        details = info.details or None
        # metacheck's gregexpr() on NULL details errors inside its tryCatch(),
        # which dropped the whole callout, description and authors too (U129)
        found = [] if details is None else regextract_all(r"<validation>.*?</validation>", details)
        if found:
            validation = [
                sub(
                    r"\s*</validation>",
                    "\n:::",
                    sub(r"<validation>\s*", "::: {.validation}\nValidation: ", v),
                )
                for v in found
            ]
        author_ack = None
        if info.author:
            a = [_strip_email(x) for x in info.author]
            if len(a) < 3:
                authors = " and ".join(a)
            else:
                authors = ", ".join(a[:-1]) + " and " + a[-1]
            author_ack = f"This module was developed by {authors}"
        if details is not None:
            details = gsub(r"\s*<validation>.*</validation>\s*", "", details)
        paragraphs = [p for p in (info.description or None, details, author_ack) if p is not None]
        if not paragraphs:
            return None, validation
        return cast(str, collapse_section(paragraphs, "How It Works", callout="note")), validation
    except Exception:
        return None, validation


def _module_report_blocks(module_output: ModuleOutput, header: Any = 3) -> list[Any]:
    tl = module_output.traffic_light if module_output.traffic_light is not None else "info"
    tl_symbol = emojis.get(f"tl_{tl}")
    title = module_output.title
    level = _header_level(header)
    head: list[str]
    # an undefined traffic light has no emoji: metacheck's sprintf() with NULL
    # dropped the whole heading (U128); here it has no symbol
    symbol = "" if tl_symbol is None else f"{tl_symbol} "
    if header is None:
        head = [""]
    elif level == 0:
        head = [f"{symbol}{title}"]
    elif level is not None and 1 <= level <= 6:
        anchor = str(title).lower().replace(" ", "-")
        head = [f"{'#' * level} {symbol}{title} {{#{anchor} .{tl}}}"]
    else:
        head = [_as_text(header)]

    summary_text = module_output.summary_text
    # without a summary text, metacheck showed "..." and dropped the report
    # (`all(NULL == report)` is TRUE; U129): here the report is the summary
    summary = _flatten(summary_text) if summary_text is not None else []
    report = module_output.report if module_output.report is not None else summary_text
    blocks: list[Any] | None = _flatten(report)
    if blocks is not None and all(isinstance(b, str) and b == "" for b in blocks):
        blocks = None
    if summary_text is None and blocks is None:
        summary = ["..."]

    hiw, validation = _how_it_works(module_output.module)

    pre: str | None = "<details><summary>View detailed feedback</summary><div>"
    post: str | None = "</div></details>"
    if summary_text is None and blocks is not None:
        pre = post = None
    elif blocks is None or _all_equal(summary, blocks, False):
        pre = post = None
        blocks = None
    elif len("\n\n".join(_block_text(b) for b in blocks)) < 300:
        pre = post = None

    out: list[Any] = [*head, *summary]
    if pre is not None:
        out.append(pre)
    out.extend(blocks or [])
    if post is not None:
        out.append(post)
    if hiw is not None:
        out.append(hiw)
    out.extend(validation)
    return out


def module_report(module_output: ModuleOutput, header: Any = 3) -> str:
    """Port of ``module_report()``: the report text for one module's output.

    ``header`` is the heading level (1-6), ``0`` for an unmarked title,
    ``None`` for no heading, or a string to use as the heading. Tables are
    written as the R chunks ``scroll_table()`` produces, as in metacheck.
    """
    return render_blocks(_module_report_blocks(module_output, header), table_chunk)


# ---------------------------------------------------------------------------
# report_module_run()
# ---------------------------------------------------------------------------


def _paper_summary_table(paper: Any) -> Any:
    import pandas as pd

    pid = getattr(paper, "paper_id", None) if isinstance(paper, Paper) else None
    if pid is None:
        return pd.DataFrame()
    return pd.DataFrame({"paper_id": pd.Series([pid], dtype="string")})


def report_module_run(
    paper: Any, modules: str | Sequence[Any], args: Mapping[str, Mapping[str, Any]] | None = None
) -> ReportOutput:
    """Port of ``report_module_run()``: run modules in order and sort them by section.

    Each module runs on the previous one's output (so modules can use
    :func:`~metacheck.module.get_prev_outputs`). A module that errors is
    recorded as a ``"fail"`` output, with a warning, and the chain goes on.
    ``args`` maps module names to extra arguments for them.
    """
    from metacheck.utils import pb

    modules = [modules] if isinstance(modules, str | os.PathLike) else list(modules)
    if not modules:
        # R fails here too (its progress bar, or `module_output[[op$module]]`)
        raise ValueError("No modules to run: `modules` is empty")
    args = args or {}
    bar = pb(len(modules), ":what [:bar] :current/:total :elapsedfull")
    try:
        bar.tick(0, tokens={"what": "Running modules"})
        op: Any = paper
        for module in modules:
            label = _label(module)
            bar.tick(0, tokens={"what": label})
            mod_args = dict(args.get(label) or {})
            mod_args.pop("paper", None)
            mod_args.pop("module", None)
            try:
                op = module_run(op, module, **mod_args)
            except Exception as exc:
                warnings.warn(f"Error in {label}", stacklevel=2)
                prev: dict[str, ModuleOutput] = {}
                if isinstance(op, ModuleOutput):
                    prev = dict(op.prev_outputs or {})
                    prev[op.module] = replace(op, prev_outputs={}, paper=None)
                summary_table = (
                    op.summary_table
                    if isinstance(op, ModuleOutput) and op.summary_table is not None
                    else _paper_summary_table(paper)
                )
                op = ModuleOutput(
                    module=label,
                    title=label,
                    section=None,  # type: ignore[arg-type]  # R's failed output has no section
                    table=None,
                    report=str(exc),
                    traffic_light="fail",
                    summary_text="This module failed to run",
                    summary_table=summary_table,
                    paper=paper,
                    prev_outputs=prev,
                )
            bar.tick(tokens={"what": label})
    finally:
        bar.terminate()

    if not isinstance(op, ModuleOutput):
        return ReportOutput(paper=op)
    outputs = dict(op.prev_outputs or {})
    outputs[op.module] = replace(op, prev_outputs={}, paper=None)

    def rank(mo: ModuleOutput) -> int:
        # factor(sections, section_levels): unknown/missing sections sort last
        return (
            SECTION_LEVELS.index(mo.section)
            if mo.section in SECTION_LEVELS
            else len(SECTION_LEVELS)
        )

    ordered = sorted(outputs.items(), key=lambda kv: rank(kv[1]))
    return ReportOutput(ordered, paper=op.paper)


# ---------------------------------------------------------------------------
# report_qmd()
# ---------------------------------------------------------------------------


@cache
def _template_lines() -> tuple[str, ...]:
    text = (
        resources.files("metacheck.report")
        .joinpath("templates", "_report.qmd")
        .read_text(encoding="utf-8")
    )
    lines = re.split(r"\r\n|\r|\n", text)
    if lines and lines[-1] == "":
        lines.pop()
    return tuple(lines)


def _report_version() -> str:
    from metacheck._version import UPSTREAM, __version__

    return f"{UPSTREAM['version']} (pytacheck {__version__})"


def _info_values(paper: Any, column: str) -> list[Any] | None:
    info = getattr(paper, "info", None) if paper is not None else None
    if info is None or not hasattr(info, "columns") or column not in info.columns:
        return None
    return cast("list[Any]", info[column].tolist())


def _is_na(x: Any) -> bool:
    import pandas as pd

    try:
        return x is None or bool(pd.isna(x))
    except (TypeError, ValueError):
        return False


@dataclass
class _ReportParts:
    head: str  # the formatted template head ("" when R's sprintf() gives character(0))
    subtitle: str
    version: str
    date: str
    doi_text: str
    summary: str
    body: list[Any]  # section titles and module report blocks (tables as blocks)

    def body_text(self, table: Callable[[ReportTable], str]) -> str:
        return re.sub(r"\n{3,}", "\n\n", render_blocks(self.body, table))

    def text(self, table: Callable[[ReportTable], str]) -> str:
        return "\n\n".join([self.head, self.summary, self.body_text(table), "\n"])


def _summary_line(mo: ModuleOutput) -> str:
    tl = mo.traffic_light if mo.traffic_light is not None else "info"
    emoji = emojis.get(f"tl_{tl}")
    summary_text = mo.summary_text if mo.summary_text is not None else ""
    if not isinstance(summary_text, str):
        summary_text = " ".join("NA" if s is None else str(s) for s in _flatten(summary_text))
    if summary_text and summary_text[0] == "\n":
        summary_text = summary_text.replace("\n", "\n    ")
    # an undefined traffic light has no emoji (metacheck's sprintf() with NULL
    # printed the line as "character(0)"; U128)
    symbol = "" if emoji is None else f"{emoji} "
    anchor = gsub(r"\s", "-", str(mo.title).lower())
    return f"- {symbol}[{mo.title}](#{anchor}){{.{tl}}}: {summary_text}  "


def _report_parts(module_output: Any, paper: Any = None) -> _ReportParts:
    # metacheck's default `paper = list()` always failed (`nrow(paper$author) > 0`),
    # and a paper whose info lacks a title or DOI lost the report header
    # (`ifelse(logical(0), ...)`); here they are just left out (U128)
    outputs = _module_output_list(module_output)
    lines = _template_lines()
    cut_after = lines.index("<!-- Demo -->")
    rt_head = "\n".join(lines[:cut_after])
    # turn real % into %%, leaving %s, %d, %f, %i for sprintf
    rt_head = re.sub(r"%(?![sdfi])", "%%", rt_head)

    titles = _info_values(paper, "title")
    subtitle_raw = ("NA" if _is_na(titles[0]) else str(titles[0])) if titles else ""
    subtitle = subtitle_raw.replace('"', '\\"')
    dois = _info_values(paper, "doi")
    version = _report_version()
    date = _dt.date.today().isoformat()
    doi = dois[0] if dois else None
    doi_text = "" if _is_na(doi) or doi == "" else f"DOI: [{doi}](https://doi.org/{doi})"
    head = rt_head % (subtitle, version, date, doi_text)

    summary_list = [_summary_line(mo) for mo in outputs]
    summary = "## Summary\n\n{}\n\n".format("\n".join(summary_list))

    body: list[Any] = []
    for sec in SECTION_LEVELS:
        section_op = [
            mo
            for mo in outputs
            if mo.section == sec and mo.traffic_light not in _BAD_TRAFFIC_LIGHTS
        ]
        if not section_op:
            continue
        body.append(f"## {sec[:1].upper()}{sec[1:]} Modules")
        for mo in section_op:
            body.extend(_module_report_blocks(mo))
    return _ReportParts(
        head=head,
        subtitle=subtitle_raw,
        version=version,
        date=date,
        doi_text=doi_text,
        summary=summary,
        body=body,
    )


def _html_table_chunk(block: ReportTable) -> str:
    from metacheck.report.render import _table_html

    return f"\n```{{=html}}\n{_table_html(block)}\n```\n"


def report_qmd(module_output: Any, paper: Any = None, tables: str = "r") -> str:
    """Port of ``report_qmd()``: the Quarto report for module output.

    ``tables="r"`` (default) writes each table as the R chunk metacheck
    writes (rendering then needs R and metacheck); ``tables="html"`` writes
    them as raw HTML blocks, so Quarto alone can render the document.
    """
    if tables not in ("r", "html"):
        raise ValueError("'tables' should be one of 'r', 'html'")
    parts = _report_parts(module_output, paper)
    if tables == "r":
        return parts.text(table_chunk)
    text = parts.text(_html_table_chunk)
    from metacheck.report.render import _asset

    return (
        text
        + "\n```{=html}\n<style>\n"
        + _asset("report.css").split("/* tables (DataTables look) */", 1)[-1]
        + "\n</style>\n<script>\n"
        + _asset("report.js")
        + "\n</script>\n```\n"
    )


def _intro_markdown(parts: _ReportParts) -> str:
    """The template's introduction (after its ``<style>``), with shortcodes filled in."""
    lines = _template_lines()
    cut_after = lines.index("<!-- Demo -->")
    head = "\n".join(lines[:cut_after])
    body = head.split("</style>", 1)[-1]
    body = body.replace("{{< meta metacheck.version >}}", parts.version)
    body = body.replace("{{< meta metacheck.report-created >}}", parts.date)
    return body.replace("%s <!-- doi, if exists -->", parts.doi_text)


def report_html(module_output: Any, paper: Any = None) -> str:
    """The report as a self-contained HTML page (no R or Quarto needed)."""
    from metacheck._version import __version__

    parts = _report_parts(module_output, paper)
    slots = TableSlots()
    body = "\n\n".join([parts.summary, parts.body_text(slots)])
    intro = _intro_markdown(parts) if parts.head else ""
    return html_page(
        title="MetaCheck Report",
        subtitle=parts.subtitle,
        intro_md=intro,
        body_md=body,
        tables=slots,
        generator=f"pytacheck {__version__}",
    )


def report_markdown(module_output: Any, paper: Any = None) -> str:
    """The report as plain (GitHub-flavoured) Markdown."""
    parts = _report_parts(module_output, paper)
    intro = _intro_markdown(parts) if parts.head else ""
    title = "# MetaCheck Report\n\n"
    if parts.subtitle:
        title += f"**{parts.subtitle}**\n\n"
    text = "\n\n".join([title + intro, parts.summary, parts.body_text(table_gfm)])
    return markdown_to_gfm(text).strip() + "\n"


def render_module_outputs(module_output: Any, paper: Any, output_format: str = "html") -> str:
    """The report text for module outputs that have already been computed.

    The REST API's ``/paper/check`` uses this (metacheck's plumber
    ``render_report_html()``: ``report_qmd()`` rendered to one self-contained
    page) so the report reuses the results instead of re-running modules.
    ``module_output`` is a :class:`ReportOutput`, a mapping or a list of
    :class:`~metacheck.module.ModuleOutput`; ``output_format`` is ``"html"``,
    ``"qmd"`` or ``"md"``.
    """
    fmt = output_format.lower()
    if fmt == "html":
        return report_html(module_output, paper)
    if fmt == "qmd":
        return report_qmd(module_output, paper)
    if fmt == "md":
        return report_markdown(module_output, paper)
    raise ValueError("The output_format must be either 'html', 'qmd' or 'md'.")


# ---------------------------------------------------------------------------
# report()
# ---------------------------------------------------------------------------


def _check_modules(modules: Sequence[Any]) -> list[Any]:
    """R: ``sapply(modules, module_find)``; unported metacheck modules are skipped."""
    from metacheck.log import logger
    from metacheck.module import ModuleError

    kept = []
    for m in modules:
        try:
            module_find(m)
        except ModuleError:
            if isinstance(m, str) and m in _UPSTREAM_MODULES:
                logger("report", {"module": m, "error": "module not available in pytacheck"})
                warnings.warn(
                    f"The module '{m}' is not available in pytacheck yet and was skipped",
                    stacklevel=3,
                )
                continue
            raise
        kept.append(m)
    return kept


def _write(text: str, path: str) -> None:
    # R write() adds a final newline
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text + "\n")


def _render_quarto(qmd_text: str, output_file: str) -> None:
    quarto = shutil.which("quarto")
    if quarto is None:
        raise RuntimeError("Quarto is not installed (https://quarto.org)")
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "report.qmd"
        src.write_text(qmd_text + "\n", encoding="utf-8")
        res = subprocess.run(  # noqa: S603 - quarto from PATH, our own temp file
            [quarto, "render", str(src), "--to", "html", "--quiet"],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            raise RuntimeError((res.stderr or res.stdout or "quarto render failed").strip())
        shutil.move(str(Path(tmp) / "report.html"), output_file)


def report(
    paper: Any,
    modules: str | Sequence[Any] = DEFAULT_MODULES,
    output_file: str | os.PathLike[str] | Sequence[str | os.PathLike[str]] | None = None,
    output_format: str | Sequence[str] = "html",
    args: Mapping[str, Mapping[str, Any]] | None = None,
    renderer: str = "pytacheck",
) -> ReportOutput | ReportList:
    """Port of ``report()``: run modules on a paper and write a report.

    Parameters
    ----------
    paper:
        A paper, or a paper list (one report per paper).
    modules:
        Modules to run (built-in names or paths), in order.
    output_file:
        Where to save the report; defaults to ``<paper_id>_report.<format>``.
        For a paper list, one path per paper, or one path whose file name is
        prefixed with each paper ID and ``_`` (``report.html`` gives
        ``<paper_id>_report.html``).
    output_format:
        ``"html"`` (default), ``"qmd"`` or ``"md"``.
    args:
        Extra module arguments by module name, e.g.
        ``{"power": {"seed": 8675309}}``.
    renderer:
        For HTML: ``"pytacheck"`` (built in, the default) or ``"quarto"``
        (render the ``.qmd`` with an installed Quarto).

    Returns
    -------
    The module outputs (:class:`ReportOutput`), with the report's path in
    ``save_path``; for a paper list, a :class:`ReportList`. If rendering
    fails, the ``.qmd`` is saved instead, with a warning.
    """
    from metacheck.log import logger
    from metacheck.utils import pb

    fmt = str(output_format if isinstance(output_format, str) else output_format[0]).lower()
    if fmt not in _OUTPUT_FORMATS:
        raise ValueError("The output_format must be either 'html', 'qmd' or 'md'.")
    if renderer not in ("pytacheck", "quarto"):
        raise ValueError("The renderer must be either 'pytacheck' or 'quarto'.")
    module_list = [modules] if isinstance(modules, str | os.PathLike) else list(modules)
    module_list = _check_modules(module_list)

    if isinstance(paper, list | tuple) and paper and all(isinstance(p, Paper) for p in paper):
        paper = PaperList(paper)
    if is_paper_list(paper) and len(paper) == 1:
        paper = paper[0]
    elif is_paper_list(paper):
        papers = list(paper)
        bar = pb(len(papers), ":what [:bar] :current/:total :elapsedfull")
        bar.tick(0, tokens={"what": "Creating reports"})
        if output_file is None:
            files: list[Any] = [f"_report.{fmt}"]
        elif isinstance(output_file, str | os.PathLike):
            files = [output_file]
        else:
            files = list(output_file)
        if len(files) != len(papers):
            first = os.fspath(files[0])
            base = os.path.basename(first)
            folder = os.path.dirname(first) or "."
            # "<id>_report.html" from the default "_report.html"; a given name
            # gets a separator (metacheck pasted "<id>report.html"; U130)
            sep = "" if base[:1] in ("_", "-", ".") else "_"
            files = [os.path.join(folder, f"{p.paper_id}{sep}{base}") for p in papers]
        out = ReportList()
        for p, of in zip(papers, files, strict=True):
            try:
                r: ReportOutput | None = report(p, module_list, of, fmt, args, renderer)  # type: ignore[assignment]
            except Exception as exc:
                logger("report", {"paper": p.paper_id, "error": str(exc)})
                warnings.warn(f"Error in {p.paper_id}:\n{exc}", stacklevel=2)
                r = None
            bar.tick(tokens={"what": str(p.paper_id)})
            out[str(p.paper_id)] = r
        return out

    if output_file is None:
        if not isinstance(paper, Paper):
            # R fails evaluating the default (paste0(paper$paper_id, ...)) here
            raise TypeError(
                "The paper argument must be a paper object (e.g., created with `read()`)"
            )
        output_file = f"{paper.paper_id}_report.{fmt}"
    if not isinstance(output_file, str | os.PathLike):
        output_file = next(iter(output_file))
    output_file = os.fspath(output_file)

    # check the output_file is writable before running the modules
    try:
        with open(output_file, "w", encoding="utf-8") as fh:
            fh.write("test\n")
    except OSError as exc:
        raise ValueError("The output_file is not a valid path.") from exc

    if not isinstance(paper, Paper):
        raise TypeError("The paper argument must be a paper object (e.g., created with `read()`)")

    module_output = report_module_run(paper, module_list, args)

    if fmt == "qmd":
        _write(report_qmd(module_output, paper), output_file)
        save_path = output_file
    elif fmt == "md":
        _write(report_markdown(module_output, paper).rstrip("\n"), output_file)
        save_path = output_file
    else:
        try:
            if renderer == "quarto":
                _render_quarto(report_qmd(module_output, paper, tables="html"), output_file)
            else:
                _write(report_html(module_output, paper), output_file)
            save_path = output_file
        except Exception as exc:
            # save the qmd on render error and return its path
            output_qmd = re.sub(r"\.html$", "", output_file) + ".qmd"
            _write(report_qmd(module_output, paper), output_qmd)
            logger(
                "quarto render",
                {"paper": paper.paper_id, "quarto": output_qmd, "error": str(exc)},
            )
            warnings.warn(
                f"There was an error rendering your report:\n{exc}"
                f"\n\nSee the following for the quarto file:\n{output_qmd}",
                stacklevel=2,
            )
            save_path = output_qmd

    module_output.save_path = save_path
    return module_output


def report_repository(
    path: str | os.PathLike[str],
    output_file: str | os.PathLike[str] | None = None,
    output_format: str | Sequence[str] = "html",
    modules: Sequence[str] = ("repo_check", "code_check", "data_check", "codebook_check"),
    args: Mapping[str, Mapping[str, Any]] | None = None,
    renderer: str = "pytacheck",
) -> ReportOutput | ReportList:
    """Port of ``report_repository()``: a report for a local repository folder.

    The first module is told where the files are (``local_path``,
    ``local_only=True``); the rest reuse its results. The paper is an empty
    stand-in named after the folder.
    """
    from metacheck.papers.io import test_paper

    fmt = str(output_format if isinstance(output_format, str) else output_format[0]).lower()
    if not isinstance(path, str | os.PathLike) or _is_na(path):
        raise ValueError("`path` must be a single path to a repository folder")
    if not os.path.isdir(path):
        raise FileNotFoundError(
            f"No folder found at {os.fspath(path)}.\nCheck the path, or download the "
            "repository first with osf_file_download()."
        )
    full_path = Path(path).resolve().as_posix()
    if output_file is None:
        output_file = f"{os.path.basename(full_path)}_report.{fmt}"
    modules = [modules] if isinstance(modules, str | os.PathLike) else list(modules)
    new_args: dict[str, dict[str, Any]] = {k: dict(v) for k, v in (args or {}).items()}
    first = str(modules[0])
    new_args[first] = {**new_args.get(first, {}), "local_path": full_path, "local_only": True}

    paper = test_paper()
    import pandas as pd

    info = paper.info.copy()
    info["title"] = pd.Series([os.path.basename(full_path)], dtype="string")
    paper.info = info
    return report(
        paper=paper,
        modules=modules,
        output_file=output_file,
        output_format=fmt,
        args=new_args,
        renderer=renderer,
    )


class _CallableReportModule(ModuleType):
    """This submodule, callable as :func:`report` (``metacheck.report.report(paper)``)."""

    def __call__(self, *args: Any, **kwargs: Any) -> ReportOutput | ReportList:
        return report(*args, **kwargs)


sys.modules[__name__].__class__ = _CallableReportModule
