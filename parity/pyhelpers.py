"""Python counterparts of parity/r/helpers.R (vectorised base-R idioms)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from metacheck._r import base as rb
from metacheck._r import regex as rx


def catch(fn: Callable[[], Any]) -> Any:
    """``pc_catch()``: ``fn()``, or ``{"error": True}`` when it raises (the message is
    dropped: parity never compares error texts). The ``$catch`` constructor."""
    try:
        return fn()
    except Exception:
        return {"error": True}


def as_character(x: list[Any]) -> list[str | None]:
    return [rb.as_character(v) for v in x]


def format_num(x: list[Any], digits: int = 7) -> list[str]:
    return [rb.format_num(v, digits) for v in x]


def trimws(x: list[Any], which: str = "both") -> Any:
    return rb.trimws(list(x), which=which)


def r_sort(x: list[Any]) -> list[Any]:
    return rb.r_sorted(x)


grepl = rx.grepl
gsub = rx.gsub
sub = rx.sub
regextract = rx.regextract
regextract_all = rx.regextract_all
regexec = rx.regexec
strsplit = rx.strsplit


def r_round(x: list[Any], digits: int = 0) -> list[Any]:
    return [rb.r_round(v, digits) for v in x]


def signif(x: list[Any], digits: int = 6) -> list[Any]:
    return [rb.signif(v, digits) for v in x]


def github_readme_probe(repo: str) -> str:
    """Harness self-test for mock_dir (replays the recorded GitHub readme)."""
    import base64

    from metacheck import http

    resp = http.request("GET", f"https://api.github.com/repos/{repo}/readme")
    assert resp is not None
    return base64.b64decode(resp.json()["content"]).decode("utf-8")


def with_mocked(bindings: dict[str, Any], thunk: Any) -> Any:
    """``testthat::with_mocked_bindings()``: run ``thunk()`` with the objects named
    by dotted paths in *bindings* (``{"metacheck.db.replications.FLoRA": fn}``)
    replaced, and restore them afterwards."""
    import importlib

    saved = []
    try:
        for dotted, value in bindings.items():
            module_name, _, attr = dotted.rpartition(".")
            module = importlib.import_module(module_name)
            saved.append((module, attr, getattr(module, attr)))
            setattr(module, attr, value)
        return thunk()
    finally:
        for module, attr, old in reversed(saved):
            setattr(module, attr, old)
