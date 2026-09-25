# Changelog

pytacheck versions track the metacheck release they are in parity with:
`X.Y.Z` = metacheck `X.Y.Z`, with a fourth component (`X.Y.Z.N`) for
pytacheck-only releases. The pinned metacheck commit is in `parity/UPSTREAM.toml`.

## Unreleased

Complete rebuild as a parity-tested Python port of metacheck `dev`
(`85c8c87`, metacheck 0.3.1) plus scienceverse/metacheck#423 (`b239264`,
bibr export schema 12.0), which pytacheck targets ahead of its merge.

- R-faithful regular expressions (TRE leftmost-longest and PCRE semantics),
  number formatting, collation and dplyr idioms (`pytacheck._r`).
- bibr-schema `Paper`/`PaperList` with lazily materialised tables; reading
  bibr JSON directly or in-process from bibr (`pytacheck[bibr]`).
- Module system compatible with metacheck's (`module_run`, chaining,
  `get_prev_outputs`, `module_list`), plugin modules via entry points.
- Parity harness running metacheck in R; goldens regenerated in CI.
- Shared HTTP layer with httr2's retry/rate-limit behaviour; replay of
  metacheck's recorded API fixtures in tests.
- CLI (`pytacheck`), Docker images (with and without bibr), and a scheduled
  upstream-sync workflow that ports new metacheck commits automatically.

### Fixed: metacheck bugs pytacheck no longer reproduces

See `docs/UPSTREAM_ISSUES.md`; every affected parity case is a documented
`known_divergence`.

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
