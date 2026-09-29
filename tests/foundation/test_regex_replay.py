"""Replay the regex calls of a realistic run (record_regex_calls.py).

About 33,000 calls with about 12,000 pattern/flag combinations, made by the paper
modules, extractors and text_search on metacheck's fixture papers, must still give
the recorded results. They were recorded with the TRE/PCRE-faithful engine that
was checked against R, so this pins the plain `regex` translation on realistic
input: TRE's leftmost-longest matching, its bracket rules and classes, PCRE's
ASCII classes, R's replacement syntax and the empty-match rules.
"""

from __future__ import annotations

from typing import Any

from metacheck._r import regex as rx
from tests.foundation.record_regex_calls import DATA, load, method_result, plain


def replay(name: str, spec: list[Any] | None, args: list[Any], kwargs: dict[str, Any]) -> Any:
    if spec is None:
        return plain(getattr(rx, name)(*args, **kwargs))
    method = name.split(".", 1)[1]
    compiled = rx.compile_r(*spec)
    return method_result(method, getattr(compiled, method)(*args, **kwargs))


def test_recorded_calls_give_the_recorded_results() -> None:
    calls = load(DATA)
    assert len(calls) > 30_000
    differ = []
    for name, spec, args, kwargs, expected in calls:
        try:
            got = replay(name, spec, args, kwargs)
        except Exception as exc:
            got = f"{type(exc).__name__}: {exc}"
        if got != expected:
            differ.append((name, spec or args[0], got, expected))
    assert not differ, "\n".join(
        f"{name} {pattern!r}: {str(got)[:200]} (recorded {str(expected)[:200]})"
        for name, pattern, got, expected in differ[:10]
    )
