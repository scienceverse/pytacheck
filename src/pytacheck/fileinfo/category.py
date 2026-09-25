"""File categories and types (port of ``R/file_category.R``).

* :func:`file_category` -- ``readme`` / ``codebook`` / ``data`` / ``code`` (or
  missing) for each file of a repository listing, from its name and file type;
* :func:`filetype` -- the coarse type(s) of a file name's last extension, from
  ``metacheck::file_types`` (:func:`pytacheck.fileinfo.types.file_types`).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import pandas as pd

from pytacheck._r import grepl

__all__ = ["file_category", "filetype"]

# -- patterns (R string literals translated to regexes) ------------------------

_README_RX = "read[ _-]?me"
# The five `is_codebook` name branches of file_category(), as one alternation
# (grepl(a) | grepl(b) == grepl("a|b")). Every branch is a two-word compound;
# see the long comment in R/file_category.R for why.
_CODEBOOK_RX = (
    "code[ _.-]?book"
    "|data[ _.-]?dict"
    "|var(iable)?[ _.-]?(key|list|descript)"
    "|data[ _.-]?legend"
    "|coding[ _.-]?(manual|scheme|sheet)"
)

_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


# -- helpers -------------------------------------------------------------------


def _is_na(x: Any) -> bool:
    if x is None or x is pd.NA or x is pd.NaT:
        return True
    try:
        return bool(x != x)  # NaN
    except (TypeError, ValueError):
        return False


def _chr(x: Any) -> str | None:
    """One element as R character (``NA`` -> ``None``)."""
    if isinstance(x, list | tuple) and len(x) == 1:  # a length-1 list-column cell
        x = x[0]
    if _is_na(x):
        return None
    if isinstance(x, str):
        return x
    from pytacheck._r import as_character

    return as_character(x)


def _chr_list(x: Any) -> list[str | None]:
    """An R atomic vector argument as a list of optional strings."""
    if x is None:
        return []
    if isinstance(x, str):
        return [x]
    if isinstance(x, pd.Series | pd.Index):
        return [_chr(v) for v in x.tolist()]
    if isinstance(x, Iterable) and not isinstance(x, Mapping):
        return [_chr(v) for v in x]
    return [_chr(x)]


def _dollar(df: pd.DataFrame, name: str) -> pd.Series | None:
    """``df$name`` for a base-R data frame: exact match, else a unique partial match."""
    cols = [str(c) for c in df.columns]
    if name in cols:
        return df.iloc[:, cols.index(name)]
    partial = [i for i, c in enumerate(cols) if c.startswith(name)]
    if len(partial) == 1:
        return df.iloc[:, partial[0]]
    return None


def _tolower(s: str) -> str:
    """R ``tolower()``: ``towlower()`` per character (no multi-character mappings).

    The one character whose full lower case is two characters, ``"İ"``
    (U+0130), becomes a plain ``"i"`` as glibc's ``towlower()`` makes it.
    """
    if s.isascii():
        return s.lower()
    return "".join(lc if len(lc := c.lower()) == 1 else lc[0] for c in s)


def _last_ext(name: str) -> str:
    """``strsplit(name, "\\\\.")[[1]]``'s last element, as ``filetype()`` takes it.

    ``strsplit()`` drops a single trailing empty piece, so ``"a.R."`` gives
    ``"R"`` and ``"a.."`` gives ``""``; ``""`` splits into nothing and R's
    ``x[[length(x)]]`` then fails.
    """
    if name == "":
        raise IndexError("attempt to select less than one element in integerOneIndex")
    if name.endswith("."):
        name = name[:-1]
    return name.rpartition(".")[2]


def _name_filetype(name: str | None) -> str:
    """The ``filetype`` that ``file_category()`` computes for one file name.

    R tests every ``file_types`` extension as the regex ``\\.<ext>$`` (``+`` and
    ``.`` escaped, ``ignore.case = TRUE``) against the name, then pastes the
    unique types of the matching rows, in table order, with ``";"``. A name
    ends in ``.<ext>`` exactly when ``<ext>`` is the text after one of its
    dots, so each such suffix is looked up instead. TRE's case folding of the
    ASCII extensions only ever matches ASCII letters, hence the ASCII-only
    lower-casing. ``grepl()`` is ``FALSE`` for ``NA``, so ``NA`` gives ``""``.
    """
    if name is None:
        return ""
    from pytacheck.fileinfo.types import ext_rows

    rows = ext_rows()
    low = name.lower() if name.isascii() else name.translate(_ASCII_LOWER)
    hits: list[tuple[int, str]] = []
    pos = low.find(".")
    while pos >= 0:
        found = rows.get(low[pos + 1 :])
        if found:
            hits.extend(found)
        pos = low.find(".", pos + 1)
    if not hits:
        return ""
    hits.sort()
    return ";".join(dict.fromkeys(t for _, t in hits))


# -- file_category() -----------------------------------------------------------


def file_category(contents: Any) -> pd.DataFrame | dict[str, list[Any]]:
    """Categorise files (port of ``R/file_category.R::file_category()``).

    Parameters
    ----------
    contents:
        a table with columns ``name`` (and optionally ``filetype`` and
        ``category``), such as from ``osf_contents()``, or a vector of file names.

    Returns
    -------
    The table (a copy) with a new column ``file_category``: ``"readme"``,
    ``"codebook"``, ``"data"``, ``"code"`` or missing. When the table has no
    ``filetype`` column one is added first (the ``";"``-separated types of every
    ``metacheck::file_types`` extension the name ends with; ``""`` when none).

    Only hard rules classify as data or code: a file type of exactly ``stats``
    or ``code`` is code, exactly ``data`` is data, and a compound type holding
    ``data`` together with ``stats`` (``.jasp``, ``.por``) or ``archive``
    (``sample.fasta.gz``) is data. A readme name wins over everything, then a
    codebook name (or a ``category`` of ``"codebook"``).

    As in R, columns are looked up like ``df$name`` (a unique partial match is
    accepted), and ``None`` (R ``NULL``) gives
    ``{"filetype": [], "file_category": []}``.
    """
    if contents is None:
        # R: `contents$filetype <- ...` on NULL makes a list
        return {"filetype": [], "file_category": []}
    if isinstance(contents, pd.DataFrame):
        df = contents.copy()
    else:
        df = pd.DataFrame({"name": pd.Series(_chr_list(contents), dtype="string")})

    nrow = len(df)
    name_col = _dollar(df, "name")
    nm = [] if name_col is None else _chr_list(name_col)

    ft_col = _dollar(df, "filetype")
    if ft_col is None:
        ft = [_name_filetype(x) for x in nm]
        if len(ft) != nrow:
            raise ValueError(f"replacement has {len(ft)} rows, data has {nrow}")
        df["filetype"] = pd.Series(ft, dtype="string", index=df.index)
    else:
        ft = _chr_list(ft_col)

    cat_col = _dollar(df, "category")
    cat = [None] if cat_col is None else _chr_list(cat_col)  # `%||% NA`

    n = len(nm)
    if n != nrow:
        # R: the case_when() conditions recycle to the 0-length `nm`, and the
        # 0-length result cannot be assigned to the table
        raise ValueError(f"replacement has {n} rows, data has {nrow}")

    # hard rules (R: sure_class). `ft_has(t)` is grepl("\\bt\\b", ft).
    has_data = grepl(r"\bdata\b", ft)
    has_stats = grepl(r"\bstats\b", ft)
    has_archive = grepl(r"\barchive\b", ft)
    sure_class: list[str | None] = []
    for f, d, s, a in zip(ft, has_data, has_stats, has_archive, strict=True):
        if f == "stats" or f == "code":
            sure_class.append("code")
        elif f == "data" or (d and s) or (d and a):
            sure_class.append("data")
        else:
            sure_class.append(None)

    is_readme = grepl(_README_RX, nm, ignore_case=True)
    is_codebook = grepl(_CODEBOOK_RX, nm, ignore_case=True)
    if cat_col is not None:
        is_codebook = [b or c == "codebook" for b, c in zip(is_codebook, cat, strict=True)]
    # (R also computes `is_data` / `is_code`, but no longer uses them.)

    out: list[str | None] = []
    for readme, codebook, sure in zip(is_readme, is_codebook, sure_class, strict=True):
        if readme:
            out.append("readme")
        elif codebook:
            out.append("codebook")
        else:
            out.append(sure)
    df["file_category"] = pd.Series(out, dtype="string", index=df.index)
    return df


# -- filetype() ----------------------------------------------------------------


def filetype(filename: Any) -> pd.Series:
    """Get file type from extension (port of ``R/file_category.R::filetype()``).

    Looks up the text after the last ``.`` of each file name (lower-cased) in
    ``metacheck::file_types``. Returns a ``string`` Series (R's named vector:
    the index holds the file names) of the matching types pasted with ``";"``
    (``"code;data"`` for ``.json``), or the string ``"NA"`` when the extension
    is unknown (R pastes the ``NA`` of an unmatched join).

    As in R, a name without a dot is taken whole as its extension, and an
    empty name is an error.
    """
    from pytacheck.fileinfo.types import ext_types

    names = _chr_list(filename)
    table = ext_types()
    types: list[str] = []
    for name in names:
        if name is None:
            types.append("NA")
            continue
        types.append(table.get(_tolower(_last_ext(name)), "NA"))
    return pd.Series(types, index=pd.Index(names, dtype=object), dtype="string")
