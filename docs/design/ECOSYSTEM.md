# Pytacheck ecosystem: validation status, presets, the module store and R ports

**Status.** Draft 1 (2026-09-26). Proposed; nothing here is implemented, in pytacheck or in the store repository `thesanogoeffect/pytacheck-modules`. It extends `docs/design/module-system-v2.md`, whose deferred item "multi-level trust tiers, a policy engine" (`:592`) it brings back as data, not as a policy engine. Legal statements in §5 are an engineering assessment, not legal advice.

**Evidence markers.** As in FIDELITY.md: **(M)** measured in this session (upstream metacheck at the harness pin; pytacheck at f559c1be; the store at 56ff399), **(E)** an estimate with its basis. The research notes behind this document are in `/tmp/claude-0/-home-user-pytacheck/b01f2e9f-255f-5b7e-9d0d-70c9d055b185/scratchpad/research/results/` (`tiers.md`, `market-gap.md`, `market-research.md`, `porting.md`).

---

## 0. Summary

Four questions, one answer each.

| Question | Answer | Where |
|---|---|---|
| **Who says a module is accurate?** | The metacheck validation team, in one registry file they own. Module authors cannot grant it; their own numbers show as "self-reported". A certification is bound to the exact code it measured | §2 |
| **What does a user run?** | A **preset** (what to check, often per field) filtered by a **status policy** (how much evidence to require): `validated` ⊂ `certified` ⊂ `experimental` ⊂ `all`. The library defaults to `experimental`, a public server to `validated` | §3 |
| **Where do modules come from?** | Authors' own repositories. The store is a reviewed list of pins with a generated catalog, like R-universe or a coding-agent marketplace, plus the one thing those lack: a measured-accuracy label | §4 |
| **How does the R community contribute?** | They write R, as they do now. `pytacheck port` translates an R module with the author's recorded consent and licence check, and a differential check against R decides whether the port is faithful | §5 |

**Two labels, never merged.**
- **Trust** says where the code came from and whether it is safe to run (builtin, store, unlisted, local). It exists today.
- **Status** says how accurate its flags are, as measured by the team (validated, external-validated, experimental, unreviewed, withdrawn). It is new.

A reviewed store pack can be experimental, and a validated module is still subject to the install consent card.

**Cost.** About 1,250 new lines across the status registry, the store additions and porting. Against that, 1,000-1,300 source lines and about 1,100 test lines can go from `packs/` (§4.11), so `packs/` ends up smaller than today. In agent-days: TIERS 2.5, STORE-2 3, PORT 5 (§8).

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
- 4 point to another tool's validation (coi_check, coi_check_oi, funding_check, funding_check_oi cite rtransparent's paper, which validates rtransparent, not metacheck's adaptation).
- 10 have no mention.

### 1.2 Questions for the validation team

These decide the launch list (§3.1). Pytacheck should record the team's answers, not infer them.

- **Q1. Which modules are the "around 4"?** Five have team-run count validations (power, marginal, stat_p_exact, stat_p_nonsig, stat_effect_size). stat_check's numbers come from the statcheck authors. No reading gives exactly 4.
- **Q2. The Shiny list is a server list, not a validation list.** Its 16 modules (`inst/app/report_app.R:162-166`, `shiny_app/app.R:236-241`) include 9 without evidence and data_check, which says it is not validated. pytacheck copies it as `metacheck::validated` (`src/pytacheck/modules/pack.json:66-86`).
- **Q3. "Instances" means three things** across the blocks. The template defines sensitivity as TP/instances (`inst/templates/_module.R:12-18`): 0.825 for power, against 0.902 from TP/(TP+FN). Structured counts remove the ambiguity.
- **Q4.** stat_p_nonsig's FN is implied, not stated.
- **Q5. Field scope.** Nearly all the evidence comes from Psychological Science. Does "validated" mean "validated for psychology"?
- **Q6. Lookup modules** (ref_retraction, ref_pubpeer, ref_replication) mostly report what a database says. Do they need a count validation, or a metrics-based one like ref_accuracy's?
- **Q7. Settings.** power can run with or without an LLM. Which setting do its numbers describe?

### 1.3 What pytacheck has today

- **Self-declared numbers look certified.** `ModuleSpec.validation` (`module.py:102-103`) is set by the author; none of the 30 built-ins sets it. `validation_metrics` derives PPV and sensitivity (`packs/manifest.py:170-180`), `store build` copies them into the index (`packs/build.py:116`), and `store search` prints "validated k/n" (`packs/stores.py:529-605`) whoever wrote them.
- **`pack check` tests presence only.** It warns "unvalidated" when there is no block (`packs/check.py:149-162`).
- **Trust labels cover provenance only** (`manifest.py:184-191`).
- **Reusable:** tree-bound reviews (`reviewed_tree_sha256`, `build.py:149-183`) and `Selection.dropped` with reasons (`presets.py:103-138`, `:496-504`).
- **`report()` has no `preset=`.** It hard-codes `DEFAULT_MODULES` (`report/report.py:61-78`, a copy of the default preset). The API falls back to every module when none is configured (`api/app.py:184-195`).
- **`init` asks for fields and presets, never for strictness** (`cli.py:685-713`).

### 1.4 The real store today

`thesanogoeffect/pytacheck-modules` at 56ff399 **(M)**:
- **Two packs.** `clinical_trials` (MIT, one module `trial_registration`, with tests) and `fields` (CC0-1.0, presets only: `general`, `psychology`, `medicine`, `open-science`).
- **CI fails on every PR.** `check.yml` installs pytacheck from git, and the repository is private, so it needs `PYTACHECK_READ_TOKEN`. That secret is empty.
- **It pins a stale feature branch.** `claude/pytacheck-metacheck-fork-0x7q73` (6cec4b56; main is 8d8a2364) is named at `check.yml:16`, `README.md:16,37` and `CONTRIBUTING.md:9,16`. pytacheck's own `packs/scaffold.py:23-26` writes the same pin into every new pack.
- **index.json is stale.** It was generated at 2026-09-24T21:00:27Z and lists clinical_trials 0.1.0 and fields 0.1.0; the pack.json files say 0.2.0 and 0.1.1.
- **Licence wording.** `REVIEW.md:22` and `CONTRIBUTING.md:23` allow CC0-1.0 for packs. CC0 is fine for preset-only packs, but it is not OSI-approved, so it should not be offered for code.
- **The medicine preset says** "add the clinical_trials pack" in prose, because pack.json cannot declare a pack dependency (§4.10).

---

## 2. Validation status

### 2.1 Labels

The registry stores certifications only. Every label is derived.

| Label | Derived as |
|---|---|
| `validated` | a team module with a certification that matches this code |
| `external-validated` | a module from outside the team with a certification that matches this code |
| `experimental` | a team module with no certification, or a stale one |
| `unreviewed` | a module from outside the team with no certification (local, unlisted and store packs alike) |
| `withdrawn` | a certification revoked for a known problem |

A **team module** is one whose pack is listed in the registry's `team_packs` (`metacheck`, plus any official scienceverse packs).

**Validated does not mean accurate.** It means the error rates are measured and published. marginal would be validated at PPV 0.63. The badge always shows the numbers (§3.6), so users judge the rate, not the word.

### 2.2 Policies

A policy is a named set of labels:

| Policy | Accepts |
|---|---|
| `validated` | validated |
| `certified` | validated, external-validated |
| `experimental` | certified + experimental |
| `all` | experimental + unreviewed |

`withdrawn` is never picked up from a preset. A comma-separated list of labels is also accepted, for anything the ladder does not cover.

This covers the three groups asked for: the team's validated modules, the team's experimental superset, and external modules the team has validated.

### 2.3 The registry

- **One file, `validation.json`,** in a repository the team owns, for example `scienceverse/metacheck-validation`.
  - CODEOWNERS is the validator group; every merge needs one validator's approval.
  - CI checks the schema, recomputes PPV and sensitivity from the counts, and checks that the referenced versions and tree hashes exist.
- **Both packages ship a snapshot at release:** `src/pytacheck/modules/validation.json` and metacheck's `inst/validation.json`.
- **An optional refresh** in config: `"validation_registry": "<url>" | "none"`, cached like the store index. If the registry is unreachable, the snapshot is used; a run never fails because of it.
- **The run record stores the registry digest**, so a report says which evidence it was judged against.

### 2.4 The evidence record

```json
{
  "schema": 1,
  "team_packs": ["metacheck"],
  "policies": {
    "validated":    ["validated"],
    "certified":    ["validated", "external-validated"],
    "experimental": ["validated", "external-validated", "experimental"],
    "all":          ["validated", "external-validated", "experimental", "unreviewed"]
  },
  "modules": {
    "metacheck::power": {
      "status": "validated",
      "implementations": {
        "r":  {"metacheck": ">=0.3.1", "file_sha256": "<64 hex>"},
        "py": {"pytacheck": ">=0.4,<0.6", "method": "rerun", "run": "<CI run URL>"}
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
        "settings": {"llm": "<as validated>"},
        "protocol": "<OSF or DOI>",
        "summary": "In a sample of 128 papers …"
      }]
    },
    "metacheck::ref_accuracy": {
      "status": "validated",
      "evidence": [{"id": "refacc-2026-08",
                    "metrics": {"flag_rate_genuine": 0.08, "recall_corrupted": 0.96},
                    "corpus": {"papers": 10, "fields": ["general"]}}]
    },
    "janedoe-psych::apa_df": {
      "status": "validated",
      "implementations": {"py": {"tree_sha256": ["<64 hex>"], "version": "1.2.0"}},
      "evidence": [{"id": "apa-df-2027-01", "counts": {"tp": 210, "fp": 21, "fn": 40}}]
    }
  }
}
```

- **PPV and sensitivity are never stored.** They are derived from `counts` (reusing `validation_metrics`), so they cannot disagree with them (Q3).
- **`metrics`** carries evidence that is not count-based, such as ref_accuracy's.
- **`settings`** records how the module was configured when measured. A run with other settings shows the status with a note ("validated without an LLM").
- `apa_df` is not in a team pack, so it derives as `external-validated`.

### 2.5 Binding a certification to code

A certification holds only for the code it measured.

| Implementation | Bound by | When the code changes |
|---|---|---|
| store and external packs | `tree_sha256` (`packs/tree.py:49`), the same rule as `reviewed_tree_sha256` | the label falls back to experimental or unreviewed, with "validated at 1.2.0 (tree ab12…), this is 1.3.0" |
| pytacheck built-ins | a pytacheck version range plus `method: "rerun"` | registry CI reruns the labelled corpus through `validate()` (`src/pytacheck/validate.py:302`). If TP, FP and FN stay within tolerance, it extends the range |
| metacheck (R) | a metacheck version range plus the `file_sha256` of `inst/modules/<name>.R` | falls back to experimental |

- Hashing one built-in file would be too narrow, since behaviour also depends on helpers and the core. Hashing the package would be too broad, since it changes every release. The rerun is what makes relaxed fidelity (FIDELITY.md) safe: **the Python port is certified on its own measured counts, not on its parity with R.**
- If the corpus cannot be redistributed, the team runs the rerun privately and commits the result.
- A Band A change in a validated module (FIDELITY.md §2.2) is therefore either signed off or re-validated. The two documents share this one rule.

### 2.6 Field scope

A certification counts only when the preset's `fields` overlap the evidence's `corpus.fields`, or the evidence says `general` (Q5). Otherwise the label reads "validated (psychology)", and under the `validated` policy the module is treated as experimental.

### 2.7 Self-reported numbers

An author's `validation=` numbers stay visible in `pack check`, `store search` and the catalog, marked "self-reported". They never grant a status. The evidence schema is the same, so a self-reported record can be promoted to the registry unchanged once a validator reproduces it.

---

## 3. Presets and status together

### 3.1 Launch statuses for the built-ins (draft for the team)

| Proposed label | Modules | Basis |
|---|---|---|
| validated | power, stat_p_exact, stat_p_nonsig, marginal, stat_effect_size | team-run counts (§1.1) |
| validated, pending Q1 | stat_check | team module whose evidence is external (Nuijten et al. 2017) |
| candidate via `metrics` | ref_accuracy | rates only, 10 papers (Q6) |
| experimental | the other 23, including data_check | no numeric evidence |

The team may cut this to "around 4". Whatever the answer, it is data in `validation.json`, not code.

### 3.2 How they combine

- **The preset says what to check.** Presets get an optional `fields` key (added to `PRESET_KEYS`, `manifest.py:57`), so `init` can offer them by field.
- **The policy says how strict to be.** It is applied after the preset is expanded and before offline dropping (`presets.py:452-505`).
- **Built-in presets:** `metacheck::default` and `metacheck::repository` stay. **`metacheck::validated` is removed**; the `validated` policy replaces it (Q2). `DEFAULT_MODULES` in `report.py:61-78` becomes a read of the default preset.
- **Field presets come from the store.** The existing `fields` pack is the seed: `fields::psychology`, `fields::medicine` and so on, maintained as reviewed lists with named maintainers (the CRAN Task Views model).

### 3.3 Setting it

```json
{"preset": "fields::psychology", "status": "validated"}
```

- **Precedence**, as for `preset` (`presets.py:424-442`): argument, then `pc.use(status=…)`, then `PYTACHECK_STATUS`, then project config, then user config, then the default.
- **Python:** `report(paper, preset="fields::psychology", status="validated")`; `select(..., status=)`; `pc.module_status("power")` returns `Status(label, evidence, stale_note)`; `pc.status_table()` returns one row per module with label, PPV, sensitivity, papers, fields and date.
- **CLI:** `pytacheck run paper.pdf --preset psychology --status validated`; `pytacheck modules --status certified`; `pytacheck status power` prints the evidence; `pack search` shows the certified label and marks self-reported numbers.
- **`pytacheck init`** keeps its field and preset questions and adds one: "How strict? 1. validated (6 modules in your presets) 2. certified 3. experimental 4. all". The counts come from the registry and the chosen presets.
- **API:** `/paper/check` takes `status`. The server's configured status is a ceiling: a request can be stricter, never looser.

### 3.4 Defaults

| Surface | Default | Why |
|---|---|---|
| library | `experimental` | All built-ins are team modules, so `report(paper)` and the parity goldens do not change. Unreviewed external modules pulled in by a preset are dropped |
| CLI | what `init` saved, else `experimental` | the same |
| web app and API server | `validated` | public users. The Shiny set of 16 becomes the validated ∩ server-safe set, 5-7 modules depending on Q1. The web app is the Gradio extra `pytacheck[app]`, mounted on the API server (ARCHITECTURE.md §3.7); the server's status is a ceiling for both |

### 3.5 Combinations that are not allowed

- **A preset entry below the policy** is dropped with a reason, e.g. `("repo_check", "status: experimental")`, next to today's `"offline"` reason. The CLI prints one line and the report lists what was left out.
- **Every entry dropped** raises `PresetError` before anything runs: "metacheck::repository has no modules at 'validated' (4 experimental: …); use --status experimental".
- **Modules named explicitly** (`modules=`, `-m`) run in the library and CLI with a warning and their badge. A server filters them and answers 400 with the list.
- **Withdrawn modules** are dropped under every policy; naming one explicitly warns loudly.

### 3.6 How the report shows status

- **A badge under each module title**, generated from the evidence:
  - "Validated by the metacheck team · PPV 0.91 · sensitivity 0.90 · 128 Psychological Science papers (2026-06) · protocol"
  - "External module, validated by the metacheck team · …"
  - "Experimental: error rates unknown" (the wording of `data_check.R:43`)
  - "Unreviewed external module"
- **The validation box** is generated from one sentence template, replacing the 8 hand-written prose blocks. This is a Band C change to the report (FIDELITY.md); module outputs do not change.
- **The summary table** gets a Status column.
- **The header** gets one line: "Ran 6 validated and 9 experimental modules (policy: experimental). Left out: …".

**Size (E).** 300-400 lines: the registry loader and labels ≈ 120, the `select` filter ≈ 30, CLI and `init` ≈ 80, report ≈ 50, tests ≈ 100. The basis is similar existing functions (`_review_fields` is 35 lines, the offline drop 9).

---

## 4. The store as a marketplace

### 4.1 What to copy from agent marketplaces

| Concept | DeepSeek Harness | Claude Code plugins, Agent Skills | pytacheck |
|---|---|---|---|
| Unit | an npm package declaring `"dsh": {"bundle": {"patch": …}}` | a plugin folder, or a `SKILL.md` folder with small frontmatter | a **pack**: `pack.json` plus `@module` `.py` files |
| What users start with | a **profile**, an ordered list of bundles (`dsh --profile web`) | bundle plugins through `dependencies` | a **preset** (`pack::preset`, with `extends` and `exclude`) |
| Install | `dsh plugin --profile P add github:owner/repo` | `claude plugin install name@marketplace` | `pytacheck pack install name`, or `owner/repo@rev` for unlisted packs |
| Catalog | the GitHub topic `dsh-plugin`; a community `marketplace.json` | `marketplace.json` in any repository | `index.json` built by store CI, a generated static site (§4.7), and a `pytacheck-pack` topic for unlisted packs |
| Pin | npm version; community catalogs require a SHA | `github{repo, ref, sha}` | `{github, rev}` with a 40-hex rev, plus `tree_sha256` |
| Vetting | none ("Review plugins … before allowing them to run") | none by default; admin allowlists | a tree-bound review, plus the status label (§2) |
| Governance | – | reserved names, `strictKnownMarketplaces`, cross-marketplace dependency allowlists | reserved names, per-store trust, the official store as the only certifier |
| Extension metadata | `package.json` | fixed keys plus a free `metadata` map | fixed pack.json keys; unknown keys pass through (`manifest.py:125-153`) |

The format is already close. pytacheck's store follows the same design: a CI-built index, sources pinned in the authors' repos, a tree hash, a consent card, an AST scan, and no code run while browsing (`module-system-v2.md:195-262`, `:438-487`). **What no agent marketplace has is the second axis:** measured accuracy. The closest precedent is Hugging Face's eval results, which tie a score to a benchmark revision and mark it verified or self-reported.

### 4.2 Principles

1. **Code stays in the authors' repositories.** The store is a list of pins (R-universe, pre-commit).
2. **Identity is not quality.** Trust and status are separate labels, and neither reads as the other (the VS Code "verified publisher" lesson).
3. **Evidence is data**, tied to a benchmark revision and a code hash (HF eval results, rOpenSci's versioned badges).
4. **One source of rules.** Store CI imports the installer's own checks (`pack check`), with no second copy (the DeepSeek Harness Desktop marketplace does the same).
5. **No popularity ranking and no open upload.** skills.sh ranks by install telemetry; on ClawHub, 341 of 2,857 skills were malicious (Feb 2026), and Snyk found flaws in 36.82% of 3,984 skills.
6. **R first.** R-only packs are listed, and ports carry recorded consent (§5).

### 4.3 The registry and the re-pin bot

- Keep `packs/<name>.json` with `{name, source: {github, rev}}` for packs in their authors' repositories.
- Add `track: "*release"` (R-universe's convention) or a branch name. A scheduled workflow opens a re-pin PR when the tracked ref moves. The PR clears `reviewed` and any tree-bound certification until the new tree is checked (the conda-forge autotick and `pre-commit autoupdate --freeze` model).
- A re-pin also reruns the AST scan and the pack's tests.

### 4.4 Index additions

`store build` derives or copies these into `index.json`:

| Key | Source |
|---|---|
| `publisher: "team" \| "external"` | the registry's `team_packs` |
| `status` per module | `validation.json`, honoured only from the certifier (§4.8) |
| `deprecated`, `successor`, `renames` | entry files (added to `_ENTRY_KEYS`, `build.py:54`) |
| `ported_from`, `consent.scope` | pack.json `ports.*` (§5.6) |
| `citation` | the pack's `CITATION.cff`, which the scaffold already writes |
| `maintainers` | pack.json, as GitHub handles (the nf-core convention) |
| `ci` | the nightly job (§4.6) |

### 4.5 Lifecycle

`experimental` → `validated` → `deprecated` → `yanked` or archived.
- A module whose nightly CI fails, or whose maintainer does not answer for one release, becomes deprecated (the Bioconductor rule).
- Renames go in a `renames` map, so pins and presets keep resolving (Claude's `renames`).
- **Yanks and decertifications reach installed packs.** At load time the cached index is checked, and a yanked or withdrawn module warns; `--strict` fails (`registry.py:511-552`). Today a yank affects only new installs.
- `pack list` shows available updates from the cached index.
- Archived entries keep their history and can be revived.

### 4.6 Nightly CI

The store runs `pack check` and each pack's tests on every listed rev each night, against pytacheck's main branch and its latest release. A failure is shown in the catalog and opens an issue on the store. This also gives pytacheck an early warning when a core change breaks a store module (G7 in ARCHITECTURE.md §4.1).

### 4.7 Static catalog

`pytacheck store site out/` (new `packs/site.py`, ≈ 200 lines) writes one index page, one page per pack and one per module: description, fields, status with PPV and sensitivity and a link to the evidence, CI state, review date and tree, licence, languages (R, Python), maintainers, preset membership and the install line. It also writes shields.io endpoint JSON for badges. A store workflow publishes it to GitHub Pages. There is no ranking by install count.

### 4.8 Security baseline

Keep the trust core, about 600 lines: the consent card, SHA and tree pins, read-only installs with a runtime integrity check, and the AST scan. Add:
- **Reserved names:** `metacheck`, `pytacheck`, `official*`, `scienceverse*`, and any non-ASCII or confusable name, accepted only from the official store.
- **A rescan on every re-pin.**
- **Per-store trust:** adding a store is explicit, and only the store named in `config.certifier` (default: the official store) can set `status`.
- **No hosted install.** The API never installs packs and runs with `use(allow_local=False)`, as today.

### 4.9 Federation, later

Labs and institutes may run their own stores. Allowlists (`stores.allow`) and a store's declared `allow_dependencies_from` come when a second real store exists, not before. Only the official store certifies.

### 4.10 Pack dependencies

`requires.packs: ["clinical_trials"]` in pack.json. `pack install` offers to install the missing packs through the same consent card. There is no version solving, only pins. The medicine preset then declares what it now says in prose.

### 4.11 What to delete from `packs/`

`packs/` is 5,361 lines, with 5,351 lines of tests **(M)**. It serves a store with two packs.

| Item | Lines | Where |
|---|---:|---|
| `auth.py`: token handling, netrc, a redirect-following HTTP client, a log-redaction filter. One `GITHUB_TOKEN` header does the job | 578 → ≈ 40 | `packs/auth.py` |
| The private-store fetch chain (contents API, raw, git fallback) | ≈ 73 | `stores.py:223-295` |
| The git fallback and `ls-remote` | ≈ 216 | `fetch.py:342-557`, `:559-605`, `:636-643` |
| Their tests | 765 + 327 | `tests/modsys/test_private_store.py`, `test_credentials.py` |
| `dist` packs (entry points) | ≈ 50 | `registry.py:304-327`, `install.py:834` |
| Legacy entry points and the `./modules/name.py` lookup | ≈ 30 | `module.py:446-553` |
| Project-config code trust | ≈ 123 | `config.py:210-300`, `:463-483`, `:559`; `install.py:672-704` |
| Path sources inside stores | ≈ 50 | `install.py:276-290`, `build.py:354-356` |
| GitLab, Codeberg, `file://` and `git+` ref forms | ≈ 40 | `stores.py:128-165`, `fetch.py:125-143`, `install.py:230-273` |
| Duplicates (`_read_record`, `_refuse_credentials`) | ≈ 25 | |
| `_card`, made table-driven | 103 → ≈ 50 | `install.py:353-455` |

**Total (E):** 1,000-1,300 source lines and about 1,100 test lines, 20-25% of `packs/`. Private stores become "clone it and use a path pin". This is a decision (§9, decision 13): private-store support was added deliberately in the store's last commit, so it goes only if the team agrees that a private lab store is a clone.

### 4.12 Net size

| Change | Lines (E) |
|---|---:|
| status (§2-§3) | +300 to +400 |
| `pack validate` plus the `validation/<module>/gt.csv` convention | +120 |
| `store site` | +200 |
| lifecycle, load-time warnings, update notices | +60 |
| `requires.packs` | +50 |
| deletions (§4.11) | −1,000 to −1,300 |
| **`packs/` and status together** | **≈ −300 to −600**, plus about −1,100 test lines |

---

## 5. Porting R modules

### 5.1 What already exists

- **A dictionary.** `porting/symbols.json` maps 1,127 R symbols, 295 of the 300 NAMESPACE exports among them, to Python paths. It records names only.
- **What modules use.** The 30 upstream modules call 162 distinct metacheck exports (scroll_table in 26 files, plural 18, text_search 13). External calls: `dplyr::` 307, `ellmer::` 54, `xml2::` 36, `stats::` 33 **(M)**. Across the mapped modules, 20,399 R lines became 27,414 Python lines (1.34x); small modules stay near 1:1 (marginal 79 → 84).
- **R execution.** `parity/r/run_cases.R` runs any module file through `do.call(module_run, …)` (`:220`), isolated: credentials unset (`:57-63`), a throwaway cache (`:64-70`), deterministic ids (`:90-105`), httptest2 mocks (`:209-215`). Upstream `module_run` accepts file paths (`R/module.R:15`, `:173-199`), so a third-party `.R` file runs unchanged.
- **Scoring.** `parity/accuracy.py:399-725` (`row_f1` `:553`, `score_output` `:604`) and `parity/canonical.py`.

### 5.2 `pytacheck port`

Four resumable subcommands, with their state in `port.json`.

**`port init`: the licence and consent gate, run first.**
1. Fetch the source through `packs/fetch.py`, pinned to a 40-hex rev, and hash the `.R` file.
2. Detect the licence as SPDX from LICENSE and DESCRIPTION. R's forms map as `GPL-2` → GPL-2.0-only, `GPL (>= 2)` → GPL-2.0-or-later, `MIT + file LICENSE` → MIT, `AGPL (>= 3)` → AGPL-3.0-or-later. Check it against the matrix (§5.4). The verdict is `ok`, `needs-notices`, `needs-relicence` or `blocked` (no licence, NC, or GPL-2.0-only).
3. Record consent (§5.6). If the porter is the repository owner or a DESCRIPTION `aut`/`cre`, it is self-consent. Otherwise `port init` prints an issue text for the author's repository and stops until there is an evidence URL.

**`port translate`: LLM-assisted.**
1. Extract the roxygen header into `@module` arguments, the `<validation>` prose into a draft evidence record (the author confirms it), and a census of the symbols the module calls.
2. Build a brief: the called symbols with their Python signatures (`inspect.signature`), the matching rows of `docs/PORTING.md` §3, one few-shot pair (marginal.R and marginal.py), and the relaxed rules of FIDELITY.md.
3. Write `<name>.py` next to the untouched `<name>.R`, a test file, and `ports.<name>` in pack.json.
4. Run on the demo paper and feed tracebacks back, for at most 3 rounds (E). The brief can also be written out for a coding agent instead.

**`port diff`: faithful or not.** R runs through a shipped copy of `run_cases.R` and `canonical.R`; the port runs through the canonicaliser (FIDELITY.md §4). The corpus is the demo paper, the 21 matrix papers, and `--corpus DIR` for the author's validation set.

| Output | Rule |
|---|---|
| run | both succeed or both fail, on every input |
| `traffic_light` | equal on ≥ 95% of inputs, each miss explained |
| `table` | row-multiset F1 ≥ 0.95 on the declared key columns; order, dtypes and declared renames ignored |
| `summary_table` | counts exact; other numbers within relative 1e-6 |
| `summary_text`, report | not compared; mismatched numbers warn |

Cost for 22 inputs (E, from `PERF_REPORT.md:30-35`): R ≈ 15 s, Python 1-2 s. `port.json` records both hashes, the corpus hashes, the metacheck rev, the scores and the verdict.

**`port finish`.** Runs `pack check`; refuses without complete consent and a passing diff; writes LICENSE and NOTICE with the original's notices; adds the header "Ported from <url>@<rev> (<licence>, © <authors>), <date>"; writes the store entry and a PR body with the licence verdict, the consent link and the agreement table.

**A passing `port diff` means "faithful port", not "validated".** A port of a validated R module keeps that status only through a rerun of the evidence corpus (§2.5).

**Size (E).** ≈ 1,180 new lines (licence 200, consent 120, extract 200, brief and loop 150, diff 250, enforcement 140, CLI 120) and ≈ 1,100 moved (the R runner and the scorer, extracted to `pytacheck.port`).

### 5.3 Running R directly

A persistent R worker (≈ 600 lines) is built **only** as the engine of `port diff`, plus an opt-in local `--allow-r` runner labelled "r-bridge (unported)" in reports. It is never enabled in the API, in hosted runs or in presets:
- R and a pinned metacheck add 0.5-1.5 GB to an image (E), and a second stack to patch.
- An `.R` module is `source()`d: arbitrary code, and `packs/scan.py` has no R counterpart.
- Warm, it costs 0.2-0.7 s per paper and module, against milliseconds in Python.

### 5.4 Licences

A translation is a derivative work (17 U.S.C. §101; EU Directive 2009/24/EC Art. 4(1)(b); GPLv2 §0). An LLM translation does not launder the original. Packs run in the same process as AGPL pytacheck, which the GPL FAQ calls "a single combined program", so a pack's licence must be GPL-compatible; the combination is AGPL-3.0-or-later, and the pack's own files keep their licence.

| Original | Ported into a pack | Notes |
|---|---|---|
| MIT, BSD | yes; pack may stay MIT | keep the notices |
| Apache-2.0 | yes | keep LICENSE and NOTICE, mark changed files; not GPLv2-compatible |
| GPL-2.0-only | **no** | needs a relicence |
| GPL-2.0-or-later | yes, as GPL-3.0-or-later | |
| GPL-3.0, AGPL-3.0 | yes | metacheck itself is AGPL (`DESCRIPTION:37`), so ports of its modules stay AGPL-3.0-or-later |
| CC BY 4.0 | yes, but ask for MIT | CC advises against CC licences for code |
| CC BY-SA 4.0 | uncertain | relicence or reject |
| CC NC or ND | **no** | not free |
| no licence | **no** | all rights reserved; needs a written grant |
| CC0 | presets only | not OSI-approved |

Notices to preserve: MIT's copyright and permission text, Apache's NOTICE and change notices, GPL/AGPL §5(a) ("prominent notices stating that you modified it, and giving a relevant date"), CC BY §3(a). The `.py` header plus `ported_from` covers them.

**Inbound terms: the DCO**, not a CLA. The store never relicenses ("You keep the copyright; the pack keeps your licence", `pytacheck-modules/CONTRIBUTING.md:71`). The DCO certifies only the submitter's own right to submit, which is why third-party ports need the consent record.

**AGPL §13 for hosted use.** A modified version must offer its Corresponding Source to remote users, and installed packs arguably count. Add `GET /source` (the pytacheck commit plus each active pack's `{source, rev, tree_sha256, license}`, which run records already hold) and a "Source" footer on reports. Hosted mode loads only store packs at public revs, so the offer can always be met.

**Uncertainties** for counsel (for example SFLC) before the store opens to third-party ports: whether 50-80-line modules made of `text_search` calls are original enough to be protected (assume yes); CC BY-SA into AGPL; GPL-3.0-only version locks; EU against US law.

### 5.5 Is consent needed at all?

Legally, code under a permissive or GPL-compatible licence can be ported with attribution alone. Consent is then courtesy. It is legally required only for unlicensed code and for licences that need a relicence. **The recommendation is to require it for store listing anyway** (decision 17): the community is small, the authors are identifiable, and a port published without asking is the fastest way to lose them. Unlisted ports (`owner/repo@rev`) need only the licence check.

### 5.6 The consent record

In pack.json under `ports.<module>`, not in `@module`, because consent governs distribution. `manifest.py:134` keeps unknown keys, so pack.json stays at schema 1.

```json
"ports": {"trial_registration": {
  "ported_from": {"source": {"github": "owner/repo", "rev": "<40 hex>", "path": "R/x.R"},
                  "sha256": "<of the .R file>", "license": "MIT",
                  "copyright": ["2025 A. Author"],
                  "authors": [{"name": "A. Author", "github": "aauthor", "orcid": "0000-…"}]},
  "consent": {"by": {"name": "A. Author", "github": "aauthor"},
              "role": "author|maintainer|rights-holder|self", "date": "2026-09-20",
              "scope": ["port", "publish-store", "host-api"], "license_granted": "MIT",
              "evidence": "https://github.com/owner/repo/issues/12#issuecomment-…",
              "tree_sha256": "<the tree consented to>"},
  "porter": {"github": "bporter"},
  "translation": {"tool": "pytacheck port", "model": "<provider/model>", "date": "2026-09-21"},
  "diff": "port.json"}}
```

**`store build --check` enforces it** (≈ 140 lines):
1. `license` must be valid SPDX and pass the matrix; today's warning (`check.py:279-285`) becomes an error for store entries.
2. A complete record is required for any `.R` file with a `.py` twin and for any `ports.*` entry. The `sha256` must match the `.R` file, and `rev` must be 40-hex.
3. Consent is required unless `role` is `self` and `by.github` owns the source. `evidence` must point into the source repository; with a token, its author must be the owner or a collaborator.
4. `scope` must include `publish-store`. Without `host-api` the entry gets `hosted: false`, and the API refuses the pack.
5. A changed tree since `consent.tree_sha256` warns reviewers; a changed licence or scope is an error until the author consents again.
6. The hashes in `port.json` must match the tree, and its verdict must meet §5.2's thresholds.
7. LICENSE (and NOTICE for Apache) must contain the `copyright` lines.
8. Commits carry a DCO sign-off (a new step in `check.yml`).

The install card gains one line: "ported from owner/repo (MIT); consent by @aauthor, 2026-09-20".

---

## 6. The real store: proposed fixes

None of these has been pushed; the store has no designated branch in this session.

| # | Fix | Where |
|---|---|---|
| 1 | **Make CI run.** Either make pytacheck public (recommended, and simplest for AGPL §13) or add the `PYTACHECK_READ_TOKEN` secret | store settings |
| 2 | **Re-pin** to pytacheck's main branch, or a tag, once `thesanogoeffect/pytacheck#1` merges. Make the scaffold derive its pin from the installed version instead of a constant | `check.yml:16`, `README.md:16,37`, `CONTRIBUTING.md:9,16`; pytacheck `packs/scaffold.py:23-26` |
| 3 | **Rebuild index.json** (the push-to-main job commits it once CI runs) | `index.json` |
| 4 | **Licence wording:** CC0-1.0 for preset-only packs; an OSI licence for code | `REVIEW.md:22`, `CONTRIBUTING.md:23` |
| 5 | **A validation section in REVIEW.md:** reviewers check code, not accuracy. Status requests go to the registry repository | `REVIEW.md` |
| 6 | **Three PR templates:** add or update a pack, port an R module (consent, licence verdict, `port diff` table), request a status (evidence record, corpus link) | `.github/PULL_REQUEST_TEMPLATE/` |
| 7 | **The fields pack:** `fields` keys on each preset; `requires.packs: ["clinical_trials"]` on `medicine` once §4.10 lands | `packs/fields/pack.json` |
| 8 | **Store CI additions:** the nightly job (§4.6), `store site` to Pages (§4.7), the DCO step (§5.6) | `.github/workflows/` |

Items 1-4 are fixes and can land now; 5-8 follow the pytacheck changes they depend on.

---

## 7. The R side

What metacheck needs so both packages read the same registry, in about 150 lines of R (E):

1. **Ship `inst/validation.json`** and add `module_status(module)` and `modules_at_status(policy)` (≈ 40 lines, jsonlite is already imported).
2. **Render the badge and evidence** in `module_report()` from the registry (`R/report.R:515-539`), falling back to `<validation>` blocks for unlisted modules.
3. **`report(..., status = "experimental")`**, and the same filter in `report_repository()`, with a `message()` listing what was dropped.
4. **The Shiny app** replaces `VALIDATED_MODULES` (`app.R:236-241`) and `validated_modules` (`report_app.R:162-166`) with `intersect(modules_at_status("validated"), <server-safe modules>)`.
5. **The same keys and hashes:** `metacheck::<name>`; a version range plus the sha256 of `inst/modules/<name>.R` (which needs `digest` moved from Suggests to Imports, or `openssl`).
6. **The preset lists** as `inst/modules/pack.json` (`module-system-v2.md:580-583`), so `report()` and the app share one list.
7. **The team process:** a named validator group and a written protocol (sampling, coding, the unit counted, how TP, FP and FN are defined), linked from each evidence record. This settles Q3 and Q4 for the future.

---

## 8. Sizes and work packages

| Package | Days (E) | Content | Depends on |
|---|---:|---|---|
| **TIERS** | 2.5 | `validation.json` snapshot, labels, policies, `status=` everywhere, `init`, report badges and box, removal of `metacheck::validated` and `DEFAULT_MODULES` | none; phase A. Owns `presets.py` before CORE-1d |
| **STORE-2** | 3 | §4.3-§4.8 and §4.10; the deletions of §4.11 if decision 13 agrees; the store fixes of §6 | TIERS |
| **PORT** | 5 | §5.2-§5.6 | HARNESS-v2 (the canonicaliser), STORE-2 |

The registry repository, its CI and the validator group are the team's work, not a pytacheck package.

---

## 9. Decisions

Numbered as in ARCHITECTURE.md §6, which carries the full list.

| # | Decision | Recommendation |
|---|---|---|
| 8 | The launch validated set (Q1) | the 5 team-validated modules, plus stat_check if the team certifies external evidence; the team decides |
| 9 | Remove `metacheck::validated` | yes. The web app uses validated ∩ server-safe (16 → 5-7 modules); data_check becomes experimental |
| 10 | Default policies | library and CLI `experimental`; web app and API `validated` |
| 11 | Field scope (Q5) | a certification counts only for overlapping fields |
| 12 | Lookup modules (Q6) | experimental until certified through `metrics` |
| 13 | Private stores | make pytacheck public, fix store CI, and replace private stores with "clone plus path pin" (−1,000 to −1,300 lines) |
| 14 | Where the registry lives | a team-owned repository, with CODEOWNERS as the validator group |
| 17 | Consent for ports | required for store listing, beyond what the licence requires |
| 18 | Inbound terms | DCO, no CLA |
| 19 | Legal review | counsel reviews §5.4 before third-party ports are listed |
| 20 | The web app | a Gradio app as `pytacheck[app]` on the API server, with analytics, run history and the public event API off; uploads kept up to ≈ 15 minutes; the Shiny app's anonymous usage counts kept (ARCHITECTURE.md §3.7) |
