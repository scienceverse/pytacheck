"""code_check synchronised with metacheck 0.4.4.35 (tests/testthat/test-code_check.R and
test-module-code_check.R: #695, #700, #702, #706)."""

from __future__ import annotations

import gzip
from pathlib import Path

import pandas as pd
import pytest

import metacheck as pc
from metacheck.codecheck.core import (
    _code_literal_strings,
    _code_loader_functions,
    _code_not_text,
    code_lang,
    code_library_names,
)
from metacheck.module import module_run
from metacheck.modules._count_refused import (
    _count_refused_add,
    _count_refused_note,
    _repo_count_message,
    _repos_over_count,
)
from metacheck.utils import _lfs_pointer_size, _lfs_pointer_text


def _report_text(out: object) -> str:
    """Text blocks and the cells of table blocks, joined."""
    from metacheck.report import ReportTable

    parts: list[str] = []
    for b in out.report:  # type: ignore[attr-defined]
        if isinstance(b, ReportTable):
            parts += [str(v) for v in b.data.to_numpy().ravel()]
        elif isinstance(b, str):
            parts.append(b)
    return " ".join(parts)


def test_code_lang_reads_the_extension_of_a_name_that_ends_in_a_space() -> None:
    assert code_lang("trailing_space.R ") == "R"


def test_code_not_text_names_files_whose_content_is_no_code(tmp_path: Path) -> None:
    (tmp_path / "rdata_named.R").write_bytes(gzip.compress(b"RDX3\n" + bytes(range(20))))
    (tmp_path / "png_named.R").write_bytes(bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]))
    (tmp_path / "html_named.py").write_text("<!DOCTYPE html>\n<html></html>\n")
    (tmp_path / "ok.R").write_text("x <- 1\n")
    (tmp_path / "utf16.R").write_bytes(b"\xff\xfe" + "x <- 1".encode("utf-16-le"))
    (tmp_path / "pdf.R").write_bytes(b"%PDF-1.4\n")
    (tmp_path / "zip.R").write_bytes(b"PK\x03\x04" + bytes(10))
    (tmp_path / "bin.R").write_bytes(b"abc\x00def")
    (tmp_path / "empty.R").write_bytes(b"")
    r = {n: _code_not_text(tmp_path / n) for n in sorted(p.name for p in tmp_path.iterdir())}
    assert "compressed file" in (r["rdata_named.R"] or "")
    assert "PNG image" in (r["png_named.R"] or "")
    assert "HTML page" in (r["html_named.py"] or "")
    assert "PDF file" in (r["pdf.R"] or "")
    assert "zip archive" in (r["zip.R"] or "")
    assert "binary data" in (r["bin.R"] or "")
    assert r["ok.R"] is None and r["utf16.R"] is None and r["empty.R"] is None
    assert _code_not_text(tmp_path / "missing.R") is None


def test_lfs_pointer_size_and_text(tmp_path: Path) -> None:
    (tmp_path / "p.R").write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:ab\nsize 99\n"
    )
    (tmp_path / "nosize.R").write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:ab\n"
    )
    (tmp_path / "ok.R").write_text("x <- 1\n")
    sizes = _lfs_pointer_size([str(tmp_path / n) for n in ("p.R", "nosize.R", "ok.R")])
    assert sizes[0] == 99 and sizes[1] == -1 and sizes[2] != sizes[2]
    report, summary = _lfs_pointer_text(["a.R", "b.R"], [99, -1])
    assert summary is not None and summary.startswith("2 files are a Git LFS pointer")
    assert report is not None and report[0] == "#### Files kept in Git LFS"
    assert report[-2] == "- `a.R` (the file itself is 99.0 B)" and report[-1] == "- `b.R`"
    assert _lfs_pointer_text([], []) == (None, None)


def test_literal_vectors_and_loader_functions() -> None:
    assert _code_literal_strings("c(\"a\", 'b')") == ["a", "b"]
    assert _code_literal_strings('c("a", f(b))') == []
    code = [
        "ipak <- function(pkg){",
        '  new.pkg <- pkg[!(pkg %in% installed.packages()[, "Package"])]',
        "  if (length(new.pkg)) install.packages(new.pkg, dependencies = TRUE)",
        "  sapply(pkg, require, character.only = TRUE)",
        "}",
        'packages <- c("Gmisc", "epitools")',
        "ipak(packages)",
        'ipak(c("a", "b"))',
        'for (p in c("fGarch", "dplyr")) library(p, character.only = TRUE)',
        'lapply(c("x", "y"), library, character.only = TRUE)',
        "other <- function(z) z + 1",
    ]
    assert _code_loader_functions(code) == ["ipak"]
    out = code_library_names(code, "R")
    got = list(zip(out["package"], out["source"], strict=True))
    assert ("Gmisc", "loader") in got and ("epitools", "loader") in got
    assert ("a", "loader") in got and ("b", "loader") in got
    assert ("fGarch", "library") in got and ("dplyr", "library") in got
    assert ("x", "library") in got and ("y", "library") in got
    assert "pkg" not in set(out["package"]) and "packages" not in set(out["package"])


def test_code_library_names_skips_lines_without_a_loading_word() -> None:
    assert code_library_names(["x <- 1", "from a.b import c", "import d, e as f"], "Python")[
        "package"
    ].tolist() == ["a", "d", "e"]
    assert code_library_names(["x <- 1", "y <- 2"], "R").empty


def test_code_check_names_the_code_files_it_did_not_check(tmp_path: Path) -> None:
    (tmp_path / "ok.R").write_text("x <- 1\n")
    (tmp_path / "big.R").write_text("y <- 2 # a comment\n" * 20000)
    (tmp_path / "rdata_named.R").write_bytes(gzip.compress(b"RDX3\n" + bytes(range(20))))
    (tmp_path / "analysis.jl").write_text("x = 1\n")
    (tmp_path / "pointer.R").write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:ab\nsize 99\n"
    )
    cc = module_run(
        pc.test_paper(["x"]),
        "code_check",
        local_path=str(tmp_path),
        local_only=True,
        max_code_size=0.1,
    )
    assert "4 code files were not checked" in cc["summary_text"]
    rep = _report_text(cc)
    assert "#### Code files not checked" in rep
    assert "more than max_code_size" in rep
    assert "compressed file" in rep
    assert "Julia code is recognised, but not checked" in rep
    assert "Git LFS pointer" in rep


def test_code_check_lists_a_jags_model_saved_as_r_as_not_checked(tmp_path: Path) -> None:
    (tmp_path / "bglmm.R").write_text(
        "model{\n  for (i in 1:N) { y[i] ~ dnorm(mu, tau) }\n  mu ~ dnorm(0, 0.001)\n}\n"
    )
    (tmp_path / "ok.R").write_text("x <- 1\n")
    cc = module_run(pc.test_paper(["x"]), "code_check", local_path=str(tmp_path), local_only=True)
    assert "JAGS or BUGS model" in _report_text(cc)
    assert not any(v is True for v in cc.table["parse_error"].tolist())


def test_count_refused_helpers() -> None:
    listing = pd.DataFrame({"repo_url": ["b"] * 3 + ["a"] * 3 + ["c"]})
    over = _repos_over_count(listing, 2)
    assert over["repo_url"].tolist() == ["a", "b"] and over["n_files"].tolist() == [3, 3]
    assert over["message"].iloc[0] == _repo_count_message("a", 3, 2)
    assert "holds 3 files, exceeding the 2-file cap" in over["message"].iloc[0]
    assert _repos_over_count(listing, float("inf")).empty
    assert _repos_over_count(None, 2).empty
    added = _count_refused_add(over.iloc[:1], over)
    assert added is not None and added["repo_url"].tolist() == ["a", "b"]
    assert _count_refused_add(over, None) is over
    note = _count_refused_note("Nothing.", over)
    assert note is not None and note["traffic_light"] == "yellow"
    assert note["summary_text"].startswith("\n-  Nothing.\n-  Repository a holds 3 files")
    assert _count_refused_note("Nothing.", over.iloc[0:0]) is None


def test_code_check_refuses_a_repository_with_too_many_files(tmp_path: Path) -> None:
    for i in range(4):
        (tmp_path / f"s{i}.R").write_text("x <- 1\n")
    with pytest.warns(UserWarning, match="exceeding the 2-file cap"):
        cc = module_run(
            pc.test_paper(["x"]),
            "code_check",
            local_path=str(tmp_path),
            local_only=True,
            max_files_per_repo=2,
        )
    assert cc["count_refused"].shape[0] == 1
    assert cc["traffic_light"] == "yellow"
    assert "exceeding the 2-file cap" in cc["summary_text"]


def test_inner_zips_are_opened_up_to_two_more_levels(tmp_path: Path) -> None:
    import io
    import zipfile

    from metacheck.codecheck.core import _code_expand_inner_zips

    def make(members: dict[str, bytes]) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for k, v in members.items():
                z.writestr(k, v)
        return buf.getvalue()

    level3 = make({"deep.R": b"z <- 3\n"})
    level2 = make({"mid.R": b"y <- 2\n", "level3.zip": level3, "data.csv": b"a,b\n1,2\n"})
    level1 = make({"top.R": b"x <- 1\n", "level2.zip": level2})
    z1 = tmp_path / "level1.zip"
    z1.write_bytes(level1)
    zips = pd.DataFrame(
        {
            "file_name": ["level1.zip"],
            "file_path": ["level1.zip"],
            "repo_url": ["https://osf.io/abcde"],
            "file_location": [str(z1)],
        }
    )
    listing = pd.DataFrame({"file_name": ["level1.zip"], "repo_url": ["https://osf.io/abcde"]})
    out = _code_expand_inner_zips(zips, listing)
    # the zip given, and the one in it: the code of the zip inside that is not reached
    assert sorted(out["file_name"]) == ["mid.R", "top.R"]
    assert all(Path(p).is_file() for p in out["file_location"])
    assert sorted(_code_expand_inner_zips(zips, listing, levels=3)["file_name"]) == [
        "deep.R",
        "mid.R",
        "top.R",
    ]
    out1 = _code_expand_inner_zips(zips, listing, levels=1)
    assert sorted(out1["file_name"]) == ["top.R"]
    assert _code_expand_inner_zips(zips.iloc[0:0], listing).empty
