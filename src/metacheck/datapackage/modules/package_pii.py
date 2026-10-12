"""Data Package Personal Data: Personal data by value in the data files of a data package."""

from __future__ import annotations

from typing import Any

from metacheck.module import module

from ._common import no_package, summary_table


@module(
    title="Data Package Personal Data",
    description="Personal data by value in the data files of a data package: BSN, student numbers, phone numbers, IPv6 addresses, postcodes and names.",
    details="""
        Reads the first rows of every data file in a data package (csv, tsv, Excel, ODS,
        SPSS, Stata and SAS files) and looks at the values of each column. The checklist has
        one item for each kind of personal data and one for whether the files could be read:

        - **Dutch citizen service numbers (BSN)**: 9 digits that pass the 11-check ("elfproef").
          A column is flagged when most of its filled cells are such numbers, so a column of
          ordinary 9-digit IDs is not.
        - **Student numbers**: 7 digits (also `s` and 6 or 7 digits), only in a column whose name
          says student (`student`, `studentnummer`, `s-number`, ...). A bare 7-digit number
          alone is not flagged.
        - **Phone numbers**: Dutch (`06-12345678`, `040 247 4747`, `+31 6 12345678`) and
          international (`+44 20 7946 0958`, `0044 ...`) formats. Timestamps, EANs and other
          long numbers are not.
        - **IPv6 addresses**: written in full or compressed (`2001:db8::1`). Times such as
          `12:30:45` and MAC addresses are not.
        - **Dutch postcodes**: `1234 AB`, four digits and two capitals after a space (without
          the space or in lower case only in a column named like a postcode column).
        - **Columns with people's names**: a column whose name says it holds names (`naam`,
          `voornaam`, `first_name`, `surname`, ...) and whose values read like names. A column
          called only `name` needs full names (two words or more) as values, and a name that
          says something else (`file_name`, `variable`, `condition`) is not looked at.

        A column is flagged when enough of its filled cells match (the share and the number
        are lower when the column's name points the same way). Only a cell that is the number
        or name on its own counts, not one inside a sentence. What is reported is the file,
        the column's name and the number of cells that matched, never a value. These are hints
        for a person to check: a flagged item has the status `manual`.

        `max_rows` rows of each file (and each of the first 10 sheets of a workbook) are read;
        files over `max_file_size` MB are skipped, and so are files beyond the first
        `max_files`; skipped files show on the `pii_scan` item. The output has the findings
        (`table`), the `checklist` and `hits`: one row for each flagged column, with its
        counts. `severity` maps a rule id (`bsn`, `student-number`, `phone`, `ipv6`, `postcode`,
        `person-name`, `file-too-large`, `file-unreadable`, `files-not-scanned`) to `problem`
        (the item fails), `suggestion` (the default), `info` or `ignore` (the detector is not
        run). This is separate from `metacheck::data_check`, which does not change.
    """,
    keywords=["general"],
    author=["Jakub Werner"],
    params={
        "paper": "a paper object or paperlist object (may be empty)",
        "local_path": "the data package: a folder, or a zip or tar archive of one",
        "max_rows": "the most rows read from each data file or sheet (default 5000)",
        "max_file_size": "files bigger than this, in MB, are not read (default 100)",
        "max_files": "the most data files read (default 500)",
        "severity": 'a dict (or the path to a JSON file) of rule id to "problem", "suggestion", "info" or "ignore"',
    },
)
def package_pii(
    paper: Any,
    local_path: Any = None,
    max_rows: int = 5000,
    max_file_size: float = 100,
    max_files: int = 500,
    severity: Any = None,
) -> dict[str, Any]:
    """Personal data by value in the data files of a data package."""
    from metacheck.datapackage import package_for
    from metacheck.datapackage.pii import check_personal_data, pii_report

    pkg = package_for(local_path)
    if pkg is None:
        return no_package()
    res = check_personal_data(
        pkg,
        max_rows=int(max_rows),
        max_file_size=float(max_file_size),
        max_files=int(max_files),
        severity=severity,
    )
    return {
        "table": res.findings,
        "summary_table": summary_table(
            paper,
            data_files=res.n_files,
            scanned=res.n_scanned,
            bsn=res.flagged.get("bsn", 0),
            student_number=res.flagged.get("student-number", 0),
            phone=res.flagged.get("phone", 0),
            ipv6=res.flagged.get("ipv6", 0),
            postcode=res.flagged.get("postcode", 0),
            person_name=res.flagged.get("person-name", 0),
        ),
        "traffic_light": res.traffic_light,
        "summary_text": res.summary_text,
        "report": pii_report(res),
        "checklist": res.checklist,
        "hits": res.hits,
    }
