# Pytacheck fidelity: results locked, presentation free

**Status.** Draft 1 (2026-09-26). Proposed; nothing here is implemented. It changes F1-F3 and F5 of ARCHITECTURE.md §1.1, and it adds harness package HARNESS-v2 (§11) to the plan in ARCHITECTURE.md §4.

**Evidence markers.**
- **(M)**: measured in this session, from `parity.cases.load_cases()`, the case YAML and the source tree at f559c1be. The scripts are in `/tmp/claude-0/-home-user-pytacheck/b01f2e9f-255f-5b7e-9d0d-70c9d055b185/scratchpad/research/fidelity/` (`count_cases.py`, `count_priv.py`, `pres.py`, `val.py`).
- **(E)**: an estimate, always with its basis.
- `upstream/metacheck` means the metacheck checkout the harness pins.

---

## 0. Summary

**Today, pytacheck copies R's output down to presentation.** It keeps row order, column order, dtypes, list-column shapes, 15- and 7-digit number text, whitespace and error wording. Every case compares column order, and no case uses `unordered` **(M)**. 1,298 case marks and 154,824 bytes of UPSTREAM_ISSUES.md exist largely to explain presentation differences **(M)**. Every refactor that changes how a result looks pays that tax, even when the result is the same.

**Proposal.** Split what a module returns into three bands.

| Band | What | Rule |
|---|---|---|
| **A: locked** | Traffic lights, which rows are flagged, statistics, references, values, counts, the numbers inside text, error vs value | Exact on tier 1. A change needs a deviation row; in a validated module it also needs a validator's sign-off |
| **B: free** | Row and column order, extra columns, dtypes, NA spelling, list shapes, number text, whitespace, error text, private helpers | Removed by a canonicaliser before any comparison. Nothing is recorded |
| **C: recorded** | Wording of summary text, report and guidance; tier-2 changes; Band A changes in experimental modules | Allowed with one scoped deviation row |

**What this buys.**
- The 1,298 marks become at most 131 deviation rows (E: one per cited U-entry). UPSTREAM_ISSUES.md shrinks from 155 KB to about 20 KB (E: the Band A entries only).
- Modules can use document order, native types and floats. The number formatting moves into the report renderer.
- About 1,500 private-helper cases stop pinning internals. They are re-pointed to public entry points (§6).
- The upstream sync compares R's *behaviour*, not its code (§7). Upstream refactors and prose edits close automatically.
- It unblocks ≈ 30,000 lines of de-emulation and structural work in ARCHITECTURE.md revision 3 (§3.5 there).

**What it costs.** HARNESS-v2, about 7 agent-days (§11). The main risk is a canonicaliser that hides a real regression. Four guards answer it (§4.3).

**What never moves without sign-off:** the Band A outputs of the validated modules (ECOSYSTEM.md §3.1). Their published error rates depend on them.

---

## 1. How fidelity is enforced today

| Mechanism | Count **(M)** | Where |
|---|---|---|
| Parity cases | 9,474 (tier 1: 1,424; tier 2: 8,050) | `parity/cases/*.yaml` |
| By target | 6,436 public functions, 1,048 module cases, 1,989 private. 1,497 of the private ones name a pytacheck-private function (ARCHITECTURE.md:583 says 1,487); 492 go through test wrappers | case specs |
| Private on the R side | at most 2,990 cases call a dot-prefixed R helper | case specs |
| Order pinned | `unordered` in 0 of 9,474 cases; `col_order: false` in 0 | `parity/compare.py:63`, `docs/PORTING.md:262` |
| Loosened | `ignore` 968, `tol` 10, `ws` 3, presence 3 | case `compare:` blocks |
| Marks | `r_bug_fixed` 1,298 (232 on tier 1), `c_quirk` 28, `type_detail` 13, `deliberate` 8, `better_logic` 3; `r_text` rewrites 160 (35 on tier 1) | `parity/cases.py:179-189` |
| Write-ups behind the marks | 131 distinct U-entries cited (U16 alone by 163 marks); 158 U + 30 D entries, 154,824 bytes; median U row 751 characters, longest 3,236 | `docs/UPSTREAM_ISSUES.md` |
| Lock | tier-1 drift fails | `parity/lockfile.py:1-44` |
| Accuracy gate | 439 outputs; 188 differences explained by 21 entries, all `r_bug_fixed`; 61 of them at whitespace level; 12 in traffic lights (U30) | `parity/accuracy/matrix.toml`, `parity/accuracy/expected.yaml` |
| Map | `porting/symbols.json` 1,127 entries (696 dot-prefixed R names, 772 private Python targets); `porting/map/` 42 files, 1,924 lines | `porting/` |
| Upstream sync | daily; the agent is asked for "what the new R code does"; "Do not weaken or delete existing parity cases" | `.github/workflows/upstream-sync.yml:28,153-166`; `.github/prompts/upstream-sync.md:15,75` |

**Presentation against results.**
- Every frame case pins presentation implicitly, through row and column order.
- 493 cases (5.2%) target formatting functions (E: a function-name heuristic).
- 160 marks exist only to rewrite R's text.
- The accuracy gate is already mostly results-level: `table` is scored as a row multiset (`parity/accuracy.py:695`). It stays strict about presentation in three places:
  - column order counts as a value difference (`_structure`, `:645`);
  - `summary_table` is compared row by row (`:667`);
  - wording is a difference level (`text_level`, `:409`).

---

## 2. The contract

### 2.1 Bands

**Band A: behaviour-locked.** Exact on tier 1; a failure blocks the PR.
- Traffic lights.
- The detection multiset: which rows are flagged, keyed on paper and located text.
- Extracted statistics and references.
- Values, to relative 1e-9.
- Counts.
- The multiset of numbers in summary and report text.
- Whether a call returns an error or a value.

**Band B: free.** The canonicaliser removes these differences, and nothing is recorded.
- Row order and column order.
- Extra columns (reported as info, never as a failure).
- dtypes and the spelling of NA.
- List-column shapes.
- Number text, such as R's 15- and 7-significant-digit output.
- Whitespace.
- Error and warning text.
- Private helpers. They are no longer compared with R at all (§6).

**Band C: may deviate if recorded.**
- The wording of `summary_text`, report prose and guidance.
- Any change on tier 2 under `--strict`. This keeps H0-1.
- A Band A change in an experimental module.

### 2.2 Rules across bands

1. **Every Band A or Band C change needs a deviation row** (§3). A Band A change in a validated module also needs sign-off from a validator in CODEOWNERS, or a re-validation (ECOSYSTEM.md §2.5). Band B needs nothing.
2. **Column names are an API.** Adding a column is free. Renaming or removing one is Band C and needs a deprecation cycle of one minor release, because users' code reads columns by name.
3. **Band B freedom is not permission to be random.** Row order must be deterministic and documented. The default is document order: the order in which the located text appears in the paper. No module may emit set or hash order. A test runs the accuracy inputs under two `PYTHONHASHSEED` values and requires identical raw outputs (H0-19).
4. **An API that defines its order keeps it.** The `text_search` façade keeps pattern-major order, because that order is part of its documented contract with store modules (ARCHITECTURE.md §2.10).
5. **Wording rows are mandatory for validated modules**, even for pure rephrasing. A wording change can flip meaning ("significant" to "not significant") while every number still matches, so a reviewer has to see it.

### 2.3 Per element

| Output element | Band A | Band B | Band C |
|---|---|---|---|
| `table` | the multiset of rows by `row_key`; values; counts | row order, column order, extra columns, dtypes, NA spelling, list shapes, number text | column renames or removals |
| `summary_table` | one row per paper; values and counts | column order, extra columns, dtypes | column renames or removals |
| `traffic_light` | exact | – | only in experimental modules, with a row |
| `summary_text` | the multiset of numbers it contains | whitespace, number text | wording |
| report section | its table and light as above; the numbers in its prose | whitespace, table formatting, number text | wording, guidance, headings |
| extras (`structure`, `codebook_vars`, …) | values and counts | order, shape, dtypes | renames or removals |
| errors | error vs value | error class and text | – |
| warnings | – | text | whether one is raised (a row, so users are not surprised) |

---

## 3. Deviation records

### 3.1 The row

Each module gets one file, `parity/deviations/<module>.toml`, with one row per deliberate difference:

```toml
[[deviation]]
scope    = "mod_marginal/*:summary_text"   # case or field glob
what     = "wording"                       # wording | value | light
why      = "R pluralises 'result' twice (U3)"   # at most 120 characters
upstream = "https://github.com/scienceverse/metacheck/issues/NNN"   # optional
since    = "0.4.0"
signoff  = "@validator"                    # required for Band A in a validated module
```

- `scope` replaces the per-case mark. One row covers every case it matches.
- `what = "value"` or `"light"` is Band A. A lint rejects such a row in a validated module without `signoff`.
- A row that matches no failing case is stale, and the harness reports it (as it does for stale marks today).
- The files load in parallel-safe fashion: one file per module, so two packages never edit the same file. This replaces H0-15's slugs.

### 3.2 Migrating the marks

| Today **(M)** | Becomes |
|---|---|
| `r_bug_fixed` 1,298 marks citing 131 U-entries | at most 131 rows (E), fewer where the difference is Band B. U16 (163 whitespace marks) disappears |
| `r_text` 160 rewrites | mostly gone (Band B whitespace and number text); the rest are wording rows |
| `type_detail` 13, `c_quirk` 28 | mostly Band B; nothing recorded |
| `deliberate` 8, `better_logic` 3 | rows |
| `docs/UPSTREAM_ISSUES.md` (158 U + 30 D, 155 KB) | a short list of metacheck bugs that touch Band A, about 20 KB (E), each linked from its rows. The archive stays in git history |

The migration is a script (`scripts/migrate_marks.py`). It groups marks by U-entry and module, runs the canonical comparison, drops the marks whose difference vanishes after canonicalisation, and writes one row for each remaining group. A reviewer checks the rows it writes for the validated modules first: those 7 modules carry 253 cases, 90 of them on tier 1 and 137 marked **(M)**.

---

## 4. The canonicaliser

### 4.1 Steps

`parity/canon.py` runs on both sides before every comparison, lock digest and accuracy score:

1. **Columns.** Match columns by name. Drop extra columns and report them as info. Sort rows by the module's `row_key`, whose default is the non-numeric columns. Keep duplicate rows: the comparison is a multiset, not a set.
2. **Numbers.** Parse number-like strings to floats with `_NUM` (`parity/accuracy.py:396`), then compare at relative 1e-9.
3. **Whitespace.** Collapse runs and trim.
4. **Missing values and lists.** Treat NA, None, NaN and "" as one value. Write lists as plain JSON.

### 4.2 Configuration

`parity/canon.toml` declares per-module keys where the default is wrong:

```toml
[module.funding_check_oi]
row_key = ["paper_id", "text"]

[module.stat_check]
row_key = ["paper_id", "raw"]
```

When a key collides (two rows share it), the rows with that key are compared as full-row multisets, so a collision can never hide a changed value.

### 4.3 Guards against hidden regressions

The canonicaliser is the one place where a real regression could disappear. Four guards:

1. **Multiset comparison** everywhere: a dropped or duplicated row always fails.
2. **Declared keys.** `row_key` is declared data, reviewed like code, and never inferred at run time.
3. **Known-difference tests** in `tests/parity/test_canon.py`. It must *flag* a dropped row, a changed light, a changed number inside summary text, and a value changed by 1e-8 relative. It must *hide* funding_check_oi's document-order rows and U16's whitespace.
4. **Raw snapshots stay raw.** SNAP (ARCHITECTURE.md §4.2) keeps recording raw outputs and compares them through `canon`. A Band B change therefore shows up as a raw-snapshot diff, which is visible in review even though it passes.

---

## 5. Gates

| Gate | Today | Under this contract |
|---|---|---|
| **G1** parity | tier 1: 0 fail, no lock drift; drift explained by a U/D entry | canonical pass rate. Tier 1 gates on Band A; tier 2 warns, and fails under `--strict` without a row (H0-1 kept). The lock stores **canonical digests**, so Band B refactors cause no drift |
| **G2** accuracy | 0 unexplained differences over 439 outputs | **results only.** `traffic_light` exact; `table` and `summary_table` as multisets with F1 = 1.0 or explained; column order leaves `_structure`; wording stops being a level; the 61 whitespace rows leave `expected.yaml`. The 90% floor stays |
| **G5** equivalence | canonical equality with SNAP | canonical equality through `canon` (SNAP stays raw) |
| H0-1 | tier-2 `py_changed` fails under `--strict` | kept; the fix is a row |
| H0-2 | report tables pinned by column names and a cell hash | pinned by **column names and the multiset of numbers**, not a cell hash. Formatting is Band B |
| H0-13 | case count and distinct inputs per area may not drop | counts **distinct realistic inputs** reached through the public API, not cases |
| H0-15 | U/D slugs, numbered at CLOSE | replaced by `parity/deviations/<module>.toml`; CLOSE no longer numbers U entries |
| **H0-17** (new) | – | `parity/canon.py`, `parity/canon.toml`, `tests/parity/test_canon.py` |
| **H0-18** (new) | – | the mark migration script and its reviewed output |
| **H0-19** (new) | – | the determinism test (two `PYTHONHASHSEED` values, identical raw outputs) |

The accuracy gate covers Band A only. Wording is governed by G1 and the deviation rows.

**Validated modules are also protected by the registry.** Their evidence records the corpus and the counts behind each published rate (ECOSYSTEM.md §2.4). Where the team can share that corpus, its labels become tier-1 inputs, and a re-run of the metrics becomes part of the gate.

---

## 6. Retiring the private-helper cases

1,497 cases name a pytacheck-private function **(M)**. They pin internals that the rewrite deletes. Each one is either re-pointed to the public entry point that reaches the same realistic input, or deleted, and `parity/retired.yaml` records which.

| Largest groups **(M)** | Cases | Re-point to |
|---|---:|---|
| `_grobid_to_bibr` | 97 | `read()` on the same TEI |
| `datacheck_columns` | 223 | `data_read_head` / `data_check` |
| `datacheck_checks` | 194 | `data_check` |

- H0-13 counts inputs, so a deletion that loses an input fails the ratchet until a public case reaches it again.
- The parser coverage these cases carry (Grobid, the data readers) is exactly what Band A depends on, so these three groups move before any deletion (§10, risk 4).
- The 492 cases that go through test wrappers are treated the same way.

---

## 7. Behaviour-level upstream sync

The goldens step (`upstream-sync.yml:153-166`) stays. What follows it changes:

1. Canonicalise R's old and new outputs.
2. Compute an **R behaviour delta** for each module: detections added or removed, lights or values changed, and pattern constants changed (parsed from `inst/modules/*.R`).
3. **An empty delta closes the sync automatically**, without running the agent. That covers upstream refactors, edits to private helpers and prose tweaks (E: the share is unmeasured).
4. Otherwise the brief (`scripts/upstream_sync.py brief-parity`) is the delta, with the R diff attached as context. The instruction at `upstream-sync.md:15` becomes "make the canonical tier-1 outputs match the new canonical R outputs". The instruction at `:75` ("Do not weaken or delete existing parity cases") stays.
5. The `needs-human-review` label (`upstream-sync.yml:250`) fires on a Band A delta in a validated module, or on any new deviation row.

**The porting map shrinks with it.**
- `porting/symbols.json` keeps exported functions, module entry points and pattern constants: at most 431 entries (1,127 − 696 dot-prefixed). The 772 private-target mappings go.
- `porting/map/*.toml` (42 files, 1,924 lines) becomes one `porting/modules.toml` of about 150 lines (E: 30 modules × 5 lines). Each entry maps an R module to its Python module, its pattern-constant names and its validation snapshot.
- File mirroring goes, and so do the "Port of R/…" docstrings (`docs/PORTING.md:107`).
- **Pattern lists stay verbatim.** They are behaviour, and they are the cheapest signal from upstream. A test compares each constant with R's vector.

---

## 8. What becomes allowed

1. **funding_check_oi in document order**, instead of pattern-major order. It changes 11 of 21 papers (ARCHITECTURE.md:440) and is Band B, so nothing is recorded.
2. **Floats in tables.** `_r.format_num` (26 call sites in 10 files) moves into the report renderer, and `Kind`'s `r15`/`r7` go (ARCHITECTURE.md §2.9).
3. **marginal's U3.** Three of its four cases carry `r_text` pairs today (`parity/cases/mod_marginal.yaml`). One wording row replaces all of them.
4. **No `json_expand`.** The planned expander of about 150 lines exists only to reproduce jsonlite's null → NA (ARCHITECTURE.md:452, :612). Under Band B, nulls stay None.
5. **Native error handling.** Error text is Band B, so no module replays R or dplyr error messages (Y1 already forbids new ones).
6. **A boundary case.** ref_accuracy turning an R error into a `fail` (U30, 12 traffic-light differences) is a Band A change in a module the team is validating. It needs a validator's sign-off before it ships.

---

## 9. KEEP / CHANGE / DROP for ARCHITECTURE.md

| Item | Verdict | Reason |
|---|---|---|
| F1 accuracy exact | CHANGE | Exact on Band A. Summary text and R-formatted numbers compare by value; wording is Band C |
| F2 recognisably metacheck | CHANGE | Module names still map to R modules and pattern lists stay verbatim. Internal mirroring goes |
| F3 R's shape | CHANGE | Order, dtypes, list shapes and number text move to Band B. A fix needs one scoped row, not a mark per case |
| F4 nothing invented | KEEP | Scientific correctness |
| F5 independent snapshots | KEEP, amended | Snapshots stay raw and compare through `canon` |
| F6 one rule for repeated ids | KEEP | Already a principled deviation |
| P1-P4 | KEEP | Independent of fidelity. P4 (measure, don't proxy) argues for results-level gates |
| Y1 | KEEP | Now applies at the output boundary too |
| Y2 | CHANGE | `_r/` (1,213 lines) keeps only the regex dialects, `.rds` and display collation |
| Y3-Y5 | KEEP | Unaffected |
| §2.13 rejections for performance, soundness or API reasons | KEEP rejected | Not fidelity questions |
| §2.13 YAML modules | CHANGE | The F2 objection is gone. Declarative checks come back as TOML in phase D (ARCHITECTURE.md decision 2) |
| §2.13 `module_api` / store filtering | CHANGE | Filtering by validation status is now needed (ECOSYSTEM.md §3) |
| §2.13 schema 2 | CHANGE | Only an additive `validation`/`status` field in schema 1 |
| §2.13 document order | DROP the rejection | Band B allows it |
| §2.13 corpus mode | KEEP deferred | Neighbour invariance is a correctness risk |
| §3.4: R parser, `.rds`, user-visible regex semantics, business logic, statcheck's three-valued logic, coi agrep, ODS walker, SPV decoders, stat-output readers, archive and database clients, `html_text2`, Grobid TEI, UAX#29, bibr 12, tier-2 corpora | KEEP | They decide inputs and detections (Band A) |
| §3.4 R number formatting | CHANGE | Only in the report renderer |
| §3.4 ICU-like collation | CHANGE | Only for sorted lists users see; audit the 55 `r_sort_key` call sites |
| §3.4 haven/vctrs/readxl shaping | CHANGE | Values only; dtypes are Band B |
| §3.4 api/jsonlite | DROP | Plain JSON |
| §3.4 `%.17g` in LLM bodies | CHANGE | Kept for power, which is validated and prompt-locked; free elsewhere |
| §3.4 repro tables, `count()`, `bind_rows` | CHANGE | Values kept; order and shape free |
| §3.4 report renderer | KEEP | Its structure is kept; wording moves to Band C |
| §3.4 harness comparator | CHANGE | The canonicaliser goes in front |
| §3.4 U-entries marked Kept | CHANGE | Keep only those that touch Band A |
| §3.4 rejected dependencies | KEEP | Unrelated to fidelity |
| Decision 1 (document order) | CHANGE to (b), unrecorded | Band B |
| Decision 5 (plan) | CHANGE | Re-planned as phases A-D (ARCHITECTURE.md §4.4) |

---

## 10. Risks

1. **power is validated but depends on an LLM.** It is missing from the offline accuracy matrix (`matrix.toml:11-30`) and has 12 cases, so no gate protects its 90.6% rate. Lock its prompt, schema and model id, and label it "validated, not regression-protected" until H0-3's recorded row lands.
2. **The corpus is smaller than the validation samples.** 21 papers and 10 repositories, against validation samples of 51-685 items. An unchanged snapshot therefore does not prove the published rates still hold. If the team can share the samples, add them to tier 1 (§5).
3. **The canonicaliser could hide a regression.** The guards are in §4.3.
4. **Retiring private cases loses parser coverage.** `_grobid_to_bibr` (97), `datacheck_columns` (223) and `datacheck_checks` (194) are re-pointed before anything is deleted, and H0-13 counts inputs.
5. **Wording can flip meaning** while the numbers match. Wording rows are mandatory for validated modules (§2.2, rule 5).
6. **Users read columns by name.** Renames are Band C with a deprecation (§2.2, rule 2), and `pc.check` (ARCHITECTURE.md §2.10) reports which columns a store module reads.
7. **Order changes surprise users** even when they are free. The release notes list every module whose default order changed, and the order is documented per module.

---

## 11. Migration: package HARNESS-v2

About 7 agent-days (E), in phase A, after HARNESS and before CORE-1b deletes anything (ARCHITECTURE.md §4.4 has it on days 4.5-11.5). It replaces H0-15 and amends H0-2 and H0-13, which HARNESS then builds directly in their amended form. H0-1 is kept.

| Step | Days (E) | Gate |
|---|---:|---|
| `canon.py`, `canon.toml`, the known-difference tests (H0-17) | 1.5 | the tests in §4.3 pass; canon is idempotent on both sides |
| Canonical lock and re-lock | 1 | **0 Band A drift** at re-lock on the full 9,474 cases |
| Mark migration (H0-18) | 1.5 | every retired mark maps to a row or to a Band B difference; the validated modules' rows reviewed |
| Accuracy gate results-only | 0.5 | 0 unexplained; `expected.yaml` holds only value and light rows |
| Private-case re-pointing | 2 | H0-13's input count does not drop |
| Determinism test (H0-19), `docs/PARITY.md` | 0.5 | two hash seeds, identical raw outputs |

**Order.** Canonicaliser, then the re-lock, then the migration. The private-case re-pointing can run in parallel with the migration. Until HARNESS-v2 lands, the current contract holds, and CORE-0's byte-identical guarantee remains the bar.
