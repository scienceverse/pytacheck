"""Python counterparts of the R expressions the grobid12 parity cases use.

Each helper does what the matching ``$expr`` R code in
``parity/cases/grobid12.yaml`` does, so R and Python results can be compared.
Every conversion passes ``schema_version="12.0"`` explicitly: the parity
harness runs the Python side with metacheck's defaults (the older conversion).
"""

from __future__ import annotations

import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytacheck as pc

ROOT = Path(__file__).resolve().parents[2]


def identity(x: Any) -> Any:
    """R's ``identity()``: the cases compute their value in ``$expr``."""
    return x


def _path(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else ROOT / p


def paths(files: Sequence[str]) -> list[str]:
    """The repository-relative *files* as absolute paths (R's cwd is the repo root)."""
    return [str(_path(f)) for f in files]


def _tempdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="pc_grobid12_"))


def read_lines(path: str | Path) -> list[str]:
    """``readLines(path, encoding = "UTF-8")``."""
    lines = _path(path).read_bytes().decode("utf-8").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def mask_volatile(lines: list[str], completed_at: bool = False) -> list[str]:
    """Blank the converter's name and version (metacheck vs pytacheck), and
    ``completed_at`` when it is the time of the conversion."""
    out = list(lines)
    for i, line in enumerate(lines):
        if line.strip() == '"converter": {':
            out[i + 1] = "<converter name>"
            out[i + 2] = "<converter version>"
        if completed_at and line.lstrip(" ").startswith('"completed_at": '):
            out[i] = "<completed_at>"
    return out


def written_lines(xml_path: str, completed_at: bool = False) -> list[str]:
    """The name and lines of ``grobid_to_bibr(xml_path, <dir>, schema_version = "12.0")``."""
    f = pc.grobid_to_bibr(str(_path(xml_path)), _tempdir(), schema_version="12.0")
    return [Path(f).name, *mask_volatile(read_lines(f), completed_at)]


def write_read(xml_path: str) -> Any:
    """``read(grobid_to_bibr(xml_path, <dir>, schema_version = "12.0"))``."""
    f = pc.grobid_to_bibr(str(_path(xml_path)), _tempdir(), schema_version="12.0")
    return pc.read(f)


def paper_tables(xml_paths: Sequence[str], tables: Sequence[str]) -> dict[str, Any]:
    """``paper_table()`` of each of *tables* over the 12.0 conversions of *xml_paths*."""
    papers = pc.grobid_to_bibr(paths(xml_paths), None, schema_version="12.0")
    return {t: pc.paper_table(papers, t) for t in tables}


def bibr12_utc(x: Sequence[Any]) -> list[str | None]:
    """``vapply(x, .bibr12_utc, "")`` (``.bibr12_utc()`` takes one value)."""
    from pytacheck.io.grobid_bibr12 import _bibr12_utc

    return [_bibr12_utc(v) for v in x]
