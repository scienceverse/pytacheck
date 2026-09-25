"""Port of metacheck's tests/testthat/test-archive-zenodo-upload.R.

Nothing here reaches Zenodo: every request is answered by respx (recorded
responses in ``mocks/`` or routes defined in the test), and an unexpected
request fails the test.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import pytest
import respx

from pytacheck import utils
from pytacheck.archives.zenodo_upload import (
    ZenodoMetadata,
    _pb_say,
    _zenodo_api,
    _zenodo_build_metadata,
    _zenodo_check_resp,
    _zenodo_check_token,
    _zenodo_flat_names,
    _zenodo_license_id,
    _zenodo_meta_from_folder,
    _zenodo_regex_escape,
    zenodo_pat,
    zenodo_upload,
)

SANDBOX = "https://sandbox.zenodo.org/api"


@pytest.fixture(autouse=True)
def _never_reach_zenodo(monkeypatch: pytest.MonkeyPatch) -> None:
    """A request that escaped the mocks cannot even resolve a Zenodo host."""
    import socket

    real = socket.getaddrinfo

    def guarded(host: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(host, str | bytes) and str(host).rstrip(".").endswith("zenodo.org"):
            raise AssertionError(f"a test tried to reach {host!r}")
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guarded)


def test_interactive_is_false_under_pytest() -> None:
    from pytacheck.archives.zenodo_upload import _interactive

    assert _interactive() is False


def test_zenodo_api() -> None:
    assert _zenodo_api(True) == "https://sandbox.zenodo.org/api"
    assert _zenodo_api(False) == "https://zenodo.org/api"


def test_zenodo_pat() -> None:
    # unset
    assert zenodo_pat(sandbox=True) == ""
    assert zenodo_pat(sandbox=False) == ""

    # the sandbox and the real server keep separate tokens
    zenodo_pat("sandbox-token", sandbox=True)
    assert zenodo_pat(sandbox=True) == "sandbox-token"
    assert zenodo_pat(sandbox=False) == ""

    zenodo_pat("real-token", sandbox=False)
    assert zenodo_pat(sandbox=False) == "real-token"
    assert zenodo_pat(sandbox=True) == "sandbox-token"

    with pytest.raises(ValueError, match="single string"):
        zenodo_pat(123)
    with pytest.raises(ValueError, match="single string"):
        zenodo_pat(["a", "b"])


def test_zenodo_pat_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZENODO_PAT", "env-real")
    monkeypatch.setenv("ZENODO_SANDBOX_PAT", "env-sandbox")
    assert zenodo_pat(sandbox=True) == "env-sandbox"
    assert zenodo_pat(sandbox=False) == "env-real"


def test_zenodo_license_id_maps_every_osf_license() -> None:
    cases = {
        "CC-By Attribution 4.0 International": "cc-by-4.0",
        "CC-BY Attribution-NonCommercial 4.0 International": "cc-by-nc-4.0",
        "CC-BY Attribution-No Derivatives 4.0 International": "cc-by-nd-4.0",
        "CC-BY Attribution-NonCommercial-ShareAlike 4.0 International": "cc-by-nc-sa-4.0",
        "CC0 1.0 Universal": "cc0-1.0",
        "MIT License": "mit",
        'BSD 2-Clause "Simplified" License': "bsd-2-clause",
        'BSD 3-Clause "New"/"Revised" License': "bsd-3-clause",
        "Apache License 2.0": "apache-2.0",
        "Artistic License 2.0": "artistic-2.0",
        "Academic Free License (AFL) 3.0": "afl-3.0",
        "Eclipse Public License 1.0": "epl-1.0",
        "Mozilla Public License 2.0": "mpl-2.0",
        "GNU General Public License (GPL) 2.0": "gpl-2.0-or-later",
        "GNU General Public License (GPL) 3.0": "gpl-3.0-or-later",
        "GNU Lesser General Public License (LGPL) 2.1": "lgpl-2.1-or-later",
        "GNU Lesser General Public License (LGPL) 3.0": "lgpl-3.0-or-later",
        # matching ignores case and punctuation
        "mit   LICENSE": "mit",
    }
    for name, expected in cases.items():
        assert _zenodo_license_id(name) == expected, name

    # "No license" and "Other" name nothing Zenodo accepts
    for value in ("No license", "Other", None, pd.NA, "", [], "Some Made Up License"):
        assert _zenodo_license_id(value) is None


def test_zenodo_regex_escape() -> None:
    from pytacheck._r import sub

    def strip(fp: str, folder: str) -> str:
        return sub("^" + _zenodo_regex_escape(folder) + "[/\\\\]*", "", fp)

    assert strip("/a/b/data.csv", "/a/b") == "data.csv"
    assert strip("/a/b/sub/data.csv", "/a/b") == "sub/data.csv"
    assert strip("/a/v1.0/data.csv", "/a/v1.0") == "data.csv"
    assert strip("/a/proj (old)/d.csv", "/a/proj (old)") == "d.csv"
    assert strip("/a/c++/d.csv", "/a/c++") == "d.csv"
    assert strip("C:/x/y/d.csv", "C:/x/y") == "d.csv"


def test_zenodo_flat_names() -> None:
    assert _zenodo_flat_names(["/p/a.R", "/p/github/README.md", "/p/data/README.md"], "/p") == [
        "a.R",
        "github__README.md",
        "data__README.md",
    ]
    assert _zenodo_flat_names(["/p/a/x/f.txt", "/p/b/x/f.txt"], "/p") == [
        "a__x__f.txt",
        "b__x__f.txt",
    ]
    # a name that still clashes gets its position
    assert _zenodo_flat_names(["/p/a.csv", "/p/x/a.csv", "/p/x__a.csv"], "/p") == [
        "a.csv",
        "x__a.csv",
        "x__a_3.csv",
    ]
    assert _zenodo_flat_names([], "/p") == []


def test_zenodo_build_metadata_from_osf() -> None:
    meta = {
        "osf_id": "6nt4v",
        "title": "My Project",
        "description": "A description",
        "tags": ["open", "data"],
        "license": "CC-By Attribution 4.0 International",
        "creators": [{"name": "DeBruine, Lisa"}],
    }
    md = _zenodo_build_metadata(meta, "/tmp/6nt4v")
    assert isinstance(md, ZenodoMetadata)
    assert md["title"] == "My Project"
    assert md["description"] == "A description"
    assert md["upload_type"] == "dataset"
    assert md["license"] == "cc-by-4.0"
    assert md["keywords"] == ["open", "data"]
    assert md["related_identifiers"][0]["identifier"] == "https://osf.io/6nt4v/"
    assert md["related_identifiers"][0]["relation"] == "isIdenticalTo"
    assert md.license_was_default is False


def test_zenodo_build_metadata_no_license() -> None:
    meta = {"osf_id": "6nt4v", "title": "T", "description": "D", "license": None,
            "creators": [{"name": "A, B"}]}  # fmt: skip
    md = _zenodo_build_metadata(meta, "/tmp/6nt4v", license="cc0-1.0")
    assert md["license"] == "cc0-1.0"
    assert md.license_was_default is True


def test_zenodo_build_metadata_without_osf_metadata() -> None:
    md = _zenodo_build_metadata(None, "/tmp/myfolder")
    assert md["title"] == "myfolder"
    assert md["creators"] == [{"name": "Unknown"}]
    assert md["description"]
    assert "related_identifiers" not in md
    assert "keywords" not in md
    assert md.license_was_default is True


def test_zenodo_build_metadata_fills_blanks() -> None:
    md = _zenodo_build_metadata({"osf_id": "abcde", "title": "", "description": ""}, "/tmp/f")
    assert md["title"] == "f"
    assert md["description"] == "Files archived from the OSF project https://osf.io/abcde/"


def test_zenodo_meta_from_folder(upload_dir: Path) -> None:
    meta = _zenodo_meta_from_folder(str(upload_dir / "proj_osf"))
    assert meta["osf_id"] == "gzosf"
    assert meta["license"] == "CC0 1.0 Universal"
    assert meta["tags"] == ["open", "data"]
    assert meta["creators"] == [
        {"name": "Doe, Jane", "orcid": "0000-0001-2345-6789"},
        {"name": "Solo"},
    ]
    assert _zenodo_meta_from_folder(str(upload_dir / "proj_plain")) is None


def test_zenodo_check_resp() -> None:
    ok = httpx.Response(201, json={"id": 5})
    assert _zenodo_check_resp(ok, "Creating") == {"id": 5}
    bad = httpx.Response(
        400,
        json={"message": "Validation error.", "errors": [{"field": "title", "message": "x"}, {}]},
    )
    with pytest.raises(RuntimeError, match=r"Creating failed \(HTTP 400\): Validation error\. "
                       r"\(title: x; \?: invalid\)"):  # fmt: skip
        _zenodo_check_resp(bad, "Creating")
    with pytest.raises(RuntimeError, match="rejected the token"):
        _zenodo_check_resp(httpx.Response(403), "Uploading")
    with pytest.raises(RuntimeError, match=r"\(HTTP 500\): Internal Server Error$"):
        _zenodo_check_resp(httpx.Response(500, content=b"oops"), "Uploading")


def test_zenodo_check_token(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.get(f"{SANDBOX}/deposit/depositions", params={"size": "1"}).mock(
        return_value=httpx.Response(200, json=[])
    )
    assert _zenodo_check_token(SANDBOX, "tok") is True
    assert route.calls.last.request.headers["Authorization"] == "Bearer tok"

    respx_mock.get("https://zenodo.org/api/deposit/depositions").mock(
        return_value=httpx.Response(500)
    )
    with pytest.raises(RuntimeError, match=r"a sandbox\.zenodo\.org token will not work here"):
        _zenodo_check_token("https://zenodo.org/api", "tok", sandbox=False)


def test_pb_say_pads() -> None:
    said: list[str] = []

    class Bar:
        def tick(self, n: int, tokens: dict[str, str]) -> None:
            said.append(tokens["what"])

    _pb_say(Bar(), "short")
    _pb_say(Bar(), "x" * 200)
    assert said[0] == "short".ljust(68)
    # a truncated line fits the width, "..." included (U50: metacheck's is 2 over)
    assert len(said[1]) == 68 and said[1].endswith("...")


def test_zenodo_upload_validates_input() -> None:
    with pytest.raises(ValueError, match="download_path"):
        zenodo_upload(pd.DataFrame({"x": [1]}), ask=False)


def test_zenodo_upload_needs_a_token(tmp_path: Path) -> None:
    (tmp_path / "data.csv").write_text("a,b\n")
    with pytest.raises(ValueError, match="ZENODO_SANDBOX_PAT"):
        zenodo_upload(str(tmp_path), ask=False)
    with pytest.raises(ValueError, match="ZENODO_PAT"):
        zenodo_upload(str(tmp_path), sandbox=False, ask=False)


def test_zenodo_upload_skips_missing_folders() -> None:
    with utils.local_options({"metacheck.zenodo.pat.sandbox": "fake-token"}):
        with pytest.warns(UserWarning, match="could not be found"):
            res = zenodo_upload("/no/such/folder", ask=False)
    assert res is None


def test_zenodo_upload_nothing_to_upload(tmp_path: Path) -> None:
    with utils.local_options({"metacheck.zenodo.pat.sandbox": "fake-token"}):
        assert zenodo_upload(str(tmp_path), ask=False) is None


def test_zenodo_upload_rejects_a_bad_token_before_uploading(respx_mock: respx.MockRouter,
                                                            tmp_path: Path) -> None:  # fmt: skip
    (tmp_path / "data.csv").write_text("a,b\n")
    respx_mock.get(f"{SANDBOX}/deposit/depositions").mock(return_value=httpx.Response(500))
    create = respx_mock.post(f"{SANDBOX}/deposit/depositions").mock(
        return_value=httpx.Response(201, json={})
    )
    with utils.local_options({"metacheck.zenodo.pat.sandbox": "definitely-not-a-real-token"}):
        with pytest.raises(RuntimeError, match="did not accept the token"):
            zenodo_upload(str(tmp_path), ask=False)
    assert not create.called


def test_zenodo_upload_recorded(mocks: object, upload_dir: Path) -> None:
    folders = [str(upload_dir / "proj_osf"), str(upload_dir / "proj_plain")]
    with pytest.warns(UserWarning, match="without specified license information"):
        out = zenodo_upload(folders, zenodo_pat="fake-token", as_zip=False, ask=False)
    assert out["folder"].tolist() == folders
    assert out["deposition_id"].tolist() == ["123", "123"]
    assert out["files_uploaded"].tolist() == [4, 3]  # the hidden file is not uploaded
    assert out["published"].tolist() == [False, False]
    assert out["error"].isna().all()
    # the token was stored for the sandbox only
    assert zenodo_pat(sandbox=True) == "fake-token"
    assert zenodo_pat(sandbox=False) == ""


def test_zenodo_upload_create_fails() -> None:
    from tests.archives_gz.conftest import MOCKS_FAIL, UPLOAD
    from tests.httpmock import replay

    with replay(MOCKS_FAIL), pytest.warns(UserWarning) as record:
        out = zenodo_upload(
            str(UPLOAD / "proj_plain"), zenodo_pat="fake-token", as_zip=False, ask=False
        )
    messages = [str(w.message) for w in record]
    assert (
        "Creating a deposition for proj_plain failed (HTTP 400): Validation error. "
        "(metadata.title: Missing data for required field.; ?: Something else.)"
    ) in messages
    assert out["deposition_id"].isna().all()
    assert out["files_uploaded"].tolist() == [0]
    assert out["error"].tolist() == [messages[0]]


class _Zenodo:
    """A fake Zenodo deposit API (bucket uploads) recording what was sent."""

    def __init__(self, router: respx.MockRouter, bucket: bool = True) -> None:
        self.files: dict[str, bytes] = {}
        self.metadata: list[dict[str, Any]] = []
        self.published = 0
        dep: dict[str, Any] = {
            "id": 9,
            "links": {"html": "https://sandbox.zenodo.org/deposit/9"},
            "metadata": {"prereserve_doi": {"doi": "10.5072/zenodo.9"}},
        }
        if bucket:
            dep["links"]["bucket"] = "https://sandbox.zenodo.org/api/files/bkt"
        router.get(f"{SANDBOX}/deposit/depositions").mock(return_value=httpx.Response(200, json=[]))
        router.post(f"{SANDBOX}/deposit/depositions").mock(side_effect=self._create(dep))
        router.put(url__startswith="https://sandbox.zenodo.org/api/files/bkt/").mock(
            side_effect=self._put
        )
        router.put(f"{SANDBOX}/deposit/depositions/9").mock(side_effect=self._meta)
        router.post(f"{SANDBOX}/deposit/depositions/9/actions/publish").mock(
            side_effect=self._publish
        )

    @staticmethod
    def _create(dep: dict[str, Any]) -> Any:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.content == b"{}"  # an empty object, never "[]"
            assert request.headers["Authorization"] == "Bearer fake-token"
            return httpx.Response(201, json=dep)

        return handler

    def _put(self, request: httpx.Request) -> httpx.Response:
        name = str(request.url).rsplit("/", 1)[1]
        self.files[name] = request.read()
        return httpx.Response(201, json={"key": name})

    def _meta(self, request: httpx.Request) -> httpx.Response:
        self.metadata.append(json.loads(request.content)["metadata"])
        return httpx.Response(200, json={})

    def _publish(self, request: httpx.Request) -> httpx.Response:
        self.published += 1
        return httpx.Response(202, json={"doi": "10.5072/zenodo.9.final"})


def _classify_by_extension(files: list[str]) -> list[str]:
    kinds = {"csv": "data", "R": "code", "png": "materials", "pdf": "output"}
    return [kinds.get(f.rsplit(".", 1)[-1], "unknown") for f in files]


def test_zenodo_upload_as_zip_split(
    respx_mock: respx.MockRouter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # the categories come from data_classify_files(), tested with the data checks
    monkeypatch.setattr("pytacheck.archives.zenodo_upload._zenodo_classify", _classify_by_extension)
    proj = tmp_path / "My Study"
    (proj / "data").mkdir(parents=True)
    (proj / "stimuli").mkdir()
    (proj / "data" / "raw.csv").write_text("a,b\n1,2\n")
    (proj / "analysis.R").write_text("x <- 1\n")
    (proj / "stimuli" / "face.png").write_bytes(b"\x89PNG....")
    fake = _Zenodo(respx_mock)

    out = zenodo_upload(str(proj), zenodo_pat="fake-token", ask=False, publish=True)

    assert out["files_uploaded"].tolist() == [2]
    assert out["published"].tolist() == [True]
    assert out["doi"].tolist() == ["10.5072/zenodo.9.final"]
    assert sorted(fake.files) == ["My_Study.zip", "My_Study_materials.zip"]
    with zipfile.ZipFile(__import__("io").BytesIO(fake.files["My_Study.zip"])) as zf:
        assert sorted(zf.namelist()) == ["My Study/analysis.R", "My Study/data/raw.csv"]
    with zipfile.ZipFile(__import__("io").BytesIO(fake.files["My_Study_materials.zip"])) as zf:
        assert zf.namelist() == ["My Study/stimuli/face.png"]
    assert fake.metadata[0]["title"] == "My Study"
    assert fake.published == 1


def test_zenodo_upload_as_zip_single(respx_mock: respx.MockRouter, tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "a.csv").write_text("a\n1\n")
    (proj / "b.txt").write_text("b\n")
    fake = _Zenodo(respx_mock)

    out = zenodo_upload(
        str(proj), zenodo_pat="fake-token", ask=False, split_materials=None,
        metadata={"title": "Custom", "version": "2"},
    )  # fmt: skip
    assert out["files_uploaded"].tolist() == [1]
    assert list(fake.files) == ["proj.zip"]
    assert fake.metadata == [
        {
            "title": "Custom",
            "upload_type": "dataset",
            "description": "Files archived from proj",
            "creators": [{"name": "Unknown"}],
            "license": "cc-by-4.0",
            "version": "2",
        }
    ]
    assert fake.published == 0


def test_zenodo_upload_individual_files_to_bucket(
    respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    proj = tmp_path / "p"
    (proj / "x").mkdir(parents=True)
    (proj / "README.md").write_text("top\n")
    (proj / "x" / "README.md").write_text("inner\n")
    (proj / "big.bin").write_bytes(b"0" * 2048)
    (proj / "_osf_metadata").mkdir()
    (proj / "_osf_metadata" / "log.txt").write_text("log\n")
    fake = _Zenodo(respx_mock)

    out = zenodo_upload(
        str(proj), zenodo_pat="fake-token", ask=False, as_zip=False,
        upload_osf_metadata=False, max_file_size=0.001,
    )  # fmt: skip
    assert out["files_skipped"].tolist() == [1]
    assert out["files_uploaded"].tolist() == [2]
    assert fake.files == {"README.md": b"top\n", "x__README.md": b"inner\n"}


def test_zenodo_upload_failed_file_warns(respx_mock: respx.MockRouter, tmp_path: Path) -> None:
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "a.txt").write_text("a\n")
    _Zenodo(respx_mock)
    respx_mock.routes.clear()
    respx_mock.get(f"{SANDBOX}/deposit/depositions").mock(return_value=httpx.Response(200, json=[]))
    respx_mock.post(f"{SANDBOX}/deposit/depositions").mock(
        return_value=httpx.Response(
            201, json={"id": 9, "links": {"bucket": "https://sandbox.zenodo.org/api/files/bkt"}}
        )
    )
    respx_mock.put(url__startswith="https://sandbox.zenodo.org/api/files/bkt/").mock(
        return_value=httpx.Response(400, json={"message": "Bad file"})
    )
    respx_mock.put(f"{SANDBOX}/deposit/depositions/9").mock(return_value=httpx.Response(200))
    with pytest.warns(
        UserWarning, match=r"1 of 1 file in p failed to upload \(e\.g\. Uploading a\.txt"
    ):
        out = zenodo_upload(str(proj), zenodo_pat="fake-token", ask=False, as_zip=False)
    assert out["files_uploaded"].tolist() == [0]
    assert out["url"].isna().all()


def test_zenodo_upload_as_zip_split_several_categories(
    respx_mock: respx.MockRouter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("pytacheck.archives.zenodo_upload._zenodo_classify", _classify_by_extension)
    proj = tmp_path / "s"
    proj.mkdir()
    for name in ("a.csv", "b.png", "c.pdf", "d.png"):
        (proj / name).write_text(name)
    fake = _Zenodo(respx_mock)
    out = zenodo_upload(
        str(proj), zenodo_pat="fake-token", ask=False,
        split_materials=["output", "materials", "code"],
    )  # fmt: skip
    # one archive per split category that has files, in the order asked for
    assert list(fake.files) == ["s.zip", "s_output.zip", "s_materials.zip"]
    assert out["files_uploaded"].tolist() == [3]


def test_zenodo_zip_members(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import sys
    import types

    from pytacheck.archives import zenodo

    fetched: list[list[str]] = []
    fake = types.ModuleType("pytacheck.archives.zip_peek")
    fake.zip_peek = lambda url: pd.DataFrame(  # type: ignore[attr-defined]
        {"name": ["d/data.csv", "img/a.png", "big.csv", "zip64.csv"],
         "size": [100.0, 50.0, 20 * 1024 * 1024.0, float("nan")]}
    )  # fmt: skip

    def fetch(url: str, names: list[str], dest: str) -> pd.DataFrame:
        fetched.append(names)
        return pd.DataFrame({"name": names, "path": names, "size": [1.0] * len(names),
                             "ok": [True] * len(names)})  # fmt: skip

    fake._zip_fetch_members = fetch  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pytacheck.archives.zip_peek", fake)
    monkeypatch.setattr(
        "pytacheck.datacheck.files.data_classify_files",
        lambda names, file_path=None: [
            "data" if n.endswith(".csv") else "materials" for n in names
        ],
    )
    out = zenodo._zenodo_zip_members("https://x/z.zip", str(tmp_path), keep_types="data")
    assert fetched == [["d/data.csv"]]  # too big and unknown-size members are skipped
    assert out["ok"].tolist() == [True]

    none = zenodo._zenodo_zip_members("https://x/z.zip", str(tmp_path), keep_types="code")
    assert len(none) == 0 and list(none.columns) == ["name", "size"]

    fake.zip_peek = lambda url: None  # type: ignore[attr-defined]
    assert zenodo._zenodo_zip_members("https://x/z.zip", str(tmp_path)) is None
