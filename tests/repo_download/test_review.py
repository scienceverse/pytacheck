"""Regression tests for the repo_download review fixes (behaviour checked against R).

Each test pins an R behaviour the first port missed; the matching parity cases
are in ``parity/cases/repo_download_review.yaml``.
"""

from __future__ import annotations

import time
import warnings
import zipfile
from collections.abc import Iterator
from pathlib import Path

import httpx
import pandas as pd
import pytest
import respx

from pytacheck.archives import download as dlm
from pytacheck.archives import zip_peek as zpm
from pytacheck.archives.download import (
    _download_zip_to_cache,
    _HttpError,
    download_repo_files,
)
from pytacheck.archives.zip_peek import (
    _expand_tar,
    _expand_zip,
    _is_readable_archive,
    _parse_zip_central_dir,
    _raw_to_char,
    _unzip_all,
    _zip_fetch_members,
    zip_decision,
    zip_peek,
)
from pytacheck.fileinfo import check_file_naming, file_category, filetype
from pytacheck.utils import local_options
from tests.repo_download import _review_helpers as rv

REVIEW = Path(__file__).resolve().parent / "data" / "review"
SRV = "https://srv.example.org"


@pytest.fixture(autouse=True)
def _isolated(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    base = tmp_path_factory.mktemp("rv-cache")
    with local_options(
        {
            "metacheck.repo_cache.notified": True,
            "metacheck.repo_cache.dir": str(base / "persist"),
            "metacheck.repo_cache.session_dir": str(base / "session"),
        }
    ):
        yield


def _routes(*names: str, **kw: object) -> list[dict[str, object]]:
    return [{"url": f"{SRV}/{n}", "fixture": f"review/{n}", **kw} for n in names]


# -- rawToChar() and the central directory -----------------------------------


def test_raw_to_char_drops_trailing_nuls_and_refuses_embedded_ones() -> None:
    assert _raw_to_char(b"data.csv\x00\x00") == "data.csv"
    assert _raw_to_char(b"P\x00") == "P"
    assert _raw_to_char(b"\x00\x00") == ""
    assert _raw_to_char(b"caf\x82.csv") == "caf\udc82.csv"
    with pytest.raises(ValueError, match="embedded nul"):
        _raw_to_char(b"a\x00b.csv")


def test_empty_member_name_is_empty() -> None:
    # U72: metacheck's raw[(p+46):(p+45)] reads two bytes backwards ("P")
    cd = _parse_zip_central_dir((REVIEW / "emptyname.cd").read_bytes())
    assert cd is not None
    assert cd["name"].tolist() == ["", "b.R"]
    cd = _parse_zip_central_dir((REVIEW / "bigoffset.cd").read_bytes())
    assert cd is not None and cd["name"].tolist() == ["", "z.R"]


# -- names that are not valid UTF-8 (CP437 zips) ------------------------------


def test_zip_peek_decodes_cp437_names() -> None:
    # U76: a name without the UTF-8 flag that is not UTF-8 is CP437 (0x82 is
    # "é"); metacheck keeps the bytes, and its grepl("/$") then keeps the folder
    out = rv.serve(_routes("cp437.zip"), zip_peek, f"{SRV}/cp437.zip")
    assert out["name"].tolist() == ["données/résumé.csv", "café.R", "plain.csv", "nul.csv"]


def test_cp437_zips_and_invalid_names_work(tmp_path: Path) -> None:
    # U76: metacheck's string functions fail on names that are not valid UTF-8
    decision = rv.serve(_routes("cp437.zip"), zip_decision, f"{SRV}/cp437.zip")
    assert decision["worth"] is True
    out = rv.serve(_routes("cp437.zip"), _zip_fetch_members, f"{SRV}/cp437.zip", dest=str(tmp_path))
    assert "données/résumé.csv" in out["name"].tolist()
    assert (tmp_path / "données" / "résumé.csv").exists()
    # an undecodable byte in a local name is a naming problem, not an error
    rules = check_file_naming(["caf\udc82.csv"])["rule"].tolist()
    assert rules == ["special-characters", "diacritics"]
    assert len(check_file_naming("ok.csv", file_path="d\udc82/ok.csv")) == 0


def test_invalid_names_are_classified() -> None:
    # U76: metacheck's grepl()/strsplit() classify a name that is not valid
    # UTF-8 as nothing; its extension still says what it is
    x = ["caf\udc82.csv", "ok.R", "READ\udc82ME.txt", "code\udc82book.csv"]
    fc = file_category(x)
    assert fc["filetype"].tolist() == ["data", "code", "text", "data"]
    assert fc["file_category"].tolist()[0] == "data"
    assert fc["file_category"].tolist()[1] == "code"
    assert filetype(x).tolist() == ["data", "code", "text", "data"]
    assert _is_readable_archive(["a\udc82.zip", "b.zip"]) == [True, True]


# -- member fetches -------------------------------------------------------------


def test_fetch_members_survives_a_file_in_the_way(tmp_path: Path) -> None:
    out = rv.serve(_routes("paths.zip"), _zip_fetch_members, f"{SRV}/paths.zip", dest=str(tmp_path))
    ok = {n: bool(v) for n, v in zip(out["name"], out["ok"], strict=True)}
    assert ok["a"] is True and ok["a/b.csv"] is False  # "a" is a file, not a folder
    assert ok["../evil.csv"] is False and ok["/abs.csv"] is True
    assert (tmp_path / "sub" / "win.csv").read_bytes() == b"win\n"
    assert (tmp_path / "C:" / "drive.csv").exists() is False  # "C:/" is stripped
    assert (tmp_path / "drive.csv").exists()


# -- expanding archives as R's unzip / GNU tar do --------------------------------


def _extracted(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def test_unzip_drops_dotdot_components_and_keeps_other_bytes(tmp_path: Path) -> None:
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        _unzip_all(str(REVIEW / "climb.zip"), str(tmp_path))
    assert sum("skipped" in str(x.message) for x in w) == 4
    got = _extracted(tmp_path)
    assert "evil.csv" in got and "z/up.csv" in got and "a/b/c.csv" in got
    assert "sub\\win.csv" in got  # a backslash is part of the name
    assert "C:/drive.csv" in got  # a folder named "C:"
    assert "x/..../y2.csv" in got and "..../z.csv" in got and "k..csv" in got


def test_unzip_stops_at_a_member_it_cannot_write_or_open(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="cannot open file"):
        _unzip_all(str(REVIEW / "dots.zip"), str(tmp_path / "d"))
    assert "after.csv" not in _extracted(tmp_path / "d")
    for name, kept in (("lzma.zip", ["first.csv"]), ("crc.zip", ["good.csv"])):
        with pytest.warns(UserWarning, match="zip file is corrupt"):
            _unzip_all(str(REVIEW / name), str(tmp_path / name))
        assert _extracted(tmp_path / name) == kept
    # bzip2 is readable and a wrong CRC in both headers is not checked
    _unzip_all(str(REVIEW / "bzcrc.zip"), str(tmp_path / "bz"))
    assert _extracted(tmp_path / "bz") == ["badcrc.csv", "bz.csv", "last.csv"]


def test_expand_zip_and_tar_follow_r(tmp_path: Path) -> None:
    row = pd.DataFrame({"repo_url": ["r"], "file_name": ["x"], "file_path": ["x"]})
    for name in ("climb.zip", "hostile.tar.gz"):
        f = tmp_path / name
        f.write_bytes((REVIEW / name).read_bytes())
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            out = (_expand_zip if name.endswith(".zip") else _expand_tar)(str(f), row)
        paths = out["file_path"].tolist()
        if name == "hostile.tar.gz":
            # GNU tar skips "../" members and strips a leading "/"
            assert paths == [
                "hostile.tar.gz/a/b.csv",
                "hostile.tar.gz/abs.csv",
                "hostile.tar.gz/last.csv",
                "hostile.tar.gz/ok/data.csv",
                "hostile.tar.gz/rel_link.csv",
                "hostile.tar.gz/sub\\win.csv",
            ]
        else:
            assert "climb.zip/C:/drive.csv" in paths and "climb.zip/sub\\win.csv" in paths


# -- httr2's error messages -----------------------------------------------------


def test_http_error_messages_use_httr2_wording() -> None:
    def msg(status: int, www: str | None = None) -> str:
        headers = {"WWW-Authenticate": www} if www else {}
        return str(_HttpError(httpx.Response(status, headers=headers)))

    assert msg(413) == "HTTP 413 Payload Too Large."
    assert msg(416) == "HTTP 416 Range Not Satisfiable."
    assert msg(431) == "HTTP 431."
    assert msg(401, 'Basic realm="x"') == "HTTP 401 Unauthorized."
    assert msg(403, "Bearer") == "HTTP 403 Forbidden.\n\u2022 OAuth error\n\u2022 :"
    assert (
        msg(401, 'Bearer error="invalid_token"  ,  error_description="bad token" , realm="api"')
        == "HTTP 401 Unauthorized.\n\u2022 OAuth error: invalid_token - bad token\n\u2022 realm: api"
    )
    long = "The access token is invalid because it has expired or was revoked by the owner long ago"
    lines = msg(401, f'Bearer error="e", error_description="{long}", realm="r"').split("\n")
    assert max(len(line) for line in lines) <= 79 and lines[2].startswith("  ")


# -- the whole-repository zip ---------------------------------------------------


def _files(tmp_path: Path, rel: list[object]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "repo_url": ["https://example.org/z"] * len(rel),
            "file_name": [None] * len(rel),
            "file_path": rel,
            "file_location": [None] * len(rel),
            ".cache_path": [str(tmp_path / f"out{i}") for i in range(len(rel))],
        }
    )


def _zip(members: dict[str, bytes]) -> bytes:
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_zip_to_cache_does_not_match_a_missing_path_to_an_entry_named_na(tmp_path: Path) -> None:
    url = "https://zip.example.org/a.zip"
    with respx.mock(assert_all_called=False) as router:
        router.get(url).mock(return_value=httpx.Response(200, content=_zip({"NA": b"x\n"})))
        out = _download_zip_to_cache(_files(tmp_path, [None, "NA"]), [0, 1], url)
    assert out["file_location"].tolist() == [None, str(tmp_path / "out1")]


def test_zip_to_cache_waits_once_unless_the_argument_says_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # the metacheck.skip_on_api_limit option does not apply here, only the argument
    url = "https://zip.example.org/b.zip"
    slept: list[float] = []
    monkeypatch.setattr(dlm, "_announce_rate_limit_wait", lambda *a, **k: None)
    monkeypatch.setattr("pytacheck.http.sleep", lambda s: slept.append(s))
    limited = httpx.Response(
        429, headers={"ratelimit-remaining": "0", "ratelimit-reset": str(round(time.time()) + 60)}
    )
    with local_options({"metacheck.skip_on_api_limit": True}), respx.mock() as router:
        route = router.get(url).mock(
            side_effect=[limited, httpx.Response(200, content=_zip({"a.csv": b"1\n"}))]
        )
        out = _download_zip_to_cache(_files(tmp_path, ["a.csv"]), [0], url)
    assert route.call_count == 2 and len(slept) == 1
    assert out["file_location"].tolist() == [str(tmp_path / "out0")]


def test_zip_to_cache_matches_cp437_entry_names(tmp_path: Path) -> None:
    # U76: a CP437 member name matches the listing's (UTF-8) name; metacheck
    # fails on the archive
    url = "https://zip.example.org/c.zip"
    with respx.mock(assert_all_called=False) as router:
        router.get(url).mock(
            return_value=httpx.Response(200, content=(REVIEW / "cp437.zip").read_bytes())
        )
        out = _download_zip_to_cache(_files(tmp_path, ["plain.csv", "café.R"]), [0, 1], url)
    loc = out["file_location"].tolist()
    assert loc[0] is not None and loc[1] is not None
    assert Path(loc[1]).exists()


def test_download_repo_files_archive_members_with_collisions() -> None:
    arc = f"{SRV}/paths.zip"
    files = pd.DataFrame(
        {
            "repo_url": ["https://example.org/rp"] * 3,
            "file_name": ["a", "b.csv", "data.csv"],
            "file_path": ["p/a", "p/a/b.csv", "p/ok/data.csv"],
            "file_url": [None] * 3,
            "file_size": [13.0, 817.0, 817.0],
            "archive_url": [arc] * 3,
            "archive_member": ["a", "a/b.csv", "ok/data.csv"],
        }
    )
    out = rv.serve(_routes("paths.zip"), download_repo_files, files)
    loc = out["file_location"].tolist()
    assert loc[0] is not pd.NA and loc[1] is pd.NA and loc[2] is not pd.NA


def test_zip_peek_cache_is_cleared_by_the_helper() -> None:
    rv.serve(_routes("crc.zip"), zip_peek, f"{SRV}/crc.zip")
    assert not zpm._ZIP_PEEK_CACHE


def test_file_listing_follows_folder_links_but_not_cycles(tmp_path: Path) -> None:
    from pytacheck.archives.zip_peek import _list_files_all

    (tmp_path / "real" / "sub").mkdir(parents=True)
    (tmp_path / "real" / "x.csv").write_text("a\n")
    (tmp_path / "real" / "sub" / "y.R").write_text("1\n")
    (tmp_path / "alias").symlink_to(tmp_path / "real")
    (tmp_path / "real" / "sub" / "loop").symlink_to(tmp_path / "real")
    (tmp_path / "dangling.csv").symlink_to(tmp_path / "nowhere.csv")
    assert _list_files_all(str(tmp_path)) == [
        "alias/sub/y.R",
        "alias/x.csv",
        "dangling.csv",
        "real/sub/y.R",
        "real/x.csv",
    ]
