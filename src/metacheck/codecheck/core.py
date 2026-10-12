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
from collections.abc import Iterable, Sequence
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
    from metacheck import http

    resp = http.request("GET", url)
    if resp is None or resp.status_code >= 400:
        status = "no response" if resp is None else f"HTTP error {resp.status_code}"
        raise OSError(f"cannot open URL '{url}': {status}")
    return bytes(resp.content)


def code_read(file_path: str | os.PathLike[str]) -> list[str]:
    """Read code from a file or URL, one element per line.

    Port of ``R/code_check.R::code_read()``. The bytes are decoded by
    :func:`metacheck.codecheck._decode.decode_lines` (a byte order mark, UTF-8,
    else chardet's guess or Windows-1252; D74), not by R's readr/vroom
    pipeline; bytes that fit no encoding are shown as ``<xx>``. A 0-byte file
    gives an empty list.
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
    if _URL.search(path):
        return decode_lines(_fetch_url(path))
    local = Path(path).expanduser()
    if not local.exists():
        raise FileNotFoundError(f"'{path}' does not exist.")
    if local.is_file() and local.stat().st_size == 0:
        return []
    if local.is_dir():
        raise IsADirectoryError(f"Cannot read file '{path}': it is a directory")
    return decode_lines(local.read_bytes())


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


def _ipynb_lang(file_name: Any) -> str:
    """The kernel language of a Jupyter notebook: ``"R"`` or ``"Python"``.

    Port of ``R/code_check.R::.ipynb_lang()``.
    """
    if not isinstance(file_name, str) or not os.path.exists(file_name):
        return "Python"
    txt = _try_code_read(file_name)
    if not txt:
        return "Python"
    nb = _json_load("\n".join(txt))
    if nb is None:
        return "Python"
    # a notebook that is not a JSON object (or whose metadata is malformed)
    # falls back to the default; R's `$` errors escape (UPSTREAM_ISSUES U68)
    meta = field(nb, "metadata")
    lang = field(meta, "kernelspec", "language")
    if lang is None:
        lang = field(meta, "language_info", "name")
    lang_s = (_first_chr(lang) or "").lower()
    return "R" if lang_s in ("r", "ir") else "Python"


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


def _qmd_lang(file_name: Any) -> str:
    """The engine language of a Quarto document: ``"R"`` or ``"Python"``.

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
        if yaml_lang.startswith("py"):
            return "Python"
        if yaml_lang in ("r", "ir"):
            return "R"

    chunk = next((t for t in txt if grepl(r"^```+\s*\{[a-zA-Z]+", t, perl=True)), None)
    if chunk is not None:
        lang = regextract(r"(?<=\{)[a-zA-Z]+", chunk, perl=True)
        if lang is not None and lang.lower().startswith("py"):
            return "Python"
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
    ``"SPSS"``, ``"SAS"``, ``"Stata"``, ``"Mplus"``, ``"MATLAB"`` (or ``"JASP"``
    / ``"jamovi"``), or ``None`` for other files. A notebook's (``.ipynb``) or
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
    file_url = _col(all_files, "file_url")
    archive_url = _col(all_files, "archive_url")
    archive_member = _col(all_files, "archive_member")
    has_target = [
        u or (a and not is_missing(m))
        for u, a, m in zip(
            _has_value(file_url), _has_value(archive_url), archive_member, strict=True
        )
    ]
    need = [
        c and e and t
        for c, e, t in zip(
            is_candidate, _empty_loc(_col(all_files, "file_location")), has_target, strict=True
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
    is_html = grepl(r"\.html?$", _col(all_files, "file_name"), ignore_case=True)
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
) -> pd.DataFrame:
    """Fetch the code members of every unexpanded remote ``.zip`` row.

    Port of ``R/code_check.R::.code_expand_zip()``: the archive is peeked
    with range requests and only members :func:`code_lang` recognises are
    fetched, one new row each (the ``.zip`` row itself is kept). Under
    *skip_on_api_limit* a rate-limited host is skipped rather than waited
    out; both *skip_on_api_limit* and *cache* go on to the peek and to the
    member fetches.
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
    from metacheck.archives.zip_peek import _zip_fetch_members, zip_peek

    new_rows: list[pd.DataFrame] = []
    for i, z in enumerate(is_zip):
        if not z:
            continue
        url = urls[i]
        try:
            peek = zip_peek(url, cache=cache, skip_on_api_limit=skip_on_api_limit)
        except Exception:
            peek = None
        if peek is None or len(peek) == 0:
            continue
        members = peek["name"].tolist()
        is_code = [lang is not None for lang in _as_lang_list(code_lang(members))]
        if not any(is_code):
            continue
        if "file_path" in all_files.columns:
            fp = all_files["file_path"].iloc[i]
            base = "NA" if is_missing(fp) else str(fp)
        else:
            base = str(names[i])
        # .repo_cache_path() is outside R's tryCatch(); the fetch is inside
        dest = _repo_cache_path(_col(all_files, "repo_url")[i], f"{base}.contents")
        try:
            fetched = _zip_fetch_members(
                url,
                names=[m for m, c in zip(members, is_code, strict=True) if c],
                dest=dest,
                cache=cache,
                skip_on_api_limit=skip_on_api_limit,
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

    out = extract_r(_text_arg(file_path, text), documentation=int(documentation))
    if save_path is None:
        return out
    _write_lines(out, save_path)
    return os.fspath(save_path)


def code_extract_py(
    file_path: str | os.PathLike[str] | None = None,
    save_path: str | os.PathLike[str] | None = None,
    text: str | Sequence[str] | None = None,
) -> Any:
    """Extract the source of a Jupyter notebook's code cells.

    Port of ``R/code_check.R::code_extract_py()``: code cells in document
    order, each followed by a blank line; markdown/raw cells, IPython magics
    (``%...``) and shell escapes (``!...``) are dropped. Works for any kernel
    (see :func:`code_lang`). Returns the lines, or *save_path* after writing.
    """
    lines = _text_arg(file_path, text)
    nb = _json_load("\n".join("NA" if t is None else t for t in lines))
    cells = field(nb, "cells")
    out: list[str] = []
    if cells is not None and _r_length(cells) > 0:
        for cl in _r_elements(cells):
            if field(cl, "cell_type") != "code":
                continue
            src = field(cl, "source")
            if src is None or _r_length(src) == 0:
                continue
            joined = "".join(r_unlist_chr(src))
            cell_lines = [s for piece in strsplit([joined], "\n") for s in piece]
            keep = grepl(r"^\s*[%!]", cell_lines)
            cell_lines = [s for s, k in zip(cell_lines, keep, strict=True) if not k]
            if not cell_lines:
                continue
            out.extend(cell_lines)
            out.append("")
    if save_path is None:
        return out
    _write_lines(out, save_path)
    return os.fspath(save_path)


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
            for fn in ("library", "require", "requireNamespace"):
                for call in regextract_all(
                    rf"\b{fn}\s*\(\s*([A-Za-z0-9._'\"]+)([^)]*)", L, perl=True
                ):
                    parts = regexec(rf"^{fn}\s*\(\s*([A-Za-z0-9._'\"]+)(.*)$", call, perl=True)
                    bare_is_name = fn != "requireNamespace" and not grepl(
                        char_only, parts[2], perl=True
                    )
                    add(arg_packages(parts[1], bare_is_name), fn, ln)
            # lapply(pkgs, library, character.only = TRUE) and the same with
            # sapply/vapply/walk/map and require
            for call in regextract_all(
                r"\b(?:lapply|sapply|vapply|walk|map)\s*\(\s*([.A-Za-z][.A-Za-z0-9_]*)"
                r"\s*,\s*(?:FUN\s*=\s*)?(library|require)\b",
                L,
                perl=True,
            ):
                parts = regexec(
                    r"\(\s*([.A-Za-z][.A-Za-z0-9_]*)\s*,\s*(?:FUN\s*=\s*)?(library|require)\b",
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
            ns = regextract_all(r"\b([A-Za-z][A-Za-z0-9._]*):{2,3}", L, perl=True)
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


def _empty_renv_packages() -> pd.DataFrame:
    return pd.DataFrame(
        {c: pd.Series([], dtype="string") for c in ("file_name", "package", "version", "source")}
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
    code line vectors). Returns a dict with ``pinned``, ``mechanisms``,
    ``r_versions``, ``renv_files``, ``renv_packages`` (a frame),
    ``sessioninfo_files`` and ``file_location`` (a Series of the candidate
    files' local paths, indexed by file name).
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
                    }
                    for p in _r_elements(pkgs)
                ]
                frames.append(
                    pd.DataFrame(recs, columns=["file_name", "package", "version", "source"])
                )
        if frames:
            out["renv_packages"] = pd.concat(
                [out["renv_packages"], *frames], ignore_index=True
            ).astype("string")
        if out["renv_files"]:
            out["mechanisms"].append("renv.lock")

    # sessionInfo() dumps
    is_si = grepl(r"session[_-]?info", base_nm, ignore_case=True)
    is_readme = grepl(r"^readme", base_nm, ignore_case=True)
    si_mask = [a or b for a, b in zip(is_si, is_readme, strict=True)]
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
                out["sessioninfo_files"].append(fname)
                out["r_versions"].append(sub("^R version ", "", version))
        if out["sessioninfo_files"]:
            out["mechanisms"].append("sessionInfo")

    # groundhog / checkpoint
    texts = code_text_list.values() if isinstance(code_text_list, dict) else (code_text_list or [])
    has_gh = has_cp = False
    for txt in texts:
        values = _as_chr(txt) or []
        if not values:
            continue
        joined = "\n".join("NA" if v is None else v for v in values)
        if not has_gh and grepl(_GROUNDHOG, joined, perl=True):
            has_gh = True
        if not has_cp and grepl(_CHECKPOINT, joined, perl=True):
            has_cp = True
        if has_gh and has_cp:
            break
    if has_gh:
        out["mechanisms"].append("groundhog")
    if has_cp:
        out["mechanisms"].append("checkpoint")

    out["mechanisms"] = list(dict.fromkeys(out["mechanisms"]))
    out["r_versions"] = list(dict.fromkeys(out["r_versions"]))
    out["pinned"] = len(out["mechanisms"]) > 0
    out["file_location"] = pd.Series(
        [loc for _, loc in locations], index=[n for n, _ in locations], dtype=object
    )
    return out, positions


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
_WRITE_REGEX = _alternation(
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
)
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
    *include_writes* (R only) written files count too.
    """
    lang = _match_arg(lang)
    lines = code_remove_comments(code_text, lang)
    pattern = _LOAD_REGEX[lang]
    if include_writes is True and lang == "R":
        pattern = f"{pattern}|{_WRITE_REGEX}"
    load_hits = grepl(pattern, lines, perl=True)
    load_lines = [s for s, h in zip(lines, load_hits, strict=True) if h]
    found = [m for ms in regextract_all(_QUOTED_FILE, load_lines, perl=True) for m in ms]
    loaded = list(gsub("^['\"]|['\"]$", "", found))
    spec = grepl(_FORMAT_SPEC, loaded, perl=True)
    loaded = [f for f, s in zip(loaded, spec, strict=True) if not s]

    quoted = grepl(_QUOTED_FILE, load_lines, perl=True)
    unquoted = [s for s, q in zip(load_lines, quoted, strict=True) if not q]
    extra: list[str] = []
    for regex, group in _UNQUOTED.get(lang, []):
        extra.extend(m[group] for m in regexec(regex, unquoted, perl=True) if len(m) >= group + 1)
    extra = list(gsub("[\"']$", "", gsub("^[\"']", "", extra)))
    return list(dict.fromkeys([*loaded, *extra]))


# kept for callers that want the positions R's gregexpr() gives
_gregexpr_all = gregexpr_all
