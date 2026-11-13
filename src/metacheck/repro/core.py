"""The reproducibility-check helpers: static analysis and execution.

Port of ``R/reproducibility_check.R``. The module answers "can the paper's
code be run on its data?" in two phases.

STATIC helpers (never run downloaded code) collect the dependencies a run
would need (:func:`repro_dependencies`), rewrite each script's file paths to
the Psych-DS layout (:func:`repro_rewrite_paths`), work out the order the
scripts must run in (:func:`repro_file_io`, :func:`repro_run_order`,
:func:`repro_defined_vars`) and diagnose why a referenced input is
unavailable (:func:`repro_missing_inputs`).

EXECUTION helpers (only reached when the module is called with
``execute=True``) build a throwaway copy of the layout
(:func:`repro_materialize_layout`, :func:`repro_write_scripts`), install the
declared packages into a throwaway library (:func:`repro_install_deps`) and
run each script in an isolated ``Rscript`` subprocess
(:func:`repro_run_scripts`) -- the analogue of metacheck's ``callr::r()``.
Running the authors' R code genuinely needs R: ``Rscript`` is taken from
``METACHECK_RSCRIPT`` (or ``PYTACHECK_RSCRIPT``), else from ``PATH``.

Tables with R list columns hold Python lists in ``object`` columns; R
attributes on a returned data frame are kept in ``DataFrame.attrs``.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import pandas as pd

from metacheck._env import env_get
from metacheck._r.base import as_character, paste, r_round, r_sort_key, slashed, trimws
from metacheck._r.frames import bind_rows
from metacheck._r.regex import (
    gregexpr_all,
    grepl,
    gsub,
    regexec,
    regextract,
    regextract_all,
    strsplit,
    sub,
)
from metacheck._values import as_float, as_str, is_missing
from metacheck.utils import _portable_name as _utils_portable_name
from metacheck.utils import _portable_path as _utils_portable_path

__all__ = [
    "MaterialisedRoot",
    "repro_defined_vars",
    "repro_dependencies",
    "repro_file_io",
    "repro_install_deps",
    "repro_materialize_layout",
    "repro_missing_inputs",
    "repro_rewrite_paths",
    "repro_run_order",
    "repro_run_scripts",
    "repro_write_scripts",
]

# ---------------------------------------------------------------------------
# small R helpers
# ---------------------------------------------------------------------------

_INT_MAX = 2147483647

# A quoted string literal (either quote, backslash escapes), used throughout.
_QUOTED = r"""(['"])((?:[^'"\\]|\\.)*)\1"""
_EXT_END = r"\.[A-Za-z0-9]{1,8}$"


def _is_true(x: Any) -> bool:
    """R ``isTRUE()`` of one logical value."""
    return not is_missing(x) and x is not None and bool(x) is True


def _is_false(x: Any) -> bool:
    """R ``isFALSE()`` of one logical value."""
    return not is_missing(x) and x is not None and bool(x) is False


def _chr_list(x: Any) -> list[str | None]:
    """A character vector (str, sequence, Series, ``None``) as a list."""
    if x is None:
        return []
    if isinstance(x, str):
        return [x]
    if isinstance(x, pd.Series | pd.Index):
        return [as_str(v) for v in x.tolist()]
    if isinstance(x, Iterable):
        return [as_str(v) for v in x]
    return [as_str(x)]


def _col(df: pd.DataFrame, name: str) -> list[Any]:
    """A data-frame column as a plain list (``NA`` -> ``None``)."""
    return [None if is_missing(v) else v for v in df[name].tolist()]


def _na_str(x: str | None) -> str:
    """``paste()`` of one character value (``NA`` pastes as ``"NA"``)."""
    return "NA" if x is None else x


def _paste_lines(lines: Sequence[str | None]) -> str:
    """``paste(lines, collapse = "\\n")`` (``NA`` pastes as ``"NA"``)."""
    return cast(str, paste(lines, collapse="\n"))


def _bs2fs(x: str | None) -> str | None:
    """``gsub("\\\\", "/", x)``."""
    return None if x is None else x.replace("\\", "/")


def _basename(x: str | None) -> str | None:
    """R ``basename()`` on Unix: tilde expansion, trailing slashes dropped."""
    if x is None:
        return None
    if x.startswith("~"):
        x = os.path.expanduser(x)
    return slashed(x).rstrip("/").rpartition("/")[2]


def _tolower(x: str | None) -> str | None:
    return None if x is None else x.lower()


def _norm_base(x: str | None) -> str | None:
    """``tolower(basename(gsub("\\\\", "/", x)))``."""
    return _tolower(_basename(_bs2fs(x)))


def _file_ext(x: str | None) -> str | None:
    """``tools::file_ext()``: the trailing alphanumeric extension, or ``""``."""
    if x is None:
        return None
    m = regextract(r"\.([[:alnum:]]+)$", x)
    return "" if m is None else m[1:]


def _file_path_sans_ext(x: str | None) -> str | None:
    """``tools::file_path_sans_ext()``."""
    out: str | None = sub(r"([^.]+)\.[[:alnum:]]+$", r"\1", x)
    return out


def _unique(xs: Iterable[Any]) -> list[Any]:
    """R ``unique()`` of a vector (first-appearance order)."""
    return list(dict.fromkeys(xs))


def _setdiff(x: Iterable[Any], y: Iterable[Any]) -> list[Any]:
    """R ``setdiff()``: the unique elements of *x* not in *y*."""
    ys = set(y)
    return [v for v in _unique(x) if v not in ys]


def _file_path(*parts: Any) -> str:
    """R ``file.path()``: the parts joined with ``/`` (no normalisation)."""
    return "/".join(str(p) for p in parts)


def _as_int(s: str) -> int | None:
    """``as.integer()`` of a digit run (``NA`` beyond R's integer range)."""
    v = int(s)
    return v if v <= _INT_MAX else None


def _frame(columns: Mapping[str, tuple[str, Sequence[Any]]]) -> pd.DataFrame:
    """A data frame from ``{name: (dtype, values)}`` (dtype ``object`` for list columns)."""
    data: dict[str, pd.Series] = {}
    for name, (dtype, values) in columns.items():
        vals = list(values)
        if dtype == "object":
            s = pd.Series([None] * len(vals), dtype=object)
            for i, v in enumerate(vals):
                s.iat[i] = v
            data[name] = s
        else:
            data[name] = pd.Series(vals, dtype=dtype)
    return pd.DataFrame(data)


def _message(*parts: Any) -> None:
    """metacheck's progress ``message()`` (gated on ``verbose()``)."""
    from metacheck.utils import message

    message(*parts)


def _rscript() -> str | None:
    """``Rscript``: ``METACHECK_RSCRIPT`` (or ``PYTACHECK_RSCRIPT``), else the first on ``PATH``."""
    return env_get("RSCRIPT") or shutil.which("Rscript")


def _r_string(x: str | None) -> str:
    """An R string literal for *x* (``NA`` for ``None``)."""
    if x is None:
        return "NA"
    out = ['"']
    for ch in x:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 32:
            out.append(f"\\{ord(ch):03o}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _r_chr(values: Sequence[str | None]) -> str:
    """An R ``character`` vector literal."""
    if not values:
        return "character(0)"
    return "c(" + ", ".join(_r_string(v) for v in values) + ")"


def _run_r(
    code: str,
    *,
    timeout: float | None = None,
    env: Mapping[str, str] | None = None,
    rscript: str | None = None,
) -> subprocess.CompletedProcess[str] | None:
    """Run R *code* with ``Rscript`` (``None`` when there is no ``Rscript``)."""
    exe = rscript or _rscript()
    if exe is None:
        return None
    with tempfile.TemporaryDirectory(prefix="pytacheck_repro_") as tmp:
        script = Path(tmp) / "code.R"
        script.write_text(code, encoding="utf-8")
        return subprocess.run(  # noqa: S603 - the configured Rscript
            [exe, "--no-save", "--no-restore", str(script)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env={**os.environ, **(env or {})},
            check=False,
        )


def _read_lines(path: str | os.PathLike[str]) -> list[str]:
    """R ``readLines()``: LF, CRLF and CR all end a line."""
    text = Path(path).read_bytes().decode("utf-8", errors="replace")
    if text == "":
        return []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _write_lines(lines: Sequence[str | None], path: str | os.PathLike[str]) -> None:
    """R ``writeLines()``: each line followed by a newline."""
    Path(path).write_text(
        "".join(("NA" if s is None else s) + "\n" for s in lines), encoding="utf-8"
    )


def _code_remove_comments(code_text: Any, lang: str = "R") -> list[str | None]:
    from metacheck.codecheck.core import code_remove_comments

    return list(code_remove_comments(code_text, lang) or [])


def _is_file_list(code_text: Any) -> bool:
    """Whether *code_text* is R's ``is.list()`` of per-file texts."""
    if isinstance(code_text, Mapping):
        return True
    if isinstance(code_text, str) or not isinstance(code_text, list | tuple):
        return False
    return any(not isinstance(v, str) and v is not None for v in code_text)


# ---------------------------------------------------------------------------
# repro_dependencies()
# ---------------------------------------------------------------------------

_GH_PAT = r"""install_github\s*\(\s*['"]([^'"]+)['"]"""
_URL_PAT = r"""install\.packages\s*\(\s*['"](https?://[^'"]+)['"]"""
_SOURCE_RANK = {"github": 0, "url": 1, "cran": 2, "base": 3}


def _deps_frame(
    package: Sequence[Any], source: Sequence[Any], ref: Sequence[Any], base: Sequence[Any]
) -> pd.DataFrame:
    return _frame(
        {
            "package": ("string", package),
            "source": ("string", source),
            "ref": ("string", ref),
            "base": ("boolean", base),
        }
    )


def repro_dependencies(code_text: Any, lang: str = "R") -> pd.DataFrame:
    """Collect the package dependencies a set of code files declare.

    Port of ``R/reproducibility_check.R::repro_dependencies()``. *code_text*
    is one file's code (a string or a sequence of lines) or a list (or
    mapping) of such, pooled across files. Returns columns ``package``,
    ``source`` (``cran``, ``bioc``, ``github``, ``url`` or ``base``), ``ref``
    (the GitHub/URL source, else NA) and ``base`` (a base/recommended
    package), one row per distinct package.
    """
    empty = _deps_frame([], [], [], [])
    if code_text is None:
        return empty

    if _is_file_list(code_text):
        items = list(code_text.values()) if isinstance(code_text, Mapping) else list(code_text)
        parts = [repro_dependencies(ct, lang=lang) for ct in items]
        parts = [d for d in parts if isinstance(d, pd.DataFrame) and len(d) > 0]
        if not parts:
            return empty
        out = bind_rows(parts)
        pkgs = _col(out, "package")
        srcs = _col(out, "source")
        big = len(_SOURCE_RANK)

        def key(i: int) -> tuple[Any, ...]:
            p = pkgs[i]
            s = _SOURCE_RANK.get(srcs[i]) if srcs[i] is not None else None
            return (
                (1,) if p is None else (0, r_sort_key(p)),
                (1, big) if s is None else (0, s),
            )

        order = sorted(range(len(out)), key=key)
        seen: set[Any] = set()
        keep: list[int] = []
        for i in order:
            if pkgs[i] in seen:
                continue
            seen.add(pkgs[i])
            keep.append(i)
        return out.iloc[keep].reset_index(drop=True)

    if lang != "R":
        return empty

    from metacheck.codecheck.core import code_library_names

    names_df = code_library_names(code_text, "R")
    pkgs = _unique(_col(names_df, "package")) if len(names_df) else []
    if not pkgs:
        return empty

    # From the code without its comments, as the package names are: an address in
    # a line made a comment (``# install.packages("http://.../x.tar.gz")``, which
    # no longer answers) was used instead of CRAN (R #598).
    joined = _paste_lines(_code_remove_comments(code_text, "R"))
    gh = regextract_all(_GH_PAT, joined, perl=True)
    url = regextract_all(_URL_PAT, joined, perl=True)
    gh_refs = list(sub(_GH_PAT, r"\1", gh, perl=True))
    url_refs = list(sub(_URL_PAT, r"\1", url, perl=True))
    gh_pkg = list(sub("@.*$", "", [_basename(r) for r in gh_refs]))
    url_pkg = list(sub("[_.].*$", "", [_basename(r) for r in url_refs]))

    base_pkgs = set(_repro_base_packages())
    bioc_pkgs = set(_repro_bioc_packages())

    src = ["cran"] * len(pkgs)
    ref: list[str | None] = [None] * len(pkgs)
    for i, p in enumerate(pkgs):
        if p in base_pkgs:
            src[i] = "base"
        elif p in bioc_pkgs:
            src[i] = "bioc"
    for k, gp in enumerate(gh_pkg):
        for i, p in enumerate(pkgs):
            if p == gp and p is not None:
                src[i] = "github"
                ref[i] = gh_refs[k]
    for k, up in enumerate(url_pkg):
        for i, p in enumerate(pkgs):
            if p == up and p is not None:
                src[i] = "url"
                ref[i] = url_refs[k]

    return _deps_frame(pkgs, src, ref, [p in base_pkgs for p in pkgs])


# The base and recommended packages of a standard R 4.x installation.
_R_BASE_RECOMMENDED = (
    "base",
    "compiler",
    "datasets",
    "graphics",
    "grDevices",
    "grid",
    "methods",
    "parallel",
    "splines",
    "stats",
    "stats4",
    "tcltk",
    "tools",
    "utils",
    "boot",
    "class",
    "cluster",
    "codetools",
    "foreign",
    "KernSmooth",
    "lattice",
    "MASS",
    "Matrix",
    "mgcv",
    "nlme",
    "nnet",
    "rpart",
    "spatial",
    "survival",
)
# R's own fallback when installed.packages() fails.
_R_BASE_FALLBACK = (
    "base",
    "methods",
    "utils",
    "stats",
    "graphics",
    "grDevices",
    "datasets",
    "tools",
)


@functools.cache
def _installed_base_packages(rscript: str) -> tuple[str, ...] | None:
    """``rownames(installed.packages(priority = c("base", "recommended")))`` of *rscript*."""
    code = (
        'ip <- tryCatch(utils::installed.packages(priority = c("base", "recommended")),\n'
        "               error = function(e) NULL)\n"
        'if (is.null(ip)) cat("<<fallback>>\\n") else cat(rownames(ip), sep = "\\n")\n'
    )
    try:
        res = _run_r(code, timeout=120, rscript=rscript)
    except (OSError, subprocess.SubprocessError):
        return None
    if res is None or res.returncode != 0:
        return None
    lines = [s for s in res.stdout.splitlines() if s]
    if lines == ["<<fallback>>"]:
        return _R_BASE_FALLBACK
    return tuple(lines)


def _repro_base_packages() -> list[str]:
    """The base + recommended packages shipped with R.

    Port of ``.repro_base_packages()``: resolved from the R installation that
    would run the code (``PYTACHECK_RSCRIPT``/``Rscript``), so it stays
    correct across R versions; R's own short fallback when
    ``installed.packages()`` fails. Without any R, the standard base and
    recommended packages of R 4.x.
    """
    exe = _rscript()
    if exe:
        found = _installed_base_packages(exe)
        if found is not None:
            return list(found)
    return list(_R_BASE_RECOMMENDED)


_BIOC = (
    "edgeR", "DESeq2", "limma", "phyloseq", "dada2", "LEA", "affy", "affyio",
    "AnnotationDbi", "AnnotationHub", "Biobase", "BiocGenerics",
    "BiocParallel", "BiocVersion", "biomaRt", "Biostrings", "BSgenome",
    "ComplexHeatmap", "ConsensusClusterPlus", "cummeRbund", "DelayedArray",
    "DESeq", "DiffBind", "DOSE", "edgeR", "EnhancedVolcano", "ensembldb",
    "enrichplot", "fgsea", "flowCore", "GenomeInfoDb", "GenomicAlignments",
    "GenomicFeatures", "GenomicRanges", "genefilter", "GEOquery", "ggtree",
    "goseq", "GSEABase", "GSVA", "Gviz", "HTSeq", "IRanges", "KEGGREST",
    "limma", "M3C", "MAGeCKFlute", "makecdfenv", "MassSpecWavelet",
    "MEDIPS", "methylKit", "minfi", "MotifDb", "mzR", "org.Hs.eg.db",
    "org.Mm.eg.db", "pathview", "PICS", "preprocessCore", "qvalue",
    "Rgraphviz", "Rhtslib", "rhdf5", "rtracklayer", "Rsamtools",
    "Rsubread", "S4Vectors", "scater", "scran", "SingleCellExperiment",
    "SummarizedExperiment", "sva", "topGO", "TxDb.Hsapiens.UCSC.hg19.knownGene",
    "TxDb.Hsapiens.UCSC.hg38.knownGene", "variancePartition", "vsn",
    "WGCNA", "XVector", "zlibbioc", "clusterProfiler", "ChIPseeker",
    "ChIPQC", "csaw", "DECIPHER", "deseq2", "monocle", "muscat", "Rbowtie",
    "Rhisat2", "Rsubread", "seqinr", "shinyMethyl", "slingshot",
    "tximport", "tximeta", "biovizBase", "regioneR", "karyoploteR",
    "VariantAnnotation", "STRINGdb", "graph", "RBGL", "limmaGUI",
    "beadarray", "lumi", "oligo", "crlmm", "snpStats",
    "IlluminaHumanMethylation450kanno.ilmn12.hg19",
    "IlluminaHumanMethylationEPICanno.ilm10b4.hg19", "FlowSOM", "CATALYST",
    "diffloop", "GenomicInteractions", "InteractionSet", "bsseq",
    "methylumi", "wateRmelon", "phyloseq", "microbiome", "ANCOMBC",
    "mixOmics", "MOFA2", "BiocSingular", "batchelor", "scDblFinder",
    "scuttle", "zellkonverter", "DropletUtils", "destiny", "MAST",
    "ShortRead", "SNPRelate", "decontam", "arrayQualityMetrics", "baySeq", "EBImage",
    "EBSeq", "gdsfmt", "OmicCircos", "pcaMethods", "rain", "SeqArray",
)  # fmt: skip


def _repro_bioc_packages() -> list[str]:
    """A bundled list of common Bioconductor package names (``.repro_bioc_packages()``)."""
    return _unique(_BIOC)


# ---------------------------------------------------------------------------
# format-string calls, simple string variables, write redirection
# ---------------------------------------------------------------------------

_FORMAT_FN_PAT = r"\b(sprintf|paste0|paste|file\.path)\s*\("
_FORMAT_SPEC_PAT = r"^%[-+ 0#]*[0-9]*(\.[0-9]+)?[diouxXeEfgGaAscp]$"


def _balanced_end(text: str, open_paren: int) -> int | None:
    """1-based position of the ``)`` closing the ``(`` at *open_paren* (quote-aware)."""
    depth = 0
    i = open_paren
    n = len(text)
    in_str: str | None = None
    while i <= n:
        ch = text[i - 1]
        if in_str is not None:
            if ch == "\\":
                i += 1  # skip an escaped char inside the string
            elif ch == in_str:
                in_str = None
        elif ch in ("'", '"'):
            in_str = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _line_of(joined: str, start: int) -> int:
    """The 1-based line a match starting at *start* (1-based) is on."""
    return 1 + joined[:start].count("\n")


def _repro_format_call_refs(code_text: Any) -> pd.DataFrame:
    """Find file paths built at runtime with ``sprintf()``/``paste()``/``paste0()``/``file.path()``.

    Port of ``.repro_format_call_refs()``: columns ``call_text`` (the full
    call), ``fmt`` (the raw format string), ``resolved`` (the format string
    with resolvable ``%s``/``%d`` placeholders substituted, else NA) and
    ``line``.
    """
    rows: list[tuple[str, str, str | None, int]] = []

    def result() -> pd.DataFrame:
        return _frame(
            {
                "call_text": ("string", [r[0] for r in rows]),
                "fmt": ("string", [r[1] for r in rows]),
                "resolved": ("string", [r[2] for r in rows]),
                "line": ("Int64", [r[3] for r in rows]),
            }
        )

    lines = _chr_list(code_text)
    if code_text is None or len(lines) == 0:
        return result()
    nc = _code_remove_comments(lines)
    joined = _paste_lines(nc)
    starts = gregexpr_all(_FORMAT_FN_PAT, joined, perl=True)
    vars_cache: dict[str, str] | None = None
    for start, length in starts:
        open_paren = start + length - 1
        end = _balanced_end(joined, open_paren)
        if end is None:
            continue
        call_text = joined[start - 1 : end]
        args_text = joined[open_paren : end - 1]
        found = gregexpr_all(_QUOTED, args_text, perl=True)
        if not found:
            continue
        q_start, quoted_len = found[0]
        quoted = args_text[q_start - 1 : q_start - 1 + quoted_len]
        fmt = quoted[1:-1]
        if not grepl(_EXT_END, fmt):
            continue
        if grepl(_FORMAT_SPEC_PAT, _basename(_bs2fs(fmt)), perl=True):
            continue
        # the arguments after the quoted string, wherever it stands: R skips
        # quoted_len characters from the start of the arguments, so a call
        # whose file string is not its first argument (paste(dir, "data.csv",
        # sep = "/")) was never a format call and had only its literal
        # rewritten (UPSTREAM_ISSUES U133)
        after_fmt = args_text[q_start - 1 + quoted_len :]
        if not grepl(r"^\s*,", after_fmt, perl=True):
            continue
        line = _line_of(joined, start)
        extra_args = [trimws(a) for a in strsplit(sub(r"^\s*,\s*", "", after_fmt, perl=True), ",")]
        resolved: str | None = None
        if grepl("%[sd]", fmt) and len(extra_args) > 0:
            if vars_cache is None:
                vars_cache = _repro_simple_string_vars(nc)
            vals = [vars_cache.get(a) if a is not None else None for a in extra_args]
            if all(v is not None for v in vals) and len(vals) == len(regextract_all("%[sd]", fmt)):
                r = fmt
                for v in vals:
                    r = sub("%[sd]", str(v), r, perl=True)
                resolved = r
        rows.append((call_text, fmt, resolved, line))
    return result()


_SIMPLE_VAR_PAT = r"""^([.a-zA-Z][.a-zA-Z0-9_]*)\s*(?:<<-|<-|=)\s*(['"])((?:[^'"\\]|\\.)*)\2\s*$"""


def _repro_simple_string_vars(code_text: Any) -> dict[str, str]:
    """Simple top-level ``name <- "literal"`` assignments (the last one per name).

    Port of ``.repro_simple_string_vars()``; R's named character vector is a
    ``dict`` (in the order of each name's last assignment).
    """
    hits = [m for m in regexec(_SIMPLE_VAR_PAT, _chr_list(code_text), perl=True) if len(m) == 4]
    names = [m[1] for m in hits]
    out: dict[str, str] = {}
    for i, m in enumerate(hits):
        if m[1] in names[i + 1 :]:
            continue
        out[m[1]] = m[3]
    return out


_WRITE_FN_PAT = r"\b(write[\._][A-Za-z\._0-9]*|saveRDS|save\.image|save|ggsave|export|fwrite)\s*\("
_ASSIGN_ANY_PAT = r"^([.a-zA-Z][.a-zA-Z0-9_]*)\s*(?:<<-|<-|=)\s*(.+)$"


class _Lang:
    """What the static helpers match differently in R and in Python code."""

    def __init__(
        self, write_fn: str, assign_any: str, ident: str, path_build: str, setwd: str
    ) -> None:
        self.write_fn = write_fn  # a call that writes a file (group 1: its name)
        self.assign_any = assign_any  # a top-level assignment (groups: name, value)
        self.ident = ident  # a bare variable name
        self.path_build = path_build  # a call that builds a path
        self.setwd = setwd  # the call that sets the working directory


def _lang_rules(lang: str) -> _Lang:
    if lang == "Python":
        from metacheck.codecheck.core import _PY_WRITE_FNS

        return _Lang(
            write_fn=r"\b(" + "|".join(_PY_WRITE_FNS) + r")\s*\(",
            assign_any=r"^([A-Za-z_][A-Za-z0-9_]*)\s*=(?!=)\s*(.+)$",
            ident=r"[A-Za-z_][A-Za-z0-9_]*",
            # an f-string or .format() builds a path too, but nothing marks it as one
            path_build=r"^(os\.path\.join|pathlib\.Path|Path)\s*\(",
            setwd="os.chdir",
        )
    return _Lang(
        write_fn=_WRITE_FN_PAT,
        assign_any=_ASSIGN_ANY_PAT,
        ident=r"[.a-zA-Z][.a-zA-Z0-9_]*",
        path_build=r"^(paste0|paste|sprintf|file\.path)\s*\(",
        setwd="setwd",
    )


def _path_like_ext(expr_text: str) -> str | None:
    """The extension the LAST quoted piece of an expression ends in, or ``None``."""
    qs = regextract_all(_QUOTED, expr_text, perl=True)
    if not qs:
        return None
    last_q = qs[-1][1:-1]
    if grepl(_EXT_END, last_q):
        out: str = sub(r".*(\.[A-Za-z0-9]{1,8})$", r"\1", last_q)
        return out
    return None


def _split_args(args_text: str) -> list[dict[str, Any]]:
    from metacheck.statout.r_output import _repro_split_args

    return _repro_split_args(args_text)


def _repro_redirect_writes(code_text: Any, lang: str = "R") -> pd.DataFrame:
    """Find a script's write calls and redirect their targets into the sandbox.

    Port of ``.repro_redirect_writes()``: one row per resolvable write call
    with ``call_text``, ``replacement`` (the call with its target replaced by
    ``"{{REPRO_OUTPUT}}<basename>"``), ``redirected_name`` and ``line``.
    *lang* is ``"R"`` or ``"Python"`` (pandas' ``to_csv()`` and the other
    writers, ``savefig()``, ``np.save()``, ``pickle.dump()``; ``os.path.join()``
    builds a path). Python's ``open(path, "w")`` is left alone: it returns a
    handle, and the path is not the argument of the call that writes.
    """
    rules = _lang_rules(lang)
    rows: list[tuple[str, str, str, int]] = []

    def result() -> pd.DataFrame:
        return _frame(
            {
                "call_text": ("string", [r[0] for r in rows]),
                "replacement": ("string", [r[1] for r in rows]),
                "redirected_name": ("string", [r[2] for r in rows]),
                "line": ("Int64", [r[3] for r in rows]),
            }
        )

    lines = _chr_list(code_text)
    if code_text is None or len(lines) == 0:
        return result()
    nc = _code_remove_comments(lines, lang)
    joined = _paste_lines(nc)
    starts = gregexpr_all(rules.write_fn, joined, perl=True)
    if not starts:
        return result()

    path_like_vars: dict[str, bool] = {}
    var_ext: dict[str, str] = {}
    for a in regexec(rules.assign_any, nc, perl=True):
        if len(a) != 3:
            continue
        ext = _path_like_ext(a[2])
        if ext is not None:
            path_like_vars[a[1]] = True
            var_ext[a[1]] = ext

    for k, (start, length) in enumerate(starts, 1):
        open_paren = start + length - 1
        end = _balanced_end(joined, open_paren)
        if end is None:
            continue
        call_text = joined[start - 1 : end]
        fn_name = regexec(rules.write_fn, call_text, perl=True)[1]
        args_text = joined[open_paren : end - 1]
        line = _line_of(joined, start)

        hit: tuple[int, int, str, str] | None = None
        for arg in _split_args(args_text):
            # the argument keeps its leading space and its name: dropped, save(a, b,
            # file = "ab.RData") became save(a, b, "<out>/ab.RData"), which saves
            # the string as another object (U134, fixed in metacheck 0.4.0)
            name_pat = "^" + rules.ident + r"\s*=\s*(?!=)"
            name = regextract(name_pat, trimws(arg["text"]), perl=True)
            val = trimws(sub(name_pat, "", trimws(arg["text"]), perl=True))
            lead = arg["text"][: len(arg["text"]) - len(arg["text"].lstrip())]
            prefix = lead + ("" if name is None else str(name))
            if regextract(r"""^(['"])((?:[^'"\\]|\\.)*)\1$""", val, perl=True) is not None:
                target = val[1:-1]
                if grepl(_EXT_END, target):
                    hit = (arg["start"], arg["end"], _basename(_bs2fs(target)) or "", prefix)
                    break
                continue
            if grepl("^" + rules.ident + "$", val) and val in path_like_vars:
                hit = (arg["start"], arg["end"], val + var_ext[val], prefix)
                break
            if grepl(rules.path_build, val, perl=True):
                ext = _path_like_ext(val)
                if ext is not None:
                    hit = (arg["start"], arg["end"], f"write_{k}{ext}", prefix)
                    break
        if hit is None:
            continue
        a_start, a_end, redirected, prefix = hit
        replacement_arg = f'{prefix}"{{{{REPRO_OUTPUT}}}}{redirected}"'
        new_args = args_text[: a_start - 1] + replacement_arg + args_text[a_end:]
        rows.append((call_text, f"{fn_name}({new_args})", redirected, line))
    return result()


# ---------------------------------------------------------------------------
# repro_rewrite_paths()
# ---------------------------------------------------------------------------


def _rewrite_frame(rows: Sequence[tuple[Any, ...]]) -> pd.DataFrame:
    return _frame(
        {
            "ref": ("string", [r[0] for r in rows]),
            "basename": ("string", [r[1] for r in rows]),
            "matched": ("boolean", [r[2] for r in rows]),
            "target": ("string", [r[3] for r in rows]),
            "ambiguous": ("boolean", [r[4] for r in rows]),
            "n_candidates": ("Int64", [r[5] for r in rows]),
            "is_call": ("boolean", [r[6] for r in rows]),
        }
    )


def _data_group_from_path(paths: Sequence[str | None]) -> list[str | None]:
    from metacheck.datacheck.files import _data_group_from_path as group

    return group(list(paths))


def _r_dirname(p: str | None) -> str | None:
    """R ``dirname()`` on Unix: ``"."`` for a bare name, trailing slashes dropped."""
    if p is None or p == "":
        return p
    s = p.rstrip("/")
    if s == "":
        return "/"
    i = s.rfind("/")
    if i < 0:
        return "."
    return s[:i].rstrip("/") or "/"


def _split_path(p: str) -> list[str]:
    """``strsplit(p, "/", fixed = TRUE)[[1]]`` (no trailing ``""``)."""
    parts = p.split("/")
    if parts and parts[-1] == "":
        parts.pop()
    return parts


#: tools::md5sum() of files, kept for the session by path, size and time
#: (``.repro_md5_cache``): only the candidates that are compared are hashed (#568).
_MD5_CACHE: dict[tuple[str, int, float], str] = {}


def _repro_md5(paths: Sequence[str | None]) -> list[str | None]:
    """Port of R/reproducibility_check.R::.repro_md5()."""
    out: list[str | None] = []
    for p in paths:
        try:
            st = os.stat(p) if p else None
        except OSError:
            st = None
        if p is None or st is None:
            out.append(None)
            continue
        key = (p, st.st_size, st.st_mtime)
        if key not in _MD5_CACHE:
            try:
                _MD5_CACHE[key] = hashlib.md5(Path(p).read_bytes()).hexdigest()  # noqa: S324
            except OSError:
                out.append(None)
                continue
        out.append(_MD5_CACHE[key])
    return out


def _repro_plan_sources(plan: pd.DataFrame, structure_df: pd.DataFrame | None) -> list[str | None]:
    """Port of R/reproducibility_check.R::.repro_plan_sources().

    The file on disk behind each row of a plan, or ``None``. A plan that names each
    row's own ``source`` uses it; a plan made by ``psychds_file_plan()`` has one row per
    row of *structure_df*, in the same order; any other plan is looked up by file name,
    then by base name. The name lookup alone took the first file of that name, so the
    ``demographics.csv`` of several OSF components all got the first one's content.
    """
    n = len(plan)
    out: list[str | None] = [None] * n
    if not n:
        return out

    def exists_(p: str | None) -> bool:
        return p is not None and p != "" and os.path.exists(p)

    own = _chr_list(plan["source"]) if "source" in plan.columns else [None] * n
    has_own = [exists_(p) for p in own]
    for i in range(n):
        if has_own[i]:
            out[i] = own[i]
    if structure_df is None or not all(
        c in structure_df.columns for c in ("file_name", "file_location")
    ):
        return out
    s_name = _chr_list(structure_df["file_name"])
    s_path = (
        _chr_list(structure_df["file_path"])
        if "file_path" in structure_df.columns
        else [None] * len(structure_df)
    )
    s_name = [
        _basename(sp) if nm is None or nm == "" else nm
        for nm, sp in zip(s_name, s_path, strict=True)
    ]
    s_loc = _chr_list(structure_df["file_location"])
    plan_fn = _chr_list(plan["file_name"])
    if len(structure_df) == n and plan_fn == s_name:
        for i in range(n):
            if not has_own[i] and exists_(s_loc[i]):
                out[i] = s_loc[i]
        return out
    loc: dict[str | None, str | None] = {}
    base: dict[str | None, str | None] = {}
    for fn, fl in zip(_chr_list(structure_df["file_name"]), s_loc, strict=True):
        loc.setdefault(fn, fl)
        base.setdefault(_tolower(_basename(fn)), fl)
    for i in range(n):
        fn = plan_fn[i]
        if has_own[i] or fn is None:
            continue
        if fn in loc and exists_(loc[fn]):
            out[i] = loc[fn]
            continue
        b = _tolower(_basename(fn))
        if b in base and exists_(base[b]):
            out[i] = base[b]
    return out


def _repro_norm_rel(path: str) -> str | None:
    """Port of R/reproducibility_check.R::.repro_norm_rel().

    A relative path with ``.`` and ``..`` resolved (``code/../raw_data/x.csv`` ->
    ``raw_data/x.csv``); ``None`` when it goes above the top.
    """
    out: list[str] = []
    for part in _split_path(path.replace("\\", "/")):
        if part in ("", "."):
            continue
        if part == "..":
            if not out:
                return None
            out.pop()
        else:
            out.append(part)
    return "/".join(out)


def _repro_path_named(
    paths: Sequence[str | None], ref: str, script_path: str | None = None
) -> list[int]:
    """Port of R/reproducibility_check.R::.repro_path_named().

    Which of several same-named files a reference means, by the path it gives: the file
    it names counted from the script's own folder, else from each folder above that,
    nearest first; else the one file sharing the most of the reference's last folders
    (at least two parts). Case does not count. The 0-based index into *paths*, or none
    when that leaves no file or several, since the code is not guessed at.
    """
    low = [None if p is None else p.replace("\\", "/").lower() for p in paths]
    ref = ref.replace("\\", "/").lower()
    if script_path:
        d = cast(str, _r_dirname(script_path.replace("\\", "/").lower()))
        while True:
            from_d = _repro_norm_rel(ref if d in (".", "") else f"{d}/{ref}")
            hit = [] if from_d is None else [i for i, p in enumerate(low) if p == from_d]
            if hit:
                return hit if len(hit) == 1 else []
            if d in (".", "", "/"):
                break
            d = cast(str, _r_dirname(d))
    parts = [p for p in _split_path(ref) if p not in ("", ".", "..")]
    if len(parts) < 2:
        return []
    shared: list[int] = []
    for p in low:
        if p is None:
            shared.append(0)
            continue
        pp = _split_path(p)
        n = min(len(pp), len(parts))
        if n == 0:
            shared.append(0)
            continue
        same = [a == b for a, b in zip(pp[::-1][:n], parts[::-1][:n], strict=True)]
        shared.append(n if all(same) else same.index(False))
    top = max(shared) if shared else 0
    best = [i for i, s in enumerate(shared) if s == top]
    return best if top >= 2 and len(best) == 1 else []


_CODE_FILE_PAT = r"\.(r|rmd|qmd|py|ipynb|m|do|jl|sas|sps)$"


def _repro_nearest_copy(
    paths: Sequence[str | None],
    script_path: str | None = None,
    all_paths: Sequence[str | None] = (),
) -> list[int]:
    """Port of R/reproducibility_check.R::.repro_nearest_copy().

    Which of several same-named files a script means when neither the reference's path
    nor the study tells. A copy inside a folder with scripts of its own, which the
    script is not in, belongs to another project of the deposit (#575, item 3). Of the
    copies left, the one whose folder shares the most folders from the top with the
    script's own folder, when that is one copy and at least one folder. The 0-based
    index into *paths*, or none.
    """
    if not script_path or len(paths) < 2 or any(p is None for p in paths):
        return []

    def norm(p: str) -> str:
        return re.sub(r"^(\./|/)+", "", p.replace("\\", "/").lower())

    cpaths = [norm(cast(str, p)) for p in paths]
    script_dir = cast(str, _r_dirname(norm(script_path)))
    code = [
        norm(p) for p in all_paths if p is not None and re.search(_CODE_FILE_PAT, p, re.IGNORECASE)
    ]

    def inside(p: str, f: str) -> bool:
        return p.startswith(f + "/")

    def other_project(p: str) -> bool:
        d = cast(str, _r_dirname(p))
        while d not in (".", "", "/"):
            if any(inside(c, d) for c in code) and not inside(script_dir + "/x", d):
                return True
            d = cast(str, _r_dirname(d))
        return False

    keep = [i for i, p in enumerate(cpaths) if not other_project(p)]
    if len(keep) == 1:
        return keep
    if not keep:
        return []
    sd = [] if script_dir in (".", "") else _split_path(script_dir)
    shared: list[int] = []
    for i in keep:
        d = cast(str, _r_dirname(cpaths[i]))
        pd_ = [] if d in (".", "") else _split_path(d)
        n = min(len(pd_), len(sd))
        if n == 0:
            shared.append(0)
            continue
        same = [a == b for a, b in zip(pd_[:n], sd[:n], strict=True)]
        shared.append(n if all(same) else same.index(False))
    top = max(shared)
    best = [k for k, s in zip(keep, shared, strict=True) if s == top]
    return best if top >= 1 and len(best) == 1 else []


def _repro_name_key(x: str | None) -> str:
    """Port of R/reproducibility_check.R::.repro_name_key().

    A file name for comparing spellings: no extension, case, spaces or punctuation. A
    separator between two digits stays, so ``s1_2`` and ``s12`` remain different files.
    """
    s = (x or "").encode("utf-8", "ignore").decode("utf-8")
    s = (_file_path_sans_ext(s) or "").lower()
    s = gsub(r"([0-9])[^[:alnum:]]+(?=[0-9])", r"\1|", s, perl=True)
    out: str = gsub(r"[^[:alnum:]|]", "", s)
    return out


def _r_call_parts(e: Any) -> list[Any]:
    """``.repro_call_parts()``: a call's function and arguments, without empty ones."""
    from metacheck.codecheck._rparse import MISSING

    return [e.fun] + [a[1] for a in e.args if a[1] is not MISSING]


def _repro_assigned_names(code_text: Sequence[str | None], lang: str = "R") -> list[str]:
    """Port of R/reproducibility_check.R::.repro_assigned_names().

    Text assigned to a variable, alone or in ``c()``: names a later call may read, as in
    ``f <- c("NetworkFunction.R")`` and later ``source(f)``.
    """
    if lang == "Python":
        pat = r"""^\s*[A-Za-z_][A-Za-z0-9_.]*\s*=\s*["']([^"']+)["']\s*(#.*)?$"""
        return _unique(m[1] for m in regexec(pat, list(code_text)) if len(m) > 1)
    from metacheck.codecheck._rparse import Const, Lang, RParseError, Sym, parse_exprs

    try:
        exprs = parse_exprs(list(code_text))
    except (RParseError, RecursionError):
        return []
    found: list[str] = []

    def text_of(x: Any) -> list[str]:
        if isinstance(x, Const) and x.kind == "character" and not x.na:
            return [x.value]
        if isinstance(x, Lang) and isinstance(x.fun, Sym) and x.fun.name == "c":
            return [t for a in _r_call_parts(x)[1:] for t in text_of(a)]
        return []

    def walk(e: Lang) -> None:
        if isinstance(e.fun, Sym) and e.fun.name in ("<-", "=", "<<-") and len(e.args) == 2:
            found.extend(text_of(e.args[1][1]))
        for a in _r_call_parts(e):
            if isinstance(a, Lang):
                walk(a)

    for e in exprs:
        if isinstance(e, Lang):
            walk(e)
    return _unique(f for f in found if f != "")


_PASTE_FUNS = ("paste", "paste0", "base::paste", "base::paste0")


def _repro_built_names(code_text: Sequence[str | None] | None, lang: str = "R") -> list[str]:
    """Port of R/reproducibility_check.R::.repro_built_names().

    File names a script joins to a variable, as lower-case base names: the literal parts
    of ``paste(x, "field.csv")``, ``paste0(x, "/field.csv")`` and, in Python,
    ``x + "field.csv"``. The whole name is known only when the code runs. A script that
    does not parse gives none.
    """
    if not code_text:
        return []
    found: list[str | None] = []
    if lang == "Python":
        nc = _paste_lines(_code_remove_comments(list(code_text), "Python"))
        hits = regextract_all(r"""\+\s*(['"])[^'"\n]+\1""", nc, perl=True)
        found = [re.sub(r"""^\+\s*['"]|['"]$""", "", h) for h in hits]
    else:
        from metacheck.codecheck._rparse import Const, Lang, RParseError, Sym, parse_exprs

        try:
            exprs = parse_exprs(list(code_text))
        except (RParseError, RecursionError):
            exprs = []

        def fun_name(f: Any) -> str | None:
            if isinstance(f, Sym):
                return f.name
            if (
                isinstance(f, Lang)
                and isinstance(f.fun, Sym)
                and f.fun.name == "::"
                and len(f.args) == 2
                and all(isinstance(a[1], Sym) for a in f.args)
            ):
                return f"{f.args[0][1].name}::{f.args[1][1].name}"
            return None

        def walk(e: Lang) -> list[str | None]:
            out: list[str | None] = []
            if fun_name(e.fun) in _PASTE_FUNS:
                args = [a[1] for a in e.args if a[0] is None or str(a[0]) == ""]
                literal = [isinstance(a, Const) and a.kind == "character" for a in args]
                if not all(literal):
                    out.extend(
                        None if a.na else a.value
                        for a, lit in zip(args, literal, strict=True)
                        if lit
                    )
            for part in [e.fun] + [a[1] for a in e.args]:
                if isinstance(part, Lang):
                    out.extend(walk(part))
            return out

        for e in exprs:
            if isinstance(e, Lang):
                found.extend(walk(e))
    return _unique(_norm_base(f) for f in found)


_JOIN_CALLS = r"\b(?:file\.path|paste0?|os\.path\.join|here|Path|sprintf|glue|str_c)\s*\([^()\n]*?"


def _re_quote(x: str) -> str:
    """``gsub("([.\\\\+*?^$()|{}\\[\\]])", "\\\\\\1", x)``: a name as a regex."""
    return re.sub(r"([.\\+*?^$()|{}\[\]])", r"\\\1", x)


def _ref_in_join(ref: str, joined_text: str) -> bool:
    """Whether a name stands in a call that joins it to a folder of the code's own.

    ``file.path(dir, "x.csv")``, ``os.path.join(base, "x.csv")``, or a variable holding
    the name that such a call, or an f-string after a ``/``, joins to a folder later
    (#568).
    """
    q = _re_quote(ref)
    if re.search(_JOIN_CALLS + "[\"']" + q + "[\"']", joined_text):
        return True
    assigned = [
        m.group(0)
        for m in re.finditer(
            r"(?m)^\s*([A-Za-z_.][A-Za-z0-9_.]*)(?:\[[^]\n]*\])?\s*(?:<-|=)\s*[\"']" + q + "[\"']",
            joined_text,
        )
    ]
    variables = _unique(
        re.sub(r"^\s*([A-Za-z_.][A-Za-z0-9_.]*).*$", r"\1", a, flags=re.S) for a in assigned
    )
    for v in variables:
        vq = v.replace(".", "\\.")
        if re.search(_JOIN_CALLS + r"\b" + vq + r"\b", joined_text) or re.search(
            r"/\{" + vq + r"\}", joined_text
        ):
            return True
    return False


def _ref_in_here(ref: str, joined_text: str) -> bool:
    """Whether the path is the one argument of ``here()``."""
    q = _re_quote(ref)
    return re.search(r"\bhere(?:::here)?\s*\(\s*[\"']" + q + r"[\"']\s*\)", joined_text) is not None


def repro_rewrite_paths(
    code_text: Any,
    file_name: str,
    plan: pd.DataFrame | None,
    lang: str = "R",
    structure_df: pd.DataFrame | None = None,
    script_path: str | None = None,
) -> pd.DataFrame:
    """Rewrite a code file's data-file paths to the deposit's layout.

    Port of ``R/reproducibility_check.R::repro_rewrite_paths()``. References are
    matched to *plan* rows (``file_name``, ``target_path``, ``current_path`` and, for
    converted tabular files, ``original_target``) by basename. Several files of one
    name are told apart by the path the reference gives (counted from
    *script_path*'s folder or a folder above it), by study group, and by the copy
    nearest the script; with *structure_df*, byte-identical mirrors count as one. A
    name held in a variable (``f <- "x.R"``, then ``source(f)``) counts when it
    resolves to one file. A reference with no exact match also matches the one plan
    file with the same extension and the same name apart from case, spaces and
    punctuation.

    Columns ``ref``, ``basename``, ``matched``, ``target``, ``ambiguous``,
    ``n_candidates``, ``is_call``, ``name_in_code`` and ``name_in_deposit`` (both
    names when they differ), ``in_join`` (the name is joined to a folder of the code's
    own) and ``in_here`` (the path is the one argument of ``here()``); the empty frame
    has the first seven. *lang* is ``"R"`` or ``"Python"``; for Python only quoted
    paths are rewritten.
    """
    empty = _rewrite_frame([])
    if lang not in ("R", "Python") or code_text is None:
        return empty
    if (
        plan is None
        or len(plan) == 0
        or not all(c in plan.columns for c in ("file_name", "target_path"))
    ):
        return empty

    from metacheck.codecheck.core import code_file_refs

    code_lines = _chr_list(code_text)
    # code_file_refs() leaves out the files the code writes (R 0.4.3.28)
    refs = list(code_file_refs(code_text, lang))
    plan_file = _chr_list(plan["file_name"])
    plan_base = [_norm_base(f) for f in plan_file]
    plan_base_set = set(plan_base)
    assigned = [
        a
        for a in _setdiff(_repro_assigned_names(code_lines, lang), refs)
        if _norm_base(a) in plan_base_set
    ]
    refs = refs + assigned
    call_refs = (
        _repro_format_call_refs(code_text)
        if lang == "R"
        else _frame({c: ("string", []) for c in ("call_text", "fmt", "resolved")})
    )
    if len(call_refs) > 0:
        refs = _setdiff(refs, _col(call_refs, "fmt"))
    if len(refs) == 0 and len(call_refs) == 0:
        return empty
    ref_base = [_norm_base(r) or "" for r in refs]
    ref_ext = [_tolower(_file_ext(b)) or "" for b in ref_base]

    plan_target = _chr_list(plan["target_path"])
    n_plan = len(plan)
    has_target = [t is not None and t != "" for t in plan_target]
    orig_target = (
        _chr_list(plan["original_target"]) if "original_target" in plan.columns else [None] * n_plan
    )
    orig_ext = [_tolower(_file_ext(o or "")) for o in orig_target]

    def pick_target(i: int, ref_e: str) -> str | None:
        o = orig_target[i]
        if o is not None and o != "" and ref_e != "" and ref_e == orig_ext[i]:
            return o
        return plan_target[i]

    script_grp = _data_group_from_path([file_name])[0]

    plan_current: list[str | None] = (
        [
            None if c is None else re.sub(r"^(\./|/)+", "", c.replace("\\", "/"))
            for c in _chr_list(plan["current_path"])
        ]
        if "current_path" in plan.columns
        else [None] * n_plan
    )

    def named_by_path(cand: list[int], ref: str) -> list[int]:
        return [
            cand[k] for k in _repro_path_named([plan_current[c] for c in cand], ref, script_path)
        ]

    def nearest(cand: list[int]) -> list[int]:
        return [
            cand[k]
            for k in _repro_nearest_copy([plan_current[c] for c in cand], script_path, plan_current)
        ]

    plan_source_path: list[str | None] = (
        _repro_plan_sources(plan, structure_df)
        if structure_df is not None
        and all(c in structure_df.columns for c in ("file_name", "file_location"))
        else [None] * n_plan
    )
    plan_hash: list[str | None] = [None] * n_plan
    hashed = [False] * n_plan

    def hash_rows(rows_: list[int]) -> list[str | None]:
        todo = [r for r in rows_ if not hashed[r] and plan_source_path[r] is not None]
        if todo:
            for r, h in zip(todo, _repro_md5([plan_source_path[r] for r in todo]), strict=True):
                plan_hash[r] = h
        for r in rows_:
            hashed[r] = True
        return [plan_hash[r] for r in rows_]

    def collapse_by_hash(cand: list[int]) -> list[int]:
        if len(cand) < 2:
            return cand
        h = hash_rows(cand)
        with_hash = [c for c, x in zip(cand, h, strict=True) if x is not None]
        if len(with_hash) < 2:
            return cand
        keep = [c for c, x in zip(cand, h, strict=True) if x is None]
        hash_of = dict(zip(cand, h, strict=True))
        for grp_h in _unique(x for x in h if x is not None):
            grp_cand = [c for c in with_hash if hash_of[c] == grp_h]
            if len(grp_cand) == 1:
                keep.append(grp_cand[0])
                continue
            grp_of = _data_group_from_path([plan_target[c] for c in grp_cand])
            # the group most mirrors agree on; a tie keeps the first listed group (U135)
            counts: dict[str, int] = {}
            for g in grp_of:
                if g is not None:
                    counts[g] = counts.get(g, 0) + 1
            if counts:
                best = max(counts, key=lambda g: counts[g])  # first of the maxima
                rep_i = next(c for c, g in zip(grp_cand, grp_of, strict=True) if g == best)
            else:
                rep_i = grp_cand[0]
            keep.append(rep_i)
        return keep

    # A file spelled differently in the code than in the deposit (relative cover.csv,
    # relative_cover.csv): the one plan row with the same extension and the same name
    # apart from case, spaces and punctuation. A piece of a name, a placeholder, a name
    # built from a variable or a file the script writes itself is not compared.
    plan_key = [_repro_name_key(b) for b in plan_base]
    plan_ext = [_tolower(_file_ext(b or "")) for b in plan_base]
    io = repro_file_io({"script": code_lines}, lang)
    io_writes = [w for ws in (io["writes"].tolist() if "writes" in io.columns else []) for w in ws]
    not_compared = set(_repro_built_names(code_lines, lang)) | set(io_writes)

    def other_spelling(base: str, literal: bool = True) -> int | None:
        if (literal and base.lower() in not_compared) or grepl(r"^[._]|[%{}$*]", base):
            return None
        ext = _file_ext(base) or ""
        if ext == "":
            return None
        key = _repro_name_key(base)
        if key == "":
            return None
        cand = collapse_by_hash(
            [
                j
                for j in range(n_plan)
                if plan_key[j] == key and plan_ext[j] == ext.lower() and has_target[j]
            ]
        )
        return cand[0] if len(_unique(plan_target[c] for c in cand)) == 1 else None

    # which(plan_base == <basename> & has_target), indexed once
    plan_index: dict[str, list[int]] = {}
    for j in range(n_plan):
        if has_target[j] and plan_base[j] is not None:
            plan_index.setdefault(cast(str, plan_base[j]), []).append(j)

    def resolve(
        ref: str,
        key_base: str,
        key_ext: str,
        key_path: str,
        cand: list[int],
        is_call: bool,
        named: bool,
    ) -> tuple[Any, ...]:
        n = len(cand)
        if n == 0:
            other = other_spelling(key_base, literal=not is_call)
            if other is not None:
                return (ref, key_base, True, pick_target(other, key_ext), False, 1, is_call)
            return (ref, key_base, False, None, False, 0, is_call)
        if n == 1:
            return (ref, key_base, True, pick_target(cand[0], key_ext), False, 1, is_call)
        cand_targets = _unique(pick_target(c, key_ext) for c in cand)
        if len(cand_targets) == 1:
            return (ref, key_base, True, cand_targets[0], False, n, is_call)
        own = named_by_path(cand, key_path) if named else []
        if len(own) == 1:
            return (ref, key_base, True, pick_target(own[0], key_ext), False, n, is_call)
        cand_grp = _data_group_from_path([plan_target[c] for c in cand])
        ref_grp = _data_group_from_path([key_path])[0]
        pick: list[int] = []
        if script_grp is not None:
            pick = [c for c, g in zip(cand, cand_grp, strict=True) if g == script_grp]
        if len(pick) != 1 and ref_grp is not None:
            pick = [c for c, g in zip(cand, cand_grp, strict=True) if g == ref_grp]
        if len(pick) != 1:
            pick = nearest(cand)
        if len(pick) == 1:
            return (ref, key_base, True, pick_target(pick[0], key_ext), False, n, is_call)
        return (ref, key_base, True, None, True, n, is_call)

    rows: list[tuple[Any, ...]] = []
    for i, r in enumerate(refs):
        cand = collapse_by_hash(list(plan_index.get(ref_base[i], [])))
        rows.append(resolve(r, ref_base[i], ref_ext[i], r, cand, False, True))

    call_name_of: dict[str | None, str | None] = {}
    for call_text, fmt, resolved in zip(
        _col(call_refs, "call_text"),
        _col(call_refs, "fmt"),
        _col(call_refs, "resolved"),
        strict=True,
    ):
        key = resolved if resolved is not None else fmt
        key_base = _norm_base(key) or ""
        key_ext = _tolower(_file_ext(key_base)) or ""
        cand = list(plan_index.get(key_base, []))
        rows.append(resolve(call_text, key_base, key_ext, key, cand, True, resolved is not None))
        call_name_of.setdefault(call_text, _basename(_bs2fs(key)))

    # a name in a variable counts only when it resolves to one file
    assigned_set = set(assigned)
    rows = [r for r in rows if not (r[0] in assigned_set and not r[6] and (not r[2] or r[4]))]

    # Where the name in the code is not the name in the deposit, both names, so the
    # report can tell the authors. Only the name's case counts here too.
    first_of_target: dict[str | None, int] = {}
    for j, t in enumerate(plan_target):
        first_of_target.setdefault(t, j)
    first_of_orig: dict[str | None, int] = {}
    for j, t in enumerate(orig_target):
        first_of_orig.setdefault(t, j)
    name_in_code: list[str | None] = []
    name_in_deposit: list[str | None] = []
    for row in rows:
        ref, target, ambiguous, is_call = row[0], row[3], row[4], row[6]
        in_code = call_name_of.get(ref) if is_call else _basename(_bs2fs(ref))
        built = is_call and (in_code is None or grepl(r"[%{}$*]", in_code))
        listed: str | None = None
        if not built:
            # match() finds an NA target too, as R does
            at = first_of_target.get(target)
            listed = None if at is None else plan_file[at]
            if listed is None and target is not None:
                at = first_of_orig.get(target)
                listed = None if at is None else plan_file[at]
        listed = _basename(_bs2fs(listed))
        differs = listed is not None and in_code is not None and not ambiguous and listed != in_code
        name_in_code.append(in_code if differs else None)
        name_in_deposit.append(listed if differs else None)

    joined_text = "\n".join("NA" if x is None else x for x in code_lines)
    in_join = [False if r[6] else _ref_in_join(r[0], joined_text) for r in rows]
    in_here = [False if r[6] else _ref_in_here(r[0], joined_text) for r in rows]
    out = _rewrite_frame(rows)
    out["name_in_code"] = pd.Series(name_in_code, dtype="string")
    out["name_in_deposit"] = pd.Series(name_in_deposit, dtype="string")
    out["in_join"] = pd.Series(in_join, dtype="boolean")
    out["in_here"] = pd.Series(in_here, dtype="boolean")
    return out


# ---------------------------------------------------------------------------
# repro_run_order(), repro_file_io(), repro_defined_vars()
# ---------------------------------------------------------------------------


def _as_names(x: Any) -> list[str | None]:
    """One list-column cell (a character vector) as a list."""
    if x is None:
        return []
    if isinstance(x, str):
        return [x]
    if isinstance(x, float) and x != x:
        return [None]
    return _chr_list(x)


def _repro_normalize_basename(b: Any) -> Any:
    """Normalise a basename for fuzzy ``source()`` matching (``.repro_normalize_basename()``).

    Lowercase, drop the extension and a leading numbering prefix, and remove
    every separator: ``"001 - data prep.R"`` and ``"103 - Data Prep.R"`` both
    give ``"dataprep"``. Vectorised like R.
    """
    single = isinstance(b, str) or b is None
    vals = [b] if single else _chr_list(b)
    out = [_tolower(_file_path_sans_ext(_tolower(v))) for v in vals]
    out = list(sub(r"^[0-9]+[\s._-]*", "", out, perl=True))
    out = list(gsub(r"[\s._-]+", "", out, perl=True))
    return out[0] if single else out


def repro_run_order(
    files: pd.DataFrame | None, extra_edges: Sequence[Sequence[str]] | None = None
) -> pd.DataFrame:
    """Determine the order code files must run in.

    Port of ``R/reproducibility_check.R::repro_run_order()``. *files* has a
    ``file_name`` column and ``reads``/``writes``/``sources`` list columns of
    basenames (as :func:`repro_file_io` returns); a missing one is derived
    from a ``code_text`` column when there is one, as metacheck documents
    (its code treats it as empty, UPSTREAM_ISSUES U135). *extra_edges* are
    ``(from_file, to_file)`` pairs. Returns ``file_name``, ``order``,
    ``depends_on`` and ``order_basis``; ``attrs["cycle"]`` (files in a
    dependency cycle), ``attrs["ambiguous"]`` and ``attrs["fuzzy_sources"]``
    (a ``from``/``to`` frame of normalised-basename ``source()`` matches)
    carry R's attributes.
    """
    if files is None or len(files) == 0:
        return _frame(
            {
                "file_name": ("string", []),
                "order": ("Int64", []),
                "depends_on": ("string", []),
                "order_basis": ("string", []),
            }
        )
    n = len(files)
    fname = _chr_list(files["file_name"])
    base_name = [_norm_base(f) for f in fname]

    derived: pd.DataFrame | None = None
    if "code_text" in files.columns and not {"reads", "writes", "sources"} <= set(files.columns):
        texts = files["code_text"].tolist()
        # a file without code text (NA, not read) has no reads/writes/sources
        parts = [
            _frame({"file_name": ("string", [])})
            if is_missing(texts[i])
            else repro_file_io({f"row{i}": texts[i]})
            for i in range(n)
        ]
        derived = pd.DataFrame(
            {
                col: [p[col].iloc[0] if col in p.columns and len(p) else [] for p in parts]
                for col in ("reads", "writes", "sources")
            }
        )

    def io_col(col: str) -> list[list[str | None]]:
        if col in files.columns:
            return [_as_names(v) for v in files[col].tolist()]
        if derived is not None:
            return [_as_names(v) for v in derived[col].tolist()]
        return [[] for _ in range(n)]

    reads, writes, sources = io_col("reads"), io_col("writes"), io_col("sources")
    paths = _chr_list(files["file_path"]) if "file_path" in files.columns else fname
    source_refs = io_col("source_refs") if "source_refs" in files.columns else sources

    before: list[list[int]] = [[] for _ in range(n)]

    def union_into(i: int, js: Iterable[int]) -> None:
        for j in js:
            if j not in before[i]:
                before[i].append(j)

    # writer_of[[w]]: R's list lookup by name never finds "" or NA, so an empty
    # or missing basename is never written by anyone.
    writer_of: dict[str, list[int]] = {}
    for j in range(n):
        for w in writes[j]:
            key = _tolower(w)
            if key is not None and key != "":
                writer_of.setdefault(key, []).append(j)
    for i in range(n):
        for r in reads[i]:
            key = _tolower(r)
            writers = writer_of.get(key, []) if key is not None else []
            writers = _setdiff(writers, [i])
            if writers:
                union_into(i, writers)

    fuzzy: list[tuple[str | None, str | None]] = []
    norm_base = _repro_normalize_basename(base_name)
    by_base: dict[str, list[int]] = {}
    by_norm: dict[str, list[int]] = {}
    for j in range(n):
        if base_name[j] is not None:
            by_base.setdefault(cast(str, base_name[j]), []).append(j)
        if norm_base[j] is not None and norm_base[j] != "":
            by_norm.setdefault(norm_base[j], []).append(j)
    source_edges: list[tuple[str | None, str | None]] = []
    for i in range(n):
        for k, s in enumerate(sources[i]):
            s = _tolower(s)
            js = _setdiff(by_base.get(s, []) if s is not None else [], [i])
            # several files with this name: only the one the call names
            if len(js) > 1:
                ref = source_refs[i][k] if k < len(source_refs[i]) else None
                js = _repro_sourced_file(js, ref, paths, i)
            if not js:
                s_norm = _repro_normalize_basename(s)
                js = _setdiff(
                    by_norm.get(s_norm, []) if s_norm is not None and s_norm != "" else [], [i]
                )
                if len(js) == 1:
                    fuzzy.append((fname[js[0]], fname[i]))
                elif len(js) > 1:
                    js = []
            if js:
                union_into(i, js)
                source_edges.extend((fname[jj], fname[i]) for jj in js)

    by_name: dict[str, list[int]] = {}
    for j, f in enumerate(fname):
        if f is not None:
            by_name.setdefault(f, []).append(j)
    for e in extra_edges or []:
        e = list(e)
        frm = by_name.get(e[0], []) if e[0] is not None else []
        to = by_name.get(e[1], []) if e[1] is not None else []
        frm = _setdiff(frm, to)
        if frm and to:
            union_into(to[0], frm)

    after: list[list[int]] = [[] for _ in range(n)]
    for i in range(n):
        for j in before[i]:
            after[j].append(i)
    indeg = [len(b) for b in before]

    digit_runs = [
        [_as_int(d) for d in regextract_all("[0-9]+", b)] if b is not None else []
        for b in base_name
    ]
    has_num = [len(d) > 0 for d in digit_runs]
    maxk = max([0, *(len(d) for d in digit_runs)])
    ncol = maxk if maxk > 0 else 1

    def sort_key(v: int) -> tuple[Any, ...]:
        runs = digit_runs[v] if maxk > 0 else []
        parts: list[tuple[Any, ...]] = []
        for k in range(ncol):
            x = runs[k] if k < len(runs) else None
            parts.append((1, 0) if x is None else (0, x))
        b = base_name[v]
        parts.append((1,) if b is None else (0, r_sort_key(b)))
        return tuple(parts)

    def tie_key(v: list[int]) -> list[int]:
        return sorted(v, key=sort_key)

    order_idx: list[int] = []
    placed = [False] * n
    ready = tie_key([i for i in range(n) if indeg[i] == 0])
    while ready:
        node = ready.pop(0)
        order_idx.append(node)
        placed[node] = True
        newly: list[int] = []
        for i in after[node]:
            indeg[i] -= 1
            if indeg[i] == 0:
                newly.append(i)
        if newly:
            ready = tie_key(ready + newly)
    cycle_nodes = [i for i in range(n) if not placed[i]]

    had_edge = [len(b) > 0 for b in before]
    any_dependency = any(had_edge)
    basis = [
        "dependency" if had_edge[i] else ("numeric" if has_num[i] else "none") for i in range(n)
    ]
    ord_: list[int | None] = [None] * n
    for pos, i in enumerate(order_idx, 1):
        ord_[i] = pos
    depends_on = [", ".join(_na_str(fname[j]) for j in b) for b in before]

    out = _frame(
        {
            "file_name": ("string", fname),
            "order": ("Int64", ord_),
            "depends_on": ("string", depends_on),
            "order_basis": ("string", basis),
        }
    )
    out.attrs["cycle"] = [fname[i] for i in cycle_nodes]
    out.attrs["ambiguous"] = n > 1 and not any_dependency and not any(has_num)
    out.attrs["fuzzy_sources"] = _frame(
        {"from": ("string", [f[0] for f in fuzzy]), "to": ("string", [f[1] for f in fuzzy])}
    )
    # every source() edge, exact and fuzzy: `from` is the sourced file, `to` the file
    # that sources it (the module runs a sourced script only inside that one)
    out.attrs["source_edges"] = _frame(
        {
            "from": ("string", [f[0] for f in source_edges]),
            "to": ("string", [f[1] for f in source_edges]),
        }
    )
    return out


def _repro_sourced_file(
    cand: Sequence[int], ref: str | None, paths: Sequence[str | None], frm: int
) -> list[int]:
    """Port of R/reproducibility_check.R::.repro_sourced_file().

    Which of several same-named files *cand* a ``source()`` reference *ref* in script
    *frm* names (see :func:`_repro_path_named`).
    """
    if ref is None:
        return []
    return [cand[k] for k in _repro_path_named([paths[c] for c in cand], ref, paths[frm])]


def _code_text_items(code_text_list: Any) -> tuple[list[str], list[Any]]:
    """Names and values of a named list of per-file code texts."""
    if isinstance(code_text_list, Mapping):
        return [str(k) for k in code_text_list], list(code_text_list.values())
    items = list(code_text_list)
    return [str(i) for i in range(1, len(items) + 1)], items


_IO_WRITE_INNER = {
    "R": r"write[._][A-Za-z._0-9]*|saveRDS|save|save\.image|ggsave|export|fwrite",
    "Python": r"to_[A-Za-z_0-9]+|savetxt|savefig|dump|save",
}
_IO_READ_INNER = (
    r"read[._][A-Za-z._0-9]+|import(_list)?|fread|readRDS|load|readLines|fromJSON|readtext|"
    r"vroom|source|read_[A-Za-z_0-9]+|loadtxt|genfromtxt|loadmat|open"
)
_SRC_PAT = r"""source\s*\(\s*['"]([^'"]+)['"]"""
_PY_WRITE_MODE_PAT = r"""^(['"])(?=[^'"]*[wax+])[rwaxbt+]+\1$"""


def _py_open_writes(line: str | None) -> bool:
    """Port of R/reproducibility_check.R::.py_open_writes().

    Whether a line of Python opens a file for writing. Only the mode argument of an
    ``open()`` call counts: the second positional argument, or ``mode =``; a mode is a
    write when it is ``w``, ``a``, ``x`` or ``r+``, with ``b``, ``t`` or ``+`` (U240,
    fixed in metacheck 0.4.0). As in R 0.4.3.28, the call text is blanked when its mode
    writes (:func:`_code_blank_write_opens`) and a changed line is a write.
    """
    if not line:
        return False
    from metacheck.codecheck.core import _code_blank_write_opens

    return _code_blank_write_opens([line], "Python", _PY_WRITE_MODE_PAT) != [line]


def _code_open_calls(
    code_text: Sequence[str | None], call_regex: str, max_lines: int = 20
) -> tuple[list[str], list[int]]:
    """Calls that go on over the next lines (``.code_open_calls()`` of R/code_check.R).

    A call matched by *call_regex* (ending in its opening ``\\(``) whose closing
    bracket is on a later line, as in ``read.csv(`` with ``"./data.csv")`` on the next
    line: a line-by-line search saw the call without its file name and the file name
    without its call (#568). Returns the text of each such call, from its name to its
    closing bracket (its lines joined with a space), and the 0-based indices of the
    lines after the first that the calls take. A call not closed within *max_lines*
    lines is left out.
    """
    calls: list[str] = []
    taken: list[int] = []
    n = len(code_text)
    if n < 2:
        return calls, taken
    pat = re.compile(call_regex)
    for i in range(n - 1):
        line0 = code_text[i] or ""
        for m in pat.finditer(line0):
            depth = 0
            in_str: str | None = None
            closed = False
            j = i
            pieces: list[str] = []
            while j <= min(n - 1, i + max_lines - 1) and not closed:
                line = code_text[j] or ""
                text = line[m.end() - 1 :] if j == i else line
                p = 0
                while p < len(text):
                    ch = text[p]
                    if in_str is not None:
                        if ch == "\\":
                            p += 1
                        elif ch == in_str:
                            in_str = None
                    elif ch in ("'", '"'):
                        in_str = ch
                    elif ch in "([{":
                        depth += 1
                    elif ch in ")]}":
                        depth -= 1
                        if depth == 0:
                            closed = True
                            break
                    p += 1
                if j == i:
                    pieces.append(line[m.start() :])
                else:
                    pieces.append(line[: p + 1] if closed else line)
                if not closed:
                    j += 1
            if closed and j > i:
                calls.append(" ".join(pieces))
                taken.extend(range(i + 1, j + 1))
    return calls, _unique(taken)


def _langs_for(fnames: Sequence[str], lang: Any) -> list[str]:
    """The language of each file: one for all (a string) or a mapping by file name."""
    if isinstance(lang, str) or lang is None:
        return [lang or "R"] * len(fnames)
    return [as_str(lang.get(fn)) or "R" for fn in fnames]


def repro_file_io(code_text_list: Any, lang: str | Mapping[str, str] = "R") -> pd.DataFrame:
    """Extract the files each script reads, writes, and sources.

    Port of ``R/reproducibility_check.R::repro_file_io()``. *code_text_list*
    maps file names to code text; *lang* is the language of every file, or a
    mapping from file name to language. Returns ``file_name`` and list columns
    ``reads``, ``writes``, ``sources`` (lowercased basenames) and ``source_refs``
    (each sourced file's reference as written, which can name its folder). Python has
    no ``source()``: an ``import`` names a module, not a script to run first, so
    ``sources`` is empty; its ``open()`` writes only with a writing mode. A call over
    several lines is judged as a whole, so a file name on a line after its write call
    is a write (#568).
    """
    if code_text_list is None or len(code_text_list) == 0:
        return _frame({"file_name": ("string", [])})

    from metacheck.codecheck.core import code_file_refs

    fnames, texts = _code_text_items(code_text_list)
    langs = _langs_for(fnames, lang)
    out_reads: list[list[str]] = []
    out_writes: list[list[str]] = []
    out_srcs: list[list[str]] = []
    out_src_refs: list[list[str]] = []
    for ct, fn_lang in zip(texts, langs, strict=True):
        nc = _code_remove_comments(ct, fn_lang)
        all_refs = list(code_file_refs(cast(list[str], nc), fn_lang, include_writes=True))
        joined = _paste_lines(nc)
        ref_base = [_norm_base(r) for r in all_refs]
        write_inner = _IO_WRITE_INNER.get(fn_lang, _IO_WRITE_INNER["R"])
        write_pat = r"\b(" + write_inner + r")\s*\("
        io_regex = r"\b(" + _IO_READ_INNER + "|" + write_inner + r")\s*\("
        open_calls, open_lines = _code_open_calls(nc, io_regex)
        taken = set(open_lines)
        units = [s for i, s in enumerate(nc) if s is not None and i not in taken] + open_calls
        # every unit is classified once, then looked up per reference
        unit_w = list(grepl(write_pat, units, perl=True, ignore_case=True))
        if fn_lang == "Python":
            unit_w = [w or _py_open_writes(u) for w, u in zip(unit_w, units, strict=True)]
        is_write: list[bool] = []
        is_read: list[bool] = []
        for ref in all_refs:
            w = [x for s, x in zip(units, unit_w, strict=True) if ref is not None and ref in s]
            is_write.append(any(w))
            is_read.append(any(not x for x in w))
        reads = [b for b, r in zip(ref_base, is_read, strict=True) if r]
        writes = [b for b, w in zip(ref_base, is_write, strict=True) if w]
        srcs: list[str | None] = []
        src_refs: list[str] = []
        if fn_lang == "R":
            src_refs = [
                str(x)
                for x in sub(
                    _SRC_PAT, r"\1", regextract_all(_SRC_PAT, joined, perl=True), perl=True
                )
            ]
            srcs = [_norm_base(s) for s in src_refs]
        reads = _setdiff(reads, srcs)
        seen: set[str | None] = set()
        first_refs: list[str] = []
        for b, r in zip(srcs, src_refs, strict=True):
            if b not in seen:
                seen.add(b)
                first_refs.append(r)
        out_reads.append(_unique(reads))
        out_writes.append(_unique(writes))
        out_srcs.append(_unique(srcs))
        out_src_refs.append(first_refs)
    return _frame(
        {
            "file_name": ("string", fnames),
            "reads": ("object", out_reads),
            "writes": ("object", out_writes),
            "sources": ("object", out_srcs),
            "source_refs": ("object", out_src_refs),
        }
    )


_ASSIGN_PAT = r"^([.a-zA-Z][.a-zA-Z0-9_]*)\s*(<<-|<-|=)(?!=)"
_ASSIGN_FN_PAT = r"""^\s*assign\s*\(\s*['"]([.a-zA-Z][.a-zA-Z0-9_]*)['"]"""


def repro_defined_vars(code_text_list: Any) -> pd.DataFrame:
    """Find the variables each script defines at top level.

    Port of ``R/reproducibility_check.R::repro_defined_vars()``: assignments
    at the start of a (comment-free) line and ``assign("name", ...)`` calls.
    Returns ``file_name`` and a list column ``defines``.
    """
    if code_text_list is None or len(code_text_list) == 0:
        return _frame({"file_name": ("string", [])})
    fnames, texts = _code_text_items(code_text_list)
    defines_out: list[list[str]] = []
    for ct in texts:
        nc = _code_remove_comments(ct)
        m1 = [m for m in regextract(_ASSIGN_PAT, nc, perl=True) if m is not None]
        v1 = list(sub(_ASSIGN_PAT + ".*", r"\1", m1, perl=True))
        m2 = [m for m in regextract(_ASSIGN_FN_PAT, nc, perl=True) if m is not None]
        v2 = list(sub(_ASSIGN_FN_PAT + ".*", r"\1", m2, perl=True))
        defines_out.append(_unique([v for v in v1 if v != ""] + [v for v in v2 if v != ""]))
    return _frame({"file_name": ("string", fnames), "defines": ("object", defines_out)})


# ---------------------------------------------------------------------------
# missing-library() corrective step
# ---------------------------------------------------------------------------


def _repro_common_pkgs() -> list[str]:
    """A curated safe-list of very common packages (``.repro_common_pkgs()``).

    Their exported names turn up in scripts that never load them; a ``could not find
    function`` error is also looked up in these (#568).
    """
    return [
        "testthat", "magrittr", "dplyr", "tidyr", "ggplot2", "purrr", "tibble",
        "stringr", "readr", "forcats", "data.table", "lubridate", "readxl", "nlme",
        "lme4", "emmeans", "lsmeans", "MASS", "ape", "picante", "shiny", "vegan",
    ]  # fmt: skip


def _repro_find_export_pkg(name: str | None, candidates: Sequence[str | None]) -> str | None:
    """The one installed candidate package that exports *name*, else ``None``.

    Port of ``.repro_find_export_pkg()``. Package namespaces only exist in R,
    so the candidates are checked by ``Rscript`` (uninstalled candidates are
    skipped; without R none is installed).
    """
    cands = _unique(c for c in _chr_list(candidates) if c is not None and c != "")
    if not cands or name == "":
        return None
    code = (
        f"name <- {_r_string(name)}\n"
        f"hits <- character(0)\n"
        f"for (pkg in {_r_chr(cands)}) {{\n"
        "  if (!requireNamespace(pkg, quietly = TRUE)) next\n"
        "  exports <- tryCatch(getNamespaceExports(asNamespace(pkg)),\n"
        "                      error = function(e) character(0))\n"
        "  if (name %in% exports) hits <- c(hits, pkg)\n"
        "}\n"
        'cat(unique(hits), sep = "\\n")\n'
    )
    try:
        res = _run_r(code, timeout=300)
    except (OSError, subprocess.SubprocessError):
        return None
    if res is None or res.returncode != 0:
        return None
    hits = _unique(s for s in res.stdout.splitlines() if s)
    return hits[0] if len(hits) == 1 else None


# ---------------------------------------------------------------------------
# repro_missing_inputs()
# ---------------------------------------------------------------------------


def _adist(a: str, b: str) -> int:
    """``utils::adist(a, b)``: the (unweighted) Levenshtein distance."""
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


#: The data formats a file can be shared in under the same name (``.repro_data_exts``).
_REPRO_DATA_EXTS = (
    "csv", "tsv", "txt", "dat", "tab", "xls", "xlsx", "ods", "rds", "rda", "rdata", "sav", "dta",
)  # fmt: skip


def repro_missing_inputs(
    refs: Any,
    plan: pd.DataFrame | None,
    structure_df: pd.DataFrame | None,
    skipped: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Diagnose why a referenced input file is unavailable.

    Port of ``R/reproducibility_check.R::repro_missing_inputs()``. Each
    referenced basename is ``withheld_size`` (in *skipped*, the
    download-skip records), ``in_archive`` (listed only inside an archive),
    ``in_repo_not_downloaded`` (listed in *structure_df* without a local copy)
    or ``absent``, with a similar repository file in ``similar_to`` when one
    exists: the same name in another data format, a name a few edits away, or
    the one name that is this one with a piece added at its start or end.
    References that are present and downloaded are dropped. Columns ``basename``,
    ``status``, ``detail`` and ``similar_to``.
    """
    del plan  # unused, as in metacheck
    ref_list = _unique(_norm_base(r) for r in _chr_list(refs))
    rows: list[tuple[str | None, str, str, str | None]] = []

    def result() -> pd.DataFrame:
        return _frame(
            {
                "basename": ("string", [r[0] for r in rows]),
                "status": ("string", [r[1] for r in rows]),
                "detail": ("string", [r[2] for r in rows]),
                "similar_to": ("string", [r[3] for r in rows]),
            }
        )

    if len(ref_list) == 0:
        return result()

    struct_base = (
        [_norm_base(f) for f in _chr_list(structure_df["file_name"])]
        if structure_df is not None and "file_name" in structure_df.columns
        else []
    )
    skip_base = (
        [_norm_base(f) for f in _chr_list(skipped["file_name"])]
        if skipped is not None and "file_name" in skipped.columns
        else []
    )
    all_candidates: list[str | None] = _unique(struct_base)
    # files listed from inside an archive have its name in front of their path
    # ("data.zip/data/x.csv"); the archive is the path up to it (#520, item 2)
    arch_re = r"^(.*?[^/]+\.(zip|tar|tgz|tar\.gz))/.*$"
    struct_path = (
        [None if x is None else x.replace("\\", "/") for x in _chr_list(structure_df["file_path"])]
        if structure_df is not None and "file_path" in structure_df.columns
        else [None] * len(struct_base)
    )
    struct_archive = [
        None if x is None or not re.match(arch_re, x) else re.sub(arch_re, r"\1", x)
        for x in struct_path
    ]

    def find_similar(b: str | None) -> str | None:
        if len(all_candidates) == 0 or b is None:
            return None
        ext = (_file_ext(b) or "").lower()
        cand_ext = [None if c is None else (_file_ext(c) or "").lower() for c in all_candidates]
        # the same name in another format counts only between data formats: x.csv and
        # x.html sharing a name is a coincidence
        key = _repro_name_key(b)
        if key and ext in _REPRO_DATA_EXTS:
            for c, ce in zip(all_candidates, cand_ext, strict=True):
                if (
                    c is not None
                    and ce != ext
                    and ce in _REPRO_DATA_EXTS
                    and _repro_name_key(c) == key
                ):
                    return c
        # a small edit distance on the full basename, among files of the same extension
        max_dist = min(3, len(b) // 4)
        if max_dist >= 1:
            best: tuple[int, str] | None = None
            for c, ce in zip(all_candidates, cand_ext, strict=True):
                if c is None or ce != ext:
                    continue
                d = _adist(b, c)
                if d <= max_dist and (best is None or d < best[0]):
                    best = (d, c)
            if best is not None:
                return best[1]
        # one name is the other with a piece added at its start or end, as a file
        # numbered or dated before it was shared: Plants2024.txt for Plants.txt (#575)
        if len(key) < 4:
            return None
        affix: list[tuple[int, str]] = []
        for c, ce in zip(all_candidates, cand_ext, strict=True):
            if c is None or ce != ext:
                continue
            ck = _repro_name_key(c)
            short, long = (ck, key) if len(ck) < len(key) else (key, ck)
            if len(short) >= 4 and ck != key and (long.startswith(short) or long.endswith(short)):
                affix.append((abs(len(ck) - len(key)), c))
        if not affix:
            return None
        least = min(x for x, _ in affix)
        best_c = [c for x, c in affix if x == least]
        return best_c[0] if len(best_c) == 1 else None

    for b in ref_list:
        if b in skip_base:
            # without a file_size column the size is left out (R stops:
            # "argument is of length zero", UPSTREAM_ISSUES U135)
            has_size = skipped is not None and "file_size" in skipped.columns
            # which(skip_base == b)[1] is NA for an NA basename
            sz = (
                None
                if b is None or not has_size
                else as_float(skipped["file_size"].iloc[skip_base.index(b)])  # type: ignore[index]
            )
            mb = f" ({sz / (1024 * 1024):.0f} MB)" if sz is not None and math.isfinite(sz) else ""
            rows.append(
                (
                    b,
                    "withheld_size",
                    "referenced file is in the repository but was not downloaded"
                    f"{mb}: over the size cap",
                    None,
                )
            )
            continue
        # in the repository only inside an archive: the code runs on the package as it
        # is, so it cannot read the file before someone unpacks it (#520)
        in_arch = [i for i, x in enumerate(struct_base) if x == b]
        if in_arch and all(struct_archive[i] is not None for i in in_arch):
            archives = ", ".join(_unique(cast(str, struct_archive[i]) for i in in_arch))
            rows.append(
                (
                    b,
                    "in_archive",
                    f"referenced file is only inside {archives}; unpack it so the code finds it",
                    None,
                )
            )
            continue
        if b in struct_base:
            # which(struct_base == b)[1] is NA for an NA basename: location NA
            loc = (
                as_str(structure_df["file_location"].iloc[struct_base.index(b)])  # type: ignore[index]
                if b is not None and "file_location" in structure_df.columns  # type: ignore[union-attr]
                else None
            )
            downloaded = loc is not None and loc != "" and os.path.exists(loc)
            if not downloaded:
                rows.append(
                    (
                        b,
                        "in_repo_not_downloaded",
                        "referenced file is listed in the repository but was not downloaded",
                        None,
                    )
                )
            else:
                rows.append((b, "present", "referenced file is available", None))
            continue
        similar = find_similar(b)
        if similar is None:
            detail = "referenced file is not present in the repository"
        elif (_file_ext(similar) or "").lower() != (_file_ext(b) or "").lower():
            detail = f"the deposit holds {similar}, the code reads {b}"
        else:
            detail = (
                "referenced file is not present in the repository, but a similarly-named "
                f"file exists: {similar}"
            )
        rows.append((b, "absent", detail, similar))
    rows = [r for r in rows if r[1] != "present"]
    return result()


# ---------------------------------------------------------------------------
# content sniffing for files that failed to parse
# ---------------------------------------------------------------------------


def _repro_content_sniff(code_text: Any) -> str | None:
    """Whether a ``.R`` file that failed to parse is really JSON, HTML or XML.

    Port of ``.repro_content_sniff()``: ``"JSON"``, ``"HTML"``, ``"XML"``, or
    ``None`` (treat it as a genuine parse failure).
    """
    lines = _chr_list(code_text)
    if code_text is None or not lines:
        return None
    txt = trimws(_paste_lines(lines))
    if txt == "":
        return None
    if grepl(r"^<!DOCTYPE\s+html", txt, ignore_case=True, perl=True):
        return "HTML"
    if grepl(r"^<html\b", txt, ignore_case=True, perl=True):
        return "HTML"
    if grepl(r"^<\?xml\b", txt, ignore_case=True, perl=True):
        return "XML"
    first_ch = txt[0]
    if first_ch in ("{", "["):
        close_ch = "}" if first_ch == "{" else "]"
        n = len(txt)
        depth = 0
        i = 1
        in_str: str | None = None
        end: int | None = None
        while i <= n:
            ch = txt[i - 1]
            if in_str is not None:
                if ch == "\\":
                    i += 1
                elif ch == in_str:
                    in_str = None
            elif ch == '"':
                in_str = ch
            elif ch == first_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    end = i
                    break
            i += 1
        if end is not None:
            tail_len = n - end
            if tail_len <= max(5, math.ceil(n * 0.01)):
                return "JSON"
    return None


_JAGS_CALL_PAT = (
    r"\b(rjags::jags\.model|jags\.model|R2jags::jags|runjags::run\.jags|run\.jags)"
    r"""\s*\(\s*(?:file\s*=\s*)?['"]([^'"]+)['"]"""
)


def _repro_is_jags_model(
    code_text: Any, file_name: str | None = "", other_code_text: Any = None
) -> bool:
    """Recognise a JAGS/BUGS model-definition file (``.repro_is_jags_model()``).

    True when the first code line opens ``model {``, the path has a
    ``jags_script``/``bugs_script``/``jags_model`` component, or another of
    the paper's files passes it to ``jags.model()``/``jags()``/``run.jags()``.
    """
    lines = _chr_list(code_text)
    if code_text is not None and lines:
        nc = [s for s in _code_remove_comments(lines) if trimws(s) != ""]
        if nc and grepl(r"^\s*model\s*\{", nc[0], perl=True):
            return True
    fn = file_name if file_name is not None else ""
    if fn != "" and grepl(
        r"(^|[/\\])(jags_script|bugs_script|jags_model)([/\\]|$)", fn, ignore_case=True, perl=True
    ):
        return True
    if other_code_text is not None and len(other_code_text):
        fn_base = _basename(_bs2fs(fn)) or ""
        if isinstance(other_code_text, Mapping):
            others = list(other_code_text.values())
        elif isinstance(other_code_text, str):
            others = [other_code_text]  # a character vector: each element is one file
        else:
            others = list(other_code_text)
        for ct in others:
            ct_lines = _chr_list(ct)
            if ct is None or not ct_lines:
                continue
            joined = _paste_lines(_code_remove_comments(ct_lines))
            m = regextract_all(_JAGS_CALL_PAT, joined, perl=True)
            if not m:
                continue
            refs = list(sub(".*" + _JAGS_CALL_PAT, r"\2", m, perl=True))
            if fn_base != "" and fn_base in [_basename(_bs2fs(r)) for r in refs]:
                return True
    return False


# ---------------------------------------------------------------------------
# EXECUTION helpers
# ---------------------------------------------------------------------------


class MaterialisedRoot(str):
    """The layout root :func:`repro_materialize_layout` returns.

    A ``str`` (the path), with R's ``materialised`` attribute -- a frame of
    ``target_path``/``source``/``ok`` rows -- in ``attrs["materialised"]``.
    """

    attrs: dict[str, Any]

    def __new__(cls, value: str, materialised: pd.DataFrame) -> MaterialisedRoot:
        obj = super().__new__(cls, value)
        obj.attrs = {"materialised": materialised}
        return obj

    @property
    def materialised(self) -> pd.DataFrame:
        out: pd.DataFrame = self.attrs["materialised"]
        return out


def _copy_file(src: str, dest: str) -> bool:
    """``isTRUE(file.copy(src, dest, overwrite = TRUE))``."""
    if os.path.isdir(src):
        return False
    try:
        shutil.copy(src, dest)
    except OSError:
        return False
    return True


def _dir_create(path: str) -> None:
    """``dir.create(path, recursive = TRUE, showWarnings = FALSE)``: failures are silent."""
    with contextlib.suppress(OSError):
        os.makedirs(path or ".", exist_ok=True)


def _portable_name(x: str | None) -> str | None:
    """:func:`metacheck.utils._portable_name` of one part of a path."""
    return _utils_portable_name([x])[0]


def _portable_path(path: str | None) -> str | None:
    """:func:`metacheck.utils._portable_path` of one path."""
    return _utils_portable_path([path])[0]


_LFS_HEAD = "version https://git-lfs.github.com/spec/"


def _repro_lfs_swaps(sources: Sequence[str | None]) -> pd.DataFrame:
    """Port of R/reproducibility_check.R::.repro_lfs_swaps().

    A Git LFS pointer in place of a file, as GitHub's archives hold for files kept in
    LFS: the copy of the same name and of the size the pointer states, elsewhere in the
    deposit, is used instead (#568). Columns ``pointer`` and ``file``.
    """
    pointers_: list[str] = []
    files_: list[str] = []

    def result() -> pd.DataFrame:
        return _frame({"pointer": ("string", pointers_), "file": ("string", files_)})

    src = _unique(p for p in sources if p is not None and os.path.exists(p))
    if not src:
        return result()
    size: dict[str, int | None] = {}
    for f in src:
        try:
            size[f] = None if os.path.isdir(f) else os.path.getsize(f)
        except OSError:
            size[f] = None
    small = [f for f in src if size[f] is not None and cast(int, size[f]) < 1024]
    stated: dict[str, int] = {}
    for f in small:
        try:
            head = _read_lines(f)[:3]
        except (OSError, UnicodeError):
            head = []
        if not head or not head[0].startswith(_LFS_HEAD):
            continue
        sizes = [ln for ln in head if re.fullmatch(r"size [0-9]+", ln)]
        if sizes:
            stated[f] = int(sizes[0][5:])
    for p in small:
        if p not in stated:
            continue
        same = [
            f for f in src if _norm_base(f) == _norm_base(p) and size[f] == stated[p] and f != p
        ]
        if same:
            pointers_.append(p)
            files_.append(same[0])
    return result()


def _chmod(path: str, mode: int) -> None:
    """``Sys.chmod(path, mode)``: failures are silent."""
    with contextlib.suppress(OSError):
        os.chmod(path, mode)


def repro_materialize_layout(
    plan: pd.DataFrame | None,
    structure_df: pd.DataFrame | None,
    root: str | os.PathLike[str],
    deadline: Any = None,
) -> MaterialisedRoot:
    """Materialise the deposit's layout for a paper into a throwaway directory.

    Port of ``R/reproducibility_check.R::repro_materialize_layout()``: every planned
    file with a target and a source is copied to ``<root>/<target_path>`` (and a
    converted file's original to ``<root>/<original_target>``); ``<root>/output`` is
    always created. Each row's own source is used (:func:`_repro_plan_sources`), a Git
    LFS pointer is swapped for the real copy elsewhere in the deposit, and a file that
    cannot be copied still gets its folder, since a script can write into a folder that
    holds only a placeholder (#575). The copies and folders are opened for the code to
    change, for the host user only (D135). Returns *root* with the copy log in ``attrs["materialised"]`` and the swaps
    in ``attrs["lfs"]``.

    *deadline* is a clock time (a :class:`datetime.datetime`, or ``time.time()`` seconds)
    after which no further file is copied: its folder is still made and its row has
    ``ok`` False. ``None`` copies every file. ``attrs["not_copied_time"]`` is the number
    of files not copied because the deadline had passed (a repository of 10,658 files took
    16 minutes to copy on the metacheck server, past its limit).
    """
    root_s = os.fspath(root)
    limit = _clock_seconds(deadline)
    late = 0

    def too_late() -> bool:
        nonlocal late
        if limit is None or time.time() <= limit:
            return False
        late += 1
        return True

    _dir_create(root_s)
    _dir_create(_file_path(root_s, "output"))
    rows: list[tuple[str | None, str | None, bool]] = []

    def result(lfs: pd.DataFrame | None = None) -> MaterialisedRoot:
        out = MaterialisedRoot(
            root_s,
            _frame(
                {
                    "target_path": ("string", [r[0] for r in rows]),
                    "source": ("string", [r[1] for r in rows]),
                    "ok": ("boolean", [r[2] for r in rows]),
                }
            ),
        )
        if lfs is not None:
            out.attrs["lfs"] = lfs
        out.attrs["not_copied_time"] = late
        return out

    if (
        plan is None
        or len(plan) == 0
        or not all(c in plan.columns for c in ("file_name", "target_path"))
    ):
        return result()

    sources = _repro_plan_sources(plan, structure_df)
    lfs = _repro_lfs_swaps(sources)
    swap = dict(zip(_col(lfs, "pointer"), _col(lfs, "file"), strict=True))
    sources = [swap.get(src, src) if src is not None else None for src in sources]

    for i, tgt in enumerate(_chr_list(plan["target_path"])):
        if tgt is None or tgt == "":
            continue
        src = sources[i]
        dest = _file_path(root_s, _portable_path(tgt))
        if src is None:
            if not grepl(r"(^|/)\.\.(/|$)", tgt.replace("\\", "/")):
                _dir_create(os.path.dirname(dest))
            rows.append((tgt, None, False))
            continue
        _dir_create(os.path.dirname(dest))
        if too_late():
            rows.append((tgt, src, False))
            continue
        ok = _copy_file(src, dest)
        if ok:
            # the copy is the code's to change (a script that saves over a file of the
            # deposit); the container runs as the host user, so no one else needs it
            _chmod(dest, 0o644)
        rows.append((tgt, src, ok))
    if "original_target" in plan.columns:
        for i, tgt in enumerate(_chr_list(plan["original_target"])):
            if tgt is None or tgt == "":
                continue
            src = sources[i]
            if src is None:
                rows.append((tgt, None, False))
                continue
            dest = _file_path(root_s, _portable_path(tgt))
            _dir_create(os.path.dirname(dest))
            if too_late():
                rows.append((tgt, src, False))
                continue
            rows.append((tgt, src, _copy_file(src, dest)))
    # and its folders, so a script can save a new file next to the data (for the host
    # user only: metacheck opens them to everyone, D135)
    for d, _subdirs, _files in os.walk(root_s):
        with contextlib.suppress(OSError):
            os.chmod(d, os.stat(d).st_mode | 0o700)
    return result(lfs)


_SETWD_LINE_FOR = {
    "R": r"^(\s*)((?:base::)?setwd\s*\(.*)$",
    "Python": r"^(\s*)((?:[A-Za-z_][A-Za-z0-9_]*\.)?chdir\s*\(.*)$",
}
_SETWD_ARG_FOR = {
    "R": r"""(?:base::)?setwd\s*\(\s*['"]([^'"]+)['"]""",
    "Python": r"""(?:[A-Za-z_][A-Za-z0-9_]*\.)?chdir\s*\(\s*['"]([^'"]+)['"]""",
}
_FAMILY_PAT = r"""\b(base_family|family)\s*=\s*(['"])[^'"]*\2"""


def _repro_bracket_depth(line: str | None, lang: str = "R") -> int:
    """Port of R/reproducibility_check.R::.repro_bracket_depth().

    How many more brackets a line of code opens than it closes, leaving out brackets in
    quoted text and after a comment sign.
    """
    if not line:
        return 0
    quotes = ("'", '"', "`") if lang == "R" else ("'", '"')
    depth = 0
    quote = ""
    esc = False
    for c in line:
        if quote:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == quote:
                quote = ""
        elif c in quotes:
            quote = c
        elif c == "#":
            break
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
    return depth


def _repro_neutralise_setwd(
    txt: Sequence[str | None], lang: str = "R", by: str = "reproducibility_check"
) -> tuple[list[str | None], int, list[str]]:
    """Port of R/reproducibility_check.R::.repro_neutralise_setwd().

    Each ``setwd()``/``os.chdir()`` line (also ``base::setwd()`` and ``chdir()`` under
    another module name) becomes a statement that does nothing, keeping the original in
    a comment that says why: a bare comment leaves an empty block (#480). The further
    lines of a call written over several lines are made comments too (#578). Returns
    the lines, the number replaced and the quoted path each named.
    """
    lines = list(txt)
    setwd_line = _SETWD_LINE_FOR.get(lang, _SETWD_LINE_FOR["R"])
    setwd_arg = _SETWD_ARG_FOR.get(lang, _SETWD_ARG_FOR["R"])
    is_setwd = [s is not None and re.search(setwd_line, s) is not None for s in lines]
    n = sum(is_setwd)
    paths: list[str] = []
    if n == 0:
        return lines, 0, paths
    start_pat = r".*?((?:[A-Za-z_][A-Za-z0-9_]*(?:::|\.))?(?:setwd|chdir)\s*\(.*)$"
    calls: list[str] = []
    more: list[int] = []
    for i, hit in enumerate(is_setwd):
        if not hit:
            continue
        call_start = re.sub(start_pat, r"\1", cast(str, lines[i]))
        depth = _repro_bracket_depth(call_start, lang)
        j = i
        while depth > 0 and j < len(lines) - 1 and j - i < 50:
            j += 1
            depth += _repro_bracket_depth(lines[j], lang)
        if j > i and depth <= 0:
            more.extend(range(i + 1, j + 1))
        calls.append("\n".join([call_start, *(_na_str(x) for x in lines[i + 1 : j + 1])]))
    for c in calls:
        m = re.search(setwd_arg, c)
        if m is not None and m.group(0):
            paths.append(m.group(1))
    by_text = (
        f"pass  # [{by} removed os.chdir] "
        if lang == "Python"
        else f"invisible(NULL)  # [{by} removed setwd] "
    )
    for i, hit in enumerate(is_setwd):
        if hit:
            m = re.search(setwd_line, cast(str, lines[i]))
            assert m is not None
            lines[i] = m.group(1) + by_text + m.group(2)
    for i in more:
        if lines[i] is not None:
            lines[i] = re.sub(r"^(\s*)", r"\1# ", cast(str, lines[i]), count=1)
    return lines, n, paths


def _portable_ref(p: str) -> str:
    """:func:`_portable_path` keeping a folder's final ``/``."""
    return str(_portable_path(p)) + ("/" if re.search(r"[/\\]$", p) else "")


def _repro_apply_rewrites(txt: list[str | None], rw: pd.DataFrame | None) -> list[str | None]:
    """Port of R/reproducibility_check.R::.repro_apply_rewrites().

    Each resolved rewrite of :func:`repro_rewrite_paths` as a literal replacement. A
    plain reference is replaced only where it stands in quotes (``"x.csv"``, ``'x.csv'``
    or ``\\"x.csv\\"`` in a notebook's JSON): replaced wherever it appeared, ``x.csv``
    also changed ``"old_x.csv"``. An ``is_call`` row replaces the whole call with a
    quoted path. The path written in is :func:`_portable_path` of the target, as the
    copy in the sandbox is (#575); a reference kept as written (``as_written``) is left
    alone unless it holds such a character.
    """
    if rw is None or len(rw) == 0:
        return txt
    n = len(rw)
    matched = _col(rw, "matched")
    ambiguous = _col(rw, "ambiguous")
    target = _col(rw, "target")
    refs = _col(rw, "ref")
    is_call = _col(rw, "is_call") if "is_call" in rw.columns else [False] * n
    kept = (
        [_is_true(v) for v in _col(rw, "as_written")] if "as_written" in rw.columns else [False] * n
    )
    good = [
        _is_true(matched[k]) and not _is_true(ambiguous[k]) and bool(target[k]) for k in range(n)
    ]
    # ambiguous NA: R's !NA is NA, and which() drops it
    good = [g and ambiguous[k] is not None for k, g in enumerate(good)]
    tgt = [_portable_ref(target[k]) if good[k] else target[k] for k in range(n)]
    for k in range(n):
        ref = refs[k]
        unportable = (
            kept[k]
            and not _is_true(is_call[k])
            and ref is not None
            and not re.match(r"^([A-Za-z]:[/\\]|/|~)", ref)
            and _portable_ref(ref) != ref.replace("\\", "/")
        )
        if unportable:
            tgt[k] = _portable_ref(cast(str, ref))
        good[k] = good[k] and (not kept[k] or unportable)
    for k in range(n):
        if not good[k]:
            continue
        if _is_true(is_call[k]):
            txt = list(gsub(refs[k], f'"{tgt[k]}"', txt, fixed=True))
        else:
            for q in ('\\"', '"', "'"):
                txt = list(gsub(f"{q}{refs[k]}{q}", f"{q}{tgt[k]}{q}", txt, fixed=True))
    return txt


_QUOTED_LINE_PAT = r"""(["'])((?:[^"'\\\n]|\\.)*)\1"""


def _repro_strip_setwd_folder(
    txt: list[str | None], folders: Sequence[str]
) -> tuple[list[str | None], list[str]]:
    """Port of R/reproducibility_check.R::.repro_strip_setwd_folder().

    A path under the folder of a ``setwd()`` the check removes is made relative to the
    working folder, which is what the authors meant (#578, item 3). Only an absolute
    folder counts; case does not count after a drive letter. Returns the lines and the
    paths changed, as ``before -> after``.
    """

    def norm(p: str) -> str:
        return re.sub(r"/+$", "", re.sub(r"\\+", "/", p))

    fs = _unique(norm(f) for f in folders if re.match(r"^([A-Za-z]:[/\\]|/|~)", f))
    fs = [f for f in fs if f and f not in ("/", "~")]
    if not txt or not fs:
        return txt, []
    changed: list[str] = []

    def one(m: re.Match[str]) -> str:
        h = m.group(0)
        inner = norm(h[1:-1])
        for f in fs:
            drive = re.match(r"^[A-Za-z]:", f) is not None
            starts = (
                inner.lower().startswith(f.lower() + "/") if drive else inner.startswith(f + "/")
            )
            if starts and len(inner) > len(f) + 1:
                out = h[0] + inner[len(f) + 1 :] + h[0]
                changed.append(f"{h} -> {out}")
                return out
        return h

    out: list[str | None] = []
    for line in txt:
        if line is None or not re.search(r"[\"']", line):
            out.append(line)
            continue
        out.append(re.sub(_QUOTED_LINE_PAT, one, line))
    return out, changed


_SLASH_PAT = r"""(["'])((?:[^"'\\\n]|\\\\)*\\\\(?:[^"'\\\n]|\\\\)*\.[A-Za-z0-9]{1,8})\1"""


def _repro_slash_paths(txt: list[str | None]) -> tuple[list[str | None], list[str]]:
    """Port of R/reproducibility_check.R::.repro_slash_paths().

    Windows paths in quoted file names, ``"Data\\\\MatrixNoct.txt"``, get ``/``: on Linux
    the backslash is part of the name (#568). Only a quoted text that ends in a file
    extension and has no sign of a regular expression is changed. Returns the lines and
    the paths changed, as they were.
    """
    if not txt:
        return txt, []
    changed: list[str] = []

    def one(m: re.Match[str]) -> str:
        h = m.group(0)
        inner = h[1:-1]
        if re.search(r"\\\\\.", inner) or re.search(r"[\^$*+?|()\[\]{}]", inner):
            return h
        changed.append(h)
        return h[0] + inner.replace("\\\\", "/") + h[0]

    out: list[str | None] = []
    for line in txt:
        if line is None or "\\\\" not in line:
            out.append(line)
            continue
        out.append(re.sub(_SLASH_PAT, one, line))
    return out, changed


_FONT_GENERIC = {
    "serif", "sans", "mono", "symbol", "times", "helvetica", "arial", "courier", "palatino",
    "calibri", "verdana", "georgia",
}  # fmt: skip

#: The family functions of R's stats package (R tests ``exists(v, envir =
#: asNamespace("stats"), mode = "function")``). Of them only ``Gamma`` is not caught by
#: the rule for a lowercase name; another function of stats is not a family.
_STATS_FAMILY_FUNS = {
    "binomial", "gaussian", "Gamma", "inverse.gaussian", "poisson", "quasi", "quasibinomial",
    "quasipoisson",
}  # fmt: skip


def _repro_is_font_family(args: Sequence[str]) -> list[bool]:
    """Port of R/reproducibility_check.R::.repro_is_font_family().

    Which ``family = "..."`` / ``base_family = "..."`` arguments name a font, not a
    model's family (#568): ``base_family`` always does; a model family is a lowercase
    name (``nbinom2``) or a function of R's stats package (``Gamma``); a font has
    capitals or spaces, or is one of R's generic fonts.
    """
    out: list[bool] = []
    for a in args:
        value = re.sub(r"""^.*=\s*['"]([^'"]*)['"]$""", r"\1", a)
        base = a.startswith("base_family")
        generic = value.lower() in _FONT_GENERIC
        stats_fn = value != "" and value in _STATS_FAMILY_FUNS
        model = not generic and (stats_fn or re.match(r"^[a-z][a-z0-9_.]*$", value) is not None)
        out.append(base or not model)
    return out


def _binary_kind_raw(b: bytes) -> str | None:
    """Port of R/code_check.R::.binary_kind_raw(): what bytes are when not text."""
    b = b[:4096]
    if not b:
        return None
    if b.startswith(b"\x1f\x8b"):
        return "compressed with gzip (as R saves data)"
    if (
        len(b) >= 10
        and b[:3] == b"BZh"
        and b[3:4] in [bytes([c]) for c in b"123456789"]
        and b[4:10] == bytes([0x31, 0x41, 0x59, 0x26, 0x53, 0x59])
    ):
        return "compressed with bzip2"
    if b.startswith(bytes([0xFD, 0x37, 0x7A, 0x58, 0x5A])):
        return "compressed with xz"
    if 0 not in b:
        return None
    if b.startswith((b"\xff\xfe", b"\xfe\xff")):
        return None
    n = len(b) - len(b) % 2
    if n >= 4:
        odd = [b[i] == 0 for i in range(0, n, 2)]
        even = [b[i] == 0 for i in range(1, n, 2)]
        m_odd = sum(odd) / len(odd)
        m_even = sum(even) / len(even)
        if (m_odd > 0.9 and m_even < 0.1) or (m_even > 0.9 and m_odd < 0.1):
            return None
    return "binary"


def _repro_binary_kind(path: str) -> str | None:
    """Port of R/reproducibility_check.R::.repro_binary_kind()."""
    try:
        with open(path, "rb") as fh:
            return _binary_kind_raw(fh.read(4096))
    except OSError:
        return None


_ENV_ARGS = r"(?:[^()]++|\((?:[^()]++|\([^()]*+\))*+\))*+"
_INSTALLERS = (
    r"(?:(?:utils|remotes|devtools|BiocManager|pak|renv)::)?"
    r"(?:install\.packages|update\.packages|install_github|install_version|install_url|"
    r"install_cran|install_bitbucket|install_local|install_git|biocLite|pkg_install|pak)"
)
_ENV_TOOLS = r"""['"]?(?:groundhog|checkpoint|renv|pacman|remotes|devtools|BiocManager|pak)['"]?"""


def _deparse1_vec(values: Sequence[str]) -> str:
    """``deparse1()`` of a character vector (no NA)."""
    lits = ['"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"' for v in values]
    return lits[0] if len(lits) == 1 else "c(" + ", ".join(lits) + ")"


def _known_vars_lines(variables: Mapping[str, Sequence[str]]) -> list[str]:
    return [f"{nm} <- {_deparse1_vec(list(v))}" for nm, v in variables.items() if len(v)]


def _repro_groundhog_as_library(call: str, variables: Mapping[str, Sequence[str]]) -> str:
    """Port of R/reproducibility_check.R::.repro_groundhog_as_library()."""
    from metacheck.codecheck.core import code_library_names

    call = re.sub(r"^groundhog::", "", call)
    arg = _split_args(re.sub(r"(?s)^[^(]*\((.*)\)$", r"\1", call))
    first = re.sub(r"^\s*pkg\s*=\s*", "", trimws(arg[0]["text"])) if arg else ""
    if re.match(r"^[.A-Za-z][.A-Za-z0-9_]*$", first) and first not in variables:
        return (
            f'{{ if (exists("{first}") && is.character({first})) '
            f"for (.pkg in {first}) library(.pkg, character.only = TRUE) "
            f'else library("{first}") }}'
        )
    lib = code_library_names([*_known_vars_lines(variables), call], "R")
    pkgs = _unique(str(p) for p in _chr_list(lib["package"]) if p is not None) if len(lib) else []
    if not pkgs:
        return "invisible(NULL)"
    return "{ " + "; ".join(f'library("{p}")' for p in pkgs) + " }"


def _repro_p_load_as_library(call: str, variables: Mapping[str, Sequence[str]]) -> str:
    """Port of R/reproducibility_check.R::.repro_p_load_as_library()."""
    from metacheck.codecheck.core import code_library_names

    held = re.search(r"\bchar\s*=\s*([.A-Za-z][.A-Za-z0-9_]*)", call)
    if held is not None and held.group(1) not in variables:
        return f"{{ for (.pkg in {held.group(1)}) library(.pkg, character.only = TRUE) }}"
    try:
        lib = code_library_names([*_known_vars_lines(variables), call], "R")
        pkgs = (
            _unique(str(p) for p in _chr_list(lib["package"]) if p is not None) if len(lib) else []
        )
    except Exception:
        pkgs = []
    pkgs = [p for p in pkgs if p != "pacman"]
    if not pkgs:
        return "invisible(NULL)"
    return "{ " + "; ".join(f'library("{p}")' for p in pkgs) + " }"


def _repro_neutralise_env_calls(txt: list[str | None]) -> tuple[list[str | None], int]:
    """Port of R/reproducibility_check.R::.repro_neutralise_env_calls().

    The calls of groundhog, checkpoint, renv and the package installers become what a
    run without network can do: the packages are installed before the run (#523, #568).
    ``groundhog.library()`` and ``p_load()`` become ``library()``; ``renv::*()``,
    ``checkpoint()`` and the installers do nothing; ``require(renv)`` becomes ``TRUE``.
    Returns the lines and the number of calls replaced.
    """
    if not txt:
        return txt, 0
    from metacheck.codecheck.core import _code_char_vector_vars

    joined = _paste_lines(txt)
    count = 0

    def swap(text: str, pat: str, repl: Callable[[str], str]) -> str:
        nonlocal count
        hits = list(re.finditer(pat, text))
        if not hits:
            return text
        count += len(hits)
        return re.sub(pat, lambda m: repl(m.group(0)), text)

    def removed(name: str) -> Callable[[str], str]:
        return lambda _call: f'invisible("reproducibility_check removed {name}")'

    try:
        variables = _code_char_vector_vars(txt)
        a = _ENV_ARGS
        joined = swap(
            joined,
            r"(?<![\w.])(?:groundhog::)?groundhog\.library\s*\(" + a + r"\)",
            lambda c: _repro_groundhog_as_library(c, variables),
        )
        joined = swap(
            joined,
            r"(?<![\w.:])(?:groundhog::)?(?:set\.groundhog\.folder|meta\.groundhog)\s*\("
            + a
            + r"\)",
            removed("a groundhog call"),
        )
        joined = swap(
            joined, r"(?<![\w.:])renv::\w+(?:\$[\w.]+)*\s*\(" + a + r"\)", removed("an renv call")
        )
        if re.search(r"""checkpoint::|(?:library|require)\s*\(\s*['"]?checkpoint""", joined):
            joined = swap(
                joined,
                r"(?<![\w.:])(?:checkpoint::)?(?:create_|use_)?checkpoint\s*\(" + a + r"\)",
                removed("a checkpoint call"),
            )
        joined = swap(
            joined,
            r"(?<![\w.])source\s*\([^)\n]*renv[^)\n]*activate[^)\n]*\)+",
            removed("renv's activate.R"),
        )
        joined = swap(
            joined,
            r"(?<![\w.:$])" + _INSTALLERS + r"\s*\(" + a + r"\)",
            lambda _c: "invisible(NULL)",
        )
        joined = swap(
            joined,
            r"(?<![\w.:$])BiocManager::install\s*\(" + a + r"\)",
            lambda _c: "invisible(NULL)",
        )
        joined = swap(
            joined,
            r"""(?<![\w.:$"'])(?:utils::)?install\.packages(?![\w.("'])""",
            lambda _c: "invisible",
        )
        joined = swap(
            joined,
            r"(?<![\w.:])(?:pacman::)?p_load\s*\(" + a + r"\)",
            lambda c: _repro_p_load_as_library(c, variables),
        )
        joined = swap(
            joined,
            r"(?<![\w.:])(?:base::)?(?:requireNamespace|require)\s*\(\s*"
            + _ENV_TOOLS
            + r"\s*(?:,[^()]*)?\)",
            lambda _c: "TRUE",
        )
        joined = swap(
            joined,
            r"(?<![\w.:])(?:base::)?library\s*\(\s*" + _ENV_TOOLS + r"\s*(?:,[^()]*)?\)",
            removed("the loading of a package manager"),
        )
    except Exception:
        return txt, 0
    return list(strsplit(joined, "\n", fixed=True)), count


def _repro_relocate_relative_paths(
    txt: list[str | None], script_path: str | None, plan: pd.DataFrame | None
) -> list[str | None]:
    """Port of R/reproducibility_check.R::.repro_relocate_relative_paths().

    Every quoted relative path whose folder does not exist from the top of the package,
    but does from the script's ``.Rproj`` folder or its own folder (in that order), is
    rewritten to its path from the top (#523, item 3). A path given as a later piece of
    a joining call is left alone.
    """
    if not txt or plan is None or len(plan) == 0 or not script_path:
        return txt
    files = [None if t is None else t.replace("\\", "/") for t in _chr_list(plan["target_path"])]
    folders: list[str] = []
    d = [cast(str, _r_dirname(f)) for f in files if f is not None]
    while True:
        d = _setdiff([x for x in d if x != "."], folders)
        if not d:
            break
        folders.extend(d)
        d = [cast(str, _r_dirname(x)) for x in d]
    if not folders:
        return txt
    script_dir = cast(str, _r_dirname(script_path.replace("\\", "/")))
    rproj = {
        cast(str, _r_dirname(f))
        for f in files
        if f is not None and re.search(r"\.Rproj$", f, re.IGNORECASE)
    }
    up = script_dir
    proj_dir: str | None = None
    while True:
        if up in rproj:
            proj_dir = up
            break
        if up in (".", "/", ""):
            break
        up = cast(str, _r_dirname(up))
    bases = [b for b in _unique([x for x in (proj_dir, script_dir) if x is not None]) if b != "."]
    if not bases:
        return txt
    folder_set = set(folders)

    def relocate(s: str) -> str:
        p = s.replace("\\", "/")
        if re.search(r"^([A-Za-z]:/|/|~)|://", p) or "/" not in p:
            return s
        dir_ = re.sub(r"/+$", "", p) if p.endswith("/") else cast(str, _r_dirname(p))
        top = _repro_norm_rel(dir_)
        if top is not None and (top in ("", ".") or top in folder_set):
            return s
        for b in bases:
            from_base = _repro_norm_rel(f"{b}/{dir_}")
            if from_base is not None and from_base in folder_set:
                out = _repro_norm_rel(f"{b}/{p}") or ""
                return out + "/" if p.endswith("/") else out
        return s

    join_pat = r"(file\.path|os\.path\.join|paste0?|Path|here)\s*\([^()]*,\s*$"
    lit_pat = r"""(['"])([^'"\n]*/[^'"\n]*)\1"""
    out: list[str | None] = []
    for line in txt:
        if line is None or not re.search(lit_pat, line):
            out.append(line)
            continue

        def one(m: re.Match[str], line: str = line) -> str:
            if re.search(join_pat, line[: m.start()]):
                return m.group(0)
            q = m.group(0)[0]
            return q + relocate(m.group(0)[1:-1]) + q

        out.append(re.sub(lit_pat, one, line))
    return out


def _repro_make_write_folders(
    wr: pd.DataFrame, root: str, run_dir: str = ".", txt: list[str | None] | None = None
) -> None:
    """Port of R/reproducibility_check.R::.repro_make_write_folders().

    Make the folders of the relative paths in write calls, counted from where the script
    runs, never above the sandbox: an empty output folder is often not deposited (#568,
    item 20). A folder held in a variable a write call uses counts too, and a path given
    as ``file =`` and the like to a function not on the list of writers.
    """
    if not len(wr):
        return
    calls = [c for c in _col(wr, "call_text") if c is not None]
    qpat = r"""["'][^"'\n]*[/\\][^"'\n]*\.[A-Za-z0-9]{1,8}["']"""
    quoted = [m.group(0) for c in calls for m in re.finditer(qpat, c)]
    in_vars: list[str] = []
    if txt:
        from metacheck.datacheck.files import _utf8_lines

        lines = _utf8_lines(txt)
        try:
            nc = _code_remove_comments(lines, "R")
        except Exception:
            nc = lines
        variables = {
            k: v for k, v in _repro_simple_string_vars(nc).items() if re.search(r"[/\\]$", v)
        }
        for v, val in variables.items():
            vq = v.replace(".", "\\.")
            if any(re.search(r"(?<![.\w])" + vq + r"(?![.\w])", c) for c in calls):
                in_vars.append(re.sub(r"[/\\]+$", "", val) + "/x")
        npat = (
            r"""\b(?:file|out|outfile|filename|output)\s*=\s*["'][^"'\n]*[/\\][^"'\n]*"""
            r"""\.[A-Za-z0-9]{1,8}["']"""
        )
        named = [m.group(0) for s in lines if s is not None for m in re.finditer(npat, s)]
        in_vars.extend(re.sub(r"""^[^"']*["']""", "", re.sub(r"""["']$""", "", x)) for x in named)
    for q in _unique([re.sub(r"""^["']|["']$""", "", x) for x in quoted] + in_vars):
        q = q.replace("\\", "/")
        if re.search(r"^([A-Za-z]:/|/|~)|://", q):
            continue
        p = _repro_norm_rel(q if run_dir == "." else f"{run_dir}/{q}")
        if p is None or _r_dirname(p) == ".":
            continue
        _dir_create(_file_path(root, _portable_path(_r_dirname(p))))


_LIB_LOC_PAT = re.compile(
    r"(\b(?:library|require|requireNamespace|loadNamespace)\s*\([^()]*?)"
    r"""\s*,\s*lib\.loc\s*=\s*(['"])((?:[A-Za-z]:[/\\]|/|~)[^'"]*)\2"""
)


def _repro_drop_author_lib_loc(txt: Sequence[str | None]) -> tuple[list[str | None], list[str]]:
    """Port of R ``.repro_drop_author_lib_loc()`` (#594).

    A ``lib.loc =`` that points to a folder on the authors' computer, removed from
    ``library()``, ``require()``, ``requireNamespace()`` and ``loadNamespace()``: the run
    has its own library, where the package is installed, and the folder is not there, so
    the script stopped with "no library trees found" although the package was there. Only
    an absolute path (``C:/...``, ``/home/...``, ``~/...``) goes: a relative one may be a
    library the deposit holds. Returns the lines and the folders removed.
    """
    out = list(txt)
    paths: list[str] = []
    for i, line in enumerate(out):
        if line is None or re.match(r"^\s*#", line):
            continue
        found = [m.group(3) for m in _LIB_LOC_PAT.finditer(line)]
        if found:
            paths.extend(found)
            out[i] = _LIB_LOC_PAT.sub(r"\1", line)
    return out, _unique(paths)


def repro_write_scripts(
    code_text_list: Mapping[str, Any],
    rewrite_list: Mapping[str, pd.DataFrame | None] | None,
    plan: pd.DataFrame | None,
    root: str | os.PathLike[str],
    inject_libs: Mapping[str, Any] | None = None,
    lang: str | Mapping[str, str] = "R",
    output_dir: str | None = None,
    script_paths: Sequence[str | None] | None = None,
    writes_in_place: bool = False,
    replace_env_calls: bool = False,
    run_dirs: Sequence[str] | None = None,
    prepend: Mapping[str, Sequence[str]] | None = None,
) -> pd.DataFrame:
    """Write path-rewritten scripts into the materialised layout.

    Port of ``R/reproducibility_check.R::repro_write_scripts()``. Each script gets its
    resolved path rewrites, the folder of a removed ``setwd()`` taken out of its paths,
    Windows paths given ``/``, its write calls redirected into ``output/`` (with
    *writes_in_place*, only a write to an absolute path or out of the deposit; the
    folders the other writes need are made), its ``setwd()`` lines made to do nothing,
    with *replace_env_calls* the calls of groundhog, renv, checkpoint and the package
    installers replaced, named font families replaced with ``"sans"`` (not a model's
    family) and (with *inject_libs*) ``library()`` calls injected; *prepend* puts lines
    first. It is written to ``<root>/<target>`` (*script_paths* gives each script's own
    path), unless a file there is not text. Returns ``file_name``, ``script_path``,
    ``run_dir``, ``setwd_removed``, ``env_calls_replaced``, ``slashes_fixed``,
    ``setwd_folder_removed``, ``setwd_paths``, ``lib_loc_removed`` (a ``lib.loc`` on the
    authors' computer taken out of ``library()`` and the like, #594), ``family_replaced``,
    ``family_detail`` and ``library_injected``.

    *lang* is the language of every script, or a mapping from file name to language.
    *run_dirs* are the folders the scripts run in, from the root (default the root). A
    redirected write names ``output/`` from there, which is the same folder in both
    sandboxes; *output_dir* names another one instead.
    """
    if code_text_list is None or len(code_text_list) == 0:
        return pd.DataFrame()  # dplyr::bind_rows(list()): no rows, no columns
    if not isinstance(code_text_list, Mapping):
        raise IndexError("subscript out of bounds")
    root_s = os.fspath(root)
    fnames = [str(k) for k in code_text_list]
    has_plan = plan is not None and "file_name" in plan.columns
    plan_base = (
        [_tolower(_basename(f)) for f in _chr_list(cast(pd.DataFrame, plan)["file_name"])]
        if has_plan
        else []
    )
    plan_target: list[str | None] = (
        _chr_list(plan["target_path"])  # type: ignore[index]
        if has_plan and "target_path" in plan.columns  # type: ignore[union-attr]
        else [None] * len(plan_base)
    )

    def script_target(fn: str, i: int) -> str:
        own = script_paths[i] if script_paths is not None else None
        if own:
            return own
        if plan_base:
            b = _tolower(_basename(fn))
            for pb_, t in zip(plan_base, plan_target, strict=True):
                if pb_ == b and t is not None and t != "":
                    return t
        return _basename(fn) or ""

    langs = dict(zip(fnames, _langs_for(fnames, lang), strict=True))
    rows: list[tuple[Any, ...]] = []
    for i, fn in enumerate(fnames):
        fn_lang = langs[fn]
        is_r = fn_lang == "R"
        txt = _chr_list(code_text_list[fn])
        _message(
            f"[repro]   rewriting '{fn}' "
            f"({sum(len(s.encode('utf-8', 'surrogateescape')) for s in txt if s is not None)}"
            " bytes) ..."
        )
        rw = rewrite_list.get(fn) if isinstance(rewrite_list, Mapping) else None
        txt = _repro_apply_rewrites(txt, rw)
        # a path under the folder of a setwd() removed below counts from the working
        # folder, before the Windows paths are changed (#578, item 3)
        txt, folder_removed = _repro_strip_setwd_folder(
            txt, _repro_neutralise_setwd(txt, fn_lang)[2]
        )
        txt, slashes = _repro_slash_paths(txt)

        run_dir = "." if run_dirs is None else run_dirs[i]
        if writes_in_place and run_dir == ".":
            txt = _repro_relocate_relative_paths(txt, script_target(fn, i), plan)
        all_wr = _repro_redirect_writes(txt, lang=fn_lang)
        wr = all_wr
        if writes_in_place and len(wr) > 0:
            outside = [
                c is not None
                and re.search(r"""["']([A-Za-z]:[/\\]|/|~|\.\.[/\\])""", c) is not None
                for c in _col(wr, "call_text")
            ]
            wr = wr[outside].reset_index(drop=True)
        if writes_in_place:
            _repro_make_write_folders(all_wr, root_s, run_dir, txt)
        if len(wr) > 0:
            if output_dir is None:
                ups = "" if run_dir == "." else "../" * len(_split_path(run_dir))
                out_dir = ups + "output/"
            else:
                out_dir = output_dir
            joined_txt = _paste_lines(txt)
            for call_text, replacement in zip(
                _col(wr, "call_text"), _col(wr, "replacement"), strict=True
            ):
                repl = gsub("{{REPRO_OUTPUT}}", out_dir, replacement, fixed=True)
                joined_txt = sub(call_text, repl, joined_txt, fixed=True)
            txt = list(strsplit(joined_txt, "\n", fixed=True))

        txt, setwd_n, setwd_paths = _repro_neutralise_setwd(txt, fn_lang)

        env_n = 0
        if replace_env_calls and is_r:
            txt, env_n = _repro_neutralise_env_calls(txt)

        # lib.loc pointing to a folder on the authors' computer (#594)
        lib_loc_paths: list[str] = []
        if is_r:
            txt, lib_loc_paths = _repro_drop_author_lib_loc(txt)

        joined_for_family = _paste_lines(txt)
        family_all = regextract_all(_FAMILY_PAT, joined_for_family, perl=True) if is_r else []
        # a model's family is no font: glmmTMB(..., family = "nbinom2") (#568)
        is_font = _repro_is_font_family(family_all)
        family_matches = [f for f, ok in zip(family_all, is_font, strict=True) if ok]
        family_n = len(family_matches)
        if family_n > 0:
            pieces: list[str] = []
            last = 0
            for m, font in zip(re.finditer(_FAMILY_PAT, joined_for_family), is_font, strict=True):
                pieces.append(joined_for_family[last : m.start()])
                pieces.append(m.expand(r'\1 = "sans"') if font else m.group(0))
                last = m.end()
            joined_for_family = "".join(pieces) + joined_for_family[last:]
            txt = list(strsplit(joined_for_family, "\n", fixed=True))

        injected: list[str] = []
        if is_r and inject_libs is not None and fn in inject_libs:
            injected = [p for p in _chr_list(inject_libs[fn]) if p is not None and p != ""]
        if injected:
            txt = [*(f"library({p})  # [reproducibility_check injected]" for p in injected), *txt]
        if prepend is not None and fn in prepend:
            txt = [*_chr_list(prepend[fn]), *txt]

        tgt = script_target(fn, i)
        dest = _file_path(root_s, _portable_path(tgt))
        _dir_create(os.path.dirname(dest))
        # a file there that is not text (R data saved under a name ending in .R) is
        # kept; a script that cannot be written is run and reported as failed (#568)
        keep_binary = os.path.exists(dest) and _repro_binary_kind(dest) is not None
        written = keep_binary
        if not keep_binary:
            try:
                _write_lines(txt, dest)
                written = True
            except (OSError, UnicodeError):
                written = False
        if not written:
            _message(f"[repro]   could not write '{fn}' to {dest}")
        rows.append(
            (
                fn,
                dest,
                root_s if run_dir == "." else _file_path(root_s, _portable_path(run_dir)),
                setwd_n,
                env_n,
                "; ".join(slashes),
                "; ".join(folder_removed),
                ", ".join(setwd_paths),
                "; ".join(lib_loc_paths),
                family_n,
                "; ".join(f'{x} -> "sans"' for x in family_matches),
                ", ".join(injected) if injected else None,
            )
        )
    return _frame(
        {
            "file_name": ("string", [r[0] for r in rows]),
            "script_path": ("string", [r[1] for r in rows]),
            "run_dir": ("string", [r[2] for r in rows]),
            "setwd_removed": ("Int64", [r[3] for r in rows]),
            "env_calls_replaced": ("Int64", [r[4] for r in rows]),
            "slashes_fixed": ("string", [r[5] for r in rows]),
            "setwd_folder_removed": ("string", [r[6] for r in rows]),
            "setwd_paths": ("string", [r[7] for r in rows]),
            "lib_loc_removed": ("string", [r[8] for r in rows]),
            "family_replaced": ("Int64", [r[9] for r in rows]),
            "family_detail": ("string", [r[10] for r in rows]),
            "library_injected": ("string", [r[11] for r in rows]),
        }
    )


# ---------------------------------------------------------------------------
# installing dependencies
# ---------------------------------------------------------------------------


_INSTALL_DTYPES = {
    "package": "string",
    "source": "string",
    "installed": "boolean",
    "message": "string",
    "via_archive": "boolean",
    "category": "string",
    "version_recorded": "string",
    "version_installed": "string",
    "version_note": "string",
}


def _install_frame(rows: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    """``dplyr::bind_rows()`` of per-package result rows."""
    if not rows:
        return _frame({k: (t, []) for k, t in _INSTALL_DTYPES.items()})
    parts = [
        # one-row frames, R's data.frame(); a row without a version column has NA there
        _frame({k: (t, [row.get(k)]) for k, t in _INSTALL_DTYPES.items()})
        for row in rows
    ]
    return bind_rows(parts)


# R code run for one package. It mirrors the body of repro_install_deps()'s
# per-package tryCatch() in a fresh R process whose library path has the
# throwaway library prepended, and prints a one-line JSON result.
_INSTALL_ONE_R = r"""
.mc_json_str <- function(s) {
  s <- enc2utf8(as.character(s))
  s <- gsub("\\", "\\\\", s, fixed = TRUE)
  s <- gsub("\"", "\\\"", s, fixed = TRUE)
  s <- gsub("\n", "\\n", s, fixed = TRUE)
  s <- gsub("\r", "\\r", s, fixed = TRUE)
  s <- gsub("\t", "\\t", s, fixed = TRUE)
  s <- vapply(s, function(x) {
    cp <- utf8ToInt(x)
    if (length(cp) && any(is.na(cp))) return(x)
    ctl <- cp < 32L
    if (!any(ctl)) return(x)
    paste(ifelse(ctl, sprintf("\\u%04x", cp), vapply(cp, intToUtf8, "")), collapse = "")
  }, "", USE.NAMES = FALSE)
  paste0("\"", s, "\"")
}
.mc_json_opt <- function(s) if (length(s) != 1 || is.na(s)) "null" else .mc_json_str(s)
old_lib <- .libPaths()
dir.create(lib_dir, recursive = TRUE, showWarnings = FALSE)
.libPaths(c(lib_dir, old_lib))
repos <- getOption("repos")
if (is.null(repos) || !length(repos) || any(!nzchar(repos)) || any(repos == "@CRAN@"))
  options(repos = c(CRAN = "https://cloud.r-project.org"))
if (!exists("version", inherits = FALSE)) version <- NA_character_
if (!exists("cran_to_main_lib", inherits = FALSE)) cran_to_main_lib <- FALSE
# the version of a package in the given libraries (NA when it is not in them), read
# from its DESCRIPTION, so a namespace already loaded does not mislead it
installed_version <- function(pkg, lib_paths) {
  v <- suppressWarnings(tryCatch(
    utils::packageDescription(pkg, lib.loc = lib_paths, fields = "Version"),
    error = function(e) NA_character_))
  if (length(v) != 1 || is.na(v)) NA_character_ else as.character(v)
}
same_version <- function(a, b) {
  if (length(a) != 1 || length(b) != 1 || is.na(a) || is.na(b)) return(FALSE)
  isTRUE(tryCatch(package_version(a) == package_version(b), error = function(e) FALSE))
}
load_error <- function(pkg, lib_dir) {
  msg <- tryCatch({
    loadNamespace(pkg, lib.loc = c(lib_dir, .libPaths()))
    ""
  }, error = function(e) conditionMessage(e))
  substr(gsub("[[:space:]]+", " ", msg), 1, 300)
}
emit <- function(ok, msg, skipped = FALSE, extra = "")
  cat("\n<<repro-result>>{\"ok\":", if (isTRUE(ok)) "true" else "false",
      ",\"skipped\":", if (isTRUE(skipped)) "true" else "false",
      ",\"msg\":", .mc_json_str(msg),
      ",\"version_installed\":", .mc_json_opt(installed_version(pkg, c(lib_dir, old_lib))),
      extra, "}\n", sep = "")
if (mode == "check_main") {
  have <- installed_version(pkg, c(lib_dir, old_lib))
  if (same_version(have, version)) {
    emit(TRUE, "", skipped = TRUE, extra = ",\"same_version\":true")
  } else {
    emit(TRUE, "", skipped = is.na(version) && isTRUE(cran_to_main_lib) &&
           length(find.package(pkg, lib.loc = old_lib, quiet = TRUE)) > 0,
         extra = paste0(",\"install_lib\":", .mc_json_str(old_lib[1])))
  }
} else if (mode == "install") {
  cran_main <- identical(src, "cran") && isTRUE(cran_to_main_lib) && is.na(version)
  {
    gh_avail <- requireNamespace("remotes", quietly = TRUE)
    install_lib <- if (cran_main) old_lib[1] else lib_dir
    install_warnings <- character(0)
    version_note <- ""
    version_done <- FALSE
    if (!is.na(version) && identical(src, "bioc")) {
      version_note <- "Bioconductor versions follow the R version, so the current version was installed"
    } else if (!is.na(version) && identical(src, "cran")) {
      if (gh_avail) {
        version_done <- tryCatch({
          remotes::install_version(pkg, version = version, lib = lib_dir, upgrade = "never",
                                   dependencies = NA, force = TRUE, quiet = FALSE)
          same_version(installed_version(pkg, lib_dir), version)
        }, error = function(e) {
          version_note <<- conditionMessage(e)
          FALSE
        })
        if (!version_done && !nzchar(version_note))
          version_note <- paste0("the recorded version could not be installed on R ", getRversion())
      } else {
        version_note <- "the 'remotes' package is needed to install a recorded version"
      }
    }
    res <- tryCatch({
      # Warnings are recorded (not silenced) for the load check below: see
      # _repro_not_loadable_msg() in the calling Python.
      withCallingHandlers({
      if (version_done) {
        # installed above
      } else if (identical(src, "github")) {
        if (!gh_avail) stop("the 'remotes' package is needed to install GitHub sources")
        remotes::install_github(ref, lib = lib_dir, upgrade = "never", quiet = FALSE)
      } else if (identical(src, "url")) {
        utils::install.packages(ref, lib = lib_dir, repos = NULL, quiet = FALSE)
      } else if (identical(src, "bioc")) {
        if (!requireNamespace("BiocManager", quietly = TRUE))
          utils::install.packages("BiocManager", lib = lib_dir, quiet = FALSE)
        BiocManager::install(pkg, lib = lib_dir, update = FALSE, ask = FALSE)
      } else {
        utils::install.packages(pkg, lib = install_lib, quiet = FALSE)
      }
      }, warning = function(w)
        install_warnings <<- c(install_warnings, conditionMessage(w)))
      # where it was installed is checked as well: the load check also passes for a
      # copy in the main library or a namespace already loaded, and a failed
      # install_github() does not stop
      in_main <- identical(src, "cran") && is.na(version) &&
        !is.na(installed_version(pkg, old_lib))
      if ((!file.exists(file.path(install_lib, pkg, "DESCRIPTION")) && !in_main) ||
          (!requireNamespace(pkg, quietly = TRUE, lib.loc = lib_dir) &&
           !requireNamespace(pkg, quietly = TRUE)))
        list(ok = FALSE, msg = "", not_loadable = TRUE)
      else list(ok = TRUE, msg = "")
    }, error = function(e) list(ok = FALSE, msg = conditionMessage(e)))
    warn_json <- if (isTRUE(res$not_loadable) && length(install_warnings))
      paste0(",\"warnings\":[", paste(.mc_json_str(install_warnings), collapse = ","), "]")
    else ""
    emit(res$ok, res$msg,
         extra = paste0(",\"install_lib\":", .mc_json_str(install_lib),
                        ",\"version_note\":", .mc_json_str(version_note),
                        if (isTRUE(res$not_loadable))
                          paste0(",\"not_loadable\":true,\"load_error\":",
                                 .mc_json_str(load_error(pkg, lib_dir))) else "",
                        warn_json))
  }
} else if (mode == "fallback") {
  # An install from GitHub or a web address that failed, for a package that is on CRAN
  # or Bioconductor: installed from there (#598). CRAN's list is read from the
  # repositories R is set to.
  ap <- tryCatch(suppressWarnings(utils::available.packages(repos = getOption("repos"))),
                 error = function(e) NULL)
  on_cran <- !is.null(ap) && pkg %in% rownames(ap)
  alt <- if (on_cran) "CRAN" else if (isTRUE(is_bioc)) "Bioconductor" else NA_character_
  if (is.na(alt)) {
    emit(FALSE, "", extra = ",\"alt\":null")
  } else {
    message("[repro]     '", pkg, "' could not be installed from ", ref, "; installing it from ", alt, " ...")
    try(if (alt == "CRAN") utils::install.packages(pkg, lib = lib_dir, quiet = FALSE) else {
      if (!requireNamespace("BiocManager", quietly = TRUE))
        utils::install.packages("BiocManager", lib = lib_dir, quiet = FALSE)
      BiocManager::install(pkg, lib = lib_dir, update = FALSE, ask = FALSE)
    })
    ok <- file.exists(file.path(lib_dir, pkg, "DESCRIPTION")) &&
      requireNamespace(pkg, quietly = TRUE, lib.loc = lib_dir)
    emit(ok, "", extra = paste0(",\"alt\":", .mc_json_str(alt)))
  }
} else if (mode == "archive") {
  ok <- tryCatch({
    if (requireNamespace("remotes", quietly = TRUE)) {
      remotes::install_url(tarball_url, dependencies = NA, lib = install_lib,
                           upgrade = "never", quiet = FALSE)
    } else {
      utils::install.packages(tarball_url, lib = install_lib,
                              repos = NULL, type = "source", quiet = FALSE)
    }
    if (!requireNamespace(pkg, quietly = TRUE, lib.loc = lib_dir) &&
        !requireNamespace(pkg, quietly = TRUE))
      stop("installed but package '", pkg, "' is not loadable")
    TRUE
  }, error = function(e) conditionMessage(e))
  if (isTRUE(ok)) emit(TRUE, "") else emit(FALSE, ok)
}
"""


def _install_r(
    mode: str, lib_dir: str, *, timeout: float | None = None, **values: Any
) -> dict[str, Any]:
    """Run one step of the install code (:data:`_INSTALL_ONE_R`) in R.

    Returns the step's result (``ok``, ``msg``, ``skipped``, ``install_lib``,
    ``version_installed``, ``version_note``); a missing ``Rscript``, a timeout or a
    crash is a failed step.
    """
    header = [f"mode <- {_r_string(mode)}", f"lib_dir <- {_r_string(lib_dir)}"]
    for key, val in values.items():
        if isinstance(val, bool):
            header.append(f"{key} <- {'TRUE' if val else 'FALSE'}")
        else:
            # _r_string(None) is NA_character_
            header.append(f"{key} <- {_r_string(as_str(val))}")
    code = "\n".join(header) + "\n" + _INSTALL_ONE_R
    try:
        res = _run_r(code, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "msg": f"install timed out after {timeout}s"}
    except OSError as exc:
        return {"ok": False, "msg": str(exc)}
    if res is None:
        return {"ok": False, "msg": "Rscript not found: install R or set PYTACHECK_RSCRIPT"}
    for chunk in reversed(res.stdout.split("<<repro-result>>")[1:]):
        try:
            out: dict[str, Any] = json.loads(chunk.strip().splitlines()[0])
            return out
        except (ValueError, IndexError):
            continue
    err = (res.stderr or "").strip()
    return {"ok": False, "msg": err or f"R exited with status {res.returncode}"}


def _repro_same_version(a: str | None, b: str | None) -> bool:
    """Port of R/reproducibility_check.R::.repro_same_version().

    Whether two version strings are the same version: ``"1.1-21"`` and ``"1.1.21"``
    are (R's ``package_version()``, whose parts are split at ``.`` and ``-``).
    """
    if a is None or b is None:
        return False

    def parts(v: str) -> tuple[int, ...] | None:
        bits = re.split(r"[.-]", v.strip())
        if not bits or not all(re.fullmatch(r"[0-9]+", x) for x in bits):
            return None
        return tuple(int(x) for x in bits)

    pa, pb = parts(a), parts(b)
    if pa is None or pb is None:
        return False
    return pa == pb


def _repro_deadline(total_timeout: Any) -> float | None:
    """Port of R/reproducibility_check.R::.repro_deadline().

    The moment (``time.time()`` seconds) after which no further script is started:
    *total_timeout* seconds from now, or a clock time (a :class:`datetime.datetime`)
    that is the deadline itself, such as a server's job limit minus the time its report
    needs. ``None``, NaN or infinity: no limit.
    """
    import datetime as _dt
    import time as _time

    if total_timeout is None or isinstance(total_timeout, bool):
        return None
    if isinstance(total_timeout, _dt.datetime):
        return total_timeout.timestamp()
    t = as_float(total_timeout)
    if t is None or not math.isfinite(t):
        return None
    return _time.time() + t


def _clock_seconds(when: Any) -> float | None:
    """A clock time (a :class:`datetime.datetime`, or ``time.time()`` seconds) as seconds; ``None`` stays."""
    import datetime as _dt

    if when is None:
        return None
    if isinstance(when, _dt.datetime):
        return when.timestamp()
    return as_float(when)


def _repro_time_left(deadline: float | None) -> float:
    """Port of R/reproducibility_check.R::.repro_time_left(): seconds until *deadline*."""
    import time as _time

    return math.inf if deadline is None else deadline - _time.time()


def _repro_deadline_text(total_timeout: Any) -> str:
    """Port of R/reproducibility_check.R::.repro_deadline_text(): the limit as the report names it."""
    import datetime as _dt

    if isinstance(total_timeout, _dt.datetime):
        return "the time for all scripts together"
    t = as_float(total_timeout)
    mins = "Inf" if t is None or not math.isfinite(t) else f"{t / 60:g}"
    return f"the time for all scripts together ({mins} minutes, `total_timeout`)"


def _repro_short_of_time(total_timeout: Any, need: float = 0) -> bool:
    """Port of R ``.repro_short_of_time()``.

    With a clock time as *total_timeout* (a server's job limit minus the time for its
    report): whether at most *need* seconds are left. Always False for a time in seconds,
    which counts from the first script and so has not begun.
    """
    import datetime as _dt

    return isinstance(total_timeout, _dt.datetime) and (
        _repro_time_left(total_timeout.timestamp()) <= need
    )


def _repro_clock_deadline(total_timeout: Any) -> Any:
    """Port of R ``.repro_clock_deadline()``: *total_timeout* when it is a clock time, else ``None``."""
    import datetime as _dt

    return total_timeout if isinstance(total_timeout, _dt.datetime) else None


def _repro_min_prep_time() -> float:
    """Port of R ``.repro_min_prep_time()``.

    The seconds a clock time as ``total_timeout`` must leave when the check starts, for
    it to copy the repository, install its packages and run its scripts. With less, none
    of that is done and every script is recorded as not started (``skipped_time_limit``),
    so the job still has its time for the report. The option ``metacheck.repro_min_time``
    sets it (default 900, 15 minutes).
    """
    from metacheck.utils import get_option

    v = as_float(get_option("metacheck.repro_min_time", 900))
    return 900.0 if v is None else v


def _repro_no_prep_text(left: float | None) -> str:
    """Port of R ``.repro_no_prep_text()``: the report's sentence when too little time was left; ``""`` otherwise."""
    if left is None:
        return ""
    return (
        f" The checks before this one had left {_repro_duration(left)} before the time limit, too"
        " little to copy the repository, install its packages and run its"
        f" scripts (at least {_repro_duration(_repro_min_prep_time())}), so none of that was done."
    )


def _repro_install_limit(
    install_timeout: float | None, timeout: float, total_timeout: Any, recorded: bool = False
) -> float:
    """Port of R/reproducibility_check.R::.repro_install_limit().

    The seconds for installing a paper's packages: *install_timeout*, else the
    per-script *timeout* (at least an hour when recorded versions are installed, since
    those are built from source). With a clock time as *total_timeout*, installing may
    take at most half of what is left (and at least a minute), so the scripts still run.
    """
    import datetime as _dt

    if install_timeout is not None:
        limit = float(install_timeout)
    else:
        limit = max(float(timeout), 3600.0) if recorded else float(timeout)
    if isinstance(total_timeout, _dt.datetime):
        left = _repro_time_left(total_timeout.timestamp())
        limit = min(limit, max(60.0, left / 2))
    return limit


def _repro_duration(secs: float) -> str:
    """Port of R/reproducibility_check.R::.repro_duration(): ``"12 sec"``, ``"4 min"``, ``"1 h 5 min"``."""
    secs = max(0.0, float(r_round(secs)))
    if secs < 60:
        return f"{int(secs):d} sec"
    mins = int(r_round(secs / 60))
    if mins < 60:
        return f"{mins:d} min"
    return f"{mins // 60:d} h {mins % 60:d} min"


def _repro_install_what(
    i: int, n: int, pkg: str | None, version: str | None, started: float
) -> str:
    """Port of ``.repro_install_what()``: what the install is doing, for its progress line.

    ``"Installing pkg 1.2 (3 of 10, 4 min so far, about 9 min left)"``: the package, its
    version (when one is recorded), the count, the time since *started*
    (``time.monotonic()``) and, from the second package on, the time left at the pace so far.
    """
    elapsed = time.monotonic() - started
    ver = "" if version is None else f" {version}"
    left = f", about {_repro_duration(elapsed / (i - 1) * (n - i + 1))} left" if i > 1 else ""
    return f"Installing {pkg}{ver} ({i:d} of {n:d}, {_repro_duration(elapsed)} so far{left})"


def repro_install_deps(
    install_deps: pd.DataFrame | None,
    lib_dir: str | os.PathLike[str],
    cran_to_main_lib: bool = False,
    deadline: float | None = None,
) -> pd.DataFrame:
    """Install a paper's declared dependencies into a throwaway library.

    Port of ``R/reproducibility_check.R::repro_install_deps()``. Each
    non-base dependency (``package``, ``source``, ``ref``, and optionally
    ``version``) is installed with R (``install.packages()``,
    ``BiocManager::install()``, ``remotes::install_github()`` or
    ``install.packages(url)``) into *lib_dir*, or -- CRAN packages with
    *cran_to_main_lib* and no recorded version -- into R's default library (skipped
    when already installed there). A row with a ``version`` is installed at that
    version with ``remotes::install_version()`` into *lib_dir*, unless the libraries
    already hold it; when it cannot be, the current version is installed and
    ``version_note`` says why (a Bioconductor package always gets its current
    version). A CRAN package missing from live CRAN is retried once from the CRAN
    Archive. Once *deadline* (``time.time()`` seconds, see :func:`_repro_deadline`) has
    passed, no further package is started.

    Returns ``package``, ``source``, ``installed``, ``message``, ``via_archive``,
    ``category``, ``version_recorded``, ``version_installed`` and ``version_note``.
    """
    if install_deps is None or len(install_deps) == 0:
        return _install_frame([])
    lib = os.fspath(lib_dir)
    _dir_create(lib)
    pkgs = _chr_list(install_deps["package"])
    srcs = _chr_list(install_deps["source"])
    n = len(pkgs)
    refs = _chr_list(install_deps["ref"]) if "ref" in install_deps.columns else [None] * n
    versions = (
        _chr_list(install_deps["version"]) if "version" in install_deps.columns else [None] * n
    )
    from metacheck.utils import pb

    rows: list[dict[str, Any]] = []
    # what is being installed, with the count and the time so far and left (#568)
    bar = pb(None, "(:spin) :what")
    started = time.monotonic()
    try:
        for i, (pkg, src, ref, version) in enumerate(
            zip(pkgs, srcs, refs, versions, strict=True), start=1
        ):
            bar.tick(1, {"what": _repro_install_what(i, n, pkg, version, started)})
            if _repro_time_left(deadline) <= 0:
                # a package left out may still be found in another library when the scripts run
                chk = _install_r("check_main", lib, pkg=pkg, version=version)
                rows.append(
                    {
                        "package": pkg,
                        "source": src,
                        "installed": False,
                        "message": "not started: the time for installing had run out",
                        "via_archive": False,
                        "category": "other",
                        "version_recorded": version,
                        "version_installed": chk.get("version_installed"),
                        "version_note": "",
                    }
                )
                continue
            cran_main = src == "cran" and _r_is_true(cran_to_main_lib) and version is None
            main_lib: str | None = None
            if cran_main or version is not None:
                # the recorded version already there, or a CRAN package already in the REAL
                # library (not the throwaway one): nothing to do
                chk = _install_r(
                    "check_main", lib, pkg=pkg, version=version, cran_to_main_lib=cran_main
                )
                main_lib = chk.get("install_lib")
                if chk.get("skipped"):
                    if chk.get("same_version"):
                        _message(
                            "[repro]     '", pkg, "' ", version, " is already installed; skipping."
                        )
                    else:
                        _message(
                            "[repro]     '", pkg, "' already installed (main library); skipping."
                        )
                    # with a category (NA) like every other row (UPSTREAM_ISSUES U135)
                    rows.append(
                        {
                            "package": pkg,
                            "source": src,
                            "installed": True,
                            "message": "",
                            "via_archive": False,
                            "category": None,
                            "version_recorded": version,
                            "version_installed": chk.get("version_installed"),
                            "version_note": "",
                        }
                    )
                    continue
            _message(
                "[repro]     installing '",
                pkg,
                "'",
                "" if version is None else f" {version}",
                " (source: ",
                src,
                ", into main library" if cran_main else ", into throwaway library",
                ") ...",
            )
            res = _install_r(
                "install",
                lib,
                pkg=pkg,
                src=src,
                ref=ref,
                version=version,
                cran_to_main_lib=_r_is_true(cran_to_main_lib),
            )
            ok = bool(res.get("ok"))
            msg = str(res.get("msg") or "")
            version_note = str(res.get("version_note") or "")
            if res.get("not_loadable"):
                msg = _repro_not_loadable_msg(
                    pkg, res.get("warnings") or [], str(res.get("load_error") or "")
                )
            via_archive = False
            have = as_str(res.get("version_installed"))
            # An install from GitHub or a web address that failed, for a package that is
            # on CRAN or Bioconductor: installed from there, with a note (#598). A failed
            # download says so, not "the install did not put the package in the library".
            if not ok and src in ("github", "url"):
                first = _repro_download_failed_msg(ref, [*(res.get("warnings") or []), msg]) or msg
                fb = _install_r(
                    "fallback",
                    lib,
                    pkg=pkg,
                    ref=ref,
                    is_bioc=pkg in _repro_bioc_packages(),
                )
                alt = as_str(fb.get("alt"))
                if alt is not None:
                    if fb.get("ok"):
                        ok, msg = True, ""
                        have = as_str(fb.get("version_installed")) or have
                        version_note = (
                            f"installed from {alt}: the install from {ref} failed ({first[:200]})"
                        )
                    else:
                        msg = f"{first}; installing it from {alt} failed too"
                else:
                    msg = first
            if not ok and src == "cran":
                _message(
                    "[repro]     '",
                    pkg,
                    "' not available on live CRAN; trying the CRAN Archive (last published version) ...",
                )
                install_lib = res.get("install_lib") or (main_lib if cran_main else lib) or lib
                res2 = _repro_cran_archive_install(pkg, str(install_lib), lib)
                if res2["ok"]:
                    _message(
                        "[repro]     '",
                        pkg,
                        "' installed from the CRAN Archive (",
                        res2["version"],
                        ").",
                    )
                    ok, msg, via_archive = True, "", True
                    have = as_str(res2.get("version_installed")) or have
                else:
                    _message(
                        "[repro]     '",
                        pkg,
                        "' could not be installed from the CRAN Archive either: ",
                        res2["msg"],
                    )
                    msg = f"{msg} (CRAN Archive retry also failed: {res2['msg']})"
            if ok and version is not None and not _repro_same_version(have, version):
                version_note = (
                    version_note or "the source gave another version than the recorded one"
                )
            rows.append(
                {
                    "package": pkg,
                    "source": src,
                    "installed": ok,
                    "message": msg,
                    "via_archive": via_archive,
                    "category": None if ok else _repro_classify_install_message(msg),
                    "version_recorded": version,
                    "version_installed": have,
                    "version_note": version_note,
                }
            )
    finally:
        bar.terminate()
    return _install_frame(rows)


def _repro_download_failed_msg(ref: str | None, msgs: Sequence[str | None]) -> str | None:
    """Port of R ``.repro_download_failed_msg()`` (#598).

    ``"could not download <ref>: <why>"`` when a GitHub or URL install failed on the
    download (a dead address, a missing repository), else ``None``.
    """
    hit = [
        m
        for m in msgs
        if m is not None
        and re.search(
            "cannot open URL|cannot open the connection|download|status was|Could not resolve host|"
            "HTTP error|HTTP status|404|403|Failed to connect",
            m,
            re.IGNORECASE,
        )
    ]
    if not hit:
        return None
    return f"could not download {ref}: " + re.sub(r"\s+", " ", hit[0])[:200]


def _repro_not_loadable_msg(
    pkg: str | None, warnings: Sequence[str | None] | None, reason: str = ""
) -> str:
    """The failure message for a package that cannot be loaded after its install step.

    Port of ``R/reproducibility_check.R::.repro_not_loadable_msg()``.
    ``install.packages()`` does not stop for a package it cannot find: it
    warns "package 'x' is not available for this version of R" and returns
    normally. The install step's own "not available" warning is the real
    reason, so it is reported when there is one (issue #421); otherwise the
    package is "installed but ... not loadable", with what R said when loading failed
    (*reason*, often a system library missing from the image).
    """
    unavailable = [w for w in warnings or () if w is not None and "is not available" in w]
    if unavailable:
        return "; ".join(dict.fromkeys(unavailable))
    suffix = f": {reason}" if reason else ""
    return f"installed but package '{pkg}' is not loadable{suffix}"


_COMPILE_PAT = (
    r"configure(:| failed| error)|non-zero exit status|compilation failed|make(:| error)\b"
    r"|\bgcc\b|g\+\+|gfortran"
)
_NETWORK_PAT = (
    r"could not resolve host|couldn.t connect to server|timed? ?out|connection (refused|reset"
    r"|timed out)|network is unreachable|ssl (connect|certificate) error|could not reach"
)
_TRANSITIVE_PAT = (
    r"there is no package called|depend(s|ency|encies) .*(is|are) not available"
    r"|unable to (find|locate) (required )?package"
)
_CRAN_UNAVAILABLE_PAT = (
    r"is not available (for|as a package)|package .* is not available"
    r"|was built (for|under) R version"
)


def _repro_classify_install_message(msg: str | None) -> str:
    """Classify why a package installation failed (``.repro_classify_install_message()``).

    One of ``"compile_failure"``, ``"network"``,
    ``"transitive_dependency_missing"``, ``"cran_unavailable"`` or
    ``"other"``.
    """
    if msg is None or is_missing(msg) or msg == "":
        return "other"
    if grepl(_COMPILE_PAT, msg, ignore_case=True, perl=True):
        return "compile_failure"
    if grepl(_NETWORK_PAT, msg, ignore_case=True, perl=True):
        return "network"
    if grepl(_TRANSITIVE_PAT, msg, ignore_case=True, perl=True):
        return "transitive_dependency_missing"
    if grepl(_CRAN_UNAVAILABLE_PAT, msg, ignore_case=True, perl=True):
        return "cran_unavailable"
    return "other"


def _read_url_lines(url: str) -> tuple[list[str] | None, bool]:
    """``readLines(url)``: ``(lines, not_found)``.

    The lines are ``None`` when the URL cannot be read; *not_found* tells a
    ``404`` (the server answered: nothing there) from a failed request. In
    R the status is only in ``readLines()``'s warning.
    """
    from metacheck import http

    try:
        resp = http.request("GET", url, max_tries=1)
    except Exception:
        return None, False
    if resp is None:
        return None, False
    if resp.status_code >= 400:
        return None, resp.status_code == 404
    text = resp.text
    if text == "":
        return [], False
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines, False


def _parse_archive_date(s: str) -> float:
    """``as.POSIXct(s, tz = "UTC")`` of an Archive listing date, as a timestamp.

    ``-inf`` when *s* holds no date: such a line sorts last instead of
    failing the whole install, as R's ``as.POSIXct()`` outside its
    ``tryCatch()`` does (UPSTREAM_ISSUES U135).
    """
    import datetime as dt

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M"):
        try:
            return dt.datetime.strptime(s, fmt).replace(tzinfo=dt.UTC).timestamp()
        except ValueError:
            continue
    return -math.inf


def _repro_cran_archive_install(pkg: str | None, install_lib: str, lib_dir: str) -> dict[str, Any]:
    """Install a package's most recent CRAN Archive version (``.repro_cran_archive_install()``).

    Lists ``https://cran.r-project.org/src/contrib/Archive/<pkg>/``, takes
    the tarball with the latest date and installs it with R
    (``remotes::install_url()``, else ``install.packages()``). Returns
    ``{"ok", "msg", "version"}``.
    """

    def fail(msg: str) -> dict[str, Any]:
        return {"ok": False, "msg": msg, "version": None}

    archive_url = f"https://cran.r-project.org/src/contrib/Archive/{pkg}/"
    listing, not_found = _read_url_lines(archive_url)
    if listing is None:
        # a 404 means the Archive has no folder for this package (never
        # archived, or not a CRAN package at all), not that CRAN is
        # unreachable (issue #421)
        if not_found:
            return fail("package not found in the CRAN Archive")
        return fail("could not reach the CRAN Archive listing")
    tarball_pat = f"{pkg}_[0-9][^\"']*\\.tar\\.gz"
    hit_lines = [s for s, h in zip(listing, grepl(tarball_pat, listing), strict=True) if h]
    if not hit_lines:
        return fail("package not found in the CRAN Archive")
    date_pat = r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})"
    dates = [_parse_archive_date(d) for d in sub(".*" + date_pat + ".*", r"\1", hit_lines)]
    files = [m for m in regextract(tarball_pat, hit_lines) if m is not None]
    # the newest dated tarball (the last listed one when none has a date)
    order = sorted(
        range(len(files)), key=lambda i: (-dates[i], i if math.isfinite(dates[i]) else -i)
    )
    latest_file = files[order[0]]
    version = sub(f"^{pkg}_(.*)\\.tar\\.gz$", r"\1", latest_file)
    tarball_url = archive_url + latest_file
    res = _install_r("archive", lib_dir, pkg=pkg, install_lib=install_lib, tarball_url=tarball_url)
    if res.get("ok"):
        return {
            "ok": True,
            "msg": "",
            "version": version,
            "version_installed": res.get("version_installed"),
        }
    return fail(str(res.get("msg") or ""))


# ---------------------------------------------------------------------------
# running the scripts (the callr::r() analogue)
# ---------------------------------------------------------------------------

_CALLR_CRASH = (
    "! callr subprocess failed: could not start R, exited with non-zero status, "
    "has crashed or was killed"
)
_CALLR_TIMEOUT = "! callr timed out"

# The driver: run the capture runner (R/r-capture.R's .r_capture_runner(),
# ported in metacheck.statout.r_capture) the way callr::r(error = "error")
# does: an error is recorded for the parent (formatted like callr's error
# message) and printed to stderr with try(stop(e)), as callr's child does.
_DRIVER_R = r"""{libpaths}local({{
{json_r}
  helpers <- {helpers}
  henv <- environment(helpers[[1L]])
  for (nm in c(".mc_json", ".mc_json_str", ".mc_write_json"))
    assign(nm, get(nm), envir = henv)
  runner <- {runner}
  environment(runner) <- henv
  res <- tryCatch(
    runner({script}, {wd}, {capture}, helpers),
    error = function(e) e)
  if (inherits(res, "error")) {{
    call <- conditionCall(res)
    where <- if (is.null(call)) ": " else {{
      cl <- trimws(format(call))
      if (length(cl) > 1) cl <- paste0(cl[1], " ...")
      paste0(" in `", cl, "`:")
    }}
    writeLines(enc2utf8(paste0("! in callr subprocess.\nCaused by error", where,
                               "\n! ", conditionMessage(res))),
               {error_file}, useBytes = TRUE)
    try(stop(res))
    quit(save = "no", status = 1L)
  }}
}})
"""


def _driver_source(
    script: str | None, wd: str | None, capture_file: str, error_file: str, lib_dir: str | None
) -> str:
    from metacheck.statout.r_capture import _JSON_R, _RUNNER_R, _r_capture_helpers

    libpaths = f".libPaths(c({_r_string(lib_dir)}, .libPaths()))\n" if lib_dir else ""
    return _DRIVER_R.format(
        libpaths=libpaths,
        json_r=_JSON_R,
        helpers=_r_capture_helpers(),
        runner=_RUNNER_R,
        script=_r_string(script),
        wd=_r_string(wd),
        capture=_r_string(capture_file),
        error_file=_r_string(error_file),
    )


class _RunError(Exception):
    """A failed script run (callr's error condition); ``timeout`` for a timeout."""

    def __init__(self, message: str, timeout: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.timeout = timeout


def _read_cap(path: str | os.PathLike[str]) -> str:
    """``paste(readLines(f), collapse = "\\n")`` of a captured stream, ``""`` if absent."""
    if not os.path.exists(path):
        return ""
    return "\n".join(_read_lines(path))


def _callr_run(
    script: str | None,
    wd: str | None,
    capture_file: str,
    lib_dir: str | None,
    timeout: float | None,
    stdout: str,
    stderr: str,
) -> None:
    """Run *script* in a fresh R process (``callr::r(.r_capture_runner(), ...)``).

    Raises :class:`_RunError` with callr's error message when the script
    errors, times out or R dies.
    """
    exe = _rscript()
    if exe is None:
        raise RuntimeError(
            "Rscript is required to execute code (execute = TRUE): install R or set "
            "PYTACHECK_RSCRIPT."
        )
    with tempfile.TemporaryDirectory(prefix="pytacheck_callr_") as tmp:
        error_file = os.path.join(tmp, "error.txt")
        driver = os.path.join(tmp, "driver.R")
        Path(driver).write_text(
            _driver_source(
                # as given, like callr: the child starts in this process's
                # working directory and resolves the script after setwd(wd)
                script,
                wd,
                os.path.abspath(capture_file),
                error_file,
                lib_dir,
            ),
            encoding="utf-8",
        )
        env = {**os.environ, "R_TESTS": "", "R_BROWSER": "false", "R_PDFVIEWER": "false"}
        with open(stdout, "wb") as out, open(stderr, "wb") as err:
            try:
                proc = subprocess.run(  # noqa: S603 - the configured Rscript
                    [exe, "--no-save", "--no-restore", driver],
                    stdout=out,
                    stderr=err,
                    stdin=subprocess.DEVNULL,
                    timeout=timeout,
                    env=env,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise _RunError(_CALLR_TIMEOUT, timeout=True) from exc
        if os.path.exists(error_file):
            msg = Path(error_file).read_text(encoding="utf-8", errors="replace")
            raise _RunError(msg[:-1] if msg.endswith("\n") else msg)
        if proc.returncode != 0:
            raise _RunError(_CALLR_CRASH)


# R quotes names with sQuote()/dQuote(), which are typographic quotes in a
# UTF-8 locale ("there is no package called ‘pkg’"); metacheck's patterns
# match only ASCII quotes, so a failed dependency was never recognised
# (UPSTREAM_ISSUES U132)
_Q = "'\"\u2018\u2019\u201c\u201d"
_UNDEF_PAT = f"object [{_Q}]([^{_Q}]+)[{_Q}] not found"
_FN_PAT = f"could not find function [{_Q}]([^{_Q}]+)[{_Q}]"
_NOPKG_PAT = f"there is no package called [{_Q}]([^{_Q}]+)[{_Q}]"


def _first_capture(pattern: str, src: str) -> str:
    """``sub(".*<p>.*", "\\\\1", regmatches(src, regexpr(<p>, src)))``."""
    m = regextract(pattern, src)
    out: str = sub(".*" + pattern + ".*", r"\1", m)
    return out


def _classify_error(
    failed_deps: Sequence[str] | str, sources: Sequence[str]
) -> tuple[str | None, bool]:
    """``(undefined_var, dependency_unavailable)`` of a failed run.

    *sources* are the texts searched, in order, for each pattern (the error
    message and/or stderr).
    """
    undef_src: str | None = None
    for pat in (_UNDEF_PAT, _FN_PAT):
        for s in sources:
            if grepl(pat, s):
                undef_src = s
                break
        if undef_src is not None:
            break
    undef_var: str | None = None
    if undef_src is not None and grepl(_UNDEF_PAT, undef_src):
        undef_var = _first_capture(_UNDEF_PAT, undef_src)
    elif undef_src is not None and grepl(_FN_PAT, undef_src):
        undef_var = _first_capture(_FN_PAT, undef_src)
    nopkg_src = next((s for s in sources if grepl(_NOPKG_PAT, s)), None)
    nopkg_var = _first_capture(_NOPKG_PAT, nopkg_src) if nopkg_src is not None else None
    dep_unavailable = nopkg_var is not None and nopkg_var in _chr_list(failed_deps)
    return undef_var, dep_unavailable


_RUN_COLUMNS = {
    "file_name": "string",
    "outcome": "string",
    "error": "string",
    "error_type": "string",
    "undefined_var": "string",
    "stdout": "string",
    "stderr": "string",
    "elapsed": "float64",
    "script_lines": "object",
    "captures": "object",
}


def _run_frame(rows: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    return _frame({c: (t, [r[c] for r in rows]) for c, t in _RUN_COLUMNS.items()})


def _run_row(
    fn: str,
    outcome: str,
    error: str = "",
    error_type: str | None = None,
    undefined_var: str | None = None,
    stdout: str = "",
    stderr: str = "",
    elapsed: float = 0.0,
    script_lines: list[str] | None = None,
    captures: Any = None,
) -> dict[str, Any]:
    return {
        "file_name": fn,
        "outcome": outcome,
        "error": error,
        "error_type": error_type,
        "undefined_var": undefined_var,
        "stdout": stdout,
        "stderr": stderr,
        "elapsed": float(elapsed),
        "script_lines": [] if script_lines is None else script_lines,
        "captures": captures,
    }


def _ordered_names(run_tbl: pd.DataFrame, order: Any) -> list[str]:
    names = _chr_list(run_tbl["file_name"])
    order_l = _chr_list(order)
    first = [o for o in order_l if o is not None and o in names]
    return first + [str(x) for x in _setdiff(names, order_l)]


def _r_is_true(x: Any) -> bool:
    """R ``isTRUE()``: a single logical ``TRUE`` (a numpy/pandas bool counts, a number does not)."""
    import numpy as np

    if isinstance(x, pd.Series | pd.Index | np.ndarray | list | tuple):
        vals = list(x)
        return len(vals) == 1 and _r_is_true(vals[0])
    return isinstance(x, bool | np.bool_) and bool(x)


def _named_value(named: Any, key: str) -> tuple[bool, Any]:
    """``key %in% names(x)`` and ``x[[key]]`` (the first entry of that name)."""
    if isinstance(named, pd.Series):
        hits = [i for i, k in enumerate(named.index.tolist()) if k == key]
        return (True, named.iloc[hits[0]]) if hits else (False, None)
    if isinstance(named, Mapping):
        return (True, named[key]) if key in named else (False, None)
    return False, None


def _pre_run_outcome(fn: str, skip: Any, parses: Any) -> dict[str, Any] | None:
    """The ``not_parsed``/``skipped_missing_inputs`` row of a script that is not run.

    *parses* is R's named logical vector (a mapping or a ``Series`` indexed by
    file name); *skip* a character vector (a single string is one name).
    """
    if parses is not None:
        found, value = _named_value(parses, fn)
        if found and not _r_is_true(value):
            return _run_row(fn, "not_parsed")
    if fn in _chr_list(skip):
        return _run_row(fn, "skipped_missing_inputs")
    return None


def _script_lines(path: str | None) -> list[str]:
    try:
        return _read_lines(path) if path is not None else []
    except OSError:
        return []


#: The error of a script the runners did not start (R's ``skipped_time_limit`` row).
_NOT_STARTED = "not started: the time for all scripts together had run out"


def _repro_script_limit(timeout: float, deadline: float | None) -> tuple[float, bool] | None:
    """A script's time limit under the *deadline* of all scripts together.

    The runners' ``left``/``cut_by_deadline`` of metacheck 0.4.3.7: ``None`` when that time
    has run out (the script is not started), else the seconds the script may run and
    whether the deadline, not its own *timeout*, sets them (#581).
    """
    left = _repro_time_left(deadline)
    if left <= 0:
        return None
    if not math.isfinite(left):
        return timeout, False
    secs = math.ceil(left)
    return min(timeout, secs), secs < timeout


def repro_run_scripts(
    run_tbl: pd.DataFrame | None,
    order: Any,
    lib_dir: str | os.PathLike[str] | None = None,
    timeout: float = 600,
    skip: Sequence[str] | str = (),
    parses: Mapping[str, Any] | pd.Series | None = None,
    failed_deps: Sequence[str] | str = (),
    deadline: float | None = None,
) -> pd.DataFrame:
    """Run the paper's scripts, in order, in isolated R subprocesses.

    Port of ``R/reproducibility_check.R::repro_run_scripts()``. Each script
    of *run_tbl* (``file_name``, ``script_path``, ``run_dir``) runs in a
    fresh ``Rscript`` process from its ``run_dir`` with *lib_dir* first on
    the library path and a per-script *timeout*; its statements are echoed
    and statistical result objects captured (``R/r-capture.R``). Scripts in
    *skip* or failing to parse (*parses*) are not run. Once *deadline*
    (``time.time()`` seconds, see :func:`_repro_deadline`) has passed, a
    script is not started (``skipped_time_limit``), and a started script
    stops at it (``error_type`` ``"deadline"``). Returns
    ``file_name``, ``outcome`` (``ran_ok``, ``errored``, ``timed_out``,
    ``not_parsed``, ``skipped_missing_inputs``, ``dependency_unavailable``,
    ``skipped_time_limit``), ``error``, ``error_type``, ``undefined_var``,
    ``stdout``, ``stderr``, ``elapsed``, ``script_lines`` and ``captures``.
    """
    if run_tbl is None or len(run_tbl) == 0:
        return _run_frame([])
    if _rscript() is None:
        raise RuntimeError(
            "Rscript is required to execute code (execute = TRUE): install R or set "
            "PYTACHECK_RSCRIPT."
        )
    from metacheck.statout.r_capture import _read_captures
    from metacheck.utils import pb

    ordered = _ordered_names(run_tbl, order)
    lib = os.fspath(lib_dir) if lib_dir is not None else None
    names = _chr_list(run_tbl["file_name"])
    paths = _chr_list(run_tbl["script_path"])
    dirs = _chr_list(run_tbl["run_dir"])

    bar = pb(len(ordered), ":what [:bar] :current/:total")
    rows: list[dict[str, Any]] = []
    try:
        bar.tick(0, {"what": ""})
        for fn in ordered:
            bar.tick(1, {"what": fn})
            i = names.index(fn)
            pre = _pre_run_outcome(fn, skip, parses)
            if pre is not None:
                rows.append(pre)
                continue
            # Once the time for all scripts together has run out, a script is not
            # started; a script that is started stops when that time runs out
            limit = _repro_script_limit(timeout, deadline)
            if limit is None:
                rows.append(_run_row(fn, "skipped_time_limit", _NOT_STARTED))
                continue
            run_timeout, cut_by_deadline = limit
            exec_lines = _script_lines(paths[i])
            _message(
                "[repro]   -> running '", fn, "' (timeout ", as_character(run_timeout), "s) ..."
            )
            with tempfile.TemporaryDirectory(prefix="pytacheck_run_") as tmp:
                out_file = os.path.join(tmp, "out.txt")
                err_file = os.path.join(tmp, "err.txt")
                cap_file = os.path.join(tmp, "captures.json")
                t0 = time.monotonic()
                error: _RunError | None = None
                try:
                    _callr_run(paths[i], dirs[i], cap_file, lib, run_timeout, out_file, err_file)
                except _RunError as exc:
                    error = exc
                elapsed = time.monotonic() - t0
                captures = _read_captures(cap_file) if os.path.exists(cap_file) else None
                so, se = _read_cap(out_file), _read_cap(err_file)
            _message(
                "[repro]   <- '",
                fn,
                "' done in ",
                as_character(round(elapsed, 1)),
                "s (",
                "condition/error" if error is not None else "ok",
                "; stdout ",
                str(len(so)),
                " chars, stderr ",
                str(len(se)),
                " chars)",
            )
            if error is not None:
                msg = error.message
                is_timeout = error.timeout or bool(grepl("timed? ?out", msg, ignore_case=True))
                undef_var, dep_unavailable = _classify_error(failed_deps, [msg, se])
                etype = (
                    "timeout"
                    if is_timeout
                    else "dependency_unavailable"
                    if dep_unavailable
                    else "undefined_variable"
                    if undef_var is not None
                    else "runtime"
                )
                outc = (
                    "timed_out"
                    if is_timeout
                    else "dependency_unavailable"
                    if dep_unavailable
                    else "errored"
                )
                # stopped by the time left for all scripts, not its own limit (#581)
                if is_timeout and cut_by_deadline:
                    etype = "deadline"
                rows.append(
                    _run_row(fn, outc, msg, etype, undef_var, so, se, elapsed, exec_lines, captures)
                )
            else:
                rows.append(
                    _run_row(fn, "ran_ok", "", None, None, so, se, elapsed, exec_lines, captures)
                )
    finally:
        bar.terminate()
    return _run_frame(rows)
