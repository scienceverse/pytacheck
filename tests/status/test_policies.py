"""Status policies: the ladder, label lists, and which labels each policy runs."""

from __future__ import annotations

import itertools

import pytest

from metacheck.module import _builtin_names
from metacheck.packs.registry import builtin_pack
from metacheck.status import (
    DEFAULT_POLICY,
    LABELS,
    POLICIES,
    Policy,
    Status,
    StatusError,
    parse_status,
    status,
)


def _st(label: str, source: str = "builtin") -> Status:
    return Status(ref="x::y", label=label, source=source)


def test_the_ladder_nests() -> None:
    ladder = [POLICIES[p] for p in ("validated", "certified", "experimental", "all")]
    assert all(a < b for a, b in itertools.pairwise(ladder))
    assert POLICIES["all"] == set(LABELS) - {"withdrawn"}
    assert DEFAULT_POLICY == "experimental"


@pytest.mark.parametrize("name", ["validated", "certified", "experimental", "all"])
def test_policy_names(name) -> None:
    assert parse_status(name) == Policy(name, POLICIES[name])
    assert parse_status(f"  {name.upper()} ") == parse_status(name)


def test_label_lists_are_normalised() -> None:
    policy = parse_status("unvalidated, validated")
    assert policy == Policy("validated,unvalidated", frozenset({"validated", "unvalidated"}))
    assert parse_status("external-validated").labels == {"external-validated"}
    assert parse_status(policy) is policy


@pytest.mark.parametrize("value", ["", "strict", "validated,,experimental", "validated,all"])
def test_unknown_statuses(value) -> None:
    with pytest.raises(StatusError, match="Unknown status"):
        parse_status(value)


def test_withdrawn_is_never_a_status() -> None:
    with pytest.raises(StatusError, match="never run"):
        parse_status("validated,withdrawn")


def test_a_status_is_a_string() -> None:
    with pytest.raises(StatusError, match="must be a string"):
        parse_status(1)  # type: ignore[arg-type]


# which labels each policy accepts, for a built-in and for a module the user added
_ACCEPTS = {
    "validated": {"validated"},
    "certified": {"validated", "external-validated"},
    "experimental": {"validated", "external-validated", "experimental"},
    "all": {"validated", "external-validated", "experimental", "unvalidated"},
    "unvalidated": {"unvalidated"},
}


@pytest.mark.parametrize("policy", sorted(_ACCEPTS))
@pytest.mark.parametrize("label", LABELS)
def test_accepts(policy, label) -> None:
    p = parse_status(policy)
    assert p.accepts(_st(label)) == (label in _ACCEPTS[policy])
    added = label in _ACCEPTS[policy] or (policy == "experimental" and label == "unvalidated")
    assert p.accepts(_st(label, source="installed")) == added


@pytest.mark.parametrize("source", ["installed", "path", "dist", "plugin", "file", "object"])
def test_experimental_runs_what_the_user_added(source) -> None:
    assert _st("unvalidated", source).opted_in
    assert parse_status("experimental").accepts(_st("unvalidated", source))
    assert not parse_status("certified").accepts(_st("unvalidated", source))


def test_withdrawn_never_runs() -> None:
    policy = Policy("custom", frozenset(LABELS))
    assert not policy.accepts(_st("withdrawn", "installed"))


def test_a_label_list_does_not_widen() -> None:
    """Only the experimental policy runs unvalidated modules the user added."""
    assert not parse_status("validated,experimental").accepts(_st("unvalidated", "installed"))


def test_validated_keeps_five_modules_of_the_default_preset() -> None:
    policy = parse_status("validated")
    modules = builtin_pack().presets["default"]["modules"]
    assert [m for m in modules if policy.accepts(status(m))] == [
        "power",
        "stat_p_exact",
        "stat_p_nonsig",
        "stat_effect_size",
        "marginal",
    ]


def test_the_default_policy_keeps_every_builtin() -> None:
    """So report(paper) under the library default runs what it runs today."""
    policy = parse_status(DEFAULT_POLICY)
    assert all(policy.accepts(status(n)) for n in _builtin_names())
