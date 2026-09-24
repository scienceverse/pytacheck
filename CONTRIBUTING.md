# Contributing to pytacheck

pytacheck is a port: **metacheck's behaviour is the specification**. Changes to what
a check does (patterns, thresholds, report wording) belong upstream in
[metacheck](https://github.com/scienceverse/metacheck); pytacheck picks them up
through the automated upstream sync.

Contributions here are welcome for: parity bugs (Python differs from R), performance,
packaging, the CLI/API/Docker surface, bibr integration, and documentation.

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
- `uv run ruff check . && uv run ruff format --check . && uv run mypy` must pass.
- Keep `import pytacheck` light: import heavy dependencies inside functions.
