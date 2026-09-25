"""Python counterparts of the R expressions the bibr12 parity cases use.

Each helper does what the matching ``$expr`` R code in
``parity/cases/bibr12.yaml`` does, so R and Python results can be compared.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import orjson
import pandas as pd

import pytacheck as pc

ROOT = Path(__file__).resolve().parents[2]
F12 = ROOT / "upstream" / "metacheck" / "tests" / "testthat" / "fixtures" / "bibr12"


def identity(x: Any) -> Any:
    """R's ``identity()``: the cases compute their value in ``$expr``."""
    return x


def _path(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


def _tempdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="pc_bibr12_"))


def read_lines(path: str | Path) -> list[str]:
    """``readLines(path, encoding = "UTF-8")``."""
    text = _path(path).read_bytes().decode("utf-8")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def normalise_converter(lines: list[str]) -> list[str]:
    """Blank the converter's name and version (metacheck vs pytacheck)."""
    out = list(lines)
    for i, line in enumerate(lines):
        if line.strip() == '"converter": {':
            out[i + 1] = "<converter name>"
            out[i + 2] = "<converter version>"
    return out


def written_lines(paper: Any, file_name: str | None = None) -> list[str]:
    """The lines of ``paper_write(paper, schema_version = "12.0")``, converter blanked."""
    path = pc.paper_write(paper, file_name, _tempdir(), schema_version="12.0")
    return normalise_converter(read_lines(path))  # type: ignore[arg-type]


def write_read(paper: Any, file_name: str | None = None) -> Any:
    """``read(paper_write(paper, schema_version = "12.0"))``."""
    path = pc.paper_write(paper, file_name, _tempdir(), schema_version="12.0")
    return pc.read(path)  # type: ignore[arg-type]


def legacy_summary(paper: Any) -> dict[str, Any]:
    """What ``paper_write(paper)`` (``schema_version = NULL``) writes, in brief."""
    path = pc.paper_write(paper, None, _tempdir(), schema_version=None)
    json = orjson.loads(Path(path).read_bytes())  # type: ignore[arg-type]
    return {
        "names": list(json),
        "title": json["info"][0]["title"],
        "schema_version": json.get("schema_version"),
    }


def json_variant(path: str | Path, name: str = "variant.json", **changes: Any) -> str:
    """A copy of a JSON file with root keys changed (``jsonlite::write_json()``)."""
    data = orjson.loads(_path(path).read_bytes())
    data.update(changes)
    out = _tempdir() / name
    out.write_bytes(orjson.dumps(data))
    return str(out)


def later_12x(path: str | Path) -> str:
    """A 12.1 file with keys 12.0 does not know (test-import-bibr12.R)."""
    data = orjson.loads(_path(path).read_bytes())
    data["schema_version"] = "12.1"
    data["new_table"] = [{"new_id": 1}]
    data["metadata"]["new_field"] = "new"
    data["text"][0]["new_field"] = "new"
    data["extraction"]["new_block"] = {"new_field": "new"}
    out = _tempdir() / Path(path).name
    out.write_bytes(orjson.dumps(data))
    return str(out)


def mixed_dir() -> str:
    """A directory with a bibr 11.0 file and a 12.0 file."""
    d = _tempdir()
    data = orjson.loads((F12 / "probe_html.json").read_bytes())
    (d / "a_v12.json").write_bytes(orjson.dumps(data))
    data["schema_version"] = "11.0"
    (d / "b_v11.json").write_bytes(orjson.dumps(data))
    return str(d)


def full_nodoi(schema_version: Any = "12.0") -> Any:
    """full.json with a reference without a DOI and an untitled 0.98 match (ref_accuracy)."""
    p = pc.read(F12 / "full.json")
    bib = p.bib.copy()
    bib["doi"] = pd.Series([None] * len(bib), dtype="string")
    bib["text_id"] = pd.Series([2] * len(bib), dtype="Int64")
    p.bib = bib
    match = p.bib_match.copy()
    match["title"] = pd.Series([None] * len(match), dtype="string")
    p.bib_match = match
    if schema_version != "12.0":
        info = p.info.copy()
        info["schema_version"] = pd.Series([schema_version], dtype="string")
        p.info = info
    return p


def with_schema_version(path: str | Path, version: Any) -> Any:
    """A paper read from *path* whose info says another schema_version."""
    p = pc.read(_path(path))
    info = p.info.copy()
    info["schema_version"] = pd.Series([version], dtype="string")
    p.info = info
    return p


def add_bib_match_rows() -> Any:
    """full.json with the bib_match columns add_bib_match() makes (test-import-bibr12.R)."""
    p = pc.read(F12 / "full.json")
    p.bib_match = pd.DataFrame(
        {
            "bib_id": pd.Series([1], dtype="Int64"),
            "service": pd.Series(["crossref"], dtype="string"),
            "service_id": pd.Series([None], dtype="string"),
            "score": pd.Series([61.7], dtype="float64"),
            "bib_type": pd.Series(["article"], dtype="string"),
            "doi": pd.Series(["10.1234/PRIOR"], dtype="string"),
            "title": pd.Series(["A Prior Study"], dtype="string"),
            "publisher": pd.Series([None], dtype="string"),
            "year": pd.Series([2020], dtype="Int64"),
            "date": pd.Series([None], dtype="string"),
            "container": pd.Series(["Journal of Things"], dtype="string"),
            "authors": pd.Series(
                [pd.DataFrame({"given": ["Jane"], "family": ["Smith"]})], dtype=object
            ),
            "editors": pd.Series(
                [
                    pd.DataFrame(
                        {
                            "given": pd.Series([], dtype="string"),
                            "family": pd.Series([], dtype="string"),
                        }
                    )
                ],
                dtype=object,
            ),
        }
    )
    return p


def match_rows_variants() -> Any:
    """full.json with older-style match rows: dates, services, types and DOIs to convert."""
    p = pc.read(F12 / "full.json")
    n = 4
    p.bib_match = pd.DataFrame(
        {
            "bib_id": pd.Series([1, 1, 1, 1], dtype="Int64"),
            "service": pd.Series(["crossref", "pubmed", None, "ror"], dtype="string"),
            "score": pd.Series([0.5, -1.0, 100.0, None], dtype="float64"),
            "bib_type": pd.Series(
                ["inproceedings", "posted-content", "weird", None], dtype="string"
            ),
            "doi": pd.Series(
                [" https://doi.org/10.1234/ABC ", "doi: 10.5555/x.y", "not a doi", None],
                dtype="string",
            ),
            "title": pd.Series(["A", "B", "C", "D"], dtype="string"),
            "year": pd.Series([2020, None, 2021, 2022], dtype="Int64"),
            "date": pd.Series(["2020-05", "May 2020", None, "2022-01-02"], dtype="string"),
            "authors": pd.Series(
                [
                    pd.DataFrame({"given": ["Jane", None], "family": ["Smith", "Doe"]}),
                    None,
                    pd.DataFrame(
                        {
                            "given": pd.Series([], dtype="string"),
                            "family": pd.Series([], dtype="string"),
                        }
                    ),
                    pd.DataFrame({"given": [None], "family": [None]}),
                ],
                dtype=object,
            ),
        }
    )
    info_match = p.info_match.copy()
    info_match["score"] = pd.Series([99.0], dtype="float64")
    p.info_match = info_match
    del n
    return p


def table_paper12(contents: list[list[str]], caption: str, schema_version: Any = "12.0") -> Any:
    """full.json whose one table has these contents and caption (match-table.R)."""
    p = pc.read(F12 / "full.json")
    tab = p.table.copy()
    tab["contents"] = pd.Series([contents], dtype=object)
    tab["caption"] = pd.Series([caption], dtype="string")
    p.table = tab
    if schema_version != "12.0":
        info = p.info.copy()
        info["schema_version"] = pd.Series([schema_version], dtype="string")
        p.info = info
    return p


def correlation_table(schema_version: Any = "12.0") -> Any:
    """full.json whose table is a correlation matrix with its caption."""
    return table_paper12(
        [["", "1.", "2."], ["1. Anxiety", "-", ""], ["2. Depression", ".45*", "-"]],
        "Table 1. Correlations between measures",
        schema_version,
    )


def descriptive_table(schema_version: Any = "12.0") -> Any:
    """full.json whose table holds means under numbered headers, named by its caption."""
    return table_paper12(
        [["", "1", "2"], ["Condition A", "1.93", "-0.15"]],
        "Table 1. Descriptive statistics",
        schema_version,
    )


def match_jasp(paper: Any, what: str = "table") -> Any:
    """``match_reported_output(paper, <sample.jasp>, include_tables = TRUE)``."""
    from pytacheck.statout.match_reported import match_reported_output
    from pytacheck.statout.stat_output import stat_results_long
    from pytacheck.statout.stat_tables import read_stat_tables

    jasp = ROOT / "upstream/metacheck/tests/testthat/fixtures/formats/sample.jasp"
    output = stat_results_long(read_stat_tables(str(jasp)), source_file="sample.jasp")
    res = match_reported_output(paper, output, include_tables=True, min_components=1)
    return res.attrs["summary"] if what == "summary" else res


def schema_summary() -> dict[str, Any]:
    """The tables, required tables and column types of ``.paper_schema_bibr12()``."""
    from pytacheck.io.bibr12 import _paper_schema_bibr12

    s = _paper_schema_bibr12()
    defs = {}
    for name, d in s["$defs"].items():
        props = d.get("properties") or {}
        types = {}
        for col, spec in props.items():
            t = spec.get("type")
            types[col] = (t[0] if isinstance(t, list) else t) if t is not None else None
        defs[name] = types
    enums = {}
    for name, d in s["$defs"].items():
        for col, spec in (d.get("properties") or {}).items():
            if "enum" in spec:
                enums[f"{name}.{col}"] = list(spec["enum"])
    return {
        "properties": list(s["properties"]),
        "required": list(s["required"]),
        "description": s["description"],
        "defs": defs,
        "enums": enums,
    }


def paperlist_write(paths: list[str], names: list[str] | None = None) -> list[str]:
    """Basenames of ``paper_write(read(paths), schema_version = "12.0")``."""
    papers = pc.read([_path(p) for p in paths])
    out = pc.paper_write(papers, names, _tempdir(), schema_version="12.0")
    return [Path(p).name for p in out]  # type: ignore[union-attr]
