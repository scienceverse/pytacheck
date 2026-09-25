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
