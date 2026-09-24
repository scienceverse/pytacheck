"""Provenance of module runs: which module, from which pack, at which commit.

:func:`module_provenance` builds the dict stored in
``ModuleOutput.run_provenance`` (alias ``.provenance``) on every
:func:`pytacheck.module.module_run`::

    {"id": "psych::apa_df", "name": "apa_df", "pack": "psych", "version": "1.2.0",
     "trust": "store", "reviewed": "2026-09-01",
     "source": {"github": "janedoe/pytacheck-psych", "rev": "<40 hex>"},
     "sha256": "<module file sha256>", "modified": false,
     "args": {"strict": true}, "requires": []}

It is a plain attribute, never part of ``keys()``, ``results()`` or the
parity encoding. Built-in runs never import :mod:`pytacheck.packs`.
"""

from __future__ import annotations

import hashlib
import inspect
import math
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pytacheck.module import ModuleSpec

__all__ = [
    "RUN_SCHEMA",
    "ModuleChain",
    "RunRecord",
    "bind_args",
    "builtin_source",
    "file_sha256",
    "json_safe",
    "module_identity",
    "module_provenance",
    "rerun",
    "run_modules",
]

_sha_cache: dict[str, tuple[tuple[int, int, int], str]] = {}


def file_sha256(path: str | os.PathLike[str]) -> str:
    """The sha256 hex digest of a file, cached against its ``(mtime_ns, size, inode)``."""
    key = os.fspath(path)
    st = os.stat(key)
    sig = (st.st_mtime_ns, st.st_size, st.st_ino)
    hit = _sha_cache.get(key)
    if hit is not None and hit[0] == sig:
        return hit[1]
    h = hashlib.sha256()
    with open(key, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    digest = h.hexdigest()
    _sha_cache[key] = (sig, digest)
    return digest


def builtin_source() -> dict[str, str]:
    """Provenance ``source`` of built-in modules."""
    from pytacheck._version import UPSTREAM, __version__

    return {
        "builtin": f"pytacheck {__version__}",
        "upstream": f"{UPSTREAM['version']}@{UPSTREAM['commit'][:10]}",
    }


def _summary(value: Any) -> str | None:
    """A short stand-in for a large object (a DataFrame, an array, a paper), not its repr."""
    shape = getattr(value, "shape", None)
    if isinstance(shape, tuple) and all(isinstance(n, int) for n in shape):
        return f"<{type(value).__name__} {'x'.join(map(str, shape))}>"
    from pytacheck.papers.model import Paper, PaperList

    if isinstance(value, Paper):
        return f"<Paper {value.paper_id}>"
    if isinstance(value, PaperList):
        return f"<PaperList of {len(value)}>"
    return None


def json_safe(value: Any) -> Any:
    """*value* as JSON data; anything that is not JSON is stored as its ``repr``.

    Large objects (DataFrames, arrays, papers) are summarised as their type and
    shape instead, so provenance stays cheap to build.
    """
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else repr(value)
    if isinstance(value, list | tuple):
        return [json_safe(v) for v in value]
    if isinstance(value, Mapping) and all(isinstance(k, str) for k in value):
        return {k: json_safe(v) for k, v in value.items()}
    summary = _summary(value)
    if summary is not None:
        return summary
    text = repr(value)
    return text if len(text) <= 1000 else text[:997] + "..."


@lru_cache(maxsize=512)
def _signature(func: Callable[..., Any]) -> inspect.Signature | None:
    try:
        return inspect.signature(func)
    except (TypeError, ValueError):
        return None


def bind_args(
    func: Callable[..., Any], paper: Any, kwargs: Mapping[str, Any]
) -> dict[str, Any] | None:
    """The effective arguments of ``func(paper, **kwargs)`` (defaults applied, paper left out).

    ``None`` when the call does not bind (the call itself will then raise).
    """
    sig = _signature(func)
    if sig is None:
        return None
    try:
        bound = sig.bind(paper, **kwargs)
    except TypeError:
        return None
    bound.apply_defaults()
    out: dict[str, Any] = {}
    for i, (name, value) in enumerate(bound.arguments.items()):
        kind = sig.parameters[name].kind
        if i == 0 and kind is not inspect.Parameter.VAR_KEYWORD:
            continue  # the paper
        if kind is inspect.Parameter.VAR_KEYWORD:
            out.update(value)
        elif kind is inspect.Parameter.VAR_POSITIONAL:
            if value:
                out[name] = list(value)
        else:
            out[name] = value
    return out


def _sha(spec: ModuleSpec) -> str | None:
    if not spec.path:
        return None
    try:
        return file_sha256(spec.path)
    except OSError:
        return None


def _is_pack_module(spec: ModuleSpec) -> bool:
    modname = getattr(spec.func, "__module__", None) or ""
    return bool(spec.pack) or modname.startswith("pytacheck_packs.")


def module_provenance(spec: ModuleSpec, args: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The provenance record of one run of *spec* with effective arguments *args*."""
    pack: str | None = None
    version: str | None = None
    trust = "local"
    reviewed: str | None = None
    modified = False
    sha = _sha(spec)
    if spec.pack == "metacheck":
        from pytacheck._version import __version__

        pack, version, trust, source = "metacheck", __version__, "builtin", builtin_source()
    elif _is_pack_module(spec):
        from pytacheck.packs.registry import integrity, pack_for_spec

        found = pack_for_spec(spec)
        pack = found.name if found is not None else spec.pack
        source = {}
        if found is not None:
            version, trust, reviewed = found.version, found.trust, found.reviewed
            source = dict(found.source)
            modified = bool(integrity(found))
    else:
        # not in a pack: a pip-installed plugin (legacy entry point) or local code
        modname = getattr(spec.func, "__module__", None) or ""
        parts = set(Path(spec.path).parts) if spec.path else set()
        trust = "dist" if parts & {"site-packages", "dist-packages"} else "local"
        source = {"path": spec.path} if spec.path else {"module": modname}
    return {
        "id": f"{pack}::{spec.name}" if pack else spec.name,
        "name": spec.name,
        "pack": pack,
        "version": version,
        "trust": trust,
        "reviewed": reviewed,
        "source": source,
        "sha256": sha,
        "modified": modified,
        "args": json_safe(dict(args or {})),
        "requires": list(spec.requires),
    }


def module_identity(spec: ModuleSpec, provenance: Mapping[str, Any]) -> tuple[Any, ...]:
    """What makes two runs of a module the same code: pack, rev, file hash and function.

    The function object itself is part of it: two functions can share a name
    and a source file (a factory, a redefinition in a notebook) and still
    differ. Only a module file loaded by path (re-executed on every call, as
    R's ``source()``) is identified by its file hash alone, since its function
    is new each time.
    """
    source = provenance.get("source") or {}
    rev = source.get("rev") if isinstance(source, Mapping) else None
    sha = provenance.get("sha256")
    modname = getattr(spec.func, "__module__", None) or ""
    code: Any = (sha, spec.func)
    if sha and modname.startswith("pytacheck_user_module_"):
        code = (sha, getattr(spec.func, "__qualname__", spec.name))
    return (provenance.get("pack"), rev or provenance.get("version"), code, spec.name)


# ---------------------------------------------------------------------------
# Run records (pytacheck.run/1), running a selection, and reruns
# ---------------------------------------------------------------------------

RUN_SCHEMA = "pytacheck.run/1"
#: ``id`` of the ``<script type="application/json">`` that embeds a record in HTML reports
RUN_SCRIPT_ID = "pytacheck-run"
_FAILED_TEXT = "This module failed to run"


def _utc_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _paper_ids(papers: Any) -> list[str]:
    from pytacheck.papers.model import Paper, PaperList

    if isinstance(papers, Paper):
        return [str(papers.paper_id)]
    if isinstance(papers, PaperList):
        return [str(n) for n in papers.names]
    if isinstance(papers, list | tuple):
        return [i for p in papers for i in _paper_ids(p)]
    return []


def _environment() -> dict[str, Any]:
    env: dict[str, Any] = {}
    try:
        from pytacheck.io.bibr import bibr_version

        env["bibr"] = bibr_version()
    except Exception:  # pragma: no cover - bibr probing must never break a record
        env["bibr"] = None
    return env


@dataclass
class RunRecord:
    """What ran, from where, with which arguments (``pytacheck.run/1``).

    Built by :func:`run_modules` (and the CLI, API and reports) from the
    provenance of every module that ran. It is JSON: :meth:`write` /
    :meth:`read` a file, :meth:`to_html` embeds it in a report as
    ``<script type="application/json" id="pytacheck-run">``, and
    :func:`rerun` replays it.
    """

    created: str
    pytacheck: str
    metacheck: dict[str, str]
    python: str
    platform: str
    preset: str | None = None
    preset_source: str | None = None
    offline: bool = False
    dropped: list[str] = field(default_factory=list)
    papers: list[str] = field(default_factory=list)
    modules: list[dict[str, Any]] = field(default_factory=list)
    environment: dict[str, Any] = field(default_factory=dict)
    schema: str = RUN_SCHEMA

    @classmethod
    def build(
        cls,
        outputs: Any = (),
        *,
        selection: Any = None,
        papers: Any = None,
    ) -> RunRecord:
        """A record for module *outputs* (a list, or a mapping such as a report's).

        *selection* (a :class:`pytacheck.presets.Selection`) supplies the
        preset, where it came from, ``offline`` and the dropped modules.
        """
        import platform as _platform
        import sys

        from pytacheck._version import UPSTREAM, __version__

        items = list(outputs.values()) if isinstance(outputs, Mapping) else list(outputs)
        modules = []
        for out in items:
            prov = getattr(out, "run_provenance", None)
            entry = dict(prov) if isinstance(prov, Mapping) else {}
            label = str(getattr(out, "module", "") or entry.get("name") or "")
            entry.setdefault("id", label)
            entry.setdefault("name", label)
            if "status" not in entry:
                failed = (
                    prov is None
                    and getattr(out, "traffic_light", None) == "fail"
                    and getattr(out, "summary_text", None) == _FAILED_TEXT
                )
                entry["status"] = "fail" if failed else "ok"
                if failed:
                    entry["error"] = str(getattr(out, "report", "") or "")
            if label and label != entry["name"]:
                entry["label"] = label
            modules.append(json_safe(entry))
        if papers is None:
            papers = next(
                (getattr(o, "paper", None) for o in items if getattr(o, "paper", None) is not None),
                None,
            )
        return cls(
            created=_utc_now(),
            pytacheck=__version__,
            metacheck={"version": UPSTREAM["version"], "commit": UPSTREAM["commit"][:10]},
            python=_platform.python_version(),
            platform=sys.platform,
            preset=getattr(selection, "preset", None),
            preset_source=getattr(selection, "source", None),
            offline=bool(getattr(selection, "offline", False)),
            dropped=[str(d) for d in getattr(selection, "dropped", []) or []],
            papers=_paper_ids(papers),
            modules=modules,
            environment=_environment(),
        )

    def to_dict(self) -> dict[str, Any]:
        """The record as JSON data (key order as in the spec)."""
        return {
            "schema": self.schema,
            "created": self.created,
            "pytacheck": self.pytacheck,
            "metacheck": dict(self.metacheck),
            "python": self.python,
            "platform": self.platform,
            "preset": self.preset,
            "preset_source": self.preset_source,
            "offline": self.offline,
            "dropped": list(self.dropped),
            "papers": list(self.papers),
            "modules": [dict(m) for m in self.modules],
            "environment": dict(self.environment),
        }

    def to_json(self, indent: int | None = 2) -> str:
        import json

        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def write(self, path: str | os.PathLike[str]) -> Path:
        """Write the record as JSON; returns the path."""
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(self.to_json() + "\n", encoding="utf-8")
        return out

    def to_html(self) -> str:
        """The record as an HTML ``<script type="application/json" id="pytacheck-run">``."""
        body = self.to_json(indent=None).replace("</", "<\\/")
        return f'<script type="application/json" id="{RUN_SCRIPT_ID}">{body}</script>'

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RunRecord:
        from pytacheck.module import ModuleError

        if not isinstance(data, Mapping) or data.get("schema") != RUN_SCHEMA:
            raise ModuleError(f"Not a pytacheck run record (schema {RUN_SCHEMA})")
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})

    @classmethod
    def from_html(cls, html: str) -> RunRecord:
        """The record embedded in an HTML report."""
        import json
        import re

        from pytacheck.module import ModuleError

        m = re.search(rf'<script[^>]*id="{RUN_SCRIPT_ID}"[^>]*>(.*?)</script>', html, re.DOTALL)
        if m is None:
            raise ModuleError("This HTML file has no embedded pytacheck run record")
        return cls.from_dict(json.loads(m.group(1).replace("<\\/", "</")))

    @classmethod
    def read(cls, source: Any) -> RunRecord:
        """A record from a file (JSON, or an HTML report embedding one), JSON text or a dict."""
        import json

        if isinstance(source, RunRecord):
            return source
        if isinstance(source, Mapping):
            return cls.from_dict(source)
        text = str(source)
        if not text.lstrip().startswith(("{", "<")):
            text = Path(source).read_text(encoding="utf-8")
        if text.lstrip().startswith("<"):
            return cls.from_html(text)
        return cls.from_dict(json.loads(text))


class ModuleChain(list):  # type: ignore[type-arg]
    """The outputs of :func:`run_modules`, in run order, with the run record.

    ``last`` is the final output (its ``summary_table`` combines every
    module's), ``paper`` the input and ``run_record`` the :class:`RunRecord`.
    """

    def __init__(
        self,
        outputs: Any = (),
        *,
        paper: Any = None,
        selection: Any = None,
        run_record: RunRecord | None = None,
    ) -> None:
        super().__init__(outputs)
        self.paper = paper
        self.selection = selection
        self.run_record = run_record

    @property
    def last(self) -> Any:
        return self[-1] if self else None

    @property
    def summary_table(self) -> Any:
        return self[-1].summary_table if self else None

    def outputs(self) -> dict[str, Any]:
        """Outputs by label (R's ``report_module_run()`` list, before sorting)."""
        return {o.module: o for o in self}


def _entries(selection: Any) -> list[tuple[Any, dict[str, Any]]]:
    if selection is None:
        from pytacheck.presets import select

        return list(select())
    if isinstance(selection, str | os.PathLike) or callable(selection):
        return [(os.fspath(selection) if isinstance(selection, os.PathLike) else selection, {})]
    out = []
    for item in selection:
        if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], Mapping):
            out.append((item[0], dict(item[1])))
        else:
            out.append((item, {}))
    return out


def _failed_output(
    paper: Any, op: Any, ref: Any, label: str, args: Mapping[str, Any], exc: Exception
) -> Any:
    """R's ``report_module_run()`` error handler: a ``fail`` output that keeps the chain."""
    from dataclasses import replace

    import pandas as pd

    from pytacheck.module import ModuleOutput, module_find
    from pytacheck.papers.model import PaperList

    prev: dict[str, Any] = {}
    if isinstance(op, ModuleOutput):
        prev = dict(op.prev_outputs or {})
        prev[op.module] = replace(op, prev_outputs={}, paper=None)
        summary_table = op.summary_table
    else:
        summary_table = None
    if summary_table is None:
        ids = paper.names if isinstance(paper, PaperList) else [getattr(paper, "paper_id", None)]
        summary_table = pd.DataFrame({"paper_id": pd.Series(ids, dtype="string")})
    try:
        prov: dict[str, Any] = module_provenance(module_find(ref), args)
    except Exception:
        prov = {"id": str(ref), "name": label, "pack": None, "args": json_safe(dict(args))}
    prov["status"] = "fail"
    prov["error"] = str(exc)
    return ModuleOutput(
        module=label,
        title=label,
        section=None,  # type: ignore[arg-type]  # R's failed output has no section
        table=None,
        report=str(exc),
        traffic_light="fail",
        summary_text=_FAILED_TEXT,
        summary_table=summary_table,
        paper=paper,
        prev_outputs=prev,
        run_provenance=prov,
    )


def run_modules(paper: Any, selection: Any = None) -> ModuleChain:
    """Run a selection in order, chaining outputs, inside a run session.

    *selection* is a :class:`pytacheck.presets.Selection` (from
    :func:`pytacheck.presets.select`), a list of ``(ref, args)`` or of refs,
    or one ref; ``None`` runs ``select()``'s default. As in metacheck's
    ``report_module_run()``, a module that errors becomes a ``"fail"``
    output (with a warning) and the chain goes on. The result carries a
    :class:`RunRecord` as ``run_record``.
    """
    import warnings

    from pytacheck.module import module_run, run_session
    from pytacheck.presets import label as label_of

    entries = _entries(selection)
    outputs: list[Any] = []
    op = paper
    with run_session():
        for ref, args in entries:
            label = label_of(ref)
            try:
                op = module_run(op, ref, **args)
            except Exception as exc:
                warnings.warn(f"Error in {label}", stacklevel=2)
                op = _failed_output(paper, op, ref, label, args, exc)
            outputs.append(op)
    chain = ModuleChain(outputs, paper=paper, selection=selection)
    chain.run_record = RunRecord.build(chain, selection=selection, papers=paper)
    return chain


def _refused(msg: str) -> Exception:
    """A rerun refusal (never replayed as a failed module)."""
    from pytacheck.module import ModuleError

    exc = ModuleError(msg)
    exc.__pytacheck_refused__ = True  # type: ignore[attr-defined]
    return exc


def _refuse(msg: str, allow: bool) -> None:
    import warnings

    if not allow:
        raise _refused(msg + " (pass allow_modified=True / --allow-modified to run it anyway)")
    warnings.warn(msg, stacklevel=3)


def _differs(path: Path, want: str | None) -> bool:
    """Whether the file at *path* is not the recorded one (hashed, never imported)."""
    if not want:
        return False
    try:
        return file_sha256(path) != want
    except OSError:
        return True


def _rerun_pack_spec(
    entry: Mapping[str, Any], *, install: bool, yes: bool, allow_modified: bool
) -> Any:
    """The spec of a recorded pack module, verified *before* its code is imported."""
    import warnings

    from pytacheck.module import ModuleError
    from pytacheck.packs.manifest import PackError
    from pytacheck.packs.registry import (
        _installed_pack,
        get_pack,
        install_dir,
        integrity,
        load_module,
    )

    name, pack_name = entry["name"], entry["pack"]
    source = dict(entry.get("source") or {})
    rev = source.get("rev")
    pack = None
    try:
        active = get_pack(pack_name)
        if active.kind in ("path", "dist") or not rev or active.rev == rev:
            pack = active
    except PackError:
        pass
    if pack is None and rev:
        pin = {"rev": rev, "source": {k: v for k, v in source.items() if k != "rev"}}
        if not install_dir(pack_name, pin).is_dir():
            if not install:
                raise ModuleError(
                    f"The pack '{pack_name}' at {rev[:12]} (used by {entry.get('id')}) is not "
                    "installed; rerun with install=True (--install) to fetch it"
                )
            from pytacheck.packs.install import _Candidate, _install

            cand = _Candidate(
                name=pack_name,
                source=pin["source"],
                rev=rev,
                version=entry.get("version"),
                store=None,
            )
            cand.notes.append("Installed to rerun a run record; it is not pinned in config.")
            _install(cand, scope=None, yes=yes)
        pack = _installed_pack(pack_name, pin, origin="run record")
    if pack is None:
        raise ModuleError(f"The pack '{pack_name}' (used by {entry.get('id')}) is not available")
    if pack.kind == "installed":
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            bad = integrity(pack)
        if bad:
            _refuse(
                f"The installed pack '{pack_name}' was modified: {', '.join(bad)}", allow_modified
            )
    if pack.has_module(name) and _differs(pack.module_path(name), entry.get("sha256")):
        _refuse(
            f"The module file of '{entry.get('id')}' differs from the recorded one", allow_modified
        )
    return pack, load_module(pack, name)


def _rerun_local_ref(entry: Mapping[str, Any], *, allow_modified: bool, yes: bool) -> Any:
    """What to run for a recorded module outside any pack, checked before it is imported.

    A record is shareable data, so a recorded file outside the working
    directory runs only after the user agrees (or with ``yes=True``).
    """
    from pytacheck.module import _allow_local, _locate
    from pytacheck.packs import ui

    name = str(entry.get("name") or entry.get("id"))
    want = entry.get("sha256")
    path = (entry.get("source") or {}).get("path")
    if path and Path(path).is_file() and _allow_local():
        target = Path(path).resolve()
        if not target.is_relative_to(Path.cwd().resolve()) and not yes:
            question = f"The run record runs the module file {target}, outside this folder. Run it?"
            if not ui.confirm(question):
                raise _refused(
                    f"Not running {target} from the run record (pass yes=True / --yes to allow it)"
                )
        if _differs(target, want):
            _refuse(
                f"The module file of '{entry.get('id')}' differs from the recorded one",
                allow_modified,
            )
        return str(path)
    kind, where = _locate(name)
    file = where if kind == "file" else where[0].module_path(name) if kind == "pack" else None
    if file is not None and _differs(Path(file), want):
        _refuse(
            f"The module file of '{entry.get('id')}' differs from the recorded one", allow_modified
        )
    return name


def rerun(
    record: Any,
    paper: Any,
    *,
    install: bool = False,
    allow_modified: bool = False,
    yes: bool = False,
) -> ModuleChain:
    """Replay a run record on *paper*: the same modules, code revisions and arguments.

    Pack modules run from the recorded commit (``install=True`` fetches a
    missing one, asking first unless ``yes=True``; it is not pinned). A module
    whose file differs from the record's sha256, or an installed pack that
    was modified after install, is refused unless ``allow_modified=True``;
    files are checked before any of their code is imported. A module that
    failed in the recorded run and cannot be found now fails again (with a
    warning) instead of stopping the rerun. Built-in modules come from this
    pytacheck; a different version or file hash only warns.
    """
    import warnings

    from pytacheck._version import __version__
    from pytacheck.module import ModuleError, module_find
    from pytacheck.presets import Selection

    rec = RunRecord.read(record)
    entries: list[tuple[Any, dict[str, Any]]] = []
    for m in rec.modules:
        name = str(m.get("name") or m.get("id"))
        args = dict(m.get("args") or {})
        pack = m.get("pack")
        want = m.get("sha256")
        try:
            if pack == "metacheck":
                spec = module_find(name)
                if m.get("version") != __version__ or (want and _sha(spec) != want):
                    warnings.warn(
                        f"The built-in module '{name}' comes from pytacheck {__version__}; "
                        f"the record used {m.get('version')}",
                        stacklevel=2,
                    )
                entries.append((name, args))
                continue
            if pack:
                _pk, spec = _rerun_pack_spec(
                    m, install=install, yes=yes, allow_modified=allow_modified
                )
                entries.append((spec, args))
            else:
                entries.append((_rerun_local_ref(m, allow_modified=allow_modified, yes=yes), args))
        except ModuleError as exc:
            if m.get("status") != "fail" or getattr(exc, "__pytacheck_refused__", False):
                raise
            # it failed in the recorded run too (e.g. a module not ported yet): fail it again
            warnings.warn(
                f"'{m.get('id') or name}' failed in the recorded run and still cannot be run: {exc}",
                stacklevel=2,
            )
            entries.append((str(m.get("id") or name), args))
    selection = Selection(
        entries,
        preset=rec.preset,
        source=f"rerun of a record created {rec.created}",
        dropped=rec.dropped,
        offline=rec.offline,
    )
    return run_modules(paper, selection)
