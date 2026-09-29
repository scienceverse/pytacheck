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
    "R/app.R": "metacheck.report.app",
    "R/archive-4tu.R": "metacheck.archives.fourtu",
    "R/archive-aspredicted.R": "metacheck.archives.aspredicted",
    "R/archive-dataone.R": "metacheck.archives.dataone",
    "R/archive-dataverse.R": "metacheck.archives.dataverse",
    "R/archive-dryad.R": "metacheck.archives.dryad",
    "R/archive-dspace7.R": "metacheck.archives.dspace7",
    "R/archive-figshare.R": "metacheck.archives.figshare",
    "R/archive-fsd.R": "metacheck.archives.fsd",
    "R/archive-github.R": "metacheck.archives.github",
    "R/archive-gitlab.R": "metacheck.archives.gitlab",
    "R/archive-local.R": "metacheck.archives.local",
    "R/archive-mendeley.R": "metacheck.archives.mendeley",
    "R/archive-osf-all.R": "metacheck.archives.osf_all",
    "R/archive-osf-helpers.R": "metacheck.archives.osf_helpers",
    "R/archive-osf-metadata.R": "metacheck.archives.osf_metadata",
    "R/archive-osf.R": "metacheck.archives.osf",
    "R/archive-psycharchives.R": "metacheck.archives.psycharchives",
    "R/archive-researchbox.R": "metacheck.archives.researchbox",
    "R/archive-reshare.R": "metacheck.archives.reshare",
    "R/archive-zenodo-upload.R": "metacheck.archives.zenodo_upload",
    "R/archive-zenodo.R": "metacheck.archives.zenodo",
    "R/cache.R": "metacheck.archives.cache",
    "R/cap-prompt.R": "metacheck.llm.cap_prompt",
    "R/causal_sentences.R": "metacheck.text.causal",
    "R/code_check.R": "metacheck.codecheck.core",
    "R/data_check_helpers.R": [
        (2472, "metacheck.datacheck.files"),
        (4622, "metacheck.datacheck.columns"),
        (10**9, "metacheck.datacheck.checks"),
    ],
    "R/db-crossref.R": "metacheck.db.crossref",
    "R/db-pubpeer.R": "metacheck.db.pubpeer",
    "R/db-regcheck.R": "metacheck.db.regcheck",
    "R/db-replications.R": "metacheck.db.replications",
    "R/db-retractionwatch.R": "metacheck.db.retractionwatch",
    "R/doi.R": "metacheck.db.doi",
    "R/emojis.R": "metacheck.report.emojis",
    "R/extract-tests.R": "metacheck.text.extract_tests",
    "R/file-naming.R": "metacheck.fileinfo.naming",
    "R/file_category.R": "metacheck.fileinfo.category",
    "R/file_types.R": "metacheck.fileinfo.types",
    "R/html-output.R": "metacheck.report.html_output",
    "R/import-bibr.R": "metacheck.io.bibr_convert",
    "R/import-bibr12.R": "metacheck.io.bibr12",
    "R/import-grobid-bibr12.R": "metacheck.io.grobid_bibr12",
    "R/import-convert.R": "metacheck.io.convert",
    "R/import-grobid.R": "metacheck.io.grobid",
    "R/import-papers.R": "metacheck.io.corpus",
    "R/import-read.R": "metacheck.io.read",
    "R/jasp.R": "metacheck.statout.jasp",
    "R/llm-cache.R": "metacheck.llm.cache",
    "R/llm.R": "metacheck.llm.core",
    "R/match-reported.R": "metacheck.statout.match_reported",
    "R/match-table.R": "metacheck.statout.match_table",
    "R/module.R": "metacheck.module",
    "R/mplus.R": "metacheck.statout.mplus",
    "R/omv.R": "metacheck.statout.omv",
    "R/paper.R": "metacheck.papers",
    "R/r-capture.R": "metacheck.statout.r_capture",
    "R/r-output.R": "metacheck.statout.r_output",
    "R/regcheck-local.R": "metacheck.db.regcheck_local",
    "R/repo-download.R": "metacheck.archives.download",
    "R/repo-info-cache.R": "metacheck.archives.info_cache",
    "R/report-helpers.R": "metacheck.report.blocks",
    "R/report.R": "metacheck.report.report",
    "R/reproducibility_check.R": "metacheck.repro.core",
    "R/reproducibility_check_docker.R": "metacheck.repro.docker",
    "R/scales.R": "metacheck.datacheck.scales",
    "R/spv.R": "metacheck.statout.spv",
    "R/stat-output.R": "metacheck.statout.stat_output",
    "R/stat-tables.R": "metacheck.statout.stat_tables",
    "R/stat_helpers.R": "metacheck.stats.helpers",
    "R/stata.R": "metacheck.statout.stata",
    "R/stato-map.R": "metacheck.statout.stato_map",
    "R/stats.R": "metacheck.stats.core",
    "R/svutils-message.R": "metacheck.utils",
    "R/svutils-orcid.R": "metacheck.db.orcid",
    "R/svutils-pb.R": "metacheck.utils",
    "R/svutils-utils.R": "metacheck.utils",
    "R/svutils-xml.R": "metacheck.io.xml",
    "R/tasks.R": "metacheck.datacheck.tasks",
    "R/text-extractors.R": "metacheck.text.extract",
    "R/text-json_expand.R": "metacheck.text.json_expand",
    "R/text_expand.R": "metacheck.text.expand",
    "R/text_search.R": "metacheck.text.search",
    "R/utils-logging.R": "metacheck.log",
    "R/utils.R": "metacheck.utils",
    "R/validate.R": "metacheck.validate",
    "R/zip-peek.R": "metacheck.archives.zip_peek",
    "R/zzz.R": "metacheck.config",
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
            "python": f"metacheck.modules.{stem}:{stem}",
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
