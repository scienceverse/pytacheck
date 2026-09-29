# Changelog

This is the Python version of metacheck. It has its own version line, starting
at 0.4.0 (0.4.0a1 is the first release); the version does not follow the R
package's. The R commit each release is compared against is in
`parity/UPSTREAM.toml` and in the notes of the release.

## Unreleased

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
