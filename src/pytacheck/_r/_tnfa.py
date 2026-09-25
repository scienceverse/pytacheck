"""TRE's tagged NFA matcher, for TRE patterns with minimal repetitions.

TRE resolves a minimal (lazy) repetition such as ``.*?`` with tags and a
"weeding" of states once a match is found (``tre_tnfa_run_parallel()``), which
gives results no backtracking engine reproduces: R's ``sub("(.*)b(.*?)", ...)``
on ``"abab"`` matches only ``"ab"``, and ``^.*?["']([^"']+)["'].*$`` captures
the *last* quoted string. This module ports the relevant parts of TRE (R's
``src/extra/tre``: ``tre_add_tags()``, ``tre_expand_ast()``,
``tre_compute_nfl()``, ``tre_ast_to_tnfa()``, ``tre_tnfa_run_parallel()`` and
``tre_fill_pmatch()``) so such patterns match exactly as in R. It runs in
Python, so :mod:`pytacheck._r.regex` only uses it for patterns with minimal
repetitions (and no back references, which TRE matches with its backtracking
matcher instead).
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field

from pytacheck._r import _charset as cs
from pytacheck._r import _tre

LIT, CAT, UNION, ITER = range(4)
CHAR, EMPTY, ASSERTION, TAG, BACKREF = range(5)

ASSERT_AT_BOL = 1
ASSERT_AT_EOL = 2
ASSERT_AT_BOW = 16
ASSERT_AT_EOW = 32
ASSERT_AT_WB = 64
ASSERT_AT_WB_NEG = 128
_ASSERT_BITS = {
    "BOL": ASSERT_AT_BOL,
    "EOL": ASSERT_AT_EOL,
    "BOW": ASSERT_AT_BOW,
    "EOW": ASSERT_AT_EOW,
    "WB": ASSERT_AT_WB,
    "WB_NEG": ASSERT_AT_WB_NEG,
}
MINIMIZE, MAXIMIZE = 0, 1


class _N:
    """A TRE AST node (``tre_ast_node_t`` with its ``obj``)."""

    __slots__ = (
        "arg",
        "chars",
        "copy_chars",
        "firstpos",
        "group",
        "kind",
        "lastpos",
        "left",
        "max",
        "min",
        "minimal",
        "nullable",
        "num_submatches",
        "num_tags",
        "position",
        "right",
        "submatch_id",
        "type",
        "value",
    )

    def __init__(self, type_: int) -> None:
        self.type = type_
        self.kind = EMPTY
        self.chars: cs.Ranges = ()
        self.copy_chars: cs.Ranges | None = None
        self.value = -1  # assertion bits, tag id or back reference number
        self.position = -1
        self.group = -1  # the bracket (in one copy) a CHAR literal belongs to
        self.left: _N | None = None
        self.right: _N | None = None
        self.arg: _N | None = None
        self.min = 0
        self.max = -1
        self.minimal = False
        self.submatch_id = -1
        self.num_submatches = 0
        self.num_tags = 0
        self.nullable = False
        self.firstpos: list[_Pos] = []
        self.lastpos: list[_Pos] = []

    def assign(self, other: _N) -> None:
        """Take over *other*'s node contents (``node->obj = other->obj``)."""
        for k in (
            "type",
            "kind",
            "chars",
            "copy_chars",
            "value",
            "position",
            "group",
            "left",
            "right",
        ):
            setattr(self, k, getattr(other, k))
        for k in ("arg", "min", "max", "minimal"):
            setattr(self, k, getattr(other, k))


def _lit(kind: int, value: int = -1, position: int = -1) -> _N:
    n = _N(LIT)
    n.kind, n.value, n.position = kind, value, position
    return n


def _cat(a: _N, b: _N) -> _N:
    n = _N(CAT)
    n.left, n.right = a, b
    n.num_submatches = a.num_submatches + b.num_submatches
    return n


def _union(a: _N, b: _N) -> _N:
    n = _N(UNION)
    n.left, n.right = a, b
    n.num_submatches = a.num_submatches + b.num_submatches
    return n


def _iter(arg: _N, mn: int, mx: int, minimal: bool) -> _N:
    n = _N(ITER)
    n.arg, n.min, n.max, n.minimal = arg, mn, mx, minimal
    n.num_submatches = arg.num_submatches
    return n


@dataclass
class _Pos:
    """An entry of ``firstpos`` / ``lastpos`` (``tre_pos_and_tags_t``)."""

    position: int
    chars: cs.Ranges
    assertions: int = 0
    tags: list[int] = field(default_factory=list)


@dataclass
class _Trans:
    chars: cs.Ranges
    target: int
    assertions: int
    tags: list[int]


class Unsupported(Exception):
    """A pattern this matcher does not handle (back references)."""


# ---------------------------------------------------------------------------
# From the pytacheck._r._tre parse to TRE's AST
# ---------------------------------------------------------------------------


class _Builder:
    def __init__(self) -> None:
        self.position = 0
        self.groups = 0

    def build(self, n: _tre.Node) -> _N:
        k = n.kind
        if k == "set":
            pos = self.position
            self.position += 1
            leaves = []
            self.groups += 1
            for _ in range(max(n.n_items, 1)):
                leaf = _lit(CHAR, position=pos)
                leaf.chars, leaf.copy_chars = n.chars, n.copy_chars
                leaf.group = self.groups
                leaves.append(leaf)
            t = leaves[0]
            for leaf in leaves[1:]:
                t = _union(t, leaf)
        elif k == "empty":
            t = _lit(EMPTY)
        elif k == "assert":
            t = _lit(ASSERTION, _ASSERT_BITS[n.name])
        elif k == "backref":
            raise Unsupported("back references use TRE's backtracking matcher")
        elif k in ("cat", "union"):
            assert n.left is not None and n.right is not None
            a, b = self.build(n.left), self.build(n.right)
            t = _cat(a, b) if k == "cat" else _union(a, b)
        else:  # iter
            assert n.left is not None
            arg = self.build(n.left)
            # tre_parse_bound(): {0} (or {0,0}) is an empty literal
            t = _lit(EMPTY) if n.min == 0 and n.max == 0 else _iter(arg, n.min, n.max, n.minimal)
        if n.submatch_id >= 0:
            t.submatch_id = n.submatch_id
            t.num_submatches += 1
        return t


# ---------------------------------------------------------------------------
# tre_add_tags()
# ---------------------------------------------------------------------------


def _add_tag_left(node: _N, tag: int) -> None:
    inner = _N(node.type)
    inner.assign(node)
    node.type = CAT
    node.left = _lit(TAG, tag)
    node.right = inner


def _add_tag_right(node: _N, tag: int) -> None:
    inner = _N(node.type)
    inner.assign(node)
    node.type = CAT
    node.left = inner
    node.right = _lit(TAG, tag)


class _Regset:
    """``regset``: submatch start (2 id) / end (2 id + 1) markers waiting for a
    tag, with a movable bottom (``ADDTAGS_AFTER_UNION_LEFT``)."""

    def __init__(self) -> None:
        self.items: list[int] = []
        self.base = 0

    def live(self) -> list[int]:
        return self.items[self.base :]

    def nonempty(self) -> bool:
        return len(self.items) > self.base

    def add(self, v: int) -> None:
        self.items.append(v)

    def clear(self) -> None:
        del self.items[self.base :]


class _TNFA:
    def __init__(self, nsub: int) -> None:
        self.num_submatches = nsub + 1
        self.so_tag = [-1] * (nsub + 1)
        self.eo_tag = [-1] * (nsub + 1)
        self.parents: list[list[int]] = [[] for _ in range(nsub + 1)]
        self.tag_directions: dict[int, int] = {}
        self.minimal_tags: list[tuple[int, int]] = []  # (end, start)
        self.num_tags = 0
        self.end_tag = 0
        self.transitions: dict[int, list[_Trans]] = {}
        self.initial: list[_Pos] = []
        self.final = -1


def _purge(regset: _Regset, tnfa: _TNFA, tag: int) -> None:
    for v in regset.live():
        if v % 2 == 0:
            tnfa.so_tag[v // 2] = tag
        else:
            tnfa.eo_tag[v // 2] = tag
    regset.clear()


def _add_tags(tree: _N, tnfa: _TNFA | None) -> int:
    """Port of ``tre_add_tags()``; the first pass (``tnfa`` None) counts tags."""
    first_pass = tnfa is None
    regset = _Regset()
    parents: list[int] = []
    num_tags = 0
    tag = 0
    next_tag = 1
    minimal_tag = -1
    direction = MINIMIZE
    stack: list[object] = [tree, "RECURSE"]

    def add_minimal(t: int) -> None:
        nonlocal minimal_tag
        if minimal_tag >= 0:
            assert tnfa is not None
            tnfa.minimal_tags.append((t, minimal_tag))
            minimal_tag = -1

    while stack:
        symbol = stack.pop()
        if symbol == "SET_SUBMATCH_END":
            sid = stack.pop()
            assert isinstance(sid, int)
            regset.add(sid * 2 + 1)
            parents.pop()
            continue
        if symbol == "RECURSE":
            node = stack.pop()
            assert isinstance(node, _N)
            if node.submatch_id >= 0:
                sid = node.submatch_id
                regset.add(sid * 2)
                if not first_pass:
                    assert tnfa is not None
                    tnfa.parents[sid] = list(parents)
                stack.append(sid)
                stack.append("SET_SUBMATCH_END")
            if node.type == LIT:
                if node.kind in (CHAR, BACKREF) and regset.nonempty():
                    if not first_pass:
                        assert tnfa is not None
                        _add_tag_left(node, tag)
                        tnfa.tag_directions[tag] = direction
                        add_minimal(tag)
                        _purge(regset, tnfa, tag)
                    else:
                        node.num_tags = 1
                    regset.clear()
                    tag = next_tag
                    num_tags += 1
                    next_tag += 1
            elif node.type == CAT:
                left, right = node.left, node.right
                assert left is not None and right is not None
                reserved_tag = -1
                stack.extend([node, "AFTER_CAT_RIGHT", right, "RECURSE"])
                stack.append(next_tag + left.num_tags)
                if left.num_tags > 0 and right.num_tags > 0:
                    reserved_tag = next_tag
                    next_tag += 1
                stack.extend([reserved_tag, "AFTER_CAT_LEFT", left, "RECURSE"])
            elif node.type == ITER:
                arg = node.arg
                assert arg is not None
                if first_pass:
                    stack.append(regset.nonempty() or node.minimal)
                else:
                    stack.append(tag)
                    stack.append(node.minimal)
                stack.extend([node, "AFTER_ITERATION", arg, "RECURSE"])
                if regset.nonempty() or node.minimal:
                    if not first_pass:
                        assert tnfa is not None
                        minimal = node.minimal
                        _add_tag_left(node, tag)
                        tnfa.tag_directions[tag] = MAXIMIZE if minimal else direction
                        add_minimal(tag)
                        _purge(regset, tnfa, tag)
                    regset.clear()
                    tag = next_tag
                    num_tags += 1
                    next_tag += 1
                direction = MINIMIZE
            else:  # UNION
                left, right = node.left, node.right
                assert left is not None and right is not None
                if regset.nonempty():
                    left_tag, right_tag = next_tag, next_tag + 1
                else:
                    left_tag, right_tag = tag, next_tag
                stack.extend([right_tag, left_tag, regset.base, regset.nonempty()])
                stack.extend([node, right, left, "AFTER_UNION_RIGHT", right, "RECURSE"])
                stack.extend(["AFTER_UNION_LEFT", left, "RECURSE"])
                if regset.nonempty():
                    if not first_pass:
                        assert tnfa is not None
                        num_sub = node.num_submatches
                        _add_tag_left(node, tag)
                        node.num_submatches = num_sub
                        tnfa.tag_directions[tag] = direction
                        add_minimal(tag)
                        _purge(regset, tnfa, tag)
                    regset.clear()
                    tag = next_tag
                    num_tags += 1
                    next_tag += 1
                if node.num_submatches > 0:
                    next_tag += 1
                    tag = next_tag
                    next_tag += 1
            if node.submatch_id >= 0:
                parents.append(node.submatch_id)
            continue
        if symbol == "AFTER_ITERATION":
            node = stack.pop()
            assert isinstance(node, _N)
            if first_pass:
                extra = stack.pop()
                assert node.arg is not None
                node.num_tags = node.arg.num_tags + int(bool(extra))
                minimal_tag = -1
            else:
                was_minimal = stack.pop()
                enter_tag = stack.pop()
                assert isinstance(enter_tag, int)
                if was_minimal:
                    minimal_tag = enter_tag
                direction = MINIMIZE if was_minimal else MAXIMIZE
            continue
        if symbol == "AFTER_CAT_LEFT":
            new_tag = stack.pop()
            nt = stack.pop()
            assert isinstance(new_tag, int) and isinstance(nt, int)
            next_tag = nt
            if new_tag >= 0:
                tag = new_tag
            continue
        if symbol == "AFTER_CAT_RIGHT":
            node = stack.pop()
            assert isinstance(node, _N)
            if first_pass:
                assert node.left is not None and node.right is not None
                node.num_tags = node.left.num_tags + node.right.num_tags
            continue
        if symbol == "AFTER_UNION_LEFT":
            regset.base = len(regset.items)
            continue
        if symbol == "AFTER_UNION_RIGHT":
            branch_l = stack.pop()
            branch_r = stack.pop()
            node = stack.pop()
            added = stack.pop()
            base = stack.pop()
            tag_left = stack.pop()
            tag_right = stack.pop()
            assert isinstance(node, _N) and isinstance(branch_l, _N) and isinstance(branch_r, _N)
            assert (
                isinstance(base, int) and isinstance(tag_left, int) and isinstance(tag_right, int)
            )
            if first_pass:
                assert node.left is not None and node.right is not None
                node.num_tags = (
                    node.left.num_tags
                    + node.right.num_tags
                    + int(bool(added))
                    + (2 if node.num_submatches > 0 else 0)
                )
            regset.base = base
            if node.num_submatches > 0:
                if not first_pass:
                    assert tnfa is not None
                    _add_tag_right(branch_l, tag_left)
                    tnfa.tag_directions[tag_left] = MAXIMIZE
                    _add_tag_right(branch_r, tag_right)
                    tnfa.tag_directions[tag_right] = MAXIMIZE
                num_tags += 2
            direction = MAXIMIZE
            continue
        raise AssertionError(symbol)  # pragma: no cover

    if not first_pass:
        assert tnfa is not None
        _purge(regset, tnfa, tag)
        if minimal_tag >= 0:
            tnfa.minimal_tags.append((tag, minimal_tag))
        tnfa.end_tag = num_tags
        tnfa.num_tags = num_tags
    return num_tags


# ---------------------------------------------------------------------------
# tre_expand_ast() / tre_copy_ast()
# ---------------------------------------------------------------------------

COPY_REMOVE_TAGS = 1
COPY_MAXIMIZE_FIRST_TAG = 2


class _Expander:
    """``tre_expand_ast()`` with its position arithmetic.

    TRE renumbers the positions of copied literals with a running offset
    (``pos_add``) that grows by the number of literal *nodes* copied (a
    bracket's items share one position, so this leaves holes) and is wound
    back by one copy after each expansion; positions after an expansion
    nested in another iteration are shifted by that wound-back offset. Two
    literals can end up with the same position, and TRE then merges their
    states: ``(-*|([.-]{1,2})+\\s\\s)?`` matches ``"- "`` in R. The
    arithmetic is reproduced so that such patterns match as in R.
    """

    def __init__(self, tnfa: _TNFA, position: int, wide: bool, groups: int = 0) -> None:
        self.tnfa = tnfa
        self.position = position  # the parser's next position
        self.wide = wide
        self.groups = groups
        self.pos_add = 0
        self.pos_add_total = 0
        self.max_pos = 0
        self.iter_depth = 0

    def copy(self, node: _N, flags: int) -> _N:
        """``tre_copy_ast()``: positions offset by ``pos_add``, which then
        grows by the number of literal nodes copied; tags removed or the
        first one maximized."""
        num_copied = 0
        first_tag = True
        groups: dict[int, int] = {}

        def rec(n: _N) -> _N:
            nonlocal num_copied, first_tag
            if n.type == LIT:
                kind, value, pos = n.kind, n.value, n.position
                if kind in (CHAR, BACKREF):
                    pos += self.pos_add
                    num_copied += 1
                elif kind == TAG and flags & COPY_REMOVE_TAGS:
                    kind, value, pos = EMPTY, -1, -1
                elif kind == TAG and flags & COPY_MAXIMIZE_FIRST_TAG and first_tag:
                    self.tnfa.tag_directions[value] = MAXIMIZE
                    first_tag = False
                self.max_pos = max(self.max_pos, pos)
                out = _lit(kind, value, pos)
                if n.group >= 0:
                    if n.group not in groups:
                        self.groups += 1
                        groups[n.group] = self.groups
                    out.group = groups[n.group]
                # the copy keeps u.class but not neg_classes
                out.chars = n.copy_chars if (self.wide and n.copy_chars is not None) else n.chars
                out.copy_chars = n.copy_chars
                return out
            if n.type in (CAT, UNION):
                assert n.left is not None and n.right is not None
                a = rec(n.left)
                b = rec(n.right)
                out = _N(n.type)
                out.left, out.right = a, b
                return out
            assert n.arg is not None
            out = _N(ITER)
            out.arg = rec(n.arg)
            out.min, out.max, out.minimal = n.min, n.max, n.minimal
            return out

        out = rec(node)
        self.pos_add += num_copied
        return out

    def expand(self, node: _N) -> None:
        if node.type == LIT:
            if node.kind in (CHAR, BACKREF):
                node.position += self.pos_add
                self.max_pos = max(self.max_pos, node.position)
            return
        if node.type in (CAT, UNION):
            assert node.left is not None and node.right is not None
            self.expand(node.left)
            self.expand(node.right)
            return
        assert node.arg is not None
        mn, mx = node.min, node.max
        expanded = mn > 1 or mx > 1
        saved = self.pos_add
        if expanded:
            # the copies get their positions when they are made
            self.pos_add = 0
        self.iter_depth += 1
        self.expand(node.arg)
        self.pos_add = pos_add_last = saved
        if expanded:
            pos_add_save = self.pos_add
            seq1: _N | None = None
            for j in range(mn):
                flags = COPY_REMOVE_TAGS if j + 1 < mn else COPY_MAXIMIZE_FIRST_TAG
                pos_add_save = self.pos_add
                c = self.copy(node.arg, flags)
                seq1 = c if seq1 is None else _cat(seq1, c)
            seq2: _N | None = None
            if mx == -1:
                pos_add_save = self.pos_add
                seq2 = _iter(self.copy(node.arg, 0), 0, -1, False)
            else:
                for _ in range(mn, mx):
                    pos_add_save = self.pos_add
                    c = self.copy(node.arg, 0)
                    seq2 = c if seq2 is None else _cat(c, seq2)
                    seq2 = _union(_lit(EMPTY), seq2)
            self.pos_add = pos_add_save
            if seq1 is None:
                seq1 = seq2
            elif seq2 is not None:
                seq1 = _cat(seq1, seq2)
            assert seq1 is not None
            node.assign(seq1)
        self.iter_depth -= 1
        self.pos_add_total += self.pos_add - pos_add_last
        if self.iter_depth == 0:
            self.pos_add = self.pos_add_total

    def final_position(self) -> int:
        """The position of the final state's dummy literal."""
        return max(self.position + self.pos_add_total, self.max_pos)


# ---------------------------------------------------------------------------
# tre_compute_nfl(), tre_match_empty(), tre_ast_to_tnfa()
# ---------------------------------------------------------------------------


def _match_empty(node: _N) -> tuple[list[int], int]:
    """Tags and assertions on the empty path through *node*."""
    tags: list[int] = []
    assertions = 0
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type == LIT:
            if n.kind == TAG and n.value >= 0:
                if n.value not in tags:
                    tags.append(n.value)
            elif n.kind == ASSERTION:
                assertions |= n.value
        elif n.type == UNION:
            assert n.left is not None and n.right is not None
            if n.left.nullable:
                stack.append(n.left)
            elif n.right.nullable:
                stack.append(n.right)
        elif n.type == CAT:
            assert n.left is not None and n.right is not None
            stack.append(n.left)
            stack.append(n.right)
        else:
            assert n.arg is not None
            if n.arg.nullable:
                stack.append(n.arg)
    return tags, assertions


def _set_union(s1: list[_Pos], s2: list[_Pos], tags: list[int], assertions: int) -> list[_Pos]:
    out = [_Pos(p.position, p.chars, p.assertions | assertions, p.tags + tags) for p in s1]
    out.extend(_Pos(p.position, p.chars, p.assertions, list(p.tags)) for p in s2)
    return out


def _compute_nfl(node: _N) -> None:
    if node.type == LIT:
        if node.kind == CHAR:
            node.nullable = False
            node.firstpos = [_Pos(node.position, node.chars)]
            node.lastpos = [_Pos(node.position, node.chars)]
        else:
            node.nullable = True
            node.firstpos, node.lastpos = [], []
        return
    if node.type in (UNION, CAT):
        assert node.left is not None and node.right is not None
        left, right = node.left, node.right
        _compute_nfl(left)
        _compute_nfl(right)
        if node.type == UNION:
            node.nullable = left.nullable or right.nullable
            node.firstpos = _set_union(left.firstpos, right.firstpos, [], 0)
            node.lastpos = _set_union(left.lastpos, right.lastpos, [], 0)
            return
        node.nullable = left.nullable and right.nullable
        if left.nullable:
            tags, asserts = _match_empty(left)
            node.firstpos = _set_union(right.firstpos, left.firstpos, tags, asserts)
        else:
            node.firstpos = left.firstpos
        if right.nullable:
            tags, asserts = _match_empty(right)
            node.lastpos = _set_union(left.lastpos, right.lastpos, tags, asserts)
        else:
            node.lastpos = right.lastpos
        return
    assert node.arg is not None
    _compute_nfl(node.arg)
    node.nullable = node.min == 0 or node.arg.nullable
    node.firstpos = node.arg.firstpos
    node.lastpos = node.arg.lastpos


def _make_trans(p1s: list[_Pos], p2s: list[_Pos], tnfa: _TNFA) -> None:
    for p1 in p1s:
        prev = -1
        for p2 in p2s:
            if p2.position == prev:
                continue
            prev = p2.position
            tags = list(p1.tags) + [t for t in p2.tags if t not in p1.tags]
            tnfa.transitions.setdefault(p1.position, []).append(
                _Trans(p1.chars, p2.position, p1.assertions | p2.assertions, tags)
            )


def _to_tnfa(node: _N, tnfa: _TNFA) -> None:
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type == UNION:
            assert n.left is not None and n.right is not None
            stack.append(n.right)
            stack.append(n.left)
        elif n.type == CAT:
            assert n.left is not None and n.right is not None
            _make_trans(n.left.lastpos, n.right.firstpos, tnfa)
            stack.append(n.right)
            stack.append(n.left)
        elif n.type == ITER:
            assert n.arg is not None
            if n.max != -1 and n.max != 1:
                raise _tre.TREError("", "BADBR")
            if n.max == -1:
                if n.min not in (0, 1):
                    raise _tre.TREError("", "BADBR")
                _make_trans(n.arg.lastpos, n.arg.firstpos, tnfa)
            stack.append(n.arg)


@functools.lru_cache(maxsize=256)
def compile_tnfa(pattern: str, icase: bool = False, wide: bool = False) -> _TNFA:
    """Compile a TRE *pattern* the way ``tre_compile()`` does."""
    tree_src, nsub = _tre.parse(pattern, icase)
    builder = _Builder()
    tree = builder.build(tree_src)
    tnfa = _TNFA(nsub)
    _add_tags(tree, None)
    _add_tags(tree, tnfa)
    exp = _Expander(tnfa, builder.position, wide, builder.groups)
    exp.expand(tree)
    final = _lit(CHAR, position=exp.final_position())
    final.chars = ((0, 0),)
    root = _cat(tree, final)
    _compute_nfl(root)
    _to_tnfa(root, tnfa)
    tnfa.initial = root.firstpos
    tnfa.final = root.lastpos[0].position
    return tnfa


def _shares_positions(tree: _N, final: int) -> bool:
    """Do two brackets (or a bracket and the final state) share a position?"""
    owner: dict[int, int] = {}
    stack = [tree]
    while stack:
        n = stack.pop()
        if n.type == LIT:
            if n.kind in (CHAR, BACKREF) and owner.setdefault(n.position, n.group) != n.group:
                return True
        elif n.type == ITER:
            assert n.arg is not None
            stack.append(n.arg)
        else:
            assert n.left is not None and n.right is not None
            stack += [n.left, n.right]
    return final in owner


@functools.lru_cache(maxsize=4096)
def merges_states(pattern: str, icase: bool = False) -> bool:
    """Does ``tre_expand_ast()`` give two literals of *pattern* one position
    (see :class:`_Expander`), so that only this matcher matches it as R does?
    ``False`` for patterns with back references (not handled here)."""
    try:
        tree_src, nsub = _tre.parse(pattern, icase)
        builder = _Builder()
        tree = builder.build(tree_src)
    except Unsupported:
        return False
    exp = _Expander(_TNFA(nsub), builder.position, False, builder.groups)
    exp.expand(tree)
    return _shares_positions(tree, exp.final_position())


# ---------------------------------------------------------------------------
# tre_tnfa_run_parallel()
# ---------------------------------------------------------------------------


def _check_assertions(a: int, pos: int, prev_c: int, next_c: int, notbol: bool) -> bool:
    """``CHECK_ASSERTIONS``: true when an assertion *fails*."""
    word = _is_word
    if a & ASSERT_AT_BOL and (pos > 0 or notbol):
        return True
    if a & ASSERT_AT_EOL and next_c != 0:
        return True
    if a & ASSERT_AT_BOW and (word(prev_c) or not word(next_c)):
        return True
    if a & ASSERT_AT_EOW and (not word(prev_c) or word(next_c)):
        return True
    if a & ASSERT_AT_WB and pos != 0 and next_c != 0 and word(prev_c) == word(next_c):
        return True
    return bool(a & ASSERT_AT_WB_NEG and (pos == 0 or next_c == 0 or word(prev_c) != word(next_c)))


@functools.cache
def _word_set() -> cs.Ranges:
    return cs.tre_word()


def _is_word(c: int) -> bool:
    if c < 0x80:
        return c == 0x5F or 0x30 <= c <= 0x39 or 0x41 <= c <= 0x5A or 0x61 <= c <= 0x7A
    return cs.contains(_word_set(), c)


def _tag_wins(dirs: dict[int, int], num_tags: int, t1: list[int], t2: list[int]) -> bool:
    """``tre_tag_order()``: does *t1* win over *t2*?"""
    for i in range(num_tags):
        if dirs.get(i, -1) == MINIMIZE:
            if t1[i] < t2[i]:
                return True
            if t1[i] > t2[i]:
                return False
        else:
            if t1[i] > t2[i]:
                return True
            if t1[i] < t2[i]:
                return False
    return False


def run(tnfa: _TNFA, s: str, offset: int = 0, notbol: bool = False) -> list[tuple[int, int]] | None:
    """Match against ``s[offset:]`` like R does (a new string at *offset*);
    returns the submatch spans (absolute, ``(-1, -1)`` unset) or ``None``."""
    text = [ord(c) for c in s[offset:]]
    n = len(text)
    num_tags = tnfa.num_tags
    dirs = tnfa.tag_directions
    trans = tnfa.transitions
    match_eo = -1
    match_tags: list[int] = [-1] * num_tags
    new_match = False
    reach_next: list[tuple[int, list[int]]] = []
    reach_pos: dict[int, int] = {}
    reach_tags: dict[int, list[int]] = {}
    # GET_NEXT_WCHAR at the start
    pos = 0
    prev_c = 0
    next_c = text[0] if n else 0

    while True:
        if match_eo < 0:
            for p in tnfa.initial:
                if reach_pos.get(p.position, -1) < pos:
                    if p.assertions and _check_assertions(
                        p.assertions, pos, prev_c, next_c, notbol
                    ):
                        continue
                    tags = [-1] * num_tags
                    for t in p.tags:
                        if t < num_tags:
                            tags[t] = pos
                    if p.position == tnfa.final:
                        match_eo = pos
                        new_match = True
                        match_tags = list(tags)
                    reach_pos[p.position] = pos
                    reach_tags[p.position] = tags
                    reach_next.append((p.position, tags))
        elif num_tags == 0 or not reach_next:
            break
        if pos >= n:
            break
        # GET_NEXT_WCHAR
        prev_c = next_c
        pos += 1
        next_c = text[pos] if pos < n else 0
        reach = reach_next
        if tnfa.minimal_tags and new_match:
            new_match = False
            kept = []
            for state, tags in reach:
                skip = False
                for end, start in tnfa.minimal_tags:
                    if end >= num_tags:
                        skip = True
                        break
                    if tags[start] == match_tags[start] and tags[end] < match_tags[end]:
                        skip = True
                        break
                if not skip:
                    kept.append((state, tags))
            reach = kept
        reach_next = []
        for state, tags in reach:
            for tr in trans.get(state, ()):
                if not cs.contains(tr.chars, prev_c):
                    continue
                if tr.assertions and _check_assertions(tr.assertions, pos, prev_c, next_c, notbol):
                    continue
                tmp = list(tags)
                for t in tr.tags:
                    if t < num_tags:
                        tmp[t] = pos
                target = tr.target
                if reach_pos.get(target, -1) < pos:
                    reach_pos[target] = pos
                    reach_tags[target] = tmp
                    entry = (target, tmp)
                    reach_next.append(entry)
                    if target == tnfa.final and (
                        match_eo == -1 or (num_tags > 0 and tmp[0] <= match_tags[0])
                    ):
                        match_eo = pos
                        new_match = True
                        match_tags = list(tmp)
                elif _tag_wins(dirs, num_tags, tmp, reach_tags[target]):
                    # the new path wins: replace the tags of the reached state
                    old = reach_tags[target]
                    old[:] = tmp
                    if target == tnfa.final:
                        match_eo = pos
                        new_match = True
                        match_tags = list(tmp)
    if match_eo < 0:
        return None
    return _fill_pmatch(tnfa, match_tags, match_eo, offset)


def _fill_pmatch(tnfa: _TNFA, tags: list[int], match_eo: int, offset: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for i in range(tnfa.num_submatches):
        so_tag, eo_tag = tnfa.so_tag[i], tnfa.eo_tag[i]
        so = (
            match_eo
            if so_tag == tnfa.end_tag
            else (tags[so_tag] if 0 <= so_tag < len(tags) else -1)
        )
        eo = (
            match_eo
            if eo_tag == tnfa.end_tag
            else (tags[eo_tag] if 0 <= eo_tag < len(tags) else -1)
        )
        if so == -1 or eo == -1:
            so = eo = -1
        out.append((so, eo))
    for i in range(tnfa.num_submatches):
        so, eo = out[i]
        if eo == -1:
            continue
        for parent in tnfa.parents[i]:
            pso, peo = out[parent]
            if so < pso or eo > peo:
                out[i] = (-1, -1)
                break
    return [(-1, -1) if so < 0 else (so + offset, eo + offset) for so, eo in out]
