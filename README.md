# metacheck (Python)

**Check research outputs for best practices — in Python.**

**Try it:** a preview app that runs on your own computer, installed with one command. See [docs/TRY.md](https://github.com/scienceverse/pytacheck/blob/main/docs/TRY.md).

metacheck (Python) is a fast, Python-native port of ScienceVerse's
[metacheck](https://github.com/scienceverse/metacheck) R package, designed to work
hand in hand with [bibr](https://bibr.org). It follows metacheck's `dev` branch (for now
`dev` plus pull request [#423](https://github.com/scienceverse/metacheck/pull/423), which
adds bibr export schema 12.0), and every function and module is **checked against the
original R implementation** by a parity test suite that runs the real R package. The
goal is results at least as accurate as metacheck's, with metacheck's bugs fixed
rather than copied, and nothing invented ([the accuracy contract](https://github.com/scienceverse/pytacheck/blob/main/docs/PORTING.md#1-the-accuracy-contract)).

> **Status: alpha.** The port is in progress; see [the porting status](https://github.com/scienceverse/pytacheck/blob/main/docs/STATUS.md).
> A difference from metacheck that is not documented as a fix or a deliberate change
> in [docs/UPSTREAM_ISSUES.md](https://github.com/scienceverse/pytacheck/blob/main/docs/UPSTREAM_ISSUES.md) is a bug: please
> [open an issue](https://github.com/scienceverse/pytacheck/issues).

## Install

```bash
pip install "metacheck>=0.4.0a1"           # core: bibr JSON / Grobid XML input
pip install "metacheck[bibr]>=0.4.0a1"     # + extract PDF/DOCX/HTML with bibr, in-process
pip install "metacheck[concepts]>=0.4.0a1" # + data_check's offline column-concept classifier
pip install "metacheck[all]>=0.4.0a1"      # + bibr, data-file readers, REST API, charset detection, concepts
```

The package is on PyPI as `metacheck` and is a pre-release for now, so the
requirement names the pre-release (`>=0.4.0a1`); this lets pip and uv pick it
for metacheck only, not for its dependencies. If you installed `pytacheck` from GitHub
before, run `pip uninstall pytacheck` first: the old and the new package share files.
The Python module is still called `pytacheck` (`import pytacheck`) and so is one of the
commands; `metacheck` is the same command.

To try the app in your browser, with [uv](https://docs.astral.sh/uv/):

```bash
uv tool install "metacheck[app]>=0.4.0a1"
metacheck-app
```

If you do not use Python, one line installs the app; see [docs/TRY.md](https://github.com/scienceverse/pytacheck/blob/main/docs/TRY.md).
On a Mac or Linux:

```bash
curl -LsSf https://raw.githubusercontent.com/scienceverse/pytacheck/main/install.sh | sh
```

With `metacheck[concepts]`, `data_check` gives concepts (reaction time, age, Likert
item, condition, ...) to the columns its rules leave blank with a local multilingual
classifier (downloaded once, ~840 MB; ~2 GB of memory) rather than an LLM. `concepts="cascade"` sends
the columns it is unsure of to the LLM, and `concepts="llm"` is metacheck's behaviour
(see D33 in [docs/UPSTREAM_ISSUES.md](docs/UPSTREAM_ISSUES.md)).

Or with Docker: `docker run --rm -v "$PWD:/work" ghcr.io/scienceverse/pytacheck run paper.json -m all_p_values`.

## Use

```python
import pytacheck as pc

paper = pc.read("paper.json")          # bibr JSON, Grobid XML — or a PDF with metacheck[bibr]
pc.paper_write(paper, "checked")        # a bibr 12.0 paper is saved as a bibr 12.0 file
pc.module_list()                        # available checks
out = pc.module_run(paper, "marginal")
out.traffic_light, out.summary_text
out.table                               # pandas DataFrame

papers = pc.read("corpus/")             # a PaperList; modules run on whole corpora
pc.module_run(papers, "marginal").summary_table
```

Command line:

```bash
pytacheck modules
pytacheck run paper.pdf -m marginal -m all_p_values
pytacheck report paper.json -o report.html
pytacheck package my_data_folder                 # check a data package (a folder or zip), no paper
```

## The paper schema: bibr export schema 12.0

**bibr export schema 12.0 is pytacheck's paper schema.** metacheck reads it natively
once pull request #423 is merged, and pytacheck already does:

* `pc.read()` reads a bibr 12.x export (a JSON file with a root `schema_version`).
  Files without one (bibr v10.x and older, metacheck's demo and fixture papers) are
  read as metacheck reads them. Other versions, bibr 11.x included, are refused
  with metacheck's error.
* Grobid TEI is converted to 12.x: `pc.read("paper.tei.xml")`,
  `pc.grobid_to_bibr(...)`, `convert()` and the CLI all do this. Pass
  `schema_version=None` for metacheck's older conversion.
* `pc.paper_write(paper)` saves a paper read from a 12.0 export as a bibr 12.0 file. The
  bytes are the ones metacheck writes, except that pytacheck is named as the converter.
  An older paper is saved as before, a paper read from a later 12.x (12.1) is refused,
  and `schema_version=None` always saves the paper object.

A 12.x paper has these tables and fields:

| table | in a 12.x paper |
|---|---|
| `info` | the export's `metadata` and `source`: title, abstract, DOI, journal, licence, statements, `file_name`, `sha256`, `input_format`, `schema_version` |
| `text` | body sentences, then the reference list, then one row for each caption and footnote. Caption and footnote rows have no `section_id` |
| `section` | only the paper's real sections. There are no figure, table or footnote pseudo-sections |
| `figure`, `table`, `footnote` | point at their caption or footnote row with `text_id`, and carry `label`, `caption` and `page_number` (tables also have `html` and `contents`) |
| `xref` | `xref_id` is the row's own key and `target_id` is the row it cites. A citation of reference 3 has `xref_type` `"bib"` and `target_id` 3 |
| `bib_match`, `info_match`, `affiliation_match`, `funding_match` | match scores are on a 0-1 scale (older papers store Crossref relevance scores such as 61.8). `info_match` is 12.x's `metadata_match` |
| `author`, `affiliation`, `funding`, `url`, `bib`, `eq` | 12.x columns. `eq` keeps degrees of freedom in parentheses (`"(28)"`), as metacheck's statistics code expects |
| `paper.extraction` | the export's extraction block: `producer` (bibr or Grobid, with its version), `converter`, `completed_at`, diagnostics and warnings |

The built-in modules handle both 12.x and older papers, and so does a mixed paper list.
[docs/MODULES.md](https://github.com/scienceverse/pytacheck/blob/main/docs/MODULES.md#papers-bibr-12x-and-the-older-format) shows how to
write modules that do the same. [docs/BIBR.md](https://github.com/scienceverse/pytacheck/blob/main/docs/BIBR.md) covers reading bibr's
output.

## Presets, packs and community modules

Choose the checks that matter for your field with **presets**, and add community
checks from **packs** (folders of modules, shared through the
[pytacheck-modules](https://github.com/scienceverse/pytacheck-modules) store):

```bash
pytacheck init                                   # pick field presets; installs their packs
pytacheck run paper.json --preset fields::psychology --record run.json
pytacheck pack search trial                      # browse the store
pytacheck pack install clinical_trials           # shows what it installs and asks first
pytacheck pack new my-checks                     # write your own
```

Installed packs are pinned to a commit and a file hash, every result records
which code produced it, and `pytacheck rerun run.json paper.json` replays a run.
See [docs/MODULES.md](https://github.com/scienceverse/pytacheck/blob/main/docs/MODULES.md) for the user and author guides and
[docs/API.md](https://github.com/scienceverse/pytacheck/blob/main/docs/API.md) for the REST API and its API key.

## Checking a data package

Every installation can also check the folder (or zip) of data, code and documentation
that goes with a paper, before it is archived: junk files, file names and formats,
folder structure, README and codebook. `pytacheck package my_data_folder` runs on your
own machine and needs no paper; `-o package.html` writes a report. See
[docs/DATAPACKAGE.md](https://github.com/scienceverse/pytacheck/blob/main/docs/DATAPACKAGE.md).

## How it relates to metacheck

* **Results as accurate as metacheck's, or better.** Modules check and report what
  metacheck's do: traffic lights, summary tables, report texts and numbers agree with
  metacheck on real papers, repositories and data. Where metacheck is clearly wrong
  (a crash on valid input, a wrong count, a false positive), pytacheck does the right
  thing and records the bug in [docs/UPSTREAM_ISSUES.md](https://github.com/scienceverse/pytacheck/blob/main/docs/UPSTREAM_ISSUES.md) so it
  can be reported upstream. The parity harness ([docs/PARITY.md](https://github.com/scienceverse/pytacheck/blob/main/docs/PARITY.md)) runs
  metacheck in R on the same inputs and compares every value, every difference is
  marked with its reason, and the committed goldens are regenerated from R in CI, so
  they cannot drift.
* **Same paper model.** Papers use bibr export schema 12.0, which metacheck reads
  natively from pull request #423 on, so bibr output is read directly (and in process,
  when bibr is installed). Older papers are read as metacheck reads them.
* **Auto-updated.** A scheduled workflow watches metacheck's `dev` branch (and, until
  it is merged, pull request #423). When the tracked head moves, the goldens are
  regenerated in R and an AI agent ports the change. The change is only merged once
  parity is green again ([docs/PORTING.md](https://github.com/scienceverse/pytacheck/blob/main/docs/PORTING.md)).
* **Faster.** pytacheck prefers mature compiled libraries (pandas, orjson, lxml, the
  `regex` engine) to re-implementing R's internals, builds paper tables lazily, and
  assembles corpus-wide tables without per-paper overhead.
* **Where things are.** [docs/CODEMAP.md](https://github.com/scienceverse/pytacheck/blob/main/docs/CODEMAP.md)
  maps each metacheck check, R function and R package to its place in the Python code.

Development continues in metacheck; pytacheck tracks it. Please report issues with
the checks themselves (validity, false positives) upstream.

## Development

```bash
uv sync --all-extras
uv run pytest                                 # includes parity vs committed R goldens
uv run python -m parity check -v              # parity report
uv run python -m parity generate --area text  # regenerate goldens (needs R + metacheck)
```

## License and credit

AGPL-3.0-or-later, like metacheck and bibr. pytacheck is a translation of metacheck by
Lisa DeBruine, Cristian Mesquida, Jakub Werner, Daniel Lakens and contributors; please
cite metacheck when you use it.
