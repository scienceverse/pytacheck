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
    # text
    "expand_text": "pytacheck.text",
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
    from pytacheck.text import expand_text, search_text, text_expand, text_search
