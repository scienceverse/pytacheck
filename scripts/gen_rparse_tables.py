"""Regenerate src/metacheck/codecheck/_rparse_tables.json.gz from R's ``gram.c``.

R's parser is a bison LALR(1) grammar; its tables are static C arrays in
``src/main/gram.c`` of the R sources. ``metacheck.codecheck._rparse`` runs the
same automaton in Python, so a parse error is found at the token R finds it.

    uv run python scripts/gen_rparse_tables.py path/to/gram.c

``gram.c`` of R 4.5.3 (``src/main/gram.c`` in the R sources) gives the tables
that are committed. Copyright (C) 1997--2025 The R Core Team; GPL (>= 2).
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
from pathlib import Path
from typing import Any

OUT = Path(__file__).resolve().parent.parent / "src/metacheck/codecheck/_rparse_tables.json.gz"

# the scalar #defines the parser uses
DEFINES = (
    "YYFINAL",
    "YYLAST",
    "YYNTOKENS",
    "YYNNTS",
    "YYNRULES",
    "YYNSTATES",
    "YYMAXUTOK",
    "YYPACT_NINF",
    "YYTABLE_NINF",
)
# the tables it indexes (bison's names in lower case)
ARRAYS = (
    "YYTRANSLATE",
    "YYPACT",
    "YYDEFACT",
    "YYPGOTO",
    "YYDEFGOTO",
    "YYTABLE",
    "YYCHECK",
    "YYR1",
    "YYR2",
    "YYTNAME",
)


def _array_body(source: str, name: str) -> str:
    m = re.search(
        rf"\bstatic const [\w\s*]+?\b{name.lower()}\[\]\s*=\s*\{{(.*?)\n\}};", source, re.S
    )
    if m is None:
        raise SystemExit(f"table {name.lower()} not found in gram.c")
    return m.group(1)


def _c_string(literal: str) -> str:
    return re.sub(r"\\(.)", lambda m: {"n": "\n"}.get(m.group(1), m.group(1)), literal[1:-1])


def parse(source: str) -> dict[str, Any]:
    tables: dict[str, Any] = {}
    for name in DEFINES:
        m = re.search(rf"^#define {name}\s+\(?(-?\d+)\)?\s*$", source, re.M)
        if m is None:
            raise SystemExit(f"#define {name} not found in gram.c")
        tables[name] = int(m.group(1))
    for name in ARRAYS:
        body = _array_body(source, name)
        if name == "YYTNAME":
            body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
            items = re.findall(r'"(?:[^"\\]|\\.)*"|YY_NULLPTR', body)
            tables[name] = [_c_string(s) for s in items if s != "YY_NULLPTR"]
        else:
            tables[name] = [int(n) for n in re.findall(r"-?\d+", body)]
    return tables


def check(tables: dict[str, Any]) -> None:
    """The array lengths bison's own #defines promise."""
    sizes = {
        "YYTRANSLATE": tables["YYMAXUTOK"] + 1,
        "YYPACT": tables["YYNSTATES"],
        "YYDEFACT": tables["YYNSTATES"],
        "YYPGOTO": tables["YYNNTS"],
        "YYDEFGOTO": tables["YYNNTS"],
        "YYTABLE": tables["YYLAST"] + 1,
        "YYCHECK": tables["YYLAST"] + 1,
        "YYR1": tables["YYNRULES"] + 1,
        "YYR2": tables["YYNRULES"] + 1,
        "YYTNAME": tables["YYNTOKENS"] + tables["YYNNTS"],
    }
    for name, size in sizes.items():
        if len(tables[name]) != size:
            raise SystemExit(f"{name} has {len(tables[name])} entries, expected {size}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("gram_c", type=Path, help="R's src/main/gram.c")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    tables = parse(args.gram_c.read_text(encoding="utf-8"))
    check(tables)
    raw = json.dumps(tables, separators=(",", ":")).encode()
    args.out.write_bytes(gzip.compress(raw, 9, mtime=0))
    print(f"{args.out}: {len(raw)} bytes of JSON, {args.out.stat().st_size} compressed")


if __name__ == "__main__":
    main()
