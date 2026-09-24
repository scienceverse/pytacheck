# Parity with metacheck

pytacheck is only as good as its agreement with metacheck. The parity harness runs
the **real R package** on the same inputs as pytacheck and compares the results
structurally.

```
parity/
  UPSTREAM.toml          pinned metacheck repo/branch/commit/version
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
# once: an R (>= 4.5) with metacheck at the pinned commit
Rscript -e 'install.packages("pak"); pak::local_install("upstream/metacheck")'
export PYTACHECK_RSCRIPT=$(which Rscript)

python -m parity generate --area text      # R -> parity/golden/text/*.json
python -m parity check --area text -v      # Python vs goldens
pytest -m parity                           # the same, as tests
```

Goldens are always generated under `LANG=C.UTF-8` and `TZ=UTC`, so they do not
depend on the machine's locale. They are committed: `pytest` needs no R.

## Writing cases

See the docstring of `parity/cases.py` for the full syntax. **Quote every string
in case YAML** (`"."`, `"1.0"`, `"yes"` would otherwise be parsed as numbers or
booleans by R's YAML reader).

What is compared (`parity/compare.py`):

* strings exactly, doubles to a relative tolerance of 1e-9 (configurable), integer
  vs double numerically;
* data frames: row count, column names *and order*, every cell; row order unless
  `compare: {unordered: true}`;
* module outputs: every element (`table`, `summary_table`, `traffic_light`,
  `summary_text`, extras...) except `paper` and `prev_outputs`; the `report` is
  compared as prose — its text blocks must match, while R code chunks and
  pytacheck table widgets are skipped;
* representation-only differences are ignored: `NULL` vs empty vs single `NA`,
  NaN vs NA, a list of scalars vs a vector, a 1-row matrix vs a vector.

## Network-dependent functions

Cases with  (or another metacheck mock directory) run against
metacheck's recorded API responses on both sides — R inside
, Python inside  — so API
clients and network-backed modules are parity-tested offline and deterministically.

## CI

* **every push**: `pytest -m parity` against the committed goldens (no R needed);
* **parity workflow** (R installed): regenerates all goldens at the pinned commit and
  fails if they differ from the committed ones (goldens can never drift from R), then
  runs the Python comparison;
* **upstream-sync workflow**: when metacheck `dev` moves, regenerates goldens at the new
  commit and asks an AI agent to port the changes until parity is green again.
