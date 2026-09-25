# pytacheck

**Check research outputs for best practices — in Python.**

pytacheck is a fast, Python-native port of ScienceVerse's
[metacheck](https://github.com/scienceverse/metacheck) R package, designed to work
hand in hand with [bibr](https://bibr.org). It follows metacheck's `dev` branch (for now
`dev` plus pull request [#423](https://github.com/scienceverse/metacheck/pull/423), which
adds bibr export schema 12.0), and every function and module is **verified against the
original R implementation** by a parity test suite that runs the real R package.

> **Status: alpha.** The port is in progress; see [the porting status](docs/STATUS.md).
> Results should match metacheck exactly. Where they don't, that is a bug: please
> [open an issue](https://github.com/thesanogoeffect/pytacheck/issues).

## Install

```bash
pip install pytacheck                 # core: bibr JSON / Grobid XML input
pip install "pytacheck[bibr]"         # + extract PDF/DOCX/HTML with bibr, in-process
pip install "pytacheck[all]"          # + bibr, data-file readers, REST API
```

Or with Docker: `docker run --rm -v "$PWD:/work" ghcr.io/thesanogoeffect/pytacheck run paper.json -m all_p_values`.

## Use

```python
import pytacheck as pc

paper = pc.read("paper.json")          # bibr JSON, Grobid XML — or a PDF with pytacheck[bibr]
pc.paper_write(paper, "checked")        # a bibr 12.x paper is saved as a bibr 12.0 file
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
```

## The paper schema: bibr export schema 12.0

**bibr export schema 12.0 is pytacheck's paper schema.** metacheck reads it natively
once pull request #423 is merged, and pytacheck already does:

* `pc.read()` reads a bibr 12.x export (a JSON file with a root `schema_version`).
  Files without one (bibr v10.x and older, metacheck's demo and fixture papers) read
  exactly as metacheck reads them. Other versions, bibr 11.x included, are refused
  with metacheck's error.
* Grobid TEI is converted to 12.x: `pc.read("paper.tei.xml")`,
  `pc.grobid_to_bibr(...)`, `convert()` and the CLI all do this. Pass
  `schema_version=None` for metacheck's older conversion.
* `pc.paper_write(paper)` saves a 12.x paper as a bibr 12.0 file. The bytes are the ones
  metacheck writes, except that pytacheck is named as the converter. An older paper is
  saved as before, and `schema_version=None` always saves the paper object.

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
[docs/MODULES.md](docs/MODULES.md#papers-bibr-12x-and-the-older-format) shows how to
write modules that do the same. [docs/BIBR.md](docs/BIBR.md) covers reading bibr's
output.

## Presets, packs and community modules

Choose the checks that matter for your field with **presets**, and add community
checks from **packs** (folders of modules, shared through the
[pytacheck-modules](https://github.com/thesanogoeffect/pytacheck-modules) store):

```bash
pytacheck init                                   # pick field presets; installs their packs
pytacheck run paper.json --preset fields::psychology --record run.json
pytacheck pack search trial                      # browse the store
pytacheck pack install clinical_trials           # shows what it installs and asks first
pytacheck pack new my-checks                     # write your own
```

Installed packs are pinned to a commit and a file hash, every result records
which code produced it, and `pytacheck rerun run.json paper.json` replays a run.
See [docs/MODULES.md](docs/MODULES.md) for the user and author guides.

## How it relates to metacheck

* **Same results.** Module tables, summary tables, traffic lights and report texts
  match metacheck's. The parity harness ([docs/PARITY.md](docs/PARITY.md)) runs
  metacheck in R on the same inputs and compares every value; the committed goldens
  are regenerated from R in CI, so they cannot drift.
* **Same paper model.** Papers use bibr export schema 12.0, which metacheck reads
  natively from pull request #423 on, so bibr output is read directly (and in process,
  when bibr is installed). Older papers read exactly as metacheck reads them.
* **Auto-updated.** A scheduled workflow watches metacheck's `dev` branch (and, until
  it is merged, pull request #423). When the tracked head moves, the goldens are
  regenerated in R and an AI agent ports the change. The change is only merged once
  parity is green again ([docs/PORTING.md](docs/PORTING.md)).
* **Faster.** R-compatible regex semantics are reproduced with the `regex` engine,
  paper tables are built lazily, and corpus-wide tables are assembled without
  per-paper overhead.

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
