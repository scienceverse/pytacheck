# The R engine

pytacheck can run the checks of R metacheck itself, the package it ports, and show
their results with its own report. Where R can run metacheck, R is the default engine
of the app (the REST API stays on Python unless started with `--engine r`): R metacheck is the reference, and pytacheck's Python
modules are an experimental, faster port with best-effort parity to R. Reading the paper stays the same: R reads the same
bibr JSON or GROBID XML file that pytacheck read. Only the checks differ.

```sh
pip install "metacheck[r]"     # names the choice; it adds no Python package
pytacheck r-engine             # can R metacheck run here, and which versions?
```

Each run starts `Rscript` once (`src/metacheck/rengine/run.R`), which loads metacheck,
runs the modules and writes their outputs as canonical JSON (`canonical.R`, the encoder
of the parity tooling). pytacheck turns them into `ModuleOutput` objects and renders
them as it renders its own. Starting R and loading metacheck take a few seconds per run.
A run that reaches its time limit, is stopped, or whose process is ended (a hosted run
at its limit) ends R and everything R started.

## Installing R and metacheck

The R engine needs R 4.5 or later (metacheck needs it, U1) with the metacheck R package
and its imports.
pytacheck uses `METACHECK_R_ENGINE_RSCRIPT`, else `METACHECK_RSCRIPT`, else the first
`Rscript` on the `PATH`.

From R:

```r
install.packages("pak")
pak::pak("scienceverse/metacheck@dev")
```

Without root, on Linux, the pinned environment of the parity tooling works too
(R 4.5.3 and conda-forge builds of metacheck's imports; about 2 GB):

```sh
micromamba create -y -p /opt/metacheck-r --file parity/r/conda-linux-64.lock
git clone --branch dev https://github.com/scienceverse/metacheck /tmp/metacheck
LANG=C.UTF-8 /opt/metacheck-r/bin/R CMD INSTALL --no-test-load /tmp/metacheck
export METACHECK_R_ENGINE_RSCRIPT=/opt/metacheck-r/bin/Rscript
pytacheck r-engine
```

Online and LLM modules need the same network access and keys in R as in Python (R reads
the same environment).

## Where it is used

* **In Python:** `metacheck.rengine.run("paper.json", ["stat_check", "marginal"])` gives
  the module outputs by name; `render_module_outputs(outputs, paper,
  version=rengine.report_version(...))` makes the report.
* **The REST API:** `engine=r` or `engine=python` on `POST /paper/check`; a request that
  names neither uses Python, so clients such as the Platform keep Python's results when
  R is installed. `pytacheck serve --engine r` makes R the server's default. See
  [API.md](API.md#the-r-engine).
* **The app:** "Run checks with: R metacheck 0.3.3.2 / Python 0.4.0a2 (experimental)"
  under "Read PDFs with", with R chosen; each choice names the version it runs, and so
  does the page's title ("R metacheck 0.3.3.2 · Python 0.4.0a2 (experimental)"). It is shown where R runs metacheck; on a shared server
  (`--hosted`) only with `METACHECK_APP_R_ENGINE=on`, and `METACHECK_APP_R_ENGINE=off`
  hides it (and runs Python) anywhere. With R the page offers only R
  metacheck's checks (not `author_check`, `figure_table_refs`, `license_check` or packs)
  and checks papers only, not data packages. R runs the shared data check too, without
  the local concept classifier (D33), and does not report its progress. The report header
  names R metacheck's version and the results line says "with R metacheck 0.3.3.2" (or
  "with Python 0.4.0a2 (experimental)" after a Python run).

## What differs from Python

The two engines give different results on the same paper on purpose:
[UPSTREAM_ISSUES.md](UPSTREAM_ISSUES.md) lists pytacheck's deliberate differences
(D-entries) and the metacheck issues it fixes (U-entries). pytacheck is compared against
the R commit in `parity/UPSTREAM.toml`; a newer R metacheck differs beyond that list
until the next upstream sync. How the R engine shows R's results is D87. R is slower:
about 2.5 to 3 times on the benchmark corpus (`docs/design/PERF_REPORT.md`).
