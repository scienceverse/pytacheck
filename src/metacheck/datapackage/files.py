"""File checks for a data package: junk files, file formats, file and folder names.

These are the checks behind ``datapackage::package_files``. Each one takes an
:class:`~metacheck.datapackage.OpenedPackage` and returns a *findings* table
(:data:`~metacheck.datapackage.FINDING_COLUMNS`), so an institution's own pack
can call them with its own policies:

========================  ======================================================
function                  checklist item
========================  ======================================================
:func:`junk_findings`     ``junk_files``: no junk or temporary files
:func:`format_findings`   ``file_formats``: files in preferred formats
:func:`name_findings`     ``file_names``: no spaces or special characters
:func:`convention_findings`  ``naming_convention``: consistent file names
:func:`check_files`       all four, and the checklist
========================  ======================================================

Policies are plain data. Every policy option takes a ``dict`` or ``list``, or the
path to a JSON file with the same content; ``None`` is the default policy in
``metacheck/datapackage/data``:

* ``junk``: the junk rules (:func:`load_junk`, ``files_junk.json``);
* ``formats``: the preferred file formats (:func:`load_formats`, ``formats_dans.json``);
* ``limits``: the file and folder name length limits (:func:`load_limits`,
  ``files_limits.json``; a partial dict changes only the limits it names);
* ``severity``: ``{rule id: "problem" | "suggestion" | "info" | "ignore"}`` (or the path
  of a JSON file with it), which
  overrides the default severity of a rule. ``"ignore"`` leaves a rule out of the
  findings (for junk, the files stay out of the other checks too). Rule ids that
  no rule has are ignored, so one dict can serve several checks.

Junk files and folders are reported once (a junk folder as one finding, not one per
file in it) and left out of the other checks. Paths in findings are relative to the
package's base folder; items next to an archive's wrapper folder keep their path
from the archive's root.

The name rules follow :mod:`metacheck.fileinfo.naming` (the R package's file-naming
check) for spaces, special characters and the length budgets, with these differences:
the rules also apply to folder names, a character that is not ASCII is reported as
``diacritics`` only (not as a special character too), the length rules have
fixed rule ids whatever the limit is, and the numbering rule ignores dates and
versions. Word separators, dates and numbering are checked by :func:`convention_findings`.
"""

from __future__ import annotations

import copy
import fnmatch
import functools
import json
import os
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from metacheck.datapackage._findings import (
    SEVERITIES,
    checklist_frame,
    findings_frame,
    status_from_findings,
)

__all__ = [
    "CHECK_TITLES",
    "DEFAULT_SEVERITY",
    "JunkScan",
    "check_files",
    "convention_findings",
    "files_checklist",
    "files_report",
    "files_summary_text",
    "format_findings",
    "junk_findings",
    "load_formats",
    "load_junk",
    "load_limits",
    "name_findings",
    "pdfa_part",
    "scan_junk",
]

_DATA = Path(__file__).parent / "data"

#: Checklist items of the file checks, in report order, with their titles.
CHECK_TITLES = {
    "junk_files": "No junk or temporary files",
    "file_formats": "Files in preferred formats",
    "file_names": "No spaces or special characters in names",
    "naming_convention": "Consistent file names",
}

#: Default severity of every rule except the junk rules (those are in the junk policy).
DEFAULT_SEVERITY = {
    "non-preferred": "suggestion",
    "unlisted": "info",
    "special-characters": "problem",
    "spaces": "suggestion",
    "diacritics": "problem",
    "path-too-long": "problem",
    "path-long": "suggestion",
    "folder-path-long": "suggestion",
    "file-name-long": "suggestion",
    "name-style": "suggestion",
    "date-format": "suggestion",
    "zero-padding": "suggestion",
}

_OVERRIDE_VALUES = (*SEVERITIES, "ignore")
_LEVELS = ("preferred", "non-preferred")
_MATCHES = ("name", "prefix", "suffix", "glob", "folder")

PolicySource = Mapping[str, Any] | Sequence[Any] | str | os.PathLike[str] | None
SeveritySource = Mapping[str, str] | str | os.PathLike[str] | None


# -- policies --------------------------------------------------------------------


@functools.cache
def _default_policy(name: str) -> Any:
    return json.loads((_DATA / name).read_text(encoding="utf-8"))


def _policy(source: PolicySource, default: str, what: str) -> Any:
    """The policy data: the default file, a JSON file, or the dict or list itself."""
    if source is None:
        return copy.deepcopy(_default_policy(default))
    if isinstance(source, str | os.PathLike):
        path = Path(source).expanduser()
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ValueError(f"Cannot read the {what} policy file {path}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"The {what} policy file {path} is not valid JSON: {exc}") from exc
    return copy.deepcopy(source)


def _requirement_pdfa(path: Path) -> bool:
    return pdfa_part(path) is not None


#: What an entry of the formats policy can require of a file, and what to say when
#: the file does not meet it.
_REQUIREMENTS: dict[str, tuple[Callable[[Path], bool], str]] = {
    "pdfa": (
        _requirement_pdfa,
        "This PDF does not say that it is PDF/A, the variant of PDF made for long-term archiving.",
    ),
}


def load_formats(policy: PolicySource = None) -> dict[str, Any]:
    """The preferred-formats policy, checked: ``{"name", "source", "formats": {ext: entry}}``.

    *policy* is ``None`` (the default, based on the DANS list), a dict, or the path
    of a JSON file. A dict without a ``"formats"`` key is taken to be the ``formats``
    mapping itself. Extensions are lower case without the dot (``"tar.gz"`` works
    for compound extensions). An entry is ``{"level": "preferred" | "non-preferred",
    "label": ..., "alternative": ...}``; ``"requires": "pdfa"`` (with ``"unmet_label"``)
    makes a preferred entry non-preferred for files that do not meet the
    requirement. Extensions that are not listed are *unlisted*.
    """
    raw = _policy(policy, "formats_dans.json", "formats")
    if not isinstance(raw, dict):
        raise ValueError("The formats policy must be a dict (or a JSON file holding one)")
    if "formats" not in raw:
        raw = {"formats": raw}
    formats: dict[str, dict[str, str]] = {}
    for ext, entry in (raw["formats"] or {}).items():
        key = str(ext).lower().lstrip(".")
        if not key or not isinstance(entry, dict):
            raise ValueError(f"formats policy: the entry for {ext!r} must be a dict")
        level = entry.get("level")
        if level not in _LEVELS:
            raise ValueError(
                f"formats policy: .{key}: level must be one of {_LEVELS}, not {level!r}"
            )
        requires = entry.get("requires", "")
        if requires and requires not in _REQUIREMENTS:
            raise ValueError(
                f"formats policy: .{key}: requires must be one of {sorted(_REQUIREMENTS)}, "
                f"not {requires!r}"
            )
        label = str(entry.get("label") or f".{key}")
        formats[key] = {
            "level": level,
            "label": label,
            "alternative": str(entry.get("alternative") or ""),
            "requires": str(requires or ""),
            "unmet_label": str(entry.get("unmet_label") or label),
        }
    return {
        "name": str(raw.get("name") or "the preferred formats"),
        "source": str(raw.get("source") or ""),
        "formats": formats,
    }


def load_junk(policy: PolicySource = None) -> list[dict[str, str]]:
    """The junk rules, checked: a list of ``{"rule", "match", "pattern", "severity", "explanation"}``.

    *policy* is ``None`` (the default rules), a list of rules, a dict with the rules
    under ``"rules"``, or the path of a JSON file holding either. ``match`` is
    ``"name"`` (a file with exactly this name), ``"prefix"`` or ``"suffix"`` (a file
    whose name starts or ends with it), ``"glob"`` (a file whose name fits a pattern
    with ``*`` and ``?``) or ``"folder"`` (a folder whose name fits the pattern).
    Matching ignores letter case. Several entries may share a ``rule`` id; a
    severity override applies to all of them.
    """
    raw = _policy(policy, "files_junk.json", "junk")
    if isinstance(raw, dict):
        raw = raw.get("rules", [])
    if not isinstance(raw, list):
        raise ValueError("The junk policy must be a list of rules (or a dict with 'rules')")
    rules: list[dict[str, str]] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(f"junk policy: rule {i + 1} must be a dict")
        match, pattern = entry.get("match"), entry.get("pattern")
        if match not in _MATCHES:
            raise ValueError(f"junk policy: rule {i + 1}: match must be one of {_MATCHES}")
        if not isinstance(pattern, str) or not pattern:
            raise ValueError(f"junk policy: rule {i + 1}: pattern must be a non-empty string")
        severity = entry.get("severity", "problem")
        if severity not in _OVERRIDE_VALUES:
            raise ValueError(
                f"junk policy: rule {i + 1}: severity must be one of {_OVERRIDE_VALUES}"
            )
        rule = str(entry.get("rule") or f"junk-{match}-{pattern}")
        rules.append(
            {
                "rule": rule,
                "match": match,
                "pattern": pattern,
                "severity": severity,
                "explanation": str(
                    entry.get("explanation") or "A junk or temporary file. Delete it."
                ),
            }
        )
    return rules


def load_limits(policy: PolicySource = None) -> dict[str, int]:
    """The length limits for file and folder names (a partial dict changes only what it names).

    ``path_too_long`` (255: a path over it is a problem), ``path_long`` (228: a
    path over it may pass the limit once unpacked in another folder),
    ``folder_path_long`` (100: the path of a folder) and ``file_name_long`` (50).
    """
    base: dict[str, Any] = {
        k: v for k, v in _default_policy("files_limits.json").items() if isinstance(v, int)
    }
    given = _policy(policy, "files_limits.json", "limits") if policy is not None else {}
    if not isinstance(given, dict):
        raise ValueError("The limits policy must be a dict (or a JSON file holding one)")
    for key, value in given.items():
        if key in base:
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"limits policy: {key} must be a whole number above 0")
            base[key] = value
    return base


def _overrides(severity: SeveritySource) -> dict[str, str]:
    """The severity overrides as a dict (from a dict or a JSON file), checked."""
    raw = _policy(severity, "", "severity") if severity is not None else {}
    if not isinstance(raw, dict):
        raise ValueError("The severity overrides must be a dict (or a JSON file holding one)")
    for rule, value in raw.items():
        if value not in _OVERRIDE_VALUES:
            raise ValueError(
                f"severity for {rule!r} must be one of {_OVERRIDE_VALUES}, not {value!r}"
            )
    return dict(raw)


# -- helpers ---------------------------------------------------------------------


def _n(count: int, word: str, plural: str | None = None) -> str:
    return f"{count} {word if count == 1 else (plural or word + 's')}"


def _real_ext(name: str) -> str:
    """The extension of *name*, lower case, or ``""`` when what follows the last dot is no extension.

    An extension has letters and digits only, at least one letter, and at most 10
    characters: so ``v0.6`` and ``Dr. Smith report`` have none.
    """
    stem = name.lstrip(".")
    if "." not in stem:
        return ""
    ext = stem.rsplit(".", 1)[1].lower()
    return ext if re.fullmatch(r"(?=[0-9]*[a-z])[a-z0-9]{1,10}", ext) else ""


def _stem(name: str) -> str:
    ext = _real_ext(name)
    return name[: -(len(ext) + 1)] if ext else name


def _row(path: str, kind: str, check: str, rule: str, severity: str, detail: str) -> dict[str, str]:
    return {
        "path": path,
        "kind": kind,
        "check": check,
        "rule": rule,
        "severity": severity,
        "detail": detail,
    }


def _sorted_frame(rows: list[dict[str, str]]) -> Any:
    rows.sort(key=lambda r: (r["path"].casefold(), r["path"]))
    return findings_frame(rows)


# -- junk ------------------------------------------------------------------------


class _JunkMatcher:
    """The junk rules compiled for quick matching of file and folder names."""

    def __init__(self, rules: Sequence[Mapping[str, str]]) -> None:
        self.names: dict[str, Mapping[str, str]] = {}
        self.prefixes: list[tuple[str, Mapping[str, str]]] = []
        self.suffixes: list[tuple[str, Mapping[str, str]]] = []
        self.globs: list[tuple[re.Pattern[str], Mapping[str, str]]] = []
        self.folders: dict[str, Mapping[str, str]] = {}
        self.folder_globs: list[tuple[re.Pattern[str], Mapping[str, str]]] = []
        for rule in rules:
            pattern = rule["pattern"].casefold()
            match = rule["match"]
            if match == "name":
                self.names.setdefault(pattern, rule)
            elif match == "prefix":
                self.prefixes.append((pattern, rule))
            elif match == "suffix":
                self.suffixes.append((pattern, rule))
            elif match == "glob":
                self.globs.append((re.compile(fnmatch.translate(pattern)), rule))
            elif any(c in pattern for c in "*?["):
                self.folder_globs.append((re.compile(fnmatch.translate(pattern)), rule))
            else:
                self.folders.setdefault(pattern, rule)
        self._files: dict[str, Mapping[str, str] | None] = {}
        self._dirs: dict[str, Mapping[str, str] | None] = {}

    def file(self, name: str) -> Mapping[str, str] | None:
        """The rule a file with this name breaks, if any."""
        if name not in self._files:
            low = name.casefold()
            hit = self.names.get(low)
            if hit is None:
                hit = next((r for p, r in self.prefixes if low.startswith(p)), None)
            if hit is None:
                hit = next((r for p, r in self.suffixes if low.endswith(p)), None)
            if hit is None:
                hit = next((r for rx, r in self.globs if rx.match(low)), None)
            self._files[name] = hit
        return self._files[name]

    def folder(self, name: str) -> Mapping[str, str] | None:
        """The rule a folder with this name breaks, if any."""
        if name not in self._dirs:
            low = name.casefold()
            hit = self.folders.get(low)
            if hit is None:
                hit = next((r for rx, r in self.folder_globs if rx.match(low)), None)
            self._dirs[name] = hit
        return self._dirs[name]


@dataclass
class JunkScan:
    """The result of :func:`scan_junk`.

    ``findings`` are the junk findings. ``files`` and ``dirs`` are the package's
    file and folder tables without the junk (and without what is inside a junk
    folder, and without the base folder itself): what the other checks look at.
    """

    findings: Any
    files: Any
    dirs: Any
    n_files: int
    n_dirs: int


def scan_junk(pkg: Any, junk: PolicySource = None, severity: SeveritySource = None) -> JunkScan:
    """Find the junk files and folders of *pkg* and what is left without them.

    A junk folder is one finding, however many files it holds; junk inside a junk
    folder is not reported again. Findings are ``check="junk_files"``, with the
    rule id and severity of the junk rule (see :func:`load_junk`); a rule whose
    severity is ``"ignore"`` is not reported but its files still count as junk.
    """
    matcher = _JunkMatcher(load_junk(junk))
    over = _overrides(severity)
    files, dirs = pkg.files(), pkg.dirs()
    rows: list[dict[str, str]] = []

    def add(rel: str, kind: str, rule: Mapping[str, str], detail: str) -> None:
        level = over.get(rule["rule"], rule["severity"])
        if level != "ignore":
            rows.append(_row(rel, kind, "junk_files", rule["rule"], level, detail))

    junk_dirs: set[str] = set()
    keep_dirs: list[bool] = []
    n_dirs = 0
    for path, rel, in_base, n_total in zip(
        dirs["path"].tolist(),
        dirs["rel"].tolist(),
        dirs["in_base"].tolist(),
        dirs["n_files_total"].tolist(),
        strict=True,
    ):
        if rel == "":  # the base folder itself
            keep_dirs.append(False)
            continue
        n_dirs += 1
        parts = path.split("/")
        if any("/".join(parts[:i]) in junk_dirs for i in range(1, len(parts))):
            keep_dirs.append(False)
            continue
        rule = matcher.folder(parts[-1])
        if rule is None:
            keep_dirs.append(True)
            continue
        junk_dirs.add(path)
        keep_dirs.append(False)
        detail = rule["explanation"]
        if n_total:
            detail += f" It holds {_n(int(n_total), 'file')}."
        if not in_base:
            detail += " It sits next to the package folder, not inside it."
        add(rel, "folder", rule, detail)

    inside: dict[str, bool] = {}

    def in_junk_folder(folder: str) -> bool:
        if folder not in inside:
            inside[folder] = bool(folder) and (
                folder in junk_dirs or in_junk_folder(folder.rpartition("/")[0])
            )
        return inside[folder]

    keep_files: list[bool] = []
    for rel, name, folder, in_base in zip(
        files["rel"].tolist(),
        files["name"].tolist(),
        files["folder"].tolist(),
        files["in_base"].tolist(),
        strict=True,
    ):
        if in_junk_folder(folder):
            keep_files.append(False)
            continue
        rule = matcher.file(name)
        if rule is None:
            keep_files.append(True)
            continue
        keep_files.append(False)
        detail = rule["explanation"]
        if not in_base:
            detail += " It sits next to the package folder, not inside it."
        add(rel, "file", rule, detail)

    return JunkScan(
        findings=_sorted_frame(rows),
        files=files[keep_files].reset_index(drop=True) if len(files) else files,
        dirs=dirs[keep_dirs].reset_index(drop=True) if len(dirs) else dirs,
        n_files=len(files),
        n_dirs=n_dirs,
    )


def junk_findings(pkg: Any, junk: PolicySource = None, severity: SeveritySource = None) -> Any:
    """The ``junk_files`` findings of *pkg* (see :func:`scan_junk`)."""
    return scan_junk(pkg, junk, severity).findings


# -- file formats ----------------------------------------------------------------

_PDFA_PART = re.compile(rb"""pdfaid:part\s*(?:=\s*["']\s*(\d+)\s*["']|>\s*(\d+)\s*<)""")
_PDFA_CONFORMANCE = re.compile(
    rb"""pdfaid:conformance\s*(?:=\s*["']\s*([A-Za-z])\s*["']|>\s*([A-Za-z])\s*<)"""
)


def pdfa_part(path: str | os.PathLike[str]) -> str | None:
    """The PDF/A level a PDF declares in its XMP metadata (``"2b"``), or ``None``.

    Looks for the ``pdfaid:part`` and ``pdfaid:conformance`` properties of the
    PDF/A identification schema, written as an attribute or as an element, in the
    file's raw bytes, in chunks, so XMP metadata that a tool stored compressed is not
    seen. The result is the part with the conformance level in lower case: ``"1b"``,
    ``"2a"``, ``"3u"``; PDF/A-4 may have no level (``"4"``) or ``"4e"``/``"4f"``.
    ``None`` means the file does not declare PDF/A; it is not a validation that a
    file declaring PDF/A really conforms. A file that cannot be read raises
    :class:`OSError`.
    """
    needle = b"pdfaid:part"
    keep = 4096
    tail = b""
    with open(path, "rb") as fh:
        while data := fh.read(1 << 20):
            buf = tail + data
            start = buf.find(needle)
            if start >= 0:
                buf += fh.read(8192)  # the rest of the description
                while start >= 0:
                    level = _declared_level(buf, start, keep)
                    if level:
                        return level
                    start = buf.find(needle, start + 1)
            tail = buf[-keep:]
    return None


def _declared_level(buf: bytes, start: int, width: int) -> str | None:
    """The PDF/A level written around the ``pdfaid:part`` found at *start* in *buf*."""
    lo = max(0, start - width)
    window = buf[lo : start + width]
    part = _PDFA_PART.match(window, start - lo)
    if part is None:
        return None
    level = part.group(1) or part.group(2)
    conf = _PDFA_CONFORMANCE.search(window)
    letter = (conf.group(1) or conf.group(2)) if conf else b""
    return (level + letter.lower()).decode("ascii")


def _format_entry(
    name: str, formats: Mapping[str, Mapping[str, str]]
) -> tuple[str, Mapping[str, str] | None]:
    """The extension of *name* and its policy entry (the longest matching extension wins)."""
    parts = name.lstrip(".").lower().split(".")
    for i in range(1, len(parts)):
        entry = formats.get(".".join(parts[i:]))
        if entry is not None:
            return ".".join(parts[i:]), entry
    return _real_ext(name), None


def format_findings(
    pkg: Any,
    formats: PolicySource = None,
    *,
    severity: SeveritySource = None,
    junk: PolicySource = None,
    scan: JunkScan | None = None,
) -> Any:
    """The ``file_formats`` findings of *pkg*: files not in a preferred format.

    A file in a non-preferred format is a ``non-preferred`` finding (default
    severity ``suggestion``) that says which format to use instead; a file whose
    extension the policy does not list is an ``unlisted`` finding (``info``).
    A file without an extension is left alone. A PDF is preferred only if it
    declares PDF/A (:func:`pdfa_part`) when its policy entry says ``"requires":
    "pdfa"``; a file that cannot be read, and a symbolic link, is not checked
    for that. See :func:`load_formats` for the policy.
    """
    policy = load_formats(formats)["formats"]
    over = _overrides(severity)
    if scan is None:
        scan = scan_junk(pkg, junk, severity)
    sev_np = over.get("non-preferred", DEFAULT_SEVERITY["non-preferred"])
    sev_un = over.get("unlisted", DEFAULT_SEVERITY["unlisted"])
    rows: list[dict[str, str]] = []
    files = scan.files
    for rel, path, name, link in zip(
        files["rel"].tolist(),
        files["path"].tolist(),
        files["name"].tolist(),
        files["link"].tolist(),
        strict=True,
    ):
        ext, entry = _format_entry(name, policy)
        shown = name[len(name) - len(ext) :]  # the extension as written
        if entry is None:
            if ext and sev_un != "ignore":
                rows.append(
                    _row(
                        rel,
                        "file",
                        "file_formats",
                        "unlisted",
                        sev_un,
                        f"The format .{shown} is not on the list of preferred formats, so we "
                        "cannot say whether it suits long-term archiving. Check that others can "
                        "open it without special software.",
                    )
                )
            continue
        label, why = entry["label"], ""
        if entry["level"] == "preferred":
            if not entry["requires"] or link:
                continue
            check, unmet = _REQUIREMENTS[entry["requires"]]
            try:
                met = check(pkg.root / path)
            except OSError:
                continue
            if met:
                continue
            label, why = entry["unmet_label"], unmet
        if sev_np == "ignore":
            continue
        alt = f" Save it as {entry['alternative']}." if entry["alternative"] else ""
        if why:
            detail = why + alt
        else:
            detail = f"{label} (.{shown}) is not a preferred format for long-term archiving.{alt}"
        rows.append(_row(rel, "file", "file_formats", "non-preferred", sev_np, detail))
    return _sorted_frame(rows)


# -- file and folder names -------------------------------------------------------

_SPECIAL = re.compile(r"[^A-Za-z0-9._ \-\u0080-\U0010ffff]")
_NON_ASCII = re.compile(r"[^\x00-\x7f]")


def _show(chars: Sequence[str], limit: int = 8) -> str:
    shown = [c if c.isprintable() else repr(c)[1:-1] for c in dict.fromkeys(chars)]
    text = " ".join(shown[:limit])
    return text + (" ..." if len(shown) > limit else "")


def _name_issues(name: str) -> list[tuple[str, str]]:
    """``(rule, detail)`` for the spaces, special characters and non-ASCII letters in *name*."""
    out: list[tuple[str, str]] = []
    special = _SPECIAL.findall(name)
    if special:
        many = len(set(special)) > 1
        out.append(
            (
                "special-characters",
                f"Contains the special character{'s' if many else ''} {_show(special)}. "
                "Use only letters, digits, underscores (_), hyphens (-) and dots (.) in names.",
            )
        )
    if " " in name:
        out.append(
            (
                "spaces",
                "Contains a space. Use an underscore (_) or a hyphen (-) instead, so that the name "
                "works on every system and in code.",
            )
        )
    non_ascii = _NON_ASCII.findall(name)
    if non_ascii:
        out.append(
            (
                "diacritics",
                f"Contains non-ASCII characters ({_show(non_ascii)}). Use plain letters "
                "(for example e instead of é).",
            )
        )
    return out


def name_findings(
    pkg: Any,
    limits: PolicySource = None,
    *,
    severity: SeveritySource = None,
    junk: PolicySource = None,
    scan: JunkScan | None = None,
) -> Any:
    """The ``file_names`` findings of *pkg*: spaces, special characters, long names and paths.

    Rules, with their default severity: ``special-characters`` (an ASCII character
    other than letters, digits, ``_``, ``-`` and ``.``; problem), ``spaces``
    (suggestion), ``diacritics`` (a character that is not ASCII; problem),
    ``path-too-long`` (the path is longer than the ``path_too_long`` limit;
    problem), ``path-long`` (longer than ``path_long``; suggestion),
    ``folder-path-long`` (a folder path longer than ``folder_path_long``, reported
    for the outermost such folder only; suggestion) and ``file-name-long`` (a file
    name longer than ``file_name_long``; suggestion). The character rules apply to
    file and folder names, each folder once. See :func:`load_limits`.
    """
    lim = load_limits(limits)
    over = _overrides(severity)
    if scan is None:
        scan = scan_junk(pkg, junk, severity)

    def level(rule: str) -> str:
        return over.get(rule, DEFAULT_SEVERITY[rule])

    rows: list[dict[str, str]] = []

    def add(rel: str, kind: str, rule: str, detail: str) -> None:
        if level(rule) != "ignore":
            rows.append(_row(rel, kind, "file_names", rule, level(rule), detail))

    for rel, name in zip(scan.files["rel"].tolist(), scan.files["name"].tolist(), strict=True):
        for rule, detail in _name_issues(name):
            add(rel, "file", rule, detail)
        if len(rel) > lim["path_too_long"]:
            add(
                rel,
                "file",
                "path-too-long",
                f"The path is {len(rel)} characters long. Windows and many tools cannot open "
                f"paths of more than {lim['path_too_long']} characters. Shorten the file name "
                "or the names of the folders above it.",
            )
        elif len(rel) > lim["path_long"]:
            add(
                rel,
                "file",
                "path-long",
                f"The path is {len(rel)} characters long. Once the package is unpacked in "
                f"another folder, paths of more than {lim['path_long']} characters can pass the "
                f"{lim['path_too_long']}-character limit. Shorten the file name or the names "
                "of the folders above it.",
            )
        if len(name) > lim["file_name_long"]:
            add(
                rel,
                "file",
                "file-name-long",
                f"The file name is {len(name)} characters long (more than "
                f"{lim['file_name_long']}). Use a shorter name that still says what the file is.",
            )

    long_folders = {
        p
        for p, rel in zip(scan.dirs["path"].tolist(), scan.dirs["rel"].tolist(), strict=True)
        if len(rel) > lim["folder_path_long"]
    }
    for path, rel, name, parent in zip(
        scan.dirs["path"].tolist(),
        scan.dirs["rel"].tolist(),
        scan.dirs["name"].tolist(),
        scan.dirs["parent"].tolist(),
        strict=True,
    ):
        for rule, detail in _name_issues(name):
            add(rel, "folder", rule, detail)
        if path in long_folders and parent not in long_folders:
            add(
                rel,
                "folder",
                "folder-path-long",
                f"The path of this folder is {len(rel)} characters long (more than "
                f"{lim['folder_path_long']}), and so are the paths inside it. Use shorter "
                "names for this folder or the folders above it.",
            )
    return _sorted_frame(rows)


# -- naming convention -----------------------------------------------------------


@dataclass(frozen=True)
class _Span:
    """A date or version number inside a name."""

    start: int
    end: int
    kind: str  # "date" or "version"
    text: str
    problem: str = ""  # "separator", "unpadded", "order", "digits8", "digits6"; "" for ISO dates
    iso: str = ""  # the date as YYYY-MM-DD, when it is clear
    ambiguous: bool = False  # could be day-month or month-day


_VERSION = re.compile(
    r"(?<![A-Za-z0-9])(?:v|ver|version|rev)[-_ ]?\d+(?:\.\d+)*(?![A-Za-z0-9])", re.I
)
_TRIPLE = re.compile(r"(?<![0-9])(\d{1,4})([-._])(\d{1,2})\2(\d{1,4})(?![0-9])")
_EIGHT = re.compile(r"(?<![0-9])(\d{8})(?![0-9])")
_SIX = re.compile(r"(?<![0-9])(\d{6})(?![0-9])")
_WORD_BEFORE = re.compile(r"([A-Za-z]+)[-_ ]?$")

#: Words that make a number that follows an identifier, not a date.
_ID_WORDS = frozenset(
    {
        *("id", "sub", "subj", "subject", "participant", "pp", "ppn", "p", "s", "n"),
        *("no", "nr", "num", "number", "case", "sample", "run", "trial", "record", "patient"),
        *("pt", "order", "ticket", "batch", "ref", "isbn", "issn", "pmid", "doi", "v", "ver"),
        *("version", "rev", "build", "session", "site", "wave", "group", "cell", "plot"),
        *("unit", "item"),
    }
)

_DAYS = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)  # February: any year


def _valid_date(year: int, month: int, day: int) -> bool:
    try:
        date(year, month, day)
    except ValueError:
        return False
    return True


def _scan_dates(text: str) -> list[_Span]:
    """Dates and versions in *text* (no two overlap), in order."""
    found: list[_Span] = []

    def after_id_word(start: int) -> bool:
        m = _WORD_BEFORE.search(text[:start])
        return m is not None and m.group(1).casefold() in _ID_WORDS

    def delimited(m: re.Match[str]) -> bool:
        before = text[m.start() - 1] if m.start() else " "
        after = text[m.end()] if m.end() < len(text) else " "
        return not before.isalnum() and not after.isalnum()

    for m in _TRIPLE.finditer(text):
        a, sep, b, c = m.groups()
        before = text[m.start() - 1] if m.start() else ""
        after = text[m.end()] if m.end() < len(text) else ""
        if before in ("v", "V"):
            continue
        if sep == "." and (
            (m.start() >= 2 and text[m.start() - 1] == "." and text[m.start() - 2].isdigit())
            or (after == "." and m.end() + 1 < len(text) and text[m.end() + 1].isdigit())
        ):
            continue  # part of a longer dotted number: a version or an address
        text_m = m.group(0)
        if len(a) == 4 and len(c) <= 2:
            y, mo, d = int(a), int(b), int(c)
            if not (1000 <= y <= 2999 and _valid_date(y, mo, d)):
                continue
            iso = f"{y:04d}-{mo:02d}-{d:02d}"
            if sep == "-" and len(b) == 2 and len(c) == 2:
                found.append(_Span(m.start(), m.end(), "date", text_m))
            elif sep == "-":
                found.append(_Span(m.start(), m.end(), "date", text_m, "unpadded", iso))
            elif sep == "_" or (len(b) == 2 and len(c) == 2):
                found.append(_Span(m.start(), m.end(), "date", text_m, "separator", iso))
        elif len(c) == 4 and len(a) <= 2 and 1900 <= int(c) <= 2099:
            y, x, z = int(c), int(a), int(b)
            dmy, mdy = _valid_date(y, z, x), _valid_date(y, x, z)
            if dmy or mdy:
                both = dmy and mdy and x != z
                iso = (
                    ""
                    if both
                    else (f"{y:04d}-{z:02d}-{x:02d}" if dmy else f"{y:04d}-{x:02d}-{z:02d}")
                )
                found.append(_Span(m.start(), m.end(), "date", text_m, "order", iso, both))

    for m in _EIGHT.finditer(text):
        s = m.group(1)
        y, mo, d = int(s[:4]), int(s[4:6]), int(s[6:])
        if 1000 <= y <= 2999 and _valid_date(y, mo, d):
            found.append(_Span(m.start(), m.end(), "date", s))
            continue
        year = int(s[4:])
        if not (1900 <= year <= 2099) or not delimited(m) or after_id_word(m.start()):
            continue
        dmy, mdy = (
            _valid_date(year, int(s[2:4]), int(s[:2])),
            _valid_date(year, int(s[:2]), int(s[2:4])),
        )
        if dmy or mdy:
            both = dmy and mdy and s[:2] != s[2:4]
            iso = (
                "" if both else (f"{year}-{s[2:4]}-{s[:2]}" if dmy else f"{year}-{s[:2]}-{s[2:4]}")
            )
            found.append(_Span(m.start(), m.end(), "date", s, "digits8", iso, both))

    for m in _SIX.finditer(text):
        s = m.group(1)
        month = int(s[2:4])
        if not (1 <= month <= 12) or not delimited(m) or after_id_word(m.start()):
            continue
        as_ddmmyy = 1 <= int(s[:2]) <= _DAYS[month - 1]
        as_yymmdd = 1 <= int(s[4:]) <= _DAYS[month - 1]
        if as_ddmmyy and not as_yymmdd:
            found.append(_Span(m.start(), m.end(), "date", s, "digits6"))

    found.extend(_Span(m.start(), m.end(), "version", m.group(0)) for m in _VERSION.finditer(text))

    found.sort(key=lambda sp: (sp.start, -(sp.end - sp.start)))
    spans: list[_Span] = []
    for sp in found:
        if not spans or sp.start >= spans[-1].end:
            spans.append(sp)
    return spans


def _mask(text: str, spans: Sequence[_Span]) -> str:
    """*text* with every date and version replaced by NUL characters of the same length."""
    out = text
    for sp in spans:
        out = out[: sp.start] + "\x00" * (sp.end - sp.start) + out[sp.end :]
    return out


#: Word-separator styles, as the keys of the style count.
_STYLE_ORDER = ("_", "-", "-_", "camel")
_STYLE_LABEL = {
    "_": "underscores (my_file)",
    "-": "hyphens (my-file)",
    "-_": "hyphens and underscores (raw_data-v1)",
    "camel": "capital letters instead of separators (myFile)",
}


def _style(masked: str) -> str | None:
    """The word-separator style of a name, or ``None`` for a name with one word (or spaces).

    A separator counts when it sits next to a word. The inside of a date or version
    is masked, so ``raw_data-2020-11-26-v0.2`` uses hyphens between its fields and
    underscores inside them (style ``"-_"``).
    """
    if " " in masked:
        return None
    parts = re.split(r"([-_]+)", masked)
    seps: set[str] = set()
    for i in range(1, len(parts), 2):
        left, right = parts[i - 1], parts[i + 1]
        if left and right and (any(c.isalpha() for c in left) or any(c.isalpha() for c in right)):
            seps.update(parts[i])
    if seps:
        return "".join(sorted(seps))
    return "camel" if re.search(r"[a-z][A-Z]", masked) else None


def _compatible(style: str, dominant: str) -> bool:
    """Whether a name of *style* fits the dominant style (using fewer separators does)."""
    if style == dominant:
        return True
    return style != "camel" and dominant != "camel" and set(style) <= set(dominant)


def _restyle(text: str, spans: Sequence[_Span], sep: str) -> str:
    """*text* with the separators outside dates and versions changed to *sep*."""
    out: list[str] = []
    pos = 0
    for sp in spans:
        out.append(re.sub(r"[-_]+", sep, text[pos : sp.start]))
        out.append(text[sp.start : sp.end])
        pos = sp.end
    out.append(re.sub(r"[-_]+", sep, text[pos:]))
    return "".join(out)


def _date_detail(sp: _Span) -> str:
    if sp.problem == "unpadded":
        return (
            f"The date {sp.text} should have two digits for the month and the day: "
            f"{sp.iso}. Write dates as YYYY-MM-DD."
        )
    if sp.problem == "digits6":
        return (
            f"{sp.text} looks like a date written day-month-year. Write dates as YYYY-MM-DD "
            "(or YYYYMMDD), so they sort in date order and cannot be misread."
        )
    if sp.ambiguous:
        return (
            f"The date {sp.text} could be read as day-month-year or as month-day-year. "
            "Write dates as YYYY-MM-DD, so they sort in date order and cannot be misread."
        )
    return (
        f"The date {sp.text} is not written as YYYY-MM-DD ({sp.iso}). "
        "Write dates that way, so they sort in date order and cannot be misread."
    )


@dataclass(frozen=True)
class _Item:
    rel: str
    kind: str  # "file" or "folder"
    name: str
    stem: str
    folder: str  # the folder it is in
    spans: tuple[_Span, ...]  # its dates and versions


def convention_findings(
    pkg: Any,
    *,
    min_names_for_style: int = 5,
    severity: SeveritySource = None,
    junk: PolicySource = None,
    scan: JunkScan | None = None,
) -> Any:
    """The ``naming_convention`` findings of *pkg*: names that do not follow the others.

    All findings are suggestions by default. Rules:

    * ``name-style``: the names that separate words differently from most of the
      package: underscores (``my_file``), hyphens (``my-file``), both (hyphens
      between the fields of a name and underscores inside them, as in
      ``raw_data-2020-11-26-v0.2``) or capital letters (``myFile``). It is only
      checked when at least *min_names_for_style* file and folder names have
      several words and the commonest style covers at least half of them. A name
      that uses fewer separators than the commonest style is fine, and a name with
      spaces is left to the ``spaces`` rule of :func:`name_findings`.
    * ``date-format``: dates not written as ISO 8601 (``YYYY-MM-DD`` or
      ``YYYYMMDD``): ``26-11-2020``, ``26.11.2020``, ``11-26-2020``,
      ``2020_11_26``, ``2020-1-5``, and runs of 6 digits that are a date only as
      day-month-year (``DDMMYY``) and of 8 digits that are one only as ``DDMMYYYY`` or
      ``MMDDYYYY``, when they stand between separators and do not follow a word like
      ``id`` or ``sub``.
    * ``zero-padding``: files in one folder that differ only in one number but
      write it with different numbers of digits (``trial1`` and ``trial10``); pad
      them (``trial01``). Numbers that are part of a date or version (``v2``) are
      ignored.
    """
    over = _overrides(severity)
    if scan is None:
        scan = scan_junk(pkg, junk, severity)

    def level(rule: str) -> str:
        return over.get(rule, DEFAULT_SEVERITY[rule])

    items: list[_Item] = []
    for rel, name, folder in zip(
        scan.files["rel"].tolist(),
        scan.files["name"].tolist(),
        scan.files["folder"].tolist(),
        strict=True,
    ):
        stem = _stem(name)
        items.append(_Item(rel, "file", name, stem, folder, tuple(_scan_dates(stem))))
    for rel, name, parent in zip(
        scan.dirs["rel"].tolist(),
        scan.dirs["name"].tolist(),
        scan.dirs["parent"].tolist(),
        strict=True,
    ):
        items.append(_Item(rel, "folder", name, name, parent, tuple(_scan_dates(name))))

    rows: list[dict[str, str]] = []
    # dates
    if level("date-format") != "ignore":
        rows.extend(
            _row(
                it.rel,
                it.kind,
                "naming_convention",
                "date-format",
                level("date-format"),
                _date_detail(sp),
            )
            for it in items
            for sp in it.spans
            if sp.problem
        )

    # word separators
    if level("name-style") != "ignore":
        styles = [_style(_mask(it.stem, it.spans)) for it in items]
        counts = Counter(s for s in styles if s is not None)
        total = sum(counts.values())
        if total >= max(min_names_for_style, 1):
            dominant, share = max(
                counts.items(), key=lambda kv: (kv[1], -_STYLE_ORDER.index(kv[0]))
            )
            if share * 2 >= total:
                for it, style in zip(items, styles, strict=True):
                    if style is None or _compatible(style, dominant):
                        continue
                    detail = (
                        f"Words are separated with {_STYLE_LABEL[style]} here, but {share} of the "
                        f"{total} names with several words use {_STYLE_LABEL[dominant]}."
                    )
                    if dominant in ("_", "-") and style != "camel":
                        new = _restyle(it.stem, it.spans, dominant) + it.name[len(it.stem) :]
                        if new != it.name:
                            detail += f" Suggested name: {new}"
                    rows.append(
                        _row(
                            it.rel,
                            it.kind,
                            "naming_convention",
                            "name-style",
                            level("name-style"),
                            detail,
                        )
                    )

    # zero padding
    if level("zero-padding") != "ignore":
        families: dict[tuple[str, str, str, str], list[tuple[_Item, int, int]]] = defaultdict(list)
        for it in items:
            if it.kind != "file":
                continue
            masked = _mask(it.stem, it.spans)
            tail = it.name[len(it.stem) :]
            for m in re.finditer(r"[0-9]+", masked):
                key = (it.folder, it.stem[: m.start()], it.stem[m.end() :], tail)
                families[key].append((it, m.start(), m.end()))
        for members in families.values():
            widths = {e - s for _, s, e in members}
            if len(members) < 2 or len(widths) < 2:
                continue
            target = max(widths)
            widest = next(it for it, s, e in members if e - s == target)
            for it, s, e in members:
                if e - s >= target:
                    continue
                new = (
                    it.stem[:s] + it.stem[s:e].zfill(target) + it.stem[e:] + it.name[len(it.stem) :]
                )
                rows.append(
                    _row(
                        it.rel,
                        "file",
                        "naming_convention",
                        "zero-padding",
                        level("zero-padding"),
                        f"The number has {_n(e - s, 'digit')}, but in files that differ only in this "
                        f"number (such as {widest.name}) it has {target}, so the files sort out of "
                        f"order (1, 10, 2). Pad the number with zeros: {new}",
                    )
                )
    return _sorted_frame(rows)


# -- all checks, the checklist and the report ------------------------------------


def _distinct(findings: Any, check: str, severity: str | None = None) -> int:
    sub = findings[findings["check"] == check]
    if severity is not None:
        sub = sub[sub["severity"] == severity]
    return int(sub["path"].nunique())


def files_checklist(findings: Any, scan: JunkScan) -> Any:
    """The checklist (:func:`~metacheck.datapackage.checklist_frame`) of the four file checks.

    ``fail`` for any problem, ``warn`` for any suggestion, else ``pass``; ``na``
    when there is nothing to check (an empty package; for the checks other than
    the junk check, nothing but junk).
    """
    empty = scan.n_files == 0 and scan.n_dirs == 0
    no_files = len(scan.files) == 0
    no_names = no_files and len(scan.dirs) == 0
    rows: list[dict[str, str]] = []

    def by(check: str, severity: str) -> int:
        return int(((findings["check"] == check) & (findings["severity"] == severity)).sum())

    def add(item: str, detail: str, *, na: bool = False, **kwargs: str) -> None:
        status = "na" if na else status_from_findings(findings, item, **kwargs)
        rows.append({"item": item, "title": CHECK_TITLES[item], "status": status, "detail": detail})

    n_junk = _distinct(findings, "junk_files")
    kinds = findings.loc[findings["check"] == "junk_files", "kind"].tolist()
    if empty:
        add("junk_files", "The package is empty.", na=True)
    elif n_junk == 0:
        add("junk_files", "No junk or temporary files found.")
    else:
        of = [_n(kinds.count(kind), kind) for kind in ("file", "folder") if kinds.count(kind)]
        add("junk_files", f"Found {_n(n_junk, 'junk or temporary item')} ({', '.join(of)}).")

    n_np, n_un = by("file_formats", "suggestion"), by("file_formats", "info")
    n_np += by("file_formats", "problem")
    if no_files:
        add("file_formats", "No files to check.", na=True)
    elif n_np == 0 and n_un == 0:
        add("file_formats", f"All {_n(len(scan.files), 'file')} are in preferred formats.")
    else:
        bits = []
        if n_np:
            bits.append(f"{_n(n_np, 'file')} not in a preferred format")
        if n_un:
            bits.append(f"{_n(n_un, 'file')} in a format that is not on the list")
        add("file_formats", ", ".join(bits).capitalize() + ".")

    n_bad, n_any = _distinct(findings, "file_names", "problem"), _distinct(findings, "file_names")
    if no_names:
        add("file_names", "No files to check.", na=True)
    elif n_any == 0:
        add("file_names", "No spaces, special characters or too long names found.")
    else:
        parts = []
        if n_bad:
            parts.append(f"{_n(n_bad, 'name')} to fix")
        if n_any - n_bad:
            parts.append(f"{_n(n_any - n_bad, 'name')} to improve")
        add("file_names", f"{' and '.join(parts).capitalize()}.")

    n_conv = _distinct(findings, "naming_convention")
    if no_names:
        add("naming_convention", "No files to check.", na=True)
    elif n_conv == 0:
        add("naming_convention", "The names are consistent.")
    else:
        add("naming_convention", f"{_n(n_conv, 'name')} to make consistent.")
    return checklist_frame(rows)


def check_files(
    pkg: Any,
    *,
    formats: PolicySource = None,
    junk: PolicySource = None,
    severity: SeveritySource = None,
    limits: PolicySource = None,
    min_names_for_style: int = 5,
) -> tuple[Any, Any]:
    """Run the four file checks on *pkg*: ``(findings, checklist)``.

    The findings of all checks in one table (in the order of :data:`CHECK_TITLES`)
    and the checklist from :func:`files_checklist`. Options as in
    :func:`junk_findings`, :func:`format_findings`, :func:`name_findings` and
    :func:`convention_findings`.
    """
    import pandas as pd

    scan = scan_junk(pkg, junk, severity)
    parts = [
        scan.findings,
        format_findings(pkg, formats, severity=severity, scan=scan),
        name_findings(pkg, limits, severity=severity, scan=scan),
        convention_findings(
            pkg, min_names_for_style=min_names_for_style, severity=severity, scan=scan
        ),
    ]
    findings = pd.concat(parts, ignore_index=True)
    return findings, files_checklist(findings, scan)


def files_summary_text(findings: Any, checklist: Any, n_files: int) -> str:
    """One plain sentence on what :func:`check_files` found."""
    if n_files == 0 and (checklist["status"] == "na").all():
        return "The package has no files."
    by_check = findings.groupby("check")["path"].nunique().to_dict()
    sev = findings.groupby(["check", "severity"])["path"].nunique().to_dict()
    phrases: list[str] = []
    if by_check.get("junk_files"):
        phrases.append(_n(by_check["junk_files"], "junk or temporary item"))
    non_pref = sev.get(("file_formats", "suggestion"), 0) + sev.get(("file_formats", "problem"), 0)
    if non_pref:
        phrases.append(_n(non_pref, "file") + " in a non-preferred format")
    if by_check.get("file_names"):
        phrases.append(_n(by_check["file_names"], "name") + " to fix or improve")
    if by_check.get("naming_convention"):
        phrases.append(_n(by_check["naming_convention"], "name") + " to make consistent")
    if not phrases:
        unlisted = sev.get(("file_formats", "info"), 0)
        text = f"No problems found in {_n(n_files, 'file')}."
        if unlisted:
            text += (
                f" {_n(unlisted, 'file')} in a format that is not on the list of preferred formats."
            )
        return text
    return f"Found {', '.join(phrases)} ({_n(n_files, 'file')} checked)."


_STATUS_WORDS = {
    "fail": "needs fixing",
    "warn": "could be improved",
    "manual": "check by hand",
    "pass": "ok",
    "na": "not applicable",
}

_SECTION = {
    "junk_files": (
        "Junk and temporary files",
        "These files and folders are made automatically by an operating system, an editor or a "
        "programming tool. They are not part of your research and add nothing to a data package.",
        "Delete these items from the package folder (turn on 'show hidden files' in your file "
        "manager, or use `ls -a` in a terminal, to see them), then create the archive again. "
        "Items marked as suggestions, such as a `.git` folder, can stay if you want to share "
        "them on purpose.",
    ),
    "file_formats": (
        "File formats",
        "Archives prefer open, well-documented formats that can be read without special "
        "software, now and in many years.",
        "Save or export these files in a preferred format, for example Word documents as "
        "ODT or PDF/A and spreadsheets as CSV or ODS, and keep the original too only if it is "
        "needed. A format that is not on the list is not necessarily a problem: check that "
        "others can open it without special software and, if they cannot, add a copy in an open "
        "format or a note on how to read it.",
    ),
    "file_names": (
        "File and folder names",
        "Spaces, accents and symbols in names cause trouble when files are moved between "
        "systems, read by code or put on the web, and very long paths cannot be opened on some "
        "systems.",
        "Rename these files and folders so that they use only letters (a-z, A-Z), digits, `_`, "
        "`-` and `.`, and keep the names and paths short. Update the code and documents that "
        "refer to the old names.",
    ),
    "naming_convention": (
        "Consistent names",
        "Names that follow one pattern are easier to find, sort and read, for people and for code.",
        "Use one way of separating words in names (for example `_`), write dates as YYYY-MM-DD "
        "and number files with leading zeros (01, 02, ... 10) so that they sort in the right "
        "order. These are suggestions: rename the files only if it is not much work, and update "
        "the code and documents that refer to the old names.",
    ),
}


def files_report(
    pkg: Any, findings: Any, checklist: Any, *, formats: PolicySource = None
) -> list[Any]:
    """The report blocks for :func:`check_files`: an overview, then a section per check with findings.

    Each section is a collapsible block with a table of the findings and a sentence on
    how to fix them. *formats* is the formats policy used, for the source of the list.
    """
    import pandas as pd

    from metacheck.report import collapse_section, scroll_table

    n_files = len(pkg.files())
    n_folders = int((pkg.dirs()["rel"] != "").sum())
    intro = (
        f"We checked {_n(n_files, 'file')} and {_n(n_folders, 'folder')} in the data package "
        f"**{pkg.name}**."
    )
    items = [
        f"- **{r['title']}**: {_STATUS_WORDS[r['status']]}. {r['detail']}"
        for r in checklist.to_dict("records")
    ]
    overview = f"{intro}\n\n" + "\n".join(items)
    if (findings["check"] == "junk_files").any():
        overview += "\n\nJunk and temporary files are left out of the other checks."
    report: list[Any] = [overview]

    source = load_formats(formats)["source"]
    for check, (title, intro, fix) in _SECTION.items():
        sub = findings[findings["check"] == check]
        if sub.empty:
            continue
        if check == "file_formats" and source:
            intro += f" {source}"
        table = pd.DataFrame(
            {
                "Path": [
                    p + "/" if k == "folder" else p
                    for p, k in zip(sub["path"].tolist(), sub["kind"].tolist(), strict=True)
                ],
                "What is wrong": sub["detail"].tolist(),
                "Severity": sub["severity"].tolist(),
            }
        )
        severities = set(sub["severity"])
        callout = (
            "warning"
            if "problem" in severities
            else "note"
            if "suggestion" in severities
            else "tip"
        )
        block = collapse_section(
            [intro, scroll_table(table, maxrows=10, escape=True), f"**How to fix:** {fix}"],
            title=f"{title} ({len(table)})",
            callout=callout,
            collapse=True,
        )
        report.extend(block) if isinstance(block, list) else report.append(block)
    return report
