"""Name rules of the security baseline: reserved prefixes, and near-duplicate store names.

ECOSYSTEM.md section 4.8: no pack name may start with ``official`` or
``scienceverse``, and ``store build --check`` refuses a new pack or module name
one typo from a name already in use (``clinical_trial`` next to
``clinical_trials``), so a typo cannot pick up someone else's code.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
import respx

from metacheck.cli import main
from metacheck.packs.build import _within_one_edit, store_build
from metacheck.packs.manifest import PackError, validate_manifest, validate_pack_name
from tests.modsys.helpers import mod_src
from tests.modsys.storekit import REV_C, codeload, dir_files, tarball

# --- reserved prefixes -----------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "official",
        "officials",
        "official-stats",
        "official_psych",
        "scienceverse",
        "scienceverse-lab",
        "science-verse",
        "science_verse_checks",
    ],
)
def test_reserved_prefixes(name) -> None:
    with pytest.raises(PackError, match="reserved: no pack name may start with"):
        validate_pack_name(name)
    assert validate_pack_name(name, allow_reserved=True) == name
    with pytest.raises(PackError, match="reserved"):
        validate_manifest({"name": name})
    assert validate_manifest({"name": name}, builtin=True)["name"] == name


@pytest.mark.parametrize("name", ["lab-official", "my_scienceverse", "science", "verse", "offal"])
def test_names_that_only_contain_a_reserved_word_are_fine(name) -> None:
    assert validate_pack_name(name) == name


@pytest.mark.parametrize("name", ["metacheck\n", "local\n", "lab\n"])
def test_a_trailing_newline_does_not_dodge_the_name_rules(name) -> None:
    with pytest.raises(PackError, match="Invalid pack name"):
        validate_pack_name(name)


def test_store_build_refuses_a_reserved_prefix(ms) -> None:
    root = ms.root / "store"
    ms.pack(root / "packs" / "official-stats", "official-stats")
    _, issues = store_build(root, check=True)
    errors = {i.where: i.message for i in issues if i.level == "error"}
    assert "reserved" in errors["packs/official-stats"]


# --- one typo apart --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("clinical_trials", "clinical_trials"),
        ("clinical_trials", "clinical_trial"),  # one removed
        ("marginal", "marginals"),  # one added
        ("power", "tower"),  # one changed
        ("stat_check", "stat_chekc"),  # neighbours swapped
        ("ab", "ba"),
        ("", "a"),
    ],
)
def test_within_one_edit(a, b) -> None:
    assert _within_one_edit(a, b) and _within_one_edit(b, a)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("power", "pow"),
        ("marginal", "marginally"),
        ("stat_check", "stat_p_exact"),
        ("abcd", "badc"),  # two swaps
        ("abc", "cab"),
        ("coi_check", "coi_check_oi"),
        ("fields", "clinical_trials"),
    ],
)
def test_more_than_one_edit(a, b) -> None:
    assert not _within_one_edit(a, b) and not _within_one_edit(b, a)


def _store(ms) -> Path:
    """A store like the real one, with its index built: two packs, one module."""
    root = ms.root / "store"
    ms.pack(
        root / "packs" / "clinical_trials",
        "clinical_trials",
        {"trial_registration": mod_src("trial_registration")},
    )
    ms.pack(root / "packs" / "fields", "fields", presets={"general": {"modules": ["marginal"]}})
    _, issues = store_build(root)
    assert not [i for i in issues if i.level == "error"], issues
    return root


def _name_issues(issues, level="error"):
    return {i.where: i.message for i in issues if i.level == level and "typo" in i.message}


def test_the_real_stores_names_pass_even_without_an_index(ms) -> None:
    root = _store(ms)
    (root / "index.json").unlink()  # every name is new
    _, issues = store_build(root, check=True)
    assert not _name_issues(issues) and not _name_issues(issues, "warning")


@pytest.mark.parametrize(
    "name", ["clinical_trial", "clinical-trials", "clinicaltrials", "clinicaltrial"]
)
def test_a_new_pack_one_typo_from_a_listed_pack(ms, name) -> None:
    root = _store(ms)
    ms.pack(root / "packs" / name, name)
    _, issues = store_build(root, check=True)
    message = _name_issues(issues)[f"packs/{name}"]
    assert f"the pack name '{name}' is one typo from the pack 'clinical_trials'" in message
    assert main(["store", "build", str(root), "--check"]) == 1
    # without --check it is a warning, and the index lists the pack
    index, issues = store_build(root)
    assert f"packs/{name}" in _name_issues(issues, "warning")
    assert name in [p["name"] for p in index["packs"]]
    _, issues = store_build(root, check=True)
    assert not _name_issues(issues), "a listed name is not checked again"


def test_a_new_pack_one_typo_from_the_built_in_pack(ms) -> None:
    root = _store(ms)
    ms.pack(root / "packs" / "metachek", "metachek")
    _, issues = store_build(root, check=True)
    assert "one typo from the pack 'metacheck'" in _name_issues(issues)["packs/metachek"]


def test_a_new_module_one_typo_from_a_built_in_module(ms) -> None:
    root = _store(ms)
    ms.pack(root / "packs" / "lab", "lab", {"marginals": mod_src("marginals")})
    _, issues = store_build(root, check=True)
    message = _name_issues(issues)["packs/lab"]
    assert "the module name 'marginals' is one typo from 'metacheck::marginal'" in message


def test_a_new_module_named_like_a_built_in_module(ms) -> None:
    root = _store(ms)
    ms.pack(root / "packs" / "lab", "lab", {"power": mod_src("power")})
    _, issues = store_build(root, check=True)
    assert "'power' is the same as 'metacheck::power'" in _name_issues(issues)["packs/lab"]


@pytest.mark.parametrize("name", ["Trial_Registration", "TrialRegistratio"])
def test_a_new_module_one_typo_from_another_packs_module(ms, name) -> None:
    root = _store(ms)
    ms.pack(root / "packs" / "lab", "lab", {name: mod_src(name)})
    _, issues = store_build(root, check=True)
    message = _name_issues(issues)["packs/lab"]
    assert "one typo from 'clinical_trials::trial_registration'" in message


def test_a_module_added_to_a_listed_pack_is_checked(ms) -> None:
    root = _store(ms)
    folder = root / "packs" / "clinical_trials"
    (folder / "stat_chekc.py").write_text(mod_src("stat_chekc"))
    _, issues = store_build(root, check=True)
    assert "one typo from 'metacheck::stat_check'" in _name_issues(issues)["packs/clinical_trials"]


def test_a_packs_own_modules_may_be_close(ms) -> None:
    root = _store(ms)
    ms.pack(
        root / "packs" / "lab",
        "lab",
        {"check_a": mod_src("check_a"), "check_b": mod_src("check_b")},
    )
    _, issues = store_build(root, check=True)
    assert not _name_issues(issues)


def test_close_names_already_listed_are_not_checked_again(ms) -> None:
    root = _store(ms)
    ms.pack(root / "packs" / "lab", "lab", {"marginals": mod_src("marginals")})
    ms.pack(root / "packs" / "labs", "labs")
    _, issues = store_build(root)  # a maintainer builds the index anyway
    assert set(_name_issues(issues, "warning")) == {"packs/lab", "packs/labs"}
    _, issues = store_build(root, check=True)
    assert not _name_issues(issues)


def test_a_renamed_pack_is_not_compared_with_its_old_name(ms) -> None:
    root = _store(ms)
    shutil.rmtree(root / "packs" / "clinical_trials")
    ms.pack(
        root / "packs" / "clinical_trial",
        "clinical_trial",
        {"trial_registration": mod_src("trial_registration")},
    )
    _, issues = store_build(root, check=True)
    assert not _name_issues(issues)


def test_an_entry_file_one_typo_from_a_listed_pack(ms) -> None:
    root = _store(ms)
    own = ms.pack(ms.root / "own", "fieldz")
    entry = {"name": "fieldz", "source": {"github": "jane/fieldz", "rev": REV_C}}
    (root / "packs" / "fieldz.json").write_text(json.dumps(entry))
    with respx.mock() as router:
        router.get(codeload("jane/fieldz", REV_C)).respond(content=tarball(dir_files(own)))
        _, issues = store_build(root, check=True)
    assert "one typo from the pack 'fields'" in _name_issues(issues)["packs/fieldz"]


# --- a trailing newline never passes a name check ---------------------------------------


def _stray_file(folder: Path) -> None:
    """Add a module file whose stem ends in a newline (not possible on Windows)."""
    if os.name == "nt":
        pytest.skip("a newline cannot be part of a file name on Windows")
    (folder / "check_b\n.py").write_text("x = 1\n")


def test_a_module_file_with_a_trailing_newline_is_not_a_module(tmp_path, monkeypatch) -> None:
    from metacheck.packs import manifest
    from metacheck.packs.manifest import Pack

    (tmp_path / "check_a.py").write_text("x = 1\n")
    monkeypatch.setattr(manifest.os, "listdir", lambda _: ["check_a.py", "check_b\n.py"])
    pack = Pack(name="lab", root=tmp_path, kind="path", trust="local")
    assert pack.modules() == ["check_a"]
    assert pack.has_module("check_a")
    assert not pack.has_module("check_b\n")


def test_pack_check_warns_about_a_module_file_with_a_trailing_newline(ms) -> None:
    from metacheck.packs.check import pack_check

    folder = ms.pack(ms.root / "lab", "lab", {"check_a": mod_src("check_a")})
    _stray_file(folder)
    issues = pack_check(folder, run=False)
    assert [i.where for i in issues if i.code == "name"] == ["check_b\n.py"]


def test_store_build_lists_no_module_file_with_a_trailing_newline(ms) -> None:
    from metacheck.packs.build import _pack_fields
    from metacheck.packs.manifest import read_manifest

    folder = ms.pack(ms.root / "lab", "lab", {"check_a": mod_src("check_a")})
    _stray_file(folder)
    entry = _pack_fields(folder, read_manifest(folder))
    assert [m["name"] for m in entry["modules"]] == ["check_a"]


def test_a_reference_with_a_trailing_newline_is_not_a_reference() -> None:
    from metacheck.module import split_ref
    from metacheck.packs.registry import find_module

    assert split_ref("lab::mod") == ("lab", "mod")
    assert split_ref("lab::mod\n") is None
    assert find_module("mod\n") == []


def test_a_preset_name_with_a_trailing_newline_is_refused() -> None:
    from metacheck.packs.manifest import validate_preset

    validate_preset("fast", {})
    with pytest.raises(PackError, match="Invalid preset name"):
        validate_preset("fast\n", {})


def test_a_store_name_with_a_trailing_newline_is_refused() -> None:
    from metacheck.packs.stores import StoreError, _validate_store_name

    assert _validate_store_name("lab") == "lab"
    with pytest.raises(StoreError, match="Invalid store name"):
        _validate_store_name("lab\n")


def test_scaffold_names_with_a_trailing_newline_are_refused(tmp_path) -> None:
    from metacheck.packs.scaffold import module_template, pack_new

    with pytest.raises(ValueError, match="only letters"):
        module_template("my_check\n", tmp_path)
    with pytest.raises(PackError, match="Invalid module name"):
        pack_new("lab", tmp_path, module="my_check\n")


def test_a_source_slug_or_git_ref_with_a_trailing_newline_is_refused() -> None:
    from metacheck.packs.fetch import _slug, git_read_file

    assert _slug({"github": "jane/my-pack"}, "github") == "jane/my-pack"
    with pytest.raises(PackError, match="Invalid github source"):
        _slug({"github": "jane/my-pack\n"}, "github")
    with pytest.raises(PackError, match="Invalid git ref"):
        git_read_file("https://example.org/r.git", "main\n", "pack.json", limit=10)


def test_a_store_entry_rev_with_a_trailing_newline_is_refused(ms) -> None:
    root = _store(ms)
    entry = {"name": "fieldz", "source": {"github": "jane/fieldz", "rev": REV_C + "\n"}}
    (root / "packs" / "fieldz.json").write_text(json.dumps(entry))
    _, issues = store_build(root, check=True)
    assert any(
        i.where == "packs/fieldz.json" and "full 40-hex commit SHA" in i.message for i in issues
    )
