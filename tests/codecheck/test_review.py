"""Regression tests for divergences from R found in the adversarial review.

Every expected value was produced by R 4.5.3 with metacheck at the pinned
commit (jsonlite 2.0.0, knitr 1.52, vroom 1.7.1); the matching parity cases
are in ``parity/cases/codecheck_review.yaml``.
"""

from __future__ import annotations

import gzip
import math
import shutil
from pathlib import Path

import pandas as pd
import pytest

from pytacheck.codecheck import core
from pytacheck.codecheck._reval import r_eval
from pytacheck.codecheck._rjson import RList, json_load, r_as_character, r_unlist_chr
from pytacheck.codecheck._rparse import parse_exprs

FIX = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# code_abs_path(): search_text() normalises the text the paths come from
# ---------------------------------------------------------------------------


def test_abs_path_whitespace_is_normalised() -> None:
    out = core.code_abs_path(['f <- "C:/My   Docs/a , b.csv"', 'g <- "/home/u/x\ty.csv"'])
    assert out["abs_path"].tolist() == ["C:/My Docs/a, b.csv", "/home/u/x y.csv"]
    assert out["line"].tolist() == [1, 2]


def test_abs_path_paragraph_marker_and_unicode_space() -> None:
    out = core.code_abs_path(['x <- "C:/a<~p~>b.csv"', 'z <- "D:/fine.R"'])
    assert out["abs_path"].tolist() == ["D:/fine.R"]
    assert out["line"].tolist() == [2]
    # TRE's \s is glibc iswspace(): an em space collapses, a no-break space does not
    out = core.code_abs_path(['p <- "/home/u/a\u2003\u2003b.csv"', 'q <- "C:/x\u00a0\u00a0y.txt"'])
    assert out["abs_path"].tolist() == ["/home/u/a b.csv", "C:/x\u00a0\u00a0y.txt"]


# ---------------------------------------------------------------------------
# code_line_stats(): TRUE & NA is NA
# ---------------------------------------------------------------------------


def test_line_stats_na_comment_line() -> None:
    stats = core.code_line_stats(["/* c", None, "*/", "x;"], "SAS")
    assert stats["total_lines"] == 4
    assert stats["comment_lines"] is None
    assert math.isnan(stats["percent_comments"])
    # an NA line that is not flagged does not matter
    stats = core.code_line_stats(["* comment.", None, "GET FILE='x.sav'."], "SPSS")
    assert stats["comment_lines"] == 1


# ---------------------------------------------------------------------------
# jsonlite (yajl) parsing and unlist()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("[1] // c", [1]),
        ("/* c */ [1]", [1]),
        ("[1, /* c */ 2]", [1, 2]),
        ("[1] /* unterminated", [1]),
        ('["//", "/*"]', ["//", "/*"]),
        ("\v[1]\f", [1]),
        ("\ufeff[1]", [1]),
        ("2147483647", 2147483647),
        ("2147483648", 2147483648.0),
        ("-2147483648", -2147483648.0),
        ('"a\\ud800b"', "a?b"),
        ('"\\ud800\\u0041"', "\U00010041"),
        ('"\\ud800\\ud800"', "\U00010000"),
        ('"\\ud83d\\ude00"', "\U0001f600"),
        ('["a\\u0000b", "c"]', ["a", "c"]),
    ],
)
def test_json_load_yajl_quirks(text: str, expected: object) -> None:
    value = json_load(text)
    assert value == expected
    assert type(value) is type(expected)


@pytest.mark.parametrize("text", ["[1] # c", "[1]/", "/**/", " \ufeff[1]", "[1,]", "[01]", "NaN"])
def test_json_load_errors(text: str) -> None:
    assert json_load(text) is None


def test_json_load_nul_in_key_and_duplicates() -> None:
    value = json_load('{"a\\u0000b": 1, "a": 2}')
    assert isinstance(value, RList)
    assert value.names() == ["a", "a"]


def test_unlist_coercion() -> None:
    assert r_unlist_chr([1, True]) == ["1", "1"]
    assert r_unlist_chr([1.5, 3e9]) == ["1.5", "3e+09"]
    assert r_unlist_chr([True, False]) == ["TRUE", "FALSE"]
    assert r_unlist_chr(["x", 1, 2.5, True, None]) == ["x", "1", "2.5", "TRUE"]
    assert r_as_character(RList([("a", "r"), ("b", [1])])) == ["r", "list(1L)"]


def test_code_extract_py_json_quirks() -> None:
    def nb(src: str) -> str:
        return '{"cells": [{"cell_type": "code", "source": ' + src + "}]}"

    assert core.code_extract_py(text=nb('"x"') + " // c") == ["x", ""]
    assert core.code_extract_py(text="\ufeff" + nb('"x"')) == ["x", ""]
    assert core.code_extract_py(text=nb('"a\\u0000b\\nc"')) == ["a", ""]
    assert core.code_extract_py(text=nb("[1, true]")) == ["11", ""]
    assert core.code_extract_py(text=nb("[1.5, 3000000000]")) == ["1.53e+09", ""]
    assert core.code_extract_py(text=nb("[0.1, 1e-20, 123456789012345678]")) == [
        "0.11e-20123456789012345680",
        "",
    ]


def test_notebook_files_with_comments_and_bom() -> None:
    assert core.code_lang(str(FIX / "review" / "nb_comments.ipynb")) == "R"
    assert core.code_lang(str(FIX / "review" / "nb_bom.ipynb")) == "R"
    assert core.code_extract_py(str(FIX / "review" / "nb_comments.ipynb")) == [
        "library(dplyr)",
        "x <- 1 // not a comment in R",
        "",
    ]


# ---------------------------------------------------------------------------
# code_read(): a compressed UTF-16 file is decompressed before re-encoding
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("bom", "codec"), [(b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")])
def test_code_read_gzip_utf16(tmp_path: Path, bom: bytes, codec: str) -> None:
    text = "x <- 1\r\ny <- 'caf\u00e9'\r\n# \u65e5\u672c\r\n"
    path = tmp_path / "a.R"
    path.write_bytes(gzip.compress(bom + text.encode(codec)))
    assert core.code_read(str(path)) == ["x <- 1", "y <- 'caf\u00e9'", "# \u65e5\u672c"]


# ---------------------------------------------------------------------------
# code_lang(): tools::file_ext()'s "$" is the very end
# ---------------------------------------------------------------------------


def test_code_lang_trailing_newline() -> None:
    assert core.code_lang(["a.R\n", "b.py", "d.R "]) == [None, "Python", None]


# ---------------------------------------------------------------------------
# knitr::purl()
# ---------------------------------------------------------------------------


def _rmd(header: str, *body: str) -> list[str]:
    return ["Intro", header, *(body or ("x <- 1", "y <- 2")), "```", "End"]


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        # engine is compared as written, never evaluated
        ("```{r, engine=x}", ["## x <- 1", "## y <- 2"]),
        ("```{r, engine=paste0('p', 'y')}", ["## x <- 1", "## y <- 2"]),
        ("```{r, engine=R}", ["x <- 1", "y <- 2"]),
        ("```{python, comment=x}", ["x x <- 1", "x y <- 2"]),
        # opts_chunk$merge(): a later duplicate option wins
        ("```{r, eval=FALSE, eval=TRUE}", ["x <- 1", "y <- 2"]),
        ("```{r, eval=TRUE, eval=FALSE}", ["# x <- 1", "# y <- 2"]),
        # ...but tangle_mask() reads x$params$error, the first one
        ("```{r, error=TRUE, error=FALSE}", ["try({", "x <- 1", "y <- 2", "})"]),
        ("```{r, eval=2:3}", ["x <- 1", "y <- 2"]),
        ("```{r, eval=seq_len(2)}", ["x <- 1", "y <- 2"]),
        ("```{r, eval=integer(0)}", ["x <- 1", "y <- 2"]),
        ("```{r, eval=Sys.time() > 0}", ["x <- 1", "y <- 2"]),
        # out_format() is "markdown" while purling R Markdown
        ("```{r, eval=knitr::is_html_output()}", ["x <- 1", "y <- 2"]),
        ("```{r, eval=knitr::is_html_output(excludes='markdown')}", ["# x <- 1", "# y <- 2"]),
        ("```{r, eval=knitr::is_latex_output()}", ["# x <- 1", "# y <- 2"]),
    ],
)
def test_purl_chunk_options(header: str, expected: list[str]) -> None:
    assert core.code_extract_r(text=_rmd(header)) == expected


@pytest.mark.parametrize(
    "header", ["```{r, engine=NA}", "```{r, engine=NULL}", "```{python, comment=f(1)}"]
)
def test_purl_option_errors(header: str) -> None:
    with pytest.raises(ValueError):
        core.code_extract_r(text=_rmd(header))


def test_purl_rnw_out_format() -> None:
    text = [
        "<<eval=knitr::is_latex_output()>>=",
        "x <- 1",
        "@",
        "<<eval=knitr::is_html_output()>>=",
        "y <- 2",
        "@",
    ]
    assert core.code_extract_r(text=text) == ["x <- 1", "# y <- 2"]


def test_purl_yaml_labels() -> None:
    doc = ["```{r}", "#| label: TRUE", "x <- 1", "```", "```{r}", "y", "```"]
    assert core.code_extract_r(text=doc) == ["x <- 1", "y"]
    doc = ["```{r}", "#| label: [a, b]", "x <- 1", "```", "```{r}", "<<a>>", "```"]
    assert core.code_extract_r(text=doc) == ["x <- 1", "x <- 1"]
    # a numeric or logical label is the chunk's name, not a position in
    # knit_code (knitr: "subscript out of bounds" / the first chunk, U153)
    doc = ["```{r}", "#| label: 3", "x <- 1", "```"]
    assert core.code_extract_r(text=doc) == ["x <- 1"]
    doc = ["```{r}", "#| label: 3", "x <- 1", "```", "```{r}", "<<3>>", "```"]
    assert core.code_extract_r(text=doc) == ["x <- 1", "x <- 1"]
    doc = ["```{r}", "a <- 1", "```", "```{r}", "#| label: TRUE", "b <- 2", "```"]
    assert core.code_extract_r(text=doc) == ["a <- 1", "b <- 2"]
    doc = ["```{r}", "a <- 1", "```", "```{r}", "#| label: 1", "b <- 2", "```"]
    assert core.code_extract_r(text=doc) == ["a <- 1", "b <- 2"]


def test_purl_na_lines() -> None:
    with pytest.raises(ValueError, match="missing value"):
        core.code_extract_r(text=["```{r}", None, "```"])
    assert core.code_extract_r(text=["```{r}", "x", None, "```"]) == ["x", "NA"]
    assert core.code_extract_r(text=[None, "text"]) == []


def test_r_eval_colon_and_constructors() -> None:
    def ev(src: str) -> object:
        return r_eval(parse_exprs([src])[0])

    assert ev("2:3") == [2.0, 3.0]
    assert ev("3:1") == [3.0, 2.0, 1.0]
    assert ev("1.5:3") == [1.5, 2.5]
    assert ev("integer(0)") == []
    assert ev("logical(2)") == [False, False]
    assert ev("seq_len(1)") == 1.0


# ---------------------------------------------------------------------------
# .code_expand_*() / .code_predownload() / .code_version_pin_check()
# ---------------------------------------------------------------------------


def test_expand_without_file_location_errors() -> None:
    af = pd.DataFrame({"repo_url": ["r"], "file_name": ["a.smcl"], "file_url": [None]})
    with pytest.raises(TypeError, match="invalid 'file' argument"):
        core._code_expand_smcl(af, 100, 500, False)
    with pytest.raises(TypeError, match="invalid 'file' argument"):
        core._code_expand_html(af.assign(file_name=["a.html"]), 100, 500, False)


@pytest.mark.parametrize("missing", ["language", "file_location", "file_url"])
def test_predownload_missing_column_is_a_no_op(
    missing: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("no download expected")

    monkeypatch.setattr("pytacheck.archives.download.download_repo_files", boom)
    af = pd.DataFrame(
        {
            "repo_url": ["r", "r"],
            "file_name": ["a.smcl", "b.R"],
            "file_url": ["https://example.invalid/a.smcl", "https://example.invalid/b.R"],
            "file_location": [None, None],
            "language": [None, "R"],
        }
    ).drop(columns=missing)
    out = core._code_predownload(af, 100, 500, False)
    pd.testing.assert_frame_equal(out, af)


def test_version_pin_check_jsonlite_lockfiles(tmp_path: Path) -> None:
    shutil.copytree(FIX / "review" / "pin", tmp_path / "pin")
    names = ["renv.lock", "sub/renv.lock", "SESSION-INFO.txt"]
    af = pd.DataFrame(
        {
            "repo_url": ["r"] * 3,
            "file_name": names,
            "file_url": [None] * 3,
            "file_location": [str(tmp_path / "pin" / n) for n in names],
        }
    )
    out = core._code_version_pin_check(af, [["checkpoint::checkpoint('2020-02-02')"]])
    assert out["mechanisms"] == ["renv.lock", "sessionInfo", "checkpoint"]
    assert out["r_versions"] == ["4.3.1", "4.2", "4.4.2"]
    assert out["renv_files"] == ["renv.lock", "sub/renv.lock"]
    assert out["renv_packages"]["package"].tolist() == ["dplyr", "x", "a"]
    with pytest.raises(TypeError, match="attribute on NULL"):
        core._code_version_pin_check(af.drop(columns="file_location"))


def test_code_expand_zip_mocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Expected rows from R with the same three functions mocked (testthat)."""

    def peek(url: str) -> pd.DataFrame:
        if "other" in url:
            return pd.DataFrame({"name": ["x.csv", "y.R"], "size": [1, 2]})
        return pd.DataFrame(
            {"name": ["code/a.R", "readme.txt", "nb.ipynb", "s.do"], "size": [10, 20, 30, 40]}
        )

    def fetch(url: str, names: list[str], dest: str) -> pd.DataFrame:
        ok = [True, *([False, True] * len(names))[: len(names) - 1]]
        return pd.DataFrame(
            {
                "name": names,
                "path": [f"{dest}/{n}" for n in names],
                "size": [7.0 * (i + 1) for i in range(len(names))],
                "ok": ok,
            }
        )

    monkeypatch.setattr("pytacheck.archives.zip_peek.zip_peek", peek)
    monkeypatch.setattr("pytacheck.archives.zip_peek._zip_fetch_members", fetch)
    monkeypatch.setattr(
        "pytacheck.archives.download._repo_cache_path", lambda repo, fp: f"/cache/{fp}"
    )
    af = pd.DataFrame(
        {
            "repo_url": ["https://osf.io/abcde"] * 3,
            "file_name": ["data.zip", "b.R", "other.ZIP"],
            "file_path": ["sub/data.zip", "b.R", None],
            "file_url": ["https://x/data.zip", None, "https://x/other.zip"],
            "file_size": [100.0, 5.0, 50.0],
        }
    )
    out = core._code_expand_zip(af)
    assert out["file_name"].tolist() == ["data.zip", "b.R", "other.ZIP", "a.R", "s.do", "y.R"]
    assert out["file_path"].tolist()[3:] == [
        "sub/data.zip/code/a.R",
        "sub/data.zip/s.do",
        "NA/y.R",
    ]
    assert out["file_location"].tolist()[3:] == [
        "/cache/sub/data.zip.contents/code/a.R",
        "/cache/sub/data.zip.contents/s.do",
        "/cache/NA.contents/y.R",
    ]
    assert out["file_size"].tolist() == [100, 5, 50, 7, 21, 7]
    assert out["file_url"].isna().tolist() == [False, True, False, True, True, True]
