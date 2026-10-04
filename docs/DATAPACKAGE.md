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
metacheck package my_data_folder -m package_files -m package_docs -m package_pii
metacheck package my_data_folder --preset myoffice::archive
metacheck package my_data_folder -a data_check.concepts=rules   # an argument for one module
metacheck package my_data_folder -a concepts=rules              # ... for every module that takes it
```

With no `-m` and no `--preset`, the preset `datapackage::default` runs, whatever
preset you have configured for papers. It runs, in this order, the four checks
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
extra arguments for it, as in `report()`.

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

A package usually comes without its paper, so that last question is often open. You
can let the check ask the paper's abstract, online and only when you switch it on:
see [Ethics from the paper's abstract](#ethics-from-the-papers-abstract-opt-in).

Options: see `metacheck modules datapackage::package_docs`.

### `package_pii`

Personal data **by value** in the data files. It reads the first rows of every csv,
tsv, Excel, ODS, SPSS, Stata and SAS file and looks at the values of each column.
It is separate from `metacheck::data_check`, which is a port of R and stays as it is:
that check flags e-mail addresses, IPv4 addresses, US social security numbers and
payment cards by value, and column names such as `naam` or `postcode`. This one adds
what is typical for Dutch research data. One checklist item for each kind
(`pii_bsn`, `pii_student_number`, `pii_phone`, `pii_ipv6`, `pii_postcode`,
`pii_name`), and `pii_scan` for whether the files could be read.

| What | How it is recognised |
|---|---|
| Dutch citizen service number (BSN) | 9 digits that pass the 11-check ("elfproef"), also written `123.456.782`; not `000000000`. About one in eleven random 9-digit numbers passes, so a column is flagged only when at least half of its filled cells (and three cells) pass; an ordinary column of 9-digit IDs is not. In a column named `bsn`, `burgerservicenummer` or `sofi...` one cell and 30% are enough, and 8 digits count too (a spreadsheet drops the leading zero). |
| Student number | 7 digits (`s1234567` and `s123456` too), **only in a column whose name says it**: `student`, `studentnummer`, `student_id`, `studentnr`, `s-number`, `snummer`. A bare 7-digit number is never flagged, because every 7-digit ID would be. At least half of the filled cells must match. |
| Phone number | `+` or `00` and a country code with 9 to 15 digits (`+31 6 12345678`, `+44 20 7946 0958`, `0031612345678`), or a Dutch national number: `0` and 10 digits with an area code of 2 to 4 digits and separators (`040-2474747`, `(020) 123 4567`, `06-12345678`), or `06` and 8 digits in one run (`0612345678`). In a column named `telefoon`, `phone`, `mobiel`, `gsm` any `0` and 9 digits count. Timestamps, EANs, dates and other numeric IDs do not (they do not start with `+` or `0`, or have the wrong number of digits); service numbers (`0800`, `0900`) do not. |
| IPv6 address | Parsed with Python's `ipaddress`, so compressed forms (`2001:db8::1`, `fe80::1%eth0`, `[2001:db8::1]`) are found and a time (`12:30:45`), a MAC address (`aa:bb:cc:dd:ee:ff`) and a ratio are not. `::1` and `::` are not personal. |
| Dutch postcode | 4 digits (not starting with 0), a space and 2 capitals: `1234 AB`; not `SA`, `SD`, `SS`, and not a number with a unit or an era (`1000 MB`, `500 BC`). Without a space or in lower case (`1234AB`, `1234 ab`) only in a column named `postcode`, `postcode_home`, `zipcode`. |
| Name column | The column name says it holds names (`naam`, `voornaam`, `achternaam`, `tussenvoegsel`, `first_name`, `surname`, `participant_name`, ...) **and** at least 70% of at least three filled cells read like names: one to five words, capitalised, letters, hyphens, apostrophes and initials with a full stop, a lower-case particle (`van`, `de`, `der`) between them. A column called just `name` or `naam` needs full names (two words or more), so a `name` column of variables (`age`, `reaction_time`), files (`data_01.csv`), single words or labels (`Control group`) is not flagged; a name that says something else (`file_name`, `variable_name`, `condition`, `country`) is not looked at at all. |

How a column is judged: a **cell** counts only when it is the number or name on its
own (`06-12345678`), not when it sits in a sentence (`call me on 06-12345678`) or in a
longer token. A column is flagged when enough of its filled cells count; a column whose
name points the same way needs fewer. Empty cells and `NA` are not counted.

What is reported is the file, the sheet, the column's name and the **number of cells**
that matched, never a value, in the findings (`table`), in the extra `hits` table (one
row for each flagged column: `path`, `sheet`, `column`, `column_index`, `rule`,
`hits`, `checked`, `named`, `rows_read`, `rows_capped`) and in the report. A column
or sheet name that is itself a value (a file without a header row has its first data
row there: an `@`, six digits in a row, more than 40 characters, or something that passes
one of the tests above, such as `5611 ZK` or `Jan de Vries`) is shown as `column 3` or
`sheet 2`.

Limits and options: at most `max_rows` rows of each file (5000) and the first 10 sheets
of a workbook; files over `max_file_size` MB (100) and files beyond the first `max_files`
(500) are not read, and say so on `pii_scan`. Hidden files and codebooks (a `name` column
there holds variable names) are left out. Text files are read as text, so a leading
zero is kept; spreadsheet cells and SPSS numbers are read as the numbers they are, which
means a Dutch phone number stored as a number (`612345678`) has lost its `0` and is not
found. `severity` maps a rule id (`bsn`, `student-number`, `phone`, `ipv6`, `postcode`,
`person-name`, `file-too-large`, `file-unreadable`, `files-not-scanned`) to `problem`,
`suggestion` (the default), `info` or `ignore` (that detector is not run).

**What it does not do.** It does not look inside free text (a comment column with a
phone number in a sentence), in other file types (JSON, XML, PDF, images, R data), in
sheets after the tenth or rows after the first `max_rows`. It does not find e-mail
addresses, IPv4 addresses or payment cards (`data_check` does), other countries'
national numbers (apart from phone numbers with a country code), dates of birth,
coordinates or indirect identifiers (a combination of age, gender and a small place).
A name column that holds single first names under a plain `name` header is missed on
purpose.

**The findings are hints for a person.** A flagged item has the status `manual`, not
`fail`: a 9-digit ID that happens to pass the 11-check, or a column of fictional
names, is a false hit, and a person has to look. Set a rule to `problem` in `severity`
to make that item fail.

#### Reading the findings from a pack's own checklist

A pack that has its own personal-data requirement (a data management check of an
institution, say) can take the result from the output of `package_pii`: the
`checklist` has the six `pii_*` items (status `manual`, `fail`, `pass` or `na`) and
`pii_scan`, and `hits` says in which file and column. In a pack's module, call
`metacheck.datapackage.pii.check_personal_data(pkg, severity=...)` with the opened
package (`package_for(local_path)`) and read `result.checklist`, `result.hits` and
`result.flagged` (columns flagged for each rule id). The pack's item can then be `fail`
when `result.hits` is not empty and its own rules say personal data may not be in the
package, and use `hits` for its detail line: the file, the column and the count, which
is all there is; the values are never kept. The pack's preset can add `package_pii` to
its modules, or leave it out of `datapackage::default` with `exclude`.

## Ethics from the paper's abstract (opt-in)

The ethical approval and the informed consent form are needed only when the research
involved people, and a package seldom says so. `package_docs` can ask the paper's
**abstract**, and it does so only when you ask it to. Off by default: with no switch
no request is ever made.

**What it does.** With a DOI, the check fetches the paper's abstract (from Crossref,
and from OpenAlex when Crossref has none) and runs on it the same live-data detection
that `ethics_check` uses (sentences about recruiting participants, informed consent,
online recruitment platforms, and the like). When that finds something, the check
decides "the research involved people": the ethical approval and consent rows become
required, and are `fail` when the package does not have them. The row says where the
decision came from, and quotes the one sentence that matched:

```text
Decided from the abstract of 10.1234/abc (Crossref): “Participants were recruited
from a panel (N = 120) and completed a survey.”
```

**What it never does.** It never decides that the research did *not* involve people.
An abstract that does not mention participants proves nothing (a short abstract
leaves out a lot), so those rows stay with a person (`manual`), as they do without the
lookup. The detection is the one `ethics_check` has: it also flags abstracts that
describe animal studies, and it does not read anything but the abstract. The answer
comes from the abstract only when `human_participants` is not given and the paper's own
text (when there is one) did not decide it. The abstract itself is not kept: the result
holds the decision, the name of the service, the DOI and that one sentence.

**What leaves the machine.** Only the DOI: one `GET` for one record, with the DOI in
the URL (`https://api.crossref.org/works/<doi>`, then
`https://api.openalex.org/works/https://doi.org/<doi>`). No file name, README text,
column name or any other content of the package is sent, and the request has no body.
Like every request of this tool it carries the User-Agent, which names the tool and
its version, and the contact e-mail address if you have set one with
`metacheck.email()` (`METACHECK_EMAIL`); the two services are told who is asking so
that they can rate-limit politely. The DOI is checked before it is used (it has to
look like `10.xxxx/...`, with no space, `?`, `#` or `%`), a `https://doi.org/` or `doi:`
prefix is removed, and it is percent-encoded into the path.

**How to switch it on.**

```bash
metacheck package my_data_folder --abstract-lookup                  # the DOI is on the README's line for the publication
metacheck package my_data_folder --paper-doi 10.1234/abc            # a DOI of your own (implies --abstract-lookup)
metacheck package my_data_folder -a package_docs.abstract_lookup=true -a package_docs.paper_doi=10.1234/abc
```

```python
from metacheck.datapackage import check_package

check_package(
    "my_data_folder",
    modules=["package_docs"],
    args={"package_docs": {"abstract_lookup": True, "paper_doi": "10.1234/abc"}},
)
```

There is no environment variable or setting that turns it on for every run; you ask
each time. With no `paper_doi`, the DOI comes from the README, from a line that is
about the publication, such as `DOI of the publication: 10.1234/abc`, `Related paper:
https://doi.org/10.1234/abc` or the `Project or Paper Title : ...` line of a README
template (or the field of your own template that has `"expect": "doi"`). A DOI that
stands in a sentence, in a list of references, or on a line about the data (`Dataset
DOI`) is never taken, because it may be another work's or the package's own. A DOI you
give with `paper_doi` always wins over the README's.

**When it cannot decide.** Nothing here fails the run or waits long. `--offline`
(`offline=True`), no network, a timeout (10 seconds a try, two tries), a service that
answers with an error or with something that is not a record, a record without an
abstract, a bad DOI and a README with no DOI all give the result the package would
have had without the lookup, and one line says why:

```text
Offline: the abstract of 10.1234/abc was not looked up.
No abstract was found for 10.1234/abc (Crossref has no record; OpenAlex has no abstract).
The abstract of 10.1234/abc could not be fetched: Crossref and OpenAlex did not answer.
The abstract of 10.1234/abc (Crossref) does not mention participants, which proves nothing either way.
```

The same line is in the module's `human_participants_note`, next to
`human_participants_source` (`given`, `paper`, `abstract` or `unknown`), in the
`abstract_decision` result and in the detail of the two rows.

**Why `package_docs` does not declare `requires=["network"]`.** `--offline` leaves out
every module that declares it needs the network. `package_docs` works fully offline
and does the same without the switch, so it must stay in an offline run; it reads the
offline setting itself (`metacheck.module.use_setting("offline")`) and does not ask
when it is set. The lookup is in `metacheck.datapackage.abstract`, which also keeps
the network code out of the pack module (`pack check` flags `metacheck.http` imports in
a pack's module files).

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
* The one thing that goes online is a request you make: `--abstract-lookup` (or
  `--paper-doi`) fetches the paper's abstract by its DOI, to decide whether the research
  involved people. Nothing but the DOI is sent; see
  [Ethics from the paper's abstract](#ethics-from-the-papers-abstract-opt-in). Without
  the switch there is no request.
* `--offline` leaves out the modules that declare they need the network or an LLM.
  Note that `metacheck::data_check` declares both, so with `--offline` it is
  left out of the list (`codebook_check` still reads the data files itself).
  `package_docs` is not left out: it does not declare the network, and with `--offline`
  its abstract lookup is skipped, with a note.
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
