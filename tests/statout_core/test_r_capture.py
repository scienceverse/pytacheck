"""Tests for pytacheck.statout.r_capture (port of R/r-capture.R)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pandas as pd
import pytest

from pytacheck.statout.r_capture import (
    RSubprocessError,
    RSubprocessTimeout,
    _r_call_fun_arg,
    _r_cap_anova,
    _r_cap_by_fun,
    _r_cap_coef_matrix,
    _r_cap_htest,
    _r_capture_helpers,
    _r_capture_run,
    _r_capture_value,
    _r_captures_to_tables,
    _r_merge_captures,
    _r_method_to_fn,
    _read_captures,
)
from pytacheck.statout.r_output import read_r_output


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").rstrip("\n").split("\n")


def test_r_method_to_fn() -> None:
    assert _r_method_to_fn("Shapiro-Wilk normality test") == "shapiro.test"
    assert _r_method_to_fn("Wilcoxon rank sum test with continuity correction") == "wilcox.test"
    assert _r_method_to_fn("Pearson's Chi-squared test") == "chisq.test"
    assert _r_method_to_fn("F test to compare two variances") == "var.test"
    assert _r_method_to_fn("Welch Two Sample t-test") == "t.test"
    assert _r_method_to_fn("Exact binomial test") == ""
    assert _r_method_to_fn(None) == ""
    assert _r_method_to_fn("") == ""


def test_captures_to_tables(data_dir: Path) -> None:
    caps = _read_captures(data_dir / "capture_script.captures.json")
    assert caps is not None and len(caps) == 18
    tabs = _r_captures_to_tables(
        caps, source_label="capture_script.R", code_lines=_lines(data_dir / "capture_script.R")
    )
    assert len(tabs) == 18
    first = tabs[0]
    assert first["captured"] is True
    assert first["call_fn"] == "t.test"
    assert first["line"] == 4 and first["line_seq"] == 1
    assert list(first["data"].columns) == [
        "label",
        "t",
        "df",
        "p",
        "mean of x",
        "mean of y",
        "conf.low",
        "conf.high",
    ]
    # full precision survives, formatted as R's format(digits = 15)
    assert first["data"]["t"].tolist() == ["1.555973210431"]
    lm = next(t for t in tabs if t["analysis"] == "lm")
    assert lm["data"]["label"].tolist() == ["(Intercept)", "gb"]
    assert lm["model_ref"] == "d"  # summary(m) -> m -> lm(..., data = d)
    by = [t for t in tabs if t["analysis"].endswith(" by group")]
    assert [t["analysis"] for t in by] == [
        "mean by group",
        "sd by group",
        "median by group",
        "mean by group",
    ]
    assert list(by[1]["data"].columns) == ["label", "sd (y)", "sd (z)"]


def test_captures_to_tables_edge_cases() -> None:
    assert _r_captures_to_tables(None) == []
    assert _r_captures_to_tables([]) == []
    tabs = _r_captures_to_tables(
        [
            {"analysis": "x", "rows": [{"label": "a", "stats": {"p": 0.5}}], "line": 3},
            {"analysis": "y", "rows": [{"label": "b", "stats": {"p": 0.2}}], "line": 3},
            {"analysis": "z", "rows": [{"stats": {"inf": float("inf"), "s": "abc"}}]},
            {"analysis": "empty", "rows": []},
        ]
    )
    assert [t["line_seq"] for t in tabs] == [1, 2, None]
    row = tabs[2]["data"].iloc[0].tolist()
    assert row[:2] == ["", "Inf"] and pd.isna(row[2])


def test_merge_captures(data_dir: Path) -> None:
    code = _lines(data_dir / "capture_script.R")
    caps = _r_captures_to_tables(
        _read_captures(data_dir / "capture_script.captures.json"), code_lines=code
    )
    txt = read_r_output(_lines(data_dir / "capture_script.Rout"), code_lines=code)
    merged = _r_merge_captures(caps, txt)
    cap_lines = {t["line"] for t in caps}
    assert all(t.get("captured") for t in merged if t["line"] in cap_lines)
    # the loop's printed tests are only visible in the text output
    loop_line = code.index("for (k in 1:2) print(t.test(x + k, y))") + 1
    loop = [t for t in merged if t["line"] == loop_line]
    assert loop and not any(t.get("captured") for t in loop)
    assert [t["line_seq"] for t in loop] == list(range(1, len(loop) + 1))
    assert _r_merge_captures(None, txt) == txt
    assert _r_merge_captures(caps, []) == caps
    # text tables without a line are dropped while captures exist
    no_line = read_r_output(_lines(data_dir / "capture_script.Rout"))
    assert len(_r_merge_captures(caps, no_line)) == len(caps)


def test_read_captures_missing(tmp_path: Path) -> None:
    assert _read_captures(tmp_path / "missing.json") is None
    bad = tmp_path / "bad.json"
    bad.write_text("{oops")
    assert _read_captures(bad) is None


def test_helpers_source(stato_vocab: None) -> None:
    src = _r_capture_helpers()
    for name in [".r_cap_htest", ".r_cap_coef_matrix", ".r_cap_anova", ".r_call_fun_arg"]:
        assert f"`{name}` <-" in src
    assert "stato_type_column <-" in src


# ---------------------------------------------------------------------------
# Running R (needs Rscript)
# ---------------------------------------------------------------------------


@pytest.mark.r
def test_runner_matches_metacheck(data_dir: Path, rscript: str, stato_vocab: None) -> None:
    res = _r_capture_run(data_dir / "capture_script.R", rscript=rscript)
    assert res.error is None
    # stdout is read back like paste(readLines(f), collapse = "\n")
    text = (data_dir / "capture_script.Rout").read_text(encoding="utf-8")
    assert res.stdout == text[:-1]
    _assert_same(res.captures, _read_captures(data_dir / "capture_script.captures.json"))


def _assert_same(a: object, b: object) -> None:
    """Structural equality with a tolerance for doubles (BLAS/R builds differ)."""
    if isinstance(a, dict) and isinstance(b, dict):
        assert list(a) == list(b)
        for k in a:
            _assert_same(a[k], b[k])
    elif isinstance(a, list) and isinstance(b, list):
        assert len(a) == len(b)
        for x, y in zip(a, b, strict=True):
            _assert_same(x, y)
    elif isinstance(a, float) or isinstance(b, float):
        assert a == pytest.approx(b, rel=1e-9, abs=1e-12)
    else:
        assert a == b


@pytest.mark.r
def test_runner_error_and_timeout(tmp_path: Path, rscript: str, stato_vocab: None) -> None:
    script = tmp_path / "err.R"
    script.write_text("t.test(c(1, 2, 3, 4), c(2, 3, 4, 9))\nx <- undefined_var + 1\n")
    res = _r_capture_run(script, rscript=rscript)
    assert isinstance(res.error, RSubprocessError)
    assert "object 'undefined_var' not found" in str(res.error)
    assert str(res.error).startswith("! in callr subprocess.")
    assert res.captures is not None and len(res.captures) == 1
    assert res.stdout.endswith("> x <- undefined_var + 1")

    slow = tmp_path / "slow.R"
    slow.write_text("Sys.sleep(20)\n")
    res = _r_capture_run(slow, timeout=1, rscript=rscript)
    assert isinstance(res.error, RSubprocessTimeout)
    assert res.captures is None


@pytest.mark.r
def test_model_object_unites_model_refs(
    fixtures_dir: Path, tmp_path: Path, rscript: str, stato_vocab: None
) -> None:
    """The .r_call_object_ref() regression test of test-module-reproducibility_check.R."""
    repro = fixtures_dir / "repro"
    for f in ("model_object.R", "data.csv"):
        shutil.copy(repro / f, tmp_path / f)
    script = tmp_path / "model_object.R"
    res = _r_capture_run(script, rscript=rscript)
    assert res.error is None
    code = _lines(script)
    caps = _r_captures_to_tables(res.captures, source_label="model_object.R", code_lines=code)
    txt = read_r_output(res.stdout, source_label="model_object.R", code_lines=code)
    merged = _r_merge_captures(caps, txt)
    refs = {t["model_ref"] for t in merged if t["model_ref"] is not None}
    assert len(refs) == 1
    assert any(any("F" in str(c) for c in t["data"].columns) for t in merged)


@pytest.mark.r
def test_child_reducers(rscript: str, stato_vocab: None) -> None:
    rec = _r_cap_htest("t.test(extra ~ group, data = sleep)", rscript=rscript)
    assert rec["analysis"] == "Welch Two Sample t-test"
    assert rec["rows"][0]["label"] == "extra by group"
    assert set(rec["rows"][0]["stats"]) >= {"t", "df", "p", "conf.low", "conf.high"}
    cf = _r_cap_coef_matrix(
        "summary(lm(mpg ~ wt, data = mtcars))$coefficients", "lm", rscript=rscript
    )
    assert [r["label"] for r in cf["rows"]] == ["(Intercept)", "wt"]
    an = _r_cap_anova("anova(lm(mpg ~ wt, data = mtcars))", "anova", rscript=rscript)
    assert [r["label"] for r in an["rows"]] == ["wt", "Residuals"]
    assert (
        _r_call_fun_arg("aggregate(mpg ~ cyl, data = mtcars, FUN = mean)", rscript=rscript)
        == "mean"
    )
    assert _r_call_fun_arg("tapply(mtcars$mpg, mtcars$cyl, sd)", rscript=rscript) == "sd"
    assert _r_call_fun_arg("mean(x)", rscript=rscript) is None
    # an all-numeric aggregate with default row names has no group identity
    assert (
        _r_cap_by_fun("aggregate(mpg ~ cyl, data = mtcars, FUN = mean)", "mean", rscript=rscript)
        is None
    )
    by = _r_cap_by_fun("tapply(mtcars$mpg, mtcars$cyl, mean)", "mean", rscript=rscript)
    assert by["analysis"] == "mean by group"
    assert [r["label"] for r in by["rows"]] == ["4", "6", "8"]
    assert _r_capture_value("mtcars$mpg", "mtcars$mpg", rscript=rscript) is None
    val = _r_capture_value(
        "tapply(mtcars$mpg, mtcars$cyl, mean)",
        "tapply(mtcars$mpg, mtcars$cyl, mean)",
        rscript=rscript,
    )
    assert val["analysis"] == "mean by group"
    assert _r_capture_value("shapiro.test(mtcars$mpg)", rscript=rscript)["method"] == (
        "Shapiro-Wilk normality test"
    )
