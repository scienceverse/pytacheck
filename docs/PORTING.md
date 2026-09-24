# Porting metacheck to pytacheck

pytacheck is a Python port of [metacheck](https://github.com/scienceverse/metacheck)
(`dev` branch). **Correctness is defined as agreement with metacheck**: every ported
function and module is checked against golden outputs produced by running the real R
package (see [PARITY.md](PARITY.md)). This document is the rulebook for humans and AI
agents porting code, including the automated upstream-sync workflow.

The pinned upstream lives in the `upstream/metacheck` git submodule; its commit is
recorded in `parity/UPSTREAM.toml` and `src/pytacheck/_version.py`.

## 1. Golden rules

1. **Reproduce R's behaviour exactly**, including quirks. Same rows, same row order,
   same columns in the same order, same strings (summary texts, report prose), same
   traffic lights. If R has an obvious bug, reproduce it and record it in
   `docs/UPSTREAM_ISSUES.md` (so it can be reported upstream); only diverge when the R
   behaviour is clearly unintended *and* harmful, and then mark the parity case with
   `known_divergence: "<reason>"`.
2. **Every ported function/module gets parity cases** (`parity/cases/<area>.yaml`) and
   its goldens are generated from R (`python -m parity generate --area <area>`) — never
   written by hand. Every module needs ≥ 3 cases: the demo paper, the psychsci fixtures
   (a paper list), and synthetic `test_paper` edge cases (no hits, several hits,
   tricky text). Cover every branch that changes the traffic light.
3. **Port the testthat tests** that cover your code to pytest under `tests/<area>/`.
4. **No network in tests.** Mock HTTP with `respx`; metacheck's recorded API responses
   (`upstream/metacheck/tests/testthat/apis*`, httptest2 format) can be replayed. Tests
   that truly need the network are marked `@pytest.mark.network`.
5. **Never mutate inputs.** Modules and functions must not modify the paper, tables or
   lists they receive (tables are returned by reference). `tests/foundation/
   test_modules_contract.py` enforces this for modules.
6. **Keep `import pytacheck` fast.** Import heavy/optional dependencies (scipy, lxml,
   pyreadstat, httpx, ...) inside functions, not at module top level, in modules that
   the top-level package imports eagerly.
7. Typed, documented code: type hints on public functions, a docstring naming the R
   function it ports (`"""Port of R/text_search.R::text_search()."""` style), `ruff`
   clean.

## 2. Where code goes

| R source | Python package |
|---|---|
| `R/paper.R`, `R/import-read.R`, `R/validate.R` (paper validation) | `pytacheck.papers` |
| `R/import-bibr.R`, `R/import-grobid.R`, `R/import-convert.R`, `R/import-papers.R`, `R/svutils-xml.R` | `pytacheck.io` |
| `R/text_search.R`, `R/text_expand.R`, `R/text-*.R`, `R/extract-tests.R`, `R/causal_sentences.R` | `pytacheck.text` |
| `R/stats.R`, `R/stat_helpers.R` (+ the statcheck package) | `pytacheck.stats` |
| `R/report*.R`, `R/html-output.R`, `inst/templates` | `pytacheck.report` |
| `R/llm*.R`, `R/cap-prompt.R` | `pytacheck.llm` |
| `R/doi.R`, `R/db-*.R`, `R/regcheck-local.R`, `R/svutils-orcid.R` | `pytacheck.db` |
| `R/archive-*.R`, `R/repo-*.R`, `R/zip-peek.R`, `R/cache.R`, `R/utils.R` (HTTP helpers) | `pytacheck.archives` |
| `R/code_check.R` | `pytacheck.codecheck` |
| `R/data_check_helpers.R`, `R/file_category.R`, `R/file-naming.R`, `R/scales.R`, `R/tasks.R` | `pytacheck.datacheck` |
| `R/spv.R`, `R/jasp.R`, `R/omv.R`, `R/mplus.R`, `R/stata.R`, `R/r-output.R`, `R/r-capture.R`, `R/stat-tables.R`, `R/stat-output.R`, `R/match-*.R`, `R/stato-map.R` | `pytacheck.statout` |
| `R/reproducibility_check*.R` | `pytacheck.repro` |
| `inst/plumber` | `pytacheck.api` (FastAPI) |
| `inst/modules/<name>.R` | `pytacheck/modules/<name>.py` |
| `inst/databases`, `data/*.rda`, `inst/schema`, `inst/demos` | `pytacheck/resources/` |

Record every mapping in `porting/map/<area>.toml` (the upstream-sync workflow uses it
to find the Python code to update when an R file changes):

```toml
[files]
"R/text_search.R" = ["src/pytacheck/text/search.py"]

[functions]
text_search = "pytacheck.text.search:text_search"
```

Public functions are exported lazily from `pytacheck/__init__.py` (`_EXPORTS`), with
the R name where it is a valid Python identifier.

## 3. Translating R to Python

### Regular expressions — always via `pytacheck._r`

Never call `re`, `regex` or pandas `.str.contains/.extract/.replace/.match/.split`
with a pattern. Use the R-faithful helpers, which emulate TRE (`perl = FALSE`, the
default: POSIX leftmost-longest, Unicode `\w`, literal backslashes inside `[...]`, `.`
matching newlines, `$` only at the very end) and PCRE (`perl = TRUE`: ASCII `\w \d \s
\b`):

| R | Python |
|---|---|
| `grepl(p, x, ignore.case, perl, fixed)` | `grepl(p, x, ignore_case=, perl=, fixed=)` |
| `sub()` / `gsub()` | `sub()` / `gsub()` (R replacement syntax: `\\1`, `\\U`...) |
| `regmatches(x, regexpr(p, x))` | `regextract(p, x)` → aligned, `None` = no match (R *drops* non-matches: filter them) |
| `regmatches(x, gregexpr(p, x))` | `regextract_all(p, x)` |
| `regmatches(x, regexec(p, x))` | `regexec(p, x)` → `[match, g1, g2, ...]` or `[]` |
| `gregexpr(p, x)` positions | `gregexpr_all(p, x)` → `[(start1, len), ...]` (1-based starts) |
| `strsplit(x, s)` | `strsplit(x, s)` (R quirks: no trailing `""`, re-anchoring) |
| `grep(p, x, value = TRUE)` | `grep(p, x, value=True)` (0-based indices) |
| `trimws()` | `trimws()` |

Helpers are vectorised: pass a list or Series (a Series keeps its index), get the same
back. Compiled patterns are cached, so calling in a loop is cheap.

**String literals.** Translate the R *string literal* first, then the regex: R
`"\\d+\\.\\s"` is the regex `\d+\.\s`, written as the Python raw string `r"\d+\.\s"`.
But R `"\t"`, `"\n"`, `" "` are real characters: write them as normal Python
strings `"\t"`, `"\n"`, `" "` (so `"[ \t\r\n]"`, *not* `r"[ \t\r\n]"`, which
inside a TRE bracket means backslash and letters).

### Numbers → text

R's `paste()`/`as.character()` of a double uses 15 significant digits and may switch
to scientific notation (`1e+05`); `format()` uses 7. Use `pytacheck._r.as_character`
and `pytacheck._r.format_num(x, digits)`; `sprintf("%.2f", x)` is `f"{x:.2f}"`,
`sprintf("%d", n)` is `f"{n:d}"`, `sprintf("%s", x)` is `as_character(x)` (and `"NA"`
for missing). R `round()` matches Python `round()`; `signif()` is `_r.signif`.
`plural(n)` is `_r.plural`.

### Missing values and types

Tables use pandas nullable dtypes, mirroring R vectors: `string` (character),
`Int64` (integer), `float64` (double; `NaN` is `NA`), `boolean` (logical), `object`
(list columns: Python lists / dicts). Logical operators on `boolean` Series follow
R's three-valued logic. Remember:

* `x %in% y` → `s.isin(y)` (NA is not in `y` unless `y` has NA);
* `is.na(x)` → `s.isna()`; `ifelse(test, a, b)` propagates NA;
* `df[cond, ]` with NA in `cond` gives NA rows in R but `dplyr::filter()` drops them —
  check which one the R code uses;
* `nrow(NULL)` is `NULL`; `length(NULL)` is 0; absent paper tables are `None`.

### dplyr and base data frames

| R | Python |
|---|---|
| `dplyr::count(df, a, name = "n")` | `pytacheck._r.count(df, "a", name="n")` (sorted, C locale) |
| `dplyr::bind_rows(...)` | `pytacheck._r.bind_rows([...])` |
| `summarise(..., .by = c(a, b))` | `groupby([...], sort=False, dropna=False)` (first-appearance order) |
| `group_by(a) |> summarise()` | `groupby(..., sort=True, dropna=False)` (sorted, C locale) |
| `arrange(a)` | `sort_values("a", kind="stable", na_position="last")` |
| base `sort()`/`order()` on strings | `sorted(key=pytacheck._r.r_sort_key)` (ICU-like collation) |
| `left_join(x, y, by)` | `x.merge(y, on=by, how="left", sort=False)` — R joins int/double keys; align dtypes first |
| `semi_join` / `anti_join` | membership masks (keep x's order) |
| `unique(df)` / `distinct()` | `drop_duplicates()` |
| `df[c(), ]` / zero-row results | `df.iloc[0:0]` — keep R's columns and dtypes even when empty |
| `data.frame(a = ..., b = ...)` | same column order; `stringsAsFactors = FALSE` |

Build new columns in the same order R does; column order is compared.

### Indices

Python is 0-based, but **data values** (`text_id`, `bib_id`, `section_id`,
`paragraph_id`, positions stored in tables) keep R's values. Only Python-level
indexing changes.

### Function arguments

Keep R argument names with `.` → `_` (`ignore.case` → `ignore_case`) and a trailing
`_` for Python keywords (`return` → `return_`). Keep R's defaults. Where R uses
`match.arg()`, validate the value and raise `ValueError` with R's message.

### Errors, warnings and messages

`stop(msg)` → raise an appropriate exception with the same message (`ValueError`,
`TypeError`, `RuntimeError`, `FileNotFoundError`...). `warning()` → `warnings.warn()`.
`message()` progress chatter → nothing, or `rich` output gated on
`pytacheck.config.verbose()`. `logger()` → `pytacheck.log.logger()`.

## 4. Porting a module

See `src/pytacheck/modules/marginal.py` for the reference pattern:

```python
@module(
    title="Marginal Significance",          # roxygen title
    description="...",                     # @description
    details="""...""",                     # @details (keep <validation> blocks)
    keywords=["results"],                  # @keywords: first section keyword = report section;
                                           # add "llm" / "network" if the module needs them
    author=["Daniel Lakens <D.Lakens@tue.nl>"],
    params={"paper": "a paper object or paperlist object", ...},   # @param
)
def marginal(paper, ...):                  # same name as the file; R's arguments + defaults
    ...
    return {"table": ..., "summary_table": ..., "na_replace": 0,
            "traffic_light": tl, "summary_text": ..., "report": [...]}
```

* Return the same list elements, in the same order, as the R module.
* `report` is a string or a list of blocks: markdown strings, `scroll_table(df, ...)`,
  `collapse_section([...])`. Prose must match R exactly (it is compared).
* `format_ref(bibentry)` in R: run R once to get the HTML (`cat(format_ref(x))`) and
  store it as a string constant, as `marginal.py` does.
* `get_prev_outputs("data_check", "table")` works the same inside a module run.
* LLM-backed modules must work without an API key when R does (e.g. fallbacks), and
  parity cases for LLM paths use the LLM cache or are marked `skip_r`.

### HTTP

All network access goes through `pytacheck.http` (port of `.batch_query()` and the
httr2 retry policy): `http.request(method, url, ...)` returns the response (error
statuses are returned, not raised) or `None` after connection failures;
`http.batch_query(urls, ...)` fetches many URLs politely; `http.skip_on_api_limit()`
reproduces the `skip_on_api_limit` option; `http.Throttle` is `req_throttle()`.
Never create your own `httpx.Client`.

Tests replay metacheck's recorded responses exactly (httptest2 file naming is
reproduced, including R's `digest()` hashes):

```python
from tests.httpmock import replay

def test_github_readme(upstream_dir):
    with replay("apis"):          # or "apis_papers_retag", ...
        ...
```

Unrecorded requests get a 404, never the network. Parity cases must not use the
network either; test network code through its parsing helpers with fixture inputs.

## 5. Performance

pytacheck must be substantially faster than metacheck on large corpora:

* Operate on whole tables (a paper list's `paper_table()`), not paper by paper.
* Vectorise with list comprehensions over compiled patterns; avoid
  `DataFrame.apply(axis=1)`, `iterrows()` and building DataFrames inside loops.
* Never recompile regexes in loops (the `_r` helpers cache; `compile_r()` for custom loops).
* Load bundled data files once (`functools.cache`).
* HTTP: reuse one `httpx.Client` with HTTP/2 and connection pooling; respect rate
  limits; cache responses on disk as metacheck does.

## 6. Working rules for parallel porting agents

* Only create/edit files in your assigned area (source package, `tests/<area>/`,
  `parity/cases/<area>*.yaml`, `parity/golden/<area>*/`, `porting/map/<area>.toml`).
* Foundation files (`src/pytacheck/_r/`, `papers/`, `module.py`, `text/search.py`,
  `text/expand.py`, `report/blocks.py`, `parity/*.py`, `parity/r/`) may only receive
  minimal bug fixes; say so in your report.
* Do not edit `pyproject.toml`, `pytacheck/__init__.py` or `uv.lock`; list new
  dependencies and exports in your report instead. Do not `pip install`.
* Do not commit; the orchestrator commits.
* Use the R reference at `$PYTACHECK_RSCRIPT` to explore R behaviour
  (`LANG=C.UTF-8 $PYTACHECK_RSCRIPT -e 'library(metacheck); ...'`).
