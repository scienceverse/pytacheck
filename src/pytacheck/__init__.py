"""pytacheck: check research outputs for best practices.

A Python-native, parity-tested port of ScienceVerse's `metacheck
<https://github.com/scienceverse/metacheck>`_ R package, built to work
hand in hand with `bibr <https://bibr.org>`_.

.. code-block:: python

    import pytacheck as pc

    paper = pc.read("paper.json")            # bibr JSON, Grobid XML, or a PDF via bibr
    out = pc.module_run(paper, "all_p_values")
    out.table

Public names are imported lazily so ``import pytacheck`` stays fast.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from pytacheck._version import UPSTREAM, __version__

# name -> module that defines it. Keep sorted by module.
_EXPORTS: dict[str, str] = {
    # configuration & logging
    "cache_dir": "pytacheck.config",
    "email": "pytacheck.config",
    "verbose": "pytacheck.config",
    "lastlog": "pytacheck.log",
    "logger": "pytacheck.log",
    "logpath": "pytacheck.log",
    # reading
    "read": "pytacheck.io.read",
    "chew": "pytacheck.io.bibr",
    # module system
    "ModuleError": "pytacheck.module",
    "ModuleOutput": "pytacheck.module",
    "ModuleSpec": "pytacheck.module",
    "get_prev_outputs": "pytacheck.module",
    "module": "pytacheck.module",
    "module_help": "pytacheck.module",
    "module_info": "pytacheck.module",
    "module_list": "pytacheck.module",
    "module_run": "pytacheck.module",
    "run_session": "pytacheck.module",
    "use": "pytacheck.module",
    # packs & presets (module system v2; pytacheck.packs is only imported on use)
    "CheckIssue": "pytacheck.packs.check",
    "pack_check": "pytacheck.packs.check",
    "pack_install": "pytacheck.packs.install",
    "pack_list": "pytacheck.packs.install",
    "pack_remove": "pytacheck.packs.install",
    "pack_show": "pytacheck.packs.install",
    "pack_update": "pytacheck.packs.install",
    "refresh": "pytacheck.packs.registry",
    "module_template": "pytacheck.packs.scaffold",
    "pack_new": "pytacheck.packs.scaffold",
    "store_add": "pytacheck.packs.stores",
    "store_list": "pytacheck.packs.stores",
    "store_remove": "pytacheck.packs.stores",
    "store_search": "pytacheck.packs.stores",
    "store_update": "pytacheck.packs.stores",
    "preset": "pytacheck.presets",
    "preset_list": "pytacheck.presets",
    # run records and reproducibility
    "ModuleChain": "pytacheck.provenance",
    "RunRecord": "pytacheck.provenance",
    "rerun": "pytacheck.provenance",
    "run_modules": "pytacheck.provenance",
    # papers
    "Paper": "pytacheck.papers",
    "PaperList": "pytacheck.papers",
    "PaperValidationError": "pytacheck.papers",
    "demofile": "pytacheck.papers",
    "demopaper": "pytacheck.papers",
    "from_bibr": "pytacheck.papers",
    "paper": "pytacheck.papers",
    "paper_id": "pytacheck.papers",
    "paper_table": "pytacheck.papers",
    "paper_validate": "pytacheck.papers",
    "paper_write": "pytacheck.papers",
    "read_bibr": "pytacheck.papers",
    "ref_table": "pytacheck.papers",
    "test_paper": "pytacheck.papers",
    # reports and validation
    "emojis": "pytacheck.report.emojis",
    "module_report": "pytacheck.report.report",
    "report": "pytacheck.report.report",
    "report_module_run": "pytacheck.report.report",
    "report_qmd": "pytacheck.report.report",
    "report_repository": "pytacheck.report.report",
    "report_table": "pytacheck.report.blocks",
    "accuracy": "pytacheck.validate",
    "validate": "pytacheck.validate",
    # statistics
    "stats": "pytacheck.stats.core",
    "statcheck": "pytacheck.stats.statcheck",
    # text
    "causal_relations": "pytacheck.text",
    "expand_text": "pytacheck.text",
    "extract_eq": "pytacheck.text",
    "extract_p_values": "pytacheck.text",
    "extract_tests": "pytacheck.text",
    "extract_urls": "pytacheck.text",
    "json_expand": "pytacheck.text",
    "search_text": "pytacheck.text",
    "text_expand": "pytacheck.text",
    "text_search": "pytacheck.text",
}

__all__ = ["UPSTREAM", "__version__", *sorted(_EXPORTS)]


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'pytacheck' has no attribute {name!r}")
    value = getattr(importlib.import_module(target), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))


if TYPE_CHECKING:  # pragma: no cover
    from pytacheck.config import cache_dir, email, verbose
    from pytacheck.io.bibr import chew
    from pytacheck.io.read import read
    from pytacheck.log import lastlog, logger, logpath
    from pytacheck.module import (
        ModuleError,
        ModuleOutput,
        ModuleSpec,
        get_prev_outputs,
        module,
        module_help,
        module_info,
        module_list,
        module_run,
        run_session,
        use,
    )
    from pytacheck.packs.check import CheckIssue, pack_check
    from pytacheck.packs.install import (
        pack_install,
        pack_list,
        pack_remove,
        pack_show,
        pack_update,
    )
    from pytacheck.packs.registry import refresh
    from pytacheck.packs.scaffold import module_template, pack_new
    from pytacheck.packs.stores import (
        store_add,
        store_list,
        store_remove,
        store_search,
        store_update,
    )
    from pytacheck.papers import (
        Paper,
        PaperList,
        PaperValidationError,
        demofile,
        demopaper,
        from_bibr,
        paper,
        paper_id,
        paper_table,
        paper_validate,
        paper_write,
        read_bibr,
        ref_table,
        test_paper,
    )
    from pytacheck.presets import preset, preset_list
    from pytacheck.provenance import ModuleChain, RunRecord, rerun, run_modules
    from pytacheck.report.blocks import report_table
    from pytacheck.report.emojis import emojis
    from pytacheck.report.report import (
        module_report,
        report,
        report_module_run,
        report_qmd,
        report_repository,
    )
    from pytacheck.stats.core import stats
    from pytacheck.stats.statcheck import statcheck
    from pytacheck.text import (
        causal_relations,
        expand_text,
        extract_eq,
        extract_p_values,
        extract_tests,
        extract_urls,
        json_expand,
        search_text,
        text_expand,
        text_search,
    )
    from pytacheck.validate import accuracy, validate
