# Changelog

This is the Python version of metacheck. It has its own version line, starting
at 0.4.0 (0.4.0a1 is the first release); the version does not follow the R
package's. The R commit each release is compared against is in
`parity/UPSTREAM.toml` and in the notes of the release.

## Unreleased

### Changed: R code is read from R Markdown and Quarto chunks without R

- **Changed:** `code_extract_r()` (and `code_parse_r()`, code_check and reproducibility_check on `.Rmd`/`.qmd` files) reads the chunks statically instead of reproducing `knitr::purl()` and evaluating chunk options with a small R evaluator (D80). Chunk delimiters, `#|` options and `<<label>>` references work as before.
- **Changed:** chunk options are used only when they are literal (`eval = FALSE`, `purl = FALSE`, `error = TRUE`, `engine = "python"`, `#| eval: false`). An option that is an R expression (`eval = params$run`, `eval = !knitr::is_latex_output()`) is not evaluated and counts as not given, so the chunk is extracted as R code; knitr dropped a chunk whose `eval` it could not evaluate (also a typo such as `eval=False`). `child` documents are not read.
- **Fixed with it:** malformed chunk options, a `NA` first code line, `engine = NA` and similar input no longer make the extraction fail; an empty chunk of another engine no longer gives a `## NA` line; a document that mentions `\Sexpr{}` is still read as R Markdown when it has R Markdown chunks.
- **Changed:** YAML `params` are written as `params <-` and one `list(...)` line, with `!r` values as their R source (knitr evaluated them and wrote `dput()`'s wrapped lines).
- **Unchanged:** the extracted code of every R Markdown/Quarto file in the test and parity inputs, except a test fixture whose `eval=False` chunk is now extracted, and the accuracy report.
- **Removed:** `metacheck.codecheck._purl` and `metacheck.codecheck._reval` (1,748 lines); the extractor, `metacheck.codecheck._chunks`, is 511.

### Changed (breaking for API clients): the REST API returns plain JSON

- **Breaking:** the REST API (`pytacheck serve`) no longer copies the JSON encoding of metacheck's plumber API (jsonlite's defaults). Responses are plain JSON (D79):
  - scalars are not wrapped in arrays: `"status": "ok"`, `"count": 47`, `"traffic_light": "red"`, `"report_html": "<!DOCTYPE html>…"` (were `["ok"]`, `[47]`, `["red"]`, `["<!DOCTYPE html>…"]`);
  - a table is an array of row objects that holds every column, with `null` for a missing cell (missing cells were left out);
  - numbers keep full precision (were rounded to 4 decimal places), and `NaN` and infinities are `null` (were the strings `"NA"`, `"NaN"`, `"Inf"`);
  - a missing value is `null` (`"summary_text": null`, was `{}`), and an absent paper table is `[]` (was `{}`).
- **Unchanged:** routes, parameters, status codes, and error bodies (`{"error": "…"}`, already unboxed).
- **Migrating a client:** drop the `[0]` that unwrapped scalars, read a missing table cell as `null` rather than an absent key, and stop parsing `"NA"`/`"NaN"`/`"Inf"` strings. [docs/API.md](docs/API.md#responses) shows the shapes.
- **Removed:** `metacheck.api.jsonlite` (`to_json`, `format_number`).

### Changed: statcheck runs on plain Python values and scipy

- **Changed:** the statcheck port behind `stats()` and stat_check computes with plain Python values (`None` is missing) and `scipy.special`, not with a port of R's runtime (three-valued `NA` logic, R's warnings and errors) and of R's nmath library (`metacheck.stats._rmath` is removed) (D78). The t and F p-values use the same reductions to the incomplete beta function as R's `pt()` and `pf()`, so on the 21 papers of the accuracy report every result of `stats()` and stat_check is unchanged, to the last digit of each p-value.
- **Fixed with it:** a result whose p-value cannot be computed (zero degrees of freedom in a t, r or F test) or whose consistency cannot be decided is left out, never reported as consistent. statcheck reported such a result as having no error when the statistic and the p-value were given with `<` or `>` (`t(0) < 2.1, p > .05`), which could make stat_check green.
- **Changed:** infinite degrees of freedom (a number too large for a float) follow scipy: a t test or correlation uses the normal distribution, a chi-square or Q test gets p = 1, and an F test cannot be checked and is left out (R uses the chi-square limit for F, and failed on a small chi-square statistic).
- **Changed:** statcheck's flags (`OneTailedTests`, `pEqualAlphaSig`, `pZeroError`, `OneTailedTxt`, `AllPValues`, `messages`) must be `True` or `False` (or 1 or 0); another value such as 2 raises `ValueError` (R treated 2 as `FALSE`). `stats()` takes statcheck's settings as ordinary keyword or positional arguments (`stats(paper, stat="t", alpha=0.01)`): an abbreviated name (`alp=`) or `texts=` raises `TypeError` instead of being matched as R matches `...`. Calling `statcheck()` no longer emits R's warnings (`NAs introduced by coercion`).
- **Smaller:** the statcheck port and `stats()` are 1,105 lines, down from 1,711.

### Internal: data_check helpers read column kinds from pandas dtypes

- **Changed:** `datacheck/_checks_rvec.py`, a 621-line model of R's atomic vectors (`RVec`, `rvec()`, `chr()`, `num()`, `quantile7()`, `tolower()`, ...), is gone. The data_check and codebook helpers read a column's kind (numeric, logical, text, categorical, date-time) from its pandas dtype through the small `datacheck/_kinds.py` and use plain pandas, numpy and Python string operations (D77). `as.character()` keeps R's number formatting where users see it (messages, sample values).
- **Unchanged:** every traffic light, count and reported number on real data files; the parity cases of the data areas and the accuracy gate give the same results.
- **Edge cases (D77):** a list mixing `True` with numbers is text, `as.numeric()` of a date-time is `NA`, a whole number beyond 2^31 is written in full in a message, lower- and upper-casing are Python's, and `data_check_constant()` counts the text `"NaN"` like any other value (R's `table()` drops it, and fails on a column of only `"NaN"`).

### Changed: json_expand reads plain JSON

- **Changed:** `json_expand()` parses each reply with `metacheck._json.loads()` and flattens it, instead of reproducing jsonlite's R value model, R's deparser and `utils::type.convert()` (D76). A JSON null stays missing, also in text columns (metacheck gives `""` there). A nested array or object is the text of its values joined by `";"` (`["E", "F"]` is still `"E;F"`; `{"b": [1, 3]}` is `"1;3"`, not `c(1, 3)`). Column types are still guessed from the values: `true`, `"TRUE"` and `"F"` make a logical column, `"1"` an integer, `"2.5"` a number; anything else is text.
- **Changed:** JSON is read strictly: a reply with comments is a `"parsing error"`. `{"$date": ...}` is an ordinary object, not a date, and no column is complex. A repeated key keeps its last value, an empty key is a column, and an array that mixes objects with other values is `"not a list"`: metacheck stopped with an error on all three.
- **Unchanged:** the values extracted from every recorded LLM reply, including the `power` module's prompt-based fallback; the `error` column (`"parsing error"`, `"not a list"`); the Markdown fence handling; the suffixes on clashing names.
- **Removed:** `metacheck.text.json_expand.as_numeric` and `type_convert`; use `metacheck._values.as_float` for R's `as.numeric()` of one value. `causal_relations()` reads the Space's replies as plain dicts and lists.
- **Smaller:** `text/json_expand.py` is 199 lines, down from 1,235.

### Changed: code files are decoded with chardet, not a port of readr's ICU and vroom pipeline

- **Changed:** `code_read()` (and so `code_check()`, `reproducibility_check()` and the other readers of code files) decodes a file's bytes in `codecheck/_decode.py`: a byte order mark picks UTF-8, UTF-16 or UTF-32; valid UTF-8 is read as UTF-8; anything else is read in the encoding chardet names when it is fairly sure (confidence 0.2), else as Windows-1252, and bytes that fit neither show as `<xx>`. Lines end at LF, CRLF or CR (D74).
- **Removed:** the ports of ICU's charset detector, vroom's line indexer, glibc's iconv tables and base `readLines()` (3,864 lines), with the R-fidelity corpus that pinned them.
- **Result changes to watch:** UTF-8 and ASCII scripts read exactly as before. Hebrew (ISO-8859-8, Windows-1255) and UTF-32 files are now read as their text where R showed `<xx>` bytes; files mixing CR with CRLF line ends keep every line; a binary file gives lines of text instead of an error.
- **Dependency:** chardet (0BSD, pure Python) is a core dependency. The `charset` extra stays as an empty alias so `metacheck[charset]` still installs.

### Changed: a .qmd report holds its tables as HTML

- **Changed:** `report(output_format="qmd")`, `report_qmd()`, `module_report()` and `scroll_table_qmd()` write each table as a raw HTML block (the table `report_table()` renders: the same cells, column widths, page size and `column` layout), not as metacheck's R chunk that rebuilds the table as R code and calls `metacheck::report_table()` (D75). A `.qmd` report with tables ends with one HTML block of their styles and pagination script, so Quarto renders it without R or metacheck. `report_qmd()`'s `tables` argument takes only `"html"` (the default); `"r"` is an error.
- **Changed:** a module report that contains a table always folds under "View detailed feedback" in the HTML and `.qmd` reports. metacheck measured the table's R code against its 300-character limit, so the smallest tables stayed open; on the 151 outputs with a table in the accuracy corpus nothing changes.
- **Removed:** `metacheck.report.render.deparse()`, the port of R's `deparse()` (with its Unicode tables) that wrote the chunks. Its parity cases go with it; the `scroll_table` cases are marked `deliberate` (D75). The "unused argument" messages and RegCheck's list cells, which quoted values with it, use a one-line R literal (`metacheck._r.base.r_literal`) and read as before.

### Internal: one `as.character()`, `as.numeric()`, `as.integer()` and `trimws()`

- **Changed:** private copies of R's coercions whose rules matched the shared helpers are gone: 9 `as.character()` copies (`_chr`, `_chr1`, `_cell_chr`) use `metacheck._values.as_str`; 7 `as.numeric()` copies (`_r_as_numeric` in `datacheck.files` and `statout.r_output`, `_as_numeric` in `statout.spv` and `repro.core`, `_num` in `archives.github`, the bodies of `datacheck`'s `as_numeric_str` and `archives.download`'s `_num`) use `as_float`; 3 `as.integer()` copies (`_as_int`, `_int`) use `as_int`; 3 `trimws()` copies (`_trim`, `_trimws`) and 2 `paste(collapse =)` copies use `metacheck._r.trimws` and `paste`. The copies left are the ones whose rules differ: R's 32-bit integer range, `NA` as NaN or as `"NA"`, a warning on coercion, list unwrapping, `str()` instead of R's number formatting, dates and times, other whitespace sets.
- **Unchanged:** results on real inputs. The shared helpers read a few malformed values differently: a hexadecimal number without digits (`"0x."`) is `NA`, an infinite GitHub rate-limit header or R-output line number, and a Grobid year that is not a number, are `NA` instead of an error, a decimal string is truncated as `as.integer()` does, and a container is `NA` rather than its Python text.

### Internal: one missing-value test and one `isTRUE()`

- **Changed:** the 25 private copies of `_is_na`/`_is_missing`/`_isna` and the 4 copies of `_is_true` that matched the shared helpers are gone; their callers use `metacheck._values.is_missing` and `is_true`. Code that imported `is_na` from `metacheck._r` uses `is_missing` from `metacheck._values` under its own name (`metacheck._r.is_na` stays as an alias). The copies left are the ones whose rules differ (R's `%in% TRUE`, `x$name` partial matching, a `None` that is `NULL` and not `NA`).
- **Unchanged:** results. The only values the shared test reads differently are a numpy NaN of a narrower float type, `NaT` and `pd.NA` in the few copies that missed them, which now count as missing as they do everywhere else.

### Changed: the bibr extra requires bibr 0.6.0

- **Changed:** `metacheck[bibr]` (and so `metacheck[all]`) requires bibr 0.6.0 or later, the first bibr release on PyPI that writes export schema 12.x (it writes 12.1). It allowed bibr 0.5.1, which writes 11.0, so `pc.read("paper.pdf")` stopped with the error that names the schema.
- **Comes with it:** bibr 0.6.0 depends on pyarrow, so the extra now installs it, and with pyarrow pandas keeps text in Arrow by default. Arrow cannot hold the lone surrogates that stand for the bytes of a file name that is not valid UTF-8, so `check_file_naming()`, `file_category()` (and `data_classify_files()` through it), the file listing of the data package checks and `llm()` given bytes raised `UnicodeEncodeError` on such names when pyarrow was installed. Those columns are now Python-backed strings, as data_check's raw text already was. Without pyarrow nothing changes.

### Changed: LLM calls go through the official provider SDKs

- **Changed:** `llm()` sends its requests with the official SDKs of the providers (`openai`, `anthropic` and `google-genai`), not with a hand-written port of ellmer (D73). The SDKs are the optional extra `metacheck[llm]` (also in `metacheck[all]`; the one-command installer installs it) and are imported when a model is first called, so `import metacheck` loads none of them. Without them a call gives an error row that says how to install them. OpenAI-compatible providers (Groq, DeepSeek, Mistral, OpenRouter, Hugging Face, Perplexity, Portkey, Cloudflare, Azure OpenAI, Ollama, LM Studio, vLLM) go through the `openai` SDK's chat completions, `openai/...` models through its Responses API.
- **Unchanged:** the arguments of `llm()` (without `capture_reasoning`), the options and their defaults (temperature 0, `max_tokens` 4096, five attempts on a structured reply that is not valid JSON, `llm_max_calls` 30, `llm_timeout` 180 s), the result columns, the `.error` and `.error_msg` columns, the warnings, the fallback of `power`, and the reasoning-effort settings.
- **Result changes to watch:** request bodies are the SDKs', no longer ellmer's byte for byte, so a model can answer differently from before; `power` on Gemini still sends its schema in `responseSchema`, as ellmer does (through the request body the SDK merges in, because the SDK's typed `response_schema` refuses a `null` among the values of an enum; `responseJsonSchema` made `power` return an extra all-null row and was not used). `scripts/llm_power_before_after.py` and `scripts/llm_power_compare.py` measure it on a set of papers.
- **Removed:** `llm_model_list()` and the model listings, the bundled price table, the Ollama-native chat path (Ollama works through its OpenAI-compatible `/v1` endpoint), and reasoning capture (`capture_reasoning`, the `.reasoning` column). A parameter an SDK does not take is dropped with a warning (`seed` on Anthropic and on OpenAI's Responses API).
- **Cache:** the LLM cache is a folder `json` of JSON files named by a SHA-256 of what was asked. R's `.rds` entries in the cache folder are not read, written or deleted, so a cache filled by metacheck is not used.
- **Fixed with it:** `GOOGLE_API_KEY` and `GEMINI_API_KEY` both mean Gemini: either one picks Gemini as the default model and either one is sent as its key (`GOOGLE_API_KEY` first, as in the Google SDK).
- **Smaller:** `src/metacheck/llm/` is 2,621 lines of Python, down from 6,315 (and the 1,027-line price table is gone).

### Changed: remote zips are read with zipfile, and downloads share one retry stack

- **Changed:** `zip_peek()` and the member fetches of `download_repo_files()` read a remote zip with Python's `zipfile` over HTTP range requests. metacheck's own reader of the zip format (the byte parsing, the inflating and the CRC code, with their tests and parity cases) is removed. Archives that could not be listed now are: Zip64, a central directory beyond the first 128 KB, a host that answers HEAD without a length. bzip2 and lzma members are fetched. In 30 real Zenodo zips (one after the other, cache on) 30 are listed instead of 23, with 65 requests instead of 69; the 23 that were listed before cost the same and list the same files (D65). A member costs one range request, none when the archive came whole with its listing (D67).
- **Changed:** a local archive is extracted with `zipfile`. A member it cannot read is skipped with the usual warning and the rest is still extracted; R's `unzip()` stops at the first such member and drops the rest (D66).
- **Changed:** every storage request goes through `metacheck.http.request()`: file downloads, whole-archive downloads (OSF, Zenodo, GitHub, GitLab, Dryad), the zip probes and the Zenodo uploads. The copies of the retry, redirect and rate-limit loop in `archives/download.py`, `osf.py`, `zenodo.py` and `zenodo_upload.py` are gone. Downloads and probes get 5 tries instead of 3 (the Zenodo upload keeps the 3 tries of its own policy), with a random backoff of 1 to 2^n seconds (at most 60) instead of 2^n (at most 30). A host's rate-limit reset is waited for (and said so, from 5 s) for every request to it; with `skip_on_api_limit` any 429 ends the request at once (a plain 429 used to be retried), and a file asked for after the host's limit is known is not requested (D68). A whole-archive download retries a 429 or a 5xx (it retried one confirmed 429).
- **Unchanged:** the OSF and Zenodo token goes on to the storage host a redirect leads to, and no other host's token does (tested both ways); downloads are written to a temporary file and moved into place; progress messages. The disk cache of zip listings is version 2: a listing of an archive that could not be read before is made again.
- **Not changed yet:** the single-try streaming downloads of Dataverse and ResearchBox do not retry, as in metacheck.

### Changed: reproducibility_check runs the paper's code in Docker by default

- **Changed:** `reproducibility_check(execute=True)` now runs the paper's R code in a Docker container: `sandbox` defaults to `"docker"` (metacheck's default is `"process"`, which runs the code on your own machine, D62). In the run phase the container has no network, a read-only root file system, a throwaway sandbox directory, a non-root user and no capabilities. Without Docker, `execute=True` stops with an error that says why and how to go on, and nothing is run; it never falls back to your machine.
- **To run on your machine:** pass `sandbox="process"`. It works as before, and each run warns (`PytacheckWarning`) that nothing is isolated, so use it only for code you trust. A caller that passes metacheck's own default `c("process", "docker")` now gets a `ValueError`: name the sandbox you want.
- **Fixed with it:** the sandbox directory is writable from the container, so the paper's code could leave a symbolic link in it that named a file on your machine, and the host followed that link when it wrote the next runner script (the file was overwritten) or read the next script (its text went into the report). After every container, links that leave the sandbox directory, and named pipes, are now removed. This applies to `sandbox="docker"` only; with `sandbox="process"` the code runs as you anyway. The report also shows the code's output in a fence that its own output cannot close (a longer fence is used when the output holds a run of four or more backticks), so output cannot add a `{r}` chunk that Quarto would run when the report is rendered.
- **Fixed after the first merge:** the container ran as a fixed user 1000:1000, which cannot enter the private sandbox directory of a Linux host user with another uid (a GitHub runner is 1001), so every script failed with "Permission denied". On Linux the container now runs as your own uid and gid; a root host and Docker Desktop use 1000:1000, and for root the sandbox is handed to that user first. Rootless Docker is not verified.
- **Unchanged:** the static phase (`execute=False`) needs no Docker and runs nothing. The install phase (only with `install_missing=True`) still has network access, by design, and sees only the install script and the package library.

### Changed: delimited files are read with pandas, not with a port of fread

- **Changed:** the maintainer decided (2026-10-04) to stop reproducing R libraries by hand where Python libraries do the job. The 1,937 lines that ported `data.table::fread()` and the `read.table()` tokenizer are gone. `.csv`, `.tsv` and `.txt` files are read with pandas' C parser (`datacheck/_files_delim.py`, about 540 lines), which splits the fields; a small layer keeps what data_check relies on: finding the table (the first run of two or more lines with the same field count, a single column otherwise), the blanks around unquoted fields, `NA` strings, the type ladder (logical, integer, 64-bit integer, double, date, timestamp, text) and the head-only reads with their size limits. The sniffers for the separator and the header, the encoding handling and `data_read_head()` keep their rules, except that the separator sniffer now counts candidates outside quoted text. The strtod helpers the column checks use moved to `datacheck/_files_strtod.py`.
- **Same result on 364 of the 382 delimited files** in the repository (fixtures, parity inputs and the upstream test files): the same columns, types, missing and unique counts, traffic light and message from data_check. A 100,000-row CSV is read in 0.36 s instead of 0.83 s and a head read of it in 6 ms instead of 31 ms; a small file takes about 0.6 ms longer (the 382 files take 0.78 s instead of 0.54 s).
- **Fixed:** a doubled quote inside a quoted field is one quote (`"he said ""hi"""` is `he said "hi"`), where fread keeps both (U216). A UTF-16 file (E-Prime exports and other Windows files) is decoded, for the separator and header sniffers as well as the reader, where R reads its bytes and finds no rows; a tab-separated UTF-16 `.txt` is now classified as data (U217).
- **Differs on purpose:** quotes that are not CSV quoting are read by a local rule, and R's four whole-file quote rules are not reproduced (D69): a quote opens a quoted field only at the start of a field, and one that is followed by text before the separator (`"a"b`, JSON text saved as `.csv`), or never closes, is an ordinary character, so one bad quote no longer changes how the rest of the file is read; where such a quote would end the table, the quoted part is also tried closed (as pandas reads it) and kept if it reads more rows at the same width. A file of blanks is empty, and a file with no run of lines of the same width is one column (D70); a tab that follows a separator in a text column is stripped like a space (D71); a `.tsv` file with no tab outside quoted text in its first 100 lines, which another separator splits evenly, is read with that separator, where R reads every `.tsv` with a tab (D72). The parity cases that show it are marked and locked.
- **Removed with it:** the fread fuzz tests and the tests that pinned the internals of the two modules. The behaviour is kept by tests that compare the reader with recorded fread output (`tests/datacheck_files/test_delim.py`) and by `tests/datacheck_files/test_delim_reader.py`.

### Upstream sync: metacheck dev 49f97ec5 (metacheck #424-#452)

The R reference is metacheck `dev` at 49f97ec5 merged into pull request #423 (the bibr export schema 12.0 work this version already follows). Ported:

- **Zip archives and downloads:**
  - `zip_peek()` lists zips on hosts that refuse HEAD (S3-backed storage) with ranged requests, and `zip_peek(cache = TRUE)` keeps listings on disk; new `zip_peek_cache_clear()`. A failed peek that may pass later is not cached (U210).
  - `download_repo_files()` downloads OSF, Zenodo and Dataverse repositories file by file. GitHub, GitLab and Dryad archives need listed sizes and are cut at twice `max_download_size`. Archive members that fail to download are reported with the reason, in `attr(, "failed")` of the `*_file_download()` functions too.
  - `osf_file_download(mode = "zip")` keeps the whole project within `max_download_size`: an archive is taken only while its listed files fit, its download stops past the budget, and the rest is downloaded file by file, smallest first; files left out are `attempted = FALSE` (metacheck stops on this path: U209).
- **data_check:** `peek_zips` is on by default: a downloaded zip is unpacked and its files are classified and listed as their own rows. `cache` and `skip_on_api_limit` are passed on to `repo_check` and to the zip peek. GIS (`.shp .dbf .shx .prj .sbn .sbx .cpg .gpkg`), phylogenetic (`.nex .nwk .tre .phy`), mass-spectrometry (`.mzxml .mztab`) and `.ply` files are data by their extension; `.tab` and `.table` are readable plain-text tables.
- **stat_effect_size:** Hedges' g (`g`, `Hedges' g`, `gs`) is checked for coherence like Cohen's d. `gav`, `gz` and `grm` count as reported effect sizes (U212).
- **code_check:** R package-list variables (`pkgs <- c(...)`, loop variables, `character.only`, `lapply(pkgs, library)`, `p_load(char = )`) are resolved to the names they hold or dropped; their own names are no longer reported or installed as packages. Empty code files are checked like any other file. `cache` and `skip_on_api_limit` reach the zip peek, and `skip_on_api_limit` reaches `repo_check`.
- **reproducibility_check:** a package CRAN cannot find is reported as unavailable, with `install.packages()`'s own warning, instead of "installed but not loadable"; a 404 on the CRAN Archive listing is "package not found in the CRAN Archive" and no longer counts as a network failure (the Docker install script does the same). `peek_zips` defaults to TRUE.
- **repo_check:** new `skip_on_api_limit`; `cache` and `skip_on_api_limit` also apply to zip peeking. A DSpace 7 repository gets its doi and licence row in `repo_metadata`.
- **Repository hosts:**
  - 4TU.ResearchData: articles cited by a uuid DOI are looked up, and downloaded, by the numeric id the DOI resolves to.
  - Dataverse: phys-techsciences.datastations.nl is added; a DOI prefix shared by several installations (10.17026, the DANS Data Stations) is resolved through doi.org by the resolved host name (U211).
  - DSpace 7: `dspace7_links()` finds DOI mentions that resolve to a DSpace 7 repository; `dspace7_file_download()` carries the item's doi and licence and returns an empty listing for an item without files.
  - DataONE: KNB `#view/` and `catalog/view/` URLs and bare `knb.<n>.<rev>` package ids are recognised; files listed without a `<physical>` description are included, with their size from the member node.
  - Figshare: collections (URL or `10.6084/m9.figshare.c.` DOI) expand into their articles; institutional DOIs with two sub-prefix segments give their article id.
  - OSF: the token check at start-up gives up after 5 seconds.
- `cap_report()` keeps its name although metacheck made it internal (D61).
- Metacheck fixed bugs that this version had marked: Dataverse DOI routing (U32) and empty code files (U67, U87); those cases match R again.
- Fixed while porting (metacheck has these too):
  - Files unpacked from an archive take their own extension's type (`data`, `code`, `text`) instead of the archive's (U213).
  - An archive in `local_path` is no longer unpacked into the user's folder: metacheck writes `<archive>.contents/` next to it, and the next run lists every unpacked file twice. pytacheck unpacks it to `metacheck-archives/` in the temporary folder (U214).
- `download_repo_files()` no longer fails when a file table read as text has a missing `repo_url` and a GitHub, GitLab or Dryad archive is still to be downloaded.
- A zip whose HEAD request failed for a passing reason (429, 5xx, no connection) is not cached on disk as unlistable (U210), and zip-listing cache entries are written under unique temporary names, so two threads cannot interleave one.

### Parity tooling

- `python -m parity generate --jobs N` runs the case files' R sessions side by side (`0`: one per CPU). A full regeneration took about 6 minutes instead of about 45; the parity and upstream-sync workflows use it. `generate` warns when the R sessions leave files in the checkout outside the goldens.

### Fixed

- The default LLM model (the first provider whose API key is set) is now set when metacheck loads, as R's `.onLoad()` does, not when the LLM code is first imported. Before, a module run before and after that import used different session-cache keys.

### Added: data package checks

- The `datapackage` pack, in every install, checks the folder or archive of data, code and documentation that comes with a paper: `datapackage::package_files`, `datapackage::package_structure` and `datapackage::package_docs` (and `package_pii`, below), and the preset `datapackage::default`, which adds `metacheck::data_check` and `metacheck::codebook_check`. Each check also returns a checklist (item, title, status `fail`, `warn`, `manual`, `pass` or `na`, detail) for a data steward to work down. A pack can supply its own policy as a preset.
- `datapackage::package_pii` looks for personal data by value in the data files of a package (csv, tsv, Excel, ODS, SPSS, Stata, SAS; the first 5000 rows of each): Dutch citizen service numbers (BSN, with the 11-check), student numbers (only in a column named like one), phone numbers (Dutch and international), IPv6 addresses, Dutch postcodes and columns of people's names (the column name says so and the values read like names). It is in `datapackage::default`, has one checklist item for each kind (`pii_bsn`, `pii_student_number`, `pii_phone`, `pii_ipv6`, `pii_postcode`, `pii_name`) and `pii_scan` for files that were not read, and an extra `hits` table. It reports files, columns and counts, never a value, and a hit is a hint for a person (status `manual`). It does not change `metacheck::data_check`, the port of R's checks. Details and limits: docs/DATAPACKAGE.md.
- `metacheck package PATH` (also `pytacheck package`) runs them on a folder, or on a zip or tar archive extracted to a temporary folder that is removed afterwards, without a paper. It takes `-m`, `--preset`, `-a`, `--offline`, `--record` and `--json` like `run`, and `-o FILE` (or `-f html|qmd|md`) writes a report titled with the package's name. A path that cannot be opened exits with status 2.
- In Python: `metacheck.datapackage.check_package()` and `report_package()`. Modules that take `local_path` and `local_only` are given the package's folder and `local_only=True`, so nothing is looked up online; the run record has a new `package` key (name, source, archive) in place of a paper. Everything runs on your machine; the one download is `data_check`'s concept model (see "a local concept classifier" below), which `-a concepts=rules` skips. Guide: docs/DATAPACKAGE.md.
- `datapackage::package_readme` drafts a README for a package, by rules and without an LLM, and hands it back as a file. It keeps the author's README text character for character and adds only what is missing: a section the README lacks (in the template's order, in the heading style the README uses), the text under an empty heading, the file list (a folder tree, one line for each top-level folder and file, the formats with counts, the naming convention when it is evident), the variables of the tabular data files (names, types and number of empty cells, never a value) and the licence. What only a person knows is a `<Describe ...>` placeholder in the syntax `package_docs` already flags, so running that check on the finished draft lists every gap. The sections and headings come from the README template data (the `readme` option; sections may have a `prompt` and a `fill`, and the template a `prompts` map). It is not in `datapackage::default`: select it with `-m datapackage::package_readme`. The package is never written to. Details and limits: docs/DATAPACKAGE.md.
- **A module can hand back files.** A module result's `files` key (file name to `str` or `bytes`, a plain name with no folder) is collected by `module_files()`, exposed as `.files` on the result of `check_package()`, `report_package()` and `run_modules()`, and written by `write_module_files()` (`metacheck.module`). `metacheck package --files-dir DIR` writes every returned file into `DIR`, which is created; it refuses a folder that is the package or inside it, and an existing file unless `--force` is given (exit status 2). `-o` still means the report. A `files` result that is not a dict (a table, say) is left alone; a dict whose names or contents are not usable fails that module with a message that says why. Guide: docs/MODULES.md and docs/DATAPACKAGE.md.
- `datapackage::package_docs` can decide "the research involved people" from the paper's abstract, **opt-in** (`abstract_lookup`, `paper_doi`; on the command, `metacheck package --abstract-lookup` and `--paper-doi DOI`). With a DOI (given, or the DOI on the README's line for the publication) it fetches the abstract and nothing else, from Crossref or else OpenAlex, runs the live-data detection of `ethics_check` on it, and on a hit sets `human_participants` so the ethical approval and informed consent rows become required. It never decides "no": an abstract without participants, a record without an abstract, a bad DOI, no network, a timeout and `--offline` all leave the rows to a person (`manual`) as before, with one line that says why (`human_participants_note`, `human_participants_source`, `abstract_decision`, and the row's detail). The result keeps the decision and the one sentence that matched, not the abstract. Off by default: with no switch no request is made. Only the DOI leaves the machine (and the User-Agent every request carries); no file name or README text is sent. The module does not declare `requires=["network"]`, so `--offline` keeps it and skips only the lookup. Details: docs/DATAPACKAGE.md.

- The local app has a second page, "Check a data package" (`/package`; `metacheck-app --page package` opens it, also in an app that is already running). Name a folder or upload a zip (up to 1 GB), choose a preset and run: it shows one row per check and the checklist, coloured by traffic light, and offers the report to download. The presets listed are the ones that run a `datapackage::` check, found through the pack registry, so a pack's own policy preset appears once the pack is installed and the app is started again. Everything runs on the computer that runs the app. An uploaded archive is unpacked with the guards of `check_package()` and lower limits (5 GB, 100,000 files). The page reads the folder you type, which the paper page never does (it reads only uploads), so it reads only inside your home folder: the path is resolved first, links are followed, and the folder that results must be inside home, or inside a folder of the new setting `METACHECK_APP_ROOTS` (separated like a `PATH`). Otherwise anything that can reach the local app with its token could make it read any folder you can. The hosted app (`--hosted`) has no such page.
- On that page the local classifier for the concepts of data columns is on by default, as in `metacheck package`. Without the `concepts` extra, which the installer does not add, the page uses rules only and says so under the results, with the command to install the extra; untick the box to download nothing. `check_package_source()` in `metacheck.app.package` takes `local_classifier=True` by default.
- `check_package()` and `report_package()` take `max_bytes` and `max_files` to lower the limits on an extracted archive.
- The installers take `--steward` (`-Steward` on Windows, or `METACHECK_STEWARD=1`): the same install, and the app opens on the data package page. It does so only if the installed app knows `--page`; the pinned commit is not moved by this change, so until it is, a steward install opens the paper page.

### The import package is metacheck

- **Changed:** the import package is now `metacheck` (`src/metacheck`). `import pytacheck` and the `pytacheck` command keep working: every `pytacheck.<sub>` is the same module object as `metacheck.<sub>`, and type checkers see the public names through stubs. Packs may declare `requires.metacheck`; use `>=0.4.0a2.dev0` if the pack does `import metacheck`, because 0.4.0a1 has no `metacheck` import package. `requires.pytacheck` is still read, and `pack check` warns if both are given. The pack scanner treats `pytacheck.*` and `metacheck.*` alike. Saved tables and the repo-info cache keep their format, so files move between this version and 0.4.0a1 both ways (until the change below). Not renamed yet: the store id, the logger names (records still carry `pytacheck`), the install record and pack module names, the command's help name and the version line (the folders keep their name; see below).
- **Things that do change:** warnings now come from `metacheck.*` modules, so a warning filter that matches `module="pytacheck..."` needs `metacheck` instead. `importlib.resources.files("pytacheck")`, `pytacheck.__file__` and `pytacheck.__path__` point at the small alias folder; use `files("metacheck")` or a subpackage such as `files("pytacheck.resources")`. Messages that name a module or a file of the package name it under `metacheck` (for example `metacheck.pack_install()`, `metacheck/resources/...` in `status --check`, and `module 'metacheck.io' has no attribute ...`).
- **Upgrading an old git install:** run `pip uninstall pytacheck` first. An install from before the distribution was renamed owns the same `pytacheck/` folder. **In a git checkout** that has been on an older commit, run `git clean -fdX src/pytacheck` once to remove the old bytecode folders.

### Settings and saved files use the metacheck name

- **Environment variables:** every `PYTACHECK_*` variable now has a `METACHECK_*` name, which is read first; the `PYTACHECK_*` names keep working unchanged. If both are set to different values, one line says which is used (the value is never shown). Two exceptions: `PYTACHECK_LLM_CACHE_DIR` still comes before `METACHECK_LLM_CACHE_DIR`, which is R metacheck's name for the same shared cache; `METACHECK_LLM_MODEL` and `METACHECK_LLM_MAX_CALLS` keep their meaning. **Changed:** `METACHECK_EMAIL` is now read before `PYTACHECK_EMAIL`. A variable set to spaces only now counts as unset; this changes the setting for `*_CACHE_DIR`, `*_DATA_DIR`, `*_LOG`, `*_RSCRIPT` and `*_LLM_CACHE_DIR`, `*_NO_SLEEP` (sleeps stay on), `*_EMAIL`, `*_STORE_URL`, `*_R_PARSER`, `*_R_SERIALIZE_VERSION` (which raised an error before) and `METACHECK_LLM_MODEL`/`METACHECK_LLM_MAX_CALLS` (the default is used). The `PYTACHECK_*` names are deprecated but stay until an end date is published in advance, except `PYTACHECK_LLM_CACHE_DIR`, which is the only way to give this package an LLM cache of its own and is not deprecated. The full list and order: docs/ENVIRONMENT.md.
- **Folders:** unchanged. Settings, the saved bibr key, trusted folders, installed packs, store indexes, refreshed databases, caches and the log stay in the `pytacheck` folders (for example `~/.config/pytacheck` and `~/.local/share/pytacheck` on Linux), which this version and 0.4.0a1 both use. They are separate from R metacheck's folders. `METACHECK_CONFIG`, `METACHECK_DATA_DIR`, `METACHECK_CACHE_DIR` and `METACHECK_LOG` now override them, ahead of the `PYTACHECK_*` names, which work as before.
- **Project file:** `metacheck.json` is read as the project file; an existing `pytacheck.json` still works and is edited in place. If a folder has both, `metacheck.json` is used. A `metacheck.json` that is not a settings file (for example saved `--json` results) is passed over with a warning, and an empty one (as a shell redirect creates it) is passed over silently. The same safety rules apply to both: a project file cannot add stores, and the local code it names runs only once you trust it. Older versions do not read `metacheck.json`.
- **Run records** are now `metacheck.run/2`, with the keys `version` (this package's version) and `r_reference` (the R commit it is compared against) in place of `pytacheck` and `metacheck`. Records written by earlier versions still open and replay, and a record written again carries the new schema. Older versions cannot read the new records. Code that checks `schema == RUN_SCHEMA` should use `schema in RUN_SCHEMAS`, which also holds the old id. In Python, `RunRecord.version` and `RunRecord.r_reference` are the new fields; `.pytacheck` and `.metacheck` still read them, but a `RunRecord` can no longer be built with the keywords `pytacheck=` or `metacheck=`.
- **Saved module tables and repo-info cache entries** are written under `metacheck.*` format ids and still read under the old ones. Older versions treat the new files as not saved: they compute those papers again, fetch those repository listings again, and their `collect_module_tables()` leaves those papers out. (This replaces the note above that these files move both ways between this version and 0.4.0a1.)
- **Options:** `metacheck.careless`, `metacheck.r_serialize_version` and `metacheck.llm.workers`; the `pytacheck.*` spellings are the same options.
- **Logging:** handlers on the `metacheck` logger now receive the package's records; the records are still named `pytacheck` and `pytacheck.api`, so existing logging setups keep working.
- **Kept:** the `pytacheck` folder names; installed packs keep their `.pytacheck-install.json` record, and pack modules keep their internal `pytacheck_packs` names, so packs installed by either version stay valid in the other.

### Added: a local concept classifier for data_check

- `data_check` fills the column concepts its rules leave blank with a local,
  multilingual classifier (XLM-RoBERTa-large fine-tuned on 108k LLM-labelled
  columns, ONNX with 8-bit weights, ~840 MB, downloaded once from the Hugging
  Face Hub), instead of leaving them to the LLM tier (D33). It runs offline in
  ~0.1 s per column on eight CPU threads and ~2 GB of memory; held out, it scores
  F1 0.795 on ResearchBox and 0.736-0.761 on OSF/GitHub/Zenodo repositories,
  against 0.784 / 0.740 for Muse Spark 1.3 on metacheck's prompt. Install it with
  `pip install "metacheck[concepts]"` (also in `[all]`); without it `data_check` behaves as metacheck.
- `concepts=` (option `metacheck.concepts`, `METACHECK_CONCEPTS`) picks the tier:
  `"classifier"` (default), `"cascade"` (the columns the classifier is least
  sure of go to the LLM under `llm_use(TRUE)`; threshold
  `metacheck.concepts.threshold` / `METACHECK_CONCEPT_THRESHOLD`, default 0.92),
  `"llm"` (metacheck's behaviour) or `"rules"`. `metacheck.concepts.model` /
  `METACHECK_CONCEPT_MODEL` loads another model (a directory or `repo@revision`), and
  `METACHECK_CONCEPT_THREADS` sets its threads. These are new, so they have only
  `METACHECK_*` names, and the options have no `pytacheck.*` spelling.

### bibr server client

- `convert_bibr(backend="bibr")` and `convert()` (with `BIBR_URL` and `BIBR_API_KEY`) use bibr serve's job API and the hosted service in front of it: submit to `/papers/jobs`, poll, fetch the result, honour `Retry-After` on a 429 (bounded), read a 409 on the result as not ready, explain 401/403/413/415, and never send the token over plain http except to localhost or across a redirect. `"selfhosted"` now sends the token and waits out a 429; the readiness check accepts an anonymous bibr serve (docs/BIBR.md section 3, docs/UPSTREAM_ISSUES.md D59).

### Faster pattern scans: required literals

- `metacheck._r.regex.required_literals(pattern, perl, icase)` reads a TRE or PCRE pattern and returns the words every match must contain (in casefolded text); `detect_many(patterns, text)` folds the text once, skips the patterns whose words are not in it, compiles only the others, and gives the same truth values as `grepl()` pattern by pattern. A pattern the reader does not fully understand has no required words and always runs, so results do not change. The dictionary scans of the codebook modules (791 scale and 833 task patterns per paper) use it: on a 94,000-character text about a third faster once the patterns are compiled. `METACHECK_LITERALS=off` (or `PYTACHECK_LITERALS=off`) turns the new filter off, to rule it out when a match seems to be missing. `grepl()`'s own filter is unchanged. Tests: every match in the recorded regex calls (92,767) has its required words; a generated-pattern test (TRE and PCRE, case on and off, tricky characters such as the Kelvin sign, the long s and no-break space) finds no violation; 229 of the 273 built-in patterns get at least one required word.
- `detect_many(patterns, text, ..., literals="auto")` looks the words up in an index of the text's distinct words when it has 64 patterns or more (a few patterns still scan the text, since building the index costs about 2 ms on a 94,000-character paper); `literals="scan"` and `literals="index"` choose either way. The answers are exactly the scan's: a word of printable ASCII without a space can only sit inside one whitespace-separated word of the text, so searching the joined distinct words finds it wherever it lies in a word (`ann` in `planning`, `ab.cd`), and any other string is looked up in the text itself. On the codebook scan (scales and tasks, 1,624 patterns, a 94,000-character paper) the index makes `detect_many` about 20-27% faster than the scan (best of 15 runs 92 ms against 114 ms, median 110 ms against 150 ms; a first call in a fresh process 234 ms against 279 ms). `METACHECK_LITERALS=off` turns off both.

### Text search on an indexed paper

- `text_search()` (every `return_` mode, lists of patterns, `exclude`, `search_header`), `extract_eq()`, `extract_urls()`, `extract_p_values()`, `text_expand()` and the one-paper `paper_table()` now work on an index of the paper's sentences (`metacheck.core`), built once per paper and kept on it: each pattern is tested on each sentence at most once, however many searches ask, and a sentence whose text lacks a word the pattern needs (`required_literals`) is skipped without running the regex. The results do not change; the code before is kept in `tests/_legacy/`, and the tests compare against it (search chains generated at random, every return mode, paper lists, tables and strings, and every built-in pattern on the accuracy papers against the regex itself).
- A paper read from Grobid XML or JSON keeps its tables as JSON records, and its index is reused for as long as it does. Once a table is a DataFrame, it may have been edited in place, so the index is built again for each search, except inside `run_modules()` and `report_module_run()`, which build it once per paper per run. `run_session()` is unchanged: it keeps module results, not indexes. `read()` builds the index while it reads the XML and finds the paper's equations with it. `paper_table()` of one paper no longer turns the paper's JSON records into a DataFrame.
- **Checking that modules do not edit papers:** with `METACHECK_CHECK_MUTATION=1` (set in CI), `run_modules()` and `report()` raise `StaleDocumentError` before the next module when a module edited a paper's text or section table in place, naming the module; without it nothing changes. Modules must copy a paper before changing it (docs/MODULES.md).
- The funding module takes the words a pattern needs from `required_literals()` instead of its own pattern reader, and `ethics_check` no longer filters sentences with its own list of words first: its 63 patterns are filtered by their own literals, and its two searches share one index of the paper. `text_search()` accepts such an index as its first argument. Results are unchanged. The hand-kept word filters of the `*_links()` functions of the archives stay, since a scan for each of their many words over a whole paper list was slower than they are.

### Changed: papers that share an ID, and a paragraph repeated word for word

- **Changed: papers with the same ID in one list are checked as separate papers.** metacheck leaves a repeated `paper_id` to each function, with different results on the same list: some modules stop, some pool the papers' sentences or references into one, and the summary repeats rows. Now the first paper keeps its ID, a later one is renamed `ID~2`, `ID~3`, ... (a copy; your papers keep their IDs), and one warning (`PytacheckWarning`) names them. Every module, `paper_table()`, `text_search()`, `module_run()` and `report()` then see distinct papers: a module gives the same numbers as running the papers one by one, and `report()` of such a list writes one file per paper. On the 21 accuracy papers read as one list (three IDs are shared by six papers), for example: `ethics_check` says "6 of 21 papers" where it said "6 of 18", `stat_p_exact` finds 469 p-values where it found 477, `stat_effect_size` 428 rows where it found 446, and `ref_consistency` returns a table where it stopped. A single paper, and a list with distinct IDs, give the same results as before. Register entry: U208 in `docs/UPSTREAM_ISSUES.md`.
- **Changed: a paragraph repeated word for word counts once.** The p-value search (`extract_p_values()`, so `all_p_values`, `stat_p_exact` and `stat_p_nonsig`) counted a sentence that appears twice in a paper, with every cell equal, twice; every other search already returned it once, as metacheck's `unique()` does. Now it counts once. Only an exact repeat counts: a different text id or paragraph, different whitespace, punctuation or case make a different row, and a match repeated inside one sentence still counts each time. A table or strings given to `text_search()` are searched as they are. None of the 21 accuracy papers repeats a sentence, so their results do not change. `stat_p_exact` and `stat_p_nonsig` are validated checks: this change and the one above wait for the maintainer's OK on the pull request (D60 and U208 in `docs/UPSTREAM_ISSUES.md`).
- `validate()` makes one test paper for each distinct ID of its ground truth (metacheck made identical papers for a repeated ID).

### The app

- The page has a header with the version and links to ScienceVerse and the Metacheck, Pytacheck and bibr repositories on GitHub, repeated under the credit line. The online and data options sit above the buttons they apply to, the bibr key field is hidden where the server supplies the key (that key wins over a typed one), and result cells are coloured by their traffic light (the word stays, so colour is never the only signal).
- The page starts on the bibr reader where the server supplies the bibr key (`SCIVRS_API_KEY`), as a hosted server does, and on GROBID otherwise, since GROBID needs no key.
- The bibr option talks to bibr serve and the hosted bibr service in front of it: it sends a PDF to `/papers/jobs`. Before, it sent every PDF to the Scienceverse platform's `/jobs`, which those services answer with 404, so the page said the bibr service could not be found. `PYTACHECK_BIBR_BACKEND=scivrs` says that the address in `PYTACHECK_BIBR_URL` is the platform. For an address from metacheck's public server list, the entry's `protocol` decides; without one, an entry whose `api_key` is `SCIVRS_API_KEY` is the platform, and any other entry is bibr serve. A hosted server does not start with a `PYTACHECK_BIBR_BACKEND` other than `bibr` or `scivrs`, or with a `PYTACHECK_BIBR_URL` that cannot work, such as plain http to another machine for bibr (deploy/space/DEPLOY.md).

### Fixed: GitHub and GitLab links of 12.x papers

- `github_links()` and `gitlab_links()`, and so `repo_check`, find the GitHub and GitLab links of papers in the 12.x format: bibr 12.x JSON, and Grobid TEI, which is read as 12.x by default. They searched the url table's first column, which in a 12.x paper is the number `url_id`, so no such repository was found; they now search `href`, as the other repository finders do. metacheck has the same bug (docs/UPSTREAM_ISSUES.md U196). On 450 Psychological Science papers read from Grobid TEI, 9 papers now give 9 GitHub and 1 GitLab repositories, against none before. More repositories found means more calls to api.github.com, which allows 60 an hour without a token.

### Fixed: open_practices on papers from older bibr exports

- `open_practices` stopped with "cannot use 'tuple' as a dict key" on papers read from an older (pre-12) bibr JSON export, whose text table has a list column (`_bbox_2d`): 36 of the first 40 papers of a bibr validation set. The list is now compared by its elements, as dplyr does when it joins on a list column. Results on papers without a list column, and on 12.x papers, do not change.
### Fixed: p-values and statistics that were not read

These change the results of validated checks (`all_p_values`, `stat_p_exact`, `stat_p_nonsig`, `stat_effect_size`); metacheck has the same bugs (docs/UPSTREAM_ISSUES.md U204, U205).

- `extract_p_values()` reads p written as "ps", "p's" or "p-values": "ps < .05", the usual way to report several p-values at once, was not a p-value. In 450 Psychological Science papers read from Grobid TEI it occurs 216 times in 70 papers. `stat_p_exact` counts "ps < .05" as an imprecise p-value, so it turns red on 3 of the 21 papers of the realistic corpus.
- Scientific notation may use "×", leave out "^" and use the Unicode minus: "p = 1.8 × 10 -6" was read as p = 1.8 and is now 1.8e-06.
- `extract_eq()` and `extract_p_values()` read the Unicode minus sign (U+2212), which text from PDFs often has (bibr keeps it, Grobid writes "-"): "t(28) = −2.15" and "d = −0.80" were not found, so `stat_effect_size` saw no t-test in such a sentence. `rhs` and `p_value` now have "-"; the matched `text` is kept as written.

### Fixed: e-mail addresses are not URLs

- `all_urls` and `extract_urls()` no longer list the parts of an e-mail address as URLs: for `k.aristovich@ucl.ac.uk` metacheck lists `k.aristovich` and `ucl.ac.uk`, and `gmail.com` for every address at gmail.com. A URL that has an `@` after its host (`twitter.com/@user`) is still listed. On 120 papers read from bibr 12.x, 274 of 575 rows were such fragments (214 of 835 in 86 older bibr papers, 12 of 2,584 in 450 Grobid TEI papers); a paper whose only matches were e-mail addresses had the traffic light `info` and now has `na` (22 of the 120). This changes the result of a validated module; metacheck has the same bug (docs/UPSTREAM_ISSUES.md U206).

### Fixed: ref_consistency on papers from older bibr exports

- `ref_consistency` counts the citations of an older bibr JSON export (bibr 0.3.0 and the v10 format), which marks them `bib` instead of Grobid's `bibr`. Before, such a paper had no citations: every reference was reported as not cited and the light was red. On 120 platform papers 4,973 of 4,973 references were reported, now 922, and 20 papers are green instead of none (86 older bibr papers: 3,700 to 375, 11 green). A 12.x export and Grobid TEI are unchanged. What the module still reports is mostly bibr's own noise (citations it did not link, author-year styles). metacheck has the same bug (docs/UPSTREAM_ISSUES.md U207).

### Changed: five copies of R library code are replaced by standard tools

The maintainer decided (2026-10-04) to stop maintaining hand-written copies of R libraries. These are the small ones; no module result changes.

- **SPSS fitted lines:** the text of a fit in a `.spv` chart is read with Python's `ast` module and drawn only when it is arithmetic in `x` (numbers, `x`, `+ - * /`, `^`, `exp()`, `log()`, `sqrt()`); nothing is run with `eval`. The 950-line copy of R's expression parser and evaluator is gone. A fit that is not arithmetic in `x` (`TRUE`, `a * x`, `if (x > 0) 1 else 2`, `x %% 2`) used to be listed in the chart's legend, and drawn when R could evaluate it; it is now left out (D63). SPSS writes plain arithmetic: the fits and drawn lines of every `.spv` fixture that stands for a real file are the same as before.
- **Progress bars:** `pb()` draws with `rich.progress` instead of a copy of R's `progress` package. `tick()`, `update()`, `message()` and `terminate()` work as before and the bar is still drawn only on a terminal with `verbose()` on. The layout words of the format (`:bar`, `:eta`, `:elapsedfull`, ...) are not read: a bar with a known total shows a bar, the count and the elapsed time, and one without a total a spinner. Bars open at the same time share one display; ticking a finished bar does nothing.
- **bibr 12.0 files:** `paper_write(schema_version = "12.0")` and `grobid_to_bibr(schema_version = "12.0")` write the file with `orjson` instead of a copy of jsonlite's pretty printer. The text is two-space indented JSON with one array value per line, a double keeps its decimal point or exponent (`100000.0`, `1e-5`), and `</` is not escaped (jsonlite: inline arrays, `100000`, `1e-05`, `<\/`). Every value reads back equal; the one difference is that a double with a whole value (in the paper's `extraction` block: page width and height, timings, diagnostic scores) reads back as a float (`1.0`) where it read back as an integer (`1`). No table column changes type. An integer wider than 64 bits is an error (D64).
- **Corpus files:** `papers_load()` reads `.rds` files with the unserializer of the data checks instead of its own copy. The 8 corpus fixtures read identically, and native-binary and ASCII streams now read too.
- **causal_relations():** the requests send the package's shared `User-Agent` (as every other client does, with the contact e-mail when one is set) instead of `causal_relations/0.1 (R curl/jsonlite)`.

### Changed: code checks read JSON and the parser tables with standard tools

- Jupyter notebooks and `renv.lock` files are read with `metacheck._json.loads`, the one JSON parser, instead of a reader that copied jsonlite's yajl parser. Results are the same on valid JSON, which every real notebook and lock file is. The text yajl accepts but no JSON writer produces is no longer read: `/* */` and `//` comments, vertical-tab and form-feed white space (such a notebook has no cells, as with any other invalid JSON), a `\u0000` escape (the NUL stays in the string) and a lone surrogate escape (it becomes U+FFFD, not `?`); a repeated key keeps its last value, not the first. Of the 39 notebooks and lock files in the repository, 35 give identical results; the 4 that differ were written to hold these quirks. The 13 parity cases of the quirks (`codecheck_review`, tier 2) are marked `c_quirk` and locked.
- The tables of R's parser (the 2,250-line `_rparse_tables.py`) are one compressed file, `_rparse_tables.json.gz`, loaded when the parser first runs; `scripts/gen_rparse_tables.py` rebuilds it from R's `gram.c`. The tables are identical, so `code_parse_r()` messages are unchanged.

### Changed: the data checks use libraries where they used reproductions of R's

- **RTF codebooks are read with `striprtf`** (U215; the `data` extra). Before, four regular expressions stripped the control words, so an RTF codebook reached the LLM as one line that began with the font table's text, with no paragraph breaks and without the characters written as `\'e9`, `\u233` or `\{`. Now paragraphs and tabs stay, the font table and other destination groups are skipped, and the escapes are decoded. Text that is not valid UTF-8 is still an error, as in R. Without the package the codebook is read as raw lines, as any file whose text cannot be extracted is. Only `parse_codebook()` of `.rtf` files changes; no data_check, codebook_check or psychds_check result on the parity cases or fixtures differs.
- **`python-calamine` is no longer a core dependency.** Nothing imported it (the spreadsheet readers use their own `.xlsx` walker and xlrd). `uv.lock` and `install/constraints-app.txt` are regenerated.
- The manifest writer of `manifest_merge()` and the value-label and missing-code writers use `json.dumps`, and the manifest is read with `json.loads` (a UTF-8 byte-order mark in an existing manifest is skipped instead of discarding the manifest). The bytes written are the same as before. The one-line vector writer (`RVector`), which only a test used, is gone.
- `median()` in the data check helpers is `statistics.median`.

### Documentation

- docs/CODEMAP.md maps every metacheck check, exported R function and R package dependency to its Python location, with the parity areas, status labels and register entries (docs/UPSTREAM_ISSUES.md) that apply. `scripts/codemap.py` generates its tables, and `scripts/codemap.py --check` (run by the test suite) fails when they are out of date.

## 0.4.0a1 (first release on PyPI)

First release on PyPI, as `metacheck` (`pip install "metacheck>=0.4.0a1"`). It is a
pre-release. The import name is still `pytacheck` and so is one of the
commands; `metacheck` is the same command, and `python -m pytacheck` works.
Compared against the R package at commit `b239264` (metacheck 0.3.1 at `85c8c87`
plus scienceverse/metacheck#423, which the port targets ahead of its merge).

- The app and the installer: `metacheck-app` and `metacheck[app]`; a one-line
  installer for people who do not use Python (docs/TRY.md).
- Packs that require `pytacheck` find the installed `metacheck`.
- Upgrading: if you installed `pytacheck` from GitHub, run `pip uninstall pytacheck`
  before installing `metacheck` (both install the same files). Pack authors: a
  workflow that installs `pytacheck @ git+...` must install `metacheck @ git+...`
  (or `metacheck>=0.4.0a1` from PyPI) instead.
- `pyyaml` is now a core dependency (codecheck reads YAML).
- NOTICE and CITATION.cff credit the R package and its authors.

Complete rebuild as a parity-tested Python port of metacheck `dev`
(`85c8c87`, metacheck 0.3.1) plus scienceverse/metacheck#423 (`b239264`,
bibr export schema 12.0), which pytacheck targets ahead of its merge.

- R-faithful regular expressions (TRE leftmost-longest and PCRE semantics),
  number formatting, collation and dplyr idioms (`pytacheck._r`).
- bibr-schema `Paper`/`PaperList` with lazily materialised tables; reading
  bibr JSON directly or in-process from bibr (`metacheck[bibr]`).
- Module system compatible with metacheck's (`module_run`, chaining,
  `get_prev_outputs`, `module_list`), plugin modules via entry points.
- Parity harness running metacheck in R; goldens regenerated in CI.
- Shared HTTP layer with httr2's retry/rate-limit behaviour; replay of
  metacheck's recorded API fixtures in tests.
- CLI (`pytacheck`), Docker images (with and without bibr), and a scheduled
  upstream-sync workflow that ports new metacheck commits automatically.

### App

- `metacheck-app` (also `pytacheck app`, extra `metacheck[app]`): a local Gradio page
  that checks a PDF, GROBID XML or bibr JSON file and shows the results table and the
  report. It listens on 127.0.0.1 only, behind a per-launch token, and a second start
  reuses the running app. `--self-test` checks the demo paper without a server.
  A browser sends its cookie for 127.0.0.1 to every local port, so another program
  that you visit in the same browser can see the token. The app therefore refuses
  requests that other local pages start, reads only files uploaded through the page,
  and shows the report in a sandboxed frame that cannot load anything.

### Parity harness

- Accuracy contract (docs/PORTING.md section 1): pytacheck is checked against
  metacheck on every change and must be at least as accurate on realistic
  inputs; metacheck's bugs are fixed, not reproduced; R's error texts and C
  library quirks are not emulated.
- Tiers: cases on realistic inputs (`parity/corpus.toml`) are tier 1 and must
  agree with R apart from documented bug fixes; synthetic edge cases are tier
  2. `python -m parity check --tier`, pytest marks `tier1`/`tier2`.
- Divergence marks are validated against `docs/UPSTREAM_ISSUES.md` (which now
  has a status column) and the Python output of every marked case is pinned
  in `parity/lock/<area>.json`: a changed value (`py_changed`), a changed R
  golden (`r_changed`) or an upstream fix (`xpass`) is reported.
- Errors: when R fails, only Python failing too is checked; R's messages are
  never compared (`$catch` constructor, `presence` compare option).
- `--jobs N` (full check in about 3 minutes on 4 CPUs instead of 10),
  per-run reports, a network guard inside cases, case loading 0.9 s (8.3 s).
- Shared helpers `pytacheck._json`, `pytacheck._values` and
  `pytacheck.http.resp_json`; `python-calamine` joins the core dependencies
  and `chardet` is the optional `charset` extra.
- A `known_divergence` can carry `r_text` substitutions applied to R's golden
  before the comparison, so a case whose only difference is text pytacheck
  corrects (typos, plurals, a full stop) is still compared with R for
  everything else; 160 cases use it (`parity/divergences/prose.yaml` and
  inline marks). A substitution that no longer matters fails as stale.
- Parity keeps the Python side's caches in a throwaway `PYTACHECK_CACHE_DIR`;
  parity and pytest fail when a run leaves a new file in the repository root.
- Network-dependent causal_claims cases are replaced by offline variants; the
  2-second `reproducibility_check.exec_timeout` case by `exec_timeout_10s`.
- `python -m parity accuracy [--generate] [--gate]`: every offline paper
  module on 21 real papers and every repository module on 10 repositories
  (439 outputs), scored against R field by field (traffic light, summary
  table, table rows, summary text and report, numbers inside prose) with a
  whitespace/wording/values level per difference. Every difference must be
  explained by an entry in `parity/accuracy/expected.yaml` that cites a
  U- or D-entry; unexplained or stale entries fail the gate. Today: traffic
  lights agree on 421 of 434 outputs, 188 differences, all explained.
- The reference R reads each paper once per session (`run_cases.R --out`);
  `careless` is installed in a separate library that only the accuracy run
  uses, so parity goldens do not change.
- CI runs unit tests plus tier 1 on every Python version, the whole harness
  and the accuracy gate once, and tier 1 with pyarrow. The upstream sync runs
  the accuracy report before and after porting and opens a draft PR labelled
  `needs-human-review` when tier-1 marks, a tier-1 case's tier,
  `parity/corpus.toml`, `expected.yaml` or D-entries change.

### Changed: regular expressions

- R's regex dialects (TRE for `grepl`/`gsub`, PCRE with `perl = TRUE`) are
  translated onto the `regex` module instead of being emulated: the
  hand-written TRE matcher, the PCRE translator and the glibc character
  tables are gone (`_r` went from 4,971 to 1,100 lines). A replay of 32,902
  recorded realistic calls gives the same results as before; compiling the
  2,591 realistic patterns takes 1.7 s instead of 8.4 s, and 13 modules on a
  paper run in 1.2 s instead of 2.7 s.
- Case-insensitive matching uses Unicode case folding, so `İ`, `ı`, `ſ` and
  the Kelvin sign match like their ASCII letters (D29): for example
  'CONFLİCT OF İNTEREST' is now found. glibc-only class members (NBSP in
  `[[:punct:]]`, U+2028 in `[[:cntrl:]]`) are not reproduced.
- Fixed: the literal prefilter in front of `grepl` could drop true matches of
  dotted or dotless i.

### Performance

The Python side of the accuracy run (439 outputs) takes 16.9 s instead of
34.7 s, its modules 13.6 s instead of 30.6 s (the minimum of 5 interleaved
runs each), with every output byte-identical (docs/design/PERF_REPORT.md).

- `text_search()` builds one table per call (strings, case-folded strings,
  row positions) and matches every pattern of a list against it, building a
  data frame for the result rows only; it used to run a full search per
  pattern. One paper's sentences get their section headers by a lookup
  instead of a merge. ethics_check and open_practices run about 6x
  faster, funding_check_oi 7.7x.
- `paper_table()` of one paper returns the paper's own table as a
  copy-on-write view, and `paper_id()` counts `info` rows instead of building
  the table.
- codebook_check folds a paper's text once for its dictionary scans and
  compiles a dictionary pattern only when one of its literals is in the text;
  the dictionaries' acronym indexes are built once.
- RetractionWatch is cleaned and indexed by DOI once per database file, not
  on every call.
- `bind_rows()` skips its column alignment when every part already has the
  output's columns and types; `casefold()` no longer uses `str.translate`
  (about 5x faster on non-ASCII text).
- data_check builds its column statistics as rows, one frame per file;
  repo_check skips the 13 platform listings when a paper has no repository
  links; bibr 12 column typing skips R's coercion for plain values.
- `grepl()`, `text_search()` and the codebook scans share one matcher with
  the literal prefilter (`pytacheck._r.regex.detector()`), replacing three
  copies of it; the hand-written prefilters of open_practices and of the
  live-data search are gone.
- Fixed: inside `run_session()` (the CLI and the API), a paper building one
  of its tables from its JSON records changed its memo key, so a later run of
  the same module on it (a nested repo_check, say) was computed again.
- The accuracy harness runs every module of an input on one shared paper,
  as `report()` does, instead of a copy per output, and flags a module that
  changes it: through the Paper API (per output), or anywhere in its content,
  a list inside a cell or a nested `extra` entry included (at the end).

### Fixed: metacheck bugs pytacheck no longer reproduces

See `docs/UPSTREAM_ISSUES.md`; every affected parity case is a documented
`known_divergence`.

- Reading papers (U16-U18, U21, U22, U24-U28, U158): Grobid TEI conversion
  keeps the space before numbers (`Hamilton 1964`, `p = .27`) and joins
  reference DOIs split by line breaks; tables without rows, references
  without text, one-ended page ranges, nested and back-matter divs, the paper
  DOI (header only) and figure/table rows are handled correctly; URL hrefs are
  printed once, where their link is, without dropping link words; Grobid
  `<s>` tags are unwrapped. bibr 12.x keys match exactly, array values in
  scalar fields are read row by row, `df` arrays keep both degrees of
  freedom, doubles keep full precision and date-times four-digit years.
  `extract_urls()` no longer takes "et al.Word" for a host name.
- Archives (U31-U54, U69-U76, U152, U155, U156): Dataverse hosts are matched
  per URL and Figshare DOIs are no longer Dataverse links; files left out by
  the size caps stay in the `*_file_download()` tables; zero-byte files,
  string licences, NA URLs and CP437 zip names no longer abort a call; Dryad
  file listings follow their pages; DataONE, 4TU, OSF and GitHub id and host
  detection fixed (`osf.io/prereg` is not an OSF id); exact CRCs, whole-member
  inflation and real decompressed sizes in the repository download code.
- data_check, codebook_check, psychds_check and the data-file helpers (U55-U64,
  U78, U91-U93, U98-U100, U112, U113, U154): blank column-name collisions are
  reported; outliers with infinite quartiles and fractional scale ranges no
  longer fail; 1e5 is no longer a "typo of 5"; `V1-V10` codebook ranges are
  expanded; `.rds`/`.RData` keep column labels; manifests keep nulls and full
  precision; Latin-1 files are read; data_check returns a full result when
  nothing is readable and counts extracted files correctly; codebook_check
  de-duplicates definitions per paper and matches acronyms literally;
  psychds_check keeps valid Psych-DS names and root files in place, gives each
  paper of a list its own summary row and accepts `paper = NULL`.
- code_check, repo_check, reg_check, the reproducibility checks and the
  statistical-output readers (U66-U68, U78, U86-U89, U120-U123, U132-U148,
  U153): `.qmd`/`.ipynb` languages are read from the local copy; empty code
  files are analysed; files are de-duplicated per file, not per name; local
  archives count as archives; `code_check(paper = NULL)` runs on a local
  folder; SPSS template cells, SMCL hex characters and HTML `<meta charset>`
  are honoured; `match_reported_output()` keeps scientific-notation precision.
- Text modules (U23, U30, U82-U85, U95, U97, U101-U111, U114-U119,
  U124-U127): paper lists are summarised per paper (open_practices,
  causal_claims, ethics_check, the ref_* modules); failed lookups and odd
  inputs no longer stop modules (ref_pubpeer, coi_check, funding_check,
  prereg_check, stat_effect_size); false positives and negatives removed
  (lowercase r as R code, acknowledgment sections, randomisation spelling and
  negation, star notes in p-value checks, beta as eta-squared, "partial"
  applied to every value of a sentence); ref_miscitation no longer quotes
  "NA" for references without an in-text citation; typos and plurals in
  module text are fixed.
- Empty inputs (U79): every module now runs on an empty paper list and on
  `paper()`, giving its empty or "na" result instead of metacheck's errors
  (ethics_check, power, prereg_check, reg_check, repo_check and the modules
  that run it, the ref_* modules); `ref_table()` and the `*_links()` functions
  return typed empty tables.
- ref_retraction matches RetractionWatch DOIs whatever their case (U157):
  13,123 of 61,376 RetractionWatch DOIs have capitals, e.g. the retracted
  Wakefield et al. (1998) Lancet paper, which a lower-case citation now flags.
  ref_replication (FLoRA) and ref_miscitation ignore DOI case too.
- marginal cites "Olsson-Collentine A, van Assen MALM, Hartgerink CHJ (2019)"
  instead of metacheck's mangled author string (U3); reg_check's light is
  "fail" instead of "error" when every RegCheck comparison fails (U30).
- repo_check reports an unfound DSpace or PsychArchives item as a failed
  repository (U43); code_check and data_check use the singular for a count of
  one ("In 1 code file", "1 distinct respondent was flagged") (U82).
- Core (U2, U4-U10, U12-U15, U19, U20, U77, U79, U80, U128-U131, U149-U151):
  the API's `/paper/search` `section` filter works; `stats()` keeps checkable
  results next to unparseable ones, rejects invalid arguments and reads the Q
  subtype correctly; `extract_eq()`/`extract_tests()` treat each paper of a
  list separately; DOI, Crossref, OpenAlex and PubPeer lookups return one row
  per input and DataCite titles are read correctly; RetractionWatch rows
  without a DOI no longer match every reference; `llm()` keeps the answers of
  sanitised and partly failing texts; `text_search(return = "section")` keeps
  section headers; reports keep the headings, callouts and authors metacheck
  dropped.
