# The installer

`install.sh` (macOS, Linux) and `install.ps1` (Windows) sit at the top of the
repository. They put metacheck and everything it needs into one folder and open
the app. [docs/TRY.md](../docs/TRY.md) is the page for users.

## What is here

| File | What it is |
|---|---|
| `constraints-app.txt` | The exact version of every dependency of `pytacheck[app]`. Generated from `uv.lock`; never edited by hand. |
| `smoke.py` | Starts an installed `metacheck-app` and checks the self-test, `/healthz` and the token gate. Used by CI. |
| `ui_smoke.py` | Starts an installed `metacheck-app` and clicks through the page in Chromium (Playwright): the demo paper, then an uploaded file. Fails on a console error or a request to any host but 127.0.0.1, and saves a screenshot. Used by CI; `--browser-path` uses a Chromium you already have. |

## The constraints file

`uv tool install` ignores `uv.lock`. The installer passes this file with `-c`
instead, so a user gets the versions we tested.

One command makes the file. Run it from the repository root whenever
`uv.lock` or the `app` extra changes:

```sh
uv export --locked --no-hashes --no-dev --extra app --no-emit-project --no-annotate --output-file install/constraints-app.txt
```

The `installer` workflow runs the same command and fails when the result
differs from the committed file. That includes a dependency update that
changes `uv.lock`: run the command on that branch and commit the file. The
install jobs run either way.

## The pinned commit

Both scripts have a `REF` line near the top: the commit that a plain run
installs. It sets the archive the app comes from and the constraints file that
goes with it. Both scripts must carry the same value (a test checks this). Move
it, and regenerate the constraints file, in the same change that the new
commit is meant to ship.

## The folder

`METACHECK_HOME` defaults to `~/.local/share/metacheck` (`$XDG_DATA_HOME/metacheck`
when set) on Linux and macOS, and `%LOCALAPPDATA%\metacheck` on Windows.

| Subfolder | Holds |
|---|---|
| `uv/` | the uv binary |
| `python/` | the Python that uv downloads |
| `tools/` | the app's virtual environment |
| `bin/` | `metacheck-app` |
| `cache/` | uv's download cache |

The scripts edit no shell profile, no `PATH` and no Windows registry key. They
set uv's folder variables, `UV_NO_CONFIG` (a user's own uv settings do not
apply), `UV_NO_MODIFY_PATH`, `UV_PYTHON_INSTALL_BIN=0` and
`UV_PYTHON_INSTALL_REGISTRY=0` for their own uv commands only.

`--uninstall` removes the tool with uv, then the five subfolders, then the folder
itself if it is empty. It refuses a folder that is empty, `/` or a drive root, the
home folder, a link, or any folder not named `metacheck`. The app's own settings
are not touched.

## Settings for scripts and CI

| Variable | Effect |
|---|---|
| `METACHECK_HOME` | the folder |
| `METACHECK_NO_LAUNCH=1` | install, do not open the app |
| `METACHECK_UNINSTALL=1` | uninstall |
| `METACHECK_REF` | install this commit instead of the pinned one |
| `METACHECK_SPEC` | install this requirement instead of the GitHub archive |
| `METACHECK_CONSTRAINTS` | use this constraints file (a path) |

## The uv pin

`UV_VERSION` and the SHA-256 of each archive are written into both scripts.
To move to another uv release, copy the checksums from the `.sha256` files
of that release page, change both scripts and the workflow's `UV_VERSION`.
