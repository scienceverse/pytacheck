# Where the Python side's 35 seconds go, and how to get it to about 12

All paths below are relative to
`/tmp/claude-0/-home-user-pytacheck/7a7b1f52-e1d1-55eb-8a56-a5f0943689fa/scratchpad/perf/`.
"Verified" means a second agent reproduced the number with interleaved A/B runs (minimum of at
least 5, cProfile used only to attribute time) and checked parity and the 439 accuracy outputs.
"Estimate" means nobody has measured it; it is labelled every time.

---

## 1. The direct answer

**No, the Python code is not well optimized yet.** It is correct and faithful, but in the hot
paths it copies R's shape step for step. Where R's own machinery is heavy (reading Grobid XML,
statcheck, the repository checks), the port is 4-9x faster. Where R is already just a chain of
dplyr calls (text search, reference joins), a pandas copy of that chain runs at dplyr speed.
That second group is most of the run.

The prototypes from this round prove the point. **Fourteen patches stacked together take the
Python side from 34.0 s to 15.5 s, with all 439 outputs unchanged** (5 interleaved runs each,
minimum; see "The stacked result" below). That puts Python at about 6x R instead of about 2.5-3x.
Finishing the remaining design items in lanes 3-7 should reach about 11-13 s (estimate).

### Where the 35 s go today

From the measure lane's best Python run (36.9 s at load about 5; `measure/raw/py-3.json`):

| Part | Seconds | Share | R's equivalent |
|---|---|---|---|
| Module work (the library) | 32.2 | 87% | 73.7 s (about 6 s of it is R's per-call roxygen/source overhead) |
| Reading 21 papers + demo (the library) | 1.7 | 5% | 10.4 s (Python 6x faster) |
| Harness: `copy.deepcopy` of the paper for every output | 1.0 | 3% | none |
| Harness: `canonical()` of every output | 0.6 | 2% | 5.3 s for R's canonical + JSON |
| Harness: temp cache dir and guards per output | 0.2 | 1% | - |
| Imports inside the timer, unattributed | 0.5-0.9 | 2% | 1.3 s startup and library load |

So **about 94% of the time is library code, and about 5% is the harness.** The harness is not
where the gap is.

Inside the 32 s of module work (unprofiled attribution; the rows overlap because one cause shows
up in several modules):

| Area | Seconds | What the time is spent on |
|---|---|---|
| Text search pipeline (13 text and statistics modules) | 13.7 | `text_search` runs a full pandas pipeline for every pattern: rebuild the paper's sentence table from JSON records, merge in the sections, grepl, subset, clean, deduplicate. A list of patterns recurses and then re-binds the pieces. ethics_check makes 94 such calls per paper, open_practices about 120. The regex work itself is tiny: ethics_check's 18,672 regex searches take 0.1 s of its 3.9 s. The whole corpus has only 4,551 sentences. |
| Repository modules (code, data, repo, codebook checks) | 9.7 | codebook_check re-casefolds the whole paper once per dictionary pattern (5,832 times, about 3 s). Nested module runs: repo_check is computed 40 times and data_check 20 times for 10 repositories, exactly as R does. Tiny DataFrames built one step at a time (pandas is 70-75% of these modules' profiled self-time). The file readers are under 0.4 s. |
| Reference modules (6 `ref_*`) | 4.4 | `ref_table` is rebuilt 90 times (4 table rebuilds and 3 merges each). The 61,375-row RetractionWatch table is re-stripped and re-filtered on every call. Six copies of dplyr-join emulation, 330 lines. |
| Statistics modules | 4.6 | Over 90% is text search and frame building. statcheck itself is about 0.3 s. |
| Cross-cutting, inside the rows above | | `paper_table` re-types a frame from JSON records on every one of 1,736 calls (about 2.2 s). `bind_rows` does full dplyr alignment even for one frame (about 1.0 s). 1,619 regex compiles take 0.96 s, and 843 of them are codebook patterns that are never searched. The regex engine itself is about 1 s. |

### Why the gap is not wider

1. **Module work caps the ratio.** It is 87% of Python's time and only 2.1x faster than R once
   R's per-call `module_run` overhead is removed. Python's big wins (reads 6x, harness 2.7x) sit
   in the small parts of the run.
2. **The port kept the data-frame-per-step shape.** On one paper (597 sentences), a single-pattern
   `text_search` takes 10.5 ms in Python and 10.6 ms in R; a 5-pattern search takes 57 ms in
   Python and 43 ms in R. In R, 80% of funding_check_oi is dplyr/tibble bookkeeping; the
   Python copy spends the same share in pandas. That is why funding_check_oi was slower in
   Python when cold (1.94 s against 1.64 s) and coi_check_oi tied.
3. **Tables are rebuilt on every call instead of once per paper**: the sentence table (5,862
   builds), `paper_table` (1,736), `ref_table` (90), the RetractionWatch table (on every call).
4. **Some R-compatibility helpers had hidden per-call costs in hot loops.** `casefold()` used
   `str.translate` with a dict (17x slower than `replace` + `casefold` on non-ASCII text), and
   codebook called it 5,832 times on the same text. `bind_rows` aligned schemas that already
   matched. `grepl` compiles a pattern before its literal prefilter rejects it.
5. **The modules where Python is far ahead are the ones where R is heavy**, not ones where the
   Python logic is lean. stat_check is 8.5x, reads 6x, code_check 4x.

### Fair per-module comparison

R and "Python today" come from the measure lane: cold, minimum of 3 R and 5 Python runs, same
harness setup and same load (3.6-9.3). "R without overhead" removes R's `module_run` cost
(module_find + roxygen2 parse + source on every call, measured per module in
`measure/raw/r_overhead.txt`), which Python does not have. The stack columns come from one
interleaved A/B session (base against the 14-patch stack, per-module minimum of 3, load 1.1-1.9,
`synth/ab5.txt`). The last ratio column applies that speed-up to the measured R/Python ratio.

| Module | R | R without overhead | Python today | R/Py today | Python, base -> stack | Stack speed-up | R/Py with stack (est.) | What still dominates Python time with the stack |
|---|---|---|---|---|---|---|---|---|
| ethics_check | 12.95 | 12.82 | 3.97 | 3.2x | 3.77 -> 0.65 | 5.8x | 18.7x | 63-pattern search over one table |
| codebook_check | 8.64 | 6.99 | 5.94 | 1.2x | 5.72 -> 1.75 | 3.3x | 3.8x | its nested data_check (about 80%) |
| data_check | 6.49 | 5.38 | 1.98 | 2.7x | 1.91 -> 1.35 | 1.4x | 3.8x | two nested repo_check runs, roster search, per-metric joins |
| stat_check | 6.37 | 6.19 | 0.73 | 8.5x | 0.71 -> 0.55 | 1.3x | 10.9x | statcheck, first scipy import |
| open_practices | 5.93 | 5.79 | 4.33 | 1.3x | 4.10 -> 0.61 | 6.7x | 9.0x | chained searches on small frames |
| stat_effect_size | 5.29 | 5.04 | 1.57 | 3.2x | 1.46 -> 0.78 | 1.9x | 6.0x | extract_eq, digit-sentence search |
| code_check | 5.15 | 4.68 | 1.17 | 4.0x | 1.12 -> 0.76 | 1.5x | 5.9x | nested repo_check (about 65%) |
| repo_check | 2.64 | 2.35 | 0.64 | 3.7x | 0.62 -> 0.37 | 1.7x | 6.2x | per-file pandas in _report/_classify, roster |
| ref_retraction | 2.44 | 2.35 | 1.07 | 2.2x | 1.05 -> 0.52 | 2.0x | 4.4x | ref_table rebuild, joins |
| funding_check | 2.42 | 1.91 | 0.87 | 2.2x | 0.73 -> 0.60 | 1.2x | 2.7x | not profiled this round (lane2 module) |
| ref_consistency | 2.08 | 1.98 | 0.99 | 2.0x | 0.96 -> 0.83 | 1.2x | 2.3x | ref_table plus 166 merges |
| funding_check_oi | 1.64 | 1.54 | 1.94 | **0.8x** | 1.96 -> 0.23 | 8.5x | 6.8x | done |
| coi_check | 1.51 | 1.37 | 0.49 | 2.8x | 0.39 -> 0.28 | 1.4x | 3.9x | - |
| stat_p_nonsig | 1.38 | 1.16 | 0.75 | 1.5x | 0.69 -> 0.45 | 1.5x | 2.4x | text_expand collapses the whole paper |
| stat_p_exact | 1.15 | 1.04 | 0.81 | 1.3x | 0.82 -> 0.70 | 1.2x | 1.5x | text_expand, full join emulation |
| ref_replication | 1.11 | 0.99 | 0.57 | 1.7x | 0.56 -> 0.41 | 1.4x | 2.4x | ref_table, joins |
| ref_miscitation | 1.00 | 0.89 | 0.56 | 1.6x | 0.57 -> 0.47 | 1.2x | 1.9x | ref_table, right-join emulation |
| ref_accuracy | 0.94 | 0.69 | 0.42 | 1.6x | 0.45 -> 0.28 | 1.6x | 2.6x | ref_table, joins |
| all_p_values | 0.93 | 0.83 | 0.68 | 1.2x | 0.68 -> 0.46 | 1.5x | 1.8x | text_expand |
| coi_check_oi | 0.86 | 0.75 | 0.88 | **0.8x** | 0.80 -> 0.27 | 3.0x | 2.5x | chained searches |
| marginal | 0.64 | 0.47 | 0.27 | 1.7x | 0.27 -> 0.15 | 1.8x | 3.1x | building the sentence table |
| ref_summary | 0.61 | 0.51 | 0.35 | 1.4x | 0.35 -> 0.20 | 1.7x | 2.5x | ref_table |
| all_urls | 0.48 | 0.40 | 0.31 | 1.3x | 0.29 -> 0.19 | 1.5x | 2.0x | extract_urls' own sentence table |
| **Total** | **72.65** | **66.11** | **31.29** | **2.1x** | **29.98 -> 12.86** | **2.3x** | **about 4.9x** | |

Peak memory: R 426-428 MB, Python 176-179 MB.

### The stacked result (measured for this report)

These 14 patches apply together cleanly to "perf base" and compile (+635/-430 lines in 21 files).
Their union is `patches/synth-stack-14.patch`:

- lane5: `V1-TS2a-join-one-no-cache` (one-pass text_search plus sentence frame without the
  merge), `TS3-drop-dead-prefilters`, `TS4-paper-id-count`, `P4-PT-one-paper-table`
- lane7: `P4-RW-retractionwatch-index`, `P4-IO-bibr12-plain-cells`,
  `P3-repo-check-local-fast-path`
- lane3: `P3-codebook-fold-once`, `P3-col-stats-rows`, `P3-codebook-dict-index`
- closing: `closing-casefold-only` (new; the 7-line part of P2-casefold-grepl), `P2-bind-rows-fast`
- harness: `P4-H1-shared-papers`, `P4-H2-canonical-items`

| | Runs (s) | Minimum |
|---|---|---|
| perf base, `run_python` over all 439 outputs | 35.19, 34.01, 34.22, 34.26, 34.72 | **34.01** |
| 14-patch stack | 15.51, 16.01, 15.85, 15.60, 15.76 | **15.51** |

The runs were interleaved (base and stack alternating) at load 1.1-1.9, using the `p1/acc_run.py`
script. Every one of the 10 runs has output fingerprint `85cbf8b269`: all 439 outputs are
identical, errors included.

pytest over the 19 affected directories (`synth/pytest_*.txt`):
- The stack: 3,146 passed, 7 skipped, 0 failed, in 64 s.
- Base: 3,180 passed, 1 failed, 7 skipped, in 158 s.
- The 35-test difference is exactly the prefilter proof tests that TS3 deletes.
- Base's one failure, `tests/db/test_orcid.py::test_check_orcid_quiet`, is a warning test that
  passes on the stack; it looks like a flake.
- Most of the wall-time difference is the codebook suites.

It includes no run_session memo, so Python still does the same work as R: 40 repo_check and 20
data_check computations. It also includes no per-paper cache. Two things were not run on the
stack as a whole: the parity areas and a second verifier. Each patch passed both on its own.

---

## 2. What an optimized design looks like here, and a realistic target

The design keeps R's outputs, dtypes and regex semantics. It changes how the work is organised:

1. **A paper is read once, built once and shared.** Modules must not mutate papers (the harness
   guard checks this). Tables are built from records at most once per paper and then reused as
   copy-on-write views (`paper_table` of one paper returns a view, P4-PT).
2. **Search works on plain lists and builds one DataFrame at the end.** One `text_search` call
   gets one table of strings, folded strings and row positions. Each pattern is one compiled scan
   over those lists. Only the result rows become a DataFrame. Pattern lists and chained searches
   create no intermediate frames (TS1, then D1).
3. **R compatibility sits at the edges, not inside loops.** Regex translation (TRE/PCRE), output
   shapes, dtypes and error messages stay R-faithful. The dplyr idioms (`bind_rows` alignment,
   six join emulations, vectorise-over-NA wrappers) become one join helper and records-first
   builders.
4. **Rows are built as dicts, with one frame at the end** and explicit dtypes: data_check
   column stats, summary tables, repo_check reports, ref_table.
5. **Reference data is indexed once per process**: RetractionWatch by DOI, codebook dictionary
   keys.
6. **Derived data is cached per paper, with strict invalidation.** A cache is trusted only while
   its source is still the paper's own JSON records, or inside the `run_session()` that built it.
   It is never carried by deepcopy or pickle. This matters for `report()`, the CLI and the API,
   where all modules see the same paper. It saves nothing in the accuracy harness until papers
   are shared.
7. **Nested module runs are memo hits inside a session** (code_check -> repo_check,
   codebook_check -> data_check). The CLI and API already use `run_session()`, but its keys break
   as soon as a module builds a table (L5-3). Report this separately from code speed, because R
   does not memoise.
8. **Each file is read once per run**: sniffing, header detection and parsing share one buffer.

### Target for the Python side of the accuracy run

| Stage | Python s | R/Python (R at 92-105 s) | Status |
|---|---|---|---|
| Today, perf base | 34.0-35.8 | about 2.5-3x | verified |
| 14-patch stack (section 1) | 15.5 | about 6x | **verified** (5 interleaved runs, 439 outputs identical) |
| + remaining design items in lanes 3, 5 and 7 (join helper and ref_table, summary-table join in module_run, text_expand on result rows only, chained-search views, roster once, data_check records, read each file once, TEI column-major, repo_check records) | about 11-13 | about 7-9x | **estimate** (the sum of per-item estimates is 2.9-4.1 s; they will not add up exactly) |
| + session memo for nested module runs (Python then does less work than R) | about 9.5-11 | not comparable | **estimate**, from P3's verified 2.7 s at base, about 2 s on top of the stack |
| Fixed floor: reads 1.5, imports 0.7-1.0 (pandas, scipy.special, harness httpx), regex compile about 0.5, regex engine about 1, canonical 0.3-0.45, guards 0.2, statcheck 0.3 | about 4.5-5 | | estimate |

On the module work alone, fair against R without its roxygen overhead: 66.1 s against 12.9 s
with the stack, about 5x (verified numbers, measured in two separate sessions).

---

## 3. Work items per owner

Items are ordered by verified saving. "Current harness" means the accuracy run as it is today
(a deepcopy per output, no session).

### lane5: text/**, stats/**, papers/**, module.py, validate.py, text/stat/ref modules

**L5-1. One-pass `text_search`, and the sentence frame built without `paper_table` + merge.**
- Files: `src/pytacheck/text/search.py`.
- Change: one `_Table` per call (strings, casefolded strings, row positions, cleaned text,
  computed once). Each pattern is one compiled scan with the grepl prefilter logic. A
  multi-pattern sentence search collects positions in `bind_rows` order, deduplicates by
  position and builds one result frame. `_join_one` builds one paper's frame from its records
  with a section_id lookup instead of paper_table + merge, falling back on unusual inputs.
- Patch: `patches/V1-TS2a-join-one-no-cache.patch` (TS1 plus `_join_one`, search.py only). Do
  **not** land `TS2-paper-sentence-index.patch`: see the cache rules below.
- Verified: TS1 alone gives a full run of 34.35 -> 24.74 s (-9.6 s). TS1+TS2+TS3+TS4 gives
  34.35 -> 23.01 s (-11.3 s). TS2a measures the same as TS2 (7.165 against 7.161 s focused), so
  the whole TS2 gain comes from `_join_one`. Time inside text_search for the 13 modules goes from
  13.7 to 2.8 s.
- Parity: text, text_extract(+_review), mod_ethics(+_review), mod_urls_open(+_review),
  mod_funding(+_review), mod_coi(+_review), mod_marginal, mod_effect_size(+_review),
  mod_p_values(+_review), statout_match(+_review), mod_power(+_review), mod_causal(+_review),
  mod_prereg(+_review), bibr12(+_review), grobid12(+_review). Keep the differential tests as
  regression tests: `p1/diff_text_search.py` (3,866 cases), `p1/diff_text_frame.py` (471),
  `v1/adv_test.py` (3,314 odd inputs plus 2,220 cache cases).
- Risks: it imports the private `_compile`, `_prefilter`, `_as_str` and `casefold` and copies
  grepl's prefilter. Use the closing step's public matcher (C-3), or land both together.
  search.py grows from 293 to about 570 lines. The rewrite should delete the recursive path, not
  keep both. The dedupe skip relies on distinct non-NA (paper_id, text_id) keys. Parity covers
  multi-pattern searches only for return="sentence"; the differential tests cover the rest.
- Cache rules, if a per-paper sentence index is added later (only worth it once the harness
  shares papers, or for report(), CLI and API):
  - Start from `patches/V1-TS2-session-scoped-cache.patch`, not TS2. TS2 reuses entries for
    materialised tables across sessions, so an in-place edit between sessions gives stale
    results (84 of 2,220 adversarial cases).
  - Trust an entry only while its source tables are the same JSON record lists, or inside the
    same `run_session()` (a weakref to the session).
  - Always rebuild a materialised table outside a session.
  - `_touch()` clears the cache, and `__getstate__`/deepcopy leave it out.
  - Measured size: about 50 KB per paper.

**L5-2. `paper_table()` of one paper returns the paper's own built table as a view.**
- Files: `src/pytacheck/papers/tables.py`, `papers/model.py`, `module.py`.
- Patch: `patches/P4-PT-one-paper-table.patch`. It bundles the LZ memo-key fix.
- Verified: -1.55 s on its own (24.87 -> 23.32 s over the 399 paper outputs) and -1.34 s on top
  of H-1. The claimed 2.2 s holds only for report()-style runs on unbuilt papers. `paper_table`
  goes from 2.5-4.3 ms to 0.27-0.37 ms per call.
- Parity: core, text, text_extract, mod_ref_db, mod_ref_accuracy, mod_ref_pubpeer_summary,
  mod_ethics, mod_p_values, mod_effect_size. The full parity run with all P4 patches was
  identical to base.
- Risks: `paper_table` now builds tables on the caller's paper. `Paper._materialise` is not
  thread-safe (pre-existing). The new `_lazy` slot changes pickles. It conflicts with L5-3 and
  with any `_derived` slot in model.py, so merge by hand. Two follow-ups: cache the
  paper_id-added view per paper (still 0.7 ms per call), and build `paper_table(cols=...)` from
  the kept columns only (about 600 calls, 0.45 ms each; estimate 0.2-0.3 s).

**L5-3. `run_session()` memo keys stay stable when a paper builds its own tables.** This is a
product bug: in a CLI or API session, 9 of 10 nested data_check calls miss today.
- Files: `src/pytacheck/module.py` (`_paper_state`), `papers/model.py`,
  `tests/modsys/test_session.py`.
- Patch: `patches/P3-memo-built-tables.patch`. The LZ part of P4-PT solves the same problem.
  Land one of the two; P3's version also covers empty schema tables.
- Verified: no saving in the current harness. -1.34 s once the harness uses a session
  (33.40 -> 32.06 s). Repository outputs go from 8.73 to 6.95 s. `_repo_check` computations drop
  from 19 to 10.
- Parity: core, mod_codebook, mod_data_check; tests/modsys and tests/foundation.
- Fix before landing: record `id(frame)` for each table the paper built itself, and use the
  stable token only while the stored table is that same object. Otherwise a table replaced
  through `paper._tables[name]` gets a stale hit (`verify-p3/memo_probe.py`). Add a `getattr`
  fallback in `_materialise` for Papers without the slot.

**L5-4. `paper_id()` counts info rows instead of building the info table.**
- Files: `src/pytacheck/papers/tables.py`.
- Patch: `patches/TS4-paper-id-count.patch` (+28/-1).
- Verified: -0.55 s (20.44 -> 19.82 s). The gain is in the repository modules, not the paper
  modules.
- Parity: mod_ethics, mod_urls_open, mod_repo_check(+_review), mod_data_check(+_review),
  mod_code(+_review), mod_codebook(+_review), mod_psychds(+_review), mod_repro(+_review),
  mod_reg(+_review), mod_ref_accuracy(+_review), mod_ref_db(+_review), core, report(+_review).
- Risk: low.

**L5-5. The ref_retraction consumer of lane7's RetractionWatch index.** `_rw_entries` uses
`rw_rows()`. It is part of `patches/P4-RW-retractionwatch-index.patch` (-0.53 s, see L7-2).
Lane5 owns this 5-line module edit, so coordinate with lane7.

**L5-6. Delete the hand-written prefilters that L5-1 made pointless** (`_LIVE_ANY`,
`_FIRST_STAGE_ANY`, `_REPO_ANY` and their proof tests).
- Patch: `patches/TS3-drop-dead-prefilters.patch` (+4/-166).
- Verified: 0 s change, which is the point: it is a simplification.
- Parity: mod_ethics(+_review), mod_urls_open(+_review), text_extract(+_review).
- Risk: land it with or after L5-1, never before. `_ETHICS_ANY` stays until C-5.

Design items (not prototyped; estimates):
- **One dplyr-join helper** on key positions (about 100 lines). It replaces ref_summary `_join`
  and friends, ref_consistency `_full_join`, ref_accuracy `_left_join`, ref_miscitation
  `_right_join`, open_practices `_full_join`, stat_p_exact `_full_join`, validate `_full_join`
  and search `_semi_join`, and lane3/lane6 can adopt it.
  - `ref_table` is then built on it and cached per paper, which only pays when papers are
    shared.
  - Estimate: 0.5-0.9 s, and about -230 lines.
  - Named output change: with duplicate keys on both sides, pandas' multi-key merge returns rows
    in a non-dplyr order. The helper returns dplyr's order (x, then y), which is closer to R.
    Test with `verify-p4/rt_fuzz.py`, where 56 of 2,618 cases differ, in row order only.
  - `patches/P4-RT-ref-table-join.patch` (-0.19 s verified) is a prototype of the ref_table part.
    Fold it into the helper; do not land it as a second code path.
- **module_run's summary_table join**: a pandas merge of two one-row frames on every output (429
  merges), plus `_apply_na_replace`. Replace them with a dict join. Estimate 0.35-0.5 s.
- **text_expand on result rows only.** Today it runs `text_search('.*')` and `_collapse` over the
  whole paper (63 calls). Estimate at most 0.3 s. TS5 (swapping the merge for a lookup) was
  measured, gained nothing and was rejected.
- **Chained searches without intermediate frames** (open_practices `_chain`, coi_check_oi,
  funding_check_oi, power, extract_eq). Realistic 0.2-0.3 s. Build it on the public matcher (C-3).
- **Validate a pattern with the same compile key that grepl uses** (posix=False). Verified cost
  0.09 s.
- **extract_eq**: one sort key instead of 10 operator searches. 0.4-0.5 s before L5-1, about
  0.05 s after it, so only worth doing inside the rewrite.
- **Share the p-value, digit-sentence and expansion tables per paper per session** across the
  five statistics modules. 0 s in the current harness; an estimated 40-50% of those modules in
  report()-style runs.
- **scipy.special import** (0.28-0.31 s, the first `pt()` call): unavoidable while stat_check
  needs it. It is a CLI-latency item only.

### lane3: datacheck/**, data_check, _data_check, codebook_check, _codebook, psychds_check

**L3-1. Casefold the paper once for the codebook dictionary scans.**
- Files: `src/pytacheck/modules/_codebook.py` (`_safe_grepl`, via `_scan_paper_with_dict` and
  `corroborates()`).
- Patch: `patches/P3-codebook-fold-once.patch` (+16/-2). It supersedes
  `measure-codebook-fold-once.patch`, whose module-global single slot is not thread-safe under
  the API server.
- Verified: codebook_check over 10 repositories goes 6.24 -> 2.60 s cold and 5.61 -> 2.36 s warm
  (-3.3 s). On a real 94K-character paper, one scan goes from 8.2 s to 0.2 s. The mod_codebook +
  mod_psychds pytest suites go from 113.5 s to 11.8 s. The equivalence check covers 19,880
  pattern-text pairs with 0 differences.
- Parity: mod_codebook(+_review), mod_psychds(+_review), datacheck_columns(+_review).
- Risks: it imports the private `_prefilter`; move to C-3's public matcher. The
  `lru_cache(maxsize=8)` holds up to 8 texts. Part of the saving is compilation it now skips
  (833 compiles, of which 68 are needed).

**L3-2. data_col_stats rows as dicts, one stats frame per file.**
- Files: `src/pytacheck/datacheck/columns.py`, `modules/data_check.py`,
  `tests/mod_data_check/test_data_check.py`.
- Patch: `patches/P3-col-stats-rows.patch` (+27/-41).
- Verified: `_file_columns` goes 0.274 -> 0.094 s per data_check pass. That is -0.38 s in the
  current harness (20 passes) and about -0.19 s with a session.
- Parity: mod_data_check(+_review), datacheck_columns(+_review), mod_codebook(+_review).
- Risk: low. Make this the default pattern for the rewrite: build rows, then one frame.

**L3-3. Build the codebook dictionaries' acronym and first-token indexes once.**
- Patch: `patches/P3-codebook-dict-index.patch`.
- Verified: -0.08 s per pass. The claimed 0.13 s was not reproduced.
- Parity: mod_codebook(+_review), mod_psychds(+_review).
- Fold it into the rewrite rather than tracking it alone. The lists it caches are shared, so
  return tuples.

Design items (estimates):
- **Compute the study roster once per paper.** `data_study_roster(paper)` costs 9-10 ms per call
  and is called 60 times, in every repo_check._classify and data_check._classify. Pass repo_check's
  roster through, or memoise it on the paper's memo key once L5-3 lands. Estimate 0.3-0.55 s in
  the current harness and about 0.18 s with a session.
- **Classify each file once.** data_check._classify re-runs `data_classify_files` and
  `_file_category` on files repo_check already classified: 40 and 60 calls even with every P3
  patch applied. Reuse repo_check's table, which data_check already receives. Estimate 0.1-0.3 s.
- **Build data_check's summary tables from records**: `_summary_table`, `_validate` and
  `_spreadsheet_counts` today chain 74 one-row joins per 10 repositories. Estimate 0.2-0.27 s.
  Risk: `merge(all=TRUE)` row order and NA fill; only mod_data_check(+_review) covers it.
- **Read each data file once per run.** Today each file is opened about 6 times per data_check
  run. Estimate 0.1-0.15 s here; on real multi-MB files this is the main cost. Cache key:
  (realpath, st_size, st_mtime_ns), scoped to the run (contextvar or session).
- **Memoise the codebook scan across repositories**: every repository scans the same demo paper
  with the same dictionaries. Key it by (haystack, tuple of patterns), never by object identity,
  because `scales()` and `tasks()` rebuild their frames. Estimate about 0.18 s.
- **The readers** (fread, read.table and readxl emulation) take 0.24-0.33 s of the whole run.
  Judge the pandas C engine and calamine rewrite on simplicity, not on speed in this run.

### lane4: codecheck/**, code_check, reproducibility

- **No speed case on this run**, measured and verified. The decoder, purl and parser take
  0.016-0.038 s per 10 repositories. `analyse_files` takes 0.15-0.22 s. 65-70% of code_check is
  its nested repo_check, which H-2, L5-3 and L7-1 handle. Judge the rewrite on lines removed.
- **Small item for the rewrite**: one tokenise/strip pass per file, shared by
  code_remove_comments, code_library_names/_live_calls, code_abs_path, code_setwd and
  code_install_packages. It removes the pandas use in `_live_calls` (0.07 s profiled).
- Parity: mod_code(+_review), codecheck, mod_repro(+_review). In perf worktrees,
  mod_code_review `local_path.code_files` and `local_path.parse_errors` fail on base too,
  because `upstream/metacheck` is symlinked there. That is an environment artefact.

### lane6: llm/**, utils.py

- Nothing in the accuracy matrix spends measurable time in llm/** or utils.py. The LLM modules
  are not in the matrix, and `data_group_llm`'s roster search belongs to lane3.
- One item: `llm/core._left_join` is another copy of the dplyr-join emulation. Switch it to
  lane5's shared join helper once that exists. No timing claim.

### lane7: archives/**, db/**, io/**, statout/**, report/**, api/**, repo_check, reg_check

**L7-1. repo_check: skip the 13 empty platform listings when a paper has no repository links.**
- Files: `src/pytacheck/modules/repo_check.py`.
- Patch: `patches/P3-repo-check-local-fast-path.patch` (+27/-49).
- Verified: `_repo_check` per 10 calls goes 0.581 -> 0.428 s. That is -0.6 to -0.8 s in the
  current harness (40 computations) and -0.15 to -0.2 s with a session.
- Parity: mod_repo_check(+_review), mod_data_check(+_review), mod_code(+_review).
- Risk: the benchmark shaped this fix. It fires only when there are no links, so a paper with
  one OSF link still pays about 17 ms per call. The underlying costs are empty-frame
  construction (`meta_frame` via `r_frame`: 0.45 ms each) and binding them (12 empty meta frames:
  8.2 ms; 15 placeholders: 4.1 ms). The general fix is to drop zero-row frames whose schema is
  already represented before `bind_rows`, or C-2's `bind_rows` fast path.

**L7-2. Clean and index RetractionWatch by lower-case DOI once per database file.**
- Files: `src/pytacheck/db/databases.py`, `db/retractionwatch.py`, plus the lane5 consumer
  `modules/ref_retraction.py`.
- Patch: `patches/P4-RW-retractionwatch-index.patch` (+45/-16).
- Verified: ref_retraction goes 1.038 -> 0.447 s, or -0.53 s net of the one-off 0.06 s index
  build.
- Parity: db(+_review), mod_ref_db(+_review).
- Fix before landing: keep `newest_database()` private, or hand callers copy-on-write copies
  only. Today it returns the process-wide cached frame, and a caller that writes into it corrupts
  every later lookup.
- Invalidation: key the index on the database frame's identity, so a newer `rw_update()` file
  rebuilds it.

**L7-3. bibr12 column typing: skip the R coercion round trip for plain values of the column's
type.**
- Files: `src/pytacheck/io/bibr12.py`.
- Patch: `patches/P4-IO-bibr12-plain-cells.patch` (+10).
- Verified: read() of the 11 TEI files goes 1.554 -> 1.405 s, and of all 21 papers 1.696 ->
  1.504 s.
- Parity: bibr12(+_review), io(+_review), grobid12(+_review).
- Risk: low.

Design items (estimates):
- **TEI read pipeline**, estimate about 0.3-0.5 s:
  - Keep converter output column-major, as typed lists, instead of record dicts that
    `records_to_frame` re-types.
  - Stop compiling one regex per reference in `grobid._join_doi` (442 per run).
  - Use `copy.deepcopy(tree)` instead of `read_xml(as_character(xml))`.
  - `extract_eq` during read gets faster automatically with L5-1.
  - Keep Grobid XML papers as lazy records, as JSON papers already are. Today 11 of the 21
    papers hold materialised DataFrames, so no per-paper cache can trust them outside a session.
  - Coordinate Paper internals with lane5.
- **repo_check `_report`, `_classify` and `_summary_table` on lists of file dicts.** Realistic
  5-10 ms per call: 0.2-0.4 s in the current harness, 0.05-0.1 s with a session. The report's
  HTML tables must stay byte-identical.
- **repo_check's side of the roster-once item** (see lane3).
- **`report/blocks.scroll_table`** runs eagerly for every output (432 calls, 0.5 s profiled). It
  is needed in the accuracy run, but could be lazy for API and table-only users.

### harness: parity/**

**H-1. One Paper object per input, shared by all outputs, with a mutation guard.** This is the
same work R does: run_cases.R reads each paper once and passes it to every module.
- Files: `parity/accuracy.py` (`run_python`, `paper_for`).
- Patch: `patches/P4-H1-shared-papers.patch` (+30/-8).
- Verified: 24.87 -> 23.29 s over the 399 paper outputs (-1.5 s). With L5-2 the two together
  save 2.92 s. They are synergistic, not additive.
- Outputs: 0 of 439 differ.
- Fix before landing: take the guard's snapshot with `copy.deepcopy(paper)` before building the
  tables. The current `paper.copy(deep=True)` misses in-place changes to list cells
  (info.keywords, author.role, bib_match.authors) and to nested `extra`
  (`verify-p4/h1_guard_test.py`). The fix costs about 0.1 s.
- H-1 is also what lets any per-paper cache survive from one module to the next.

**H-2. run_session() for the repository outputs, or for all outputs.** This makes Python do
**less work than R**: 10+10 computations instead of R's 40+20 repo_check and data_check. Record
it as a separate timing (for example `python_session`), or keep the plain mode as the number
compared with R.
- Patch: `patches/P3-harness-repo-session.patch`. It conflicts with H-1 in `run_python`: merge
  by hand, keeping H-1's shared papers for all outputs and adding the session.
- Verified: -1.38 s alone (34.78 -> 33.40 s) and -2.7 s with L5-3 (32.06 s). Shared papers plus
  a session over all outputs, on top of the text_search rewrite, gives 28.84 -> 24.10 s
  (-4.7 s), with 0 of 439 outputs changed.
- The "stat_check leak" that blocked this was an artefact: the benchmark monkeypatched the
  global `copy.deepcopy`, which broke scipy's `_docscrape`.
- Without H-1's guard, keep one deepcopy per repository (0.5 ms each) so results do not depend
  on matrix order.

**H-3. `canonical()` takes columns with `df.items()`.**
- Patch: `patches/P4-H2-canonical-items.patch` (1 line).
- Verified: 0.43 -> 0.30 s. It also speeds up every parity check.

**H-4. Record timings in the accuracy report.** See section 4.

Keep the fresh cache directory per output (`_isolated`, 0.21-0.23 s): it is what keeps outputs
isolated.

### closing: _r/** and cross-cutting items after lanes 3-7

**C-1. `casefold()` without `str.translate`.**
- Files: `src/pytacheck/_r/regex.py`.
- Patch: `patches/closing-casefold-only.patch` (new, 7 lines, from verify-p2's `cfonly` tree; it
  applies to perf base).
- Verified as part of P2-casefold-grepl: whole run 38.71 -> 34.65 s. A replay of real grepl
  calls attributes 97-98% of that to this 7-line change: grepl time per run goes 3.83 -> 1.00 s,
  against 0.94 s for the full patch.
- Once L3-1 lands, what is left is about 0.14 s, and less after L5-1. It is still a pure
  7-line win for every caller.
- Parity: rcompat, text, text_extract, stats, the mod_* regex areas, and
  `tests/foundation/test_regex_replay.py` (33k recorded calls).
- Drop P2's `_detector` cache (+25 lines, a second 8,192-entry cache that keeps compiled
  patterns alive, about 0.06 s) unless it becomes C-3.

**C-2. `bind_rows` fast path when every part already has the output schema.**
- Files: `src/pytacheck/_r/frames.py`.
- Patch: `patches/P2-bind-rows-fast.patch` (+12/-2).
- Verified: -1.0 s at base. The claimed 2.2 s was not reproduced. After L5-1 and L5-2 it is
  worth 0.1-0.2 s.
- Extend it to zero-row parts, which covers repo_check's empty listings for papers that do have
  links (L7-1).
- Parity: every area; all 71 areas were identical.

**C-3. A public matcher in `_r/regex`.** This merges three proposals: D5, P2-public-detector and
D-closing-grepl-many.
- It compiles once, applies the literal prefilter, takes precomputed and pre-folded strings, and
  has a "one text, many patterns" form.
- text_search (L5-1), `_codebook` (L3-1) and `_funding._column_mask` then stop importing
  `_compile`, `_prefilter`, `_as_str` and `casefold`, which removes about 25-30 duplicated lines.
- Speed: 0 s.
- Do **not** skip the prefilter for long strings. The verifier measured that as slower: 0.39 s of
  compiles plus 0.26 s of search, against 0.16 s.

**C-4. Compile only after the literal prefilter.** 793 of 1,229 grepl patterns are compiled but
never searched (0.33 s per process); after L3-1 this is worth 0.01-0.05 s. R's grepl must still
raise on an invalid pattern, so this needs a cheap validity check first. Low value: do it only
inside C-3.

**C-5. Optional: required-literal extraction plus one union prefilter per multi-pattern search,
then delete `_ETHICS_ANY`.** 0 s, a small net simplification. A wrong literal for TRE (`\<`,
`\>`, bounded repeats, `[[:class:]]`) drops matches silently, so it needs a property test over
every module's pattern list.

**C-6. Lazy `__version__`.** importlib.metadata is 44-61 ms of `import pytacheck`. It saves
about 0.05 s per process, which matters for CLI latency, not for this run.

**C-7. Pre-existing test failures in the regex layer.** Ten `tests/mod_power` TRE ignore-case
tests and `tests/stats/test_review.py::test_case_insensitive_patterns_fold_like_r` fail on perf
base. Under IGNORECASE the regex engine treats İ as i; PCRE2 and TRE do not.

**C-8. Performance guard: the work-count test** (section 4).

Not recommended: routing simple patterns to `re` (0.4 s, high risk of case-folding and Unicode
class differences) and a dict-based `frames.count` (0.1 s, ordering semantics risk).

Environment note for whoever makes worktrees: in every perf worktree, `upstream/metacheck` was a
directory containing a symlink, so upstream fixtures did not resolve (mod_marginal.psychsci
failed with nrow R=2, Python=0). Several agents replaced it locally with a symlink.

---

## 4. A performance guard that tolerates noise

There are two layers. Deterministic work counts are the hard gate. Timings are recorded and
compared as a ratio against R, which runs on the same machine.

**(a) Timings in the accuracy JSON report (harness, `parity/accuracy.py`).**
- Extend the existing `seconds` field to a `timing` block:
  `{"python": {"total", "imports", "reads", "modules": {name: seconds}, "harness"}, "r",
  "ratio", "host": {"cpus", "load_before", "load_after", "python", "pandas"}}`.
- Time each module by wrapping top-level `module_run` calls only, with a depth counter as
  `p1/acc_run.py` does. That is 439 `perf_counter` pairs, which cost nothing.
- Print the five slowest modules in the text summary and in `--md` (the GitHub step summary).

**(b) A self-calibrating ratio budget where R runs** (`parity.yml`, which already runs
`--generate`).
- Fail when `r_seconds / python_seconds` falls below a budget kept in
  `parity/accuracy/perf_budget.toml`.
- Both sides run in the same job on the same runner, so machine speed cancels out and load
  mostly does too.
- Set the budget to about 70% of the ratio measured after the lanes land (for example: measured
  6x, budget 4x).
- Also compare per-module ratios as a warning only, never a failure, at half the recorded
  per-module ratio.

**(c) A warning-only absolute budget where R does not run** (the `ci.yml` accuracy job). Emit
`::warning::` when the Python total exceeds 1.5x the value recorded in `perf_budget.toml` for
GitHub runners. Timings on shared runners are too noisy to fail on.

**(d) The hard gate: deterministic work counts** (closing, `tests/perf/test_work_counts.py`,
about 5-10 s). Run a fixed small workload: 3 papers, 2 repositories, the heaviest modules. Count
calls with monkeypatched wrappers, so production code carries no instrumentation. Suggested
assertions, each tied to a regression found in this round:
- One `text_search` call on a paper builds its sentence frame at most once, whatever the number
  of patterns (was 1 per pattern).
- One `module_run` calls `records_to_frame` at most once per (paper, table) (was once per
  `paper_table` call).
- One codebook_check on the demo paper casefolds strings over 2,000 characters at most twice (was
  833 or more).
- One codebook_check compiles fewer than 150 regex patterns (was 833 or more).
- Inside `run_session()`, code_check + data_check + repo_check + codebook_check on one
  repository compute `_repo_check` once and `data_check._extract` once (today 4 and 2).
- The RetractionWatch index is built once per process.
- `ref_table` runs at most once per paper per module.

These counts do not depend on the machine. They fail on exactly the structural mistakes that cost
the most here, and they are cheap enough for every CI run.

**(e) Keep `benchmarks/test_bench.py` for trends only** (pytest-benchmark; CI already uploads
`benchmark.json`). Add a 60-pattern `text_search`, a codebook dictionary scan on the demo paper
and a single-paper `paper_table`. Do not gate on them.

---

## Evidence

- Measurements, R against Python: `measure/` (`raw/analyze.txt`, `raw/r_overhead.txt`, `raw/r-*.json`,
  `raw/py-*.json`, the `time_r.R` and `time_py.py` scripts).
- Stacked A/B for this report: `synth/ab5.txt` and `synth/pair.txt` (runs), `synth/compose`
  (the stacked tree), `synth/pytest_compose.txt` and `synth/pytest_base.txt`.
- Text search: `p1/` (`ab_cum.summary`, `acc_ab.txt`, the diff_* differential tests), verified in
  `v1/` (`acc_ab.txt`, `adv_test.py`, `accshared_v1.py`).
- Regex layer: `p2/`, verified in `verify-p2/` (grepl and bind_rows replays, fuzzing).
- Repository modules: `p3/`, verified in `verify-p3/` (`memo_probe.py`, `scan_equiv.py`,
  `grepl_waste.py`).
- Paper I/O, references and harness: `p4/`, verified in `verify-p4/` (`h1_guard_test.py`,
  `rt_fuzz.py`, `pt_equiv.py`, `memo_test.py`).
- Patches: `patches/`. Each applies alone to "perf base". The 14 in the stack also apply together
  (`synth-stack-14.patch`). `P3-memo-built-tables` conflicts with `P4-PT` (the same memo fix), and
  `P3-harness-repo-session` conflicts with `P4-H1` (both edit `run_python`).
