"""Trial Registration: find clinical trial registry numbers in a paper."""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from pytacheck.module import module
from pytacheck.report import collapse_section, scroll_table
from pytacheck.text import text_search

#: (registry, id pattern, lookup URL with {id}); patterns are PCRE and Python compatible
REGISTRIES: tuple[tuple[str, str, str], ...] = (
    ("ClinicalTrials.gov", r"NCT\s?\d{8}", "https://clinicaltrials.gov/study/{id}"),
    ("ISRCTN", r"ISRCTN\s?\d{8}", "https://www.isrctn.com/{id}"),
    (
        "EU CTR (EudraCT)",
        r"(?<![\d-])20\d{2}-\d{6}-\d{2}(?!-?\d)",
        "https://www.clinicaltrialsregister.eu/ctr-search/search?query={id}",
    ),
    (
        "CTIS (EU CT)",
        r"(?<![\d-])20\d{2}-5\d{5}-\d{2}-\d{2}(?!\d)",
        "https://euclinicaltrials.eu/search-for-clinical-trials/?lang=en&EUCT={id}",
    ),
    ("ANZCTR", r"ACTRN\s?\d{14}", "https://trialsearch.who.int/Trial2.aspx?TrialID={id}"),
    (
        "ChiCTR",
        r"ChiCTR-?(?:[A-Z]{2,4}-)?\d{8,10}",
        "https://trialsearch.who.int/Trial2.aspx?TrialID={id}",
    ),
    ("DRKS", r"DRKS\s?\d{8}", "https://drks.de/search/en/trial/{id}"),
    ("CTRI", r"CTRI/\d{4}/\d{2,3}/\d{6}", "https://trialsearch.who.int/Trial2.aspx?TrialID={id}"),
)
#: sentences suggesting the paper reports a (clinical) trial
TRIAL_CONTEXT = (
    r"\brandomi[sz]ed(?: \w+){0,2} trials?\b|\bclinical trials?\b|\bRCTs?\b"
    r"|\btrials? (?:was |were |is )?registered\b|\btrial registration\b"
)

_PATTERNS = [(name, re.compile(rx, re.IGNORECASE), url) for name, rx, url in REGISTRIES]
_ANY = "|".join(f"(?:{rx})" for _, rx, _ in REGISTRIES)


def _normalise(match: str) -> str:
    text = re.sub(r"\s+", "", match)
    for prefix in ("NCT", "ISRCTN", "ACTRN", "DRKS", "CTRI"):
        if text.upper().startswith(prefix):
            return prefix + text[len(prefix) :]
    if text.upper().startswith("CHICTR"):
        return "ChiCTR" + text[6:].upper()
    return text


def _find_ids(sentences: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for rec in sentences.to_dict("records"):
        seen = set()
        for registry, pattern, url in _PATTERNS:
            for m in pattern.finditer(str(rec["text"])):
                trial_id = _normalise(m.group(0))
                if trial_id in seen:
                    continue
                seen.add(trial_id)
                rows.append(
                    {
                        "paper_id": rec["paper_id"],
                        "registry": registry,
                        "trial_id": trial_id,
                        "url": url.format(id=trial_id),
                        "text": rec["text"],
                        "header": rec.get("header"),
                    }
                )
    table = pd.DataFrame(
        rows, columns=["paper_id", "registry", "trial_id", "url", "text", "header"]
    )
    return table.drop_duplicates(["paper_id", "trial_id"]).reset_index(drop=True)


@module(
    title="Trial Registration",
    description=(
        "Find clinical trial registration numbers (ClinicalTrials.gov, ISRCTN, EudraCT/CTIS, "
        "ANZCTR, ChiCTR, DRKS, CTRI) and flag trials that report none."
    ),
    details="""
        Clinical trials should be registered before the first participant is enrolled
        (ICMJE; CONSORT item 23), and the registration number should be reported so that
        readers can compare the published trial with its registered plan.

        The module searches every sentence for the identifier formats of the major
        registries: ClinicalTrials.gov (NCT + 8 digits), ISRCTN (ISRCTN + 8 digits),
        the EU Clinical Trials Register (EudraCT, YYYY-NNNNNN-CC) and CTIS (EU CT,
        YYYY-5NNNNN-CC-NN), ANZCTR (ACTRN + 14 digits), ChiCTR, DRKS (DRKS + 8 digits)
        and CTRI (CTRI/YYYY/MM/NNNNNN). Each number links to the registry (or the WHO
        ICTRP search portal). If no number is found but the paper mentions a clinical or
        randomised trial, the module asks you to check that the registration is reported.
        It does not check that the registry record exists or matches the paper.

        <validation>This module has not been validated yet. Validation against a sample
        of trial reports (with registry numbers coded by hand) is welcome: see the
        CONTRIBUTING guide of the pytacheck-modules store.</validation>
    """,
    keywords=["method"],
    author=["pytacheck-modules contributors"],
)
def trial_registration(paper: Any) -> dict[str, Any]:
    sentences = text_search(paper, _ANY, perl=True)
    table = _find_ids(sentences)
    context = text_search(paper, TRIAL_CONTEXT, perl=True)

    ids = table.groupby("paper_id", sort=False)["trial_id"]
    summary_table = pd.DataFrame(
        {
            "paper_id": ids.size().index.astype("string"),
            "trial_registration": ids.nunique().to_numpy(),
            "trial_ids": ids.agg(lambda x: "; ".join(dict.fromkeys(x))).to_numpy(),
        }
    )

    if len(table):
        tl = "green"
    elif len(context):
        tl = "yellow"
    else:
        tl = "na"
    unique = list(dict.fromkeys(table["trial_id"]))
    shown = ", ".join(unique[:5]) + (f" and {len(unique) - 5} more" if len(unique) > 5 else "")
    summary_text = {
        "green": f"Trial registration number{'s' if len(unique) != 1 else ''} found: {shown}.",
        "yellow": "The paper describes a trial, but no trial registration number was found.",
        "na": "No trial registration numbers were found (no clinical trial detected).",
    }[tl]

    guidance = [
        "The ICMJE requires trials to be registered in a public registry before the first "
        "participant is enrolled, and CONSORT (item 23) asks reports to give the registry "
        "name and number.",
        "Search all WHO primary registries at once with the "
        "[ICTRP search portal](https://trialsearch.who.int/).",
    ]
    if tl == "green":
        links = table.assign(
            trial_id=[f"[{i}]({u})" for i, u in zip(table["trial_id"], table["url"], strict=True)]
        )
        report: Any = [
            "The paper reports these trial registration numbers. Check that the registered "
            "outcomes and analyses match the ones reported.",
            scroll_table(links.loc[:, ["registry", "trial_id", "text"]], colwidths=[0.2, 0.2, 0.6]),
            collapse_section(guidance, title="Trial registration"),
        ]
    elif tl == "yellow":
        report = [
            "The paper mentions a clinical or randomised trial, but no registration number from "
            "the major registries was found. If the trial was registered, report the registry "
            "and number; if not, explain why.",
            scroll_table(context.loc[:, ["text"]]),
            collapse_section(guidance, title="Trial registration"),
        ]
    else:
        report = summary_text

    return {
        "table": table,
        "summary_table": summary_table,
        "na_replace": {"trial_registration": 0, "trial_ids": ""},
        "traffic_light": tl,
        "summary_text": summary_text,
        "report": report,
    }
