"""``causal_relations()``: causal relations via the *lakens-causal-sentences* Space.

Port of ``R/causal_sentences.R``. Each sentence is sent to a public Gradio
app on Hugging Face (based on Rasoul Norouzi's SocioCausaNet model) using
Gradio's two-step queue API: a POST enqueues the job and returns an
``event_id``; a GET then streams Server-Sent Events until ``event: complete``,
whose (often double-encoded) payload holds the relations.

References: Norouzi, R., Kleinberg, B., Vermunt, J. K., & van Lissa, C. J.
(2025). Capturing causal claims: A fine-tuned text mining model for
extracting causal sentences from social science papers. *Research Synthesis
Methods*, 16(1), 139-156. https://doi.org/10.1017/rsm.2024.13
"""

from __future__ import annotations

import json
import math
import sys
import time
from collections.abc import Sequence
from typing import Any

import pandas as pd

__all__ = ["causal_relations"]

_BASE = "https://lakens-causal-sentences.hf.space"
_PREFIX = "gradio_api/call"
_API = "/predict"
_COLUMNS = ("sentence", "causal", "cause", "effect")


def _message(msg: str) -> None:
    """R ``message()``: diagnostics go to stderr."""
    print(msg, file=sys.stderr)


def _empty() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "sentence": pd.Series([], dtype="string"),
            "causal": pd.Series([], dtype="boolean"),
            "cause": pd.Series([], dtype="string"),
            "effect": pd.Series([], dtype="string"),
        }
    )


def _is_json_text(s: Any) -> bool:
    return isinstance(s, str) and s[:1] in ("[", "{")


def _dollar(x: Any, name: str) -> Any:
    """R ``x$name`` on a parsed JSON value: exact name, else a unique partial match."""
    from metacheck.text.json_expand import _List, _Vec

    if x is None:
        return None
    if isinstance(x, _Vec):
        raise TypeError("$ operator is invalid for atomic vectors")
    if not isinstance(x, _List) or x.names is None:
        return None
    if name in x.names:
        return x.items[x.names.index(name)]
    hits = [k for k, nm in enumerate(x.names) if nm.startswith(name)]
    return x.items[hits[0]] if len(hits) == 1 else None


def _is_true(x: Any) -> bool:
    """R ``isTRUE()``."""
    from metacheck.text.json_expand import _Vec

    return isinstance(x, _Vec) and x.type == "logical" and x.values == [True]


def _unwrap_final_json(payload: str) -> str:
    """Unwrap Gradio's ``["<final JSON>"]`` completion payload."""
    from metacheck.text.json_expand import _as_character, _JSONError, _parse_json, _simplify, _Vec

    try:
        decoded = _simplify(_parse_json(payload))
    except _JSONError:
        decoded = None
    if isinstance(decoded, _Vec) and decoded.type == "character" and decoded.values:
        first = _as_character(decoded)[0]
        if _is_json_text(first):
            return str(first)
    if _is_json_text(payload):
        return payload
    raise ValueError("Unexpected SSE payload format; cannot unwrap to final JSON.")


def _recycle(cols: dict[str, list[Any]]) -> list[dict[str, Any]]:
    """``data.frame(...)`` of vector arguments, with R's recycling rules."""
    n = max(len(v) for v in cols.values())
    if any(len(v) == 0 or n % len(v) for v in cols.values()):
        detail = ", ".join(str(k) for k in dict.fromkeys(len(v) for v in cols.values()))
        raise ValueError(f"arguments imply differing number of rows: {detail}")
    return [{k: v[i % len(v)] for k, v in cols.items()} for i in range(n)]


def _parse_relations(final_json: str, sentence: str | None) -> list[dict[str, Any]]:
    """One row per relation (or one row with missing cause/effect) for *sentence*."""
    from metacheck.text.json_expand import _as_character, _JSONError, _List, _parse_json, _Vec

    try:
        x = _parse_json(final_json)
    except _JSONError as exc:
        raise ValueError(str(exc)) from exc
    if not isinstance(x, _List):
        raise ValueError("Final payload is not a JSON array.")
    rows: list[dict[str, Any]] = []
    for item in x.items:
        causal = _is_true(_dollar(item, "causal"))
        rels = _dollar(item, "relations")
        n_rels = 0 if rels is None else len(rels.items if isinstance(rels, _List) else rels.values)
        if n_rels == 0:
            rows.append({"sentence": sentence, "causal": causal, "cause": None, "effect": None})
            continue
        if isinstance(rels, _List):
            rel_items = rels.items
        else:
            rel_items = [_Vec(rels.type, [v]) for v in rels.values]
        for r in rel_items:
            cause = _dollar(r, "cause")
            effect = _dollar(r, "effect")
            rows.extend(
                _recycle(
                    {
                        "sentence": [sentence],
                        "causal": [causal],
                        "cause": [None] if cause is None else _as_character(cause),
                        "effect": [None] if effect is None else _as_character(effect),
                    }
                )
            )
    return rows


def _modp_dtoa2(value: float, prec: int) -> str:
    """jsonlite's vendored ``modp_dtoa2()``: fixed decimals, trailing zeros dropped."""
    neg = value < 0
    if neg:
        value = -value
    whole = int(value)
    scale = 10**prec
    tmp = (value - whole) * float(scale)
    frac = int(tmp)
    diff = tmp - frac
    if diff > 0.5 or (diff == 0.5 and prec > 0 and frac & 1):
        frac += 1
        if frac >= scale:
            frac = 0
            whole += 1
    count = prec
    while count > 0 and frac % 10 == 0:
        count -= 1
        frac //= 10
    digits = str(frac).rjust(count, "0") if count else ""
    text = f"{whole}.{digits}" if digits else str(whole)
    return f"-{text}" if neg else text


def _json_number(x: float) -> str:
    """A number as ``jsonlite::toJSON()`` writes it (``num_to_char(digits = 4)``)."""
    if isinstance(x, int) and not isinstance(x, bool):
        return str(x)
    v = float(x)
    if 1e-5 < abs(v) < 2147483647:
        return _modp_dtoa2(v, 4)
    decimals = math.ceil(min(17, max(1, math.log10(abs(v)) if v else -math.inf) + 4))
    return f"{v:.{decimals}g}"


def _post_enqueue(
    sentence: str | None,
    rel_mode: str,
    rel_threshold: float,
    cause_decision: str,
    verbose: bool,
) -> str:
    from metacheck import http

    url = f"{_BASE}/{_PREFIX}{_API}"
    body = (
        '{"data":['
        + ",".join(
            [
                json.dumps(sentence, ensure_ascii=False),
                json.dumps(rel_mode),
                _json_number(rel_threshold),
                json.dumps(cause_decision),
            ]
        )
        + "]}"
    )
    if verbose:
        _message(f"[POST] {url}")
    resp = http.request(
        "POST",
        url,
        content=body.encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": http._user_agent()},
        max_tries=1,
    )
    if resp is None:
        raise ConnectionError(f"Could not connect to {url}")
    if verbose:
        _message(f"[POST] HTTP {resp.status_code}, {len(resp.content)} bytes")
    if resp.status_code < 200 or resp.status_code >= 300:
        raise RuntimeError(
            f"POST failed (HTTP {resp.status_code}). Check base URL or parameters.\n"
            f"URL: {url}\nBody: {resp.text}"
        )
    from metacheck.text.json_expand import _as_character, _JSONError, _parse_json, _simplify

    try:
        j = _simplify(_parse_json(resp.text))
    except _JSONError as exc:
        raise ValueError(str(exc)) from exc
    event_id = None
    for key in ("event_id", "EVENT_ID", "id"):
        event_id = _dollar(j, key)
        if event_id is not None:
            break
    if event_id is None:
        event = _dollar(j, "event")
        if event is not None:
            event_id = _dollar(event, "id")
    if event_id is None:
        raise RuntimeError(
            f"No `event_id` found in POST response.\nURL: {url}\nResponse: {resp.text}"
        )
    return "".join("NA" if v is None else v for v in _as_character(event_id)[:1])


def _get_until_complete(event_id: str, timeout: float, verbose: bool) -> str:
    import httpx

    from metacheck import http

    url = f"{_BASE}/{_PREFIX}{_API}/{event_id}"
    if verbose:
        _message(f"[GET/SSE] {url}")
    last_event: str | None = None
    payload: str | None = None
    deadline = time.monotonic() + timeout
    try:
        with http.client().stream(
            "GET", url, headers={"User-Agent": http._user_agent()}, timeout=timeout
        ) as resp:
            for line in resp.iter_lines():
                if time.monotonic() > deadline:
                    break
                if not line:
                    continue
                if line.startswith("event:"):
                    last_event = line[len("event:") :].lstrip()
                    if verbose:
                        _message(f"[SSE] event: {last_event}")
                elif line.startswith("data:"):
                    if last_event == "complete":
                        payload = line[len("data:") :].lstrip()
                        if verbose:
                            _message("[SSE] received complete payload")
                        break
    except httpx.TimeoutException:
        payload = None
    if payload is None:
        raise TimeoutError(
            f"SSE timed out after {timeout:g} seconds without `event: complete`.\n"
            f"URL: {url}\nEVENT_ID: {event_id}"
        )
    return payload


def causal_relations(
    sentence: str | Sequence[str],
    rel_mode: str = "auto",
    rel_threshold: float = 0.5,
    cause_decision: str = "cls+span",
    timeout: float = 10,
    verbose: bool = False,
) -> pd.DataFrame:
    """Extract causal relations from sentences (port of ``causal_relations()``).

    Parameters
    ----------
    sentence:
        One or more sentences to analyse.
    rel_mode:
        ``"auto"`` or ``"neural_only"``.
    rel_threshold:
        Threshold in [0, 1] for deciding whether a relation is causal.
    cause_decision:
        ``"cls_only"``, ``"span_only"`` or ``"cls+span"``.
    timeout:
        Seconds to wait for the Space to finish (Server-Sent Events).
    verbose:
        Print the URLs, status codes and event progression.

    Returns
    -------
    A DataFrame with ``sentence``, ``causal`` (the model's flag), ``cause`` and
    ``effect``: one row per detected relation, or one row with missing
    cause/effect for a sentence without relations. Rows are ordered by input
    sentence, then cause and effect.
    """
    sentences = [sentence] if isinstance(sentence, str) or sentence is None else list(sentence)
    # R: length(sentence) == 0 || all(trimws(sentence) == "") (NA makes all() NA)
    blank = [
        None if s is None else isinstance(s, str) and s.strip(" \t\r\n") == "" for s in sentences
    ]
    if len(sentences) == 0 or all(b is True for b in blank):
        return _empty()
    if False not in blank:
        raise ValueError("missing value where TRUE/FALSE needed")

    if not all(s is None or isinstance(s, str) for s in sentences):
        raise TypeError("`sentence` must be a non-empty character vector.")
    if not isinstance(rel_mode, str) or rel_mode not in ("auto", "neural_only"):
        raise ValueError("`rel_mode` must be one of: 'auto', 'neural_only'.")
    if (
        isinstance(rel_threshold, bool)
        or not isinstance(rel_threshold, int | float)
        or math.isnan(rel_threshold)
    ):
        raise TypeError("`rel_threshold` must be a single numeric value.")
    if rel_threshold < 0 or rel_threshold > 1:
        raise ValueError("`rel_threshold` must be in [0, 1].")
    if not isinstance(cause_decision, str) or cause_decision not in (
        "cls_only",
        "span_only",
        "cls+span",
    ):
        raise ValueError("`cause_decision` must be one of: 'cls_only', 'span_only', 'cls+span'.")
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, int | float)
        or math.isnan(timeout)
        or timeout <= 0
    ):
        raise ValueError("`timeout` must be a positive numeric scalar (seconds).")
    if not isinstance(verbose, bool):
        raise TypeError("`verbose` must be a single logical value (TRUE/FALSE).")

    rows: list[dict[str, Any]] = []
    for k, one in enumerate(sentences, start=1):
        if verbose:
            _message(f"=== Processing sentence {k}/{len(sentences)} ===")
        event_id = _post_enqueue(one, rel_mode, rel_threshold, cause_decision, verbose)
        payload = _get_until_complete(event_id, timeout, verbose)
        final_json = _unwrap_final_json(payload)
        rows.extend(_parse_relations(final_json, one))

    if not rows:
        return _empty()

    from metacheck._r.base import r_sort_key

    first_pos: dict[str, int] = {}
    for i, s in enumerate(sentences):
        first_pos.setdefault(s, i)

    def key(r: dict[str, Any]) -> tuple[Any, ...]:
        def na_last(v: Any) -> tuple[int, Any]:
            return (1, ()) if v is None else (0, r_sort_key(v))

        return (
            first_pos.get(r["sentence"], len(sentences)),
            na_last(r["cause"]),
            na_last(r["effect"]),
        )

    rows.sort(key=key)
    return pd.DataFrame(
        {
            "sentence": pd.Series([r["sentence"] for r in rows], dtype="string"),
            "causal": pd.Series([r["causal"] for r in rows], dtype="boolean"),
            "cause": pd.Series([r["cause"] for r in rows], dtype="string"),
            "effect": pd.Series([r["effect"] for r in rows], dtype="string"),
        }
    )
