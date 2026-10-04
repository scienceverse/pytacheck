# Environment variables and folders

This page is the one place that lists every environment variable the package reads,
in the order it reads them, and the folders it uses.

Each setting has a `METACHECK_` name, which is read first. Where the setting existed
before, the old `PYTACHECK_` name keeps working. The package reads its variables
through one table (`metacheck._env.ENV_VARS`); the table below is that table, and a
test keeps the two in step.

## Rules

1. **The first name that is not blank wins.** Names are tried in the order the table
   gives. Values from different names are never merged. A setting is decided by the
   first name that is set, even when that value then turns out to be unusable: if a
   `METACHECK_RSCRIPT` path does not exist, the search goes on to `Rscript` on the
   `PATH`, not to `PYTACHECK_RSCRIPT`.
2. **A blank value means unset.** Blank means empty or only spaces. An empty value
   already meant unset almost everywhere. What changed is a value of only spaces,
   which these settings used as given before: `CACHE_DIR`, `DATA_DIR`, `LOG`,
   `RSCRIPT` and `LLM_CACHE_DIR` (used as a path), `NO_SLEEP` (sleeps stay on),
   `EMAIL`, `STORE_URL`, `R_PARSER` and `LLM_MODEL` (the default is used)
   and `LLM_MAX_CALLS` (`serve` stopped).
   The other settings strip or parse the value, so spaces already gave the default.
3. **Each setting resolves on its own.** `METACHECK_BIBR_URL` next to
   `PYTACHECK_BIBR_BACKEND` is used as given: the URL comes from the first, the
   backend from the second.
4. **A clash is logged, never hidden.** If both names of a setting are set to
   different values, the `METACHECK_` value is used and one line per process says
   which variable is ignored (for example `METACHECK_CACHE_DIR is set, so
   PYTACHECK_CACHE_DIR is ignored`). The line names the variables and never shows a
   value. It goes to the `pytacheck` logger at WARNING, so it is not a Python warning
   and `-W error` is not affected. `LLM_CACHE_DIR` logs at DEBUG (see below).
5. **Messages name the variable that was read.** A record or an error that names a
   variable names the one that was actually set. With an old name the text is what
   it was before. Advice texts ("set X") still name the old variables and stay true,
   because the old names are still read.

## The table

The names of each setting are listed in lookup order, first name first. "Secret"
means the value is a key or token, which the package never logs.

| Setting | Names, in lookup order | Default | Notes |
|---|---|---|---|
| `API_KEY` | `METACHECK_API_KEY`, `PYTACHECK_API_KEY` | none: `serve` binds only to a loopback address unless `--behind-authenticating-proxy` | secret. Stripped. A key under 32 characters is an error that names the variable read, even when the other name holds a valid key (fail closed). See [API.md](API.md). |
| `API_MAX_CHECKS` | `METACHECK_API_MAX_CHECKS`, `PYTACHECK_API_MAX_CHECKS` | the number of CPUs | How many uploads the API checks at once. Zero, a negative number or a value that is not an integer gives the default. |
| `BIBR_BACKEND` | `METACHECK_BIBR_BACKEND`, `PYTACHECK_BIBR_BACKEND` | `bibr` | Stripped and lower-cased. Any value but `bibr` or `scivrs` is an error. See [BIBR.md](BIBR.md). |
| `BIBR_URL` | `METACHECK_BIBR_URL`, `PYTACHECK_BIBR_URL` | the public server list | The address of the bibr server the app uses. Stripped, a trailing `/` is dropped. |
| `CACHE_DIR` | `METACHECK_CACHE_DIR`, `PYTACHECK_CACHE_DIR` | see below | It has two meanings, as before. For `cache_dir()` and the app's cache, it replaces the platform cache folder. For the `.metacheck_*` caches, the option `metacheck.cache.dir` comes first, then this variable, then the working directory. |
| `CHECK_MUTATION` | `METACHECK_CHECK_MUTATION`, `PYTACHECK_CHECK_MUTATION` | off | `1`, `true`, `yes` or `on` (any case) turns on the mutation check for CI and tests: `run_modules()` and `report()` fingerprint the text and section tables of every paper they index, and raise `StaleDocumentError` when a module edited one in place (a module must copy a paper before changing it). Costs a hash of the tables at each search, so leave it off in normal use. |
| `CONFIG` | `METACHECK_CONFIG`, `PYTACHECK_CONFIG` | the user file plus the project file | Stripped. `none` (any case) means no config files at all. A path means only that file. A file named this way may hold `stores`. |
| `DATA_DIR` | `METACHECK_DATA_DIR`, `PYTACHECK_DATA_DIR` | the platform data folders | Installed packs, store indexes, `trusted.json`, refreshed databases and `regcheck/`. It does not move the log (see `LOG`). |
| `EMAIL` | `METACHECK_EMAIL`, `PYTACHECK_EMAIL` | none (callers use the built-in address) | `email(address)` in code comes first. **The order changed:** `METACHECK_EMAIL` is now read before `PYTACHECK_EMAIL`. R does not read `METACHECK_EMAIL`. |
| `GITHUB_TOKEN` | `METACHECK_GITHUB_TOKEN`, `PYTACHECK_GITHUB_TOKEN`, `GH_TOKEN`, `GITHUB_TOKEN` | none | secret. Stripped. Sent only to GitHub hosts. Redaction covers all four names. `GH_TOKEN` and `GITHUB_TOKEN` are other tools' variables: the first one set wins, with no log line. |
| `GROBID_URL` | `METACHECK_GROBID_URL`, `PYTACHECK_GROBID_URL` | the built-in list | A comma-separated list replaces the built-in list of Grobid servers. |
| `LITERALS` | `METACHECK_LITERALS`, `PYTACHECK_LITERALS` | on | `off`, `0`, `false` or `no` (any case) turns off the required-literal prefilter (`required_literals()`, `detect_many()`), so a suspected missed match can be ruled out. Results are the same either way. `grepl()`'s own prefilter is not affected. |
| `LLM_CACHE_DIR` | `PYTACHECK_LLM_CACHE_DIR`, `METACHECK_LLM_CACHE_DIR` | `.metacheck_llm_cache` under the cache root | shared with R. **The old name ranks first.** `METACHECK_LLM_CACHE_DIR` is R metacheck's name for its own cache, so it ranks last, and a setting meant for this package wins. If the two differ, only a DEBUG line is logged, because that is expected. `CACHE_DIR` feeds only the default cache root, below both names. |
| `LLM_MAX_CALLS` | `METACHECK_LLM_MAX_CALLS` | 200 | shared with R, which has the same name and meaning. No old name. Read by `serve` only, and only when `GEMINI_API_KEY` is set. A value that is not a number stops `serve` (a known gap, see below). |
| `LLM_MODEL` | `METACHECK_LLM_MODEL` | `google_gemini/gemini-3.1-flash-lite-preview` | shared with R. No old name. Read as `LLM_MAX_CALLS` is. |
| `LLM_WORKERS` | `METACHECK_LLM_WORKERS`, `PYTACHECK_LLM_WORKERS` | 1 | The option `metacheck.llm.workers` comes first (the old spelling `pytacheck.llm.workers` is the same option). A value that is not a number gives 1. |
| `LOG` | `METACHECK_LOG`, `PYTACHECK_LOG` | `log/pytacheck.log.jsonl` in the platform data folder | A file path, used as given. |
| `NO_SLEEP` | `METACHECK_NO_SLEEP`, `PYTACHECK_NO_SLEEP` | off | **Any** value that is not blank turns courtesy delays and backoff sleeps off, `0` included. |
| `PDFTOTEXT` | `METACHECK_PDFTOTEXT`, `PYTACHECK_PDFTOTEXT` | `pdftotext` on the `PATH`, then next to the reference R | Used only if the path exists. |
| `PRESET` | `METACHECK_PRESET`, `PYTACHECK_PRESET` | the config preset, then `metacheck::default` | `use(preset=)` in code comes first. Command line and API only. The preset's source names the variable read. |
| `R_PARSER` | `METACHECK_R_PARSER`, `PYTACHECK_R_PARSER` | `r` if the reference R exists, else `python` | The `engine` argument comes first. |
| `RSCRIPT` | `METACHECK_RSCRIPT`, `PYTACHECK_RSCRIPT` | `Rscript` on the `PATH` | The `rscript` argument comes first where a function has one. Some places use the path only if the file exists. |
| `STORE_URL` | `METACHECK_STORE_URL`, `PYTACHECK_STORE_URL` | the built-in store URL | A mirror for the built-in store. The config records which variable set it. |
| `VERBOSE` | `METACHECK_VERBOSE`, `PYTACHECK_VERBOSE` | on | `verbose(value)` in code comes first. `0`, `false` and `no` (any case) mean off. |
| `APP_AUTH` | `METACHECK_APP_AUTH` | `tokens` | Hosted app: `tokens` or `proxy`. New since 0.4.0a1, so it has no old name. |
| `APP_COMMIT` | `METACHECK_APP_COMMIT` | none | Hosted app: the commit of the running version, for the source link. |
| `APP_HOSTS` | `METACHECK_APP_HOSTS` | a host name set by the hosting environment | Hosted app: the host names it is served under, separated by commas. |
| `APP_JOB_TIMEOUT` | `METACHECK_APP_JOB_TIMEOUT` | 900 seconds | Hosted app: how long one check may run. |
| `APP_ROOTS` | `METACHECK_APP_ROOTS` | none | Local app, "Check a data package" page: folders it may read besides your home folder, separated by `:` (Linux, macOS) or `;` (Windows). A folder you type must be inside one of them (links are followed first). Zip uploads are not affected. New, so it has no old name. |
| `APP_TOKENS` | `METACHECK_APP_TOKENS` | none | secret. Hosted app: access tokens of 32 or more characters, separated by commas. |
| `APP_USER_HEADER` | `METACHECK_APP_USER_HEADER` | none | Hosted app, proxy mode only: the header that names the signed-in user. |
| `CONCEPTS` | `METACHECK_CONCEPTS` | `classifier` | `data_check`'s concept tier: `classifier`, `cascade`, `llm` or `rules`. The `concepts` argument comes first, then the option `metacheck.concepts`. Stripped and lower-cased; any other value is an error. New, so it has no old name. |
| `CONCEPT_MODEL` | `METACHECK_CONCEPT_MODEL` | `scienceverse/datacheck-concepts@v1` | The classifier's model: a local folder or `repo@revision` on the Hugging Face Hub. The option `metacheck.concepts.model` comes first. |
| `CONCEPT_THREADS` | `METACHECK_CONCEPT_THREADS` | onnxruntime's default | How many threads the classifier uses. A value that is not an integer is an error when the model loads. |
| `CONCEPT_THRESHOLD` | `METACHECK_CONCEPT_THRESHOLD` | 0.92 | Under `cascade`, columns the classifier is less sure of go to the LLM. The option `metacheck.concepts.threshold` comes first. A value that is not a number gives the default. |

The hosted-app names are read through the same table. The hosted app gets them in a
mapping it is given (its `env=` argument), so tests can pass its settings without
touching the process environment.

### Direct reads

Every read of a name in the table goes through the table, with three exceptions.
All three use names taken from the table.

* `app/hosted.py` reads the `APP_*` names from the `env=` mapping it is given.
* `packs.auth._secrets()` reads every name of `GITHUB_TOKEN`, so that a token set under
  any of the four names is hidden in messages and logs. It does not use the table's
  `secret` column: API keys and app tokens are not added to that redaction.
* `config.config_stamp()` stamps the raw value of every name in the config settings
  (`CONFIG`, `DATA_DIR`, `STORE_URL` and `PRESET`, both names of each). It is a
  fingerprint for cache invalidation, not a lookup.

A test stops any other module from spelling a variable name as a string.

### Names that are not in the table

These are shared with R or with other tools, and they are read as before:

* `SCIVRS_API_KEY`, `BIBR_URL`, `BIBR_API_URL` and `BIBR_API_KEY` (the bibr clients);
* the LLM provider keys, such as `GEMINI_API_KEY`;
* `GITHUB_PAT_GITHUB_COM`, `NETRC` and the personal access tokens for the other
  archive hosts;
* `REGCHECK_*`, `GRADIO_*`, `PORT`, `TZ`, `TESTTHAT` and `PYTEST_CURRENT_TEST`;
* `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_CACHE_HOME` and `XDG_STATE_HOME`, which
  the platform-folder library reads as it always did.

### Reserved names

The package never reads these with another meaning.

* The installer's: `METACHECK_HOME`, `METACHECK_NO_LAUNCH`, `METACHECK_UNINSTALL`,
  `METACHECK_REF`, `METACHECK_SPEC` and `METACHECK_CONSTRAINTS`.
* R metacheck's: `METACHECK_USAGE_DIR` (its Shiny apps), and `METACHECK_API_URL` and
  `METACHECK_BIBR12_EXPORTS` (used only in its tests).

### Names shared with R

R metacheck reads `METACHECK_LLM_CACHE_DIR`, `METACHECK_LLM_MODEL` and
`METACHECK_LLM_MAX_CALLS`, with the same meaning as here. It reads none of the other
`METACHECK_` names of the table. Two of them, `METACHECK_EMAIL` and
`METACHECK_LLM_CACHE_DIR`, were already read by this package before the table. A
test checks the list of shared names against R's source.

## Deprecation

The `PYTACHECK_*` names still work and are deprecated. No notice is printed when you
use one. They stay until an end date is agreed with the people who run deployments
that use them, and that date is published in advance.

One name is not deprecated: `PYTACHECK_LLM_CACHE_DIR`. It ranks first, and it is the
only way to give this package an LLM cache of its own, because
`METACHECK_LLM_CACHE_DIR` is R's shared setting.

## Folders

The folders keep the name `pytacheck`, and nothing moves. They are already separate
from R metacheck's folder, which is named `metacheck`. This version and 0.4.0a1 use
the same folders, so a setting, a key or a pack saved by one is found by the other.

| Used by | Linux | macOS | Windows |
|---|---|---|---|
| Config (`config.json`), the saved bibr key (`app-keys.json`) | `~/.config/pytacheck` | `~/Library/Application Support/pytacheck` | `%LOCALAPPDATA%\pytacheck\pytacheck` |
| Packs, `stores/`, `trusted.json` | `~/.local/share/pytacheck` | `~/Library/Application Support/pytacheck` | `%LOCALAPPDATA%\pytacheck\pytacheck` |
| Refreshed databases, `regcheck/`, the log | `~/.local/share/pytacheck` | `~/Library/Application Support/pytacheck` | `%LOCALAPPDATA%\scienceverse\pytacheck` |
| `cache_dir()` | `~/.cache/pytacheck` | `~/Library/Caches/pytacheck` | `%LOCALAPPDATA%\scienceverse\pytacheck\Cache` |
| The app's cache: downloaded data files and, through the option `metacheck.cache.dir`, its `.metacheck_*` caches | `~/.cache/pytacheck` | `~/Library/Caches/pytacheck` | `%LOCALAPPDATA%\pytacheck\pytacheck\Cache` |
| The app's state (`app.json`, `open.html`) | `~/.local/state/pytacheck` | `~/Library/Application Support/pytacheck` | `%LOCALAPPDATA%\pytacheck\pytacheck` |

The `XDG_*_HOME` variables are honoured wherever the platform-folder library honours
them. The Windows split between `pytacheck\pytacheck` and `scienceverse\pytacheck`
stays as it was, because ending it would move files.

### Overrides

Each override reads its `METACHECK_` name first, then its `PYTACHECK_` name.

* **Config:** `CONFIG` (`none`, or one file).
* **Data root** (packs, stores, trust, databases, `regcheck/`): `DATA_DIR`, else the
  platform data folders.
* **Cache:** `cache_dir()` and the app cache use `CACHE_DIR`, else the platform cache
  folders. The `.metacheck_*` caches use the option `metacheck.cache.dir`, then
  `CACHE_DIR`, then the working directory.
* **Log:** `LOG`, else `log/pytacheck.log.jsonl` in the platform data folder. It does
  not follow `DATA_DIR`.
* **LLM cache:** `LLM_CACHE_DIR` (old name first).
* **App state:** no variable.

A deployment that sets only `PYTACHECK_*` names resolves every folder as it did
before.

### Folders shared with 0.4.0a1

What each version finds in the shared folders:

* `config.json`, `app-keys.json`, `trusted.json`, `stores/`, the databases and the
  log have the same format in both, so each version reads what the other wrote.
* Installed packs: the install record is still written as `.pytacheck-install.json`,
  and the pack modules keep their internal `pytacheck_packs` names, so a pack
  installed by one version is valid in the other.
* The repo-info cache `.metacheck_repo_info_cache` sits under the cache root
  (the option `metacheck.repo_info_cache.dir`, else the option `metacheck.cache.dir`,
  else `CACHE_DIR`, else the working directory). It is in a platform cache folder only
  for the app. Where both versions use the same cache root, entries written by this
  version are misses for 0.4.0a1, which costs one fetch, and entries written by
  0.4.0a1 are hits here.
* `app.json` describes the running app. Only one app runs at a time, whichever
  version it is.

Run records and saved tables are written where you save results, not in these folders.

### Folders shared with R, and one that is not the package's

* **Shared with R on purpose:** the corpus cache `papers/` under
  `user_data_dir("metacheck", "scienceverse")`, which is R's folder, so a corpus cached
  by either package is reused. This is the one exception to "the folders are separate
  from R's". The `.metacheck_*` caches in the working folder are shared with R too.
* **Not the package's folder:** the installer's default home on Linux,
  `~/.local/share/metacheck` (`METACHECK_HOME` in `install.sh`), is R's data folder. It
  holds the installer's own Python and tool environment, not package data. It is a
  known exception, and the installer owns it.

## Logging

The package logs under `pytacheck` and `pytacheck.api`, and those names do not change.
Both loggers have a parent named `metacheck`, so a handler or a level set on
`metacheck` also applies to the package's records. Existing handlers on `pytacheck`
keep working, and `%(name)s` still shows `pytacheck`.

One caveat. `logging.config.dictConfig` disables existing loggers by default. A
configuration that names only `metacheck` therefore disables the `pytacheck` loggers,
because they are not name-children of `metacheck`. Set `disable_existing_loggers` to
`false` in such a configuration, or name `pytacheck` as well.

## Running in a container

Defaults baked into an image use the `PYTACHECK_` name (for example
`PYTACHECK_CACHE_DIR=/cache`), because a default on the `METACHECK_` name would beat
the old name you set yourself. Your own settings may use either name. The bundled
`docker-compose.yml` passes only the `PYTACHECK_` names, and requires
`PYTACHECK_API_KEY`, until a later release renames them. If you set
`METACHECK_CACHE_DIR` next to the image's `PYTACHECK_CACHE_DIR`, the clash line is
logged once at start-up, and your value is used.

## Known gaps

* `METACHECK_LLM_MAX_CALLS=abc` stops `serve` with an error. R metacheck treats such
  a value as missing. This is not fixed yet.
* Two developer tools read only the old names: `PYTACHECK_R_IMAGE` in the R image
  scripts `parity/r/docker/Rscript` and `parity/r/docker/build.sh`, and `PYTACHECK_RSCRIPT` in
  `benchmarks/compare_r.py`. With only `METACHECK_RSCRIPT` set, `compare_r.py` takes
  the `Rscript` on the `PATH` while the package takes the twin. The parity harness
  itself folds any `METACHECK_*` twin into its `PYTACHECK_*` name at start-up
  ([PARITY.md](PARITY.md)).
