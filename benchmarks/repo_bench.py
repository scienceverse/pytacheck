"""Time repo_check -> code_check -> data_check in metacheck (R) and pytacheck on repositories.

    uv run python benchmarks/repo_bench.py --runs 2 --out bench-out \
        https://github.com/owner/repo https://osf.io/abcde ...

This is the chain metacheck's repository app (inst/app/shiny_app_repocheck) runs
on a link: a test paper whose only text is the link, then repo_check, code_check
and data_check with ``max_file_size = 100`` and ``max_download_size = 500`` (MB),
without an LLM. Each run starts a fresh process with an empty cache folder and
temp folder, so every run is cold. Both engines run the same repository back to
back, alternating which goes first, so a slow spell on a host hits both.

For each run it records wall time per module, the HTTP requests made (R: calls
into curl; Python: requests through httpx's transport less the redirect hops,
which curl follows inside one transfer; both count each retry), the requests per host, the bytes left in the
cache and temp folders, and what each module found. Results go to
``<out>/results.jsonl``, one line per engine, repository and run;
``--summary`` prints a table from that file.

Needs PYTACHECK_RSCRIPT (or Rscript on PATH) with metacheck installed. A GitHub
token in GITHUB_PAT_GITHUB_COM (or GITHUB_TOKEN, copied to it) is used by both.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MODULES = ["repo_check", "code_check", "data_check"]

R_SCRIPT = r"""
suppressPackageStartupMessages(library(metacheck))
a <- commandArgs(trailingOnly = TRUE)
url <- a[[1]]; out <- a[[2]]; cache <- a[[3]]
max_file <- as.numeric(a[[4]]); max_dl <- as.numeric(a[[5]])
options(metacheck.cache.dir = cache)
llm_use(FALSE)
.bench <- new.env()
.bench$urls <- character(0)
.bench$n <- 0L
.note <- function(u) {
  .bench$n <- .bench$n + 1L
  .bench$urls <- c(.bench$urls, if (is.character(u)) u[[1]] else NA_character_)
}
# every transfer: httr2's req_perform() ends in curl_fetch_*, req_perform_connection()
# (zip archives) in curl(), req_perform_parallel() in multi_add()
for (f in c("curl_fetch_memory", "curl_fetch_disk", "curl_fetch_stream", "curl_download", "curl")) {
  invisible(suppressMessages(trace(f, tracer = quote(.note(url)), where = asNamespace("curl"), print = FALSE)))
}
invisible(suppressMessages(trace("multi_download", tracer = quote(for (u in urls) .note(u)),
      where = asNamespace("curl"), print = FALSE)))
invisible(suppressMessages(trace("multi_add", tracer = quote(.note(tryCatch(curl::handle_data(handle)$url, error = function(e) NA_character_))),
      where = asNamespace("curl"), print = FALSE)))
environment(.note) <- globalenv()
args <- list(code_check = list(max_file_size = max_file, max_download_size = max_dl),
             data_check = list(max_file_size = max_file, max_download_size = max_dl))
paper <- test_paper(text = url, url = url)
op <- paper
res <- list()
for (m in c("repo_check", "code_check", "data_check")) {
  n0 <- .bench$n; u0 <- length(.bench$urls)
  t0 <- Sys.time()
  err <- NULL
  op2 <- tryCatch(withCallingHandlers(
    do.call(module_run, c(list(op, m), args[[m]])),
    warning = function(w) invokeRestart("muffleWarning")),
    error = function(e) { err <<- conditionMessage(e); NULL })
  secs <- as.numeric(Sys.time() - t0, units = "secs")
  urls <- if (length(.bench$urls) > u0) .bench$urls[(u0 + 1):length(.bench$urls)] else character(0)
  hosts <- sub("^https?://([^/]+).*$", "\\1", urls[!is.na(urls)])
  tab <- if (!is.null(op2)) op2$table else NULL
  res[[m]] <- list(seconds = secs, requests = .bench$n - n0,
                   hosts = as.list(table(hosts)),
                   rows = if (is.data.frame(tab)) nrow(tab) else NA,
                   traffic_light = if (!is.null(op2)) op2$traffic_light else "error",
                   error = err)
  if (m == "data_check" && is.data.frame(tab)) {
    # the table has one row per data column described: count the files they came from
    fcol <- intersect(c("source_file", "file_location", "file_name"), names(tab))
    res[[m]]$read <- if (length(fcol)) length(unique(tab[[fcol[[1]]]])) else NA
    res[[m]]$columns <- names(tab)
  }
  if (!is.null(op2)) op <- op2
}
tmp_files <- list.files(tempdir(), recursive = TRUE, full.names = TRUE, all.files = TRUE)
res$tmp_bytes_at_exit <- sum(file.size(tmp_files), na.rm = TRUE)
writeLines(jsonlite::toJSON(res, auto_unbox = TRUE, null = "null", na = "null"), out)
"""


PY_SCRIPT = r"""
import collections, json, sys, threading, time, warnings
from urllib.parse import urlsplit

url, out, cache, max_file, max_dl, concepts = sys.argv[1:7]
import httpx

lock = threading.Lock()
seen = []
redirects = []

def wrap(cls, name):
    orig = getattr(cls, name)
    if name.startswith("handle_async"):
        async def f(self, request):
            with lock:
                seen.append(str(request.url))
            resp = await orig(self, request)
            if 300 <= resp.status_code < 400 and "location" in resp.headers:
                with lock:
                    redirects.append(str(request.url))
            return resp
    else:
        def f(self, request):
            with lock:
                seen.append(str(request.url))
            resp = orig(self, request)
            if 300 <= resp.status_code < 400 and "location" in resp.headers:
                with lock:
                    redirects.append(str(request.url))
            return resp
    setattr(cls, name, f)

wrap(httpx.HTTPTransport, "handle_request")
wrap(httpx.AsyncHTTPTransport, "handle_async_request")

import metacheck as mc
from metacheck.llm.core import llm_use
from metacheck.utils import options

options({"metacheck.cache.dir": cache})
llm_use(False)
dl = {"max_file_size": float(max_file), "max_download_size": float(max_dl)}
args = {"code_check": dict(dl), "data_check": dict(dl)}
if concepts:
    args["data_check"]["concepts"] = concepts
paper = mc.test_paper(text=[url], url=[url])
op = paper
res = {}
for m in ["repo_check", "code_check", "data_check"]:
    n0, r0 = len(seen), len(redirects)
    t0 = time.perf_counter()
    err = None
    op2 = None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            op2 = mc.module_run(op, m, **args.get(m, {}))
    except Exception as exc:  # noqa: BLE001 - recorded, as R's tryCatch
        err = f"{type(exc).__name__}: {exc}"
    secs = time.perf_counter() - t0
    urls = seen[n0:]
    hosts = collections.Counter(urlsplit(u).netloc for u in urls)
    tab = getattr(op2, "table", None) if op2 is not None else None
    rows = int(len(tab)) if hasattr(tab, "shape") else None
    # R's curl follows a redirect inside one transfer, so a redirect hop is not counted
    r = {"seconds": secs, "requests": len(urls) - (len(redirects) - r0),
         "http_hops": len(urls), "hosts": dict(hosts), "rows": rows,
         "traffic_light": op2.traffic_light if op2 is not None else "error", "error": err}
    if m == "data_check" and hasattr(tab, "columns"):
        # the table has one row per data column described: count the files they came from
        fcol = [c for c in ("source_file", "file_location", "file_name") if c in tab.columns]
        r["read"] = int(tab[fcol[0]].nunique()) if fcol else None
        r["columns"] = list(tab.columns)
    res[m] = r
    if op2 is not None:
        op = op2
import os, tempfile
total = 0
for root, _d, files in os.walk(tempfile.gettempdir()):
    for f in files:
        try:
            total += os.lstat(os.path.join(root, f)).st_size
        except OSError:
            pass
res["tmp_bytes_at_exit"] = total
with open(out, "w") as fh:
    json.dump(res, fh)
"""


def _du(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def run_one(engine: str, url: str, run: int, out: Path, a: argparse.Namespace) -> dict:
    work = Path(tempfile.mkdtemp(prefix=f"{engine}-", dir=out / "work"))
    cache, tmp, result = work / "cache", work / "tmp", work / "result.json"
    cache.mkdir()
    tmp.mkdir()
    env = {
        **os.environ,
        "TMPDIR": str(tmp),
        "TMP": str(tmp),
        "TEMP": str(tmp),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
    if not env.get("GITHUB_PAT_GITHUB_COM") and env.get("GITHUB_TOKEN"):
        env["GITHUB_PAT_GITHUB_COM"] = env["GITHUB_TOKEN"]
    if engine == "r":
        script = work / "bench.R"
        script.write_text(R_SCRIPT)
        rscript = os.environ.get("PYTACHECK_RSCRIPT") or shutil.which("Rscript") or "Rscript"
        cmd = [
            rscript,
            str(script),
            url,
            str(result),
            str(cache),
            str(a.max_file_size),
            str(a.max_download_size),
        ]
    else:
        script = work / "bench.py"
        script.write_text(PY_SCRIPT)
        cmd = [
            sys.executable,
            str(script),
            url,
            str(result),
            str(cache),
            str(a.max_file_size),
            str(a.max_download_size),
            a.concepts,
        ]
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd, cwd=work, env=env, capture_output=True, text=True, timeout=a.timeout
        )
        code, log = proc.returncode, proc.stdout + proc.stderr
    except subprocess.TimeoutExpired as exc:
        code = "timeout"
        log = (
            (exc.stdout or b"").decode(errors="replace")
            if isinstance(exc.stdout, bytes)
            else (exc.stdout or "")
        )
    wall = time.perf_counter() - t0
    (out / "logs").mkdir(exist_ok=True)
    slug = url.split("://", 1)[-1].replace("/", "_")
    (out / "logs" / f"{engine}-{slug}-{run}.log").write_text(log)
    modules = json.loads(result.read_text()) if result.exists() else None
    tmp_at_exit = modules.pop("tmp_bytes_at_exit", None) if modules else None
    row = {
        "engine": engine,
        "url": url,
        "run": run,
        "exit": code,
        "process_seconds": wall,
        "module_seconds": sum(m["seconds"] for m in modules.values()) if modules else None,
        "requests": sum(m["requests"] for m in modules.values()) if modules else None,
        "cache_bytes": _du(cache),
        "tmp_bytes": max(_du(tmp), tmp_at_exit or 0),
        "modules": modules,
    }
    if not a.keep:
        shutil.rmtree(work, ignore_errors=True)
    return row


def summary(path: Path) -> None:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    by: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        by.setdefault((r["url"], r["engine"]), []).append(r)
    print(
        "| Repository | Engine | Runs | Total s (median) | repo_check s | code_check s | data_check s "
        "| Requests | Downloaded MB | Files listed | Data files read |"
    )
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for (url, engine), rs in sorted(by.items()):
        ok = [r for r in rs if r["modules"]]

        def med(f, ok=ok):
            vals = [v for v in (f(x) for x in ok) if v is not None]
            return statistics.median(vals) if vals else float("nan")

        def mod(m, key):
            return med(lambda x: (x["modules"].get(m) or {}).get(key))

        print(
            f"| {url} | {'R' if engine == 'r' else 'Python'} | {len(ok)}/{len(rs)} "
            f"| {med(lambda r: r['module_seconds']):.1f} "
            f"| {mod('repo_check', 'seconds'):.1f} | {mod('code_check', 'seconds'):.1f} "
            f"| {mod('data_check', 'seconds'):.1f} | {med(lambda r: r['requests']):.0f} "
            f"| {med(lambda r: (r['cache_bytes'] + r['tmp_bytes']) / 1e6):.0f} "
            f"| {mod('repo_check', 'rows'):.0f} | {mod('data_check', 'read'):.0f} |"
        )


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("urls", nargs="*")
    p.add_argument("--runs", type=int, default=1)
    p.add_argument("--out", type=Path, default=Path("bench-out"))
    p.add_argument("--engines", default="r,py")
    p.add_argument("--max-file-size", type=float, default=100)
    p.add_argument("--max-download-size", type=float, default=500)
    p.add_argument(
        "--concepts",
        default="rules",
        help="pytacheck data_check concepts tier ('rules' matches R without an LLM; "
        "'' keeps pytacheck's default, the local classifier)",
    )
    p.add_argument("--timeout", type=float, default=3600)
    p.add_argument("--keep", action="store_true", help="keep each run's cache and temp folders")
    p.add_argument("--summary", action="store_true")
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "work").mkdir(exist_ok=True)
    results = a.out / "results.jsonl"
    engines = a.engines.split(",")
    for url in a.urls:
        for run in range(a.runs):
            order = engines if run % 2 == 0 else engines[::-1]
            for engine in order:
                row = run_one(engine, url, run, a.out, a)
                with results.open("a") as fh:
                    fh.write(json.dumps(row) + "\n")
                print(
                    f"{engine:2} run {run} {url}: {row['module_seconds'] or float('nan'):.1f} s, "
                    f"{row['requests']} requests, exit {row['exit']}",
                    flush=True,
                )
    if a.summary or not a.urls:
        summary(results)


if __name__ == "__main__":
    main()
