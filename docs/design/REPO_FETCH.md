# Repository fetches: batching, a cache store, and OSF's limits

**Status.** Proposal (2026-09-27), a companion to [ARCHITECTURE.md](ARCHITECTURE.md) revision 3 and [BATCH_DESIGN.md](BATCH_DESIGN.md). It answers three requests from the maintainer:
- batch the repository fetches, which take about 40 s today;
- add caching, possibly with a bundled Redis;
- say whether pytacheck and bibr should share a Redis layer.

It adds two decisions (ARCHITECTURE.md §6, 22 and 23) and two packages (FETCH and CACHE, §8), and it amends BATCH_DESIGN's R5, R6, N1, §3.5, §4.1, P3-2 and §7. Nothing in `src/` changes until the decisions are made.

**Evidence markers.** `scratchpad/` means `/tmp/claude-1000/-home-jakub-dev-pytacheck--claude-worktrees-pytacheck-scienceverse-transfer-09704e/3c3979d0-9ad6-438c-9171-6e13a6483378/scratchpad`.
- **(M)**: measured on 2026-09-27 at 86564608, anonymously (no token, no email, `trust_env=False`), from one workstation and one IP. Since 86564608 only CI files have changed.
- **(R)**: read from code or docs. Sources are given as file:line: pytacheck at 86564608; metacheck at b239264f, the parity pin; osf.io `develop` at ee1af72a; WaterButler at 0a8af486; scienceverse/bibr `main` at 05990c5.
- **(E)**: an estimate, always given with its basis.

Request counts are logical requests, with redirect hops folded in. Where every hop counts, the text says "wire requests".

---

## 0. Summary

**The ~40 s reproduces on the demo paper** (metacheck's `inst/demos/to_err_is_human.xml`):
- a cold `repo_check` takes **38.95 s (M)**;
- a `data_check` re-run in a new process with `cache=True` takes **35.6 s (M)**.

About 99% of the wall time is waiting: CPU is 0.5-1.0 s per run **(M)**. OSF dominates, with 73 of 77 wire requests on the demo **(M)**. The causes, largest first:
1. **Serial structure.** OSF projects are listed one after another, the tree is walked level by level, and zip peeks, members and downloads run in serial loops.
2. **The shared HTTP/2 client races under threads.** 21% of same-host concurrent requests fail **(M)**, and the retries' backoff costs 8-40 s per demo run **(M)**. The cause is on the client side (§2.1).
3. **Duplicate and wasted requests.** On psy737, 32 of 63 wire requests (22 of 41 logical) are exact repeats **(M)**. Other waste: a HEAD that always returns 501, a second existence HEAD, and zip members fetched again.
4. **Trailing sleeps** in `batch_query`.
5. **Almost nothing persists.** Even `cache=True` does not keep the OSF tree or the zip peeks.

A separate bug makes `data_check` download 500.5 MiB to get 4.2 KB **(M)**. R has the same bug (§4).

**What this proposes:**

| Lever | What | Where |
|---|---|---|
| **Transport** | The shared client speaks HTTP/1.1 to every host (RF-1). This must land before any change that raises concurrency | BATCH-a |
| **Batching** | Per-host work queues across repositories; OSF id canonicalisation, bulk id lookups and leaf-skip traversal; no wasted HEADs; parallel zip peeks with suffix ranges; reuse of peeked bytes; one download scheduler; archives shared across modules (RF-2 to RF-10, §3) | FETCH, LINKS, REPO, BATCH-b |
| **Cache store** | One `CacheStore`: the in-run memo, then a file store on the machine, then an optional Redis/Valkey backend chosen by URL. It holds repository listings that are validated on every run, immutable, or kept for a short TTL, plus zip-peek indexes and the request ledger. File bytes go to a content-addressed blob store on disk, never to Redis (§5) | CACHE |
| **OSF's limits** | OSF publishes 100 requests/h for anonymous clients. A per-identity request ledger enforces OSF's windows of a minute or longer across processes and runs, and `OSF_PAT` is recommended when a run would exceed them (§2.2, decision 23) | BATCH-a, CACHE |

**Headline targets (E, §7):**

| Workload | Today **(M)** | Cold, after batching (RF-1 to RF-10) | Warm, with the store |
|---|---|---|---|
| demo `repo_check` | 38.95 s | ≈ 12-15 s anonymous; ≈ 10-12 s with `OSF_PAT` | ≈ 2-2.5 s |
| demo `data_check` | 72.9-76.0 s cold; 35.6 s with `cache=True` in a new process | ≈ 20-30 s | ≈ 2-3 s |
| psy737 `repo_check` | 47.3 s | ≈ 8-14 s | ≈ 1-2 s |

**Redis and bibr.**
- **Agreed:** Redis, or better Valkey, as an optional tier for server deployments, bundled in the compose stack.
- **Not agreed:** a layer shared with bibr. bibr never calls a repository host, so it would give datacheck 0 cache hits.
  - No other namespace is shared either: bibr's Crossref cache is for another host (`api.crossref.org`, pytacheck uses `api.labs.crossref.org`), and the two LLM caches use different keys and formats.
  - Sharing would couple eviction policy, credentials and deploys (§6.2).
  - Share the operations layer instead: the same image and monitoring, as sibling containers.

---

## 1. Where the time goes

### 1.1 Runs (M)

Sources: `scratchpad/measure/out/E*.json` (wire counts) and `scratchpad/repofetch/out/*.json` (logical counts).

| Run | Wall | Requests | Where the time went |
|---|---|---|---|
| demo `repo_check`, cold | **38.95 s** | 77 wire (61 to api.osf.io) | listing 22.7 s (OSF 21.3); zip peeks 16.05 s. Sleep 15.1 s: 13.1 s of backoff after 8 EAGAIN errors, plus 2.0 s of trailing `batch_query` sleeps. CPU 0.51 s |
| demo `data_check`, cold | **72.9 s / 76.0 s** (two runs) | 97 logical / 187 wire | listing 20-22 s, zip peeks 11.6-13.6 s, downloads 37-42 s, extraction 0.3 s. 19-34 s of sleep |
| demo `data_check`, `cache=True`, a second new process | **35.6 s** | 56 (46 of them OSF listing) | the recursive OSF listing, zip peeks and zip members are not persisted; the GUID lookups, the GitHub and Zenodo records and the downloaded files are |
| demo `data_check`, same process again | 4.8-5.1 s | 4 logical | zip-member ranges fetched again (a path bug, §4) |
| psy737 `repo_check`, cold | **47.3 s** | 63 wire (41 logical), all api.osf.io, fully serial | 32 exact wire repeats (22 logical; 11.88 s of HTTP) from 9 spellings of 4 OSF ids; 5.5 s of trailing sleeps |
| elife70119 `data_check`, cold | 120-122 s | 46 wire | an OSF whole-node zip of **500.5 MiB for 2 wanted files of 4.2 KB** (45-101 s); Zenodo's record API at 1.3-27 s |

The critical path at unlimited concurrency is 9.78 s for the demo `repo_check`, of which the listing is 7.53 s (E, a dependency-graph replay of the successful requests, `scratchpad/measure/simulate.py`).

### 1.2 Causes

1. **Serial structure (R).**
   - OSF URLs are listed one after another (`modules/_repo_check.py:616`), and registration parents after them (`:683-687`).
   - The tree is walked breadth-first with a barrier after each level (`archives/osf.py:447-487`).
   - A GUID costs 3 serial hops: 301, 302, then 200 **(M)**.
   - Git repositories (`_repo_check.py:792-810`), each dataset host's records, zip peeks (`modules/repo_check.py:591-630`), zip members (`archives/zip_peek.py:529-530`), `_remote_size` HEADs (`archives/download.py:1562`) and whole-repository downloads (`:1715-1946`) all run in serial loops.
2. **The shared HTTP/2 client.**
   - The existing pools run 4 threads in `_fetch_listings` (`osf.py:357`), 10 for OSF pages (`:727`) and 8 for downloads (`download.py:1038`). All of them share one HTTP/2 client (`http.py:84`).
   - **35 of 164** requests that started while another request to the same host was in flight failed with `ReadError [Errno 11]` **(M)**.
   - Each failure is retried after a random 1-2^n s backoff, 8-40 s per demo run **(M)**. The demo's 4-thread listing pool gains about 5% over serial (22.7 s against 24.0 s, M/E).
3. **Duplicate and wasted requests (M).**
   - The OSF memo is keyed on the raw URL string (`osf.py:314-327`), so each spelling of an id is listed again. That gives psy737's 32 wire repeats (22 logical), and 16.6% of all OSF listing calls in the psychology corpus (§1.4).
   - A registration's parent is listed again unless the exact parent URL was also linked (`_repo_check.py:650, 683-684`).
   - The GitHub/GitLab existence HEAD is sent twice, once in the listing and once in the download (`archives/github.py:400`, `download.py:1901-1931`).
   - The OSF `?zip=` HEAD always returns 501.
   - `zip_peek` sends a HEAD before its range GET (`zip_peek.py:235-245`).
   - Zip-member ranges fetch bytes that `zip_peek` already has.
   - One 185 B storage blob was fetched 15 times, for 15 OSF files with identical content.
4. **Sleeps.**
   - `batch_query` sleeps 0.5 s after every batch, including the last (`http.py:324`): 2.0 s on the demo and 5.5 s on psy737 **(M)**.
   - Every `_query(url)` wrapper pays that sleep once per dataset request (`archives/dataverse.py:567-582` and the other dataset clients).
5. **Little persists (M).**
   - With the default `cache=False`, only the ResearchBox archive survives on disk.
   - `cache=True` persists the OSF GUID lookups (`osf_helpers.py:529-548`), the GitHub and GitLab trees (`_repo_check.py:793-809`), the dataset hosts' records (for example `dataverse.py:1386-1392`, `zenodo.py:210-226`) and the downloaded files. It does not keep the OSF recursive tree or the zip peeks, and it never validates what it stores (`archives/info_cache.py:87-120`).

### 1.3 What each lever is worth on the demo (E, `simulate.py` over the M runs)

| Lever | demo `repo_check` | demo `data_check` |
|---|---|---|
| hosts in parallel (L7-6, planned) | −1.6 s | about −1.6 s |
| OSF concurrency at 4 / 8 in flight, on a transport that works | 12.0 / 10.3 s | ≈ 34 / 25.5 s |
| in-run dedupe | 0 (no logical repeats) | −2.5 s of HTTP; psy737 −14.4 s |
| warm persistent listing store, validated as in §5.2 | ≈ 2-2.5 s (0.15 s without validation, in a same-process analogue, M) | its `repo_check` part ≈ −31 s |
| persistent content-addressed blobs | – | downloads 42 s → ≈ 0 |

The planned items alone (BL-1, BL-2 and L7-6) give about 67-72 s on the demo `data_check` **(E**: 72.9-76.0 s less 2.0 s of trailing sleeps, about 1.6 s of host overlap and at most 2.5 s of dedupe**)**. They remove the trailing sleeps and overlap the hosts, but OSF stays serial per project and per level, and the EAGAIN errors stay.

### 1.4 Host mix across real papers (M)

Method: pytacheck's own `collect_links()` over 2,454 distinct papers, run offline, with `href` searched as U-2's fix does (§4), then 49 anonymous OSF lookups (`scratchpad/hostmix/`). Today's finder gives Psych Sci 75.6% linked and 1.1% with 2+ hosts. The table shows the three main corpora: 250 Psychological Science papers (GROBID TEI), PLOS_1000 and eLife_984.

| | Psych Sci | PLOS | eLife |
|---|---|---|---|
| Papers that link at least one repository | 77.2% | 17.2% | 30.4% |
| OSF share of distinct repositories | **92.7%** | 18.0% | 6.6% |
| GitHub share | 2.0% | 30.3% | **66.0%** |
| Linked papers with 2+ hosts | **3.1%** | 7.6% | 20.4% |
| Linked papers with 2+ repositories on one host | 35.8% | 20.3% | 57.5% |
| OSF listing calls per OSF paper, mean / median / p90 / max (E: distinct URL strings plus registration parents, counted over the measured links) | 2.45 / 2 / 4 / 19 | 1.92 / 2 / 3 / 4 | 3.17 / 2 / 4 / 17 |

- **The gains are inside one host, not across hosts.** Threading hosts (L7-6) can overlap at most about 1.5%, 3.5% and 7% of listings in the three corpora (E: 1 minus the mean share of a paper's repositories on its largest host). BATCH_DESIGN §4.1's "repo_check listings 3-4x, since wall time becomes the slowest host" does not hold for any corpus.
- **The demo is a heavy-tail case.** Its 4 OSF listing calls put it in the top 16% of psychology OSF papers (E, by the same call model), and its 8-node tree is larger than every sampled tree. psy737 is in the top 7%. Only 14% of public root projects have any component.
- A cold median psychology paper (2 OSF listing calls) takes about 10 s today (E, from psy737's per-request rates).
- 28% of psychology OSF papers link a registration, and every registration triggers a second full listing of its parent.

---

## 2. Constraints found on the way

### 2.1 The HTTP/2 errors come from the client (M, R)

Source: `scratchpad/h2cause/`, httpx 0.28.1, httpcore 1.0.9, h2 4.4.1.

**Two races in httpcore's sync `HTTP2Connection`:**
- **TLS race.** The read lock and the write lock are separate (`httpcore/_sync/http2.py:61-62`). A thread writing a PING ACK after its read (`:388`) can overlap another thread's `SSL_read` on the same socket, and the reader gets `BlockingIOError [Errno 11]`.
  - Every failing read overlapped a foreign write: 55 of 55, and 43 of 43. Among successful reads, only 20.3% did.
  - Plain `ssl` with two threads and no httpx reproduces it.
- **h2 state race.** Stream-id allocation and HEADERS are unlocked (`:134-135`, `:249`). The result is duplicate or out-of-order stream ids, `KeyError` or `LocalProtocolError`, and 60 s hangs.
  - `KeyError` escapes `http.request`'s retry, which catches only `httpx.TransportError` and `TimeoutException` (`http.py:218`).

**Evidence for the client:**

| Setup | Failed |
|---|---|
| localhost h2 server that never sends GOAWAY | 1.0-16.3% in loop runs; 43% (172 of 400) when it sends a PING every 20 ms |
| per-thread HTTP/2 clients | 0 of 1,800 |
| HTTP/1.1 | 0 of 1,800 |
| api.osf.io, shared HTTP/2, 4 threads × 4 rounds | 10 of 16 |
| api.osf.io, per-thread HTTP/2 | 0 of 16 |
| api.osf.io, HTTP/1.1 | 0 of 16 |
| storage.googleapis.com, shared HTTP/2 | 11 of 16, 4 of them hung about 60 s |

- The live shared-HTTP/2 rows ran with DEBUG logging, which widens the stream-id race: 7 of OSF's 10 failures and all 11 on GCS were `KeyError` or `LocalProtocolError`. pytacheck's own runs, without logging, failed only with errno 11 (40 of 40) **(M)**.
- The servers sent no GOAWAY and no RST_STREAM before any error.
- Our earlier errors were only on OSF because OSF is where pytacheck runs concurrent requests.
- httpcore 1.0.9 is the latest release (2025-04-24).

**RF-1: `http2=False` on the shared client for all hosts** (`http.py:84`). A per-host flag for OSF is not enough, since the race needs only two threads on one connection.
- **Precedent:** `packs/auth.py:388-389` already uses `http2=False`.
- **Cost:** one TCP and TLS handshake per extra connection, 40-80 ms, well under 0.5 s per run (E).
- **Sizing:** `max_connections=32` (`http.py:88`) becomes the global cap on requests in flight. `max_keepalive_connections` must be at least the sum of the host caps.
- **Test:** a regression test asserts that the client negotiates HTTP/1.1.
- **Follow-up:** once nothing negotiates h2, `httpx[http2]` and `h2` can leave `pyproject.toml`.

### 2.2 OSF's limits, and what R6 binds to (R, M)

Source: `scratchpad/osfrate/`.

**What OSF publishes.** developer.osf.io says: "Unauthenticated requests have a rate limit of 100/hour", and "Authenticated requests have a rate limit of 10,000/day" (swagger `:636-648`, and the live `swagger.json`, M).

**What OSF's code does** (`api/base/settings/defaults.py:183-200`):

| Scope | Anonymous (no token, no cookie) | With `OSF_PAT` |
|---|---|---|
| default views (GUIDs, nodes, `/children/`, provider lists, `?filter[...]`) | 10/s and 100/h, per IP | 10,000/day per user; no per-second throttle |
| files-list (`/v2/{nodes,registrations}/{id}/files/{provider}/…`, `api/nodes/views.py:1189, 1206`) | 3/s and 75/min, exempt from the 100/h | not throttled (M: the real throttle classes on DRF 3.15.1 denied 0 of 7,300 requests) |
| WaterButler (files.*.osf.io) and osf.io/download | no limit in the code; an optional limiter of 3,600/h per identity is off by default | same |

- **Cookies bypass the anonymous throttles** (`api/base/throttling.py:44-55`).
- A 429's `Retry-After` is the time left in the exhausted window (DRF `throttling.py:149-160`): up to 3,600 s on the anonymous hourly scope, and up to 86,400 s on the 10,000/day scope a token uses.
- The counters live in a per-process `LocMemCache` (`defaults.py:349-352`), and the production settings are private.

**What our traffic got (M).**
- 451 anonymous api.osf.io requests in 24.5 min, peaking at 12 starts per second. 116 of the 216 default-view requests came after the 100th of the hour (the 27 slash-appending 301s never reach a throttle), and **0 got a 429**.
- Production is therefore looser than the published limits, or does not enforce them per client. But OSF's own web app hit its throttles (osf.io PRs #9658, 2021, and #9922, 2022), and third parties report 429s (BU-Neuromics/gosf#86, 2026-07; GoldenCheetah/OpenData#4, 2018). COS once asked osfclient to back off after "WaterButler servers are getting hammered" (osfclient#103), and osfclient now paces itself at 1 request/s.
- **The measured headroom is not a limit.**

**R's bound (metacheck b239264f, R):**

| Call site | R | pytacheck |
|---|---|---|
| recursive listing | serial, `osf.delay` 0 (`R/archive-osf.R:249, 272`; `R/zzz.R:47`) | 4 threads per level |
| pages 2..n | 10 in flight (httr2 `max_active`) | ≤ 10 |
| `.batch_query` | batches of 5, `req_perform_sequential`, then 0.5 s (`R/utils.R:101-103, 181-197`; sequential "to avoid 429s", `:185-187`); 1 in flight; 10/s asymptote | 5 concurrent |
| osfstorage and Zenodo downloads | 10 in flight, unthrottled | 8 |
| other providers' downloads | serial, 10 per 10 s per host | same |

- pytacheck is already above the pinned R in two places, the traversal and `batch_query`.
- BATCH_DESIGN is inconsistent on this. §0 and §1.2 call R's `batch_query` sequential, while N1 sets "at most `batch_size` in flight".

**What this means:**
1. Anonymously, the published limit binds on every axis, and it is tighter than R. R already goes past 100/h after 2-3 OSF-heavy papers.
2. With a token, only 10,000/day is published. Per-second rate and concurrency fall back to R's bound.
3. Decision 23 recommends reading R's bound as a **host envelope**: at most 10 in flight (R's `max_active`), plus OSF's windows. The strict reading, 1 in flight per call site, forbids OSF fan-out.
4. **N1's `HostPolicy` cannot express this** with one (rate, burst, concurrency). It needs:
   - **path scopes**, since files-list and the provider list share the prefix `/v2/nodes/{id}/files/`;
   - **several windows per scope**;
   - **an identity dimension**, anonymous or a token hash;
   - **sliding-window logs**, as DRF uses. A token bucket of capacity 100 filling at 100/h allows up to 200 requests in a rolling hour.
5. **The windows of a minute or longer span runs.** A second run five minutes later must see the first run's requests, and two runs back to back must share files-list's 75 per minute. BATCH's static `1/jobs` rate share cannot do that, so it needs the request ledger (§5.5).
6. **Never rely on the cookie bypass.** httpx keeps cookies by default, so the client pins an empty jar for OSF hosts.

**Recommended policies (E, from the limits above):**

| Scope | Anonymous | With a token |
|---|---|---|
| api.osf.io default views | 10 per 1 s, 100 per 3,600 s | 10 per 1 s, 10,000 per 86,400 s |
| api.osf.io files-list | 3 per 1 s, 75 per 60 s | 10 per 1 s (R's asymptote), counted in the token's 10,000 per 86,400 s, which OSF publishes for all authenticated requests; its code exempts file lists (`api/nodes/views.py:1206`) |
| api.osf.io, in flight | 4 | 8 (R allows 10) |
| files.*.osf.io and osf.io/download | 8 in flight; 3,600 per hour per identity as an outer cap | same |
| api.github.com | 60 per 3,600 s (published) | 5,000 per 3,600 s |

### 2.3 `filter[root]` cannot keep row order, but a leaf skip can (M, R)

Source: `scratchpad/filterroot/`, 45 anonymous requests.

**Order.**
- `?filter[root]=` returns a whole tree in one request, but ordered by the server's unexposed `modified` field (`api/nodes/views.py:277`).
- `/children/` has no ORDER BY at all: `BaseChildrenList` sets `default_ordering`, which DRF never reads (`api/base/views.py:471-481`).
- The observed `/children` order matches no exposed key, and repo_check does not re-sort rows. So frames built in server order differed from today's on every tree with 2 or more siblings: pngda, 2nyqv and ft6ed.

**Visibility.**
- Anonymously, `filter[root]` returns a superset: public children under private intermediate nodes. On ft6ed, 6 of its 13 descendants cannot be reached by today's BFS.
- With an implicit-admin token it returns a subset (`api/base/utils.py:173-191`).
- With a view-only link it returns only public nodes.

**RF-4′, the leaf skip.** Keep `/children`, but add `related_counts=children` to the GUID or id lookup and to every `/children` URL. Skip the `/children` request for a node whose count is 0.
- The endpoint, order and visible set stay exactly as today.
- Parsed frames were identical on pngda and ft6ed.
- Every node with count 0 had an empty `/children`: 13 of 13 nodes checked. Every count above 0 equalled the listing length: 6 of 6 nodes.
- The query parameter survives the GUID redirect.
- pngda's listing would go from 34 to 28 requests and the demo's `/children` calls from 10 to 2; a 192-node tree (ezcuj) would need 44 `/children` calls instead of 192 (E, computed from the measured trees and counts).

**Bulk id lookups stay.** `GET /v2/nodes/?filter[id]=…` and `/v2/registrations/?filter[id]=…` looked up all 444 distinct OSF ids found in the corpora in 7 requests, and resolved 350 of them: 269 public nodes and 81 registrations **(M)**. That path re-associates results by id, so order does not matter. The 94 misses (in a sample of 30: 21 files, 6 preprints, 3 private nodes) keep today's per-id lookup.
- `/v2/guids/{id}/?resolve=false` cannot replace it. It gives only the referent's type and URL (30 of 30 had no attributes), and the 3 private nodes answered 200 as `nodes`, where today's lookup gets a 401 and labels them private **(M)**.
- Sending today's lookup with the trailing slash skips Django's slash redirect: 2 hops instead of 3 (R, `research/fill2.md`).

### 2.4 OSF has no HTTP validators, and `date_modified` is `last_logged` (M, R)

Source: `scratchpad/osfvalid/`.

**No HTTP validators.** 0 of 217 api.osf.io 2xx responses had an ETag or a Last-Modified, and all of them were `no-cache, no-store, max-age=0, must-revalidate` **(M)**. An RFC 9111 cache would store nothing.

**What node `date_modified` is.** It is `last_logged` (`api/nodes/serializers.py:271`). The only code that writes it is `Loggable._complete_add_log` (`osf/models/mixins.py:139-150`). It is a sound change signal for osfstorage content changed through OSF on that node, with these holes:

| Hole | Why | Closed by |
|---|---|---|
| children | a child's logs attach to the child; the parent is never bumped (M: pngda 2025-12-17 < child m4nbv 2026-01-16) | a per-node vector, never the root's date |
| a move out of a node | only the destination is logged (`addons/base/views.py:671`; WaterButler `core/remote_logging.py:29`). 31 of 79 sampled move/copy logs were cross-node | re-list the whole tree on any change (intra-tree); a max-age backstop (cross-tree) |
| a lost callback | WaterButler tries the log callback 6 times (5 retries, waits of 0+5+10+15+20 s), then drops it (`core/remote_logging.py:23`, `core/utils.py:71-98`). 1 of 198 checkable files had no creation log | the max-age backstop |
| add-on providers | under GravyValet, `create_waterbutler_log` is a no-op (`osf/external/gravy_valet/translations.py:215-216`). M: the demo's GitHub add-on repository got 4 commits after its node's last log, and the date did not move. Re-pointing an add-on to another repository is not logged either: GravyValet logs only `ADDON_ADDED` and `ADDON_REMOVED` (`osf/tasks.py:23-32`) | re-read `/addons/` every run for the linked repository, then its git head SHA for public GitHub; re-list the rest every run |
| backwards dates | legacy webhook logs carry the commit time | compare with `!=`, never `>` |
| the validate/list race | the log lands 0.24-0.48 s after the change (M), up to 50 s with retries | read the vector **before** listing |

Registrations are archived: the osfstorage delete hook is `@must_not_be_registration` (`addons/osfstorage/views.py:439`).

---

## 3. Batching changes

R4 applies to each change: outputs must be provably identical when every request succeeds, and fixtures whose request counts change are re-recorded in the same commit. "Gain" is on the demo unless stated otherwise.

| ID | Change | Touches (R) | Gain | R4 | Package |
|---|---|---|---|---|---|
| **RF-1** | HTTP/1.1 on the shared client, for every host (§2.1) | `http.py:84` | removes the EAGAIN errors and 8-40 s of backoff per demo run, plus the `KeyError` escapes and 60 s hangs **(M)** | wire protocol only | BATCH-a |
| **RF-2** | BL-1 as planned (rolling window, no trailing sleep). Every per-dataset `_query` loop becomes one `batch_query` per host, and each URL keeps `_query`'s error isolation: an exception gives `None` for that URL alone, and `RequestAbort` still propagates. The PsychArchives and DSpace 7 loops, which send dependent `http.request` chains with no sleep, run their URLs in parallel through the limiter | `http.py:314-325`; `_query` loops: `dataverse.py:567-582, 1383-1395`, `figshare.py:398-409`, `fourtu.py:150-160`, `reshare.py:144`, `mendeley.py:134`, `dataone.py:271-282`, `dryad.py:220`; serial `http.request` loops: `psycharchives.py:611`, `dspace7.py:335` | −2.0 s on the demo, −5.5 s on psy737 **(M)**; 0.5 s per `_query` dataset request elsewhere | dedup only; per-URL errors isolated as today | BATCH-a (core), FETCH (call sites) |
| **RF-3** | One work queue per host across a paper's repositories: every OSF id at once, registration parents through the same queue, `list_git` threaded, all through the N1 limiter. Errors isolated per repository; assembly in input order | `_repo_check.py:610-621, 683-687, 792-810` | the OSF listing approaches its slowest project: 22.7 s → ≈ 8-9 s (E: `all_k4`). 51% of psychology OSF papers make 2 or more listing calls | order preserved | FETCH |
| **RF-4** | OSF traversal: (a) the memo key is the canonical id (`osf_check_id`), which also reuses a registration's parent listing; (b) bulk `filter[id]` lookups for nodes and registrations; misses keep today's `/guids/{id}/` lookup, with the trailing slash (§2.3); (c) RF-4′'s leaf skip (§2.3); (d) the files URL is built from the id, so a node's record and its files request run together, while its `/children` request waits for (c)'s count and is sent only when the count is above 0 | `osf.py:314-327, 350-364, 447-487`; `osf_helpers.py:529-548` | psy737 −32 of 63 wire requests (−14.4 s serial) and critical path 13.98 → ≈ 7-8 s (E, `research/code-map.md`); −16.6% of psychology listing calls (E); demo `/children` 10 → 2 (E) | identical (count 0 ⇒ empty `/children`, M 13/13; ids re-associated). The fixture redactors must strip `related_counts`: `tests/mod_repo_check/parity_support.py:35, 40-55` and `tests/archives_osf/osfmock.py:28, 32-45` | FETCH |
| **RF-5** | Drop the wasted HEADs. The GitHub/GitLab existence HEAD becomes the API's 404 (`github.py:400`, `gitlab.py:77`), and download reuses the listing's repository. The OSF `?zip=` HEAD is skipped (it is always 501). The GitHub zipball's size HEAD goes too: today's size warning reads the tree's blob sizes instead, and the cap itself stays with RF-8 (U-5) | `github.py:400`; `gitlab.py:77`; `download.py:1731, 1906-1951` | 2 requests on the demo (its two `?zip=` HEADs, both 501), plus 3 per GitHub repository (both existence HEADs and the zipball HEAD) and 2 per GitLab repository; api.github.com calls 4 → 3 per repository | identical, except the OSF zip gate (U-1, §4) | FETCH |
| **RF-6** | zip_peek runs in parallel across zips through the limiter. A suffix range `bytes=-131072` replaces HEAD plus range, with the size read from `content-range` (M: a 206 with `bytes 5386-6409/6410`). A file whose listed size fits the window is fetched whole. A server that answers 200 falls back to HEAD | `repo_check.py:591-630`; `data_check.py:884-901`; `zip_peek.py:235-285, 315-363` | 13.6-16.05 s → ≈ 5 s (E: the longest single GET) | identical | FETCH |
| **RF-7** | Zip members: keep the peeked bytes. When the archive fits the window, extract locally with 0 requests; otherwise send one coalesced range per member, in parallel. Fix the member-cache path (U-4) | `zip_peek.py:332-344, 464-471, 529-530`; `download.py:1452-1456, 1495-1500` | 4.9 s → 0; the same-process re-run 4.8 s → 0 **(M baseline)** | identical | FETCH |
| **RF-8** | One download scheduler: all hosts' whole-repository zips concurrently under per-host caps; `_remote_size` HEADs in parallel, or skipped when the listing gives a size; the serial remainder on per-host threads; large files inside the pool; file blobs read from and written to the store by the listing's hash (§5.1). The GitHub zipball is capped by the want-set: small want-sets use per-file raw fetches, but only when the tree has no `.gitattributes` and no LFS pointers, since `git archive` applies export-ignore, export-subst and eol rules and may include LFS content, and raw fetches do not; otherwise the zipball | `download.py:1562, 1715-1946, 1978-2002` (becomes `archives/fetch.py`) | downloads 37-42 s → ≈ 8-12 s (E); elife70119's zipball 51.8 MB → ≈ 5 KB (M baseline) | identical | LINKS (the fetch.py engine) |
| **RF-9** | Fetched archives and files are shared across data_check, code_check and the others in one run: the zip stays in the run directory, and code_check goes through `RepoIndex.fetch` | `download.py:1110-1296`; `code_check.py:281-293` | one zip download per repository per run, not one per module (R) | identical | REPO (`RepoIndex.fetch` with the shared run directory); LINKS (the zip kept, in `archives/fetch.py`); CODE-b (code_check onto `RepoIndex.fetch`) |
| **RF-10** | BL-2's HTTP memo, as planned. It also covers the in-process GitHub listing repeat **(M)** | `http.py` | 1-3 requests per Git repository | dedup | BATCH-b |

**Not proposed:**
- **Skipping data_check's nested zip peek.** R runs it too (`inst/modules/data_check.R:401-404`), so dropping it would be a behaviour change. RF-6 and the store bring it to about 5 s cold and 0 warm.
- **`filter[root]` for traversal**, because of the order problem (§2.3).
- **Per-thread HTTP/2 clients**: they fail no more than HTTP/1.1 (§2.1), but in sync threaded code multiplexing adds no parallelism, since the thread count sets it either way (`research/fill1.md`).
- **An asyncio client**, already rejected in BATCH §7.
- **Removing L7-6.** Host threads stay: they are cheap, and they help eLife-like papers (20% use 2 or more hosts).

---

## 4. Bugs found (U-entry candidates)

| # | Bug | Where (R) | In R too? | Effect | Fix | Package |
|---|---|---|---|---|---|---|
| U-1 | **The OSF zip gate never refuses.** `_remote_content_length` ignores the status: OSF answers a HEAD on `?zip=` with 501 and `Content-Length: 0`, which reads as 0 bytes. `node_osf_n` counts only the rows passed in, and data_check passes only the wanted subset | `download.py:615-633, 1731-1740`; `data_check.py:926-940` | **yes**: `R/repo-download.R:205-217, 1519, 1547-1552`; `R/code_check.R:289` | elife70119: 500.5 MiB and 45-101 s for 4.2 KB **(M)** | A non-2xx HEAD means the size is unknown. Gate on the summed listing sizes, and count the node's full listing (the `repo_file_counts` data_check already computes, `:929-938`) | FETCH |
| U-2 | **GitHub and GitLab hyperlinks are never found on bibr12 JSON or GROBID TEI reads.** `_host_links` searches the url table's first column, which on those reads is `url_id` | `archives/github.py:331`; `text/search.py:255-256` | **yes**: `R/text_search.R:102`; `R/import-bibr12.R:57` | 0956797620970559.xml holds a github.com link twice and finds 0 repositories; fixing it recovers 7 GitHub and 1 GitLab repositories in the psychology corpus **(M)**. On biomedical papers read from TEI or bibr12 JSON (GitHub 30-66% of repositories), most GitHub repositories would be missed **(E**, from the mechanism**)** | Search `href` explicitly, as the OSF, Zenodo, Dataverse and FSD finders already do. This raises GitHub traffic: about 10 api.github.com calls per eLife paper that links GitHub, against 60/h anonymous (E: 2.58 repositories × 4 calls), so it needs the GitHub policy in §2.2 | FETCH |
| U-3 | The OSF memo stores errors and incomplete listings, against R5. In a long-lived server a transient error sticks until restart | `osf.py:488-490` | not checked | wrong results after a transient failure | store only complete, error-free listings | FETCH |
| U-4 | The zip-member cache path is never hit: members are written where the reuse check does not look | `download.py:1452-1456` against `:1495-1500` | not checked | members fetched again on every run | RF-7 | FETCH |
| U-5 | The GitHub zipball has no size cap and only warns | `download.py:1931-1958` | not checked | 51.8 MB for 10 wanted files of 4.4 KB **(M)** | RF-8 | LINKS |
| U-6 | httpcore's `KeyError` escapes `http.request`'s retry | `http.py:218` | – | no retry at the HTTP layer. An OSF page becomes `request_failed`, which the OSF layer retries twice (`osf.py:604-609, 718-719`). A GUID batch fails the paper's whole OSF block (`osf_helpers.py:538-540`; `_repo_check.py:739, 763`). A parallel download fails its file (`download.py:1004-1005`). An unguarded caller such as `crossref_doi` raises (`db/crossref.py:719-724`) | gone with RF-1 | BATCH-a |
| – | `repo_info_cache()` is read nowhere; `PYTACHECK_BIBR_URL` in the compose file is read by nothing | `info_cache.py:35-41`; `docker-compose.yml:21` | – | dead settings | wire them to the store (§5.6), or remove them | CACHE |
| – | Resolving a cache path creates that cache's `.metacheck_*` directory in the current directory (the repo-file, repo-listing and LLM caches each have one), and the documented `docker run --rm` loses its anonymous `/cache` volume, so every Docker CLI run starts cold | `archives/cache.py:20-42`; `README.md:27`; `Dockerfile:56, 60` | R's own cache directory rule was not checked | clutter; cold runs | the store lives in `config.cache_dir()`; the README mounts a named volume | CACHE |

U-1 and U-2 change results (Band A), so each needs a deviation row under FIDELITY.md. U-5's fix does not: RF-8 still fetches every wanted file, only by a cheaper route. Reporting U-1 and U-2 to metacheck is the maintainer's call.

---

## 5. The cache store

### 5.1 What is cached, and how it is validated

Every entry is one of three kinds: **validated** on every run, **immutable** (keyed by a hash, SHA or version), or held for a **short TTL** (1-30 d). A TTL entry can be up to one TTL old; nothing else can be stale beyond the OSF max-age backstop.

| Object | Key (plus identity, §5.4) | Validation on every run | Lifetime | May go to a networked tier |
|---|---|---|---|---|
| OSF osfstorage tree (nodes, files, folders) | linked id (as `osf_check_id` gives it) | the root's tree vector (§5.2): 1 request per 100 nodes | max-age 7 d, configurable | only if fetched anonymously |
| OSF registration tree | linked id | the same vector, from `/v2/registrations/` | max-age 30 d (E; registrations are archived) | only if fetched anonymously |
| OSF GitHub add-on folder, public repository | repository + head SHA | `GET /v2/nodes/{id}/addons/` for the linked repository (M: 1.97 s; re-pointing an add-on writes no log, §2.4), then `GET https://github.com/<repo>.git/info/refs?service=git-upload-pack` (M: 0.34 s, 444 B, no REST quota) | immutable per SHA | only if fetched anonymously |
| other OSF add-on folders; private GitHub; any listing reached through a view-only link | – | not cached across runs: re-listed every run | – | – |
| GitHub and GitLab trees | repository + commit SHA | `info/refs` for GitHub; GitLab: `info/refs` (untested) or 1 API request | immutable per SHA | only if fetched anonymously (GitLab marks responses `private`) |
| GitHub `/repos` metadata | URL | none within the TTL; then ETag revalidation (a 304 costs no quota only with a token) | TTL 1 d | only if fetched anonymously |
| Zenodo record | record (version) id | none within the TTL; then `W/"n"` ETag revalidation (R: upstream-apis) | TTL 7 d | only if fetched anonymously |
| Dataverse dataset | persistentId + version | none: a version is immutable; the latest-version pointer has its own TTL | immutable; pointer TTL 1 d | only if fetched anonymously |
| Figshare, 4TU, Dryad, DSpace, PsychArchives, ReShare, Mendeley records | URL | none within the TTL; then an ETag where the host sends one, else a re-fetch | TTL 1 d | only if fetched anonymously |
| OSF id → type (bulk `filter[id]`, `/guids/`) | id | none within the TTL. Only a 200 answer's type is stored; a 401 or 403 ("private") lasts one run, like a 404 | TTL 30 d | only if fetched anonymously |
| zip-peek index (central directory) | the listing's file URL + size + the listing's hash (OSF sha256, Zenodo md5) | the listing's size and hash, so a warm run sends no storage request. Without a listed hash, the storage ETag or Last-Modified (OSF storage sends both, **M**), which costs the osf.io and WaterButler redirects | as its listing | only if fetched anonymously |
| file bytes (blobs) | the strongest hash the listing gives (OSF sha256, Zenodo md5, git blob SHA-1); otherwise URL + size + storage validator | verified on write; reuse needs a hash match | LRU by size; 2 GB by default (E) | **never**: local disk only |
| negative answers (404, 410) and "private" answers | – | in-run only: 404 and 410 in BL-2's HTTP memo; a 401 or 403 "private" answer in the run cache (N2), since BL-2's memo keeps only 2xx, 404 and 410 | one run | no |
| errors, 429s, 5xx | – | **never stored** (R5) | – | – |

- **Lookups keep BATCH §7's rule.** Crossref, PubPeer, retractions and replications are not in the store, and a persistent cache for them stays off by default. A re-run months later must not miss a new retraction.
- **The LLM cache stays where it is:** R-compatible `.rds` files on disk, shared with metacheck (`llm/cache.py`), outside the store.

### 5.2 The OSF tree vector

1. Before any listing request, read `GET /v2/nodes/?filter[root]=<root>&page[size]=100`, or `/v2/registrations/?filter[root]=` for a registration, following `links.next` to the last page. Keep `{node id: date_modified}` as a vector (M: pngda, 8 nodes, 0.80 s; ezcuj, 192 nodes, 2 pages at 7.9 s and 7.7 s). Accept the vector only if its distinct ids number `links.meta.total`; otherwise treat the tree as changed. The list is sorted by `-modified`, so an edit during the read can skip or repeat a node (`api/nodes/views.py:277`).
2. The lookup that gives the root (the GUID or id record, whose `relationships.root` names it, M) runs before step 1, so its record of the linked node predates the vector. Keep that record only if its `date_modified` equals the vector's entry for that node; otherwise re-read it after step 1. Every other request whose result is stored starts after step 1.
3. Accept the stored listing only if **all** of these hold:
   - the fresh vector's node set equals the stored vector's node set;
   - every `date_modified` string is equal (with `!=`, never `>`);
   - the fresh vector covers every node of the stored listing. This matters for token callers, whose `filter[root]` omits implicit-admin nodes (§2.3).
4. On any difference, re-list from the linked node with today's traversal, and store the listing with the vector read in step 1. The whole root vector is compared even for a component link, because a move out of the subtree is logged only at its destination.
5. The max-age backstop re-lists regardless. `--cache-store refresh` re-lists everything.
6. Add-on folders inside the tree follow their own rows in §5.1. A tree with any add-on other than a public GitHub repository (GitLab, Bitbucket and private GitHub included) re-lists that folder every run, which costs 1.5-6.4 s per folder level **(M)**.

A root link and a component link in the same tree are separate entries, each storing its root's vector beside today's BFS output from the linked node. Rows are therefore unchanged, except the file `downloads` count: OSF bumps it without a log, so no vector sees it. Served from the store, `downloads` in `osf_info()` and `osf_file_download()` can be up to max-age old; their docs say so, and a deviation row records it. repo_check's frame does not carry the column.

### 5.3 Tiers and backends

| Tier | What | Default |
|---|---|---|
| 0: in-run memo | `RunContext.cache` (N2) | always |
| 1: local store | one file per key under `config.cache_dir("store")` (the OS user cache directory from platformdirs, or `PYTACHECK_CACHE_DIR`, which is `/cache` in the image); JSON in a versioned envelope, written with `os.replace`; blobs under `blobs/<algo>/<ab>/<hash>`; a size-bounded LRU sweep at most once per run, which never touches the ledger files | on (decision 22) |
| 2: networked | `RedisStore` (optional extra `pytacheck[redis]`, redis-py, MIT), chosen by `PYTACHECK_CACHE_URL=redis://…`, which also works for Valkey. Cache reads and writes fail open, with bibr's timeouts (2 s connect, 5 s per operation, `bibr/cache.py:70-71`), and after the first failure the tier is skipped for the rest of the run; prefix `pytacheck:v1:` | only when configured |

Reads go memo → local → networked, and writes go through to every tier that may hold the entry. With a networked tier the local tier still holds the blobs.

**Why files, not SQLite** (P3-2 chose SQLite):
- The read cost is the same: 19.3 µs against 21.3 µs per read and parse of a 23.7 KB entry **(M**, `scratchpad/deploy-modes/local_tier_bench.py`**)**.
- SQLite's WAL does not work on a network filesystem (https://www.sqlite.org/wal.html), which is where HPC home directories live.
- `repo_info_cache` already writes JSON with `os.replace` (`info_cache.py:120-123`), and bibr's LLM cache uses the same layout.
- The cost is many small files, which the LRU sweep bounds.

### 5.4 Keys, identity, and what may be shared

- **Key shape:** `pytacheck:v1:<kind>:<host>:<id>:<identity>[:<validator>]`. Identity is `anon` or a hash of the credential, never the token itself. It is part of every key in both tiers except blob paths, which are content hashes verified on write (`blobs/<algo>/<ab>/<hash>`, §5.3), and the ledger keeps one count per (scope, identity) (§5.5).
- **Fetched anonymously** means that no hop carried a credential: no Authorization or `X-Dataverse-key` header, no token in the query, no cookie, and no `view_only` key.
- **The networked tier takes only `anon` cache entries** (listings, peek indexes, id types), **plus the ledger's counts for every identity**, which hold only request times under a hashed identity (§5.5). It never takes paper-derived content (text, LLM answers), credentialed responses, or file bytes.
- **Who may read what.** A token caller may read an `anon` entry only where the answer does not depend on who asks: git trees by SHA, versioned dataset records, zip-peek indexes and blobs, each keyed by a hash, SHA or version. OSF trees and OSF id types are read only under the caller's own identity.
- **View-only links.** A link with `view_only` exposes private nodes, and `filter[root]` ignores the key (§2.3), so no vector can validate its listing. Such a listing is re-listed every run, like an add-on folder other than a public GitHub repository, and never reaches the networked tier. An `anon` entry is never served to a `view_only` link.
- **Today's in-process OSF memo keys on the raw URL string, not the canonical id** (`osf.py:314-327`). Neither it nor `repo_info_cache` keys on the credential (`{host}_{id}`, `info_cache.py:71-79`; canonical ids for OSF, raw URLs for GitHub and GitLab). In `pytacheck serve`, a listing fetched with `OSF_PAT` can be served to a later anonymous request. The store keys on both, and FETCH moves the memo onto it.

### 5.5 The request ledger

OSF's windows of a minute or longer (§2.2) span processes and runs, so an in-process limiter cannot enforce them.

- **Division of labour.** The ledger keeps every window of a minute or longer: files-list's 75 per 60 s, default views' 100 per hour, a token's 10,000 per day, the 3,600 per hour outer cap on files.*.osf.io and osf.io/download (§2.2), and GitHub's hour. Shorter windows and in-flight caps stay in N1's in-process limiter, with BATCH's rate share.
- **What it records.** For each (host scope, identity), the start times of the requests in the longest window, trimmed on every write. It counts every request the server may have seen, including attempts that ended in a transport error.
- **Local backend.** One small file per (scope, identity) in the store, updated under an `O_CREAT | O_EXCL` lock file that holds a unique token. A process never waits for a window while it holds the lock.
  - A lock older than 10 s is stale, judged against the mtime of a file just touched in the same directory, so on the file server's clock.
  - A stale lock is broken only after re-reading its token and finding it unchanged, under a second `O_EXCL` lock (`<name>.break`), so two processes cannot both take it.
  - `cache_store="off"` and `"refresh"` do not reset the ledger, and the LRU sweep never deletes it.
- **Redis backend.** A sorted set per (scope, identity), updated by one Lua script (ZREMRANGEBYSCORE, ZCARD, ZADD) that scores by the server's `TIME` and adds a unique member per request (the time plus a random suffix). Ledger keys carry **no TTL**, so `volatile-lru` never evicts them (§6.3). A set is trimmed only when its own identity sends again, so an identity that stops sending (a retired token) leaves its set behind, with at most one longest window of members. At most once per run, the backend SCANs the ledger keys and deletes the sets whose newest member is older than their longest window.
- **When a window is exhausted** (decision 23):
  - the limiter waits up to 60 s, or not at all under `skip_on_api_limit`. For a known reset, `http.request` already skips the wait under `skip_on_api_limit`, but otherwise sleeps until the reset with no cap (`http.py:209-213`);
  - beyond that, the request fails as a 429 would, with a message naming `OSF_PAT` (or, for GitHub, `GITHUB_PAT_GITHUB_COM`, `archives/github.py:418`) and the reset time. That needs a deviation row, since R would have sent the request (CACHE's gates, §8);
  - a run forecast to exceed the anonymous hour prints one warning at the start that recommends a token.
- **If the networked ledger fails** (an error or a timeout), the limiter uses the local file ledger for the rest of the run, or the in-process windows at the `1/jobs` share if that fails too. It warns once and records the fallback in `run.json`. During a fallback, replicas no longer share one count, so OSF's windows can be exceeded across replicas; decision 23's cost says so.
- **Scope of the count.** The ledger counts per store directory (by default per OS user, per volume in Docker), or per Redis. Users, containers or machines that share an egress IP but not a store each think they have the whole budget. The docs say so, and point them at one `PYTACHECK_CACHE_DIR` or `PYTACHECK_CACHE_URL`.

### 5.6 Defaults, switches and the run record

- **Option `cache_store`**: `"on"` (default), `"off"` or `"refresh"`. It is also set by `PYTACHECK_CACHE_STORE` and by the CLI's `--cache-store`.
- **R's `cache=` argument does two things today.**
  - Every repository module reuses listings from `.metacheck_repo_info_cache`, without validation (`osf_helpers.py:529-548`, `_repo_check.py:793-809`, and the dataset clients).
  - `data_check` and `code_check` also keep downloads in `.metacheck_repo_cache`.
  - The download half keeps its meaning and stays independent of the store. The listing half moves into the store: FETCH moves those call sites onto it, `repo_info_cache()` becomes an alias of `cache_store`, and `.metacheck_repo_info_cache` is no longer read or written. With `cache_store="off"`, `cache=True` reuses no listings.
- **The ledger runs whatever `cache_store` says.** It is off only in the parity harness and the tests.
- **The parity harness and the test suite run with the store off**, so fixtures always hit their mocks. FETCH's gates add a cold-then-warm run of the replayed row with the store on; CACHE's gates test the store and the ledger directly (§8).
- **The run record (`run.json`) gains** the entries served, validated and re-fetched; the age of the oldest entry served under a max-age or TTL (P3-2's rule); the time spent waiting on ledger windows; and any ledger fallback.

### 5.7 Rejected

| Rejected | Why |
|---|---|
| SQLite for the local tier | §5.3: the same speed, and it breaks on network filesystems |
| hishel as the HTTP cache | 1.4.0 is "3 - Alpha"; its key is the URL only, so it can leak an authenticated response across tokens; its force mode does not revalidate (R: upstream-apis) |
| RFC 9111 semantics | the hosts send almost no validators; OSF sends `no-store` (§2.4) |
| TTL-only OSF listings | either stale until expiry, or a full re-list (35.6 s) |
| the root node's `date_modified` alone | it misses children and add-ons (§2.4) |
| file bytes in Redis | MB to GB per repository; a filesystem keyed by hash fits better |
| sending cookies to OSF | it would bypass the anonymous throttles (§2.2) |
| the store on by default for lookups | BATCH §7, unchanged |

---

## 6. Redis, Valkey and bibr

### 6.1 Where a networked tier helps (R, M)

Source: `scratchpad/research/deployment.md`.

| Mode | What it needs |
|---|---|
| notebook, CLI, `pytacheck batch -j N` on one machine | memo + local store; workers share through files |
| HPC | the local store on the shared filesystem (atomic rename, `O_EXCL` locks); no services |
| Docker CLI | the local store on a named volume |
| `pytacheck serve`, one replica | memo + local store on the `/cache` volume |
| compose with several replicas; the scienceverse platform | + the networked tier: one cache and one ledger across replicas |

- A local file read costs 19 µs, a network round trip to another host on a private network 3.3 ms (a ping, which is the floor for a Redis GET), and one OSF call about 0.3 s **(M)**.
- The scienceverse platform plans to call `pytacheck serve` over HTTP, replacing the R plumber service; its checks worker runs one job at a time today (R).
- With one replica, the file store on a volume is enough. A networked tier pays off when there are several replicas.

### 6.2 Sharing with bibr (R)

| Service | bibr | pytacheck | Shared hits |
|---|---|---|---|
| Repository hosts (OSF, GitHub, Zenodo, …) | none: no bibr code sends a request to them (the names appear only in text patterns and messages) | all of datacheck's traffic | **0** |
| Crossref by DOI | `api.crossref.org/works/{doi}` and bulk `filter=doi:`, stored as `crossref:cache:works:{casefolded doi}`: the whole response for a single lookup, or `{"message": item}` when the bulk prefetch seeded it (`bibr/clients/crossref.py:21, 348-431, 520-529`) | `api.labs.crossref.org/works/{doi}`, with the `status` envelope (`db/crossref.py:652-730`) | 0: a different host, so neither reads the other's entries (bulk-seeded ones also lack the envelope). Aligning them would break R's request shape (R4) |
| Crossref search | `query.bibliographic` with `select`, `rows={limit}` | `rows=1&sort=score`, no `select` | 0 |
| LLM | SHA-256 JSON files | MD5 of R `serialize`, `.rds`, shared with metacheck | 0 |
| Rate limits | `rate_limit:{crossref,llm}:*`, unprefixed (`bibr/utils/rate_limiter.py:151, 198`) | its own per-host limiter | sharing would be interference: bibr's traffic would throttle pytacheck's LLM calls |

- **pytacheck's module runs make no Crossref calls at all.** Crossref is reached only through the opt-in GROBID `crossref_lookup` (`io/grobid.py:1430`) or explicit `db/` calls.
- **Reuse already happens through data.** `ref_accuracy` reads bibr's `bib_match` from the export.

**Risks of one instance:**
- **Eviction.** `maxmemory` and the policy apply to the whole instance. bibr itself needs `allkeys-lru` for its Crossref cache but `noeviction` for jobs (`bibr/config.py:1286-1288`).
- **Privacy.** bibr's result cache and job results hold full extractions of uploaded, possibly unpublished papers. With one credential for both apps, a flaw in either could read the other's keys.
- **Ops coupling.** A bibr deploy that flushes its cache database would flush pytacheck's ledger, and upgrades would have to be joint.
- **Licence.** bibr pins `redis:7-alpine`, which is 7.4 (RSALv2/SSPLv1, not OSI).

**Verdict.**
- A Redis tier: yes, pytacheck's own, optional.
- A shared keyspace or instance: no.
- The operations layer can be shared: one image and one monitoring setup, run as sibling containers with separate passwords.
- A shared Crossref politeness budget for one egress IP is a limiter question, and it would protect about 0 requests today. It waits until pytacheck makes Crossref calls in module runs.

### 6.3 The bundled server

- **Server and licence.** **Valkey** (BSD-3), or Redis ≥ 8 under its AGPLv3 option. Not `redis:7.x`: 7.4 is RSALv2/SSPLv1.
  - pytacheck is AGPL-3.0-or-later.
  - Pulling an official image is not redistribution; shipping one inside the pytacheck image would be.
- **Compose.** `docker-compose.yml` gains a `valkey` service, pinned by digest, with no published port.
  - Command: `valkey-server --requirepass $PYTACHECK_VALKEY_PASSWORD --maxmemory 256mb --maxmemory-policy volatile-lru --appendonly yes`, on a named volume.
  - pytacheck gets `PYTACHECK_CACHE_URL=redis://:…@valkey:6379/0`.
- **Why `volatile-lru` with AOF.** Every cache entry written to the networked tier carries an expiry: its §5.1 lifetime, or 30 d for an immutable entry, reset on a hit (E). So there is always a key to evict: with no expiring key left, `volatile-lru` acts like `noeviction` and refuses all writes, the ledger's included. Ledger keys carry none, so they are never evicted. AOF keeps the ledger across restarts, so a restart does not reset OSF's hourly count.
- **The pip install never needs a server.**

### 6.4 The scienceverse platform

- **Instance.** Its own instance, one per role as the platform already runs its Redis servers: about 512 MB, its own password, reachable only on the private network. Valkey is preferred.
- **Privacy.** It holds only public metadata. Paper content stays per job, as the platform requires.
- **Persistence.** A RAM-only instance restarts with an empty ledger, so it should keep AOF, or the operator accepts that window. The platform owner decides.

---

## 7. Expected gains (E)

Basis: `simulate.py` over the M runs with the dead attempts and their backoff removed; the OSF policy floors of §2.2; and, for warm runs, fill-ins measured in `scratchpad/osfvalid/` (filter[root] 0.80 s, `/addons/` 1.97 s, `info/refs` 0.34 s).

| Workload | Today | Cold, RF-1 to RF-10 | Warm, with the store |
|---|---|---|---|
| demo `repo_check` | 38.95 s (M) | ≈ 12-15 s anonymous (21 files-list requests at 3/s set a ≈ 7 s floor); ≈ 10-12 s with a token | ≈ 2-2.5 s: 4-6 validator requests in parallel |
| demo `data_check` | 72.9-76.0 s (M) | ≈ 20-30 s | ≈ 2-3 s (blobs from the store; extraction 0.2-0.3 s) |
| demo `data_check`, `cache=True`, new process | 35.6 s (M) | – | ≈ 2-3 s |
| psy737 `repo_check` | 47.3 s (M) | ≈ 8-14 s (32 wire repeats gone; ids resolved in bulk) | ≈ 1-2 s |
| elife70119 `data_check` | 120-122 s (M) | Zenodo-bound: ≈ 5-30 s (its record API ranged 0.25-27 s); the OSF zip 45-101 s → ≈ 3 s | ≈ 1-2 s |
| median psychology paper, `repo_check` | ≈ 10 s (E, §1.4) | ≈ 3-5 s (E: its 2 listing calls in parallel, without sleeps or backoff) | ≈ 1 s |
| anonymous OSF budget per IP | ≈ 3.6 cold demo-like papers per hour (E: 100 / 28 measured default views); ignored today | ≈ 5-6 per hour (≈ 16-20 default views after RF-4) | ≈ 15-25 re-runs per hour (4-6 counted requests each) |
| with `OSF_PAT` | ≈ 196 demo-like papers per day (E: 10,000 / 51 measured requests, counting every request) | ≈ 250 per day (10,000 / ≈ 40, counted the same way) | – |

One sample per condition was measured, OSF latency is heavy-tailed, and Zenodo was degraded that day. These numbers are for planning; CLOSE re-measures them.

---

## 8. Plan changes

**Decisions.** 22 (the store and Redis) and 23 (OSF's limits under R6), in ARCHITECTURE.md §6.

**Packages (E):**

| Package | Days | Window | Content |
|---|---:|---|---|
| **BATCH-a** | 2 → **3** | 6.5-9.5 | + RF-1 with its test and pool sizing; `HostPolicy` path scopes, several windows, identity and sliding-window logs; the OSF and GitHub policies (§2.2); an empty cookie jar for OSF hosts |
| **FETCH** (new) | **4** | 9.5-13.5, after BATCH-a | RF-2's call sites, RF-3 to RF-7, U-1 to U-4, the OSF vector and git-SHA validators on CACHE's API, and the `repo_info_cache` call sites moved onto the store |
| **CACHE** (new) | **4** | 9.5-13.5, after BATCH-a | §5 and §6.3: the store protocol, file backend, blob store, ledger, Redis backend and compose service, the switches and run-record fields, the dead settings |
| **LINKS** | 7.5 → **8** | 16-24 (was 16-23.5) | + RF-8's scheduler, the zipball cap (U-5), and the download side of the blob store (read and write by the listing hash, through CACHE's API) in the `archives/fetch.py` engine |
| **REPO** | unchanged | unchanged | RF-9's `RepoIndex.fetch` with the shared download directory; LINKS keeps the zip, and CODE-b moves code_check onto it; RF-3's queues arrive from FETCH; L7-6 stays |
| **BATCH-b** | unchanged | unchanged | RF-10 (BL-2's HTTP memo) sits in front of the store |

**FETCH.**
- **Files:**
  - the network paths in `archives/**`: `osf.py`, `osf_helpers.py`, `zip_peek.py`, `github.py`, `gitlab.py`, the dataset clients' `_query` call sites and their `repo_info_cache` call sites (onto the store), and in `download.py` `_remote_content_length` (`:615-633`), the OSF zip gate (`:1731-1740`), the Git repository reuse and the GitHub zipball size HEAD with the warnings that read it (`:1906-1951`) and the member path (`:1452-1500`);
  - `modules/{repo_check,_repo_check}.py` (the listing loops and `_peek_zips`);
  - `modules/data_check.py`: the zip-peek loop (`:884-901`, RF-6) and the zip-gate count (`:926-940`, U-1);
  - `tests/{mod_repo_check,mod_data_check,archives_d1,archives_d2,archives_gz,archives_osf,repo_download}/**`, `mod_data_check` until DATA-b;
  - a new replayed row in `parity/cassettes/**`, through the `parity/**` owner.
- **Gates:**
  - G1 on the archives areas, repo_download, mod_repo_check and mod_data_check (+ review), with fixtures re-recorded where request counts change (R4);
  - the replayed row: `repo_check` and `data_check` on the demo, psy737 and one GitHub + Zenodo paper, recorded live once with HARNESS-NET's mechanism. HARNESS-NET's H0-16 does not cover this traversal today;
  - frames identical with and without RF-4′ on the recorded trees;
  - with the store on, the replayed row run cold and then warm gives identical outputs;
  - recorded vector fixtures (a child's date changed, a node added or removed, a stored node missing from a token caller's vector, a vector over 2 pages that skips or repeats a node (distinct ids short of `links.meta.total`), a component link against a root link in one tree, a view-only link) each force a re-list;
  - deviation rows for U-1 and U-2, and for OSF file `downloads` counts served from the store (§5.2), with the note in `osf_info()`'s and `osf_file_download()`'s docs.
- **Depends on** BATCH-a, and on CACHE's store protocol (day 10.5) for the validators.
- **Token fixture.** The token-bearing OSF fixture of `scratchpad/research/fill4.md` §7 needs two OSF test accounts, which the maintainer has to create. Until they exist, RF-4′ runs only without a token.
- **Order of work.** `repo_check.py` and `_repo_check.py` first, days 9.5-11, because REPO takes them over at day 13.5.

**CACHE.**
- **Files:**
  - `cache/**` (new);
  - `archives/{info_cache,cache}.py`;
  - the ledger hook in `http.py`, held between BATCH-a's close and BATCH-b;
  - `docker-compose.yml`, `README.md` (the Docker volume), `docs/CACHE.md`, `tests/cache/**`;
  - `cli.py` (`--cache-store` only, after BATCH-a);
  - `config.py`, `pyproject.toml` (the `redis` extra), `provenance.py` (the run-record fields), `core/run.py` (`RunContext.store`) and the Valkey service in `.github/workflows/**`, through the core agent.
- **Gates:**
  - ledger windows tested with a fake clock across two processes, as a sliding log, not a bucket;
  - a Redis test against a Valkey service container in CI;
  - fail-open when the backend is down;
  - a credentialed or view-only response never reaches the networked tier, and a token caller is never served an `anon` OSF tree or id type;
  - a deviation row under FIDELITY.md for a request the ledger refuses (decision 23), with a test that `skip_on_api_limit` fails it at once;
  - with the store off, outputs byte-identical to today on the full parity run and the accuracy gate;
  - NFS-safe writes (atomic rename, `O_EXCL` locks).

**Schedule.**
- **Critical path.** Unchanged at ≈ 27 days. FETCH and CACHE run on days 9.5-13.5, after BATCH-a's third day (8.5-9.5). Before this proposal, ARCHITECTURE §4.4 had only HARNESS-v2 and the core agent on days 8.5-11.5, and PORT from day 11.5.
- **The zero-slack chain.** BATCH-a (placed at 6.5-9.5) → FETCH → REPO has no float: FETCH ends at day 13.5, when REPO takes over `_repo_check.py`, so a slip in BATCH-a or FETCH delays REPO. So does a slip in CACHE's store protocol (day 10.5).
- **The LINKS path** grows to 26.5 days, still under 27.
- **Peak parallelism** on days 9.5-13.5 is 4-5 agents, under the plan's 7.
- **Total:** ≈ 116.5 + 9.5 = **≈ 126 package-days in 36 packages**.

**Doc amendments made with this proposal:**
- ARCHITECTURE.md: the header, §0, §2.6, §2.8, §3.2, §4.3, §4.4, §4.5, §4.6, §5.2, §5.3, §6 and Appendix A.
- BATCH_DESIGN.md: R5, R6, N1, §3.5, §4.1, P3-2, §7.
- `docs/design/README.md` and `improvements.md` (#6).

---

## 9. Open questions for the maintainer

1. **Which workload is the ~40 s?** The paper; the command (`repo_check` or `data_check`, CLI or Python); `cache=True` or not; a first or a repeated run; and the network. The demo gives 38.95 s cold and 35.6 s on a `cache=True` re-run, and a median psychology paper gives about 10 s (§1). If a median paper takes 40 s, look at `data_check`'s downloads, or at HTTP/2 backoff.
2. **Two OSF test accounts** for the token-bearing traversal fixture (§8).
3. **Upstream reports** of U-1 and U-2 to metacheck.
4. **Early fixes.** RF-1 (one line), U-1, U-3 and RF-4(a) (the canonical OSF key) touch files that no phase-A package holds. They could land ahead of the rewrite as small PRs if the speed is wanted now. Without RF-1, today's 4-, 8- and 10-thread pools keep hitting the HTTP/2 race.
5. **The platform.** Will it run more than one `pytacheck serve` replica? That decides whether it needs the networked tier.

---

## Appendix: evidence

All paths are under `scratchpad/`.

| Topic | Where |
|---|---|
| Wire-level runs, simulations, the HTTP/1.1 check | `measure/` (`instr.py`, `run_one.py`, `simulate.py`, `conc_h11.py`, `out/E1-E5_*.json`); report `research/measure.md` |
| Logical-level runs, the call graph, request counts per host | `repofetch/` (`measure.py`, `h2test.py`, `out/*.json`); report `research/code-map.md` |
| HTTP/2 cause | `h2cause/` (`h2server.py`, `h2instr.py`, `ssl_race.py`, `run_net.py`, `out/`); report `research/fill1.md` |
| OSF limits, throttle simulation, our traffic | `osfrate/` (`rates.py`, `classes.py`, `sim/run_sim.py`, the osf.io, WaterButler and helm-charts clones); report `research/fill2.md` |
| OSF validators | `osfvalid/` (`probe_lib.py`, `step*.py`, `analyze*.py`, `ledger.jsonl`); report `research/fill3.md` |
| `filter[root]` against BFS, RF-4′ | `filterroot/` (`compare.py`, `order.py`, `rccheck.py`, `log.jsonl`); report `research/fill4.md` |
| Host mix over 2,454 papers | `hostmix/` (`tally.py`, `analyze.py`, `design_metrics.py`, `osf_probe.py`); report `research/fill5.md` |
| Upstream API behaviour, cache tooling | `apiresearch/`; report `research/upstream-apis.md` |
| Deployment modes, local tier benchmark | `deploy-modes/`; report `research/deployment.md` |
| bibr's caches | report `research/bibr-cache.md` (scienceverse/bibr 05990c5) |
| Contradictions between the reports and how they were settled | `research/critic.md`, answered by `research/fill1.md`-`fill5.md` |
