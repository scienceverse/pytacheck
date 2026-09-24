"""Bibliographic databases and registries (port of metacheck's ``R/doi.R``,
``R/db-*.R``, ``R/regcheck-local.R`` and ``R/svutils-orcid.R``).

* DOIs: :func:`doi_clean`, :func:`doi_valid_format`, :func:`doi_resolves`,
  :func:`doi_lookup`
* Crossref / DataCite / OpenAlex: :func:`crossref_doi`,
  :func:`crossref_query`, :func:`add_bib_match`, :func:`datacite_doi`,
  :func:`openalex_doi`, :func:`openalex_query`
* PubPeer: :func:`pubpeer_comments`
* bundled databases: :func:`retractionwatch` (:func:`rw`), :func:`rw_date`,
  :func:`rw_update`, :func:`FLoRA`, :func:`FLoRA_date`, :func:`FLoRA_update`,
  :func:`miscite`
* ORCiD / CRediT: :func:`check_orcid`, :func:`get_orcid`,
  :func:`orcid_person`, :func:`credit_roles`
* RegCheck: :func:`regcheck_compare`, :func:`regcheck_tidy`,
  :func:`regcheck_base_url`, :func:`regcheck_setup_local`,
  :func:`regcheck_start_local`, :func:`regcheck_stop_local`

Names are imported lazily, so ``import pytacheck.db`` is cheap.
"""

from __future__ import annotations

import importlib
from typing import Any

_EXPORTS: dict[str, str] = {
    "doi_clean": "pytacheck.db.doi",
    "doi_lookup": "pytacheck.db.doi",
    "doi_resolves": "pytacheck.db.doi",
    "doi_valid_format": "pytacheck.db.doi",
    "add_bib_match": "pytacheck.db.crossref",
    "crossref_doi": "pytacheck.db.crossref",
    "crossref_query": "pytacheck.db.crossref",
    "datacite_doi": "pytacheck.db.crossref",
    "openalex_doi": "pytacheck.db.crossref",
    "openalex_query": "pytacheck.db.crossref",
    "pubpeer_comments": "pytacheck.db.pubpeer",
    "retractionwatch": "pytacheck.db.retractionwatch",
    "rw": "pytacheck.db.retractionwatch",
    "rw_date": "pytacheck.db.retractionwatch",
    "rw_update": "pytacheck.db.retractionwatch",
    "FLoRA": "pytacheck.db.replications",
    "FLoRA_date": "pytacheck.db.replications",
    "FLoRA_update": "pytacheck.db.replications",
    "miscite": "pytacheck.db.miscite",
    "check_orcid": "pytacheck.db.orcid",
    "credit_roles": "pytacheck.db.orcid",
    "get_orcid": "pytacheck.db.orcid",
    "orcid_person": "pytacheck.db.orcid",
    "RegCheckError": "pytacheck.db.regcheck",
    "regcheck_base_url": "pytacheck.db.regcheck",
    "regcheck_compare": "pytacheck.db.regcheck",
    "regcheck_tidy": "pytacheck.db.regcheck",
    "regcheck_setup_local": "pytacheck.db.regcheck_local",
    "regcheck_start_local": "pytacheck.db.regcheck_local",
    "regcheck_stop_local": "pytacheck.db.regcheck_local",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'pytacheck.db' has no attribute {name!r}")
    value = getattr(importlib.import_module(target), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))
