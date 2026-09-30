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

This file covers the settings of config, presets, pack tokens, the API and bibr.
The other keys, and the check that every key of ``ENV_VARS`` has an accessor,
follow with the remaining call sites.
"""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import platformdirs
import pytest

from metacheck import _env
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


def _default_preset() -> tuple[str, str]:
    from metacheck.presets import DEFAULT_PRESET

    return DEFAULT_PRESET, "default"


def _builtin_store() -> tuple[str, tuple[str, str]]:
    from metacheck.config import BUILTIN_STORE_URL

    return BUILTIN_STORE_URL, ("builtin", "builtin")


_KEY = "m" * 40
_LLM_MODEL = "openai/gpt-from-the-table"

#: setting key -> its accessor. Keys are added here as their call sites move to the table.
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
}

#: (key, name): every name of every key with an accessor
NAMES = [(key, name) for key in SITES for name in env_names(key)]


# -- isolation ------------------------------------------------------------------------


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Ctx:
    """The machine stays out: no variable of the table, no R or poppler, a fake
    platformdirs, fresh clash notes."""
    _env._reset_warned()
    for var in ENV_VARS.values():  # the fold leaves single-name keys such as LLM_MODEL alone
        for name in var.names:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(shutil, "which", lambda *a, **k: None)
    here = Ctx(tmp_path, monkeypatch)
    for kind in ("cache", "config", "data", "state"):
        folder = str(here.platform(kind))
        monkeypatch.setattr(platformdirs, f"user_{kind}_dir", lambda *a, _f=folder, **k: _f)
    return here


def _set(ctx: Ctx, key: str, values: dict[str, str]) -> None:
    """Every name of *key* unset, then *values* set (``<tmp>`` is ``tmp_path``)."""
    for name in env_names(key):
        ctx.monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        ctx.monkeypatch.setenv(name, value.replace("<tmp>", str(ctx.tmp_path)))


# -- the tests ------------------------------------------------------------------------


def test_the_accessors_are_keys_of_the_table() -> None:
    assert set(SITES) <= set(ENV_VARS)


@pytest.mark.parametrize(("key", "name"), NAMES)
def test_each_name_alone_is_read_at_the_call_site(ctx: Ctx, key: str, name: str) -> None:
    site = SITES[key]
    _set(ctx, key, {name: site.value})
    assert site.read(ctx) == site.expect(ctx, name)


@pytest.mark.parametrize("key", [k for k in SITES if len(env_names(k)) > 1])
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
