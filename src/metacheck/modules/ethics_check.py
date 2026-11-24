"""Ethics Approval Check (port of ``inst/modules/ethics_check.R``).

Whether a statement is expected is decided from where the paper's data came
from, ``.data_source()`` (``R/data-source.R``), ported as
:func:`metacheck.modules._data_source._data_source` (metacheck 0.4.4.37; before,
a search for live data collection, ``.detect_live_data()``).
"""

from __future__ import annotations

import warnings
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

from metacheck._r import grepl
from metacheck._r.base import plural
from metacheck._r.frames import bind_rows
from metacheck.core.doc import Doc
from metacheck.core.errors import PytacheckWarning
from metacheck.module import module
from metacheck.modules._data_source import _data_source
from metacheck.modules._paper_type import NO_LIVE_DATA, skipped_type, type_name
from metacheck.papers.ids import resolve
from metacheck.papers.model import Paper, PaperList, is_paper_list
from metacheck.text.search import search_doc, text_search

__all__ = ["ethics_check"]

# patterns ----
# R string literals translated to regexes (``"\\birb\\b"`` is ``\birb\b``); all are
# TRE patterns searched case-insensitively by text_search().
_ETHICS_WORDS = (
    # committee name variants — human research
    r"(institutional|internal)\s+review\s+boards?",
    r"independent\s+ethics\s+committee",
    r"ethical?\s+review\s+boards?",
    r"ethics\s+(advisory\s+|review\s+)?"
    r"(committees?|boards?|panels?|sub-?committees?|commission)",
    r"ethical\s+(advisory\s+|review\s+)?"
    r"(committees?|boards?|panels?|sub-?committees?|commission)",
    r"research\s+ethics\s+(committees?|boards?|panels?)",
    r"medical\s+ethics\s+committee",
    r"bioethic(s|al)?\s+(committee|commission|board)",
    r"ethik-?kommission",
    r"ethic(s|al)\s+review\s+(authority|unit|office|agency)",
    r"human\s+(subjects?|research)\s+(committee|review\s+board)",
    r"human\s+research\s+ethics\s+(advisory\s+)?(panel|committee|board)",
    r"human\s+(subjects?|research)\s+protections?\s+(office|program|programme|committee)",
    r"office\s+(for|of)\s+(the\s+)?(protection\s+of\s+)?human\s+(subjects?|research)"
    r"(\s+protections?)?",
    r"committee\s+(for|on)\s+(the\s+)?protection\s+of\s+human\s+subjects",
    r"committees?\s+(for|on)\s+(\w+\s+){0,4}research\s+ethics",
    r"office\s+of\s+research\s+ethics",
    r"\birb\b",
    # not a technical standard ("IEC 62471")
    r"\biec\b($|[^ 0-9-]| +[^ 0-9])",
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
    r"(committees?|boards?|panels?|sub-?committees?|council|office|commission)",
    r"approved\s+by\s+.{0,80}(committees?|boards?|panels?|sub-?committees?|council|commission)"
    r"\s+.{0,40}(ethic|research)",
    r"(received|obtained)\s+(ethics|ethical)?\s*(approval|clearance)\s+from\s+.{0,80}"
    r"(ethics|irb|institutional|research|review)\s+.{0,30}"
    r"(committees?|boards?|panels?|sub-?committees?|office|commission)",
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
    # ethics review that was not needed ("did not require further ethical
    # review", "exempt from formal human research ethics review")
    r"(ethics|ethical|irb|institutional)\s+review\s+(was|is)\s+"
    r"(not\s+(required|needed|necessary|sought)|waived)",
    r"not\s+(require|requiring|required|need|needed)\s+(an?\s+)?"
    r"(further\s+|formal\s+|additional\s+|separate\s+)?(ethics|ethical|irb)\s+"
    r"(review|approval|clearance)",
    r"exempt(ed|ion)?\s+from\s+(formal\s+|further\s+)?(\w+\s+){0,3}(ethics|ethical|irb)\s+review",
    r"\bno\s+(new\s+|further\s+)?(institutional\s+)?ethics\s+review",
    r"scope\s+of\s+.{0,40}ethical\s+review",
    # Helsinki
    r"declaration\s+of\s+helsinki",
    r"helsinki\s+declaration",
)

# a template line that is not a statement ("[Add ethics approval, recruitment, and stimulus
# details here.]"), searched with perl = TRUE
_PLACEHOLDER = r"\[\s*(add|insert)\b[^]]*(ethic|approv|irb|committee|board)"


def _search_doc(paper: Any) -> Doc:
    """The Doc both searches of the module (ethics statements and live data) run on.

    It is made once, so the paper's text and section tables are joined and
    indexed once, and a literal is looked for once, whichever search asks. *paper*
    may be a paper (its cached Doc), a paper list (also a plain list of papers,
    as R's ``.is_paper_list()`` accepts) or a table. Raises R's errors for the
    inputs ``text_search(paper, ethics_words)`` rejects: another type (``The
    paper argument doesn't seem to be ...``) or a character vector, whose
    per-pattern results ``bind_rows()`` refuses.
    """
    doc, is_vector = search_doc(paper)
    if is_vector:
        text_search(paper, list(_ETHICS_WORDS))  # raises R's bind_rows() error
    if "text" in doc.missing:
        # a text table without a `text` column has no sentences to search
        # (metacheck searches its first column instead and fails, U102)
        doc = Doc.from_frame(doc.base_frame().iloc[0:0])
    return doc


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
    # the sentences carry the resolved IDs (U208), so the summary rows must too; the
    # search that built them has already warned
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", PytacheckWarning)
        papers = list(resolve(papers))
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
        - `Institutional Review Board` / IRB (US standard), also `Internal Review Board`
        - `Independent Ethics Committee` / IEC (ICH-GCP international clinical trial
          term); not `IEC` followed by a number, which is a technical standard
        - `Ethical/Ethics Review Board` (generic)
        - `Ethics (Advisory/Review) Committee/Board/Panel/Sub-committee/Commission`,
          in the singular or plural
        - `Research Ethics Committee/Board/Panel` (UK/Canada/Australia)
        - `Medical Ethics Committee`, `Bioethics Committee/Commission`, `Ethikkommission`
        - `Ethical Review Authority/Unit/Office/Agency`
        - `Human Subjects/Research Committee` / `Human Research Ethics (Advisory) Panel`
        - `Human Research Protection Office/Program`, `Office for Human Subjects Protection`
        - `Committee for/on the Protection of Human Subjects`
        - `Committee(s) on/for (Medical and Health) Research Ethics`
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
        - Ethics review that was not needed: `Ethics review was not required`,
          `did not require further ethical review`, `exempt from formal human
          research ethics review`, `no new institutional ethics review`

        A template line the authors have not filled in ("[Add ethics approval ...
        here.]") is not a statement.

        **Declaration of Helsinki**
        - `Declaration of Helsinki` / `Helsinki Declaration`

        Patterns are NOT designed to capture:
        - General ethical considerations or ethical dilemmas in study content
        - Mentions of "ethics" in theoretical or philosophical contexts
        - Author approval of a manuscript (e.g. "all authors approved the final version")
        - Ethical behaviour as a measured variable (e.g. "ethical decision-making")
        - URLs containing "ethics" in path components

        **When a statement is expected**

        The module sorts each paper by where its data came from (the internal
        `.data_source()`; the signs and how the rule was tested are described in
        `R/data-source.R`):
        - `own`: the authors collected data from people or animals, for this paper,
          in an earlier study of their own, or (a protocol) will collect them;
        - `existing_records`: records about individual people that the authors
          obtained for this study, such as hospital records or a cohort;
        - `existing_unclear`: existing data about people from a source the module
          could not identify;
        - `open_resource`: an open resource any researcher can use, such as UK
          Biobank or NHANES;
        - `none`: no data about people or animals (a review, simulation,
          commentary, theory, tutorial, coding of published papers, or figures
          about groups);
        - `uncertain`: the signs were missing or pointed both ways;
        - `no_text`: the paper has no text.

        A statement is expected for `own`, `existing_records` and
        `existing_unclear`. A paper without a statement in these classes, or in
        `uncertain`, is flagged. The module reads the text, and the results of
        `power`, `stat_p_exact`, `stat_check`, `repo_check`, `data_check` and
        `prereg_check` when they ran before it (run it after them); without
        `stat_p_exact`, it finds the p-values in the text.

        On 199 PsyArXiv and medRxiv preprints labelled after the rule was fixed,
        the module decided right whether a statement is needed for 86.9% (the
        earlier live-data search: 83.4%). Its main errors: other people's studies
        described in the words of a new study, records and specimens described
        only as counts, and open resources the paper does not call open.
    """,
    keywords=["general"],
    params={"paper": "a paper object or paperlist object"},
)
def ethics_check(paper: Any) -> dict[str, Any]:
    """Port of ``inst/modules/ethics_check.R::ethics_check()``.

    Searches *paper* (a paper or paper list) for ethics approval, IRB,
    waiver/exemption and Declaration of Helsinki statements, and sorts it by where its
    data came from (``.data_source()``) to decide whether a statement is expected.
    Returns the matching sentences (``table``, flagged ``ethics``, and the sentences
    behind the data source, flagged ``live_data`` when they show the authors' own data
    collection), a per-paper summary (``summary_table``), the traffic light (``na``: no
    paper needs a statement; ``green``: every paper that does has one; ``yellow``: some
    lack one), a summary text and, for a single paper, the report.

    Like R, *paper* may also be a plain list of papers. Papers with duplicated
    IDs are renamed (``ID~2``, U208) and summarised one by one, a paper without
    an ``info`` row is summarised under its own ID, and a text table without a
    ``text`` column has no sentences (metacheck stops or mislabels these,
    U101/U102). An empty paper list gives an empty result. Reproduces R's errors
    for a table or a character vector instead of a paper.
    """
    from metacheck.module import get_prev_outputs

    # R runs text_search(paper, ethics_words) first, which rejects anything but a
    # paper, a paper list (a plain list of papers too) or a table
    doc = _search_doc(paper)
    # a table fails here: "paper must be a paper or paperlist object."
    # An empty paper list has no IDs and gets a summary table without rows
    paper_ids = _paper_ids(paper)

    table = text_search(doc, list(_ETHICS_WORDS))
    # a template line the authors have not filled in yet ("[Add ethics approval, recruitment,
    # and stimulus details here.]") is not a statement; an anonymised one ("approved by the XXX
    # University ethics committee") is, and so is one followed by a layout note
    # ("[INSERT TABLE 1 HERE]")
    if len(table) > 0:
        placeholder = grepl(_PLACEHOLDER, table["text"].tolist(), ignore_case=True, perl=True)
        table = table.loc[[not x for x in placeholder]].reset_index(drop=True)
    table["ethics"] = pd.Series([True] * len(table), index=table.index, dtype="boolean")
    if "text" not in table.columns:
        table["text"] = pd.Series([], dtype="string")
    # D84: a paper the expressions found no statement in gets bibr's statement
    table = _with_bibr_ethics(table, paper)

    # where the data came from ----
    # read from the text, and from the results of the modules that ran before this one (see
    # _data_source())
    prev: dict[str, dict[str, Any]] = {}
    for m in ("power", "stat_p_exact", "stat_check", "repo_check", "data_check", "prereg_check"):
        if get_prev_outputs(m, "traffic_light") is None:
            continue
        prev[m] = {
            "table": get_prev_outputs(m, "table"),
            "ownership": get_prev_outputs(m, "ownership"),
        }
    # D85: a paper bibr classifies as a review, commentary, erratum, ... collects no data from
    # participants, whatever its sentences about participants say
    skipped = _skipped_types(paper)
    ds = _data_source(
        text_search(doc),
        paper_ids,
        info=_titles(paper, paper_ids),
        prev=prev,
        paper=paper,
        forced_none={p: type_name(k) for p, k in skipped.items() if p in paper_ids},
    )
    source_table = ds["evidence"]
    # the sentences that show the authors' own data collection, under the name the run scripts
    # of the preprint repositories count (totals.py)
    source_table["live_data"] = pd.Series(
        [x == "own" for x in source_table["sign"].tolist()],
        index=source_table.index,
        dtype="boolean",
    )

    # re-order by paper_id and text_id
    table = _arrange(table, paper_ids)

    # summary_table ----
    ethics_summary = _summarise(table, "ethics", "ethics_statements")
    na: tuple[bool, None] = (False, None)
    ethics_rows = [ethics_summary.get(p, na) for p in paper_ids]
    source_summary = ds["summary"].set_index("paper_id")
    src_text = source_table["text"].tolist() if "text" in source_table.columns else []
    src_pid = source_table["paper_id"].tolist() if "paper_id" in source_table.columns else []
    statements_of: list[Any] = []
    for p in paper_ids:
        found = list(dict.fromkeys(t for t, q in zip(src_text, src_pid, strict=True) if q == p))
        statements_of.append(found or None)
    summary_table = pd.DataFrame(
        {
            "paper_id": pd.Series(paper_ids, dtype="string"),
            "ethics_approved": pd.Series([r[0] for r in ethics_rows], dtype="boolean"),
            "ethics_statements": pd.Series([r[1] for r in ethics_rows], dtype=object),
            "data_source": pd.Series(
                [source_summary.at[p, "data_source"] for p in paper_ids], dtype="string"
            ),
            "needs_ethics": pd.Series(
                [source_summary.at[p, "needs_ethics"] for p in paper_ids], dtype="boolean"
            ),
            "data_source_signs": pd.Series(
                [source_summary.at[p, "signs"] for p in paper_ids], dtype="string"
            ),
            "data_source_statements": pd.Series(statements_of, dtype=object),
        }
    )

    # traffic_light + summary_text ----
    # A statement is expected for the authors' own data and for records obtained for the study;
    # a paper whose data source is uncertain is flagged too, as we could not rule out that it
    # needs one.
    needs = [x is True for x in summary_table["needs_ethics"].tolist()]
    uncertain = [x == "uncertain" for x in summary_table["data_source"].tolist()]
    approved = [r[0] for r in ethics_rows]
    n_check = sum(n or u for n, u in zip(needs, uncertain, strict=True))
    n_missing = sum(n and not a for n, a in zip(needs, approved, strict=True))
    n_unsure = sum(u and not a for u, a in zip(uncertain, approved, strict=True))
    if n_missing + n_unsure > 0:
        # yellow, not red, until a validation shows people agree with most of its red findings
        # (#695, point 2)
        tl = "yellow"
    elif n_check > 0:
        tl = "green"
    else:
        tl = "na"
    n_papers = len(summary_table)
    if n_missing + n_unsure > 0:
        parts_text = []
        if n_missing > 0:
            parts_text.append(
                f"{n_missing:d} of {n_papers:d} paper{plural(n_papers)} appeared to report data "
                "about people or animals that need an ethics statement, and lacked one."
            )
        if n_unsure > 0:
            parts_text.append(
                f"For {n_unsure:d} of {n_papers:d} paper{plural(n_papers)} we could not tell "
                f"whether {'it reports' if n_unsure == 1 else 'they report'} data about people "
                "or animals, and found no ethics statement."
            )
        summary_text = " ".join(parts_text)
    elif n_check > 0:
        summary_text = (
            "All papers that appeared to report data about people or animals that need an "
            "ethics statement contained one."
        )
    else:
        summary_text = (
            "No papers appeared to report data about people or animals that need an ethics "
            "statement."
        )
    if n_papers == 1 and paper_ids[0] in skipped:
        summary_text = (
            f"bibr classifies the paper as {type_name(skipped[paper_ids[0]])}, which collects "
            "no data from participants, so it needs no ethics approval statement."
        )

    # report ----
    report: str | None = None
    if n_papers == 1:
        report = _report(
            src=str(summary_table["data_source"].iloc[0]),
            is_approved=bool(approved[0]),
            statements=[_as_text(t) for t in (ethics_rows[0][1] or [])],
            evidence=[_as_text(t) for t in src_text],
            signs=str(summary_table["data_source_signs"].iloc[0]),
        )

    # return a list ----
    return {
        "table": bind_rows([table, source_table]),
        "summary_table": summary_table,
        "na_replace": {"ethics_approved": False},
        "traffic_light": tl,
        "summary_text": summary_text,
        "report": report,
    }


def _quote(x: Sequence[str], max_: int | None = 3) -> str:
    """``paste0("> ", head(unique(x), max), collapse = "\n\n")``."""
    items = list(dict.fromkeys(x))
    if max_ is not None:
        items = items[:max_]
    return "\n\n".join(f"> {t}" for t in items)


def _report(
    src: str, is_approved: bool, statements: Sequence[str], evidence: Sequence[str], signs: str
) -> str:
    """The report of a single paper: what its data source says, the sentences it rests on and
    the statement found."""
    main: str
    if src == "own":
        main = (
            "The paper appears to report data that the authors collected from people or animals, "
            "and it has an ethics statement."
            if is_approved
            else "The paper appears to report data that the authors collected from people or "
            "animals, but we found no ethics statement. Such data need one: approval by an "
            "ethics committee, or a reason why none was needed. We based this on the sentences "
            "below. A paper can describe someone else's study in the same words; if these data "
            "are not the authors' own, no statement is needed."
        )
    elif src == "existing_records":
        main = (
            "The paper appears to analyse records about individual people that the authors "
            "obtained for this study, and it has an ethics statement."
            if is_approved
            else "The paper appears to analyse records about individual people that the authors "
            "obtained for this study, such as hospital records or a cohort, but we found no "
            "ethics statement. Such analyses usually need an approval or a waiver from an "
            "ethics committee."
        )
    elif src == "existing_unclear":
        main = (
            "The paper appears to analyse data about people, from a source we could not "
            "identify, and it has an ethics statement."
            if is_approved
            else "The paper appears to analyse data about people, but we could not tell where "
            "the data came from. If the authors collected them, or obtained records for this "
            "study, an ethics statement is needed. If they come from an open resource such as "
            "UK Biobank or NHANES, it is not. We found no ethics statement."
        )
    elif src == "open_resource":
        main = (
            "The paper appears to analyse an open resource, which needs no ethics statement "
            "of its own."
        )
    elif src == "none":
        basis = (
            f"we found: {signs}"
            if signs
            else "we found no method part, no sample description and no statistical results, "
            "and at most one sign that the authors collected data"
        )
        main = (
            f"The paper does not appear to report data about individual people or animals "
            f"({basis}), so we do not expect an ethics statement."
        )
    elif src == "uncertain":
        main = (
            "We could not tell whether this paper reports data about people or animals, but it "
            "has an ethics statement."
            if is_approved
            else "We could not tell whether this paper reports data about people or animals: the "
            "signs we look for were missing or pointed both ways. If it does, it needs an ethics "
            "statement, and we found none."
        )
    else:  # no_text
        main = "The paper has too little text to check."
    parts = [main]
    if len(evidence) > 0:
        parts.append(f"We based this on:\n\n{_quote(evidence)}")
    elif signs and src != "none":
        parts.append(f"We based this on: {signs}.")
    if is_approved:
        parts.append(f"Ethics statement:\n\n{_quote(statements, None)}")
    return "\n\n".join(parts)


def _titles(paper: Any, ids: Sequence[str]) -> pd.DataFrame | None:
    """A table with ``paper_id`` and ``title`` for the papers (the ``info`` tables, under the
    IDs the module uses), or ``None`` for something that is not a paper or a paper list."""
    if isinstance(paper, Paper):
        papers: list[Any] = [paper]
    elif is_paper_list(paper):
        papers = list(paper.values()) if isinstance(paper, Mapping) else list(paper)
    else:
        return None
    from metacheck.papers.tables import paper_id

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", PytacheckWarning)
        papers = list(resolve(papers))
    rows: dict[str, Any] = {}
    for p in papers:
        own = paper_id(p)
        if not own and isinstance(p, Paper) and isinstance(p.paper_id, str):
            own = [p.paper_id]
        if own and own[0] in ids:
            rows.setdefault(own[0], p.title if isinstance(p, Paper) else None)
    return pd.DataFrame(
        {"paper_id": list(rows), "title": pd.Series(list(rows.values()), dtype="string")}
    )


def _with_bibr_ethics(table: pd.DataFrame, paper: Any) -> pd.DataFrame:
    """*table* with a row (no ``text_id``) for each paper it lacks whose bibr
    ``info.ethics_statement`` exists (D84)."""
    from metacheck.modules._statements import bibr_statements

    found = bibr_statements(paper, "ethics_statement")
    have = set(table["paper_id"].dropna().astype(str)) if "paper_id" in table.columns else set()
    extra = {pid: text for pid, text in found.items() if pid not in have}
    if not extra:
        return table
    rows = pd.DataFrame(
        {
            "text": pd.Series(list(extra.values()), dtype="string"),
            "paper_id": pd.Series(list(extra), dtype="string"),
            "ethics": pd.Series([True] * len(extra), dtype="boolean"),
        }
    )
    return bind_rows([table, rows]).reset_index(drop=True)


def _skipped_types(paper: Any) -> dict[str, str]:
    """The papers bibr confidently classifies as a type without live data (D85), by ID."""
    papers = (
        [paper] if isinstance(paper, Paper) else list(paper) if isinstance(paper, PaperList) else []
    )
    out: dict[str, str] = {}
    for p in papers:
        kind = skipped_type(p, NO_LIVE_DATA)
        if kind is not None:
            out[str(p.paper_id)] = kind
    return out


def _as_text(x: Any) -> str:
    """``paste()`` of one element: ``NA`` is ``"NA"``."""
    return "NA" if x is None or pd.isna(x) else str(x)
