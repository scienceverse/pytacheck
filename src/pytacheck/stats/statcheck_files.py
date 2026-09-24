"""statcheck's file front ends: ``checkHTML()``, ``checkPDF()``, ``checkdir()`` ...

Port of statcheck 1.5.0's ``R/file-to-txt.R`` (``getHTML()``, ``getPDF()``),
``R/checkHTML.R``, ``R/checkHTMLdir.R``, ``R/checkPDF.R``, ``R/checkPDFdir.R``
and ``R/checkdir.R``. They turn article files into text and run
:func:`pytacheck.stats.statcheck.statcheck` on it, with the file names as
sources. metacheck itself never calls them (it gives statcheck the sentences
of a paper), but they are part of the statcheck API.

R quirks kept on purpose:

* ``getHTML()`` round-trips the text through UTF-32 code points and replaces
  the *digits* ``8239`` in every code point's decimal form with ``32`` (meant
  to turn a narrow no-break space, U+202F, into a space, it also rewrites
  e.g. U+473F, decimal 18239, into U+0084, decimal 132);
* ``checkHTMLdir()``'s file pattern ``".html|.htm"`` is a regular
  expression, so any file whose name contains ``html``/``htm`` after some
  character is read;
* ``getPDF()`` runs ``pdftotext -q -enc ASCII7`` and leaves the ``.txt`` file
  next to the PDF; a file not ending in ``.pdf`` is read as it is;
* ``checkdir()`` stops with "statcheck did not find any results" when a
  folder has both PDF and HTML files and either kind yields no results.

Interactive file choosers (``tcltk``) used by R when the path is missing are
not available: the path arguments are required.
"""

from __future__ import annotations

import os
import subprocess
import warnings
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pandas as pd

__all__ = ["checkHTML", "checkHTMLdir", "checkPDF", "checkPDFdir", "checkdir", "getHTML", "getPDF"]

# (pattern, replacement) pairs of getHTML(), applied in order with fixed = TRUE.
_ENTITIES_1 = (
    ("&#60;", "<"),
    ("&lt;", "<"),
    ("&LT;", "<"),
    ("&#x0003C;", "<"),
    ("&#x0003c;", "<"),
    ("&#61;", "="),
    ("&equals;", "="),
    ("&#x0003D;", "="),
    ("&#62;", ">"),
    ("&gt;", ">"),
    ("&GT;", ">"),
    ("&#x0003E;", ">"),
    ("&#40;", "("),
    ("&#41;", ")"),
    ("&thinsp;", " "),
    ("&nbsp;", " "),
    ("&nnbsp;", " "),
    ("&#8239;", " "),
    ("&#x202F;", " "),
    ("&#32;", " "),
    ("&#160;", " "),
    ("&ensp;", " "),
    ("&#8194;", " "),
    ("&emsp;", " "),
    ("&#8195;", " "),
    ("&#8201;", " "),
    ("&zwnj;", " "),
    ("&#8204;", " "),
    ("&zwj;", " "),
    ("&#8205;", " "),
    ("&lrm;", " "),
    ("&#8206;", " "),
    ("&rlm;", " "),
    ("&#8207;", " "),
)
_ENTITIES_2 = (
    ("&minus;", "-"),
    ("&#x02212;", "-"),
    ("&#8722;", "-"),
    ("&chi;", "X"),
    ("&#x003C7;", "X"),
    ("&#x003c7;", "X"),
    ("&#967;", "X"),
    ("&Chi;", "X"),
    ("&#x003A7;", "X"),
    ("&#935;", "X"),
)


def _read_bytes_as_text(path: str | os.PathLike[str]) -> str:
    """``readChar(con, file.info(f)$size, useBytes = TRUE)`` in a UTF-8 session."""
    data = Path(path).read_bytes()
    nul = data.find(b"\x00")  # readChar() stops at an embedded nul
    if nul >= 0:
        data = data[:nul]
    return data.decode("utf-8", errors="replace")


def _utf32_digits_8239(text: str) -> str:
    """``stri_enc_fromutf32(gsub("8239", "32", stri_enc_toutf32(x), fixed = TRUE))``.

    Every code point containing the decimal digits ``8239`` (all are >= 8239)
    is rewritten; the rest of the text is unchanged.
    """
    changed = {
        ch: chr(int(str(ord(ch)).replace("8239", "32")))
        for ch in set(text)
        if ord(ch) >= 8239 and "8239" in str(ord(ch))
    }
    if not changed:
        return text
    return text.translate({ord(k): v for k, v in changed.items()})


def _html_to_text(raw: str) -> str:
    from pytacheck._r.regex import gsub

    # Remove subscripts (except for p_rep)
    s = gsub("<sub>(?!rep).*?</sub>", "", raw, perl=True)
    # Remove HTML tags
    s = gsub("<(.|\n)*?>", "", s)
    s = _utf32_digits_8239(s)
    for pattern, replacement in _ENTITIES_1:
        s = s.replace(pattern, replacement)
    s = gsub("\\s+", " ", s)
    s = s.replace("\n", "").replace("\r", "")
    for pattern, replacement in _ENTITIES_2:
        s = s.replace(pattern, replacement)
    return s


def getHTML(x: str | os.PathLike[str] | Sequence[str | os.PathLike[str]]) -> list[str]:
    """Port of ``statcheck:::getHTML()``: the plain text of HTML files (one string per file).

    Tags and subscripts (except ``p<sub>rep</sub>``) are removed, common
    entities for comparison signs, spaces, minus signs and chi are replaced,
    and runs of whitespace become a single space.
    """
    files = [x] if isinstance(x, str | os.PathLike) else list(x)
    return [_html_to_text(_read_bytes_as_text(f)) for f in files]


def _run_pdftotext(pdf: str) -> None:
    """``system('pdftotext -q -enc "ASCII7" "<pdf>"')``; a missing tool is only reported."""
    try:
        subprocess.run(
            ["pdftotext", "-q", "-enc", "ASCII7", pdf],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass  # R's system() prints "sh: pdftotext: not found" and goes on


def getPDF(x: str | os.PathLike[str] | Sequence[str | os.PathLike[str]]) -> list[str]:
    """Port of ``statcheck:::getPDF()``: the text of PDF files, via ``pdftotext``.

    Like R, the ``.txt`` file ``pdftotext`` writes next to each PDF is kept;
    a failure gives ``""`` and the warning ``"Failure in file <x>"``.
    """
    from pytacheck._r.regex import gsub

    files = [os.fspath(x)] if isinstance(x, str | os.PathLike) else [os.fspath(f) for f in x]
    if not files:
        # for (i in 1:length(x)) runs i = 1, 0 on an empty vector: `if (logical(0))`
        raise RuntimeError("argument is of length zero")
    out: list[str] = []
    for f in files:
        _run_pdftotext(f)
        txt = gsub(r"\.pdf$", r"\.txt", f)
        if os.path.exists(txt):
            text = Path(txt).read_bytes().decode("utf-8", errors="replace")
            out.append(gsub("[\r\n]", "", text))
        else:
            warnings.warn(f"Failure in file {f}", stacklevel=2)
            out.append("")
    return out


def _statcheck(texts: pd.Series, kwargs: dict[str, Any]) -> pd.DataFrame | None:
    from pytacheck.stats.statcheck import statcheck

    return statcheck(texts, **kwargs)


def _named(files: Sequence[str], texts: Sequence[str]) -> pd.Series:
    """``names(txts) <- basename(files)``: a named character vector (names may repeat)."""
    return pd.Series(list(texts), index=[os.path.basename(f) for f in files], dtype=object)


def _as_files(files: str | os.PathLike[str] | Sequence[str | os.PathLike[str]]) -> list[str]:
    if isinstance(files, str | os.PathLike):
        return [os.fspath(files)]
    return [os.fspath(f) for f in files]


def checkHTML(
    files: str | os.PathLike[str] | Sequence[str | os.PathLike[str]], **kwargs: Any
) -> pd.DataFrame | None:
    """Port of ``statcheck::checkHTML()``: run statcheck on HTML files.

    *kwargs* are passed to :func:`~pytacheck.stats.statcheck.statcheck`; the
    ``source`` column holds the file names.
    """
    paths = _as_files(files)
    return _statcheck(_named(paths, [getHTML(f)[0] for f in paths]), kwargs)


def checkPDF(
    files: str | os.PathLike[str] | Sequence[str | os.PathLike[str]], **kwargs: Any
) -> pd.DataFrame | None:
    """Port of ``statcheck::checkPDF()``: run statcheck on PDF files (needs ``pdftotext``)."""
    paths = _as_files(files)
    return _statcheck(_named(paths, [getPDF(f)[0] for f in paths]), kwargs)


def _list_files(
    directory: str | os.PathLike[str], pattern: str | None, recursive: bool
) -> list[str]:
    """``list.files(dir, pattern, full.names = TRUE, recursive)`` (sorted, no dot files)."""
    from pytacheck._r.regex import grepl

    root = os.fspath(directory)
    found: list[str] = []
    if recursive:
        for base, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            rel = os.path.relpath(base, root)
            for name in names:
                if name.startswith("."):
                    continue
                found.append(name if rel == "." else os.path.join(rel, name))
    elif os.path.isdir(root):
        found = [n for n in os.listdir(root) if not n.startswith(".")]
    if pattern:
        keep = grepl(pattern, [os.path.basename(f) for f in found])
        found = [f for f, k in zip(found, keep, strict=True) if k]
    return sorted(os.path.join(root, f) for f in found)


def _flag(x: Any, value: bool) -> bool:
    """R ``x == TRUE`` / ``x == FALSE`` in an ``if ()``."""
    if x is None:
        raise RuntimeError("missing value where TRUE/FALSE needed")
    return bool(x == (1 if value else 0))


def checkHTMLdir(
    dir: str | os.PathLike[str],  # noqa: A002 - R argument name
    subdir: bool = True,
    extension: bool = True,
    **kwargs: Any,
) -> pd.DataFrame | None:
    """Port of ``statcheck::checkHTMLdir()``: run statcheck on the HTML files in a folder.

    With ``extension=True`` files whose name matches the regular expression
    ``".html|.htm"`` are read, with ``extension=False`` all files;
    ``subdir=True`` also searches subfolders.
    """
    pat: str | None = None
    if _flag(extension, True):
        pat = ".html|.htm"
    if _flag(extension, False):
        pat = ""
    if pat is None:
        raise RuntimeError("object 'pat' not found")
    files = _list_files(dir, pat, bool(subdir))
    if not files:
        raise RuntimeError("No HTML found")
    return _statcheck(_named(files, [getHTML(f)[0] for f in files]), kwargs)


def checkPDFdir(
    dir: str | os.PathLike[str],  # noqa: A002 - R argument name
    subdir: bool = True,
    **kwargs: Any,
) -> pd.DataFrame | None:
    """Port of ``statcheck::checkPDFdir()``: run statcheck on the PDF files in a folder."""
    from pytacheck._r.regex import grepl

    all_files = _list_files(dir, r"\.pdf", bool(subdir))
    files = [f for f, k in zip(all_files, grepl(r"\.pdf$", all_files), strict=True) if k]
    if not files:
        raise RuntimeError("No PDF found")
    return _statcheck(_named(files, [getPDF(f)[0] for f in files]), kwargs)


def checkdir(
    dir: str | os.PathLike[str],  # noqa: A002 - R argument name
    subdir: bool = True,
    **kwargs: Any,
) -> pd.DataFrame:
    """Port of ``statcheck::checkdir()``: statcheck on all PDF and HTML files in a folder."""
    from pytacheck._r.regex import grepl

    root = os.fspath(dir)
    names = [os.path.relpath(f, root) for f in _list_files(root, None, bool(subdir))]
    pdfs = any(grepl(r"\.pdf$", names, ignore_case=True))
    htmls = any(grepl(r"\.html?$", names, ignore_case=True))
    if pdfs:
        pdfres = checkPDFdir(root, subdir, **kwargs)
    if htmls:
        htmlres = checkHTMLdir(root, subdir, **kwargs)
    if pdfs and htmls:
        if pdfres is not None and htmlres is not None:
            return pd.concat([pdfres, htmlres], ignore_index=True)
        raise RuntimeError("statcheck did not find any results")
    if pdfs:
        if pdfres is not None:
            return pdfres
        raise RuntimeError("statcheck did not find any results")
    if htmls:
        if htmlres is not None:
            return htmlres
        raise RuntimeError("statcheck did not find any results")
    raise RuntimeError("No PDF or HTML found")
