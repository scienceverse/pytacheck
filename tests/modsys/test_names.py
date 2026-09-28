"""Name rules of the security baseline: reserved prefixes, and near-duplicate store names.

ECOSYSTEM.md section 4.8: no pack name may start with ``official`` or
``scienceverse``, and ``store build --check`` refuses a new pack or module name
one typo from a name already in use (``clinical_trial`` next to
``clinical_trials``), so a typo cannot pick up someone else's code.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import respx

from pytacheck.cli import main
from pytacheck.packs.build import _within_one_edit, store_build
from pytacheck.packs.manifest import PackError, validate_manifest, validate_pack_name
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
