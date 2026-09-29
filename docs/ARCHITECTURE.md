# Architecture

pytacheck is a translation of metacheck, so its structure mirrors metacheck's; this page
explains the pieces that are *not* one-to-one translations and why they exist.

```
src/pytacheck/
  _r/            R semantics: regex engines, number formatting, collation, dplyr idioms
  papers/        Paper / PaperList (bibr export schema 12.0), reading/writing, cross-paper tables
  module.py      module decorator, discovery, module_run chaining
  modules/       built-in checks, one file per metacheck module (+ pack.json: built-in presets)
  packs/         packs, stores, install, pack check, store build (module system v2)
  presets.py     presets and selection; provenance.py: provenance and run records
  text/          text_search, text_expand, extractors (p-values, statistics, URLs, ...)
  stats/         statcheck port
  report/        report blocks and rendering (HTML/QMD/Markdown without R or Quarto)
  io/            read(): bibr JSON, Grobid TEI, bibr in-process, conversion servers
  db/            DOIs, Crossref/OpenAlex/DataCite, Retraction Watch, FLoRA, PubPeer, ...
  archives/      OSF, GitHub, Zenodo, Dryad, Figshare, Dataverse, ... + downloads
  codecheck/ datacheck/ statout/ repro/ fileinfo/   the large helper subsystems
  http.py        shared HTTP client with metacheck's retry/rate-limit policy
  api/           REST API (FastAPI), port of metacheck's plumber API
parity/          the R-vs-Python harness (not shipped)
porting/         R -> Python maps used by humans and the upstream-sync agent
upstream/        metacheck, pinned (git submodule; not shipped)
```

## Reproducing R exactly

metacheck's results depend on R semantics that Python does not share. The `_r`
package concentrates all of them so modules can be translated almost line by line:

* **Regular expressions.** R's default engine (TRE) is POSIX: the *longest* of the
  leftmost matches wins (`a|ab` matches `ab`), `\w`/`[[:alpha:]]` are Unicode-aware,
  a backslash inside `[...]` is a literal character, `.` matches newlines and `$` only
  matches at the very end. `perl = TRUE` (PCRE) is leftmost-first with ASCII-only `\w`,
  `\d`, `\s`, `\b`. Patterns are translated to the `regex` module (which has a POSIX
  mode) and cached; `_r.regex` also reproduces R's replacement syntax, `strsplit()`
  quirks and empty-match rules. A literal prefilter skips the engine for texts that
  cannot match a keyword alternation — a large speed-up on corpora, proven equivalent
  by a differential test.
* **Numbers as text.** `paste()` shows doubles with 15 significant digits and may
  switch to scientific notation (`1e+05`); `format()` uses 7 digits. `_r.base`
  implements R's width rule exactly.
* **Missing values and types.** Tables use pandas nullable dtypes (`string`, `Int64`,
  `boolean`) so NA propagates with R's three-valued logic.
* **Sorting.** `dplyr` sorts in the C locale; base R `sort()` uses ICU collation.

All of this is verified against R (`parity/cases/rcompat.yaml`).

## Papers

A `Paper` holds bibr JSON tables as DataFrames. bibr export schema 12.0 is the schema
pytacheck targets: 12.x exports are read natively (`io/bibr12.py`, the port of
metacheck pull request #423), Grobid TEI is converted to 12.x (`io/grobid_bibr12.py`),
and older files without a root `schema_version` read exactly as metacheck reads them.
Tables read from JSON are kept as
records and **materialised lazily**; `paper_table()` over a `PaperList` concatenates
raw records directly into one DataFrame instead of building one per paper. Tables are
returned by reference and must not be mutated by library code (tested for every
module).

With `metacheck[bibr]`, `pc.read("paper.pdf")` runs bibr in-process and converts its
result dict straight into a `Paper` (`from_bibr`), without writing JSON.

## Modules

A module is a decorated function returning a dict (`table`, `summary_table`,
`traffic_light`, `summary_text`, `report`, extras). `module_run()` reproduces
metacheck's: default traffic light, `report` fallback, the `summary_table` left join
with `.<module>` suffixes, `na_replace`, and chaining through `prev_outputs` /
`get_prev_outputs()`. Modules are discovered among the built-ins, entry points
(`pytacheck.modules`), `./<name>.py`, `./modules/<name>.py`, or a path.

Packs (`pytacheck/packs/`), presets (`presets.py`), run records (`provenance.py`)
and the store extend this: community modules are addressed as `pack::name`,
pinned by commit and file hash, and recorded in every run. See
[MODULES.md](MODULES.md) (guides) and
[design/module-system-v2.md](design/module-system-v2.md) (design).

Reports are lists of blocks: markdown strings (with Quarto-style callouts) and
`ReportTable` objects. metacheck emits R code chunks for tables and needs Quarto; here
the renderer turns blocks into self-contained HTML (or `.qmd`/Markdown) directly.

## Parity harness

See [PARITY.md](PARITY.md). The key property: goldens are only ever produced by R
(locally or in CI from an exact conda lockfile), and CI regenerates them to prove they
still match metacheck. `pytest` compares against the committed goldens, so ordinary
development and CI test runs do not need R.

## Upstream sync

`.github/workflows/upstream-sync.yml` runs daily. When metacheck `dev` has new commits
(or, while the pin names a pull request such as #423, that pull request's head moves;
once it is merged, dev again) it moves the pin, writes a brief (`scripts/upstream_sync.py`: commits, changed files and
functions mapped to their Python homes via `porting/`), regenerates the goldens with
the new metacheck (so behaviour changes show up as golden diffs), lets Claude Code port
the changes under `docs/PORTING.md`, verifies parity/tests/lint and opens a pull
request. Merging requires the CI and Parity workflows to pass, so an AI-generated
change can only land when it matches R.

## Performance

* lazy imports keep `import pytacheck` free of pandas/scipy/httpx;
* lazy table materialisation and corpus-wide record concatenation;
* compiled, cached R-regex translations and the literal prefilter;
* typed column construction without per-value Python loops when possible;
* one pooled HTTP/2 client with polite concurrency.

`benchmarks/compare_r.py` times the same work in R and Python.
