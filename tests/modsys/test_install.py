"""Installing packs: store tarballs, URLs, git, paths, safety checks, consent, pins, sync,
remove, update, list and show. No network: respx serves GitHub, git uses file:// repos."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest
import respx

from pytacheck.module import ModuleError, module_run
from pytacheck.packs import ui
from pytacheck.packs.install import (
    Cancelled,
    pack_install,
    pack_list,
    pack_remove,
    pack_show,
    pack_update,
)
from pytacheck.packs.manifest import PackError
from pytacheck.packs.registry import get_pack
from pytacheck.packs.tree import INSTALL_RECORD
from tests.modsys.helpers import mod_src
from tests.modsys.storekit import (
    REV_C,
    REV_D,
    STORE_URL,
    FakeStore,
    codeload,
    dir_files,
    tarball,
)


@pytest.fixture
def store(ms, monkeypatch):
    monkeypatch.setenv("PYTACHECK_STORE_URL", STORE_URL)
    with respx.mock(assert_all_called=False) as router:
        yield FakeStore(router, ms)


def _installed_root(ms, name: str, rev: str) -> Path:
    return ms.data / "packs" / name / rev[:12]


def test_install_from_store(store, ms, paper) -> None:
    store.add("demo", {"hello": mod_src("hello", "hi")}, reviewed="2026-09-01")
    pack = pack_install("demo", yes=True)
    assert (pack.name, pack.trust, pack.rev, pack.reviewed) == (
        "demo",
        "store",
        REV_C,
        "2026-09-01",
    )
    root = _installed_root(ms, "demo", REV_C)
    record = json.loads((root / INSTALL_RECORD).read_text())
    assert record["rev"] == REV_C and record["store"] == "pytacheck"
    assert set(record["files"]) == {"pack.json", "hello.py"}
    assert stat.S_IMODE((root / "hello.py").stat().st_mode) & 0o222 == 0, "files are read-only"
    pin = store.config()["packs"]["demo"]
    assert pin["rev"] == REV_C and pin["store"] == "pytacheck"
    assert pin["source"] == {"github": "example/store", "subdir": "packs/demo"}
    assert pin["tree_sha256"] == store.entries["demo"]["tree_sha256"]
    out = module_run(paper, "demo::hello")
    assert out.summary_text == "hi"
    assert out.provenance["trust"] == "store" and out.provenance["source"]["rev"] == REV_C
    assert not list(ms.data.glob("packs/.staging*")), "staging is cleaned up"


def test_already_installed_is_just_pinned(store, ms, capsys) -> None:
    store.add("demo", {"hello": mod_src("hello")})
    pack_install("demo", yes=True)
    store.router.routes[f"tar-{REV_C}"].reset()
    pack_install("demo", yes=True)
    assert store.router.routes[f"tar-{REV_C}"].call_count == 0
    assert "already installed" in capsys.readouterr().err


def test_install_project_scope(store, ms, monkeypatch) -> None:
    user_file = ms.root / "user" / "config.json"
    monkeypatch.delenv("PYTACHECK_CONFIG")
    monkeypatch.setattr("pytacheck.config.user_config_path", lambda: user_file)
    store.add("demo", {"hello": mod_src("hello")})
    pack_install("demo", scope="project", yes=True)
    project = json.loads((ms.work / "pytacheck.json").read_text())
    assert project["packs"]["demo"]["rev"] == REV_C
    assert not user_file.exists()
    assert get_pack("demo").origin == str(ms.work / "pytacheck.json")
    store.add("other", {"other_mod": mod_src("other_mod")})
    pack_install("other", scope="user", yes=True)
    assert "other" in json.loads(user_file.read_text())["packs"]
    assert "other" not in json.loads((ms.work / "pytacheck.json").read_text())["packs"]


def test_tree_hash_mismatch_is_refused(store, ms) -> None:
    store.add("demo", {"hello": mod_src("hello")}, tamper={"tree_sha256": "0" * 64})
    with pytest.raises(PackError, match="tree hash"):
        pack_install("demo", yes=True)
    assert not (ms.data / "packs").exists()
    assert "demo" not in store.config().get("packs", {})


def _member(name: str, kind: bytes, linkname: str = "") -> tuple[tarfile.TarInfo, None]:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = linkname
    return info, None


@pytest.mark.parametrize(
    ("member", "why"),
    [
        (_member("store-x/packs/demo/link.py", tarfile.SYMTYPE, "/etc/passwd"), "is a link"),
        (_member("store-x/packs/demo/hard.py", tarfile.LNKTYPE, "store-x/x"), "is a link"),
        (_member("store-x/packs/demo/dev", tarfile.CHRTYPE), "device"),
        (_member("store-x/packs/demo/fifo", tarfile.FIFOTYPE), "device"),
        ((tarfile.TarInfo("store-x/../../evil.py"), b""), r"outside the archive \('..'\)"),
        ((tarfile.TarInfo("/abs/evil.py"), b""), "absolute path"),
        ((tarfile.TarInfo("other-top/evil.py"), b""), "single top-level folder"),
    ],
)
def test_unsafe_tarball_members_are_refused(store, ms, member, why) -> None:
    entry = store.add("demo", {"hello": mod_src("hello")})
    files = dir_files(ms.root / "src" / REV_C[:8] / "demo", "packs/demo/")
    info, data = member
    if data is not None:
        info.size = len(data)
    store.router.get(codeload("example/store", REV_C), name=f"tar-{REV_C}").respond(
        content=tarball(files, top="store-x", extra=[(info, data)])
    )
    assert entry["name"] == "demo"
    with pytest.raises(PackError, match=why):
        pack_install("demo", yes=True)
    assert not (ms.data / "packs").exists()
    assert not (ms.root / "evil.py").exists() and not Path("/abs/evil.py").exists()


def test_size_and_count_caps(store, ms, monkeypatch) -> None:
    store.add("demo", {"hello": mod_src("hello")}, files={"data/big.txt": "x" * 5000})
    monkeypatch.setattr("pytacheck.packs.fetch.MAX_BYTES", 1000)
    with pytest.raises(PackError, match="more than"):
        pack_install("demo", yes=True)
    monkeypatch.setattr("pytacheck.packs.fetch.MAX_BYTES", 10**9)
    monkeypatch.setattr("pytacheck.packs.fetch.MAX_FILES", 1)
    with pytest.raises(PackError, match="more than 1 files"):
        pack_install("demo", yes=True)


def test_consent_refusal_leaves_no_trace(store, ms, monkeypatch, capsys) -> None:
    store.add("demo", {"hello": mod_src("hello")}, yanked="broken on Windows")
    asked = []
    monkeypatch.setattr(ui, "confirm", lambda q, **_: asked.append(q) or False)
    with pytest.raises(Cancelled):
        pack_install("demo")
    assert asked == ["Install 'demo'?"]
    assert not (ms.data / "packs").exists()
    assert not ms.config_file.exists()
    card = capsys.readouterr().err
    assert "YANKED" in card and "broken on Windows" in card
    assert "hello.py" in card and REV_C in card


def test_consent_without_a_terminal_is_no(store, ms) -> None:
    store.add("demo", {"hello": mod_src("hello")})
    with pytest.raises(Cancelled):
        pack_install("demo")  # pytest's stdin is not a terminal


def test_consent_card_highlights_risky_code(store, ms, monkeypatch, capsys) -> None:
    risky = mod_src("sneaky", header="import socket, subprocess")
    store.add("demo", {"sneaky": risky}, dependencies=["surely-not-installed-pkg>=1"])
    monkeypatch.setattr(ui, "confirm", lambda *a, **k: True)
    pack_install("demo")
    err = capsys.readouterr().err
    assert "Look closely at" in err
    assert "subprocess" in err and "socket" in err
    assert "pip install 'surely-not-installed-pkg>=1'" in err


def test_code_free_pack_says_so(store, ms, capsys) -> None:
    store.add("presets-only", presets={"x": {"modules": ["marginal"]}})
    pack_install("presets-only", yes=True)
    assert "no code (presets only)" in capsys.readouterr().err


def test_sync_installs_missing_pins(store, ms, paper) -> None:
    entry = store.add("demo", {"hello": mod_src("hello", "synced")})
    ms.pin(
        "demo",
        {
            "source": {"github": "example/store", "subdir": "packs/demo"},
            "rev": REV_C,
            "tree_sha256": entry["tree_sha256"],
            "store": "pytacheck",
        },
    )
    with pytest.raises(ModuleError, match="not installed"):
        module_run(paper, "demo::hello")  # pinned, not installed
    installed = pack_install(yes=True)
    assert [p.name for p in installed] == ["demo"]
    assert module_run(paper, "demo::hello").summary_text == "synced"
    assert pack_install(yes=True) == []  # nothing left to do


def test_sync_refuses_a_tampered_source(store, ms) -> None:
    store.add("demo", {"hello": mod_src("hello")})
    ms.pin(
        "demo",
        {"source": {"github": "example/store", "subdir": "packs/demo"}, "rev": REV_C,
         "tree_sha256": "f" * 64},
    )  # fmt: skip
    with pytest.raises(PackError, match="tree hash"):
        pack_install(yes=True)


def test_remove(store, ms) -> None:
    store.add("demo", {"hello": mod_src("hello")})
    pack_install("demo", yes=True)
    root = _installed_root(ms, "demo", REV_C)
    assert root.is_dir()
    pack_remove("demo")
    assert "demo" not in store.config()["packs"]
    assert not root.exists() and not root.parent.exists()
    with pytest.raises(PackError, match="not pinned"):
        pack_remove("demo")


def test_update_shows_changes_and_repins(store, ms, paper, capsys) -> None:
    store.add("demo", {"hello": mod_src("hello", "old")})
    pack_install("demo", yes=True)
    assert pack_update("demo", yes=True)[0]["updated"] is False
    store.add(
        "demo",
        {"hello": mod_src("hello", "new"), "extra": mod_src("extra")},
        rev=REV_D,
        version="1.1.0",
    )
    capsys.readouterr()
    rows = pack_update("demo", yes=True)
    assert rows == [{"name": "demo", "from": REV_C, "to": REV_D, "updated": True}]
    err = capsys.readouterr().err
    assert "1.0.0" in err and "1.1.0" in err
    assert "added:" in err and "extra.py" in err and "changed:" in err
    assert store.config()["packs"]["demo"]["rev"] == REV_D
    assert module_run(paper, "demo::hello").summary_text == "new"


def test_update_asks_and_can_be_declined(store, ms, monkeypatch) -> None:
    store.add("demo", {"hello": mod_src("hello")})
    pack_install("demo", yes=True)
    store.add("demo", {"hello": mod_src("hello", "new")}, rev=REV_D)
    monkeypatch.setattr(ui, "confirm", lambda *a, **k: False)
    with pytest.raises(Cancelled):
        pack_update("demo")
    assert store.config()["packs"]["demo"]["rev"] == REV_C


def test_install_github_url_resolves_a_tag_once(ms, paper) -> None:
    folder = ms.pack(ms.root / "own", "own", {"mine": mod_src("mine", "from url")})
    with respx.mock(assert_all_called=True) as router:
        router.get("https://api.github.com/repos/jane/own-pack/commits/v1.0").respond(text=REV_D)
        router.get(codeload("jane/own-pack", REV_D)).respond(
            content=tarball(dir_files(folder), top=f"own-pack-{REV_D}")
        )
        pack = pack_install("https://github.com/jane/own-pack@v1.0", yes=True)
    assert (pack.name, pack.trust, pack.rev, pack.store) == ("own", "unlisted", REV_D, None)
    pin = json.loads(ms.config_file.read_text())["packs"]["own"]
    assert pin["source"] == {"github": "jane/own-pack"} and pin["rev"] == REV_D
    assert module_run(paper, "own::mine").summary_text == "from url"


def test_install_github_tree_url_with_subdir(ms) -> None:
    folder = ms.pack(ms.root / "sub", "sub", {"subm": mod_src("subm")})
    with respx.mock() as router:
        router.get("https://api.github.com/repos/jane/mono/commits/main").respond(text=REV_C)
        router.get(codeload("jane/mono", REV_C)).respond(
            content=tarball(dir_files(folder, "packs/sub/"), top="mono-x")
        )
        pack = pack_install("https://github.com/jane/mono/tree/main/packs/sub", yes=True)
    assert pack.source["subdir"] == "packs/sub"


def test_store_rev_prefix_installs_unlisted(store, ms) -> None:
    store.add("demo", {"hello": mod_src("hello")}, reviewed="2026-09-01")
    older = ms.pack(ms.root / "older", "demo", {"hello": mod_src("hello", "older")})
    store.router.get("https://api.github.com/repos/example/store/commits/dddd").respond(text=REV_D)
    store.router.get(codeload("example/store", REV_D)).respond(
        content=tarball(dir_files(older, "packs/demo/"), top="store-d")
    )
    pack = pack_install("demo@dddd", yes=True)
    assert (pack.rev, pack.trust, pack.reviewed) == (REV_D, "unlisted", None)
    same = pack_install("pytacheck/demo@cccc", yes=True)  # the listed commit: a store pack again
    assert (same.rev, same.trust) == (REV_C, "store")


def test_unknown_and_ambiguous_names(store, ms) -> None:
    with pytest.raises(PackError, match="No store lists a pack named 'nope'"):
        pack_install("nope", yes=True)
    with pytest.raises(PackError, match="Cannot read the pack reference"):
        pack_install("Not A Ref", yes=True)


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_install_from_a_file_git_repo(ms, paper) -> None:
    repo = ms.pack(ms.root / "gitpack", "gitpack", {"gm": mod_src("gm", "from git")})

    def git(*args: str) -> str:
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.org"}  # fmt: skip
        res = subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, text=True, env=env, check=True
        )
        return res.stdout.strip()

    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("commit", "-q", "-m", "first")
    sha = git("rev-parse", "HEAD")
    (repo / "later.txt").write_text("not in the pinned commit")
    git("add", "-A")
    git("commit", "-q", "-m", "second")
    pack = pack_install(f"file://{repo}@{sha}", yes=True)
    assert pack.rev == sha and pack.trust == "unlisted"
    assert not (pack.root / "later.txt").exists()
    assert module_run(paper, "gitpack::gm").summary_text == "from git"
    pin = json.loads(ms.config_file.read_text())["packs"]["gitpack"]
    assert pin["source"] == {"git": f"file://{repo}"}
    # a branch name resolves with git ls-remote, once
    pack_remove("gitpack")
    head = pack_install(f"git+file://{repo}@main", yes=True)
    assert head.rev == git("rev-parse", "HEAD") != sha


def test_git_urls_are_restricted() -> None:
    from pytacheck.packs.fetch import git_fetch

    for url in ("ext::sh -c touch% /tmp/pwned", "-uhoh", "http://example.org/x.git"):
        with pytest.raises(PackError):
            git_fetch(url, REV_C, "/nonexistent")
    with pytest.raises(PackError, match="40-character"):
        git_fetch("https://example.org/x.git", "main", "/nonexistent")


def test_install_path_pack(ms, paper) -> None:
    folder = ms.pack(ms.work / "lab", "lab", {"labmod": mod_src("labmod", "local")})
    pack = pack_install("./lab", yes=True)
    assert (pack.kind, pack.trust) == ("path", "local")
    assert json.loads(ms.config_file.read_text())["packs"]["lab"] == {"path": str(folder)}
    assert module_run(paper, "labmod").summary_text == "local"


def test_list_and_show(store, ms) -> None:
    validation = '{"papers": 10, "tp": 9, "fp": 1, "fn": 3}'
    src = mod_src("hello").replace("requires=[])", f"requires=[], validation={validation})")
    store.add("demo", {"hello": src}, presets={"quick": {"modules": ["hello"]}})
    listed = pack_show("demo")
    assert listed["installed"] is False and listed["store"] == "pytacheck"
    pack_install("demo", yes=True)
    df = pack_list()
    row = df[df["name"] == "demo"].iloc[0]
    assert (row["trust"], row["rev"], row["modules"], row["status"]) == (
        "store",
        REV_C[:12],
        1,
        "ok",
    )
    info = pack_show("demo")
    assert info["installed"] is True and info["trust"] == "store"
    assert info["modules"][0]["validation"]["ppv"] == 0.9
    assert info["modules"][0]["validation"]["sensitivity"] == 0.75
    assert info["presets"] == {"quick": ""}
    # a modified install is reported, not hidden
    target = Path(info["root"]) / "hello.py"
    target.chmod(0o644)
    target.write_text(target.read_text() + "\n# edited\n")
    assert pack_list().set_index("name").loc["demo", "status"] == "modified: hello.py"
    assert pack_show("demo")["modified"] == ["hello.py"]
