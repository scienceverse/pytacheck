"""The LALR tables of R 4.5.3's parser (``src/main/gram.c``), loaded on first use.

The tables are bison's output for R's grammar and live in
``_rparse_tables.json.gz``; ``scripts/gen_rparse_tables.py`` rebuilds that file
from R's ``gram.c``. Reading an attribute of this module (``YYTABLE``,
``YYFINAL``, ...) loads the file once, so importing the parser costs nothing
until it parses something.

``YYFINAL``, ``YYLAST``, ``YYNTOKENS``, ``YYNNTS``, ``YYNRULES``, ``YYNSTATES``,
``YYMAXUTOK``, ``YYPACT_NINF`` and ``YYTABLE_NINF`` are integers; ``YYTRANSLATE``,
``YYPACT``, ``YYDEFACT``, ``YYPGOTO``, ``YYDEFGOTO``, ``YYTABLE``, ``YYCHECK``,
``YYR1`` and ``YYR2`` are tuples of integers; ``YYTNAME`` is a tuple of symbol
names.

Copyright (C) 1997--2025 The R Core Team; GPL (>= 2), as R itself.
"""

from __future__ import annotations

import gzip
import json
from functools import cache
from importlib import resources
from typing import Any


@cache
def _load() -> dict[str, Any]:
    raw = resources.files("metacheck.codecheck").joinpath("_rparse_tables.json.gz").read_bytes()
    tables: dict[str, Any] = json.loads(gzip.decompress(raw))
    return {k: tuple(v) if isinstance(v, list) else v for k, v in tables.items()}


def __getattr__(name: str) -> Any:
    tables = _load()
    if name not in tables:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals().update(tables)  # later reads are plain module attributes
    return tables[name]
