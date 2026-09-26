# Design proposals

Working documents for the architecture-first rewrite. They are proposals
awaiting decisions, not descriptions of the shipped code. (Earlier designs:
[module-system-v2.md](module-system-v2.md), [improvements.md](improvements.md).)

- [ARCHITECTURE.md](ARCHITECTURE.md): the indexed-document core, the
  rewrite plan (work packages, gates, schedule) and the decisions that
  need the maintainer (section 6). Revision 2, after a measured spike.
- [PERF_REPORT.md](PERF_REPORT.md): where the time goes today, R against
  Python per module, and the verified optimisation prototypes.
- [BATCH_DESIGN.md](BATCH_DESIGN.md): batch processing (fixed costs,
  network and LLM batching, a batch runner with resumable output).

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
Next: SNAP, HARNESS and SPIKE-2 (ARCHITECTURE.md, section 4).

`patches/` keeps the prototype the plan builds on:

- `spike-1.patch`: the spike of the new core (`src/pytacheck/core/`) and
  four modules rewritten on it, against commit fd5e6f3; SPIKE-2 and
  CORE-1b build on it.
