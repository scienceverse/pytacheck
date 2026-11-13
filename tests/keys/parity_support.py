"""Python side of the keys parity helpers (``tests/keys/helpers.R``).

Nothing here returns a key itself: the tables hold only the type of key and the beginning
of it.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pandas as pd


def identity(x: Any) -> Any:
    return x


def _dir(files: Mapping[str, Sequence[str]]) -> Path:
    d = Path(tempfile.mkdtemp(prefix="kp"))
    for n, lines in files.items():
        f = d / n
        f.parent.mkdir(parents=True, exist_ok=True)
        # writeLines(): every line ends with a line break
        f.write_text("".join(f"{ln}\n" for ln in lines), encoding="utf-8", newline="\n")
    return d


def kp_scan(files: Mapping[str, Sequence[str]]) -> dict[str, Any]:
    from metacheck.keys import _key_scan_files

    d = _dir(files)
    try:
        f, ns = _key_scan_files([str(d / n) for n in files], names=list(files))
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return {"keys": f, "not_searched": ns}


def kp_find(text: str | None, path: str | None = None) -> pd.DataFrame:
    from metacheck.keys import _key_find

    return _key_find(text, path=path).drop(columns=["key"])


def kp_remove(x: Any) -> dict[str, Any]:
    from metacheck.keys import _key_remove

    out, n = _key_remove(x)
    return {"out": out, "n": n}


def kp_tables(tables: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    from metacheck.keys import _key_scan_tables

    return _key_scan_tables(tables)


def kp_data_check(
    files: Mapping[str, Sequence[str]], data: Mapping[str, pd.DataFrame] | None = None
) -> dict[str, Any]:
    import re

    import metacheck as pc
    from metacheck.module import module_run

    d = _dir(files)
    try:
        for n, df in (data or {}).items():
            df.to_csv(d / n, index=False)
        mo = module_run(pc.test_paper("x"), "data_check", local_path=str(d), local_only=True)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    rep = "\n".join(str(r) for r in (mo.report if isinstance(mo.report, list) else [mo.report]))
    found = re.search(r"We found [0-9]+ keys?", str(mo.summary_text))
    return {
        "keys": mo.extras["keys"],
        "traffic_light": mo.traffic_light,
        "key_n": mo.summary_table["key_n"],
        "has_block": "Keys in the repository" in rep,
        "summary_found": [found.group(0)] if found else [],
    }


def kp_rules() -> dict[str, Any]:
    from metacheck.keys import _key_rules

    x = _key_rules()
    return {
        "n": len(x.patterns),
        "names": [p.name for p in x.patterns],
        "confidence": [p.confidence for p in x.patterns],
    }
