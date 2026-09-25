# Contributing to pytacheck

pytacheck is a port of [metacheck](https://github.com/scienceverse/metacheck) held
to an accuracy contract ([docs/PORTING.md](docs/PORTING.md#1-the-accuracy-contract)):
at least as accurate as metacheck on real papers, repositories and data, nothing
invented, and metacheck's bugs fixed rather than copied. Changes to what a check does
(patterns, thresholds, report wording) belong upstream in metacheck; pytacheck picks
them up through the automated upstream sync. A clear metacheck bug is fixed here and
recorded in [docs/UPSTREAM_ISSUES.md](docs/UPSTREAM_ISSUES.md) so it can be reported
upstream.

Contributions here are welcome for: accuracy bugs (an undocumented difference from R,
or a metacheck bug pytacheck still reproduces), performance, simpler and more
idiomatic code, packaging, the CLI/API/Docker surface, bibr integration, and
documentation.

## Setup

```bash
git clone --recurse-submodules https://github.com/thesanogoeffect/pytacheck
cd pytacheck
uv sync --all-extras
uv run pre-commit install
uv run pytest -n auto
```

To work on parity you also need the R reference (Linux: `parity/r/setup-reference.sh`,
elsewhere `Rscript parity/r/install-with-pak.R`) and `PYTACHECK_RSCRIPT` pointing at
its `Rscript`.

## Rules

- Read [docs/PORTING.md](docs/PORTING.md) and [docs/PARITY.md](docs/PARITY.md).
- Every behaviour change needs parity cases, with goldens generated from R
  (`uv run python -m parity generate --area <area>`); never edit `parity/golden/` by hand.
- Every difference from R is marked (`known_divergence: {kind, ref, reason}`), and a
  fixed metacheck bug or a deliberate change gets a U- or D-entry in
  `docs/UPSTREAM_ISSUES.md`.
- Use the shared helpers (`pytacheck._values`, `pytacheck._json`, `pytacheck.http`)
  instead of private copies.
- `uv run ruff check . && uv run ruff format --check . && uv run mypy` must pass.
- Keep `import pytacheck` light: import heavy dependencies inside functions.
