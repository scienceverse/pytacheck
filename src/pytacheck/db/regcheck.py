"""RegCheck API client (port of ``R/db-regcheck.R``).

RegCheck (https://github.com/JamieCummins/regcheck) compares a study
registration with the corresponding published paper using an LLM, one
comparison per "dimension" (e.g. sample size, hypotheses, exclusion
criteria). A minimal server can run locally with Ollama (see
:mod:`pytacheck.db.regcheck_local`), so comparisons can run without any data
leaving your machine.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from pytacheck.db._utils import message, paste_unlist, r_dollar

if TYPE_CHECKING:
    import httpx
    import pandas as pd

__all__ = ["RegCheckError", "regcheck_base_url", "regcheck_compare", "regcheck_tidy"]

# default server locations per client
_REGCHECK_HOSTED_URL = "https://preregpt-8584b32c9141.herokuapp.com"
_REGCHECK_LOCAL_URL = "http://localhost:8000"
_REGCHECK_DEFAULT_TOKEN = "metacheck-local"  # noqa: S105 - public token of the local server
_CLIENTS = ("ollama", "groq", "openai", "deepseek")
_BOOK = "http://www.scienceverse.org/metacheck_book/"

_TIDY_COLUMNS = (
    ("dimension", "dimension"),
    ("deviation_judgement", "deviation_judgement"),
    ("paper_summary", "paper_content_summary"),
    ("prereg_summary", "registration_content_summary"),
    ("deviation_information", "deviation_information"),
    ("paper_quotes", "paper_content_quotes"),
    ("prereg_quotes", "registration_content_quotes"),
)

# greek letters and scientific symbols -> readable latin-1 equivalents
_SANITIZE_MAP = {
    "α": "alpha",
    "β": "beta",
    "γ": "gamma",
    "δ": "delta",
    "Δ": "Delta",
    "ε": "epsilon",
    "ζ": "zeta",
    "η": "eta",
    "θ": "theta",
    "ι": "iota",
    "κ": "kappa",
    "λ": "lambda",
    "μ": "mu",
    "ν": "nu",
    "ξ": "xi",
    "π": "pi",
    "ρ": "rho",
    "σ": "sigma",
    "Σ": "Sigma",
    "τ": "tau",
    "φ": "phi",
    "υ": "upsilon",
    "χ": "chi",
    "ψ": "psi",
    "ω": "omega",
    "Ω": "Omega",
    "²": "2",
    "³": "3",
    "⁰": "0",
    "¹": "1",
    "≤": "<=",
    "≥": ">=",
    "≠": "!=",
    "≈": "~",
    "×": "x",
    "·": "*",
    "−": "-",
    "±": "+/-",
    "–": "-",
    "—": "-",
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    "…": "...",
    "°": " degrees",
}


class RegCheckError(RuntimeError):
    """An error talking to a RegCheck server (R's ``stop()`` in the client)."""


def regcheck_base_url(client: str = "ollama", base_url: str | None = None) -> str:
    """RegCheck server URL for a client (port of ``regcheck_base_url()``).

    An explicit *base_url* wins, then the ``REGCHECK_BASE_URL`` environment
    variable, then a client default: the local server
    (``http://localhost:8000``) for ``"ollama"`` and the hosted RegCheck app
    for the API clients. Trailing slashes are removed.
    """
    url = base_url or ""
    if not url:
        url = os.environ.get("REGCHECK_BASE_URL", "")
    if not url:
        url = _REGCHECK_LOCAL_URL if client == "ollama" else _REGCHECK_HOSTED_URL
    return url.rstrip("/")


def _match_client(client: str | Sequence[str] | None) -> str:
    """``match.arg(client)`` with metacheck's friendly error (``NULL`` is the first client)."""
    if client is None:
        return _CLIENTS[0]
    shown = client
    if not isinstance(client, str):
        values = list(client)
        if values == list(_CLIENTS):
            return _CLIENTS[0]
        # stop() pastes every element of the vector into the message
        shown = "".join(str(v) for v in values)
        client = values[0] if len(values) == 1 else ""
    if client in _CLIENTS:
        return client
    hits = [c for c in _CLIENTS if client and c.startswith(client)]
    if len(hits) == 1:
        return hits[0]
    raise ValueError(
        f"Unknown RegCheck client '{shown}'. "
        'Use one of: "ollama" (local server), "groq", "openai", or "deepseek".\n'
        f"See {_BOOK} for setup instructions."
    )


def _regcheck_sanitize(text: str | None) -> str | None:
    """Make text latin-1 safe for the RegCheck server (port of ``.regcheck_sanitize()``).

    Greek letters and common scientific symbols become readable equivalents
    (``α`` -> ``alpha``, typographic quotes -> straight quotes); any other
    character latin-1 cannot represent is dropped.
    """
    if text is None:
        return None
    for ch, repl in _SANITIZE_MAP.items():
        text = text.replace(ch, repl)
    return text.encode("latin-1", errors="ignore").decode("latin-1")


def _http_error(resp: httpx.Response) -> RegCheckError:
    reason = resp.reason_phrase or ""
    return RegCheckError(f"HTTP {resp.status_code} {reason}.".replace(" .", "."))


def _regcheck_submit(
    url: str,
    api_token: str,
    paper_text: str,
    prereg_text: str | None = None,
    registration_id: str | None = None,
    client: str = "ollama",
    dimensions: pd.DataFrame | None = None,
    reasoning_effort: str = "medium",
) -> Any:
    """Submit a RegCheck comparison (port of ``.regcheck_submit()``).

    Tries the JSON text endpoint (``POST /api/v1/comparisons/text``) and
    falls back to the multipart endpoint (``POST /api/v1/comparisons``) with
    the texts as .txt uploads when the server does not have it (404/405).
    Returns the parsed creation response (``task_id``, ``status_url``, ...).
    """
    from pytacheck import http
    from pytacheck.db._utils import resp_body_json

    body: dict[str, Any] = {
        "paper_text": paper_text,
        "client": client,
        "reasoning_effort": reasoning_effort,
        "append_previous_output": True,
        "multiple_experiments": False,
    }
    if prereg_text is not None:
        body["registration_text"] = prereg_text
    if registration_id is not None:
        body["registration_id"] = registration_id
    if dimensions is not None:
        body["dimensions"] = [
            {"dimension": d, "definition": f}
            for d, f in zip(dimensions["dimension"], dimensions["definition"], strict=True)
        ]

    resp = http.request(
        "POST",
        f"{url}/api/v1/comparisons/text",
        headers={"Authorization": f"Bearer {api_token}"},
        json=body,
        max_tries=1,
    )
    if resp is None:
        if client == "ollama":
            raise RegCheckError(
                f"Could not connect to the local RegCheck server at {url}.\n"
                "Start it first with regcheck_start_local(), then try again.\n"
                f"See {_BOOK} for setup instructions."
            )
        raise RegCheckError(f"Failed to perform HTTP request to {url}.")
    if resp.status_code == 401:
        if client == "ollama":
            raise RegCheckError(
                "The local RegCheck server rejected the API token.\n"
                "This should not happen with regcheck_start_local() -- "
                "try stopping and restarting the server with regcheck_start_local()."
            )
        raise RegCheckError(
            "The RegCheck server rejected your API token (401 Unauthorized).\n"
            "Check that REGCHECK_API_TOKEN in your .Renviron is correct.\n"
            f"See {_BOOK} for details."
        )
    if resp.status_code in (404, 405):
        # server has no /text endpoint (e.g. the local ollama fork):
        # fall back to the multipart endpoint with .txt uploads
        resp = _regcheck_submit_multipart(
            url,
            api_token,
            paper_text,
            prereg_text=prereg_text,
            registration_id=registration_id,
            client=client,
            dimensions=dimensions,
            reasoning_effort=reasoning_effort,
        )
    elif resp.status_code >= 400:
        raise _http_error(resp)
    return resp_body_json(resp)


def _regcheck_submit_multipart(
    url: str,
    api_token: str,
    paper_text: str,
    prereg_text: str | None = None,
    registration_id: str | None = None,
    client: str = "ollama",
    dimensions: pd.DataFrame | None = None,
    reasoning_effort: str = "medium",
) -> httpx.Response:
    """Submit via the multipart endpoint (port of ``.regcheck_submit_multipart()``)."""
    from pytacheck import http

    files: dict[str, tuple[str, bytes, str]] = {
        "paper": ("regcheck_paper.txt", (paper_text + "\n").encode("utf-8"), "text/plain"),
    }
    data: dict[str, str] = {
        "client": client,
        "reasoning_effort": reasoning_effort,
        "append_previous_output": "yes",
        "multiple_experiments": "no",
    }
    if prereg_text is not None:
        files["registration_file"] = (
            "regcheck_prereg.txt",
            (prereg_text + "\n").encode("utf-8"),
            "text/plain",
        )
    if registration_id is not None:
        data["registration_id"] = registration_id
    if dimensions is not None:
        # the multipart endpoint takes dimensions as a JSON array of
        # {dimension, definition} objects
        data["dimensions"] = json.dumps(
            [
                {"dimension": d, "definition": f}
                for d, f in zip(dimensions["dimension"], dimensions["definition"], strict=True)
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
    resp = http.request(
        "POST",
        f"{url}/api/v1/comparisons",
        headers={"Authorization": f"Bearer {api_token}"},
        data=data,
        files=files,
        max_tries=1,
    )
    if resp is None:
        raise RegCheckError(f"Failed to perform HTTP request to {url}.")
    if resp.status_code >= 400:
        raise _http_error(resp)
    return resp


def _regcheck_poll(
    url: str, api_token: str, task_id: str, poll_interval: float = 5, timeout: float = 3600
) -> Any:
    """Poll a RegCheck task until it completes (port of ``.regcheck_poll()``).

    Returns the task result (a dict with an ``items`` list).
    """
    from pytacheck import http
    from pytacheck.db._utils import resp_body_json

    status_url = f"{url}/api/v1/comparisons/{task_id}"
    deadline = time.monotonic() + timeout
    while True:
        http.sleep(poll_interval)
        resp = http.request(
            "GET", status_url, headers={"Authorization": f"Bearer {api_token}"}, max_tries=1
        )
        if resp is None:
            raise RegCheckError(f"Failed to perform HTTP request to {status_url}.")
        if resp.status_code >= 400:
            raise _http_error(resp)
        status = resp_body_json(resp)
        state = _or_default(r_dollar(status, "state"), "unknown")
        if state == "success":
            message("RegCheck comparison complete.")
            return r_dollar(status, "result")
        if state == "failure":
            msg = _or_default(r_dollar(status, "status"), "unknown error")
            raise RegCheckError(f"RegCheck comparison failed: {msg}")
        if time.monotonic() > deadline:
            raise RegCheckError(f"Timed out waiting for RegCheck task {task_id}")
        done = _or_default(r_dollar(status, "processed_dimensions"), 0)
        total = _or_default(r_dollar(status, "total_dimensions"), 0)
        message(f"RegCheck running ({done}/{total} dimensions)")


def _or_default(value: Any, default: Any) -> Any:
    """R's ``value %||% default`` (only ``NULL`` is replaced)."""
    return default if value is None else value


def regcheck_tidy(result: Any) -> pd.DataFrame:
    """Tidy a raw RegCheck result into a data frame (port of ``regcheck_tidy()``).

    One row per compared dimension with columns ``dimension``,
    ``deviation_judgement``, ``paper_summary``, ``prereg_summary``,
    ``deviation_information``, ``paper_quotes`` and ``prereg_quotes``;
    missing fields are ``NA``.
    """
    import pandas as pd

    items = r_dollar(result, "items") or []
    if isinstance(items, dict):
        items = list(items.values())
    data: dict[str, list[Any]] = {col: [] for col, _ in _TIDY_COLUMNS}
    for item in items:
        for col, key in _TIDY_COLUMNS:
            value = r_dollar(item, key)
            if isinstance(value, list | dict):
                value = paste_unlist(value, collapse="; ")
            data[col].append(value)
    return pd.DataFrame(
        {col: pd.array(values, dtype="string") for col, values in data.items()},
        index=pd.RangeIndex(len(items)),
    )


def regcheck_compare(
    paper_text: Any,
    prereg_text: str | None = None,
    registration_id: str | None = None,
    client: str | Sequence[str] = _CLIENTS,
    base_url: str | None = None,
    api_token: str | None = None,
    dimensions: pd.DataFrame | None = None,
    reasoning_effort: str = "medium",
    poll_interval: float = 5,
    timeout: float = 3600,
) -> pd.DataFrame:
    """Compare a preregistration with a paper via RegCheck (port of ``regcheck_compare()``).

    Sends the paper text and the registration text (or a clinical trial
    registration ID) to a RegCheck server, which compares them dimension by
    dimension with an LLM, polls until the comparison completes and returns
    :func:`regcheck_tidy` of the result (the raw result is in
    ``frame.attrs["regcheck_result"]``).

    By default this uses the local server with Ollama (``client="ollama"``,
    ``http://localhost:8000``, started with ``regcheck_start_local()``), so
    no text leaves your machine; ``"groq"``, ``"openai"`` or ``"deepseek"``
    use the hosted RegCheck app and send the texts to it and the LLM
    provider. Every server needs an API token: ``REGCHECK_API_TOKEN`` (the
    local server's built-in token is used automatically for ``"ollama"``).
    """
    client = _match_client(client)

    # resolve API token: for local ollama use, fall back to the built-in token
    if not api_token:
        api_token = os.environ.get("REGCHECK_API_TOKEN", "")
    if not api_token:
        if client == "ollama":
            api_token = _REGCHECK_DEFAULT_TOKEN
        else:
            raise RegCheckError(
                f"RegCheck requires an API token for the '{client}' client.\n"
                "Set REGCHECK_API_TOKEN in your .Renviron:\n"
                '  usethis::edit_r_environ()  # add: REGCHECK_API_TOKEN="your_token"\n'
                f"See {_BOOK} for details."
            )
    from pytacheck._r.base import trimws

    # trimws() strips only [ \t\r\n]
    if not isinstance(paper_text, str) or not trimws(paper_text):
        raise ValueError("paper_text must be a single non-empty string")
    has_prereg = prereg_text is not None and bool(trimws(str(prereg_text)))
    has_reg_id = registration_id is not None and bool(trimws(str(registration_id)))
    if has_prereg == has_reg_id:
        raise ValueError("Provide exactly one of prereg_text or registration_id")

    url = regcheck_base_url(client, base_url)

    # the RegCheck server's text-to-PDF step only supports latin-1
    paper_text = _regcheck_sanitize(paper_text) or ""
    if has_prereg:
        prereg_text = _regcheck_sanitize(prereg_text)

    created = _regcheck_submit(
        url,
        api_token,
        paper_text=paper_text,
        prereg_text=prereg_text if has_prereg else None,
        registration_id=registration_id if has_reg_id else None,
        client=client,
        dimensions=dimensions,
        reasoning_effort=reasoning_effort,
    )
    task_id = r_dollar(created, "task_id")
    message(f"RegCheck task {task_id} queued on {url}")

    result = _regcheck_poll(
        url, api_token, str(task_id), poll_interval=poll_interval, timeout=timeout
    )
    tidy = regcheck_tidy(result)
    tidy.attrs["regcheck_result"] = result
    return tidy
