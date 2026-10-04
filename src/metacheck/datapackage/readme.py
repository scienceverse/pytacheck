"""Draft a README for a data package, filled in by rules.

:func:`draft_readme` is the library behind the ``datapackage::package_readme`` module. It takes an
opened package and returns a :class:`ReadmeDraft`: the text of a README, to be saved as a file next
to the package (the package is never changed), and a table that says what the draft did with each
section of the README template.

What it does, with no LLM:

* **keeps the author's text.** If the package has a README, the draft is that text, character for
  character (line ends and a byte-order mark aside). A section that the README lacks is added at the
  end, in the order of the template, in the heading style the README already uses (a plain-text
  README with ``1. NUMBERED`` or ``CAPITAL`` headings gets headings like that, since Markdown
  headings would make the README check stop seeing them). A section that is there but empty gets
  its text inserted under its heading; nothing the author wrote is removed or changed.
* **fills what rules can say:** the file list (a folder tree, with the number of files, and one line
  for each top-level folder and file), the file formats, the naming convention when one is evident,
  the variables of the data files (name, type, number of empty cells, never a value) and the licence.
* **leaves a placeholder for the rest** (description, contact, methods, how to reproduce, what a file
  or variable is): ``<Describe ...>``, which is template text that the README check
  (:func:`~metacheck.datapackage.docs.check_docs`) reports, so running it on the completed draft
  lists every gap that still needs a person. The placeholders are checked against the template's own
  patterns.

The sections and their headings come from the README template (the ``readme`` option of
``package_docs``; see :mod:`metacheck.datapackage.docs`). A section's ``prompt`` is what the placeholder
asks for, and ``fill`` (``files``, ``variables`` or ``licence``; its id when it has none) names the part
of the package that fills it. The template's ``prompts`` give the wording for the title and the lines
of the file list.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from metacheck.datapackage._docs_policy import (
    ReadmeTemplate,
    SectionSpec,
    _load_licences,
    load_readme_template,
)
from metacheck.datapackage._docs_readme import (
    _is_caps,
    find_headings,
    find_placeholders,
    match_sections,
    read_readme_text,
)
from metacheck.datapackage._readme_facts import (
    TableProfile,
    TableScan,
    candidate_files,
    describe_licence,
    file_tree,
    format_counts,
    human_size,
    naming_notes,
    scan_tables,
    show_name,
    top_level_entries,
)
from metacheck.datapackage.docs import _candidates, _licence_files, find_readmes

__all__ = [
    "DRAFT_COLUMNS",
    "FILE_NAME",
    "VARIABLE_COLUMNS",
    "ReadmeDraft",
    "draft_readme",
    "readme_report",
    "summary_line",
]

#: The name of the file the draft is meant to be saved as.
FILE_NAME = "README.md"

#: Columns of the ``sections`` table of a draft (one row for each section of the template).
DRAFT_COLUMNS = (
    "id",
    "title",
    "in_readme",  # "yes", "empty" (there, but nothing in it) or "no"
    "action",  # kept, filled from the package, added from the package, added as a placeholder, ...
    "placeholders",  # places in this section of the draft that still need a person
    "detail",
)

#: Columns of the ``variables`` table: names, types and counts, never a value.
VARIABLE_COLUMNS = ("path", "sheet", "rows", "position", "variable", "type", "empty_cells")

_MAX_TOP_FILES = 15  # top-level files that get a line of their own
_MAX_VARIABLES = 50  # variables listed for one data file
_MAX_NAMED = 6  # files named in a group of data files with the same variables
_MAX_FORMATS = 12

_TYPE_WORDS = {
    "unknown": "not known (no data rows)",
    "empty": "empty (no values)",
}


@dataclass
class ReadmeDraft:
    """A drafted README and what was done to make it.

    ``text`` is the draft (Markdown) and ``file_name`` the name to save it under. ``files`` is
    ``{file_name: text}``: what a module hands back. ``sections`` has one row for each section of the
    template (:data:`DRAFT_COLUMNS`) and ``variables`` one row for each variable that the draft lists
    (:data:`VARIABLE_COLUMNS`). ``readme_path`` is the package's own README that the draft keeps
    (``None`` when there is none, or when it could not be read); ``placeholders`` is the number of
    places in the draft that the README check reports as template text. ``notes`` are things worth
    knowing about the draft.
    """

    text: str
    sections: Any
    variables: Any
    readme_path: str | None = None
    placeholders: int = 0
    notes: list[str] = field(default_factory=list)
    file_name: str = FILE_NAME
    template_name: str = ""

    @property
    def files(self) -> dict[str, str]:
        """The draft as the files a module hands back."""
        return {self.file_name: self.text}

    @property
    def counts(self) -> dict[str, int]:
        """How many sections were kept, filled or added from the package, added as a placeholder, left."""
        actions = [str(a) for a in self.sections["action"]]
        return {
            "kept": actions.count("kept"),
            "from_package": sum(a.endswith("from the package") for a in actions),
            "placeholders": actions.count("added as a placeholder"),
            "left": actions.count("left as written"),
            "skipped": actions.count("not needed"),
        }


# -- small helpers --------------------------------------------------------------------------------


def _n(count: int, word: str, plural: str | None = None) -> str:
    return f"{count:,} {word if count == 1 else (plural or word + 's')}"


def _code(text: str) -> str:
    """*text* as inline code. The README check does not look inside it for template text."""
    return "`" + show_name(text).replace("`", "'") + "`"


def _rows_text(table: TableProfile) -> str:
    return ("more than " if table.capped else "") + _n(table.rows, "row")


def _cell(text: str) -> str:
    """*text* for a cell of a Markdown table."""
    return text.replace("|", "\\|").replace("\n", " ")


def _placeholder(prompt: str, template: ReadmeTemplate) -> str:
    """A placeholder that asks for *prompt*, written so that the README check reports it.

    ``<Describe ...>`` first; when the template's patterns do not flag that, ``TODO: ...`` or
    ``[...]``. A prompt that starts with an HTML tag name (``a``, ``p``, ``i``) is not flagged by the
    default pattern, which is why the prompts begin with a verb.
    """
    text = " ".join(prompt.split()).rstrip(".:;") or "Write this section"
    for form in (f"<{text}>", f"TODO: {text}", f"[{text}]"):
        if find_placeholders(form, template):
            return form
    return f"<{text}>"


@dataclass
class _Block:
    lines: list[str]
    rules: bool  # the package supplied facts (as well as, perhaps, placeholders)
    placeholders: int = 0
    notes: list[str] = field(default_factory=list)


class _Builder:
    """Makes the text of each section from the package, reading what it needs once."""

    def __init__(
        self,
        pkg: Any,
        template: ReadmeTemplate,
        readmes: Any,
        readme_text: str | None,
        licences: Any,
        limits: dict[str, Any],
    ) -> None:
        self.pkg = pkg
        self.template = template
        self.readme_text = readme_text
        self.licences = licences
        self.limits = limits
        self.files = pkg.files()
        self.dirs = pkg.dirs()
        self.listed = candidate_files(self.files, readmes)
        self._scan: TableScan | None = None
        self.placeholders = 0

    # -- placeholders ------------------------------------------------------------------------

    def ph(self, prompt: str) -> str:
        self.placeholders += 1
        return _placeholder(prompt, self.template)

    def prompt_for(self, spec: SectionSpec) -> str:
        return spec.prompt or f"Write the {spec.title.lower()} section"

    def placeholder_block(self, spec: SectionSpec) -> _Block:
        before = self.placeholders
        lines = [self.ph(self.prompt_for(spec))]
        if spec.fields:
            lines.append("")
        for fld in spec.fields:
            label = fld.label or fld.id
            lines.append(f"- {label}: {self.ph(f'Give the {label.lower()}')}")
        return _Block(lines, rules=False, placeholders=self.placeholders - before)

    # -- the sections ------------------------------------------------------------------------

    def build(self, spec: SectionSpec) -> _Block | None:
        """The text of a section, or ``None`` when the package has nothing the section is about."""
        kind = spec.fill or spec.id
        before = self.placeholders
        block: _Block | None
        if kind == "files":
            block = self.files_block(spec)
        elif kind == "variables":
            block = self.variables_block(spec)
        elif kind == "licence":
            block = self.licence_block(spec)
        else:
            block = self.placeholder_block(spec)
        if block is not None:
            block.placeholders = self.placeholders - before
        return block

    def prompts(self, key: str, default: str) -> str:
        return self.template.prompts.get(key, default)

    # -- files and folders ---------------------------------------------------------------------

    def files_block(self, spec: SectionSpec) -> _Block:
        files = self.listed
        dirs = _candidates(self.dirs)
        if len(files) == 0 and len(dirs) == 0:
            return self.placeholder_block(spec)
        n_files, n_dirs = len(files), len(dirs)
        size = human_size(int(files["size"].sum())) if n_files else "0 bytes"
        lines = [f"The package has {_n(n_files, 'file')} in {_n(n_dirs, 'folder')} ({size}).", ""]
        tree = file_tree(
            [str(r) for r in files["rel"]],
            [str(r) for r in dirs["rel"]],
            int(self.limits["max_tree_lines"]),
        )
        fence = "```"
        while any(fence in line for line in tree):
            fence += "`"
        lines += [fence + "text", *tree, fence, ""]

        licence_names = {
            str(r.name) for r in _licence_files(files).itertuples() if int(r.depth) == 0
        }
        entries = top_level_entries(files, self.dirs, licence_names)
        folder_prompt = self.prompts("folder", "Describe what is in this folder")
        file_prompt = self.prompts("file", "Describe what this file holds or does")
        shown = [e for e in entries if e["kind"] == "folder"]
        top_files = [e for e in entries if e["kind"] == "file"]
        shown += top_files[:_MAX_TOP_FILES]
        for e in shown:
            if e["kind"] == "folder":
                lines.append(
                    f"- {_code(e['name'] + '/')} ({_n(e['files'], 'file')}): {self.ph(folder_prompt)}"
                )
            else:
                lines.append(f"- {_code(e['name'])}: {self.ph(file_prompt)}")
        rest = len(top_files) - _MAX_TOP_FILES
        if rest > 0:
            other = self.prompts("other_files", "Describe these files")
            lines.append(f"- the other {rest} files at the top: {self.ph(other)}")
        if shown or rest > 0:
            lines.append("")

        formats = format_counts(files)
        if formats:
            parts = [
                (f"{_code(ext)}" if ext else "no extension") + f" ({_n(n, 'file')})"
                for ext, n in formats[:_MAX_FORMATS]
            ]
            more = len(formats) - _MAX_FORMATS
            tail = f" and {_n(more, 'other format')}" if more > 0 else ""
            lines += [f"The files are in these formats: {', '.join(parts)}{tail}.", ""]
        naming = naming_notes(files, self.dirs)
        if naming:
            lines += [" ".join(naming), ""]
        while lines and not lines[-1]:
            lines.pop()
        return _Block(lines, rules=True)

    # -- variables -----------------------------------------------------------------------------

    @property
    def scan(self) -> TableScan:
        if self._scan is None:
            self._scan = scan_tables(
                self.pkg,
                max_rows=int(self.limits["max_rows"]),
                max_file_size=float(self.limits["max_file_size"]),
                max_files=int(self.limits["max_files"]),
            )
        return self._scan

    def variables_block(self, spec: SectionSpec) -> _Block | None:
        codebooks = [str(r.rel) for r in self.listed.itertuples() if str(r.doc_role) == "codebook"]
        scan = self.scan
        has_data = any(str(t) == "data" for t in self.listed["data_type"])
        if scan.n_files == 0 and not codebooks and not has_data:
            return None
        lines: list[str] = []
        notes: list[str] = []
        if scan.tables:
            lines += [
                "These are the variables of the data files, with their type and the number of "
                "cells that are empty.",
                "",
            ]
            lines += self.variable_tables(scan.tables)
        if scan.not_read:
            what = _n(scan.not_read, "data file")
            named = ", ".join(_code(f) for f in scan.not_read_files[:5])
            more = f" and {scan.not_read - 5} more" if scan.not_read > 5 else ""
            lines += [
                f"The variables of {named}{more} are not listed: "
                f"{'it' if scan.not_read == 1 else 'they'} could not be read here "
                "(too big, empty or unreadable).",
                "",
            ]
            notes.append(f"{what} not read for the variable list: {named}{more}.")
        if codebooks:
            named = ", ".join(_code(c) for c in codebooks[:5])
            lines.append(f"The variables are described in {named}.")
            return _Block(lines, rules=True, notes=notes)
        lines.append(self.ph(self.prompt_for(spec)))
        return _Block(lines, rules=bool(scan.tables), notes=notes)

    def variable_tables(self, tables: Sequence[TableProfile]) -> list[str]:
        groups: dict[tuple[Any, ...], list[TableProfile]] = {}
        for t in tables:
            key = (tuple((v.name, v.type) for v in t.variables), t.rows == 0)
            groups.setdefault(key, []).append(t)
        lines: list[str] = []
        for group in groups.values():
            first = group[0]
            lines.append(self.group_sentence(group))
            lines.append("")
            single = len(group) == 1
            header = "| Variable | Type | Empty cells |" if single else "| Variable | Type |"
            lines += [header, "|---|---|---|" if single else "|---|---|"]
            for v in first.variables[:_MAX_VARIABLES]:
                kind = _TYPE_WORDS.get(v.type, v.type)
                row = f"| {_cell(_code(v.name))} | {kind} |"
                lines.append(row + (f" {v.missing:,} |" if single else ""))
            if len(first.variables) > _MAX_VARIABLES:
                lines += [
                    "",
                    f"and {_n(len(first.variables) - _MAX_VARIABLES, 'more variable')}.",
                ]
            lines.append("")
        return lines

    @staticmethod
    def label(t: TableProfile) -> str:
        sheet = f" (sheet {_code(t.sheet)})" if t.sheet else ""
        return _code(t.path) + sheet

    def group_sentence(self, group: Sequence[TableProfile]) -> str:
        first = group[0]
        n_vars = _n(len(first.variables), "variable")
        rows = _rows_text
        if len(group) == 1:
            if first.rows == 0:
                return f"{self.label(first)} has no data rows, only a header with {n_vars}."
            return f"{self.label(first)} has {rows(first)} and {n_vars}."
        named = [f"{self.label(t)} ({rows(t)})" for t in group[:_MAX_NAMED]]
        more = len(group) - _MAX_NAMED
        listing = ", ".join(named[:-1]) + f" and {named[-1]}" if len(named) > 1 else named[0]
        if more > 0:
            listing = ", ".join(named) + f" and {_n(more, 'more file')}"
        return f"{listing} have the same {n_vars}."

    # -- licence -------------------------------------------------------------------------------

    def licence_block(self, spec: SectionSpec) -> _Block:
        facts = describe_licence(self.pkg, self.files, self.readme_text, self.licences)
        if facts.files:
            shown = ", ".join(_code(f) for f in facts.files[:3])
            where = f"The full text is in {shown}." if facts.names else f"The terms are in {shown}."
            if facts.names:
                return _Block([f"Licence: {', '.join(facts.names)}. {where}"], rules=True)
            return _Block([where], rules=True)
        if facts.named_in_readme:
            add = self.ph(self.prompts("licence_file", "Add a LICENSE file with the full text"))
            return _Block([f"Licence: {', '.join(facts.named_in_readme)}. {add}"], rules=True)
        return self.placeholder_block(spec)


# -- the text of the draft -------------------------------------------------------------------------


def _heading_text(spec: SectionSpec) -> str:
    """The heading to write for a section: its title, or its hint if only that fits its patterns."""
    for text in (spec.title, spec.hint):
        if text and any(p.search(text) for p in spec.match):
            return text
    return spec.title


def _heading_maker(text: str, sections: Any) -> Callable[[str], str]:
    """A function that writes a heading in the style the README uses, for the sections to be added.

    Markdown headings at the level of the README's own sections. A plain-text README
    (``1. NUMBERED`` or ``CAPITAL`` headings, ``Bold``, ``Colon:``) gets headings like its own, because
    with two or more Markdown headings the README check no longer reads the others as headings.
    """
    headings = find_headings(text)
    strong = [h for h in headings if h.style in ("markdown", "setext")]
    weak = [h for h in headings if h.style not in ("markdown", "setext")]
    by_index = {h.index: h for h in headings}
    matched = [
        by_index[int(row.line) - 1]
        for row in sections.itertuples()
        if row.found and row.kind == "heading" and (int(row.line) - 1) in by_index
    ]
    if len(strong) >= 2 or not weak:
        levels = [h.level for h in matched if h.style in ("markdown", "setext")]
        if not levels and not (len(strong) == 1 and strong[0].level == 1):
            # (a lone level-1 heading is the README's title: the sections go below it)
            levels = [h.level for h in strong if not h.is_title]
        level = min(levels) if levels else 2
        if level == 1 and any(h.is_title for h in headings):
            level = 2
        return lambda title: f"{'#' * level} {title}"

    pool = [h for h in matched if h.style not in ("markdown", "setext")] or weak
    top = min(pool, key=lambda h: (h.level, h.index))
    shout = _is_caps(top.key)

    def case(title: str) -> str:
        return title.upper() if shout else title

    if top.style == "numbered":
        peers = [h for h in weak if h.style == "numbered" and h.level == top.level]
        last = peers[-1]
        m = re.match(r"\s{0,3}(\d{1,2}(?:\.\d{1,2})*)([.)]?)", last.raw)
        parts = (m.group(1) if m else "0").split(".")
        punct = m.group(2) if m else "."
        counter = {"n": int(parts[-1])}

        def numbered(title: str) -> str:
            counter["n"] += 1
            return f"{'.'.join([*parts[:-1], str(counter['n'])])}{punct} {case(title)}"

        return numbered
    if top.style == "bold":
        return lambda title: f"**{title}**"
    if top.style == "caps":
        return lambda title: title.upper()
    return lambda title: f"{title}:"


#: A heading that no section matches, put at the end of a README to read it as the draft will be.
_PROBE = "\n\n## Zzz probe heading\n\nx\n"


def _lone_title(text: str) -> bool:
    """Whether *text* has one heading, of level 1, and no other (a title with its text below)."""
    headings = find_headings(text)
    return (
        len(headings) == 1
        and headings[0].level == 1
        and headings[0].style in ("markdown", "setext")
    )


def _markdown_heading(title: str) -> str:
    return f"## {title}"


def _read_readme(pkg: Any, row: Any) -> str | None:
    """The README's text; a link is only followed when it stays inside the package."""
    path = Path(pkg.root) / str(row["path"])
    if bool(row["link"]):
        try:
            if not path.resolve().is_relative_to(Path(pkg.root).resolve()):
                return None
        except OSError:
            return None
    return read_readme_text(path)


def draft_readme(
    pkg: Any,
    *,
    readme: Any = None,
    licences: Any = None,
    max_rows: int = 100_000,
    max_file_size: float = 50,
    max_files: int = 30,
    max_tree_lines: int = 120,
) -> ReadmeDraft:
    """Draft a README for the data package *pkg* (an :class:`~metacheck.datapackage.OpenedPackage`).

    *readme* is the README template (a dict, a path to a JSON file, or ``None`` for the generic
    default; see :mod:`metacheck.datapackage.docs`) and *licences* the table of licences to
    recognise. The variables of the data files are read from at most *max_files* files of at most
    *max_file_size* MB and *max_rows* rows each; the folder tree has about *max_tree_lines* lines.
    Nothing in the package is changed, and no cell value is read into the result.
    """
    import pandas as pd

    template = load_readme_template(readme)
    licence_table = _load_licences(licences)
    limits = {
        "max_rows": max_rows,
        "max_file_size": max_file_size,
        "max_files": max_files,
        "max_tree_lines": max_tree_lines,
    }
    files = pkg.files()
    readmes = find_readmes(files)
    original: str | None = None
    own_path: str | None = None
    notes: list[str] = []
    if len(readmes):
        first = readmes.iloc[0]
        own_path = str(first["rel"])
        original = _read_readme(pkg, first)
        if original is None:
            notes.append(
                f"The text of {own_path} could not be read, so the draft starts from the template: "
                "merge the two by hand."
            )
            own_path = None
        elif not original.strip():
            notes.append(f"{own_path} is empty, so the draft starts from the template.")
            own_path = None
        elif Path(own_path).suffix.lower() not in (".md", ".txt", "", ".markdown", ".rst", ".text"):
            notes.append(
                f"{own_path} is not plain text; its text was carried over without formatting."
            )
    keep = original is not None and bool(original.strip())
    text = original if (keep and original is not None) else ""
    # A README with one level-1 heading and no other is read by the README check as a heading with
    # that text under it. The sections the draft adds go below it, which makes it the title and
    # its text the description, so that is how the README is looked at here.
    probe = text + _PROBE if keep and _lone_title(text) else text
    sections, _fields = match_sections(probe, template)
    builder = _Builder(pkg, template, readmes, text if keep else None, licence_table, limits)

    inserts: list[tuple[int, list[str]]] = []
    additions: list[tuple[SectionSpec, _Block]] = []
    actions: dict[str, tuple[str, str]] = {}
    headings = find_headings(text) if keep else []
    by_index = {h.index: h for h in headings}

    for row in sections.itertuples():
        spec = template.section(str(row.id))
        assert spec is not None
        if row.found and not row.empty:
            actions[spec.id] = ("kept", "The README has this section; its text is unchanged.")
            continue
        block = builder.build(spec)
        if block is None:
            actions[spec.id] = (
                "not needed",
                "The package has no data files, so there are no variables to list.",
            )
            continue
        notes.extend(n for n in block.notes if n not in notes)
        if row.found:
            heading = by_index.get(int(row.line) - 1)
            bare = int(row.end_line) == int(row.line)
            if row.kind == "heading" and heading is not None and (block.rules or bare):
                inserts.append((heading.body_start, block.lines))
                what = "filled from the package" if block.rules else "added as a placeholder"
                actions[spec.id] = (
                    what,
                    "The heading was there but had no text; the draft fills it in.",
                )
            else:
                actions[spec.id] = (
                    "left as written",
                    "The section is there but only holds template text or is written as a "
                    "line, so it is left as it is.",
                )
            continue
        additions.append((spec, block))
        what = "added from the package" if block.rules else "added as a placeholder"
        actions[spec.id] = (what, "The README has no such section.")

    if keep and not inserts and not additions:
        out = text  # nothing to add: the author's README, as it is
    else:
        lines: list[str]
        if keep:
            lines = text.split("\n")
            for at, block_lines in sorted(inserts, key=lambda t: -t[0]):
                new = ["", *block_lines]
                if at < len(lines) and lines[at].strip():
                    new.append("")
                lines[at:at] = new
            if lines and lines[-1] == "":
                lines.pop()  # the newline at the end of the file, put back below
            make = _heading_maker(text, sections)
        else:
            title = _placeholder(
                builder.prompts("title", "Give the title of the study or dataset"), template
            )
            builder.placeholders += 1
            lines = [f"# {title}", ""]
            make = _markdown_heading
        for spec, block in additions:
            if lines and lines[-1].strip():
                lines.append("")
            lines += [make(_heading_text(spec)), "", *block.lines]
        out = "\n".join(lines) + "\n"

    # what the finished draft looks like to the README check
    final, _ = match_sections(out, template)
    hits = find_placeholders(out, template)
    rows: list[dict[str, Any]] = []
    for row in final.itertuples():
        action, detail = actions[str(row.id)]
        if row.found:
            lo, hi = int(row.line), int(row.end_line)
            count = sum(1 for number, _v in hits if lo <= number <= hi)
        else:
            count = 0
        rows.append(
            {
                "id": str(row.id),
                "title": str(row.title),
                "in_readme": _in_readme(sections, str(row.id)),
                "action": action,
                "placeholders": count,
                "detail": detail,
            }
        )
    asked = builder.placeholders
    if len(hits) < asked:
        notes.append(
            f"The template's placeholder patterns report {len(hits)} of the {asked} placeholders "
            "in the draft, so the README check will not list the rest."
        )
    section_table = pd.DataFrame(
        {
            "id": pd.Series([r["id"] for r in rows], dtype="string"),
            "title": pd.Series([r["title"] for r in rows], dtype="string"),
            "in_readme": pd.Series([r["in_readme"] for r in rows], dtype="string"),
            "action": pd.Series([r["action"] for r in rows], dtype="string"),
            "placeholders": pd.Series([r["placeholders"] for r in rows], dtype="Int64"),
            "detail": pd.Series([r["detail"] for r in rows], dtype="string"),
        }
    )
    return ReadmeDraft(
        text=out,
        sections=section_table,
        variables=_variables_frame(builder._scan),
        readme_path=own_path,
        placeholders=len(hits),
        notes=notes,
        template_name=template.name,
    )


def _in_readme(sections: Any, section_id: str) -> str:
    row = sections.loc[sections["id"] == section_id].iloc[0]
    return "no" if not row["found"] else ("empty" if row["empty"] else "yes")


def _variables_frame(scan: TableScan | None) -> Any:
    import pandas as pd

    rows: dict[str, list[Any]] = {c: [] for c in VARIABLE_COLUMNS}
    for t in scan.tables if scan is not None else []:
        for position, v in enumerate(t.variables, 1):
            rows["path"].append(t.path)
            rows["sheet"].append(t.sheet)
            rows["rows"].append(t.rows)
            rows["position"].append(position)
            rows["variable"].append(v.name)
            rows["type"].append(v.type)
            rows["empty_cells"].append(v.missing)
    dtypes = {
        "path": "string",
        "sheet": "string",
        "rows": "Int64",
        "position": "Int64",
        "variable": "string",
        "type": "string",
        "empty_cells": "Int64",
    }
    return pd.DataFrame({c: pd.Series(rows[c], dtype=dtypes[c]) for c in VARIABLE_COLUMNS})  # type: ignore[call-overload]


# -- what the module says about the draft ---------------------------------------------------------


def summary_line(draft: ReadmeDraft) -> str:
    """One sentence for the module's summary: what the draft keeps, adds, and still needs."""
    counts = draft.counts
    parts = [
        f"{counts[key]} {label}"
        for key, label in (
            ("kept", "kept"),
            ("from_package", "filled from the package"),
            ("placeholders", "left as a placeholder"),
            ("left", "left as written"),
        )
        if counts[key]
    ]
    base = (
        f"Drafted {draft.file_name} from {draft.readme_path}"
        if draft.readme_path
        else f"Drafted {draft.file_name} from the template (the package has no README to keep)"
    )
    sections = f"Sections: {', '.join(parts)}." if parts else "No sections to fill."
    gaps = (
        f"{_n(draft.placeholders, 'place')} still to fill in (the README check lists them)."
        if draft.placeholders
        else "No placeholders left."
    )
    return f"{base}. {sections} {gaps}"


def _fence_around(text: str) -> str:
    """A code fence long enough to hold *text*, which has fences of its own."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    return "`" * max(4, longest + 1)


def readme_report(draft: ReadmeDraft) -> list[Any]:
    """The module's report as blocks: what was done, the sections, and the draft itself."""
    import pandas as pd

    from metacheck.report import collapse_section, scroll_table

    keeps = (
        f"The text of `{draft.readme_path}` is kept as it is and only what is missing is added. "
        if draft.readme_path
        else "The package has no README, so the draft follows the README template. "
    )
    intro = (
        f"This is a draft of `{draft.file_name}`, made by rules from what the checks know about the "
        f"package: its files, formats, variables and licence. {keeps}Nothing in the package was "
        "changed. Every `<...>` is a gap that a person has to fill in; run the README check on the "
        "finished file to see what is left. No value of a data file is in the draft."
    )
    blocks: list[Any] = ["#### README draft", intro]
    if draft.notes:
        blocks.append("\n".join(f"- {n}" for n in draft.notes))
    sections = draft.sections
    table = pd.DataFrame(
        {
            "Section": sections["title"].astype(str),
            "In the README": sections["in_readme"].astype(str),
            "In the draft": sections["action"].astype(str),
            "To fill in": sections["placeholders"].astype(int),
        }
    )
    blocks.append(scroll_table(table, colwidths=[34, 18, 32, 16], maxrows=20, escape=True))
    fence = _fence_around(draft.text)
    shown = collapse_section(
        [f"{fence}markdown\n{draft.text.rstrip()}\n{fence}"],
        title=f"The draft ({draft.file_name})",
        callout="note",
        collapse=True,
    )
    blocks.extend(shown) if isinstance(shown, list) else blocks.append(shown)
    return blocks
