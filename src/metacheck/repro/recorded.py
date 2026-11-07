"""Recorded package versions, the load check, Stan upgrades and old R defaults.

Ports of the helpers of metacheck's ``R/reproducibility_check.R`` that install the
package versions a repository records (``renv.lock``, ``sessionInfo()`` records, the date of
a ``groundhog`` or ``checkpoint`` call), check that the installed packages load, bring Stan
code from before 2.33 to the newer syntax, re-run a script with the defaults R had when the
code was written, and load the scripts a script uses first.

Several functions here write R code as text, for an R process (or a container) to run. The
text is the same as metacheck's.

Where metacheck reads the version of R that runs (``getRversion()``), the version of the
``Rscript`` that runs the scripts is asked once (:func:`_repro_host_r_version`).
"""

from __future__ import annotations

import contextlib
import gzip
import os
import re
import shutil
import tempfile
import warnings
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

from metacheck._r.base import plural
from metacheck._values import as_str, is_true
from metacheck.repro.core import (
    _chr_list,
    _col,
    _frame,
    _message,
    _na_str,
    _repro_classify_install_message,
    _repro_same_version,
    _run_r,
)
from metacheck.repro.docker import (
    _REPOS_LINES,
    _deparse1_chr,
    _docker,
    _normalize_path,
    _repro_docker_container_name,
    _repro_docker_hand_over,
    _repro_docker_resource_args,
    _repro_docker_stop,
    _repro_docker_user,
)

# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

#: The packages that install packages themselves: their calls are rewritten, not run.
_TOOLS = ["groundhog", "checkpoint", "renv"]

_VERSION_RE = re.compile(r"([0-9]+[.-])*[0-9]+")


class _NamedVector(dict[str, "str | None"]):
    """A named character vector; ``attrs`` holds R's attributes of it (``attr(x, "foo")``)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.attrs: dict[str, Any] = {}


def _package_version(v: str | None) -> tuple[int, ...] | None:
    """``package_version(v, strict = FALSE)``: the numbers of a valid version, else ``None``."""
    if v is None or not _VERSION_RE.fullmatch(v):
        return None
    return tuple(int(x) for x in re.split(r"[.-]", v))


def _version_greater(a: str | None, b: str | None) -> bool:
    """``package_version(a) > package_version(b)`` (false when either is not a version)."""
    pa, pb = _package_version(a), _package_version(b)
    return pa is not None and pb is not None and pa > pb


def _dirname(p: str) -> str:
    """R ``dirname()`` of a ``/``-separated path (``"."`` when there is no folder)."""
    p = p.rstrip("/")
    if "/" not in p:
        return "."
    return p.rpartition("/")[0].rstrip("/") or "/"


def _repo_rel_from(path: str, dir_: str = ".") -> str:
    """``.repro_rel_from()`` of the layout helpers (imported when it is used)."""
    from metacheck.repro.layout import _repro_rel_from

    return _repro_rel_from(path, dir_)


def _pin(version_pin: Mapping[str, Any] | None, key: str) -> Any:
    """``version_pin$key``: ``None`` when there is no such element."""
    if version_pin is None:
        return None
    return version_pin.get(key)


def _nrows(x: Any) -> int:
    """``nrow(x %||% data.frame())``."""
    return len(x) if isinstance(x, pd.DataFrame) else 0


def _pin_dates(version_pin: Mapping[str, Any] | None) -> list[str | None]:
    """``version_pin$pin_dates$date %||% character(0)``."""
    pd_ = _pin(version_pin, "pin_dates")
    if isinstance(pd_, pd.DataFrame):
        return _chr_list(pd_["date"]) if "date" in pd_.columns else []
    if isinstance(pd_, Mapping):
        return _chr_list(pd_.get("date"))
    return []


def _opt_col(df: pd.DataFrame | None, name: str) -> list[Any]:
    """A column of *df* as a list, ``[]`` when there is no such column."""
    if df is None or name not in df.columns:
        return []
    return _col(df, name)


def _str_col(df: pd.DataFrame, name: str) -> list[str | None]:
    """A text column as a list (``NA`` is ``None``)."""
    return _chr_list(df[name])


def _ascii_sub(pattern: str) -> re.Pattern[str]:
    """A PCRE pattern (``perl = TRUE``): ``\\s``, ``\\w`` and ``\\b`` see ASCII only."""
    return re.compile(pattern, re.ASCII)


# ---------------------------------------------------------------------------
# lines that load other scripts first
# ---------------------------------------------------------------------------


def _repro_run_after_lines(
    run_after: Mapping[str, Sequence[str]] | None,
    run_tbl: pd.DataFrame,
    root: str,
) -> dict[str, list[str]] | None:
    """Port of ``R/reproducibility_check.R::.repro_run_after_lines()``.

    Lines that load other scripts first, in the same session, each from its own run
    folder, since its paths count from there. *run_after* lists by script the scripts to
    load, and *run_tbl* is the :func:`~metacheck.repro.core.repro_write_scripts` result.
    Errors stop the script, as they would have in the authors' session. ``None`` when
    *run_after* is empty.
    """
    if not run_after:
        return None
    names = _str_col(run_tbl, "file_name")
    run_dirs = _str_col(run_tbl, "run_dir")
    script_paths = _str_col(run_tbl, "script_path")

    def from_top(p: str | None) -> str:
        if p is None:
            raise ValueError("a script in run_after is not in run_tbl")
        p = _normalize_path(p).replace("\\", "/")
        top = _normalize_path(root).replace("\\", "/")
        if p == top:
            return "."
        return p.replace(top + "/", "", 1)

    def q(x: str) -> str:
        return '"' + x.replace('"', '\\"') + '"'

    def row_of(name: str) -> int:
        if name not in names:
            raise ValueError(f"{name!r} is not in run_tbl")
        return names.index(name)

    template = (
        "local({ .old <- setwd(%s); on.exit(setwd(.old)); source(%s, local = globalenv()) })"
        "  # [reproducibility_check: load the script that defines what this one uses]"
    )
    out: dict[str, list[str]] = {}
    for user, defs in run_after.items():
        u = row_of(user)
        lines: list[str] = []
        for definition in defs:
            d = row_of(definition)
            def_dir = from_top(run_dirs[d])
            to_dir = _repo_rel_from(
                "./x" if def_dir == "." else f"{def_dir}/x", from_top(run_dirs[u])
            )
            to_dir = "." if _dirname(to_dir) == "." else _dirname(to_dir)
            script = _repo_rel_from(from_top(script_paths[d]), def_dir)
            lines.append(template % (q(to_dir), q(script)))
        out[user] = lines
    return out


# ---------------------------------------------------------------------------
# defaults of R that have changed
# ---------------------------------------------------------------------------

_FACTORS_RE = _ascii_sub(
    "only for \\(unordered\\) factors|invalid 'labels'; length|"
    "is not a factor|level sets of factors are different|"
    "argument is not a factor|must be a factor|factor level \\[\\d+\\] is duplicated|"
    "no applicable method for '(?:droplevels|levels|relevel|reorder|nlevels)' applied to an "
    'object of class "character"|'
    "must be either numeric or a factor"
)
_XY_COORDS_RE = _ascii_sub("xy\\.coords[^\n]*(?:\n[^\n]*)?NAs introduced by coercion")
_LATIN1_RE = _ascii_sub("invalid multibyte string|is invalid in this locale|utf8towcs")


def _repro_old_defaults(run_results: pd.DataFrame | None) -> dict[str, list[str]]:
    """Port of ``R/reproducibility_check.R::.repro_old_defaults()``.

    Defaults the code was written for that current R lacks, from a script's error (#568):
    before R 4.0, ``read.csv()``, ``read.table()`` and ``data.frame()`` turned text into
    factors ("'relevel' only for (unordered) factors", "invalid 'labels'"), and code
    written on Windows reads data files in Latin-1 ("invalid multibyte string").
    ``options(stringsAsFactors = TRUE)`` no longer has any effect in R 4.5. Four more
    messages of text that is no longer a factor (#578, item 5): "factor level [3] is
    duplicated" when ``levels(x)`` of text is ``NULL``, ``droplevels()`` on text,
    ``arm::standardize()``'s "must be either numeric or a factor", and ``plot()`` of text,
    "need finite 'xlim' values", only with the warning that the text became ``NA``: without
    it the message has other causes.

    Returns, for each script that stopped on such an error, ``["factors"]``,
    ``["latin1"]`` or both; an empty dict when none did.
    """
    if run_results is None or len(run_results) == 0:
        return {}
    errors = _opt_col(run_results, "error") or [None] * len(run_results)
    stderrs = _opt_col(run_results, "stderr") or [None] * len(run_results)
    outcomes = _opt_col(run_results, "outcome") or [None] * len(run_results)
    names = _opt_col(run_results, "file_name") or [None] * len(run_results)
    out: dict[str, list[str]] = {}
    for name, outcome, err, stderr in zip(names, outcomes, errors, stderrs, strict=True):
        if outcome != "errored":
            continue
        msg = f"{_na_str(as_str(err))} {_na_str(as_str(stderr))}"
        factors = bool(
            _FACTORS_RE.search(msg)
            or ("need finite 'xlim' values" in msg and _XY_COORDS_RE.search(msg))
        )
        latin1 = bool(_LATIN1_RE.search(msg))
        if factors or latin1:
            out[_na_str(name)] = [
                *(["factors"] if factors else []),
                *(["latin1"] if latin1 else []),
            ]
    return out


def _repro_old_default_lines(kinds: Sequence[str]) -> list[str]:
    """Port of ``R/reproducibility_check.R::.repro_old_default_lines()``.

    The lines put at the top of a script for :func:`_repro_old_defaults`'s defaults. The
    default is changed in R's own functions, not only in the script: before R 4.0 it held
    inside packages too, and in 10.1111/1365-2435.13388 reshape2's ``dcast()`` gave the
    dates as text again, so the script still stopped. An argument the code gives itself
    still counts.
    """
    out: list[str] = []
    if "factors" in kinds:
        out.append(
            'local({ for (w in list(c("base", "data.frame"), c("base", "as.data.frame.character"), '
            'c("base", "as.data.frame.list"), c("base", "rbind.data.frame"), '
            'c("base", "cbind.data.frame"), '
            'c("utils", "read.table"))) for (env in list(asNamespace(w[1]), '
            'as.environment(paste0("package:", w[1])))) { '
            "if (!exists(w[2], envir = env, inherits = FALSE)) next; f <- get(w[2], envir = env); "
            'if (!"stringsAsFactors" %in% names(formals(f))) next; '
            "formals(f)$stringsAsFactors <- TRUE; "
            "unlockBinding(w[2], env); assign(w[2], f, envir = env); lockBinding(w[2], env) } })"
            "  # [reproducibility_check: text read as factors, as before R 4.0]"
        )
    if "latin1" in kinds:
        out.append(
            'options(encoding = "latin1")'
            "  # [reproducibility_check: data files written on Windows, in Latin-1]"
        )
    return out


def _repro_old_defaults_text(
    used: Mapping[str, Sequence[str]], ran_ok: Sequence[str | None] = ()
) -> str | None:
    """Port of ``R/reproducibility_check.R::.repro_old_defaults_text()``.

    The report's sentence on the scripts run again with the old defaults, and which of
    them then ran to the end. ``None`` when there are none.
    """
    if not used:
        return None
    n = len(used)
    kinds = list(dict.fromkeys(k for v in used.values() for k in v))
    all_what = {
        "factors": "text read as factors, as R did before version 4.0",
        "latin1": "data files read as Latin-1, as written on Windows",
    }
    all_fix = {"factors": "`stringsAsFactors = TRUE`", "latin1": '`fileEncoding = "latin1"`'}
    what = [all_what[k] for k in kinds]
    fix = [all_fix[k] for k in kinds]
    ok = [name for name in dict.fromkeys(used) if name in set(ran_ok)]
    first = (
        f" **{n} script{plural(n)} ran again with a default R had when the code was written** "
        f"({'; '.join(what)}), because {plural(n, 'it', 'they')}, or a script that writes a file "
        f"{plural(n, 'it reads', 'they read')}, stopped on a default that has changed since: "
        f"{', '.join(used)}."
    )
    second = (
        f" {plural(len(ok), 'It', 'These')} then ran to the end: {', '.join(ok)}." if ok else ""
    )
    third = f" Setting {' or '.join(fix)} in the code itself solves this."
    return first + second + third


# ---------------------------------------------------------------------------
# Stan code from before 2.33
# ---------------------------------------------------------------------------

_STAN_DISCRETE = [
    "bernoulli", "bernoulli_logit", "binomial", "binomial_logit", "beta_binomial",
    "hypergeometric", "categorical", "categorical_logit", "ordered_logistic",
    "ordered_probit", "neg_binomial", "neg_binomial_2", "neg_binomial_2_log",
    "poisson", "poisson_log", "multinomial", "discrete_range",
]  # fmt: skip
_STAN_CONTINUOUS = [
    "normal", "normal_id_glm", "std_normal", "student_t", "cauchy", "double_exponential",
    "logistic", "gumbel", "skew_normal", "lognormal", "chi_square", "inv_chi_square",
    "scaled_inv_chi_square", "exponential", "gamma", "inv_gamma", "weibull", "frechet",
    "rayleigh", "pareto", "pareto_type_2", "beta", "beta_proportion", "von_mises",
    "uniform", "multi_normal", "multi_normal_cholesky", "multi_normal_prec",
    "multi_student_t", "dirichlet", "lkj_corr", "lkj_corr_cholesky", "wishart",
    "inv_wishart", "exp_mod_normal", "skew_double_exponential",
]  # fmt: skip
_STAN_DIST_RE = _ascii_sub("\\b(" + "|".join([*_STAN_DISCRETE, *_STAN_CONTINUOUS]) + ")_log\\s*\\(")
_STAN_BAR_RE = _ascii_sub("\\b[A-Za-z_][A-Za-z0-9_]*_(cdf|lcdf|lccdf|lpdf|lpmf|lupdf|lupmf)\\s*\\(")
_STAN_DEF_BEFORE_RE = _ascii_sub(
    "(\\b(real|int|vector|row_vector|matrix|void|complex|array\\[[^]]*\\]\\s*\\w+)"
    "(\\[[^]]*\\])?\\s+)$"
)
_STAN_TYPES = (
    "(?:int|real|complex|vector|row_vector|matrix|simplex|unit_vector|ordered|"
    "positive_ordered|cov_matrix|corr_matrix|cholesky_factor_cov|cholesky_factor_corr)"
)
_STAN_DIMS = "((?:[^\\[\\]]|\\[[^\\[\\]]*\\])+)"
_STAN_ARRAY_DECL_RE = _ascii_sub(
    "^(\\s*)((?:data\\s+)?" + _STAN_TYPES + "\\s*(?:<[^<>;]*>)?\\s*(?:\\[[^\\[\\]]*\\])?)\\s+"
    "([A-Za-z_][A-Za-z0-9_]*)\\s*\\[" + _STAN_DIMS + "\\]"
)
_STAN_ARRAY_ARG_RE = _ascii_sub(
    "\\b((?:data\\s+)?" + _STAN_TYPES + ")\\s*\\[(\\s*(?:,\\s*)*)\\]\\s+([A-Za-z_][A-Za-z0-9_]*)"
)


def _stan_call_end(line: str, open_: int, stop_at_bar: bool) -> int | None:
    """The index of the first top-level ``,`` (or ``|``) of the call opened at *open_*.

    ``None`` when the call closes first; with *stop_at_bar* a ``|`` also ends the search,
    giving ``-1``.
    """
    depth = 0
    for k in range(open_, len(line)):
        ch = line[k]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                break
        elif ch == "," and depth == 1:
            return k
        elif ch == "|" and depth == 1 and stop_at_bar:
            return -1
    return None


def _repro_upgrade_stan(txt: Sequence[str | None]) -> dict[str, Any]:
    """Port of ``R/reproducibility_check.R::.repro_upgrade_stan()``.

    Stan code in the syntax Stan removed in version 2.33 (2023), in the newer form that
    means the same (#568): ``#`` comments, ``<-``, ``increment_log_prob()``, ``get_lp()``,
    ``fabs()``, ``_log``/``_cdf_log``/``_ccdf_log`` functions, and arrays declared after the
    name (``int y[N];``, ``real[] x``). ``stanc()`` stopped on them in 12 scripts of
    10.1111/1365-2656.12673 and 10.1111/1365-2656.13534. Returns ``{"txt": the lines,
    "changes": the kinds of change made}``.
    """
    lines: list[str | None] = list(txt)
    if not lines:
        return {"txt": lines, "changes": []}
    changes: list[str] = []

    def sub_all(pattern: re.Pattern[str], rep: str, what: str) -> None:
        nonlocal lines
        new = [None if s is None else pattern.sub(rep, s) for s in lines]
        if new != lines:
            changes.append(what)
        lines = new

    sub_all(_ascii_sub('(?<![/"])#(?!include)'), "//", "# comments")
    sub_all(_ascii_sub("\\s<-\\s"), " = ", "<- assignments")
    sub_all(
        _ascii_sub("\\bincrement_log_prob\\s*\\((.*)\\)\\s*;"),
        r"target += \1;",
        "increment_log_prob()",
    )
    sub_all(_ascii_sub("\\bget_lp\\s*\\(\\s*\\)"), "target()", "get_lp()")
    sub_all(_ascii_sub("\\bfabs\\s*\\("), "abs(", "fabs()")
    sub_all(_ascii_sub("_ccdf_log\\s*\\("), "_lccdf(", "_ccdf_log()")
    sub_all(_ascii_sub("_cdf_log\\s*\\("), "_lcdf(", "_cdf_log()")

    # name_log(y, a, b) -> name_lpdf(y | a, b), or _lpmf for a discrete distribution
    known = set(_STAN_DISCRETE) | set(_STAN_CONTINUOUS)
    for i, original in enumerate(lines):
        if original is None or not _STAN_DIST_RE.search(original):
            continue
        line = original
        for m in reversed(list(_STAN_DIST_RE.finditer(original))):
            start, open_ = m.start(), m.end() - 1
            name = re.sub(r"_log\s*\($", "", m.group(0))
            # After `~`, poisson_log() and neg_binomial_2_log() are distributions of their
            # own (a log link), not poisson with the old _log suffix:
            # `y ~ poisson_log(x)` became poisson_lpmf in 10.1111/1365-2435.14436 (#568).
            # In a function call, poisson_log(y, l) was the old name of poisson's density.
            if name + "_log" in known and re.search(r"~\s*\Z", line[:start]):
                continue
            comma = _stan_call_end(line, open_, False)
            suffix = "_lpmf(" if name in _STAN_DISCRETE else "_lpdf("
            rest = line[open_ + 1 :]
            if comma is not None:
                rest = line[open_ + 1 : comma] + " |" + line[comma + 1 :]
            line = line[:start] + name + suffix + rest
        if line != original:
            changes.append("_log() functions")
            lines[i] = line

    # name_cdf(y, a), _lcdf, _lccdf, _lpdf, _lpmf: since 2.33 a vertical bar after the first
    # argument, name_cdf(y | a); `logistic_cdf(cutoff, log(g), 1)` stopped stanc in
    # 10.1111/2041-210X.14079 (#568). A function's definition (`real foo_lpdf(real y, ...)`)
    # keeps its commas, and a call with a bar is left as it is.
    for i, original in enumerate(lines):
        if original is None or not _STAN_BAR_RE.search(original):
            continue
        line = original
        for m in reversed(list(_STAN_BAR_RE.finditer(original))):
            start = m.start()
            cur = _STAN_BAR_RE.match(line, start)
            if cur is None or _STAN_DEF_BEFORE_RE.search(line[:start]):
                continue
            at = _stan_call_end(line, cur.end() - 1, True)
            if at is not None and at >= 0:
                line = line[:at] + " |" + line[at + 1 :]
        if line != original:
            changes.append("a vertical bar in _cdf()/_lpdf() calls")
            lines[i] = line

    # arrays: `int<lower=0> y[N];` -> `array[N] int<lower=0> y;`, and in function
    # arguments `real[] x` -> `array[] real x`
    sub_all(_STAN_ARRAY_DECL_RE, r"\1array[\4] \2 \3", "array declarations")
    sub_all(_STAN_ARRAY_ARG_RE, r"array[\2] \1 \3", "array arguments")
    return {"txt": lines, "changes": list(dict.fromkeys(changes))}


def _repro_upgrade_stan_in_r(txt: Sequence[str | None]) -> dict[str, Any]:
    """Port of ``R/reproducibility_check.R::.repro_upgrade_stan_in_r()``.

    Stan programs held in R code, as a quoted text over several lines that the code writes
    to a ``.stan`` file or passes as ``model_code``: ``write("data{ ... }", "TempCater.stan")``
    in 10.1111/1365-2435.14436 (#568). The lines inside such a text get the same update as
    a ``.stan`` file (see :func:`_repro_upgrade_stan`); the lines where the text opens and
    closes also hold R code and are left as they are. A text counts as a Stan program when
    it has a model block and a data or parameters block. Returns ``{"txt", "changes"}``.
    """
    lines: list[str | None] = list(txt)
    none = {"txt": lines, "changes": []}
    if not lines or not any(s is not None and re.search(r"model\s*\{", s) for s in lines):
        return none
    # where quoted texts that run over several lines start and end
    spans: list[tuple[int, int]] = []
    quote: str | None = None
    start = 0
    for i, text in enumerate(lines):
        chars = text or ""
        k = 0
        while k < len(chars):
            c1 = chars[k]
            if quote is None:
                if c1 == "#":
                    break  # an R comment
                if c1 in ('"', "'"):
                    quote = c1
                    start = i
            elif c1 == "\\":
                k += 1  # an escaped character
            elif c1 == quote:
                if i > start:
                    spans.append((start, i))
                quote = None
            k += 1
    changes: list[str] = []
    for first, last in spans:
        whole = "\n".join(_na_str(s) for s in lines[first : last + 1])
        if not re.search(r"\bmodel\s*\{", whole, re.ASCII) or not re.search(
            r"\b(data|parameters)\s*\{", whole, re.ASCII
        ):
            continue
        if last - first < 2:
            continue
        inner = range(first + 1, last)
        up = _repro_upgrade_stan([lines[j] for j in inner])
        if up["changes"]:
            for j, new in zip(inner, up["txt"], strict=True):
                lines[j] = new
            changes.extend(up["changes"])
    return {"txt": lines, "changes": list(dict.fromkeys(changes))}


def _repro_upgrade_stan_files(root: str | os.PathLike[str]) -> pd.DataFrame | None:
    """Port of ``R/reproducibility_check.R::.repro_upgrade_stan_files()``.

    Give every Stan file in the sandbox the newer syntax (:func:`_repro_upgrade_stan`);
    returns the files changed and how, as modifications rows (``file_name``,
    ``change_type``, ``detail``; ``None`` when none).
    """
    top = os.fspath(root)
    found: list[str] = []
    for here, dirs, files in os.walk(top):
        # list.files(recursive = TRUE) leaves out hidden files and folders
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        found.extend(
            os.path.join(here, f)
            for f in files
            if not f.startswith(".") and f.lower().endswith(".stan")
        )
    real_top = _normalize_path(top).replace("\\", "/")
    names: list[str] = []
    details: list[str] = []
    for path in sorted(found):
        try:
            with open(path, "rb") as fh:
                raw = fh.read().decode("utf-8", errors="surrogateescape")
        except OSError:
            continue
        txt = _read_text_lines(raw)
        up = _repro_upgrade_stan(txt)
        if not up["changes"]:
            continue
        with open(path, "wb") as fh:
            fh.write(("".join(f"{s}\n" for s in up["txt"])).encode("utf-8", "surrogateescape"))
        full = _normalize_path(path).replace("\\", "/")
        names.append(full[len(real_top) + 1 :] if full.startswith(real_top + "/") else full)
        details.append("Stan from before 2.33: " + ", ".join(up["changes"]))
    if not names:
        return None
    return _frame(
        {
            "file_name": ("string", names),
            "change_type": ("string", ["stan_syntax"] * len(names)),
            "detail": ("string", details),
        }
    )


def _read_text_lines(text: str) -> list[str]:
    """``readLines()`` of a text: LF, CRLF and CR all end a line."""
    if text == "":
        return []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines[-1] == "":
        lines.pop()
    return lines


# ---------------------------------------------------------------------------
# recorded package versions
# ---------------------------------------------------------------------------


def _repro_highest_version(versions: Sequence[str | None]) -> str | None:
    """Port of ``R/reproducibility_check.R::.repro_highest_version()``.

    The highest of several version strings; ``None`` when none can be read as a version.
    """
    valid = [v for v in versions if v is not None and v != "" and _package_version(v) is not None]
    if not valid:
        return None
    highest = valid[0]
    for v in valid[1:]:
        if _version_greater(v, highest):
            highest = v
    return highest


def _fetch_packages_file(url: str) -> bytes | None:
    """The body of *url* (``None`` for any failure): one try, no retries."""
    from metacheck import http

    try:
        resp = http.request("GET", url, max_tries=1, retry_on_failure=False, timeout=60)
    except Exception:
        return None
    if resp is None or resp.status_code != 200:
        return None
    return bytes(resp.content)


def _read_packages_index(text: str) -> dict[str, str]:
    """``Package`` -> ``Version`` of a ``PACKAGES`` file, the highest version of a package."""
    out: dict[str, str] = {}
    for record in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        fields: dict[str, str] = {}
        key = None
        for line in record.split("\n"):
            if line[:1] in (" ", "\t") and key:
                fields[key] += " " + line.strip()
            elif ":" in line:
                key, _, value = line.partition(":")
                fields[key] = value.strip()
        pkg, version = fields.get("Package"), fields.get("Version")
        if not pkg or version is None:
            continue
        have = out.get(pkg)
        if have is None or _version_greater(version, have):
            out[pkg] = version
    return out


def _repro_date_versions(pkgs: Sequence[str], date: str) -> dict[str, str | None]:
    """Port of ``R/reproducibility_check.R::.repro_date_versions()``.

    The version of each package on a dated copy of CRAN, from Posit's package manager (the
    copies start in late 2017). ``None`` for a package that was not on CRAN at that date.
    All ``None``, with ``attrs["failed"]`` true, when the copy could not be read: a date
    before the first copy, or no network.
    """
    base = f"https://packagemanager.posit.co/cran/{date}/src/contrib/"
    index: dict[str, str] | None = None
    body = _fetch_packages_file(base + "PACKAGES.gz")
    if body is not None:
        with contextlib.suppress(OSError, EOFError):
            index = _read_packages_index(gzip.decompress(body).decode("utf-8", "replace"))
    if not index:
        body = _fetch_packages_file(base + "PACKAGES")
        index = _read_packages_index(body.decode("utf-8", "replace")) if body else None
    failed = not index
    out = _NamedVector(dict.fromkeys(pkgs))
    if index:
        for p in pkgs:
            out[p] = index.get(p)
    out.attrs["failed"] = failed
    return out


def _repro_recorded_plan(
    deps: pd.DataFrame | None, version_pin: Mapping[str, Any] | None, use_dates: bool = True
) -> dict[str, Any] | None:
    """Port of ``R/reproducibility_check.R::.repro_recorded_plan()``.

    The recorded version of each package the scripts load. A repository can record the
    package versions its analysis used: an ``renv.lock`` file, a ``sessionInfo()`` or
    ``session_info()`` record, or a date in a ``groundhog.library()`` or ``checkpoint()``
    call (the ``version_pin`` result of ``code_check``). This matches those records to the
    packages the scripts load, because only those are installed: a listed package no script
    loads is left out, and a loaded package that is not listed keeps ``None`` and is
    installed at its current version.

    ``renv.lock`` comes first, then the ``sessionInfo()`` and ``session_info()`` records,
    then a date, the latest one when there are several. Within one kind of record the
    highest version wins when files disagree, since a run has one library. A package whose
    record says it came from GitHub is installed from that commit.

    ``groundhog``, ``checkpoint`` and ``renv`` are left out: the scripts' calls to them are
    rewritten (see ``repro_write_scripts()``), since they would install packages themselves,
    which needs the network that is off during a run.

    Packages that come with R (``Matrix``, ``MASS``, ...) keep R's own version, since they
    are built for that R version, and are listed in ``kept_with_r``.

    *deps* is the frame of ``repro_dependencies()`` (``package``, ``source``, ``ref``,
    ``base``). *version_pin* is the ``code_check`` result (a mapping with
    ``renv_packages``, ``session_packages``, ``pin_dates`` and ``r_versions``). With
    *use_dates* false the versions for a date, which needs the network, are not looked up;
    such a package then keeps ``None``.

    Returns ``None`` when nothing records a version of a package the scripts load, else a
    dict: ``deps`` (the non-base rows of *deps*, with ``version`` and ``recorded_from``),
    ``kept_with_r`` (frame ``package``, ``version``), ``dates`` (the date used, as a list of
    one, or ``[]``) and ``date_failed`` (true when the date's versions could not be looked
    up).
    """
    if deps is None or len(deps) == 0 or version_pin is None:
        return None
    packages = _str_col(deps, "package")
    base = [b is True for b in _col(deps, "base")]
    base_pkgs = {p for p, b in zip(packages, base, strict=True) if b}
    loaded = [p for p in dict.fromkeys(packages) if p not in _TOOLS]

    version: dict[str | None, str | None] = dict.fromkeys(loaded)
    from_: dict[str | None, str | None] = dict(version)
    origin: dict[str | None, str | None] = dict(version)
    remote: dict[str | None, str | None] = dict(version)
    for rec in (_pin(version_pin, "renv_packages"), _pin(version_pin, "session_packages")):
        if rec is None or len(rec) == 0:
            continue
        rec_pkg = _str_col(rec, "package")
        rec_ver = _str_col(rec, "version")
        rec_file = _str_col(rec, "file_name")
        rec_src = _str_col(rec, "source") if "source" in rec.columns else [None] * len(rec)
        rec_remote = _str_col(rec, "remote") if "remote" in rec.columns else [None] * len(rec)
        listed = set(rec_pkg)
        for pkg in [p for p in loaded if version[p] is None and p in listed]:
            rows = [i for i, p in enumerate(rec_pkg) if p == pkg]
            v = _repro_highest_version([rec_ver[i] for i in rows])
            if v is None:
                continue
            row = next(i for i in rows if rec_ver[i] == v)
            version[pkg] = v
            from_[pkg] = rec_file[row]
            origin[pkg] = rec_src[row]
            remote[pkg] = rec_remote[row]

    dates = sorted({d for d in _pin_dates(version_pin) if d is not None}, reverse=True)
    date_failed = False
    if dates and is_true(use_dates):
        open_ = [p for p in loaded if version[p] is None and p not in base_pkgs]
        if open_:
            dv = _repro_date_versions([p for p in open_ if p is not None], dates[0])
            attrs = getattr(dv, "attrs", {})
            date_failed = attrs.get("failed") is True
            if date_failed:
                warnings.warn(
                    f"reproducibility_check(): the package versions for the date {dates[0]} "
                    "could not be looked up, because copies of CRAN start in late 2017 or "
                    "the network could not be reached. Those packages are not installed at "
                    "recorded versions.",
                    UserWarning,
                    stacklevel=2,
                )
            for p in open_:
                if p is not None and dv.get(p) is not None:
                    version[p] = dv[p]
                    from_[p] = f"date {dates[0]}"
    if all(v is None for v in version.values()):
        return None

    rec_base = [p for p, b in zip(packages, base, strict=True) if b and version.get(p) is not None]
    keep = [not b and p not in _TOOLS for p, b in zip(packages, base, strict=True)]
    out = deps.loc[keep].reset_index(drop=True).copy()
    out_pkgs = _str_col(out, "package")
    if not any(version.get(p) is not None for p in out_pkgs):
        return None
    out["version"] = pd.Series([version.get(p) for p in out_pkgs], dtype="string")
    out["recorded_from"] = pd.Series([from_.get(p) for p in out_pkgs], dtype="string")

    # Where the record says the package came from decides how it is installed, unless the
    # code itself names a GitHub or URL source.
    sources = _str_col(out, "source")
    refs: list[str | None] = _str_col(out, "ref") if "ref" in out.columns else [None] * len(out)
    for i, p in enumerate(out_pkgs):
        rec_origin, rec_remote_i = origin.get(p), remote.get(p)
        if rec_origin == "GitHub" and rec_remote_i is not None:
            sources[i] = "github"
            refs[i] = rec_remote_i
        elif rec_origin == "Bioconductor" and sources[i] == "cran":
            sources[i] = "bioc"
    out["source"] = pd.Series(sources, dtype="string")
    out["ref"] = pd.Series(refs, dtype="string")

    return {
        "deps": out,
        "kept_with_r": _frame(
            {
                "package": ("string", rec_base),
                "version": ("string", [version.get(p) for p in rec_base]),
            }
        ),
        "dates": dates[:1],
        "date_failed": date_failed,
    }


def _repro_recorded_sources(version_pin: Mapping[str, Any] | None) -> str:
    """Port of ``R/reproducibility_check.R::.repro_recorded_sources()``.

    Which files or dates record the versions, as words for the warning and report.
    """
    dates = [d for d in _pin_dates(version_pin) if d is not None]
    has_session = _nrows(_pin(version_pin, "session_packages")) > 0
    has_renv = _nrows(_pin(version_pin, "renv_packages")) > 0
    parts = [
        *(["an `renv.lock` file"] if has_renv else []),
        *(["a `sessionInfo()` record"] if has_session else []),
        *([f"the date {max(dates)} in a `groundhog` or `checkpoint` call"] if dates else []),
    ]
    return " and ".join(parts)


def _repro_host_r_version() -> str | None:
    """``as.character(getRversion())`` of the ``Rscript`` that runs the scripts, else ``None``."""
    try:
        res = _run_r("cat(as.character(getRversion()))", timeout=60)
    except Exception:
        return None
    if res is None or res.returncode != 0:
        return None
    return res.stdout.strip() or None


def _repro_recorded_limits(
    recorded: Mapping[str, Any],
    version_pin: Mapping[str, Any] | None,
    sandbox: str,
    use_declared: bool = False,
) -> list[str]:
    """Port of ``R/reproducibility_check.R::.repro_recorded_limits()``.

    What installing recorded versions does not do, as sentences: the limitation stated in
    both the warning before a run and the report after it.
    """
    declared = [r for r in _chr_list(_pin(version_pin, "r_versions")) if r is not None]
    r_kept = not (sandbox == "docker" and is_true(use_declared) and len(declared) > 0)
    deps = recorded["deps"]
    bioc = [
        p
        for p, s, v in zip(
            _str_col(deps, "package"),
            _str_col(deps, "source"),
            _str_col(deps, "version"),
            strict=True,
        )
        if s == "bioc" and v is not None
    ]
    kept = [p for p in _str_col(recorded["kept_with_r"], "package") if p is not None]
    bioc_pkgs = [p for p in bioc if p is not None]
    out: list[str] = []
    if r_kept:
        if sandbox == "docker":
            which = "the R version of the Docker image"
        else:
            which = "R " + (_repro_host_r_version() or "of this machine")
        records = f", while the repository records R {declared[0]}" if declared else ""
        out.append(
            f"The R version is not changed: this run uses {which}{records}, so an old package "
            "may not install, or may give other results than it gave the authors."
        )
    out.append(
        "Packages that the installed packages depend on are not at their recorded versions, "
        "which can also change results."
    )
    if kept:
        out.append(f"Packages that come with R keep R's own version: {', '.join(kept)}.")
    if bioc_pkgs:
        out.append(
            "Bioconductor versions follow the R version, so these get their current version: "
            f"{', '.join(bioc_pkgs)}."
        )
    return out


def _repro_recorded_warning(
    recorded: Mapping[str, Any],
    version_pin: Mapping[str, Any] | None,
    sandbox: str,
    use_declared: bool = False,
) -> str:
    """Port of ``R/reproducibility_check.R::.repro_recorded_warning()``.

    The warning before a run that installs recorded versions.
    """
    deps = recorded["deps"]
    n = len(deps)
    n_listed = sum(v is not None for v in _str_col(deps, "version"))
    parts = [
        "reproducibility_check(): the repository records package versions in "
        f"{_repro_recorded_sources(version_pin)}, so {n_listed} of the {n} package{plural(n)} the "
        "scripts load will be installed at those versions. This can take a long time, because "
        "old versions are built from source."
    ]
    if n_listed < n:
        k = n - n_listed
        parts.append(
            f"{k} loaded package{plural(k, ' is', 's are')} not listed and "
            f"get{plural(k, 's', '')} the current version."
        )
    parts.extend(_repro_recorded_limits(recorded, version_pin, sandbox, use_declared))
    return " ".join(parts)


def _repro_recorded_counts(
    recorded: Mapping[str, Any], install_results: pd.DataFrame
) -> dict[str, Any]:
    """Port of ``R/reproducibility_check.R::.repro_recorded_counts()``.

    The packages of a run with recorded versions, by what happened to them: true per
    package in ``exact`` (at the recorded version), ``replaced`` (installed at another
    version) and ``failed`` (not installed), for the recorded packages. Also ``listed`` (the
    rows of the recorded ``deps`` that have a version) and ``results`` (their rows of
    *install_results*; all missing for a package without one).
    """
    deps = recorded["deps"]
    listed = deps.loc[[v is not None for v in _str_col(deps, "version")]].reset_index(drop=True)
    results = install_results.reset_index(drop=True)
    first: dict[str | None, int] = {}
    for i, p in enumerate(_str_col(results, "package")):
        first.setdefault(p, i)
    ir = results.reindex([first.get(p, -1) for p in _str_col(listed, "package")]).reset_index(
        drop=True
    )
    installed = [v is True for v in _opt_col(ir, "installed")] or [False] * len(ir)
    vers_installed = (
        _str_col(ir, "version_installed") if "version_installed" in ir.columns else [None] * len(ir)
    )
    same = [
        _repro_same_version(have, want)
        for have, want in zip(vers_installed, _str_col(listed, "version"), strict=True)
    ]
    return {
        "listed": listed,
        "results": ir,
        "exact": [i and s for i, s in zip(installed, same, strict=True)],
        "replaced": [i and not s for i, s in zip(installed, same, strict=True)],
        "failed": [not i for i in installed],
    }


def _repro_recorded_summary(
    recorded: Mapping[str, Any] | None, install_results: pd.DataFrame | None
) -> str | None:
    """Port of ``R/reproducibility_check.R::.repro_recorded_summary()``.

    One sentence for the summary and the checklist. ``None`` without recorded versions.
    """
    if recorded is None or install_results is None or len(install_results) == 0:
        return None
    counts = _repro_recorded_counts(recorded, install_results)
    n = len(counts["listed"])
    return (
        f"We installed {sum(counts['exact'])} of the {n} package{plural(n)} the scripts load and "
        "the repository lists at the recorded version, but did not change the R version, so "
        "this is not an exact copy of the authors' setup."
    )


def _repro_recorded_report(
    recorded: Mapping[str, Any] | None,
    install_results: pd.DataFrame | None,
    version_pin: Mapping[str, Any] | None,
    sandbox: str,
    use_declared: bool = False,
) -> list[Any] | None:
    """Port of ``R/reproducibility_check.R::.repro_recorded_report()``.

    The section of the report on the recorded versions a run used: the heading, the
    paragraph and, when a package is not at its recorded version, a table of them. ``None``
    without recorded versions.
    """
    from metacheck.report.blocks import scroll_table

    if recorded is None or install_results is None or len(install_results) == 0:
        return None
    counts = _repro_recorded_counts(recorded, install_results)
    n = len(counts["listed"])
    deps = recorded["deps"]
    not_listed = [
        p
        for p, v in zip(_str_col(deps, "package"), _str_col(deps, "version"), strict=True)
        if v is None
    ]
    odd = [not e for e in counts["exact"]]
    ir = counts["results"]
    notes = _str_col(ir, "version_note") if "version_note" in ir.columns else [""] * len(ir)
    msgs = _str_col(ir, "message") if "message" in ir.columns else [None] * len(ir)
    # nzchar(NA) is TRUE in R: a missing note stays missing
    why = [
        note if (note is None or note != "") else ("" if msg is None else msg)
        for note, msg in zip(notes, msgs, strict=True)
    ]
    vers_installed = (
        _str_col(ir, "version_installed") if "version_installed" in ir.columns else [None] * len(ir)
    )
    table = None
    if any(odd):

        def pick(values: Sequence[Any]) -> list[Any]:
            return [v for v, o in zip(values, odd, strict=True) if o]

        table = _frame(
            {
                "Package": ("string", pick(_str_col(counts["listed"], "package"))),
                "Recorded": ("string", pick(_str_col(counts["listed"], "version"))),
                "Installed": (
                    "string",
                    pick(["not installed" if v is None else v for v in vers_installed]),
                ),
                "Why": ("string", pick(why)),
            }
        )
    n_exact = sum(counts["exact"])
    paragraph = [
        f"The repository records package versions in {_repro_recorded_sources(version_pin)}. "
        f"Of the {n} package{plural(n)} the scripts load and the repository lists, {n_exact} "
        f"{plural(n_exact, 'was', 'were')} installed at the recorded version, "
        f"{sum(counts['replaced'])} got another version, and {sum(counts['failed'])} could not "
        "be installed."
    ]
    if any(counts["replaced"]):
        paragraph.append(
            "A script that uses a package with another version is not an exact reproduction."
        )
    if not_listed:
        names = ", ".join(_na_str(p) for p in not_listed)
        paragraph.append(
            f"The scripts also load {names}, which {plural(len(not_listed), 'is', 'are')} "
            "not listed and got the current version."
        )
    if recorded.get("date_failed") is True:
        date = (recorded["dates"] or [""])[0]
        paragraph.append(f"The versions for the date {date} could not be looked up.")
    paragraph.extend(_repro_recorded_limits(recorded, version_pin, sandbox, use_declared))
    out: list[Any] = ["#### Recorded package versions", " ".join(paragraph)]
    if table is not None:
        out.append(scroll_table(table, maxrows=15))
    return out


def _repro_recorded_n(deps: pd.DataFrame | None, version_pin: Mapping[str, Any] | None) -> int:
    """Port of ``R/reproducibility_check.R::.repro_recorded_n()``.

    How many packages the scripts load have a recorded version, without looking up the
    versions for a date, which needs the network.
    """
    if deps is None or len(deps) == 0:
        return 0
    recorded = _repro_recorded_plan(deps, version_pin, use_dates=False)
    dates = _pin_dates(version_pin)
    if dates:
        return sum(
            b is not True and p not in _TOOLS
            for p, b in zip(_str_col(deps, "package"), _col(deps, "base"), strict=True)
        )
    if recorded is not None:
        return sum(v is not None for v in _str_col(recorded["deps"], "version"))
    return 0


def _repro_recorded_advice(
    deps: pd.DataFrame | None,
    version_pin: Mapping[str, Any] | None,
    run_results: pd.DataFrame | None,
) -> str | None:
    """Port of ``R/reproducibility_check.R::.repro_recorded_advice()``.

    The recommendation after a run in which scripts failed, when the repository records
    versions that the run did not use. ``None`` otherwise. A shared server (the option
    ``metacheck.keys.shared``) does not offer the setting, so there it says so.
    """
    from metacheck.utils import get_option

    failed = {"errored", "timed_out", "dependency_unavailable"}
    if run_results is None or not any(o in failed for o in _opt_col(run_results, "outcome")):
        return None
    if _repro_recorded_n(deps, version_pin) == 0:
        return None
    parts = [
        "Metacheck detected version information that can be used to install specific versions "
        "of software for the reproducibility run.",
        "We recommend retrying the reproducibility check with `recorded_versions = TRUE`.",
        "This will install the required R packages, if still available.",
    ]
    if is_true(get_option("metacheck.keys.shared")):
        parts.append(
            "As this takes a long time, this setting is not implemented on this shared server."
        )
    return " ".join(parts)


def _repro_recorded_preview(
    deps: pd.DataFrame | None, version_pin: Mapping[str, Any] | None
) -> str | None:
    """Port of ``R/reproducibility_check.R::.repro_recorded_preview()``.

    What a run with ``execute = TRUE`` would install, for the report of a run without.
    """
    n = _repro_recorded_n(deps, version_pin)
    if n == 0:
        return None
    return (
        f"The repository records package versions in {_repro_recorded_sources(version_pin)}. "
        f"A run with `execute = TRUE` would install {n} package{plural(n)} the scripts load at "
        "those versions, which can take a long time. It would not change the R version."
    )


# ---------------------------------------------------------------------------
# installing, and checking that what is installed loads
# ---------------------------------------------------------------------------


def _repro_install_order(deps: pd.DataFrame) -> pd.DataFrame:
    """Port of ``R/reproducibility_check.R::.repro_install_order()``.

    The order to install in: first the packages at their current version, then the
    recorded versions. Next to old versions, a current package can fail its own load test
    during the install: ggtree did, in a real repository. A conflict that is left then shows
    in the load check, which can repair it.
    """
    versions = _str_col(deps, "version")
    sources = _str_col(deps, "source")
    current = [v is None or s == "bioc" for v, s in zip(versions, sources, strict=True)]
    order = [i for i, c in enumerate(current) if c] + [i for i, c in enumerate(current) if not c]
    return deps.iloc[order].reset_index(drop=True)


def _repro_load_check_lines(
    lib: str, pkgs: Sequence[str | None], recorded: Sequence[str | None], results_file: str
) -> list[str]:
    r"""Port of ``R/reproducibility_check.R::.repro_load_check_lines()``.

    The script that checks whether the installed packages load. Only the packages the
    scripts load are installed at their recorded versions, next to the current versions of
    everything else. An old version can then keep another package from loading (a current
    ``ggplot2`` next to a recorded ``scales`` stopped with "namespace 'scales' 1.2.1 is
    being loaded, but >= 1.4.0 is required"), or crash R when a current package uses it (a
    recorded ``magrittr`` crashed ``library(tidyverse)`` on R 4.6.1). Both were found in
    real repositories.

    The script loads each package in a new R process, so a crash stops only that process,
    and a namespace that an earlier package loaded does not hide a fault. When a package
    does not load, a recorded version is set aside, so the current version is used: the one
    the error names, or else the first recorded package it depends on (or the package
    itself) without which it loads. A package that does not load without them either is
    reported, and nothing is set aside for it. The script runs without this package, in a
    container or in a new process, and works from R 3.6.

    *lib* is the library the packages were installed in, *pkgs* the packages the scripts
    load, *recorded* the packages installed at a recorded version and *results_file* where
    the script saves its results, after every package: a list of ``set_aside``
    (``package``, ``because``, ``version_installed``, ``note``) and ``not_loading``
    (``package``, ``message``). Returns the lines of the script.
    """
    return [
        "lib <- " + _deparse1_chr([lib]),
        "pkgs <- " + _deparse1_chr(list(pkgs)),
        "recorded <- " + _deparse1_chr(list(recorded)),
        "results_file <- " + _deparse1_chr([results_file]),
        ".libPaths(c(lib, .libPaths()))",
        *_REPOS_LINES,
        # a folder without a DESCRIPTION file is not a package for R
        'aside <- file.path(lib, ".set_aside")',
        "dir.create(aside, showWarnings = FALSE)",
        'load_file <- tempfile(fileext = ".R")',
        'writeLines(c("args <- commandArgs(TRUE)", ".libPaths(c(args[1], .libPaths()))",',
        '             "for (p in args[-1]) suppressPackageStartupMessages(library(p, '
        'character.only = TRUE))"), load_file)',
        # away from the folder of the scripts, which can hold an .Rprofile
        "setwd(tempdir())",
        'quote_arg <- function(x) shQuote(x, type = if (.Platform$OS.type == "windows") "cmd" '
        'else "sh")',
        "try_load <- function(p) {",
        '  out <- suppressWarnings(system2(file.path(R.home("bin"), "Rscript"),',
        "                                  quote_arg(c(load_file, lib, p)), stdout = TRUE, "
        "stderr = TRUE, timeout = 300))",
        '  status <- attr(out, "status")',
        "  list(ok = is.null(status) || identical(as.integer(status), 0L), "
        r'msg = paste(out, collapse = "\n"))',
        "}",
        'in_lib <- function(p) file.exists(file.path(lib, p, "DESCRIPTION"))',
        "can_go <- function() recorded[vapply(recorded, in_lib, logical(1))]",
        # The current version comes from another library, or is installed.
        "other_copy <- function(p) length(find.package(p, lib.loc = setdiff(.libPaths(), lib), "
        "quiet = TRUE)) > 0",
        "use_current <- function(p) {",
        "  file.rename(file.path(lib, p), file.path(aside, p))",
        "  if (!other_copy(p)) try(utils::install.packages(p, lib = lib, quiet = TRUE), "
        "silent = TRUE)",
        "}",
        "put_back <- function(p) {",
        "  unlink(file.path(lib, p), recursive = TRUE)",
        "  file.rename(file.path(aside, p), file.path(lib, p))",
        "}",
        "depends_on <- function(p) {",
        "  db <- utils::installed.packages(lib.loc = .libPaths(), noCache = TRUE)",
        '  db <- db[!duplicated(db[, "Package"]), , drop = FALSE]',
        '  unique(unlist(tools::package_dependencies(p, db = db, which = c("Depends", '
        '"Imports", "LinkingTo"), recursive = TRUE)))',
        "}",
        "named_in <- function(msg) {",
        '  m <- regmatches(msg, regexec("namespace [^A-Za-z]*([A-Za-z][A-Za-z0-9.]*)'
        '[^A-Za-z0-9.]+[0-9.-]+ is (?:being|already) loaded, but",',
        "                               msg, perl = TRUE))[[1]]",
        "  if (length(m) > 1) m[2] else NA_character_",
        "}",
        "have_version <- function(p) {",
        "  v <- suppressWarnings(tryCatch(utils::packageDescription(p, lib.loc = .libPaths(), "
        'fields = "Version"),',
        "                                 error = function(e) NA_character_))",
        "  if (length(v) != 1 || is.na(v)) NA_character_ else as.character(v)",
        "}",
        "set_aside <- list(); not_loading <- list()",
        "save_results <- function() saveRDS(list(set_aside = do.call(rbind, set_aside),",
        "                                        not_loading = do.call(rbind, not_loading)), "
        "results_file)",
        "note <- function(p, because) {",
        '  why <- if (identical(p, because)) "the recorded version does not load on R " else',
        '    paste0("the recorded version keeps ", because, " from loading on R ")',
        "  set_aside[[length(set_aside) + 1L]] <<- data.frame(package = p, because = because,",
        "    version_installed = have_version(p), note = paste0(why, getRversion(), "
        '", so the current version was used"),',
        "    stringsAsFactors = FALSE)",
        "  save_results()",
        "}",
        "repair <- function(p) {",
        "  r <- try_load(p)",
        "  if (r$ok) return(invisible(TRUE))",
        # the package the error names, as often as it names one
        "  repeat {",
        "    named <- named_in(r$msg)",
        "    if (is.na(named) || !named %in% can_go()) break",
        "    use_current(named); note(named, p)",
        "    r <- try_load(p)",
        "    if (r$ok) return(invisible(TRUE))",
        "  }",
        # An error that names nothing, or a crash: what p depends on first, then p.
        "  tries <- intersect(depends_on(p), can_go())",
        "  tries <- tries[vapply(tries, other_copy, logical(1))]",
        "  if (p %in% can_go()) tries <- c(tries, p)",
        "  for (x in tries) {",
        "    use_current(x)",
        "    if (try_load(p)$ok) { note(x, p); return(invisible(TRUE)) }",
        "    put_back(x)",
        "  }",
        "  if (length(tries) > 1) {",
        "    for (x in tries) use_current(x)",
        "    if (try_load(p)$ok) { for (x in tries) note(x, p); return(invisible(TRUE)) }",
        "    for (x in tries) put_back(x)",
        "  }",
        r'  lines <- strsplit(r$msg, "\n", fixed = TRUE)[[1]]',
        "  not_loading[[length(not_loading) + 1L]] <<- data.frame(package = p,",
        '    message = paste0("the package does not load: ", '
        'substr(paste(utils::head(lines[nzchar(lines)], 5), collapse = " "), 1, 400)),',
        "    stringsAsFactors = FALSE)",
        "  save_results()",
        "  invisible(FALSE)",
        "}",
        "for (p in pkgs) repair(p)",
        # A conflict can also show only when the packages are loaded together.
        "for (i in seq_along(pkgs)) {",
        '  r <- try_load(pkgs[!pkgs %in% vapply(not_loading, function(x) x$package, "")])',
        "  named <- named_in(r$msg)",
        "  if (r$ok || is.na(named) || !named %in% can_go()) break",
        '  use_current(named); note(named, "the other packages")',
        "}",
        "save_results()",
    ]


def _frame_from_rds(obj: Any) -> pd.DataFrame | None:
    """A data frame read from an RDS file as a frame of text columns (``None`` for ``NULL``)."""
    from metacheck.datacheck._files_rdata import r_frame_to_pandas

    if obj is None:
        return None
    return r_frame_to_pandas(obj, subset=False)


def _read_found(path: str) -> dict[str, pd.DataFrame | None] | None:
    """``readRDS()`` of the load check's results; ``None`` when it cannot be read."""
    from metacheck.datacheck._files_rdata import STRSXP, VECSXP, RObject, read_rds

    try:
        obj = read_rds(path)
        if not isinstance(obj, RObject) or obj.type != VECSXP:
            return None
        names_attr = obj.attr("names")
        names = (
            list(names_attr.value)
            if isinstance(names_attr, RObject) and names_attr.type == STRSXP
            else []
        )
        parts = dict(zip(names, obj.value or [], strict=False))
        return {
            "set_aside": _frame_from_rds(parts.get("set_aside")),
            "not_loading": _frame_from_rds(parts.get("not_loading")),
        }
    except Exception:
        return None


def _repro_load_check_apply(
    install_results: pd.DataFrame, found: Mapping[str, pd.DataFrame | None]
) -> pd.DataFrame:
    """Port of ``R/reproducibility_check.R::.repro_load_check_apply()``.

    Puts what the load check found in the install results.
    """
    out = install_results.copy()
    packages = _str_col(out, "package")
    gone = found.get("set_aside")
    if gone is not None:
        for pkg, installed, note in zip(
            _str_col(gone, "package"),
            _str_col(gone, "version_installed"),
            _str_col(gone, "note"),
            strict=True,
        ):
            if pkg not in packages:
                continue
            row = out.index[packages.index(pkg)]
            out["version_installed"] = out["version_installed"].astype("string")
            out["version_note"] = out["version_note"].astype("string")
            out.loc[row, "version_installed"] = installed
            out.loc[row, "version_note"] = note
    bad = found.get("not_loading")
    if bad is not None:
        for pkg, message in zip(_str_col(bad, "package"), _str_col(bad, "message"), strict=True):
            if pkg not in packages:
                continue
            row = out.index[packages.index(pkg)]
            out["installed"] = out["installed"].astype("boolean")
            out["message"] = out["message"].astype("string")
            out["category"] = out["category"].astype("string")
            out.loc[row, "installed"] = False
            out.loc[row, "message"] = message
            out.loc[row, "category"] = _repro_classify_install_message(message)
    return out


def _repro_load_check(
    install_results: pd.DataFrame,
    lib_dir: str | os.PathLike[str],
    image: str | None = None,
    timeout: float = 1800,
) -> pd.DataFrame:
    """Port of ``R/reproducibility_check.R::.repro_load_check()``.

    Replace recorded versions that keep the installed packages from loading. Runs the
    script of :func:`_repro_load_check_lines` where the scripts will run (in the Docker
    *image*, or in a new ``Rscript`` process on this machine without one), and puts its
    findings in the install results. Does nothing when no package was installed at a
    recorded version, since then no recorded version can be at fault.

    *install_results* is the result of ``repro_install_deps()`` or
    ``repro_install_deps_docker()``, *lib_dir* the library the packages were installed in
    and *timeout* the time in seconds for the check. Returns *install_results*, with
    ``version_installed`` and ``version_note`` updated for a replaced package, and
    ``installed`` false for a package that does not load.
    """
    lib = os.fspath(lib_dir)
    packages = _str_col(install_results, "package")
    installed = [v is True for v in _col(install_results, "installed")]
    recorded_versions = _str_col(install_results, "version_recorded")
    have_versions = _str_col(install_results, "version_installed")
    # the library is a folder of this machine in Docker too (a bind mount)
    in_lib = [os.path.exists(os.path.join(lib, str(p), "DESCRIPTION")) for p in packages]
    if not any(in_lib):
        return install_results
    at_recorded = [
        lib_ok and inst and want is not None and _repro_same_version(have, want)
        for lib_ok, inst, want, have in zip(
            in_lib, installed, recorded_versions, have_versions, strict=True
        )
    ]
    if not any(at_recorded):
        return install_results
    _message(
        "[repro]   Checking that the installed packages load (", sum(installed), " packages) ..."
    )

    in_docker = image is not None
    sandbox_dir = tempfile.mkdtemp(prefix="repro_load_check_")
    try:
        lib_path = _normalize_path(lib).replace("\\", "/")
        lines = _repro_load_check_lines(
            "/rlib" if in_docker else lib_path,
            [p for p, i in zip(packages, installed, strict=True) if i and p is not None],
            [p for p, a in zip(packages, at_recorded, strict=True) if a and p is not None],
            "/sandbox/results.rds"
            if in_docker
            else _normalize_path(sandbox_dir).replace("\\", "/") + "/results.rds",
        )
        script = os.path.join(sandbox_dir, "check.R")
        with open(script, "wb") as fh:
            fh.write(("\n".join(lines) + "\n").encode("utf-8"))

        if image is not None:
            _repro_docker_hand_over(sandbox_dir)
            _repro_docker_hand_over(lib, tree=False)  # what is in it was written by a container
            container_name = _repro_docker_container_name()
            # as for the install: the network is on, because a replaced package may need
            # installing
            args = [
                "run", "--rm", "--name", container_name,
                "--user", _repro_docker_user(),
                "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges",
                "--pids-limit", "512",
                *_repro_docker_resource_args(),
                "-v", f"{sandbox_dir}:/sandbox",
                "-v", f"{_normalize_path(lib)}:/rlib",
                image, "Rscript", "/sandbox/check.R",
            ]  # fmt: skip
            raw = _docker(args, timeout=timeout)
            res = None if isinstance(raw, Exception) else raw
            if res is None or res.get("timeout"):
                _repro_docker_stop(container_name)
        else:
            with contextlib.suppress(Exception):
                _run_r("\n".join(lines) + "\n", timeout=timeout)

        results = os.path.join(sandbox_dir, "results.rds")
        found = _read_found(results)
    finally:
        shutil.rmtree(sandbox_dir, ignore_errors=True)
    if found is None:
        warnings.warn(
            "reproducibility_check(): the check that the installed packages load did not "
            "finish, so a recorded version may keep a package from loading.",
            UserWarning,
            stacklevel=2,
        )
        return install_results
    return _repro_load_check_apply(install_results, found)


def _repro_load_error(pkg: str, lib_dir: str | os.PathLike[str]) -> str:
    """Port of ``R/reproducibility_check.R::.repro_load_error()``.

    What R says when a package does not load, on one line: often a system library that is
    missing, which "is not loadable" alone does not tell. R loads the package in a new
    process; ``""`` for a package that loads (or when R cannot be run).
    """
    code = "\n".join(
        [
            f"pkg <- {_deparse1_chr([pkg])}",
            f"lib_dir <- {_deparse1_chr([os.fspath(lib_dir)])}",
            "msg <- tryCatch({",
            "  loadNamespace(pkg, lib.loc = c(lib_dir, .libPaths()))",
            '  ""',
            "}, error = function(e) conditionMessage(e))",
            'cat("<<repro-load-error>>", msg, sep = "")',
        ]
    )
    try:
        res = _run_r(code, timeout=300)
    except Exception:
        return ""
    if res is None:
        return ""
    marker = "<<repro-load-error>>"
    if marker not in res.stdout:
        return ""
    msg = res.stdout.rpartition(marker)[2]
    return re.sub(r"\s+", " ", msg)[:300]
