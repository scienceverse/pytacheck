"""Funding Check (port of ``inst/modules/funding_check.R``).

The rtransparent helpers the module embeds live in
:mod:`metacheck.modules._funding`.

References (R ``@references``):

* Serghiou, S., Contopoulos-Ioannidis, D. G., Boyack, K. W., Riedel, N.,
  Wallach, J. D., & Ioannidis, J. P. (2021). Assessment of transparency
  indicators across the biomedical literature: How open is open?. PLoS
  biology, 19(3), e3001107. doi: 10.1371/journal.pbio.3001107
* Serghiou S (2025). *rtransparent: Identifies indicators of transparency*.
  R package version 0.2.5, commit d0d5dfe4b6c4e519d54e341436d4263fb05d84be,
  <https://github.com/serghiou/rtransparent>.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from metacheck.module import module

REFERENCES = (
    "Serghiou, S., Contopoulos-Ioannidis, D. G., Boyack, K. W., Riedel, N., Wallach, J. D., "
    "& Ioannidis, J. P. (2021). Assessment of transparency indicators across the biomedical "
    "literature: How open is open?. PLoS biology, 19(3), e3001107. "
    "doi: 10.1371/journal.pbio.3001107",
    "Serghiou S (2025). _rtransparent: Identifies indicators of transparency_. R package "
    "version 0.2.5, commit d0d5dfe4b6c4e519d54e341436d4263fb05d84be, "
    "<https://github.com/serghiou/rtransparent>.",
)

_FOUND_INTRO = "The following funding statement was detected:"
_FOUND_SUMMARY = "A funding statement was detected."
_NONE_REPORT = "No funding statement was detected. Consider adding one."
_NONE_SUMMARY = "No funding statement was detected."


def _funding_table(sentences: pd.DataFrame) -> pd.DataFrame:
    """``summarise(text = rtransparent_funding(text), .by = paper_id) |> filter(...)``.

    Every paper's sentences view one shared column, so each detector pattern
    runs once over all papers (see :class:`metacheck.modules._funding._Article`).
    """
    from metacheck.modules._funding import _Article, rtransparent_funding

    ids = sentences["paper_id"]
    codes, uniques = pd.factorize(ids, use_na_sentinel=False)
    order = codes.argsort(kind="stable")
    texts_col = sentences["text"].tolist()
    texts: list[str | None] = [
        t if isinstance(t, str) else None for t in (texts_col[i] for i in order)
    ]
    counts = np.bincount(codes, minlength=len(uniques)).tolist()
    cache: dict[Any, Any] = {}
    start = 0
    found: list[str] = []
    for n in counts:
        found.append(rtransparent_funding(_Article(texts, start, start + n, cache)))
        start += n
    table = pd.DataFrame(
        {
            "paper_id": pd.Series(list(uniques), dtype="string"),
            "text": pd.Series(found, dtype="string"),
        }
    )
    keep = table["text"].notna() & (table["text"].str.len() > 0)
    return table.loc[keep.fillna(False).astype(bool).to_numpy()].reset_index(drop=True)


def _results(table: pd.DataFrame) -> dict[str, Any]:
    """The summary table, traffic light and report shared by both funding modules."""
    from metacheck.report import scroll_table

    ids = table["paper_id"].drop_duplicates()
    summary_table = pd.DataFrame(
        {
            "paper_id": pd.Series(ids.tolist(), dtype="string"),
            "funding_found": pd.Series([True] * len(ids), dtype="boolean"),
        }
    )
    tl = "green" if len(table) else "red"
    if tl == "green":
        report: Any = [_FOUND_INTRO, scroll_table(table["text"].tolist())]
        summary_text = _FOUND_SUMMARY
    else:
        report = _NONE_REPORT
        summary_text = _NONE_SUMMARY
    return {
        "table": table,
        "summary_table": summary_table,
        "na_replace": False,
        "traffic_light": tl,
        "summary_text": summary_text,
        "report": report,
    }


@module(
    title="Funding Check",
    description="Identify and extract funding statements.",
    details="""
        The Funding Check module uses regular expressions to check sentences for words related to funding statements. It will return the sentences in which the funding statement was found.

        The function incorporates code from [rtransparent](https://github.com/serghiou/rtransparent), which is no longer maintained. For their validation, see [the paper](https://doi.org/10.1371/journal.pbio.3001107).
    """,
    keywords=["general"],
    author=["Daniel Lakens <d.lakens@tue.nl>"],
    params={"paper": "a paper object or paperlist object"},
)
def funding_check(paper: Any) -> dict[str, Any]:
    """Port of ``inst/modules/funding_check.R::funding_check()``.

    Runs the rtransparent funding detector
    (:func:`metacheck.modules._funding.rtransparent_funding`) on each paper's
    sentences (references excluded) and returns one row per paper with a
    funding statement.
    """
    from metacheck.text import text_search

    sentences = text_search(paper)
    table = _funding_table(sentences.loc[:, ["paper_id", "text"]])
    return _results(table)
