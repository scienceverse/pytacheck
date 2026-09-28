# Pytacheck ecosystem: validation status, presets, the module store and R ports

**Status.** Draft 2 (2026-09-27; draft 1 was 2026-09-26). Proposed; its decisions (§9) are all decided (maintainer, 2026-09-27). Of §6's store fixes, rows 1, 2, 3 and 5 are partly done, through pytacheck-modules#1 (merged 2026-09-27) and pytacheck-modules#2 (open), and pytacheck's scaffold names row 2's interim pin. Nothing else here is implemented, in pytacheck or in the store repository `scienceverse/pytacheck-modules`. It extends `docs/design/module-system-v2.md`, whose deferred item "multi-level trust tiers, a policy engine" (`:592`) it brings back as data, not as a policy engine. Legal statements in §5 are an engineering assessment, not legal advice.

**Evidence markers.** As in FIDELITY.md: **(M)** measured in this session (upstream metacheck at the harness pin; pytacheck at f559c1be; the store at 1005d6b, and at 23736aa for §1.4 and §6), **(E)** an estimate with its basis. The research notes behind this document are in `/tmp/claude-0/-home-user-pytacheck/b01f2e9f-255f-5b7e-9d0d-70c9d055b185/scratchpad/research/results/` (`tiers.md`, `market-gap.md`, `market-research.md`, `porting.md`).

---

## 0. Summary

Four questions, one answer each.

| Question | Answer | Where |
|---|---|---|
| **Who says a module is accurate?** | The metacheck validation team, in one registry file they own. Module authors cannot grant it; their own numbers show as "self-reported". A certification is bound to the exact released version (built-ins) or code digest (packs) it measured, and is renewed by a rerun at each release (§2.5) | §2 |
| **What does a user run?** | A **preset** (what to check, often per field) filtered by a **status policy** (how much evidence to require): `validated` ⊂ `certified` ⊂ `experimental` ⊂ `all`. The library defaults to `experimental`, which also runs the packs the user installed; a public server defaults to `validated` | §3 |
| **Where do modules come from?** | Authors' own repositories. The store is a reviewed list of pins with a generated catalog, like R-universe or a coding-agent marketplace, plus the one thing those lack: a measured-accuracy label | §4 |
| **How does the R community contribute?** | They write R, as they do now, and their modules are listed as port candidates. `pytacheck port` translates an R module with the author's recorded consent and licence check, and a differential check against R decides whether the port is faithful | §5 |

**Two labels, never merged.**
- **Trust** says where the code came from and whether it is safe to run (builtin, store, unlisted, local). It exists today.
- **Status** says how accurate its flags are, as measured by the team (validated, external-validated, experimental, unvalidated, withdrawn). It is new.

The store's code review (`reviewed`) belongs to trust, not status. That is why the status label for an external module without a certification is `unvalidated`, not "unreviewed": a reviewed store pack is still unvalidated.

A reviewed store pack can be experimental, and a validated module is still subject to the install consent card.

**Cost (E).** About 1,800-1,900 new source lines: status and store ≈ 560-660 (§4.12), `port/` ≈ 1,260 (§5.2). Against that, 850-1,100 source lines and about 700 test lines can go from `packs/` (§4.11). `packs/` and status together end ≈ 190-540 lines smaller than today; `src/` as a whole grows by ≈ 700-1,100 lines, all of it in `port/`. These are the figures ARCHITECTURE.md §5.3's line ledger should carry. In agent-days: TIERS 2.5, STORE-2a 1.5, STORE-2b 1.5-2, PORT 5, and DECL 3 for YAML modules (§8), from ARCHITECTURE.md decision 2 (decided 2026-09-27: (a)). DECL's ≈ +380-460 net lines are in ARCHITECTURE.md §5.3, not in the figures above.

**Outside pytacheck.** Most of §2, §3.4 and §7 needs work or agreement from the metacheck team. §8.1 lists those asks, the date each is needed by, and what pytacheck does if the answer is no or not yet.

---

## 1. Evidence

### 1.1 Which modules have validation numbers

The example report (`docs/report-example.qmd` at 8cc2132d in upstream; the rendered site could not be checked from this sandbox) prints the `<validation>` block of each module verbatim (`R/report.R:515-520`). Six of those blocks carry counts. PPV and sensitivity below are derived from the counts in the text **(M)**.

| Module | Block | Evidence as written | PPV / sensitivity | Validator named |
|---|---|---|---|---|
| power | `power.R:11` | 128 papers; TP 203, FP 21, FN 22 | 0.906 / 0.902 | "Metacheck team" (`:9`) |
| stat_p_exact | `stat_p_exact.R:13` | 225 papers; TP 269, FP 78, FN 136, TN 4,557 | 0.775 / 0.664 | none |
| stat_p_nonsig | `stat_p_nonsig.R:13` | 194 papers; TP 1,486, FP 153; FN not stated | 0.907 / ≈ 0.928 | none |
| marginal | `marginal.R:11` | 51 papers; TP 38, FP 22, FN 27 | 0.633 / 0.585 | none |
| stat_effect_size | `stat_effect_size.R:11` | 161 papers; TP 1,106, TN 295, FN 23, FP 45 | 0.961 / 0.980 | "Metacheck team" (`:7`) |
| stat_check | `stat_check.R:15` | 685 tests; TP 34, FP 26, FN 0 | 0.567 / 1.000 | Nuijten et al. 2017, i.e. **external** |
| ref_accuracy | `ref_accuracy.R:25` | rates only: flags 8% (3% with the stricter setting) of genuine references; catches 96-100% of corrupted ones; 10 papers from 5 fields | – | none; added 2026-08-09, not in the example report |
| data_check | `data_check.R:43` | "has not been validated" | – | – |

**The other 22 modules** **(M)**:
- 8 are in the Shiny app's `validated_modules` list with no evidence (prereg_check, ethics_check, ref_pubpeer, ref_retraction, ref_replication, repo_check, codebook_check, code_check). code_check says only that it "was validated internally" (`code_check.R:9`).
- 4 point to another tool's validation (coi_check, coi_check_oi, funding_check, funding_check_oi cite rtransparent's paper, which validates rtransparent, not metacheck's adaptation). stat_check (above) is the same case: its counts measure the R statcheck package, while metacheck keeps only t- and F-tests (`stat_check.R:50-54`) and pytacheck's stat_check is a Python reimplementation. The rule used here: **evidence counts only if it measured this implementation.**
- 10 have no mention.

### 1.2 Questions for the validation team

These decide the launch list (§3.1). Pytacheck should record the team's answers, not infer them.

- **Q1. Which modules are the "around 4"?** Five have team-run count validations (power, marginal, stat_p_exact, stat_p_nonsig, stat_effect_size). The choice is 5, or 4 without one of them (for example marginal, at PPV 0.63). stat_check's numbers come from the statcheck authors, so under the rule above it is not in the count until the team reruns it on the port.
- **Q2. The Shiny list is a server list, not a validation list.** Its 16 modules (`inst/app/report_app.R:162-166`, `shiny_app/app.R:236-241`) include 9 without evidence and data_check, which says it is not validated. pytacheck copies it as `metacheck::validated` (`src/pytacheck/modules/pack.json:66-86`).
- **Q3. "Instances" means three things** across the blocks. The template defines sensitivity as TP/instances (`inst/templates/_module.R:12-18`): 0.825 for power, against 0.902 from TP/(TP+FN). Structured counts remove the ambiguity.
- **Q4.** stat_p_nonsig's FN is implied, not stated.
- **Q5. Field scope.** Nearly all the evidence comes from Psychological Science. Does "validated" mean "validated for psychology"?
- **Q6. Lookup modules** (ref_retraction, ref_pubpeer, ref_replication) mostly report what a database says. Do they need a count validation, or a metrics-based one like ref_accuracy's?
- **Q7. Settings.** power can run with or without an LLM. Which setting do its numbers describe, and which model id and prompt?
- **Q8. Corpora.** Where does each validated module's coded, full-paper ground truth live, and can it be shared? The per-release rerun (§2.5) needs it. Upstream metacheck ships none (only `R/validate.R`). It must be answered before the first non-provisional snapshot (§2.3); TIERS merges without it.

### 1.3 What pytacheck has today

- **Self-declared numbers look certified.** `ModuleSpec.validation` (`module.py:102-103`) is set by the author; none of the 30 built-ins sets it. `validation_metrics` derives PPV and sensitivity (`packs/manifest.py:170-180`), `store build` copies them into the index (`packs/build.py:116`), and `store search` prints "validated k/n" (`packs/stores.py:529-605`) whoever wrote them.
- **`pack check` tests presence only.** It warns "unvalidated" when there is no block (`packs/check.py:149-162`).
- **Trust labels cover provenance only** (`manifest.py:184-191`).
- **Reusable:** tree-bound reviews (`reviewed_tree_sha256`, `build.py:149-183`) and `Selection.dropped` (`presets.py:103-138`, `:496-504`). `dropped` holds refs only, documented as the refs left out because offline was on, and `cli.py:173-209` prints it as "Offline: skipped".
- **`report()` has no `preset=`.** It hard-codes `DEFAULT_MODULES` (`report/report.py:61-78`, a copy of the default preset). The API falls back to every module when none is configured (`api/app.py:184-195`).
- **`init` asks for fields and presets, never for strictness** (`cli.py:685-713`).

### 1.4 The real store today

`scienceverse/pytacheck-modules` at 23736aa (main, after pytacheck-modules#1 merged on 2026-09-27) **(M)**:
- **Two packs.** `clinical_trials` (MIT, one module `trial_registration`, with tests) and `fields` (CC0-1.0, presets only: `general`, `psychology`, `medicine`, `open-science`).
- **The store repository is private too.** Its README says: "While this store is private, pytacheck needs read access to it: set PYTACHECK_GITHUB_TOKEN (or GH_TOKEN / GITHUB_TOKEN)". Every index.json entry points at `scienceverse/pytacheck-modules`. So today every store user goes through the token fetch chain in `stores.py:223-295`, and outside researchers cannot fork it to contribute.
- **CI passes.** pytacheck-modules#1 (merged 2026-09-27) moved `check.yml` to a self-hosted runner and gave `PYTACHECK_READ_TOKEN` only to the two "Install pytacheck" steps (`check.yml:86-87`, `:150-151`), as an HTTP header scoped to `scienceverse/pytacheck`. It is no longer in the workflow-level `env`. The secret is set, and every run since 09:11 UTC on 2026-09-27 passed, including the push to main after the merge and pytacheck-modules#2. The `check` job still runs pack code and tests with `contents: read` (§6 item 5), and it skips PRs from forks (`check.yml:59-61`), so an outside contributor gets no CI run.
- **Main still pins a stale feature branch.** `claude/pytacheck-metacheck-fork-0x7q73` (6cec4b56; pytacheck's main is 8d8a2364, the initial commit only) is named at `check.yml:39`, `README.md:16,37` and `CONTRIBUTING.md:9,16`. pytacheck-modules#2 (open; `ci/repin-pytacheck` at 160a27e) re-pins all five to `claude/elegant-fermat-eo7s89`, the head of `scienceverse/pytacheck#1`, and says to switch to `@main` once that pull request merges. pytacheck's own `packs/scaffold.py:24-26` now writes the same branch into every new pack. It is still a constant, not derived from the installed version.
- **index.json is current.** 40cc467 (in pytacheck-modules#1) rebuilt it: generated at 2026-09-27T13:19:18Z, it lists clinical_trials 0.2.0 and fields 0.1.1, as the pack.json files say. The index job's run on main after the merge found it up to date.
- **Licence wording.** `REVIEW.md:22` and `CONTRIBUTING.md:23` allow CC0-1.0 for packs, while `pack check` asks for "an OSI-approved licence" (`check.py:284`). CC0 is not OSI-approved but is GPL-compatible, so the two rules disagree. The store should ask for a licence on the §5.4 list instead.
- **The medicine preset says** "add the clinical_trials pack" in prose, because pack.json cannot declare a pack dependency (§4.10).
- **No `packs/*.json` entries yet.** Both packs are folders in the store repository; no pack is pinned from an author's repository.

---

## 2. Validation status

### 2.1 Labels

The registry stores certifications and withdrawals only. Every label is derived from them; no record stores a label.

| Label | Derived as |
|---|---|
| `validated` | a team module with a certification that matches this code |
| `external-validated` | a module from outside the team with a certification that matches this code |
| `experimental` | a team module with no certification, or a stale one |
| `unvalidated` | a module from outside the team with no certification (local, unlisted and store packs alike, reviewed or not) |
| `withdrawn` | a module the registry lists under `withdrawn`, for a known problem. Any module can be withdrawn, certified or not |

A **team module** is one whose pack is listed in the registry's `team_packs` (`metacheck`, plus any official scienceverse packs). Team or external is an attribute of the module, and the badge says it ("External module, validated by the metacheck team"); the two validated labels differ only in that attribute.

**Validated does not mean accurate.** It means the error rates are measured and published. marginal would be validated at PPV 0.63. The badge always shows the numbers (§3.6), so users judge the rate, not the word.

### 2.2 Policies

A policy is a named set of labels, defined in code, not in the registry. A refreshed registry (§2.3) can change which modules are certified, never what `--status validated` means.

| Policy | Accepts |
|---|---|
| `validated` | validated |
| `certified` | validated, external-validated |
| `experimental` | certified + experimental, plus modules from packs the user installed or path-pinned (§3.4) |
| `all` | experimental + unvalidated |

`withdrawn` is never picked up from a preset. A comma-separated list of labels is also accepted, for anything the ladder does not cover.

This covers the three groups asked for: the team's validated modules, the team's experimental superset, and external modules the team has validated.

### 2.3 The registry

- **One file, `validation.json`,** in a repository the team owns, for example `scienceverse/metacheck-validation`.
  - CODEOWNERS is the validator group; every merge needs one validator's approval.
  - CI checks the schema, recomputes PPV and sensitivity from the counts, and checks that the referenced versions and digests exist.
- **Labels come from this file only.** The shipped snapshot, or the refresh below, is the one source. Packs carry only self-reported `validation=` numbers (§2.7), never a status. Store indexes and the catalog copy status for display; the snapshot or refresh wins.
- **Both packages ship a snapshot at release:** `src/pytacheck/resources/status/validation.json` and metacheck's `inst/validation.json`. The pytacheck snapshot changes only in a PR that names the registry commit it copies.
- **An optional refresh:** `"validation_registry": "<url>" | "none"`, cached like the store index. It is read from user config or `PYTACHECK_VALIDATION_REGISTRY` only, never from project config (§4.8). A server ignores it unless the operator sets it. If the registry is unreachable, the snapshot is used; a run never fails because of it.
- **The run record stores the registry digest**, so a report says which evidence it was judged against.

**Bootstrap.** The registry repository does not exist yet, and Q1-Q8 are open. Until the team answers (§8.1):
- TIERS ships a snapshot built from the §3.1 draft, with every entry marked `"provisional": true`.
- A provisional entry derives as validated for policy purposes, but its badge reads "Provisional: based on the metacheck team's published counts (R version), not yet certified by the team". pytacheck does not certify on the team's behalf.
- The TIERS gate checks the snapshot's schema and that every built-in has an entry, not the certifications themselves.
- The first registry commit from the team replaces the provisional snapshot.

### 2.4 The evidence record

```json
{
  "schema": 1,
  "team_packs": ["metacheck"],
  "modules": {
    "metacheck::power": {
      "implementations": {
        "r":  {"metacheck": ["0.3.1"]},
        "py": {"pytacheck": ["0.4.0"], "method": "rerun", "run": "<CI run URL>"}
      },
      "evidence": [{
        "id": "power-2026-06",
        "validators": ["<name>", "<name>"],
        "date": "2026-06-05",
        "unit": "power-analysis statement",
        "corpus": {"source": "Psychological Science", "papers": 128,
                   "fields": ["psychology"], "sampling": "<how the papers were chosen>",
                   "labels": "<OSF or DOI of the coded ground truth>"},
        "counts": {"tp": 203, "fp": 21, "fn": 22, "tn": null},
        "settings": {"llm": "<as validated, Q7>", "settings_sha256": "<model id, prompt, schema>"},
        "server_safe": true,
        "protocol": "<OSF or DOI>",
        "summary": "In a sample of 128 papers …"
      }]
    },
    "metacheck::ref_accuracy": {
      "evidence": [{"id": "refacc-2026-08",
                    "metrics": {"flag_rate_genuine": 0.08, "recall_corrupted": 0.96},
                    "corpus": {"papers": 10, "fields": ["general"]}}]
    },
    "janedoe-psych::apa_df": {
      "implementations": {"py": {"code_sha256": ["<64 hex>"], "version": "1.2.0",
                                 "pytacheck": ["0.4.0"]}},
      "evidence": [{"id": "apa-df-2027-01", "counts": {"tp": 210, "fp": 21, "fn": 40}}]
    }
  },
  "withdrawn": {"<pack::module>": {"date": "<date>", "reason": "<known problem>"}}
}
```

- **No stored labels.** `power` derives as validated because it is in a team pack; `apa_df` derives as external-validated because it is not.
- **PPV and sensitivity are never stored.** They are derived from `counts` (reusing `validation_metrics`), so they cannot disagree with them (Q3).
- **`metrics`** carries evidence that is not count-based, such as ref_accuracy's. It is only an example of the form: ref_accuracy is a candidate, not certified (§3.1, Q6).
- **`settings`** records how the module was configured when measured. For an LLM module it holds a digest of the model id, prompt and response schema. A run with other settings shows the status with a note ("validated without an LLM"), and a server under `validated` runs it only with the certified settings.
- **`server_safe`** is set by the team after a timed run on the server's limits, and it is the one source for "server-safe" in both packages (§3.4, §7 item 4). With the scienceverse Platform on the public side (the maintainer, 2026-09-27), the server whose limits count is the Platform's (§3.4, §8).

### 2.5 Binding a certification to code

A certification holds only for the code it measured. There is one binding per kind, stated here once.

| Implementation | Bound by | When the code changes |
|---|---|---|
| pytacheck built-ins | a list of exact pytacheck releases, plus `method: "rerun"` | a release is added only after a rerun on its release candidate (below). There is no range and no open upper bound |
| store and external packs | `code_sha256` over the pack's module sources (`.py` files and `pytacheck.module/1` `.yaml` files) and declared data files, excluding docs, tests and CI files, plus the exact pytacheck releases it was measured on | the label falls back to experimental or unvalidated, with "validated at 1.2.0 (code ab12…), this is 1.3.0". A README or test edit does not decertify |
| metacheck (R) | a list of exact metacheck releases | falls back to experimental |

- Hashing one file would be too narrow, since behaviour also depends on helpers, the core and packages such as statcheck. Hashing the whole package would decertify at every release. The exact release plus a rerun is what makes relaxed fidelity (FIDELITY.md) safe: **the Python port is certified on its own measured counts, not on its parity with R.**
- For LLM modules the binding also includes the `settings_sha256`. Parity cases replay recorded LLM replies, so they protect everything after the model call, not the model's own accuracy (FIDELITY.md §10 risk 1). Only the locked model id and prompt, plus the rerun, protect that.
- `code_sha256` is new. `tree_sha256` (`packs/tree.py:49`) hashes every file in the pack folder and stays the integrity and review hash.
- The rule that gates apply to: **a Band A change, on any tier, in a module the registry snapshot labels validated** (FIDELITY.md §2.2) is either signed off by a validator or re-validated. The two documents share this one rule. For the sign-off to mean anything, the validators must be code owners in pytacheck's own `.github/CODEOWNERS` for each validated module's own area file, `parity/deviations/<area>.toml` (e.g. `mod_power.toml`), `parity/canon.toml` and the validated modules' source, with required code-owner review. Today that file has one owner and covers none of these paths. The pytacheck maintainer adds the entries once the team names its validators (§8.1).

**Re-certification at each release.** Without this loop the first release after a certification drops every built-in to experimental, and a server at `validated` has nothing to run.
1. **Who and when.** A validator named by the team runs the rerun. The order is: tag a release candidate, rerun, commit the new release to `implementations.py.pytacheck`, then ship.
2. **On what.** The team's full-paper ground truth for each module (Q8), run paper by paper, so paper-level false positives (flags on uncoded sentences) are counted. `validate()` (`src/pytacheck/validate.py:302`) turns each ground-truth text into its own test paper, which can reproduce TP and FN but not those FP, so it is not enough on its own. If the corpus cannot be redistributed, the team runs the rerun privately and commits the result.
3. **Tolerance.** Each of TP, FP and FN within 5% of the certified count, or ±2 when the count is under 40 (E; the team sets the number). Outside it, the result goes to a validator as new evidence, not an automatic extension.
4. **If no rerun happens in time.** The last certified release keeps its label for one further release, with the note "validated on 0.4.0, not re-measured on 0.5.0". After that it falls to experimental. A server whose validated set would become empty falls back to the last snapshot's validated list with a warning; it never raises `PresetError` on every request.

### 2.6 Field scope

Field scope applies only when the preset declares `fields`. With no preset fields (`metacheck::default`, `metacheck::repository`, explicit `modules=`, the API's fallback), a certification counts, and the badge names the corpus field ("validated on psychology papers"). Only a preset whose `fields` are disjoint from the evidence's `corpus.fields` (and the evidence does not say `general`) turns the label into "validated (psychology)", and under the `validated` policy the module is then treated as experimental (Q5). So `select(preset="metacheck::default", status="validated")` returns the 5 modules of §3.1.

### 2.7 Self-reported numbers

An author's `validation=` numbers stay visible in `pack check`, `store search` and the catalog, marked "self-reported". They never grant a status. The evidence schema is the same, so a self-reported record can be promoted to the registry unchanged once a validator reproduces it.

---

## 3. Presets and status together

### 3.1 Launch statuses for the built-ins (draft for the team)

| Proposed label | Modules | Basis |
|---|---|---|
| validated | power, stat_p_exact, stat_p_nonsig, marginal, stat_effect_size | team-run counts (§1.1) |
| experimental, pending | stat_check | external evidence for R statcheck (Nuijten et al. 2017); certifiable after a team rerun on the pytacheck port restricted to t- and F-tests |
| experimental, candidate via `metrics` | ref_accuracy | rates only, 10 papers (Q6) |
| experimental | the other 23, including data_check | no numeric evidence |

**The planning number is 5 validated modules**, with stat_check and ref_accuracy conditional. The team may cut this to 4 (Q1). Whatever the answer, it is data in `validation.json`, not code. Every gate in the three documents (the sign-off rule, mandatory wording rows, review order, the web app's set) applies to **every module the registry snapshot labels validated**, not to a fixed list, so a change in the set needs no change to the gates.

### 3.2 How they combine

- **The preset says what to check.** Presets get an optional `fields` key (added to `PRESET_KEYS`, `manifest.py:57`), so `init` can offer them by field.
- **The policy says how strict to be.** It is applied after the preset is expanded and before offline dropping (`presets.py:452-505`).
- **Built-in presets:** `metacheck::default` and `metacheck::repository` stay. **`metacheck::validated` is deprecated**; the `validated` policy replaces it (Q2). Until 1.0 it stays as an alias that resolves to `metacheck::default` with `status="validated"` and warns once, so configs written by `init --preset metacheck::validated`, `PYTACHECK_PRESET` and `pc.configure(preset=…)` keep working. The tests that pin it to the Shiny list (`tests/modsys/test_builtin_presets.py:57`, `test_cli.py:231`, `test_presets.py:87,275`, `test_manifest_tree.py:125`) change in TIERS. `DEFAULT_MODULES` in `report.py:61-78` becomes a read of the default preset.
- **Field presets come from the store.** The existing `fields` pack is the seed: `fields::psychology`, `fields::medicine` and so on, maintained as reviewed lists with named maintainers (the CRAN Task Views model).

### 3.3 Setting it

```json
{"preset": "fields::psychology", "status": "validated"}
```

- **Precedence**, as for `preset` (`presets.py:424-442`): argument, then `pc.configure(status=…)`, then `PYTACHECK_STATUS`, then project config, then user config, then the default. Project config may set any status except `all` (§4.8).
- **Python**, on the new top level of ARCHITECTURE.md §2.10: `pc.check(paper, preset="fields::psychology", status="validated")`; `pc.configure(preset=…, status=…)`; `pc.status("power")` returns `Status(label, evidence, stale_note)`; `pc.status_table()` returns one row per module with label, PPV, sensitivity, papers, fields and date. `select(..., status=)` takes the same argument. The compat names `report()` and `pc.use()` accept `status=` too until they go.
- **CLI:** `pytacheck run paper.pdf --preset psychology --status validated`; `pytacheck modules --status certified`; `pytacheck status power` prints the evidence; `pack search` shows the certified label and marks self-reported numbers.
- **`pytacheck init`** keeps its field and preset questions and adds one: "How strict? 1. validated (5 modules in your presets) 2. certified 3. experimental 4. all". The counts come from the registry and the chosen presets.
- **API:** `/paper/check` takes `status`. The server's configured status is a ceiling: a request can be stricter, never looser.

### 3.4 Defaults

| Surface | Default | Why |
|---|---|---|
| library | `experimental` | All built-ins are team modules, and modules from packs the user installed or path-pinned count as opted in: they run with an "Unvalidated external module" badge. So `report(paper)`, the parity goldens and existing store users' output (for example `clinical_trials::trial_registration` under `fields::medicine`) do not change. Only unvalidated modules the user never installed are dropped, which today is none |
| CLI | what `init` saved, else `experimental` | the same |
| web app and API server | `validated` | public users. The Shiny set of 16 becomes the validated ∩ `server_safe` set (§2.4): at most 5 modules, 7 if stat_check and ref_accuracy are certified. The web app is the Gradio extra `pytacheck[app]`, mounted on the API server (ARCHITECTURE.md §3.7); the server's status is a ceiling for both. The drop from 16 modules is visible to users and needs a notice at cutover (§8.1). The scienceverse Platform will take care of the public side (the maintainer, 2026-09-27) and plans to call `pytacheck serve` (REPO_FETCH.md §6.1), so its users get this server default unless its operator sets another status (§8) |

The TIERS gate includes a test that an installed store module named by a preset still runs under the library default, and the release notes state the one behaviour change: `metacheck::validated` now means the `validated` policy.

### 3.5 Combinations that are not allowed

- **A preset entry below the policy** is dropped with a reason. `Selection` gains `dropped_reasons: dict[ref, str]` beside `dropped`, e.g. `{"metacheck::repo_check": "status: experimental"}`, next to today's `"offline"` reason, so `dropped` and its CLI consumer do not change. The CLI prints one line and the report lists what was left out.
- **Every entry dropped** raises `PresetError` before anything runs: "metacheck::repository has no modules at 'validated' (4 experimental: …); use --status experimental". A server instead uses the fallback of §2.5 step 4.
- **Modules named explicitly** (`modules=`, `-m`) run in the library and CLI with a warning and their badge. A server answers 400, listing the modules below its ceiling; it does not run the rest.
- **Dependencies** (`@module(depends=…)`, ARCHITECTURE.md §2.7) are not filtered on their own. The policy applies to the modules the user or preset selected, and a dependency inherits its dependent's check. So a validated module that depends on an experimental lookup still runs, and its dependency is listed in `run.json` only. A server's `server_safe` check covers the whole `depends=` closure.
- **Withdrawn modules** are dropped under every policy; naming one explicitly warns loudly.

### 3.6 How the report shows status

- **A badge under each module title**, generated from the evidence:
  - "Validated by the metacheck team · PPV 0.91 · sensitivity 0.90 · 128 Psychological Science papers (2026-06) · protocol"
  - "External module, validated by the metacheck team · …"
  - "Experimental: error rates unknown" (the wording of `data_check.R:43`)
  - "Unvalidated external module: error rates not measured by the team"
  - "Provisional: based on the metacheck team's published counts (R version)" (§2.3, bootstrap)
- **The validation box** is generated from one sentence template, replacing the 8 hand-written prose blocks. This is a Band C change to the report (FIDELITY.md); module outputs do not change.
- **The summary table** gets a Status column.
- **The header** gets one line: "Ran 5 validated and 10 experimental modules (policy: experimental). Left out: …".

**Size (E).** 300-400 lines: the registry loader and labels ≈ 120, the `select` filter and `dropped_reasons` ≈ 30, CLI and `init` ≈ 80, report ≈ 50, tests ≈ 100. The basis is similar existing functions (`_review_fields` is 35 lines, the offline drop 9).

---

## 4. The store as a marketplace

### 4.1 What to copy from agent marketplaces

| Concept | DeepSeek Harness | Claude Code plugins, Agent Skills | pytacheck |
|---|---|---|---|
| Unit | an npm package declaring `"dsh": {"bundle": {"patch": …}}` | a plugin folder, or a `SKILL.md` folder with small frontmatter | a **pack**: `pack.json` plus `@module` `.py` files or `pytacheck.module/1` YAML files (ARCHITECTURE.md decision 2 (a)) |
| What users start with | a **profile**, an ordered list of bundles (`dsh --profile web`) | bundle plugins through `dependencies` | a **preset** (`pack::preset`, with `extends` and `exclude`) |
| Install | `dsh plugin --profile P add github:owner/repo` | `claude plugin install name@marketplace` | `pytacheck pack install name`, or `owner/repo@rev` for unlisted packs |
| Catalog | the GitHub topic `dsh-plugin`; a community `marketplace.json` | `marketplace.json` in any repository | `index.json` built by store CI, a generated `CATALOG.md` (§4.7), and a `pytacheck-pack` topic for unlisted packs |
| Pin | npm version; community catalogs require a SHA | `github{repo, ref, sha}` | `{github, rev}` with a 40-hex rev, plus `tree_sha256` |
| Vetting | none ("Review plugins … before allowing them to run") | none by default; admin allowlists | a tree-bound review, plus the status label (§2) |
| Governance | – | reserved names, `strictKnownMarketplaces`, cross-marketplace dependency allowlists | reserved names, per-store trust, status only from the team's registry (§2.3) |
| Extension metadata | `package.json` | fixed keys plus a free `metadata` map | fixed pack.json keys; unknown keys pass through (`manifest.py:125-153`) |

The format is already close. pytacheck's store follows the same design: a CI-built index, sources pinned in the authors' repos, a tree hash, a consent card, an AST scan, and no code run while browsing (`module-system-v2.md:195-262`, `:438-487`). **What no agent marketplace has is the second axis:** measured accuracy. The closest precedent is Hugging Face's eval results, which tie a score to a benchmark revision and mark it verified or self-reported.

### 4.2 Principles

1. **Code stays in the authors' repositories.** The store is a list of pins (R-universe, pre-commit).
2. **Identity is not quality.** Trust and status are separate labels, and neither reads as the other (the VS Code "verified publisher" lesson). A store review sets `reviewed`; it never makes a module validated.
3. **Evidence is data**, tied to a benchmark revision and a code hash (HF eval results, rOpenSci's versioned badges).
4. **One source of rules.** Store CI imports the installer's own checks (`pack check`), with no second copy (the DeepSeek Harness Desktop marketplace does the same).
5. **No popularity ranking and no open upload.** skills.sh ranks by install telemetry; on ClawHub, 341 of 2,857 skills were malicious (Feb 2026), and Snyk found flaws in 36.82% of 3,984 skills.
6. **R first.** R-only packs are listed as port candidates, not as installable packs: neither tool runs an `.R` file from the store (§5.3). Ports carry recorded consent (§5).
7. **Build for the store that exists.** It has two in-repo packs and no external `.json` entry (§1.4). Pieces with no user yet are deferred until the store lists five or more external packs: the re-pin bot (§4.3), `renames`, deprecation automation (§4.5) and badge endpoints.

### 4.3 The registry and the re-pin bot

- Keep `packs/<name>.json` with `{name, source: {github, rev}}` for packs in their authors' repositories.
- **Deferred (principle 7):** `track: "*release"` (R-universe's convention) or a branch name, and a scheduled workflow that opens a re-pin PR when the tracked ref moves (the conda-forge autotick and `pre-commit autoupdate --freeze` model). When it is built:
  - The PR leaves `reviewed` and `reviewed_tree_sha256` untouched, so `store build --check` fails on the stale review until a maintainer re-reviews. Only a store maintainer's commit may clear `reviewed`; the check job flags any other `"reviewed": null`, since `_review_fields` treats an explicit null as a withdrawn review with no error (`build.py:185-186`).
  - Merging needs CODEOWNERS approval.
  - The bot uses a GitHub App token, because PRs opened with the workflow's `GITHUB_TOKEN` do not trigger `pull_request` workflows, so the check job would never run.
  - A certification bound by `code_sha256` (§2.5) is unaffected unless the module code changed.
- A re-pin reruns the AST scan and the pack's tests. The check job today runs tests only for folder packs (`CONTRIBUTING.md:67-69`), so running the tests of a `.json` pack at its rev is a new CI step (§6 item 8).

### 4.4 Index additions

`store build` derives or copies these into `index.json`:

| Key | Source |
|---|---|
| `publisher: "team" \| "external"` | the registry's `team_packs` |
| `status` per module, for display only | copied from `validation.json` at build time. A client never reads status from an index; the snapshot or refresh wins (§2.3) |
| `deprecated`, `successor` | entry files (added to `_ENTRY_KEYS`, `build.py:54`) |
| `ported_from`, `consent.scope` | pack.json `ports.*` (§5.6) |
| `citation` | the pack's `CITATION.cff`, which the scaffold already writes |
| `maintainers` | pack.json, as GitHub handles (the nf-core convention) |
| `ci` | the nightly job (§4.6) |
| `yaml` in `languages` | the pack's `pytacheck.module/1` files (ARCHITECTURE.md §4.3 DECL). `store build` must set the `code` flag to true for a pack with YAML modules, since they run on the user's papers like any module; today it counts only `.py` files |

### 4.5 Lifecycle

The lifecycle uses the §2.1 labels plus the index; it adds no label of its own.
- **Deprecated** = the entry's `deprecated` flag plus a notice, and a `successor` if there is one. A module whose nightly CI fails, or whose maintainer does not answer for one release, is deprecated by a maintainer (the Bioconductor rule, applied by hand until principle 7's threshold).
- **Withdrawn** = listed under `withdrawn` in the registry (§2.4), for a known accuracy problem, whether the module was certified or not.
- **Yanked** = removed from the index; the entry file keeps its history and can be revived.
- **Yanks and withdrawals reach installed packs.** At load time the cached index and snapshot are checked, and a yanked or withdrawn module warns; `--strict` fails (`registry.py:511-552`). Today a yank affects only new installs.
- `pack list` shows available updates from the cached index.
- **Deferred:** a `renames` map (Claude's `renames`), so pins and presets keep resolving after a rename.

### 4.6 Nightly CI

The store runs `pack check` and each pack's tests on every listed rev each night, against pytacheck's main branch (and its latest release, once one exists; there is no tag yet). A failure is shown in the catalog and opens an issue on the store. This also gives pytacheck an early warning when a core change breaks a store module (G7 in ARCHITECTURE.md §4.1). The job follows the CI rule of §4.8: the test job holds no secrets, and the issue is opened by a separate job.

### 4.7 Catalog

`store build` also writes `CATALOG.md` (≈ 30 lines of code): one table of packs and modules with description, fields, status with PPV and sensitivity and a link to the evidence, CI state, review date and tree, licence, languages (R, Python, YAML), maintainers, preset membership and the install line. GitHub renders it, and it replaces the README's hand-written pack table. There is no ranking by install count. A static site on GitHub Pages is deferred: Pages needs the store repository to be public (or a paid plan), and publishing it would expose a store that is private today (§1.4).

### 4.8 Security baseline

Keep the trust core, about 720 lines: the consent card, SHA and tree pins, read-only installs with a runtime integrity check, the AST scan, and **the project-config code-trust gate**:
- `_unsafe` refuses a project config owned by another user or writable by everyone, like git's `safe.directory` (`config.py:210-228`).
- `trusted_local` and `local_code` keep path packs and `.py` preset modules named by a project config inactive until the user trusts them (`config.py:264-300`, `:476-481`), and `_trust_project_code` shows the consent card for them (`install.py:672-704`).
- Without it, running pytacheck inside a cloned repository (a colleague's analysis repo, a downloaded replication package) imports whatever path pack its config names: the VS Code workspace-trust problem. Its UI may be simplified; the check stays.

Add:
- **Reserved names:** extend `RESERVED_PACK_NAMES` (`manifest.py:41`, today `metacheck`, `local`, `pytacheck`, `modules`) with `official*` and `scienceverse*`. `PACK_NAME_RE` is already ASCII-only. Store CI rejects a new pack or module name within edit distance 1 of an existing pack or built-in module name (`clinical_trial` against `clinical_trials`).
- **A rescan on every re-pin.**
- **Per-store trust:** adding a store is explicit. No store sets status (§2.3).
- **Project config cannot widen trust.** A project config may not set `stores`, `validation_registry` or `status: all`. These are the keys a hostile checked-out repository could use.
- **CI that runs pack code holds no credentials.** Jobs that import or test pack code run with `permissions: {}`, no secrets and no persisted credentials. A read token, while one is needed, is set only on the install step, never in workflow-level `env`. Issue creation, index commits and catalog publishing run in a separate job on main that reads the test job's artifact. Fork PRs get no secrets anyway, so a token never fixes CI for community PRs. The same rule applies to G7 in pytacheck's own CI (ARCHITECTURE.md §2.11).
- **No hosted install.** The API never installs packs and runs with `use(allow_local=False)`, as today.
- **YAML modules** (ARCHITECTURE.md decision 2 (a), §4.3 DECL): a strict safe loader that refuses anchors, aliases, tags and duplicate keys and caps a file at 64 KB (E), a typed schema, patterns only through lane 2 with count and length caps and a time limit per paper over all of a module's `regex` calls, no HTML in text, and `{n}` and `{s}` as the only placeholders. `pack check` runs the schema check where it runs the AST scan on `.py` files. A YAML module gets no extra trust: the same pin, tree hash, consent card and project-config gate. Today the gate counts only `.py` references as local code, and the consent card calls a pack without `.py` files "no code" (`install.py:426`); DECL extends both to YAML modules.

### 4.9 Federation, later

Labs and institutes may run their own stores, public or private (§4.11). Allowlists (`stores.allow`) and a store's declared `allow_dependencies_from` come when a second real store exists, not before. A store never certifies; status comes only from the team's registry.

### 4.10 Pack dependencies

`"depends": ["clinical_trials"]` in pack.json, the pack-level twin of `@module(depends=…)`. It is not a `requires` key: pack.json `requires` maps names to version specifiers (`manifest.py:145-148`), as `@module(requires=…)` holds capabilities, and neither is used for dependencies. Unknown keys pass through (`manifest.py:125-153`), so pack.json stays at schema 1. `pack install` offers to install the missing packs through the same consent card. There is no version solving, only pins. The medicine preset then declares what it now says in prose.

### 4.11 What to delete from `packs/`

`packs/` is 5,361 lines, with 5,351 lines of tests **(M)**. It serves a store with two packs.

**Precondition.** The official store is private today, and every user reaches it through the token fetch chain (§1.4). None of the fetch or auth deletions below lands until both `scienceverse/pytacheck` and `scienceverse/pytacheck-modules` are public and index.json has been rebuilt from the public store. Even then private GitHub stores keep working, through the token path that stays.

| Item | Lines | Where |
|---|---:|---|
| `auth.py`: netrc and the extra HTTP client go. It keeps a `GITHUB_TOKEN` header sent only to GitHub hosts (api.github.com, github.com, raw, codeload), stripped on a cross-host redirect, and never written into pins or run records (`has_credentials`, `redact`, used by `install.py:209`, `stores.py`, `ui.py`). `GET /source` (§5.4) publishes pack sources, so the credential-free records matter | 578 → ≈ 120-150 | `packs/auth.py` |
| The private-store fetch chain: keep the contents and tarball API with a token; drop the raw and git fallbacks | 73 → ≈ 35 | `stores.py:223-295` |
| The git fallback and `ls-remote`. Cost: `ls-remote` is what makes installs work when the anonymous GitHub API limit (60 requests per hour per IP, often shared behind a university NAT) is used up; after this, such users need a token | ≈ 216 | `fetch.py:342-557`, `:559-605`, `:636-643` |
| Their tests, except those for the token path and credential-free records | ≈ 700 of 765 + 327 | `tests/modsys/test_private_store.py`, `test_credentials.py` |
| `dist` packs (entry points) | ≈ 50 | `registry.py:304-327`, `install.py:834` |
| Legacy entry points and the `./modules/name.py` lookup | ≈ 30 | `module.py:446-553` |
| Path sources inside stores | ≈ 50 | `install.py:276-290`, `build.py:354-356` |
| GitLab, Codeberg, `file://` and `git+` ref forms | ≈ 40 | `stores.py:128-165`, `fetch.py:125-143`, `install.py:230-273` |
| Duplicates (`_read_record`, `_refuse_credentials`) | ≈ 25 | |
| `_card`, made table-driven | 103 → ≈ 50 | `install.py:353-455` |

The project-config code-trust gate is **not** deleted (§4.8). Nor is `config_files()` (`config.py:231-261`), which is core config loading.

**Total (E):** 850-1,100 source lines and about 700 test lines, about 16-20% of `packs/`. Decision 13 (decided 2026-09-27: (a); §9) settles it: private-store support was added deliberately in the store's commit 1005d6b, so it is narrowed to GitHub with a token, not removed. "Clone it and use a path pin" is not the replacement: a path pack is `trust=local`, unpinned ("live: edits take effect at once, nothing is pinned", `install.py:666`), gets no integrity check or yank notice, and is excluded from the API and the web app (`registry.py:453-458`). The rows that touch `module.py`, `config.py` and `registry.py` are files CORE holds, so they land as a change request to the core agent after CORE-1d (§8, STORE-2b).

### 4.12 Net size

| Change | Lines (E) |
|---|---:|
| status (§2-§3) | +300 to +400 |
| `pack validate` plus the `validation/<module>/gt.csv` convention | +120 |
| `CATALOG.md` in `store build` | +30 |
| lifecycle, load-time warnings, update notices | +60 |
| pack `depends` | +50 |
| deletions (§4.11) | −850 to −1,100 |
| **`packs/` and status together** | **≈ −190 to −540**, plus about −700 test lines |

New lines in this table: +560 to +660. PORT adds ≈ +1,260 in `src/pytacheck/port/` (§5.2), outside `packs/`. DECL's ≈ 40-100 lines of YAML discovery, checks and the consent card in `packs/` are not in this table; ARCHITECTURE.md §5.3 carries them with the rest of decision 2 (a).

---

## 5. Porting R modules

### 5.1 What already exists

- **A dictionary.** `porting/symbols.json` maps 1,127 R symbols, 295 of the 300 NAMESPACE exports among them, to Python paths. It records names only.
- **What modules use.** The 30 upstream modules call 162 distinct metacheck exports (scroll_table in 26 files, plural 18, text_search 13). External calls: `dplyr::` 307, `ellmer::` 54, `xml2::` 36, `stats::` 33 **(M)**. Across the mapped modules, 20,399 R lines became 27,414 Python lines (1.34x); small modules stay near 1:1 (marginal 79 → 84).
- **R execution.** `parity/r/run_cases.R` runs any module file through `do.call(module_run, …)` (`:220`), isolated: credentials unset (`:57-63`), a throwaway cache (`:64-70`), deterministic ids (`:90-105`), httptest2 mocks (`:209-215`). Upstream `module_run` accepts file paths (`R/module.R:15`, `:173-199`), so a third-party `.R` file runs unchanged.
- **Scoring.** `parity/accuracy.py:399-725` (`row_f1` `:553`, `score_output` `:604`) and `parity/canonical.py`.
- **Report helpers.** `scroll_table` and `collapse_section` return fenced Quarto strings, and `report/render.py` parses them. They are permanent façades (ARCHITECTURE.md §2.10), used by the store's `clinical_trials` pack, and the fence parser is kept for them.

### 5.2 `pytacheck port`

Five resumable subcommands, with their state in `port.json`.

**`port init`: the licence and consent gate, run first.**
1. Fetch the source through `packs/fetch.py`, pinned to a 40-hex rev, and hash the `.R` file. Record whether the source repository is a fork (the GitHub API's `fork` flag) and, if so, its parent.
2. Detect the licence as SPDX from LICENSE and DESCRIPTION. R's forms map as `GPL-2` → GPL-2.0-only, `GPL (>= 2)` → GPL-2.0-or-later, `MIT + file LICENSE` → MIT, `AGPL (>= 3)` → AGPL-3.0-or-later. Check it against the matrix (§5.4). The verdict is one of:
   - `ok`;
   - `needs-notices`;
   - `needs-relicence`: no licence, or GPL-2.0-only. It becomes `ok` only when a licence grant is recorded in `consent.license_granted` under §5.6 rule 3b;
   - `blocked`: NC or ND. No grant path; the author would have to relicense the original.
3. Record consent (§5.6). **Self-consent** has one rule, the same as §5.6 rule 3a: the pinned source repository is not a fork (or the pin points at the fork's upstream parent), and the porter's GitHub identity is the owner of that repository or a DESCRIPTION `aut`/`cre` whose GitHub handle is confirmed. For a source with several authors, the `cre` or maintainer's consent is enough for listing, since consent is courtesy (§5.5); it is not a relicence. Otherwise `port init` prints an issue text for the author's repository and stops until there is an evidence URL.

**`port translate`: LLM-assisted.**
1. Extract the roxygen header into `@module` arguments, the `<validation>` prose into a draft evidence record (the author confirms it), and a census of the symbols the module calls.
2. Build a brief: the called symbols mapped to the new API (`pytacheck.doc`, `pc.check`, ARCHITECTURE.md §2.10) with their Python signatures (`inspect.signature`), not the R-shaped names in `pytacheck.compat`; the matching rows of `docs/PORTING.md` §3; one few-shot pair per output form (marginal.R and the migrated marginal check once CORE lands, today's marginal.py until then; once DECL converts marginal, marginal.R and `marginal.yaml` for YAML output, and ethics_check.R and the migrated ethics_check for `.py` output); and the relaxed rules of FIDELITY.md.
3. Write the port next to the untouched `<name>.R`, with a test file and `ports.<name>` in pack.json. The port is `<name>.py` against `pytacheck.doc`. From DECL on, under ARCHITECTURE.md decision 2 (decided 2026-09-27: (a)), it is `<name>.yaml` in the `pytacheck.module/1` format when step 1's census finds only `text_search`, `count`, `scroll_table`, `collapse_section`, `format_ref` and `bibentry`, plus the base R glue `ifelse`, `nrow`, `sprintf`, `c`, `names` and `list`, and a two-way traffic light. `--form yaml` or `--form py` forces a form. Its report is typed blocks (`Table`, `Callout`), not fenced strings. The `scroll_table` and `collapse_section` façades still work for hand-written v1 modules, because the fence parser is kept for them (ARCHITECTURE.md §2.9).
4. Run on the demo paper and feed tracebacks back, for at most 3 rounds (E). The brief can also be written out for a coding agent instead.

**`port diff`: faithful or not.** R runs as one `Rscript` process per diff: the shipped copy of `run_cases.R` and `canonical.R` reads a JSON case file. The port runs through the canonicaliser (FIDELITY.md §4). The corpus is the demo paper, the 21 matrix papers, and `--corpus DIR` for the author's validation set.
- **Reproducible for a pip-installed porter.** The matrix papers are upstream metacheck test fixtures (`parity/accuracy/matrix.toml:32-54`), which the wheel does not contain, so `port diff` fetches them from metacheck at the pinned rev through `packs/fetch.py`. R needs a pinned metacheck, httptest2 and the module's own R dependencies: The conda lock that pins them, `parity/r/conda-linux-64.lock`, is not in the wheel, so `port diff` fetches it from pytacheck's repository at the installed version's tag (once the repository is public, decision 13 (decided 2026-09-27: (a))) or uses a published container image with them.
- **Scope.** The corpus is paper-only and offline. Ports of repository modules and LLM modules (ellmer is called 54 times) get "diff: n/a, manual review" in `port.json`.

| Output | Rule |
|---|---|
| run | both succeed or both fail, on every input |
| `traffic_light` | equal on ≥ 95% of inputs, each miss explained |
| `table` | row-multiset F1 ≥ 0.95 on the declared key columns; order, dtypes and declared renames ignored |
| `summary_table` | counts exact; other numbers within relative 1e-6 |
| `summary_text`, report | not compared; mismatched numbers warn |

Cost for 22 inputs (E, from `PERF_REPORT.md:30-35`): R ≈ 15 s, Python 1-2 s. `port.json` records both hashes, the corpus hashes, the metacheck rev, the scores and the verdict.

**`port finish`.** Runs `pack check`; refuses without complete consent and a passing diff; writes LICENSE and NOTICE with the original's notices; adds the header "Ported from <url>@<rev> (<licence>, © <authors>), <date>"; writes the store entry and a PR body with the licence verdict, the consent link and the agreement table.

**`port update`: when the R original moves.** Fetch the new R rev, show the R diff and a brief built the way `scripts/upstream_sync.py` builds one, retranslate the changed parts, rerun `port diff`, and bump `ported_from.rev` and `sha256`. A new R rev under the same licence and scope needs no new consent. The nightly job (§4.6) compares each `ported_from` rev with the source's default branch and opens an "R original moved" issue, so a fix by the R author reaches Python users.

**A passing `port diff` means "faithful port", not "validated".** A port of a validated R module keeps that status only through a rerun of the evidence corpus (§2.5).

**Size (E).** ≈ 1,260 new lines (licence 200, consent 120, extract 200, brief and loop 150, diff 250, update 80, enforcement 140, CLI 120) and ≈ 1,100 moved (the R runner and the scorer, extracted to `pytacheck.port`). DECL's YAML output adds ≈ 80 (ARCHITECTURE.md §5.3).

**The pilot.** No third-party metacheck R module is known today; all 30 upstream modules are already ported by hand. So PORT's gate is a round-trip port of an upstream module, for example `marginal.R`, that passes `port diff` at the thresholds above. DECL adds a second round trip: `marginal.R` translated to YAML passes the same thresholds. An external pilot needs a named module and author, with consent in hand before PORT starts: waiting for an author's reply is not in PORT's 5 days. The lack of demand evidence is a risk, and PORT can wait behind ARCHITECTURE.md decision 5's phase-A stop point (decided 2026-09-27: (a)) until a real request arrives.

**For R authors.** What an R author does to get a module into the store:
1. Put the `.R` module in a public GitHub repository with a LICENSE on the §5.4 list.
2. Open an issue or PR on the store naming it. The module is listed as a port candidate.
3. Whoever ports it (the author, the team or another porter) runs `pytacheck port`; the author answers the consent issue, unless they are the porter.
4. Optionally supply the validation corpus (`--corpus`), and send the evidence to the registry for a status (§2.7).

### 5.3 Running R directly

pytacheck never runs R except as the oracle of `port diff`, which needs a local R (or the container of §5.2) and metacheck. There is no persistent R worker and no `--allow-r` runner for reports; one `Rscript` process per diff is enough for a 15-second check. R is never run in the API, in hosted runs or in presets:
- R and a pinned metacheck add 0.5-1.5 GB to an image (E), and a second stack to patch.
- An `.R` module is `source()`d: arbitrary code, and `packs/scan.py` has no R counterpart.
- Per paper and module it costs seconds, against milliseconds in Python.

### 5.4 Licences

A translation is a derivative work (17 U.S.C. §101; EU Directive 2009/24/EC Art. 4(1)(b); GPLv2 §0). An LLM translation does not launder the original. **The store requires GPL-compatible pack licences as policy**, so that the store's recommended combination with AGPL pytacheck can be distributed and hosted under AGPL-3.0-or-later; the pack's own files keep their licence. The GPL FAQ calls a plug-in in the same process "a single combined program", but that is the FSF's interpretation, not settled law. Packs are distributed separately, from the authors' own repositories (§4.2), and combined on the user's machine. Whether such a pack is legally bound is a question for counsel (below).

| Original | Ported into a pack | Notes |
|---|---|---|
| MIT, BSD | yes; pack may stay MIT | keep the notices |
| Apache-2.0 | yes | keep LICENSE and NOTICE, mark changed files; not GPLv2-compatible |
| GPL-2.0-only | only with a relicence (`needs-relicence`) | store policy: not combinable with AGPL-3.0 |
| GPL-2.0-or-later | yes, as GPL-3.0-or-later | |
| GPL-3.0, AGPL-3.0 | yes | metacheck itself is AGPL (`DESCRIPTION:37`), so ports of its modules stay AGPL-3.0-or-later |
| CC BY 4.0 | yes, but ask for MIT | CC advises against CC licences for code |
| CC BY-SA 4.0 | yes; the port is licensed GPL-3.0-or-later | CC declared BY-SA 4.0 one-way compatible with GPLv3 (2015); counsel to confirm the AGPL combination |
| CC NC or ND | **no** (`blocked`) | not free |
| no licence | only with a written grant (`needs-relicence`) | all rights reserved |
| CC0 | yes; any pack licence | no conditions; GPL-compatible, though not OSI-approved |

So the store asks for "a licence on the §5.4 list", not for "an OSI-approved licence" as `check.py:284` says today.

Notices to preserve: MIT's copyright and permission text, Apache's NOTICE and change notices, GPL/AGPL §5(a) ("prominent notices stating that you modified it, and giving a relevant date"), CC BY §3(a). The port's header, in its `.py` or `.yaml` file, plus `ported_from` covers them. pytacheck's own port of metacheck needs the same: FIDELITY.md and ARCHITECTURE.md drop the per-file "Port of R/…" docstrings, and pytacheck has no NOTICE file. A top-level NOTICE replaces them: "pytacheck is a modified Python translation of metacheck (© DeBruine, Mesquida, Werner, Lakens et al., AGPL-3.0-or-later), <url>@<pinned rev>, modified <date>".

**Inbound terms: the DCO**, not a CLA, for the pytacheck repository and for packs kept in the store repository. The store never relicenses ("You keep the copyright; the pack keeps your licence", `pytacheck-modules/CONTRIBUTING.md:71`). For a pack pinned from an author's repository, the store's commits are only the pin, so a DCO there certifies nothing about the code. Those packs rely on the licence check and, for ports, the consent record. The DCO certifies only the submitter's own right to submit, which is why third-party ports need the consent record.

**AGPL §13 for hosted use.** A modified version must offer its Corresponding Source to remote users, and installed packs arguably count.
- **What.** `GET /source` returns the pytacheck commit plus each active pack's `{source, rev, tree_sha256, license}`, which run records already hold. Reports get a "Source" footer, and the Gradio app a visible "Source code" link, because its footer is hidden (`footer_links=[]`, ARCHITECTURE.md §3.7).
- **Who.** `GET /source` and the UI link belong to WEB, which ships first; the report "Source" footer, built from the same data, belongs to SERVICES-b (ARCHITECTURE.md §3.7, §4.3). The WEB/API gate checks that the response names the running commit and each active pack, and that the UI shows the link. With the scienceverse Platform on the public side (the maintainer, 2026-09-27), remote users would meet pytacheck on the Platform's pages, so the offer would likely be needed there too (a question for counsel, below), and `GET /source` could feed it (ARCHITECTURE.md §3.7).
- **Precondition.** The offer can be met only if pytacheck's repository (or an sdist per release) is public, which ties it to decision 13 (decided 2026-09-27: (a)).
- **Hosted packs.** Hosted mode should load only store packs at public revs. Today `use(allow_local=False)` drops only path packs (`registry.py:458`); unlisted `owner/repo@rev` installs, dist packs and private-store packs stay active. So the hosted filter is new: only packs with trust `store` from a public store, plus the validated ceiling. WEB owns it.

**Uncertainties** for counsel (for example SFLC) before the store opens to third-party ports:
- whether 50-80-line modules made of `text_search` calls are original enough to be protected (assume yes);
- whether a separately distributed pack is bound by the plug-in interpretation;
- CC BY-SA 4.0 into AGPL through GPLv3;
- whether an issue comment is a sufficient licence grant;
- whether users of a site that calls `pytacheck serve` from its own server, as the scienceverse Platform plans to, interact with pytacheck remotely under AGPL §13;
- the scaffold's module template, derived from AGPL metacheck `_module.R`, emitted into packs whose default LICENSE is MIT (`packs/scaffold.py:3-5`, `:84`);
- GPL-3.0-only version locks;
- EU against US law.

### 5.5 Is consent needed at all?

Legally, code under a permissive or GPL-compatible licence can be ported with attribution alone. Consent is then courtesy. It is legally required only for unlicensed code and for licences that need a relicence. **The store requires it for listing anyway** (decision 17 (decided 2026-09-27: (a))): the community is small, the authors are identifiable, and a port published without asking is the fastest way to lose them. Unlisted ports (`owner/repo@rev`) need only the licence check.

### 5.6 The consent record

In pack.json under `ports.<module>`, not in `@module`, because consent governs distribution. `manifest.py:134` keeps unknown keys, so pack.json stays at schema 1.

```json
"ports": {"trial_registration": {
  "ported_from": {"source": {"github": "owner/repo", "rev": "<40 hex>", "path": "R/x.R",
                             "fork": false},
                  "sha256": "<of the .R file>", "license": "MIT",
                  "copyright": ["2025 A. Author"],
                  "authors": [{"name": "A. Author", "github": "aauthor", "orcid": "0000-…"}]},
  "consent": {"by": {"name": "A. Author", "github": "aauthor"},
              "role": "author|maintainer|rights-holder|self", "date": "2026-09-20",
              "scope": ["port", "publish-store", "host-api"], "license_granted": null,
              "evidence": "https://github.com/owner/repo/issues/12#issuecomment-…",
              "tree_sha256": "<tree hash of the R source repository at the consented rev>"},
  "porter": {"github": "bporter"},
  "translation": {"tool": "pytacheck port", "model": "<provider/model>", "date": "2026-09-21"},
  "diff": "port.json"}}
```

**`store build --check` enforces it** (≈ 140 lines):
1. `license` must be valid SPDX and pass the matrix; today's warning (`check.py:279-285`) becomes an error for store entries.
2. A complete record is required for any `.R` file with a `.py` or `.yaml` twin and for any `ports.*` entry. The `sha256` must match the `.R` file, and `rev` must be 40-hex.
3. Consent:
   - a. It is not needed only for self-consent, under the single rule of §5.2 step 3: the source is not a fork (or the pin is its upstream parent), and `by.github` owns that repository or is a DESCRIPTION `aut`/`cre` with a confirmed handle. Otherwise `evidence` must point into the source repository; with a token, its author must be the owner or a collaborator.
   - b. A licence grant (`license_granted`, for a `needs-relicence` verdict) must come from every holder in `ported_from.copyright`, or from the rights-holder of an organisation-owned repository. It should be a LICENSE commit in the source repository, not only a comment. One collaborator's comment is not a grant.
4. `scope` must include `publish-store`. Without `host-api` the entry gets `hosted: false`, and the API refuses the pack.
5. `consent.tree_sha256` is the R source tree at the consented rev. A new R rev under the same licence and scope needs no new consent and only warns reviewers; a changed licence or scope is an error until the author consents again. Normal pack releases do not trigger it.
6. The hashes in `port.json` must match the tree, and its verdict must meet §5.2's thresholds. Store CI reruns `port diff` in the §5.2 container for port PRs. Until it does, the install card shows the verdict as "porter-reported", as §2.7 does for self-reported numbers.
7. LICENSE (and NOTICE for Apache) must contain the `copyright` lines.
8. For packs kept in the store repository, commits carry a DCO sign-off (a new step in `check.yml`). Pinned external packs are covered by rules 1-7 instead (§5.4).

The install card gains one line: "ported from owner/repo (MIT); consent by @aauthor, 2026-09-20".

---

## 6. The real store: proposed fixes

Status on 2026-09-27: rows 1, 2, 3 and 5 are partly done, through pytacheck-modules#1 (merged 2026-09-27) and pytacheck-modules#2 (open). The other rows are open.

| # | Fix | Where |
|---|---|---|
| 1 | **Make CI run, and let outsiders in.** Make both `scienceverse/pytacheck` and `scienceverse/pytacheck-modules` public (decision 13 (decided 2026-09-27: (a)); the simplest route for AGPL §13, fork-based contributions and a public catalog). Adding the `PYTACHECK_READ_TOKEN` secret is only a stopgap: it fixes maintainer PRs, never fork PRs, which get no secrets. Until the store is public, pytacheck keeps the token fetch path (§4.11), so no user is cut off. **Partly done** (pytacheck-modules#1): store CI runs on a self-hosted runner and passes, with the `PYTACHECK_READ_TOKEN` secret set. Both repositories are still private | store and pytacheck settings |
| 2 | **Re-pin** to pytacheck's main branch, or a tag, once `scienceverse/pytacheck#1` merges. Make the scaffold derive its pin from the installed version instead of a constant. **Partly done**: pytacheck-modules#2 (open) re-pins the store to `claude/elegant-fermat-eo7s89`, the head of `scienceverse/pytacheck#1`, since pytacheck's main holds only the initial commit; pytacheck's scaffold names the same branch. The move to main and the derived scaffold pin are not done | `check.yml:39`, `README.md:16,37`, `CONTRIBUTING.md:9,16`; pytacheck `packs/scaffold.py:24-26` |
| 3 | **Rebuild index.json** (the push-to-main job commits it once CI runs), and again from the public repository once it is public. **Partly done** (pytacheck-modules#1): 40cc467 rebuilt it, and the index job on main found it up to date after the merge. The rebuild from the public repository waits | `index.json` |
| 4 | **Licence wording:** "a licence on the §5.4 list" for packs, in place of the CC0-only-for-presets and OSI wording; pytacheck's `check.py:284` message to match | `REVIEW.md:22`, `CONTRIBUTING.md:23` |
| 5 | **Scope the read token to one step.** Move `PYTACHECK_READ_TOKEN` out of the workflow-level `env` onto the install step; run pack code and tests with `permissions: {}`; commit index.json from a separate job on main (§4.8). **Partly done** (pytacheck-modules#1): the token reaches only the install steps. The separate `index` job on main, which commits index.json, was already there before #1. The `check` job still runs pack code and tests with `contents: read` | `check.yml` |
| 6 | **Drop the other forges from the docs:** remove "gitlab and codeberg sources work the same way" | `CONTRIBUTING.md:61` |
| 7 | **A validation section in REVIEW.md:** reviewers check code, not accuracy. Status requests go to the registry repository | `REVIEW.md` |
| 8 | **Three PR templates:** add or update a pack, port an R module (consent, licence verdict, `port diff` table), request a status (evidence record, corpus link) | `.github/PULL_REQUEST_TEMPLATE/` |
| 9 | **The fields pack:** `fields` keys on each preset; `"depends": ["clinical_trials"]` on the pack once §4.10 lands | `packs/fields/pack.json` |
| 10 | **Store CI additions:** the nightly job (§4.6), tests for `.json` packs at their rev (§4.3), `CATALOG.md` from `store build` (§4.7), the DCO step for in-repo packs (§5.6) | `.github/workflows/` |

Items 1-6 are fixes and can land now; 7-10 follow the pytacheck changes they depend on.

---

## 7. The R side

What metacheck needs so both packages read the same registry, in about 150 lines of R (E). All of it is optional for pytacheck: §8.1 says what happens if the team declines.

1. **Ship `inst/validation.json`** and add `module_status(module)` and `modules_at_status(policy)` (≈ 40 lines, jsonlite is already imported).
2. **Render the badge and evidence** in `module_report()` from the registry (`R/report.R:515-539`), falling back to `<validation>` blocks for unlisted modules.
3. **`report(..., status = "experimental")`**, and the same filter in `report_repository()`, with a `message()` listing what was dropped.
4. **The Shiny app** replaces `VALIDATED_MODULES` (`app.R:236-241`) and `validated_modules` (`report_app.R:162-166`) with the validated modules whose evidence says `server_safe: true` (§2.4), so both apps read the same flag.
5. **The same keys and binding:** `metacheck::<name>`, and a list of exact metacheck releases (§2.5). No file hash, so `digest` stays in Suggests.
6. **The preset lists** as `inst/modules/pack.json` (`module-system-v2.md:580-583`), so `report()` and the app share one list.
7. **The team process:** a named validator group and a written protocol (sampling, coding, the unit counted, how TP, FP and FN are defined), linked from each evidence record. This settles Q3 and Q4 for the future.
8. **Store index, optionally.** R-only packs are port candidates (§4.2 principle 6). If the team wants R users to install them, metacheck would need a jsonlite reader of `index.json` and `module_run(path)` on the fetched file; this document does not propose it.

---

## 8. Sizes and work packages

| Package | Days (E) | Content | Depends on |
|---|---:|---|---|
| **TIERS** | 2.5 | the provisional `validation.json` snapshot at `src/pytacheck/resources/status/validation.json` (§2.3), labels, policies, `status=` everywhere, `dropped_reasons`, `init`, report badges and box, the `metacheck::validated` alias and its five test files (§3.2), removal of `DEFAULT_MODULES`. Gate: the snapshot's schema, a built-in entry for each module, `select(preset="metacheck::default", status="validated")` returns the 5 modules, and an installed store module named by a preset still runs by default | none; phase A. Owns `presets.py` before CORE-1d. The §8.1 asks are sent before it closes |
| **STORE-2a** | 1.5 | §4.3 (without the bot), §4.4 (without the `yaml` row, which is DECL's), §4.5, §4.7 `CATALOG.md`, §4.8 additions (without the YAML modules bullet, which is DECL's), §4.10; the store fixes 1-6 of §6. Files: `packs/**` except the §4.11 rows | TIERS |
| **STORE-2b** | 1.5-2 | the deletions of §4.11. The `module.py`, `config.py` and `registry.py` rows go as a change request to the core agent | decision 13 (decided 2026-09-27: (a)); both repositories public and index.json rebuilt (§4.11 precondition); CORE-1d closed |
| **PORT** | 5 | §5.2-§5.6, with the round-trip pilot of §5.2 | HARNESS-v2 (the canonicaliser), STORE-2a |
| **DECL** | 3 | YAML modules (ARCHITECTURE.md §4.3 DECL, decision 2 (a)): the loader and its security rules (§4.8), marginal's conversion, YAML packs in `pack check`, `store build` (§4.4) and the scaffold, and `port translate`'s YAML output (§5.2) | CORE-1e, PATTERNS, MODSYS |

The registry repository, its CI and the validator group are the team's work, not a pytacheck package.

Hosting the public server (the image and host, a self-hosted or named GROBID, worker sizing against the Shiny app's slots, running alongside the Shiny server, the cutover) is not in these packages. Decision 20 (decided 2026-09-27: (a)) chooses the app; the cutover date and the user notice about the smaller module set (16 → 5) need the team (§8.1). The scienceverse Platform will take care of the public side and host metacheck and bibr as well (the maintainer, 2026-09-27). The Gradio app may then never need to replace the Shiny server, and the cutover and the notice would be the Platform's, agreed with the team. Each WEB item stays as planned until the maintainer re-scopes WEB (ARCHITECTURE.md §3.7).

### 8.1 Asks of the metacheck team

The design needs work or agreement from people outside pytacheck. The pytacheck maintainer sends each ask with the §3.1 draft and Q1-Q8 before TIERS closes.

| Ask | Needed by | If no, or not yet |
|---|---|---|
| Answers to Q1-Q8, including where the corpora live (Q8) | TIERS merge | the snapshot stays provisional (§2.3); badges say "based on the metacheck team's published counts (R version)" |
| A registry repository with the team's validators as CODEOWNERS (decision 14 (decided 2026-09-27: (a))) | first non-provisional snapshot | a pytacheck-owned registry file, with the same provisional badge wording; never "Validated by the metacheck team" |
| Validator names for pytacheck's own CODEOWNERS (§2.5), which the pytacheck maintainer then adds | before the FIDELITY sign-off lint is enforced | the entries name the maintainer, and a Band A change in a validated module waits for the team's written approval on the PR (FIDELITY.md §2.2, rule 1) |
| A named validator for the per-release rerun (§2.5) | the first pytacheck release after TIERS | the one-release grace of §2.5 step 4, then experimental |
| The R side of §7 | none; optional | pytacheck ships its own snapshot; metacheck keeps its `<validation>` blocks |
| Agreement to replace the public Shiny server, and a cutover date (decision 20 (decided 2026-09-27: (a))) | before the Gradio server goes public | the Shiny server stays in place; the Gradio app ships as the `pytacheck[app]` extra only. With the scienceverse Platform on the public side, the cutover would be the Platform's (§8) |

---

## 9. Decisions

Numbered as in ARCHITECTURE.md §6, which carries the full list. All 23 decisions there are decided (maintainer, 2026-09-27), all as recommended except 2, which takes (a), and 3, which takes (b). Neither is in this table, so every row here takes its recommendation: (a), and for decision 20 also uploads (i) and counts (i).

| # | Decision | Recommendation |
|---|---|---|
| 8 | The launch validated set (Q1). **Decided (maintainer, 2026-09-27): (a).** | the 5 team-validated modules (or 4, if the team drops one); the team decides. stat_check and ref_accuracy stay experimental until a team rerun on this implementation certifies them. Gates apply to every module the registry snapshot labels validated |
| 9 | Replace `metacheck::validated`. **Decided (maintainer, 2026-09-27): (a).** | yes, by the `validated` policy, with a deprecated alias until 1.0 (§3.2). The web app uses validated ∩ `server_safe` (16 → at most 5 modules); data_check becomes experimental |
| 10 | Default policies. **Decided (maintainer, 2026-09-27): (a).** | library and CLI `experimental`, which also runs modules from packs the user installed; web app and API `validated` |
| 11 | Field scope (Q5). **Decided (maintainer, 2026-09-27): (a).** | applies only when the preset declares `fields`: then a certification counts only for overlapping fields. With no preset fields it counts, and the badge names the corpus field |
| 12 | Lookup modules (Q6). **Decided (maintainer, 2026-09-27): (a).** | experimental until certified through `metrics` |
| 13 | Private stores. **Decided (maintainer, 2026-09-27): (a).** | make pytacheck and the store public, and fix store CI. Private packs and stores stay supported on GitHub only, through a token on the contents and tarball API (host allowlist, cross-origin strip and credential-free records kept); netrc, the git fallback and the GitLab and Codeberg forms go (−850 to −1,100 lines). The project-config code-trust gate stays. No fetch or auth deletion lands before the store is public |
| 14 | Where the registry lives. **Decided (maintainer, 2026-09-27): (a).** | a team-owned repository, with CODEOWNERS as the validator group; the same validators are code owners of the validated modules and their deviation rows in pytacheck (§2.5) |
| 17 | Consent for ports. **Decided (maintainer, 2026-09-27): (a).** | required for store listing, beyond what the licence requires |
| 18 | Inbound terms. **Decided (maintainer, 2026-09-27): (a).** | DCO, no CLA, for the pytacheck repository and packs kept in the store repository; pinned external packs rely on the licence check and consent record |
| 19 | Legal review. **Decided (maintainer, 2026-09-27): (a).** | counsel reviews §5.4, including its Uncertainties list, before third-party ports are listed |
| 20 | The web app. **Decided (maintainer, 2026-09-27): (a), uploads (i), counts (i).** | a Gradio app as `pytacheck[app]` on the API server, with analytics, run history and the public event API off; uploads kept up to ≈ 15 minutes; the Shiny app's anonymous usage counts kept (ARCHITECTURE.md §3.7). It replaces the public Shiny server only after the team agrees a cutover (§8.1). The scienceverse Platform will take care of the public side and host metacheck and bibr as well, and the Gradio app is a minimalist front end for now (the maintainer, 2026-09-27). Each WEB item stays as planned until the maintainer re-scopes WEB (ARCHITECTURE.md §3.7) |
