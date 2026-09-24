"""Loading parity cases and running their Python side.

A case file ``parity/cases/<area>.yaml`` looks like::

    area: text
    cases:
      - id: text_search.demo.significant   # golden: parity/golden/text/<id>.json
        r: text_search                     # function in the metacheck namespace
        py: pytacheck.text.text_search     # dotted path of the Python port
        args:                              # R argument names; see below
          paper: {$paper: demo}
          pattern: significant
          ignore.case: false
        compare: {unordered: true}         # see parity.compare
      - id: marginal.demo
        module: marginal                   # module_run(paper, "marginal", ...)
        args: {paper: {$paper: demo}}

Argument names are translated for Python by replacing ``.`` with ``_`` and
appending ``_`` to Python keywords (``return`` -> ``return_``). ``py_args``
adds or overrides Python arguments and ``py_drop`` removes some.

Argument constructors (a one-key mapping whose key starts with ``$``):

``$paper: demo | <path>``      demopaper() / read(path)   (paths relative to repo root)
``$read: [<path>, ...]``        read() of several files -> paper list
``$test_paper: {text, url}``    test_paper(text, url)
``$df: {col: [...], ...}``      a data frame (``null`` -> NA)
``$chr|$int|$dbl|$lgl: [...]``  a typed vector (use for empty or length-1 vectors)
``$list: [...]``                an unnamed list
``$null: true`` / ``$NA: true`` NULL / NA
``$file: <path>``               an absolute path string
``$expr: {r: <code>, py: <code>}``  escape hatch; py code sees ``pc`` (pytacheck), ``pd``, ``np``
``$call: {r: fn, py: dotted, args: {...}}``  the result of another call

A case may set ``known_divergence: <reason>`` to record an intentional,
documented difference from R (the test is then an expected failure).

``mock_dir: apis`` runs the case against recorded HTTP responses on both
sides: R inside ``httptest2::with_mock_dir()``, Python inside
``tests.httpmock.replay()``. Relative names are metacheck test mock
directories (``apis``, ``apis_papers_retag``, ...); paths starting with
``tests/`` or ``parity/`` are relative to this repository.
"""

from __future__ import annotations

import importlib
import keyword
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CASES_DIR = ROOT / "parity" / "cases"
GOLDEN_DIR = ROOT / "parity" / "golden"


@dataclass
class Case:
    area: str
    id: str
    spec: dict[str, Any]
    file: Path

    @property
    def golden_path(self) -> Path:
        return GOLDEN_DIR / self.area / f"{self.id}.json"

    @property
    def key(self) -> str:
        return f"{self.area}/{self.id}"


def iter_case_files(area: str | None = None) -> Iterator[Path]:
    for f in sorted(CASES_DIR.glob("*.yaml")):
        if area is None or f.stem == area:
            yield f


def load_cases(area: str | None = None) -> list[Case]:
    cases: list[Case] = []
    seen: set[str] = set()
    for f in iter_case_files(area):
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        a = data.get("area", f.stem)
        for spec in data.get("cases", []) or []:
            case = Case(area=a, id=str(spec["id"]), spec=spec, file=f)
            if case.key in seen:
                raise ValueError(f"duplicate parity case id {case.key}")
            seen.add(case.key)
            cases.append(case)
    return cases


def _resolve(dotted: str) -> Any:
    module_name, _, attr = dotted.rpartition(".")
    obj = importlib.import_module(module_name)
    return getattr(obj, attr)


def py_name(r_name: str) -> str:
    name = r_name.replace(".", "_")
    return f"{name}_" if keyword.iskeyword(name) else name


def decode(x: Any) -> Any:
    """Decode a YAML argument spec into a Python value."""
    import numpy as np
    import pandas as pd

    import pytacheck as pc

    if isinstance(x, dict) and len(x) == 1 and next(iter(x)).startswith("$"):
        key, val = next(iter(x.items()))
        if key == "$paper":
            return pc.demopaper() if val == "demo" else pc.read(ROOT / val)
        if key == "$read":
            vals = val if isinstance(val, list) else [val]
            papers = pc.read([ROOT / v for v in vals])
            return papers if isinstance(papers, pc.PaperList) else pc.PaperList([papers])
        if key == "$test_paper":
            val = val or {}
            return pc.test_paper(val.get("text"), val.get("url") or [])
        if key == "$df":
            return pd.DataFrame(
                {k: [None if v is None else v for v in col] for k, col in val.items()}
            )
        if key in ("$chr", "$int", "$dbl", "$lgl"):
            vals = val if isinstance(val, list) else [val]
            conv = {"$chr": str, "$int": int, "$dbl": float, "$lgl": bool}[key]
            return [None if v is None else conv(v) for v in vals]
        if key == "$list":
            return [decode(v) for v in val]
        if key == "$null":
            return None
        if key == "$NA":
            return None
        if key == "$file":
            return str(ROOT / val)
        if key == "$expr":
            return eval(val["py"], {"pc": pc, "pd": pd, "np": np})
        if key == "$call":
            fn = _resolve(val["py"])
            return fn(**decode_args(val.get("args") or {}, val.get("py_args"), val.get("py_drop")))
        raise ValueError(f"unknown argument constructor {key}")
    if isinstance(x, dict):
        return {k: decode(v) for k, v in x.items()}
    return x


def decode_args(
    args: dict[str, Any], py_args: dict[str, Any] | None = None, py_drop: list[str] | None = None
) -> dict[str, Any]:
    out = {py_name(k): decode(v) for k, v in args.items()}
    for k in py_drop or []:
        out.pop(py_name(k), None)
    for k, v in (py_args or {}).items():
        out[k] = decode(v)
    return out


def _mock_dir(spec: dict[str, Any]) -> Any:
    """``replay()`` context for a case's ``mock_dir`` (a no-op without one)."""
    import contextlib

    d = spec.get("mock_dir")
    if not d:
        return contextlib.nullcontext()
    from tests.httpmock import replay

    path = Path(d)
    if not path.is_absolute() and (d.startswith("tests/") or d.startswith("parity/")):
        path = ROOT / d
    return replay(path if path.is_absolute() else d)


def run_python(case: Case) -> Any:
    """Run the Python side of *case* and return its result."""
    with _mock_dir(case.spec):
        return _run_python(case)


def _run_python(case: Case) -> Any:
    spec = case.spec
    args = decode_args(spec.get("args") or {}, spec.get("py_args"), spec.get("py_drop"))
    if "module" in spec:
        from pytacheck.module import module_run

        paper = args.pop("paper")
        return module_run(paper, spec["module"], **args)
    fn = _resolve(spec["py"])
    return fn(**args)
