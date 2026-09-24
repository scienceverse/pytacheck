# pytacheck

**Check research outputs for best practices — in Python.**

pytacheck is a fast, Python-native port of ScienceVerse's
[metacheck](https://github.com/scienceverse/metacheck) R package, designed to work
hand in hand with [bibr](https://bibr.org). It follows metacheck's `dev` branch, and
every function and module is **verified against the original R implementation** by a
parity test suite that runs the real R package.

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

## How it relates to metacheck

* **Same results.** Module tables, summary tables, traffic lights and report texts
  match metacheck's. The parity harness ([docs/PARITY.md](docs/PARITY.md)) runs
  metacheck in R on the same inputs and compares every value; the committed goldens
  are regenerated from R in CI, so they cannot drift.
* **Same paper model.** Papers use bibr's JSON schema, as in metacheck, so bibr output
  is read directly (and in memory, without a JSON round trip, when bibr is installed).
* **Auto-updated.** A scheduled workflow watches metacheck's `dev` branch; when it
  changes, the goldens are regenerated in R and an AI agent ports the change, which
  is only merged once parity is green again ([docs/PORTING.md](docs/PORTING.md)).
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
