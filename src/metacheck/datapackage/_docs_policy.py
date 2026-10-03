"""The policy of the documentation check: its rules, the README template and the component catalogue.

See :mod:`metacheck.datapackage.docs` for the formats. Everything here is also
available from there.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

from metacheck.datapackage._findings import SEVERITIES

#: Every rule this check can raise and its default severity. Pass
#: ``severity={"rule-id": "info"}`` to :func:`check_docs` to change one.
RULES: dict[str, str] = {
    # readme_present
    "readme-missing": "problem",
    "readme-in-subfolder": "suggestion",
    "readme-several": "info",
    "readme-empty": "suggestion",
    # readme_format
    "readme-format": "suggestion",
    "readme-unreadable": "info",
    # readme_sections
    "section-missing": "suggestion",
    "section-empty": "suggestion",
    "section-optional-missing": "info",
    "section-optional-empty": "info",
    "field-missing": "suggestion",
    "field-invalid": "suggestion",
    # readme_placeholders
    "template-text": "suggestion",
    # readme_contact
    "contact-missing": "suggestion",
    # readme_file_list
    "file-list-incomplete": "suggestion",
    "file-not-described": "info",
    # licence
    "licence-missing": "suggestion",
    "licence-only-in-readme": "info",
    # components
    "component-missing": "problem",
    "component-recommended-missing": "suggestion",
    "component-undecided": "info",
    "component-format": "suggestion",
}


_DATA_DIR = ("data",)


# --------------------------------------------------------------------------
# policy: loading the template, the catalogue and the other options
# --------------------------------------------------------------------------


def _read_policy(spec: Any, what: str) -> Any:
    """A policy option as plain data: a path or JSON text is read, other values pass."""
    if isinstance(spec, os.PathLike) or (
        isinstance(spec, str) and not spec.lstrip().startswith(("{", "["))
    ):
        path = Path(spec).expanduser()
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except OSError as exc:
            raise ValueError(f"{what}: cannot read {path}: {exc.strerror or exc}") from exc
        except ValueError as exc:
            raise ValueError(f"{what}: {path} is not valid JSON: {exc}") from exc
    if isinstance(spec, str):
        try:
            return json.loads(spec)
        except ValueError as exc:
            raise ValueError(f"{what}: not valid JSON: {exc}") from exc
    return spec


@cache
def _default_policy(filename: str) -> Any:
    text = (
        resources.files("metacheck.datapackage").joinpath(*_DATA_DIR, filename).read_text("utf-8")
    )
    return json.loads(text)


def _regexes(values: Any, what: str) -> tuple[re.Pattern[str], ...]:
    """Compile a list of regexes (case-insensitive); a single string is a list of one."""
    if values is None:
        return ()
    if isinstance(values, str):
        values = [values]
    out = []
    for value in values:
        try:
            out.append(re.compile(value, re.IGNORECASE))
        except (re.error, TypeError) as exc:
            raise ValueError(f"{what}: invalid regular expression {value!r}: {exc}") from exc
    return tuple(out)


def _strings(values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        return (values,)
    return tuple(str(v) for v in values)


def _flag(value: Any, default: bool) -> bool:
    return default if value is None else bool(value)


@dataclass(frozen=True)
class FieldSpec:
    """A field a section should hold (a labelled line such as ``Email : ...``)."""

    id: str
    label: str
    match: tuple[re.Pattern[str], ...]
    required: bool = False
    expect: str | None = None


@dataclass(frozen=True)
class SectionSpec:
    """A section a README should have."""

    id: str
    title: str
    match: tuple[re.Pattern[str], ...]
    required: bool = False
    intro: bool = False
    hint: str = ""
    fields: tuple[FieldSpec, ...] = ()


@dataclass(frozen=True)
class ReadmeTemplate:
    """What a README should look like (:func:`load_readme_template`)."""

    name: str
    source: str
    sections: tuple[SectionSpec, ...]
    placeholders: tuple[re.Pattern[str], ...]

    def section(self, section_id: str) -> SectionSpec | None:
        """The section with this id, if the template has it."""
        return next((s for s in self.sections if s.id == section_id), None)

    def strip_placeholders(self, text: str) -> str:
        """*text* without the template text matched by :attr:`placeholders`."""
        for pattern in self.placeholders:
            text = pattern.sub(" ", text)
        return text


_EXPECT: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"),
    "url": re.compile(r"(?:https?|ftp)://[^\s<>]+", re.IGNORECASE),
    "doi": re.compile(r"\b10\.\d{4,9}/\S+", re.IGNORECASE),
}


def _field_spec(raw: Mapping[str, Any], where: str) -> FieldSpec:
    if "id" not in raw:
        raise ValueError(f"{where}: a field needs an id")
    fid = str(raw["id"])
    expect = raw.get("expect")
    if expect is not None and expect not in _EXPECT:
        raise ValueError(
            f"{where}, field {fid}: expect must be one of {sorted(_EXPECT)}, not {expect!r}"
        )
    return FieldSpec(
        id=fid,
        label=str(raw.get("label", fid)),
        match=_regexes(raw.get("match"), f"{where}, field {fid}"),
        required=_flag(raw.get("required"), False),
        expect=expect,
    )


def _section_spec(raw: Mapping[str, Any]) -> SectionSpec:
    if "id" not in raw:
        raise ValueError("README template: a section needs an id")
    sid = str(raw["id"])
    where = f"README template, section {sid}"
    return SectionSpec(
        id=sid,
        title=str(raw.get("title", sid)),
        match=_regexes(raw.get("match"), where),
        required=_flag(raw.get("required"), False),
        intro=_flag(raw.get("intro"), False),
        hint=str(raw.get("hint", "")),
        fields=tuple(_field_spec(f, where) for f in raw.get("fields") or ()),
    )


def load_readme_template(spec: Any = None) -> ReadmeTemplate:
    """A README template from a dict, a list of sections, a JSON file or JSON text.

    ``None`` gives the generic default. A template without ``placeholders``
    uses the default ones; ``"placeholders": []`` turns the check off.
    """
    if isinstance(spec, ReadmeTemplate):
        return spec
    raw = (
        _default_policy("docs_readme_generic.json")
        if spec is None
        else _read_policy(spec, "README template")
    )
    if isinstance(raw, list):
        raw = {"sections": raw}
    if not isinstance(raw, Mapping) or "sections" not in raw:
        raise ValueError("README template: expected an object with a list of sections")
    sections = tuple(_section_spec(s) for s in raw["sections"])
    ids = [s.id for s in sections]
    if len(set(ids)) != len(ids):
        raise ValueError("README template: section ids must be unique")
    placeholders = raw.get("placeholders")
    if placeholders is None:
        placeholders = _default_policy("docs_readme_generic.json")["placeholders"]
    return ReadmeTemplate(
        name=str(raw.get("name", "README template")),
        source=str(raw.get("source", "")),
        sections=sections,
        placeholders=_regexes(placeholders, "README template, placeholders"),
    )


_REQUIRED = ("always", "recommended", "optional", "human_participants")


@dataclass(frozen=True)
class ComponentSpec:
    """A part a package should have."""

    id: str
    title: str
    group: str = ""
    description: str = ""
    required: str = "optional"
    names: tuple[re.Pattern[str], ...] = ()
    paths: tuple[re.Pattern[str], ...] = ()
    folders: tuple[re.Pattern[str], ...] = ()
    exts: frozenset[str] = frozenset()
    data_types: frozenset[str] = frozenset()
    doc_roles: frozenset[str] = frozenset()
    readme_sections: tuple[str, ...] = ()
    formats: tuple[str, ...] = ()
    when: tuple[str, ...] = ()


@dataclass(frozen=True)
class ComponentCatalogue:
    """The parts a package should have (:func:`load_components`)."""

    name: str
    source: str
    components: tuple[ComponentSpec, ...]


def _ext_set(values: Any) -> frozenset[str]:
    return frozenset(v.lower().lstrip(".") for v in _strings(values))


def _component_spec(raw: Mapping[str, Any]) -> ComponentSpec:
    if "id" not in raw:
        raise ValueError("Component catalogue: a component needs an id")
    cid = str(raw["id"])
    where = f"Component catalogue, component {cid}"
    required = str(raw.get("required", "optional"))
    if required not in _REQUIRED:
        raise ValueError(f"{where}: required must be one of {_REQUIRED}, not {required!r}")
    match = raw.get("match") or {}
    return ComponentSpec(
        id=cid,
        title=str(raw.get("title", cid)),
        group=str(raw.get("group", "")),
        description=str(raw.get("description", "")),
        required=required,
        names=_regexes(match.get("names"), where),
        paths=_regexes(match.get("paths"), where),
        folders=_regexes(match.get("folders"), where),
        exts=_ext_set(match.get("exts")),
        data_types=frozenset(_strings(match.get("data_type"))),
        doc_roles=frozenset(_strings(match.get("doc_role"))),
        readme_sections=_strings(match.get("readme_sections")),
        formats=tuple(_ext_set(raw.get("formats")) and sorted(_ext_set(raw.get("formats")))),
        when=_strings(raw.get("when")),
    )


def load_components(spec: Any = None) -> ComponentCatalogue:
    """A component catalogue from a dict, a list of components, a JSON file or JSON text.

    ``None`` gives the generic default.
    """
    if isinstance(spec, ComponentCatalogue):
        return spec
    raw = (
        _default_policy("docs_components_generic.json")
        if spec is None
        else _read_policy(spec, "Component catalogue")
    )
    if isinstance(raw, list):
        raw = {"components": raw}
    if not isinstance(raw, Mapping) or "components" not in raw:
        raise ValueError("Component catalogue: expected an object with a list of components")
    components = tuple(_component_spec(c) for c in raw["components"])
    ids = [c.id for c in components]
    if len(set(ids)) != len(ids):
        raise ValueError("Component catalogue: component ids must be unique")
    return ComponentCatalogue(
        name=str(raw.get("name", "Component catalogue")),
        source=str(raw.get("source", "")),
        components=components,
    )


@dataclass(frozen=True)
class _Licence:
    id: str
    name: str
    match: tuple[re.Pattern[str], ...]
    needs_context: bool = False


def _load_licences(spec: Any = None) -> tuple[_Licence, ...]:
    raw = _default_policy("docs_licences.json") if spec is None else _read_policy(spec, "Licences")
    if isinstance(raw, Mapping):
        raw = raw.get("licences", [])
    return tuple(
        _Licence(
            id=str(item["id"]),
            name=str(item.get("name", item["id"])),
            match=_regexes(item.get("match"), f"Licences, {item['id']}"),
            needs_context=_flag(item.get("needs_context"), False),
        )
        for item in raw
    )


def _load_severity(spec: Any) -> dict[str, str]:
    """The severity overrides, checked. Rules that do not exist here are ignored."""
    if spec is None:
        return {}
    raw = _read_policy(spec, "Severity")
    if not isinstance(raw, Mapping):
        raise ValueError("Severity: expected an object that maps rule ids to severities")
    out = {}
    for rule, severity in raw.items():
        if severity not in SEVERITIES:
            raise ValueError(f"Severity of {rule!r} must be one of {SEVERITIES}, not {severity!r}")
        if rule in RULES:
            out[str(rule)] = str(severity)
    return out
