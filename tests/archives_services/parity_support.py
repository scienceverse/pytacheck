"""Python side of the ``archives_services`` parity cases.

The R side of a case that needs an answer from a host runs metacheck with the function that asks
mocked (``.seq_get()``, ``.batch_query()``, ``.handle_resolve()``); here the same functions are
stood in for. Case code receives a namespace ``m`` with the modules, ``m.pc`` (pytacheck),
:func:`seq_mock`, :func:`batch_mock` and :func:`handle_mock`.
"""

from __future__ import annotations

import contextlib
import inspect
import tempfile
import types
from collections.abc import Callable, Iterator
from typing import Any


@contextlib.contextmanager
def _patched(obj: Any, name: str, value: Any) -> Iterator[None]:
    saved = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, saved)


def seq_mock(answers: dict[str, str], fn: Callable[[], Any]) -> Any:
    """``fn()`` with ``.seq_get()`` answering from *answers* (url -> text; any other url fails)."""
    from metacheck.archives import seq

    with (
        _patched(seq, "_seq_get", lambda url: answers.get(url)),
        _patched(seq, "_seq_fetch", lambda url: {"text": answers.get(url), "unreachable": False}),
    ):
        return fn()


def seq_unreachable(fn: Callable[[], Any]) -> Any:
    """``fn()`` with every archive failing to answer (``.seq_fetch()``: no text, ``unreachable``)."""
    from metacheck.archives import seq

    with _patched(seq, "_seq_fetch", lambda url: {"text": None, "unreachable": True}):
        return fn()


def batch_mock(answers: dict[str, tuple[int, Any]], fn: Callable[[], Any]) -> Any:
    """``fn()`` with ``.batch_query()`` answering from *answers* (url -> (status, JSON body))."""
    import httpx

    from metacheck import http

    def batch_query(urls: list[str], *args: Any, **kwargs: Any) -> list[Any]:
        out = []
        for u in urls:
            status, body = answers.get(u, (404, None))
            out.append(httpx.Response(status, json=body, request=httpx.Request("GET", u)))
        return out

    with _patched(http, "batch_query", batch_query):
        return fn()


def handle_mock(answers: dict[str, str | None], fn: Callable[[], Any]) -> Any:
    """``fn()`` with ``.handle_resolve()`` answering from *answers* (handle -> address)."""
    from metacheck.archives import handle

    with _patched(handle, "_handle_resolve", lambda h: answers.get(h)):
        return fn()


def _namespace() -> types.SimpleNamespace:
    import pandas as pd

    import metacheck as pc
    from metacheck.archives import _useless, cran, file_share, handle, icpsr, link_versions, seq
    from metacheck.fileinfo import category
    from metacheck.report import file_tree

    return types.SimpleNamespace(
        pc=pc,
        pd=pd,
        cran=cran,
        file_share=file_share,
        handle=handle,
        icpsr=icpsr,
        link_versions=link_versions,
        seq=seq,
        useless=_useless,
        category=category,
        file_tree=file_tree,
        seq_mock=seq_mock,
        seq_unreachable=seq_unreachable,
        batch_mock=batch_mock,
        handle_mock=handle_mock,
    )


def run(x: Callable[..., Any]) -> Any:
    """Call *x* (with the module namespace if it takes an argument), the metacheck cache in a temporary directory."""
    from metacheck.utils import local_options

    with tempfile.TemporaryDirectory() as cache, local_options({"metacheck.cache.dir": cache}):
        if inspect.signature(x).parameters:
            return x(_namespace())
        return x()
