# Deploy the app as a shared link

This is for a link that a few people open in a browser, with nothing to install. They need a personal access token in the link, unless a sign-in proxy lets them in (see [Behind a sign-in proxy](#behind-a-sign-in-proxy)).

## On a Hugging Face Space

1. Create a new Space. Choose the Docker SDK. Make the Space public: the access tokens are what keep people out, and the files in this folder hold no secrets. A private Space opens only for members of your Hugging Face organisation who are signed in to Hugging Face, so people with a token link could not open it.
2. Copy `README.md` and `Dockerfile` from this folder into the Space.
3. In the `Dockerfile`, or as a build variable named `REF` in the Space settings, set `REF` to the commit of pytacheck to install. Use a full commit hash.
4. In the Space settings, add these secrets:
   - `METACHECK_APP_TOKENS`: one access token per person, separated by commas. Each token has at least 32 characters.
   - `SCIVRS_API_KEY` (optional): a key for the bibr service, whichever kind of service it is (R metacheck reads a key of this name). People who choose bibr to read their PDF then need no key of their own. Without it, each person types their own key; it is used for that check only and never saved on the server.
   - `PYTACHECK_BIBR_URL` (optional): the address of the bibr service: the root of a `bibr serve`, or of the hosted bibr service in front of it, where a paper goes to `/papers/jobs`. It starts with `https://`. Plain `http://` works only for an address on the same machine (`localhost`, `127.0.0.1` or `::1`), because the key goes along with every paper. Without it, the app takes the address from metacheck's public server list, whose entries say which kind of service each one is.
   - `PYTACHECK_BIBR_BACKEND` (optional): the kind of service at `PYTACHECK_BIBR_URL`. `bibr`, the default, is `bibr serve` and the hosted bibr service. `scivrs` is the Scienceverse platform, where a paper goes to `/jobs`. The app does not start with any other value.
   - `OSF_PAT` (optional): an OSF token for the checks that look up OSF pages.
5. The Space sets `SPACE_HOST` itself, and the app allows that host name only.

Send people the direct address of the Space, which is the `SPACE_HOST` value (shown as the direct URL in the Space's embed settings, and ending in `.hf.space`). Do not send the `huggingface.co/spaces/...` page: it shows the app inside another site's frame, and the app refuses that.

## One link per person

Make a token for each person:

```sh
openssl rand -hex 24
```

Put all tokens in `METACHECK_APP_TOKENS`, separated by commas. Send each person their own link:

```
https://<host>/?token=<their token>
```

The first visit stores a cookie and takes the token out of the address bar. The cookie lasts until the browser is closed, and it is not sent when someone clicks a plain link from a mail or a chat. Tell people to keep their link and to open it each time.

## Take a link away

Remove that person's token from `METACHECK_APP_TOKENS` and restart the Space. Their cookie stops working.

## What the server keeps

An uploaded paper and its report are deleted when the check ends. The data check (on unless a person unticks it) downloads the data files a paper links to; those files are public, and they are kept in the container's cache folder for the next run until the container is replaced. The data check is part of the run, so the time limit below covers it. The report goes to the browser as a download link inside the page. The server keeps no log of tokens or papers. At most 2 checks run at a time and at most 10 wait. A check that takes longer than `METACHECK_APP_JOB_TIMEOUT` seconds (default 900) is stopped and its process ended, and the person gets a plain message.

## Any other host

Build the image with the commit to install, then run it behind a proxy that serves https. Set the host name people use in `METACHECK_APP_HOSTS` (comma-separated for several).

```sh
docker build --build-arg REF=<commit> -t metacheck-app deploy/space
docker run --rm -p 127.0.0.1:7860:7860 \
  -e METACHECK_APP_TOKENS=<token>,<another token> \
  -e METACHECK_APP_HOSTS=<host name> \
  metacheck-app
```

Use `-p 127.0.0.1:7860:7860` when the proxy runs on the same machine, so that the container cannot be reached around it over plain http. The proxy must:

- pass the `Host` header unchanged (in nginx: `proxy_set_header Host $host;`). The app checks that header only and answers 403 to any other name.
- set `X-Forwarded-Proto https`.
- turn off response buffering, because the page gets its progress as a stream (in nginx: `proxy_buffering off;`).

To offer bibr with the server's key, add `-e SCIVRS_API_KEY -e PYTACHECK_BIBR_URL=<address>` (step 4 above). `-e SCIVRS_API_KEY` without a value passes the key from your shell, so it is not written in the command.

Without `METACHECK_APP_TOKENS` (or `METACHECK_APP_AUTH=proxy`) or a host name the app refuses to start. It also refuses to start with a `PYTACHECK_BIBR_URL` or `PYTACHECK_BIBR_BACKEND` that cannot work, and says what to change. Cookies are marked Secure, so the link has to be opened over https.

## Behind a sign-in proxy

When the server already has a sign-in page in front of it (a proxy that lets only signed-in people through), the app needs no tokens. Set:

- `METACHECK_APP_AUTH=proxy`, and no `METACHECK_APP_TOKENS`.
- `METACHECK_APP_HOSTS` as above.
- `METACHECK_APP_USER_HEADER` (optional but recommended): the header that the proxy adds to every request it lets through, for example `X-Forwarded-User`. A request without it gets 403.

```sh
docker run --rm -p 127.0.0.1:7860:7860 \
  -e METACHECK_APP_AUTH=proxy \
  -e METACHECK_APP_HOSTS=<host name> \
  -e METACHECK_APP_USER_HEADER=<header name> \
  metacheck-app
```

In this mode anyone who reaches the container gets in, so it must be reachable through the proxy only: publish the port on `127.0.0.1` or on a network that only the proxy is on. The proxy must also remove that header from what the browser sends before it adds its own. The Host, `X-Forwarded-Proto` and buffering rules above still apply. The app still checks the host name, refuses requests that another site starts, and keeps no log of papers.
