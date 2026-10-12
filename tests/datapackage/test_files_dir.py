"""Files a check hands back: ``check_package(...).files`` and ``metacheck package --files-dir``.

The checks here are small modules of their own that return a ``files`` result, so the mechanism is
tested apart from any real check (the README draft is tested in ``test_readme.py``).
"""

from __future__ import annotations

import hashlib
import json
import os
import textwrap
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from metacheck.cli import main
from metacheck.datapackage import check_package, report_package
from metacheck.packs.registry import refresh

MAKER = textwrap.dedent(
    """
    from metacheck.module import module


    @module(title="Maker", description="Hands back two files", keywords=["general"])
    def maker(paper, local_path=None):
        return {
            "summary_text": "made two files",
            "files": {"notes.md": "# Notes\\r\\nline two\\n", "blob.bin": b"\\x00\\x01\\xff"},
        }
    """
)

SECOND = textwrap.dedent(
    """
    from metacheck.module import module


    @module(title="Second", description="Hands back a file of the same name", keywords=["general"])
    def second(paper, local_path=None):
        return {"summary_text": "second", "files": {"notes.md": "from the second"}}
    """
)

NOTHING = textwrap.dedent(
    """
    from metacheck.module import module


    @module(title="Nothing", description="Hands back no file", keywords=["general"])
    def nothing(paper, local_path=None):
        return {"summary_text": "no files"}
    """
)

BAD = textwrap.dedent(
    """
    from metacheck.module import module


    @module(title="Bad", description="Hands back a file in a folder", keywords=["general"])
    def bad(paper, local_path=None):
        return {"summary_text": "bad", "files": {"../escape.md": "x"}}
    """
)

PACKAGE = {"README.md": "# Study\n\nData.\n", "data/survey.csv": "id,age\n1,23\n2,31\n"}


@pytest.fixture(autouse=True)
def _hermetic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PYTACHECK_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("PYTACHECK_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("PYTACHECK_PRESET", raising=False)
    monkeypatch.chdir(tmp_path)
    refresh()
    yield
    refresh()


def _mod(tmp_path: Path, name: str, source: str) -> str:
    path = tmp_path / "mods" / f"{name}.py"
    path.parent.mkdir(exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return str(path)


def _folder(tmp_path: Path, name: str = "pkg") -> Path:
    root = tmp_path / name
    for rel, text in PACKAGE.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def _command(root: Path, *modules: str, extra: tuple[str, ...] = ()) -> list[str]:
    """``package ROOT -m MODULE ... EXTRA``."""
    args = ["package", str(root)]
    for module in modules:
        args += ["-m", module]
    return [*args, *extra]


def _snapshot(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): (
            hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "<folder>"
        )
        for p in sorted(root.rglob("*"))
    }


# -- Python -----------------------------------------------------------------------------


def test_the_chain_and_the_report_collect_the_files(tmp_path: Path) -> None:
    maker = _mod(tmp_path, "maker", MAKER)
    root = _folder(tmp_path)
    chain = check_package(root, modules=[maker])
    assert chain.files == {"notes.md": "# Notes\r\nline two\n", "blob.bin": b"\x00\x01\xff"}
    assert chain[0]["files"] == chain.files
    report = report_package(root, tmp_path / "r.md", "md", modules=[maker])
    assert report.files == chain.files


def test_a_chain_without_files_has_none(tmp_path: Path) -> None:
    chain = check_package(_folder(tmp_path), modules=[_mod(tmp_path, "nothing", NOTHING)])
    assert chain.files == {}


def test_two_checks_with_the_same_name_both_keep_their_file(tmp_path: Path) -> None:
    chain = check_package(
        _folder(tmp_path),
        modules=[_mod(tmp_path, "maker", MAKER), _mod(tmp_path, "second", SECOND)],
    )
    assert chain.files["notes.md"] == "# Notes\r\nline two\n"
    assert chain.files["second_notes.md"] == "from the second"


def test_a_check_with_a_bad_file_name_fails_alone(tmp_path: Path) -> None:
    with pytest.warns(UserWarning, match="Error in bad"):
        chain = check_package(
            _folder(tmp_path),
            modules=[_mod(tmp_path, "bad", BAD), _mod(tmp_path, "maker", MAKER)],
        )
    assert chain[0].traffic_light == "fail"
    assert "no folders" in str(chain[0].report)
    assert set(chain.files) == {"notes.md", "blob.bin"}  # the other check still ran


# -- the command --------------------------------------------------------------------------


def test_files_are_written_exactly_as_given(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _folder(tmp_path)
    before = _snapshot(root)
    target = tmp_path / "out" / "deeper"
    maker = _mod(tmp_path, "maker", MAKER)
    assert main(["package", str(root), "-m", maker, "--files-dir", str(target)]) == 0
    assert (target / "notes.md").read_bytes() == b"# Notes\r\nline two\n"  # no line-end change
    assert (target / "blob.bin").read_bytes() == b"\x00\x01\xff"
    err = capsys.readouterr().err
    assert str(target / "notes.md") in err and str(target / "blob.bin") in err
    assert _snapshot(root) == before


def test_without_files_dir_nothing_is_written(tmp_path: Path) -> None:
    root = _folder(tmp_path)
    assert main(["package", str(root), "-m", _mod(tmp_path, "maker", MAKER)]) == 0
    assert not (tmp_path / "notes.md").exists() and not (root / "notes.md").exists()


def test_the_package_folder_and_what_is_inside_it_are_refused_before_anything_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _folder(tmp_path)
    before = _snapshot(root)
    maker = _mod(tmp_path, "maker", MAKER)
    for where in (root, root / "data", root / "new_folder", root / "data" / ".." / "x"):
        code = main(["package", str(root), "-m", maker, "--files-dir", str(where)])
        assert code == 2, where
        err = capsys.readouterr().err
        assert "never changed" in err
        assert "Maker" not in err  # it stopped before the check ran
    assert _snapshot(root) == before


@pytest.mark.skipif(os.name != "posix", reason="symbolic links")
def test_a_link_into_the_package_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _folder(tmp_path)
    (tmp_path / "sneaky").symlink_to(root, target_is_directory=True)
    code = main(
        ["package", str(root), "-m", _mod(tmp_path, "maker", MAKER), "--files-dir", "sneaky"]
    )
    assert code == 2
    assert "never changed" in capsys.readouterr().err
    assert sorted(p.name for p in root.iterdir()) == ["README.md", "data"]


def test_an_existing_file_stops_the_whole_write_unless_forced(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _folder(tmp_path)
    maker = _mod(tmp_path, "maker", MAKER)
    target = tmp_path / "out"
    target.mkdir()
    (target / "notes.md").write_text("mine", encoding="utf-8")
    assert main(["package", str(root), "-m", maker, "--files-dir", str(target)]) == 2
    err = capsys.readouterr().err
    assert "notes.md already exists" in err and "--force" in err
    assert (target / "notes.md").read_text(encoding="utf-8") == "mine"
    assert not (target / "blob.bin").exists()  # not even the file that was free
    assert main(["package", str(root), "-m", maker, "--files-dir", str(target), "--force"]) == 0
    assert (target / "notes.md").read_bytes() == b"# Notes\r\nline two\n"
    assert (target / "blob.bin").exists()


def test_force_does_not_replace_a_folder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _folder(tmp_path)
    target = tmp_path / "out"
    (target / "notes.md").mkdir(parents=True)
    maker = _mod(tmp_path, "maker", MAKER)
    code = main(_command(root, maker, extra=("--files-dir", str(target), "--force")))
    assert code == 2
    assert "is a folder" in capsys.readouterr().err
    assert (target / "notes.md").is_dir()


def test_force_needs_files_dir(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    maker = _mod(tmp_path, "maker", MAKER)
    code = main(_command(_folder(tmp_path), maker, extra=("--force",)))
    assert code == 2
    assert "--files-dir" in capsys.readouterr().err


def test_a_files_dir_that_is_a_file_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "taken").write_text("a file", encoding="utf-8")
    maker = _mod(tmp_path, "maker", MAKER)
    code = main(_command(_folder(tmp_path), maker, extra=("--files-dir", str(tmp_path / "taken"))))
    assert code == 2
    assert "not a folder" in capsys.readouterr().err


def test_checks_that_hand_back_nothing_leave_no_folder_behind(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "out"
    nothing = _mod(tmp_path, "nothing", NOTHING)
    code = main(_command(_folder(tmp_path), nothing, extra=("--files-dir", str(target))))
    assert code == 0
    assert "nothing was written" in capsys.readouterr().err
    assert not target.exists()


def test_a_report_and_json_and_files_dir_together(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _folder(tmp_path)
    target = tmp_path / "out"
    maker = _mod(tmp_path, "maker", MAKER)
    extra = ("--json", "-o", str(tmp_path / "r.html"), "--files-dir", str(target))
    code = main(_command(root, maker, extra=extra))
    assert code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)  # standard output is only the JSON
    assert payload[0]["module"].endswith("maker.py")
    assert (target / "notes.md").exists() and (tmp_path / "r.html").exists()


def test_an_archive_works_and_nothing_goes_next_to_it(tmp_path: Path) -> None:
    archive = tmp_path / "study.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("study/data/survey.csv", PACKAGE["data/survey.csv"])
    target = tmp_path / "out"
    code = main(
        ["package", str(archive), "-m", _mod(tmp_path, "maker", MAKER), "--files-dir", str(target)]
    )
    assert code == 0
    assert sorted(p.name for p in target.iterdir()) == ["blob.bin", "notes.md"]
    assert not (tmp_path / "notes.md").exists()


def test_two_checks_with_the_same_file_name_both_get_written(tmp_path: Path) -> None:
    maker, second = _mod(tmp_path, "maker", MAKER), _mod(tmp_path, "second", SECOND)
    code = main(
        _command(_folder(tmp_path), maker, second, extra=("--files-dir", str(tmp_path / "out")))
    )
    assert code == 0
    assert (tmp_path / "out" / "notes.md").read_bytes() == b"# Notes\r\nline two\n"
    assert (tmp_path / "out" / "second_notes.md").read_text(encoding="utf-8") == "from the second"


def test_a_check_that_fails_over_its_file_names_is_exit_1_and_writes_the_rest(
    tmp_path: Path,
) -> None:
    bad, maker = _mod(tmp_path, "bad", BAD), _mod(tmp_path, "maker", MAKER)
    code = main(
        _command(_folder(tmp_path), bad, maker, extra=("--files-dir", str(tmp_path / "out")))
    )
    assert code == 1
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == ["blob.bin", "notes.md"]
    assert not (tmp_path / "escape.md").exists()


def test_the_help_names_the_flags(capsys: Any) -> None:
    with pytest.raises(SystemExit):
        main(["package", "--help"])
    out = capsys.readouterr().out
    assert "--files-dir DIR" in out and "--force" in out
