"""Ethics Approval Check (port of ``inst/modules/ethics_check.R``).

The live-data helper the module relies on, ``.detect_live_data()``
(``R/text-extractors.R``), is ported as
:func:`metacheck.text.extract._detect_live_data`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

from metacheck._r.base import plural
from metacheck._r.frames import bind_rows
from metacheck._r.regex import grepl
from metacheck.module import module
from metacheck.papers.model import Paper, is_paper_list
from metacheck.text.search import text_search

__all__ = ["ethics_check"]

# patterns ----
# R string literals translated to regexes (``"\\birb\\b"`` is ``\birb\b``); all are
# TRE patterns searched case-insensitively by text_search().
_ETHICS_WORDS = (
    # committee name variants — human research
    r"institutional\s+review\s+board",
    r"independent\s+ethics\s+committee",
    r"ethical?\s+review\s+board",
    r"ethics\s+(advisory\s+|review\s+)?(committee|board|panel|sub-?committee)",
    r"ethical\s+(advisory\s+|review\s+)?(committee|board|panel|sub-?committee)",
    r"research\s+ethics\s+(committee|board|panel)",
    r"medical\s+ethics\s+committee",
    r"human\s+(subjects?|research)\s+(committee|review\s+board)",
    r"human\s+research\s+ethics\s+(advisory\s+)?(panel|committee|board)",
    r"committee\s+(for|on)\s+(the\s+)?protection\s+of\s+human\s+subjects",
    r"committee\s+on\s+(health\s+)?research\s+ethics",
    r"office\s+of\s+research\s+ethics",
    r"\birb\b",
    r"\biec\b",
    r"\b(rec|reb|erb|dec)\b.{0,30}\b(ethic|approv|committee|board|panel|institutional|protocol)",
    r"\b(ethic|approv|committee|board|panel|institutional|protocol).{0,30}\b(rec|reb|erb|dec)\b",
    r"\bmetc\b",
    r"medisch[- ]ethische\s+toetsingscommissie",
    r"comit[eé]\s+d.[ée]thique",
    # committee name variants — animal research
    r"\biacuc\b",
    r"\bawerb\b",
    r"animals?\s+(\w+\s+){0,4}(committee|board|panel)",
    r"(committee|board|panel)\s+(\w+\s+){0,4}animals?",
    r"dierexperimentencommissie",
    r"instantie\s+voor\s+dierenwelzijn",
    r"\bivd\b",
    r"tierversuchskommission",
    r"\btvk\b",
    r"comit[eé]\s+d.[ée]thique\s+(en\s+exp[eé]rimentation\s+animale|animale)",
    r"\bceea\b",
    # approval phrasing
    r"ethics\s+(approval|clearance)\s+(was\s+)?(granted|obtained|received|given|secured)",
    r"ethics\s+(approval|clearance)",
    r"ethical\s+(approval|clearance)\s+(was\s+)?(granted|obtained|received|given|secured)",
    r"ethical\s+(approval|clearance)",
    r"ethically\s+approved",
    r"ethical\s+approval\s+was\s+(granted|obtained|received|given|secured)",
    r"ethics\s+approval\s+was\s+(granted|obtained|received|given|secured)",
    r"received\s+ethical?\s+approval",
    r"approved\s+by\s+(the\s+)?(ethics|irb|institutional|independent|local|review|research)",
    r"approved\s+by\s+.{0,60}ethics\s+(committee|board|panel|sub-?committee)",
    r"approved\s+by\s+.{0,80}(ethics|research)\s+.{0,30}"
    r"(committee|board|panel|sub-?committee|council|office)",
    r"approved\s+by\s+.{0,80}(committee|board|panel|sub-?committee|council)\s+.{0,40}"
    r"(ethic|research)",
    r"(received|obtained)\s+(ethics|ethical)?\s*(approval|clearance)\s+from\s+.{0,80}"
    r"(ethics|irb|institutional|research|review)\s+.{0,30}"
    r"(committee|board|panel|sub-?committee|office)",
    r"this\s+study\s+(was\s+)?(approved|reviewed)\s+by",
    r"the\s+study\s+(was\s+)?(approved|reviewed)\s+by",
    r"the\s+protocol\s+(was\s+)?(approved|reviewed)\s+by",
    r"experiment\s+(was\s+)?(approved|reviewed)\s+by",
    r"procedures?\s+(was\s+|were\s+)?(approved|reviewed)\s+by",
    r"methods?\s+(was\s+|were\s+|reported\s+in\s+.{0,30})?approved\s+by",
    r"research\s+(was\s+)?(approved|reviewed)\s+by",
    r"study\s+was\s+ethically\s+approved",
    r"(followed|met|follow)\s+(the\s+)?ethical\s+(guidelines|standards|requirements)",
    r"in\s+accordance\s+with\s+.{0,40}ethical\s+(guidelines|standards)",
    r"ethics\s+protocol",
    r"approved\s+under\s+(protocol|reference|number)",
    # waiver / exemption
    r"ethics\s+(waiver|exemption)",
    r"ethical\s+(waiver|exemption)",
    r"\birb\s+(waiver|exemption|exempt)",
    r"exempt(ed)?\s+from\s+(ethics|irb|institutional\s+review)",
    r"waived?\s+(by\s+)?(the\s+)?(ethics|irb|institutional|review)\s+(committee|board|review)?",
    r"deemed\s+exempt",
    # Helsinki
    r"declaration\s+of\s+helsinki",
    r"helsinki\s+declaration",
)

# Not in R: a match of every pattern above contains one of these literals
# (case-insensitively), so sentences without any of them are dropped before the
# 63 searches; text_search() then returns exactly the same rows, in the same
# order. Keep this in sync when the patterns change (the pattern tests in
# tests/mod_ethics check every pattern against it).
_ETHICS_ANY = "|".join(
    (
        "ethic",
        "board",
        "committee",
        "panel",
        "institutional",
        "protocol",
        "approv",
        "clearance",
        "reviewed",
        "exempt",
        "waive",
        "helsinki",
        "irb",
        "iec",
        "metc",
        "toetsingscommissie",
        "thique",
        "iacuc",
        "awerb",
        "dierexperimentencommissie",
        "dierenwelzijn",
        "ivd",
        "tierversuchskommission",
        "tvk",
        "ceea",
    )
)

_REPORT_APPROVED = "An ethics approval statement was detected, based on the following text:\n\n> {}"
_REPORT_NONE = (
    "We did not detect an ethics approval statement, and this paper does not appear to "
    "involve live data collection from human or animal participants. Ethics approval is "
    "typically not required for theoretical work, simulations, reviews, meta-analyses, or "
    "secondary analyses of published data."
)
_REPORT_NEEDS_PRESENT = (
    "Based on the following text, we would expect an ethics approval statement, and it was "
    "present:\n\n> {}\n\nEthics approval statement:\n\n> {}"
)
_REPORT_NEEDS_MISSING = (
    "Based on the following text, we would expect an ethics approval statement, but it was "
    "not present:\n\n> {}"
)


def _search_frame(paper: Any) -> pd.DataFrame:
    """The sentence table ``text_search()`` builds from *paper*, built once.

    Both searches of the module (ethics statements and live data) run on it,
    so the paper's text and section tables are joined only once. *paper* may
    be a paper, a paper list (also a plain list of papers, as R's
    ``.is_paper_list()`` accepts) or a table. Raises R's errors for the
    inputs ``text_search(paper, ethics_words)`` rejects: another type (``The
    paper argument doesn't seem to be ...``) or a character vector, whose
    per-pattern results ``bind_rows()`` refuses.
    """
    from metacheck.text.search import _text_frame

    frame, is_vector = _text_frame(paper)
    if is_vector:
        text_search(paper, list(_ETHICS_WORDS))  # raises R's bind_rows() error
    return frame


def _may_mention_ethics(frame: pd.DataFrame) -> pd.DataFrame:
    """Rows of *frame* that can match one of :data:`_ETHICS_WORDS` (see ``_ETHICS_ANY``)."""
    if "text" not in frame.columns or len(frame) == 0:
        return frame
    keep = grepl(_ETHICS_ANY, frame["text"].tolist(), ignore_case=True)
    out: pd.DataFrame = frame.loc[keep]
    return out


def _paper_ids(paper: Any) -> list[str]:
    """The IDs of the papers, once each, in list order.

    metacheck uses ``paper_id(paper)`` (the ``info`` tables) as factor levels,
    so duplicated IDs stop the module ("factor level [2] is duplicated", U101),
    and a paper without an ``info`` row is left out of the summary while its
    sentences get an ``NA`` paper ID (U102). Here each paper contributes its
    ``info`` IDs, or its own ID when its ``info`` table is empty.
    """
    from metacheck.papers.tables import paper_id

    if isinstance(paper, Paper):
        papers: list[Any] = [paper]
    elif is_paper_list(paper):
        papers = list(paper.values()) if isinstance(paper, Mapping) else list(paper)
    else:
        return paper_id(paper)  # a table: "paper must be a paper or paperlist object."
    ids: list[str] = []
    for p in papers:
        own = paper_id(p)
        if not own and isinstance(p, Paper) and isinstance(p.paper_id, str):
            own = [p.paper_id]
        ids.extend(own)
    return list(dict.fromkeys(ids))


def _arrange(table: pd.DataFrame, paper_ids: Sequence[str]) -> pd.DataFrame:
    """``paper_id`` as ``factor(paper_id, paper_ids)``, ``arrange(paper_id, text_id)``, then
    ``as.character(paper_id)`` (ids that are not levels become ``NA``)."""
    n = len(table)
    level = {pid: i for i, pid in enumerate(paper_ids)}
    pids = table["paper_id"].tolist() if "paper_id" in table.columns else [None] * n
    codes = [level.get(p) if isinstance(p, str) else None for p in pids]
    keys = pd.DataFrame(
        {
            "paper_id": pd.array(codes, dtype="Int64"),
            "text_id": pd.array(
                [None if pd.isna(t) else t for t in table["text_id"].tolist()], dtype="Float64"
            )
            if "text_id" in table.columns
            else pd.array([None] * n, dtype="Float64"),
        }
    )
    order = keys.sort_values(
        ["paper_id", "text_id"], kind="stable", na_position="last"
    ).index.to_numpy()
    out = table.iloc[order].reset_index(drop=True)
    labels = [None if codes[i] is None else paper_ids[codes[i]] for i in order]
    out["paper_id"] = pd.Series(labels, dtype="string")
    return out


def _summarise(
    table: pd.DataFrame, flag: str, column: str
) -> dict[Any, tuple[bool, list[str] | None]]:
    """``summarise(any(flag), <column> = list(unique(text[flag])), .by = paper_id)``.

    Returns ``{paper_id: (any, statements)}`` in first-appearance order; empty
    statements are ``None`` (R's ``NA_character_``). R only summarises a table
    with rows (``if (nrow(table) > 0)``).
    """
    if len(table) == 0:
        return {}
    pids = [None if pd.isna(p) else p for p in table["paper_id"].tolist()]
    if "text" not in table.columns:
        # R: no `text` column in the data mask, so `text` is graphics::text(), and
        # subsetting it fails ("object of type 'closure' is not subsettable")
        raise TypeError(f"In argument: `{column} = list(unique(text[{flag}]))`.")
    groups: dict[Any, tuple[list[bool | None], list[Any]]] = {}
    flags = [bool(f) if not pd.isna(f) else None for f in table[flag].tolist()]
    texts = table["text"].tolist()
    for p, f, t in zip(pids, flags, texts, strict=True):
        fl, tx = groups.setdefault(p, ([], []))
        fl.append(f)
        if f:
            tx.append(None if pd.isna(t) else t)
    out: dict[Any, tuple[bool, list[str] | None]] = {}
    for p, (fl, tx) in groups.items():
        statements = list(dict.fromkeys(tx))
        out[p] = (any(f is True for f in fl), statements or None)
    return out


@module(
    title="Ethics Approval Check",
    description=(
        "This module searches for statements of ethics approval, IRB approval,\n"
        "institutional review board approval, and related terms."
    ),
    details="""
        Patterns are designed to capture:

        **Human research committee names**
        - `Institutional Review Board` / IRB (US standard)
        - `Independent Ethics Committee` / IEC (ICH-GCP international clinical trial term)
        - `Ethical/Ethics Review Board` (generic)
        - `Ethics (Advisory/Review) Committee/Board/Panel/Sub-committee`
        - `Research Ethics Committee/Board/Panel` (UK/Canada/Australia)
        - `Medical Ethics Committee`
        - `Human Subjects/Research Committee` / `Human Research Ethics (Advisory) Panel`
        - `Committee for/on the Protection of Human Subjects`
        - `Committee on (Health) Research Ethics`
        - `Office of Research Ethics`
        - Abbreviations: IEC, REC, REB, ERB, METC, DEC. REC/REB/ERB/DEC are short
          enough to collide with unrelated jargon (e.g. `REC` as a recognition-heuristic
          model abbreviation, `DEC` as a decision-task label, `Reb` as an author surname),
          so these four require an ethics/approval/committee/board/panel/institutional/
          protocol word within ~30 characters in the same sentence. IRB and IEC are kept
          unconstrained as they are not observed to collide in practice.
        - Dutch: `Medisch-ethische toetsingscommissie` (METC)
        - French: `Comité d'éthique` / `Comité d'Ethique`

        **Animal research committee names**
        - `Institutional Animal Care and Use Committee` / IACUC (US standard)
        - Any sentence containing `animal(s)` and `committee` (or `board`/`panel`) within ~5 words
          of each other — catches `Experimental Animal Care and Use Committee`,
          `Animal Ethics Committee`, `Animal Welfare Board`, `Animal Welfare Ethical Review Body`, etc.
        - UK: `Animal Welfare Ethical Review Body` / AWERB
        - Dutch: `Dierexperimentencommissie` / DEC (see DEC constraint above); `Instantie voor Dierenwelzijn` / IvD
        - German: `Tierversuchskommission` / TvK (advisory committee under §15 Tierschutzgesetz)
        - French: `Comité d'éthique en expérimentation animale` / CEEA; `Comité d'éthique animale`
        - Spanish: `Comité de ética en experimentación animal` / CEEA

        **Approval phrasing**
        - `Ethics/Ethical (approval|clearance) [was] (granted|obtained|received|given|secured)`
        - `Ethically approved`
        - `Approved by the (ethics|irb|institutional|independent|local|review|research) ...`
        - `This/The study/protocol/experiment/procedures/methods/research [was] (approved|reviewed) by`
        - `Study was ethically approved`
        - `(Followed|met|follow) the ethical (guidelines|standards|requirements)`
        - `In accordance with ... ethical (guidelines|standards)`
        - `Ethics protocol`
        - `Approved under (protocol|reference|number)`

        **Waiver / exemption**
        - `Ethics/Ethical (waiver|exemption)`
        - `IRB (waiver|exemption|exempt)`
        - `Exempted from (ethics|irb|institutional review)`
        - `Waived by the (ethics|irb|institutional|review) (committee|board)`
        - `Deemed exempt`

        **Declaration of Helsinki**
        - `Declaration of Helsinki` / `Helsinki Declaration`

        Patterns are NOT designed to capture:
        - General ethical considerations or ethical dilemmas in study content
        - Mentions of "ethics" in theoretical or philosophical contexts
        - Author approval of a manuscript (e.g. "all authors approved the final version")
        - Ethical behaviour as a measured variable (e.g. "ethical decision-making")
        - URLs containing "ethics" in path components

        Live data collection is detected by the internal helper `.detect_live_data()`,
        which searches for sentences indicating that data were collected directly from
        human or animal participants. Papers without any live-data signal are not
        flagged as missing ethics approval. The helper uses conservative patterns to
        avoid flagging secondary data analyses, computational experiments, or
        economics papers that analyse existing survey data. See `.detect_live_data()`
        for the full list of patterns and intentional exclusions.
    """,
    keywords=["general"],
    params={"paper": "a paper object or paperlist object"},
)
def ethics_check(paper: Any) -> dict[str, Any]:
    """Port of ``inst/modules/ethics_check.R::ethics_check()``.

    Searches *paper* (a paper or paper list) for ethics approval, IRB,
    waiver/exemption and Declaration of Helsinki statements, and for
    sentences indicating live data collection from human or animal
    participants (``.detect_live_data()``). Returns the matching sentences
    (``table``, flagged ``ethics`` or ``live_data``), a per-paper summary
    (``summary_table``), the traffic light (``na``: no paper collected live
    data; ``green``: every such paper has an ethics statement; ``red``: some
    lack one), a summary text and, for a single paper, the report.

    Like R, *paper* may also be a plain list of papers. Papers with duplicated
    IDs are summarised together, a paper without an ``info`` row is summarised
    under its own ID, and a text table without a ``text`` column has no
    sentences (metacheck stops or mislabels these, U101/U102). An empty paper
    list gives an empty result (metacheck stops, U79). Reproduces R's errors
    for a table or a character vector instead of a paper.
    """
    from metacheck.text.extract import _detect_live_data

    # R runs text_search(paper, ethics_words) first, which rejects anything but a
    # paper, a paper list (a plain list of papers too) or a table
    frame = _search_frame(paper)
    if "text" not in frame.columns:
        # a text table without a `text` column has no sentences to search
        # (metacheck searches its first column instead and fails, U102)
        frame = frame.iloc[0:0]
    # a table fails here: "paper must be a paper or paperlist object."
    # An empty paper list has no IDs and gets a summary table without rows
    # (metacheck stops: data.frame(paper_id = NULL) has no column to join by, U79)
    paper_ids = _paper_ids(paper)

    table = text_search(_may_mention_ethics(frame), list(_ETHICS_WORDS))
    table["ethics"] = pd.Series([True] * len(table), index=table.index, dtype="boolean")
    if "text" not in table.columns:
        table["text"] = pd.Series([], dtype="string")

    live_table = _detect_live_data(frame).copy()
    live_table["live_data"] = pd.Series(
        [True] * len(live_table), index=live_table.index, dtype="boolean"
    )

    # re-order by paper_id and text_id
    table = _arrange(table, paper_ids)
    live_table = _arrange(live_table, paper_ids)

    # summary_table ----
    ethics_summary = _summarise(table, "ethics", "ethics_statements")
    live_summary = _summarise(live_table, "live_data", "live_data_statements")
    na: tuple[bool, None] = (False, None)
    ethics_rows = [ethics_summary.get(p, na) for p in paper_ids]
    live_rows = [live_summary.get(p, na) for p in paper_ids]
    summary_table = pd.DataFrame(
        {
            "paper_id": pd.Series(paper_ids, dtype="string"),
            "ethics_approved": pd.Series([r[0] for r in ethics_rows], dtype="boolean"),
            "ethics_statements": pd.Series([r[1] for r in ethics_rows], dtype=object),
            "needs_ethics": pd.Series([r[0] for r in live_rows], dtype="boolean"),
            "live_data_statements": pd.Series([r[1] for r in live_rows], dtype=object),
        }
    )

    # traffic_light + summary_text ----
    needs = [r[0] for r in live_rows]
    approved = [r[0] for r in ethics_rows]
    n_needs = sum(needs)
    n_missing = sum(n and not a for n, a in zip(needs, approved, strict=True))
    if n_needs == 0:
        tl = "na"
    elif n_missing == 0:
        tl = "green"
    else:
        tl = "red"
    n_papers = len(summary_table)
    if n_missing > 0:
        summary_text = (
            f"{n_missing:d} of {n_papers:d} paper{plural(n_papers)} appeared to involve live "
            "data collection and lacked an ethics approval statement."
        )
    elif n_needs > 0:
        summary_text = (
            "All papers that appeared to involve live data collection contained an ethics "
            "approval statement."
        )
    else:
        summary_text = (
            "No papers appeared to involve live data collection requiring an ethics approval "
            "statement."
        )

    # report ----
    report: str | None = None
    if n_papers == 1:
        # each statement once (metacheck quotes `table$text[table$ethics]`, every
        # row, so a sentence found at several text_ids was repeated, U102)
        statements = ethics_rows[0][1] or []
        ethics_quote = "\n\n> ".join(_as_text(t) for t in statements)
        if not needs[0]:
            report = _REPORT_APPROVED.format(ethics_quote) if approved[0] else _REPORT_NONE
        else:
            found = live_rows[0][1]
            live_statements: list[str | None] = [None] if found is None else list(found)
            live_quote = "\n\n> ".join(_as_text(t) for t in live_statements)
            if approved[0]:
                report = _REPORT_NEEDS_PRESENT.format(live_quote, ethics_quote)
            else:
                report = _REPORT_NEEDS_MISSING.format(live_quote)

    # return a list ----
    return {
        "table": bind_rows([table, live_table]),
        "summary_table": summary_table,
        "na_replace": {"ethics_approved": False},
        "traffic_light": tl,
        "summary_text": summary_text,
        "report": report,
    }


def _as_text(x: Any) -> str:
    """``paste()`` of one element: ``NA`` is ``"NA"``."""
    return "NA" if x is None or pd.isna(x) else str(x)
