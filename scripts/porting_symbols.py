"""Generate porting/symbols.json: the canonical Python home of every R function.

Every function defined in upstream/metacheck/R/*.R and inst/modules/*.R is
assigned a Python module and name up front, so code ported in parallel (or by
the upstream-sync agent) imports cross-area helpers from predictable places.

    uv run python scripts/porting_symbols.py

Rules: exported `foo.bar` -> `foo_bar`; internal `.foo` -> `_foo`; the module
comes from FILE_MODULES (line ranges split very large files). Entries from
porting/map/*.toml ([functions]) override the defaults once code exists.
"""

from __future__ import annotations

import json
import keyword
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UP = ROOT / "upstream" / "metacheck"

FILE_MODULES: dict[str, str | list[tuple[int, str]]] = {
    "R/app.R": "pytacheck.report.app",
    "R/archive-4tu.R": "pytacheck.archives.fourtu",
    "R/archive-aspredicted.R": "pytacheck.archives.aspredicted",
    "R/archive-dataone.R": "pytacheck.archives.dataone",
    "R/archive-dataverse.R": "pytacheck.archives.dataverse",
    "R/archive-dryad.R": "pytacheck.archives.dryad",
    "R/archive-dspace7.R": "pytacheck.archives.dspace7",
    "R/archive-figshare.R": "pytacheck.archives.figshare",
    "R/archive-fsd.R": "pytacheck.archives.fsd",
    "R/archive-github.R": "pytacheck.archives.github",
    "R/archive-gitlab.R": "pytacheck.archives.gitlab",
    "R/archive-local.R": "pytacheck.archives.local",
    "R/archive-mendeley.R": "pytacheck.archives.mendeley",
    "R/archive-osf-all.R": "pytacheck.archives.osf_all",
    "R/archive-osf-helpers.R": "pytacheck.archives.osf_helpers",
    "R/archive-osf-metadata.R": "pytacheck.archives.osf_metadata",
    "R/archive-osf.R": "pytacheck.archives.osf",
    "R/archive-psycharchives.R": "pytacheck.archives.psycharchives",
    "R/archive-researchbox.R": "pytacheck.archives.researchbox",
    "R/archive-reshare.R": "pytacheck.archives.reshare",
    "R/archive-zenodo-upload.R": "pytacheck.archives.zenodo_upload",
    "R/archive-zenodo.R": "pytacheck.archives.zenodo",
    "R/cache.R": "pytacheck.archives.cache",
    "R/cap-prompt.R": "pytacheck.llm.cap_prompt",
    "R/causal_sentences.R": "pytacheck.text.causal",
    "R/code_check.R": "pytacheck.codecheck.core",
    "R/data_check_helpers.R": [
        (2472, "pytacheck.datacheck.files"),
        (4622, "pytacheck.datacheck.columns"),
        (10**9, "pytacheck.datacheck.checks"),
    ],
    "R/db-crossref.R": "pytacheck.db.crossref",
    "R/db-pubpeer.R": "pytacheck.db.pubpeer",
    "R/db-regcheck.R": "pytacheck.db.regcheck",
    "R/db-replications.R": "pytacheck.db.replications",
    "R/db-retractionwatch.R": "pytacheck.db.retractionwatch",
    "R/doi.R": "pytacheck.db.doi",
    "R/emojis.R": "pytacheck.report.emojis",
    "R/extract-tests.R": "pytacheck.text.extract_tests",
    "R/file-naming.R": "pytacheck.fileinfo.naming",
    "R/file_category.R": "pytacheck.fileinfo.category",
    "R/file_types.R": "pytacheck.fileinfo.types",
    "R/html-output.R": "pytacheck.report.html_output",
    "R/import-bibr.R": "pytacheck.io.bibr_convert",
    "R/import-convert.R": "pytacheck.io.convert",
    "R/import-grobid.R": "pytacheck.io.grobid",
    "R/import-papers.R": "pytacheck.io.corpus",
    "R/import-read.R": "pytacheck.io.read",
    "R/jasp.R": "pytacheck.statout.jasp",
    "R/llm-cache.R": "pytacheck.llm.cache",
    "R/llm.R": "pytacheck.llm.core",
    "R/match-reported.R": "pytacheck.statout.match_reported",
    "R/match-table.R": "pytacheck.statout.match_table",
    "R/module.R": "pytacheck.module",
    "R/mplus.R": "pytacheck.statout.mplus",
    "R/omv.R": "pytacheck.statout.omv",
    "R/paper.R": "pytacheck.papers",
    "R/r-capture.R": "pytacheck.statout.r_capture",
    "R/r-output.R": "pytacheck.statout.r_output",
    "R/regcheck-local.R": "pytacheck.db.regcheck_local",
    "R/repo-download.R": "pytacheck.archives.download",
    "R/repo-info-cache.R": "pytacheck.archives.info_cache",
    "R/report-helpers.R": "pytacheck.report.blocks",
    "R/report.R": "pytacheck.report.report",
    "R/reproducibility_check.R": "pytacheck.repro.core",
    "R/reproducibility_check_docker.R": "pytacheck.repro.docker",
    "R/scales.R": "pytacheck.datacheck.scales",
    "R/spv.R": "pytacheck.statout.spv",
    "R/stat-output.R": "pytacheck.statout.stat_output",
    "R/stat-tables.R": "pytacheck.statout.stat_tables",
    "R/stat_helpers.R": "pytacheck.stats.helpers",
    "R/stata.R": "pytacheck.statout.stata",
    "R/stato-map.R": "pytacheck.statout.stato_map",
    "R/stats.R": "pytacheck.stats.core",
    "R/svutils-message.R": "pytacheck.utils",
    "R/svutils-orcid.R": "pytacheck.db.orcid",
    "R/svutils-pb.R": "pytacheck.utils",
    "R/svutils-utils.R": "pytacheck.utils",
    "R/svutils-xml.R": "pytacheck.io.xml",
    "R/tasks.R": "pytacheck.datacheck.tasks",
    "R/text-extractors.R": "pytacheck.text.extract",
    "R/text-json_expand.R": "pytacheck.text.json_expand",
    "R/text_expand.R": "pytacheck.text.expand",
    "R/text_search.R": "pytacheck.text.search",
    "R/utils-logging.R": "pytacheck.log",
    "R/utils.R": "pytacheck.utils",
    "R/validate.R": "pytacheck.validate",
    "R/zip-peek.R": "pytacheck.archives.zip_peek",
    "R/zzz.R": "pytacheck.config",
}

DEF = re.compile(r"^\s*`?([A-Za-z0-9._]+)`?\s*(?:<-|=)\s*function\b")


def py_name(r_name: str) -> str:
    internal = r_name.startswith(".")
    name = re.sub(r"[^A-Za-z0-9_]", "_", r_name.lstrip("."))
    if internal:
        name = "_" + name
    if keyword.iskeyword(name) or name in (
        "print",
        "format",
        "type",
        "id",
        "input",
        "open",
        "filter",
    ):
        name += "_"
    return name


def module_for(rel: str, line: int) -> str:
    target = FILE_MODULES.get(rel)
    if target is None:
        raise SystemExit(f"no module mapping for {rel}")
    if isinstance(target, str):
        return target
    for last_line, mod in target:
        if line <= last_line:
            return mod
    raise AssertionError


def main() -> None:
    symbols: dict[str, dict[str, str | int]] = {}
    for path in sorted((UP / "R").glob("*.R")):
        rel = f"R/{path.name}"
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            m = DEF.match(line)
            if m and line[: len(line) - len(line.lstrip())] == "":  # top-level only
                name = m.group(1)
                symbols.setdefault(
                    name,
                    {"file": rel, "line": i, "python": f"{module_for(rel, i)}:{py_name(name)}"},
                )
    for path in sorted((UP / "inst" / "modules").glob("*.R")):
        stem = path.stem
        symbols[f"module:{stem}"] = {
            "file": f"inst/modules/{path.name}",
            "line": 1,
            "python": f"pytacheck.modules.{stem}:{stem}",
        }
    for toml in sorted((ROOT / "porting" / "map").glob("*.toml")):
        data = tomllib.loads(toml.read_text(encoding="utf-8"))
        for r_name, target in data.get("functions", {}).items():
            entry = symbols.setdefault(r_name, {"file": "", "line": 0})
            entry["python"] = target
            entry["ported"] = True
    out = ROOT / "porting" / "symbols.json"
    out.write_text(json.dumps(symbols, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{len(symbols)} symbols -> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
