# Porting metacheck to pytacheck

pytacheck is a Python rewrite of [metacheck](https://github.com/scienceverse/metacheck)
(`dev` branch) that aims to improve on it: **outputs at least as accurate as
metacheck's, nothing invented, and a leaner, faster code base.** Accuracy is validated
against the real R package: every ported function and module is checked against
golden outputs produced by running metacheck (see [PARITY.md](PARITY.md)), and every
difference is either within the margins of section 1 or a documented divergence. This
document is the rulebook for humans and AI agents porting code, including the
automated upstream-sync workflow.

The pinned upstream lives in the `upstream/metacheck` git submodule; its commit is
recorded in `parity/UPSTREAM.toml` and `src/metacheck/_version.py`.

**The pin is `dev` plus pull request
[#423](https://github.com/scienceverse/metacheck/pull/423)** (bibr export schema 12.0:
reading and writing 12.x, Grobid TEI to 12.0), which pytacheck targets before it is
merged. The pin records it as `pull_request = 423` and `base_commit`, the `dev` commit
the pull request is built on. The R reference is installed from the pinned commit, so
pytacheck is compared with the pull request's behaviour. Until it is merged, the
upstream-sync workflow follows the pull request's head (`refs/pull/423/head`) and warns
about `dev` commits the pull request does not contain yet. Those commits are not in the
reference and are not ported. Once the pull request is merged into `dev`, the workflow
goes back to `dev` and drops `pull_request` and `base_commit` from the pin
(`scripts/upstream_sync.py`; `prepare --drop-pr` stops following it by hand).

## 1. The accuracy contract

pytacheck is checked against the real metacheck in R on every change. It is not a bug-for-bug copy of it. It must be at least as accurate as metacheck on real research outputs, and smaller, faster and easier to maintain.

**What must agree with metacheck, within margins, on realistic inputs.** Realistic inputs are real papers, repositories, data files and code files: the corpus in `parity/corpus.toml`.
- What each module checks, how it decides and what it reports: traffic lights, summary tables, summary text, and report text and numbers.
- Extracted statistics, p-values, references, URLs and other extracted tables.
- The public API: exported function and module names, arguments and defaults, returned column names and their order. People port their scripts from metacheck.

"Within margins" means:
- Traffic lights and counts are exact.
- Numbers agree to a relative 1e-9, after R's rounding and formatting, which `metacheck._r` keeps.
- Text agrees after whitespace normalisation.
- Any other difference is documented as described below.

`python -m parity accuracy --gate` measures this, and the upstream sync must pass it.

**Fix metacheck's bugs, don't reproduce them.** Sometimes metacheck is clearly wrong: it crashes on valid input, mis-parses something, gives a wrong count, reports a false positive or negative, or truncates data silently. Then do the right thing, record the bug as a U-entry in `docs/UPSTREAM_ISSUES.md`, and mark the affected cases `r_bug_fixed`. A bug may be kept (reproduced) only with a written reason why fixing it would make results worse.

**Faithful outputs, opinionated internals.** Change what users see only to fix a clear bug, or where a different design is clearly better (`better_logic`, recorded as a D-entry). Inside, write idiomatic Python:
- clear module boundaries and typed results;
- pandas idioms;
- the shared helpers in `metacheck._values`, `metacheck._json` and `metacheck.http` instead of private copies in each file;
- no helper layers that exist only to mimic R.

**Don't emulate R internals.** Document these, never reproduce them:
- R's error, warning and message texts. Python raises its own clear exceptions, and parity only checks that both sides fail.
- Quirks of R's C libraries on malformed, adversarial or synthetic input. Examples: data.table fread quote rules, type bumps and silent truncation; readxl, readODS, readr and vroom; yajl/jsonlite escapes and NULs; TRE and PCRE corner cases; glibc and ICU character tables and charset confidences; knitr's evaluation of chunk options; long-double number parsing.
- R type details users never see: integer vs double, factor vs character, tibble vs data.frame, row names, list-column shapes, int32 overflow.

**The R conventions that do reach users stay:**
- R's number formatting and rounding (`format_num`, `as_character`, `r_round`, `signif`);
- R's string collation for user-visible listings (`r_sort_key`);
- metacheck's regular expressions, with R's replacement syntax;
- R's .rds/.RData format for users' files.

**Prefer mature compiled libraries** to pure-Python ports of R's C code: pandas' C parser, orjson, python-calamine, zipfile, the `regex` module, pyreadstat, lxml. Adding a well-maintained dependency to delete thousands of lines is a good trade. Keep the core install lean: heavy or niche libraries go in extras. On realistic inputs, results must not depend on which extras are installed, unless that is documented.

**Accuracy first.** No change may make realistic results less accurate, and nothing may be invented: no references, statistics, links or LLM-derived claims that are not grounded in the paper or its materials. When in doubt, run the accuracy report.

**Record every difference** that a parity case or the accuracy report sees, as `known_divergence: {kind, ref, reason}` in `parity/divergences/<lane>.yaml` (or inline in a hand-written case file):

| kind | use for | needs |
|---|---|---|
| `r_bug_fixed` | a metacheck/R bug that pytacheck fixes | ref U<n> |
| `better_logic` | pytacheck decides differently on purpose, and better | ref D<n> |
| `deliberate` | a design decision (defaults, security, scope) | ref D<n> |
| `c_quirk` | an R C-library quirk on malformed or synthetic input | reason; tier 2 only |
| `type_detail` | an R type or attribute detail users never see | reason; tier 2 only |
| `r_nondeterministic` | R's own result is undefined or unstable | reason |

A difference that only corrects wording (a typo, a plural, a missing full stop) adds `r_text` substitutions to its mark: they turn R's text into pytacheck's before the comparison, so the case stays a plain pass and is still compared with R for everything else ([PARITY.md](PARITY.md)).

Tier-1 (realistic) cases may not be marked `c_quirk` or `type_detail`: a difference that reaches users on real inputs is not a quirk. Tier-2 (synthetic) cases may be marked in bulk with glob keys. Every mark is pinned by the divergence lock, so a marked case still fails when either side's output changes.

### Golden rules

1. **Follow the accuracy contract above.** At least as accurate as metacheck on real
   inputs, nothing invented, metacheck's bugs fixed and recorded, every difference
   marked, and idiomatic Python inside.
2. **Every ported function and module gets parity cases**
   (`parity/cases/<area>.yaml`), generated from R
   (`python -m parity generate --area <area>`) and never written by hand:
   * Tier-1 cases on the realistic corpus: the demo paper, the psychsci paper list and
     a Grobid TEI paper; real repositories, data and code files for the file checks.
   * Tier-2 edge cases for every branch that changes the traffic light.

   Tier-2 cases check that Python behaves sensibly (no crash where a sensible result
   exists), not that it copies R's internals. Do not write new cases whose only
   purpose is to force exact emulation of an R internal.
3. **Port the testthat tests** that cover your code to pytest under `tests/<area>/`.
4. **No network in tests.** Mock HTTP with `respx`; metacheck's recorded API responses
   (`upstream/metacheck/tests/testthat/apis*`, httptest2 format) can be replayed. Tests
   that truly need the network are marked `@pytest.mark.network`.
5. **Never mutate inputs.** Modules and functions must not modify the paper, tables or
   lists they receive (tables are returned by reference). `tests/foundation/
   test_modules_contract.py` enforces this for modules.
6. **Keep `import metacheck` fast.** Import heavy/optional dependencies (scipy, lxml,
   pyreadstat, httpx, ...) inside functions, not at module top level, in modules that
   the top-level package imports eagerly.
7. Typed, documented code: type hints on public functions, a docstring naming the R
   function it ports (`"""Port of R/text_search.R::text_search()."""` style), `ruff`
   clean.

## 2. Where code goes

| R source | Python package |
|---|---|
| `R/paper.R`, `R/import-read.R`, `R/validate.R` (paper validation) | `metacheck.papers` |
| `R/import-bibr.R`, `R/import-bibr12.R`, `R/import-grobid.R`, `R/import-grobid-bibr12.R`, `R/import-convert.R`, `R/import-papers.R`, `R/svutils-xml.R` | `metacheck.io` |
| `R/text_search.R`, `R/text_expand.R`, `R/text-*.R`, `R/extract-tests.R`, `R/causal_sentences.R` | `metacheck.text` |
| `R/stats.R`, `R/stat_helpers.R` (+ the statcheck package) | `metacheck.stats` |
| `R/report*.R`, `R/html-output.R`, `inst/templates` | `metacheck.report` |
| `R/llm*.R`, `R/cap-prompt.R` | `metacheck.llm` |
| `R/doi.R`, `R/db-*.R`, `R/regcheck-local.R`, `R/svutils-orcid.R` | `metacheck.db` |
| `R/archive-*.R`, `R/repo-*.R`, `R/zip-peek.R`, `R/cache.R`, `R/utils.R` (HTTP helpers) | `metacheck.archives` |
| `R/code_check.R` | `metacheck.codecheck` |
| `R/data_check_helpers.R`, `R/file_category.R`, `R/file-naming.R`, `R/scales.R`, `R/tasks.R` | `metacheck.datacheck` |
| `R/spv.R`, `R/jasp.R`, `R/omv.R`, `R/mplus.R`, `R/stata.R`, `R/r-output.R`, `R/r-capture.R`, `R/stat-tables.R`, `R/stat-output.R`, `R/match-*.R`, `R/stato-map.R` | `metacheck.statout` |
| `R/reproducibility_check*.R` | `metacheck.repro` |
| `inst/plumber` | `metacheck.api` (FastAPI) |
| `inst/modules/<name>.R` | `metacheck/modules/<name>.py` |
| `inst/databases`, `data/*.rda`, `inst/schema`, `inst/demos` | `metacheck/resources/` |

Record every mapping in `porting/map/<area>.toml` (the upstream-sync workflow uses it
to find the Python code to update when an R file changes):

```toml
[files]
"R/text_search.R" = ["src/metacheck/text/search.py"]

[functions]
text_search = "metacheck.text.search:text_search"
```

Public functions are exported lazily from `metacheck/__init__.py` (`_EXPORTS`), with
the R name where it is a valid Python identifier.

### Not ported by design

Some of metacheck has no place in a Python package whose paper schema is bibr
export schema v12.0 (older v10.x files are still read as metacheck reads them),
which runs bibr in-process, keeps the Grobid adapter, has module system v2 and ships
its own CLI and API. [`porting/skip.toml`](../porting/skip.toml) lists it, with a
reason for each entry:

* `[skip]`: R symbols that are never ported, such as the Shiny apps (`report_app`,
  `osf_app`), package hooks (`.onLoad`, `.onAttach`), S3 `print` methods (Python
  uses `__repr__` instead), R graphics viewers and base-R fallbacks for things
  Python's standard library does.
* `[skip_files]`: upstream files and directories with nothing to port (`inst/app`,
  the R deployment files in `inst/plumber`, `man`, `data-raw`, ...).
* `[drop.<id>]`: ported code that has been superseded and should be removed.

`scripts/port_status.py` measures coverage against what pytacheck does port and
counts skipped symbols separately (`--missing` lists what is left). The
upstream-sync agent does not port changes to skipped symbols or files. Add an entry
only when something is obviously superseded or only makes sense inside an R
session. Checks, modules, archives, reproducibility checks and report output are
always ported.

## 3. Translating R to Python

### Regular expressions

Patterns copied from metacheck keep R's syntax and go through the `metacheck._r`
helpers (`grepl`, `sub`, `gsub`, `regextract`, `regextract_all`, `regexec`,
`strsplit`, `compile_r`). These translate them onto the `regex` module:

* TRE patterns: POSIX classes, `\<` and `\>`, a literal backslash inside brackets,
  `.` matching newline, `$` at the very end.
* `perl = TRUE`: ASCII `\w \d \s \b`, and inline flags scoped to their group.

The helpers keep R's replacement syntax and R's gsub/strsplit empty-match rules.
TRE/PCRE behaviour on malformed patterns, and glibc/ICU character-class edge cases,
are not reproduced. Patterns written for pytacheck itself may use `re` or `regex`
directly.

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
to scientific notation (`1e+05`); `format()` uses 7. Use `metacheck._r.as_character`
and `metacheck._r.format_num(x, digits)`; `sprintf("%.2f", x)` is `f"{x:.2f}"`,
`sprintf("%d", n)` is `f"{n:d}"`, `sprintf("%s", x)` is `as_character(x)` (and `"NA"`
for missing). **R `round()` is not Python `round()`**: they disagree on ~3% of decimal
"half" cases (`round(0.12355, 4)` is 0.1236 in R, 0.1235 in Python) — always use
`metacheck._r.r_round(x, digits)` where R calls `round()`, and `_r.signif()` for
`signif()`.
`plural(n)` is `_r.plural`.

### Missing values and types

Use `metacheck._values` (`is_missing`, `is_true`, `as_float`, `as_int`, `as_str`,
`field`) and `metacheck._json.loads`. Do not add new private `_is_na`, `_chr`,
`_dollar` or `as_numeric` copies:

| R | Python |
|---|---|
| `is.na(x)` of one value | `is_missing(x)`: `None`, `pd.NA`, NaN, NaT |
| `isTRUE(x)` | `is_true(x)` |
| `as.numeric(x)` / `as.integer(x)` / `as.character(x)` of one value | `as_float(x)` / `as_int(x)` / `as_str(x)` (`None` is `NA`) |
| `x$a$b`, `x[["a"]][[1]]` on parsed JSON | `field(x, "a", "b")`, `field(x, "a", 0)` (exact names; `None` when absent) |
| `jsonlite::fromJSON(txt, simplifyVector = FALSE)` | `metacheck._json.loads(txt)` |
| `httr2::resp_body_json(resp)` | `metacheck.http.resp_json(resp)` |

For whole columns:

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
| `dplyr::count(df, a, name = "n")` | `metacheck._r.count(df, "a", name="n")` (sorted, C locale) |
| `dplyr::bind_rows(...)` | `metacheck._r.bind_rows([...])` |
| `summarise(..., .by = c(a, b))` | `groupby([...], sort=False, dropna=False)` (first-appearance order) |
| `group_by(a) |> summarise()` | `groupby(..., sort=True, dropna=False)` (sorted, C locale) |
| `arrange(a)` | `sort_values("a", kind="stable", na_position="last")` |
| base `sort()`/`order()` on strings | `sorted(key=metacheck._r.r_sort_key)` (ICU-like collation) |
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
`match.arg()`, validate the value and raise `ValueError` naming the allowed values.

### Errors, warnings and messages

`stop(msg)` → raise an appropriate exception (`ValueError`, `TypeError`,
`RuntimeError`, `FileNotFoundError`...) with a clear message of its own; R's wording
is not reproduced and parity only checks that both sides fail. Where R fails on
valid input because of a bug, return the right result instead (see section 1). `warning()` → `warnings.warn()`.
`message()` progress chatter → nothing, or `rich` output gated on
`metacheck.config.verbose()`. `logger()` → `metacheck.log.logger()`.

## 4. Porting a module

See `src/metacheck/modules/marginal.py` for the reference pattern:

```python
@module(
    title="Marginal Significance",          # roxygen title
    description="...",                     # @description
    details="""...""",                     # @details (keep <validation> blocks)
    keywords=["results"],                  # @keywords, verbatim from R (the report section)
    requires=[],                           # capabilities: ["network"] and/or ["llm"] if the
                                           # module needs them (never put these in keywords)
    author=["Daniel Lakens <D.Lakens@tue.nl>"],   # one entry per R @author line, verbatim;
                                           # "Name <email>" is stored as R's "Name (\email{email})"
    params={"paper": "a paper object or paperlist object", ...},   # @param
)
def marginal(paper, ...):                  # same name as the file; R's arguments + defaults
    ...
    return {"table": ..., "summary_table": ..., "na_replace": 0,
            "traffic_light": tl, "summary_text": ..., "report": [...]}
```

* `tests/foundation/test_module_metadata.py` checks `title`, `keywords` and `author`
  against the R module's roxygen header: module_list(), module_help() and
  module_report() print them.
* Return the same list elements, in the same order, as the R module.
* `report` is a string or a list of blocks: markdown strings, `scroll_table(df, ...)`,
  `collapse_section([...])`. Prose follows R's wording and is compared with it; a
  corrected typo or plural is a U-entry whose marks carry `r_text` (section 1).
* `format_ref(bibentry)` in R: run R once to get the HTML (`cat(format_ref(x))`) and
  store it as a string constant, as `marginal.py` does.
* `keywords` must stay exactly R's (one section keyword per upstream module). Declare
  network or LLM use with `requires=["network"]` / `requires=["llm"]`: offline selection
  (`--offline`, the app's toggles) and provenance read it. (For old code the decorator
  still moves `"llm"` / `"network"` out of `keywords` into `requires`.)
* `get_prev_outputs("data_check", "table")` works the same inside a module run.
* LLM-backed modules must work without an API key when R does (e.g. fallbacks), and
  parity cases for LLM paths use the LLM cache or are marked `skip_r`.

### HTTP

All network access goes through `metacheck.http` (port of `.batch_query()` and the
httr2 retry policy): `http.request(method, url, ...)` returns the response (error
statuses are returned, not raised) or `None` after connection failures;
`http.batch_query(urls, ...)` fetches many URLs politely; `http.skip_on_api_limit()`
reproduces the `skip_on_api_limit` option; `http.Throttle` is `req_throttle()`;
`http.resp_json(resp)` parses a JSON body (the content type is checked
case-insensitively). Never create your own `httpx.Client`.

Tests replay metacheck's recorded responses exactly (httptest2 file naming is
reproduced, including R's `digest()` hashes):

```python
from tests.httpmock import replay

def test_github_readme(upstream_dir):
    with replay("apis"):          # or "apis_papers_retag", ...
        ...
```

Unrecorded requests get a 404, never the network. Parity cases must not use the
network either: give them `mock_dir: apis` (or another metacheck mock directory) and
both R (via httptest2) and Python replay the same recorded responses — this is how
API clients and network-backed modules are parity-tested.

## 5. Performance

pytacheck must be substantially faster than metacheck on large corpora:

* Operate on whole tables (a paper list's `paper_table()`), not paper by paper.
* Vectorise with list comprehensions over compiled patterns; avoid
  `DataFrame.apply(axis=1)`, `iterrows()` and building DataFrames inside loops.
* Never recompile regexes in loops (the `_r` helpers cache; `compile_r()` for custom loops).
* Load bundled data files once (`functools.cache`).
* HTTP: reuse one `httpx.Client` with HTTP/2 and connection pooling; respect rate
  limits; cache responses on disk as metacheck does.

## 6. Working rules for parallel lanes and agents

Work is split into lanes that run in parallel: porting areas, and the right-sizing
lanes listed at the end of this section. File ownership is disjoint:

* Only create or edit files in your lane's area (source package, `tests/<area>/`,
  `parity/cases/<area>*.yaml`, `parity/golden/<area>*/`, `porting/map/<area>.toml`,
  and your lane's `parity/divergences/<lane>.yaml`).
* Shared files belong to the harness lane: `pyproject.toml`, `uv.lock`,
  `metacheck/__init__.py`, `src/metacheck/http.py`, `_json.py`, `_values.py`, the
  docs, the parity core (`parity/*.py`, `parity/r/`) and CI. Other lanes only
  receive minimal bug fixes there and say so in their report; list new dependencies
  and exports in the report instead of editing them. Do not `pip install`.
* Foundation files (`src/metacheck/_r/`, `papers/`, `module.py`, `text/search.py`,
  `text/expand.py`, `report/blocks.py`) likewise only receive minimal bug fixes unless
  they are your lane's.
* Do not commit; the orchestrator commits.
* Use the R reference at `$PYTACHECK_RSCRIPT` to explore R behaviour
  (`LANG=C.UTF-8 $PYTACHECK_RSCRIPT -e 'library(metacheck); ...'`).

Cross-lane rules:

* **Never delete a function another lane imports.** Point it at the shared primitive
  (`metacheck._values`, `metacheck._json`, `metacheck.http`) or leave it; the closing
  step removes it once nothing imports it. Kept for now: `llm._rds.RInt` (imported by
  `datacheck/files.py`), `text.json_expand.as_numeric` (`db/crossref.py`,
  `text/extract.py`), `stats._rmath.as_numeric` (`archives/download.py`,
  `archives/zip_peek.py`), `datacheck.files._r_as_numeric` (`archives/dryad.py`,
  `archives/dataverse.py`), `datacheck._strip_llm_wrapper` (`modules/_power.py`,
  `modules/_codebook.py`) and the `datacheck._files_rdata` API (`repro/docker.py`,
  `repro/tables.py`).
* **Consolidate private helpers in your own files.** Replace a file's private
  `_is_na`/`_chr`/`_dollar`/`as_numeric` copies with `metacheck._values` when you
  touch it, and check the area's parity after each file. R-worded error messages are
  reworded when their site is touched, not in a blanket pass.
* **Marks.** A lane marks its divergences in its own `parity/divergences/<lane>.yaml`;
  a case is marked in exactly one file. Lock files are per area
  (`parity/lock/<area>.json`, one line per case). A lane that changes a case already
  marked by another lane re-locks it and may edit only that mark's `reason` line.
* **IDs.** Lanes append D- and U-rows to `docs/UPSTREAM_ISSUES.md` only within their
  reserved section and ID range.
* **Order.** The regex lane merges first, because it shifts regex behaviour in every
  area; the others rebase on it and re-lock their areas.

| lane | scope | divergences file | reserved IDs |
|---|---|---|---|
| 1 | harness, policy, shared primitives | `core.yaml`, `deliberate.yaml`, `prose.yaml` | — |
| 2 | regex engine and the R foundation (`_r`) | `regex.yaml` | D29-D32, U159-U162 |
| 3 | data files and data modules | `data.yaml` | D33-D38, U163-U170 |
| 4 | code checks and reproducibility | `code.yaml` | D39-D43, U171-U176 |
| 5 | text modules, statistics and paper tables | `text.yaml` | D44-D48, U177-U183 |
| 6 | LLM client | `llm.yaml` | D49-D52, U184-U187 |
| 7 | archives, databases, paper I/O, stat outputs, report and repository modules | `archives.yaml`, `io.yaml` | D53-D58, U188-U194 |
