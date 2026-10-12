"""The compressed LALR tables of R's parser and the script that rebuilds them from gram.c."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from metacheck.codecheck import _rparse_tables as T

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "gen_rparse_tables.py"
SIZES = {
    "YYTRANSLATE": lambda t: t.YYMAXUTOK + 1,
    "YYPACT": lambda t: t.YYNSTATES,
    "YYDEFACT": lambda t: t.YYNSTATES,
    "YYPGOTO": lambda t: t.YYNNTS,
    "YYDEFGOTO": lambda t: t.YYNNTS,
    "YYTABLE": lambda t: t.YYLAST + 1,
    "YYCHECK": lambda t: t.YYLAST + 1,
    "YYR1": lambda t: t.YYNRULES + 1,
    "YYR2": lambda t: t.YYNRULES + 1,
    "YYTNAME": lambda t: t.YYNTOKENS + t.YYNNTS,
}


@pytest.mark.parametrize("name", sorted(SIZES))
def test_table_sizes_match_bisons_defines(name: str) -> None:
    table = getattr(T, name)
    assert isinstance(table, tuple)
    assert len(table) == SIZES[name](T)


def test_known_values() -> None:
    assert (T.YYFINAL, T.YYNSTATES, T.YYPACT_NINF, T.YYTABLE_NINF) == (48, 174, -130, -1)
    assert T.YYTNAME[:3] == ('"end of file"', "error", '"invalid token"')
    assert "'\\n'" in T.YYTNAME
    with pytest.raises(AttributeError):
        T.NOT_A_TABLE  # noqa: B018


def _gram_c(tables: dict[str, Any]) -> str:
    """The parts of bison's gram.c the script reads, written the way bison formats them."""
    out = [
        f"#define {k}  ({v})" if v < 0 else f"#define {k}  {v}"
        for k, v in tables.items()
        if isinstance(v, int)
    ]
    for name, values in tables.items():
        if isinstance(values, int):
            continue
        if name == "YYTNAME":
            body = ",\n".join(
                "  " + '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"' for v in values
            )
            body += ",\n  YY_NULLPTR"
            out.append(f"static const char *const {name.lower()}[] =\n{{\n{body}\n}};")
        else:
            rows = [
                ", ".join(f"{v:6d}" for v in values[i : i + 10]) for i in range(0, len(values), 10)
            ]
            out.append(
                f"static const yytype_int16 {name.lower()}[] =\n{{\n  "
                + ",\n  ".join(rows)
                + "\n};"
            )
    return "\n\n".join(out)


def test_script_reads_gram_c() -> None:
    spec = importlib.util.spec_from_file_location("gen_rparse_tables", SCRIPT)
    assert spec is not None and spec.loader is not None
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    tables = {k: (list(v) if isinstance(v, tuple) else v) for k, v in T._load().items()}
    parsed = gen.parse(_gram_c(tables))
    assert parsed == tables
    gen.check(parsed)
