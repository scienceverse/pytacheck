# Modules, presets and packs

pytacheck's checks are **modules**. The built-in ones are ported from metacheck.
Anyone can write more, bundle them in a **pack**, and share the pack through a
**store**. **Presets** choose which modules run, in which order and with which
arguments. This page covers using all of that (part 1) and writing modules and
packs (part 2). The design, with the reasons behind it, is in
[design/module-system-v2.md](design/module-system-v2.md).

* [Part 1: Using modules](#part-1-using-modules)
  * [Presets](#presets)
  * [Setting up with `pytacheck init`](#setting-up-with-pytacheck-init)
  * [Choosing modules at run time](#choosing-modules-at-run-time)
  * [Installing packs](#installing-packs)
  * [Trust: what you are agreeing to](#trust-what-you-are-agreeing-to)
  * [Reproducibility: run records](#reproducibility-run-records)
  * [Configuration files](#configuration-files)
* [Part 2: Writing modules and packs](#part-2-writing-modules-and-packs)
  * [Write a module](#write-a-module)
  * [Make a pack](#make-a-pack)
  * [Preset-only packs](#preset-only-packs)
  * [Test with `pack check`](#test-with-pack-check)
  * [Publish to the store](#publish-to-the-store)
  * [Licensing](#licensing)
  * [Dual-language packs (Python and R)](#dual-language-packs-python-and-r)
* [Reference: the Python API](#reference-the-python-api)

---

## Part 1: Using modules

A module is referred to by:

* its **name**, `"marginal"`. Built-in names always mean the built-in module;
* a **qualified ref**, `"pack::name"` (R's namespace syntax). `"metacheck::marginal"`
  is the same as `"marginal"`;
* a **path** to a `.py` file, as before.

However it is addressed, a module's output is labelled with its bare name
(`ModuleOutput.module`), so chaining with `get_prev_outputs()` and the
`summary_table` column suffixes work the same way.

```bash
pytacheck modules                 # built-in modules
pytacheck modules --all           # ... plus every active pack's, as pack::name
pytacheck modules --pack psych    # one pack
pytacheck modules psych::apa_df   # help for one module
```

When a module declares how it was validated, the listings show its positive
predictive value (PPV, the share of its flags that were right) and sensitivity
(the share of real cases it found).

### Presets

A preset is a named, ordered list of modules, optionally with arguments for them.
Three are built in and mirror lists hard-coded in metacheck:

| preset | what it runs |
|---|---|
| `metacheck::default` | the modules `report()` runs by default in metacheck |
| `metacheck::repository` | the modules `report_repository()` runs |
| `metacheck::validated` | the validated modules of metacheck's Shiny app |

```bash
pytacheck presets                          # every available preset
pytacheck presets metacheck::default       # what it runs, with arguments
pytacheck presets psych --as-r             # the same run as a metacheck call in R
```

Packs add presets (`psych::default`, or just `psych`), and you can define your own
in a config file (see [Configuration files](#configuration-files)). A preset can
build on others: `extends` includes other presets, `exclude` drops modules,
`replace` swaps one module for another *in the same position* (so later modules
that read its output still work) and `modules` adds more.

```json
"presets": {
  "thesis": {
    "extends": ["fields::psychology"],
    "exclude": ["ref_pubpeer"],
    "replace": {"power": "psych::power_sesoi"},
    "args": {"stat_check": {"alpha": 0.01}}
  }
}
```

### Setting up with `pytacheck init`

`pytacheck init` asks which fields you work in, offers matching presets (the
built-in ones and those in the store), installs the packs they need (asking
first), and saves your choice as the default:

```bash
pytacheck init                                        # interactive
pytacheck init --preset fields::psychology --yes      # non-interactive
pytacheck init --preset fields::medicine --preset clinical_trials --project --yes
```

With several presets, `init` saves a config preset called `mine` that extends
them all. `--project` writes `./pytacheck.json` (for a team, commit it) instead of
your user config. If the store cannot be reached, `init` still offers the
built-in presets.

### Choosing modules at run time

On the command line (`run` and `report` take the same flags):

```bash
pytacheck run paper.json                              # your default preset
pytacheck run paper.json --preset metacheck::repository
pytacheck run paper.json -m marginal -m psych::apa_df # exactly these, in order
pytacheck run paper.json --preset psych -m trial_registration   # preset, then extras
pytacheck run paper.json -a power.seed=8675309        # an argument for one module
pytacheck run paper.json -a seed=1                    # ... for every module that takes it
pytacheck run paper.json --offline                    # skip modules needing network/LLMs
```

The CLI prints which preset it used and where that choice came from. When no
preset or module is given, the choice is, in order: `pc.use(preset=...)`,
`PYTACHECK_PRESET`, the project config, the user config, then
`metacheck::default`.

In Python, the **library keeps metacheck's defaults**: `report(paper)` runs what
metacheck's `report()` runs, whatever your config says. Choose explicitly:

```python
import pytacheck as pc
from pytacheck.presets import select

pc.preset("psych")                        # [(module, args), ...]
sel = select(preset="psych", args={"power": {"seed": 1}})
chain = pc.run_modules(paper, sel)        # runs in order, chaining outputs
chain.last.summary_table

with pc.use(preset="psych", offline=True):  # scoped to this block (and thread)
    chain = pc.run_modules(paper, select())
```

A module that fails does not stop the run: as in metacheck's `report()`, it
becomes an output with the traffic light `fail` and the text "This module failed
to run", and the next module runs.

### Installing packs

```bash
pytacheck pack search trial --field medicine    # browse the stores
pytacheck pack show clinical_trials             # details, read without running code
pytacheck pack install clinical_trials          # from a store
pytacheck pack install pytacheck/clinical_trials   # from a named store
pytacheck pack install psych@1a2b3c4            # a specific commit
pytacheck pack install https://github.com/jane/pytacheck-psych@v1.2   # not in a store
pytacheck pack install ./my-pack                # a local folder (for development)
pytacheck pack install                          # install every pinned pack (e.g. after cloning a project)
pytacheck pack list                             # active packs
pytacheck pack update [NAME]                    # newer revisions, after showing the changes
pytacheck pack remove NAME
```

Add `--project` to pin in `./pytacheck.json` rather than your user config, and
`--yes` to skip the confirmation. The Python functions are the same:
`pc.pack_install("clinical_trials")`, `pc.pack_list()`, and so on.

Installing:

1. downloads the pinned commit (a tarball over HTTPS; git is only a fallback),
2. extracts it safely (no links, device files, absolute or `..` paths; at most
   50 MB and 5000 files),
3. checks the files' hash against the store's index,
4. shows a **consent card** and asks `[y/N]`:
   the store and review date, the source and commit, every file with its size,
   what the pack requires, and every import its code makes, with `subprocess`,
   `eval`/`exec`, sockets, native code and HTTP libraries highlighted. A pack
   without code says so;
5. moves the pack to `<data>/packs/<name>/<commit>/`, makes its files
   read-only, and records the pin in your config.

Nothing from the pack is imported during installation. Packs that need other
Python packages list them; pytacheck prints the `pip install` command but never
runs pip.

Pinned packs switch without a restart: after `pack install` or `pack update`, or
after you edit a config file, the next module lookup uses the new state, in a
notebook or in a running API server alike.

### Trust: what you are agreeing to

**There is no sandbox.** An installed pack runs with your permissions, like any
Python package, R package or metacheck `./modules/*.R` file. What pytacheck does
instead:

* **Curation.** The official store (`pytacheck`,
  <https://github.com/thesanogoeffect/pytacheck-modules>) reviews submissions
  and records the date of review. Other stores are as trustworthy as whoever
  runs them.
* **Pinning.** A pin is a commit and a hash of the pack's files, so what you
  reviewed is what runs. An update is never automatic.
* **Integrity.** Each run compares the installed files with the install record;
  a modified pack warns and is marked `modified` in the run's provenance.
* **Visibility.** Every output records its module, pack, commit and file hash,
  and reports show the pack name next to any module that is not built in.
* **Consent.** You see the consent card before any code is installed.

Each pack has a trust label: `builtin`, `store` (listed and pinned by a store),
`unlisted` (installed from a URL or at a commit the store does not list), `local`
(a folder on your machine) or `dist` (a pip-installed package).

The REST API server never installs packs and never runs local code: it runs
built-in modules and installed packs only (`use(allow_local=False)`).

### Reproducibility: run records

Every module output carries its provenance (`out.provenance`): the module, pack,
version, commit, file hash, effective arguments and whether the files were
modified. A **run record** collects these for a whole run, with the preset, the
modules dropped by `--offline`, the paper IDs and the versions of pytacheck,
metacheck and bibr:

```bash
pytacheck run paper.json --preset psych --record run.json
pytacheck rerun run.json paper.json                 # same modules, commits and arguments
pytacheck rerun run.json paper.json --install       # fetch recorded pack commits if missing
```

```python
chain = pc.run_modules(paper, select(preset="psych"))
chain.run_record.write("run.json")
pc.rerun("run.json", paper)
pc.RunRecord.read("report.html")      # records embedded in HTML reports
```

`rerun` refuses to run a module whose file differs from the recorded hash (or an
installed pack that was modified) unless you pass `--allow-modified`. Packs it
installs for a rerun are not pinned in your config. Built-in modules come from the
pytacheck you have; a different version only warns.

HTML reports embed the record as
`<script type="application/json" id="pytacheck-run">`, so the visible report is
unchanged. (Report integration: the report renderer calls
`pytacheck.provenance.run_modules()` and embeds `chain.run_record.to_html()`.)

### Configuration files

Config is JSON, in two scopes: the **user** file
(`platformdirs.user_config_dir("pytacheck")/config.json`, e.g.
`~/.config/pytacheck/config.json` on Linux) and the **project** file (the nearest
`pytacheck.json` in the working directory or above it). The project wins over
the user file; `stores`, `packs` and `presets` merge by key, and `null` removes an
entry.

```json
{
  "preset": "thesis",
  "stores": {"mylab": "https://gitlab.com/mylab/pytacheck-store"},
  "packs": {
    "psych": {"source": {"github": "janedoe/pytacheck-psych"}, "rev": "<40 hex>",
              "tree_sha256": "<64 hex>", "version": "1.2.0", "store": "pytacheck"},
    "labmods": {"path": "../lab-modules"}
  },
  "presets": {"thesis": {"extends": ["psych::default"], "exclude": ["ref_pubpeer"]}}
}
```

Commands write these files for you (`init`, `pack install`, `store add`...). A
committed `pytacheck.json` with pins works as a team lock file: teammates run
`pytacheck pack install` to get the same commits.

Environment variables: `PYTACHECK_CONFIG` (use only this file; `none` for no
config at all), `PYTACHECK_DATA_DIR` (installed packs and store caches),
`PYTACHECK_PRESET`, `PYTACHECK_STORE_URL` (a mirror for the `pytacheck` store)
and, for the API, `PYTACHECK_API_MAX_CHECKS` (how many uploads are checked at
once; default: the number of CPUs).

**Stores.** The store `pytacheck` is built in. Add your lab's or institute's:

```bash
pytacheck store add mylab https://github.com/mylab/pytacheck-store
pytacheck store list
pytacheck store update          # re-fetch indexes (they are cached for an hour)
pytacheck store remove mylab
```

A store URL can be a GitHub or GitLab repository, a URL of an `index.json`, or a
local folder. Offline, cached indexes are used (with a warning).

---

## Part 2: Writing modules and packs

### Write a module

A module is a Python function decorated with `@module`, in a file with the same
name. Start from the template (a port of metacheck's `module_template()`):

```bash
python -c "import pytacheck as pc; pc.module_template('my_check')"   # ./modules/my_check.py
pytacheck run paper.json -m my_check                                 # found in ./modules
```

```python
from pytacheck.module import module
from pytacheck.text import text_search


@module(
    title="Significance Wording",
    description="List sentences that call a result significant.",
    details="""
        How it works, in markdown (shown in reports and `pytacheck modules NAME`).

        <validation>In a sample of 40 papers with 120 statements, ...</validation>
    """,
    keywords=["results"],     # the report section: general, intro, method, results,
                              # discussion or reference
    author=["Jane Doe <jane@uni.edu>"],
    requires=[],              # "network" and/or "llm" if the module needs them
    validation={"papers": 40, "instances": 120, "tp": 100, "fp": 12, "fn": 20,
                "reference": "https://osf.io/abcd3"},
)
def my_check(paper, pattern="significant"):
    table = text_search(paper, pattern)
    summary_table = table.groupby("paper_id").size().rename("n_significant").reset_index()
    return {
        "table": table,                      # details, for later modules and users
        "summary_table": summary_table,      # one row per paper (paper_id + columns)
        "na_replace": 0,                     # fill papers without rows
        "traffic_light": "info" if len(table) else "na",
        "summary_text": "...",               # one line at the top of the report
        "report": ["markdown text", ...],    # the module's section of the report
    }
```

The contract (the same one the built-in modules follow):

* the function's name equals the file name, and it has a title, a description and
  one section keyword;
* it never modifies the paper it receives (copy tables before changing them);
* `traffic_light` is one of `na`, `fail`, `info`, `green`, `yellow`, `red`;
* `summary_table` has a `paper_id` column and at most one row per paper;
* it works on a single paper and on a paper list;
* modules that call online services declare `requires=["network"]`, and those
  that use an LLM declare `requires=["llm"]`, so `--offline` can skip them;
* validation: describe it in a `<validation>` block in `details`, and give the
  numbers in `validation=`, from which tools compute PPV and sensitivity. An
  unvalidated module is allowed, but `pack check` warns about it.

Use `get_prev_outputs("other_module", "table")` to read an earlier module's
output in the same run. Use the helpers pytacheck exports (`text_search`,
`pytacheck.report.scroll_table`, `collapse_section`, ...); see the built-in
modules in `src/pytacheck/modules/` for complete examples.

### Make a pack

A pack is a folder with a `pack.json` and module files:

```
my-pack/
  pack.json
  my_check.py           # a module (every *.py not starting with _)
  _helpers.py           # a helper: `from ._helpers import x` in modules
  my_check.R            # optional: the metacheck version (ignored by pytacheck)
  tests/                # your tests (ignored by the loader)
  README.md  LICENSE  CITATION.cff
```

`pytacheck pack new my-pack` creates one, with an example module, a test, a
README, an MIT licence, `CITATION.cff` and a GitHub workflow that runs
`pack check`:

```bash
pytacheck pack new my-pack
pytacheck pack install ./my-pack    # use it live while developing (edits apply at once)
pytacheck modules --pack my-pack
```

`pack.json`:

```json
{
  "schema": 1,
  "name": "my-pack",
  "version": "0.1.0",
  "title": "One line",
  "description": "What it checks, for which field.",
  "authors": ["Jane Doe <jane@uni.edu>"],
  "license": "MIT",
  "homepage": "https://github.com/jane/my-pack",
  "fields": ["psychology"],
  "keywords": ["apa"],
  "requires": {"pytacheck": ">=0.3"},
  "dependencies": [],
  "presets": {
    "default": {"description": "metacheck's defaults plus mine",
                "extends": ["metacheck::default"], "modules": ["my_check"]}
  }
}
```

* Names: packs `^[a-z][a-z0-9_-]{1,39}$` (not `metacheck`, `local`, `pytacheck`
  or `modules`); modules `^[A-Za-z][A-Za-z0-9_]*$`.
* `fields`: `general`, `psychology`, `medicine`, `neuroscience`, `economics`,
  `education`, `biology`, `ecology`, `sociology`, `political-science`,
  `computer-science`, `physics`.
* `dependencies`: other Python packages (PEP 508, e.g. `"scikit-learn>=1.4"`).
  Prefer none: every dependency is something users must install and trust.
* In a pack's presets, a bare name is the pack's own module if it has one; refer
  to other packs' modules as `pack::name`.

### Preset-only packs

A pack does not need code. A **preset-only pack** is just a `pack.json` with
presets that pick and configure existing modules, for example "the checks
psychologists should run, with these arguments". It is the easiest pack to
review and to trust, and often all a field needs. The store's `fields` pack is
one:

```json
{
  "schema": 1, "name": "fields", "version": "0.1.0", "license": "CC0-1.0",
  "presets": {
    "psychology": {
      "description": "...",
      "extends": ["metacheck::default"],
      "modules": ["ethics_check", "open_practices", "all_p_values", "causal_claims"]
    }
  }
}
```

### Test with `pack check`

```bash
pytacheck pack check ./my-pack          # exit code 1 on errors; --json for CI
```

It checks that `pack.json` is valid, that each module meets the contract above
(it runs each one on the demo paper, edge-case test papers and a paper list, and
checks it does not modify them), that network use is declared (a static scan for
`pytacheck.http`, `httpx`, `requests`, `urllib` and `socket`), that validation is
described, and that presets expand. Modules that need the network or an LLM are
not run. Add your own tests in `tests/` and run them with `pytest`.

### Publish to the store

The official store is <https://github.com/thesanogoeffect/pytacheck-modules>.
There are two ways in; both are a pull request:

1. **A folder in the store** (no repository of your own needed): add
   `packs/<name>/` with your `pack.json` and modules, for example with GitHub's
   web editor ("Add file" > "Upload files"). The store's CI runs `pack check`
   on it, and once merged, `store build` lists it at the commit that last
   changed the folder.
2. **Your own repository**: add `packs/<name>.json` to the store:

   ```json
   {"name": "my-pack", "source": {"github": "jane/my-pack", "rev": "<40-hex commit>"}}
   ```

   To release a new version, bump `version` in your `pack.json`, tag a commit,
   and open a pull request changing `rev`.

Maintainers review the code (see the store's `REVIEW.md`) and record the review
date; they can also mark a pack `yanked`. Everything else in the store's
`index.json` is computed by `pytacheck store build`; nobody edits it by hand. A
change to a pack clears its review date until it is reviewed again.

You can also run your own store: any git repository with `packs/` and an
`index.json` built by `pytacheck store build . [--repo OWNER/REPO]`.

### Licensing

Packs run inside pytacheck, which is AGPL-3.0-or-later. The official store asks
for an OSI-approved licence in `pack.json` (`MIT`, `BSD-3-Clause`, `Apache-2.0`,
`GPL-3.0-or-later`, ...; `CC0-1.0` suits preset-only packs). If your module ports
or adapts code from elsewhere, keep its licence and credit its authors.

### Dual-language packs (Python and R)

pytacheck loads only `.py` files, and metacheck's `module_find()` loads `.R` files
from a folder, so a pack can ship both: `trial_registration.py` next to
`trial_registration.R`. pytacheck ignores the `.R` file; metacheck users can run
`report(paper, modules = "path/to/pack/trial_registration.R")`, and
`pytacheck presets <preset> --as-r` then uses the `.R` path. `pack.json`,
`index.json` and run records are JSON that R's `jsonlite` reads, and store
entries list each pack's `languages`.

---

## Reference: the Python API

| function | what it does |
|---|---|
| `module_run(paper, "pack::name", **args)` | run one module |
| `module_list()` / `module_list(pack="psych")` / `module_list(pack="*")` | list modules |
| `preset(ref)`, `preset_list()` | a preset's modules and arguments; every preset |
| `presets.select(modules, preset, args, offline=...)` | what a call would run |
| `run_modules(paper, selection)` | run in order; returns a `ModuleChain` with `.run_record` |
| `use(preset=..., allow_local=..., offline=...)` | scope settings to a `with` block |
| `run_session()` | memoise repeated module runs in a `with` block |
| `pack_install(ref=None, scope="user", yes=False)` | install (or sync all pins) |
| `pack_remove(name)`, `pack_update(name=None)`, `pack_list()`, `pack_show(name)` | manage packs |
| `pack_check(path)`, `pack_new(name, path=".")`, `module_template(name)` | author packs |
| `store_list()`, `store_add(name, url)`, `store_remove(name)`, `store_update(name=None)`, `store_search(text, field)` | stores |
| `RunRecord.read(path)`, `rerun(record, paper)` | run records |
| `refresh()` | forget cached config, packs and pack modules |
