# Architecture

## Scope

Pytacheck is a JSON computation service for Metacheck's six validated default checks. It is not a
line-for-line R translation and does not ship an HTML renderer, Shiny application, archive clients,
or experimental/LLM modules. The production dependency graph contains Python, Pydantic, SciPy,
FastAPI, Uvicorn, and Prometheus instrumentation—no R runtime.

## Request and computation path

1. FastAPI accepts the Platform-compatible multipart request or the versioned native JSON envelope.
2. `BibrPaper` validates the external document while retaining compatible unknown fields.
3. `PaperContext.from_paper()` performs shared extraction once and freezes sentences, assembled
   paragraphs, p-values, equations, and APA statistics as cached tuples.
4. `CheckEngine` selects registered modules in caller/default order and passes every module the same
   immutable context.
5. Each module returns a typed `ModuleResult`; the engine isolates an unexpected exception as that
   module's `fail` result instead of losing the entire paper response.
6. The API observes request/module latency and returns native JSON. `report_html` remains empty.

The synchronous engine is intentionally small and deterministic. FastAPI dispatches it with
`anyio.to_thread.run_sync`, keeping the event loop responsive while modules run. Threads do not make
CPU work intrinsically faster; process workers or replicas provide throughput isolation when a
deployment outgrows one process. Production containers deliberately run one Uvicorn worker because
each application owns an in-process Prometheus registry. Scale with additional containers or pods so
each metrics endpoint remains internally coherent.

## Tolerant bibr ingress

Pytacheck owns a small consumer model rather than importing bibr's private implementation models.
Known fields needed by the checks are typed, additional fields are preserved, legacy 10.x version
metadata is normalized, and unsupported major versions fail explicitly. Contract fixtures cover
schema 10.2 and 10.6 so changes in either repository have an executable compatibility boundary.

This approach also keeps the core dependency set close to bibr's Python serving stack, avoiding a
second R runtime, Plumber process, cross-language serialization layer, and duplicated model-service
orchestration.

## Registry and failure isolation

Modules register immutable metadata and one `PaperContext -> ModuleResult` function. The registry
validates unknown or duplicate selections before work begins. The six validated defaults are an
explicit tuple, not whatever happens to be present in a directory.

Per-module timers surround only module computation. The benchmark separately measures a cold check,
warm checks on one validated paper, and an end-to-end path that repeats JSON decoding, singleton-info
normalization, `BibrPaper` validation, and checking. Its default profile is a deterministic generated
full-size synthetic bibr document; the small simulated R parity fixture is intentionally not used as
performance evidence. The result reports profile kind, serialized bytes, text rows, and text
characters so unlike workloads cannot be compared as if they were equivalent.

## Parity boundary

R remains authoritative for research behavior. `scripts/capture_r_oracle.R` loads an explicit
Metacheck checkout without attaching its startup hook, disables LLM use and provider credentials,
and captures normalized results for the six defaults. Its expected JSON records source/package
versions, hashes, module order, declared fields, null rules, and numeric tolerances.

The Python parity test compares fields, rows, and classifications directly. It does not use semantic
similarity or collapse disagreements into a score. R-only report markup is outside the initial JSON
boundary; deliberate omissions or Python provenance extensions must be written into the oracle
metadata rather than hidden in test code.

R and the source checkout are development-only. Neither is installed by the Python package nor copied
into the container.

## Reviewed Python production extensions

The frozen aggregate fixture remains exact R parity evidence. Outside that fixture, maintainers have
reviewed the following deliberate production-safety extensions rather than treating them as accidental
drift:

- bounded extraction and output budgets reject adversarial amplification;
- malformed, out-of-domain, and non-finite statistics are not accepted as valid findings;
- exact-zero lexical semantics distinguish a reported zero from an arbitrarily small nonzero value;
- starred-p-value note exemptions are match-local instead of suppressing unrelated matches; and
- partial omega coherence is active even though the corresponding branch in the frozen R source is
  currently unreachable.

Every extension needs focused tests, remains subject to the same normal aggregate R parity suite, and
should be proposed upstream when it represents a generally useful correction.

## Feeding discoveries back to Metacheck

Porting often reveals repeated extraction, ambiguous null behavior, scaling problems, or edge cases
that were difficult to see in the original execution path. A generally applicable discovery should
be handled in this order:

1. preserve the observed behavior in a minimal fixture;
2. decide with the Metacheck maintainers whether it is a bug, an intentional R contract, or a Python
   extension;
3. add the R regression and upstream the correction when appropriate;
4. recapture the versioned oracle and then update Pytacheck;
5. document any intentionally different production representation.

That loop allows new functionality to continue in the team's familiar R environment while the
optimized Python service follows validated behavior without freezing either codebase.
