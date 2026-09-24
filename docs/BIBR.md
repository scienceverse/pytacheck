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

bibr's result is converted straight into a `Paper` (`pc.from_bibr(result)`), with no
JSON written to disk. Save it with `pc.paper_write(paper)` to skip extraction next
time.

## 2. From bibr JSON

```bash
bibr chew papers/ -o json/
pytacheck run json/ -m marginal
```

Any bibr version works: files in schema v11/v12 (bibr ≥ 0.4) are converted to the
layout metacheck's checks expect (see `pytacheck.io.bibr_schema` and
[UPSTREAM_ISSUES.md](UPSTREAM_ISSUES.md) D1).

## 3. A bibr server

For a shared, warm extraction service use bibr's server (or the Scienceverse
platform) with `convert_bibr()`, or run both services with Docker:

```bash
docker compose up        # pytacheck API on :8000, bibr on :8001
```

The `pytacheck:<version>-bibr` image bundles bibr so the API accepts PDF uploads
directly.
