# Repository fetching and reference checks, 12 October 2026

The changes overlap independent OSF project listings and unknown file-size
probes, deduplicate metadata requests within a listing (including work already
in flight), persist complete public recursive OSF trees briefly, and reuse
extracted ZIP members after checking their size and CRC. Reference joins are
shared within a trusted run, for single papers and whole paper lists, and
reference matching always uses RapidFuzz's native edit distance.

Requests retain the shared retry, rate-limit and cancellation behavior.
Nested fetch pools share a request limit of four per api.osf.io origin and
eight per other host. Failed requests and incomplete trees are not cached.
With `cache=True`, public OSF trees live on disk for five minutes; set
`metacheck.osf.cache_ttl` (seconds) to change that lifetime, or zero to disable
tree reuse. Private and view-only trees are never persisted. Per-ID metadata
continues using its existing cache.

## Measurements

Measured locally on macOS with Python 3.12, pandas 3.0.6 and the pinned upstream
fixtures. These timings are not directly comparable with the older Linux
measurements in PERF_REPORT.md.

| Workload | Before | After | Result |
| --- | ---: | ---: | --- |
| Warm report-style runs: 19 checks on each of 21 papers, four checks on each of 10 local repositories, including reading inputs | 3.063 s | 2.771 s | 9.5% less time |
| Nine OSF project inputs, eight distinct; controlled 40 ms latency per listing, real scheduling and row assembly | 0.410 s | 0.095 s | about 4.3× faster; nine result rows preserved |

The offline timings are medians of six alternating measurements per variant,
after a warm-up pair, in one process. The comparison restores the old reference
join and Python edit-distance implementation for the before variant. All 14
runs had the same canonical output fingerprint across the 439 outputs,
including the existing failures on malformed fixtures. The separate full
accuracy harness also produced the exact same fingerprint as before the changes.

The listing measurement is simulated latency, not a live-host benchmark.
Tests separately verify the shared host cap, request deduplication, cancellation,
credential separation, expiry across process-memory resets, retries after failed
or partial listings, and ZIP corruption repair.

## Validation limits

The full accuracy gate reports the same two pre-existing macOS skewness
differences before and after this change: `repos/qualtrics` and
`repos/spreadsheets`, at magnitudes around 1e-14 and 1e-16. No expected-output
entries or numerical tolerances were changed to hide these differences.
