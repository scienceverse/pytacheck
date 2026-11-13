"""Keys in text and files (metacheck #607): passwords for paid services.

Port of ``R/keys.R``. A key (an API key, an access token, a private key) in a
paper's repository lets anyone use the service at the authors' cost, so
``data_check`` warns about one (:func:`_key_scan_repo_files`,
:func:`_key_scan_tables`) and ``module_run()`` removes any key from a module's
result (:func:`_key_remove`), so that pytacheck never copies one into its own
output.

The rules are the secret scanner betterleaks' rules of high and medium
confidence (MIT licence; ``resources/databases/key_patterns.json.gz`` made by
``scripts/convert_key_patterns.R`` from metacheck's ``inst/databases/
key_patterns.Rds``, copyright notice in ``key_patterns.COPYRIGHT`` beside it).
Each rule has keywords, of which one must be in the text before the rule is
tried (as in betterleaks), and is tried only near them (:func:`_key_windows`):
a backtracking search can be very slow on a long text. Of a rule's filter, its
minimum entropy and the patterns and texts a key must or must not match are
applied, and the general filter's placeholders (such as ``${API_KEY}``).

A key is never tested with its provider: that would use someone else's key.

Positions are Python's: ``start`` and ``end`` of a find are slice indexes into
the text (R's are 1-based and inclusive).
"""

from __future__ import annotations

import bisect
import codecs
import copy
import functools
import gzip
import math
import os
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import resources
from typing import Any, NamedTuple

import orjson
import pandas as pd
import regex

from metacheck._r.regex import compile_r

__all__ = ["KeyHit", "KeySeen"]

#: characters searched on either side of a keyword
_KEY_WINDOW = 5000

#: an unbroken run of this many base64 characters or more is encoded data (an
#: image in a knitted HTML page), not text: it is masked before searching
_KEY_ENCODED_RUN = 1000

#: largest file searched, in bytes
_KEY_MAX_BYTES = 25 * 1024**2

#: rows searched at the top of a data table (and its column names)
_KEY_TABLE_ROWS = 3

#: the text that replaces a key
_KEY_REMOVED = "[key removed]"

#: seconds one rule may take on one stretch of text; R's PCRE gives up at its
#: match limit and the stretch has no find
_KEY_TIMEOUT = 20.0

# every character that is in no keyword splits a text into pieces
_KEY_SPLIT = regex.compile(r"[^a-z0-9$./:_~+<=-]+")
_ENCODED = regex.compile(rf"[A-Za-z0-9+/=]{{{_KEY_ENCODED_RUN},}}")


class KeyHit(NamedTuple):
    """A key found in a text: where (slice indexes), the rule's name and the key itself."""

    start: int
    end: int
    rule: str
    key: str


@dataclass
class _Rule:
    """One of betterleaks' rules, with its regexes compiled when first needed."""

    name: str
    regex: str
    group: int | None
    path: str | None
    entropy: float | None
    entropy_strict: bool
    keywords: tuple[str, ...]
    drop_match: tuple[str, ...]
    need_match: tuple[str, ...]
    drop_contains: tuple[str, ...]
    confidence: str = ""
    description: str | None = None
    _rx: Any = field(default=None, repr=False)

    @property
    def rx(self) -> Any:
        if self._rx is None:
            self._rx = compile_r(self.regex, perl=True)
        return self._rx


@dataclass
class _Rules:
    patterns: list[_Rule]
    ignore_patterns: tuple[str, ...]
    ignore_texts: tuple[str, ...]
    source: dict[str, Any]
    keywords: list[str]
    kw_parts: list[list[str]]


def _search(pattern: str, text: str) -> bool:
    """``grepl(pattern, text, perl = TRUE)``."""
    return compile_r(pattern, perl=True).search(text) is not None


@functools.cache
def _key_rules() -> _Rules:
    """The rules, read once (``.key_rules()``)."""
    raw = resources.files("metacheck.resources.databases").joinpath("key_patterns.json.gz")
    obj = orjson.loads(gzip.decompress(raw.read_bytes()))
    patterns = [
        _Rule(
            name=p["name"],
            regex=p["regex"],
            group=p["group"],
            path=p["path"],
            entropy=p["entropy"],
            entropy_strict=bool(p["entropy_strict"]),
            keywords=tuple(p["keywords"]),
            drop_match=tuple(p["drop_match"]),
            need_match=tuple(p["need_match"]),
            drop_contains=tuple(p["drop_contains"]),
            confidence=p["confidence"],
            description=p["description"],
        )
        for p in obj["patterns"]
    ]
    keywords = list(dict.fromkeys(k for p in patterns for k in p.keywords))
    return _Rules(
        patterns=patterns,
        ignore_patterns=tuple(obj["ignore"]["patterns"]),
        ignore_texts=tuple(obj["ignore"]["texts"]),
        source=obj["source"],
        keywords=keywords,
        kw_parts=[[p for p in _KEY_SPLIT.split(k) if p] for k in keywords],
    )


def _lower(text: str) -> str:
    """``tolower()``: the text in lower case, with every character in its place."""
    low = text.lower()
    if len(low) == len(text):
        return low
    # a few characters (such as "İ") become two when lowered; R's tolower() does not
    return "".join(c if len(c.lower()) != 1 else c.lower() for c in text)


def _key_present(texts: Iterable[str]) -> list[str]:
    """The keywords that may be in *texts* (in lower case).

    The texts are split at every character that is in no keyword, and each
    keyword is looked for in the unique pieces, joined: a keyword can only be
    inside one piece, and text repeats a lot, so this is much faster than
    looking in the whole text. A keyword with such a character in it (a space:
    ``"<add key="``) counts when each of its own pieces is there, which it must
    be if the keyword is: no keyword is missed, and the rules are then tried on
    the texts themselves.
    """
    joined_all = " ".join(texts)
    pieces = set(_KEY_SPLIT.sub(" ", joined_all).split(" "))
    pieces.discard("")
    if not pieces:
        return []
    joined = " ".join(pieces)
    rules = _key_rules()
    return [
        k
        for k, parts in zip(rules.keywords, rules.kw_parts, strict=True)
        if all(p in joined for p in parts)
    ]


def _key_entropy(s: str) -> float:
    """Shannon entropy in bits per character, as betterleaks computes it."""
    n = len(s)
    if n == 0:
        return 0.0
    return -sum(c / n * math.log2(c / n) for c in Counter(s).values())


def _key_not_a_key(secret: str, rule: _Rule) -> bool:
    """True when betterleaks' filters drop this find.

    Too little entropy for the rule, a pattern of the rule it must not match
    (such as ``.+EXAMPLE$``) or must match, a text it must not contain, or a
    placeholder of the general filter.
    """
    if rule.entropy is not None:
        h = _key_entropy(secret)
        if (h <= rule.entropy) if rule.entropy_strict else (h < rule.entropy):
            return True
    if rule.drop_match and any(_search(rx, secret) for rx in rule.drop_match):
        return True
    if rule.need_match and not any(_search(rx, secret) for rx in rule.need_match):
        return True
    if rule.drop_contains and any(t in secret for t in rule.drop_contains):
        return True
    ign = _key_rules()
    return any(_search(rx, secret) for rx in ign.ignore_patterns) or any(
        t in secret for t in ign.ignore_texts
    )


def _key_windows(low: str, keywords: Sequence[str]) -> list[tuple[int, int]]:
    """The stretches of *low* (the text in lower case) within the window of a keyword.

    Slice indexes ``(start, end)``, joined where they overlap. TruffleHog
    likewise tries a detector's patterns only on the part of a text around one
    of its keywords.
    """
    n = len(low)
    hits: list[int] = []
    for k in keywords:
        pos = low.find(k)
        while pos != -1:
            hits.append(pos)
            pos = low.find(k, pos + max(1, len(k)))
    if not hits:
        return []
    if n <= 2 * _KEY_WINDOW:
        return [(0, n)]
    longest = max(len(k) for k in keywords)
    hits.sort()
    out: list[tuple[int, int]] = []
    for h in hits:
        a = max(0, h - _KEY_WINDOW)
        b = min(n, h + _KEY_WINDOW + longest)
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _key_mask_encoded(text: str) -> str:
    """*text* with each run of encoded data replaced by spaces of the same length.

    Its random letters matched rules such as the Brave Search key's ("BSA" and
    24 to 40 letters) four times in one HTML page of a corpus repository, and
    "[key removed]" in it would damage the image; positions and line numbers
    stay as they are. No rule's key is near this long (the longest about 160
    characters), and a private key's base64 is in lines of 64 characters.
    """
    return _ENCODED.sub(lambda m: " " * len(m[0]), text)


def _key_find(
    text: Any,
    rules: Sequence[_Rule] | None = None,
    checked: bool = False,
    path: str | None = None,
) -> list[KeyHit]:
    """The keys in one text, each with its place, the rule that found it and the key.

    *rules* can be given to try only some (see :func:`_key_remove`); with
    ``checked=True`` they are known to have a keyword in the text. *path*, the
    file the text is from, decides the rules made for some files only (such as a
    Terraform password, for ``.tf`` files); without a path these are not tried.
    Sorted by start, one find per place.
    """
    if not isinstance(text, str) or not text:
        return []
    text = _key_mask_encoded(text)
    pats = list(_key_rules().patterns if rules is None else rules)
    if path is None:
        pats = [p for p in pats if p.path is None]
    else:
        pats = [p for p in pats if p.path is None or _search(p.path, path)]
    if not pats:
        return []
    low = _lower(text)
    # only the rules with a keyword in the text
    if not checked:
        present = set(_key_present([low]))
        pats = [p for p in pats if not p.keywords or any(k in present for k in p.keywords)]
    out: list[KeyHit] = []
    for rule in pats:
        wins = _key_windows(low, rule.keywords) if rule.keywords else [(0, len(text))]
        for a, b in wins:
            part = text[a:b]
            try:
                matches = list(rule.rx.finditer(part, timeout=_KEY_TIMEOUT))
            except (TimeoutError, regex.error):
                continue
            for m in matches:
                # the rule's group, else its first group that matched, else the match
                g = rule.group
                if g is None:
                    used = [
                        i
                        for i in range(1, m.re.groups + 1)
                        if m.start(i) >= 0 and m.end(i) > m.start(i)
                    ]
                    g = used[0] if used else None
                if g is not None and g <= m.re.groups and m.start(g) >= 0:
                    s, e = m.start(g), m.end(g)
                else:
                    s, e = m.start(), m.end()
                if e <= s:
                    continue
                key = part[s:e]
                if _key_not_a_key(key, rule):
                    continue
                out.append(KeyHit(a + s, a + e, rule.name, key))
    # one row per place: two windows or two rules can find the same key
    out.sort(key=lambda h: (h.start, -h.end))
    seen: set[tuple[int, int]] = set()
    unique: list[KeyHit] = []
    for h in out:
        if (h.start, h.end) not in seen:
            seen.add((h.start, h.end))
            unique.append(h)
    return unique


def _key_replace(text: str, found: Sequence[KeyHit], with_: str = _KEY_REMOVED) -> str:
    """*text* with each key replaced by *with_*; keys that overlap are taken as one."""
    if not found:
        return text
    pieces: list[str] = []
    pos = 0
    for h in sorted(found, key=lambda h: h.start):
        if h.end <= pos:
            continue  # inside a key already replaced
        if h.start >= pos:
            pieces += [text[pos : h.start], with_]
        pos = h.end
    pieces.append(text[pos:])
    return "".join(pieces)


# -----------------------------------------------------------------------------
# removing keys from a module's result
# -----------------------------------------------------------------------------


def _is_str_dtype(s: pd.Series) -> bool:
    return pd.api.types.is_string_dtype(s.dtype) or s.dtype == object


def _series_texts(s: pd.Series) -> list[str]:
    """The distinct text values of a Series (object, string or categorical)."""
    if isinstance(s.dtype, pd.CategoricalDtype):
        return [c for c in s.cat.categories if isinstance(c, str)]
    if not _is_str_dtype(s):
        return []
    vals: Iterable[Any]
    try:
        vals = pd.unique(s.dropna())
    except TypeError:  # unhashable cells (lists)
        vals = s.tolist()
    return [v for v in vals if isinstance(v, str)]


def _collect(x: Any, out: list[str]) -> None:
    """Every text in *x* (values and names) into *out*."""
    if isinstance(x, str):
        out.append(x)
    elif isinstance(x, pd.DataFrame):
        out.extend(c for c in x.columns if isinstance(c, str))
        for i in range(x.shape[1]):
            col = x.iloc[:, i]
            out.extend(_series_texts(col))
            if col.dtype == object:
                for v in col.tolist():
                    if isinstance(v, Mapping | list | tuple):
                        _collect(v, out)
    elif isinstance(x, pd.Series):
        if isinstance(x.index, pd.Index) and _is_str_dtype(pd.Series(x.index)):
            out.extend(i for i in x.index if isinstance(i, str))
        out.extend(_series_texts(x))
    elif isinstance(x, Mapping):
        for k, v in x.items():
            if isinstance(k, str):
                out.append(k)
            _collect(v, out)
    elif isinstance(x, list | tuple):
        for v in x:
            _collect(v, out)


class _Cleaner:
    """Replaces the keys in texts; each distinct text is looked at once."""

    def __init__(self, texts: list[str]) -> None:
        self.present = _key_present([_lower(t) for t in texts])
        pats = _key_rules().patterns
        self.pats = [
            p for p in pats if not p.keywords or any(k in self.present for k in p.keywords)
        ]
        self.looked: dict[str, str] = {}
        self.removed = 0

    def clean(self, s: str) -> str:
        done = self.looked.get(s)
        if done is not None:
            return done
        out = s
        if s:
            lt = _lower(s)
            kws = {k for k in self.present if k in lt}
            if kws:
                rules = [p for p in self.pats if any(k in kws for k in p.keywords)]
                found = _key_find(s, rules, checked=True)
                if found:
                    self.removed += len(found)
                    out = _key_replace(s, found)
        self.looked[s] = out
        return out

    def walk(self, x: Any) -> Any:
        """*x* with the keys replaced; the same object when it holds none."""
        if isinstance(x, str):
            c = self.clean(x)
            return x if c == x else c  # the same object when there is no key in it
        if isinstance(x, pd.DataFrame):
            return self._frame(x)
        if isinstance(x, pd.Series):
            return self._series(x)
        if isinstance(x, Mapping):
            items = [
                (self.clean(k) if isinstance(k, str) else k, self.walk(v)) for k, v in x.items()
            ]
            if all(a == k and b is v for (a, b), (k, v) in zip(items, x.items(), strict=True)):
                return x
            if type(x) is dict or not isinstance(x, dict):
                return dict(items)
            # a dict of a subclass keeps its class and attributes (a shallow copy)
            new = copy.copy(x)
            new.clear()
            new.update(items)
            return new
        if isinstance(x, list | tuple) and not hasattr(x, "_fields"):
            new_list = [self.walk(v) for v in x]
            if all(a is b for a, b in zip(x, new_list, strict=True)):
                return x
            return new_list if isinstance(x, list) else tuple(new_list)
        return x

    def _series(self, s: pd.Series) -> pd.Series:
        out = s
        if isinstance(s.dtype, pd.CategoricalDtype):
            mapping = {c: self.clean(c) for c in s.cat.categories if isinstance(c, str)}
            if any(k != v for k, v in mapping.items()):
                out = s.astype(object).map(lambda v: mapping.get(v, v)).astype("category")
        elif _is_str_dtype(s):
            texts = _series_texts(s)
            mapping = {t: self.clean(t) for t in texts}
            changed = {k: v for k, v in mapping.items() if k != v}
            has_nested = s.dtype == object and any(
                isinstance(v, Mapping | list | tuple) for v in s.tolist()
            )
            if changed or has_nested:
                vals = [
                    changed.get(v, v)
                    if isinstance(v, str)
                    else (self.walk(v) if isinstance(v, Mapping | list | tuple) else v)
                    for v in s.tolist()
                ]
                out = pd.Series(vals, index=s.index, name=s.name, dtype=s.dtype)
        if isinstance(s.index, pd.Index) and _is_str_dtype(pd.Series(s.index)):
            idx = [self.clean(i) if isinstance(i, str) else i for i in s.index]
            if any(a != b for a, b in zip(idx, s.index, strict=True)):
                out = out.copy()
                out.index = pd.Index(idx, dtype=s.index.dtype)
        return out

    def _frame(self, df: pd.DataFrame) -> pd.DataFrame:
        cols = [self.clean(c) if isinstance(c, str) else c for c in df.columns]
        new_cols: list[pd.Series] = []
        changed = any(a != b for a, b in zip(cols, df.columns, strict=True))
        for i in range(df.shape[1]):
            col = df.iloc[:, i]
            new = self._series(col)
            if new is not col:
                changed = True
            new_cols.append(new)
        if not changed:
            return df
        out = pd.concat(new_cols, axis=1) if new_cols else df.copy()
        out.columns = pd.Index(cols, dtype=df.columns.dtype)
        out.index = df.index
        return out


def _key_remove(x: Any) -> tuple[Any, int]:
    """The keys removed from *x* (a module's result: containers, tables, text).

    Each key is replaced by ``"[key removed]"``, in names too. Only the rules
    with a keyword somewhere in *x* are tried, and only on the texts holding
    one of them, so a result without any keyword passes almost at once.
    Returns the result and the number of keys removed; a result without a key
    comes back as the same object.
    """
    texts: list[str] = []
    _collect(x, texts)
    texts = list(dict.fromkeys(texts))
    if not texts:
        return x, 0
    cleaner = _Cleaner(texts)
    if not cleaner.present:
        return x, 0
    out = cleaner.walk(x)
    return out, cleaner.removed


# -----------------------------------------------------------------------------
# searching files and tables
# -----------------------------------------------------------------------------


def _key_shown(key: str) -> str:
    """The beginning of a key, to show without giving it away.

    Up to a sixth of it, at least 4 and at most 12 characters, then ``"..."``.
    """
    n = min(12, max(4, len(key) // 6))
    return key[:n] + "..."


class KeySeen:
    """The keys seen in one module run: each distinct key gets a number (1, 2, ...).

    Shared by the searches of one run, so that the same key in a survey file and
    in a data file has one number; the keys are never returned.
    """

    def __init__(self) -> None:
        self.keys: dict[str, int] = {}

    def number(self, keys: Iterable[str]) -> list[int]:
        out = []
        for k in keys:
            if k not in self.keys:
                self.keys[k] = len(self.keys) + 1
            out.append(self.keys[k])
        return out


_FILES = ["file", "line", "key", "rule", "shown"]


def _frame(cols: Mapping[str, list[Any]], kinds: Mapping[str, str]) -> pd.DataFrame:
    return pd.DataFrame(
        {c: pd.Series(cols.get(c, []), dtype=kinds[c]) for c in kinds}  # type: ignore[call-overload]
    )


_FILE_KINDS = {
    "file": "string",
    "line": "Int64",
    "key": "Int64",
    "rule": "string",
    "shown": "string",
}
_SKIP_KINDS = {"file": "string", "reason": "string"}


def _utf8_question(err: UnicodeDecodeError) -> tuple[str, int]:
    """Decode errors: one ``?`` for each invalid byte, as ``iconv(sub = "?")``."""
    return "?" * (err.end - err.start), err.end


codecs.register_error("metacheck_question", _utf8_question)  # type: ignore[arg-type]


def _read_text(path: str, size: int) -> str | None:
    """A file's text, or None when it is binary (a null byte in its first 8 KB) or unreadable."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(size)
    except OSError:
        return None
    if b"\x00" in raw[:8192]:
        return None
    return raw.replace(b"\x00", b" ").decode("utf-8", errors="metacheck_question")


def _find_or_none(text: str, path: str) -> list[KeyHit]:
    """:func:`_key_find`; a text that cannot be searched has no keys, as in R."""
    try:
        return _key_find(text, path=path)
    except Exception:
        return []


def _key_scan_files(
    paths: Sequence[str | None],
    names: Sequence[str] | None = None,
    seen: KeySeen | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The keys in files: ``(found, not_searched)``.

    ``found`` has ``file`` (the name given in *names*), ``line``, ``key`` (a
    number per distinct key: the same key in two places has the same number),
    ``rule`` (betterleaks' name for the type of key) and ``shown`` (the beginning
    of the key, see :func:`_key_shown`); never the key itself. A file that holds
    a null byte in its first 8 KB is binary and not searched; one over 25 MB is
    not searched either: ``not_searched`` has its ``file`` and ``reason``.
    """
    seen = seen if seen is not None else KeySeen()
    names = list(paths) if names is None else list(names)  # type: ignore[arg-type]
    rows: dict[str, list[Any]] = {c: [] for c in _FILE_KINDS}
    skipped: dict[str, list[Any]] = {"file": [], "reason": []}
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
            skipped["file"].append(name)
            skipped["reason"].append("too large")
            continue
        txt = _read_text(p, size)
        if txt is None:
            continue
        found = _find_or_none(txt, str(p))
        if not found:
            continue
        nl = [i for i, c in enumerate(txt) if c == "\n"]
        nums = seen.number(h.key for h in found)
        for h, n in zip(found, nums, strict=True):
            rows["file"].append(name)
            rows["line"].append(bisect.bisect_right(nl, h.start) + 1)
            rows["key"].append(n)
            rows["rule"].append(h.rule)
            rows["shown"].append(_key_shown(h.key))
    return _frame(rows, _FILE_KINDS), _frame(skipped, _SKIP_KINDS)


_TABLE_KINDS = {
    "table": "string",
    "row": "Int64",
    "key": "Int64",
    "rule": "string",
    "shown": "string",
}


def _key_scan_tables(tables: Mapping[str, Any] | None, seen: KeySeen | None = None) -> pd.DataFrame:
    """The keys in data tables (data_check's previews): column names and first rows.

    A data file's values come from participants or instruments, so a key in
    them is rare, and searching a large data file whole is slow. But a key the
    authors set for a whole survey is in every response: in 6qf9p_v3 (#607) a
    Qualtrics survey flow held an OpenRouter key as embedded data, which the
    export wrote into the column "OpenRouterAPIKey" of every response. The
    table as data_check read it (with Qualtrics' extra header rows taken out)
    is used, not the file's first lines: Qualtrics' question texts hold line
    breaks, so the first response can be many lines down.

    A frame with ``table`` (the mapping's key), ``row`` (0 for the column
    names), ``key``, ``rule`` and ``shown``, as :func:`_key_scan_files`.
    """
    seen = seen if seen is not None else KeySeen()
    rows: dict[str, list[Any]] = {c: [] for c in _TABLE_KINDS}
    for nm, df in (tables or {}).items():
        if not isinstance(df, pd.DataFrame):
            continue
        top = df.head(_KEY_TABLE_ROWS)
        txt: list[Any] = [str(c) for c in df.columns]
        row: list[int] = [0] * df.shape[1]
        for i in range(top.shape[1]):
            col = top.iloc[:, i]
            if isinstance(col.dtype, pd.CategoricalDtype) or _is_str_dtype(col):
                vals = col.tolist()
                txt.extend(v if isinstance(v, str) else None for v in vals)
                row.extend(range(1, len(vals) + 1))
        keep = [i for i, t in enumerate(txt) if isinstance(t, str) and t]
        txt = [txt[i] for i in keep]
        row = [row[i] for i in keep]
        if not txt:
            continue
        lt = [_lower(t) for t in txt]
        present = _key_present(lt)
        if not present:
            continue
        pats = _key_rules().patterns
        for j, t in enumerate(txt):
            kws = {k for k in present if k in lt[j]}
            if not kws:
                continue
            rules = [p for p in pats if any(k in kws for k in p.keywords)]
            found = _key_find(t, rules, checked=True)
            for h, n in zip(found, seen.number(h.key for h in found), strict=True):
                rows["table"].append(nm)
                rows["row"].append(row[j])
                rows["key"].append(n)
                rows["rule"].append(h.rule)
                rows["shown"].append(_key_shown(h.key))
    return _frame(rows, _TABLE_KINDS)


_REPO_KINDS = {
    "paper_id": "string",
    "repo_url": "string",
    "file": "string",
    "line": "Int64",
    "key": "Int64",
    "rule": "string",
    "shown": "string",
}


def _hidden_files(folder: str) -> list[str]:
    """Files of a folder that ``local_files()`` leaves out: a hidden file or one in a hidden folder.

    Of ``.git/`` only its config, which can hold a token in a remote's address;
    the rest is compressed.
    """
    from metacheck._r import r_sorted

    found: list[str] = []
    for dirpath, _dirs, files in os.walk(folder):
        rel_dir = os.path.relpath(dirpath, folder)
        for f in files:
            rel = f if rel_dir == "." else f"{rel_dir}/{f}".replace("\\", "/")
            if any(part.startswith(".") for part in rel.split("/")):
                found.append(rel)
    found = [r for r in found if "/.git/" not in f"/{r}" or f"/{r}".endswith("/.git/config")]
    return r_sorted(found)


def _col(files: pd.DataFrame, name: str) -> list[str | None]:
    if name not in files.columns:
        return [None] * len(files)
    return [None if _na(v) else str(v) for v in files[name].tolist()]


def _na(v: Any) -> bool:
    from metacheck._values import is_missing

    return is_missing(v)


def _key_scan_repo_files(
    files: pd.DataFrame | None, cache: Any = False, seen: KeySeen | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The keys in a module's file table: :func:`_key_scan_files` on each file on disk.

    *files* is data_check's ``all_files`` (``paper_id``, ``repo_url``,
    ``file_name``, ``file_path``, ``file_location``, ``data_type``); the result
    has ``paper_id`` and ``repo_url`` too, and the not-searched files. A tabular
    data file (``data_type`` "data") is left out: see :func:`_key_scan_tables`.
    A file without a ``file_location`` of its own is looked for where
    ``download_repo_files()`` keeps it for the same *cache* setting, so a file
    code_check downloaded earlier in the chain is searched too, without
    downloading anything. A local folder's hidden files, which ``local_files()``
    leaves out (keys are usually kept in one: ``.env``), are searched too.
    """
    from metacheck.archives.download import _repo_cache_path

    seen = seen if seen is not None else KeySeen()
    empty_skip = _frame({}, _SKIP_KINDS)
    none = _frame({}, _REPO_KINDS)
    if files is None or len(files) == 0:
        return none, empty_skip
    loc = _col(files, "file_location")
    shown_name = _col(files, "file_path")
    file_name = _col(files, "file_name")
    shown_name = [s if s else f for s, f in zip(shown_name, file_name, strict=True)]
    repo = _col(files, "repo_url")
    pid = _col(files, "paper_id")
    is_data = [t == "data" for t in _col(files, "data_type")]
    on_disk = [la is not None and os.path.exists(la) for la in loc]
    for i in range(len(files)):
        if not on_disk[i] and repo[i] and shown_name[i]:
            p = _repo_cache_path(repo[i], shown_name[i], cache=cache)
            if os.path.isfile(p):
                loc[i] = str(p)
    for folder in dict.fromkeys(r for r in repo if r and os.path.isdir(r)):
        rel = _hidden_files(folder)
        if not rel:
            continue
        first = repo.index(folder)
        loc += [os.path.join(folder, r) for r in rel]
        shown_name += rel
        repo += [folder] * len(rel)
        pid += [pid[first]] * len(rel)
        is_data += [False] * len(rel)
    use: list[int] = []
    seen_paths: set[str] = set()
    for i, la in enumerate(loc):
        if is_data[i] or not la or not os.path.isfile(la):
            continue
        real = os.path.realpath(la)
        if real in seen_paths:
            continue
        seen_paths.add(real)
        use.append(i)
    if not use:
        return none, empty_skip
    found, skipped = _key_scan_files([loc[i] for i in use], names=[str(i) for i in use], seen=seen)
    if len(skipped):
        skipped["file"] = pd.Series([shown_name[int(i)] for i in skipped["file"]], dtype="string")
    if len(found) == 0:
        return none, skipped
    rows = [int(r) for r in found["file"]]
    out = pd.DataFrame(
        {
            "paper_id": pd.Series([pid[r] for r in rows], dtype="string"),
            "repo_url": pd.Series([repo[r] for r in rows], dtype="string"),
            "file": pd.Series([shown_name[r] for r in rows], dtype="string"),
            "line": found["line"],
            "key": found["key"],
            "rule": found["rule"],
            "shown": found["shown"],
        }
    )
    return out, skipped
