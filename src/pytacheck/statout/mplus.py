"""Read Mplus (``.out``) output files.

Port of ``R/mplus.R``. An Mplus ``.out`` file is plain text: the verbatim input
syntax under ``INPUT INSTRUCTIONS`` and result sections introduced by
ALL-CAPS headers from a fixed vocabulary (``MODEL FIT INFORMATION``,
``MODEL RESULTS``, ...). Each section yields fixed-width tables (a
"MODEL RESULTS"-style grouped table, a higher-order-moment statistics table or
a correlation/covariance matrix) plus one table of its remaining
``Label ... value`` lines.

Lines are handled with R's ``NA`` semantics: R's ``a:b`` with ``a > b`` counts
down, so a section header on the last line (or two adjacent headers) gives a
section whose lines include ``NA``/the next header, exactly as in metacheck.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from pytacheck._r import grepl, regexec, strsplit, sub, trimws
from pytacheck.statout.spv import (
    _file_path,
    _file_path_sans_ext,
    _html_page,
    _r_dirname,
    _read_lines,
    _spv_html_escape,
    _spv_table_html,
    _write_lines,
)
from pytacheck.statout.stata import (
    _any_numlike as _any_numlike_cols,
)
from pytacheck.statout.stata import (
    _cols_names,
    _cols_to_frame,
    _one_row_frame,
    _split_block,
    _string_columns_frame,
    _trim,
)

if TYPE_CHECKING:
    pass

__all__ = ["export_mplus_html", "import_mplus_output"]

# Mplus's "not applicable" placeholder for fixed parameters (documentation).
_MPLUS_FIXED_SENTINEL = "999.000"

# Known section titles (adapted by metacheck from MplusAutomation), matched
# whole-line, case-insensitively, as PCRE.
_MPLUS_SECTION_TITLES = [
    "INPUT INSTRUCTIONS",
    "SUMMARY OF ANALYSIS",
    "SUMMARY OF DATA",
    "SUMMARY OF DATA FOR THE FIRST DATA SET",
    "SUMMARY OF DATA FOR THE FIRST REPLICATION",
    "SUMMARY OF MISSING DATA PATTERNS FOR THE FIRST REPLICATION",
    "SUMMARY OF MISSING DATA PATTERNS FOR THE FIRST DATA SET",
    "SUMMARY OF MISSING DATA PATTERNS",
    "SUMMARY OF CATEGORICAL DATA PROPORTIONS",
    "COVARIANCE COVERAGE OF DATA FOR THE FIRST REPLICATION",
    "COVARIANCE COVERAGE OF DATA",
    "PROPORTION OF DATA PRESENT",
    "UNIVARIATE SAMPLE STATISTICS",
    "UNIVARIATE HIGHER-ORDER MOMENT DESCRIPTIVE STATISTICS",
    "THE MODEL ESTIMATION TERMINATED NORMALLY",
    "SAMPLE STATISTICS",
    "SAMPLE STATISTICS FOR THE FIRST REPLICATION",
    "RESULTS FOR BASIC ANALYSIS",
    "CROSSTABS FOR CATEGORICAL VARIABLES",
    "UNIVARIATE PROPORTIONS AND COUNTS FOR CATEGORICAL VARIABLES",
    "SUMMARY OF CENSORED LIMITS",
    "COUNT PROPORTION OF ZERO, MINIMUM AND MAXIMUM VALUES",
    "RANDOM STARTS RESULTS RANKED FROM THE BEST TO THE WORST FIT FUNCTION VALUES",
    "RANDOM STARTS RESULTS RANKED FROM THE BEST TO THE WORST LOGLIKELIHOOD VALUES",
    "TESTS OF MODEL FIT",
    "MODEL FIT INFORMATION",
    "CLASSIFICATION QUALITY",
    "SUMMARY OF MODEL FIT INFORMATION",
    "RESULTS FOR EXPLORATORY FACTOR ANALYSIS",
    "MODEL RESULTS USE THE LATENT CLASS VARIABLE ORDER",
    "FINAL CLASS COUNTS AND PROPORTIONS FOR THE LATENT CLASSES",
    "FINAL CLASS COUNTS AND PROPORTIONS FOR THE LATENT CLASS PATTERNS",
    "CLASSIFICATION OF INDIVIDUALS BASED ON THEIR MOST LIKELY LATENT CLASS PATTERN",
    "C-SPECIFIC CLASSIFICATION RESULTS",
    "LATENT CLASS INDICATOR MEANS AND PROBABILITIES FOR EACH LATENT CLASS",
    r"AVERAGE LATENT CLASS PROBABILITIES FOR MOST LIKELY LATENT CLASS PATTERN \(ROW\)",
    "LATENT TRANSITION PROBABILITIES BASED ON THE ESTIMATED MODEL",
    "FINAL CLASS COUNTS AND PROPORTIONS FOR EACH LATENT CLASS VARIABLE",
    "CLASSIFICATION OF INDIVIDUALS BASED ON THEIR MOST LIKELY LATENT CLASS MEMBERSHIP",
    r"AVERAGE LATENT CLASS PROBABILITIES FOR MOST LIKELY LATENT CLASS MEMBERSHIP \(ROW\)",
    r"CLASSIFICATION PROBABILITIES FOR THE MOST LIKELY LATENT CLASS MEMBERSHIP \(ROW\)",
    r"CLASSIFICATION PROBABILITIES FOR THE MOST LIKELY LATENT CLASS MEMBERSHIP \(COLUMN\)",
    r"LOGITS FOR THE CLASSIFICATION PROBABILITIES FOR THE MOST LIKELY LATENT CLASS MEMBERSHIP \(ROW\)",
    r"LOGITS FOR THE CLASSIFICATION PROBABILITIES FOR THE MOST LIKELY LATENT CLASS MEMBERSHIP \(COLUMN\)",
    "MODEL RESULTS",
    "MODEL RESULTS FOR .*",
    "LOGISTIC REGRESSION ODDS RATIO RESULTS.*",
    "RESULTS IN PROBABILITY SCALE",
    "LATENT CLASS INDICATOR ODDS RATIOS FOR THE LATENT CLASSES",
    "IRT PARAMETERIZATION IN TWO-PARAMETER LOGISTIC METRIC",
    "IRT PARAMETERIZATION IN TWO-PARAMETER PROBIT METRIC",
    "IRT PARAMETERIZATION",
    "BRANT WALD TEST FOR PROPORTIONAL ODDS",
    "BETWEEN-LEVEL FACTOR SCORE COMPARISONS",
    "ALTERNATIVE PARAMETERIZATIONS FOR THE CATEGORICAL LATENT VARIABLE REGRESSION",
    "ODDS RATIOS? FOR THE ALTERNATIVE PARAMETERIZATIONS FOR THE CATEGORICAL LATENT VARIABLE REGRESSION",
    "ODDS RATIOS FOR TESTS OF CATEGORICAL LATENT VARIABLE MULTINOMIAL LOGISTIC REGRESSIONS",
    "LATENT CLASS ODDS RATIO RESULTS",
    "LOGRANK OUTPUT",
    "STANDARDIZED MODEL RESULTS",
    r"WITHIN-LEVEL STANDARDIZED MODEL RESULTS FOR CLUSTER \d+",
    "R-SQUARE",
    "QUALITY OF NUMERICAL RESULTS",
    "QUALITY OF NUMERICAL RESULTS FOR .*",
    "TECHNICAL OUTPUT",
    r"TECHNICAL \d+ OUTPUT",
    r"TECHNICAL \d+ OUTPUT FOR THE .* MODEL",
    "TECHNICAL 5/6 OUTPUT",
    "TOTAL, TOTAL INDIRECT, SPECIFIC INDIRECT, AND DIRECT EFFECTS",
    "TOTAL, TOTAL INDIRECT, SPECIFIC INDIRECT, AND DIRECT EFFECTS FOR LATENT RESPONSE VARIABLES",
    r"TOTAL, INDIRECT, AND DIRECT EFFECTS BASED ON COUNTERFACTUALS \(CAUSALLY-DEFINED EFFECTS\)",
    "STANDARDIZED TOTAL, TOTAL INDIRECT, SPECIFIC INDIRECT, AND DIRECT EFFECTS",
    "CONFIDENCE INTERVALS OF MODEL RESULTS",
    "CONFIDENCE INTERVALS FOR THE LOGISTIC REGRESSION ODDS RATIO RESULTS.*",
    "CREDIBILITY INTERVALS OF MODEL RESULTS",
    "CONFIDENCE INTERVALS OF STANDARDIZED MODEL RESULTS",
    "CREDIBILITY INTERVALS OF STANDARDIZED MODEL RESULTS",
    "CONFIDENCE INTERVALS IN PROBABILITY SCALE",
    "CONFIDENCE INTERVALS OF TOTAL, TOTAL INDIRECT, SPECIFIC INDIRECT, AND DIRECT EFFECTS",
    "CONFIDENCE INTERVALS OF STANDARDIZED TOTAL, TOTAL INDIRECT, SPECIFIC INDIRECT,",
    "CONFIDENCE INTERVALS OF STANDARDIZED TOTAL, TOTAL INDIRECT, SPECIFIC INDIRECT, AND DIRECT EFFECTS",
    "CONFIDENCE INTERVALS FOR TESTS OF CATEGORICAL LATENT VARIABLE MULTINOMIAL LOGISTIC REGRESSIONS",
    "CONFIDENCE INTERVALS OF ODDS RATIOS FOR TESTS OF CATEGORICAL LATENT VARIABLE MULTINOMIAL",
    "EQUALITY TESTS OF MEANS ACROSS CLASSES USING POSTERIOR PROBABILITY-BASED",
    "EQUALITY TESTS OF MEANS ACROSS CLASSES USING THE BCH PROCEDURE",
    "EQUALITY TESTS OF MEANS ACROSS CLASSES USING THE 3-STEP PROCEDURE",
    "EQUALITY TESTS OF MEANS/PROBABILITIES ACROSS CLASSES",
    "TESTS OF CATEGORICAL LATENT VARIABLE MULTINOMIAL LOGISTIC REGRESSIONS USING",
    "DIFFERENCE OUTPUT",
    r"THE FOLLOWING DATA SET\(S\) DID NOT RESULT IN A COMPLETED REPLICATION:",
    "RESIDUAL OUTPUT",
    "RESIDUAL OUTPUT FOR THE.*",
    "MODEL MODIFICATION INDICES",
    "MODIFICATION INDICES",
    "MODEL COMMAND WITH FINAL ESTIMATES USED AS STARTING VALUES",
    r"SUMMARIES OF PLAUSIBLE VALUES \(N = NUMBER OF OBSERVATIONS \* NUMBER OF IMPUTATIONS\)",
    r"SUMMARY OF PLAUSIBLE STANDARD DEVIATION \(N = NUMBER OF OBSERVATIONS\)",
    r"FACTOR SCORE INFORMATION \(COMPLETE DATA\)",
    "SUMMARY OF FACTOR SCORES",
    "PLOT INFORMATION",
    "SAVEDATA INFORMATION",
    "CORRELATIONS AND MEAN SQUARE ERROR OF THE TRUE FACTOR VALUES AND THE FACTOR SCORES",
    "RESULTS SAVING INFORMATION",
    "SAMPLE STATISTICS FOR ESTIMATED FACTOR SCORES",
    "DIAGRAM INFORMATION",
    r"EXPLORATORY FACTOR ANALYSIS WITH [1-9]\d* FACTOR\(S\):",
    r"EXPLORATORY FACTOR ANALYSIS WITH \d+ WITHIN FACTOR\(S\) AND \d+ BETWEEN FACTOR\(S\):",
    r"EXPLORATORY FACTOR ANALYSIS WITH \d+ WITHIN FACTOR\(S\) AND UNRESTRICTED BETWEEN COVARIANCE:",
    r"EXPLORATORY FACTOR ANALYSIS WITH UNRESTRICTED WITHIN COVARIANCE AND \d+ BETWEEN FACTOR\(S\):",
]
_MPLUS_SECTION_HEADER_REGEXPR = "^(" + "|".join(_MPLUS_SECTION_TITLES) + ")$"

_MPLUS_HEADER_WORDS = ["estimate", r"s\.e\.", r"est\./s\.e\.", "p-value", "two-tailed", r"std\."]
_MPLUS_STATS_HEADER_WORDS = ["skewness", "kurtosis", "percentiles"]
_SEP = "␟"


# -- R NA semantics for single values ------------------------------------------


def _nz(x: str | None) -> bool:
    """R ``nzchar()`` (``nzchar(NA)`` is ``TRUE``)."""
    return x is None or x != ""


def _gl(pattern: str, x: str | None, **kw: Any) -> bool:
    """R ``grepl()`` on one value (``FALSE`` for ``NA``)."""
    return x is not None and bool(grepl(pattern, x, **kw))


def _r_seq(a: int, b: int) -> list[int]:
    """R ``a:b`` (counts down when ``a > b``)."""
    return list(range(a, b + 1)) if a <= b else list(range(a, b - 1, -1))


def _mplus_is_section_header(line: str | None) -> bool:
    """Port of R/mplus.R::.mplus_is_section_header()."""
    tl = _trim(line)
    return _nz(tl) and _gl(_MPLUS_SECTION_HEADER_REGEXPR, tl, ignore_case=True, perl=True)


def _mplus_sections(lines: Sequence[str | None]) -> list[dict[str, Any]]:
    """Port of R/mplus.R::.mplus_sections(): ``{"title", "lines"}`` per section.

    A section's lines are those between its header and the next one: none
    when the header is the last line or directly followed by another header.
    R's ``(h + 1):end`` then counts down and yields ``NA`` and the header
    itself (UPSTREAM_ISSUES U147).
    """
    lines = list(lines)
    header_idx = [i for i, ln in enumerate(lines, 1) if _mplus_is_section_header(ln)]
    if not header_idx:
        return []
    ends = [h - 1 for h in header_idx[1:]] + [len(lines)]
    return [
        {"title": _trim(lines[h - 1]), "lines": lines[h:e]}
        for h, e in zip(header_idx, ends, strict=True)
    ]


def _mplus_is_numlike(x: Any) -> Any:
    """Port of R/mplus.R::.mplus_is_numlike() (vectorised like ``grepl()``)."""
    t = trimws(x)
    a = grepl(r"^-?[0-9][0-9.]*(e[-+]?[0-9]+)?\*?$", t, ignore_case=True)
    b = grepl(r"(?i)^(inf|-?inf|na|nan|\*+)$", t)
    if isinstance(a, bool):
        return a or b
    return [p or q for p, q in zip(a, b, strict=True)]


def _mplus_split_block(block: Sequence[str | None]) -> list[list[str]] | None:
    """Port of R/mplus.R::.mplus_split_block() (blank-column splitting)."""
    return _split_block(block)


def _mplus_looks_header(ln: str | None) -> bool:
    """Port of R/mplus.R::.mplus_looks_header()."""
    tl = _trim(ln)
    if not _nz(tl) or _gl(r"^_+(\s+_+)*$", tl):
        return False
    grps = [None] if tl is None else strsplit(tl, r"\s+")
    grps = [g for g in grps if _nz(g)]
    if not grps:
        return False
    num = _mplus_is_numlike(grps)
    return sum(bool(v) for v in num) / len(num) < 0.5


def _mplus_is_group_label(ln: str | None) -> bool:
    """Port of R/mplus.R::.mplus_is_group_label()."""
    tl = _trim(ln)
    return _nz(tl) and not _gl("[0-9]", tl)


def _mplus_is_stat_header_line(ln: str | None) -> bool:
    """Port of R/mplus.R::.mplus_is_stat_header_line(): a MODEL RESULTS header."""
    tl = _trim(ln)
    if not _nz(tl) or _gl("[0-9]", tl):
        return False
    return _gl("|".join(_MPLUS_HEADER_WORDS), tl, ignore_case=True) or (
        _gl("observed", tl, ignore_case=True) and _gl("variable", tl, ignore_case=True)
    )


def _mplus_find_header(lines: Sequence[str | None], start: int) -> dict[str, Any] | None:
    """Port of R/mplus.R::.mplus_find_header() (``start`` is 1-based)."""
    n = len(lines)
    if start > n or not _mplus_is_stat_header_line(lines[start - 1]):
        return None
    end = start
    if start + 1 <= n and _mplus_is_stat_header_line(lines[start]):
        end = start + 1
    return {"lines": list(lines[start - 1 : end]), "next_line": end + 1}


def _mplus_is_level_label(ln: str | None) -> bool:
    """Port of R/mplus.R::.mplus_is_level_label(): Within/Between/Class/Group labels."""
    tl = _trim(ln)
    return _gl(r"^(within|between)(\s+level)?$", tl, ignore_case=True) or _gl(
        r"^(latent class|class|group)\s+\S+", tl, ignore_case=True
    )


def _any_numlike(columns: list[list[str | None]]) -> bool:
    """``any(vapply(df, function(c_) any(.mplus_is_numlike(c_)), logical(1)))``."""
    return _any_numlike_cols(columns, _mplus_is_numlike)


def _mplus_read_grouped_table(lines: Sequence[str | None], start: int) -> dict[str, Any] | None:
    """Port of R/mplus.R::.mplus_read_grouped_table(): a MODEL RESULTS-style table."""
    n = len(lines)
    hdr = _mplus_find_header(lines, start)
    if hdr is None:
        return None
    header_lines = hdr["lines"]
    j = hdr["next_line"]
    data_lines: list[str] = []
    group: str | None = None
    level: str | None = None
    while j <= n:
        dl = lines[j - 1]
        dtl = _trim(dl)
        if not _nz(dtl):
            k = j + 1
            if k <= n and _mplus_find_header(lines, k) is not None:
                break
            if k <= n and _nz(_trim(lines[k - 1])):
                j += 1
                continue
            break
        if not _gl("[0-9]", dtl):
            if _mplus_is_level_label(dtl):
                level, group = dtl, None
            else:
                group = dtl
            j += 1
            continue
        lv = "" if level is None or level == "" else level
        gp = "" if group is None or group == "" else group
        data_lines.append(f"{lv}{_SEP}{gp}{_SEP}{'NA' if dl is None else dl}")
        j += 1
    if not data_lines:
        return {"data": None, "next_line": start + 1}
    parts = [d.split(_SEP, 2) for d in data_lines]
    level_col = [p[0] for p in parts]
    group_col = [p[1] for p in parts]
    body_lines = [p[2] for p in parts]
    has_level = any(v != "" for v in level_col)
    has_group = any(v != "" for v in group_col)
    cols = _mplus_split_block(header_lines + body_lines)
    if cols is None or len(cols) < 2:
        return {"data": None, "next_line": start + 1}
    n_header = len(header_lines)
    columns: list[list[str | None]] = [cl[n_header:] for cl in cols]
    names = _cols_names(cols, n_header)
    if has_group:
        columns.insert(0, group_col)  # type: ignore[arg-type]
        names.insert(0, "group")
    if has_level:
        columns.insert(0, level_col)  # type: ignore[arg-type]
        names.insert(0, "level")
    if not _any_numlike(columns):
        return {"data": None, "next_line": start + 1}
    return {"data": _string_columns_frame(columns, names), "next_line": j}


def _read_simple_table(
    lines: Sequence[str | None], start: int, header_lines: list[str | None], j: int
) -> dict[str, Any]:
    n = len(lines)
    data_lines: list[str | None] = []
    while j <= n:
        dl = lines[j - 1]
        dtl = _trim(dl)
        if not _nz(dtl) or not _gl("[0-9]", dtl):
            break
        data_lines.append(dl)
        j += 1
    if not data_lines:
        return {"data": None, "next_line": start + 1}
    cols = _mplus_split_block(header_lines + data_lines)
    if cols is None or len(cols) < 2:
        return {"data": None, "next_line": start + 1}
    n_header = len(header_lines)
    if not _any_numlike([cl[n_header:] for cl in cols]):
        return {"data": None, "next_line": start + 1}
    return {"data": _cols_to_frame(cols, n_header), "next_line": j}


def _mplus_read_matrix_table(lines: Sequence[str | None], start: int) -> dict[str, Any] | None:
    """Port of R/mplus.R::.mplus_read_matrix_table(): header, ``____`` underline, rows."""
    n = len(lines)
    if start > n or not _mplus_looks_header(lines[start - 1]):
        return None
    header_lines = [lines[start - 1]]
    j = start + 1
    if j <= n and _mplus_looks_header(lines[j - 1]) and (j + 1 > n or _gl("^_+", _trim(lines[j]))):
        header_lines.append(lines[j - 1])
        j += 1
    if not (j <= n and _gl(r"^_+(\s+_+)*$", _trim(lines[j - 1]))):
        return None
    return _read_simple_table(lines, start, header_lines, j + 1)


def _mplus_is_stats_header_line(ln: str | None) -> bool:
    """Port of R/mplus.R::.mplus_is_stats_header_line()."""
    tl = _trim(ln)
    return (
        _nz(tl)
        and not _gl("[0-9]", tl)
        and _gl("|".join(_MPLUS_STATS_HEADER_WORDS), tl, ignore_case=True)
    )


def _mplus_read_stats_table(lines: Sequence[str | None], start: int) -> dict[str, Any] | None:
    """Port of R/mplus.R::.mplus_read_stats_table(): higher-order moment statistics."""
    n = len(lines)
    if start > n or not _mplus_is_stats_header_line(lines[start - 1]):
        return None
    header_lines = [lines[start - 1]]
    j = start + 1
    if j <= n and _mplus_looks_header(lines[j - 1]):
        header_lines.append(lines[j - 1])
        j += 1
    if j <= n and not _nz(_trim(lines[j - 1])):
        j += 1
    return _read_simple_table(lines, start, header_lines, j)


class _Tables(list):  # type: ignore[type-arg]
    """A list of tables carrying R's ``consumed`` attribute."""

    consumed: list[bool]


def _mplus_output_tables(lines: Sequence[str | None]) -> _Tables:
    """Port of R/mplus.R::.mplus_output_tables().

    Returns the detected tables (``{"title": None, "data"}``); the returned list's
    ``consumed`` attribute marks every line used by a table.
    """
    lines = list(lines)
    n = len(lines)
    i = 1
    tables = _Tables()
    consumed = [False] * n
    while i <= n:
        res = _mplus_read_grouped_table(lines, i)
        if res is None:
            res = _mplus_read_stats_table(lines, i)
        if res is None:
            res = _mplus_read_matrix_table(lines, i)
        if res is None:
            i += 1
            continue
        if res["data"] is not None:
            tables.append({"title": None, "data": res["data"]})
            for k in _r_seq(i, res["next_line"] - 1):
                if 1 <= k <= n:
                    consumed[k - 1] = True
        i = res["next_line"]
    tables.consumed = consumed
    return tables


_LABELVALUE_RE = (
    r"^\s*([A-Za-z][A-Za-z0-9 ./()%*-]*?)\s{2,}(-?[0-9][0-9.]*(?:D[-+]?[0-9]+)?\*?)\s*$"
)


def _mplus_output_labelvalue(
    lines: Sequence[str | None], title: str | None
) -> list[dict[str, Any]]:
    """Port of R/mplus.R::.mplus_output_labelvalue(): one row of label/value pairs."""
    stat: list[str] = []
    val: list[str] = []
    for ln in lines:
        if ln is None:
            continue
        m = regexec(_LABELVALUE_RE, ln)
        if len(m) == 3:
            stat.append(str(trimws(m[1])))
            val.append(m[2])
    if not stat:
        return []
    return [{"title": title, "data": _one_row_frame(stat, val)}]


def _mplus_is_genuine_output(path: str | os.PathLike[str]) -> bool:
    """Port of R/mplus.R::.mplus_is_genuine_output(): an ``Mplus VERSION`` banner."""
    try:
        lines = _read_lines(path, n=5)
    except OSError:
        return False
    return any(grepl("^Mplus VERSION", lines))


def import_mplus_output(path: str | os.PathLike[str]) -> list[dict[str, Any]]:
    """Read an Mplus (.out) output file.

    Port of R/mplus.R::import_mplus_output(). Splits the file into its
    section-headed blocks and extracts each section's fixed-width tables and
    label/value statistics (``INPUT INSTRUCTIONS`` is skipped).

    Args:
        path: path to a ``.out`` file.

    Returns:
        A list of result tables, each a dict ``analysis`` (the section title),
        ``title``, ``data`` (a data frame of character columns), ``syntax``
        (the file's ``INPUT INSTRUCTIONS`` block) and ``table_index``.

    Raises:
        FileNotFoundError: if ``path`` does not exist.
        ValueError: if it is not a ``.out`` file or not Mplus output.
    """
    path = os.fspath(path)
    if not os.path.exists(path):
        raise FileNotFoundError(f"File not found: {path}")
    if not grepl(r"\.out$", path, ignore_case=True):
        raise ValueError(f"Not a .out file: {path}")
    if not _mplus_is_genuine_output(path):
        raise ValueError(f"Not a Mplus .out file: {path}")
    lines = _read_lines(path)
    syntax = _mplus_syntax_lines(lines)
    sections = _mplus_sections(lines)
    out: list[dict[str, Any]] = []
    for sec in sections:
        if sec["title"] == "INPUT INSTRUCTIONS":
            continue
        tabs = _mplus_output_tables(sec["lines"])
        remaining = [ln for ln, c in zip(sec["lines"], tabs.consumed, strict=True) if not c]
        blocks = [*tabs, *_mplus_output_labelvalue(remaining, sec["title"])]
        for b in blocks:
            data = b.get("data")
            if data is None or len(data) == 0 or data.shape[1] == 0:
                continue
            out.append(
                {
                    "analysis": sec["title"],
                    "title": b.get("title"),
                    "data": data,
                    "syntax": syntax,
                }
            )
    for i, tb in enumerate(out, 1):
        tb["table_index"] = i
    return out


def _mplus_syntax_lines(lines: Sequence[str | None]) -> str | None:
    """Port of R/mplus.R::.mplus_syntax_lines(): the ``INPUT INSTRUCTIONS`` block."""
    lines = list(lines)
    starts = [i for i, ln in enumerate(lines, 1) if _gl(r"^INPUT INSTRUCTIONS\s*$", _trim(ln))]
    if not starts:
        return None
    # the lines after the header (none when it is the last line; R's
    # (start):length counts down there, UPSTREAM_ISSUES U147)
    rest = lines[starts[0] :]
    end_rel = [
        k
        for k, ln in enumerate(rest, 1)
        if _mplus_is_section_header(ln) or _gl(r"^\*\*\*\s*(WARNING|ERROR)", _trim(ln))
    ]
    body = rest[: end_rel[0] - 1] if end_rel else rest
    body = [b for b in body if _nz(_trim(b))]
    if not body:
        return None
    return "\n".join("NA" if b is None else str(_trim(b)) for b in body)


def _mplus_export_syntax(
    out_path: str | os.PathLike[str], code_dir_name: str = "code"
) -> str | None:
    """Port of R/mplus.R::.mplus_export_syntax().

    Writes the ``INPUT INSTRUCTIONS`` block to ``<dir>/<code_dir_name>/<name>.inp``;
    returns that path, or ``None`` when there is none (or the file is not
    Mplus output).
    """
    out_path = os.fspath(out_path)
    if not os.path.exists(out_path):
        raise FileNotFoundError(f"File not found: {out_path}")
    if not _mplus_is_genuine_output(out_path):
        return None
    try:
        lines = _read_lines(out_path)
    except OSError:
        lines = []
    if not lines:
        return None
    syntax = _mplus_syntax_lines(lines)
    if syntax is None or syntax == "":
        return None
    code_dir = _file_path(_r_dirname(out_path), code_dir_name)
    os.makedirs(code_dir, exist_ok=True)
    out_file = _file_path(code_dir, _file_path_sans_ext(os.path.basename(out_path)) + ".inp")
    _write_lines(strsplit(syntax, "\n", fixed=True), out_file)
    return out_file


def export_mplus_html(
    path: str | os.PathLike[str], out: str | os.PathLike[str] | None = None
) -> str:
    """Export an Mplus (.out) output file as standalone HTML.

    Port of R/mplus.R::export_mplus_html(): one heading per section and one
    ``<table>`` per result :func:`import_mplus_output` recovers.

    Args:
        path: path to a ``.out`` file.
        out: path to write the HTML file to; defaults to ``path`` with its
            extension replaced by ``.html``.

    Returns:
        The path written to.
    """
    path = os.fspath(path)
    if not os.path.exists(path):
        raise FileNotFoundError(f"File not found: {path}")
    if not grepl(r"\.out$", path, ignore_case=True):
        raise ValueError(f"Not a .out file: {path}")
    out = str(sub(r"\.out$", ".html", path, ignore_case=True)) if out is None else os.fspath(out)
    tables = import_mplus_output(path)
    if not tables:
        body = "<p>No result tables could be recovered from this .out file.</p>"
    else:
        sections = []
        last_section: object = None
        for k, tb in enumerate(tables):
            heading = ""
            if k == 0 or tb["analysis"] != last_section:
                heading = f"<h3>{_spv_html_escape(tb['analysis'])}</h3>"
                last_section = tb["analysis"]
            ttl = tb.get("title")
            title = (
                f"<h4>{_spv_html_escape(ttl)}</h4>"
                if ttl is not None and ttl != tb["analysis"]
                else ""
            )
            sections.append(heading + title + _spv_table_html(tb["data"]))
        body = "\n".join(sections)
    _write_lines(_html_page(os.path.basename(path), "h3", body), out)
    return out
