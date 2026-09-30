"""JSON config: files, merge rules, precedence, PYTACHECK_CONFIG, atomic writes."""

from __future__ import annotations

import hashlib
import json
import os
import time
import warnings
from pathlib import Path

import pytest

from metacheck._env import env_names
from metacheck.config import (
    BUILTIN_STORE,
    BUILTIN_STORE_URL,
    ConfigError,
    config_files,
    config_path,
    config_stamp,
    data_dir,
    load_config,
    project_config_path,
    trust_local,
    trusted_local,
    update_config,
)

#: the settings whose variables config_stamp() stamps
_ENV_KEY_SETTINGS = ("CONFIG", "DATA_DIR", "STORE_URL", "PRESET")


@pytest.fixture
def scopes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A user config file and a project tree: <tmp>/proj/pytacheck.json, cwd <tmp>/proj/a/b."""
    user = tmp_path / "userconf" / "config.json"
    user.parent.mkdir()
    proj = tmp_path / "proj"
    cwd = proj / "a" / "b"
    cwd.mkdir(parents=True)
    monkeypatch.setattr("metacheck.config.user_config_path", lambda: user)
    monkeypatch.delenv("PYTACHECK_CONFIG", raising=False)
    monkeypatch.delenv("PYTACHECK_STORE_URL", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))  # the search stops here
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(cwd)
    return user, proj / "pytacheck.json"


def _write(path: Path, data: dict) -> None:
    _write_text(path, json.dumps(data))


def _write_text(path: Path, text: str) -> None:
    path.write_text(text)
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
            "stores": {
                "pytacheck": None,  # null removes the built-in store
                "mylab": "https://gitlab.example/lab/store",
                "local": "../stores/here",
            },
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
    # a project file's local code (a path pack) waits until the user trusts it
    assert set(cfg.packs) == {"labmods", "some_dist"}
    assert cfg.untrusted["packs.other"][2] == (str(project.parent / "vendored"),)
    trust_local([project.parent / "vendored"])
    cfg = load_config()
    assert set(cfg.packs) == {"labmods", "some_dist", "other"} and not cfg.untrusted
    assert cfg.packs["labmods"]["path"] == str((user.parent / "../lab-modules").resolve())
    assert cfg.packs["other"]["path"] == str(project.parent / "vendored")
    assert cfg.packs["some_dist"] is False
    assert cfg.source("packs.other") == ("project", str(project))
    assert cfg.presets["thesis"]["modules"] == ["marginal", str(user.parent / "mine.py")]
    assert set(cfg.presets) == {"thesis", "proj"}


def test_a_malformed_ignored_project_stores_section_does_not_stop_commands(scopes) -> None:
    user, project = scopes
    _write(user, {"preset": "u"})
    _write(project, {"preset": "p", "stores": {"lab": 5}})
    with pytest.warns(UserWarning, match="Ignoring the stores in the project config"):
        cfg = load_config()
    assert cfg.preset == "p" and set(cfg.stores) == {"pytacheck"}
    _write(user, {"stores": {"lab": 5}})  # a user file's stores are still checked
    with pytest.raises(ConfigError, match=r"stores\.lab must be a URL"):
        load_config()


def test_update_config_ignores_a_malformed_ignored_project_stores_section(
    scopes, monkeypatch
) -> None:
    _, project = scopes
    _write(project, {"stores": {"lab": 5}})
    update_config("project", lambda c: c.update(preset="p"))
    assert json.loads(project.read_text())["preset"] == "p"
    monkeypatch.setenv("PYTACHECK_CONFIG", str(project))  # now its stores apply
    with pytest.raises(ConfigError, match=r"stores\.lab must be a URL"):
        update_config("project", lambda c: c.update(preset="q"))


def test_a_project_config_cannot_set_stores(scopes, monkeypatch) -> None:
    # a cloned repository must not add a store, nor replace or hide the official one
    from metacheck.packs.stores import store_list

    user, project = scopes
    _write(user, {"stores": {"mylab": "https://github.com/mylab/store"}})
    shadow = {"pytacheck": "./shadow-store", "evil": "https://evil.example/s", "mylab": None}
    _write(project, {"preset": "p", "stores": shadow})
    with pytest.warns(UserWarning, match="Ignoring the stores in the project config") as info:
        cfg = load_config()
    assert str(project) in str(info[0].message) and "user config" in str(info[0].message)
    assert cfg.stores == {"pytacheck": BUILTIN_STORE_URL, "mylab": "https://github.com/mylab/store"}
    assert cfg.source("stores.pytacheck") == ("builtin", "builtin")
    assert cfg.source("stores.mylab") == ("user", str(user))
    assert cfg.preset == "p"  # the rest of the file still counts
    assert store_list()["defined_in"].tolist() == ["builtin", str(user)]

    _write(project, {"preset": "q", "stores": {"evil": "https://evil.example/s"}})
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # warned once per file
        assert load_config().preset == "q" and "evil" not in load_config().stores

    # named with PYTACHECK_CONFIG, the same file is the user's own choice
    monkeypatch.setenv("PYTACHECK_CONFIG", str(project))
    assert load_config().stores["evil"] == "https://evil.example/s"


def test_stores_cannot_be_written_to_a_project_config(scopes) -> None:
    from metacheck.packs.stores import StoreError, store_add, store_remove

    user, project = scopes
    _write(project, {})
    with pytest.raises(StoreError, match="Stores go in your user config"):
        store_add("lab", "https://github.com/lab/store", scope="project")
    with pytest.raises(StoreError, match="Stores go in your user config"):
        store_remove("pytacheck", scope="project")
    assert json.loads(project.read_text()) == {}
    assert store_add("lab", "https://github.com/lab/store") == user
    assert json.loads(user.read_text()) == {"stores": {"lab": "https://github.com/lab/store"}}


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


def test_rewrite_in_the_same_timestamp_tick_is_seen(scopes) -> None:
    # a same-size rewrite in place that lands in the tick of the filesystem clock
    # the first write did: mtime and size both stay the same
    user, _ = scopes
    user.write_text(json.dumps({"preset": "one"}))
    assert load_config().preset == "one"
    st = user.stat()
    user.write_text(json.dumps({"preset": "two"}))
    os.utime(user, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert load_config().preset == "two"
    assert load_config() is load_config()


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
    assert config_path("project") == Path.cwd() / "metacheck.json"  # none yet: create in cwd
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


# -- the METACHECK_ names of the config variables, and the API key (RENAME-3) --------------


@pytest.mark.parametrize("name", [n for k in _ENV_KEY_SETTINGS for n in env_names(k)])
def test_the_stamp_follows_each_config_variable_alone(monkeypatch, name) -> None:
    # "NONE" and "None" both disable the files, so only the variable's own value can move it
    monkeypatch.setenv("PYTACHECK_CONFIG", "none")
    before = config_stamp()
    monkeypatch.setenv(name, "NONE")
    first = config_stamp()
    monkeypatch.setenv(name, "None")
    assert len({before, first, config_stamp()}) == 3


@pytest.mark.parametrize("name", env_names("CONFIG"))
def test_a_file_named_by_either_config_variable_is_the_users_own(
    scopes, monkeypatch, tmp_path, name
) -> None:
    # the stores check runs and no code is trusted, exactly as for the old name
    monkeypatch.setenv("PYTACHECK_DATA_DIR", str(tmp_path / "data"))
    only = tmp_path / "only.json"
    monkeypatch.setenv(name, str(only))
    pack = tmp_path / "mypack"
    pack.mkdir()
    with pytest.raises(ConfigError, match=r"stores\.x must be"):
        update_config("project", lambda c: {"stores": {"x": 1}})
    lab = "https://github.com/lab/store"
    path = update_config(
        "project", lambda c: {"stores": {"lab": lab}, "packs": {"mine": {"path": str(pack)}}}
    )
    assert path == only
    assert trusted_local() == frozenset()
    config = load_config()
    assert config.files == (("env", only),)
    assert config.stores["lab"] == lab
    assert config.packs["mine"]["path"] == str(pack)
    assert not config.untrusted


def test_serve_refuses_a_short_new_api_key_next_to_a_valid_old_one(monkeypatch, capsys) -> None:
    pytest.importorskip("fastapi")
    uvicorn = pytest.importorskip("uvicorn")
    from metacheck.api.app import ApiConfigError, api_key
    from metacheck.cli import main

    served: list[dict] = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **kw: served.append(kw))
    monkeypatch.setenv("PYTACHECK_API_KEY", "k" * 32)
    monkeypatch.setenv("METACHECK_API_KEY", "s" * 10)
    with pytest.raises(ApiConfigError, match=r"^METACHECK_API_KEY has 10 characters"):
        api_key()
    assert main(["serve", "--behind-authenticating-proxy"]) == 1
    assert served == []
    out = capsys.readouterr()
    assert "METACHECK_API_KEY has 10 characters" in " ".join((out.out + out.err).split())


# -- the project file: metacheck.json, and pytacheck.json still read (RENAME-3) -------------

_POSIX = os.name != "nt"  # _unsafe() looks at mode bits and owners on POSIX only


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """cwd <tmp>/home/proj, which is below the home folder (so the search stops there)."""
    root = tmp_path / "home"
    cwd = root / "proj"
    cwd.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setenv("USERPROFILE", str(root))
    monkeypatch.setattr("metacheck.config.user_config_path", lambda: tmp_path / "no-user.json")
    for name in (*env_names("CONFIG"), *env_names("STORE_URL"), *env_names("DATA_DIR")):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PYTACHECK_DATA_DIR", str(tmp_path / "data"))  # its own trust file
    monkeypatch.chdir(cwd)
    return cwd


def _warned(fn):
    """``(fn(), the messages of the warnings it issued)``."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out = fn()
    return out, [str(w.message) for w in caught]


def test_a_metacheck_json_cannot_set_stores(home) -> None:
    project = home / "metacheck.json"
    _write(project, {"preset": "p", "stores": {"evil": "https://evil.example/s"}})
    with pytest.warns(UserWarning, match="Ignoring the stores in the project config"):
        cfg = load_config()
    assert cfg.files == (("project", project),)
    assert cfg.preset == "p"
    assert cfg.stores == {BUILTIN_STORE: BUILTIN_STORE_URL}
    assert cfg.source(f"stores.{BUILTIN_STORE}") == ("builtin", "builtin")


def test_a_path_pack_in_a_metacheck_json_waits_for_trust(home) -> None:
    project = home / "metacheck.json"
    _write(project, {"packs": {"mine": {"path": "vendored"}}})
    cfg = load_config()
    assert "mine" not in cfg.packs
    assert cfg.untrusted["packs.mine"][2] == (str(home / "vendored"),)
    trust_local([home / "vendored"])
    assert load_config().packs["mine"]["path"] == str(home / "vendored")
    # update_config("project") trusts only the code it adds
    update_config("project", lambda c: c["packs"].update(more={"path": "more"}))
    assert trusted_local() == {str(home / "vendored"), str(home / "more")}


@pytest.mark.skipif(not _POSIX, reason="Windows has no mode bits for others")
def test_an_unsafe_metacheck_json_is_ignored(home) -> None:
    project = home / "metacheck.json"
    _write(project, {"preset": "p"})
    assert [s for s, _ in config_files()] == ["project"]
    project.chmod(0o666)
    try:
        with pytest.warns(UserWarning, match=r"metacheck\.json: its file is writable by everyone"):
            assert config_files() == []
        assert load_config().preset is None
    finally:
        project.chmod(0o644)


@pytest.mark.skipif(not _POSIX, reason="Windows has no mode bits for others")
def test_an_unsafe_metacheck_json_does_not_fall_back_to_pytacheck_json(home) -> None:
    # the search found metacheck.json: neither the other name nor the folder above is read
    _write(home.parent / "metacheck.json", {"preset": "above"})
    _write(home / "pytacheck.json", {"preset": "old"})
    project = home / "metacheck.json"
    _write(project, {"preset": "new"})
    project.chmod(0o666)
    try:
        files, messages = _warned(config_files)
        assert files == []
        assert any("writable by everyone" in m and str(project) in m for m in messages)
        assert load_config().preset is None
        assert project_config_path() == project
    finally:
        project.chmod(0o644)


def test_both_names_in_one_folder_metacheck_json_is_used_with_one_warning(home) -> None:
    new, old = home / "metacheck.json", home / "pytacheck.json"
    _write(new, {"preset": "new", "packs": {"a": False}})
    _write(old, {"preset": "old", "packs": {"b": False}})
    cfg, messages = _warned(load_config)
    assert messages == [f"Ignoring {old}: {new} is used instead"]
    assert cfg.files == (("project", new),)
    assert cfg.preset == "new" and cfg.packs == {"a": False}  # no merging
    assert cfg.source("preset") == ("project", str(new))
    _, again = _warned(lambda: (config_files(), project_config_path(), config_path("project")))
    assert again == []  # once per folder
    assert config_path("project") == new


@pytest.mark.parametrize(
    ("near", "far"),
    [
        ("pytacheck.json", "metacheck.json"),
        ("metacheck.json", "pytacheck.json"),
        ("metacheck.json", "metacheck.json"),
        ("pytacheck.json", "pytacheck.json"),
    ],
)
def test_the_nearest_folder_wins_whatever_the_name(home, monkeypatch, near, far) -> None:
    sub = home / "a"
    sub.mkdir()
    _write(home / far, {"preset": "far"})
    _write(sub / near, {"preset": "near"})
    monkeypatch.chdir(sub)
    assert project_config_path() == sub / near
    assert load_config().preset == "near"
    assert config_path("project") == sub / near


def test_a_new_project_file_is_metacheck_json(home) -> None:
    assert config_path("project") == home / "metacheck.json"
    assert update_config("project", lambda c: {"preset": "p"}) == home / "metacheck.json"
    assert [p.name for p in home.iterdir()] == ["metacheck.json"]
    assert json.loads((home / "metacheck.json").read_text()) == {"preset": "p"}
    assert load_config().source("preset") == ("project", str(home / "metacheck.json"))


@pytest.mark.parametrize("name", env_names("CONFIG"))
def test_the_config_variable_overrides_both_names(home, monkeypatch, tmp_path, name) -> None:
    _write(home / "metacheck.json", {"preset": "new"})
    _write(home / "pytacheck.json", {"preset": "old"})
    only = tmp_path / "only.json"
    _write(only, {"preset": "env"})
    monkeypatch.setenv(name, str(only))
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # no search, so no warning about the two names
        assert config_files() == [("env", only)]
        assert load_config().preset == "env"
        assert update_config("project", lambda c: c.update(preset="written")) == only
    assert json.loads((home / "metacheck.json").read_text()) == {"preset": "new"}
    assert json.loads((home / "pytacheck.json").read_text()) == {"preset": "old"}


# a JSON file named metacheck.json that is not a project file: saved results, say


@pytest.mark.parametrize("content", ["[1, 2]", '{"papers": []}', '"text"', "3"])
def test_a_file_that_is_not_a_project_file_is_passed_over(home, content) -> None:
    results = home / "metacheck.json"
    results.write_text(content)
    old = home / "pytacheck.json"
    _write(old, {"preset": "old"})
    cfg, messages = _warned(load_config)
    assert messages == [
        f"Ignoring {results}: it is not a metacheck project file "
        "(it has no preset, stores, packs or presets)"
    ]
    assert cfg.files == (("project", old),) and cfg.preset == "old"
    assert config_path("project") == old  # never the passed-over file
    old.unlink()
    _write(home.parent / "metacheck.json", {"preset": "above"})
    cfg, messages = _warned(load_config)
    assert messages == []  # one warning per file
    assert cfg.preset == "above"
    assert config_path("project") == home.parent / "metacheck.json"


@pytest.mark.parametrize("content", ["[1, 2]", '{"papers": []}'])
def test_config_path_does_not_write_into_a_passed_over_file(home, content) -> None:
    results = home / "metacheck.json"
    results.write_text(content)
    with pytest.warns(UserWarning, match="not a metacheck project file"):
        cfg = load_config()
    assert cfg.files == () and cfg.preset is None
    assert config_files() == []
    with pytest.raises(ConfigError) as info:
        config_path("project")
    assert str(info.value) == (
        f"{results} is not a metacheck project file; move it, "
        "or set METACHECK_CONFIG to the file to write"
    )
    with pytest.raises(ConfigError, match="not a metacheck project file"):
        update_config("project", lambda c: c.update(preset="p"))
    assert results.read_text() == content
    assert config_path("user") == home.parent.parent / "no-user.json"  # the user file is fine


@pytest.mark.skipif(not _POSIX, reason="Windows has no mode bits for others")
def test_a_passed_over_file_is_passed_over_whatever_its_permissions(home) -> None:
    results = home / "metacheck.json"
    results.write_text("[1, 2]")
    results.chmod(0o666)
    try:
        cfg, messages = _warned(load_config)
    finally:
        results.chmod(0o644)
    assert cfg.files == ()
    assert len(messages) == 1 and "not a metacheck project file" in messages[0]


@pytest.mark.parametrize("content", ["{}", '{"preset": null}', '{"packs": {}}'])
def test_empty_objects_are_project_files(home, content) -> None:
    project = home / "metacheck.json"
    project.write_text(content)
    _write(home / "pytacheck.json", {"preset": "old"})  # used if the new one were passed over
    with pytest.warns(UserWarning, match="is used instead"):
        cfg = load_config()
    assert cfg.files == (("project", project),) and cfg.preset is None
    assert config_path("project") == project


@pytest.mark.parametrize("content", ["{}", "", "  \n"])
def test_an_empty_or_blank_pytacheck_json_still_counts(home, content) -> None:
    # as in 0.4.0a1: it shields the folder above
    project = home / "pytacheck.json"
    project.write_text(content)
    _write(home.parent / "metacheck.json", {"preset": "above"})
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        cfg = load_config()
    assert cfg.files == (("project", project),) and cfg.preset is None
    assert config_path("project") == project


@pytest.mark.parametrize("content", ["", "  \n"])
def test_a_blank_metacheck_json_is_passed_over_silently(home, content) -> None:
    # `metacheck check --json > metacheck.json` creates it empty before the command runs
    blank = home / "metacheck.json"
    blank.write_text(content)
    above = home.parent / "metacheck.json"
    _write(above, {"preset": "above"})
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        cfg = load_config()
        assert config_path("project") == above
    assert cfg.files == (("project", above),) and cfg.preset == "above"
    assert ("passed", str(blank)) in {f[:2] for f in config_stamp()[2]}


def test_a_blank_metacheck_json_next_to_a_pytacheck_json(home) -> None:
    (home / "metacheck.json").write_text("")
    old = home / "pytacheck.json"
    _write(old, {"preset": "old"})
    cfg, messages = _warned(load_config)
    assert messages == []  # neither "not a project file" nor "is used instead"
    assert cfg.files == (("project", old),) and cfg.preset == "old"
    assert config_path("project") == old


def test_config_path_writes_into_a_blank_metacheck_json(home) -> None:
    # `touch metacheck.json`, then `init --project`
    blank = home / "metacheck.json"
    blank.write_text("")
    assert project_config_path() is None
    assert load_config().files == ()
    assert config_path("project") == blank
    assert update_config("project", lambda c: c.update(preset="p")) == blank
    assert json.loads(blank.read_text()) == {"preset": "p"}
    assert load_config().files == (("project", blank),) and load_config().preset == "p"


def test_a_shell_redirect_into_metacheck_json(home) -> None:
    above = home.parent / "metacheck.json"
    _write(above, {"preset": "above"})
    results = home / "metacheck.json"
    results.write_text("")  # the shell creates it before `check --json` runs
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert load_config().preset == "above"  # the run sees the settings above
    stamp = config_stamp()
    _write_text(results, "[]")  # the command wrote its results
    with pytest.warns(UserWarning, match="not a metacheck project file"):
        assert config_stamp() != stamp
        cfg = load_config()
    assert cfg.files == (("project", above),) and cfg.preset == "above"
    assert config_path("project") == above


def test_invalid_json_is_a_project_file_and_still_raises(home) -> None:
    project = home / "metacheck.json"
    project.write_text("{nope")
    _write(home / "pytacheck.json", {"preset": "old"})
    with pytest.warns(UserWarning, match="is used instead"):
        assert project_config_path() == project
    with pytest.raises(ConfigError, match="not valid JSON"):
        load_config()


def test_turning_a_passed_over_file_into_a_project_file_is_seen_at_once(home) -> None:
    results = home / "metacheck.json"
    _write(results, {"papers": []})
    with pytest.warns(UserWarning, match="not a metacheck project file"):
        assert load_config().preset is None
    stamp = config_stamp()
    assert ("passed", str(results)) in {f[:2] for f in stamp[2]}
    _write(results, {"preset": "x"})
    assert config_stamp() != stamp
    assert load_config().preset == "x"
    assert load_config().files == (("project", results),)


def test_a_same_size_rewrite_in_the_same_tick_gets_a_new_verdict(home) -> None:
    # {"papers": [1]} and {"preset": "x"} are both 15 bytes; the verdict is cached on the
    # file's stamp, which holds a digest while the file is racy
    results = home / "metacheck.json"
    results.write_text(json.dumps({"papers": [1]}))
    with pytest.warns(UserWarning, match="not a metacheck project file"):
        assert load_config().preset is None
    st = results.stat()
    results.write_text(json.dumps({"preset": "x"}))
    os.utime(results, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert results.stat().st_size == st.st_size
    assert load_config().preset == "x"


def test_above_the_read_cap_the_first_byte_decides(home, monkeypatch) -> None:
    from metacheck import config

    monkeypatch.setattr(config, "_VERDICT_CAP", 16)
    above = home.parent / "metacheck.json"
    _write(above, {"preset": "above"})
    big = home / "metacheck.json"
    _write_text(big, json.dumps({"papers": [1, 2, 3, 4, 5]}))  # 27 bytes
    assert len(config._read_head(big) or b"") == 17  # the cap and one byte, no more
    stamp = config._candidate_stamp(big, big.stat().st_mtime_ns + config._RACY_NS)
    assert stamp is not None and stamp[2] is None  # not racy: no digest
    stamp = config._candidate_stamp(big, big.stat().st_mtime_ns)
    assert stamp is not None
    assert stamp[2] == hashlib.sha256(big.read_bytes()[:17]).hexdigest()  # capped digest
    # it starts with "{": a project file (under the cap it would be passed over)
    assert load_config().files == (("project", big),)
    _write_text(big, "  \n" + json.dumps(list(range(10))))
    with pytest.warns(UserWarning, match="not a metacheck project file"):
        assert load_config().files == (("project", above),)
    # its "passed" stamp holds a digest of the capped read too (it is racy here)
    passed = {f[1]: f[2] for f in config_stamp()[2] if f[0] == "passed"}
    assert passed[str(big)][2] == hashlib.sha256(big.read_bytes()[:17]).hexdigest()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs here")
def test_a_fifo_named_like_a_project_file_is_not_read(home) -> None:
    from metacheck import config

    fifo = home / "metacheck.json"
    os.mkfifo(fifo)
    # opened without blocking, and not read: nobody writes to it
    assert config._read_head(fifo) is None
    assert config._file_kind(fifo) == "project"
    stamp = config._candidate_stamp(fifo, time.time_ns())  # racy, yet not read
    assert stamp is not None and stamp[2] is None
    above = home.parent / "metacheck.json"
    _write(above, {"preset": "above"})
    assert load_config().files == (("project", above),)  # the search skips non-files


@pytest.mark.parametrize("name", env_names("CONFIG"))
def test_a_config_variable_naming_a_metacheck_json_gives_scope_env(home, monkeypatch, name) -> None:
    # the rules follow the scope, never the file name: its stores apply
    named = home / "metacheck.json"  # also the project file the search would find
    _write(named, {"stores": {"lab": "https://github.com/lab/store"}, "preset": "p"})
    monkeypatch.setenv(name, str(named))
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # no "Ignoring the stores" warning
        cfg = load_config()
    assert cfg.files == (("env", named),)
    assert cfg.stores["lab"] == "https://github.com/lab/store"
    assert cfg.source("stores.lab") == ("env", str(named))
    assert config_files() == [("env", named)]
