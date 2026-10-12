"""Figure and Table References: every figure and table is referred to in the text.

A pytacheck module (metacheck has no counterpart). It reads bibr's cross-reference
table first and falls back to searching the text for the float's label.
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from metacheck.module import module
from metacheck.papers.model import Paper

_KINDS = (("figure", "figure_id", "Figure"), ("table", "table_id", "Table"))
# how the text names a figure or table: "Figure 2", "Fig. 2a", "Figs. 1 and 2", "Table S1"
_WORDS = {"figure": r"fig(?:ure)?s?\.?", "table": r"tables?"}


def _floats(paper: Paper) -> pd.DataFrame:
    """One row per figure and table: ``kind``, ``float_id``, ``label``, its caption's
    ``caption_text_id`` and ``caption``."""
    rows: list[dict[str, Any]] = []
    for kind, id_col, _ in _KINDS:
        t = paper.get(kind)
        if not isinstance(t, pd.DataFrame) or not len(t) or id_col not in t.columns:
            continue
        for r in t.to_dict("records"):
            label = r.get("label")
            rows.append(
                {
                    "kind": kind,
                    "float_id": r[id_col],
                    "label": None if label is None or pd.isna(label) else str(label),
                    "caption_text_id": r.get("text_id"),
                    "caption": r.get("caption"),
                }
            )
    return pd.DataFrame(rows, columns=["kind", "float_id", "label", "caption_text_id", "caption"])


def _xref_counts(paper: Paper) -> dict[tuple[str, Any], list[Any]]:
    """The sentences (``text_id``) that refer to each float, from the ``xref`` table."""
    xref = paper.get("xref")
    out: dict[tuple[str, Any], list[Any]] = {}
    if not isinstance(xref, pd.DataFrame) or "target_id" not in xref.columns:
        return out  # an older export: references are not resolved to their target
    for r in xref.to_dict("records"):
        kind, target = r.get("xref_type"), r.get("target_id")
        if kind in ("figure", "table") and target is not None and not pd.isna(target):
            out.setdefault((kind, target), []).append(r.get("text_id"))
    return out


def _mention(kind: str, label: str) -> re.Pattern[str]:
    """A mention of the float in running text: its word, then its label as a whole token,
    alone or in a list or range ("Figures 1 and 2", "Tables 2-4" mention 2)."""
    lab = re.escape(label)
    return re.compile(
        rf"\b{_WORDS[kind]}\s*(?:[\w.]+\s*(?:,|and|&|-|–|to)\s*)*{lab}(?!\d)(?![.,]\d)",
        re.IGNORECASE,
    )


def _text_mentions(sentences: pd.DataFrame, kind: str, label: str, skip: set[Any]) -> list[Any]:
    """The sentences outside the captions (*skip*) that mention the float by its label."""
    pattern = _mention(kind, label)
    return [
        tid
        for tid, text in zip(sentences["text_id"], sentences["text"], strict=True)
        if tid not in skip and isinstance(text, str) and pattern.search(text)
    ]


def _paper_rows(paper: Paper) -> pd.DataFrame:
    from metacheck.text.search import text_search

    floats = _floats(paper)
    if not len(floats):
        return floats.assign(
            references=pd.Series([], dtype="Int64"),
            first_text_id=pd.Series([], dtype="Int64"),
            found_by=pd.Series([], dtype="string"),
        )
    xrefs = _xref_counts(paper)
    sentences = text_search(paper)
    captions = {c for c in floats["caption_text_id"] if c is not None and not pd.isna(c)}
    first: list[Any] = []
    found_by: list[str | None] = []
    refs: list[int | None] = []
    for kind, float_id, label in zip(
        floats["kind"], floats["float_id"], floats["label"], strict=True
    ):
        hits = xrefs.get((str(kind), float_id), [])
        how: str | None = "xref" if hits else None
        if not hits and isinstance(label, str):
            # cascade: the reference was not linked to its target, so look for the label
            hits = _text_mentions(sentences, str(kind), label, captions)
            how = "text" if hits else None
        known = [h for h in hits if h is not None and not pd.isna(h)]
        # without a reference or a label (an older export), it cannot be decided
        refs.append(len(hits) if hits or isinstance(label, str) else None)
        first.append(min(known) if known else None)
        found_by.append(how)
    out = floats.assign(
        references=pd.Series(refs, dtype="Int64"),
        first_text_id=pd.Series(first, dtype="Int64"),
        found_by=pd.Series(found_by, dtype="string"),
    )
    # a converter may list a figure twice (the TEI of a PDF): one row per kind and label
    labelled = out["label"].notna()
    dup = labelled & out.duplicated(["kind", "label"], keep=False)
    if dup.any():
        keep = ~(labelled & out.duplicated(["kind", "label"], keep="first"))
        for _, group in out.loc[dup].groupby(["kind", "label"], sort=False):
            i = group.index[0]
            out.loc[i, "references"] = int(group["references"].max())
            out.loc[i, "first_text_id"] = group["first_text_id"].min()
            out.loc[i, "found_by"] = (
                "xref"
                if (group["found_by"] == "xref").any()
                else (
                    group["found_by"].dropna().iloc[0] if group["found_by"].notna().any() else pd.NA
                )
            )
        out = out.loc[keep].reset_index(drop=True)
    return out


def _name(kind: str, label: str) -> str:
    word = {k: w for k, _, w in _KINDS}[kind]
    return label if label.lower().startswith(word.lower()[:3]) else f"{word} {label}"


@module(
    title="Figure and Table References",
    description="Check that every figure and table is referred to in the text.",
    details="""
        Lists the figures and tables of the paper and the sentences that refer to
        them. References are read from the cross-references bibr links to each
        figure and table; when a figure or table has none, the text is searched for
        its label ("Figure 2", "Fig. 2a", "Tables 1 and 2"), outside the captions.
        A figure or table that is never referred to is flagged.
    """,
    keywords=["general"],
    author=["Jakub Werner"],
    params={"paper": "a paper object or paperlist object"},
)
def figure_table_refs(paper: Any) -> dict[str, Any]:
    """Check that each figure and table of *paper* (a paper or paper list) is referred to.

    ``table`` has one row per figure and table: ``kind``, ``float_id``,
    ``label``, ``caption``, ``references`` (the number of references),
    ``first_text_id`` (the first sentence that refers to it) and ``found_by``
    (``"xref"``: bibr's cross-references; ``"text"``: the label found in the
    text; missing: not referred to). The summary table counts the figures and
    tables and the unreferenced ones per paper. The traffic light is ``"na"``
    without figures or tables, ``"green"`` when all are referred to,
    ``"yellow"`` otherwise, and ``"info"`` for several papers.
    """
    from metacheck.papers.model import PaperList

    papers = list(paper) if isinstance(paper, PaperList) else [paper]
    tables = []
    for p in papers:
        rows = _paper_rows(p)
        tables.append(rows.assign(paper_id=pd.Series([p.paper_id] * len(rows), dtype="string")))
    table = pd.concat(tables, ignore_index=True).drop(columns=["caption_text_id"])
    table = table.assign(label=table["label"].astype("string"))
    table = table.assign(caption=table["caption"].astype("string"))

    ids = [p.paper_id for p in papers]
    by_paper = {pid: table.loc[table["paper_id"] == pid] for pid in ids}
    summary_table = pd.DataFrame(
        {
            "paper_id": pd.Series(ids, dtype="string"),
            "floats": pd.Series([len(by_paper[i]) for i in ids], dtype="Int64"),
            "unreferenced": pd.Series(
                [int((by_paper[i]["references"] == 0).fillna(False).sum()) for i in ids],
                dtype="Int64",
            ),
        }
    )

    missing = table.loc[(table["references"] == 0).fillna(False).to_numpy(dtype=bool)]
    checked = int(table["references"].notna().sum())
    names = [_name(k, str(lab)) for k, lab in zip(missing["kind"], missing["label"], strict=True)]
    report: Any = ""
    if len(papers) > 1:
        tl = "info" if len(table) else "na"
        summary_text = (
            f"{int((summary_table['unreferenced'] > 0).sum())} of {len(ids)} papers have a "
            "figure or table that is not referred to in the text."
        )
    elif not len(table):
        tl, summary_text = "na", "No figures or tables were found."
    elif not checked:
        tl = "na"
        summary_text = (
            "The figures and tables have no labels and no linked references, so they "
            "could not be checked."
        )
    elif not names:
        tl = "green"
        summary_text = f"All {checked} figures and tables are referred to in the text."
        report = summary_text
    else:
        tl = "yellow"
        summary_text = (
            f"{len(names)} of {checked} figures and tables "
            f"{'is' if len(names) == 1 else 'are'} not referred to in the text: "
            f"{', '.join(names)}."
        )
        report = (
            summary_text + " Refer to every figure and table in the text, so readers know "
            "what it shows and when to look at it; if it is not needed, consider removing it."
        )

    return {
        "table": table,
        "summary_table": summary_table,
        "na_replace": {"floats": 0, "unreferenced": 0},
        "traffic_light": tl,
        "summary_text": summary_text,
        "report": report,
    }
