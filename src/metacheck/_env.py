"""The one table of environment variables, and how they are read.

Every setting has a ``METACHECK_*`` name that is read first and, where it existed
before, a ``PYTACHECK_*`` name that keeps working. :data:`ENV_VARS` lists the names
of each setting in lookup order; the rest of the package reads them through
:func:`env_get` and :func:`env_lookup`. The few direct reads (the ``env=`` mapping in
``app/hosted.py``, ``packs.auth._secrets()`` for redaction and ``config.config_stamp()``
for the fingerprint) are listed in ``docs/ENVIRONMENT.md``.

The first name whose value is not blank (empty or only spaces) wins, and values of
different names are never combined. If both names of a pair are set to different
values, one line is logged per process and setting (values are never logged).
This module imports only the standard library and ``metacheck._logging``.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass

from metacheck._logging import get_logger

__all__ = [
    "ENV_VARS",
    "EnvVar",
    "env_get",
    "env_lookup",
    "env_name",
    "env_names",
    "fold_to_legacy",
]

#: the prefixes of a twin pair: the new name, then the old one
_NEW, _OLD = "METACHECK_", "PYTACHECK_"


@dataclass(frozen=True)
class EnvVar:
    """One setting: the variables that set it."""

    #: lookup order: the first non-blank value wins
    names: tuple[str, ...]
    #: informational: a column of the docs, and a test that no value is logged
    secret: bool = False
    #: R metacheck reads one of these names (reviewed in tests/foundation/test_env_r_names.py)
    shared_with_r: bool = False


#: setting key -> its variables. The order is the order of docs/ENVIRONMENT.md.
ENV_VARS: dict[str, EnvVar] = {
    "API_KEY": EnvVar(("METACHECK_API_KEY", "PYTACHECK_API_KEY"), secret=True),
    "API_MAX_CHECKS": EnvVar(("METACHECK_API_MAX_CHECKS", "PYTACHECK_API_MAX_CHECKS")),
    "BIBR_BACKEND": EnvVar(("METACHECK_BIBR_BACKEND", "PYTACHECK_BIBR_BACKEND")),
    "BIBR_URL": EnvVar(("METACHECK_BIBR_URL", "PYTACHECK_BIBR_URL")),
    "CACHE_DIR": EnvVar(("METACHECK_CACHE_DIR", "PYTACHECK_CACHE_DIR")),
    "CONFIG": EnvVar(("METACHECK_CONFIG", "PYTACHECK_CONFIG")),
    "DATA_DIR": EnvVar(("METACHECK_DATA_DIR", "PYTACHECK_DATA_DIR")),
    "EMAIL": EnvVar(("METACHECK_EMAIL", "PYTACHECK_EMAIL")),
    "GITHUB_TOKEN": EnvVar(
        ("METACHECK_GITHUB_TOKEN", "PYTACHECK_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"),
        secret=True,
    ),
    "GROBID_URL": EnvVar(("METACHECK_GROBID_URL", "PYTACHECK_GROBID_URL")),
    # R's name ranks last: it is shared with R on purpose, so a setting for this package wins
    "LLM_CACHE_DIR": EnvVar(
        ("PYTACHECK_LLM_CACHE_DIR", "METACHECK_LLM_CACHE_DIR"), shared_with_r=True
    ),
    "LLM_MAX_CALLS": EnvVar(("METACHECK_LLM_MAX_CALLS",), shared_with_r=True),
    "LLM_MODEL": EnvVar(("METACHECK_LLM_MODEL",), shared_with_r=True),
    "LLM_WORKERS": EnvVar(("METACHECK_LLM_WORKERS", "PYTACHECK_LLM_WORKERS")),
    "LOG": EnvVar(("METACHECK_LOG", "PYTACHECK_LOG")),
    "NO_SLEEP": EnvVar(("METACHECK_NO_SLEEP", "PYTACHECK_NO_SLEEP")),
    "PDFTOTEXT": EnvVar(("METACHECK_PDFTOTEXT", "PYTACHECK_PDFTOTEXT")),
    "PRESET": EnvVar(("METACHECK_PRESET", "PYTACHECK_PRESET")),
    "R_PARSER": EnvVar(("METACHECK_R_PARSER", "PYTACHECK_R_PARSER")),
    "RSCRIPT": EnvVar(("METACHECK_RSCRIPT", "PYTACHECK_RSCRIPT")),
    "R_SERIALIZE_VERSION": EnvVar(
        ("METACHECK_R_SERIALIZE_VERSION", "PYTACHECK_R_SERIALIZE_VERSION")
    ),
    "STORE_URL": EnvVar(("METACHECK_STORE_URL", "PYTACHECK_STORE_URL")),
    "VERBOSE": EnvVar(("METACHECK_VERBOSE", "PYTACHECK_VERBOSE")),
    # the hosted app: new since 0.4.0a1, so no old names
    "APP_AUTH": EnvVar(("METACHECK_APP_AUTH",)),
    "APP_COMMIT": EnvVar(("METACHECK_APP_COMMIT",)),
    "APP_HOSTS": EnvVar(("METACHECK_APP_HOSTS",)),
    "APP_JOB_TIMEOUT": EnvVar(("METACHECK_APP_JOB_TIMEOUT",)),
    "APP_TOKENS": EnvVar(("METACHECK_APP_TOKENS",), secret=True),
    "APP_USER_HEADER": EnvVar(("METACHECK_APP_USER_HEADER",)),
    # data_check's local concept classifier: new, so no old names
    "CONCEPTS": EnvVar(("METACHECK_CONCEPTS",)),
    "CONCEPT_MODEL": EnvVar(("METACHECK_CONCEPT_MODEL",)),
    "CONCEPT_THREADS": EnvVar(("METACHECK_CONCEPT_THREADS",)),
    "CONCEPT_THRESHOLD": EnvVar(("METACHECK_CONCEPT_THRESHOLD",)),
}

#: the keys whose clash was logged (once per process and key), and what guards it
_warned: set[str] = set()
_warned_lock = threading.Lock()


def env_names(key: str) -> tuple[str, ...]:
    """The variables of setting *key*, in lookup order."""
    return ENV_VARS[key].names


def env_name(key: str) -> str:
    """The variable docs tell people to set for *key* (the first name)."""
    return ENV_VARS[key].names[0]


def _note_clash(key: str, name: str, value: str, environ: Mapping[str, str]) -> None:
    """Log once that the pair's other name is set to another value and so is ignored.

    Only the pair of ``METACHECK_`` and ``PYTACHECK_`` names counts: ``GH_TOKEN`` and
    ``GITHUB_TOKEN`` are other tools' variables and the first one silently wins, as before.
    Only names go into the record, never a value.
    """
    var = ENV_VARS[key]
    pair = [n for n in var.names if n.startswith((_NEW, _OLD))]
    if not pair or name != pair[0]:
        return  # the higher name is blank, or not one of the pair: nothing is ignored
    for other in pair[1:]:
        found = environ.get(other)
        if found is None or not found.strip() or found.strip() == value.strip():
            continue
        with _warned_lock:
            if key in _warned:
                return
            _warned.add(key)
        # R's name ranks below ours, so a different R setting is expected there
        level = logging.DEBUG if var.names[0].startswith(_OLD) else logging.WARNING
        get_logger().log(level, "%s is set, so %s is ignored", name, other)
        return


def env_lookup(key: str, environ: Mapping[str, str] | None = None) -> tuple[str, str] | None:
    """``(name, value)`` for the first name of *key* whose value is not blank, or ``None``.

    The value is returned as it is set, not stripped: each caller keeps its own
    parsing. It is read at call time and never cached. *environ* defaults to
    ``os.environ``. If both names of a ``METACHECK_``/``PYTACHECK_`` pair are set to
    different values, one line is logged per process and key.
    """
    env = os.environ if environ is None else environ
    for name in ENV_VARS[key].names:
        value = env.get(name)
        if value is not None and value.strip():
            _note_clash(key, name, value, env)
            return name, value
    return None


def env_get(
    key: str, default: str | None = None, environ: Mapping[str, str] | None = None
) -> str | None:
    """The value of setting *key* (see :func:`env_lookup`), or *default*."""
    hit = env_lookup(key, environ)
    return default if hit is None else hit[1]


def fold_to_legacy(environ: MutableMapping[str, str] | None = None) -> list[str]:
    """Move every ``METACHECK_<K>`` twin into its ``PYTACHECK_<K>`` name; return the names removed.

    For test and parity harnesses only, which set only ``PYTACHECK_*`` names and read
    their folders back from them. A twin set in a developer's shell would otherwise
    shadow the harness's folders. A twin with a non-blank value replaces the old name's
    value, a blank one is dropped; afterwards every lookup gives what it gave before,
    and only old names exist. Settings whose first name is the old one
    (``LLM_CACHE_DIR``) or that have no old name, and ``GH_TOKEN``/``GITHUB_TOKEN``,
    are left alone. Nothing is logged.
    """
    env = os.environ if environ is None else environ
    removed = []
    for var in ENV_VARS.values():
        if len(var.names) < 2:
            continue
        new, old = var.names[:2]
        if not (new.startswith(_NEW) and old.startswith(_OLD)):
            continue
        if new[len(_NEW) :] != old[len(_OLD) :] or new not in env:
            continue
        value = env.pop(new)
        removed.append(new)
        if value.strip():
            env[old] = value
    return removed


def _reset_warned() -> None:
    """Forget which clashes were logged (tests only)."""
    with _warned_lock:
        _warned.clear()
