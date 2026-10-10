# The REST API

`pytacheck serve` runs the REST API, a port of metacheck's plumber API. It needs
`pip install "metacheck[api]>=0.4.0a1"`. The routes are listed at `/docs` and in the
docstring of `pytacheck.api.app`.

## Responses

Routes, parameters and status codes are the plumber API's. The bodies are plain
JSON (D79 in [UPSTREAM_ISSUES.md](UPSTREAM_ISSUES.md)):

* A scalar is a JSON scalar: `"status": "ok"`, `"count": 47`.
* A table (info, authors, references, cross-references, search results, a module's
  `table` and `summary_table`) is an array of row objects. Every row has every
  column; a missing cell is `null`. A paper without that table gives `[]`.
* Numbers are JSON numbers at full precision. `NaN` and infinities are `null`.
* A missing value is `null`, for example a module's `summary_text` when it has none.
* An error is `{"error": "<message>"}` with a 4xx or 5xx status.

`POST /paper/info` on the demo paper:

```json
[{"paper_id": "to_err_is_human", "title": "To Err is Human: An Empirical Investigation",
  "keywords": null, "doi": "10.32614/10.5281/zenodo.2669586", "description": null}]
```

`POST /paper/check` with `modules=marginal,all_urls` and `report=false`, shortened:

```json
{
  "metacheck_version": "0.3.1",
  "paper_info": [{"paper_id": "to_err_is_human", "title": "To Err is Human: …", "keywords": null, …}],
  "authors": [{"author_id": 1, "given": "Daniel", "family": "Lakens", "corresponding": false, …}],
  "references": […],
  "cross_references": […],
  "modules_run": ["marginal", "all_urls"],
  "results": {
    "marginal": {
      "module": "marginal",
      "title": "Marginal Significance",
      "table": [{"text": "…", "text_id": 3, "page_number": null, …}],
      "summary_table": [{"paper_id": "to_err_is_human", "marginal": 2}],
      "summary_text": "You described 2 effects with terms related to 'marginally significant'.",
      "report": ["You described effects with terms …", [{"Text": "…", "Section Header": "Abstract"}, …], "…"],
      "traffic_light": "red"
    },
    "all_urls": {"module": "all_urls", …, "summary_text": null, "report": "", "traffic_light": "info"}
  },
  "report_html": ""
}
```

A module's `report` is a string, or a list of blocks: text blocks are strings,
table blocks are arrays of row objects.

### Migrating from metacheck's plumber API

metacheck's plumber API writes JSON with jsonlite's defaults. Clients written for
it need these changes:

| plumber (jsonlite) | pytacheck |
|---|---|
| `"status": ["ok"]`, `"traffic_light": ["red"]`, `"report_html": ["<html>…"]` | `"status": "ok"`, `"traffic_light": "red"`, `"report_html": "<html>…"`: drop the `[0]` |
| a missing table cell is left out of its row object | the key is there with `null` |
| numbers rounded to 4 decimal places | full precision |
| `"NA"`, `"NaN"`, `"Inf"` strings for missing or non-finite numbers | `null` |
| `NULL` as `{}` (`"summary_text": {}`, a paper without references) | `null`, and `[]` for an absent table |

Error bodies were already unboxed and do not change.

## Access

The plumber API has no authentication. pytacheck adds an API key.

Set `PYTACHECK_API_KEY` to a random string of 32 or more characters:

```bash
export PYTACHECK_API_KEY=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')
pytacheck serve --host 0.0.0.0
curl -H "Authorization: Bearer $PYTACHECK_API_KEY" -F file=@paper.json http://localhost:8000/paper/info
```

* Every route except `GET /health` needs the header `Authorization: Bearer <key>`.
  That includes `/docs` and `/openapi.json`. A missing or wrong key gets a 401 with
  a short message.
* A key shorter than 32 characters stops the server from starting. Spaces around the
  key in the variable are ignored.
* The key is read from the environment only, so it does not show up in the process
  list. It is never logged.

Environment variables and folders: see [ENVIRONMENT.md](ENVIRONMENT.md).

Without a key the API is open, so `pytacheck serve` only binds to a loopback address
(`127.0.0.1`, `::1`, `localhost`). With a key, `--host` can be anything.

If you start it on another host without a key, `serve` refuses and says so. When
something in front of the server already authenticates every request (a reverse
proxy or an API gateway), say that with `--behind-authenticating-proxy`:

```bash
pytacheck serve --host 0.0.0.0 --behind-authenticating-proxy
```

This flag turns off the check and nothing else. The server then trusts every request
it receives, so only use it when the port is not reachable except through that
proxy. A key and the flag can be combined: the key is still required.

The bind check belongs to `pytacheck serve`. Starting the app with
`uvicorn pytacheck.api.app:create_app --factory` enforces the key when one is set,
but does not check the host.

## Docker

`docker-compose.yml` passes `PYTACHECK_API_KEY` to the container and stops with an
error when it is unset. Compose reads the variable for every command, not only `up`,
so `docker compose down` and `docker compose logs` need it too. The simplest way is a
`.env` file next to `docker-compose.yml`, which compose reads by itself and git ignores:

```bash
echo "PYTACHECK_API_KEY=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" > .env
docker compose up
```

Compose also publishes bibr's port on `127.0.0.1` only, since bibr has no key.

With `docker run`, pass the variable with `-e PYTACHECK_API_KEY`.
