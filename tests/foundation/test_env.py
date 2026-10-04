"""The environment table: which variable wins, what is logged, and the harness fold.

Every case passes its own mapping as ``environ``, so the machine's environment does
not matter; the few that read ``os.environ`` say so.
"""

from __future__ import annotations

import logging
import os

import pytest

from metacheck import _env
from metacheck._env import (
    ENV_VARS,
    EnvVar,
    env_get,
    env_lookup,
    env_name,
    env_names,
    fold_to_legacy,
)
from metacheck._logging import get_logger

#: the settings whose new name ranks first, with a twin of it
TWINS = [
    key
    for key, var in ENV_VARS.items()
    if len(var.names) > 1
    and var.names[0].startswith("METACHECK_")
    and var.names[1].startswith("PYTACHECK_")
]
SECRETS = [key for key, var in ENV_VARS.items() if var.secret]


@pytest.fixture(autouse=True)
def _fresh_warnings() -> None:
    _env._reset_warned()


def _ours(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == get_logger().name]


def test_the_table_has_the_twins_and_the_exceptions_the_spec_lists() -> None:
    assert len(TWINS) == 22  # 23 twins, and LLM_CACHE_DIR ranks the old name first
    assert ENV_VARS["LLM_CACHE_DIR"].names == ("PYTACHECK_LLM_CACHE_DIR", "METACHECK_LLM_CACHE_DIR")
    assert ENV_VARS["LLM_MODEL"].names == ("METACHECK_LLM_MODEL",)
    assert ENV_VARS["LLM_MAX_CALLS"].names == ("METACHECK_LLM_MAX_CALLS",)
    assert ENV_VARS["GITHUB_TOKEN"].names == (
        "METACHECK_GITHUB_TOKEN",
        "PYTACHECK_GITHUB_TOKEN",
        "GH_TOKEN",
        "GITHUB_TOKEN",
    )
    assert {k for k, v in ENV_VARS.items() if v.shared_with_r} == {
        "LLM_CACHE_DIR",
        "LLM_MAX_CALLS",
        "LLM_MODEL",
    }
    assert set(SECRETS) == {"API_KEY", "GITHUB_TOKEN", "APP_TOKENS"}
    names = [n for var in ENV_VARS.values() for n in var.names]
    assert len(names) == len(set(names)), "a variable belongs to one setting only"
    assert [k for k in ENV_VARS if k.startswith("APP_")] == [
        "APP_AUTH",
        "APP_COMMIT",
        "APP_HOSTS",
        "APP_JOB_TIMEOUT",
        "APP_ROOTS",
        "APP_TOKENS",
        "APP_USER_HEADER",
    ]


def test_names_in_order_and_the_one_docs_tell_people_to_set() -> None:
    assert env_names("CACHE_DIR") == ("METACHECK_CACHE_DIR", "PYTACHECK_CACHE_DIR")
    assert env_name("CACHE_DIR") == "METACHECK_CACHE_DIR"
    assert env_name("LLM_CACHE_DIR") == "PYTACHECK_LLM_CACHE_DIR"
    with pytest.raises(KeyError):
        env_names("NOT_A_SETTING")


# -- lookup -------------------------------------------------------------------------


@pytest.mark.parametrize("key", TWINS)
def test_the_old_name_alone_is_read(key: str, caplog: pytest.LogCaptureFixture) -> None:
    old = env_names(key)[1]
    with caplog.at_level(logging.DEBUG, logger=get_logger().name):
        assert env_lookup(key, {old: "old"}) == (old, "old")
        assert env_get(key, environ={old: "old"}) == "old"
    assert not _ours(caplog)


@pytest.mark.parametrize("key", TWINS)
def test_the_new_name_alone_is_read(key: str) -> None:
    new = env_names(key)[0]
    assert env_lookup(key, {new: "new"}) == (new, "new")


@pytest.mark.parametrize("key", TWINS)
def test_both_names_set_differently_the_new_one_wins_and_one_line_says_so(
    key: str, caplog: pytest.LogCaptureFixture
) -> None:
    new, old = env_names(key)[:2]
    environ = {new: "new-value-1", old: "old-value-2"}
    with caplog.at_level(logging.DEBUG, logger=get_logger().name):
        assert env_get(key, environ=environ) == "new-value-1"
        assert env_get(key, environ=environ) == "new-value-1"  # the second lookup is silent
    (record,) = _ours(caplog)
    assert record.levelno == logging.WARNING
    assert record.getMessage() == f"{new} is set, so {old} is ignored"
    assert record.args == (new, old)


@pytest.mark.parametrize("key", TWINS)
@pytest.mark.parametrize("blank", ["", "  ", "\t\n"])
def test_a_blank_new_name_is_unset(key: str, blank: str, caplog: pytest.LogCaptureFixture) -> None:
    new, old = env_names(key)[:2]
    with caplog.at_level(logging.DEBUG, logger=get_logger().name):
        assert env_lookup(key, {new: blank, old: "old"}) == (old, "old")
        assert env_lookup(key, {new: blank}) is None
        assert env_lookup(key, {old: blank, new: "new"}) == (new, "new")
    assert not _ours(caplog)


@pytest.mark.parametrize("key", TWINS)
def test_both_names_with_the_same_value_log_nothing(
    key: str, caplog: pytest.LogCaptureFixture
) -> None:
    new, old = env_names(key)[:2]
    with caplog.at_level(logging.DEBUG, logger=get_logger().name):
        assert env_get(key, environ={new: "same", old: "same"}) == "same"
        assert env_get(key, environ={new: "same", old: " same "}) == "same"  # stripped
    assert not _ours(caplog)


def test_a_clash_is_logged_once_per_key(caplog: pytest.LogCaptureFixture) -> None:
    log = {"METACHECK_LOG": "a", "PYTACHECK_LOG": "b"}
    sleep = {"METACHECK_NO_SLEEP": "a", "PYTACHECK_NO_SLEEP": "b"}
    with caplog.at_level(logging.DEBUG, logger=get_logger().name):
        for _ in range(3):
            env_get("LOG", environ=log)
            env_get("NO_SLEEP", environ=sleep)
    assert sorted(r.getMessage() for r in _ours(caplog)) == [
        "METACHECK_LOG is set, so PYTACHECK_LOG is ignored",
        "METACHECK_NO_SLEEP is set, so PYTACHECK_NO_SLEEP is ignored",
    ]


def test_the_value_comes_back_as_set_not_stripped() -> None:
    assert env_lookup("EMAIL", {"METACHECK_EMAIL": "  a@b.org "}) == (
        "METACHECK_EMAIL",
        "  a@b.org ",
    )
    assert env_get("EMAIL", "x", {"METACHECK_EMAIL": "  a@b.org "}) == "  a@b.org "


def test_nothing_set_gives_none_or_the_default() -> None:
    assert env_lookup("CACHE_DIR", {}) is None
    assert env_get("CACHE_DIR", environ={}) is None
    assert env_get("CACHE_DIR", "dflt", {}) == "dflt"
    assert env_get("CACHE_DIR", "dflt", {"METACHECK_CACHE_DIR": " "}) == "dflt"


def test_the_environment_is_read_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTACHECK_VERBOSE", raising=False)
    monkeypatch.delenv("METACHECK_VERBOSE", raising=False)
    assert env_get("VERBOSE") is None
    monkeypatch.setenv("PYTACHECK_VERBOSE", "0")
    assert env_get("VERBOSE") == "0"
    monkeypatch.setenv("METACHECK_VERBOSE", "1")
    assert env_get("VERBOSE") == "1"
    assert os.environ["PYTACHECK_VERBOSE"] == "0"  # reading changes nothing


def test_llm_cache_dir_ranks_the_old_name_first_and_a_clash_is_only_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    old, new = env_names("LLM_CACHE_DIR")
    environ = {old: "mine", new: "r-side"}
    with caplog.at_level(logging.DEBUG, logger=get_logger().name):
        assert env_lookup("LLM_CACHE_DIR", environ) == (old, "mine")
        assert env_lookup("LLM_CACHE_DIR", {new: "r-side"}) == (new, "r-side")
    (record,) = _ours(caplog)
    assert record.levelno == logging.DEBUG
    assert record.getMessage() == f"{old} is set, so {new} is ignored"

    with caplog.at_level(logging.WARNING, logger=get_logger().name):
        caplog.clear()
        _env._reset_warned()
        env_lookup("LLM_CACHE_DIR", environ)
    assert not _ours(caplog)


def test_a_shared_name_ranked_below_ours_is_the_only_debug_clash(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The level follows the rank, not the sharing: DEBUG only when R's name ranks below ours."""
    row = EnvVar(("METACHECK_MADE_UP", "PYTACHECK_MADE_UP"), shared_with_r=True)
    monkeypatch.setitem(ENV_VARS, "MADE_UP", row)
    environ = {"METACHECK_MADE_UP": "a", "PYTACHECK_MADE_UP": "b"}
    with caplog.at_level(logging.DEBUG, logger=get_logger().name):
        assert env_get("MADE_UP", environ=environ) == "a"
    (record,) = _ours(caplog)
    assert record.levelno == logging.WARNING


def test_email_is_read_new_name_first() -> None:
    environ = {"METACHECK_EMAIL": "new@b.org", "PYTACHECK_EMAIL": "old@b.org"}
    assert env_get("EMAIL", environ=environ) == "new@b.org"


def test_email_set_in_code_beats_both_names(monkeypatch: pytest.MonkeyPatch) -> None:
    from metacheck import config

    monkeypatch.setitem(config._state, "email", None)
    monkeypatch.setenv("METACHECK_EMAIL", "new@b.org")
    monkeypatch.setenv("PYTACHECK_EMAIL", "old@b.org")
    assert config.email("set@b.org") == "set@b.org"
    assert config.email() == "set@b.org"


def test_github_token_names_in_order_and_only_the_pair_is_checked(
    caplog: pytest.LogCaptureFixture,
) -> None:
    new, old, gh, github = env_names("GITHUB_TOKEN")
    environ = {new: "t1", old: "t2", gh: "t3", github: "t4"}
    hits = []
    for name in (new, old, gh, github):
        hits.append(env_lookup("GITHUB_TOKEN", environ))
        del environ[name]
    assert hits == [(new, "t1"), (old, "t2"), (gh, "t3"), (github, "t4")]
    assert env_lookup("GITHUB_TOKEN", environ) is None

    _env._reset_warned()
    caplog.clear()  # the first lookup above had both names of the pair set
    with caplog.at_level(logging.DEBUG, logger=get_logger().name):
        assert env_lookup("GITHUB_TOKEN", {gh: "t3", github: "t4"}) == (gh, "t3")
        assert env_lookup("GITHUB_TOKEN", {new: "t1", gh: "t3", github: "t4"}) == (new, "t1")
        assert env_lookup("GITHUB_TOKEN", {old: "t2", gh: "t3"}) == (old, "t2")
    assert not _ours(caplog), "GH_TOKEN and GITHUB_TOKEN belong to other tools"


@pytest.mark.parametrize("key", SECRETS)
def test_no_value_of_a_secret_is_logged(key: str, caplog: pytest.LogCaptureFixture) -> None:
    names = env_names(key)
    environ = {name: f"sekret-{i}-{name.lower()}" for i, name in enumerate(names)}
    with caplog.at_level(logging.DEBUG, logger=get_logger().name):
        env_lookup(key, environ)
        env_get(key, environ=environ)
        fold_to_legacy(environ)
        env_lookup(key, environ)
    for record in caplog.records:
        text = " ".join([record.getMessage(), *map(str, record.args or ())])
        assert "sekret" not in text


# -- the fold -----------------------------------------------------------------------


def _worlds(key: str) -> dict[str, dict[str, str]]:
    new, old = env_names(key)[:2]
    return {
        "none": {},
        "old": {old: "old"},
        "new": {new: "new"},
        "both-different": {new: "new", old: "old"},
        "new-blank": {new: "  ", old: "old"},
        "new-blank-old-unset": {new: ""},
        "old-blank": {new: "new", old: "  "},
        "both-blank": {new: "", old: "  "},
    }


@pytest.mark.parametrize("key", TWINS)
@pytest.mark.parametrize(
    "world",
    [
        "none",
        "old",
        "new",
        "both-different",
        "new-blank",
        "new-blank-old-unset",
        "old-blank",
        "both-blank",
    ],
)
def test_the_fold_keeps_what_every_lookup_gives(
    key: str, world: str, caplog: pytest.LogCaptureFixture
) -> None:
    new, old = env_names(key)[:2]
    environ = dict(_worlds(key)[world])
    before = env_get(key, environ=environ)
    _env._reset_warned()
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger=get_logger().name):
        removed = fold_to_legacy(environ)
    assert env_get(key, environ=environ) == before
    assert new not in environ, "only old names are left"
    assert removed == ([new] if new in _worlds(key)[world] else [])
    assert not caplog.records, "the fold logs nothing"
    if before is not None:
        assert environ[old] == before


def test_the_fold_leaves_the_other_names_alone() -> None:
    untouched = {
        name: f"v-{name}"
        for key in ("LLM_CACHE_DIR", "LLM_MODEL", "LLM_MAX_CALLS", "GITHUB_TOKEN")
        for name in env_names(key)
        if not name.startswith("METACHECK_GITHUB") and not name.startswith("PYTACHECK_GITHUB")
    }
    untouched.update(
        {name: "app" for key in ENV_VARS if key.startswith("APP_") for name in env_names(key)}
    )
    environ = {**untouched, "METACHECK_GITHUB_TOKEN": "t1", "METACHECK_CACHE_DIR": "/c"}
    assert fold_to_legacy(environ) == ["METACHECK_CACHE_DIR", "METACHECK_GITHUB_TOKEN"]
    assert {k: environ[k] for k in untouched} == untouched
    assert environ["PYTACHECK_GITHUB_TOKEN"] == "t1"
    assert environ["PYTACHECK_CACHE_DIR"] == "/c"
    assert len(environ) == len(untouched) + 2


def test_the_fold_works_on_os_environ_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTACHECK_EMAIL", "set-first-and-replaced")  # so the undo removes it
    monkeypatch.setenv("METACHECK_EMAIL", "a@b.org")
    assert fold_to_legacy() == ["METACHECK_EMAIL"]
    assert "METACHECK_EMAIL" not in os.environ
    assert os.environ["PYTACHECK_EMAIL"] == "a@b.org"
