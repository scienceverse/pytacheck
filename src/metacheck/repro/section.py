"""The checklist report's own section for ``reproducibility_check``.

Port of metacheck's ``R/reproducibility_check_section.R`` (the module's
``checklist_section``, shown under its own heading by the checklist report): for each
script that was run, what happened, in words a reader of the report knows, then the
problems in the files and the comparison with the paper. ``.repro_error_line`` of that
file lives in :mod:`metacheck.repro.outcomes`, which also needs it.

Where metacheck reads the option ``metacheck.docker_resource_limits`` (the memory each
script may use), the value is the keyword argument ``memory_gb`` here, ``None`` when no
limit is set.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

import pandas as pd

from metacheck._r import (
    as_character,
    format_num,
    grepl,
    plural,
    r_round,
    regexec,
    regextract,
    strsplit,
    sub,
)
from metacheck._values import as_int, is_missing, is_true
from metacheck.repro.core import _basename
from metacheck.repro.outcomes import (
    _repro_error_line,
    _repro_failure_reason,
    _repro_internet_text,
    _repro_memory_limit_text,
    _repro_missing_program,
    _repro_out_of_memory,
    _repro_unopened_files,
)
from metacheck.repro.software import _repro_not_analysis_why
from metacheck.statout.spv import _spv_html_escape

#: Packages removed from CRAN, with the packages that replaced them: rgdal, rgeos and
#: maptools were retired in October 2023, and their successors are sf and terra.
_REPRO_RETIRED_PACKAGES = {"rgdal": "sf or terra", "rgeos": "sf or terra", "maptools": "sf"}

#: Errors of code written for older versions of a package: a function or argument that
#: was removed or renamed since, as group_indices()'s arguments, ggplot2's "Cannot add
#: <ggproto> objects together", or "is defunct" (#575).
_REPRO_CHANGED_PACKAGE_PAT = (
    "defunct|deprecated|is not an exported object|unused argument|Cannot add <ggproto>|"
    "was removed in|no longer supported|has been removed|"
    # Python: keras.utils.generic_utils, requests_cache.core
    r"module '[^']+' has no attribute|cannot import name|No module named '[^']+\.[^']+'"
)

#: The quote characters around a package's name in R's and Python's errors.
_PKG_PAT = "(?:no package called|No module named) ['\"‘]([^'\"’]+)['\"’]"

#: The outcomes that are not scripts with a result of their own (the files of the
#: section's count; ``static_*`` are the outcomes of files that were never handed to R
#: or Python).
_NOT_COUNTED = frozenset(
    {
        "run_by_source",
        "run_by_import",
        "jags_model",
        "not_r_content",
        "static_jags_model",
        "static_not_r_content",
        "defines_functions",
        "not_analysis",
        "static_defines_functions",
        "static_not_analysis",
        "not_downloaded",
        "empty",
        "static_empty",
    }
)

#: What happened to the scripts of the groups of the headline.
_HEADLINE_GROUPS = {
    "errored": "stopped with an error",
    "timed_out": "ran out of time",
    "dependency_unavailable": "could not run on our server",
    "docker_failed": "could not run on our server",
    "needs_a_person": "needed a person",
    "needs_internet": "needed the internet",
    "out_of_memory": "ran out of memory",
    "download_too_large": "downloaded too much",
}


def _strs(df: pd.DataFrame | None, name: str, n: int) -> list[str | None]:
    """A text column as a list, ``None`` for a missing value; all ``None`` when absent."""
    if df is None or name not in df.columns:
        return [None] * n
    return [None if is_missing(v) else str(v) for v in df[name].tolist()]


def _repro_cell(x: Any) -> str:
    """Port of R/reproducibility_check_section.R::.repro_cell().

    Text for a table cell, shown as it is: HTML-escaped, and the characters Markdown
    would read as formatting (or ``|``, which ends a table cell) written as HTML
    character references.
    """
    text = _spv_html_escape(x)
    for ch in "\\|*_`$[]~^":
        text = text.replace(ch, f"&#{ord(ch):d};")
    return text


def _code(x: Any) -> str:
    return f"<code>{_repro_cell(x)}</code>"


def _repro_advice(
    tbl: pd.DataFrame,
    rr: pd.DataFrame,
    outcome: Sequence[str | None],
    mi: pd.DataFrame,
    install_results: pd.DataFrame | None = None,
    versions_recorded: bool = False,
    timeout: float = 600,
    *,
    memory_gb: float | None = None,
) -> tuple[list[str], list[int]]:
    """Port of R/reproducibility_check_section.R::.repro_advice().

    How to make the code run on another computer, from what stopped each script (#575):
    one block per problem, in the order in which they should be fixed (a problem near
    the top can cause the ones below it), each with the scripts it affects, why it
    fails, and the steps to fix it. It is written for the authors, as guidance a student
    can follow. A failure whose cause the output does not show gets a block that says
    so, rather than a guess.

    *rr* holds the run results in the order of *tbl*; *outcome* the outcome per row;
    *mi* the rows of ``repro_missing_inputs()``; *install_results* the install of the
    packages; *versions_recorded* whether the deposit records package versions (see
    ``.repro_recorded_advice()``); *timeout* the time limit per script, in seconds.
    Returns the blocks (Markdown) and the positions (0-based) of the rows of *tbl* they
    cover, sorted (R's attribute ``scripts``); both empty when nothing failed.
    """
    n = len(tbl)
    name = [f or "" for f in _strs(tbl, "file_name", n)]
    is_py = [bool(grepl(r"\.(py|ipynb)$", f, ignore_case=True)) for f in name]
    error = _strs(rr, "error", n)
    stderr = _strs(rr, "stderr", n)
    msg = [f"{e or ''} {s or ''}" for e, s in zip(error, stderr, strict=True)]
    given = _strs(rr, "reason", n)
    reason = given if any(r is not None for r in given) else _repro_failure_reason(outcome, msg)
    key = [
        _repro_error_line(m, python=py, max_chars=5000) for m, py in zip(msg, is_py, strict=True)
    ]
    err = [oc == "errored" for oc in outcome]
    writes = [
        strsplit(w.lower(), r",\s*") if w is not None else [] for w in _strs(tbl, "writes", n)
    ]
    mi_base = [(b or "").lower() for b in _strs(mi, "basename", len(mi))]
    status = _strs(mi, "status", len(mi_base))
    detail = _strs(mi, "detail", len(mi_base))
    similar = _strs(mi, "similar_to", len(mi_base))

    blocks: dict[str, str] = {}
    covered: list[int] = []

    def lines_of(x: Sequence[str]) -> str:
        # the lines naming the scripts of a problem: at most ten, then how many more
        x = list(dict.fromkeys(x))
        out = [f"- {s}" for s in x[:10]]
        if len(x) > 10:
            out.append(f"- and {len(x) - 10:d} more")
        return "\n".join(out)

    def block(
        id_: str,
        rows: Sequence[int],
        title: str,
        lines: Sequence[str] = (),
        text: Sequence[str] = (),
        steps: Sequence[str] = (),
    ) -> None:
        if not rows:
            return
        parts = [f"**{title}**"]
        if lines:
            parts.append(lines_of(lines))
        parts.extend(text)
        if steps:
            parts.append("\n".join(f"{k:d}. {s}" for k, s in enumerate(steps, 1)))
        blocks[id_] = "\n\n".join(parts)
        covered.extend(r for r in rows if r not in covered)

    def rows_where(test: Callable[[int], bool]) -> list[int]:
        return [i for i in range(n) if test(i)]

    def not_covered(rows: Sequence[int]) -> list[int]:
        return [r for r in dict.fromkeys(rows) if r not in covered]

    # A file a script could not open: what kind of file it is decides what helps.
    found: dict[str, list[tuple[int, str]]] = {
        k: [] for k in ("by_script", "absolute", "renamed", "archived", "absent")
    }
    for i in rows_where(lambda i: err[i] and reason[i] == "file"):
        for p in _repro_unopened_files(msg[i], full=True):
            base = _basename(p) or ""
            b = base.lower()
            k = mi_base.index(b) if b in mi_base else None
            maker = [j for j in range(n) if b in writes[j] and j != i]
            if maker:
                line = f"{_code(name[i])} needs {_code(base)}, which {_code(name[maker[0]])} makes."
                found["by_script"].append((i, line))
            elif k is not None and similar[k] is not None:
                line = (
                    f"{_code(name[i])} reads {_code(base)}; your deposit holds {_code(similar[k])}."
                )
                found["renamed"].append((i, line))
            elif k is not None and status[k] == "in_archive":
                arch = sub("^.*only inside (.*?); unpack.*$", r"\1", detail[k])
                where = "an archive" if arch == detail[k] else _code(arch)
                line = f"{_code(name[i])} reads {_code(p)}, which is inside {where}."
                found["archived"].append((i, line))
            elif grepl("^(/(?!sandbox/)|~|[A-Za-z]:)", p, perl=True):
                found["absolute"].append((i, f"{_code(name[i])} reads {_code(p)}."))
            else:
                found["absent"].append((i, f"{_code(name[i])} reads {_code(base)}."))

    def rows_of(kind: str) -> list[int]:
        return [i for i, _ in found[kind]]

    def text_of(kind: str) -> list[str]:
        return [line for _, line in found[kind]]

    block(
        "by_script",
        rows_of("by_script"),
        "A script needs a file that another script should have made.",
        text_of("by_script"),
        [
            "The other script stopped before it made the file, so fix that script first; this "
            "one may then run as well. To make the order clear to readers, number your scripts "
            "in the order they run (<code>01&#95;clean.R</code>, <code>02&#95;model.R</code>), "
            "or add one main script that runs the others in order."
        ],
    )
    block(
        "absent",
        rows_of("absent"),
        "Files are missing from your deposit.",
        text_of("absent"),
        ["The script stops at the line that reads the file."],
        [
            "Find the file on your computer.",
            "Add it to the deposit, in the folder where the script looks for it.",
            "If you cannot share it, for example for privacy reasons, say so in the README, "
            "and explain where readers can get it and where to put it.",
        ],
    )
    block(
        "absolute",
        rows_of("absolute"),
        "A script uses a location that exists only on your computer.",
        text_of("absolute"),
        ["No other computer has that folder, and the file is also not in the deposit."],
        [
            "Add the file to the deposit.",
            "In the script, write the location from the top folder of your project, such as "
            '<code>data/survey.csv</code>. In R, <code>here::here("data", "survey.csv")</code> '
            "does this for you.",
            "Remove any <code>setwd()</code> lines.",
        ],
    )
    block(
        "renamed",
        rows_of("renamed"),
        "A file may have been renamed.",
        text_of("renamed"),
        [
            "Check whether they are the same file. If so, use one name in both the script and "
            "the deposit."
        ],
    )
    block(
        "archived",
        rows_of("archived"),
        "A file is only inside a zip file.",
        text_of("archived"),
        [
            "Code cannot read a file inside an archive until someone unpacks it. Either upload "
            "the files unpacked, in the folders the script expects, or start the script with a "
            'line that unpacks the archive, such as <code>unzip("Data.zip")</code> in R.'
        ],
    )
    unnamed = not_covered(rows_where(lambda i: err[i] and reason[i] == "file"))
    block(
        "unnamed",
        unnamed,
        "A script could not open a file, but the error does not say which.",
        [_code(name[i]) for i in unnamed],
        [
            "Check every file the script reads. Make sure each one is in the deposit, and that "
            "its location is written from the top folder of the project."
        ],
    )

    # Code written for older versions of its packages.
    changed = rows_where(
        lambda i: (
            err[i]
            and reason[i] in ("other", "package")
            and bool(grepl(_REPRO_CHANGED_PACKAGE_PAT, key[i], ignore_case=True))
        )
    )

    def short(i: int) -> str:
        return str(sub(r"^Error( in [^:]*)? *: *", "", key[i][:160]))

    block(
        "changed",
        changed,
        "Your code was written for older versions of its packages.",
        [f'{_code(name[i])}: "{_repro_cell(short(i))}"' for i in changed],
        [
            "A function or option has changed since you wrote the code, so it stops with "
            "today's version of the package."
        ],
        []
        if versions_recorded
        else [
            "In R, run <code>renv::init()</code> in your project and add the "
            "<code>renv.lock</code> file this creates to the deposit; in Python, run "
            "<code>pip freeze &gt; requirements.txt</code> and add that file.",
            "State your version of R or Python in the README.",
            "Better still, update the code so it also works with current versions.",
        ],
    )
    if versions_recorded and "changed" in blocks:
        blocks["changed"] += "\n\n" + (
            "Your deposit records which package versions you used, and a run with those "
            "versions may succeed. Check that the record lists every package the scripts load."
        )

    # A package the code loads that was not there, which the install did not know of
    def pkg_of(m: str) -> str | None:
        hit = regexec(_PKG_PAT, m, perl=True)
        return hit[1] if len(hit) > 1 else None

    missing_pkg = not_covered(rows_where(lambda i: err[i] and reason[i] == "package"))
    mp = [pkg_of(msg[i]) for i in missing_pkg]
    block(
        "missing_pkg",
        missing_pkg,
        "A package was not installed.",
        [
            _code(name[i]) if p is None else f"{_code(name[i])}: {_code(p)}"
            for i, p in zip(missing_pkg, mp, strict=True)
        ],
        [
            "The code loads it in a way we could not detect before running it. List every "
            "package with its version, in <code>renv.lock</code> (R) or "
            "<code>requirements.txt</code> (Python), and load each one at the top of the "
            "script with <code>library()</code> or <code>import</code>."
        ],
    )

    # A package that could not be installed.
    unknown_source: set[str | None] = set()
    if (
        install_results is not None
        and len(install_results)
        and {"package", "installed"} <= set(install_results.columns)
    ):
        categories = _strs(install_results, "category", len(install_results))
        unknown_source = {
            p
            for p, ok, cat in zip(
                _strs(install_results, "package", len(install_results)),
                install_results["installed"].tolist(),
                categories,
                strict=True,
            )
            if not is_true(ok) and cat == "source_unknown"
        }
    dep = rows_where(lambda i: outcome[i] == "dependency_unavailable")
    if dep:
        dp = [pkg_of(msg[i]) for i in dep]
        pkgs = list(dict.fromkeys(p for p in dp if p is not None))
        explain = []
        for p in pkgs:
            if p in _REPRO_RETIRED_PACKAGES:
                successors = " or ".join(_code(s) for s in _REPRO_RETIRED_PACKAGES[p].split(" or "))
                explain.append(
                    f"{_code(p)} was removed from CRAN in 2023 and can no longer be installed. "
                    f"Replace it with {successors}, which do the same work, and check that your "
                    "results stay the same."
                )
            elif p in unknown_source:
                install = f'remotes::install_github("user/{p}")'
                explain.append(
                    f"{_code(p)} is not on CRAN, and your code does not say where it comes from. "
                    f"Add the command that installs it to the README, for example {_code(install)}."
                )
            else:
                explain.append(
                    f"{_code(p)} could not be installed with the current version of R, probably "
                    "because it is old. Record the versions you used (in R, "
                    "<code>renv::init()</code> writes them to a <code>renv.lock</code> file) and "
                    "your R version. To be sure the code keeps running, provide a Docker image: "
                    "a frozen copy of the whole computer setup the code needs, which you can "
                    "build from <code>rocker/r-ver</code> at your R version."
                )
        block(
            "dep",
            dep,
            "A package could not be installed.",
            [
                _code(name[i]) if p is None else f"{_code(name[i])} needs {_code(p)}."
                for i, p in zip(dep, dp, strict=True)
            ],
            explain,
        )

    # What only the authors' computer, or another computer than ours, has.
    program = _repro_missing_program(msg)
    prog = rows_where(lambda i: err[i] and program[i] is not None)
    block(
        "program",
        prog,
        "A script starts a program outside R or Python.",
        [f"{_code(name[i])} starts {_code(program[i])}." for i in prog],
        [
            "Our computer does not have this program, and neither will most readers'. Name the "
            "program, its version and where to get it in the README. A Docker image can "
            "include it."
        ],
    )
    oom = _repro_out_of_memory(msg)
    memory = rows_where(lambda i: outcome[i] == "out_of_memory" or (err[i] and oom[i]))
    keys = [
        i
        for i in rows_where(
            lambda i: (
                err[i]
                and bool(
                    grepl("API key|api[_ ]key|set env(ironment)? var", key[i], ignore_case=True)
                )
            )
        )
        if i not in prog
    ]
    windows = [
        i
        for i in rows_where(
            lambda i: err[i] and bool(grepl(r"RODBC|odbcConnect|\.accdb|\.mdb\b", msg[i]))
        )
        if i not in prog
    ]
    skip = {*prog, *memory, *keys, *windows}
    env = [i for i in rows_where(lambda i: err[i] and reason[i] == "environment") if i not in skip]
    block(
        "env",
        env,
        "A script needs software our computer does not have.",
        [f'{_code(name[i])}: "{_repro_cell(short(i))}"' for i in env],
        [
            "Name everything the code needs beyond R and Python packages in the README; a "
            "Docker image can include all of it."
        ],
    )
    block(
        "keys",
        keys,
        "A script uses an online service that needs a personal key.",
        [_code(name[i]) for i in keys],
        [
            "Say in the README which service it is and how to get a key. Services change or "
            "close (Dark Sky has closed), so also save the data the script downloads, add it "
            "to the deposit, and let the script read the saved copy."
        ],
    )
    block(
        "windows",
        windows,
        "A script reads a Microsoft Access database, which works only on Windows.",
        [_code(name[i]) for i in windows],
        ["Export the tables to CSV files, add them to the deposit, and read those instead."],
    )

    # What the code itself does.
    undefined = not_covered(rows_where(lambda i: err[i] and reason[i] == "undefined"))
    objects: list[str | None] = []
    for i in undefined:
        hit = regexec(
            "object '([^']+)' not found|could not find function \"([^\"]+)\"|"
            "name '([^']+)' is not defined",
            key[i],
        )
        objects.append(next((g for g in hit[1:] if g), None))
    block(
        "undefined",
        undefined,
        "A script uses something it does not create or load itself.",
        [
            _code(name[i]) if o is None else f"{_code(name[i])}: {_code(o)}"
            for i, o in zip(undefined, objects, strict=True)
        ],
        [
            "Usually the object was still in your R session from earlier work, or another script made it."
        ],
        [
            "Restart R (in RStudio: Session > Restart R).",
            "Run each script from top to bottom, and fix what is missing.",
            "If a script needs results of another, save them to a file in the first script "
            "and read them in the second.",
            "Load every package with <code>library()</code> at the top.",
        ],
    )
    syntax = rows_where(
        lambda i: (
            outcome[i] in ("not_parsed", "static_parse_error") or (err[i] and reason[i] == "syntax")
        )
    )
    block(
        "syntax",
        syntax,
        "R or Python could not read a script, because of a mistake in the code itself.",
        [_code(name[i]) for i in syntax],
        [
            "Run each script once in a fresh session before you deposit it; the error message "
            "shows the line to fix."
        ],
    )
    slow = rows_where(lambda i: outcome[i] in ("timed_out", "skipped_time_limit"))
    limit_minutes = r_round(timeout / 60, 1)
    minutes = format_num(limit_minutes, 7)
    block(
        "slow",
        slow,
        f"A script runs longer than our limit of {minutes} minute{plural(limit_minutes)}.",
        [
            f"{_code(name[i])} was not started, or was stopped early, because the time for all "
            "scripts together had run out."
            if outcome[i] == "skipped_time_limit"
            else _code(name[i])
            for i in slow
        ],
        [
            "State in the README how long each script runs. If some steps take hours, save "
            "their results (such as fitted models) to files, add those to the deposit, and let "
            "the script read them, so readers can check the rest without waiting."
        ],
    )
    gb = [g for g in regextract(r"memory limit of [0-9.]+ GB", [msg[i] for i in memory]) if g]
    limit_text = _repro_memory_limit_text(memory_gb=memory_gb)
    if not gb and limit_text is not None:
        gb = [f"memory limit of {limit_text}"]
    block(
        "memory",
        memory,
        f"A script needs more memory than our {sub('memory limit of', 'limit of', gb[0])}."
        if gb
        else "A script needs more memory than our limit.",
        [_code(name[i]) for i in memory],
        ["State in the README how much memory the code needs."],
    )
    online = rows_where(lambda i: outcome[i] in ("needs_internet", "download_too_large"))
    block(
        "online",
        online,
        "A script downloads data while it runs.",
        [_code(name[i]) for i in online],
        [
            "Online files can move, change or disappear. Save the downloaded files, add them "
            "to the deposit, and let the script read the saved copies."
        ],
    )
    person = rows_where(lambda i: outcome[i] in ("needs_a_person", "static_needs_a_person"))
    block(
        "person",
        person,
        "A script waits for a person to choose a file, answer a question, or give arguments "
        "on the command line.",
        [_code(name[i]) for i in person],
        [
            "Nobody can answer when the code is checked automatically. Write the file name into "
            "the script, and put the exact command with its arguments in the README."
        ],
    )

    # A failure no block explains is said to be one, not left out
    failed_outcomes = (
        "dependency_unavailable",
        "timed_out",
        "skipped_time_limit",
        "needs_internet",
        "download_too_large",
        "needs_a_person",
        "not_parsed",
        "out_of_memory",
    )
    unknown = not_covered(rows_where(lambda i: err[i] or outcome[i] in failed_outcomes))
    block(
        "unknown",
        unknown,
        "Metacheck can't automatically establish what caused the error, or how it should be "
        "improved.",
        [_code(name[i]) for i in unknown],
        ["The error message of each script is in the table above."],
    )

    # in the order in which the problems should be fixed
    order = (
        "by_script",
        "absent",
        "absolute",
        "renamed",
        "archived",
        "unnamed",
        "changed",
        "missing_pkg",
        "dep",
        "program",
        "env",
        "keys",
        "windows",
        "undefined",
        "syntax",
        "slow",
        "memory",
        "online",
        "person",
        "unknown",
    )
    return [blocks[k] for k in order if k in blocks], sorted(covered)


def _repro_script_result(
    oc: str,
    *,
    lang: str,
    err: str,
    pkg: str,
    inputs: Sequence[str],
    error: str | None,
    timeout: float,
    used_by: str | None,
    not_analysis_why: str,
    source_unknown: Sequence[str],
    install_missing: bool,
    net: Mapping[str, Any] | None,
    input_reason: Callable[[str], str],
    memory_gb: float | None,
    error_message: str | None = None,
) -> tuple[str, str]:
    """What happened to one script, as the *Result* and *What happened* cells."""
    error = error or ""
    match oc:
        case "ran_ok":
            return "Ran to the end", ""
        case "run_by_source":
            return (
                "Ran inside another script",
                "Another script runs it with source(), so it ran as part of that script.",
            )
        case "run_by_import":
            return (
                "Ran inside another script",
                "Another script imports it, so it ran as part of that script.",
            )
        case "errored":
            return "Stopped with an error", (
                f"<code>{_repro_cell(err)}</code>"
                if err
                else "The script stopped with an error (see its output below)."
            )
        case "timed_out":
            downloading = (
                ", while it was still downloading."
                if "while a download was still arriving" in error
                else "."
            )
            return "Ran out of time", (
                f"It was still running after {as_character(timeout)} seconds, our time limit per "
                f"script{downloading}"
            )
        case "download_too_large":
            stopped = (
                f" ({_repro_cell(error.removeprefix('stopped: '))})"
                if error.startswith("stopped: ")
                else ""
            )
            return "Stopped", f"It downloaded more than our limits allow{stopped}."
        case "dependency_unavailable":
            if pkg in source_unknown:
                why = (
                    "is not on CRAN or Bioconductor, and the code does not say where it comes from"
                )
            elif install_missing:
                why = "we could not install"
            else:
                why = "is not installed on our server (we do not install packages)"
            named = _repro_cell(pkg) if pkg else "(see its output below)"
            return "Could not run on our server", (
                f"It needs the {lang} package {named}, which {why}. "
                "This is not an error in the script."
            )
        case "skipped_missing_inputs":
            one = len(inputs) == 1
            listed = _repro_cell("; ".join(input_reason(f) for f in inputs))
            return "Not run", (
                f"It reads {'a file' if one else 'files'} that {'is' if one else 'are'} not "
                f"available: {listed}."
            )
        case "skipped_time_limit":
            return (
                "Not run",
                "The time for all scripts together had run out before it could start or finish.",
            )
        case "not_parsed" | "static_parse_error":
            return "Not run", f"{lang} cannot read the script: it contains a syntax error."
        case "python2" | "static_python2":
            return "Not run", (
                "It is written for Python 2, which has been unsupported since 2020. We run "
                "Python 3, and do not convert code, since that can change results."
            )
        case "static_unplaceable":
            return "Not run", "We could not work out when to run it, relative to the other scripts."
        case "jags_model" | "static_jags_model":
            return "Not run", "It is a JAGS model, which another script reads."
        case "not_r_content" | "static_not_r_content":
            return "Not run", "It is not R code that can run on its own."
        case "defines_functions" | "static_defines_functions":
            return "Not run", (
                f"It only defines functions, which {_repro_cell(used_by)} uses."
                if used_by is not None
                else "It only defines functions, and no script in the deposit uses them."
            )
        case "docker_failed":
            return "Not run", "Docker failed on our side, so this says nothing about the script."
        case "not_downloaded":
            return "Not tried", (
                "It needs a file that the deposit holds but that we did not download, such as "
                "one over our size limit, so this says nothing about the script."
            )
        case "out_of_memory":
            limit = _repro_memory_limit_text(memory_gb=memory_gb)
            return "Ran out of memory", (
                "It needed more memory than it could use here."
                if limit is None
                else f"It needed more than the {limit} of memory it could use here."
            )
        case "needs_internet":
            return "Stopped", (
                "It needs the internet, which is off while we run code."
                if net is None
                else "It could not reach what it asked for on the internet."
            )
        case "needs_a_person" | "static_needs_a_person":
            return "Stopped", (
                "It asks a person to choose a file or answer a question, starts an app, or is a "
                "program that needs its arguments."
            )
        case "not_analysis" | "static_not_analysis":
            return "Not run", _repro_cell(not_analysis_why)
        case "not_read" | "static_not_read":
            return "Not read", _repro_cell(
                error_message if error_message is not None else "metacheck could not read the file."
            )
        case "empty" | "static_empty":
            return "Not run", "The file is empty."
        case _:
            return "Not run", ""


def _repro_checklist_section(
    table: pd.DataFrame | None,
    run_results: pd.DataFrame | None,
    missing_inputs: pd.DataFrame | None = None,
    n_dup: float = 0,
    n_tests: float | None = None,
    n_found: float | None = None,
    sandbox: str = "docker",
    timeout: float = 600,
    install_missing: bool = False,
    recorded_note: str | None = None,
    net: Mapping[str, Any] | None = None,
    source_unknown: Sequence[str] = (),
    install_results: pd.DataFrame | None = None,
    versions_recorded: bool = False,
    *,
    memory_gb: float | None = None,
) -> str | None:
    """Port of R/reproducibility_check_section.R::.repro_checklist_section().

    The section, as R Markdown, in one string (blocks separated by a blank line). *table*
    is the module's per-script table, *run_results* its per-script run results,
    *missing_inputs* the rows of ``repro_missing_inputs()`` for both languages, *n_dup*
    the number of identical copies that were not run again, *n_tests*/*n_found* the
    comparison with the paper (``None`` when there was none), *recorded_note* a sentence
    on the recorded package versions used (see ``.repro_recorded_summary()``), *net* the
    limits of the internet during the run (``max_file``, ``max_total``; ``None`` when it
    was off), *source_unknown* the packages found nowhere (see
    :func:`~metacheck.repro.outcomes._repro_source_unknown`), *install_results* the
    install of the packages and *versions_recorded* whether the deposit records package
    versions, both for what would make the code run (see :func:`_repro_advice`).
    ``None`` when no script was run.
    """
    if table is None or len(table) == 0 or run_results is None or len(run_results) == 0:
        return None
    total = len(table)
    run_order = table["run_order"].tolist() if "run_order" in table.columns else [None] * total
    first = [(is_missing(o), 0 if is_missing(o) else o) for o in run_order]
    tbl = table.iloc[sorted(range(total), key=first.__getitem__)].reset_index(drop=True)
    n = len(tbl)
    name = _strs(tbl, "file_name", n)
    is_py = [bool(grepl(r"\.(py|ipynb)$", f, ignore_case=True)) for f in name]
    position: dict[str | None, int] = {}
    for j, fn in enumerate(_strs(run_results, "file_name", len(run_results))):
        position.setdefault(fn, j)
    # the run results of each script (a script that was not run has a row of NA)
    rr = run_results.reset_index(drop=True).reindex([position.get(f, -1) for f in name])
    rr = rr.reset_index(drop=True)
    rr_outcome = _strs(rr, "outcome", n)
    tbl_outcome = _strs(tbl, "outcome", n)
    outcome = [r if r is not None else t for r, t in zip(rr_outcome, tbl_outcome, strict=True)]
    # a file that was not handed to R or Python: why, from the static check
    not_runnable = _strs(tbl, "not_runnable_reason", n)
    for i in range(n):
        if outcome[i] is None:
            outcome[i] = f"static_{'NA' if not_runnable[i] is None else not_runnable[i]}"
    oc = [o or "" for o in outcome]
    stderr = _strs(rr, "stderr", n)
    error = _strs(rr, "error", n)

    # why an input file is not available, from repro_missing_inputs()
    mi = missing_inputs if missing_inputs is not None else pd.DataFrame({"basename": []})
    mi_base = [(b or "").lower() for b in _strs(mi, "basename", len(mi))]
    mi_status = _strs(mi, "status", len(mi_base))
    mi_similar = _strs(mi, "similar_to", len(mi_base))
    reasons = {
        "withheld_size": "too large for us to download",
        "in_archive": "only inside a .zip or .tar file",
        "in_repo_not_downloaded": "in the repository, but not downloaded",
    }

    def input_reason(f: str) -> str:
        if f.lower() not in mi_base:
            return f"{f} (not in the repository)"
        i = mi_base.index(f.lower())
        why = reasons.get(mi_status[i] or "", "not in the repository")
        similar = mi_similar[i]
        if similar:
            why = f"{why}, but {similar} is"
        return f"{f} ({why})"

    used_by = _strs(tbl, "used_by", n)
    # why a file is not part of the analysis (see .repro_software_kind())
    not_analysis_why = _repro_not_analysis_why(_strs(tbl, "not_analysis_why", n))
    unresolved = _strs(tbl, "unresolved_inputs", n)
    error_message = _strs(tbl, "error_message", n)

    # one row per script: its result and what happened, in run order
    rows = []
    for i in range(n):
        lang = "Python" if is_py[i] else "R"
        err = _repro_error_line(stderr[i], python=is_py[i]) or _repro_error_line(
            error[i], python=is_py[i]
        )
        hit = regextract(_PKG_PAT, stderr[i] or "", perl=True)
        pkg = str(sub(r".*['\"‘]([^'\"’]+)['\"’].*", r"\1", hit)) if hit else ""
        inputs = (
            [f for f in strsplit(unresolved[i], ", ", fixed=True) if f] if unresolved[i] else []
        )
        result, happened = _repro_script_result(
            oc[i],
            lang=lang,
            err=err,
            pkg=pkg,
            inputs=inputs,
            error=error[i],
            timeout=timeout,
            used_by=used_by[i],
            not_analysis_why=not_analysis_why[i],
            source_unknown=source_unknown,
            install_missing=install_missing,
            net=net,
            input_reason=input_reason,
            memory_gb=memory_gb,
            error_message=error_message[i],
        )
        rows.append(f"| {_repro_cell(name[i])} | {result} | {happened} |")

    # the opening sentences: what was run, and how
    counted = [o not in _NOT_COUNTED for o in oc]
    n_r = sum(c and not py for c, py in zip(counted, is_py, strict=True))
    n_py = sum(c and py for c, py in zip(counted, is_py, strict=True))
    scripts = " and ".join(
        [
            *([f"{n_r:d} R script{plural(n_r)}"] if n_r else []),
            *([f"{n_py:d} Python script{plural(n_py)}"] if n_py else []),
        ]
    )
    how = (
        "each in an isolated container"
        if sandbox == "docker"
        else "each in a separate R or Python session"
    )
    limit_minutes = r_round(timeout / 60, 1)
    intro = (
        f"We ran the paper's {scripts} in the order they depend on each other, {how}, for at "
        f"most {format_num(limit_minutes, 7)} minute{plural(limit_minutes)} per script."
        + ("" if install_missing else " We did not install packages the scripts need.")
    )
    if net is not None:
        intro += " " + _repro_internet_text(net)
    if recorded_note is not None:
        intro += " " + recorded_note
    if n_dup > 0:
        n_copies = int(n_dup)
        intro += (
            f" {n_copies:d} identical cop{plural(n_dup, 'y', 'ies')} of these scripts, which "
            f"other links in the paper lead to, {plural(n_dup, 'was', 'were')} not run again."
        )

    # the headline: how many ran to the end, and what happened to the others
    counted_outcomes = [o for o, c in zip(oc, counted, strict=True) if c]
    n_counted = len(counted_outcomes)
    n_ok = sum(o == "ran_ok" for o in counted_outcomes)
    by_label: dict[str, int] = {}
    for group, label in _HEADLINE_GROUPS.items():
        k = sum(o == group for o in counted_outcomes)
        if k:
            by_label[label] = by_label.get(label, 0) + k
    parts = [f"{k:d} {label}" for label, k in by_label.items()]
    n_not_run = n_counted - n_ok - sum(o in _HEADLINE_GROUPS for o in counted_outcomes)
    if n_not_run:
        parts.append(f"{n_not_run:d} {plural(n_not_run, 'was', 'were')} not run")
    headline = (
        f"**{n_ok:d} of {n_counted:d} script{plural(n_counted)} ran to the end**"
        + (": " + ", ".join(parts) if parts else "")
        + "."
    )
    n_nd = sum(o == "not_downloaded" for o in oc)
    if n_nd:
        headline += (
            f" {n_nd:d} more {plural(n_nd, 'script was', 'scripts were')} not tried, because "
            f"{plural(n_nd, 'it needs', 'they need')} a file that we did not download."
        )

    # problems in the files the table does not show: setwd() / os.chdir()
    problems = []
    setwd_removed = tbl["setwd_removed"].tolist() if "setwd_removed" in tbl.columns else []
    for i, value in enumerate(setwd_removed):
        if (as_int(value) or 0) > 0:
            fun = "os.chdir()" if is_py[i] else "setwd()"
            problems.append(
                f"- {_repro_cell(name[i])} calls {fun}, which ties the script to a folder on the "
                "author's computer. We replaced the call with one that does nothing so the "
                "script could run; it should be removed from the script."
            )
    # file names spelled otherwise in the code than in the deposit
    for i, fixed in enumerate(_strs(tbl, "names_corrected", n)):
        if fixed is not None:
            problems.append(
                f"- {_repro_cell(name[i])} reads files under another name than the deposit holds "
                f"({_repro_cell(fixed)}). We corrected the names so the script could run; they "
                "should be changed in the script."
            )
    # notebook commands left out to run the code
    for i, command in enumerate(_strs(tbl, "commands_removed", n)):
        if command is not None:
            problems.append(
                f"- {_repro_cell(name[i])} holds notebook commands that are not Python "
                f"({_repro_cell(command)}). We left them out so the code could run; what they "
                "install or prepare was not done."
            )

    # the comparison with the paper
    found = 0 if n_found is None or is_missing(n_found) else int(n_found)
    if n_tests is None or is_missing(n_tests) or n_tests == 0:
        comparison = (
            "We found no statistical tests in the paper to compare with the output of the code."
        )
    else:
        comparison = (
            f"Of the {int(n_tests):d} statistical test{plural(n_tests)} reported in the paper, "
            f"{found:d} {plural(found, 'was', 'were')} found in the output of the code."
            + (
                " No script ran to the end, so most results were never produced."
                if n_ok == 0
                else ""
            )
        )

    # what the authors can change so the code runs, one line per cause (#575)
    advice, _ = _repro_advice(
        tbl,
        rr,
        outcome,
        mi,
        install_results,
        versions_recorded,
        timeout=timeout,
        memory_gb=memory_gb,
    )

    out = [
        intro,
        headline,
        "\n".join(["| Script | Result | What happened |", "|---|---|---|", *rows]),
    ]
    if advice:
        out += [
            "### How to make your code run on another computer",
            "We ran your code on a clean computer, the way a reader or reviewer would. Below is "
            "each problem we found, the scripts it affects, and how to fix it. Fix them in the "
            "order given: a problem near the top can cause the ones below it.",
            *advice,
        ]
    if problems:
        out += ["### Other problems in the files", "\n".join(problems)]
    out += ["### Comparison with the paper", comparison]
    return "\n\n".join(out)
