"""Code-check helpers: reading, parsing and scanning analysis code.

Port of ``R/code_check.R``: reading code files (:func:`code_read`, with
readr/vroom's encoding guess reproduced byte for byte), language detection
(:func:`code_lang`, including Jupyter and Quarto kernels), code extraction
from notebooks and literate documents (:func:`code_extract_r`,
:func:`code_extract_py`, :func:`code_extract_qmd_py`), R parse checking
(:func:`code_parse_r`), comment handling and line statistics, package,
file-reference, ``setwd()``, ``install.packages()`` and absolute-path
detection, environment pinning (:func:`_code_version_pin_check`) and the
repository-listing expansions that recover checkable code from SPSS/Stata/
Mplus/HTML output and zip archives (``_code_expand_*``).

Functions from other areas (repository downloads, output-format syntax
recovery, zip peeking) are imported lazily where they are used.
"""

from __future__ import annotations

import math
import os
import re
import urllib.parse
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import pandas as pd

from metacheck._json import loads as _json_loads
from metacheck._r.base import r_sorted, trimws
from metacheck._r.regex import (
    compile_r,
    gregexpr_all,
    grepl,
    gsub,
    regexec,
    regextract,
    regextract_all,
    strsplit,
    sub,
)
from metacheck._values import field, is_missing
from metacheck.codecheck._rcoerce import r_as_character, r_unlist_chr

__all__ = [
    "code_abs_path",
    "code_extract_py",
    "code_extract_qmd_py",
    "code_extract_r",
    "code_file_refs",
    "code_install_packages",
    "code_lang",
    "code_library_lines",
    "code_library_names",
    "code_line_stats",
    "code_packages",
    "code_parse_r",
    "code_read",
    "code_remove_comments",
    "code_setwd",
]

LANGS = ("R", "Python", "SPSS", "SAS", "Stata", "Mplus", "MATLAB")

# ---------------------------------------------------------------------------
# small R idioms
# ---------------------------------------------------------------------------


def _match_arg(arg: Any, choices: Sequence[str] = LANGS) -> str:
    """``match.arg(arg)`` against the default *choices* (partial matching)."""
    if arg is None:
        return choices[0]
    if not isinstance(arg, str):
        values = list(arg)
        if values == list(choices):
            return choices[0]
        if len(values) != 1:
            raise ValueError("'arg' must be of length 1")
        arg = values[0]
    if arg in choices:
        return str(arg)
    hits = [c for c in choices if c.startswith(arg)] if arg else []
    if len(hits) == 1:
        return hits[0]
    raise ValueError("'arg' should be one of " + ", ".join(f"“{c}”" for c in choices))


def _as_chr(x: Any) -> list[str | None] | None:
    """A character vector argument as a list (``None`` for R's ``NULL``)."""
    if x is None:
        return None
    if isinstance(x, str):
        return [x]
    if isinstance(x, pd.Series | pd.Index):
        return [None if pd.isna(v) else str(v) for v in x.tolist()]
    if isinstance(x, Iterable):
        out: list[str | None] = []
        for v in x:
            if v is None or (isinstance(v, float) and math.isnan(v)) or v is pd.NA:
                out.append(None)
            elif isinstance(v, str):
                out.append(v)
            else:
                raise TypeError("non-character argument")
        return out
    raise TypeError("non-character argument")


def _split_lines(code_text: Any) -> list[str | None] | None:
    """``strsplit(code_text, "\\n+") |> unlist()``; ``None`` is R's ``NULL``.

    ``strsplit()`` of ``NULL`` (or a non-character) is an error, of
    ``character(0)`` ``list()`` (``unlist()`` gives ``NULL``), of ``""``
    ``character(0)``.
    """
    x = _as_chr(code_text)
    if x is None:
        raise TypeError("non-character argument")
    if not x:
        return None
    out: list[str | None] = []
    for piece in strsplit(x, "\n+"):
        out.extend(piece)
    return out


def _nzchar(x: Any) -> bool:
    """``nzchar()``: ``NA`` counts as non-empty."""
    return x is None or (isinstance(x, float) and math.isnan(x)) or str(x) != ""


def _json_load(text: str) -> Any:
    """Parsed JSON (objects as dicts); ``None`` when *text* is not valid JSON."""
    try:
        return _json_loads(text)
    except ValueError:
        return None


def _r_as_character1(x: Any) -> str | None:
    """``as.character(x)[[1]]`` for a parsed JSON/YAML value (``NULL`` gives ``None``)."""
    if x is None:
        return None
    values = r_as_character(x)
    if not values:
        raise IndexError("subscript out of bounds")
    return values[0]


# ---------------------------------------------------------------------------
# code_read()
# ---------------------------------------------------------------------------

_URL = re.compile(r"^((http|ftp)s?|sftp)://")


def _fetch_url(url: str) -> bytes:
    if url.startswith("file://"):
        # libcurl reads a file: URL (file:///folder/file.R) from the disk
        local = Path(urllib.parse.unquote(url[len("file://") :]))
        try:
            return local.read_bytes()
        except OSError:
            raise FileNotFoundError(
                f"Couldn't open file {local}:\nFailed reading the given file"
            ) from None
    from metacheck import http

    resp = http.request("GET", url)
    if resp is None or resp.status_code >= 400:
        status = "no response" if resp is None else f"HTTP error {resp.status_code}"
        raise OSError(f"cannot open URL '{url}': {status}")
    return bytes(resp.content)


class _CodeLines(list):  # type: ignore[type-arg]
    """The lines of a file that is not text: no lines, and ``binary`` says what it is.

    R marks the empty result with ``attr(, "binary")``.
    """

    def __init__(self, binary: str) -> None:
        super().__init__()
        self.binary = binary


def _binary_kind_raw(b: bytes) -> str | None:
    """What bytes are when they are not text, from the first ones.

    Port of ``R/code_check.R::.binary_kind_raw()``: saved R data compressed with gzip, bzip2 or xz,
    or zero bytes, which text does not have; ``None`` for text, UTF-16 included (a byte order
    mark, or zero bytes in every other place). Authors save data under names ending in ``.R``; a
    109 MB one was read as code for more than 35 minutes (metacheck #568).
    """
    b = b[:4096]
    if not b:
        return None
    if b[:2] == b"\x1f\x8b":
        return "compressed with gzip (as R saves data)"
    # bzip2: "BZh", the block size 1-9, then the block's own mark, so a script starting with
    # "BZh" is not taken for one
    if (
        len(b) >= 10
        and b[:3] == b"BZh"
        and b[3:4] in tuple(bytes([c]) for c in b"123456789")
        and b[4:10] == bytes([0x31, 0x41, 0x59, 0x26, 0x53, 0x59])
    ):
        return "compressed with bzip2"
    if b[:5] == bytes([0xFD, 0x37, 0x7A, 0x58, 0x5A]):
        return "compressed with xz"
    if 0 not in b:
        return None
    # UTF-16 or UTF-32 text: a byte order mark, or the zero bytes in every other place
    if b[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return None
    n = len(b) - len(b) % 2
    if n >= 4:
        odd = [b[i] == 0 for i in range(0, n, 2)]
        even = [b[i] == 0 for i in range(1, n, 2)]
        m_odd, m_even = sum(odd) / len(odd), sum(even) / len(even)
        if (m_odd > 0.9 and m_even < 0.1) or (m_even > 0.9 and m_odd < 0.1):
            return None
    # R data saved without compression has zero bytes, as other binary files do
    return "binary"


def _curl_error_for(path: str) -> str:
    """What R's ``curl::curl_fetch_memory()`` says about a path that is not a file.

    ``code_read()`` treats a path that does not exist as a URL (libcurl's wording, for a path
    without a scheme): no host for one that starts with a slash, else the first part as a host name
    that cannot be resolved.
    """
    if path.startswith("/"):
        return "URL using bad/illegal format or missing URL:\nURL rejected: No host present"
    host = re.split(r"[/:?#]", path, maxsplit=1)[0]
    return f"Could not resolve hostname [{host}]:\nCould not resolve host: {host}"


def code_read(file_path: str | os.PathLike[str]) -> list[str]:
    """Read code from a file or URL, one element per line.

    Port of ``R/code_check.R::code_read()``. The bytes are decoded by
    :func:`metacheck.codecheck._decode.decode_lines` (a byte order mark, UTF-8,
    else chardet's guess or Windows-1252; D74), not by R's readr/vroom
    pipeline; bytes that fit no encoding are shown as ``<xx>``. A 0-byte file
    gives an empty list, and so does a file that is not text (compressed R
    data, zero bytes; see :func:`_binary_kind_raw`), whose result has a
    ``binary`` attribute saying what it is.
    """
    from metacheck.codecheck._decode import decode_lines

    if file_path is None:
        raise TypeError("argument is of length zero")
    if not isinstance(file_path, str | os.PathLike):
        values = list(file_path)
        if len(values) != 1:
            raise ValueError(f"'length = {len(values)}' in coercion to 'logical(1)'")
        file_path = values[0]
    path = os.fspath(file_path)
    if _URL.search(path) or path.startswith("file://"):
        raw = _fetch_url(path)
    else:
        local = Path(path).expanduser()
        if not local.exists():
            raise FileNotFoundError(_curl_error_for(path))
        if local.is_file() and local.stat().st_size == 0:
            return []
        if local.is_dir():
            raise IsADirectoryError(f"Cannot read file '{path}': it is a directory")
        raw = local.read_bytes()
    if not raw:
        return []
    # not text, such as R data saved under a name ending in .R: no lines, marked, instead of
    # decoding and scanning megabytes of it as code (metacheck #568)
    binary = _binary_kind_raw(raw)
    if binary is not None:
        return _CodeLines(binary)
    return decode_lines(raw)


def _try_code_read(file_name: str) -> list[str] | None:
    try:
        return code_read(file_name)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# code_lang()
# ---------------------------------------------------------------------------


def _file_ext(x: str) -> str:
    """``tools::file_ext()``."""
    m = re.search(r"\.([^\W_]+)\Z", x)  # TRE: [[:alnum:]], "$" only at the very end
    return m.group(1) if m else ""


def _kernel_language(lang: str | None) -> str | None:
    """metacheck's label for a Jupyter kernel's (or Quarto engine's) declared language.

    Port of ``R/code_check.R::.kernel_language()``: ``"R"`` (``r``, or ``ir``, the IRkernel's
    name), ``"Python"``, ``"Julia"``, ``"MATLAB"``, ``"SAS"``, ``"Stata"``, otherwise the
    language's own name in title case; ``None`` for an empty value.
    """
    lang = trimws(lang or "").lower()
    if not lang:
        return None
    if lang in ("r", "ir"):
        return "R"
    if re.match(r"(i?python|py)", lang):
        return "Python"
    if lang.startswith("julia"):
        return "Julia"
    if lang in ("matlab", "octave"):
        return "MATLAB"
    if lang == "sas":
        return "SAS"
    if lang.startswith("stata"):
        return "Stata"
    return lang[:1].upper() + lang[1:]  # tools::toTitleCase(), for a one-word name


def _ipynb_lang(file_name: Any) -> str | None:
    """The kernel language of a Jupyter notebook: ``"R"``, ``"Python"``, another language, or ``None``.

    Port of ``R/code_check.R::.ipynb_lang()``. A notebook that is not on disk is ``"Python"``
    provisionally (``code_check()`` first calls this on bare file names, before downloading, and a
    notebook must count as code to be downloaded at all); one that is on disk gets the language it
    declares, never ``"Python"`` by default, since a Julia notebook labelled Python would be
    syntax-checked and run as Python (metacheck #480). ``None`` when it declares no language or
    cannot be read.
    """
    if not isinstance(file_name, str) or not os.path.exists(file_name):
        return "Python"
    txt = _try_code_read(file_name)
    if not txt:
        return None
    nb = _json_load("\n".join(txt))
    if nb is None:
        return None
    # a notebook that is not a JSON object (or whose metadata is malformed) gives no language
    # instead of stopping code_check ("$ operator is invalid", UPSTREAM_ISSUES U68)
    meta = field(nb, "metadata")
    lang = field(meta, "kernelspec", "language")
    if lang is None:
        lang = field(meta, "language_info", "name")
    if lang is None:
        lang = field(meta, "kernelspec", "name")
    return _kernel_language(_first_chr(lang) or "")


def _first_chr(x: Any) -> str | None:
    """``as.character(x)[[1]]``; ``None`` when *x* has no (text) value."""
    if x is None or isinstance(x, dict):
        return None
    try:
        return _r_as_character1(x)
    except (IndexError, TypeError, ValueError):
        return None


def _yaml_loader() -> Any:
    """A PyYAML loader resolving scalars the way R's ``yaml`` package does."""
    import yaml

    class Loader(yaml.SafeLoader):
        pass

    # R's yaml keeps timestamps as strings and reads y/n as booleans
    Loader.yaml_implicit_resolvers = {
        k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:timestamp"]
        for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }
    Loader.add_implicit_resolver(
        "tag:yaml.org,2002:bool", re.compile(r"^(?:y|Y|n|N)$"), list("yYnN")
    )

    def construct_bool(loader: Any, node: Any) -> bool:
        return str(loader.construct_scalar(node)).lower() in ("y", "yes", "true", "on")

    def construct_mapping(loader: Any, node: Any, deep: bool = False) -> dict[Any, Any]:
        # R's yaml fails on a repeated key ("Duplicate map key: 'x'")
        loader.flatten_mapping(node)
        out: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in out:
                raise yaml.constructor.ConstructorError(None, None, f"Duplicate map key: '{key}'")
            out[key] = loader.construct_object(value_node, deep=deep)
        return out

    def construct_string(loader: Any, node: Any) -> str:
        return str(loader.construct_scalar(node))

    def construct_tagged(loader: Any, suffix: str, node: Any) -> Any:
        # R's yaml ignores tags it does not know: a scalar is kept as its
        # text (no implicit typing), a sequence becomes a list (never an
        # atomic vector) and a mapping a named list; !expr (not evaluated
        # without eval.expr = TRUE) is only allowed on a scalar
        if isinstance(node, yaml.ScalarNode):
            return str(loader.construct_scalar(node))
        if suffix == "expr":
            raise yaml.constructor.ConstructorError(None, None, "Invalid tag: expr")
        if isinstance(node, yaml.SequenceNode):
            return _RTaggedSeq(loader.construct_object(v, deep=True) for v in node.value)
        return construct_mapping(loader, node, deep=True)

    Loader.add_constructor("tag:yaml.org,2002:bool", construct_bool)
    Loader.add_constructor("tag:yaml.org,2002:map", construct_mapping)
    Loader.add_constructor("tag:yaml.org,2002:binary", construct_string)
    Loader.add_constructor("tag:yaml.org,2002:timestamp", construct_string)
    Loader.add_multi_constructor("!", construct_tagged)
    Loader.add_multi_constructor("tag:", construct_tagged)
    return Loader


class _RTaggedSeq(list):  # type: ignore[type-arg]
    """A tagged YAML sequence: R's yaml keeps it a list (not an atomic vector)."""


def _r_yaml(x: Any) -> Any:
    """PyYAML output as R's ``yaml.load()`` shapes it.

    Mappings stay dicts (named lists); a sequence of scalars of one type is an
    atomic vector (a tuple here), any other sequence a list.
    """
    if isinstance(x, dict):
        return {str(k): _r_yaml(v) for k, v in x.items()}
    if isinstance(x, _RTaggedSeq):
        return [_r_yaml(v) for v in x]
    if isinstance(x, list):
        items = [_r_yaml(v) for v in x]
        kinds = {type(v) for v in items}
        if items and len(kinds) == 1 and kinds <= {str, int, float, bool}:
            return tuple(items)
        return items
    return x


def _yaml_load(text: str) -> Any:
    """``yaml::yaml.load()`` (``None`` on error)."""
    import yaml

    try:
        return _r_yaml(yaml.load(text, Loader=_yaml_loader()))  # noqa: S506 - SafeLoader subclass
    except Exception:
        return None


def _qmd_lang(file_name: Any) -> str | None:
    """The engine language of a Quarto document: ``"R"``, ``"Python"`` or another language.

    Port of ``R/code_check.R::.qmd_lang()``.
    """
    if not isinstance(file_name, str) or not os.path.exists(file_name):
        return "R"
    txt = _try_code_read(file_name)
    if not txt:
        return "R"

    yaml_lang: str | None = None
    if grepl(r"^---\s*$", txt[0]):
        rest = grepl(r"^---\s*$", txt[1:])
        end = next((i + 1 for i, hit in enumerate(rest) if hit), None)
        if end is not None:
            # the lines between the fences: none when the fence closes at once
            # (R's txt[2:end] then reads the two fences in reverse), and a
            # scalar or array front matter has no `jupyter` key (R's `$`
            # errors escape code_lang(); UPSTREAM_ISSUES U68)
            doc = _yaml_load("\n".join(txt[1:end]))
            jupyter = field(doc, "jupyter")
            value: Any = None
            if isinstance(jupyter, dict | list):  # is.list()
                value = field(jupyter, "kernelspec", "language")
                if value is None:
                    value = field(jupyter, "language")
            elif isinstance(jupyter, str) or (
                isinstance(jupyter, tuple) and all(isinstance(v, str) for v in jupyter)
            ):
                value = jupyter
            first = _first_chr(value)
            yaml_lang = first.lower() if first is not None else None
    if yaml_lang is not None:
        # bare Jupyter kernel names ("python3", "ir", "julia-1.9") as well as language names
        # are seen; an engine other than R or Python (Julia) is returned as itself, so it is
        # never purled or run as R (metacheck #480)
        declared = _kernel_language(yaml_lang)
        if declared is not None:
            return declared

    chunk = next((t for t in txt if grepl(r"^```+\s*\{[a-zA-Z]+", t, perl=True)), None)
    if chunk is not None:
        lang = regextract(r"(?<=\{)[a-zA-Z]+", chunk, perl=True)
        if lang is not None and lang.lower().startswith("py"):
            return "Python"
        if lang is not None and lang.lower() == "julia":
            return "Julia"
    return "R"


def _ext_code_lang(ext: str) -> str | None:
    from metacheck.datacheck._files_registry import EXT_REGISTRY

    for row in EXT_REGISTRY:
        if row[0] == ext:
            return row[3]
    return None


def _code_lang1(file_name: Any) -> str | None:
    if is_missing(file_name):
        raise ValueError("missing value where TRUE/FALSE needed")
    ext = _file_ext(str(file_name)).lower()
    if ext == "ipynb":
        return _ipynb_lang(file_name)
    if ext == "qmd":
        return _qmd_lang(file_name)
    return _ext_code_lang(ext)


def code_lang(file_name: Any) -> Any:
    """Detect the code language of files from their names.

    Port of ``R/code_check.R::code_lang()``. Returns ``"R"``, ``"Python"``,
    ``"SPSS"``, ``"SAS"``, ``"Stata"``, ``"Mplus"``, ``"MATLAB"``, ``"NetLogo"``,
    ``"Julia"`` (or ``"JASP"`` / ``"jamovi"``), or ``None`` for other files. A notebook's (``.ipynb``) or
    Quarto document's (``.qmd``) language is read from the file when it
    exists. A single name gives a single value; a sequence of names gives a
    list (R's named vector, in order).
    """
    if file_name is None:
        return []
    if isinstance(file_name, str | os.PathLike):
        return _code_lang1(os.fspath(file_name))
    values = list(file_name.tolist() if isinstance(file_name, pd.Series) else file_name)
    return [_code_lang1(None if is_missing(v) else os.fspath(v)) for v in values]


# ---------------------------------------------------------------------------
# repository listing expansions
# ---------------------------------------------------------------------------


def _col(df: pd.DataFrame, name: str, default: Any = None) -> list[Any]:
    """``df$name`` as a list (``default`` recycled when the column is absent)."""
    if name in df.columns:
        return df[name].tolist()
    return [default] * len(df)


def _in_folder(files: pd.DataFrame, folder: str) -> list[bool]:
    """Whether each file is in a folder of this name, at any depth.

    Port of ``R/code_check.R::.in_folder()``. The folder is in ``file_path``; ``file_name`` holds
    the base name only, so it is used just when a row has no ``file_path``.
    """
    if len(files) == 0:
        return []
    paths = _col(files, "file_path")
    names = _col(files, "file_name")
    where = [n if is_missing(p) or str(p) == "" else p for p, n in zip(paths, names, strict=True)]
    rx = re.compile(f"(^|/){re.escape(folder)}/", re.IGNORECASE)
    return [not is_missing(w) and rx.search(str(w).replace("\\", "/")) is not None for w in where]


def _in_renv_folder(files: pd.DataFrame) -> list[bool]:
    """Whether each file of a listing is in a ``renv/`` folder.

    Port of ``R/code_check.R::.in_renv_folder()``. Files there were written by the renv package,
    not by the authors: ``renv/activate.R`` (run by the project's ``.Rprofile`` to set up its
    package library) and, when the folder is shared in full, ``renv/library/`` with the R files of
    every installed package. ``code_check`` and ``reproducibility_check`` leave them out, so they
    are not counted, checked or run as the paper's code (metacheck #548). ``renv.lock`` sits next
    to the folder, not in it, so it is still found as a version pin.
    """
    return _in_folder(files, "renv")


def _empty_loc(values: list[Any]) -> list[bool]:
    """``is.na(x) | !nzchar(x %||% "")``."""
    return [is_missing(v) or str(v) == "" for v in values]


def _has_value(values: list[Any]) -> list[bool]:
    """``!is.na(x) & nzchar(x %||% "")``."""
    return [not is_missing(v) and str(v) != "" for v in values]


def _repo_file_counts(all_files: pd.DataFrame) -> dict[Any, int]:
    """``table(all_files$repo_url)`` (NA not counted)."""
    counts: dict[Any, int] = {}
    for v in _col(all_files, "repo_url"):
        if not is_missing(v):
            counts[v] = counts.get(v, 0) + 1
    return counts


def _download(rows: pd.DataFrame, all_files: pd.DataFrame, **kwargs: Any) -> pd.DataFrame | None:
    """``tryCatch(download_repo_files(...), error = function(e) NULL)``."""
    try:
        from metacheck.archives.download import download_repo_files

        return download_repo_files(  # type: ignore[no-any-return]
            rows.reset_index(drop=True),
            repo_file_counts=_repo_file_counts(all_files),
            **kwargs,
        )
    except Exception:
        return None


def _set_locations(df: pd.DataFrame, mask: list[bool], dl: pd.DataFrame | None) -> pd.DataFrame:
    if dl is None or "file_location" not in getattr(dl, "columns", []):
        return df
    df = df.copy()
    if "file_location" not in df.columns:
        df["file_location"] = pd.Series([None] * len(df), dtype=object)
    locs = df["file_location"].astype(object).tolist()
    new = dl["file_location"].tolist()
    k = 0
    for i, m in enumerate(mask):
        if m:
            locs[i] = new[k] if k < len(new) else None
            k += 1
    df["file_location"] = pd.Series(locs, index=df.index, dtype=object)
    return df


def _copy_attrs(df: pd.DataFrame, src: Any, names: Sequence[str]) -> None:
    for name in names:
        df.attrs[name] = getattr(src, "attrs", {}).get(name) if src is not None else None


def _code_predownload(
    all_files: pd.DataFrame,
    max_file_size: Any,
    max_download_size: Any,
    cache: Any,
    skip_on_api_limit: bool = False,
    max_files_per_repo: float = math.inf,
) -> pd.DataFrame:
    """Download every code-check candidate file in one pass.

    Port of ``R/code_check.R::.code_predownload()``: the union of the rows the
    ``_code_expand_*`` steps and the checked code languages need is fetched
    with a single ``download_repo_files()`` call, filling ``file_location``;
    the ``gated``, ``oversize_skipped`` and ``failed`` attributes of that
    download are copied to ``all_files.attrs``.
    """
    from metacheck.datacheck._files_registry import EXT_REGISTRY

    # R: a missing language/file_location/file_url column is NULL, and the
    # zero-length logical vectors it produces make need_dl empty
    if not {"language", "file_location", "file_url"} <= set(all_files.columns):
        return all_files
    langs = {r[3] for r in EXT_REGISTRY if r[3] is not None} - {"JASP", "jamovi"}
    langs.add("Python")
    names = _col(all_files, "file_name")
    is_ext = grepl(r"\.(spv|smcl|out|html?)$", names, ignore_case=True)
    language = _col(all_files, "language")
    is_candidate = [e or (lg in langs) for e, lg in zip(is_ext, language, strict=True)]
    # When the code will run, this first download also fetches what the run needs (data, models,
    # configuration), so no host is asked for the repository twice (see _download_for_run())
    from metacheck.archives.download import _download_for_run, _run_download_want

    if _download_for_run():
        is_candidate = [
            c or r for c, r in zip(is_candidate, _run_download_want(all_files), strict=True)
        ]
    file_url = _col(all_files, "file_url")
    archive_url = _col(all_files, "archive_url")
    archive_member = _col(all_files, "archive_member")
    has_target = [
        u or (a and not is_missing(m))
        for u, a, m in zip(
            _has_value(file_url), _has_value(archive_url), archive_member, strict=True
        )
    ]
    # renv's own files are left out of code_check, so they are not downloaded either: a shared
    # renv/library/ would otherwise spend the repository's download budget before the paper's own
    # code
    in_renv = _in_renv_folder(all_files)
    need = [
        c and not r and e and t
        for c, r, e, t in zip(
            is_candidate,
            in_renv,
            _empty_loc(_col(all_files, "file_location")),
            has_target,
            strict=True,
        )
    ]
    if not any(need):
        return all_files
    dl = _download(
        all_files.loc[need],
        all_files,
        max_file_size=max_file_size,
        max_download_size=max_download_size,
        max_files_per_repo=max_files_per_repo,
        cache=cache,
        skip_on_api_limit=skip_on_api_limit,
    )
    out = _set_locations(all_files, need, dl)
    _copy_attrs(out, dl, ("gated", "oversize_skipped", "failed"))
    return out


def _expand_output(
    all_files: pd.DataFrame,
    pattern: str,
    export: Any,
    max_file_size: Any,
    max_download_size: Any,
    cache: Any,
    skip_on_api_limit: bool,
    max_files_per_repo: float,
) -> tuple[pd.DataFrame, list[bool], list[pd.DataFrame]]:
    """Shared body of the ``.code_expand_spv/smcl/mplus()`` steps."""
    is_match = grepl(pattern, _col(all_files, "file_name"), ignore_case=True)
    sub_files = all_files.loc[is_match].reset_index(drop=True)
    _require_file_location(sub_files)
    need = _empty_loc(_col(sub_files, "file_location"))
    if any(need) and "file_url" in sub_files.columns:
        dl = _download(
            sub_files.loc[need],
            all_files,
            max_file_size=max_file_size,
            max_download_size=max_download_size,
            cache=cache,
            max_files_per_repo=max_files_per_repo,
            skip_on_api_limit=skip_on_api_limit,
        )
        sub_files = _set_locations(sub_files, need, dl)
    new_rows: list[pd.DataFrame] = []
    for i in range(len(sub_files)):
        loc = _col(sub_files, "file_location")[i]
        if is_missing(loc) or not str(loc) or not os.path.exists(str(loc)):
            continue
        try:
            path = export(str(loc))
        except Exception:
            path = None
        if is_missing(path):
            continue
        new_rows.append(_synthetic_row(sub_files, i, str(path)))
    return sub_files, is_match, new_rows


def _require_file_location(files: pd.DataFrame) -> None:
    """R's ``file.exists(files$file_location[i])`` fails when the column is absent."""
    if len(files) and "file_location" not in files.columns:
        raise TypeError("invalid 'file' argument")


def _synthetic_row(files: pd.DataFrame, i: int, path: str) -> pd.DataFrame:
    """One recovered-code row: named after *path*, under ``<dir>/code/``."""
    row = files.iloc[[i]].copy().reset_index(drop=True)
    if "file_path" in files.columns:
        fp = files["file_path"].iloc[i]
        base_dir = "NA" if is_missing(fp) else _r_dirname(str(fp))
    else:
        base_dir = _r_dirname(str(files["file_name"].iloc[i]))
    name = os.path.basename(path)
    row["file_name"] = name
    row["file_path"] = _r_file_path(base_dir, "code", name)
    # the output file it came from (FAIR_repo() keeps the code, and says so)
    if "file_path" in files.columns:
        fp0 = files["file_path"].iloc[i]
        row["recovered_from"] = pd.Series([None if is_missing(fp0) else str(fp0)], dtype="string")
    else:
        row["recovered_from"] = pd.Series([str(files["file_name"].iloc[i])], dtype="string")
    row["file_location"] = path
    row["file_url"] = pd.Series([None], dtype=object)
    row["file_size"] = float(os.path.getsize(path))
    return row


def _r_dirname(p: str) -> str:
    """R ``dirname()``: ``"."`` for a bare name, trailing slashes ignored."""
    p = p.rstrip("/") or "/"
    d = os.path.dirname(p)
    return d if d else "."


def _r_file_path(*parts: str) -> str:
    return "/".join(parts)


def _bind(all_files: pd.DataFrame, new_rows: list[pd.DataFrame]) -> pd.DataFrame:
    if not new_rows:
        return all_files
    from metacheck._r.frames import bind_rows

    return bind_rows([all_files, *new_rows])


def _code_expand_spv(
    all_files: pd.DataFrame,
    max_file_size: Any,
    max_download_size: Any,
    cache: Any,
    skip_on_api_limit: bool = False,
    max_files_per_repo: float = math.inf,
) -> pd.DataFrame:
    """Recover the SPSS syntax of every ``.spv`` output as a ``code/<name>.sps`` row.

    Port of ``R/code_check.R::.code_expand_spv()``.
    """
    if not any(grepl(r"\.spv$", _col(all_files, "file_name"), ignore_case=True)):
        return all_files

    def export(loc: str) -> Any:
        from metacheck.statout.spv import _spv_export_syntax

        return _spv_export_syntax(loc)

    _, _, rows = _expand_output(
        all_files,
        r"\.spv$",
        export,
        max_file_size,
        max_download_size,
        cache,
        skip_on_api_limit,
        max_files_per_repo,
    )
    return _bind(all_files, rows)


def _code_expand_smcl(
    all_files: pd.DataFrame,
    max_file_size: Any,
    max_download_size: Any,
    cache: Any,
    skip_on_api_limit: bool = False,
    max_files_per_repo: float = math.inf,
) -> pd.DataFrame:
    """Recover the Stata syntax echoed in every ``.smcl`` log as a ``.do`` row.

    Port of ``R/code_check.R::.code_expand_smcl()``.
    """
    if not any(grepl(r"\.smcl$", _col(all_files, "file_name"), ignore_case=True)):
        return all_files

    def export(loc: str) -> Any:
        from metacheck.statout.stata import _smcl_export_syntax

        return _smcl_export_syntax(loc)

    _, _, rows = _expand_output(
        all_files,
        r"\.smcl$",
        export,
        max_file_size,
        max_download_size,
        cache,
        skip_on_api_limit,
        max_files_per_repo,
    )
    return _bind(all_files, rows)


def _code_expand_mplus(
    all_files: pd.DataFrame,
    max_file_size: Any,
    max_download_size: Any,
    cache: Any,
    skip_on_api_limit: bool = False,
    max_files_per_repo: float = math.inf,
) -> pd.DataFrame:
    """Recover the INPUT INSTRUCTIONS of every Mplus ``.out`` as an ``.inp`` row.

    Port of ``R/code_check.R::.code_expand_mplus()``.
    """
    if not any(grepl(r"\.out$", _col(all_files, "file_name"), ignore_case=True)):
        return all_files

    def export(loc: str) -> Any:
        from metacheck.statout.mplus import _mplus_export_syntax

        return _mplus_export_syntax(loc)

    _, _, rows = _expand_output(
        all_files,
        r"\.out$",
        export,
        max_file_size,
        max_download_size,
        cache,
        skip_on_api_limit,
        max_files_per_repo,
    )
    return _bind(all_files, rows)


def _code_expand_html(
    all_files: pd.DataFrame,
    max_file_size: Any,
    max_download_size: Any,
    cache: Any,
    skip_on_api_limit: bool = False,
    max_files_per_repo: float = math.inf,
) -> pd.DataFrame:
    """Recover the R source of rendered R Markdown/Quarto ``.html`` output.

    Port of ``R/code_check.R::.code_expand_html()``: every ``.html``/``.htm``
    file is content-sniffed; an ``"rmd"`` page yields a ``code/<name>.R`` row,
    and any sniffed page (``"rmd"`` or ``"stata"``) is re-typed
    ``data_type = "output"`` when that column exists.
    """
    # not the help pages and vignettes of the packages in a shared renv/library/: renv's own files
    # are left out of code_check (see _in_renv_folder())
    is_html = [
        h and not r
        for h, r in zip(
            grepl(r"\.html?$", _col(all_files, "file_name"), ignore_case=True),
            _in_renv_folder(all_files),
            strict=True,
        )
    ]
    if not any(is_html):
        return all_files
    from metacheck.report import html_output

    html_files = all_files.loc[is_html].reset_index(drop=True)
    _require_file_location(html_files)
    need = _empty_loc(_col(html_files, "file_location"))
    if any(need) and "file_url" in html_files.columns:
        dl = _download(
            html_files.loc[need],
            all_files,
            max_file_size=max_file_size,
            max_download_size=max_download_size,
            cache=cache,
            max_files_per_repo=max_files_per_repo,
            skip_on_api_limit=skip_on_api_limit,
        )
        html_files = _set_locations(html_files, need, dl)

    new_rows: list[pd.DataFrame] = []
    kinds: list[Any] = [None] * len(html_files)
    locs = _col(html_files, "file_location")
    for i, loc in enumerate(locs):
        if is_missing(loc) or not str(loc) or not os.path.exists(str(loc)):
            continue
        try:
            kinds[i] = html_output._html_sniff_kind(str(loc))
        except Exception:
            kinds[i] = None
        if is_missing(kinds[i]) or kinds[i] != "rmd":
            continue
        try:
            r_path = html_output._html_export_r_source(str(loc))
        except Exception:
            r_path = None
        if is_missing(r_path):
            continue
        new_rows.append(_synthetic_row(html_files, i, str(r_path)))

    if "data_type" in all_files.columns:
        sniffed = [not is_missing(k) for k in kinds]
        if any(sniffed):
            all_files = all_files.copy()
            positions = [i for i, h in enumerate(is_html) if h]
            dtype_col = all_files["data_type"].astype(object).tolist()
            for pos, s in zip(positions, sniffed, strict=True):
                if s:
                    dtype_col[pos] = "output"
            all_files["data_type"] = pd.Series(dtype_col, index=all_files.index, dtype=object)
    return _bind(all_files, new_rows)


def _as_lang_list(x: Any) -> list[Any]:
    """``code_lang()`` of one name is a scalar, of several a list."""
    return x if isinstance(x, list) else [x]


def _code_expand_zip(
    all_files: pd.DataFrame,
    skip_on_api_limit: bool = False,
    cache: bool = False,
    max_download_size: Any = 500,
) -> pd.DataFrame:
    """Fetch the code members of every unexpanded remote ``.zip`` row.

    Port of ``R/code_check.R::.code_expand_zip()``: the archive is peeked
    with range requests and only members :func:`code_lang` recognises are
    fetched, one new row each (the ``.zip`` row itself is kept). Under
    *skip_on_api_limit* a rate-limited host is skipped rather than waited
    out; both *skip_on_api_limit* and *cache* go on to the peek and to the
    member fetches. The listing's ``file_size`` saves the peek a HEAD
    request; a zip already on disk (one inside a ResearchBox, whose
    ``file_url`` is the box's web page) is read from the file. An archive
    within *max_download_size* (MB) is downloaded once instead of fetched by
    range when that is cheaper (always on Dryad: metacheck #471), and kept
    where the ordinary download would put it.
    """
    names = _col(all_files, "file_name")
    urls = _col(all_files, "file_url")
    is_zip = [
        z and u
        for z, u in zip(grepl(r"\.zip$", names, ignore_case=True), _has_value(urls), strict=True)
    ]
    if not any(is_zip):
        return all_files
    from metacheck.archives.download import _repo_cache_path
    from metacheck.archives.zip_peek import (
        _whole_request_bytes,
        _zip_fetch_members,
        _zip_local_source,
        zip_peek,
    )

    local = _zip_local_source(all_files)
    sizes = _col(all_files, "file_size")
    try:
        whole_bytes = float(max_download_size) * 1024 * 1024
    except (TypeError, ValueError):
        whole_bytes = 0.0  # R: isTRUE(NULL > 0) is FALSE, no whole download
    new_rows: list[pd.DataFrame] = []
    for i, z in enumerate(is_zip):
        if not z:
            continue
        loc = local[i]
        url = urls[i] if loc is None else loc
        zip_size = sizes[i]
        try:
            peek = zip_peek(url, cache=cache, skip_on_api_limit=skip_on_api_limit, size=zip_size)
        except Exception:
            peek = None
        if peek is None or len(peek) == 0:
            continue
        members = peek["name"].tolist()
        # renv's own files are left out of code_check, so they are not fetched
        in_renv = _in_renv_folder(pd.DataFrame({"file_path": pd.Series(members, dtype=object)}))
        is_code = [
            lang is not None and not r
            for lang, r in zip(_as_lang_list(code_lang(members)), in_renv, strict=True)
        ]
        if not any(is_code):
            continue
        if "file_path" in all_files.columns:
            fp = all_files["file_path"].iloc[i]
            base = "NA" if is_missing(fp) else str(fp)
        else:
            base = str(names[i])
        # .repo_cache_path() is outside R's tryCatch(); the fetch is inside
        dest = _repo_cache_path(_col(all_files, "repo_url")[i], f"{base}.contents", cache=cache)
        try:
            fetched = _zip_fetch_members(
                url,
                names=[m for m, c in zip(members, is_code, strict=True) if c],
                dest=dest,
                cache=cache,
                skip_on_api_limit=skip_on_api_limit,
                max_whole_bytes=whole_bytes,
                archive_path=re.sub(r"\.contents$", "", str(dest)),
                request_bytes=_whole_request_bytes(url),
                size=zip_size,
            )
        except Exception:
            fetched = None
        if fetched is None or not any(fetched["ok"].tolist()):
            continue
        got = fetched.loc[fetched["ok"].astype(bool).tolist()].reset_index(drop=True)
        rows = all_files.iloc[[i] * len(got)].copy().reset_index(drop=True)
        rows["file_name"] = [os.path.basename(str(n)) for n in got["name"].tolist()]
        rows["file_path"] = [_r_file_path(str(base), str(n)) for n in got["name"].tolist()]
        rows["file_location"] = got["path"].tolist()
        rows["file_url"] = pd.Series([None] * len(got), dtype=object)
        rows["file_size"] = got["size"].tolist()
        new_rows.append(rows)
    return _bind(all_files, new_rows)


# ---------------------------------------------------------------------------
# code extraction
# ---------------------------------------------------------------------------


def _write_lines(lines: Sequence[str | None], path: str | os.PathLike[str]) -> None:
    """``writeLines(lines, path)`` (``NA`` is written as ``"NA"``)."""
    with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as fh:
        fh.writelines(f"{'NA' if line is None else line}\n" for line in lines)


def _text_arg(file_path: Any, text: Any) -> list[str | None]:
    if file_path is None and text is None:
        raise ValueError("You must specify one of file_path or text")
    if text is None:
        return list(code_read(file_path))
    return list(_as_chr(text) or [])


def code_extract_r(
    file_path: str | os.PathLike[str] | None = None,
    save_path: str | os.PathLike[str] | None = None,
    documentation: int = 0,
    text: str | Sequence[str | None] | None = None,
) -> Any:
    """Extract the R code of an R Markdown, Quarto or Sweave document.

    Port of ``R/code_check.R::code_extract_r()``, which runs
    ``knitr::purl()``; the chunks are read without R
    (:mod:`metacheck.codecheck._chunks`, D80). *documentation* is purl's
    level: 0 (code only), 1 (chunk headers as comments) or 2 (text chunks as
    roxygen comments too). Returns the code lines, or *save_path* after
    writing them there.
    """
    from metacheck.codecheck._chunks import extract_r

    source = _text_arg(file_path, text)
    # A numeric or logical YAML chunk label ("#| label: 3") is read by knitr as a number and used
    # as a position ("subscript out of bounds"), so the whole file was skipped; quoted, it is the
    # chunk's name (UPSTREAM_ISSUES U153, fixed in metacheck 0.4.3.7)
    source = [
        s
        if s is None
        else re.sub(r"^(\s*#\|\s*label:\s*)([0-9][0-9.]*|TRUE|FALSE|true|false)\s*$", r"\1'\2'", s)
        for s in source
    ]
    out = extract_r(source, documentation=int(documentation))
    if save_path is None:
        # a document with text but without chunks is purled to a file of one empty line, which
        # code_read() now reads as that one line (strsplit() of "\n"); no text, an empty file
        return out or ([""] if source else [])
    _write_lines(out, save_path)
    return os.fspath(save_path)


def code_extract_py(
    file_path: str | os.PathLike[str] | None = None,
    save_path: str | os.PathLike[str] | None = None,
    text: str | Sequence[str] | None = None,
) -> Any:
    """Extract the source of a Jupyter notebook's code cells.

    Port of ``R/code_check.R::code_extract_py()``: code cells in document
    order, each followed by a blank line; markdown/raw cells and the commands
    of the notebook's kernel are dropped (see :func:`_code_notebook_parts`).
    Works for any kernel (see :func:`code_lang`). Returns the lines, or
    *save_path* after writing.
    """
    out = _code_notebook_parts(_text_arg(file_path, text))[0]
    if save_path is None:
        return out
    _write_lines(out, save_path)
    return os.fspath(save_path)


def _code_notebook_parts(text: Sequence[str | None]) -> tuple[list[str], list[str]]:
    """A notebook's code, and the commands left out of it, from its JSON text.

    Port of ``R/code_check.R::.code_notebook_parts()``. Commands are IPython magics (``%cd ..``)
    and shell escapes (``!pip install x``), which are not Python and would otherwise be scanned as
    though they were. An R-kernel notebook has no such commands, and ``  %>% filter()`` and ``!x``
    are code there.
    """
    nb = _json_load("\n".join("NA" if t is None else t for t in text))
    cells = field(nb, "cells")
    if cells is None or _r_length(cells) == 0:
        return [], []
    kernel = field(nb, "metadata", "kernelspec")
    is_r = (_first_chr(field(kernel, "language")) or "").lower() == "r" or (
        _first_chr(field(kernel, "name")) or ""
    ).lower() in ("ir", "r")
    lines: list[str] = []
    removed: list[str] = []
    for cl in _r_elements(cells):
        if field(cl, "cell_type") != "code":
            continue
        src = field(cl, "source")
        if src is None or _r_length(src) == 0:
            continue
        joined = "".join(r_unlist_chr(src))
        cell_lines = [s for piece in strsplit([joined], "\n") for s in piece]
        kept: list[str]
        gone: list[str]
        if is_r:
            kept, gone = cell_lines, []
        else:
            kept, gone = _code_notebook_cell(cell_lines)
        if kept:
            kept = [*kept, ""]  # blank line marks the cell boundary
        lines.extend(kept)
        removed.extend(gone)
    return lines, removed


def _code_notebook_cell(lines: list[str]) -> tuple[list[str], list[str]]:
    """One notebook cell of a non-R kernel without its commands.

    Port of ``R/code_check.R::.code_notebook_cell()``. A cell that starts with ``%%bash`` and the
    like is not Python at all, so the whole cell goes; ``%%time`` and the like only wrap Python. An
    indented command becomes ``pass``, since removing the only line of an ``if`` block would leave
    code that does not parse. Also removed: the commands IPython accepts without a ``%``
    (``cd folder``), and ``name?`` for help.
    """
    first = next((s for s in lines if trimws(s) != ""), None)
    if (
        first is not None
        and re.match(r"\s*%%", first)
        and not re.match(r"\s*%%(time|timeit|capture|prun)\b", first)
    ):
        return [], [trimws(first)]
    is_cmd = [
        bool(
            re.match(r"\s*[%!?]", s)
            or re.match(r"\s*cd(\s+[^=(\s].*)?$", s)
            or re.match(r"\s*[A-Za-z_][A-Za-z0-9_.]*\?{1,2}\s*$", s)
        )
        for s in lines
    ]
    indented = [c and bool(re.match(r"\s", s)) for c, s in zip(is_cmd, lines, strict=True)]
    removed = [trimws(s) for s, c in zip(lines, is_cmd, strict=True) if c]
    out: list[str] = []
    for s, c, ind in zip(lines, is_cmd, indented, strict=True):
        if ind:
            m = re.match(r"(\s*)", s)
            out.append((m.group(1) if m else "") + "pass")
        elif not c:
            out.append(s)
    return out, removed


def _r_length(x: Any) -> int:
    if isinstance(x, list | dict):
        return len(x)
    return 1


def _r_elements(x: Any) -> list[Any]:
    if isinstance(x, dict):
        return list(x.values())
    if isinstance(x, list):
        return list(x)
    return [x]


def code_extract_qmd_py(
    file_path: str | os.PathLike[str] | None = None,
    save_path: str | os.PathLike[str] | None = None,
    text: str | Sequence[str | None] | None = None,
) -> Any:
    """Extract the Python chunks of a Quarto document.

    Port of ``R/code_check.R::code_extract_qmd_py()``: the body of every
    ```` ```{python} ```` fence (closed by a backtick line of the same
    length), without ``#|`` option lines, each chunk followed by a blank line.
    """
    text_lines = _text_arg(file_path, text)
    out: list[str | None] = []
    i = 0
    n = len(text_lines)
    opens = regexec(r"^(```+)\s*\{python\b", text_lines, ignore_case=True)
    while i < n:
        fence = opens[i]
        if not fence:
            i += 1
            continue
        close = compile_r(f"^{fence[1]}\\s*$")
        j = i + 1
        body: list[str | None] = []
        while j < n and (text_lines[j] is None or close.search(cast(str, text_lines[j])) is None):
            body.append(text_lines[j])
            j += 1
        keep = grepl(r"^\s*#\|", body)
        body = [b for b, k in zip(body, keep, strict=True) if not k]
        if body:
            out.extend(body)
            out.append("")
        i = j + 1
    if save_path is None:
        return out
    _write_lines(out, save_path)
    return os.fspath(save_path)


# ---------------------------------------------------------------------------
# code_parse_r()
# ---------------------------------------------------------------------------


def code_parse_r(
    file_path: str | os.PathLike[str] | Sequence[str] = "",
    text: str | Sequence[str] | None = None,
    *,
    engine: str | None = None,
) -> pd.DataFrame:
    """Check R code for parse errors.

    Port of ``R/code_check.R::code_parse_r()``: one row per file with columns
    ``file_path``, ``error`` and ``msg`` (R's parse error message, with
    ``<text>`` replaced by ``line``). R Markdown/Quarto files (text starting
    with a ``---`` line) are purled first.

    *engine* (pytacheck only) picks the parser: ``"r"`` runs R's own parser in
    one ``Rscript`` process (``PYTACHECK_RSCRIPT`` or ``Rscript`` on the
    ``PATH``; Python when there is none), ``"python"`` a Python port of R
    4.5.3's parser that reproduces its messages, and ``None`` (default) uses
    ``PYTACHECK_R_PARSER`` if set, else R when the reference R is configured
    with ``PYTACHECK_RSCRIPT``, else Python.
    """
    from metacheck.codecheck._rparse import parse_errors

    paths = [file_path] if isinstance(file_path, str | os.PathLike) else list(file_path)
    paths = [os.fspath(p) for p in paths]
    if all(p == "" for p in paths) and text is None:
        raise ValueError("You must specify one of file_path or text")
    given = None if text is None else list(_as_chr(text) or [])
    texts: list[list[str]] = []
    for fp in paths:
        lines = code_read(fp) if fp != "" else given
        if lines is None:
            raise TypeError("argument is of length zero")
        # an empty file parses (older R: "subscript out of bounds", fixed in PR #426)
        if lines and lines[0] is not None and grepl(r"^---\s*$", lines[0]):
            lines = code_extract_r(text=lines)
        texts.append(["NA" if v is None else v for v in lines])
    if not paths:
        return pd.DataFrame()
    msgs = parse_errors(texts, engine)
    return pd.DataFrame(
        {
            "file_path": pd.Series(paths, dtype="string"),
            "error": pd.Series([m is not None for m in msgs], dtype="boolean"),
            "msg": pd.Series(
                [None if m is None else m.replace("<text>", "line", 1) for m in msgs],
                dtype="string",
            ),
        }
    )


# ---------------------------------------------------------------------------
# absolute paths, setwd(), install.packages()
# ---------------------------------------------------------------------------

_ABS_PATH = (
    "([\"'])"
    "(?:~/(?:[^\\n'\"]+)|"
    "/(?!/)[^\\n'\"/]+/[^\\n'\"]+|"
    "[A-Za-z]:[\\\\/][^\\n'\"]+|"
    "\\\\\\\\(?=[A-Za-z0-9._-]*[A-Za-z])[A-Za-z0-9._-]+\\\\[^\\n'\"]+)"
    "\\1"
)


def _search_matches(lines: list[str | None], pattern: str) -> tuple[list[int], list[str]]:
    """``search_text(<lines>, pattern, perl = TRUE, return = "match")``.

    ``search_text()`` ignores case by default. Returns the 1-based line
    numbers and the matched texts, one per match.
    """
    hits = grepl(pattern, lines, ignore_case=True, perl=True)
    ids: list[int] = []
    texts: list[str] = []
    matched = [s for s, h in zip(lines, hits, strict=True) if h]
    found = regextract_all(pattern, matched, ignore_case=True, perl=True)
    line_ids = [i + 1 for i, h in enumerate(hits) if h]
    for line_id, ms in zip(line_ids, found, strict=True):
        for m in ms:
            ids.append(line_id)
            texts.append(m)
    return ids, texts


def code_abs_path(code_text: str | Sequence[str]) -> pd.DataFrame:
    """Find absolute file paths in (comment-free) code.

    Port of ``R/code_check.R::code_abs_path()``: quoted strings that are
    Windows (``C:/``, ``D:\\``), UNC (``\\\\host\\share``), home (``~/``) or
    two-segment Unix (``/Users/x``) paths. Returns a frame with columns
    ``abs_path`` and ``line``.
    """
    # no code (character(0)) has no paths; older R returned its columns the
    # other way round and warned (fixed in PR #426, UPSTREAM_ISSUES U67)
    lines = _split_lines(code_text) or []
    # R runs search_text() twice: the first (return = "sentence") keeps the
    # matching lines but normalises their text the way search_text() does for
    # every non-"match" return (whitespace runs -> " ", " , " -> ", ", the
    # paragraph marker -> "\n\n"); the paths are then extracted from that
    # normalised text, so "C:/My   Docs/a , b.csv" is reported as
    # "C:/My Docs/a, b.csv".
    hits = grepl(_ABS_PATH, lines, ignore_case=True, perl=True)
    line_ids = [i + 1 for i, h in enumerate(hits) if h]
    normed = gsub(r"\s+", " ", [s for s, h in zip(lines, hits, strict=True) if h])
    normed = gsub(" , ", ", ", normed, fixed=True)
    normed = gsub("<~p~>", "\n\n", normed, fixed=True)
    ids, texts = _search_matches(normed, _ABS_PATH)
    ids = [line_ids[i - 1] for i in ids]
    paths = gsub("[\"']$", "", gsub("^[\"']", "", texts))
    return pd.DataFrame(
        {
            "abs_path": pd.Series(paths, dtype="string"),
            "line": pd.Series(ids, dtype="Int64"),
        }
    )


def _code_pos_in_string(L: str | None, pos: int | None) -> bool:
    """Whether 1-based character *pos* of line *L* is inside a string literal.

    Port of ``R/code_check.R::.code_pos_in_string()`` (single or double
    quotes, backslash escapes the next character).
    """
    if pos is None or pos < 1 or L is None:
        return False
    if not L or pos > len(L):
        return False
    quote: str | None = None
    escaped = False
    for ch in L[: pos - 1]:
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if quote is None:
            if ch in ("'", '"'):
                quote = ch
        elif ch == quote:
            quote = None
    return quote is not None


def _live_calls(
    lines: list[str | None], pattern: str, call_start: str, column: str
) -> pd.DataFrame:
    ids, texts = _search_matches(lines, pattern)
    start_rx = compile_r(call_start, perl=True)
    keep = []
    for line_id in ids:
        line = lines[line_id - 1]
        m = start_rx.search(line) if line is not None else None
        keep.append(not _code_pos_in_string(line, m.start() + 1 if m else None))
    return pd.DataFrame(
        {
            column: pd.Series([t for t, k in zip(texts, keep, strict=True) if k], dtype="string"),
            "line": pd.Series([i for i, k in zip(ids, keep, strict=True) if k], dtype="Int64"),
        }
    )


def code_setwd(code_text: str | Sequence[str]) -> pd.DataFrame:
    """Find ``setwd()`` calls in (comment-free) R code.

    Port of ``R/code_check.R::code_setwd()``: columns ``setwd_call`` (the call
    as written, to the last ``)`` on the line) and ``line``; a ``setwd(``
    inside a string literal is not a call.
    """
    lines = _split_lines(code_text) or []
    return _live_calls(lines, r"setwd\s*\(.*\)", r"setwd\s*\(", "setwd_call")


def code_install_packages(code_text: str | Sequence[str]) -> pd.DataFrame:
    """Find ``install.packages()`` calls in (comment-free) R code.

    Port of ``R/code_check.R::code_install_packages()``: columns
    ``install_packages_call`` and ``line``.
    """
    lines = _split_lines(code_text) or []
    return _live_calls(
        lines,
        r"(?:utils::)?install\.packages\s*\(.*\)",
        r"(?:utils::)?install\.packages\s*\(",
        "install_packages_call",
    )


# ---------------------------------------------------------------------------
# comments
# ---------------------------------------------------------------------------


def _code_strip_inline_comment(L: str | None, marker: str) -> str | None:
    """Strip a trailing comment, ignoring markers inside string literals.

    Port of ``R/code_check.R::.code_strip_inline_comment()``.
    """
    if L is None:
        raise ValueError("missing value where TRUE/FALSE needed")
    if marker not in L:
        return L
    n = len(L)
    m = len(marker)
    quote: str | None = None
    escaped = False
    i = 0
    while i < n:
        ch = L[i]
        if escaped:
            escaped = False
            i += 1
            continue
        if ch == "\\":
            escaped = True
            i += 1
            continue
        if quote is None:
            if ch in ("'", '"'):
                quote = ch
            elif L.startswith(marker, i) and i + m <= n:
                return L[:i]
        elif ch == quote:
            quote = None
        i += 1
    return L


def _flags(pattern: str, lines: list[str | None]) -> list[bool]:
    return list(grepl(pattern, lines))


def code_remove_comments(
    code_text: str | Sequence[str | None], lang: str | Sequence[str] = LANGS
) -> list[str | None]:
    """Remove comments (and, for R/Python, blank lines) from code.

    Port of ``R/code_check.R::code_remove_comments()`` for R, Python, SPSS,
    SAS, Stata, Mplus and MATLAB: whole-line comments and block comments are
    dropped, trailing comments stripped (markers inside strings are kept).
    """
    lang = _match_arg(lang)
    lines = _split_lines(code_text) or []
    out: list[str | None] = []
    in_block = False

    if lang == "R":
        drop = _flags(r"^(\s*$|\s*#|```\s*\{r)", lines)
        return [
            _code_strip_inline_comment(s, "#") for s, d in zip(lines, drop, strict=True) if not d
        ]
    if lang == "Python":
        drop = _flags(r"^\s*#", lines)
        kept = [
            _code_strip_inline_comment(s, "#") for s, d in zip(lines, drop, strict=True) if not d
        ]
        blank = trimws(kept)
        return [s for s, b in zip(kept, blank, strict=True) if b != ""]
    if lang == "SAS":
        starts = _flags(r"/\*", lines)
        ends = _flags(r"\*/", lines)
        line_comment = _flags(r"^\s*\*.*;\s*$", lines)
        for L, sb, eb, lc in zip(lines, starts, ends, line_comment, strict=True):
            if not in_block and sb:
                in_block = True
            if not in_block and not lc:
                out.append(L)
            if in_block and eb:
                in_block = False
        return out
    if lang == "SPSS":
        starts = _flags(r"/\*|COMMENT BEGIN", lines)
        ends = _flags(r"\*/|COMMENT END\.", lines)
        line_comment = _flags(r"^\s*(\*|COMMENT)", lines)
        for L, sb, eb, lc in zip(lines, starts, ends, line_comment, strict=True):
            if not in_block and sb:
                in_block = True
            if not in_block and not lc:
                out.append(L)
            if in_block and eb:
                in_block = False
        return out
    if lang == "Stata":
        starts = _flags(r"/\*", lines)
        ends = _flags(r"\*/", lines)
        line_comment = _flags(r"^\s*\*", lines)
        for L, sb, eb, lc in zip(lines, starts, ends, line_comment, strict=True):
            if not in_block and sb:
                in_block = True
            if not in_block and not lc:
                if L is not None and "//" in L:
                    L = _code_strip_inline_comment(L, "//")
                out.append(L)
            if in_block and eb:
                in_block = False
        return out
    if lang == "Mplus":
        drop = _flags(r"^\s*!", lines)
        return list(sub("!.*$", "", [s for s, d in zip(lines, drop, strict=True) if not d]))
    if lang == "MATLAB":
        starts = _flags(r"^\s*%\{\s*$", lines)
        ends = _flags(r"^\s*%\}\s*$", lines)
        whole = _flags(r"^\s*%", lines)
        for L, sb, eb, wl in zip(lines, starts, ends, whole, strict=True):
            if not in_block and sb:
                in_block = True
                continue
            if in_block:
                if eb:
                    in_block = False
                continue
            if wl:
                continue
            out.append(_code_strip_inline_comment(L, "%"))
        return out
    return list(lines)


def _code_comment_flags(code_text: Sequence[str | None], lang: str) -> list[bool]:
    """Whether each line holds a comment (whole-line or trailing).

    Port of ``R/code_check.R::.code_comment_flags()``, mirroring
    :func:`code_remove_comments`'s rules line for line.
    """
    lines = list(code_text)
    n = len(lines)
    has = [False] * n
    in_block = False
    if lang in ("R", "Python"):
        whole = _flags(r"^\s*#", lines)
        return [
            w or _code_strip_inline_comment(L, "#") != L for L, w in zip(lines, whole, strict=True)
        ]
    if lang in ("SAS", "SPSS", "Stata"):
        if lang == "SPSS":
            starts = _flags(r"/\*|COMMENT BEGIN", lines)
            ends = _flags(r"\*/|COMMENT END\.", lines)
            line_comment = _flags(r"^\s*(\*|COMMENT)", lines)
        else:
            starts = _flags(r"/\*", lines)
            ends = _flags(r"\*/", lines)
            line_comment = _flags(r"^\s*\*.*;\s*$" if lang == "SAS" else r"^\s*\*", lines)
        for ln, L in enumerate(lines):
            was = in_block
            if not in_block and starts[ln]:
                in_block = True
            trailing = False
            if lang == "Stata":
                trailing = (
                    not was
                    and not line_comment[ln]
                    and L is not None
                    and "//" in L
                    and _code_strip_inline_comment(L, "//") != L
                )
            has[ln] = was or line_comment[ln] or (not was and starts[ln]) or trailing
            if in_block and ends[ln]:
                in_block = False
        return has
    if lang == "Mplus":
        return _flags("!", lines)
    if lang == "MATLAB":
        starts = _flags(r"^\s*%\{\s*$", lines)
        ends = _flags(r"^\s*%\}\s*$", lines)
        whole = _flags(r"^\s*%", lines)
        for ln, L in enumerate(lines):
            was = in_block
            if not in_block and starts[ln]:
                in_block = True
                has[ln] = True
                continue
            if was:
                has[ln] = True
                if ends[ln]:
                    in_block = False
                continue
            trailing = not whole[ln] and _code_strip_inline_comment(L, "%") != L
            has[ln] = whole[ln] or trailing
        return has
    return has


def _code_has_docstring(code_text: str | Sequence[str | None]) -> bool:
    """Whether Python code holds a complete triple-quoted string block.

    Port of ``R/code_check.R::.code_has_docstring()``.
    """
    lines = _split_lines(code_text) or []
    text = "\n".join("NA" if s is None else s for s in lines)
    return bool(grepl("(?s)(\"\"\"|''').*?\\1", text, perl=True))


def code_line_stats(
    code_text: str | Sequence[str], lang: str | Sequence[str] = LANGS
) -> dict[str, Any]:
    """Line counts of a code file.

    Port of ``R/code_check.R::code_line_stats()``: a dict with
    ``total_lines`` (after dropping blank lines between elements, as R's
    ``strsplit(x, "\\n+")`` does), ``comment_lines`` (lines holding a
    whole-line or trailing comment), ``code_lines``, ``percent_comments``
    (``nan`` for empty code) and ``has_docstring`` (Python only, else
    ``None``).
    """
    lang = _match_arg(lang)
    # no code (character(0)) counts like "" (older R errors; fixed in PR #426, U67)
    lines = _split_lines(code_text) or []
    total = len(lines)
    code_lines = len(code_remove_comments(lines, lang))
    flags = _code_comment_flags(lines, lang)
    blank = trimws(lines)
    # R: sum(flags & trimws(code_text) != ""), where a flagged NA line gives
    # TRUE & NA = NA and so an NA sum (and percentage)
    comment_lines: int | None = sum(
        1 for f, b in zip(flags, blank, strict=True) if f and b is not None and b != ""
    )
    if any(f and b is None for f, b in zip(flags, blank, strict=True)):
        comment_lines = None
    percent = (math.nan if comment_lines is None else comment_lines / total) if total else math.nan
    has_doc = _code_has_docstring(lines) if lang == "Python" else None
    return {
        "total_lines": total,
        "comment_lines": comment_lines,
        "code_lines": code_lines,
        "percent_comments": percent,
        "has_docstring": has_doc,
    }


# ---------------------------------------------------------------------------
# packages
# ---------------------------------------------------------------------------

_IMPORT_REGEX = {
    "R": r"^[^#]*\b(library|require|renv::install|p_load)\s*\(",
    "Python": (
        r"^\s*from\s+[A-Za-z_][A-Za-z0-9_.]*\s+import\s|"
        r"^\s*import\s+[A-Za-z_][A-Za-z0-9_.]*"
        r"(\s*,\s*[A-Za-z_][A-Za-z0-9_.]*)*(\s+as\s+\w+)?\s*$"
    ),
    "SAS": r"\b(%include|libname|filename|options)\b",
    "SPSS": r"\b(INSERT|BEGIN\s+PROGRAM|SET)\b",
    "Stata": r"\b(do|run|cd|adopath|net\s+install|ssc\s+install)\b",
    "Mplus": r"(?!)",
    "MATLAB": r"\b(addpath|import)\s*\(",
}


def code_library_lines(
    code_text: str | Sequence[str], lang: str | Sequence[str] = LANGS
) -> pd.DataFrame:
    """The lines that load libraries/packages (after removing comments).

    Port of ``R/code_check.R::code_library_lines()``: columns ``code`` and
    ``line`` (the line number among the comment-free lines). Matching ignores
    case, as ``search_text()`` does.
    """
    lang = _match_arg(lang)
    lines = code_remove_comments(code_text, lang)
    hits = grepl(_IMPORT_REGEX[lang], lines, ignore_case=True, perl=True)
    return pd.DataFrame(
        {
            "code": pd.Series([s for s, h in zip(lines, hits, strict=True) if h], dtype="string"),
            "line": pd.Series([i + 1 for i, h in enumerate(hits) if h], dtype="Int64"),
        }
    )


# the function around the call that loads a package, in lapply(pkgs, function(x) library(x, ...))
_FN_WRAP = r"(?:function\s*\([^)]*\)|\\\s*\([^)]*\))?\s*\{?\s*(?:suppress\w+\s*\(\s*)?"


def _empty_packages() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "package": pd.Series([], dtype="string"),
            "source": pd.Series([], dtype="string"),
            "line": pd.Series([], dtype="Int64"),
        }
    )


def _cap(regex: str, line: str | None, i: int) -> str | None:
    """The *i*-th capture group of the first PCRE match (``None`` if empty)."""
    m = regexec(regex, line, perl=True)
    if len(m) >= i + 1 and m[i] != "":
        return str(m[i])
    return None


def _code_char_vector_vars(code_text: Sequence[str | None]) -> dict[str, list[str]]:
    """Character values a script assigns literally to a variable.

    Port of ``R/code_check.R::.code_char_vector_vars()``, for
    :func:`code_library_names` to resolve a package-list variable (issue
    #421). Recognises ``name <- c("a", "b")`` (also ``=`` and ``<<-``, and a
    ``c(...)`` spread over several lines), ``name <- "a"``, and a for-loop
    variable over such a vector, ``for (v in name)``, which takes the
    vector's values. Anything else (a vector built by a function call,
    indexing, other variables) is not resolved. A later assignment to the
    same name wins.
    """
    joined = "\n".join("NA" if s is None else s for s in code_text)
    ident = "[.A-Za-z][.A-Za-z0-9_]*"
    out: dict[str, list[str]] = {}

    pat = (
        rf"(?m)^\s*({ident})\s*(?:<<-|<-|=)\s*"
        r"(c\s*\([^()]*\)|['\"][^'\"\n]*['\"])"
    )
    for hit in regextract_all(pat, joined, perl=True):
        parts = regexec(pat, hit, perl=True)
        value = str(sub(r"^c\s*\((.*)\)$", r"\1", parts[2]))
        strs = regextract_all(r"(['\"])[^'\"\n]*\1", value, perl=True)
        # only a vector of nothing but quoted strings is a literal package list
        rest = gsub(r"(['\"])[^'\"\n]*\1", "", value, perl=True)
        if not strs or grepl(r"[^[:space:],]", rest):
            continue
        out[parts[1]] = [str(gsub(r"^['\"]|['\"]$", "", q)) for q in strs]

    for loop in regextract_all(rf"\bfor\s*\(\s*({ident})\s+in\s+({ident})\s*\)", joined, perl=True):
        parts = regexec(rf"\(\s*({ident})\s+in\s+({ident})\s*\)", loop, perl=True)
        if parts[2] in out:
            out[parts[1]] = out[parts[2]]
    return out


def code_library_names(
    code_text: str | Sequence[str], lang: str | Sequence[str] = LANGS
) -> pd.DataFrame:
    """The names of the packages a code file loads.

    Port of ``R/code_check.R::code_library_names()``. R: ``library()``,
    ``require()``, ``requireNamespace()`` (all calls on a line), pacman's
    ``p_load()``, ``install.packages()``/``renv::install()`` and ``pkg::fun``
    namespaces; Python: ``import a, b as c`` and ``from a.b import c`` (top
    level package). Other languages give an empty frame. Columns ``package``,
    ``source`` and ``line``; rows are unique.

    Where an R call takes a variable rather than a package name
    (``install.packages(pkgs)``, ``requireNamespace(pkg)``,
    ``library(pkg, character.only = TRUE)``, ``lapply(pkgs, library, ...)``)
    the variable is resolved to the names it holds when the file assigns it a
    literal vector (``pkgs <- c("a", "b")``, including a ``for (pkg in pkgs)``
    loop variable) and dropped otherwise: the variable's own name is never
    reported as a package (:func:`_code_char_vector_vars`).
    """
    lang = _match_arg(lang)
    if lang in ("SPSS", "SAS", "Stata", "Mplus", "MATLAB"):
        return _empty_packages()
    lines = code_remove_comments(code_text, lang)
    if not lines:
        return _empty_packages()

    pkgs_out: list[str] = []
    src_out: list[str] = []
    line_out: list[int] = []

    def add(pkgs: Sequence[str | None], source: str, line: int) -> None:
        cleaned = trimws(gsub("^['\"]|['\"]$", "", list(pkgs)))
        for p in cleaned:
            if _nzchar(p):
                pkgs_out.append("NA" if p is None else p)
                src_out.append(source)
                line_out.append(line)

    # Variables the script assigns a literal package list to (issue #421): the
    # argument of a loading call is then the VARIABLE, not a package name.
    variables = _code_char_vector_vars(lines) if lang == "R" else {}

    def arg_packages(tok: str, bare_is_name: bool) -> list[str]:
        """Package names of one argument: a quoted name, a bare name only
        where *bare_is_name* (``library(dplyr)``), else a variable resolved
        through *variables* (``pkgs[...]`` resolves like ``pkgs``)."""
        tok = str(trimws(tok))
        if grepl(r"^(['\"]).*\1$", tok):
            return [tok]
        ident = str(sub(r"\[.*$", "", tok))
        if not grepl(r"^[.A-Za-z][.A-Za-z0-9_]*$", ident):
            return []
        if bare_is_name and ident == tok:
            return [tok]
        return list(variables.get(ident, []))

    char_only = r"character\.only\s*=\s*(TRUE|T)\b"

    for ln, L in enumerate(lines, start=1):
        if lang == "R":
            # a bare word is a package name for library()/require() unless the
            # call sets character.only = TRUE; requireNamespace() always
            # evaluates its argument, so a bare word there is a variable
            # not when a point comes before the name: groundhog.library(c("a", "b"), date) was read
            # as library(c), so a package called "c" was installed (it has its own search, below)
            for fn in ("library", "require", "requireNamespace"):
                for call in regextract_all(
                    rf"(?<![\w.]){fn}\s*\(\s*([A-Za-z0-9._'\"]+)([^)]*)", L, perl=True
                ):
                    parts = regexec(rf"^{fn}\s*\(\s*([A-Za-z0-9._'\"]+)(.*)$", call, perl=True)
                    bare_is_name = fn != "requireNamespace" and not grepl(
                        char_only, parts[2], perl=True
                    )
                    add(arg_packages(parts[1], bare_is_name), fn, ln)
            # lapply(pkgs, library, character.only = TRUE) and the same with
            # sapply/vapply/walk/map and require; also through a function of its own,
            # lapply(pkgs, function(x) library(x, character.only = T)), which left tmap
            # uninstalled in 10.1002/pan3.10736 (metacheck #568)
            for call in regextract_all(
                r"\b(?:lapply|sapply|vapply|walk|map)\s*\(\s*([.A-Za-z][.A-Za-z0-9_]*)"
                r"\s*,\s*(?:FUN\s*=\s*)?" + _FN_WRAP + r"(library|require)\b",
                L,
                perl=True,
            ):
                parts = regexec(
                    r"\(\s*([.A-Za-z][.A-Za-z0-9_]*)\s*,\s*(?:FUN\s*=\s*)?"
                    + _FN_WRAP
                    + r"(library|require)\b",
                    call,
                    perl=True,
                )
                add(variables.get(parts[1], []), parts[2], ln)
            # pacman::p_load(a, b, c); with character.only = TRUE, or given as
            # p_load(char = pkgs), the arguments are variables instead
            pl = _cap(r"\bp_load\s*\(([^)]*)\)", L, 1)
            if pl is not None:
                args = strsplit(pl, r"\s*,\s*")
                chr_arg = grepl(r"^\s*char\s*=", args)
                args = [
                    str(sub(r"^\s*char\s*=\s*", "", a)) if c else a
                    for a, c in zip(args, chr_arg, strict=True)
                ]
                bare_is_name = not any(chr_arg) and not grepl(char_only, pl, perl=True)
                args = [a for a in args if not grepl("=", a)]  # other named arguments
                add([p for a in args for p in arg_packages(a, bare_is_name)], "p_load", ln)
            # install.packages("x") / renv::install("x") / BiocManager::install("x"):
            # the first argument, a name or c(...) of names; it is always
            # evaluated, so a bare word there is a variable
            for call in regextract_all(
                r"\binstall(?:\.packages)?\s*\(\s*(c\s*\([^)]*\)|[^,)]+)", L, perl=True
            ):
                arg = sub(r"^install(?:\.packages)?\s*\(\s*", "", call, perl=True)
                arg = sub(r"^c\s*\((.*)\)$", r"\1", trimws(arg))
                toks = strsplit(arg, r"\s*,\s*")
                add([p for t in toks for p in arg_packages(t, False)], "install", ln)
            # pkg::fun / pkg:::fun namespace-qualified calls (all on the line), but not the source
            # in groundhog.library("github::user/repo", date)
            ns = regextract_all(
                r"\b(?!(?:github|gitlab)::[\w.-]+/)([A-Za-z][A-Za-z0-9._]*):{2,3}", L, perl=True
            )
            if ns:
                add(sub(":{2,3}$", "", ns), "namespace", ln)
        else:
            fr = _cap(r"^\s*from\s+([A-Za-z0-9_.]+)\s+import\b", L, 1)
            if fr is not None:
                add([sub(r"\..*$", "", fr)], "import", ln)
            im = _cap(r"^\s*import\s+(.+)$", L, 1)
            if im is not None:
                parts = strsplit(im, r"\s*,\s*")
                top = sub(r"\..*$", "", sub(r"\s+as\s+.*$", "", trimws(parts)))
                add(top, "import", ln)

    # groundhog.library() loads its first argument: a name in quotes or not, a GitHub "user/repo",
    # c(...), which may run over several lines, or a variable holding these. In the form
    # groundhog.library("library(a); library(b)", date) the argument is a piece of code, which the
    # library() search above has read.
    if lang == "R":
        joined = "\n".join("NA" if s is None else s for s in lines)
        gpat = r"(?<![\w.])groundhog\.library\s*\(\s*(?:pkg\s*=\s*)?(?:c\s*\([^)]*\)|[^,)]+)"
        positions = gregexpr_all(gpat, joined, perl=True)
        calls = regextract_all(gpat, joined, perl=True)
        for (start, _length), call in zip(positions, calls, strict=True):
            arg = str(sub(r"(?s)^.*?\(\s*(?:pkg\s*=\s*)?", "", call, perl=True))
            arg = str(sub(r"(?s)^c\s*\((.*)\)$", r"\1", trimws(arg), perl=True))
            toks = [trimws(t) for t in arg.split(",")]
            pkgs: list[str] = []
            for tok in toks:
                pkgs.extend(variables[tok] if tok in variables else arg_packages(tok, True))
            ln = joined[: start - 1].count("\n") + 1
            add([sub(r"@.*$", "", sub(r"^.*/", "", p)) for p in pkgs], "groundhog", ln)

    if not pkgs_out:
        return _empty_packages()
    ok = grepl(r"^[A-Za-z][A-Za-z0-9._]*$", pkgs_out)
    out = pd.DataFrame(
        {
            "package": pd.Series(
                [p for p, k in zip(pkgs_out, ok, strict=True) if k], dtype="string"
            ),
            "source": pd.Series([s for s, k in zip(src_out, ok, strict=True) if k], dtype="string"),
            "line": pd.Series([n for n, k in zip(line_out, ok, strict=True) if k], dtype="Int64"),
        }
    )
    return out.drop_duplicates().reset_index(drop=True)


def code_packages(packages: Any) -> list[str]:
    """Sorted, distinct package names from comma-joined strings.

    Port of ``R/code_check.R::code_packages()``: *packages* is a sequence of
    strings like ``"dplyr, ggplot2"`` or a ``code_check`` table with a
    ``packages`` column.
    """
    if isinstance(packages, pd.DataFrame):
        packages = packages["packages"] if "packages" in packages.columns else None
    values = _as_chr(packages) or []
    values = [v for v in values if v is not None and v != ""]
    if not values:
        return []
    parts = strsplit(", ".join(cast(list[str], values)), r"\s*,\s*")
    return r_sorted(list(dict.fromkeys(parts)))


# ---------------------------------------------------------------------------
# environment pinning
# ---------------------------------------------------------------------------

_GROUNDHOG = r"groundhog(?:::)?\.?library\s*\((?:[^()]|\([^()]*\))*[\"'](\d{4}-\d{2}-\d{2})[\"']"
_CHECKPOINT = r"checkpoint(?:::checkpoint)?\s*\(\s*[\"'](\d{4}-\d{2}-\d{2})[\"']"
_R_VERSION = r"R version ([0-9]+\.[0-9]+\.[0-9]+)"


_PACKAGE_COLS = ("file_name", "package", "version", "source", "remote")


def _empty_renv_packages() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series([], dtype="string") for c in _PACKAGE_COLS})


def _empty_pin_dates() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series([], dtype="string") for c in ("mechanism", "date")})


# the packages that are part of R itself: rownames(installed.packages(priority = "base"))
_BASE_PACKAGES = frozenset(
    [
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
    ]
)


def _code_renv_remote(p: Any) -> str | None:
    """Where an ``renv.lock`` entry says a package came from GitHub: ``user/repo@ref``.

    Port of ``R/code_check.R::.code_renv_remote()``: with the exact commit when the file records
    it. ``None`` for any other package.
    """
    user = field(p, "RemoteUsername")
    repo = field(p, "RemoteRepo")
    if user is None or repo is None:
        return None
    ref = field(p, "RemoteSha")
    if ref is None:
        ref = field(p, "RemoteRef")
    out = f"{_r_as_character1(user)}/{_r_as_character1(repo)}"
    return out if ref is None else f"{out}@{_r_as_character1(ref)}"


def _code_session_packages(txt: Sequence[str | None]) -> pd.DataFrame:
    """The package versions a ``sessionInfo()`` or ``session_info()`` record lists.

    Port of ``R/code_check.R::.code_session_packages()``. Reads the two forms R writes.
    ``sessionInfo()`` lists ``name_version`` items under "other attached packages:" and "loaded
    via a namespace", on lines starting with an index such as ``[1]``.
    ``sessioninfo::session_info()`` and ``devtools::session_info()`` write a table with a header
    row starting ``package * version date``. Text that has neither heading gives no rows, so a
    README that mentions an R version is not taken for a list of packages. A ``## `` or ``> ``
    before each line, as in a knitted document, is ignored. Packages that are part of R itself
    (``stats``, ``compiler``) are left out. Returns the columns ``package``, ``version``,
    ``source`` (``GitHub``, ``Bioconductor``, ``Local``, ``Repository``, or ``NA`` for
    ``sessionInfo()``, which does not say) and ``remote`` (``user/repo@ref`` for GitHub, else
    ``NA``).
    """
    cols = ("package", "version", "source", "remote")
    lines = [None if t is None else str(sub(r"^\s*(?:#+|>)\s?", "", t, perl=True)) for t in txt]
    n = len(lines)
    rows: list[tuple[str, str, str | None, str | None]] = []

    item_pat = r"(?<![A-Za-z0-9._])([A-Za-z][A-Za-z0-9.]*)_([0-9]+(?:[.-][0-9]+)+)"
    heads = [
        i
        for i, t in enumerate(lines)
        if t is not None and grepl(r"^\s*(other attached packages|loaded via a namespace)", t)
    ]
    for h in heads:
        j = h + 1
        while j < n and lines[j] is not None and grepl(r"^\s*\[[0-9]+\]", lines[j]):
            for item in regextract_all(item_pat, lines[j], perl=True):
                pkg, _, ver = str(item).partition("_")
                rows.append((pkg, ver, None, None))
            j += 1

    # The flag columns (`P`, `V`, `!`) come before the name, and `*` after it.
    row_pat = (
        r"^\s*(?:[A-Z!]+\s+)?([A-Za-z][A-Za-z0-9.]*)\s+(?:\*\s+)?"
        r"([0-9]+(?:[.-][0-9]+)+)\s+\d{4}-\d{2}-\d{2}\s+"
        r"(?:\[[0-9?]+\]\s+)?(.*)$"
    )
    heads = [
        i
        for i, t in enumerate(lines)
        if t is not None
        and grepl(r"^\s*(?:!\s+)?package\s+\*?\s*(?:loadedversion|version)\s+date", t, perl=True)
    ]
    for h in heads:
        j = h + 1
        while j < n and lines[j] is not None and grepl(row_pat, lines[j], perl=True):
            p = regexec(row_pat, lines[j], perl=True)
            src = str(trimws(p[3]))
            is_gh = bool(grepl(r"^git ?hub", src, ignore_case=True))
            if is_gh:
                source = "GitHub"
            elif grepl(r"^bioc", src, ignore_case=True):
                source = "Bioconductor"
            elif grepl(r"^local", src, ignore_case=True):
                source = "Local"
            else:
                source = "Repository"
            remote = (
                str(sub(r"^.*\(([^)]+)\).*$", r"\1", src))
                if is_gh and grepl(r"\(([^)]+)\)", src)
                else None
            )
            rows.append((p[1], p[2], source, remote))
            j += 1

    frame = pd.DataFrame(rows, columns=list(cols)).astype("string")
    frame = frame[~frame["package"].isin(_BASE_PACKAGES)]
    frame = frame.drop_duplicates(subset=["package", "version"])
    return frame.reset_index(drop=True)


def _code_pin_dates(code_text: Sequence[str | None]) -> pd.DataFrame:
    """The dates one script passes to ``groundhog.library()`` and ``checkpoint()``.

    Port of ``R/code_check.R::.code_pin_dates()``, as a frame ``mechanism``, ``date``. The date is
    written in the call, or held in a variable the script assigns it to: ``day <- "2023-01-01"``
    followed by ``groundhog.library(pkgs, day)`` was not seen before. Comments are removed first,
    because the date decides which versions are installed.
    """
    import datetime

    from metacheck.statout.r_output import _repro_split_args

    if not code_text:
        return _empty_pin_dates()
    stripped = code_remove_comments(list(code_text), "R")
    if not stripped:
        return _empty_pin_dates()
    joined = "\n".join("NA" if s is None else s for s in stripped)
    variables = _code_char_vector_vars(stripped)
    date_pat = r"\d{4}-\d{2}-\d{2}"
    # An argument list, with up to two levels of brackets inside it: c("a", "b") and a piece of
    # code in a string, "library(a)".
    args_pat = r"((?:[^()]++|\((?:[^()]++|\([^()]*+\))*+\))*+)"

    def dates_of(call_pat: str, date_arg: str, position: int) -> list[str]:
        pat = rf"(?<![\w.]){call_pat}\s*\({args_pat}\)"
        found: list[str] = []
        for call in regextract_all(pat, joined, perl=True):
            args_txt = str(sub(r"(?s)^[^(]*\((.*)\)$", r"\1", call, perl=True))
            literal = regextract(rf"[\"']{date_pat}[\"']", args_txt, perl=True)
            if literal is not None:
                found.append(re.sub(r"[\"']", "", literal))
                continue
            args = [str(trimws(a["text"])) for a in _repro_split_args(args_txt)]
            name_pat = rf"^(?:{date_arg})\s*=\s*"
            named = [bool(grepl(name_pat, a, perl=True)) for a in args]
            if any(named):
                arg = str(sub(name_pat, "", args[named.index(True)], perl=True))
            elif len(args) >= position:
                arg = args[position - 1]
            else:
                arg = ""
            value = variables.get(arg)
            if value is not None and len(value) == 1:
                found.append(value[0])
        good = []
        for d in found:
            if re.fullmatch(date_pat, d):
                try:
                    datetime.datetime.strptime(d, "%Y-%m-%d")
                except ValueError:
                    continue
                good.append(d)
        return good

    gh = dates_of(r"groundhog\.library", "date", 2)
    cp = dates_of(r"(?:checkpoint::)?(?:create_|use_)?checkpoint", "snapshot_date|snapshotDate", 1)
    out = pd.DataFrame(
        {
            "mechanism": pd.Series(
                ["groundhog"] * len(gh) + ["checkpoint"] * len(cp), dtype="string"
            ),
            "date": pd.Series([*gh, *cp], dtype="string"),
        }
    )
    return out.drop_duplicates().reset_index(drop=True)


def _named_texts(code_text_list: Any) -> list[tuple[str | None, Any]]:
    """The scripts of *code_text_list* as ``(file name, lines)`` pairs.

    The list is a dict (its keys are the names when they are text), a list of
    ``(name, lines)`` pairs, or a list of line vectors (no names).
    """
    if code_text_list is None:
        return []
    if isinstance(code_text_list, Mapping):
        return [(k if isinstance(k, str) else None, v) for k, v in code_text_list.items()]
    out: list[tuple[str | None, Any]] = []
    for item in code_text_list:
        if isinstance(item, tuple) and len(item) == 2 and not isinstance(item[0], list):
            out.append((item[0], item[1]))
        else:
            out.append((None, item))
    return out


def _bind_packages(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    return pd.concat([a, b], ignore_index=True).astype("string")


def _code_env_pinned(version_pin: Mapping[str, Any], langs: Sequence[Any]) -> bool:
    """Whether the code's environment is written down, per language.

    Port of ``R/code_check.R::.code_env_pinned()``: R code needs one of the R mechanisms of
    :func:`_code_version_pin_check` (``pinned``), Python code a list of Python packages
    (``python_pinned``). Code in neither language is judged by the R mechanisms, as before Python
    was checked.
    """
    has_r = "R" in langs
    has_py = "Python" in langs
    if not has_r and not has_py:
        return version_pin.get("pinned") is True
    return (not has_r or version_pin.get("pinned") is True) and (
        not has_py or version_pin.get("python_pinned") is True
    )


def _json_scalar(x: Any) -> Any:
    if x is None:
        return None
    return _r_as_character1(x)


def _code_version_pin_check(
    all_files: pd.DataFrame | None,
    code_text_list: Any = None,
    max_file_size: float = 100,
    max_download_size: float = 500,
    cache: bool = False,
    skip_on_api_limit: bool = False,
    max_files_per_repo: float = math.inf,
) -> dict[str, Any]:
    """Detect whether a repository pinned its R/package versions.

    Port of ``R/code_check.R::.code_version_pin_check()``: ``renv.lock``
    files (R version and locked packages), ``sessionInfo()`` dumps (files
    named like ``session_info`` and READMEs), and ``groundhog``/
    ``checkpoint`` date-pinning calls in *code_text_list* (a list or dict of
    code line vectors, or a list of ``(file name, lines)`` pairs, whose names
    say where a ``sessionInfo()`` record pasted into a script was found).
    Returns a dict with ``pinned``, ``mechanisms``, ``r_versions``,
    ``renv_files``, ``renv_packages`` (a frame ``file_name``, ``package``,
    ``version``, ``source``, ``remote``), ``sessioninfo_files``,
    ``session_packages`` (the same columns: the package versions the
    ``sessionInfo()``/``session_info()`` records list), ``pin_dates`` (a frame
    ``mechanism`` (``groundhog`` or ``checkpoint``), ``date``),
    ``python_files`` (the file names of Python package lists:
    ``requirements*.txt``, ``environment.yml``, ``Pipfile(.lock)``,
    ``poetry.lock``, ``uv.lock``, ``pyproject.toml``), ``python_pinned``
    (whether any was found; ``pinned`` stays about R only) and
    ``file_location`` (a Series of the candidate files' local paths, indexed by
    file name).
    """
    return _version_pin_scan(
        all_files,
        code_text_list,
        max_file_size=max_file_size,
        max_download_size=max_download_size,
        cache=cache,
        skip_on_api_limit=skip_on_api_limit,
        max_files_per_repo=max_files_per_repo,
    )[0]


def _version_pin_scan(
    all_files: pd.DataFrame | None,
    code_text_list: Any = None,
    max_file_size: float = 100,
    max_download_size: float = 500,
    cache: bool = False,
    skip_on_api_limit: bool = False,
    max_files_per_repo: float = math.inf,
) -> tuple[dict[str, Any], dict[int, Any]]:
    """:func:`_code_version_pin_check` plus where each candidate file lies.

    The second value maps the row positions of *all_files* whose file was
    looked for to its local path. ``code_check()`` copies these back by row,
    where R matches them by file name, so of two files of one name (two
    READMEs) both locations went to the first row: that row then held the
    other file, and a paper could be credited with another paper's
    ``sessionInfo()`` (UPSTREAM_ISSUES U89).
    """
    out: dict[str, Any] = {
        "pinned": False,
        "mechanisms": [],
        "r_versions": [],
        "renv_files": [],
        "renv_packages": _empty_renv_packages(),
        "sessioninfo_files": [],
        "session_packages": _empty_renv_packages(),
        "pin_dates": _empty_pin_dates(),
        "python_files": [],
        "python_pinned": False,
        "file_location": pd.Series([], dtype=object),
    }
    positions: dict[int, Any] = {}
    if all_files is None or len(all_files) == 0:
        return out, positions
    all_files = all_files.reset_index(drop=True)
    names = _col(all_files, "file_name")
    base_nm = [
        None if is_missing(n) else os.path.basename(str(n).replace("\\", "/").rstrip("/"))
        for n in names
    ]
    dl_args = {
        "max_file_size": max_file_size,
        "max_download_size": max_download_size,
        "cache": cache,
        "max_files_per_repo": max_files_per_repo,
        "skip_on_api_limit": skip_on_api_limit,
    }
    locations: list[tuple[Any, Any]] = []

    def fetch(mask: list[bool]) -> pd.DataFrame:
        rows = all_files.loc[mask].reset_index(drop=True)
        if "file_location" not in rows.columns:
            # setNames(NULL, file_name): "attempt to set an attribute on NULL"
            raise TypeError("attempt to set an attribute on NULL")
        need = [
            e and h
            for e, h in zip(
                _empty_loc(_col(rows, "file_location")),
                _has_value(_col(rows, "file_url")),
                strict=True,
            )
        ]
        if any(need):
            rows = _set_locations(rows, need, _download(rows.loc[need], all_files, **dl_args))
        locs = _col(rows, "file_location")
        locations.extend(zip(_col(rows, "file_name"), locs, strict=True))
        positions.update(zip([i for i, m in enumerate(mask) if m], locs, strict=True))
        return rows

    # renv.lock
    is_renv = [b == "renv.lock" for b in base_nm]
    if any(is_renv):
        rows = fetch(is_renv)
        frames = []
        for fname, loc in zip(_col(rows, "file_name"), _col(rows, "file_location"), strict=True):
            if is_missing(loc) or not str(loc) or not os.path.exists(str(loc)):
                continue
            try:
                lock = _json_load(Path(str(loc)).read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                lock = None
            if lock is None:
                continue
            out["renv_files"].append(fname)
            rv = field(lock, "R", "Version")
            if rv is not None:
                out["r_versions"].extend(r_as_character(rv))
            pkgs = field(lock, "Packages")
            if pkgs is not None and _r_length(pkgs) > 0:
                recs = [
                    {
                        "file_name": fname,
                        "package": _json_scalar(field(p, "Package")),
                        "version": _json_scalar(field(p, "Version")),
                        "source": _json_scalar(field(p, "Source")),
                        "remote": _code_renv_remote(p),
                    }
                    for p in _r_elements(pkgs)
                ]
                frames.append(pd.DataFrame(recs, columns=list(_PACKAGE_COLS)))
        if frames:
            out["renv_packages"] = pd.concat(
                [out["renv_packages"], *frames], ignore_index=True
            ).astype("string")
        if out["renv_files"]:
            out["mechanisms"].append("renv.lock")

    # sessionInfo() dumps
    is_si = grepl(r"session[_-]?info", base_nm, ignore_case=True)
    # Names that START with a README name only ("read.me.txt" too, metacheck #487), not every name
    # a README is: each candidate may be downloaded.
    is_readme = grepl(r"^read[ _.-]?me", base_nm, ignore_case=True)
    # revdep/ holds a package's tests of other packages; its README names the R version of those
    # tests, not of the analysis (found in real repositories)
    in_revdep = _in_folder(all_files, "revdep")
    si_mask = [(a or b) and not r for a, b, r in zip(is_si, is_readme, in_revdep, strict=True)]
    if any(si_mask):
        rows = fetch(si_mask)
        for fname, loc in zip(_col(rows, "file_name"), _col(rows, "file_location"), strict=True):
            if is_missing(loc) or not str(loc) or not os.path.exists(str(loc)):
                continue
            txt = _try_code_read(str(loc))
            if not txt:
                continue
            txt = [piece for t in txt for piece in strsplit(t, "\n", fixed=True)]
            hit = next((t for t in txt if grepl(_R_VERSION, t, perl=True)), None)
            if hit is not None:
                version = regextract(_R_VERSION, hit, perl=True)
                out["r_versions"].append(sub("^R version ", "", version))
            si_pkgs = _code_session_packages(txt)
            if len(si_pkgs) > 0:
                si_pkgs.insert(0, "file_name", pd.Series([fname] * len(si_pkgs), dtype="string"))
                out["session_packages"] = _bind_packages(out["session_packages"], si_pkgs)
            if hit is not None or len(si_pkgs) > 0:
                out["sessioninfo_files"].append(fname)

    # A record pasted into a script, usually as comments. Only with a list of packages: a comment
    # that names an R version does not say what was used.
    texts = _named_texts(code_text_list)
    for fn, lines_i in texts:
        txt = [
            piece
            for t in (_as_chr(lines_i) or [])
            for piece in strsplit(t or "NA", "\n", fixed=True)
        ]
        if not txt:
            continue  # an empty script
        si_pkgs = _code_session_packages(txt)
        if len(si_pkgs) == 0:
            continue
        si_pkgs.insert(0, "file_name", pd.Series([fn] * len(si_pkgs), dtype="string"))
        out["session_packages"] = _bind_packages(out["session_packages"], si_pkgs)
        out["sessioninfo_files"].append(fn)
        # comments, or a record without comment signs; not code such as print("R version 4.2.0")
        rv_hit = next(
            (
                t
                for t in txt
                if grepl(r"^\s*(?:#.*|version\s+)?R version [0-9]+\.[0-9]+\.[0-9]+", t, perl=True)
            ),
            None,
        )
        if rv_hit is not None:
            version = regextract(r"R version [0-9]+\.[0-9]+\.[0-9]+", rv_hit, perl=True)
            out["r_versions"].append(sub("^R version ", "", version))
    if out["sessioninfo_files"]:
        out["mechanisms"].append("sessionInfo")

    # groundhog / checkpoint date-pinning calls: the pinning CALL itself, with a date-shaped
    # argument -- library(groundhog) / library(checkpoint) alone is not evidence anything was
    # pinned
    dates = [_code_pin_dates(_as_chr(lines_i) or []) for _, lines_i in texts]
    pin_dates = pd.concat([out["pin_dates"], *dates], ignore_index=True).astype("string")
    out["pin_dates"] = pin_dates.drop_duplicates().reset_index(drop=True)
    mechs = set(out["pin_dates"]["mechanism"].tolist())
    if "groundhog" in mechs:
        out["mechanisms"].append("groundhog")
    if "checkpoint" in mechs:
        out["mechanisms"].append("checkpoint")

    # Python package lists, by file name only: the files pip, conda, pipenv, poetry and uv write
    py_re = (
        r"^(requirements|constraints)([_.-][a-z0-9]+)*\.(txt|in)$|"
        r"^pipfile(\.lock)?$|^poetry\.lock$|^uv\.lock$|"
        r"^environment\.ya?ml$|^conda[_.-]?lock\.ya?ml$|^pyproject\.toml$"
    )
    out["python_files"] = [
        n for n, h in zip(names, grepl(py_re, base_nm, ignore_case=True), strict=True) if h
    ]
    out["python_pinned"] = len(out["python_files"]) > 0

    out["mechanisms"] = list(dict.fromkeys(out["mechanisms"]))
    out["r_versions"] = list(dict.fromkeys(out["r_versions"]))
    out["pinned"] = len(out["mechanisms"]) > 0
    out["file_location"] = pd.Series(
        [loc for _, loc in locations], index=[n for n, _ in locations], dtype=object
    )
    return out, positions


def _code_open_calls(
    code_text: Sequence[str | None], call_regex: str, max_lines: int = 20
) -> tuple[list[str], list[int]]:
    """Calls that go on over the next lines.

    Port of ``R/code_check.R::.code_open_calls()``: a call matched by *call_regex* (ending in the
    call's opening parenthesis) whose closing parenthesis is on a later line, as in ``read.csv(``
    with ``"./data.csv")`` on the next line: the line-by-line search for file names saw the call
    without its file name, and the file name without its call (metacheck #568). Quotes and
    brackets are counted; a call not closed within *max_lines* lines is left out. Returns the text
    of each such call from its name to its closing parenthesis, and the (1-based) numbers of the
    lines after the first that the calls take.
    """
    calls: list[str] = []
    taken: list[int] = []
    text = ["" if t is None else t for t in code_text]
    n = len(text)
    if n < 2:
        return calls, taken
    hits = [i for i, h in enumerate(grepl(call_regex, text, perl=True), start=1) if h]
    for i in (h for h in hits if h < n):
        starts = gregexpr_all(call_regex, text[i - 1], perl=True)
        for start, length in starts:
            first = start + length - 1  # the "("
            depth = 0
            in_str: str | None = None
            closed = False
            j = i
            pieces: list[str] = []
            while j <= min(n, i + max_lines - 1) and not closed:
                line = text[j - 1]
                frm = first if j == i else 1
                chars = line[frm - 1 :]
                p = 0
                while p < len(chars):
                    p += 1
                    ch = chars[p - 1]
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
                if j == i:
                    pieces.append(line[start - 1 :])
                elif closed:
                    pieces.append(line[:p])
                else:
                    pieces.append(line)
                if not closed:
                    j += 1
            if closed and j > i:
                calls.append(" ".join(pieces))
                taken.extend(range(i + 1, j + 1))
    return calls, list(dict.fromkeys(taken))


# ---------------------------------------------------------------------------
# file references
# ---------------------------------------------------------------------------


def _alternation(parts: Sequence[str], suffix: str) -> str:
    return r"\b(" + "|".join(parts) + ")" + suffix


_LOAD_REGEX = {
    "R": _alternation(
        [
            r"read[\._][A-Za-z\._0-9]+",
            "import(_list)?",
            "fread",
            "readRDS",
            "load",
            "readLines",
            "fromJSON",
            "readtext",
            "vroom",
            "source",
        ],
        r"\s*\(",
    ),
    "Python": _alternation(
        ["read_[A-Za-z_0-9]+", "loadtxt", "genfromtxt", "loadmat", "load", "open", "read_table"],
        r"\s*\(",
    ),
    "SAS": r"\b(proc\s+import|infile|datafile\s*=|libname)\b",
    "SPSS": _alternation(
        [r"\/?FILE", r"FILE\s+HANDLE\s+.+\s+\/NAME", r"GET\s+SAS\s+DATA"], r"\s*="
    ),
    "Stata": r"\b(use|import\s+delimited|insheet|merge|append)\b",
    "Mplus": r"\bFILE\s*=",
    "MATLAB": _alternation(
        [
            "load",
            "csvread",
            "dlmread",
            "readtable",
            "readmatrix",
            "readcell",
            "xlsread",
            "importdata",
            "fopen",
            "run",
        ],
        r"\s*\(",
    ),
}
#: The calls that write a file, by language (``code_file_refs(include_writes = TRUE)``).
#: Python's are named one by one: metacheck's generic ``to_[A-Za-z_0-9]+`` also takes
#: ``pd.to_datetime("2020.01")`` or ``to_numeric("1.5")`` for a write of a file.
_PY_WRITE_FNS = [
    "to_(?:csv|excel|parquet|pickle|json|feather|hdf|stata|html|latex|markdown|xml|orc|netcdf|file)",
    "write_[A-Za-z0-9_]+",
    "savez(?:_compressed)?",
    "savetxt",
    "savefig",
    "save",
    "dump",
]
_WRITE_REGEX = {
    "R": _alternation(
        [
            r"write[\._][A-Za-z\._0-9]*",
            "saveRDS",
            "save",
            r"save\.image",
            "ggsave",
            "export",
            "fwrite",
        ],
        r"\s*\(",
    ),
    "Python": _alternation(_PY_WRITE_FNS, r"\s*\("),
}
_QUOTED_FILE = "(['\"])(?!\\.\\1)[^'\"]+\\.[A-Za-z0-9]{1,8}(?:\\.[A-Za-z0-9]{1,8})*\\1"
_FORMAT_SPEC = r"^%[-+ 0#]*[0-9]*(\.[0-9]+)?[diouxXeEfgGaAscp]$"
_UNQUOTED = {
    "SAS": [(r"infile\s+([^\s;]+)", 1), (r"datafile\s*=\s*([^\s;]+)", 1)],
    "SPSS": [(r"GET\s+DATA.*?/FILE\s*=\s*([^\s]+)", 1)],
    "Stata": [
        (r"^\s*use\s+([^,\s]+)", 1),
        (r"import\s+delimited\s+using\s+([^,\s]+)", 1),
        (r"insheet\s+using\s+([^,\s]+)", 1),
        (r"merge\b.*?using\s+([^,\s]+)", 1),
        (r"append\b.*?using\s+([^,\s]+)", 1),
    ],
}


def _code_blank_write_opens(
    code_text: Sequence[str | None], lang: str, mode_regex: str | None = None
) -> list[str]:
    """Calls that open a file for writing, blanked out.

    Port of ``R/code_check.R::.code_blank_write_opens()``. Python's ``open()`` and MATLAB's
    ``fopen()`` read a file or write one, decided only by their mode argument.
    :func:`code_file_refs` took the file name in every such call as an input, so
    ``open("out.txt", "w")`` was reported as a missing file when the script itself creates it.
    This replaces the text of each call that writes with spaces, so its file name is not found;
    the lines and their number stay as they were. A call over several lines is judged as a whole.

    The mode is the second argument without a name, or (Python) ``mode =``. The default modes are
    those that create the file: Python's ``w``, ``a`` and ``x``, MATLAB's ``w`` and ``a``. ``r+``
    changes a file that must already exist, so it is left as an input; the reproducibility check's
    ``_py_open_writes()`` passes its own *mode_regex*, which counts ``r+``.

    *code_text* is the lines of one file, comments removed; only ``"Python"`` and ``"MATLAB"`` are
    changed. *mode_regex* is matched (PCRE) against the mode argument with its quotes. Returns the
    lines, with each writing call's text replaced by spaces.
    """
    from metacheck.statout.r_output import _repro_split_args

    lines = ["NA" if t is None else t for t in code_text]
    call_regex = {"Python": r"\bopen\s*\(", "MATLAB": r"\bfopen\s*\("}.get(lang)
    if call_regex is None or not lines:
        return lines
    if mode_regex is None:
        mode_regex = {
            "Python": r"^(['\"])(?=[^'\"]*[wax])[rwaxbt+]+\1$",
            "MATLAB": r"^(['\"])[wWaA][+tb]*\1$",
        }[lang]
    joined = "\n".join(lines)
    starts = list(compile_r(call_regex, perl=True).finditer(joined))
    if not starts:
        return lines
    blank = [False] * len(joined)
    named_rx = compile_r(r"^[A-Za-z_][A-Za-z0-9_]*\s*=(?!=)", perl=True)
    mode_arg_rx = compile_r(r"^mode\s*=")
    mode_rx = compile_r(mode_regex, perl=True)
    for m in starts:
        first = m.end()  # the first character after "("
        # the call's closing parenthesis, skipping quoted text and nested brackets
        depth = 1
        in_str: str | None = None
        end: int | None = None
        i = first
        while i < len(joined):
            ch = joined[i]
            if in_str is not None:
                if ch == "\\":
                    i += 1
                elif ch == in_str:
                    in_str = None
            elif ch in ("'", '"'):
                in_str = ch
            elif ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
            i += 1
        if end is None or end == first:
            continue
        args = [str(trimws(a["text"])) for a in _repro_split_args(joined[first:end])]
        named = [named_rx.search(a) is not None for a in args]
        mode_args = [a for a, nm in zip(args, named, strict=True) if nm and mode_arg_rx.search(a)]
        if mode_args:
            mode: str | None = re.sub(r"^mode\s*=\s*", "", mode_args[0])
        else:
            positional = [a for a, nm in zip(args, named, strict=True) if not nm]
            mode = positional[1] if len(positional) > 1 else None
        if mode is not None and mode_rx.search(mode):
            for k in range(m.start(), end + 1):
                blank[k] = True
    if not any(blank):
        return lines
    out = "".join(" " if b and c != "\n" else c for b, c in zip(blank, joined, strict=True))
    return out.split("\n")


_MPLUS_HEADER = (
    r"^\s*(TITLE|DATA(\s+[A-Za-z]+)*|VARIABLE|DEFINE|ANALYSIS|"
    r"MODEL(\s+[A-Za-z]+)*|OUTPUT|SAVEDATA|PLOT|MONTECARLO)\s*:"
)


def _code_blank_mplus_savedata(code_text: Sequence[str | None]) -> list[str]:
    """Mplus's SAVEDATA command, blanked out.

    Port of ``R/code_check.R::.code_blank_mplus_savedata()``. Mplus names its input with
    ``FILE =`` under ``DATA:``, and the file it writes with ``FILE =`` under ``SAVEDATA:``.
    :func:`code_file_refs` took both as inputs, so a file of factor scores the model writes was
    reported as missing. This empties every line from ``SAVEDATA:`` up to the next command.
    """
    lines = ["NA" if t is None else t for t in code_text]
    header = compile_r(_MPLUS_HEADER, ignore_case=True, perl=True)
    in_savedata = False
    out = []
    for line in lines:
        m = header.search(line)
        # each line belongs to the last command named on or above it
        if m is not None:
            in_savedata = str(trimws(m.group(1))).upper() == "SAVEDATA"
        out.append("" if in_savedata else line)
    return out


def code_file_refs(
    code_text: str | Sequence[str],
    lang: str | Sequence[str] = LANGS,
    include_writes: bool = False,
) -> list[str]:
    """The files that (comment-free) code reads.

    Port of ``R/code_check.R::code_file_refs()``: quoted file names (with an
    extension) on lines calling a reader (``read.csv``, ``read_*``,
    ``import()``, ``fread``, ``load``, ``source``, pandas readers, ``use``,
    ``GET FILE=``, ...), plus bare-word references for SAS/SPSS/Stata.
    Format specifiers such as ``"%.2f"`` are not file names. With
    *include_writes* (R and Python) written files count too.
    """
    lang = _match_arg(lang)
    lines: Sequence[str | None] = code_remove_comments(code_text, lang)
    # Python's open() and MATLAB's fopen() are reading calls below, but with a writing mode they
    # create the file, and Mplus's SAVEDATA names a file the model writes: outputs, left out
    # unless writes are asked for
    if include_writes is not True:
        lines = _code_blank_write_opens(lines, lang)
        if lang == "Mplus":
            lines = _code_blank_mplus_savedata(lines)
    # SAS, SPSS and Mplus ignore capitals: PROC IMPORT DATAFILE= and proc import datafile=,
    # GET FILE= and get file=, FILE = and file = are the same command
    no_case = "(?i)" if lang in ("SAS", "SPSS", "Mplus") else ""
    pattern = no_case + _LOAD_REGEX[lang]
    if include_writes is True and lang in _WRITE_REGEX:
        pattern = f"{pattern}|{_WRITE_REGEX[lang]}"
    load_hits = grepl(pattern, lines, perl=True)
    load_lines = [s for s, h in zip(lines, load_hits, strict=True) if h]
    # A call whose file name is on a line after it, as in `read.csv(` and then `"./data.csv")`, is
    # searched as a whole too (metacheck #568)
    if lang in ("R", "Python"):
        load_lines = [*load_lines, *_code_open_calls(lines, pattern)[0]]
    found = [m for ms in regextract_all(_QUOTED_FILE, load_lines, perl=True) for m in ms]
    loaded = list(gsub("^['\"]|['\"]$", "", found))
    spec = grepl(_FORMAT_SPEC, loaded, perl=True)
    loaded = [f for f, s in zip(loaded, spec, strict=True) if not s]

    quoted = grepl(_QUOTED_FILE, load_lines, perl=True)
    unquoted = [s for s, q in zip(load_lines, quoted, strict=True) if not q]
    extra: list[str] = []
    for regex, group in _UNQUOTED.get(lang, []):
        extra.extend(
            m[group] for m in regexec(no_case + regex, unquoted, perl=True) if len(m) >= group + 1
        )
    extra = list(gsub("[\"']$", "", gsub("^[\"']", "", extra)))
    return list(dict.fromkeys([*loaded, *extra]))


# kept for callers that want the positions R's gregexpr() gives
_gregexpr_all = gregexpr_all
