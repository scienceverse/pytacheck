"""API keys for the data repositories and databases metacheck reads (port of ``R/api-keys.R``).

A key is read from an option first and from an environment variable second
(:func:`osf_pat`, :func:`github_pat`, :func:`dryad_pat`, :func:`rbox_token`,
:func:`gitlab_pat`, :func:`openalex_key`). On a shared server (the hosted app and
API) the server's own keys are environment variables, read by every process the
server starts. A visitor's own key is set as an option inside that visitor's job
only (:func:`api_keys_use`), so for that job it takes the place of the server's
key, and it never reaches the process the other visitors share.

What went wrong with a key (the service refused it, or its limit was reached)
is written to a small JSON file in metacheck's state folder
(:func:`state_dir`), which every process of the same user on the computer
reads. A job started after another job used up the server's key therefore knows
it before sending anything, and the app can tell visitors that the key
metacheck provides has run out and that they can enter their own
(:func:`api_key_status`). Problems with a visitor's own key are not written
there: they concern that visitor's job only.

A key is never written to a log, a message, the state file or a file name. The
state file holds the service, the kind of problem, whose key it was
("user" or "server") and two times; the in-process memory of checked keys is
keyed by a digest of the key.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import warnings
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

__all__ = [
    "KEY_SERVICES",
    "api_key_status",
    "api_keys_use",
    "github_pat",
    "openalex_key",
    "rbox_token",
]

#: ResearchBox's limit for AsPredicted preregistrations is recorded apart from
#: its limit for boxes (see ``researchbox._rbox_api``); it names this limit
RBOX_ASPREDICTED_LIMIT = "ResearchBox's limit for AsPredicted preregistrations"
#: the state-file record of that limit
RBOX_ASPREDICTED_RECORD = "researchbox_aspredicted"


@dataclass(frozen=True)
class KeyService:
    """One service with a key: how it is named to a reader, set and made."""

    label: str
    option: str
    env: str
    url: str


#: The services for which a key is worth setting on a shared server: a key is
#: required (Dryad downloads, the ResearchBox API) or raises a limit a shared
#: server reaches (GitHub 60 -> 5,000 requests per hour; OSF 100 per hour ->
#: 10,000 per day; GitLab and OpenAlex).
KEY_SERVICES: dict[str, KeyService] = {
    "github": KeyService(
        "GitHub",
        "metacheck.github.pat",
        "GITHUB_PAT",
        "https://github.com/settings/personal-access-tokens/new",
    ),
    "osf": KeyService("OSF", "metacheck.osf.pat", "OSF_PAT", "https://osf.io/settings/tokens"),
    "dryad": KeyService(
        "Dryad", "metacheck.dryad.pat", "DRYAD_PAT", "https://datadryad.org/account"
    ),
    "researchbox": KeyService(
        "ResearchBox",
        "metacheck.researchbox.token",
        "RESEARCHBOX_API_KEY",
        "https://researchbox.org/api",
    ),
    "gitlab": KeyService(
        "GitLab",
        "metacheck.gitlab.pat",
        "GITLAB_PAT",
        "https://gitlab.com/-/user_settings/personal_access_tokens",
    ),
    "openalex": KeyService(
        "OpenAlex",
        "metacheck.openalex.key",
        "OPENALEX_API_KEY",
        "https://openalex.org/settings/api-key",
    ),
}

_DOMAINS = {
    "github": "github.com",
    "osf": "osf.io",
    "dryad": "datadryad.org",
    "researchbox": "researchbox.org",
    "gitlab": "gitlab.com",
    "openalex": "openalex.org",
}

_LOCK = threading.RLock()


def key_service_for_host(host: str | None) -> str | None:
    """The service whose key a request to *host* uses, or ``None``.

    Files on ``raw.githubusercontent.com`` are fetched without the GitHub key,
    so its limit is not the key's.
    """
    h = (host or "").lower()
    for service, domain in _DOMAINS.items():
        if h == domain or h.endswith("." + domain):
            return service
    return None


# ---------------------------------------------------------------------------
# the keys themselves
# ---------------------------------------------------------------------------


def _get_key(option: str, env: str) -> str:
    from metacheck.utils import get_option

    value = get_option(option)
    return value if value is not None else os.environ.get(env, "")


def _set_key(option: str, value: Any, what: str, name: str) -> str:
    from metacheck.utils import options

    if not isinstance(value, str):
        raise ValueError(f"Set {name} with a single string containing your {what}")
    options({option: value})
    return value


def openalex_key(key: str | None = None) -> str:
    """Port of R/db-crossref.R::openalex_key(): get or set the OpenAlex API key.

    OpenAlex works without a key, with a budget of US$0.10 per day: about 1,000
    searches, while looking up a work by its DOI or OpenAlex ID is free. A free
    key raises the budget to US$1 per day. Without an argument, the key set for
    this session, else ``OPENALEX_API_KEY`` (``""`` when neither is set). A key
    OpenAlex refuses is left out (see :func:`api_key_status`).
    """
    s = KEY_SERVICES["openalex"]
    if key is None:
        return _get_key(s.option, s.env)
    return _set_key(s.option, key, "OpenAlex key", "openalex_key")


def rbox_token(token: str | None = None) -> str:
    """Port of R/archive-researchbox.R::rbox_token(): get or set the ResearchBox API key.

    Without an argument, the key set for this session, else the
    ``RESEARCHBOX_API_KEY`` environment variable (``""`` when neither is set).
    With a string, sets it for the rest of the session. ResearchBox's API
    answers only requests with a key (make one at https://researchbox.org/api).
    """
    s = KEY_SERVICES["researchbox"]
    if token is None:
        return _get_key(s.option, s.env)
    return _set_key(s.option, token, "ResearchBox key", "rbox_token")


def github_pat(pat: str | None = None) -> str:
    """Port of R/archive-github.R::github_pat(): get or set a GitHub token.

    A token raises GitHub's limit from 60 to 5,000 requests per hour. Without a
    token set here, the token is found the way git finds one: the
    ``GITHUB_PAT_GITHUB_COM`` environment variable, else git's credential
    store. ``""`` when none is set.
    """
    from metacheck.utils import get_option, options

    s = KEY_SERVICES["github"]
    if pat is None:
        set_ = get_option(s.option) or ""
        if set_:
            return str(set_)
        from metacheck.archives.github import _github_token

        return _github_token() or ""
    if not isinstance(pat, str):
        raise ValueError("Set github_pat with a single string containing your GitHub token")
    options({s.option: pat})
    return pat


def key_get(service: str) -> str:
    """The key a request to *service* would use now (``""`` when none is set)."""
    if service == "github":
        return github_pat()
    if service == "osf":
        from metacheck.archives.osf_helpers import osf_pat

        return osf_pat()
    if service == "dryad":
        from metacheck.archives.dryad import dryad_pat

        return dryad_pat()
    if service == "gitlab":
        from metacheck.archives.gitlab import gitlab_pat

        return str(gitlab_pat())
    if service == "researchbox":
        return rbox_token()
    if service == "openalex":
        return openalex_key()
    return ""


def key_source(service: str) -> str:
    """Whose key a request to *service* uses.

    ``"user"`` for a key set as an option (by :func:`api_keys_use` or a setter
    such as ``osf_pat("...")``) that differs from the environment variable,
    ``"server"`` for the environment variable's key (Dryad: also its client id
    and secret), ``"none"`` without a key.
    """
    from metacheck.utils import get_option

    s = KEY_SERVICES[service]
    env = os.environ.get(s.env, "")
    opt = get_option(s.option) or ""
    if opt and opt != env:
        return "user"
    if env:
        return "server"
    if (
        service == "dryad"
        and os.environ.get("DRYAD_CLIENT_ID")
        and os.environ.get("DRYAD_CLIENT_SECRET")
    ):
        return "server"
    if service == "github" and key_get("github"):
        return "server"
    return "none"


# ---------------------------------------------------------------------------
# the state file
# ---------------------------------------------------------------------------


def state_dir() -> Path:
    """The folder for what metacheck keeps between sessions and shares between the
    processes of one user: key problems, ResearchBox's count of requests for boxes
    that could not be found.

    The ``metacheck.state.dir`` option, else ``METACHECK_STATE_DIR``, else the
    platform's state folder for metacheck. Unlike the caches it is not in the
    working directory: each job of the apps and the API runs in a folder of its
    own, which is removed when the job ends.
    """
    import platformdirs

    from metacheck._env import env_get
    from metacheck.utils import get_option

    configured = get_option("metacheck.state.dir") or env_get("STATE_DIR")
    if configured:
        return Path(str(configured))
    return Path(platformdirs.user_state_dir("pytacheck", "scienceverse"))


def _status_path() -> Path:
    return state_dir() / "key_status.json"


def _status_read() -> dict[str, dict[str, Any]]:
    path = _status_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def _status_write(status: Mapping[str, Any]) -> bool:
    """Written to a temporary file first and then renamed, so a reader never sees half of it."""
    path = _status_path()
    tmp: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix="key_status", suffix=".json", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(status, fh)
        os.replace(tmp, path)
        return True
    except OSError:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return False


def _now() -> datetime:
    return datetime.now(UTC)


def _to_utc(value: Any) -> datetime | None:
    """A time as an aware UTC ``datetime`` (``None`` for a missing one)."""
    if value is None or (isinstance(value, float) and value != value):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, int | float):
        return datetime.fromtimestamp(float(value), UTC)
    if isinstance(value, pd.Timestamp):
        return None if pd.isna(value) else value.to_pydatetime().astimezone(UTC)
    return None


def key_problem_record(
    service: str,
    problem: str,
    until: Any = None,
    source: str | None = None,
) -> None:
    """Record that *service* refused its key (``"rejected"``) or that its limit was
    reached (``"limit"``) until *until*.

    Not for a visitor's own key (see the top of this module). A rejected key is
    tried again after an hour, so a key replaced on the server is used again
    without anyone clearing the record.
    """
    src = key_source(service) if source is None else source
    if src == "user":
        return
    end = _to_utc(until)
    if problem == "rejected" or end is None:
        end = _now() + timedelta(hours=1)
    with _LOCK:
        status = _status_read()
        status[service] = {
            "problem": problem,
            "source": src,
            "until": end.timestamp(),
            "time": _now().timestamp(),
        }
        _status_write(status)


def key_problem_clear(service: str) -> None:
    """Remove the record about *service*, if there is one."""
    with _LOCK:
        status = _status_read()
        if service not in status:
            return
        del status[service]
        _status_write(status)


def key_problem(service: str, source: str | None = None) -> dict[str, Any] | None:
    """The current problem of the key *service* uses now, or ``None``.

    A record about another source (a key was added or removed since) or whose
    time has passed does not count. The record has ``problem``, ``source``,
    ``until`` (an aware datetime, or ``None``) and ``time``.
    """
    src = key_source(service) if source is None else source
    rec = _status_read().get(service)
    if rec is None or src == "user":
        return None
    if rec.get("source") != src:
        return None
    until = _to_utc(rec.get("until"))
    if until is not None and until <= _now():
        return None
    return {
        "problem": rec.get("problem"),
        "source": rec.get("source"),
        "until": until,
        "time": _to_utc(rec.get("time")),
    }


# ---------------------------------------------------------------------------
# messages
# ---------------------------------------------------------------------------


def key_problem_message(
    service: str,
    problem: str,
    until: Any = None,
    source: str | None = None,
    shared: bool | None = None,
    limit: str | None = None,
) -> str:
    """The sentence shown to the user for a key problem.

    *shared* is true in the apps and the API (see :func:`api_keys_use`), where
    the key is metacheck's, not the user's own. *limit* names the limit that
    was reached, for a service with more than one (ResearchBox's limit for
    AsPredicted preregistrations).
    """
    from metacheck.utils import get_option

    s = KEY_SERVICES[service.split("_")[0] if service not in KEY_SERVICES else service]
    src = key_source(service) if source is None and service in KEY_SERVICES else (source or "none")
    if shared is None:
        shared = bool(get_option("metacheck.keys.shared"))
    lim = limit or f"{s.label}'s limit"
    end = _to_utc(until)
    when = f" until {end.strftime('%Y-%m-%d %H:%M')} UTC" if end is not None else ""
    if src == "user":
        whose = f"Your {s.label} key"
    elif src == "server":
        whose = (
            f"The {s.label} key provided by metacheck"
            if shared
            else f"The {s.label} key in {s.env}"
        )
    else:
        whose = f"{s.label}'s limit for requests without a key"
    if problem == "rejected":
        what = f"{whose} was rejected by {s.label}; it may have expired or been withdrawn."
    elif src == "none":
        what = f"{whose} was reached{when}."
    else:
        what = f"{whose} has reached {lim}{when}."
    if src == "user":
        ask = f"Check the key, or make a new one at {s.url}"
    else:
        ask = f"Please provide your own {s.label} key to continue (you can make one at {s.url})"
    return f"{what} {ask}"


def key_limit_reached(service: str, until: Any = None) -> str:
    """Record that the limit of *service* was reached and return the message for it,
    prefixed with ``"API rate limit exhausted: "`` like ``download_repo_files()``'s own
    rate-limit errors."""
    src = key_source(service)
    key_problem_record(service, "limit", until, src)
    return "API rate limit exhausted: " + key_problem_message(service, "limit", until, src)


# ---------------------------------------------------------------------------
# using a key
# ---------------------------------------------------------------------------

#: Keys already checked in this process (in memory only, never on disk): each key
#: is checked once per process, see :func:`key_use`. The memo is keyed by a digest.
_CHECKED: dict[str, bool | None] = {}


def _memo(ident: str, key: str) -> str:
    return ident + ":" + hashlib.sha256(key.encode("utf-8")).hexdigest()


def key_remember(ident: str, key: str, ok: bool | None) -> None:
    """Remember that the key was checked (``True``), refused (``False``) or could not be."""
    _CHECKED[_memo(ident, key)] = ok


def key_use(
    service: str,
    key: str | None = None,
    check: Callable[[str], bool | None] | None = None,
    ident: str | None = None,
) -> str:
    """The key to send to *service*: its current key, or ``""`` when there is none or
    the service refused it.

    A key the service refuses makes it answer 401 even for public records (OSF,
    GitHub, GitLab, OpenAlex, Dataverse), so one wrong or expired key would stop
    every request. Each key is therefore checked once per process with *check*
    (a function of the key returning True, False for refused, or None when the
    check itself failed), and a refused key is left out, with a warning, so
    requests go without a key.
    """
    value = key_get(service) if key is None else key
    value = value or ""
    if not value or check is None:
        return value
    name = ident or service
    memo = _memo(name, value)
    if memo in _CHECKED:
        ok = _CHECKED[memo]
    else:
        try:
            ok = check(value)
        except Exception:
            ok = None
        _CHECKED[memo] = ok
        known = service in KEY_SERVICES
        if ok is False and known:
            src = key_source(service)
            key_problem_record(service, "rejected", source=src)
            warnings.warn(
                key_problem_message(service, "rejected", source=src)
                + " Continuing without the key.",
                stacklevel=3,
            )
        elif ok is False:
            warnings.warn(f"The {name} key was rejected; continuing without the key.", stacklevel=3)
        elif (
            ok is True
            and known
            and (_status_read().get(service) or {}).get("problem") == "rejected"
        ):
            key_problem_clear(service)
    return "" if ok is False else value


def key_check_request(url: str, headers: Mapping[str, str]) -> bool | None:
    """One request to *url* with *headers*: True for 200, False for 401/403, None for
    anything else (no connection, the service down)."""
    from metacheck import http

    try:
        resp = http.request(
            "GET", url, headers={**headers, "User-Agent": "metacheck"}, timeout=15.0
        )
    except Exception:
        return None
    status = getattr(resp, "status_code", None)
    if status == 200:
        return True
    if status in (401, 403):
        return False
    return None


# ---------------------------------------------------------------------------
# the public functions
# ---------------------------------------------------------------------------


def api_key_status() -> pd.DataFrame:
    """Port of R/api-keys.R::api_key_status(): check the API keys metacheck uses.

    One row per service: ``service``, ``label``, ``key`` (``"user"``,
    ``"server"`` or ``"none"``, see :func:`api_keys_use`), ``problem`` (missing,
    ``"rejected"`` or ``"limit"``), ``until`` (when a limit ends), ``url``
    (where a key is made) and ``message`` (missing without a problem). On a
    shared server the apps show the ``message`` to visitors so they can enter
    their own key.
    """
    rows: list[dict[str, Any]] = []
    for service, s in KEY_SERVICES.items():
        src = key_source(service)
        p = key_problem(service, src)
        limit = None
        if p is None and service == "researchbox":
            p = key_problem(RBOX_ASPREDICTED_RECORD, src)
            if p is not None:
                limit = RBOX_ASPREDICTED_LIMIT
        rows.append(
            {
                "service": service,
                "label": s.label,
                "key": src,
                "problem": None if p is None else p["problem"],
                "until": pd.NaT if p is None or p["until"] is None else pd.Timestamp(p["until"]),
                "url": s.url,
                "message": None
                if p is None
                else key_problem_message(service, p["problem"], p["until"], src, limit=limit),
            }
        )
    frame = pd.DataFrame(rows)
    for col in ("service", "label", "key", "problem", "url", "message"):
        frame[col] = frame[col].astype("string")
    frame["until"] = pd.to_datetime(frame["until"], utc=True)
    return frame


def api_keys_use(keys: Mapping[str, Any] | None = None, shared: bool = False) -> dict[str, Any]:
    """Port of R/api-keys.R::api_keys_use(): use your own API keys.

    Sets keys for the rest of the session, in place of the keys set as
    environment variables. The hosted app and API call this inside each
    visitor's own job with the keys the visitor entered, so the keys are used
    for that job only. *keys* is a mapping named by service: ``github``,
    ``osf``, ``dryad``, ``researchbox``, ``gitlab``, ``openalex``. Empty and
    missing keys are skipped, so the key set as an environment variable is used
    for those services. *shared* is true when the environment-variable keys
    belong to a shared server, so that messages call them "the key provided by
    metacheck". Returns the previous values of the options that were set, to
    restore them with :func:`metacheck.utils.options`.
    """
    from metacheck.utils import options

    given = dict(keys or {})
    unknown = [k for k in given if k not in KEY_SERVICES]
    if given and unknown:
        raise ValueError(
            "Name each key by its service: " + ", ".join(KEY_SERVICES)  # no key in the message
        )
    opts: dict[str, Any] = {}
    for name, value in given.items():
        if isinstance(value, str) and value.strip():
            opts[KEY_SERVICES[name].option] = value.strip()
    opts["metacheck.keys.shared"] = bool(shared)
    return options(opts)
