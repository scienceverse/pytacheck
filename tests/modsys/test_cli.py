"""CLI smoke tests: every subcommand through ``main(argv)`` (no network, hermetic config)."""

from __future__ import annotations

import json

import pytest
import respx

import metacheck as pc
from metacheck.cli import main
from metacheck.packs import ui
from tests.modsys.helpers import mod_src
from tests.modsys.storekit import STORE_URL, FakeStore


@pytest.fixture
def demo_json() -> str:
    return str(pc.demofile("json"))


@pytest.fixture
def lab(ms):
    mods = {
        "apa": mod_src(
            "apa", args="strict=False", body='return {"summary_text": f"strict={strict}"}'
        ),
        "seeded": mod_src("seeded", args="seed=0", body='return {"summary_text": f"seed={seed}"}'),
        "online": mod_src("online", requires=("network",)),
    }
    folder = ms.pack(
        ms.root / "lab",
        "lab",
        mods,
        presets={"default": {"description": "Lab checks", "modules": ["apa", "seeded", "online"],
                             "args": {"seeded": {"seed": 1}}}},
    )  # fmt: skip
    ms.pin("lab", {"path": str(folder)})
    return folder


@pytest.fixture
def store(ms, monkeypatch):
    monkeypatch.setenv("PYTACHECK_STORE_URL", STORE_URL)
    with respx.mock(assert_all_called=False) as router:
        yield FakeStore(router, ms)


def run_json(capsys, argv: list[str]) -> list[dict]:
    capsys.readouterr()
    assert main(argv) == 0
    return json.loads(capsys.readouterr().out)


def test_modules(lab, capsys) -> None:
    assert main(["modules"]) == 0
    out = capsys.readouterr().out
    assert "* marginal:" in out and "lab::" not in out
    assert main(["modules", "--all"]) == 0
    out = capsys.readouterr().out
    assert "* lab::apa: The apa module" in out and "* marginal:" in out
    assert main(["modules", "--pack", "lab"]) == 0
    out = capsys.readouterr().out
    assert "lab::seeded" in out and "marginal" not in out
    assert main(["modules", "lab::apa"]) == 0
    out = capsys.readouterr().out
    assert 'module_run(paper, "lab::apa", strict = False)' in out and "Pack: lab" in out
    assert main(["modules", "nope"]) == 1
    assert "no modules that matched nope" in capsys.readouterr().err


def test_presets(lab, capsys) -> None:
    assert main(["presets"]) == 0
    out = capsys.readouterr().out
    assert "metacheck::default" in out and "lab::default" in out
    assert main(["presets", "lab"]) == 0
    out = capsys.readouterr().out
    assert "lab::default: Lab checks" in out and '2. lab::seeded  {"seed":1}' in out
    assert main(["presets", "show", "metacheck::repository", "--as-r"]) == 0
    assert 'modules = c("repo_check", "code_check"' in capsys.readouterr().out
    assert main(["presets", "nope"]) == 1


def test_run_selection_and_args(lab, capsys, demo_json) -> None:
    out = run_json(capsys, ["run", demo_json, "-m", "marginal", "--json"])
    assert [o["module"] for o in out] == ["marginal"]  # unchanged JSON shape
    assert set(out[0]) == {"module", "title", "traffic_light", "summary_text", "table"}
    out = run_json(capsys, ["run", demo_json, "--preset", "lab", "--json"])
    assert [o["summary_text"] for o in out[:2]] == ["strict=False", "seed=1"]
    out = run_json(
        capsys,
        [
            "run",
            demo_json,
            "--preset",
            "lab",
            "-a",
            "lab::apa.strict=true",
            "-a",
            "seed=7",
            "--json",
        ],
    )
    assert [o["summary_text"] for o in out[:2]] == ["strict=True", "seed=7"]
    out = run_json(
        capsys,
        ["run", demo_json, "--preset", "lab", "-a", "seed=7", "-a", "seeded.seed=9", "--json"],
    )
    assert out[1]["summary_text"] == "seed=9", "MODULE.KEY beats a bare KEY"
    with pytest.raises(SystemExit, match="none of the selected modules accepts 'nope'"):
        main(["run", demo_json, "-m", "marginal", "-a", "nope=1"])
    out = run_json(capsys, ["run", demo_json, "--preset", "lab", "--offline", "--json"])
    assert [o["module"] for o in out] == ["apa", "seeded"]


def test_run_uses_the_configured_preset_and_says_so(lab, ms, capsys, demo_json) -> None:
    ms.config({"preset": "lab", "packs": {"lab": {"path": str(lab)}}})
    assert main(["run", demo_json]) == 0
    captured = capsys.readouterr()
    assert f"Preset: lab (from {ms.config_file})" in captured.err
    assert "Apa" in captured.out and "[lab]" in captured.out  # pack shown next to the title


def test_run_record_and_rerun(lab, ms, capsys, demo_json) -> None:
    record = ms.root / "run.json"
    assert main(["run", demo_json, "--preset", "lab", "--offline", "--record", str(record)]) == 0
    data = json.loads(record.read_text())
    assert data["preset"] == "lab" and data["dropped"] == ["lab::online"]
    out = run_json(capsys, ["rerun", str(record), demo_json, "--json"])
    assert [o["summary_text"] for o in out] == ["strict=False", "seed=1"]
    (lab / "apa.py").write_text(
        mod_src("apa", args="strict=False", body='return {"summary_text": "changed"}')
    )
    assert main(["rerun", str(record), demo_json]) == 1
    assert "allow-modified" in capsys.readouterr().err
    assert main(["rerun", str(record), demo_json, "--allow-modified"]) == 0


def test_report(lab, ms, capsys, demo_json) -> None:
    try:
        from metacheck.report import report as report_mod

        report_mod.report  # noqa: B018
    except (ImportError, AttributeError):
        assert main(["report", demo_json, "-m", "marginal"]) == 2
        return
    out = ms.work / "r.md"
    record = ms.work / "r.json"
    argv = [
        "report",
        demo_json,
        "-m",
        "marginal",
        "-f",
        "md",
        "-o",
        str(out),
        "--record",
        str(record),
    ]
    assert main(argv) == 0
    assert out.is_file()
    assert json.loads(record.read_text())["modules"][0]["id"] == "metacheck::marginal"


def test_pack_commands(store, ms, capsys) -> None:
    store.add("demo", {"hello": mod_src("hello", "hi")}, fields=["psychology"], title="Demo pack")
    assert main(["pack", "search", "demo", "--field", "psychology"]) == 0
    assert "demo" in capsys.readouterr().out
    assert main(["pack", "search", "nothing-matches"]) == 0
    assert "No packs found" in capsys.readouterr().out
    assert main(["pack", "show", "demo", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["installed"] is False
    assert main(["pack", "install", "demo"]) == 1  # no terminal to confirm
    assert "cancelled" in capsys.readouterr().err
    assert main(["pack", "install", "demo", "--yes"]) == 0
    assert main(["pack", "list"]) == 0
    assert "demo" in capsys.readouterr().out
    assert main(["pack", "show", "demo"]) == 0
    assert "trust: store" in capsys.readouterr().out
    assert main(["pack", "update", "--yes"]) == 0
    assert "demo: unchanged" in capsys.readouterr().out
    assert main(["pack", "install"]) == 0  # sync: nothing to do
    assert main(["pack", "remove", "demo"]) == 1  # asks first
    assert main(["pack", "remove", "demo", "--yes"]) == 0
    assert "demo" not in store.config()["packs"]


def test_store_commands(store, ms, capsys, tmp_path) -> None:
    store.add("demo", {"hello": mod_src("hello")})
    assert main(["store", "list"]) == 0
    assert STORE_URL in capsys.readouterr().out
    assert main(["store", "update"]) == 0
    assert "ok" in capsys.readouterr().out
    assert main(["store", "add", "lab", str(tmp_path)]) == 1  # asks first
    assert main(["store", "add", "lab", str(tmp_path), "--yes"]) == 0
    assert store.config()["stores"]["lab"] == str(tmp_path)
    assert main(["store", "update", "lab"]) == 1  # no index.json yet
    assert main(["store", "remove", "lab", "--yes"]) == 0
    assert "lab" not in store.config()["stores"]
    seed = ms.root / "seed"
    ms.pack(seed / "packs" / "tiny", "tiny", presets={"x": {"modules": ["marginal"]}})
    assert main(["store", "build", str(seed), "--check"]) == 0
    assert not (seed / "index.json").exists()
    assert main(["store", "build", str(seed)]) == 0
    assert json.loads((seed / "index.json").read_text())["packs"][0]["name"] == "tiny"


def test_store_add_and_remove_have_no_project_option(ms, tmp_path) -> None:
    for argv in (["add", "lab", str(tmp_path)], ["remove", "lab"]):
        with pytest.raises(SystemExit) as exc:
            main(["store", *argv, "--project", "--yes"])
        assert exc.value.code == 2


def test_init_hides_only_the_cached_index_warning(ms, monkeypatch, capsys) -> None:
    import warnings

    def indexes():
        warnings.warn(
            "The store 'lab' is unreachable (x); using its index cached at y", stacklevel=2
        )
        warnings.warn(
            "Ignoring the stores in the project config /p/pytacheck.json: x", stacklevel=2
        )
        return {}, {}

    answers = iter(["1", "2", "u"])
    monkeypatch.setattr("metacheck.packs.stores.store_indexes", indexes)
    monkeypatch.setattr(ui, "interactive", lambda: True)
    monkeypatch.setattr(ui, "ask", lambda *a, **k: next(answers))
    monkeypatch.setattr(ui, "confirm", lambda *a, **k: True)
    assert main(["init"]) == 0
    err = capsys.readouterr().err
    assert "Ignoring the stores in the project config" in err and "unreachable" not in err


def test_init_non_interactive(store, ms, capsys) -> None:
    store.add("fields", presets={"psychology": {"modules": ["marginal", "stat_check"]}})
    assert main(["init"]) == 1
    assert "needs --preset" in capsys.readouterr().err
    assert main(["init", "--preset", "fields::psychology", "--yes"]) == 0
    cfg = store.config()
    assert cfg["preset"] == "fields::psychology" and "fields" in cfg["packs"]
    assert "(2 modules)" in capsys.readouterr().out
    assert main(["init", "--preset", "fields::psychology", "--preset", "metacheck::repository",
                 "--yes"]) == 0  # fmt: skip
    cfg = store.config()
    assert cfg["preset"] == "mine"
    assert cfg["presets"]["mine"]["extends"] == ["fields::psychology", "metacheck::repository"]
    assert main(["init", "--yes"]) == 0
    assert store.config()["preset"] == "metacheck::default"


def test_init_degrades_when_the_store_is_unreachable(store, ms, capsys) -> None:
    store.router.routes["index"].respond(404)
    assert main(["init", "--preset", "fields::psychology", "--yes"]) == 1
    assert "unreachable" in capsys.readouterr().err
    assert not ms.config_file.exists()
    assert main(["init", "--preset", "metacheck::validated", "--yes"]) == 0  # built-ins still work


def test_init_interactive(store, ms, monkeypatch, capsys) -> None:
    store.add(
        "fields",
        fields=["psychology", "medicine"],
        presets={"psychology": {"description": "Psych", "modules": ["marginal"]},
                 "medicine": {"description": "Med", "modules": ["stat_check"]}},
    )  # fmt: skip
    answers = iter(["2", "4", "p"])  # field "psychology", 4th preset, project config
    monkeypatch.setattr(ui, "interactive", lambda: True)
    monkeypatch.setattr(ui, "ask", lambda *a, **k: next(answers))
    monkeypatch.setattr(ui, "confirm", lambda *a, **k: True)
    assert main(["init"]) == 0
    err = capsys.readouterr().err
    assert "4. fields::psychology" in err and "fields::medicine" not in err
    assert store.config()["preset"] == "fields::psychology"


def test_init_interactive_offline_offers_builtins(store, ms, monkeypatch, capsys) -> None:
    store.router.routes["index"].respond(404)
    answers = iter(["1", "2", "u"])
    monkeypatch.setattr(ui, "interactive", lambda: True)
    monkeypatch.setattr(ui, "ask", lambda *a, **k: next(answers))
    monkeypatch.setattr(ui, "confirm", lambda *a, **k: True)
    assert main(["init"]) == 0
    assert "Only the built-in presets" in capsys.readouterr().err
    assert store.config()["preset"] == "metacheck::repository"


def test_version(capsys) -> None:
    assert main(["version"]) == 0
    assert capsys.readouterr().out.startswith("pytacheck ")


def test_read_and_serve(ms, capsys, monkeypatch, demo_json) -> None:
    out = ms.work / "copy.json"
    assert main(["read", demo_json, "-o", str(out)]) == 0
    assert out.is_file() and pc.read(str(out)).paper_id == pc.demopaper().paper_id
    uvicorn = pytest.importorskip("uvicorn")
    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: calls.append((a, k)))
    assert main(["serve", "--port", "8123"]) == 0
    assert calls == [(("metacheck.api.app:create_app",), {"factory": True, "host": "127.0.0.1",
                                                          "port": 8123})]  # fmt: skip
