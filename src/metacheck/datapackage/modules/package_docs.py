"""Data Package Documentation: The README of a data package and whether the package has the parts it should have."""

from __future__ import annotations

from typing import Any

from metacheck.module import module

from ._common import no_package, summary_table


@module(
    title="Data Package Documentation",
    description="The README of a data package and whether the package has the parts it should have.",
    details="""
        Checks the documentation of a data package before it is archived, and gives a checklist a data steward can work down:

        * a README at the top of the package, in plain text (TXT) or Markdown;
        * the sections it should have (description, contact, files, and optionally methods, how to reproduce, licence and variables), found by their headings, also in plain-text READMEs with numbered or ALL-CAPS headings;
        * no template text left behind (`<help text>`, `[FILENAME]`, `TODO`) and a contact e-mail address;
        * whether the README names the package's folders and files;
        * a licence, as a LICENSE file or named in the README;
        * the parts the package should have: the data, a codebook, and, when the research involved people, the ethical approval and the informed consent form.

        The README template and the list of parts are data, not code: pass your own as a dict or a path to a JSON file (`readme`, `components`), and change how serious a finding is with `severity`.
        Whether the research involved people is taken from `human_participants`, or, when the paper has text, from the same live-data detection that `ethics_check` uses; when it is not known, the checklist leaves those parts for a person to decide.

        **Optional: ask the paper's abstract (off by default).** With `abstract_lookup=TRUE` and no answer from the above, the abstract of the paper is fetched online, by DOI (`paper_doi`, or the DOI on the README's line for the publication), from Crossref and, if it has none, OpenAlex, and the same live-data detection runs on it. A hit means the research involved people; anything else leaves the question open (`manual`), because an abstract that does not mention participants proves nothing. **Only the DOI is sent.** No file name, README text or any other content of the package leaves the machine, and the abstract itself is not kept (the result holds the one sentence that matched). With no network, a record without an abstract, a bad DOI or `--offline`, nothing changes except one line that says why. The module does not declare `requires=["network"]`, because it works fully offline by default and `--offline` would then leave it out altogether; it reads the offline setting itself.
    """,
    keywords=["general"],
    author=["Jakub Werner"],
    params={
        "paper": "a paper object or paperlist object (may be empty)",
        "local_path": "the data package: a folder, or a zip or tar archive of one",
        "readme": "the README template: a dict, or the path to a JSON file (default: a generic template)",
        "components": "the parts a package should have: a dict, or the path to a JSON file (default: a generic list)",
        "human_participants": "whether the research involved people: TRUE or FALSE; by default it is decided from the paper's text when it has any, and left to a person when not (or, with `abstract_lookup`, from the paper's abstract)",
        "min_file_list_coverage": "the share of the package's top-level folders and files (and data files, up to 50) the README has to mention (default 0.8)",
        "severity": "a dict (or the path to a JSON file) that maps a rule id to 'problem', 'suggestion' or 'info', to change how serious a finding is",
        "abstract_lookup": "TRUE to decide whether the research involved people from the paper's abstract, fetched online (Crossref, then OpenAlex) when `human_participants` and the paper's text do not decide it; only the DOI is sent, never any content of the package; default FALSE (no request is made)",
        "paper_doi": "the DOI of the paper, for `abstract_lookup` (10.xxxx/..., or a https://doi.org/ link); default: the DOI on the README's line for the publication (such as 'DOI of the publication: ...'); it is used only with `abstract_lookup=TRUE`",
    },
)
def package_docs(
    paper: Any,
    local_path: Any = None,
    readme: Any = None,
    components: Any = None,
    human_participants: Any = None,
    min_file_list_coverage: float = 0.8,
    severity: Any = None,
    abstract_lookup: Any = False,
    paper_doi: Any = None,
) -> dict[str, Any]:
    """The README of a data package and whether the package has the parts it should have."""
    from metacheck.datapackage import package_for
    from metacheck.datapackage.abstract import decide_from_abstract, lookup_asked
    from metacheck.datapackage.docs import (
        check_docs,
        report_blocks,
        resolve_human_participants,
        status_counts,
        summary_line,
    )

    pkg = package_for(local_path)
    if pkg is None:
        return no_package()
    from metacheck.module import use_setting

    people, source = resolve_human_participants(human_participants, paper)
    asked = lookup_asked(abstract_lookup)
    note: str | None = None
    decision = None
    if asked and people is None:
        decision = decide_from_abstract(
            pkg, paper_doi, readme=readme, offline=bool(use_setting("offline", False))
        )
        people, source, note = decision.people, decision.source, decision.note
    elif asked:
        note = f"The abstract was not looked up: it was decided already ({source})."
    elif paper_doi is not None and str(paper_doi).strip():
        note = "paper_doi is set but abstract_lookup is off, so nothing was looked up."
    result = check_docs(
        pkg,
        readme=readme,
        components=components,
        human_participants=people,
        min_file_list_coverage=min_file_list_coverage,
        severity=severity,
        human_participants_note=None if decision is None else note,
    )
    counts = status_counts(result)
    summary = summary_table(
        paper,
        readme=result.readme_path or "",
        checks_fail=counts["fail"],
        checks_warn=counts["warn"],
        checks_manual=counts["manual"],
        checks_pass=counts["pass"],
    )
    return {
        "table": result.findings,
        "summary_table": summary,
        "traffic_light": result.traffic_light,
        "summary_text": summary_line(result),
        "report": report_blocks(result),
        "checklist": result.checklist,
        "readme_path": result.readme_path,
        "readme_text": result.readme_text,
        "sections": result.sections,
        "fields": result.fields,
        "components": result.components,
        "human_participants": people,
        "human_participants_source": source,
        "human_participants_note": note,
        "abstract_decision": None if decision is None else decision.as_dict(),
    }
