"""Tests for metacheck.statout.r_output (port of R/r-output.R)."""

from __future__ import annotations

from pathlib import Path

from metacheck._values import as_float
from metacheck.statout.r_output import (
    _make_unique,
    _r_call_fn,
    _r_call_object_ref,
    _r_echo_chunks,
    _r_is_preview_call,
    _r_output_cohend,
    _r_output_effectsize_d,
    _r_output_oneline,
    _r_output_tables,
    _r_root_ref_map,
    _repro_split_args,
    read_r_output,
)


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").rstrip("\n").split("\n")


def test_read_r_output_null_and_empty() -> None:
    assert read_r_output(None) == []
    assert read_r_output([]) == []
    assert read_r_output("") == []


def test_read_r_output_attributes_lines(data_dir: Path) -> None:
    res = read_r_output(
        _lines(data_dir / "analysis.Rout"), code_lines=_lines(data_dir / "analysis.R")
    )
    code = _lines(data_dir / "analysis.R")
    # the fixed-width "sample estimates" table comes first, then the one-liner
    assert res[0]["analysis"] == "sample estimates"
    assert list(res[0]["data"].columns) == ["mean in group 1", "mean in group 2"]
    welch = res[1]
    assert welch["analysis"] == "Welch Two Sample t-test"
    assert welch["line"] == res[0]["line"] and welch["line_seq"] == 2
    assert list(welch["data"].columns) == ["t", "df", "p-value"]
    assert welch["data"].iloc[0].tolist() == ["-1.8608", "17.776", "0.07939"]
    assert code[welch["line"] - 1] == "t.test(extra ~ group, data = d)"
    assert welch["call_fn"] == "t.test"
    assert welch["model_ref"] == "sleep"  # d <- sleep
    # head(d) is a data preview and is never extracted
    assert all(r["line"] is None or code[r["line"] - 1] != "head(d)" for r in res)
    # source(echo = TRUE) deparses statements: a deparse that differs from the
    # script beyond whitespace (the for loop, the wrapped with() call) is not
    # attributed, but results sharing one statement are still numbered.
    unattributed = [r for r in res if r["line"] is None]
    assert [r["line_seq"] for r in unattributed] == [1, 2, 1, 1, 1, 2, 3, 4]
    # a statement whose author omitted spaces still matches its echo
    assert any(code[r["line"] - 1] == "res" for r in res if r["line"] is not None)


def test_read_r_output_single_string_matches_lines(data_dir: Path) -> None:
    lines = _lines(data_dir / "analysis.Rout")
    code = _lines(data_dir / "analysis.R")
    a = read_r_output(lines, code_lines=code)
    b = read_r_output("\n".join(lines), code_lines=code)
    assert len(a) == len(b)
    for x, y in zip(a, b, strict=True):
        assert x["line"] == y["line"]
        assert x["data"].equals(y["data"])


def test_read_r_output_without_code_lines(data_dir: Path) -> None:
    res = read_r_output(_lines(data_dir / "analysis.Rout"))
    assert res
    assert all(r["line"] is None and r["call_fn"] == "" and r["model_ref"] is None for r in res)
    assert [r["line_seq"] for r in res] == list(range(1, len(res) + 1))


def test_read_r_output_model_refs(data_dir: Path) -> None:
    res = read_r_output(
        _lines(data_dir / "model_chain.Rout"), code_lines=_lines(data_dir / "model_chain.R")
    )
    refs = {r["model_ref"] for r in res if r["line"] in (4, 6, 7)}
    # anova(m), s$coefficients[...] (s <- summary(m)) and m |> summary() share
    # one root: m -> dat (its data argument) -> mtcars (dat <- mtcars).
    assert refs == {"mtcars"}


def test_read_r_output_ansi_fused_prompt(data_dir: Path) -> None:
    text = (data_dir / "synthetic.Rout").read_text(encoding="utf-8")
    code = _lines(data_dir / "synthetic.R")
    res = read_r_output(text, code_lines=code)
    fr = [r for r in res if r["analysis"] == "Welch Two Sample t-test"]
    # the prompt glued to the colour reset is split back onto its own line
    assert fr and fr[0]["line"] == 5
    assert fr[0]["model_ref"] == "x"
    effect = [r for r in res if r["analysis"] == "Cohen's d" and "ci_lower" in r["data"].columns]
    assert effect[0]["data"].iloc[0].tolist() == ["-0.12", "-0.73", "0.51"]
    anova = next(r for r in res if r["line"] == 6)
    assert anova["data"]["df"].tolist() == ["2, 560", "1, 280"]


def test_r_echo_chunks() -> None:
    lines = ["> x <- c(1,", "+   2)", "> x", "[1] 1 2", "> y <- 1", "> summary( m )", "out"]
    code = ["x <- c(1,", "  2)", "x", "y <- 1", "summary(m)"]
    chunks = _r_echo_chunks(lines, code)
    assert chunks == [
        {"line": 3, "call": "x", "output": ["[1] 1 2"]},
        {"line": 5, "call": "summary( m )", "output": ["out"]},
    ]
    assert _r_echo_chunks(["no", "prompts"], code) == []


def test_r_call_fn_and_preview() -> None:
    assert _r_call_fn("res <- t.test(a ~ b)") == "t.test"
    assert _r_call_fn("wilcox.test(x); t.test(y)") == "wilcox.test"
    assert _r_call_fn("d %>% t_test(score ~ group)") == "t.test"
    assert _r_call_fn("summary(lm(y ~ x))") == ""
    assert _r_call_fn("") == ""
    assert _r_is_preview_call("utils::head(df, 3)")
    assert not _r_is_preview_call("x <- head(d)")
    assert not _r_is_preview_call("stats::head(d)")


def test_r_call_object_ref() -> None:
    assert _r_call_object_ref("summary(m_vid)") == "m_vid"
    assert _r_call_object_ref("m_vid <- lmer(y ~ x)") is None
    assert _r_call_object_ref("m_vid |> summary()") == "m_vid"
    assert _r_call_object_ref("eta_squared(mc_model$anova_table$F[1])") == "mc_model"
    assert _r_call_object_ref('emmeans(specs = "team", object = mc_model)') == "mc_model"
    assert _r_call_object_ref("summary(update(m_vid, . ~ . - x))") is None
    assert _r_call_object_ref("x == 5") is None
    assert _r_call_object_ref("f(unbalanced") is None


def test_repro_split_args() -> None:
    args = _repro_split_args('a, f(b, c), "x,y", d[1, 2]')
    assert [a["text"] for a in args] == ["a", " f(b, c)", ' "x,y"', " d[1, 2]"]
    assert args[1]["start"] == 3 and args[1]["end"] == 10
    assert _repro_split_args("") == []


def test_r_root_ref_map() -> None:
    m = _r_root_ref_map(
        ["m <- lmer(y ~ x, data = dat)", "r2 <- f(m)[1,2]", "ci = g(r2)", "a <- b", "b <- a"]
    )
    assert m == {"m": "dat", "r2": "dat", "ci": "dat", "a": "a", "b": "b"}
    assert _r_root_ref_map(["x <- 5"]) == {}


def test_oneline_cohend_effectsize_tables() -> None:
    one = _r_output_oneline(["\tShapiro-Wilk normality test", "W = 0.94756, p-value = 0.1229"])
    assert one[0]["title"] == "Shapiro-Wilk normality test"
    assert one[0]["data"].iloc[0].tolist() == ["0.94756", "0.1229"]
    # "p < .05" has no leading digit, so the value pattern does not match it
    dup = _r_output_oneline(["F(2, 27) = 4.50, p < .05, t = 2, t = 3"])
    assert list(dup[0]["data"].columns) == ["F", "df", "t", "t.1"]
    coh = _r_output_cohend(
        [
            "d estimate: 0.1811285 (negligible)",
            "95 percent confidence interval:",
            "    lower     upper ",
            "-0.760357  1.122614 ",
        ]
    )
    assert coh[0]["title"] == "Cohen's d (negligible)"
    assert list(coh[0]["data"].columns) == ["d", "lower", "upper"]
    eff = _r_output_effectsize_d(
        ["Hedges' g |         95% CI", "--------------------------", "0.45      | [ 0.02,  0.88]"]
    )
    assert eff[0]["analysis"] == "Hedges' g"
    assert eff[0]["data"].iloc[0].tolist() == ["0.45", "0.02", "0.88"]
    tabs = _r_output_tables(
        [
            "Coefficients:",
            "            Estimate Std. Error t value Pr(>|t|)    ",
            "(Intercept)   17.147      1.125  15.247 1.13e-15 ***",
            "gb             7.245      1.764   4.106 0.000285 ***",
            "---",
        ]
    )
    assert tabs[0]["analysis"] == "Coefficients"
    assert list(tabs[0]["data"].columns) == ["V1", "Estimate", "Std. Error", "t value", "Pr(>|t|)"]


def test_r_helpers() -> None:
    assert _make_unique(["a", "a", "a.1", "a", "b", "a.1"]) == [
        "a",
        "a.2",
        "a.1",
        "a.3",
        "b",
        "a.1.1",
    ]
    assert _make_unique(["", "", "V1"]) == ["", ".1", "V1"]
    assert as_float(" 5 ") == 5.0
    assert as_float("0x1A") == 26.0
    assert as_float("1e") is None
    assert as_float("NA") is None
    assert as_float("Infinity") == float("inf")
