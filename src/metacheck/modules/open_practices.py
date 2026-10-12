"""Open Practices Check (port of ``inst/modules/open_practices.R``)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

import pandas as pd

from metacheck._r.base import plural
from metacheck._r.frames import bind_rows
from metacheck._r.regex import grepl, gsub
from metacheck.module import module
from metacheck.papers.model import Paper, PaperList
from metacheck.text.search import text_search

# patterns ----
# R string literals translated to regexes (``"\\bavailab"`` is ``\bavailab``).
_ON_REQUEST = r"(on|by)\s+(reasonable\s+)?request"
_AVAILABILITY = (
    r"\bavailab",
    r"\bsupplement",
    r"\barchive",
    r"\baccess",
    r"\bshare",
    r"\bonline\b",
    r"\bfound\b",
    r"\bfind\b",
    r"\bdetailed\b",
    r"\bsee\b",
)
_REPO_WORDS = (
    "http",  # url
    "repositor",
    "archive",
    r"\bosf\b",
    "open science framework",
    "researchbox",
    "zenodo",
    "github",
    "figshare",
    "datadryad",
    "kaggle",
    "mendeley",
    "ukbiobank",
    "ukdataservice",
    "dataverse",
    "clinicalstudydatarequest",
    "ourworldindata",
    _ON_REQUEST,
)
_DATA_WORDS = ("data",)
_CODE_WORDS = (
    r"\bcode\b",
    r"\bsoftware\b",
    r"\bscript",
    r"\banaly",
    r"\bR\b",
    r"\bpython\b",
)
# U107: metacheck searches `\bR\b` case-insensitively, so a lone "r" (a
# correlation, "r = .45") counted as a code word; only a capital R names the
# language (see `_code_rows()`)
_CODE_WORDS_NOT_R = "|".join(w for w in _CODE_WORDS if w != r"\bR\b")
_MATERIALS_WORDS = (
    r"\bmaterials?\b",
    r"\bquestionnaires?\b",
    r"\binstruments?\b",
    r"\bfigures?\b",
    r"\bplots?\b",
)
_PREREG_WORDS = ("pre-?regist", "aspredicted")

#: ``setdiff(repo_words, on_request) |> paste(collapse = "|")``
_IN_REPO = "|".join(w for w in _REPO_WORDS if w != _ON_REQUEST)

_FLAGS = ("data", "code", "materials", "prereg")

_NO_DATA_REPORT = (
    "We did not detect open sharing of data, which could be because there is no data related "
    "to this article, or the repository is not recognized by our code. If there is data, please "
    "consider sharing it in a repository."
)
# metacheck's typo "reconized" is fixed (U83)
_NO_CODE_REPORT = (
    "We did not detect open sharing of code, which could be because there is no code related "
    "to this article, or the repository is not recognized by our code. If there is code, please "
    "consider sharing it in a repository."
)
_STATED_NO_DATA_REPORT = (
    "The data availability statement says there are no data to share for this article."
)
_DATA_REPORT = "Data was openly shared for this article, based on the following text:\n\n> {}"
_CODE_REPORT = "Code was openly shared for this article, based on the following text:\n\n> {}"
_ON_REQUEST_REPORT = (
    "Some materials are shared on request, which is not an acceptable method of sharing. "
    "This is based on the following text:\n\n> {}"
)


# D83: a data availability statement that says there is nothing to share
_NO_DATA_VERBS = r"(generated|collected|used|analy[sz]ed|created|produced|associated)"
_NO_DATA = (
    r"\bnot applicable\b"
    rf"|\bno (new |original |primary |research )?(data|datasets?)\b[^.]{{0,40}}\b{_NO_DATA_VERBS}\b"
    rf"|\b(data|datasets?)\b[^.]{{0,60}}\b(were|was|are|is) not {_NO_DATA_VERBS}\b"
    r"|\bdid not (generate|collect|use|create|produce|analy[sz]e) (any )?(new )?(data|datasets?)\b"
)
_DAS = "data_availability"
# a sentence boundary in a statement bibr kept outside the text
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\"'])")


def _squash(text: str) -> str:
    return " ".join(text.split()).lower()


def _statement_rows(papers: Sequence[Paper], frame: pd.DataFrame) -> pd.DataFrame | None:
    """The sentences of each paper's data availability statement (bibr's
    ``info.data_availability``) that its text does not contain, as rows of *frame*
    with no ``text_id`` and ``section_type`` ``"data_availability"`` (D83)."""
    rows: list[dict[str, Any]] = []
    for p in papers:
        info = p.get("info")
        if not isinstance(info, pd.DataFrame) or _DAS not in info.columns or not len(info):
            continue
        statement = info[_DAS].iloc[0]
        if not isinstance(statement, str) or not statement.strip():
            continue
        body = frame.loc[frame["paper_id"] == p.paper_id, "text"].dropna()
        if _squash(statement) in _squash(" ".join(body.astype(str))):
            continue
        rows.extend(
            {
                "text": sentence,
                "paper_id": p.paper_id,
                "header": "Data Availability",
                "section_type": _DAS,
            }
            for sentence in _SENTENCE_END.split(" ".join(statement.split()))
        )
    if not rows:
        return None
    extra = pd.DataFrame(rows)
    return pd.DataFrame(
        {
            c: pd.Series(extra[c].tolist() if c in extra.columns else [None] * len(extra))
            for c in frame.columns
        }
    ).astype(frame.dtypes.to_dict())


def _search_frame(paper: Any) -> Any:
    """What the searches run on: the sentence table of a paper (:func:`_code_rows` needs a
    table) and the sentences of a data availability statement bibr kept outside the
    text (D83); other inputs are passed through."""
    if not isinstance(paper, Paper | PaperList):
        return paper
    from metacheck.text.search import sentence_table

    frame = sentence_table(paper)[0]
    if "paper_id" not in frame.columns or "text" not in frame.columns:
        return frame
    extra = _statement_rows(list(paper) if isinstance(paper, PaperList) else [paper], frame)
    if extra is None:
        return frame
    return pd.concat([frame, extra], ignore_index=True)


def _in_statement(frame: Any) -> list[bool]:
    """Whether each row of *frame* is in a data availability section or statement."""
    if not isinstance(frame, pd.DataFrame) or "section_type" not in frame.columns:
        return [False] * len(frame) if isinstance(frame, pd.DataFrame) else []
    return (frame["section_type"].astype("string") == _DAS).fillna(False).tolist()


def _data_rows(frame: Any) -> Any:
    """The rows about data: those with the word, and (D83) the sentences of a data
    availability statement that name neither code nor materials ("They can be
    found at https://osf.io/x")."""
    worded = text_search(frame, _DATA_WORDS)
    das = _in_statement(frame)
    if not any(das):
        return worded
    texts = frame["text"].tolist()
    other = grepl("|".join((*_CODE_WORDS, *_MATERIALS_WORDS)), texts, ignore_case=True)
    data = grepl("|".join(_DATA_WORDS), texts, ignore_case=True)
    keep = [d and not o and not w for d, o, w in zip(das, other, data, strict=True)]
    if not any(keep):
        return worded
    return bind_rows([worded, frame.loc[keep].reset_index(drop=True)])


def _no_data(frame: Any, paper_ids: list[str]) -> dict[str, bool]:
    """Per paper, whether its data availability statement says it has no data (D83)."""
    out = dict.fromkeys(paper_ids, False)
    if not isinstance(frame, pd.DataFrame) or "paper_id" not in frame.columns:
        return out
    das = _in_statement(frame)
    texts = frame["text"].tolist()
    said = grepl(_NO_DATA, texts, ignore_case=True)
    for pid, d, s in zip(frame["paper_id"].tolist(), das, said, strict=True):
        if d and s and pid in out:
            out[pid] = True
    return out


def _code_rows(frame: Any) -> Any:
    """Rows of *frame* with a code word, where "R" must be a capital (U107)."""
    if not isinstance(frame, pd.DataFrame) or "text" not in frame.columns or len(frame) == 0:
        return frame
    texts = frame["text"].tolist()
    other = pd.Series(grepl(_CODE_WORDS_NOT_R, texts, ignore_case=True), index=frame.index)
    capital_r = pd.Series(grepl(r"\bR\b", texts), index=frame.index)
    return frame.loc[(other | capital_r).astype(bool).to_numpy()]


def _chain(frame: Any, *steps: tuple[str | Sequence[str], bool]) -> pd.DataFrame:
    """Successive ``text_search(pattern, exclude = ...)`` calls."""
    out: pd.DataFrame = frame
    for pattern, exclude in steps:
        out = text_search(out, pattern, exclude=exclude)
    return out


def _key_value(v: Any) -> Any:
    if isinstance(v, list | tuple):  # a list column (an older bibr export's _bbox_2d)
        return tuple(_key_value(x) for x in v)
    return None if v is None or v is pd.NA or v != v else v


def _key(values: Sequence[Any]) -> tuple[Any, ...]:
    """A join key in which every missing value equals every other (dplyr's ``na_matches``)
    and a list is its elements (dplyr joins on list columns)."""
    return tuple(_key_value(v) for v in values)


def _full_join(
    parts: Sequence[pd.DataFrame], by: list[str]
) -> tuple[pd.DataFrame, list[list[bool]]]:
    """Chained ``dplyr::full_join(..., by = by)`` of tables with unique keys.

    Each flag table's rows are unique (``text_search()`` returns distinct
    rows), so the join keeps the first table's rows, then appends each later
    table's unmatched rows in order. Returns the joined key columns and, per
    table, whether each joined row was in it.
    """
    index: dict[tuple[Any, ...], int] = {}
    picks: list[tuple[int, int]] = []
    for k, part in enumerate(parts):
        cols = [part[c].tolist() for c in by]
        for i, key in enumerate(zip(*cols, strict=True)):
            norm = _key(key)
            if norm not in index:
                index[norm] = len(picks)
                picks.append((k, i))
    flags = [[False] * len(picks) for _ in parts]
    for k, part in enumerate(parts):
        cols = [part[c].tolist() for c in by]
        for key in zip(*cols, strict=True):
            flags[k][index[_key(key)]] = True
    offsets = [0]
    for part in parts[:-1]:
        offsets.append(offsets[-1] + len(part))
    combined = bind_rows([p.loc[:, by] for p in parts])
    rows = [offsets[k] + i for k, i in picks]
    return combined.iloc[rows].reset_index(drop=True), flags


def _arrange(table: pd.DataFrame, paper_ids: list[str]) -> pd.DataFrame:
    """``paper_id`` as ``factor(paper_id, paper_ids)``, then ``arrange(paper_id, text_id)``.

    *paper_ids* are unique (metacheck's ``factor()`` stops on duplicated IDs, U101).
    """
    seen = {pid: i for i, pid in enumerate(paper_ids)}
    pids = table["paper_id"].tolist() if "paper_id" in table.columns else [None] * len(table)
    codes = [None if p is None or p is pd.NA or p != p else seen.get(p) for p in pids]
    keys = pd.DataFrame(
        {
            "paper_id": pd.Series(codes, dtype="Int64"),
            "text_id": pd.array(table["text_id"].tolist(), dtype="Float64")
            if "text_id" in table.columns
            else pd.array([None] * len(table), dtype="Float64"),
        }
    )
    order = keys.sort_values(
        ["paper_id", "text_id"], kind="stable", na_position="last"
    ).index.to_numpy()
    out = table.iloc[order].reset_index(drop=True)
    labels = [None if c is None else paper_ids[c] for c in codes]
    out["paper_id"] = pd.Series([labels[i] for i in order], dtype="string")
    return out


def _summarise(table: pd.DataFrame, in_repo: list[bool]) -> pd.DataFrame:
    """The per-paper ``summarise(..., .by = paper_id)`` of the module."""
    pids = [None if p is pd.NA else p for p in table["paper_id"].tolist()]
    texts = table["text"].tolist()
    flags = {f: table[f].tolist() for f in (*_FLAGS, "on_request")}
    groups: dict[Any, list[int]] = {}
    for i, p in enumerate(pids):
        groups.setdefault(p, []).append(i)

    def any_of(rows: list[int], flag: str, repo: bool) -> bool:
        return any(flags[flag][i] and (in_repo[i] or not repo) for i in rows)

    def statements(rows: list[int], flag: str) -> list[str] | None:
        found = list(dict.fromkeys(texts[i] for i in rows if flags[flag][i]))
        return found or None  # R: character(0) -> NA_character_

    idx = list(groups.values())
    cols: dict[str, Any] = {"paper_id": pd.Series(list(groups), dtype="string")}
    for name, flag, repo in (
        ("data_open", "data", True),
        ("code_open", "code", True),
        ("materials_open", "materials", True),
        ("prereg_open", "prereg", False),
        ("on_request", "on_request", False),
    ):
        cols[name] = pd.Series([any_of(r, flag, repo) for r in idx], dtype="boolean")
    for flag in _FLAGS:
        cols[f"{flag}_statements"] = pd.Series([statements(r, flag) for r in idx], dtype=object)
    return pd.DataFrame(cols)


def _with_no_data(
    summary_table: pd.DataFrame, no_data: dict[str, bool], paper_ids: list[str]
) -> pd.DataFrame:
    """*summary_table* with a ``no_data`` column, and a row for each paper that states it
    has no data but has no flagged sentence (D83)."""
    out = summary_table.copy()
    out.insert(
        6,
        "no_data",
        pd.Series([no_data.get(p, False) for p in out["paper_id"].tolist()], dtype="boolean"),
    )
    have = set(out["paper_id"].tolist())
    missing = [p for p in paper_ids if no_data.get(p) and p not in have]
    if not missing:
        return out
    rows = pd.DataFrame(
        {
            c: pd.Series(
                missing
                if c == "paper_id"
                else [True] * len(missing)
                if c == "no_data"
                else [None] * len(missing)
                if c.endswith("_statements")
                else [False] * len(missing),
                dtype=out[c].dtype,
            )
            for c in out.columns
        }
    )
    out = pd.concat([out, rows], ignore_index=True)
    order = {p: i for i, p in enumerate(paper_ids)}
    keys = out["paper_id"].map(lambda p: order.get(p, len(order)))
    return out.iloc[keys.argsort(kind="stable").to_numpy()].reset_index(drop=True)


@module(
    title="Open Practices Check",
    description=(
        "This module searches for open data, code, materials, and registration statements."
    ),
    details="""
        It is much faster than the previous ODDPub version of this module, and has a lower false negative rate, but also a higher false positive rate.

        pytacheck also reads the data availability statement bibr extracts: its
        sentences count as data statements without the word "data", a statement
        kept outside the text is searched too, and a statement that there are no
        data to share is not reported as missing data.
    """,
    keywords=["general"],
    author=["Lisa DeBruine <lisa.debruine@glasgow.ac.uk>"],
    params={"paper": "a paper object or paperlist object"},
)
def open_practices(paper: Any) -> dict[str, Any]:
    """Port of ``inst/modules/open_practices.R::open_practices()``.

    Searches *paper* (a paper or paper list) for sentences about open data,
    code, materials and preregistration that mention a repository, and flags
    sentences that share things only "on request". Returns the flagged
    sentences (``table``), a per-paper summary (``summary_table``), a traffic
    light (green: data and code shared; yellow: one of them; red: neither,
    or some sharing only on request; info: several papers), a summary text
    and the report.

    D83: bibr's data availability statement counts. Its sentences (in the text
    or only in ``info.data_availability``) need no data word, and a statement
    that there are no data (``summary_table.no_data``) turns the missing data
    into "no data to share" (yellow without code, green with it).
    """
    from metacheck.papers.tables import paper_id

    frame = _search_frame(paper)
    repo = (_REPO_WORDS, False)
    avail = (_AVAILABILITY, False)
    data = _chain(_data_rows(frame), repo, avail)
    code = _chain(_code_rows(frame), (_CODE_WORDS, False), repo, avail)
    materials = _chain(frame, (_MATERIALS_WORDS, False), repo, avail)
    prereg = _chain(
        frame,
        (_PREREG_WORDS, False),
        ("non-?pre-?regist", True),
        (r"not\s+pre-?regist", True),
        repo,
    )

    # table ----
    # put in a sensible naming scheme and order
    by = list(code.columns)
    table, flags = _full_join([data, code, materials, prereg], by)
    for flag, values in zip(_FLAGS, flags, strict=True):
        table[flag] = pd.Series(values, dtype="boolean")

    # R: on an empty paper list the searches return no `text` column, so
    # `table$text` is NULL (grepl() and arrange() still work on the 0 rows) and
    # the summarise() below fails on `text[data]` (U79). A text table without a
    # `text` column whose first column matched (text_search() searches it and
    # drops `text`) has rows, and the tibble refuses the 0-length on_request
    if "text" not in table.columns:
        if len(table) > 0:
            raise ValueError(
                "Assigned data `grepl(on_request, table$text)` must be compatible with "
                "existing data."
            )
        raise ValueError("In argument: `data_statements = list(unique(text[data]))`.")

    # flag on_request, ignoring case like the repository search that found the
    # sentence (metacheck's case-sensitive grepl() misses "ON REQUEST", U107)
    texts = table["text"].tolist()
    table["on_request"] = pd.Series(grepl(_ON_REQUEST, texts, ignore_case=True), dtype="boolean")

    # re-order by paper_id (same as paper) and text_id (inc)
    paper_ids = list(dict.fromkeys(paper_id(paper)))
    table = _arrange(table, paper_ids)

    # summary_table ----
    # sentences that only matched repo_words because of "on request"
    # are flagged in the table, but do not count as open sharing
    texts = table["text"].tolist()
    in_repo = grepl(_IN_REPO, texts, ignore_case=True)
    summary_table = _summarise(table, in_repo)
    # D83: the data availability statement says there are no data to share; such a
    # paper has a row even when no sentence was flagged
    summary_table = _with_no_data(summary_table, _no_data(frame, paper_ids), paper_ids)

    # traffic_light / summary_text ----
    # branch on the number of papers (metacheck branches on the rows of
    # summary_table, i.e. the papers with a flagged sentence, U106)
    several = len(paper_ids) > 1
    # one paper: the single-paper branch when it has a flagged sentence
    single = not several and len(summary_table) == 1
    if several:
        tl = "info"
        found = dict(
            zip(
                summary_table["paper_id"].tolist(),
                zip(
                    summary_table["data_open"].tolist(),
                    summary_table["code_open"].tolist(),
                    strict=True,
                ),
                strict=True,
            )
        )
        # papers without a flagged sentence shared neither
        opened = [found.get(pid, (False, False)) for pid in paper_ids]
        both = sum(bool(d) and bool(c) for d, c in opened)
        only_data = sum(bool(d) and not c for d, c in opened)
        only_code = sum(not d and bool(c) for d, c in opened)
        neither = sum(not d and not c for d, c in opened)
        # "1 paper shared" (metacheck: "1 papers", U82)
        summary_text = (
            f"{both:d} paper{plural(both)} shared both data and code, {only_data:d} only data, "
            f"{only_code:d} only code, and {neither:d} neither."
        )
        stated = dict(
            zip(summary_table["paper_id"].tolist(), summary_table["no_data"].tolist(), strict=True)
        )
        none = sum(
            bool(stated.get(pid)) and not d for pid, (d, _) in zip(paper_ids, opened, strict=True)
        )
        if none:
            summary_text = summary_text[:-1] + (
                f"; {none:d} paper{plural(none)} {'states' if none == 1 else 'state'} "
                "that there are no data to share."
            )
    elif not single:
        summary_text = "Neither shared data nor code detected."
        tl = "red"
    else:
        data_open = bool(summary_table["data_open"].iloc[0])
        code_open = bool(summary_table["code_open"].iloc[0])
        if data_open and code_open:
            summary_text = "Shared data and code detected."
            tl = "green"
        elif data_open:
            summary_text = "Shared data detected."
            tl = "yellow"
        elif code_open:
            summary_text = "Shared code detected."
            tl = "yellow"
        else:
            summary_text = "Neither shared data nor code detected."
            tl = "red"
        if not data_open and bool(summary_table["no_data"].iloc[0]):
            # data that do not exist cannot be shared: only code is missing
            summary_text = (
                "Shared code detected" if code_open else "No shared code detected"
            ) + "; the paper states it has no data to share."
            tl = "green" if code_open else "yellow"
        if bool(summary_table["on_request"].iloc[0]):
            summary_text = gsub(r"\.$", "; some sharing is only on request.", summary_text)
            tl = "red"

    # report ----
    report: list[str] | None = None
    if single:
        data_flag = table["data"].tolist()
        code_flag = table["code"].tolist()
        if not data_open and bool(summary_table["no_data"].iloc[0]):
            data_report = _STATED_NO_DATA_REPORT
        elif not data_open:
            data_report = _NO_DATA_REPORT
        else:
            shared = [t for t, f, r in zip(texts, data_flag, in_repo, strict=True) if f and r]
            data_report = _DATA_REPORT.format("\n\n> ".join(shared))
        if not code_open:
            code_report = _NO_CODE_REPORT
        else:
            shared = [t for t, f, r in zip(texts, code_flag, in_repo, strict=True) if f and r]
            code_report = _CODE_REPORT.format("\n\n> ".join(shared))
        report = [data_report, code_report]
        if bool(summary_table["on_request"].iloc[0]):
            # one paragraph quoting each on-request sentence once (metacheck repeats
            # the paragraph for every matching row, U107)
            asked = [t for t, f in zip(texts, table["on_request"].tolist(), strict=True) if f]
            asked = list(dict.fromkeys(asked))
            quoted = "\n\n> ".join(gsub("\n\n", "\n\n> ", asked))
            report.append(_ON_REQUEST_REPORT.format(quoted))

    # return a list ----
    return {
        "table": table,
        "summary_table": summary_table,
        "na_replace": {"data_open": False, "code_open": False, "no_data": False},
        "traffic_light": tl,
        "summary_text": summary_text,
        "report": report,
    }
