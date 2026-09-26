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

`patches/` keeps two prototypes the plan builds on:

- `synth-stack-14.patch`: 14 verified optimisations stacked (accuracy run
  34.0 s to 15.5 s, all 439 outputs identical); CORE-0 lands them.
- `spike-1.patch`: the spike of the new core (`src/pytacheck/core/`) and
  four modules rewritten on it, against commit 844360d; SPIKE-2 and
  CORE-1b build on it.
