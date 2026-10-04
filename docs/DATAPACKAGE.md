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
metacheck package my_data_folder -m package_readme --files-dir drafts   # draft a README into drafts/
```

(`pytacheck package ...` is the same command.) Without `-o` the results are printed,
one line per check, followed by the checklist (see below). With `-o FILE`, or with
`-f html|qmd|md` alone (the file is then `<name>_report.<format>` in the current
folder), the report is written and the results are printed too. A report written
inside the package's own folder is not counted among its files.

The exit status is 0 when every check ran, 1 when a check failed to run (the others
still run, and the failure is shown next to it), and 2 when the path cannot be
opened: it does not exist, it is a file that is not a supported archive, or an
archive is unreadable or too big (more than 20 GB extracted, or 200,000 files). It is
also 2 when `--files-dir` or `--force` is refused (see
[Files a check hands back](#files-a-check-hands-back)).

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
below that give a verdict (`package_files`, `package_structure`, `package_docs` and
`package_pii`; `package_readme` makes a file, so it is not in the preset and is
selected by name) and then two of metacheck's modules that read the data files themselves:
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

chain = check_package("my_data_folder", modules=["package_readme"])
chain.files                                           # {"README.md": "# ...", ...}: files that checks hand back
```

`check_package(path, *, preset=None, modules=None, args=None, record=None)` returns
the outputs in run order (a `ModuleChain`). `report_package(path, output_file=None,
output_format="html", *, preset=None, modules=None, args=None)` returns the report's
module outputs, with the file's path in `save_path`. Both results have `.files`, the
files that checks handed back (see [Files a check hands back](#files-a-check-hands-back)). Both raise
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

### `package_readme`

Drafts a README for the package, filled in by rules, and hands it back as a file.
It is not in `datapackage::default`, because it makes a file and not a verdict; select
it by name:

```bash
metacheck package my_data_folder -m package_readme --files-dir drafts   # writes drafts/README.md
```

There is no LLM and the package is never written to: the draft is a separate output
(see [Files a check hands back](#files-a-check-hands-back)). What goes into it:

| Part of the draft | Where it comes from |
|---|---|
| The author's own text | If the package has a README, the draft **is** that text, character for character (a byte-order mark and the kind of line end aside). A section it lacks is added at the end, in the template's order and in the heading style the README already uses (a plain-text README with `1. NUMBERED` or `CAPITAL` headings gets headings like that, because Markdown headings would make the README check stop seeing the author's own). A heading with nothing under it gets its text inserted. Nothing the author wrote is removed or changed. A README with every section comes back unchanged. |
| File list | A folder tree with the number of files and the size of the package (long folders are summarised), then one line for each top-level folder and file, so that `package_docs` finds the README naming them. |
| File formats | Extension counts (`.csv` (3 files), `.R` (1 file)); the more common spelling when `.R` and `.r` both occur. |
| Naming convention | One or two sentences, only when it is evident: at least five names with several words, at least 80% of them with the same word separator, and names in lower case; dates in names in the form `YYYY-MM-DD`. Otherwise nothing is claimed. |
| Variables | For each tabular data file (csv, tsv, Excel, ODS, SPSS, Stata, SAS): a table of **names, types and number of empty cells**. Files with the same variables share one table. A file with a header and no rows says so. Types are `integer`, `decimal`, `date`, `date-time`, `time`, `logical`, `text`, or `empty` / `not known` when there is nothing to tell. |
| Licence | A `LICENSE` file, named with the licence recognised from its text (the same table `package_docs` uses), or the licence the README names with a request to add the file; with neither, a placeholder. An unrecognised licence file is named, never guessed. |
| Everything else | A **placeholder** such as `<Describe how the data were collected and processed: ...>`. |

**No cell value is ever in the draft**, the report or any other output of the module:
a data file is read for its header, the type of each column and the count of its empty
cells, and the objects that carry that have no place for a value. A column name that is
itself a value (a file without a header row has its first data row there) is shown as
`column 3`, as in `package_pii`; the same goes for sheet names.

**The placeholders are the README check's own syntax.** They are `<help text>` (what
`package_docs` reports as `template-text`; also the form of `[FILENAME]` and `TODO`
when the template's patterns call for it). So running `package_docs` on the package with
the draft saved as its README lists every gap that is left, and
`metacheck package my_data_folder -m package_docs` is how a person works down them.
The wording of a placeholder is the template's `prompt`; a prompt that starts with an
HTML tag name (`a`, `p`, `i`) would hide it from the check, so the draft falls back to
`TODO: ...` and `[...]` in that case. A file name that looks like template text
(`TODO notes.txt`, `[FILENAME].csv`) is written in code spans and never counts as a gap.

**The sections and headings come from the README template data**: the `readme` option,
a dict or the path to a JSON file (default `data/docs_readme_generic.json`), the same
one `package_docs` takes, so a pack can supply its own template and get its own
sections, headings and prompts. Each section may have a `prompt` (what its placeholder
asks for) and a `fill` (`files`, `variables` or `licence`; the section's id by default,
so a section called `files` is filled from the file list). The top-level `prompts` map
has the wording for the title, a folder, a file and the request for a licence file. The
keys are documented in `metacheck.datapackage.docs`.

The module's `table` has one row for each section of the template: whether it was in the
README (`yes`, `empty`, `no`) and what the draft did (`kept`, `filled from the package`,
`added from the package`, `added as a placeholder`, `left as written`, `not needed`), and
how many placeholders are in it. `variables` has one row for each variable listed, `placeholders`
is the number of gaps left, `notes` are things worth knowing (a README that could not be
read, data files that were not read), and `files` is `{"README.md": text}`.

Options: `readme`, `max_rows` (100,000: rows read from each file to count its empty
cells), `max_file_size` (50 MB) and `max_files` (30); see
`metacheck modules datapackage::package_readme`.

**Limits.** Naming conventions are detected conservatively, so a package with few files,
or a mixed convention, gets no note. A column name that looks like a value is hidden
by the rules `package_pii` uses (a postcode, a phone number, an IP address, a long
number, a full name such as `Jan Jansen`); an ordinary capitalised header (`Total Score`)
is shown. A single first name in the first row of a file without a header row cannot be
told from a variable name, so it is shown. Variables are read only from the formats
above, and only the first `max_rows` rows of a file count. The
draft is always Markdown and always called `README.md`; the text of a README in another
format (docx, pdf) is carried over as the plain text that could be extracted from it, and
a note says so. Which sections of the author's README are found is decided by the same
code as the README check, so it is as good as that check.

## Files a check hands back

A check can return a **file** besides its findings (the README draft is one). In a
module's result, `files` maps a plain file name to its content, text or bytes
(see [MODULES.md](MODULES.md#write-a-module)). A name has no folder in it, so a module
can only name a file in the folder the caller chooses. Nothing writes a file unless the
caller asks:

* In Python, `check_package(...).files` (and `report_package(...).files`) is
  `{file name: text or bytes}` for all the checks that ran, and
  `metacheck.module.write_module_files(files, directory)` writes them. The attribute is
  new; nothing that read the result before changes.
* On the command line, `metacheck package PATH --files-dir DIR` writes every returned file
  into `DIR`, which is created, and prints each path. `-o` is still the report, and the
  two can be used together; with `--json` the results are the only thing on standard
  output.
* `DIR` must not be the package folder or lie inside it (a package is never written to;
  this is checked before anything runs, also through a symbolic link, and the exit status
  is 2). If a file with the same name is already in `DIR`, nothing is written and the
  exit status is 2, unless `--force`, which replaces the file (a folder of that name is
  never replaced). `--force` without `--files-dir` is an error.
* When no check that ran hands back a file, a note says so and nothing is created. When
  two checks hand back the same name, the later one's file is stored as
  `<module>_<name>`.

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
