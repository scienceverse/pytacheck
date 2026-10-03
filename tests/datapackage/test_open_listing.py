"""Opening a data package (folder or archive) and listing its files and folders."""

from __future__ import annotations

import io
import os
import tarfile
import zipfile
from pathlib import Path

import pytest

from metacheck.datapackage import (
    PackageError,
    list_files,
    open_package,
    package_for,
    using_package,
)
from metacheck.module import module_run


def _tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


PACKAGE = {
    "README.txt": "about",
    ".DS_Store": "x",
    "4 Data/Raw data/raw.csv": "a,b\n1,2\n",
    "4 Data/Processed data/clean.csv": "a,b\n1,2\n",
    "3 Code & Script/R-Scripts/analysis.R": "x <- 1",
}


def _zip(path: Path, members: dict[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, text in members.items():
            zf.writestr(name, text)
    return path


def test_a_folder_is_used_in_place_and_hidden_files_are_listed(tmp_path: Path) -> None:
    root = _tree(tmp_path / "pkg", PACKAGE)
    with open_package(root) as pkg:
        assert pkg.root == root.resolve()
        assert pkg.base == ""
        assert not pkg.archive
        files = pkg.files()
    assert files["path"].tolist() == [
        ".DS_Store",
        "3 Code & Script/R-Scripts/analysis.R",
        "4 Data/Processed data/clean.csv",
        "4 Data/Raw data/raw.csv",
        "README.txt",
    ]
    row = files.set_index("path").loc["4 Data/Raw data/raw.csv"]
    assert (row["top"], row["depth"], row["ext"], row["data_type"]) == ("4 Data", 2, "csv", "data")
    assert files.set_index("path").loc[".DS_Store", "hidden"]
    assert files.set_index("path").loc["README.txt", "doc_role"] == "readme"


def test_dirs_count_depth_and_files(tmp_path: Path) -> None:
    root = _tree(tmp_path / "pkg", PACKAGE)
    (root / "empty").mkdir()
    with open_package(root) as pkg:
        dirs = pkg.dirs().set_index("path")
    assert dirs.loc["4 Data", "depth"] == 1
    assert dirs.loc["4 Data/Raw data", "depth"] == 2
    assert dirs.loc["4 Data", "n_files"] == 0
    assert dirs.loc["4 Data", "n_files_total"] == 2
    assert dirs.loc["empty", "empty"]
    assert not dirs.loc["4 Data", "empty"]


def test_a_zip_with_a_wrapper_folder_is_counted_from_inside_it(tmp_path: Path) -> None:
    members = {f"mypkg/{k}": v for k, v in PACKAGE.items()}
    members["__MACOSX/mypkg/._README.txt"] = "x"
    archive = _zip(tmp_path / "mypkg.zip", members)
    with open_package(archive) as pkg:
        root = pkg.root
        assert pkg.archive
        assert pkg.base == "mypkg"
        assert pkg.name == "mypkg"
        files = pkg.files().set_index("path")
        assert files.loc["mypkg/4 Data/Raw data/raw.csv", "depth"] == 2
        assert files.loc["mypkg/4 Data/Raw data/raw.csv", "rel"] == "4 Data/Raw data/raw.csv"
        # files next to the wrapper are still listed, counted from the root
        assert not files.loc["__MACOSX/mypkg/._README.txt", "in_base"]
        assert root.exists()
    assert not root.exists()  # the extracted copy is removed afterwards


def test_a_single_component_folder_is_not_a_wrapper(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "x.zip", {"data/a.csv": "a\n1\n"})
    with open_package(archive) as pkg:
        assert pkg.base == ""
        assert pkg.files()["depth"].tolist() == [1]


def test_unsafe_members_are_skipped(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "x.zip", {"ok.txt": "x", "../evil.txt": "x", "/abs.txt": "x"})
    with open_package(archive) as pkg:
        assert set(pkg.skipped) == {"../evil.txt", "/abs.txt"}
        assert pkg.files()["path"].tolist() == ["ok.txt"]
    assert not (tmp_path / "evil.txt").exists()


def test_a_tar_gz_is_extracted(tmp_path: Path) -> None:
    archive = tmp_path / "pkg.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        for name, text in {"pkg/README.md": "x", "pkg/data/a.csv": "a\n1\n"}.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("pkg/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        tf.addfile(link)
    with open_package(archive) as pkg:
        assert pkg.base == "pkg"
        assert pkg.name == "pkg"
        assert pkg.skipped == ("pkg/link",)
        assert sorted(pkg.files()["rel"]) == ["README.md", "data/a.csv"]


def test_limits_and_bad_paths(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "x.zip", {f"f{i}.txt": "x" for i in range(5)})
    with pytest.raises(PackageError, match="more than 3 files"), open_package(archive, max_files=3):
        pass
    with pytest.raises(PackageError, match="bigger than"), open_package(archive, max_bytes=2):
        pass
    with pytest.raises(PackageError, match="No folder or archive"), open_package(tmp_path / "nope"):
        pass
    (tmp_path / "paper.pdf").write_text("x")
    with pytest.raises(PackageError, match="neither a folder nor an archive"):
        with open_package(tmp_path / "paper.pdf"):
            pass
    (tmp_path / "broken.zip").write_text("not a zip")
    with pytest.raises(PackageError, match="cannot be read"), open_package(tmp_path / "broken.zip"):
        pass


def test_package_for_shares_the_opened_package(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "mypkg.zip", {f"mypkg/{k}": v for k, v in PACKAGE.items()})
    assert package_for(None) is None
    with open_package(archive) as pkg, using_package(pkg):
        assert package_for(str(pkg.base_dir)) is pkg
        assert package_for([str(pkg.base_dir)]) is pkg
    # on its own, a check opens the archive itself (once)
    first = package_for(archive)
    assert first is not None
    assert first.base == "mypkg"
    assert package_for(archive) is first
    with pytest.raises(PackageError, match="one data package"):
        package_for([archive, archive])


def test_listing_an_empty_folder(tmp_path: Path) -> None:
    files = list_files(tmp_path)
    assert len(files) == 0
    assert "depth" in files.columns


def test_the_datapackage_pack_is_always_there(tmp_path: Path) -> None:
    out = module_run(None, "datapackage::package_files")
    assert out.traffic_light == "na"
    root = _tree(tmp_path / "pkg", PACKAGE)
    out = module_run(None, "datapackage::package_files", local_path=str(root))
    assert out.traffic_light != "fail"


def test_findings_and_checklist_tables() -> None:
    from metacheck.datapackage import (
        checklist_frame,
        findings_frame,
        status_from_findings,
        traffic_light,
    )

    f = findings_frame(
        [
            {
                "path": "a b.csv",
                "kind": "file",
                "check": "file_names",
                "rule": "spaces",
                "severity": "suggestion",
                "detail": "has a space",
            },
            {
                "path": ".DS_Store",
                "kind": "file",
                "check": "junk_files",
                "rule": "macos",
                "severity": "problem",
                "detail": "macOS folder settings",
            },
        ]
    )
    assert status_from_findings(f, "junk_files") == "fail"
    assert status_from_findings(f, "file_names") == "warn"
    assert status_from_findings(f, "folder_depth") == "pass"
    assert traffic_light(checklist_frame([{"item": "x", "status": "pass"}])) == "green"
    assert traffic_light(checklist_frame([{"item": "x", "status": "manual"}])) == "yellow"
    assert traffic_light(checklist_frame([{"item": "x", "status": "fail"}])) == "red"
    assert traffic_light(checklist_frame()) == "na"
    with pytest.raises(ValueError, match="severity"):
        findings_frame([{"severity": "bad"}])


def test_a_folder_given_as_dot_is_named_after_the_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _tree(tmp_path / "study1", PACKAGE)
    monkeypatch.chdir(root)
    with open_package(".") as pkg:
        assert pkg.name == "study1"


@pytest.mark.skipif(os.name == "nt", reason="Windows file names are always Unicode")
def test_a_name_that_is_not_utf8_is_listed(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "ok.csv").write_text("a\n1\n")
    with open(os.path.join(os.fsencode(root), b"caf\xe9.csv"), "w") as fh:
        fh.write("a\n1\n")
    files = list_files(root)
    assert len(files) == 2
    assert set(files["data_type"]) == {"data"}
