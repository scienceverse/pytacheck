"""Time the same work in metacheck (R) and pytacheck (Python).

    uv run python benchmarks/compare_r.py --papers 500 --module marginal --module all_p_values

Needs PYTACHECK_RSCRIPT (or Rscript on PATH) with metacheck installed. Both
sides read the same synthetic corpus from disk and run the same modules; the
reported times exclude interpreter/package start-up.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import make_corpus

R_SCRIPT = r"""
suppressPackageStartupMessages(library(metacheck))
args <- commandArgs(trailingOnly = TRUE)
dir <- args[[1]]; mods <- args[-1]
t0 <- Sys.time(); papers <- read(dir); t1 <- Sys.time()
res <- lapply(mods, function(m) { s <- Sys.time(); invisible(module_run(papers, m)); as.numeric(Sys.time() - s, units = "secs") })
cat(sprintf("read\t%.3f\n", as.numeric(t1 - t0, units = "secs")))
for (i in seq_along(mods)) cat(sprintf("%s\t%.3f\n", mods[[i]], res[[i]]))
"""


def python_times(corpus: Path, modules: list[str]) -> dict[str, float]:
    import pytacheck as pc

    times: dict[str, float] = {}
    t0 = time.perf_counter()
    papers = pc.read(corpus)
    times["read"] = time.perf_counter() - t0
    for m in modules:
        t = time.perf_counter()
        pc.module_run(papers, m)
        times[m] = time.perf_counter() - t
    return times


def r_times(corpus: Path, modules: list[str]) -> dict[str, float]:
    rscript = os.environ.get("PYTACHECK_RSCRIPT") or shutil.which("Rscript")
    if not rscript:
        raise SystemExit("Rscript not found (set PYTACHECK_RSCRIPT)")
    with tempfile.NamedTemporaryFile("w", suffix=".R", delete=False) as fh:
        fh.write(R_SCRIPT)
    out = subprocess.run(
        [rscript, fh.name, str(corpus), *modules],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
    ).stdout
    return {k: float(v) for k, v in (line.split("\t") for line in out.strip().splitlines())}


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--papers", type=int, default=300)
    ap.add_argument("--module", action="append", default=None)
    ns = ap.parse_args()
    modules = ns.module or ["marginal"]
    with tempfile.TemporaryDirectory() as tmp:
        corpus = make_corpus(Path(tmp) / "corpus", ns.papers)
        py = python_times(corpus, modules)
        r = r_times(corpus, modules)
    print(f"{ns.papers} papers")
    print(f"{'step':24} {'R (s)':>10} {'Python (s)':>12} {'speed-up':>9}")
    for step in ["read", *modules]:
        print(f"{step:24} {r[step]:10.3f} {py[step]:12.3f} {r[step] / max(py[step], 1e-9):8.1f}x")


if __name__ == "__main__":
    main()
