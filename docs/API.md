# The REST API

`pytacheck serve` runs the REST API, a port of metacheck's plumber API. It needs
`pip install "metacheck[api]>=0.4.0a1"`. The routes are listed at `/docs` and in the
docstring of `pytacheck.api.app`.

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

## Deprecated Python names

This page is about the REST API; the Python API is the top level of `import metacheck`.
38 of its names exist only because the R package exports them, and nothing in metacheck
uses them: no module, check, pack, CLI command, API route or app page. They are
deprecated and go in the next minor release (decided by the maintainer, 2026-10-10).
Reading one from `metacheck` gives one `DeprecationWarning` per process; importing it
from its own submodule does not warn. The list is `_DEPRECATED` in
`src/metacheck/__init__.py`, and `tests/foundation/test_deprecations.py` fails if code
in the package starts to use one of them. The [changelog](../CHANGELOG.md) says what was
kept, and why.

| Name | Evidence (callers in `src/`) | Instead |
|---|---|---|
| `dataverse_file_download` | none; `repo_check` lists Dataverse with `dataverse_info()` and `download_repo_files()` fetches the files | `repo_check`, then `download_repo_files()` |
| `dryad_file_download` | none; `repo_check` lists Dryad with `dryad_info()` | as above |
| `dspace7_file_download` | none; `repo_check` lists DSpace 7 items with the private `_dspace7_file_lists()` | as above |
| `figshare_file_download` | only `researchdata4tu_file_download()`, itself deprecated; `repo_check` uses `figshare_info()` | as above |
| `psycharchives_file_download` | none; `repo_check` uses the private `_psycharchives_file_lists()` | as above |
| `researchdata4tu_file_download` | none; `repo_check` uses `researchdata4tu_info()` | as above |
| `reshare_file_download` | none; `repo_check` uses `reshare_info()` | as above |
| `zenodo_file_download` | none; `repo_check` uses `zenodo_info()` | as above |
| `fsd_info`, `fsd_links` | none; no check looks for Finnish Social Science Data Archive links | – |
| `github_info` | none; `repo_check` lists GitHub with `github_files()` and `github_tree_files()` | – |
| `github_languages`, `github_readme` | only `github_info()` | – |
| `osf_api_check` | none; the OSF checks call `osf_info()` and `osf_get_all_pages()` directly | – |
| `osf_preprint_list` | none; no check lists OSF preprints | – |
| `psycharchives_info` | none; `repo_check` uses `_psycharchives_file_lists()` | – |
| `psycharchives_links` | none; `repo_check` finds PsychArchives links with `dspace_links()` | `dspace_links()` |
| `rbox_info` | none; `repo_check` lists ResearchBox with `rbox_file_download()` | – |
| `zenodo_upload` | none; it uploads a user's folders to Zenodo, outside any check (ROADMAP R-ONLY-FEATURES) | – |
| `check_orcid`, `get_orcid`, `orcid_person` | none; no check looks up ORCIDs | – |
| `credit_roles` | none; it returns the CRediT role table (the paper readers fill the schema field of that name themselves) | – |
| `datacite_doi`, `openalex_doi`, `openalex_query` | none; the reference checks query Crossref through `add_bib_match()` | – |
| `doi_lookup`, `doi_resolves` | none; no check asks doi.org | – |
| `rw` | none; an alias of `retractionwatch()`, and `ref_retraction` reads the database with `rw_rows()` | `retractionwatch()` |
| `papers_available` | only `papers_metadata()`, itself deprecated | – |
| `papers_load`, `papers_metadata`, `papers_remove` | none; they download, describe and delete the scienceverse/papers corpora in a session | – |
| `export_jasp_html`, `export_mplus_html`, `export_omv_html`, `export_spv_html`, `export_stata_smcl_html` | none; the checks read statistics output with the `import_*()` functions (`reproducibility_check`, the data file readers), which stay (ROADMAP D19) | – |
