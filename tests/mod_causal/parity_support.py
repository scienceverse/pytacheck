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
