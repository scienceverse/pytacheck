"""Python side of the ``mod_causal`` parity cases (see ``gen_parity_cases.py``).

``causal_relations()`` calls a Hugging Face Space. metacheck reaches it with
``curl`` directly (not httr2), so httptest2 cannot replay it and parity cases
must not use the network. Cases that exercise the causal-claim branches
therefore replace ``causal_relations()`` on both sides with the same
deterministic fake: R with ``testthat::with_mocked_bindings()`` and Python with
:func:`run_fake_causal`. The fake mirrors the real function's contract (one row
per relation, ``NA`` cause/effect for sentences without relations, an empty
table for empty or blank input):

* a sentence is causal when it contains ``"caus"`` or ``"effect"`` (any case);
* its relation is (first word, last word), plus (second word, second-to-last
  word) when the sentence contains ``" and "``.

The R twin is :data:`R_FAKE` / :data:`R_MK` in ``gen_parity_cases.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any
from unittest import mock

import pandas as pd

import pytacheck as pc


def fake_causal_relations(sentence: Any, *args: Any, **kwargs: Any) -> pd.DataFrame:
    """Deterministic stand-in for :func:`pytacheck.text.causal.causal_relations`."""
    sentences = [sentence] if isinstance(sentence, str) else list(sentence or [])
    rows: list[tuple[Any, ...]] = []
    if sentences and not all(s.strip(" \t\r\n") == "" for s in sentences):
        for s in sentences:
            words = s.split(" ")
            if "caus" in s.lower() or "effect" in s.lower():
                rows.append((s, True, words[0], words[-1]))
                if " and " in s:
                    rows.append((s, True, words[1], words[-2]))
            else:
                rows.append((s, False, None, None))
    return pd.DataFrame(
        {
            "sentence": pd.Series([r[0] for r in rows], dtype="string"),
            "causal": pd.Series([r[1] for r in rows], dtype="boolean"),
            "cause": pd.Series([r[2] for r in rows], dtype="string"),
            "effect": pd.Series([r[3] for r in rows], dtype="string"),
        }
    )


def run_fake_causal(x: Callable[[], Any]) -> Any:
    """Call *x* with ``causal_relations()`` replaced by :func:`fake_causal_relations`."""
    with mock.patch("pytacheck.text.causal.causal_relations", fake_causal_relations):
        return x()


def mk(
    abstract: Sequence[str] = (),
    body: Sequence[str] = (),
    title: str | None = "",
    id: str | None = None,
) -> Any:
    """A test paper whose first sentences form an abstract section.

    R: ``p <- test_paper(c(abstract, body))`` with sections 1 ("Abstract",
    type abstract) and 2 ("Methods", type method), ``p$info$title <- title``.
    """
    p = pc.test_paper([*abstract, *body])
    text = p.text.copy()
    text["section_id"] = pd.Series(
        [1.0] * len(abstract) + [2.0] * len(body), index=text.index, dtype="float64"
    )
    p.text = text
    p.section = pd.DataFrame(
        {
            "section_id": [1.0, 2.0],
            "header": pd.Series(["Abstract", "Methods"], dtype="string"),
            "parent_section_id": pd.Series([None, None], dtype="Int64"),
            "section_type": pd.Series(["abstract", "method"], dtype="string"),
            "classification_score": [0.0, 0.0],
        }
    )
    info = p.info.copy()
    info["title"] = pd.Series([title] * len(info), index=info.index, dtype="string")
    p.info = info
    if id is not None:
        p.paper_id = id
    return p


def untitled(text: Sequence[str], title: str | None = "", id: str | None = None) -> Any:
    """``test_paper(text)`` with ``p$info$title <- title`` (no abstract section)."""
    p = pc.test_paper(list(text))
    info = p.info.copy()
    info["title"] = pd.Series([title] * len(info), index=info.index, dtype="string")
    p.info = info
    if id is not None:
        p.paper_id = id
    return p


def fake_causal_relations_na(sentence: Any, *args: Any, **kwargs: Any) -> pd.DataFrame:
    """:func:`fake_causal_relations`, but sentences containing ``"maybe"`` get ``causal = NA``.

    The real classifier never returns a missing flag (``isTRUE()``); this fake
    checks that the module keeps R's ``NA`` semantics (``sum()`` propagates it,
    ``dplyr::filter()`` drops it, ``if (!any(NA))`` is an error).
    R twin: ``R_FAKE_NA`` in ``gen_review_cases.py``.
    """
    out = fake_causal_relations(sentence)
    maybe = [isinstance(s, str) and "maybe" in s.lower() for s in out["sentence"].tolist()]
    causal = out["causal"].astype("boolean").copy()
    causal[pd.Series(maybe, index=out.index, dtype=bool)] = pd.NA
    out["causal"] = causal
    return out


def run_fake_causal_na(x: Callable[[], Any]) -> Any:
    """Call *x* with ``causal_relations()`` replaced by :func:`fake_causal_relations_na`."""
    with mock.patch("pytacheck.text.causal.causal_relations", fake_causal_relations_na):
        return x()


def with_section_ids(p: Any, section_id: Sequence[float | None]) -> Any:
    """R ``p$text$section_id <- section_id`` (``None`` -> ``NA``)."""
    text = p.text.copy()
    text["section_id"] = pd.Series(
        [float("nan") if v is None else float(v) for v in section_id],
        index=text.index,
        dtype="float64",
    )
    p.text = text
    return p


def without_title(p: Any) -> Any:
    """R ``p$info$title <- NULL``."""
    p.info = p.info.drop(columns=["title"])
    return p


def without_text(p: Any) -> Any:
    """R ``p$text$text <- NULL``."""
    p.text = p.text.drop(columns=["text"])
    return p


def with_section_types(p: Any, section_type: Sequence[str]) -> Any:
    """R ``p$section$section_type <- section_type``."""
    section = p.section.copy()
    section["section_type"] = pd.Series(list(section_type), index=section.index, dtype="string")
    p.section = section
    return p


def error_message(x: Callable[[], Any]) -> Any:
    """R ``tryCatch(x, error = function(e) conditionMessage(e))``."""
    try:
        return x()
    except Exception as exc:
        return str(exc)


def report_qmd(out: Any) -> list[str]:
    """``module_run(...)$report`` as R holds it: table blocks become the R
    chunk ``scroll_table()`` returns, so tables are compared too."""
    from pytacheck.report import ReportTable
    from pytacheck.report.render import table_chunk

    return [table_chunk(x) if isinstance(x, ReportTable) else x for x in out.report]
