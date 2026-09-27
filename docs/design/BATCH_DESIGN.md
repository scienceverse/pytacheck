# Batch processing in pytacheck: final design

This design merges three candidate designs (throughput-first, batch-native architecture, network/LLM/I-O-first) and three independent reviews of them. It also uses one new measurement taken for this document (`final/`, load about 3 on the shared 4-core VM, the quietest run so far). Nothing in `/home/user/pytacheck` was edited.

Markers used for numbers:
- **(M)** measured in pytacheck on this VM, minimum of at least 2 runs;
- **(R)** measured in metacheck 0.3.1 (R 4.5.3);
- **(E)** an estimate derived from measurements, with the derivation given.

The VM was shared with other agents throughout, so absolute times are only indicative. Ratios measured within one run are reliable.

**Amended on 2026-09-27 by [REPO_FETCH.md](REPO_FETCH.md)** for repository fetches, a cache store across runs, and OSF's limits. The notes marked "REPO_FETCH" below (R5, R6, N1, §3.5, §4.1, P3-2, §7) say what changes; everything else stands.

---

## 0. Summary

Yes, batches can be optimised much further, and metacheck never did this. It offers two ways to process many papers, and neither is designed for scale:
- **list mode** (`module_run(paperlist)`) is fast, but one bad paper fails the module for every paper, and it gives one verdict for the whole list;
- **the `report()` loop** isolates each paper, but pays every fixed cost again for every paper, runs one paper at a time, saves nothing until the end, and cannot resume.

Its network layer is sequential by design: batches of 5, then a 0.5 s sleep. It never deduplicates a DOI across papers. pytacheck has inherited both modes.

The final design has three layers. They are ordered by payoff per line of new machinery:

| Layer | What it does | Owners | Gain |
|---|---|---|---|
| **1. Fixed costs** | Build each per-paper table once. Normalise each bundled database once per process. Remove the fixed costs of 5 specific modules. | lane5, lane7 | Helps every path: single paper, list, loop and batch. About 20-30% off a per-paper chain **(E)**; the profile below shows why. |
| **2. Network, LLM and I/O** | One limiter per host. `batch_query` becomes a rolling window. Keys are deduplicated. A run-scoped memo. PubPeer in isolated chunks. repo_check hosts queried concurrently. Jobs submitted first, then polled. LLM workers, single-flight and atomic cache writes. | lane5, lane6 and lane7 as riders; the `http.py` core goes to the batch lane | Real batches are network- and LLM-bound. 1.45-2.7x per `batch_query` **(M)**; network stages go from hours to tens of minutes **(E)** |
| **3. One batch engine** | `module_run_each` gives exactly the per-paper outputs. `run_batch` / `pytacheck batch` / `report(list, jobs=N)` run chunks in worker processes. Each paper's results stream to disk, errors are isolated per paper, and a run can resume. An optional corpus mode uses list mode on a chunk, but only for modules that pass an equality harness. | small hooks in lane5 and lane6; the rest is a new batch lane | About 3x from 4 processes on an idle machine **(E)**. Corpus mode halves CPU **(M)**. A crash no longer loses finished work. |

**Fidelity is never traded:**
- `module_run`, `run_modules` and `report` on one paper behave as today.
- `module_run(PaperList)` stays R's list mode.
- Every paper's batch output equals the per-paper loop's output.
- The CSVs are byte-identical for any number of workers and any chunk size.
- A harness check (`python -m parity batch-equality`) enforces all of this.

**Headline numbers:**

| Workload | Today | With this design |
|---|---|---|
| 1,000 offline XML papers, 19 modules, one result per paper | about 12.7 min: `report(list)` on the lane5 prototype, 0.76 s/paper **(E from M)** | paper mode, `-j 4`, idle machine: about 3-4 min **(E)** |
| same, corpus tables only | about 5.5 min in `pytacheck run` list mode, with no isolation and memory that grows with N **(E from M)** | corpus mode, `-j 4`: about 2-2.5 min **(E)**, isolated, resumable, bounded memory |
| Crossref lookups for 28k DOIs | about 2.1 h at 3.7 req/s (lockstep) **(E from M)** | about 69 min at 6.8 req/s, and less with deduplication **(E from M)** |
| about 2,500 LLM calls | 1.7-8 h, sequential | about 25-60 min at 8 in flight, bounded by the provider tier **(E)** |
| accuracy matrix (Python side) | 22-25 s serial on the lane5 prototype **(M)** | about 5-8 s with `--jobs 4` and shared read-only papers **(E; busiest worker 6.9 s CPU (M))** |

---

## 1. What metacheck does with batches, and why it is slow at scale

The evidence is in `metacheck_batch.md`. Paths are relative to `upstream/metacheck`.

### 1.1 Two modes, one trade-off

| | List mode: `module_run(paperlist, m)` | Loop: `report(paperlist)` |
|---|---|---|
| How | the whole list goes to the module once (R/module.R:34-35, 84-88) | `report()` per paper in `tryCatch` (R/report.R:61-94) |
| Speed | 0.48 s/paper for 11 offline modules **(R)** | 1.46 s/paper **(R)**; loop ÷ list is 1.0-31x per module **(R)** |
| One error | fails the module for the whole batch (module.R:90-98) | fails one paper, or one module of it |
| Verdicts | one traffic light and summary text for the whole list | one per paper |
| Saved while running | nothing | nothing; the results stay in memory until the end |

### 1.2 Why it is slow at scale

1. **Fixed cost per call, paid once per paper in the loop:**
   - the module file is re-sourced and re-parsed on every call, about 30 ms;
   - bundled databases are re-read, 0.10 s per RetractionWatch load;
   - text search rebuilds the stacked text table on every call (text_search.R:72-87).
2. **Sequential network I/O.**
   - `.batch_query` sends batches of 5 in sequence, with a 0.5 s sleep between batches (utils.R:100-226).
   - causal_claims makes 2 requests per sentence, in sequence.
   - reg_check submits a job and then polls it every 5 s, one preregistration at a time.
   - Repositories are processed one at a time (repo-download.R:1347).
3. **No deduplication or cache across papers.**
   - CrossRef repeats shared DOIs (NEWS.md:119).
   - PubPeer gets one POST with every DOI, duplicates included.
   - The only exceptions are the OSF listing memo and the LLM disk cache.
4. **One process.** Only three things run in parallel:
   - file downloads within one repository;
   - OSF listing pages;
   - the Docker reproducibility workers.
5. **LLM:** calls are sequential. In list mode, the cap of 30 calls per `llm()` call stops any real batch (llm.R:88-92).

### 1.3 Bugs that only show up in batches

| R bug | Status in this design |
|---|---|
| `read(dir)` fails completely on one bad file | already fixed in pytacheck (`io/read.py:105-113`) |
| `report()` prints `x$id`, which is always `NULL` | already correct in pytacheck |
| repo_check takes the OSF paper id from the first paper; one OSF error fails every OSF repository | L7-6: per-repository isolation and the right id on each row (U-entry) |
| causal_claims reads `paper$info$title`, which is `NULL` on a list | L5-8: each paper's own title (U-entry) |
| `ref_pubpeer` crashes when the PubPeer lookup returns `NULL` | L5-9 / L7-3 |
| the 30-call LLM cap stops list-mode modules | the engine runs LLM modules per paper, so the cap applies per paper, as in R's `report()` loop |
| `convert_grobid`'s directory branch drops `start/end/consolidate` | L7-7 |
| the log is rewritten on every call and keeps only 999 entries | BL-4: one log file per worker process |

---

## 2. The pytacheck baseline this builds on

- **Two batch modes, inherited from R:**
  - list mode is used by `module_run(PaperList)`, `run_modules` and `pytacheck run`;
  - the per-paper loop is used by `report(PaperList)` (`report/report.py:773-801`) and the API (one file per request).
  - `report(list)` called from Python does not open a `run_session`. The CLI wraps it in one (`cli.py:393`).
- **Network:**
  - `batch_query` creates a new `Throttle` on every call (`http.py:292`), runs its batches in lockstep, and sleeps after every batch, including the last (`http.py:315-324`);
  - `doi_resolves` starts 500 threads that compete for 32 connections (`db/doi.py:185-187`, `http.py:88`);
  - PubPeer is one unchunked POST (`db/pubpeer.py:67-75`), and `total_comments` is filled with 0 (`:126`);
  - `causal_relations` sends one request and waits for its result, per sentence, strictly in sequence (`text/causal.py:350-355`).
- **Global state:**
  - options are one global dict, and `local_options` is not scoped to a thread (`utils.py:56-106`);
  - the reproducibility spawn worker receives only the Docker limits (`modules/_reproducibility.py:745-752`), which is a latent bug for LLM-enabled Docker batches.
- **Two measured correctness constraints:**
  - Only **14 of 19** offline modules give each paper the same result whatever its neighbours in the list (`throughput/chunk_inv.log`). ref_accuracy, stat_check, stat_effect_size, ref_replication and ref_retraction depend on their neighbours. For ref_accuracy the cause is the corpus-level `len(bib_match) == 0` branch (`modules/ref_accuracy.py:498-501`), which R has too.
  - Duplicate `paper_id`s crash ref_consistency (`throughput/dup_ids.log`).

### 2.1 New measurement: cost per module, loop vs list (lane5 prototype tree, load 2.8)

`final/bench_mixed.py` and `final/mixed.log`. CPU ms per paper; the loop runs 21 papers inside `run_session`, the list is N=105.

| module | loop | list | loop ÷ list |
|---|---:|---:|---:|
| open_practices | 45.1 | 9.2 | 4.9 |
| ethics_check | 42.2 | 18.4 | 2.3 |
| ref_consistency | 48.4 | 18.4 | 2.6 |
| stat_p_exact | 36.0 | 6.8 | 5.3 |
| marginal | 16.1 | 2.9 | 5.5 |
| ref_miscitation | 27.4 | 4.5 | 6.1 |
| *9 other invariant modules* | 229.4 | 58.2 | 3.9 |
| **ref_retraction** (neighbour-dependent) | 64.2 | 7.8 | 8.3 |
| **stat_effect_size** (neighbour-dependent) | 55.7 | 21.7 | 2.6 |
| **stat_check** (neighbour-dependent) | 27.8 | 10.6 | 2.6 |
| **ref_replication** (neighbour-dependent) | 26.2 | 7.0 | 3.8 |
| **ref_accuracy** (neighbour-dependent) | 17.7 | 16.8 | 1.1 |
| **total** | **606.4** | **175.6** | **3.45** |
| **mixed**: list for the 14 invariant modules, per paper for the 5 others | | **303.3** | **2.0 against the loop** |

What this shows:
- List mode alone gives 3.45x. A corpus mode that stays correct gets 2.0x, because the 5 modules that must run per paper cost 191 ms/paper.
- Most of that is fixed cost. ref_retraction spends 64 ms per call, of which the list mode needs only 7.8.

`final/prof_chain.txt` profiles one per-paper 19-module chain under cProfile:
- `paper_table` takes **27%** of the chain, at 37 calls per paper. It rebuilds each table from JSON records on every call (`papers/tables.py:49-80`).
- `ref_table` is 21% of that, at 4.3 calls per paper, about 50 ms each; every call rebuilds the full sentence table.
- `retractionwatch()` strips 61k DOIs on every call, 33 of its 44 ms (`db/retractionwatch.py:24-40`; `final/prof_ref_retraction.txt`).

This is why **layer 1 comes first**: removing these costs speeds up the single-paper path, the loop, the list and the batch alike, with no new machinery.

---

## 3. The design

### 3.1 Rules

- **R1. The existing entry points do not change behaviour.**
  - `module_run`, `run_modules` and `report()` on one paper behave as today.
  - `module_run(PaperList)`, `run_modules(PaperList)` and `pytacheck run` keep R's list mode: an aggregate verdict, one error fails the module for the list, and the 30-call LLM cap applies per call. They only get faster, through layers 1 and 2.
- **R2. Batch output for a paper equals the per-paper output.**
  - Paper mode reproduces `run_modules(paper, selection)` for each paper, including traffic light, summary text and report.
  - Corpus mode reproduces the table rows and summary rows.
  - A per-paper verdict is never derived from an aggregate list output.
- **R3. Deterministic.** Results are in input order. `summary.csv` and `tables/*.csv` are byte-identical for any `jobs` and `chunk_size`.
- **R4. Request shapes.** Requests may change only where the output is provably identical when every request succeeds (deduplicating keys, chunking PubPeer). Fixtures whose request counts change are re-recorded in the same commit. Behaviour that differs only on failure is an isolation improvement recorded as a U-entry.
- **R5. Caching.** Errors, 429s and 5xx are never cached. Staleness is explicit: memos last one run; nothing persistent is added in v1.
  - *REPO_FETCH, if decision 22 (a) is taken:* v1 adds a `CacheStore` for repository metadata that is validated on every run, immutable, or held for a short TTL (1-30 d), and for file blobs by hash (REPO_FETCH.md §5). Errors are still never stored, and lookups still last one run.
- **R6. Politeness.** Across all worker processes together, requests never exceed R's bound or a service's published limit.
  - *REPO_FETCH, if decision 23 (a) is taken:* OSF's published limits are hourly and daily (100 per hour anonymous, 10,000 per day with a token), and its file lists are limited to 75 per minute, so they span runs as well as processes. A request ledger keeps every window of a minute or longer (REPO_FETCH.md §2.2, §5.5). R's bound is read as a per-host envelope of 10 in flight (R's `max_active` for pages 2..n). The pinned R's `.batch_query` is sequential (1 in flight, `R/utils.R:185-188`), while N1 below allows `batch_size`.
- **R7. One run object.** The existing `run_session()` owns every run-scoped cache. There is no separate network session.

### 3.2 Shape

```
run_batch(inputs, selection, ...)  /  pytacheck batch  /  report(PaperList, jobs=N)
│
├─ parent
│    plan:     list inputs (io.read_plan), sha256 each, resume filter,
│              contiguous chunks in input order
│    pool:     ProcessPoolExecutor(forkserver on Linux | spawn), max_tasks_per_child,
│              initializer applies the Settings snapshot, rate share 1/jobs,
│              the in_worker flag, 1 BLAS thread, SIGINT ignored
│    collect:  ChunkResult (~1 kB) per chunk → append + fsync manifest.jsonl
│    progress: one rich bar fed by per-paper events (multiprocessing queue)
│    merge:    parts → summary.csv, tables/<module>.csv, verdicts.csv, in input order
│
└─ worker, for each chunk, inside a fresh run_session():
     papers = [io.read_one(path) for path in chunk]      # a read failure → status read_error
     for module in selection (in order):
        if mode == "corpus" and spec.batch == "list" and the chunk has unique ids:
            list call on the chunk → split by paper_id → _assemble per paper
            (if the list call raises: re-run this module per paper for this chunk)
        else:
            module_run_each(chunk papers)                 # network/LLM modules: io thread pool
     write papers/<i>-<id>/outputs.json (+ report) and parts/<chunk>/*.csv; drop outputs

Shared within a process for the whole run: the host limiters, the run_cache memos (LRU-bounded),
bundled databases, compiled regexes. Nothing is shared across processes except files.
```

### 3.3 Components

#### Layer 1: fixed costs (owner riders; no new machinery)

| ID | Component | Evidence |
|---|---|---|
| F1 | **Per-paper table cache.** `paper_table(p, table)` for a single paper, and `ref_table(p)`, cached in the per-paper derived cache that lane5 is building (`Paper._derived`), keyed on the paper's generation. It returns copy-on-write views. Lists keep assembling from the list (F2). | `paper_table` is 27% of a per-paper chain; `ref_table` is about 50 ms per call and 4.3 calls per paper (`final/prof_chain.txt`) |
| F2 | **PaperList list view.** The text frame and `_Table` for a PaperList are cached on the list, keyed on `(id(p), p._generation)` of every paper, and built directly from the list. Also an O(1) `paper_id` index on PaperList (`__getitem__`, `__contains__`, `names`). | Building from the list takes 60 ms, against 426 ms when assembled from per-paper tables at N=105 **(M, arch/)**; `reg_check.py:311` is O(n²) |
| F3 | **Bundled databases normalised once per process.** Cache the cleaned `retractionwatch()` and `FLoRA()` frames per loaded file (path + mtime), and `ref_retraction._rw_entries` per process. | 33 of 44 ms per call is `.str.strip` over 61k DOIs **(M)** |
| F4 | **Fixed costs of specific modules:** ref_consistency (48 ms/call **(M)**), stat_effect_size (3 `text_search` calls per paper, 56 ms/call **(M)**), ref_replication, ref_retraction. | `final/mixed.log`, `scaling/fixed_costs.txt` |

All of these are output-neutral. The acceptance test for each: parity and the accuracy gate are unchanged, and `final/bench_mixed.py` shows the loop cost falling.

#### Layer 2: network, LLM and I/O

| ID | Component | Owner |
|---|---|---|
| N1 | **One limiter per host in `http.py`.** A process-wide registry holds a token bucket, an in-flight cap and the reset memory for each host (this replaces `_host_reset`). Policies are data: `HostPolicy(rate, burst, concurrency)`, looked up by host or path prefix, with `set_host_policy()`. `set_rate_share(f)` scales every budget by f in worker processes. `batch_query` keeps its signature and results in input order, and becomes a **rolling window with R's bound** (at most `batch_size` in flight, at most `batch_size/delay` per second) with no sleep after the last request. Identical specs within one call are sent once. `doi_resolves` gets at most 16 in flight. No AIMD. *REPO_FETCH:* one (rate, burst, concurrency) cannot express OSF's limits; policies gain path scopes, several windows per scope, an identity dimension and sliding-window logs, and the shared client moves to HTTP/1.1, because httpcore's HTTP/2 connection races under threads (REPO_FETCH.md §2.1-2.2). | batch lane (`http.py` belongs to no lane) |
| N2 | **Run-scoped memo in `RunSession`.** `run_cache(name)` returns `None` outside a session. Its `.get(key, compute)` and `.get_many(keys, fetch)` deduplicate keys in first-seen order and call `fetch` once for the misses. Single-flight: concurrent callers of one key wait on one Future. Errors are never stored. LRU-bounded (default 256 MB per process). An HTTP GET/HEAD memo sits on top of it inside a session only: status 2xx, 404 or 410, keyed on method, URL without `mailto`, Accept, and a hash of the credential identity. | module.py hook: lane5 (small); HTTP wiring: batch lane |
| N3 | **Lookups:** keys deduplicated in `crossref_doi`, `crossref_query`, `openalex_doi`, `datacite_doi`, `doi_lookup`, `doi_resolves` and `pubpeer_comments`. `add_bib_match` groups rows once with a `groupby`, and `grobid_to_bibr` calls it once per list. PubPeer POSTs carry at most 500 unique DOIs, each chunk is isolated, and unchecked DOIs get NA, not 0. `osf_type` goes through `run_cache`. | lane7, and lane5 for `ref_pubpeer` |
| N4 | **Services:** repo_check queries its host blocks in a thread pool, isolates errors per repository and puts the right `paper_id` on each row. Grobid gets one health check, argument pass-through and `workers=`. bibr and RegCheck submit every job, then poll. causal_relations deduplicates sentences, runs 2 in flight and isolates each sentence. `read(workers=)` uses a process pool, never threads. | lane7; lane5 for causal |
| N5 | **LLM:** single-flight on the cache key across concurrent `llm()` calls, atomic cache writes, a circuit breaker after 3 consecutive systemic errors, `cache_control` only on the system block for single-turn calls, and the `Chat.build`/`accept` split (preparation for phase 3). Workers stay at 1 by default; `run_batch` raises them inside its context. | lane6 |

The LLM call cap needs no change: the engine runs LLM modules per paper, so the cap applies per paper exactly as in R's `report()` loop. `llm(max_calls=)` is not needed.

#### Layer 3: the batch engine

| ID | Component | Owner |
|---|---|---|
| E1 | **`_assemble` + `_call` extracted from `module_run`** (`module.py:958-1014`: summary merge, `na_replace`, the report default, building the `ModuleOutput`, the memo put). **`module_run_each(papers, module, **kw) -> list[ModuleOutput \| ModuleError]`** is defined as `[module_run(p, module, **kw) for p in papers]`, with the same memo keys, and returns errors per paper. v1 is literally that loop with shared caches. Phase 3 may add a faster path behind the same contract. | lane5 |
| E2 | **`ModuleSpec.batch: Literal["list", "paper"] = "paper"`**, set with `@module(batch=...)` and the declarative pack field. A module may be declared `"list"` only after `batch-equality` (H2) passes for it. | lane5 adds the field; the batch lane flips built-ins after H2 |
| E3 | **Settings snapshot.** `Settings.capture()` / `.apply()` covers: `utils.options_snapshot()` (lane6); `config` (email, verbose); `module.use_snapshot()` (lane5); the packs overlay; `os.environ`; the cwd. Sent with every chunk, so a long-lived pool follows the caller's current settings. Secrets are masked in `run.json`. A test asserts that every setter exported from `pytacheck/__init__.py` is covered. | batch lane, with hooks in lane5 and lane6 |
| E4 | **`run_batch` runner**: plan, chunk, worker, corpus views, sink, manifest, merge, progress and cancel (§3.4-3.9). | batch lane |
| E5 | **Pool**: stdlib `ProcessPoolExecutor`. No custom supervised pool. | batch lane |
| E6 | **Entry points**: `report(PaperList, jobs=N)` delegates to paper mode when N≠1; `pytacheck batch`; `pytacheck report -j`. The API job endpoint comes later (lane7). | batch lane; lane7 |

**How corpus mode works (the only place list mode is used for per-paper results):**
- The canonical state is always per paper.
- For a module declared `batch="list"`, the worker calls `_call(spec, chunk_input)` once on the chunk. `chunk_input` is the chunk's PaperList, with earlier outputs visible through `get_prev_outputs` as each item's per-paper DataFrames concatenated in input order.
- The worker splits the raw `table`, `summary_table` and every DataFrame extra by `paper_id`, then calls `_assemble` per paper. The summary merge, its suffixes and `na_replace` are therefore exactly the per-paper code path.
- The list call's `traffic_light`, `summary_text` and `report` are discarded, and the stored per-paper records mark them "not computed in corpus mode".
- A module is not eligible if it returns an item that is not a DataFrame with `paper_id` (or `None`). H2 checks this.
- A paper whose `paper_id` already appears in the chunk runs that module per paper.

### 3.4 API and CLI

**Python:**

```python
res = pc.run_batch(
    inputs,                     # directory, list of paths, or PaperList
    selection=None,             # same argument as run_modules: preset ref, Selection, list of refs
    *,
    out_dir=None,               # None: results kept in memory (small runs; report(list, jobs=N))
    mode="paper",               # "paper": everything per paper | "corpus": tables and summary rows
    jobs=1,                     # int or "auto"
    chunk_size="auto",
    report=None,                # paper mode: None | "html" | "qmd" | "md"
    resume=False, retry_failed=False, max_age="7d",
    io_workers=4,               # threads per worker for per-paper network/LLM modules
    read_kwargs=None, progress=True, cancel=None,   # cancel: threading.Event
) -> BatchResult
```

`BatchResult` fields and methods:
- `.status`: `"done"` or `"cancelled"`;
- `.summary`: DataFrame, one row per input, in input order;
- `.papers`: per paper, the status, error and seconds, and the status of each module;
- `.failures`: rows of (item, paper_id, module, error);
- `.table(module)`;
- `.verdicts`: paper mode only;
- `.outputs(i | paper_id)`: loaded lazily from `out_dir`;
- `.run_record`.

```python
outs = pc.module_run_each(papers, "marginal")   # == [pc.module_run(p, "marginal") for p in papers]
pc.report(papers, output_format="md", jobs=4)   # same ReportList and files as jobs=1
```

**CLI:**

```
pytacheck batch PATH... [-m MOD ... | --preset REF] -o OUT_DIR
      [--mode paper|corpus] [-j N|auto] [--chunk-size N] [--report html|qmd|md]
      [--resume [--retry-failed] [--max-age 7d]] [--force] [--io-workers N] [--dry-run]
pytacheck report PATH... -j N           # with N≠1, runs on the batch engine
pytacheck read DIR -o OUT --jobs N       # lane7
python -m parity accuracy --jobs N       # harness
python -m parity batch-equality          # harness
```

- Exit status: 0 when every (paper, module) pair succeeded; 1 when any failed; 130 after a cancel; 2 for a usage error or a refused resume.
- `pytacheck run` and `pytacheck report` given 32 or more papers with `-j 1` print a one-line hint about `batch`.

### 3.5 Defaults

| Setting | Default | Why |
|---|---|---|
| `jobs` | `1` in the library and `report()`. `pytacheck batch`: `"auto"`, which is 1 when the estimated CPU is under 5 s (about 30 papers), otherwise the usable CPUs (affinity, capped by any cgroup quota), capped by the number of chunks and by free memory / 300 MB. `PYTACHECK_JOBS` overrides it. | Spawning 4 workers costs 1.5-2.0 s; forkserver 0.25-0.30 s **(M, throughput/start.log)** |
| start method | Linux: `forkserver` with `set_forkserver_preload(["pytacheck"])`. macOS/Windows: `spawn`. `fork` only in the parity harness (which already uses it). | Fork with live threads can deadlock. A test asserts that importing pytacheck starts no thread. |
| `max_tasks_per_child` | 20 chunks | Bounds memory growth; available on 3.11 (non-fork contexts) |
| `chunk_size` | paper mode: `clamp(ceil(N / (4·jobs)), 1, 50)`; corpus mode: `clamp(…, 16, 250)`. Contiguous ranges of the input order. | 4 chunks per worker balances the load. Corpus per-paper cost is flat from about N=100 **(M, scaling/)**. |
| `mode` | `"paper"` | A superset of outputs and exactly the loop. `"corpus"` is the explicit fast path for tables. |
| `io_workers` | 4 once lane6's context-local options land, 1 before that | Network modules wait; CPU modules stay serial within a worker (GIL) |
| LLM concurrency | `pytacheck.llm.workers` = 2 inside `run_batch`, 1 elsewhere (unchanged); provider in-flight cap 8 per host for the whole run, local servers 1 | Bounded by the per-host limiter |
| Rate share | each worker gets `1/jobs` of every host's rate and in-flight budget. In-flight is `max(1, floor(cap/jobs))`, with a warning when `jobs` exceeds a host's cap. | R6 with no shared-memory limiter |
| Memo | module outputs: one `run_session` per chunk. HTTP and lookup memo: per worker process for the run, LRU 256 MB. | Bounded memory; deduplicates within each process |
| Persistent HTTP cache | none (phase 3, opt-in). *REPO_FETCH:* repository metadata goes to the `CacheStore`, on by default if decision 22 (a) is taken; lookups stay uncached | R5 |
| `resume` | off. A non-empty `out_dir` without `--resume` or `--force` is refused. | No silent reuse |
| `max_age` | 7 days, for reusing results of modules that `require` network or LLM when resuming | A resumed run must not miss a new retraction or PubPeer comment |
| Progress | a rich bar when `verbose()` is on and stderr is a TTY; workers run with `verbose(False)` | One bar, no nested bars |

### 3.6 Fidelity guarantees

| # | Guarantee | Mechanism | Checked by |
|---|---|---|---|
| G1 | **Paper mode = per-paper loop.** Each paper's outputs (module order, `table`, `summary_table`, `traffic_light`, `summary_text`, `report`, extras) equal `run_modules(paper, selection)` after the harness's canonical normalisation. Report files equal `report(p)` apart from the date and the embedded run record's timestamps. | Same code: `module_run_each` is `module_run` per paper through the shared `_assemble`. Per-paper chain. The parent's settings restored in workers. | H2(a) |
| G2 | **Corpus mode = per-paper loop, for table and summary rows.** | List calls only for modules declared `"list"` that passed H2; split and `_assemble` per paper; duplicate ids never share a list call | H2(b) |
| G3 | **Deterministic order and bytes.** `summary.csv` and `tables/*.csv` are identical for jobs 1 or 4 and for chunk size 1 or all, with shuffled completion. | Merge in input order; parts are read back as text cells (`dtype=str`, no NA parsing); the column union in first-seen order over papers in input order | H2(c) |
| G4 | **R's list mode untouched.** `module_run(PaperList)` still gives stat_check `0` where the per-paper run gives `NA`, the aggregate traffic light, and the 30-call cap per call. Each pytacheck path matches its R counterpart. | No change to module code for batching | existing parity cases |
| G5 | **Network.** Within one run, identical requests get identical bytes from the memo. Results differ only if a remote changes during the run (then the memo makes them more consistent). Isolation changes are U-entries that keep R's output whenever R would have succeeded. | R4, R5 | httpmock tests; accuracy gate (no network) |
| G6 | **LLM.** Same prompts, same R-compatible cache keys and `.rds` format. Concurrency changes nothing beyond model non-determinism. The cap applies per paper, as in R's `report()` loop. | N5, per-paper LLM modules | llm tests |
| G7 | **Resume = fresh.** A run killed part-way and resumed produces the same `summary.csv`, `tables/` and `outputs.json` as an uninterrupted run. | §3.9 | H2(d) |

### 3.7 Error isolation

| Failure | What happens |
|---|---|
| unreadable input | That item gets status `read_error` with the message, and the others continue, as `read()` does today |
| one module fails on one paper | That (paper, module) becomes a `"fail"` output, exactly as `report_module_run` does, and the chain continues. The row goes to `errors.jsonl`, and the warning is re-emitted by the parent in input order, so `report(list, jobs=4)` warns like `report(list)`. |
| a list call fails in corpus mode | The module is re-run per paper for that chunk (`module_run_each`), which isolates the bad paper. No bisection is needed: re-running one module per paper on at most 250 papers is cheap. |
| a worker process dies (segfault, OOM) | `BrokenProcessPool`: the parent recreates the pool and requeues the chunks that were running. A chunk that crashes twice is resubmitted one paper per task. A paper that crashes alone gets status `crashed`. |
| a hang | Every request already has a timeout (HTTP 60 s, LLM, RegCheck and Grobid polling). v1 has no hard per-paper timeout, because `ProcessPoolExecutor` cannot kill a single task before Python 3.14. The escape is Ctrl-C twice. |
| a systemic LLM error (bad key, quota) | lane6's circuit breaker: after 3 consecutive errors the remaining texts fail at once with one message, instead of thousands of retries |
| Ctrl-C | First: stop submitting, let running chunks finish, write the manifest, status `cancelled`, exit 130. Second: terminate the workers. Papers already finished are on disk and resumable. `cancel=threading.Event` does the same from Python. |

Rejected:
- skipping the rest of a chunk after N identical errors, because it hides results that depend on each paper's content;
- one error failing the whole batch.

### 3.8 Progress and logging

- **Progress.** One rich progress bar in the parent: papers done out of total, failures, ETA. Workers send one event per finished paper (and per module in `--verbose`) through a `multiprocessing` queue created from the pool's context and passed to the initializer. Workers never draw bars.
- **Logs.** Each worker writes `logs/worker-<pid>.jsonl`: `log.py` accepts a per-process path through an option or environment variable, so processes never trim one shared file. The parent writes `errors.jsonl`, one row per failed (item, module).
- **Run record.** `run.json` holds:
  - the `RunRecord` (selection with pack revisions and module identities);
  - the settings, with secrets masked;
  - versions;
  - the plan (N, jobs, chunk size, mode);
  - network statistics (requests per host, memo hits, 429s, time spent waiting on limiters).

### 3.9 Resumability and the output folder

```
OUT_DIR/
  run.json               run key, RunRecord, settings (masked), versions, plan, network statistics
  manifest.jsonl         one row per finished paper; the parent is the only writer (append + fsync per chunk)
  summary.csv            item, input, paper_id, then the modules' summary columns; input order
  tables/<module>.csv    item, input, then the module's table columns; input order
  verdicts.csv           paper mode: item, paper_id, module, traffic_light, summary_text
  papers/<item>-<id>/    outputs.json (api/jsonlite serialiser) and report.<fmt>
  errors.jsonl
  logs/worker-<pid>.jsonl
  parts/                 worker output per chunk; removed after the final merge
```

- **`item` and `input` columns.** `item` is the 0-based input index and `input` is the path. They keep duplicate `paper_id`s distinct. The columns after them are exactly the module's.
- **Atomic writes.** Workers write `papers/…` and `parts/<chunk>/…` through a temp file and `os.replace`. The parent appends manifest rows only after the chunk's files are in place. A torn last line of the manifest is ignored.
- **The run key** is a sha256 over:
  - the RunRecord selection (refs, pack revisions, module file hashes);
  - the arguments;
  - the mode;
  - the pytacheck version.
- **`--resume` requires the same run key**; otherwise it is refused (exit 2) and points to a new `out_dir` or `--force`.
- **What a resume skips and re-runs:**
  - items whose input sha256 and all-ok status match are skipped;
  - `--retry-failed` re-runs failed items;
  - an item containing a network- or LLM-dependent module (`spec.requires`) older than `max_age` is re-run entirely, because its chain may depend on that module.
- **`--dry-run`** prints the plan, for example "9,812 done, 188 to run, 0 stale".
- **No incremental store keyed on code or environment digests** in v1. It is the most rot-prone idea in the candidate designs, and any core change invalidates everything anyway.

---

## 4. Expected gains

### 4.1 Per workload

| Workload | Before | After | Basis |
|---|---|---|---|
| **(i) Accuracy matrix**, Python side (21 inputs, 439 outputs) | base tree about 36 s; lane5 prototype serial 22-25 s **(M)** | **H1** `--jobs 4`: about 7-8 s on idle cores **(E)**. The busiest worker used 6.9-7.0 s CPU and 439/439 outputs were identical **(M)**. On the loaded VM, wall time got worse (30-35 s) **(M)**, so `--jobs` defaults to 1 in CI unless cores are free. **H1b**, one read-only paper shared per input: 9.26 → 6.20 s on the 14-module offline shape **(M)**. Combined: about 5-6 s **(E)**. | throughput/acc_pool.log; arch/matrix.log |
| **(ii) 1,000 offline XML papers, 19 modules, per-paper results** | R: 11 modules at 1.46 s/paper, about 24 min **(R, E)**. pytacheck `report(list)` on the lane5 prototype: 0.61 s chain + 0.15 s read, about **12.7 min** **(E from M)**. | Layer 1: chain −20-30% **(E**, from the 27% `paper_table` share and 3% RW share**)**, about 10 min. **Paper mode `-j 4`**: about **3-4 min** on idle cores **(E**: scaling 3.1x from the busiest worker's CPU, M; the wall gain on the loaded VM was only 1.4-1.5x, M**)**. | final/mixed.log, prof_chain.txt; throughput/chain19.log |
| **(ii') same, corpus tables only** | `pytacheck run` single-process list mode: 0.18 + 0.15 s/paper, about **5.5 min**, with no isolation and a single list in memory (about 0.6 GB) **(E from M)** | Corpus mode, mixed: 0.30 + 0.15 s/paper, about 7.5 min with `-j 1` and about **2.5 min with `-j 4`** **(E)**. After the layer 1 fixes to the 5 per-paper modules, about 2 min **(E)**. Memory at most `jobs` × about 150 MB **(E from 0.56 MB/paper, M)**. | final/mixed.log; scaling/mem_*.txt |
| **(ii'') crash at paper 900** | everything in memory is lost | resume re-runs only the unfinished chunks | design |
| **(iii) 1,000 papers with network lookups** (default preset: prereg_check, repo_check, code_check, ref_pubpeer, power, plus the offline set) | R: CPU plus the sum of every request's latency, one after another | `batch_query` 1.45-2.7x at the same politeness bound **(M**, simulated latency, 100 GETs through the real `http.request`**)**. Crossref at 3 in flight: 3.7 → 6.8 req/s **(M)**, so 28k DOIs drop from about 2.1 h to about 69 min **(E)**; deduplicating 10-30% of DOIs brings that to 48-62 min **(E**; the overlap between distinct papers is unmeasured and was 0 in the matrix**)**. PubPeer: one fragile 28k-DOI POST, or 1,000 POSTs, becomes about 56 isolated POSTs of at most 500 DOIs **(E)**. repo_check listings 3-4x, since wall time becomes the slowest host instead of the sum of 8 hosts **(E)**. *REPO_FETCH:* this does not hold. Most papers' repositories are on one host, so threading hosts overlaps at most ≈ 1.5-7% of listings (REPO_FETCH.md §1.4); the gain comes from per-host queues inside OSF (§7 there). reg_check up to 4x **(E)**. causal_claims 3-6 h → 1.5-3 h at 2 in flight, bounded by the Space **(E)**. Worker processes overlap network waits with other workers' CPU. | io/bench_window*.py; design_io.md |
| **(iv) LLM-heavy run** (about 2,000-3,000 calls) | R: sequential at 2-10 s per call, 1.7-8 h; list mode stops above 30 calls | Per-paper LLM modules (no cap problem). At 8 in flight: about **25-60 min**, bounded by the provider tier **(E)**. Duplicate texts are shared through single-flight and the existing disk cache. Phase 3 provider batch APIs: about 50% of the token cost, usually under 1 h per batch (published pricing and limits). | design_io.md N7/N8 |

### 4.2 What the numbers do not show

- Scaling across processes on an idle machine is **not measured**. On the loaded VM, chunked pools gave 1.4-1.5x over a single-process list and forked accuracy pools were slower. Every "`-j 4`" figure above is an estimate from the busiest worker's CPU time. Closing item C-3 re-measures on an idle machine before the numbers go into the docs.
- Service capacities (the causal Space, RegCheck, the public Grobid server) and DOI overlap in real corpora are unmeasured. The defaults are conservative for that reason.

---

## 5. Work items by owner

Every item keeps outputs unchanged unless it names a U-entry. "Accept" is the acceptance test. Effort: S is at most 1 day, M is 2-3 days, L is 4 or more days.

### lane5: text/**, stats/**, papers/**, module.py, validate.py, paper-text and ref_* modules

| ID | Item | Files | Accept | Effort |
|---|---|---|---|---|
| L5-1 | F1: per-paper cache for `paper_table(p, t)` and `ref_table(p)` in the per-paper derived cache, keyed on the generation, returning copy-on-write views | `papers/tables.py`, `papers/model.py` | `final/prof_chain.py`: `paper_table` cumulative ms/paper −70% or more; parity and accuracy unchanged | S-M |
| L5-2 | F2: PaperList list-view cache (text frame, `_Table`) built directly from the list, and an O(1) id index | `text/search.py`, `papers/model.py` | A second `text_search` on the same PaperList does not rebuild the table; `names` and getitem are O(1); `reg_check`'s lookup uses the index | S |
| L5-3 | F4: fixed costs of ref_consistency (48 ms/call), stat_effect_size (56 ms/call), ref_retraction's `_rw_entries` (16 ms/call), ref_replication | `modules/ref_consistency.py`, `stat_effect_size.py`, `ref_retraction.py`, `ref_replication.py`, `text/extract.py` | `final/bench_mixed.py`: loop ≤ 1.5x list for these modules at N=105; outputs unchanged | M |
| L5-4 | ref_consistency crash on duplicate `paper_id`s; one warning from `module_run(PaperList)` on duplicate ids. Check R's many-to-many `left_join` output first; a U-entry if it differs. | `modules/ref_consistency.py`, `module.py` | `throughput/dup_ids.py`: no crash, one warning | S |
| L5-5 | E1: extract `_assemble` and `_call`; add `module_run_each` | `module.py`, `__init__.py` | For every offline module on the 21 inputs, `module_run_each` is canonically equal to the list comprehension, with the same memo keys and `ModuleError`s per paper; the module tests are unchanged | S-M |
| L5-6 | E2: the `ModuleSpec.batch` field (default `"paper"`) and `@module(batch=)`; shown in `pytacheck modules MOD` | `module.py` | The field exists; the default is `"paper"`; no built-in changes (BL-8 flips them) | S |
| L5-7 | `module.use_snapshot()` / `use_restore()` for E3 | `module.py` | Round trip under spawn | S |
| L5-8 | causal_relations: deduplicate sentences, 2 in flight, per-sentence isolation (an NA row plus one warning), `run_cache("causal")`. causal_claims uses each paper's own title on lists. U-entries for both. | `text/causal.py`, `modules/causal_claims.py` | httpmock: a duplicated sentence sends one request; one failing sentence leaves the other rows intact; the list-mode title check works | S-M |
| L5-9 | `ref_pubpeer` handles `None` and NA ("not checked") from `pubpeer_comments` | `modules/ref_pubpeer.py` | A test with a failed chunk: those DOIs show as not checked, never as 0 comments | S |
| — | **Leave stat_check and stat_effect_size's 0-vs-NA alone.** Both modes are faithful to R; these modules stay `"paper"` in corpus mode. | — | — | — |

### lane6: llm/**, utils.py

| ID | Item | Files | Accept | Effort |
|---|---|---|---|---|
| L6-1 | Context-local options: `local_options` scoped to the context or thread through a ContextVar overlay; `options_snapshot()` / `options_restore()` | `utils.py` | Two threads with different `local_options` do not see each other's values; reproducibility's `skip_on_api_limit` no longer leaks; the existing tests pass | M |
| L6-2 | LLM: single-flight on the cache key across concurrent calls; atomic cache writes (temp file, then `os.replace`); a circuit breaker after 3 consecutive systemic errors; the default of 1 worker unchanged | `llm/core.py`, `llm/cache.py`, `llm/_rds.py` | Two threads with the same text send one provider request; a crash mid-write leaves no partial `.rds`; three 401s trip the breaker | M |
| L6-3 | `cache_control` only on the system block for single-turn `llm()` calls (`providers.py:1021-1024` marks the last user block today) | `llm/providers.py` | Request-body test; affects cost only | S |
| L6-4 | Split `Chat._submit` into `build()` and `accept()`, behaviour-neutral (preparation for phase 3) | `llm/providers.py` | The recorded provider tests are unchanged | S-M |
| L6-5 | Memoise `utils.online()` per host inside `run_session` (positive 10 min, negative 60 s) | `utils.py` | An offline host costs one 2 s check per run, not one per call per paper | S |
| L6-6 | Parse the reset headers LLM providers send (`retry-after` on 503/529, RFC 3339 dates, Go-style durations) into the N1 limiter's reset memory, once N1 lands (coordinated with the batch lane) | `llm/providers.py` | Unit tests on recorded headers | S |

### lane7: archives/**, db/**, io/**, statout/**, report/**, api/**, repo_check, reg_check, prereg_check

| ID | Item | Files | Accept | Effort |
|---|---|---|---|---|
| L7-1 | Deduplicate keys in `crossref_doi`, `crossref_query`, `openalex_doi`, `datacite_doi`, `doi_lookup` and `doi_resolves` (cleaned, case-folded keys mapped back by position) | `db/crossref.py`, `db/doi.py` | Duplicate DOIs send one request each; outputs identical; fixtures that count requests are re-recorded | S |
| L7-2 | `add_bib_match`: one `groupby('paper_id')` instead of a filter per paper (`crossref.py:1036-1041`); `grobid_to_bibr(crossref_lookup=True)` calls it once per list | `db/crossref.py`, `io/grobid.py` | Equal outputs; linear time | S |
| L7-3 | `pubpeer_comments`: unique DOIs, POSTs of at most 500, per-chunk isolation; DOIs from failed chunks get NA `total_comments` (not `fillna(0)`); returns `None` only when every chunk fails (U-entry). At most 500 unique DOIs gives today's body. | `db/pubpeer.py` | httpmock: 1,200 DOIs send 3 POSTs; one 500 response leaves those DOIs NA and fills the others | S |
| L7-4 | F3: cache the cleaned `retractionwatch()` and `FLoRA()` frames, keyed on the loaded file (path + mtime) | `db/retractionwatch.py`, `db/replications.py`, `db/databases.py` | Second call under 1 ms; identical frame; `rw_update()` invalidates | S |
| L7-5 | `osf_type` through `run_cache("osf-type")` inside a session | `archives/osf.py` | prereg_check then repo_check on one paper: one type request per GUID | S |
| L7-6 | repo_check: host blocks in a `ThreadPoolExecutor` (at most 8, `copy_context`) with a fixed assembly order; OSF isolated per repository; `paper_id` per row (U-entry) | `modules/repo_check.py`, `modules/_repo_check.py` | httpmock with one failing OSF repository: the others succeed; row order unchanged | M |
| L7-7 | Conversions and jobs: `convert_grobid` does one health check per call, passes the directory branch's arguments through, and takes `workers=` (at most 2 against the public server); `convert_bibr` submits up to 8 jobs, then polls with backoff; `regcheck_compare_many` submits, then polls (at most 4 outstanding); job POSTs are never retried after a read timeout | `io/grobid.py`, `io/bibr_convert.py`, `db/regcheck.py`, `modules/reg_check.py` | Mocked servers: order kept, per-file isolation, submit count equals file count | M |
| L7-8 | Split `read()` into `read_plan(paths, recursive)` and `read_one(path, **kw)`; `read(workers=)` uses a fork (Linux) or spawn process pool at 32 XML/source files or more, never threads; errors are logged in the parent | `io/read.py` | `read(dir, workers=4) == read(dir)`; a bad file is skipped and logged; threads are not used (they measured 0.63-0.76x) | M |
| L7-9 | `report(list)` wraps its loop in `run_session`; duplicate ids get separate files (suffix `~2` and a warning); `render_report(outputs, path, fmt, renderer)` can be called from workers | `report/report.py` | Two papers with the same id write two files; reports are unchanged | S |
| L7-10 | (Phase 3, after the batch lane) an API job endpoint on `run_batch` (upload several files, get a job id, poll `manifest.jsonl`) | `api/app.py` | Mock client test | M |

### lane4: codecheck/**, code and reproducibility modules

| ID | Item | Files | Accept | Effort |
|---|---|---|---|---|
| L4-1 | The reproducibility worker applies the full Settings snapshot (E3) instead of only `docker_resource_limits` (`_reproducibility.py:745-752`). Inside a batch worker (option `pytacheck.batch.in_worker`): `workers=1`, no nested pool, and the Docker CPU and memory limits divided by `jobs`. | `modules/_reproducibility.py`, `modules/reproducibility_check.py` | An LLM option set in the parent is visible in a spawned worker; no nested pool is created under `run_batch` | S |

### lane3: datacheck/**, data and codebook modules

| ID | Item | Files | Accept | Effort |
|---|---|---|---|---|
| L3-1 | Mark `data_check`'s `previews` (full data files) as transient: needed by later modules in the chain, then droppable. The runner then leaves them out of `outputs.json` (unless `keep_previews`) and frees them when the paper's chain ends. Audit the memory held per paper. | `modules/data_check.py`, `modules/codebook_check.py` | Peak RSS of a 20-paper repository batch stays bounded; per-paper outputs are otherwise equal | S |
| L3-2 | Review the places where data_check and codebook_check collapse a list to a single paper id (R's `data_check.R:363-378` fallbacks), so list mode stays R-faithful and per-paper runs are correct. No batch flag: these modules stay `"paper"`. | `modules/data_check.py`, `modules/codebook_check.py`, `datacheck/**` | Existing parity cases; a 2-paper list test | S-M |

### harness: parity/**

| ID | Item | Files | Accept | Effort |
|---|---|---|---|---|
| H1 | `python -m parity accuracy --jobs N`: one task per input (all of its modules), a fork pool on Linux and spawn on macOS (the `run_cases` pattern), longest first, results keyed by output id. **H1b:** within a task, one read-only paper shared by all modules inside `run_session`, instead of a deepcopy per (module, paper). | `parity/accuracy.py`, `parity/__main__.py` | 439/439 outputs and the report identical to serial | S |
| H2 | **`python -m parity batch-equality`**, on the realistic corpus (unique ids), the 21 accuracy inputs, and a corpus with one input duplicated under two ids. Offline modules and httpmock-backed network modules, each module alone and each shipped preset as a chain. **(a)** `run_batch(mode="paper")` with jobs 1 and 4, chunk size 1 and all, and shuffled inputs, compared with `run_modules(p)` per paper: canonically equal, traffic lights and reports included. **(b)** For modules proposed as `"list"`: the whole list, chunks of 2 and 7, each paper alone, and reversed order give per-paper table rows (as a multiset and in within-paper order) and summary rows (values and CSV dtypes) equal to (a); returned items must be DataFrames with `paper_id`. **(c)** `summary.csv` and `tables/*.csv` byte-identical across (a)'s variants. **(d)** A run killed after 2 chunks and resumed equals a fresh run. It prints the modules eligible for `"list"`. | `parity/batch.py` (new), `parity/__main__.py`, `tests/batch/**` | Runs in CI in under 2 minutes on the offline set; today it should mark 14/19 offline modules eligible | M |
| H3 | Keep the accuracy gate (zero network, identical outputs) as the final acceptance for every lane's batch items | — | Green | — |

### batch lane (new, after lanes 3-7)

**Scope:** everything that cuts across the lanes:
- the shared HTTP limiter and run memo in `http.py` (which no lane owns);
- the settings snapshot pieces outside lanes 5 and 6 (`config.py`, `packs/registry.py`);
- per-process logs (`log.py`);
- the new `pytacheck/batch/` package: planner, worker, corpus views, sink and manifest, progress, the pool on stdlib `ProcessPoolExecutor`, and `BatchResult`;
- `run_batch`, `pytacheck batch`, and delegating `report(list, jobs=N)` to the engine;
- switching built-in modules to `batch="list"` from H2's eligibility list;
- `docs/BATCH.md`.

| ID | Item | Files | Accept | Effort |
|---|---|---|---|---|
| BL-1 | N1: per-host limiter registry, a `HostPolicy` table (data), `set_host_policy`, `set_rate_share`; a rolling-window `batch_query` with R's bound and no trailing sleep; identical specs sent once per call; `doi_resolves` at most 16 in flight; `archives/download.py`'s rate memory delegates to the registry (coordinated with lane7's file) | `http.py`, `archives/download.py` | `MockTransport` plus a fake-clock test of in-flight caps, pacing, input-order results under shuffled replies, and the reset memory; httpmock replays unchanged; `io/bench_window.py` at least 2x at typical latency | M |
| BL-2 | N2: `RunSession.cache(name)`, `run_cache(name)`, `get` and `get_many` (dedup, single-flight, no stored errors, LRU); the HTTP GET/HEAD memo inside a session | `module.py` (after lane5 lands), `http.py` | Unit tests: memo hits, single-flight, errors not stored, the LRU bound; outside a session nothing changes | S-M |
| BL-3 | E3: `Settings.capture()` / `apply()` (options via L6-1, `config`, `use` via L5-7, the packs overlay, environment, cwd); secrets masked | `batch/settings.py`, `config.py`, `packs/registry.py` | Settings round-trip under forkserver and spawn; `use(offline=True)` holds in workers; a test enumerates the setters in `__init__.py` | S |
| BL-4 | Per-process log path | `log.py` | Two processes never write one file | S |
| BL-5 | E4: `run_batch` paper mode: plan, contiguous chunks, worker loop with `module_run_each` (network and LLM modules in `io_workers` threads), sink, manifest, merge, `BatchResult`, progress queue, two-stage Ctrl-C, `--dry-run`, resume with `max_age` | `batch/{__init__,plan,worker,sink,progress}.py` | H2(a), (c), (d); a 1,000-paper synthetic run stays under `jobs` × 300 MB RSS | L |
| BL-6 | E5: pool (`forkserver` with preload, or spawn; `max_tasks_per_child=20`; initializer; requeue on `BrokenProcessPool`, then one paper per task) | `batch/pool.py` | Fault test: a module that calls `os._exit` for one paper marks only that paper `crashed`; the preload starts no thread | M |
| BL-7 | E6: `pytacheck batch`, `pytacheck report -j`, `report(PaperList, jobs=N)` (the same `ReportList`, files and warnings as `jobs=1`) | `cli.py`, `report/report.py` (delegation only, after lane7), `__init__.py` | CLI tests; `report(list, jobs=4)` equals `report(list)` apart from the date and run-record timestamps | M |
| BL-8 | Corpus mode: `batch/views.py` (chunk input with concatenated earlier outputs, split by `paper_id`, `_assemble` per paper, the duplicate-id fallback, per-paper re-run when a list call fails), then set `batch="list"` on the built-ins H2 lists as eligible (lane5 reviews) | `batch/views.py`, `modules/*.py` (decorators only) | H2(b) green for every flipped module; `final/bench_mixed.py`-style chain in corpus mode at most 0.55x of paper mode's CPU | M |
| BL-9 | `docs/BATCH.md`: the two modes, defaults, fidelity guarantees, the output folder, resume, politeness | `docs/BATCH.md` | Reviewed | S |

### closing: _r/** and cross-cutting

| ID | Item | Accept |
|---|---|---|
| C-1 | U-entries and divergence-lock marks for: PubPeer per-chunk NA; causal per-sentence isolation and per-paper titles; repo_check per-repository isolation and ids; `convert_grobid`'s directory-branch arguments; duplicate-id report files. Note that the LLM cap applies per paper in batches, which equals R's `report()` loop. | Lock green |
| C-2 | (Optional) compile regexes with `concurrent=True` in `_r/regex.py`; keep it only if 4 threads on the text modules reach at least 1.5x | Measurement recorded |
| C-3 | Re-measure (i)-(iv) on an idle machine and replace the (E) numbers in `docs/BATCH.md`; the process speed-up is not proven yet | Numbers recorded |
| C-4 | CHANGELOG | — |

### Phase 3 (after the batch lane, only if measurements call for it)

| ID | Item | Gate |
|---|---|---|
| P3-1 | A faster `module_run_each` for paper mode through a per-module scan/finish split (design 2's C3), behind the same contract | Only for modules whose loop ÷ list stays at 3x or more **after** L5-1 to L5-3. Each conversion passes H2(a) on in-order, shuffled and duplicate-id corpora. |
| P3-2 | An opt-in persistent SQLite HTTP cache with TTLs per service and `replay`/`refresh` modes; the run record shows the age of the oldest entry used. *REPO_FETCH:* superseded for repository hosts by the `CacheStore` (files, not SQLite, since SQLite's WAL fails on network filesystems; REPO_FETCH.md §5.3), which keeps the run-record rule. Offline replay of lookups stays here | Users ask for offline replay, or re-runs dominate |
| P3-3 | Crossref `filter=doi:` and OpenAlex `doi:` multi-key endpoints | A recorded equality test against the `api.labs.crossref.org` single records |
| P3-4 | Provider batch APIs (Anthropic Message Batches, OpenAI Batch) as a **blocking façade**: one provider batch per `llm()` call's misses, the same cache keys and `.rds` files, opt-in, with a note on provider-side retention. A coalescer across threads only if per-call batches prove too small. | L6-4 has landed; there is demand for cost reduction |
| P3-5 | The API job endpoint (L7-10); AIMD; a free-threaded 3.14t probe | Measured need |

---

## 6. Sequencing

1. **Now, independent of the lanes:** H1 and H1b (harness). They are small, measured, and give identical outputs.
2. **During lanes 3-7, as riders in files each lane is already rewriting:**
   - **lane5:** L5-1, L5-2, L5-3 first (layer 1; they also help lane5's own performance goal), then L5-4 to L5-9. L5-5 to L5-7 are small hooks that the batch lane needs.
   - **lane6:** L6-1 first (it unblocks threads and the snapshot), then L6-2 to L6-5.
   - **lane7:** L7-4 and L7-1 to L7-3 first (small and output-neutral), then L7-5 to L7-9.
   - **lane4:** L4-1, once L6-1 exists (until then it can pass options explicitly).
   - **lane3:** L3-1 and L3-2.
3. **Batch lane, after lanes 3-7:**
   1. BL-1 and BL-2: the network core.
   2. BL-3 and BL-4: settings and logs.
   3. BL-5, BL-6 and BL-7: the paper-mode engine, the pool and the entry points.
   4. H2 (harness) together with BL-5.
   5. BL-8: corpus mode and the `batch="list"` flips.
   6. BL-9: docs.
   - *Option for the orchestrator:* BL-1 touches only `http.py`, which no lane owns, and keeps every signature. It could start during lanes 3-7 if lane7 agrees, because lane7's db tests exercise `batch_query` timing.
4. **Closing:** C-1 to C-4.
5. **Phase 3:** P3-1 to P3-5, each behind its gate.

The critical path to a usable `pytacheck batch` is: L6-1 → L5-5/L5-7 → BL-3 → BL-5/6/7 → H2.

---

## 7. Non-goals and rejected ideas

| Rejected | Why (evidence) |
|---|---|
| Changing `module_run(PaperList)` so modules no longer depend on their neighbours (design 1, T2) | This is R's own list-mode behaviour: ref_accuracy's corpus-level `len(bib_match)==0` branch, and stat_check's 0 in list mode against NA per paper. Those modules simply stay `"paper"` in batches. |
| Deriving per-paper verdicts from aggregate list outputs | R2. The traffic light and report are aggregates in list mode. |
| "Collect rounds" for LLM batch APIs (designs 1 and 2) | Deferred error rows make power's all-failed branch fire (`_power.py:368-389`), which sends the fallback prompt for every paragraph, doubling the batch. Modules with broad `except` clauses would also take degraded paths. |
| A custom supervised worker pool, a shared-memory (`RawArray`/crc32) limiter, SQLite single-flight across processes | Stdlib `ProcessPoolExecutor` plus a per-worker rate share covers the need. Those pieces are maintenance-heavy and unproven. *REPO_FETCH:* the rate share cannot keep an hourly window across runs, so REPO_FETCH.md §5.5 adds a request ledger in files or Redis. It counts requests; it is not a limiter in shared memory. |
| An incremental result store keyed on code and environment digests, with dependency recording | Any core change invalidates everything anyway. A missing key component gives silently stale results. v1 resumes by run key plus input hash. |
| Resume on by default; a persistent HTTP cache on by default | A re-run months later would silently miss new retractions or PubPeer comments. *REPO_FETCH:* this still holds for lookups. The repository `CacheStore` is on by default only because each entry is re-validated on every run, cannot change, or expires within its TTL (REPO_FETCH.md §5.1). |
| A separate `net_session` beside `run_session` | One run object (R7). |
| One pool task per paper for offline modules | Measured 2.2-2.5x over the loop, against 17x for one list and more for chunks. |
| Threads for CPU work (`read()`, text modules) | `read()` with threads measured 0.63-0.76x (the GIL). |
| Returning `ModuleOutput`s through the pipe in `out_dir` mode | About 109 kB each pickled; workers write to disk and return about 1 kB. |
| Reading every input in the parent | 150 ms per XML file, about 2.5 min per 1,000 before any work starts. |
| Raising `llm_max_calls` globally | Not needed: LLM modules run per paper in the engine. |
| AIMD as a default; asyncio or an `httpx.AsyncClient` rewrite; exceeding published limits | Threads with per-host caps already reach the rate caps (measured). |
| Skipping the rest of a chunk after N identical errors | It hides results that depend on each paper's content. |
| New external services (Semantic Scholar, Unpaywall) | They change outputs and send references to new parties. |
| Multi-machine execution (Ray, Dask, Slurm), GPU, Parquet or pyarrow outputs | Out of scope. The manifest and output folder make manual sharding easy later. |
| `jobs` on `module_run` or `run_modules` | These stay R's single-process paths. |

---

## 8. Decisions for the orchestrator

1. **Default `mode` for `pytacheck batch`.** I recommend `"paper"`: it is a superset of the outputs and exactly the loop. `"corpus"` halves CPU (303 vs 606 ms/paper **(M)**) but has no per-paper verdicts.
2. **Pulling BL-1 (`http.py`) forward** into lanes 3-7. It is safe signature-wise, but lane7 must agree (§6).
3. **Duplicate `paper_id`s** in `report(list)`: `~2` suffixes plus a warning (L7-9). R overwrites.
4. **CI:** keep `accuracy --jobs 1` in CI unless the runner has idle cores. On the loaded VM a 4-worker fork pool was slower (30-35 s against 23-25 s) **(M)**.

---

## Appendix: evidence

All paths are under `scratchpad/perf/batch/`.

**New for this document:**
- `final/bench_mixed.py` and `final/mixed.log`, with `mixed.json`: per-module loop vs list vs mixed, on the lane5 prototype tree (`perf/wt-measure`), load 2.8.
- `final/prof_chain.py` and `final/prof_chain.txt`: a per-paper 19-module chain under cProfile (shares of `paper_table`, `ref_table`, `retractionwatch`).
- `final/prof_fixed.py`, `final/prof_ref_retraction.txt` and `final/prof_stat_effect_size.txt`: where those modules' fixed costs go.

**Earlier:**
- `metacheck_batch.md` and `rtest/`: R behaviour and timings.
- `pytacheck_batch.md`: the map of current batch paths.
- `scaling/`: loop vs batch at N = 21/105/210/840, fixed costs, `read()` with threads and processes, memory, pools.
- `throughput/`: `chunk_inv.log` (14/19 invariant), `dup_ids.log`, `chain19.log`, `acc_pool.log` (439/439 identical), `start.log` (start methods).
- `arch/`: `ab_summary.txt` (loop ÷ list 8.6x on the base tree, 4.0x on the lane5 prototype), `matrix.log` (shared papers 9.26 → 6.20 s), `split_*.json` (a split prototype with 0 mismatches).
- `io/`: `bench_window*.py` (rolling window 1.45-2.7x; Crossref 3.7 → 6.8 req/s), `doi_overlap*.py`.
- The three candidate designs: `design_throughput.md`, `design_architecture.md`, `design_io.md`.
