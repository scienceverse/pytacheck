"""Python halves of ``tests/db/review.R`` (parity/cases/db_review.yaml).

Bulk comparisons of the Crossref parsing on every recorded fixture in
metacheck's ``tests/testthat/apis``; errors become ``"ERROR: <message>"``
exactly as the R helpers report them.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import orjson

APIS = (
    Path(__file__).resolve().parents[2] / "upstream" / "metacheck" / "tests" / "testthat" / "apis"
)


def work_files() -> list[Path]:
    """Recorded ``api.labs.crossref.org/works/<doi>`` bodies that hold a work."""
    d = APIS / "api.labs.crossref.org" / "works"
    out = []
    for f in sorted(d.glob("*.json"), key=lambda p: p.name.encode("utf-8")):
        msg = orjson.loads(f.read_bytes()).get("message")
        if isinstance(msg, dict):
            out.append(f)
    return out


def work(f: Path) -> Any:
    return orjson.loads(f.read_bytes())["message"]


def try_(fn: Callable[[], Any]) -> Any:
    """``review_try()``: the value, or ``"ERROR: <message>"``."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 - mirrors R's tryCatch(error = )
        return f"ERROR: {exc}"


def parse_each(selects: Sequence[Sequence[str]]) -> list[list[Any]]:
    from pytacheck.db.crossref import _crossref_parse_item

    files = work_files()
    return [
        [try_(lambda f=f, sel=sel: _crossref_parse_item(work(f), list(sel))) for f in files]
        for sel in selects
    ]


def query_parse_all(select: Sequence[str], min_score: float = 0) -> Any:
    from pytacheck.db.crossref import _crossref_query_parse

    items = [work(f) for f in work_files()]
    return try_(lambda: _crossref_query_parse(items, min_score, list(select)))


def query_files() -> list[Path]:
    d = APIS / "api.crossref.org"
    return sorted(d.glob("works-*.json"), key=lambda p: p.name.encode("utf-8"))


def query_parse_each(select: Sequence[str], min_score: float = 50) -> list[Any]:
    from pytacheck.db.crossref import _crossref_query_parse

    out = []
    for f in query_files():
        j = orjson.loads(f.read_bytes())
        out.append(
            try_(
                lambda j=j: _crossref_query_parse(
                    j["message"]["items"], min_score, list(select)
                )
            )
        )
    return out
