"""Funding Check (Overinclusive) (port of ``inst/modules/funding_check_oi.R``).

In R the file defines a function named ``funding_check`` (metacheck runs it
by the name found in the file); here the module is ``funding_check_oi``, like
its file. See :mod:`metacheck.modules.funding_check` for the references.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from metacheck.module import module

PATTERN_FUND = ("funder", "funded", "funding", "financed", "support")
PATTERN_STUDY = (
    "work",
    "study",
    "studies",
    "research",
    "manuscript",
    "collaboration",
    "paper",
    "article",
    "project",
    "program",
    "grant",
    "award",
    "fellowship",
    "scholarship",
    "stipend",
    # other common words
    "none",
    "author",
    "declare",
    "thank",
    "acknowledge",
)
# if more than 1 per ID, favour those in specific sections. metacheck lists only
# "acknowledgement", but its section type is "acknowledgment" (bibr, bibr 12.0
# Grobid conversion), so acknowledgment sections were never favoured (U105)
LIKELY_SECTION = ("funding", "annex", "acknowledgement", "acknowledgment")


@module(
    title="Funding Check (Overinclusive)",
    description="Identify and extract funding statements.",
    details="""
        The Funding Check module uses regular expressions to check sentences for words related to funding statements. It will return the sentences in which the funding statement was found.

        The function is based on code from [rtransparent](https://github.com/serghiou/rtransparent), which is no longer maintained. For their validation, see [the paper](https://doi.org/10.1371/journal.pbio.3001107).

        This version is over-inclusive, so will have more false positives, but is less likely to miss something.
    """,
    keywords=["general"],
    author=[
        "Daniel Lakens <d.lakens@tue.nl>",
        "Lisa DeBruine <lisa.debruine@glasgow.ac.uk>",
    ],
    params={"paper": "a paper object or paperlist object"},
)
def funding_check_oi(paper: Any) -> dict[str, Any]:
    """Port of ``inst/modules/funding_check_oi.R::funding_check()``.

    Keeps the sentences that mention both a funding term and a study term;
    when a paper has such sentences in a "funding" section, only those.
    """
    from metacheck.modules.funding_check import _results
    from metacheck.text import text_search

    found = text_search(text_search(paper, list(PATTERN_FUND)), list(PATTERN_STUDY))
    likely = found["section_type"].isin(LIKELY_SECTION).fillna(False).astype(bool)
    groups = found["paper_id"].astype(object).where(found["paper_id"].notna(), None)
    any_likely = likely.groupby(groups.tolist(), sort=False, dropna=False).transform("any")
    keep = (~any_likely.astype(bool)) | likely
    table = found.loc[keep.to_numpy(), ["paper_id", "text"]].reset_index(drop=True)
    table = table.astype({"paper_id": "string", "text": "string"})
    return _results(pd.DataFrame(table))
