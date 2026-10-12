"""A data package named by a link: which links are understood, where a download may go, what it does."""

from __future__ import annotations

import io
import os
import socket
import tarfile
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from metacheck import http
from metacheck.datapackage import (
    PackageError,
    check_package,
    fetch_archive,
    is_link,
    open_link,
    parse_link,
)
from metacheck.datapackage import _remote as remote

PUBLIC = "140.82.112.3"


def _zip_bytes(members: dict[str, str] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in (
            members
            or {"repo-abc123/README.md": "# Study\n", "repo-abc123/data/a.csv": "a,b\n1,2\n"}
        ).items():
            zf.writestr(name, text)
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def hermetic(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No proxy (so that the address check is the one that runs), a public address for every
    name unless a test says otherwise, no token, and a fresh HTTP client under respx."""
    for name in (
        "HTTPS_PROXY",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
        "HTTP_PROXY",
        "http_proxy",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.delenv("GITHUB_PAT_GITHUB_COM", raising=False)
    monkeypatch.delenv("GITLAB_PAT", raising=False)
    monkeypatch.delenv("OSF_PAT", raising=False)
    monkeypatch.setattr("metacheck.archives.github._TOKEN_CACHE", {"token": None})
    monkeypatch.setattr(socket, "getaddrinfo", lookup({}))
    http.close_client()
    yield
    http.close_client()


def lookup(table: dict[str, str]) -> Any:
    """A name lookup that answers from *table*, and with a public address for any other name."""

    def getaddrinfo(host: str, port: Any, *args: Any, **kwargs: Any) -> list[tuple[Any, ...]]:
        address = table.get(host, PUBLIC)
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (address, 443))]

    return getaddrinfo


# -- reading a link -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kind", "name", "archive"),
    [
        (
            "https://github.com/scienceverse/example-study",
            "github",
            "example-study",
            "https://api.github.com/repos/scienceverse/example-study/zipball",
        ),
        (
            "github.com/scienceverse/example-study.git",
            "github",
            "example-study",
            "https://api.github.com/repos/scienceverse/example-study/zipball",
        ),
        (
            "  <https://www.github.com/o/r/>  ",
            "github",
            "r",
            "https://api.github.com/repos/o/r/zipball",
        ),
        (
            "https://github.com/o/r/tree/v1.2",
            "github",
            "r",
            "https://api.github.com/repos/o/r/zipball/v1.2",
        ),
        (
            "https://github.com/o/r/tree/feature/new-data",
            "github",
            "r",
            "https://api.github.com/repos/o/r/zipball/feature/new-data",
        ),
        (
            "https://github.com/o/r/releases/tag/v3",
            "github",
            "r",
            "https://api.github.com/repos/o/r/zipball/v3",
        ),
        (
            "https://github.com/o/r/commit/0123abc",
            "github",
            "r",
            "https://api.github.com/repos/o/r/zipball/0123abc",
        ),
        (
            "https://github.com/o/r/issues/3",
            "github",
            "r",
            "https://api.github.com/repos/o/r/zipball",
        ),
        (
            "https://github.com/o/r/archive/refs/heads/main.zip",
            "github",
            "main",
            "https://github.com/o/r/archive/refs/heads/main.zip",
        ),
        (
            "https://github.com/o/r/releases/download/v1/data-v1.tar.gz",
            "github",
            "data-v1",
            "https://github.com/o/r/releases/download/v1/data-v1.tar.gz",
        ),
        (
            "https://gitlab.com/group/project",
            "gitlab",
            "project",
            "https://gitlab.com/api/v4/projects/group%2Fproject/repository/archive.zip",
        ),
        (
            "https://gitlab.com/group/sub/project.git",
            "gitlab",
            "project",
            "https://gitlab.com/api/v4/projects/group%2Fsub%2Fproject/repository/archive.zip",
        ),
        (
            "https://gitlab.com/group/project/-/tree/release/1.0",
            "gitlab",
            "project",
            "https://gitlab.com/api/v4/projects/group%2Fproject/repository/archive.zip?sha=release%2F1.0",
        ),
        (
            "https://gitlab.com/group/project/-/issues",
            "gitlab",
            "project",
            "https://gitlab.com/api/v4/projects/group%2Fproject/repository/archive.zip",
        ),
        (
            "https://osf.io/abc12/",
            "osf",
            "osf-abc12",
            "https://files.osf.io/v1/resources/abc12/providers/osfstorage/?zip=",
        ),
        (
            "osf.io/ABC12/files/osfstorage",
            "osf",
            "osf-abc12",
            "https://files.osf.io/v1/resources/abc12/providers/osfstorage/?zip=",
        ),
        (
            "https://zenodo.org/records/1234567",
            "zenodo",
            "zenodo-1234567",
            "https://zenodo.org/api/records/1234567/files-archive",
        ),
        (
            "https://zenodo.org/record/1234567#files",
            "zenodo",
            "zenodo-1234567",
            "https://zenodo.org/api/records/1234567/files-archive",
        ),
        (
            "https://zenodo.org/doi/10.5281/zenodo.1234567",
            "zenodo",
            "zenodo-1234567",
            "https://zenodo.org/api/records/1234567/files-archive",
        ),
        (
            "https://doi.org/10.5281/zenodo.7654321",
            "zenodo",
            "zenodo-7654321",
            "https://zenodo.org/api/records/7654321/files-archive",
        ),
        (
            "10.5281/zenodo.7654321",
            "zenodo",
            "zenodo-7654321",
            "https://zenodo.org/api/records/7654321/files-archive",
        ),
        (
            "doi:10.5281/ZENODO.7654321",
            "zenodo",
            "zenodo-7654321",
            "https://zenodo.org/api/records/7654321/files-archive",
        ),
        (
            "https://zenodo.org/records/123/files/study_data.zip?download=1",
            "zenodo",
            "study_data",
            "https://zenodo.org/records/123/files/study_data.zip?download=1",
        ),
    ],
)
def test_the_links_that_are_understood(
    text: str, kind: str, name: str, archive: str | None
) -> None:
    link = parse_link(text)
    assert (link.kind, link.name, link.archive_url) == (kind, name, archive)
    assert link.url.startswith("https://")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "no link"),
        ("my data", "spaces"),
        ("https://example.com/o/r", "not a repository this can fetch from"),
        ("https://github.com.evil.example/o/r", "not a repository this can fetch from"),
        ("https://evil.example/github.com/o/r", "not a repository this can fetch from"),
        ("https://user:pw@github.com/o/r", "login or a port"),
        ("https://github.com:8443/o/r", "login or a port"),
        ("https://127.0.0.1/o/r", "not a repository this can fetch from"),
        ("ftp://github.com/o/r", "not a web link"),
        ("https://github.com/o", "owner and the repository"),
        ("https://github.com/o/r/blob/main/data.csv", "one file"),
        ("https://gitlab.com/onlyone", "group and the project"),
        ("https://osf.io/preprints/psyarxiv/abcde", "address of a project"),
        ("https://osf.io/search?q=x", "address of a project"),
        ("https://zenodo.org/communities/x", "published record"),
        ("https://zenodo.org/deposit/123", "published record"),
        ("https://doi.org/10.1000/xyz", "Zenodo DOI"),
    ],
)
def test_the_links_that_are_refused_say_why(text: str, message: str) -> None:
    with pytest.raises(PackageError, match=message):
        parse_link(text)


def test_what_is_a_link() -> None:
    assert is_link("https://github.com/o/r") and is_link("  HTTP://x.org")
    assert not is_link("github.com/o/r") and not is_link("/home/me/data") and not is_link(None)
    assert not is_link(Path("https://x"))


# -- where a download may go ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://api.github.com/x",  # not https
        "https://api.github.com:8443/x",  # not the default port
        "https://user@api.github.com/x",
        "https://127.0.0.1/x",  # an address, not a name
        "https://[::1]/x",
        "https://localhost/x",
        "https://db.internal/x",
        "https://printer.local/x",
        "https://169.254.169.254/latest/meta-data",
    ],
)
def test_a_hop_to_somewhere_that_is_not_a_repository_host_is_refused(url: str) -> None:
    with pytest.raises(PackageError, match="not allowed to fetch from"):
        remote._check_hop(url, "github")


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.9",
        "192.168.1.1",
        "169.254.169.254",  # a cloud's metadata service
        "100.64.1.2",  # a tailnet or carrier-grade address
        "0.0.0.0",
        "::1",
        "fe80::1",
        "fc00::5",
        "::ffff:10.0.0.1",  # a private address in IPv6's clothes
        "224.0.0.1",
    ],
)
def test_a_name_that_resolves_to_a_private_address_is_refused(
    address: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lookup({"rebind.example": address}))
    with pytest.raises(PackageError, match="not allowed to fetch from"):
        remote._check_hop("https://rebind.example/x", "github")


def test_a_name_with_one_private_address_among_public_ones_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def mixed(*_args: Any, **_kwargs: Any) -> list[tuple[Any, ...]]:
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC, 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", mixed)
    with pytest.raises(PackageError, match="not allowed"):
        remote._check_hop("https://sneaky.example/x", "zenodo")


def test_a_public_name_passes_and_a_proxy_decides_for_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote._check_hop("https://codeload.github.com/o/r/zip/main", "github")
    monkeypatch.setattr(socket, "getaddrinfo", lookup({"internal.example": "10.0.0.1"}))
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
    remote._check_hop("https://internal.example/x", "github")  # the proxy's rules apply, not ours
    with pytest.raises(PackageError):  # but a name that says it is local is never fetched
        remote._check_hop("https://localhost/x", "github")


# -- the download -------------------------------------------------------------------------------


GITHUB_API = "https://api.github.com/repos/o/repo/zipball"
CODELOAD = "https://codeload.github.com/o/repo/legacy.zip/refs/heads/main"


def _github_ok(router: respx.MockRouter, body: bytes | None = None) -> respx.Route:
    router.get(GITHUB_API).respond(302, headers={"location": CODELOAD})
    return router.get(CODELOAD).respond(
        200, content=body or _zip_bytes(), headers={"content-type": "application/zip"}
    )


def test_a_repository_is_downloaded_named_for_it_and_opened(tmp_path: Path) -> None:
    with respx.mock(assert_all_called=False) as router:
        _github_ok(router)
        with open_link("https://github.com/o/repo") as pkg:
            assert pkg.name == "repo" and pkg.source == "https://github.com/o/repo" and pkg.remote
            assert pkg.base == "repo-abc123"  # GitHub's wrapper folder is stepped into
            assert sorted(pkg.files()["rel"]) == ["README.md", "data/a.csv"]
            download = next(iter(Path(pkg.root).parents))
    assert not download.exists()  # everything is removed again


def test_the_downloaded_file_is_named_for_the_package_by_what_it_is(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tf:
        data = b"hello"
        info = tarfile.TarInfo("README.md")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    with respx.mock(assert_all_called=False) as router:
        _github_ok(router, buffer.getvalue())
        saved = fetch_archive(parse_link("https://github.com/o/repo"), tmp_path / "dl")
    assert saved.name == "repo.tar.gz" and saved.parent == tmp_path / "dl"
    assert not (tmp_path / "dl" / "download.part").exists()


def test_a_token_goes_to_the_hosts_own_api_only_and_not_at_all_on_a_shared_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_PAT_GITHUB_COM", "ghp_secret")
    monkeypatch.setattr("metacheck.archives.github._TOKEN_CACHE", {})
    link = parse_link("https://github.com/o/repo")
    with respx.mock(assert_all_called=False) as router:
        api = router.get(GITHUB_API).respond(302, headers={"location": CODELOAD})
        storage = router.get(CODELOAD).respond(200, content=_zip_bytes())
        fetch_archive(link, tmp_path / "a")
        assert api.calls[0].request.headers["authorization"] == "token ghp_secret"
        assert "authorization" not in storage.calls[0].request.headers  # not beyond the API host
        fetch_archive(link, tmp_path / "b", credentials=False)
        assert "authorization" not in api.calls[1].request.headers


def test_a_redirect_to_a_private_address_is_not_followed(tmp_path: Path) -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get(GITHUB_API).respond(
            302, headers={"location": "https://169.254.169.254/latest/meta-data/"}
        )
        meta = router.get("https://169.254.169.254/latest/meta-data/").respond(200, text="secret")
        with pytest.raises(PackageError, match="not allowed to fetch from"):
            fetch_archive(parse_link("https://github.com/o/repo"), tmp_path)
        assert not meta.called


def test_a_redirect_to_a_name_that_resolves_privately_or_to_plain_http_is_not_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lookup({"storage.rebind.example": "10.9.9.9"}))
    link = parse_link("https://github.com/o/repo")
    for target in ("https://storage.rebind.example/zip", "http://codeload.github.com/o/repo"):
        with respx.mock(assert_all_called=False) as router:
            router.get(GITHUB_API).respond(302, headers={"location": target})
            followed = router.get(target).respond(200, content=_zip_bytes())
            with pytest.raises(PackageError, match="not allowed to fetch from"):
                fetch_archive(link, tmp_path)
            assert not followed.called


def test_a_redirect_loop_ends(tmp_path: Path) -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get(GITHUB_API).respond(302, headers={"location": GITHUB_API})
        with pytest.raises(PackageError, match="in circles"):
            fetch_archive(parse_link("https://github.com/o/repo"), tmp_path)


@pytest.mark.parametrize(
    ("status", "headers", "message"),
    [
        (404, {}, r"GitHub did not find https://github\.com/o/repo"),
        (410, {}, "did not find"),
        (403, {"x-ratelimit-remaining": "0"}, "limit on requests is used up"),
        (403, {}, "private, or needs a login"),
        (401, {}, "private, or needs a login"),
        (500, {}, r"did not answer well \(500\)"),
    ],
)
def test_what_the_host_says_is_said_in_plain_words(
    status: int, headers: dict[str, str], message: str, tmp_path: Path
) -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get(GITHUB_API).respond(status, headers=headers)
        with pytest.raises(PackageError, match=message):
            fetch_archive(parse_link("https://github.com/o/repo"), tmp_path)


def test_a_busy_host_is_asked_again(tmp_path: Path) -> None:
    with respx.mock(assert_all_called=False) as router:
        route = router.get(GITHUB_API).mock(
            side_effect=[
                httpx.Response(503),
                httpx.Response(429, headers={"retry-after": "1"}),
                httpx.Response(200, content=_zip_bytes()),
            ]
        )
        saved = fetch_archive(parse_link("https://github.com/o/repo"), tmp_path)
        assert route.call_count == 3 and saved.suffix == ".zip"


def test_a_host_that_cannot_be_reached_is_a_plain_message(tmp_path: Path) -> None:
    with respx.mock(assert_all_called=False) as router:
        route = router.get(GITHUB_API).mock(side_effect=httpx.ConnectError("no route"))
        with pytest.raises(PackageError, match="Could not reach GitHub"):
            fetch_archive(parse_link("https://github.com/o/repo"), tmp_path)
        assert route.call_count == remote.TRIES
    assert not (tmp_path / "download.part").exists()


def test_a_web_page_or_a_file_that_is_no_archive_is_not_taken_for_one(tmp_path: Path) -> None:
    link = parse_link("https://github.com/o/repo")
    with respx.mock(assert_all_called=False) as router:
        router.get(GITHUB_API).respond(
            200,
            content=b"<html>Sign in</html>",
            headers={"content-type": "text/html; charset=utf-8"},
        )
        with pytest.raises(PackageError, match="a web page, not an archive"):
            fetch_archive(link, tmp_path)
    with respx.mock(assert_all_called=False) as router:
        router.get(GITHUB_API).respond(200, content=b"just some text " * 100)
        with pytest.raises(PackageError, match="did not give an archive"):
            fetch_archive(link, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_a_download_is_stopped_at_its_limit_whatever_the_host_announces(tmp_path: Path) -> None:
    link = parse_link("https://github.com/o/repo")
    big = b"PK\x03\x04" + os.urandom(200_000)  # incompressible, so its size is its own

    def unannounced() -> Iterator[bytes]:
        yield from (big[i : i + 50_000] for i in range(0, len(big), 50_000))

    with respx.mock(assert_all_called=False) as router:
        router.get(GITHUB_API).respond(200, content=unannounced())  # no length is announced
        with pytest.raises(PackageError, match="bigger than the 98 KB"):
            fetch_archive(link, tmp_path, max_bytes=100_000)
    with respx.mock(assert_all_called=False) as router:
        router.get(GITHUB_API).respond(
            200, content=b"PK\x03\x04", headers={"content-length": str(10**12)}
        )  # a length is announced, and it is refused before anything is read
        with pytest.raises(PackageError, match="more than the 98 KB"):
            fetch_archive(link, tmp_path, max_bytes=100_000)
    assert list(tmp_path.iterdir()) == []  # no part of it is left


def test_a_download_that_takes_too_long_is_stopped(tmp_path: Path) -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get(GITHUB_API).respond(200, content=_zip_bytes())
        with pytest.raises(PackageError, match="took too long"):
            fetch_archive(parse_link("https://github.com/o/repo"), tmp_path, timeout=-1.0)
    assert list(tmp_path.iterdir()) == []


def test_each_host_is_asked_at_its_own_address(tmp_path: Path) -> None:
    wanted = {
        "https://gitlab.com/g/p": "https://gitlab.com/api/v4/projects/g%2Fp/repository/archive.zip",
        "https://osf.io/abc12": "https://files.osf.io/v1/resources/abc12/providers/osfstorage/?zip=",
        "https://zenodo.org/records/42": "https://zenodo.org/api/records/42/files-archive",
        "https://github.com/o/r/archive/refs/heads/main.zip": "https://github.com/o/r/archive/refs/heads/main.zip",
    }
    for link, url in wanted.items():
        with respx.mock(assert_all_called=False) as router:
            route = router.get(url).respond(200, content=_zip_bytes())
            saved = fetch_archive(parse_link(link), tmp_path / link.rsplit("/", 1)[-1][:6])
            assert route.called and saved.suffix == ".zip"


# -- through the checks --------------------------------------------------------------------------


def test_check_package_takes_a_link_and_the_report_names_the_link(tmp_path: Path) -> None:
    with respx.mock(assert_all_called=False) as router:
        _github_ok(router)
        chain = check_package("https://github.com/o/repo", modules=["datapackage::package_files"])
    assert chain.run_record.package["source"] == "https://github.com/o/repo"
    assert chain.run_record.package["name"] == "repo" and chain.run_record.package["archive"]


def test_an_offline_run_does_not_download(tmp_path: Path) -> None:
    with pytest.raises(PackageError, match="offline"):
        check_package("https://github.com/o/repo", offline=True)


def test_a_zip_bomb_behind_a_link_is_refused_like_one_that_was_uploaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from metacheck.datapackage import _open

    monkeypatch.setattr(_open, "RATIO_FLOOR", 1024 * 1024)
    bomb = _zip_bytes({"repo-abc/zeros.bin": "\0" * (20 * 1024 * 1024)})
    with respx.mock(assert_all_called=False) as router:
        _github_ok(router, bomb)
        with pytest.raises(PackageError, match="zip bomb"), open_link("https://github.com/o/repo"):
            pass
