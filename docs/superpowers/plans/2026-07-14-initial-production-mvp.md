# Pytacheck Initial Production MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a drop-in, JSON-only Python production engine for the six validated Metacheck modules, with no R runtime in the serving image and with a measurable parity and performance harness.

**Architecture:** Pytacheck owns a tolerant Pydantic consumer contract for bibr JSON and converts each request into an immutable `PaperContext` with request-local cached text features. Small, synchronous module functions run behind a registry and failure-isolating engine; FastAPI moves engine work off the event loop and scales across requests with normal ASGI workers. R is used only to generate and audit golden fixtures, never as a runtime dependency.

**Tech Stack:** Python 3.12-3.14, Pydantic 2, FastAPI, SciPy, python-multipart, Uvicorn, Prometheus client, Hatchling, uv, pytest, Ruff, mypy, Docker, GitHub Actions.

## Global Constraints

- The production install and Docker image must contain no R, Plumber, Quarto, or rpy2 dependency.
- The public compatibility route is `POST /paper/check`, accepting the multipart request currently sent by Platform.
- The preferred native route also accepts `application/json` without serializing the paper a second time.
- The canonical native route is `POST /v1/checks`; its API version is independent from the bibr paper schema version.
- The six defaults are exactly `power`, `marginal`, `stat_check`, `stat_effect_size`, `stat_p_exact`, and `stat_p_nonsig`.
- `report_html` is an empty string in this JSON-only milestone; report rendering is a separate consumer concern.
- A failing module returns a synthetic `traffic_light="fail"` result and does not fail the paper.
- Input models tolerate new bibr fields with `extra="allow"`; pytacheck must not import bibr private models.
- Shared extractions such as normalized text, paragraphs, p-values, equations, and APA tests are computed at most once per request.
- Ported Metacheck behavior remains licensed AGPL-3.0-or-later and records upstream provenance.
- Tests are written and observed failing before each production behavior is implemented.

---

## File Structure

- `pyproject.toml`: package metadata, runtime dependencies, development group, lint/type/test configuration, and CLI entry point.
- `pytacheck/models.py`: tolerant bibr request records and stable API result models.
- `pytacheck/context.py`: normalized paper text/section view and request-local cached feature extraction.
- `pytacheck/text.py`: regex search, paragraph assembly, p-value extraction, equation extraction, and APA-test extraction.
- `pytacheck/modules/base.py`: module protocol, metadata, registry, and registration decorator.
- `pytacheck/modules/*.py`: one validated check per file.
- `pytacheck/engine.py`: module selection, execution, timing, failure isolation, and aggregate response construction.
- `pytacheck/api.py`: FastAPI compatibility/native ingress, health/readiness, module catalogue, and metrics.
- `pytacheck/cli.py`: Uvicorn launcher and benchmark command.
- `tests/unit/`: narrow behavioral tests translated from upstream Metacheck tests.
- `tests/contract/`: bibr request and Metacheck-compatible response tests using frozen fixtures.
- `tests/parity/`: checked-in R-oracle outputs and field-aware comparison rules.
- `benchmarks/benchmark_default_modules.py`: warm/cold latency and throughput runner.
- `scripts/capture_r_oracle.R`: optional developer script that regenerates parity outputs from an adjacent Metacheck checkout.
- `Dockerfile`, `.dockerignore`: minimal non-root serving image.
- `.github/workflows/ci.yml`: lint, typing, test, package-build, and container-build gates.

---

### Task 1: Package and Test Baseline

**Files:**
- Create: `pyproject.toml`
- Create: `pytacheck/__init__.py`
- Create: `pytacheck/_version.py`
- Create: `tests/unit/test_package.py`
- Create: `.gitignore`
- Create: `LICENSE.md`
- Modify: `README.md`

**Interfaces:**
- Produces: `pytacheck.__version__: str`, initially `"0.1.0"`.
- Produces: a uv environment containing FastAPI, Pydantic, SciPy, python-multipart, Prometheus client, Uvicorn, pytest, Ruff, mypy, and build.

- [ ] **Step 1: Write the failing import test**

```python
def test_package_exposes_version() -> None:
    import pytacheck

    assert pytacheck.__version__ == "0.1.0"
```

- [ ] **Step 2: Run the test and verify RED**

Run: `uv run pytest tests/unit/test_package.py -q`

Expected: collection fails because `pytacheck` does not exist.

- [ ] **Step 3: Add package metadata and the minimal implementation**

```python
# pytacheck/_version.py
__version__ = "0.1.0"

# pytacheck/__init__.py
from pytacheck._version import __version__

__all__ = ["__version__"]
```

Configure Hatchling to package `pytacheck`, require Python `>=3.12,<3.15`, declare `AGPL-3.0-or-later`, and mirror bibr's Ruff rules and uv workflow where applicable.

- [ ] **Step 4: Sync and verify GREEN**

Run: `uv sync --all-groups && uv run pytest tests/unit/test_package.py -q`

Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock pytacheck tests README.md LICENSE.md .gitignore
git commit -m "chore: establish pytacheck package"
```

---

### Task 2: Tolerant Paper Contract and Shared Text Context

**Files:**
- Create: `pytacheck/models.py`
- Create: `pytacheck/context.py`
- Create: `pytacheck/text.py`
- Create: `tests/unit/test_context.py`
- Create: `tests/unit/test_text.py`
- Create: `tests/contract/fixtures/bibr_v10_2.json`
- Create: `tests/contract/fixtures/bibr_v10_6.json`

**Interfaces:**
- Consumes: bibr JSON with `paper_id`, `info`, `text`, `section`, `author`, `bib`, and `xref`; all other top-level and row fields are retained or ignored safely.
- Produces: `BibrPaper.model_validate(payload) -> BibrPaper` with `extra="allow"`.
- Produces: `PaperContext.from_paper(paper: BibrPaper) -> PaperContext`.
- Produces: `PaperContext.sentences`, `.paragraphs`, `.p_values`, `.equations`, and `.apa_tests` as cached tuples of JSON-safe dictionaries.
- Produces: `search_rows(rows, pattern, *, return_matches=False, ignore_case=True) -> tuple[dict[str, Any], ...]`.
- Produces: schema-version normalization that prefers `info.schema_version`, recognizes legacy `info.bibr_version="10.x"`, accepts known major version 10, and rejects an unsupported major.

- [ ] **Step 1: Write failing contract tests**

```python
def test_context_joins_sections_and_adds_paper_id(golden_payload: dict) -> None:
    context = PaperContext.from_paper(BibrPaper.model_validate(golden_payload))

    first = context.sentences[0]
    assert first["paper_id"] == golden_payload["paper_id"]
    assert first["header"] == golden_payload["section"][0]["header"]
    assert first["section_type"] == "title"


def test_unknown_bibr_fields_do_not_break_validation(golden_payload: dict) -> None:
    golden_payload["future_table"] = [{"new": "field"}]
    assert BibrPaper.model_validate(golden_payload).paper_id
```

- [ ] **Step 2: Run and verify RED**

Run: `uv run pytest tests/unit/test_context.py tests/unit/test_text.py -q`

Expected: imports fail because the models and context are absent.

- [ ] **Step 3: Implement the boundary and normalization**

Use these stable model shapes:

```python
class TextRecord(BaseModel):
    model_config = ConfigDict(extra="allow")
    text: str
    text_id: int
    paragraph_id: int | None = None
    section_id: int | None = None


class SectionRecord(BaseModel):
    model_config = ConfigDict(extra="allow")
    section_id: int
    header: str | None = None
    section_type: str | None = None


class BibrPaper(BaseModel):
    model_config = ConfigDict(extra="allow")
    paper_id: str
    info: dict[str, Any] = Field(default_factory=dict)
    text: list[TextRecord] = Field(default_factory=list)
    section: list[SectionRecord] = Field(default_factory=list)
    author: list[dict[str, Any]] = Field(default_factory=list)
    bib: list[dict[str, Any]] = Field(default_factory=list)
    xref: list[dict[str, Any]] = Field(default_factory=list)
```

Normalize sentence ordering by source order, exclude `section_type="references"` from default searches, assemble paragraphs by `(paper_id, section_id, paragraph_id)`, and retain the first row's location metadata. Run adapter tests against both Platform's historical v10.2 fixture and a current bibr v10.6 export; the legacy fixture is an ingress fixture, not a six-module success oracle.

- [ ] **Step 4: Implement shared extractors**

The p-value extractor must recognize exact operators and values used upstream:

```python
P_VALUE_PATTERN = re.compile(
    r"\bp-?(?:value)?\s*(?P<p_comp>[=<>~≈≠≤≥≪≫]{1,2})\s*"
    r"(?P<value>n\.?s\.?|\d?\.\d+)(?:\s*e\s*-\d+)?"
    r"(?:\s*[x*]\s*10\s*\^\s*-\d+)?"
)
```

Each match row contains the matched token as `text`, the whole sentence as `expanded`, `p_comp`, numeric-or-null `p_value`, and the original location fields.

- [ ] **Step 5: Run and verify GREEN**

Run: `uv run pytest tests/unit/test_context.py tests/unit/test_text.py -q`

Expected: all context and extraction tests pass.

- [ ] **Step 6: Commit**

```bash
git add pytacheck/models.py pytacheck/context.py pytacheck/text.py tests
git commit -m "feat: add tolerant bibr paper context"
```

---

### Task 3: Module Registry and Failure-Isolating Engine

**Files:**
- Create: `pytacheck/modules/__init__.py`
- Create: `pytacheck/modules/base.py`
- Create: `pytacheck/engine.py`
- Create: `tests/unit/test_engine.py`

**Interfaces:**
- Produces: `TrafficLight = Literal["na", "info", "red", "yellow", "green", "fail"]`.
- Produces: `ModuleResult(module, title, table, summary_table, summary_text, report, traffic_light)`.
- Produces: `register_module(metadata: ModuleMetadata) -> Callable[[ModuleFunction], ModuleFunction]`.
- Produces: `CheckEngine.check(paper: BibrPaper, modules: Sequence[str] | None = None) -> CheckResponse`.
- Produces: `DEFAULT_MODULES` in the exact upstream order.

- [ ] **Step 1: Write failing engine tests**

```python
def test_engine_contains_one_module_failure() -> None:
    engine = CheckEngine(registry={"broken": broken_module})
    response = engine.check(minimal_paper(), ["broken"])

    assert response.results["broken"].traffic_light == "fail"
    assert "boom" in response.results["broken"].report


def test_engine_rejects_unknown_modules() -> None:
    with pytest.raises(UnknownModuleError, match="missing"):
        CheckEngine().check(minimal_paper(), ["missing"])
```

- [ ] **Step 2: Run and verify RED**

Run: `uv run pytest tests/unit/test_engine.py -q`

Expected: imports fail because the engine is absent.

- [ ] **Step 3: Implement stable result models and registry**

```python
ModuleFunction = Callable[[PaperContext], ModuleResult]


@dataclass(frozen=True, slots=True)
class ModuleMetadata:
    name: str
    title: str
    description: str
    section: str
    validated: bool = True
```

The engine constructs one context, validates the full selection before starting, records `perf_counter()` duration per module in a non-compatibility `timings_ms` mapping, catches `Exception` per module, and preserves requested result order.

- [ ] **Step 4: Run and verify GREEN**

Run: `uv run pytest tests/unit/test_engine.py -q`

Expected: all engine tests pass.

- [ ] **Step 5: Commit**

```bash
git add pytacheck/modules pytacheck/engine.py tests/unit/test_engine.py
git commit -m "feat: add module registry and check engine"
```

---

### Task 4: Marginal and P-Value Modules

**Files:**
- Create: `pytacheck/modules/marginal.py`
- Create: `pytacheck/modules/stat_p_exact.py`
- Create: `pytacheck/modules/stat_p_nonsig.py`
- Create: `tests/unit/modules/test_marginal.py`
- Create: `tests/unit/modules/test_stat_p_exact.py`
- Create: `tests/unit/modules/test_stat_p_nonsig.py`

**Interfaces:**
- Consumes: `PaperContext.sentences` and `PaperContext.p_values`.
- Produces: registered `marginal(context)`, `stat_p_exact(context)`, and `stat_p_nonsig(context)` module functions.

- [ ] **Step 1: Translate upstream examples into failing tests**

```python
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("This effect was marginally significant (p = .065).", "red"),
        ("This effect approached significance (p = .34).", "red"),
        ("This effect is absent.", "green"),
    ],
)
def test_marginal_traffic_light(text: str, expected: str) -> None:
    assert marginal(context_for(text)).traffic_light == expected


def test_exact_flags_imprecise_and_zero() -> None:
    result = stat_p_exact(context_for("p < .05. A second result had p = .000."))
    assert result.traffic_light == "red"
    assert result.summary_table == [{"paper_id": "test", "n_imprecise": 1, "n_zero": 1}]


def test_nonsignificant_selects_values_above_alpha() -> None:
    result = stat_p_nonsig(context_for("p = .051; p = .049"))
    assert [row["p_value"] for row in result.table] == [0.051]
```

- [ ] **Step 2: Run and verify RED**

Run: `uv run pytest tests/unit/modules/test_marginal.py tests/unit/modules/test_stat_p_exact.py tests/unit/modules/test_stat_p_nonsig.py -q`

Expected: imports fail because the modules are absent.

- [ ] **Step 3: Implement the three modules**

Port the upstream patterns and semantics exactly. In particular, `stat_p_exact` flags `<` only when the numeric value is greater than `.001`, flags every comparator other than `=` and `<`, flags `n.s.`, excludes figure/table-note patterns such as `* p < .05`, and separately flags exact zero. `stat_p_nonsig` marks a p-value significant only when it is numeric, `<= .05`, and its comparator is `=` or `<`.

- [ ] **Step 4: Run and verify GREEN**

Run: `uv run pytest tests/unit/modules/test_marginal.py tests/unit/modules/test_stat_p_exact.py tests/unit/modules/test_stat_p_nonsig.py -q`

Expected: all translated examples pass.

- [ ] **Step 5: Commit**

```bash
git add pytacheck/modules tests/unit/modules
git commit -m "feat: port marginal and p-value checks"
```

---

### Task 5: Validated t/F Statcheck Module

**Files:**
- Create: `pytacheck/modules/stat_check.py`
- Create: `tests/unit/modules/test_stat_check.py`
- Create: `tests/parity/fixtures/statcheck_cases.json`

**Interfaces:**
- Consumes: `PaperContext.apa_tests`, containing raw text, test type, statistics, degrees of freedom, comparator, reported p, and source location.
- Produces: `stat_check(context) -> ModuleResult`, limited to validated t-tests and F-tests.
- Uses: `scipy.stats.t.sf` for two-tailed t-tests and `scipy.stats.f.sf` for F-tests.

- [ ] **Step 1: Write failing extraction and decision tests**

```python
def test_statcheck_flags_inconsistent_t_test() -> None:
    result = stat_check(context_for("t(97.2) = -1.96, p = 0.152"))
    assert result.traffic_light == "red"
    assert result.table[0]["raw"] == "t(97.2) = -1.96, p = 0.152"
    assert result.table[0]["error"] is True


def test_statcheck_accepts_consistent_t_test() -> None:
    result = stat_check(context_for("t(18) = 2.10, p = .050"))
    assert result.table[0]["error"] is False
```

- [ ] **Step 2: Run and verify RED**

Run: `uv run pytest tests/unit/modules/test_stat_check.py -q`

Expected: module import fails.

- [ ] **Step 3: Implement APA extraction and recomputation**

Normalize reported `p < x` as an interval decision, compare exact `p = x` using the rounding interval implied by its decimal places, expose `computed_p`, `error`, and `decision_error`, and preserve the raw matched substring. Do not claim support for chi-square, correlations, or z-tests in this milestone.

- [ ] **Step 4: Differentially verify the fixture**

Run: `uv run pytest tests/unit/modules/test_stat_check.py -q`

Expected: every checked-in R-oracle case matches on test type, raw token, error, decision error, summary counts, and traffic light.

- [ ] **Step 5: Commit**

```bash
git add pytacheck/modules/stat_check.py pytacheck/text.py tests
git commit -m "feat: add validated t and F statcheck"
```

---

### Task 6: Regex-Only Power Module

**Files:**
- Create: `pytacheck/modules/power.py`
- Create: `tests/unit/modules/test_power.py`

**Interfaces:**
- Consumes: `PaperContext.paragraphs`.
- Produces: `power(context) -> ModuleResult` with no model or network call.

- [ ] **Step 1: Write failing detector and classifier tests**

```python
def test_power_classifies_apriori_and_sensitivity() -> None:
    result = power(context_for_paragraphs(APRIORI_TEXT, SENSITIVITY_TEXT))
    assert [row["power_type"] for row in result.table] == ["apriori", "sensitivity"]
    assert result.summary_table[0]["power_n"] == 2
    assert result.traffic_light == "yellow"


def test_power_rejects_ordinary_use_of_word() -> None:
    result = power(context_for("I love to power pose."))
    assert result.table == []
    assert result.traffic_light == "na"
```

- [ ] **Step 2: Run and verify RED**

Run: `uv run pytest tests/unit/modules/test_power.py -q`

Expected: module import fails.

- [ ] **Step 3: Implement the validated regex path**

Require all three gates used upstream: the standalone word `power`, at least one power-analysis phrase, and at least one number that is not a four-digit year. Classify in precedence order: a-priori, sensitivity, compromise, post-hoc/retrospective, unknown. Set `complete` and `power_complete` to null because this validated path does not use an LLM.

- [ ] **Step 4: Run and verify GREEN**

Run: `uv run pytest tests/unit/modules/test_power.py -q`

Expected: translated upstream detector cases pass.

- [ ] **Step 5: Commit**

```bash
git add pytacheck/modules/power.py tests/unit/modules/test_power.py
git commit -m "feat: port validated power detector"
```

---

### Task 7: Effect-Size Presence and Coherence Module

**Files:**
- Create: `pytacheck/modules/stat_effect_size.py`
- Create: `tests/unit/modules/test_stat_effect_size.py`
- Create: `tests/parity/fixtures/effect_size_cases.json`

**Interfaces:**
- Consumes: `PaperContext.equations` grouped by paper and sentence.
- Produces: `stat_effect_size(context) -> ModuleResult` for t-tests, F-tests, Cohen's d variants, partial eta squared, eta squared, and partial omega squared.

- [ ] **Step 1: Write failing presence and coherence tests**

```python
def test_missing_effect_size_is_red() -> None:
    result = stat_effect_size(context_for("t(28) = 2.40, p = .023"))
    assert result.traffic_light == "red"
    assert result.summary_table[0]["ttests_without_es"] == 1


def test_equal_group_cohens_d_matches() -> None:
    result = stat_effect_size(context_for("t(38) = 2.00, d = .63"))
    assert result.table[0]["d_coherence"] == "match_under_assumptions"
    assert result.table[0]["d_coherence_assumption"] == "independent_equal_n"


def test_partial_eta_matches_f_statistic() -> None:
    result = stat_effect_size(context_for("F(1, 38) = 4.00, partial eta squared = .095"))
    assert result.table[0]["eta_coherence"] == "match_under_assumptions"
```

- [ ] **Step 2: Run and verify RED**

Run: `uv run pytest tests/unit/modules/test_stat_effect_size.py -q`

Expected: module import fails.

- [ ] **Step 3: Implement equation pairing and formulas**

For each sentence, pair tests and effect sizes positionally only when there are multiple tests and exactly the same number of effect sizes; otherwise attach the sentence's complete effect-size list to every test. Apply the upstream tolerance `0.01` and formulas:

```python
d_paired_dz = abs(t_value) / sqrt(df + 1)
d_paired_drm_r05 = d_paired_dz / sqrt(1 - 0.5)
d_independent_equal = 2 * abs(t_value) / sqrt(df + 2)
partial_eta = (df1 * abs(f_value)) / (df1 * abs(f_value) + df2)
partial_omega = (df1 * (abs(f_value) - 1)) / (df1 * abs(f_value) + df2 + 1)
```

Non-integer t degrees of freedom are indeterminate; plain eta squared is indeterminate; Cohen's f is not reconstructed; partial omega below zero matches a reported zero.

- [ ] **Step 4: Run and verify GREEN**

Run: `uv run pytest tests/unit/modules/test_stat_effect_size.py -q`

Expected: all unit and R-oracle cases match on traffic light, counts, and coherence classifications.

- [ ] **Step 5: Commit**

```bash
git add pytacheck/modules/stat_effect_size.py tests
git commit -m "feat: port effect-size coherence check"
```

---

### Task 8: FastAPI Compatibility and Native JSON API

**Files:**
- Create: `pytacheck/api.py`
- Create: `pytacheck/cli.py`
- Create: `tests/contract/test_api.py`

**Interfaces:**
- Produces: `create_app(engine: CheckEngine | None = None) -> FastAPI`.
- Produces: `GET /health`, `GET /ready`, `GET /paper/modules`, `POST /paper/module`, `POST /paper/check`, `POST /v1/checks`, and `GET /metrics`.
- `POST /paper/check` accepts either multipart `file`, `modules`, `report` or an `application/json` body plus optional query `modules`.

- [ ] **Step 1: Write failing API contract tests**

```python
def test_platform_multipart_contract(client: TestClient, golden_bytes: bytes) -> None:
    response = client.post(
        "/paper/check",
        files={"file": ("paper.json", golden_bytes, "application/json")},
        data={"modules": "marginal,stat_p_exact", "report": "false"},
    )
    assert response.status_code == 200
    assert response.json()["modules_run"] == ["marginal", "stat_p_exact"]
    assert response.json()["report_html"] == ""


def test_native_json_contract(client: TestClient, golden_payload: dict) -> None:
    response = client.post(
        "/v1/checks",
        json={"paper": golden_payload, "modules": ["power"], "render_html": False},
    )
    assert response.status_code == 200
    assert list(response.json()["results"]) == ["power"]
```

- [ ] **Step 2: Run and verify RED**

Run: `uv run pytest tests/contract/test_api.py -q`

Expected: app import fails.

- [ ] **Step 3: Implement ingress and off-event-loop execution**

Parse and validate the entire module selection before reading/processing the paper where possible. Use `await anyio.to_thread.run_sync(engine.check, paper, selection)` so CPU work never blocks FastAPI's event loop. Return `{ "error": message }` for compatibility 400 responses, preserve Pydantic 422 errors for invalid JSON structure, and expose Prometheus counters/histograms labeled only by module and outcome.

- [ ] **Step 4: Run and verify GREEN**

Run: `uv run pytest tests/contract/test_api.py -q`

Expected: multipart and native JSON contracts pass.

- [ ] **Step 5: Commit**

```bash
git add pytacheck/api.py pytacheck/cli.py tests/contract/test_api.py
git commit -m "feat: expose metacheck-compatible FastAPI service"
```

---

### Task 9: Parity, Performance, Deployment, and Contribution Workflow

**Files:**
- Create: `tests/parity/test_r_oracle.py`
- Create: `tests/parity/fixtures/to_err_is_human.json`
- Create: `tests/parity/fixtures/to_err_is_human.expected.json`
- Create: `scripts/capture_r_oracle.R`
- Create: `benchmarks/benchmark_default_modules.py`
- Create: `tests/performance/test_default_modules.py`
- Create: `Dockerfile`
- Create: `.dockerignore`
- Create: `.github/workflows/ci.yml`
- Create: `CONTRIBUTING.md`
- Create: `docs/architecture.md`
- Modify: `README.md`

**Interfaces:**
- Produces: `python -m benchmarks.benchmark_default_modules --fixture ... --iterations 20` JSON containing cold latency, warm p50/p95, papers/second, and per-module timing.
- Produces: an optional R-oracle capture command that reads an adjacent Metacheck checkout and writes normalized JSON fixtures.
- Produces: a non-root image launched as `pytacheck serve --host 0.0.0.0 --port 2005`.

- [ ] **Step 1: Write failing aggregate parity and performance smoke tests**

```python
def test_all_six_defaults_return_without_failure(to_err_is_human: BibrPaper) -> None:
    response = CheckEngine().check(to_err_is_human)
    assert response.modules_run == list(DEFAULT_MODULES)
    assert all(result.traffic_light != "fail" for result in response.results.values())


def test_warm_default_check_stays_under_smoke_budget(to_err_is_human: BibrPaper) -> None:
    engine = CheckEngine()
    engine.check(to_err_is_human)
    started = perf_counter()
    engine.check(to_err_is_human)
    assert perf_counter() - started < 2.0
```

- [ ] **Step 2: Run and verify RED for missing deployment/performance pieces**

Run: `uv run pytest tests/parity tests/performance -q`

Expected: missing fixtures or aggregate behavior causes failure.

- [ ] **Step 3: Add field-aware parity normalization**

Require exact equality for module names, traffic lights, summary counts, extracted raw tokens, and coherence/error classifications. Compare floating recomputations with `abs_tol=1e-10`; normalize null/absent optional columns and row ordering explicitly. Never reduce the gate to a single similarity score.

- [ ] **Step 4: Add benchmark, container, CI, and contributor docs**

CI runs `uv lock --check`, `ruff check`, `ruff format --check`, `mypy pytacheck`, `pytest`, `python -m build`, and a Docker build. The README documents the R-lab/Python-production ownership model: new R modules are ported only after validation, using checked-in oracle fixtures; experimental R modules need not exist in pytacheck.

- [ ] **Step 5: Run full verification**

Run:

```bash
uv lock --check
uv run ruff check .
uv run ruff format --check .
uv run mypy pytacheck
uv run pytest -q
uv run python -m build
docker build -t pytacheck:local .
uv run python -m benchmarks.benchmark_default_modules --iterations 20
```

Expected: every command exits zero; the benchmark reports the measured figures without substituting estimates.

- [ ] **Step 6: Commit**

```bash
git add .github Dockerfile .dockerignore README.md CONTRIBUTING.md docs scripts benchmarks tests
git commit -m "build: add parity performance and deployment gates"
```

---

## Self-Review

- Spec coverage: the plan covers all six validated defaults, bibr schema tolerance, shared extraction, failure isolation, Platform multipart compatibility, native JSON, no production R, parity, benchmarks, metrics, CI, and deployment.
- Scope boundary: HTML/Quarto reports, Shiny, archive importers, experimental modules, and direct causal-model serving are intentionally excluded from this first independently deployable milestone.
- Type consistency: all modules consume `PaperContext` and return `ModuleResult`; the engine accepts `BibrPaper` and returns `CheckResponse`; API and benchmarks use those same interfaces.
- Placeholder scan: implementation choices, formulas, commands, expected outcomes, and acceptance behavior are specified without deferred decisions.
