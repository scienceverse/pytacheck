"""pack.json validation, the pytacheck-tree-v1 hash and lazy importing."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

from pytacheck.packs.manifest import (
    FIELDS,
    Pack,
    PackError,
    read_manifest,
    validate_manifest,
    validate_pack_name,
    validate_preset,
)
from pytacheck.packs.registry import builtin_pack, install_dir, pin_rev12
from pytacheck.packs.tree import tree_files, tree_sha256


@pytest.mark.parametrize("name", ["ab", "psych", "my-pack", "lab_2", "a" * 40])
def test_valid_pack_names(name) -> None:
    assert validate_pack_name(name) == name


@pytest.mark.parametrize("name", ["a", "Psych", "1abc", "-x", "a" * 41, "my pack", "x.y", None, 3])
def test_invalid_pack_names(name) -> None:
    with pytest.raises(PackError, match="Invalid pack name"):
        validate_pack_name(name)


@pytest.mark.parametrize("name", ["metacheck", "local", "pytacheck", "modules"])
def test_reserved_pack_names(name) -> None:
    with pytest.raises(PackError, match="reserved"):
        validate_pack_name(name)
    assert validate_pack_name(name, allow_reserved=True) == name


def test_manifest_example_from_the_design() -> None:
    data = {
        "schema": 1,
        "name": "psych",
        "version": "1.2.0",
        "title": "Psychology reporting checks",
        "description": "Reporting checks for psychology papers.",
        "authors": ["Jane Doe <jane@uni.edu>"],
        "license": "MIT",
        "homepage": "https://github.com/janedoe/pytacheck-psych",
        "fields": ["psychology"],
        "keywords": ["apa", "reporting"],
        "requires": {"pytacheck": ">=0.3.1"},
        "dependencies": [],
        "presets": {
            "default": {
                "description": "metacheck's defaults plus psychology checks",
                "extends": ["metacheck::default"],
                "replace": {"power": "power_sesoi"},
                "exclude": ["ref_pubpeer"],
                "modules": ["apa_df", "manipulation_check"],
                "args": {"apa_df": {"strict": True}},
            },
            "minimal": {"description": "Only the psychology checks", "modules": ["apa_df"]},
        },
    }
    out = validate_manifest(data)
    assert out["presets"]["minimal"] == {
        "description": "Only the psychology checks",
        "extends": [],
        "exclude": [],
        "modules": ["apa_df"],
        "dependencies": [],
        "replace": {},
        "args": {},
    }
    assert set(FIELDS) >= set(out["fields"])


@pytest.mark.parametrize(
    ("data", "error"),
    [
        ({"name": "ok", "schema": 2}, "schema 2"),
        ({"name": "Bad"}, "Invalid pack name"),
        ({"name": "metacheck"}, "reserved"),
        ({"name": "ok", "version": 1}, "version must be a string"),
        ({"name": "ok", "fields": [1]}, "fields must be a list of strings"),
        ({"name": "ok", "presets": []}, "presets must be an object"),
        ({"name": "ok", "presets": {"bad name": {}}}, "Invalid preset name"),
        ({"name": "ok", "presets": {"p": {"replace": {"a": 1}}}}, "replace must map"),
        ({"name": "ok", "presets": {"p": {"extras": 1}}}, "unknown keys"),
        ({"name": "ok", "requires": {"pytacheck": 1}}, "requires must map"),
        ([], "must hold a JSON object"),
    ],
)
def test_manifest_errors(data, error) -> None:
    with pytest.raises(PackError, match=error):
        validate_manifest(data)


def test_preset_accepts_single_strings() -> None:
    out = validate_preset("p", {"extends": "metacheck::default", "modules": "x"})
    assert out["extends"] == ["metacheck::default"] and out["modules"] == ["x"]


def test_read_manifest_and_modules(tmp_path) -> None:
    with pytest.raises(PackError, match=r"No pack\.json"):
        read_manifest(tmp_path)
    (tmp_path / "pack.json").write_text(json.dumps({"name": "demo", "version": "0.1"}))
    for f in ("alpha.py", "_helper.py", "Beta.py", "not-a-module.py", "notes.R", "README.md"):
        (tmp_path / f).write_text("")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "x.py").write_text("")
    pack = Pack.from_manifest(read_manifest(tmp_path), root=tmp_path, kind="path", trust="local")
    assert pack.modules() == ["Beta", "alpha"]
    assert pack.has_module("alpha") and not pack.has_module("_helper")


def test_builtin_manifest() -> None:
    pack = builtin_pack()
    assert (pack.name, pack.kind, pack.trust) == ("metacheck", "builtin", "builtin")
    assert set(pack.presets) == {"default", "repository", "validated"}
    assert "version" not in json.loads((pack.root / "pack.json").read_text())


def test_install_dir(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PYTACHECK_DATA_DIR", str(tmp_path))
    assert install_dir("psych", {"rev": "0123456789abcdef" * 2 + "01234567"}) == (
        tmp_path / "packs" / "psych" / "0123456789ab"
    )
    assert pin_rev12({"tree_sha256": "f" * 64}) == "f" * 12
    with pytest.raises(PackError, match="needs a 'rev'"):
        pin_rev12({"rev": "main"})


def _tree(root) -> None:
    files = {
        "pack.json": '{"name": "demo"}\n',
        "alpha.py": "x = 1\n",
        "B.py": "upper sorts first in C\n",
        "_helper.py": "",
        "a b.txt": "space\n",
        "a.py": "a\n",
        "a/b.txt": "slash sorts after dot\n",
        "data/x.csv": "1,2\n",
        "é.txt": "utf-8 name\n",
        ".hidden": "dotfile\n",
        # excluded:
        "__pycache__/alpha.cpython-312.pyc": "bytecode",
        "sub/__pycache__/y.pyc": "bytecode",
        "z.pyc": "bytecode",
        ".git/HEAD": "ref: refs/heads/main\n",
        ".pytacheck-install.json": "{}",
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    (root / "link.py").symlink_to(root / "alpha.py")  # not a regular file


def test_tree_files_and_hash(tmp_path) -> None:
    _tree(tmp_path)
    assert tree_files(tmp_path) == [
        ".hidden", "B.py", "_helper.py", "a b.txt", "a.py", "a/b.txt", "alpha.py",
        "data/x.csv", "pack.json", "é.txt",
    ]  # fmt: skip
    first = tree_sha256(tmp_path)
    (tmp_path / "__pycache__" / "new.pyc").write_text("ignored")
    assert tree_sha256(tmp_path) == first
    (tmp_path / "alpha.py").write_text("x = 2\n")
    assert tree_sha256(tmp_path) != first


@pytest.mark.skipif(
    not (shutil.which("find") and shutil.which("sha256sum") and shutil.which("xargs")),
    reason="needs GNU find, sort, xargs and sha256sum",
)
def test_tree_hash_equals_sha256sum_pipeline(tmp_path) -> None:
    _tree(tmp_path)
    cmd = (
        "find . -type f ! -path '*/.git/*' ! -path '*/__pycache__/*' ! -name '*.pyc' "
        "! -name .pytacheck-install.json -printf '%P\\0' "
        "| LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum"
    )
    out = subprocess.run(
        ["sh", "-c", cmd], cwd=tmp_path, capture_output=True, text=True, check=True
    ).stdout
    assert out.split()[0] == tree_sha256(tmp_path)


def test_import_pytacheck_does_not_import_packs() -> None:
    code = (
        "import sys, pytacheck as pc\n"
        "assert 'pytacheck.packs' not in sys.modules\n"
        "pc.module_run(pc.demopaper(), 'marginal')\n"
        "pc.module_list()\n"
        "assert 'pytacheck.packs' not in sys.modules, 'built-in runs must not need packs'\n"
        "pc.refresh\n"
        "assert 'pytacheck.packs' in sys.modules\n"
    )
    env = {**os.environ, "PYTACHECK_CONFIG": "none"}
    subprocess.run([sys.executable, "-c", code], check=True, env=env)
