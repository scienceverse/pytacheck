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
  * [Papers: bibr 12.x and the older format](#papers-bibr-12x-and-the-older-format)
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
pytacheck init --preset fields::medicine --preset clinical_trials::only --project --yes
```

With several presets, `init` saves a config preset called `mine` that extends
them all. `extends` is a union: a module that one preset excludes comes back if
another includes it (so the example adds `clinical_trials::only`, not
`clinical_trials`, whose default extends metacheck's default and would bring back
the `ref_replication` that `fields::medicine` leaves out). `--project` writes
`./pytacheck.json` (for a team, commit it) instead of your user config, and pins
every pack the presets need there, so it works as the team's lock file. If the
store cannot be reached, `init` still offers the built-in presets.

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
`metacheck::default`. `-a MOD.KEY=VALUE` must name a selected module and one of
its arguments (a typo is an error, not ignored). When a module fails, `run`
shows why next to it (and in `--json` as `error`), runs the remaining modules,
then exits with status 1.

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
to run" (its `report` holds the error), and the next module runs. Some modules
that metacheck's presets list are not ported to pytacheck yet; they fail this way
until they are.

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

1. downloads the pinned commit (a tarball over HTTPS; git is only a fallback;
   see "Private stores and packs" below for repositories that need a login),
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
  <https://github.com/scienceverse/pytacheck-modules>) reviews submissions
  and records the date of review. Other stores are as trustworthy as whoever
  runs them.
* **Pinning.** A pin is a commit and a hash of the pack's files, so what you
  reviewed is what runs. An update is never automatic.
* **Integrity.** Each run compares the installed files with the install record;
  a modified pack warns and is marked `modified` in the run's provenance.
* **Visibility.** Every output records its module, pack, commit and file hash,
  and `pytacheck run` shows the pack name next to any module that is not built
  in. (HTML reports do not show it yet; see below.)
* **Consent.** You see the consent card before any code is installed.
* **Project files.** A `pytacheck.json` comes with its folder, which may be a
  shared folder or a repository you cloned, so it is not trusted like your own
  config. One owned by another user, or writable by others, is ignored with a
  warning (as git does; name it with `PYTACHECK_CONFIG` to use it anyway). The
  search for it stops at your home folder. Local code it names (a path pack
  `{"path": ...}` or a `.py` module in a preset) stays inactive until you trust
  it: `pytacheck pack install` in the project shows it and asks. Store packs it
  pins run only once installed, which also asks. It cannot add, change or
  remove stores, since a store decides where packs come from: its `stores` are
  ignored with a warning. Stores go in your user config.

Each pack has a trust label: `builtin`, `store` (listed and pinned by a store),
`unlisted` (installed from a URL or at a commit the store does not list), `local`
(a folder on your machine) or `dist` (a pip-installed package).

The REST API server never installs packs and never runs local code: it runs
built-in modules and installed packs only (`use(allow_local=False)`).

### Reproducibility: run records

Every module output carries its provenance (`out.run_provenance`, also readable
as `out.provenance` unless the module returned an element of that name): the
module, pack, version, commit, file hash, effective arguments and whether the
files were modified. A **run record** collects these for a whole run, with the
preset, the modules dropped by `--offline`, the paper IDs and the versions of
pytacheck, metacheck and bibr:

```bash
pytacheck run paper.json --preset psych --record run.json
pytacheck rerun run.json paper.json                 # same modules, commits and arguments
pytacheck rerun run.json paper.json --install       # fetch recorded pack commits if missing
```

```python
chain = pc.run_modules(paper, select(preset="psych"), record="run.json")
chain.run_record                      # the same record, as an object
pc.rerun("run.json", paper)
```

`rerun` refuses to run a module whose file differs from the recorded hash (or an
installed pack that was modified) unless you pass `--allow-modified`; files are
checked before any of their code is imported. A record is data you may have been
sent, so a recorded module file outside the current folder runs only after you
agree (`--yes`). Packs it installs for a rerun are not pinned in your config.
Built-in modules come from the pytacheck you have; a different version only
warns. A module that failed in the recorded run and still cannot be found (a
metacheck module not ported yet) fails again rather than stopping the rerun.

**Not available yet:** HTML reports do not embed the record, and do not show pack
names, until the report renderer runs modules through
`pytacheck.provenance.run_modules()` (it will embed `chain.run_record.to_html()`,
a `<script type="application/json" id="pytacheck-run">` that leaves the visible
report unchanged, and `RunRecord.read("report.html")` will read it back). Until
then, use `pytacheck report ... --record run.json` to write the record next to
the report.

### Configuration files

Config is JSON, in two scopes: the **user** file
(`platformdirs.user_config_dir("pytacheck")/config.json`, e.g.
`~/.config/pytacheck/config.json` on Linux) and the **project** file (the nearest
`pytacheck.json` in the working directory or above it). The project wins over
the user file; `packs` and `presets` merge by key, and `null` removes an entry.
`stores` are read from the user file only (see "Project files" above).

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
`PYTACHECK_PRESET`, `PYTACHECK_STORE_URL` (a mirror for the `pytacheck` store),
`PYTACHECK_GITHUB_TOKEN` (read access to private GitHub stores and packs; see
below) and, for the API, `PYTACHECK_API_MAX_CHECKS` (how many uploads are checked at
once; default: the number of CPUs).

**Stores.** The store `pytacheck` is built in. Add your lab's or institute's to
your user config (a project's `pytacheck.json` cannot hold stores):

```bash
pytacheck store add mylab https://github.com/mylab/pytacheck-store
pytacheck store list
pytacheck store update          # re-fetch indexes (they are cached for an hour)
pytacheck store remove mylab
```

A store URL can be a GitHub or GitLab repository, a URL of an `index.json`, or a
local folder. Offline, cached indexes are used (with a warning).

**Private stores and packs.** A private GitHub store (the official one, while
it is private) or pack repository needs read access, in either of two ways:

* a GitHub token in `PYTACHECK_GITHUB_TOKEN` (or `GH_TOKEN` / `GITHUB_TOKEN`; the
  first one set wins), for example `export PYTACHECK_GITHUB_TOKEN=$(gh auth token)`
  or a fine-grained token with "Contents: read" on the repository. pytacheck then
  reads the index and pack tarballs through GitHub's API;
* git credentials for github.com (for example `gh auth setup-git` or a credential
  helper): when GitHub answers 401/403/404, pytacheck falls back to git, which
  never prompts. For a store index it fetches only commits and trees
  (`--depth 1 --filter=blob:none`) and reads `index.json` alone, so nothing else
  of the repository is downloaded or checked out.

The token is sent only over HTTPS to `api.github.com`, `github.com`,
`raw.githubusercontent.com` and `codeload.github.com`, never on a redirect to
another host, and is never written anywhere: pins, install records, run records
and index caches keep the plain source (`{"github": "owner/repo", "rev": ...}`),
so they work on a machine that authenticates differently. An expired token does
not break public stores (a 401 is retried without it).

A store on another host whose `index.json` needs a login (an intranet server
with HTTP basic auth, say) reads it from `~/.netrc`, or from the file `NETRC`
names:

```text
machine store.example.org login alice password <secret>
```

Only an explicit `machine` entry for exactly that host is used (never
`default`), only over HTTPS (or plain HTTP to this machine), and only for the
host the index URL names, not on a redirect elsewhere. GitLab stores and pack
repositories on other hosts use your git credentials (a credential helper, or
`~/.netrc`, which git reads too).

**No credentials in URLs.** Store URLs and pack sources are shown on screen and
written to config, install records and run records, so a URL that carries a
credential is refused: a password or user info (`https://user:pw@...`,
`https://TOKEN@...`, `ssh://git:pw@...`), or a secret query parameter
(`?token=`, `?access_token=`, `?private_token=`, `?sig=`...). The error shows
the URL redacted and says what to do instead. (Before, `https://u:pw@host/...`
store URLs were accepted and sent as HTTP basic auth: move such logins to
`~/.netrc`.) A store or pin from an older config that carries a credential is
never shown or recorded with it: `store list` shows it as `https://***@...`, the
store is skipped with a warning, the pinned pack is not (re)installed or
updated, and an already installed one runs with the credential left out of its
run records (with a warning to pin the plain URL).

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
* it works on a single paper and on a paper list, and on bibr 12.x papers as well
  as older ones (see the next section);
* modules that call online services declare `requires=["network"]`, and those
  that use an LLM declare `requires=["llm"]`, so `--offline` can skip them;
* validation: describe it in a `<validation>` block in `details`, and give the
  numbers in `validation=`, from which tools compute PPV and sensitivity. An
  unvalidated module is allowed, but `pack check` warns about it.

Use `get_prev_outputs("other_module", "table")` to read an earlier module's
output in the same run. Use the helpers pytacheck exports (`text_search`,
`pytacheck.report.scroll_table`, `collapse_section`, ...); see the built-in
modules in `src/pytacheck/modules/` for complete examples.

### Papers: bibr 12.x and the older format

bibr export schema 12.0 is pytacheck's paper schema: bibr's exports and Grobid
conversions are read as 12.x papers. Modules also get papers in the older format that
metacheck used before, such as bibr v10.x files, `demopaper()` and metacheck's fixture
papers, and one paper list can hold both. The [README](../README.md#the-paper-schema-bibr-export-schema-120)
lists a 12.x paper's tables. These are the differences a module usually has to handle:

| | bibr 12.x paper | older paper |
|---|---|---|
| how to tell | `is_bibr12(paper)`: `info.schema_version` starts with `12.` | no `schema_version` |
| citation of a reference | `xref_type` `"bib"`; the `bib_id` is in `target_id`, and `xref_id` is the xref's own key | `xref_type` `"bibr"`; the `bib_id` is in `xref_id` |
| figure and table captions | `figure.caption` and `table.caption`, plus a `text` row with no `section_id`, which `text_id` points at | `text` rows in a section whose `section_type` is `"figure"` or `"table"` |
| footnotes | the `footnote` table, plus `text` rows with no section | `text` rows in a `"foot"` section |
| match scores (`bib_match`, `info_match`) | 0-1 | Crossref relevance scores (for example 61.8) |
| how it was extracted | `paper.get("extraction")`: `producer`, `converter`, `completed_at`, diagnostics | `None` |

`text_search()` finds caption and footnote text in both formats. To search only the
body, drop the rows without a `section_id` in a 12.x paper, and the `figure`, `table`
and `foot` sections in an older one.

Work on whole tables, as the built-in modules do: take `paper_table()` over the paper
or paper list, then choose each row's rule from its `paper_id`. This is the pattern of
metacheck's `.bibr12_paper_ids()` (`ref_consistency`, `ref_miscitation` and
`ref_accuracy` use it):

```python
import pytacheck as pc
from pytacheck.io.bibr12 import is_bibr12


def bibr12_paper_ids(paper) -> set[str]:
    """IDs of the bibr 12.x papers in a paper or a paper list."""
    papers = [paper] if isinstance(paper, pc.Paper) else list(paper)
    return {p.paper_id for p in papers if is_bibr12(p)}


def citations(paper):
    """paper_id, bib_id and text_id of every citation of a reference."""
    xref = pc.paper_table(paper, "xref").copy()  # never change the paper's own table
    v12 = xref["paper_id"].isin(bibr12_paper_ids(paper))
    if v12.any():  # only 12.x papers have target_id
        xref.loc[v12, "xref_id"] = xref.loc[v12, "target_id"]
    typ = xref["xref_type"]
    cites = xref[((v12 & (typ == "bib")) | (~v12 & (typ == "bibr"))).fillna(False)]
    return cites.rename(columns={"xref_id": "bib_id"})[["paper_id", "bib_id", "text_id"]]
```

Thresholds on match scores work the same way. `ref_accuracy` compares a 12.x paper's
score with `suggest_score / 100`.

`pack check` runs modules on older papers only (`demopaper()` and test papers), so
also try yours on a 12.x paper: `pc.read(pc.demofile("xml"))` converts the demo
paper's Grobid TEI to 12.x, and any bibr export is one.

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

The official store is <https://github.com/scienceverse/pytacheck-modules>.
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
date together with the tree hash of the files they read
(`"reviewed": "2026-09-01", "reviewed_tree_sha256": "..."` in `packs/<name>.json`);
they can also mark a pack `yanked`. Everything else in the store's `index.json` is
computed by `pytacheck store build`; nobody edits it by hand. A change to a pack
clears its review date until it is reviewed again (and `store build --check`
fails until then). CI runs `pack check` on every changed pack; for a pack in its
own repository it fetches the listed commit first
(`pytacheck pack check packs/<name>.json`).

You can also run your own store: any git repository with `packs/` and an
`index.json` built by `pytacheck store build . [--repo OWNER/REPO]` (a private
repository works too, see "Private stores and packs" above).

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
| `run_modules(paper, selection, record=None)` | run in order; returns a `ModuleChain` with `.run_record` (written to `record` if given) |
| `use(preset=..., allow_local=..., offline=...)` | scope settings to a `with` block |
| `run_session()` | memoise repeated module runs in a `with` block (`run_modules`, the CLI and the API use it; do not edit a paper's tables inside one, as edits within a table are not detected) |
| `pack_install(ref=None, scope="user", yes=False)` | install (or sync all pins) |
| `pack_remove(name)`, `pack_update(name=None)`, `pack_list()`, `pack_show(name)` | manage packs |
| `pack_check(path)`, `pack_new(name, path=".")`, `module_template(name)` | author packs |
| `store_list()`, `store_add(name, url)`, `store_remove(name)`, `store_update(name=None)`, `store_search(text, field)` | stores |
| `RunRecord.read(path)`, `rerun(record, paper)` | run records |
| `refresh()` | forget cached config, packs and pack modules |
