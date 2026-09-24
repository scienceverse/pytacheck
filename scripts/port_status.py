"""Write docs/STATUS.md: how much of metacheck is ported and parity-tested.

    uv run python scripts/port_status.py            # write docs/STATUS.md (runs parity)
    uv run python scripts/port_status.py --missing  # list what is left to port

A function counts as ported when its canonical Python target can be imported:
porting/symbols.json, updated with the porting/map/*.toml [functions] entries
(so a stale symbols.json does not hide new maps). Functions defined in
upstream R/*.R but not yet in symbols.json count as not ported.

Coverage is measured against what pytacheck intends to port. Symbols in
porting/skip.toml [skip] (not ported by design) and ported code listed under
[drop.*] (superseded, to be removed) are counted separately. Parity numbers
come from running the parity suite.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import tomllib
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

UNASSIGNED = "(unassigned)"


def resolve(target: str) -> bool:
    module, _, name = target.partition(":")
    try:
        mod = importlib.import_module(module)
    except Exception:
        return False
    return hasattr(mod, name)


def load_symbols() -> dict[str, dict[str, Any]]:
    """symbols.json + map [functions] + upstream functions symbols.json does not list yet."""
    import porting_symbols as ps

    symbols: dict[str, dict[str, Any]] = json.loads(
        (ROOT / "porting" / "symbols.json").read_text(encoding="utf-8")
    )
    for toml in sorted((ROOT / "porting" / "map").glob("*.toml")):
        data = tomllib.loads(toml.read_text(encoding="utf-8"))
        for r_name, target in data.get("functions", {}).items():
            entry = symbols.setdefault(r_name, {"file": "", "line": 0})
            entry["python"] = target
    r_dir = ps.UP / "R"
    for path in sorted(r_dir.glob("*.R")) if r_dir.is_dir() else []:
        rel = f"R/{path.name}"
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            m = ps.DEF.match(line)
            if not m or line[: len(line) - len(line.lstrip())] != "" or m.group(1) in symbols:
                continue
            try:
                target = f"{ps.module_for(rel, i)}:{ps.py_name(m.group(1))}"
            except SystemExit:  # an R file porting_symbols.py has no module for yet
                target = f"{UNASSIGNED}:{ps.py_name(m.group(1))}"
            symbols[m.group(1)] = {"file": rel, "line": i, "python": target}
    return symbols


def load_scope() -> tuple[dict[str, str], dict[str, str]]:
    """``(skip, drop)``: R name -> reason, and R name -> [drop.<id>] id."""
    path = ROOT / "porting" / "skip.toml"
    data = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    drop = {r: key for key, item in data.get("drop", {}).items() for r in item.get("r", [])}
    return dict(data.get("skip", {})), drop


def classify() -> tuple[dict[str, list[tuple[str, str, str]]], dict[str, str]]:
    """``{R file: [(name, status, python target)]}`` with status ported/missing/skip/drop."""
    symbols = load_symbols()
    skip, drop = load_scope()
    by_file: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for name, entry in symbols.items():
        if name in skip:
            status = "skip"
        elif name in drop:
            status = "drop"
        else:
            status = "ported" if resolve(entry["python"]) else "missing"
        by_file[entry.get("file") or "(other)"].append((name, status, entry["python"]))
    return by_file, skip


def print_missing() -> None:
    """The symbols left to port, grouped by R file and Python module."""
    by_file, _ = classify()
    total = 0
    for rfile in sorted(by_file):
        missing = [(n, t) for n, s, t in by_file[rfile] if s == "missing"]
        if not missing:
            continue
        total += len(missing)
        modules = sorted({t.partition(":")[0] for _, t in missing})
        print(f"{rfile} ({len(missing)}) -> {', '.join(modules)}")
        print("    " + ", ".join(n for n, _ in missing))
    print(f"{total} symbols left to port")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--missing", action="store_true", help="list what is left to port")
    if parser.parse_args().missing:
        print_missing()
        return

    from parity.__main__ import check_case
    from parity.cases import load_cases

    by_file, skip = classify()
    area_stats: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for case in load_cases():
        status, _, _ = check_case(case)
        area_stats[case.area][status] += 1
        area_stats[case.area]["total"] += 1

    def count(status: str, items: list[tuple[str, str, str]] | None = None) -> int:
        rows = items if items is not None else [x for v in by_file.values() for x in v]
        return sum(s == status for _, s, _ in rows)

    done, missing = count("ported"), count("missing")
    intended = done + missing
    skipped, dropping = count("skip"), count("drop")
    modules = [(n, s) for f, v in by_file.items() if f.startswith("inst/modules") for n, s, _ in v]
    lines = [
        "# Porting status",
        "",
        "Generated by `scripts/port_status.py`.",
        "",
        f"- **R functions ported:** {done} / {intended} ({100 * done / max(intended, 1):.0f}%)"
        " of those pytacheck ports",
        f"- **Not ported by design:** {skipped} R functions, and {len(skip) - skipped} "
        "dependency functions ([porting/skip.toml](../porting/skip.toml))",
        f"- **Ported but superseded (to remove):** {dropping} R functions ([drop] in"
        " porting/skip.toml)",
        f"- **Modules ported:** {sum(s == 'ported' for _, s in modules)} / "
        f"{sum(s in ('ported', 'missing') for _, s in modules)}",
        f"- **Parity cases:** {sum(s['pass'] for s in area_stats.values())} passing of "
        f"{sum(s['total'] for s in area_stats.values())}",
        "",
        "## Parity by area",
        "",
        "| area | cases | pass | fail | error | missing golden | known divergence |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for area in sorted(area_stats):
        s = area_stats[area]
        lines.append(
            f"| {area} | {s['total']} | {s['pass']} | {s['fail']} | {s['error']} | {s['missing']} | {s['xfail']} |"
        )
    lines += [
        "",
        "## R source files",
        "",
        "| file | ported | missing | not ported by design |",
        "|---|---:|---|---|",
    ]
    for rfile in sorted(by_file):
        items = by_file[rfile]
        names = [n for n, s, _ in items if s == "missing"]
        shown = ", ".join(f"`{m}`" for m in names[:12]) + (" ..." if len(names) > 12 else "")
        skipped_names = ", ".join(
            f"`{n}`" + (" (drop)" if s == "drop" else "")
            for n, s, _ in items
            if s in ("skip", "drop")
        )
        n_ported, n_intended = count("ported", items), count("ported", items) + len(names)
        lines.append(f"| `{rfile}` | {n_ported}/{n_intended} | {shown} | {skipped_names} |")
    (ROOT / "docs" / "STATUS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{done}/{intended} functions ({skipped} skipped), {len(area_stats)} parity areas")
    print("-> docs/STATUS.md")


if __name__ == "__main__":
    main()
