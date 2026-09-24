You are maintaining pytacheck, the Python port of the R package metacheck. metacheck's
`dev` branch has moved; the submodule `upstream/metacheck` and the pin
(`parity/UPSTREAM.toml`, `src/pytacheck/_version.py`) already point at the new commit.

Read, in this order:
1. `.upstream-sync/brief.md` — what changed upstream (commits, files, functions, diff),
   which pytacheck files correspond to each changed R file, and which parity goldens
   changed when regenerated with the new metacheck (those are behaviour changes you must
   port).
2. `docs/PORTING.md` and `docs/PARITY.md` — binding rules.

Then port the upstream changes:
- Update the mapped Python files so their behaviour matches the new R code exactly.
  New R functions go to the location given by `porting/symbols.json` (regenerate it with
  `uv run python scripts/porting_symbols.py` after adding entries to `porting/map/*.toml`).
  New modules go to `src/pytacheck/modules/<name>.py`.
- Add or update parity cases (`parity/cases/*.yaml`) that exercise every changed
  behaviour, then regenerate goldens with R:
  `uv run python -m parity generate --area <area>` (R is installed; `$PYTACHECK_RSCRIPT`
  is set). Never edit golden JSON by hand.
- Port new/changed testthat tests to pytest.
- Update `CHANGELOG.md` (an "Upstream sync" entry listing the ported changes).
- If upstream changed the paper JSON schema, copy `inst/schema/paper.json` into
  `src/pytacheck/resources/schema/`; if demos changed, copy `inst/demos/*`.

Done means all of these pass:
    uv run python -m parity check
    uv run pytest -n auto -m "not network and not r"
    uv run ruff check . && uv run ruff format --check .

If something cannot be ported faithfully, reproduce R's behaviour as closely as possible,
mark the affected parity cases with `known_divergence: "<precise reason>"`, and explain it
in `.upstream-sync/notes.md` (it becomes part of the pull request description). Do not
weaken or delete existing parity cases to make them pass. Do not run git commands; the
workflow commits your changes.
