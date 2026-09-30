"""Labels derived from a registry: team or external, the code binding and withdrawals."""

from __future__ import annotations

import hashlib
import json
import textwrap
from pathlib import Path
from typing import Any

import pytest

from metacheck._version import __version__
from metacheck.module import ModuleError, ModuleSpec, module, module_find
from metacheck.packs.registry import _path_pack, load_module, overlay
from metacheck.packs.tree import file_sha256
from metacheck.status import (
    PROVISIONAL_NOTE,
    Registry,
    code_sha256,
    snapshot,
    status,
    status_table,
)

_COUNTS = {"tp": 90, "fp": 10, "fn": 30, "tn": None}


def _registry(modules: dict[str, Any], **top: Any) -> Registry:
    data = {
        "schema": 1,
        "provisional": False,
        "registry_commit": "c" * 40,
        "team_packs": ["metacheck"],
        "modules": modules,
        "withdrawn": {},
        **top,
    }
    return Registry.from_bytes(json.dumps(data).encode(), where="test.json")


def _certified(py: dict[str, Any] | None = None, **evidence: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {"evidence": [{"id": "e-1", "counts": dict(_COUNTS), **evidence}]}
    if py is not None:
        entry["implementations"] = {"py": py}
    return entry


@pytest.fixture
def lab(tmp_path: Path):
    """A path pack ``lab`` with one module, ``apa``, active in this test only."""
    root = tmp_path / "lab"
    (root / "tests").mkdir(parents=True)
    (root / "pack.json").write_text(json.dumps({"name": "lab", "version": "1.0.0"}))
    (root / "apa.py").write_text(
        textwrap.dedent(
            """\
            from metacheck.module import module

            @module(title="APA", description="An APA check")
            def apa(paper):
                return {"summary_text": "ok"}
            """
        )
    )
    (root / "README.md").write_text("A lab pack\n")
    (root / "tests" / "test_apa.py").write_text("def test_apa():\n    pass\n")
    pack = _path_pack("lab", {"path": str(root)}, "test")
    with overlay(pack):
        yield pack


# ---------------------------------------------------------------------------
# Built-ins
# ---------------------------------------------------------------------------


def test_a_builtin_is_bound_to_exact_releases() -> None:
    reg = _registry({"metacheck::power": _certified({"pytacheck": ["0.0.1", __version__]})})
    st = status("power", registry=reg)
    assert (st.label, st.stale_note, st.team, st.provisional) == ("validated", None, True, False)
    assert st.note is None


def test_a_builtin_not_measured_on_this_release_is_experimental() -> None:
    reg = _registry({"metacheck::power": _certified({"pytacheck": ["0.0.1", "0.0.2"]})})
    st = status("metacheck::power", registry=reg)
    assert st.label == "experimental"
    assert st.stale_note == f"validated on 0.0.2, not re-measured on {__version__}"


def test_counts_measured_on_r_only_do_not_certify_the_port() -> None:
    reg = _registry({"metacheck::power": _certified()})
    st = status("power", registry=reg)
    assert st.label == "experimental"
    assert st.stale_note == f"measured on metacheck (R) only, not on pytacheck {__version__}"


def test_a_provisional_entry_counts_as_validated_without_a_binding() -> None:
    entry = {**_certified(), "provisional": True}
    st = status("power", registry=_registry({"metacheck::power": entry}))
    assert (st.label, st.stale_note, st.note) == ("validated", None, PROVISIONAL_NOTE)
    noted = _registry({"metacheck::power": {**entry, "note": "The team may drop it (Q1)."}})
    assert status("power", registry=noted).note == f"{PROVISIONAL_NOTE}. The team may drop it (Q1)."


def test_no_entry_or_no_evidence_is_experimental() -> None:
    reg = _registry({"metacheck::power": {"note": "pending"}})
    assert status("power", registry=reg).label == "experimental"
    assert status("power", registry=reg).note == "pending"
    assert status("marginal", registry=reg).label == "experimental"


def test_withdrawn_wins_over_a_certification() -> None:
    reg = _registry(
        {"metacheck::power": _certified({"pytacheck": [__version__]})},
        withdrawn={"metacheck::power": {"date": "2026-10-01", "reason": "flags every sentence"}},
    )
    st = status("power", registry=reg)
    assert (st.label, st.note) == ("withdrawn", "Withdrawn: flags every sentence")
    assert st.evidence


def test_specs_and_functions_resolve_like_names() -> None:
    spec = module_find("marginal")
    assert status(spec) == status("marginal") == status(spec.func)
    assert status("marginal").ref == "metacheck::marginal"
    assert status("marginal").source == "builtin"


def test_unknown_modules_raise() -> None:
    with pytest.raises(ModuleError):
        status("no_such_module_anywhere")


# ---------------------------------------------------------------------------
# Modules outside the team
# ---------------------------------------------------------------------------


def test_a_pack_module_without_evidence_is_unvalidated(lab) -> None:
    st = status("lab::apa", registry=_registry({}))
    assert (st.ref, st.label, st.team, st.source, st.opted_in) == (
        "lab::apa",
        "unvalidated",
        False,
        "path",
        True,
    )
    assert status("apa", registry=_registry({})) == st


def test_a_pack_module_is_bound_to_its_code(lab) -> None:
    digest = code_sha256(lab.root)
    reg = _registry({"lab::apa": _certified({"code_sha256": [digest], "version": "1.0.0"})})
    assert status("lab::apa", registry=reg).label == "external-validated"

    (lab.root / "README.md").write_text("A lab pack, now documented\n")
    (lab.root / "tests" / "test_apa.py").write_text("def test_apa():\n    assert True\n")
    assert status("lab::apa", registry=reg).label == "external-validated"

    (lab.root / "apa.py").write_text((lab.root / "apa.py").read_text() + "\n# changed\n")
    st = status("lab::apa", registry=reg)
    assert st.label == "unvalidated"
    assert st.stale_note == (
        f"validated at 1.0.0 (code {digest[:12]}…), "
        f"this is 1.0.0 (code {code_sha256(lab.root)[:12]}…)"
    )


def test_a_pack_module_measured_elsewhere_is_unvalidated(lab) -> None:
    st = status("lab::apa", registry=_registry({"lab::apa": _certified()}))
    assert st.label == "unvalidated"
    assert st.stale_note == "measured on another implementation, not on this code"


def test_a_team_pack_derives_team_labels(lab) -> None:
    teams = ["metacheck", "lab"]
    certified = _registry(
        {"lab::apa": _certified({"code_sha256": [code_sha256(lab.root)]})}, team_packs=teams
    )
    assert status("lab::apa", registry=certified).label == "validated"
    assert status("lab::apa", registry=_registry({}, team_packs=teams)).label == "experimental"


def test_a_loaded_pack_spec_resolves_to_its_pack(lab) -> None:
    spec = load_module(lab, "apa")
    assert status(spec, registry=_registry({})).ref == "lab::apa"
    (lab.root / "wordy.py").write_text(
        "from metacheck.module import module\n\n"
        '@module(title="Wordy")\n'
        "def count_words(paper):\n"
        "    return {}\n"
    )
    spec = load_module(lab, "wordy")  # the ref names the file, not the function
    assert (spec.name, status(spec, registry=_registry({})).ref) == ("count_words", "lab::wordy")


def test_modules_outside_packs_are_unvalidated(tmp_path: Path) -> None:
    path = tmp_path / "mine.py"
    path.write_text(
        "from metacheck.module import module\n\n"
        '@module(title="Mine")\n'
        "def mine(paper):\n"
        "    return {}\n"
    )
    st = status(str(path))
    assert (st.label, st.source, st.opted_in, st.team) == ("unvalidated", "file", True, False)

    @module(title="Inline")
    def inline(paper):
        return {}

    assert status(inline).source == "file"  # its path is this test file
    bare = ModuleSpec(name="bare", func=inline, title="Bare")
    assert (status(bare).ref, status(bare).source) == ("bare", "object")


# ---------------------------------------------------------------------------
# code_sha256
# ---------------------------------------------------------------------------


def test_code_sha256_hashes_code_and_data_only(tmp_path: Path) -> None:
    files = {
        "apa.py": "x = 1\n",
        "_helpers.py": "y = 2\n",
        "lib/core.py": "z = 3\n",
        "apa.yaml": "schema: pytacheck.module/1\n",
        "data/words.csv": "a,b\n",
        "data/nested/more.txt": "c\n",
        # left out
        "pack.json": "{}\n",
        "README.md": "readme\n",
        "lib/notes.yaml": "not a module\n",
        "tests/test_apa.py": "def test(): pass\n",
        "docs/conf.py": "project = 'x'\n",
        ".github/workflows/ci.yml": "on: push\n",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    hashed = sorted(
        [
            "_helpers.py",
            "apa.py",
            "apa.yaml",
            "data/nested/more.txt",
            "data/words.csv",
            "lib/core.py",
        ]
    )
    lines = "".join(f"{file_sha256(tmp_path / rel)}  {rel}\n" for rel in hashed)
    assert code_sha256(tmp_path) == hashlib.sha256(lines.encode()).hexdigest()


# ---------------------------------------------------------------------------
# status_table and metrics
# ---------------------------------------------------------------------------


def test_metrics_derive_ppv_and_sensitivity() -> None:
    reg = _registry({"metacheck::power": _certified(corpus={"papers": 12})})
    assert status("power", registry=reg).metrics == {
        **_COUNTS,
        "papers": 12,
        "ppv": 0.9,
        "sensitivity": 0.75,
    }
    assert status("stat_p_nonsig").metrics["ppv"] == 0.907
    assert "sensitivity" not in status("stat_p_nonsig").metrics  # FN is not stated
    assert status("all_urls").metrics == {}


def test_metrics_evidence_is_passed_through() -> None:
    entry = {"evidence": [{"id": "m-1", "metrics": {"flag_rate_genuine": 0.08}}]}
    st = status("ref_accuracy", registry=_registry({"metacheck::ref_accuracy": entry}))
    assert st.metrics == {"flag_rate_genuine": 0.08}


def test_status_table_lists_the_builtins() -> None:
    table = status_table()
    assert list(table.columns) == [
        "module",
        "label",
        "provisional",
        "ppv",
        "sensitivity",
        "papers",
        "fields",
        "date",
        "note",
    ]
    assert len(table) == len(snapshot().modules) == 30
    power = table[table["module"] == "metacheck::power"].iloc[0]
    assert (power["label"], power["ppv"], power["sensitivity"], power["papers"]) == (
        "validated",
        0.906,
        0.902,
        128,
    )
    assert power["fields"] == "psychology"
    assert power["note"] == PROVISIONAL_NOTE
    assert table["label"].value_counts().to_dict() == {"experimental": 25, "validated": 5}


def test_status_table_takes_refs(lab) -> None:
    table = status_table(["lab::apa", "stat_p_exact"], registry=_registry({}))
    assert table["module"].tolist() == ["lab::apa", "metacheck::stat_p_exact"]
    assert table["label"].tolist() == ["unvalidated", "experimental"]
    assert table["ppv"].isna().all()
