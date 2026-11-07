# The demo site and its web interface

`pytacheck site` serves a one-page website and a web interface under `/api`. You send a
paper or a data and code folder. You get a report back. It is the Python version of the
Metacheck demo site that runs on SURF Research Cloud, and the checks are pytacheck's own.

It needs `pip install "metacheck[api]>=0.4.0a1"`. Nothing else.

## Run it

```sh
pytacheck site                 # http://127.0.0.1:8080/
pytacheck site --port 9000
pytacheck site --public        # bind 0.0.0.0: anyone who can reach the port can run checks
pytacheck site --run-code      # run a repository's code in Docker (see below)
```

| Option | What it does |
|---|---|
| `--host` | The address to bind. Default `127.0.0.1`, or `0.0.0.0` with `--public`. |
| `--port` | The port. Default 8080. |
| `--public` | Allows an address that is not the loopback. Without it, `--host 0.0.0.0` is refused. |
| `--run-code` | Lets repository jobs run their code. Needs a running Docker. |
| `--jobs-dir` | Where uploads and results live while a job lives. Default: a private temporary folder that is removed when the server stops. |

`site` is its own command, not a flag of `serve`. `serve` is the paper API (`/paper/...`,
see [API.md](API.md)), and the two have different routes, limits and jobs. They can run
side by side on two ports.

The site has no sign-in. Keep it on `127.0.0.1`, or put a proxy that signs people in in
front of it. Behind a proxy, set `X-Forwarded-Proto` and `X-Forwarded-Prefix`, so that the
`status_url` and `result_url` of a job point at the address people use. Let the proxy
accept bodies of 600 MB, and give it a long timeout for the blocking routes.

A paper in PDF form is converted by a bibr server if the server has one
(`METACHECK_BIBR_URL`), else by a GROBID server (`METACHECK_GROBID_URL`). If bibr cannot
convert it, GROBID does. A GROBID server is another computer, unless it is a local one.
Bibr JSON and GROBID XML go to no other computer.

## The web interface

Every route is under `/api`. A job is sent as `multipart/form-data`. The answers are JSON,
except a result.

### Send a job

`POST /api/jobs` answers `202`:

```json
{
  "id": "6f1c0f4e-8c6c-4d0e-9d2b-6d5e1c2f9a10",
  "status": "queued",
  "step": "waiting in the queue",
  "position": 1,
  "created": "2026-11-07T10:00:00+00:00",
  "status_url": "http://127.0.0.1:8080/api/jobs/6f1c0f4e-8c6c-4d0e-9d2b-6d5e1c2f9a10",
  "result_url": "http://127.0.0.1:8080/api/jobs/6f1c0f4e-8c6c-4d0e-9d2b-6d5e1c2f9a10/result"
}
```

The fields:

| Field | Meaning |
|---|---|
| `file` | A PDF, bibr JSON, GROBID XML (up to 50 MB) or a `.zip` of a folder (up to 500 MB). The kind is told by the first bytes of the file, not by its name. |
| `url` | Instead of a file, for `repocheck`: the link of a public repository (GitHub, GitLab, OSF or Zenodo). |
| `psyarxiv` | Instead of a file, for `report` and `check`: a PsyArXiv link, DOI or id. The server fetches the preprint's PDF. |
| `kind` | `report`, `check`, `convert` or `repocheck`. Left out, it is `repocheck` for a zip or a link, else `report`. |
| `modules` | Comma-separated checks for a paper. Left out, the default checks run. `GET /api/modules` lists them. |
| `report` | `check` only: `true` adds the HTML report to the JSON. |
| `report_type` | `checklist`, or left out. See "What is stubbed". |
| `crossref` | `true` has the references looked up at Crossref. Default `false`. |
| `run_code` | `true` or `false`. See "Running code". |
| `key_github`, `key_osf`, `key_dryad`, `key_gitlab`, `key_researchbox`, `key_openalex` | The visitor's own key for that service. See "Keys". |

The kinds:

* `report`: a paper in, an HTML report out.
* `check`: a paper in, JSON out: `metacheck_version`, `converter`, `paper_info` (the
  first row of the paper's info), `modules` (each module's output by name) and, with
  `report=true`, `report_html`.
* `convert`: a PDF in, bibr JSON out. No checks run.
* `repocheck`: a `.zip` or a link in, an HTML report of the repository checks out
  (`repo_check`, `code_check`, `data_check`). The report is the brief one. A folder
  is named after the zip, or after the one folder the zip holds.

### Follow a job

`GET /api/jobs/{id}` answers:

```json
{
  "id": "6f1c0f4e-8c6c-4d0e-9d2b-6d5e1c2f9a10",
  "status": "running",
  "step": "running module 3 of 7: ethics_check",
  "progress": 0.4,
  "created": "2026-11-07T10:00:00+00:00",
  "started": "2026-11-07T10:00:01+00:00",
  "status_url": "...",
  "result_url": "..."
}
```

`status` is `queued`, `running`, `done` or `failed`. A field with nothing to say is left
out: `position` is only there while the job is queued, `progress` while it runs, `error`
when it failed, `converter` (`bibr` or `grobid`) once a paper job is done, and `run_code`
when the job runs code. A job that is not known, or that is older than an hour, answers
`404`.

### Get the result

`GET /api/jobs/{id}/result` answers `200` with the report (`text/html`) or the JSON,
`409` while the job is not finished, and `422` with `{"error": "..."}` if it failed.
An HTML report is sent with `Content-Security-Policy: sandbox allow-scripts`, so a report
runs in a sandbox of its own and cannot reach the site. The header
`X-Metacheck-Report-Type` says which layout was made.

### One call

`POST /api/report`, `/api/check`, `/api/convert` and `/api/repocheck` take the same fields
and wait for the result. They answer with the result, or with the error of a failed job.
The job waits in the same queue, so these calls can take a while.

### Other routes

* `GET /api/modules`: `allowed` (the checks that may be asked for), `default`, `info` (name,
  title, description and default flag of each) and `installed` (always empty).
* `GET /api/health`: `status`, `metacheck_version`, `upstream_version`, `bibr`, `grobid`
  (`local` or `remote`), `jobs_running`, `jobs_waiting`, `run_code`, `sandbox`, `local`,
  `key_problems` and `workers`.
* `GET /api/openapi.json`: the OpenAPI description. The interactive page (`/docs`) is not
  served, because it loads its code from a CDN, and this site makes no request to another
  site.

### Errors

An error is `{"error": "<message>"}`:

| Status | When |
|---|---|
| 400 | The request is wrong: no file, an unknown kind or module, a file that is not a PDF, bibr JSON, GROBID XML or zip, a Word file, a bad link or PsyArXiv id, a damaged zip, a zip with a path that leaves its folder. |
| 404 | No such job. |
| 409 | The result is asked for before the job is done. |
| 413 | The file is too big, or the zip holds too many files or unpacks to too much. |
| 422 | The job failed. |
| 503 | The queue is full, or the server is short of disk space. |

### Examples

With curl:

```sh
# one call
curl -F file=@paper.pdf http://127.0.0.1:8080/api/report -o report.html

# a repository
curl -F file=@study.zip http://127.0.0.1:8080/api/repocheck -o report.html
curl -F url=https://github.com/user/repository http://127.0.0.1:8080/api/repocheck -o report.html

# a queued job
curl -s -F file=@paper.pdf -F modules=power,marginal http://127.0.0.1:8080/api/jobs
curl -s http://127.0.0.1:8080/api/jobs/<id>
curl -s http://127.0.0.1:8080/api/jobs/<id>/result -o report.html
```

With Python:

```python
import time
import httpx

base = "http://127.0.0.1:8080"
with open("paper.pdf", "rb") as paper:
    job = httpx.post(f"{base}/api/jobs", files={"file": paper}, data={"modules": "power,marginal"}).json()

while (status := httpx.get(job["status_url"]).json())["status"] in ("queued", "running"):
    print(status["step"])
    time.sleep(2)

result = httpx.get(job["result_url"])
if result.status_code == 200:
    open("report.html", "wb").write(result.content)
else:
    print(result.json()["error"])
```

## Limits

| What | Limit |
|---|---|
| A PDF, bibr JSON or GROBID XML | 50 MB |
| A `.zip` | 500 MB, 20,000 files, 1000 MB unpacked, no path that leaves its folder |
| Jobs running at once | 2 |
| Jobs waiting | 20. The next one gets `503`. |
| One job | 30 minutes. Then its process is stopped and the job fails. |
| A finished job's result | Kept for 60 minutes, then deleted with its folder |
| Free disk | New jobs get `503` when less than 1 GB is free where the jobs live |

The zip limits are checked twice: from what the zip says about itself before a job is
made, and again from what is really written when it is unpacked. A link's download has the
same 500 MB limit, goes only to the known public hosts over https, and is unpacked under the
same limits. Its time limit, its compression ratio and its folder depth follow the hosted
data package limits (`METACHECK_PACKAGE_DOWNLOAD_SECONDS`, `METACHECK_PACKAGE_MAX_RATIO`,
`METACHECK_PACKAGE_MAX_DEPTH`, see [ENVIRONMENT.md](ENVIRONMENT.md)).

## What happens to a file

* Each job has a folder with a random name (a random UUID, mode 0700) under the jobs
  directory. The job's id is that name.
* Each job runs in a process of its own. A key goes into that process only.
* The upload is deleted as soon as it has been read. A PDF is deleted when it has been
  converted.
* A report holds no path of this server.
* The result is deleted 60 minutes after the job ends. The server keeps nothing else: no
  usage log of files or names, and no cookies.

## Keys

A visitor can send their own key for a service with a `key_<service>` field. It is for
that job only: it is held in memory until the job starts, put in the environment of the
job's own process, and never written to disk. It is not in the job's status. A link's
download is sent the server's own tokens only if the visitor sent a key for that host.

| Field | Read as |
|---|---|
| `key_github` | `GITHUB_PAT_GITHUB_COM` |
| `key_osf` | `OSF_PAT` |
| `key_dryad` | `DRYAD_PAT` |
| `key_gitlab` | `GITLAB_PAT` |
| `key_researchbox`, `key_openalex` | Accepted, and no effect: pytacheck reads no such key. |

The website has no box for keys. They are for scripts.

## Running code

`run_code` is off unless the server was started with `--run-code` and Docker is running.
`GET /api/health` says what is on: `run_code` and `sandbox` (`docker` or `none`). If
`--run-code` is given and Docker is not running, the server starts anyway, says so, and
runs no code.

When it is on, a repository job runs the repository's code unless the field says
`run_code=false`. A paper job runs the code of the paper's repository only if the field says
`run_code=true`. The code runs in Docker's sandbox (`reproducibility_check`, missing packages installed,
300 seconds). A `convert` job never runs code.

## What is stubbed

* **`report_type=checklist`** is accepted, but pytacheck has no checklist report yet, so
  the full report (a paper) or the brief report (a repository) is made. The header
  `X-Metacheck-Report-Type` says which. The one place to change when the checklist
  report is ported is `report_layout()` in `src/metacheck/web/worker.py`.
* **ResearchBox links** are refused with a message. pytacheck's data package code does not
  fetch from ResearchBox yet. Download the box and send it as a `.zip`. The ResearchBox page
  of the site says this too.
* **`key_researchbox` and `key_openalex`** have no effect (see "Keys").
* **`crossref`** only has an effect when GROBID converts the PDF. pytacheck's bibr client has
  no such option.
* **`installed`** in `/api/modules` is always `[]`, and a `repocheck` with `modules` is
  refused. The site on SURF Research Cloud runs installed modules; this one has none.

## Differences from the site on SURF Research Cloud

The routes, fields, status codes, limits and the website's layout follow that site. It was
written again, not copied. These differ:

* The checks are pytacheck's own. No R runs. The report is pytacheck's report.
* Word files (`.docx`) are refused. GROBID XML is accepted.
* No limit per visitor and no usage log. A server in front of this one can limit visitors.
* No installed modules (see above), and no checklist report yet.
* No PsyArXiv record checks in the browser. The page does not ask PsyArXiv anything; the
  server fetches the preprint, and a bad id or a missing file comes back as the job's error.
* No interactive API page (see "Other routes").
* The website loads nothing from another site: the font (Cabin, SIL Open Font License) and
  the one script (JSZip 3.10.1, MIT) are served from the package. The folder is packed into
  a `.zip` in the visitor's browser with JSZip.
* `converter` is in a job's status only when the job is done, not while it runs.
* The page of the TU/e data managers and the PsyArXiv reports prototype are not part of the
  site.

## The website

The pages are in `src/metacheck/web/site/` and are served from the package:

| Path | Page |
|---|---|
| `/` | Check a paper, check a repository |
| `/researchbox/` | Check a ResearchBox by its number |
| `/psyarxiv/` | Check a PsyArXiv preprint by its link |
| `/site/...` | Style, scripts, font and logo |

The script talks to the web interface: it sends a job, asks for its status every 2 seconds,
shows the place in the queue, the step and the progress, and then offers the report to open
or download. A folder dropped on the page is read in the browser, left without `.git`,
`.svn` and `.hg`, checked against the limits above, packed with JSZip and sent. A visitor
sees a refusal before the upload, not after it. The pages work from a phone's width up.

The files `site/vendor/jszip.min.js` (SHA-256
`acc7e41455a80765b5fd9c7ee1b8078a6d160bbbca455aeae854de65c947d59e`) and
`site/vendor/LICENSE.markdown` (SHA-256
`566c953c6090b1218ca6217dd7359d45dde46581968586dc607d59a78af6a9c4`) are JSZip 3.10.1 as
published. The font is `site/fonts/Cabin.ttf` with its licence next to it.

Every page is sent with a Content-Security-Policy that allows only the site's own scripts,
styles, images and fonts, and no request to anywhere else.

## Settings

| Variable | Meaning |
|---|---|
| `METACHECK_PSYARXIV_API` | The PsyArXiv API. Default `https://next.psyarxiv.org/api/v1`. |
| `METACHECK_BIBR_URL`, `METACHECK_GROBID_URL` | The converters (see [ENVIRONMENT.md](ENVIRONMENT.md)). |
| `METACHECK_PACKAGE_DOWNLOAD_SECONDS`, `METACHECK_PACKAGE_MAX_RATIO`, `METACHECK_PACKAGE_MAX_DEPTH` | The time, compression ratio and depth limits of a link's download. |
| `GEMINI_API_KEY` and the other LLM keys | Used by the checks that call an LLM, as everywhere else. |
