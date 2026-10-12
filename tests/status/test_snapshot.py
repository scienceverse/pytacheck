"""The shipped registry snapshot, its schema check and gate G9 (``status.check``).

The snapshot is provisional until the metacheck team's registry exists: its
counts are copied from the ``<validation>`` blocks of the pinned metacheck
sources, and these tests compare them with those sources.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.resources
import json
import re
from pathlib import Path
from typing import Any

import pytest

from metacheck.module import _builtin_names
from metacheck.status import (
    PROVISIONAL_NOTE,
    Registry,
    StatusError,
    check,
    read_registry,
    snapshot,
    status,
    validate_registry,
)

VALIDATED = {"power", "stat_p_exact", "stat_p_nonsig", "marginal", "stat_effect_size"}
#: the validated modules whose R header says the team validated them on Psychological Science
PSYCH_SCI = {"power", "stat_effect_size"}


def _raw() -> bytes:
    return (
        importlib.resources.files("metacheck.resources")
        .joinpath("status/validation.json")
        .read_bytes()
    )


def _data() -> dict[str, Any]:
    return json.loads(_raw())


def _registry(data: Any) -> Registry:
    return Registry.from_bytes(json.dumps(data).encode(), where="test.json")


def _r_block(upstream_dir: Path, name: str) -> str:
    source = (upstream_dir / "inst" / "modules" / f"{name}.R").read_text(encoding="utf-8")
    m = re.search(r"<validation>(.*?)</validation>", source, flags=re.DOTALL)
    assert m, f"{name}.R has no <validation> block"
    return " ".join(m.group(1).split())


def test_snapshot_ships_in_the_package() -> None:
    root = importlib.resources.files("metacheck.resources")
    assert root.joinpath("status/validation.json").is_file()


def test_snapshot_schema_is_valid() -> None:
    reg = snapshot()
    assert reg.data == validate_registry(_data())
    assert reg.digest == hashlib.sha256(_raw()).hexdigest()
    assert check() == []


def test_snapshot_is_provisional() -> None:
    reg = snapshot()
    assert reg.provisional
    assert reg.registry_commit is None
    assert reg.team_packs == {"metacheck"}
    assert all(entry["provisional"] for entry in reg.modules.values())
    assert reg.withdrawn == {}


def test_every_builtin_has_an_entry() -> None:
    assert {ref.removeprefix("metacheck::") for ref in snapshot().modules} == set(_builtin_names())


def test_exactly_five_builtins_are_validated() -> None:
    labels = {name: status(name) for name in _builtin_names()}
    assert {n for n, s in labels.items() if s.label == "validated"} == VALIDATED
    assert {s.label for n, s in labels.items() if n not in VALIDATED} == {"experimental"}
    for name in VALIDATED:
        assert labels[name].provisional
        assert labels[name].note == PROVISIONAL_NOTE
        assert labels[name].stale_note is None


def test_counts_are_copied_from_the_r_blocks(upstream_dir: Path) -> None:
    version = re.search(
        r"^Version:\s*(\S+)",
        (upstream_dir / "DESCRIPTION").read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    assert version
    for name in sorted(VALIDATED):
        entry = snapshot().modules[f"metacheck::{name}"]
        assert entry["implementations"] == {"r": {"metacheck": [version.group(1)]}}, name
        (ev,) = entry["evidence"]
        text = _r_block(upstream_dir, name)
        assert ev["summary"] == text, name
        numbers = {int(n) for n in re.findall(r"\b\d+\b", text)}
        stated = [*(v for v in ev["counts"].values() if v is not None), ev["corpus"]["papers"]]
        assert set(stated) <= numbers, name
        assert text.startswith(f"In a sample of {ev['corpus']['papers']} papers"), name


#: ECOSYSTEM.md §1.1: papers, (tp, fp, fn, tn), and the PPV and sensitivity derived
#: from them; stat_p_nonsig states no FN, so it has no sensitivity
_COUNTS = {
    "power": (128, (203, 21, 22, None), 0.906, 0.902),
    "stat_p_exact": (225, (269, 78, 136, 4557), 0.775, 0.664),
    "stat_p_nonsig": (194, (1486, 153, None, None), 0.907, None),
    "marginal": (51, (38, 22, 27, None), 0.633, 0.585),
    "stat_effect_size": (161, (1106, 45, 23, 295), 0.961, 0.98),
}


@pytest.mark.parametrize("name", sorted(_COUNTS))
def test_each_count_has_its_own_key(name) -> None:
    """The R test above finds each number in the text; this catches a TP/FP/FN swap."""
    papers, (tp, fp, fn, tn), ppv, sensitivity = _COUNTS[name]
    (ev,) = snapshot().modules[f"metacheck::{name}"]["evidence"]
    assert ev["corpus"]["papers"] == papers
    assert ev["counts"] == {"tp": tp, "fp": fp, "fn": fn, "tn": tn}
    metrics = status(name).metrics
    assert (metrics.get("ppv"), metrics.get("sensitivity")) == (ppv, sensitivity)


def test_nothing_is_inferred() -> None:
    """Only what the R blocks say: no implied FN, no py binding, no invented metadata."""
    modules = snapshot().modules
    assert modules["metacheck::stat_p_nonsig"]["evidence"][0]["counts"]["fn"] is None
    for ref in (f"metacheck::{n}" for n in VALIDATED):
        assert "py" not in modules[ref]["implementations"]
        (ev,) = modules[ref]["evidence"]
        for key in ("validators", "date", "unit", "settings", "server_safe", "protocol"):
            assert ev[key] is None, (ref, key)
        named = ref.removeprefix("metacheck::") in PSYCH_SCI
        assert ev["corpus"]["source"] == ("Psychological Science" if named else None), ref
        assert ev["corpus"]["fields"] == (["psychology"] if named else None), ref


def test_the_corpus_is_named_where_r_names_it(upstream_dir: Path) -> None:
    for name in sorted(VALIDATED):
        source = (upstream_dir / "inst" / "modules" / f"{name}.R").read_text(encoding="utf-8")
        named = re.search(r"validated by the Metacheck team on [^.]*Psychological Science", source)
        assert bool(named) == (name in PSYCH_SCI), name


@pytest.mark.parametrize("name", ["stat_check", "ref_accuracy"])
def test_pending_and_candidate_modules_are_not_certified(name) -> None:
    entry = snapshot().modules[f"metacheck::{name}"]
    assert entry["evidence"] == []
    assert entry["note"]
    st = status(name)
    assert st.label == "experimental"
    assert st.note == entry["note"]


def test_read_registry(tmp_path) -> None:
    path = tmp_path / "validation.json"
    path.write_bytes(_raw())
    assert read_registry(path).data == snapshot().data
    with pytest.raises(StatusError, match="Cannot read"):
        read_registry(tmp_path / "missing.json")
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(StatusError, match="Cannot read"):
        read_registry(path)


def test_a_repeated_key_is_refused() -> None:
    """Otherwise the later, empty power entry would silently replace its evidence."""
    raw = _raw().replace(
        b'"metacheck::stat_p_nonsig": {',
        b'"metacheck::power": {"provisional": true},\n    "metacheck::stat_p_nonsig": {',
    )
    with pytest.raises(StatusError, match="'metacheck::power' appears twice"):
        Registry.from_bytes(raw, where="test.json")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_EVIDENCE = {"id": "x-1", "counts": {"tp": 1, "fp": 1, "fn": 1, "tn": None}}


def _file(**entry: Any) -> dict[str, Any]:
    """A small registry that is not provisional, with one entry for ``lab::mod``."""
    return {
        "schema": 1,
        "provisional": False,
        "registry_commit": "a" * 40,
        "team_packs": ["metacheck"],
        "modules": {"lab::mod": {"evidence": [copy.deepcopy(_EVIDENCE)], **entry}},
        "withdrawn": {},
    }


def test_a_full_evidence_record_is_valid() -> None:
    data = _file(
        implementations={
            "r": {"metacheck": ["0.3.1"]},
            "py": {"pytacheck": ["0.4.0"], "method": "rerun", "run": "https://ci/1"},
        }
    )
    data["modules"]["lab::mod"]["evidence"][0].update(
        validators=["A. Person"],
        date="2026-06-05",
        unit="statement",
        corpus={"source": "Psychological Science", "papers": 128, "fields": ["psychology"]},
        settings={"settings_sha256": "0" * 64},
        server_safe=True,
        protocol="https://osf.io/x",
        summary="In a sample of ...",
    )
    data["withdrawn"] = {"lab::old": {"date": "2026-01-01", "reason": "a known problem"}}
    out = validate_registry(data)
    assert out["modules"]["lab::mod"]["implementations"]["py"]["pytacheck"] == ["0.4.0"]
    assert out["withdrawn"]["lab::old"]["reason"] == "a known problem"


def _set(data: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    """Set *path* (keys joined by '/'; list indexes are numbers) in *data*."""
    *parents, last = path.split("/")
    node: Any = data
    for key in parents:
        node = node[int(key)] if isinstance(node, list) else node[key]
    if isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value
    return data


_EV = "modules/lab::mod/evidence/0"


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        ("schema", 2, "uses schema 2"),
        ("extra", 1, "unknown keys"),
        ("provisional", "yes", "provisional must be true or false"),
        ("registry_commit", None, "registry_commit is missing"),
        ("registry_commit", "abc", "full commit id"),
        ("team_packs", ["Not A Pack"], "Invalid pack name"),
        ("modules/lab::mod/state", "validated", "unknown keys"),
        ("modules/lab::mod/implementations", {"py": {"pytacheck": "0.4.0"}}, "list of strings"),
        ("modules/lab::mod/implementations", {"py": {"code_sha256": ["ab12"]}}, "invalid values"),
        (f"{_EV}/id", "", "id must be a non-empty string"),
        (f"{_EV}/counts", {"tp": 1, "ppv": 0.5}, "unknown keys"),
        (f"{_EV}/counts", {"tp": -1}, "whole number"),
        (f"{_EV}/counts", {"tp": True}, "whole number"),
        (f"{_EV}/counts", {"tp": 1.5}, "whole number"),
        (f"{_EV}/counts", None, "neither counts nor metrics"),
        (f"{_EV}/metrics", {"recall": "high"}, "map names to numbers"),
        (f"{_EV}/date", "June 2026", "YYYY-MM-DD"),
        (f"{_EV}/corpus", {"papers": 10, "fields": ["astrology"]}, "unknown fields"),
        (f"{_EV}/corpus", {"n": 10}, "unknown keys"),
        (f"{_EV}/server_safe", "yes", "server_safe"),
        ("withdrawn", {"lab::old": {"date": None}}, "reason must be"),
        ("withdrawn", {"lab::old": {"date": "soon", "reason": "x"}}, "YYYY-MM-DD"),
        ("withdrawn", {"old": {"reason": "x"}}, "not a pack::module ref"),
    ],
)
def test_invalid_registries(path, value, message) -> None:
    with pytest.raises(StatusError, match=message):
        validate_registry(_set(_file(), path, value))


def test_refs_must_be_qualified() -> None:
    data = _file()
    data["modules"] = {"mod": data["modules"]["lab::mod"]}
    with pytest.raises(StatusError, match="not a pack::module ref"):
        validate_registry(data)


def test_evidence_ids_are_unique() -> None:
    data = _file()
    data["modules"]["lab::other"] = copy.deepcopy(data["modules"]["lab::mod"])
    with pytest.raises(StatusError, match="used twice"):
        validate_registry(data)


def test_a_provisional_file_marks_every_entry_provisional() -> None:
    data = _file()
    data["provisional"], data["registry_commit"] = True, None
    with pytest.raises(StatusError, match="must be marked provisional"):
        validate_registry(data)
    data["modules"]["lab::mod"]["provisional"] = True
    assert validate_registry(data)["provisional"]


# ---------------------------------------------------------------------------
# check()
# ---------------------------------------------------------------------------


def test_check_finds_a_missing_builtin() -> None:
    data = _data()
    del data["modules"]["metacheck::marginal"]
    assert check(_registry(data)) == ["test.json: no entry for the built-in modules marginal"]


def test_check_finds_unknown_metacheck_modules() -> None:
    data = _data()
    data["modules"]["metacheck::no_such"] = {"provisional": True}
    data["withdrawn"]["metacheck::gone"] = {"reason": "x"}
    assert check(_registry(data)) == [
        "test.json: metacheck::no_such is not a metacheck module",
        "test.json: metacheck::gone is not a metacheck module",
    ]


def test_check_needs_metacheck_as_a_team_pack() -> None:
    data = _data()
    data["team_packs"] = []
    assert check(_registry(data)) == ["test.json: team_packs does not list metacheck"]


def test_check_reports_an_unreadable_snapshot(monkeypatch) -> None:
    import metacheck.status as status_mod

    def broken() -> Registry:
        raise StatusError("validation.json uses schema 9")

    monkeypatch.setattr(status_mod, "snapshot", broken)
    assert check() == ["validation.json uses schema 9"]
