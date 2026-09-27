# Pytacheck architecture: an indexed-document core, and the rewrite onto it

**Status.** Revision 3 (2026-09-26), a structural re-plan on top of revision 2 (the spike, its review, and the baselines re-measured after CORE-0, §5.4). Revision 3 adopts two companion proposals and re-plans around them (§0, "Revision 3"):
- [FIDELITY.md](FIDELITY.md): results locked, presentation free. It replaces F1-F3 and amends F5.
- [ECOSYSTEM.md](ECOSYSTEM.md): validation status, presets, the module store as a marketplace, and porting R modules.

Amended on 2026-09-27 by [REPO_FETCH.md](REPO_FETCH.md): batched repository fetches, a cache store across runs, and OSF's rate limits. It adds packages FETCH and CACHE, grows BATCH-a and LINKS, and adds decisions 22 and 23 (§0, §2.6, §2.8, §3.2, §4.3 to §4.6, §5.2, §5.3, §6 and Appendix A).

Carried over from revision 2:
- It replaces the paused right-sizing lanes 3-7. Every de-emulation goal and every acceptance item of those lanes has a home in §3.3; §3.5 adds what relaxed fidelity makes possible.
- It absorbs `perf/batch/BATCH_DESIGN.md`; §2.8 lists only what changes.
- Lane 2 (`_r/regex.py`, the R-dialect pattern compiler) stays the only pattern compiler.
- Nothing in `/home/user/pytacheck` was edited, apart from CORE-0 (§4.3), which has now landed.

**Evidence markers.** `scratchpad/` means `/tmp/claude-0/-home-user-pytacheck/7a7b1f52-e1d1-55eb-8a56-a5f0943689fa/scratchpad`.
- **(M)**: measured before the spike (sources in Appendix A).
- **(S)**: measured by the spike: `arch/spike/results.json`, `arch/spike/out/*`, and the tree `arch/wt-spike` (commit bcf5429 on base fd5e6f3), at a load average of 1.1-2.6 on 4 cores.
- **(R)**: measured by the spike's reviewer (`arch/review_*.py`, reproductions quoted in Appendix B).
- **(C)**: measured after CORE-0, at commit 23704aec (accuracy fingerprint a312423fa7, the same as before CORE-0). The files are in a later session's scratchpad, `/tmp/claude-0/-home-user-pytacheck/b01f2e9f-255f-5b7e-9d0d-70c9d055b185/scratchpad/perf/` (Appendix A).
- **(E)**: an estimate, always given with its basis.
- **(M3)**: measured for revision 3 at f559c1be. The notes and scripts are in this session's scratchpad, `/tmp/claude-0/-home-user-pytacheck/b01f2e9f-255f-5b7e-9d0d-70c9d055b185/scratchpad/research/` (`results/structure.md`, `results/emul-{paper,repo,services}.json`, `idioms.py`, `anatomy.py`, `fnsizes.py`). The web spike's files and report are in the same scratchpad, under `gradio-spike/` (`REPORT.md`).
- The spike report (`arch/SPIKE_REPORT.md`, planned as `arch/spike/REPORT.md`) was never written. Every spike figure here comes from `results.json` and the tree.
- Absolute times are ±10-20% on this shared VM. Ratios measured within one run are reliable.

---

## 0. Summary

### Why pytacheck is slow

Module work runs only 2.3x faster than R **(M)**, because pytacheck replays R's pipeline step by step in pandas. For the 20 offline text modules, one paper costs **1,165 ms (M)**: 304 `text_search` calls (266 of them self-recursion, one per pattern), 290 rebuilds of the text ⋈ section table, 74 `paper_table` calls, 56 `bind_rows` calls and 64 merges. Matching itself is 2-9% of that. The repository chain opens 23 files 96 times, parses one CSV 3 times and one workbook 5-6 times **(M)**.

**After CORE-0 (C).** CORE-0's patches alone halved the accuracy run, from 34.7 s to 16.9 s (5 interleaved A/B runs each, 34.7-36.4 s against 16.9-17.6 s), with every output byte-identical. The rest of the profile is flat. pandas object construction is 54% of self time, from 21,000 `Series`, 4,000 `DataFrame`, 1,072 merges and 13,800 `.loc`/`.iloc` reads. `text_search` is still called 817 times, and it rebuilds the text ⋈ section table 428 times. Regex matching is ≈ 7%, and 1,354 patterns are compiled per run. That is the cost the core removes (§5.4).

### What the spike proved, and what it did not

| Claim | Result |
|---|---|
| The engine finds exactly what `grepl` finds | **Yes.** I1: 0 mismatches in 50,336 checks (286 built-in triples × 22 inputs) **(S)**. I2: 0 violations over 61,635 recorded matches **(S)**. Generative fuzz: 0 violations over ≈ 15,100 matches of ≈ 5,700 TRE/PCRE patterns **(R)** |
| The `text_search` façade on the Doc changes nothing for unmigrated code | **Yes.** 4 differential suites, 6,614 cases, 0 differences; full parity, 9,474 cases, identical statuses on base and tree; accuracy, 439 outputs, identical differences **(S)** |
| Modules rewritten on the core reproduce their outputs | **Yes on the gated inputs:** 4 modules × 24 inputs × 6 elements, plus 50 chains, 0 differences **(S)**. **No beyond them:** the reviewer found three output defects the gates missed (ref_consistency pools the papers that share an id; exact duplicate rows are kept; a third chained run overwrites columns) **(R)** |
| The per-module budgets are real | **Partly.** On these four modules shared-mode CPU fell 11.7x, from 427 to 36 ms per paper **(S)**. Two of four missed their budget, because the output boundary costs 3-7 ms per module |
| The code gets smaller | **Less than planned.** The core subset is 1,975 lines (plan: 1,300). Module lines fell 17% overall (ethics_check −33%, stat_p_exact +11%) **(S)**, with R error replays still inside |
| Not built | Paragraph and section groups, the reader-time Doc and lazy `eq` table, `detect_many`, the full run context. Only 1 of the 16 default-preset modules (stat_p_exact) was spiked |

The engine, the Doc and the façade hold. The defects sit in the module layer, the cache lifetime and the oracles. This revision fixes each one in a contract (§2) or a gate (§4), and adds a 2-day **SPIKE-2** (§7.3) that builds what the spike skipped before CORE 1b deletes anything.

### What we build

A core package, `src/pytacheck/core/` (≈ 3,200 lines, ≈ 700 of them moved; §5.3):

1. **`Doc`**: each paper indexed once, as column lists aliased from the reader's records, with lazy casefolded text, cleaned text per chain stage, and paragraph and section groups. The cache is trusted only while the tables are raw, or inside an internal runner.
2. **A pattern engine**: `Pat` and `PatternSet` values, compiled only through lane 2; derived required literals checked with a per-Doc literal scan; `(known, true)` int-bitset partial evaluation; short-circuit pattern sets, never one union alternation. Chains read like the check: `docs.hits(DATA).where(REPOSITORY).where(AVAILABLE)`.
3. **Facets and `RefIndex`**, computed once per Doc: p-values, equations, statcheck rows, URLs, repository links, study roster, live data; references and citations **per Doc**. DOI databases are normalised once per process.
4. **One `RunContext`** per run: the frozen `Settings` snapshot, the output memo for plain papers, run caches with single-flight, repository indexes and counters. One executor serves all runners, and the batch engine runs on it in paper mode.
5. **An explicit output boundary**: a module returns a dict, a DataFrame or a `Result`; `Schema` types the frames; the summary merge runs on dicts. A module called directly still returns a plain `dict`.
6. **Store contract v1, unchanged**: `f(paper, **kw)`, the metacheck façades re-implemented on the core with their R semantics, no new schema, no new enforcement. Revision 3 adds no pack field: status comes only from the team's registry, `validation.json` (§2.11).

### What revision 3 changes

Revision 2 kept R's output shape everywhere (old decision 1a), so every de-emulation had to reproduce R's presentation too. Revision 3 drops that constraint for presentation only.

| Change | What it means | Where |
|---|---|---|
| **Fidelity bands** | Results (lights, flagged rows, statistics, references, values) stay exact. Order, dtypes, number text, whitespace and error text become free. Wording is recorded with one scoped row per change | FIDELITY.md; §1.1 |
| **Checks produce data; the report renders it** | `Result` is the main return type. Report text comes from each check's small `render()` function, which returns typed blocks, not hand-written Quarto markdown. `scroll_table` and `collapse_section` stay as façades that return blocks, and the fence parser moves to compat for v1 string reports. `ModuleOutput` becomes a compat adapter | §2.9 |
| **Enforced dependencies** | `@module` gets `depends=`, which the executor enforces; revision 2's proposed `uses=` is dropped, and `requires=` keeps its meaning (capabilities). The executor runs each dependency once per run from the run memo, which ends nested module runs | §2.7 |
| **R names move to `pytacheck.compat`** | The main entry point becomes `pc.check(paper, preset=…, status=…)`. The 303 R-shaped exports stay importable from compat, and from the top level with a deprecation until 1.0. ECOSYSTEM.md §3.3 uses the same surface | §2.10 |
| **Validation status and presets** | A registry owned by the validation team holds certifications and withdrawals; each module's label (validated, external-validated, experimental, unvalidated, withdrawn) is derived from it, never stored. A preset says what to check, and a status policy says how much evidence to require | ECOSYSTEM.md §2-3 |
| **The store as a marketplace** | Modules live in their authors' repositories, pinned by SHA, with a generated catalog and nightly CI | ECOSYSTEM.md §4 |
| **Porting R modules** | `pytacheck port` translates an R module with the author's recorded consent, and checks the result against R | ECOSYSTEM.md §5 |
| **Further de-emulation** | ≈ 11,000 more lines than revision 2's plan (≈ 36,000 in all across §3.5's sweeps) can go once presentation is free: wholesale library ports (ellmer, ICU, fread, knitr), value models and deparsers | §3.5 |
| **The web app** | The Shiny app is not ported. A Gradio app, the optional extra `pytacheck[app]`, is mounted on the API server; it covers the Shiny app's features in ≈ 215 lines. The app itself makes no third-party calls; the PDF path and the network options send data to GROBID and the lookup services, as its privacy text says | §3.7 |

### Headline targets, re-based on the spike

| | Today **(M)** | Spike **(S)** | After the rewrite **(E)** |
|---|---|---|---|
| 20 offline text modules, CPU per paper | 1,165 ms | 4 modules: 427 → 36 ms | **≈ 255 ms budget (≈ 4.5x)**; ≈ 215 ms if CORE 1e cuts the boundary floor to 3 ms. Basis: §5.1 |
| Accuracy matrix, Python side (439 outputs) | 35.7 s; R 91.6 s. **16.9 s after CORE-0 (C)** | 21.4 s, with only the façade and 4 modules | ≈ 14-15 s after CORE (was ≈ 15-18 s, already reached by CORE-0); ≈ 10-12 s at the end |
| 1,000 offline XML papers, 19 modules | ≈ 12.7 min | – | ≈ 6-7 min at `-j 1`; ≈ 2-2.5 min at `-j 4` on idle cores (scaling unmeasured) |
| `src/pytacheck` | 125,800 lines (M3) | core subset 1,975 lines | **≈ 93,300 (−26%; range 88-97k)**, after adding status, compat, `port/` and the web app. Revision 2's plan, re-sized on the same sweeps, gives ≈ 103,000 (−18%; it claimed ≈ 98,000); relaxed fidelity removes ≈ 11,000 more. One ledger: §5.3 |
| Repository fetches, demo paper | `repo_check` 38.95 s; `data_check` 72.9-76.0 s cold, 35.6 s on a `cache=True` re-run in a new process (REPO_FETCH.md §1) | – | ≈ 12-15 s and ≈ 20-30 s cold; ≈ 2-3 s warm from the cache store (REPO_FETCH.md §7) |
| Work | – | – | **≈ 126 package-days in 36 packages** (116.5 in 34 before REPO_FETCH.md; CORE-0's 1 day is done); **critical path ≈ 27 days**, plus ≈ 4 days of calendar contingency (≈ 31). Revision 2: 78.5 days in 27 packages, critical path 25 (§4.4) |

All hard per-module budgets stay **provisional** until W1 (PATTERNS) lands and re-measures them.

---

## 1. Principles

### 1.1 Fidelity contract

Revision 3 replaces F1-F3 with FIDELITY.md's three bands (decision 1). F4 and F6 are unchanged, and F5 is amended.

**Terminology.** Revision 2's U-entries (R bugs fixed) and D-entries (deliberate differences) become deviation rows in `parity/deviations/<area>.toml` (FIDELITY.md §3), one file per parity area, so non-module cases have a home too. Every case a row matches keeps its own per-case lock entry under that row. Where text carried over from revision 2 says U-entry or D-entry, read "deviation row". `docs/UPSTREAM_ISSUES.md` keeps only the metacheck bugs that touch Band A.

**F1. Results are exact and never traded for speed** (Band A). On realistic inputs these do not change without a deviation row: traffic lights, which rows are flagged, counts, extracted statistics and references, values, the numbers inside summary texts, and whether an output is an error or a value. A prefilter may never drop a true match (invariants I1-I3, §2.3). In a module the registry snapshot labels validated, a Band A change on any tier also needs a validator's sign-off or a re-validation (ECOSYSTEM.md §2.5).

**F2. Modules stay recognisable to upstream**, which keeps the daily upstream sync tractable:
- every R module maps to a named Python check (`porting/modules.toml`; the map can be many-to-one, as for the `_oi` variants);
- pattern lists stay verbatim R-syntax constants, and a test compares each with R's vector;
- traffic-light rules and summary column names are kept.

Internal file mirroring, private-function mirroring and "Port of R/…" docstrings go. The sync compares R's behaviour, not its code (FIDELITY.md §7).

**F3. Presentation is free, and wording is recorded** (Bands B and C).

| Aspect | Rule |
|---|---|
| Row order, column order, extra columns, dtypes, NA spelling, list-column shapes, number text, whitespace, error text | **Band B.** Removed by the canonicaliser before any comparison; nothing is recorded. The permanent façades keep order and NA spelling (`ordered`, `na_strict` in `canon.toml`), because their contract defines them |
| Column names | An API. A rename is Band C with a deprecation for one minor release |
| Wording of summary text, report text and guidance | **Band C.** One scoped row in `parity/deviations/<area>.toml`; mandatory for validated modules |
| R bugs, including R errors on valid input; deliberate improvements | A deviation row (`what = "value"` or `"light"`), with a sign-off in validated modules |
| Output order | Deterministic, never set or hash order (H0-19). The `text_search` façade keeps pattern-major order, because its contract defines it |

**F4. Nothing is invented.** The core only selects and reshapes what is in the paper and its materials. LLM-derived fields stay labelled; LLM quotes are checked against their source and flagged, never changed (decision 4). No new external data sources.

**F5. Oracles are independent snapshots** (changed; Review B-4). Before any change lands, package SNAP records the canonical outputs of every module on G5's inputs, and the outputs of the four differential suites, as golden JSON. G5 and the suites compare against those files **through the canonicaliser** (revision 3; FIDELITY.md §4). The snapshots stay raw, so a Band B change still shows up as a raw diff in review, and they change only through reviewed deviation rows. There are no frozen legacy module copies: the spike's copies imported live helpers (`_r.frames`, `_r.regex.grepl`, `papers.tables`) that CORE rewrites, so a core bug would have been on both sides.

**F6. Repeated `paper_id`s have one rule** (new; decision 6). They are resolved once, at the `PaperList`/`Docs` boundary, and no module carries its own rule.

### 1.2 Performance contract

**P1. Work is bounded by deterministic counters, as a hard gate** (collected only with `PYTACHECK_COUNTERS=1`): Doc builds ≤ 1 per paper per run, on raw **and materialised** papers and on mixed chains; 0 `text_search` recursions and 0 `paper_table` calls in migrated modules; 0 regex compiles on a warm run; on the traced repository chain, file opens ≤ the number of distinct files and one parse per file and format.

**P2. Timings are A/B ratios on one machine.** `parity bench` runs interleaved rounds and takes the minimum of 5. A module more than 15% slower than the base branch fails the PR. Absolute budgets (§5.1) are soft in PRs and hard in the nightly idle-runner job once W1 has re-measured them.

**P3. No work is repeated at any scope**: per paper generation (Doc, masks, facets, RefIndex), per run (HTTP responses, repository files, plain-paper module outputs), per process (compiled patterns, literal CNFs, DOI databases, host limiters).

**P4. Measurements over proxies.** No union alternation (2.5-4.5x slower, **M**); no per-sentence prefilter (140.9 against 86.0 ms at document level, **M**); no `re` fallback for "simple" patterns; no new native dependency on the text path; no machinery without a measured payoff (Review B-13).

### 1.3 Pythonic contract

**Y1. Types, not R idioms.** Frozen dataclasses for records (`Ref`, `Citation`, `RepoLink`, `FileEntry`, `ColumnProfile`); `Literal` types; `pytacheck._values` instead of `_is_na`/`_chr`/`_dollar` copies; no dplyr join, pivot or factor emulation; no R value models (jsonlite, deparse, long-double parsing). **No R or dplyr error text or type-rule replay in new code** (Review B-15): parity compares errors by presence (`$catch`), and where R crashes on valid input, pytacheck returns a result under a U-entry.

**Y2. Every R semantic that is kept lives in exactly one place**, and revision 3 keeps fewer:
- `_r/` holds only the TRE/PCRE dialects (`_r/regex.py`), the `.rds` reader and the display collation for lists users see sorted;
- cleaning and chain-stage text live in `core/doc.py`;
- `text_search` row semantics live in the façade;
- R's number text lives in `_r.format_num`, used only for statout cell text and LLM prompts (§3.4); the report renderer formats result floats itself;
- module contracts live in `core/output.py`, and repeated-id resolution in `papers/ids.py`.

**Y3. pandas at the edge.** DataFrames exist for users: module outputs and extras, the façades, report tables, bundled datasets, and data files (lane 3's `Table`). The Doc, the engine, reference joins and module intermediates use lists, dicts and int bitsets. Where pandas is *imported* is not policed: `papers.model` imports it anyway **(M)**, so a lazy-import rule would buy nothing (Review B-20).

**Y4. Readable modules.** A store author tests a module without a paper file: `module_run(document("… approved by the IRB …"), "ethics_check")`.

**Y5. Enforced** by mypy strict on `core/**` and `pytacheck/doc`, ruff, the AST layering lint (G6), and an author-surface snapshot of `pytacheck.doc`.

---

## 2. The core

### 2.1 Layout and layering

| File | Contents | Lines (S → E) |
|---|---|---:|
| `core/doc.py` | `Doc`, `Docs`, groups, the trust rule, lazy fields, the literal scan, projection; the `.tolist()` backing for materialised papers | 702 → ~950 |
| `core/patterns.py` | `Pat`, `PatternSet`, `patterns()`, `detect_many()` | 330 → ~380 |
| `_r/regex.py` (additions) | `detector()`, `required_literals()`; lane 2's translator unchanged | +~170 |
| `core/hits.py` | `Hits`: set operations, chains, `paragraphs()`, `sections()`, order, statements | 184 → ~300 |
| `core/facets.py` | shared extractors (§2.4), ≈ 260 lines moved from `text/extract.py` | 115 → ~420 |
| `core/refs.py` | `RefIndex` per Doc, `Ref`, `Citation`, DOI dictionaries | 227 → ~260 |
| `core/fuzzy.py` | agrep, moved unchanged from `modules/coi_check` | ~130 moved |
| `core/run.py` | `Settings`, `RunContext`, `RunCache`, trusted scopes, `execute`, counters | 118 → ~420 |
| `core/output.py` | `Result`, `Schema`, `Summary`, `sentence_table`, `stack`, the dict summary merge | 173 → ~280 |
| `core/errors.py`, `core/warm.py` | error classes (§2.12); forkserver preload | ~80 |
| `pytacheck/doc/` | the author API and `testing.document()` | 40 → ~90 |
| **Total** | | **1,975 → ≈ 3,200 (≈ 2,500 net new plus ≈ 700 moved; §5.3)** |

Dropped from revision 1: `core/index.py` (the word index missed its own 10% bar, Review B-13) and `core/declarative.py` (YAML modules deferred, decision 2).

**Layering (G6, an AST lint):**
- `core/**` imports only `_r`, `_values`, `_json`, `papers.model`, `papers.schema` and `papers.ids`. It never imports `text.*`, `modules`, `report`, `api`, `cli`, `archives`, `datacheck` or `codecheck`. The spike broke this twice (`core/doc.py` → `text.search._legacy_text_frame`; `core/facets.py` → `text.json_expand.as_numeric`); CORE 1b moves the frame builder into the Doc's `.tolist()` backing and uses `_values.as_float`.
- The façades import the core, never the reverse.
- **A module that imports `pytacheck.doc` counts as migrated.** G6 then bans `text.search`, `papers.tables`, `_r.frames` and `_r.regex.grepl` in it. The rule is derived from the imports, so no package edits a shared list (Review B-14).

### 2.2 Data model: `Doc` and `Docs`

A **`Doc`** is one paper, indexed, read-only once built.

| Part | Contents | Built | Cost |
|---|---|---|---|
| identity | `paper_id` (resolved, §F6), `n` rows in text-table order | eager | – |
| columns | `text` (stage-1 field), `text_id`, `paragraph_id`, `section_id`, `sec` (row → section, a dict join within this paper), every other column kept for projection | eager | build 1.5 ms/paper shared; column join 1.4 ms **(S)** |
| selections | `all`, `body` (`section_type != "references"`) as int bitsets | eager | – |
| `fold` | `_r.regex.casefold` per row | lazy | 0.70 ms **(S)** |
| stage-k text | cleaning (TRE `\s+` → " ", then " , " → ", ") applied k times, with an ASCII fast path | lazy | clean 1.07 ms **(S)** |
| `groups(level)` | paragraph, section, header and paper views with R's keys and first-appearance order; joined text (" " within a paragraph, "\n\n" between the paragraphs of a section), cleaned after joining; the U80 header rule | lazy | **SPIKE-2** |
| masks, facets, `RefIndex` | §2.3-2.5 | on demand | – |
| `rows(idx, stage)` | output projection from the hit rows only, dtypes decided once per Doc with `papers/schema.py`'s coercion | on demand | 1.49 ms **(S)** |

**Constructors.** `Doc.of(paper)` uses the raw records when no table was materialised (21 of 21 corpus papers, **M**), otherwise one `.tolist()` per column. The spike instead rebuilt a legacy pandas frame for materialised papers, so a user's single access to `p.text` raised a 4-module run from 1 Doc build and 37 ms to 4 builds and 65 ms **(R)**. `Doc.from_records` serves the reader (SPIKE-2 measures `read(xml)`), `Doc.from_frame` DataFrames given to the façade, and `Doc.from_strings` character vectors.

**Invariants**, pinned before the façades switch:

| # | Invariant | Pinned by |
|---|---|---|
| V1 | Row order is the text table's order | `diff_text_frame`, 471 cases: 0 differences **(S)** |
| V2 | `body` is exactly `text_search`'s reference exclusion | `diff_text_search`, 3,866 cases: 0 differences **(S)** |
| V3 | Stage-k text is cleaning applied k times | `diff_text_search` |
| V4 | Projection equals `_text_frame(p).iloc[idx]`: columns, order, dtypes | 471 cases + 128 slices: 0 differences **(S)** |
| V5 | Groups have `_GROUPS`' keys, joins and cleaning | `diff_text_search` return-mode cases: **not yet run (SPIKE-2)** |
| V6 | A Doc never mutates its paper | mutation guard: 14 checks, 0 failed **(S)** |
| V7 | Exact duplicate rows within one Doc give one output row, as R's `unique(ft_match_all)` (`text_search.R:208`) does | a duplicate-row fixture in `diff_text_search` and G5 (Review B-10) |

The spike deduplicated only across Docs sharing an id, so a paper with one duplicated body row gave 12 ethics rows where legacy gives 11 **(R)**. Each Doc now precomputes whether its row keys are unique; if not, `Hits.ordered` deduplicates on the row key.

**Cache and trust rule** (revised; Review B-9, B-11).
- A Doc lives in `Paper._derived`, a new slot dropped by pickling, `copy()` and `deepcopy()`.
- **Key:** the paper's generation plus the identity of each source table, or a *raw* marker for tables never materialised.
- **Reused only when the key matches and** (1) every source table is still raw records (no DataFrame was handed out, so none can have been edited), or (2) the Doc was built inside the current **trusted scope**. Only internal runners open trusted scopes: `report()`, `run_modules`, batch workers, `execute` chains and the harness. They freeze their inputs by contract.
- **The public `run_session()` stays memo-only and grants no Doc trust.** In the spike it granted trust, and an in-place edit inside a session then made `text_search` return 0 rows where base returns 1 **(R)**. Fingerprints cannot replace the rule, because an in-place `.loc` edit keeps the identity and length of every frame and column.
- **`_derived` holds an opaque integer token** of the scope, never the `RunContext`. The spike stored the context itself, which would keep a run's memo, `RunCache` (≤ 256 MB) and repository bytes alive for as long as any of its papers lives. Measured with outputs kept, 21 papers × 19 modules retained 28.3 MB against 21.9 MB on base, before the memo and caches joined the context **(R)**.
- A rebuild on a materialised paper costs one `.tolist()` per column, ≈ 1.5-3 ms **(E from S; SPIKE-2 measures it)**.
- **CI mutation check:** with `PYTACHECK_CHECK_MUTATION=1`, a trusted scope fingerprints every Doc it built at build and at exit, and raises `StaleDocumentError` on a change.

**Memory.** Doc mean 0.37 MB, max 1.02 MB per paper **(S)**. Docs are never pickled.

**`Docs`**, the module input, is a tuple of Docs. `Docs.of(paper)`: a `Paper` gives one Doc, a `PaperList` gives its Docs in list order, and anything else raises `TypeError("paper must be a paper or paperlist object.")`. `paper_ids()` is R's `paper_id()` rule, taken after F6's resolution. `hits(ps, *, include_refs=False, header=False) -> Hits` matches stage 1 on raw text.

**Repeated ids (F6, decision 6).** `papers/ids.py:resolve(list)` gives every paper in a list a unique id: the first keeps its id, later repeats become `id~2`, `id~3`, … with one `PytacheckWarning`. `Docs.of`, the summary base in `_assemble`, the façades and `report(list)` all use it, so every module has one defined behaviour. It replaces five behaviours the spike showed on the same list **(S, R)**: ethics_check and open_practices dropped identical rows; stat_p_exact's text went from "out of 699" to "out of 445"; ref_consistency pooled the papers; the summary join repeated rows (29 on the 21-paper list); `report(list)` planned its own suffix. It is one U-entry, and the distinct-papers fixture (§4.2 H0-12) pins it.

### 2.3 Pattern engine

```python
Dialect = Literal["tre", "pcre", "fixed"]

@dataclass(frozen=True, slots=True)
class Pat:                        # value-equal: equal patterns share masks across modules
    src: str
    dialect: Dialect = "tre"
    icase: bool = True            # text_search's ignore_case = TRUE
    @classmethod
    def from_r(cls, p, *, perl=False, fixed=False, ignore_case=True) -> Pat: ...

@dataclass(frozen=True, slots=True)
class PatternSet:                 # ordered any-of; never compiled into one alternation
    name: str
    pats: tuple[Pat, ...]
```

**Compilation goes only through lane 2.** Detection uses `_r.regex.detector()` (`posix=False`, the same truth value as `grepl`); TRE match extents use `compile_r(posix=True)` for leftmost-longest. A per-process LRU (8,192) caches compiled patterns. Patterns compile lazily on their first candidate row: 0 compiles on a warm second pass **(S)**; the lazy codebook compile removes 843 compiles (0.43 s, **M**). Eager validation remains where errors must surface: a test compiles every built-in set, and the façade validates user patterns.

**Required literals.** `_r.regex.required_literals(src, perl, icase)` returns a CNF: every match contains, for each clause, at least one of its pieces. It generalises `_funding._required` to TRE: zero-width anchors are skipped; brackets, classes, escapes, optional or repeated atoms, lookarounds and inline flags end a literal run; an alternation contributes the OR of its branches' best clauses; anything not understood contributes nothing. Pieces are casefolded, split on whitespace and commas, ASCII, ≥ 3 characters; a clause with any other piece is dropped. 244 of 286 built-in triples get literals **(S)**. `grepl` keeps lane 2's replay-pinned prefilter unchanged. The CNF replaces eight hand-kept mechanisms (`_funding._required`, `_ETHICS_ANY`, `extract._LIVE_ANY`, `_REPO_ANY`, `_FIRST_STAGE_ANY`, `extract._search_table`, `dataverse._search_frame`, `dataone._scan_links`) and their sre_parse proof tests.

**Literal scan** (the word index is dropped). `Doc.literal_rows(piece)` is one `str.find` pass over `fold`, memoised per piece per Doc. The spike measured the 4-module run at 41.3 ms with the scan against 37.9 ms with a word index (8%, below the plan's own 10% bar) **(S)**. The index returns only if `detect_many` on the 94K-character paper, or the 20-module chain after W1, measures ≥ 10% faster with it. The scan is sound because casefolding works per code point, and pieces contain no whitespace or comma, so they survive cleaning and lie inside one folded row. Case-sensitive patterns use folded literals on folded text, which is a necessary condition.

**Evaluation.** `Doc.mask(p, stage, within)` keeps `(known, true)` bitsets per `(Pat, stage)`. It evaluates only rows that are wanted, not yet known and candidates, and publishes the new state in one assignment. `any(ps, …)` evaluates each pattern only on rows no earlier pattern matched; with `header=True` it matches each distinct header once. Equal `Pat` values share state across modules. **(M)** on ethics_check + open_practices detection: 370.8 ms/paper today, 12.1-12.6 ms with partial evaluation and int bitsets.

**`Hits`**, the author-facing chain object:

| metacheck construct | Core expression |
|---|---|
| `text_search(paper, P)`; `include_refs`; `search_header` | `docs.hits(P)`; `include_refs=True`; `header=True` |
| a list of patterns | a `PatternSet` |
| `text_search(text_search(paper, A), B)` | `docs.hits(A).where(B)`: each `.where` matches the next stage's cleaned text |
| `exclude = TRUE` | `.without(X)` |
| `return = "paragraph"` then B; `return = "section"` | `.paragraphs().where(B)`; `.sections()` (**SPIKE-2**) |
| `return = "match"` | `doc.find(p)`, leftmost-longest for TRE |
| `arrange(factor(paper_id, ids), text_id)` + `unique()` | `hits.ordered()` (V7) |
| unique cleaned texts per paper | `hits.statements()` |
| set algebra | `&`, `\|`, `-`, `bool`, `len`, iteration |

**One text, many patterns** (codebook: 833 + 791 patterns per paper): `detect_many(pats, text)` folds once, filters by pieces and compiles only candidates. With CORE-0's fold-once and lazy-compile patches the 94K-character scan takes 0.2-0.3 s instead of 16.0 s **(M)**, without an index.

**Thread safety** (corrected; Review B-19). Every lazy field, including the stage-text list, is computed locally and published with one assignment after re-checking; `_stages` is a tuple replaced whole. The spike appended to a shared list, so two threads could make `field(2)` return stage-1 text (`'a , , b'` → `'a, , b'` → `'a,, b'`). A threaded test pins this. A lock is added only for a free-threaded build.

**CI invariants.**
- **I1:** every built-in `(pattern, icase, perl)` triple gives the same mask as `grepl` on the corpus and the demo paper. 0 mismatches in 50,336 checks **(S)**.
- **I2:** over the recorded replay (`tests/foundation/data/regex_calls.json.xz`), every clause of `required_literals` has a piece in `casefold(string)`. 0 violations over 61,635 matches **(S)**.
- **I3** (new; Review B-22): the reviewer's generative fuzz (`arch/review_fuzz2.py`: groups, alternations, quantifiers, `\b`, POSIX classes, Kelvin sign, long s, dotted I, sharp s, ligatures, NBSP, `\x1c`/`\x85`) becomes a hypothesis property test in `tests/core`, with a fixed seed budget in PR CI and a larger nightly run. 0 violations over ≈ 15,100 matches today **(R)**.
- **Kill switch:** `PYTACHECK_LITERALS=off` disables the prefilter, so a user who suspects a miss can rule it out.

### 2.4 Facets

Facets are memoised per Doc. The public `extract_*` functions become façades over them.

| Facet | Serves | Replaces |
|---|---|---|
| `p_values()` (built in the spike) | the 3 p-value modules | `extract_p_values`, run 3 times per paper in a chain |
| `equations()` | stat_effect_size, `extract_tests` → statout → reproducibility_check, the lazy `eq` table | `extract_eq`: 35% of `read(xml)` **(M)**, run twice |
| `digits`, `statcheck()` | stat_check, stat_effect_size | `stats()` + a join |
| `urls()` | all_urls | – |
| `links()`: `RepoLink(host, kind, url, row)` | repo_check (paper side), prereg_check, open_practices, the `RepoIndex` key | 14 detectors at 205 ms/paper **(M)** |
| `live_data` | ethics_check | `_detect_live_data` |
| `roster()` | data_check, repo_check | 60 `text_search` calls per accuracy run **(M)** |
| `fuzzy(literal, k)` | coi_check | per-call agrep |

**The `eq` table becomes lazy:** the reader registers it as a derived table, computed from `equations()` on first access, write or pickle. SPIKE-2 measures the effect on `read(xml)`.

### 2.5 Reference index

`RefIndex` (`core/refs.py`) is built once **per Doc**. It holds `refs` (`Ref(bib_id, doi, doi_key, printed_doi, text, bib, match)` in `ref_table` order), `by_id`, `by_doi`, and `citations` (bib_id → bitset of citing rows, bibr 12 `xref_type == "bib"` resolved at build).

**Joins are keyed per paper object, never on `paper_id`** (Review B-1). A module iterates `for doc in docs: doc.refs` and puts `paper_id` on its output rows only. The spike's ref_consistency keyed dictionaries on `(paper_id, bib_id)` with `setdefault`. Grobid bib ids (`b0`, `b1`, …) repeat across papers, so two different papers forced to one id were merged: 6 rows instead of 1 + 7, and 4 identical summary rows for 2 papers **(R)**. The gated duplicate-id list hid this, because its repeats are copies of one paper.

**Missing columns.** A bib table without `text_id` or `doi` (bibr 10.2, CERMINE) gives empty maps, and the module returns its normal `na` result (U-entry). It does not replay dplyr's "Join columns in `x` must be present" (Y1).

**The `ref_table()` façade** is `RefIndex.frame()`, snapshot-tested for merge order and the many-to-many result. Today `ref_table` costs 14 ms per call at 4.3 calls per paper **(M)**.

**DOI dictionaries** (RetractionWatch, FLoRA, miscite) are normalised once per process, keyed on path and mtime. Today `retractionwatch()` spends 33 of its 44 ms re-stripping 61k DOIs on every call **(M)**.

### 2.6 Run context, settings and caches

```python
@dataclass(frozen=True)
class Settings:                    # BATCH E3 as one type
    options: Mapping[str, object]  # context-local overlay (L6-1)
    email: str | None; verbose: bool; preset: str | None; allow_local: bool; offline: bool
    llm: LLMSettings; packs_overlay: tuple; cache_dir: str | None; download_dir: str | None
    cwd: str; env: Mapping[str, str]            # masked in run.json
    @classmethod
    def capture(cls) -> Settings: ...
    def apply(self) -> None: ...
    def digest(self) -> str: ...                # the options that change outputs

class RunContext:
    token: int                     # opaque; the only thing a Paper's _derived stores
    settings: Settings
    memo: OutputMemo | None
    trusted: bool                  # internal runners only (§2.2)
    counters: Counter | None
    def cache(self, name) -> RunCache: ...      # BATCH N2: single-flight, errors never stored, LRU 256 MB
    def repo(self, key) -> RepoIndex: ...
    store: CacheStore | None       # REPO_FETCH §5: across runs; off in the parity harness and tests
    def override(self, **settings): ...         # e.g. llm_use=False, scoped; the one override API
```

- `run_session()` stays public with today's documented contract: it memoises repeated module runs and grants no Doc trust. `docs/MODULES.md:636` keeps its warning about in-place edits.
- `module_run` alone opens a transient context: memo off, no trust.
- `local_options` becomes a context-local overlay (L6-1). `_reproducibility`'s process-wide `llm_use(False)` becomes `with current().override(llm_use=False):`.
- A test asserts that `Settings.capture()` covers every setter exported from `pytacheck/__init__.py`.

**Lifetimes.** Per process: compiled patterns, literal CNFs, DOI dictionaries, host limiters, resolved module specs, file modules. Per run: the output memo, run caches, `RepoIndex`es, `Settings`. Per paper generation: Doc, masks, facets, `RefIndex`. Per call: `get_prev_outputs`. Across runs (REPO_FETCH.md §5): the `CacheStore`, which holds only validated, immutable or short-TTL (1-30 d) repository metadata, zip-peek indexes and file blobs, and the request ledger for OSF's and GitHub's windows of a minute or longer.

**Output memo** (revised; Review B-8). The key is the label, the module identity (pack, rev, file sha256), the frozen arguments, `settings.digest()` (including the 4 LLM options memo keys use today) and the paper state (generation plus id; lazily built tables no longer change it, P3-memo-built-tables). **Only plain-paper calls are memoised, as today; chain steps are not.** A chain-prefix key cannot see whether a predecessor succeeded (errors are never cached, BATCH R5), and no measurement shows chain-step hits, since each `report()` step runs once per paper. A test runs a chain whose predecessor fails the second time.

### 2.7 One executor for all runners

`execute(inputs, steps, *, chain=True, on_error="raise"|"fail", failure="provenance"|"report"|"api", each=False)`.

| Runner | Call |
|---|---|
| `module_run` | one step; `raise`; transient context |
| `module_run_each` (BATCH E1) | `each=True`; errors per paper |
| `run_modules` | chained; `fail`; `provenance._failed_output`; trusted scope |
| `report()` / `report_module_run` | chained per paper; `fail`; "This module failed to run"; trusted scope |
| API `/paper/check` | `chain=False`; `fail`; "Error running module", section `"general"` |
| `validate()`, `pack check` | list mode on `test_paper()`s; per input with the mutation fingerprint |
| repro pool, batch workers | inside a trusted `Settings` scope |

**Dependencies are declared and enforced** (revision 3; replaces revision 2's advisory `uses=`). Today six modules silently re-run others: code_check and data_check run repo_check, codebook_check and psychds_check run data_check, reg_check runs prereg_check, and reproducibility_check calls `run_missing` with 13 keyword arguments and a process-wide `llm_use(False)` toggle **(M3)**. Run alone, reproducibility_check therefore runs repo_check three times and data_check twice.
- `@module(depends=("repo_check",))` is a static literal. `requires=` keeps its meaning today (capabilities such as network and LLM). `execute()` builds the `depends=` graph, adds missing dependencies before the modules that need them, runs each (module, forwarded arguments) once per run, and serves repeats from the `RunContext` memo.
- The executor never reorders what the user listed; it only inserts dependencies before their first user. A test pins this.
- No typed artifacts in CORE. A dependency's output reaches its dependent as today, through `extras` (data_check alone has 9 such keys, **M3**). REPO, DATA-b and CODE-b may add internal artifacts (`RepoIndex` first) when they rewrite their modules; extras stay as user outputs.
- The one cycle (data_check reads codebook_check's table while codebook_check runs data_check) is broken in DATA-b, which owns both files.
- Scoped settings replace the `llm_use` toggle: a dependency runs under `RunContext.override(llm_use=False)`, which is context-local and thread-safe.

**Dependency rules** (revision 3):
1. **Arguments.** A dependency runs with its own defaults plus an explicit tuple of forwarded arguments declared by the dependent, for example `depends={"repo_check": ("local_path", "local_only", "cache")}`. The memo key uses the normalised forwarded arguments.
2. **Visibility.** An inserted dependency is hidden: it is not in the report or the summary merge, and is listed only in `run.json`. A module the user also selected is shown as usual.
3. **Status policy.** The policy applies to the modules the user or the preset selected. Dependencies inherit their dependent's check and are not filtered on their own.
4. **Capabilities.** A module's effective network and LLM need is the union of the `requires=` capabilities over its `depends=` closure. The server-safe check (§3.7) covers the whole closure.
5. **`chain=False`** (the API) still resolves dependencies, through the run cache.
- `get_prev_outputs` still returns DataFrames, through compat.
- Expected effect on the reproducibility_check chain (E, from the M trace): 7 nested module runs → 1 run per module, and 18 file opens → ≤ 6. `module_run` = `_resolve` (cached per process) → bind → `_call` → `_assemble(spec, label, raw, input)`. `module_run` overhead is 0.33 ms per call **(S)**.

### 2.8 Batch engine

`BATCH_DESIGN.md` stands (R1-R7, G1-G7, run shape, API, CLI, error isolation, output folder, resume, rejected ideas) except for these changes:

| BATCH item | With this core |
|---|---|
| Layer 1 (F1-F4 fixed costs) | Replaced by Doc, RefIndex, DOI dictionaries and facets; L5-1 to L5-3 dropped |
| N1 host limiter, rolling window | BATCH-a (BL-1), days 6.5-9.5, after phase 0. REPO_FETCH.md §2.2 adds path scopes, several windows per scope, an identity dimension and sliding-window logs, and moves the shared client to HTTP/1.1 (RF-1) |
| N2 `run_cache` | `RunContext.cache` (CORE 1d); HTTP memo wired by BATCH-b (BL-2) |
| R5 "nothing persistent is added in v1"; P3-2, an opt-in SQLite HTTP cache | Superseded in part by REPO_FETCH.md §5 (decision 22): a `CacheStore` in v1, on by default only for validated, immutable or short-TTL (1-30 d) repository metadata and blobs, in files with an optional Redis/Valkey backend (CACHE). Lookups (Crossref, PubPeer, retractions) stay unpersisted, and errors are never stored |
| R6 across processes and runs | The request ledger (CACHE) keeps OSF's and GitHub's windows of a minute or longer across processes and runs; the `1/jobs` rate share stays for shorter windows (decision 23) |
| N3 key dedup, PubPeer chunks, `add_bib_match` groupby | REFS (L7-1 to L7-3); `osf_type` via the run cache in LINKS (L7-5) |
| N4 threads for hosts, Grobid/bibr/RegCheck jobs, causal | REPO (L7-6), SERVICES-b (L7-7), LINKS (RegCheck), REFS (L5-8); per-host work queues across a paper's repositories in FETCH (RF-3) |
| N5 LLM single-flight, atomic writes, breaker | LLM-b |
| E1 `_assemble`/`_call`/`module_run_each`; E3 Settings | CORE 1d |
| E2 `ModuleSpec.batch` + BL-8 corpus mode | Deferred behind a measured gate (decision 3): build only if loop/list is still ≥ 1.5x after W1-W3 (3.45x today, **M**) |
| E4-E6, forkserver preload | BATCH-b; the preload imports pandas and `regex`, compiles the built-in sets and loads the DOI dictionaries |

Workers run `with trusted_scope(settings): for path in chunk: execute([read_one(path)], …)`, and Docs die with the chunk.

### 2.9 Output boundary

**Revision 3: a check produces data, and the report renders it.** Today `ModuleOutput` (module.py:715) is a bag of pre-rendered strings.
- Modules write Quarto markdown with `scroll_table` and `collapse_section`, and `collapse_section` emits `::: {.callout-…}` fences.
- report/render.py (1,238 lines) then parses those fences back to produce HTML and GFM.
- In 13 paper modules, the traffic light, summary text and report code make up 45-55% of the body lines. The wording is spread over 214 `plural` calls and 60 "We found / You described" literals **(M3)**.

The target splits every check into three steps:
- **detect** finds hits on the Doc;
- **evaluate** returns a typed `Result` with rows, a `Summary` and a traffic light;
- **render** is a small Python function per check, `render(result) -> list[Block]`, written with f-strings and the `plural` helper. There are no template files: templates cannot hold the branching and plural logic the texts need.

The report is built from typed blocks (`Paragraph`, `Table`, `Callout`, `Ref`, `Badge`), written straight to HTML, GFM or `.qmd`. `scroll_table` and `collapse_section` return `Table` and `Callout` blocks whose `str()` is today's markdown, so the permanent façades, clinical_trials and v1 modules keep working. A string `report` from a v1 module is wrapped as a `Markdown` block and read by a small fence-to-blocks reader, so **the fence parser moves to compat and serves only v1 string reports**. G7 gains a gate: clinical_trials renders the same HTML on base and after CORE-1e. Wording changes are diffs of `render()`, which are Band C. So there are two report paths, blocks and the v1 reader, not three.

```python
Kind = Literal["string", "Int64", "Float64", "float64", "boolean", "list"]   # r15/r7 removed (Band B)

class Schema:                      # explicit, never derived from annotations
    def __init__(self, **cols: Kind) -> None: ...   # Schema(paper_id="string", n="Int64"); column order = keyword order
    cols: tuple[tuple[str, Kind], ...]              # read-only, set by __init__
    def frame(self, records) -> pd.DataFrame: ...

@dataclass
class Summary:                     # one row per resolved paper id
    paper_ids: Sequence[str]; rows: Mapping[str, Mapping[str, Any]]; schema: Schema

@dataclass
class Result:                      # the one definition; a builder, not a Mapping
    rows: list[Mapping[str, Any]]
    summary: Summary
    light: Light = "info"
    text: str | None = None
    blocks: tuple[Block, ...] = ()
    extras: Mapping[str, Any] = field(default_factory=dict)
    na_replace: Mapping[str, Any] | None = None
    def to_frame(self) -> pd.DataFrame: ...     # built lazily, at the edge
    def as_dict(self) -> dict[str, Any]: ...    # v1 keys and order; Summary framed once and cached

def sentence_rows(hits, /, **flags) -> list[dict]: ...
def stack(*row_lists) -> list[dict]: ...        # concatenates lists
```

**Direct calls return a plain `dict`** (Review B-6). `@module` returns `Callable[..., dict]` for v1 compatibility: the wrapper converts a `Result` with `as_dict()`, and the executor calls the unwrapped function to receive the `Result`. `pc.check` and the `check.result(paper)` attribute give the `Result`. `Schema(**cols)` is the constructor. The spike's `Result` was a read-only `Mapping`: item assignment, `pop`, `copy` and `update` raised, `isinstance(r, dict)` was false, and `r["summary_table"]` was rebuilt on every access, so edits to it were lost **(R)**. A test asserts that every built-in module's direct return value is a mutable `dict`.

**Summary merge on dicts:** a left join on the resolved input ids, with dplyr's suffix rule (**append the suffix again while the name is taken**, giving `.ethics_check.ethics_check`) and U77's `na_replace` scope (Review B-18). The spike overwrote the second run's columns on a third chained run; the merge differential test gains a 3-deep repeated-module chain. With F6 there are no repeated ids, so the `DataFrame.merge` fallback goes.

**The boundary floor is the budget driver.** It is the per-output cost of `Schema.frame`, `sentence_rows` and `to_frame`, `Summary.frame` and `_assemble`. The spike measured ≈ 7.4 ms in ethics_check (5.7 ms of tables, Summary and report, plus 1.7 ms of `_assemble`) and ≈ 6.5 ms in stat_p_exact (≈ 4 ms of frames plus 2.5 ms of merge) **(S)**, against 0.3-0.5 µs for the dict merge alone **(M)**. CORE 1e profiles it on the six SPIKE-2 modules and targets ≤ 3 ms per module **(E)**. §5.1's budgets assume 5 ms until then.

**`Result` is the primary type; `ModuleOutput` becomes a compat adapter** (revision 3).
- `pc.check()` returns a `Report` of `Result`s (`Report.to_html()`, `Report.save()`), with `result.rows`, `result.summary` and `result.to_frame()`. DataFrames are built only there, at the edge. pandas object construction is 54% of self time today **(C)**.
- `ModuleOutput` stays an eager dataclass with its fields, key order, pickling and canonical form, built from a `Result` by `pytacheck.compat`. Lazy properties stay rejected (they break `dataclasses.replace`, `fields` and equality, and move errors past the failure policy).
- A check's direct call still returns a plain mutable `dict` (Review B-6), so contract-v1 store modules and user scripts keep working.
- `Result` values stay floats, and the report renderer formats them. `_r.format_num` (about 9 direct calls in 7 files, plus `as_character`'s numeric path with 265 call sites) stays for statout cell text and LLM prompts, where request bodies stay byte-identical (§3.4). STATS and SERVICES audit `as_character`'s numeric path per package. `Kind` loses `r15` and `r7`.

### 2.10 Module API and façades

**Contract v1 is frozen** (the store, the template, `docs/MODULES.md`, 1,048 `module:` parity cases): `f(paper, **kwargs)` with a `Paper` or `PaperList`; output a dict, a DataFrame or (new) a `Result`; the input not mutated; valid traffic light; a summary with `paper_id`. `@module(...)` takes literal metadata scanned by `packs/scan.py`. It accepts unknown literal keywords with a warning instead of a `TypeError`, and an optional `depends=` (§2.7). `requires=` keeps its capability meaning.

**Revision 3: the main API is `pc.check`, and the R names move to `pytacheck.compat`** (decision 15).
- Today `_EXPORTS` (`__init__.py:26`) has 303 flat names against R's 300, including 25 `data_check_*` names, 15 `code_*` names and about 75 archive-host functions (`*_links`, `*_info`, `*_file_download`, `*_pat`) **(M3)**.
- The new top level is small:
  - `pc.check(paper, preset=…, status=…, settings=…) -> Report`;
  - `pc.Paper`, `pc.read`, `pc.Settings`, `pc.configure(preset=…, status=…)` (replacing `pc.use`);
  - `pc.status(name)` and `pc.status_table()` (ECOSYSTEM.md §3.3, which uses this same surface);
  - `pc.hosts`, one `Host` protocol with `links`, `info` and `download`, in place of the per-host functions. It covers about 50 of the 75 host exports. The other 25 become `Host` methods where they fit the protocol, `pc.hosts.osf.*`-style extras where they are host-specific, or stay in compat only; CORE-1e lists which is which.
- `pytacheck.compat` holds the R-shaped names: `module_run`, `get_prev_outputs`, `paper_table`, the option shims, the `ModuleOutput` adapter and the 303 exports. The top-level names stay as deprecated re-exports until 1.0.
- The parity harness calls compat, so the public-function cases need no change.
- The "Permanent façades" list below moves into compat unchanged. Its contract to store modules does not change.

**Declarative pattern checks in TOML are deferred** (decision 2 (c), revision 3 review). A check that is only patterns, section filters and a traffic-light rule could be a TOML file compiled to `pytacheck.doc`, but nothing needs it yet: the porting translator writes `.py` against `pytacheck.doc` (ECOSYSTEM.md §5.2), and the maintainer's goal is fewer lines. It is built when a store author or the translator needs it, and then becomes the translator's preferred output for pattern-only modules. §2.13's "four module forms" rejection holds: there is one form, a Python check.

**Permanent façades**, with R semantics and DataFrame results, never deleted: `module`, `module_run`, `get_prev_outputs`; `text_search` (pattern-major order, first match wins, `exclude`, every `return_` mode); `paper_table`, `ref_table`, `paper_id`, `text_expand`, `extract_*`, `json_expand`, `stats`; `_r.frames.bind_rows` and `count`; `report.scroll_table`, `collapse_section`; `test_paper`, `demopaper`, `read`; `Paper`/`PaperList` access.

**`text_search` for character vectors with several patterns returns results** (lane 5's U-entries; Review B-15), covering `text_search.vector.multi`, `.multi.one_hit`, `.multi.no_hits`, `.multi.exclude.three`, `.four` and `.three_empty`, and `text_search.demo.multi.exclude.three` and `.four`. Invalid arguments raise Python errors with pytacheck's own messages, compared by presence. The spike's replays of dplyr's "`...` must be empty" and R's "unused argument (…)" are deleted.

**The author API** is `pytacheck.doc`: `Docs`, `Doc`, `Pat`, `PatternSet`, `patterns`, `Hits`, `Result`, `Summary`, `Schema`, `sentence_rows`, `stack`, `plural`, `facets`, and `testing.document(*sentences, section="method", references=(), paper_id="test")`. It stays provisional until 1.0 and is snapshot-tested.

A module on the core (ethics_check; its logic went from 141 to 69 lines, **S**):

```python
ETHICS = patterns(*_ETHICS_WORDS, name="ethics_check.ethics")   # metacheck's 63 patterns, verbatim
SUMMARY = Schema(ethics_approved="boolean", ethics_statements="list",
                 needs_ethics="boolean", live_data_statements="list")

@module(title="Ethics Approval Check", ...)
def ethics_check(paper) -> Result:
    docs = Docs.of(paper)
    ids = docs.paper_ids()
    eth, live = docs.hits(ETHICS).ordered(), docs.hits(facets.LIVE_DATA).ordered()
    e_by, l_by = eth.statements(), live.statements()
    approved, needs = [p in e_by for p in ids], [p in l_by for p in ids]
    n_missing = sum(n and not a for n, a in zip(needs, approved))
    light = "na" if not any(needs) else "green" if n_missing == 0 else "red"
    return Result(rows=stack(sentence_rows(eth, ethics=True), sentence_rows(live, live_data=True)),
                  summary=Summary(ids, {...}, SUMMARY), na_replace={"ethics_approved": False},
                  light=light, text=_text(n_missing, len(ids), any(needs)))

def render(result) -> list[Block]:           # the report part; wording changes are Band C
    ...
```

In revision 3 the report moves from `_report(...)` into `render(result)`, which returns blocks. The strings stay in the module, in one place; the line count changes little.

**LLM grounding** is observation only (decision 4): where an LLM field claims a quote (power's `text`, causal sentences), the core checks that it occurs in its source paragraph after whitespace and case normalisation. On a miss it records a `notes` extra, one `PytacheckWarning` per run and a count in `run.json`, and changes no value.

### 2.11 Store contract and versioning

Today's parsers keep working (`packs/manifest.py:134` copies unknown keys; `packs/stores.py:190-207` ignores extra keys). **No schema bump, and no change here needs one.**
- **Revision 3 adds nothing a pack writes about status.** Packs carry only the self-reported, structured `validation` evidence record already accepted by `@module(validation=…)` (module.py:102-103), never a `status`. An author's own numbers show as "self-reported". The label that policies filter on comes only from the team's registry (`validation.json`), never from the pack (ECOSYSTEM.md §2.3). Store indexes copy the status for display; the snapshot or its refresh wins.
- **`store search` and presets can filter on status** (ECOSYSTEM.md §3). This is the one filtering that revision 2 rejected and revision 3 needs.
- **`module_api` is reserved but not enforced** (Review B-13). The loader reads an optional integer `module_api`, treating absent as 1, and ignores it. Nothing writes it, and `store search` does not filter on it. Enforcement arrives with a v2, if one is ever needed; v1 and v2 would then be supported side by side for at least two minor releases.
- **`requires.pytacheck`** (decision 7; Review B-7). Versions compare on `Version(v).release`, so `0.3.1.dev1` satisfies `>=0.3.1`. `packaging` alone says no, even with `prereleases=True` **(R)**, which would reject every pack on a dev build, including in G7's store CI. At load time a mismatch stays a warning, as at install today (`install.py:160-172`). It is an error only in `pack check` and `store build --check`, together with the static check that packs importing `pytacheck.doc` declare `requires.pytacheck`.
- **Minor additions** (`pytacheck.doc`, `Result`, `depends=`) are gated by `requires.pytacheck`.
- **G7:** pytacheck's CI checks out `pytacheck-modules` and runs `store build . --check`, `pack check packs/*` and the packs' pytest on every core PR. The job that runs pack code has `permissions: {}`, no secrets and no persisted credentials; a token, if needed while the store is private, is set only on the checkout step (ECOSYSTEM.md §4.8). The store's only pack (clinical_trials) gave byte-identical outputs on base and spike across 22 papers and a probe paper **(R)**.

### 2.12 Error model

`RegexError` and `ModuleError` stay. New in `core/errors.py`: `PytacheckError`; `InputError(ValueError)`; `PatternError(InputError)`; `ServiceError` (`RateLimited`, `ServiceUnavailable`; `.service`, `.status`, `.retryable`); `StaleDocumentError`; `PytacheckWarning(UserWarning)` (repeated ids, skipped rows, ungrounded quotes).

1. Missing data is a result, not an error: no bib table, `text_id` or DOI gives `na`; an empty list gives empty typed results.
2. Bugs raise, and the executor wraps them in `ModuleError`; `on_error` decides.
3. Services raise `ServiceError`, which modules turn into `"fail"` or into NA per item; errors are never cached.
4. New code never replays R's error texts (Y1).
5. Warnings go through `warnings.warn` and the log; the batch runner re-emits them in input order.

### 2.13 Considered and rejected

| Idea | Why not (evidence) |
|---|---|
| Word index (D2) | 37.9 against 41.3 ms for plain scans: 8%, below the 10% bar **(S)**; `detect_many` meets its target without it **(M)** |
| Chain-step memoisation | Unsound across predecessor failures; no measured hits (Review B-8) |
| YAML or TOML declarative modules now | Revision 3 considered TOML checks in phase D and **deferred** them (decision 2 (c)): the porting translator writes `.py`, and no store author has asked. Built when one does |
| `module_api` enforcement | Serves a v2 that nothing planned needs (Review B-13). Revision 3 does need `store search` to filter on **status** (§2.11), which is a different field |
| Frozen legacy module copies as the G5 oracle | Not independent: they import helpers that CORE rewrites (Review B-4) |
| Doc trust inside the public `run_session`; fingerprints | Stale `text_search` results after in-place edits **(R)**; `.loc` edits keep identity and length |
| `Result` as a read-only Mapping | Breaks callers that mutate a module's direct return value **(R)** |
| numpy masks in the Doc | 17.3 against 12.6 ms/paper with int partial evaluation **(M)** |
| A pandas text ⋈ section join inside the Doc | 6.7 against 0.08 ms/paper **(M)** |
| `Table` storage in `Paper`, the `Rows` mini-frame, interned `Pat` (D2) | Rewrites every reader to save ≈ 1 ms; grows toward pandas; value equality already shares |
| Lazy `ModuleOutput` properties (D2, D3) | Break `replace`, `fields` and equality; errors escape the failure policy |
| Four module forms, dtypes from annotations, `pack.json` schema 2 (D3) | Too many concepts; a hint edit would change dtypes; schema 2 breaks every installed pytacheck. Revision 3 keeps one form and adds no pack field |
| Union alternation; per-sentence prefilter; `re` for simple patterns | 2.5-4.5x slower; 140.9 against 86.0 ms; case-folding risk for ≈ 0.4 s **(M)** |
| Aho-Corasick, pyarrow, polars | The prefilter is already cheap; pyarrow is 152 MB; `regex` needs `str` |
| Corpus mode (BL-8) now | ≈ 1.1-1.3x expected on the core **(E)**, plus a neighbour-invariance risk (decision 3) |

Revision 3 dropped one rejection: **document order where R shows pattern-major order**. Row order is Band B (FIDELITY.md §8), so funding_check_oi may use document order; it is noted in the release notes (decision 1 b), and no deviation row is needed. The `text_search` façade keeps pattern-major order.

---

## 3. Subsystems

### 3.1 What happens to each package

Written for revision 2. Where §3.5 goes further (for example `json_expand`, the report deparser, `api/jsonlite.py`), §3.5 wins.

| Package | Onto the core | De-emulated (paused lane) | Kept, because outputs depend on it | WP |
|---|---|---|---|---|
| `papers/` | `paper_table`, `paper_id`, `ref_table` become Doc/RefIndex views; `_derived` slot; `papers/ids.py` (F6) | – | schema, coercion, `records_to_frame`; the `Paper` API | CORE 1b/1c |
| `text/`, `stats/` | `text_search`, `text_expand`, `extract_*` become façades; `stats()` on `statcheck()`; `extract_tests` on `equations()` | lane 5: `json_expand`'s jsonlite model → ≈ 150-line expander (null → NA, U) in CORE 1c; `stats()` keyword arguments; long-double `R_strtod`/`formatReal` → plain floats (`Kind` drops `r15`); ICU rank and `towlower` dropped; unequal-n in closed form | statcheck 1.5.0 logic, including its three-valued control flow; `_rmath` | CORE, STATS |
| paper modules | all 24 on `pytacheck.doc` | lane 5: `_prereg` value model → ≈ 50-line flatten; ref joins through `RefIndex`; prefilters deleted | patterns verbatim; coi agrep; `_funding` synonyms and `get_*` locators taking `(doc, pattern)`; traffic lights; texts | PATTERNS, STATS, REFS, LINKS |
| `io/` | `Doc.from_records` at read time; lazy `eq`; `read_plan`/`read_one`; `read(workers=)` | lane 7: Grobid timestamps via `fromisoformat`; `corpus.py` RDS → `_files_rdata` + adapter; bibr12 plain cells | TEI and bibr 12 parsing and writer; the UAX#29 splitter | CORE 1b (hook), SERVICES |
| `db/` | DOI dictionaries → `core/refs.py`; lookups via `RunContext.cache` | lane 7 + N3: one JSON path; key dedup (L7-1); `add_bib_match` groupby (L7-2); PubPeer chunks ≤ 500, NA per failed chunk (L7-3, U) | the clients; bundled data | CORE 1c, REFS, LINKS (regcheck) |
| `llm/` | driven by `Settings`; scoped overrides; typed structured results; grounding | lane 6, all of it (§3.3) | byte-identical request bodies; the `llm()` API, sanitiser, type constructors, `ellmer_prices.csv` | LLM-a, LLM-b |
| `datacheck/` + data modules | `Table` columns and `ColumnProfile` records; readers via `RepoIndex`; `detect_many`; the roster facet | lane 3, all of it; its "keep ColAttrs" constraint dropped; `_checks_rvec` → column kinds | fread type vocabulary; readxl shaping; vctrs names; haven labels; ODS walker; `_files_rdata`; R `strptime` validity; sniffing heuristics | DATA-a, DATA-b |
| `codecheck/` + code_check | lazy `CodeDoc` facets on `RepoIndex.code(e)`, shared by three modules; `parse_errors` batched | lane 4, all of it | `_rparse.py` (agrees with R on 1,383/1,383 files), tables regenerated compactly; purl's layout | CODE-a, CODE-b |
| `repro/` + reproducibility_check | plans from `CodeDoc` facets and index hashes; scoped LLM override; full `Settings` in workers (L4-1) | lane 4: scripts keyed by relative path (`RNamedList` deleted, U); Docker results as JSON | R-literal quoting; package lists; `repro/tables.py` `.rds` interop | CODE-b |
| `statout/` | readers return records; `match_reported` reads `equations()` | lane 7: SPSS fit-line evaluator (840 lines) → ≈ 50-line `ast` whitelist; jsonlite → `_json`; one `.stat_num_to_chr`; clear error on a list | SPV decoders; JASP, jamovi, SMCL, Mplus readers; STATO map | SERVICES-a |
| `archives/` | 14 detectors → one pass (`archives/links.py`) behind `links()`; `*_links` façades; host clients return `FileEntry`; `osf_type` via the run cache | lane 7: one JSON path; `zip_peek` via `zipfile`; `osf_metadata` via `json.dumps`; github/gitlab window `{0,10}` (U); idiom helpers → `_values` | domain clients; downloads and caches; `aspredicted._html_text2`; `zenodo_upload` | LINKS |
| `repository/` (new), `fileinfo/` | `RepoIndex` (§3.2); repo_check as a renderer; classification once per `FileEntry` | lane 7: exact keys, readable listing errors, `repo_error` by presence | – | REPO |
| `report/`, `api/` | loops through `execute()`; `DEFAULT_MODULES` removed by TIERS (the `metacheck::default` preset replaces it); `report(list)` ids via F6 | lane 7: `_Deparser` → ≈ 80-line R-literal writer | blocks, renderer, the v1 fence reader (in compat); `api/jsonlite.py` | CORE 1d/1e, SERVICES-b |
| `packs/`, `module.py`, `provenance.py`, `presets.py` | resolve/call/assemble; `depends=` per §2.7 and `requires.pytacheck` per §2.11; path packs checked with one `stat` per run | the R "unused argument" replay | `@module`, `ModuleSpec`, `ModuleOutput`, packs, presets, `RunRecord` | CORE 1d, MODSYS |
| `http.py`, `utils.py`, `config.py`, `log.py`, `batch/` (new) | N1 limiter; HTTP memo on the run cache; context-local options; per-process logs; `run_batch` | – | – | BATCH-a/b, CORE 1d |

### 3.2 `RepoIndex`

`src/pytacheck/repository/index.py`, the repository counterpart of the Doc (census_subsystems §6):

```python
@dataclass(frozen=True, slots=True)
class FileEntry:
    repo: str; rel_path: str; name: str; ext: str; size: int | None; url: str | None
    member_of: str | None; category: str; data_type: str | None; doc_role: str | None
    group: str | None; local_path: str | None

class RepoIndex:              # one per (repo URL or realpath, listing fingerprint) per run
    entries: tuple[FileEntry, ...]
    def listing(self): ...                      # archives.listing adapter (REPO), host threads (L7-6), per-host queues (FETCH)
    def fetch(self, entries, policy): ...       # shared Settings.download_dir; one download scheduler (RF-8); blobs by hash (CACHE's store, wired in LINKS's archives/fetch.py)
    def raw(self, e) -> bytes: ...              # read once; hashes memoised
    def text(self, e) -> Decoded: ...           # delegates to codecheck._decode (CODE-a/b)
    def table(self, e, sheet=None) -> Table: ...# delegates to datacheck readers (DATA-a/b)
    def workbook(self, e): ...; def stat_meta(self, e): ...      # delegate to datacheck
    def code(self, e) -> CodeDoc: ...           # delegates to codecheck
    def json(self, e): ...; def yaml(self, e): ...
```

**The index owns caching and I/O, not parsing** (Review B-14). Every parsing method delegates to a function in `datacheck/` or `codecheck/`. DATA-b and CODE-b therefore change only their own packages, never `repository/index.py`. The public adapters (`code_read`, `data_read_head`, `parse_codebook`, `download_repo_files`, `code_parse_r`, `*_info`, `*_links`) stay for the 6,125 parity cases in these areas. Implicit re-runs (`run_missing("psychds_check")`) get the same index, which ends census §5.4's `local_path` loss (a U-entry if an R-faithful output changes). The one-row DataFrames per data column become `ColumnProfile` records, one frame per file; today they take 22% of warm chain time **(M)**.

**Expected on the traced six-module chain (E, from the M trace):** file opens 96 → ≤ 23; CSV parses 3 → 1; workbook parses 5-6 → 1; code decodes 2 → 1 (4 decoders → 1); hashes 2 → 1.

### 3.3 Where each goal of lanes 3-7 lands

Every unit in `rightsize_plan.json` → `lanes[2..6]` has a place. **Every acceptance item becomes a gate of the package that absorbs it** (Review B-15 restored the items revision 1 dropped, marked ✚).

**Lane 3, data files and data modules → DATA-a** (a leaf behind `data_read_head`) **and DATA-b** (consumers on the index).
- DATA-a: fread emulator → ≈ 300-line pandas C-engine reader with a type layer (`_files_delim.py`); read.table/scan/type.convert emulator deleted (codebook files and the header sniff use the new reader or `csv`); readxl → python-calamine + ≈ 140 lines of shaping (ODS walker kept); sniffers on decoded head text, UTF-16 decoded, `_LineReader` deleted; `as.numeric` copies → `_values.as_float`; `_raw_rows` from the head parse; Latin-1 repaired at read time; `data_is_manifest` on string columns only; `_columns_labels` via `_json.loads`; the `RInt` seed → `int`.
- DATA-b: no "the model request failed" with the LLM off (U); Data Tree widths via `unicodedata`, one tree walker shared with `psychds_tree_html`; module idiom helpers → `_values`; consumers on `Table`/`ColumnProfile`; `_checks_rvec` and the trial-sniff temp file deleted; one workbook handle; summaries from dicts; codebook scans through `detect_many`; BATCH L3-1 and L3-2.
- **Gates:** the data_check column table and preview identical on ≥ 398/425 delimited files, every other difference documented; all 128 R-written files keep R's dims, names, classes and NA counts (except the documented `trials__write.csv2`); spreadsheets identical on ≥ 70/83, ✚ all 8 realistic openxlsx workbooks and the spreadsheets-repo fixtures identical; ✚ with pyarrow installed, `data_read_head` of `latin1_row3.csv`, `latin1_header.csv` and `latin1.rds` returns the right frames; ✚ tier-1 mod_data_check, mod_codebook and mod_psychds unchanged apart from the LLM-off sentence; 100k×11 CSV ≤ 1.5 s, and as xlsx ≤ 5 s; the 425-file corpus ≥ 40% faster; the wide 400×138 repository ≥ 30% faster; ≥ 2,500 lines smaller.

**Lane 4, code checks and reproducibility → CODE-a** (a leaf behind `code_read`/`code_extract_r`) **and CODE-b.**
- CODE-a: the ICU/vroom/iconv pipeline → ≈ 150-line decoder (`_decode.py`), later the index's only decoder (4 decoders and 68 `.decode(` sites → 1); knitr purl and the R evaluator → ≈ 260-line static chunk extractor keeping purl's layout (`_chunks.py`); `_rjson.py` → `_json.loads`; yaml → a `SafeLoader` subclass; R parser tables regenerated by `scripts/gen_rparse_tables.py`.
- CODE-b: reproducibility_check keys scripts by relative path, `RNamedList` deleted (U); Docker install results as JSON; idiom helpers → `_values`; lazy `CodeDoc` facets; `parse_errors` batched; BATCH L4-1.
- **Gates:** `code_read` identical on 2,770/2,774 files and the 795 project files in ≤ 0.5 s; chunk extraction identical on the 20 research documents ✚ and on all 70 `code_extract_r` cases except `bad_options.*`, ≥ 452/481 vignettes agreeing with knitr, ✚ extraction ≥ 20x faster; the 39 `code_parse_r` cases unchanged; the 525-file corpus identical on 523; ✚ tier-1 mod_code, mod_repro and repro_core unchanged apart from the 3 base-name cases; `codecheck` ≤ 6,300 lines, ✚ ≤ 4,200 counting the compact tables.

**Lane 5, text modules, statistics and paper tables.**

| Unit | Lands in |
|---|---|
| One-pass search on a cached sentence table; run-scoped table caches; module prefilters deleted | CORE 1b (the Doc and engine) |
| ✚ Multi-pattern character-vector searches return results (8 U-cases, §2.10) | CORE 1b |
| `json_expand` → ≈ 150-line expander (null → NA, errors per reply); causal walks plain dicts | CORE 1c (façade); REFS (causal) |
| stat_effect_size long double → plain float (no `r15`); ICU rank, `towlower`, `INT_MAX` removed; closed-form `d_min`/`d_max`; `stats()` keyword arguments | STATS |
| ref_* join emulation → `RefIndex` records; `_refs_with_doi` deduplicated; a bib without `text_id`/`doi` gives `na` (U) | CORE 1c + REFS |
| `_prereg` → a flatten | LINKS |
| Leftover R-crash guards; causal_claims counts only TRUE; `as_numeric` → `_values.as_float` | each package |

**Gates:** 0 of 529 module-suite outputs differ apart from documented fixes, ✚ and the suite runs in ≤ 17 s (34 s today); the effect-size table identical on 12,780 cells and 2,790 corpus cells; ✚ the in-scope parity areas (1,929 cases) in ≤ 95 s; tier 1 0 unmarked changes; ✚ mod_power(+review) unchanged apart from null → NA; ✚ recorded causal/API mocks still match with byte-identical request bodies; the cache mutation test; ≥ 2,500 lines smaller.

**Lane 6, the LLM client → LLM-a and LLM-b:** yajl → `_json.loads` with retries decided on the exception type; the jsonlite writer → a `%.17g` plain-value writer; a JSON cache instead of the R-shared `.rds` cache (D-entry); the structured-result builder; table-driven providers with ISO timestamps; `Type.__repr__`; N5 and L6-2 to L6-6 (L6-1 goes to CORE 1d). **Gates:** 198/198 request bodies byte-identical; 384/385 parsed replies equal; 89/89 cached frames round-trip; a cache key ≤ 0.05 ms; `𝐀` decodes to U+1D400; LLM-using module areas show 0 changes; `llm/` ≤ 3,300 lines; `import pytacheck.llm.providers` ≤ 100 ms.

**Lane 7, services, I/O, stat outputs, report and repository modules.**

| Unit | Lands in |
|---|---|
| Archive JSON, httr2 and base-R helpers → `http.resp_json`, `_json.loads`, `_values.field`; `zip_peek` → `zipfile` over the range tail (path-safety filter, CRC failures skipped); `osf_metadata` → `json.dumps`; github/gitlab window `{0,10}` (U); reg_check `str.lower` + `pd.DataFrame` | LINKS |
| db JSON path; psycharchives scalars or `None` | REFS |
| SPSS fit-line `ast` whitelist; `fromisoformat` timestamps; `corpus.py` RDS adapter; R-internal warnings deleted; `bibr_convert` skips empty author parts; `match_reported_output` errors on a list | SERVICES-a |
| report's deparse port → R-literal writer; L7-7 to L7-9 | SERVICES-b |
| repo_check: exact keys, plain frames, readable listing errors, `repo_error` by presence | REPO |
| BATCH riders L7-1 to L7-3 (REFS), L7-4 (CORE-0), L7-5 (LINKS), L7-6 (REPO), L7-7 (SERVICES-b; RegCheck in LINKS), L7-8/9 (SERVICES-b), L7-10 (later) | as listed |

**Gates:** 502/502 recorded API bodies parse identically; zip listings equal on 68/70 and central-directory parsing ≥ 10x faster; 14/14 fit lines, 15/15 timestamps and 8/8 corpus `.rds` files identical; ✚ tier-1 archives_*, repo_download, db, io, bibr12, grobid12, statout_*, report, mod_repo_check and mod_reg show 0 unmarked changes; ✚ the repo_check error-text cases (`demo`, `psychsci_demo`, `researchbox`, `all_platforms`, `.paperlist`, `.tables`) **pass via presence compare, not xfail**; ≥ 2,800 lines smaller.

### 3.4 Kept deliberately

Revision 3 prunes revision 2's list with FIDELITY.md §9. What stays decides inputs, detections or values (Band A), or is a feature users call:
- **Kept:**
  - the R parser (`_rparse.py`, which agrees with R on 1,383/1,383 files), with its tables regenerated compactly;
  - the `.rds`/`.RData` reader (a feature);
  - user-visible R regex semantics (`_r/regex.py`, lane 2) and coi agrep;
  - module business logic, statcheck's three-valued logic and `r_round` (it decides statcheck's error flags);
  - the SPV binary decoders, the stat-output readers, and the archive and database clients;
  - `html_text2`, Grobid TEI, the UAX#29 splitter and bibr 12;
  - the report renderer's structure;
  - the harness comparator, with the canonicaliser in front of it;
  - the tier-2 corpora.
- **Narrowed:**
  - R number formatting survives only in statout cell text and LLM prompts; the report renderer formats result floats itself;
  - ICU-like collation survives only for sorted lists users see, after an audit of the 55 `r_sort_key` call sites;
  - haven/vctrs/readxl shaping keeps values and labels, not dtypes;
  - LLM request bodies stay byte-identical (`%.17g` numbers included) for every module with mock-replayed parity cases: power, causal_claims and the `llm` area. The mocks are keyed by a hash of the body, so a free body would break about 330 LLM parity cases;
  - repro tables, `count()` and `bind_rows` keep values, not order or shape;
  - U-entries marked Kept stay only where they touch Band A.
- **No longer kept:** `api/jsonlite.py` (plain JSON); the ODS walker (calamine reads ODS); `zenodo_upload` moves out of the package (decision 16).
- **Rejected dependencies stay rejected:** pyarrow (as a requirement), polars, PyICU, tree-sitter-r, provider SDKs, re2 and charset-normalizer. `_funding._Article` is superseded by the Doc.

### 3.5 Further de-emulation under relaxed fidelity (revision 3)

Three sweeps (paper, repository, services) listed 85 emulation items at f559c1be **(M3)**. Each was sized twice: as revision 2 scopes it, and with presentation free.

| Area | Lines today **(M3)** | Revision 2 plan **(E)** | Relaxed **(E)** | Extra from relaxing |
|---|---:|---:|---:|---:|
| Paper modules, `text/`, `stats/`, `papers/`, `_r/`, runners | 21,964 | ≈ 16,800 (−24%) | ≈ 13,850 (−37%) | ≈ −2,950 |
| Repository cluster (`codecheck`, `datacheck`, repository modules, `repro`, `fileinfo`) | 46,275 | ≈ 33,900 | ≈ 31,100 | ≈ −2,800 |
| Services (`archives`, `statout`, `llm`, `io`, `db`, `report`, `api`, `http`, `cli`, `config`) | 51,335 | ≈ 43,700 (−15%) | ≈ 38,300 (−25%) | ≈ −5,400 |
| **Total** | **119,574** | **≈ 94,400** | **≈ 83,250** | **≈ −11,150** |

Basis: each item's measured line range, the lane gates, and the spike's −17% on module bodies. The largest items, with the band their deviation falls into (B unless noted):

| Item | Lines now → after **(E)** | Package | Deviation |
|---|---|---|---|
| **Wholesale library ports (decision 16)** | | | |
| ICU ucsdet + vroom + iconv → `_decode.py` (BOM, UTF-8, the optional chardet extra, cp1252) | 3,864 → 150 | CODE-a | edge cases: non-UTF-8 scripts without a BOM |
| ellmer port (`llm/providers.py`) → a thin httpx client for OpenAI-compatible, Anthropic and Gemini | 2,096 → 700 | LLM-a | fewer built-in providers; request bodies stay byte-identical for every module with mock-replayed cases (§3.4) |
| S7 type system (`llm/types.py`) | 800 → 300 | LLM-a | B |
| data.table fread C port → pandas C engine + type layer | 1,352 → 300 | DATA-a | edge cases |
| knitr purl + chunk-option evaluator → static `_chunks.py` | 1,748 → 260 | CODE-a | edge cases: computed chunk options |
| read.table/scan/type.convert port | 585 → 30 | DATA-a | edge cases |
| readxl walker + two ODS readers + minty → calamine (already declared) | 909 → 200 | DATA-a | edge cases |
| `zenodo_upload` out of the package | 1,228 → 800, then separate | SERVICES-b | – |
| **Value models, deparsers and JSON emulation** | | | |
| `json_expand`'s jsonlite model, `type.convert`, dates, deparser → `json.loads` + a flatten (nulls stay None) | 1,235 → 120 | CORE-1c | B |
| LLM `.rds` cache and R value model → a JSON cache | 1,095 → 120 | LLM-a | B (a D-entry in revision 2) |
| yajl parser/writer (`llm/_json.py`), `_rjson`, statout's jsonlite model | 512 + 235 + 950 → ≈ 240 | LLM-a, CODE-a, SERVICES-a | B |
| report's R deparse port → the `.qmd` embeds an HTML table | 470 → 0 | SERVICES-b | B |
| `_prereg` value model, `osf_metadata`, bibr 12 simplification, `api/jsonlite.py` | 417 + 270 + 495 + 221 → ≈ 280 | LINKS, SERVICES-b | B |
| `_checks_rvec` R atomic-vector model | 678 → 100 | DATA-b | edge cases |
| **Frame machinery and duplicated idioms** | | | |
| `text_search`/`paper_table`/`extract_*` frame machinery | 1,167 → 490 | CORE-1b | B |
| `module.py` output-contract replays (`na_replace`, "unused argument") | 1,075 → 600 | CORE-1d/1e | B |
| ref_summary join engine + per-module ref joins and pivots | 497 + 448 → 240 | REFS | B |
| `validate.py` recycling, vctrs typing and dplyr replays (`accuracy()` kept exactly) | 371 → 110 | CORE-1d | B |
| Duplicated R base-idiom helpers (147 private definitions, 1,297 lines) → `core/values.py` | ≈ 1,300 → ≈ 150 | each package | none |
| Archive JSON/httr2/base-R helpers and a second HTTP stack | 1,272 + 530 → 320 | LINKS | edge cases |
| **Monoliths and duplicated structure** | | | |
| Per-archive download/verify/zip-member bodies → one `archives/fetch.py` engine | 3,354 → 1,400 | LINKS (+2 d) | edge cases; the riskiest single cut |
| Repository module monoliths (`_codebook_check` 689 lines, `_assess_r` 669) → sub-checks on `RepoIndex`, no function over ≈ 300 lines | 3,658 → 2,850 | REPO, DATA-b, CODE-b | none |
| `_rparse` messages, AST builder and Rscript engine | 1,676 → 1,050 | CODE-a | B |
| Compact `_rparse_tables.py` (one int per line today) | 2,250 → 110 | CODE-a | none |
| statcheck port's R runtime and nmath → Optional values and `scipy.special` | 1,711 → 860 | STATS (+1 d) | edge cases; strict differential gate while stat_check is pending certification |
| Two Grobid converters (legacy and 12.x) → one | 300 → 0 | SERVICES-a | **results**: a Band A row |
| `presets.py` gains the status axis | 585 → 650 | TIERS | **results**: intended |

**What stays exact while these go:** the detections, traffic lights and extracted numbers of every module. The canonicaliser (FIDELITY.md §4) is what makes that checkable without pinning presentation. **Edge-case** items change results only on rare inputs (non-UTF-8 code, computed chunk options, unusual CSVs). Each gets one scoped deviation row naming the inputs, and none of them touches a validated module's Band A outputs.

**Top wins by lines per day (E):** compact parser tables (≈ 8,500/day), ICU → `_decode.py` (≈ 3,700/day), the SPV `ast` whitelist (≈ 1,800/day), the LLM `.rds` cache (≈ 1,300/day), osf_metadata (≈ 1,200/day), read.table (≈ 1,100/day), purl (≈ 1,000/day), and `json_expand` (≈ 740/day).

**Structural items that come with it** (§2.7, §2.9, §2.10):
- `_oi` variants become `coi_check(variant="oi")` and `funding_check(variant="oi")`, with the old names kept as registry aliases (−60 to −100 lines).
- The p-value trio reads one shared `p_values` facet, so extraction runs once per paper, not three times (−150 to −250 of 485 lines).
- `_funding._synonyms` (319 lines of string building) and the other pattern lists move word for word into `modules/patterns/*.toml`, so the upstream sync becomes a data diff.

### 3.6 Target layout (revision 3)

Counting rule: `wc -l` over `.py` files under `src/pytacheck` **(M3)**. Directory names are today's: revision 3 moves no directories (decision 21). The targets are estimates and sum to §5.3's ledger. §4.3's package size gates are ceilings at or above them, not the same numbers: codecheck's target is 3.5k, and CODE-a's gate is ≤ 6,300 lines (≤ 4,200 counting the compact tables).

```
src/pytacheck/                                             now (M3) -> target (E)
  core/        doc, patterns, facets, refindex, execute, result,
               blocks, settings, values (new; ≈ 0.7k moved in),
               and doc/ (pytacheck.doc, the author API; 40 lines today) 0    -> 3.2k
  modules/     paper checks; patterns/*.toml                           10.6k -> 7.0k
  text/ papers/ _r/ stats/ module.py and the other paper-side
               top-level files (façades, statcheck, ids)                11.3k -> 6.15k
  modules/ (repository checks) + fileinfo/: files, data, codebook,
               psychds, code, repro sub-checks on RepoIndex            16.1k -> 13.7k
  datacheck/   readers, column profiling (no fread emulation)          14.5k -> 10.3k
  codecheck/   R/Python/Stata parsing, static chunks, heuristic decoding 11.8k -> 3.5k
  repro/       execution sandbox                                        3.9k -> 3.6k
  archives/    Host protocol: osf, github, dataverse, zenodo, …        17.2k -> 12.4k
  statout/     spv, jasp/jamovi, match_reported                        12.3k -> 9.5k
  io/          grobid, bibr12, pdf                                      6.3k -> 5.5k
  llm/         thin client                                              6.3k -> 3.3k
  db/          crossref, openalex, retraction, regcheck                 3.7k -> 3.0k
  report/      typed blocks -> html / gfm / qmd                         2.7k -> 1.8k
  api/ cli http log config _json   incl. api/web.py (+0.2k)             2.8k -> 3.0k
  packs/ presets provenance   status registry (+0.6k), deletions (−1.0k) 5.4k -> 5.0k
  compat/ + __init__   _EXPORTS, deprecation hook, ModuleOutput adapter 0.8k -> 1.1k
  port/        pytacheck port (ECOSYSTEM.md §5.2)                       0    -> 1.26k
  TOTAL                                                               125.8k -> ≈ 93.3k
```

- **Total:** ≈ 93,300 lines (E; range 88-97k), 26% smaller than today. Revision 2's plan, re-sized on the same sweeps, gives ≈ 103,000 (−18%); relaxed fidelity removes ≈ 11,000 more (§3.5). The derivation is §5.3's ledger, which all three documents cite. There is no unitemised plug row.
- **compat** is the existing `_EXPORTS` lazy-export map plus a deprecation hook (≈ 100 lines) and the `ModuleOutput` adapter (≈ 200). The façades move into it as moves, not new lines.
- **Stable import paths.** These stay importable, unchanged, until a contract v2:

| Path | Names |
|---|---|
| `pytacheck.module` | `module`, `module_run`, `get_prev_outputs` |
| `pytacheck.text` | `text_search`, `text_expand`, `extract_*` |
| `pytacheck.report` | `scroll_table`, `collapse_section` |
| `pytacheck.doc` | the author API (§2.10) |
| `pytacheck.papers` | `Paper`, `PaperList`, `paper_table`, `ref_table` |

  One deprecation rule covers compat names and any old path: a `DeprecationWarning` once per process, for at least two minor releases, and removal no earlier than 1.0.
- **No directory renames** (decision 21). Public namespaces (`pc.hosts`, `pc.checks`, `pytacheck store …`) are exposed without moving directories, so the waves do not depend on renames and no import path breaks.
- **Tests** (106,358 lines) have their own row in §5.3 (target ≈ 90,000). Each wave package deletes or rewrites the tests of the emulation it removes, and moves tests that assert Band B properties to canon-based checks; CLOSE does a final measured sweep.

### 3.7 Report and web app (revision 3)

**The Shiny app is not ported.** It is 1,297 lines of R: `app.R` 818, `render_report.R` 121, `setup.R` 83, `www/custom.css` 221 and `www/custom.js` 54 **(M3)**. `render_report.R` and `setup.R` exist to drive Quarto; `app.R` holds the form, the four job slots, the privacy text and the LLM options. pytacheck already renders the report itself: `render_module_outputs()` writes one self-contained HTML page in the layout of `_report.qmd`, with no Quarto (`report/render.py`). A web front end is therefore a thin form in front of `read()`, the preset and status selection, and `report()`.

**The report page** keeps metacheck's design, with relaxed fidelity: layout, colours, the traffic lights, the summary table, the details toggles, dark mode and table paging stay close to the Quarto report, but not pixel-identical (Band B). Under revision 3 it is rendered from the typed blocks of §2.9, so the page, the `.md` output and the `.qmd` output share one source. TIERS adds the status badge, the validation box and the header line (ECOSYSTEM.md §3.6).

**The spike** built two front ends on today's code and tested them in headless Chromium on the demo paper ("To Err is Human", TEI XML and bibr JSON). The figures are **(M3)**, from `gradio-spike/REPORT.md`:

| | Gradio 6.28.0 | Static page on the API |
|---|---|---|
| Size | 200 lines of Python (169 without blanks and comments), no custom JS | 81 lines of HTML + 15 lines of server |
| Install on top of `pytacheck[api]` | +21 packages, +129.5 MB (gradio is 84 MB, 68 MB of it the frontend) | nothing |
| `import gradio` | 2.6-2.9 s; RSS 9 → 127 MiB (pytacheck + api + uvicorn: 0.41 s, 42 MiB) | – |
| Server ready, idle memory | 3.6-3.7 s, 151 MiB | 1.1 s, 94 MiB |
| Demo report, 10 offline modules | 0.69 s on the server; 2.7-3.2 s from click to report in the browser | 1.4 s in the browser |
| Inputs | PDF (through GROBID), TEI XML, bibr JSON | bibr JSON only |
| Queue, progress, cooldown | queue with position shown (`max_size=20`, `concurrency_limit`), per-step progress, a 20 s cooldown; 3 simultaneous jobs finished in 25.1 s with 0 errors | none: requests wait at the API's semaphore |
| Options | GROBID server (EU or USA), CrossRef, PubPeer, repositories; the privacy text updates with them | none |
| Shiny's 16 features | 14; not the anonymous usage counts or the LLM option | upload, report, download |
| Third-party hosts contacted | none over 4 starts, with the settings below; with Gradio's defaults, huggingface.co twice and api.gradio.app once at each start | none |

Both show the report in a sandboxed `<iframe srcdoc>`, where the report's own JS works (details toggles, dark mode, the four paging buttons), and both offer the same self-contained file as a download (52 KB) and an open-in-new-tab link.

**Recommendation: Gradio, as the optional extra `pytacheck[app]`, pinned `gradio>=6.28,<7`** (decision 20). The privacy settings below are Gradio 6.28's flags, so a major upgrade is a reviewed change, not a routine bump.
- **Why:** 200 lines cover 14 of the Shiny app's 16 features, and the queue, progress, uploads and cooldown come built in. The static page would need API changes to gain a queue, progress or XML input, which rebuilds what Gradio provides.
- **Risk: upgrade churn.** Gradio changes flags and defaults often, and a minor 6.x release can change what a fixed setting does. The unit test per setting and the nightly proxy check catch it; the fix is a reviewed change to `web.py`, not an automatic bump. Gradio 7 needs a new review of every fixed setting before the pin moves. If the churn costs more than the app saves, the static page of decision 20 (b) is the fallback.
- **Cost:** only for those who install `[app]`: +129.5 MB, about +57 MiB idle memory and about +2.5 s startup. The base and `[api]` installs do not change, and an import test keeps them free of gradio.
- **Mounting:** `gr.mount_gradio_app(create_app(), demo, path="/app")`, so one process serves the REST API at `/` and the app at `/app`. WEB adds `pytacheck serve --app` to start both.
- **Fixed settings, not options** (a unit test asserts each one; the nightly job starts the app behind a logging proxy and fails on any third-party host at startup or during a demo run, and checks that `/app/gradio_api/info` lists nothing): `GRADIO_ANALYTICS_ENABLED=False` (set before the import; it also disables Hugging Face telemetry), `run_history=False` (otherwise past runs, reports included, are kept in the browser), `api_visibility="private"` on every event (otherwise a script can call the report function directly and skip the cooldown), `ssr_mode=False` (no Node process), `footer_links=[]`, and never `share=True` (a tunnel through api.gradio.app). The theme and CSS go to `mount_gradio_app()`, and `gr.HTML` gets `js_on_load=None`.
- **Uploads:** Gradio stores uploads by content hash, so deleting one after its job broke the other queued jobs with the same file. `delete_cache=(300, 600)` removes them instead, which leaves an upload on disk for up to about 15 minutes; Shiny deletes it at once. The privacy text says so (decision 20 asks whether that is acceptable). Reports are kept in memory only, for an hour, under an unguessable token.
- **Usage counts:** the Shiny app keeps anonymous counts on purpose ("so that continued development of metacheck can be justified in future grant applications", `app.R:42-44`): one CSV row per session or report with the date, the number of reports and the number of modules, no identifier of any kind, written only on the hosted server and never sent anywhere. WEB carries the same record over in ≈ 15 lines, to the same file format, so the counts of both apps can be added up (decision 20).
- **Modules:** the server's policy is validated ∩ server-safe (ECOSYSTEM.md §3.4; decisions 9 and 10), and a request can be stricter, never looser. **Server-safe is data:** a `server_safe: true` field per certification in `validation.json`, set by the team after a timed run on the server's limits and shared with the R app (ECOSYSTEM.md §2.4). At run time a module also needs its network need, the union of `requires=` capabilities over its `depends=` closure (§2.7), to be covered by the options the user switched on. Hosted mode loads only store packs at public revs (trust `store`, from a public store); WEB owns that filter. Each job runs in a process-pool worker with a wall-clock limit, so one slow module cannot hold a slot. A request that names a module below the ceiling gets a 400 that lists it.
- **Source offer (AGPL §13):** `GET /source` names the running commit and each active pack's source, rev, tree hash and licence, and the Gradio layout shows a visible "Source code" link, since the footer is hidden. It needs pytacheck's repository, or an sdist per release, to be public (decision 13). WEB builds it, because the app ships first. SERVICES-b adds the report's "Source" footer from the same data (§4.3).

**What the library needs first.** The spike found four gaps. Each lands in the package that owns the code, not in the app:

| Gap **(M3)** | Fix | Package |
|---|---|---|
| `llm_use` is process-global, so two users of one server would race on each other's LLM settings. The prototype leaves the LLM option out | per-run `Settings` and `override` (§2.6); the app gets the LLM option afterwards | CORE-1d, then LLM-b (+0.5 d) |
| codebook_check declares no network need but reaches the network through its repository and data dependencies, so `offline=True` keeps it; it takes 55-63 s even with every network option off | the network need is derived through the `depends=` closure (§2.7); codebook_check is not server-safe until DATA-b brings it within the time limit. WEB runs after CORE-1d, so it needs no deny-list | CORE-1d, DATA-b |
| ref_accuracy fails when the CrossRef match was not run | it `depends=` on the match, so switching CrossRef off drops it with a reason, like any other network module | CORE-1d, REFS |
| `/paper/check` returns `report_html` boxed as `["<html>…"]` | plain JSON | SERVICES-b (`api/jsonlite.py` goes, §3.4) |

**Not measured:** the PDF path. The sandbox's proxy blocked both GROBID servers (403), so conversion time and its error messages were not tested; the prototype carries over `app.R`'s plain-language messages for them. The network options with the network on were not measured either. WEB measures both before the app is announced.

**Hosting is out of scope.** Decision 20 chooses the library extra. The image and host, a self-hosted or named GROBID, worker sizing, running alongside the Shiny server, the cutover date and the user notice about the smaller module set (16 → 5) are a deployment checklist for whoever runs the public server, agreed with the metacheck team (ECOSYSTEM.md §8.1). No package here does them.

---

## 4. The rewrite plan

### 4.1 Gates

| Gate | Command | Passes when |
|---|---|---|
| **G1** parity | `python -m parity check --strict --area <areas> --jobs 4`; full run in CORE 1b and nightly | Tier 1: 0 fail on **Band A**, and no drift in the **canonical** lock digests (revision 3; FIDELITY.md §5). Any Band A or Band C change needs a deviation row in the same PR, and a validator's code-owner approval for a Band A change, on any tier, in a module the registry snapshot labels validated. Each case under a row keeps its per-case lock entry. **Plus the coverage ratchet (H0-13), counting distinct realistic inputs.** |
| **G2** accuracy | `python -m parity accuracy --gate --budget parity/bench/budget.toml` | **Results only** (revision 3): traffic lights exact; tables and summaries compared as multisets, F1 = 1.0 or explained; wording and column order are not levels. 0 unexplained, **including in a fresh worktree** (H0-11) |
| **G3** tests | `pytest` (core, foundation, modsys, owned dirs), `ruff check`, `mypy --strict` on `core` and `doc` | green |
| **G4** performance | `python -m parity bench --check` | counters within caps (hard, including a materialised-paper row and a mixed chain); ≤ +15% per module against base (hard); absolute budgets soft in PRs, hard nightly once W1 has re-measured them |
| **G5** equivalence | `pytest tests/migration/test_<wp>.py` | equality through `parity/canon.py` with **SNAP's raw golden snapshots** on the 24 accuracy inputs, the 3-paper list, the distinct-papers-same-id fixture, the duplicate-row fixture and every parity input of the package's areas, except listed U/D entries |
| **G6** layering | `pytest tests/foundation/test_layering.py` | §2.1, derived from imports |
| **G7** store | CI: `pytacheck-modules` → `store build . --check`, `pack check packs/*`, the packs' pytest | green, against pytacheck built from source (a `.devN` version) |
| **G8** batch-equality | `python -m parity batch-equality --level shared,each[,batch]` | `shared` from phase 0 (0 differences in 399 comparisons, **S**); `each` from CORE 1d; `batch` (BATCH_DESIGN H2 a, c, d) from BATCH-b |
| **G9** status (new) | `pytest tests/status/`; `pytacheck status --check` | the snapshot's schema is valid; every built-in has a registry entry; the running pytacheck version (built-ins) or code digest (packs) is in the certification's list, or the badge reads "changed since validation"; `select(preset="metacheck::default", status="validated")` returns the 5 modules; policies filter as ECOSYSTEM.md §3.5 says; the web and API defaults are `validated`. While the snapshot is provisional (ECOSYSTEM.md §2.3), G9 checks the schema only |

**Behaviour changes in thin-coverage modules ship separately** (Review B-5). causal_claims (0 tier-1 cases), ref_pubpeer (2), prereg_check (1), reg_check (1), reproducibility_check (0) and psychds_check (0 tier-1; 35 of 46 tier-2 cases warn-only) get a pure rewrite PR (G5 equal) first, then a separate behaviour PR carrying its deviation row and HARNESS-NET's replayed rows.

**Validated modules get a stricter rule** (revision 3). It applies to **every module the registry snapshot labels validated**; for planning that is 5 modules (power, stat_p_exact, stat_p_nonsig, marginal, stat_effect_size), with stat_check pending and ref_accuracy a candidate (ECOSYSTEM.md §3.1). They change Band A outputs, on any tier, only with a validator's code-owner approval or a re-validation that reruns the team's full-paper ground truth (ECOSYSTEM.md §2.5). Their wording rows are mandatory. power's parity cases replay recorded LLM replies, so they protect everything after the model call, not the model's own accuracy. Only its locked model id, prompt and schema, plus the rerun, protect that; a recorded accuracy row does not close the gap (FIDELITY.md §10, risk 1).

### 4.2 Harness changes

**SNAP** (1 day; first, right after lane 2 merges; before CORE-0):

| ID | Change |
|---|---|
| H0-12 | Record at base the raw outputs (canonical JSON serialisation, not canon-processed) of every module on G5's inputs, and of the 4 differential suites (3,866 + 471 + 233 + 1,916 cases), as golden JSON under `tests/snapshots/`. Add two fixtures: **distinct papers forced to one id** (debruine-fret + debruine-child as `X`; expected = the per-paper snapshots concatenated under F6's ids) and **one body row duplicated exactly** (expected = the unduplicated snapshot). Snapshots change only through reviewed U/D lock diffs. |

**HARNESS** (4.5 days; phase 0):

| ID | Change |
|---|---|
| H0-1 | `parity check --strict`: a tier-2 `py_changed` fails (289 of 772 tier-2 module cases only warn today); a re-lock needs a reviewed diff |
| H0-2 | Report tables pinned: `ReportTable.to_canonical()` adds column names and a cell hash, `run_cases.R` emits the same, and goldens are regenerated with R (59 `scroll_table` call sites) |
| H0-3 | New offline accuracy rows with R goldens: `power`; paper-side `repo_check` (stubbed listing); extras (`structure`, `codebook_vars`, `gated_repos`, `naming_issues`); a default-preset chain row minus network modules; a 3-paper `PaperList`; psychds and reproducibility plan-only; miscite and RetractionWatch DOIs; an opt-in row-order check |
| H0-4 | Performance in the accuracy report: CPU per (module, input) and the counters, collected by wrapping legacy entry points and an audit hook, so `src/` is not touched |
| H0-5 | `python -m parity bench [--base TREE] [--check]`, seeded from today's baseline, with a **materialised-paper row** and a **mixed-chain counter row** (migrated + unmigrated modules + a user DataFrame edit) |
| H0-6 | `parity batch-equality --level shared` |
| H0-7 | Fix stat_check's shared-paper scipy `_docscrape` leak; adopt one read-only paper per input (−1.0 s) and the harness repository session (−1.9 s) **(M)** |
| H0-8 | Differential suites committed as `tests/core/test_diff_*.py`, comparing against SNAP's snapshots (no frozen façade copies) |
| H0-9 | Mutation-guard test: an in-place `p.text` edit between two `module_run` calls, **and inside `run_session`**, gives fresh results |
| H0-10 | `docs/PARITY.md`: counts (9,474 cases) and the new gates |
| H0-11 | CI: G7's store job; a nightly idle-runner job for G1, G2, G4 (hard budgets) and G8. **Fresh-worktree zero:** `file_location` paths realpath-normalised on both sides (7 unexplained in worktrees today, **S**); the 8 environment failures (**S**, also on base) fixed or listed in `parity/quarantine.yaml` with reasons and a count ratchet; a CI job that a fresh worktree gives 0 unexplained and 0 fail |
| H0-13 | **Coverage ratchet:** per area, the case count and the number of distinct realistic inputs reached through the public API may not drop unless a reviewed `parity/retired.yaml` maps each retired case to a replacement on a public surface; every PR reports retired and re-pointed counts (1,497 cases target private helpers today, **M**) |
| H0-14 | Layering lint derived from imports (§2.1); no per-package list |
| H0-15 | **Parallel-safe bookkeeping:** divergences load from `parity/divergences/**/*.yaml`, with new entries in `parity/divergences/<package>.yaml`; U/D entries use slugs (`U-refs-pubpeer-na`) in `docs/upstream_issues/<package>.md` fragments, numbered at CLOSE (the table ends at U158; the paused lanes reserved numbers up to U194) |

**HARNESS-v2** (8.5 days; phase A, after HARNESS and in parallel with CORE-1b, which stays on the byte-identical bar against SNAP; revision 3; FIDELITY.md §11). It replaces H0-15 and amends H0-2 and H0-13, which HARNESS builds directly in their amended form, so nothing is built twice. H0-1 is kept; a tier-2 change is fixed by adding a row:

| ID | Change |
|---|---|
| H0-17 | `parity/canon.py` and `parity/canon.toml` (row keys per module; key collisions compared as full-row multisets; `ordered` and `na_strict` for the permanent façades), with the known-difference tests in `tests/parity/test_canon.py` and the flip audit (below) |
| H0-18 | The mark migration: the 1,298 case marks become about 200-280 (U-entry × area) rows before Band B removal, fewer after, in `parity/deviations/<area>.toml`, starting with the validated modules. Every case under a row keeps its per-case lock entry; `scripts/migrate_marks.py` and its reviewed output |
| H0-19 | The determinism test: two `PYTHONHASHSEED` values give identical raw outputs |
| H0-2 (amended) | Report tables are compared through canon as row multisets, the same rule as `table`; cell formatting is Band B |
| H0-13 (amended) | The ratchet counts distinct realistic inputs reached through public entry points. The 1,497 private-helper cases are re-pointed (FIDELITY.md §6) |
| H0-15 (replaced) | The deviation rows replace the U/D slugs; CLOSE no longer numbers U entries |
| CODEOWNERS | The validator group owns `parity/deviations/<validated area>.toml`, `parity/canon.toml` and the validated modules' source, with required code-owner review; the sign-off lint checks that an approver is in that group. The pytacheck maintainer adds the names the team gives (ECOSYSTEM.md §8.1); until then the entries name the maintainer, and a Band A change in a validated module does not merge without the team's written approval on the PR (FIDELITY.md §2.2, rule 1) |
| Porting map and sync | `porting/modules.toml` replaces `porting/map/*.toml`, `symbols.json` shrinks to at most 431 entries, and the upstream sync compares behaviour (FIDELITY.md §7); plus the top-level NOTICE (1.5 d of the 8.5) |

**The flip audit replaces the re-lock gate.** "0 Band A drift at re-lock" compared canon with itself, so it could not fail. Instead, for each canon rule, the harness runs the raw R-locked outputs through canon and reports every pair whose raw digests differ but whose canonical digests match, and the old and new comparators run side by side over the 9,474 cases, listing every case that flips to pass or xpass with the paths canon removed. Each removed path must fall in a declared Band B category; any such pair in Band A fails the gate. A human reviews the flips in validated and candidate modules. The audit reruns whenever `canon.toml` changes. Until HARNESS-v2 lands, CORE-0's byte-identical bar holds.

**HARNESS-NET** (2 days; after HARNESS; blocks the behaviour PRs of REFS and LINKS):

| ID | Change |
|---|---|
| H0-16 | Replayed end-to-end rows: R runs on 3-5 real papers with HTTP traffic recorded into httpmock cassettes (the existing R-recorded-mock mechanism), for causal_claims, ref_pubpeer, prereg_check, reg_check, and a `ref_summary` chain that includes the replayed network modules. Accuracy rows run over the replays on both sides. |

### 4.3 Work packages

Each package has one owner and **exclusive file ownership** while active (§4.5 lists the shared files). Efforts are agent-days **(E)**. Each package also carries the gates of §3.3 that name it.

**SNAP** (1 d). Files: `tests/snapshots/**`, `scripts/record_snapshots.py`. Gates: snapshots reproduce on base twice, byte-identical; both fixtures recorded. Depends on: lane 2 merged.

**HARNESS** (**4.5 d**; revision 2: 5). Files: `parity/**`, `report/blocks.py` (`to_canonical` only), `tests/foundation/{test_shared_paper,test_mutation_guard,test_layering}.py`, `tests/core/test_diff_*.py`, `docs/PARITY.md`, `docs/upstream_issues/`, `.github/workflows/**`, `pyproject.toml` (during phase 0). Content: H0-1 to H0-11, H0-13, H0-14, with H0-2 and H0-13 built in their revision 3 form (§4.2) and no H0-15 (HARNESS-v2 replaces it). Gates: the accuracy gate passes with the new rows; full parity shows no new failure; H0-7 changes 0 outputs; fresh-worktree zero. Depends on: none (H0-8 consumes SNAP).

**HARNESS-NET** (2 d). Files: `parity/cassettes/**`, `parity/accuracy/**` (the new rows), `parity/r/**` (recording). Content: H0-16. Gates: the replayed rows pass on base, and R and Python agree on them. Depends on: HARNESS.

**SPIKE-2** (2 d, scratch tree; §7.3). Depends on: none.

**CORE**, owned by the core agent, who stays steward of `core/**` through the waves (§4.5). Output-neutral except for listed U-entries.

| Step | Days | Content | Gates | Deletes |
|---|---:|---|---|---|
| **CORE-0** | 1 | Rebase onto HEAD; land P2-casefold-grepl, P2-bind-rows-fast, P3-codebook-fold-once + P2-codebook-lazy-compile, P4-RW/L7-4, P3-memo-built-tables | accuracy 439/439 byte-identical; G5 against SNAP; the speedups reproduced (35.0 → 30.0 s, **M**). **Done: 34.7 → 16.9 s (C)** | – |
| **CORE-1a** | 1.5 | `detector()`, `required_literals()`, `detect_many()`; I2; **I3 fuzz**; `PYTACHECK_LITERALS=off` | regex replay (30k calls), rcompat 174, text areas; I2 and I3 0 violations | – |
| **CORE-1b** | **5** (unchanged from revision 2) | `core/doc.py`, `patterns.py`, `hits.py` with groups, `paragraphs()`, `sections()` (from SPIKE-2); `_derived` slot, trusted scopes, opaque token; `.tolist()` backing; V7 dedup; thread-safe stages; `papers/ids.py` (F6); façades (`text_search` incl. multi-pattern vectors, `text_expand`, `extract_*`, `paper_id`, one-paper `paper_table`) on the Doc; `Doc.from_records` + lazy `eq` | text 65, text_extract(+review), core 22, bibr12, grobid12, io (each + review), then **full `--strict` parity**; diff suites 0 differences against SNAP; I1 0 mismatches; accuracy identical except the F6 and lane-5 U-entries; counters incl. materialised and mixed-chain rows; `read(xml)` target from SPIKE-2; threaded-stage test; G6 | `_text_frame`, the per-pattern recursion, `_search_table`, the slow `paper_table` path, `_fast_concat`, `_ETHICS_ANY`, `_may_mention_ethics`, `_LIVE_ANY`, `_REPO_ANY`, `_FIRST_STAGE_ANY` and their proof tests; `_paste_groups`, `_semi_join`, `_section_headers` **only if SPIKE-2 passed V5** |
| **CORE-1c** | **1.5** (was 2) | `core/facets.py`, `refs.py` (per-Doc joins), `fuzzy.py`; `ref_table` façade; DOI dictionaries; **`json_expand` → `json.loads` + a flatten** (revision 3: nulls stay None, Band B; ≈ 120 lines instead of ≈ 150 plus the kept 335) | mod_ref_* areas, `ref_table` 14, stats(+review), text_extract(+review); `ref_table` snapshot (merge order, many-to-many); json_expand's 9 + 3 U/D cases | `ref_table`'s merges; `extract.py`'s private pipelines; the jsonlite model (≈ 900 lines) |
| **CORE-1d** | **2.5** (was 2) | `core/run.py`: `RunContext` absorbs `RunSession`; `Settings`; `override`; `RunCache`; plain-paper memo; counters; public `run_session` memo-only; L6-1; `execute()` for all runners; `_resolve`/`_call`/`_assemble`; `module_run_each`; file-module cache; forward-compatible decorator; **revision 3: the enforced `depends=` graph and the dependency rules (§2.7); the six nested re-runs served from the `RunContext` memo, keyed on normalised forwarded arguments; no typed artifacts; `validate.py`'s replays**; `core/warm.py` | 1,048 `module:` cases, modsys 241, report 136, api 10; G8 `shared` and `each`; `Settings` round trip under spawn and forkserver; executor order test; **a gc test that a `RunContext` is collected after its block while its papers live**; the failing-predecessor memo test | duplicated failure outputs (`report/report.py:417-440`, `api/app.py:405-412`), `RunSession` internals, the per-call entry-point scan (`report.DEFAULT_MODULES` goes in TIERS) |
| **CORE-1e** | **2.5** (was 1.5) | `core/output.py`; `_assemble` accepts `Result`; direct calls return `dict`; dict summary merge with the dplyr suffix rule; `pytacheck.doc` + `testing.document()`; **profile and cut the boundary floor**; **revision 3: one `Result` definition, primary; typed report blocks in `report/blocks.py` and `report/render.py`; `scroll_table`/`collapse_section` returning blocks; the fence parser moved to a v1 reader for string reports; `plural`; each built-in's `render()`; `ModuleOutput` built by compat (§2.9)** | twin module (dict vs `Result`) canonical-equal on 24 inputs; clinical_trials renders the same HTML on base and after 1e (G7); merge differential incl. a 3-deep chain; every built-in's direct return is a mutable `dict`; boundary floor measured per output and reported; G6 | the `DataFrame.merge` fallback |

Files held by CORE: `core/**`, `pytacheck/doc/**`, `_r/**` (after lane 2), `papers/**`, `text/search.py`, `text/expand.py`, `text/extract.py`, `text/json_expand.py`, `module.py`, `provenance.py`, `presets.py` (from TIERS's close, day 2.5), `validate.py`, `utils.py`, `config.py`, `src/pytacheck/__init__.py`, `pyproject.toml` (after phase 0); the `report()` loop and the `/paper/check` loop until 1d closes; `report/render.py` and `report/blocks.py` in 1e (then to SERVICES-b); the `io/grobid.py` hook until 1b closes; `packs/registry.py`'s snapshot hook in 1d; the CORE-0 quick-win files (`db/retractionwatch.py`, `db/databases.py`, `modules/ref_retraction.py`, `modules/_codebook.py`, `tests/modsys/test_session.py`; P2-codebook-lazy-compile is `perf/patches/P3-codebook-dict-index.patch` + `measure-codebook-fold-once.patch`) in CORE-0 only. **Checkpoint** at CORE-1d's close (day 13.5): the measured numbers of 1a-1d go to the user before REPO and the other waves start; 1e's boundary floor is reported when 1e closes (day 16).

**Revision 3 packages** (new):

**HARNESS-v2** (8.5 d; phase A; after HARNESS, in parallel with CORE-1b). Files: `parity/**` (after HARNESS closes, minus the carve-outs in §4.5), `tests/parity/**`, `scripts/migrate_marks.py`, `docs/PARITY.md`, `.github/CODEOWNERS`, `porting/modules.toml`, `porting/map/**` (removed), `porting/symbols.json`, the upstream-sync script and prompt, `NOTICE`. Content: H0-17 to H0-19, the amendments, CODEOWNERS and the porting-map step in §4.2. It re-points the private cases of CORE-1b's deleted helpers first, as CORE-1b's change requests. Its results-only accuracy step runs after HARNESS-NET closes (day 6.5), and the porting-map step last. Gates: the flip audit (§4.2) with no Band A pair and the validated and candidate flips reviewed; every retired mark maps to a row or to a Band B difference, and every case under a row keeps its lock entry; H0-13's input count does not drop; two hash seeds give identical raw outputs; the sign-off lint checks the approver; `symbols.json` at most 431 entries. Depends on: SNAP, HARNESS. Off the critical path: CORE-1b stays byte-identical to SNAP, and CORE-1c's first Band B change needs only the canonicaliser and the flip audit (≈ day 7). FIDELITY.md §11 has the step breakdown.

**TIERS** (2.5 d; phase A). Files: `presets.py` (until it closes at day 2.5), `src/pytacheck/resources/status/validation.json` (a snapshot of the team's registry, marked `provisional: true` until the team answers), `report/` (the badge and the validation box only), `cli.py` (`status`, `--status`, the `init` question), `tests/status/**`, the five tests that pin `metacheck::validated` (ECOSYSTEM.md §3.2), `docs/MIGRATING.md` (first entry). Content: ECOSYSTEM.md §2-3: derived labels, policies, `status=` on every surface, `metacheck::validated` as a deprecated alias of `metacheck::default` with `status="validated"` until 1.0, report badges; the removal of `DEFAULT_MODULES`, sent as a change request to the core agent. Before TIERS closes, the maintainer sends the §3.1 draft and the questions to the metacheck team (ECOSYSTEM.md §8.1). Gates: G9; `report(paper)` unchanged under the library default `experimental`; an installed store module named by a preset still runs under that default; the API refuses a looser status than its ceiling. Depends on: none.

**STORE-2a** (1.5 d; phase A; after TIERS). Files: `packs/**` except the §4.11 rows (before MODSYS), the store repository (with the maintainer's permission). Content: ECOSYSTEM.md §4.3 (without the re-pin bot), §4.4, §4.5, §4.7's `CATALOG.md`, the §4.8 additions and §4.10; the §6 store fixes. The project-config code-trust gate stays. Gates: G7; `CATALOG.md` builds; the security baseline rejects reserved and near-duplicate names. Depends on: TIERS.

**STORE-2b** (1.5-2 d, planned at 2; after decision 13, after both `scienceverse/pytacheck` and `scienceverse/pytacheck-modules` are public and index.json is rebuilt from the public store, and after CORE-1d closes). Files: the §4.11 rows of `packs/**`. The `module.py`, `config.py` and `registry.py` rows go as change requests to the core agent. Content: ECOSYSTEM.md §4.11's deletions. Private GitHub stores keep working through the token-only path (host allowlist, cross-origin strip, credential-free records). It writes the `docs/MIGRATING.md` entry for the removals, with the token setup as the before/after example (§4.6). Gates: G7; the token path's tests pass; no pin or run record holds a credential. Until its preconditions hold, STORE-2b does not start, and nothing else waits for it.

**COMPAT** (2.5 d; phase C; after PATTERNS, STATS and REFS, days 21-23.5). Files: `compat/**` (new), `src/pytacheck/__init__.py` (from the core agent), `docs/MIGRATING.md`. Content: `pc.check()` and `Report`, `pc.configure`, `pc.status`, `pc.status_table` (the surface of ECOSYSTEM.md §3.3); the 303 R-shaped names in `pytacheck.compat`, on the existing `_EXPORTS` lazy map plus a deprecation hook; deprecated top-level re-exports until 1.0; `pc.hosts` with the `Host` protocol, and the list of what happens to the 25 non-protocol host functions (decision 15). Gates: every name in today's `_EXPORTS` resolves from compat; the parity harness runs on compat with no case changes; an old-style script runs with deprecation warnings only. Depends on: CORE-1e.

**PORT** (5 d; phase D; after the day-11.5 stop point, days 11.5-16.5). Files: `src/pytacheck/port/**` (new), `docs/PORTING.md`, `porting/ports/**` (new: consent and translation records). `porting/modules.toml` and `symbols.json` belong to HARNESS-v2, then the core agent (§4.5). Content: ECOSYSTEM.md §5: `pytacheck port init/translate/diff/finish/update`, the licence check, the consent record, and the differential check against R, which needs a local R and metacheck and runs one `Rscript` per diff. There is no persistent R worker and no `--allow-r` runner. The translator writes `.py` against `pytacheck.doc`. Gates: a round-trip port of an upstream module (for example `marginal.R`) passes `port diff` at the §5.2 thresholds, since no third-party R module is known today; a port without a consent record cannot be listed. An external pilot needs a named module and author with consent in hand before PORT starts. Depends on: HARNESS-v2's canonicaliser (H0-17, ≈ day 6), STORE-2a.

**TOML** is not a package in revision 3: decision 2 reverts to (c), so declarative checks wait until a store author or the translator needs them (§2.10).

**WEB** (2.5 d; phase D; after TIERS and CORE-1d, days 13.5-16, so it relies on the `depends=` closure for network needs). Files: `src/pytacheck/api/web.py` (new; it mounts itself on `create_app()`, so `api/app.py` is not edited), `cli.py` (`serve --app` only), `tests/web/**`, the `[app]` extra in `pyproject.toml` (through the core agent). Content: §3.7: the spike's app on the public report functions, using the compat names deliberately (compat keeps them, so later packages change the report under it without editing it; CLOSE moves it to `pc.check(...).to_html()`); TIERS's status policy with the server's ceiling and the hosted filter; `server_safe` read from the `validation.json` snapshot; a per-job wall-clock limit in a process-pool worker; `gradio>=6.28,<7` in `[app]`; the fixed settings, each with a unit test; `GET /source` and the visible "Source code" link (AGPL §13); the privacy text, including the upload retention; plain-language errors, the cooldown and the queue limit; the anonymous usage counts; the PDF path and the network options measured. Gates: the base and `[api]` installs import no gradio; the app loads under `[app]`; nightly, no third-party host is contacted at startup or during a demo run, through a logging proxy, and `/app/gradio_api/info` lists nothing; a nightly Playwright run of upload → report → download, with the report's JS working in the iframe; the 3-job queue test; a request for a looser status than the ceiling is refused with a 400 naming the modules; `/source` names the running commit and each active pack's source, rev, tree hash and licence, and the UI shows the link. Depends on: TIERS, CORE-1d.

**Changes to revision 2's packages:**

| Package | Days | Change |
|---|---|---|
| CORE-1c | −0.5 | `json_expand` shrinks (§3.5) |
| CORE-1d | +0.5 | the `depends=` graph and the dependency rules, served from the run memo; no typed artifacts (§2.7) |
| CORE-1e | +1 | `Result` primary, blocks, per-check `render()`, the v1 fence reader (§2.9); no templates |
| STATS | +1 | the statcheck R runtime and nmath → Optional values and `scipy.special`; the p-value facet |
| LINKS | +2 | the shared `archives/fetch.py` download engine |
| LLM-a | +1.5 | the ellmer port → a thin client (decision 16) |
| LLM-b | +0.5 | the web app's LLM option (§3.7) |
| SERVICES-a | +1 | one Grobid converter; statout floats until the boundary; jasp typing |
| SERVICES-b | +1 | the report deparser removed; `api/jsonlite.py` → plain JSON; `zenodo_upload` split out |
| CODE-a | +1 | `_rparse` messages and AST trims |
| DATA-a | +0.5 | calamine for xlsx and ODS; the ODS walker goes |
| REFS | +0.5 | ref_summary and the per-module joins as dict rows |
| BATCH-a | +0.5 | the second HTTP stack folded into `http.request` |
| REPO, DATA-b, CODE-b | +1 each | the repository sub-checks and the monolith split (§3.5) |
| CLOSE | +0.5 | the final test sweep with a measured before and after (§5.3), WEB moved onto `pc.check`, the release notes (§4.6); no renames (decision 21) |
| HARNESS | −0.5 | H0-15 is not built, and H0-2 and H0-13 are built once, in their revision 3 form |

**MODSYS** (1.5 d; after CORE-1e). Files: `packs/**`, `resources/templates/**`, `docs/MODULES.md`. Content: `requires.pytacheck` on release tuples, a warning at load, an error in `pack check` and `store build --check` (decision 7); the static `requires` check for packs importing `pytacheck.doc`; `module_api` read and ignored; the author guide; `run_session`'s contract text. Gates: modsys 241; G7 on a `.devN` build; an old-style pack loads; a pack with an unsatisfied `requires` warns at load and fails `pack check`.

**PATTERNS / W1** (5 d; after CORE-1e). Files: `modules/{ethics_check,open_practices,all_urls,coi_check,coi_check_oi,funding_check,funding_check_oi,_funding,power,_power,marginal}.py`, `tests/{mod_ethics,mod_urls_open,mod_coi,mod_funding,mod_power,mod_marginal}/**`. Order, one PR each: ethics_check (spiked); open_practices + all_urls; coi_check + coi_check_oi (section scope, from SPIKE-2); funding_check + funding_check_oi + `_funding` (`_Article` → Doc; synonyms and 34 `get_*` locators take `(doc, pattern)`; document order, decision 1 b, noted in the release notes); power + `_power` (paragraph groups); marginal. Gates: G1 on each area + review and text_extract(+review); G2 incl. the power row; G4; G5; G6; **re-measure the §5.1 budgets and make them hard**. Deletes: `_search_frame`, `_chain`, `_full_join`, `_arrange`, `_summarise`, `_paper_ids`, `_to_title_case`, NA shims, `_Article`.

**STATS / W2** (**5 d**, was 4; after CORE-1e). Files: `modules/{all_p_values,stat_p_exact,stat_p_nonsig,stat_check,stat_effect_size}.py`, `stats/**`, `text/extract_tests.py`, `tests/{mod_p_values,mod_effect_size,stats}/**`. Content: the p-value trio on `p_values()` (star test from the span; no `grepl`); stat_check on `statcheck()` with keyword `stats()`, three-valued logic kept; stat_effect_size on `equations()` with plain floats (no `r15`). Gates: G1 on mod_p_values, mod_effect_size, stats (+ review); effect-size table identical on 12,780 + 2,790 cells (`c_quirk` only for `strtod_long_double` and `as_character_near_ties`, tier 2); G2, G4, G5. Deletes: long-double `R_strtod`/`formatReal` (≈ 225 lines), `_match_statcheck_args`, `_icu_rank`, the three p-value pipelines.

**REFS / W3** (**5 d**, was 4.5; after CORE-1e; behaviour PRs after HARNESS-NET). Files: `modules/{ref_accuracy,ref_consistency,ref_miscitation,ref_pubpeer,ref_replication,ref_retraction,ref_summary,causal_claims}.py`, `text/causal.py`, `db/**` except `regcheck*.py`, `tests/{mod_ref_accuracy,mod_ref_db,mod_ref_pubpeer_summary,mod_causal,db}/**`. Order: **ref_consistency first, with per-Doc joins, before any other ref_* module** (Review B-1); the other ref_* modules on `RefIndex` records; ref_summary's 260-line join engine → dict rows with `depends=` for its 4 predecessors; db L7-1 to L7-3 and one JSON path; then the behaviour PRs: ref_pubpeer NA per failed chunk (L5-9), causal_claims dedup, 2 in flight, per-sentence isolation, each paper's own title (L5-8). Gates: G1 on mod_ref_accuracy, mod_ref_db, mod_ref_pubpeer_summary, mod_causal, db (+ review, httpmock replays); G2 incl. miscite/RW rows and HARNESS-NET's rows; G5 incl. the distinct-papers fixture; causal request bodies byte-identical; G4. Deletes: the 7 join helpers, `_key_kind`/`_align_key`, `_refs_with_doi` copies, pivot emulations, dplyr error replays.

**LINKS / W4** (**8 d**: 5.5 in revision 2, +2 in revision 3, +0.5 from REPO_FETCH.md; after CORE-1e; behaviour PRs after HARNESS-NET; step 5 after REPO). Files: `archives/**` except `archives/listing.py` while REPO holds it, `modules/{prereg_check,_prereg,reg_check}.py`, `db/regcheck.py`, `db/regcheck_local.py`, `tests/{archives_d1,archives_d2,archives_gz,archives_osf,repo_download,mod_prereg,mod_reg}/**`. Order: `archives/links.py` behind `links()` (facet hook via the core steward), `*_links` as façades; prereg_check (pure rewrite), then the `_prereg` flatten and `osf_type` via the run cache; reg_check (pure rewrite), then RegCheck submit-then-poll (L7-7); lane 7's archive units; step 5: host clients return `FileEntry` behind REPO's `archives/listing.py`. The `archives/fetch.py` engine is REPO_FETCH.md's one download scheduler (RF-8), with the GitHub zipball capped by the want-set (U-5); it reads and writes file blobs through CACHE's store, by the listing hash, and keeps a downloaded zip in the run directory for other modules (RF-9). Gates: G1 on the four archives areas, repo_download, mod_prereg, mod_reg (+ review); 502/502 bodies; 68/70 zips; the paper-side repo_check row; link detection ≤ 25 ms/paper (205 today, **M**); G4, G5. Deletes: the 14 detectors' `text_search` pipelines, `dataverse._search_frame`, `dataone._scan_links`, `_tre_wide`, the yajl/JSON shims, zip internals, the `_prereg` value model.

**REPO** (**5 d**, was 4; after CORE-1d and FETCH). Files: `repository/**` (new), `archives/listing.py` (new, handed to LINKS at close), `modules/{repo_check,_repo_check,_schemas}.py`, `fileinfo/**`, `tests/mod_repo_check/**`. Content: freeze the extras schemas first (`structure`, `previews`, `codebook_vars`, `gated_repos`, `naming_issues`, `repo_metadata`, `version_pin`) with snapshot tests; `RepoIndex` with delegating parse methods and a `FileEntry` adapter over today's listing frames; repo_check as a records-first renderer with host threads, per-repository isolation and `paper_id` per row (L7-6, U); FETCH's per-host queues kept; archives shared across modules in one run through `RepoIndex.fetch` (RF-9; LINKS keeps the zip, CODE-b moves code_check onto it). Gates: G1 on mod_repo_check(+review), with the error-text cases passing by presence; G2 on the repository block; file opens ≤ 23 on the traced chain; G5.

**DATA-a** (**3.5 d**, was 3; after HARNESS) and **DATA-b** (**6 d**, was 5; after REPO and DATA-a). Files: `datacheck/**`, `modules/{data_check,_data_check,codebook_check,_codebook,psychds_check}.py` (DATA-b; `_codebook.py` after CORE-0), `tests/{datacheck_*,mod_data_check,mod_codebook,mod_psychds}/**`. Content: §3.3 lane 3; psychds_check as a pure rewrite PR first; DATA-b breaks the data_check/codebook_check cycle, since it owns both files (§2.7). Gates: lane 3's gates; G1 on the datacheck_* areas, mod_data_check, mod_codebook, mod_psychds (+ review); G2 incl. extras and the psychds plan row; codebook scan of the 94K paper ≤ 0.5 s; G5. Deletes: `_files_fread.py`, `_files_readtable.py`, the readxl XML reader, `_LineReader`, `_checks_rvec.py`, `_stats_frame`/`_stack_stats`, the fread fuzz tests.

**CODE-a** (**4 d**, was 3; after HARNESS) and **CODE-b** (**5 d**, was 4; after REPO and CODE-a). Files: `codecheck/**`, `repro/**`, `modules/{code_check,_code_check,reproducibility_check,_reproducibility}.py` (CODE-b), `scripts/gen_rparse_tables.py`, `tests/{codecheck,mod_code,mod_repro,repro_core}/**`. Content: §3.3 lane 4; the scoped LLM override; reproducibility_check as a pure rewrite PR first. Gates: lane 4's gates; G1 on codecheck, mod_code, mod_repro, repro_core (+ review); G2 incl. the plan-only row; G5. Deletes: `_encoding.py`, `_icu.py`, `_icu_tables.py`, `_purl.py`, `_reval.py`, `_rjson.py`'s parser, `RNamedList`, the ICU-pinning tests.

**LLM-a** (**3.5 d**, was 2; after HARNESS) and **LLM-b** (**1.5 d**, was 1; after CORE-1d, LLM-a and WEB). Files: `llm/**`, `tests/llm/**`, and `api/web.py` in LLM-b. LLM-a: lane 6's de-emulation units. LLM-b: `Settings` wiring, scoped overrides, N5, L6-2 to L6-6, typed structured results with source references for grounding; the web app's LLM option, once LLM settings are per run (§3.7). Gates: lane 6's gates; G1 on llm(+review) with 0 changes in mod_power, mod_codebook, mod_data_check, mod_reg and mod_causal. Deletes: `_Lexer`, `parse_json`, `_render`, `Vec`/`RInt`, the `.rds` cache, `EllmerOutput`, the S7 printer, `_friendly`, the `_cli_*` helpers.

**SERVICES-a** (**3 d**, was 2; after HARNESS) and **SERVICES-b** (**2.5 d**, was 1.5; after CORE-1e and SERVICES-a, days 16-18.5). Files: `io/**` (the grobid hook after CORE-1b), `statout/**`, `report/**` except `blocks.to_canonical` (SERVICES-b; `report/render.py` and `report/blocks.py` pass to it when CORE-1e closes), `api/**` except `api/web.py` (SERVICES-b), `tests/{io,bibr12,grobid12,statout_*,report,api}/**`. SERVICES-a: statout, timestamps, corpus RDS, `bibr_convert`, R-internal warnings. SERVICES-b: `_Deparser` → R-literal writer; `read_plan`/`read_one`, `read(workers=)`; L7-7 to L7-9; the report's "Source" footer (AGPL §13, ECOSYSTEM.md §5.4), a block built from the data `GET /source` returns, inside its 2.5 days. Gates: lane 7's SERVICES gates; G1 on io, bibr12, grobid12, statout_*, report (+ review); the api tests; the report footer names the running commit and each active pack; `read(xml)` ≤ SPIKE-2's target. Deletes: the SPV R evaluator, the civil-date and `R_strtod` ports, `_RdsReader`, `_Deparser` and its Unicode tables.

**BATCH-a** (**3 d**: 1.5 in revision 2, +0.5 in revision 3, +1 from REPO_FETCH.md; after HARNESS, placed at days 6.5-9.5 to hold parallelism to 7) and **BATCH-b** (6 d; after CORE-1d and SERVICES-b). Files: `http.py`, `log.py`, `cli.py`, `batch/**` (new), `parity/batch.py` (from HARNESS), `docs/BATCH.md`, `tests/batch/**`. BATCH-a: BL-1 (N1 limiter, rolling window), BL-4 (per-process logs); from REPO_FETCH.md: HTTP/1.1 on the shared client with its test and pool sizing (RF-1), `HostPolicy` path scopes with several windows, an identity dimension and sliding-window logs, the OSF and GitHub policies (§2.2 there), and an empty cookie jar for OSF hosts. BATCH-b: BL-2, BL-5 to BL-7, H2 (a, c, d), BL-9; BL-8 only if decision 3's gate calls for it. Gates: G8 `batch`; a 1,000-paper synthetic run under `jobs` × 300 MB RSS; the fault test; CLI tests; httpmock replays unchanged; `io/bench_window.py` ≥ 2x. Deletes: per-call `Throttle`; `_host_reset`.

**FETCH** (**4 d**; new, from REPO_FETCH.md; after BATCH-a, days 9.5-13.5). Files: the network paths in `archives/**` (`osf.py`, `osf_helpers.py`, `zip_peek.py`, `github.py`, `gitlab.py`, the dataset clients' `_query` and `repo_info_cache` call sites, and in `download.py` `_remote_content_length` (`:615-633`), the OSF zip gate (`:1731-1740`), the Git repository reuse and the GitHub zipball size HEAD with the warnings that read it (`:1906-1951`) and the member path (`:1452-1500`)), `modules/{repo_check,_repo_check}.py` (the listing loops and `_peek_zips`), `modules/data_check.py` (the zip-peek loop, `:884-901`, for RF-6; the zip-gate count, `:926-940`, for U-1), `tests/{mod_repo_check,mod_data_check,archives_d1,archives_d2,archives_gz,archives_osf,repo_download}/**` (`mod_data_check` until DATA-b), and a new replayed row in `parity/cassettes/**` through the `parity/**` owner. Content: REPO_FETCH.md RF-2's call sites and RF-3 to RF-7; U-1 to U-4; the OSF tree-vector and git-SHA validators, on CACHE's API; the `repo_info_cache` call sites moved onto the store. `repo_check.py` and `_repo_check.py` come first (days 9.5-11), since REPO takes them at day 13.5. Gates: G1 on the archives areas, repo_download, mod_repo_check and mod_data_check (+ review), with fixtures re-recorded where request counts change (BATCH R4); the replayed row, `repo_check` and `data_check` on the demo, psy737 and a GitHub + Zenodo paper, recorded live once with HARNESS-NET's mechanism; frames identical with and without RF-4′ on the recorded trees; with the store on, the replayed row cold and then warm gives identical outputs; recorded vector fixtures (a child's date changed, a node added or removed, a node missing from a token caller's vector, a vector over 2 pages that skips or repeats a node, a component link against a root link, a view-only link) each force a re-list; deviation rows for U-1 and U-2, and for OSF file `downloads` counts served from the store (REPO_FETCH.md §5.2), with the note in `osf_info()`'s and `osf_file_download()`'s docs. A token-bearing OSF fixture needs two OSF test accounts; until they exist, RF-4′ runs only without a token. Depends on: BATCH-a, and CACHE's store protocol (day 10.5) for the validators.

**CACHE** (**4 d**, ≈ 3 under decision 22 (a2), which defers the Redis backend, the compose service and the Valkey CI test; new, from REPO_FETCH.md; after BATCH-a, days 9.5-13.5, beside FETCH). Files: `cache/**` (new), `archives/{info_cache,cache}.py`, the ledger hook in `http.py` (between BATCH-a's close and BATCH-b), `docker-compose.yml`, `README.md` (the Docker volume only), `docs/CACHE.md`, `tests/cache/**`, `cli.py` (`--cache-store` only, after BATCH-a); `config.py`, the `redis` extra in `pyproject.toml`, `provenance.py` (the run-record fields), `core/run.py` (`RunContext.store`) and the Valkey service in `.github/workflows/**`, through the core agent. Content: REPO_FETCH.md §5 and §6.3: the store protocol first (by day 10.5, so FETCH can build its validators on it), the file backend, the blob store, the request ledger, the Redis/Valkey backend and the bundled Valkey service, the `cache_store` option and run-record fields, and the dead settings. Gates: the ledger's windows tested with a fake clock across two processes, as a sliding log; a Redis test against a Valkey service container in CI; fail-open when the backend is down; no credentialed or view-only response in the networked tier, and no `anon` OSF tree or id type served to a token caller; a deviation row under FIDELITY.md for a request the ledger refuses (decision 23), with a test that `skip_on_api_limit` fails it at once; with the store off, full parity and the accuracy gate byte-identical to today; NFS-safe writes. Depends on: BATCH-a.

**CLOSE** (**2.5 d**, was 2; after all; core agent). Shrink `docs/UPSTREAM_ISSUES.md` to the Band A metacheck bugs, each linked from its rows (FIDELITY.md §3.2); no directory renames (decision 21); the final test sweep, measured before and after (§5.3); WEB moved onto `pc.check`; ratchet G6 (no module imports `_r.frames`); remove helpers without importers; docs (`docs/ARCHITECTURE.md`, `MODULES.md`, `PORTING.md`, the upstream-sync prompt); CHANGELOG and `docs/MIGRATING.md` for the release (§4.6); re-measure on an idle machine and replace the (E) figures. Gates: one green nightly on main (G1-G9, idle G4).

### 4.4 Schedule

Revision 3 runs in four phases. They overlap: a phase is defined by what it delivers, not by its dates.

| Phase | Days | Packages | Delivers | Stop point |
|---|---|---|---|---|
| **A, contract and ecosystem** | 0-13 | SNAP, HARNESS, HARNESS-v2, HARNESS-NET, SPIKE-2, TIERS, STORE-2a | the three fidelity bands, enforced by the harness; status labels, policies and presets; the store fixes and catalog. TIERS and STORE-2a can ship in a release on their own (0.4, §4.6), before any module is rewritten | after SPIKE-2 (day 2); at day 11.5, when the user reviews the deviation rows that the mark migration produced and the metacheck team's answers (ECOSYSTEM.md §8.1). Without the answers, phase A still closes, with a provisional snapshot |
| **B, the core** | 1-16 | CORE-1a to 1e, the first halves that need only HARNESS (LLM-a, SERVICES-a, DATA-a, CODE-a, BATCH-a), and FETCH and CACHE after BATCH-a | `Doc`, the pattern engine, facets, the enforced `depends=` graph, `Result` and typed blocks; batched repository fetches and the cache store (REPO_FETCH.md) | the CORE checkpoint at CORE-1d's close (day 13.5): measured numbers go to the user before any wave starts |
| **C, the waves** | 13.5-27 | REPO, LLM-b, SERVICES-b, BATCH-b, MODSYS, COMPAT, PATTERNS, STATS, REFS, LINKS, DATA-b, CODE-b, CLOSE | every module on the core; `pc.check` and `pytacheck.compat`; ≈ 93,300 lines | CLOSE (day 27) |
| **D, porting, the web app and the store deletions** | 11.5-16.5 | PORT, WEB, STORE-2b | `pytacheck port` and the round-trip pilot; the web app as `pytacheck[app]`; the ECOSYSTEM.md §4.11 deletions once their preconditions hold | independent of C: stopping PORT, WEB or STORE-2b does not hold up the rewrite |

```
day             0   2   4   6   8   10  12  14  16  18  20  22  24  26  28
SNAP            ██
HARNESS         █████████
SPIKE-2         ████
TIERS/STORE-2a  ████████
CORE-1a           ███
HARNESS-v2               █████████████████
HARNESS-NET              ████
CORE-1b                  ██████████
CORE-1c/1d/1e                      █████████████
LLM-a / LLM-b            ███████                 ███
SERVICES-a/-b            ██████                 █████
DATA-a                   ███████
CODE-a                   ████████
BATCH-a / -b                 ██████                  ████████████
FETCH                              ████████
CACHE                              ████████
PORT                                   ██████████
REPO                                       ██████████
WEB                                        █████
STORE-2b                                   ████
PATTERNS                                        ██████████
STATS                                           ██████████
REFS                                            ██████████
LINKS                                           ████████████████
COMPAT                                                    █████
MODSYS                                                    ███
DATA-b                                               ████████████
CODE-b                                               ██████████
CLOSE                                                            █████
```

- **Critical path:** HARNESS (4.5) → CORE-1b (5) → 1c (1.5) → 1d (2.5) → REPO (5) → DATA-b (6) → CLOSE (2.5) ≈ **27 agent-days**, ≈ 31 with the contingency below. It ties with the path through CORE-1e (2.5), SERVICES-b (2.5) and BATCH-b (6). The next path, through CORE-1e and LINKS (8), is 26.5 days. Revision 2: 25.
- **FETCH → REPO has no slack.** HARNESS (4.5) → BATCH-a (placed at 6.5-9.5) → FETCH (4) ends at day 13.5, the day CORE-1d closes and REPO takes over `repo_check.py` and `_repo_check.py`. A day's slip in BATCH-a or FETCH, or in CACHE's store protocol (day 10.5), moves REPO, and so the end, by a day; FETCH lands those two files first (REPO_FETCH.md §8).
- **HARNESS-v2 is off the critical path.** It runs in parallel with CORE-1b, which is checked against SNAP's raw snapshots under today's byte-identical bar. HARNESS-v2 re-points the private cases of CORE-1b's deleted helpers first, as CORE-1b's change requests; from its close (day 13) each deleting PR re-points or retires its own private cases. CORE-1c's first Band B change (`json_expand`'s nulls) needs only the canonicaliser and the flip audit, done by about day 7. The waves need the deviation rows, done by day 11.5.
- **The checkpoint is a real stop.** It is at CORE-1d's close (day 13.5), before REPO, WEB, STORE-2b and the waves start. Each day the user takes to review it moves the end by a day.
- **Total:**

  | | Days (E) |
  |---|---:|
  | Revision 2's packages | 78.5 |
  | New packages: HARNESS-v2 8.5, TIERS 2.5, STORE-2a 1.5, STORE-2b 2, COMPAT 2.5, PORT 5, WEB 2.5 | + 24.5 |
  | Changes to revision 2's packages (§4.3), including HARNESS −0.5, CORE-1d +0.5, CORE-1e +1, CLOSE +0.5 and LLM-b +0.5 | + 13.5 |
  | Revision 3 before REPO_FETCH.md, in 34 packages | 116.5 |
  | REPO_FETCH.md: FETCH 4 and CACHE 4 (new), BATCH-a +1, LINKS +0.5 | + 9.5 |
  | **Total, in 36 packages** | **≈ 126** |
  | Already done (CORE-0) | 1 |

  The contingency is calendar slack on the critical path, not package-days: ≈ 4 days, sized as the ≈ 10% of the 39.5 package-days that carried marks and U write-ups per PR, which revision 3 no longer needs. It is not in the 126 total and not taken out of any package; it is the margin between 27 and ≈ 31 days.
- **Peak parallelism:** 7 agents on days 4.5-6.5 (HARNESS-v2, HARNESS-NET, CORE-1b, LLM-a, SERVICES-a, DATA-a and CODE-a are separate packages with separate owners), 16-18 and 18.5-21, not counting the core agent as steward. Revision 2: 7. That comes from placing BATCH-a at 6.5-9.5 (after HARNESS-NET closes), PORT after the day-11.5 stop point, LLM-b at 16.5-18 and MODSYS at 21-22.5. PORT, LLM-b and MODSYS are on no critical path. BATCH-a, as placed, now is: it feeds FETCH and REPO with no float (the bullet on FETCH → REPO).
- **The middle:** before REPO_FETCH.md, days 8.5-11.5 held only HARNESS-v2 and the core agent. Now BATCH-a's third day fills 8.5-9.5, and FETCH and CACHE run 9.5-13.5 beside HARNESS-v2, the core agent and, from day 11.5, PORT: at most 4 agents besides the core agent. WEB cannot start there, since it needs CORE-1d.
- **Stop points:** after SPIKE-2 (day 2); at day 11.5 (the deviation rows and the team's answers); at the CORE checkpoint (day 13.5).

### 4.5 Coexistence, deletion and ownership rules

**Coexistence.** One shipped implementation per module and no runtime switch. Mixed chains just work: `_assemble` normalises a dict, a DataFrame or a `Result`, and unmigrated modules call the façades on the same Doc and RefIndex.

**Deletion.** A module's private R machinery goes in that module's migration PR (the snapshots are the oracle). Façade internals go in CORE 1b after the differential suites pass, and the grouped-mode helpers only after SPIKE-2's V5. Emulation stacks go in the PR that switches the adapter, after that lane's corpus gate passes. Public façades are never deleted. Private-helper parity cases (1,497) are re-pointed or retired under H0-13's ratchet, with `porting/symbols.json` and `porting/modules.toml` updated in the same PR. Until HARNESS-v2 closes (day 13), it re-points CORE-1b's cases as CORE-1b's change requests; after that, each deleting PR re-points or retires its own.

**Shared files** (Review B-14):

| File or area | Rule |
|---|---|
| `core/**`, `pytacheck/doc/**`, `text/extract.py`, `text/json_expand.py`, `src/pytacheck/__init__.py`, `pyproject.toml` | The core agent owns them through the waves. Wave packages file change requests (a paragraph-chain stage for PATTERNS, the `links()` hook for LINKS, equations/statcheck for STATS, the roster for DATA-b), and the core agent lands them as small PRs within a day |
| `_r/regex.py` | Lane 2 until merged, then the core agent |
| `repository/index.py` | REPO only; DATA-b and CODE-b change the `datacheck`/`codecheck` functions it delegates to |
| `archives/listing.py` | REPO while active, then LINKS (LINKS step 5 waits for REPO) |
| `tests/foundation/test_layering.py` | HARNESS writes it once; the rule is derived from imports |
| Deviation rows | One file per case area, `parity/deviations/<area>.toml` (revision 3; replaces H0-15's per-package fragments and slugs). A row that matches a validated module's cases goes in that module's own area file, which the validator group owns through CODEOWNERS. A package edits only its own areas' files |
| `parity/**` | HARNESS, then HARNESS-v2 from day 4.5, then the core agent. Carve-outs: HARNESS-NET holds `parity/cassettes/**`, its new rows in `parity/accuracy/**` and `parity/r/**` on days 4.5-6.5 (HARNESS-v2's results-only accuracy step waits for it); BATCH-a and BATCH-b hold `parity/batch.py`. Wave packages send requests, except for their own deviation rows and lock areas |
| `presets.py` | TIERS until it closes (day 2.5), then the core agent. No move into `market/` unless decision 21 asks for it |
| `report/**` | Held per file. HARNESS for `ReportTable.to_canonical()` in `report/blocks.py` (days 0-4.5); TIERS for the badge and the validation box, in other files (days 0-2.5); CORE-1e for `report/render.py` and `report/blocks.py` (days 13.5-16); then SERVICES-b. `to_canonical` is a parity pin: after HARNESS it belongs to the `parity/**` owner (HARNESS-v2, then the core agent), and others send requests. Never two holders of one file at once |
| `packs/**` | STORE-2a (days 2.5-4), except the ECOSYSTEM.md §4.11 rows; STORE-2b for those rows, with `module.py`, `config.py` and `registry.py` changed through the core agent; then MODSYS |
| `porting/**` | HARNESS-v2 for `porting/modules.toml`, `porting/map/**` and `symbols.json` until it closes (day 13); CORE-1b's entries go to it as change requests. After that the core agent owns both files, and each deleting PR updates its own entries in them (the Deletion rule above; one sorted entry per line). PORT holds `porting/ports/**` only |
| `compat/**`, `src/pytacheck/__init__.py` | COMPAT from CORE-1e's close; the core agent before and after. The wave packages never edit `__init__.py`; COMPAT re-exports what they add |
| `api/web.py` | WEB, then LLM-b for the LLM option. It is outside SERVICES-b's `api/**` |
| `cli.py` | TIERS (`status`, `--status`, `init`), BATCH-a, CACHE (`--cache-store`), WEB (`serve --app`), BATCH-b, in that order; each changes only its own commands |
| `src/pytacheck/resources/status/validation.json` | A snapshot of the team's registry, refreshed only by a PR that names the registry commit (ECOSYSTEM.md §2.3) |
| `parity/lock/<area>.json` | Re-locked only by the package that owns the area; others send requests |
| `porting/symbols.json` | One sorted entry per line, so parallel edits merge line by line |

### 4.6 Releases

The tree is at 0.3.1.dev1. The rewrite ships in four minor releases, so users meet breaking changes in small, documented steps. Each release has a CHANGELOG entry and a `docs/MIGRATING.md` section that lists every breaking change, its replacement and one before/after example.

| Release | When | Contents | Breaking for users |
|---|---|---|---|
| 0.4 | after phase A (day 13) | TIERS (derived labels, `status=`, badges), STORE-2a, the fidelity bands and the canonicaliser | `DEFAULT_MODULES` goes (use the `metacheck::default` preset); `metacheck::validated` becomes a deprecated alias |
| 0.5 | after the CORE checkpoint (CORE-1d closes, day 13.5) and CORE-1e | Doc, `depends=`, `Result` and blocks, WEB; FETCH and CACHE (REPO_FETCH.md); STORE-2b if its preconditions hold (decision 13) | none intended for modules: v1 modules and string reports still load; `Result` is new. From FETCH and CACHE: U-1 and U-2 change data_check and repo_check results (deviation rows); the cache store is on by default (`cache_store`, decision 22), and `repo_info_cache()` becomes its alias; without `OSF_PAT`, a request past OSF's hourly window fails after waiting at most 60 s with a message naming `OSF_PAT`, and without a GitHub token a request past api.github.com's 60 per hour fails the same way, naming `GITHUB_PAT_GITHUB_COM` (decision 23). If STORE-2b lands: netrc, the git fallback and the GitLab, Codeberg, `file://` and `git+` store forms go; private GitHub stores use a `GITHUB_TOKEN` (ECOSYSTEM.md §4.11). If it lands later, these removals move to that release's notes |
| 0.6 | after the waves, COMPAT and CLOSE (day 27) | W1-W3, `pytacheck.compat`, the top-level surface (§2.10) | R-shaped top-level names warn and point to `pytacheck.compat`; Band B/C changes listed per module in the release notes |
| 1.0 | after 0.7 at the earliest, so the names that warn from 0.6 warn for two minor releases (§3.6's deprecation rule) | end of the deprecations | the deprecated top-level re-exports and the `metacheck::validated` alias go |

- **Re-certification per release.** Every release candidate goes through ECOSYSTEM.md §2.5's loop: a validator named by the team reruns each validated built-in on the candidate, and the release is added to `implementations.py.pytacheck` before the tag. If no rerun happens in time, the one-release grace of ECOSYSTEM.md §2.5 step 4 applies, then the module falls to experimental. The release checklist names the validator; until the team names one (ECOSYSTEM.md §8.1), the maintainer ships with the grace note.
- **Going public.** Publishing to PyPI and opening the repository follow decision 13. The web app's `/source` offer (§3.7) needs the public repository or an sdist per release, so the first public WEB deployment waits for it.
- **Owner.** The core agent cuts each release; CLOSE writes 0.6's notes. A release adds no package-days: its checklist runs inside the package that ends the phase.

---

## 5. Targets

### 5.1 Per module: `parity/bench/budget.toml`, provisional

CPU ms per paper, 21 papers, all modules in one context sharing each Doc. Budget = matching and logic + a boundary floor F. **F = 5 ms (S: 6.5-7.4 in the two modules that measured it), CORE 1e targets 3.** All budgets are provisional until W1 re-measures them; hard counters are hard from CORE 1b.

| Module | Today **(M)** | Spike **(S)** | Budget **(E)** | Basis |
|---|---:|---:|---:|---|
| Doc build + lazy fields as used | 290 rebuilds | 1.5 build (+ fold 0.7, clean 1.1, projection 1.5) | 4 | S, without the word index |
| ethics_check | 179.6 | **13.5** | 15 | S + 10% |
| open_practices | 204.4 | **7.5** | 9 | S + 10% |
| stat_p_exact | (in 112.6) | **7.5** | 9 | S + 10% |
| all_p_values, stat_p_nonsig | (in 112.6) | – | 7 each | shared facet 2 + F |
| ref_consistency | (in 230.5) | **7.9** | 9 | S + 10% |
| 5 other offline ref_* | (in 230.5) | – | 40 | ≈ 3 each + F |
| funding_check_oi | 87.3 | – | 10 | pattern floor 2.9 **(M)** + F |
| coi_check_oi | 41.8 | – | 8 | ≈ 3 + F; SPIKE-2 measures |
| power (no LLM) | 120.6 | – | 15 | pattern floor 3.2 **(M)** + groups + F; SPIKE-2 measures |
| funding_check | 35.2 | – | 23 | bank with doc prefilter 18.0 **(M)** + F |
| coi_check | 20.3 | – | 19 | agrep kept ≈ 14 + F |
| marginal | 12.4 | – | 6 | one pattern + F |
| all_urls | 14.6 | – | 7 | `urls()` facet + F |
| stat_check | 37.8 | – | 20 | statcheck ≈ 14 **(M)** + F |
| stat_effect_size | 67.4 | – | 40 | TS-all 44 **(M)**, label parser prefiltered + F |
| `module_run` overhead × 20 | ≈ 37 | 0.33 per call | 7 | S |
| **Total, 20 modules** | **1,165** | 4 modules: 427 → 36 | **≈ 255 (≈ 4.5x)** | ≈ 215 at F = 3 |
| repo_check paper-side links | 205 | – | 25 | `links()` facet |
| `read(xml)` | 113-193 | – | 110, re-set by SPIKE-2 | lazy `eq` removes ≈ 35% **(M share)** |

**Hard counters:** `doc_builds` ≤ 1 per paper per run, also on materialised papers and mixed chains; `text_search` recursions and `paper_table` calls in migrated modules = 0; warm-run regex compiles = 0 (**S**: 0); traced repository chain file opens ≤ 23; CSV, workbook and code parses per file = 1.

```toml
[defaults]   # counters hard; +15% vs base hard; absolute cpu_ms provisional (soft) until W1 re-measures
counters = { doc_builds = 1, text_search_recursions = 0, paper_table_calls = 0, regex_compiles_warm = 0 }
[module.ethics_check]
cpu_ms = 15
[module.open_practices]
cpu_ms = 9
[module.stat_p_exact]
cpu_ms = 9
[module.ref_consistency]
cpu_ms = 9
[chain.default_offline]
cpu_ms = 270
[repo_chain.traced]
file_opens = 23
csv_parses_per_file = 1
workbook_parses_per_file = 1
code_decodes_per_file = 1
```

### 5.2 Whole runs

| Workload | Today **(M)** | Measured so far | After CORE **(E)** | After CLOSE **(E)** |
|---|---|---|---|---|
| Accuracy matrix, Python side | 35.7 s; R 91.6 s | **21.4 s (S)**, façade + 4 modules, without CORE-0's patches. **16.9 s (C)**, CORE-0 alone | ≈ 14-15 s: 16.9 − the four spiked modules in shared mode (2.8 → ≈ 0.7 s, the spike's 12 → 3 s ratio, **S**) − shared papers ≈ 0.5 **(E)**. The old ≈ 15-18 s was reached by CORE-0 | ≈ 10-12 s: paper modules 21 × ≈ 0.26 s; repository ≈ 2 s; reads ≈ 1.2 s; harness ≈ 0.9 s |
| Four spiked modules, accuracy run | 12 s | **3 s (S)**; **2.8 s after CORE-0 (C)** | – | – |
| Module work against R | 2.3x | – | ≈ 4x | ≈ 6-8x |
| 1,000 offline XML papers | ≈ 12.7 min | – | ≈ 8 min | ≈ 6-7 min at `-j 1` (≈ 0.26 s chain + 0.1 s read per paper); ≈ 2-2.5 min at `-j 4` on idle cores |
| Repository chain (40 outputs) | 9.3-10.2 s | 2.5 s **(M, P3)** | 2.5 s | ≈ 2 s; opens 96 → ≤ 23 |
| Repository fetches, demo paper (network) | `repo_check` 38.95 s; `data_check` 72.9-76.0 s cold (REPO_FETCH.md §1) | – | after FETCH and CACHE: `repo_check` ≈ 12-15 s cold, ≈ 2-2.5 s warm; `data_check`'s downloads wait for RF-8 (LINKS) | `repo_check` the same; `data_check` ≈ 20-30 s cold and ≈ 2-3 s warm, with RF-8's scheduler and blobs from the store (REPO_FETCH.md §7) |
| Codebook scan, 94K characters | 16.0 s | 0.2-0.3 s **(M)** | same | same |
| Memory | Doc – | Doc 0.37 MB mean, 1.02 MB max **(S)** | a finished run's caches are collectable (the gc test) | + ≈ 0.4 MB per paper in flight |

Network- and LLM-bound workloads keep BATCH_DESIGN §4.1's gains (rolling window 1.45-2.7x, **M**), except repository listings: §4.1's "3-4x, since wall time becomes the slowest host" does not hold, because 92.7% of psychology papers' repositories are on OSF and 3.1% of those papers use two or more hosts **(M)**. REPO_FETCH.md §1.4 and §7 replace it.

### 5.3 Size

**The line ledger.** This is the one size table for all three documents; FIDELITY.md and ECOSYSTEM.md cite it; the figures they give for their own parts are rows here, not separate totals. Counting rule: `wc -l` over `.py` files **(M3)**. Every row comes from a per-package target (§3.5, §4.3, ECOSYSTEM.md §4.12 and §5.2), and §3.6's tree sums to the same total. There is no unitemised row.

| Part | Today **(M3)** | Revision 2 **(E)** | Revision 3 **(E)** | Basis |
|---|---:|---:|---:|---|
| Paper modules, `text/`, `stats/`, `papers/`, `_r/`, runners | 21,964 | ≈ 16,800 | ≈ 13,850 | §3.5; the spike's −17% on module bodies **(S)** |
| Repository cluster (`codecheck`, `datacheck`, repository modules, `repro`, `fileinfo`) | 46,275 | ≈ 33,900 | ≈ 31,100 | §3.5; lanes 3/4 size gates |
| Services (`archives`, `statout`, `llm`, `io`, `db`, `report`, `api`, `http`, `cli`, `config`) | 51,335 | ≈ 43,700 | ≈ 38,300 | §3.5; lanes 6/7 size gates |
| The rest of `src/` (`packs/`, `__init__`, resources) | ≈ 6,200 | ≈ 6,200 | ≈ 5,200 | ECOSYSTEM.md §4.11: −850 to −1,100. The project-config trust gate and the token-only GitHub path stay |
| New core, net | 0 | + ≈ 2,600 (≈ 3,300 with ≈ 700 moved) | + ≈ 2,500 | 1,975 for the spiked subset **(S)**, plus groups, run, facets, output, blocks, values; no `core/templates.py` |
| Status and store additions | – | – | + ≈ 600 | ECOSYSTEM.md §4.12: +560 to +660 |
| `compat/` | – | – | + ≈ 300 | the existing `_EXPORTS` map plus a deprecation hook (≈ 100) and the `ModuleOutput` adapter (≈ 200); the façades move in as moves |
| `port/` | – | – | + ≈ 1,260 | ECOSYSTEM.md §5.2; no R worker |
| `api/web.py` | – | – | + ≈ 215 | §3.7: the spike's 200 lines, plus the usage counts and `/source` |
| **`src/pytacheck`** | **125,800** (125,200 at revision 2's count, **M**) | **≈ 103,000 (−18%)**, re-measured; revision 2 claimed ≈ 98,000 | **≈ 93,300 (−26%; range 88-97k)** | §3.6 |
| Tests (`tests/**/*.py`) | 106,358 | – | ≈ 90,000 (−15%; the least certain row) | the retired private-helper cases (FIDELITY.md §6), `packs/` (≈ 700), the emulation tests each wave deletes, and CLOSE's measured sweep |

- **Like for like:** revision 2's plan on the same sweeps is ≈ 103,000; relaxed fidelity removes ≈ 11,000 more (§3.5's ≈ 11,150). The rest of the difference to 93,300 is the smaller core and `packs/`, less the new status, compat, port and web lines.
- **Net:** the sweeps take out ≈ 36,300 lines, `packs/` ≈ 1,000, and the additions put back ≈ 4,900 (core 2,500, status 600, compat 300, port 1,260, web 215).
- **Not yet summed:** REPO_FETCH.md's CACHE package adds ≈ 700 lines (`cache/**`; E, not yet sized from a prototype: upstream-apis.md put an HTTP store alone at 250-400, and CACHE adds the blob store, the ledger and the Redis backend). It removes at most ≈ 250 of the 266 lines in `archives/info_cache.py` (156) and `archives/cache.py` (110), because `repo_info_cache()`, `repo_info_cache_clear()` and `metacheck_cache_info()` stay public, and the download and LLM caches still resolve their directories there. That is ≈ +450 net or more **(E)**, inside the 88-97k range. The total is re-summed once decision 22 is made.
- **Not in the ledger:** parity data (140 MB in 9,745 files under `parity/`, **M3**), which the mark migration and the retired cases shrink but which is not code.

### 5.4 Where the time goes after CORE-0 (C)

Accuracy run, Python side: 16.9, 16.7 and 17.8 s in three runs; import 0.6 s. Time per module (all its calls, summed over the matrix):

| Module | s | Module | s | Module | s |
|---|---:|---|---:|---|---:|
| codebook_check | 1.80 | funding_check | 0.65 | repo_check | 0.38 |
| data_check | 1.34 | ethics_check | 0.63 | ref_accuracy | 0.37 |
| code_check | 0.82 | stat_p_exact | 0.60 | coi_check_oi | 0.36 |
| stat_effect_size | 0.82 | ref_retraction | 0.55 | coi_check | 0.30 |
| stat_check | 0.79 | ref_miscitation | 0.50 | funding_check_oi | 0.26 |
| ref_consistency | 0.78 | all_p_values | 0.50 | all_urls | 0.22 |
| open_practices | 0.74 | stat_p_nonsig | 0.47 | ref_summary | 0.21 |
| | | ref_replication | 0.41 | marginal | 0.17 |

The modules sum to ≈ 14.6 s. Paper reads, the harness and the import take the rest.

Under cProfile (36.1 s), self time splits as:

| Bucket | Share |
|---|---:|
| pandas | 53.7% |
| stdlib | 7.6% |
| `isinstance` | 4.0% |
| `pytacheck._r` | 3.2% |
| regex (compile, search, sub) | 6.9% |
| parity harness | 2.8% |
| pytacheck modules | 2.5% |
| numpy | 1.1% |

The main cumulative costs are:
- `text_search`: 817 calls, 16% of the run, 7% of it in `_join_sections`;
- `merge`: 1,072 calls, 9%;
- `.loc`/`.iloc` reads: 13,800 calls, 11%;
- `Series.__init__`: 21,000 calls, 8%;
- regex compiles: 1,354, 5%;
- `read(xml)`: 7%, most of it in `_grobid_to_bibr`.

Implications:
- After CORE-0 no single hotspot is left. The remaining ≈ 1.5x (16.9 → 10-12 s) comes only from the core's structural changes: a Doc built once instead of per-call frames, facets, and dict outputs.
- Compared with the plan, the rewrite's payoff on this matrix is now ≈ 1.5x, not ≈ 3x. The per-paper budget in §5.1 (≈ 4.5x on module CPU) is a separate measure. Its 1,165 ms baseline predates CORE-0, so it stays provisional until W1 re-measures it.
- The hard counter `regex_compiles_warm = 0` is not met today: 1,354 compiles per run.

---

## 6. Decisions for the user

The plan assumes each recommendation until the user says otherwise.

**1. Output shape where it is visible but does not affect accuracy** (row order, R's 15-digit number text, list-column shapes). Revised in revision 3.
- (a) Keep R's shape everywhere; only R bugs change, as U-entries. Revision 2's recommendation.
- **(b) Adopt FIDELITY.md's three bands: results locked (Band A); order, dtypes, number text, whitespace and error text free and unrecorded (Band B); wording recorded with one scoped row per change (Band C).** Recommended (changed from (a)).
- *Why (b):* the user asked for relaxed fidelity where it makes the code more Pythonic. The 1,298 case marks become about 200-280 rows before Band B removal, and fewer after (FIDELITY.md §3.2), and ≈ 11,150 more lines of emulation can go (§3.5). The validated modules keep Band A exact on every tier, with a validator's code-owner approval for any change (§4.1). *Cost:* funding_check_oi's row order changes on 11 of 21 papers **(M)**; the release notes list every module whose default order changed (FIDELITY.md §10, risk 7).

**2. No-code modules in the store.** Revised in revision 3.
- (a) Ship `pytacheck.module/1` YAML now and convert marginal as the proof.
- (b) Build the loader, keep every built-in in Python.
- **(c) Defer until a store author or the porting translator needs it; marginal stays Python.** Recommended (revision 2's recommendation, restored after the revision 3 review).
- (d) TOML checks in phase D, once `pytacheck.doc` is stable: patterns, section filters and a traffic-light rule, compiled to `pytacheck.doc`; marginal and all_urls as the proof.
- *Why (c):* revision 3 proposed (d) because the R translator would emit TOML, but the porting design writes `.py` against `pytacheck.doc` (ECOSYSTEM.md §5.2), and no store author has asked. (d) adds a second module form and ≈ 2 days for no current user, against the goal of fewer lines. Under revision 3 the one-module-per-R-module objection is gone (the map is `porting/modules.toml`, F2), so (d) stays open for when a user appears.

**3. Batch corpus mode (BATCH BL-8).**
- **(a) Defer: re-measure the loop/list ratio after W1-W3 and build only if still ≥ 1.5x (3.45x today, M).** Recommended.
- (b) Build it with the batch engine, taking on per-module neighbour-invariance risk.

**4. LLM grounding flags.**
- **(a) Observation-only checks that LLM quotes occur in their source; a note, a warning and a count, and no value changed.** Recommended.
- (b) Not now.
- *Why (a):* it makes hallucination visible, and it runs only when the LLM is on.

**5. Pacing.** Revised in revision 3.
- **(a) Phases A-D (§4.4) with three stop points: after SPIKE-2 (day 2), at day 11.5 (the deviation rows and the metacheck team's answers), and at the CORE checkpoint at CORE-1d's close (day 13.5), before any wave starts.** Recommended.
- (b) Phase A only, then re-plan: the fidelity contract, validation status, presets and the store ship, and the rewrite waits.
- (c) Phases A and B plus the paper-module waves (PATTERNS, STATS, REFS, MODSYS, COMPAT); the repository cluster then gets only the lane 3/4 leaf de-emulations behind adapters.
- (d) The whole plan without stop points.
- *Context:* ≈ 126 package-days in 36 packages (116.5 in 34 before REPO_FETCH.md); critical path ≈ 27 days, ≈ 31 with ≈ 4 days of calendar contingency (§4.4). The payoff is ≈ 4.5x on module CPU, −26% code (≈ 93,300 lines, §5.3), and the ecosystem of ECOSYSTEM.md. CORE-0 alone already gave 2.1x on the accuracy matrix (34.7 → 16.9 s, **C**); the rest of the plan adds ≈ 1.5x there (to 10-12 s, §5.4).
- *Why (a):* phase A is useful on its own (users choose validated or experimental checks from the first release), and each stop point comes with measured numbers.

**6. Repeated `paper_id`s in a paper list** (new; changes counts users read).
- **(a) Resolve once at the list boundary: warn, and rename later repeats `id~2`, `id~3`, as `report(list)` already plans; every module then treats them as distinct papers.** Recommended.
- (b) Keep R's per-module behaviour (merged pools, cross-joined sections, crashes), each replicated and pinned.
- (c) Refuse such lists with an error.
- *Why (a):* one defined behaviour instead of five; no pooling of different papers (ref_consistency merged two distinct papers' references, **R**); a list that repeats one paper in two formats still works.

**7. A pack whose `requires.pytacheck` excludes the running version** (new; store contract).
- **(a) Warn at load (as at install today), with dev builds satisfying `>= X.Y.Z`; error only in `pack check` and `store build --check`.** Recommended.
- (b) Refuse at load and hide the pack from `store search`.
- *Why (a):* (b) would reject every pack on a dev build, including in our own store CI **(R)**, and it changes user-facing store behaviour for no measured benefit.

**Revision 3 decisions.** Decisions 8-14 and 17-19 come from ECOSYSTEM.md §9, which gives the evidence for each.

| # | Decision | Recommendation | Where |
|---|---|---|---|
| 8 | The launch validated set | the 5 team-validated modules (power, stat_p_exact, stat_p_nonsig, marginal, stat_effect_size), or 4 if the team drops one; the team decides. stat_check and ref_accuracy stay experimental until a team rerun on this implementation certifies them. Every gate applies to every module the registry snapshot labels validated | ECOSYSTEM.md §1.1, §3.1 |
| 9 | Replace `metacheck::validated` | yes, by the `validated` policy, with a deprecated alias until 1.0. The web app uses validated ∩ `server_safe` (16 → at most 5 modules); data_check becomes experimental | ECOSYSTEM.md §3.2, §3.4 |
| 10 | Default policies | library and CLI `experimental`, which also runs modules from packs the user installed; web app and API `validated` | ECOSYSTEM.md §3.4 |
| 11 | Field scope | applies only when the preset declares `fields`; with no preset fields a certification counts, and the badge names the corpus field | ECOSYSTEM.md §2.6 |
| 12 | Lookup modules | experimental until certified through `metrics` | ECOSYSTEM.md §2.4 |
| 13 | Private stores | make pytacheck and the store (`scienceverse/pytacheck-modules`, private today) public, and fix store CI. Private packs and stores stay supported on GitHub only, through a token on the contents and tarball API; netrc, the git fallback and the GitLab and Codeberg forms go (−850 to −1,100 lines, STORE-2b). The project-config code-trust gate stays. No fetch or auth deletion lands before the store is public | ECOSYSTEM.md §4.11, §6 |
| 14 | Where the registry lives | a team-owned repository, with CODEOWNERS as the validator group; the same validators are code owners of the validated modules and their deviation rows in pytacheck | ECOSYSTEM.md §2.3, §2.5 |
| 17 | Consent for ports | required for store listing, beyond what the licence requires | ECOSYSTEM.md §5.5 |
| 18 | Inbound terms | DCO, no CLA, for the pytacheck repository and packs kept in the store repository; pinned external packs rely on the licence check and the consent record | ECOSYSTEM.md §5.4 |
| 19 | Legal review | counsel reviews ECOSYSTEM.md §5.4, including its Uncertainties list, before third-party ports are listed | ECOSYSTEM.md §5.4 |

**15. The main API** (new).
- **(a) `pc.check(paper, preset=…, status=…)` returns a `Report`. The 303 R-shaped names move to `pytacheck.compat` and stay importable from the top level with a deprecation warning until 1.0.** Recommended.
- (b) Keep the R names as the main API and add `pc.check` beside them.
- (c) Remove the R names from the top level at once.
- *Why (a):* Python users get a small top level (`check`, `Paper`, `read`, `Settings`, `configure`, `status`, `status_table`, `hosts`; §2.10), the same surface ECOSYSTEM.md §3.3 uses. R users, existing scripts and the parity harness keep working through compat. (c) breaks every script today; (b) leaves 303 names as the first thing a new user sees. *Cost:* COMPAT, 2.5 days, and ≈ 300 lines: the existing `_EXPORTS` lazy map already does the re-exporting, so compat adds a deprecation hook and the `ModuleOutput` adapter.

**16. Wholesale ports of R libraries** (new).
- **(a) Replace them with thin Python equivalents: ellmer → an httpx client for OpenAI-compatible, Anthropic and Gemini APIs; ICU ucsdet + vroom + iconv → `_decode.py`; data.table fread and read.table → pandas plus a type layer; knitr purl → a static chunk parser; readxl and the ODS readers → calamine. Move `zenodo_upload` out of the package.** Recommended.
- (b) Keep the ports (revision 2).
- *Why (a):* ≈ 11,350 lines become ≈ 1,950 (§3.5), and `zenodo_upload`'s 1,228 leave the package. The differences are edge cases (non-UTF-8 code without a BOM, computed chunk options, unusual CSVs); each gets one scoped deviation row, and none touches a validated module's Band A outputs. Request bodies stay byte-identical for every module with mock-replayed parity cases (power, causal_claims, the `llm` area). *Cost:* fewer built-in LLM providers.

**20. The web app** (new).
- **(a) A Gradio app as the optional extra `pytacheck[app]` (`gradio>=6.28,<7`), mounted at `/app` on the API server, with the fixed settings of §3.7. It replaces the public Shiny server only after the metacheck team agrees a cutover (ECOSYSTEM.md §8.1); hosting itself is out of scope.** Recommended.
- (b) The static page on the API (96 lines, no new dependency): bibr JSON only, with no queue, progress or options.
- (c) Both: Gradio for the public server, and the static page as a no-dependency fallback.
- (d) No web front end: the API only, and the R Shiny app keeps serving the public.
- *Why (a):* 200 lines cover 14 of the Shiny app's 16 features, with no custom JS. The app itself makes no third-party calls; the PDF path and the network options send data to GROBID and the lookup services, as the privacy text says (§3.7). WEB adds the usage counts and the `/source` offer, and LLM-b the LLM option. The cost (+129.5 MB, ≈ +2.5 s startup) falls only on `[app]` installs. (b) needs API changes to gain a queue, progress or XML input; (c) keeps two front ends in step for one fallback case; (d) keeps R on the public server. *Cost:* WEB, 2.5 days, and LLM-b +0.5 day.
- **Upload retention.** **(i) Up to ≈ 15 minutes on disk, stated in the privacy text.** Recommended: Gradio shares one cached file among queued jobs with the same upload, and deleting it after each job broke the others **(M3)**. (ii) Delete an upload once no queued job refers to it, with a count taken when the job is submitted (≈ 20 lines, not measured).
- **Usage counts.** **(i) Keep the Shiny app's anonymous record** (date, event, reports, modules; no identifier; only on the hosted server; never sent anywhere), since the team keeps it to justify funding. Recommended. (ii) Record nothing.

**21. Directory renames** (new; revision 3 review).
- **(a) No directory moves. Expose the public namespaces (`pc.hosts`, `pc.checks`, `pytacheck store …`) over today's directories.** Recommended.
- (b) Revision 3's renames in CLOSE: `modules/` → `checks/`, `archives/` → `hosts/`, `packs/` + presets + provenance → `market/`, with compat re-exports for one minor release.
- (c) Only `packs/` → `market/`, if presets and provenance really merge.
- *Why (a):* the renames save no lines, break import paths that store modules and scripts use (§3.6's stable paths), and make every wave's file list stale. *Cost of (b):* +0.5 day in CLOSE and a deprecation window for every moved path.

**REPO_FETCH.md decisions** (2026-09-27). REPO_FETCH.md gives the evidence for each.

**22. Persistence across runs, and Redis** (new; amends BATCH R5 and P3-2).
- (a) A `CacheStore` in v1, on by default for repository metadata and file blobs only. Most entries are validated on every run or immutable: OSF trees behind the tree vector, git trees by commit SHA, versioned Dataverse datasets, zip-peek indexes and file blobs by hash. The rest are served on a TTL and can be up to one TTL stale: GitHub `/repos` metadata, the Dataverse latest-version pointer and the other dataset records 1 d, Zenodo records 7 d, OSF id types 30 d. Only a 200 answer's id type is stored; a 401 or 403 ("private") answer lasts one run, like a 404. A local file tier by default; an optional Redis/Valkey tier chosen by `PYTACHECK_CACHE_URL`; Valkey bundled in `docker-compose.yml`; pytacheck's own keyspace, not shared with bibr. Recommended before the maintainer's answer of 2026-09-27; now (a2).
- **(a2) (a) without the networked tier for now: the local file tier and its file ledger only. The Redis/Valkey backend, its compose service and its CI test wait until a second `pytacheck serve` replica is planned, behind the same `CacheStore` interface.** Recommended. The scienceverse platform will run one server for the foreseeable future (maintainer, 2026-09-27), and for one server a file store on its `/cache` volume does what Redis would (REPO_FETCH.md §6.1). It saves about 1 of CACHE's 4 days (E: the Redis backend, its Lua ledger, the compose service and the Valkey CI test).
- (b) The same store, off by default (BATCH R5 as written; P3-2 moved into v1).
- (c) No persistent store in v1: batching only (RF-1 to RF-10); P3-2 stays in phase 3. With 23 (a), CACHE still builds the request ledger and its file and Redis backends.
- (d) One Redis shared with bibr, as the request proposed.
- *Why (a):* a warm re-run of the demo drops from 35.6 s to ≈ 2-3 s, and a repeat paper costs 4-6 counted OSF requests instead of ≈ 16-20, so ≈ 15-25 warm re-runs fit in OSF's anonymous hour (REPO_FETCH.md §7). BATCH §7 rejected a persistent cache on by default because a re-run could miss a new retraction; (a) keeps that rule for lookups, and stores only what it re-validates, what cannot change, or what expires within 1-30 days. (d) gives 0 shared cache hits: bibr never calls a repository host, its Crossref cache is for another host (`api.crossref.org`; pytacheck uses `api.labs.crossref.org`), and its LLM cache uses other keys and formats. It would also couple eviction, credentials and deploys (REPO_FETCH.md §6.2). *Cost:* CACHE, 4 days; ≈ +450 lines net or more; the store is off in the parity harness and the tests; TTL entries can be up to one TTL old, so within those TTLs an edited dataset record or repository description is served stale; OSF file download counts can be up to the max-age old, 7 days for projects and 30 for registrations (a deviation row); and a stale entry is possible when OSF drops a log callback or a file moves to another project, which the 7-day max-age bounds.

**23. OSF's limits under BATCH R6** (new).
- **(a) Enforce OSF's published and coded windows per identity, those of a minute or longer through a request ledger across processes and runs (anonymous: 10 per second and 100 per hour on default views, 3 per second and 75 per minute on file lists; with `OSF_PAT`: 10,000 per day), with R's 10 in flight as the per-host envelope. The same ledger keeps api.github.com's published 60 requests per hour anonymous and 5,000 per hour with a token. When a window is exhausted, wait up to 60 s (not at all under `skip_on_api_limit`, as for a known reset today), then fail as a 429 would, naming `OSF_PAT` or, for GitHub, `GITHUB_PAT_GITHUB_COM`. Recommend `OSF_PAT` in the docs and in a warning when a run would exceed the anonymous hour. The ledger is CACHE's (REPO_FETCH.md §5.5), so (a) needs CACHE whatever decision 22 says. It runs even with `cache_store="off"`, and is off only in the parity harness and the tests.** Recommended.
- (b) Keep only R's bound per call site: serial listings, 1 in flight for `batch_query`, no ledger. That forbids RF-3 and RF-4's fan-out and returns pytacheck's traversal and `batch_query` to R's pace.
- (c) Ignore the windows of a minute or longer, since production sent no 429 for 451 anonymous requests in 24.5 minutes **(M)**.
- *Why (a):* R6 says "never exceed … a service's published limit", and OSF publishes 100 per hour for anonymous clients. R itself goes past that after 2-3 OSF-heavy papers. The measured headroom is not a published limit: OSF's own web app hit its throttles, and third parties report 429s (REPO_FETCH.md §2.2). *Cost:* a deviation row, because R would have sent the request that (a) refuses; a user without a token gets about 5-6 cold demo-like papers per hour from one IP, or 15-25 warm re-runs with 22 (a); and if the networked ledger fails, replicas fall back to separate counts for the rest of the run, so OSF's windows can be exceeded across replicas (REPO_FETCH.md §5.5).

---

## 7. Spike results and SPIKE-2

### 7.1 Criteria

Tree `arch/wt-spike`: 46 files, +6,561/−1,020 lines. Scripts in `arch/spike/`.

| # | Criterion | Result **(S)** | Verdict |
|---|---|---|---|
| 1 | I1, I2 | 0/50,336; 0/61,635 | pass |
| 2 | Façade suites and text/io parity areas | 6,614 suite cases 0 differences; 0 status changes in 9,474 cases | pass |
| 3 | Whole accuracy matrix, Python ≤ 26 s | identical differences; 7 unexplained on both sides (worktree symlink paths); 21.4 s | pass (the 7 are H0-11) |
| 4 | Four modules A/B | 0 differences in 576 elements + 50 chains; duplicate-id list: ethics 198 → 172 rows, open_practices 78 → 54, stat_p_exact 699 → 445 (summary text changes), ref_consistency crash → 154 rows | pass on gated inputs; defects B-1, B-10, B-18 found afterwards |
| 5 | Parity, 8 module areas | 0 status changes, identical lock digests | pass |
| 6 | Accuracy, 4 modules | 84 outputs, 60 differences all explained; 12 → 3 s | pass |
| 7 | Performance, shared (ms/paper) | ethics 157.4 → 13.5 (budget 12), open_practices 178.5 → 7.5 (12), stat_p_exact 39.3 → 7.5 (6), ref_consistency 51.9 → 7.9 (8); Doc 1.5 (5); overhead 0.33 (0.7). Cold: 158.3 → 14.8, 181.4 → 12.7, 39.2 → 9.7, 51.9 → 9.3 | 2 of 4 budgets missed (boundary floor) |
| 8 | Counters | 1 Doc build per paper (17 `paper_table` and 224 `text_search` calls per paper on the old path); 0 warm compiles | pass on raw papers; materialised papers fail (B-21) |
| 9 | Safety | mutation 14/14; shared 399 comparisons, 0 differences | pass; lifetime and staleness defects B-9, B-11 found afterwards |
| 10 | Memory and size | Doc 0.37 MB mean; core 1,975 lines; module logic 590 → 495 lines | memory pass; size over plan |
| – | pytest; lint | 5,963 passed both sides; ruff clean; mypy strict clean on core | pass |

**Choices settled:** (a) word index vs scans: 37.9 vs 41.3 ms, below the 10% bar → **scans**. (b) Projection from records: V4 holds on 471/471 + 128 slices (slicing a once-built frame was ≈ 4% faster, with identical outputs) → **records**, which need no full frame per Doc. (c) Dict summary merge: equal on 576 elements and 50 chains → **dict merge**, with the suffix rule fixed (B-18).

### 7.2 What changed in the design because of the spike and its review

Per-Doc reference joins (§2.5); F6 repeated-id resolution (§2.2, decision 6); V7 duplicate rows; opaque scope token and memo-only `run_session` (§2.2, §2.6); no chain-step memo; `.tolist()` backing; thread-safe stages; `Result` returns a dict on direct calls; dplyr suffix rule; the word index, YAML modules and `module_api` enforcement dropped; the `requires` dev-version fix; snapshot oracles (F5); replayed network rows and split behaviour PRs; the coverage ratchet; fresh-worktree zero; import-derived layering; I3 fuzz and the kill switch; re-based budgets, size and schedule; lane acceptance items restored.

### 7.3 SPIKE-2 (2 days; before CORE-1b; scratch tree `arch/spike2`, never `/home/user/pytacheck`)

**Build** on top of `arch/wt-spike`:
1. `Doc.groups(level)` and `Hits.paragraphs()`/`sections()`, behind the façade's `return_="paragraph"|"section"` modes, replacing `_paste_groups`, `_semi_join` and `_section_headers` in the spike tree.
2. power (LLM off) and coi_check_oi on the core: the only paragraph chain in the default preset, and the section-scope module.
3. `Doc.from_records` at read time and the lazy `eq` table.
4. The `.tolist()` backing for materialised papers.
5. `detect_many` on the 94K-character paper, with the literal scan and with the word index.
6. A per-output profile of the boundary floor (`Schema.frame`, `sentence_table`, `Summary.frame`, `_assemble`) on the six spiked modules, with one prototype cut.

**Pass:** V5 holds, with 0 differences on `diff_text_search`'s return-mode cases; power and coi_check_oi are A/B-equal on the 24 inputs × 6 elements, including critic §3.7's hazard (later stages run on the joined, cleaned paragraph text); `read(xml)` is measured and its target re-set; a materialised paper gives 1 Doc build per run and ≤ 3 ms; the index-or-scan answer for `detect_many`; the boundary-floor breakdown; **a re-estimate of CORE-1b's days**.

**If V5 fails** in a way the timebox cannot fix, CORE-1b keeps the pandas grouped path behind the façade, PATTERNS migrates power on it, and the plan is re-estimated before the waves.

---

## Appendix A: evidence

All paths are under `scratchpad/`.

| Topic | Where |
|---|---|
| Spike results, scripts, patch, tree | `arch/spike/results.json`, `arch/spike/out/*.json`, `arch/spike/*.py`, `arch/spike/spike.patch`, `arch/wt-spike` (bcf5429) |
| Spike review reproductions | `arch/review_stale.py` (run_session staleness), `arch/review_mem.py` (retained memory), `arch/review_fuzz.py`, `arch/review_fuzz2.py` (generative fuzz), `arch/review_store.py`, `arch/rs_old.json`, `arch/rs_new.json` (store pack A/B) |
| Module costs, call counts, pattern bank, union vs separate | `arch/census_paper_modules.md`; `arch/census/*.py` |
| Repository trace (96 opens), warm attribution, `RepoIndex` | `arch/census_subsystems.md` |
| Public API, store contract, global state, parity surface | `arch/census_core_api.md` |
| Gaps, coupling, thin coverage per module | `arch/census_critic.md` |
| Batch engine | `perf/batch/BATCH_DESIGN.md` |
| Repository fetches, cache store, OSF limits (2026-09-27, another session's scratchpad) | REPO_FETCH.md, Appendix |
| R vs Python, TS1-TS4, P2, P3, P4 | `arch/inputs/perf_partial.json`; `perf/patches/*.patch` |
| D1/D2/D3 prototypes and judge re-measurements | `arch/minimal_proto/`, `arch/design_performance/`, `arch/domain_proto/`, `arch/judge1/`, `arch/judge_integrity/` |
| Lanes 3-7: units, gates, divergences, acceptance | `rightsize_plan.json` → `lanes` |
| After CORE-0 (C; the later session's scratchpad) | `perf/ab.txt` (5 interleaved A/B runs), `perf/head-{1,2,3}.json.summary.json` (per module), `perf/head.prof` (cProfile), `perf/acc_run.py`, `perf/ab.sh` |
| Revision 3 (M3; this session's scratchpad, at f559c1be) | `research/results/structure.md` (P1-P10, the bold options, the target tree); `research/results/emul-{paper,repo,services}.json` (the 85 emulation items, each with lines now, revision 2 and relaxed sizes); `research/results/fidelity.md`, `tiers.md`, `market-research.md`, `market-gap.md`, `porting.md` (the inputs to FIDELITY.md and ECOSYSTEM.md); `research/{idioms,anatomy,fnsizes,rerun_count,rhelpers,sloc,span}.py` (the measuring scripts); `gradio-spike/` (§3.7) |

## Appendix B: Review log

One line per finding of the spike review. All 22 accepted; where the reviewer offered alternatives, the chosen one is named.

| # | Finding | Verdict and change |
|---|---|---|
| B-1 | ref_consistency pools the references of papers sharing a `paper_id` (high) | **Accepted.** Joins keyed per Doc (§2.5); REFS migrates ref_consistency first; distinct-papers fixture in SNAP, G5, the accuracy matrix and `tests/mod_ref_*` |
| B-2 | Repeated-id handling is a buried, per-module user-visible choice (high) | **Accepted.** F6 + `papers/ids.py`; decision 6 recommends resolving once at the boundary with `~2` suffixes; one U-entry |
| B-3 | Groups, reader-time Docs, lazy `eq` and `detect_many` were never built, yet CORE 1b deletes the grouped path (high) | **Accepted.** SPIKE-2 (§7.3) gates the deletion; CORE-1b re-estimated at 5 days |
| B-4 | The G5 oracle imports the live helpers CORE rewrites (high) | **Accepted, snapshot option.** SNAP records golden JSON at base (F5, H0-12); no `tests/_legacy/` copies; the diff suites use snapshots too |
| B-5 | Thin-coverage modules get behaviour changes inside their rewrite PR (high) | **Accepted.** HARNESS-NET replayed rows (H0-16); pure rewrite and behaviour PRs split for six modules (§4.1) |
| B-6 | `Result` is a read-only Mapping (medium) | **Accepted, dict-on-direct-call option.** `@module` returns `as_dict()` with the Summary framed once; mutable-dict test (§2.9) |
| B-7 | Refusing packs on `requires.pytacheck` rejects dev builds (medium) | **Accepted.** Release-tuple compare; warn at load, error in `pack check`/`store build --check`; decision 7 |
| B-8 | The chain-prefix memo key ignores predecessor success (medium) | **Accepted, drop option.** Only plain-paper calls memoised; failing-predecessor test (§2.6) |
| B-9 | The Doc cache holds the `RunContext` (medium) | **Accepted.** Opaque integer token; gc test in CORE-1d (§2.2) |
| B-10 | Exact duplicate rows within one Doc are kept (medium) | **Accepted.** V7; per-Doc uniqueness check; duplicate-row fixture (§2.2) |
| B-11 | `run_session()` trusts the Doc cache, giving stale façade results (medium) | **Accepted, memo-only option.** Trust only in internal runners; the fingerprint alternative rejected (`.loc` edits keep identity and length); H0-9 extended |
| B-12 | Headline figures not supported by the spike (medium) | **Accepted.** §0, §5.1-5.3 and §4.4 re-based; budgets provisional until W1; boundary floor measured in CORE-1e |
| B-13 | Word index, `module_api` enforcement and YAML have no measured payoff (medium) | **Accepted.** Index deleted (scans; re-add at ≥ 10%); `module_api` read and ignored; decision 2 now recommends (c) |
| B-14 | Work packages overlap on shared files; some files unowned (medium) | **Accepted, all seven.** Import-derived layering; per-package divergence YAML and U slugs; core agent stewards `core/**`; `json_expand` moved to CORE-1c; `RepoIndex` delegates parsing, LINKS step 5 after REPO; LLM-b after CORE-1d; owners named (§4.5) |
| B-15 | Lane-5 vector-search U-entries lost; new code replays R/dplyr errors; lane acceptance items dropped (medium) | **Accepted.** Multi-pattern vector searches return results (CORE-1b); replays removed (Y1); every lane acceptance item restored (✚ in §3.3) |
| B-16 | Parity coverage can shrink as private-helper cases retire (medium) | **Accepted.** H0-13 coverage ratchet with `parity/retired.yaml` |
| B-17 | Zero-unexplained and zero-fail gates cannot pass in worktrees (low) | **Accepted.** H0-11: realpath-normalised paths, quarantine list with a ratchet, fresh-worktree CI job |
| B-18 | The dict merge overwrites columns on a third chained run (low) | **Accepted.** dplyr's append-while-taken suffix rule; 3-deep test (§2.9) |
| B-19 | The stage-text cache can return wrong text under a race (low) | **Accepted.** Local compute + single-assignment publish; threaded test (§2.3) |
| B-20 | The spike's core breaks the layering rules (low) | **Accepted.** Frame builder moved into the Doc; `_values.as_float`; stat_p_exact off `grepl`; the pandas import-location rule dropped as payoff-free (§2.1, Y3) |
| B-21 | Materialised papers rebuild a slow fallback Doc per call (low) | **Accepted.** `.tolist()` backing in CORE-1b; materialised and mixed-chain bench and counter rows (H0-5) |
| B-22 | Only built-in and replayed patterns are gated (low) | **Accepted.** I3 hypothesis fuzz in PR CI and nightly; `PYTACHECK_LITERALS=off` (§2.3) |

Revision 3 review: one line per confirmed finding (B-23-B-77), gap (B-78-B-85) and low (B-86-B-98). None rejected.

| # | Finding | Verdict and change |
|---|---|---|
| B-23 | "0 Band A drift at re-lock" does not test the thing that can go wrong (high) | **Accepted, changed.** A per-rule flip audit (smaller than a re-lock) replaces the re-lock gate; per-case locks stay under rows (§4.2 H0-17, FIDELITY.md §3.1, §11) |
| B-24 | Scoped deviation rows drop the per-case lock, so later regressions under a row pass silently (medium) | **Accepted.** Per-case lock entries kept under every deviation row (§1.1, §4.2 H0-18, FIDELITY.md §3.1) |
| B-25 | Canon's whitespace rule cannot hide U16, and U16 is not only whitespace (high) | **Accepted.** Canon's whitespace rule narrowed and U16 kept as its own row (FIDELITY.md §2); no ARCHITECTURE change |
| B-26 | The number normaliser drops exponent signs, and Band B 'number text' conflicts with Band A 'numbers in text at 1e-9' (medium) | **Accepted.** Exponent signs kept and the number bands reconciled (FIDELITY.md §2); no ARCHITECTURE change |
| B-27 | Canon sorts rows and drops NA spelling, so the façade contracts the plan keeps are no longer tested (medium) | **Accepted.** `ordered`/`na_strict` in canon.toml for façade contracts (§1.1 F3, §4.2 H0-17, FIDELITY.md §2) |
| B-28 | power's protection is misdescribed, and the validated-module case count mixes statuses and misses power's LLM cases (medium) | **Accepted.** Power's protection restated per FIDELITY.md §10 risk 1; case count split by status, LLM cases included (§4.1) |
| B-29 | "At most 131 rows" is the wrong bound for one-file-per-module rows, and 925 marks sit on non-module cases (low) | **Accepted.** ≈ 200-280 rows in `parity/deviations/<area>.toml`; non-module marks counted (§4.2 H0-18, §6 decision 1) |
| B-30 | The sign-off rule is not enforceable as written, and tier-2 Band A changes in validated modules escape it (medium) | **Accepted.** Code-owner approval on any tier for modules the snapshot labels validated (§1.1 F1, §4.1 G1, §4.2 CODEOWNERS row) |
| B-31 | Making LLM request bodies free breaks the body-hashed mocks behind about 330 LLM parity cases (medium) | **Accepted.** Request bodies stay byte-identical for mock-replayed modules, about 330 cases (§3.4, §3.5, §6 decision 16) |
| B-32 | Field-scope rule empties the `validated` policy on the default preset, the web app and the API (high) | **Accepted.** Field-scope rule dropped; the validated policy is `select(...)` = 5 modules, tested (§4.1 G9, ECOSYSTEM.md §2-3) |
| B-33 | Certification-to-code binding is unsound as specified: what is hashed is both too broad and too narrow, and the built-in range is pre-granted (high) | **Accepted, changed.** Exact-release binding plus rerun; G9 checks version and digest in the certification list (§4.1 G9, ECOSYSTEM.md §2.5) |
| B-34 | No bootstrap step: TIERS ships a snapshot of a registry that does not exist yet (medium) | **Accepted.** TIERS ships a provisional snapshot and asks the team (§4.3 TIERS, ECOSYSTEM.md §8.1) |
| B-35 | The 'validated set' differs across the three docs (5, 6 or 7), and the gates depend on it (medium) | **Accepted.** One validated set of 5, stat_check and ref_accuracy conditional; gates apply to every module the snapshot labels validated (§1.1, §4.1) |
| B-36 | stat_check is proposed as validated on evidence the doc rejects for coi_check and funding_check (low) | **Accepted.** Stat_check conditional, pending certification (§3.5, ECOSYSTEM.md §2); no other ARCHITECTURE change |
| B-37 | Five labels, a four-rung policy ladder and policies stored in the registry are more than the three-way ask needs, and the record contradicts 'every label is derived' (medium) | **Accepted.** Labels are derived, no stored policies; `unvalidated` used (§0, ECOSYSTEM.md §2) |
| B-38 | The library default `experimental` silently drops every store module, which contradicts 'report(paper) does not change' and the marketplace (medium) | **Accepted.** An installed store module named by a preset still runs under the default; `report(paper)` unchanged (§4.3 TIERS gates) |
| B-39 | Removing `metacheck::validated` outright breaks saved configs and tests (low) | **Accepted.** `metacheck::validated` kept as a deprecated alias until 1.0 (§4.3 TIERS, §4.6, ECOSYSTEM.md §3.2) |
| B-40 | The official store is itself a private repository, so the §4.11 deletions and the Pages catalog would cut every user off from it (high) | **Accepted.** Store stays private for now; no user is cut off; going public is decision 13 (§6 decision 13, §4.3 STORE-2b preconditions, ECOSYSTEM.md §4.11) |
| B-41 | §4.11 deletes the project-config code-trust gate, which is what stops a cloned repo from running code, while pushing labs to path packs (high) | **Accepted.** Project-config trust gate kept (§6 decision 13, ECOSYSTEM.md §4.11) |
| B-42 | The re-pin bot can bypass the review gate, and its PRs will not run CI or the pack tests the doc promises (medium) | **Accepted.** Re-pin bot PRs go through review and CI (ECOSYSTEM.md §4); no ARCHITECTURE change |
| B-43 | Store and core CI run third-party pack code with secrets and write tokens in scope (medium) | **Accepted.** Pack code runs in CI with `permissions: {}` and no secrets (§2.11 G7 job, ECOSYSTEM.md §4) |
| B-44 | 'Clone plus path pin' loses pinning, hosted use and default-policy inclusion; a token-only GitHub path is simpler and keeps them (medium) | **Accepted.** Token-only GitHub path for private packs replaces clone plus path pin (§4.3 STORE-2b, §6 decision 13, ECOSYSTEM.md §4.11) |
| B-45 | Status has two channels and a configurable certifier, which contradicts 'only the official store certifies' (medium) | **Accepted.** One status channel; status comes only from the team's registry, and no store certifies; packs carry self-reported `validation` only (§2.11, ECOSYSTEM.md §2) |
| B-46 | 'Server-safe' has no source of truth, no owner and no enforcement, and hosted mode does not load 'only store packs at public revs' (medium) | **Accepted.** `server_safe` is a field in validation.json, closure-checked, hosted filter owned by WEB, 400 lists the modules (§3.7) |
| B-47 | STORE-2's §4.11 deletions edit files that CORE owns, and its 3 days do not cover the scope (medium) | **Accepted, changed.** Split into STORE-2a (1.5 d) and STORE-2b (1.5-2 d, planned 2); core-owned rows go as change requests (§4.3, §4.4) |
| B-48 | The ecosystem LoC figures are internally inconsistent and understate the growth (medium) | **Accepted, changed.** One ledger in §5.3 carries the ecosystem items; ECOSYSTEM.md §0 points to it (§0, §5.3) |
| B-49 | Self-consent rule can be bypassed by forking, and the two docs' definitions of it disagree (high) | **Accepted.** Self-consent rule defined once and fork-proof (ECOSYSTEM.md §5); no ARCHITECTURE change |
| B-50 | Relicensing by one GitHub comment is stated too confidently, and the blocked/needs-relicence verdicts contradict the matrix (medium) | **Accepted.** Relicensing wording softened and verdicts aligned with the matrix (ECOSYSTEM.md §5); no ARCHITECTURE change |
| B-51 | Licence matrix: the CC0 row is wrong and inconsistent; CC BY-SA 4.0 is not actually uncertain (low) | **Accepted.** Licence matrix corrected (ECOSYSTEM.md §5); no ARCHITECTURE change |
| B-52 | The plug-in claim ('a pack's licence must be GPL-compatible') is presented as law; it is store policy (low) | **Accepted.** GPL compatibility stated as store policy (ECOSYSTEM.md §5); no ARCHITECTURE change |
| B-53 | The headline '≈1,250 new lines' undercounts the ecosystem by about half (medium) | **Accepted, changed.** The single ledger counts core +2,500, status/store +600, compat +300, port +1,260, web +215 (§5.3) |
| B-54 | The persistent R worker and the --allow-r runner are over-engineered for a 15-second check (medium) | **Accepted.** No R worker, no --allow-r runner; PORT is 5 d (§4.3 PORT, ECOSYSTEM.md §5.3) |
| B-55 | port diff is not reproducible for a pip-installed porter, and store CI cannot re-check the verdict (medium) | **Accepted.** Port diff made reproducible and re-checkable in store CI (ECOSYSTEM.md §5); no ARCHITECTURE change |
| B-56 | No flow for upstream updates to a ported R module; consent.tree_sha256 is ambiguous (low) | **Accepted.** Upstream-sync flow and the tree hash defined (ECOSYSTEM.md §5, FIDELITY.md §7); the sync lands in HARNESS-v2 (§4.3) |
| B-57 | What an R author does to get a module into the store is never spelled out; 'R-only packs are listed' leads nowhere (low) | **Accepted.** The R author's route into the store spelled out (ECOSYSTEM.md §5); no ARCHITECTURE change |
| B-58 | Port output target contradicts ARCHITECTURE, and the translation brief targets R-shaped compat APIs (medium) | **Accepted.** Port output targets `@module` and the core APIs, not compat (§4.3 PORT, ECOSYSTEM.md §5) |
| B-59 | "≈ 30,000 more lines" of de-emulation contradicts §3.5's own ≈ 11,150 (high) | **Accepted.** De-emulation figure set to ≈ 11,000 everywhere (§0, §5.3) |
| B-60 | The ≈ 88,000 headline rests on a −4,000 plug row, and §3.6's per-directory targets contradict the sweeps and CODE-a's gate (high) | **Accepted, changed.** The −4,000 plug row is gone; §3.6's per-directory targets sum to ≈ 93,300 and match the sweeps (§3.6, §5.3) |
| B-61 | The revision 2 column of §5.3 sums to ≈ 103,000, not 98,000, so '−22% → −30%' and 'the extra ≈ 10,000 come from relaxed fidelity' are not like-for-like (medium) | **Accepted.** Revision 2 re-measured at ≈ 103,000 (−18%); the comparison is like-for-like (§0, §5.3) |
| B-62 | The day total subtracts 4 days that the same table says were not taken out of any package (low) | **Accepted, changed.** The ≈ 4 days of contingency are calendar slack on the critical path (27 → ≈ 31), not package-days; the 116.5 total neither includes nor subtracts them (§0, §4.4) |
| B-63 | FIDELITY.md places HARNESS-v2 before CORE-1b deletes anything; the schedule runs them in parallel, and both own the private-case re-pointing (medium) | **Accepted.** HARNESS-v2 runs in parallel with CORE-1b and owns the re-pointing until day 13 (§4.2, §4.3, §4.5, FIDELITY.md §11) |
| B-64 | report/** has two concurrent owners (CORE-1e and SERVICES-b), and the presets.py hand-off contradicts itself (medium) | **Accepted, changed.** Report/** split by file (CORE-1e owns render.py/blocks.py, then SERVICES-b); presets.py dated; no market/ move (§4.3, §4.5) |
| B-65 | parity/** is exclusive to HARNESS-v2 from day 4.5, while HARNESS-NET and BATCH-a own parts of it at the same time (low) | **Accepted.** Parity/** carve-outs for HARNESS-NET and BATCH-a (§4.5) |
| B-66 | PORT owns porting/** exclusively, but other PRs must edit it, and FIDELITY §7's porting-map and upstream-sync changes have no package or days (medium) | **Accepted.** Porting/** rows split; porting map, sync and NOTICE get 1.5 d in HARNESS-v2 (§4.3, §4.5, FIDELITY.md §11) |
| B-67 | The CORE checkpoint (day 17.5) comes after critical-path wave work has started, so decision 5's stop point is not a real stop (medium) | **Accepted, changed.** Checkpoint moved to CORE-1d's close, day 13.5, before any wave; stop points at days 2, 11.5 and 13.5 (§4.3, §4.4, §6 decision 5) |
| B-68 | `requires=` already means capabilities in @module; the plan reuses it for module dependencies (high) | **Accepted.** `depends=` for modules; `requires=` keeps its capability meaning (§0, §2.7, §2.10, §3.1) |
| B-69 | Dependency semantics are undefined: arguments, visibility, status policy and chain=False (medium) | **Accepted.** Five dependency rules: arguments, visibility, status policy, capability union, chain=False (§2.7) |
| B-70 | Typed artifacts are over-built for CORE-1d and conflict with ownership and sequencing; the existing run memo already removes the re-runs (medium) | **Accepted, changed.** No typed artifacts; the run memo serves repeats; CORE-1d +0.5 d (§2.7, §4.3) |
| B-71 | Removing the fence parser breaks the permanent `scroll_table`/`collapse_section` façades, the only store pack and every R port (high) | **Accepted.** The fence parser moves to compat and serves v1 string reports; façades return blocks (§2.9, §3.1) |
| B-72 | The `Result` API is inconsistent across §2.9/§2.10 and contradicts 'DataFrames only at the edge' (medium) | **Accepted.** One `Result` definition with `to_frame()`/`as_dict()`; DataFrames only at the edge (§2.9, §2.10) |
| B-73 | `string.Template` templates cannot hold the branching and plural logic; typed blocks plus TOML templates plus fence façades make three report mechanisms (medium) | **Accepted, changed.** Per-check `render()` with f-strings and `plural`; no template files; two mechanisms, blocks and the v1 fence reader (§2.9, §4.3 CORE-1e) |
| B-74 | The TOML check form is justified by a translator output the porting design does not have (medium) | **Accepted, changed.** Option (c): the TOML check form is deferred (§2.10, §2.13, §4.3, §6 decision 2) |
| B-75 | Target layout, frozen import paths and deprecation windows do not fit together; the waves depend on the renames anyway (medium) | **Accepted, changed.** No directory renames; stable import paths with one deprecation rule (§3.6, §4.3 CLOSE, §6 decision 21) |
| B-76 | The new top-level API disagrees with ECOSYSTEM and with the web app, which is built on names the plan deprecates (medium) | **Accepted.** Top level check/Paper/read/Settings/configure/status/status_table/hosts, as in ECOSYSTEM.md §3.3; WEB moves onto `pc.check` in CLOSE (§2.10, §4.3) |
| B-77 | The `format_num` claim is wrong: R's number text is not produced in the report path (medium) | **Accepted.** `format_num` scoped to statout and LLM prompts (§2.9, §3.4) |
| B-78 | No operating loop for re-certifying built-ins at each release, so the validated set (and the public server) can empty on any release (high) | **Accepted.** Per-release rerun by a named validator, with ECOSYSTEM.md §2.5's one-release grace (§4.6) |
| B-79 | The plan relies on the metacheck team's agreement and never asks for it or says what happens if they decline (high) | **Accepted.** Asks to the team sent before TIERS closes, with fallbacks (§4.3 TIERS, ECOSYSTEM.md §8.1) |
| B-80 | No release or user-migration plan: versions, what the first release contains, and when breaking changes are documented (medium) | **Accepted.** Releases 0.4-1.0 with breaking changes and a MIGRATING section each (§4.6) |
| B-81 | The hosted service has no deployment, operator or cutover plan, and 'makes no third-party calls' overstates privacy (medium) | **Accepted, changed.** Hosting is out of scope; WEB builds the app, the cutover and the pin; privacy wording qualified (§0, §3.7, §6 decision 20) |
| B-82 | AGPL §13 source offer is specified but belongs to no package, and it does not cover the Gradio UI (medium) | **Accepted.** `GET /source` and a visible link, built by WEB; the report's "Source" footer, built by SERVICES-b (§3.7, §4.3 WEB and SERVICES-b) |
| B-83 | The LoC goal ignores the test suite, which is almost as large as the source (medium) | **Accepted.** Tests in the ledger: 106,358 → ≈ 90,000, with a measured sweep in CLOSE (§5.3, §4.3 CLOSE) |
| B-84 | PORT's pilot has no named target module, and waiting for an author's consent is not in its 5 days (medium) | **Accepted, changed.** PORT's pilot is a round-trip on marginal.R, so no author consent is waited on (§4.3 PORT, ECOSYSTEM.md §5.2) |
| B-85 | The web app's privacy guarantees rest on Gradio 6.28-specific flags, with no version pin and no ongoing check (low) | **Accepted.** `gradio>=6.28,<7`, a unit test per fixed setting and a nightly check; the upgrade-churn risk is stated (§3.7) |
| B-86 | The amended H0-2 pins report tables more loosely than §2.3's report-section rule (low) | **Accepted.** H0-2 compares report tables as row multisets through canon (§4.2 H0-2) |
| B-87 | ARCHITECTURE contradicts itself on funding_check_oi's row order, and on the count of private-helper cases (low) | **Accepted.** Funding_check_oi document order everywhere, noted in release notes; 1,497 everywhere (§2.13, §4.2, §4.5) |
| B-88 | Status label `unreviewed` clashes with the store's existing 'reviewed' flag, so a reviewed store pack gets an 'Unreviewed' badge (low) | **Accepted.** Label renamed `unvalidated` (§0, ECOSYSTEM.md §2) |
| B-89 | §3.5 and §1.3 misdescribe `Selection.dropped`, and the server rule for explicit modules says both 'filter' and '400' (low) | **Accepted.** `dropped` stays refs only and the server rule is 400 (ECOSYSTEM.md §1.3, §3.5; §3.7) |
| B-90 | The marketplace layer is heavier than the two-pack store needs; the skills-marketplace UX already exists (low) | **Accepted.** Marketplace layer trimmed (ECOSYSTEM.md §4); no ARCHITECTURE change |
| B-91 | The path of the validation.json snapshot differs between the two docs (low) | **Accepted.** One path, `src/pytacheck/resources/status/validation.json` (§4.3 TIERS, §4.5) |
| B-92 | DCO sign-off on the store repository certifies the wrong commits (low) | **Accepted.** DCO scope corrected (ECOSYSTEM.md §4); no ARCHITECTURE change |
| B-93 | Attribution rules for store ports are stricter than what pytacheck applies to its own port of metacheck (low) | **Accepted.** Attribution rules aligned with pytacheck's own NOTICE (ECOSYSTEM.md §5; NOTICE in HARNESS-v2, §4.3) |
| B-94 | WEB's placement is inconsistent, and WEB runs before the CORE-1d graph it relies on (low) | **Accepted, changed.** WEB runs after CORE-1d, days 13.5-16, so it needs no deny-list (§3.7, §4.3, §4.4) |
| B-95 | The 'hold parallelism to 7 at no cost' recipe still gives 8 agents on days 19.5-21.5 (low) | **Accepted, changed.** COMPAT placed after PATTERNS/STATS/REFS at days 21-23.5 in the new schedule, and BATCH-a at days 6.5-8.5, so days 4.5-6.5 count DATA-a and CODE-a as two agents and still reach only 7; peak stays 7 (§4.3, §4.4) |
| B-96 | Stale and mismatched numbers in the package and size text (low) | **Accepted.** CORE-1b reads 5 (unchanged), HARNESS 4.5 d, today 125,800, pattern lists in `modules/patterns/*.toml` (§0, §3.5, §4.2, §4.3) |
| B-97 | compat is sized at +1,800 lines although the lazy-export mechanism already exists; `Host` covers only 50 of the 75 host exports (low) | **Accepted.** Compat re-sized to +300 on the existing `_EXPORTS` map; `Host` covers 50 of 75, CORE-1e lists the other 25 (§2.10, §3.6, §4.3 COMPAT, §5.3) |
| B-98 | The directory renames are churn without a payoff (low) | **Accepted, changed.** No renames; decision 21 recommends (a) (§3.6, §6 decision 21) |
