# Using pytacheck with bibr

[bibr](https://bibr.org) extracts papers (PDF, DOCX, JATS XML, HTML, ePub) into the
JSON schema pytacheck works on. There are three ways to combine them.

## 1. In-process (recommended)

```bash
pip install "pytacheck[bibr]"
bibr setup            # once: models, OCR/LLM configuration (see bibr's docs)
```

```python
import pytacheck as pc

paper = pc.read("paper.pdf")                  # bibr runs in-process
papers = pc.read(["a.pdf", "b.docx"])         # one bibr pipeline for the batch
paper = pc.chew("paper.pdf", refs="llm", pages="1-20")   # pass bibr options
pc.module_run(paper, "all_p_values")
```

or on the command line: `pytacheck run paper.pdf -m marginal -m all_p_values`.

bibr's result is read straight into a `Paper` (`pc.from_bibr(result)`), with no
JSON written to disk: a native bibr export schema 12.x paper, the same object
`pc.read()` returns for bibr's JSON export. Save it with `pc.paper_write(paper)` (a
bibr 12.0 file, see below) to skip extraction next time.

## 2. From bibr JSON

```bash
bibr chew papers/ -o json/
pytacheck run json/ -m marginal
```

bibr export schema 12.0 is the schema pytacheck targets, and it is read natively,
as metacheck reads it (`pytacheck.io.bibr12`, the port of metacheck's
`R/import-bibr12.R`): the export's `metadata` and `source` make the `info` table,
`metadata_match` is `info_match`, every other table keeps its 12.x name and meaning
(`xref_id` is the row's own key and `target_id` the row it cites; captions and
footnotes are text rows with no section), and the `extraction` block is kept as
`paper["extraction"]`. Files without a root `schema_version` (bibr v10.x and older,
metacheck's demo and fixture papers) read exactly as before; any other root
`schema_version` (bibr 11.x, 13.x) is refused with metacheck's error.

`pc.paper_write(paper)` saves a 12.x paper as a bibr 12.0 file, byte for byte as
metacheck's `paper_write(schema_version = "12.0")` writes it, keeping bibr as the
producer and naming pytacheck as the converter (`schema_version="auto"`, the
default; `None` saves the paper object as metacheck's default does; see
[UPSTREAM_ISSUES.md](UPSTREAM_ISSUES.md) D1 and D4).

Grobid TEI is converted to 12.x as well (`pc.read("paper.tei.xml")`,
`pc.grobid_to_bibr(...)`, `convert()`), with Grobid as the producer; pass
`schema_version=None` for metacheck's older conversion. The source is the PDF next to
the TEI (`paper.pdf` for `paper.pdf.tei.xml`), so its `sha256` matches a bibr export
of the same PDF. `convert_grobid(pdf, save_path=None)` reads a temporary TEI file, so
its paper's `paper_id`, `source.file_name` and `sha256` come from that file, not from
the PDF.

## 3. A bibr server

For a shared, warm extraction service use bibr's server (or the Scienceverse
platform) with `convert_bibr()`, or run both services with Docker:

```bash
export PYTACHECK_API_KEY=...   # 32 or more characters; see API.md
docker compose up              # pytacheck API on :8000, bibr on :8001
```

Compose refuses to start without `PYTACHECK_API_KEY`.

The `pytacheck:<version>-bibr` image bundles bibr so the API accepts PDF uploads
directly.
