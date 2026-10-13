"""Browser sign-in to the hosted bibr service (OAuth device flow, RFC 8628).

``POST /device/code`` gives a short code to type in the browser, ``POST /device/token`` is
polled until the person has approved it and answers with a normal ``bibr_pat_...`` token.
A service without these endpoints (bibr-gate before the sign-in is deployed, ``bibr serve``,
the platform) raises :class:`LoginUnavailable`; the caller then asks for a pasted token.
It does not import gradio.
"""

from __future__ import annotations

import time
import webbrowser
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

__all__ = ["LoginFailed", "LoginUnavailable", "device_login", "service_for_login"]

GRANT = "urn:ietf:params:oauth:grant-type:device_code"
#: seconds between polls when the service names none; ``slow_down`` adds this much each time
DEFAULT_INTERVAL = 5.0
#: seconds a code lives when the service names none
DEFAULT_EXPIRES = 900.0
#: seconds to wait for one answer
REQUEST_TIMEOUT = 10.0
#: failed polls in a row before giving up
MAX_NETWORK_ERRORS = 3

_FAILED = {
    "access_denied": "The sign-in was refused in the browser.",
    "expired_token": "The sign-in code ran out before it was approved.",
    "token_limit": "This account already has the most tokens bibr allows. "
    "Revoke one on the tokens page of the bibr service, then try again.",
}


class LoginUnavailable(Exception):
    """The service has no browser sign-in (yet), or cannot be reached."""


class LoginFailed(Exception):
    """The sign-in was started but did not end with a token; the message says why."""


def service_for_login() -> str | None:
    """The address of the bibr service a token could come from, or ``None`` (none known, the
    wrong kind of service, or an address that must not get a token)."""
    from metacheck.app import bibr

    try:
        service = bibr.bibr_service()
    except ValueError:
        return None
    if service is None or service[1] != "bibr":
        return None
    url = service[0]
    # a service on this computer (bibr serve) needs no token at all
    return None if bibr._local(url) or not bibr._safe(url) else url


def _json(response: Any) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _number(value: object, default: float) -> float:
    return float(value) if isinstance(value, (int, float)) and value > 0 else default


def _openable(url: str) -> bool:
    return urlsplit(url).scheme in ("http", "https")


def device_login(
    base_url: str,
    say: Callable[[str], None],
    *,
    client: Any | None = None,
    open_url: Callable[[str], bool] = webbrowser.open,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    name: str = "metacheck",
) -> str:
    """Sign in through the browser and return the token.

    Raises :class:`LoginUnavailable` when the service has no sign-in or cannot be reached and
    :class:`LoginFailed` when the person refused it, the code expired or the account is full.
    """
    import httpx

    base = base_url.rstrip("/")
    own = client is None
    http = client or httpx.Client(timeout=REQUEST_TIMEOUT, follow_redirects=False)
    try:
        try:
            first = http.post(f"{base}/device/code", json={"name": name})
        except httpx.HTTPError as exc:
            raise LoginUnavailable(f"{base} could not be reached ({type(exc).__name__}).") from exc
        started = _json(first)
        needed = ("device_code", "user_code")
        if first.status_code != 200 or not all(isinstance(started.get(k), str) for k in needed):
            raise LoginUnavailable(
                f"{base} has no browser sign-in (it answered HTTP {first.status_code})."
            )
        device_code, user_code = started["device_code"], started["user_code"]
        link = str(
            started.get("verification_uri_complete") or started.get("verification_uri") or ""
        )
        interval = _number(started.get("interval"), DEFAULT_INTERVAL)
        deadline = clock() + _number(started.get("expires_in"), DEFAULT_EXPIRES)

        say("")
        say(f"Your sign-in code is: {user_code}")
        opened = _openable(link) and bool(open_url(link))
        if opened:
            say("The browser should open now. Check that it shows this code, then press Approve.")
        elif link:
            say(f"Open this page in a browser, check the code and press Approve:\n  {link}")
        say("Waiting for you to approve it (Ctrl+C to type a token instead)...")

        errors = 0
        while True:
            if clock() >= deadline:
                raise LoginFailed(_FAILED["expired_token"])
            sleep(interval)
            try:
                resp = http.post(
                    f"{base}/device/token", data={"grant_type": GRANT, "device_code": device_code}
                )
            except httpx.HTTPError as exc:
                errors += 1
                if errors >= MAX_NETWORK_ERRORS:
                    raise LoginFailed(
                        f"The connection to {base} was lost while waiting ({type(exc).__name__})."
                    ) from exc
                continue
            errors = 0
            body = _json(resp)
            token = body.get("access_token")
            if resp.status_code == 200 and isinstance(token, str) and token:
                return token
            error = body.get("error")
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += DEFAULT_INTERVAL
                continue
            if isinstance(error, str) and error in _FAILED:
                raise LoginFailed(_FAILED[error])
            raise LoginFailed(f"The sign-in ended unexpectedly (HTTP {resp.status_code}).")
    finally:
        if own:
            http.close()
