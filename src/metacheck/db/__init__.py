"""Bibliographic databases and registries (port of metacheck's ``R/doi.R``,
``R/db-*.R``, ``R/regcheck-local.R`` and ``R/svutils-orcid.R``).

* DOIs: :func:`doi_clean`, :func:`doi_valid_format`, :func:`doi_resolves`,
  :func:`doi_lookup`
* Crossref / DataCite / OpenAlex: :func:`crossref_doi`,
  :func:`crossref_query`, :func:`add_bib_match`, :func:`datacite_doi`,
  :func:`openalex_doi`, :func:`openalex_query`
* PubPeer: :func:`pubpeer_comments`
* bundled databases: ``retractionwatch.retractionwatch`` (:func:`rw`), :func:`rw_date`,
  :func:`rw_update`, :func:`FLoRA`, :func:`FLoRA_date`, :func:`FLoRA_update`,
  :func:`miscite`
* ORCiD / CRediT: :func:`check_orcid`, :func:`get_orcid`,
  :func:`orcid_person`, :func:`credit_roles`
* RegCheck: :func:`regcheck_compare`, :func:`regcheck_tidy`,
  :func:`regcheck_base_url`, :func:`regcheck_setup_local`,
  :func:`regcheck_start_local`, :func:`regcheck_stop_local`

Names are imported lazily, so ``import metacheck.db`` is cheap. The
``retractionwatch()`` function is reached as ``metacheck.retractionwatch`` or
``metacheck.db.retractionwatch.retractionwatch`` (``metacheck.db.retractionwatch``
is its module).
"""

from __future__ import annotations

import importlib
from typing import Any

_EXPORTS: dict[str, str] = {
    "doi_clean": "metacheck.db.doi",
    "doi_lookup": "metacheck.db.doi",
    "doi_resolves": "metacheck.db.doi",
    "doi_valid_format": "metacheck.db.doi",
    "add_bib_match": "metacheck.db.crossref",
    "crossref_doi": "metacheck.db.crossref",
    "crossref_query": "metacheck.db.crossref",
    "datacite_doi": "metacheck.db.crossref",
    "openalex_doi": "metacheck.db.crossref",
    "openalex_query": "metacheck.db.crossref",
    "pubpeer_comments": "metacheck.db.pubpeer",
    "rw": "metacheck.db.retractionwatch",
    "rw_date": "metacheck.db.retractionwatch",
    "rw_update": "metacheck.db.retractionwatch",
    "FLoRA": "metacheck.db.replications",
    "FLoRA_date": "metacheck.db.replications",
    "FLoRA_update": "metacheck.db.replications",
    "miscite": "metacheck.db.databases",
    "check_orcid": "metacheck.db.orcid",
    "credit_roles": "metacheck.db.orcid",
    "get_orcid": "metacheck.db.orcid",
    "orcid_person": "metacheck.db.orcid",
    "RegCheckError": "metacheck.db.regcheck",
    "regcheck_base_url": "metacheck.db.regcheck",
    "regcheck_compare": "metacheck.db.regcheck",
    "regcheck_tidy": "metacheck.db.regcheck",
    "regcheck_setup_local": "metacheck.db.regcheck_local",
    "regcheck_start_local": "metacheck.db.regcheck_local",
    "regcheck_stop_local": "metacheck.db.regcheck_local",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'metacheck.db' has no attribute {name!r}")
    value = getattr(importlib.import_module(target), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))
