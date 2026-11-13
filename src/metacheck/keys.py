"""Keys in text and files: passwords for paid services such as API keys and access tokens.

Port of ``R/keys.R`` (metacheck #607). A key in a paper's repository lets anyone use the
service at the authors' cost, so ``data_check`` warns about one
(:func:`_key_scan_repo_files`, :func:`_key_scan_tables`), and :func:`module_run()
<metacheck.module.module_run>` removes any key from a module's result
(:func:`_key_remove`), so that metacheck never copies one into its own output.

The rules are betterleaks' rules of high and medium confidence
(``resources/databases/key_patterns.json.gz``, made from metacheck's
``inst/databases/key_patterns.Rds``; MIT licence, see ``COPYRIGHTS``). Each has keywords, of which
one must be in the text before the rule is tried, as in betterleaks, and it is tried only near
them (:func:`_key_windows`): a backtracking search can be very slow on a long text. Of a rule's
filter, its minimum entropy, the patterns a key must or must not match, the texts it must not
contain and the general filter's placeholders are applied.

A key is never tested with its provider.
"""

from __future__ import annotations

import bisect
import functools
import gzip
import json
import math
import os
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from importlib import resources
from typing import Any

import pandas as pd

from metacheck._r.regex import compile_r

__all__: list[str] = []

#: characters searched on either side of a keyword
_KEY_WINDOW = 5000

#: an unbroken run of this many base64 characters or more is encoded data, not text
_KEY_ENCODED_RUN = 1000

#: largest file searched, in bytes
_KEY_MAX_BYTES = 25 * 1024**2

#: rows searched at the top of a data table
_KEY_TABLE_ROWS = 3

_KEY_SPLIT = re.compile(r"[^a-z0-9$./:_~+<=-]+")
_KEY_ENCODED = re.compile(rf"[A-Za-z0-9+/=]{{{_KEY_ENCODED_RUN},}}")

_KEY_COLUMNS = ["start", "end", "rule", "key"]


@dataclass(frozen=True)
class KeyRule:
    """One of betterleaks' rules, with the filter of the conditions kept."""

    name: str
    description: str
    confidence: str
    regex: str
    group: int | None
    path: str | None
    entropy: float | None
    entropy_strict: bool
    keywords: tuple[str, ...]
    drop_match: tuple[str, ...]
    need_match: tuple[str, ...]
    drop_contains: tuple[str, ...]


@dataclass(frozen=True)
class KeyRules:
    """The rules, with the general filter and where they come from."""

    source: dict[str, Any]
    patterns: tuple[KeyRule, ...]
    ignore_patterns: tuple[str, ...]
    ignore_texts: tuple[str, ...]
    keywords: tuple[str, ...]


@functools.cache
def _key_rules() -> KeyRules:
    """Port of ``.key_rules()``: the rules, read once."""
    path = resources.files("metacheck.resources.databases").joinpath("key_patterns.json.gz")
    with path.open("rb") as fh, gzip.open(fh, "rt", encoding="utf-8") as gz:
        raw = json.load(gz)
    pats = tuple(
        KeyRule(
            name=p["name"],
            description=p["description"],
            confidence=p["confidence"],
            regex=p["regex"],
            group=p.get("group"),
            path=p.get("path"),
            entropy=p.get("entropy"),
            entropy_strict=bool(p.get("entropy_strict")),
            keywords=tuple(p.get("keywords") or ()),
            drop_match=tuple(p.get("drop_match") or ()),
            need_match=tuple(p.get("need_match") or ()),
            drop_contains=tuple(p.get("drop_contains") or ()),
        )
        for p in raw["patterns"]
    )
    kws = tuple(dict.fromkeys(k for p in pats for k in p.keywords))
    ign = raw["ignore"]
    return KeyRules(
        source=raw["source"],
        patterns=pats,
        ignore_patterns=tuple(ign.get("patterns") or ()),
        ignore_texts=tuple(ign.get("texts") or ()),
        keywords=kws,
    )


@functools.cache
def _key_regex(pattern: str) -> Any:
    """A rule's regular expression, read as R reads it with ``perl = TRUE``."""
    return compile_r(pattern, perl=True)


def _key_lower(text: str) -> str:
    """``tolower()``: one character for each character (Python's can make two of one)."""
    low = text.lower()
    if len(low) == len(text):
        return low
    return "".join(c if len(c.lower()) != 1 else c.lower() for c in text)


@functools.cache
def _key_kw_parts() -> tuple[tuple[str, ...], ...]:
    return tuple(tuple(p for p in _KEY_SPLIT.split(k) if p) for k in _key_rules().keywords)


def _key_present(texts: Sequence[str]) -> list[str]:
    """Port of ``.key_present()``: the keywords that may be in *texts* (in lower case).

    The texts are split at every character that is in no keyword, and each keyword is looked
    for once in their unique pieces, joined: a keyword can only be inside one piece, and text
    repeats a lot, so this is much faster than looking in the whole text. A keyword with such
    a character in it counts when each of its own pieces is there, which it must be if the
    keyword is: no keyword is missed.
    """
    pieces: dict[str, None] = {}
    for t in texts:
        for p in _KEY_SPLIT.sub(" ", t).split(" "):
            pieces[p] = None
    pieces.pop("", None)
    if not pieces:
        return []
    joined = " ".join(pieces)
    kw = _key_rules().keywords
    parts = _key_kw_parts()
    return [k for k, ps in zip(kw, parts, strict=True) if all(p in joined for p in ps)]


def _key_entropy(s: str) -> float:
    """Port of ``.key_entropy()``: Shannon entropy in bits per character."""
    if not s:
        return 0.0
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in Counter(s).values())


def _key_not_a_key(secret: str, rule: KeyRule) -> bool:
    """Port of ``.key_not_a_key()``: whether betterleaks' filters drop this find.

    Too little entropy for the rule, a pattern of the rule it must not match, one it must
    match, a text it must not contain, or a placeholder of the general filter.
    """
    e = rule.entropy
    if e is not None:
        h = _key_entropy(secret)
        if (h <= e) if rule.entropy_strict else (h < e):
            return True
    if rule.drop_match and any(_key_regex(rx).search(secret) for rx in rule.drop_match):
        return True
    if rule.need_match and not any(_key_regex(rx).search(secret) for rx in rule.need_match):
        return True
    if rule.drop_contains and any(t in secret for t in rule.drop_contains):
        return True
    rules = _key_rules()
    return any(_key_regex(rx).search(secret) for rx in rules.ignore_patterns) or any(
        t in secret for t in rules.ignore_texts
    )


def _key_windows(low: str, keywords: Sequence[str]) -> list[tuple[int, int]]:
    """Port of ``.key_windows()``: the stretches of *low* near one of *keywords*.

    1-based start and end (inclusive) of the parts within :data:`_KEY_WINDOW` characters of a
    keyword, joined where they overlap. TruffleHog likewise tries a detector's patterns only on
    the part of a text around one of its keywords.
    """
    n = len(low)
    hits: list[int] = []
    for k in keywords:
        if not k:
            continue
        pos = low.find(k)
        while pos != -1:
            hits.append(pos + 1)
            pos = low.find(k, pos + len(k))
    if not hits:
        return []
    if n <= 2 * _KEY_WINDOW:
        return [(1, n)]
    longest = max(len(k) for k in keywords)
    hits.sort()
    a = [max(1, h - _KEY_WINDOW) for h in hits]
    b = [min(n, h + _KEY_WINDOW + longest) for h in hits]
    out = [(a[0], b[0])]
    for i in range(1, len(a)):
        last = out[-1]
        if a[i] <= last[1]:
            out[-1] = (last[0], max(last[1], b[i]))
        else:
            out.append((a[i], b[i]))
    return out


def _key_mask_encoded(text: str) -> str:
    """Port of ``.key_mask_encoded()``: each run of encoded data as spaces of the same length."""
    if len(text) < _KEY_ENCODED_RUN:
        return text
    return _KEY_ENCODED.sub(lambda m: " " * len(m.group(0)), text)


def _key_empty() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "start": pd.Series([], dtype="int64"),
            "end": pd.Series([], dtype="int64"),
            "rule": pd.Series([], dtype="object"),
            "key": pd.Series([], dtype="object"),
        }
    )


def _key_clean_text(text: str) -> str:
    """The text with a lone surrogate (which is not text) as ``?``, as ``iconv(sub = "?")``."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return text.encode("utf-8", "replace").decode("utf-8")
    return text


def _key_find_rows(
    text: str,
    rules: Sequence[KeyRule] | None = None,
    checked: bool = False,
    path: str | None = None,
) -> list[tuple[int, int, str, str]]:
    """The keys in one text as ``(start, end, rule, key)``, with 1-based inclusive positions."""
    if not isinstance(text, str) or not text:
        return []
    text = _key_mask_encoded(_key_clean_text(text))
    pats = list(_key_rules().patterns if rules is None else rules)
    if path is None:
        pats = [p for p in pats if p.path is None]
    else:
        pats = [p for p in pats if p.path is None or _key_regex(p.path).search(path)]
    if not pats:
        return []
    low = _key_lower(text)
    if not checked:
        present = set(_key_present([low]))
        pats = [p for p in pats if not p.keywords or any(k in present for k in p.keywords)]
    if not pats:
        return []
    out: list[tuple[int, int, str, str]] = []
    for rule in pats:
        if rule.keywords:
            wins = _key_windows(low, rule.keywords)
        else:
            wins = [(1, len(text))]
        rx = _key_regex(rule.regex)
        for w0, w1 in wins:
            part = text[w0 - 1 : w1]
            try:
                matches = list(rx.finditer(part))
            except Exception:  # noqa: S112 - R's tryCatch(): a rule that errors finds nothing
                continue
            for m in matches:
                g = rule.group
                if g is None and m.re.groups > 0:
                    used = [
                        i
                        for i in range(1, m.re.groups + 1)
                        if m.start(i) != -1 and m.end(i) > m.start(i)
                    ]
                    g = used[0] if used else None
                if g is not None and g <= m.re.groups and m.start(g) != -1:
                    s, e = m.start(g), m.end(g)
                else:
                    s, e = m.start(), m.end()
                if e - s <= 0:
                    continue
                key = part[s:e]
                if _key_not_a_key(key, rule):
                    continue
                start = w0 + s
                out.append((start, start + (e - s) - 1, rule.name, key))
    if not out:
        return []
    # one row per place: two windows or two rules can find the same key
    out.sort(key=lambda r: (r[0], -r[1]))
    seen: set[tuple[int, int]] = set()
    uniq = []
    for r in out:
        if (r[0], r[1]) not in seen:
            seen.add((r[0], r[1]))
            uniq.append(r)
    return uniq


def _key_find(
    text: Any,
    rules: Sequence[KeyRule] | None = None,
    checked: bool = False,
    path: str | None = None,
) -> pd.DataFrame:
    """Port of ``.key_find()``: the keys in one text.

    A data frame with the ``start`` and ``end`` (character positions, from 1) of each, the
    ``rule`` that found it and the ``key`` itself. *rules* can be given to try only some; with
    ``checked = True`` they are known to have a keyword in the text. *path*, the file the text
    is from, decides the rules made for some files only (such as a Terraform password, for
    ``.tf`` files); without a path these are not tried.
    """
    rows = _key_find_rows(text, rules, checked, path)
    if not rows:
        return _key_empty()
    return pd.DataFrame(rows, columns=_KEY_COLUMNS)


def _key_replace(text: str, found: Sequence[tuple[int, int]], with_: str = "[key removed]") -> str:
    """Port of ``.key_replace()``: *text* with each key replaced; overlapping keys are one."""
    if not found:
        return text
    pieces: list[str] = []
    pos = 1
    for start, end in sorted(found):
        if end < pos:
            continue
        if start >= pos:
            pieces.append(text[pos - 1 : start - 1])
            pieces.append(with_)
        pos = end + 1
    pieces.append(text[pos - 1 :])
    return "".join(pieces)


def _key_clean_map(texts: Sequence[str]) -> tuple[dict[str, str], int]:
    """The texts that hold a key, each with its keys replaced, and how many keys were removed."""
    uniq = [t for t in dict.fromkeys(texts) if t]
    lows = [_key_lower(t) for t in uniq]
    present = _key_present(lows)
    if not present:
        return {}, 0
    pats = _key_rules().patterns
    wanted = [p for p in pats if not p.keywords or any(k in present for k in p.keywords)]
    cleaned: dict[str, str] = {}
    n_removed = 0
    for t, lt in zip(uniq, lows, strict=True):
        kws = {k for k in present if k in lt}
        if not kws:
            continue
        rules = [p for p in wanted if any(k in kws for k in p.keywords)]
        rows = _key_find_rows(t, rules, checked=True)
        if rows:
            n_removed += len(rows)
            cleaned[t] = _key_replace(t, [(r[0], r[1]) for r in rows])
    return cleaned, n_removed


def _key_unique_text(col: pd.Series) -> list[str]:
    """The distinct texts in a column (other values, such as lists, are not text)."""
    vals: Any
    try:
        vals = pd.unique(col)
    except TypeError:  # unhashable values (lists, dicts)
        vals = list(dict.fromkeys(v for v in col if isinstance(v, str)))
    return [v for v in vals if isinstance(v, str)]


def _key_collect(x: Any, out: list[str]) -> None:
    """Every text in *x* (lists, mappings, data frames, series), names included."""
    if isinstance(x, str):
        out.append(x)
    elif isinstance(x, pd.DataFrame):
        out.extend(c for c in x.columns if isinstance(c, str))
        for i in range(x.shape[1]):  # by position: names can repeat
            _key_collect(x.iloc[:, i], out)
    elif isinstance(x, pd.Series):
        if isinstance(x.dtype, pd.CategoricalDtype):
            out.extend(c for c in x.cat.categories if isinstance(c, str))
        elif x.dtype == object or pd.api.types.is_string_dtype(x.dtype):
            out.extend(_key_unique_text(x))
        if isinstance(x.name, str):
            out.append(x.name)
    elif isinstance(x, Mapping):
        for k, v in x.items():
            if isinstance(k, str):
                out.append(k)
            _key_collect(v, out)
    elif isinstance(x, list | tuple | set | frozenset):
        for v in x:
            _key_collect(v, out)


def _key_apply_column(col: pd.Series, repl: dict[str, str]) -> pd.Series:
    """A column with each text in *repl* replaced (the column itself when it holds none)."""
    if isinstance(col.dtype, pd.CategoricalDtype):
        cats = list(col.cat.categories)
        if not any(isinstance(v, str) and v in repl for v in cats):
            return col
        new = [repl.get(v, v) if isinstance(v, str) else v for v in cats]
        if len(set(new)) == len(new):
            return col.cat.rename_categories(new)
        return col.astype(object).map(lambda v: repl.get(v, v) if isinstance(v, str) else v)
    if col.dtype == object or pd.api.types.is_string_dtype(col.dtype):
        if not any(v in repl for v in _key_unique_text(col)):
            return col
        return col.map(lambda v: repl.get(v, v) if isinstance(v, str) else v)
    return col


def _key_apply(x: Any, repl: dict[str, str]) -> Any:
    """*x* with each text in *repl* replaced, in names too; what holds no key is returned as it was."""
    if isinstance(x, str):
        return repl.get(x, x)
    if isinstance(x, pd.DataFrame):
        out: pd.DataFrame | None = None
        for i in range(x.shape[1]):  # by position: names can repeat
            col = x.iloc[:, i]
            fixed = _key_apply_column(col, repl)
            if fixed is not col:
                if out is None:
                    out = x.copy()
                out.isetitem(i, fixed.array)
        if any(isinstance(c, str) and c in repl for c in x.columns):
            if out is None:
                out = x.copy()
            out.columns = [repl.get(c, c) if isinstance(c, str) else c for c in out.columns]
        return x if out is None else out
    if isinstance(x, pd.Series):
        out_s = _key_apply_column(x, repl)
        if isinstance(x.name, str) and x.name in repl:
            out_s = out_s.rename(repl[x.name])
        return out_s
    if isinstance(x, dict):
        return {
            (repl.get(k, k) if isinstance(k, str) else k): _key_apply(v, repl) for k, v in x.items()
        }
    if isinstance(x, list):
        return [_key_apply(v, repl) for v in x]
    if isinstance(x, tuple) and type(x) is tuple:
        return tuple(_key_apply(v, repl) for v in x)
    return x


def _key_remove(x: Any) -> tuple[Any, int]:
    """Port of ``.key_remove()``: the keys removed from *x*, and how many.

    *x* is a module's result (lists, data frames, text). Each key is replaced by
    ``"[key removed]"``, in names too. Only the rules with a keyword somewhere in *x* are
    tried, and only on the texts holding one of them, so a result without any keyword passes
    almost at once (and is returned as it was). R returns the number removed as the attribute
    ``keys_removed``; here it is the second value.
    """
    texts: list[str] = []
    _key_collect(x, texts)
    if not texts:
        return x, 0
    cleaned, n_removed = _key_clean_map(texts)
    if not cleaned:
        return x, 0
    return _key_apply(x, cleaned), n_removed


def _key_shown(key: str) -> str:
    """Port of ``.key_shown()``: the beginning of a key, up to a sixth of it (4 to 12), then ``...``"""
    n = min(12, max(4, len(key) // 6))
    return key[:n] + "..."


class _KeySeen:
    """The keys met so far in one module run (R's environment ``seen``)."""

    def __init__(self) -> None:
        self.keys: list[str] = []


def _key_number(keys: Sequence[str], seen: _KeySeen) -> list[int]:
    """Port of ``.key_number()``: a number for each distinct key, the same for the same key.

    The keys are never returned.
    """
    for k in dict.fromkeys(keys):
        if k not in seen.keys:
            seen.keys.append(k)
    return [seen.keys.index(k) + 1 for k in keys]


def _key_files_empty() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "file": pd.Series([], dtype="object"),
            "line": pd.Series([], dtype="int64"),
            "key": pd.Series([], dtype="int64"),
            "rule": pd.Series([], dtype="object"),
            "shown": pd.Series([], dtype="object"),
        }
    )


def _key_scan_files(
    paths: Sequence[str | None],
    names: Sequence[str] | None = None,
    seen: _KeySeen | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Port of ``.key_scan_files()``: the keys in files.

    Returns a data frame with ``file`` (the name given in *names*), ``line``, ``key`` (a number
    per distinct key), ``rule`` (betterleaks' name for the type of key) and ``shown`` (the
    beginning of the key); never the key itself. A file that holds a null byte in its first 8 KB
    is binary and not searched; one over :data:`_KEY_MAX_BYTES` is not searched either. The
    second value lists the paths not searched (``file`` and ``reason``), R's attribute
    ``not_searched``.
    """
    names = list(paths) if names is None else list(names)  # type: ignore[arg-type]
    seen = _KeySeen() if seen is None else seen
    skipped: list[dict[str, str]] = []
    parts: list[pd.DataFrame] = []
    for p, name in zip(paths, names, strict=True):
        if p is None or not os.path.isfile(p):
            continue
        try:
            size = os.path.getsize(p)
        except OSError:
            continue
        if size == 0:
            continue
        if size > _KEY_MAX_BYTES:
            skipped.append({"file": name, "reason": "too large"})
            continue
        try:
            with open(p, "rb") as fh:
                raw = fh.read(size)
        except OSError:
            continue
        if b"\x00" in raw[:8192]:
            continue  # binary
        raw = raw.replace(b"\x00", b" ")
        txt = raw.decode("utf-8", "replace").replace("�", "?")
        rows = _key_find_rows(txt, path=str(p))
        if not rows:
            continue
        nl = [i + 1 for i, c in enumerate(txt) if c == "\n"] if "\n" in txt else []
        numbers = _key_number([r[3] for r in rows], seen)
        parts.append(
            pd.DataFrame(
                {
                    "file": [name] * len(rows),
                    "line": [bisect.bisect_right(nl, r[0]) + 1 for r in rows],
                    "key": numbers,
                    "rule": [r[2] for r in rows],
                    "shown": [_key_shown(r[3]) for r in rows],
                }
            )
        )
    out = pd.concat(parts, ignore_index=True) if parts else _key_files_empty()
    return out, pd.DataFrame(
        {
            "file": pd.Series([s["file"] for s in skipped], dtype="object"),
            "reason": pd.Series([s["reason"] for s in skipped], dtype="object"),
        }
    )


def _key_scan_tables(tables: Mapping[str, Any], seen: _KeySeen | None = None) -> pd.DataFrame:
    """Port of ``.key_scan_tables()``: the keys in data tables.

    *tables* is a mapping of names to data frames (data_check's previews). Their column names
    and first :data:`_KEY_TABLE_ROWS` rows are searched: a key the authors set for a whole survey
    is in every response, a key a participant typed is not looked for. A data frame with
    ``table`` (the name), ``row`` (0 for the column names), ``key``, ``rule`` and ``shown``.
    """
    seen = _KeySeen() if seen is None else seen
    cols = {
        "table": pd.Series([], dtype="object"),
        "row": pd.Series([], dtype="int64"),
        "key": pd.Series([], dtype="int64"),
        "rule": pd.Series([], dtype="object"),
        "shown": pd.Series([], dtype="object"),
    }
    recs: list[tuple[str, int, int, str, str]] = []
    for nm, df in tables.items():
        if not isinstance(df, pd.DataFrame):
            continue
        top = df.head(_KEY_TABLE_ROWS)
        txt: list[str] = [str(c) for c in df.columns]
        row: list[int] = [0] * len(txt)
        for c in range(top.shape[1]):
            col = top.iloc[:, c]
            is_text = (
                isinstance(col.dtype, pd.CategoricalDtype)
                or col.dtype == object
                or pd.api.types.is_string_dtype(col.dtype)
            )
            if not is_text:
                continue
            for i, v in enumerate(col.tolist(), start=1):
                txt.append(v if isinstance(v, str) else "")
                row.append(i)
                if not isinstance(v, str):
                    txt[-1] = ""
        keep = [i for i, t in enumerate(txt) if t]
        if not keep:
            continue
        txt = [txt[i] for i in keep]
        row = [row[i] for i in keep]
        lt = [_key_lower(t) for t in txt]
        present = _key_present(lt)
        if not present:
            continue
        pats = _key_rules().patterns
        for j, t in enumerate(txt):
            kws = {k for k in present if k in lt[j]}
            if not kws:
                continue
            rules = [p for p in pats if any(k in kws for k in p.keywords)]
            rows = _key_find_rows(t, rules, checked=True)
            if not rows:
                continue
            numbers = _key_number([r[3] for r in rows], seen)
            for r, nbr in zip(rows, numbers, strict=True):
                recs.append((str(nm), row[j], nbr, r[2], _key_shown(r[3])))
    if not recs:
        return pd.DataFrame(cols)
    return pd.DataFrame(recs, columns=["table", "row", "key", "rule", "shown"])


def _key_scan_repo_files(
    files: pd.DataFrame | None,
    cache: bool = False,
    seen: _KeySeen | None = None,
    cache_path: Callable[[Any, Any, bool], Any] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Port of ``.key_scan_repo_files()``: the keys in a module's file table.

    *files* is data_check's ``all_files`` (``paper_id``, ``repo_url``, ``file_name``,
    ``file_path``, ``file_location``, ``data_type``). :func:`_key_scan_files` runs on each file on
    disk, with ``paper_id`` and ``repo_url``. A tabular data file (``data_type`` ``"data"``) is
    left out: see :func:`_key_scan_tables`. A file without a ``file_location`` of its own is looked
    for where ``download_repo_files()`` keeps it for the same *cache* setting, so a file
    ``code_check`` downloaded earlier in the chain is searched too, without downloading
    anything. Returns the keys and the files not searched.
    """
    seen = _KeySeen() if seen is None else seen

    def none() -> pd.DataFrame:
        return pd.DataFrame(
            {
                "paper_id": pd.Series([], dtype="object"),
                "repo_url": pd.Series([], dtype="object"),
                "file": pd.Series([], dtype="object"),
                "line": pd.Series([], dtype="int64"),
                "key": pd.Series([], dtype="int64"),
                "rule": pd.Series([], dtype="object"),
                "shown": pd.Series([], dtype="object"),
            }
        )

    nothing = pd.DataFrame(
        {"file": pd.Series([], dtype="object"), "reason": pd.Series([], dtype="object")}
    )
    if files is None or len(files) == 0:
        return none(), nothing
    if cache_path is None:
        from metacheck.archives.download import _repo_cache_path

        cache_path = _repo_cache_path
    n = len(files)

    def col(nm: str) -> list[str | None]:
        if nm not in files.columns:
            return [None] * n
        return [
            None
            if (v is None or v is pd.NA or (isinstance(v, float) and math.isnan(v)))
            else str(v)
            for v in files[nm].tolist()
        ]

    loc = col("file_location")
    shown_name = col("file_path")
    fname = col("file_name")
    shown_name = [s if s else f for s, f in zip(shown_name, fname, strict=True)]
    repo = col("repo_url")
    on_disk = [a is not None and a != "" and os.path.exists(a) for a in loc]
    for i in range(n):
        if on_disk[i] or not repo[i] or not shown_name[i]:
            continue
        p = cache_path(repo[i], shown_name[i], cache)
        if os.path.isfile(p):
            loc[i] = p
    pid = col("paper_id")
    is_data = [d == "data" for d in col("data_type")]
    # A local folder's hidden files, which local_files() leaves out (except junk such as
    # .DS_Store): keys are usually kept in one (.env). Of .git/ only its config, which can hold
    # a token in a remote's address; the rest is compressed.
    for d in dict.fromkeys(r for r in repo if r and os.path.isdir(r)):
        every = sorted(
            os.path.relpath(os.path.join(root, f), d).replace(os.sep, "/")
            for root, _dirs, fs in os.walk(d)
            for f in fs
        )
        visible = {r for r in every if not any(part.startswith(".") for part in r.split("/"))}
        # list.files(all.files = FALSE) leaves out files in hidden folders as well
        rel = [r for r in every if r not in visible]
        rel = [
            r
            for r in rel
            if not re.search(r"(^|/)\.git/", r) or re.search(r"(^|/)\.git/config$", r)
        ]
        if not rel:
            continue
        first = repo.index(d)
        for r in rel:
            loc.append(os.path.join(d, r))
            shown_name.append(r)
            repo.append(d)
            pid.append(pid[first])
            is_data.append(False)
    use: list[int] = []
    seen_paths: set[str] = set()
    for i, p in enumerate(loc):
        if is_data[i] or not p or not os.path.isfile(p):
            continue
        norm = os.path.normpath(os.path.abspath(p)).replace(os.sep, "/")
        if norm in seen_paths:
            continue
        seen_paths.add(norm)
        use.append(i)
    if not use:
        return none(), nothing
    found, skipped = _key_scan_files([loc[i] for i in use], names=[str(i) for i in use], seen=seen)
    if len(skipped):
        skipped = skipped.copy()
        skipped["file"] = [shown_name[int(i)] or "" for i in skipped["file"]]
    if len(found) == 0:
        return none(), skipped
    idx = [int(i) for i in found["file"]]
    out = pd.DataFrame(
        {
            "paper_id": pd.Series([pid[i] for i in idx], dtype="object"),
            "repo_url": pd.Series([repo[i] for i in idx], dtype="object"),
            "file": pd.Series([shown_name[i] for i in idx], dtype="object"),
            "line": found["line"].astype("int64").to_numpy(),
            "key": found["key"].astype("int64").to_numpy(),
            "rule": found["rule"].to_numpy(),
            "shown": found["shown"].to_numpy(),
        }
    )
    return out, skipped
