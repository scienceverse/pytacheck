# Using pytacheck with bibr

[bibr](https://bibr.org) extracts papers (PDF, DOCX, JATS XML, HTML, ePub) into the
JSON schema pytacheck works on. There are three ways to combine them.

## 1. In-process (recommended)

```bash
pip install "metacheck[bibr]"
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

bibr 0.5.1, the release on PyPI, writes export schema 11.0, which pytacheck does not
read (see section 2). Until a bibr release writes schema 12.x, `pc.read("paper.pdf")`
stops with an error that names the schema. bibr's main branch writes 12.1.

bibr's result is read straight into a `Paper` (`pc.from_bibr(result)`), with no
JSON written to disk: a native bibr export schema 12.x paper, the same object
`pc.read()` returns for bibr's JSON export. To skip extraction next time, save the
paper (see below for what `pc.paper_write()` does with each schema version) or write
the result's `data` to a JSON file yourself, which holds every key bibr wrote.

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
`schema_version` (bibr 11.x, 13.x) is refused with metacheck's error, to which
pytacheck adds what to do: extract the paper again with a bibr version that writes
12.x, or update pytacheck for a newer schema.

Within 12.x the schema only grows: a later minor version may add keys and enum values
(a new section type, say). pytacheck ignores a key it does not know, keeps an enum
value it does not know as it is, and logs each new value once (`pytacheck.lastlog()`,
label `bibr12_new_value`).

`pc.paper_write(paper)` saves a paper read from a 12.0 export (or converted from
Grobid) as a bibr 12.0 file, byte for byte as metacheck's
`paper_write(schema_version = "12.0")` writes it, keeping bibr as the producer and
naming pytacheck as the converter (`schema_version="auto"`, the default; see
[UPSTREAM_ISSUES.md](UPSTREAM_ISSUES.md) D1 and D4). It refuses a paper read from
a later 12.x such as 12.1, because a 12.0 file cannot hold the keys a later 12.x adds.
For such a paper:

* keep bibr's own export (`bibr chew papers/ -o json/`) and read that next time; or
* pass `schema_version=None` to save the paper object, as metacheck's default does.
  `pc.read()` reads it back, but without its funding, affiliation, footnote and
  extraction blocks and without the abstract in `info`, so the bibr version that
  extracted it is lost.

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
docker compose up              # pytacheck API on :8000, bibr on 127.0.0.1:8001
```

Every compose command needs `PYTACHECK_API_KEY` (a `.env` file next to
`docker-compose.yml` works; see API.md). bibr has no key, so its port is published on
the loopback address only.

The `pytacheck:<version>-bibr` image bundles bibr so the API accepts PDF uploads
directly.
