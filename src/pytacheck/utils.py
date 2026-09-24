"""General helpers: ports of ``R/utils.R`` and ``R/svutils-utils.R``, plus R options.

metacheck configures itself through R ``options()`` (``metacheck.osf.api``,
``metacheck.cache.dir``, ...). :func:`get_option` / :func:`options` keep the
same option names and defaults so ports can read them exactly where the R
code calls ``getOption()``; :func:`local_options` is ``withr::local_options``.

``.batch_query()`` lives in :mod:`pytacheck.http`; ``email()`` and ``verbose()``
in :mod:`pytacheck.config`.
"""

from __future__ import annotations

import contextlib
import os
import socket
import threading
import warnings
from collections.abc import Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from pytacheck._r import as_character, gsub, is_na, strsplit, sub, trimws

if TYPE_CHECKING:
    import pandas as pd

__all__ = [
    "get_option",
    "left_join",
    "local_options",
    "match_arg",
    "online",
    "options",
    "path_sanitize",
    "rep_if",
]

# ---------------------------------------------------------------------------
# R options
# ---------------------------------------------------------------------------

#: metacheck's ``.onLoad()`` defaults (R/zzz.R), plus the defaults the R code
#: passes to ``getOption()`` where it matters for every caller.
_DEFAULTS: dict[str, Any] = {
    "metacheck.osf.delay": 0,
    "metacheck.osf.api": "https://api.osf.io/v2",
    "metacheck.osf.api.calls": 0,
    "metacheck.osf.cache": True,
}
_options: dict[str, Any] = {}
_options_lock = threading.Lock()
_MISSING = object()


def get_option(name: str, default: Any = None) -> Any:
    """R ``getOption(name, default)`` over pytacheck's option store."""
    with _options_lock:
        if name in _options:
            return _options[name]
    return _DEFAULTS.get(name, default)


def options(values: Mapping[str, Any] | None = None, /, **kwargs: Any) -> dict[str, Any]:
    """R ``options()``: set options, returning their previous values.

    Option names contain dots, so pass a mapping
    (``options({"metacheck.osf.delay": 1})``); keyword arguments are also
    accepted with ``_`` standing for ``.``. Setting a value to ``None``
    removes the option (R ``options(x = NULL)``), restoring its default.
    """
    new = dict(values or {})
    new.update({k.replace("_", "."): v for k, v in kwargs.items()})
    old: dict[str, Any] = {}
    with _options_lock:
        for key, value in new.items():
            old[key] = _options.get(key)
            if value is None:
                _options.pop(key, None)
            else:
                _options[key] = value
    return old


@contextlib.contextmanager
def local_options(values: Mapping[str, Any]) -> Iterator[None]:
    """Temporarily set options (``withr::local_options``)."""
    with _options_lock:
        saved = {k: _options.get(k, _MISSING) for k in values}
    options(values)
    try:
        yield
    finally:
        with _options_lock:
            for key, value in saved.items():
                if value is _MISSING:
                    _options.pop(key, None)
                else:
                    _options[key] = value


def match_arg(arg: Any, choices: Sequence[str]) -> str:
    """R ``match.arg(arg)``: ``None`` or the full choice vector gives the first
    choice; otherwise an exact or unique partial match of *arg*."""
    if arg is None or (not isinstance(arg, str) and list(arg) == list(choices)):
        return choices[0]
    if isinstance(arg, str):
        if arg in choices:
            return arg
        hits = [c for c in choices if arg and c.startswith(arg)]
        if len(hits) == 1:
            return hits[0]
    quoted = ", ".join(f"“{c}”" for c in choices)
    raise ValueError(f"'arg' should be one of {quoted}")


# ---------------------------------------------------------------------------
# R/utils.R
# ---------------------------------------------------------------------------


def _is_list_cell(x: Any) -> bool:
    import numpy as np

    return isinstance(x, list | tuple | dict | set | np.ndarray)


def _col_chr(df: pd.DataFrame | None, col: str, default: str | None = None) -> list[str | None]:
    """Port of R/utils.R::.col_chr(): one column as character, ``nrow(df)`` long.

    A missing column gives *default* for every row; a list column is coerced
    element by element, and any element that is not exactly one value
    (``None``, empty, several values) becomes *default*.
    """
    if df is None:
        return []
    n = len(df)
    if n == 0:
        return []
    if col not in df.columns:
        return [default] * n
    values = df[col].tolist()
    if not any(_is_list_cell(v) for v in values):
        return [as_character(v) for v in values]

    def one(el: Any) -> str | None:
        if el is None:
            return default
        if _is_list_cell(el):
            items = list(el.values()) if isinstance(el, dict) else list(el)
            if len(items) != 1 or _is_list_cell(items[0]):
                return default
            el = items[0]
        try:
            return as_character(el)
        except Exception:
            return default

    return [one(v) for v in values]


def path_sanitize(
    path: Any,
    replacement: str = "_",
    remove_whitespace: bool = True,
    keep_sep: bool = True,
) -> Any:
    """Port of R/utils.R::path_sanitize(): make user-supplied file names safe.

    Removes control characters, replaces ``\\ : * ? " < > |`` (and ``/``
    unless *keep_sep*, and whitespace if *remove_whitespace*) with
    *replacement*, then condenses runs of *replacement*. Vectorised: a string
    gives a string, a sequence a list (``None`` stays ``None``).
    """
    rep_plus = f"{replacement}+"
    invalid = '[\\:*?"<>|]'
    sep = replacement if keep_sep else r"\/"
    ws = r"\s" if remove_whitespace else replacement
    x = trimws(path)
    x = gsub("[[:cntrl:]]", "", x)
    x = gsub(invalid, replacement, x)
    x = gsub(sep, replacement, x)
    x = gsub(ws, replacement, x)
    x = gsub(rep_plus, replacement, x)
    return trimws(x)


# Conservative maximum ABSOLUTE path length metacheck will write to (Windows'
# MAX_PATH with a margin), and the per-component limit on all platforms.
_MAX_PATH_CHARS = 250 if os.name == "nt" else 4000
_MAX_COMPONENT_CHARS = 255


def _short_hash(s: str) -> str:
    """Polynomial rolling hash of the UTF-8 bytes, as 8 hex chars (R short_hash)."""
    m = 2147483647
    h = 0
    for b in s.encode("utf-8"):
        h = (h * 131 + b) % m
    return f"{h:08x}"


def _shorten_component(comp: str, budget: int) -> str:
    if len(comp) <= budget:
        return comp
    ext = sub(r"^.*(\.[A-Za-z0-9]{1,8})$", r"\1", comp)
    if ext == comp:
        ext = ""
    h = "-" + _short_hash(comp)
    keep = max(1, budget - len(h) - len(ext))
    return comp[:keep] + h + ext


def _normalize_path(path: str) -> str:
    """R ``normalizePath(mustWork = FALSE)``: resolved if it exists, else unchanged
    (apart from ``~`` expansion)."""
    path = os.path.expanduser(path)
    if os.path.exists(path):
        return os.path.realpath(path).replace("\\", "/")
    return path


def _safe_write_path(path: str | None) -> str | None:
    """Port of R/utils.R::.safe_write_path(): shorten an over-long path.

    Over-long components (or, when the absolute path exceeds the platform
    limit, the longest components) are truncated and suffixed with a short
    hash of the original so the result stays unique; the leaf keeps its
    extension. Warns when a path was shortened (or is still too long).
    """
    if path is None or is_na(path) or not isinstance(path, str) or path == "":
        return path

    parts: list[str] = [p if p is not None else "NA" for p in strsplit(path, r"[/\\]")]
    changed = False

    new_parts = []
    for p in parts:
        if p and len(p) > _MAX_COMPONENT_CHARS:
            changed = True
            new_parts.append(_shorten_component(p, _MAX_COMPONENT_CHARS))
        else:
            new_parts.append(p)
    parts = new_parts

    long_comp, min_comp = 40, 16

    def abs_len() -> int:
        joined = "/".join(parts)
        try:
            return len(_normalize_path(joined))
        except OSError:
            return len(joined)

    if abs_len() > _MAX_PATH_CHARS:
        for j, p in enumerate(parts):
            if len(p) > long_comp:
                parts[j] = _shorten_component(p, long_comp)
                changed = True
        while abs_len() > _MAX_PATH_CHARS and parts:
            j = max(range(len(parts)), key=lambda k: (len(parts[k]), -k))
            if len(parts[j]) <= min_comp:
                break
            parts[j] = _shorten_component(parts[j], len(parts[j]) - 1)
            changed = True
    new_path = "/".join(parts)

    if abs_len() > _MAX_PATH_CHARS:
        warnings.warn(
            f"Path is still too long after shortening and the write may fail: {new_path} "
            f"(absolute length {abs_len()} > {_MAX_PATH_CHARS}). This usually means a very "
            "deeply nested output/repo tree; use a shorter output directory or a path "
            "outside OneDrive.",
            stacklevel=2,
        )
    elif changed:
        warnings.warn(
            "Path too long for the filesystem; shortened it (a hash keeps it unique). "
            f"Original: {path} -> {new_path}. Deeply nested repos or very long names on "
            "OneDrive/Windows hit the ~260-character limit.",
            stacklevel=2,
        )
    return new_path


# ---------------------------------------------------------------------------
# R/svutils-utils.R
# ---------------------------------------------------------------------------


def _r_in(value: Any, table: Sequence[Any]) -> bool:
    """R ``value %in% table`` for a single value (NA matches NA; str vs number
    compares as character, as ``match()`` coerces)."""
    if is_na(value):
        return any(is_na(t) for t in table)
    for t in table:
        if is_na(t):
            continue
        if isinstance(value, str) != isinstance(t, str):
            if as_character(value) == as_character(t):
                return True
        elif value == t:
            return True
    return False


def rep_if(x: Any, y: Any, replace: Any = None) -> Any:
    """Port of R/svutils-utils.R::rep_if(): replace ``None`` (or values in *replace*).

    For each ``x[i]`` that is ``None`` or in *replace*, use ``y[i]`` (*y* is
    recycled to the length of *x*). Returns a list.
    """
    xs = list(x) if not isinstance(x, str) else [x]
    ys = list(y) if isinstance(y, list | tuple | range) else [y]
    table = (
        []
        if replace is None
        else (list(replace) if isinstance(replace, list | tuple) else [replace])
    )
    if not xs:
        return []
    if not ys:
        raise ValueError("attempt to replicate an object of type 'NULL'")
    out = []
    for i, xi in enumerate(xs):
        yi = ys[i % len(ys)]
        out.append(yi if xi is None or _r_in(xi, table) else xi)
    return out


def online(url: str = "google.com", tries: int = 3, wait: float = 1) -> bool:
    """Port of R/svutils-utils.R::online(): does the URL's host resolve in DNS?

    A missing scheme is fine; the lookup is retried *tries* times, *wait*
    seconds apart, before reporting the host as unreachable.
    """
    from pytacheck import http
    from pytacheck._r import grepl

    if not grepl("^[a-zA-Z]+://", url):
        url = "http://" + url
    host = sub("^[a-zA-Z]+://([^/]+).*", r"\1", url)
    for i in range(1, tries + 1):
        try:
            if socket.getaddrinfo(host, None):
                return True
        except (OSError, UnicodeError):
            pass
        if i < tries:
            http.sleep(wait)
    return False


# ---------------------------------------------------------------------------
# dplyr helpers shared by the archive *_info() functions
# ---------------------------------------------------------------------------


def left_join(
    x: pd.DataFrame,
    y: pd.DataFrame,
    by: str | Sequence[str] | Mapping[str, str],
    suffix: tuple[str, str] = (".x", ".y"),
) -> pd.DataFrame:
    """``dplyr::left_join(x, y, by, suffix)`` with dplyr's semantics.

    Keeps every row of *x* in order (a row matching several rows of *y* is
    repeated), matches ``NA`` keys to ``NA`` keys, keeps *x*'s key columns
    (dropping *y*'s), and suffixes non-key columns present in both tables. A
    mapping ``{x_col: y_col}`` joins differently-named keys (R's
    ``by = c(x_col = "y_col")``).
    """
    import pandas as pd

    if isinstance(by, str):
        pairs = [(by, by)]
    elif isinstance(by, Mapping):
        pairs = list(by.items())
    else:
        pairs = [(b, b) for b in by]
    xkeys = [a for a, _ in pairs]
    ykeys = [b for _, b in pairs]

    def key_tuple(row: Sequence[Any]) -> tuple[Any, ...]:
        return tuple("\x00NA" if is_na(v) else _norm_key(v) for v in row)

    y_index: dict[tuple[Any, ...], list[int]] = {}
    ykey_rows = zip(*(y[k].tolist() for k in ykeys), strict=True) if len(y) else iter(())
    for j, row in enumerate(ykey_rows):
        y_index.setdefault(key_tuple(row), []).append(j)

    xi: list[int] = []
    yi: list[int | None] = []
    xkey_rows = zip(*(x[k].tolist() for k in xkeys), strict=True) if len(x) else iter(())
    for i, row in enumerate(xkey_rows):
        hits = y_index.get(key_tuple(row))
        if hits:
            for j in hits:
                xi.append(i)
                yi.append(j)
        else:
            xi.append(i)
            yi.append(None)

    left = x.iloc[xi].reset_index(drop=True)
    yrest = [c for c in y.columns if c not in ykeys]
    xcols = list(x.columns)
    out_cols: dict[str, pd.Series] = {}
    for c in xcols:
        name = c
        if c in yrest and c not in xkeys:
            name = c + suffix[0]
        out_cols[name] = left[c]
    ysel = [j if j is not None else -1 for j in yi]
    has_missing = any(j is None for j in yi)
    for c in yrest:
        name = c + suffix[1] if (c in xcols) else c
        col = y[c]
        if len(y) == 0 or has_missing:
            values = (
                [col.iloc[j] if j >= 0 else None for j in ysel] if len(y) else [None] * len(ysel)
            )
            out_cols[name] = pd.Series(values, dtype=_nullable_dtype(col.dtype))
        else:
            out_cols[name] = col.iloc[ysel].reset_index(drop=True)
    return pd.DataFrame(out_cols, index=pd.RangeIndex(len(xi)))


def _norm_key(v: Any) -> Any:
    """Join keys: R joins integer and double keys numerically."""
    if isinstance(v, bool):
        return v
    if isinstance(v, int | float):
        return float(v)
    try:
        import numpy as np

        if isinstance(v, np.integer | np.floating):
            return float(v)
    except ImportError:  # pragma: no cover
        pass
    return v


def _nullable_dtype(dtype: Any) -> Any:
    import pandas as pd

    if pd.api.types.is_bool_dtype(dtype):
        return "boolean"
    if pd.api.types.is_integer_dtype(dtype):
        return "Int64"
    if pd.api.types.is_float_dtype(dtype):
        return "float64"
    if pd.api.types.is_string_dtype(dtype) and not pd.api.types.is_object_dtype(dtype):
        return "string"
    return object


# ---------------------------------------------------------------------------
# Messages and progress bars (ports of R/svutils-message.R, R/svutils-pb.R;
# owned by the report port)
# ---------------------------------------------------------------------------


def message(*args: Any, domain: Any = None, appendLF: bool = True) -> None:  # noqa: N803
    """Port of metacheck's ``message()``: print a message unless ``verbose()`` is off.

    Like R's ``message()`` the parts are pasted together without separators
    and written to stderr; on a terminal the text is green, so it reads as
    information rather than a warning.
    """
    import sys

    from pytacheck.config import verbose

    if not verbose():
        return None
    text = "".join("NULL" if a is None else (a if isinstance(a, str) else str(as_character(a))) for a in args)
    stream = sys.stderr
    try:
        interactive = stream.isatty()
    except (AttributeError, ValueError):
        interactive = False
    if interactive:
        text = f"\033[32m{text}\033[39m"
    stream.write(text + ("\n" if appendLF else ""))
    stream.flush()
    return None


class ProgressBar:
    """A console progress bar (R ``progress::progress_bar``), as :func:`pb` makes it.

    ``format`` understands the progress package's tokens (``:bar``,
    ``:current``, ``:total``, ``:elapsed``, ``:elapsedfull``, ``:eta``,
    ``:percent``, ``:rate``, ``:tick_rate``, ``:bytes``, ``:spin``) plus any
    custom ``:name`` passed in ``tokens``. A ``total`` of ``None``/``NaN``
    shows a spinner and never finishes on its own. The bar is only drawn on
    a terminal; ticking a finished bar is an error, as in R.
    """

    _SPIN = "-\\|/"

    def __init__(self, total: float | None, format: str = "[:bar] :current/:total", width: int | None = None, stream: Any = None) -> None:  # noqa: A002
        import math
        import shutil
        import sys
        import time

        if total is not None and not (isinstance(total, int | float) and (math.isnan(total) or total >= 0)):
            raise ValueError("`total` must be a non-negative number or NA")
        self.total = None if total is None or (isinstance(total, float) and math.isnan(total)) else total
        self.format = format
        self.stream = stream if stream is not None else sys.stderr
        self.width = width or min(shutil.get_terminal_size((80, 20)).columns, 80)
        self.current = 0
        self.finished = False
        self._start = time.monotonic()
        self._tokens: dict[str, Any] = {}
        self._spin = 0
        self._last = ""
        try:
            self._draw_ok = bool(self.stream.isatty())
        except (AttributeError, ValueError):
            self._draw_ok = False

    # -- R methods -------------------------------------------------------------

    def tick(self, len: float = 1, tokens: Mapping[str, Any] | None = None) -> None:  # noqa: A002
        """Advance the bar by *len* ticks (0 just redraws, e.g. with new *tokens*)."""
        if self.finished:
            raise RuntimeError("!self$finished is not TRUE")
        if tokens:
            self._tokens.update(tokens)
        self.current += len
        self._spin += 1
        if self.total is not None and self.current >= self.total:
            self.current = self.total
            self._render()
            self.terminate()
            return
        self._render()

    def update(self, ratio: float, tokens: Mapping[str, Any] | None = None) -> None:
        """Set the progress to *ratio* of the total."""
        if self.total is None:
            raise RuntimeError("Cannot update a progress bar with an unknown total")
        self.tick(ratio * self.total - self.current, tokens)

    def message(self, msg: str, set_width: bool = True) -> None:
        """Print a message above the bar."""
        if self._draw_ok and not self.finished:
            self.stream.write("\r\033[2K" + str(msg) + "\n")
            self.stream.write(self._last)
            self.stream.flush()
        elif self._draw_ok:
            self.stream.write(str(msg) + "\n")

    def terminate(self) -> None:
        """Finish the bar (a newline ends the drawn line)."""
        if self.finished:
            return
        self.finished = True
        if self._draw_ok and self._last:
            self.stream.write("\n")
            self.stream.flush()

    # -- rendering ---------------------------------------------------------------

    def render(self) -> str:
        """The bar's current text."""
        import re
        import time

        elapsed = time.monotonic() - self._start
        total = self.total
        ratio = (self.current / total) if total else (1.0 if total == 0 else 0.0)
        ratio = max(0.0, min(1.0, ratio))
        rate = self.current / elapsed if elapsed > 0 else 0.0
        eta = (total - self.current) / rate if total is not None and rate > 0 else float("nan")

        def fmt_secs(s: float) -> str:
            if s != s:  # NaN
                return "?"
            if s < 60:
                return f"{s:.0f}s"
            if s < 3600:
                return f"{s / 60:.0f}m"
            return f"{s / 3600:.1f}h"

        def full(s: float) -> str:
            s = int(s)
            return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"

        def as_bytes(n: float) -> str:
            units = ["B", "kB", "MB", "GB", "TB"]
            i = 0
            while n >= 1000 and i < len(units) - 1:
                n /= 1000
                i += 1
            return f"{n:.0f}{units[i]}" if i == 0 else f"{n:.2f}{units[i]}"

        values: dict[str, str] = {
            "current": f"{self.current:g}",
            "total": "?" if total is None else f"{total:g}",
            "elapsedfull": full(elapsed),
            "elapsed": fmt_secs(elapsed),
            "eta": fmt_secs(eta),
            "percent": f"{ratio * 100:3.0f}%",
            "tick_rate": f"{rate:.1f}",
            "rate": as_bytes(rate) + "/s",
            "bytes": as_bytes(self.current),
            "spin": self._SPIN[self._spin % len(self._SPIN)],
        }
        for key, value in self._tokens.items():
            values[str(key)] = str(value)
        names = sorted(values, key=len, reverse=True)
        pattern = re.compile(":(" + "|".join(re.escape(n) for n in names) + ")")
        text = pattern.sub(lambda m: values[m.group(1)], self.format.replace(":bar", "\x00"))
        if "\x00" in text:
            space = max(self.width - (len(text) - 1), 0)
            done = int(round(space * ratio)) if total is not None else 0
            text = text.replace("\x00", "=" * done + "-" * (space - done))
        return text

    def _render(self) -> None:
        if not self._draw_ok:
            return
        line = self.render()
        self._last = "\r" + line
        self.stream.write("\r\033[2K" + line)
        self.stream.flush()


class _NullProgressBar:
    """The no-op bar :func:`pb` returns when ``verbose()`` is off."""

    finished = False

    def tick(self, *args: Any, **kwargs: Any) -> None:
        return None

    def update(self, *args: Any, **kwargs: Any) -> None:
        return None

    def message(self, *args: Any, **kwargs: Any) -> None:
        return None

    def terminate(self, *args: Any, **kwargs: Any) -> None:
        return None


def pb(total: float | None, format: str = "[:bar] :current/:total") -> ProgressBar | _NullProgressBar:  # noqa: A002
    """Port of metacheck's ``pb()``: a progress bar that respects ``verbose()``.

    With verbosity off it returns a dummy whose ``tick()``, ``message()`` and
    ``terminate()`` do nothing, so callers never need to check.
    """
    from pytacheck.config import verbose

    if verbose():
        bar = ProgressBar(total, format)
        bar.tick(0)
        return bar
    return _NullProgressBar()
