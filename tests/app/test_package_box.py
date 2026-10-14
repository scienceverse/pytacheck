"""The paper page's one box: a paper, the archive of a data package, a repository link or a folder.

What the box was given is told apart (``classify_entry``), a data package is checked under limits
(tighter on a shared server), a link is fetched only from the hosts the library knows and, on a
shared server, with no token of the server, and the page answers in plain words when it cannot.
"""

from __future__ import annotations

import dataclasses
import io
import json
import socket
import tempfile
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import respx
from starlette.testclient import TestClient

import metacheck as pc
from metacheck import http
from metacheck.app import hosted as hosting
from metacheck.app import package as pk
from metacheck.app import ui
from metacheck.app.run import UserError
from metacheck.app.security import cookie_name
from metacheck.app.server import create_app, create_hosted_app
from metacheck.config import update_config
from metacheck.packs.registry import refresh

PORT = 4325
TOKEN = "package-box-token"
HOST = "metacheck.example.org"
TOKEN_A = "a" * 32
DEFAULT = "datapackage::default"
GITHUB_API = "https://api.github.com/repos/o/repo/zipball"
CODELOAD = "https://codeload.github.com/o/repo/legacy.zip/refs/heads/main"
FILES = {
    "repo-abc123/README.md": "# Study\n\nData and code.\n",
    "repo-abc123/data/survey.csv": "participant_id,age,gender\n1,23,f\n2,31,m\n3,27,f\n4,45,m\n",
    "repo-abc123/code/analysis.R": "d <- read.csv('data/survey.csv')\n",
}


@pytest.fixture(autouse=True)
def hermetic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No user config or packs, no concept model (a download), the home folder is ``tmp_path``,
    every name resolves to a public address (so the address check lets the mocked hosts
    through), and there is no proxy and no token of the machine."""
    monkeypatch.setenv("PYTACHECK_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("PYTACHECK_DATA_DIR", str(tmp_path / "data"))
    for name in ("PYTACHECK_PRESET", "METACHECK_PRESET", "METACHECK_APP_ROOTS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
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
    monkeypatch.setenv("PYTACHECK_NO_SLEEP", "1")

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("the local concept model must not be loaded in a test")

    monkeypatch.setattr("metacheck.datacheck.concepts.load_classifier", refuse)
    monkeypatch.setattr("metacheck.datacheck.concepts.classifier_available", lambda: False)
    monkeypatch.setattr("metacheck.archives.github._TOKEN_CACHE", {"token": None})
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_a, **_k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("140.82.112.3", 443))],
    )
    http.close_client()
    refresh()
    yield
    http.close_client()
    refresh()


def zip_bytes(files: dict[str, str] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in (FILES if files is None else files).items():
            zf.writestr(name, text)
    return buffer.getvalue()


def github(router: respx.MockRouter, body: bytes | None = None) -> respx.Route:
    """GitHub as it answers for ``o/repo``: the API redirects to the host of the zip."""
    router.get(GITHUB_API).respond(302, headers={"location": CODELOAD})
    return router.get(CODELOAD).respond(
        200,
        content=zip_bytes() if body is None else body,
        headers={"content-type": "application/zip"},
    )


def leftovers(folder: Path) -> list[str]:
    """What a run left in the temporary folder, apart from the app's own folder for reports
    (made when the app is built, and removed when it stops)."""
    return [p.name for p in folder.iterdir() if not p.name.startswith("metacheck-app-reports-")]


def default_checks() -> int:
    from metacheck.presets import expand

    return len(expand(DEFAULT))


# -- what the box was given -----------------------------------------------------------------


@pytest.mark.parametrize("name", ["paper.pdf", "paper.XML", "paper.json", "to err.PDF"])
def test_a_paper_file_is_a_paper(name: str) -> None:
    entry = pk.classify_entry({"text": "", "files": [f"/up/{name}"]})
    assert entry == pk.Entry(pk.PAPER, f"/up/{name}")


@pytest.mark.parametrize(
    "name",
    ["p.zip", "p.ZIP", "p.tar", "p.tar.gz", "p.tgz", "p.tar.bz2", "p.tbz2", "p.tar.xz", "p.txz"],
)
def test_an_archive_is_an_archive(name: str) -> None:
    assert pk.classify_entry({"text": "", "files": [f"/up/{name}"]}) == pk.Entry(
        pk.ARCHIVE, f"/up/{name}"
    )


def test_a_file_of_the_box_may_come_as_the_path_or_as_the_files_record() -> None:
    entry = pk.classify_entry({"text": "", "files": [{"path": "/up/p.zip", "meta": {}}]})
    assert entry == pk.Entry(pk.ARCHIVE, "/up/p.zip")


@pytest.mark.parametrize(
    "name", ["notes.txt", "sheet.xlsx", "slides.docx", "image.png", "noending"]
)
def test_any_other_file_is_refused(name: str) -> None:
    # a spreadsheet is a zip inside, but it is not an archive of a data package
    with pytest.raises(UserError, match="not supported"):
        pk.classify_entry({"text": "", "files": [f"/up/{name}"]})


def test_a_file_and_a_text_together_or_two_files_are_refused() -> None:
    with pytest.raises(UserError, match="not both"):
        pk.classify_entry({"text": "https://github.com/o/r", "files": ["/up/p.zip"]})
    with pytest.raises(UserError, match="one file at a time"):
        pk.classify_entry({"text": "", "files": ["/up/a.pdf", "/up/b.pdf"]})


@pytest.mark.parametrize(
    "text",
    [
        "https://github.com/o/repo",
        "  https://github.com/o/repo  ",
        '"https://github.com/o/repo"',
        "github.com/o/repo",
        "www.github.com/o/repo/tree/v1",
        "https://gitlab.com/g/p",
        "https://osf.io/abc12",
        "osf.io/abc12",
        "https://zenodo.org/records/1234567",
        "10.5281/zenodo.1234567",
        "doi:10.5281/zenodo.1234567",
        "https://doi.org/10.5281/zenodo.1234567",
    ],
)
@pytest.mark.parametrize("hosted", [False, True])
def test_a_repository_link_is_a_link(text: str, hosted: bool) -> None:
    entry = pk.classify_entry({"text": text, "files": []}, hosted=hosted)
    assert entry.kind == pk.LINK
    assert entry.value == text.strip().strip('"')


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("https://example.com/o/r", "not a repository this can fetch from"),
        ("https://github.com.evil.example/o/r", "not a repository this can fetch from"),
        ("http://127.0.0.1/x", "not a repository this can fetch from"),
        ("http://127.0.0.1:8080/x", "login or a port"),
        ("https://169.254.169.254/latest/meta-data", "not a repository this can fetch from"),
        ("ftp://github.com/o/r", "not a web link"),
        ("file:///etc/passwd", "not a web link"),
        ("https://github.com/o", "owner and the repository"),
        ("https://github.com/o/r/blob/main/data.csv", "one file"),
        ("https://user:pw@github.com/o/r", "login or a port"),
        ("https://zenodo.org/communities/x", "published record"),
    ],
)
@pytest.mark.parametrize("hosted", [False, True])
def test_a_link_that_is_not_understood_is_never_taken_for_a_folder(
    text: str, message: str, hosted: bool
) -> None:
    with pytest.raises(UserError, match=message):
        pk.classify_entry({"text": text, "files": []}, hosted=hosted)


def test_a_text_that_is_no_link_is_a_folder_here_and_nothing_on_a_shared_server() -> None:
    assert pk.classify_entry("~/data/study") == pk.Entry(pk.FOLDER, "~/data/study")
    assert pk.classify_entry({"text": "'/home/me/data'"}) == pk.Entry(pk.FOLDER, "/home/me/data")
    assert pk.classify_entry({"text": r"C:\Users\me\data", "files": []}).kind == pk.FOLDER
    with pytest.raises(UserError, match="not a link this server can check"):
        pk.classify_entry({"text": "/home/me/data", "files": []}, hosted=True)
    with pytest.raises(UserError, match="not a link this server can check"):
        pk.classify_entry({"text": r"C:\Users\me\data"}, hosted=True)


def test_an_empty_box_says_what_it_takes() -> None:
    for empty in (None, "", "   ", {}, {"text": "", "files": []}, {"text": None, "files": None}):
        with pytest.raises(UserError, match="Upload a paper or an archive") as info:
            pk.classify_entry(empty)
        assert "type the path" in str(info.value)  # a folder is for this computer
        with pytest.raises(UserError, match="Upload a paper or an archive") as info:
            pk.classify_entry(empty, hosted=True)
        assert "type the path" not in str(info.value)


def test_a_text_too_long_to_be_a_link_is_refused_unread() -> None:
    with pytest.raises(UserError, match="too long"):
        pk.classify_entry({"text": "https://github.com/o/" + "r" * 5000, "files": []})


def test_the_parts_of_the_box() -> None:
    assert pk.box_parts(None) == ("", [])
    assert pk.box_parts("abc") == ("abc", [])
    assert pk.box_parts({"text": "t", "files": ["/a", {"path": "/b"}, {}, None]}) == (
        "t",
        ["/a", "/b"],
    )


# -- the presets ----------------------------------------------------------------------------


THESIS_CHECK = """\
from metacheck.module import module


@module(title="Thesis check", description="A check of ours", keywords=["results"])
def thesis_check(paper, x=1):
    return {"summary_text": "ok", "table": None, "x": x}
"""


def make_pack(tmp_path: Path) -> None:
    pack = tmp_path / "tue"
    pack.mkdir()
    (pack / "thesis_check.py").write_text(THESIS_CHECK)
    (pack / "pack.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "name": "tue",
                "version": "1.0.0",
                "presets": {
                    # the same built-in checks under another name: fine for a shared server
                    "data_steward": {
                        "description": "TU/e data steward checks",
                        "extends": DEFAULT,
                    },
                    # a check of the pack itself: its code runs on a person's computer only
                    "own_check": {
                        "description": "With a check of ours",
                        "modules": ["datapackage::package_files", "thesis_check"],
                    },
                },
            }
        )
    )

    def edit(cfg: dict[str, Any]) -> None:
        cfg["packs"] = {"tue": {"path": str(pack)}}

    update_config("user", edit)
    refresh()


def test_a_shared_server_offers_the_presets_made_of_its_own_checks(tmp_path: Path) -> None:
    make_pack(tmp_path)
    here = [ref for _, ref in pk.package_presets()]
    assert here[0] == DEFAULT and "tue::data_steward" in here
    shared = [ref for _, ref in pk.package_presets(hosted=True)]
    assert shared[0] == DEFAULT and "tue::data_steward" in shared
    assert "tue::own_check" not in shared  # nothing of a pack is run on a shared server


def test_the_value_from_the_browser_is_checked_against_the_presets(tmp_path: Path) -> None:
    make_pack(tmp_path)
    assert pk.valid_preset("tue::data_steward") == "tue::data_steward"
    assert pk.valid_preset("tue::own_check") == "tue::own_check"
    assert pk.valid_preset("tue::own_check", hosted=True) == DEFAULT
    for wrong in (None, "", "nonsense", "metacheck::stat_check", "../../etc/passwd"):
        assert pk.valid_preset(wrong) == DEFAULT


# -- checking a repository --------------------------------------------------------------------


def run_link(
    tmp_path: Path, link: str = "https://github.com/o/repo", **kw: Any
) -> pk.PackageAnalysis:
    return pk.check_package_source(link, DEFAULT, report_dir=tmp_path / "out", **kw)


def test_a_repository_is_downloaded_checked_and_named_for_the_repository(tmp_path: Path) -> None:
    ticks: list[tuple[float, str]] = []
    with respx.mock(assert_all_called=False) as router:
        github(router)
        result = run_link(tmp_path, progress=lambda fraction, text: ticks.append((fraction, text)))
    assert result.name == "repo" and len(result.rows) == default_checks()
    assert result.report_path.name == "repo_report.html" and result.report_path.is_file()
    assert ticks[0] == (0.0, "Fetching the repository") and ticks[-1] == (1.0, "Done")
    stages = [text.split(":")[0].split(" (")[0] for _, text in ticks]
    first_check = next(i for i, text in enumerate(stages) if text.startswith("Running check 1 of"))
    assert "Downloading" in stages[:first_check] and "Unpacking the archive" in stages
    # each stage has its own bar: the checks finished of the checks to run, only moving forward
    counts = [f for f, text in ticks[first_check:] if text.startswith("Running check")]
    assert counts == sorted(counts) and counts[0] == 0.0 and counts[-1] < 1.0
    assert ticks[-2] == (1.0, "Building the report")
    assert {row.status for row in result.rows} == {"Data package"}
    assert all(
        row.result.split(":")[0] in {"Red", "Yellow", "Green", "Info", "Failed"}
        for row in result.rows
    )
    assert not list(tmp_path.glob("**/*.zip"))  # the download is not kept


def test_a_link_with_spaces_around_it_is_the_same_link(tmp_path: Path) -> None:
    with respx.mock(assert_all_called=False) as router:
        github(router)
        assert run_link(tmp_path, "  https://github.com/o/repo \n").name == "repo"


def test_a_repository_that_is_not_there_says_so(tmp_path: Path) -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get(GITHUB_API).respond(404)
        with pytest.raises(UserError, match=r"did not find https://github\.com/o/repo"):
            run_link(tmp_path)


def test_a_zip_bomb_behind_a_link_is_refused_like_an_upload(tmp_path: Path) -> None:
    bomb = zip_bytes({"repo-abc/zeros.csv": "0\n" * 2_000_000})  # 4 MB, a few KB packed
    small = dataclasses.replace(pk.LOCAL, unpacked=1024 * 1024)
    with respx.mock(assert_all_called=False) as router:
        github(router, bomb)
        with pytest.raises(UserError, match="bigger than"):
            run_link(tmp_path, limits=small)
    assert not list((tmp_path / "out").glob("*.html"))


def test_a_download_over_the_limit_is_stopped(tmp_path: Path) -> None:
    tiny = dataclasses.replace(pk.HOSTED, download=100)
    with respx.mock(assert_all_called=False) as router:
        github(router)
        with pytest.raises(UserError, match="Download it yourself"):
            run_link(tmp_path, limits=tiny)


def test_the_tokens_of_this_computer_go_to_the_hosts_api_when_the_person_runs_it_themself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_PAT_GITHUB_COM", "ghp_secret")
    monkeypatch.setattr("metacheck.archives.github._TOKEN_CACHE", {})
    with respx.mock(assert_all_called=False) as router:
        api = router.get(GITHUB_API).respond(302, headers={"location": CODELOAD})
        router.get(CODELOAD).respond(200, content=zip_bytes())
        run_link(tmp_path, limits=pk.LOCAL)
        assert api.calls[0].request.headers["authorization"] == "token ghp_secret"
        run_link(tmp_path, limits=pk.HOSTED)  # a shared server uses none for what a visitor names
        assert "authorization" not in api.calls[1].request.headers


def test_the_limits_of_a_shared_server_are_the_tighter() -> None:
    local, shared = pk.LOCAL, pk.HOSTED
    assert shared.download <= 50 * 1024**2  # what the page takes as an upload
    assert shared.unpacked < local.unpacked and shared.files < local.files
    assert shared.ratio < local.ratio and shared.depth < local.depth
    assert shared.download_seconds < local.download_seconds
    assert shared.credentials is False and local.credentials is True
    assert (
        set(shared.kwargs())
        == set(local.kwargs())
        == {
            "max_download",
            "download_timeout",
            "max_bytes",
            "max_files",
            "max_ratio",
            "max_depth",
            "credentials",
        }
    )


def test_a_hosted_run_unpacks_into_its_own_folder_and_uses_no_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What the run's process does, run here: all it makes is under the folder the server removes."""
    monkeypatch.setenv("GITHUB_PAT_GITHUB_COM", "ghp_secret")
    monkeypatch.setattr("metacheck.archives.github._TOKEN_CACHE", {})
    monkeypatch.setattr(tempfile, "tempdir", None)
    work = tmp_path / "work"
    work.mkdir()
    seen: list[str] = []
    real = tempfile.mkdtemp

    def watching(*args: Any, **kwargs: Any) -> str:
        made = real(*args, **kwargs)
        seen.append(made)
        return made

    monkeypatch.setattr(tempfile, "mkdtemp", watching)
    with respx.mock(assert_all_called=False) as router:
        api = router.get(GITHUB_API).respond(302, headers={"location": CODELOAD})
        router.get(CODELOAD).respond(200, content=zip_bytes())
        result = ui.analyse_package(
            "https://github.com/o/repo", DEFAULT, str(work), pk.HOSTED, lambda _f, _t: None
        )
    assert "authorization" not in api.calls[0].request.headers
    assert result.name == "repo" and result.report_path.is_relative_to(work)
    assert seen and all(Path(made).is_relative_to(work) for made in seen), seen
    assert not [p for p in work.iterdir() if p.name != "report"]  # the download and the unpacking


def test_a_hosted_run_on_an_upload_that_is_gone_says_so(tmp_path: Path) -> None:
    with pytest.raises(UserError, match="no longer on the server"):
        ui.analyse_package(
            str(tmp_path / "gone.zip"), DEFAULT, str(tmp_path), pk.HOSTED, lambda _f, _t: None
        )


# -- the local app's box ----------------------------------------------------------------------


def client() -> TestClient:
    tc = TestClient(
        create_app(PORT, TOKEN), base_url=f"http://127.0.0.1:{PORT}", follow_redirects=False
    )
    assert tc.get(f"/?token={TOKEN}").status_code == 303
    assert cookie_name(PORT) in tc.cookies
    return tc


def settings(preset: str = DEFAULT) -> list[Any]:
    """The other inputs of the check button: online, data, reader, key, remember, ticked checks,
    paper preset, package preset, classifier."""
    return [False, False, "grobid", "", False, ui.catalog.selected_checks(), "", preset, True]


def event(
    tc: TestClient, box: dict[str, Any], session: str, preset: str = DEFAULT
) -> dict[str, Any]:
    deps = tc.get("/config").json()["dependencies"]
    (dep,) = (d for d in deps if d["targets"][0][1] == "click" and len(d["inputs"]) == 10)
    body = {"data": [box, *settings(preset)], "fn_index": dep["id"], "session_hash": session}
    assert tc.post("/gradio_api/queue/join", json=body).status_code == 200
    text = ""
    with tc.stream("GET", "/gradio_api/queue/data", params={"session_hash": session}) as stream:
        for line in stream.iter_lines():
            text += line
            if '"process_completed"' in line:
                break
    done = next(part for part in text.split("data: ") if '"process_completed"' in part)
    return json.loads(done)  # type: ignore[no-any-return]


def typed(text: str) -> dict[str, Any]:
    return {"text": text, "files": []}


def dropped(tc: TestClient, name: str, body: bytes) -> dict[str, Any]:
    (path,) = tc.post("/gradio_api/upload", files={"files": (name, body, "application/zip")}).json()
    return {"text": "", "files": [{"path": path, "meta": {"_type": "gradio.FileData"}}]}


def shown(done: dict[str, Any]) -> dict[str, Any]:
    """The outputs of the check button by name."""
    assert done["success"], done
    names = [
        "panel",
        "results",
        "summary",
        "table",
        "checklist",
        "frame",
        "download",
        "status",
        "stop",
    ]
    return dict(zip(names, done["output"]["data"], strict=False))


def test_a_link_in_the_box_is_checked_as_a_data_package(tmp_path: Path) -> None:
    with respx.mock(assert_all_called=False) as router, client() as tc:
        github(router)
        out = shown(event(tc, typed("https://github.com/o/repo"), "b1"))
        assert out["summary"].startswith("Checked **repo** in ")
        assert out["results"]["visible"] is True
        assert out["table"]["headers"] == ["Check", "Status", "Result"]
        assert len(out["table"]["data"]) == default_checks()
        assert {row[1] for row in out["table"]["data"]} == {"Data package"}
        checklist = out["checklist"]  # the package's requirements, which a paper has none of
        assert (
            checklist["visible"] is True and checklist["value"]["headers"] == pk.CHECKLIST_COLUMNS
        )
        assert {row[2] for row in checklist["value"]["data"]} >= {"Pass", "Warning"}
        assert 'href="/report/b1/repo_report.html"' in out["download"]
        assert out["status"] == "" and out["stop"]["visible"] is False
        got = tc.get("/report/b1/repo_report.html")
        assert got.status_code == 200 and got.headers["content-security-policy"] == "sandbox"


def test_an_archive_in_the_box_is_checked_as_a_data_package(tmp_path: Path) -> None:
    with client() as tc:
        box = dropped(
            tc, "my_study.zip", zip_bytes({"README.md": "# Study\n", "d/a.csv": "a,b\n1,2\n"})
        )
        out = shown(event(tc, box, "b2"))
    assert out["summary"].startswith("Checked **my\\_study** in ")
    assert out["checklist"]["visible"] is True


def test_a_paper_in_the_box_is_still_a_paper(tmp_path: Path) -> None:
    with client() as tc:
        box = dropped(tc, "paper.json", pc.demofile("json").read_bytes())
        out = shown(event(tc, box, "b3"))
    assert "Ran 19 checks" in out["summary"]
    assert out["checklist"]["visible"] is False  # the checklist is a data package's


def test_a_folder_typed_in_the_box_is_read_inside_home_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "study1"
    (root / "data").mkdir(parents=True)
    (root / "README.md").write_text("# Study\n")
    (root / "data" / "a.csv").write_text("a,b\n1,2\n")
    outside = tmp_path.parent / f"outside-{tmp_path.name}"
    outside.mkdir()
    (outside / "README.md").write_text("# Private\n")
    with client() as tc:
        assert shown(event(tc, typed(str(root)), "b4"))["summary"].startswith("Checked **study1**")
        refused = event(tc, typed(str(outside)), "b5")
        assert not refused["success"]
        text = json.dumps(refused)
        assert "outside the places this page may read" in text and "Private" not in text
        monkeypatch.setenv("METACHECK_APP_ROOTS", str(outside.parent))
        assert event(tc, typed(str(outside)), "b6")["success"]


@pytest.mark.parametrize(
    ("box", "message"),
    [
        (typed(""), "Upload a paper or an archive"),
        (typed("https://example.com/o/r"), "not a repository this can fetch from"),
        (typed("https://127.0.0.1/x"), "not a repository this can fetch from"),
        (typed("https://127.0.0.1:8000/x"), "login or a port"),
        (typed("https://github.com/o"), "owner and the repository"),
    ],
)
def test_the_box_answers_in_plain_words(box: dict[str, Any], message: str) -> None:
    with client() as tc:
        done = event(tc, box, "b7")
    assert not done["success"] and message in json.dumps(done)


def test_a_file_and_a_link_together_are_refused(tmp_path: Path) -> None:
    with client() as tc:
        box = dropped(tc, "p.zip", zip_bytes())
        box["text"] = "https://github.com/o/repo"
        done = event(tc, box, "b8")
    assert not done["success"] and "not both" in json.dumps(done)


def test_the_file_types_the_box_takes_are_checked_by_the_server_too() -> None:
    with client() as tc:
        box = dropped(tc, "notes.txt", b"hello")
        done = event(tc, box, "b9")
    assert not done["success"] and "Invalid file type" in json.dumps(done)


def test_a_run_from_a_link_leaves_no_download_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    with respx.mock(assert_all_called=False) as router, client() as tc:
        github(router)
        shown(event(tc, typed("https://github.com/o/repo"), "b10"))
    assert leftovers(scratch) == []


def _props(config: dict[str, Any], **match: Any) -> dict[str, Any]:
    (found,) = (
        c["props"]
        for c in config["components"]
        if all(c["props"].get(k) == v for k, v in match.items())
    )
    return found  # type: ignore[no-any-return]


def test_the_page_names_the_package_choices_and_the_box() -> None:
    config = ui.build_app().get_config_file()
    boxes = [c for c in config["components"] if c["type"] == "multimodaltextbox"]
    assert [b["props"]["label"] for b in boxes] == [ui.BOX_LABEL]  # the page starts on Paper
    box = boxes[0]["props"]
    assert box["file_count"] == "single" and box["info"] == ui.BOX_INFO
    assert set(box["file_types"]) >= {".pdf", ".xml", ".json", ".zip", ".tar", ".gz", ".tgz"}
    labels = {c["props"].get("label") for c in config["components"]}
    assert {ui.PACKAGE_PRESET_LABEL, ui.CLASSIFIER_LABEL, "Checklist of the data package"} <= labels
    switch = _props(config, elem_id="mc-mode")
    assert switch["value"] == ui.PAPER_MODE
    assert [value for _, value in switch["choices"]] == [ui.PAPER_MODE, ui.PACKAGE_MODE]
    # Data package names the box for a package; only this computer has folders
    on_mode = _handlers(ui.build_app())["on_mode"]
    here = on_mode(ui.PACKAGE_MODE)[0]
    assert here["label"] == ui.PACKAGE_BOX_LABEL and here["info"] == ui.PACKAGE_BOX_INFO_LOCAL
    shared = ui.build_app(hosted=hosting.HostedConfig((TOKEN_A,), (HOST,)))
    there = _handlers(shared)["on_mode"](ui.PACKAGE_MODE)[0]
    assert there["info"] == ui.PACKAGE_BOX_INFO and "folder" not in there["info"]
    assert on_mode(ui.PAPER_MODE)[0]["label"] == ui.BOX_LABEL


def _handlers(blocks: Any) -> dict[str, Any]:
    return {fn.name: fn.fn for fn in blocks.fns.values()}


def test_the_switch_shows_the_options_and_the_demo_of_its_mode() -> None:
    on_mode = _handlers(ui.build_app())["on_mode"]
    _box, paper, package, demo, demo_package = on_mode(ui.PACKAGE_MODE)
    assert paper["visible"] is False and package["visible"] is True
    assert demo["visible"] is False and demo_package["visible"] is True
    _box, paper, package, demo, demo_package = on_mode(ui.PAPER_MODE)
    assert paper["visible"] is True and package["visible"] is False
    assert demo["visible"] is True and demo_package["visible"] is False


def test_the_switch_follows_the_box_and_says_how_it_is_checked(tmp_path: Path) -> None:
    on_box = _handlers(ui.build_app())["on_box"]
    paper = {"text": "", "files": [str(tmp_path / "my paper.pdf")]}
    archive = {"text": "", "files": [str(tmp_path / "study.zip")]}
    mode, line = on_box(paper, ui.PAPER_MODE)
    assert (
        mode == {"__type__": "update"} and line == "Ready to check **my paper\\.pdf** as a paper."
    )
    mode, line = on_box(archive, ui.PAPER_MODE)  # a package dropped on Paper moves the switch
    assert mode == ui.PACKAGE_MODE
    assert line.startswith("Ready to check the archive **study\\.zip** as a data package.")
    assert line.endswith(ui.SWITCHED[ui.PACKAGE_MODE])
    mode, line = on_box(paper, ui.PACKAGE_MODE)
    assert mode == ui.PAPER_MODE and line.endswith(ui.SWITCHED[ui.PAPER_MODE])
    mode, line = on_box(typed("github.com/o/repo"), ui.PACKAGE_MODE)
    assert line == (
        "Ready to check **github\\.com/o/repo** as a data package. The repository is "
        "downloaded when you press Check."
    )
    # a text is a package, even while it is being typed and is no link yet
    mode, line = on_box(typed("https://github.com/o"), ui.PAPER_MODE)
    assert mode == ui.PACKAGE_MODE and line == ui.SWITCHED[ui.PACKAGE_MODE]
    # a file that cannot be checked is named at once
    _mode, line = on_box({"text": "", "files": [str(tmp_path / "notes.docx")]}, ui.PAPER_MODE)
    assert line.startswith(pk.BAD_FILE)
    assert on_box(ui.EMPTY_BOX, ui.PAPER_MODE)[1] == ""


def test_moving_the_switch_takes_out_what_the_other_mode_holds(tmp_path: Path) -> None:
    on_mode_input = _handlers(ui.build_app())["on_mode_input"]
    paper = {"text": "", "files": [str(tmp_path / "paper.json")]}
    assert on_mode_input(ui.PACKAGE_MODE, paper) == (ui.EMPTY_BOX, "")
    assert on_mode_input(ui.PAPER_MODE, typed("https://github.com/o/repo")) == (ui.EMPTY_BOX, "")
    kept = on_mode_input(ui.PAPER_MODE, paper)
    assert kept == ({"__type__": "update"}, {"__type__": "update"})


def test_the_demo_package_is_checked_like_an_uploaded_archive(tmp_path: Path) -> None:
    archive = ui.demo_package_archive(tmp_path)
    with zipfile.ZipFile(archive) as opened:
        names = opened.namelist()
    assert archive.name == "sleep_study.zip"
    assert "sleep_study/README.md" in names and "sleep_study/data/raw/sleep_survey.csv" in names
    on_demo_package = _handlers(ui.build_app())["on_demo_package"]
    sent = list(on_demo_package(DEFAULT, False, SimpleNamespace(session_hash="dp1")))
    assert "Open the archive" in sent[0][0]
    out = dict(zip(["panel", "results", "summary", "table", "checklist"], sent[-1], strict=False))
    assert out["panel"] == "" and out["summary"].startswith("Checked **sleep\\_study** in ")
    statuses = set(out["checklist"]["value"].data["Status"])
    assert {"Fail", "Warning", "Manual", "Pass"} <= statuses  # something to show in a demo


def test_a_preset_the_app_was_started_with_is_chosen_for_packages(tmp_path: Path) -> None:
    make_pack(tmp_path)
    config = ui.build_app(start_preset="tue::data_steward").get_config_file()
    dropdown = _props(config, label=ui.PACKAGE_PRESET_LABEL)
    assert dropdown["value"] == "tue::data_steward"
    assert [ref for _, ref in dropdown["choices"]] == [
        DEFAULT,
        "tue::data_steward",
        "tue::own_check",
        "datapackage::tue",
    ]
    # the page opens on Data package, where the choice is
    assert _props(config, elem_id="mc-mode")["value"] == ui.PACKAGE_MODE
    assert _props(config, label=ui.PICKER_TITLE)["open"] is False
    plain = ui.build_app().get_config_file()
    assert _props(plain, label=ui.PACKAGE_PRESET_LABEL)["value"] == DEFAULT
    assert _props(plain, elem_id="mc-mode")["value"] == ui.PAPER_MODE


def test_a_shared_server_can_start_on_the_tue_preset() -> None:
    config = hosting.HostedConfig((TOKEN_A,), (HOST,), preset="datapackage::tue")
    page = ui.build_app(hosted=config, start_preset=config.preset).get_config_file()
    assert _props(page, label=ui.PACKAGE_PRESET_LABEL)["value"] == "datapackage::tue"
    assert _props(page, elem_id="mc-mode")["value"] == ui.PACKAGE_MODE
    with TestClient(
        create_hosted_app(PORT, config), base_url=f"https://{HOST}", follow_redirects=False
    ) as tc:
        tc.get(f"/?token={TOKEN_A}")
        served = tc.get("/config").json()
    assert _props(served, label=ui.PACKAGE_PRESET_LABEL)["value"] == "datapackage::tue"


def test_the_package_page_takes_a_link_too(tmp_path: Path) -> None:
    with respx.mock(assert_all_called=False) as router, client() as tc:
        github(router)
        deps = tc.get("/config").json()["dependencies"]
        radios = {
            c["id"]
            for c in tc.get("/config").json()["components"]
            if c["type"] == "radio" and c["props"].get("label") == "The package is"
        }
        (dep,) = (
            d
            for d in deps
            if d["targets"][0][1] == "click" and d["inputs"] and d["inputs"][0] in radios
        )
        body = {
            "data": ["link", "", None, "https://github.com/o/repo", DEFAULT, False],
            "fn_index": dep["id"],
            "session_hash": "pp1",
        }
        assert tc.post("/gradio_api/queue/join", json=body).status_code == 200
        text = ""
        with tc.stream("GET", "/gradio_api/queue/data", params={"session_hash": "pp1"}) as stream:
            for line in stream.iter_lines():
                text += line
                if '"process_completed"' in line:
                    break
    done = json.loads(next(p for p in text.split("data: ") if '"process_completed"' in p))
    assert done["success"] and done["output"]["data"][1].startswith("Checked **repo**")
    for blank in ("", "  ", None):
        with pytest.raises(UserError, match="Type the link"):
            pk.resolve_link(blank)
    assert pk.resolve_link('  "https://github.com/o/repo" ') == "https://github.com/o/repo"
    assert pk.resolve_link("github.com/o/repo") == "https://github.com/o/repo"
    assert pk.resolve_link("doi:10.5281/zenodo.1") == "https://doi.org/10.5281/zenodo.1"
    with pytest.raises(UserError, match="not a repository this can fetch from"):
        pk.resolve_link("https://example.com/x")


# -- a shared server's box ----------------------------------------------------------------------


@pytest.fixture
def shared_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in ("TOKENS", "HOSTS", "JOB_TIMEOUT", "COMMIT", "AUTH", "USER_HEADER", "PRESET"):
        monkeypatch.delenv(f"METACHECK_APP_{name}", raising=False)
    monkeypatch.setattr(hosting, "START_METHOD", "spawn")
    return monkeypatch


def shared_client(port: int, timeout: float = hosting.DEFAULT_TIMEOUT) -> TestClient:
    config = hosting.HostedConfig((TOKEN_A,), (HOST,), timeout)
    tc = TestClient(
        create_hosted_app(port, config), base_url=f"https://{HOST}", follow_redirects=False
    )
    tc.get(f"/?token={TOKEN_A}")
    return tc


def test_a_shared_server_checks_an_uploaded_archive_and_keeps_nothing(
    tmp_path: Path, shared_env: pytest.MonkeyPatch
) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    shared_env.setenv("TMPDIR", str(scratch))
    shared_env.setattr(tempfile, "tempdir", None)
    with shared_client(PORT + 1) as tc:
        box = dropped(
            tc, "my_study.zip", zip_bytes({"README.md": "# Study\n", "d/a.csv": "a,b\n1,2\n"})
        )
        path = Path(box["files"][0]["path"])
        assert path.exists()
        out = shown(event(tc, box, "h1"))
        assert out["summary"].startswith("Checked **my\\_study** in ")
        assert out["checklist"]["visible"] is True
        assert "data:text/html" in out["download"]  # the report comes with the page
        assert out["download"].count("data:text/html") == 2  # and its static copy for email
        assert "Download for email (no script)" in out["download"]
        assert tc.get("/report/h1/my_study_report.html").status_code == 404  # no file on the server
    assert not path.exists()
    assert leftovers(scratch) == []  # not the unpacked package, not the report folder


def test_a_shared_server_clears_the_box_after_a_run(shared_env: pytest.MonkeyPatch) -> None:
    with shared_client(PORT + 2) as tc:
        box = dropped(tc, "again.zip", zip_bytes({"README.md": "# Study\n"}))
        done = event(tc, box, "h2")
    assert done["success"]
    assert done["output"]["data"][-1] == {"text": "", "files": []}
    assert len(done["output"]["data"]) == 10


def test_a_shared_server_does_not_read_a_folder_or_follow_a_link_beyond_its_hosts(
    shared_env: pytest.MonkeyPatch,
) -> None:
    with shared_client(PORT + 3) as tc:
        for text, message in [
            ("/etc", "not a link this server can check"),
            ("~", "not a link this server can check"),
            ("file:///etc/passwd", "not a web link"),
            ("https://127.0.0.1/o/r", "not a repository this can fetch from"),
            ("http://169.254.169.254/latest/meta-data/", "not a repository this can fetch from"),
            ("https://example.com/o/r", "not a repository this can fetch from"),
        ]:
            done = event(tc, typed(text), "h3")
            assert not done["success"] and message in json.dumps(done), text


def test_a_shared_server_refuses_a_zip_bomb(tmp_path: Path, shared_env: pytest.MonkeyPatch) -> None:
    # 4 MB of zeros, a few KB packed, against a limit of 1 MB unpacked
    shared_env.setattr(pk, "HOSTED", dataclasses.replace(pk.HOSTED, unpacked=1024 * 1024))
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    shared_env.setenv("TMPDIR", str(scratch))
    shared_env.setattr(tempfile, "tempdir", None)
    with shared_client(PORT + 4) as tc:
        box = dropped(tc, "bomb.zip", zip_bytes({"big/zeros.csv": "0\n" * 2_000_000}))
        done = event(tc, box, "h4")
        path = Path(box["files"][0]["path"])
    assert not done["success"] and "bigger than" in json.dumps(done)
    assert not path.exists() and leftovers(scratch) == []


def test_a_shared_server_refuses_an_archive_with_too_many_files(
    shared_env: pytest.MonkeyPatch,
) -> None:
    shared_env.setattr(pk, "HOSTED", dataclasses.replace(pk.HOSTED, files=20))
    with shared_client(PORT + 5) as tc:
        box = dropped(tc, "many.zip", zip_bytes({f"p/f{n}.txt": "x" for n in range(30)}))
        done = event(tc, box, "h5")
    assert not done["success"] and "more than 20 files" in json.dumps(done)


def test_a_package_over_the_time_limit_gets_a_plain_message(
    tmp_path: Path, shared_env: pytest.MonkeyPatch
) -> None:
    import multiprocessing

    shared_env.syspath_prepend(str(Path(__file__).parent))
    import _hosted_jobs

    shared_env.setattr(ui, "analyse_package", _hosted_jobs.sleep_package)
    with shared_client(PORT + 6, timeout=0.5) as tc:
        box = dropped(tc, "slow.zip", zip_bytes({"README.md": "# Study\n"}))
        path = Path(box["files"][0]["path"])
        start = time.perf_counter()
        done = event(tc, box, "h6")
        assert time.perf_counter() - start < 10
    assert not done["success"] and hosting.TOO_SLOW_PACKAGE in json.dumps(done)
    assert not path.exists()
    assert not multiprocessing.active_children()  # the run was stopped, not abandoned


def test_a_shared_server_gives_a_link_run_the_limits_and_no_token(
    shared_env: pytest.MonkeyPatch,
) -> None:
    """Through the app, in the run's process: what the run is handed for a link."""
    shared_env.syspath_prepend(str(Path(__file__).parent))
    import _hosted_jobs

    shared_env.setattr(ui, "analyse_package", _hosted_jobs.echo_package)
    with shared_client(PORT + 7) as tc:
        done = event(tc, typed("https://github.com/o/repo"), "h7")
        out = shown(done)
        # a link typed without https://, and a bare DOI, reach the run as links (not as the
        # path of an upload that is gone)
        bare = [
            json.loads(shown(event(tc, typed(text), "h7"))["summary"].split("\n\n", 1)[1])
            for text in ("github.com/o/repo", "10.5281/zenodo.1234567")
        ]
    seen = json.loads(out["summary"].split("\n\n", 1)[1])  # the stand-in tells in its note
    assert seen["source"] == "https://github.com/o/repo" and seen["preset"] == DEFAULT
    assert (
        seen["limits"] == dataclasses.asdict(pk.HOSTED) and seen["limits"]["credentials"] is False
    )
    assert [b["source"] for b in bare] == [
        "https://github.com/o/repo",
        "https://doi.org/10.5281/zenodo.1234567",
    ]


def test_a_shared_server_runs_the_demo_package(shared_env: pytest.MonkeyPatch) -> None:
    """In the run's process, like an upload, and nothing to clear from the box after it."""
    with shared_client(PORT + 8) as tc:
        config = tc.get("/config").json()
        (button,) = (
            c["id"]
            for c in config["components"]
            if c["props"].get("value") == ui.DEMO_PACKAGE_BUTTON
        )
        (dep,) = (d for d in config["dependencies"] if d["targets"][0][0] == button and d["inputs"])
        body = {"data": [DEFAULT, False], "fn_index": dep["id"], "session_hash": "h8"}
        assert tc.post("/gradio_api/queue/join", json=body).status_code == 200
        text = ""
        with tc.stream("GET", "/gradio_api/queue/data", params={"session_hash": "h8"}) as stream:
            for line in stream.iter_lines():
                text += line
                if '"process_completed"' in line:
                    break
    done = json.loads(next(p for p in text.split("data: ") if '"process_completed"' in p))
    out = shown(done)
    assert out["summary"].startswith("Checked **sleep\\_study** in ")
    assert out["checklist"]["visible"] is True and out["panel"] == ""
    assert "Open the archive" in text  # its steps were drawn
    assert done["output"]["data"][-1] == {"__type__": "update"}  # the box is left as it is


def test_the_shared_server_note_names_the_limit() -> None:
    assert "50 MB" in hosting.PACKAGE_NOTE
    assert pk.HOSTED.download == 50 * 1024**2
    assert hosting.package_note(pk.HOSTED.download) == hosting.PACKAGE_NOTE
    # an operator who raises the download limit gets it named; an upload stays at 50 MB
    raised = hosting.package_note(200 * 1024**2)
    assert "An archive can be up to 50 MB, and a repository up to 200 MB" in raised
