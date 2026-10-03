"""``Doc``: one paper's sentences, indexed once; ``Docs``: the papers of an input.

A Doc is the sentence table ``text_search()`` searches: the paper's text table
with each sentence's section ``header`` and ``section_type`` (invariant V4: the
same rows, columns and dtypes as the table the façade searched before the core).
It keeps the columns a search reads as lists and builds the rest lazily: the
text cleaned once, twice, ... (the text each call of a chain matches), the
casefolded text that the literal prefilter scans, per (pattern, stage) masks as
``(known, true)`` int bitsets, filled in only for the rows a search asks about,
and the DataFrame that results are sliced from.

**Backings.** Both give the same table:

* **columns**: the paper's text and section tables are joined through a lookup
  on ``section_id``. Each table comes from its JSON records while it still has
  them (each column typed on its own, the way ``records_to_frame()`` types it,
  and only when a search needs it), or with one ``.tolist()`` per column from
  its DataFrame once the paper holds one (a materialised paper).
* **frame**: anything else (no rows, a ``section_id`` that is not unique, odd
  columns or types, a paper list, a DataFrame or strings given to the façade):
  the table joined with pandas, as the façade always has.

**The cache.** A paper's Doc lives in ``Paper._derived``, which is never pickled
or copied. It is reused while the paper's ID and the objects its text and
section tables come from are unchanged, and either both tables are still JSON
records (which no caller edits in place), or the Doc was built inside the
trusted scope in effect (:mod:`metacheck.core.scope`). A DataFrame may have been
edited in place, which no cheap check sees, so outside a trusted scope a Doc
built from one is rebuilt on every use.

**Threads.** A Doc is read-only once built; its lazy parts are computed locally
and published with one assignment, so concurrent readers at worst compute a
part twice.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any

import pandas as pd

from metacheck._r.regex import _as_str, _compile, fold
from metacheck._values import is_missing
from metacheck.core.patterns import Pat, PatternSet, as_patterns
from metacheck.core.scope import count, trusted_token, watch_paper
from metacheck.papers.model import Paper, PaperList, is_paper_list
from metacheck.papers.schema import records_to_columns

if TYPE_CHECKING:
    from metacheck.core.groups import GroupDoc
    from metacheck.core.hits import Hits

__all__ = [
    "MARKER",
    "REQUIRED",
    "Doc",
    "Docs",
    "bits",
    "clean",
    "empty_text_frame",
    "merge_sections",
    "table_frame",
]

#: the columns ``text_search()`` adds (as ``NA``) when its table lacks them
REQUIRED = ("text", "text_id", "section_id", "paragraph_id", "paper_id", "header", "section_type")
#: what joins the paragraphs of a group (the cleaning turns it into a blank line)
MARKER = "<~p~>"
_WS = _compile(r"\s+", False, False, False, True)  # gsub("\\s+", " ", x): TRE's \s


def bits(mask: int) -> Iterator[int]:
    """The set bits of *mask*, lowest first."""
    while mask:
        low = mask & -mask
        yield low.bit_length() - 1
        mask ^= low


def clean(s: str | None, first: bool = True) -> str | None:
    """One cleaning of ``text_search()``'s output text.

    White-space runs become one space, ``" , "`` becomes ``", "`` and paragraph
    markers become blank lines (``gsub()`` three times). A sentence the
    cleaning leaves unchanged is returned as it is: when *first*, a printable
    ASCII string without a double space, ``" , "`` or a marker; otherwise (*s*
    was cleaned before) one without ``" , "`` or a newline, the only things a
    second cleaning still changes.
    """
    if s is None:
        return None
    if first:
        if s.isascii() and s.isprintable() and "  " not in s and " , " not in s and MARKER not in s:
            return s
    elif " , " not in s and "\n" not in s:
        return s
    return _WS.sub(" ", s).replace(" , ", ", ").replace(MARKER, "\n\n")


def _key(v: Any) -> Any:
    """A group-key cell as the grouping compares it (``NA`` as ``None``)."""
    return None if pd.isna(v) else v


def _na(v: Any) -> Any:
    return None if is_missing(v) else v


def _hashable(v: Any) -> Any:
    if isinstance(v, list):
        return ("list", tuple(_hashable(x) for x in v))
    if isinstance(v, dict):
        return ("dict", tuple(sorted((k, _hashable(x)) for k, x in v.items())))
    return v


def empty_text_frame() -> pd.DataFrame:
    """A zero-row text table with the columns ``text_search()`` returns for papers."""
    return pd.DataFrame(
        {
            "text": pd.Series([], dtype="string"),
            "text_id": pd.Series([], dtype="Int64"),
            "section_id": pd.Series([], dtype="Int64"),
            "paragraph_id": pd.Series([], dtype="Int64"),
            "paper_id": pd.Series([], dtype="string"),
            "header": pd.Series([], dtype="string"),
            "section_type": pd.Series([], dtype="string"),
        }
    )


def merge_sections(text: pd.DataFrame, sections: Callable[[], pd.DataFrame]) -> pd.DataFrame:
    """*text* (``paper_table(x, "text")``) with each sentence's ``header`` and ``section_type``.

    *sections* gives ``paper_table(x, "section")``; it is called only when the
    text table has columns. An empty input without a ``text`` column (an empty
    paper list, a paper without text) gives the typed columns of a text table,
    so that searches of it chain like any other (metacheck gives logical ``NA``
    columns and drops ``text``; U79).
    """
    if "text" not in text.columns and len(text) == 0:
        return empty_text_frame()
    secs = sections()
    cols = ["section_id", "paper_id", "header", "section_type"]
    if all(c in secs.columns for c in cols) and len(text.columns) > 0:
        right = secs.loc[:, cols]
        if "section_id" in text.columns:
            right = right.astype({"section_id": text["section_id"].dtype}, errors="ignore")
        text = text.merge(right, on=["section_id", "paper_id"], how="left", sort=False)
    return text


def _paper_table(paper: Paper, name: str) -> pd.DataFrame:
    """``paper_table(paper, name)`` of one paper: its table plus ``paper_id``."""
    x = paper.get(name)
    if not isinstance(x, pd.DataFrame):
        return pd.DataFrame()
    x = x.copy(deep=False)
    x["paper_id"] = pd.Series([paper.paper_id] * len(x), index=x.index, dtype="string")
    return x.reset_index(drop=True)


def table_frame(paper: Paper, name: str) -> pd.DataFrame | None:
    """``paper_table(paper, name)`` of one paper whose table is still JSON records.

    Built from the records' typed columns, which the paper keeps (with its Doc)
    for as long as it keeps the records, so the paper's own table is not built:
    the paper stays in the form whose Doc can be reused. ``None`` when the
    table is not JSON records (or names a column twice).
    """
    raw = paper._raw_records(name)
    if raw is None:
        return None
    records, own = raw
    if len(set(own)) != len(own):
        return None
    wanted = [c for c in own if c != "paper_id"]
    at = own.index("paper_id") if "paper_id" in own else len(wanted)
    table = _Table(name, wanted, at, records, None, _typed_columns(paper, name))
    return table.frame(paper.paper_id)


def _typed_columns(paper: Paper, name: str) -> dict[str, pd.Series]:
    """The typed columns of *paper*'s JSON records of table *name*, kept on the paper."""
    derived = _derived(paper)
    token = paper._lazy.get(name)
    hit = derived.get(("columns", name))
    if hit is not None and token is not None and hit[0] is token:
        return hit[1]  # type: ignore[no-any-return]
    cols: dict[str, pd.Series] = {}
    if token is not None:
        derived[("columns", name)] = (token, cols)
    return cols


# -- the columns backing ----------------------------------------------------------------


class _Table:
    """One table of a paper, as ``paper_table(paper, name, columns)`` would give it cheaply.

    From its JSON records (each column typed when first asked for) or from its
    DataFrame (``table.loc[:, wanted]``); ``paper_id`` is not one of its
    columns but is inserted at *at* when the frame is built.
    """

    __slots__ = ("_cols", "at", "base", "n", "name", "records", "wanted")

    def __init__(
        self,
        name: str,
        wanted: list[str],
        at: int,
        records: Sequence[Mapping[str, Any]] | None,
        base: pd.DataFrame | None,
        cols: dict[str, pd.Series] | None = None,
    ) -> None:
        self.name = name
        self.wanted = wanted
        self.at = at
        self.records = records
        self.base = base
        self.n = len(records) if records is not None else len(base)  # type: ignore[arg-type]
        self._cols: dict[str, pd.Series] = {} if cols is None else cols

    @classmethod
    def of(cls, paper: Paper, name: str, columns: list[str] | None = None) -> _Table | None:
        """The table, or ``None`` when it has no rows or lacks one of *columns*."""
        raw = paper._raw_records(name)
        if raw is not None:
            records, own = raw
            if not records or (columns is not None and not set(columns) <= set(own)):
                return None
            wanted = [c for c in (own if columns is None else columns) if c != "paper_id"]
            at = own.index("paper_id") if "paper_id" in own and columns is None else len(wanted)
            return cls(name, wanted, at, records, None, _typed_columns(paper, name))
        table = paper.get(name)
        if not isinstance(table, pd.DataFrame) or len(table) == 0:
            return None
        own = list(table.columns)
        if not table.columns.is_unique or (columns is not None and not set(columns) <= set(own)):
            return None
        wanted = [c for c in (own if columns is None else columns) if c != "paper_id"]
        at = own.index("paper_id") if "paper_id" in own and columns is None else len(wanted)
        return cls(name, wanted, at, None, table.loc[:, wanted].reset_index(drop=True))

    @classmethod
    def from_records(
        cls, name: str, records: Sequence[Mapping[str, Any]], own: Sequence[str]
    ) -> _Table | None:
        """The table of *records* with columns *own* (``None`` without rows)."""
        if not records:
            return None
        wanted = [c for c in own if c != "paper_id"]
        at = list(own).index("paper_id") if "paper_id" in own else len(wanted)
        return cls(name, wanted, at, records, None)

    def column(self, c: str) -> pd.Series:
        """Column *c*, typed (from all of its values, on its own)."""
        s = self._cols.get(c)
        if s is None:
            if self.base is not None:
                s = self.base[c]
            else:
                assert self.records is not None
                s = records_to_columns(self.name, self.records, [c])[c]
            self._cols[c] = s
        return s

    def frame(self, paper_id: Any) -> pd.DataFrame:
        """The table with ``paper_id`` inserted (a new frame each time)."""
        if self.base is not None:
            frame = self.base.copy(deep=False)
        else:
            data = {c: self.column(c) for c in self.wanted}
            frame = pd.DataFrame(data) if data else pd.DataFrame(index=range(self.n))
        ids = pd.Series([paper_id] * len(frame), index=frame.index, dtype="string")
        frame.insert(self.at, "paper_id", ids)
        return frame

    def columns(self) -> list[str]:
        """The frame's column names."""
        cols = list(dict.fromkeys(self.wanted))
        cols.insert(self.at, "paper_id")
        return cols


class _Joined:
    """The text table joined to its sections through a lookup (``_join_one`` of old)."""

    __slots__ = ("paper_id", "rows", "sections", "text")

    def __init__(self, paper_id: str, text: _Table, sections: _Table, rows: list[int]) -> None:
        self.paper_id = paper_id
        self.text = text
        self.sections = sections
        self.rows = rows

    @classmethod
    def of(cls, paper_id: Any, text: _Table | None, sections: _Table | None) -> _Joined | None:
        """``None`` when the merge is needed: no rows, a section ID that is not unique
        (the merge repeats rows), columns the merge would rename, or key types it
        would convert."""
        if not isinstance(paper_id, str) or text is None or sections is None:
            return None
        tcols = text.columns()
        if "section_id" not in tcols or "header" in tcols or "section_type" in tcols:
            return None
        if not isinstance(text.column("section_id").dtype, pd.Int64Dtype):
            return None
        if not isinstance(sections.column("section_id").dtype, pd.Int64Dtype):
            return None
        if not all(
            isinstance(sections.column(c).dtype, pd.StringDtype) for c in ("header", "section_type")
        ):
            return None
        keys = [None if k is pd.NA else k for k in sections.column("section_id").tolist()]
        row_of = {k: j for j, k in enumerate(keys)}
        if len(row_of) < len(keys):
            return None
        rows = [
            row_of.get(None if k is pd.NA else k, -1) for k in text.column("section_id").tolist()
        ]
        return cls(paper_id, text, sections, rows)

    def columns(self) -> list[str]:
        return [*self.text.columns(), "header", "section_type"]

    def complete(self) -> bool:
        """Whether the table has every column ``text_search()`` requires, each once."""
        cols = self.columns()
        return len(set(cols)) == len(cols) and all(c in cols for c in REQUIRED)

    def section_values(self, c: str) -> list[Any]:
        """Column *c* of the sections, per sentence (``None`` without a section row)."""
        vals = [_as_str(v) for v in self.sections.column(c).tolist()]
        return [None if j < 0 else vals[j] for j in self.rows]

    def frame(self) -> pd.DataFrame:
        frame = self.text.frame(self.paper_id)
        for c in ("header", "section_type"):
            frame[c] = self.sections.column(c).array.take(self.rows, allow_fill=True)
        return frame


# -- the Doc --------------------------------------------------------------------------


class Doc:
    """One table of sentences, indexed: a paper's, or one a search is given."""

    paper_id: Any  #: the paper's ID (``None`` for a table of several papers or none)
    n: int
    columns: list[str]  #: the table's columns (those ``text_search()`` adds included)
    missing: list[str]  #: the required columns the source lacked
    text: list[str | None]  #: the stage-0 field: each sentence, as a string
    header: list[str | None]
    section_type: list[Any]
    all: int
    body: int  #: the rows outside the references (what a search skips by default)

    __slots__ = (
        "_base",
        "_blob",
        "_fold",
        "_frame",
        "_groups",
        "_hdr",
        "_hdr_groups",
        "_join",
        "_lit",
        "_stages",
        "_state",
        "_uniq",
        "_values",
        "all",
        "body",
        "columns",
        "header",
        "missing",
        "n",
        "paper_id",
        "section_type",
        "text",
    )

    def __init__(self) -> None:
        self._base: pd.DataFrame | None = None
        self._blob: tuple[str, list[int]] | None = None
        self._fold: list[str] | None = None
        self._frame: pd.DataFrame | None = None
        self._groups: dict[Any, GroupDoc] = {}
        self._hdr: dict[Pat, int] = {}
        self._hdr_groups: dict[str, int] | None = None
        self._join: _Joined | None = None
        self._lit: dict[str, int] = {}
        self._stages: tuple[list[str | None], ...] = ()
        self._state: dict[tuple[Pat, int], tuple[int, int]] = {}
        self._uniq: bool | None = None
        self._values: dict[str, list[Any]] = {}
        self.missing = []

    # -- construction ------------------------------------------------------------------

    @classmethod
    def of(cls, paper: Paper) -> Doc:
        """*paper*'s Doc: the cached one when it may be trusted, else a new one (kept)."""
        derived = _derived(paper)
        token = trusted_token()
        hit = derived.get("doc")
        if hit is not None:
            deps, built_in, doc = hit
            if _same_deps(deps, _deps(paper)) and (
                _all_raw(deps) or (built_in is not None and built_in == token)
            ):
                if token is not None:
                    watch_paper(paper, False)  # the mutation check (CI), else nothing
                return doc  # type: ignore[no-any-return]
        doc = cls._build(paper)
        count("doc_builds")
        # what it was built from, read after the build (which may build a table).
        # Kept only when a later call can be served from it: outside a trusted
        # scope a DataFrame-backed Doc never is, and would keep old frames alive.
        deps = _deps(paper)
        if token is not None or _all_raw(deps):
            derived["doc"] = (deps, token, doc)
        else:
            derived.pop("doc", None)
        if token is not None:
            watch_paper(paper, True)
        return doc

    @classmethod
    def _build(cls, paper: Paper) -> Doc:
        pid = paper.paper_id
        if isinstance(pid, str):  # checked first: the tables are read only then
            joined = _Joined.of(
                pid,
                _Table.of(paper, "text"),
                _Table.of(paper, "section", ["section_id", "header", "section_type"]),
            )
            if joined is not None:
                return cls._from_joined(joined)
        frame = merge_sections(_paper_table(paper, "text"), lambda: _paper_table(paper, "section"))
        return cls.from_frame(frame, paper.paper_id)

    @classmethod
    def from_records(
        cls,
        paper_id: Any,
        text: Sequence[Mapping[str, Any]],
        text_columns: Sequence[str],
        section: Sequence[Mapping[str, Any]],
        section_columns: Sequence[str],
    ) -> Doc | None:
        """The Doc of a paper whose text and section tables are these JSON records.

        For a reader that searches the paper it is making (and gives the Doc to
        it with :meth:`attach`). ``None`` when the records need the merge.
        """
        sec_cols = ["section_id", "header", "section_type"]
        if not set(sec_cols) <= set(section_columns):
            return None
        sections = _Table.from_records("section", section, sec_cols)
        joined = _Joined.of(paper_id, _Table.from_records("text", text, text_columns), sections)
        if joined is None:
            return None
        count("doc_builds")
        return cls._from_joined(joined)

    @classmethod
    def _from_joined(cls, joined: _Joined) -> Doc:
        if not joined.complete():
            return cls.from_frame(joined.frame(), joined.paper_id)
        d = cls()
        d._join = joined
        d.paper_id = joined.paper_id
        d.n = joined.text.n
        d.columns = joined.columns()
        d.text = [_as_str(v) for v in joined.text.column("text").tolist()]
        d.header = joined.section_values("header")
        d.section_type = joined.section_values("section_type")
        d._finish()
        return d

    @classmethod
    def from_frame(cls, frame: pd.DataFrame, paper_id: Any = None) -> Doc:
        """The Doc of a table of sentences (it must not be changed afterwards).

        Required columns it lacks are added as ``NA``; without ``text``, its
        first column is searched (``text_search()``'s rule).
        """
        d = cls()
        d._base = frame
        missing = [c for c in REQUIRED if c not in frame.columns]
        if missing:
            frame = frame.copy(deep=False)
            for m in missing:
                frame[m] = pd.Series([pd.NA] * len(frame), index=frame.index, dtype=object)
            if "text" in missing:
                frame["text"] = frame.iloc[:, 0]
        d.missing = missing
        d._frame = frame
        d.paper_id = paper_id
        d.n = len(frame)
        d.columns = list(frame.columns)
        d.text = [_as_str(v) for v in frame["text"].tolist()]
        d.header = [_as_str(v) for v in frame["header"].tolist()]
        d.section_type = frame["section_type"].tolist()
        d._finish()
        return d

    @classmethod
    def from_strings(cls, xs: Sequence[str]) -> Doc:
        """The Doc of character strings (a one-column ``text`` table)."""
        return cls.from_frame(pd.DataFrame({"text": pd.Series(list(xs), dtype="string")}))

    @classmethod
    def attach(cls, paper: Paper, doc: Doc) -> bool:
        """Keep *doc* (from :meth:`from_records`) as *paper*'s Doc, if it is made from
        the very records *paper* holds (the same row objects); whether it was."""
        join = doc._join
        if join is None or join.paper_id != paper.paper_id or not isinstance(paper.paper_id, str):
            return False
        for name, table in (("text", join.text), ("section", join.sections)):
            raw = paper._raw_records(name)
            if raw is None or table.records is None:
                return False
            records, own = raw
            mine = table.records
            if len(records) != len(mine) or any(
                a is not b for a, b in zip(records, mine, strict=True)
            ):
                return False
            if name == "text" and [c for c in own if c != "paper_id"] != table.wanted:
                return False
            if name == "text" and table.at != (
                own.index("paper_id") if "paper_id" in own else len(table.wanted)
            ):
                return False
            if name == "section" and not {"section_id", "header", "section_type"} <= set(own):
                return False
        _derived(paper)["doc"] = (_deps(paper), None, doc)
        return True

    def _finish(self) -> None:
        self.all = (1 << self.n) - 1
        body = 0
        for i, t in enumerate(self.section_type):
            if not (isinstance(t, str) and t == "references"):
                body |= 1 << i
        self.body = body

    # -- columns -----------------------------------------------------------------------

    def values(self, c: str) -> list[Any]:
        """Column *c*'s cells, as ``.tolist()`` gives them (``NA`` may be ``None``)."""
        vals = self._values.get(c)
        if vals is None:
            join = self._join
            if join is None:
                vals = self.frame()[c].tolist()
            elif c == "paper_id":
                vals = [self.paper_id] * self.n
            elif c == "header":
                vals = self.header
            elif c == "section_type":
                vals = self.section_type
            else:
                vals = join.text.column(c).tolist()
            self._values[c] = vals
        return vals

    def frame(self) -> pd.DataFrame:
        """The whole table, with its raw text (do not change it)."""
        frame = self._frame
        if frame is None:
            assert self._join is not None
            frame = self._frame = self._join.frame()
        return frame

    def base_frame(self) -> pd.DataFrame:
        """:meth:`frame` before the required columns it lacked were added."""
        return self._base if self._base is not None else self.frame()

    def take(self, idx: Sequence[int]) -> pd.DataFrame:
        """Rows *idx* of the table (raw text), index reset."""
        return self.frame().take(list(idx)).reset_index(drop=True)

    def take_series(self, c: str, idx: Sequence[int]) -> pd.Series:
        """Column *c* at rows *idx*, typed as in the table, index reset."""
        idx = list(idx)
        join = self._join
        if join is not None and self._frame is None:
            if c == "paper_id":
                return pd.Series([self.paper_id] * len(idx), dtype="string")
            if c not in ("header", "section_type"):
                return join.text.column(c).take(idx).reset_index(drop=True)
        return self.frame()[c].take(idx).reset_index(drop=True)

    def rows(self, idx: Sequence[int], stage: int = 1) -> pd.DataFrame:
        """Rows *idx* as a search returns them: the text cleaned *stage* times."""
        out = self.take(idx)
        out["text"] = pd.Series(self.stage_text(idx, stage), dtype="string")
        return out

    def stage_text(self, idx: Sequence[int], stage: int) -> list[str | None]:
        """The text of rows *idx*, cleaned *stage* times."""
        if stage == 1 and not self._stages:  # clean just these rows
            t = self.text
            return [clean(t[i]) for i in idx]
        field = self.field(stage)
        return [field[i] for i in idx]

    def distinct(self, idx: Sequence[int]) -> bool:
        """Whether rows *idx* have distinct, known ``(paper_id, text_id)`` (then no two
        of them can be identical rows)."""
        pids = self.values("paper_id")
        tids = self.values("text_id")
        keys = set()
        for i in idx:
            pid, tid = pids[i], tids[i]
            if is_missing(pid) or is_missing(tid):
                return False
            try:
                keys.add((pid, tid))
            except TypeError:  # an unhashable cell: let drop_duplicates() decide
                return False
        return len(keys) == len(idx)

    def unique(self) -> bool:
        """Whether no two rows of the table can be identical (V7)."""
        if self._uniq is None:
            self._uniq = self.distinct(range(self.n))
        return self._uniq

    def row_key(self, i: int, stage: int) -> tuple[Any, ...]:
        """Every cell of row *i* with its text at *stage* (what ``distinct()`` compares)."""
        field = self.field(stage)
        return tuple(
            field[i] if c == "text" else _hashable(_na(self.values(c)[i])) for c in self.columns
        )

    # -- fields ------------------------------------------------------------------------

    def field(self, stage: int) -> list[str | None]:
        """The text a search matches at *stage*: raw (0), or cleaned *stage* times."""
        if stage == 0:
            return self.text
        stages = self._stages
        if len(stages) >= stage:
            return stages[stage - 1]
        out = list(stages)
        while len(out) < stage:
            prev = out[-1] if out else self.text
            first = not out
            out.append([clean(s, first) for s in prev])
        self._stages = tuple(out)
        return out[stage - 1]

    def fold(self) -> list[str]:
        """The stage-0 text casefolded (what the literal prefilter scans)."""
        folded = self._fold
        if folded is None:
            folded = self._fold = ["" if s is None else fold(s) for s in self.text]
        return folded

    def _literal_rows(self, piece: str) -> int:
        """The rows whose folded text contains *piece* (memoised)."""
        hit = self._lit.get(piece)
        if hit is not None:
            return hit
        blob = self._blob
        if blob is None:
            folded = self.fold()
            starts = []
            pos = 0
            for s in folded:
                starts.append(pos)
                pos += len(s) + 1
            blob = self._blob = ("\0".join(folded), starts)
        text, starts = blob
        out = 0
        pos = text.find(piece)
        while pos != -1:
            row = bisect_right(starts, pos) - 1
            out |= 1 << row
            if row + 1 >= len(starts):
                break
            pos = text.find(piece, starts[row + 1])
        self._lit[piece] = out
        return out

    def candidates(self, p: Pat) -> int | None:
        """The rows that can match *p* at any stage (``None``: no literal is known).

        Pieces hold no white space and no comma, and cleaning only collapses white
        space, drops the space of ``" , "`` and turns markers into blank lines, so
        a piece in a cleaned text is in the raw text too.
        """
        cnf = p.literals()
        if not cnf:
            return None
        out = self.all
        for clause in cnf:
            rows = 0
            for piece in clause:
                rows |= self._literal_rows(piece)
            out &= rows
            if not out:
                break
        return out

    # -- masks -------------------------------------------------------------------------

    def mask(self, p: Pat, stage: int = 0, within: int | None = None) -> int:
        """The rows of *within* (default: the body) whose text at *stage* matches *p*."""
        # made first, as text_search() made it, though it may never run
        det = p.detector()
        want = self.body if within is None else within
        if not want:
            return 0
        key = (p, stage)
        known, true = self._state.get(key, (0, 0))
        todo = want & ~known
        if todo:
            ev = todo
            cand = self.candidates(p)
            if cand is not None:
                ev &= cand
            hits = 0
            if ev:
                texts = self.field(stage)
                if stage == 0 and det.folds:
                    folded = self.fold()
                    for i in bits(ev):
                        s = texts[i]
                        if s is not None and det(s, folded[i]):
                            hits |= 1 << i
                else:
                    for i in bits(ev):
                        s = texts[i]
                        if s is not None and det(s):
                            hits |= 1 << i
            now = self._state.get(key, (0, 0))
            self._state[key] = (now[0] | known | todo, now[1] | true | hits)
            true |= hits
        return true & want

    def header_mask(self, p: Pat, within: int) -> int:
        """The rows of *within* whose header matches *p* (each header tested once)."""
        rows = self._hdr.get(p)
        if rows is None:
            groups = self._hdr_groups
            if groups is None:
                groups = {}
                for i, h in enumerate(self.header):
                    if h is not None:
                        groups[h] = groups.get(h, 0) | (1 << i)
                self._hdr_groups = groups
            det = p.detector()
            rows = 0
            for h, r in groups.items():
                if det(h):
                    rows |= r
            self._hdr[p] = rows
        return rows & within

    def any(
        self,
        ps: Pat | PatternSet,
        stage: int = 0,
        within: int | None = None,
        header: bool = False,
        rank: dict[int, int] | None = None,
    ) -> int:
        """The rows matching any of *ps*, each pattern tried on the rows no earlier one
        matched; with *rank*, each row's first matching pattern is recorded."""
        rest = self.body if within is None else within
        out = 0
        for k, p in enumerate(as_patterns(ps)):
            # every pattern is given to mask(), which makes its detector even when
            # no row is left, as text_search() did
            m = self.mask(p, stage, rest)
            if header and rest:
                m |= self.header_mask(p, rest)
            if m:
                if rank is not None:
                    for i in bits(m):
                        rank[i] = k
                out |= m
                rest &= ~m
        return out

    # -- grouped views -----------------------------------------------------------------

    def groups(self, level: str, rows: Sequence[int], stage: int) -> GroupDoc:
        """The *level* groups of *rows* (the rows one call searched, in its table's
        order), joining their text at *stage*. Memoised."""
        from metacheck.core.groups import build_groups

        key = (level, stage, tuple(rows))
        hit = self._groups.get(key)
        if hit is None:
            hit = build_groups(self, level, rows, stage)
            count("group_builds")
            self._groups[key] = hit
        return hit

    def key_values(self, c: str) -> list[Any]:
        """Column *c* as group keys (``NA`` as ``None``)."""
        return [_key(v) for v in self.values(c)]


# -- the cache on the paper -------------------------------------------------------------


def _derived(paper: Paper) -> dict[Any, Any]:
    d = getattr(paper, "_derived", None)
    if d is None:  # a paper unpickled or copied with copy.copy()
        d = {}
        object.__setattr__(paper, "_derived", d)
    return d  # type: ignore[no-any-return]


_RAW = "raw"


def _dep(paper: Paper, name: str) -> tuple[str, Any]:
    """What table *name* comes from: its JSON records' token, or the table object."""
    if paper._raw_records(name) is not None:
        token = paper._lazy.get(name)
        return (_RAW, token) if token is not None else ("unknown", object())
    return ("table", paper._tables.get(name))


def _deps(paper: Paper) -> tuple[Any, ...]:
    return (paper.paper_id, _dep(paper, "text"), _dep(paper, "section"))


def _same_deps(a: tuple[Any, ...], b: tuple[Any, ...]) -> bool:
    if type(a[0]) is not type(b[0]) or a[0] != b[0]:
        return False
    return all(x[0] == y[0] and x[1] is y[1] for x, y in zip(a[1:], b[1:], strict=True))


def _all_raw(deps: tuple[Any, ...]) -> bool:
    """Whether text and section were JSON records (which no caller edits in place)."""
    return bool(deps[1][0] == _RAW and deps[2][0] == _RAW)


# -- Docs -------------------------------------------------------------------------------


class Docs:
    """The papers of an input as Docs: a paper gives one, a paper list one per paper."""

    __slots__ = ("docs", "papers")

    docs: tuple[Doc, ...]
    papers: tuple[Paper, ...]

    def __init__(self, papers: Sequence[Paper]) -> None:
        self.papers = tuple(papers)
        self.docs = tuple(Doc.of(p) for p in self.papers)

    @classmethod
    def of(cls, paper: Any) -> Docs:
        """The Docs of a paper or a paper list."""
        if isinstance(paper, Paper):
            return cls([paper])
        if isinstance(paper, PaperList) or (is_paper_list(paper) and not isinstance(paper, str)):
            return cls(list(paper.values()) if isinstance(paper, Mapping) else list(paper))
        raise TypeError("paper must be a paper or paperlist object.")

    @classmethod
    def wrap(cls, docs: Sequence[Doc], papers: Sequence[Paper]) -> Docs:
        """Docs already built (the grouped views of a chain) for *papers*."""
        out = cls.__new__(cls)
        out.papers = tuple(papers)
        out.docs = tuple(docs)
        return out

    def __len__(self) -> int:
        return len(self.docs)

    def __iter__(self) -> Iterator[Doc]:
        return iter(self.docs)

    def __getitem__(self, i: int) -> Doc:
        return self.docs[i]

    def template(self) -> pd.DataFrame | None:
        """The empty table of the whole list when its papers' columns differ (else ``None``).

        A paper list's table is the union of its papers' text columns in
        first-seen order, then ``header`` and ``section_type`` (when the merge
        added them), then the required columns ``text_search()`` adds, typed by
        the first paper that has each.
        """
        if len({tuple(d.columns) for d in self.docs}) <= 1:
            return None
        from metacheck._r.frames import bind_rows

        order: dict[str, None] = {}
        merged = False
        for d in self.docs:
            cols = list(d.base_frame().columns)
            if cols[-2:] == ["header", "section_type"]:
                cols, merged = cols[:-2], True
            order.update(dict.fromkeys(cols))
        if merged:
            order.update(dict.fromkeys(["header", "section_type"]))
        frame = bind_rows([d.base_frame().iloc[0:0] for d in self.docs])
        frame = frame.loc[:, [c for c in order if c in frame.columns]]
        for m in REQUIRED:
            if m not in frame.columns:
                frame[m] = pd.Series([], dtype=object)
        return frame.reset_index(drop=True)

    def hits(
        self, ps: Pat | PatternSet, *, include_refs: bool = False, header: bool = False
    ) -> Hits:
        """``text_search(paper, ps)``: the rows whose raw text (or header) matches *ps*."""
        from metacheck.core.hits import Hits

        return Hits.first(self, ps, include_refs=include_refs, header=header)
