"""How a script's run is classified and counted.

Ports of the helpers of metacheck's ``R/reproducibility_check.R`` and
``R/reproducibility_check_docker.R`` that turn the output of a run into an outcome, a
reason, a count or a sentence of the report: the files a script could not open, the
scripts that start an app or a server, the errors that mean the code needs the
internet, a person, arguments or more memory, and the per-paper counts of
``summary_table``.

Where metacheck reads the option ``metacheck.docker_resource_limits`` (the memory each
script may use), the value is the keyword argument ``memory_gb`` here, ``None`` when no
limit is set.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

from metacheck._r import (
    as_character,
    format_num,
    grep,
    grepl,
    gsub,
    plural,
    r_round,
    r_sort_key,
    regexec,
    regextract_all,
    strsplit,
    trimws,
)
from metacheck._values import as_int, is_missing, is_true
from metacheck.codecheck._rparse import Lang, RParseError, Sym, parse_exprs
from metacheck.datacheck.files import _utf8_lines
from metacheck.report.blocks import ReportTable, scroll_table
from metacheck.repro.core import _basename, _unique

# ---------------------------------------------------------------------------
# the error a script stopped with
# ---------------------------------------------------------------------------


def _repro_error_line(stderr: str | None, python: bool = False, max_chars: int = 300) -> str:
    """Port of R/reproducibility_check_section.R::.repro_error_line().

    The error a script stopped with, from its error output: for R the lines from the
    first "Error" line up to R's own closing lines ("Calls:", "Backtrace:", "In
    addition:", "Execution halted"), for Python the last error line of the traceback.
    ``""`` when there is none.
    """
    if is_missing(stderr):
        return ""
    lines = [ln for ln in trimws(strsplit(stderr, "\r?\n")) if ln]
    if not lines:
        return ""
    if python:
        hit = grep(r"^[A-Za-z_.]*(Error|Exception)\b", lines)
        if not hit:
            return ""
        out = lines[max(hit)]
    else:
        starts = grep(r"^Error\b", lines)
        if not starts:
            return ""
        start = starts[0]
        ends = grep(
            "^(Calls:|Backtrace:|In addition:|Execution halted|"
            "Run `rlang::last_trace|Warning message)",
            lines,
        )
        later = [e for e in ends if e > start]
        end = later[0] - 1 if later else min(len(lines) - 1, start + 5)
        out = " ".join(lines[start : end + 1])
    out = str(gsub(r"\s+", " ", out))
    if len(out) > max_chars:
        out = out[: max_chars - 1] + "\u2026"
    return out


_NO_ERROR_MESSAGE = (
    "ran_ok",
    "run_by_source",
    "run_by_import",
    "jags_model",
    "not_r_content",
    "defines_functions",
    "not_analysis",
)


def _repro_error_message(
    outcome: Sequence[str | None],
    error: Sequence[str | None] | None,
    stderr: Sequence[str | None] | None = None,
    python: bool = False,
    max_chars: int = 300,
) -> list[str | None]:
    """Port of ``.repro_error_message()``: each script's failure, short, for the table (#615).

    So a reader of the table need not take it from the report text, whose wording changes:
    the error line of the script's output (as the report shows it, see
    :func:`_repro_error_line`), which names the package that is not there; for a script that
    needed the internet, with the address it asked for when the error line names none; for
    one that timed out, the time limit. Else the last line of the output. At most
    *max_chars* characters; ``None`` for a script that ran through or has no message.
    """
    n = len(outcome)
    errors = list(error) if error is not None else [None] * n
    stderrs = list(stderr) if stderr is not None else [None] * n

    def shorten(x: str) -> str:
        x = re.sub(r"\s+", " ", x.strip())
        return x[: max_chars - 1] + "\u2026" if len(x) > max_chars else x

    out: list[str | None] = []
    for i in range(n):
        o = outcome[i]
        if o is None or o in _NO_ERROR_MESSAGE:
            out.append(None)
            continue
        err, se = errors[i], stderrs[i]
        e = err if (err is not None and err.strip() != "") else se
        if e is None or e.strip() == "":
            out.append(None)
            continue
        # the time limit: error is the limit's own sentence ("timed out after 3600s")
        if o in ("timed_out", "skipped_time_limit"):
            out.append(shorten(e))
            continue
        line = _repro_error_line(e, python=python, max_chars=max_chars)
        if not line:
            line = _repro_error_line(e, python=not python, max_chars=max_chars)
        if not line:
            lines = [ln.strip() for ln in re.split(r"\r?\n", e)]
            lines = [
                ln
                for ln in lines
                if ln and not re.match(r"^(Execution halted|Calls:|Backtrace:)", ln)
            ]
            line = shorten(lines[-1]) if lines else ""
        if o == "needs_internet" and not re.search(r"https?://", line, re.I):
            m = re.search(r"https?://[^\s'\"<>)\]]+", f"{e} {se if se is not None else 'NA'}")
            if m:
                line = shorten(f"{line} ({m.group(0)})")
        out.append(line if line else None)
    return out


# ---------------------------------------------------------------------------
# what a script reads and starts
# ---------------------------------------------------------------------------


def _repro_unopened_files(msgs: str | Sequence[str | None], full: bool = False) -> list[str]:
    """Port of R/reproducibility_check.R::.repro_unopened_files().

    After a run: the names of the files scripts could not open, from their errors;
    with ``full=True`` the paths as the errors give them. Also sf's ``Cannot open
    "x.shp"`` and terra's ``file does not exist: x.tif`` (#575, item 2), and the curly
    quotes R uses in a UTF-8 locale: readxl's ``path does not exist: '~/...'``.
    """
    q = "['\"\u2018\u2019]"
    nq = "[^'\"\u2018\u2019\n]"
    pat = (
        "(?:cannot open (?:compressed )?file|No such file or directory:?|does not exist)"
        f"{nq}*{q}({nq}+){q}|"
        f"{q}({nq}+){q}:? No such file|"
        f"{q}({nq}+){q} does not exist|"
        f"Cannot open {q}({nq}+){q}|"
        "file does not exist: ([^\\s'\"]+)"
    )
    texts = [msgs] if isinstance(msgs, str) else [m for m in msgs if not is_missing(m)]
    out: list[str] = []
    for m in _utf8_lines([t for t in texts if t is not None]):
        if m is None:
            continue
        for found in regextract_all(pat, m, perl=True):
            groups = regexec(pat, found, perl=True)[1:]
            first = next((g for g in groups if g), None)
            if first is not None:
                out.append(first.replace("\\", "/"))
    return _unique(out if full else [_basename(p) or "" for p in out])


def _repro_starts_app(code_text: Sequence[str | None]) -> bool:
    """Port of R/reproducibility_check.R::.repro_starts_app().

    Whether an R script starts an interactive app at its top level (``runApp()`` and
    the like): it would wait for a person until the time limit, with nothing to show
    for it. A script that does not parse starts none.
    """
    try:
        exprs = parse_exprs(code_text)
    except RParseError:
        return False
    apps = {
        "runApp",
        "shinyApp",
        "launch_shinystan",
        "runExample",
        "runGitHub",
        "runUrl",
        "shiny::runApp",
        "shiny::shinyApp",
        "shinystan::launch_shinystan",
    }
    return any(_call_name(e) in apps for e in exprs if isinstance(e, Lang))


def _call_name(call: Lang) -> str | None:
    """``deparse(call[[1]])`` of a call to a function or to ``pkg::function``."""
    fun = call.fun
    if isinstance(fun, Sym):
        return fun.name
    if (
        isinstance(fun, Lang)
        and isinstance(fun.fun, Sym)
        and fun.fun.name == "::"
        and len(fun.args) == 2
        and all(isinstance(a[1], Sym) for a in fun.args)
    ):
        return f"{fun.args[0][1].name}::{fun.args[1][1].name}"
    return None


def _repro_py_starts_server(code_text: Sequence[str | None]) -> bool:
    """Port of R/reproducibility_check.R::.repro_py_starts_server().

    Whether a Python script serves others: it waits for a connection until the time
    limit, with nothing to show for it (#568, item 22). ``tcpserver.py`` took 50
    minutes. A program with a window waits for a person the same way: in
    10.1111/1365-2435.14173 a Qt application waited 38 minutes, and 13 more scripts
    then had no time left (#568).
    """
    not_comment = [ln for ln in code_text if not grepl(r"^\s*#", ln)]
    pat = (
        r"\.serve_forever\s*\(|\bsocketserver\.|\bapp\.run\s*\(|\buvicorn\.run\s*\(|"
        r"\.listen\s*\(\s*\d*\s*\).*$|\bwhile True:.*\.accept\(|"
        r"\bQ(?:Gui)?Application\s*\(|\.mainloop\s*\(\s*\)|\.MainLoop\s*\(\s*\)|"
        r"\bpygame\.display\.set_mode\s*\("
    )
    return any(grepl(pat, not_comment, perl=True))


def _repro_missing_program(msg: Sequence[str | None]) -> list[str | None]:
    """Port of R/reproducibility_check.R::.repro_missing_program().

    The program a script could not start, from its output, or ``None``: R's
    ``system()`` answers ``sh: 1: mark: not found`` (RMark, 10.1111/1365-2656.13583),
    Python's subprocess a ``FileNotFoundError`` that names the program, ``'rsync'`` in
    10.1002/pan3.10551 (#575, item 7).
    """
    pats = [
        r"sh: [0-9]+: ([^:\n]+): not found",
        r"\n?([^\s:]+): command not found",
        r"Unable to find or run ([^\s]+)",
    ]
    out: list[str | None] = []
    for m in msg:
        found: str | None = None
        if not is_missing(m) and m:
            for p in pats:
                hit = regexec(p, m, perl=True)
                if len(hit) > 1:
                    found = trimws(hit[1])
                    break
            else:
                if "subprocess" in m:
                    hit = regexec(
                        r"FileNotFoundError: \[Errno 2\] No such file or directory: '([^'/]+)'",
                        m,
                    )
                    if len(hit) > 1:
                        found = hit[1]
        out.append(found)
    return out


# ---------------------------------------------------------------------------
# errors that are not the code's
# ---------------------------------------------------------------------------


def _as_flags(x: Any) -> list[bool]:
    return [bool(v) for v in x]


def _repro_needs_internet_error(txt: Sequence[str | None]) -> list[bool]:
    """Port of R/reproducibility_check.R::.repro_needs_internet_error().

    Whether a run's error shows that the code needs the internet, which is off during
    a Docker run, since this runs code nobody has checked (#568, item 21). A package
    that keeps data it downloads once, as tidyhydat keeps its HYDAT database, says so
    when the data is not there yet (``Run download_hydat()``).
    """
    pat = (
        "cannot open URL|[Cc]ould not resolve host|Couldn't resolve host|"
        "Temporary failure in name resolution|"
        "Name or service not known|NameResolutionError|Max retries exceeded|urlopen error|"
        "ConnectionError|LocalEntryNotFound|Could not reach|Failed to connect|"
        "Network is unreachable|"
        r"getaddrinfo failed|Timeout was reached|no internet|internet connection|"
        r"Run download_hydat\(\)|"
        # GDAL reading from the web (sf, terra): Cannot open "/vsizip//vsicurl/https://..." (14079)
        "/vsicurl/"
    )
    return _as_flags(grepl(pat, list(txt), perl=True))


def _repro_needs_person_error(txt: Sequence[str | None]) -> list[bool]:
    """Port of R/reproducibility_check.R::.repro_needs_person_error().

    Whether a run's error shows that the script waited for a person: a file to choose,
    a question, a window, the clipboard (``read.table("clipboard")`` is what a person
    copied, 10.1111/1365-2656.14113).
    """
    pat = (
        "file choice cancelled|invalid 'description' argument|"
        r"menu\(\) cannot be used non-interactively|"
        r"could not find function \"(choose\.files|choose\.dir)\"|"
        "RStudio not running|tcltk|EOFError: EOF when reading a line|"
        r"no display name and no \$DISPLAY|couldn't connect to display|"
        "for the clipboard must be|clipboard cannot be opened"
    )
    return _as_flags(grepl(pat, list(txt), perl=True))


def _repro_needs_arguments(
    code: Sequence[str | Sequence[str | None]],
    txt: str | Sequence[str | None] | None,
    lang: str = "R",
    out: str | Sequence[str | None] | None = None,
) -> list[bool]:
    """Port of R/reproducibility_check.R::.repro_needs_arguments().

    Whether a script is a program that needs arguments from the person running it, and
    stopped for want of them: its code reads command-line arguments and its error
    points at them. In R the call that failed names ``args[``/``argv[`` or
    ``commandArgs()``; in Python argparse says which are required, or the failing line
    (which Python prints) reads ``sys.argv`` or ``parse_args()``. 53 failed scripts in
    8 papers of the corpus read arguments, as in 10.1111/2041-210X.13845 (PyRate) and
    10.1111/2041-210X.13721 (#568). *code* holds the scripts' lines, one entry per
    script.

    A program that prints how to use it, on its standard output (*out*) or in its
    error, also stopped for want of arguments: 45 tools of the Stacks pipeline of
    10.1111/1365-2656.13545 print ``Usage: ./extract_first_snp.py input_vcf output_vcf``
    when ``sys.argv`` is short, and stop without a message, and an R script with
    optparse prints its help before ``stop()`` (#578, item 6). In R, optparse,
    argparser and docopt read arguments too.
    """
    n = len(code)
    texts = _per_script(txt, n)
    outs = _per_script(out, len(texts))
    python = lang == "Python"
    reads_pat = (
        "argparse|sys[.]argv|getopt|docopt|click[.](command|argument|option)"
        if python
        else "commandArgs[(]|optparse|OptionParser[(]|parse_args[(]|argparser::|arg_parser[(]|"
        "docopt[(]"
    )
    reads = [
        bool(grepl(reads_pat, s if isinstance(s, str) else "\n".join(map(str, s)), perl=True))
        for s in code
    ]
    if python:
        points = grepl(
            "the following arguments are required|: error: (argument|unrecognized arguments)|"
            r"Use '-h'|sys[.]argv\[|parse_args\(",
            texts,
            perl=True,
        )
    else:
        points = grepl("Error in [^\n:]*(\\bargs?\\[|\\bargv\\[|commandArgs)", texts, perl=True)
    usage = grepl(
        r"(?im)^\s*usage\s*:|Show this help message and exit",
        [f"{t} {o}" for t, o in zip(texts, outs, strict=False)],
        perl=True,
    )
    return [r and (p or u) for r, p, u in zip(reads, points, usage, strict=False)]


def _per_script(x: str | Sequence[str | None] | None, n: int) -> list[str]:
    """A text per script, ``""`` for a missing one (a single text is every script's)."""
    if x is None:
        return [""] * n
    if isinstance(x, str):
        return [x] * n
    return ["" if is_missing(v) else str(v) for v in x]


def _repro_docker_failed(txt: Sequence[str | None]) -> list[bool]:
    """Port of R/reproducibility_check_docker.R::.repro_docker_failed().

    Whether a run's message comes from Docker itself rather than from the script: a
    script cannot be judged when its container never started. While Docker Desktop was
    down, every script of a paper was recorded as a failing script (#568, item 27).
    """
    pat = (
        "Error response from daemon|Docker Desktop is unable to start|"
        "Cannot connect to the Docker daemon|failed to connect to the docker API|"
        "error during connect|dockerDesktopLinuxEngine|"
        # Docker lost the container while it ran: three papers' scripts at the same
        # second (#568)
        "error waiting for container"
    )
    return _as_flags(grepl(pat, list(txt), ignore_case=True))


# ---------------------------------------------------------------------------
# memory
# ---------------------------------------------------------------------------


def _repro_oom_text(status: Any, *, memory_gb: float | None = None) -> str | None:
    """Port of R/reproducibility_check_docker.R::.repro_oom_text().

    The note added to a script's error output when its container was stopped for using
    more memory than the limit: Docker gives the process the exit status 137, and the
    script ends without a message. In 10.1111/2041-210X.14359 nimble's C++ compiler
    went over 3 GB, and three scripts read as failures with no message and the reason
    "other" (#568). ``None`` when no memory limit (*memory_gb*) is set.
    """
    if as_int(status) != 137 or memory_gb is None:
        return None
    return (
        "\n[reproducibility_check] The script was stopped for using more than the memory "
        f"limit of {format_num(memory_gb, 7)} GB that each script has here; this says "
        "nothing about the code."
    )


def _repro_out_of_memory(txt: Sequence[str | None]) -> list[bool]:
    """Port of R/reproducibility_check_docker.R::.repro_out_of_memory().

    Whether a script's output shows that it, or a program it started, ran out of
    memory. A compiler that runs out is stopped on its own, and R carries on: in
    10.1111/1365-2435.14319 compiling a Stan model ended with ``g++: fatal error:
    Killed signal terminated program cc1plus``, and the script then failed on
    ``sink()`` (#578, item 8). Also R's own ``cannot allocate vector of size``, C++'s
    ``std::bad_alloc``, Python's ``MemoryError``, and the note of
    :func:`_repro_oom_text`.
    """
    pat = (
        "Killed signal terminated program|cannot allocate vector of size|std::bad_alloc|"
        r"(?m)^\s*MemoryError\b|was stopped for using more than the memory limit"
    )
    return _as_flags(grepl(pat, ["" if is_missing(t) else t for t in txt], perl=True))


def _repro_memory_limit_text(*, memory_gb: float | None = None) -> str | None:
    """Port of R/reproducibility_check_docker.R::.repro_memory_limit_text().

    The memory each script may use here, as text (``"4 GB"``), or ``None`` when no
    limit (*memory_gb*) is set.
    """
    return None if memory_gb is None else f"{format_num(memory_gb, 7)} GB"


def _repro_mb(nbytes: float) -> str:
    """Port of R/reproducibility_check_docker.R::.repro_mb().

    Bytes as megabytes with one decimal at most and a comma between thousands:
    ``format(round(bytes / 1024^2, 1), big.mark = ",")``.
    """
    text = format_num(r_round(nbytes / 1024**2, 1), 7)
    if "e" in text:
        return text
    whole, dot, frac = text.partition(".")
    return f"{int(whole):,}{dot}{frac}" if whole.lstrip("-").isdigit() else text


def _repro_timeout_text(timeout: float, watched: Mapping[str, Any] | None = None) -> str:
    """Port of R/reproducibility_check_docker.R::.repro_timeout_text().

    The error of a script that ran out of time, which names a download that was still
    coming in (``watched`` has ``arriving`` and ``received``, in bytes).
    """
    text = f"timed out after {as_character(r_round(timeout, 0))}s"
    if watched is not None and is_true(watched.get("arriving")):
        text += (
            f" while a download was still arriving ({_repro_mb(watched['received'])} MB received)"
        )
    return text


# ---------------------------------------------------------------------------
# the internet during a run
# ---------------------------------------------------------------------------


def _repro_internet_text(net: Mapping[str, Any]) -> str:
    """Port of R/reproducibility_check.R::.repro_internet_text().

    The report's sentence that scripts could use the internet, with its limits
    (``max_file`` and ``max_total``, in bytes; see ``.repro_download_limits()``), so a
    reader knows that what they downloaded may differ from what the authors had.
    """

    def lim(b: Any, what: str) -> str | None:
        if b is None or not math.isfinite(b):
            return None
        return f"{_repro_mb(b)} MB {what}"

    limits = [
        s
        for s in (
            lim(net.get("max_file"), "per file"),
            lim(net.get("max_total"), "for all scripts together"),
        )
        if s is not None
    ]
    up_to = ", up to " + " and ".join(limits) if limits else ""
    return (
        f"The scripts could use the internet{up_to}, "
        "so what they downloaded may differ from what the authors had."
    )


def _repro_needs_internet_text(files: Sequence[str], internet: bool = False) -> str:
    """Port of R/reproducibility_check.R::.repro_needs_internet_text().

    The report's sentence naming the scripts that needed the internet: with it off, or
    on but without reaching what they asked for.
    """
    n = len(files)
    why = "and could not reach what they asked for" if internet else "which is off during the run"
    return (
        f" **{n:d} script{plural(n)} need{plural(n, 's', '')} the internet**, "
        f"{why}: {', '.join(files)}. Keeping what they download in the deposit solves this."
    )


def _repro_download_stop_text(files: Sequence[str]) -> str:
    """Port of R/reproducibility_check.R::.repro_download_stop_text().

    The report's sentence naming the scripts stopped for downloading more than the
    limits allow.
    """
    n = len(files)
    return (
        f" **{n:d} script{plural(n)} {plural(n, 'was', 'were')} stopped for downloading "
        f"more than our limits allow**: {', '.join(files)}. "
        "Keeping what they download in the deposit solves this."
    )


# ---------------------------------------------------------------------------
# why a script did not run through
# ---------------------------------------------------------------------------

_FILE_PAT = (
    "cannot open the connection|cannot open file|No such file|FileNotFoundError|"
    "(?:[Ff]ile|[Pp]ath|[Dd]irectory|[Ff]older|['\"\u2018\u2019`])[^\n]{0,300}?does not exist|"
    "does not exist:? *['\"\u2018`/~.]|Cannot find the file|Could not find a root|"
    "cannot open compressed"
)

_OUTCOME_REASON = {
    "dependency_unavailable": "package",
    "not_parsed": "syntax",
    "python2": "syntax",
    "timed_out": "time",
    "skipped_time_limit": "time",
    "needs_a_person": "interactive",
    "needs_internet": "internet",
    "out_of_memory": "environment",  # #578, item 8
    # a download that was too large
    "download_too_large": "download",
    "skipped_missing_inputs": "file",
}


def _repro_failure_reason(
    outcome: Sequence[str | None], msg: Sequence[str | None]
) -> list[str | None]:
    """Port of R/reproducibility_check.R::.repro_failure_reason().

    Why a script did not run through, as one word, so reasons need not be recovered
    from messages afterwards (#568, item 24). ``None`` for what is not a failure of the
    script. *msg* is the output of each script, in the order of *outcome*.
    """
    return [
        _failure_reason_one(oc, m if isinstance(m, str) else "")
        for oc, m in zip(outcome, msg, strict=True)
    ]


def _failure_reason_one(outcome: str | None, full: str) -> str | None:
    reason = _OUTCOME_REASON.get(outcome) if outcome is not None else None
    # a download still arriving when the time ran out
    if outcome == "timed_out" and "while a download was still arriving" in full:
        reason = "download"
    if outcome != "errored":
        return reason

    def has(pat: str, text: str) -> bool:
        return bool(grepl(pat, text, perl=True))

    # stopped by the memory limit of the run (see _repro_oom_text()), before any
    # message the script's own output happens to contain
    if has("was stopped for using more than the memory limit", full):
        return "environment"
    # The reason comes from the error that stopped the script, not from a warning
    # before it: "object 'egg_hatching_date' not found" after a warning on
    # "TIFFOpen: C://your.file.directory/Figure1.tiff" was counted as a missing file
    # (10.1111/1365-2435.12953; #575, item 10). R's "cannot open the connection" names
    # no file, so the warnings, which do, count then; so does the whole output when
    # no error line is found (a crash, a time limit).
    err = _repro_error_line(full, max_chars=5000) or _repro_error_line(
        full, python=True, max_chars=5000
    )
    msg = full if not err or "cannot open the connection" in err else err
    # provided by Snakemake when it runs the script, so only there
    if has("object 'snakemake' not found|name 'snakemake' is not defined", msg):
        return "environment"
    if has(
        "there is no package called|ModuleNotFoundError|No module named|ImportError|"
        "package or namespace|Error: package ",
        msg,
    ):
        return "package"
    # "does not exist" only of a file: Stan's "variable does not exist" is not one
    # (10.1111/1365-2435.14436; #575, item 10). A file error after a program could not
    # be started is about the program (#575, item 7): "Output file does not exist.
    # Unable to find or run mark.exe"
    if has(_FILE_PAT, msg):
        return "environment" if _repro_missing_program([full])[0] is not None else "file"
    for pat, label in (
        ("object '[^']*' not found|could not find function|NameError|is not defined", "undefined"),
        ("SyntaxError|IndentationError", "syntax"),
        (
            "shared object|ImproperlyConfigured|cannot cache function|cannot open display|X11|"
            "can only print from a screen device",
            "environment",
        ),
        # R itself crashed, in a package's compiled code: rgeos, installed from the CRAN
        # Archive, crashes on every geometry against the GEOS of the image (#568)
        (
            "caught segfault|Segmentation fault|core dumped|caught illegal operation",
            "environment",
        ),
    ):
        if has(pat, msg):
            return label
    return "no_message" if not grepl(r"\S", msg) else "other"


def _repro_source_unknown(install_results: pd.DataFrame | None) -> list[str]:
    """Port of R/reproducibility_check.R::.repro_source_unknown().

    The packages the code loads that are on neither CRAN nor Bioconductor and that the
    install found nowhere else, since the code does not say where they come from
    (#389). An empty list when there are none.
    """
    if (
        install_results is None
        or len(install_results) == 0
        or not {"category", "installed", "package"} <= set(install_results.columns)
    ):
        return []
    rows = zip(
        install_results["package"].tolist(),
        install_results["installed"].tolist(),
        install_results["category"].tolist(),
        strict=True,
    )
    return _unique(
        pkg
        for pkg, installed, category in rows
        # a Python install result has no category (NA)
        if not is_missing(category)
        and category == "source_unknown"
        and not is_missing(installed)
        and not installed
    )


def _repro_source_unknown_text(pkgs: Sequence[str]) -> str | None:
    """Port of R/reproducibility_check.R::.repro_source_unknown_text().

    The report's sentence on the packages found nowhere (see
    :func:`_repro_source_unknown`), or ``None`` when there are none.
    """
    if not pkgs:
        return None
    n = len(pkgs)
    names = ", ".join(f"`{p}`" for p in pkgs)
    return (
        f"**{n:d} package{plural(n)} the code loads {plural(n, 'is', 'are')} not on CRAN or "
        "Bioconductor, and the code does not say where "
        f"{plural(n, 'it comes', 'they come')} from**: {names}. We also looked in the CRAN "
        "Archive, on R-universe and in a list of known places. Saying where to install "
        f"{plural(n, 'it', 'them')} from, such as with `remotes::install_github()` or in the "
        "README, solves this."
    )


#: Every reason :func:`_repro_failure_reason` gives, so each paper's row of
#: ``summary_table`` has the same columns, also for a reason none of its scripts had.
_REPRO_REASON_LEVELS = (
    "file",
    "package",
    "undefined",
    "syntax",
    "environment",
    "internet",
    "download",
    "interactive",
    "time",
    "no_message",
    "other",
)

#: The outcomes of files that are not scripts with a result of their own.
_NOT_SCRIPTS = frozenset(
    {
        "run_by_source",
        "run_by_import",
        "jags_model",
        "not_r_content",
        "defines_functions",
        "not_analysis",
        "not_downloaded",
    }
)


def _repro_run_counts(run_results: pd.DataFrame | None) -> pd.DataFrame:
    """Port of R/reproducibility_check.R::.repro_run_counts().

    Per-paper counts for ``summary_table`` (#568, item 24): the scripts with a result
    of their own, the files of software that were not run (not analyses, counted
    apart), and for each reason how many scripts did not run through for it. A sourced
    or imported file, a model or function file and a Docker failure on our side are
    not scripts with a result of their own, nor is a script that stopped on a file we
    did not download (counted apart as well). One row; ``NA`` when nothing was run.
    """
    cols = [
        "repro_scripts_n",
        "repro_not_analysis_n",
        "repro_not_downloaded_n",
        *(f"repro_fail_{r}" for r in _REPRO_REASON_LEVELS),
    ]
    if run_results is None or len(run_results) == 0 or "outcome" not in run_results.columns:
        return pd.DataFrame({c: pd.array([None], dtype="Int64") for c in cols})
    outcome = [None if is_missing(v) else v for v in run_results["outcome"].tolist()]
    if "reason" in run_results.columns:
        reason = [None if is_missing(v) else v for v in run_results["reason"].tolist()]
    else:
        # a missing error or output is no text (R's paste() would write "NA")
        err = _texts(run_results, "error")
        stderr = _texts(run_results, "stderr")
        reason = _repro_failure_reason(
            outcome, [f"{e} {s}" for e, s in zip(err, stderr, strict=True)]
        )
    counted = [
        oc is not None and oc not in _NOT_SCRIPTS and oc != "docker_failed" for oc in outcome
    ]
    fails = [
        sum(c and r == level for c, r in zip(counted, reason, strict=True))
        for level in _REPRO_REASON_LEVELS
    ]
    values = [
        sum(counted),
        sum(oc == "not_analysis" for oc in outcome),
        sum(oc == "not_downloaded" for oc in outcome),
        *fails,
    ]
    return pd.DataFrame(
        {c: pd.array([v], dtype="Int64") for c, v in zip(cols, values, strict=True)}
    )


def _texts(df: pd.DataFrame, name: str) -> list[str]:
    """A text column as a list, ``""`` for a missing value or a missing column."""
    if name not in df.columns:
        return [""] * len(df)
    return ["" if is_missing(v) else str(v) for v in df[name].tolist()]


# ---------------------------------------------------------------------------
# lines of the report
# ---------------------------------------------------------------------------


def _repro_by_repository(table: pd.DataFrame | None) -> list[str | ReportTable] | None:
    """Port of R/reproducibility_check.R::.repro_by_repository().

    One line per repository when a paper links several, so a reader can see when the
    failures lie in one of them, such as software by others that the paper only uses.
    Nothing is left out or labelled: a repository whose owner is no author is often the
    authors' own, and skipping those would hide 575 real failures (#568, item 8).
    The heading and the table, or ``None`` with fewer than two repositories.
    """
    if table is None or len(table) == 0 or not {"repo_name", "outcome"} <= set(table.columns):
        return None
    outcome = table["outcome"].tolist()
    repo = table["repo_name"].tolist()
    keep = [i for i, oc in enumerate(outcome) if not is_missing(oc) and oc not in _NOT_SCRIPTS]
    repos = [None if is_missing(repo[i]) else str(repo[i]) for i in keep]
    names = sorted({r for r in repos if r is not None}, key=r_sort_key)
    if len(names) < 2:
        return None
    ok = [outcome[i] == "ran_ok" for i in keep]
    frame = pd.DataFrame(
        {
            "Repository": pd.array(names, dtype="string"),
            "Scripts": pd.array([sum(r == nm for r in repos) for nm in names], dtype="Int64"),
            "Ran to the end": pd.array(
                [sum(o and r == nm for o, r in zip(ok, repos, strict=True)) for nm in names],
                dtype="Int64",
            ),
        }
    )
    return ["#### Results by repository", scroll_table(frame, maxrows=10)]


def _repro_absent_report(absent: pd.DataFrame) -> list[str] | None:
    """Port of R/reproducibility_check.R::.repro_absent_report().

    What the deposit does not hold, for the scripts that did not run through, with
    scripts apart from data: authors can act on this (#568, item 23). *absent* has the
    rows of ``repro_missing_inputs()``; those with a similar file in the deposit are
    left out. ``None`` when none is left.
    """
    if not {"status", "similar_to", "basename"} <= set(absent.columns):
        return None
    rows = absent[
        (absent["status"] == "absent").fillna(False).astype(bool) & absent["similar_to"].isna()
    ]
    if len(rows) == 0:
        return None
    base = [str(b) for b in rows["basename"].tolist()]
    code = [
        bool(grepl(r"\.(r|rmd|qmd|py|ipynb|stan|jags|bugs)$", b, ignore_case=True)) for b in base
    ]

    def list_some(x: Sequence[str]) -> str:
        shown = ", ".join(f"`{s}`" for s in x[:25])
        return shown + (f" and {len(x) - 25:d} more" if len(x) > 25 else "")

    data = [b for b, c in zip(base, code, strict=True) if not c]
    scripts = [b for b, c in zip(base, code, strict=True) if c]
    out = ["#### Files the deposit does not hold"]
    if data:
        n = len(data)
        out.append(
            f"The scripts that did not run through read {n:d} data file{plural(n)} that "
            f"{plural(n, 'is', 'are')} not in the deposit, and no script of the deposit "
            f"writes: {list_some(data)}."
        )
    if scripts:
        n = len(scripts)
        out.append(
            f"They also run {n:d} script{plural(n)} that {plural(n, 'is', 'are')} not in the "
            f"deposit: {list_some(scripts)}."
        )
    return out
