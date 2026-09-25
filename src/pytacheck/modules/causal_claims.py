"""Randomization and Causal Claims (port of ``inst/modules/causal_claims.R``)."""

from __future__ import annotations

from typing import Any

import pandas as pd

from pytacheck._r import grepl, gsub, plural
from pytacheck.module import module
from pytacheck.report import collapse_section, format_ref, scroll_table
from pytacheck.text import text_search

# R: `include_re` / `exclude_re`, PCRE with inline (?ix) flags (extended mode:
# layout whitespace and `#` comments are ignored). Changed from metacheck (U85):
# British spellings (randomis...) are found, negated statements ("not
# randomized", "non-randomized", "without random assignment") are excluded, and
# an exclusion only removes its own words, so "randomly assigned ... random
# intercepts" still counts (see `_describes_randomization()`).
_INCLUDE_RE = r"""(?ix)(
    \brandom(?:ly)?\s+assign(?:ed|ment)\b
  | \bassign(?:ed)?\s+at\s+random\b
  | \bassign(?:ed)?\s+random(?:ly)?\b
  | \brandomi[sz](?:e|ed|ation)\b
  | \brandom(?:ly)?\s+allocat(?:ed|ion)\b
  | \brandom(?:ly)?\s+divid(?:e|ed)\b
  | \brandom(?:ly)?\s+split\b
  | \bstratified\s+random\s+assign(?:ment|ed)\b
)"""

_EXCLUDE_RE = r"""(?ix)(
    \brandom\s+effects?\b
  | \brandom\s+intercepts?\b
  | \brandom\s+slopes?\b
  | \brandom\s+factors?\b
  | \brandom\s+coefficients?\b
  | \brandom\s+error\b
  | \brandom\s+fields?\b
  | \brandom\s+generator\b

  # order / trial / block randomized (either side of the word)
  | (?:\border\b[^\n]{0,60}\brandom(?:ly|i[sz]ed)?\b)|(?:\brandom(?:ly|i[sz]ed)?\b[^\n]{0,60}\border\b)
  | (?:\btrial\b[^\n]{0,60}\brandom(?:ly|i[sz]ed)?\b)|(?:\brandom(?:ly|i[sz]ed)?\b[^\n]{0,60}\btrial\b)
  | (?:\bblock\b[^\n]{0,60}\brandom(?:ly|i[sz]ed)?\b)|(?:\brandom(?:ly|i[sz]ed)?\b[^\n]{0,60}\bblock\b)
  | \brandom\s+jitter(?:ed|ing)?\b
  | \brandom\s+noise\b
  | \brandom\s+pixels?\b|\brandom\-pixel\b
  | \bpseudo\-?random(?:ly|i[sz]ed)?\b

  # sampling / lotteries / non-arm selection
  | \brandom\s+digit\-?dial(?:ing)?\b
  | \brandom\s+samples?\b|\brandom\s+sampling\b|\brandom\s+sampled\b
  | \brandom\s+lotter(y|ies)\b|\brandom\s+offer\b
  | \brandom\s+distribut(?:ion|ed)\b
  | \brandom\s+locations?\b
  | \brandom(?:ly)?\s+intermixed\b

  # diagnostic mentions rather than the assignment sentence itself
  | \bsuccessful\s+random\s+assignment\b

  # negated or absent random assignment (pytacheck, U85)
  | \bnon\-?randomi[sz](?:e|ed|ation)\b
  | \bnot\s+(?:been\s+|be\s+)?(?:randomi[sz]ed|random(?:ly)?\s+(?:assign|allocat|divid)\w*|assigned\s+(?:at\s+)?random(?:ly)?)\b
  | \b(?:without|no|lack\s+of|absence\s+of)\s+(?:randomi[sz]ation|random\s+(?:assignment|allocation))\b
)"""


def _describes_randomization(texts: pd.Series) -> pd.Series:
    """Which sentences describe random assignment.

    metacheck keeps a sentence when it matches `include_re` and not
    `exclude_re`, so "Participants were randomly assigned ... random
    intercepts" was dropped (U85). Here the words an exclusion matches are
    removed first and the rest of the sentence is checked with `include_re`.
    """
    masked = gsub(_EXCLUDE_RE, " ", texts, perl=True)
    return pd.Series(grepl(_INCLUDE_RE, masked, perl=True), index=texts.index, dtype=bool)


# R: format_ref(Antonakis2010) and format_ref(Grosz2020), rendered by R's
# bibentry html style.
_ANTONAKIS2010 = (
    "Antonakis J, Bendahan S, Jacquart P, Lalive R (2010). "
    "&ldquo;On making causal claims: A review and recommendations.&rdquo; "
    "<em>The Leadership Quarterly</em>, <b>21</b>(6), 1086&ndash;1120. "
    '<a href="https://doi.org/10.1016/j.leaqua.2010.10.010">doi:10.1016/j.leaqua.2010.10.010</a>.'
)
_GROSZ2020 = (
    "Grosz M, Rohrer J, Thoemmes F (2020). "
    "&ldquo;The Taboo Against Explicit Causal Inference in Nonexperimental Psychology.&rdquo; "
    "<em>Perspectives on Psychological Science</em>, <b>15</b>(5), 1243&ndash;1255. "
    '<a href="https://doi.org/10.1177/1745691620921521">doi:10.1177/1745691620921521</a>.'
)

_NO_RANDOMIZATION = [
    "Metacheck's text matching algorithm did not identify sentences describing randomization. "
    "If random assignment was present, please clearly report this (e.g., participants were "
    "randomly assigned to...). If this was a non-randomized study, the journal article "
    "reporting standards (JARS) ask that you describe the following:",
    "- Procedures employed to help minimize potential bias due to nonrandomization "
    "(e.g., matching, propensity score matching).",
]

_JARS_RANDOMIZATION = [
    "If this was a study that contained random assignment to conditions, the journal article "
    "reporting standards (JARS) ask that you describe the following:",
    "1. Random assignment method: Procedure used to generate the random assignment sequence, "
    "including details of any restriction (e.g., blocking, stratification)",
    "2. Random assignment concealment: Whether sequence was concealed until interventions "
    "were assigned",
    "3. Random assignment implementation: Who generated the assignment sequence, who enrolled "
    "participants, who assigned participants to groups",
]

_NO_RANDOM_ABSTRACT = (
    "As no random assignment to conditions was detected in the text, carefully check if the "
    "sentences below are warranted, given the study design. If random assignment was present, "
    "please clearly report this."
)
_RANDOM_ABSTRACT = (
    "Random assignment was detected, so these causal claims might be warranted, but it is "
    "always prudent to double-check."
)

_REPORT_TEXT = (
    "Journal Article Reporting Standards require details about randomization procedures, or "
    "how possible bias due to non-randomization is mitigated. This information is often not "
    "reported. Furthermore, researchers sometimes make causal claims that are not warranted, "
    "for example because there was no random assignment to conditions. This module checks how "
    "(non)randomization is reported, and checks for causal claims in the title and abstract. "
    "Researchers are asked to double check whether this information is reported completely "
    "and correctly."
)

_DETAILS = """
    The Randomization and Causal Claims Check first uses regular expressions to check whether the manuscript contains a statement about randomization to conditions. Subsequently, it sends the title and abstract to a [machine learning classifier developed by Rasoul Norouzi](https://github.com/rasoulnorouzi/causal_relation_miner) that runs on [HuggingFace](https://huggingface.co/spaces/lakens/causal_sentences). Causal statements are identified. Researchers are recommended to double check if causal statements are warranted, especially if no sentences describing randomization were detected.

    The regular expressions can miss statements about randomization, or incorrectly assume there is a sentence describing randomization. The module can’t evaluate if the causal statements that are identified are warranted or not, and it only reminds users to double-check.

    If you want to improve the detection of sentences describing randomization, or otherwise improve the module, reach out to the Metacheck development team.
"""


def _titles(paper: Any) -> list[str]:
    """The titles to classify: ``info$title`` of the paper, or of every paper in a list.

    metacheck reads ``paper$info$title``, which is ``NULL`` for a paper list (so
    no title of a list was ever checked), and a missing (``NA``) title stopped
    the module (U84). Missing and blank titles are skipped.
    """
    from pytacheck.papers.model import Paper, PaperList

    if isinstance(paper, Paper):
        papers = [paper]
    elif isinstance(paper, PaperList):
        papers = list(paper)
    else:
        papers = []
    out: list[str] = []
    for p in papers:
        info = p.info
        if not isinstance(info, pd.DataFrame) or "title" not in info.columns:
            continue
        out.extend(v for v in info["title"].tolist() if isinstance(v, str) and v.strip(" \t\r\n"))
    return out


def _any(causal: pd.Series) -> bool:
    """R ``if (any(x))``: ``any()`` is ``NA`` when *x* has ``NA`` and no ``TRUE``,
    and ``if (NA)`` is an error."""
    x = causal.astype("boolean")
    if bool(x.eq(True).fillna(False).any()):
        return True
    if bool(x.isna().any()):
        raise ValueError("missing value where TRUE/FALSE needed")
    return False


def _summarise_causal(causal_abstract: pd.DataFrame, table: pd.DataFrame) -> pd.DataFrame:
    """The number of causal relations in each paper's abstract.

    metacheck joins the classifier's rows back to the papers by sentence text
    (``left_join(causal_abstract, table, by = c(sentence = "text"))``), so a
    sentence occurring k times (in one paper or across a paper list) was
    counted k * k times, and for every paper that has it (U84). Here each
    occurrence of a sentence counts its own relations once.
    """
    if "text" not in table.columns:
        # text_search() drops the text column when the text table has none
        # (e.g. an empty paper list)
        raise ValueError("Join columns in `y` must be present in the data.")
    sentences = table["text"].tolist()
    occurrences: dict[Any, int] = {}
    for sent in sentences:
        occurrences[sent] = occurrences.get(sent, 0) + 1
    # relations per occurrence: causal_relations() returns the rows of each
    # input sentence, so a repeated sentence's rows are repeated
    per_sentence: dict[Any, Any] = {}
    causal = causal_abstract["causal"].astype("boolean")
    for sent, flag in zip(causal_abstract["sentence"].tolist(), causal.tolist(), strict=True):
        if sent not in occurrences:
            continue
        prev = per_sentence.get(sent, 0)
        per_sentence[sent] = pd.NA if prev is pd.NA or flag is pd.NA else prev + int(bool(flag))
    counts: dict[Any, Any] = {}
    for pid, sent in zip(table["paper_id"].tolist(), sentences, strict=True):
        if sent not in per_sentence:
            continue
        n = per_sentence[sent]
        n = pd.NA if n is pd.NA else round(n / occurrences[sent])
        prev = counts.get(pid, 0)
        # R `sum()` propagates NA (module_run() later replaces it with na_replace)
        counts[pid] = pd.NA if prev is pd.NA or n is pd.NA else prev + n
    return pd.DataFrame(
        {
            "paper_id": pd.Series(list(counts), dtype="string"),
            "causal": pd.Series(list(counts.values()), dtype="Int64"),
        }
    )


@module(
    title="Randomization and Causal Claims",
    description=(
        "Aims to identify the presence of random assignment, and lists sentences that make "
        "causal claims in title or abstract."
    ),
    details=_DETAILS,
    keywords=["method"],
    requires=["network"],
    author=["Daniel Lakens <D.Lakens@tue.nl>"],
    params={"paper": "a paper object or paperlist object"},
)
def causal_claims(paper: Any) -> dict[str, Any]:
    """Port of inst/modules/causal_claims.R::causal_claims().

    Finds sentences describing random assignment (regular expressions), then
    sends the title and the abstract sentences to the causal relation
    classifier (:func:`pytacheck.text.causal.causal_relations`, a Hugging Face
    Space) and lists the causal claims. Causal claims without detected random
    assignment give a yellow traffic light.
    """
    from pytacheck.text import causal

    # R calls text_search() twice: text_search(paper, "random") and, below,
    # text_search(paper) for the abstract. The first is the second filtered on
    # grepl("random", text, ignore.case = TRUE): text_search() matches the raw
    # text, but whitespace normalisation and unique() cannot change whether a
    # row contains "random", so one search of the whole table is enough.
    sentences = text_search(paper)

    # randomisation ----
    # R: `random_sentences$text` is NULL when text_search() returned no text
    # column (an empty paper list), so nothing matches
    all_text = sentences["text"] if "text" in sentences.columns else pd.Series([], dtype="string")
    has_random = grepl("random", all_text, ignore_case=True)
    texts = all_text[pd.Series(has_random, index=all_text.index, dtype=bool)]
    random_assignment_subset = texts[_describes_randomization(texts)]
    n_random = len(random_assignment_subset)

    if n_random == 0:
        summary_text_randomization = "We identified no sentences describing randomization."
        report_randomization: list[Any] = list(_NO_RANDOMIZATION)
    else:
        summary_text_randomization = (
            f"We identified {n_random} sentence{plural(n_random)} describing randomization."
        )
        report_randomization = [
            summary_text_randomization,
            scroll_table(random_assignment_subset.tolist()),
            *_JARS_RANDOMIZATION,
        ]

    # causal claims ----
    table = sentences[sentences["section_type"].eq("abstract").fillna(False).astype(bool)]
    table = table.reset_index(drop=True)
    causal_title = causal.causal_relations(_titles(paper))
    causal_abstract = causal.causal_relations(
        table["text"].tolist() if "text" in table.columns else []
    )

    ## summary_table ----
    summary_table = _summarise_causal(causal_abstract, table)
    # filter after so join doesn't fail
    causal_abstract = causal_abstract[
        causal_abstract["causal"].fillna(False).astype(bool)
    ].reset_index(drop=True)

    ## causal title ----
    title_causal = _any(causal_title["causal"])
    if not title_causal:
        summary_text_title = "No causal claims were observed in the title."
        report_causal_title: list[Any] = [summary_text_title]
    else:
        summary_text_title = "Causal claims were detected in the title."
        # only the causal titles (a paper list has several)
        causal_rows = causal_title["causal"].astype("boolean").fillna(False).astype(bool)
        report_causal_title = [
            summary_text_title,
            scroll_table(
                causal_title.loc[causal_rows.to_numpy(), ["sentence", "cause", "effect"]], 1
            ),
        ]

    ## causal abstract ----
    abstract_causal = _any(causal_abstract["causal"])
    if not abstract_causal:
        summary_text_abstract = "No causal claims were observed in the abstract."
        report_text_causal_abstract = ""
    else:
        summary_text_abstract = "Causal claims were detected in the abstract."
        report_text_causal_abstract = _NO_RANDOM_ABSTRACT if n_random == 0 else _RANDOM_ABSTRACT
    report_causal_abstract = [
        summary_text_abstract,
        scroll_table(causal_abstract.loc[:, ["sentence", "cause", "effect"]], 1),
        report_text_causal_abstract,
    ]

    guidance = [
        "For advice on how to make causal claims, and when not to, see:",
        format_ref(_ANTONAKIS2010),
        format_ref(_GROSZ2020),
        "For the APA journal articles reporting standards, see <https://apastyle.apa.org/jars>",
    ]

    # traffic light ----
    if title_causal or abstract_causal:
        # causal language without / with random assignment
        tl = "yellow" if n_random == 0 else "green"
    else:
        tl = "green"  # no causal language

    # report ----
    report = [
        _REPORT_TEXT,
        "#### Randomization",
        *report_randomization,
        "#### Causal Claims",
        *report_causal_title,
        *report_causal_abstract,
        collapse_section(guidance),
    ]

    # summary_text ----
    # R: paste("\n- ", x = c(...), collapse = "") (sep = " " adds a second space)
    summary_text = "".join(
        f"\n-  {s}" for s in (summary_text_randomization, summary_text_title, summary_text_abstract)
    )

    return {
        "table": causal_abstract,
        "summary_table": summary_table,
        "na_replace": 0,
        "traffic_light": tl,
        "report": report,
        "summary_text": summary_text,
    }
