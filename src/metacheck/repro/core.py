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
``PYTACHECK_RSCRIPT``, else from ``PATH``.

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
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import pandas as pd

from metacheck._r.base import as_character, r_sort_key, slashed, trimws
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


def _is_na(x: Any) -> bool:
    if x is None or x is pd.NA:
        return True
    return isinstance(x, float) and x != x


def _is_true(x: Any) -> bool:
    """R ``isTRUE()`` of one logical value."""
    return not _is_na(x) and x is not None and bool(x) is True


def _is_false(x: Any) -> bool:
    """R ``isFALSE()`` of one logical value."""
    return not _is_na(x) and x is not None and bool(x) is False


def _chr(x: Any) -> str | None:
    """One value as R character (``None`` for ``NA``)."""
    if _is_na(x):
        return None
    return x if isinstance(x, str) else as_character(x)


def _chr_list(x: Any) -> list[str | None]:
    """A character vector (str, sequence, Series, ``None``) as a list."""
    if x is None:
        return []
    if isinstance(x, str):
        return [x]
    if isinstance(x, pd.Series | pd.Index):
        return [_chr(v) for v in x.tolist()]
    if isinstance(x, Iterable):
        return [_chr(v) for v in x]
    return [_chr(x)]


def _col(df: pd.DataFrame, name: str) -> list[Any]:
    """A data-frame column as a plain list (``NA`` -> ``None``)."""
    return [None if _is_na(v) else v for v in df[name].tolist()]


def _na_str(x: str | None) -> str:
    """``paste()`` of one character value (``NA`` pastes as ``"NA"``)."""
    return "NA" if x is None else x


def _paste_lines(lines: Sequence[str | None]) -> str:
    """``paste(lines, collapse = "\\n")`` (``NA`` pastes as ``"NA"``)."""
    return "\n".join("NA" if s is None else s for s in lines)


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
    """``Rscript``: ``PYTACHECK_RSCRIPT``, else the first one on ``PATH``."""
    return os.environ.get("PYTACHECK_RSCRIPT") or shutil.which("Rscript")


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


def _code_remove_comments(code_text: Any) -> list[str | None]:
    from metacheck.codecheck.core import code_remove_comments

    return list(code_remove_comments(code_text, "R") or [])


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

    joined = _paste_lines(_chr_list(code_text))
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


def _repro_redirect_writes(code_text: Any) -> pd.DataFrame:
    """Find a script's write calls and redirect their targets into the sandbox.

    Port of ``.repro_redirect_writes()``: one row per resolvable write call
    with ``call_text``, ``replacement`` (the call with its target replaced by
    ``"{{REPRO_OUTPUT}}<basename>"``), ``redirected_name`` and ``line``.
    """
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
    nc = _code_remove_comments(lines)
    joined = _paste_lines(nc)
    starts = gregexpr_all(_WRITE_FN_PAT, joined, perl=True)
    if not starts:
        return result()

    path_like_vars: dict[str, bool] = {}
    var_ext: dict[str, str] = {}
    for a in regexec(_ASSIGN_ANY_PAT, nc, perl=True):
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
        fn_name = regexec(_WRITE_FN_PAT, call_text, perl=True)[1]
        args_text = joined[open_paren : end - 1]
        line = _line_of(joined, start)

        hit: tuple[int, int, str, str] | None = None
        for arg in _split_args(args_text):
            # a named argument keeps its name (R drops it, so save(a, b, file
            # = "ab.RData") became save(a, b, "<out>/ab.RData"), which saves
            # the string as another object; UPSTREAM_ISSUES U134)
            name = regextract(
                r"^[.a-zA-Z][.a-zA-Z0-9_]*\s*=\s*(?!=)", trimws(arg["text"]), perl=True
            )
            val = trimws(
                sub(r"^[.a-zA-Z][.a-zA-Z0-9_]*\s*=\s*(?!=)", "", trimws(arg["text"]), perl=True)
            )
            lead = arg["text"][: len(arg["text"]) - len(arg["text"].lstrip())]
            prefix = "" if name is None else lead + str(name)
            if regextract(r"""^(['"])((?:[^'"\\]|\\.)*)\1$""", val, perl=True) is not None:
                target = val[1:-1]
                if grepl(_EXT_END, target):
                    hit = (arg["start"], arg["end"], _basename(_bs2fs(target)) or "", prefix)
                    break
                continue
            if grepl(r"^[.a-zA-Z][.a-zA-Z0-9_]*$", val) and val in path_like_vars:
                hit = (arg["start"], arg["end"], val + var_ext[val], prefix)
                break
            if grepl(r"^(paste0|paste|sprintf|file\.path)\s*\(", val, perl=True):
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


def _md5(path: str | None) -> str | None:
    if path is None:
        return None
    try:
        return hashlib.md5(Path(path).read_bytes()).hexdigest()  # noqa: S324 - tools::md5sum
    except OSError:
        return None


def _location_lookup(
    structure_df: pd.DataFrame | None,
) -> tuple[dict[str | None, str | None], dict[str | None, str | None]]:
    """``setNames(file_location, file_name)`` and its basename-keyed twin (first match wins)."""
    loc: dict[str | None, str | None] = {}
    base: dict[str | None, str | None] = {}
    if structure_df is None or not all(
        c in structure_df.columns for c in ("file_name", "file_location")
    ):
        return loc, base
    for fn, fl in zip(
        _chr_list(structure_df["file_name"]), _chr_list(structure_df["file_location"]), strict=True
    ):
        loc.setdefault(fn, fl)
        base.setdefault(_tolower(_basename(fn)), fl)
    return loc, base


def _find_source(
    fn: str | None, loc: Mapping[str | None, str | None], base: Mapping[str | None, str | None]
) -> str | None:
    """The existing on-disk source of a plan file (by name, then by basename)."""
    if fn in loc:
        path = loc[fn]
        if path is not None and path != "" and os.path.exists(path):
            return path
    b = _tolower(_basename(fn))
    if b in base:
        path = base[b]
        if path is not None and path != "" and os.path.exists(path):
            return path
    return None


def repro_rewrite_paths(
    code_text: Any,
    file_name: str,
    plan: pd.DataFrame | None,
    lang: str = "R",
    structure_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Rewrite a code file's data-file paths to the Psych-DS layout.

    Port of ``R/reproducibility_check.R::repro_rewrite_paths()``. References
    are matched to *plan* rows (``file_name``, ``target_path`` and, for
    converted tabular files, ``original_target``) by basename; ambiguities
    are resolved by study group and, with *structure_df*, by collapsing
    byte-identical mirrors. Columns ``ref``, ``basename``, ``matched``,
    ``target``, ``ambiguous``, ``n_candidates`` and ``is_call``.
    """
    empty = _rewrite_frame([])
    if lang != "R" or code_text is None:
        return empty
    if (
        plan is None
        or len(plan) == 0
        or not all(c in plan.columns for c in ("file_name", "target_path"))
    ):
        return empty

    from metacheck.codecheck.core import code_file_refs

    refs = list(code_file_refs(code_text, "R"))
    call_refs = _repro_format_call_refs(code_text)
    if len(call_refs) > 0:
        refs = _setdiff(refs, _col(call_refs, "fmt"))
    if len(refs) == 0 and len(call_refs) == 0:
        return empty
    ref_base = [_norm_base(r) or "" for r in refs]
    ref_ext = [_tolower(_file_ext(b)) or "" for b in ref_base]

    plan_file = _chr_list(plan["file_name"])
    plan_target = _chr_list(plan["target_path"])
    n_plan = len(plan)
    plan_base = [_norm_base(f) for f in plan_file]
    has_target = [t is not None and t != "" for t in plan_target]
    orig_target = (
        _chr_list(plan["original_target"]) if "original_target" in plan.columns else [None] * n_plan
    )
    orig_ext = [_tolower(_file_ext(o)) for o in orig_target]

    def pick_target(i: int, ref_e: str) -> str | None:
        o = orig_target[i]
        if o is not None and o != "" and ref_e != "" and ref_e == orig_ext[i]:
            return o
        return plan_target[i]

    script_grp = _data_group_from_path([file_name])[0]

    plan_hash_of: Callable[[int], str | None]
    if structure_df is not None and all(
        c in structure_df.columns for c in ("file_name", "file_location")
    ):
        loc, loc_base = _location_lookup(structure_df)

        # tools::md5sum() of each plan row's source; only ever needed for rows
        # that share a basename, so computed on demand (and once)
        @functools.cache
        def plan_hash_of(i: int) -> str | None:
            return _md5(_find_source(plan_file[i], loc, loc_base))
    else:

        def plan_hash_of(i: int) -> str | None:  # noqa: ARG001 - no structure_df: no hashes
            return None

    def collapse_by_hash(cand: list[int]) -> list[int]:
        if len(cand) < 2:
            return cand
        plan_hash = {i: plan_hash_of(i) for i in cand}
        h = [plan_hash[i] for i in cand]
        with_hash = [c for c, x in zip(cand, h, strict=True) if x is not None]
        if len(with_hash) < 2:
            return cand
        keep = [c for c, x in zip(cand, h, strict=True) if x is None]
        for grp_h in _unique(x for x in h if x is not None):
            grp_cand = [c for c in with_hash if plan_hash[c] == grp_h]
            if len(grp_cand) == 1:
                keep.append(grp_cand[0])
                continue
            grp_of = _data_group_from_path([plan_target[c] for c in grp_cand])
            # the group most mirrors agree on; a tie keeps the first
            # occurrence, as metacheck's comment says (its which.max(table())
            # picks the alphabetically first group; UPSTREAM_ISSUES U135)
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

    def resolve(
        ref: str, key_base: str, key_ext: str, key_path: str, cand: list[int], is_call: bool
    ) -> tuple[Any, ...]:
        n = len(cand)
        if n == 0:
            return (ref, key_base, False, None, False, 0, is_call)
        if n == 1:
            return (ref, key_base, True, pick_target(cand[0], key_ext), False, 1, is_call)
        cand_targets = _unique(pick_target(c, key_ext) for c in cand)
        if len(cand_targets) == 1:
            return (ref, key_base, True, cand_targets[0], False, n, is_call)
        cand_grp = _data_group_from_path([plan_target[c] for c in cand])
        ref_grp = _data_group_from_path([key_path])[0]
        pick: list[int] = []
        if script_grp is not None:
            pick = [c for c, g in zip(cand, cand_grp, strict=True) if g == script_grp]
        if len(pick) != 1 and ref_grp is not None:
            pick = [c for c, g in zip(cand, cand_grp, strict=True) if g == ref_grp]
        if len(pick) == 1:
            return (ref, key_base, True, pick_target(pick[0], key_ext), False, n, is_call)
        return (ref, key_base, True, None, True, n, is_call)

    # which(plan_base == <basename> & has_target), indexed once
    plan_index: dict[str, list[int]] = {}
    for j in range(n_plan):
        if has_target[j] and plan_base[j] is not None:
            plan_index.setdefault(cast(str, plan_base[j]), []).append(j)

    rows: list[tuple[Any, ...]] = []
    for i, r in enumerate(refs):
        cand = list(plan_index.get(ref_base[i], []))
        cand = collapse_by_hash(cand)
        rows.append(resolve(r, ref_base[i], ref_ext[i], r, cand, False))

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
        rows.append(resolve(call_text, key_base, key_ext, key, cand, True))

    return _rewrite_frame(rows)


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
            if _is_na(texts[i])
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
    for i in range(n):
        for s in sources[i]:
            s = _tolower(s)
            js = _setdiff(by_base.get(s, []) if s is not None else [], [i])
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
    return out


def _code_text_items(code_text_list: Any) -> tuple[list[str], list[Any]]:
    """Names and values of a named list of per-file code texts."""
    if isinstance(code_text_list, Mapping):
        return [str(k) for k in code_text_list], list(code_text_list.values())
    items = list(code_text_list)
    return [str(i) for i in range(1, len(items) + 1)], items


_IO_WRITE_PAT = r"\b(write[._][A-Za-z._0-9]*|saveRDS|save|save\.image|ggsave|export|fwrite)\s*\("
_SRC_PAT = r"""source\s*\(\s*['"]([^'"]+)['"]"""


def repro_file_io(code_text_list: Any) -> pd.DataFrame:
    """Extract the files each script reads, writes, and sources.

    Port of ``R/reproducibility_check.R::repro_file_io()``. *code_text_list*
    maps file names to code text. Returns ``file_name`` and list columns
    ``reads``, ``writes`` and ``sources`` (lowercased basenames).
    """
    if code_text_list is None or len(code_text_list) == 0:
        return _frame({"file_name": ("string", [])})

    from metacheck.codecheck.core import code_file_refs

    fnames, texts = _code_text_items(code_text_list)
    out_reads: list[list[str]] = []
    out_writes: list[list[str]] = []
    out_srcs: list[list[str]] = []
    for ct in texts:
        nc = _code_remove_comments(ct)
        all_refs = list(code_file_refs(cast(list[str], nc), "R", include_writes=True))
        joined = _paste_lines(nc)
        ref_base = [_norm_base(r) for r in all_refs]
        # grepl(write_fns, grep(ref, nc, fixed = TRUE, value = TRUE)): every
        # line is classified once, then looked up per reference
        lines = [s for s in nc if s is not None]
        line_w = list(grepl(_IO_WRITE_PAT, lines, perl=True, ignore_case=True))
        is_write: list[bool] = []
        is_read: list[bool] = []
        for ref in all_refs:
            w = [x for s, x in zip(lines, line_w, strict=True) if ref is not None and ref in s]
            is_write.append(any(w))
            is_read.append(any(not x for x in w))
        reads = [b for b, r in zip(ref_base, is_read, strict=True) if r]
        writes = [b for b, w in zip(ref_base, is_write, strict=True) if w]
        srcs_raw = list(
            sub(_SRC_PAT, r"\1", regextract_all(_SRC_PAT, joined, perl=True), perl=True)
        )
        srcs = [_norm_base(s) for s in srcs_raw]
        reads = _setdiff(reads, srcs)
        out_reads.append(_unique(reads))
        out_writes.append(_unique(writes))
        out_srcs.append(_unique(srcs))
    return _frame(
        {
            "file_name": ("string", fnames),
            "reads": ("object", out_reads),
            "writes": ("object", out_writes),
            "sources": ("object", out_srcs),
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
    """A curated safe-list of very common packages (``.repro_common_pkgs()``)."""
    return [
        "testthat",
        "magrittr",
        "dplyr",
        "tidyr",
        "ggplot2",
        "purrr",
        "tibble",
        "stringr",
        "readr",
        "forcats",
    ]


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


def _as_numeric(x: Any) -> float | None:
    """``as.numeric()`` of one value (``None`` for NA / unparseable)."""
    if _is_na(x):
        return None
    if isinstance(x, bool):
        return float(x)
    if isinstance(x, int | float):
        return float(x)
    s = str(x).strip()
    try:
        if len(s) > 2 and s.lstrip("+-")[:2].lower() == "0x":
            return float(int(s, 16))
        return float(s)
    except ValueError:
        return None


def repro_missing_inputs(
    refs: Any,
    plan: pd.DataFrame | None,
    structure_df: pd.DataFrame | None,
    skipped: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Diagnose why a referenced input file is unavailable.

    Port of ``R/reproducibility_check.R::repro_missing_inputs()``. Each
    referenced basename is ``withheld_size`` (in *skipped*, the
    download-skip records), ``in_repo_not_downloaded`` (listed in
    *structure_df* without a local copy) or ``absent`` (with a similarly
    named repository file in ``similar_to`` when one exists); references
    that are present and downloaded are dropped. Columns ``basename``,
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

    def find_similar(b: str | None) -> str | None:
        if len(all_candidates) == 0 or b is None:
            return None
        stem = _file_path_sans_ext(b)
        # all_candidates[cand_stems == stem & nzchar(stem)][[1]]: an NA candidate
        # gives an NA index, which selects an NA element
        if stem != "":
            for c in all_candidates:
                if c is None or _file_path_sans_ext(c) == stem:
                    return c
        max_dist = min(3, len(b) // 4)
        if max_dist < 1:
            return None
        best: tuple[int, str] | None = None
        for c in all_candidates:
            if c is None:
                continue
            d = _adist(b, c)
            if d <= max_dist and (best is None or d < best[0]):
                best = (d, c)
        return None if best is None else best[1]

    for b in ref_list:
        if b in skip_base:
            # without a file_size column the size is left out (R stops:
            # "argument is of length zero", UPSTREAM_ISSUES U135)
            has_size = skipped is not None and "file_size" in skipped.columns
            # which(skip_base == b)[1] is NA for an NA basename
            sz = (
                None
                if b is None or not has_size
                else _as_numeric(skipped["file_size"].iloc[skip_base.index(b)])  # type: ignore[index]
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
        if b in struct_base:
            # which(struct_base == b)[1] is NA for an NA basename: location NA
            loc = (
                _chr(structure_df["file_location"].iloc[struct_base.index(b)])  # type: ignore[index]
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
        detail = (
            "referenced file is not present in the repository, but a similarly-named "
            f"file exists: {similar}"
            if similar is not None
            else "referenced file is not present in the repository"
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


def repro_materialize_layout(
    plan: pd.DataFrame | None, structure_df: pd.DataFrame | None, root: str | os.PathLike[str]
) -> MaterialisedRoot:
    """Materialise the Psych-DS layout for a paper into a throwaway directory.

    Port of ``R/reproducibility_check.R::repro_materialize_layout()``: every
    planned file with a target and an available source is copied to
    ``<root>/<target_path>`` (and a converted file's original to
    ``<root>/<original_target>``); ``<root>/output`` is always created.
    Returns *root* with the copy log in ``attrs["materialised"]``.
    """
    root_s = os.fspath(root)
    _dir_create(root_s)
    _dir_create(_file_path(root_s, "output"))
    rows: list[tuple[str | None, str | None, bool]] = []

    def result() -> MaterialisedRoot:
        return MaterialisedRoot(
            root_s,
            _frame(
                {
                    "target_path": ("string", [r[0] for r in rows]),
                    "source": ("string", [r[1] for r in rows]),
                    "ok": ("boolean", [r[2] for r in rows]),
                }
            ),
        )

    if (
        plan is None
        or len(plan) == 0
        or not all(c in plan.columns for c in ("file_name", "target_path"))
    ):
        return result()

    loc, loc_base = _location_lookup(structure_df)
    plan_file = _chr_list(plan["file_name"])

    def copy_rows(targets: list[str | None]) -> None:
        for i, tgt in enumerate(targets):
            if tgt is None or tgt == "":
                continue
            src = _find_source(plan_file[i], loc, loc_base)
            if src is None:
                rows.append((tgt, None, False))
                continue
            dest = _file_path(root_s, tgt)
            _dir_create(os.path.dirname(dest))
            rows.append((tgt, src, _copy_file(src, dest)))

    copy_rows(_chr_list(plan["target_path"]))
    if "original_target" in plan.columns:
        copy_rows(_chr_list(plan["original_target"]))
    return result()


_SETWD_LINE = r"^(\s*)(setwd\s*\(.*)$"
_SETWD_ARG = r"""setwd\s*\(\s*['"]([^'"]+)['"]"""
_FAMILY_PAT = r"""\b(base_family|family)\s*=\s*(['"])[^'"]*\2"""


def repro_write_scripts(
    code_text_list: Mapping[str, Any],
    rewrite_list: Mapping[str, pd.DataFrame | None] | None,
    plan: pd.DataFrame | None,
    root: str | os.PathLike[str],
    inject_libs: Mapping[str, str] | None = None,
) -> pd.DataFrame:
    """Write path-rewritten scripts into the materialised layout.

    Port of ``R/reproducibility_check.R::repro_write_scripts()``. Each
    script gets its resolved path rewrites, its write calls redirected into
    ``<root>/output/``, its ``setwd()`` lines commented out, named font
    families replaced with ``"sans"`` and (with *inject_libs*) a
    ``library()`` call injected, and is written to ``<root>/<target>``.
    Returns ``file_name``, ``script_path``, ``run_dir``, ``setwd_removed``,
    ``setwd_paths``, ``family_replaced``, ``family_detail`` and
    ``library_injected``.
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

    def script_target(fn: str) -> str:
        if plan_base:
            b = _tolower(_basename(fn))
            for pb_, t in zip(plan_base, plan_target, strict=True):
                if pb_ == b and t is not None and t != "":
                    return t
        return _basename(fn) or ""

    output_dir = str(_bs2fs(_file_path(root_s, "output"))) + "/"
    rows: list[tuple[Any, ...]] = []
    for fn in fnames:
        txt = _chr_list(code_text_list[fn])
        rw = rewrite_list.get(fn) if isinstance(rewrite_list, Mapping) else None
        if rw is not None and len(rw) > 0:
            matched = _col(rw, "matched")
            ambiguous = _col(rw, "ambiguous")
            target = _col(rw, "target")
            refs = _col(rw, "ref")
            is_call = _col(rw, "is_call") if "is_call" in rw.columns else [False] * len(rw)
            for k in range(len(rw)):
                # which(matched & !ambiguous & !is.na(target) & nzchar(target))
                good = (
                    _is_true(matched[k])
                    and _is_false(ambiguous[k])
                    and target[k] is not None
                    and target[k] != ""
                )
                if not good:
                    continue
                repl = f'"{target[k]}"' if _is_true(is_call[k]) else target[k]
                txt = list(gsub(refs[k], repl, txt, fixed=True))

        wr = _repro_redirect_writes(txt)
        if len(wr) > 0:
            joined_txt = _paste_lines(txt)
            for call_text, replacement in zip(
                _col(wr, "call_text"), _col(wr, "replacement"), strict=True
            ):
                repl = gsub("{{REPRO_OUTPUT}}", output_dir, replacement, fixed=True)
                joined_txt = sub(call_text, repl, joined_txt, fixed=True)
            txt = list(strsplit(joined_txt, "\n", fixed=True))

        is_setwd = list(grepl(_SETWD_LINE, txt, perl=True))
        setwd_n = sum(is_setwd)
        setwd_paths: list[str] = []
        if setwd_n > 0:
            hit_lines = [s for s, h in zip(txt, is_setwd, strict=True) if h]
            m = [x for x in regextract(_SETWD_ARG, hit_lines, perl=True) if x is not None]
            setwd_paths = list(
                sub(".*" + _SETWD_ARG + ".*", r"\1", [x for x in m if x != ""], perl=True)
            )
            commented = sub(
                _SETWD_LINE, r"\1# [reproducibility_check removed setwd] \2", hit_lines, perl=True
            )
            it = iter(commented)
            txt = [next(it) if h else s for s, h in zip(txt, is_setwd, strict=True)]

        joined_for_family = _paste_lines(txt)
        family_matches = regextract_all(_FAMILY_PAT, joined_for_family, perl=True)
        family_n = len(family_matches)
        if family_n > 0:
            joined_for_family = gsub(_FAMILY_PAT, r'\1 = "sans"', joined_for_family, perl=True)
            txt = list(strsplit(joined_for_family, "\n", fixed=True))

        injected: str | None = None
        if inject_libs is not None and fn in inject_libs:
            injected = _chr(inject_libs[fn])
        if injected is not None and injected != "":
            txt = [f"library({injected})  # [reproducibility_check injected]", *txt]

        tgt = script_target(fn)
        dest = _file_path(root_s, tgt)
        _dir_create(os.path.dirname(dest))
        _write_lines(txt, dest)
        rows.append(
            (
                fn,
                dest,
                root_s,
                setwd_n,
                ", ".join(setwd_paths),
                family_n,
                "; ".join(f'{x} -> "sans"' for x in family_matches),
                injected,
            )
        )
    return _frame(
        {
            "file_name": ("string", [r[0] for r in rows]),
            "script_path": ("string", [r[1] for r in rows]),
            "run_dir": ("string", [r[2] for r in rows]),
            "setwd_removed": ("Int64", [r[3] for r in rows]),
            "setwd_paths": ("string", [r[4] for r in rows]),
            "family_replaced": ("Int64", [r[5] for r in rows]),
            "family_detail": ("string", [r[6] for r in rows]),
            "library_injected": ("string", [r[7] for r in rows]),
        }
    )


# ---------------------------------------------------------------------------
# installing dependencies
# ---------------------------------------------------------------------------


def _install_frame(rows: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    """``dplyr::bind_rows()`` of per-package result rows."""
    if not rows:
        return _frame(
            {
                "package": ("string", []),
                "source": ("string", []),
                "installed": ("boolean", []),
                "message": ("string", []),
                "via_archive": ("boolean", []),
                "category": ("string", []),
            }
        )
    dtypes = {
        "package": "string",
        "source": "string",
        "installed": "boolean",
        "message": "string",
        "via_archive": "boolean",
        "category": "string",
    }
    parts = [
        _frame({k: (dtypes[k], [v]) for k, v in row.items()})  # one-row frames, R's data.frame()
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
old_lib <- .libPaths()
dir.create(lib_dir, recursive = TRUE, showWarnings = FALSE)
.libPaths(c(lib_dir, old_lib))
repos <- getOption("repos")
if (is.null(repos) || !length(repos) || any(!nzchar(repos)) || any(repos == "@CRAN@"))
  options(repos = c(CRAN = "https://cloud.r-project.org"))
emit <- function(ok, msg, skipped = FALSE, extra = "")
  cat("\n<<repro-result>>{\"ok\":", if (isTRUE(ok)) "true" else "false",
      ",\"skipped\":", if (isTRUE(skipped)) "true" else "false",
      ",\"msg\":", .mc_json_str(msg), extra, "}\n", sep = "")
if (mode == "check_main") {
  emit(TRUE, "", skipped = length(find.package(pkg, lib.loc = old_lib, quiet = TRUE)) > 0,
       extra = paste0(",\"install_lib\":", .mc_json_str(old_lib[1])))
} else if (mode == "install") {
  cran_main <- identical(src, "cran") && isTRUE(cran_to_main_lib)
  {
    gh_avail <- requireNamespace("remotes", quietly = TRUE)
    install_lib <- if (cran_main) old_lib[1] else lib_dir
    res <- tryCatch({
      if (identical(src, "github")) {
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
      if (!requireNamespace(pkg, quietly = TRUE, lib.loc = lib_dir) &&
          !requireNamespace(pkg, quietly = TRUE))
        stop("installed but package '", pkg, "' is not loadable")
      list(ok = TRUE, msg = "")
    }, error = function(e) list(ok = FALSE, msg = conditionMessage(e)))
    emit(res$ok, res$msg, extra = paste0(",\"install_lib\":", .mc_json_str(install_lib)))
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

    Returns the step's result (``ok``, ``msg``, ``skipped``, ``install_lib``);
    a missing ``Rscript``, a timeout or a crash is a failed step.
    """
    header = [f"mode <- {_r_string(mode)}", f"lib_dir <- {_r_string(lib_dir)}"]
    for key, val in values.items():
        if isinstance(val, bool):
            header.append(f"{key} <- {'TRUE' if val else 'FALSE'}")
        else:
            header.append(f"{key} <- {_r_string(_chr(val))}")
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


def repro_install_deps(
    install_deps: pd.DataFrame | None,
    lib_dir: str | os.PathLike[str],
    cran_to_main_lib: bool = False,
) -> pd.DataFrame:
    """Install a paper's declared dependencies into a throwaway library.

    Port of ``R/reproducibility_check.R::repro_install_deps()``. Each
    non-base dependency (``package``, ``source``, ``ref``) is installed with
    R (``install.packages()``, ``BiocManager::install()``,
    ``remotes::install_github()`` or ``install.packages(url)``) into
    *lib_dir*, or -- CRAN packages with *cran_to_main_lib* -- into R's
    default library (skipped when already installed there). A CRAN package
    missing from live CRAN is retried once from the CRAN Archive. Returns
    ``package``, ``source``, ``installed``, ``message``, ``via_archive`` and
    ``category``.
    """
    if install_deps is None or len(install_deps) == 0:
        return _install_frame([])
    lib = os.fspath(lib_dir)
    _dir_create(lib)
    pkgs = _chr_list(install_deps["package"])
    srcs = _chr_list(install_deps["source"])
    refs = _chr_list(install_deps["ref"]) if "ref" in install_deps.columns else [None] * len(pkgs)
    rows: list[dict[str, Any]] = []
    for pkg, src, ref in zip(pkgs, srcs, refs, strict=True):
        cran_main = src == "cran" and _r_is_true(cran_to_main_lib)
        main_lib: str | None = None
        if cran_main:
            # a CRAN package already in the REAL library (not the throwaway one)
            chk = _install_r("check_main", lib, pkg=pkg)
            main_lib = chk.get("install_lib")
            if chk.get("skipped"):
                _message("[repro]     '", pkg, "' already installed (main library); skipping.")
                # with a category (NA) like every other row, so the column is
                # there even when every package is already installed (R's
                # early row has none; UPSTREAM_ISSUES U135)
                rows.append(
                    {
                        "package": pkg,
                        "source": src,
                        "installed": True,
                        "message": "",
                        "via_archive": False,
                        "category": None,
                    }
                )
                continue
        _message(
            "[repro]     installing '",
            pkg,
            "' (source: ",
            src,
            ", into main library" if cran_main else ", into throwaway library",
            ") ...",
        )
        res = _install_r(
            "install", lib, pkg=pkg, src=src, ref=ref, cran_to_main_lib=_r_is_true(cran_to_main_lib)
        )
        ok = bool(res.get("ok"))
        msg = str(res.get("msg") or "")
        via_archive = False
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
            else:
                _message(
                    "[repro]     '",
                    pkg,
                    "' could not be installed from the CRAN Archive either: ",
                    res2["msg"],
                )
                msg = f"{msg} (CRAN Archive retry also failed: {res2['msg']})"
        rows.append(
            {
                "package": pkg,
                "source": src,
                "installed": ok,
                "message": msg,
                "via_archive": via_archive,
                "category": None if ok else _repro_classify_install_message(msg),
            }
        )
    return _install_frame(rows)


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
    if msg is None or _is_na(msg) or msg == "":
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


def _read_url_lines(url: str) -> list[str] | None:
    """``readLines(url)`` (``None`` when the URL cannot be read)."""
    from metacheck import http

    try:
        resp = http.request("GET", url, max_tries=1)
    except Exception:
        return None
    if resp is None or resp.status_code >= 400:
        return None
    text = resp.text
    if text == "":
        return []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


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
    listing = _read_url_lines(archive_url)
    if listing is None:
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
        return {"ok": True, "msg": "", "version": version}
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


def repro_run_scripts(
    run_tbl: pd.DataFrame | None,
    order: Any,
    lib_dir: str | os.PathLike[str] | None = None,
    timeout: float = 600,
    skip: Sequence[str] | str = (),
    parses: Mapping[str, Any] | pd.Series | None = None,
    failed_deps: Sequence[str] | str = (),
) -> pd.DataFrame:
    """Run the paper's scripts, in order, in isolated R subprocesses.

    Port of ``R/reproducibility_check.R::repro_run_scripts()``. Each script
    of *run_tbl* (``file_name``, ``script_path``, ``run_dir``) runs in a
    fresh ``Rscript`` process from its ``run_dir`` with *lib_dir* first on
    the library path and a per-script *timeout*; its statements are echoed
    and statistical result objects captured (``R/r-capture.R``). Scripts in
    *skip* or failing to parse (*parses*) are not run. Returns
    ``file_name``, ``outcome`` (``ran_ok``, ``errored``, ``timed_out``,
    ``not_parsed``, ``skipped_missing_inputs``, ``dependency_unavailable``),
    ``error``, ``error_type``, ``undefined_var``, ``stdout``, ``stderr``,
    ``elapsed``, ``script_lines`` and ``captures``.
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
            exec_lines = _script_lines(paths[i])
            _message("[repro]   -> running '", fn, "' (timeout ", as_character(timeout), "s) ...")
            with tempfile.TemporaryDirectory(prefix="pytacheck_run_") as tmp:
                out_file = os.path.join(tmp, "out.txt")
                err_file = os.path.join(tmp, "err.txt")
                cap_file = os.path.join(tmp, "captures.json")
                t0 = time.monotonic()
                error: _RunError | None = None
                try:
                    _callr_run(paths[i], dirs[i], cap_file, lib, timeout, out_file, err_file)
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
