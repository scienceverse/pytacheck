# Deploy the app as a shared link

This is for a link that a few people open in a browser, with nothing to install. They need a personal access token in the link.

## On a Hugging Face Space

1. Create a new Space. Choose the Docker SDK and make the Space private.
2. Copy `README.md` and `Dockerfile` from this folder into the Space.
3. In the `Dockerfile`, or as a build variable named `REF` in the Space settings, set `REF` to the commit of pytacheck to install. Use a full commit hash.
4. In the Space settings, add these secrets:
   - `METACHECK_APP_TOKENS`: one access token per person, separated by commas. Each token has at least 32 characters.
   - `SCIVRS_API_KEY` (optional): a key for the bibr service, used for PDFs.
   - `OSF_PAT` (optional): an OSF token for the checks that look up OSF pages.
5. The Space sets `SPACE_HOST` itself, and the app allows that host name only.

## One link per person

Make a token for each person:

```sh
openssl rand -hex 24
```

Put all tokens in `METACHECK_APP_TOKENS`, separated by commas. Send each person their own link:

```
https://<host>/?token=<their token>
```

The first visit stores a cookie and takes the token out of the address bar. After that the person can open the plain address `https://<host>/` on that browser.

## Take a link away

Remove that person's token from `METACHECK_APP_TOKENS` and restart the Space. Their cookie stops working.

## What the server keeps

An uploaded paper and its report are deleted when the check ends. The report goes to the browser as a download link inside the page. The server keeps no log of tokens or papers. At most 2 checks run at a time and at most 10 wait. A check that takes longer than `METACHECK_APP_JOB_TIMEOUT` seconds (default 900) is stopped with a plain message.

## Any other host

Build the image with the commit to install, then run it. Put it behind a proxy that serves https, and set the host name people use in `METACHECK_APP_HOSTS` (comma-separated for several).

```sh
docker build --build-arg REF=<commit> -t metacheck-app deploy/space
docker run --rm -p 7860:7860 \
  -e METACHECK_APP_TOKENS=<token>,<another token> \
  -e METACHECK_APP_HOSTS=<host name> \
  metacheck-app
```

Without `METACHECK_APP_TOKENS` or a host name the app refuses to start. Cookies are marked Secure, so the link has to be opened over https.
