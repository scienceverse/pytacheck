"""``store build``: index.json for a store repo, ``--check``, git revs, and the contrib seed."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import respx

from pytacheck.cli import main
from pytacheck.module import module_run
from pytacheck.packs.build import store_build
from pytacheck.packs.check import pack_check
from pytacheck.packs.install import pack_install
from pytacheck.packs.tree import tree_sha256
from tests.modsys.helpers import mod_src
from tests.modsys.storekit import REV_C, codeload, dir_files, tarball

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "contrib" / "pytacheck-modules"
VALIDATION = '{"papers": 20, "instances": 30, "tp": 24, "fp": 6, "fn": 6}'


def make_store(ms, root: Path) -> Path:
    code = mod_src("counts", keywords=("method",)).replace(
        "requires=[])", f"requires=[], validation={VALIDATION})"
    )
    ms.pack(
        root / "packs" / "alpha",
        "alpha",
        {"counts": code},
        title="Alpha",
        fields=["psychology"],
        license="MIT",
        presets={"default": {"description": "Alpha's checks", "modules": ["counts"]}},
        files={"counts.R": "counts <- function(paper) list()\n", "tests/test_a.py": "x = 1\n"},
    )
    ms.pack(
        root / "packs" / "beta",
        "beta",
        presets={"only": {"description": "Just marginal", "modules": ["marginal"]}},
    )
    (root / "store.json").write_text(json.dumps({"name": "lab", "description": "Lab store"}))
    return root


def git(root: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.org"}  # fmt: skip
    res = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, env=env, check=True
    )
    return res.stdout.strip()


def test_build_without_git_history(ms) -> None:
    root = make_store(ms, ms.root / "store")
    index, issues = store_build(root)
    assert not [i for i in issues if i.level == "error"]
    assert any("no git history" in i.message for i in issues)
    assert (index["name"], index["description"], index["schema"]) == ("lab", "Lab store", 1)
    alpha, beta = index["packs"]
    assert alpha["source"] == {"path": "packs/alpha"}
    assert alpha["tree_sha256"] == tree_sha256(root / "packs" / "alpha")
    assert (alpha["code"], alpha["languages"]) == (True, ["python", "r"])
    assert alpha["modules"] == [
        {
            "name": "counts",
            "title": "Counts",
            "description": "The counts module",
            "section": "method",
            "requires": [],
            "validation": {
                "papers": 20, "instances": 30, "tp": 24, "fp": 6, "fn": 6,
                "ppv": 0.8, "sensitivity": 0.8,
            },
        }
    ]  # fmt: skip
    assert alpha["presets"] == {"default": "Alpha's checks"}
    assert (beta["code"], beta["languages"], beta["modules"]) == (False, [], [])
    written = json.loads((root / "index.json").read_text())
    assert written == index
    again, _ = store_build(root)
    assert again["generated"] == index["generated"], "unchanged stores keep their timestamp"


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_build_uses_the_last_commit_touching_each_pack(ms) -> None:
    root = make_store(ms, ms.root / "store")
    git(root, "init", "-q", "-b", "main")
    git(root, "remote", "add", "origin", "https://github.com/lab/pytacheck-store.git")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "packs")
    first = git(root, "rev-parse", "HEAD")
    (root / "packs" / "beta" / "README.md").write_text("beta\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "beta readme")
    second = git(root, "rev-parse", "HEAD")
    index, issues = store_build(root)
    assert not [i for i in issues if i.level == "error"], issues
    sources = {p["name"]: p["source"] for p in index["packs"]}
    assert sources["alpha"] == {"github": "lab/pytacheck-store", "rev": first, "subdir": "packs/alpha"}
    assert sources["beta"] == {"github": "lab/pytacheck-store", "rev": second, "subdir": "packs/beta"}
    index, _ = store_build(root, repo="other/repo", check=True)
    assert index["packs"][0]["source"]["github"] == "other/repo"


def test_external_entries(ms) -> None:
    root = ms.root / "store"
    (root / "packs").mkdir(parents=True)
    own = ms.pack(ms.root / "own", "ext", {"extm": mod_src("extm")}, version="2.0.0")
    entry = {"name": "ext", "source": {"github": "jane/ext", "rev": REV_C}, "version": "2.0.0"}
    (root / "packs" / "ext.json").write_text(json.dumps(entry))
    with respx.mock() as router:
        router.get(codeload("jane/ext", REV_C)).respond(content=tarball(dir_files(own)))
        index, issues = store_build(root, check=True)
        assert not [i for i in issues if i.level == "error"], issues
        (pack,) = index["packs"]
        assert pack["source"] == {"github": "jane/ext", "rev": REV_C}
        assert pack["tree_sha256"] == tree_sha256(own)
        assert pack["version"] == "2.0.0" and pack["modules"][0]["name"] == "extm"
        assert not (root / "index.json").exists(), "--check writes nothing"
        entry["version"] = "1.0.0"
        (root / "packs" / "ext.json").write_text(json.dumps(entry))
        _, issues = store_build(root, check=True)
    assert any("declares version '1.0.0'" in i.message for i in issues if i.level == "error")


def test_check_catches_bad_entries(ms) -> None:
    root = make_store(ms, ms.root / "store")
    packs = root / "packs"
    (packs / "short.json").write_text(json.dumps({"source": {"github": "a/b", "rev": "abc123"}}))
    (packs / "alpha.json").write_text(json.dumps({"source": {"github": "a/b", "rev": REV_C}}))
    (packs / "named.json").write_text(json.dumps({"name": "other", "source": {}}))
    ms.pack(packs / "gamma", "wrong-name")
    ms.pack(packs / "delta", "delta", reviewed="2026-01-01")
    _, issues = store_build(root, check=True)
    errors = {i.where: i.message for i in issues if i.level == "error"}
    assert "40-hex" in errors["packs/short.json"]
    assert "both a folder and a source" in errors["packs/alpha"]
    assert "names the pack 'other'" in errors["packs/named.json"]
    assert "not 'gamma'" in errors["packs/gamma"]
    assert "may not set 'reviewed'" in errors["packs/delta"]
    assert main(["store", "build", str(root), "--check"]) == 1
    assert main(["store", "build", str(root)]) == 1
    assert not (root / "index.json").exists()


def test_reviewed_and_yanked(ms) -> None:
    root = make_store(ms, ms.root / "store")
    index, _ = store_build(root)
    index["packs"][0]["reviewed"] = "2026-09-01"  # set by a maintainer
    (root / "index.json").write_text(json.dumps(index))
    (root / "packs" / "beta.json").write_text(json.dumps({"yanked": "superseded"}))
    index, _ = store_build(root)
    alpha, beta = index["packs"]
    assert alpha["reviewed"] == "2026-09-01" and beta["yanked"] == "superseded"
    (root / "packs" / "alpha" / "counts.py").write_text(mod_src("counts", "changed"))
    index, issues = store_build(root)
    assert index["packs"][0]["reviewed"] is None
    assert any("changed since it was reviewed" in i.message for i in issues)


def test_contrib_seed_index_is_current_and_its_packs_pass(ms, tmp_path) -> None:
    if not SEED.is_dir():
        pytest.skip("no contrib seed")
    copy = tmp_path / "seed"
    shutil.copytree(SEED, copy, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    committed = json.loads((SEED / "index.json").read_text())
    index, issues = store_build(copy, repo="thesanogoeffect/pytacheck-modules")
    assert not [i for i in issues if i.level == "error"], issues
    assert index["packs"] == committed["packs"], "regenerate contrib/pytacheck-modules/index.json"
    assert {p["name"] for p in index["packs"]} == {"fields", "clinical_trials"}
    for pack in ("fields", "clinical_trials"):
        assert [i for i in pack_check(copy / "packs" / pack) if i.level == "error"] == []


def test_install_from_the_contrib_seed(ms, monkeypatch, tmp_path) -> None:
    if not SEED.is_dir():
        pytest.skip("no contrib seed")
    import pytacheck as pc

    copy = tmp_path / "seed"
    shutil.copytree(SEED, copy, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    monkeypatch.setenv("PYTACHECK_STORE_URL", str(copy))
    pack = pack_install("clinical_trials", yes=True)
    assert pack.trust == "store" and pack.rev is None
    paper = pc.test_paper(["The trial was registered (NCT01234567)."])
    out = module_run(paper, "clinical_trials::trial_registration")
    assert out.traffic_light == "green" and out.table["trial_id"].tolist() == ["NCT01234567"]
    pack_install("fields", yes=True)
    from pytacheck.presets import expand

    psych = [m for m, _ in expand("fields::psychology")]
    assert psych[-4:] == ["ethics_check", "open_practices", "all_p_values", "causal_claims"]
    assert "ref_replication" not in [m for m, _ in expand("fields::medicine")]
