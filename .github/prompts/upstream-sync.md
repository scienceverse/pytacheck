You are maintaining pytacheck, the Python port of the R package metacheck. metacheck's
`dev` branch has moved; the submodule `upstream/metacheck` and the pin
(`parity/UPSTREAM.toml`, `src/pytacheck/_version.py`) already point at the new commit.

Read, in this order:
1. `.upstream-sync/brief.md` — what changed upstream (commits, files, functions, diff),
   which pytacheck files correspond to each changed R file, which parity goldens
   changed when regenerated with the new metacheck (those are behaviour changes you must
   port), the marked cases whose golden changed (`r_changed`) or that now match R
   (`xpass`), and the accuracy report on the realistic corpus before porting.
2. `docs/PORTING.md` and `docs/PARITY.md` — binding rules, above all the accuracy
   contract (`docs/PORTING.md`, section 1).

Then port the upstream changes:
- Update the mapped Python files so that pytacheck does what the new R code does: the
  same checks, decisions and reported results (traffic lights, tables, summary and
  report text, extracted values) on realistic inputs, and the same public API. Write
  idiomatic Python with the shared helpers (`pytacheck._values`, `pytacheck._json`,
  `pytacheck.http`); do not emulate R internals (error texts, C-library quirks on
  malformed input, R type details).
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
    uv run python -m parity check --jobs 0
    uv run python -m parity accuracy --gate
    uv run pytest -n auto -m "not network and not r and not parity"   # parity check ran them
    uv run ruff check . && uv run ruff format --check .

The accuracy report (`docs/PARITY.md`, "Accuracy") runs every offline module on the real
papers and repositories of `parity/accuracy/matrix.toml` and scores pytacheck's results
against metacheck's (the goldens under `parity/accuracy/golden/` were regenerated with the
new metacheck). Port the changes until its differences are those that
`parity/accuracy/expected.yaml` explains. A new difference that is a metacheck bug pytacheck
fixes, or a deliberate difference, gets an entry there citing its U- or D-entry, like a mark;
an entry that no longer explains anything (`stale`) is removed. Changes to that file, to
`matrix.toml` or `parity/corpus.toml`, to the marks of tier-1 (realistic) cases, a tier-1
case leaving tier 1, or changes to D-entries make the pull request a draft that needs human
review, so keep them to what the upstream changes require and explain each in
`.upstream-sync/notes.md`.

Every difference from R's goldens is marked, never hidden:
- If the new R code is clearly wrong (a crash on valid input, a mis-parse, a wrong count,
  a false positive or negative, silent truncation), do the right thing instead: add a
  U-entry to `docs/UPSTREAM_ISSUES.md` and mark the affected cases
  `known_divergence: {kind: r_bug_fixed, ref: U<n>, reason: ...}`. A fix that only
  corrects wording (a typo, a plural) adds `r_text` substitutions to the mark, so the case
  is still compared with R. Keep a bug only with a written reason why fixing it would make
  results worse.
- Other differences use the other kinds of `docs/PORTING.md` section 1, with a reason (a
  D-entry for `better_logic` and `deliberate`; `c_quirk` and `type_detail` only on
  synthetic cases, never on realistic ones).
- Marks of generated cases go in the `parity/divergences/*.yaml` file of the area's lane
  (`docs/PORTING.md`, section 6); hand-written case files carry them inline.
- A marked case that now matches R (`xpass`) means upstream fixed the bug: remove its mark
  and say so.
- Every marked case is locked (`parity/lock/<area>.json`, see `docs/PARITY.md`). When
  `check` reports `r_changed`, `py_changed` or `unlocked`, first check that the mark still
  holds for the new outputs, then re-lock the case with
  `uv run python -m parity lock -k <case>`. Never re-lock to silence a difference you
  have not explained.
- Nothing may be invented: no references, statistics, links or LLM-derived claims that are
  not grounded in the paper or its materials.

Explain every new or removed mark in `.upstream-sync/notes.md` (it becomes part of the pull
request description). Do not weaken or delete existing parity cases to make them pass. Do
not run git commands; the workflow commits your changes.
