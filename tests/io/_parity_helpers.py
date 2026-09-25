"""Small Python counterparts of base R helpers used to normalise io_review parity results."""

from __future__ import annotations

import os
from typing import Any


def basename(path: Any) -> Any:
    """R's ``basename()`` (vectorised; ``NA`` stays ``NA``)."""
    if path is None:
        return None
    if isinstance(path, str | os.PathLike):
        return os.path.basename(os.fspath(path))
    return [basename(p) for p in path]


def unname(obj: Any, force: bool = False) -> Any:
    """R's ``unname()``: the values of a named vector (a dict) as a plain list."""
    del force
    if obj is None:
        return None
    return list(obj.values()) if isinstance(obj, dict) else list(obj)


def names(x: Any) -> Any:
    """R's ``names()`` of a named vector (a dict)."""
    return None if x is None else [str(k) for k in x]


_EMPTY_TEI = "tests/io/fixtures/empty_body.tei.xml"
_PROBE_JSON = "upstream/metacheck/tests/testthat/fixtures/bibr12/probe_docx.json"


def _copies(files: dict[str, list[str]]) -> str:
    """A temporary directory holding copies of repository files under new names."""
    import shutil
    import tempfile
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    d = Path(tempfile.mkdtemp(prefix="pc_io_read_"))
    for src, names in files.items():
        for name in names:
            (d / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / src, d / name)
    return str(d)


def read_dir() -> str:
    """A directory for ``read()``'s ``list.files()`` scan (the ``read.dir.*`` cases).

    Hidden files and directories, an upper-case extension, a subdirectory and
    a file named like it, and names whose C and ICU sort orders differ.
    """
    names = ["good.xml", ".hidden.xml", "UP.XML", "Z.xml", "sub/c.xml", ".hid/h.xml", "sub.xml"]
    return _copies({_EMPTY_TEI: names})


def read_list() -> list[str]:
    """Paths for ``read()``: a repeated path, an XML twin of a ``.JSON`` and of a ``.json``."""
    d = _copies({_EMPTY_TEI: ["Z.xml", "p.xml", "q.xml"], _PROBE_JSON: ["p.JSON", "q.json"]})
    return [f"{d}/{n}" for n in ["Z.xml", "p.JSON", "p.xml", "q.json", "q.xml", "Z.xml"]]


def read_file_names() -> dict[str, list[str]]:
    """``info$file_name`` of TEI papers ``read()`` from ``dir/``, ``dir/./p.xml``, ``dir//q.xml``.

    R stores each XML path exactly as ``read()`` got it (``list.files()`` of
    ``"dir/"`` gives ``"dir//p.xml"``); the temporary directory is written ``D``.
    """
    import pytacheck as pc
    from pytacheck.papers import PaperList

    d = _copies({_EMPTY_TEI: ["p.xml", "q.xml"]})

    def fn(x: Any) -> list[str]:
        papers = list(x) if isinstance(x, PaperList) else [x]
        return [str(p.info["file_name"].iloc[0]).replace(d, "D", 1) for p in papers]

    return {
        "dir_slash": fn(pc.read(d + "/", schema_version=None)),
        "dot": fn(pc.read(f"{d}/./p.xml", schema_version=None)),
        "double": fn(pc.read([f"{d}//q.xml", f"{d}/p.xml"], schema_version=None)),
    }


def identity(x: Any) -> Any:
    """R's ``identity()``: the case computes its value in ``$expr``."""
    return x


def read_basenames(file_path: Any, recursive: bool = False) -> Any:
    """``read(file_path, recursive)`` with each paper's ``info$file_name`` basename'd."""
    import pytacheck as pc

    papers = pc.read(file_path, recursive=recursive, schema_version=None)
    for p in papers:
        info = p.info.copy()
        info["file_name"] = info["file_name"].map(basename).astype(info["file_name"].dtype)
        p.info = info
    return papers
