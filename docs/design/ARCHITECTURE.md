# Pytacheck architecture: an indexed-document core, and the rewrite onto it

**Status.** Revision 3 (2026-09-26), a structural re-plan on top of revision 2 (the spike, its review, and the baselines re-measured after CORE-0, §5.4). Revision 3 adopts two companion proposals and re-plans around them (§0, "Revision 3"):
- [FIDELITY.md](FIDELITY.md): results locked, presentation free. It replaces F1-F3 and amends F5.
- [ECOSYSTEM.md](ECOSYSTEM.md): validation status, presets, the module store as a marketplace, and porting R modules.

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
- **(M3)**: measured for revision 3 at f559c1be. The notes and scripts are in this session's scratchpad, `/tmp/claude-0/-home-user-pytacheck/b01f2e9f-255f-5b7e-9d0d-70c9d055b185/scratchpad/research/` (`results/structure.md`, `results/emul-{paper,repo,services}.json`, `idioms.py`, `anatomy.py`, `fnsizes.py`).
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

A core package, `src/pytacheck/core/` (≈ 3,300 lines, ≈ 700 of them moved; §5.3):

1. **`Doc`**: each paper indexed once, as column lists aliased from the reader's records, with lazy casefolded text, cleaned text per chain stage, and paragraph and section groups. The cache is trusted only while the tables are raw, or inside an internal runner.
2. **A pattern engine**: `Pat` and `PatternSet` values, compiled only through lane 2; derived required literals checked with a per-Doc literal scan; `(known, true)` int-bitset partial evaluation; short-circuit pattern sets, never one union alternation. Chains read like the check: `docs.hits(DATA).where(REPOSITORY).where(AVAILABLE)`.
3. **Facets and `RefIndex`**, computed once per Doc: p-values, equations, statcheck rows, URLs, repository links, study roster, live data; references and citations **per Doc**. DOI databases are normalised once per process.
4. **One `RunContext`** per run: the frozen `Settings` snapshot, the output memo for plain papers, run caches with single-flight, repository indexes and counters. One executor serves all runners, and the batch engine runs on it in paper mode.
5. **An explicit output boundary**: a module returns a dict, a DataFrame or a `Result`; `Schema` types the frames; the summary merge runs on dicts. A module called directly still returns a plain `dict`.
6. **Store contract v1, unchanged**: `f(paper, **kw)`, the metacheck façades re-implemented on the core with their R semantics, no new schema, no new enforcement. Revision 3 adds one additive field, `status` (§2.11).

### What revision 3 changes

Revision 2 kept R's output shape everywhere (old decision 1a), so every de-emulation had to reproduce R's presentation too. Revision 3 drops that constraint for presentation only.

| Change | What it means | Where |
|---|---|---|
| **Fidelity bands** | Results (lights, flagged rows, statistics, references, values) stay exact. Order, dtypes, number text, whitespace and error text become free. Wording is recorded with one scoped row per change | FIDELITY.md; §1.1 |
| **Checks produce data; the report renders it** | `Result` is the main return type. Report text comes from per-check templates and typed blocks, not hand-written Quarto markdown. `ModuleOutput` becomes a compat adapter | §2.9 |
| **Enforced dependencies** | `requires=` replaces the advisory `uses=`. The executor runs each dependency once per run and passes typed artifacts, which ends nested module runs | §2.7 |
| **R names move to `pytacheck.compat`** | The main entry point becomes `pc.check(paper, preset=…, status=…)`. The 303 R-shaped exports stay importable from compat, and from the top level with a deprecation until 1.0 | §2.10 |
| **Validation status and presets** | A registry owned by the validation team labels each module (validated, external-validated, experimental, unreviewed, withdrawn). A preset says what to check, and a status policy says how much evidence to require | ECOSYSTEM.md §2-3 |
| **The store as a marketplace** | Modules live in their authors' repositories, pinned by SHA, with a generated catalog and nightly CI | ECOSYSTEM.md §4 |
| **Porting R modules** | `pytacheck port` translates an R module with the author's recorded consent, and checks the result against R | ECOSYSTEM.md §5 |
| **Further de-emulation** | ≈ 30,000 more lines of R emulation can go once presentation is free: wholesale library ports (ellmer, ICU, fread, knitr), value models and deparsers | §3.5 |
| **The web app** | The Shiny app is not ported. A web front end sits on the FastAPI app | §3.7 |

### Headline targets, re-based on the spike

| | Today **(M)** | Spike **(S)** | After the rewrite **(E)** |
|---|---|---|---|
| 20 offline text modules, CPU per paper | 1,165 ms | 4 modules: 427 → 36 ms | **≈ 255 ms budget (≈ 4.5x)**; ≈ 215 ms if CORE 1e cuts the boundary floor to 3 ms. Basis: §5.1 |
| Accuracy matrix, Python side (439 outputs) | 35.7 s; R 91.6 s. **16.9 s after CORE-0 (C)** | 21.4 s, with only the façade and 4 modules | ≈ 14-15 s after CORE (was ≈ 15-18 s, already reached by CORE-0); ≈ 10-12 s at the end |
| 1,000 offline XML papers, 19 modules | ≈ 12.7 min | – | ≈ 6-7 min at `-j 1`; ≈ 2-2.5 min at `-j 4` on idle cores (scaling unmeasured) |
| `src/pytacheck` | 125,200 lines | core subset 1,975 lines | **≈ 88,000 (−30%; range 82-92k)**. Revision 2 targeted ≈ 98,000 (−22%). The extra ≈ 10,000 come from relaxed fidelity (§3.5, §5.3) |
| Work | – | – | **≈ 111 agent-days in 33 packages** (≈ 109 without the optional TOML package; CORE-0's 1 day is done); **critical path ≈ 28 days** (≈ 32 with slack). Revision 2: 78 days in 27 packages, critical path 25 (§4.4) |

All hard per-module budgets stay **provisional** until W1 (PATTERNS) lands and re-measures them.

---

## 1. Principles

### 1.1 Fidelity contract

Revision 3 replaces F1-F3 with FIDELITY.md's three bands (decision 1). F4 and F6 are unchanged, and F5 is amended.

**Terminology.** Revision 2's U-entries (R bugs fixed) and D-entries (deliberate differences) become deviation rows in `parity/deviations/<module>.toml` (FIDELITY.md §3). Where text carried over from revision 2 says U-entry or D-entry, read "deviation row". `docs/UPSTREAM_ISSUES.md` keeps only the metacheck bugs that touch Band A.

**F1. Results are exact and never traded for speed** (Band A). On realistic inputs these do not change without a deviation row: traffic lights, which rows are flagged, counts, extracted statistics and references, values, the numbers inside summary texts, and whether an output is an error or a value. A prefilter may never drop a true match (invariants I1-I3, §2.3). In a module the registry lists as validated, a Band A change also needs a validator's sign-off or a re-validation (ECOSYSTEM.md §2.5).

**F2. Modules stay recognisable to upstream**, which keeps the daily upstream sync tractable:
- every R module maps to a named Python check (`porting/modules.toml`; the map can be many-to-one, as for the `_oi` variants);
- pattern lists stay verbatim R-syntax constants, and a test compares each with R's vector;
- traffic-light rules and summary column names are kept.

Internal file mirroring, private-function mirroring and "Port of R/…" docstrings go. The sync compares R's behaviour, not its code (FIDELITY.md §7).

**F3. Presentation is free, and wording is recorded** (Bands B and C).

| Aspect | Rule |
|---|---|
| Row order, column order, extra columns, dtypes, NA spelling, list-column shapes, number text, whitespace, error text | **Band B.** Removed by the canonicaliser before any comparison; nothing is recorded |
| Column names | An API. A rename is Band C with a deprecation for one minor release |
| Wording of summary text, report text and guidance | **Band C.** One scoped row in `parity/deviations/<module>.toml`; mandatory for validated modules |
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
- R's number text lives in the report renderer, the only place users read it;
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
| **Total** | | **1,975 → ≈ 3,300 (≈ 700 moved)** |

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
    def override(self, **settings): ...         # e.g. llm_use=False, scoped
```

- `run_session()` stays public with today's documented contract: it memoises repeated module runs and grants no Doc trust. `docs/MODULES.md:636` keeps its warning about in-place edits.
- `module_run` alone opens a transient context: memo off, no trust.
- `local_options` becomes a context-local overlay (L6-1). `_reproducibility`'s process-wide `llm_use(False)` becomes `with current().override(llm_use=False):`.
- A test asserts that `Settings.capture()` covers every setter exported from `pytacheck/__init__.py`.

**Lifetimes.** Per process: compiled patterns, literal CNFs, DOI dictionaries, host limiters, resolved module specs, file modules. Per run: the output memo, run caches, `RepoIndex`es, `Settings`. Per paper generation: Doc, masks, facets, `RefIndex`. Per call: `get_prev_outputs`.

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
- `@module(requires=["repo_check"])` is a static literal. `execute()` builds the graph, adds missing dependencies before the modules that need them, runs each (module, arguments) once per run, and caches the result in the `RunContext`.
- The executor never reorders what the user listed; it only inserts dependencies before their first user. A test pins this.
- A dependency is passed as a **typed artifact** instead of a string-keyed `extras` entry: `RepoIndex`, `DataProfile`, `CodeIndex`, `Codebook`. data_check alone has 9 such keys today **(M3)**.
- The one cycle (data_check reads codebook_check's table while codebook_check runs data_check) is broken by a shared `Codebook` artifact that both read.
- Scoped settings replace the `llm_use` toggle: a dependency runs under `Settings.override(llm_use=False)`, which is context-local and thread-safe.
- `get_prev_outputs` still returns DataFrames, through compat.
- Expected effect on the reproducibility_check chain (E, from the M trace): 7 nested module runs → 1 run per module, and 18 file opens → ≤ 6. `module_run` = `_resolve` (cached per process) → bind → `_call` → `_assemble(spec, label, raw, input)`. `module_run` overhead is 0.33 ms per call **(S)**.

### 2.8 Batch engine

`BATCH_DESIGN.md` stands (R1-R7, G1-G7, run shape, API, CLI, error isolation, output folder, resume, rejected ideas) except for these changes:

| BATCH item | With this core |
|---|---|
| Layer 1 (F1-F4 fixed costs) | Replaced by Doc, RefIndex, DOI dictionaries and facets; L5-1 to L5-3 dropped |
| N1 host limiter, rolling window | BATCH-a (BL-1), right after phase 0 |
| N2 `run_cache` | `RunContext.cache` (CORE 1d); HTTP memo wired by BATCH-b (BL-2) |
| N3 key dedup, PubPeer chunks, `add_bib_match` groupby | REFS (L7-1 to L7-3); `osf_type` via the run cache in LINKS (L7-5) |
| N4 threads for hosts, Grobid/bibr/RegCheck jobs, causal | REPO (L7-6), SERVICES-b (L7-7), LINKS (RegCheck), REFS (L5-8) |
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
- **render** fills a declared template, one per check, with `string.Template` and a `plural` helper (no new dependency).

The report is built from typed blocks (`Paragraph`, `Table`, `Callout`, `Ref`, `Badge`), written straight to HTML, GFM or `.qmd`, so the fence parser goes. Wording changes become template diffs, which are Band C.

```python
Kind = Literal["string", "Int64", "Float64", "float64", "boolean", "list"]   # r15/r7 removed (Band B)

@dataclass(frozen=True)
class Schema:                      # explicit, never derived from annotations
    cols: tuple[tuple[str, Kind], ...]
    def frame(self, records) -> pd.DataFrame: ...

@dataclass
class Summary:                     # one row per resolved paper id
    paper_ids: Sequence[str]; rows: Mapping[str, Mapping[str, Any]]; schema: Schema

@dataclass
class Result:                      # a builder; not a Mapping
    table: pd.DataFrame | None = None
    summary: Summary | pd.DataFrame | None = None
    traffic_light: str = "info"; summary_text: str | None = None
    report: Any = None; na_replace: Any = None
    extras: dict[str, Any] = field(default_factory=dict)
    def as_dict(self) -> dict[str, Any]: ...    # key order kept; Summary framed once and cached

def sentence_table(hits, /, **flags) -> pd.DataFrame: ...
def stack(frames) -> pd.DataFrame: ...
```

**Direct calls return a plain `dict`** (Review B-6). The `@module` wrapper converts a `Result` with `as_dict()`, and the executor calls the unwrapped function to receive the `Result`. The spike's `Result` was a read-only `Mapping`: item assignment, `pop`, `copy` and `update` raised, `isinstance(r, dict)` was false, and `r["summary_table"]` was rebuilt on every access, so edits to it were lost **(R)**. A test asserts that every built-in module's direct return value is a mutable `dict`.

**Summary merge on dicts:** a left join on the resolved input ids, with dplyr's suffix rule (**append the suffix again while the name is taken**, giving `.ethics_check.ethics_check`) and U77's `na_replace` scope (Review B-18). The spike overwrote the second run's columns on a third chained run; the merge differential test gains a 3-deep repeated-module chain. With F6 there are no repeated ids, so the `DataFrame.merge` fallback goes.

**The boundary floor is the budget driver.** It is the per-output cost of `Schema.frame`, `sentence_table`, `Summary.frame` and `_assemble`. The spike measured ≈ 7.4 ms in ethics_check (5.7 ms of tables, Summary and report, plus 1.7 ms of `_assemble`) and ≈ 6.5 ms in stat_p_exact (≈ 4 ms of frames plus 2.5 ms of merge) **(S)**, against 0.3-0.5 µs for the dict merge alone **(M)**. CORE 1e profiles it on the six SPIKE-2 modules and targets ≤ 3 ms per module **(E)**. §5.1's budgets assume 5 ms until then.

**`Result` is the primary type; `ModuleOutput` becomes a compat adapter** (revision 3).
- `pc.check()` returns a `Report` of `Result`s, with `result.rows`, `result.summary` and `result.to_frame()`. DataFrames are built only there, at the edge. pandas object construction is 54% of self time today **(C)**.
- `ModuleOutput` stays an eager dataclass with its fields, key order, pickling and canonical form, built from a `Result` by `pytacheck.compat`. Lazy properties stay rejected (they break `dataclasses.replace`, `fields` and equality, and move errors past the failure policy).
- A check's direct call still returns a plain mutable `dict` (Review B-6), so contract-v1 store modules and user scripts keep working.
- Numbers stay floats until the renderer formats them. `_r.format_num` (26 call sites in 10 files) moves into the report renderer, and `Kind` loses `r15` and `r7`.

### 2.10 Module API and façades

**Contract v1 is frozen** (the store, the template, `docs/MODULES.md`, 1,048 `module:` parity cases): `f(paper, **kwargs)` with a `Paper` or `PaperList`; output a dict, a DataFrame or (new) a `Result`; the input not mutated; valid traffic light; a summary with `paper_id`. `@module(...)` takes literal metadata scanned by `packs/scan.py`. It accepts unknown literal keywords with a warning instead of a `TypeError`, and an optional `requires=` (§2.7).

**Revision 3: the main API is `pc.check`, and the R names move to `pytacheck.compat`** (decision 15).
- Today `_EXPORTS` (`__init__.py:26`) has 303 flat names against R's 300, including 25 `data_check_*` names, 15 `code_*` names and about 75 archive-host functions (`*_links`, `*_info`, `*_file_download`, `*_pat`) **(M3)**.
- The new top level is small:
  - `pc.check(paper, preset=…, status=…, settings=…) -> Report`;
  - `pc.Paper`, `pc.read`, `pc.Settings`, `pc.configure`;
  - `pc.hosts`, one `Host` protocol with `links`, `info` and `download`, in place of the per-host functions.
- `pytacheck.compat` holds the R-shaped names: `module_run`, `get_prev_outputs`, `paper_table`, the option shims, the `ModuleOutput` adapter and the 303 exports. The top-level names stay as deprecated re-exports until 1.0.
- The parity harness calls compat, so the public-function cases need no change.
- The "Permanent façades" list below moves into compat unchanged. Its contract to store modules does not change.

**Revision 3: declarative pattern checks in TOML** (decision 2, phase D). A check that is only patterns, section filters and a traffic-light rule can be a TOML file (`tomllib`, Python ≥ 3.11 already required) compiled to `pytacheck.doc`.
- Candidates: marginal, all_urls, the `_oi` variants, and the detection parts of ethics_check and open_practices.
- The main user is the store, and the R translator's first output format (ECOSYSTEM.md §5).
- §2.13's "four module forms" rejection still holds: there are exactly two forms, a Python check and a TOML check.

**Permanent façades**, with R semantics and DataFrame results, never deleted: `module`, `module_run`, `get_prev_outputs`; `text_search` (pattern-major order, first match wins, `exclude`, every `return_` mode); `paper_table`, `ref_table`, `paper_id`, `text_expand`, `extract_*`, `json_expand`, `stats`; `_r.frames.bind_rows` and `count`; `report.scroll_table`, `collapse_section`; `test_paper`, `demopaper`, `read`; `Paper`/`PaperList` access.

**`text_search` for character vectors with several patterns returns results** (lane 5's U-entries; Review B-15), covering `text_search.vector.multi`, `.multi.one_hit`, `.multi.no_hits`, `.multi.exclude.three`, `.four` and `.three_empty`, and `text_search.demo.multi.exclude.three` and `.four`. Invalid arguments raise Python errors with pytacheck's own messages, compared by presence. The spike's replays of dplyr's "`...` must be empty" and R's "unused argument (…)" are deleted.

**The author API** is `pytacheck.doc`: `Docs`, `Doc`, `Pat`, `PatternSet`, `patterns`, `Hits`, `Result`, `Summary`, `Schema`, `sentence_table`, `stack`, `facets`, and `testing.document(*sentences, section="method", references=(), paper_id="test")`. It stays provisional until 1.0 and is snapshot-tested.

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
    return Result(table=stack([sentence_table(eth, ethics=True), sentence_table(live, live_data=True)]),
                  summary=Summary(ids, {...}, SUMMARY), na_replace={"ethics_approved": False},
                  traffic_light=light, summary_text=_text(n_missing, len(ids), any(needs)),
                  report=_report(ids, approved, needs, e_by, l_by))
```

In revision 3 the last two arguments become data plus a template: `Result(..., text=TEXT.fill(n_missing=n_missing, n=len(ids)))` with `TEXT = template("ethics_check")`, read from `checks/templates/ethics_check.toml`. The ≈ 40% of the body that builds strings leaves the module.

**LLM grounding** is observation only (decision 4): where an LLM field claims a quote (power's `text`, causal sentences), the core checks that it occurs in its source paragraph after whitespace and case normalisation. On a miss it records a `notes` extra, one `PytacheckWarning` per run and a count in `run.json`, and changes no value.

### 2.11 Store contract and versioning

Today's parsers keep working (`packs/manifest.py:134` copies unknown keys; `packs/stores.py:190-207` ignores extra keys). **No schema bump, and no change here needs one.**
- **Revision 3 adds one field, additively:** `status`, and the structured `validation` evidence record already accepted by `@module(validation=…)` (module.py:102-103). An author's own numbers show as "self-reported". The label that policies filter on comes only from the team's registry (`validation.json`), never from the pack (ECOSYSTEM.md §2). Old pytacheck versions ignore the field.
- **`store search` and presets can filter on status** (ECOSYSTEM.md §3). This is the one filtering that revision 2 rejected and revision 3 needs.
- **`module_api` is reserved but not enforced** (Review B-13). The loader reads an optional integer `module_api`, treating absent as 1, and ignores it. Nothing writes it, and `store search` does not filter on it. Enforcement arrives with a v2, if one is ever needed; v1 and v2 would then be supported side by side for at least two minor releases.
- **`requires.pytacheck`** (decision 7; Review B-7). Versions compare on `Version(v).release`, so `0.3.1.dev1` satisfies `>=0.3.1`. `packaging` alone says no, even with `prereleases=True` **(R)**, which would reject every pack on a dev build, including in G7's store CI. At load time a mismatch stays a warning, as at install today (`install.py:160-172`). It is an error only in `pack check` and `store build --check`, together with the static check that packs importing `pytacheck.doc` declare `requires.pytacheck`.
- **Minor additions** (`pytacheck.doc`, `Result`, `requires=`) are gated by `requires.pytacheck`.
- **G7:** pytacheck's CI checks out `pytacheck-modules` and runs `store build . --check`, `pack check packs/*` and the packs' pytest on every core PR. The store's only pack (clinical_trials) gave byte-identical outputs on base and spike across 22 papers and a probe paper **(R)**.

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
| YAML declarative modules now | Revision 3: **reopened as TOML checks in phase D** (decision 2). F2's module-per-R-module objection is gone, and the store and the R translator need a no-code form. Still not now: it waits until `pytacheck.doc` is stable |
| `module_api` enforcement | Serves a v2 that nothing planned needs (Review B-13). Revision 3 does need `store search` to filter on **status** (§2.11), which is a different field |
| Frozen legacy module copies as the G5 oracle | Not independent: they import helpers that CORE rewrites (Review B-4) |
| Doc trust inside the public `run_session`; fingerprints | Stale `text_search` results after in-place edits **(R)**; `.loc` edits keep identity and length |
| `Result` as a read-only Mapping | Breaks callers that mutate a module's direct return value **(R)** |
| numpy masks in the Doc | 17.3 against 12.6 ms/paper with int partial evaluation **(M)** |
| A pandas text ⋈ section join inside the Doc | 6.7 against 0.08 ms/paper **(M)** |
| `Table` storage in `Paper`, the `Rows` mini-frame, interned `Pat` (D2) | Rewrites every reader to save ≈ 1 ms; grows toward pandas; value equality already shares |
| Lazy `ModuleOutput` properties (D2, D3) | Break `replace`, `fields` and equality; errors escape the failure policy |
| Four module forms, dtypes from annotations, `pack.json` schema 2 (D3) | Too many concepts; a hint edit would change dtypes; schema 2 breaks every installed pytacheck. Revision 3 has two forms (Python and TOML) and one additive field |
| Union alternation; per-sentence prefilter; `re` for simple patterns | 2.5-4.5x slower; 140.9 against 86.0 ms; case-folding risk for ≈ 0.4 s **(M)** |
| Aho-Corasick, pyarrow, polars | The prefilter is already cheap; pyarrow is 152 MB; `regex` needs `str` |
| Corpus mode (BL-8) now | ≈ 1.1-1.3x expected on the core **(E)**, plus a neighbour-invariance risk (decision 3) |

Revision 3 dropped one rejection: **document order where R shows pattern-major order**. Row order is Band B (FIDELITY.md §8), so funding_check_oi may use document order, and nothing is recorded. The `text_search` façade keeps pattern-major order.

---

## 3. Subsystems

### 3.1 What happens to each package

Written for revision 2. Where §3.5 goes further (for example `json_expand`, the report deparser, `api/jsonlite.py`), §3.5 wins.

| Package | Onto the core | De-emulated (paused lane) | Kept, because outputs depend on it | WP |
|---|---|---|---|---|
| `papers/` | `paper_table`, `paper_id`, `ref_table` become Doc/RefIndex views; `_derived` slot; `papers/ids.py` (F6) | – | schema, coercion, `records_to_frame`; the `Paper` API | CORE 1b/1c |
| `text/`, `stats/` | `text_search`, `text_expand`, `extract_*` become façades; `stats()` on `statcheck()`; `extract_tests` on `equations()` | lane 5: `json_expand`'s jsonlite model → ≈ 150-line expander (null → NA, U) in CORE 1c; `stats()` keyword arguments; long-double `R_strtod`/`formatReal` → floats + `r15`; ICU rank and `towlower` dropped; unequal-n in closed form | statcheck 1.5.0 logic, including its three-valued control flow; `_rmath` | CORE, STATS |
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
| `report/`, `api/` | loops through `execute()`; `DEFAULT_MODULES` from the `metacheck::default` preset; `report(list)` ids via F6 | lane 7: `_Deparser` → ≈ 80-line R-literal writer | blocks, renderer, templates; `api/jsonlite.py` | CORE 1d, SERVICES-b |
| `packs/`, `module.py`, `provenance.py`, `presets.py` | resolve/call/assemble; `requires` per §2.11; path packs checked with one `stat` per run | the R "unused argument" replay | `@module`, `ModuleSpec`, `ModuleOutput`, packs, presets, `RunRecord` | CORE 1d, MODSYS |
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
    def listing(self): ...                      # archives.listing adapter (REPO), host threads (L7-6)
    def fetch(self, entries, policy): ...       # shared Settings.download_dir
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
| stat_effect_size long double → float + `r15`; ICU rank, `towlower`, `INT_MAX` removed; closed-form `d_min`/`d_max`; `stats()` keyword arguments | STATS |
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
  - R number formatting survives only in the report renderer;
  - ICU-like collation survives only for sorted lists users see, after an audit of the 55 `r_sort_key` call sites;
  - haven/vctrs/readxl shaping keeps values and labels, not dtypes;
  - `%.17g` request bodies stay only for power, which is validated and prompt-locked;
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
| ellmer port (`llm/providers.py`) → a thin httpx client for OpenAI-compatible, Anthropic and Gemini | 2,096 → 700 | LLM-a | fewer built-in providers; request bodies byte-identical only for power |
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
| statcheck port's R runtime and nmath → Optional values and `scipy.special` | 1,711 → 860 | STATS (+1 d) | edge cases; strict differential gate, because stat_check is validated |
| Two Grobid converters (legacy and 12.x) → one | 300 → 0 | SERVICES-a | **results**: a Band A row |
| `presets.py` gains the status axis | 585 → 650 | TIERS | **results**: intended |

**What stays exact while these go:** the detections, traffic lights and extracted numbers of every module. The canonicaliser (FIDELITY.md §4) is what makes that checkable without pinning presentation. **Edge-case** items change results only on rare inputs (non-UTF-8 code, computed chunk options, unusual CSVs). Each gets one scoped deviation row naming the inputs, and none of them touches a validated module's Band A outputs.

**Top wins by lines per day (E):** compact parser tables (≈ 8,500/day), ICU → `_decode.py` (≈ 3,700/day), the SPV `ast` whitelist (≈ 1,800/day), the LLM `.rds` cache (≈ 1,300/day), osf_metadata (≈ 1,200/day), read.table (≈ 1,100/day), purl (≈ 1,000/day), and `json_expand` (≈ 740/day).

**Structural items that come with it** (§2.7, §2.9, §2.10):
- `_oi` variants become `coi_check(variant="oi")` and `funding_check(variant="oi")`, with the old names kept as registry aliases (−60 to −100 lines).
- The p-value trio reads one shared `p_values` facet, so extraction runs once per paper, not three times (−150 to −250 of 485 lines).
- `_funding._synonyms` (319 lines of string building) and the other pattern lists move word for word into `checks/patterns/*.toml`, so the upstream sync becomes a data diff.

### 3.6 Target layout (revision 3)

```
src/pytacheck/                                             now (M3) -> target (E)
  core/        doc, patterns, facets, refindex, execute, result,
               output, templates, settings, values                          7.6k -> 5.0k
  checks/      paper checks; templates/*.toml, patterns/*.toml             10.6k -> 6.0-6.5k
  repository/  RepoIndex, fileinfo; files, data, codebook,
               psychds, code, repro sub-checks                             16.1k -> 9.0k
  datacheck/   readers, column profiling (no fread emulation)              14.5k -> 9.5k
  codecheck/   R/Python/Stata parsing, static chunks, heuristic decoding   11.8k -> 8.0k
  repro/       execution sandbox                                            3.9k -> 3.0k
  hosts/       Host protocol: osf, github, dataverse, zenodo, …            17.2k -> 11.5k
  statout/     spv, jasp/jamovi, match_reported                            12.3k -> 9.5k
  io/          grobid, bibr12, pdf                                          6.3k -> 5.5k
  llm/         thin client                                                  6.3k -> 3.3k
  db/          crossref, openalex, retraction, regcheck                     3.7k -> 3.0k
  report/      typed blocks -> html / gfm / qmd                             2.7k -> 1.8k
  stats/       statcheck, rmath                                             1.9k -> 1.6k
  market/      packs, presets, provenance, status registry, store index     6.7k -> 5.5k
  compat/      module_run, paper_table, get_prev_outputs, options,
               ModuleOutput adapter, the 303 R names                        0.7k -> 2.5k
  cli/ api/    cli, api, web app, config, http, validate                    3.4k -> 2.8k
  TOTAL                                                                   125.8k -> ≈ 88k
```

- **Total:** ≈ 88,000 lines (E; range 82-92k), 27-35% smaller than today, against revision 2's ≈ 98,000. Two estimates bound it:
  - bottom-up, §3.5's relaxed ≈ 83,250 plus the rest of `src/` (≈ 6,200, mostly `packs/`), minus ECOSYSTEM.md's `packs/` deletions (−1,000 to −1,300), plus the new core (+≈ 2,600 net) and compat (+≈ 1,800) gives ≈ 92,000;
  - the per-package targets in the tree above, which also count the structural items (templates, the repository sub-checks, the `_oi` and p-value merges), give ≈ 88,000.
- **Renames are moves, not rewrites.** `modules/` → `checks/`, `archives/` → `hosts/`, and `packs/` + presets + provenance → `market/` happen in CLOSE, with the old import paths re-exported from compat for one minor release. Nothing in the waves depends on them.
- **Tests** (106,358 lines) shrink too, from the retired private-helper cases (FIDELITY.md §6) and from `packs/` (≈ 1,100 lines). That was not measured.

### 3.7 Report and web app (revision 3)

*Pending: written from the Gradio spike's measurements.*

---

## 4. The rewrite plan

### 4.1 Gates

| Gate | Command | Passes when |
|---|---|---|
| **G1** parity | `python -m parity check --strict --area <areas> --jobs 4`; full run in CORE 1b and nightly | Tier 1: 0 fail on **Band A**, and no drift in the **canonical** lock digests (revision 3; FIDELITY.md §5). Any Band A or Band C change needs a deviation row in the same PR, and a sign-off in validated modules. **Plus the coverage ratchet (H0-13), counting distinct realistic inputs.** |
| **G2** accuracy | `python -m parity accuracy --gate --budget parity/bench/budget.toml` | **Results only** (revision 3): traffic lights exact; tables and summaries compared as multisets, F1 = 1.0 or explained; wording and column order are not levels. 0 unexplained, **including in a fresh worktree** (H0-11) |
| **G3** tests | `pytest` (core, foundation, modsys, owned dirs), `ruff check`, `mypy --strict` on `core` and `doc` | green |
| **G4** performance | `python -m parity bench --check` | counters within caps (hard, including a materialised-paper row and a mixed chain); ≤ +15% per module against base (hard); absolute budgets soft in PRs, hard nightly once W1 has re-measured them |
| **G5** equivalence | `pytest tests/migration/test_<wp>.py` | equality through `parity/canon.py` with **SNAP's raw golden snapshots** on the 24 accuracy inputs, the 3-paper list, the distinct-papers-same-id fixture, the duplicate-row fixture and every parity input of the package's areas, except listed U/D entries |
| **G6** layering | `pytest tests/foundation/test_layering.py` | §2.1, derived from imports |
| **G7** store | CI: `pytacheck-modules` → `store build . --check`, `pack check packs/*`, the packs' pytest | green, against pytacheck built from source (a `.devN` version) |
| **G8** batch-equality | `python -m parity batch-equality --level shared,each[,batch]` | `shared` from phase 0 (0 differences in 399 comparisons, **S**); `each` from CORE 1d; `batch` (BATCH_DESIGN H2 a, c, d) from BATCH-b |
| **G9** status (new) | `pytest tests/status/`; `pytacheck status --check` | every built-in has a registry entry; a validated module's certified code hash matches, or its badge reads "changed since validation"; policies filter as ECOSYSTEM.md §3.5 says; the web and API defaults are `validated` |

**Behaviour changes in thin-coverage modules ship separately** (Review B-5). causal_claims (0 tier-1 cases), ref_pubpeer (2), prereg_check (1), reg_check (1), reproducibility_check (0) and psychds_check (0 tier-1; 35 of 46 tier-2 cases warn-only) get a pure rewrite PR (G5 equal) first, then a separate behaviour PR carrying its deviation row and HARNESS-NET's replayed rows.

**Validated modules get a stricter rule** (revision 3). power, stat_p_exact, stat_p_nonsig, marginal and stat_effect_size, plus stat_check if the team certifies it, change Band A outputs only with a validator's sign-off or a re-validation run (ECOSYSTEM.md §2.5). Their wording rows are mandatory. power is not in the offline accuracy matrix, so until H0-3's recorded row lands its prompt, schema and model id are locked.

### 4.2 Harness changes

**SNAP** (1 day; first, right after lane 2 merges; before CORE-0):

| ID | Change |
|---|---|
| H0-12 | Record at base the canonical outputs of every module on G5's inputs, and of the 4 differential suites (3,866 + 471 + 233 + 1,916 cases), as golden JSON under `tests/snapshots/`. Add two fixtures: **distinct papers forced to one id** (debruine-fret + debruine-child as `X`; expected = the per-paper snapshots concatenated under F6's ids) and **one body row duplicated exactly** (expected = the unduplicated snapshot). Snapshots change only through reviewed U/D lock diffs. |

**HARNESS** (5 days; phase 0):

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
| H0-13 | **Coverage ratchet:** per area, the case count and the number of distinct realistic inputs reached through the public API may not drop unless a reviewed `parity/retired.yaml` maps each retired case to a replacement on a public surface; every PR reports retired and re-pointed counts (1,487 cases target private helpers today, **M**) |
| H0-14 | Layering lint derived from imports (§2.1); no per-package list |
| H0-15 | **Parallel-safe bookkeeping:** divergences load from `parity/divergences/**/*.yaml`, with new entries in `parity/divergences/<package>.yaml`; U/D entries use slugs (`U-refs-pubpeer-na`) in `docs/upstream_issues/<package>.md` fragments, numbered at CLOSE (the table ends at U158; the paused lanes reserved numbers up to U194) |

**HARNESS-v2** (7 days; phase A, after HARNESS; revision 3; FIDELITY.md §11). It replaces H0-15 and amends H0-2 and H0-13, which HARNESS builds directly in their amended form, so nothing is built twice. H0-1 is kept; a tier-2 change is fixed by adding a row:

| ID | Change |
|---|---|
| H0-17 | `parity/canon.py` and `parity/canon.toml` (row keys per module; key collisions compared as full-row multisets), with the known-difference tests in `tests/parity/test_canon.py` |
| H0-18 | The mark migration: the 1,298 case marks become at most 131 rows in `parity/deviations/<module>.toml`, starting with the validated modules; `scripts/migrate_marks.py` and its reviewed output |
| H0-19 | The determinism test: two `PYTHONHASHSEED` values give identical raw outputs |
| H0-2 (amended) | Report tables are pinned by column names and the multiset of numbers, not a cell hash |
| H0-13 (amended) | The ratchet counts distinct realistic inputs reached through public entry points. The 1,497 private-helper cases are re-pointed (FIDELITY.md §6) |
| H0-15 (replaced) | The deviation rows replace the U/D slugs; CLOSE no longer numbers U entries |

The canonical re-lock must show **0 Band A drift** over the full 9,474 cases. Until HARNESS-v2 lands, CORE-0's byte-identical bar holds.

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
| **CORE-1b** | **5** (was 3) | `core/doc.py`, `patterns.py`, `hits.py` with groups, `paragraphs()`, `sections()` (from SPIKE-2); `_derived` slot, trusted scopes, opaque token; `.tolist()` backing; V7 dedup; thread-safe stages; `papers/ids.py` (F6); façades (`text_search` incl. multi-pattern vectors, `text_expand`, `extract_*`, `paper_id`, one-paper `paper_table`) on the Doc; `Doc.from_records` + lazy `eq` | text 65, text_extract(+review), core 22, bibr12, grobid12, io (each + review), then **full `--strict` parity**; diff suites 0 differences against SNAP; I1 0 mismatches; accuracy identical except the F6 and lane-5 U-entries; counters incl. materialised and mixed-chain rows; `read(xml)` target from SPIKE-2; threaded-stage test; G6 | `_text_frame`, the per-pattern recursion, `_search_table`, the slow `paper_table` path, `_fast_concat`, `_ETHICS_ANY`, `_may_mention_ethics`, `_LIVE_ANY`, `_REPO_ANY`, `_FIRST_STAGE_ANY` and their proof tests; `_paste_groups`, `_semi_join`, `_section_headers` **only if SPIKE-2 passed V5** |
| **CORE-1c** | **1.5** (was 2) | `core/facets.py`, `refs.py` (per-Doc joins), `fuzzy.py`; `ref_table` façade; DOI dictionaries; **`json_expand` → `json.loads` + a flatten** (revision 3: nulls stay None, Band B; ≈ 120 lines instead of ≈ 150 plus the kept 335) | mod_ref_* areas, `ref_table` 14, stats(+review), text_extract(+review); `ref_table` snapshot (merge order, many-to-many); json_expand's 9 + 3 U/D cases | `ref_table`'s merges; `extract.py`'s private pipelines; the jsonlite model (≈ 900 lines) |
| **CORE-1d** | **3.5** (was 2) | `core/run.py`: `RunContext` absorbs `RunSession`; `Settings`; `override`; `RunCache`; plain-paper memo; counters; public `run_session` memo-only; L6-1; `execute()` for all runners; `_resolve`/`_call`/`_assemble`; `module_run_each`; file-module cache; forward-compatible decorator; **revision 3: the enforced `requires=` graph, typed artifacts, and the six nested re-runs replaced (§2.7); `validate.py`'s replays**; `core/warm.py` | 1,048 `module:` cases, modsys 241, report 136, api 10; G8 `shared` and `each`; `Settings` round trip under spawn and forkserver; executor order test; **a gc test that a `RunContext` is collected after its block while its papers live**; the failing-predecessor memo test | duplicated failure outputs (`report/report.py:417-440`, `api/app.py:405-412`), `report.DEFAULT_MODULES`, `RunSession` internals, the per-call entry-point scan |
| **CORE-1e** | **3** (was 1.5) | `core/output.py`; `_assemble` accepts `Result`; direct calls return `dict`; dict summary merge with the dplyr suffix rule; `pytacheck.doc` + `testing.document()`; **profile and cut the boundary floor**; **revision 3: `Result` primary, typed report blocks, `core/templates.py` with `plural`, `format_num` moved to the renderer, `ModuleOutput` built by compat (§2.9)** | twin module (dict vs `Result`) canonical-equal on 24 inputs; merge differential incl. a 3-deep chain; every built-in's direct return is a mutable `dict`; boundary floor measured per output and reported; G6 | the `DataFrame.merge` fallback |

Files held by CORE: `core/**`, `pytacheck/doc/**`, `_r/**` (after lane 2), `papers/**`, `text/search.py`, `text/expand.py`, `text/extract.py`, `text/json_expand.py`, `module.py`, `provenance.py`, `presets.py`, `validate.py`, `utils.py`, `config.py`, `src/pytacheck/__init__.py`, `pyproject.toml` (after phase 0); the `report()` loop and the `/paper/check` loop until 1d closes; the `io/grobid.py` hook until 1b closes; `packs/registry.py`'s snapshot hook in 1d; the CORE-0 quick-win files (`db/retractionwatch.py`, `db/databases.py`, `modules/ref_retraction.py`, `modules/_codebook.py`, `tests/modsys/test_session.py`; P2-codebook-lazy-compile is `perf/patches/P3-codebook-dict-index.patch` + `measure-codebook-fold-once.patch`) in CORE-0 only. **Checkpoint:** measured numbers go to the user before the waves start.

**Revision 3 packages** (new):

**HARNESS-v2** (7 d; phase A; after HARNESS). Files: `parity/**` (after HARNESS closes), `tests/parity/**`, `scripts/migrate_marks.py`, `docs/PARITY.md`. Content: H0-17 to H0-19 and the amendments in §4.2. Gates: the canonical re-lock shows 0 Band A drift over 9,474 cases; every retired mark maps to a row or to a Band B difference; H0-13's input count does not drop; two hash seeds give identical raw outputs. Depends on: SNAP, HARNESS (it owns `parity/**` after HARNESS closes). Off the critical path: CORE-1b stays byte-identical to SNAP, and CORE-1c's first Band B change needs only the canonical re-lock (≈ day 7). FIDELITY.md §11 has the step breakdown.

**TIERS** (2.5 d; phase A). Files: `presets.py` (until CORE-1d takes it), `resources/status/validation.json` (a snapshot of the team's registry), `report/` (the badge and the validation box only), `cli.py` (`status`, `--status`, the `init` question), `tests/status/**`. Content: ECOSYSTEM.md §2-3: labels, policies, `status=` on every surface, the removal of `metacheck::validated` and `DEFAULT_MODULES`, report badges. Gates: G9; `report(paper)` unchanged under the library default `experimental`; the API refuses a looser status than its ceiling. Depends on: none.

**STORE-2** (3 d; phase A; after TIERS). Files: `packs/**` (before MODSYS), the store repository (with the maintainer's permission). Content: ECOSYSTEM.md §4.3-§4.8 and §4.10, the §6 store fixes, and the §4.11 deletions if decision 13 agrees. Gates: G7; the catalog builds; the re-pin bot opens a PR against a test pin; the security baseline rejects the reserved names. Depends on: TIERS.

**COMPAT** (2.5 d; phase C; after CORE-1e). Files: `compat/**` (new), `src/pytacheck/__init__.py` (from the core agent), `docs/MIGRATING.md`. Content: `pc.check()` and `Report`; the 303 R-shaped names in `pytacheck.compat`; deprecated top-level re-exports until 1.0; `pc.hosts` with the `Host` protocol (decision 15). Gates: every name in today's `_EXPORTS` resolves from compat; the parity harness runs on compat with no case changes; an old-style script runs with deprecation warnings only. Depends on: CORE-1e.

**PORT** (5 d; phase D; can start at about day 6.5). Files: `porting/**`, `src/pytacheck/port/**` (new), `docs/PORTING.md`. Content: ECOSYSTEM.md §5: `pytacheck port init/translate/diff/finish`, the licence check, the consent record, the differential check against R behind `--allow-r`. Gates: the pilot port of one external R module passes `port diff` at the §5.2 thresholds; a port without a consent record cannot be listed. Depends on: HARNESS-v2's canonicaliser (H0-17, ≈ day 6), STORE-2.

**TOML** (2 d; phase D; optional, decision 2). Files: `core/declarative.py` (new, through the core agent), `checks/*.toml`. Content: the TOML check form (§2.10), compiled to `pytacheck.doc`; marginal and all_urls converted as the proof. Gates: the converted checks are G5-equal; `pack check` validates a TOML pack. Depends on: CORE-1e, PATTERNS.

**Changes to revision 2's packages:**

| Package | Days | Change |
|---|---|---|
| CORE-1c | −0.5 | `json_expand` shrinks (§3.5) |
| CORE-1d | +1.5 | the `requires=` graph and typed artifacts (§2.7) |
| CORE-1e | +1.5 | `Result` primary, blocks and templates (§2.9) |
| STATS | +1 | the statcheck R runtime and nmath → Optional values and `scipy.special`; the p-value facet |
| LINKS | +2 | the shared `archives/fetch.py` download engine |
| LLM-a | +1.5 | the ellmer port → a thin client (decision 16) |
| SERVICES-a | +1 | one Grobid converter; statout floats until the boundary; jasp typing |
| SERVICES-b | +1 | the report deparser removed; `api/jsonlite.py` → plain JSON; `zenodo_upload` split out |
| CODE-a | +1 | `_rparse` messages and AST trims |
| DATA-a | +0.5 | calamine for xlsx and ODS; the ODS walker goes |
| REFS | +0.5 | ref_summary and the per-module joins as dict rows |
| BATCH-a | +0.5 | the second HTTP stack folded into `http.request` |
| REPO, DATA-b, CODE-b | +1 each | the repository sub-checks and the monolith split (§3.5) |
| CLOSE | +0.5 | the §3.6 renames, with compat re-exports |
| HARNESS | −0.5 | H0-15 is not built, and H0-2 and H0-13 are built once, in their revision 3 form |
| All | ≈ −4 | no marks or U write-ups per PR (≈ 10% of the 39.5 days that carried them). Not taken out of any package's days; it is the schedule's slack |

**MODSYS** (1.5 d; after CORE-1e). Files: `packs/**`, `resources/templates/**`, `docs/MODULES.md`. Content: `requires.pytacheck` on release tuples, a warning at load, an error in `pack check` and `store build --check` (decision 7); the static `requires` check for packs importing `pytacheck.doc`; `module_api` read and ignored; the author guide; `run_session`'s contract text. Gates: modsys 241; G7 on a `.devN` build; an old-style pack loads; a pack with an unsatisfied `requires` warns at load and fails `pack check`.

**PATTERNS / W1** (5 d; after CORE-1e). Files: `modules/{ethics_check,open_practices,all_urls,coi_check,coi_check_oi,funding_check,funding_check_oi,_funding,power,_power,marginal}.py`, `tests/{mod_ethics,mod_urls_open,mod_coi,mod_funding,mod_power,mod_marginal}/**`. Order, one PR each: ethics_check (spiked); open_practices + all_urls; coi_check + coi_check_oi (section scope, from SPIKE-2); funding_check + funding_check_oi + `_funding` (`_Article` → Doc; synonyms and 34 `get_*` locators take `(doc, pattern)`; R's row order kept); power + `_power` (paragraph groups); marginal. Gates: G1 on each area + review and text_extract(+review); G2 incl. the power row; G4; G5; G6; **re-measure the §5.1 budgets and make them hard**. Deletes: `_search_frame`, `_chain`, `_full_join`, `_arrange`, `_summarise`, `_paper_ids`, `_to_title_case`, NA shims, `_Article`.

**STATS / W2** (**5 d**, was 4; after CORE-1e). Files: `modules/{all_p_values,stat_p_exact,stat_p_nonsig,stat_check,stat_effect_size}.py`, `stats/**`, `text/extract_tests.py`, `tests/{mod_p_values,mod_effect_size,stats}/**`. Content: the p-value trio on `p_values()` (star test from the span; no `grepl`); stat_check on `statcheck()` with keyword `stats()`, three-valued logic kept; stat_effect_size on `equations()` with floats + `r15`. Gates: G1 on mod_p_values, mod_effect_size, stats (+ review); effect-size table identical on 12,780 + 2,790 cells (`c_quirk` only for `strtod_long_double` and `as_character_near_ties`, tier 2); G2, G4, G5. Deletes: long-double `R_strtod`/`formatReal` (≈ 225 lines), `_match_statcheck_args`, `_icu_rank`, the three p-value pipelines.

**REFS / W3** (**5 d**, was 4.5; after CORE-1e; behaviour PRs after HARNESS-NET). Files: `modules/{ref_accuracy,ref_consistency,ref_miscitation,ref_pubpeer,ref_replication,ref_retraction,ref_summary,causal_claims}.py`, `text/causal.py`, `db/**` except `regcheck*.py`, `tests/{mod_ref_accuracy,mod_ref_db,mod_ref_pubpeer_summary,mod_causal,db}/**`. Order: **ref_consistency first, with per-Doc joins, before any other ref_* module** (Review B-1); the other ref_* modules on `RefIndex` records; ref_summary's 260-line join engine → dict rows with `requires=` for its 4 predecessors; db L7-1 to L7-3 and one JSON path; then the behaviour PRs: ref_pubpeer NA per failed chunk (L5-9), causal_claims dedup, 2 in flight, per-sentence isolation, each paper's own title (L5-8). Gates: G1 on mod_ref_accuracy, mod_ref_db, mod_ref_pubpeer_summary, mod_causal, db (+ review, httpmock replays); G2 incl. miscite/RW rows and HARNESS-NET's rows; G5 incl. the distinct-papers fixture; causal request bodies byte-identical; G4. Deletes: the 7 join helpers, `_key_kind`/`_align_key`, `_refs_with_doi` copies, pivot emulations, dplyr error replays.

**LINKS / W4** (**7.5 d**, was 5.5; after CORE-1e; behaviour PRs after HARNESS-NET; step 5 after REPO). Files: `archives/**` except `archives/listing.py` while REPO holds it, `modules/{prereg_check,_prereg,reg_check}.py`, `db/regcheck.py`, `db/regcheck_local.py`, `tests/{archives_d1,archives_d2,archives_gz,archives_osf,repo_download,mod_prereg,mod_reg}/**`. Order: `archives/links.py` behind `links()` (facet hook via the core steward), `*_links` as façades; prereg_check (pure rewrite), then the `_prereg` flatten and `osf_type` via the run cache; reg_check (pure rewrite), then RegCheck submit-then-poll (L7-7); lane 7's archive units; step 5: host clients return `FileEntry` behind REPO's `archives/listing.py`. Gates: G1 on the four archives areas, repo_download, mod_prereg, mod_reg (+ review); 502/502 bodies; 68/70 zips; the paper-side repo_check row; link detection ≤ 25 ms/paper (205 today, **M**); G4, G5. Deletes: the 14 detectors' `text_search` pipelines, `dataverse._search_frame`, `dataone._scan_links`, `_tre_wide`, the yajl/JSON shims, zip internals, the `_prereg` value model.

**REPO** (**5 d**, was 4; after CORE-1d). Files: `repository/**` (new), `archives/listing.py` (new, handed to LINKS at close), `modules/{repo_check,_repo_check,_schemas}.py`, `fileinfo/**`, `tests/mod_repo_check/**`. Content: freeze the extras schemas first (`structure`, `previews`, `codebook_vars`, `gated_repos`, `naming_issues`, `repo_metadata`, `version_pin`) with snapshot tests; `RepoIndex` with delegating parse methods and a `FileEntry` adapter over today's listing frames; repo_check as a records-first renderer with host threads, per-repository isolation and `paper_id` per row (L7-6, U). Gates: G1 on mod_repo_check(+review), with the error-text cases passing by presence; G2 on the repository block; file opens ≤ 23 on the traced chain; G5.

**DATA-a** (**3.5 d**, was 3; after HARNESS) and **DATA-b** (**6 d**, was 5; after REPO and DATA-a). Files: `datacheck/**`, `modules/{data_check,_data_check,codebook_check,_codebook,psychds_check}.py` (DATA-b; `_codebook.py` after CORE-0), `tests/{datacheck_*,mod_data_check,mod_codebook,mod_psychds}/**`. Content: §3.3 lane 3; psychds_check as a pure rewrite PR first. Gates: lane 3's gates; G1 on the datacheck_* areas, mod_data_check, mod_codebook, mod_psychds (+ review); G2 incl. extras and the psychds plan row; codebook scan of the 94K paper ≤ 0.5 s; G5. Deletes: `_files_fread.py`, `_files_readtable.py`, the readxl XML reader, `_LineReader`, `_checks_rvec.py`, `_stats_frame`/`_stack_stats`, the fread fuzz tests.

**CODE-a** (**4 d**, was 3; after HARNESS) and **CODE-b** (**5 d**, was 4; after REPO and CODE-a). Files: `codecheck/**`, `repro/**`, `modules/{code_check,_code_check,reproducibility_check,_reproducibility}.py` (CODE-b), `scripts/gen_rparse_tables.py`, `tests/{codecheck,mod_code,mod_repro,repro_core}/**`. Content: §3.3 lane 4; the scoped LLM override; reproducibility_check as a pure rewrite PR first. Gates: lane 4's gates; G1 on codecheck, mod_code, mod_repro, repro_core (+ review); G2 incl. the plan-only row; G5. Deletes: `_encoding.py`, `_icu.py`, `_icu_tables.py`, `_purl.py`, `_reval.py`, `_rjson.py`'s parser, `RNamedList`, the ICU-pinning tests.

**LLM-a** (**3.5 d**, was 2; after HARNESS) and **LLM-b** (1 d; after CORE-1d and LLM-a). Files: `llm/**`, `tests/llm/**`. LLM-a: lane 6's de-emulation units. LLM-b: `Settings` wiring, scoped overrides, N5, L6-2 to L6-6, typed structured results with source references for grounding. Gates: lane 6's gates; G1 on llm(+review) with 0 changes in mod_power, mod_codebook, mod_data_check, mod_reg and mod_causal. Deletes: `_Lexer`, `parse_json`, `_render`, `Vec`/`RInt`, the `.rds` cache, `EllmerOutput`, the S7 printer, `_friendly`, the `_cli_*` helpers.

**SERVICES-a** (**3 d**, was 2; after HARNESS) and **SERVICES-b** (**2.5 d**, was 1.5; after CORE-1d and SERVICES-a). Files: `io/**` (the grobid hook after CORE-1b), `statout/**`, `report/**` except `blocks.to_canonical` (SERVICES-b), `api/**` (SERVICES-b), `tests/{io,bibr12,grobid12,statout_*,report,api}/**`. SERVICES-a: statout, timestamps, corpus RDS, `bibr_convert`, R-internal warnings. SERVICES-b: `_Deparser` → R-literal writer; `read_plan`/`read_one`, `read(workers=)`; L7-7 to L7-9. Gates: lane 7's SERVICES gates; G1 on io, bibr12, grobid12, statout_*, report (+ review); the api tests; `read(xml)` ≤ SPIKE-2's target. Deletes: the SPV R evaluator, the civil-date and `R_strtod` ports, `_RdsReader`, `_Deparser` and its Unicode tables.

**BATCH-a** (**2 d**, was 1.5; after HARNESS) and **BATCH-b** (6 d; after CORE-1d and SERVICES-b). Files: `http.py`, `log.py`, `cli.py`, `batch/**` (new), `parity/batch.py` (from HARNESS), `docs/BATCH.md`, `tests/batch/**`. BATCH-a: BL-1 (N1 limiter, rolling window), BL-4 (per-process logs). BATCH-b: BL-2, BL-5 to BL-7, H2 (a, c, d), BL-9; BL-8 only if decision 3's gate calls for it. Gates: G8 `batch`; a 1,000-paper synthetic run under `jobs` × 300 MB RSS; the fault test; CLI tests; httpmock replays unchanged; `io/bench_window.py` ≥ 2x. Deletes: per-call `Throttle`; `_host_reset`.

**CLOSE** (**2.5 d**, was 2; after all; core agent). Shrink `docs/UPSTREAM_ISSUES.md` to the Band A metacheck bugs, each linked from its rows (FIDELITY.md §3.2); the §3.6 renames (`modules/` → `checks/`, `archives/` → `hosts/`, `packs/` + presets + provenance → `market/`), with the old paths re-exported from compat for one minor release; ratchet G6 (no module imports `_r.frames`); remove helpers without importers; docs (`docs/ARCHITECTURE.md`, `MODULES.md`, `PORTING.md`, the upstream-sync prompt); CHANGELOG; re-measure on an idle machine and replace the (E) figures. Gates: one green nightly on main (G1-G8, idle G4).

### 4.4 Schedule

Revision 3 runs in four phases. They overlap: a phase is defined by what it delivers, not by its dates.

| Phase | Days | Packages | Delivers | Stop point |
|---|---|---|---|---|
| **A, contract and ecosystem** | 0-11.5 | SNAP, HARNESS, HARNESS-v2, HARNESS-NET, SPIKE-2, TIERS, STORE-2 | the three fidelity bands, enforced by the harness; status labels, policies and presets; the store as a marketplace. TIERS and STORE-2 can ship in a release on their own, before any module is rewritten | after SPIKE-2 (day 2); at day 11.5, when the user reviews the deviation rows that the mark migration produced |
| **B, the core** | 1-17.5 | CORE-1a to 1e, and the first halves that need only HARNESS (LLM-a, SERVICES-a, DATA-a, CODE-a, BATCH-a) | `Doc`, the pattern engine, facets, the enforced `requires=` graph, `Result` and templates | the CORE checkpoint (day 17.5): measured numbers go to the user before the waves |
| **C, the waves** | 14.5-28 | REPO, LLM-b, SERVICES-b, BATCH-b, MODSYS, COMPAT, PATTERNS, STATS, REFS, LINKS, DATA-b, CODE-b, CLOSE | every module on the core; `pc.check` and `pytacheck.compat`; ≈ 88,000 lines | CLOSE (day 28) |
| **D, porting and declarative checks** | 6.5-24.5 | PORT; TOML (optional) | `pytacheck port` and one pilot port; TOML checks | independent of C: stopping PORT or TOML does not hold up the rewrite |

```
day             0   2   4   6   8   10  12  14  16  18  20  22  24  26  28
SNAP            ██
HARNESS         █████████
SPIKE-2         ████
TIERS / STORE-2 ███████████
CORE-1a           ███
HARNESS-v2               ██████████████
HARNESS-NET              ████
CORE-1b                  ██████████
CORE-1c/1d/1e                      ████████████████
LLM-a / LLM-b            ███████             ██
SERVICES-a/-b            ██████              █████
DATA-a, CODE-a           ████████
BATCH-a / -b             ████                     ████████████
PORT                         ██████████
REPO                                         ██████████
MODSYS                                             ███
COMPAT                                             █████
PATTERNS                                           ██████████
STATS                                              ██████████
REFS                                               ██████████
LINKS                                              ███████████████
TOML (optional)                                              ████
DATA-b                                                 ████████████
CODE-b                                                 ██████████
CLOSE                                                              █████
```

- **Critical path:** HARNESS (4.5) → CORE-1b (5) → 1c (1.5) → 1d (3.5) → REPO (5) → DATA-b (6) → CLOSE (2.5) ≈ **28 agent-days** (≈ 32 with slack). Revision 2: 25. The next path, through CORE-1e (3) and LINKS (7.5), is 27.5 days.
- **HARNESS-v2 is off the critical path.** CORE-1b is checked against SNAP's raw snapshots under today's byte-identical bar. CORE-1c's first Band B change (`json_expand`'s nulls) needs only the canonical re-lock, done by about day 7. The waves need the deviation rows, done by day 11.5.
- **Total:**

  | | Days (E) |
  |---|---:|
  | Revision 2's packages | 78.5 |
  | New packages: HARNESS-v2 7, TIERS 2.5, STORE-2 3, COMPAT 2.5, PORT 5 | + 20 |
  | Changes to revision 2's packages (§4.3), including HARNESS −0.5 and CLOSE +0.5 | + 14.5 |
  | No marks or U write-ups per PR (kept in the schedule as slack) | − 4 |
  | **Total** | **≈ 109**, or ≈ 111 with TOML |
  | Already done (CORE-0) | 1 |

- **Peak parallelism:** ≈ 9 agents on days 4.5-5.5 and 8 on days 17.5-19.5, not counting the core agent as steward. Revision 2: 7. Holding it to 7 costs nothing on the critical path: HARNESS-NET starts a day later, BATCH-a at day 7.5, PORT after day 11.5, and COMPAT after MODSYS.
- **Slack in the middle:** on days 11.5-14.5 only the core agent works (CORE-1d). §3.7's web front end fits there if decision 20 approves it.
- **Stop points:** after SPIKE-2 (day 2); at the end of phase A (day 11.5); at the CORE checkpoint (day 17.5).

### 4.5 Coexistence, deletion and ownership rules

**Coexistence.** One shipped implementation per module and no runtime switch. Mixed chains just work: `_assemble` normalises a dict, a DataFrame or a `Result`, and unmigrated modules call the façades on the same Doc and RefIndex.

**Deletion.** A module's private R machinery goes in that module's migration PR (the snapshots are the oracle). Façade internals go in CORE 1b after the differential suites pass, and the grouped-mode helpers only after SPIKE-2's V5. Emulation stacks go in the PR that switches the adapter, after that lane's corpus gate passes. Public façades are never deleted. Private-helper parity cases (1,487) are re-pointed or retired under H0-13's ratchet, with `porting/symbols.json` and `porting/map/*.toml` updated in the same PR.

**Shared files** (Review B-14):

| File or area | Rule |
|---|---|
| `core/**`, `pytacheck/doc/**`, `text/extract.py`, `text/json_expand.py`, `src/pytacheck/__init__.py`, `pyproject.toml` | The core agent owns them through the waves. Wave packages file change requests (a paragraph-chain stage for PATTERNS, the `links()` hook for LINKS, equations/statcheck for STATS, the roster for DATA-b), and the core agent lands them as small PRs within a day |
| `_r/regex.py` | Lane 2 until merged, then the core agent |
| `repository/index.py` | REPO only; DATA-b and CODE-b change the `datacheck`/`codecheck` functions it delegates to |
| `archives/listing.py` | REPO while active, then LINKS (LINKS step 5 waits for REPO) |
| `tests/foundation/test_layering.py` | HARNESS writes it once; the rule is derived from imports |
| Deviation rows | One file per module, `parity/deviations/<module>.toml` (revision 3; replaces H0-15's per-package fragments and slugs). A package edits only its own modules' files |
| `parity/**` | HARNESS, then HARNESS-v2 from day 4.5, then the core agent. Wave packages send requests, except for their own deviation rows and lock areas |
| `presets.py` | TIERS until it closes (day 2.5), then the core agent from CORE-1d. TIERS lands the status axis before CORE-1d moves presets into `market/` |
| `compat/**`, `src/pytacheck/__init__.py` | COMPAT from CORE-1e's close; the core agent before and after. The wave packages never edit `__init__.py`; COMPAT re-exports what they add |
| `resources/status/validation.json` | A snapshot of the team's registry, refreshed only by a PR that names the registry commit (ECOSYSTEM.md §2.3) |
| `parity/lock/<area>.json` | Re-locked only by the package that owns the area; others send requests |
| `porting/symbols.json` | One sorted entry per line, so parallel edits merge line by line |

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
| Codebook scan, 94K characters | 16.0 s | 0.2-0.3 s **(M)** | same | same |
| Memory | Doc – | Doc 0.37 MB mean, 1.02 MB max **(S)** | a finished run's caches are collectable (the gc test) | + ≈ 0.4 MB per paper in flight |

Network- and LLM-bound workloads keep BATCH_DESIGN §4.1's gains (rolling window 1.45-2.7x, **M**).

### 5.3 Size

Re-based for revision 3 on the sweeps of §3.5 **(M3)**. Revision 2's column is kept for comparison.

| Part | Today **(M3)** | Revision 2 **(E)** | Revision 3 **(E)** | Basis |
|---|---:|---:|---:|---|
| Paper modules, `text/`, `stats/`, `papers/`, `_r/`, runners | 21,964 | ≈ 16,800 | ≈ 13,850 | §3.5; the spike's −17% on module bodies **(S)** |
| Repository cluster (`codecheck`, `datacheck`, repository modules, `repro`, `fileinfo`) | 46,275 | ≈ 33,900 | ≈ 31,100 | §3.5; lanes 3/4 size gates |
| Services (`archives`, `statout`, `llm`, `io`, `db`, `report`, `api`, `http`, `cli`, `config`) | 51,335 | ≈ 43,700 | ≈ 38,300 | §3.5; lanes 6/7 size gates |
| The rest of `src/` (mostly `packs/`) | ≈ 6,200 | ≈ 6,200 | ≈ 5,000 | ECOSYSTEM.md §4.11 (−1,000 to −1,300) |
| New core | 0 | + ≈ 3,300 (≈ 700 moved) | + ≈ 2,600 net | 1,975 for the spiked subset **(S)**, plus groups, run, facets, output, templates, values |
| `compat/` | 0 | – | + ≈ 1,800 | the 303 names, the `ModuleOutput` adapter (§2.10) |
| Structural items outside §3.5's sweeps | – | – | ≈ −4,000 | templates, the repository sub-checks, the `_oi` and p-value merges: the gap between §3.6's two estimates, and the least certain row |
| **`src/pytacheck`** | **125,800** (125,200 at revision 2's count, **M**) | **≈ 98,000 (−22%)** | **≈ 88,000 (−30%; range 82-92k)** | §3.6 |

Most of the net reduction (≈ 37,000 lines) is still de-emulation (≈ 36,000 in §3.5's sweeps). The new core and compat add ≈ 4,400 back, and the structural items and `packs/` take ≈ 5,000 more out.

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
- *Why (b):* the user asked for relaxed fidelity where it makes the code more Pythonic. The 1,298 case marks become at most 131 rows (FIDELITY.md §3.2), and ≈ 11,150 more lines of emulation can go (§3.5). The validated modules keep Band A exact, with a validator's sign-off for any change (§4.1). *Cost:* funding_check_oi's row order changes on 11 of 21 papers **(M)**; the release notes list every module whose default order changed (FIDELITY.md §10, risk 7).

**2. No-code modules in the store.** Revised in revision 3.
- (a) Ship `pytacheck.module/1` YAML now and convert marginal as the proof.
- (b) Build the loader, keep every built-in in Python.
- (c) Defer until a store author needs it; marginal stays Python. Revision 2's recommendation.
- **(d) TOML checks in phase D, once `pytacheck.doc` is stable: patterns, section filters and a traffic-light rule, compiled to `pytacheck.doc`; marginal and all_urls as the proof; exactly two forms (Python and TOML).** Recommended (changed from (c)).
- *Why (d):* revision 2's objection was that a converted built-in breaks the one-module-per-R-module map. Under revision 3 that map is `porting/modules.toml` (F2), so the objection is gone. The store and the R translator (ECOSYSTEM.md §5) need a no-code form for simple pattern checks, and `tomllib` is in the standard library. 2 days, optional, off the critical path (§4.3 TOML).

**3. Batch corpus mode (BATCH BL-8).**
- **(a) Defer: re-measure the loop/list ratio after W1-W3 and build only if still ≥ 1.5x (3.45x today, M).** Recommended.
- (b) Build it with the batch engine, taking on per-module neighbour-invariance risk.

**4. LLM grounding flags.**
- **(a) Observation-only checks that LLM quotes occur in their source; a note, a warning and a count, and no value changed.** Recommended.
- (b) Not now.
- *Why (a):* it makes hallucination visible, and it runs only when the LLM is on.

**5. Pacing.** Revised in revision 3.
- **(a) Phases A-D (§4.4) with three stop points: after SPIKE-2 (day 2), at the end of phase A (day 11.5), and at the CORE checkpoint (day 17.5).** Recommended.
- (b) Phase A only, then re-plan: the fidelity contract, validation status, presets and the store ship, and the rewrite waits.
- (c) Phases A and B plus the paper-module waves (PATTERNS, STATS, REFS, MODSYS, COMPAT); the repository cluster then gets only the lane 3/4 leaf de-emulations behind adapters.
- (d) The whole plan without stop points.
- *Context:* ≈ 111 agent-days, critical path ≈ 28 days (§4.4). The payoff is ≈ 4.5x on module CPU, −30% code (≈ 88,000 lines), and the ecosystem of ECOSYSTEM.md. CORE-0 alone already gave 2.1x on the accuracy matrix (34.7 → 16.9 s, **C**); the rest of the plan adds ≈ 1.5x there (to 10-12 s, §5.4).
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
| 8 | The launch validated set | the 5 team-validated modules (power, stat_p_exact, stat_p_nonsig, marginal, stat_effect_size), plus stat_check if the team certifies its external evidence; the team decides | ECOSYSTEM.md §1.1, §3.1 |
| 9 | Remove `metacheck::validated` | yes. The web app uses validated ∩ server-safe (16 → 5-7 modules); data_check becomes experimental | ECOSYSTEM.md §3.4 |
| 10 | Default policies | library and CLI `experimental`; web app and API `validated` | ECOSYSTEM.md §3.4 |
| 11 | Field scope | a certification counts only for overlapping fields | ECOSYSTEM.md §2.6 |
| 12 | Lookup modules | experimental until certified through `metrics` | ECOSYSTEM.md §2.4 |
| 13 | Private stores | make pytacheck public, fix store CI, and replace private stores with "clone plus path pin" (−1,000 to −1,300 lines) | ECOSYSTEM.md §4.11, §6 |
| 14 | Where the registry lives | a team-owned repository, with CODEOWNERS as the validator group | ECOSYSTEM.md §2.3 |
| 17 | Consent for ports | required for store listing, beyond what the licence requires | ECOSYSTEM.md §5.5 |
| 18 | Inbound terms | DCO, no CLA | ECOSYSTEM.md §5.4 |
| 19 | Legal review | counsel reviews ECOSYSTEM.md §5.4 before third-party ports are listed | ECOSYSTEM.md §5.4 |

**15. The main API** (new).
- **(a) `pc.check(paper, preset=…, status=…)` returns a `Report`. The 303 R-shaped names move to `pytacheck.compat` and stay importable from the top level with a deprecation warning until 1.0.** Recommended.
- (b) Keep the R names as the main API and add `pc.check` beside them.
- (c) Remove the R names from the top level at once.
- *Why (a):* Python users get a top level of six names (§2.10). R users, existing scripts and the parity harness keep working through compat. (c) breaks every script today; (b) leaves 303 names as the first thing a new user sees. *Cost:* COMPAT, 2.5 days, and ≈ 1,800 lines.

**16. Wholesale ports of R libraries** (new).
- **(a) Replace them with thin Python equivalents: ellmer → an httpx client for OpenAI-compatible, Anthropic and Gemini APIs; ICU ucsdet + vroom + iconv → `_decode.py`; data.table fread and read.table → pandas plus a type layer; knitr purl → a static chunk parser; readxl and the ODS readers → calamine. Move `zenodo_upload` out of the package.** Recommended.
- (b) Keep the ports (revision 2).
- *Why (a):* ≈ 11,350 lines become ≈ 1,950 (§3.5), and `zenodo_upload`'s 1,228 leave the package. The differences are edge cases (non-UTF-8 code without a BOM, computed chunk options, unusual CSVs); each gets one scoped deviation row, and none touches a validated module's Band A outputs. power's request bodies stay byte-identical. *Cost:* fewer built-in LLM providers.

**20. The web app** (new).
*Pending the web spike (§3.7).*

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
