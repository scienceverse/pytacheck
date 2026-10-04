"""The "Check a data package" page: finding the package, the tables, the zip guard, the page."""

from __future__ import annotations

import json
import os
import re
import stat
import zipfile
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest
from starlette.testclient import TestClient

from metacheck.app import launch, ui, ui_package
from metacheck.app import package as pk
from metacheck.app.run import UserError
from metacheck.app.security import cookie_name
from metacheck.app.server import create_app, create_hosted_app
from metacheck.config import update_config
from metacheck.packs.registry import refresh

FILES = {
    "README.md": "# Study\n\nData and code.\n",
    "data/survey.csv": "participant_id,age,gender\n1,23,f\n2,31,m\n3,27,f\n4,45,m\n",
    "code/analysis.R": "d <- read.csv('data/survey.csv')\n",
}
PORT = 4323
TOKEN = "package-page-token"
HOST = "metacheck.example.org"


@pytest.fixture(autouse=True)
def hermetic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No user config or packs, the concept model is never loaded (a download), and the home
    folder is ``tmp_path``: the page reads only inside home, and a test's folders are there."""
    monkeypatch.setenv("PYTACHECK_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("PYTACHECK_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("PYTACHECK_PRESET", raising=False)
    monkeypatch.delenv("METACHECK_PRESET", raising=False)
    monkeypatch.delenv("METACHECK_APP_ROOTS", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))  # Path.home() on Linux and macOS
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # ... and on Windows

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("the local concept model must not be loaded in a test")

    monkeypatch.setattr("metacheck.datacheck.concepts.load_classifier", refuse)
    # the page asks for the classifier by default: say the concepts extra is not installed (as
    # in the app's own install), whatever this environment has, so nothing is ever downloaded
    monkeypatch.setattr("metacheck.datacheck.concepts.classifier_available", lambda: False)
    refresh()
    yield
    refresh()


def default_checks() -> int:
    """How many checks the default preset holds, read from the preset, not written down here."""
    from metacheck.presets import expand

    return len(expand("datapackage::default"))


def folder(tmp_path: Path, name: str = "study1", files: dict[str, str] | None = None) -> Path:
    root = tmp_path / name
    for rel, text in (FILES if files is None else files).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def run_on(source: Path, tmp_path: Path, **kw: Any) -> pk.PackageAnalysis:
    return pk.check_package_source(
        source, kw.pop("preset", "datapackage::default"), report_dir=tmp_path / "out", **kw
    )


# -- the presets --------------------------------------------------------------------------


def test_the_default_preset_is_offered_first_with_its_description() -> None:
    found = pk.package_presets()
    assert [ref for _, ref in found] == ["datapackage::default"]
    assert found[0][0].startswith("datapackage::default: Check a data package")


def test_presets_come_from_the_registry_not_a_fixed_list(tmp_path: Path) -> None:
    pack = tmp_path / "labx"
    pack.mkdir()
    (pack / "pack.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "name": "labx",
                "version": "1.0.0",
                "presets": {
                    "audit": {"description": "Our audit", "extends": "datapackage::default"},
                    "papers": {"modules": ["metacheck::all_p_values"]},
                },
            }
        )
    )

    def edit(cfg: dict[str, Any]) -> None:
        cfg["packs"] = {"labx": {"path": str(pack)}}
        cfg["presets"] = {
            "aaa_office": {"description": "Office", "extends": "datapackage::default"},
            "aaa_quick": {"modules": ["metacheck::all_p_values"]},
            "aaa_broken": {"extends": "nowhere::missing"},
        }

    update_config("user", edit)
    refresh()
    refs = [ref for _, ref in pk.package_presets()]
    assert refs[0] == "datapackage::default"  # first, though "aaa_office" sorts before it
    assert set(refs) == {"datapackage::default", "labx::audit", "aaa_office"}
    labels = {ref: label for label, ref in pk.package_presets()}
    assert labels["labx::audit"] == "labx::audit: Our audit"
    # the page's choice is built from the same list
    app = ui.build_app()
    assert "labx::audit" in json.dumps(app.get_config_file())


# -- finding the package ------------------------------------------------------------------


def test_the_folder_is_the_one_typed_quotes_and_all(tmp_path: Path) -> None:
    root = folder(tmp_path)
    assert pk.resolve_source(pk.FOLDER, str(root), None) == root.resolve()
    assert pk.resolve_source(pk.FOLDER, f'  "{root}"  ', None) == root.resolve()
    assert pk.resolve_source(pk.FOLDER, f"'{root}'", "ignored.zip") == root.resolve()


def test_nothing_or_nothing_there_is_a_plain_message(tmp_path: Path) -> None:
    with pytest.raises(UserError, match="Type the path"):
        pk.resolve_source(pk.FOLDER, "  ", None)
    with pytest.raises(UserError, match="Type the path"):
        pk.resolve_source(pk.FOLDER, None, None)
    with pytest.raises(UserError, match="No folder was found"):
        pk.resolve_source(pk.FOLDER, str(tmp_path / "nope"), None)
    with pytest.raises(UserError, match="No folder was found"):  # a file is not a folder
        pk.resolve_source(pk.FOLDER, str(tmp_path / "a.zip"), None)
    with pytest.raises(UserError, match="Upload a zip"):
        pk.resolve_source(pk.ZIP, str(tmp_path), None)
    with pytest.raises(UserError, match="no longer on the server"):
        pk.resolve_source(pk.ZIP, "", str(tmp_path / "gone.zip"))
    (tmp_path / "notes.txt").write_text("x")
    with pytest.raises(UserError, match="not a zip or tar"):
        pk.resolve_source(pk.ZIP, "", str(tmp_path / "notes.txt"))


def test_a_zip_or_tar_upload_is_accepted(tmp_path: Path) -> None:
    for name in ("p.zip", "p.tar.gz", "P.TGZ"):
        (tmp_path / name).write_bytes(b"")
        assert pk.resolve_source(pk.ZIP, "", str(tmp_path / name)) == tmp_path / name


# -- where a folder may be ----------------------------------------------------------------
# The fixture makes ``tmp_path`` the home folder; ``elsewhere`` is a folder outside it.


@pytest.fixture
def elsewhere(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return folder(tmp_path_factory.mktemp("elsewhere"), "secrets", {"README.md": "# Private\n"})


def _link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):  # Windows without the right to make links
        pytest.skip("this system does not let the test make a symbolic link")


def test_a_folder_inside_home_is_read(tmp_path: Path) -> None:
    root = folder(tmp_path)
    assert pk.resolve_source(pk.FOLDER, str(root), None) == root.resolve()
    assert pk.resolve_source(pk.FOLDER, "~/study1", None) == root.resolve()  # ~ is home
    assert pk.resolve_source(pk.FOLDER, str(tmp_path), None) == tmp_path.resolve()  # home itself
    deep = folder(tmp_path, "a/b/c")
    assert pk.resolve_source(pk.FOLDER, str(deep), None) == deep.resolve()


def test_a_folder_outside_home_is_refused_and_says_where_and_why(
    tmp_path: Path, elsewhere: Path
) -> None:
    with pytest.raises(UserError) as caught:
        pk.resolve_source(pk.FOLDER, str(elsewhere), None)
    message = str(caught.value)
    assert str(tmp_path.resolve()) in message  # where folders may be
    assert "home folder" in message and "no other program" in message  # and why
    assert pk.ROOTS_ENV == "METACHECK_APP_ROOTS" and pk.ROOTS_ENV in message  # how to widen it
    # the answer does not say whether a folder outside exists
    ghost = elsewhere.parent / "not-there"
    with pytest.raises(UserError) as other:
        pk.resolve_source(pk.FOLDER, str(ghost), None)
    assert str(other.value) == message
    with pytest.raises(UserError, match="outside the places"):
        pk.resolve_source(pk.FOLDER, str(Path(tmp_path.anchor) / "etc"), None)


def test_a_link_in_home_that_points_out_of_it_is_refused(tmp_path: Path, elsewhere: Path) -> None:
    link = tmp_path / "innocent"
    _link(link, elsewhere)
    assert link.is_dir()  # it looks like a folder inside home
    with pytest.raises(UserError, match="outside the places"):
        pk.resolve_source(pk.FOLDER, str(link), None)
    with pytest.raises(UserError, match="outside the places"):  # a link in the middle, too
        pk.resolve_source(pk.FOLDER, str(link / "data"), None)
    # a link that stays inside home is fine, and the folder that is checked is its target
    inside = folder(tmp_path, "real")
    _link(tmp_path / "shortcut", inside)
    assert pk.resolve_source(pk.FOLDER, str(tmp_path / "shortcut"), None) == inside.resolve()


def test_going_up_with_dot_dot_does_not_leave_home(tmp_path: Path, elsewhere: Path) -> None:
    root = folder(tmp_path)
    up = os.path.relpath(elsewhere, root)  # ../../elsewhereN/secrets
    assert ".." in up
    for text in (str(root / up), f"~/study1/{up}", f"{root}/data/../{up}"):
        with pytest.raises(UserError, match="outside the places"):
            pk.resolve_source(pk.FOLDER, text, None)
    # going up and down inside home is fine
    assert pk.resolve_source(pk.FOLDER, f"{root}/data/../code/..", None) == root.resolve()


def test_a_home_folder_that_is_a_link_still_holds_its_folders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = folder(tmp_path, "real_home/study1")
    home = tmp_path / "home_link"
    _link(home, tmp_path / "real_home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    assert pk.resolve_source(pk.FOLDER, str(home / "study1"), None) == real.resolve()
    assert pk.resolve_source(pk.FOLDER, str(real), None) == real.resolve()


def test_the_environment_variable_adds_folders(
    tmp_path: Path,
    elsewhere: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = folder(tmp_path_factory.mktemp("other"), "study2")
    monkeypatch.setenv("METACHECK_APP_ROOTS", str(elsewhere.parent))
    assert pk.resolve_source(pk.FOLDER, str(elsewhere), None) == elsewhere.resolve()
    with pytest.raises(UserError, match="outside the places"):  # a sibling is not added
        pk.resolve_source(pk.FOLDER, str(other), None)
    # several, separated as a PATH is; blanks and nothing-there entries do no harm
    value = os.pathsep.join([str(elsewhere.parent), " ", str(other.parent), str(tmp_path / "gone")])
    monkeypatch.setenv("METACHECK_APP_ROOTS", value)
    assert pk.resolve_source(pk.FOLDER, str(other), None) == other.resolve()
    assert pk.resolve_source(pk.FOLDER, str(elsewhere), None) == elsewhere.resolve()
    assert pk.resolve_source(pk.FOLDER, str(tmp_path), None) == tmp_path.resolve()  # home stays
    # a folder added by the variable is also checked after its links are followed
    stray = other.parent.parent / "stray"
    stray.mkdir()
    _link(other.parent / "to_stray", stray)
    with pytest.raises(UserError, match="outside the places"):
        pk.resolve_source(pk.FOLDER, str(other.parent / "to_stray"), None)
    # the message names the folders that are allowed
    with pytest.raises(UserError) as caught:
        pk.resolve_source(pk.FOLDER, str(stray), None)
    assert str(other.parent.resolve()) in str(caught.value)


def test_a_zip_upload_is_not_tested_for_its_place(elsewhere: Path) -> None:
    archive = elsewhere.parent / "upload.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("README.md", "# Study\n")
    assert pk.resolve_source(pk.ZIP, "", str(archive)) == archive


# -- running a package --------------------------------------------------------------------


def test_a_folder_is_checked_and_the_report_is_written(tmp_path: Path) -> None:
    root = folder(tmp_path)
    steps: list[str] = []
    result = run_on(root, tmp_path, progress=lambda _f, text: steps.append(text))
    assert steps == ["Checking the package", "Writing the report", "Done"]
    assert result.name == "study1"
    assert result.preset == "datapackage::default"
    assert list(result.checks.columns) == pk.CHECK_COLUMNS
    assert list(result.checklist.columns) == pk.CHECKLIST_COLUMNS
    titles = result.checks["Check"].tolist()
    assert titles[:3] == [
        "Data Package Files",
        "Data Package Structure",
        "Data Package Documentation",
    ]
    assert len(titles) == default_checks()
    assert set(result.checks["Status"]) <= {"Pass", "Warning", "Fail", "Info", "Not applicable"}
    # the checklist holds the items of the three checks that have one, and one row for each
    # of the others
    by_check = result.checklist.groupby("Check")["Item"].count()
    assert by_check["Data Package Files"] >= 4
    assert by_check["Data Package Documentation"] >= 8
    whole = result.checklist[result.checklist["Item"] == "Whole check"]
    assert len(whole) == 2 and not whole["Detail"].eq("").any()
    assert set(result.checklist["Status"]) <= set(pk.STATUS_WORDS.values()) | {"Info", "Fail"}
    assert "Pass" in result.checklist["Status"].tolist()
    assert "README" in result.html
    assert " pass" in pk.counts_line(result.checklist)


def test_the_report_file_is_html_under_the_report_policy(tmp_path: Path) -> None:
    root = folder(tmp_path)
    result = run_on(root, tmp_path)
    assert result.report_path == tmp_path / "out" / "study1_report.html"
    saved = result.report_path.read_text(encoding="utf-8")
    assert saved.strip() == pk.protect(result.html).strip()
    assert "Content-Security-Policy" in saved
    assert "Content-Security-Policy" not in result.html
    # the report is not one of the package's files
    assert not list(root.rglob("*report*"))


def test_a_folder_is_not_changed_and_nothing_is_downloaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import socket

    attempts: list[Any] = []

    def refuse(*args: Any, **_kwargs: Any) -> Any:
        attempts.append(args)
        raise OSError("no network in this test")

    # no lookup and no connection, for any reason: the page says nothing leaves the computer
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    root = folder(tmp_path)
    before = sorted((p.relative_to(root), p.read_bytes()) for p in root.rglob("*") if p.is_file())
    run_on(root, tmp_path)  # the fixture refuses the concept model: it is not loaded
    assert attempts == []
    after = sorted((p.relative_to(root), p.read_bytes()) for p in root.rglob("*") if p.is_file())
    assert before == after
    assert sorted(p.name for p in root.iterdir()) == ["README.md", "code", "data"]


def _selection_args(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """The ``args`` each run hands to the library's module selection, in order."""
    from metacheck.datapackage import _check

    seen: list[Any] = []
    real = _check.package_selection

    def spy(**kw: Any) -> Any:
        seen.append(kw.get("args"))
        return real(**kw)

    monkeypatch.setattr(_check, "package_selection", spy)
    return seen


def test_the_classifier_is_the_default_as_on_the_command_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _selection_args(monkeypatch)
    # the extra is there (the model itself is not loaded: it would be a download)
    monkeypatch.setattr("metacheck.datacheck.concepts.classifier_available", lambda: True)
    monkeypatch.setattr("metacheck.datacheck.concepts.load_classifier", lambda *_a, **_k: None)
    root = folder(tmp_path)
    analysis = run_on(root, tmp_path)  # no argument: the page's box is ticked
    # nothing is passed to data_check, as `metacheck package` does without -a, so that
    # METACHECK_CONCEPTS and the option decide
    assert not seen[-1]
    assert analysis.note == ""
    run_on(root, tmp_path, local_classifier=False)  # the box is unticked
    assert seen[-1] == {"data_check": {"concepts": "rules"}}


def test_without_the_extra_the_run_falls_back_to_rules_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _selection_args(monkeypatch)  # the fixture says the extra is not installed
    root = folder(tmp_path)
    analysis = run_on(root, tmp_path)  # the classifier is asked for, by default
    assert seen[-1] == {"data_check": {"concepts": "rules"}}
    assert analysis.note == pk.NO_CLASSIFIER
    assert 'pip install "metacheck[concepts]"' in analysis.note and "rules only" in analysis.note
    assert len(analysis.checks) == default_checks()  # the checks ran all the same
    assert "Did not run" not in set(analysis.checks["Status"])
    # a person who did not ask for the classifier is not told about it
    assert run_on(root, tmp_path, local_classifier=False).note == ""


def test_a_zip_is_checked_and_named_without_its_ending(tmp_path: Path) -> None:
    archive = tmp_path / "my_data.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for rel, text in FILES.items():
            zf.writestr(rel, text)
    result = run_on(archive, tmp_path)
    assert result.name == "my_data"
    assert result.report_path.name == "my_data_report.html"
    assert "Fail" not in result.checks["Status"].tolist()[:2]


def test_a_tar_gz_is_checked(tmp_path: Path) -> None:
    import tarfile

    root = folder(tmp_path, "src")
    archive = tmp_path / "pkg.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(root, arcname="pkg")
    result = run_on(archive, tmp_path)
    assert result.name == "pkg"
    assert len(result.checks) == default_checks()


def test_a_preset_that_does_not_exist_is_a_plain_message(tmp_path: Path) -> None:
    with pytest.raises(UserError, match="nowhere"):
        run_on(folder(tmp_path), tmp_path, preset="nowhere::default")


# -- the zip guard ------------------------------------------------------------------------


def malicious_zip(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("pkg/README.md", "# Study\n\nData.\n")
        zf.writestr("pkg/data/a.csv", "id,age\n1,20\n2,30\n")
        zf.writestr("../evil_dotdot.txt", "x")
        zf.writestr("pkg/../../evil_nested.txt", "x")
        zf.writestr("/tmp/mc_evil_absolute.txt", "x")
        zf.writestr("C:\\mc_evil_drive.txt", "x")
        zf.writestr("..\\evil_backslash.txt", "x")
        link = zipfile.ZipInfo("pkg/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(link, "/etc/passwd")
    return path


def test_a_malicious_zip_writes_nothing_outside_its_folder(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    archive = malicious_zip(work / "evil.zip")
    result = run_on(archive, tmp_path)
    # the good members were checked, with the wrapper folder dropped
    assert result.name == "evil"
    files = result.checks.loc[result.checks["Check"] == "Data Package Files", "Result"].item()
    assert "2 files" in files
    # and nothing the archive named outside its folder exists, anywhere it could have gone
    names = (
        "evil_dotdot.txt",
        "evil_nested.txt",
        "evil_backslash.txt",
        "mc_evil_drive.txt",
        "mc_evil_absolute.txt",
    )
    for name in names:
        assert not (tmp_path / name).exists()
        assert not (work / name).exists()
        assert not (work.parent / name).exists()
        assert not (tmp_path.parent / name).exists()
        assert not Path("/tmp", name).exists()
    assert sorted(p.name for p in work.iterdir()) == ["evil.zip"]
    for name in names:
        assert name not in result.html
    # the link was not followed or kept
    assert "passwd" not in result.html


def test_the_unpacked_size_and_the_number_of_files_are_capped(tmp_path: Path) -> None:
    archive = tmp_path / "bomb.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("big/zeros.csv", "0\n" * 2_000_000)  # 4 MB, a few KB compressed
    assert archive.stat().st_size < 100_000
    with pytest.raises(UserError, match="bigger than"):
        run_on(archive, tmp_path, max_bytes=1024 * 1024)
    many = tmp_path / "many.zip"
    with zipfile.ZipFile(many, "w") as zf:
        for i in range(30):
            zf.writestr(f"p/f{i}.txt", "x")
    with pytest.raises(UserError, match="more than 20 files"):
        run_on(many, tmp_path, max_files=20)
    # nothing was left behind in the report folder by a refused archive
    assert not list((tmp_path / "out").glob("*.html"))


def test_the_apps_caps_are_below_the_librarys(tmp_path: Path) -> None:
    from metacheck.datapackage import _open

    assert pk.MAX_UNPACKED_BYTES < _open.MAX_BYTES
    assert pk.MAX_FILES < _open.MAX_FILES
    assert pk.check_package_source.__kwdefaults__["max_bytes"] == pk.MAX_UNPACKED_BYTES
    assert pk.check_package_source.__kwdefaults__["max_files"] == pk.MAX_FILES


def test_a_file_that_is_not_an_archive_says_so(tmp_path: Path) -> None:
    broken = tmp_path / "broken.zip"
    broken.write_text("not a zip")
    with pytest.raises(UserError, match="cannot be read as an archive"):
        run_on(broken, tmp_path)


# -- the tables ---------------------------------------------------------------------------


def _output(title: str, light: str, summary: str, checklist: Any = None, report: str = "") -> Any:
    extras = {} if checklist is None else {"checklist": checklist}
    return SimpleNamespace(
        title=title, traffic_light=light, summary_text=summary, report=report, extras=extras
    )


def test_the_tables_name_every_status_in_words() -> None:
    items = pd.DataFrame(
        {
            "item": ["a", "b", "c", "d", "e"],
            "title": ["A", "B", "C", "D", "E"],
            "status": ["fail", "warn", "pass", "manual", "na"],
            "detail": ["bad", "so-so", "fine", "ask a person", None],
        }
    )
    outputs = [
        _output("Files", "red", "**1** problem\n- see below", items),
        _output("Codebook", "green", "documented"),
        _output("Broken", "fail", "", report="The check stopped:\n  an error"),
        _output("Quiet", "info", "nothing to say"),
    ]
    checks = pk.checks_table(outputs)
    assert checks.values.tolist() == [
        ["Files", "Fail", "1 problem see below"],
        ["Codebook", "Pass", "documented"],
        ["Broken", "Did not run", "The check stopped: an error"],
        ["Quiet", "Info", "nothing to say"],
    ]
    checklist = pk.checklist_table(outputs)
    assert checklist.values.tolist() == [
        ["Files", "A", "Fail", "bad"],
        ["Files", "B", "Warning", "so-so"],
        ["Files", "C", "Pass", "fine"],
        ["Files", "D", "Manual", "ask a person"],
        ["Files", "E", "Not applicable", ""],
        ["Codebook", "Whole check", "Pass", "documented"],
        ["Broken", "Whole check", "Did not run", "The check stopped: an error"],
        ["Quiet", "Whole check", "Info", "nothing to say"],
    ]
    assert pk.counts_line(checklist) == (
        "1 fail, 1 warning, 1 manual, 2 pass, 1 not applicable, 1 info, 1 did not run"
    )
    assert pk.counts_line(pk.checklist_table([])) == "no checklist items"


def test_a_pack_checklist_with_more_columns_still_fits() -> None:
    items = pd.DataFrame(
        {
            "part": ["Part 1"],
            "item": ["x"],
            "title": ["X"],
            "status": ["warn"],
            "detail": ["d"],
            "extra": [1],
        }
    )
    assert pk.checklist_table([_output("Pack", "yellow", "s", items)]).values.tolist() == [
        ["Pack", "X", "Warning", "d"]
    ]


def test_traffic_light_colours_follow_the_status_and_keep_the_word() -> None:
    frame = pd.DataFrame(
        {
            "Check": ["a"] * 7,
            "Status": [
                "Fail",
                "Warning",
                "Manual",
                "Pass",
                "Did not run",
                "Not applicable",
                "Info",
            ],
            "Detail": ["x"] * 7,
        }
    )
    html = ui_package.coloured(frame).to_html()
    painted: dict[int, str] = {}
    # rules with the same style share one block: "#T_x_row1_col1, #T_x_row2_col1 { ... }"
    for selectors, colour in re.findall(
        r"((?:#\w+_col\d+,?\s*)+)\{\s*background-color: ([^;]+);\s*color: inherit;", html
    ):
        for row in re.findall(r"_row(\d+)_col1", selectors):
            painted[int(row)] = colour
    assert painted == {
        0: ui.LIGHT_COLOURS["Red"][0],
        1: ui.LIGHT_COLOURS["Yellow"][0],
        2: ui.LIGHT_COLOURS["Yellow"][0],
        3: ui.LIGHT_COLOURS["Green"][0],
        4: ui.LIGHT_COLOURS["Failed"][0],
    }  # not applicable and info stay plain
    for word in frame["Status"]:
        assert f">{word}</td>" in html  # the word is always shown
    assert "col0 {" not in html and "col2 {" not in html
    # only the status column is coloured
    assert pk.status_light("Fail") == "Red" and pk.status_light("Info") is None


def test_the_tables_of_a_real_run_colour_by_status(tmp_path: Path) -> None:
    result = run_on(folder(tmp_path), tmp_path)
    html = ui_package.coloured(result.checklist).to_html()
    assert ui.LIGHT_COLOURS["Green"][0] in html
    assert ui.LIGHT_COLOURS["Yellow"][0] in html  # the README has none of its sections
    assert ui_package.coloured(result.checks).to_html().count("background-color") >= 3


# -- the page -----------------------------------------------------------------------------


def _client() -> TestClient:
    tc = TestClient(
        create_app(PORT, TOKEN), base_url=f"http://127.0.0.1:{PORT}", follow_redirects=False
    )
    assert tc.get(f"/?token={TOKEN}").status_code == 303
    assert cookie_name(PORT) in tc.cookies
    return tc


def _package_clicks(config: dict[str, Any]) -> list[dict[str, Any]]:
    """The click events that start with the package page's "The package is" choice."""
    radios = {
        c["id"]
        for c in config["components"]
        if c["type"] == "radio" and c["props"].get("label") == "The package is"
    }
    return [
        d
        for d in config["dependencies"]
        if d["targets"][0][1] == "click" and d["inputs"] and d["inputs"][0] in radios
    ]


def _join(tc: TestClient, data: list[Any], session: str) -> dict[str, Any]:
    (dep,) = _package_clicks(tc.get("/config").json())
    body = {"data": data, "fn_index": dep["id"], "session_hash": session}
    assert tc.post("/gradio_api/queue/join", json=body).status_code == 200
    text = ""
    with tc.stream("GET", "/gradio_api/queue/data", params={"session_hash": session}) as stream:
        for line in stream.iter_lines():
            text += line
            if '"process_completed"' in line:
                break
    done = next(part for part in text.split("data: ") if '"process_completed"' in part)
    return json.loads(done)  # type: ignore[no-any-return]


def test_the_local_app_has_the_package_page_and_a_link_to_the_paper_page() -> None:
    app = ui.build_app()
    pages = app.get_config_file()["pages"]
    assert [path for path, _name, _show in pages] == ["", "package"]
    with _client() as tc:
        assert tc.get("/package").status_code == 200
        assert tc.get("/").status_code == 200
        links = json.dumps(tc.get("/config").json())
        assert "Check a data package" in links and ui.MAIN_PAGE_NAME in links


def test_the_package_page_has_no_load_event() -> None:
    """A load event races with an early click: Gradio closes the event stream between the two
    and the click's answer never arrives (the CI smoke lost it in about 4 of 10 runs)."""
    config = ui.build_app().get_config_file()
    loads = [
        d for d in config["dependencies"] if any(event == "load" for _id, event in d["targets"])
    ]
    assert loads == []


def test_the_token_link_to_the_package_page_keeps_the_page() -> None:
    tc = TestClient(
        create_app(PORT + 1, TOKEN),
        base_url=f"http://127.0.0.1:{PORT + 1}",
        follow_redirects=False,
    )
    reply = tc.get(f"/package?token={TOKEN}")
    assert reply.status_code == 303 and reply.headers["location"] == "/package"
    assert tc.get("/package").status_code == 200
    assert (
        TestClient(  # without the token the page is not served
            create_app(PORT + 2, TOKEN), base_url=f"http://127.0.0.1:{PORT + 2}"
        )
        .get("/package")
        .status_code
        == 403
    )


def test_a_shared_server_has_no_package_page() -> None:
    from metacheck.app import hosted as hosting

    cfg = hosting.HostedConfig(("a" * 32,), (HOST,))
    shared = ui.build_app(hosted=cfg)
    assert [path for path, _n, _s in shared.get_config_file()["pages"]] == [""]
    dump = json.dumps(shared.get_config_file())
    assert "Check a data package" not in dump and "package's folder" not in dump
    with TestClient(
        create_hosted_app(PORT + 3, cfg), base_url=f"https://{HOST}", follow_redirects=False
    ) as tc:
        tc.get(f"/?token={'a' * 32}")
        assert tc.get("/package").status_code in (404, 405)
        assert _package_clicks(tc.get("/config").json()) == []
    assert ui.build_app().get_config_file() != shared.get_config_file()


def test_the_page_runs_a_folder_through_the_app(tmp_path: Path) -> None:
    root = folder(tmp_path)
    with _client() as tc:
        done = _join(tc, ["folder", str(root), None, "datapackage::default", False], "p1")
        assert done["success"], done
        summary, checks, checklist, download, frame = (
            done["output"]["data"][1],
            done["output"]["data"][2],
            done["output"]["data"][3],
            done["output"]["data"][4],
            done["output"]["data"][5],
        )
        assert summary.startswith("Checked **study1**")
        assert len(checks["data"]) == default_checks() and checks["headers"] == pk.CHECK_COLUMNS
        assert {row[2] for row in checklist["data"]} >= {"Pass", "Warning"}
        assert checklist["headers"] == pk.CHECKLIST_COLUMNS
        assert 'href="/report/p1package/study1_report.html"' in download
        assert "<iframe" in frame and "sandbox" in frame
        # the report downloads from the app's own folder, under a sandbox policy
        got = tc.get("/report/p1package/study1_report.html")
        assert got.status_code == 200
        assert got.headers["content-security-policy"] == "sandbox"
        assert "Content-Security-Policy" in got.text
        # another session cannot read it
        assert tc.get("/report/p2package/study1_report.html").status_code == 404


def test_the_page_says_what_is_wrong_in_plain_words(tmp_path: Path) -> None:
    with _client() as tc:
        for data, message in [
            (["folder", "", None, "datapackage::default", False], pk.NO_SOURCE_FOLDER),
            (["folder", str(tmp_path / "x"), None, "datapackage::default", False], pk.NOT_A_FOLDER),
            (["zip", "", None, "datapackage::default", False], pk.NO_SOURCE_ZIP),
        ]:
            done = _join(tc, data, "p3")
            assert not done["success"] and message in json.dumps(done)


def test_the_page_refuses_a_folder_outside_home_in_plain_words(
    elsewhere: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = ["folder", str(elsewhere), None, "datapackage::default", False]
    with _client() as tc:
        done = _join(tc, data, "p4")
        assert not done["success"]
        text = json.dumps(done)
        assert "outside the places this page may read" in text
        assert "METACHECK_APP_ROOTS" in text
        assert "Private" not in text  # nothing of the folder came back
        # the folder is read once the variable allows it
        monkeypatch.setenv("METACHECK_APP_ROOTS", str(elsewhere.parent))
        assert _join(tc, data, "p5")["success"]


def _checkbox(config: dict[str, Any]) -> dict[str, Any]:
    (box,) = [
        c["props"]
        for c in config["components"]
        if c["type"] == "checkbox" and c["props"].get("label") == ui_package.CLASSIFIER_LABEL
    ]
    return box  # type: ignore[no-any-return]


def test_the_classifier_box_is_ticked_and_says_when_the_extra_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = _checkbox(ui.build_app().get_config_file())  # the fixture: no extra
    assert missing["value"] is True  # the classifier is the default, as in `metacheck package`
    assert missing["info"] == ui_package.CLASSIFIER_MISSING_INFO
    assert 'pip install "metacheck[concepts]"' in missing["info"]
    monkeypatch.setattr("metacheck.datacheck.concepts.classifier_available", lambda: True)
    there = _checkbox(ui.build_app().get_config_file())
    assert there["value"] is True and there["info"] == ui_package.CLASSIFIER_INFO
    assert "840 MB" in there["info"]


def test_the_page_runs_with_the_classifier_asked_for_but_missing(tmp_path: Path) -> None:
    root = folder(tmp_path)
    with _client() as tc:
        ticked = _join(tc, ["folder", str(root), None, "datapackage::default", True], "p6")
        assert ticked["success"], ticked
        assert pk.NO_CLASSIFIER in ticked["output"]["data"][1]  # said under the results
        unticked = _join(tc, ["folder", str(root), None, "datapackage::default", False], "p7")
        assert unticked["success"] and "classifier" not in unticked["output"]["data"][1]


def test_the_two_pages_keep_their_reports_apart() -> None:
    assert ui_package.report_key("abc") != "abc"
    assert ui.Sessions.safe(ui_package.report_key("abc")) != ui.Sessions.safe("abc")


# -- starting the app on the page ---------------------------------------------------------


def test_the_link_names_the_page() -> None:
    assert launch._url(1234, "tok") == "http://127.0.0.1:1234/?token=tok"
    assert launch._url(1234, "tok", "paper") == "http://127.0.0.1:1234/?token=tok"
    assert launch._url(1234, "tok", "package") == "http://127.0.0.1:1234/package?token=tok"


def test_page_option(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    assert launch.build_parser().parse_args(["--page", "package"]).page == "package"
    assert launch.build_parser().parse_args([]).page is None
    with pytest.raises(SystemExit):
        launch.build_parser().parse_args(["--page", "nowhere"])
    assert launch.main(["--hosted", "--page", "package"]) == 2
    assert "--page only works in the local app" in capsys.readouterr().err
    # a second start reuses the running app and opens its package page
    opened: list[str] = []
    monkeypatch.setattr(launch, "_open_browser", opened.append)
    monkeypatch.setattr(
        launch.saved, "find_running", lambda: {"port": 5555, "token": "tok", "pid": 1}
    )
    assert launch.main(["--page", "package"]) == 0
    assert opened == ["http://127.0.0.1:5555/package?token=tok"]
