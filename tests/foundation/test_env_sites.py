"""Each setting is read through the environment table at its real call site.

One accessor per key of ``metacheck._env.ENV_VARS`` calls the code that reads the
setting and returns what it observed. For each key:

* each of its names set alone gives the value set (so ``METACHECK_<K>`` alone is
  read, and ``PYTACHECK_<K>`` alone is read as before);
* spaces only under every name mean unset, as any blank value (empty or spaces
  only) does: the call site gives its default. For the folder keys that default is
  the path a fake platformdirs returns (under ``tmp_path``), never a real folder.

The accessors do not depend on the machine: no variable of the table is set unless
the case sets it, no ``shutil.which`` hits, a fake platformdirs, and the ``CONFIG``
accessor works in ``tmp_path`` as its home folder.

A test fails if a key of ``ENV_VARS`` has no accessor. Keys read at more than one place
(``CACHE_DIR``, ``DATA_DIR``, ``RSCRIPT``, ``NO_SLEEP``) have further accessors in
``EXTRA_SITES``, which get the same three cases.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import platformdirs
import pytest

from metacheck import _env, utils
from metacheck._env import ENV_VARS, env_names
from metacheck._logging import get_logger


@dataclass
class Ctx:
    """What an accessor may use: the test's ``tmp_path`` and ``monkeypatch``."""

    tmp_path: Path
    monkeypatch: pytest.MonkeyPatch

    def platform(self, kind: str) -> Path:
        """The folder the fake platformdirs returns for ``user_<kind>_dir``."""
        return self.tmp_path / "platform" / kind

    def exe(self, name: str, folder: str = "bin") -> Path:
        """An executable file ``<tmp>/<folder>/<name>`` (it exists, as a check for one needs)."""
        path = self.tmp_path / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n")
        path.chmod(0o755)
        return path

    def no_config_files(self) -> None:
        self.monkeypatch.delenv("METACHECK_CONFIG", raising=False)
        self.monkeypatch.setenv("PYTACHECK_CONFIG", "none")


@dataclass(frozen=True)
class Site:
    """How to observe one setting at its call site."""

    #: a value to set under one name
    value: str
    #: another valid value, set under the lower names when the first name holds ``value``
    other: str
    #: prepares what the call site needs and returns what it observed
    read: Callable[[Ctx], Any]
    #: what ``read`` returns with only the variable *name* set to ``value``
    expect: Callable[[Ctx, str], Any]
    #: what ``read`` returns with every name of the key set to spaces only
    spaces: Callable[[Ctx], Any]


# -- the accessors --------------------------------------------------------------------


def _api() -> Any:
    pytest.importorskip("fastapi")
    from metacheck.api import app

    return app


def _llm_setup(ctx: Ctx) -> dict[str, Any]:
    """The model and call limit ``serve`` sets up, recorded instead of applied."""
    import metacheck.llm

    seen: dict[str, Any] = {}

    def recorder(name: str) -> Callable[..., Any]:
        def record(*args: Any) -> Any:
            if args:
                seen[name] = args[0]
            return seen.get(name)

        return record

    for name in ("llm_use", "llm_model", "llm_max_calls"):
        ctx.monkeypatch.setattr(metacheck.llm, name, recorder(name))
    ctx.monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    _api()._configure_llm()
    return seen


def _bibr_configured(ctx: Ctx) -> tuple[str, str] | None:
    from metacheck.app import bibr

    return bibr._configured()


def _read_bibr_backend(ctx: Ctx) -> Any:
    ctx.monkeypatch.delenv("METACHECK_BIBR_URL", raising=False)
    ctx.monkeypatch.setenv("PYTACHECK_BIBR_URL", "https://bibr.example.org")
    return _bibr_configured(ctx)


def _read_bibr_url(ctx: Ctx) -> Any:
    for name in env_names("BIBR_BACKEND"):
        ctx.monkeypatch.delenv(name, raising=False)
    return _bibr_configured(ctx)


def _read_cache_dir(ctx: Ctx) -> Any:
    from metacheck.config import cache_dir

    return cache_dir()


def _read_data_dir(ctx: Ctx) -> Any:
    from metacheck.config import data_dir

    return data_dir()


def _read_config(ctx: Ctx) -> Any:
    from metacheck.config import config_files

    ctx.monkeypatch.chdir(ctx.tmp_path)
    ctx.monkeypatch.setenv("HOME", str(ctx.tmp_path))
    ctx.monkeypatch.setenv("USERPROFILE", str(ctx.tmp_path))
    user = ctx.platform("config") / "config.json"
    user.parent.mkdir(parents=True, exist_ok=True)
    user.write_text("{}")
    return config_files()


def _read_email(ctx: Ctx) -> Any:
    from metacheck import config

    ctx.monkeypatch.delitem(config._state, "email", raising=False)
    return config.email()


def _read_github_token(ctx: Ctx) -> Any:
    from metacheck.packs.auth import github_token, redact, token_var

    return token_var(), github_token(), redact("sent ghp_from_the_table here")


def _read_preset(ctx: Ctx) -> Any:
    from metacheck.presets import default_preset

    ctx.no_config_files()
    return default_preset(use_config=True)


def _read_store_url(ctx: Ctx) -> Any:
    from metacheck.config import BUILTIN_STORE, load_config

    ctx.no_config_files()
    config = load_config()
    return config.stores[BUILTIN_STORE], config.source(f"stores.{BUILTIN_STORE}")


def _read_verbose(ctx: Ctx) -> Any:
    from metacheck import config

    ctx.monkeypatch.delitem(config._state, "verbose", raising=False)
    return config.verbose()


def _hosted(ctx: Ctx, attr: str, *, tokens: bool = True, proxy: bool = False) -> Any:
    """One field of ``HostedConfig.from_env()`` with the other hosted settings valid.

    The key under test is already set (or blank); this only fills in the others, so a
    setting that is blank reads as unset. A refusal returns its first sentence.
    """
    from metacheck.app import hosted

    ctx.monkeypatch.delenv(hosted.SPACE_HOST_ENV, raising=False)
    if tokens:
        ctx.monkeypatch.setenv(hosted.TOKENS_ENV, "t" * 32)
    if proxy:
        ctx.monkeypatch.setenv(hosted.AUTH_ENV, "proxy")
    try:
        config = hosted.HostedConfig.from_env()
    except hosted.HostedError as exc:
        return ("refused", str(exc).split(". ")[0])
    return getattr(config, attr)


def _hosted_with_hosts(ctx: Ctx, attr: str, **kwargs: bool) -> Any:
    ctx.monkeypatch.setenv(env_names("APP_HOSTS")[0], "app.example.org")
    return _hosted(ctx, attr, **kwargs)


#: what ``HostedConfig.from_env()`` refuses with when a setting it needs is blank
_NEEDS_TOKENS = ("refused", "Hosted mode needs access tokens")
_NEEDS_HOSTS = ("refused", "Hosted mode needs the host names it is served under")


def _read_grobid(ctx: Ctx) -> Any:
    from metacheck.app import run

    return run.grobid_servers()


def _grobid_default() -> list[str]:
    from metacheck.app import run

    return list(run.GROBID_SERVERS)


def _read_llm_cache_dir(ctx: Ctx) -> Any:
    from metacheck.llm.cache import _llm_cache_dir

    ctx.monkeypatch.chdir(ctx.tmp_path)
    return Path(_llm_cache_dir())


def _read_llm_workers(ctx: Ctx) -> Any:
    from metacheck.llm.core import _llm_workers

    return _llm_workers()


def _default_log_name() -> str:
    """The default log file's name (``test_pytacheck_folders.py`` pins it)."""
    from metacheck import log

    return log._default_path().name


def _read_log(ctx: Ctx) -> Any:
    from metacheck import log

    return log.logpath()


def _read_sleep(ctx: Ctx) -> Any:
    """The waits ``http.sleep`` really asks the clock for (the clock is a recorder)."""
    from metacheck import http

    slept: list[float] = []

    class Clock:
        """``http``'s view of the ``time`` module, with a sleep that only records."""

        sleep = staticmethod(slept.append)

        def __getattr__(self, name: str) -> Any:
            return getattr(time, name)

    ctx.monkeypatch.setattr(http, "time", Clock())
    http.sleep(1.5)
    return slept


def _read_throttle(ctx: Ctx) -> Any:
    """How often an empty bucket waits before ``NO_SLEEP`` lets ``acquire`` go on.

    ``sleep`` is a recorder that gives up on the third call, so the loop ends (the
    wrong answer is 3 and never a hang) and only the check after the wait is at work.
    """
    from metacheck import http

    calls: list[float] = []

    class GaveUp(Exception):
        pass

    def fake(seconds: float) -> None:
        calls.append(seconds)
        if len(calls) >= 3:
            raise GaveUp

    ctx.monkeypatch.setattr(http, "sleep", fake)
    bucket = http.Throttle(1, fill_time_s=1000.0)
    bucket.acquire("host.example.org")  # takes the one token
    try:
        bucket.acquire("host.example.org")
    except GaveUp:
        pass
    return len(calls)


def _read_pdftotext(ctx: Ctx) -> Any:
    from metacheck.datacheck._columns_codebook import _pdftotext

    ctx.exe("pdftotext-table")
    ctx.exe("pdftotext-other")
    return _pdftotext()


def _read_r_parser(ctx: Ctx) -> Any:
    """``parse_errors`` with R faked: ``"from R"`` when the engine is R, else Python's own."""
    from metacheck.codecheck import _rparse

    def fake_rscript(search_path: bool = True) -> str | None:
        return "/fake/Rscript" if search_path else None  # no reference R: the default is Python

    ctx.monkeypatch.setattr(_rparse, "rscript", fake_rscript)
    ctx.monkeypatch.setattr(_rparse, "_parse_errors_r", lambda texts, script: ["from R"])
    return _rparse.parse_errors([["x <- "]])


def _parsed_by_python() -> list[str | None]:
    from metacheck.codecheck import _rparse

    return [_rparse._parse_error_py(["x <- "])]


def _read_rscript(ctx: Ctx) -> Any:
    from metacheck.codecheck import _rparse

    ctx.exe("Rscript")
    ctx.exe("Rscript-other")
    return _rparse.rscript()


def _read_r_serialize_version(ctx: Ctx) -> Any:
    from metacheck.llm._rds import _r_version_int

    return _r_version_int()


def _r_version(major: int, minor: int, patch: int) -> int:
    return major * 65536 + minor * 256 + patch


def _read_cache_root(ctx: Ctx) -> Any:
    from metacheck.archives.cache import _metacheck_cache_root

    ctx.monkeypatch.chdir(ctx.tmp_path)
    return _metacheck_cache_root()


def _read_app_cache_root(ctx: Ctx) -> Any:
    from metacheck.app import run

    return run.cache_root()


def _read_repo_cache_clear(ctx: Ctx) -> Any:
    """``repo_cache_clear()`` refuses to empty a cache that was not redirected, in tests."""
    from metacheck.archives.download import repo_cache_clear

    ctx.monkeypatch.chdir(ctx.tmp_path)
    try:
        repo_cache_clear(quiet=True)
    except RuntimeError:
        return "refused"
    return "cleared"


def _read_db_data_dir(ctx: Ctx) -> Any:
    from metacheck.db._utils import user_data_dir

    return user_data_dir()


def _read_repro_rscript(ctx: Ctx) -> Any:
    from metacheck.repro.core import _rscript

    return _rscript()


def _read_capture_rscript(ctx: Ctx) -> Any:
    from metacheck.statout.r_capture import _rscript

    return _rscript()


def _read_rscript_path(ctx: Ctx) -> Any:
    from metacheck.datacheck.files import rscript_path

    return rscript_path()


def _read_pdftotext_beside_rscript(ctx: Ctx) -> Any:
    """``pdftotext`` is looked for next to the ``Rscript`` (no other one is found)."""
    from metacheck.datacheck._columns_codebook import _pdftotext

    ctx.exe("pdftotext", folder="bin")
    return _pdftotext()


def _default_preset() -> tuple[str, str]:
    from metacheck.presets import DEFAULT_PRESET

    return DEFAULT_PRESET, "default"


def _builtin_store() -> tuple[str, tuple[str, str]]:
    from metacheck.config import BUILTIN_STORE_URL

    return BUILTIN_STORE_URL, ("builtin", "builtin")


_KEY = "m" * 40
_LLM_MODEL = "openai/gpt-from-the-table"

#: setting key -> its accessor (the main place that reads it)
SITES: dict[str, Site] = {
    "API_KEY": Site(
        value=f"  {_KEY}\n",
        other="o" * 40,
        read=lambda ctx: _api().api_key(),
        expect=lambda ctx, name: _KEY,
        spaces=lambda ctx: None,
    ),
    "API_MAX_CHECKS": Site(
        value="3",
        other="5",
        read=lambda ctx: _api().max_checks(),
        expect=lambda ctx, name: 3,
        spaces=lambda ctx: os.cpu_count() or 1,
    ),
    "BIBR_BACKEND": Site(
        value=" SCIVRS ",
        other="bibr",
        read=_read_bibr_backend,
        expect=lambda ctx, name: ("https://bibr.example.org", "scivrs"),
        spaces=lambda ctx: ("https://bibr.example.org", "bibr"),
    ),
    "BIBR_URL": Site(
        value="https://bibr.example.org/",
        other="https://other.example.org",
        read=_read_bibr_url,
        expect=lambda ctx, name: ("https://bibr.example.org", "bibr"),
        spaces=lambda ctx: None,
    ),
    "CACHE_DIR": Site(
        value="<tmp>/cache-from-the-table",
        other="<tmp>/other-cache",
        read=_read_cache_dir,
        expect=lambda ctx, name: ctx.tmp_path / "cache-from-the-table",
        spaces=lambda ctx: ctx.platform("cache"),
    ),
    "CONFIG": Site(
        value="<tmp>/only.json",
        other="<tmp>/other.json",
        read=_read_config,
        expect=lambda ctx, name: [("env", ctx.tmp_path / "only.json")],
        spaces=lambda ctx: [("user", ctx.platform("config") / "config.json")],
    ),
    "DATA_DIR": Site(
        value="<tmp>/data-from-the-table",
        other="<tmp>/other-data",
        read=_read_data_dir,
        expect=lambda ctx, name: ctx.tmp_path / "data-from-the-table",
        spaces=lambda ctx: ctx.platform("data"),
    ),
    "EMAIL": Site(
        value="table@example.org",
        other="other@example.org",
        read=_read_email,
        expect=lambda ctx, name: "table@example.org",
        spaces=lambda ctx: None,
    ),
    "GITHUB_TOKEN": Site(
        value=" ghp_from_the_table ",
        other="ghp_other",
        read=_read_github_token,
        expect=lambda ctx, name: (name, "ghp_from_the_table", "sent *** here"),
        spaces=lambda ctx: (None, None, "sent ghp_from_the_table here"),
    ),
    "LLM_MAX_CALLS": Site(
        value="7",
        other="",  # no other name
        read=lambda ctx: _llm_setup(ctx)["llm_max_calls"],
        expect=lambda ctx, name: 7,
        spaces=lambda ctx: 200,
    ),
    "LLM_MODEL": Site(
        value=_LLM_MODEL,
        other="",  # no other name
        read=lambda ctx: _llm_setup(ctx)["llm_model"],
        expect=lambda ctx, name: _LLM_MODEL,
        spaces=lambda ctx: "google_gemini/gemini-3.1-flash-lite-preview",
    ),
    "PRESET": Site(
        value=" lab::strict ",
        other="other::preset",
        read=_read_preset,
        expect=lambda ctx, name: ("lab::strict", name),
        spaces=lambda ctx: _default_preset(),
    ),
    "STORE_URL": Site(
        value="file:///srv/mirror",
        other="file:///srv/other",
        read=_read_store_url,
        expect=lambda ctx, name: ("file:///srv/mirror", ("env", name)),
        spaces=lambda ctx: _builtin_store(),
    ),
    "VERBOSE": Site(
        value="No",
        other="yes",
        read=_read_verbose,
        expect=lambda ctx, name: False,
        spaces=lambda ctx: True,
    ),
    "GROBID_URL": Site(
        value=" http://a.example/, http://b.example ",
        other="http://other.example",
        read=_read_grobid,
        expect=lambda ctx, name: ["http://a.example", "http://b.example"],
        spaces=lambda ctx: _grobid_default(),
    ),
    "LLM_CACHE_DIR": Site(
        value="<tmp>/llm-from-the-table",
        other="<tmp>/other-llm",
        read=_read_llm_cache_dir,
        expect=lambda ctx, name: (ctx.tmp_path / "llm-from-the-table").resolve(),
        # no name, no CACHE_DIR, no option: the folder under the working directory
        spaces=lambda ctx: (ctx.tmp_path / ".metacheck_llm_cache").resolve(),
    ),
    "LLM_WORKERS": Site(
        value=" 4 ",
        other="2",
        read=_read_llm_workers,
        expect=lambda ctx, name: 4,
        spaces=lambda ctx: 1,
    ),
    "LOG": Site(
        value="<tmp>/elsewhere/table.log.jsonl",
        other="<tmp>/elsewhere/other.log.jsonl",
        read=_read_log,
        expect=lambda ctx, name: ctx.tmp_path / "elsewhere" / "table.log.jsonl",
        spaces=lambda ctx: ctx.platform("data") / "log" / _default_log_name(),
    ),
    "NO_SLEEP": Site(
        value="0",  # any non-blank value, 0 included
        other="",  # unused: every non-blank value means the same, see RANKING_IS_INVISIBLE
        read=_read_sleep,
        expect=lambda ctx, name: [],
        spaces=lambda ctx: [1.5],
    ),
    "PDFTOTEXT": Site(
        value="<tmp>/bin/pdftotext-table",
        other="<tmp>/bin/pdftotext-other",
        read=_read_pdftotext,
        expect=lambda ctx, name: str(ctx.tmp_path / "bin" / "pdftotext-table"),
        spaces=lambda ctx: None,
    ),
    "R_PARSER": Site(
        value="r",
        other="python",
        read=_read_r_parser,
        expect=lambda ctx, name: ["from R"],
        spaces=lambda ctx: _parsed_by_python(),
    ),
    "RSCRIPT": Site(
        value="<tmp>/bin/Rscript",
        other="<tmp>/bin/Rscript-other",
        read=_read_rscript,
        expect=lambda ctx, name: str(ctx.tmp_path / "bin" / "Rscript"),
        spaces=lambda ctx: None,
    ),
    "R_SERIALIZE_VERSION": Site(
        value="4.4.1",
        other="4.3.0",
        read=_read_r_serialize_version,
        expect=lambda ctx, name: _r_version(4, 4, 1),
        spaces=lambda ctx: _r_version(4, 5, 3),  # the default, which is part of the cache key
    ),
    "APP_AUTH": Site(
        value=" Proxy ",
        other="",  # no other name
        # no tokens: only the proxy mode needs none, and the default mode refuses
        read=lambda ctx: _hosted_with_hosts(ctx, "proxy_auth", tokens=False),
        expect=lambda ctx, name: True,
        spaces=lambda ctx: _NEEDS_TOKENS,
    ),
    "APP_COMMIT": Site(
        value=" abc123 ",
        other="",
        read=lambda ctx: _hosted_with_hosts(ctx, "commit"),
        expect=lambda ctx, name: "abc123",
        spaces=lambda ctx: None,
    ),
    "APP_HOSTS": Site(
        value="App.Example.org, two.example.org",
        other="",
        read=lambda ctx: _hosted(ctx, "hosts"),
        expect=lambda ctx, name: ("app.example.org", "two.example.org"),
        spaces=lambda ctx: _NEEDS_HOSTS,
    ),
    "APP_JOB_TIMEOUT": Site(
        value=" 30 ",
        other="",
        read=lambda ctx: _hosted_with_hosts(ctx, "job_timeout"),
        expect=lambda ctx, name: 30.0,
        spaces=lambda ctx: 900.0,
    ),
    "APP_TOKENS": Site(
        value=f" {_KEY}, {_KEY[:-1]}x ",
        other="",
        read=lambda ctx: _hosted_with_hosts(ctx, "tokens", tokens=False),
        expect=lambda ctx, name: (_KEY, _KEY[:-1] + "x"),
        spaces=lambda ctx: _NEEDS_TOKENS,
    ),
    "APP_USER_HEADER": Site(
        value=" X-Forwarded-User ",
        other="",
        read=lambda ctx: _hosted_with_hosts(ctx, "user_header", tokens=False, proxy=True),
        expect=lambda ctx, name: "X-Forwarded-User",
        spaces=lambda ctx: None,
    ),
}

#: further places that read a key, as (key, label) -> accessor. They get the same cases.
EXTRA_SITES: dict[tuple[str, str], Site] = {
    ("CACHE_DIR", "archives.cache"): Site(
        value="<tmp>/cache-from-the-table",
        other="<tmp>/other-cache",
        read=_read_cache_root,
        expect=lambda ctx, name: str(ctx.tmp_path / "cache-from-the-table"),
        spaces=lambda ctx: os.getcwd(),  # the working directory, which is tmp_path
    ),
    ("CACHE_DIR", "app.run.cache_root"): Site(
        value="<tmp>/cache-from-the-table",
        other="<tmp>/other-cache",
        read=_read_app_cache_root,
        expect=lambda ctx, name: None,  # set: the app leaves the library's own place alone
        spaces=lambda ctx: ctx.platform("cache"),
    ),
    ("CACHE_DIR", "archives.download"): Site(
        value="<tmp>/cache-from-the-table",
        other="<tmp>/other-cache",
        read=_read_repo_cache_clear,
        expect=lambda ctx, name: "cleared",
        spaces=lambda ctx: "refused",
    ),
    ("DATA_DIR", "db._utils"): Site(
        value="<tmp>/data-from-the-table",
        other="<tmp>/other-data",
        read=_read_db_data_dir,
        expect=lambda ctx, name: ctx.tmp_path / "data-from-the-table",
        spaces=lambda ctx: ctx.platform("data"),
    ),
    ("NO_SLEEP", "http.Throttle"): Site(
        value="0",
        other="",  # unused, as for the main NO_SLEEP accessor
        read=_read_throttle,
        expect=lambda ctx, name: 1,  # one wait, then it goes on
        spaces=lambda ctx: 3,  # it keeps waiting until the recorder gives up
    ),
    ("RSCRIPT", "repro.core"): Site(
        value="<tmp>/bin/Rscript",
        other="<tmp>/other-bin/Rscript",
        read=_read_repro_rscript,
        expect=lambda ctx, name: str(ctx.tmp_path / "bin" / "Rscript"),
        spaces=lambda ctx: None,
    ),
    ("RSCRIPT", "statout.r_capture"): Site(
        value="<tmp>/bin/Rscript",
        other="<tmp>/other-bin/Rscript",
        read=_read_capture_rscript,
        expect=lambda ctx, name: str(ctx.tmp_path / "bin" / "Rscript"),
        spaces=lambda ctx: None,
    ),
    ("RSCRIPT", "datacheck.files"): Site(
        value="<tmp>/bin/Rscript",
        other="<tmp>/other-bin/Rscript",
        read=_read_rscript_path,
        expect=lambda ctx, name: str(ctx.tmp_path / "bin" / "Rscript"),
        spaces=lambda ctx: None,
    ),
    ("RSCRIPT", "datacheck pdftotext"): Site(
        value="<tmp>/bin/Rscript",
        other="<tmp>/other-bin/Rscript",
        read=_read_pdftotext_beside_rscript,
        expect=lambda ctx, name: str(ctx.tmp_path / "bin" / "pdftotext"),
        spaces=lambda ctx: None,
    ),
}

#: keys whose values cannot tell the names apart, so which name ranks first shows in no
#: observation: every non-blank ``NO_SLEEP`` value turns sleeps off. A name alone is still
#: read (``test_each_name_alone...``), which is what pins each name.
RANKING_IS_INVISIBLE = frozenset({"NO_SLEEP"})

#: (key, name): every name of every key with an accessor
NAMES = [(key, name) for key in SITES for name in env_names(key)]
#: the same for the further places
EXTRA_NAMES = [(key, label, name) for key, label in EXTRA_SITES for name in env_names(key)]


# -- isolation ------------------------------------------------------------------------


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Ctx:
    """The machine stays out: no variable of the table, no R or poppler, no options, a
    fake platformdirs, fresh clash notes."""
    _env._reset_warned()
    monkeypatch.setattr(utils, "_options", {})  # an option would come before the variable
    for var in ENV_VARS.values():  # the fold leaves single-name keys such as LLM_MODEL alone
        for name in var.names:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    here = Ctx(tmp_path, monkeypatch)
    for kind in ("cache", "config", "data", "state"):
        folder = str(here.platform(kind))
        monkeypatch.setattr(platformdirs, f"user_{kind}_dir", lambda *a, _f=folder, **k: _f)
    return here


def _tmp(ctx: Ctx, value: str) -> str:
    """*value* with a leading ``<tmp>/a/b`` made a native path under ``tmp_path``."""
    if value.startswith("<tmp>/"):
        # built with Path, so the separators match what the expectations build on Windows
        return str(ctx.tmp_path.joinpath(*value.removeprefix("<tmp>/").split("/")))
    return value


def _set(ctx: Ctx, key: str, values: dict[str, str]) -> None:
    """Every name of *key* unset, then *values* set (``<tmp>`` is ``tmp_path``)."""
    for name in env_names(key):
        ctx.monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        ctx.monkeypatch.setenv(name, _tmp(ctx, value))


# -- the tests ------------------------------------------------------------------------


def test_every_key_of_the_table_has_an_accessor() -> None:
    assert set(SITES) == set(ENV_VARS), (
        f"no accessor for {sorted(set(ENV_VARS) - set(SITES))}; "
        f"not in the table: {sorted(set(SITES) - set(ENV_VARS))}"
    )


def test_the_further_accessors_are_for_keys_of_the_table() -> None:
    assert {key for key, _ in EXTRA_SITES} <= set(ENV_VARS)


@pytest.mark.parametrize(("key", "name"), NAMES)
def test_each_name_alone_is_read_at_the_call_site(ctx: Ctx, key: str, name: str) -> None:
    site = SITES[key]
    _set(ctx, key, {name: site.value})
    assert site.read(ctx) == site.expect(ctx, name)


@pytest.mark.parametrize(
    "key", [k for k in SITES if len(env_names(k)) > 1 and k not in RANKING_IS_INVISIBLE]
)
def test_the_first_name_wins_over_the_others_at_the_call_site(ctx: Ctx, key: str) -> None:
    site = SITES[key]
    first, *rest = env_names(key)
    _set(ctx, key, {**dict.fromkeys(rest, site.other), first: site.value})
    assert site.read(ctx) == site.expect(ctx, first)


@pytest.mark.parametrize("key", list(SITES))
def test_spaces_only_mean_unset_at_the_call_site(ctx: Ctx, key: str) -> None:
    site = SITES[key]
    _set(ctx, key, dict.fromkeys(env_names(key), "  "))
    assert site.read(ctx) == site.spaces(ctx)


@pytest.mark.parametrize(("key", "label", "name"), EXTRA_NAMES)
def test_each_name_alone_is_read_at_the_further_call_sites(
    ctx: Ctx, key: str, label: str, name: str
) -> None:
    site = EXTRA_SITES[key, label]
    _set(ctx, key, {name: site.value})
    assert site.read(ctx) == site.expect(ctx, name)


@pytest.mark.parametrize(
    ("key", "label"),
    [kl for kl in EXTRA_SITES if len(env_names(kl[0])) > 1 and kl[0] not in RANKING_IS_INVISIBLE],
)
def test_the_first_name_wins_at_the_further_call_sites(ctx: Ctx, key: str, label: str) -> None:
    site = EXTRA_SITES[key, label]
    first, *rest = env_names(key)
    _set(ctx, key, {**dict.fromkeys(rest, site.other), first: site.value})
    assert site.read(ctx) == site.expect(ctx, first)


@pytest.mark.parametrize(("key", "label"), list(EXTRA_SITES))
def test_spaces_only_mean_unset_at_the_further_call_sites(ctx: Ctx, key: str, label: str) -> None:
    site = EXTRA_SITES[key, label]
    _set(ctx, key, dict.fromkeys(env_names(key), "  "))
    assert site.read(ctx) == site.spaces(ctx)


# -- names the sites record or report -----------------------------------------------------


def test_the_github_token_clash_uses_the_new_name_and_says_so_once(
    ctx: Ctx, caplog: pytest.LogCaptureFixture
) -> None:
    from metacheck.packs.auth import github_token, token_var

    _set(
        ctx,
        "GITHUB_TOKEN",
        {"METACHECK_GITHUB_TOKEN": "ghp_new", "PYTACHECK_GITHUB_TOKEN": "ghp_old"},
    )
    with caplog.at_level(logging.DEBUG, logger=get_logger().name):
        assert token_var() == "METACHECK_GITHUB_TOKEN"
        assert github_token() == "ghp_new"
    records = [r for r in caplog.records if r.name == get_logger().name]
    assert [(r.levelno, r.getMessage()) for r in records] == [
        (logging.WARNING, "METACHECK_GITHUB_TOKEN is set, so PYTACHECK_GITHUB_TOKEN is ignored")
    ]


def test_the_module_constants_keep_the_old_names() -> None:
    from metacheck.app import bibr
    from metacheck.packs import auth

    assert auth.TOKEN_VARS == (
        "METACHECK_GITHUB_TOKEN",
        "PYTACHECK_GITHUB_TOKEN",
        "GH_TOKEN",
        "GITHUB_TOKEN",
    )
    assert bibr.URL_ENVS == ("METACHECK_BIBR_URL", "PYTACHECK_BIBR_URL")
    assert bibr.BACKEND_ENVS == ("METACHECK_BIBR_BACKEND", "PYTACHECK_BIBR_BACKEND")
    assert (bibr.URL_ENV, bibr.BACKEND_ENV) == ("PYTACHECK_BIBR_URL", "PYTACHECK_BIBR_BACKEND")
    from metacheck.app import hosted, run

    assert run.GROBID_ENVS == ("METACHECK_GROBID_URL", "PYTACHECK_GROBID_URL")
    assert run.GROBID_ENV == "PYTACHECK_GROBID_URL"
    assert "GROBID_ENV" not in run.__all__ and "GROBID_ENVS" not in run.__all__
    assert (
        hosted.TOKENS_ENV,
        hosted.AUTH_ENV,
        hosted.USER_HEADER_ENV,
        hosted.HOSTS_ENV,
        hosted.TIMEOUT_ENV,
        hosted.COMMIT_ENV,
    ) == (
        "METACHECK_APP_TOKENS",
        "METACHECK_APP_AUTH",
        "METACHECK_APP_USER_HEADER",
        "METACHECK_APP_HOSTS",
        "METACHECK_APP_JOB_TIMEOUT",
        "METACHECK_APP_COMMIT",
    )
    app = _api()
    assert app.API_KEY_ENVS == ("METACHECK_API_KEY", "PYTACHECK_API_KEY")
    assert app.API_KEY_ENV == "PYTACHECK_API_KEY"


@pytest.mark.parametrize("key", ["BIBR_URL", "BIBR_BACKEND"])
def test_bad_bibr_settings_name_the_variable_read(ctx: Ctx, key: str) -> None:
    from metacheck.app import bibr

    new, old = env_names(key)
    bad = {"BIBR_URL": "bibr.example.org", "BIBR_BACKEND": "platform"}[key]
    constant = {"BIBR_URL": bibr.BAD_URL, "BIBR_BACKEND": bibr.BAD_BACKEND}[key]
    _set(ctx, "BIBR_URL", {})
    _set(ctx, "BIBR_BACKEND", {})
    _set(ctx, key, {old: bad})
    assert bibr.settings_problem() == constant  # the old name: today's text, byte for byte
    _set(ctx, key, {new: bad})
    assert bibr.settings_problem() == constant.replace(old, new)
    assert old not in (bibr.settings_problem() or "")


def test_plain_http_names_the_address_variable_read(ctx: Ctx) -> None:
    from metacheck.app import bibr

    _set(ctx, "BIBR_BACKEND", {})
    _set(ctx, "BIBR_URL", {"PYTACHECK_BIBR_URL": "http://bibr.example.org"})
    assert bibr.settings_problem() == bibr.PLAIN_HTTP
    _set(ctx, "BIBR_URL", {"METACHECK_BIBR_URL": "http://bibr.example.org"})
    assert bibr.settings_problem() == bibr.PLAIN_HTTP.replace(
        "PYTACHECK_BIBR_URL", "METACHECK_BIBR_URL"
    )


@pytest.mark.parametrize("name", env_names("API_KEY"))
def test_a_short_api_key_names_the_variable_read(ctx: Ctx, name: str) -> None:
    app = _api()
    _set(ctx, "API_KEY", {name: "k" * 31})
    with pytest.raises(app.ApiConfigError) as info:
        app.api_key()
    assert str(info.value).startswith(f"{name} has 31 characters; it needs at least 32.")


@pytest.mark.parametrize("name", env_names("CONFIG"))
def test_disabled_config_files_name_the_variable_read(ctx: Ctx, name: str) -> None:
    from metacheck.config import ConfigError, config_path

    _set(ctx, "CONFIG", {name: " None "})
    with pytest.raises(ConfigError) as info:
        config_path("user")
    assert str(info.value) == f"Config files are disabled ({name}=none)"
