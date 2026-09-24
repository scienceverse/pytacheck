"""PubPeer comments (port of ``R/db-pubpeer.R``)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pytacheck._r.base import trimws
from pytacheck._r.regex import is_na
from pytacheck.db._utils import as_vector, r_dollar, records_frame, resp_body_json, unlist

if TYPE_CHECKING:
    import pandas as pd

__all__ = ["pubpeer_comments"]

_URL = "https://pubpeer.com/v3/publications?devkey=PubPeerZotero"


def _request_body(dois: list[str]) -> str:
    """``jsonlite::toJSON(list(dois = dois), auto_unbox = TRUE)``: one DOI is unboxed."""
    value: Any = dois[0] if len(dois) == 1 else dois
    return json.dumps({"dois": value}, ensure_ascii=False, separators=(",", ":"))


def _feedback_record(fb: Any) -> dict[str, Any]:
    users = r_dollar(fb, "users")
    user_list = (
        unlist(users) if isinstance(users, list | dict) else ([] if users is None else [users])
    )
    trimmed = trimws([u if isinstance(u, str) else str(u) for u in user_list])
    rec = {
        "doi": r_dollar(fb, "id"),
        "total_comments": r_dollar(fb, "total_comments"),
        "url": r_dollar(fb, "url"),
        "users": ", ".join(trimmed),
    }
    return {k: v for k, v in rec.items() if v is not None}


def pubpeer_comments(doi: Any) -> pd.DataFrame | None:
    """Get PubPeer comments for DOIs (port of ``pubpeer_comments()``).

    Returns one row per DOI (in input order) with ``doi``,
    ``total_comments`` (0 when PubPeer has none), ``url`` and ``users``, or
    ``None`` when the request fails (e.g. no DOIs).
    """
    import pandas as pd

    from pytacheck import http

    values = as_vector(doi)
    lower = [None if is_na(v) else str(v).lower() for v in values]
    body = _request_body([d for d in lower if d is not None])

    resp = http.request(
        "POST",
        _URL,
        content=body.encode("utf-8"),
        headers={"Content-Type": "application/json;charset=UTF-8"},
    )
    if resp is None:
        raise ConnectionError(f"Failed to perform HTTP request to {_URL}")
    if resp.status_code != 200:
        return None  # Request failed

    data = resp_body_json(resp)
    feedbacks = r_dollar(data, "feedbacks") or []
    if isinstance(feedbacks, dict):
        feedbacks = list(feedbacks.values())
    pp_fb = [_feedback_record(fb) for fb in feedbacks]
    pp_fb = [r for r in pp_fb if r]

    if not pp_fb:
        if not values:
            raise ValueError("arguments imply differing number of rows: 0, 1")
        return pd.DataFrame(
            {
                "doi": pd.array(lower, dtype="string"),
                "total_comments": pd.array([0.0] * len(values), dtype="float64"),
                "url": pd.array([None] * len(values), dtype="string"),
                "users": pd.array([None] * len(values), dtype="string"),
            }
        )

    # data.frame(doi = tolower(doi)) |> left_join(pp_fb, by = "doi")
    fb_frame = records_frame(pp_fb)
    fb_cols = [c for c in fb_frame.columns if c != "doi"]
    by_doi: dict[Any, list[int]] = {}
    for i, d in enumerate(fb_frame["doi"].tolist() if "doi" in fb_frame.columns else []):
        by_doi.setdefault(None if is_na(d) else d, []).append(i)
    rows: list[dict[str, Any]] = []
    for d in lower:
        hits = by_doi.get(d, [])
        if not hits:
            rows.append({"doi": d})
        rows.extend({"doi": d, **{c: fb_frame[c].iat[i] for c in fb_cols}} for i in hits)
    if len(rows) != len(values):
        raise ValueError(f"replacement has {len(values)} rows, data has {len(rows)}")
    for row, original in zip(rows, values, strict=True):
        row["doi"] = None if is_na(original) else original
        for c in fb_cols:
            if c in row and is_na(row[c]):
                row[c] = None
    out = records_frame(rows, columns=["doi", *fb_cols])
    if "total_comments" in out.columns:
        tc = out["total_comments"]
        if tc.isna().any():
            out["total_comments"] = tc.astype("float64").fillna(0.0)
    return out
