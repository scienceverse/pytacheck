"""Private stores: GitHub tokens, the hosts they go to, the API paths, the git fallback, no leaks.

Everything but the last test is served by respx (a mocked transport); the last
one reads the real (private) default store and runs only with ``-m network``
and a token in the environment.
"""

from __future__ import annotations

import contextlib
import io
import json
import logging
import os
import re
import secrets
import shutil
import warnings
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from metacheck.cli import main
from metacheck.packs import auth, fetch
from metacheck.packs.auth import DownloadError, get, github_token, may_send_token, token_var
from metacheck.packs.fetch import resolve_rev
from metacheck.packs.install import pack_install
from metacheck.packs.manifest import PackError
from metacheck.packs.stores import StoreError, store_index
from metacheck.packs.tree import INSTALL_RECORD
from tests.modsys.helpers import mod_src
from tests.modsys.storekit import (
    INDEX_URL,
    REV_C,
    REV_D,
    STORE_REPO,
    STORE_URL,
    codeload,
    dir_files,
    entry_for,
    tarball,
)

TOKEN = f"ghp_SENTINEL{secrets.token_hex(12)}"
CODELOAD_TOKEN = f"CLSENTINEL{secrets.token_hex(12)}"
API = "https://api.github.com"
CONTENTS = f"{API}/repos/{STORE_REPO}/contents/index.json"


class PrivateGitHub:
    """A private GitHub store repo: the API serves it to the token, raw and codeload do not.

    Every request is recorded as ``(url, Authorization header)``.
    """

    def __init__(self, router: Any, ms: Any, token: str = TOKEN) -> None:
        self.ms, self.token = ms, token
        self.entries: dict[str, dict[str, Any]] = {}
        self.trees: dict[str, dict[str, bytes]] = {}
        self.seen: list[tuple[str, str | None]] = []
        self.public = False  # True: raw and codeload serve it without a token too
        router.route().mock(side_effect=self.handle)

    def add(self, name: str, modules: dict[str, str], *, rev: str = REV_C) -> dict[str, Any]:
        folder = self.ms.pack(self.ms.root / "src" / rev[:8] / name, name, modules)
        self.trees.setdefault(rev, {}).update(dir_files(folder, f"packs/{name}/"))
        source = {"github": STORE_REPO, "rev": rev, "subdir": f"packs/{name}"}
        self.entries[name] = entry_for(folder, source)
        return self.entries[name]

    def index(self) -> bytes:
        return json.dumps(
            {"schema": 1, "name": "pytacheck", "packs": list(self.entries.values())}
        ).encode()

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        self.seen.append((str(url), request.headers.get("Authorization")))
        header = request.headers.get("Authorization")
        authorized = header == f"Bearer {self.token}"
        path = url.path
        if url.host == "api.github.com":
            if header is not None and not authorized:
                return httpx.Response(401, json={"message": "Bad credentials"})
            if not authorized:
                return httpx.Response(404, json={"message": "Not Found"})
            if path == f"/repos/{STORE_REPO}/contents/index.json":
                assert request.headers.get("Accept") == "application/vnd.github.raw"
                assert url.params.get("ref") == "HEAD"
                return httpx.Response(200, content=self.index())
            m = re.match(rf"^/repos/{STORE_REPO}/tarball/([0-9a-f]{{40}})$", path)
            if m:
                where = f"https://codeload.github.com/{STORE_REPO}/legacy.tar.gz/{m.group(1)}"
                return httpx.Response(302, headers={"Location": f"{where}?token={CODELOAD_TOKEN}"})
            m = re.match(rf"^/repos/{STORE_REPO}/commits/(.+)$", path)
            if m:
                return httpx.Response(200, text=REV_D if m.group(1) == "main" else REV_C)
        if url.host == "codeload.github.com":
            m = re.match(rf"^/{STORE_REPO}/(?:legacy\.tar\.gz|tar\.gz)/([0-9a-f]{{40}})$", path)
            signed = url.params.get("token") == CODELOAD_TOKEN
            if m and (signed or self.public) and m.group(1) in self.trees:
                return httpx.Response(200, content=tarball(self.trees[m.group(1)]))
        if url.host == "raw.githubusercontent.com" and self.public:
            return httpx.Response(200, content=self.index())
        return httpx.Response(404, text="404: Not Found")

    def authorized_hosts(self) -> set[str]:
        return {httpx.URL(u).host for u, a in self.seen if a is not None}


@pytest.fixture
def private(ms, monkeypatch):
    monkeypatch.setenv("PYTACHECK_STORE_URL", STORE_URL)
    monkeypatch.setenv("PYTACHECK_GITHUB_TOKEN", TOKEN)
    with respx.mock(assert_all_called=False) as router:
        gh = PrivateGitHub(router, ms)
        gh.add("demo", {"hello": mod_src("hello", "hi from a private store")})
        yield gh


# --- tokens and hosts ----------------------------------------------------------------------


def test_token_discovery_order(monkeypatch) -> None:
    assert github_token() is None and token_var() is None
    monkeypatch.setenv("GITHUB_TOKEN", "from-github-token")
    assert (token_var(), github_token()) == ("GITHUB_TOKEN", "from-github-token")
    monkeypatch.setenv("GH_TOKEN", " from-gh-token\n")
    assert (token_var(), github_token()) == ("GH_TOKEN", "from-gh-token")
    monkeypatch.setenv("PYTACHECK_GITHUB_TOKEN", "from-pytacheck")
    assert (token_var(), github_token()) == ("PYTACHECK_GITHUB_TOKEN", "from-pytacheck")
    monkeypatch.setenv("PYTACHECK_GITHUB_TOKEN", "   ")  # empty: the next one counts
    assert token_var() == "GH_TOKEN"


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://api.github.com/repos/o/r/contents/index.json", True),
        ("https://github.com/o/r", True),
        ("https://raw.githubusercontent.com/o/r/HEAD/index.json", True),
        ("https://codeload.github.com/o/r/tar.gz/main", True),
        ("https://API.GitHub.com/repos/o/r", True),  # hosts are case-insensitive
        ("http://api.github.com/repos/o/r", False),
        ("https://api.github.com:443/repos/o/r", False),
        ("https://api.github.com:8443/repos/o/r", False),
        ("https://user@api.github.com/repos/o/r", False),
        ("https://user:pw@api.github.com/repos/o/r", False),
        ("https://api.github.com@evil.example.org/", False),
        ("https://evil.example.org\\@api.github.com/", False),
        ("https://evil.example.org#@api.github.com/", False),
        ("https://evil.example.org/api.github.com", False),
        ("https://api.github.com.evil.example.org/", False),
        ("https://api.github.com./repos/o/r", False),
        ("https://gist.github.com/o/1", False),
        ("https://uploads.github.com/o", False),
        ("https://objects.githubusercontent.com/x", False),
        ("https://gitlab.com/o/r", False),
        (" https://api.github.com/", False),
        ("https://api.github.com/a b", False),
        ("https:api.github.com/x", False),
        ("ftp://api.github.com/x", False),
    ],
)
def test_host_allow_list(url: str, ok: bool) -> None:
    assert may_send_token(url) is ok


def test_the_token_goes_only_to_github_hosts(monkeypatch) -> None:
    monkeypatch.setenv("GH_TOKEN", TOKEN)
    with respx.mock() as router:
        route = router.route().respond(200, text="ok")
        for url in ("https://api.github.com/x", "https://example.org/x", "http://api.github.com/x"):
            assert get(url, token=True).ok
        get("https://api.github.com/y")  # token=False: never sent
        sent = [(str(c.request.url), c.request.headers.get("Authorization")) for c in route.calls]
    assert sent == [
        ("https://api.github.com/x", f"Bearer {TOKEN}"),
        ("https://example.org/x", None),
        ("http://api.github.com/x", None),
        ("https://api.github.com/y", None),
    ]


@pytest.mark.parametrize(
    ("target", "keeps_token"),
    [
        ("https://evil.example.org/steal", False),
        ("https://codeload.github.com/o/r/legacy.tar.gz/x?token=abc", False),  # another origin
        ("http://api.github.com/b", False),  # a downgrade is another origin
        ("/b", True),  # same origin (a renamed repository)
    ],
)
def test_redirects_drop_the_token_when_they_leave_the_origin(monkeypatch, target, keeps_token):
    monkeypatch.setenv("GH_TOKEN", TOKEN)
    with respx.mock() as router:
        router.get("https://api.github.com/a").respond(302, headers={"Location": target})
        final = router.route(path__regex=r"^/(b|steal|o/r/legacy\.tar\.gz/x)$").respond(200)
        res = get("https://api.github.com/a", token=True)
        assert res.ok and res.url == "https://api.github.com/a"
        (call,) = final.calls
    assert call.request.headers.get("Authorization") == (f"Bearer {TOKEN}" if keeps_token else None)


def test_401_with_a_token_is_retried_once_without_it(monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "expired-token")

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401 if "Authorization" in request.headers else 200, text="public")

    with respx.mock() as router:
        route = router.get("https://api.github.com/x").mock(side_effect=answer)
        res = get("https://api.github.com/x", token=True)
        auths = [c.request.headers.get("Authorization") for c in route.calls]
    assert res.ok and res.rejected and not res.token and res.text == "public"
    assert auths == ["Bearer expired-token", None]
    with respx.mock() as router:
        route = router.get("https://api.github.com/x").respond(401)
        res = get("https://api.github.com/x", token=True)
        assert route.call_count == 2 and res.status == 401
        assert "GitHub rejected the token in GITHUB_TOKEN" in res.problem()
        assert "expired-token" not in res.problem() + repr(res)
        monkeypatch.delenv("GITHUB_TOKEN")
        route.reset()
        assert get("https://api.github.com/x", token=True).status == 401
        assert route.call_count == 1  # no token: nothing to retry without


def test_connection_errors_name_the_url_asked_for_not_the_redirect(monkeypatch) -> None:
    monkeypatch.setenv("GH_TOKEN", TOKEN)
    secret = "https://codeload.github.com/o/r/legacy.tar.gz/x?token=SECRETSECRET"
    with respx.mock() as router:
        router.get("https://api.github.com/a").respond(302, headers={"Location": secret})
        router.get(url__startswith="https://codeload.github.com/").mock(
            side_effect=httpx.ConnectError(f"cannot reach {secret}")
        )
        with pytest.raises(DownloadError) as info:
            get("https://api.github.com/a", token=True)
    assert "https://api.github.com/a" in str(info.value)
    assert "SECRETSECRET" not in str(info.value) and TOKEN not in str(info.value)


# --- private stores ----------------------------------------------------------------------


def test_private_index_comes_from_the_contents_api(private, ms) -> None:
    index = store_index("pytacheck")
    assert [p["name"] for p in index["packs"]] == ["demo"]
    assert private.seen[0] == (f"{CONTENTS}?ref=HEAD", f"Bearer {TOKEN}")
    cache = ms.data / "stores" / "pytacheck"
    assert json.loads((cache / "meta.json").read_text())["url"] == INDEX_URL  # unchanged key
    assert private.authorized_hosts() == {"api.github.com"}


def test_public_store_without_a_token_works_as_before(private, ms, monkeypatch) -> None:
    monkeypatch.delenv("PYTACHECK_GITHUB_TOKEN")
    private.public = True
    assert store_index("pytacheck")["packs"][0]["name"] == "demo"
    pack = pack_install("demo", yes=True)
    assert pack.rev == REV_C
    assert [u for u, _ in private.seen] == [INDEX_URL, codeload(STORE_REPO, REV_C)]
    assert all(a is None for _, a in private.seen)


def test_a_rejected_token_does_not_break_a_public_store(private, ms, monkeypatch) -> None:
    monkeypatch.setenv("PYTACHECK_GITHUB_TOKEN", "ghp_expired")
    private.public = True
    assert store_index("pytacheck")["packs"][0]["name"] == "demo"
    assert pack_install("demo", yes=True).rev == REV_C
    urls = [u for u, _ in private.seen]
    assert INDEX_URL in urls and codeload(STORE_REPO, REV_C) in urls
    assert private.authorized_hosts() == {"api.github.com"}


def test_private_tarball_comes_from_the_api_and_the_record_stays_canonical(private, ms) -> None:
    pack = pack_install("demo", yes=True)
    tar = f"{API}/repos/{STORE_REPO}/tarball/{REV_C}"
    signed = (
        f"https://codeload.github.com/{STORE_REPO}/legacy.tar.gz/{REV_C}?token={CODELOAD_TOKEN}"
    )
    assert (tar, f"Bearer {TOKEN}") in private.seen
    assert (signed, None) in private.seen  # the redirect is followed without the token
    assert codeload(STORE_REPO, REV_C) not in [u for u, _ in private.seen]
    record = json.loads((pack.root / INSTALL_RECORD).read_text())
    canonical = {"github": STORE_REPO, "subdir": "packs/demo"}
    assert (record["source"], record["rev"], record["store"]) == (canonical, REV_C, "pytacheck")
    pin = json.loads(ms.config_file.read_text())["packs"]["demo"]
    assert (pin["source"], pin["rev"]) == (canonical, REV_C)


def test_resolve_rev_asks_the_api_with_the_token(private) -> None:
    assert resolve_rev({"github": STORE_REPO}, "main") == REV_D
    assert private.seen == [(f"{API}/repos/{STORE_REPO}/commits/main", f"Bearer {TOKEN}")]


def test_resolve_rev_on_404_tries_git_then_says_how_to_authenticate(private, monkeypatch) -> None:
    monkeypatch.delenv("PYTACHECK_GITHUB_TOKEN")
    calls: list[tuple[str, str | None]] = []

    def ls_remote(url: str, ref: str | None) -> str:
        calls.append((url, ref))
        raise DownloadError(f"git ls-remote {url} failed: could not read Username")

    monkeypatch.setattr(fetch, "_ls_remote", ls_remote)
    with pytest.raises(PackError, match=r"PYTACHECK_GITHUB_TOKEN.*git credentials"):
        resolve_rev({"github": STORE_REPO}, "main")
    assert calls == [(f"https://github.com/{STORE_REPO}.git", "main")]
    monkeypatch.setattr(fetch, "_ls_remote", lambda url, ref: REV_D)  # git credentials work
    assert resolve_rev({"github": STORE_REPO}, "main") == REV_D


def test_git_fallback_for_the_index_and_the_tarball(private, ms, monkeypatch) -> None:
    """No token: the API, raw and codeload all answer 404; git (the user's credentials) works."""
    monkeypatch.delenv("PYTACHECK_GITHUB_TOKEN")
    fetched: list[tuple[str, str, str]] = []
    read: list[tuple[str, str, str]] = []

    def git_read_file(url: str, ref: str, path: str, *, limit: int) -> bytes:
        read.append((url, ref, path))
        return private.index()

    def git_fetch(url: str, rev: str, dest: str | os.PathLike[str], subdir: str = "") -> int:
        fetched.append((url, rev, subdir))
        out = Path(dest)
        out.mkdir(parents=True, exist_ok=True)
        for rel, data in private.trees[rev].items():
            if rel.startswith(subdir + "/"):
                target = out / rel[len(subdir) + 1 :]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        return 1

    monkeypatch.setattr(fetch, "git_fetch", git_fetch)
    monkeypatch.setattr(fetch, "git_read_file", git_read_file)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/git" if name == "git" else None)
    pack = pack_install("demo", yes=True)
    clone = f"https://github.com/{STORE_REPO}.git"
    assert read == [(clone, "HEAD", "index.json")]  # the index file alone, no checkout
    assert fetched == [(clone, REV_C, "packs/demo")]
    assert pack.rev == REV_C and (pack.root / "hello.py").is_file()
    assert json.loads((pack.root / INSTALL_RECORD).read_text())["source"] == {
        "github": STORE_REPO,
        "subdir": "packs/demo",
    }
    assert all(a is None for _, a in private.seen)


def test_failures_say_how_to_authenticate_without_the_secret(private, ms, monkeypatch) -> None:
    monkeypatch.setenv("PYTACHECK_GITHUB_TOKEN", "ghp_wrongrepo" + TOKEN[-8:])  # no access

    def git_fetch(url: str, rev: str, dest: Any, subdir: str = "") -> int:
        # as git prints it when a credential helper URL carries the token
        raise DownloadError(f"fatal: unable to access 'https://x:{TOKEN}@github.com/{STORE_REPO}/'")

    def git_read_file(url: str, ref: str, path: str, *, limit: int) -> bytes:
        return git_fetch(url, ref, "", path)  # type: ignore[return-value]

    monkeypatch.setattr(fetch, "git_fetch", git_fetch)
    monkeypatch.setattr(fetch, "git_read_file", git_read_file)
    monkeypatch.setattr(fetch, "_ls_remote", lambda url, ref: REV_D)
    monkeypatch.setenv("GH_TOKEN", TOKEN)  # a second token in the environment is redacted too
    with pytest.raises(StoreError) as info:
        store_index("pytacheck")
    text = str(info.value)
    assert "HTTP 404 (GitHub rejected the token in PYTACHECK_GITHUB_TOKEN)" in text
    assert "PYTACHECK_GITHUB_TOKEN" in text and "git credentials" in text
    assert TOKEN not in text and "ghp_wrongrepo" not in text
    private.public = True  # the index is readable, the pack's tarball is not
    private.trees.clear()
    with pytest.raises(PackError) as info:
        pack_install("demo", yes=True)
    text = str(info.value)
    assert "Cannot download github example/store (packs/demo)" in text
    assert "gh auth token" in text and "gh auth setup-git" in text
    assert TOKEN not in text and CODELOAD_TOKEN not in text


def test_credentials_in_urls_are_refused(ms) -> None:
    from metacheck.packs.stores import store_add

    with pytest.raises(PackError, match="contains credentials") as info:
        pack_install(f"https://x-access-token:{TOKEN}@github.com/o/r@v1", yes=True)
    assert TOKEN not in str(info.value)
    with pytest.raises(StoreError, match="contains credentials") as info:
        store_add("mine", f"https://{TOKEN}@github.com/o/store")
    assert TOKEN not in str(info.value)
    assert not ms.config_file.exists()


# --- the git fallback reads the index file alone ------------------------------------------


def _git(*args: str, cwd: Path | None = None) -> str:
    import subprocess

    res = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.org", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout.strip()


@pytest.fixture
def store_repo(tmp_path: Path) -> Path:
    """A store repository with what a pack install would refuse: a symlink, a .so, many files."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    repo = tmp_path / "store-repo"
    repo.mkdir()
    _git("init", "-q", "-b", "main", str(repo))
    (repo / "index.json").write_text('{"schema": 1, "name": "lab", "packs": []}')
    (repo / "packs" / "a").mkdir(parents=True)
    (repo / "packs" / "a" / "README.md").write_text("pack a")
    (repo / "README.md").symlink_to("packs/a/README.md")
    (repo / "native.so").write_bytes(b"\x7fELF")
    (repo / "sub").mkdir()
    (repo / "sub" / "index.json").write_text('{"schema": 1, "packs": [{"name": "x"}]}')
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", "store", cwd=repo)
    _git("tag", "v1", cwd=repo)
    return repo


@pytest.mark.parametrize("filtering", [False, True])
def test_git_index_reads_only_the_index_file(store_repo: Path, filtering: bool) -> None:
    from metacheck.packs.stores import _git_index

    if filtering:  # a server that honours --filter=blob:none (as GitHub does): lazy blobs
        _git("config", "uploadpack.allowFilter", "true", cwd=store_repo)
    url = f"file://{store_repo}"
    assert json.loads(_git_index(url, "HEAD", "index.json"))["name"] == "lab"
    assert json.loads(_git_index(url, "main", "sub/index.json"))["packs"] == [{"name": "x"}]
    assert json.loads(_git_index(url, "v1", "index.json"))["name"] == "lab"
    sha = _git("rev-parse", "HEAD", cwd=store_repo)
    assert json.loads(_git_index(url, sha, "index.json"))["name"] == "lab"


def test_git_index_errors_name_the_problem_not_a_temp_folder(store_repo: Path) -> None:
    url = f"file://{store_repo}"
    sha = _git("rev-parse", "HEAD", cwd=store_repo)
    with pytest.raises(fetch.GitMissing) as info:
        fetch.git_read_file(url, "HEAD", "nope/index.json", limit=1000)
    text = str(info.value)
    assert text == f"the repository {url} has no nope/index.json at HEAD ({sha[:12]})"
    with pytest.raises(fetch.GitMissing, match="has no branch or tag 'gone'"):
        fetch.git_read_file(url, "gone", "index.json", limit=1000)
    with pytest.raises(fetch.GitMissing, match="is not a regular file"):
        fetch.git_read_file(url, "HEAD", "packs", limit=1000)
    with pytest.raises(fetch.GitMissing, match="is not a regular file"):
        fetch.git_read_file(url, "HEAD", "README.md", limit=1000)  # a symlink
    with pytest.raises(PackError, match="too large"):
        fetch.git_read_file(url, "HEAD", "index.json", limit=10)
    with pytest.raises(DownloadError) as info:
        fetch.git_read_file(f"file://{store_repo.parent}/missing", "HEAD", "index.json", limit=10)
    for exc_text in (text, str(info.value)):
        assert "pytacheck-git-" not in exc_text and "Refusing to install" not in exc_text
    for bad in ("--upload-pack=x", "a b", ""):
        with pytest.raises(PackError, match="Invalid git ref"):
            fetch.git_read_file(url, bad, "index.json", limit=10)
    for bad in ("../x.json", "/etc/passwd", "-x"):
        with pytest.raises(PackError, match="Invalid path"):
            fetch.git_read_file(url, "HEAD", bad, limit=10)


def test_a_missing_index_in_a_readable_repo_is_not_an_access_problem(ms, monkeypatch) -> None:
    """A public repo without index.json: git reads it, so the error does not ask for a token."""
    monkeypatch.setenv("PYTACHECK_STORE_URL", STORE_URL)
    asked: list[tuple[str, str, str]] = []

    def git_read_file(url: str, ref: str, path: str, *, limit: int) -> bytes:
        asked.append((url, ref, path))
        raise fetch.GitMissing(f"the repository {url} has no {path} at {ref} (0123456789ab)")

    monkeypatch.setattr(fetch, "git_read_file", git_read_file)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/git" if name == "git" else None)
    with respx.mock() as router:
        router.route().respond(404, text="404: Not Found")
        with pytest.raises(StoreError) as info:
            store_index("pytacheck")
    text = str(info.value)
    assert asked == [(f"https://github.com/{STORE_REPO}.git", "HEAD", "index.json")]
    assert "HTTP 404; git: the repository" in text and "has no index.json at HEAD" in text
    assert "PYTACHECK_GITHUB_TOKEN" not in text and "private" not in text


# --- ~/.netrc logins for store indexes on other hosts -------------------------------------

LAB = "https://lab.example.org/store/index.json"


@pytest.fixture
def netrc_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "netrc"
    path.write_text(
        "machine lab.example.org login alice password s3cret-netrc\n"
        "machine api.github.com login bob password gh-netrc\n"
        "machine 127.0.0.1 login carol password loop-netrc\n"
        "machine plain.example.org login dave password plain-netrc\n"
        "default login eve password default-netrc\n"
    )
    path.chmod(0o600)
    monkeypatch.setenv("NETRC", str(path))
    return path


def _basic(login: str, password: str) -> str:
    import base64

    return "Basic " + base64.b64encode(f"{login}:{password}".encode()).decode()


def test_a_store_behind_a_login_reads_it_from_netrc(ms, netrc_file) -> None:
    from metacheck.packs.stores import store_add

    store_add("lab", "https://lab.example.org/store")
    index = json.dumps({"schema": 1, "name": "lab", "packs": []})
    with respx.mock() as router:
        route = router.get(LAB).mock(
            side_effect=lambda r: httpx.Response(
                200 if r.headers.get("Authorization") == _basic("alice", "s3cret-netrc") else 401,
                text=index,
            )
        )
        assert store_index("lab")["name"] == "lab"
        assert route.call_count == 1
    meta = (ms.data / "stores" / "lab" / "meta.json").read_text()
    assert "s3cret" not in meta and "alice" not in meta
    assert "s3cret" not in ms.config_file.read_text()


def test_netrc_logins_go_only_to_their_own_host(netrc_file) -> None:
    with respx.mock() as router:
        route = router.route().respond(200, text="{}")
        get(LAB, netrc=True)
        get("https://api.github.com/x", netrc=True)  # GitHub: tokens only
        get("http://plain.example.org/x", netrc=True)  # plain http: never
        get("http://127.0.0.1:8765/index.json", netrc=True)  # this machine: yes
        get("https://other.example.org/x", netrc=True)  # the default entry: ignored
        get(LAB)  # netrc=False
        sent = [(str(c.request.url), c.request.headers.get("Authorization")) for c in route.calls]
    assert sent == [
        (LAB, _basic("alice", "s3cret-netrc")),
        ("https://api.github.com/x", None),
        ("http://plain.example.org/x", None),
        ("http://127.0.0.1:8765/index.json", _basic("carol", "loop-netrc")),
        ("https://other.example.org/x", None),
        (LAB, None),
    ]


@pytest.mark.parametrize(
    ("target", "keeps"),
    [
        ("https://evil.example.org/steal", False),
        ("http://lab.example.org/store/index.json", False),  # a downgrade
        ("/store/moved.json", True),
    ],
)
def test_netrc_logins_do_not_follow_redirects_elsewhere(netrc_file, target, keeps) -> None:
    with respx.mock() as router:
        router.get(LAB).respond(302, headers={"Location": target})
        final = router.route(path__regex=r"^/(steal|store/index\.json|store/moved\.json)$")
        final.respond(200, text="{}")
        assert get(LAB, netrc=True).ok
        (call,) = [c for c in final.calls if str(c.request.url) != LAB]
    header = call.request.headers.get("Authorization")
    assert header == (_basic("alice", "s3cret-netrc") if keeps else None)


def test_a_redirect_with_credentials_in_it_is_refused(netrc_file) -> None:
    with respx.mock() as router:
        router.get(LAB).respond(302, headers={"Location": "https://u:pw@lab.example.org/x"})
        with pytest.raises(DownloadError, match="a redirect to a URL with credentials") as info:
            get(LAB, netrc=True)
    assert "pw@" not in str(info.value)


def test_without_a_login_the_error_points_to_netrc(ms, monkeypatch) -> None:
    from metacheck.packs.stores import store_add

    monkeypatch.setenv("NETRC", str(ms.root / "no-such-netrc"))
    store_add("lab", "https://lab.example.org/store")
    with respx.mock() as router:
        router.get(LAB).respond(401)
        with pytest.raises(StoreError) as info:
            store_index("lab")
    text = str(info.value)
    assert "HTTP 401" in text and "~/.netrc" in text and "PYTACHECK_GITHUB_TOKEN" not in text
    with pytest.raises(StoreError) as info:
        store_add("lab2", "https://alice:pw@lab.example.org/store")
    assert "~/.netrc" in str(info.value) and "PYTACHECK_GITHUB_TOKEN" not in str(info.value)
    assert "pw@" not in str(info.value)


def test_a_malformed_netrc_warns_without_quoting_it(tmp_path, monkeypatch) -> None:
    path = tmp_path / "netrc"
    path.write_text("machine lab.example.org login alice password\nleaked-value junk junk\n")
    path.chmod(0o600)
    monkeypatch.setenv("NETRC", str(path))
    with pytest.warns(UserWarning, match="cannot be read") as caught:
        assert auth.netrc_login(LAB) is None
    assert all("leaked-value" not in str(w.message) for w in caught)


# --- nothing is ever written ---------------------------------------------------------------


def _written(root: Path) -> dict[str, bytes]:
    return {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_no_secret_is_written_logged_or_printed(private, ms, capsys, caplog, recwarn) -> None:
    import metacheck as pc
    from metacheck.provenance import RunRecord, run_modules

    caplog.set_level(logging.DEBUG)
    assert main(["store", "update"]) == 0
    assert main(["pack", "search", "demo"]) == 0
    assert main(["pack", "show", "demo", "--json"]) == 0
    assert main(["pack", "install", "demo", "--yes"]) == 0
    assert main(["pack", "list"]) == 0
    private.add("demo", {"hello": mod_src("hello", "newer")}, rev=REV_D)
    assert main(["store", "update"]) == 0
    assert main(["pack", "update", "--yes"]) == 0
    record = ms.work / "run.json"
    chain = run_modules(pc.test_paper("A sentence."), ["demo::hello"], record=record)
    assert chain.last.summary_text == "newer"
    source = {"github": STORE_REPO, "subdir": "packs/demo", "rev": REV_D}
    assert RunRecord.read(record).modules[0]["source"] == source
    out = capsys.readouterr()
    texts = {
        "stdout": out.out,
        "stderr": out.err,
        "logs": caplog.text,
        "warnings": "\n".join(str(w.message) for w in recwarn),
    }
    files = _written(ms.root)
    log_file = Path(os.environ["PYTACHECK_LOG"])
    if log_file.is_file():
        files[str(log_file)] = log_file.read_bytes()
    assert any(p.endswith(INSTALL_RECORD) for p in files) and str(record) in files
    for secret in (TOKEN, CODELOAD_TOKEN):
        assert [k for k, v in texts.items() if secret in v] == []
        assert [p for p, data in files.items() if secret.encode() in data] == []
    # the signed redirect was requested (and logged by httpx), only ever redacted
    assert any(CODELOAD_TOKEN in u for u, _ in private.seen)
    assert "legacy.tar.gz" in caplog.text and "token=***" in caplog.text
    assert private.authorized_hosts() == {"api.github.com"}


# --- the real store ------------------------------------------------------------------------


def _network_selected(request: pytest.FixtureRequest) -> bool:
    expr = str(request.config.getoption("markexpr") or "")
    return "network" in expr and "not network" not in expr


@contextlib.contextmanager
def _private_http_log() -> Iterator[io.StringIO]:
    """DEBUG records of httpx and httpcore, kept out of pytest's report.

    They go to a buffer of the test's own and do not propagate, so a failing
    live test never shows them in its "Captured log" section.
    """
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
    saved = []
    for name in ("httpx", "httpcore"):
        logger = logging.getLogger(name)
        saved.append((logger, logger.level, logger.propagate))
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        logger.addHandler(handler)
    try:
        yield buf
    finally:
        for logger, level, propagate in saved:
            logger.removeHandler(handler)
            logger.setLevel(level)
            logger.propagate = propagate


@pytest.mark.network
def test_live_private_store(live_github_token, ms, monkeypatch, capsys, request) -> None:
    """``pack search`` and ``pack install clinical_trials`` from the real (private) store.

    This test holds a real token, so nothing that might contain it reaches
    pytest's report: the HTTP debug log goes to a private buffer, warnings are
    recorded here, output is read before any assertion, the leak checks come
    first and report only indexes and paths, and the other checks run on text
    that passed them.
    """
    if not live_github_token:
        pytest.skip("no GitHub token (PYTACHECK_GITHUB_TOKEN, GH_TOKEN or GITHUB_TOKEN)")
    if not _network_selected(request):
        pytest.skip("reads the real store: run with -m network")
    import metacheck as pc
    from metacheck.provenance import run_modules

    monkeypatch.setenv("PYTACHECK_GITHUB_TOKEN", live_github_token)
    monkeypatch.delenv("GIT_ALLOW_PROTOCOL", raising=False)
    paper = pc.test_paper(["The trial was registered at ClinicalTrials.gov (NCT01234567)."])
    failure: str | None = None
    chain = None
    with _private_http_log() as http_log, warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        rc_search = main(["pack", "search", "trial"])
        searched = capsys.readouterr()
        rc_install = main(["pack", "install", "clinical_trials", "--yes"])
        try:
            chain = run_modules(
                paper, ["clinical_trials::trial_registration"], record=ms.work / "run.json"
            )
        except Exception as exc:  # checked for leaks below, then reported
            failure = f"{type(exc).__name__}: {exc}"
    printed = capsys.readouterr()
    texts = [
        searched.out,
        searched.err,
        printed.out,
        printed.err,
        http_log.getvalue(),
        "\n".join(str(w.message) for w in caught),
        failure or "",
    ]
    files = _written(ms.root)
    log_file = Path(os.environ["PYTACHECK_LOG"])
    if log_file.is_file():
        texts.append(log_file.read_text(errors="replace"))

    # 1. leaks first; a failure names only indexes into `texts` and file paths
    signed = re.compile(r"[?&]token=(?!\*\*\*)[^&\s'\"]+")
    leaked_files = [p for p, data in files.items() if live_github_token.encode() in data]
    signed_files = [p for p, d in files.items() if signed.search(d.decode("utf-8", "replace"))]
    leaks = [i for i, t in enumerate(texts) if live_github_token in t]
    signed_leaks = [i for i, t in enumerate(texts) if signed.search(t)]
    assert not leaked_files, f"the token is in {leaked_files}"
    assert not signed_files, f"a signed URL is in {signed_files}"
    assert not leaks, f"the token is in text number {leaks}"
    assert not signed_leaks, f"a signed URL is in text number {signed_leaks}"
    assert "legacy.tar.gz" in texts[4] or "tarball" in texts[4]  # the log was captured

    # 2. what should have happened (these texts passed the leak checks)
    assert failure is None, failure
    assert (rc_search, rc_install) == (0, 0), searched.err + printed.err
    assert "clinical_trials" in searched.out
    pin = json.loads(ms.config_file.read_text())["packs"]["clinical_trials"]
    assert pin["source"] == {
        "github": "scienceverse/pytacheck-modules",
        "subdir": "packs/clinical_trials",
    }
    assert re.fullmatch(r"[0-9a-f]{40}", pin["rev"]) and pin["store"] == "pytacheck"
    assert chain is not None
    out = chain.last
    assert out.traffic_light == "green" and out.table["trial_id"].tolist() == ["NCT01234567"]
    assert files and auth._client is not None  # the GitHub requests used the HTTP/1.1 client
