"""Python halves of ``tests/db/review.R`` (parity/cases/db_review.yaml).

Bulk comparisons of the Crossref parsing on every recorded fixture in
metacheck's ``tests/testthat/apis``; a failure in a list of results is
``{"error": True}`` (``parity.pyhelpers.catch``, R's ``pc_catch()``).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import orjson

from parity.pyhelpers import catch

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


def parse_each(selects: Sequence[Sequence[str]]) -> list[list[Any]]:
    from metacheck.db.crossref import _crossref_parse_item

    files = work_files()
    return [
        [catch(lambda f=f, sel=sel: _crossref_parse_item(work(f), list(sel))) for f in files]
        for sel in selects
    ]


def query_parse_all(select: Sequence[str], min_score: float = 0) -> Any:
    from metacheck.db.crossref import _crossref_query_parse

    items = [work(f) for f in work_files()]
    return _crossref_query_parse(items, min_score, list(select))


def query_files() -> list[Path]:
    d = APIS / "api.crossref.org"
    files = sorted(d.glob("works-*.json"), key=lambda p: p.name.encode("utf-8"))
    return [f for f in files if f.stat().st_size > 0]


def query_parse_each(select: Sequence[str], min_score: float = 50) -> list[Any]:
    from metacheck.db.crossref import _crossref_query_parse

    out = []
    for f in query_files():
        j = orjson.loads(f.read_bytes())
        out.append(
            catch(lambda j=j: _crossref_query_parse(j["message"]["items"], min_score, list(select)))
        )
    return out


MOCK = Path(__file__).resolve().parent / "data" / "mock_review"


def call_mock(fn: str, *args: Any, **kwargs: Any) -> Any:
    """Call *fn* on the synthetic responses in ``tests/db/data/mock_review``."""
    from tests.db.parity_replay import call

    return call(fn, *args, mock_dir=str(MOCK), **kwargs)


def call_offline(fn: str, *args: Any, **kwargs: Any) -> Any:
    """Call *fn* with ``online()`` false (R: ``with_mocked_bindings(online = \\(...) FALSE)``)."""
    import importlib

    from metacheck.db import _utils

    module, _, name = fn.rpartition(".")
    func = getattr(importlib.import_module(module), name)
    old = _utils.online
    _utils.online = lambda *_a, **_k: False  # type: ignore[assignment]
    try:
        return func(*args, **kwargs)
    finally:
        _utils.online = old  # type: ignore[assignment]
