# Pytacheck provenance notice

Pytacheck is an independent Python production port of selected behavior from
[Scienceverse/Metacheck](https://github.com/scienceverse/metacheck), which is distributed under
the GNU Affero General Public License, version 3 or later.

The initial six-module port was reviewed against upstream commit
`0291d575628b0c8cec56eb64c944ad269c91edc4` on 2026-07-14. The relevant upstream source paths
were:

- `inst/modules/power.R`
- `inst/modules/marginal.R`
- `inst/modules/stat_check.R`
- `inst/modules/stat_effect_size.R`
- `inst/modules/stat_p_exact.R`
- `inst/modules/stat_p_nonsig.R`
- `R/text-extractors.R`

Pytacheck contains substantial modifications: a typed bibr JSON boundary, shared immutable
extraction context, independent Python implementations, FastAPI serving, bounded production
ingress and computation, Prometheus instrumentation, and field-aware parity tests. The original
R code is not included in the Python package or runtime container. See `LICENSE.md` for this
repository's license and `tests/parity/fixtures/to_err_is_human.expected.json` for the frozen
module-source hashes used by the aggregate oracle.
