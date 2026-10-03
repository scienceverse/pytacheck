"""The file checks of a data package: junk, file formats, file and folder names, naming convention."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from metacheck.datapackage import OpenedPackage, open_package
from metacheck.datapackage.files import (
    CHECK_TITLES,
    DEFAULT_SEVERITY,
    check_files,
    convention_findings,
    files_checklist,
    files_report,
    files_summary_text,
    format_findings,
    junk_findings,
    load_formats,
    load_junk,
    load_limits,
    name_findings,
    pdfa_part,
    scan_junk,
)
from metacheck.datapackage.modules.package_files import package_files
from metacheck.fileinfo.naming import check_file_naming
from metacheck.module import module_run


def _tree(root: Path, files: dict[str, str | bytes]) -> OpenedPackage:
    """Make the files under *root* and open it as a package."""
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        p = root / rel
        if rel.endswith("/"):
            p.mkdir(parents=True, exist_ok=True)
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            p.write_bytes(content)
        else:
            p.write_text(content)
    return OpenedPackage(source=str(root), root=root.resolve())


def _rows(findings: Any) -> set[tuple[str, str]]:
    return set(zip(findings["path"], findings["rule"], strict=True))


def _by_path(findings: Any) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for path, rule in zip(findings["path"], findings["rule"], strict=True):
        out.setdefault(path, []).append(rule)
    return out


# -- junk ------------------------------------------------------------------------


JUNK_FILES = {
    ".DS_Store": "macos-metadata",
    "Icon\r": "macos-metadata",
    "._notes.txt": "macos-resource-fork",
    "Thumbs.db": "windows-metadata",
    "ehthumbs.db": "windows-metadata",
    "desktop.ini": "windows-metadata",
    "~$report.docx": "office-lock",
    ".~lock.table.ods#": "office-lock",
    "notes.txt~": "editor-backup",
    "table.bak": "editor-backup",
    ".analysis.R.swp": "editor-backup",
    ".analysis.R.swo": "editor-backup",
    "scratch.tmp": "temporary-file",
    "scratch.temp": "temporary-file",
    ".Rhistory": "r-session",
    "helpers.pyc": "python-cache",
    "THUMBS.DB": "windows-metadata",
    "Scratch.TMP": "temporary-file",
}

JUNK_FOLDERS = {
    "__MACOSX": "macos-resource-fork",
    ".Spotlight-V100": "macos-metadata",
    ".fseventsd": "macos-metadata",
    ".TemporaryItems": "macos-metadata",
    ".Trashes": "macos-metadata",
    "$RECYCLE.BIN": "windows-metadata",
    ".Rproj.user": "r-session",
    "__pycache__": "python-cache",
    ".ipynb_checkpoints": "jupyter-checkpoints",
    ".Trash-1000": "linux-trash",
}

KEPT_FOLDERS = {
    ".git": "version-control",
    ".svn": "version-control",
    ".hg": "version-control",
    ".idea": "editor-settings",
    ".vscode": "editor-settings",
}


@pytest.mark.parametrize(("name", "rule"), list(JUNK_FILES.items()))
def test_junk_files_are_found(tmp_path: Path, name: str, rule: str) -> None:
    pkg = _tree(tmp_path / "pkg", {"README.txt": "x", f"sub/{name}": "x"})
    f = junk_findings(pkg)
    assert f["path"].tolist() == [f"sub/{name}"]
    assert (f["kind"][0], f["check"][0], f["rule"][0], f["severity"][0]) == (
        "file",
        "junk_files",
        rule,
        "problem",
    )
    assert f["detail"][0]


@pytest.mark.parametrize(("name", "rule"), list(JUNK_FOLDERS.items()))
def test_junk_folders_are_found_once(tmp_path: Path, name: str, rule: str) -> None:
    pkg = _tree(
        tmp_path / "pkg",
        {"README.txt": "x", f"{name}/a.txt": "x", f"{name}/deeper/b.txt": "x", f"{name}/c/": ""},
    )
    f = junk_findings(pkg)
    assert f["path"].tolist() == [name]
    assert (f["kind"][0], f["rule"][0], f["severity"][0]) == ("folder", rule, "problem")
    assert "holds 2 files" in f["detail"][0]


@pytest.mark.parametrize(("name", "rule"), list(KEPT_FOLDERS.items()))
def test_version_control_and_editor_folders_are_suggestions(
    tmp_path: Path, name: str, rule: str
) -> None:
    pkg = _tree(tmp_path / "pkg", {"README.txt": "x", f"{name}/config": "x"})
    f = junk_findings(pkg)
    assert f["path"].tolist() == [name]
    assert (f["rule"][0], f["severity"][0]) == (rule, "suggestion")


def test_names_that_only_look_like_junk_are_not_flagged(tmp_path: Path) -> None:
    pkg = _tree(
        tmp_path / "pkg",
        {
            "template.txt": "x",
            "tmp_notes.txt": "x",
            "Thumbs.db.csv": "x",
            "backup.bak.csv": "x",
            "git/readme.md": "x",
            "my.git/readme.md": "x",
            "pycache/a.py": "x",
            ".gitignore": "x",
            "a_~b.txt": "x",
        },
    )
    assert len(junk_findings(pkg)) == 0


def test_junk_inside_a_junk_folder_is_not_reported_again(tmp_path: Path) -> None:
    pkg = _tree(
        tmp_path / "pkg",
        {
            ".git/objects/a.tmp": "x",
            ".git/__pycache__/m.pyc": "x",
            ".git/.DS_Store": "x",
            "code/__pycache__/m.pyc": "x",
            "code/run.py": "x",
        },
    )
    assert junk_findings(pkg)["path"].tolist() == [".git", "code/__pycache__"]


def test_junk_is_left_out_of_the_other_checks(tmp_path: Path) -> None:
    pkg = _tree(
        tmp_path / "pkg",
        {
            "~$my report.docx": "x",
            "__MACOSX/._my data.docx": "x",
            ".git/objects/pack 1.xyz": "x",
            "my data.docx": "x",
            "run 1.py": "x",
            "results 1.xyz": "x",
        },
    )
    scan = scan_junk(pkg)
    assert scan.findings["path"].tolist() == [".git", "__MACOSX", "~$my report.docx"]
    assert sorted(scan.files["rel"]) == ["my data.docx", "results 1.xyz", "run 1.py"]
    assert scan.dirs.empty
    assert (scan.n_files, scan.n_dirs) == (6, 3)
    findings, _ = check_files(pkg)
    others = findings[findings["check"] != "junk_files"]
    assert set(others["path"]) == {"my data.docx", "results 1.xyz", "run 1.py"}


def test_junk_next_to_the_wrapper_folder_of_an_archive_is_found(tmp_path: Path) -> None:
    archive = tmp_path / "mypkg.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("mypkg/README.txt", "x")
        zf.writestr("mypkg/data/a.csv", "a\n1\n")
        zf.writestr("mypkg/data/.DS_Store", "x")
        zf.writestr("__MACOSX/mypkg/._README.txt", "x")
        zf.writestr("__MACOSX/mypkg/data/._a.csv", "x")
        zf.writestr(".DS_Store", "x")
    with open_package(archive) as pkg:
        assert pkg.base == "mypkg"
        scan = scan_junk(pkg)
        f = scan.findings
        assert set(f["path"]) == {".DS_Store", "__MACOSX", "data/.DS_Store"}
        outside = f[f["detail"].str.contains("next to the package folder")]
        assert set(outside["path"]) == {".DS_Store", "__MACOSX"}
        assert sorted(scan.files["rel"]) == ["README.txt", "data/a.csv"]
        # the wrapper folder itself is not a folder of the package
        assert scan.dirs["rel"].tolist() == ["data"]


def test_junk_rules_from_a_list_a_dict_and_a_json_file(tmp_path: Path) -> None:
    pkg = _tree(tmp_path / "pkg", {"a.tmp": "x", "notes.private": "x", "keep/b.txt": "x"})
    rules = [
        {
            "rule": "private",
            "match": "suffix",
            "pattern": ".private",
            "explanation": "A private note.",
        }
    ]
    f = junk_findings(pkg, junk=rules)
    assert f["path"].tolist() == ["notes.private"]
    assert (f["rule"][0], f["severity"][0], f["detail"][0]) == (
        "private",
        "problem",
        "A private note.",
    )
    assert junk_findings(pkg, junk={"rules": rules})["path"].tolist() == ["notes.private"]
    path = tmp_path / "junk.json"
    path.write_text(
        json.dumps({"rules": [*rules, {"rule": "k", "match": "folder", "pattern": "keep"}]})
    )
    f = junk_findings(pkg, junk=path)
    assert f["path"].tolist() == ["keep", "notes.private"]
    assert junk_findings(pkg, junk=str(path))["rule"].tolist() == ["k", "private"]


@pytest.mark.parametrize(
    ("match", "pattern", "name", "hit"),
    [
        ("name", "x.dat", "X.DAT", True),
        ("name", "x.dat", "xx.dat", False),
        ("prefix", "tmp-", "tmp-1.csv", True),
        ("prefix", "tmp-", "a-tmp-1.csv", False),
        ("suffix", ".old", "a.OLD", True),
        ("suffix", ".old", "a.old.csv", False),
        ("glob", "run-?.log", "run-1.log", True),
        ("glob", "run-?.log", "run-10.log", False),
        ("glob", "*.cache.*", "a.cache.json", True),
    ],
)
def test_junk_match_types(tmp_path: Path, match: str, pattern: str, name: str, hit: bool) -> None:
    pkg = _tree(tmp_path / "pkg", {name: "x"})
    f = junk_findings(pkg, junk=[{"rule": "r", "match": match, "pattern": pattern}])
    assert (len(f) == 1) is hit


def test_a_junk_folder_glob_and_a_folder_name_that_is_a_file_name(tmp_path: Path) -> None:
    pkg = _tree(
        tmp_path / "pkg", {"cache-1/a.txt": "x", "cache-2/b.txt": "x", "Thumbs.db/c.txt": "x"}
    )
    f = junk_findings(pkg, junk=[{"rule": "c", "match": "folder", "pattern": "cache-*"}])
    assert f["path"].tolist() == ["cache-1", "cache-2"]


def test_severity_overrides_for_junk(tmp_path: Path) -> None:
    pkg = _tree(tmp_path / "pkg", {".DS_Store": "x", ".git/config": "x", "my file.docx": "x"})
    f = junk_findings(pkg, severity={"macos-metadata": "info", "version-control": "problem"})
    assert dict(zip(f["path"], f["severity"], strict=True)) == {
        ".DS_Store": "info",
        ".git": "problem",
    }
    # "ignore" leaves the finding out but the files still do not count as content
    f = junk_findings(pkg, severity={"version-control": "ignore"})
    assert f["path"].tolist() == [".DS_Store"]
    findings, _ = check_files(
        pkg, severity={"version-control": "ignore", "macos-metadata": "ignore"}
    )
    assert not (findings["check"] == "junk_files").any()
    assert "config" not in " ".join(findings["path"])
    with pytest.raises(ValueError, match="severity"):
        junk_findings(pkg, severity={"macos-metadata": "bad"})


def test_a_bad_junk_policy_is_refused(tmp_path: Path) -> None:
    pkg = _tree(tmp_path / "pkg", {"a.txt": "x"})
    for bad in (
        [{"rule": "r", "match": "regex", "pattern": "x"}],
        [{"rule": "r", "match": "name", "pattern": ""}],
        [{"rule": "r", "match": "name", "pattern": "x", "severity": "meh"}],
        ["not a dict"],
        "not a list",
    ):
        with pytest.raises(ValueError):
            junk_findings(pkg, junk=bad)
    with pytest.raises(ValueError, match="Cannot read"):
        junk_findings(pkg, junk=tmp_path / "missing.json")
    (tmp_path / "broken.json").write_text("{")
    with pytest.raises(ValueError, match="not valid JSON"):
        junk_findings(pkg, junk=tmp_path / "broken.json")


def test_the_default_junk_rules_are_well_formed() -> None:
    rules = load_junk()
    assert {r["rule"] for r in rules} >= set(JUNK_FILES.values()) | set(JUNK_FOLDERS.values())
    assert all(r["explanation"] for r in rules)
    assert {r["severity"] for r in rules} == {"problem", "suggestion"}


# -- file formats ----------------------------------------------------------------


def _pdf(conformance: str = "", *, element: bool = False, part: str = "2", pad: int = 0) -> bytes:
    body = b"%PDF-1.7\n" + b"0" * pad
    if part:
        if element:
            xmp = f"<pdfaid:part>{part}</pdfaid:part>\n"
            if conformance:
                xmp += f"<pdfaid:conformance>{conformance}</pdfaid:conformance>\n"
        else:
            xmp = f'<rdf:Description rdf:about="" pdfaid:part="{part}"'
            if conformance:
                xmp += f' pdfaid:conformance="{conformance}"'
            xmp += "/>\n"
        body += (
            b'<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?><x:xmpmeta xmlns:x="adobe:ns:meta/">'
            b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
            b'xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/">' + xmp.encode() + b"</rdf:RDF>"
        )
    return body + b"\n%%EOF\n"


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (_pdf("B"), "2b"),
        (_pdf("B", element=True), "2b"),
        (_pdf("A", part="1"), "1a"),
        (_pdf("U", part="3", element=True), "3u"),
        (_pdf("", part="4"), "4"),
        (_pdf("F", part="4"), "4f"),
        (_pdf("", part=""), None),
        (b"%PDF-1.4\nno metadata here\n", None),
        (b"%PDF-1.4\nthe text pdfaid:part is mentioned, but with no value\n", None),
        (b"", None),
        (b"just text\n", None),
    ],
)
def test_pdfa_part(tmp_path: Path, data: bytes, expected: str | None) -> None:
    p = tmp_path / "a.pdf"
    p.write_bytes(data)
    assert pdfa_part(p) == expected
    assert pdfa_part(str(p)) == expected


def test_pdfa_part_reads_a_big_file_in_chunks(tmp_path: Path) -> None:
    chunk = 1 << 20
    p = tmp_path / "big.pdf"
    # the XMP far into the file
    p.write_bytes(_pdf("B", pad=3 * chunk + 17))
    assert pdfa_part(p) == "2b"
    # the property name split by a chunk boundary, and its conformance after it
    for offset in range(1, len(b"pdfaid:part")):
        head = b"%PDF-1.7\n" + b"0" * (chunk - 9 - offset)
        p.write_bytes(head + b'<x pdfaid:part="3" pdfaid:conformance="A"/>' + b"0" * 100)
        assert pdfa_part(p) == "3a", offset
    with pytest.raises(OSError):
        pdfa_part(tmp_path / "missing.pdf")


def test_default_formats(tmp_path: Path) -> None:
    pkg = _tree(
        tmp_path / "pkg",
        {
            "README.txt": "x",
            "analysis.R": "x",
            "run.py": "x",
            "data/raw.csv": "x",
            "data/clean.sav": "x",
            "data/results.RDS": "x",
            "data/old.xlsx": "x",
            "doc/protocol.odt": "x",
            "doc/protocol.docx": "x",
            "doc/slides.pptx": "x",
            "doc/figure.png": "x",
            "doc/figure.eps": "x",
            "doc/notes.xyz": "x",
            "audio/a.flac": "x",
            "audio/a.mp3": "x",
            "LICENSE": "x",
            "Makefile": "x",
            "v0.6": "x",
            "notes.2020": "x",
            "Dr. Smith report": "x",
            "scans/a.pdf": _pdf("B"),
            "scans/b.pdf": b"%PDF-1.4\n",
        },
    )
    f = format_findings(pkg)
    got = dict(zip(f["path"], zip(f["rule"], f["severity"], strict=True), strict=True))
    assert got == {
        "data/clean.sav": ("non-preferred", "suggestion"),
        "data/results.RDS": ("non-preferred", "suggestion"),
        "data/old.xlsx": ("non-preferred", "suggestion"),
        "doc/protocol.docx": ("non-preferred", "suggestion"),
        "doc/slides.pptx": ("non-preferred", "suggestion"),
        "doc/figure.eps": ("non-preferred", "suggestion"),
        "doc/notes.xyz": ("unlisted", "info"),
        "audio/a.mp3": ("non-preferred", "suggestion"),
        "scans/b.pdf": ("non-preferred", "suggestion"),
    }
    assert set(f["check"]) == {"file_formats"}
    detail = dict(zip(f["path"], f["detail"], strict=True))
    assert "Save it as ODT or PDF/A" in detail["doc/protocol.docx"]
    assert "PDF/A or ODP" in detail["doc/slides.pptx"]
    assert "CSV" in detail["data/clean.sav"]
    assert "(.RDS)" in detail["data/results.RDS"]  # the extension as it is written
    assert "does not say that it is PDF/A" in detail["scans/b.pdf"]
    assert detail["scans/b.pdf"].endswith("Save it as PDF/A.")
    assert ".xyz is not on the list" in detail["doc/notes.xyz"]


def test_the_format_policy_covers_the_dans_list_and_the_extras() -> None:
    formats = load_formats()["formats"]
    preferred = [
        "pdf",
        "odt",
        "txt",
        "xml",
        "html",
        "css",
        "xslt",
        "js",
        "md",
        "ods",
        "csv",
        "sql",
        "siard",
        "dat",
        "sps",
        "do",
        "r",
        "jpg",
        "jpeg",
        "tif",
        "tiff",
        "png",
        "jp2",
        "dcm",
        "svg",
        "bwf",
        "mxf",
        "mka",
        "flac",
        "opus",
        "mkv",
        "dxf",
        "gml",
        "mif",
        "mid",
        "geojson",
        "gpkg",
        "asc",
        "obj",
        "ply",
        "x3d",
        "gltf",
        "glb",
        "dae",
        "las",
        "laz",
        "ifc",
        "rdf",
        "trig",
        "ttl",
        "nt",
        "jsonld",
        "json",
        "py",
        "rmd",
        "qmd",
        "m",
        "jl",
        "sas",
        "sh",
        "ipynb",
        "c",
        "cpp",
        "java",
        "stan",
    ]
    non_preferred = [
        "doc",
        "docx",
        "rtf",
        "sgml",
        "xls",
        "xlsx",
        "mdb",
        "accdb",
        "dbf",
        "hdf5",
        "he5",
        "h5",
        "por",
        "sav",
        "dta",
        "sas7bdat",
        "jasp",
        "ai",
        "eps",
        "wmf",
        "emf",
        "cdr",
        "wav",
        "mp3",
        "aac",
        "m4a",
        "aif",
        "aiff",
        "ogg",
        "mp4",
        "m4v",
        "mpg",
        "mpeg",
        "m2v",
        "avi",
        "mov",
        "qt",
        "dwg",
        "dgn",
        "shp",
        "tab",
        "kml",
        "kmz",
        "gdb",
        "mxd",
        "qgs",
        "fbx",
        "blend",
        "stl",
        "wrl",
        "ppt",
        "pptx",
        "pages",
        "numbers",
        "key",
        "mat",
        "rdata",
        "rda",
        "rds",
    ]
    assert [e for e in preferred if formats[e]["level"] != "preferred"] == []
    assert [e for e in non_preferred if formats[e]["level"] != "non-preferred"] == []
    assert all(formats[e]["alternative"] for e in non_preferred)
    assert formats["pdf"]["requires"] == "pdfa"
    assert "DANS" in load_formats()["source"]
    assert "1.3" in load_formats()["source"]


def test_formats_from_a_dict_a_shorthand_dict_and_a_json_file(tmp_path: Path) -> None:
    pkg = _tree(
        tmp_path / "pkg", {"a.csv": "x", "b.docx": "x", "c.nii.gz": "x", "d.gz": "x", "e.txt": "x"}
    )
    policy = {
        "name": "Test list",
        "source": "A test.",
        "formats": {
            "csv": {"level": "non-preferred", "label": "Comma-separated", "alternative": "ODS"},
            "docx": {"level": "preferred"},
            "nii.gz": {"level": "preferred", "label": "NIfTI"},
        },
    }
    f = format_findings(pkg, formats=policy)
    assert dict(zip(f["path"], f["rule"], strict=True)) == {
        "a.csv": "non-preferred",
        "d.gz": "unlisted",
        "e.txt": "unlisted",
    }
    assert "Save it as ODS." in f["detail"][0]
    # a dict without "formats" is the mapping itself
    shorthand = dict(policy["formats"])
    assert _rows(format_findings(pkg, formats=shorthand)) == _rows(f)
    path = tmp_path / "formats.json"
    path.write_text(json.dumps(policy))
    assert _rows(format_findings(pkg, formats=path)) == _rows(f)
    assert _rows(format_findings(pkg, formats=str(path))) == _rows(f)
    assert load_formats(path)["name"] == "Test list"
    # a policy can start from the default
    base = load_formats()["formats"]
    mine = {**base, "docx": {"level": "preferred", "label": "Word"}}
    assert "b.docx" not in format_findings(pkg, formats=mine)["path"].tolist()


def test_a_bad_formats_policy_is_refused() -> None:
    for bad in (
        {"formats": {"csv": {"level": "great"}}},
        {"formats": {"csv": "preferred"}},
        {"formats": {"pdf": {"level": "preferred", "requires": "magic"}}},
        ["csv"],
    ):
        with pytest.raises(ValueError):
            load_formats(bad)  # type: ignore[arg-type]


def test_format_severity_overrides_and_ignore(tmp_path: Path) -> None:
    pkg = _tree(tmp_path / "pkg", {"a.docx": "x", "b.xyz": "x"})
    f = format_findings(pkg, severity={"non-preferred": "problem", "unlisted": "suggestion"})
    assert dict(zip(f["path"], f["severity"], strict=True)) == {
        "a.docx": "problem",
        "b.xyz": "suggestion",
    }
    f = format_findings(pkg, severity={"unlisted": "ignore"})
    assert f["path"].tolist() == ["a.docx"]
    f = format_findings(pkg, severity={"non-preferred": "ignore", "spaces": "problem"})
    assert f["path"].tolist() == ["b.xyz"]


def test_a_pdf_is_not_read_when_the_policy_does_not_ask_for_pdfa(tmp_path: Path) -> None:
    pkg = _tree(tmp_path / "pkg", {"a.pdf": b"%PDF-1.4\n"})
    policy = {"pdf": {"level": "preferred", "label": "PDF"}}
    assert len(format_findings(pkg, formats=policy)) == 0


def test_pdfa_requirement_with_a_link_or_an_unreadable_file_is_skipped(tmp_path: Path) -> None:
    pkg = _tree(tmp_path / "pkg", {"real.pdf": _pdf("B")})
    (tmp_path / "pkg" / "link.pdf").symlink_to(tmp_path / "pkg" / "missing.pdf")
    assert len(format_findings(pkg)) == 0


# -- file and folder names -------------------------------------------------------


@pytest.mark.parametrize("char", list("~!@#$%^&*:?()[]{}+=,;'"))
def test_special_characters(tmp_path: Path, char: str) -> None:
    pkg = _tree(tmp_path / "pkg", {f"a{char}b.txt": "x", "ok.txt": "x"})
    f = name_findings(pkg)
    assert f["path"].tolist() == [f"a{char}b.txt"]
    assert (f["rule"][0], f["severity"][0], f["check"][0], f["kind"][0]) == (
        "special-characters",
        "problem",
        "file_names",
        "file",
    )
    assert char in f["detail"][0]


def test_spaces_and_diacritics(tmp_path: Path) -> None:
    pkg = _tree(
        tmp_path / "pkg",
        {
            "my file.txt": "x",
            "café.csv": "x",
            "Ångström 1.txt": "x",
            "名前.csv": "x",
            "fine_name-1.v2.csv": "x",
            ".hidden": "x",
            "UPPER.TXT": "x",
        },
    )
    got = _by_path(name_findings(pkg))
    assert got == {
        "my file.txt": ["spaces"],
        "café.csv": ["diacritics"],
        "Ångström 1.txt": ["spaces", "diacritics"],
        "名前.csv": ["diacritics"],
    }
    f = name_findings(pkg).set_index(["path", "rule"])
    assert f.loc[("my file.txt", "spaces"), "severity"] == "suggestion"
    assert f.loc[("café.csv", "diacritics"), "severity"] == "problem"


def test_a_name_can_break_several_rules(tmp_path: Path) -> None:
    pkg = _tree(tmp_path / "pkg", {"Protocols & méthodes.txt": "x"})
    assert _by_path(name_findings(pkg)) == {
        "Protocols & méthodes.txt": ["special-characters", "spaces", "diacritics"]
    }


def test_each_folder_is_reported_once(tmp_path: Path) -> None:
    files = {f"3 Code & Script/R Scripts/run{i}.R": "x" for i in range(5)}
    files["3 Code & Script/clean.R"] = "x"
    pkg = _tree(tmp_path / "pkg", files)
    f = name_findings(pkg)
    folders = f[f["kind"] == "folder"]
    assert _by_path(folders) == {
        "3 Code & Script": ["special-characters", "spaces"],
        "3 Code & Script/R Scripts": ["spaces"],
    }
    assert len(f) == 3
    assert not (f["kind"] == "file").any()


def test_the_base_folder_of_an_archive_is_not_checked(tmp_path: Path) -> None:
    archive = tmp_path / "my package (1).zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("my package (1)/README.txt", "x")
        zf.writestr("my package (1)/data/a.csv", "x")
    with open_package(archive) as pkg:
        assert pkg.base == "my package (1)"
        assert len(name_findings(pkg)) == 0


def test_name_findings_agree_with_the_r_file_naming_check(tmp_path: Path) -> None:
    names = [
        "a b.txt",
        "a&b.txt",
        "a_b.txt",
        "a-b.2.csv",
        "résumé.txt",
        "x" * 60 + ".csv",
        "(1).txt",
        "a b&c.txt",
        "data#1.csv",
    ]
    pkg = _tree(tmp_path / "pkg", dict.fromkeys(names, "x"))
    mine = _by_path(name_findings(pkg))
    r = check_file_naming(names)
    theirs: dict[str, set[str]] = {}
    same = {"spaces", "special-characters", "diacritics", "filename-length-50"}
    for name, rule in zip(r["file_name"], r["rule"], strict=True):
        if rule in same:
            theirs.setdefault(name, set()).add(rule)
    renamed = {"filename-length-50": "file-name-long"}
    for name in names:
        expected = {renamed.get(x, x) for x in theirs.get(name, set())}
        if "diacritics" in expected:  # the R check calls a non-ASCII letter special too
            expected.discard("special-characters")
        assert set(mine.get(name, [])) == expected, name


def test_length_rules_and_limits(tmp_path: Path) -> None:
    long_name = "n" * 51 + ".csv"
    deep = "/".join(["d" * 40, "e" * 40, "f" * 40])  # 122 characters
    pkg = _tree(
        tmp_path / "pkg",
        {
            long_name: "x",
            f"{deep}/a.csv": "x",
            f"{deep}/b.csv": "x",
            f"{deep}/{'q' * 50}.csv": "x",  # a path of 122 + 1 + 54 = 177
            f"{'g' * 100}/{'h' * 100}/{'i' * 30}.csv": "x",  # 232 characters: long
            f"{'g' * 100}/{'h' * 100}/{'j' * 100}.csv": "x",  # 305 characters: too long
        },
    )
    f = name_findings(pkg).set_index(["path", "rule"])
    rules = _by_path(name_findings(pkg))
    assert rules[long_name] == ["file-name-long"]
    # the folder is reported once, at the folder that first passes 100 characters
    folder_rows = [p for p, r in rules.items() if "folder-path-long" in r]
    assert folder_rows == [f"{'d' * 40}/{'e' * 40}/{'f' * 40}", "g" * 100 + "/" + "h" * 100]
    assert rules[f"{'g' * 100}/{'h' * 100}/{'i' * 30}.csv"] == ["path-long"]
    assert rules[f"{'g' * 100}/{'h' * 100}/{'j' * 100}.csv"] == ["path-too-long", "file-name-long"]
    assert (
        f.loc[(f"{'g' * 100}/{'h' * 100}/{'i' * 30}.csv", "path-long"), "severity"] == "suggestion"
    )
    assert (
        f.loc[(f"{'g' * 100}/{'h' * 100}/{'j' * 100}.csv", "path-too-long"), "severity"]
        == "problem"
    )
    assert f"{deep}/a.csv" not in rules

    # limits: a partial dict changes only what it names
    f2 = name_findings(pkg, limits={"file_name_long": 200, "folder_path_long": 1000})
    rules2 = _by_path(f2)
    assert long_name not in rules2
    assert not any("folder-path-long" in r for r in rules2.values())
    assert rules2[f"{'g' * 100}/{'h' * 100}/{'j' * 100}.csv"] == ["path-too-long"]
    path = tmp_path / "limits.json"
    path.write_text(json.dumps({"path_too_long": 500, "path_long": 400}))
    rules3 = _by_path(name_findings(pkg, limits=path))
    assert not any("path-too-long" in r or "path-long" in r for r in rules3.values())
    assert load_limits()["path_too_long"] == 255
    with pytest.raises(ValueError, match="whole number"):
        load_limits({"path_long": "many"})


def test_path_length_rules_agree_with_the_r_file_naming_check(tmp_path: Path) -> None:
    paths = [
        "a/" + "b" * 100 + ".csv",  # 106
        "a/" + "b" * 226 + ".csv",  # 232: over 228
        "a/" + "b" * 250 + ".csv",  # 256: over 255
        "a/" + "b" * 220 + ".csv",  # 226: fine
    ]
    pkg = _tree(tmp_path / "pkg", dict.fromkeys(paths, "x"))
    mine = _by_path(name_findings(pkg))
    r = check_file_naming([p.rsplit("/", 1)[1] for p in paths], file_path=paths)
    r255 = set(r.loc[r["rule"] == "path-length-255", "file_name"])
    r228 = set(r.loc[r["rule"] == "path-length-228", "file_name"]) - r255
    assert {p for p, rules in mine.items() if "path-too-long" in rules} == r255 == {paths[2]}
    assert {p for p, rules in mine.items() if "path-long" in rules} == r228 == {paths[1]}


def test_name_severity_overrides(tmp_path: Path) -> None:
    pkg = _tree(tmp_path / "pkg", {"a b.txt": "x", "a&b.txt": "x", "é.txt": "x"})
    f = name_findings(
        pkg, severity={"spaces": "problem", "special-characters": "info", "diacritics": "ignore"}
    )
    assert dict(zip(f["path"], f["severity"], strict=True)) == {
        "a b.txt": "problem",
        "a&b.txt": "info",
    }
    assert set(DEFAULT_SEVERITY) >= {"spaces", "special-characters", "diacritics"}
    # the overrides can also come from a JSON file; a rule id that no rule has is ignored
    path = tmp_path / "severity.json"
    path.write_text(json.dumps({"spaces": "problem", "no-such-rule": "info"}))
    f = name_findings(pkg, severity=path)
    assert dict(zip(f["path"], f["severity"], strict=True))["a b.txt"] == "problem"
    with pytest.raises(ValueError, match="must be a dict"):
        name_findings(pkg, severity=["spaces"])  # type: ignore[arg-type]


def test_files_outside_the_base_are_checked_too(tmp_path: Path) -> None:
    archive = tmp_path / "pkg.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("pkg/README.txt", "x")
        zf.writestr("pkg/data/a.csv", "x")
        zf.writestr("__MACOSX/pkg/._my file.csv", "x")
        zf.writestr("Thumbs.db", "x")
    with open_package(archive) as pkg:
        assert pkg.base == "pkg"
        findings, _ = check_files(pkg)
        assert _by_path(findings) == {
            "__MACOSX": ["macos-resource-fork"],
            "Thumbs.db": ["windows-metadata"],
        }
        # without junk rules they are ordinary files next to the package folder
        findings, _ = check_files(pkg, junk=[])
        assert _by_path(findings) == {
            "__MACOSX/pkg/._my file.csv": ["spaces"],
            "Thumbs.db": ["unlisted"],
        }


# -- naming convention -----------------------------------------------------------


def _conv(tmp_path: Path, names: list[str], **kwargs: Any) -> dict[str, list[str]]:
    pkg = _tree(tmp_path / "pkg", dict.fromkeys(names, "x"))
    return _by_path(convention_findings(pkg, **kwargs))


UNDERSCORE = ["raw_data.csv", "clean_data.csv", "model_fit.R", "figure_one.png", "run_all.sh"]


def test_the_style_outlier_is_flagged_with_a_suggestion(tmp_path: Path) -> None:
    pkg = _tree(tmp_path / "pkg", dict.fromkeys([*UNDERSCORE, "final-results.csv"], "x"))
    f = convention_findings(pkg)
    assert f["path"].tolist() == ["final-results.csv"]
    assert (f["rule"][0], f["severity"][0], f["check"][0]) == (
        "name-style",
        "suggestion",
        "naming_convention",
    )
    assert "hyphens (my-file)" in f["detail"][0]
    assert "5 of the 6 names" in f["detail"][0]
    assert "Suggested name: final_results.csv" in f["detail"][0]


def test_style_needs_enough_multi_word_names_and_a_dominant_style(tmp_path: Path) -> None:
    few = [*UNDERSCORE[:3], "final-results.csv"]
    assert _conv(tmp_path / "a", few) == {}
    assert _conv(tmp_path / "b", few, min_names_for_style=4) == {
        "final-results.csv": ["name-style"]
    }
    # single-word names do not count
    assert (
        _conv(tmp_path / "c", [*UNDERSCORE[:3], "final-results.csv", "README", "data", "x1"]) == {}
    )
    # no style covers half of the names
    mixed = ["a_b", "c_d", "e-f", "g-h", "i-j", "kL", "mN", "o_p-q", "r-s_t"]
    assert _conv(tmp_path / "d", mixed) == {}
    # exactly half is enough
    half = ["a_b.csv", "c_d.csv", "e_f.csv", "g-h.csv", "i-j.csv", "k-l.csv"]
    assert set(_conv(tmp_path / "e", half)) == {"g-h.csv", "i-j.csv", "k-l.csv"}


def test_style_counts_hyphens_between_fields_and_underscores_inside(tmp_path: Path) -> None:
    names = [
        "raw_data-2020-11-26-v0.2.csv",
        "R_script_analysis-20201126-v0.6-Smith.R",
        "Raw_data-20201126-v0.2-Smith.csv",
        "Processed_data-20201126-v0.3-Smith.csv",
        "figure_one-20201126.png",
    ]
    assert _conv(tmp_path / "a", names) == {}
    # names that use fewer separators fit in
    assert _conv(tmp_path / "b", [*names, "notes_final.txt", "plot-final.png", "code.R"]) == {}
    # but a camelCase name does not
    assert _conv(tmp_path / "c", [*names, "plotFinal.png"]) == {"plotFinal.png": ["name-style"]}


def test_style_is_not_fooled_by_dates_versions_and_numbers(tmp_path: Path) -> None:
    names = [
        *UNDERSCORE,
        "data_2020-11-26.csv",
        "data_v1.2.csv",
        "sample_01.csv",
        "2020-11-26.csv",
        "1-2.txt",
    ]
    assert _conv(tmp_path / "a", names) == {}
    # a hyphen in a date or version number is not a word separator
    assert _conv(tmp_path / "b", [*UNDERSCORE, "run_2020-11-26_v1-2.txt"]) == {}


def test_a_name_with_spaces_is_left_to_the_spaces_rule(tmp_path: Path) -> None:
    assert _conv(tmp_path / "a", [*UNDERSCORE, "my data file.csv"]) == {}
    # and spaces are not a style the others have to follow
    spaced = [f"my data {i}.csv" for i in range(8)]
    assert _conv(tmp_path / "b", [*spaced, "a_b.csv"]) == {}


def test_style_applies_to_folders_too(tmp_path: Path) -> None:
    pkg = _tree(
        tmp_path / "pkg",
        {
            "raw_data/a.csv": "x",
            "clean_data/b.csv": "x",
            "model_fits/c.csv": "x",
            "all_figures/d.csv": "x",
            "run_logs/e.csv": "x",
            "final-results/f.csv": "x",
        },
    )
    f = convention_findings(pkg)
    assert f["path"].tolist() == ["final-results"]
    assert f["kind"][0] == "folder"
    assert "Suggested name: final_results" in f["detail"][0]


def test_a_both_style_name_among_underscore_names_gets_a_suggestion_that_keeps_dates(
    tmp_path: Path,
) -> None:
    pkg = _tree(tmp_path / "pkg", dict.fromkeys([*UNDERSCORE, "raw_data-2020-11-26-v0.2.csv"], "x"))
    f = convention_findings(pkg)
    assert f["path"].tolist() == ["raw_data-2020-11-26-v0.2.csv"]
    assert "hyphens and underscores" in f["detail"][0]
    assert "Suggested name: raw_data_2020-11-26_v0.2.csv" in f["detail"][0]


@pytest.mark.parametrize(
    ("name", "iso"),
    [
        ("data_26-11-2020.csv", "2020-11-26"),
        ("data_26.11.2020.csv", "2020-11-26"),
        ("data_26_11_2020.csv", "2020-11-26"),
        ("data_11-26-2020.csv", "2020-11-26"),
        ("data_2020_11_26.csv", "2020-11-26"),
        ("data_2020.11.26.csv", "2020-11-26"),
        ("data_2020-1-5.csv", "2020-01-05"),
        ("data_26112020.csv", "2020-11-26"),
        ("data-01012005-final.csv", "2005-01-01"),
        ("data_150547.csv", ""),
        ("26-11-2020", "2020-11-26"),
        ("data_03-04-2020.csv", ""),
    ],
)
def test_non_iso_dates_are_flagged(tmp_path: Path, name: str, iso: str) -> None:
    pkg = _tree(tmp_path / "pkg", {name: "x"} if "." in name else {f"{name}/a.csv": "x"})
    f = convention_findings(pkg)
    assert set(f["rule"]) == {"date-format"}
    assert f["path"].tolist()[0] == name
    assert f["severity"][0] == "suggestion"
    if iso:
        assert iso in f["detail"][0]
    else:
        assert "YYYY-MM-DD" in f["detail"][0]


@pytest.mark.parametrize(
    "name",
    [
        "data_2020-11-26.csv",
        "data_20201126.csv",
        "data-20201126-v0.6.csv",
        "data_2020-11.csv",
        "data_2020.csv",
        "data_2020.1.5.csv",  # version-like
        "data_v1.2.2020.csv",
        "data_2020_13_45.csv",  # not a date
        "data_31-31-2020.csv",
        "participant_150547.csv",
        "sub-150547_task.csv",
        "id_01012005.csv",
        "data_12345678.csv",  # an ID
        "data_261120.csv",  # also valid as YYMMDD
        "data_20201126T101500.csv",
        "table_1.2.3.csv",
        "ip_10.1.2.2020.csv",
        "run_001_002.csv",
        "scan_1234.csv",
        "e1_2_3.csv",
        "Dr. Smith.csv",
    ],
)
def test_ids_versions_and_iso_dates_are_not_flagged(tmp_path: Path, name: str) -> None:
    pkg = _tree(tmp_path / "pkg", {name: "x"})
    assert "date-format" not in set(convention_findings(pkg)["rule"])


def test_dates_in_folder_names(tmp_path: Path) -> None:
    pkg = _tree(tmp_path / "pkg", {"2020_11_26 raw/a.csv": "x", "2020-11-26 clean/b.csv": "x"})
    f = convention_findings(pkg)
    assert f["path"].tolist() == ["2020_11_26 raw"]
    assert f["kind"][0] == "folder"


def test_leading_zeros(tmp_path: Path) -> None:
    pkg = _tree(
        tmp_path / "pkg",
        {f"trial{i}.csv": "x" for i in (1, 2, 9, 10, 11)}
        | {"trial100.txt": "x", "trial5.txt": "x"},
    )
    f = convention_findings(pkg)
    got = dict(zip(f["path"], f["detail"], strict=True))
    assert set(got) == {"trial1.csv", "trial2.csv", "trial9.csv", "trial5.txt"}
    assert set(f["rule"]) == {"zero-padding"}
    assert "Pad the number with zeros: trial01.csv" in got["trial1.csv"]
    assert "Pad the number with zeros: trial09.csv" in got["trial9.csv"]
    assert "Pad the number with zeros: trial005.txt" in got["trial5.txt"]
    assert "(such as trial10.csv)" in got["trial1.csv"]
    assert "1 digit" in got["trial1.csv"]


def test_leading_zeros_only_compare_files_in_one_folder_that_differ_in_one_number(
    tmp_path: Path,
) -> None:
    pkg = _tree(
        tmp_path / "pkg",
        {
            "a/trial1.csv": "x",
            "b/trial10.csv": "x",  # another folder
            "c/subject1_trial1.csv": "x",
            "c/subject10_trial10.csv": "x",  # differs in two numbers: not a family
            "d/s1_t01.csv": "x",
            "d/s1_t02.csv": "x",
            "d/s1_t10.csv": "x",  # all padded
            "e/run01.csv": "x",
            "e/run1.txt": "x",  # another extension
            "f/Trial1.csv": "x",
            "f/trial10.csv": "x",  # another prefix
        },
    )
    assert len(convention_findings(pkg)) == 0


def test_leading_zeros_ignore_dates_and_versions(tmp_path: Path) -> None:
    pkg = _tree(
        tmp_path / "pkg",
        {
            "analysis_v1.R": "x",
            "analysis_v2.R": "x",
            "analysis_v10.R": "x",
            "report_v0.6.pdf": "x",
            "report_v0.10.pdf": "x",
            "data_1.csv": "x",
            "data_2.csv": "x",
            "data_20201126.csv": "x",  # a date is not a number to pad to
            "log_1_20201126.txt": "x",
            "log_10_20201126.txt": "x",
        },
    )
    f = convention_findings(pkg)
    assert _by_path(f) == {"log_1_20201126.txt": ["zero-padding"]}
    assert "log_01_20201126.txt" in f["detail"][0]


def test_leading_zeros_with_a_longer_run_among_padded_names(tmp_path: Path) -> None:
    pkg = _tree(tmp_path / "pkg", {"f001.csv": "x", "f002.csv": "x", "f3.csv": "x", "f04.csv": "x"})
    got = _by_path(convention_findings(pkg))
    assert got == {"f3.csv": ["zero-padding"], "f04.csv": ["zero-padding"]}


def test_convention_severity_overrides(tmp_path: Path) -> None:
    pkg = _tree(tmp_path / "pkg", {"trial1.csv": "x", "trial10.csv": "x", "d_26-11-2020.csv": "x"})
    f = convention_findings(pkg, severity={"zero-padding": "problem", "date-format": "ignore"})
    assert dict(zip(f["path"], f["severity"], strict=True)) == {"trial1.csv": "problem"}
    f = convention_findings(pkg, severity={"zero-padding": "ignore"})
    assert f["path"].tolist() == ["d_26-11-2020.csv"]


# -- the whole check, the checklist and the module -------------------------------


REALISTIC = {
    "README.txt": "about",
    "1 Scientific documents/Protocols & methodologies.txt": "x",
    "2 Administrative documents/Data Management Plan.pdf": b"%PDF-1.4\n",
    "3 Code & Script/R-Scripts/R_script_analysis-20201126-v0.6-Smith.R": "x",
    "4 Data/Raw data/Raw_data-20201126-v0.2-Smith.csv": "a,b\n1,2\n",
    "4 Data/Processed data/Processed_data-20201126-v0.3-Smith.csv": "a,b\n1,2\n",
    ".DS_Store": "x",
    "PDF_Manuscript.docx": "x",
}


def test_check_files_on_a_realistic_package(tmp_path: Path) -> None:
    pkg = _tree(tmp_path / "pkg", REALISTIC)
    findings, checklist = check_files(pkg)
    assert list(findings.columns) == ["path", "kind", "check", "rule", "severity", "detail"]
    by = findings.groupby("check")["path"].nunique().to_dict()
    assert by["junk_files"] == 1
    assert by["file_formats"] == 2
    assert by["file_names"] == 8  # six folders and files with spaces, two with an ampersand
    assert (
        "naming_convention" not in by
    )  # the date and version fields are fine, spaces are not a style
    assert checklist["item"].tolist() == list(CHECK_TITLES)
    assert checklist["title"].tolist() == list(CHECK_TITLES.values())
    assert checklist["status"].tolist() == ["fail", "warn", "fail", "pass"]
    # the findings come in the order of the checklist
    order = list(CHECK_TITLES)
    assert [order.index(c) for c in findings["check"]] == sorted(
        order.index(c) for c in findings["check"]
    )
    assert "junk" in checklist["detail"][0]


def test_a_clean_package_is_green(tmp_path: Path) -> None:
    pkg = _tree(
        tmp_path / "pkg",
        {
            "README.md": "x",
            "data/raw_data_2020-11-26.csv": "a\n1\n",
            "data/codebook.csv": "a\n1\n",
            "code/analysis.R": "x",
            "doc/protocol.odt": "x",
            "doc/figure.png": "x",
            "LICENSE": "x",
        },
    )
    findings, checklist = check_files(pkg)
    assert len(findings) == 0
    assert checklist["status"].tolist() == ["pass"] * 4
    assert files_summary_text(findings, checklist, 7) == "No problems found in 7 files."


def test_unlisted_formats_alone_do_not_fail_the_check(tmp_path: Path) -> None:
    pkg = _tree(tmp_path / "pkg", {"README.md": "x", "data/a.xyz": "x"})
    findings, checklist = check_files(pkg)
    assert findings["severity"].tolist() == ["info"]
    assert checklist.set_index("item").loc["file_formats", "status"] == "pass"
    assert "not on the list" in checklist.set_index("item").loc["file_formats", "detail"]
    assert "not on the list" in files_summary_text(findings, checklist, 2)


def test_an_empty_package(tmp_path: Path) -> None:
    pkg = _tree(tmp_path / "pkg", {})
    findings, checklist = check_files(pkg)
    assert len(findings) == 0
    assert checklist["status"].tolist() == ["na"] * 4
    assert files_summary_text(findings, checklist, 0) == "The package has no files."
    assert "empty" in checklist["detail"][0]
    out = module_run(None, "datapackage::package_files", local_path=str(tmp_path / "pkg"))
    assert out.traffic_light == "na"
    assert "no files" in out.summary_text


def test_a_package_with_only_junk(tmp_path: Path) -> None:
    pkg = _tree(tmp_path / "pkg", {".DS_Store": "x", "__MACOSX/._a": "x"})
    findings, checklist = check_files(pkg)
    assert checklist["status"].tolist() == ["fail", "na", "na", "na"]
    assert findings["path"].tolist() == [".DS_Store", "__MACOSX"]


def test_the_checklist_follows_the_severity_overrides(tmp_path: Path) -> None:
    pkg = _tree(tmp_path / "pkg", {"a b.txt": "x"})
    _, checklist = check_files(pkg)
    assert checklist.set_index("item").loc["file_names", "status"] == "warn"
    _, checklist = check_files(pkg, severity={"spaces": "problem"})
    assert checklist.set_index("item").loc["file_names", "status"] == "fail"
    _, checklist = check_files(pkg, severity={"spaces": "info"})
    assert checklist.set_index("item").loc["file_names", "status"] == "pass"


def test_min_names_for_style_reaches_the_check(tmp_path: Path) -> None:
    pkg = _tree(tmp_path / "pkg", dict.fromkeys([*UNDERSCORE[:3], "final-results.csv"], "x"))
    assert len(check_files(pkg)[0]) == 0
    findings, _ = check_files(pkg, min_names_for_style=4)
    assert findings["rule"].tolist() == ["name-style"]


def test_the_functions_share_a_scan(tmp_path: Path) -> None:
    pkg = _tree(tmp_path / "pkg", REALISTIC)
    scan = scan_junk(pkg)
    a = format_findings(pkg, scan=scan)
    b = format_findings(pkg)
    assert a.equals(b)
    assert name_findings(pkg, scan=scan).equals(name_findings(pkg))
    assert convention_findings(pkg, scan=scan).equals(convention_findings(pkg))
    assert files_checklist(check_files(pkg)[0], scan).equals(check_files(pkg)[1])


def test_the_module_output(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    _tree(root, REALISTIC)
    out = module_run(None, "datapackage::package_files", local_path=str(root))
    assert out.traffic_light == "red"
    assert out.table is not None
    assert list(out.table.columns) == ["path", "kind", "check", "rule", "severity", "detail"]
    assert out.checklist["status"].tolist() == ["fail", "warn", "fail", "pass"]
    assert "1 junk or temporary item" in out.summary_text
    assert "\n" not in out.summary_text
    st = package_files(None, local_path=str(root))["summary_table"]
    assert len(st) == 0  # no paper: no rows
    assert {"files", "junk_files", "file_formats", "file_names", "naming_convention"} <= set(
        st.columns
    )
    assert {"problems", "suggestions"} <= set(out.summary_table.columns)
    text = "\n\n".join(str(b) if isinstance(b, str) else "[table]" for b in out.report)
    assert text.startswith("We checked 8 files and 7 folders in the data package **pkg**.")
    for title in CHECK_TITLES.values():
        assert f"**{title}**" in text
    for section in (
        "Junk and temporary files (1)",
        "File formats (2)",
        "File and folder names (10)",
    ):
        assert f'title="{section}"' in text
    assert "Consistent names" not in text  # nothing to report there
    assert text.count("**How to fix:**") == 3
    assert text.count("[table]") == 3


def test_the_module_options(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    _tree(root, {"a b.docx": "x", ".DS_Store": "x", "trial1.csv": "x", "trial10.csv": "x"})
    formats = tmp_path / "formats.json"
    formats.write_text(json.dumps({"formats": {"docx": {"level": "preferred"}}}))
    out = module_run(
        None,
        "datapackage::package_files",
        local_path=str(root),
        formats=str(formats),
        junk=[],
        severity={"spaces": "problem", "zero-padding": "ignore"},
        limits={"file_name_long": 3},
    )
    f = out.table
    # no junk rules: .DS_Store is an ordinary file; only docx is a listed format
    assert sorted(zip(f["rule"], f["severity"], strict=True)) == sorted(
        [("file-name-long", "suggestion")] * 4
        + [("spaces", "problem")]
        + [("unlisted", "info")] * 2
    )
    assert "zero-padding" not in set(f["rule"])
    out2 = module_run(
        None, "datapackage::package_files", local_path=str(root), min_names_for_style=2
    )
    assert out2.table is not None


def test_the_module_reads_an_archive(tmp_path: Path) -> None:
    archive = tmp_path / "mypkg.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("mypkg/README.txt", "x")
        zf.writestr("mypkg/my data.csv", "a\n1\n")
        zf.writestr("mypkg/doc/archival.pdf", _pdf("B"))
        zf.writestr("mypkg/doc/plain.pdf", b"%PDF-1.4\n")
        zf.writestr("__MACOSX/mypkg/._README.txt", "x")
    out = module_run(None, "datapackage::package_files", local_path=str(archive))
    f = out.table
    assert dict(_by_path(f)) == {
        "__MACOSX": ["macos-resource-fork"],
        "doc/plain.pdf": ["non-preferred"],
        "my data.csv": ["spaces"],
    }
    assert out.traffic_light == "red"


def test_the_report_of_a_clean_package_is_short(tmp_path: Path) -> None:
    pkg = _tree(tmp_path / "pkg", {"README.md": "x", "data/a.csv": "a\n1\n"})
    findings, checklist = check_files(pkg)
    report = files_report(pkg, findings, checklist)
    assert len(report) == 1
    assert report[0].startswith("We checked 2 files and 1 folder in the data package **pkg**.")
    assert "- **No junk or temporary files**: ok." in report[0]
    assert "left out of the other checks" not in report[0]


def test_no_package_given() -> None:
    out = module_run(None, "datapackage::package_files")
    assert out.traffic_light == "na"
    assert "No data package" in out.summary_text
