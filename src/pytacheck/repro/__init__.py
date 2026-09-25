"""Computational reproducibility: can a paper's code be run on its data?

Ports of metacheck's ``R/reproducibility_check.R`` (static analysis of a
paper's scripts and the execution helpers), ``R/reproducibility_check_docker.R``
(the Docker sandbox backend) and the module-table capture of ``R/module.R``
(``capture_module_tables()``, ``collect_module_tables()``,
``.load_module_tables()``):

* static helpers -- :func:`repro_dependencies`, :func:`repro_rewrite_paths`,
  :func:`repro_run_order`, :func:`repro_file_io`, :func:`repro_defined_vars`,
  :func:`repro_missing_inputs`;
* execution helpers -- :func:`repro_materialize_layout`,
  :func:`repro_write_scripts`, :func:`repro_install_deps`,
  :func:`repro_run_scripts` (authors' R code runs in an ``Rscript``
  subprocess, the analogue of metacheck's ``callr::r()``);
* Docker backend -- :func:`repro_docker_available`,
  :func:`repro_install_deps_docker`, :func:`repro_run_scripts_docker`;
* module tables -- :func:`capture_module_tables`, :func:`collect_module_tables`.

Submodules are imported lazily, so ``import pytacheck.repro`` stays cheap.
"""

from __future__ import annotations

import importlib
from typing import Any

_LAZY = {
    "repro_dependencies": "pytacheck.repro.core",
    "repro_rewrite_paths": "pytacheck.repro.core",
    "repro_run_order": "pytacheck.repro.core",
    "repro_file_io": "pytacheck.repro.core",
    "repro_defined_vars": "pytacheck.repro.core",
    "repro_missing_inputs": "pytacheck.repro.core",
    "repro_materialize_layout": "pytacheck.repro.core",
    "repro_write_scripts": "pytacheck.repro.core",
    "repro_install_deps": "pytacheck.repro.core",
    "repro_run_scripts": "pytacheck.repro.core",
    "repro_docker_available": "pytacheck.repro.docker",
    "repro_install_deps_docker": "pytacheck.repro.docker",
    "repro_run_scripts_docker": "pytacheck.repro.docker",
    "capture_module_tables": "pytacheck.repro.tables",
    "collect_module_tables": "pytacheck.repro.tables",
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
