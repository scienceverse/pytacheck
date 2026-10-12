"""Reading papers from files: bibr JSON, Grobid TEI XML, and (via bibr) PDF/DOCX/HTML."""

from metacheck.io.read import read

__all__ = ["read"]

# Grobid / bibr conversion clients and paper corpora (loaded on first use)
_LAZY_EXPORTS = {
    "convert": "metacheck.io.convert",
    "convert_bibr": "metacheck.io.bibr_convert",
    "convert_grobid": "metacheck.io.grobid",
    "format_bib_authors": "metacheck.io.bibr_convert",
    "grobid_to_bibr": "metacheck.io.grobid",
    "papers_available": "metacheck.io.corpus",
    "papers_load": "metacheck.io.corpus",
    "papers_metadata": "metacheck.io.corpus",
    "papers_remove": "metacheck.io.corpus",
}
__all__ += sorted(_LAZY_EXPORTS)


def __getattr__(name: str) -> object:
    import importlib

    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'metacheck.io' has no attribute {name!r}")
    return getattr(importlib.import_module(target), name)
