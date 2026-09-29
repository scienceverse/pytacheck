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
