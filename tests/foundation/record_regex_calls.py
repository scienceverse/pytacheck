"""Record the metacheck._r regex calls of a realistic run, for test_regex_replay.py.

Reads the fixture papers (metacheck's formats, problems, debruine, psychsci and
bibr12 fixtures and the demo paper) and runs the offline paper modules (on the
list and per paper), the extractors, statcheck and text_search on them with the
network blocked. Every call of a metacheck._r regex function, and of a method of a
pattern compile_r() returned, is recorded with its result: up to 400 distinct
argument sets per function, pattern and flags (2000 per compiled pattern).

The stored results are the reference. The committed recording was made with the
TRE/PCRE-faithful engine that the parity suite checked against R before lane 2
replaced it (and that engine's results were identical to today's). Re-record
only to add calls that new module code makes, after checking that the existing
calls still give the stored results:

    python tests/foundation/record_regex_calls.py
"""

from __future__ import annotations

import glob
import hashlib
import json
import lzma
import os
import socket
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(__file__).with_name("data") / "regex_calls.json.xz"
FUNCTIONS = [
    "grepl",
    "grep",
    "sub",
    "gsub",
    "regextract",
    "regextract_all",
    "gregexpr_all",
    "regexec",
    "strsplit",
]
METHODS = ["search", "match", "fullmatch", "sub", "subn", "finditer", "findall", "split"]
MODULES = [
    "all_urls",
    "coi_check",
    "coi_check_oi",
    "ethics_check",
    "funding_check",
    "funding_check_oi",
    "open_practices",
    "causal_claims",
    "power",
    "all_p_values",
    "marginal",
    "stat_check",
    "stat_effect_size",
    "stat_p_exact",
    "stat_p_nonsig",
    "ref_consistency",
    "ref_miscitation",
    "ref_replication",
    "ref_retraction",
    "ref_summary",
    "prereg_check",
    "ref_pubpeer",
    "ref_accuracy",
]
PER_PAPER = [
    "coi_check",
    "ethics_check",
    "funding_check",
    "open_practices",
    "power",
    "stat_check",
    "stat_p_exact",
    "stat_p_nonsig",
    "marginal",
    "causal_claims",
    "stat_effect_size",
    "all_p_values",
    "all_urls",
]
SEARCHES = [
    r"significant",
    r"\bp\s*[<>=]\s*0?\.\d+",
    r"(power|sample size)",
    r"[[:alpha:]]+ et al\.",
    r"osf\.io/[a-z0-9]{5}",
    r"\<data\>",
]


def plain(v: Any) -> Any:
    """A JSON-able value: Series and tuples as lists, NaN and NA as None."""
    import pandas as pd

    if isinstance(v, pd.Series):
        v = v.tolist()
    if isinstance(v, list | tuple):
        return [plain(x) for x in v]
    if v is pd.NA or (isinstance(v, float) and v != v):
        return None
    return v


def method_result(name: str, r: Any) -> Any:
    """What a compiled pattern's method returned, as plain data."""
    if name == "finditer":
        return [[list(m.span()), m.group(0)] for m in r]
    if name in ("search", "match", "fullmatch"):
        return None if r is None else [list(r.span()), plain(r.groups())]
    return plain(r)


def fixture_papers() -> list[str]:
    fix = ROOT / "upstream/metacheck/tests/testthat/fixtures"
    out = [
        f
        for d in ["formats", "problems", "debruine", "psychsci", "bibr12"]
        for f in sorted(glob.glob(str(fix / d / "*")))
        if f.endswith((".xml", ".json")) and "schema" not in f and "corrupt" not in f
    ]
    return [*out, str(ROOT / "upstream/metacheck/inst/demos/to_err_is_human.xml")]


def run_workload() -> None:
    """The realistic run (module errors are part of it and ignored)."""

    def no_network(*a: Any, **k: Any) -> Any:
        raise OSError("network disabled")

    socket.create_connection = no_network  # type: ignore[assignment]
    socket.socket.connect = no_network  # type: ignore[method-assign]
    os.environ["PYTACHECK_CONFIG"] = "none"
    os.environ["PYTACHECK_NO_SLEEP"] = "1"
    import metacheck as pc

    papers = [pc.read(f) for f in fixture_papers()]
    plist = pc.PaperList(papers)
    runs: list[Any] = [(plist, m) for m in MODULES]
    runs += [(p, m) for m in PER_PAPER for p in papers]
    for x, m in runs:
        try:
            pc.module_run(x, m)
        except Exception:
            continue
    for fn in [
        "extract_p_values",
        "extract_urls",
        "extract_tests",
        "causal_relations",
        "statcheck",
    ]:
        try:
            getattr(pc, fn)(plist)
        except Exception:
            continue
    for pattern in SEARCHES:
        pc.text_search(plist, pattern)
        pc.text_search(plist, pattern, return_="match")


def record() -> dict[tuple[Any, ...], dict[str, Any]]:
    """Run the workload with every regex entry point wrapped; the recorded calls."""
    import metacheck._r as rpkg
    import metacheck._r.base as rbase
    import metacheck._r.regex as rx

    calls: dict[tuple[Any, ...], dict[str, Any]] = defaultdict(dict)

    def keep(key: tuple[Any, ...], cap: int, args: Any, kwargs: Any, result: Any) -> None:
        seen = calls[key]
        if len(seen) < cap:
            digest = hashlib.md5(
                repr((args, kwargs)).encode("utf-8", "surrogatepass"), usedforsecurity=False
            ).hexdigest()
            spec = list(key[1:]) if key[0].startswith("compile_r.") else None
            seen.setdefault(digest, [key[0], spec, args, kwargs, result])

    def wrap(name: str, fn: Any) -> Any:
        def recorded(*a: Any, **k: Any) -> Any:
            result = fn(*a, **k)
            args, kwargs = plain(a), {kk: plain(vv) for kk, vv in k.items()}
            scalars = [repr(v) for v in args if not isinstance(v, list)]
            scalars += [
                f"{kk}={vv!r}" for kk, vv in sorted(kwargs.items()) if not isinstance(vv, list)
            ]
            keep((name, *scalars), 400, args, kwargs, plain(result))
            return result

        return recorded

    class Recorded:
        def __init__(self, compiled: Any, spec: tuple[Any, ...]) -> None:
            self._compiled, self._spec = compiled, spec

        def __getattr__(self, attr: str) -> Any:
            method = getattr(self._compiled, attr)
            if attr not in METHODS:
                return method

            def call(*a: Any, **k: Any) -> Any:
                result = method(*a, **k)
                if attr == "finditer":
                    result = list(result)
                if not any(callable(v) for v in (*a, *k.values())):
                    keep(
                        ("compile_r." + attr, *self._spec),
                        2000,
                        plain(a),
                        {kk: plain(vv) for kk, vv in k.items()},
                        method_result(attr, result),
                    )
                return iter(result) if attr == "finditer" else result

            return call

    compile_r = rx.compile_r

    def recorded_compile(
        pattern: str,
        ignore_case: bool = False,
        perl: bool = False,
        fixed: bool = False,
        posix: bool | None = None,
        wide: bool = False,
    ) -> Any:
        compiled = compile_r(pattern, ignore_case, perl, fixed, posix, wide)
        return Recorded(compiled, (pattern, ignore_case, perl, fixed, posix))

    for name in FUNCTIONS:
        wrapped = wrap(name, getattr(rx, name))
        setattr(rx, name, wrapped)
        setattr(rpkg, name, wrapped)
    rx.compile_r = rpkg.compile_r = rbase.compile_r = recorded_compile  # type: ignore[assignment]
    run_workload()
    return calls


def save(calls: dict[tuple[Any, ...], dict[str, Any]], path: Path = DATA) -> None:
    """Write the calls: long strings once, in a table, and the whole xz-compressed."""
    strings: dict[str, int] = {}

    def intern(v: Any) -> Any:
        if isinstance(v, str) and len(v) > 8:
            return {"s": strings.setdefault(v, len(strings))}
        if isinstance(v, list):
            return [intern(x) for x in v]
        if isinstance(v, dict):
            return {k: intern(x) for k, x in v.items()}
        return v

    rows = [intern(row) for key in sorted(calls, key=repr) for row in calls[key].values()]
    data = {"strings": list(strings), "calls": rows}
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(lzma.compress(json.dumps(data).encode(), preset=9 | lzma.PRESET_EXTREME))


def load(path: Path = DATA) -> list[list[Any]]:
    """The recorded calls, ``[function, spec, args, kwargs, result]`` each.

    *function* is a metacheck._r.regex function, or ``compile_r.<method>`` for a
    method of a compiled pattern, whose compile_r() arguments *spec* gives.
    """
    data = json.loads(lzma.decompress(path.read_bytes()))
    strings = data["strings"]

    def expand(v: Any) -> Any:
        if isinstance(v, dict):
            return strings[v["s"]] if set(v) == {"s"} else {k: expand(x) for k, x in v.items()}
        if isinstance(v, list):
            return [expand(x) for x in v]
        return v

    return [expand(row) for row in data["calls"]]


if __name__ == "__main__":
    recorded = record()
    save(recorded)
    print(
        f"{sum(map(len, recorded.values()))} calls of {len(recorded)} functions/patterns "
        f"-> {DATA.relative_to(ROOT)}"
    )
