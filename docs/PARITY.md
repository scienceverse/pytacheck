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
  quarantine.yaml        cases that fail for a reason outside pytacheck (none today)
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
python -m parity generate --jobs 0             # every area, one R session per CPU
python -m parity check --area text -v          # Python vs goldens
python -m parity check --tier 1 --jobs 4       # the realistic corpus, 4 processes
python -m parity check --jobs 0 --md summary.md  # everything, one process per CPU
python -m parity check --strict --area core,text  # a changed tier-2 mark fails too
python -m parity lock --area text              # after adding a mark
python -m parity lock --area text --reviewed   # after changing a marked case
python -m parity list --tier 2                 # cases, their tiers, missing goldens
pytest -m "parity and tier1"                   # the same checks, as tests
```

Goldens are R's output and are only ever written by `python -m parity generate`
with the reference R; never edit one by hand. They are committed, so `check` and
`pytest` need no R.

`check` selects cases with `--area`, `--only <id or area/id> ...`, `-k <substring>`
and `--tier 1|2` (`--area` takes one area, several separated by commas, or
`<area>+review` for an area and its `<area>_review` cases; it may be repeated, and
an area without a case file is an error), runs them in `--jobs N` processes (`0`:
one per CPU), writes a per-case JSON report (`--report PATH`; by default a new
`parity/_out/report-<time>-<pid>.json`, so runs side by side do not overwrite each
other) and, with `--md [PATH]`, a Markdown summary: statuses by tier and the marked
cases grouped by tier, kind and ref, and the stale lock entries and quarantine entries
that name no case. It prints only the cases that fail or warn
(`-v` prints every case, with what it printed) and exits non-zero on any failing
status, a stale lock entry, a quarantine entry that names no case, or a file left
in the repository root.

### A fresh worktree

A fresh clone or `git worktree` runs `check` to zero failures once it has what the
cases read:

```bash
git submodule update --init      # upstream/metacheck: fixtures and recorded responses
uv sync --locked --all-extras    # the data extra (pyreadstat, xlrd, snowballstemmer)
```

Without the submodule some 1,800 cases fail, and without the extra some 60, all for
that one reason. So `check` and `lock` look first and stop with exit status 2 and
the command that fixes it, before running anything. The cases that need the reference
R are `skip`ped without it, in a worktree as anywhere.

Goldens write the checkout as `<repo>`: R replaces its real path (as
`normalizePath()` gives it). Python results do the same, and also replace the
symlinked spelling the checkout was reached by, but only as a whole path, not inside
a longer name or a URL. When `upstream/metacheck` is a symlink to another checkout's
submodule, Python writes its real path `<repo>/upstream/metacheck`, as R did on the
checkout the goldens came from. R does not map these symlinks, so run `generate` on a
plain checkout with its own submodule. A case reads its files through
`case_path()` (`$paper`, `$read`, `$file`), which refuses a path that leaves the
checkout through any other symlink, so a case cannot depend on a file that only
exists in one worktree. A `$expr` that builds its own path is not checked.

### Quarantine

`parity/quarantine.yaml` lists cases that fail in some environment for a reason
that is not pytacheck's, each with its reason. `check` reports such a case as
`quarantined` instead of any status that would fail it (`fail`, `error`,
`r_changed`, `py_changed`, `xpass`, `unlocked`, `missing`). A quarantined case never
fails the run, and `check --strict` does not fail it either; the tests pin both. One
that passes is a `pass`. `lock` ignores the quarantine: it still records, changes and
removes the lock entry of a quarantined case. The file is a last resort: fix the environment first.
`max_cases` must equal the number of cases listed, and `QUARANTINE_CEILING` in
`tests/test_parity_cli.py` must equal it too, so adding a case means changing both,
in a change a reviewer sees. It should only ever go down. Nothing is quarantined
today.

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
"rcompat/*.posix_punct.tre*":          # a glob key: tier-2 cases only
  kind: c_quirk
  ref: D29
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
| `r_changed` | marked, and R's golden changed since it was locked: check the mark still holds | tier 1; tier 2 warns, unless `--strict` |
| `py_changed` | marked, and Python's result or the differing paths changed | tier 1; tier 2 warns, unless a value became an exception or `--strict` |
| `unlocked` | marked, without a lock entry | yes |
| `fail` / `error` | unmarked, and differs from R / Python raised where R returned | yes |
| `skip` | needs the reference R (see below) | no |
| `quarantined` | would fail, but `parity/quarantine.yaml` lists it with a reason | no, `--strict` included |
| `missing` | no golden: run `generate` | yes |

A tier-2 warning is a `LockWarning` under pytest and is listed in the summary;
`check --strict` fails on it instead. `check` also fails on a lock entry that names
no marked case (a stale entry).

After changing a marked case, or code a marked case runs, re-lock its area and review
what moved:

```bash
python -m parity lock --area mod_repo_check --jobs 4   # prints what it adds, changes, removes
python -m parity lock --area mod_repo_check --reviewed # writes the changes it printed
python -m parity lock -k json_expand --md lock.md      # just some cases; a table for the PR
python -m parity lock --area db_review --suggest       # also propose marks
```

`lock` prints each entry it adds, changes or removes: the case, its tier and mark,
the old and new `r`, `py` and `diff`, and the first differences from R. It adds the
entries of newly marked cases, drops entries of cases that pass or are no longer
marked, and keeps those of cases it could not run. It changes an existing entry only
with `--reviewed`: without it, the change is printed, not written, and `lock` exits
non-zero. `--md [PATH]` also writes the changes as a Markdown table to put in the
pull request. With `--suggest` it also runs the unmarked cases and prints
ready-to-edit `r_bug_fixed` marks for those where R crashed (an R or dplyr error
such as "subscript out of bounds") and Python returns a value. Check each value,
record the bug as a U-entry and add the mark to your lane's divergences file.

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
  it; the report keeps it for failing cases). The harness folds any `METACHECK_*`
  twin of these variables into its `PYTACHECK_*` name at start-up, so a setting in
  your shell cannot shadow the harness's folders. The R image scripts read only
  `PYTACHECK_R_IMAGE`, and `benchmarks/compare_r.py` reads only `PYTACHECK_RSCRIPT`.
  Environment variables and folders: see [ENVIRONMENT.md](ENVIRONMENT.md).
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
* **`execute = TRUE` cases name the sandbox.** `reproducibility_check` runs the code in
  Docker unless told otherwise (UPSTREAM_ISSUES D62), metacheck on this machine. The
  R call of such a case is left as it is and the Python call adds `sandbox="process"`,
  which is what metacheck does; the case `exec_docker` names `sandbox="docker"` on both
  sides.

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

## Accuracy

Parity cases check functions branch by branch. The accuracy report checks the end
result users see, on the realistic corpus:

```bash
python -m parity accuracy                    # score Python against R's committed outputs
python -m parity accuracy --gate             # ... and fail on anything unexplained (CI)
python -m parity accuracy -m all_urls -m marginal --md accuracy.md
python -m parity accuracy --generate         # first rewrite R's outputs (reference R)
```

```
parity/
  accuracy.py                the report: runners, scorer, expectations, gate
  accuracy/matrix.toml       which modules run on which corpus inputs
  accuracy/expected.yaml     every difference it may find, and why
  accuracy/golden/*.json.gz  R's outputs, one file per module (committed)
  r/install-suggests.R       careless for the reference R (see below)
```

**What runs.** `parity/accuracy/matrix.toml` lists the inputs, each of which must be
in the corpus (`parity/corpus.toml`): 21 real papers (the demo paper as TEI and JSON,
Grobid TEI of published papers and preprints, the problem papers, the psychsci JSONs
and the bibr 12.0 exports) through the 19 offline paper modules, and 10 real
repositories (the `tests/mod_data_check` repositories and metacheck's `demo`,
`code_files`, `notebooks` and `parse-errors` fixtures) through `code_check`,
`data_check`, `repo_check` and `codebook_check`, on the demo paper with `local_path`
and `local_only = TRUE`. Every module runs with its default arguments: 439 outputs.
Python reads each paper once and gives every module its own copy, runs offline (an
output that uses the network or starts R is a problem that fails the gate) and uses
pytacheck's port of R's parser (`PYTACHECK_R_PARSER=python`), so the result does not
depend on whether R is installed. It takes about 35 s.

**R's outputs** are committed under `parity/accuracy/golden/<module>.json.gz`
(gzip without a time stamp, so regenerating unchanged outputs rewrites the same
bytes): about 0.3 MB, so the report needs no R. `--generate` writes a transient case
file and runs it with `parity/r/run_cases.R --out`, which reads each paper once per
session; R's network is disabled (its proxy points at a closed port), because an R
module that reached the network would give other results where it is reachable. It
takes about 105 s. R runs as a user with metacheck's suggested packages installed:
`careless`, which `data_check` needs to screen survey data for careless responding,
lives in its own library, `<R.home()>/suggests`, which only this report puts on R's
library path (`parity/r/install-suggests.R` installs it there, called by
`setup-reference.sh` and the workflows; `install-with-pak.R` does the same with pak).
Its one dependency, `psych`, is pinned in both lock files. The parity cases keep
switching `careless` on and off themselves (`tests/mod_data_check/dc_helpers.R`), so
their goldens do not depend on it.

**Scoring.** Each output is compared field by field:

| field | agreement |
|---|---|
| `run` | both succeed, or both fail |
| `traffic_light` | exact |
| `summary_table` | column names and order and the row count exact; numbers to a relative 1e-9; text after whitespace normalisation (cells) |
| `table` | row count and column names exact; the rows as a multiset, text whitespace-normalised and numbers to 10 digits (row F1) |
| `summary_text`, `report` | after whitespace normalisation (runs of whitespace are one space; the report as its prose, as parity cases compare it), and the multiset of numbers in it, with their minus signs |

Every difference has a level: `whitespace` (equal once all whitespace is removed:
`p =0.152` vs `p = 0.152`), `wording` (other text, the same numbers) or `values`
(numbers, including a minus sign; rows, columns, traffic lights, a failed run; a
table cell that is NA, logical or numeric on either side, so a value lost or added
is never `wording`; and the same cells paired up into other rows). The report
prints, per module, the share of outputs that agree on each field, and lists the
differences.

**Expected differences.** Every difference must be explained by an entry of
`parity/accuracy/expected.yaml`:

```yaml
differences:
  - module: [all_p_values, stat_p_exact]      # globs, or lists of globs
    input: problems/0956797617737129.xml       # the path or its short name
    field: [summary_text, summary_table.p_values, table]
    match: values                              # the highest level it explains
    kind: r_bug_fixed                          # as a mark on a tier-1 case
    ref: U28
    reason: the link text keeps its p-value; metacheck replaces link text in every row
floors:
  - module: ref_accuracy
    traffic_light: 0.4
    kind: r_bug_fixed
    ref: U30
    reason: 12 of the 21 papers have no bib_match rows (pytacheck 'fail', metacheck 'error')
```

`field` is `run`, `traffic_light`, `summary_text`, `report`, `summary_table` or
`table` (their columns or row counts), or `summary_table.<column>` / `table.<column>`.
An entry is validated like a mark on a tier-1 case: `r_bug_fixed` cites a fixed or
partly fixed U-entry, `better_logic` and `deliberate` a D-entry, and `c_quirk` and
`type_detail` are not allowed. Today 20 entries explain the 188 differences, all of
them metacheck bugs pytacheck fixes (Grobid clean-up and URL handling U16/U28, U3,
U30, U80, U82, U83, U86, U98, U99, U115, U123, U125, U158).

**The gate** (`--gate`) fails on a difference no entry explains, an entry that
explains none (stale; checked on full runs, not with `-m`), a missing golden, a golden
of a module or input the matrix no longer has, or an output that used the network or
started R. A module whose traffic lights agree with
metacheck's on fewer than 90% of its inputs is a warning, unless a `floors` entry
names the share expected and why; the upstream sync turns such a warning into the
`needs-human-review` label. The JSON report (`--report PATH`, by default
`parity/_out/accuracy-<time>-<pid>.json`) has the scores, every difference and the
entry that explains it, and `passed` and `needs_review`; `--md` writes a Markdown
summary.

After a change that moves a realistic result, run the report: a new difference is
either a regression to fix or a documented improvement that gets an entry (and its
U- or D-entry). Changing the matrix means `--generate` with the reference R.

## CI

* **every push** (`ci.yml`, no R needed):
  * the test matrix (Linux on Python 3.11 to 3.14, macOS, Windows) runs
    `pytest -n auto -m "not network and not r and (not parity or tier1)"`: the unit
    tests and the tier-1 parity cases on every platform;
  * the `parity` job runs every parity case once, `python -m parity check --jobs 0`,
    with the per-case report as an artifact and the Markdown summary (statuses by
    tier, marks by tier, kind and ref) on the run page, then the accuracy gate,
    `python -m parity accuracy --gate`, from the committed R outputs;
  * the `parity-pyarrow` job runs the tier-1 cases with pyarrow installed
    (`uv run --with pyarrow pytest -n auto -m "parity and tier1"`): pandas 3 then stores
    strings in Arrow, which rejects text that is not valid Unicode;
* **parity workflow** (`parity.yml`, with the reference R, when `parity/**` or
  `upstream/**` change and weekly): regenerates every golden and the accuracy
  report's R outputs at the pinned commit and fails when one differs from the
  committed ones, or a case or module has none, so neither can drift from R; then runs
  `python -m parity check --jobs 0` and the accuracy gate;
* **upstream-sync workflow**: when metacheck `dev` moves (or, while the pin names a
  pull request, that pull request's head), regenerates the goldens and the accuracy
  outputs at the new commit and writes a brief (`scripts/upstream_sync.py`) with the
  goldens that changed, the marked cases whose golden changed (`r_changed`) or that
  now match R (`xpass`), the failing cases, and the accuracy report before porting.
  An AI agent then ports the changes until `parity check`, the accuracy gate, the
  tests and the linters pass. The pull request is a draft labelled
  `needs-human-review` when tier-1 marks changed or a case left tier 1 (a case in
  tier 2 may carry any mark), `parity/accuracy/expected.yaml`, `matrix.toml` or
  `parity/corpus.toml` (dropping an input would hide its differences), or D-entries
  changed since the commit the sync branched from (committed by the agent or not), or
  the accuracy report warns or fails (`scripts/upstream_sync.py review`);
  `.github/CODEOWNERS` names the code owners of the marks, the lock, `expected.yaml`,
  `matrix.toml`, `corpus.toml` and `docs/UPSTREAM_ISSUES.md`, whose review a change to
  them needs once branch protection requires code-owner review.
