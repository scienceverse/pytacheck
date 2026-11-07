"""The checklist report (port of ``R/report-checklist.R``): ``report_type="checklist"``.

Every check of every module is sorted by its result -- to fix, to improve,
decide by hand, for information, fine, not applicable -- instead of being
shown module by module, and each check that needs work says what was found and
what to do on separate lines. The page is static (no script), light only, in
the colours and font of metacheck's website
(``templates/_report_checklist.Rmd``). metacheck renders it with Pandoc;
pytacheck renders it itself, so the page needs neither R, Quarto nor Pandoc.

The one function to call on finished module outputs is
:func:`checklist_html`::

    from metacheck.report.checklist import checklist_html
    html = checklist_html(module_outputs, paper, title="My report")

A module gives its checks in a ``checks`` table in its output, one row per
check (see :func:`~metacheck.report.blocks.report_type` for the columns). A
module without one is shown as a single check: its traffic light gives the
result, its summary text what was found and its report the folded details.
"""

from __future__ import annotations

import base64
import datetime as _dt
import html as _html
import os
import re
import warnings
from collections.abc import Callable, Mapping, Sequence
from functools import cache
from importlib import resources
from typing import TYPE_CHECKING, Any

import pandas as pd

from metacheck.module import CHECK_COLUMNS, CHECK_STATUSES

if TYPE_CHECKING:
    from metacheck.report.blocks import ReportTable

__all__ = [
    "CHECKLIST_RESULTS",
    "checklist_checks",
    "checklist_html",
    "checklist_markdown",
    "checklist_qmd",
]

#: The results, in the order the report shows them: ``(status, section heading, the word
#: on a check's tag, the label of its count)``.
CHECKLIST_RESULTS: tuple[tuple[str, str, str, str], ...] = (
    ("fix", "To fix", "To fix", "to fix"),
    ("improve", "To improve", "To improve", "to improve"),
    ("decide", "Decide by hand", "Decide by hand", "to decide by hand"),
    ("info", "For information", "Info", "for information"),
    ("fine", "Fine", "Fine", "fine"),
    ("na", "Not applicable", "N/A", "not applicable"),
)
_RESULT = {r[0]: r for r in CHECKLIST_RESULTS}
_WORK = ("fix", "improve", "decide")

#: The result a module without a ``checks`` table gets, from its traffic light.
_LIGHT_STATUS = {
    "red": "fix",
    "yellow": "improve",
    "green": "fine",
    "info": "info",
    "na": "na",
    "fail": "fix",
}

TableRenderer = Callable[["ReportTable"], str]


# ---------------------------------------------------------------------------
# reading module outputs
# ---------------------------------------------------------------------------


def _is_na(x: Any) -> bool:
    if x is None or x is pd.NA:
        return True
    try:
        return bool(x != x)
    except (TypeError, ValueError):
        return False


def _outputs(module_output: Any) -> list[Any]:
    """The module outputs of a report: a mapping, a list, or one output."""
    if module_output is None:
        return []
    if (
        isinstance(module_output, Mapping)
        and "module" in module_output
        and "title" in module_output
    ):
        return [module_output]  # one output given as a dict
    if isinstance(module_output, Mapping):
        return list(module_output.values())
    if isinstance(module_output, str | bytes) or not hasattr(module_output, "__iter__"):
        return [module_output]
    return list(module_output)


def _field(mo: Any, name: str) -> Any:
    """An element of a module output (an object or a dict), ``None`` when it has none."""
    if isinstance(mo, Mapping):
        return mo.get(name)
    getter = getattr(mo, "get", None)
    if callable(getter):
        return getter(name)
    return getattr(mo, name, None)


def _by_module(outputs: list[Any]) -> dict[str, Any]:
    return {str(_field(mo, "module")): mo for mo in outputs}


def _title(mo: Any) -> str:
    title = _field(mo, "title")
    return str(title if title is not None else (_field(mo, "module") or ""))


def _plain(x: Any, table: TableRenderer) -> list[str]:
    """Markdown pieces of a value a module gives: text, tables and lists of them."""
    from metacheck.report.blocks import ReportTable

    if x is None or (not isinstance(x, str | ReportTable | list | tuple) and _is_na(x)):
        return []
    if isinstance(x, ReportTable):
        return [table(x)]
    if isinstance(x, str):
        return [x]
    if isinstance(x, Sequence) or hasattr(x, "tolist"):
        out: list[str] = []
        for item in x.tolist() if hasattr(x, "tolist") else x:
            out.extend(_plain(item, table) if not _is_na(item) else [])
        return out
    return [str(x)]


def _joined(x: Any, table: TableRenderer) -> str:
    """R ``paste(x, collapse = "\\n\\n")`` of a module's text or blocks."""
    return "\n\n".join(_plain(x, table))


def _cell(x: Any, table: TableRenderer) -> str:
    """A cell of a ``checks`` table as text: ``""`` for NA."""
    return _joined(x, table)


def _md(text: str) -> str:
    """Markdown a module wrote, in the checklist report's form: a callout with a title folds
    in a ``<details>`` box and tabs become headings (R: written so under ``report_type()``)."""
    if ":::" not in text:
        return text
    from metacheck.report.render import static_markdown

    return static_markdown(text, fold=True)


def _escape(x: str) -> str:
    """R ``.spv_html_escape()``: ``&``, ``<`` and ``>``."""
    return x.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ---------------------------------------------------------------------------
# the checks
# ---------------------------------------------------------------------------


def _has_section(mo: Any, table: TableRenderer) -> bool:
    """Whether a module gives its own section (``checklist_section``)."""
    return any(p != "" for p in _plain(_field(mo, "checklist_section"), table))


def _section_id(mo: Any) -> str:
    module = _field(mo, "module")
    return "section-" + re.sub(r"[^A-Za-z0-9_-]", "-", str(module) if module else "module")


def _single_check(mo: Any, table: TableRenderer) -> pd.DataFrame:
    """A module without ``checks`` as one check: light, summary, report."""
    from metacheck.report.file_tree import file_tree_drop_from_report

    tl = _field(mo, "traffic_light") or "info"
    report: Any = _field(mo, "report")
    report = "" if report is None else report
    # data_check's file tree has a section of its own (the "Files in the repository")
    if _field(mo, "module") == "data_check":
        report = file_tree_drop_from_report(report)
    report_text = _joined(report, table)
    summary = _joined(_field(mo, "summary_text"), table)
    details = "" if tl == "fail" or report_text == summary else report_text
    found = f"This module failed to run: {report_text}" if tl == "fail" else summary
    status = _LIGHT_STATUS.get(tl, "info")
    # a module with its own section shows its details there, not folded here
    note = ""
    if _has_section(mo, table):
        details = ""
        note = f"See the section [{_title(mo)}](#{_section_id(mo)}) below for the details."
    return pd.DataFrame(
        {
            "status": [status],
            "title": [_title(mo)],
            "found": [found],
            "note": [note],
            "details": [details],
        }
    )


def checklist_checks(module_output: Any, table: TableRenderer | None = None) -> pd.DataFrame:
    """Every check of every module, as one table (port of ``.checklist_checks()``).

    Columns ``module``, ``status``, ``title``, ``section``, ``found``, ``todo``, ``why``,
    ``note``, ``details`` and ``details_title`` (all text; ``""`` when a module left one
    out). A module's ``checks`` table gives its rows; a module without one is a single
    check (see the module's docs). A status that is not one of
    :data:`~metacheck.module.CHECK_STATUSES` is shown for information, with a warning.
    ``table`` turns a :class:`~metacheck.report.blocks.ReportTable` in a cell into text
    (default: a static table).
    """
    if table is None:
        from metacheck.report.render import static_table_chunk

        table = static_table_chunk
    frames: list[pd.DataFrame] = []
    for mo in _outputs(module_output):
        checks = _field(mo, "checks")
        if isinstance(checks, pd.DataFrame) and len(checks) > 0:
            frame = pd.DataFrame(
                {
                    col: (
                        [_cell(v, table) for v in checks[col].tolist()]
                        if col in checks.columns
                        else [""] * len(checks)
                    )
                    for col in CHECK_COLUMNS
                }
            )
        else:
            single = _single_check(mo, table)
            frame = pd.DataFrame(
                {
                    col: single[col].tolist() if col in single.columns else [""]
                    for col in CHECK_COLUMNS
                }
            )
        frame = frame.apply(lambda col: col.map(_md)).astype("object")
        module = _field(mo, "module")
        unknown = ~frame["status"].isin(CHECK_STATUSES)
        if unknown.any():
            names = ", ".join(dict.fromkeys(frame.loc[unknown, "status"]))
            warnings.warn(
                f"{module} gave checks with an unknown status ({names}); "
                "they are shown for information",
                stacklevel=2,
            )
            frame.loc[unknown, "status"] = "info"
        frame["module"] = str(module) if module else ""
        frames.append(frame)
    if not frames:
        return pd.DataFrame(
            {col: pd.Series([], dtype="object") for col in (*CHECK_COLUMNS, "module")}
        )
    return pd.concat(frames, ignore_index=True)


def _records(checks: pd.DataFrame) -> list[dict[str, str]]:
    """The rows of a checks table, as dicts of text."""
    return [{str(k): str(v) for k, v in row.items()} for row in checks.to_dict(orient="records")]


def _fold(details: str, title: str = "") -> str:
    """A check's folded details: ``<details>`` with its summary line (``""`` for none)."""
    if details == "":
        return ""
    if title == "":
        title = "Show details"
    return f"<details><summary>{_escape(title)}</summary>\n\n::: {{.fold}}\n\n{details}\n\n:::\n\n</details>"


def _module_sections(outputs: list[Any], table: TableRenderer) -> list[str]:
    """The sections modules give, each under its own heading, with the module's
    full report folded at its end."""
    sections = []
    for mo in outputs:
        if not _has_section(mo, table):
            continue
        report_value = _field(mo, "report")
        report = _md(_joined("" if report_value is None else report_value, table))
        title = _title(mo)
        sections.append(
            "\n\n".join(
                [
                    f"## {title} {{#{_section_id(mo)}}}",
                    _md(_joined(_field(mo, "checklist_section"), table)),
                    _fold(report, f"All details of the {title}"),
                ]
            )
        )
    return sections


def _file_tree_section(outputs: list[Any], table: TableRenderer) -> str | None:
    """The repository's file tree, open in a section of its own (``None`` when no
    repository was checked)."""
    from metacheck.report.file_tree import file_tree

    tree = file_tree(_by_module(outputs))
    if tree is None:
        return None
    return "\n\n".join(
        ["## Files in the repository {#files}", *(p for t in tree for p in _plain(t, table))]
    )


def _block(check: Mapping[str, str]) -> str:
    """One block for a check that needs work: its tag, title and section, then what
    was found, what to do, why and a note, each on its own line, then its folded details."""
    tag = _RESULT[check["status"]][2]
    where = f" [{check['section']}]{{.where}}" if check["section"] else ""
    head = (
        f"::: {{.check-head}}\n[{tag}]{{.tag .{check['status']}}} "
        f"[{check['title']}]{{.check-title}}{where}\n:::"
    )
    labels = {"found": "What we found", "todo": "What to do", "why": "Why", "note": "Note"}
    rows = []
    for col, label in labels.items():
        if not check[col]:
            continue
        cls = " .todo" if col == "todo" else ""
        rows.append(f"::: {{.label}}\n{label}\n:::\n\n::: {{.text{cls}}}\n{check[col]}\n:::")
    body = "::: {.rows}\n" + "\n\n".join(rows) + "\n:::" if rows else ""
    return (
        f"::: {{.check .{check['status']}}}\n{head}\n\n{body}\n\n"
        f"{_fold(check['details'], check['details_title'])}\n:::"
    )


def _row(check: Mapping[str, str]) -> str:
    """One line for a check that is fine, not applicable or for information: its tag,
    title, what was found, what to do and a note (if any)."""
    tag = _RESULT[check["status"]][2]
    text = "\n\n".join(t for t in (check["found"], check["todo"], check["note"]) if t)
    return (
        f"::: {{.done-row}}\n::: {{.tagcell}}\n[{tag}]{{.tag .{check['status']}}}\n:::\n\n"
        f"::: {{.t}}\n{check['title']}\n:::\n\n::: {{.d}}\n{text}\n:::\n:::"
    )


def _counts(checks: pd.DataFrame) -> str:
    """The counts at the top: one box per result except "for information", a link to
    its section when there is something in it."""
    boxes = []
    for status, _, _, label in CHECKLIST_RESULTS:
        if status == "info":
            continue
        n = int((checks["status"] == status).sum())
        inner = f"<b>{n}</b><span>{label}</span>"
        if n > 0:
            boxes.append(f'<a class="count {status}" href="#{status}">{inner}</a>')
        else:
            boxes.append(f'<span class="count {status} zero">{inner}</span>')
    return f'<nav class="counts" aria-label="Results">{"".join(boxes)}</nav>'


# ---------------------------------------------------------------------------
# images and the template
# ---------------------------------------------------------------------------

_MEDIA = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "svg": "image/svg+xml",
    "gif": "image/gif",
    "ttf": "font/ttf",
}


def _data_uri(data: bytes, ext: str) -> str:
    media = _MEDIA.get(ext.lower(), "application/octet-stream")
    return f"data:{media};base64,{base64.b64encode(data).decode('ascii')}"


def _file_uri(path: Any) -> str:
    """An image file as a ``data:`` URI, so the report stays one file; ``""`` when the
    file is not there."""
    if path is None or _is_na(path) or str(path) == "" or not os.path.isfile(os.fspath(path)):
        return ""
    ext = os.path.splitext(os.fspath(path))[1].lstrip(".")
    with open(os.fspath(path), "rb") as fh:
        return _data_uri(fh.read(), ext)


@cache
def _asset_uri(name: str) -> str:
    data = resources.files("metacheck.report").joinpath("assets", name).read_bytes()
    return _data_uri(data, name.rsplit(".", 1)[-1])


@cache
def _template() -> str:
    text = (
        resources.files("metacheck.report")
        .joinpath("templates", "_report_checklist.Rmd")
        .read_text(encoding="utf-8")
    )
    return "\n".join(re.split(r"\r\n|\r|\n", text)).rstrip("\n")


_FOOTER = (
    "<footer><p>If there are things we can improve, please email us at "
    '<a href="mailto:metacheck@scienceverse.org">metacheck@scienceverse.org</a> '
    'or leave an issue at <a href="https://github.com/scienceverse/metacheck/issues">'
    "github.com/scienceverse/metacheck/issues</a>.</p></footer>"
)


def _paper_values(paper: Any, column: str) -> list[Any]:
    info = getattr(paper, "info", None) if paper is not None else None
    if info is None or not hasattr(info, "columns") or column not in info.columns:
        return []
    return list(info[column].tolist())


def _module_commits(outputs: list[Any]) -> list[str]:
    """``module X (commit abc1234)`` for each module installed from a repository."""
    lines = []
    for mo in outputs:
        prov = getattr(mo, "run_provenance", None)
        source = prov.get("source") if isinstance(prov, Mapping) else None
        rev = source.get("rev") if isinstance(source, Mapping) else None
        if rev:
            lines.append(f"module {_field(mo, 'module')} (commit {str(rev)[:7]})")
    return lines


def checklist_qmd(
    module_output: Any,
    paper: Any = None,
    *,
    table: TableRenderer | None = None,
    version: str | None = None,
    title: str | None = None,
    subtitle: str | None = None,
) -> str:
    """The text of the checklist report (R Markdown), from module output (port of
    ``.report_checklist_qmd()``).

    The text starts with the template's YAML header, style and masthead, followed by
    the counts, the checks sorted by result, the modules' own sections, the file tree
    and "More detail". Tables are raw HTML blocks (metacheck writes R chunks), so
    Pandoc renders it without R. ``title`` and ``subtitle`` replace the report's title
    (a module's own title when the report has one module) and the paper's title.
    """
    if table is None:
        from metacheck.report.render import static_table_chunk

        table = static_table_chunk
    from metacheck.report.report import _how_it_works_parts, _report_version

    outputs = _outputs(module_output)

    # title: the module's own title when the report has one module
    if title is None:
        own = _field(outputs[0], "title") if len(outputs) == 1 else None
        title = "MetaCheck Report" if own is None else str(own)
    if subtitle is None:
        titles = _paper_values(paper, "title")
        subtitle = "" if not titles or _is_na(titles[0]) else str(titles[0])

    # date, metacheck version, DOI and the commits of installed modules
    dois = _paper_values(paper, "doi")
    meta = [
        _dt.date.today().isoformat(),
        f"Metacheck {version or _report_version()}",
        *_module_commits(outputs),
        *([f"DOI {dois[0]}"] if dois and not _is_na(dois[0]) and str(dois[0]) != "" else []),
    ]

    # metacheck's logo, and any logo a module gives (`logo`, a file path)
    logos = [_asset_uri("logo.png")]
    for mo in outputs:
        for path in _plain(_field(mo, "logo"), table):
            uri = _file_uri(path)
            if uri and uri not in logos:
                logos.append(uri)
    imgs = "".join(f'<img src="{uri}" alt="logo">' for uri in logos)

    head = _template()
    fill = {
        "PAGETITLE": re.sub(
            r'(["\\])', r"\\\1", ": ".join([title, *([subtitle] if subtitle else [])])
        ),
        "FONT_DATA_URI": _asset_uri("Cabin.ttf"),
        "LOGOS": imgs,
        "TITLE": _escape(title),
        "SUBTITLE": _escape(subtitle),
        "META": _escape(" · ".join(meta)),
    }
    for token, value in fill.items():
        head = head.replace("{{" + token + "}}", value)

    # the checks, by result
    checks = checklist_checks(outputs, table)
    intro = [p for mo in outputs for p in _plain(_field(mo, "intro"), table)]
    sections = []
    for status, heading, _, _ in CHECKLIST_RESULTS:
        these = checks[checks["status"] == status]
        if len(these) == 0:
            continue
        items = [_block(c) if status in _WORK else _row(c) for c in _records(these)]
        if status not in _WORK:
            items = ["::: {.done}\n" + "\n\n".join(items) + "\n:::"]
        sections.append("\n\n".join([f"## {heading} [({len(these)})]{{.n}} {{#{status}}}", *items]))

    # more detail: the folded details of the checks shown as one line, and how each
    # module works
    more = [
        _fold(c["details"], c["details_title"] or c["title"])
        for c in _records(
            checks[checks["status"].isin(["info", "fine", "na"]) & (checks["details"] != "")]
        )
    ]
    for mo in outputs:
        text, validation = _how_it_works_parts(_field(mo, "module"))
        if text is None and not validation:
            continue
        works = _fold("\n\n".join([*(text or []), *validation]), f"How {_title(mo)} works")
        if works:
            more.append(works)
    more_section = "## More detail\n\n::: {.more}\n" + "\n\n".join(more) + "\n:::" if more else None

    body = [
        *(["::: {.mc-intro}\n" + "\n\n".join(intro) + "\n:::"] if intro else []),
        _counts(checks),
        *sections,
        *_module_sections(outputs, table),
        *([tree] if (tree := _file_tree_section(outputs, table)) else []),
        *([more_section] if more_section else []),
    ]
    return "\n\n".join([head, "::: {.mc-main}\n\n" + "\n\n".join(body) + "\n\n:::", _FOOTER, "\n"])


def checklist_html(
    module_output: Any,
    paper: Any = None,
    *,
    title: str | None = None,
    subtitle: str | None = None,
    repository: str | None = None,
    version: str | None = None,
) -> str:
    """The checklist report as one self-contained HTML page: no script, no Quarto, Pandoc
    or R.

    Parameters
    ----------
    module_output:
        Finished module outputs: a :class:`~metacheck.report.report.ReportOutput` or any
        mapping of module name to output, a list of
        :class:`~metacheck.module.ModuleOutput`, or one output. A module may give a
        ``checks`` table, a ``checklist_section``, an ``intro`` and a ``logo`` (see
        :func:`~metacheck.report.blocks.report_type`); one without is shown as a single
        check from its traffic light, summary text and report. When ``data_check`` or
        ``repo_check`` ran, the repository's file tree gets a section of its own.
    paper:
        The paper checked (its title becomes the subtitle and its DOI is listed), or
        ``None``.
    title:
        The report's title. Default: the module's own title when the report has one
        module, else "MetaCheck Report".
    subtitle:
        Replaces the paper's title under the title.
    repository:
        The name or link of the repository checked, used as the subtitle when neither
        ``subtitle`` nor the paper gives one.
    version:
        The version shown in the report (default: metacheck's, with pytacheck's).

    Returns
    -------
    The page, ready to save or serve (UTF-8).
    """
    from metacheck._version import __version__
    from metacheck.report.render import TableSlots, markdown_to_html

    if subtitle is None and repository:
        titles = _paper_values(paper, "title")
        if not titles or _is_na(titles[0]) or str(titles[0]) == "":
            subtitle = str(repository)
    slots = TableSlots(static=True)
    text = checklist_qmd(
        module_output, paper, table=slots, version=version, title=title, subtitle=subtitle
    )
    page_title = re.search(r'^pagetitle: "((?:[^"\\]|\\.)*)"', text, flags=re.M)
    heading = re.sub(r"\\(.)", r"\1", page_title.group(1)) if page_title else "MetaCheck Report"
    text = re.sub(r"\A---\n.*?\n---\n", "", text, count=1, flags=re.S)
    body = slots.fill(markdown_to_html(text, static=True))
    # the style goes in the head
    style = "".join(re.findall(r"<style>.*?</style>", body, flags=re.S))
    body = re.sub(r"<style>.*?</style>\n?", "", body, flags=re.S)
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<meta name="generator" content="pytacheck {_html.escape(__version__)}">\n'
        f"<title>{_html.escape(heading, quote=False)}</title>\n{style}\n</head>\n"
        f"<body>\n{body}\n</body>\n</html>\n"
    )


def checklist_markdown(
    module_output: Any,
    paper: Any = None,
    *,
    version: str | None = None,
    title: str | None = None,
    subtitle: str | None = None,
) -> str:
    """The checklist report as plain (GitHub-flavoured) Markdown: the title, the checks
    sorted by result and the modules' own sections, without the page's style."""
    from metacheck.report.render import markdown_to_gfm, table_gfm

    text = checklist_qmd(
        module_output, paper, table=table_gfm, version=version, title=title, subtitle=subtitle
    )
    heading = re.search(r"<h1>(.*?)</h1>", text, flags=re.S)
    sub = re.search(r'<p class="subtitle">(.*?)</p>', text, flags=re.S)
    meta = re.search(r'<p class="meta">(.*?)</p>', text, flags=re.S)
    main = text.split("::: {.mc-main}", 1)[1].rsplit("<footer>", 1)[0]
    main = re.sub(r"\n:::\s*$", "", main.rstrip())
    lines = [f"# {_html.unescape(heading.group(1)) if heading else 'MetaCheck Report'}"]
    if sub and sub.group(1):
        lines.append(f"**{_html.unescape(sub.group(1))}**")
    if meta:
        lines.append(_html.unescape(meta.group(1)))
    out = "\n\n".join([*lines, main.strip()])
    # bracketed spans are Pandoc's: keep their text
    out = re.sub(r"\[([^\]\n]*)\]\{[^}\n]*\}", r"\1", out)
    out = re.sub(r"(?m)^(#{2,6} .*?) \{#[^}]*\}$", r"\1", out)
    return markdown_to_gfm(out).strip() + "\n"
