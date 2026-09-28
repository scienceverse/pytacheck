# Module system v2: packs, presets and a module store

Status: accepted (2026-09-24). This design came out of a judge panel of three
independent designs ("community", "rigor", "minimal"). Both judges picked
**minimal**, and this document is minimal plus the ideas they said to take
from the other two. Anything listed under *Deferred* was cut on purpose.

## Goals

* **Community extension.** Researchers can write checks for their own field
  and share them the way Claude Skills and plugins are shared: a folder in a
  git repo, listed in a public store.
* **Selective.** Users choose the checks that matter to them with presets,
  both when they set pytacheck up (`pytacheck init`) and at runtime
  (`preset=`, `--preset`). Sensible default presets ship built in.
* **Hot-swappable.** Packs can be installed, removed or re-pinned, and presets
  can be edited, without restarting a notebook or the API server. A preset
  can replace one module with another *in place*, so later modules still
  chain.
* **Faithful.** Built-in modules, `module_run()`, `module_list()` and every
  parity golden behave exactly as before. With no preset or modules given,
  the library's `report(paper)` runs exactly what metacheck's `report()` runs.
* **Reproducible and auditable.** Every run records which module, from which
  pack, at which commit, file hash and arguments. Third-party code runs only
  after an explicit, auditable decision.
* **No new runtime dependencies.** The standard library, plus `platformdirs`,
  `rich` and `httpx`, which pytacheck already depends on. All shared files
  are JSON, which R's `jsonlite` (already a metacheck dependency) can read.

## Concepts

**Module** (unchanged). A function decorated with `@module` in `<name>.py`
that returns metacheck's result dict. A module can be referred to as:

* a bare name, `"marginal"`, resolved by search order (below);
* a qualified ref, `"pack::name"` (R's namespace syntax, which never appears
  in a file path). `"metacheck::marginal"` is the same as `"marginal"`;
* a path to a `.py` file, a `ModuleSpec`, or a decorated function, as today.

However a module is addressed, its **label** is the bare module name. That
label is `ModuleOutput.module`, the key `get_prev_outputs()` looks up, and the
basis of the `.<stem>` suffix in `summary_table`. So chaining works the same
whether a module was run as `x` or as `pack::x`.

**Pack.** A folder containing `pack.json` and ordinary `@module` `.py` files.
It has the same shape as `src/pytacheck/modules/`:

* every `*.py` in the pack root whose name does not start with `_` is a
  module;
* `_*.py` files are helpers, imported by modules with relative imports
  (`from ._util import X`);
* `data/`, `tests/`, `README.md`, `LICENSE`, `CITATION.cff` and any `.R`
  files are ignored by the loader. This means a pack can hold metacheck `.R`
  implementations next to the `.py` ones.

A pack can also contain no modules at all: a **preset-only pack** (for
example, "the checks psychologists care about, with these arguments") needs
no code. This is the cheapest kind of pack to review and to trust.

Packs come in five kinds, and each has a trust label:

| kind | where it lives | trust label |
|---|---|---|
| built-in `metacheck` | `src/pytacheck/modules/` + `pack.json` | `builtin` |
| store pack | `<data>/packs/<name>/<rev12>/`, pinned by commit and tree hash | `store` (plus the `reviewed` date) |
| git/URL pack | same folder layout, pinned the same way, not listed in any store | `unlisted` |
| path pack | a local folder, live and unpinned, for development | `local` |
| dist pack | a pip-installed package that registers the `pytacheck.packs` entry point | `dist` |

**Preset.** A named, ordered list of modules, optionally with per-module
arguments. It is composed with `extends`, `exclude`, `replace` and `modules`.
Presets live in a pack's `pack.json` (ref `pack::preset`; `pack` on its own
means `pack::default`) or in config (ref by bare name). The built-in presets
mirror lists that are hard-coded in metacheck:

* `metacheck::default`: the default of `formals(report)$modules`;
* `metacheck::repository`: the default of `report_repository()`;
* `metacheck::validated`: `validated_modules` in the Shiny app
  (`inst/app/report_app.R`).

A test parses the upstream R sources and asserts that these three lists are
equal to R's, so upstream-sync picks up any change.

**Store.** A git repository whose `index.json` lists packs, like a Claude
Code plugin marketplace. The default store is named `pytacheck` and lives at
**`https://github.com/scienceverse/pytacheck-modules`**. Config can add
other stores (a lab's or an institute's) or remove this one. A store is data
only: nothing in it runs until the user installs a pack.

**Config and scopes.** Config is JSON. The user config is
`platformdirs.user_config_dir("pytacheck")/config.json`. The project config
is the nearest `pytacheck.json` in the working directory or any directory
above it. Precedence, lowest first: built-in defaults, then user, then
project, then environment variables, then `pc.use(...)`, then the arguments
of the call. `PYTACHECK_CONFIG=/path/file.json` uses only that file;
`PYTACHECK_CONFIG=none` ignores all config files, which is how tests and
parity runs stay hermetic.

**Pin.** A config entry for a pack:
`{"source": {...}, "rev": "<40 hex>", "tree_sha256": "...", "version": "...", "store": "..."}`
or `{"path": "../lab-modules"}`. A committed project `pytacheck.json` with
pins works as the team's lock file.

**Registry.** An in-process view of the active packs. It is built lazily, the
first time a ref is not a built-in, and cached against a *stamp*: the
relevant environment variables, the working directory, and `(path, mtime_ns)`
of every config file. Every non-built-in lookup re-checks the stamp, which
costs one `stat` call per file, so edits are picked up without a restart.

**Provenance and run record.**

* `ModuleOutput.provenance` is set on every `module_run()`. It is a plain
  attribute, not an element of `keys()`, `results()` or the canonical
  encoding, so parity is unaffected.
* `RunRecord` is built for each report or run from the provenance of every
  module that ran. It is embedded in HTML reports and can be written with
  `--record`.

## File formats

### `pack.json`

```json
{
  "schema": 1,
  "name": "psych",
  "version": "1.2.0",
  "title": "Psychology reporting checks",
  "description": "Reporting checks for psychology papers.",
  "authors": ["Jane Doe <jane@uni.edu>"],
  "license": "MIT",
  "homepage": "https://github.com/janedoe/pytacheck-psych",
  "fields": ["psychology"],
  "keywords": ["apa", "reporting"],
  "requires": {"pytacheck": ">=0.3.1"},
  "dependencies": [],
  "presets": {
    "default": {
      "description": "metacheck's defaults plus psychology checks",
      "extends": ["metacheck::default"],
      "replace": {"power": "power_sesoi"},
      "exclude": ["ref_pubpeer"],
      "modules": ["apa_df", "manipulation_check"],
      "args": {"apa_df": {"strict": true}}
    },
    "minimal": {"description": "Only the psychology checks", "modules": ["apa_df", "manipulation_check"]}
  }
}
```

Rules:

* **Pack names** match `^[a-z][a-z0-9_-]{1,39}$`. The names `metacheck`,
  `local`, `pytacheck` and `modules` are reserved.
* **Module names** match `^[A-Za-z][A-Za-z0-9_]*$` and equal the file stem.
* **`fields`** is the controlled vocabulary used by `init` and search:
  `general`, `psychology`, `medicine`, `neuroscience`, `economics`,
  `education`, `biology`, `ecology`, `sociology`, `political-science`,
  `computer-science`, `physics`. The store may extend it.
* **`dependencies`** are PEP 508 strings. They are checked for presence and
  never installed: pytacheck prints the `pip install` command instead.
* **Inside a pack's presets**, a bare module name means that pack's own module
  when one exists; otherwise normal resolution applies. A ref to another
  pack's module must be qualified (`pack check` warns if it is not).
* **Built-in manifest** `src/pytacheck/modules/pack.json`: it has no
  `version` (the loader uses `<pytacheck version>`) and holds only the three
  presets above. It does not list the built-in modules, which are found by
  scanning the directory as today, so parallel porting PRs never conflict
  over it.

### Module metadata (decorator, all new keyword arguments optional)

```python
@module(
    title="APA degrees of freedom", description="...", keywords=["results"],
    author=["Jane Doe <jane@uni.edu>"], details="... <validation>...</validation>",
    requires=["network"],            # capabilities: "network", "llm"
    validation={"papers": 120, "instances": 260, "tp": 210, "fp": 21, "fn": 40,
                "reference": "https://osf.io/abcd3"},
)
def apa_df(paper, strict=False): ...
```

* `keywords` stays faithful to R: every upstream module has exactly one
  keyword, its section. For backwards compatibility the decorator moves any
  `"llm"` or `"network"` found in `keywords` into `requires`, so
  `module_list()` shows R's keywords. PORTING.md now says to use `requires=`.
* `validation` is optional and structured. Tools derive
  `ppv = tp/(tp+fp)` and `sensitivity = tp/(tp+fn)` and show them in
  `pytacheck modules`, `pack show`, `pack search` and the store index. The
  prose `<validation>` block in `details` stays verbatim for R parity.
* `ModuleSpec` gains the defaulted fields `pack: str | None`,
  `requires: tuple[str, ...]` and `validation: Mapping | None`.

### Store `index.json`

The store's CI generates this file with `pytacheck store build`; nobody
edits it by hand.

```json
{
  "schema": 1,
  "name": "pytacheck",
  "description": "Community modules for pytacheck (and, in future, metacheck).",
  "generated": "2026-09-24T10:00:00Z",
  "fields": ["general", "psychology", "medicine", "..."],
  "packs": [
    {
      "name": "psych", "version": "1.2.0", "title": "...", "description": "...",
      "fields": ["psychology"], "keywords": ["apa"], "license": "MIT",
      "authors": ["Jane Doe <jane@uni.edu>"], "homepage": "...",
      "source": {"github": "janedoe/pytacheck-psych", "rev": "<40 hex>", "subdir": ""},
      "tree_sha256": "<64 hex>",
      "code": true,
      "languages": ["python"],
      "reviewed": "2026-09-01",
      "yanked": null,
      "requires": {"pytacheck": ">=0.3.1"},
      "dependencies": [],
      "modules": [
        {"name": "apa_df", "title": "...", "description": "...", "section": "results",
         "requires": [], "validation": {"papers": 120, "ppv": 0.909, "sensitivity": 0.84}}
      ],
      "presets": {"default": "metacheck's defaults plus psychology checks"}
    }
  ]
}
```

Sources:

* `{"github": "owner/repo", "rev": ..., "subdir": ...}`,
  `{"gitlab": "group/repo", ...}` and `{"codeberg": "owner/repo", ...}`
  download the commit tarball over HTTPS through `pytacheck.http`, so no git
  binary is needed (researchers on Windows and slim Docker images often lack
  one).
* `{"git": "<https or ssh url>", "rev": ...}` works with any host and needs
  git.
* `{"path": "<local folder>"}` is for local and test stores only.

For a pack that lives inside the store repo (`packs/<name>/`), `store build`
sets `source` to `{"github": "<store repo>", "rev": <last commit that touched
packs/<name>>, "subdir": "packs/<name>"}`. A contributor can therefore submit
a pack as a folder through GitHub's web editor, with no repo of their own.

A pack submitted from its own repo is listed as `packs/<name>.json` in the
store repo:

```json
{"name": "psych", "source": {"github": "janedoe/pytacheck-psych", "rev": "<40 hex>"}}
```

Store maintainers set `reviewed` and `yanked`; `store build` computes
everything else.

**Tree hash** (`pytacheck-tree-v1`). Take the regular files in the pack
folder, excluding `.git/`, `__pycache__/`, `*.pyc` and
`.pytacheck-install.json`. Sort their paths bytewise as POSIX paths relative
to the pack root. For each file write the line `<sha256 hex>  <path>\n`, then
take the sha256 of the concatenated lines. This is the same as running
`find . -type f ... | LC_ALL=C sort | xargs sha256sum | sha256sum`.

### Config (`config.json` / `pytacheck.json`)

```json
{
  "preset": "psych::default",
  "stores": {"pytacheck": "https://github.com/scienceverse/pytacheck-modules",
             "mylab": "https://gitlab.uni.edu/lab/pytacheck-store"},
  "packs": {
    "psych": {"source": {"github": "janedoe/pytacheck-psych"}, "rev": "<40 hex>",
              "tree_sha256": "<64 hex>", "version": "1.2.0", "store": "pytacheck"},
    "labmods": {"path": "../lab-modules"},
    "some_dist_pack": false
  },
  "presets": {
    "thesis": {"extends": ["psych::default"], "exclude": ["ref_pubpeer"],
               "args": {"power": {"seed": 8675309}}}
  }
}
```

Merge rules:

* Scalars: the higher scope wins.
* `stores`, `packs` and `presets` merge by key, with the higher scope winning.
  A `null` value removes the entry, and `false` hides a dist pack.
* Relative paths are resolved against the config file that contains them.
* The store `pytacheck` is built in; `"stores": {"pytacheck": null}` removes it.

Writes to config are atomic: write a temp file, then rename.

### Install record

`<data>/packs/<name>/<rev12>/.pytacheck-install.json`, where `<data>` is
`PYTACHECK_DATA_DIR` or `platformdirs.user_data_dir("pytacheck")`:

```json
{"source": {...}, "rev": "...", "tree_sha256": "...", "installed": "<UTC ISO>",
 "store": "pytacheck", "files": {"pack.json": "<sha256>", "apa_df.py": "<sha256>"}}
```

Installed files are made read-only. At run time the module file's sha256 is
compared with the record. A mismatch warns and sets `"modified": true` in
the provenance.

### Provenance (`ModuleOutput.provenance`)

```json
{"id": "psych::apa_df", "name": "apa_df", "pack": "psych", "version": "1.2.0",
 "trust": "store", "reviewed": "2026-09-01",
 "source": {"github": "janedoe/pytacheck-psych", "rev": "<40 hex>"},
 "sha256": "<module file sha256>", "modified": false,
 "args": {"strict": true}, "requires": []}
```

`args` holds the effective arguments: the signature defaults merged with the
arguments passed. Values that are not JSON are stored as their `repr`. For
built-ins, `source` is `{"builtin": "pytacheck <version>", "upstream": "<metacheck version>@<commit10>"}`.

### Run record (`pytacheck.run/1`)

```json
{"schema": "pytacheck.run/1", "created": "<UTC ISO>",
 "pytacheck": "0.3.1.dev1", "metacheck": {"version": "0.3.1", "commit": "85c8c872cf"},
 "python": "3.12.6", "platform": "linux",
 "preset": "psych::default", "preset_source": "/home/me/thesis/pytacheck.json",
 "offline": false, "dropped": [],
 "papers": ["10.1177_0956797620912345"],
 "modules": [{"...provenance...": "...", "status": "ok"}],
 "environment": {"bibr": "0.5.1"}}
```

The record is:

* embedded in HTML reports as `<script type="application/json" id="pytacheck-run">`,
  so the visible report is unchanged;
* written by `--record run.json` or `record=`;
* replayed by `pytacheck rerun run.json paper.json`.

## Resolution

`module_find(ref)` checks the following in order. Steps 1, 2, 4 and 6 are
exactly today's behaviour.

1. A `ModuleSpec` or decorated function is used as is.
2. A bare name that is a built-in resolves to the built-in, fast, without
   reading any config. `marginal` always means metacheck's marginal.
3. `pack::name` resolves to that module of that active pack; `metacheck::x`
   is `x`. An unknown pack or module raises `ModuleError`, naming the active
   packs or that pack's modules.
4. Legacy `pytacheck.modules` entry points, then `./<name>.py`, then
   `./modules/<name>.py`.
5. Active packs. If more than one pack provides the name, raise
   `ModuleError` listing the qualified refs to use instead.
6. A path to an existing `.py` file.

**`allow_local`.** `pc.use(allow_local=False)` turns off steps 4 (the file
lookups), 6, and any path or path pack. The API server always runs with
`allow_local=False` and never installs packs or loads paths or URLs from a
request.

**Loading.**

* Pack modules are imported lazily as
  `pytacheck_packs.<pack>_<rev12>.<module>`. Each is a synthetic package whose
  `__path__` is the pack folder, so helpers and relative imports work and two
  revisions never share module objects.
* Path packs use the key `local_<sha8 of path>` and are re-imported when a
  file's `(mtime_ns, size)` changes, with bytecode writing disabled.
* `_load_file` names modules by a deterministic content hash (the sha256 of
  the resolved path) instead of Python's randomised `hash()`.
* `./name.py` files are still re-executed on every call, as R's `source()`
  does.

## Selection

`presets.select(modules=None, preset=None, args=None, *, use_config=False)`
returns an ordered `[(ref, args)]`.

* When `modules` is given, it runs in the given order, as in R, with no
  deduplication. If `preset` is also given, the preset is expanded first and
  the explicit modules are appended.
* Otherwise the preset is, in order of precedence: `preset=`, then
  `pc.use(preset=...)`, then (only when `use_config=True`) `PYTACHECK_PRESET`,
  then the project config, then the user config, and finally
  `metacheck::default`.
* The **library** (`report()`, `report_module_run()`) uses
  `use_config=False`, so `report(paper)` always runs what R's `report()`
  runs. The **CLI** and **API** use `use_config=True`, and the CLI prints
  which preset it used and where that setting came from.
* Args are merged in this order: preset args (deep-merged along `extends`),
  then the call's `args=` (R's `report(args = list(power = list(seed = 1)))`).
  Keys can be bare or qualified; a qualified key wins.
* `offline=True` drops modules whose `requires` include `network` or `llm`.
  They are listed under `dropped` in the run record. This mirrors the toggles
  in metacheck's Shiny app.

Expansion:

```
expand(ref, seen):
  P = lookup(ref)                      # config presets (bare), then pack::preset, "pack" = pack::default,
                                       # else metacheck::<ref>
  entries = dedup_by_label(concat(expand(b) for b in P.extends))   # order kept, first wins
  entries = [e for e in entries if label(e) not in P.exclude]
  entries = [P.replace.get(label(e), e) for e in entries]          # swap in place: chain position kept
  entries += [m for m in P.modules if label(m) not in labels(entries)]
  args = deep_merge(*(base args), P.args)
```

Errors are raised before any module runs: cycles, unknown presets, unknown
module refs, and missing preset `dependencies`. This mirrors R's `report()`,
which calls `module_find()` on every module up front.

`pytacheck presets show X --as-r` prints the equivalent metacheck call,
`report(paper, modules = c(...), args = list(...))`.

## Run session (memoisation)

`with pc.run_session():` memoises `module_run()` calls whose input is a plain
`Paper` or `PaperList`, not a chained `ModuleOutput`.

* The key is `(id(paper), paper generation, module identity, bound args)`:
  * the paper generation is a counter bumped by any mutation of the paper;
  * module identity is the pack, the rev and the sha256 of the module file
    (or `id(func)` if there is no file);
  * bound args are the arguments bound to the signature with
    `apply_defaults()`, so a call that spells out a default and one that
    leaves it out share a cache entry.
* Failures are not cached. A hit returns a shallow copy of the output.

`report()`, the CLI and each API request enter a session automatically, so
the implicit dependency re-runs in metacheck (`data_check` → `repo_check`,
`codebook_check` → `data_check`, and so on) happen once per run. Outside a
session, `module_run()` behaves exactly as before.

## Store operations and trust

**Browsing.**

* `store update` fetches `index.json`. For a GitHub store the URL is
  `https://raw.githubusercontent.com/<owner>/<repo>/HEAD/index.json` (with a
  GitHub token, the contents API first; see "Implementation notes (private
  stores)"); a store URL can also point straight at an `index.json`, or at a
  local folder.
* The index is cached in `<data>/stores/<name>/index.json` with a one-hour
  TTL. When offline, the cached copy is used with a warning. If there is no
  cached copy, the error says the store is unreachable and how to add
  another.
* Searching and listing never run third-party code: the index and
  `pack.json` are both data.

**Install.**

1. Resolve the ref:
   * `psych` (from a store);
   * `store/psych`;
   * `psych@<rev prefix>`;
   * an `https://github.com/x/y[@ref]` URL, where a tag or branch is resolved
     to a SHA once, through the GitHub API or `git ls-remote`;
   * `./folder` (a path pack);
   * nothing, which syncs every pin in the effective config.
2. Download the pinned commit's tarball through `pytacheck.http`, falling
   back to hardened git.
3. Extract safely:
   * reject absolute paths, `..`, symlinks, hardlinks and device files;
   * cap the total size at 50 MB and the file count at 5000;
   * strip the top directory and select `subdir`.
4. Compute the tree hash. It must equal the index's `tree_sha256` when the
   index has one.
5. Show the **consent card** and ask `[y/N]` unless `yes=True`. The card
   shows:
   * store, source, rev, tree hash, reviewed date, and any yanked notice;
   * every file with its size;
   * declared `requires` and `dependencies`;
   * the top-level imports found by a static `ast` scan, highlighting
     `subprocess`, `os.system`, `eval`/`exec`, `socket`, `ctypes` and HTTP
     libraries.
   A code-free pack says so.
6. Move the pack into `<data>/packs/<name>/<rev12>/` atomically, write the
   install record, make the files read-only, and pin the pack in the
   user or project config.

Nothing from the pack is imported or executed during install.

**Git hardening** (fallback only):

* only `https`, `ssh` and local paths are accepted;
* `-c protocol.ext.allow=never`, `-c core.hooksPath=/dev/null`;
* `GIT_TERMINAL_PROMPT=0`;
* `--no-recurse-submodules`, and LFS is disabled;
* fetch the commit with `--depth 1`, falling back to a full clone plus
  checkout, then verify that `rev-parse HEAD` equals the pin.

**Update.** Updates never happen automatically. `pack update` shows the
version and rev change (and a file-level diff summary) and asks before it
re-pins.

**Honest limits.**

* There is no sandbox. An installed pack runs with the user's permissions,
  like a pip package, an R package or a metacheck `./modules/*.R` file.
* The controls are curation, pinning, hashes, visibility and consent.
* Reports show the pack name next to the title of any module that is not
  built in, so readers can tell community checks from metacheck's.

**Licensing.** Packs are loaded into an AGPL-3.0 process. The official store
asks for an OSI-approved licence in `pack.json`, and the docs say so up front.

## CLI

```
pytacheck init [--preset P ...] [--project] [--yes]   # pick field presets, install their packs, write config
pytacheck modules [NAME] [--pack P | --all]           # list (non-built-ins as pack::name) / help
pytacheck presets [REF] [--as-r]                      # list / expand one preset
pytacheck pack list | search [TEXT] [--field F] | install [REF] [--project] [--yes]
             | remove NAME [--project] | update [NAME] [--yes] | show NAME
             | new NAME [--path DIR] | check [DIR] [--json]
pytacheck store list | add NAME URL | remove NAME | update [NAME] | build DIR [--check]
pytacheck run PAPER... [-m MOD ...] [--preset P] [-a [MOD.]KEY=VALUE ...] [--offline] [--record FILE] [--json]
pytacheck report PAPER... [same selection flags] [-o FILE]
pytacheck rerun RECORD PAPER... [--install] [--allow-modified]
```

* `-a module.key=value` applies to one module. A bare `-a key=value` applies
  to every selected module whose signature accepts `key`, and it is an error
  if none does. This replaces the old behaviour of passing every `-a` to
  every module, which crashed modules that didn't take the argument.
* Every command that changes config or fetches code asks for confirmation
  unless `--yes` is given.
* Environment variables: `PYTACHECK_CONFIG`, `PYTACHECK_DATA_DIR`,
  `PYTACHECK_PRESET`, `PYTACHECK_STORE_URL` (overrides the URL of the default
  `pytacheck` store; useful for mirrors and tests).

## Python API (new names are exported lazily from `pytacheck`)

```python
pc.module_run(paper, "psych::apa_df", strict=True)        # label "apa_df"
pc.module_list(pack="psych") / pc.module_list(pack="*")   # extra "pack" column; no args = unchanged
pc.preset("thesis") -> list[tuple[str, dict]]
pc.preset_list() -> DataFrame[ref, description, n_modules, defined_in]
with pc.use(preset="psych", allow_local=False, offline=True): ...   # ContextVar-scoped
with pc.run_session(): ...
pc.pack_install(ref=None, *, scope="user", yes=False) / pack_remove / pack_update / pack_list / pack_show
pc.pack_check(path) -> list[CheckIssue]; pc.pack_new(name, path=".")
pc.store_list() / store_add(name, url) / store_remove(name) / store_update(name=None) / store_search(text="", field=None)
pc.refresh()
pc.RunRecord, pc.rerun(record, paper, *, install=False, allow_modified=False)
```

## `pack check` (authors and store CI)

`pack check` reuses the module contract tests from
`tests/foundation/test_modules_contract.py`, moved to
`pytacheck.packs.check`. It checks that:

* `pack.json` matches the schema and naming rules;
* each module's name equals its file stem and it has a title, a description
  and a valid section keyword;
* each module has a `<validation>` block or a `validation=` record,
  otherwise it warns that the module is unvalidated;
* each module runs on `demopaper()` and on the `test_paper()` edge cases
  without mutating the paper, returns a valid traffic light, and passes the
  `summary_table` checks;
* network use is declared, based on a static scan for `pytacheck.http`,
  `httpx`, `requests`, `urllib` and `socket`;
* presets expand, and refs to other packs' modules are qualified;
* the index version equals the `pack.json` version.

Exit code 1 means errors; warnings are printed.

## Metacheck interop

* `pack.json`, `index.json` and the run record are JSON, and they refer to
  modules only by name, so metacheck could read the same store. Index entries
  carry `languages`.
* A dual-language pack can ship `x.R` next to `x.py`. pytacheck loads only
  `.py`, and metacheck's `module_find()` already loads `.R` from a directory.
* The parity harness can compare the two implementations of such a pack.
* Proposed upstream change: metacheck ships `inst/modules/pack.json` with the
  three built-in presets. That would give one source of truth for the lists
  that are hard-coded today in `report()`, `report_repository()` and the app.

## Deferred (on purpose)

1. **Declarative YAML/JSON "pattern" modules for non-programmers.** These are
   the first follow-up. They are accepted only if `marginal`, re-expressed in
   the format, matches `parity/golden/mod_marginal` exactly.
2. Aliases and side-by-side versioned ids (`pack@1.2/name`). A preset's
   `replace` plus pins covers the need.
3. Multi-level trust tiers, a policy engine, signing, mirrors, and a hosted
   registry or web UI.
4. Output digests and fingerprints, and a `reproduce` command beyond `rerun`.
5. Moving heavy built-ins out of core into packs. That would split parity
   testing and upstream-sync.
6. Running R modules from Python, or the reverse.

## Implementation notes (phase 1: core)

Decisions taken while implementing the core (config, packs, resolution,
presets, sessions, provenance), where the text above left room or where the
code deliberately differs:

* **Unported built-ins in presets.** `presets.select()` / `expand()` check
  every ref before anything runs, but names that metacheck's own presets list
  and that are not ported yet are accepted by default and fail when run, like
  a failing module in `report_module_run()`. `validate=True` imports every
  module and makes those errors too (R's up-front `module_find()`); the
  library `report()` can switch to it once every built-in is ported.
  Checking without `validate` never imports module code.
* **Built-in preset entries stay bare** (`"power"`, not `"metacheck::power"`),
  so `select()` output and `--as-r` read like R's `modules = c(...)`.
* **Preset refs inside a pack** (`extends: ["minimal"]`) mean the pack's own
  preset when it has one, as bare module names do. `replace` of a label the
  preset does not include warns. Path refs (`*.py`) in config presets'
  `modules`/`replace` are resolved against the config file.
* **Integrity.** At run time every file in the install record (plus any
  unrecorded root `.py` file) is compared, not only the module file: a
  modified helper changes behaviour just as much. Hashes are cached against
  `(mtime_ns, size, inode)`, so this costs one `stat` per file. A pack whose
  install record is missing, or whose record's `rev`/`tree_sha256` differ
  from the pin, is unavailable (with a reinstall hint).
* **Install folder** is `<rev12>` of the pin's `rev`; a pin without a commit
  (a local-folder store source) uses its `tree_sha256[:12]`
  (`packs.registry.pin_rev12()`, `install_dir()`).
* **Kinds and trust.** `Pack.kind` is how a pack loads (`builtin`,
  `installed`, `path`, `dist`); `Pack.trust` is the label from the table
  above. Modules outside any pack get trust `local`, or `dist` when their file
  lives in `site-packages` (legacy `pytacheck.modules` entry points).
  `allow_local=False` does not disable legacy entry points (they are
  installed packages, not local files).
* **Path packs** may omit `pack.json` (the config key is the name). Their key
  is `local_<sha8 of the resolved path>`. Pack code (installed and path) is
  compiled from source by a `pytacheck_packs.*` meta-path finder and never
  reads or writes bytecode; a pack folder's own `__init__.py` is ignored.
* **Dist packs**: the `pytacheck.packs` entry point names a Python package
  containing `pack.json`; its modules import normally as `<package>.<module>`.
  A config pin with the same name wins over a dist pack.
* **Config.** `PYTACHECK_STORE_URL` sets the `pytacheck` store's URL even if
  config removed the store (environment beats config). `update_config()`
  writes the `PYTACHECK_CONFIG` file for every scope when one is named, and
  raises `ConfigError` under `PYTACHECK_CONFIG=none`. A `null` scalar removes
  a lower scope's value, like `null` in the merged sections.
* **Run sessions.** Nested `run_session()` blocks share the outermost memo.
  The key also includes the run's label. The paper generation is a
  process-wide counter (so `(id, generation)` never collides after garbage
  collection); it tracks mutation through the `Paper` API only, not in-place
  edits of a table or of `paper.extra` (papers must not be mutated in place).
  Arguments that cannot be frozen into a key (a DataFrame, a paper) skip the
  memo. Hits return a new `ModuleOutput` whose DataFrames are copy-on-write
  views, so editing a hit never changes the cache.
* **Provenance** is computed before the run and never breaks one; if it
  cannot be built, `provenance` is `None` and the run is not memoised.
  `module_list(pack=...)` appends the `pack` column after `path`.

## Implementation notes (phase 2: distribution, CLI, API, run records)

Decisions taken while implementing stores, installation, authoring tools,
run records, the CLI and the API, where the text above left room or where the
code deliberately differs:

* **Store layout.** `store.json` (optional) holds the store's `name`,
  `description` and extra `fields`. Next to a folder pack, `packs/<name>.json`
  may only carry the maintainer fields `reviewed` / `yanked` (that is where
  maintainers record a review). `store build` keeps `reviewed` from the
  existing index only while the pack's tree hash is unchanged (a changed pack
  needs a new review) and always keeps `yanked`. `--check`'s version check
  applies to the optional `version` of an external entry file. `generated` is
  kept when nothing else changed, so CI does not commit an index every push.
* **Index locations.** GitHub repos use `raw.githubusercontent.com/.../HEAD`,
  GitLab repos `<url>/-/raw/HEAD/index.json`, `*.json` URLs are used as is,
  other URLs get `/index.json`; local folders (and `file://`) are read
  directly, without a cache. A pack name missing from a cached index is
  looked up once more in a freshly fetched index; `pack update` always fetches.
* **Path sources.** A store without git history (a local store, or a copy
  of a store repository without its history, like the test fixture
  `tests/modsys/fixtures/store`) lists in-repo packs as `{"path": "packs/<name>"}`.
  Installing one from a local store pins the absolute folder and the tree
  hash (the install folder is `tree_sha256[:12]`); from a remote store it is
  refused (the store's CI replaces path sources with commits).
* **Refs.** Besides the forms above: `.../tree/<ref>/<subdir>` GitHub/GitLab
  browse URLs, a `git+` prefix to force git, and `file://` git repositories.
  `name@<rev>` at a commit other than the listed one installs the pack as
  *unlisted* (no store, no review date), with a note on the consent card.
* **Extraction.** Members outside the selected `subdir` are skipped (they are
  never written), but absolute and `..` paths are refused anywhere; the
  downloaded tarball is capped at 100 MB. Git is only a fallback for download
  errors, never for safety or hash errors.
* **Consent.** Declining raises `pytacheck.packs.Cancelled` (a `PackError`);
  without a terminal the answer is no. Adding a path pack also asks. The
  staging folder and any folders created for it are removed on failure, so a
  cancelled install leaves no trace.
* **Remove / update.** `pack remove` of a dist pack writes `false`; it deletes
  the install folder unless the effective config still pins that revision.
  `pack update` follows the store index for store packs and the default
  branch for unlisted git packs; path and dist packs are not updated. Old
  revisions stay on disk until `pack remove`. `pack_install(ref)` returns the
  `Pack`; `pack_install()` (sync) returns the list of packs it installed.
* **Unported metacheck modules.** The built-in `pack.json` also lists
  `metacheck_modules`: every metacheck module, ported or not (a test keeps it
  equal to `upstream/metacheck/inst/modules`). Presets may name any of them,
  not only those in the three built-in presets, so field presets can use
  modules still being ported (they fail per module until then).
* **`pack check`.** `registry.overlay()` makes the checked folder an active
  pack in the current context only, so its presets and `pack::name` refs
  resolve without touching config. Modules run on `demopaper()`, two
  `test_paper()`s and a paper list; modules requiring `network`/`llm` are
  not run. A preset extending a pack that is not active is a warning, not an
  error. Each issue has a `code` (the built-in contract test filters on them).
* **Authoring.** `module_template()` does not overwrite an existing file
  unless `overwrite=True` (R's does). `pack_new()` names its example module
  `<pack>_example` and requires `pytacheck>=<major.minor>` so development
  builds pass.
* **Run records.** Module entries carry `status` (`ok`/`fail`), `error` for
  failures and `label` when the output label differs from the module name.
  `run_modules(paper, selection)` accepts a `Selection`, `(ref, args)` pairs,
  refs or one ref; failed modules have the shape of metacheck's
  `report_module_run()` failures (as in `pytacheck.report`, `section` is
  `None`). `rerun()` also takes `yes=`; pack modules run from the recorded
  commit's install folder (installed, not pinned, with `install=True`);
  built-in version or file-hash differences only warn.
* **Report integration.** Not wired yet: `pytacheck.report` belongs to the
  report port. The hook is `chain = run_modules(paper, selection)` (inside
  `report()`, replacing its own loop) and embedding
  `chain.run_record.to_html()` in the HTML. Meanwhile `pytacheck report
  --record` builds the record from the report's module outputs.
* **CLI.** `presets show REF` is accepted as well as `presets REF`. Tables
  print as plain aligned text when stdout is not a terminal. Extra flags:
  `rerun --yes/--record/--json`, `store add|remove --yes`,
  `store build --repo`, `pack show --json`. `init` with several presets saves
  a config preset `mine` extending them.
* **API.** Without `modules` or `preset`, `/paper/check` runs a preset
  configured for the server (`use()`, `PYTACHECK_PRESET` or config) if there
  is one, else every available module (plumber's behaviour). Modules still
  run independently on the paper, as in plumber. Every upload endpoint's
  blocking work runs in a worker thread behind a per-event-loop semaphore.

## Implementation notes (review fixes)

Changes made after the review of phases 1 and 2, where the code now differs
from the text above:

* **`run_provenance`.** The provenance field is `ModuleOutput.run_provenance`.
  `out.provenance` still reads it, unless the module returned an element
  called `provenance` (a module's own elements win, as before v2).
* **Memo keys.** Module identity is the pack, the rev, the file hash *and* the
  function object (two functions can share a name and a file: a factory, a
  redefinition, `replace(spec, func=...)`); `./name.py` files, re-executed on
  every call, are identified by file hash and qualified name. The paper part
  of the key is the generation (bumped by the `Paper` API only), the ids of
  its table objects and its frozen `extra`; the LLM options (`llm_use()`,
  `llm_model()`, ...) are part of the key too. Edits *inside* a table are not
  seen, so "bumped by any mutation" above does not hold: papers must not be
  mutated in place within a session. Hits deep-copy containers (lists, dicts)
  as well as viewing DataFrames.
* **`pack::name`** is only a qualified ref when both sides are valid names and
  no file of that name exists, so legacy paths containing `::` still load. A
  malformed config file makes a lookup raise `ModuleError` (naming the config
  problem), not `ConfigError`.
* **Provenance `args`.** Large objects (DataFrames, arrays, papers) are stored
  as `<Type shape>`, and other `repr`s are cut at 1000 characters, so
  building provenance stays cheap.
* **Extraction.** What the tree hash leaves out (`.git/`, `__pycache__/`,
  `*.pyc`, an install record) is never written, and compiled extension
  modules (`*.so`, `*.pyd`) are refused, so the installed files are exactly
  the hashed, recorded and reviewed ones. The `pytacheck_packs.*` finder is
  authoritative: a name with no `.py` source raises `ModuleNotFoundError`
  instead of falling through to bytecode, extensions or namespace packages.
* **Reviews.** Maintainers record `reviewed` together with
  `reviewed_tree_sha256` (the tree they read) in `packs/<name>.json`; a date
  without it, or for another tree, is dropped with a warning, and
  `store build --check` makes that an error.
* **Remote stores** cannot list `{"path": ...}` sources: `_`-prefixed keys are
  stripped from every index, `_location` is set only for local-folder stores,
  and a local store's paths must stay inside its folder.
* **Project config trust** (like git's `safe.directory`). The upward search
  stops at the home folder and at filesystem boundaries. A `pytacheck.json`
  owned by another user, or writable by others (a world-writable folder
  without the sticky bit, or a group other than the user's), is ignored with
  a warning. Local code it names (path packs, `.py` modules in presets) stays
  inactive until the user trusts it (`<data>/trusted.json`; `pack install`
  shows the code and asks, and adding a path pack with `--project` trusts
  it). Such path pins are written relative to the project file.
* **Reruns** hash every file before importing it, ask before running a
  recorded module file outside the working directory (a record is shareable
  data), and replay a module that failed in the record and still cannot be
  resolved as a failed output instead of stopping.
* **CLI.** `run` and `rerun` print why each failed module failed (with a note
  for metacheck modules not ported yet), add `error` to `--json`, run the
  remaining modules and exit 1. `-a MOD.KEY` must name a selected module and
  one of its arguments. `init --project` pins every pack its presets need in
  the project file (copying the user's pin when there is one), so the file is
  a working lock file. `pack check packs/<name>.json` fetches an external
  entry at its rev and checks it (store CI runs it). `presets --as-r` leaves
  modules without an `.R` twin out of the call (listed in a comment).
  `report --record` records the order the modules ran in. `pack search`
  shows PPV and sensitivity. `run_modules(..., record=path)` writes the run
  record. Unreadable paper files are a one-line error; a cancelled
  `pack update` says so and exits 1.
* **Still open.** The report renderer does not run modules through
  `run_modules()` yet, so HTML reports neither embed the run record nor show
  pack names (MODULES.md says so). The default store is published at
  <https://github.com/scienceverse/pytacheck-modules>, which is the single
  source of truth for its packs (this repository no longer carries a
  `contrib/` copy; the tests use `tests/modsys/fixtures/store`). While it is
  private, users need read access (see below).

## Implementation notes (private stores)

The default store (and pytacheck itself) started out private, so stores and
packs on private GitHub repositories work (`pytacheck/packs/auth.py`):

* **Credentials.** A token comes from `PYTACHECK_GITHUB_TOKEN`, `GH_TOKEN` or
  `GITHUB_TOKEN` (the first non-empty one). It is sent as `Authorization:
  Bearer` only over https to `api.github.com`, `github.com`,
  `raw.githubusercontent.com` and `codeload.github.com` (exact host, default
  port, no user info; both `urllib` and httpx must parse the URL the same way),
  and in practice only to `api.github.com`. These requests use their own
  HTTP/1.1 client with redirects followed by hand: the token goes only to the
  origin it was sent to, so the tarball endpoint's redirect to a short-lived
  `codeload.github.com/...?token=` URL is followed without it. A 401 to a
  request with a token is retried once without it, so an expired token never
  breaks a public store.
* **Paths.** With a token, the index is read through the contents API
  (`/repos/<o>/<r>/contents/<path>?ref=<ref>`, `Accept:
  application/vnd.github.raw`), since raw.githubusercontent.com does not serve
  private files to a token; then the raw URL without it. Tarballs come from
  `/repos/<o>/<r>/tarball/<sha>`, then the public codeload URL. Branch and tag
  names resolve through `/commits/<ref>` (422: not a ref; 401/403/404: private
  or no access, so `git ls-remote` is tried). Without a token, requests are
  exactly as before.
* **Git fallback.** When the index or a tarball gets 401/403/404 and git is
  installed, the hardened git machinery (`GIT_TERMINAL_PROMPT=0`, no askpass)
  takes over, so the user's own git credential helpers work; the token is never
  handed to git. A tarball becomes a shallow fetch of the pinned commit. The
  index is read alone (`fetch.git_read_file`): a bare temporary repository,
  `git fetch --depth 1 --filter=blob:none origin <ref>` (commits and trees
  only where the server allows filtering, as GitHub does), then `git cat-file`
  of `index.json` with a size cap. Nothing is checked out, so the pack-install
  rules (no symlinks, no `.so`, 5000 files) do not apply to the rest of the
  store repository, and a public repository without an index costs a few
  kilobytes, not a clone. When git reads the repository but finds no index
  (`GitMissing`), the error says so and does not suggest a token. If everything
  fails, the error says how to authenticate (`PYTACHECK_GITHUB_TOKEN`, `gh auth
  token`, `gh auth setup-git`).
* **Other hosts.** A non-GitHub index behind a login reads it from `~/.netrc`
  (or `$NETRC`): an explicit `machine` entry for exactly that host (never
  `default`), sent as HTTP basic auth only over https (or http to a loopback
  host), only to that origin; a redirect to a URL with user info is refused.
  This replaces `https://user:pw@host/index.json` store URLs, which httpx used
  to turn into basic auth and which are now refused (see below). Errors for
  such hosts point to `~/.netrc`, never to `PYTACHECK_GITHUB_TOKEN`.
* **No credentials in URLs.** `auth.has_credentials()` flags a password or
  user info (any scheme; a bare user name in `ssh://git@host` or
  `git@host:o/r` is fine), a secret query or fragment parameter (`token`,
  `access_token`, `private_token`, `x-amz-security-token`, `sig`,
  `signature`) and any token value from the environment. Such a URL is refused
  by `store add` (`stores.check_store_url()`, which a caller can run before
  asking) and when reading a store, and every pack source is checked at the
  top of `install._install()`, which covers URL installs, pin syncs, updates,
  store entries and run-record reinstalls (an unlisted update is checked before
  `resolve_rev`). What an older config or install record already holds is
  never passed on: `store list`/`store update` redact the URL column,
  `describe_source()` (the consent card) and confirmation prompts go through
  `redact()`, and `registry._installed_pack()` strips credentials from the
  pack's `source` (`auth.clean_source()`) with a warning, so run records and
  `pack show` never carry them.
* **No leaks.** Pins, install records, run records and index caches keep the
  canonical source (`{"github": "owner/repo", "rev": ...}`) and the cache key
  stays the raw URL. Errors name the URL that was asked for, never a redirect
  target; git's messages, httpx's request log and httpcore's header log pass
  through `redact()` (token values, secret query values, URL passwords). The
  tests (`tests/modsys/test_private_store.py`, `tests/modsys/test_credentials.py`)
  install, update and run a pack with a sentinel token and grep every written
  file, the logs and the output for it, and seed legacy pins, records, store
  URLs and run records with one; a live test (`-m network`, with a token) does
  the same against the real store, reporting only which output leaked, never
  its text.
