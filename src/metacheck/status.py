"""Validation status: labels derived from the metacheck team's registry.

Two labels describe a module, and they are never merged. Trust (``builtin``,
``store``, ``unlisted``, ``local``) says where its code came from. Status says
how accurate its flags are, as measured by the metacheck validation team
(``docs/design/ECOSYSTEM.md`` §2). This module holds the status side:

* the registry snapshot shipped at ``resources/status/validation.json``, its
  schema check (:func:`validate_registry`) and gate G9 (:func:`check`);
* the labels, derived from the registry and never stored (:func:`status`,
  :func:`status_table`): ``validated``, ``external-validated``,
  ``experimental``, ``unvalidated`` and ``withdrawn``;
* the policies that filter on them (:data:`POLICIES`, :func:`parse_status`):
  ``validated`` ⊂ ``certified`` ⊂ ``experimental`` ⊂ ``all``, or a
  comma-separated list of labels.

An author's own ``validation=`` numbers never grant a status: they stay
self-reported. While the snapshot is provisional (the team's registry does not
exist yet), its entries are pytacheck's copy of the counts in metacheck's
``<validation>`` blocks. They count as validated for policies, and their note
says the team has not certified them.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import cache
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any

from metacheck.module import ModuleError, ModuleSpec, _allow_local, _builtin_names, _locate

if TYPE_CHECKING:
    import pandas as pd

    from metacheck.packs.manifest import Pack

__all__ = [
    "DEFAULT_POLICY",
    "LABELS",
    "POLICIES",
    "PROVISIONAL_NOTE",
    "Policy",
    "Registry",
    "Status",
    "StatusError",
    "check",
    "code_sha256",
    "parse_status",
    "read_registry",
    "snapshot",
    "status",
    "status_table",
    "validate_registry",
]

SCHEMA = 1
#: the snapshot's path under ``metacheck.resources``
SNAPSHOT = "status/validation.json"
LABELS = ("validated", "external-validated", "experimental", "unvalidated", "withdrawn")
POLICIES: dict[str, frozenset[str]] = {
    "validated": frozenset({"validated"}),
    "certified": frozenset({"validated", "external-validated"}),
    "experimental": frozenset({"validated", "external-validated", "experimental"}),
    "all": frozenset({"validated", "external-validated", "experimental", "unvalidated"}),
}
#: the library's and the CLI's policy; a public server uses ``validated``
DEFAULT_POLICY = "experimental"
PROVISIONAL_NOTE = (
    "Provisional: based on the metacheck team's published counts (R version), "
    "not yet certified by the team"
)
#: how a module can be loaded, besides ``builtin``: all of these are modules the
#: user added, so the experimental policy runs them even when unvalidated
_OPTED_IN = frozenset({"installed", "path", "dist", "plugin", "file", "object"})

_REF_RE = re.compile(r"^[a-z][a-z0-9_-]{1,39}::[A-Za-z][A-Za-z0-9_]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_TOP_KEYS = frozenset(
    {"schema", "provisional", "registry_commit", "note", "team_packs", "modules", "withdrawn"}
)
_ENTRY_KEYS = frozenset({"provisional", "note", "implementations", "evidence"})
_PY_KEYS = frozenset({"pytacheck", "method", "run", "code_sha256", "version"})
_EVIDENCE_KEYS = frozenset(
    {
        "id", "validators", "date", "unit", "corpus", "counts", "metrics",
        "settings", "server_safe", "protocol", "summary",
    }
)  # fmt: skip
_CORPUS_KEYS = frozenset({"source", "papers", "fields", "sampling", "labels"})
_COUNT_KEYS = frozenset({"tp", "fp", "fn", "tn"})
_WITHDRAWN_KEYS = frozenset({"date", "reason"})
#: pack folders whose files do not change what a module does
_NOT_CODE = ("tests/", "docs/", ".github/")
#: what a module with no registry entry derives from
_NO_ENTRY: Mapping[str, Any] = {
    "provisional": False,
    "note": None,
    "implementations": {},
    "evidence": [],
}


class StatusError(ModuleError):
    """A registry file or a status policy is invalid."""


# ---------------------------------------------------------------------------
# The registry file
# ---------------------------------------------------------------------------


def _object(value: Any, what: str, keys: frozenset[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise StatusError(f"{what} must be an object")
    unknown = sorted(set(value) - keys)
    if unknown:
        raise StatusError(f"{what} has unknown keys {unknown}; allowed: {sorted(keys)}")
    return value


def _text(value: Any, what: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise StatusError(f"{what} must be a string or null")
    return value


def _texts(value: Any, what: str, pattern: re.Pattern[str] | None = None) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise StatusError(f"{what} must be a list of strings")
    if pattern is not None:
        bad = [v for v in value if not pattern.match(v)]
        if bad:
            raise StatusError(f"{what} holds invalid values {bad}")
    return list(value)


def _count(value: Any, what: str) -> int | None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
        raise StatusError(f"{what} must be a whole number of at least 0, or null")
    return value


def _implementations(value: Any, what: str) -> dict[str, Any]:
    impl = _object(value, what, frozenset({"r", "py"}))
    out: dict[str, Any] = {}
    if "r" in impl:
        r = impl["r"]
        if not isinstance(r, Mapping):
            raise StatusError(f"{what}.r must map packages to lists of releases")
        out["r"] = {str(k): _texts(v, f"{what}.r.{k}") for k, v in r.items()}
    if "py" in impl:
        py = _object(impl["py"], f"{what}.py", _PY_KEYS)
        out["py"] = {
            "pytacheck": _texts(py.get("pytacheck", []), f"{what}.py.pytacheck"),
            "code_sha256": _texts(py.get("code_sha256", []), f"{what}.py.code_sha256", _SHA256_RE),
            **{k: _text(py.get(k), f"{what}.py.{k}") for k in ("method", "run", "version")},
        }
    return out


def _date(value: Any, what: str) -> str | None:
    date = _text(value, what)
    if date is not None:
        try:
            datetime.date.fromisoformat(date)
        except ValueError:
            raise StatusError(f"{what} must be a YYYY-MM-DD date") from None
    return date


def _evidence(value: Any, what: str) -> dict[str, Any]:
    from metacheck.packs.manifest import FIELDS

    ev = dict(_object(value, what, _EVIDENCE_KEYS))
    if not isinstance(ev.get("id"), str) or not ev["id"]:
        raise StatusError(f"{what}.id must be a non-empty string")
    for key in ("unit", "protocol", "summary"):
        _text(ev.get(key), f"{what}.{key}")
    if ev.get("validators") is not None:
        _texts(ev["validators"], f"{what}.validators")
    _date(ev.get("date"), f"{what}.date")
    if ev.get("server_safe") is not None and not isinstance(ev["server_safe"], bool):
        raise StatusError(f"{what}.server_safe must be true, false or null")
    if ev.get("settings") is not None and not isinstance(ev["settings"], Mapping):
        raise StatusError(f"{what}.settings must be an object or null")
    if ev.get("corpus") is not None:
        corpus = dict(_object(ev["corpus"], f"{what}.corpus", _CORPUS_KEYS))
        for key in ("source", "sampling", "labels"):
            _text(corpus.get(key), f"{what}.corpus.{key}")
        _count(corpus.get("papers"), f"{what}.corpus.papers")
        if corpus.get("fields") is not None:
            fields = _texts(corpus["fields"], f"{what}.corpus.fields")
            unknown = sorted(set(fields) - set(FIELDS))
            if unknown:
                raise StatusError(f"{what}.corpus.fields has unknown fields {unknown}")
        ev["corpus"] = corpus
    counts = ev.get("counts")
    if counts is not None:
        counts = _object(counts, f"{what}.counts", _COUNT_KEYS)
        ev["counts"] = {k: _count(v, f"{what}.counts.{k}") for k, v in counts.items()}
    metrics = ev.get("metrics")
    if metrics is not None and (
        not isinstance(metrics, Mapping)
        or not all(isinstance(v, int | float) and not isinstance(v, bool) for v in metrics.values())
    ):
        raise StatusError(f"{what}.metrics must map names to numbers")
    if not counts and not metrics:
        raise StatusError(f"{what} has neither counts nor metrics")
    return ev


def _entry(value: Any, what: str) -> dict[str, Any]:
    entry = _object(value, what, _ENTRY_KEYS)
    provisional = entry.get("provisional", False)
    if not isinstance(provisional, bool):
        raise StatusError(f"{what}.provisional must be true or false")
    evidence = entry.get("evidence", [])
    if not isinstance(evidence, list):
        raise StatusError(f"{what}.evidence must be a list")
    return {
        "provisional": provisional,
        "note": _text(entry.get("note"), f"{what}.note"),
        "implementations": _implementations(
            entry.get("implementations", {}), f"{what}.implementations"
        ),
        "evidence": [_evidence(e, f"{what}.evidence[{i}]") for i, e in enumerate(evidence)],
    }


def validate_registry(data: Any, *, where: str = "validation.json") -> dict[str, Any]:
    """Check a parsed registry file (ECOSYSTEM.md §2.4) and return a normalised copy.

    Unknown keys are errors, so a misspelt key cannot hide evidence. PPV and
    sensitivity are never stored: they are derived from ``counts``. A
    provisional file marks every entry provisional, since pytacheck does not
    certify on the team's behalf; any other file names the registry commit
    it copies (``registry_commit``).
    """
    from metacheck.packs.manifest import PackError, validate_pack_name

    top = _object(data, where, _TOP_KEYS)
    schema = top.get("schema")
    if schema != SCHEMA:
        raise StatusError(f"{where} uses schema {schema!r}; this pytacheck reads schema {SCHEMA}")
    provisional = top.get("provisional", False)
    if not isinstance(provisional, bool):
        raise StatusError(f"{where}: provisional must be true or false")
    commit = _text(top.get("registry_commit"), f"{where}: registry_commit")
    if commit is not None and not _COMMIT_RE.match(commit):
        raise StatusError(f"{where}: registry_commit must be a full commit id")
    if commit is None and not provisional:
        raise StatusError(
            f"{where}: registry_commit is missing; a snapshot that is not provisional "
            "names the registry commit it copies"
        )
    teams = _texts(top.get("team_packs", []), f"{where}: team_packs")
    for name in teams:
        try:
            validate_pack_name(name, allow_reserved=True)
        except PackError as exc:
            raise StatusError(f"{where}: team_packs: {exc}") from None
    modules = top.get("modules", {})
    if not isinstance(modules, Mapping):
        raise StatusError(f"{where}: modules must be an object")
    entries: dict[str, dict[str, Any]] = {}
    ids: set[str] = set()
    for ref, value in modules.items():
        if not _REF_RE.match(ref):
            raise StatusError(f"{where}: {ref!r} is not a pack::module ref")
        what = f"{where}: modules.{ref}"
        entry = _entry(value, what)
        if provisional and not entry["provisional"]:
            raise StatusError(f"{what} must be marked provisional, as the file is")
        for ev in entry["evidence"]:
            if ev["id"] in ids:
                raise StatusError(f"{what}: the evidence id {ev['id']!r} is used twice")
            ids.add(ev["id"])
        entries[ref] = entry
    withdrawn = top.get("withdrawn", {})
    if not isinstance(withdrawn, Mapping):
        raise StatusError(f"{where}: withdrawn must be an object")
    gone: dict[str, dict[str, Any]] = {}
    for ref, value in withdrawn.items():
        if not _REF_RE.match(ref):
            raise StatusError(f"{where}: {ref!r} is not a pack::module ref")
        what = f"{where}: withdrawn.{ref}"
        item = _object(value, what, _WITHDRAWN_KEYS)
        reason = item.get("reason")
        if not isinstance(reason, str) or not reason:
            raise StatusError(f"{what}.reason must be a non-empty string")
        gone[ref] = {"date": _date(item.get("date"), f"{what}.date"), "reason": reason}
    return {
        "schema": SCHEMA,
        "provisional": provisional,
        "registry_commit": commit,
        "note": _text(top.get("note"), f"{where}: note"),
        "team_packs": teams,
        "modules": entries,
        "withdrawn": gone,
    }


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """A JSON object, refusing a repeated key: ``json`` would keep only the last one."""
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"the key {key!r} appears twice in one object")
        out[key] = value
    return out


@dataclass(frozen=True)
class Registry:
    """A checked registry file and the sha256 digest of its bytes.

    The digest is for the run record: it says which evidence a run's modules
    were judged against (ECOSYSTEM.md §2.3).
    """

    data: Mapping[str, Any]
    digest: str
    where: str

    @classmethod
    def from_bytes(cls, raw: bytes, *, where: str) -> Registry:
        try:
            data = json.loads(raw, object_pairs_hook=_unique_keys)
        except ValueError as exc:
            raise StatusError(f"Cannot read {where}: {exc}") from exc
        return cls(validate_registry(data, where=where), hashlib.sha256(raw).hexdigest(), where)

    @property
    def provisional(self) -> bool:
        return bool(self.data["provisional"])

    @property
    def registry_commit(self) -> str | None:
        return self.data["registry_commit"]  # type: ignore[no-any-return]

    @property
    def team_packs(self) -> frozenset[str]:
        return frozenset(self.data["team_packs"])

    @property
    def modules(self) -> Mapping[str, Mapping[str, Any]]:
        return self.data["modules"]  # type: ignore[no-any-return]

    @property
    def withdrawn(self) -> Mapping[str, Mapping[str, Any]]:
        return self.data["withdrawn"]  # type: ignore[no-any-return]


@cache
def snapshot() -> Registry:
    """The registry snapshot shipped with this pytacheck (read once per process)."""
    raw = resources.files("metacheck.resources").joinpath(SNAPSHOT).read_bytes()
    return Registry.from_bytes(raw, where=f"metacheck/resources/{SNAPSHOT}")


def read_registry(path: str | os.PathLike[str]) -> Registry:
    """Read and check a registry file."""
    p = Path(path)
    try:
        raw = p.read_bytes()
    except OSError as exc:
        raise StatusError(f"Cannot read {p}: {exc}") from exc
    return Registry.from_bytes(raw, where=str(p))


def check(registry: Registry | None = None) -> list[str]:
    """What is wrong with the registry snapshot, for ``pytacheck status --check`` (G9).

    The schema is checked when the file is read. This also checks that the
    file covers this pytacheck: ``metacheck`` is a team pack, every built-in
    has an entry, and every ``metacheck::`` ref names a metacheck module. An
    empty list means the check passes.
    """
    from metacheck.packs.registry import builtin_pack

    try:
        reg = registry or snapshot()
    except StatusError as exc:
        return [str(exc)]
    issues: list[str] = []
    if "metacheck" not in reg.team_packs:
        issues.append(f"{reg.where}: team_packs does not list metacheck")
    missing = [n for n in _builtin_names() if f"metacheck::{n}" not in reg.modules]
    if missing:
        issues.append(f"{reg.where}: no entry for the built-in modules {', '.join(missing)}")
    known = {*_builtin_names(), *builtin_pack().manifest.get("metacheck_modules", ())}
    for ref in [*reg.modules, *reg.withdrawn]:
        pack, _, name = ref.partition("::")
        if pack == "metacheck" and name not in known:
            issues.append(f"{reg.where}: {ref} is not a metacheck module")
    return issues


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------


def code_sha256(root: str | os.PathLike[str]) -> str:
    """The code digest a pack module's certification is bound to.

    Hashed like the ``pytacheck-tree-v1`` tree hash (:mod:`metacheck.packs.tree`),
    but over the files that decide what the modules do: every ``.py`` file,
    the ``.yaml`` files in the pack root and everything under ``data/``. Files
    under ``tests/``, ``docs/`` and ``.github/`` are left out, so a README or
    test edit does not decertify a module.
    """
    from metacheck.packs.tree import file_sha256, tree_files

    base = Path(root)
    h = hashlib.sha256()
    for rel in tree_files(base):
        if _is_code(rel):
            h.update(f"{file_sha256(base / rel)}  {rel}\n".encode("utf-8", "surrogateescape"))
    return h.hexdigest()


def _is_code(rel: str) -> bool:
    """Whether :func:`code_sha256` hashes a pack file (a POSIX path from the pack root)."""
    if rel.startswith(_NOT_CODE):
        return False
    if rel.startswith("data/") or rel.endswith(".py"):
        return True
    return "/" not in rel and rel.endswith(".yaml")


@dataclass(frozen=True)
class Status:
    """The derived status of one module.

    ``label`` is one of :data:`LABELS`. ``evidence`` holds the registry's
    records for the module. ``stale_note`` says why a certification does not
    hold for this code (``"validated on 0.4.0, not re-measured on 0.5.0"``),
    and ``note`` is the registry's note, the reason for a withdrawal, or the
    provisional wording before the registry's note. ``team`` is true for a
    module of a team pack, and ``source`` is how the module is loaded:
    ``builtin``, ``installed``, ``path`` or ``dist`` (its pack's kind),
    ``plugin``, ``file`` or ``object``.
    """

    ref: str
    label: str
    evidence: tuple[Mapping[str, Any], ...] = ()
    stale_note: str | None = None
    note: str | None = None
    provisional: bool = False
    team: bool = False
    source: str = "builtin"

    @property
    def opted_in(self) -> bool:
        """Whether the user added the module (from a pack, a plugin, a file or code)."""
        return self.source in _OPTED_IN

    @property
    def metrics(self) -> dict[str, Any]:
        """The first evidence record's counts, metrics and papers, with ``ppv`` and
        ``sensitivity`` derived from the counts."""
        from metacheck.packs.manifest import validation_metrics

        if not self.evidence:
            return {}
        ev = self.evidence[0]
        flat = {**(ev.get("counts") or {}), **(ev.get("metrics") or {})}
        papers = (ev.get("corpus") or {}).get("papers")
        if papers is not None:
            flat["papers"] = papers
        return validation_metrics(flat)

    @property
    def fields(self) -> tuple[str, ...]:
        """The fields of the first evidence record's corpus (empty when not stated)."""
        if not self.evidence:
            return ()
        return tuple((self.evidence[0].get("corpus") or {}).get("fields") or ())


def _origin(ref: Any) -> tuple[str, Pack | None, str]:
    """``(registry ref, pack, source)`` of a module ref, spec or decorated function."""
    from metacheck.packs.registry import builtin_pack, pack_for_spec

    if callable(ref) and hasattr(ref, "__pytacheck_module__"):
        ref = ref.__pytacheck_module__
    if isinstance(ref, ModuleSpec):
        pack = pack_for_spec(ref) if ref.pack else None
        if pack is None:
            return (ref.path or ref.name, None, "file" if ref.path else "object")
        name = Path(ref.path).stem if ref.path and pack.kind != "builtin" else ref.name
        return (f"{pack.name}::{name}", pack, pack.kind)
    kind, where = _locate(str(ref))
    if kind == "builtin":
        return (f"metacheck::{where}", builtin_pack(), "builtin")
    if kind == "pack":
        pack, name = where
        return (f"{pack.name}::{name}", pack, pack.kind)
    if kind == "entry_point":
        return (str(ref), None, "plugin")
    return (str(where), None, "file")


def _stale(entry: Mapping[str, Any], pack: Pack | None) -> str | None:
    """Why a certification does not hold for this code, or ``None`` when it does.

    A built-in is bound to the exact pytacheck releases it was re-measured on,
    a pack module to the code digests it was measured at (ECOSYSTEM.md §2.5).
    """
    py = entry["implementations"].get("py") or {}
    if pack is None:  # a file or plugin: registry refs name pack modules only
        return "not from a pack"
    if pack.kind == "builtin":
        releases, running = py.get("pytacheck") or [], pack.version
        if running in releases:
            return None
        if not releases:
            return f"measured on metacheck (R) only, not on pytacheck {running}"
        return f"validated on {releases[-1]}, not re-measured on {running}"
    digests, mine = py.get("code_sha256") or [], code_sha256(pack.root)
    if mine in digests:
        return None
    if not digests:
        return "measured on another implementation, not on this code"
    was = f"validated at {py['version']}" if py.get("version") else "validated"
    this = f"this is {pack.version}" if pack.version else "this is"
    return f"{was} (code {digests[-1][:12]}…), {this} (code {mine[:12]}…)"


def status(ref: Any, *, registry: Registry | None = None) -> Status:
    """The status of a module: a name, ``pack::name`` ref, path, spec or decorated function.

    * ``withdrawn``: the registry lists it under ``withdrawn``;
    * ``validated`` / ``external-validated``: a module of a team pack / of
      any other pack, with evidence that holds for this code (or that is
      provisional);
    * ``experimental`` / ``unvalidated``: the same without such evidence.
    """
    reg = registry or snapshot()
    key, pack, source = _origin(ref)
    team = pack is not None and pack.name in reg.team_packs
    entry = reg.modules.get(key) or _NO_ENTRY
    evidence = tuple(entry["evidence"])
    provisional, note = bool(entry["provisional"]), entry["note"]
    gone = reg.withdrawn.get(key)
    stale = None
    if gone is None and evidence and not provisional:
        stale = _stale(entry, pack)
    if gone is not None:
        label, note = "withdrawn", f"Withdrawn: {gone['reason']}"
    elif evidence and stale is None:
        label = "validated" if team else "external-validated"
        if provisional:  # the wording stays, whatever the entry's own note says
            note = f"{PROVISIONAL_NOTE}. {note}" if note else PROVISIONAL_NOTE
    else:
        label = "experimental" if team else "unvalidated"
    return Status(
        ref=key,
        label=label,
        evidence=evidence,
        stale_note=stale,
        note=note,
        provisional=provisional,
        team=team,
        source=source,
    )


def status_table(
    refs: Iterable[Any] | None = None, *, registry: Registry | None = None
) -> pd.DataFrame:
    """One row per module: its label, whether that is provisional, PPV, sensitivity,
    papers, fields, date and notes.

    *refs* defaults to the built-ins and the modules of every active pack.
    """
    import pandas as pd

    if refs is None:
        from metacheck.packs.registry import active_packs

        packs = active_packs(allow_local=_allow_local()).values()
        refs = [
            *(f"metacheck::{n}" for n in _builtin_names()),
            *(f"{p.name}::{m}" for p in packs if p.kind != "builtin" for m in p.modules()),
        ]
    rows = [status(ref, registry=registry) for ref in refs]
    metrics = [s.metrics for s in rows]
    first = [s.evidence[0] if s.evidence else {} for s in rows]
    return pd.DataFrame(
        {
            "module": pd.Series([s.ref for s in rows], dtype="string"),
            "label": pd.Series([s.label for s in rows], dtype="string"),
            "provisional": pd.Series([s.provisional for s in rows], dtype="boolean"),
            "ppv": pd.Series([m.get("ppv") for m in metrics], dtype="Float64"),
            "sensitivity": pd.Series([m.get("sensitivity") for m in metrics], dtype="Float64"),
            "papers": pd.Series([m.get("papers") for m in metrics], dtype="Int64"),
            "fields": pd.Series([", ".join(s.fields) or None for s in rows], dtype="string"),
            "date": pd.Series([e.get("date") for e in first], dtype="string"),
            "note": pd.Series(
                ["; ".join(n for n in (s.stale_note, s.note) if n) or None for s in rows],
                dtype="string",
            ),
        }
    )


# ---------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Policy:
    """A status policy: a name and the labels it accepts."""

    name: str
    labels: frozenset[str]

    def accepts(self, st: Status) -> bool:
        """Whether a module with status *st* runs under this policy.

        ``withdrawn`` is never accepted. ``experimental`` also accepts an
        unvalidated module the user added (ECOSYSTEM.md §3.4).
        """
        if st.label == "withdrawn":
            return False
        if st.label in self.labels:
            return True
        return self.name == "experimental" and st.label == "unvalidated" and st.opted_in


def parse_status(value: str | Policy) -> Policy:
    """A policy from its name (``validated``, ``certified``, ``experimental``,
    ``all``) or from a comma-separated list of labels (``"validated,experimental"``)."""
    if isinstance(value, Policy):
        return value
    if not isinstance(value, str):
        raise StatusError(f"A status must be a string, not {type(value).__name__}")
    text = value.strip().lower()
    if text in POLICIES:
        return Policy(text, POLICIES[text])
    parts = [p.strip() for p in text.split(",")]
    if "withdrawn" in parts:
        raise StatusError("Withdrawn modules never run, so 'withdrawn' cannot be a status")
    unknown = [p for p in parts if p not in LABELS]
    if unknown:
        raise StatusError(
            f"Unknown status {value!r}. Use {', '.join(POLICIES)}, or a comma-separated "
            f"list of the labels {', '.join(LABELS[:-1])}"
        )
    labels = [label for label in LABELS if label in parts]
    return Policy(",".join(labels), frozenset(labels))
