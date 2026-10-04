# Data package checks

A **data package** is the folder of data, code and documentation that a researcher
archives or shares with a paper. The data package checks look at that folder as a
whole before it is archived: are there junk or temporary files, are the files in
formats and with names that last, is the folder tree understandable, is there a
README, are the data columns documented. They are meant for the person who
receives packages (a data steward, a repository curator) and for researchers
checking their own package before they hand it in. Nothing needs a paper.

The checks are modules of the `datapackage` pack, which every installation has.
They are pytacheck's own: metacheck has no counterpart.

## Run it

```bash
metacheck package my_data_folder                   # a folder
metacheck package my_data.zip                      # a zip, or .tar .tar.gz .tgz .tar.bz2 .tar.xz
metacheck package my_data.zip -o package.html      # write a report instead of printing
metacheck package my_data.zip -f md                # ... as Markdown, to my_data_report.md
metacheck package my_data_folder --json            # the results as JSON
metacheck package my_data_folder --record run.json # also write a run record
```

(`pytacheck package ...` is the same command.) Without `-o` the results are printed,
one line per check, followed by the checklist (see below). With `-o FILE`, or with
`-f html|qmd|md` alone (the file is then `<name>_report.<format>` in the current
folder), the report is written and the results are printed too. A report written
inside the package's own folder is not counted among its files.

The exit status is 0 when every check ran, 1 when a check failed to run (the others
still run, and the failure is shown next to it), and 2 when the path cannot be
opened: it does not exist, it is a file that is not a supported archive, or an
archive is unreadable or too big (more than 20 GB extracted, or 200,000 files).

Choose the checks like for papers, with `-m`, `--preset` and `-a` (see
[MODULES.md](MODULES.md#choosing-modules-at-run-time)):

```bash
metacheck package my_data_folder -m package_files -m package_docs
metacheck package my_data_folder --preset myoffice::archive
metacheck package my_data_folder -a data_check.concepts=rules   # an argument for one module
metacheck package my_data_folder -a concepts=rules              # ... for every module that takes it
```

With no `-m` and no `--preset`, the preset `datapackage::default` runs, whatever
preset you have configured for papers. It runs, in this order, the three checks
below and then two of metacheck's modules that read the data files themselves:
`metacheck::data_check` (classifies the files and describes every column of the
data files, with a data-quality screen) and `metacheck::codebook_check` (whether
each column is documented in a codebook or README).

In Python:

```python
from metacheck.datapackage import check_package, report_package

chain = check_package("my_data.zip")                  # a list of module outputs
for out in chain:
    print(out.title, out.traffic_light, out.summary_text)
chain[0].table                                        # a module's own table
chain.run_record.package                              # {"name": ..., "source": ..., "archive": True}

check_package("my_data_folder", modules=["package_files"])
check_package("my_data_folder", args={"data_check": {"concepts": "rules"}})
check_package("my_data_folder", preset="myoffice::archive", record="run.json")

report_package("my_data.zip", "package.html")         # html, qmd or md; the title is the package's name
```

`check_package(path, *, preset=None, modules=None, args=None, record=None)` returns
the outputs in run order (a `ModuleChain`). `report_package(path, output_file=None,
output_format="html", *, preset=None, modules=None, args=None)` returns the report's
module outputs, with the file's path in `save_path`. Both raise
`metacheck.datapackage.PackageError` for a path that cannot be opened.
`modules` beats `preset`, which beats the default; `args` maps a module's name to
extra arguments for it, as in `report()`. Both also take `max_bytes` and `max_files`,
which lower the limits on an extracted archive (20 GB and 200,000 files otherwise);
a folder has none.

## In the app

`metacheck-app` has a second page, "Check a data package" (`/package`; open the app on
it with `metacheck-app --page package`). Name a folder on your computer or upload a zip
(up to 1 GB; for a bigger package give the folder), choose the checks and press the
button. The page shows one row per check, the checklist with one row per requirement
(the status cell is red for `fail`, yellow for `warn` and `manual`, green for `pass`;
the word is always shown) and the report to download. It runs on your machine only and
is not part of the hosted app (`--hosted` has no such page, because a folder is named by
a path on the machine that runs the app).

The choice lists the presets that run a `datapackage::` check: `datapackage::default`
and every preset of an installed pack or of your config that runs one (for example one
that `extends` it), found through the pack registry. A preset for papers is not listed.
The box for the local classifier is ticked, because the classifier is the default of
`metacheck package` too: `data_check` names the concept of each data column with it,
which downloads its model (about 840 MB) the first time. Without the `concepts` extra
(the installer does not add it) the page does not fail. It names the concepts by rules
only, says so under the results and gives the command that installs the extra. Untick
the box to use rules only and download nothing. An uploaded archive is unpacked as
described below, with lower limits: 5 GB unpacked and 100,000 files.

A folder you type must be inside your home folder. The path is resolved first (`~` and
`..` are worked out and links are followed), and the folder that results is the one
tested, so a link in your home folder that leads elsewhere is refused. The page quotes
the README in its report, and another program on your computer that holds the app's
token could otherwise ask the page to read any folder you can. The refusal says where
folders may be and why. To allow other folders, set `METACHECK_APP_ROOTS` to them
(separated by `:` on Linux and macOS, `;` on Windows) before you start the app; see
[ENVIRONMENT.md](ENVIRONMENT.md). A zip that you upload can come from anywhere.

## What is checked

Each check is a module that takes the package as `local_path`. The sections below
say in a sentence what a check covers; the options of each are in the module's help.

### `package_files`

Junk and temporary files, file formats and file and folder names. Checklist items:
`junk_files` (`.DS_Store`, `~$` lock files, `__MACOSX`, `.git`, ...), `file_formats`
(files outside the list of preferred archival formats, by default based on the DANS
list; a PDF counts only as PDF/A), `file_names` (spaces, special characters, accents,
too long names or paths) and `naming_convention` (names that separate words
differently from the rest, dates not written as YYYY-MM-DD, numbers without leading
zeros). The format list, the junk rules, the limits and the severity of each rule
are options.

Options: see `metacheck modules datapackage::package_files`.

### `package_structure`

The folder tree. Checklist items: `folder_tree` (a snapshot of the tree, in the
report), `folder_depth` (no more than three levels by default), `folder_layout`
(whether the folders follow a recognised layout: data, code and documentation;
Psych-DS; or one folder per figure) and `empty_folders`. The layouts and the depth
are options.

Options: see `metacheck modules datapackage::package_structure`.

### `package_docs`

The README, and whether the package has the parts it should have. Checklist items:
`readme_present`, `readme_format` (plain text or Markdown), `readme_sections` (a
description, the authors and a contact, the files; methods, how to reproduce, the
licence and the variables if there), `readme_placeholders` (template text such as
`<provide DOI>` left in), `readme_contact` (an e-mail address), `readme_file_list`
(the README names the folders and data files), `licence`, and `components` with one
`component:<id>` row per part: README, data, code, codebook, data management plan,
ethical approval and informed consent. Whether the last two are needed depends on
whether the research involved people: give `human_participants`, or the check looks
at the paper's text if there is one, and otherwise leaves them to a person
(`manual`). The README template and the catalogue of parts are options, so a pack
can supply its institution's own.

Options: see `metacheck modules datapackage::package_docs`.

## The checklist

A package check does not end with a traffic light. Each check also returns a
**checklist**: one row per requirement, which is what a data steward works down.
The columns are `item` (a stable id such as `junk_files`), `title`, `status` and
`detail` (one sentence on what was found). `status` is one of:

| status | meaning |
|---|---|
| `fail` | the package does not meet this requirement |
| `warn` | it could be better |
| `pass` | it meets it |
| `manual` | a person has to decide (for example, whether the research involved people) |
| `na` | does not apply |

The command prints the checklists of all checks as one table after the results,
and `--json` has them as a `checklist` list on each check that returns one. In
Python it is `out["checklist"]`, a pandas table. A check's traffic light follows
from its checklist: red for any `fail`, yellow for any `warn` or `manual`, green
otherwise. Item ids are stable, so a pack can collect the rows of several checks
into one list in its own order.

Alongside the checklist a check has its **findings**: the details, one row per file
or folder to fix or look at (`path`, `check`, `rule`, `severity`, `detail`). They are
the module's `table`.

## Everything runs on your machine

A data package can hold personal data, so the checks do not upload it.

* A folder is read in place and never changed. A zip or tar archive is extracted to
  a private temporary folder, and the folder is removed when the run ends, also when
  a check fails. Members whose path would leave that folder (`..`, absolute paths),
  links and device files are skipped.
* Every selected module that takes `local_path` and `local_only` gets the package's
  folder and `local_only=True`, so nothing is looked up online. A value you give
  yourself (`-a MODULE.local_only=false`), or that a preset sets, is not overridden.
  The modules run on a stand-in paper with no content, titled with the package's name.
* `--offline` leaves out the modules that declare they need the network or an LLM.
  Note that `metacheck::data_check` declares both, so with `--offline` it is
  left out of the list (`codebook_check` still reads the data files itself).
* No LLM is used unless you have turned it on yourself (`llm_use(True)` in Python).
* One thing does download: `data_check` fills the concept of a data column (age,
  reaction time, ...) with a local classifier when the `concepts` extra is installed
  (`pip install "metacheck[concepts]"`). The first run fetches that model (about 840 MB)
  from the Hugging Face Hub once and keeps it in the cache folder; it then runs
  without a connection, and no file content is sent. To skip it, pass
  `-a concepts=rules` (or set `METACHECK_CONCEPTS=rules`). Without the extra, the
  classifier is not used.
* The run record (`--record`) says which package was checked (`package`: `name`,
  `source`, `archive`), not a paper. A module's `local_path` there is the path you
  gave, not the temporary folder.

## Your own policy: a preset

A repository or a research office has its own rules (which formats are accepted,
how deep folders may be, which files must be there). A pack can supply them as a
preset that selects the checks and sets their options, with no code at all:

```json
{
  "schema": 1,
  "name": "myoffice",
  "version": "1.0.0",
  "presets": {
    "archive": {
      "description": "Our archiving policy for data packages",
      "extends": "datapackage::default",
      "args": {"data_check": {"concepts": "rules"}}
    }
  }
}
```

```bash
metacheck package my_data_folder --preset myoffice::archive
```

A preset can extend `datapackage::default`, leave modules out (`exclude`), add its
own (`modules`) and set arguments for them (`args`, with the option names shown by
`metacheck modules datapackage::package_files` and so on); this one runs the default
checks and keeps `data_check` from downloading its concept model. Modules in a pack can be written against the
package like the built-in checks: they take `local_path`, call
`metacheck.datapackage.package_for(local_path)` for the opened package and its file
listing (`.files()`, `.dirs()`), and return a `checklist` with `checklist_frame()`.
See [MODULES.md](MODULES.md#part-2-writing-modules-and-packs) for writing and sharing a pack.
