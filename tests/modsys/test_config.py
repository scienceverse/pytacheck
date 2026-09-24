"""JSON config: files, merge rules, precedence, PYTACHECK_CONFIG, atomic writes."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pytacheck.config import (
    BUILTIN_STORE_URL,
    ConfigError,
    config_files,
    config_path,
    config_stamp,
    data_dir,
    load_config,
    project_config_path,
    update_config,
)


@pytest.fixture
def scopes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A user config file and a project tree: <tmp>/proj/pytacheck.json, cwd <tmp>/proj/a/b."""
    user = tmp_path / "userconf" / "config.json"
    user.parent.mkdir()
    proj = tmp_path / "proj"
    cwd = proj / "a" / "b"
    cwd.mkdir(parents=True)
    monkeypatch.setattr("pytacheck.config.user_config_path", lambda: user)
    monkeypatch.delenv("PYTACHECK_CONFIG", raising=False)
    monkeypatch.delenv("PYTACHECK_STORE_URL", raising=False)
    monkeypatch.chdir(cwd)
    return user, proj / "pytacheck.json"


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data))
    st = path.stat()  # make sure the stamp moves even on coarse clocks
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


def test_data_dir(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PYTACHECK_DATA_DIR", str(tmp_path / "d"))
    assert data_dir() == tmp_path / "d"
    assert not (tmp_path / "d").exists()
    assert data_dir("packs", create=True).is_dir()
    monkeypatch.delenv("PYTACHECK_DATA_DIR")
    import platformdirs

    assert data_dir() == Path(platformdirs.user_data_dir("pytacheck"))


def test_files_and_scopes(scopes, monkeypatch, tmp_path) -> None:
    user, project = scopes
    assert config_files() == []
    _write(user, {})
    _write(project, {})
    assert config_files() == [("user", user), ("project", project)]
    assert project_config_path() == project
    monkeypatch.setenv("PYTACHECK_CONFIG", str(tmp_path / "only.json"))
    assert config_files() == [("env", tmp_path / "only.json")]
    monkeypatch.setenv("PYTACHECK_CONFIG", "none")
    assert config_files() == []
    assert load_config().packs == {} and load_config().preset is None


def test_merge_rules(scopes, tmp_path) -> None:
    user, project = scopes
    _write(
        user,
        {
            "preset": "psych::default",
            "color": "blue",
            "stores": {"mylab": "https://gitlab.example/lab/store", "local": "../stores/here"},
            "packs": {
                "psych": {"source": {"github": "j/p"}, "rev": "a" * 40},
                "labmods": {"path": "../lab-modules"},
                "some_dist": False,
            },
            "presets": {"thesis": {"modules": ["marginal", "mine.py"]}},
        },
    )
    _write(
        project,
        {
            "preset": "thesis",
            "color": None,  # null removes a scalar too
            "stores": {"pytacheck": None},
            "packs": {"psych": None, "other": {"path": "vendored"}},
            "presets": {"proj": {"modules": ["stat_check"]}},
        },
    )
    cfg = load_config()
    assert cfg.preset == "thesis"
    assert cfg.source("preset") == ("project", str(project))
    assert "color" not in cfg.values
    assert cfg.stores == {
        "mylab": "https://gitlab.example/lab/store",
        "local": str((user.parent / "../stores/here").resolve()),
    }
    assert set(cfg.packs) == {"labmods", "some_dist", "other"}
    assert cfg.packs["labmods"]["path"] == str((user.parent / "../lab-modules").resolve())
    assert cfg.packs["other"]["path"] == str(project.parent / "vendored")
    assert cfg.packs["some_dist"] is False
    assert cfg.source("packs.other") == ("project", str(project))
    assert cfg.presets["thesis"]["modules"] == ["marginal", str(user.parent / "mine.py")]
    assert set(cfg.presets) == {"thesis", "proj"}


def test_builtin_store_and_env_override(scopes, monkeypatch) -> None:
    assert load_config().stores == {"pytacheck": BUILTIN_STORE_URL}
    assert load_config().source("stores.pytacheck") == ("builtin", "builtin")
    monkeypatch.setenv("PYTACHECK_STORE_URL", "file:///tmp/mirror")
    assert load_config().stores["pytacheck"] == "file:///tmp/mirror"


def test_env_file_is_the_only_file(scopes, monkeypatch, tmp_path) -> None:
    user, project = scopes
    _write(user, {"preset": "from-user"})
    _write(project, {"preset": "from-project"})
    assert load_config().preset == "from-project"
    only = tmp_path / "only.json"
    _write(only, {"preset": "from-env"})
    monkeypatch.setenv("PYTACHECK_CONFIG", str(only))
    assert load_config().preset == "from-env"
    monkeypatch.setenv("PYTACHECK_CONFIG", "none")
    assert load_config().preset is None


def test_cache_follows_edits(scopes) -> None:
    user, _ = scopes
    _write(user, {"preset": "one"})
    stamp = config_stamp()
    assert load_config().preset == "one"
    assert load_config() is load_config()
    _write(user, {"preset": "two"})
    assert config_stamp() != stamp
    assert load_config().preset == "two"


@pytest.mark.parametrize(
    ("content", "error"),
    [
        ("{nope", "not valid JSON"),
        ("[1, 2]", "must hold a JSON object"),
        ('{"packs": []}', "'packs' must be an object"),
        ('{"packs": {"x": true}}', "may be false"),
        ('{"stores": {"x": 1}}', "stores.x must be"),
        ('{"preset": 3}', "'preset' must be a string"),
    ],
)
def test_malformed_config(scopes, content, error) -> None:
    user, _ = scopes
    user.write_text(content)
    with pytest.raises(ConfigError, match=error):
        load_config()


def test_update_config_atomic(scopes, monkeypatch, tmp_path) -> None:
    user, project = scopes
    assert config_path("user") == user
    assert config_path("project") == Path.cwd() / "pytacheck.json"  # none yet: create in cwd
    _write(project, {})
    assert config_path("project") == project

    path = update_config("project", lambda c: c.setdefault("packs", {}).update(x={"path": "."}))
    assert path == project
    assert json.loads(project.read_text()) == {"packs": {"x": {"path": "."}}}
    assert load_config().packs["x"]["path"] == str(project.parent)  # resolved on load, not on write
    update_config("user", lambda c: {"preset": "p"})
    assert json.loads(user.read_text()) == {"preset": "p"}
    assert load_config().preset == "p"
    assert [p.name for p in user.parent.iterdir()] == ["config.json"]  # no temp files left

    with pytest.raises(ConfigError, match="'packs' must be an object"):
        update_config("user", lambda c: {"packs": 1})
    assert json.loads(user.read_text()) == {"preset": "p"}  # unchanged on error
    with pytest.raises(ValueError, match="scope"):
        update_config("site", lambda c: None)

    only = tmp_path / "only.json"
    monkeypatch.setenv("PYTACHECK_CONFIG", str(only))
    update_config("project", lambda c: {"preset": "env"})
    assert json.loads(only.read_text()) == {"preset": "env"}
    monkeypatch.setenv("PYTACHECK_CONFIG", "none")
    with pytest.raises(ConfigError, match="disabled"):
        update_config("user", lambda c: None)


def test_hermetic_defaults_in_the_test_suite() -> None:
    import platformdirs

    # tests/conftest.py: no config files and a temporary data dir unless the caller set them
    assert os.environ.get("PYTACHECK_CONFIG")
    assert os.environ.get("PYTACHECK_DATA_DIR")
    assert data_dir() != Path(platformdirs.user_data_dir("pytacheck"))
