"""ModuleOutput.provenance and the @module metadata additions."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest

import pytacheck as pc
from pytacheck._version import UPSTREAM, __version__
from pytacheck.module import ModuleOutput, ModuleSpec, module, module_find, module_run
from pytacheck.provenance import bind_args, json_safe
from tests.modsys.helpers import REV_A, mod_src

KEYS = [
    "id",
    "name",
    "pack",
    "version",
    "trust",
    "reviewed",
    "source",
    "sha256",
    "modified",
    "args",
    "requires",
]


def test_builtin_provenance(paper) -> None:
    out = module_run(paper, "marginal")
    prov = out.provenance
    assert list(prov) == KEYS
    spec = module_find("marginal")
    assert prov == {
        "id": "metacheck::marginal",
        "name": "marginal",
        "pack": "metacheck",
        "version": __version__,
        "trust": "builtin",
        "reviewed": None,
        "source": {
            "builtin": f"pytacheck {__version__}",
            "upstream": f"{UPSTREAM['version']}@{UPSTREAM['commit'][:10]}",
        },
        "sha256": hashlib.sha256(Path(spec.path).read_bytes()).hexdigest(),
        "modified": False,
        "args": {},
        "requires": [],
    }


def test_provenance_is_not_an_element(paper) -> None:
    out = module_run(paper, "marginal")
    assert "provenance" not in out.keys()  # noqa: SIM118 - keys() is under test
    assert "provenance" not in out.results()
    assert "provenance" not in repr(out)
    twin = ModuleOutput(**{f: getattr(out, f) for f in (*out._FIELDS, "extras")})
    assert twin.provenance is None
    assert twin == out  # provenance is excluded from equality
    with pytest.raises(KeyError):
        out["provenance"]


def test_store_pack_provenance(ms, paper) -> None:
    ms.install(
        "psych",
        {"apa_df": mod_src("apa_df", args="strict=False", body='return {"summary_text": "ok"}')},
        reviewed="2026-09-01",
        version="1.2.0",
    )
    out = module_run(paper, "psych::apa_df", strict=True)
    prov = out.provenance
    path = Path(module_find("psych::apa_df").path)
    assert prov["id"] == "psych::apa_df" and prov["name"] == "apa_df" and prov["pack"] == "psych"
    assert prov["version"] == "1.2.0"
    assert prov["trust"] == "store" and prov["reviewed"] == "2026-09-01"
    assert prov["source"] == {"github": "someone/psych", "rev": REV_A}
    assert prov["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert prov["modified"] is False
    assert prov["args"] == {"strict": True}
    assert prov["requires"] == []


def test_unlisted_path_and_local_trust(ms, paper) -> None:
    ms.install("gitpack", {"g": mod_src("g")}, store=None)
    assert module_run(paper, "g").provenance["trust"] == "unlisted"
    folder = ms.pack(ms.root / "dev", "dev", {"d": mod_src("d")})
    ms.pin("dev", {"path": str(folder)})
    prov = module_run(paper, "d").provenance
    assert (prov["trust"], prov["pack"], prov["source"]) == (
        "local",
        "dev",
        {"path": str(folder.resolve())},
    )
    (ms.work / "loose.py").write_text(mod_src("loose"))
    prov = module_run(paper, "loose").provenance
    assert (prov["id"], prov["pack"], prov["trust"]) == ("loose", None, "local")
    assert prov["source"] == {"path": "loose.py"}


def test_effective_args_are_json_safe(paper) -> None:
    @module(title="Args", description="d")
    def args_mod(paper, n=1, when=None, items=(1, 2), **extra):
        return {}

    prov = module_run(
        paper, args_mod, when=Path("x"), flag=np.float64(1.5), weird=float("nan")
    ).provenance
    assert prov["args"] == {
        "n": 1,
        "when": repr(Path("x")),
        "items": [1, 2],
        "flag": 1.5,
        "weird": "nan",
    }
    assert prov["trust"] == "local" and prov["source"] == {"path": __file__}


def test_bind_args_defaults_and_failures() -> None:
    def f(paper, a=1, *, b=2):
        return None

    assert bind_args(f, "p", {"b": 3}) == {"a": 1, "b": 3}
    assert bind_args(f, "p", {"zzz": 1}) is None
    assert json_safe({"a": (1, {"b": {1, 2}})}) == {"a": [1, {"b": repr({1, 2})}]}


def test_keywords_migrate_to_requires() -> None:
    @module(title="LLM", description="d", keywords=["results", "llm", "network"], requires=["llm"])
    def both(paper):
        return {}

    spec = both.__pytacheck_module__
    assert spec.keywords == ("results",)
    assert spec.requires == ("llm", "network")
    assert spec.section == "results"

    @module(
        title="Net", description="d", keywords=["network"], validation={"tp": 9, "fp": 1, "fn": 3}
    )
    def net(paper):
        return {}

    spec = net.__pytacheck_module__
    assert spec.keywords == () and spec.section == "general" and spec.requires == ("network",)
    assert spec.validation == {"tp": 9, "fp": 1, "fn": 3}
    from pytacheck.packs.manifest import validation_metrics

    assert validation_metrics(spec.validation) == {
        "tp": 9,
        "fp": 1,
        "fn": 3,
        "ppv": 0.9,
        "sensitivity": 0.75,
    }


def test_modulespec_new_fields_are_defaulted() -> None:
    spec = ModuleSpec(name="x", func=print, title="X")
    assert (spec.pack, spec.requires, spec.validation) == (None, (), None)
    assert spec["pack"] is None
    assert module_find("marginal").pack == "metacheck"
    assert pc.module_info("stat_check").pack == "metacheck"
