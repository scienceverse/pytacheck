"""License Check: under which license the paper is published.

A pytacheck module (metacheck has no counterpart). It is a small cascade: bibr's
``info.license_spdx``, then its ``info.license_url`` and ``info.license`` text,
then the paper's sentences that name a Creative Commons license.
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from metacheck.module import module
from metacheck.papers.model import Paper

# creativecommons.org/licenses/by-nc-sa/4.0, creativecommons.org/publicdomain/zero/1.0
_CC_URL = re.compile(
    r"creativecommons\.org/(?:licenses/(?P<kind>by(?:-(?:nc|sa|nd)){0,2})"
    r"|publicdomain/(?P<pd>zero|mark))/(?P<version>\d(?:\.\d)?)?",
    re.IGNORECASE,
)
# "CC BY 4.0", "CC-BY-NC-ND 4.0", "CC0 1.0", "Creative Commons Attribution 4.0"
_CC_NAME = re.compile(
    r"\bCC[- ]?(?P<kind>BY(?:[- ](?:NC|SA|ND)){0,2}|0)\b(?:[- ](?P<version>\d\.\d))?",
    re.IGNORECASE,
)
_CC_WORDS = re.compile(
    r"Creative Commons (?P<words>Attribution(?:[- ](?:Non-?Commercial|Share-?Alike|"
    r"No-?Deriv(?:atives|s)?)){0,2})(?: License)?(?:,? (?:version )?(?P<version>\d\.\d))?",
    re.IGNORECASE,
)
_WORD_CODES = (("commercial", "NC"), ("share", "SA"), ("deriv", "ND"))
_RESERVED = re.compile(r"all rights reserved", re.IGNORECASE)
# a sentence worth parsing for a license
_MENTION = re.compile(r"creative ?commons|\bCC[- ]?(?:BY|0)\b|all rights reserved", re.IGNORECASE)
#: licenses that let anyone reuse the paper, also commercially
OPEN = ("CC-BY", "CC-BY-SA", "CC0", "PDDL", "ODC-BY")


def _spdx(kind: str, version: str | None) -> str:
    kind = kind.upper().replace(" ", "-")
    base = "CC0" if kind in ("0", "ZERO") else f"CC-{kind}"
    return f"{base}-{version}" if version else base


def _from_text(text: str) -> str | None:
    """The SPDX ID of the Creative Commons license *text* names, if any."""
    m = _CC_URL.search(text)
    if m:
        if m["pd"]:
            return "CC0-1.0" if m["pd"].lower() == "zero" else "CC-PDM-1.0"
        return _spdx(m["kind"], m["version"])
    m = _CC_NAME.search(text)
    if m:
        return _spdx(m["kind"], m["version"])
    m = _CC_WORDS.search(text)
    if m:
        words = m["words"].lower()
        codes = [code for word, code in _WORD_CODES if word in words]
        return _spdx("-".join(["BY", *codes]), m["version"])
    if _RESERVED.search(text):
        return "all-rights-reserved"
    return None


def _kind(spdx: str | None) -> str | None:
    """``"open"``, ``"restricted"`` (non-commercial, no derivatives or all rights
    reserved) or ``"other"``."""
    if spdx is None:
        return None
    up = spdx.upper()
    if up == "ALL-RIGHTS-RESERVED" or re.search(r"-(NC|ND)\b", up):
        return "restricted"
    if any(up == o or up.startswith(o + "-") for o in OPEN) or up == "CC-PDM-1.0":
        return "open"
    return "other"


def _info(paper: Paper, field: str) -> str | None:
    info = paper.get("info")
    if not isinstance(info, pd.DataFrame) or field not in info.columns or not len(info):
        return None
    value = info[field].iloc[0]
    return " ".join(value.split()) if isinstance(value, str) and value.strip() else None


def _paper_row(paper: Paper) -> dict[str, Any]:
    from metacheck.text.search import text_search

    url, text = _info(paper, "license_url"), _info(paper, "license")
    spdx = _info(paper, "license_spdx")
    if spdx is None:
        spdx = next((f for f in (_from_text(c) for c in (url, text) if c) if f), None)
    found_by: str | None = "bibr"
    statement = text or url or spdx
    if spdx is None:
        # cascade: bibr found no license, so look for one in the text
        found_by = statement = None
        for sentence in text_search(paper)["text"].dropna().astype(str):
            spdx = _from_text(sentence) if _MENTION.search(sentence) else None
            if spdx:
                found_by, statement = "text", sentence
                break
    if spdx == "all-rights-reserved":
        spdx = None
    return {
        "license": spdx,
        "license_kind": _kind(spdx) if spdx else ("restricted" if found_by else None),
        "license_url": url,
        "statement": statement,
        "found_by": found_by,
    }


@module(
    title="License",
    description="Check under which license the paper is published.",
    details="""
        Reads the license bibr extracted from the document (its SPDX identifier,
        link or statement) and, when there is none, looks for a Creative Commons
        license named in the text ("CC BY 4.0", a creativecommons.org link,
        "Creative Commons Attribution License"). An open license (CC BY, CC BY-SA,
        CC0) is green; a license that forbids commercial reuse or derivatives,
        or "all rights reserved", is yellow; no license found is na.
    """,
    keywords=["general"],
    author=["Jakub Werner"],
    params={"paper": "a paper object or paperlist object"},
)
def license_check(paper: Any) -> dict[str, Any]:
    """Find the license of *paper* (a paper or paper list).

    ``table`` has one row per paper with a license: ``license`` (an SPDX
    identifier such as ``CC-BY-4.0``; missing for "all rights reserved"),
    ``license_kind`` (``"open"``, ``"restricted"`` or ``"other"``),
    ``license_url``, ``statement`` and ``found_by`` (``"bibr"``: the license
    bibr extracted; ``"text"``: a sentence of the paper). The summary table
    has ``license`` and ``open`` per paper. The traffic light is ``"na"``
    without a license, ``"green"`` for an open one, ``"yellow"`` otherwise,
    and ``"info"`` for several papers.
    """
    from metacheck.papers.model import PaperList

    papers = list(paper) if isinstance(paper, PaperList) else [paper]
    rows = [{**_paper_row(p), "paper_id": p.paper_id} for p in papers]
    cols = ["license", "license_kind", "license_url", "statement", "found_by", "paper_id"]
    every = pd.DataFrame(rows, columns=cols).astype("string")
    table = every.loc[every["found_by"].notna().to_numpy(dtype=bool)].reset_index(drop=True)
    summary_table = pd.DataFrame(
        {
            "paper_id": every["paper_id"],
            "license": every["license"],
            "open": pd.array(
                [None if k is pd.NA else k == "open" for k in every["license_kind"]],
                dtype="boolean",
            ),
        }
    )

    report: Any = ""
    if len(papers) > 1:
        tl = "info" if len(table) else "na"
        n_open = int(summary_table["open"].fillna(False).sum())
        summary_text = (
            f"{n_open} of {len(papers)} papers have an open license; "
            f"{len(papers) - len(table)} have no license that could be found."
        )
    elif not len(table):
        tl, summary_text = "na", "No license was found."
    else:
        row = table.iloc[0]
        name = row["license"] if row["license"] is not pd.NA else "All rights reserved"
        kind = row["license_kind"]
        if kind == "open":
            tl, summary_text = "green", f"The paper has an open license: {name}."
        elif kind == "restricted":
            tl = "yellow"
            summary_text = f"The paper's license restricts reuse: {name}."
            report = summary_text + (
                " A license that forbids commercial reuse or derivatives (or all rights "
                "reserved) keeps the paper from being reused in teaching material, "
                "reviews and text mining; consider CC BY."
            )
        else:
            tl, summary_text = (
                "yellow",
                f"The paper's license is not a Creative Commons one: {name}.",
            )
        report = report or summary_text

    return {
        "table": table,
        "summary_table": summary_table,
        "na_replace": {"open": False},
        "traffic_light": tl,
        "summary_text": summary_text,
        "report": report,
    }
