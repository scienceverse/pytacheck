"""The ``pytacheck`` command line.

Examples::

    pytacheck init                                   # pick field presets, install their packs
    pytacheck modules                                # list built-in modules
    pytacheck modules --all                          # ... and every active pack's (pack::name)
    pytacheck presets                                # list presets; `presets REF --as-r` for R
    pytacheck run paper.json                         # the configured preset (or metacheck's default)
    pytacheck run paper.json -m all_p_values -m marginal
    pytacheck run paper.json --preset psych -a power.seed=1 --record run.json
    pytacheck run paper.pdf -m marginal --json       # PDFs need pytacheck[bibr]
    pytacheck report paper.json -o report.html
    pytacheck rerun run.json paper.json              # replay a run record
    pytacheck pack search trial --field medicine     # browse the store
    pytacheck pack install clinical_trials           # shows a consent card first
    pytacheck pack new mypack && pytacheck pack check mypack
    pytacheck store build . --check                  # store CI
    pytacheck read paper.pdf -o paper.json           # extract with bibr, save JSON
    pytacheck serve --port 8000                      # REST API (pytacheck[api])
    pytacheck version

Selection flags (run, report): ``-m`` modules (in order), ``--preset``,
``-a MOD.KEY=VALUE`` (one module) or ``-a KEY=VALUE`` (every selected module
that accepts KEY), ``--offline`` (skip network/LLM modules). See docs/MODULES.md.
"""

from __future__ import annotations

import argparse
import inspect
import sys
import warnings
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def _parse_value(text: str) -> Any:
    import orjson

    try:
        return orjson.loads(text)
    except orjson.JSONDecodeError:
        return text


def _module_args(pairs: Sequence[str]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """``-a`` values: ``({module: {key: value}}, {key: value for bare keys})``."""
    per: dict[str, dict[str, Any]] = {}
    bare: dict[str, Any] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            raise SystemExit(f"--arg expects [MODULE.]KEY=VALUE, got {pair!r}")
        key = key.strip()
        mod, dot, name = key.rpartition(".")
        if not name.isidentifier():
            raise SystemExit(f"--arg {pair!r}: {name!r} is not an argument name")
        if dot and mod:
            per.setdefault(mod, {})[name] = _parse_value(value)
        else:
            bare[name] = _parse_value(value)
    return per, bare


def _params(ref: Any) -> list[inspect.Parameter] | None:
    """A module's parameters after the paper, or ``None`` when it cannot be loaded."""
    from pytacheck.module import ModuleError, module_find

    try:
        return list(inspect.signature(module_find(ref).func).parameters.values())[1:]
    except (ModuleError, TypeError, ValueError):
        return None


def _accepts(ref: Any, key: str) -> bool:
    params = _params(ref)
    if params is None:
        return False
    return any(p.name == key or p.kind is inspect.Parameter.VAR_KEYWORD for p in params)


class _Failure(Exception):
    """A command failed for a reason worth one line (``main`` prints it, exit 1)."""


def _read(paths: Sequence[str]) -> Any:
    from pytacheck.io.read import read

    try:
        return read(paths[0] if len(paths) == 1 else list(paths))
    except (OSError, ValueError) as exc:
        raise _Failure(f"Cannot read {', '.join(paths)}: {exc}") from exc


def _err(message: str) -> int:
    from pytacheck.packs.ui import console

    console().print(f"[red]error:[/] {message}", markup=True, highlight=False)
    return 1


def _escape(text: Any) -> str:
    from rich.markup import escape

    return escape("" if text is None else str(text))


def _print_table(df: Any, columns: Sequence[str], *, title: str | None = None) -> None:
    """A table on stdout: rich in a terminal, plain aligned text (never truncated) otherwise."""
    import pandas as pd

    def cell(value: Any) -> str:
        if value is None or value is pd.NA or (isinstance(value, float) and value != value):
            return ""
        return str(value)

    rows = [[cell(row.get(c)) for c in columns] for _, row in df.iterrows()]
    if not sys.stdout.isatty():
        widths = [max([len(c), *(len(r[i]) for r in rows)]) for i, c in enumerate(columns)]
        lines = [title] if title else []
        lines.extend(
            "  ".join(v.ljust(w) for v, w in zip(r, widths, strict=True)).rstrip()
            for r in [list(columns), *rows]
        )
        print("\n".join(lines))
        return
    from rich.console import Console
    from rich.table import Table

    table = Table(title=title, title_justify="left", show_edge=False, pad_edge=False)
    for col in columns:
        table.add_column(col, overflow="fold")
    for r in rows:
        table.add_row(*(_escape(v) for v in r))
    Console().print(table)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def _selection(ns: argparse.Namespace) -> Any:
    """The modules to run for ``run`` / ``report`` (config presets apply here)."""
    from pytacheck.presets import _entry_args, select

    per, bare = _module_args(ns.arg or [])
    sel = select(
        modules=ns.module or None,
        preset=ns.preset,
        args=per,
        use_config=True,
        offline=True if ns.offline else None,
    )
    _check_module_args(sel, per)
    for key, value in bare.items():
        hits = [i for i, (ref, _) in enumerate(sel) if _accepts(ref, key)]
        if not hits:
            raise SystemExit(f"-a {key}=...: none of the selected modules accepts '{key}'")
        for i in hits:
            ref, a = sel[i]
            if key not in _entry_args(ref, per):  # MODULE.KEY beats a bare KEY
                sel[i] = (ref, {**a, key: value})
    return sel


def _check_module_args(sel: Any, per: dict[str, dict[str, Any]]) -> None:
    """``-a MOD.KEY=...`` must name a selected module and one of its arguments."""
    from pytacheck.presets import _qualified, label

    dropped = {label(d) for d in sel.dropped}
    for mod, keys in per.items():
        hits = [
            ref
            for ref, _ in sel
            if mod in {label(ref), ref if isinstance(ref, str) else None}
            or (isinstance(ref, str) and _qualified(ref) == mod)
        ]
        if not hits:
            if label(mod) in dropped:
                continue  # left out by --offline
            names = ", ".join(label(ref) for ref, _ in sel) or "none"
            raise SystemExit(
                f"-a {mod}.{next(iter(keys))}=...: '{mod}' is not among the selected modules "
                f"({names})"
            )
        for key in keys:
            params = [_params(ref) for ref in hits]
            known = [p for p in params if p is not None]
            if known and not any(
                q.name == key or q.kind is inspect.Parameter.VAR_KEYWORD for ps in known for q in ps
            ):
                takes = ", ".join(q.name for q in known[0]) or "no arguments"
                raise SystemExit(
                    f"-a {mod}.{key}=...: the module '{label(mod)}' has no argument '{key}' "
                    f"(it takes: {takes})"
                )


def _announce(sel: Any) -> None:
    from pytacheck.packs.ui import console

    con = console()
    if sel.preset:
        con.print(f"[dim]Preset: {_escape(sel.preset)} (from {_escape(sel.source)})[/]")
    if sel.dropped:
        con.print(f"[dim]Offline: skipped {_escape(', '.join(map(str, sel.dropped)))}[/]")


def _failed(o: Any) -> bool:
    """Whether an output is a module that failed to run (not a module's own verdict)."""
    return (o.run_provenance or {}).get("status") == "fail"


def _error_text(o: Any) -> str:
    """Why a module failed, in one line (with a note for metacheck modules not ported yet)."""
    from pytacheck.module import _builtin_names
    from pytacheck.presets import _declared_builtin

    text = str(o.report or (o.run_provenance or {}).get("error") or "").strip()
    first = text.splitlines()[0] if text else "unknown error"
    unported = o.module in _declared_builtin() and o.module not in _builtin_names()
    if unported and first.startswith("There were no modules that matched"):
        return f"metacheck's '{o.module}' is not ported to pytacheck yet"
    return first


def _print_outputs(outputs: Sequence[Any], as_json: bool) -> None:
    import orjson

    if as_json:
        payload = [
            {
                "module": o.module,
                "title": o.title,
                "traffic_light": o.traffic_light,
                "summary_text": o.summary_text,
                "table": (
                    o.table.to_dict(orient="records") if hasattr(o.table, "to_dict") else o.table
                ),
                **({"error": _error_text(o)} if _failed(o) else {}),
            }
            for o in outputs
        ]
        sys.stdout.buffer.write(
            orjson.dumps(
                payload, option=orjson.OPT_INDENT_2 | orjson.OPT_SERIALIZE_NUMPY, default=str
            )
        )
        sys.stdout.write("\n")
        sys.stdout.flush()
        return
    from rich.console import Console

    console = Console()
    colours = {"red": "red", "yellow": "yellow", "green": "green", "fail": "magenta"}
    for o in outputs:
        colour = colours.get(o.traffic_light, "cyan")
        pack = (o.run_provenance or {}).get("pack")
        tag = f" [dim]\\[{_escape(pack)}][/]" if pack and pack != "metacheck" else ""
        console.print(f"[{colour}]●[/] [bold]{_escape(o.title)}[/]{tag}: {_escape(o.summary_text)}")
        if _failed(o):
            console.print(f"  [magenta]error:[/] {_escape(_error_text(o))}")
    if outputs and outputs[-1].summary_table is not None:
        console.print(outputs[-1].summary_table.to_string(index=False))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def _metrics(validation: Any) -> str:
    from pytacheck.packs.manifest import validation_metrics

    v = validation_metrics(validation)
    parts = []
    if "papers" in v:
        parts.append(f"{v['papers']} papers")
    if "ppv" in v:
        parts.append(f"PPV {v['ppv']}")
    if "sensitivity" in v:
        parts.append(f"sensitivity {v['sensitivity']}")
    return ", ".join(parts)


def cmd_modules(ns: argparse.Namespace) -> int:
    from pytacheck.module import format_module_list, module_find, module_help, module_list

    if ns.name:
        print(module_help(ns.name))
        spec = module_find(ns.name)
        if spec.pack and spec.pack != "metacheck":
            print(f"\nPack: {spec.pack}")
        if spec.validation:
            print(f"Validation: {_metrics(spec.validation)}")
        return 0
    if not (ns.pack or ns.all):
        print(format_module_list(module_list()))
        return 0
    df = module_list(pack=ns.pack or "*")
    names, descriptions = [], []
    for name, pack, desc in zip(df["name"], df["pack"], df["description"], strict=True):
        qualified = name if pack == "metacheck" else f"{pack}::{name}"
        metrics = ""
        if pack != "metacheck":
            metrics = _metrics(module_find(qualified).validation)
        names.append(qualified)
        descriptions.append(f"{desc} ({metrics})" if metrics else desc)
    df = df.assign(name=names, description=descriptions)
    print(format_module_list(df))
    return 0


def cmd_presets(ns: argparse.Namespace) -> int:
    import orjson

    from pytacheck.presets import lookup, preset, preset_as_r, preset_list

    refs = list(ns.ref or [])
    if refs and refs[0] == "show":
        refs = refs[1:]
    if not refs:
        _print_table(preset_list(), ["ref", "n_modules", "description", "defined_in"])
        return 0
    ref = refs[0]
    if ns.as_r:
        print(preset_as_r(ref))
        return 0
    entries = preset(ref)
    p = lookup(ref)
    print(f"{p.ref}: {p.description}" if p.description else p.ref)
    print(f"defined in {p.defined_in}\n")
    for i, (mod, args) in enumerate(entries, 1):
        extra = f"  {orjson.dumps(args, default=str).decode()}" if args else ""
        print(f"{i:>3}. {mod}{extra}")
    return 0


def cmd_run(ns: argparse.Namespace) -> int:
    from pytacheck.module import run_session
    from pytacheck.packs.ui import console
    from pytacheck.provenance import run_modules

    papers = _read(ns.papers)
    sel = _selection(ns)
    _announce(sel)
    with run_session():
        chain = run_modules(papers, sel)
    if ns.record and chain.run_record is not None:
        path = chain.run_record.write(ns.record)
        console().print(f"[dim]Run record: {_escape(path)}[/]")
    _print_outputs(list(chain), ns.json)
    return _exit_status(chain)


def _exit_status(outputs: Sequence[Any]) -> int:
    """1 when a module failed to run (after printing every result), else 0."""
    from pytacheck.packs.ui import console

    failed = [o.module for o in outputs if _failed(o)]
    if not failed:
        return 0
    console().print(f"[red]{len(failed)} module(s) failed to run:[/] {_escape(', '.join(failed))}")
    return 1


def cmd_report(ns: argparse.Namespace) -> int:
    try:
        from pytacheck.report import report as report_mod

        report = report_mod.report  # the port may not be complete
    except (ImportError, AttributeError):
        print(
            "report rendering is not available in this build (pytacheck.report.report.report "
            "is missing)",
            file=sys.stderr,
        )
        return 2
    from pytacheck.module import run_session
    from pytacheck.packs.ui import console
    from pytacheck.presets import label
    from pytacheck.provenance import RunRecord

    papers = _read(ns.papers)
    sel = _selection(ns)
    _announce(sel)
    modules = [ref for ref, _ in sel]
    args = {(ref if isinstance(ref, str) else label(ref)): a for ref, a in sel if a}
    kwargs: dict[str, Any] = {"output_file": ns.output, "output_format": ns.format}
    with run_session():
        result = report(papers, modules=modules, args=args or None, **kwargs)
    if ns.record:
        outputs: Any = result
        if not hasattr(result, "paper"):  # a ReportList: record the first report
            outputs = next((r for r in result.values() if r is not None), {})
        # the report is sorted by section: record the order the modules ran in
        ordered = list(outputs.values()) if hasattr(outputs, "values") else list(outputs)
        by_label = {o.module: o for o in ordered}
        ordered = [by_label.pop(label(ref)) for ref, _ in sel if label(ref) in by_label]
        ordered += list(by_label.values())
        path = RunRecord.build(ordered, selection=sel, papers=papers).write(ns.record)
        console().print(f"[dim]Run record: {_escape(path)}[/]")
    print(result)
    return 0


def cmd_rerun(ns: argparse.Namespace) -> int:
    from pytacheck.packs.ui import console
    from pytacheck.provenance import rerun

    papers = _read(ns.papers)
    chain = rerun(
        ns.record_file, papers, install=ns.install, allow_modified=ns.allow_modified, yes=ns.yes
    )
    if ns.record and chain.run_record is not None:
        path = chain.run_record.write(ns.record)
        console().print(f"[dim]Run record: {_escape(path)}[/]")
    _print_outputs(list(chain), ns.json)
    return _exit_status(chain)


def cmd_read(ns: argparse.Namespace) -> int:
    from pytacheck.papers.io import paper_write
    from pytacheck.papers.model import PaperList

    papers = _read(ns.papers)
    out = Path(ns.output) if ns.output else Path(".")
    if isinstance(papers, PaperList):
        paths = paper_write(papers, save_path=out)
        print("\n".join(str(p) for p in paths))  # type: ignore[union-attr]
    else:
        target_dir = out.parent if out.suffix == ".json" else out
        name = out.stem if out.suffix == ".json" else None
        print(paper_write(papers, name, target_dir))
    return 0


def cmd_serve(ns: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print('The API needs the api extra: pip install "pytacheck[api]"', file=sys.stderr)
        return 2
    uvicorn.run("pytacheck.api.app:create_app", factory=True, host=ns.host, port=ns.port)
    return 0


def cmd_version(_: argparse.Namespace) -> int:
    from pytacheck._version import UPSTREAM, __version__
    from pytacheck.io.bibr import bibr_version

    print(f"pytacheck {__version__}")
    pin = f"{UPSTREAM['branch']}@{UPSTREAM['commit'][:10]}"
    if UPSTREAM.get("pull_request"):
        pin += f" + #{UPSTREAM['pull_request']}"
    print(f"metacheck parity: {UPSTREAM['version']} ({pin})")
    print(f"bibr: {bibr_version() or 'not installed'}")
    return 0


# --- pack -------------------------------------------------------------------


def _scope(ns: argparse.Namespace) -> str:
    return "project" if getattr(ns, "project", False) else "user"


def _confirm(ns: argparse.Namespace, question: str) -> bool:
    from pytacheck.packs import ui

    return bool(getattr(ns, "yes", False)) or ui.confirm(question)


def cmd_pack(ns: argparse.Namespace) -> int:
    import orjson

    from pytacheck.packs import install as inst

    action = ns.pack_command
    if action == "list":
        _print_table(
            inst.pack_list(),
            ["name", "version", "kind", "trust", "rev", "store", "modules", "presets", "status"],
        )
        return 0
    if action == "search":
        from pytacheck.packs.stores import store_search

        df = store_search(" ".join(ns.text or []), field=ns.field)
        if not len(df):
            print("No packs found.")
            return 0
        cols = ["name", "store", "version", "fields", "modules", "validation", "reviewed", "title"]
        _print_table(df, cols)
        for _, row in df[df["yanked"].notna()].iterrows():
            print(f"{row['name']} is yanked: {row['yanked']}")
        return 0
    if action == "install":
        result = inst.pack_install(ns.ref, scope=_scope(ns), yes=ns.yes)
        if isinstance(result, list):
            for p in result:
                print(f"{p.name} {p.version or ''} ({(p.rev or p.tree_sha256 or '')[:12]})")
        return 0
    if action == "remove":
        from pytacheck.config import config_path

        if not _confirm(ns, f"Remove the pack '{ns.name}' from {config_path(_scope(ns))}?"):
            return _err("cancelled")
        inst.pack_remove(ns.name, scope=_scope(ns))
        return 0
    if action == "update":
        rows = inst.pack_update(ns.name, yes=ns.yes)
        for r in rows:
            if r.get("status") in ("cancelled", "error"):
                change = str(r["status"])
            elif r["updated"]:
                change = f"{str(r['from'])[:12]} -> {str(r['to'])[:12]}"
            else:
                change = "unchanged"
            print(f"{r['name']}: {change}")
        return 1 if any(r.get("status") in ("cancelled", "error") for r in rows) else 0
    if action == "show":
        info = inst.pack_show(ns.name)
        if ns.json:
            sys.stdout.buffer.write(orjson.dumps(info, option=orjson.OPT_INDENT_2, default=str))
            sys.stdout.write("\n")
            return 0
        _print_show(info)
        return 0
    if action == "new":
        from pytacheck.packs.scaffold import pack_new

        root = pack_new(ns.name, ns.path)
        shown = root.as_posix()
        if not root.is_absolute() and not shown.startswith(("./", "../")):
            shown = f"./{shown}"  # a bare name would be read as a store pack
        print(f"Created {root}")
        print(
            f"Next: edit it, then `pytacheck pack check {shown}` and "
            f"`pytacheck pack install {shown}`"
        )
        return 0
    if action == "check":
        from pytacheck.packs.check import pack_check

        if ns.dir.endswith(".json") and Path(ns.dir).is_file():
            from pytacheck.packs.build import check_entry_file

            issues = check_entry_file(ns.dir)  # a store's packs/<name>.json: fetch, then check
        else:
            issues = pack_check(ns.dir)
        errors = [i for i in issues if i.level == "error"]
        if ns.json:
            payload = {"ok": not errors, "issues": [i.to_dict() for i in issues]}
            sys.stdout.buffer.write(orjson.dumps(payload, option=orjson.OPT_INDENT_2))
            sys.stdout.write("\n")
        else:
            for i in issues:
                print(i)
            warnings_n = len(issues) - len(errors)
            print(f"{len(errors)} error(s), {warnings_n} warning(s)")
        return 1 if errors else 0
    raise AssertionError(action)  # pragma: no cover


def _print_show(info: dict[str, Any]) -> None:
    lines = [f"{info.get('name')} {info.get('version') or ''}".strip()]
    if info.get("title"):
        lines.append(str(info["title"]))
    if info.get("description"):
        lines.append(str(info["description"]))
    lines.append("")
    keys = ("installed", "kind", "trust", "store", "reviewed", "yanked", "rev", "tree_sha256",
            "root", "defined_in", "license", "homepage")  # fmt: skip
    lines.extend(f"{k}: {info[k]}" for k in keys if info.get(k) not in (None, "", [], {}))
    if info.get("source"):
        from pytacheck.packs.fetch import describe_source

        lines.append(f"source: {describe_source(info['source'])}")
    lists = ("fields", "keywords", "dependencies")
    lines.extend(f"{k}: {', '.join(map(str, info[k]))}" for k in lists if info.get(k))
    if info.get("requires"):
        lines.append("requires: " + ", ".join(f"{k} {v}" for k, v in info["requires"].items()))
    if info.get("pip"):
        lines.append(f"missing dependencies: {info['pip']}")
    if info.get("modified"):
        lines.append(f"MODIFIED after install: {', '.join(info['modified'])}")
    mods = info.get("modules") or []
    if mods:
        lines.append("\nmodules:")
        for m in mods:
            metrics = _metrics(m.get("validation"))
            caps = f" [requires {', '.join(m['requires'])}]" if m.get("requires") else ""
            extra = f" ({metrics})" if metrics else ""
            lines.append(f"* {m['name']}: {m.get('title') or ''}{caps}{extra}")
    presets = info.get("presets") or {}
    if presets:
        lines.append("\npresets:")
        lines.extend(f"* {info['name']}::{k}: {v}" for k, v in presets.items())
    print("\n".join(lines))


# --- store ------------------------------------------------------------------


def cmd_store(ns: argparse.Namespace) -> int:
    from pytacheck.packs import stores

    action = ns.store_command
    if action == "list":
        _print_table(stores.store_list(), ["name", "url", "packs", "updated", "defined_in"])
        return 0
    if action == "add":
        from pytacheck.config import config_path

        # refuse URLs with credentials before the prompt shows them
        url = stores.check_store_url(ns.url)
        if not _confirm(ns, f"Add the store '{ns.name}' ({url}) to {config_path()}?"):
            return _err("cancelled")
        path = stores.store_add(ns.name, ns.url)
        print(f"Added the store '{ns.name}' to {path}")
        return 0
    if action == "remove":
        from pytacheck.config import config_path

        if not _confirm(ns, f"Remove the store '{ns.name}' from {config_path()}?"):
            return _err("cancelled")
        path = stores.store_remove(ns.name)
        print(f"Removed the store '{ns.name}' ({path})")
        return 0
    if action == "update":
        df = stores.store_update(ns.name)
        _print_table(df, ["name", "packs", "status", "url"])
        return 0 if (df["status"] == "ok").all() or ns.name is None else 1
    if action == "build":
        from pytacheck.packs.build import store_build

        index, issues = store_build(ns.dir, check=ns.check, repo=ns.repo)
        for i in issues:
            print(i)
        errors = [i for i in issues if i.level == "error"]
        n = len(index.get("packs", []))
        if errors:
            print(f"{len(errors)} error(s): index.json was not written" if not ns.check else
                  f"{len(errors)} error(s)")  # fmt: skip
            return 1
        print(f"{n} pack(s) OK" if ns.check else f"Wrote {Path(ns.dir) / 'index.json'} ({n} packs)")
        return 0
    raise AssertionError(action)  # pragma: no cover


# --- init -------------------------------------------------------------------


def _hide_cached_index_warning() -> None:
    """Hide only "store unreachable, using its cached index" (init offers what is cached)."""
    import warnings

    warnings.filterwarnings("ignore", message=r"The store '.*' is unreachable")


def _init_choices(fields: list[str]) -> list[tuple[str, str]]:
    """``(preset ref, description)`` offered by ``init`` for the chosen fields."""
    import warnings

    from pytacheck.packs.registry import builtin_pack
    from pytacheck.packs.stores import store_indexes

    choices = [
        (f"metacheck::{k}", v.get("description", "")) for k, v in builtin_pack().presets.items()
    ]
    with warnings.catch_warnings():
        _hide_cached_index_warning()
        indexes, _ = store_indexes()
    wanted = set(fields)
    for index in indexes.values():
        for entry in index.get("packs", []):
            if entry.get("yanked"):
                continue
            if wanted and not wanted & set(entry.get("fields") or []):
                continue
            presets = entry.get("presets") or {}
            for name, desc in presets.items():
                if name in wanted or not {n for n in presets if n in wanted}:
                    choices.append((f"{entry['name']}::{name}", str(desc)))
    return list(dict.fromkeys(choices))


def _init_interactive(ns: argparse.Namespace) -> tuple[list[str], str]:
    import warnings

    from pytacheck.packs import ui
    from pytacheck.packs.manifest import FIELDS
    from pytacheck.packs.stores import store_indexes

    con = ui.console()
    with warnings.catch_warnings():
        _hide_cached_index_warning()
        indexes, problems = store_indexes()
    for name, problem in problems.items():
        con.print(f"[yellow]The store '{_escape(name)}' is unavailable:[/] {_escape(problem)}")
    if not indexes:
        con.print("[yellow]Only the built-in presets can be offered.[/]")
    fields = list(dict.fromkeys(f for idx in indexes.values() for f in idx.get("fields", [])))
    fields = fields or list(FIELDS)
    con.print("\n[bold]Which fields do you work in?[/]")
    for i, f in enumerate(fields, 1):
        con.print(f"  {i:>2}. {f}")
    picked = _pick(ui.ask("Numbers, comma-separated", default="1"), fields)
    choices = _init_choices(picked)
    con.print("\n[bold]Which presets should pytacheck run by default?[/]")
    for i, (ref, desc) in enumerate(choices, 1):
        con.print(f"  {i:>2}. {_escape(ref)}  [dim]{_escape(desc)}[/]")
    refs = _pick(ui.ask("Numbers, comma-separated", default="1"), [r for r, _ in choices])
    scope = _scope(ns)
    if not ns.project:
        answer = ui.ask(
            "Save to the (u)ser config or this (p)roject's pytacheck.json?", default="u"
        )
        scope = "project" if answer.strip().lower().startswith("p") else "user"
    return refs, scope


def _pick(answer: str, options: list[str]) -> list[str]:
    out = []
    for part in answer.replace(" ", ",").split(","):
        if part.strip().isdigit() and 1 <= int(part) <= len(options):
            out.append(options[int(part) - 1])
        elif part.strip() in options:
            out.append(part.strip())
    return list(dict.fromkeys(out)) or options[:1]


def _preset_pack(ref: str) -> str | None:
    """The pack a preset ref needs installed (``None`` for built-in and config presets)."""
    from pytacheck.config import load_config
    from pytacheck.packs.registry import builtin_pack

    pack, sep, _ = ref.partition("::")
    if sep:
        return None if pack == "metacheck" else pack
    if ref in load_config().presets or ref in builtin_pack().presets:
        return None
    return ref


def _pin_in_project(pack: str, *, yes: bool) -> None:
    """Pin *pack* in the project config: the user's pin if it has one, else from a store."""
    from pytacheck.config import load_config, update_config
    from pytacheck.packs.install import pack_install

    config = load_config()
    pin = config.packs.get(pack)
    scope, _where = config.source(f"packs.{pack}") or ("", "")
    if scope == "project":
        return
    if isinstance(pin, dict) and "path" not in pin and pin.get("source"):

        def edit(cfg: dict[str, Any]) -> None:
            packs = cfg.get("packs")
            if not isinstance(packs, dict):
                packs = cfg["packs"] = {}
            packs[pack] = dict(pin)

        update_config("project", edit)  # the same commit and hash as the user's pin
        return
    pack_install(pack, scope="project", yes=yes)


def cmd_init(ns: argparse.Namespace) -> int:
    from pytacheck.config import config_path, update_config
    from pytacheck.packs import ui
    from pytacheck.packs.install import pack_install
    from pytacheck.packs.registry import active_packs
    from pytacheck.presets import expand

    scope = _scope(ns)
    refs = list(ns.preset or [])
    if not refs:
        if ui.interactive():
            refs, scope = _init_interactive(ns)
        elif ns.yes:
            refs = ["metacheck::default"]
        else:
            return _err(
                "pytacheck init needs --preset REF (and --yes) when it cannot ask; "
                "see `pytacheck presets` and `pytacheck pack search`"
            )
    for ref in refs:
        pack = _preset_pack(ref)
        if pack is None:
            continue
        if scope == "project":
            # the project file is the team's lock file: pin every pack it needs there,
            # even one already installed for this user (an intact install is only re-pinned)
            _pin_in_project(pack, yes=ns.yes)
        elif pack not in active_packs():
            pack_install(pack, scope=scope, yes=ns.yes)
    count = {}
    for ref in refs:
        count[ref] = len(expand(ref))
    if len(refs) == 1:
        chosen = refs[0]
        combined = None
    else:
        chosen = "mine"
        combined = {"description": "Chosen with pytacheck init", "extends": refs}
    target = config_path(scope)
    if not _confirm(ns, f"Use the preset '{chosen}' by default (write {target})?"):
        return _err("cancelled; nothing was written")

    def edit(cfg: dict[str, Any]) -> None:
        cfg["preset"] = chosen
        if combined is not None:
            presets = cfg.get("presets")
            if not isinstance(presets, dict):
                presets = cfg["presets"] = {}
            presets[chosen] = combined

    path = update_config(scope, edit)
    total = len(expand(chosen))
    print(
        f"Saved to {path}: `pytacheck run PAPER` now runs the preset '{chosen}' ({total} modules)"
    )
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _selection_flags(p: argparse.ArgumentParser, *, module_help: str) -> None:
    p.add_argument("-m", "--module", action="append", help=module_help)
    p.add_argument("--preset", default=None, help="a preset (see `pytacheck presets`)")
    p.add_argument(
        "-a",
        "--arg",
        action="append",
        help="[MODULE.]KEY=VALUE (JSON values allowed); a bare KEY goes to every module "
        "that accepts it",
    )
    p.add_argument("--offline", action="store_true", help="skip modules needing network or LLMs")
    p.add_argument("--record", default=None, metavar="FILE", help="write a run record (JSON)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pytacheck",
        description="Check research outputs for best practices.",
        epilog=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="choose default presets (and install their packs)")
    p.add_argument("--preset", action="append", help="preset ref (repeatable); skips questions")
    p.add_argument("--project", action="store_true", help="write ./pytacheck.json")
    p.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("modules", help="list modules or show help for one")
    p.add_argument("name", nargs="?")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--pack", default=None, help="list one active pack's modules")
    group.add_argument("--all", action="store_true", help="list every active pack's modules")
    p.set_defaults(func=cmd_modules)

    p = sub.add_parser("presets", help="list presets or expand one")
    p.add_argument("ref", nargs="*", help="a preset ref (e.g. metacheck::default)")
    p.add_argument("--as-r", action="store_true", help="print the equivalent metacheck call")
    p.set_defaults(func=cmd_presets)

    p = sub.add_parser("run", help="run modules on papers")
    p.add_argument(
        "papers", nargs="+", help="bibr JSON, Grobid XML, PDF/DOCX (with bibr), or a directory"
    )
    _selection_flags(p, module_help="module name, pack::name or path (repeatable)")
    p.add_argument("--json", action="store_true", help="print results as JSON")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("report", help="write a report for papers")
    p.add_argument("papers", nargs="+")
    _selection_flags(p, module_help="module (repeatable)")
    p.add_argument("-o", "--output", default=None)
    p.add_argument("-f", "--format", default="html", choices=["html", "qmd", "md"])
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("rerun", help="replay a run record on papers")
    p.add_argument("record_file", metavar="RECORD")
    p.add_argument("papers", nargs="+")
    p.add_argument("--install", action="store_true", help="fetch recorded pack revisions")
    p.add_argument("--allow-modified", action="store_true", help="run modified code anyway")
    p.add_argument("--yes", action="store_true", help="do not ask before installing")
    p.add_argument("--record", default=None, metavar="FILE", help="write the new run record")
    p.add_argument("--json", action="store_true", help="print results as JSON")
    p.set_defaults(func=cmd_rerun)

    p = sub.add_parser("pack", help="install, manage and author packs")
    psub = p.add_subparsers(dest="pack_command", required=True)
    q = psub.add_parser("list", help="active and configured packs")
    q = psub.add_parser("search", help="search the stores")
    q.add_argument("text", nargs="*")
    q.add_argument("--field", default=None)
    q = psub.add_parser("install", help="install a pack (no REF: install every pin)")
    q.add_argument("ref", nargs="?", default=None)
    q.add_argument("--project", action="store_true", help="pin in ./pytacheck.json")
    q.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    q = psub.add_parser("remove", help="unpin a pack and delete its files")
    q.add_argument("name")
    q.add_argument("--project", action="store_true")
    q.add_argument("--yes", action="store_true")
    q = psub.add_parser("update", help="re-pin packs to their newest revision")
    q.add_argument("name", nargs="?", default=None)
    q.add_argument("--yes", action="store_true")
    q = psub.add_parser("show", help="details of a pack (active or in a store)")
    q.add_argument("name")
    q.add_argument("--json", action="store_true")
    q = psub.add_parser("new", help="create a pack folder")
    q.add_argument("name")
    q.add_argument("--path", default=".", help="parent folder (default: .)")
    q = psub.add_parser(
        "check", help="check a pack folder or a store's packs/<name>.json (exit 1 on errors)"
    )
    q.add_argument("dir", nargs="?", default=".")
    q.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_pack)

    p = sub.add_parser("store", help="manage stores and build store indexes")
    ssub = p.add_subparsers(dest="store_command", required=True)
    q = ssub.add_parser("list", help="configured stores")
    q = ssub.add_parser("add", help="add a store")
    q.add_argument("name")
    q.add_argument("url")
    q.add_argument("--yes", action="store_true")
    q = ssub.add_parser("remove", help="remove a store")
    q.add_argument("name")
    q.add_argument("--yes", action="store_true")
    q = ssub.add_parser("update", help="fetch store indexes")
    q.add_argument("name", nargs="?", default=None)
    q = ssub.add_parser("build", help="build a store's index.json")
    q.add_argument("dir")
    q.add_argument("--check", action="store_true", help="validate only")
    q.add_argument("--repo", default=None, help="OWNER/REPO of the store on GitHub")
    p.set_defaults(func=cmd_store)

    p = sub.add_parser("read", help="read/extract papers and save bibr JSON")
    p.add_argument("papers", nargs="+")
    p.add_argument("-o", "--output", default=None, help="output file or directory")
    p.set_defaults(func=cmd_read)

    p = sub.add_parser("serve", help="run the REST API")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("version", help="show versions")
    p.set_defaults(func=cmd_version)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    from pytacheck.config import ConfigError
    from pytacheck.module import ModuleError

    ns = build_parser().parse_args(argv)
    with warnings.catch_warnings():
        warnings.showwarning = _show_warning  # restored on exit
        try:
            return int(ns.func(ns))
        except (ModuleError, ConfigError, _Failure) as exc:
            return _err(str(exc))


def _show_warning(message: Any, *_args: Any, **_kwargs: Any) -> None:
    from pytacheck.packs.ui import console

    console().print(f"[yellow]warning:[/] {_escape(message)}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
