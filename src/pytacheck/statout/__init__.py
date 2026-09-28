"""Statistical software output: readers, tables and matching.

Readers for the output formats of statistics packages (ports of metacheck's
``R/spv.R``, ``R/jasp.R``, ``R/omv.R``, ``R/stata.R`` and ``R/mplus.R``):

* SPSS Viewer ``.spv`` -- :func:`import_spv`, :func:`export_spv_html`;
* JASP ``.jasp`` -- :func:`import_jasp`, :func:`export_jasp_html`;
* jamovi ``.omv`` -- :func:`import_omv`, :func:`export_omv_html`;
* Stata ``.smcl`` logs -- :func:`import_stata_smcl`, :func:`export_stata_smcl_html`;
* Mplus ``.out`` -- :func:`import_mplus_output`, :func:`export_mplus_html`.

Submodules are imported lazily, so ``import pytacheck.statout`` stays cheap.
"""

from __future__ import annotations

import importlib
from typing import Any

_LAZY = {
    "import_spv": "pytacheck.statout.spv",
    "export_spv_html": "pytacheck.statout.spv",
    "spv_assemble_table": "pytacheck.statout.spv",
    "import_jasp": "pytacheck.statout.jasp",
    "export_jasp_html": "pytacheck.statout.jasp",
    "import_omv": "pytacheck.statout.omv",
    "export_omv_html": "pytacheck.statout.omv",
    "import_stata_smcl": "pytacheck.statout.stata",
    "export_stata_smcl_html": "pytacheck.statout.stata",
    "import_mplus_output": "pytacheck.statout.mplus",
    "export_mplus_html": "pytacheck.statout.mplus",
}

__all__ = sorted(_LAZY)


def __getattr__(name: str) -> Any:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY})
