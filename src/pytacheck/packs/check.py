"""``pack check``: the module contract, for pack authors and store CI.

The same checks guard the built-in modules (``tests/foundation/
test_modules_contract.py`` calls :func:`metadata_issues` and
:func:`run_issues`). :func:`pack_check` checks that:

* ``pack.json`` matches the schema and naming rules (and leaves ``reviewed``
  / ``yanked`` to store maintainers);
* each module is named like its file, has a title, a description and a valid
  section keyword, and a ``<validation>`` block or ``validation=`` record
  (otherwise it warns that the module is unvalidated);
* each module runs on ``demopaper()``, ``test_paper()`` edge cases and a
  paper list without mutating them, returns a valid traffic light and a
  ``summary_table`` with one row per paper;
* network use is declared (a static scan for ``pytacheck.http``, ``httpx``,
  ``requests``, ``urllib`` and ``socket``);
* presets expand, and refs to other packs' modules are qualified.

Errors mean the pack is broken (exit code 1 from the CLI); warnings are
advice.
"""

from __future__ import annotations

import copy
import functools
import os
import warnings
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from pytacheck.module import (
    CAPABILITIES,
    SECTION_LEVELS,
    TRAFFIC_LIGHTS,
    ModuleError,
    ModuleSpec,
    module_run,
    use,
)
from pytacheck.packs.manifest import FIELDS, MODULE_NAME_RE, PackError, read_manifest

__all__ = [
    "CheckIssue",
    "check_papers",
    "fingerprint",
    "metadata_issues",
    "pack_check",
    "run_issues",
]

_NETWORKISH = ("pytacheck.db", "pytacheck.archives")  # pytacheck helpers that call APIs
_VALIDATION_NUMBERS = ("papers", "instances", "tp", "fp", "fn", "tn")


@dataclass(frozen=True)
class CheckIssue:
    """One problem found by :func:`pack_check`."""

    level: str  # "error" or "warning"
    code: str  # e.g. "manifest", "name", "metadata", "section", "run", "mutation", "network"
    where: str  # "pack.json", "module apa_df", "preset default", ...
    message: str

    def __str__(self) -> str:
        return f"{self.level}: {self.where}: {self.message}"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _err(code: str, where: str, message: str) -> CheckIssue:
    return CheckIssue("error", code, where, message)


def _warn(code: str, where: str, message: str) -> CheckIssue:
    return CheckIssue("warning", code, where, message)


# ---------------------------------------------------------------------------
# Module contract (shared with the built-in module tests)
# ---------------------------------------------------------------------------


def fingerprint(paper: Any) -> Any:
    """A deep, comparable snapshot of a paper (or paper list), to detect mutation."""
    import pandas as pd

    if hasattr(paper, "names") and not hasattr(paper, "items"):
        return [fingerprint(p) for p in paper]
    return {
        k: (
            v.to_json(orient="split", default_handler=str)
            if isinstance(v, pd.DataFrame)
            else copy.deepcopy(v)
        )
        for k, v in paper.items()
    }


def check_papers() -> list[tuple[str, Any]]:
    """The papers modules must handle: the demo paper, ``test_paper()`` edge cases and a list."""
    from pytacheck.papers import demopaper, test_paper
    from pytacheck.papers.model import PaperList

    return [
        ("demopaper()", demopaper()),
        ("test_paper()", test_paper()),
        (
            "a short test_paper()",
            test_paper(["No findings here.", "The effect was significant, p = .03."]),
        ),
        ("a paper list", PaperList([demopaper(), test_paper()])),
    ]


def metadata_issues(spec: ModuleSpec, stem: str | None = None) -> list[CheckIssue]:
    """Metadata problems of a loaded module (*stem* is its file name)."""
    where = f"module {stem or spec.name}"
    out: list[CheckIssue] = []
    if stem is not None and spec.name != stem:
        out.append(_err("name", where, f"the module is named '{spec.name}', not like its file"))
    if not spec.title:
        out.append(_err("metadata", where, "no title"))
    if not spec.description:
        out.append(_err("metadata", where, "no description"))
    if not spec.keywords:
        out.append(
            _err(
                "section", where, f"no section: give keywords=[one of {', '.join(SECTION_LEVELS)}]"
            )
        )
    elif spec.keywords[0] not in SECTION_LEVELS:
        out.append(
            _err(
                "section",
                where,
                f"the first keyword {spec.keywords[0]!r} is not a section "
                f"({', '.join(SECTION_LEVELS)})",
            )
        )
    unknown = [r for r in spec.requires if r not in CAPABILITIES]
    if unknown:
        out.append(
            _err("requires", where, f"unknown requires {unknown}; allowed: {list(CAPABILITIES)}")
        )
    if spec.validation is not None:
        bad = [
            k
            for k in _VALIDATION_NUMBERS
            if k in spec.validation and not isinstance(spec.validation[k], int | float)
        ]
        if bad:
            out.append(_warn("validation", where, f"validation values must be numbers: {bad}"))
    elif "<validation>" not in (spec.details or ""):
        out.append(
            _warn(
                "validation",
                where,
                "unvalidated: add a <validation> block to details or a validation= record",
            )
        )
    return out


def run_issues(spec: ModuleSpec, papers: Sequence[tuple[str, Any]]) -> list[CheckIssue]:
    """Run a module on *papers* and report contract violations.

    A module that raises gives a ``"run"`` error; mutating its input a
    ``"mutation"`` error; a bad traffic light or ``summary_table`` a
    ``"traffic_light"`` / ``"summary_table"`` error.
    """
    import pandas as pd

    where = f"module {spec.name}"
    out: list[CheckIssue] = []
    captured: dict[str, Any] = {}

    @functools.wraps(spec.func)
    def capture(*args: Any, **kwargs: Any) -> Any:
        captured["raw"] = spec.func(*args, **kwargs)
        return captured["raw"]

    wrapped = replace(spec, func=capture)
    for desc, paper in papers:
        before = fingerprint(paper)
        captured.clear()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                module_run(paper, wrapped)
        except Exception as exc:
            cause = exc.__cause__ or exc
            out.append(_err("run", where, f"fails on {desc}: {type(cause).__name__}: {cause}"))
            continue
        if fingerprint(paper) != before:
            out.append(_err("mutation", where, f"changes its input paper ({desc})"))
        raw = captured.get("raw")
        if raw is not None and not isinstance(raw, dict | pd.DataFrame):
            out.append(
                _err("return", where, f"returns a {type(raw).__name__}, not a dict ({desc})")
            )
            continue
        if not isinstance(raw, dict):
            continue
        tl = raw.get("traffic_light")
        if tl is not None and tl not in TRAFFIC_LIGHTS:
            out.append(
                _err(
                    "traffic_light",
                    where,
                    f"traffic_light {tl!r} is not one of {', '.join(TRAFFIC_LIGHTS)} ({desc})",
                )
            )
        st = raw.get("summary_table")
        if st is None:
            continue
        if not isinstance(st, pd.DataFrame):
            out.append(_err("summary_table", where, f"summary_table is not a DataFrame ({desc})"))
        elif "paper_id" not in st.columns:
            out.append(_err("summary_table", where, f"summary_table has no paper_id ({desc})"))
        else:
            ids = [p.paper_id for p in paper] if hasattr(paper, "names") else [paper.paper_id]
            got = [str(x) for x in st["paper_id"].tolist()]
            if len(got) != len(set(got)):
                out.append(
                    _err("summary_table", where, f"summary_table repeats a paper_id ({desc})")
                )
            stray = sorted(set(got) - {str(i) for i in ids})
            if stray:
                out.append(
                    _err("summary_table", where, f"summary_table has unknown paper_ids {stray}")
                )
    return out


# ---------------------------------------------------------------------------
# pack check
# ---------------------------------------------------------------------------


def _file_issues(root: Path) -> list[CheckIssue]:
    from pytacheck.packs.fetch import MAX_BYTES, MAX_FILES

    out: list[CheckIssue] = []
    files = total = 0
    for folder, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        for name in [*dirs, *names]:
            full = Path(folder) / name
            if full.is_symlink():
                out.append(
                    _err("files", full.relative_to(root).as_posix(), "symlinks are not allowed")
                )
        for name in names:
            full = Path(folder) / name
            if full.is_file() and not full.is_symlink():
                files += 1
                total += full.stat().st_size
    if files > MAX_FILES:
        out.append(_err("files", "pack", f"{files} files (the limit is {MAX_FILES})"))
    if total > MAX_BYTES:
        out.append(_err("files", "pack", f"{total} bytes (the limit is {MAX_BYTES})"))
    return out


def _manifest_issues(manifest: dict[str, Any]) -> list[CheckIssue]:
    from pytacheck.packs.install import _requires_ok, missing_dependencies

    out = [
        _err("manifest", "pack.json", f"'{key}' is set by store maintainers, not in pack.json")
        for key in ("reviewed", "yanked")
        if key in manifest
    ]
    if not manifest.get("version"):
        out.append(_warn("manifest", "pack.json", "no version"))
    if not manifest.get("license"):
        out.append(
            _warn(
                "license",
                "pack.json",
                "no license: the official store asks for an OSI-approved licence",
            )
        )
    if not manifest.get("title") or not manifest.get("description"):
        out.append(_warn("manifest", "pack.json", "add a title and a description"))
    unknown = [f for f in manifest.get("fields", []) if f not in FIELDS]
    if unknown:
        out.append(_warn("manifest", "pack.json", f"fields outside the vocabulary: {unknown}"))
    unmet = _requires_ok(manifest.get("requires") or {})
    if unmet:
        out.append(_warn("requires", "pack.json", f"requires {unmet}, which is not installed"))
    missing = missing_dependencies(manifest.get("dependencies"))
    if missing:
        out.append(
            _warn(
                "dependencies",
                "pack.json",
                f"missing dependencies: pip install {' '.join(missing)}",
            )
        )
    return out


def _static_issues(root: Path, name: str, meta: dict[str, Any] | None) -> list[CheckIssue]:
    from pytacheck.packs.scan import network_imports, scan_file

    where = f"module {name}"
    out: list[CheckIssue] = []
    scan = scan_file(root / f"{name}.py", f"{name}.py")
    if scan.error:
        return [_err("syntax", where, scan.error)]
    if meta is None:
        return [_err("metadata", where, "no @module(...) decorator found")]
    requires = set(meta.get("requires") or [])
    net = network_imports(scan)
    if net and "network" not in requires:
        out.append(
            _err(
                "network",
                where,
                f"imports {', '.join(net)} but does not declare requires=['network']",
            )
        )
    helpers = [i for i in scan.imports if any(i.startswith(p) for p in _NETWORKISH)]
    if helpers and "network" not in requires:
        out.append(
            _warn(
                "network",
                where,
                f"uses {', '.join(helpers)}, which queries online services: "
                "declare requires=['network'] if it does",
            )
        )
    if any(i.startswith("pytacheck.llm") for i in scan.imports) and "llm" not in requires:
        out.append(_warn("llm", where, "uses pytacheck.llm: declare requires=['llm']"))
    return out


def _preset_issues(pack: Any) -> list[CheckIssue]:
    from pytacheck.module import _builtin_names
    from pytacheck.presets import _declared_builtin, expand, label

    out: list[CheckIssue] = []
    builtin = set(_builtin_names()) | set(_declared_builtin())
    for pname, body in pack.presets.items():
        where = f"preset {pname}"
        refs = [*body.get("modules", ()), *body.get("replace", {}).values()]
        for ref in refs:
            if "::" in ref or ref.endswith(".py") or "/" in ref:
                continue
            if not pack.has_module(ref) and ref not in builtin:
                out.append(
                    _warn(
                        "preset",
                        where,
                        f"'{ref}' is not a module of this pack or of metacheck: "
                        f"qualify another pack's module as 'pack::{label(ref)}'",
                    )
                )
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                expand(f"{pack.name}::{pname}")
        except ModuleError as exc:
            text = str(exc)
            if "no active pack named" in text:
                out.append(
                    _warn("preset", where, f"cannot be checked here: {text.splitlines()[0]}")
                )
            else:
                out.append(_err("preset", where, text))
            continue
        out.extend(_warn("preset", where, str(w.message)) for w in caught)
    return out


def pack_check(
    path: str | os.PathLike[str] = ".",
    *,
    run: bool = True,
    papers: Iterable[tuple[str, Any]] | None = None,
    on_module: Callable[[str], None] | None = None,
) -> list[CheckIssue]:
    """Check a pack folder; returns the issues found (errors and warnings).

    ``run=False`` skips running the modules. Modules that require
    ``network`` or ``llm`` are not run (a warning says so).
    """
    from pytacheck.packs.registry import _path_pack, load_module, overlay
    from pytacheck.packs.scan import module_metadata

    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        return [_err("files", str(root), "not a folder")]
    issues = _file_issues(root)
    try:
        manifest = read_manifest(root)
    except PackError as exc:
        return [*issues, _err("manifest", "pack.json", str(exc))]
    issues += _manifest_issues(manifest)
    for p in sorted(root.glob("*.py")):
        stem = p.stem
        if not p.name.startswith("_") and not MODULE_NAME_RE.fullmatch(stem):
            issues.append(
                _warn("name", p.name, "not a valid module name, so the loader ignores this file")
            )
    try:
        pack = _path_pack(manifest["name"], {"path": str(root)}, "pack check")
    except PackError as exc:
        return [*issues, _err("manifest", "pack.json", str(exc))]
    test_papers = list(papers) if papers is not None else None
    with use(allow_local=True), overlay(pack):
        for mod in pack.modules():
            if on_module is not None:
                on_module(mod)
            meta = module_metadata(root / f"{mod}.py")
            static = _static_issues(root, mod, meta)
            issues += static
            if any(i.code in ("syntax", "metadata") for i in static):
                continue
            try:
                spec = load_module(pack, mod)
            except ModuleError as exc:
                issues.append(_err("import", f"module {mod}", str(exc)))
                continue
            issues += metadata_issues(spec, mod)
            if not run:
                continue
            needs = sorted(set(spec.requires) & {"network", "llm"})
            if needs:
                issues.append(
                    _warn("run", f"module {mod}", f"not run: it requires {', '.join(needs)}")
                )
                continue
            if test_papers is None:
                test_papers = check_papers()
            issues += run_issues(spec, test_papers)
        issues += _preset_issues(pack)
    return issues
