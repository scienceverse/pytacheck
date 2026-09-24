# Upstream issues found while porting

pytacheck reproduces metacheck's behaviour, including its bugs (see
[PORTING.md](PORTING.md)). This page collects problems found in metacheck (`dev`,
pinned in `parity/UPSTREAM.toml`) so they can be reported upstream, and records the
few places where pytacheck deliberately differs.

## Deliberate differences

| # | metacheck behaviour | pytacheck | why |
|---|---|---|---|
| D1 | `.read_bibr()` only understands bibr schema ≤ v10.x; a bibr ≥ 0.4 file (schema v11/v12: `metadata`, `source`, `target_id`, 0–1 scores) is read with no title, DOI, keywords or file info, xrefs pointing at the wrong rows and match scores on the wrong scale. | v11/v12 payloads are converted to the v10.x layout first (`pytacheck.io.bibr_schema`); v10.x and older files are read exactly like metacheck. | Tight bibr integration needs current bibr output to work. |

## Bugs reproduced faithfully

| # | where | issue |
|---|---|---|
| U1 | `DESCRIPTION` | `Depends: R (>= 4.3.0)`, but the package needs R ≥ 4.5 (`tools::md5sum(bytes =)` in `paper()`, R ≥ 4.5.0) and `sort_by()` (R ≥ 4.4.0) in `module_list()`. |
| U2 | `inst/plumber/endpoints/paper.R` `/paper/search` | forwards the documented `section` parameter to `text_search()`, which has no such argument, so any request using it fails with "unused argument". pytacheck's API returns the same 500. |
| U3 | `format_ref()` / R `bibentry` | the author string of hand-written references (e.g. in `marginal`) is re-parsed by R's bibentry formatter into "…Hartgerink &amp;, J. CH"; pytacheck stores the HTML R produces. |
