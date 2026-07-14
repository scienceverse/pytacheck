# Contributing to Pytacheck

Pytacheck treats R Metacheck as the research laboratory and Python as the production serving
implementation. The split keeps behavioral-science contributors working in their established R
toolchain without putting R, Plumber, or Quarto in the serving image.

## Module ownership

- New research checks originate, mature, and receive validation evidence in Metacheck R.
- A validated R module is ported only when there is a concrete production need.
- Experimental R modules do not need a Pytacheck equivalent and must not be added to the Python
  defaults merely to make the catalogues look identical.
- The six Python defaults remain ordered and named exactly like `metacheck::module_defaults()`.

Porting a module requires a checked-in, inspectable R oracle—not a generated similarity score.
Tests must compare meaningful fields such as extracted tokens, classifications, summary counts,
traffic lights, and source order. Floating recomputations use a documented absolute tolerance;
text and categorical values remain exact.

## Port workflow

1. Establish and validate the behavior in R, including edge cases.
2. Add or update an R capture script and frozen input/expected fixture under `tests/parity/`.
3. Record the Metacheck commit, package versions, input hash, normalization, and any deliberate
   cross-language difference. Oracle capture must disable model/API calls.
4. Add a failing Python test, implement the smallest shared computation, and make it pass.
5. Run focused tests, the aggregate oracle, the real-paper benchmark, and all repository gates.
6. If the port exposes an R bug or a generally useful optimization, add an R reproduction and
   upstream the fix rather than allowing the implementations to drift silently.

Do not put R, `rpy2`, an R source checkout, local papers, credentials, or oracle-generation tools
in the Python runtime image.

## bibr contract changes

Shared schema changes must keep both contract fixtures green:

```bash
uv run pytest tests/contract -q
```

At minimum, verify bibr 10.2 and 10.6 inputs. Pydantic ingress is intentionally tolerant of
additional fields so a compatible minor-version addition does not break production. Major-version
changes need an explicit migration and tests; do not accept them silently.

## Local verification

Install exactly the locked development environment and run the same gates as CI:

```bash
uv lock --check
uv sync --frozen --all-groups
uv run ruff check .
uv run ruff format --check .
uv run mypy pytacheck
uv run pytest -q
uv run python -m build
uv run python -m benchmarks.benchmark_default_modules --iterations 20
docker build -t pytacheck:local .
```

When Docker is unavailable, say so explicitly in the change report; static Docker tests are not a
substitute for an actual image build. Never report estimated speed as measured performance.

## Licensing

Contributions are accepted under AGPL-3.0-or-later. A public network deployment can trigger source
availability obligations for the running modified version. Maintainers and deployers should review
those obligations with appropriate legal guidance; this is not legal advice.
