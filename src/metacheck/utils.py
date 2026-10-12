"""General helpers: ports of ``R/utils.R`` and ``R/svutils-utils.R``, plus R options.

metacheck configures itself through R ``options()`` (``metacheck.osf.api``,
``metacheck.cache.dir``, ...). :func:`get_option` / :func:`options` keep the
same option names and defaults so ports can read them exactly where the R
code calls ``getOption()``; :func:`local_options` is ``withr::local_options``.

``.batch_query()`` lives in :mod:`metacheck.http`; ``email()`` and ``verbose()``
in :mod:`metacheck.config`.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
import re
import socket
import threading
import warnings
from collections.abc import Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from metacheck._r import as_character, gsub, strsplit, sub, trimws
from metacheck._values import is_missing

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
#: passes to ``getOption()`` where it matters for every caller. The default
#: ``metacheck.llm.model`` depends on the API keys set and is an option set by
#: :func:`_init_default_model` when this module loads, as ``.onLoad()`` sets it.
_DEFAULTS: dict[str, Any] = {
    "metacheck.llm_max_calls": 30,
    "metacheck.llm.use": False,
    "metacheck.osf.delay": 0,
    "metacheck.osf.api": "https://api.osf.io/v2",
    "metacheck.osf.api.calls": 0,
    "metacheck.osf.cache": True,
}
_options: dict[str, Any] = {}
_options_lock = threading.Lock()
_MISSING = object()

#: old spellings of the Python-only options (read and set as the new name)
_OPTION_ALIASES = {
    "pytacheck.careless": "metacheck.careless",
    "pytacheck.llm.workers": "metacheck.llm.workers",
}


def _canon(name: str) -> str:
    """The name an option is stored under: an old spelling maps to its new name."""
    return _OPTION_ALIASES.get(name, name)


def get_option(name: str, default: Any = None) -> Any:
    """R ``getOption(name, default)`` over metacheck's option store."""
    name = _canon(name)
    with _options_lock:
        if name in _options:
            return _options[name]
    return _DEFAULTS.get(name, default)


def options(values: Mapping[str, Any] | None = None, /, **kwargs: Any) -> dict[str, Any]:
    """R ``options()``: set options, returning their previous values.

    Option names contain dots, so pass a mapping
    (``options({"metacheck.osf.delay": 1})``); keyword arguments are also
    accepted with every ``_`` standing for ``.`` (so a name that itself
    contains ``_`` needs the mapping). Setting a value to ``None``
    removes the option (R ``options(x = NULL)``), restoring its default.

    An old spelling (see ``_OPTION_ALIASES``) sets the same option as its new
    name; the previous values come back keyed as the caller spelled them.
    """
    new = dict(values or {})
    new.update({k.replace("_", "."): v for k, v in kwargs.items()})
    old: dict[str, Any] = {}
    with _options_lock:
        for key in new:
            old[key] = _options.get(_canon(key))
        for key, value in new.items():
            if value is None:
                _options.pop(_canon(key), None)
            else:
                _options[_canon(key)] = value
    return old


def _init_default_model() -> None:
    """metacheck's ``.onLoad()`` default model (R/zzz.R), applied when this module loads.

    The first provider whose API key is set becomes ``metacheck.llm.model``,
    unless a model is already set, so ``llm_model(None)`` can still unset it.
    Applying it here, rather than when :mod:`metacheck.llm` loads, keeps the
    option the same before and after the LLM code is first imported.
    """
    from metacheck.llm._onload import _default_model_from_env

    model = _default_model_from_env()
    if model is None:
        return
    with _options_lock:
        _options.setdefault("metacheck.llm.model", model)


@contextlib.contextmanager
def local_options(values: Mapping[str, Any]) -> Iterator[None]:
    """Temporarily set options (``withr::local_options``)."""
    with _options_lock:
        saved: dict[str, Any] = {}
        for k in values:
            saved.setdefault(_canon(k), _options.get(_canon(k), _MISSING))
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
    ext: str = sub(r"^.*(\.[A-Za-z0-9]{1,8})$", r"\1", comp)
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
    if path is None or is_missing(path) or not isinstance(path, str) or path == "":
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
    if is_missing(value):
        return any(is_missing(t) for t in table)
    for t in table:
        if is_missing(t):
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
    from metacheck import http
    from metacheck._r import grepl

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
        return tuple("\x00NA" if is_missing(v) else _norm_key(v) for v in row)

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
            # pandas-stubs omits list[int] from Series.iloc's accepted keys
            out_cols[name] = col.iloc[ysel].reset_index(drop=True)  # type: ignore[call-overload]
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

#: Set inside :func:`suppress_messages` (a context variable, so a block that
#: silences messages in one thread or task does not silence another).
_SUPPRESS_MESSAGES: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "pytacheck_suppress_messages", default=False
)


@contextlib.contextmanager
def suppress_messages() -> Iterator[None]:
    """R's ``suppressMessages()`` for :func:`message`: silence messages in the block.

    Only messages are silenced (warnings and progress bars are not, as in R),
    and only in the current thread or task, unlike switching ``verbose()``
    off, which is global.
    """
    token = _SUPPRESS_MESSAGES.set(True)
    try:
        yield
    finally:
        _SUPPRESS_MESSAGES.reset(token)


def message(*args: Any, domain: Any = None, appendLF: bool = True) -> None:  # noqa: ARG001
    """Port of metacheck's ``message()``: print a message unless ``verbose()`` is off.

    Like R's ``message()`` the parts are pasted together without separators
    and written to stderr; on a terminal the text is green, so it reads as
    information rather than a warning. ``domain`` is accepted for R
    compatibility (no translation). Nothing is printed inside
    :func:`suppress_messages`.
    """
    import sys

    from metacheck.config import verbose

    if _SUPPRESS_MESSAGES.get() or not verbose():
        return

    def part(a: Any) -> str:
        # R pastes each argument's elements with no separator (NULL gives nothing)
        if a is None:
            return ""
        if isinstance(a, str):
            return a
        if isinstance(a, list | tuple):
            return "".join(part(x) if x is not None else "NA" for x in a)
        value = as_character(a)
        return "NA" if value is None else str(value)

    text = "".join(part(a) for a in args)
    stream = sys.stderr
    try:
        interactive = stream.isatty()
    except (AttributeError, ValueError):
        interactive = False
    if interactive:
        text = f"\033[32m{text}\033[39m"
    stream.write(text + ("\n" if appendLF else ""))
    stream.flush()


# ---------------------------------------------------------------------------
# Progress bars
# ---------------------------------------------------------------------------

# the layout words of R's progress package that callers put in a format; the
# rest of the format is the bar's text
_LAYOUT = re.compile(
    r"\s*(?:\[:bar\]|\(:spin\)|:current/:total"
    r"|:(?:bar|spin|current|total|elapsedfull|elapsed|eta|percent|rate|tick_rate|bytes)\b)"
)
_TOKEN = re.compile(r":([A-Za-z_]\w*)")
_progress_lock = threading.RLock()
_progress: Any = None  # the shared rich.progress.Progress while a bar is open
_open_bars = 0


def _shared_progress() -> Any:
    """The one live display on stderr, started for the first bar (rich allows only one)."""
    global _progress
    if _progress is None:
        import atexit
        import sys

        from rich.console import Console
        from rich.progress import (
            BarColumn,
            MofNCompleteColumn,
            Progress,
            ProgressColumn,
            SpinnerColumn,
            TextColumn,
            TimeElapsedColumn,
        )
        from rich.text import Text

        class Only(ProgressColumn):
            """A column shown for tasks with a known total (or, with ``known=False``, without)."""

            def __init__(self, column: ProgressColumn, known: bool) -> None:
                super().__init__()
                self.column, self.known = column, known

            def render(self, task: Any) -> Any:
                if (task.total is not None) == self.known:
                    return self.column.render(task)
                return Text("")

        _progress = Progress(
            Only(SpinnerColumn(), False),
            TextColumn("{task.description}", markup=False),
            Only(BarColumn(), True),
            Only(MofNCompleteColumn(), True),
            Only(TimeElapsedColumn(), True),
            console=Console(file=sys.stderr, force_jupyter=False),
            transient=True,
        )
        _progress.start()
        atexit.register(_stop_progress)
    return _progress


def _stop_progress() -> None:
    global _progress
    with _progress_lock:
        if _progress is not None:
            _progress.stop()
            _progress = None


class ProgressBar:
    """A progress bar on stderr (``rich.progress``), as :func:`pb` makes it.

    The words of the *format* that name a layout (``:bar``, ``:current/:total``,
    ``:elapsedfull``, ``:spin``) are not read: a bar with a known *total* shows a
    bar, the count and the elapsed time, and one without (``None`` or NaN) a
    spinner. What is left of the format is the text, and its ``:name`` tokens
    are filled from the *tokens* given to :meth:`tick`. The bar is drawn only
    on a terminal and only with ``show=True``; bars open at the same time share
    one display. A bar finishes when it reaches its total or is terminated, and
    ticking a finished bar does nothing.
    """

    def __init__(
        self, total: float | None, format: str = "[:bar] :current/:total", show: bool = True
    ):
        if total is not None and not total >= 0 and total == total:
            raise ValueError("`total` must be a non-negative number or NA")
        self.total = None if total is None or total != total else total
        self.text = _LAYOUT.sub("", format).strip()
        self.current: float = 0
        self.finished = False
        self._tokens: dict[str, Any] = {}
        self._task: Any = None
        if show:
            import sys

            global _open_bars
            with _progress_lock:
                # while a display is live, sys.stderr is rich's proxy for it
                try:
                    interactive = _progress is not None or sys.stderr.isatty()
                except (AttributeError, ValueError):
                    interactive = False
                if interactive:
                    self._task = _shared_progress().add_task(self._text(), total=self.total)
                    _open_bars += 1

    def _text(self) -> str:
        return _TOKEN.sub(lambda m: str(self._tokens.get(m[1], "")), self.text)

    def tick(self, len: float = 1, tokens: Mapping[str, Any] | None = None) -> None:
        """Advance the bar by *len* ticks (0 just redraws, e.g. with new *tokens*)."""
        if self.finished:
            return
        if tokens:
            self._tokens.update(tokens)
        self.current += len
        if self._task is not None:
            with _progress_lock:
                _progress.update(self._task, completed=self.current, description=self._text())
        if self.total is not None and self.current >= self.total:
            self.terminate()

    def update(self, ratio: float, tokens: Mapping[str, Any] | None = None) -> None:
        """Set the progress to *ratio* of the total."""
        if self.total is None:
            raise RuntimeError("Cannot update a progress bar with an unknown total")
        self.tick(ratio * self.total - self.current, tokens)

    def message(self, msg: str) -> None:
        """Print *msg* above the bar."""
        if self._task is not None and not self.finished:
            with _progress_lock:
                _progress.console.print(str(msg), markup=False, highlight=False)

    def terminate(self) -> None:
        """Finish the bar and take it off the display."""
        if self.finished:
            return
        self.finished = True
        if self._task is not None:
            global _open_bars
            with _progress_lock:
                _progress.remove_task(self._task)
                _open_bars -= 1
                if _open_bars == 0:
                    _stop_progress()


def pb(total: float | None, format: str = "[:bar] :current/:total") -> ProgressBar:
    """Port of metacheck's ``pb()``: a progress bar that respects ``verbose()``.

    With verbosity off the bar is never drawn, but ``tick()``, ``message()`` and
    ``terminate()`` still work, so callers never need to check.
    """
    from metacheck.config import verbose

    bar = ProgressBar(total, format, show=verbose())
    bar.tick(0)
    return bar


_init_default_model()
