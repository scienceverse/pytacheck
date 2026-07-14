# Pytacheck

Pytacheck is the Python production engine for the six validated research checks from
[Scienceverse/Metacheck](https://github.com/scienceverse/metacheck). It accepts bibr JSON,
builds one cached paper context, and runs the default checks without an R runtime or remote
model call:

1. `power`
2. `marginal`
3. `stat_check`
4. `stat_effect_size`
5. `stat_p_exact`
6. `stat_p_nonsig`

The initial production milestone is usable but deliberately narrow. Its checked-in,
field-aware R oracle establishes aggregate parity on the `to_err_is_human` fixture, including
module order, traffic lights, summary counts, extracted tokens, classifications, and row order.
That is evidence for the tested boundary, not a claim of complete behavioral equivalence for
all papers.

## Run the service

With [uv](https://docs.astral.sh/uv/) installed:

```bash
uv sync --frozen
uv run pytacheck serve --host 0.0.0.0 --port 2005
```

Check readiness and submit a bibr paper through the Metacheck-compatible multipart route:

```bash
curl http://localhost:2005/ready
curl -F file=@paper.json http://localhost:2005/paper/check
```

The versioned native route accepts a JSON envelope and an optional module selection:

```bash
curl -X POST http://localhost:2005/v1/checks \
  -H 'content-type: application/json' \
  --data '{"paper":{"paper_id":"demo","text":[],"section":[]},"modules":["marginal"]}'
```

Other operational routes are `GET /health`, `GET /paper/modules`, `POST /paper/module`, and
`GET /metrics`. The container uses port 2005 and runs as an unprivileged user:

```bash
docker build -t pytacheck:local .
docker run --rm -p 2005:2005 pytacheck:local
```

## Measured performance

This repository includes a machine-readable benchmark that validates and checks a real,
non-empty paper fixture:

```bash
uv run python -m benchmarks.benchmark_default_modules --iterations 20
```

One local run on 2026-07-14 used Python 3.12.12 on
`macOS-26.5.1-arm64-arm-64bit`, with fixture SHA-256
`c99be5ea276910a6e7fbd0301ee0b89e0ccc7c7af5d27727e018d573dc646da4`:

| Measurement | Result |
| --- | ---: |
| Cold default check | 8.39 ms |
| Warm default check p50 | 4.41 ms |
| Warm default check p95 | 5.49 ms |
| Warm throughput | 229.10 papers/s |
| bibr validation + check p50 | 4.39 ms |
| bibr validation + check p95 | 5.06 ms |

| Module | p50 | p95 |
| --- | ---: | ---: |
| `power` | 0.56 ms | 1.22 ms |
| `marginal` | 0.39 ms | 0.56 ms |
| `stat_check` | 0.68 ms | 0.88 ms |
| `stat_effect_size` | 0.18 ms | 0.32 ms |
| `stat_p_exact` | 0.07 ms | 0.12 ms |
| `stat_p_nonsig` | 0.04 ms | 0.08 ms |

These figures describe that run, not a workstation-independent guarantee. CI uses a generous
two-second warm smoke budget and records machine-readable results instead of treating local
hardware numbers as thresholds.

## Compatibility limits

- Ingress is tested against bibr schema 10.2 and 10.6, tolerates additional fields, and rejects
  unsupported schema major versions.
- The multipart `/paper/check` contract covers the Platform integration; `/v1/checks` is the
  stricter native JSON contract.
- `report_html` is always an empty string. Pytacheck does not include Metacheck's Quarto or Shiny
  renderer.
- Experimental R modules, archive integrations, and LLM modules are outside this milestone.
- R is used to capture development oracles only and is not a production dependency or container
  component.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the R-lab/Python-production ownership model and
[docs/architecture.md](docs/architecture.md) for the runtime and parity boundaries.

## License and provenance

Pytacheck is a port of AGPL-licensed Metacheck behavior and is licensed under the GNU Affero
General Public License, version 3 or later. See [LICENSE.md](LICENSE.md).

Private development does not by itself settle the obligations of a public network deployment.
Before offering a modified AGPL service publicly, review the corresponding-source requirements
and the licenses of incorporated work with appropriate legal guidance. This note is practical
project guidance, not legal advice.
