# Changelog

This is the Python version of metacheck. It has its own version line, starting
at 0.4.0 (0.4.0a1 is the first release); the version does not follow the R
package's. The R commit each release is compared against is in
`parity/UPSTREAM.toml` and in the notes of the release.

## Unreleased

### Changed: reproducibility_check runs the paper's code in Docker by default

- **Changed:** `reproducibility_check(execute=True)` now runs the paper's R code in a Docker container: `sandbox` defaults to `"docker"` (metacheck's default is `"process"`, which runs the code on your own machine, D62). In the run phase the container has no network, a read-only root file system, a throwaway sandbox directory, a non-root user and no capabilities. Without Docker, `execute=True` stops with an error that says why and how to go on, and nothing is run; it never falls back to your machine.
- **To run on your machine:** pass `sandbox="process"`. It works as before, and each run warns (`PytacheckWarning`) that nothing is isolated, so use it only for code you trust. A caller that passes metacheck's own default `c("process", "docker")` now gets a `ValueError`: name the sandbox you want.
- **Fixed with it:** the sandbox directory is writable from the container, so the paper's code could leave a symbolic link in it that named a file on your machine, and the host followed that link when it wrote the next runner script (the file was overwritten) or read the next script (its text went into the report). After every container, links that leave the sandbox directory, and named pipes, are now removed. This applies to `sandbox="docker"` only; with `sandbox="process"` the code runs as you anyway. The report also shows the code's output in a fence that its own output cannot close (a longer fence is used when the output holds a run of four or more backticks), so output cannot add a `{r}` chunk that Quarto would run when the report is rendered.
- **Unchanged:** the static phase (`execute=False`) needs no Docker and runs nothing. The install phase (only with `install_missing=True`) still has network access, by design, and sees only the install script and the package library.

### Added: data package checks

- The `datapackage` pack, in every install, checks the folder or archive of data, code and documentation that comes with a paper: `datapackage::package_files`, `datapackage::package_structure` and `datapackage::package_docs` (and `package_pii`, below), and the preset `datapackage::default`, which adds `metacheck::data_check` and `metacheck::codebook_check`. Each check also returns a checklist (item, title, status `fail`, `warn`, `manual`, `pass` or `na`, detail) for a data steward to work down. A pack can supply its own policy as a preset.
- `datapackage::package_pii` looks for personal data by value in the data files of a package (csv, tsv, Excel, ODS, SPSS, Stata, SAS; the first 5000 rows of each): Dutch citizen service numbers (BSN, with the 11-check), student numbers (only in a column named like one), phone numbers (Dutch and international), IPv6 addresses, Dutch postcodes and columns of people's names (the column name says so and the values read like names). It is in `datapackage::default`, has one checklist item for each kind (`pii_bsn`, `pii_student_number`, `pii_phone`, `pii_ipv6`, `pii_postcode`, `pii_name`) and `pii_scan` for files that were not read, and an extra `hits` table. It reports files, columns and counts, never a value, and a hit is a hint for a person (status `manual`). It does not change `metacheck::data_check`, the port of R's checks. Details and limits: docs/DATAPACKAGE.md.
- `metacheck package PATH` (also `pytacheck package`) runs them on a folder, or on a zip or tar archive extracted to a temporary folder that is removed afterwards, without a paper. It takes `-m`, `--preset`, `-a`, `--offline`, `--record` and `--json` like `run`, and `-o FILE` (or `-f html|qmd|md`) writes a report titled with the package's name. A path that cannot be opened exits with status 2.
- In Python: `metacheck.datapackage.check_package()` and `report_package()`. Modules that take `local_path` and `local_only` are given the package's folder and `local_only=True`, so nothing is looked up online; the run record has a new `package` key (name, source, archive) in place of a paper. Everything runs on your machine; the one download is `data_check`'s concept model (see "a local concept classifier" below), which `-a concepts=rules` skips. Guide: docs/DATAPACKAGE.md.

- The local app has a second page, "Check a data package" (`/package`; `metacheck-app --page package` opens it, also in an app that is already running). Name a folder or upload a zip (up to 1 GB), choose a preset and run: it shows one row per check and the checklist, coloured by traffic light, and offers the report to download. The presets listed are the ones that run a `datapackage::` check, found through the pack registry, so a pack's own policy preset appears once the pack is installed and the app is started again. Everything runs on the computer that runs the app. An uploaded archive is unpacked with the guards of `check_package()` and lower limits (5 GB, 100,000 files). The page reads the folder you type, which the paper page never does (it reads only uploads), so it reads only inside your home folder: the path is resolved first, links are followed, and the folder that results must be inside home, or inside a folder of the new setting `METACHECK_APP_ROOTS` (separated like a `PATH`). Otherwise anything that can reach the local app with its token could make it read any folder you can. The hosted app (`--hosted`) has no such page.
- On that page the local classifier for the concepts of data columns is on by default, as in `metacheck package`. Without the `concepts` extra, which the installer does not add, the page uses rules only and says so under the results, with the command to install the extra; untick the box to download nothing. `check_package_source()` in `metacheck.app.package` takes `local_classifier=True` by default.
- `check_package()` and `report_package()` take `max_bytes` and `max_files` to lower the limits on an extracted archive.
- The installers take `--steward` (`-Steward` on Windows, or `METACHECK_STEWARD=1`): the same install, and the app opens on the data package page. It does so only if the installed app knows `--page`; the pinned commit is not moved by this change, so until it is, a steward install opens the paper page.

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
- `detect_many(patterns, text, ..., literals="auto")` looks the words up in an index of the text's distinct words when it has 64 patterns or more (a few patterns still scan the text, since building the index costs about 2 ms on a 94,000-character paper); `literals="scan"` and `literals="index"` choose either way. The answers are exactly the scan's: a word of printable ASCII without a space can only sit inside one whitespace-separated word of the text, so searching the joined distinct words finds it wherever it lies in a word (`ann` in `planning`, `ab.cd`), and any other string is looked up in the text itself. On the codebook scan (scales and tasks, 1,624 patterns, a 94,000-character paper) the index makes `detect_many` about 20-27% faster than the scan (best of 15 runs 92 ms against 114 ms, median 110 ms against 150 ms; a first call in a fresh process 234 ms against 279 ms). `METACHECK_LITERALS=off` turns off both.

### Text search on an indexed paper

- `text_search()` (every `return_` mode, lists of patterns, `exclude`, `search_header`), `extract_eq()`, `extract_urls()`, `extract_p_values()`, `text_expand()` and the one-paper `paper_table()` now work on an index of the paper's sentences (`metacheck.core`), built once per paper and kept on it: each pattern is tested on each sentence at most once, however many searches ask, and a sentence whose text lacks a word the pattern needs (`required_literals`) is skipped without running the regex. The results do not change; the code before is kept in `tests/_legacy/`, and the tests compare against it (search chains generated at random, every return mode, paper lists, tables and strings, and every built-in pattern on the accuracy papers against the regex itself).
- A paper read from Grobid XML or JSON keeps its tables as JSON records, and its index is reused for as long as it does. Once a table is a DataFrame, it may have been edited in place, so the index is built again for each search, except inside `run_modules()` and `report_module_run()`, which build it once per paper per run. `run_session()` is unchanged: it keeps module results, not indexes. `read()` builds the index while it reads the XML and finds the paper's equations with it. `paper_table()` of one paper no longer turns the paper's JSON records into a DataFrame.
- **Checking that modules do not edit papers:** with `METACHECK_CHECK_MUTATION=1` (set in CI), `run_modules()` and `report()` raise `StaleDocumentError` before the next module when a module edited a paper's text or section table in place, naming the module; without it nothing changes. Modules must copy a paper before changing it (docs/MODULES.md).
- The funding module takes the words a pattern needs from `required_literals()` instead of its own pattern reader, and `ethics_check` no longer filters sentences with its own list of words first: its 63 patterns are filtered by their own literals, and its two searches share one index of the paper. `text_search()` accepts such an index as its first argument. Results are unchanged. The hand-kept word filters of the `*_links()` functions of the archives stay, since a scan for each of their many words over a whole paper list was slower than they are.

### Changed: papers that share an ID, and a paragraph repeated word for word

- **Changed: papers with the same ID in one list are checked as separate papers.** metacheck leaves a repeated `paper_id` to each function, with different results on the same list: some modules stop, some pool the papers' sentences or references into one, and the summary repeats rows. Now the first paper keeps its ID, a later one is renamed `ID~2`, `ID~3`, ... (a copy; your papers keep their IDs), and one warning (`PytacheckWarning`) names them. Every module, `paper_table()`, `text_search()`, `module_run()` and `report()` then see distinct papers: a module gives the same numbers as running the papers one by one, and `report()` of such a list writes one file per paper. On the 21 accuracy papers read as one list (three IDs are shared by six papers), for example: `ethics_check` says "6 of 21 papers" where it said "6 of 18", `stat_p_exact` finds 469 p-values where it found 477, `stat_effect_size` 428 rows where it found 446, and `ref_consistency` returns a table where it stopped. A single paper, and a list with distinct IDs, give the same results as before. Register entry: U208 in `docs/UPSTREAM_ISSUES.md`.
- **Changed: a paragraph repeated word for word counts once.** The p-value search (`extract_p_values()`, so `all_p_values`, `stat_p_exact` and `stat_p_nonsig`) counted a sentence that appears twice in a paper, with every cell equal, twice; every other search already returned it once, as metacheck's `unique()` does. Now it counts once. Only an exact repeat counts: a different text id or paragraph, different whitespace, punctuation or case make a different row, and a match repeated inside one sentence still counts each time. A table or strings given to `text_search()` are searched as they are. None of the 21 accuracy papers repeats a sentence, so their results do not change. `stat_p_exact` and `stat_p_nonsig` are validated checks: this change and the one above wait for the maintainer's OK on the pull request (D60 and U208 in `docs/UPSTREAM_ISSUES.md`).
- `validate()` makes one test paper for each distinct ID of its ground truth (metacheck made identical papers for a repeated ID).

### The app

- The page has a header with the version and links to ScienceVerse and the Metacheck, Pytacheck and bibr repositories on GitHub, repeated under the credit line. The online and data options sit above the buttons they apply to, the bibr key field is hidden where the server supplies the key (that key wins over a typed one), and result cells are coloured by their traffic light (the word stays, so colour is never the only signal).
- The page starts on the bibr reader where the server supplies the bibr key (`SCIVRS_API_KEY`), as a hosted server does, and on GROBID otherwise, since GROBID needs no key.
- The bibr option talks to bibr serve and the hosted bibr service in front of it: it sends a PDF to `/papers/jobs`. Before, it sent every PDF to the Scienceverse platform's `/jobs`, which those services answer with 404, so the page said the bibr service could not be found. `PYTACHECK_BIBR_BACKEND=scivrs` says that the address in `PYTACHECK_BIBR_URL` is the platform. For an address from metacheck's public server list, the entry's `protocol` decides; without one, an entry whose `api_key` is `SCIVRS_API_KEY` is the platform, and any other entry is bibr serve. A hosted server does not start with a `PYTACHECK_BIBR_BACKEND` other than `bibr` or `scivrs`, or with a `PYTACHECK_BIBR_URL` that cannot work, such as plain http to another machine for bibr (deploy/space/DEPLOY.md).

### Fixed: GitHub and GitLab links of 12.x papers

- `github_links()` and `gitlab_links()`, and so `repo_check`, find the GitHub and GitLab links of papers in the 12.x format: bibr 12.x JSON, and Grobid TEI, which is read as 12.x by default. They searched the url table's first column, which in a 12.x paper is the number `url_id`, so no such repository was found; they now search `href`, as the other repository finders do. metacheck has the same bug (docs/UPSTREAM_ISSUES.md U196). On 450 Psychological Science papers read from Grobid TEI, 9 papers now give 9 GitHub and 1 GitLab repositories, against none before. More repositories found means more calls to api.github.com, which allows 60 an hour without a token.

### Fixed: open_practices on papers from older bibr exports

- `open_practices` stopped with "cannot use 'tuple' as a dict key" on papers read from an older (pre-12) bibr JSON export, whose text table has a list column (`_bbox_2d`): 36 of the first 40 papers of a bibr validation set. The list is now compared by its elements, as dplyr does when it joins on a list column. Results on papers without a list column, and on 12.x papers, do not change.
### Fixed: p-values and statistics that were not read

These change the results of validated checks (`all_p_values`, `stat_p_exact`, `stat_p_nonsig`, `stat_effect_size`); metacheck has the same bugs (docs/UPSTREAM_ISSUES.md U204, U205).

- `extract_p_values()` reads p written as "ps", "p's" or "p-values": "ps < .05", the usual way to report several p-values at once, was not a p-value. In 450 Psychological Science papers read from Grobid TEI it occurs 216 times in 70 papers. `stat_p_exact` counts "ps < .05" as an imprecise p-value, so it turns red on 3 of the 21 papers of the realistic corpus.
- Scientific notation may use "×", leave out "^" and use the Unicode minus: "p = 1.8 × 10 -6" was read as p = 1.8 and is now 1.8e-06.
- `extract_eq()` and `extract_p_values()` read the Unicode minus sign (U+2212), which text from PDFs often has (bibr keeps it, Grobid writes "-"): "t(28) = −2.15" and "d = −0.80" were not found, so `stat_effect_size` saw no t-test in such a sentence. `rhs` and `p_value` now have "-"; the matched `text` is kept as written.

### Fixed: e-mail addresses are not URLs

- `all_urls` and `extract_urls()` no longer list the parts of an e-mail address as URLs: for `k.aristovich@ucl.ac.uk` metacheck lists `k.aristovich` and `ucl.ac.uk`, and `gmail.com` for every address at gmail.com. A URL that has an `@` after its host (`twitter.com/@user`) is still listed. On 120 papers read from bibr 12.x, 274 of 575 rows were such fragments (214 of 835 in 86 older bibr papers, 12 of 2,584 in 450 Grobid TEI papers); a paper whose only matches were e-mail addresses had the traffic light `info` and now has `na` (22 of the 120). This changes the result of a validated module; metacheck has the same bug (docs/UPSTREAM_ISSUES.md U206).

### Fixed: ref_consistency on papers from older bibr exports

- `ref_consistency` counts the citations of an older bibr JSON export (bibr 0.3.0 and the v10 format), which marks them `bib` instead of Grobid's `bibr`. Before, such a paper had no citations: every reference was reported as not cited and the light was red. On 120 platform papers 4,973 of 4,973 references were reported, now 922, and 20 papers are green instead of none (86 older bibr papers: 3,700 to 375, 11 green). A 12.x export and Grobid TEI are unchanged. What the module still reports is mostly bibr's own noise (citations it did not link, author-year styles). metacheck has the same bug (docs/UPSTREAM_ISSUES.md U207).

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
