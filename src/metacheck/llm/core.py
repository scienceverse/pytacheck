"""Querying LLMs (port of ``R/llm.R`` and the LLM defaults of ``R/zzz.R``).

:func:`llm` sends each unique text to a model and returns one row per input
(or, for structured extraction, one row per extracted item), like metacheck's
``llm()``: same arguments and defaults (temperature 0, ``max_tokens`` 4096 or
:func:`llm_max_tokens`, reasoning effort from :func:`llm_reasoning`), same
retry of malformed structured replies, same per-row error columns and
warnings, a response cache (:mod:`metacheck.llm.cache`), and the same session
settings, stored in the R option names metacheck uses (``metacheck.llm.use``,
``metacheck.llm.model``, ``metacheck.llm_max_calls``, ``metacheck.llm_max_tokens``,
``metacheck.llm_timeout``, ``metacheck.llm_reasoning``).

Models are reached through the official provider SDKs (``pip install
"metacheck[llm]"``, see :mod:`metacheck.llm._backend`) and are named
``"provider/model"`` (``"groq/openai/gpt-oss-20b"``,
``"google_gemini/gemini-2.5-flash"``, ``"ollama/qwen3:8b"``,
``"vllm/<model>"``, ...).
"""

from __future__ import annotations

import itertools
import math
import re
import sys
import time
import unicodedata
import warnings
from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, cast

from metacheck._env import env_get
from metacheck.llm._onload import _API_KEY_ENV, _default_model_from_env  # noqa: F401  (re-exported)

if TYPE_CHECKING:
    import pandas as pd

__all__ = [
    "llm",
    "llm_max_calls",
    "llm_max_tokens",
    "llm_model",
    "llm_reasoning",
    "llm_timeout",
    "llm_use",
]

_MISSING: Any = object()


def _message(msg: str, always: bool = False) -> None:
    """R ``message()``: to stderr (progress chatter only when verbose)."""
    if not always:
        from metacheck.config import verbose

        if not verbose():
            return
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# options (R/zzz.R .onLoad)
# ---------------------------------------------------------------------------


def _init_options() -> None:
    """metacheck's ``.onLoad()`` default model (R/zzz.R); see :func:`metacheck.utils._init_default_model`.

    :mod:`metacheck.utils` applies it when it loads, so the option is set before
    any LLM code runs; calling this again (tests do) re-applies it.
    """
    from metacheck import utils

    utils._init_default_model()


def _get(name: str, default: Any = None) -> Any:
    from metacheck.utils import get_option

    return get_option(name, default)


def _set(name: str, value: Any) -> None:
    from metacheck.utils import options

    options({name: value})


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------


def _r_as_logical(x: Any) -> bool | None:
    if isinstance(x, bool):
        return x
    if isinstance(x, int | float):
        return None if isinstance(x, float) and math.isnan(x) else bool(x)
    if isinstance(x, str):
        if x in ("TRUE", "true", "True", "T"):
            return True
        if x in ("FALSE", "false", "False", "F"):
            return False
    return None


def llm_use(llm_use: Any = None) -> bool:
    """Port of ``llm_use()``: get or set whether LLM workflows are enabled."""
    if llm_use is None:
        return bool(_get("metacheck.llm.use"))
    val = _r_as_logical(llm_use)
    if val is None:
        raise ValueError("Set llm_use with TRUE or FALSE")
    _set("metacheck.llm.use", val)
    return bool(_get("metacheck.llm.use"))


def llm_model(model: Any = _MISSING) -> str | None:
    """Port of ``llm_model()``: get (no argument), set, or unset (``None``) the default model."""
    if model is _MISSING:
        return _get("metacheck.llm.model")  # type: ignore[no-any-return]
    if model is None:
        _set("metacheck.llm.model", None)
        return _get("metacheck.llm.model")  # type: ignore[no-any-return]
    if isinstance(model, str) or (
        isinstance(model, list | tuple) and all(isinstance(m, str) for m in model)
    ):
        _set("metacheck.llm.model", model)
        return _get("metacheck.llm.model")  # type: ignore[no-any-return]
    raise ValueError("set llm_model with the name of a model, as 'provider/model'")


def _is_numeric(n: Any) -> bool:
    return isinstance(n, int | float) and not isinstance(n, bool)


def _set_count(option: str, n: Any) -> Any:
    if not _is_numeric(n):
        raise ValueError("n must be a number")
    if isinstance(n, float) and math.isnan(n):
        raise ValueError("missing value where TRUE/FALSE needed")
    if n >= 2147483648 or n <= -2147483648:
        # as.integer() gives NA outside R's integer range; `if (NA < 1)` fails
        warnings.warn("NAs introduced by coercion to integer range", stacklevel=3)
        raise ValueError("missing value where TRUE/FALSE needed")
    value = int(n)  # as.integer() truncates toward zero
    if value < 1:
        current = _get(option)
        warnings.warn(
            f"n must be greater than 0; it was not changed from {'' if current is None else current}",
            stacklevel=3,
        )
    else:
        _set(option, value)
    return _get(option)


def llm_max_calls(n: Any = None) -> int:
    """Port of ``llm_max_calls()``: the maximum number of calls one ``llm()`` may make."""
    if n is None:
        return _get("metacheck.llm_max_calls")  # type: ignore[no-any-return]
    return _set_count("metacheck.llm_max_calls", n)  # type: ignore[no-any-return]


def llm_max_tokens(n: Any = None) -> int | None:
    """Port of ``llm_max_tokens()``: the session default ``max_tokens`` (``None`` = 4096)."""
    if n is None:
        return _get("metacheck.llm_max_tokens")  # type: ignore[no-any-return]
    return _set_count("metacheck.llm_max_tokens", n)  # type: ignore[no-any-return]


def llm_timeout(seconds: Any = None) -> float:
    """Port of ``llm_timeout()``: per-request timeout in seconds (default 180).

    It bounds every request ``llm()`` makes, as documented; metacheck applies
    it to native Ollama requests only, while ellmer's hosted providers wait
    300 s (U20).
    """
    if seconds is None:
        return _get("metacheck.llm_timeout", 180)  # type: ignore[no-any-return]
    if (
        not _is_numeric(seconds)
        or (isinstance(seconds, float) and math.isnan(seconds))
        or seconds <= 0
    ):
        raise ValueError("seconds must be a single positive number")
    _set("metacheck.llm_timeout", seconds)
    return _get("metacheck.llm_timeout")  # type: ignore[no-any-return]


def llm_reasoning(effort: str | None = None) -> str | None:
    """Port of ``llm_reasoning()``: default reasoning effort (low/medium/high/none)."""
    if effort is None:
        return _get("metacheck.llm_reasoning")  # type: ignore[no-any-return]
    effort = _r_match_arg(effort, ["low", "medium", "high", "none"])
    _set("metacheck.llm_reasoning", effort)
    return _get("metacheck.llm_reasoning")  # type: ignore[no-any-return]


def _r_match_arg(arg: Any, choices: Sequence[str]) -> str:
    """R ``match.arg(arg, choices)`` (a non-``NULL`` *arg*)."""
    if isinstance(arg, str):
        vals: list[Any] = [arg]
    elif isinstance(arg, list | tuple) and all(isinstance(a, str | None) for a in arg):
        vals = list(arg)
    else:
        raise ValueError("'arg' must be NULL or a character vector")
    if vals == list(choices):
        return choices[0]
    if len(vals) > 1:
        raise ValueError("'arg' must be of length 1")
    if vals and isinstance(vals[0], str) and vals[0]:
        a = vals[0]
        if a in choices:
            return a
        hits = [c for c in choices if c.startswith(a)]
        if len(hits) == 1:
            return hits[0]
    quoted = ", ".join(f"“{c}”" for c in choices)
    raise ValueError(f"'arg' should be one of {quoted}")


def _llm_apply_reasoning(
    params_list: Mapping[str, Any], model: str, api_args: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Port of ``.llm_apply_reasoning()``.

    Translates :func:`llm_reasoning` (``"low"`` when unset) into what the
    model family understands; returns ``{"params_list": ..., "api_args": ...}``.
    """
    from metacheck._r import grepl

    params_out = dict(params_list)
    args_out = dict(api_args or {})
    out = {"params_list": params_out, "api_args": args_out}
    effort = _get("metacheck.llm_reasoning")
    if effort is None:
        effort = "low"
    m = model.lower()
    is_gpt_oss = "gpt-oss" in m
    is_qwen3 = "qwen3" in m
    is_mistral = bool(grepl("^mistral", [m])[0])
    is_ollama = bool(grepl("^ollama", [m])[0])
    if is_mistral and _r_dollar(args_out, "reasoning_effort") is None:
        args_out["reasoning_effort"] = "high" if effort == "high" else "none"
        return out
    if is_ollama and (is_qwen3 or "deepseek-r1" in m):
        if effort in ("low", "none") and _r_dollar(params_out, "think") is None:
            params_out["think"] = False
        return out
    if is_qwen3 and _r_dollar(args_out, "reasoning_effort") is None:
        args_out["reasoning_effort"] = "none" if effort in ("low", "none") else "default"
        return out
    if is_gpt_oss and _r_dollar(args_out, "reasoning_effort") is None:
        args_out["reasoning_effort"] = "low" if effort == "none" else effort
        return out
    return out


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _llm_sanitise_text(x: Any) -> list[str | None] | None:
    """Port of ``.llm_sanitise_text()``: text that is safe inside a JSON request body.

    Invalid UTF-8 (undecodable bytes, lone surrogates) is reinterpreted as
    Latin-1 and C0 control characters other than tab and newline are removed.
    """
    from metacheck._r import as_character, gsub

    if x is None:
        return None
    vals = list(x) if isinstance(x, list | tuple) else [x]
    if not vals:
        return []
    out: list[str | None] = []
    for v in vals:
        if isinstance(v, bytes):
            try:
                s: str | None = v.decode("utf-8")
            except UnicodeDecodeError:
                s = v.decode("latin-1")
        else:
            s = as_character(v)
        if s is not None:
            try:
                s.encode("utf-8")
            except UnicodeEncodeError:
                raw = s.encode("utf-8", "surrogateescape")
                try:
                    s = raw.decode("utf-8")
                except UnicodeDecodeError:
                    s = raw.decode("latin-1")
        out.append(s)
    return gsub("[\\x01-\\x08\\x0B\\x0C\\x0E-\\x1F]", "", out, perl=True)  # type: ignore[no-any-return]


def _r_dollar(x: Any, name: str) -> Any:
    """R ``x$name`` on parsed JSON: exact, else unique partial match; ``NULL`` if none."""
    if x is None or isinstance(x, list):  # NULL$x, unnamed list$x: NULL
        return None
    if not isinstance(x, dict):
        raise TypeError("$ operator is invalid for atomic vectors")
    if name in x:
        return x[name]
    hits = [k for k in x if k.startswith(name)]
    return x[hits[0]] if len(hits) == 1 else None


def _llm_error_message(e: BaseException) -> str:
    """Port of ``.llm_error_message()``: the error text plus the provider's own reason."""
    msg = str(e)
    detail = getattr(e, "detail", None)
    if not isinstance(detail, str) or not detail:
        return msg
    if len(detail) > 500:
        detail = detail[:500] + " [truncated]"
    return f"{msg}\n  Provider says: {detail}"


# a reply that is not valid JSON ("Failed to parse JSON", and the wording of
# jsonlite's errors in metacheck) or does not fit the schema is asked for again
_RETRYABLE = re.compile(
    "Failed to (generate|validate|parse) JSON|lexical error|malformed number|premature EOF|"
    "parse error|```json",
    re.IGNORECASE,
)
_TRANSPORT = re.compile(
    "could not resolve|connection refused|couldn't connect|timed out|timeout|"
    "connection reset|network is unreachable|no route to host|failed to connect|"
    "empty reply from server|handshake|ssl",
    re.IGNORECASE,
)


def _llm_json_retryable(e: BaseException) -> bool:
    """Port of ``.llm_json_retryable()``: a structured-output failure worth retrying."""
    return _RETRYABLE.search(_llm_error_message(e)) is not None


def _llm_is_systemic_error(e: BaseException) -> bool:
    """Port of ``.llm_is_systemic_error()``: will every call this run fail this way?

    A rejected key or a failing server is; so is a connection that never
    answered, unless it only ran out of time on this one request.
    """
    status = getattr(e, "status", None)
    if status is not None:  # the provider answered: only its authentication and 5xx errors
        return bool(status in (401, 403) or status >= 500)
    if getattr(e, "timeout", False) or _llm_json_retryable(e):
        return False
    return _TRANSPORT.search(str(e)) is not None


class _SystemicNotice:
    """``.llm_systemic_notice``: say once per session that the LLM is unavailable."""

    def __init__(self) -> None:
        self.done = False

    def trip(self, msg: str) -> None:
        if not self.done:
            _message(msg, always=True)
            self.done = True

    def reset(self) -> None:
        self.done = False


_llm_systemic_notice = _SystemicNotice()


# ---------------------------------------------------------------------------
# R data-frame semantics for structured results
# ---------------------------------------------------------------------------

_RESERVED = {
    "if", "else", "repeat", "while", "function", "for", "next", "break", "TRUE", "FALSE",
    "NULL", "Inf", "NaN", "NA", "NA_integer_", "NA_real_", "NA_character_", "NA_complex_", "in",
}  # fmt: skip


class _RList(list):  # type: ignore[type-arg]
    """An R ``list()``: a plain Python list of scalars stands for an atomic vector instead."""


def _make_names(names: Sequence[str], unique: bool = True) -> list[str]:
    """R ``make.names(names, unique = TRUE)``."""
    out = []
    for n in names:
        # "X" is prefixed when the *original* name does not start with a letter
        # or a dot not followed by a digit; then invalid characters become "."
        if not n or not (n[0].isalpha() or n[0] == ".") or (n[0] == "." and n[1:2].isdigit()):
            n = "X" + n
        s = "".join(c if (c.isalnum() or c in "._") else "." for c in n)
        if s in _RESERVED:
            s += "."
        out.append(s)
    return _make_unique(out) if unique else out


def _make_unique(names: Sequence[str], sep: str = ".") -> list[str]:
    """R ``make.unique()``."""
    seen: set[str] = set(names)
    counts: dict[str, int] = {}
    out: list[str] = []
    used: set[str] = set()
    for n in names:
        if n not in used:
            used.add(n)
            out.append(n)
            continue
        k = counts.get(n, 0)
        while True:
            k += 1
            cand = f"{n}{sep}{k}"
            if cand not in seen and cand not in used:
                break
        counts[n] = k
        used.add(cand)
        out.append(cand)
    return out


_STRING_ESCAPES = {
    '"': '\\"', "\\": "\\\\", "\n": "\\n", "\t": "\\t", "\r": "\\r",
    "\a": "\\a", "\b": "\\b", "\f": "\\f", "\v": "\\v",
}  # fmt: skip


def _encode_string(s: str) -> str:
    """R ``encodeString(s, quote = '"')`` in a UTF-8 locale."""
    out = ['"']
    for c in s:
        if c in _STRING_ESCAPES:
            out.append(_STRING_ESCAPES[c])
        elif ord(c) < 0x20 or ord(c) == 0x7F:
            out.append(f"\\{ord(c):03o}")
        elif ord(c) > 0x7F and unicodedata.category(c) in ("Cc", "Cf", "Co", "Cn", "Cs"):
            out.append(f"\\u{ord(c):04x}" if ord(c) <= 0xFFFF else f"\\U{ord(c):08x}")
        else:
            out.append(c)
    out.append('"')
    return "".join(out)


_NA_DEPARSE = {"chr": "NA_character_", "int": "NA_integer_", "dbl": "NA_real_", "lgl": "NA"}
_EMPTY_DEPARSE = {
    "chr": "character(0)",
    "int": "integer(0)",
    "dbl": "numeric(0)",
    "lgl": "logical(0)",
}


def _deparse_arg(v: Any) -> str:
    """``deparse()`` of an unnamed ``data.frame()`` argument (its column name)."""
    import pandas as pd

    if isinstance(v, list | tuple):
        v = _list_series(list(v))
    elif not isinstance(v, pd.Series):
        v = _scalar_series(v)
    return _deparse_vec(v)


def _deparse_vec(s: pd.Series[Any]) -> str:
    """``deparse()`` of an atomic vector."""
    import pandas as pd

    from metacheck._r import as_character

    kind = _col_kind(s)
    if kind == "fct":
        kind = "chr"
    vals: list[Any] = [None if pd.isna(x) else x for x in s.astype(object).tolist()]
    if not vals:
        return _EMPTY_DEPARSE.get(kind, "NULL")
    if len(vals) == 1 and vals[0] is None:
        return _NA_DEPARSE.get(kind, "NA")

    def one(x: Any) -> str:
        if x is None:
            return "NA"
        if kind == "chr":
            return _encode_string(str(x))
        if kind == "lgl":
            return "TRUE" if x else "FALSE"
        if kind == "int":
            return f"{int(x)}L"
        return as_character(float(x)) or "NA"

    if (
        kind == "int"
        and len(vals) > 1
        and None not in vals
        and all(b - a == 1 for a, b in itertools.pairwise(vals))
    ):
        return f"{vals[0]}:{vals[-1]}"
    if len(vals) == 1:
        return one(vals[0])
    return "c(" + ", ".join(one(x) for x in vals) + ")"


def _is_df(x: Any) -> bool:
    import pandas as pd

    return isinstance(x, pd.DataFrame)


def _is_list(x: Any) -> bool:
    """R ``is.list()`` (data frames included)."""
    import pandas as pd

    return isinstance(x, dict | list | tuple | pd.DataFrame)


def _list_items(x: Any) -> list[tuple[str | None, Any]]:
    import pandas as pd

    if isinstance(x, dict):
        return list(x.items())
    if isinstance(x, pd.DataFrame):
        return [(str(c), x[c]) for c in x.columns]
    return [(None, v) for v in x]


def _scalar_series(v: Any, n: int = 1) -> pd.Series[Any]:
    import pandas as pd

    if v is None or v is pd.NA:
        return pd.Series([pd.NA] * n, dtype="boolean")
    if isinstance(v, bool):
        return pd.Series([v] * n, dtype="boolean")
    if isinstance(v, int):
        return pd.Series([v] * n, dtype="Int64")
    if isinstance(v, float):
        return pd.Series([v] * n, dtype="float64")
    return pd.Series([str(v)] * n, dtype="string")


def _is_scalar(v: Any) -> bool:
    import pandas as pd

    return v is None or v is pd.NA or isinstance(v, str | bool | int | float)


def _list_series(vals: list[Any]) -> pd.Series[Any]:
    """An R atomic vector from Python scalars (``c(...)`` coercion rules)."""
    import pandas as pd

    from metacheck._r import as_character

    present = [v for v in vals if v is not None and v is not pd.NA]
    na = [None if (v is None or v is pd.NA) else v for v in vals]
    if all(isinstance(v, bool) for v in present):
        return pd.Series(na, dtype="boolean")
    if all(isinstance(v, int) and not isinstance(v, bool) for v in present):
        return pd.Series(na, dtype="Int64")
    if all(isinstance(v, int | float) and not isinstance(v, bool) for v in present):
        return pd.Series([float("nan") if v is None else float(v) for v in na], dtype="float64")
    return pd.Series([None if v is None else as_character(v) for v in na], dtype="string")


def _as_data_frame(x: Any, optional: bool = False) -> pd.DataFrame:
    """R ``as.data.frame()`` of a list (``data.frame(...)`` column semantics)."""
    import pandas as pd

    if _is_df(x):
        return cast("pd.DataFrame", x.reset_index(drop=True))
    if not _is_list(x):
        s = x.reset_index(drop=True) if isinstance(x, pd.Series) else _scalar_series(x)
        return pd.DataFrame({"result": s})
    names: list[str] = []
    cols: list[pd.Series[Any]] = []
    nrows: list[int] = []
    groups: list[tuple[int, int]] = []  # column span of each argument
    for name, v in _list_items(x):
        start = len(cols)
        if v is None:  # a NULL argument: no columns, but 0 rows
            nrows.append(0)
            groups.append((start, start))
            continue
        if (
            _is_df(v)
            or isinstance(v, dict | _RList)
            or (isinstance(v, list | tuple) and not all(_is_scalar(e) for e in v))
        ):
            # a list/data frame argument: its columns (unnamed elements are
            # named by deparse()), prefixed with the argument's name when it
            # has more than one
            sub = _as_data_frame(v, optional=True)
            inner = [str(c) for c in sub.columns]
            labels = inner if len(inner) <= 1 or not name else [f"{name}.{c}" for c in inner]
            for j, lab in enumerate(labels):  # by position: names may repeat
                cols.append(sub.iloc[:, j].reset_index(drop=True))
                names.append(lab)
            nrows.append(len(sub))
        else:
            if isinstance(v, list | tuple):
                # a Python list of scalars is an atomic vector (as jsonlite/yaml simplify)
                s = _list_series(list(v))
            elif isinstance(v, pd.Series):
                s = v.reset_index(drop=True)
            else:
                s = _scalar_series(v)
            cols.append(s)
            names.append(name if name else _deparse_arg(v))
            nrows.append(len(s))
        groups.append((start, len(cols)))
    if not cols and not nrows:
        return pd.DataFrame()
    nr = max(nrows) if nrows else 0
    for (start, end), n in zip(groups, nrows, strict=True):
        if n < nr:
            if n > 0 and nr % n == 0:
                for j in range(start, end):
                    cols[j] = pd.concat([cols[j]] * (nr // n), ignore_index=True)
                continue
            uniq = list(dict.fromkeys(nrows))
            raise ValueError(
                "arguments imply differing number of rows: " + ", ".join(str(k) for k in uniq)
            )
    if not optional:
        names = _make_names(names)
    out = pd.DataFrame(dict(enumerate(cols)))
    out.columns = pd.Index(names)
    return out


def _col_kind(s: pd.Series[Any]) -> str:
    import pandas as pd

    dt = s.dtype
    if isinstance(dt, pd.CategoricalDtype):
        return "fct"
    if pd.api.types.is_bool_dtype(dt):
        return "lgl"
    if pd.api.types.is_integer_dtype(dt):
        return "int"
    if pd.api.types.is_float_dtype(dt):
        return "dbl"
    if pd.api.types.is_string_dtype(dt) and not pd.api.types.is_object_dtype(dt):
        return "chr"
    if pd.api.types.is_object_dtype(dt):
        # an object column of scalars is the R atomic vector it holds (pandas
        # code often builds text columns with dtype=object)
        inferred = pd.api.types.infer_dtype(s, skipna=True)
        return _INFERRED_KIND.get(inferred, "list")
    return "list"


_INFERRED_KIND = {
    "string": "chr",
    "boolean": "lgl",
    "integer": "int",
    "floating": "dbl",
    "mixed-integer-float": "dbl",
    "empty": "lgl",
}


def _bind_rows_r(frames: Sequence[Any]) -> pd.DataFrame:
    """``dplyr::bind_rows()`` with vctrs' type combination rules."""
    import pandas as pd

    parts = [f for f in frames if f is not None]
    if not parts:
        return pd.DataFrame()
    columns: list[str] = []
    for f in parts:
        for c in f.columns:
            if c not in columns:
                columns.append(c)
    total = sum(len(f) for f in parts)
    out: dict[str, pd.Series[Any]] = {}
    for c in columns:
        pieces = []
        kinds = []
        for i, f in enumerate(parts):
            if c in f.columns:
                s = f[c].reset_index(drop=True)
                k = _col_kind(s)
                if k == "lgl" and s.isna().all():
                    k = "na"
                pieces.append(s)
                kinds.append((i, k))
            else:
                pieces.append(pd.Series([pd.NA] * len(f), dtype="boolean"))
                kinds.append((i, "na"))
        real = [k for _, k in kinds if k != "na"]
        target = _combine_kinds(real, c)
        converted = [_convert_kind(p, target) for p in pieces]
        if target == "fct":
            levels: list[str] = []
            for p in converted:
                for lv in p.cat.categories:
                    if lv not in levels:
                        levels.append(lv)
            vals = [v for p in converted for v in p.astype(object).tolist()]
            out[c] = pd.Series(pd.Categorical(vals, categories=levels))
        elif converted:
            out[c] = (
                pd.concat(converted, ignore_index=True)
                if total
                else converted[0].iloc[0:0].reset_index(drop=True)
            )
    return pd.DataFrame(out, columns=columns) if out else pd.DataFrame(columns=columns)


_KIND_DTYPE = {"lgl": "boolean", "int": "Int64", "dbl": "float64", "chr": "string", "na": "boolean"}


def _combine_kinds(kinds: list[str], col: str) -> str:
    if not kinds:
        return "na"
    ks = set(kinds)
    if len(ks) == 1:
        return kinds[0]
    if ks <= {"int", "dbl"}:
        return "dbl"
    if ks <= {"fct", "chr"}:
        return "chr"
    if "list" in ks:
        return "list"
    first, other = kinds[0], next(k for k in kinds if k != kinds[0])
    names = {
        "lgl": "logical",
        "int": "integer",
        "dbl": "double",
        "chr": "character",
        "fct": "factor<>",
    }
    raise ValueError(
        f"Can't combine `..1${col}` <{names.get(first, first)}> and `..2${col}` "
        f"<{names.get(other, other)}>."
    )


def _convert_kind(s: pd.Series[Any], target: str) -> pd.Series[Any]:
    import pandas as pd

    if target == "fct":
        if isinstance(s.dtype, pd.CategoricalDtype):
            return s
        return pd.Series(pd.Categorical([None] * len(s)))
    if target == "list":
        return s.astype(object)
    if target == "chr" and isinstance(s.dtype, pd.CategoricalDtype):
        return s.astype(object).astype("string")
    dtype = _KIND_DTYPE.get(target)
    if dtype is None:
        return s
    if _col_kind(s) == "lgl" and s.isna().all():
        na = pd.Series([pd.NA] * len(s), dtype="Float64" if dtype == "float64" else dtype)
        return na.astype(cast(Any, dtype))
    return s.astype(cast(Any, dtype))


def _unnest_result(result: Any) -> pd.DataFrame:
    """Port of ``.unnest_result()``: a structured LLM result as a data frame.

    A data frame passes through; an object with a single array-of-objects
    field unnests into one row per item; anything else becomes one row
    (``as.data.frame()``, so a wrapped data frame gives ``field.column`` names).
    """
    import pandas as pd

    if _is_df(result):
        return cast("pd.DataFrame", result.reset_index(drop=True))
    if _is_list(result) and len(result) == 1:
        inner = _list_items(result)[0][1]
        if _is_list(inner) and not _is_df(inner):
            items = [v for _, v in _list_items(inner)]
            if len(items) == 0:
                return pd.DataFrame()
            if all(_is_list(item) for item in items):
                return _bind_rows_r([_as_data_frame(_null_to_na(item)) for item in items])
    if isinstance(result, dict | list | tuple):
        result = _null_to_na(result)
    elif result is None:
        raise ValueError("argument is of length zero")
    return _as_data_frame(result)


def _as_rlists(x: Any) -> Any:
    """A structured result with its Python lists as R lists.

    Converted results hold atomic vectors as :class:`pandas.Series`; every
    Python list left in them (a raw JSON array, an array of arrays) is an R
    ``list()``, which ``as.data.frame()`` spreads over columns rather than rows.
    """
    if isinstance(x, dict):
        return {k: _as_rlists(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return _RList([_as_rlists(v) for v in x])
    return x


def _null_to_na(x: Any) -> Any:
    import pandas as pd

    def na(v: Any) -> Any:
        return pd.Series([pd.NA], dtype="boolean") if v is None else v

    if isinstance(x, dict):
        return {k: na(v) for k, v in x.items()}
    if isinstance(x, _RList):
        return _RList(na(v) for v in x)
    if isinstance(x, list | tuple):
        return [na(v) for v in x]
    return x


def _left_join(
    x: pd.DataFrame, y: pd.DataFrame, by: str, suffix: tuple[str, str] = (".x", ".y")
) -> pd.DataFrame:
    """``dplyr::left_join(x, y, by)`` (NA keys match; x's row order; y's order within)."""
    import numpy as np
    import pandas as pd

    def keys(s: pd.Series[Any]) -> list[Any]:
        kind = _col_kind(s)
        return [
            None if pd.isna(v) else (str(v) if kind in ("chr", "fct", "list") else v)
            for v in s.astype(object).tolist()
        ]

    kx, ky = _col_kind(x[by]), _col_kind(y[by]) if by in y.columns else "chr"
    textual = {"chr", "fct"}
    if (
        (kx in textual) != (ky in textual)
        and not (_col_kind(y[by]) == "lgl" and y[by].isna().all())
        and not (kx == "lgl" and x[by].isna().all())
    ):
        rnames = {
            "int": "integer",
            "dbl": "double",
            "lgl": "logical",
            "chr": "character",
            "fct": "factor<>",
            "list": "list",
        }
        raise TypeError(
            f"Can't join `x${by}` with `y${by}` due to incompatible types.\n"
            f"ℹ `x${by}` is a <{rnames[kx]}>.\nℹ `y${by}` is a <{rnames[ky]}>."
        )
    xk = keys(x[by])
    yk = keys(y[by])
    index: dict[Any, list[int]] = {}
    for j, k in enumerate(yk):
        index.setdefault(k, []).append(j)
    xi: list[int] = []
    yj: list[int] = []
    for i, k in enumerate(xk):
        hits = index.get(k)
        if hits:
            xi.extend([i] * len(hits))
            yj.extend(hits)
        else:
            xi.append(i)
            yj.append(-1)
    xpos = np.asarray(xi, dtype=np.intp)
    ypos = np.asarray(yj, dtype=np.intp)
    out: dict[str, pd.Series[Any]] = {}
    ycols = [c for c in y.columns if c != by]
    for c in x.columns:
        name = c + suffix[0] if (c in ycols and c != by) else c
        out[name] = x[c].reset_index(drop=True).take(xpos).reset_index(drop=True)
    for c in ycols:
        name = c + suffix[1] if c in x.columns else c
        out[name] = _take_fill(y[c].reset_index(drop=True), ypos)
    return pd.DataFrame(out)


def _take_fill(s: pd.Series[Any], pos: Any) -> pd.Series[Any]:
    import numpy as np
    import pandas as pd

    if len(s) == 0:
        kind = _col_kind(s)
        dtype: Any = s.dtype if kind != "list" else object
        if kind == "fct":
            return pd.Series(pd.Categorical([None] * len(pos), categories=s.cat.categories))
        return pd.Series([pd.NA if kind != "dbl" else np.nan] * len(pos), dtype=dtype)
    arr = s.array
    return pd.Series(arr.take(pos, allow_fill=True), dtype=s.dtype)


# ---------------------------------------------------------------------------
# llm()
# ---------------------------------------------------------------------------


def _text_frame(text: Any, text_col: str) -> pd.DataFrame:
    """``data.frame(text = text)`` named *text_col* (a data frame passes through).

    Python scalars and sequences become the R vector they denote: ints an
    integer column, numbers a double one, booleans a logical one, anything
    else character. ``None`` is R's ``NULL``, which ``data.frame()`` cannot
    name.
    """
    import numpy as np
    import pandas as pd

    from metacheck._r import as_character

    if isinstance(text, pd.DataFrame):
        return text
    if text is None:
        raise ValueError("'names' attribute [1] must be the same length as the vector [0]")
    if isinstance(text, pd.Series | pd.Index | np.ndarray):
        s = pd.Series(np.asarray(text) if isinstance(text, pd.Index) else text).reset_index(
            drop=True
        )
        kind = _col_kind(s)
        if kind in ("int", "dbl", "lgl"):
            return pd.DataFrame({text_col: s.astype(_KIND_DTYPE[kind])})  # type: ignore[call-overload]
        if kind in ("chr", "fct"):
            return pd.DataFrame({text_col: s if kind == "fct" else s.astype("string")})
        vals = s.tolist()
    elif (
        isinstance(text, str | bytes) or not isinstance(text, Iterable) or isinstance(text, Mapping)
    ):
        vals = [text]
    else:
        vals = list(text)
    kinds = {
        type(v) for v in vals if v is not None and not (isinstance(v, float) and math.isnan(v))
    }
    if kinds and kinds <= {int}:
        s = pd.Series(vals, dtype="Int64")
    elif kinds and kinds <= {int, float}:
        s = pd.Series([math.nan if v is None else v for v in vals], dtype="float64")
    elif kinds and kinds <= {bool}:
        s = pd.Series(vals, dtype="boolean")
    else:
        # bytes are text in an unknown encoding (R: a string that may not be
        # valid UTF-8); .llm_sanitise_text() reinterprets them as R does. Their
        # surrogates need Python-backed strings: Arrow's (pandas' default with
        # pyarrow installed) cannot hold them
        s = pd.Series(
            [
                v.decode("utf-8", "surrogateescape") if isinstance(v, bytes) else as_character(v)
                for v in vals
            ],
            dtype=pd.StringDtype("python"),
        )
    return pd.DataFrame({text_col: s})


def _column_values(df: pd.DataFrame, col: str) -> list[Any] | None:
    import pandas as pd

    if col not in df.columns:
        return None
    return [None if pd.isna(v) else v for v in df[col].astype(object).tolist()]


def _unique(vals: list[Any]) -> list[Any]:
    seen: set[Any] = set()
    out = []
    for v in vals:
        k = ("na",) if v is None else (type(v).__name__, v)
        if k not in seen:
            seen.add(k)
            out.append(v)
    return out


def llm(
    text: Any,
    system_prompt: str,
    type: Any = None,
    text_col: str = "text",
    model: Any = _MISSING,
    params: Mapping[str, Any] | None = None,
    phase: str | None = None,
) -> pd.DataFrame:
    """Query an LLM (port of ``llm()``).

    Parameters
    ----------
    text
        The texts to send (a string, a sequence, or a data frame with the text
        in *text_col*). Each unique text is sent once.
    system_prompt
        The system prompt.
    type
        An optional structured-output type (:mod:`metacheck.llm.types`, or a
        JSON-schema ``dict``): the reply is parsed into columns instead of a
        free-text ``answer``.
    text_col
        The text column when *text* is a data frame.
    model
        ``"provider"`` or ``"provider/model"``; defaults to :func:`llm_model`.
    params
        Model parameters (``temperature``, ``max_tokens``, ``seed``,
        ``top_p``, ``top_k``, ``stop_sequences``, ...). A parameter the
        provider's SDK does not know is sent to the provider as given.
    phase
        A label for the calling step (progress messages only).

    Returns
    -------
    pandas.DataFrame
        The input rows with ``answer`` (and ``error``/``error_msg`` when a
        call failed), or the extracted columns (and ``.error``/``.error_msg``).
        ``df.attrs["llm"]`` holds the system prompt, model and type.
    """
    import pandas as pd

    from metacheck._r import plural, trimws
    from metacheck.llm import _backend as backend
    from metacheck.llm.cache import _llm_cache_get, _llm_cache_key, _llm_cache_put, llm_cache
    from metacheck.llm.types import as_type, convert_from_type

    if not llm_use():
        raise RuntimeError("Set llm_use(TRUE) to use LLM functions")
    if model is _MISSING:
        model = llm_model()
    if not isinstance(model, str) or not model:
        raise ValueError(
            "No LLM model set. Use llm_model('provider/model') or pass model = 'provider/model'"
        )
    if params is None:
        params = {}
    if not isinstance(params, Mapping):
        raise ValueError("params must be a named list")
    params_list: dict[str, Any] = dict(params)

    text_df = _text_frame(text, text_col)
    raw_values = _column_values(text_df, text_col)
    # the texts to send: each distinct one once, never a missing one (metacheck
    # sent NA as the text "NA"; U19)
    send_raw = [v for v in _unique(raw_values) if v is not None] if raw_values else []
    unique_text = _llm_sanitise_text(send_raw)
    ncalls = len(unique_text or [])
    if ncalls == 0 and not raw_values:
        raise ValueError("No calls to the LLM")
    max_calls = llm_max_calls()
    if ncalls > max_calls:
        raise ValueError(
            f"This would make {ncalls} calls to the LLM, but your maximum number of calls is "
            f"set to {max_calls}. Set `llm_max_calls({ncalls})` (or higher) to allow this call."
        )
    assert unique_text is not None

    # `params_list$x` partially matches a longer name, as R's `$` does
    if _r_dollar(params_list, "temperature") is None:
        params_list["temperature"] = 0.0
    if _r_dollar(params_list, "max_tokens") is None:
        params_list["max_tokens"] = llm_max_tokens() or 4096

    reasoning = _llm_apply_reasoning(params_list, model, api_args={})
    params_list = reasoning["params_list"]
    api_args = reasoning["api_args"]

    structured = type is not None
    type_obj = as_type(type) if structured else None
    system = (
        "\n\n".join(system_prompt) if isinstance(system_prompt, list | tuple) else system_prompt
    )

    # R shows a progress bar labelled with the phase (or a generic label) and model
    label = phase if phase else ("Extracting data" if structured else "Querying LLM")
    label = f"{label} ({model})"
    started = time.monotonic()
    use_cache = llm_cache()

    def one(i: int, ut: str | None) -> Any:
        key = (
            _llm_cache_key(ut, system, type_obj, model, params_list, api_args)
            if use_cache
            else None
        )
        try:
            hit = _llm_cache_get(key) if key is not None else None
            if hit is not None:
                reply = hit["reply"]
                if not structured:
                    return {"answer": trimws(reply)}
                return _keyed(_unnest_result(_as_rlists(convert_from_type(reply, type_obj))), i)
            if not structured:
                reply = backend.complete(model, system, cast(str, ut), None, params_list, api_args)
                if key is not None:
                    _llm_cache_put(key, reply)
                return {"answer": trimws(reply)}
            # Structured output: a reply that fails JSON generation/validation is
            # asked for again, up to five attempts in all.
            for attempt in range(1, 6):
                try:
                    reply = backend.complete(
                        model, system, cast(str, ut), type_obj, params_list, api_args
                    )
                    result = convert_from_type(reply, type_obj)
                    break
                except Exception as e:
                    if attempt == 5 or not _llm_json_retryable(e):
                        raise
            if key is not None:
                _llm_cache_put(key, reply)
            return _keyed(_unnest_result(_as_rlists(result)), i)
        except Exception as e:
            msg = _llm_error_message(e)
            if _llm_is_systemic_error(e):
                _llm_systemic_notice.trip(
                    "LLM appears unavailable this run -- checks will fall back to "
                    "RULES ONLY (no LLM inference). Check the endpoint and API key "
                    '(vllm reads Sys.getenv("VLLM_API_KEY")).\n  First error: ' + msg
                )
            if structured:
                return pd.DataFrame(
                    {
                        ".error": pd.Series([True], dtype="boolean"),
                        ".error_msg": pd.Series([msg], dtype="string"),
                        _ROW_KEY: pd.Series([i], dtype="Int64"),
                    }
                )
            return {"answer": pd.NA, "error": True, "error_msg": msg}

    workers = _llm_workers()
    if workers > 1 and ncalls > 1:
        from concurrent.futures import ThreadPoolExecutor
        from contextvars import copy_context

        with ThreadPoolExecutor(max_workers=min(workers, ncalls)) as pool:
            futures = [
                pool.submit(copy_context().run, one, i, ut) for i, ut in enumerate(unique_text)
            ]
            responses = [f.result() for f in futures]
    else:
        responses = [one(i, ut) for i, ut in enumerate(unique_text)]
    elapsed = int(time.monotonic() - started)
    _message(
        f"{label} {ncalls}/{ncalls} "
        f"{elapsed // 3600:02d}:{elapsed % 3600 // 60:02d}:{elapsed % 60:02d}"
    )

    # join the answers back by the position of each row's text among the texts
    # sent: metacheck joined on the sanitised text, so a text that
    # .llm_sanitise_text() changed (control characters, invalid UTF-8) or a
    # numeric one got NA or failed the join (U19)
    where = {_unique_key(v): k for k, v in enumerate(send_raw)}
    row_keys = [None if v is None else where[_unique_key(v)] for v in raw_values or []]
    x = text_df.copy()
    x[_ROW_KEY] = pd.Series(row_keys, dtype="Int64", index=text_df.index)
    if structured:
        response_df = _bind_rows_r([r if _is_df(r) else _as_data_frame(r) for r in responses])
        if _ROW_KEY not in response_df.columns:
            response_df[_ROW_KEY] = pd.Series([], dtype="Int64")
        answer_df = _left_join(x, response_df, _ROW_KEY, suffix=("", ".extracted"))
    else:
        frames = []
        for k, r in enumerate(responses):
            f = _as_data_frame(r)
            f[_ROW_KEY] = pd.Series([k] * len(f), dtype="Int64", index=f.index)
            frames.append(f)
        response_df = _bind_rows_r(frames)
        if "answer" not in response_df.columns:
            response_df["answer"] = pd.Series([pd.NA] * len(response_df), dtype="string")
        if _ROW_KEY not in response_df.columns:
            response_df[_ROW_KEY] = pd.Series([], dtype="Int64")
        answer_df = _left_join(x, response_df, _ROW_KEY)
    answer_df = answer_df.drop(columns=[_ROW_KEY])

    answer_df.attrs["class"] = ["metacheck_llm", "data.frame"]
    answer_df.attrs["llm"] = {"system_prompt": system_prompt, "model": model, "type": type_obj}

    if structured and ".error" in answer_df.columns:
        err = answer_df[".error"].astype(object).tolist()
        error_rows = [i + 1 for i, v in enumerate(err) if v is not None and v is not pd.NA and v]
        if error_rows:
            msgs_all = answer_df[".error_msg"].astype(object).tolist()
            msgs = _unique([msgs_all[r - 1] for r in error_rows])
            n_err, n = len(error_rows), len(answer_df)
            warnings.warn(
                f"Note (not fatal): {n_err} of {n} LLM extraction{plural(n)} failed and "
                f"{'was' if n_err == 1 else 'were'} left blank; the other {n - n_err} "
                "succeeded and all checks continued.\n"
                f"Row{plural(n_err)} {', '.join(str(r) for r in error_rows)}: "
                f"{'reason' if n_err == 1 else 'reasons'}\n  "
                + "\n  ".join("NA" if m is None else str(m) for m in msgs),
                stacklevel=2,
            )
    elif not structured:
        # every row whose text failed (metacheck's isTRUE(answer_df$error) only
        # ever warned for a single-row result; U19)
        failed = {
            k: r.get("error_msg")
            for k, r in enumerate(responses)
            if isinstance(r, dict) and r.get("error") is True
        }
        rows = [i + 1 for i, k in enumerate(row_keys) if k in failed]
        if rows:
            msgs = _unique([failed[k] for k in row_keys if k in failed])
            warnings.warn(
                f"There were errors in the following rows: {', '.join(str(r) for r in rows)} "
                + "".join(f"\n  *  {'NA' if m is None else m}" for m in msgs),
                stacklevel=2,
            )
    return answer_df


_ROW_KEY = ".pytacheck_text_row."


def _unique_key(v: Any) -> Any:
    """The key :func:`_unique` tells texts apart by."""
    return ("na",) if v is None else (type(v).__name__, v)


def _keyed(df: pd.DataFrame, i: int) -> pd.DataFrame:
    """A structured reply keyed by the position *i* of its text (not by the text)."""
    import pandas as pd

    if len(df) > 0:
        df[_ROW_KEY] = pd.Series([i] * len(df), dtype="Int64", index=df.index)
    return df


def _llm_workers() -> int:
    """Concurrent LLM requests per ``llm()`` call (pytacheck extension; default 1).

    Set the ``metacheck.llm.workers`` option (the old spelling
    ``pytacheck.llm.workers`` also works) or ``PYTACHECK_LLM_WORKERS`` to
    send several texts at once; results, caching and errors are unchanged.
    """
    from metacheck.utils import get_option

    value = get_option("metacheck.llm.workers") or env_get("LLM_WORKERS") or 1
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 1


_init_options()
