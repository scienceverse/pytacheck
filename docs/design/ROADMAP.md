# Roadmap: metacheck for Python

Revision 1, 2026-09-29. Status: proposed. The open decisions are in §12.

This roadmap puts the work from the rest of phase A to 1.0 in order. It builds on `docs/design/ARCHITECTURE.md` revision 3 ("rev 3"), `FIDELITY.md` and `ECOSYSTEM.md`. Where it changes their order or scope, §2 says so. Work package ids (such as RENAME-1) are defined in §4's tables; §1 to §3 use them as names, with a gloss where it matters. Rev 3's wave packages (HARNESS-NET, REFS, LINKS, DATA-b and CODE-b) sit inside WAVES-PAPER and WAVES-REPO. Phase A PR ids (such as H-13 or V-5) are explained in Appendix A, and D01-D20 are the open decisions in §12. A package-day (pd) is one agent-day of implementation, including the PR's review round.

## 1. Goals and principles

### Goals

1. A researcher installs metacheck with one command, answers a few setup questions and checks a paper in a local window. After the install, no terminal is needed.
2. Every check tells users, in plain language, how well it has been measured. That means the sample size, the interval, the corpus, and whether the numbers were measured on this version.
3. The code gets smaller and easier to read, with fewer R idioms, so that a maintainer can find and understand every part of it.
4. As a technical design goal, there is one codebase in the long run. The Python package owns its expected outputs and its validation evidence. The R reference is frozen at a release agreed with the metacheck team.

### Principles

- **Validation first.** No change to what a validated check detects lands until there is a measurement that would show its effect. The validated checks are the five in the registry: marginal, power, stat_effect_size, stat_p_exact and stat_p_nonsig (decision 8).
- **Two kinds of evidence, kept apart.**
  - *Agreement with R* is what the parity harness (gate G1, 9,474 cases) and the accuracy gate (gate G2: 21 inputs and 10 repositories, 439 outputs) measure. It is sameness with R metacheck's outputs at a named R commit.
  - *Validation* is measurement against labelled ground truth. Only validation evidence can make a check "validated".
- **Honest labels.** Today's five registry entries with counts are provisional. They hold counts published for the R package, and their validators, date and protocol are not recorded. The labels say so until the checks are re-measured (D07).
- **Every change is recorded.** Decision 1 sets three bands:
  - Band A (results): locked (decision 1). Until the bridge (§5.4), a change in a validated check needs a deviation row, a validator's code-owner approval and, once a public labelled set exists, a passing re-measurement (D07; rev 3 asked for approval or re-validation).
  - Band B (order, types, number text): free.
  - Band C (wording): one scoped row per change.
- **Nothing is removed before the coverage ratchet (H-13) guards it.** The ratchet counts the realistic inputs the tests reach through public functions and fails when that count drops, unless each retired case names its replacement.
- **Aliases before removals.** The old names keep working: `pytacheck`, `PYTACHECK_*` and the `pytacheck` store id. They stay until the hosted deployments that use them have switched (§8).
- **Local by default.** Checks that run locally never need an account. Hosted services are opt-in.
- **Nothing here changes the R package.** The plan asks the metacheck team for data and answers, not for code changes.

### How the work is done

- Agents implement and the maintainer merges.
- Planned capacity is 3 agents on average and 4-5 at peaks. It never goes above rev 3's limit of 7.
- **Mechanical packages are implemented by a standard agent model.** These are renames, packaging, CI, docs, lints, UI strings, store tooling and most phase A PRs.
- **The difficult list is implemented by a stronger model and reviewed independently before merge.** The reviewer is a second agent that did not write the change, followed by the maintainer. The list:
  - the validation runner, the ground-truth gate, the bridge re-measurement, the R reference freeze and the per-field states (VAL-RUN, GT-GATE, VAL-BRIDGE, R-FREEZE and FIELD-STATES);
  - any change to the comparison rules (`parity/canon.py` or `parity/canon.toml`);
  - any Band A change in a validated check;
  - the rename's contract points in RENAME-1 to RENAME-3: import names, the entry-point groups `pytacheck.modules` and `pytacheck.packs`, the pack scanner's first-party and network prefixes, `requires`, the store id, format ids and the precedence of environment variables;
  - token handling in the settings store and in sign-in (SETTINGS, LOGIN and GUI-SIGNIN);
  - the PDF client's retry and token rules (EXTRACT-CLIENT);
  - the core's document and pattern code (CORE-1b).

## 2. What changes from ARCHITECTURE rev 3

### Order and scope

1. **The validation harness comes before the core rewrite.** Rev 3 ran phase A, then the core, then the waves. Here the ground-truth harness ships in 0.4.0 (VAL-FORMAT, VAL-RUN, GT-GATE, VAL-GATE, VAL-LLM), and the core (CORE-1) and the waves ship in 0.6 (D01).
2. **Product work comes before the core.** It covers:
   - the rename to `metacheck`;
   - a PyPI release pipeline;
   - a one-command installer;
   - a local GUI with setup questions;
   - an extraction client for bibr services.

   Before 1.0, rev 3 planned only the web app from this list. Its hosted pieces stay, as WEB-HOSTED in M8.
3. **Phase A is trimmed** from 82 PRs (254.75 h) to 55 active PRs (163.25 h): 12 done, 9 accelerated, 22 kept, 24 rescoped, 13 deferred into later packages and 2 dropped. See §10 and Appendix A.
4. **The gates are renamed and extended.** The accuracy gate (G2) is renamed "agreement with R", and a ground-truth gate (G2-gt) is added (FID-3). The parity harness stays G1.
5. **Validation is bound to the extractor.** Every run record names the PDF reader and its version (EXTRACT-PROV), and every evidence record says which extraction it covers.
6. **Size.** Rev 3's 1.0 target of about 93,300 source lines (range 88-97k) stands. §9 adds interim targets and ratchets.
7. **Total work** is about 236 pd to 1.0 in 82 packages (81 with their own days; V-1 is a phase A PR), plus about 15.5 pd after 1.0 (M11). Rev 3 planned about 129 pd in 36 packages, including its own phase A.

### Recorded decisions that change (ARCHITECTURE §6)

| Recorded decision | Change | Decision here |
|---|---|---|
| 3 (b): corpus mode in 0.6 | Postponed until after 1.0, together with PORT (rev 3's phase D). | D18 |
| 5 (a): phases A-D of §4.4 | Changed order: the rest of phase A (trimmed), then the validation harness and the product work (M1-M5), then the core and the waves (M8). Phase D's PORT waits (D18). The three stop points stay; stop point 2 moves from rev 3's day 11.5 to before CORE-1 starts. | D01 |
| 10 (a): the web app defaults to `validated` | For the local GUI, the setup choice decides: basic runs the validated checks, advanced the experimental ones. | D15 |
| 13 (a): GitHub-only sources | Changed in part. Public GitLab and Codeberg sources are kept; private packs stay GitHub-only; the other deletions of decision 13 (a) stay in STORE-2b (M8). | D20 |
| 15 (a): R names at the top level with a warning until 1.0 | A small top level from the first release. The R names live in `metacheck.compat`. | D12 |
| 16 (a): thin equivalents, `zenodo_upload` out | Extended. statout's HTML exporters also leave the package; the reproducibility check and statout's importers stay. | D19 |
| 20 (a): the web app | Amended. The app is first a local GUI that the installer opens, with basic defaults (D15) and a PDF-reader choice (D13). Its hosted pieces (mounting on the API server, the server ceiling and pack filter, upload retention (i), usage counts (i) and GET /source) stay, as WEB-HOSTED in M8. | D13, D15 |
| 21 (a): no directory moves | One move: `src/pytacheck` becomes `src/metacheck` with the rename. There are no other moves. | D04 |
| The "versions track R" rule (`pyproject.toml`, `CHANGELOG.md`) | Our own version line from 0.4.0. | D05 |
| Rev 3's permanent façades (§2.10, §4.5) | Façades that store packs and templates use stay in compat, with an end date reviewed at 1.0. The other façades, and the 89 exports nothing in `src` uses, warn from 0.5 (FACADE-SUNSET) and are removed no earlier than 1.0, after two minor releases of warnings (§3.6). | D12 |
| ECOSYSTEM §2.5: the one-release grace and "signed off or re-validated" | Until the bridge, provisional entries get no grace, and a Band A change needs a validator's sign-off and, where a public labelled set exists, a passing validation subset. | D07 |
| ECOSYSTEM §2.5 step 3: the rerun tolerance | D08's rule replaces it once the metacheck team agrees. | D08 |
| FIDELITY §7: the daily upstream sync | It ends when the R reference is frozen, at a release agreed with the metacheck team. | D11 |

These stay as recorded:
- Decision 5's three stop points (stop point 2 now comes before CORE-1; see the row for decision 5): after the second core prototype (SPIKE-2, written up in PA-SPIKE); before the core rewrite starts, on the deviation rows and the team's answers; and at the CORE checkpoint when the core's run-and-settings part (CORE-1d) closes.
- Decision 2 (c): the no-code module form waits until a store author or the porting translator needs it.
- Decisions 22 (a2) and 23 (a), which govern CACHE and FETCH.
- The phase A answer on duplicate paragraphs: a paragraph repeated word for word counts once. It lands with CORE-1b's V7 dedup (§5.4).
- All other recorded decisions.

## 3. Milestones

Dates are planning targets for this project's own releases. A simulation at 3 agents on average ended each milestone's work before its target: M3 around 11 November, M4's app lane around 6 November and M8 around mid-January. The simulation predates this revision's additions (about +5 pd, mostly in M1, M5, M7 and M8), so it is re-run before the dates are relied on. The difference is contingency for review rounds, the stop points and testers' feedback.

### M0: wave 1 closed (by Wed 30 Sep), 1.35 pd

- **Users see:** nothing. main is green with every wave 1 PR merged. The last, #14 (H-4's work counters), merged on 28 Sep, and main's CI, Parity and Docker runs on it (00ef3a21) are green.
- **Packages:**
  - WAVE1-CLOSE: the merges are done (the last, #14, on 28 Sep); what is left is deleting the merged wave 1 branches, which are still on origin.
  - V-1: CODEOWNERS for canon, the registry and the validated checks' modules, and for the deviation and accuracy-row folders ahead of V-6 and H-3spec. It merged as #25, before RENAME-1 moves those paths.
  - REF-ARCHIVE: record the digest of the pinned R reference image once it is archived in a second location.
  - DOCS-ROADMAP: this roadmap and the design-doc pointers.
- **Status on 29 Sep:**
  - Merged: #20 (V-2), #21 (C-S1's follow-ups: `store add` and `store remove` lose `--project`, and an ignored project store no longer breaks `init` or `config`), #22 (S-4's follow-up: names that end in a newline are refused), #24 (the first part of BIBR-12X-a), #25 (V-1), #26 (TRUST-1), #27 (a follow-up to #24: a log that cannot be written never fails the caller), #28 (this roadmap), #29 (H-11), #30 (H-14), #31 (`lastlog()` returns `None` for a log that cannot be created) and #32 (a store pack reports the source in its install record, not its pin's).
  - Store: pytacheck-modules#4 (store CI on GitHub-hosted runners) and #5 (no read token) are merged.
  - Open: #23 (SERVE-AUTH, which waits until the operators of hosted services have been told) and #33 (H-11's follow-up: the `file_location` ignores in `mod_repo_check` go, and the area is re-locked).
- **Exit criteria:**
  - main CI is green.
  - CODEOWNERS covers `canon.toml` and the five validated checks.
  - The R golden job's time is recorded: about 40 minutes on a GitHub-hosted runner (measured on #14 and on main), within its 120-minute limit.
  - The image digest is recorded in `parity/UPSTREAM.toml`.
  - The decisions marked "now" (D04 and D07) are answered.

### M1: internal build on TestPyPI (target Fri 9 Oct), 23.5 pd

- **Users see:** `metacheck` installs from TestPyPI on Linux, macOS and Windows. `import pytacheck` and the `pytacheck` command still work.
- **Rename barrier:** the rename runs in the first days of October.
  - A committed script (`scripts/rename_to_metacheck.py`) does it. The script can be re-run, so an open branch rebases by re-running it.
  - From RENAME-1's start until RENAME-3 merges, no other PR touches a file the rename script rewrites.
  - Before it starts, the PRs that go before it (V-1, DOCS-ROADMAP, BIBR-12X-a, H-11, V-2, H-14, SERVE-AUTH and TRUST-1) are merged or parked, and the author of open PR #6 is told. V-1, V-2 and TRUST-1 have merged.
- **Packages:** BIBR-12X-a, BIBR-12X-b (once bibr releases 12.x), FID-3, SERVE-AUTH, TRUST-1, RENAME-1 to RENAME-4, STORE-CI-0, REG-RENAME, API-SLIM (moves names only), PA-STATUS, OFFLINE-REQ, PKG-1, PKG-2, CONSTRAINTS, REL-PIPE, EXTRACT-PROV, SIZE-GUARD, DOCKER-1 and DOCS-DECISIONS.
- **Exit criteria:**
  - The parity, snapshot and accuracy gates are green after the rename, and no lock digest moved.
  - The old names are still read, with a deprecation note.
  - Store CI runs on GitHub-hosted runners and passes against metacheck built from main.
  - A TestPyPI wheel installed into a fresh environment with the release's constraints file passes the accuracy gate, run from the checkout.
  - The install matrix passes on 3 OSes.

### M2: public pre-release 0.4.0a1 (target Wed 14 Oct)

- **Users see:** the library and the CLI, from `uv tool install metacheck` or `pip install metacheck`. The 0.0.1 placeholder is deleted after the upload, so both find the pre-release; `pip install --pre metacheck` works either way.
  - Inputs: bibr export 12.x JSON, GROBID TEI XML, and PDFs through a bibr 12.x server the user runs. Today only a bibr built from its main branch writes 12.x.
  - Reports show each check's status badge with the provisional wording.
- **Gate:** before the tag, the metacheck team has seen the release wording (the status note, the README and the credit). If not, the release stays on TestPyPI.
- **Exit criteria:**
  - Published through the release pipeline, with attestations and the constraints file.
  - The 0.0.1 placeholder is deleted.
  - On a clean machine, `uv tool install metacheck` and `pip install metacheck` install 0.4.0a1, and neither installs the placeholder.
  - The release notes name the R commit that agreement is measured against.

### M3: 0.4.0, phase A closed and the validation harness in place (target Fri 13 Nov), 38.25 pd

- **Users see:**
  - `metacheck validate` computes PPV and sensitivity, with Wilson intervals, against labelled data.
  - Status appears in plain language in the CLI and in reports.
  - An unannounced preview of the app and the installer goes to invited testers.
- **Packages:** PA-ORACLE, PA-SPIKE (stop point 1 is at its write-up), PA-STORE, VAL-FORMAT, GT-PILOT, VAL-RUN, GT-GATE, VAL-GATE, VAL-LLM, EXTRACT-AGREE and STATUS-UX.
- **Exit criteria:**
  - Every one of the 55 active phase A PRs is merged or moved by name.
  - The pilot gate passes: the ground-truth gate runs end to end in CI on the pilot set. This is plumbing only; the pilot set produces no badge or evidence.
  - A PR that touches a validated check's code runs the validation subset. Until GT-OPEN's public set exists, that run is on the pilot set and checks the tooling only; the merge rule is a validator's sign-off (§5.5).
  - The accuracy matrix has extractor-agreement rows.
  - Store CI passes on GitHub-hosted runners against the latest metacheck pre-release (CI-5), and a pack in its author's own repository is tested at its pinned rev (S-8) and listed in CATALOG.md (S-7).

### M4: 0.5.0b1, installer and GUI public beta (target Fri 4 Dec), 28.5 pd

- **Users see:** one command installs metacheck and opens the local GUI, whose first page asks the setup questions. A user uploads a paper and downloads the report.
- **Depends on, outside this repository:** for PDFs, the hosted service accepting keys created on its web page, or a bibr release that writes 12.x. Without either, the beta reads exports and, by opt-in, a GROBID server, and its release notes say so.
- **Packages:** APP-SVC, SETTINGS, EXTRACT-CLIENT, ENGINE, SETUP, GUI-LOCAL, PLAIN, A11Y, GUI-GATE, INSTALLER, INSTALL-CI and KEY-LOGIN.
- **Exit criteria:**
  - Installer CI is green on Ubuntu, macOS arm64 and Windows, for a fresh install, a re-run, an upgrade, an uninstall, a run behind a proxy and a run with no network.
  - The one command changes no shell profile and ends with the GUI answering on 127.0.0.1 and showing its first-run page.
  - INSTALL-CI's upgrade leg goes from release X to release Y with Y's constraints file, and settings and stored keys survive it. Uninstalling leaves nothing the user did not choose to keep.
  - From a fresh install on each OS, the GUI reads a PDF through every PDF path setup offers.
  - The GUI and the CLI give identical results and run-record digests on the accuracy corpus.
  - A nightly logging-proxy check sees no third-party host from the GUI's start, its first-run page, a demo run or sign-in. It runs on the newest Gradio 6.x before the constraints file moves.
  - axe reports 0 serious or critical issues on the GUI and a sample report in both themes. The keyboard-only end-to-end run and the contrast test pass, and the accessibility statement is published.
  - A manual NVDA and VoiceOver pass is done.

### M5: 0.5.0, faster repository checks (target about Fri 18 Dec), 19.5 pd

- **Users see:**
  - Repository checks are faster on a cold run, and warm re-runs are near-instant. Today a cold data_check on the demo paper takes 72.9-76.0 s.
  - data_check's FAIR concepts carry measured numbers with a clear label.
  - Façades and R-shaped names that are due to go warn in `metacheck.compat` (FACADE-SUNSET).
- **Packages:** BATCH-a, FETCH, CACHE, FAIR-VAL, CODEMAP, STORE-FORK and FACADE-SUNSET.
- **Exit criteria:**
  - FETCH and CACHE meet decisions 22 (a2) and 23 (a), with OSF's limits enforced.
  - FAIR-VAL's numbers are in the registry with their label.
  - The code map is offered to the R package's authors for review, and their comments are folded in when they arrive.
  - `src` is within §9's 0.5 cap.

### M6: bridge re-validation (when the metacheck team's labelled data is available; no date)

- **Users see:** the five validated checks re-measured on this implementation. Each check either keeps "validated", now with measured numbers, or shows "measured below the published rates" and runs as experimental.
- **Packages:** VAL-BRIDGE, then FIELD-STATES. GT-OPEN runs alongside once coders are named.
- **Exit criteria:**
  - The validators have signed the evidence records.
  - The tolerance follows D08.
  - The results are published with the R commit, the extractor and the corpus.

### M7: hosted PDF reading and sign-in (when the scienceverse-hosted bibr service and the scienceverse identity broker with ORCID sign-in are live; no date)

- **Users see:** they can sign in with ORCID, from the CLI or the GUI, and have their PDFs read by the hosted service.
- **Packages:** LOGIN, GUI-SIGNIN, DRIFT-GATE and HOSTED-DEFAULT.
- **Exit criteria:**
  - Sign-in and token refresh work on 3 OSes, from the CLI and from the GUI.
  - The drift gate runs on every hosted bibr upgrade.
  - Setup preselects the hosted reader only after the bridge's bibr arm is within tolerance.

### M8: 0.6, the core and the waves (planning target February 2027), 95 pd

- **Users see:** the same results from a smaller, faster core. This is rev 3 §4.3's rewrite.
- **Packages:** CORE-1, WAVES-PAPER, WAVES-REPO, SERVICES-WAVE, MODSYS-COMPAT, WEB-HOSTED, STORE-2b, CAPS, CURATE-1, DEDUP-IDIOMS, ONE-RDS and R-ONLY-FEATURES.
- **Exit criteria:**
  - Stop point 2 is held before CORE-1 starts, and stop point 3 at CORE-1d's close.
  - Every Band A change in a validated check has its deviation row, its sign-off and a passing validation subset.
  - CURATE-1's policy (who curates, the status policy each preset declares, and review) is recorded in ECOSYSTEM.md.
  - Size is within §9's 0.6 target.

### M9: R reference frozen (at a release agreed with the metacheck team)

- **Users see:** nothing. The test suite stops needing an R installation.
- **Packages:** R-FREEZE, R-TOOLS-OUT, CASE-PRUNE and LLM-REKEY.
- **Exit criteria:**
  - The expected outputs are owned by this repository.
  - The image stays archived, so goldens can be regenerated.
  - The R package's authors have confirmed that the code map covers what the porting map did.
  - `tests` is within §9's 55-65k lines.

### M10: 1.0 (planning target Q1 2027)

- **Requires** M6 (the bridge) and a validator rerun on the 1.0 release candidate. It does not wait for M9.
- **Packages:** CLOSE, A11Y-AUDIT and DEPRECATE-END.
- **Exit criteria:**
  - The API stability promise is published.
  - Deprecations whose window ends at 1.0 are removed.
  - The accessibility statement is updated.
  - `src` is within §9's 1.0 target (89-93k lines).

### M11: after 1.0, about 15.5 pd

- Corpus mode (BL-8) and PORT, the R-module translator (D18).
- GUI-PACKS (the store in the GUI), PRESETS-INSTALL (curated field presets in setup) and LAUNCHER-SHORTCUT (a desktop shortcut).
- STORE-VAL (validation data in store packs).
- **Exit criterion:** every preset that setup offers declares a status policy that CURATE-1 approved.

The no-code module form is not scheduled. It comes when a store author or the porting translator needs it (decision 2 (c)), before or after 1.0; rev 3's re-plan sizes it at about 3 pd.

## 4. Work packages

- **Validation role:**
  - Evidence: produces measurements.
  - Guard: stops unmeasured changes.
  - Record: records what was run.
  - Shows: presents status to users.
  - "—": none.
- **Model:**
  - S: implemented by a standard agent model.
  - S+: implemented by a stronger model, with an independent review.
- **Depends on** lists packages and the decisions (D01-D20) a package waits for.

### M0

| Id | What | Days | Depends on | Validation role | Model |
|---|---|---:|---|---|---|
| WAVE1-CLOSE | Done: #14 (H-4's work counters) merged on 28 Sep (00ef3a21), and main's CI, Parity and Docker runs on it are green. Left: delete the merged wave 1 branches | 0.1 | — | — | S |
| V-1 | CODEOWNERS for `canon.py`, `canon.toml`, `validation.json` and the validated checks' modules, and for the `parity/deviations/`, `parity/accuracy/matrix.d/` and `parity/accuracy/expected.d/` folders, listed before V-6 and H-3spec create them (phase A, 0.5 h). Merged as #25, before RENAME-1 moves the paths. It only requests reviews until a ruleset on main requires code-owner review and the owner is not the PR's author; until then a Band A change waits for the maintainer's explicit approval in the PR | — | — | Guard | S |
| REF-ARCHIVE | Record in `parity/UPSTREAM.toml` the digest of the pinned R reference image, once the maintainer has archived it in a second location | 0.25 | the archived image (maintainer) | Record | S |
| DOCS-ROADMAP | This roadmap as `docs/design/ROADMAP.md`, with Appendix A, and the design-doc updates that no other PR owns (ARCHITECTURE §4.3-§4.6 pointers, the §5.3 ledger, the ECOSYSTEM lines, the UPSTREAM_ISSUES entry) | 1 | — | — | S |

### M1

| Id | What | Days | Depends on | Validation role | Model |
|---|---|---:|---|---|---|
| BIBR-12X-a | Make bibr export schema 12.x the pinned contract. Input in 11.x gets a plain error that names the schema found and the bibr version needed (PyPI bibr 0.5.1 writes 11.0). Fixtures come from a 12.1 export made by bibr's main branch at a pinned commit, and unknown enum values are logged. Until bibr publishes a release that writes 12.x, the `[bibr]` extra is marked unavailable in the docs and kept out of `[all]` and the no-compiler matrix. Fixes BIBR.md's paper_write advice. The existing test job already runs the bibr12 and io tests, so no new CI job is needed. #24 (merged) was the first part: the error, the log of new values, fixtures from bibr's own schema examples and the BIBR.md fix. It changed only the error text, because main already refused 11.x input. Left for the second part: fixtures from a pinned bibr export. The `[bibr]` extra is not marked unavailable and stays in `[all]`: bibr 0.6.0 (PyPI, 30 Sep) writes 12.1, and BIBR-12X-b requires it. Before the second part merges, the export schema the hosted bibr service writes is confirmed with the bibr maintainers | 1 | — | Record | S |
| BIBR-12X-b | Raise the `[bibr]` extra's floor to the first bibr release that writes 12.x, with an upper bound, and re-lock. The floor is bibr 0.6.0, which writes 12.1, and the lock has moved to it (it brings in pyarrow, so the columns that hold file names that are not valid UTF-8 use Python-backed strings). Left: the upper bound (`<0.7`: bibr 0.6.0 moved the schema from 11.0 to 12.1 in a minor release) | 0.25 | a bibr release on PyPI that writes 12.x (external): bibr 0.6.0 | — | S |
| FID-3 | Fidelity contract v3, in FIDELITY §5 (the gates) and §0 and in ARCHITECTURE §1.1 and §4.1: G2 renamed "agreement with R"; G2-gt added; R treated as a reference frozen at an agreed release. The CI job and the docs text are renamed to match | 1.5 | DOCS-ROADMAP, D08, D11 | Guard | S |
| SERVE-AUTH | `metacheck serve` without a key binds only to loopback. A non-loopback host without a key is refused unless `--behind-authenticating-proxy` says a proxy or gateway in front authenticates every request. With a key of 32 or more characters, every route except /health needs a Bearer header. #23 does this and reads the key from `PYTACHECK_API_KEY`; RENAME-3 adds `METACHECK_API_KEY`, which then comes first. Breaking for serve on a non-loopback host without a key or the flag: the Dockerfile's documented run and the compose file are updated, and compose publishes bibr's port on 127.0.0.1 only. Not in #23: Host and cross-site checks for serving on loopback without a key, and a CHANGELOG line for the breaking change. They are added to #23 before it merges, or follow in a second PR before RENAME-1 starts. #23 merges after the operators of hosted services have been told | 0.5 | — | — | S |
| TRUST-1 | Give trust=store only to pins the store index lists (store, name, rev and tree hash); add a test with a spoofed project pin. Merged as #26 | 0.75 | — | Guard | S |
| RENAME-1 | Distribution and import `metacheck`, and `src/pytacheck` moved to `src/metacheck`, by a re-runnable script. Keeps a `pytacheck` import alias (its own top-level module) and command. Adds `requires.metacheck`, with `requires.pytacheck` still read. The entry-point groups `pytacheck.modules` and `pytacheck.packs` are still read and get no `metacheck.*` twin, because STORE-2b removes them (decision 13 (a)). The `pytacheck_packs` import root, `.pytacheck-install.json` and the `__pytacheck_module__` names resolve in both forms. The pack scanner treats `metacheck.*` and `pytacheck.*` alike as first-party, and flags both forms of `.http`, `.llm`, `.db` and `.archives`, with a test. Not renamed: the parity id seed `pytacheck-parity-<n>`, the `pytacheck-parity-cache` and `-data` folders (in 7 goldens), `TREE_ALGORITHM` `pytacheck-tree-v1`, the platformdirs folder names (RENAME-3), the environment variable reads (the `PYTACHECK_*` names and the four `METACHECK_*` names already read, which RENAME-3 orders; a blind rewrite would turn today's `PYTACHECK_*`-then-`METACHECK_*` fallbacks into duplicate lookups), the repository URLs, `porting/` and the history in `docs/design`. The scaffold's install spec becomes `metacheck @ git+https://github.com/scienceverse/pytacheck@main`, and a store PR switches the store's install spec the same day. CODEOWNERS paths move with the tree | 3 | D04; V-1, DOCS-ROADMAP, BIBR-12X-a, H-11, V-2, H-14, SERVE-AUTH and TRUST-1 merged or parked; PR #6 merged or its author told | Guard (no lock digest moves) | S+ for contract points |
| RENAME-2 | Store id `scienceverse`, with `pytacheck` read as an alias in pins and install records. Reserves the metacheck\* and pytacheck\* prefixes. A store PR renames store.json's and index.json's name, moves clinical_trials to `requires.metacheck` and check.yml to `METACHECK_*` | 1 | RENAME-1, RENAME-3 | — | S+ |
| RENAME-3 | Config, data and cache folders (the store cache included) separate from R metacheck's. `METACHECK_*` names come first (with the one exception below), and the `PYTACHECK_*` names read in the source at that point (17 today, plus those of open PRs that have merged) are still read, with a documented precedence. Four `METACHECK_*` names are already read today, and three of them are also R metacheck's own (§8): `METACHECK_LLM_CACHE_DIR` keeps ranking below `PYTACHECK_LLM_CACHE_DIR`, so R's setting does not override the Python one; `METACHECK_LLM_MODEL` and `METACHECK_LLM_MAX_CALLS` keep their meaning; `METACHECK_EMAIL`, which R does not read, moves ahead of `PYTACHECK_EMAIL` like the new names. Format ids are readable in both forms: `RUN_SCHEMA` and the run record's keys, where the `pytacheck` (version) and `metacheck` (R reference) fields become `version` and `r_reference`. The option key `pytacheck.careless` gets an alias. `metacheck.json` follows C-S1's rules (a project file cannot add stores) | 1.5 | RENAME-1 | — | S+ |
| RENAME-4 | Update the scaffold, templates, README, CONTRIBUTING and store docs. The scaffold's install spec follows S-2's rule: the git source at main for .dev builds and `metacheck>=<version>` for release builds, so nothing points at PyPI before 0.4.0a1 | 1 | RENAME-2, RENAME-3 | — | S |
| STORE-CI-0 | The store's check.yml runs on GitHub-hosted runners, so PA-STORE's PRs get CI; fork PRs stay refused until STORE-FORK | 0.25 | — | — | S |
| REG-RENAME | The registry's implementation key comes from the distribution name, so R and Python implementations are told apart. Adds the provisional wording (D07), optional ORCID iDs for validators and the FAIR-VAL label field | 0.75 | RENAME-3, D07, D10 | Shows | S |
| API-SLIM | The M1 top level: `Paper`, `read`, `status` and `status_table` (the status submodule becomes `metacheck.registry`, so that `status()` can sit at the top level), plus today's `module_run` and `use` until CORE-1 adds `check`, `Settings`, `configure` and `hosts`. The other R-shaped names move to `metacheck.compat`, and the parity cases that call R-shaped top-level names (on main at 00ef3a21: 199 as their `py` target, and about 520 including calls inside `$expr` code) are re-pointed there by a scripted rewrite, with no digest change. Nothing is dropped yet; `import pytacheck` keeps today's names | 1.5 | RENAME-1, D12 | — | S |
| PA-STATUS | Phase A's status PRs, one PR each: T-2, T-6, T-4, C-1, T-5 and the status part of T-3. T-6 reads status from the registry snapshot and does not wait for T-4. T-4 merges after the operators of hosted services that call the API have been told | 2.5 | RENAME-1, D01, D07 | Shows | S |
| OFFLINE-REQ | codebook_check declares `requires=["network", "llm"]`, as psychds_check does, so an offline selection drops it with a reason. ref_accuracy is dropped with a reason when the CrossRef match is off. Both checks are experimental; CORE-1d later derives these needs from `depends=` | 0.5 | RENAME-1 | Guard | S |
| PKG-1 | Packaging fixes: pyyaml in the core dependencies; an `[app]` extra (`gradio>=6.28,<7`); `[all]` from wheels only; `__main__.py`; main carries `0.4.0a1.dev0` (D05); NOTICE and CITATION credit; project URLs. The author list is unchanged until the team answers (D05). The README's install lines stay git-based until 0.4.0a1 is on PyPI; the switch goes in the release PR | 0.5 | RENAME-1, D05 | — | S |
| PKG-2 | Install-matrix CI on 3 OSes with Python 3.12 and 3.13, plus 3.14 on Linux. Adds a no-compiler check, a size budget for the basic install, and a weekly fresh-resolution job that runs the accuracy gate | 1 | PKG-1 | Guard | S |
| CONSTRAINTS | A constraints file for `metacheck[app]` per release, Python and OS, because `uv tool install` ignores `uv.lock`. The built wheel is installed with it into a fresh environment, and the accuracy gate and a parity smoke run use that environment from the checkout, since the parity package and the accuracy corpus are not in the wheel. `uv tool install -c` is smoke-tested for the CLI | 1.5 | PKG-1, D16 | Guard | S |
| REL-PIPE | `release.yml` requires the tag commit's parity and accuracy jobs. Adds a TestPyPI path, which stamps a unique `.devN` before building, and a release checklist, and keeps the PEP 740 attestations; the constraints file and the wheel are attached to the GitHub release. Needs the maintainer's trusted publishers and environments | 1.5 | CONSTRAINTS, PKG-1, D16 | Guard | S |
| EXTRACT-PROV | A per-paper extraction record in the run record: engine, producer name, version and build, export schema version, source hash, request options and validation counts, with a note in the report. The note is Band C: it is kept out of the output the report parity cases compare, or added with a scoped wording row. The warning for a validated check run on an extraction its evidence does not cover comes with VAL-FORMAT and VAL-GATE | 1.5 | RENAME-3 | Record | S |
| SIZE-GUARD | Record lines per package; growth fails without a justification line. A lint refuses new private R-idiom helpers outside `_values` and `_r` | 0.5 | RENAME-1 | — | S |
| DOCKER-1 | Image `ghcr.io/scienceverse/metacheck`, with the user, paths, environment and healthcheck renamed. Until 1.0 it also pushes the `pytacheck` tags, with a deprecation label, so old pulls keep getting updates. The `-bibr` variant is dropped until a bibr release writes 12.x. The Docker Hub image of the same name belongs to the R package and is not touched | 0.5 | RENAME-1 | — | S |
| DOCS-DECISIONS | ARCHITECTURE §6 decisions 24 on, for the answered D items, with the supersede marks | 0.5 | RENAME-3, the answers to D01-D20 | — | S |

### M3

| Id | What | Days | Depends on | Validation role | Model |
|---|---|---:|---|---|---|
| PA-ORACLE | Phase A's oracle, canon, harness and CI PRs (Appendix A lists them) | 12.25 | RENAME-1 (except H-11, V-2 and H-14, which go before it), D01, D03 | Guard | S; S+ for canon (V-4, V-5, V-10) |
| PA-SPIKE | SP-2, SP-3 (which also measures the bibr 12.x JSON reader path), SP-4 and D-2 (the SPIKE-2 write-up; stop point 1) | 2.75 | — | — | S |
| PA-STORE | Phase A's store PRs as rescoped: CI-5, S-3 and S-6 to S-13b | 2.75 | RENAME-4, STORE-CI-0 | — | S |
| VAL-FORMAT | The validation data format. `validation/<check>/manifest.toml` holds the corpus, licence, sampling, unit definition, protocol, coders and agreement, and extraction pipeline and version. `labels.jsonl` is keyed by paper plus located text, using `canon.toml`'s row keys. Public and private sets share one schema for built-in checks and packs | 2 | RENAME-1, D03, D09 | Evidence | S |
| GT-PILOT | A pilot labelled set on about 10 open-access papers. Agents prepare it from the unit definitions in the R package's documentation, and the maintainer checks it. Plumbing only, never evidence or a badge | 1.5 | VAL-FORMAT | Evidence (plumbing) | S |
| VAL-RUN | `metacheck validate` runs checks on full papers from frozen exports, optionally end to end through bibr. It computes confusion counts by unit key, PPV and sensitivity with Wilson intervals, and per-field breakdowns, and writes evidence records. It reuses the accuracy scorer | 3.5 | VAL-FORMAT | Evidence | S+ |
| GT-GATE | The ground-truth gate (G2-gt): runs VAL-RUN in CI on the public sets and fails outside tolerance. It is a separate job from agreement with R | 4 | VAL-RUN, PA-STATUS, GT-PILOT, D08 | Guard | S+ |
| VAL-GATE | Re-measurement triggers. A PR that changes a validated check's code closure (H-14's import graph, pattern files or data) runs the public subset. The release job reruns the private set or records the validator's run. Adds a "changed since validation" badge | 2.5 | GT-GATE, PA-ORACLE, D08 | Guard | S |
| VAL-LLM | A settings digest (model, prompt, schema) for power and the other LLM checks. A model or prompt change shows "setting not confirmed" until re-measured. Adds power's non-LLM row to the regression report | 1.5 | REG-RENAME, PA-STATUS | Evidence, Shows | S |
| EXTRACT-AGREE | Extractor agreement. bibr 12.x extractions of the 8 upstream papers that ship PDFs become accuracy rows (BIBR-ROWS), with R goldens from the pinned image, produced on a GitHub-hosted job with bibr pinned (bibr's main branch at a commit until a release writes 12.x). It compares the validated checks' outputs on GROBID TEI and on bibr 12.x. None of the 8 is a Psychological Science paper, so this measures agreement between extractors, not the published rates | 3 | EXTRACT-PROV, BIBR-12X-a | Evidence | S |
| STATUS-UX | Status in the CLI and report (and later the GUI) in plain language: the label, PPV, sensitivity, sample size, Wilson interval, corpus, field, date and the provisional note. It never implies that "validated" means "accurate" | 2.5 | PA-STATUS, REG-RENAME | Shows | S |

### M4

| Id | What | Days | Depends on | Validation role | Model |
|---|---|---:|---|---|---|
| APP-SVC | One application service for the CLI, the API and the GUI: a request becomes a job with progress, cancel and plain-language errors, and the job produces a report | 3 | RENAME-1 | — | S |
| SETTINGS | A validated settings schema and a secret store: the OS keyring, with a 0600-file fallback, and environment variables take precedence. Keys that can redirect data are user-scope only | 2.5 | RENAME-3 | — | S+ for token handling |
| EXTRACT-CLIENT | An HTTP client for bibr serve and the hosted service: /ready, and jobs with polling. Rules: follow Retry-After on 429; 409 while running; 404 after expiry; retry transient 5xx; stop on 401 or 403. A token is refused over plain HTTP to a non-loopback host. It keeps an adapter for the hosted service's job API | 2.5 | RENAME-3, BIBR-12X-a, D13 | Record | S+ for retry and token rules |
| ENGINE | One PDF-reader setting: hosted, a URL, a local bibr (offered once a bibr release writes 12.x), an opt-in GROBID server, or none. There is no silent fallback, and when the server is down the error names the setup command. Using GROBID asks for consent in one sentence. The extractor is recorded | 2 | EXTRACT-CLIENT, SETTINGS, D13 | Record | S |
| SETUP | Setup questions defined as data. The GUI's first-run page shows them, `metacheck setup` asks the same questions in the terminal, and flags or environment variables answer them for scripted installs. The questions: basic or advanced; PDF reader; online lookups; contact email; AI features; strictness; API keys (an AI provider key when AI features are on, with provider and region notes; the hosted service's key, through KEY-LOGIN's paste field, shown only when the hosted reader is chosen; optional GitHub and OSF tokens in advanced mode), all stored through SETTINGS. Includes connection tests. Setup offers no field preset beyond `metacheck::default` until curated presets exist. `metacheck setup` absorbs `init` | 4 | SETTINGS, PA-STATUS, ENGINE, OFFLINE-REQ, D15 | Shows | S |
| GUI-LOCAL | `metacheck app`: the local Gradio GUI (`gradio>=6.28,<7`). It starts on 127.0.0.1 on a free port with a per-launch token and a Host check, opens the browser itself, always prints the URL and reuses a running instance. The fixed settings of ARCHITECTURE §3.7 that apply locally hold, each with a unit test: `GRADIO_ANALYTICS_ENABLED=False` before import, `run_history=False`, `enable_monitoring=False`, `mcp_server=False`, `ssr_mode=False`, `api_visibility="private"` with `queue(api_open=False)`, `max_file_size="50mb"`, the `file=` URL guard, and never `share=True`. Concurrency stays at 1 until per-run LLM settings exist. The About page shows the installed version; only when online lookups are on does it say that a newer release exists, with the update command | 4 | APP-SVC, SETTINGS, STATUS-UX, VAL-LLM, D15 | Shows | S |
| PLAIN | All GUI and report strings in one catalogue; "checks", not "modules"; badge sentences generated from the evidence | 1 | APP-SVC | Shows | S |
| A11Y | WCAG 2.2 AA fixes and gates for the GUI and the report: the tabs pattern, labels, live regions, contrast and focus. Link contrast is 2.41:1 today. Tests: axe through Playwright in both themes on the GUI and a sample report, keyboard-only end-to-end runs, a contrast unit test on the report's CSS tokens, and 200% zoom and 320 px reflow. A manual NVDA and VoiceOver pass, and a published accessibility statement | 3 | GUI-LOCAL, D17 | — | S |
| GUI-GATE | The GUI and the CLI give identical results and run-record digests on the accuracy corpus; a nightly end-to-end run | 1 | GUI-LOCAL | Guard | S |
| INSTALLER | `install.sh` and `install.ps1`, published as GitHub release assets. Each installs a pinned uv, checked by checksum, into its own folder without editing shell profiles. It then runs `uv tool install --managed-python --python 3.12 'metacheck[app]==X' -c <constraints for X>`, where X is the script's own release, and starts `metacheck app`. The terminal asks nothing, and flags cover scripted installs. `metacheck update` reinstalls the newest release with that release's constraints file (`uv tool install --force`), never a bare `uv tool upgrade`. An uninstall path (`install.sh --uninstall`, `install.ps1 -Uninstall` and `metacheck uninstall`) removes the tool and the uv the installer placed, and asks before it removes settings, caches and the keys in the secret store. A scheduled job bumps the uv pin | 3 | REL-PIPE, CONSTRAINTS, GUI-LOCAL, SETUP, D16 | — | S |
| INSTALL-CI | The installer matrix on Ubuntu, macOS arm64 and Windows: fresh install, re-run, upgrade (X to Y with Y's constraints file), uninstall, a proxy, and no network | 2 | INSTALLER | — | S |
| KEY-LOGIN | `metacheck login --key`, and the same paste field on the GUI's first-run and Settings pages, store a key created on the hosted service's web page in the secret store | 0.5 | ENGINE, SETTINGS, D14 | — | S |

### M5

| Id | What | Days | Depends on | Validation role | Model |
|---|---|---:|---|---|---|
| BATCH-a | The first part of rev 3's batch engine (the rest, BATCH-b, is in SERVICES-WAVE) | 3 | PA-ORACLE | Guard (batch equality) | S |
| FETCH | Repository fetching (`REPO_FETCH.md`) | 4 | BATCH-a | — | S |
| CACHE | The local CacheStore of decision 22 (a2), with the request ledger of decision 23 (a) | 3 | BATCH-a | — | S |
| FAIR-VAL | data_check's FAIR data and code concepts measured at check level, on labelled repositories held out from the classifier's evaluation sets. Registry label: "measured on N labelled repositories, not a random sample, not certified" | 4.5 | VAL-RUN, REG-RENAME, D10 | Evidence | S |
| CODEMAP | `docs/CODEMAP.md`: each check, package and R function mapped to its Python location, in plain language, offered to the R package's authors for review | 1.5 | RENAME-1 | — | S |
| STORE-FORK | Fork-PR checks for packs in their authors' repositories, on GitHub-hosted runners with no secrets; the read-token path is removed | 1.5 | RENAME-4, D20 | Guard | S |
| FACADE-SUNSET | Keep the façades that store packs and templates use, in compat, with an end date reviewed at 1.0. Deprecate the other façades and the 89 exports nothing in `src` uses, with warnings from 0.5, so that two minor releases warn before 1.0 (§3.6), and re-point their parity cases | 2 | API-SLIM, PA-ORACLE, FID-3, D12 | Guard | S |

### M6, and the open ground-truth set

| Id | What | Days | Depends on | Validation role | Model |
|---|---|---:|---|---|---|
| VAL-BRIDGE | Re-measure the five validated checks on this implementation with the team's labelled data, in two arms: the inputs the published figures used, and bibr 12.x extractions. Compare with the published figures under D08 | 4 | GT-GATE, EXTRACT-AGREE, VAL-LLM, the team's data | Evidence | S+ |
| FIELD-STATES | Read bibr 12.1's per-field states, so that a field that failed or was not attempted gives "not checked" instead of a false negative. This is a Band A change for validated checks | 1.5 | VAL-BRIDGE, BIBR-12X-a | Guard | S+ |
| GT-OPEN | An open labelled set on open-access papers, coded by coders the metacheck team names. About 2 pd of preparation, plus coding time | 2 | VAL-FORMAT, GT-PILOT, D09 | Evidence | S |

### M7

| Id | What | Days | Depends on | Validation role | Model |
|---|---|---:|---|---|---|
| LOGIN | `metacheck login`, `logout` and `whoami` against the scienceverse identity broker with ORCID sign-in. Uses authorization code with PKCE and a loopback redirect, or the device grant when there is no browser. Handles refresh. Tokens live in the secret store and are redacted in logs and records | 3 | EXTRACT-CLIENT, SETTINGS, D14, the broker | — | S+ |
| GUI-SIGNIN | A "Sign in with ORCID" button on the first-run page (only when a hosted reader is chosen) and on the Settings page. It runs LOGIN's flow in a new browser tab, because ORCID pages cannot sit in an iframe | 1.5 | LOGIN, SETUP | — | S+ for token handling |
| DRIFT-GATE | On each hosted bibr upgrade or 12.x minor: extract the gate corpus through the hosted service, run the validated checks and compare with the last certified run. Band A differences go to a validator | 2 | EXTRACT-CLIENT, EXTRACT-PROV, EXTRACT-AGREE | Guard | S |
| HOSTED-DEFAULT | Setup preselects the hosted reader | 0.5 | VAL-BRIDGE, KEY-LOGIN, DRIFT-GATE, D13 | Guard | S |

### M8

| Id | What | Days | Depends on | Validation role | Model |
|---|---|---:|---|---|---|
| CORE-1 | The core rewrite of rev 3 §4.3 (CORE-1a to CORE-1e): a Doc built once per paper, facets and dict outputs. CORE-1b's V7 dedup applies the duplicate-paragraph answer (a paragraph repeated word for word counts once), with deviation rows for stat_p_exact and stat_p_nonsig and a validator's sign-off | 13 | PA-ORACLE, PA-SPIKE, CODEMAP, stop point 2 | Guard (differential suites against snapshots) | S; S+ for CORE-1b |
| WAVES-PAPER | The paper-side waves of rev 3 §4.3 on the core, with the deferred phase A rows they need: H-3d's REFS rows, H-3e, NET-2 to NET-7 (rev 3's HARNESS-NET, the network-recording harness, before REFS and LINKS), V-12 and V-13 | 17 | CORE-1, VAL-GATE | Guard | S; S+ for Band A changes in validated checks |
| WAVES-REPO | The repository-side waves (data_check, code_check and the archive hosts; rev 3's DATA-b and CODE-b), with H-3d's repository rows | 31.5 | CORE-1, FETCH, CACHE | Guard | S |
| SERVICES-WAVE | The LLM client, lookups and the other services (decision 16: LLM-a, LLM-b, SERVICES-a, SERVICES-b), and the rest of rev 3's batch engine (BATCH-b without corpus mode, 6 pd) | 16.5 | CORE-1 | Guard | S |
| MODSYS-COMPAT | The module system on the core; compat holds the R-shaped names and the façades | 4 | CORE-1, API-SLIM | — | S |
| WEB-HOSTED | The hosted pieces of rev 3's WEB on the same app: mounted at /app with `metacheck serve --app`; the server's status ceiling (validated ∩ server_safe) and the hosted pack filter (store packs at public revs only); a per-job wall-clock limit; the privacy text with upload retention (decision 20, uploads (i)); the anonymous usage counts (counts (i)); GET /source and the visible "Source code" link (AGPL §13) | 2 | CORE-1, GUI-LOCAL, PA-STATUS (T-4) | Shows | S |
| STORE-2b | ECOSYSTEM §4.11's deletions under decision 13 (a), except the public GitLab and Codeberg forms (D20): netrc and the extra HTTP client, the git fallback and ls-remote, dist and legacy entry-point packs, path sources in stores, and the `file://` and `git+` forms. Private GitHub stores keep the token path | 2 | CORE-1, PA-STORE, D20 | — | S |
| CAPS | Per-service capability data, generated privacy text, and "skipped because" reasons | 2 | CORE-1 | — | S |
| CURATE-1 | Design of curated presets: who curates, the required status policy for each preset, and review | 1 | STATUS-UX | — | S |
| DEDUP-IDIOMS | Replace the 247 private R-idiom helpers (about 1,900 lines in 82 files) with the shared helpers, with byte-identical parity. The validated checks' code closure waits until after the bridge | 3 | SIZE-GUARD, PA-ORACLE | Guard | S |
| ONE-RDS | One R serialisation reader | 1 | PA-ORACLE | — | S |
| R-ONLY-FEATURES | Re-home `zenodo_upload` (1,228 lines, no callers) and statout's HTML exporters; retire their cases through the H-13 ratchet | 2 | PA-ORACLE, D19 | Guard | S |

### M9

| Id | What | Days | Depends on | Validation role | Model |
|---|---|---:|---|---|---|
| R-FREEZE | Freeze the R reference at the agreed release: the goldens become expected outputs owned here, the daily upstream sync stops and the image stays archived | 3 | VAL-BRIDGE, PA-ORACLE, D11 | Guard | S+ |
| R-TOOLS-OUT | Move the upstream-sync tooling, the porting data and the R harness out of the default path, once the R package's authors confirm that the code map covers what the porting map did | 1.5 | R-FREEZE | — | S |
| CASE-PRUNE | Under H-13: retire rcompat (174 cases) and about 984 private-helper cases, re-point 514 parser cases and consolidate the review areas. The validated checks' cases are kept | 4 | R-FREEZE | Guard | S |
| LLM-REKEY | Re-key recorded LLM replies by what the request means; a JSON cache instead of `.rds` | 2 | R-FREEZE, VAL-LLM | Guard | S |

### M10

| Id | What | Days | Depends on | Validation role | Model |
|---|---|---:|---|---|---|
| CLOSE | Rev 3's CLOSE | 2.5 | WAVES-PAPER, WAVES-REPO, SERVICES-WAVE, MODSYS-COMPAT, VAL-BRIDGE | — | S |
| A11Y-AUDIT | An accessibility re-check on the 1.0 release candidate, and an update to the statement | 1 | A11Y | — | S |
| DEPRECATE-END | Remove what was deprecated with a window that ends at 1.0 (two minor releases of warnings): the `metacheck::validated` alias, the sunset façades, and the 89 exports nothing in `src` uses (3,229 lines), the last only after the metacheck team has said which R-named functions their scripts use. The `pytacheck` command and import, the `PYTACHECK_*` names and the `pytacheck` store id are not removed on the 1.0 date alone: they stay until the hosted deployments that use them have switched, with an end date agreed with their operators and published in advance | 1 | MODSYS-COMPAT, CLOSE, FACADE-SUNSET | — | S |

### M11 (after 1.0)

| Id | What | Days | Depends on | Validation role | Model |
|---|---|---:|---|---|---|
| BL-8 | Corpus mode (rev 3's BL-8: many papers in one pass), with its batch-equality tests | 3 | SERVICES-WAVE (BATCH-b's engine), WAVES-PAPER, D18 | Guard (batch equality) | S |
| PORT | The R-module translator (ECOSYSTEM §5); decisions 17 and 19 (consent and legal review for ports) apply again | 5 | PA-ORACLE, PA-STORE, D18 | Guard | S |
| GUI-PACKS | The store in the GUI: reviewed modules only, the consent card as a modal, trust and validation badges, update and remove | 2 | GUI-LOCAL, TRUST-1 | Shows | S |
| PRESETS-INSTALL | Setup offers curated field presets that meet CURATE-1's policy; basic mode stays validated-only; fields without a preset say that the general preset is used | 1 | CURATE-1, SETUP | Shows | S |
| LAUNCHER-SHORTCUT | A desktop shortcut that starts `metacheck app` | 2 | INSTALLER | — | S |
| STORE-VAL | A validation/ folder for pack repositories; `pack check` recomputes self-reported metrics, and a team rerun gives external-validated | 2.5 | VAL-RUN, PA-STORE | Evidence | S |

### Totals

| Milestone | Package-days |
|---|---:|
| M0 | 1.35 |
| M1 | 23.5 |
| M3 | 38.25 |
| M4 | 28.5 |
| M5 | 19.5 |
| M6 with GT-OPEN | 7.5 |
| M7 | 7 |
| M8 | 95 |
| M9 | 10.5 |
| M10 | 4.5 |
| **Total** | **about 236** |

M11 adds about 15.5 pd.

## 5. Validation plan

### 5.1 Where we stand

The registry (`validation.json`) is provisional. Of its 30 entries, five carry counts, built from the `<validation>` blocks in R metacheck 0.3.1's documentation; two more (stat_check and ref_accuracy) carry pending or candidate notes. For the five, the validators, date, protocol and unit definition are not recorded. The intervals below are 95% Wilson intervals computed from the published counts.

| Check | Papers | Corpus | TP / FP / FN | PPV | Sensitivity |
|---|---:|---|---|---|---|
| marginal | 51 | not recorded | 38 / 22 / 27 | 63% (51-74%) | 58% (46-70%) |
| power | 128 | Psychological Science | 203 / 21 / 22 | 91% (86-94%) | 90% (86-93%) |
| stat_effect_size | 161 | Psychological Science | 1,106 / 45 / 23 | 96% (95-97%) | 98% (97-99%) |
| stat_p_exact | 225 | not recorded | 269 / 78 / 136 | 78% (73-82%) | 66% (62-71%) |
| stat_p_nonsig | 194 | not recorded | 1,486 / 153 / — | 91% (89-92%) | not published |

Agreement with R is not the same as these rates.
- Of the accuracy gate's 21 inputs, only 5 are Psychological Science papers.
- The validated checks already differ from R on real papers. For example, on one Psychological Science paper, all_p_values and stat_p_exact find 37 p-values where R finds 36.

### 5.2 Two kinds of gate

**Agreement with R.** It covers:
- G1, the parity harness (9,474 cases);
- G2, the 439-output accuracy matrix, renamed "agreement with R" by FID-3.

Both are measured against the R commit pinned in `parity/UPSTREAM.toml` (today b239264f: 0.3.1 development plus upstream PR 423). Any statement of agreement names that commit.

**G2-gt, ground truth** (GT-GATE). It covers:
- PPV and sensitivity on labelled sets;
- a tolerance per check (D08).

It runs as its own CI job, and the first run is on the pilot set.

**Re-measurement triggers** (VAL-GATE, VAL-LLM, DRIFT-GATE):
- a change to a validated check's code closure, pattern files or data;
- a change to an LLM check's model, prompt or schema;
- a new extractor version, meaning a new bibr build or 12.x minor;
- a dependency change in the constraints file that touches the closure.

### 5.3 Harness sequence

1. VAL-FORMAT defines the data format.
2. GT-PILOT exercises the plumbing on about 10 open-access papers. It is never used as evidence (D09).
3. VAL-RUN computes the measures.
4. GT-GATE and VAL-GATE enforce them.
5. VAL-LLM binds LLM settings.
6. EXTRACT-PROV and EXTRACT-AGREE bind the extractor.
7. GT-OPEN adds an open, publishable set once coders are named. It feeds CI's public subset.

### 5.4 The bridge (VAL-BRIDGE)

- "The bridge" is one planned step: re-measuring the five validated checks on the Python code with the metacheck team's labelled papers. It has no date because it waits for that data.
- When the data is available, the five checks are re-measured on this implementation in two arms:
  - the inputs the published figures were measured on;
  - bibr 12.x extractions of the same papers.
- The published figures do not record the R version or the PDF pipeline, so the first arm is what separates implementation differences from extractor differences.
- Outcomes:
  - Within tolerance: the check keeps "validated", with measured numbers and the validators' sign-off.
  - Outside tolerance: the check drops to experimental and shows "measured below the published rates".
- FIELD-STATES changes validated checks' results, so it waits until after the bridge.
- The duplicate-paragraph rule (phase A answer, 2026-09-28) lands with CORE-1b's V7 dedup, with deviation rows for stat_p_exact and stat_p_nonsig and a validator's sign-off.

### 5.5 Rules for changes to validated checks

- Until the bridge, a Band A change needs all of:
  - a deviation row;
  - a validator's code-owner approval (until a ruleset enforces code-owner review and validators are named, the maintainer's explicit approval in the PR);
  - once GT-OPEN's public set exists, a passing validation subset. Runs on the pilot set only check the tooling and never count as evidence (D09);
  - for the difficult list, an independent review.
- CODEOWNERS covers `canon.py`, `canon.toml`, `validation.json` and the validated checks' modules (V-1, #25). It also covers the `parity/deviations/`, `parity/accuracy/matrix.d/` and `parity/accuracy/expected.d/` folders, listed before V-6 and H-3spec create them.
- Canon changes follow D03. The flip audit (V-4) makes sure canon cannot hide a Band A change.
- DEDUP-IDIOMS and other clean-ups exclude the validated checks' code closure until after the bridge.

### 5.6 What users see

- Badges show:
  - the label;
  - the sample size;
  - the Wilson interval;
  - the corpus;
  - whether the numbers were measured on this version.
- Provisional entries carry this wording from the first public build (D07): "Error rates as published in the documentation of R metacheck 0.3.1. The R version and PDF pipeline they were measured with are not recorded. Not yet re-measured on this version."
- Until the bridge, the one-release grace of ECOSYSTEM §2.5 does not apply to provisional entries (D07). A Band A change in a validated check gets its sign-off, and passes the subset once a public set exists, before it merges.
- Release notes say the outputs are "identical to the previous release's snapshots except the listed deviation rows".

### 5.7 FAIR data and code concepts (FAIR-VAL)

- data_check's concept classifier (open PR #6) is evaluated at concept level, on 98 labelled boxes and 86 out-of-distribution repositories. FAIR-VAL instead measures at check level, on repositories held out from those evaluation sets.
- It records the result with the label "measured on N labelled repositories, not a random sample, not certified" (D10).
- data_check stays experimental (decision 9).

## 6. The installer and the GUI

- **One command.**
  - Final releases on Linux and macOS: `curl -LsSf https://github.com/scienceverse/pytacheck/releases/latest/download/install.sh | sh`.
  - On Windows: `powershell -ExecutionPolicy ByPass -c "irm https://github.com/scienceverse/pytacheck/releases/latest/download/install.ps1 | iex"`.
  - Both scripts are GitHub release assets, and each pins its own release (`==X`).
  - GitHub's `releases/latest` skips pre-releases, so the 0.5.0b1 beta's command uses the tag URL: `https://github.com/scienceverse/pytacheck/releases/download/v0.5.0b1/install.sh` (and `install.ps1`).
- **What the installer does:**
  1. Downloads a pinned uv release archive into a metacheck folder and checks its SHA-256 (`sha256sum`, or `shasum -a 256` on stock macOS; `Get-FileHash` on Windows). It edits no shell profile or user PATH (with astral's own installer, `UV_UNMANAGED_INSTALL` would do the same).
  2. Runs `uv tool install --managed-python --python 3.12 "metacheck[app]==<this release>" -c <this release's constraints file>` by full path. `uv tool install` ignores `uv.lock`, so the constraints file is what pins the tested dependency set.
  3. Asks nothing in the terminal. It starts `metacheck app`, and the setup questions (SETUP) are the GUI's first-run page. `metacheck setup` stays available to terminal users.
  4. Takes flags or environment variables for scripted installs: a version, no launch, a folder, and proxy and certificate settings (`UV_SYSTEM_CERTS`). The script body is one function, so a truncated download runs nothing.
  5. Updates: re-running the installer, or `metacheck update`, reinstalls the newest release with that release's constraints file (`uv tool install --force 'metacheck[app]==Y' -c <constraints for Y>`), never a bare `uv tool upgrade`.
  6. Uninstalls: `install.sh --uninstall`, `install.ps1 -Uninstall` or `metacheck uninstall` removes the tool and the uv the installer placed, and asks before it removes settings, caches and the keys in the secret store.
- **Size.** The basic install downloads about 130 MB and takes about 390 MB on disk. PKG-2 sets a budget.
- **Setup questions** (SETUP) are defined as data. The GUI's first-run page shows them, `metacheck setup` asks them in the terminal, and flags answer them for scripted installs:
  - basic or advanced;
  - how to read PDFs (D13);
  - online lookups;
  - contact email;
  - AI features (off by default);
  - strictness;
  - API keys: an AI provider key when AI is on, the hosted service's key when the hosted reader is chosen, and optional GitHub and OSF tokens in advanced mode (D14).
- **Basic mode** (D15) runs the validated checks only, with AI off. Power then runs without AI, and its badge says the setting behind the published rates is not confirmed for this run. Advanced mode adds experimental checks and the AI options. Until curated presets exist, setup offers no field preset beyond `metacheck::default`.
- **The GUI** (GUI-LOCAL):
  - Gradio pinned to `>=6.28,<7`.
  - It starts on 127.0.0.1 with a per-launch token, opens the browser itself, prints the URL and reuses a running instance.
  - ARCHITECTURE §3.7's fixed settings that apply locally, each with a unit test: analytics off (`GRADIO_ANALYTICS_ENABLED=False`), no run history, no monitoring, no MCP server, no SSR, a closed API, an upload size limit, the `file=` URL guard, and never `share`.
  - The app itself makes no third-party calls, and a nightly logging-proxy check tests that.
  - The About page shows the installed version. Only when online lookups are on does it say that a newer release exists.
  - Gradio moves quickly (39 6.x releases in 10 months), so the constraints file moves it only after the GUI tests pass on the newest 6.x.
  - GUI-GATE keeps GUI and CLI results identical.
- **Accessibility** (D17): WCAG 2.2 AA for the GUI and the HTML report.
  - axe via Playwright in CI, in both themes.
  - Keyboard-only end-to-end tests.
  - A contrast unit test on the report's CSS tokens, and 200% zoom and 320 px reflow.
  - A manual NVDA and VoiceOver pass before the public beta.
  - An accessibility statement.
- **Speed.** A cold data_check takes 72.9-76.0 s today. The GUI shows progress, and FETCH and CACHE (M5) cut the time.

## 7. Sign-in and hosted services

- **Local use needs no account.** Sign-in is only for hosted services: the scienceverse-hosted bibr service, and later other hosted options (D14).
- **Sign-in goes through a scienceverse identity broker with ORCID sign-in.** ORCID's public API offers neither PKCE nor a device grant for desktop apps, requires HTTPS redirect URIs, and does not allow a client secret to ship in a distributed app. So the desktop client never talks OAuth to ORCID directly, and hosted bibr accepts broker tokens, never ORCID tokens.
  - LOGIN uses authorization code with PKCE and a loopback redirect, or the device grant, against the broker.
  - In the GUI, a "Sign in with ORCID" button (GUI-SIGNIN) runs LOGIN's flow in a new browser tab, because ORCID pages cannot sit in an iframe.
  - Until LOGIN exists, KEY-LOGIN stores a key the user creates on the service's web page, from the CLI or from a paste field in the GUI.
- **Tokens and keys:**
  - stored in the OS keyring, with a 0600-file fallback;
  - environment variables take precedence;
  - never read from a project config;
  - redacted in logs and run records;
  - refused over plain HTTP to non-loopback hosts (SETTINGS, EXTRACT-CLIENT).
- **The hosted reader is opt-in** until the bridge's bibr arm is within tolerance and DRIFT-GATE runs. Only then does setup preselect it (HOSTED-DEFAULT).
  - Every run records which extractor read the paper.
  - An opt-in GROBID server needs a one-sentence consent, and its errors are plain when it is down (ENGINE).
- **`metacheck serve`** binds to loopback unless a key is set or `--behind-authenticating-proxy` is passed (SERVE-AUTH). A deployment that serves on a non-loopback address then needs a key, or an authenticating proxy in front and that flag.
- **The API's default check set** drops to at most 5 checks (decisions 9 and 10, T-4). SERVE-AUTH and T-4 merge only after the operators of hosted services that call the API have been told.

## 8. Naming and versioning

- **Names:**
  - PyPI distribution `metacheck`, import `metacheck`, and command `metacheck`.
  - `pytacheck` is kept as an import alias and as a command. The aliases, the `PYTACHECK_*` names and the `pytacheck` store id are not removed on the 1.0 date alone: they stay until the hosted deployments that use them have switched, with an end date agreed with their operators and published in advance.
  - The repository stays `scienceverse/pytacheck`.
- **Scale of the rename.** "pytacheck" appears about 16,200 times in 714 tracked files (main at 00ef3a21). A re-runnable script in RENAME-1 does most of it, RENAME-2 to RENAME-4 the rest, behind a barrier, and no lock digest may move. Some strings keep the old name on purpose; RENAME-1 lists them.
- **Environment variables.** Each of the 17 `PYTACHECK_*` names read in the source today gets a `METACHECK_*` twin, which comes first except where noted below, and the `PYTACHECK_*` name is still accepted. Four `METACHECK_*` names are already read today, and RENAME-3 sets their order explicitly:
  - `METACHECK_LLM_CACHE_DIR` is read after `PYTACHECK_LLM_CACHE_DIR`. It is also R metacheck's name for the same cache, which is shared with R by design. It keeps that lower rank, so a setting meant for R does not override one meant for the Python package.
  - `METACHECK_LLM_MODEL` and `METACHECK_LLM_MAX_CALLS` are read by the API, as R's plumber API reads them, and have no `PYTACHECK_*` twin. They keep their meaning.
  - `METACHECK_EMAIL` is read after `PYTACHECK_EMAIL`. R does not read it, so it moves first like the new names.
  - The rename script does not rewrite these reads (RENAME-1).
- **Folders.** Config, data and cache folders are separate from R metacheck's. R keeps its data in a `metacheck` folder through rappdirs, so the Python folders need a different name (D04). The pytacheck folders are migrated.
- **Packs and the store:**
  - The store id becomes `scienceverse`, with `pytacheck` read as an alias.
  - Packs declare `requires.metacheck`; `requires.pytacheck` is still read.
  - Modules registered through the entry-point groups `pytacheck.modules` and `pytacheck.packs` keep loading. The groups get no `metacheck.*` twin, because STORE-2b removes dist packs and legacy entry points under decision 13 (a).
  - The store's own CI switches to the renamed package the day RENAME-1 merges.
- **Image.** `ghcr.io/scienceverse/metacheck`. Until 1.0 the `pytacheck` tags are pushed too, with a deprecation label. The Docker Hub image under the name metacheck belongs to the R package and is left alone.
- **Versions:**
  - Our own line from 0.4.0, with 0.4.0a1 as the first public pre-release (D05, D06). Between releases, main carries `.dev` versions.
  - The 0.0.1 placeholder is deleted after 0.4.0a1 is uploaded, so plain `pip install metacheck` finds the pre-release.
  - The registry tells R and Python implementations apart by the implementation key, not by version number. Nothing is asked of the R package's numbering.
  - Metadata and release notes record the R commit that agreement is measured against.
- **Credit.**
  - NOTICE and CITATION credit the R package and its authors.
  - The package metadata lists them as authors only with their permission, asked before the first upload.

## 9. Debt reduction targets

| Measure | Today (main, 00ef3a21) | 0.5 | 0.6 | 1.0 |
|---|---:|---:|---:|---:|
| `src` lines | 126,556 | at most 130k | 95-100k | 89-93k (rev 3: about 93,300, range 88-97k) |
| `tests` lines | 110,832 | — | — | 55-65k after the R reference freeze |

- **0.5 is a cap, not a cut.** 0.5 adds about 2-3k lines of product code (the service, settings, setup, the GUI, sign-in and the extraction client) and removes nothing, since DEDUP-IDIOMS, ONE-RDS, R-ONLY-FEATURES and STORE-2b come in 0.6. SIZE-GUARD keeps everything else from growing.
- **Floor.** About 80k source lines, after LLM-REKEY and the post-freeze clean-ups.
- **Ratchets:**
  - SIZE-GUARD fails growth that has no justification line.
  - A lint refuses new private R-idiom helpers.
  - H-13 guards input coverage before any removal.
- **Named reductions:**
  - DEDUP-IDIOMS: the 247 R-idiom helpers, about 1,900 lines.
  - FACADE-SUNSET, then DEPRECATE-END: the 89 exports nothing in `src` uses, 3,229 lines. They move to compat in M1 (API-SLIM), warn from 0.5, and are removed at 1.0, after the metacheck team has said which R-named functions their scripts use.
  - R-ONLY-FEATURES: `zenodo_upload`'s 1,228 lines and statout's HTML exporters.
  - STORE-2b: ECOSYSTEM §4.11's 850-1,100 source lines and about 700 test lines, less the public GitLab and Codeberg forms that D20 keeps.
  - ONE-RDS: one R serialisation reader.
  - CASE-PRUNE: parity case generators, 37,119 test lines, replaced by the case YAML and the goldens as the source of truth after the freeze.
- **R-side tooling after the freeze.** The porting data (8,784 lines), R test code (5,041), the R harness (538) and the sync scripts (1,281) move out of the default path. The 95 MB of goldens and the pinned image stay archived.
- **Readability.**
  - CODEMAP explains where everything lives.
  - PLAIN keeps the user-facing strings in one place.
  - The reproducibility check, repro and statout (18,890 lines together; statout's importers are shared with code_check and data_check) stay in the package (D19). They are simplified in the waves.

## 10. Phase A rescope

Phase A is re-classified against this roadmap. The full table is in Appendix A.

| Class | PRs | Hours |
|---|---:|---:|
| done | 12 | 36 |
| accelerate | 9 | 32 |
| keep | 22 | 64.5 |
| rescope | 24 | 66.75 |
| defer | 13 | 51.5 |
| drop | 2 | 4 |
| **Total** | **82** | **254.75** |

- The 55 active PRs (163.25 h) land in these bundles:

  | Bundle | Hours | Notes |
  |---|---:|---|
  | PA-ORACLE | 97 | Includes V-16; H-11, V-2 and H-14 go before the rename |
  | PA-STATUS | 14.75 | T-3 adds 4 h, shared with SETUP |
  | PA-SPIKE | 21.5 | |
  | PA-STORE | 21 | |
  | PKG-1 with PA-ORACLE | 3 | V-15 |
  | RENAME-4 | 1 | S-2 |
  | M0 | 1 | V-1 and the questions to the metacheck team (T-asks) |

- Deferred PRs move into the packages that first need them: the waves (rev 3's REFS, LINKS, DATA-b and CODE-b, inside WAVES-PAPER and WAVES-REPO), HARNESS-NET (rev 3's network-recording harness, NET-2 to NET-7) before REFS and LINKS, R-FREEZE and CURATE-1.
- H-2b (pinning R's report tables) is deferred, possibly indefinitely.
- H-1b and H-7c are dropped.

## 11. Risks

1. **The bridge has no date.** It needs labelled data that this repository does not hold yet, and 1.0 requires the bridge. Meanwhile GT-PILOT and GT-OPEN keep the harness exercised, and the labels stay honest.
2. **Unknown provenance of the published rates.** The R version and PDF pipeline are not recorded, so a difference at the bridge could come from either. The bridge's two arms, and asking the team, reduce this.
3. **Extractor drift.** A new bibr build can change results without any code change here. EXTRACT-PROV, EXTRACT-AGREE and DRIFT-GATE catch it.
4. **The rename breaks a contract** (store pins, `requires`, entry points, environment variables, format ids, the pack scanner's prefixes, the store's own CI). Mitigations: aliases, a re-runnable script, a barrier until RENAME-3 merges, lock digests unchanged, a store PR on the day RENAME-1 merges, and S+ review of the contract points.
5. **Canon hides a Band A change.** Today "p = 1" and "p = 0.6" compare as equal, as do "1e+05" and "140000" (D03). Mitigations: D03 (c) and the flip audit.
6. **Dependency drift.** Gradio moves quickly, and `uv tool install` ignores the lock. Mitigations: CONSTRAINTS, PKG-2's weekly fresh-resolution job and the update path.
7. **A bibr release changes the schema.** bibr 0.5.1 wrote 11.0 and bibr 0.6.0 (30 Sep), the first release that writes 12.x, writes 12.1. BIBR-12X-a gives 11.x input a plain error, and BIBR-12X-b raised the extra's floor to 0.6.0. The extra has no upper bound yet, so a bibr release that writes 13.x would install and then be refused.
8. **A less capable model makes a subtle mistake in hard code.** The difficult list gets a stronger model plus independent review. Everything else goes through the same gates.
9. **CI time.** The R golden job takes about 40 of its 120 minutes. Area-scoped PR runs (CI-1b) keep PR feedback short.
10. **Accessibility debt.** Link contrast is 2.41:1 today. A11Y fixes it before the beta.
11. **Users of the hosted API and of `serve`.** The API default drops to at most 5 checks (T-4), and `serve` on a non-loopback address needs a key or an authenticating proxy (SERVE-AUTH). The operators of hosted services are told before either merges.
12. **Scope creep before 1.0.** Corpus mode and PORT wait until after 1.0 (D18). The no-code module form waits until a store author or the translator needs it (decision 2 (c)).

## 12. Open decisions

| Id | Urgency | Question, with the recommended answer |
|---|---|---|
| D01 | this week | Trim phase A to the table in Appendix A, and put the validation harness and the product work before the core rewrite (changes decision 5's order; the stop points stay). Recommended (a). |
| D02 | this week | Accept the nine wave-1 recommendations and the five phase A defaults (0.4.0 for the status tiers, telling hosted operators before T-4, reg_check's recording token, a RetractionWatch excerpt, the nightly time), plus codebook_check leaving offline runs. Recommended (a). |
| D03 | this week | How lenient comparisons are about rounded numbers in text. Recommended (c): only in summary text and report prose, with whole numbers and numbers like 1e+05 always compared exactly. |
| D04 | now | Rename details: import `metacheck` with a `pytacheck` alias, separate data folders, store id `scienceverse`, the metacheck\* and pytacheck\* prefixes reserved, the GHCR image, and the one directory move (one exception to decision 21). Recommended (a). |
| D05 | this week | Our own version line from 0.4.0; credit in NOTICE and CITATION; the author list only with permission (changes the "versions track R" rule). Recommended (a). |
| D06 | this week | Nothing on PyPI this week; 0.4.0a1 after the rename and after the team has seen the wording. Recommended (a). |
| D07 | now | The provisional wording, and no grace for provisional entries until the bridge (changes ECOSYSTEM §2.5's grace and sign-off rule). Recommended (a). |
| D08 | this week | Two gates, re-measurement triggers and the bridge tolerance (replaces ECOSYSTEM §2.5 step 3's tolerance once the team agrees). Recommended (a). |
| D09 | this week | Where labelled validation data lives; the pilot rule. Who the validators are is settled by decision 14. Recommended (a). |
| D10 | this week | FAIR-VAL at check level, with its label. Recommended (a). |
| D11 | this week | Keep the R reference live (daily upstream sync and R goldens) until a release agreed with the metacheck team, and only after the bridge (ends FIDELITY §7's sync then). Recommended (a). |
| D12 | this week | A small top level; R names and façades in `metacheck.compat`, with the façades nothing needs deprecated under §3.6's rule (changes decision 15 and the permanent-façades rule). Recommended (a). |
| D13 | this week | The PDF-reader question in setup; hosted preselection only after it has been measured (amends decision 20). Recommended (a). |
| D14 | later | Sign-in only for hosted services, through the broker; how secrets are stored. Recommended (a). |
| D15 | later | GUI basic defaults: validated checks only, AI off, and power's note (amends decision 20, and decision 10 for the local GUI). Recommended (a). |
| D16 | this week | The one-command installer, and the update path. Recommended (a). |
| D17 | later | WCAG 2.2 AA as the accessibility target. Recommended (a). |
| D18 | later | Postpone corpus mode and PORT until after 1.0 (changes decision 3 (b) and PORT's place in phase D). Recommended (a). |
| D19 | later | Re-home `zenodo_upload` and statout's exporters; keep the reproducibility check (extends decision 16). Recommended (a). |
| D20 | later | Keep public GitLab and Codeberg sources and check fork PRs without secrets; the other deletions of decision 13 stay in STORE-2b (changes decision 13 in part). Recommended (a). |

## Appendix A: phase A table

| PR | Class | Lands in | Reason |
|---|---|---|---|
| SNAP-1 | done | #12 | Snapshot recorder, oracle and fixtures: the regression baseline that does not depend on R. |
| SNAP-2 | accelerate | PA-ORACLE | Records the module snapshots (plain JSON, Python 3.12) that become the main regression oracle once the R reference is frozen. |
| SNAP-3 | keep | PA-ORACLE | Snapshots the four differential suites; R only for the 89 R-side inputs, from the pinned image. |
| H-1 | done | #9 | check --strict, --area lists and a reviewed re-lock. |
| H-1b | drop | - | A full check found 0 tier-2 warnings, so the re-lock is a likely no-op; CI-2 no longer waits for it. |
| H-11 | keep | PA-ORACLE (before the rename) | Fresh-worktree zero makes the parity gates trustworthy in any checkout and on GitHub-hosted runners; it merges before RENAME-1 starts. |
| H-6 | keep | PA-ORACLE | Batch equality at the shared level compares the code with itself; no R. |
| H-13 | accelerate | PA-ORACLE | The coverage ratchet lands before any removal: FACADE-SUNSET's and DEPRECATE-END's removals, R-ONLY-FEATURES, ONE-RDS and CASE-PRUNE all wait for it. |
| H-5 | rescope | PA-ORACLE | Counters are hard caps, and the ≤ +15% A/B ratio per module stays hard (both sides run on one runner, P2); absolute budgets stay soft until an idle runner exists. |
| H-10 | rescope | PA-ORACLE | docs/PARITY.md written once, after the rename, as 'agreement with R' against a named R commit, with the pinned-image route. |
| H-2a | done | #15 | Report tables encoded with column names and rows; no golden or lock digest moved. |
| R-LOCAL | done | #13 | Pinned R image recipe; the built image is to be archived by digest in a second location (REF-ARCHIVE, M0). |
| H-3spec | keep | PA-ORACLE | New accuracy spec kinds (chains, paper lists, arguments) that H-3a and H-3c need. |
| H-7c | drop | - | Only speeds up the harness; dropping it shortens the accuracy.py chain. |
| H-3b | rescope | PA-ORACLE | Keep scoring the repository extras; drop the row-order check (Band B); depends on H-3spec, not H-7c. |
| H-3a | accelerate | PA-ORACLE | power, a validated check, has no accuracy row; the app's basic mode and the bridge both rely on it. |
| H-3c | accelerate | PA-ORACLE | Default-preset chain row, plus a chain row that runs the validated set exactly as the app's basic mode does (AI off). |
| H-3d | defer | WAVES-PAPER (REFS rows), WAVES-REPO (DATA-b and CODE-b rows) | Rows for experimental checks; needed only before the waves that rewrite them. |
| H-3e | defer | WAVES-PAPER (REFS) | Waits on a source and licence question (D02, item 13); needed only before REFS changes ref_retraction. |
| H-2b | defer | later, or never | Pinning R's report tables means about 1,054 regenerated goldens; H-2a and SNAP already pin the Python side. |
| H-9 | done | #8 | Mutation-guard and run-order tests. |
| H-14 | keep | PA-ORACLE (before the rename) | The layering lint enforces the core's boundaries; VAL-GATE reads its import graph. It merges before RENAME-1 starts. |
| H-8 | keep | PA-ORACLE | Differential suites compared against SNAP: CORE-1b's oracle. |
| V-11 | rescope | PA-ORACLE | Retire private-helper cases by default under H-13; re-point the parser groups first. |
| V-12 | defer | WAVES-PAPER, WAVES-REPO (the wave PRs) | Folded into the wave PRs that delete those helpers. |
| V-13 | defer | WAVES-PAPER, WAVES-REPO (the wave PRs) | As V-12; the coverage baseline moves with the last deleting PR. |
| H-4 | done | #14 | parity/perf.py counters and CPU per (module, input); gates on regex compiles only. |
| CI-1 | keep | PA-ORACLE | Golden upload and dispatch inputs; micromamba and setup-micromamba pinned. |
| CI-1b | accelerate | PA-ORACLE | Area-scoped PR parity runs (phase A answer), kept inside the 120-minute limit; the full R job takes about 40 minutes on a GitHub-hosted runner. |
| CI-2 | rescope | PA-ORACLE | --strict on after CI-1b and H-1 only (H-1b dropped). |
| CI-3 | rescope | PA-ORACLE | Nightly G1, G2, G8 and fresh-worktree jobs; counters and the per-module A/B ratio are hard, and absolute time budgets stay soft on hosted runners. |
| CI-4 | keep | PA-ORACLE | Snapshot, G6, G9 and determinism steps in ci.yml. |
| CI-5 | rescope | PA-STORE | Store CI against a source build, and against the latest metacheck pre-release on PyPI once one exists; no read token. |
| V-3 | done | #16 | canon.py and canon.toml; the printed-precision question is decision D03. |
| V-2 | keep | PA-ORACLE (before the rename) | Determinism across PYTHONHASHSEED values, needed once row order is free; it merges before RENAME-1 starts. |
| V-1 | accelerate | now (M0) | CODEOWNERS for canon.py, canon.toml, validation.json and the validated checks' code, and for the deviations, matrix.d and expected.d folders ahead of V-6 and H-3spec; it merged as #25, before RENAME-1 moves those paths. |
| V-4 | keep | PA-ORACLE | The flip audit guards canon against hiding a Band A change. |
| V-5 | rescope | PA-ORACLE | Compare through canon and lock canonical digests; no longer waits for H-2b or H-1b; follows D03. |
| V-10 | rescope | PA-ORACLE | report_table canon rule for the Python-side tables only. |
| V-6 | keep | PA-ORACLE | Deviation rows with a band per case: how every intended change is recorded. |
| V-7 | keep | PA-ORACLE | Validated checks' marks first, grandfathered once and flagged (phase A answer). |
| V-8 | keep | PA-ORACLE | Migrates the remaining marks into about 215-294 rows (FIDELITY §3.2), so every Band A and Band C difference in every check stays recorded (decision 1 (b)). |
| V-9 | rescope | PA-ORACLE | Results-only G2, documented as agreement with R (FID-3); no longer waits for H-3e or NET-4. |
| V-14 | rescope | PA-ORACLE | Shrink only; a small readable porting map stays until the freeze and goes in R-TOOLS-OUT. |
| V-15 | rescope | PKG-1 and PA-ORACLE | Split: NOTICE and credit before the first upload (PKG-1); the F2 test stays while the upstream sync runs. |
| V-16 | keep | PA-ORACLE | Sign-off lint after V-6's deviation rows exist; warns until validators are named. |
| V-17 | defer | R-FREEZE | The daily sync covers the R reference until the agreed freeze; a behaviour-level sync (R and network) is reconsidered at R-FREEZE if it is still needed. |
| NET-1 | rescope | PA-ORACLE | Only the guard that fails a case whose mock_dir is missing; the curl shim moves with NET-2. |
| NET-2 | defer | WAVES-PAPER (HARNESS-NET) | R HTTP recorder; needed only before the REFS and LINKS behaviour PRs. |
| NET-3 | defer | WAVES-PAPER (HARNESS-NET) | Recordings of real papers; at the start of REFS and LINKS. |
| NET-4 | defer | WAVES-PAPER (HARNESS-NET) | Replayed accuracy rows; no longer a V-9 dependency. |
| NET-5 | defer | WAVES-PAPER (HARNESS-NET) | ref_summary chain row over the replays. |
| NET-6 | defer | WAVES-PAPER (HARNESS-NET) | Still required before any Band A change in causal_claims, ref_pubpeer, prereg_check and reg_check. |
| NET-7 | defer | WAVES-PAPER (HARNESS-NET) | reg_check replay row; needs a RegCheck backend and a read-only token (D02, item 12). |
| SP-1 | done | #17 | Spike tree rebased on main; 3,866 text_search cases, 0 differences. |
| SP-2 | keep | PA-SPIKE | Doc.groups and Hits paragraphs/sections de-risk CORE-1b's deletions. |
| SP-3 | rescope | PA-SPIKE | Also measures the bibr 12.x JSON reader path, the main input once hosted bibr is used. |
| SP-4 | keep | PA-SPIKE | Index against scan and the output-boundary profile. |
| D-2 | rescope | PA-SPIKE | SPIKE-2 write-up and CORE-1b re-estimate against this roadmap; stop point 1 (decision 5). |
| T-1 | done | #10 | Status snapshot and labels; its registry-key rename and wording move to REG-RENAME. |
| T-2 | accelerate | PA-STATUS | select(status=) and the metacheck::validated alias: what the app's basic mode needs. |
| T-3 | rescope | PA-STATUS and SETUP | status and --status stay; the init question moves into SETUP's question catalogue. |
| T-5 | rescope | PA-STATUS | MIGRATING.md for R users trying the Python package, written after the rename. |
| T-6 | accelerate | PA-STATUS | Report badges: the only way status reaches users of the first pre-release. It reads status from the registry snapshot, so it does not wait for T-4. |
| T-asks | rescope | now (M0) | The questions to the metacheck team (published evidence, ground truth and pass criteria, validators, credit) are updated to match this roadmap. |
| C-S1 | done | #7 | A project config can no longer add or replace stores. |
| C-1 | keep | PA-STATUS | use(status=); a project config cannot widen trust. |
| T-4 | accelerate | PA-STATUS | Status in report(), the API and the run record; the API default drops to at most 5 checks, after the operators of hosted services that call the API have been told. |
| S-4 | done | #11 | Reserved official*/scienceverse* prefixes; RENAME-2 adds metacheck*/pytacheck*. |
| S-2 | rescope | RENAME-4 | The scaffold's install spec follows S-2's rule: the git source at main for .dev builds, metacheck>=X.Y for release builds. |
| S-8 | keep | PA-STORE | Tests of .json packs at their pinned rev: the key check for modules in authors' repositories. |
| S-11 | keep | PA-STORE | Licence check against the approved SPDX list. |
| S-6 | rescope | PA-STORE | Lifecycle fields without the port fields (PORT waits until after 1.0, D18). |
| S-7 | keep | PA-STORE | CATALOG.md: how people find packs that live in their authors' repositories. |
| S-3 | keep | PA-STORE | The fields preset key keeps external packs forward-compatible before curated presets. |
| S-5 | defer | CURATE-1 | Pack-to-pack depends has no user yet. |
| S-9 | rescope | PA-STORE | Yanks and withdrawals reach installed packs; no longer waits for S-5. |
| S-10 | keep | PA-STORE | The index copies status from the registry, for display only. |
| S-12 | rescope | PA-STORE | Store docs keep the public GitLab and Codeberg sources if D20 (a) is taken; licence wording and a validation section. |
| S-13a | rescope | PA-STORE | PR templates without the port-an-R-module template. |
| S-13b | rescope | PA-STORE | Store CI additions without the fields pack's depends. |
| D-1 | done | #17 | Plan corrections and the phase A answers. |

Totals: done 12 PRs (36 h), accelerate 9 (32 h), keep 22 (64.5 h), rescope 24 (66.75 h), defer 13 (51.5 h), drop 2 (4 h); 82 PRs, 254.75 h. Active: 55 PRs, 163.25 h. The classes and totals are as drawn up on 28 Sep. Since then V-2 has merged as #20, C-S1's and S-4's follow-ups as #21 and #22, the first part of BIBR-12X-a as #24, V-1 as #25 and TRUST-1 as #26. H-11 has merged as #29 and H-14 as #30. SERVE-AUTH (#23) is open. "Lands in" names the package or bundle of this roadmap that carries the PR; a number is the merged PR. Rev 3's wave packages (HARNESS-NET, REFS, LINKS, DATA-b and CODE-b) sit inside WAVES-PAPER and WAVES-REPO.
