"""The platform folders keep 0.4.0a1's names and arguments (decision Q1 (c)).

The folders of config, data, cache, state and the log are still ``pytacheck``. This file
pins every ``platformdirs`` call, so a later rename cannot slip in unnoticed, and that
an override variable wins over the platform folder under either name.

The file name is excluded by the rename guard (``tests/*test_pytacheck_*.py``): it is the
one place that may spell the old folder name as a bare string.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from pathlib import Path
from typing import Any

import platformdirs
import pytest

import metacheck
from metacheck import _env, utils
from metacheck._env import ENV_VARS

SRC = Path(metacheck.__file__).resolve().parent

#: the settings that override a folder
OVERRIDES = ("CONFIG", "DATA_DIR", "CACHE_DIR", "LOG")


@pytest.fixture
def folders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any, Any]]:
    """A fake platformdirs that records ``(kind, appname, appauthor)`` and returns folders
    under ``tmp_path``; no override variable is set. The list of calls is returned."""
    _env._reset_warned()
    for key in OVERRIDES:
        for name in ENV_VARS[key].names:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(utils, "_options", {})
    calls: list[tuple[str, Any, Any]] = []

    def fake(kind: str) -> Callable[..., str]:
        def user_dir(appname: Any = None, appauthor: Any = None, *args: Any, **kw: Any) -> str:
            assert not args and not kw, "0.4.0a1 passes only the app name and the author"
            calls.append((kind, appname, appauthor))
            return str(tmp_path / "platform" / kind)

        return user_dir

    for kind in ("cache", "config", "data", "state"):
        monkeypatch.setattr(platformdirs, f"user_{kind}_dir", fake(kind))
    return calls


def _user_config_path() -> Path:
    from metacheck.config import user_config_path

    return user_config_path()


def _bibr_key_path() -> Path:
    from metacheck.app.bibr import key_path

    return key_path()


def _data_dir() -> Path:
    from metacheck.config import data_dir

    return data_dir()


def _db_data_dir() -> Path:
    from metacheck.db._utils import user_data_dir

    return user_data_dir()


def _log_path() -> Path:
    from metacheck import log

    return log._default_path()


def _cache_dir() -> Path:
    from metacheck.config import cache_dir

    return cache_dir()


def _app_cache_root() -> Path | None:
    from metacheck.app import run

    return run.cache_root()


def _state_path() -> Path:
    from metacheck.app.state import state_path

    return state_path()


#: function -> (the call it makes, the path below the returned folder)
CALLS: dict[str, tuple[Callable[[], Path | None], tuple[str, str, str | None], str]] = {
    "config.user_config_path": (_user_config_path, ("config", "pytacheck", None), "config.json"),
    "app.bibr.key_path": (_bibr_key_path, ("config", "pytacheck", None), "app-keys.json"),
    "config.data_dir": (_data_dir, ("data", "pytacheck", None), ""),
    "db._utils.user_data_dir": (_db_data_dir, ("data", "pytacheck", "scienceverse"), ""),
    "log._default_path": (
        _log_path,
        ("data", "pytacheck", "scienceverse"),
        "log/pytacheck.log.jsonl",
    ),
    "config.cache_dir": (_cache_dir, ("cache", "pytacheck", "scienceverse"), ""),
    "app.run.cache_root": (_app_cache_root, ("cache", "pytacheck", None), ""),
    "app.state.state_path": (_state_path, ("state", "pytacheck", None), "app.json"),
}


@pytest.mark.parametrize("name", list(CALLS))
def test_each_folder_function_makes_the_call_0_4_0a1_made(
    folders: list[tuple[str, Any, Any]], tmp_path: Path, name: str
) -> None:
    function, call, below = CALLS[name]
    path = function()
    assert folders == [call]
    assert path == tmp_path / "platform" / call[0] / below


# -- overrides -----------------------------------------------------------------------


def _config_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from metacheck.config import config_files

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return config_files()


@pytest.mark.parametrize("key", ["DATA_DIR", "CACHE_DIR", "LOG"])
@pytest.mark.parametrize("winner", ["METACHECK", "PYTACHECK"])
def test_an_override_wins_over_the_platform_folder_under_either_name(
    folders: list[tuple[str, Any, Any]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    winner: str,
) -> None:
    target = tmp_path / "elsewhere" / key
    monkeypatch.setenv(f"{winner}_{key}", str(target))
    if key == "DATA_DIR":
        assert [_data_dir(), _db_data_dir()] == [target, target]
    elif key == "CACHE_DIR":
        assert _cache_dir() == target
        assert _app_cache_root() is None  # the app leaves the library's own place alone
    else:
        assert _log_path() == target
    assert folders == []  # no platform folder was asked for


@pytest.mark.parametrize("winner", ["METACHECK", "PYTACHECK"])
def test_a_config_override_replaces_the_user_file_under_either_name(
    folders: list[tuple[str, Any, Any]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    winner: str,
) -> None:
    target = tmp_path / "elsewhere.json"
    monkeypatch.setenv(f"{winner}_CONFIG", str(target))
    assert _config_files(tmp_path, monkeypatch) == [("env", target)]
    assert ("config", "pytacheck", None) not in folders


@pytest.mark.parametrize("key", ["DATA_DIR", "CACHE_DIR", "LOG", "CONFIG"])
def test_the_new_name_beats_the_old_one(
    folders: list[tuple[str, Any, Any]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    key: str,
) -> None:
    new, old = ENV_VARS[key].names
    assert (new, old) == (f"METACHECK_{key}", f"PYTACHECK_{key}")
    new_path, old_path = tmp_path / "new" / key, tmp_path / "old" / key
    monkeypatch.setenv(new, str(new_path))
    monkeypatch.setenv(old, str(old_path))
    if key == "DATA_DIR":
        assert _data_dir() == new_path
        assert _db_data_dir() == new_path
    elif key == "CACHE_DIR":
        assert _cache_dir() == new_path
    elif key == "LOG":
        assert _log_path() == new_path
    else:
        assert _config_files(tmp_path, monkeypatch) == [("env", new_path)]


def test_the_overrides_are_the_four_settings_that_redirect_a_folder() -> None:
    # a new setting that moves a folder needs its own cases above
    assert set(OVERRIDES) <= set(ENV_VARS)
    assert all(ENV_VARS[key].names == (f"METACHECK_{key}", f"PYTACHECK_{key}") for key in OVERRIDES)


# -- every platformdirs call in the package ------------------------------------------------


def _platformdirs_calls() -> tuple[set[str], list[tuple[str, str, Any]]]:
    """The modules that import platformdirs, and its ``user_*_dir`` calls as
    ``(module, function, first argument)`` (``"<not a literal>"`` for anything else)."""
    importers: set[str] = set()
    calls: list[tuple[str, str, Any]] = []
    for path in sorted(SRC.rglob("*.py")):
        module = path.relative_to(SRC).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(a.name == "platformdirs" for a in node.names):
                    importers.add(module)
            elif isinstance(node, ast.ImportFrom):
                if node.module == "platformdirs":
                    importers.add(module)
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "platformdirs"
                and node.func.attr.startswith("user_")
                and node.func.attr.endswith("_dir")
            ):
                first = node.args[0] if node.args else None
                literal = (
                    first.value
                    if isinstance(first, ast.Constant) and isinstance(first.value, str)
                    else "<not a literal>"
                )
                calls.append((module, node.func.attr, literal))
    return importers, calls


def test_every_platformdirs_call_names_the_pytacheck_folder_except_the_shared_corpus() -> None:
    importers, calls = _platformdirs_calls()
    assert importers == {
        "app/bibr.py",
        "app/run.py",
        "app/state.py",
        "config.py",
        "db/_utils.py",
        "io/corpus.py",
        "log.py",
    }
    assert sorted(calls) == [
        ("app/bibr.py", "user_config_dir", "pytacheck"),
        ("app/run.py", "user_cache_dir", "pytacheck"),
        ("app/state.py", "user_state_dir", "pytacheck"),
        ("config.py", "user_cache_dir", "pytacheck"),
        ("config.py", "user_config_dir", "pytacheck"),
        ("config.py", "user_data_dir", "pytacheck"),
        ("db/_utils.py", "user_data_dir", "pytacheck"),
        # R's own folder, shared on purpose: a corpus cached by either package is reused
        ("io/corpus.py", "user_data_dir", "metacheck"),
        ("log.py", "user_data_dir", "pytacheck"),
    ]
