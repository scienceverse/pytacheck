# Design proposals

Working documents for the architecture-first rewrite. They are proposals
awaiting decisions, not descriptions of the shipped code. (Earlier designs:
[module-system-v2.md](module-system-v2.md), [improvements.md](improvements.md).)

- [ARCHITECTURE.md](ARCHITECTURE.md): the indexed-document core, the
  rewrite plan (work packages, gates, schedule) and the decisions that
  need the maintainer (section 6). Revision 3: a structural re-plan on
  top of revision 2 (the measured spike), built on the two proposals below.
- [FIDELITY.md](FIDELITY.md): what pytacheck must keep exactly equal to R
  (results) and what it may change (presentation), how deviations are
  recorded, and the canonicaliser that makes this checkable.
- [ECOSYSTEM.md](ECOSYSTEM.md): validation status and presets, the module
  store as a marketplace, and porting R modules (licences and consent).
- [PERF_REPORT.md](PERF_REPORT.md): where the time goes today, R against
  Python per module, and the verified optimisation prototypes.
- [BATCH_DESIGN.md](BATCH_DESIGN.md): batch processing (fixed costs,
  network and LLM batching, a batch runner with resumable output).
- [REPO_FETCH.md](REPO_FETCH.md): repository fetches (where the ~40 s
  goes, batching per host, OSF's rate limits), a cache store across runs
  with an optional Redis/Valkey tier, and why it is not shared with bibr.

Paths under `scratchpad/` in these documents refer to the session's
working directory where the measurements were taken; the evidence that
matters is summarised in the documents themselves.

**Progress.** CORE-0 has landed: the 14 verified optimisations of
`synth-stack-14.patch` (kept in the history, commit ed6e830), the review's
fixes before landing (H-1's guard compares papers by value; RetractionWatch's
cached frame stays private; the memo token is used only while the paper holds
the table it built) and C-3's shared matcher (`_r.regex.detector()`). The
accuracy run's Python side takes 16.9 s instead of 34.7 s (minimum of 5
interleaved runs), all 439 outputs byte-identical.
Next: phase A of revision 3 (SNAP, HARNESS, SPIKE-2, TIERS; ARCHITECTURE.md,
section 4.4), once the maintainer has decided the rest of section 6.
Decisions 1, 3-11, 13 and 14 are decided (maintainer, 2026-09-27), all as
recommended except 3, which takes (b). The rest remain open.

`patches/` keeps the prototype the plan builds on:

- `spike-1.patch`: the spike of the new core (`src/pytacheck/core/`) and
  four modules rewritten on it, against commit fd5e6f3; SPIKE-2 and
  CORE-1b build on it.
