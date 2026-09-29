"""Tests beyond metacheck's testthat file: repository expansions, version
pinning, URLs, notebooks, Quarto engines and the chunk-option evaluator.

Expected values were checked against R (metacheck at the pinned commit).
"""

from __future__ import annotations

import gzip
import shutil
from pathlib import Path

import pandas as pd
import pytest

from metacheck.codecheck import core
from metacheck.codecheck._reval import NA, EvalError, RVersion, r_eval
from metacheck.codecheck._rparse import parse_exprs

ROOT = Path(__file__).resolve().parents[2]
EXPAND = Path(__file__).parent / "fixtures" / "expand"  # copies of statout fixtures
FIX = Path(__file__).parent / "fixtures"


def _copy(names: dict[str, Path], dest: Path) -> None:
    for name, src in names.items():
        if not src.exists():
            pytest.skip(f"fixture {src} not available")
        shutil.copy(src, dest / name)


# ---------------------------------------------------------------------------
# .code_expand_*()
# ---------------------------------------------------------------------------


def test_code_expand_output_formats(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _copy(
        {
            "modern.spv": EXPAND / "modern.spv",
            "notzip.spv": EXPAND / "notzip.spv",
            "analysis.smcl": EXPAND / "analysis.smcl",
            "twolevel.out": EXPAND / "twolevel.out",
            "nosections.out": EXPAND / "nosections.out",
        },
        tmp_path,
    )
    monkeypatch.chdir(tmp_path)
    names = ["modern.spv", "notzip.spv", "analysis.smcl", "twolevel.out", "nosections.out", "a.R"]
    af = pd.DataFrame(
        {
            "file_name": names,
            "file_path": [
                "sub/modern.spv",
                "notzip.spv",
                None,
                "x/y/twolevel.out",
                "nosections.out",
                "a.R",
            ],
            "file_location": names,
            "file_url": [None] * 6,
            "repo_url": ["r"] * 6,
            "data_type": ["output"] * 6,
        }
    )
    expected = {
        "_code_expand_spv": ("modern.sps", "sub/code/modern.sps", "./code/modern.sps", 109.0),
        "_code_expand_smcl": ("analysis.do", "NA/code/analysis.do", "./code/analysis.do", 274.0),
        "_code_expand_mplus": (
            "twolevel.inp",
            "x/y/code/twolevel.inp",
            "./code/twolevel.inp",
            192.0,
        ),
    }
    for fn, (name, path, loc, size) in expected.items():
        out = getattr(core, fn)(af, 10, 100, False)
        assert len(out) == 7, fn
        row = out.iloc[6]
        assert (row["file_name"], row["file_path"], row["file_location"]) == (name, path, loc)
        assert pd.isna(row["file_url"])
        assert row["file_size"] == size
        assert row["data_type"] == "output"
        assert out.iloc[:6]["file_name"].tolist() == names  # originals kept, first


def test_code_expand_html(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "report.html").write_text(
        """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<meta name="generator" content="pandoc" />
<title>Report</title>
</head>
<body>
<pre class="r"><code>library(dplyr)
x &lt;- read.csv(&quot;data.csv&quot;)</code></pre>
<pre><code>## output</code></pre>
</body>
</html>
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    af = pd.DataFrame(
        {
            "file_name": ["report.html", "x.R"],
            "file_path": ["docs/report.html", "x.R"],
            "file_location": ["report.html", None],
            "file_url": [None, None],
            "repo_url": ["r", "r"],
            "data_type": ["materials", "code"],
        }
    )
    out = core._code_expand_html(af, 10, 100, False)
    assert out["file_name"].tolist() == ["report.html", "x.R", "report.R"]
    assert out["data_type"].tolist() == ["output", "code", "materials"]
    assert out["file_path"].iloc[2] == "docs/code/report.R"
    assert (tmp_path / "code" / "report.R").read_text(encoding="utf-8").startswith("library(dplyr)")


def test_code_expand_no_candidates_returns_input() -> None:
    af = pd.DataFrame({"file_name": ["a.R"], "file_location": ["a.R"], "repo_url": ["r"]})
    for fn in ("_code_expand_spv", "_code_expand_smcl", "_code_expand_mplus", "_code_expand_html"):
        assert getattr(core, fn)(af, 10, 100, False) is af
    assert core._code_expand_zip(af) is af
    assert core._code_predownload(af, 10, 100, False) is af


def test_code_predownload_uses_download_repo_files(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[pd.DataFrame] = []

    def fake_download(rows: pd.DataFrame, **kwargs: object) -> pd.DataFrame:
        calls.append(rows)
        out = rows.copy()
        out["file_location"] = [f"/cache/{n}" for n in rows["file_name"]]
        out.attrs["gated"] = ["g"]
        return out

    monkeypatch.setattr(core, "_download", lambda rows, all_files, **kw: fake_download(rows, **kw))
    af = pd.DataFrame(
        {
            "file_name": ["a.R", "b.spv", "c.csv", "d.do"],
            "file_url": ["u1", "u2", "u3", None],
            "file_location": [None, "", None, None],
            "repo_url": ["r"] * 4,
            "language": ["R", None, None, "Stata"],
        }
    )
    out = core._code_predownload(af, 10, 100, False)
    assert calls[0]["file_name"].tolist() == ["a.R", "b.spv"]  # d.do has no target
    assert out["file_location"].tolist()[:2] == ["/cache/a.R", "/cache/b.spv"]
    assert out.attrs["gated"] == ["g"]
    assert out.attrs["failed"] is None


# ---------------------------------------------------------------------------
# .code_version_pin_check()
# ---------------------------------------------------------------------------


def test_code_version_pin_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(ROOT)
    pin = "tests/codecheck/fixtures/pin"
    af = pd.DataFrame(
        {
            "file_name": ["renv.lock", "sub/renv.lock", "session_info.txt", "README.md", "x.R"],
            "file_url": [None] * 5,
            "repo_url": ["r"] * 5,
            "file_location": [
                f"{pin}/renv.lock",
                f"{pin}/sub/renv.lock",
                f"{pin}/session_info.txt",
                f"{pin}/README.md",
                None,
            ],
        }
    )
    out = core._code_version_pin_check(
        af, {"a.R": ["groundhog.library(c('a', 'b'), '2023-01-15')"]}
    )
    assert out["pinned"] is True
    assert out["mechanisms"] == ["renv.lock", "sessionInfo", "groundhog"]
    assert out["r_versions"] == ["4.3.1", "4.2.0", "4.2.3"]
    assert out["renv_files"] == ["renv.lock", "sub/renv.lock"]
    assert out["renv_packages"]["package"].tolist() == ["dplyr", "rlang", "local"]
    assert pd.isna(out["renv_packages"]["source"].iloc[2])
    assert out["sessioninfo_files"] == ["session_info.txt", "README.md"]
    assert out["file_location"].index.tolist() == [
        "renv.lock",
        "sub/renv.lock",
        "session_info.txt",
        "README.md",
    ]

    empty = core._code_version_pin_check(None)
    assert empty["pinned"] is False
    assert list(empty["renv_packages"].columns) == ["file_name", "package", "version", "source"]


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------


def test_code_read_url(respx_mock) -> None:  # type: ignore[no-untyped-def]
    import httpx

    respx_mock.get("https://example.org/a.R").mock(
        return_value=httpx.Response(200, content="x <- 1\r\ny <- 'Größe'".encode("latin-1"))
    )
    assert core.code_read("https://example.org/a.R") == ["x <- 1", "y <- 'Größe'"]
    respx_mock.get("https://example.org/b.R.gz").mock(
        return_value=httpx.Response(200, content=gzip.compress(b"z <- 3\n"))
    )
    assert core.code_read("https://example.org/b.R.gz") == ["z <- 3"]


def test_code_read_compressed_and_odd_files() -> None:
    enc = FIX / "encodings"
    assert core.code_read(enc / "gzipped.R")[0] == "# Die Größe der Stichprobe"
    assert core.code_read(enc / "newline_only.R") == []
    assert core.code_read(enc / "cr_only.R")[1].startswith("x <- read.csv")
    # invalid UTF-8: ICU's best guess is ISO-8859-1, so the valid "é" is mojibake
    assert core.code_read(enc / "invalid_utf8.R") == [
        "x <- 'caf\u00c3\u00a9'",
        "y <- 'caf\u00e9 \u00ff'",
    ]
    with pytest.raises(FileNotFoundError):
        core.code_read(enc / "missing.R")


def test_notebook_and_quarto_languages() -> None:
    nb = FIX / "notebooks"
    assert core.code_lang(str(nb / "ir_language_info.ipynb")) == "R"
    assert core.code_lang(str(nb / "ir_kernel_name.ipynb")) == "R"
    assert core.code_lang(str(nb / "julia.ipynb")) == "Python"
    assert core.code_lang(str(nb / "lang_list.ipynb")) == "R"
    assert core.code_lang(str(nb / "invalid.ipynb")) == "Python"
    qmd = FIX / "qmd"
    assert core.code_lang(str(qmd / "jupyter_python.qmd")) == "Python"
    assert core.code_lang(str(qmd / "kernelspec.qmd")) == "Python"
    assert core.code_lang(str(qmd / "jupyter_ir.qmd")) == "R"
    assert core.code_lang(str(qmd / "first_chunk_python.qmd")) == "Python"
    assert core.code_lang(str(qmd / "r_default.qmd")) == "R"
    # a scalar front matter has no `jupyter` key: the first chunk decides
    # (R's `$` error escapes code_lang(), U68)
    assert core.code_lang(str(qmd / "scalar_yaml.qmd")) == "Python"


def test_malformed_front_matter_and_notebooks_fall_back(tmp_path: Path) -> None:
    # U68: an empty, scalar or array front matter and a notebook that is not a
    # JSON object fall back to the default / the first chunk's engine
    cases = {
        "empty.qmd": ("---\n---\n```{python}\nx = 1\n```\n", "Python"),
        "empty_r.qmd": ("---\n---\n```{r}\nx <- 1\n```\n", "R"),
        "scalar.qmd": ("---\nhello\n---\n```{python}\nx = 1\n```\n", "Python"),
        "array.qmd": ("---\n- a\n- b\n---\n```{python}\nx = 1\n```\n", "Python"),
        "no_lang.qmd": ("---\njupyter: []\n---\n```{python}\n```\n", "Python"),
        "string.ipynb": ('"hello"\n', "Python"),
        "array.ipynb": ("[1, 2]\n", "Python"),
        "meta_scalar.ipynb": ('{"metadata": 3}\n', "Python"),
        "lang_empty.ipynb": ('{"metadata": {"kernelspec": {"language": []}}}\n', "Python"),
        "lang_list.ipynb": ('{"metadata": {"kernelspec": {"language": ["R"]}}}\n', "R"),
    }
    for name, (text, lang) in cases.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
        assert core.code_lang(str(tmp_path / name)) == lang, name


def test_code_extract_qmd_py() -> None:
    out = core.code_extract_qmd_py(FIX / "qmd" / "first_chunk_python.qmd")
    assert out == [
        "import numpy as np",
        "x = np.array([1])",
        "",
        "print('four')",
        "```",
        "still inside",
        "",
    ]


def test_code_extract_py_notebook_magics() -> None:
    assert core.code_extract_py(FIX / "notebooks" / "no_metadata.ipynb") == [
        "import os",
        "y = 2",
        "",
        "",
        "",
    ]


# ---------------------------------------------------------------------------
# chunk-option evaluator
# ---------------------------------------------------------------------------


def _ev(src: str) -> object:
    return r_eval(parse_exprs([src])[0])


def test_r_eval() -> None:
    assert _ev("FALSE") is False
    assert _ev("F") is False
    assert _ev("!T") is False
    assert _ev("TRUE && NA") is NA
    assert _ev("FALSE && NA") is False
    assert _ev("c(1, 3)") == [1.0, 3.0]
    assert _ev("getRversion() >= '4.1.0'") is True
    assert isinstance(_ev("getRversion()"), RVersion)
    assert _ev("identical(Sys.getenv('PYTACHECK_SURELY_UNSET_VAR'), '')") is True
    assert _ev("exists('penguins')") is True
    assert _ev("require('emo')") is False
    assert _ev("if (1 > 2) 'a' else 'b'") == "b"
    assert _ev(".Platform$OS.type %in% c('unix', 'windows')") is True
    with pytest.raises(EvalError):
        _ev("params$run")
    with pytest.raises(EvalError):
        _ev("undefined_fun()")
