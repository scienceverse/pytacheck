# Parity with metacheck

pytacheck is checked against the real metacheck, in R, on every change. It is not
a bug-for-bug copy: it must be at least as accurate as metacheck on real research
outputs (the accuracy contract, [PORTING.md](PORTING.md) section 1). The parity
harness makes that checkable. It runs the R package and the Python port on the same
inputs and compares the results structurally. Every difference is either within the
margins below or recorded as a mark that cites
[UPSTREAM_ISSUES.md](UPSTREAM_ISSUES.md), and every mark is pinned by a lock, so a
recorded difference still fails when either side changes.

```
parity/
  UPSTREAM.toml          pinned metacheck repo/branch/commit/version (+ pull request)
  corpus.toml            the realistic corpus (tier 1) and explicit tiers
  cases/<area>.yaml      what to run: an R function or module and its arguments
  golden/<area>/*.json   R's results, canonically encoded (committed)
  divergences/*.yaml     the marks of generated cases (one file per lane or topic)
  lock/<area>.json       the divergence lock: one line per marked case
  r/run_cases.R          R runner: evaluates cases, writes goldens
  r/canonical.R          R -> canonical JSON encoder
  r/helpers.R            R helpers for cases (pc_catch(), base-R idioms)
  canonical.py           Python -> canonical JSON encoder
  compare.py             structural comparator and its options
  cases.py               case loading, marks, tiers, argument decoding, Python runner
  pyhelpers.py           Python twins of r/helpers.R
  lockfile.py            lock fingerprints and files
  __main__.py            `python -m parity generate|check|lock|list`
```

## Workflow

```bash
# once: an R (>= 4.5) with metacheck at the pinned commit, installed under a
# UTF-8 locale (otherwise R mangles non-ASCII names in metacheck's code and
# `generate` refuses to run)
LANG=C.UTF-8 LC_ALL=C.UTF-8 Rscript -e 'install.packages("pak"); pak::local_install("upstream/metacheck")'
export PYTACHECK_RSCRIPT=$(which Rscript)

python -m parity generate --area text          # R -> parity/golden/text/*.json
python -m parity generate --area text --only text_search.demo.significant
python -m parity check --area text -v          # Python vs goldens
python -m parity check --tier 1 --jobs 4       # the realistic corpus, 4 processes
python -m parity check --jobs 0 --md summary.md  # everything, one process per CPU
python -m parity lock --area text              # after changing a marked case
python -m parity list --tier 2                 # cases, their tiers, missing goldens
pytest -m "parity and tier1"                   # the same checks, as tests
```

Goldens are R's output and are only ever written by `python -m parity generate`
with the reference R; never edit one by hand. They are committed, so `check` and
`pytest` need no R.

`check` selects cases with `--area`, `--only <id or area/id> ...`, `-k <substring>`
and `--tier 1|2`, runs them in `--jobs N` processes (`0`: one per CPU), writes a
per-case JSON report (`--report PATH`; by default a new
`parity/_out/report-<time>-<pid>.json`, so runs side by side do not overwrite each
other) and, with `--md [PATH]`, a Markdown summary: statuses by tier and the marked
cases grouped by tier, kind and ref. It prints only the cases that fail or warn
(`-v` prints every case, with what it printed) and exits non-zero on any failing
status, a stale lock entry, or a file left in the repository root.

## What is compared

Both sides are encoded canonically (`canonical.R`, `canonical.py`) and compared by
`parity/compare.py`:

* strings exactly; doubles to a relative tolerance of 1e-9; integer and double
  vectors numerically; complex values as `[re, im]` pairs; a Python list is a vector
  only when its elements share one R type (otherwise it is a list, as R keeps it);
* named lists as maps, unless R repeats a name or the case sets `strict_names`:
  then by position, names in order;
* data frames: row count, column names and their order, every cell, and row order
  unless the case sets `unordered`;
* module outputs: every element (`table`, `summary_table`, `traffic_light`,
  `summary_text`, extras) except `paper` and `prev_outputs`; the `report` as prose:
  its text blocks must match, while R code chunks and pytacheck table widgets are
  skipped;
* differences of representation only are not differences: `NULL` vs empty
  (including a 0 x 0 data frame) vs a single `NA`, NaN vs NA, a list of scalars vs a
  vector, a one-row or one-column matrix vs a vector, a list of same-named records
  vs a data frame.

A case adjusts the comparison with `compare:`. An unknown option fails when the
cases are loaded.

| option | effect |
|---|---|
| `ignore: [path, ...]` | skip these elements (`table.formatted`, `report`) |
| `unordered: true` or `[path, ...]` | rows of these data frames may come in any order |
| `tol: 1e-6` | relative tolerance for doubles (default 1e-9) |
| `report: prose \| exact \| ignore` | how a module output's `report` is compared |
| `col_order: false` | compare column sets, not their order |
| `ws: true` | collapse runs of whitespace in strings |
| `strict_names: true` | compare named lists by position |
| `presence: [name or path, ...]` | compare only whether each value is there (see below) |

Paths are element names joined by `.`, with `[i]` for unnamed positions
(`table.formatted`, `[0].error`).

## Errors

**R's error, warning and message texts are never compared.** pytacheck raises its
own clear exceptions ([PORTING.md](PORTING.md), section 3), so the harness only asks
whether each side failed:

| R | Python | outcome |
|---|---|---|
| raises | raises | pass, whatever the two messages say |
| raises | returns a value | `fail`, unless marked `r_bug_fixed`, `better_logic` or `deliberate` (a mark of another kind fails too); the value is then locked |
| returns a value | raises | `error`, unless marked; the case is locked as `raises:<ExceptionType>` |

A case whose call fails needs nothing special: the golden records `ok: false` and
R's message (kept for people reading the golden and for `lock --suggest`, never
compared). R's warnings are listed in the golden too, and never compared either.

`$catch` is for an error that is one part of a larger result, such as one call of
several in a list. It yields the value, or `{error: true}` when the call fails, on
both sides, and drops the message:

```yaml
- id: errors.report
  r: identity
  py: tests.report.parity_helpers.identity
  args:
    x:
      $list:
        - {$catch: {$expr: {r: "report(1, 'marginal', tempfile(fileext = '.qmd'), 'qmd')",
                            py: "pc.report(1, 'marginal', __import__('tempfile').mktemp(suffix='.qmd'), 'qmd')"}}}
        - {$catch: {$expr: {r: "report_repository('no/such/folder')",
                            py: "pc.report_repository('no/such/folder')"}}}
```

Helper code that loops over inputs does the same with `pc_catch(expr)`
(`parity/r/helpers.R`) and `parity.pyhelpers.catch(fn)`, as `tests/db/review.R`
does for every recorded Crossref work.

Some outputs hold free error text in a field: a `repo_error` column, an LLM
`error_msg`. Where R's text there is its own crash message (a dplyr or `match()`
error) and pytacheck says what went wrong, the case compares that field for
presence only: whether each value is there (not NA, NULL or `""`), not what it
says.

```yaml
compare:
  presence: [gated_repos.repo_error]   # a path, or a name: every repo_error column
```

Name a field explicitly per case: a column called `error` is a boolean in some
outputs (statcheck's, `llm()`'s), and the comparison never guesses which strings are
error text.

## Tiers

Every case has a tier (`Case.tier`, `parity.cases.classify_tier`):

* **tier 1**: realistic inputs, the corpus listed in `parity/corpus.toml` (real
  papers, repositories, data and code files, recorded metacheck API responses).
  pytacheck must agree with metacheck on these within the margins of the accuracy
  contract;
* **tier 2**: synthetic edge cases. They check that Python behaves sensibly (no crash
  where a sensible result exists), not that it copies R's internals.

A case is tier 1 when its area is not `*_review` or `rcompat*`, it reads at least
one corpus input and nothing outside the corpus, and it builds no synthetic input
(`$test_paper`, `$df`, `$chr`/`$int`/`$dbl`/`$lgl`/`$list`, or an `$expr` that
builds a `test_paper()` or names no corpus path). A corpus input is a file that
exists: a case about a missing file is an edge case. An `$expr` that is only a
constant or a temporary path (`Inf`, `tempfile()`) is an argument, not an input.
pytacheck's own fixtures under `tests/*/fixtures` are made up to reach one branch,
so only the few that stand for real inputs are in the corpus. An explicit tier
overrides the rule: `tier: {level: 1 | 2, reason: ...}` on a case or at the top of a
hand-written case file, or an `[[override]]` table in `parity/corpus.toml` for
generated files (a case that runs a real input through made-up test modules or
edited module output). `check --tier` and the pytest markers `tier1`/`tier2` select
them. Today 1,424 cases are tier 1 and 8,432 tier 2.

## Marks: cases that differ from R on purpose

pytacheck fixes metacheck's bugs and improves on some of its decisions, so some cases
differ from their golden on purpose. Such a case carries a mark, `known_divergence:
{kind, ref, reason}`:

| kind | use for | needs |
|---|---|---|
| `r_bug_fixed` | a metacheck/R bug that pytacheck fixes | ref U<n>, whose status is fixed or partly fixed |
| `better_logic` | pytacheck decides differently on purpose, and better | ref D<n> |
| `deliberate` | a design decision (defaults, security, scope) | ref D<n> |
| `c_quirk` | an R C-library quirk on malformed or synthetic input | reason; tier 2 only |
| `type_detail` | an R type or attribute detail users never see | reason; tier 2 only |
| `r_nondeterministic` | R's own result is undefined or unstable | reason |

`ref` names an entry of [UPSTREAM_ISSUES.md](UPSTREAM_ISSUES.md), and every mark
needs a one-line `reason`. A tier-1 case cannot be marked `c_quirk` or
`type_detail`: a difference that reaches users on real inputs is not a quirk.

A hand-written case file marks its cases inline. A generated one is marked in
`parity/divergences/<lane>.yaml`, so regenerating the case file keeps the marks:

```yaml
"mod_repo_check/repo_check.figshare.share_link":
  kind: r_bug_fixed
  ref: U122
  reason: a Figshare listing without doi/license has NA metadata; R's data.frame() error overwrites the share link's informative error
"rcompat_regex/pcre.mid_char.*":       # a glob key: tier-2 cases only
  kind: c_quirk
  reason: ...
```

A key with `*` marks every case it matches (any run of characters), all of which must
be tier 2. A case is marked in exactly one place. `load_cases()` checks all of this
when the cases load, and reports every problem at once.

### Text that pytacheck corrects: `r_text`

When the only difference is text pytacheck corrects (a typo, a plural, a full stop
in report prose), the mark says how, with `r_text`: substitutions applied, in order,
to every string value of R's golden before the comparison.

```yaml
"mod_ref_accuracy_review/ref_consistency.text_missing":
  kind: r_bug_fixed
  ref: U83
  reason: "the caveat says 'likely' (metacheck: 'likley')"
  r_text: [["likley", "likely"]]
"bibr12/module.stat_effect_size.full":
  kind: r_bug_fixed
  ref: U125
  reason: "the missing-effect-size summary ends with a full stop (metacheck leaves it out)"
  r_text: [["consider adding effect sizes$", "consider adding effect sizes.", regex]]
```

`[old, new]` replaces literal text; `[old, new, regex]` is Python's `re.sub(old,
new, s)` (`\1` is a group in `new`). Such a case is a plain pass, not an expected
failure: it passes when Python's result equals R's rewritten golden, any other
difference fails it, and it is never locked, so the rest of its output stays
compared with R. A substitution that changes nothing in the golden fails the case
(the mark is stale), and so does a case that also matches R's golden as it is (the
substitutions only change text the comparison skips, such as an ignored element).
R's error text is never compared, so an `r_text` mark on a case where R fails is
always stale. A case that also differs for another reason adds `xfail: true`: it
stays an expected failure, locked against the golden as rewritten. The prose marks of
generated cases are collected in `parity/divergences/prose.yaml`.

## The divergence lock

A marked case is an expected failure, but not an unchecked one.
`parity/lock/<area>.json` pins, for every marked case, what R returned, what Python
returned and where the two differ, one sorted line per case so that lanes locking
different cases merge cleanly:

```json
{
"json_expand.null": {"r": "3f1c0b7e9a2d4c55", "py": "9a0b61d0c1e2f3a4", "diff": ["table.x[]"]},
"read.bad": {"r": "0d4e5f6a7b8c9d0e", "py": "raises:ValueError", "diff": ["<R value, Python error>"]}
}
```

* `r`: a digest of the golden's `ok` flag and value (not R's error text);
* `py`: a digest of Python's result as the comparison sees it (ignored elements
  dropped, a report as its prose, `presence` fields as their masks, unordered rows
  sorted), or `raises:<ExceptionType>`;
* `diff`: the paths where they differ, element indices written `[]`, or `<R error,
  Python value>` / `<R value, Python error>`.

Digests round doubles to 10 significant digits and write what differs from run to
run as placeholders: the random names `tempfile` gave while the case ran
(`<tempfile>`), the temporary directory (`<tmp>`), the run's data and cache
directories (`<data>`, `<cache>`) and date-time stamps from while it ran (`<now>`).
Locking twice gives the same file.

What `check` reports:

| status | meaning | fails |
|---|---|---|
| `pass` | matches R (after its `r_text`, if any) | no |
| `xfail` | marked, and R's golden, Python's result and the differing paths are as locked | no |
| `xpass` | marked, but matches R: remove the mark (metacheck may have fixed the bug) | yes |
| `r_changed` | marked, and R's golden changed since it was locked: check the mark still holds | tier 1; tier 2 warns |
| `py_changed` | marked, and Python's result or the differing paths changed | tier 1; tier 2 warns, unless a value became an exception |
| `unlocked` | marked, without a lock entry | yes |
| `fail` / `error` | unmarked, and differs from R / Python raised where R returned | yes |
| `skip` | needs the reference R (see below) | no |
| `missing` | no golden: run `generate` | yes |

A tier-2 warning is a `LockWarning` under pytest and is listed in the summary.
`check` also fails on a lock entry that names no marked case (a stale entry).

After changing a marked case, or code a marked case runs, re-lock its area and review
what moved:

```bash
python -m parity lock --area mod_repo_check --jobs 4   # prints new, changed and removed keys
python -m parity lock -k json_expand                   # just some cases
python -m parity lock --area db_review --suggest       # also propose marks
```

`lock` rewrites the entries of the marked cases it runs, drops entries of cases that
pass or are no longer marked, and keeps those of cases it could not run. With
`--suggest` it also runs the unmarked cases and prints ready-to-edit `r_bug_fixed`
marks for those where R crashed (an R or dplyr error such as "subscript out of
bounds") and Python returns a value. Check each value, record the bug as a U-entry
and add the mark to your lane's divergences file.

## How cases run

* **Offline.** A case that opens a connection, sends a datagram or looks up a host
  name is an `error` (in the Python process: a program the case starts, such as
  `git` or `Rscript`, is not watched).
  Cases with `mock_dir: apis` (or another metacheck mock directory, or a path under
  `tests/` or `parity/`) run against recorded API responses on both sides: R inside
  `httptest2::with_mock_dir()`, Python inside `tests.httpmock.replay()`. Other
  network code is replaced by a fake in the case's helpers.
* **Isolated.** Each case gets a fresh `PYTACHECK_CACHE_DIR`, so no case sees what
  another cached, and runs with no user or project configuration
  (`PYTACHECK_CONFIG=none`). What it prints is kept out of the terminal (`-v` shows
  it; the report keeps it for failing cases).
* **Out of the checkout.** A case must not write into the repository: give functions
  that save files (`paper_write()`, `grobid_to_bibr()`, `convert()`, ...) a temporary
  `save_path`, because metacheck's default `"."` is the repository root when the
  harness runs. `check` fails when a run leaves a new file there, and so does
  `pytest`.
* **Deterministic.** Both sides run in UTC; papers created without an id
  (`test_paper()`) are numbered within a case (the n-th is
  `md5("pytacheck-parity-<n>")[:14]`) instead of hashed from the time.
* **With metacheck's defaults.** pytacheck produces bibr export schema 12.x where
  metacheck's defaults keep the older format (UPSTREAM_ISSUES D1). The Python side of
  every case runs with metacheck's defaults (`METACHECK_DEFAULTS` in
  `parity/cases.py`): `read()` and `grobid_to_bibr()` convert Grobid TEI with
  `schema_version=None` and `paper_write()` saves the paper object. A case that tests
  the 12.0 conversion passes `schema_version = "12.0"` on both sides.
* **Cases whose Python side runs R** (`reproducibility_check`, `code_parse_r(engine =
  "r")`, ...) need the reference R: without `PYTACHECK_RSCRIPT` naming an R >= 4.5
  they are reported as `skip`, never as pass or fail. The harness notices any case
  whose Python side starts `Rscript`/`R`; `needs_r: true` marks one explicitly.

## Goldens

Goldens are generated under `LANG=C.UTF-8` and `TZ=UTC`, one R session per case
file, so they do not depend on the machine, and `--area` and a full run write the
same files. In every string the repository directory is written `<repo>` (the Python
results get `<repo>` the same way), R's session temporary directory `<tempdir>`, the
random names of R's temporary files `<tempfile>`, and date-time stamps from while the
case ran `<now>`.

## Writing cases

Every ported function and module gets parity cases, generated from R, never
written by hand ([PORTING.md](PORTING.md), rule 2): tier-1 cases on the realistic
corpus, and tier-2 edge cases for every branch that changes the traffic light. Do
not write a case whose only purpose is to make Python copy an R internal: R's error
texts, a C library's behaviour on malformed input, a type detail.

The case syntax (argument constructors such as `$paper`, `$read`, `$df`, `$expr`,
`$call`, `$catch`; `py_args`, `py_drop`, `mock_dir`, `needs_r`) is documented in the
docstring of `parity/cases.py`. Most case files are written by a generator in the
area's `tests/<package>/` directory; edit the generator and rerun it, not the YAML.
**Quote every string in case YAML** (`"."`, `"1.0"` and `"yes"` would otherwise be
read as numbers or booleans by R's YAML reader); infinities are `!!float ".inf"` and
`!!float "-.inf"`.

## Accuracy (phase B, to come)

Parity cases check functions branch by branch. The accuracy report will check the
end result on the realistic corpus: `python -m parity accuracy [--generate] [--gate]
[-m MODULE] [--md OUT]` will run 21 papers through the 19 offline paper modules and
10 repositories through `code_check`, `data_check`, `repo_check` and
`codebook_check` on both sides, and score each output (runs, traffic lights exactly,
summary tables, tables, summary text and report text and numbers). R's outputs will
be committed under `parity/accuracy/golden/`, so the report needs no R, and every
difference must match an entry of `parity/accuracy/expected.yaml` that cites a U- or
D-entry. The gate requires no unexplained difference and no stale entry. Until it
lands, the parity cases are the check.

## CI

Today:

* **every push** (`ci.yml`): `pytest -m "not network and not r"`, which includes
  every parity case against the committed goldens (no R needed; the cases whose
  Python side runs R are skipped), then `python -m parity check`;
* **parity workflow** (`parity.yml`, with R): regenerates every golden at the pinned
  commit and fails when one differs from the committed goldens, or a case has none,
  so the goldens cannot drift from R; then runs `python -m parity check`;
* **upstream-sync workflow**: when metacheck `dev` moves (or, while the pin names a
  pull request, that pull request's head), regenerates the goldens at the new commit
  and asks an AI agent to port the changes until parity is green again.

Planned with the accuracy report (phase B): parity run once per push with the tier
marks and a single report (`check --jobs 4 --report ... --md $GITHUB_STEP_SUMMARY`),
the accuracy gate on every push and in the upstream sync, a leg with pyarrow
installed that runs the tier-1 cases, and a sync that lists `r_changed` and `xpass`
cases and asks for human review when tier-1 marks, `expected.yaml` or D-entries
change.
