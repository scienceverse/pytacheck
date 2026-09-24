"""Helpers of the Preregistration Check module.

Port of the helper functions in ``inst/modules/prereg_check.R``: extracting a
standardised set of fields from an OSF registration (``osf_prereg_extract()``
and the extractors it dispatches to) or an AsPredicted page
(``ap_schema()``).

metacheck parses OSF API responses with ``jsonlite`` (``simplifyVector =
TRUE``) and then flattens every answer with ``paste(unlist(value))``; the
order and formatting of what ends up in the table depends on how jsonlite
simplified the JSON (arrays of objects become data frames, which ``unlist()``
walks column by column). pytacheck keeps API responses as plain JSON, so this
module carries a small model of the simplified R values (:func:`simplify`) and
of the base-R operations the module applies to them (``unlist()``, ``c()``,
``paste()``, ``as.character()`` of a list), so fields come out exactly as in R.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

import pandas as pd

__all__ = [
    "OSF_LABEL_FIELD",
    "PREREG_SCHEMA_FIELDS",
    "ap_schema",
    "common_osf",
    "osf_blocks_labels",
    "osf_label_to_field",
    "osf_pages_labels",
    "osf_pr_schema",
    "osf_prereg_extract",
    "osf_schema_labels",
    "osf_special_handlers",
    "prereg_schema",
    "prsp",
    "withdrawn",
]

# ---------------------------------------------------------------------------
# a model of jsonlite-simplified R values
# ---------------------------------------------------------------------------

_INT_MAX = 2147483647
_RANK = {"logical": 0, "integer": 1, "double": 2, "character": 3}


@dataclass(frozen=True)
class RVec:
    """An atomic R vector: ``type`` is logical/integer/double/character, ``None`` is ``NA``."""

    type: str
    values: tuple[Any, ...]


@dataclass(frozen=True)
class RList:
    """An R list (``names`` is ``None`` for an unnamed list); ``None`` items are ``NULL``."""

    items: tuple[Any, ...]
    names: tuple[str, ...] | None = None


@dataclass(frozen=True)
class RFrame:
    """A data frame made by jsonlite from an array of JSON objects."""

    names: tuple[str, ...]
    cols: tuple[Any, ...]
    nrow: int


@dataclass(frozen=True)
class RMatrix:
    """A matrix (or higher array) made by jsonlite from nested equal-length arrays.

    An array of equal-length scalar arrays is a matrix (``rbind()`` of the
    rows); an array of matrices/arrays with the same ``dim`` is an array one
    dimension up (``array(rbind(lapply(out, as.vector)))``).
    """

    vec: RVec  # column-major
    dim: tuple[int, ...]


RValue = RVec | RList | RFrame | RMatrix | None


def _is_scalar(x: Any) -> bool:
    return x is None or isinstance(x, str | int | float | bool)


def _scalar_type(x: Any) -> str | None:
    if x is None:
        return None
    if isinstance(x, bool):
        return "logical"
    if isinstance(x, int):
        return "integer" if -_INT_MAX <= x <= _INT_MAX else "double"
    if isinstance(x, float):
        return "double"
    return "character"


def _promote(types: Sequence[str | None]) -> str:
    known = [t for t in types if t is not None]
    return max(known, key=_RANK.__getitem__) if known else "logical"


def _coerce(value: Any, from_type: str | None, to_type: str) -> Any:
    """Coerce one element as R's ``c()`` / ``unlist()`` do (``None`` stays ``NA``)."""
    if value is None:
        return None
    if to_type == "character":
        return value if from_type == "character" else _elem_chr(value, from_type or "logical")
    if to_type == "double":
        return float(value)
    if to_type == "integer":
        return int(value)
    return value


def _vec_from_scalars(values: Sequence[Any]) -> RVec:
    """``list_to_vec()``: JSON scalars (``null`` -> ``NA``) as one atomic vector."""
    types = [_scalar_type(v) for v in values]
    to = _promote(types)
    return RVec(to, tuple(_coerce(v, t, to) for v, t in zip(values, types, strict=True)))


def simplify(x: Any) -> RValue:
    """Parsed JSON -> the R value ``jsonlite::fromJSON(simplifyVector = TRUE)`` gives.

    Objects become named lists (``null`` members are ``NULL``), arrays of
    scalars atomic vectors (``null`` -> ``NA``), arrays of objects data
    frames, arrays of equal-length scalar arrays matrices, arrays of
    same-shaped matrices higher arrays; anything else a list (in which an
    empty array ``[]`` next to atomic vectors becomes an empty vector of the
    first one's type: jsonlite's ``homoList``).
    """
    if x is None:
        return None
    if _is_scalar(x):
        return _vec_from_scalars([x])
    if isinstance(x, Mapping):
        return RList(tuple(simplify(v) for v in x.values()), tuple(str(k) for k in x))
    return _simplify_array(list(x), matrix=True)


def _simplify_array(arr: list[Any], matrix: bool) -> RValue:
    """``jsonlite:::simplify()`` of a JSON array (*matrix*: ``simplifyMatrix``)."""
    if not arr:
        return RList(())
    if all(_is_scalar(v) for v in arr):
        return _vec_from_scalars(arr)
    if all(v is None or isinstance(v, Mapping) for v in arr):
        return _records_frame(arr)
    if (
        matrix
        and all(isinstance(v, list) and v and all(_is_scalar(e) for e in v) for v in arr)
        and len({len(v) for v in arr}) == 1
    ):
        return _rbind([_vec_from_scalars(v) for v in arr], (len(arr[0]),))
    items = [simplify(v) for v in arr]
    if (
        matrix
        and all(isinstance(it, RMatrix) for it in items)
        and len({it.dim for it in items}) == 1  # type: ignore[union-attr]
    ):
        arrays: list[RMatrix] = items  # type: ignore[assignment]
        return _rbind([a.vec for a in arrays], arrays[0].dim)
    return RList(_homo_list(items))


def _rbind(rows: Sequence[RVec], dim: tuple[int, ...]) -> RMatrix:
    """``array(do.call(rbind, rows), c(length(rows), dim))``: rows interleaved, column-major."""
    to = _promote([r.type for r in rows])
    size = len(rows[0].values)
    flat = [_coerce(r.values[j], r.type, to) for j in range(size) for r in rows]
    return RMatrix(RVec(to, tuple(flat)), (len(rows), *dim))


def _is_empty_array(x: RValue) -> bool:
    """``identical(x, list())``: an empty JSON array (``{}`` is a *named* empty list)."""
    return isinstance(x, RList) and not x.items and x.names is None


def _homo_list(items: list[RValue]) -> tuple[RValue, ...]:
    """jsonlite's ``homoList``: empty arrays among data frames / atomic vectors."""
    empty = [_is_empty_array(it) for it in items]
    if not any(empty) or all(empty):
        return tuple(items)
    others = [it for it, e in zip(items, empty, strict=True) if not e]
    if all(isinstance(it, RFrame) for it in others):
        blank: RValue = RFrame((), (), 0)  # data.frame()
    elif all(isinstance(it, RVec) for it in others):  # is.vector() && is.atomic()
        blank = RVec(others[0].type, ())  # type: ignore[union-attr]
    else:
        return tuple(items)
    return tuple(blank if e else it for it, e in zip(items, empty, strict=True))


def _records_frame(arr: list[Any]) -> RFrame:
    names: list[str] = []
    for rec in arr:
        if isinstance(rec, Mapping):
            for k in rec:
                if k not in names:
                    names.append(str(k))
    cols = []
    for name in names:
        values = [rec.get(name) if isinstance(rec, Mapping) else None for rec in arr]
        cols.append(_simplify_array(values, matrix=False))
    return RFrame(tuple(names), tuple(cols), len(arr))


# ---------------------------------------------------------------------------
# base R operations on those values
# ---------------------------------------------------------------------------


def _elem_chr(value: Any, type_: str) -> str | None:
    """``as.character()`` of one vector element (``None`` for ``NA``)."""
    from pytacheck._r import as_character

    if value is None:
        return None
    if type_ == "logical":
        return "TRUE" if value else "FALSE"
    if type_ == "integer":
        return str(int(value))
    if type_ == "double":
        return str(as_character(float(value)))
    return str(value)


def _leaves(x: RValue, out: list[tuple[Any, str]], types: list[str]) -> None:
    if x is None:
        return
    if isinstance(x, RMatrix):
        x = x.vec
    if isinstance(x, RVec):
        types.append(x.type)  # an empty vector still takes part in the type
        out.extend((v, x.type) for v in x.values)
    elif isinstance(x, RFrame):
        for col in x.cols:
            _leaves(col, out, types)
    else:
        for item in x.items:
            _leaves(item, out, types)


def unlist(x: RValue) -> RVec | None:
    """``unlist(x)``: every atomic leaf, depth first (data frames column by column).

    The result has the highest type of all atomic components, empty ones
    included (``unlist(list(numeric(0), 100000L))`` is double, ``1e+05``).
    """
    leaves: list[tuple[Any, str]] = []
    types: list[str] = []
    _leaves(x, leaves, types)
    if not types:
        return None
    to = _promote(types)
    return RVec(to, tuple(_coerce(v, t, to) for v, t in leaves))


def r_c(values: Sequence[RValue]) -> RValue:
    """``c(...)``: atomic vectors concatenate; any list-like argument makes a list."""
    present = [v for v in values if v is not None]
    if not present:
        return None
    if all(isinstance(v, RVec | RMatrix) for v in present):
        # matrices and arrays are atomic: c() drops their dim
        vecs: list[RVec] = [v.vec if isinstance(v, RMatrix) else v for v in present]  # type: ignore[misc]
        to = _promote([v.type for v in vecs])
        return RVec(to, tuple(_coerce(e, v.type, to) for v in vecs for e in v.values))
    items: list[RValue] = []
    for v in present:
        if isinstance(v, RVec):
            items.extend(RVec(v.type, (e,)) for e in v.values)
        elif isinstance(v, RMatrix):
            items.extend(RVec(v.vec.type, (e,)) for e in v.vec.values)
        elif isinstance(v, RFrame):
            items.extend(v.cols)
        else:
            items.extend(v.items)
    return RList(tuple(items))


def _chr_values(x: RValue) -> list[str]:
    """``as.character(x)`` with ``NA`` shown as ``"NA"`` (what ``paste()`` sees)."""
    if x is None:
        return []
    if isinstance(x, RVec):
        return [_na(_elem_chr(v, x.type)) for v in x.values]
    if isinstance(x, RMatrix):
        return [_na(_elem_chr(v, x.vec.type)) for v in x.vec.values]
    items = x.cols if isinstance(x, RFrame) else x.items
    return [_list_elem_chr(item) for item in items]


def _na(s: str | None) -> str:
    return "NA" if s is None else s


def _list_elem_chr(item: RValue) -> str:
    """``as.character()`` of one list element: a scalar's value, else its deparse."""
    if isinstance(item, RVec) and len(item.values) == 1:
        return _na(_elem_chr(item.values[0], item.type))
    return deparse(item)


def paste_collapse(x: RValue, collapse: str) -> str:
    """``paste(x, collapse = collapse)`` of one value (``NULL`` gives ``""``)."""
    return collapse.join(_chr_values(x))


_EMPTY = {"logical": "logical(0)", "integer": "integer(0)", "double": "numeric(0)"}


#: R's EncodeString() escapes of ASCII control characters (others: octal)
_ASCII_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\a": "\\a",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\v": "\\v",
}
#: Unicode categories R does not print as is (controls, line/paragraph
#: separators, unassigned code points, surrogates)
_UNPRINTABLE = frozenset({"Cc", "Zl", "Zp", "Cn", "Cs"})
#: code points assigned after Python's Unicode 15.0 that R's tables print
_NEWER_ASSIGNED = ((0x2FFC, 0x2FFF), (0x31EF, 0x31EF), (0x2EBF0, 0x2EE5D))


def _r_printable(ch: str) -> bool:
    """R's ``iswprint()`` for a non-ASCII character (UTF-8 locale)."""
    if unicodedata.category(ch) not in _UNPRINTABLE:
        return True
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _NEWER_ASSIGNED)


def _deparse_str(s: str) -> str:
    """A string as R's ``deparse()`` writes it (``EncodeString()`` in a UTF-8 locale).

    ``\\a \\b \\f \\n \\r \\t \\v`` are named escapes, other ASCII controls
    octal (``\\001``, ``\\177``); non-printable non-ASCII characters (C1
    controls, U+2028/U+2029, unassigned code points) are ``\\uxxxx`` or
    ``\\U{xxxxxx}``; everything else is written as is.
    """
    out = ['"']
    for ch in s:
        cp = ord(ch)
        if cp < 0x80:
            esc = _ascii_escape(ch, cp)
        elif _r_printable(ch):
            esc = ch
        else:
            esc = f"\\u{cp:04x}" if cp <= 0xFFFF else f"\\U{{{cp:06x}}}"
        out.append(esc)
    out.append('"')
    return "".join(out)


def _ascii_escape(ch: str, cp: int) -> str:
    esc = _ASCII_ESCAPES.get(ch)
    if esc is not None:
        return esc
    if cp < 32 or cp == 127:
        return f"\\{cp:03o}"
    return ch


def _deparse_elem(value: Any, type_: str) -> str:
    if value is None:
        return "NA"
    if type_ == "character":
        return _deparse_str(value)
    return _elem_chr(value, type_) or "NA"


def _syntactic(name: str) -> bool:
    from pytacheck._r import grepl

    reserved = {
        "if", "else", "repeat", "while", "function", "for", "next", "break", "TRUE", "FALSE",
        "NULL", "Inf", "NaN", "NA", "NA_integer_", "NA_real_", "NA_character_", "in",
    }  # fmt: skip
    return name not in reserved and bool(
        grepl(r"^((([[:alpha:]]|[.][._[:alpha:]])[._[:alnum:]]*)|[.])$", name)
    )


def deparse(x: RValue) -> str:
    """``deparse()`` as ``as.character()`` uses it for list elements.

    R's simple deparse: no ``L`` suffixes, every ``NA`` is ``NA`` (not
    ``NA_integer_``...), attributes (a matrix's ``dim``) dropped, and integer
    runs increasing or decreasing by one written ``a:b``.
    """
    if x is None:
        return "NULL"
    if isinstance(x, RMatrix):
        x = x.vec
    if isinstance(x, RVec):
        vals = x.values
        if not vals:
            return _EMPTY.get(x.type, "character(0)")
        if len(vals) == 1:
            return _deparse_elem(vals[0], x.type)
        if x.type == "integer" and all(v is not None for v in vals):
            steps = {b - a for a, b in pairwise(vals)}
            if steps in ({1}, {-1}):
                return f"{vals[0]}:{vals[-1]}"
        return "c(" + ", ".join(_deparse_elem(v, x.type) for v in vals) + ")"
    if isinstance(x, RFrame):
        names: Sequence[str] | None = x.names
        items: Sequence[RValue] = x.cols
    else:
        names, items = x.names, x.items
    parts = []
    for i, item in enumerate(items):
        name = names[i] if names else ""
        if name:
            shown = name if _syntactic(name) else f"`{name}`"
            parts.append(f"{shown} = {deparse(item)}")
        else:
            parts.append(deparse(item))
    return "list(" + ", ".join(parts) + ")"


def dollar(x: Any, name: str) -> Any:
    """R ``x$name`` on a named list (a JSON object): exact name, else a unique prefix."""
    if not isinstance(x, Mapping):
        return None
    if name in x:
        return x[name]
    hits = [k for k in x if isinstance(k, str) and k.startswith(name)]
    return x[hits[0]] if len(hits) == 1 else None


def _path(x: Any, *names: str) -> Any:
    for name in names:
        x = dollar(x, name)
        if x is None:
            return None
    return x


def _chr1(x: RValue) -> str | None:
    """A length-one character scalar, or ``None``."""
    if isinstance(x, RVec) and len(x.values) == 1 and x.type == "character":
        value = x.values[0]
        return value if isinstance(value, str) else None
    return None


# ---------------------------------------------------------------------------
# OSF preregistration dispatch
# ---------------------------------------------------------------------------

#: Schema fields: an ordered mapping field -> R value (``None`` is ``NULL``).
Schema = dict[str, RValue]
#: Fetches a registration schema (``resp_body_json(simplifyVector = FALSE)``) by URL.
SchemaFetcher = Callable[[str], Any]


def osf_prereg_extract(
    info: Mapping[str, Any], fetch_schema: SchemaFetcher | None = None
) -> Schema:
    """Port of inst/modules/prereg_check.R::osf_prereg_extract().

    Identify a registration by its ``schema_id`` and extract its responses:
    withdrawn registrations are marked, a few deeply nested schemas have
    dedicated extractors (:func:`osf_special_handlers`), everything else is
    read generically from the schema's field labels (:func:`osf_pr_schema`).
    *info* is the registration resource (parsed JSON).
    """
    if _withdrawn(info):
        return withdrawn(info)
    schema_id = _schema_id(info)
    handler = osf_special_handlers().get(schema_id) if schema_id is not None else None
    if handler is not None:
        return handler(info)
    return osf_pr_schema(info, fetch_schema)


def osf_special_handlers() -> dict[str, Callable[[Mapping[str, Any]], Schema]]:
    """Port of inst/modules/prereg_check.R::osf_special_handlers().

    ``schema_id`` -> dedicated extractor, for schemas the label-driven
    extractor cannot handle.
    """
    return {
        # Pre-Registration in Social Psychology (van 't Veer & Giner-Sorolla, 2016)
        "5730e99a9ad5a102c5745a8a": prsp,
    }


def _withdrawn(info: Mapping[str, Any]) -> bool:
    """``isTRUE(info$attributes$withdrawn)`` on the jsonlite-simplified value."""
    w = simplify(_path(info, "attributes", "withdrawn"))
    return isinstance(w, RVec) and w.type == "logical" and w.values == (True,)


def _schema_id(info: Mapping[str, Any]) -> str | None:
    """``info$relationships$registration_schema$data$id`` as a string (``None``: NA)."""
    return _chr1(simplify(_path(info, "relationships", "registration_schema", "data", "id")))


def _schema_href(info: Mapping[str, Any]) -> str | None:
    """The registration schema's URL; ``None`` when there is no usable one.

    R requests whatever ``$links$related$href`` holds; anything but one string
    fails inside its ``tryCatch()`` and reads as "no schema", as ``None`` does.
    """
    href = _path(info, "relationships", "registration_schema", "links", "related", "href")
    return _chr1(simplify(href))


def needs_schema(info: Mapping[str, Any]) -> str | None:
    """The schema URL :func:`osf_prereg_extract` will fetch for *info* (``None`` if none)."""
    if _withdrawn(info):
        return None
    schema_id = _schema_id(info)
    if schema_id is not None and schema_id in osf_special_handlers():
        return None
    responses = _path(info, "attributes", "registration_responses")
    if not _r_length(responses):
        return None
    return _schema_href(info)


def _r_length(x: Any) -> int:
    if x is None:
        return 0
    if isinstance(x, Mapping | list | tuple):
        return len(x)
    return 1


def osf_pr_schema(info: Mapping[str, Any], fetch_schema: SchemaFetcher | None = None) -> Schema:
    """Port of inst/modules/prereg_check.R::osf_pr_schema(): the generic extractor.

    Each response key is matched to its human-readable label from the
    registration's schema (blocks format: ``"<grp>-<pos>"`` keys, matched on
    the position; pages format: question ids), the label is mapped to a
    canonical field (:func:`osf_label_to_field`), and the flattened answer is
    stored there (answers mapping to the same field are joined by a space).
    """
    from pytacheck._r import sub, trimws

    common = common_osf(info)
    responses = _path(info, "attributes", "registration_responses")
    if not _r_length(responses):
        return common
    key_labels = osf_schema_labels(info, fetch_schema)
    if not key_labels:
        return common
    if not isinstance(responses, Mapping):
        # R: an unnamed array has no names to look up
        return common

    reserved = set(common)
    extra: Schema = {}
    for key, raw in responses.items():
        label = key_labels.get(key, _MISSING)
        if label is _MISSING:
            label = key_labels.get(str(sub("^.*-", "", key)), _MISSING)
        if not isinstance(label, str) or label == "":
            continue  # no label, NA or empty
        field = osf_label_to_field(label)
        if field in reserved:
            continue
        value = str(trimws(paste_collapse(unlist(simplify(raw)), " ")))
        if value == "":
            continue
        prev = _chr1(extra.get(field))
        extra[field] = RVec("character", (value if prev is None else f"{prev} {value}",))
    return {**common, **extra}


_MISSING = object()


def osf_schema_labels(
    info: Mapping[str, Any], fetch_schema: SchemaFetcher | None = None
) -> dict[str, str | None]:
    """Port of inst/modules/prereg_check.R::osf_schema_labels().

    Fetch the registration's schema and map each possible response key to its
    human-readable label (``None`` for a label R has as ``NA``); empty when the
    schema cannot be fetched or has neither format.
    """
    schema_url = _schema_href(info)
    if schema_url is None:
        return {}
    body = (fetch_schema or fetch_schema_json)(schema_url)
    schema = _path(body, "data", "attributes", "schema")
    blocks = dollar(schema, "blocks")
    if blocks is not None:
        return osf_blocks_labels(blocks)
    pages = dollar(schema, "pages")
    if pages is not None:
        return osf_pages_labels(pages)
    return {}


def fetch_schema_json(url: str) -> Any:
    """The body of a registration schema request, or ``None`` on any failure.

    R: ``httr2::request(url) |> .osf_headers() |> req_error(FALSE) |>
    req_retry(max_tries = 3, 429) |> req_perform() |> resp_body_json()`` in a
    ``tryCatch()`` returning ``NULL``.
    """
    from pytacheck import http
    from pytacheck.archives.osf_helpers import _osf_headers, _resp_body_json

    try:
        resp = http.request(
            "GET", url, headers=_osf_headers()["headers"], max_tries=3, retry_statuses=(429,)
        )
        if resp is None:
            return None
        return _resp_body_json(resp)
    except Exception:
        return None


_INPUT_TYPES = frozenset(
    {
        "long-text-input",
        "short-text-input",
        "single-select-input",
        "multi-select-input",
        "file-input",
        "contributors-input",
    }
)


def _label_chr(x: Any) -> str | None:
    """A schema label as R stores it in its character vector of labels.

    Strings are kept; a number or logical is coerced by ``as.character()``
    (``2024`` -> ``"2024"``, ``true`` -> ``"TRUE"``, ``1e5`` -> ``"1e+05"``);
    ``null`` (and anything else) is ``NA``.
    """
    if isinstance(x, str):
        return x
    if isinstance(x, bool | int | float):
        return _elem_chr(x, _scalar_type(x) or "logical")
    return None


def osf_blocks_labels(blocks: Any) -> dict[str, str | None]:
    """Port of inst/modules/prereg_check.R::osf_blocks_labels().

    Blocks format: the 0-based position of each input block -> the
    ``display_text`` of the question label preceding it.
    """
    labels: dict[str, str | None] = {}
    last_label: str | None = None
    items = list(blocks.values()) if isinstance(blocks, Mapping) else list(blocks or [])
    for i, block in enumerate(items):
        bt = dollar(block, "block_type")
        bt = bt if isinstance(bt, str) else None
        if bt == "question-label":
            last_label = _label_chr(dollar(block, "display_text"))
        if bt in _INPUT_TYPES:
            labels[str(i)] = last_label
    return labels


def osf_pages_labels(pages: Any) -> dict[str, str | None]:
    """Port of inst/modules/prereg_check.R::osf_pages_labels().

    Pages format: question id (and its ``.question`` / ``.uploader`` sub-keys)
    -> the question title, or the id itself when the title is missing, longer
    than 40 characters or a question.
    """
    from pytacheck._r import grepl

    labels: dict[str, str | None] = {}
    page_list = list(pages.values()) if isinstance(pages, Mapping) else list(pages or [])
    for page in page_list:
        questions = dollar(page, "questions")
        if questions is None:
            continue
        q_list = list(questions.values()) if isinstance(questions, Mapping) else list(questions)
        for q in q_list:
            qid = dollar(q, "qid")
            if not isinstance(qid, str):
                continue
            title = _label_chr(dollar(q, "title"))
            title_is_label = (
                title is not None and title != "" and len(title) <= 40 and not grepl("[?]", title)
            )
            label = title if title_is_label else qid
            labels[qid] = label
            labels[f"{qid}.question"] = label
            labels[f"{qid}.uploader"] = label
    return labels


def osf_label_to_field(label: str) -> str:
    """Port of inst/modules/prereg_check.R::osf_label_to_field().

    Map a schema field label to a canonical field name (via
    :data:`OSF_LABEL_FIELD`), falling back to a slug of the label.
    """
    from pytacheck._r import gsub, trimws

    key = _tolower(str(trimws(label)))
    if key in OSF_LABEL_FIELD:
        return OSF_LABEL_FIELD[key]
    slug = str(gsub("[^a-z0-9]+", "_", key))
    slug = str(gsub("^_+|_+$", "", slug))
    return slug if slug else "field"


def _tolower(s: str) -> str:
    """R ``tolower()``: ``towlower()`` character by character.

    Unlike ``str.lower()`` this is the simple case mapping: ``"İ"`` gives
    ``"i"`` (not ``"i̇"``) and a word-final ``"Σ"`` gives ``"σ"``.
    """
    if s.isascii():
        return s.lower()
    return "".join(ch.lower()[:1] or ch for ch in s)


#: Research-core field labels (lowercased) -> canonical prereg_schema fields
#: (R: ``osf_label_field``).
OSF_LABEL_FIELD: dict[str, str] = {
    # research questions / hypotheses
    "research question": "research_questions",
    "research questions": "research_questions",
    "research question(s)": "research_questions",
    "primary research question(s)": "research_questions",
    "research questions or hypotheses": "research_questions",
    "research questions or hypothesis": "research_questions",
    "hypothesis": "research_questions",
    "hypotheses": "research_questions",
    "expectations / hypotheses": "research_questions",
    # description / background
    "description": "description",
    "study description": "description",
    "background": "description",
    "summary": "description",
    # study design / type
    "study design": "study_design_overview",
    "study type": "study_type",
    "number of conditions": "study_design_overview",
    "conditions": "study_design_overview",
    # variables
    "manipulated variables": "manipulated_variables",
    "measured variables": "measured_variables",
    "independent variables": "design_independent_variables",
    "dependent variables": "design_dependent_variables",
    "dependent variable": "design_dependent_variables",
    "dependent": "design_dependent_variables",
    "indices": "indices",
    # blinding / randomisation
    "blinding of experimental treatments": "blinding",
    "randomization": "randomization",
    # data
    "existing data": "existing_data",
    "explanation of existing data": "existing_data_explanation",
    "data collection procedures": "data_collection_procedures",
    "data collection": "data_collection_started",
    "data": "data_collection_started",
    # sample size
    "sample size": "sample_size",
    "sample size rationale": "sample_size_rationale",
    "sampling and sample size": "sample_size",
    "my target sample size is": "sample_size",
    "the rationale for my sample size is": "sample_size_rationale",
    "sample": "sample_size",
    "stopping rule": "stopping_rule",
    "stopping criteria": "stopping_rule",
    "starting and stopping rules": "stopping_rule",
    # analysis
    "statistical models": "statistical_tests",
    "statistical technique": "statistical_tests",
    "analyses": "statistical_tests",
    "analyses2": "additional_analyses",
    "transformations": "transformations",
    "data transformations": "transformations",
    "planned data transformations": "transformations",
    "inference criteria": "inference_criteria",
    "method of correction": "multiple_testing_correction",
    "reliability criteria": "reliability_criteria",
    "exploratory analysis": "exploratory_analyses",
    "other planned analysis": "exploratory_analyses",
    # exclusions / missing data / outliers
    "data exclusion": "data_exclusion_criteria",
    "data inclusion and exclusion": "data_exclusion_criteria",
    "inclusion and exclusion criteria": "data_exclusion_criteria",
    "specific exclusion criteria": "data_exclusion_criteria",
    "outliers": "outliers_and_exclusions",
    "outliers and exclusions": "outliers_and_exclusions",
    "missing data": "missing_data_handling",
    # replication
    "replication importance": "replication_importance",
    # other
    "other": "additional_comments",
    "additional information": "additional_comments",
    "context and additional information": "additional_comments",
}


# ---------------------------------------------------------------------------
# AsPredicted
# ---------------------------------------------------------------------------

#: ap_schema() output column -> aspredicted_info() column (``None``: a constant).
_AP_COLUMNS: tuple[tuple[str, str | None], ...] = (
    ("template_name", None),
    ("id", None),
    ("link", "ap_url"),
    ("title", "AP_title"),
    ("date_created", "AP_created"),
    ("existing_data_explanation", "AP_data"),
    ("research_questions", "AP_hypotheses"),
    ("design_dependent_variables", "AP_key_dv"),
    ("study_design_overview", "AP_conditions"),
    ("statistical_tests", "AP_analyses"),
    ("outliers_and_exclusions", "AP_outliers"),
    ("sample_size", "AP_sample_size"),
    ("additional_comments", "AP_anything_else"),
)


def ap_schema(table_ap: pd.DataFrame) -> pd.DataFrame:
    """Port of inst/modules/prereg_check.R::ap_schema().

    :func:`pytacheck.archives.aspredicted.aspredicted_info` output in the
    standard field names (an empty data frame for no rows). Fields missing
    from the input are left out, as ``data.frame(x = NULL)`` does.
    """
    from pytacheck._r import sub

    if len(table_ap) == 0:
        return pd.DataFrame()
    urls = table_ap["ap_url"].tolist()
    ap_id = sub("^https://aspredicted\\.org/", "", urls)
    ap_id = sub("\\.pdf.*", "", ap_id)
    ap_id = sub("blind\\.php\\?x\\=", "", ap_id)
    out: dict[str, pd.Series] = {}
    n = len(table_ap)
    for name, source in _AP_COLUMNS:
        if name == "template_name":
            values: list[Any] = ["AsPredicted"] * n
        elif name == "id":
            values = list(ap_id)
        elif source in table_ap.columns:
            values = [None if pd.isna(v) else v for v in table_ap[source].tolist()]
        else:
            continue
        out[name] = pd.Series(values, dtype="string")
    return pd.DataFrame(out)


def frame_schema(df: pd.DataFrame) -> dict[str, str]:
    """``lapply(df, paste, collapse = "\\n\\n")``: each column as one string."""
    return {
        str(col): "\n\n".join("NA" if pd.isna(v) else str(v) for v in df[col].tolist())
        for col in df.columns
    }


def flatten_schema(schema: Schema) -> dict[str, str]:
    """``lapply(x, paste, collapse = "\\n\\n")``: each field as one string."""
    return {k: paste_collapse(v, "\n\n") for k, v in schema.items()}


# ---------------------------------------------------------------------------
# common fields and the dedicated extractors
# ---------------------------------------------------------------------------


def common_osf(info: Mapping[str, Any]) -> Schema:
    """Port of inst/modules/prereg_check.R::common_osf(): fields every registration has."""
    ra = dollar(info, "attributes")
    ident = simplify(dollar(info, "id"))
    return {
        "template_name": simplify(dollar(ra, "registration_supplement")),
        "title": simplify(dollar(ra, "title")),
        "id": ident,
        "link": RVec("character", tuple("https://osf.io/" + s for s in _chr_values(ident) or [""])),
        "date_created": simplify(dollar(ra, "date_created")),
        "date_modified": simplify(dollar(ra, "date_modified")),
        "date_registered": simplify(dollar(ra, "date_registered")),
        "embargo_end_date": simplify(dollar(ra, "embargo_end_date")),
        "ia_url": simplify(dollar(ra, "ia_url")),
    }


def withdrawn(info: Mapping[str, Any]) -> Schema:
    """Port of inst/modules/prereg_check.R::withdrawn(): a withdrawn registration."""
    return {**common_osf(info), "description": RVec("character", ("WITHDRAWN",))}


#: prsp() output field -> the response keys pasted into it (``None``: taken as is).
_PRSP_FIELDS: tuple[tuple[str, tuple[str, ...] | str], ...] = (
    ("research_questions", ("description-hypothesis.question1a", "84-5")),
    ("hypotheses_interactions", ("description-hypothesis.question2a", "84-7")),
    ("manipulation_checks", ("description-hypothesis.question3a", "84-9")),
    (
        "theoretical_rationale",
        (
            "recommended-hypothesis.question5a",
            "recommended-hypothesis.question6a",
            "84-14",
            "84-16",
        ),
    ),
    ("design_independent_variables", ("description-methods.design.question2a", "84-23")),
    ("design_dependent_variables", ("description-methods.design.question2b", "84-25")),
    ("design_covariates_moderators", "description-methods.design.question3b"),
    ("data_exclusion_criteria", ("description-methods.planned-sample.question4b", "84-30")),
    (
        "data_collection_procedures",
        (
            "description-methods.planned-sample.question5b",
            "description-methods.procedure.question10b",
            "84-32",
            "84-44",
            "84-47",
            "84-49",
        ),
    ),
    (
        "sample_size",
        ("description-methods.planned-sample.question6b", "84-34", "84-36"),
    ),
    ("stopping_rule", ("description-methods.planned-sample.question7b", "84-38")),
    (
        "outliers_and_exclusions",
        ("description-methods.exclusion-criteria.question8b", "84-41"),
    ),
    ("fail_safe_exclusion_levels", "recommended-methods.procedure.question9b"),
    (
        "indices",
        (
            "confirmatory-analyses-first.first.question1c",
            "confirmatory-analyses-second.second.question1c",
            "confirmatory-analyses-third.third.question1c",
            "confirmatory-analyses-fourth.fourth.question1c",
            "confirmatory-analyses-further.further.question1c",
            "84-56",
            "84-68",
            "84-80",
            "84-92",
        ),
    ),
    (
        "statistical_tests",
        (
            "confirmatory-analyses-first.first.question2c",
            "confirmatory-analyses-second.second.question2c",
            "confirmatory-analyses-third.third.question2c",
            "confirmatory-analyses-fourth.fourth.question2c",
            "confirmatory-analyses-further.further.question2c",
            "84-58",
            "84-70",
            "84-82",
            "84-94",
            "84-126",
        ),
    ),
    (
        "rationale_covariate",
        (
            "confirmatory-analyses-first.first.question3c",
            "confirmatory-analyses-second.second.question3c",
            "confirmatory-analyses-third.third.question3c",
            "confirmatory-analyses-fourth.fourth.question3c",
            "confirmatory-analyses-further.further.question3c",
            "84-62",
            "84-74",
            "84-84",
            "84-96",
        ),
    ),
    (
        "variables_roles_in_analyses",
        (
            "confirmatory-analyses-first.first.question4c",
            "confirmatory-analyses-second.second.question4c",
            "confirmatory-analyses-third.third.question4c",
            "confirmatory-analyses-fourth.fourth.question4c",
            "confirmatory-analyses-further.further.question4c",
            "84-60",
            "84-72",
            "84-86",
            "84-98",
        ),
    ),
    (
        "inference_criteria",
        (
            "confirmatory-analyses-first.first.question5c",
            "confirmatory-analyses-second.second.question5c",
            "confirmatory-analyses-third.third.question5c",
            "confirmatory-analyses-fourth.fourth.question5c",
            "confirmatory-analyses-further.further.question5c",
            "84-64",
            "84-76",
            "84-88",
            "84-100",
        ),
    ),
    ("multiple_testing_correction", ("recommended-analysis.specify.question6c", "84-116")),
    ("missing_data_handling", ("recommended-analysis.specify.question7c", "84-118")),
    ("reliability_criteria", ("recommended-analysis.specify.question8c", "84-120")),
    ("transformations", ("recommended-analysis.specify.question9c", "84-122")),
    (
        "assumptions_and_contingencies",
        ("recommended-analysis.specify.question10c", "84-124"),
    ),
    ("data_collection_started", ("datacompletion", "84-130")),
    ("data_looked", ("looked", "84-134")),
    ("project_dates_start_end", ("dataCollectionDates", "84-138")),
    ("additional_comments", ("additionalComments", "84-140")),
)


def prsp(info: Mapping[str, Any]) -> Schema:
    """Port of inst/modules/prereg_check.R::prsp().

    Pre-Registration in Social Psychology (van 't Veer & Giner-Sorolla,
    2016), original pages version: answers are read from fixed response keys
    (``$`` partial matching included) and pasted together per field.
    """
    answers = _path(info, "attributes", "registration_responses")
    extra: Schema = {}
    for field, keys in _PRSP_FIELDS:
        if isinstance(keys, str):
            extra[field] = simplify(dollar(answers, keys))
        else:
            combined = r_c([simplify(dollar(answers, k)) for k in keys])
            extra[field] = RVec("character", (paste_collapse(combined, " "),))
    return {**common_osf(info), **extra}


#: The columns of R's ``prereg_schema`` template (all character, one ``NA`` row).
PREREG_SCHEMA_FIELDS: tuple[str, ...] = (
    "id", "date_created", "template_name", "registration_narrative_summary", "title",
    "authors", "description", "research_questions", "hypotheses_main",
    "hypotheses_interactions", "manipulation_checks", "theoretical_rationale",
    "additional_comments", "project_dates_start_end", "study_type", "study_design_overview",
    "design_independent_variables", "design_dependent_variables",
    "design_covariates_moderators", "blinding", "randomization", "manipulated_variables",
    "measured_variables", "indices", "existing_data", "existing_data_explanation",
    "data_collection_procedures", "data_collection_location", "data_collection_started",
    "data_looked", "sample_size", "sample_size_rationale", "stopping_rule",
    "fail_safe_exclusion_levels", "statistical_tests", "additional_analyses",
    "transformations", "inference_criteria", "multiple_testing_correction",
    "assumptions_and_contingencies", "variables_roles_in_analyses", "rationale_covariate",
    "reliability_criteria", "data_exclusion_criteria", "outliers_and_exclusions",
    "missing_data_handling", "exploratory_analyses", "replication_description",
    "replication_importance", "effect_size_original", "confidence_interval_original",
    "original_study_conducted", "region", "original_sample_size", "original_population",
    "original_data_collection", "original_materials_available", "instruction_similarities",
    "measure_similarities", "stimuli_similarities", "procedure_similarities",
    "location_similarities", "remuneration_similarities", "participant_similarities",
    "differences_influencing_effects", "date_modified", "date_registered",
    "embargo_end_date", "ia_url",
)  # fmt: skip


def prereg_schema() -> pd.DataFrame:
    """R's ``prereg_schema``: a one-row, all-``NA`` template of the standard fields."""
    return pd.DataFrame({f: pd.Series([None], dtype="string") for f in PREREG_SCHEMA_FIELDS})
