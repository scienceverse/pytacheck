"""The documentation of a data package: its README and the parts it should have.

:func:`check_docs` is the library behind the ``datapackage::package_docs``
module. It takes an opened package (:class:`~metacheck.datapackage.OpenedPackage`)
and returns a :class:`DocsResult`: the shared **findings** and **checklist**
tables (see :mod:`metacheck.datapackage._findings`), plus what was read from
the README (its text, and which sections it has).

The policy lives in data, not in code, so an institution can bring its own:

* a **README template** (:func:`load_readme_template`): the sections a README
  should have, the fields in them and the template text that must not be left
  behind. The default is ``data/docs_readme_generic.json``.
* a **component catalogue** (:func:`load_components`): the parts a package
  should have (data, code, codebook, ethical approval, ...) and how to
  recognise them. The default is ``data/docs_components_generic.json``.
* a table of **licences** to recognise (``data/docs_licences.json``).
* **severity** overrides: a ``{rule id: severity}`` map (:data:`RULES` lists
  the rules and their defaults).

Every policy option accepts a dict (or list), a path to a JSON file, or JSON
text. Regular expressions in the policy are case-insensitive; use ``(?-i:...)``
inside one to switch that off.

README template (JSON)::

    {"name": "...", "source": "...",
     "sections": [
        {"id": "contact", "title": "Contact", "hint": "Authors or Contact",
         "match": ["\\bauthors?\\b", "\\bcontact"],      # regexes for the heading
         "required": true,
         "intro": false,                                 # the text at the top may stand in
         "prompt": "Give the names of the authors",      # what a drafted README asks for here
         "fill": "files",                                # what a drafted README fills it with
         "fields": [{"id": "email", "label": "E-mail", "match": ["e-?mail"],
                     "required": false, "expect": "email"}]}],
     "prompts": {"title": "Give the title", "folder": "Describe this folder"},
     "placeholders": ["<[^>]+>", "\\bTODO\\b"]}          # template text left behind

``prompt``, ``fill`` and ``prompts`` are only read by the README draft
(:mod:`metacheck.datapackage.readme`, the ``package_readme`` module); the checks
ignore them. ``fill`` is ``files``, ``variables`` or ``licence`` (the section's
id when absent): the part of the package that fills the section; other sections
get a placeholder that asks for ``prompt``.

Component catalogue (JSON)::

    {"name": "...", "source": "...",
     "components": [
        {"id": "ethics", "title": "Ethical approval", "group": "ethics",
         "description": "...",
         "match": {"names": [...], "paths": [...], "folders": [...], "exts": [...],
                   "data_type": [...], "doc_role": [...], "readme_sections": [...]},
         "required": "always" | "recommended" | "optional" | "human_participants",
         "formats": ["pdf"],        # other formats of a matched file get a suggestion
         "file_types": ["text", "image"],  # files of other known types never count
         "when": ["data"]}]}        # only applies when these components are present

A file belongs to a component when any one of the ``match`` criteria hits and
its type (:func:`metacheck.fileinfo.category.filetype`: ``text``, ``image``,
``data``, ``code``, ...) is not ruled out by ``file_types``.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from metacheck.datapackage._docs_policy import (
    RULES,
    ComponentCatalogue,
    ComponentSpec,
    FieldSpec,
    ReadmeTemplate,
    SectionSpec,
    _load_licences,
    _load_severity,
    load_components,
    load_readme_template,
)
from metacheck.datapackage._docs_readme import (
    FIELD_COLUMNS,
    PLAIN_TEXT_EXTS,
    SECTION_COLUMNS,
    Heading,
    _emails,
    _licences_named,
    find_headings,
    find_placeholders,
    match_sections,
    read_readme_text,
)
from metacheck.datapackage._findings import (
    checklist_frame,
    findings_frame,
    status_from_findings,
    traffic_light,
)

__all__ = [
    "COMPONENT_COLUMNS",
    "FIELD_COLUMNS",
    "RULES",
    "SECTION_COLUMNS",
    "ComponentCatalogue",
    "ComponentSpec",
    "DocsResult",
    "FieldSpec",
    "Heading",
    "ReadmeTemplate",
    "SectionSpec",
    "check_docs",
    "find_headings",
    "find_placeholders",
    "find_readmes",
    "load_components",
    "load_readme_template",
    "match_sections",
    "read_readme_text",
    "report_blocks",
    "resolve_human_participants",
    "status_counts",
    "summary_line",
]

#: Columns of the ``components`` table (:attr:`DocsResult.components`).
COMPONENT_COLUMNS = (
    "id",
    "title",
    "group",
    "required",  # always, recommended, optional or human_participants
    "status",  # pass, fail, warn, manual or na
    "files",  # the matched files, "; " separated
    "n_files",
    "detail",
)

_FORMAT_NAMES = {
    "pdf": "a PDF",
    "docx": "a Word document",
    "doc": "a Word document",
    "odt": "an OpenDocument text file",
    "rtf": "an RTF file",
    "html": "a web page",
    "htm": "a web page",
    "pages": "a Pages document",
}

#: Most files from a folder of data that the README is expected to name one by one.
_MAX_DATA_FILES = 50


def _ext(path: str) -> str:
    """The extension of the file at *path*, in lower case ("" for none)."""
    name = path.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[-1].lower() if "." in name.lstrip(".") else ""


def _type_allowed(spec: ComponentSpec, file_type: str) -> bool:
    """Whether a file of *file_type* may count for *spec* (unknown types always may)."""
    return not spec.file_types or file_type in ("NA", "") or file_type in spec.file_types


# documents: a part that names one (a DMP, a consent form) takes it from "data"
_DOCUMENT_EXTS = frozenset({"pdf", "doc", "docx", "odt", "rtf", "txt", "md", "html", "htm"})
#: Findings listed one by one before the rest are summed up.
_MAX_LISTED = 10


# --------------------------------------------------------------------------
# looking at the package
# --------------------------------------------------------------------------

_README_NAME = re.compile(r"^(?:read[ _-]?me|lees[ _-]?mij)", re.IGNORECASE)
_LICENCE_NAME = re.compile(r"^(?:licen[cs]es?|copying|unlicen[cs]e)(?![a-z])", re.IGNORECASE)
_JUNK_NAMES = frozenset({"__macosx", ".ds_store", "thumbs.db", "desktop.ini"})
_JUNK_ENDINGS = (".tmp", ".bak", ".swp", ".part", ".crdownload")


def _is_junk(name: str) -> bool:
    """Operating-system and editor leftovers (``._README.txt``, ``~$README.docx``, ``notes.txt~``)."""
    low = name.lower()
    return (
        name.startswith(("._", "~$", ".~lock", ".#"))
        or name.endswith("~")
        or low in _JUNK_NAMES
        or low.endswith(_JUNK_ENDINGS)
    )


def _candidates(table: Any) -> Any:
    """The rows of a files or folders table that are in the base and not hidden or junk."""
    if len(table) == 0:
        return table
    keep = [
        bool(in_base) and not bool(hidden) and not _is_junk(str(name))
        for in_base, hidden, name in zip(
            table["in_base"], table["hidden"], table["name"], strict=True
        )
    ]
    return table.loc[keep]


def _format_rank(ext: str) -> int:
    return {"md": 0, "markdown": 0, "txt": 1}.get(ext, 2 if ext in PLAIN_TEXT_EXTS else 3)


def find_readmes(package_or_files: Any) -> Any:
    """The README files of a package, best first (rows of the package's file listing).

    A README is a file whose ``doc_role`` is readme or whose name starts with
    ``read me``, ``readme`` or ``leesmij``; hidden and junk files are left out.
    The ones nearest the top come first, then Markdown, then plain text, then
    the rest (``README.docx``, ``README.pdf``).
    """
    files = package_or_files.files() if hasattr(package_or_files, "files") else package_or_files
    files = _candidates(files)
    if len(files) == 0:
        return files
    mask = [
        (str(role) == "readme" or bool(_README_NAME.match(str(name))))
        and str(name).lower() != "ro-crate-metadata.json"
        for role, name in zip(files["doc_role"], files["name"], strict=True)
    ]
    found = files.loc[mask]
    order = sorted(
        range(len(found)),
        key=lambda k: (
            int(found["depth"].iloc[k]),
            _format_rank(str(found["ext"].iloc[k])),
            str(found["name"].iloc[k]).casefold(),
        ),
    )
    return found.iloc[order]


def _licence_files(files: Any) -> Any:
    """The licence files of the package (``LICENSE``, ``LICENCE.txt``, ``COPYING``), top first."""
    cand = _candidates(files)
    if len(cand) == 0:
        return cand
    mask = [
        str(role) == "license" or bool(_LICENCE_NAME.match(str(name)))
        for role, name in zip(cand["doc_role"], cand["name"], strict=True)
    ]
    found = cand.loc[mask]
    order = sorted(
        range(len(found)),
        key=lambda k: (int(found["depth"].iloc[k]), str(found["rel"].iloc[k])),
    )
    return found.iloc[order]


def _normalise(text: str) -> str:
    return re.sub(r"[_\-\s]+", " ", text.lower())


def _mentioned(readme: str, names: Iterable[str]) -> bool:
    """Whether the (normalised) README text names any of *names*: ``_``, ``-`` and space are alike."""
    for name in names:
        norm = _normalise(name).strip()
        if norm and re.search(rf"(?<!\w){re.escape(norm)}(?!\w)", readme):
            return True
    return False


# --------------------------------------------------------------------------
# did the research involve people?
# --------------------------------------------------------------------------

_TRUE = frozenset({"true", "yes", "y", "1"})
_FALSE = frozenset({"false", "no", "n", "0"})


def resolve_human_participants(value: Any = None, paper: Any = None) -> tuple[bool | None, str]:
    """Whether the research involved people: ``(answer, where it comes from)``.

    A given *value* (``True``/``False``, or text such as ``"yes"``/``"no"``)
    wins, with the source ``"given"``. Otherwise the paper, when it has text,
    is searched for sentences about recruiting participants, informed consent
    and the like (the live-data detection that ``ethics_check`` uses): a hit
    gives ``True`` with the source ``"paper"``. Anything else, including any
    problem with the paper, gives ``None`` (source ``"unknown"``): a person
    has to decide.
    """
    if isinstance(value, str):
        low = value.strip().lower()
        value = True if low in _TRUE else False if low in _FALSE else None
    if value is not None:
        return bool(value), "given"
    if paper is None:
        return None, "unknown"
    import contextlib

    # a paper that cannot be searched leaves the question open
    with contextlib.suppress(Exception):
        from metacheck.text.extract import _detect_live_data

        if len(_detect_live_data(paper)) > 0:
            return True, "paper"
    return None, "unknown"


# --------------------------------------------------------------------------
# the result
# --------------------------------------------------------------------------


@dataclass
class DocsResult:
    """What :func:`check_docs` found.

    ``findings`` and ``checklist`` are the shared tables. ``readme_path`` is
    the README that was checked, relative to the package's base (``None`` when
    there is none), and ``readme_text`` its text (``None`` when there is no
    README or it could not be read). ``sections`` and ``fields`` say what the
    README has (see :func:`match_sections`); ``components`` says which parts of
    the package were found (:data:`COMPONENT_COLUMNS`).
    """

    findings: Any
    checklist: Any
    readme_path: str | None = None
    readme_text: str | None = None
    sections: Any = None
    fields: Any = None
    components: Any = None
    human_participants: bool | None = None

    @property
    def traffic_light(self) -> str:
        """``red``, ``yellow``, ``green`` or ``na`` (see :func:`~metacheck.datapackage.traffic_light`)."""
        return str(traffic_light(self.checklist))


def _listing(items: Sequence[str], limit: int = 5) -> str:
    shown = ", ".join(items[:limit])
    return shown + (f" and {len(items) - limit} more" if len(items) > limit else "")


def _n(count: int, one: str, many: str | None = None) -> str:
    return f"{count} {one if count == 1 else (many or one + 's')}"


_EXPECTED = {"email": "e-mail address", "url": "web address", "doi": "DOI"}


def _kind_of(ext: str) -> str:
    return _FORMAT_NAMES.get(ext, f"a .{ext} file" if ext else "a file without an extension")


def check_docs(
    package: Any,
    *,
    readme: Any = None,
    components: Any = None,
    human_participants: bool | None = None,
    min_file_list_coverage: float = 0.8,
    severity: Any = None,
    licences: Any = None,
) -> DocsResult:
    """Check the README of a data package and whether it has the parts it should have.

    *package* is an :class:`~metacheck.datapackage.OpenedPackage`. *readme* is
    the README template and *components* the component catalogue (each a dict,
    a path to a JSON file, or ``None`` for the generic default; see the module
    docstring). *human_participants* says whether the research involved people:
    ``True`` or ``False``, or ``None`` when that is not known, which leaves the
    components that depend on it to a person (see
    :func:`resolve_human_participants` for deciding it from a paper).
    *min_file_list_coverage* is the share of the package's top-level folders
    and files (and its data files, up to 50) that the README has to mention.
    *severity* maps rule ids (:data:`RULES`) to ``"problem"``, ``"suggestion"``
    or ``"info"``. *licences* is the table of licences to recognise.
    """
    run = _Run(
        package,
        load_readme_template(readme),
        load_components(components),
        human_participants,
        float(min_file_list_coverage),
        {**RULES, **_load_severity(severity)},
        _load_licences(licences),
    )
    return run.run()


class _Run:
    """One run of the checks: collects the findings and the checklist."""

    def __init__(
        self,
        package: Any,
        template: ReadmeTemplate,
        catalogue: ComponentCatalogue,
        human_participants: bool | None,
        min_coverage: float,
        severity: Mapping[str, str],
        licences: Any,
    ) -> None:
        self.package = package
        self.template = template
        self.catalogue = catalogue
        self.human_participants = human_participants
        self.min_coverage = min_coverage
        self.severity = severity
        self.licences = licences
        self.rows: list[dict[str, str]] = []
        self.items: list[dict[str, str]] = []
        self.readme_rel: str | None = None
        self.text: str | None = None
        self.sections: Any = None
        self.fields: Any = None

    # -- bookkeeping -------------------------------------------------------

    def add(
        self, check: str, rule: str, detail: str, path: str = "", kind: str = "package"
    ) -> None:
        self.rows.append(
            {
                "path": path,
                "kind": kind,
                "check": check,
                "rule": rule,
                "severity": self.severity[rule],
                "detail": detail,
            }
        )

    def status(self, check: str) -> str:
        """The status the findings of *check* make it: a problem fails, a suggestion warns."""
        return str(status_from_findings(findings_frame(self.rows), check))

    def item(self, item: str, title: str, status: str, detail: str) -> None:
        self.items.append({"item": item, "title": title, "status": status, "detail": detail})

    def gated(self, check: str, title: str) -> bool:
        """Add the item and say so when there is no README text to check."""
        if self.readme_rel is None:
            self.item(check, title, "na", "There is no README to check.")
            return True
        if self.text is None:
            self.item(
                check,
                title,
                "manual",
                f"The text of {self.readme_rel} could not be read, so check this by hand.",
            )
            return True
        return False

    def _read(self, row: Any) -> str | None:
        """The README's text. A link is only followed when it stays inside the package."""
        path = self.package.root / str(row["path"])
        if bool(row["link"]):
            try:
                if not path.resolve().is_relative_to(self.package.root.resolve()):
                    return None
            except OSError:
                return None
        return read_readme_text(path)

    # -- the run -----------------------------------------------------------

    def run(self) -> DocsResult:
        files = self.package.files()
        readmes = find_readmes(files)
        if len(readmes):
            first = readmes.iloc[0]
            self.readme_rel = str(first["rel"])
            self.text = self._read(first)
        self.sections, self.fields = match_sections(self.text or "", self.template)

        self.check_present(readmes)
        self.check_format(readmes.iloc[0] if len(readmes) else None)
        self.check_sections()
        self.check_placeholders()
        self.check_contact()
        self.check_file_list(files, readmes)
        self.check_licence(files)
        components = self.check_components(files)
        return DocsResult(
            findings=findings_frame(self.rows),
            checklist=checklist_frame(self.items),
            readme_path=self.readme_rel,
            readme_text=self.text,
            sections=self.sections,
            fields=self.fields,
            components=components,
            human_participants=self.human_participants,
        )

    # -- readme_present, readme_format -------------------------------------

    def check_present(self, readmes: Any) -> None:
        check, title = "readme_present", "README at the top of the package"
        if len(readmes) == 0:
            self.add(
                check,
                "readme-missing",
                "The package has no README. Add a README (README.txt or README.md) at the top that "
                "explains what the package contains and how to use it.",
            )
            self.item(check, title, self.status(check), "No README found.")
            return
        top = readmes.loc[readmes["depth"] == 0]
        first = readmes.iloc[0]
        rel = str(first["rel"])
        if len(top) == 0:
            folder = rel.rsplit("/", 1)[0]
            self.add(
                check,
                "readme-in-subfolder",
                f"The README is in {folder}/, not at the top. Move it to the top of the package, "
                "so it is the first thing a reader sees.",
                rel,
                "file",
            )
            self.item(
                check,
                title,
                self.status(check),
                f"The README ({rel}) is in {folder}/, not at the top.",
            )
            return
        if len(top) > 1:
            names = [str(v) for v in top["name"]]
            self.add(
                check,
                "readme-several",
                f"There are {len(top)} READMEs at the top ({_listing(names)}). Keep one, so readers "
                f"do not wonder which is current. {first['name']} is the one checked here.",
                rel,
                "file",
            )
        if self.text is not None and not self.text.strip():
            self.add(
                check,
                "readme-empty",
                f"{rel} is empty. Write what the package contains and how to use it.",
                rel,
                "file",
            )
        detail = f"Found {first['name']}" + (
            f" (and {_n(len(top) - 1, 'more README')} at the top)." if len(top) > 1 else "."
        )
        if self.text is not None and not self.text.strip():
            detail = f"{first['name']} is there but empty."
        self.item(check, title, self.status(check), detail)

    def check_format(self, row: Any) -> None:
        check, title = "readme_format", "README is plain text (TXT or Markdown)"
        if row is None:
            self.item(check, title, "na", "There is no README to check.")
            return
        rel, ext = str(row["rel"]), str(row["ext"])
        plain = ext in PLAIN_TEXT_EXTS
        if not plain:
            self.add(
                check,
                "readme-format",
                f"{rel} is {_kind_of(ext)}. Save the README as plain text (TXT) or Markdown (MD): it "
                "then opens everywhere and stays readable when the package is archived.",
                rel,
                "file",
            )
        if self.text is None:
            self.add(
                check,
                "readme-unreadable",
                f"The text of {rel} could not be read here, so the checks of its content are left to you.",
                rel,
                "file",
            )
        self.item(
            check,
            title,
            self.status(check),
            f"{rel} is plain text." if plain else f"{rel} is {_kind_of(ext)}.",
        )

    # -- readme_sections ----------------------------------------------------

    def check_sections(self) -> None:
        check, title = "readme_sections", "README has the expected sections"
        if self.gated(check, title):
            return
        rel = self.readme_rel or ""
        specs = {s.id: s for s in self.template.sections}
        missing_req: list[str] = []
        missing_opt: list[str] = []
        ok_req: list[str] = []
        for _, row in self.sections.iterrows():
            spec = specs[str(row["id"])]
            label = spec.title
            hint = f" (a heading such as {spec.hint})" if spec.hint else ""
            required = bool(row["required"])
            if not row["found"]:
                (missing_req if required else missing_opt).append(label)
                if required:
                    self.add(
                        check,
                        "section-missing",
                        f"The README has no “{label}” section{hint}. Add one.",
                        rel,
                        "file",
                    )
                else:
                    self.add(
                        check,
                        "section-optional-missing",
                        f"The README has no “{label}” section{hint}. It is optional.",
                        rel,
                        "file",
                    )
            elif row["empty"]:
                (missing_req if required else missing_opt).append(label)
                where = f"line {int(row['line'])}"
                if required:
                    self.add(
                        check,
                        "section-empty",
                        f"The “{label}” section ({where}) is empty or only holds template text. Fill it in.",
                        rel,
                        "file",
                    )
                else:
                    self.add(
                        check,
                        "section-optional-empty",
                        f"The optional “{label}” section ({where}) is empty or only holds template text.",
                        rel,
                        "file",
                    )
            elif required:
                ok_req.append(label)
        for _, row in self.fields.iterrows():
            if (
                row["section"] not in specs
                or not self.sections.set_index("id").loc[row["section"], "found"]
            ):
                continue
            section_title = specs[str(row["section"])].title
            if row["required"] and not row["found"]:
                self.add(
                    check,
                    "field-missing",
                    f"The “{section_title}” section has no “{row['label']}” line. Add it.",
                    rel,
                    "file",
                )
            elif row["found"] and not row["ok"]:
                field = next(f for f in specs[str(row["section"])].fields if f.id == row["id"])
                wanted = _EXPECTED.get(field.expect or "", "anything")
                self.add(
                    check,
                    "field-invalid",
                    f"The “{row['label']}” line in the “{section_title}” section "
                    f"(line {int(row['line'])}) has no {wanted} in it.",
                    rel,
                    "file",
                )
        n_req = int(self.sections["required"].sum())
        detail = f"{len(ok_req)} of {n_req} required sections are there"
        if ok_req:
            detail += f" ({', '.join(ok_req)})"
        if missing_req:
            detail += f"; missing or empty: {', '.join(missing_req)}"
        detail += "."
        if missing_opt:
            detail += f" Optional sections not filled in: {', '.join(missing_opt)}."
        self.item(check, title, self.status(check), detail)

    # -- readme_placeholders, readme_contact ---------------------------------

    def check_placeholders(self) -> None:
        check, title = "readme_placeholders", "No template text left in the README"
        if self.gated(check, title):
            return
        rel = self.readme_rel or ""
        hits = find_placeholders(self.text or "", self.template)
        for number, value in hits[:_MAX_LISTED]:
            self.add(
                check,
                "template-text",
                f"Line {number} still has template text: {value}. Replace it with your own text or delete it.",
                rel,
                "file",
            )
        if len(hits) > _MAX_LISTED:
            self.add(
                check,
                "template-text",
                f"{len(hits) - _MAX_LISTED} more places in the README still have template text.",
                rel,
                "file",
            )
        if not hits:
            self.item(check, title, "pass", "No template text found.")
            return
        examples = "; ".join(f"line {n}: {v}" for n, v in hits[:3])
        self.item(
            check,
            title,
            self.status(check),
            f"{_n(len(hits), 'place')} with template text ({examples}{'; ...' if len(hits) > 3 else ''}).",
        )

    def check_contact(self) -> None:
        check, title = "readme_contact", "README gives a contact e-mail"
        if self.gated(check, title):
            return
        emails = _emails(self.text or "")
        if not emails:
            self.add(
                check,
                "contact-missing",
                "The README has no e-mail address. Give one that stays valid, so people can reach "
                "the person who knows the data.",
                self.readme_rel or "",
                "file",
            )
        self.item(
            check,
            title,
            self.status(check),
            f"E-mail: {_listing(emails, 3)}." if emails else "No e-mail address found.",
        )

    # -- readme_file_list --------------------------------------------------

    def _entries(self, files: Any, readmes: Any) -> list[tuple[str, str, list[str]]]:
        """What the README should describe: ``(path, kind, names to look for)``."""
        cand = _candidates(files)
        readme_names = {str(v) for v in readmes["name"]} if len(readmes) else set()
        licence_names = {str(v) for v in _licence_files(files)["name"]} if len(cand) else set()

        def names_of(name: str, is_file: bool) -> list[str]:
            stem = name.rsplit(".", 1)[0] if is_file and "." in name.lstrip(".") else name
            return [name] if stem == name else [name, stem]

        out: list[tuple[str, str, list[str]]] = [
            (str(d.rel), "folder", names_of(str(d.name), False))
            for d in _candidates(self.package.dirs()).itertuples()
            if int(d.depth) == 1
        ]
        data: list[tuple[int, str, str]] = []
        for f in cand.itertuples():
            name = str(f.name)
            if int(f.depth) == 0:
                if name not in readme_names and name not in licence_names:
                    out.append((str(f.rel), "file", names_of(name, True)))
            elif str(f.data_type) == "data":
                data.append((int(f.depth), str(f.rel), name))
        data.sort(key=lambda t: (t[0], t[1].casefold()))
        for _, rel, name in data[:_MAX_DATA_FILES]:
            out.append((rel, "file", names_of(name, True)))
        return out

    def check_file_list(self, files: Any, readmes: Any) -> None:
        check, title = "readme_file_list", "README describes the files"
        if self.gated(check, title):
            return
        rel = self.readme_rel or ""
        entries = self._entries(files, readmes)
        if not entries:
            self.item(
                check,
                title,
                "na",
                "The package holds nothing besides the README (and a licence) to describe.",
            )
            return
        readme = _normalise(self.text or "")
        missing = [(p, k) for p, k, names in entries if not _mentioned(readme, names)]
        coverage = 1 - len(missing) / len(entries)
        names_missing = [p for p, _ in missing]
        if coverage < self.min_coverage:
            self.add(
                check,
                "file-list-incomplete",
                f"The README names {len(entries) - len(missing)} of the {len(entries)} top-level folders "
                f"and files (and data files) of the package; at least {self.min_coverage:.0%} is expected. "
                f"Not named: {_listing(names_missing, 8)}. Say what each of them is.",
                rel,
                "file",
            )
        for path, kind in missing[:_MAX_DATA_FILES]:
            self.add(
                check, "file-not-described", f"The README does not mention {path}.", path, kind
            )
        detail = f"The README names {len(entries) - len(missing)} of {len(entries)} folders and files ({coverage:.0%})"
        detail += f"; not named: {_listing(names_missing, 5)}." if missing else "."
        self.item(check, title, self.status(check), detail)

    # -- licence -----------------------------------------------------------

    def check_licence(self, files: Any) -> None:
        check, title = "licence", "Licence for reuse"
        found = _licence_files(files)
        named: list[str] = []
        if self.text:
            spec = self.template.section("licence")
            if spec is not None:
                row = self.sections.set_index("id").loc["licence"]
                if row["found"] and not row["empty"]:
                    lines = self.text.splitlines()[int(row["line"]) - 1 : int(row["end_line"])]
                    named += _licences_named("\n".join(lines), self.licences, context_needed=False)
            named += [
                n
                for n in _licences_named(self.text, self.licences, context_needed=True)
                if n not in named
            ]
        if len(found):
            first = str(found.iloc[0]["rel"])
            self.item(
                check,
                title,
                "pass",
                f"Licence file: {first}"
                + (f"; the README names {', '.join(named)}." if named else "."),
            )
        elif named:
            self.add(
                check,
                "licence-only-in-readme",
                f"The README names the licence ({', '.join(named)}) but the package has no LICENSE file. "
                "Add one with the licence text, so the terms travel with the files.",
                self.readme_rel or "",
                "file",
            )
            self.item(
                check,
                title,
                self.status(check),
                f"The README names {', '.join(named)}; there is no LICENSE file.",
            )
        elif self.readme_rel is not None and self.text is None:
            self.item(
                check,
                title,
                "manual",
                f"There is no LICENSE file and the text of {self.readme_rel} could not be read, so check by hand whether it names a licence.",
            )
        else:
            self.add(
                check,
                "licence-missing",
                "The package has no licence. Add a LICENSE file, or state the licence in the README (for "
                "example CC BY 4.0 for data and MIT for code), so others know how they may reuse it.",
            )
            self.item(
                check,
                title,
                self.status(check),
                "No LICENSE file, and the README names no licence.",
            )

    # -- components --------------------------------------------------------

    def check_components(self, files: Any) -> Any:
        import pandas as pd

        from metacheck.fileinfo.category import filetype

        cand = _candidates(files)
        rels = [str(v) for v in cand["rel"]] if len(cand) else []
        names = [str(v) for v in cand["name"]] if len(cand) else []
        exts = [str(v) for v in cand["ext"]] if len(cand) else []
        types = [str(v) for v in cand["data_type"]] if len(cand) else []
        roles = [str(v) for v in cand["doc_role"]] if len(cand) else []
        folder_hits: dict[tuple[int, str], bool] = {}

        def in_folder(spec_index: int, spec: ComponentSpec, rel: str) -> bool:
            parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
            key = (spec_index, parent)
            if key not in folder_hits:
                folder_hits[key] = any(
                    p.search(part) for part in parent.split("/") if part for p in spec.folders
                )
            return folder_hits[key]

        sec_found: dict[str, tuple[str, int]] = {}
        for _, row in self.sections.iterrows():
            if row["found"] and not row["empty"]:
                sec_found[str(row["id"])] = (str(row["title"]), int(row["line"]))

        # A file's data type is a rough guess ("Data Management Plan.pdf" is "data"),
        # so a part that matches by data type does not take the documents another
        # part names more precisely.
        ext_of = dict(zip(rels, exts, strict=True))
        ftypes = [str(t) for t in filetype(names)] if names else []
        named: dict[str, set[str]] = {}
        for index, spec in enumerate(self.catalogue.components):
            named[spec.id] = {
                rel
                for k, rel in enumerate(rels)
                if _type_allowed(spec, ftypes[k])
                and (
                    exts[k] in spec.exts
                    or roles[k] in spec.doc_roles
                    or any(p.search(names[k]) for p in spec.names)
                    or any(p.search(rel) for p in spec.paths)
                    or (spec.folders and in_folder(index, spec, rel))
                )
            }
        matched: dict[str, list[str]] = {}
        via_readme: dict[str, str] = {}
        for spec in self.catalogue.components:
            taken = {
                rel
                for c, v in named.items()
                if c != spec.id
                for rel in v
                if ext_of[rel] in _DOCUMENT_EXTS
            }
            matched[spec.id] = [
                rel
                for k, rel in enumerate(rels)
                if rel in named[spec.id]
                or (
                    types[k] in spec.data_types
                    and rel not in taken
                    and _type_allowed(spec, ftypes[k])
                )
            ]
            for sid in spec.readme_sections:
                if sid in sec_found and self.readme_rel:
                    title_, line = sec_found[sid]
                    via_readme[spec.id] = f"{self.readme_rel} (section “{title_}”, line {line})"
                    break
        present = {c.id for c in self.catalogue.components if matched[c.id] or c.id in via_readme}

        rows: list[dict[str, Any]] = []
        for spec in self.catalogue.components:
            check = f"component:{spec.id}"
            hits = matched[spec.id]
            shown = hits + ([via_readme[spec.id]] if spec.id in via_readme else [])
            status, detail = self._component_status(spec, check, hits, shown, present)
            rows.append(
                {
                    "id": spec.id,
                    "title": spec.title,
                    "group": spec.group,
                    "required": spec.required,
                    "status": status,
                    "files": "; ".join(shown),
                    "n_files": len(shown),
                    "detail": detail,
                }
            )
        table = pd.DataFrame(
            {
                "id": pd.Series([r["id"] for r in rows], dtype="string"),
                "title": pd.Series([r["title"] for r in rows], dtype="string"),
                "group": pd.Series([r["group"] for r in rows], dtype="string"),
                "required": pd.Series([r["required"] for r in rows], dtype="string"),
                "status": pd.Series([r["status"] for r in rows], dtype="string"),
                "files": pd.Series([r["files"] for r in rows], dtype="string"),
                "n_files": pd.Series([r["n_files"] for r in rows], dtype="Int64"),
                "detail": pd.Series([r["detail"] for r in rows], dtype="string"),
            }
        )
        self._components_item(rows)
        for r in rows:
            self.item(f"component:{r['id']}", r["title"], r["status"], r["detail"])
        return table

    def _missing(self, check: str, rule: str, detail: str) -> str:
        """Add a finding about a missing part; the status follows its severity."""
        self.add(check, rule, detail)
        return {"problem": "fail", "suggestion": "warn"}.get(self.severity[rule], "na")

    def _component_status(
        self, spec: ComponentSpec, check: str, hits: list[str], shown: list[str], present: set[str]
    ) -> tuple[str, str]:
        if shown:
            wrong = [h for h in hits if spec.formats and _ext(h) not in spec.formats]
            for path in wrong:
                ext = _ext(path)
                wanted = " or ".join(f.upper() for f in spec.formats)
                self.add(
                    check,
                    "component-format",
                    f"{spec.title} should be a {wanted} file, but {path} is {_kind_of(ext)}.",
                    path,
                    "file",
                )
            detail = f"Found: {_listing(shown, 4)}."
            if wrong:
                wanted = " or ".join(f.upper() for f in spec.formats)
                detail += f" Should be {wanted}: {_listing(wrong, 3)}."
            return self.status(check), detail
        unmet = [i for i in spec.when if i not in present]
        if unmet:
            wanted = ", ".join(self._title_of(i).lower() for i in unmet)
            return "na", f"Not needed: the package has no {wanted}."
        if self.text is None and self.readme_rel is not None and spec.readme_sections:
            return (
                "manual",
                f"The text of {self.readme_rel} could not be read, so whether it covers this is left to you.",
            )
        needed = spec.required
        if needed == "human_participants":
            if self.human_participants is None:
                self.add(
                    check,
                    "component-undecided",
                    f"{spec.title} is needed if the research involved people. Check by hand.",
                )
                return "manual", "Needed if the research involved people. Check by hand."
            if not self.human_participants:
                return "na", "Not needed: the research did not involve people."
            return (
                self._missing(
                    check,
                    "component-missing",
                    f"{spec.title} is missing from the package. The research involved people. "
                    f"{spec.description}".strip(),
                ),
                "Missing. The research involved people, so it should be included.",
            )
        if needed == "always":
            return (
                self._missing(
                    check,
                    "component-missing",
                    f"{spec.title} is missing from the package. {spec.description}".strip(),
                ),
                "Missing.",
            )
        if needed == "recommended":
            return (
                self._missing(
                    check,
                    "component-recommended-missing",
                    f"The package has no {spec.title.lower()}. {spec.description}".strip(),
                ),
                "Not found; recommended.",
            )
        return "na", "Not found; optional."

    def _title_of(self, component_id: str) -> str:
        return next(
            (c.title for c in self.catalogue.components if c.id == component_id), component_id
        )

    def _components_item(self, rows: list[dict[str, Any]]) -> None:
        check, title = "components", "Package has the parts it should have"
        by_status: dict[str, list[str]] = {}
        for r in rows:
            by_status.setdefault(r["status"], []).append(r["title"])
        applicable = sum(len(v) for k, v in by_status.items() if k != "na")
        if applicable == 0:
            self.item(check, title, "na", "No part applies.")
            return
        status = next((s for s in ("fail", "warn", "manual") if by_status.get(s)), "pass")
        bits = [f"{len(by_status.get('pass', []))} of {applicable} parts are in order"]
        for key, label in (
            ("fail", "to fix"),
            ("warn", "to improve"),
            ("manual", "to decide by hand"),
        ):
            if by_status.get(key):
                bits.append(f"{label}: {', '.join(by_status[key])}")
        self.item(check, title, status, "; ".join(bits) + ".")


# --------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------

_STATUS_LABEL = {
    "pass": "pass",
    "warn": "to improve",
    "fail": "to fix",
    "manual": "decide by hand",
    "na": "n/a",
}


def status_counts(result: DocsResult) -> dict[str, int]:
    """How many checklist items have each status.

    The ``components`` item sums up the ``component:<id>`` items that follow it,
    so it is left out here.
    """
    counts = dict.fromkeys(("fail", "warn", "manual", "pass", "na"), 0)
    for item, status in zip(result.checklist["item"], result.checklist["status"], strict=True):
        if item != "components":
            counts[str(status)] += 1
    return counts


def summary_line(result: DocsResult) -> str:
    """One sentence for the module's summary: the README and how the checklist came out."""
    counts = status_counts(result)
    parts = [
        f"{counts[s]} {label}"
        for s, label in (
            ("fail", "to fix"),
            ("warn", "to improve"),
            ("manual", "to decide by hand"),
            ("pass", "fine"),
        )
        if counts[s]
    ]
    readme = f"README: {result.readme_path}." if result.readme_path else "No README."
    return f"{readme} Checklist: {', '.join(parts) if parts else 'nothing to check'}."


def _esc(text: Any) -> str:
    """Text for the report: ``<provide a DOI>`` from a README must not be read as a tag."""
    return html.escape(str(text), quote=False)


def report_blocks(result: DocsResult) -> list[Any]:
    """The module's report as blocks: markdown text and tables (see :mod:`metacheck.report`)."""
    import pandas as pd

    from metacheck.report import collapse_section, emojis, scroll_table

    symbol = {
        "pass": emojis["check"],
        "warn": emojis["warning"],
        "fail": emojis["x"],
        "manual": emojis["question"],
        "na": "–",
    }
    # the parts of the package have a table of their own below
    overview = result.checklist.loc[~result.checklist["item"].str.startswith("component:")]
    check_table = pd.DataFrame(
        {
            "Check": overview["title"],
            "Result": [f"{symbol[s]} {_STATUS_LABEL[s]}" for s in overview["status"]],
            "Details": [_esc(d) for d in overview["detail"]],
        }
    )
    blocks: list[Any] = [
        "#### Checklist",
        scroll_table(check_table, colwidths=[30, 12, 58], maxrows=20),
    ]

    titles = dict(zip(result.checklist["item"], result.checklist["title"], strict=True))
    findings = result.findings
    for severity, heading in (
        ("problem", "To fix before the package is archived"),
        ("suggestion", "Would make the package better"),
    ):
        sub = findings.loc[findings["severity"] == severity] if len(findings) else findings
        if not len(sub):
            continue
        lines = [f"#### {heading}"]
        for check in dict.fromkeys(sub["check"]):
            title = titles.get(str(check), str(check))
            lines.append(f"**{title}**\n")
            lines.extend(f"- {_esc(d)}" for d in sub.loc[sub["check"] == check, "detail"])
            lines.append("")
        blocks.append("\n".join(lines).rstrip())

    info = findings.loc[findings["severity"] == "info"] if len(findings) else findings
    if len(info):
        notes = pd.DataFrame({"Note": [_esc(d) for d in info["detail"]]})
        blocks.extend(
            _flat(collapse_section(scroll_table(notes, maxrows=10), title="Notes", callout="note"))
        )

    if result.sections is not None and len(result.sections):
        sec = result.sections
        shown = pd.DataFrame(
            {
                "Section": sec["title"],
                "Needed": ["yes" if r else "optional" for r in sec["required"]],
                "In the README": [
                    "no" if not f else "empty" if e else "yes"
                    for f, e in zip(sec["found"], sec["empty"], strict=True)
                ],
                "Heading as written": [_esc(h) for h in sec["heading"]],
                "Line": [("" if pd.isna(v) else str(int(v))) for v in sec["line"]],
            }
        )
        if result.readme_text is not None:
            blocks.extend(
                _flat(
                    collapse_section(
                        scroll_table(shown, maxrows=10), title="README sections", callout="note"
                    )
                )
            )

    if result.components is not None and len(result.components):
        comp = result.components
        shown = pd.DataFrame(
            {
                "Part": comp["title"],
                "Result": [f"{symbol[s]} {_STATUS_LABEL[s]}" for s in comp["status"]],
                "Details": [_esc(d) for d in comp["detail"]],
            }
        )
        blocks.extend(
            _flat(
                collapse_section(
                    scroll_table(shown, maxrows=10), title="Parts of the package", callout="note"
                )
            )
        )
    return blocks


def _flat(block: Any) -> list[Any]:
    return list(block) if isinstance(block, list) else [block]
