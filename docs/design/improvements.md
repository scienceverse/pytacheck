# Improvements over metacheck

pytacheck's outputs follow metacheck's under the accuracy contract
([PORTING.md](../PORTING.md#1-the-accuracy-contract)). This page lists
improvements around the checks themselves: where metacheck leaves a large,
cheap win on the table, we take it, as long as a run that metacheck would
complete still reports the same results (traffic lights, tables, summary and
report text). Changes that do alter results are metacheck bug fixes (U-entries)
or deliberate differences (D-entries) in
[UPSTREAM_ISSUES.md](../UPSTREAM_ISSUES.md). The candidates came from a scout
pass over both code bases (2026-09-24).

| # | Improvement | Same results | Status |
|---|---|---|---|
| 1 | **Run-scoped memoisation.** Implicit dependency re-runs (`data_check` → `repo_check`, `codebook_check` → `data_check`, `reproducibility_check`'s `run_missing()`) happen once per report or API request, instead of re-listing and re-downloading repositories and hitting API quotas. | yes | in module system v2 (`run_session()`) |
| 2 | **REST API off the event loop.** Blocking work runs in a thread pool behind a concurrency cap (`PYTACHECK_API_MAX_CHECKS`), so a slow `/check` no longer stalls `/health` or other requests. | yes | in module system v2 |
| 3 | **Run record.** Each run records modules, packs, commits, file hashes and effective arguments. It is embedded in reports and can be replayed with `rerun`. | yes (outside the compared outputs) | in module system v2 |
| 4 | **CLI per-module arguments.** `-a power.seed=1` mirrors R's `report(args = list(power = list(seed = 1)))`, instead of passing every argument to every module. | yes | in module system v2 |
| 5 | **Batch bibr extraction in `read()`.** All PDFs and other source documents go through one `chew()` call, so the models load once. | yes | after the io lane lands |
| 6 | **HTTP response store.** In-run deduplication, a persistent cache for repository hosts (on by default under ARCHITECTURE.md decision 22 (decided 2026-09-27: (a2)): validated, immutable or short-TTL entries), and record/replay bundles for offline, reproducible network-backed outputs. | yes, except entries up to their max-age or TTL old (REPO_FETCH.md §2.4, §5.1) | designed for repository hosts in [REPO_FETCH.md](REPO_FETCH.md) §5; record/replay still planned |
| 7 | **LLM layer.** Bounded concurrency, a JSON cache with a replay-only mode, and a per-run token and call ledger. | yes | after the llm lane lands |
| 8 | **Corpus `report()`.** Parallel, resumable, with a lossless typed-JSON results store. | yes | after the report lane lands |
| 9 | **No pickle in on-disk caches.** A crafted `.pkl` in a cloned project could execute code. | yes | after the archives lane lands |
| 10 | **Memoised `paper_table` on `PaperList`.** Text searches over a corpus stop rebuilding the concatenated text table every time (about 40% of `text_search` time on 500 papers). | yes | planned |
| 11 | **Opt-in per-paper failure isolation** for corpus `module_run`. | no (adds an element) | deferred |
