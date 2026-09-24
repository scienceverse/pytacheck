"""Trial Registration: find clinical trial registry numbers in a paper."""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from pytacheck.module import module
from pytacheck.report import collapse_section, scroll_table
from pytacheck.text import text_search

_ICTRP = "https://trialsearch.who.int/Trial2.aspx?TrialID={id}"
#: (registry, id pattern, lookup URL with {id}, words the sentence must also contain);
#: patterns are PCRE and Python compatible
REGISTRIES: tuple[tuple[str, str, str, str | None], ...] = (
    ("ClinicalTrials.gov", r"NCT\s?\d{8}", "https://clinicaltrials.gov/study/{id}", None),
    ("ISRCTN", r"ISRCTN\s?\d{8}", "https://www.isrctn.com/{id}", None),
    (
        # a bare YYYY-NNNNNN-CC is also an ethics or grant number: only with the register named
        "EU CTR (EudraCT)",
        r"(?<![\d-])20\d{2}-\d{6}-\d{2}(?!-?\d)",
        "https://www.clinicaltrialsregister.eu/ctr-search/search?query={id}",
        r"\bEudra\s?CT\b|\bEU[\s-]?CTR\b|EU Clinical Trials? Register|clinicaltrialsregister\.eu",
    ),
    (
        "CTIS (EU CT)",
        r"(?<![\d-])20\d{2}-5\d{5}-\d{2}-\d{2}(?!\d)",
        "https://euclinicaltrials.eu/search-for-clinical-trials/?lang=en&EUCT={id}",
        None,
    ),
    ("ANZCTR", r"ACTRN\s?\d{14}", _ICTRP, None),
    ("ChiCTR", r"ChiCTR-?(?:[A-Z]{2,4}-)?\d{8,10}", _ICTRP, None),
    ("DRKS", r"DRKS\s?\d{8}", "https://drks.de/search/en/trial/{id}", None),
    ("CTRI", r"CTRI/\d{4}/\d{2,3}/\d{6}", _ICTRP, None),
    ("PACTR", r"PACTR\s?\d{15}", _ICTRP, None),
    ("UMIN-CTR", r"UMIN\s?\d{9}", _ICTRP, None),
    ("jRCT", r"\bjRCT[0-9a-z]\d{9}\b", _ICTRP, None),
    ("IRCT", r"IRCT\d{10,16}N\d{1,3}", _ICTRP, None),
    ("ReBEC", r"\bRBR-[0-9a-z]{6,8}\b", _ICTRP, None),
    ("Netherlands (NTR / OMON)", r"\bNL-OMON\d{5,6}\b|\bNTR\s?\d{3,5}\b", _ICTRP, None),
)
#: sentences saying the paper reports a trial: a (singular) randomised or clinical
#: trial, an RCT, or trial registration. Generic mentions ("clinical trials have
#: shown...", "120 trials were registered as correct") do not count.
TRIAL_CONTEXT = (
    r"\brandomi[sz]ed\b[\w\s,-]{0,40}?\btrial\b(?!s)"
    r"|\b(?:this|the|our|a|an)\s+(?:[\w-]+\s+){0,3}clinical\s+trial\b(?!s)"
    r"|\bRCT\b|\btrial registration\b"
)

_PATTERNS = [
    (name, re.compile(rx, re.IGNORECASE), url, re.compile(ctx, re.IGNORECASE) if ctx else None)
    for name, rx, url, ctx in REGISTRIES
]
_ANY = "|".join(f"(?:{rx})" for _, rx, _, _ in REGISTRIES)


def _normalise(match: str) -> str:
    text = re.sub(r"\s+", "", match)
    upper = text.upper()
    for prefix in ("NCT", "ISRCTN", "ACTRN", "DRKS", "CTRI", "PACTR", "UMIN", "IRCT", "NTR"):
        if upper.startswith(prefix):
            return prefix + upper[len(prefix) :]
    if upper.startswith("NL-OMON"):
        return upper
    if upper.startswith("CHICTR"):
        return "ChiCTR" + upper[6:]
    if upper.startswith("JRCT"):
        return "jRCT" + text[4:].lower()
    if upper.startswith("RBR-"):
        return "RBR-" + text[4:].lower()
    return text


def _find_ids(sentences: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for rec in sentences.to_dict("records"):
        seen = set()
        for registry, pattern, url, needs in _PATTERNS:
            if needs is not None and not needs.search(str(rec["text"])):
                continue
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
        "ANZCTR, ChiCTR, DRKS, CTRI, PACTR, UMIN/jRCT, IRCT, ReBEC, NTR) and flag trials "
        "that report none."
    ),
    details="""
        Clinical trials should be registered before the first participant is enrolled
        (ICMJE; CONSORT item 23), and the registration number should be reported so that
        readers can compare the published trial with its registered plan.

        The module searches every sentence for the identifier formats of the major
        registries: ClinicalTrials.gov (NCT + 8 digits), ISRCTN (ISRCTN + 8 digits),
        the EU Clinical Trials Register (EudraCT, YYYY-NNNNNN-CC, only in a sentence that
        names EudraCT or the register, since ethics and grant numbers look the same) and
        CTIS (EU CT, YYYY-5NNNNN-CC-NN), ANZCTR (ACTRN + 14 digits), ChiCTR, DRKS (DRKS +
        8 digits), CTRI (CTRI/YYYY/MM/NNNNNN), PACTR, UMIN-CTR and jRCT, IRCT, ReBEC
        (RBR-...) and the Netherlands registers (NTR, NL-OMON). Each number links to the
        registry (or the WHO ICTRP search portal). Numbers from other registries are not
        recognised. If no number is found but the paper says it reports a randomised or
        clinical trial (or an RCT), the module asks you to check that the registration is
        reported. It does not check that the registry record exists or matches the paper.

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
        "yellow": (
            "The paper describes a trial, but no registration number from the recognised "
            "registries was found."
        ),
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
