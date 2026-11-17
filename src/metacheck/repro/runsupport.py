"""What runs and what is only used, for the reproducibility check.

Port of the helpers of ``R/reproducibility_check.R`` (and ``R/reproducibility_check_docker.R``)
that decide which scripts are run on their own: a file that only defines functions is used
by the scripts that call it, not run (:func:`_repro_function_names`,
:func:`_repro_function_users`); a script that another script sources runs inside that one
(:func:`_repro_run_by_source`); the second run adds ``library()`` calls for the packages the
other scripts attach (:func:`_repro_attached_pkgs`, :func:`_repro_missing_fn_pkgs`); and
what is reported as not run (:func:`_repro_not_run_report`). Also how a long output is cut
(:func:`_repro_output_head`, :func:`_repro_output_cut_note`), the limits on what scripts
download (:func:`_repro_download_limits`), the repository's own Python modules a script
imports (:func:`_repro_py_import_links`, :func:`_repro_py_local_closure`) and the outcome of
a pass after the first (:func:`_repro_later_pass`).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

from metacheck._r.base import plural
from metacheck._r.regex import grepl
from metacheck._values import is_missing
from metacheck.report.blocks import ReportTable, scroll_table
from metacheck.repro.core import (
    _basename,
    _chr_list,
    _file_path_sans_ext,
    _norm_base,
    _r_call_parts,
    _r_chr,
    _r_is_true,
    _r_string,
    _repro_find_export_pkg,
    _rscript,
    _run_r,
    _setdiff,
    _unique,
)

__all__: list[str] = []

# ---------------------------------------------------------------------------
# files that only define functions
# ---------------------------------------------------------------------------

#: What an R file may do besides defining functions and still count as only defining them.
_SETUP_CALLS = frozenset(
    {
        "library",
        "require",
        "requireNamespace",
        "suppressPackageStartupMessages",
        "setGeneric",
        "setMethod",
        "setClass",
        "setValidity",
        "setRefClass",
    }
)
_ASSIGN_CALLS = frozenset({"<-", "=", "<<-"})


def _is_atomic(x: Any) -> bool:
    """R ``is.atomic()`` of a parsed constant (``NULL`` is not atomic since R 4.4)."""
    from metacheck.codecheck._rparse import Const

    return isinstance(x, Const) and x.kind != "NULL"


def _repro_function_names(code_text: Sequence[str | None] | str | None) -> list[str]:
    """Port of ``R/reproducibility_check.R::.repro_function_names()``.

    The functions an R file defines, when defining things is all it does: apart from
    functions, only ``library()`` calls, constants and the like. Empty otherwise, also for
    a file that does not parse. Such a file is not an analysis: run on its own it
    "succeeds" without doing anything. A function counts when a script uses it (#568).
    """
    from metacheck.codecheck._rparse import Const, Lang, RParseError, Sym, parse_exprs

    try:
        exprs = parse_exprs(_chr_list(code_text))
    except (RParseError, RecursionError):
        return []
    if not exprs:
        return []

    def literal(x: Any) -> bool:
        if _is_atomic(x):
            return True
        return (
            isinstance(x, Lang)
            and isinstance(x.fun, Sym)
            and x.fun.name == "c"
            and all(literal(a) for a in _r_call_parts(x)[1:])
        )

    defs: list[str] = []
    for e in exprs:
        if _is_atomic(e):
            continue
        if not isinstance(e, Lang):
            return []
        fun = e.fun
        if (
            isinstance(fun, Lang)
            and isinstance(fun.fun, Sym)
            and fun.fun.name == "::"
            and [a[1].name for a in fun.args if isinstance(a[1], Sym)]
            == ["utils", "globalVariables"]
        ):
            continue
        if not isinstance(fun, Sym):
            return []
        if fun.name in _SETUP_CALLS:
            continue
        if fun.name not in _ASSIGN_CALLS or len(e.args) != 2:
            return []
        target, value = e.args[0][1], e.args[1][1]
        if isinstance(target, Sym):
            name = target.name
        elif isinstance(target, Const) and target.kind == "character" and not target.na:
            name = str(target.value)
        else:
            return []
        if isinstance(value, Lang) and value.fun is Sym("function"):
            defs.append(name)
        elif not literal(value):
            return []
    return defs


def _repro_function_users(
    fn: str,
    functions: Sequence[str | None] | str | None,
    code_text_list: Mapping[str, Any],
    sources: Mapping[str, Any] | None = None,
) -> list[str]:
    """Port of ``R/reproducibility_check.R::.repro_function_users()``.

    The scripts that use a file's functions: calling one by name, or sourcing the file.
    *code_text_list* maps each script's file name to its lines; *sources* maps a script to
    the lower-case base names of the files it sources.
    """
    funs = [f for f in _chr_list(functions) if f is not None]
    others = [n for n in code_text_list if n != fn]
    if not funs or not others:
        return []
    called = "(^|[^A-Za-z0-9._])(" + "|".join(f.replace(".", r"\.") for f in funs) + r")\s*\("
    base = _norm_base(fn)
    sourced = sources or {}
    return [
        o
        for o in others
        if base in _chr_list(sourced.get(o))
        or any(grepl(called, [t for t in _chr_list(code_text_list[o]) if t is not None], perl=True))
    ]


# ---------------------------------------------------------------------------
# scripts that run inside another script
# ---------------------------------------------------------------------------


def _repro_run_by_source(
    order_tbl: pd.DataFrame, can_run: Mapping[str, Any] | pd.Series
) -> dict[str, list[str]]:
    """Port of ``R/reproducibility_check.R::.repro_run_by_source()``.

    Which scripts run inside another script, through ``source()``. When ``main.R`` loads
    its packages and then runs ``a.R`` with ``source()``, ``a.R`` only works inside
    ``main.R``: run on its own, in a fresh R process, it lacks the packages and objects
    ``main.R`` set up first (the most common symptom is ``could not find function
    "%>%"``). Such a script is therefore not run on its own; it runs as part of the
    script that sources it.

    A script counts as run through ``source()`` only when a chain of ``source()`` calls
    leads to it from a script that IS run on its own (it parses, its inputs are
    available, and nothing sources it). So two scripts that source each other, or a
    script sourced only by a script that cannot run, are still run on their own.

    *order_tbl* is the :func:`~metacheck.repro.core.repro_run_order` output (its
    ``attrs["source_edges"]``); *can_run* maps each script (by ``file_name``) to whether
    it parses and has its inputs, i.e. would be run at all. Returns, for each script run
    through ``source()``, the script(s) that source it; empty when there are none.
    """
    edges = order_tbl.attrs.get("source_edges")
    if edges is None or len(edges) == 0:
        return {}
    pairs = [
        (a, b)
        for a, b in zip(_chr_list(edges["from"]), _chr_list(edges["to"]), strict=True)
        if a != b
    ]
    flags = (
        dict(zip(can_run.index, can_run.values, strict=True))
        if isinstance(can_run, pd.Series)
        else dict(can_run)
    )
    runs = [n for n, v in flags.items() if _r_is_true(v)]
    runnable = set(runs)
    sourced = _unique(a for a, _ in pairs)
    # Start from the scripts that run on their own and follow source() calls down:
    # everything reached runs inside one of them.
    reached: list[str | None] = []
    frontier: list[str | None] = _setdiff(runs, sourced)
    while frontier:
        front = set(frontier)
        nxt = _setdiff((a for a, b in pairs if b in front), reached)
        # A sourced script that does not parse, or lacks its inputs, keeps that outcome of
        # its own rather than being hidden inside the sourcing script.
        nxt = [n for n in nxt if n in runnable]
        reached.extend(nxt)
        frontier = nxt
    allowed = set(reached) | runnable
    return {str(f): _unique(b for a, b in pairs if a == f and b in allowed) for f in reached}


# ---------------------------------------------------------------------------
# library() calls for the second run
# ---------------------------------------------------------------------------

#: Packages that export ``%>%``, for when the package cannot be checked because it is not
#: installed on this computer (a Docker run installs packages in the container only).
#: "tidyverse" itself does not export it, but attaches dplyr, which does.
_REPRO_PIPE_PKGS = ["magrittr", "dplyr", "tidyr", "purrr", "stringr", "tibble", "tidyverse"]


def _repro_attached_pkgs(code_text: Sequence[str | None] | str | None) -> list[str]:
    """Port of ``R/reproducibility_check.R::.repro_attached_pkgs()``.

    Packages a script attaches itself (``library()``, ``require()``, ``pacman::p_load()``),
    in the order it attaches them: not packages it only uses with ``pkg::``.
    """
    from metacheck.codecheck.core import code_library_names

    try:
        libs = code_library_names([t for t in _chr_list(code_text) if t is not None], "R")
    except Exception:  # R: tryCatch(error = NULL)
        return []
    if len(libs) == 0:
        return []
    return _unique(
        p
        for p, s in zip(_chr_list(libs["package"]), _chr_list(libs["source"]), strict=True)
        if s != "namespace"
    )


def _repro_r_exporters(name: str, pkgs: Sequence[str]) -> set[str]:
    """The packages among *pkgs* that are installed (for R) and export *name*.

    Package namespaces only exist in R, so ``Rscript`` is asked; without R none is
    installed.
    """
    pkgs = list(pkgs)
    if not pkgs or _rscript() is None:
        return set()
    code = (
        f"name <- {_r_string(name)}\n"
        f"for (pkg in {_r_chr(pkgs)}) {{\n"
        "  if (!requireNamespace(pkg, quietly = TRUE)) next\n"
        "  exports <- tryCatch(getNamespaceExports(asNamespace(pkg)),\n"
        "                      error = function(e) character(0))\n"
        '  if (name %in% exports) cat(pkg, "\\n", sep = "")\n'
        "}\n"
    )
    try:
        res = _run_r(code, timeout=300)
    except (OSError, ValueError):
        return set()
    except Exception:  # a timeout or another failure of the subprocess: nothing is known
        return set()
    if res is None or res.returncode != 0:
        return set()
    return {s for s in res.stdout.splitlines() if s}


def _repro_missing_fn_pkgs(
    name: str,
    user: str,
    code_text_list: Mapping[str, Any],
    candidates: Sequence[str | None] | None,
) -> list[str]:
    """Port of ``R/reproducibility_check.R::.repro_missing_fn_pkgs()``.

    Which ``library()`` calls to add for a missing function. The module's corrective
    re-run calls this for a script that failed with ``could not find function "X"`` when
    no script of the paper defines ``X``. In order:

    1. When the paper's OTHER scripts attach a package that provides ``X``, all the
       packages those scripts attach are returned, in the order they attach them. Such a
       script was written to run after them, in the same session (typically with
       ``library(tidyverse)`` in a script that runs first), and usually needs more than
       the one function that failed first: loading only magrittr for ``%>%`` would make
       the script fail one line later, on the first dplyr function.
    2. Otherwise, the one package among *candidates* that exports ``X``
       (:func:`~metacheck.repro.core._repro_find_export_pkg`), as before.
    3. Otherwise, for ``%>%`` when no script of the paper attaches a package that provides
       it, ``"magrittr"``.

    A package provides ``X`` when it is installed here and exports ``X``, or, for ``%>%``,
    when it is one of the pipe packages, or it is ``"tidyverse"``. Returns the package
    names to attach (empty when none).
    """
    known: dict[str, bool] = {}

    def provides_all(pkgs: Sequence[str]) -> list[bool]:
        todo = [
            p
            for p in _unique(pkgs)
            if p not in known
            and not (p == "tidyverse" or (name == "%>%" and p in _REPRO_PIPE_PKGS))
        ]
        found = _repro_r_exporters(name, todo)
        for p in todo:
            known[p] = p in found
        return [
            p == "tidyverse" or (name == "%>%" and p in _REPRO_PIPE_PKGS) or known[p] for p in pkgs
        ]

    own = _repro_attached_pkgs(code_text_list.get(user, []))
    others = _unique(
        p for f in code_text_list if f != user for p in _repro_attached_pkgs(code_text_list[f])
    )
    others = _setdiff(others, own)
    if others and any(provides_all(others)):
        return others

    pkg = _repro_find_export_pkg(name, list(candidates or []))
    if pkg is not None:
        return [pkg]

    if name == "%>%" and not any(provides_all(_unique([*own, *others]))):
        return ["magrittr"]
    return []


# ---------------------------------------------------------------------------
# the report: files not run on their own
# ---------------------------------------------------------------------------


def _repro_not_run_report(
    file_names: Sequence[str | None],
    kinds: Sequence[str | None],
    used_by: Sequence[str | None] | None = None,
    why: Sequence[str | None] | None = None,
    read_msg: Sequence[str | None] | None = None,
) -> list[str | ReportTable] | None:
    """Port of ``R/reproducibility_check.R::.repro_not_run_report()``.

    The report's section on files that are not run on their own; ``None`` when none.
    *why* gives, for a file that is not part of the analysis, the sentence of
    ``.repro_software_kind()`` (``None`` for a package's tests and ``setup.py``);
    *read_msg* gives, for a file that could not be read (``not_read``), why (#620). An
    ``empty`` file is named in a sentence.
    """
    from metacheck.repro.software import _repro_not_analysis_why

    names = _chr_list(file_names)
    kind = _chr_list(kinds)
    fn = [k == "defines_functions" for k in kind]
    ts = [k == "not_analysis" for k in kind]
    nr = [k == "not_read" for k in kind]
    em = [k == "empty" for k in kind]
    n_fn, n_ts, n_nr, n_em = sum(fn), sum(ts), sum(nr), sum(em)
    if not n_fn and not n_ts and not n_nr and not n_em:
        return None
    used = _chr_list(used_by) if used_by is not None else [None] * len(kind)
    out: list[str | ReportTable] = ["#### Files not run on their own"]
    if n_nr:
        out.append(
            f"{n_nr:d} file{plural(n_nr)} could not be read, so {plural(n_nr, 'it was', 'they were')}"
            f" not run and {plural(n_nr, 'needs', 'need')} to be checked again: this may be a "
            f"problem of the computer that ran the check, not of the {plural(n_nr, 'file', 'files')}."
        )
        msgs = _chr_list(read_msg) if read_msg is not None else [None] * len(kind)
        out.append(
            scroll_table(
                pd.DataFrame(
                    {
                        "File": pd.array(
                            [x for x, f in zip(names, nr, strict=True) if f], dtype="string"
                        ),
                        "Why": pd.array(
                            ["" if m is None else m for m, f in zip(msgs, nr, strict=True) if f],
                            dtype="string",
                        ),
                    }
                ),
                maxrows=10,
            )
        )
    if n_em:
        out.append(
            f"{n_em:d} file{plural(n_em)} {plural(n_em, 'is', 'are')} empty, so there is nothing "
            f"to run, and {plural(n_em, 'it is', 'they are')} not counted as "
            f"{plural(n_em, 'a script', 'scripts')}: "
            f"{', '.join(x or 'NA' for x, e in zip(names, em, strict=True) if e)}."
        )
    if n_fn:
        used_fn = [u for u, f in zip(used, fn, strict=True) if f]
        n_unused = sum(1 for u in used_fn if u is None)
        out.append(
            f"{n_fn:d} file{plural(n_fn)} only define{plural(n_fn, 's', '')} functions, so "
            f"running {plural(n_fn, 'it', 'them')} on {plural(n_fn, 'its', 'their')} own says "
            "nothing: a function counts when a script uses it. These files are not counted "
            f"as scripts. {n_unused:d} {plural(n_unused, 'is', 'are')} used by no script in "
            "the deposit."
        )
        out.append(
            scroll_table(
                pd.DataFrame(
                    {
                        "File": pd.array(
                            [x for x, f in zip(names, fn, strict=True) if f], dtype="string"
                        ),
                        "Used by": pd.array(
                            ["no script" if u is None else u for u in used_fn], dtype="string"
                        ),
                    }
                ),
                maxrows=10,
            )
        )
    if n_ts:
        reasons = (
            [w for w, t in zip(_chr_list(why), ts, strict=True) if t]
            if why is not None
            else [None] * n_ts
        )
        out.append(
            f"{n_ts:d} file{plural(n_ts)} {plural(n_ts, 'is', 'are')} not part of the analysis "
            "but of software (a package, its tests or documentation, a tool, a web "
            f"application), so {plural(n_ts, 'it was', 'they were')} not run or counted as "
            "scripts."
        )
        out.append(
            scroll_table(
                pd.DataFrame(
                    {
                        "File": pd.array(
                            [x for x, t in zip(names, ts, strict=True) if t], dtype="string"
                        ),
                        "Why": pd.array(_repro_not_analysis_why(reasons), dtype="string"),
                    }
                ),
                maxrows=10,
            )
        )
    return out


# ---------------------------------------------------------------------------
# long output
# ---------------------------------------------------------------------------


class _CutText(str):
    """A string cut by :func:`_repro_output_head`; ``n_lines`` is R's attribute of that name."""

    n_lines: int


class _CutLines(list[str | None]):
    """Lines cut by :func:`_repro_output_head`; ``n_lines`` is R's attribute of that name."""

    n_lines: int


def _max_output_lines() -> Any:
    from metacheck.utils import get_option

    return get_option("metacheck.repro_output_max_lines", 20000)


def _repro_output_head(
    txt: str | Sequence[str | None] | None, max_lines: float | None = None
) -> Any:
    """Port of ``R/reproducibility_check.R::.repro_output_head()``.

    The first lines of a script's console output, which are searched for statistical
    results. ``read_r_output()`` reads about 700 lines a second, so a script that printed
    a growing table in a loop (millions of lines in its 300 seconds) kept the check busy
    for most of an hour after the script had been stopped, and the job ran into its
    60-minute limit with no report (#523, item 2: OSF sq8y6). The option
    ``metacheck.repro_output_max_lines`` (default 20000) sets the number of lines, used
    when *max_lines* is ``None``. *txt* is one string or a sequence of lines; the result
    has the same form. When it was cut, it is a ``str`` or ``list`` subclass with the
    attribute ``n_lines`` (the number of lines before cutting), which R sets as
    ``attr(x, "n_lines")``; otherwise *txt* itself comes back.
    """
    limit = _max_output_lines() if max_lines is None else max_lines
    if txt is None:
        return txt
    if isinstance(txt, str):
        if txt == "" or not math.isfinite(limit):
            return txt
        count = txt.count("\n")
        if count == 0 or count < limit:
            return txt
        if limit < 1:
            raise ValueError("max_lines must be at least 1 to cut a text")
        pos = -1
        for _ in range(int(limit)):
            pos = txt.index("\n", pos + 1)
        cut = _CutText(txt[:pos])
        cut.n_lines = count + 1
        return cut
    lines = list(txt)
    if not lines or any(is_missing(x) for x in lines) or not math.isfinite(limit):
        return txt
    if len(lines) <= limit:
        return txt
    head = _CutLines(lines[: int(limit)])
    head.n_lines = len(lines)
    return head


def _big_mark(n: Any) -> str:
    """R ``format(n, big.mark = ",")`` of a whole number (``Inf`` stays ``Inf``)."""
    if isinstance(n, float) and not math.isfinite(n):
        return "Inf" if n > 0 else ("-Inf" if n < 0 else "NaN")
    return f"{int(n):,}"


def _repro_output_cut_note(
    cut: Mapping[str, Any] | None, max_lines: float | None = None
) -> str | None:
    """Port of ``R/reproducibility_check.R::.repro_output_cut_note()``.

    The report's note on scripts whose output was searched only in part: *cut* maps a
    script name to its number of output lines. ``None`` when nothing was cut.
    """
    if not cut:
        return None
    limit = _max_output_lines() if max_lines is None else max_lines
    scripts = ", ".join(f"`{name}` ({_big_mark(n)} lines)" for name, n in cut.items())
    return (
        f"*Statistical results were searched for in the first {_big_mark(limit)} lines "
        f"of the output of {scripts}, to keep the check within its time.*"
    )


# ---------------------------------------------------------------------------
# the repository's own Python modules
# ---------------------------------------------------------------------------


def _repro_py_import_links(
    imports: Mapping[str, Sequence[str | None]], paths: Sequence[str | None] | None
) -> dict[str, list[int]]:
    """Port of ``R/reproducibility_check.R::.repro_py_import_links()``.

    For each file, the positions of the repository's files it imports. *imports* maps a
    file name to the module names it imports (:func:`~metacheck.repro.software._repro_py_import_names`);
    *paths* are the files' places, in the same order. Positions count from 0, not 1.
    """
    fns = list(imports)
    places = _chr_list(paths)
    by_stem: dict[str, list[int]] = {}
    for i, fname in enumerate(fns):
        place = places[i] if i < len(places) else None
        p = fname if place is None or place == "" else place.replace("\\", "/")
        stem = _file_path_sans_ext(_basename(p))
        by_stem.setdefault(stem or "", []).append(i)
    return {
        fname: _unique(
            i for nm in _unique(_chr_list(names)) if nm in by_stem for i in by_stem[nm or ""]
        )
        for fname, names in imports.items()
    }


def _repro_py_local_closure(
    imports: Mapping[str, Sequence[str | None]],
    paths: Sequence[str | None] | None,
    start: str | Sequence[str | None],
    links: Mapping[str, Sequence[int]] | Sequence[Sequence[int]] | None = None,
) -> list[str]:
    """Port of ``R/reproducibility_check.R::.repro_py_local_closure()``.

    The repository's own files a Python file imports, and the files those import in turn:
    *start* is a file name of *imports*, *paths* their places. The folders a script needs
    on Python's search path follow from all their imports: 10.1111/2041-210X.14007's
    ``scripts/run.py`` imports ``run_NNs``, which imports the package ``fluxrgnn`` at the
    top of the repository (#568). *imports* is
    :func:`~metacheck.repro.software._repro_py_import_names` of all files, read once for
    all scripts; *links*, from :func:`_repro_py_import_links`, can be passed to reuse it
    too. Returns the file names reached, without the start.
    """
    fns = list(imports)
    link_list = (
        list(links.values())
        if isinstance(links, Mapping)
        else list(links)
        if links is not None
        else list(_repro_py_import_links(imports, paths).values())
    )
    starts = _chr_list(start)
    seen = [False] * len(fns)
    todo = [fns.index(s) for s in starts if s in fns]
    for i in todo:
        seen[i] = True
    while todo:
        nxt = _unique(j for i in todo for j in link_list[i])
        nxt = [j for j in nxt if not seen[j]]
        for j in nxt:
            seen[j] = True
        todo = nxt
    return _setdiff((f for f, s in zip(fns, seen, strict=True) if s), starts)


# ---------------------------------------------------------------------------
# downloads during a run, and later passes
# ---------------------------------------------------------------------------


def _repro_download_limits(
    internet: bool = False,
    max_file_size: float | None = 100,
    max_download_size: float | None = 500,
) -> dict[str, Any] | None:
    """Port of ``R/reproducibility_check_docker.R::.repro_download_limits()``.

    The limits on what scripts download when the internet is on during a run (#568, item
    21): the module's own ``max_file_size`` and ``max_download_size``, in MB, as for the
    files of the repository, and what the paper's scripts received so far, which all of
    them share (``used["bytes"]``, changed in place). ``None`` when the internet is off.
    ``None`` for a size means no limit (``inf``).
    """
    if not _r_is_true(internet):
        return None
    file_limit = math.inf if max_file_size is None else max_file_size
    total_limit = math.inf if max_download_size is None else max_download_size
    return {
        "max_file": file_limit * 1024**2,
        "max_total": total_limit * 1024**2,
        "used": {"bytes": 0},
    }


def _repro_later_pass(res: pd.DataFrame | None) -> pd.DataFrame | None:
    """Port of ``R/reproducibility_check.R::.repro_later_pass()``.

    A pass after the first runs scripts it changed (added lines, a new order, old
    defaults, a fetched or placed file). A script of such a pass that the time left for
    all scripts stopped (``error_type`` "deadline", set by the runners), or did not start,
    says nothing about the change: it is ``skipped_time_limit``, never the first pass's
    failure the change was meant to fix. A script got the last second of the paper and was
    reported as timed out (#581). In the first pass, such a script stays ``timed_out``: it
    is slow. Returns a changed copy.
    """
    if res is None or len(res) == 0:
        return res
    if not {"outcome", "error_type"} <= set(res.columns):
        return res
    cut = [
        o == "timed_out" and e == "deadline"
        for o, e in zip(_chr_list(res["outcome"]), _chr_list(res["error_type"]), strict=True)
    ]
    if not any(cut):
        return res
    out = res.copy()
    elapsed: list[Any] = out["elapsed"].tolist() if "elapsed" in out.columns else [None] * len(out)
    outcome: list[Any] = out["outcome"].tolist()
    error: list[Any] = out["error"].tolist() if "error" in out.columns else [None] * len(out)
    for i, c in enumerate(cut):
        if not c:
            continue
        outcome[i] = "skipped_time_limit"
        secs = "NA" if is_missing(elapsed[i]) else f"{math.ceil(float(elapsed[i])):d}"
        error[i] = f"stopped after {secs} s: the time for all scripts together had run out"
    out["outcome"] = pd.Series(outcome, index=out.index, dtype=res["outcome"].dtype)
    out["error"] = pd.Series(
        error, index=out.index, dtype=res["error"].dtype if "error" in res.columns else "string"
    )
    return out
