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
| U4 | `stats()` (R/stats.R) | `tryCatch(warning = )` drops all of a sentence's statcheck results on its first warning, so an unrelated unparseable p-value (`p = .05-.10`) removes valid results; any statcheck error also drops the sentence. Unknown arguments in `...` make every call error, and the error is swallowed, so `stats()` returns an empty table. A character vector input errors ("argument is of length zero"). |
| U5 | statcheck 1.5.0 | Q-test subtype detection looks for a lowercase `b` then `w` in the test name (`Q-Between(2)` → Qw). `RGX_P_NS` is case-insensitive while `[^a-z]` is not, which misaligns p-values across results via `data.frame()` recycling. `if (NA)` / length-2 `if()` errors (df = 0, r = ±1.00, `t(20) = (2.1`) abort the whole call. |
| U6 | `module_report()` (R/report.R) | the greedy `\\s*\\(.*email\\{.+\\})` gsub cuts a single `@author` line naming two authors down to the first name (`stat_p_exact`). |
| U7 | `report_qmd()` | a module `summary_text` with several elements errors in R ≥ 4.3 (`'length = 2' in coercion to 'logical(1)'`), failing the whole report. |
| U8 | `link(type = "doi")` | turns `NA` into a link to `https://doi.org/NA`. |
| U9 | `json_expand()` | a `_row` key holding nested objects with NA cells is mangled by jsonlite's `rn[is.na(rn)] <- …` replacement through a logical matrix; not reproduced. |
| U10 | `extract_tests()` | fails on paper lists and on some papers whose sentences mix anchors with unknown statistic names; `extract_eq()` errors on `NA` paper ids and orders `grp_id` inconsistently. |
| U11 | `test_paper()` | paper ids differ on every run even after `set.seed()`, so goldens built from test papers compare everything except `paper_id`. |
