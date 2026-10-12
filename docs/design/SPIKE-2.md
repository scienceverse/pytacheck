# SPIKE-2: grouped search, read-time Docs and the output boundary

SPIKE-2 is a two-day throwaway prototype. It tests the parts of the core rewrite that the first spike skipped, before the rewrite deletes the old code. That rewrite step is **CORE-1b**. It moves `text_search()` and its relatives onto the Doc engine and deletes the pandas helpers they use today. ARCHITECTURE.md §7.3 sets the brief.

The prototype is the local branch `arch/spike2-main`, based on main at 4010388b. It is not published, and nothing on it is meant to be merged as is; the commit ids below refer to it. CORE-1b rebuilds what it needs from it on main. Numbers marked **(M)** were measured on this branch, on a shared machine (load average 11-18 from other jobs). Timings are the best of 5-15 interleaved rounds unless a row says otherwise.

## Short glossary

- **Doc**: one paper's sentences, indexed once. Searches return bitsets over its rows.
- **Hits**: the result of a search on Docs. Chaining `.where(...)` is the same as nesting `text_search()` calls.
- **Grouped modes**: `text_search(..., return_="paragraph" | "section" | "header" | "paper_id")`. These join the matching rows' text per group.
- **V5**: the rule that a grouped mode returns exactly the table metacheck (R) returns, including the text joined and cleaned once.
- **V7**: each call drops duplicate rows, as R's `unique()` does.
- **F6**: the rule for paper lists whose papers share an id.
- **U80**: a section's header is the header its paragraphs share.
- **Materialised paper**: a paper whose `text` or `section` table a user has read as a DataFrame. It may then have been edited, so the raw records can no longer be trusted.
- **Boundary floor**: the fixed cost per module output of turning results into DataFrames: tables, the summary merge, the report and the `ModuleOutput`.
- **A/B**: a rewritten module compared with a frozen copy of main's version (`tests/_legacy/modules`).
- **W1**: the first wave of module migrations after CORE.

## What was built

| # | Item | Where |
|---|---|---|
| 0 | The spike-1 tree carried onto current main, with the `pytacheck` → `metacheck` rename applied. This finished inside its timebox, so the fallback branch was not needed. | c3d4e123 |
| 1 | `Doc.groups(level)` builds a `GroupDoc`, a Doc whose rows are paragraphs, sections, headers or papers. Its stage 0 is the joined text and its stage 1 is that text cleaned once. `Hits.group()`, `.paragraphs()` and `.sections()` work on it, and `text_search()`'s grouped modes use them. `_paste_groups`, `_semi_join` and `_section_headers` are deleted. | `core/groups.py`, `core/hits.py`, `text/search.py`; 0205d2ad |
| 2 | power (LLM off) and coi_check_oi search on the core: `hits(...).paragraphs().where(...)` and `.sections()`. Frozen copies of main's versions serve as the A/B oracle. | `modules/power.py`, `modules/coi_check_oi.py`; 2c0de3f4 |
| 3 | The Grobid reader computes the `eq` table from a Doc built from the paper's records at read time, then keeps that Doc for the run. A lazy variant builds `eq` on first use. A switch selects one of three modes: base (main's path), core (eager) and lazy. | `io/grobid_bibr12.py`, `text/extract.py` (`eq_matches`), `papers/model.py` (`_defer`); 8e2be950 |
| 4 | A `.tolist()` backing: a materialised paper's Doc reads one `.tolist()` per column instead of joining pandas frames. | `core/doc.py` (`_source_columns`); adf0ae5d |
| 5 | `detect_many(pats, text, literals="scan" \| "index")` tests one text against many patterns. It folds the text once, checks every clause of each pattern's required literals, and compiles only the candidates. | `core/patterns.py`; 46c415dd |
| 6 | A per-output profile of the boundary floor, plus one cut: the summary merge builds its frame once instead of inserting one column at a time. | `spike/floor.py`, `module.py` (`_merge_summary`); 5954bac2 |

Scripts added under `spike/`:
- `diff_chains.py`: a generative test that compares random chains of 1-4 calls against the frozen façade.
- `mutants.py`: breaks the grouped views on purpose, to show the checks notice.
- `read_xml.py`, `materialised.py`, `detect_many.py` and `floor.py`: the measurements below.
- `bench.py` now also covers power and coi_check_oi.

## Pass criteria

### V5: grouped modes give R's tables. **Pass**

- `diff_text_search` compared against main's own `text_search()`, recorded in a separate process so no oracle code ran alongside: 3,866 cases with 0 differences. Of these, 1,192 are return-mode cases, also with 0 differences **(M)**. The same result holds against the frozen façade in-process.
- `diff_chains` generates chains of 1-4 calls on random papers and paper lists built to hit the hazards: interleaved paragraphs, `" , "` and whitespace that cleaning changes, empty and NA text, repeated texts, and pattern lists. Seed 1: 2,000 chains, 4,023 of their steps grouped, 0 differences. Seed 7: 2,000 chains, 3,999 grouped steps, 0 differences **(M)**.
- Mutants: each of the four deliberate breaks is caught by `diff_chains` **(M)**:

  | Mutant | diff_chains differences | ab_outputs differences |
  |---|---:|---:|
  | later stages match the joined text before cleaning | 132 | 0 |
  | paragraphs joined with a space, not the paragraph marker | 61 | 0 |
  | groups built from matched rows only, not all searched rows | 131 | 18 |
  | groups in row order, not the call's table order | 19 | 2 |

  The module A/B on real papers catches only two of the four. **The generative chain test has to be a CORE-1b gate**, not just the module A/B.

- `exclude = TRUE` with a pattern list in a grouped mode, on the 21 corpus papers: 420 cases with 0 differences on the façade **(M)**.

### A/B: power and coi_check_oi. **Pass**

24 gated inputs: the 21 accuracy papers, the demo paper, the psychsci list and a 3-paper list. Each runs all six modules and is checked on 6 elements (table with exact dtypes, summary table, traffic light, summary text, report and `na_replace`). That gives 864 elements plus 105 chained runs, with **0 differences** **(M)**. The two SPIKE-2 modules alone account for 288 elements and 15 chains, with 0 differences.

The hazard (later stages run on the joined, cleaned paragraph text, in pattern-major order) is covered by `diff_chains` and its mutants above. The first power A/B found 2 row-order differences. They came from grouped calls with a pattern list, which R orders pattern by pattern. They were fixed before the result above.

The 21-paper list with 4 repeated ids is not gated. It changes on purpose under F6/V7, as in spike 1. power goes from 29 to 19 table rows and coi_check_oi from 12 to 9. ethics, open_practices and stat_p_exact change exactly as spike 1 reported.

### read(xml): measured, target proposed

The 11 Grobid XML papers of the accuracy matrix, warm, best of 15, mean per paper **(M)**:

| Mode | read | first `Doc.of` afterwards | first `paper.eq` |
|---|---:|---:|---:|
| main (its own tree, separate process) | 47.0 ms | – | – |
| base (main's path, this tree) | 43.2 ms | 0.54 ms | 0.60 ms |
| **core** (eq from the read-time Doc, Doc kept) | **40.3 ms** | 0.01 ms | 0.72 ms |
| lazy (eq built on first use) | 32.8 ms | 0.55 ms | 7.85 ms |

On the largest paper (92,589 characters), the read takes 77.7 ms in base, 72.3 ms in core and 58.6 ms in lazy **(M)**.

- **Proposed target: ≤ 40 ms mean on these 11 papers, with `eq` eager.** That is about 7% below base and the Doc comes free.
- Lazy `eq` saves about 7.5 ms per paper only when nothing reads `eq`. Many default modules do read it. It also moves the "comparator left out" warning from read time to first use, which changes behaviour. If that is accepted, the target becomes ≤ 33 ms.
- The rest of the read is XML parsing and R-compatible regex in the reader. That belongs to SERVICES, not CORE.

### Materialised papers: 1 Doc build per run, ≤ 3 ms. **Pass**

- The `.tolist()` backing was used on 21 of 21 materialised papers. Doc build: 0.21 ms mean, 0.41 ms max. Building from raw records takes 0.38 ms; the old frame-join fallback took 0.86 ms mean and 1.29 ms max. A paper with only `text` materialised builds in 0.26 ms **(M)**.
- The six modules on the largest paper, materialised, inside one run context (`run_modules`): **1 Doc build** in 33.7 ms. A raw paper in the same run: 1 build in 36.2 ms **(M)**.
- The same six as separate bare `module_run` calls, outside any run context: 6 builds in 64.9 ms **(M)**. This follows the trust rule, since an edited frame cannot be detected cheaply. But every call then also loses the Doc's cached search results. Users who call modules one at a time on a materialised paper pay about twice as much.
- `diff_text_frame`'s projection slices now include materialised and text-only-materialised papers: 384 cases with 0 differences. `_text_frame` cases: 471 with 0 differences **(M)**.

### detect_many: index or scan? **The index returns, for detect_many**

The codebook scan uses the `scales` (833) and `tasks` (791) dictionaries: 1,624 PCRE patterns, case ignored. It ran on the 94,362-character paper. All three variants gave the same answers on both large papers (5 hits each) **(M)**.

| Variant | best | median | cold (first call in a fresh process) |
|---|---:|---:|---:|
| today's scan (CORE-0: one-clause prefilter) | 213.5 ms | 246.8 ms | 434.9 ms |
| `detect_many`, literal scan | 131.2 ms | 152.1 ms | 331.0 ms |
| `detect_many`, word index | **90.9 ms** | **116.4 ms** | **274.9 ms** |

- The index is 31% faster at best and 23% at the median, well over the 10% bar.
- In both variants about 72 ms goes to the 307 candidates that still run the regex. Literal lookups take 57 ms with the scan and 21 ms with the index (1.8 ms of that is building the index). There are 2,040 distinct pieces, and the index's vocabulary is 26,839 characters against the 94,362-character text **(M)**.
- Both variants meet DATA-b's "codebook scan of the 94K paper ≤ 0.5 s" target.

For the Doc's per-row prefilter (not part of the brief, measured with the same switch), the six modules in a shared run took 17.3 ms with the index and 19.2 ms with the scan, over 3 runs: the index is 9-11% faster. The four spike-1 modules alone: 13.4 ms against 14.4 ms (7%) **(M)**. power and coi_check_oi on their own are faster with the scan, because their grouped Docs always scan. This is at the bar, not over it. So the Doc default can stay "scan" as designed (this spike tree defaults to the index), while `WordIndex` (80 lines) stays in the tree for `detect_many`. W1's 20-module chain decides the Doc default.

### Boundary floor: per-output breakdown, and one cut

Each module ran on a fresh paper in one run context, with its Doc built first. Time is charged to the outermost boundary function running, so nothing counts twice. The table gives the mean per paper in ms over the 21 papers **(M)**:

| Module | total | search | **floor** | assemble | rows (sentence/hits table) | stack | report blocks | other frames | logic + overhead |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ethics_check | 5.78 | 2.20 | **2.80** | 0.51 | 1.35 | 0.82 | 0.00 | 0.12 | 0.78 |
| open_practices | 5.05 | 2.45 | **2.25** | 0.96 | 1.15 | – | – | 0.14 | 0.34 |
| stat_p_exact | 2.96 | 0.35 | **1.71** | 0.63 | 0.41 | – | 0.05 | 0.62 | 0.90 |
| ref_consistency | 2.38 | 0.01 | **1.57** | 0.59 | – | – | 0.13 | 0.85 | 0.81 |
| power | 4.63 | 2.64 | **1.51** | 0.57 | 0.46 | – | 0.08 | 0.40 | 0.48 |
| coi_check_oi | 3.38 | 1.88 | **1.23** | 0.55 | 0.41 | – | 0.05 | 0.22 | 0.26 |

- **The floor is 1.2-2.8 ms per output on this tree, already under CORE-1e's 3 ms target** (spike 1 measured 6.5-7.4 ms on an older base).
- `Schema.frame` and `Summary.frame` are never called by these six. Summaries go through the dict merge inside `_assemble`.
- `_assemble` costs 0.5-1.0 ms in every module. 62% of it was `_merge_summary` inserting one column at a time; each insert re-reads pandas options (about 100,000 regex calls over 315 outputs) and re-blocks the frame.
- **The cut**: `_merge_summary` builds the frame in one construction. The old loop stays for duplicate column or index labels. Results: open_practices `_assemble` 0.96 → 0.50 ms (floor 2.25 → 1.50), ethics_check 0.51 → 0.38 ms (floor 2.80 → 2.32) **(M)**. The other four modules still return a DataFrame summary and go through pandas `merge` (0.55-0.63 ms). They gain the same once they return a `Summary`, which is CORE-1e's dict-merge work. All A/B elements and chains stay equal with the cut, and `tests/modsys` passes.
- Next cut, not built: ethics_check builds two sentence tables and stacks them (1.35 + 0.82 ms). One projection carrying both flags would save about 1 ms.

## Spike-1 checks re-run (regressions against §7.1)

| §7.1 row | Now **(M)** | Change |
|---|---|---|
| 1 I1, I2 | I1 0 mismatches in 46,816 checks (bank rebuilt from the matrix: 266 triples); I2 0 violations in 61,635 matches | none |
| 2 Façade suites | `_text_frame` 471 + 384 slices, `paper_id` 233, `text_expand` 1,916, `text_search` 3,866 (main, separate process): 0 differences | none (more cases) |
| 4 Module A/B | 6 modules, 864 elements + 105 chains, 0 differences | none (2 modules added) |
| 7 Performance, shared (CPU ms/paper, old → new) | ethics 42.5 → 5.0, open_practices 47.8 → 2.4, stat_p_exact 10.9 → 2.4, ref_consistency 13.3 → 2.4, power 31.9 → 3.2, coi_check_oi 9.5 → 1.6; Doc 0.49; module_run overhead 0.11 | all within budget (the old side is also faster than in spike 1: CORE-0) |
| 8 Counters | 1 Doc build per paper; 4 group builds per paper (power 3, coi 1); 0 regex compiles on a second pass; materialised: 1 build per run | the materialised row now passes |
| 9 Safety | `tests/foundation/test_mutation_guard.py`: 2 failures (run_session misses an in-place edit) | **regression against main**, present since the spike-1 carry (c3d4e123); see design changes |
| 10 Memory | Doc with its cached facets: 0.52 MB mean, 1.52 MB max | up from 0.37 MB (group views and more cached facets) |
| pytest (text_extract, foundation, mod_power, mod_coi, mod_ethics, mod_urls_open, mod_p_values, mod_funding, io, grobid12, bibr12, modsys, stats; `-n 8`) | 4,029 passed, 5 failed, 23 skipped | the 5: the 2 above, and 3 structural tests (settings read outside `metacheck._env`; `core` importing `text.search` and `text.json_expand`) that already fail at the spike-1 carry |

Not re-run: the whole accuracy matrix and the 8-area parity run (rows 3, 5, 6). They are outside SPIKE-2's re-run list.

## CORE-1b re-estimate: **5.5 days** (planned 5)

Why it does not drop:
- SPIKE-2 delivered prototypes, not production code: a mode switch in the reader, scripts, and an unused deferral path.
- The full `--strict` parity run and the review rounds are the same size as before.

Why it does not grow more:
- The riskiest parts (groups and V5, the `.tolist()` backing, `eq` from the Doc, the materialised-paper counters) now exist and pass their differentials.
- The deletion of `_paste_groups`, `_semi_join` and `_section_headers` is unblocked.

What adds about half a day:
- **Hits needs more state than the design describes.** It carries the call's searched rows (the universe), their table order, each row's pattern rank, and the table to return when nothing matches. These carry R's rules through a chain: grouped modes join all searched rows, groups follow the input table's order, and pattern lists are pattern-major. This needs a clear API and unit tests: +0.25 day.
- **`diff_chains` and its mutants become a CI gate** (a property test in `tests/core` with a fixed seed budget): +0.25 day.

Already in the plan, now confirmed:
- Trust moves from `run_session()` to internal runners. This fixes the mutation-guard regression.
- `core` stops importing `text.*`: the `.tolist()` backing replaces the `_legacy_text_frame` fallback, and `_values.as_float` replaces `json_expand`.
- The spike's settings get registered in `metacheck._env`.

## Design changes

1. **Hits state** (above): ARCHITECTURE §2 should describe universe, order, pattern rank and empty template as part of a Hits.
2. **`eq` stays eager in CORE-1b.** It is computed from the read-time Doc, which is kept. Lazy `eq` is a separate decision, because it moves a warning.
3. **`detect_many` uses the word index.** The Doc keeps the scan by default; W1 re-measures, since the gain there sits at the 10% bar.
4. **The generative chain test is a CORE-1b gate.** The module A/B on real papers missed half of the deliberate grouped-mode breaks.
5. **`.without(PatternSet)` after a grouped call** has no core expression for R's result. R intersects the per-pattern grouped tables. The façade handles it (420 cases equal), so the core API should raise rather than guess, and modules that need it use the façade.
6. **Bare `module_run` on materialised papers** rebuilds the Doc per call and costs about 2x. This is the trust rule working as designed. Documenting "use `run_modules` or a session" is enough for now.
7. **Boundary floor**: the target of ≤ 3 ms per output is already met on these six modules. CORE-1e keeps the dict summary merge, with the one-construction cut, and moves the remaining modules from DataFrame summaries to `Summary`.

## Decision needed

**Decided 2026-10-03: option a**, as designed with the changes above; the two small decisions keep their defaults until CORE-1b starts.

**Recommendation: go ahead as designed, with the changes above.** Plan CORE-1b at 5.5 days. V5 holds against main's own code, power and coi_check_oi are A/B-equal, and the fallback (keeping the pandas grouped path behind the façade) is not needed.

| Option | What it means | Days |
|---|---|---:|
| **a. As designed, with the changes (recommended)** | CORE-1b ports SPIKE-2's groups, `.tolist()` backing, read-time Doc and eager `eq`; deletes the grouped pandas helpers; adds the chain gate | 5.5 |
| b. As designed, no changes | Same, without the chain gate and the Hits API work; the grouped-mode rules then rest on the module A/B, which missed half the breaks | 5 |
| c. Fallback | Keep `_paste_groups`/`_semi_join`/`_section_headers` behind the façade; power migrates on them later | 5, plus a later migration |

Separate small decisions, which can wait until CORE-1b starts:
- Lazy `eq` (moves the "comparator left out" warning to first use; read time -7.5 ms per paper when `eq` is unused). Default: no.
- The Doc's literal prefilter default. Default: scan, re-measured in W1.
