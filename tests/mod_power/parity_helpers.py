"""Python side of the power-module parity cases (``parity/cases/mod_power.yaml``).

LLM settings live in global options and API keys in environment variables,
so the LLM cases run inside :func:`run_llm` (R: ``withr::with_options()`` /
``withr::with_envvar()``) and leave no state behind for other areas. Their
provider replies are replayed from ``tests/mod_power/mocks`` (httptest2
layout; written by ``tests/mod_power/make_fixtures.py``).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import pandas as pd

#: the LLM options of every LLM case (R: withr::with_options(list(...)))
LLM_OPTIONS_R = (
    "list(metacheck.llm.use = TRUE, metacheck.llm.cache = FALSE, "
    "metacheck.llm_reasoning = NULL, metacheck.llm_max_tokens = NULL, "
    "metacheck.llm_max_calls = 30L, metacheck.llm.model = 'groq/test-model')"
)
LLM_ENV_R = "c(GROQ_API_KEY = 'test-key')"


def llm_options() -> dict[str, Any]:
    from pytacheck.llm._rds import RInt

    return {
        "metacheck.llm.use": True,
        "metacheck.llm.cache": False,
        "metacheck.llm_reasoning": None,
        "metacheck.llm_max_tokens": None,
        "metacheck.llm_max_calls": RInt(30),
        "metacheck.llm.model": "groq/test-model",
    }


LLM_ENV = {"GROQ_API_KEY": "test-key"}


def identity(x: Any) -> Any:
    """R ``base::identity()``."""
    return x


def scoped(fn: Callable[[], Any], options: dict[str, Any], env: dict[str, str | None]) -> Any:
    from tests.llm.parity_helpers import scoped as _scoped

    return _scoped(fn, options, env)


def run_llm(paper: Any, **kwargs: Any) -> Any:
    """``module_run(paper, "power", ...)`` with the LLM on (``groq/test-model``)."""
    from pytacheck.module import module_run

    return scoped(lambda: module_run(paper, "power", **kwargs), llm_options(), LLM_ENV)


def run_llm_tables(paper: Any, **kwargs: Any) -> list[pd.DataFrame]:
    """The report tables of :func:`run_llm`."""
    return report_tables(run_llm(paper, **kwargs))


def run_llm_with(
    paper: Any,
    options: dict[str, Any] | None = None,
    env: dict[str, str | None] | None = None,
    **kwargs: Any,
) -> Any:
    """:func:`run_llm` with some LLM options / environment variables overridden.

    ``options`` values replace those of :func:`llm_options` (``None`` unsets
    the option, as R's ``options(x = NULL)``); an ``env`` value of ``None``
    unsets the variable (R ``withr::with_envvar(c(X = NA))``).
    """
    from pytacheck.module import module_run

    opts = {**llm_options(), **(options or {})}
    envs = {**LLM_ENV, **(env or {})}
    return scoped(lambda: module_run(paper, "power", **kwargs), opts, envs)


def report_tables(out: Any) -> list[pd.DataFrame]:
    """The data of the ``scroll_table()`` blocks of a module report.

    R side: the ``table <- structure(...)`` line of each report R chunk,
    evaluated (see ``make_fixtures.py``).
    """
    from pytacheck.report.blocks import ReportTable

    report = out.report if isinstance(out.report, list) else [out.report]
    return [b.data for b in report if isinstance(b, ReportTable)]


def tp(texts: Sequence[str], pid: str | None = None) -> Any:
    """``test_paper(texts)``, with ``paper$paper_id <- pid`` when given.

    R's ``test_paper()`` ids come from the clock, so parity cases fix them.
    """
    import pytacheck as pc

    p = pc.test_paper([texts] if isinstance(texts, str) else list(texts))
    if pid is not None:
        p.paper_id = pid
    return p


def paragraphs(texts: Sequence[str], ids: Sequence[int], pid: str | None = None) -> Any:
    """``test_paper(texts)`` with ``paper$text$paragraph_id <- ids``."""
    p = tp(texts, pid)
    p.text = p.text.assign(paragraph_id=pd.Series(list(ids), dtype="Int64"))
    return p


def papers(*texts: str | Sequence[str]) -> Any:
    """``paperlist()`` of test papers with ids ``p1``, ``p2``, ..."""
    import pytacheck as pc

    return pc.PaperList([tp(t, f"p{i}") for i, t in enumerate(texts, start=1)])


def read(files: Sequence[str]) -> Any:
    """``read(files)`` with paths relative to the repository root."""
    from pathlib import Path

    import pytacheck as pc

    root = Path(__file__).resolve().parents[2]
    papers = pc.read([root / f for f in files])
    return papers if isinstance(papers, pc.PaperList) else pc.PaperList([papers])
