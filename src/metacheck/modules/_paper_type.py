"""bibr's paper type, for the checks that only apply to some papers (D85).

bibr classifies each paper (``info.paper_type``: empirical, review,
meta_analysis, case_study, commentary, corrigendum, erratum, retraction or
unknown). A check that only makes sense for papers that collect data (ethics
approval) or report research (open data and code) skips a paper bibr
classifies as another type, unless bibr was unsure.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from metacheck.papers.model import Paper

__all__ = [
    "MIN_CONFIDENCE",
    "NO_LIVE_DATA",
    "NO_RESEARCH",
    "paper_type",
    "skipped_type",
    "type_name",
]

#: below this confidence bibr's paper type is not acted on
MIN_CONFIDENCE = 0.7
#: paper types that collect no data from participants: no ethics approval needed
NO_LIVE_DATA = frozenset(
    {"review", "meta_analysis", "commentary", "corrigendum", "erratum", "retraction"}
)
#: paper types that report no research of their own: no data or code to share
NO_RESEARCH = frozenset({"commentary", "corrigendum", "erratum", "retraction"})

_NAMES = {"meta_analysis": "a meta-analysis", "erratum": "an erratum"}


def paper_type(paper: Any) -> tuple[str | None, float | None]:
    """bibr's paper type of *paper* and its confidence (``None`` when not given)."""
    info = paper.get("info") if isinstance(paper, Paper) else None
    if not isinstance(info, pd.DataFrame) or "paper_type" not in info.columns or not len(info):
        return None, None
    kind = info["paper_type"].iloc[0]
    conf = (
        info["paper_type_confidence"].iloc[0] if "paper_type_confidence" in info.columns else None
    )
    kind = kind if isinstance(kind, str) and kind else None
    conf = None if conf is None or pd.isna(conf) else float(conf)
    return kind, conf


def skipped_type(paper: Any, types: frozenset[str]) -> str | None:
    """The paper type of *paper* when it is one of *types* and bibr is sure enough
    (no confidence given counts as sure), else ``None``."""
    kind, conf = paper_type(paper)
    if kind not in types or (conf is not None and conf < MIN_CONFIDENCE):
        return None
    return kind


def type_name(kind: str) -> str:
    """``"a commentary"``, ``"a meta-analysis"``."""
    return _NAMES.get(kind, f"a {kind.replace('_', ' ')}")
