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


_NO_MESSAGE_OUTCOMES = frozenset(
    {
        "ran_ok",
        "run_by_source",
        "run_by_import",
        "jags_model",
        "not_r_content",
        "defines_functions",
        "not_analysis",
        "empty",
    }
)
_URL_PAT = r"https?://[^\s'\"<>)\]]+"


def _repro_error_message(
    outcome: Sequence[str | None],
    error: Sequence[str | None] | None,
    stderr: Sequence[str | None] | None = None,
    python: bool = False,
    max_chars: int = 300,
) -> list[str | None]:
    """Port of R ``.repro_error_message()`` (#615).

    The message of each script's failure, short, for the module's table, so a reader of
    the table need not take it from the report text, whose wording changes: the error line
    of the script's output (as the report shows it, see :func:`_repro_error_line`), which
    names the package that is not there; for a script that needed the internet, with the
    address it asked for when the error line names none; for one that timed out, the time
    limit. Else the last line of the output. At most *max_chars* characters; ``None`` for
    a script that ran through or has no message.
    """
    n = len(outcome)
    err = list(error) if error is not None else [None] * n
    sto = list(stderr) if stderr is not None else [None] * n
    err = (err * n)[:n] if len(err) == 1 else err
    sto = (sto * n)[:n] if len(sto) == 1 else sto

    def shorten(x: str) -> str:
        x = " ".join(x.split())
        return x[: max_chars - 1] + "\u2026" if len(x) > max_chars else x

    out: list[str | None] = []
    for i in range(n):
        o = outcome[i]
        if is_missing(o) or o in _NO_MESSAGE_OUTCOMES:
            out.append(None)
            continue
        e_i, s_i = err[i], sto[i]
        e = e_i if not is_missing(e_i) and str(e_i).strip() else s_i
        if is_missing(e) or not str(e).strip():
            out.append(None)
            continue
        e = str(e)
        # the time limit: error is the limit's own sentence ("timed out after 3600s")
        if o in ("timed_out", "skipped_time_limit"):
            out.append(shorten(e))
            continue
        line = _repro_error_line(e, python=python, max_chars=max_chars)
        if not line:
            line = _repro_error_line(e, python=not python, max_chars=max_chars)
        if not line:
            lines = [
                ln
                for ln in (x.strip() for x in e.replace("\r\n", "\n").split("\n"))
                if ln and not re.match(r"^(Execution halted|Calls:|Backtrace:)", ln)
            ]
            line = shorten(lines[-1]) if lines else ""
        if o == "needs_internet" and not re.search("https?://", line, re.IGNORECASE):
            both = f"{e} {'' if is_missing(s_i) else s_i}"
            url = re.search(_URL_PAT, both)
            if url:
                line = shorten(f"{line} ({url.group(0)})")
        out.append(line if line else None)
    return out


def _repro_read_failed_msg(errors: Sequence[str | None], max_chars: int = 300) -> list[str | None]:
    """Port of R ``.repro_read_failed_msg()`` (#620).

    For each script that could not be read, why, for its row in the table and the report;
    ``None`` where the read worked. R names here any of the R packages it reads scripts
    with that is installed but cannot be loaded; pytacheck reads them with Python, so it
    has no such package to name (D139).
    """
    out: list[str | None] = []
    for e in errors:
        if is_missing(e):
            out.append(None)
            continue
        text = " ".join(str(e).split())
        if len(text) > max_chars:
            text = text[: max_chars - 1] + "\u2026"
        out.append(f"metacheck could not read the file: {text}.")
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
        # "Data file not found: ../data/x.csv", Rcpp's "file not found: 'x.cpp'", h5py's "unable
        # to open file: name = 'x.h5'", ggsave's "Cannot find directory `x`", a
        # FileNotFoundError with only a path (#648)
        f"|[Ff]ile not found:? *{q}?([^\\s'\"\u2018\u2019(]+[./][^\\s'\"\u2018\u2019(]*)"
        f"|unable to open file[^\n]*?name = {q}({nq}+){q}"
        f"|[Cc]annot find (?:the )?(?:file|directory|folder) [`'\"\u2018]({nq}+?)[`'\"\u2019]"
        "|FileNotFoundError: (?!\\[Errno)([^\\s'\"]+[./][^\\s'\"]*)"
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
                # programs R packages start: Chrome for chrome_print() and webshot2, pandoc for
                # rmarkdown and Word tables, Python for reticulate and argparse (#648)
                if grepl(
                    r"Cannot find (Chromium or )?(Google )?Chrome|find_chrome\(\)", m, perl=True
                ):
                    found = "Chrome"
                elif grepl(
                    r"[Pp]andoc[^\n]{0,40}(is required|not found|was not found|is not available)",
                    m,
                    perl=True,
                ):
                    found = "pandoc"
                elif grepl(
                    r"\b[Pp]ython3? (is required|was not found|not found)|Python was not found",
                    m,
                    perl=True,
                ):
                    found = "Python"
                elif "subprocess" in m:
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
    # The script's own message about its arguments, in either language: "Provide the file index
    # as the first command-line argument", "--units is required", argparse's "error: one of
    # --synthetic / --real is required", "One of -x, -y, or -z should be given" (#648)
    own = (
        "command[- ]line arguments?\\b|"
        "(?:^|[\\s'\"`])--?[A-Za-z][\\w-]*\\b[^\n]{0,60}?\\b(?:is|are) required|"
        "\\b[Oo]ne of --?[A-Za-z][\\w-]*|"
        "--?[A-Za-z][\\w-]*\\b[^\n]{0,60}?\\b(?:should|must) be (?:given|specified|provided|supplied)"
    )
    if python:
        points = grepl(
            "the following arguments are required|: error: (argument|unrecognized arguments|one of)|"
            r"Use '-h'|sys[.]argv\[|parse_args\(|" + own,
            texts,
            perl=True,
        )
    else:
        points = grepl(
            "Error in [^\n:]*(\\bargs?\\[|\\bargv\\[|commandArgs)|" + own, texts, perl=True
        )
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
    "cannot open compressed|"
    # "Data file not found: x", "model file(s) not found", h5py's "unable to open file",
    # ggsave's "Cannot find directory", sf's "The file doesn't seem to exist" (#648)
    "[Ff]ile(?:\\(s\\)|s)? not found|[Uu]nable to open file|"
    "[Cc]annot find (?:the )?(?:directory|folder)|doesn't seem to exist"
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

    def has(pat: str, text: str) -> bool:
        return bool(grepl(pat, text, perl=True))

    # A file of a package of the code, which imports its other files with "from .", run on
    # its own: Python runs such a file only as a module of its package (python -m
    # package.module), so this says how the run started it, not that a package was missing
    # (#580, #648; 8 of 620 medRxiv papers)
    if outcome == "errored" and has(
        "attempted relative import (?:with no known parent package|beyond top-level package)",
        full,
    ):
        return "how_to_run"
    # A Python module that exists only on one platform (Google Colab, Kaggle, Windows,
    # OpenSAFELY): the script can be checked only there, so neither a package that was
    # not available nor an error of the code (#580, #648; in the medRxiv runs google.colab,
    # win32com and ehrql).
    if outcome in ("errored", "dependency_unavailable") and _repro_platform_of([full])[0]:
        return "platform"
    if outcome != "errored":
        return reason

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
    # a setting from the authors' computer that a deposit cannot hold: an environment variable
    # with the place of restricted data, or a personal key for an online service ("LEVELS_DB is
    # not set", "ANTHROPIC_API_KEY environment variable is required", openai's "Missing
    # credentials") (#648)
    if has(
        "\\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\\b[^\n]{0,40}?\\b(?:is not set|not set\\b|is required|must be set)|"
        "environment variable[^\n]{0,60}?\\b(?:is not set|not set|is required|must be set|missing)|"
        "[Ss]et the [^\n]{0,40}?environment variable|Missing credentials|"
        "api[_ ]?key[^\n]{0,40}?(?:not set|missing|required)|"
        # a key, token or password the authors' own message asks for ("ERROR: set
        # ZENODO_TOKEN", "Either set OPENAI_API_KEY"), a cloud sign-in (Google's "default
        # credentials were not found"), and a variable a platform sets, read from the
        # environment (All of Us: KeyError: 'WORKSPACE_CDR') (#648, K4)
        "(?i:\\b(?:set|provide|missing|export|define)\\b)[^\n]{0,40}?\\b[A-Z][A-Z0-9]*_(?:[A-Z0-9]+_)*"
        "(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIALS?|API)\\b|"
        "DefaultCredentialsError|default credentials were not found|"
        "could not automatically determine credentials",
        msg,
    ):
        return "settings"
    if has("KeyError: '[A-Z][A-Z0-9_]{2,}'", msg) and has("environ|os\\.py", full):
        return "settings"
    if has(
        "there is no package called|ModuleNotFoundError|No module named|ImportError|"
        "package or namespace|Error: package ",
        msg,
    ):
        return "package"
    # a package that another package loads only when one of its functions is used, so the run
    # did not install it: "Please install the 'cmdstanr' package", 'The package
    # "broom.helpers" (>= 1.20.0) is required' (#648)
    if has(
        "[Pp]lease install the ['\"`\u2018][^'\"`\u2019]+['\"`\u2019] package|"
        "[Tt]he package ['\"`\u2018][^'\"`\u2019]+['\"`\u2019] (?:\\([^)]*\\) )?is required|"
        "requires the ['\"`\u2018][^'\"`\u2019]+['\"`\u2019] package",
        msg,
    ):
        return "package"
    # a program our run does not have (git, pandoc, Chrome): a gap in our run
    if _repro_missing_program([full])[0] is not None:
        return "environment"
    # "does not exist" only of a file: Stan's "variable does not exist" is not one
    # (10.1111/1365-2435.14436; #575, item 10). A file error after a program could not
    # be started is about the program (#575, item 7): "Output file does not exist.
    # Unable to find or run mark.exe"
    if has(_FILE_PAT, msg):
        return "file"
    for pat, label in (
        # a function only R for Windows has, which the run's Linux lacks: the plot windows
        # (windows(), win.graph(), bringToTop()), its own plot files and fonts. Without
        # this, 'could not find function "windows"' counted as something the code does not
        # define (#580).
        (
            'could not find function "(?:windows|win\\.graph|win\\.metafile|win\\.print|'
            'bringToTop|msgWindow|windowsFonts?)"',
            "environment",
        ),
        # names Jupyter itself provides to a notebook, run here as plain Python (ufpgn_v1's
        # display(); #648)
        ("name '(?:display|get_ipython)' is not defined", "environment"),
        ("object '[^']*' not found|could not find function|NameError|is not defined", "undefined"),
        ("SyntaxError|IndentationError", "syntax"),
        (
            "shared object|ImproperlyConfigured|cannot cache function|cannot open display|X11|"
            "can only print from a screen device|unable to start data viewer",
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


def _repro_from_report_names(code_tbl: pd.DataFrame | None) -> list[str]:
    """The scripts code_check recovered from a knitted report (``.html``).

    The module's ``from_report``: the report leaves out the chunks it does not show, so the
    recovered code is incomplete, and its failure was blamed on the code (#648, K8a: 17 of 132
    preprints). Their ``file_name``\\s; empty when there are none.
    """
    if code_tbl is None or len(code_tbl) == 0 or "recovered_from" not in code_tbl.columns:
        return []
    return [
        str(fn)
        for fn, rf in zip(
            code_tbl["file_name"].tolist(), code_tbl["recovered_from"].tolist(), strict=True
        )
        if not is_missing(rf) and re.search(r"\.html?$", str(rf), re.IGNORECASE)
    ]


def _repro_from_report_failed(
    run_results: pd.DataFrame | None, from_report: Sequence[str]
) -> list[bool]:
    """Port of R/reproducibility_check.R::.repro_from_report_failed().

    Which run results are of code recovered from a knitted report (*from_report*, file
    names) and stopped on what would otherwise count against the code: a file, a name not
    defined, a syntax error, or another error. A knitted report leaves out the chunks it
    does not show (``echo = FALSE``, ``include = FALSE``), so such code is incomplete, and
    its failure says nothing about the authors' analysis (#648, K8a).
    """
    if run_results is None or len(run_results) == 0:
        return []
    if len(from_report) == 0:
        return [False] * len(run_results)
    reason = (
        run_results["reason"].tolist()
        if "reason" in run_results.columns
        else [None] * len(run_results)
    )
    keep = set(from_report)
    return [
        oc == "errored"
        and fn in keep
        and not is_missing(r)
        and r in ("file", "undefined", "syntax", "other", "no_message")
        for oc, fn, r in zip(
            run_results["outcome"].tolist(), run_results["file_name"].tolist(), reason, strict=True
        )
    ]


_PLATFORM_MODULE = re.compile(r"No module named ['\"]([A-Za-z0-9_.]+)['\"]")
_WINDOWS_MODULES = frozenset(
    {
        "win32com",
        "win32api",
        "win32con",
        "win32gui",
        "win32file",
        "win32process",
        "win32clipboard",
        "pywintypes",
        "pythoncom",
        "winreg",
        "_winreg",
        "msvcrt",
    }
)


def _repro_platform_of(msg: Sequence[str | None]) -> list[str | None]:
    """Port of R/reproducibility_check.R::.repro_platform_of().

    The platform a Python module that exists only there belongs to, from an error "No
    module named '<module>'": "Google Colab" (google.colab), "Kaggle" (kaggle_secrets and
    the like), "Windows" (pywin32's win32com, win32api and the like; winreg and msvcrt of
    Python on Windows) or "OpenSAFELY" (ehrql, cohortextractor). ``None`` for any other
    message.
    """
    out: list[str | None] = []
    for m in msg:
        found = _PLATFORM_MODULE.search("" if is_missing(m) else str(m))
        mod = found.group(1) if found else ""
        top = mod.split(".", 1)[0]
        label: str | None = None
        if re.match(r"^google\.colab(\.|$)", mod):
            label = "Google Colab"
        elif re.match(r"^kaggle_(secrets|datasets|web_client|gcp)$", top):
            label = "Kaggle"
        elif top in _WINDOWS_MODULES:
            label = "Windows"
        elif top in ("ehrql", "cohortextractor"):
            label = "OpenSAFELY"
        out.append(label)
    return out


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
    "settings",
    "platform",
    "how_to_run",
    "from_report",
    "internet",
    "download",
    "interactive",
    "time",
    "no_message",
    "other",
)

#: Outcomes of files that are not scripts with a result of their own: run by another script,
#: files of software or of functions only, not downloaded, empty (``.repro_not_own_outcomes``).
#: The summary counted them as scripts run: "0 of 5 scripts ran" when 4 were files of a package
#: that were rightly not run (#648).
_REPRO_NOT_OWN_OUTCOMES = frozenset(
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
#: Outcomes of scripts that were started (``.repro_started_outcomes``). The summary said "We
#: assessed AND ran 32 Python code files" when none was started (#648).
_REPRO_STARTED_OUTCOMES = frozenset(
    {
        "ran_ok",
        "errored",
        "timed_out",
        "needs_a_person",
        "needs_internet",
        "download_too_large",
        "out_of_memory",
    }
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
        "empty",
    }
)


#: What the run of each script says about the code (#580): ``ran``, ``code_error``,
#: ``dependency``, ``time`` and ``not_checkable`` (``.repro_check_groups``).
_REPRO_CHECK_GROUPS = ("ran", "code_error", "dependency", "time", "not_checkable")


def _repro_check_group(
    outcome: Sequence[str | None],
    reason: Sequence[str | None] | None = None,
    msg: Sequence[str | None] | str | None = None,
) -> list[str | None]:
    """Port of R/reproducibility_check.R::.repro_check_group().

    What the run of each script says about the code (#580), in five groups, so that a script
    that does not reproduce is kept apart from one the check could not test:

    - ``ran``: it ran to the end;
    - ``code_error``: it did not reproduce: it stopped with an error of the code or the
      deposit (a file it reads, something it does not define), could not be read, is Python 2
      code, or needs a file the deposit does not hold;
    - ``dependency``: a package or a program was not available: a package that could not be
      installed or was not loaded ("there is no package called"), a program outside R and
      Python, a system library ("shared object"), or R crashing in a package's compiled code,
      which comes from a system library of our computer that does not match the package (#652);
    - ``time``: it did not finish within the time limit for one script, or was not started
      because the time for all scripts together had run out;
    - ``not_checkable``: it could not be checked automatically, because it needs a person, the
      internet, a key or another setting of the authors' computer, one platform (Google Colab,
      Kaggle, Windows, OpenSAFELY), more memory than the run allows, a download that was too
      large, a file we did not download, a screen, or a program that runs it (Snakemake), or it
      is a file of a package of the code that Python runs only as a module of that package.

    ``None`` for what is not a result of the script itself: a file run inside another script, a
    file that is not an analysis, a file not read, and a Docker failure, which says nothing about
    the code. *reason* is :func:`_repro_failure_reason`'s, and *msg* the script's output, which
    tells a missing program or system library apart from the other needs of the reason
    ``environment``.
    """
    n = len(outcome)
    if n == 0:
        return []
    reasons = _recycle(reason, n, None)
    msgs = ["" if is_missing(m) else str(m) for m in _recycle(msg, n, "")]
    out: list[str | None] = []
    for oc, rs, m in zip(outcome, reasons, msgs, strict=True):
        err = oc == "errored"
        group: str | None = None
        if oc == "ran_ok":
            group = "ran"
        elif oc in ("errored", "not_parsed", "python2", "skipped_missing_inputs"):
            group = "code_error"
        if oc == "dependency_unavailable" or (err and rs == "package"):
            group = "dependency"
        if oc in ("timed_out", "skipped_time_limit"):
            group = "time"
        if oc in (
            "needs_a_person",
            "needs_internet",
            "out_of_memory",
            "download_too_large",
            "not_downloaded",
        ) or (err and rs in ("settings", "internet", "interactive", "download")):
            group = "not_checkable"
        # a module of one platform, or a file of a package run on its own, also when the
        # install tried to find the module as a package
        if oc in ("errored", "dependency_unavailable") and rs in (
            "platform",
            "how_to_run",
            "from_report",
        ):
            group = "not_checkable"
        if err and rs == "environment":
            missing_software = not _repro_out_of_memory([m])[0] and (
                _repro_missing_program([m])[0] is not None
                or bool(
                    grepl(
                        "shared object|caught segfault|Segmentation fault|core dumped|"
                        "caught illegal operation",
                        m,
                        perl=True,
                    )
                )
            )
            group = "dependency" if missing_software else "not_checkable"
        out.append(group)
    return out


def _recycle(x: Any, n: int, default: Any) -> list[Any]:
    """R's ``rep_len(x %||% default, n)``: a missing *x* is *default*, a scalar is repeated."""
    if x is None:
        x = default
    if isinstance(x, str) or not isinstance(x, Sequence):
        x = [x]
    items = list(x)
    if not items:
        return [default] * n
    return [items[i % len(items)] for i in range(n)]


def _checked_groups(
    run_results: pd.DataFrame, reason: Sequence[str | None] | None = None
) -> list[str | None]:
    """``run_results$checked``, or what :func:`_repro_check_group` gives for the results.

    *reason* is the reason of each script when it is not in ``run_results["reason"]``.
    """
    if "checked" in run_results.columns:
        return [None if is_missing(v) else str(v) for v in run_results["checked"].tolist()]
    if reason is None and "reason" in run_results.columns:
        reason = [None if is_missing(v) else v for v in run_results["reason"].tolist()]
    return _repro_check_group(
        [None if is_missing(v) else v for v in run_results["outcome"].tolist()],
        reason,
        [
            f"{e} {s}"
            for e, s in zip(
                _texts(run_results, "error"), _texts(run_results, "stderr"), strict=True
            )
        ],
    )


def _repro_groups_sentence(run_results: pd.DataFrame | None) -> str | None:
    """Port of R/reproducibility_check.R::.repro_groups_sentence().

    The module summary's sentence on the scripts that did not run to the end, by group (#580):
    "Of the others, 2 did not reproduce, 1 could not run because a package or program was not
    available, and 1 could not be checked automatically." Only scripts with a result of their
    own count (as in "3 of 6 scripts ran without error"), and not a Docker failure. ``None``
    when there are none.
    """
    if run_results is None or len(run_results) == 0:
        return None
    oc = [None if is_missing(v) else v for v in run_results["outcome"].tolist()]
    group = _checked_groups(run_results)
    own = [o not in _REPRO_NOT_OWN_OUTCOMES for o in oc]
    labels = {
        "code_error": "did not reproduce",
        "dependency": "could not run because a package or program was not available",
        "time": "did not finish within the time limit",
        "not_checkable": "could not be checked automatically",
    }
    parts = []
    for g, text in labels.items():
        k = sum(o and gr == g for o, gr in zip(own, group, strict=True))
        if k > 0:
            parts.append(f"{k:d} {text}")
    if not parts:
        return None
    if len(parts) > 1:
        parts = [*parts[:-1], f"and {parts[-1]}"]
    return "Of the others, " + (", " if len(parts) > 2 else " ").join(parts) + "."


def _repro_run_light(tl: str, run_results: pd.DataFrame | None) -> str:
    """Port of ``.repro_run_light()``: the light after a run of one language.

    From the light before it (*tl*, from the files the code reads) and the scripts' outcomes
    and reasons. A script that stopped on an error of the code makes it red. Yellow, when
    nothing is red: a script whose package or program was not available, that ran past a time
    limit (a slow analysis is not an error of the code; a time-out alone made 5 papers red,
    #648), or that could not be checked automatically (see :func:`_repro_check_group`; #580);
    a Docker failure or a file not read on our side; and a run in which no script ran to the
    end, which was green when every script was set aside or could not install its packages
    (#648).
    """
    if run_results is None or len(run_results) == 0:
        return tl
    oc = [None if is_missing(v) else v for v in run_results["outcome"].tolist()]
    group = _checked_groups(run_results)
    if any(o == "errored" and g == "code_error" for o, g in zip(oc, group, strict=True)):
        return "red"
    unfinished = (
        any(g in ("dependency", "time", "not_checkable") for g in group)
        or any(o in ("docker_failed", "not_read") for o in oc)
        or not any(o in ("ran_ok", "run_by_source", "run_by_import") for o in oc)
    )
    return "yellow" if unfinished and tl == "green" else tl


def _repro_install_sentence(install_results: pd.DataFrame, pip: bool = False) -> str:
    """Port of ``.repro_install_sentence()``: the report's sentence on the packages installed.

    It named every package tried as installed, also those that failed: "6 succeeded, 1 failed.
    matplotlib, monai, ... were installed." (#648)
    """
    installed = [not is_missing(v) and bool(v) for v in install_results["installed"].tolist()]
    ok = [str(p) for p, i in zip(install_results["package"].tolist(), installed, strict=True) if i]
    n_ok = len(ok)
    n_fail = len(install_results) - n_ok
    text = (
        "Dependencies were installed into a throwaway library before running"
        f"{' (`pip install`)' if pip else ''}: {n_ok:d} succeeded, {n_fail:d} failed."
    )
    if n_ok:
        text += f" {', '.join(ok)} {plural(n_ok, 'was', 'were')} installed."
    return text


def _repro_run_counts(run_results: pd.DataFrame | None) -> pd.DataFrame:
    """Port of R/reproducibility_check.R::.repro_run_counts().

    Per-paper counts for ``summary_table`` (#568, item 24): the scripts with a result
    of their own, the files of software that were not run (not analyses, counted
    apart), and for each reason how many scripts did not run through for it. A sourced
    or imported file, a model or function file, an empty file, and a Docker failure or a
    file not read on our side are not scripts with a result of their own, nor is a script
    that stopped on a file we did not download (counted apart as well). One row; ``NA``
    when nothing was run.
    """
    cols = [
        "repro_scripts_n",
        "repro_not_analysis_n",
        "repro_not_downloaded_n",
        *(f"repro_{g}_n" for g in _REPRO_CHECK_GROUPS),
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
        oc is not None and oc not in _NOT_SCRIPTS and oc not in ("docker_failed", "not_read")
        for oc in outcome
    ]
    fails = [
        sum(c and r == level for c, r in zip(counted, reason, strict=True))
        for level in _REPRO_REASON_LEVELS
    ]
    group = _checked_groups(run_results, reason)
    groups = [
        sum(c and g == level for c, g in zip(counted, group, strict=True))
        for level in _REPRO_CHECK_GROUPS
    ]
    values = [
        sum(counted),
        sum(oc == "not_analysis" for oc in outcome),
        sum(oc == "not_downloaded" for oc in outcome),
        *groups,
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
