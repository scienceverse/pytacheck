"""Private stores: GitHub tokens, the hosts they go to, the API paths, the git fallback, no leaks.

Everything but the last test is served by respx (a mocked transport); the last
one reads the real (private) default store and runs only with ``-m network``
and a token in the environment.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from pytacheck.cli import main
from pytacheck.packs import auth, fetch
from pytacheck.packs.auth import DownloadError, get, github_token, may_send_token, token_var
from pytacheck.packs.fetch import resolve_rev
from pytacheck.packs.install import pack_install
from pytacheck.packs.manifest import PackError
from pytacheck.packs.stores import StoreError, store_index
from pytacheck.packs.tree import INSTALL_RECORD
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

    def git_fetch(url: str, rev: str, dest: str | os.PathLike[str], subdir: str = "") -> int:
        fetched.append((url, rev, subdir))
        out = Path(dest)
        out.mkdir(parents=True, exist_ok=True)
        if not subdir:
            (out / "index.json").write_bytes(private.index())
            return 1
        for rel, data in private.trees[rev].items():
            if rel.startswith(subdir + "/"):
                target = out / rel[len(subdir) + 1 :]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        return 1

    monkeypatch.setattr(fetch, "git_fetch", git_fetch)
    monkeypatch.setattr(fetch, "_ls_remote", lambda url, ref: REV_D if ref is None else "?")
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/git" if name == "git" else None)
    pack = pack_install("demo", yes=True)
    clone = f"https://github.com/{STORE_REPO}.git"
    assert fetched == [(clone, REV_D, ""), (clone, REV_C, "packs/demo")]
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

    monkeypatch.setattr(fetch, "git_fetch", git_fetch)
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
    from pytacheck.packs.stores import store_add

    with pytest.raises(PackError, match="contains credentials") as info:
        pack_install(f"https://x-access-token:{TOKEN}@github.com/o/r@v1", yes=True)
    assert TOKEN not in str(info.value)
    with pytest.raises(StoreError, match="contains credentials") as info:
        store_add("mine", f"https://{TOKEN}@github.com/o/store")
    assert TOKEN not in str(info.value)
    assert not ms.config_file.exists()


# --- nothing is ever written ---------------------------------------------------------------


def _written(root: Path) -> dict[str, bytes]:
    return {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_no_secret_is_written_logged_or_printed(private, ms, capsys, caplog, recwarn) -> None:
    import pytacheck as pc
    from pytacheck.provenance import RunRecord, run_modules

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


@pytest.mark.network
def test_live_private_store(live_github_token, ms, monkeypatch, capsys, caplog, request) -> None:
    """``pack search`` and ``pack install clinical_trials`` from the real (private) store."""
    if not live_github_token:
        pytest.skip("no GitHub token (PYTACHECK_GITHUB_TOKEN, GH_TOKEN or GITHUB_TOKEN)")
    if not _network_selected(request):
        pytest.skip("reads the real store: run with -m network")
    import pytacheck as pc
    from pytacheck.provenance import run_modules

    monkeypatch.setenv("PYTACHECK_GITHUB_TOKEN", live_github_token)
    monkeypatch.delenv("GIT_ALLOW_PROTOCOL", raising=False)
    caplog.set_level(logging.DEBUG)
    assert main(["pack", "search", "trial"]) == 0
    assert "clinical_trials" in capsys.readouterr().out
    assert main(["pack", "install", "clinical_trials", "--yes"]) == 0
    pin = json.loads(ms.config_file.read_text())["packs"]["clinical_trials"]
    assert pin["source"] == {
        "github": "thesanogoeffect/pytacheck-modules",
        "subdir": "packs/clinical_trials",
    }
    assert re.fullmatch(r"[0-9a-f]{40}", pin["rev"]) and pin["store"] == "pytacheck"
    paper = pc.test_paper(["The trial was registered at ClinicalTrials.gov (NCT01234567)."])
    chain = run_modules(paper, ["clinical_trials::trial_registration"], record=ms.work / "run.json")
    out = chain.last
    assert out.traffic_light == "green" and out.table["trial_id"].tolist() == ["NCT01234567"]
    printed = capsys.readouterr()
    texts = [printed.out, printed.err, caplog.text]
    files = _written(ms.root)
    log_file = Path(os.environ["PYTACHECK_LOG"])
    if log_file.is_file():
        texts.append(log_file.read_text(errors="replace"))
    assert files and not [p for p, data in files.items() if live_github_token.encode() in data]
    assert not [t for t in texts if live_github_token in t]
    signed = re.compile(r"[?&]token=(?!\*\*\*)[^&\s'\"]+")
    assert not [t for t in texts if signed.search(t)]
    assert not [p for p, data in files.items() if signed.search(data.decode("utf-8", "replace"))]
    assert auth._client is not None  # the GitHub requests used the HTTP/1.1 client
