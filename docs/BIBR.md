# Using pytacheck with bibr

[bibr](https://bibr.org) extracts papers (PDF, DOCX, JATS XML, HTML, ePub) into the
JSON schema pytacheck works on. There are three ways to combine them.

## 1. In-process (recommended)

```bash
pip install "metacheck[bibr]>=0.4.0a1"
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

For a shared, warm extraction service, `convert_bibr()` sends PDFs to a bibr server and
saves the JSON it returns, which `pc.read()` then reads as any bibr export. `backend=`
names the kind of server:

| backend | server | how it talks |
|---|---|---|
| `"bibr"` | `bibr serve`, and the hosted service in front of it (bibr-gate: the same paths, a personal bearer token) | `POST /papers/jobs`, poll `GET /papers/jobs/{id}`, fetch `/papers/jobs/{id}/result` |
| `"selfhosted"` | `bibr serve` | one `POST /papers/extract`, which holds the connection until the paper is done |
| `"scivrs"` | the Scienceverse platform | its own `/jobs` queue and API key (`SCIVRS_API_KEY`) |

### The hosted service, or your own bibr serve

```bash
export BIBR_URL=https://...        # address of the bibr API: the root that serves /papers/jobs
export BIBR_API_KEY=bibr_pat_...   # your personal token; for your own server its AUTH_API_KEY
```

```python
import pytacheck as pc

path = pc.convert_bibr("paper.pdf", "json/")     # BIBR_URL is set, so backend="bibr"
paper = pc.read(path)
pc.module_run(paper, "all_p_values")

pc.convert_bibr("paper.pdf", "json/", api_url="https://...", api_key="bibr_pat_...")  # or pass them
pc.convert("paper.pdf", "json/")                 # BIBR_URL also wins over convert()'s guessing
```

Without `BIBR_URL`, `backend="bibr"` talks to `http://localhost:8000` (a `bibr serve` on
this machine), and needs no token if that server has no `AUTH_API_KEY`. bibr's own example
clients name the address `BIBR_API_URL`; `backend="bibr"` reads that too, and `BIBR_URL`
wins when both are set. `BIBR_API_URL` never picks the backend by itself, though: only
`BIBR_URL` steers `convert()` and `backend="auto"`. With `backend="auto"`, `BIBR_URL` picks
`"bibr"` unless you pass `api_url`, and so does `BIBR_API_KEY` when it is the only key
(no `api_key`, no `SCIVRS_API_KEY`, which has meant the platform since before this
backend existed); otherwise a key that was passed or `SCIVRS_API_KEY` picks `"scivrs"`,
and the rest is `"selfhosted"`, as before. To use a bibr server with both keys set, set
`BIBR_URL` or pass `backend="bibr"`.

What the `"bibr"` backend does:

* **Options.** It sends the file, `include_figures` and the page range (`start_page` and
  `end_page`, 1-based here, 0-based on the wire). The server decides everything else;
  the hosted service fixes its extraction settings whatever a client asks for. The hosted
  service takes PDF files only.
* **Waiting.** The job is queued (`202`), then polled at 2 s, growing by half each time
  to 10 s (`poll_interval=2`), until it has `succeeded` or `failed`, for at most
  `timeout=600` seconds in all (`poll_interval` must be positive). The result is fetched
  when the job has succeeded; a `409` there means "not finished yet" and the polling goes
  on. A poll or a result fetch that gets no answer, or a 502, 503 or 504, is tried again
  up to five times in a row (the paper is already done on the server, and has used up a
  quota slot). The job's own address is built from its id and `BIBR_URL`: the `status_url`
  in the answer is not followed. A job that outlasts `timeout` keeps running on the
  server, and counts against your limit on active jobs there until it ends: a longer
  `timeout` for big documents is better than several short ones.
* **Busy servers.** A `429` (upload limit, job limit, quota of the hosted service) is
  retried after the `Retry-After` it names (1, 2, 4 ... s if it names none), at most
  `max_retries=5` times, and never waiting more than `max_retry_wait=120` s for one retry
  or past `timeout`. A daily quota that asks to wait an hour is an error at once, not a
  sleep, and so is a rate limit another call of pytacheck (the readiness probe of
  `convert()`, say) has already got from the same host and remembered. A `429` means
  nothing was done, so retrying cannot upload the file twice. Other failed submissions are
  not retried.
* **Errors** say what the status means and what the server said, and never contain the
  token. `401` is a missing, mistyped, expired or revoked token; `403` a refused request;
  `413` a file over the server's limit (50 MiB); `415` a file that is not a PDF (hosted
  service); `404` an unknown or expired job (bibr keeps jobs for an hour) or a wrong
  `BIBR_URL`; a failed job is reported with bibr's message and `error_code`
  (`llm_truncated`, `llm_timeout`, ...). All are `BibrRequestError` (a `RuntimeError` with
  `status_code`, `detail` and `retry_after`) except a failed job (`RuntimeError`), a job
  that outlasts `timeout` (`TimeoutError`) and a server that does not answer
  (`ConnectionError`).
* **The token stays put.** It is sent to `https://` addresses, and to `http://` only for
  `localhost` and loopback addresses (`127.0.0.1` and the rest of `127.0.0.0/8`, `::1`;
  not `*.localhost` names, which a resolver may send elsewhere); anything else raises
  `ValueError` before a byte is sent. Redirects are not followed (the error names where the server wanted to go), so a
  redirect cannot move the token to another host.
* **Several files** are converted one after the other, each failure logged and returned as
  `None`. A `401` or `403` stops the list: every later file would be refused too.

`"selfhosted"` sends the same bearer token (`api_key=` or `BIBR_API_KEY`) when there is
one, which a `bibr serve` with `AUTH_API_KEY` requires, waits out a `429` the same way, and
explains its errors the same way. Its synchronous request holds the connection for the
whole extraction, which proxies and load balancers often cut off; prefer `"bibr"` for
anything but a server on this machine.

The readiness probe that `convert()` uses to find a local server (`GET /ready`) accepts
`{"status": "ready"}`, all an anonymous caller gets from `bibr serve`; it only turns down a
server whose `checks.bibr` is present and not `ok`.

The client follows bibr serve as of main `7483212` (`docs/reference/rest-api.md` there).
Its tests use mocked servers modelled on that code, not a live server.

### Docker

To run pytacheck and bibr together:

```bash
export PYTACHECK_API_KEY=...   # 32 or more characters; see API.md
docker compose up              # pytacheck API on :8000, bibr on 127.0.0.1:8001
```

Every compose command needs `PYTACHECK_API_KEY` (a `.env` file next to
`docker-compose.yml` works; see API.md). bibr has no key, so its port is published on
the loopback address only.

Environment variables and folders: see [ENVIRONMENT.md](ENVIRONMENT.md).

The `pytacheck:<version>-bibr` image bundles bibr so the API accepts PDF uploads
directly.
