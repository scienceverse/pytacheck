# Parity with metacheck

pytacheck is only as good as its agreement with metacheck. The parity harness runs
the **real R package** on the same inputs as pytacheck and compares the results
structurally.

```
parity/
  UPSTREAM.toml          pinned metacheck repo/branch/commit/version (+ pull request)
  cases/<area>.yaml      what to run (R function / module + arguments)
  golden/<area>/*.json   R's results, canonically encoded (committed)
  r/run_cases.R          R runner: evaluates cases, writes goldens
  r/canonical.R          R -> canonical JSON encoder
  r/helpers.R            R wrappers for base-R idioms used as cases
  canonical.py           Python -> canonical JSON encoder
  compare.py             structural comparator
  cases.py               case loading, argument decoding, Python runner
  __main__.py            `python -m parity generate|check|list`
```

## Workflow

```bash
# once: an R (>= 4.5) with metacheck at the pinned commit, installed under a
# UTF-8 locale (otherwise R mangles non-ASCII names in metacheck's code and
# `generate` refuses to run)
LANG=C.UTF-8 LC_ALL=C.UTF-8 Rscript -e 'install.packages("pak"); pak::local_install("upstream/metacheck")'
export PYTACHECK_RSCRIPT=$(which Rscript)

python -m parity generate --area text      # R -> parity/golden/text/*.json
python -m parity check --area text -v      # Python vs goldens
pytest -m parity                           # the same, as tests
```

Goldens are always generated under `LANG=C.UTF-8` and `TZ=UTC`, so they do not
depend on the machine's locale; the Python side of every case also runs in UTC.
Regenerating a golden gives the same file, on any machine: in every string the
repository directory is written `<repo>` (the Python results get `<repo>` the same
way), R's session temporary directory `<tempdir>`, the random names of R's
temporary files `<tempfile>` and date-time stamps from while the case ran (a
conversion's `completed_at`) `<now>`; papers created without an id
(`test_paper()`) are numbered within a case instead of hashed from the time (the
n-th is `md5("pytacheck-parity-<n>")[:14]`, on both sides); and each case file
runs in its own R session, so `--area` and a full run write the same goldens.
They are committed: `pytest` needs no R.

## Writing cases

See the docstring of `parity/cases.py` for the full syntax. **Quote every string
in case YAML** (`"."`, `"1.0"`, `"yes"` would otherwise be parsed as numbers or
booleans by R's YAML reader); infinities are `!!float ".inf"` / `!!float "-.inf"`.

What is compared (`parity/compare.py`):

* strings exactly, doubles to a relative tolerance of 1e-9 (configurable), integer
  vs double numerically; a Python list is a vector only when its elements share
  one R type (otherwise it is a list, as R keeps them), complex and raw vectors as
  R's `as.character()`;
* errors: when R raised one, Python must raise one whose message matches
  (`compare: {error: contains}`, the default: R's message is part of Python's, or
  Python's is the start of R's, after dropping cli bullets and whitespace;
  `exact`; or `any`);
* named lists as maps, unless R repeats a name or the case sets
  `compare: {strict_names: true}`: then by position, names in order;
* data frames: row count, column names *and order*, every cell; row order unless
  `compare: {unordered: true}`;
* module outputs: every element (`table`, `summary_table`, `traffic_light`,
  `summary_text`, extras...) except `paper` and `prev_outputs`; the `report` is
  compared as prose — its text blocks must match, while R code chunks and
  pytacheck table widgets are skipped;
* representation-only differences are ignored: `NULL` vs empty (including a
  0 x 0 data frame) vs single `NA`, NaN vs NA, a list of scalars vs a vector, a
  1-row matrix vs a vector.

Cases whose Python side runs R (`reproducibility_check`, `code_parse_r(engine =
"r")`, ...) need the reference R: without `PYTACHECK_RSCRIPT` naming an R >= 4.5
they are reported as `skip`, never as pass or fail. The harness notices any case
whose Python side starts `Rscript`/`R`; `needs_r: true` marks one explicitly.

## Deliberate pytacheck defaults

pytacheck produces bibr export schema 12.x where metacheck's defaults keep the older
format (see [UPSTREAM_ISSUES.md](UPSTREAM_ISSUES.md) D1). The Python side of every case
runs with metacheck's defaults (`METACHECK_DEFAULTS` in `parity/cases.py`): `read()` and
`grobid_to_bibr()` convert Grobid TEI with `schema_version=None`, so `$paper` and `$read`
of an XML file, `$expr` code and functions such as `convert()` compare with R's
`read()`. A case that tests the 12.0 conversion passes `schema_version = "12.0"` on
both sides. `paper_write()` runs with `schema_version=None` too (metacheck saves the
paper object; pytacheck's `"auto"` would write a 12.x paper as a 12.0 file).

## Network-dependent functions

Cases with `mock_dir: apis` (or another metacheck mock directory) run against
metacheck's recorded API responses on both sides — R inside
`httptest2::with_mock_dir()`, Python inside `tests.httpmock.replay()` — so API
clients and network-backed modules are parity-tested offline and deterministically.

## CI

* **every push**: `pytest -m parity` against the committed goldens (no R needed; the
  cases whose Python side runs R are skipped);
* **parity workflow** (R installed): regenerates all goldens at the pinned commit and
  fails if they differ from the committed ones (goldens can never drift from R), then
  runs the Python comparison;
* **upstream-sync workflow**: when metacheck `dev` moves (or, while the pin names a
  pull request, that pull request's head), regenerates goldens at the new commit and
  asks an AI agent to port the changes until parity is green again.
