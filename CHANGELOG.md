# Changelog

This is the Python version of metacheck. It has its own version line, starting
at 0.4.0 (0.4.0a1 is the first release); the version does not follow the R
package's. The R commit each release is compared against is in
`parity/UPSTREAM.toml` and in the notes of the release.

## Unreleased

### Upstream sync: metacheck dev 49f97ec5 (metacheck #424-#452)

The R reference is metacheck `dev` at 49f97ec5 merged into pull request #423 (the bibr export schema 12.0 work this version already follows). Ported:

- **Zip archives and downloads:**
  - `zip_peek()` lists zips on hosts that refuse HEAD (S3-backed storage) with ranged requests, and `zip_peek(cache = TRUE)` keeps listings on disk; new `zip_peek_cache_clear()`. A failed peek that may pass later is not cached (U210).
  - `download_repo_files()` downloads OSF, Zenodo and Dataverse repositories file by file. GitHub, GitLab and Dryad archives need listed sizes and are cut at twice `max_download_size`. Archive members that fail to download are reported with the reason, in `attr(, "failed")` of the `*_file_download()` functions too.
  - `osf_file_download(mode = "zip")` keeps the whole project within `max_download_size`: an archive is taken only while its listed files fit, its download stops past the budget, and the rest is downloaded file by file, smallest first; files left out are `attempted = FALSE` (metacheck stops on this path: U209).
- **data_check:** `peek_zips` is on by default: a downloaded zip is unpacked and its files are classified and listed as their own rows. `cache` and `skip_on_api_limit` are passed on to `repo_check` and to the zip peek. GIS (`.shp .dbf .shx .prj .sbn .sbx .cpg .gpkg`), phylogenetic (`.nex .nwk .tre .phy`), mass-spectrometry (`.mzxml .mztab`) and `.ply` files are data by their extension; `.tab` and `.table` are readable plain-text tables.
- **stat_effect_size:** Hedges' g (`g`, `Hedges' g`, `gs`) is checked for coherence like Cohen's d. `gav`, `gz` and `grm` count as reported effect sizes (U212).
- **code_check:** R package-list variables (`pkgs <- c(...)`, loop variables, `character.only`, `lapply(pkgs, library)`, `p_load(char = )`) are resolved to the names they hold or dropped; their own names are no longer reported or installed as packages. Empty code files are checked like any other file. `cache` and `skip_on_api_limit` reach the zip peek, and `skip_on_api_limit` reaches `repo_check`.
- **reproducibility_check:** a package CRAN cannot find is reported as unavailable, with `install.packages()`'s own warning, instead of "installed but not loadable"; a 404 on the CRAN Archive listing is "package not found in the CRAN Archive" and no longer counts as a network failure (the Docker install script does the same). `peek_zips` defaults to TRUE.
- **repo_check:** new `skip_on_api_limit`; `cache` and `skip_on_api_limit` also apply to zip peeking. A DSpace 7 repository gets its doi and licence row in `repo_metadata`.
- **Repository hosts:**
  - 4TU.ResearchData: articles cited by a uuid DOI are looked up, and downloaded, by the numeric id the DOI resolves to.
  - Dataverse: phys-techsciences.datastations.nl is added; a DOI prefix shared by several installations (10.17026, the DANS Data Stations) is resolved through doi.org by the resolved host name (U211).
  - DSpace 7: `dspace7_links()` finds DOI mentions that resolve to a DSpace 7 repository; `dspace7_file_download()` carries the item's doi and licence and returns an empty listing for an item without files.
  - DataONE: KNB `#view/` and `catalog/view/` URLs and bare `knb.<n>.<rev>` package ids are recognised; files listed without a `<physical>` description are included, with their size from the member node.
  - Figshare: collections (URL or `10.6084/m9.figshare.c.` DOI) expand into their articles; institutional DOIs with two sub-prefix segments give their article id.
  - OSF: the token check at start-up gives up after 5 seconds.
- `cap_report()` keeps its name although metacheck made it internal (D61).
- Metacheck fixed bugs that this version had marked: Dataverse DOI routing (U32) and empty code files (U67, U87); those cases match R again.
- Fixed while porting (metacheck has these too):
  - Files unpacked from an archive take their own extension's type (`data`, `code`, `text`) instead of the archive's (U213).
  - An archive in `local_path` is no longer unpacked into the user's folder: metacheck writes `<archive>.contents/` next to it, and the next run lists every unpacked file twice. pytacheck unpacks it to `metacheck-archives/` in the temporary folder (U214).
- `download_repo_files()` no longer fails when a file table read as text has a missing `repo_url` and a GitHub, GitLab or Dryad archive is still to be downloaded.
- A zip whose HEAD request failed for a passing reason (429, 5xx, no connection) is not cached on disk as unlistable (U210), and zip-listing cache entries are written under unique temporary names, so two threads cannot interleave one.

### Parity tooling

- `python -m parity generate --jobs N` runs the case files' R sessions side by side (`0`: one per CPU). A full regeneration took about 6 minutes instead of about 45; the parity and upstream-sync workflows use it. `generate` warns when the R sessions leave files in the checkout outside the goldens.

### Fixed

- The default LLM model (the first provider whose API key is set) is now set when metacheck loads, as R's `.onLoad()` does, not when the LLM code is first imported. Before, a module run before and after that import used different session-cache keys.

### The import package is metacheck

- **Changed:** the import package is now `metacheck` (`src/metacheck`). `import pytacheck` and the `pytacheck` command keep working: every `pytacheck.<sub>` is the same module object as `metacheck.<sub>`, and type checkers see the public names through stubs. Packs may declare `requires.metacheck`; use `>=0.4.0a2.dev0` if the pack does `import metacheck`, because 0.4.0a1 has no `metacheck` import package. `requires.pytacheck` is still read, and `pack check` warns if both are given. The pack scanner treats `pytacheck.*` and `metacheck.*` alike. Saved tables and the repo-info cache keep their format, so files move between this version and 0.4.0a1 both ways (until the change below). Not renamed yet: the store id, the logger names (records still carry `pytacheck`), the install record and pack module names, the command's help name and the version line (the folders keep their name; see below).
- **Things that do change:** warnings now come from `metacheck.*` modules, so a warning filter that matches `module="pytacheck..."` needs `metacheck` instead. `importlib.resources.files("pytacheck")`, `pytacheck.__file__` and `pytacheck.__path__` point at the small alias folder; use `files("metacheck")` or a subpackage such as `files("pytacheck.resources")`. Messages that name a module or a file of the package name it under `metacheck` (for example `metacheck.pack_install()`, `metacheck/resources/...` in `status --check`, and `module 'metacheck.io' has no attribute ...`).
- **Upgrading an old git install:** run `pip uninstall pytacheck` first. An install from before the distribution was renamed owns the same `pytacheck/` folder. **In a git checkout** that has been on an older commit, run `git clean -fdX src/pytacheck` once to remove the old bytecode folders.

### Settings and saved files use the metacheck name

- **Environment variables:** every `PYTACHECK_*` variable now has a `METACHECK_*` name, which is read first; the `PYTACHECK_*` names keep working unchanged. If both are set to different values, one line says which is used (the value is never shown). Two exceptions: `PYTACHECK_LLM_CACHE_DIR` still comes before `METACHECK_LLM_CACHE_DIR`, which is R metacheck's name for the same shared cache; `METACHECK_LLM_MODEL` and `METACHECK_LLM_MAX_CALLS` keep their meaning. **Changed:** `METACHECK_EMAIL` is now read before `PYTACHECK_EMAIL`. A variable set to spaces only now counts as unset; this changes the setting for `*_CACHE_DIR`, `*_DATA_DIR`, `*_LOG`, `*_RSCRIPT` and `*_LLM_CACHE_DIR`, `*_NO_SLEEP` (sleeps stay on), `*_EMAIL`, `*_STORE_URL`, `*_R_PARSER`, `*_R_SERIALIZE_VERSION` (which raised an error before) and `METACHECK_LLM_MODEL`/`METACHECK_LLM_MAX_CALLS` (the default is used). The `PYTACHECK_*` names are deprecated but stay until an end date is published in advance, except `PYTACHECK_LLM_CACHE_DIR`, which is the only way to give this package an LLM cache of its own and is not deprecated. The full list and order: docs/ENVIRONMENT.md.
- **Folders:** unchanged. Settings, the saved bibr key, trusted folders, installed packs, store indexes, refreshed databases, caches and the log stay in the `pytacheck` folders (for example `~/.config/pytacheck` and `~/.local/share/pytacheck` on Linux), which this version and 0.4.0a1 both use. They are separate from R metacheck's folders. `METACHECK_CONFIG`, `METACHECK_DATA_DIR`, `METACHECK_CACHE_DIR` and `METACHECK_LOG` now override them, ahead of the `PYTACHECK_*` names, which work as before.
- **Project file:** `metacheck.json` is read as the project file; an existing `pytacheck.json` still works and is edited in place. If a folder has both, `metacheck.json` is used. A `metacheck.json` that is not a settings file (for example saved `--json` results) is passed over with a warning, and an empty one (as a shell redirect creates it) is passed over silently. The same safety rules apply to both: a project file cannot add stores, and the local code it names runs only once you trust it. Older versions do not read `metacheck.json`.
- **Run records** are now `metacheck.run/2`, with the keys `version` (this package's version) and `r_reference` (the R commit it is compared against) in place of `pytacheck` and `metacheck`. Records written by earlier versions still open and replay, and a record written again carries the new schema. Older versions cannot read the new records. Code that checks `schema == RUN_SCHEMA` should use `schema in RUN_SCHEMAS`, which also holds the old id. In Python, `RunRecord.version` and `RunRecord.r_reference` are the new fields; `.pytacheck` and `.metacheck` still read them, but a `RunRecord` can no longer be built with the keywords `pytacheck=` or `metacheck=`.
- **Saved module tables and repo-info cache entries** are written under `metacheck.*` format ids and still read under the old ones. Older versions treat the new files as not saved: they compute those papers again, fetch those repository listings again, and their `collect_module_tables()` leaves those papers out. (This replaces the note above that these files move both ways between this version and 0.4.0a1.)
- **Options:** `metacheck.careless`, `metacheck.r_serialize_version` and `metacheck.llm.workers`; the `pytacheck.*` spellings are the same options.
- **Logging:** handlers on the `metacheck` logger now receive the package's records; the records are still named `pytacheck` and `pytacheck.api`, so existing logging setups keep working.
- **Kept:** the `pytacheck` folder names; installed packs keep their `.pytacheck-install.json` record, and pack modules keep their internal `pytacheck_packs` names, so packs installed by either version stay valid in the other.

### Added: a local concept classifier for data_check

- `data_check` fills the column concepts its rules leave blank with a local,
  multilingual classifier (XLM-RoBERTa-large fine-tuned on 108k LLM-labelled
  columns, ONNX with 8-bit weights, ~840 MB, downloaded once from the Hugging
  Face Hub), instead of leaving them to the LLM tier (D33). It runs offline in
  ~0.1 s per column on eight CPU threads and ~2 GB of memory; held out, it scores
  F1 0.795 on ResearchBox and 0.736-0.761 on OSF/GitHub/Zenodo repositories,
  against 0.784 / 0.740 for Muse Spark 1.3 on metacheck's prompt. Install it with
  `pip install "metacheck[concepts]"` (also in `[all]`); without it `data_check` behaves as metacheck.
- `concepts=` (option `metacheck.concepts`, `METACHECK_CONCEPTS`) picks the tier:
  `"classifier"` (default), `"cascade"` (the columns the classifier is least
  sure of go to the LLM under `llm_use(TRUE)`; threshold
  `metacheck.concepts.threshold` / `METACHECK_CONCEPT_THRESHOLD`, default 0.92),
  `"llm"` (metacheck's behaviour) or `"rules"`. `metacheck.concepts.model` /
  `METACHECK_CONCEPT_MODEL` loads another model (a directory or `repo@revision`), and
  `METACHECK_CONCEPT_THREADS` sets its threads. These are new, so they have only
  `METACHECK_*` names, and the options have no `pytacheck.*` spelling.

### bibr server client

- `convert_bibr(backend="bibr")` and `convert()` (with `BIBR_URL` and `BIBR_API_KEY`) use bibr serve's job API and the hosted service in front of it: submit to `/papers/jobs`, poll, fetch the result, honour `Retry-After` on a 429 (bounded), read a 409 on the result as not ready, explain 401/403/413/415, and never send the token over plain http except to localhost or across a redirect. `"selfhosted"` now sends the token and waits out a 429; the readiness check accepts an anonymous bibr serve (docs/BIBR.md section 3, docs/UPSTREAM_ISSUES.md D59).

### Faster pattern scans: required literals

- `metacheck._r.regex.required_literals(pattern, perl, icase)` reads a TRE or PCRE pattern and returns the words every match must contain (in casefolded text); `detect_many(patterns, text)` folds the text once, skips the patterns whose words are not in it, compiles only the others, and gives the same truth values as `grepl()` pattern by pattern. A pattern the reader does not fully understand has no required words and always runs, so results do not change. The dictionary scans of the codebook modules (791 scale and 833 task patterns per paper) use it: on a 94,000-character text about a third faster once the patterns are compiled. `METACHECK_LITERALS=off` (or `PYTACHECK_LITERALS=off`) turns the new filter off, to rule it out when a match seems to be missing. `grepl()`'s own filter is unchanged. Tests: every match in the recorded regex calls (92,767) has its required words; a generated-pattern test (TRE and PCRE, case on and off, tricky characters such as the Kelvin sign, the long s and no-break space) finds no violation; 229 of the 273 built-in patterns get at least one required word.

### The app

- The page has a header with the version and links to ScienceVerse and the Metacheck, Pytacheck and bibr repositories on GitHub, repeated under the credit line. The online and data options sit above the buttons they apply to, the bibr key field is hidden where the server supplies the key (that key wins over a typed one), and result cells are coloured by their traffic light (the word stays, so colour is never the only signal).
- The page starts on the bibr reader where the server supplies the bibr key (`SCIVRS_API_KEY`), as a hosted server does, and on GROBID otherwise, since GROBID needs no key.
- The bibr option talks to bibr serve and the hosted bibr service in front of it: it sends a PDF to `/papers/jobs`. Before, it sent every PDF to the Scienceverse platform's `/jobs`, which those services answer with 404, so the page said the bibr service could not be found. `PYTACHECK_BIBR_BACKEND=scivrs` says that the address in `PYTACHECK_BIBR_URL` is the platform. For an address from metacheck's public server list, the entry's `protocol` decides; without one, an entry whose `api_key` is `SCIVRS_API_KEY` is the platform, and any other entry is bibr serve. A hosted server does not start with a `PYTACHECK_BIBR_BACKEND` other than `bibr` or `scivrs`, or with a `PYTACHECK_BIBR_URL` that cannot work, such as plain http to another machine for bibr (deploy/space/DEPLOY.md).

### Documentation

- docs/CODEMAP.md maps every metacheck check, exported R function and R package dependency to its Python location, with the parity areas, status labels and register entries (docs/UPSTREAM_ISSUES.md) that apply. `scripts/codemap.py` generates its tables, and `scripts/codemap.py --check` (run by the test suite) fails when they are out of date.

## 0.4.0a1 (first release on PyPI)

First release on PyPI, as `metacheck` (`pip install "metacheck>=0.4.0a1"`). It is a
pre-release. The import name is still `pytacheck` and so is one of the
commands; `metacheck` is the same command, and `python -m pytacheck` works.
Compared against the R package at commit `b239264` (metacheck 0.3.1 at `85c8c87`
plus scienceverse/metacheck#423, which the port targets ahead of its merge).

- The app and the installer: `metacheck-app` and `metacheck[app]`; a one-line
  installer for people who do not use Python (docs/TRY.md).
- Packs that require `pytacheck` find the installed `metacheck`.
- Upgrading: if you installed `pytacheck` from GitHub, run `pip uninstall pytacheck`
  before installing `metacheck` (both install the same files). Pack authors: a
  workflow that installs `pytacheck @ git+...` must install `metacheck @ git+...`
  (or `metacheck>=0.4.0a1` from PyPI) instead.
- `pyyaml` is now a core dependency (codecheck reads YAML).
- NOTICE and CITATION.cff credit the R package and its authors.

Complete rebuild as a parity-tested Python port of metacheck `dev`
(`85c8c87`, metacheck 0.3.1) plus scienceverse/metacheck#423 (`b239264`,
bibr export schema 12.0), which pytacheck targets ahead of its merge.

- R-faithful regular expressions (TRE leftmost-longest and PCRE semantics),
  number formatting, collation and dplyr idioms (`pytacheck._r`).
- bibr-schema `Paper`/`PaperList` with lazily materialised tables; reading
  bibr JSON directly or in-process from bibr (`metacheck[bibr]`).
- Module system compatible with metacheck's (`module_run`, chaining,
  `get_prev_outputs`, `module_list`), plugin modules via entry points.
- Parity harness running metacheck in R; goldens regenerated in CI.
- Shared HTTP layer with httr2's retry/rate-limit behaviour; replay of
  metacheck's recorded API fixtures in tests.
- CLI (`pytacheck`), Docker images (with and without bibr), and a scheduled
  upstream-sync workflow that ports new metacheck commits automatically.

### App

- `metacheck-app` (also `pytacheck app`, extra `metacheck[app]`): a local Gradio page
  that checks a PDF, GROBID XML or bibr JSON file and shows the results table and the
  report. It listens on 127.0.0.1 only, behind a per-launch token, and a second start
  reuses the running app. `--self-test` checks the demo paper without a server.
  A browser sends its cookie for 127.0.0.1 to every local port, so another program
  that you visit in the same browser can see the token. The app therefore refuses
  requests that other local pages start, reads only files uploaded through the page,
  and shows the report in a sandboxed frame that cannot load anything.

### Parity harness

- Accuracy contract (docs/PORTING.md section 1): pytacheck is checked against
  metacheck on every change and must be at least as accurate on realistic
  inputs; metacheck's bugs are fixed, not reproduced; R's error texts and C
  library quirks are not emulated.
- Tiers: cases on realistic inputs (`parity/corpus.toml`) are tier 1 and must
  agree with R apart from documented bug fixes; synthetic edge cases are tier
  2. `python -m parity check --tier`, pytest marks `tier1`/`tier2`.
- Divergence marks are validated against `docs/UPSTREAM_ISSUES.md` (which now
  has a status column) and the Python output of every marked case is pinned
  in `parity/lock/<area>.json`: a changed value (`py_changed`), a changed R
  golden (`r_changed`) or an upstream fix (`xpass`) is reported.
- Errors: when R fails, only Python failing too is checked; R's messages are
  never compared (`$catch` constructor, `presence` compare option).
- `--jobs N` (full check in about 3 minutes on 4 CPUs instead of 10),
  per-run reports, a network guard inside cases, case loading 0.9 s (8.3 s).
- Shared helpers `pytacheck._json`, `pytacheck._values` and
  `pytacheck.http.resp_json`; `python-calamine` joins the core dependencies
  and `chardet` is the optional `charset` extra.
- A `known_divergence` can carry `r_text` substitutions applied to R's golden
  before the comparison, so a case whose only difference is text pytacheck
  corrects (typos, plurals, a full stop) is still compared with R for
  everything else; 160 cases use it (`parity/divergences/prose.yaml` and
  inline marks). A substitution that no longer matters fails as stale.
- Parity keeps the Python side's caches in a throwaway `PYTACHECK_CACHE_DIR`;
  parity and pytest fail when a run leaves a new file in the repository root.
- Network-dependent causal_claims cases are replaced by offline variants; the
  2-second `reproducibility_check.exec_timeout` case by `exec_timeout_10s`.
- `python -m parity accuracy [--generate] [--gate]`: every offline paper
  module on 21 real papers and every repository module on 10 repositories
  (439 outputs), scored against R field by field (traffic light, summary
  table, table rows, summary text and report, numbers inside prose) with a
  whitespace/wording/values level per difference. Every difference must be
  explained by an entry in `parity/accuracy/expected.yaml` that cites a
  U- or D-entry; unexplained or stale entries fail the gate. Today: traffic
  lights agree on 421 of 434 outputs, 188 differences, all explained.
- The reference R reads each paper once per session (`run_cases.R --out`);
  `careless` is installed in a separate library that only the accuracy run
  uses, so parity goldens do not change.
- CI runs unit tests plus tier 1 on every Python version, the whole harness
  and the accuracy gate once, and tier 1 with pyarrow. The upstream sync runs
  the accuracy report before and after porting and opens a draft PR labelled
  `needs-human-review` when tier-1 marks, a tier-1 case's tier,
  `parity/corpus.toml`, `expected.yaml` or D-entries change.

### Changed: regular expressions

- R's regex dialects (TRE for `grepl`/`gsub`, PCRE with `perl = TRUE`) are
  translated onto the `regex` module instead of being emulated: the
  hand-written TRE matcher, the PCRE translator and the glibc character
  tables are gone (`_r` went from 4,971 to 1,100 lines). A replay of 32,902
  recorded realistic calls gives the same results as before; compiling the
  2,591 realistic patterns takes 1.7 s instead of 8.4 s, and 13 modules on a
  paper run in 1.2 s instead of 2.7 s.
- Case-insensitive matching uses Unicode case folding, so `İ`, `ı`, `ſ` and
  the Kelvin sign match like their ASCII letters (D29): for example
  'CONFLİCT OF İNTEREST' is now found. glibc-only class members (NBSP in
  `[[:punct:]]`, U+2028 in `[[:cntrl:]]`) are not reproduced.
- Fixed: the literal prefilter in front of `grepl` could drop true matches of
  dotted or dotless i.

### Performance

The Python side of the accuracy run (439 outputs) takes 16.9 s instead of
34.7 s, its modules 13.6 s instead of 30.6 s (the minimum of 5 interleaved
runs each), with every output byte-identical (docs/design/PERF_REPORT.md).

- `text_search()` builds one table per call (strings, case-folded strings,
  row positions) and matches every pattern of a list against it, building a
  data frame for the result rows only; it used to run a full search per
  pattern. One paper's sentences get their section headers by a lookup
  instead of a merge. ethics_check and open_practices run about 6x
  faster, funding_check_oi 7.7x.
- `paper_table()` of one paper returns the paper's own table as a
  copy-on-write view, and `paper_id()` counts `info` rows instead of building
  the table.
- codebook_check folds a paper's text once for its dictionary scans and
  compiles a dictionary pattern only when one of its literals is in the text;
  the dictionaries' acronym indexes are built once.
- RetractionWatch is cleaned and indexed by DOI once per database file, not
  on every call.
- `bind_rows()` skips its column alignment when every part already has the
  output's columns and types; `casefold()` no longer uses `str.translate`
  (about 5x faster on non-ASCII text).
- data_check builds its column statistics as rows, one frame per file;
  repo_check skips the 13 platform listings when a paper has no repository
  links; bibr 12 column typing skips R's coercion for plain values.
- `grepl()`, `text_search()` and the codebook scans share one matcher with
  the literal prefilter (`pytacheck._r.regex.detector()`), replacing three
  copies of it; the hand-written prefilters of open_practices and of the
  live-data search are gone.
- Fixed: inside `run_session()` (the CLI and the API), a paper building one
  of its tables from its JSON records changed its memo key, so a later run of
  the same module on it (a nested repo_check, say) was computed again.
- The accuracy harness runs every module of an input on one shared paper,
  as `report()` does, instead of a copy per output, and flags a module that
  changes it: through the Paper API (per output), or anywhere in its content,
  a list inside a cell or a nested `extra` entry included (at the end).

### Fixed: metacheck bugs pytacheck no longer reproduces

See `docs/UPSTREAM_ISSUES.md`; every affected parity case is a documented
`known_divergence`.

- Reading papers (U16-U18, U21, U22, U24-U28, U158): Grobid TEI conversion
  keeps the space before numbers (`Hamilton 1964`, `p = .27`) and joins
  reference DOIs split by line breaks; tables without rows, references
  without text, one-ended page ranges, nested and back-matter divs, the paper
  DOI (header only) and figure/table rows are handled correctly; URL hrefs are
  printed once, where their link is, without dropping link words; Grobid
  `<s>` tags are unwrapped. bibr 12.x keys match exactly, array values in
  scalar fields are read row by row, `df` arrays keep both degrees of
  freedom, doubles keep full precision and date-times four-digit years.
  `extract_urls()` no longer takes "et al.Word" for a host name.
- Archives (U31-U54, U69-U76, U152, U155, U156): Dataverse hosts are matched
  per URL and Figshare DOIs are no longer Dataverse links; files left out by
  the size caps stay in the `*_file_download()` tables; zero-byte files,
  string licences, NA URLs and CP437 zip names no longer abort a call; Dryad
  file listings follow their pages; DataONE, 4TU, OSF and GitHub id and host
  detection fixed (`osf.io/prereg` is not an OSF id); exact CRCs, whole-member
  inflation and real decompressed sizes in the repository download code.
- data_check, codebook_check, psychds_check and the data-file helpers (U55-U64,
  U78, U91-U93, U98-U100, U112, U113, U154): blank column-name collisions are
  reported; outliers with infinite quartiles and fractional scale ranges no
  longer fail; 1e5 is no longer a "typo of 5"; `V1-V10` codebook ranges are
  expanded; `.rds`/`.RData` keep column labels; manifests keep nulls and full
  precision; Latin-1 files are read; data_check returns a full result when
  nothing is readable and counts extracted files correctly; codebook_check
  de-duplicates definitions per paper and matches acronyms literally;
  psychds_check keeps valid Psych-DS names and root files in place, gives each
  paper of a list its own summary row and accepts `paper = NULL`.
- code_check, repo_check, reg_check, the reproducibility checks and the
  statistical-output readers (U66-U68, U78, U86-U89, U120-U123, U132-U148,
  U153): `.qmd`/`.ipynb` languages are read from the local copy; empty code
  files are analysed; files are de-duplicated per file, not per name; local
  archives count as archives; `code_check(paper = NULL)` runs on a local
  folder; SPSS template cells, SMCL hex characters and HTML `<meta charset>`
  are honoured; `match_reported_output()` keeps scientific-notation precision.
- Text modules (U23, U30, U82-U85, U95, U97, U101-U111, U114-U119,
  U124-U127): paper lists are summarised per paper (open_practices,
  causal_claims, ethics_check, the ref_* modules); failed lookups and odd
  inputs no longer stop modules (ref_pubpeer, coi_check, funding_check,
  prereg_check, stat_effect_size); false positives and negatives removed
  (lowercase r as R code, acknowledgment sections, randomisation spelling and
  negation, star notes in p-value checks, beta as eta-squared, "partial"
  applied to every value of a sentence); ref_miscitation no longer quotes
  "NA" for references without an in-text citation; typos and plurals in
  module text are fixed.
- Empty inputs (U79): every module now runs on an empty paper list and on
  `paper()`, giving its empty or "na" result instead of metacheck's errors
  (ethics_check, power, prereg_check, reg_check, repo_check and the modules
  that run it, the ref_* modules); `ref_table()` and the `*_links()` functions
  return typed empty tables.
- ref_retraction matches RetractionWatch DOIs whatever their case (U157):
  13,123 of 61,376 RetractionWatch DOIs have capitals, e.g. the retracted
  Wakefield et al. (1998) Lancet paper, which a lower-case citation now flags.
  ref_replication (FLoRA) and ref_miscitation ignore DOI case too.
- marginal cites "Olsson-Collentine A, van Assen MALM, Hartgerink CHJ (2019)"
  instead of metacheck's mangled author string (U3); reg_check's light is
  "fail" instead of "error" when every RegCheck comparison fails (U30).
- repo_check reports an unfound DSpace or PsychArchives item as a failed
  repository (U43); code_check and data_check use the singular for a count of
  one ("In 1 code file", "1 distinct respondent was flagged") (U82).
- Core (U2, U4-U10, U12-U15, U19, U20, U77, U79, U80, U128-U131, U149-U151):
  the API's `/paper/search` `section` filter works; `stats()` keeps checkable
  results next to unparseable ones, rejects invalid arguments and reads the Q
  subtype correctly; `extract_eq()`/`extract_tests()` treat each paper of a
  list separately; DOI, Crossref, OpenAlex and PubPeer lookups return one row
  per input and DataCite titles are read correctly; RetractionWatch rows
  without a DOI no longer match every reference; `llm()` keeps the answers of
  sanitised and partly failing texts; `text_search(return = "section")` keeps
  section headers; reports keep the headings, callouts and authors metacheck
  dropped.
