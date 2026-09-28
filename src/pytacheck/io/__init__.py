"""Reading papers from files: bibr JSON, Grobid TEI XML, and (via bibr) PDF/DOCX/HTML."""

from pytacheck.io.read import read

__all__ = ["read"]

# Grobid / bibr conversion clients and paper corpora (loaded on first use)
_LAZY_EXPORTS = {
    "convert": "pytacheck.io.convert",
    "convert_bibr": "pytacheck.io.bibr_convert",
    "convert_grobid": "pytacheck.io.grobid",
    "format_bib_authors": "pytacheck.io.bibr_convert",
    "grobid_to_bibr": "pytacheck.io.grobid",
    "papers_available": "pytacheck.io.corpus",
    "papers_load": "pytacheck.io.corpus",
    "papers_metadata": "pytacheck.io.corpus",
    "papers_remove": "pytacheck.io.corpus",
}
__all__ += sorted(_LAZY_EXPORTS)


def __getattr__(name: str) -> object:
    import importlib

    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'pytacheck.io' has no attribute {name!r}")
    return getattr(importlib.import_module(target), name)
