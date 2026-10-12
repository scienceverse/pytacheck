"""Files a module hands back: the ``files`` result, ``module_files`` and ``write_module_files``."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from metacheck.module import (
    ModuleError,
    ModuleFileError,
    check_file_name,
    check_files_directory,
    module,
    module_files,
    module_run,
    run_session,
    write_module_files,
)


@module(title="Maker", description="Hands back a text file and a binary one")
def maker(paper: Any, text: str = "hello\n") -> dict[str, Any]:
    return {
        "summary_text": "made",
        "files": {"notes.md": text, "blob.bin": b"\x00\x01\xff"},
    }


@module(title="Other maker", description="Hands back a file with the same name")
def other_maker(paper: Any) -> dict[str, Any]:
    return {"summary_text": "made", "files": {"notes.md": "from the other one"}}


@module(title="Bad name", description="Hands back a file in a folder")
def bad_name(paper: Any) -> dict[str, Any]:
    return {"files": {"../escape.txt": "x"}}


@module(title="Bad content", description="Hands back something that is not text")
def bad_content(paper: Any) -> dict[str, Any]:
    return {"files": {"a.txt": 3}}


@module(title="A table named files", description="Its own `files` result is a table")
def table_named_files(paper: Any) -> dict[str, Any]:
    import pandas as pd

    return {"files": pd.DataFrame({"name": ["a"]}), "summary_text": "fine"}


@module(title="Nothing", description="Hands back no file")
def nothing(paper: Any) -> dict[str, Any]:
    return {"summary_text": "no files"}


# -- the result of a module -------------------------------------------------------------


def test_a_module_hands_back_files(paper: Any) -> None:
    out = module_run(paper, maker)
    assert out["files"] == {"notes.md": "hello\n", "blob.bin": b"\x00\x01\xff"}
    assert out.files == out["files"]
    assert module_files([out]) == {"notes.md": "hello\n", "blob.bin": b"\x00\x01\xff"}


def test_files_survive_the_session_memo_and_cannot_change_it(paper: Any) -> None:
    with run_session():
        first = module_run(paper, maker)
        first["files"]["notes.md"] = "changed by the caller"
        again = module_run(paper, maker)
    assert again["files"]["notes.md"] == "hello\n"


def test_a_bad_name_or_content_fails_that_module(paper: Any) -> None:
    with pytest.raises(ModuleError, match="no folders"):
        module_run(paper, bad_name)
    with pytest.raises(ModuleError, match="str or bytes"):
        module_run(paper, bad_content)


def test_a_files_result_that_is_not_a_dict_is_left_alone(paper: Any) -> None:
    out = module_run(paper, table_named_files)
    assert len(out["files"]) == 1
    assert module_files([out]) == {}


def test_outputs_without_files_give_an_empty_dict(paper: Any) -> None:
    assert module_files([module_run(paper, nothing)]) == {}
    assert module_files([]) == {}


def test_the_same_name_from_two_modules_is_not_lost(paper: Any) -> None:
    outputs = [module_run(paper, maker), module_run(paper, other_maker)]
    files = module_files(outputs)
    assert files["notes.md"] == "hello\n"
    assert files["other_maker_notes.md"] == "from the other one"
    assert set(files) == {"notes.md", "blob.bin", "other_maker_notes.md"}
    # a mapping of outputs (the outputs of a report) works as well
    assert module_files({"a": outputs[0], "b": outputs[1]}) == files


# -- names ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "README.md",
        "notes (2).txt",
        "no_extension",
        ".hidden",
        "ünïcode.md",
        "a" * 255,
        "..data",
    ],
)
def test_plain_names_are_accepted(name: str) -> None:
    assert check_file_name(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "",
        ".",
        "..",
        "a/b.txt",
        "../b.txt",
        "/etc/passwd",
        "a\\b.txt",
        "C:name.txt",
        "line\nbreak.txt",
        "nul\x00.txt",
        "tab\t.txt",
        "a" * 256,
        "lone\udc80surrogate",
        None,
        3,
        b"bytes.txt",
    ],
)
def test_names_that_are_not_plain_are_refused(name: Any) -> None:
    with pytest.raises(ModuleFileError):
        check_file_name(name)


def test_names_that_differ_only_in_case_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ModuleFileError, match="differ only in case"):
        write_module_files({"README.md": "a", "readme.md": "b"}, tmp_path / "out")
    assert not (tmp_path / "out").exists()


# -- writing ----------------------------------------------------------------------------


def test_files_are_written_exactly_as_given(tmp_path: Path) -> None:
    target = tmp_path / "new" / "deeper"
    text = "line one\r\nline two\n\n"
    paths = write_module_files({"a.md": text, "b.bin": b"\x00\xff"}, target)
    assert sorted(p.name for p in paths) == ["a.md", "b.bin"]
    assert (target / "a.md").read_bytes() == text.encode("utf-8")  # no line-end translation
    assert (target / "b.bin").read_bytes() == b"\x00\xff"


def test_an_existing_file_stops_the_whole_write(tmp_path: Path) -> None:
    target = tmp_path / "out"
    target.mkdir()
    (target / "a.md").write_text("mine", encoding="utf-8")
    with pytest.raises(ModuleFileError, match="nothing was written"):
        write_module_files({"b.md": "new", "a.md": "new"}, target)
    assert (target / "a.md").read_text(encoding="utf-8") == "mine"
    assert not (target / "b.md").exists()  # not even the file that was free


def test_force_replaces_files_and_keeps_their_permissions(tmp_path: Path) -> None:
    target = tmp_path / "out"
    target.mkdir()
    old = target / "a.md"
    old.write_text("mine", encoding="utf-8")
    old.chmod(0o640)
    write_module_files({"a.md": "new", "b.md": "second"}, target, force=True)
    assert old.read_text(encoding="utf-8") == "new"
    assert (target / "b.md").read_text(encoding="utf-8") == "second"
    if os.name == "posix":
        assert old.stat().st_mode & 0o777 == 0o640
    assert [p.name for p in target.iterdir() if p.name.startswith(".metacheck-")] == []


def test_force_never_replaces_a_folder(tmp_path: Path) -> None:
    target = tmp_path / "out"
    (target / "a.md").mkdir(parents=True)
    with pytest.raises(ModuleFileError, match="is a folder"):
        write_module_files({"a.md": "x"}, target, force=True)
    assert (target / "a.md").is_dir()


@pytest.mark.skipif(os.name != "posix", reason="symbolic links")
def test_force_replaces_a_link_not_what_it_points_to(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me", encoding="utf-8")
    target = tmp_path / "out"
    target.mkdir()
    (target / "a.md").symlink_to(outside)
    with pytest.raises(ModuleFileError, match="already exist"):
        write_module_files({"a.md": "x"}, target)  # a link counts as existing
    write_module_files({"a.md": "x"}, target, force=True)
    assert outside.read_text(encoding="utf-8") == "keep me"
    assert (target / "a.md").read_text(encoding="utf-8") == "x"
    assert not (target / "a.md").is_symlink()


def test_a_name_with_a_folder_writes_nothing(tmp_path: Path) -> None:
    target = tmp_path / "out"
    with pytest.raises(ModuleFileError):
        write_module_files({"ok.md": "x", "../escape.md": "y"}, target)
    assert not target.exists()
    assert not (tmp_path / "escape.md").exists()


def test_a_folder_that_is_a_file_is_refused(tmp_path: Path) -> None:
    (tmp_path / "taken").write_text("a file", encoding="utf-8")
    with pytest.raises(ModuleFileError, match="not a folder"):
        write_module_files({"a.md": "x"}, tmp_path / "taken")


def test_the_protected_folder_and_everything_in_it_is_refused(tmp_path: Path) -> None:
    package = tmp_path / "package"
    (package / "data").mkdir(parents=True)
    for where in (package, package / "data", package / "new", package / "data" / ".." / "x"):
        with pytest.raises(ModuleFileError, match="never changed"):
            write_module_files({"a.md": "x"}, where, protect=[package])
    assert sorted(p.name for p in package.iterdir()) == ["data"]
    # next to it is fine, and so is a folder whose name starts the same
    write_module_files({"a.md": "x"}, tmp_path / "package-drafts", protect=[package])
    assert (tmp_path / "package-drafts" / "a.md").exists()


@pytest.mark.skipif(os.name != "posix", reason="symbolic links")
def test_a_link_into_the_protected_folder_is_refused(tmp_path: Path) -> None:
    package = tmp_path / "package"
    package.mkdir()
    (tmp_path / "sneaky").symlink_to(package, target_is_directory=True)
    with pytest.raises(ModuleFileError, match="never changed"):
        write_module_files({"a.md": "x"}, tmp_path / "sneaky", protect=[package])
    assert list(package.iterdir()) == []


def test_check_files_directory_does_not_create_anything(tmp_path: Path) -> None:
    target = tmp_path / "later"
    assert check_files_directory(target) == target
    assert not target.exists()
