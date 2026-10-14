"""The to-do list of a brief report (port of the brief-mode helpers of ``R/report-helpers.R``).

A brief report (``report_type("brief")`` or ``"simple_brief"``) lists only the
modules flagged red, yellow or failed, each followed by the specific things to
fix: the sentence, value or file a module flagged. No module returns those as
a field of its own, so they are read from the tables of its report.
metacheck parses each table back out of the R code chunk ``scroll_table()``
wrote; pytacheck keeps tables as data (:class:`~metacheck.report.blocks.ReportTable`),
so it reads them directly.

Most modules' tables go through one generic rule (:func:`_report_group_table`):
a column whose values repeat is a status to count, the others are evidence to
quote. Modules whose tables mix findings with an inventory of what was checked
have a renderer of their own (:data:`_MODULE_BULLETS`).
"""

from __future__ import annotations

import html as _html
import re
from collections.abc import Callable, Sequence
from typing import Any

import pandas as pd

from metacheck._r import as_character, plural, r_sort_key, regextract_all, sub
from metacheck.report.blocks import ReportTable

__all__ = ["flagged_bullets", "validation_table"]

#: the module statuses a brief report lists: what needs attention
FLAGGED_LIGHTS = ("red", "yellow", "fail")

FEEDBACK = (
    "If there are things we can improve, please email us at "
    "[metacheck@scienceverse.org](mailto:metacheck@scienceverse.org) "
    "or leave an issue at "
    "[github.com/scienceverse/metacheck/issues](https://github.com/scienceverse/metacheck/issues)."
)

_NO_VALIDATION = "No validation information yet."


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _text(x: Any) -> str:
    """A cell as R's ``paste()`` shows it (``"NA"`` when missing)."""
    v = as_character(x)
    return "NA" if v is None else v


def _blank(x: Any) -> bool:
    """Missing or ``""``."""
    v = as_character(x)
    return v is None or v == ""


def _flat(report: Any) -> list[Any]:
    if report is None:
        return []
    if isinstance(report, str | ReportTable):
        return [report]
    if isinstance(report, Sequence):
        out: list[Any] = []
        for item in report:
            out.extend(_flat(item))
        return out
    return [report]


def _sorted_groups(keys: Sequence[str]) -> list[list[int]]:
    """R ``split(seq_along(keys), keys)``: row positions by key, keys sorted."""
    groups: dict[str, list[int]] = {}
    for i, k in enumerate(keys):
        groups.setdefault(k, []).append(i)
    return [groups[k] for k in sorted(groups, key=r_sort_key)]


def _items(n: int) -> str:
    return f"{n:d} item{plural(n)}"


def _col(df: pd.DataFrame, name: str) -> list[Any]:
    return df[name].tolist()


def _named(tables: list[pd.DataFrame], *names: str) -> list[pd.DataFrame]:
    """The tables whose columns are exactly *names*, in order."""
    return [t for t in tables if [str(c) for c in t.columns] == list(names)]


# ---------------------------------------------------------------------------
# tables of a report
# ---------------------------------------------------------------------------


def _report_extract_tables(report: Any) -> list[pd.DataFrame]:
    """Port of ``.report_extract_tables()``: every non-empty table of a report, in order."""
    return [b.data for b in _flat(report) if isinstance(b, ReportTable) and len(b.data) > 0]


def _report_merge_tables(tables: list[pd.DataFrame]) -> list[pd.DataFrame]:
    """Port of ``.report_merge_tables()``: join tables that describe the same rows.

    Consecutive tables with the same columns are stacked (the same table once
    per file); a table sharing exactly one column with the one before is
    joined on it (``power``'s ``power_id`` links a status to its sentence).
    """
    if len(tables) <= 1:
        return list(tables)
    merged = [tables[0]]
    for tbl in tables[1:]:
        last = merged[-1]
        if list(tbl.columns) == list(last.columns):
            merged[-1] = pd.concat([last, tbl], ignore_index=True)
            continue
        shared = [c for c in tbl.columns if c in set(last.columns)]
        if len(shared) == 1:
            key = shared[0]
            # the key can be a number in one table and text in the other
            left = last.assign(**{key: [_text(v) for v in last[key]]})
            right = tbl.assign(**{key: [_text(v) for v in tbl[key]]})
            joined = left.merge(right, on=key, how="outer", sort=False)
            order = sorted(range(len(joined)), key=lambda i: r_sort_key(joined[key].iloc[i]))
            cols = [key, *[c for c in joined.columns if c != key]]
            merged[-1] = joined.iloc[order][cols].reset_index(drop=True)
            continue
        merged.append(tbl)
    return merged


_ID_COLUMN = re.compile(r"(^|_)id$", re.IGNORECASE)


def _report_group_table(tbl: pd.DataFrame, max_quotes: int = 3) -> list[str]:
    """Port of ``.report_group_table()``: one bullet per distinct issue of a table.

    A column whose values repeat is a status, counted per group; the other
    non-empty columns (but row ids) are evidence, quoted for the first
    *max_quotes* rows of each group. A table with no such column gives one
    bullet per row.
    """
    n = len(tbl)
    if n == 0:
        return []
    names = [str(c) for c in tbl.columns]
    values = [tbl.iloc[:, i].tolist() for i in range(tbl.shape[1])]
    status_cols: list[int] = []
    context_cols: list[int] = []
    for i, col in enumerate(values):
        kept = [_text(v) for v in col if not _blank(v)]
        if not kept:
            continue  # nothing in it
        if len(set(kept)) < n:
            status_cols.append(i)
        elif not _ID_COLUMN.search(names[i]):
            context_cols.append(i)

    if not status_cols:
        bullets = []
        for r in range(n):
            clauses = [
                f"**{names[i]}:** {_text(values[i][r])}" if names[i] else _text(values[i][r])
                for i in range(len(names))
            ]
            bullets.append("- " + " -- ".join(clauses))
        return bullets

    keys = ["\x1f".join(_text(values[i][r]) for i in status_cols) for r in range(n)]
    out = []
    for rows in _sorted_groups(keys):
        first = rows[0]
        status = ", ".join(f"**{names[i]}:** {_text(values[i][first])}" for i in status_cols)
        bullet = f"- {_items(len(rows))}: {status}"
        if not context_cols:
            out.append(bullet)
            continue
        quote_rows = rows[:max_quotes]
        quotes = [
            "  > " + " -- ".join(_text(values[i][r]) for i in context_cols) for r in quote_rows
        ]
        if len(rows) > max_quotes:
            quotes.append(f"  > ...and {len(rows) - max_quotes:d} more.")
        verb = "sentence" if len(quote_rows) == 1 else "sentences"
        out.append(
            "\n".join([bullet, f"  These issues were observed in the following {verb}:", *quotes])
        )
    return out


def _power_filter(tbl: pd.DataFrame) -> pd.DataFrame:
    """``power``'s rows that are a problem (``.report_table_filters$power``).

    Without an LLM, ``power_type`` is a classification and only ``"unknown"``
    is a problem; with one, a row is a problem when any detail the LLM was
    asked for is missing.
    """
    llm_cols = [
        "power_type",
        "statistical_test",
        "sample_size",
        "alpha_level",
        "power",
        "effect_size",
        "effect_size_metric",
        "software",
    ]
    present = [c for c in llm_cols if c in tbl.columns]
    if not present:
        return tbl
    if present == ["power_type"]:
        keep = [as_character(v) in (None, "unknown") for v in tbl["power_type"]]
    else:
        keep = [any(as_character(tbl[c].iloc[r]) is None for c in present) for r in range(len(tbl))]
    return tbl[keep].reset_index(drop=True)


#: Port of ``.report_table_filters``: per module, the rows of a table that are a problem.
_TABLE_FILTERS: dict[str, Callable[[pd.DataFrame], pd.DataFrame]] = {"power": _power_filter}


def _report_find_callout(report: Any, title_pattern: str) -> tuple[str, str] | None:
    """Port of ``.report_find_callout()``: (title, HTML body) of a callout in a report.

    The body goes into a ``<details>`` block of a to-do item, where markdown
    is not reliably rendered, so it is turned into HTML here: escaped, its
    ```` fences as ``<pre>`` and ``**bold**`` as ``<strong>``.
    """
    from metacheck.stats.helpers import _stat_html_escape

    chunk = next(
        (b for b in _flat(report) if isinstance(b, str) and re.search(title_pattern, b)), None
    )
    if chunk is None:
        return None
    m = re.search(
        r'::: \{\.callout-\w+[^}]*title="([^"]*)"[^}]*\}\n\n(.*?)\n\n:::', chunk, flags=re.S
    )
    if m is None:
        return None
    body = str(_stat_html_escape(m.group(2)))
    body = re.sub(r"````\n(.*?)\n````", r"<pre>\1</pre>", body, flags=re.S)
    body = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", body)
    return m.group(1), body


def _report_file_findings_by_file(
    tbl: pd.DataFrame, file_col: str, finding_col: str, split: bool = True
) -> dict[str, list[str]]:
    """Port of ``.report_file_findings_by_file()``: each file's distinct findings.

    A cell can hold several findings joined by ``,`` or ``|``; with
    ``split=False`` (free text such as a parse error) the cell is one finding,
    its line breaks collapsed.
    """
    out: dict[str, list[str]] = {}
    for file, cell in zip(_col(tbl, file_col), _col(tbl, finding_col), strict=True):
        text = _text(cell)
        if split:
            findings = re.split(r"\s*(?:,|\|)\s*", text)
        else:
            text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
            findings = [re.sub(r"\s+", " ", text).strip()]
        kept = out.setdefault(_text(file), [])
        kept.extend(f for f in dict.fromkeys(findings) if f not in kept)
    return out


def _report_file_finding_bullets(
    tbl: pd.DataFrame, file_col: str, finding_col: str, kind: str, split: bool = True
) -> list[str]:
    """Port of ``.report_file_finding_bullets()``: ``- <kind>: <findings> in <file>``."""
    by_file = _report_file_findings_by_file(tbl, file_col, finding_col, split)
    return [f"- {kind}: {', '.join(f)} in {file}" for file, f in by_file.items()]


# ---------------------------------------------------------------------------
# module renderers (.report_module_bullets)
# ---------------------------------------------------------------------------

_CODE_MISSING = ("File name", "Missing Files")
_CODE_ABSOLUTE = ("File name", "Absolute paths found")
_CODE_PARSE = ("File name", "Error Message")
_CODE_OVERVIEW = (
    "File Name",
    "% Comments",
    "Missing Files",
    "Absolute Paths",
    "Code Between Libraries",
)


def _code_check_bullets(mo: Any) -> list[str]:
    """``code_check``: one line per file naming its missing files and absolute paths,
    one per parse error, then its other tables (not the per-file overview)."""
    tables = _report_extract_tables(mo.report)
    missing = _named(tables, *_CODE_MISSING)
    absolute = _named(tables, *_CODE_ABSOLUTE)
    missing_by = _report_file_findings_by_file(missing[0], *_CODE_MISSING) if missing else {}
    abs_by = _report_file_findings_by_file(absolute[0], *_CODE_ABSOLUTE) if absolute else {}
    folded = []
    for f in dict.fromkeys([*missing_by, *abs_by]):
        parts = []
        if f in missing_by:
            parts.append(f"a missing file: <strong>{', '.join(missing_by[f])}</strong>")
        if f in abs_by:
            parts.append(f"an absolute path <strong>{', '.join(abs_by[f])}</strong>")
        folded.append(f"- In {f}: we observed {'; and '.join(parts)}")
    parse = [
        b
        for t in _named(tables, *_CODE_PARSE)
        for b in _report_file_finding_bullets(t, *_CODE_PARSE, "parse error", split=False)
    ]
    known = (_CODE_MISSING, _CODE_ABSOLUTE, _CODE_PARSE, _CODE_OVERVIEW)
    others = [t for t in tables if tuple(str(c) for c in t.columns) not in known]
    generic = [b for t in _report_merge_tables(others) for b in _report_group_table(t)]
    return [*dict.fromkeys([*folded, *parse]), *generic]


def _bold_list(values: Sequence[str]) -> str:
    return ", ".join(f"<strong>{v}</strong>" for v in values)


def _codebook_check_bullets(mo: Any) -> list[str]:
    """``codebook_check``: the data columns no codebook documents, and the codebook
    variables that are not in the data."""
    tables = _report_extract_tables(mo.report)
    out = []
    docs = _named(tables, "Column", "Documented", "Label", "Codebook Variable", "Source", "Status")
    if docs:
        doc = pd.concat(docs, ignore_index=True)
        undoc = list(
            dict.fromkeys(
                _text(c)
                for c, d in zip(_col(doc, "Column"), _col(doc, "Documented"), strict=True)
                if as_character(d) == "no"
            )
        )
        if undoc:
            one = len(undoc) == 1
            out.append(
                f"- The following data column{plural(len(undoc))} {'is' if one else 'are'} "
                f"not documented in a codebook: {_bold_list(undoc)}"
            )
    unused = _named(tables, "Variable", "Label", "Source")
    if unused and len(unused[0]) > 0:
        names = [_text(v) for v in _col(unused[0], "Variable")]
        one = len(names) == 1
        out.append(
            f"- The following codebook variable{plural(len(names))} "
            f"{'does' if one else 'do'} not appear in the data: {_bold_list(names)}"
        )
    return out


def _data_check_bullets(mo: Any) -> list[str]:
    """``data_check``: its "Issues Identified" table, one line per kind of issue
    naming every column and file it was found in (its data previews and
    descriptives are not problems)."""
    tables = _named(_report_extract_tables(mo.report), "File", "Column", "Issues")
    if not tables:
        return []
    tbl = tables[0]
    flat: list[tuple[str, str, str]] = []
    for file, column, cell in zip(
        _col(tbl, "File"), _col(tbl, "Column"), _col(tbl, "Issues"), strict=True
    ):
        plain = re.sub(r"<span[^>]*>\s*|\s*</span>", "", _text(cell))
        flat += [(issue, _text(column), _text(file)) for issue in plain.split("<br>")]
    out = []
    for rows in _sorted_groups([f[0] for f in flat]):
        where = "; ".join(f'"{flat[r][1]}" in "{flat[r][2]}"' for r in rows)
        out.append(f"- {_items(len(rows))}: {flat[rows[0]][0]}, namely {where}")
    return out


def _repo_check_bullets(mo: Any) -> list[str]:
    """``repo_check``: the repositories that could not be read, and one line per file
    naming every file-naming rule it breaks (its file listings are an inventory)."""
    tables = _report_extract_tables(mo.report)
    errors: list[str] = []
    repos = [
        t
        for t in tables
        if "Error" in t.columns and "Repository" in t.columns and "File" not in t.columns
    ]
    if repos:
        for err, repo in zip(_col(repos[0], "Error"), _col(repos[0], "Repository"), strict=True):
            if not _blank(err):
                errors.append(f'- {_text(err)}: "{_text(repo)}"')
    naming: list[str] = []
    named = _named(tables, "File", "Rule", "Severity", "Detail")
    if named and len(named[0]) > 0:
        tbl = named[0]
        files, rules, details = _col(tbl, "File"), _col(tbl, "Rule"), _col(tbl, "Detail")
        for rows in _sorted_groups([_text(f) for f in files]):
            found = "; ".join(f"{_text(rules[r])} ({_text(details[r])})" for r in rows)
            naming.append(f"- <strong>{_text(files[rows[0]])}</strong>: {found}")
    return [*errors, *naming]


_OUTCOME_LABELS = {
    "skipped_missing_inputs": "could not be run because a referenced input file is missing",
    "not_parsed": "could not be parsed and so could not be run",
    "dependency_unavailable": "could not be run because a required dependency is unavailable",
}


def _reproducibility_check_bullets(mo: Any) -> list[str]:
    """``reproducibility_check``: inputs that are missing, scripts that did not run
    (with the output of each that errored or timed out) and reported statistics
    that were not fully reproduced."""
    tables = _report_extract_tables(mo.report)
    out: list[str] = []
    missing = _named(tables, "File", "Status", "Reason")
    if missing and len(missing[0]) > 0:
        out += [
            f"- {_text(f)}: {_text(r)}"
            for f, r in zip(_col(missing[0], "File"), _col(missing[0], "Reason"), strict=True)
        ]
    runs = _named(tables, "File", "Outcome", "Detail", "Time (s)")
    if runs:
        tbl = runs[0]
        flagged = [
            (_text(f), _text(o))
            for f, o in zip(_col(tbl, "File"), _col(tbl, "Outcome"), strict=True)
            if as_character(o) != "ran_ok"
        ]
        with_output = [x for x in flagged if x[1] in ("errored", "timed_out")]
        grouped = [x for x in flagged if x[1] not in ("errored", "timed_out")]
        for rows in _sorted_groups([x[1] for x in grouped]):
            outcome = grouped[rows[0]][1]
            label = _OUTCOME_LABELS.get(outcome, outcome)
            files = ", ".join(grouped[r][0] for r in rows)
            out.append(f"- The following file{plural(len(rows))} {label}: {files}")
        for file, outcome in with_output:
            what = (
                "timed out when run" if outcome == "timed_out" else "resulted in an error when run"
            )
            bullet = (
                f"- The following file {what} (see Error details below for more "
                f"information): {file}"
            )
            callout = _report_find_callout(
                mo.report, f"Output — {re.escape(file)} \\({re.escape(outcome)}\\)"
            )
            if callout is not None:
                body = callout[1].replace("\n", "\n    ")
                bullet += (
                    f"\n    <details><summary>Error details</summary>\n\n    {body}\n\n"
                    "    </details>"
                )
            out.append(bullet)
    matches = [t for t in tables if {"Reported", "Found", "Confidence"} <= set(t.columns)]
    if matches:
        tbl = matches[0]
        rows_kept = [r for r, c in enumerate(_col(tbl, "Confidence")) if as_character(c) != "full"]
        reported, found, conf = _col(tbl, "Reported"), _col(tbl, "Found"), _col(tbl, "Confidence")
        keys = [_text(conf[r]) for r in rows_kept]
        for group in _sorted_groups(keys):
            rows = [rows_kept[g] for g in group]
            where = "; ".join(
                f'"{_text(reported[r])}" (found: {"no" if _blank(found[r]) else _text(found[r])})'
                for r in rows
            )
            out.append(f"- {_items(len(rows))}: confidence {keys[group[0]]}, namely {where}")
    return out


def _stat_effect_size_bullets(mo: Any) -> list[str]:
    """``stat_effect_size``: the sentences without an effect size, and the tests whose
    effect size does not match their statistic ("no_match"; "indeterminate" is
    not a confirmed problem)."""
    tables = _report_extract_tables(mo.report)
    out: list[str] = []
    missing = [t for t in tables if [str(c) for c in t.columns] == [""]]
    if missing:
        out += [f'- missing effect size, namely "{_text(v)}"' for v in missing[0].iloc[:, 0]]
    detail = [t for t in tables if {"d Coherence", "eta Coherence"} <= set(t.columns)]
    if detail:
        tbl = detail[0]
        no_match = [
            as_character(d) == "no_match" or as_character(e) == "no_match"
            for d, e in zip(_col(tbl, "d Coherence"), _col(tbl, "eta Coherence"), strict=True)
        ]
        if any(no_match):
            out += _report_group_table(tbl[no_match].reset_index(drop=True))
    return out


#: Port of ``.report_module_bullets``: modules whose tables need a renderer of their own.
_MODULE_BULLETS: dict[str, Callable[[Any], list[str]]] = {
    "code_check": _code_check_bullets,
    "codebook_check": _codebook_check_bullets,
    "data_check": _data_check_bullets,
    "repo_check": _repo_check_bullets,
    "reproducibility_check": _reproducibility_check_bullets,
    "stat_effect_size": _stat_effect_size_bullets,
}


def flagged_bullets(module_output: Any) -> list[str]:
    """Port of ``.report_flagged_bullets()``: a module's to-do items, as markdown bullets.

    Each item names a specific thing to fix (a sentence, value or file), read
    from the tables of the module's report; ``[]`` when its report has none.
    """
    report = getattr(module_output, "report", None)
    blocks = _flat(report)
    if not blocks or all(isinstance(b, str) and b == "" for b in blocks):
        return []
    module = str(getattr(module_output, "module", None) or "")
    renderer = _MODULE_BULLETS.get(module)
    if renderer is not None:
        return renderer(module_output)
    tables = _report_extract_tables(blocks)
    if not tables:
        return []
    # filtered after the merge, so a filtered-out status row cannot leave its
    # partner row in the other table unmatched
    merged = _report_merge_tables(tables)
    keep = _TABLE_FILTERS.get(module)
    if keep is not None:
        merged = [t for t in (keep(m) for m in merged) if len(t) > 0]
    return [b for t in merged for b in _report_group_table(t)]


# ---------------------------------------------------------------------------
# the validation table
# ---------------------------------------------------------------------------


def _validation_texts(module: Any) -> list[str]:
    from metacheck.module import module_info

    try:
        details = module_info(module).details or ""
    except Exception:
        return [_NO_VALIDATION]
    found = regextract_all(r"<validation>.*?</validation>", details)
    if not found:
        return [_NO_VALIDATION]
    return [sub(r"\s*</validation>", "", sub(r"<validation>\s*", "", v)) for v in found]


def validation_table(module_outputs: Sequence[Any]) -> str:
    """Port of ``.report_validation_table()``: how each listed module was validated.

    One row per module (and per ``<validation>`` block of its details); a
    module without one says so, since the absence of validation matters as
    much as its presence.
    """
    rows = [
        f"<tr><td>{_html.escape(_text(mo.title), quote=False)}</td>"
        f"<td>{_html.escape(text, quote=False)}</td></tr>"
        for mo in module_outputs
        for text in _validation_texts(mo.module)
    ]
    table = (
        '<table class="dt-static"><thead><tr><th>Module</th><th>Validation</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table>"
    )
    return (
        "## Validation\n\nIn line with Metacheck's values, we transparently communicate how "
        "each module was validated against manually coded ground truth data.\n\n"
        f"<details><summary>Validation details</summary>\n\n{table}\n\n</details>"
    )
