"""The static chunk extractor behind ``code_extract_r()`` (UPSTREAM_ISSUES D80)."""

from __future__ import annotations

import pytest

from metacheck.codecheck._chunks import extract_r
from metacheck.codecheck.core import code_extract_r


def _rmd(header: str, *body: str) -> list[str]:
    return ["Intro", header, *(body or ("x <- 1", "y <- 2")), "```", "End"]


CODE = ["x <- 1", "y <- 2"]
COMMENTED = ["# x <- 1", "# y <- 2"]


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("```{r}", CODE),
        ("```{r, eval=FALSE}", COMMENTED),
        ("```{r, eval = F}", COMMENTED),
        ("```{r label, purl=FALSE}", []),
        ("```{r, error=TRUE}", ["try({", *CODE, "})"]),
        ("```{r, eval=FALSE, eval=TRUE}", CODE),  # a later option wins
        ("```{r, eval=TRUE, eval=FALSE}", COMMENTED),
        ("```{r, eval=0}", CODE),  # only FALSE comments the code out
        ("```{r, fig.cap='a, b', eval=FALSE}", COMMENTED),
        # R expressions are not evaluated: the option counts as not given
        ("```{r, eval=params$run}", CODE),
        ("```{r, eval=knitr::is_latex_output()}", CODE),
        ("```{r, purl=interactive()}", CODE),
        ("```{r, engine=paste0('p', 'y')}", CODE),
        # chunks of other engines are commented out with their comment prefix
        ("```{python}", ["## x <- 1", "## y <- 2"]),
        ("```{r, engine='bash'}", ["## x <- 1", "## y <- 2"]),
        ("```{python, comment='#>'}", ["#> x <- 1", "#> y <- 2"]),
        ("```{python, comment=NA}", CODE),
        ("```{python, comment=f(x)}", ["## x <- 1", "## y <- 2"]),
        # malformed options do not stop the extraction
        ("```{r, bad syntax=}", CODE),
        ("```{r, a, b}", CODE),
    ],
)
def test_chunk_options(header: str, expected: list[str]) -> None:
    assert code_extract_r(text=_rmd(header)) == expected


def test_yaml_chunk_options() -> None:
    doc = ["```{r}", "#| label: a", "#| eval: false", "", "x <- 1", "```"]
    assert extract_r(doc) == ["# x <- 1"]
    assert extract_r(doc, 1) == [
        "## " + "-" * 77,
        "#| label: a",
        "#| eval: false",
        "",
        "# x <- 1",
        "",
    ]
    assert extract_r(["```{r}", "#| eval: !expr params$x", "x", "```"]) == ["x"]
    assert extract_r(["```{r}", "#| error: true", "x", "```"]) == ["try({", "x", "})"]
    assert extract_r(["```{r}", "#| eval=FALSE, echo=FALSE", "x", "```"]) == ["# x"]
    assert extract_r(["```{js}", "//| echo: false", "x", "```"]) == ["## x"]


def test_references() -> None:
    doc = [
        "```{r setup}",
        "a <- 1",
        "```",
        "```{r}",
        "<<setup>>",
        "  <<two>>",
        "<<unknown>>",
        "```",
        "```{r}",
        "#| label: two",
        "b <- 2",
        "<<setup>>",
        "```",
    ]
    assert extract_r(doc) == [
        "a <- 1",
        "a <- 1",
        "  b <- 2",
        "  a <- 1",
        "<<unknown>>",
        "b <- 2",
        "a <- 1",
    ]
    # a reference back to a chunk being expanded stays as written
    doc = ["```{r a}", "<<b>>", "```", "```{r b}", "<<a>>", "```"]
    assert extract_r(doc) == ["<<b>>", "<<a>>"]


def test_fences() -> None:
    # a longer fence shows a chunk verbatim
    doc = ["````{r}", "x <- 1", "```", "y", "````", "text"]
    assert extract_r(doc) == ["x <- 1", "```", "y"]
    # a fence of another length closes the chunk when no matching one follows
    assert extract_r(["```{r}", "x", "````", "text"]) == ["x"]
    assert extract_r(["```{r}", "x", "````", "```"]) == ["x", "````"]
    # a new chunk ends one that was not closed
    assert extract_r(["```{r}", "x", "```{r}", "y", "```"]) == ["x", "y"]
    # indented and quoted chunks
    assert extract_r(["  ```{r}", "  x", "  ```"]) == ["x"]
    assert extract_r(["> ```{r}", "> x", "> ```"]) == ["x"]


def test_documentation_levels() -> None:
    doc = ["---", "title: T", "---", "Text", "```{r a, echo=FALSE}", "x", "```", "More"]
    assert extract_r(doc, 0) == ["x"]
    assert extract_r(doc, 1) == ["## ----a, echo=FALSE" + "-" * 56 + "----", "x", ""]
    assert extract_r(doc, 2) == [
        "#' ---",
        "#' title: T",
        "#' ---",
        "#' Text",
        "## ----a, echo=FALSE" + "-" * 56 + "----",
        "x",
        "",
        "#' More",
    ]


def test_other_syntaxes() -> None:
    assert extract_r(["<<a, eval=FALSE>>=", "x", "@", "<<>>=", "<<a>>", "@"]) == ["# x", "x"]
    assert extract_r(["% begin.rcode", "% x <- 1", "%   y", "% end.rcode"]) == ["x <- 1", "  y"]
    assert extract_r(["<!--begin.rcode", "x", "end.rcode-->"]) == ["x"]
    assert extract_r(["Just text", "with `r 1` inline code"]) == []
    assert extract_r(["Just text"], 2) == []


def test_params() -> None:
    doc = [
        "---",
        "params:",
        "  a: 1",
        "  b: hello",
        "  c: [1, 2.5]",
        "  d: !r Sys.Date()",
        "  e: {value: true}",
        "  f: null",
        "  my name: 2020-01-01",
        "---",
        "```{r}",
        "x",
        "```",
    ]
    assert extract_r(doc) == [
        "params <-",
        'list(a = 1L, b = "hello", c = c(1, 2.5), d = Sys.Date(), e = TRUE, '
        '`my name` = "2020-01-01")',
        "",
        "x",
    ]
    # a front matter that is not YAML has no params
    assert extract_r(["---", "params: [", "---", "```{r}", "x", "```"]) == ["x"]


def test_na_and_multiline_elements() -> None:
    assert extract_r(["```{r}", None, "```"]) == ["NA"]
    assert extract_r(["```{r}\nx\r\ny\n```"]) == ["x", "y"]
    assert extract_r(["```{python}", "```"]) == []
