"""Python counterparts of the R expressions in ``parity/cases/bibr12_review.yaml``.

The review cases feed adversarial bibr 12.x files (``tests/bibr12/fixtures``)
and modified 12.x papers through the reader, the writer and the modules.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import orjson
import pandas as pd

import pytacheck as pc
from tests.bibr12._parity_helpers import _path, _tempdir, normalise_converter, read_lines

ROOT = Path(__file__).resolve().parents[2]
FX = ROOT / "tests" / "bibr12" / "fixtures"
F12 = ROOT / "upstream" / "metacheck" / "tests" / "testthat" / "fixtures" / "bibr12"


def written(paper: Any, blank_time: bool = False) -> list[str]:
    """The lines ``paper_write(paper, schema_version = "12.0")`` writes.

    The converter is blanked (metacheck vs pytacheck), and so is
    ``extraction.completed_at`` when *blank_time* (the time of writing).
    """
    path = pc.paper_write(paper, None, _tempdir(), schema_version="12.0")
    lines = normalise_converter(read_lines(path))  # type: ignore[arg-type]
    if blank_time:
        lines = ["<completed_at>" if ln.startswith('    "completed_at": ') else ln for ln in lines]
    return lines


def completed_at_is_iso(paper: Any) -> bool:
    """Whether the ``completed_at`` written for *paper* is an ISO 8601 UTC time."""
    path = pc.paper_write(paper, None, _tempdir(), schema_version="12.0")
    json = orjson.loads(Path(path).read_bytes())  # type: ignore[arg-type]
    return bool(
        re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", json["extraction"]["completed_at"])
    )


def roundtrip(paper: Any, blank_time: bool = False) -> Any:
    """``read(paper_write(paper, schema_version = "12.0"))``.

    With *blank_time*, ``extraction.completed_at`` (then the time of writing)
    is set to a constant, so the goldens do not change.
    """
    path = pc.paper_write(paper, None, _tempdir(), schema_version="12.0")
    out = pc.read(path)  # type: ignore[arg-type]
    if blank_time:
        out["extraction"]["completed_at"] = "<completed_at>"  # type: ignore[index]
    return out


def variant(path: str | Path, name: str = "variant.json", **changes: Any) -> str:
    """A copy of a JSON file with root keys changed (``None`` writes null)."""
    data = orjson.loads(_path(path).read_bytes())
    data.update(changes)
    out = _tempdir() / name
    out.write_bytes(orjson.dumps(data))
    return str(out)


def version_variant(path: str | Path, name: str, value: str) -> str:
    """A copy of *path* whose root ``"schema_version": "12.0"`` is *value* (JSON text)."""
    old = '"schema_version": "12.0"'
    lines = _path(path).read_bytes().decode("utf-8").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    i = next(k for k, ln in enumerate(lines) if old in ln)
    lines[i] = lines[i].replace(old, '"schema_version": ' + value, 1)
    out = _tempdir() / name
    out.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    return str(out)


def modified_edge() -> Any:
    """edge_types.json with tables changed as a user might change them."""
    p = pc.read(FX / "edge_types.json")
    text = p.text.copy()
    # a new row with doubles where 12.0 has integers, and no formatted value
    extra = pd.DataFrame(
        {
            "text": pd.Series(["Added row."], dtype="string"),
            "text_id": pd.Series([9.0]),
            "paragraph_id": pd.Series([8.0]),
            "section_id": pd.Series([2.0]),
        }
    )
    text = pd.concat([text, extra], ignore_index=True)
    text["page_number"] = text["page_number"].astype("float64")
    p.text = text.drop(columns=["formatted"])
    bib = p.bib.copy()
    bib["year"] = pd.Series([2019.9, None], dtype="float64")
    bib["volume"] = pd.Series([12.0, 1e5], dtype="float64")
    bib["is_in_press"] = pd.Series(["TRUE", "no"], dtype="string")
    bib["extra_col"] = pd.Series(["x", "y"], dtype="string")
    p.bib = bib
    info = p.info.copy()
    info["title"] = pd.Series([None], dtype="string")
    info["keywords"] = pd.Series([["k1", "k2"]], dtype=object)
    p.info = info
    author = p.author.copy()
    author["role"] = pd.Series(["single", None, "x", "y"], dtype="string")
    p.author = author
    del p.footnote
    return p


def legacy_roundtrip(paper: Any) -> Any:
    """``read(paper_write(paper))``: metacheck's default writer (``schema_version=None``)."""
    path = pc.paper_write(paper, None, _tempdir(), schema_version=None)
    return pc.read(path)  # type: ignore[arg-type]


def file_names(paper: Any, names: list[str]) -> list[str]:
    """The basenames ``paper_write(paper, name, schema_version = "12.0")`` writes."""
    d = _tempdir()
    return [Path(pc.paper_write(paper, n, d, schema_version="12.0")).name for n in names]  # type: ignore[arg-type]


def full_with_crossref_matches() -> Any:
    """full.json whose bib is replaced and matched with ``add_bib_match(p, 0)`` (recorded API)."""
    from tests.db.parity_replay import call

    p = pc.read(F12 / "full.json")
    p.bib = pd.DataFrame(
        {
            "bib_id": [1, 2],
            "doi": [None, None],
            "title": ["Facial resemblance enhances trust", "Trustworthy but not Lustworthy"],
            "container": ["Proceedings of the Royal Society of London B"] * 2,
            "authors": [["Lisa DeBruine"], ["Lisa DeBruine"]],
        }
    )
    return call("pytacheck.db.crossref.add_bib_match", p, 0)
