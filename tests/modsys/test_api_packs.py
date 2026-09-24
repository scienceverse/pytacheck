"""The REST API with packs: qualified modules, presets, no local code, concurrency cap."""

from __future__ import annotations

import asyncio
import time

import pytest

pytest.importorskip("fastapi")
import httpx
from fastapi.testclient import TestClient

import pytacheck as pc
from pytacheck.api.app import available_modules, create_app
from tests.modsys import api_probe
from tests.modsys.helpers import mod_src


@pytest.fixture
def demo_json() -> bytes:
    return pc.demofile("json").read_bytes()


def upload(data: bytes) -> dict:
    return {"file": ("paper.json", data, "application/json")}


@pytest.fixture
def packs(ms):
    """An installed pack (allowed on the server) and a path pack (local code: never)."""
    policy = mod_src(
        "policy",
        header="from pytacheck.module import use_setting",
        body='return {"summary_text": f"allow_local={use_setting(\'allow_local\', True)}"}',
        args="",
    )
    ms.install(
        "inst",
        {"policy": policy, "hello": mod_src("hello", "hi")},
        presets={"both": {"modules": ["policy", "hello", "marginal"]}},
    )
    local = ms.pack(ms.root / "local", "localpack", {"secret": mod_src("secret", "local code")})
    ms.pin("localpack", {"path": str(local)})
    (ms.work / "cwd_mod.py").write_text(mod_src("cwd_mod", "cwd code"))
    return ms


def test_modules_lists_builtins_bare_and_pack_modules_qualified(packs) -> None:
    mods = available_modules()
    assert "marginal" in mods and "metacheck::marginal" not in mods
    assert {"inst::policy", "inst::hello"} <= set(mods)
    assert not any("secret" in m or "cwd_mod" in m for m in mods), "no local code"
    body = TestClient(create_app()).get("/paper/modules").json()
    assert body["modules"] == mods and body["count"] == [len(mods)]


def test_qualified_module_runs_and_local_code_does_not(packs, demo_json) -> None:
    client = TestClient(create_app())
    r = client.post("/paper/module", files=upload(demo_json), data={"name": "inst::policy"})
    assert r.status_code == 200
    assert r.json()["summary_text"] == ["allow_local=False"]
    for name in ("localpack::secret", "secret", "cwd_mod", "./cwd_mod.py"):
        r = client.post("/paper/module", files=upload(demo_json), data={"name": name})
        assert r.status_code == 400, name
    r = client.post("/paper/check", files=upload(demo_json), data={"modules": "cwd_mod"})
    assert r.status_code == 400 and "Invalid modules: cwd_mod" in r.json()["error"]


def test_check_with_a_preset(packs, demo_json) -> None:
    client = TestClient(create_app())
    body = client.post(
        "/paper/check", files=upload(demo_json), data={"preset": "inst::both", "report": "false"}
    ).json()
    assert body["modules_run"] == ["inst::policy", "inst::hello", "marginal"]
    assert body["results"]["inst::policy"]["summary_text"] == ["allow_local=False"]
    assert body["results"]["inst::hello"]["summary_text"] == ["hi"]
    bad = client.post("/paper/check", files=upload(demo_json), data={"preset": "nope"})
    assert bad.status_code == 400 and bad.json()["error"].startswith("Invalid preset")
    # explicit modules win over a preset, as before
    body = client.post(
        "/paper/check",
        files=upload(demo_json),
        data={"modules": "marginal", "preset": "inst::both", "report": "false"},
    ).json()
    assert body["modules_run"] == ["marginal"]


def test_check_defaults(packs, demo_json, monkeypatch) -> None:
    from pytacheck.api.app import _check_selection

    # plumber: every available module (not run here: some built-ins use the network)
    assert [m for m, _ in _check_selection({})] == available_modules()
    client = TestClient(create_app())
    monkeypatch.setenv("PYTACHECK_PRESET", "inst::both")  # the server's configured preset
    body = client.post("/paper/check", files=upload(demo_json), data={"report": "false"}).json()
    assert body["modules_run"] == ["inst::policy", "inst::hello", "marginal"]


def test_a_config_preset_naming_local_code_does_not_run_it(packs, demo_json, monkeypatch) -> None:
    packs.config(
        {
            "packs": {"inst": _pin(packs), "localpack": {"path": str(packs.root / "local")}},
            "presets": {"sneaky": {"modules": ["localpack::secret", "marginal"]}},
        }
    )
    client = TestClient(create_app())
    body = client.post(
        "/paper/check", files=upload(demo_json), data={"preset": "sneaky", "report": "false"}
    ).json()
    if "error" in body:  # refused up front
        assert "localpack" in body["error"]
    else:
        assert body["results"]["localpack::secret"]["traffic_light"] == ["fail"]


def _pin(ms) -> dict:
    import json

    return json.loads(ms.config_file.read_text())["packs"]["inst"]


def _slow_pack(ms) -> None:
    slow = mod_src(
        "slow",
        header="from tests.modsys import api_probe",
        body='api_probe.slow(0.25); return {"summary_text": "done"}',
        args="",
    )
    ms.install("slowpack", {"slow": slow})


async def _checks(app, n: int, *, health: bool = False) -> list[float]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        start = time.monotonic()
        done: list[float] = []

        async def check() -> None:
            data = {"modules": "slowpack::slow", "report": "false"}
            files = upload(pc.demofile("json").read_bytes())
            r = await client.post("/paper/check", files=files, data=data)
            assert r.status_code == 200, r.text
            done.append(time.monotonic() - start)

        async def ping() -> float:
            await asyncio.sleep(0.05)
            r = await client.get("/health")
            assert r.status_code == 200
            return time.monotonic() - start

        tasks = [check() for _ in range(n)]
        if health:
            results = await asyncio.gather(ping(), *tasks)
            return [results[0], *sorted(done)]
        await asyncio.gather(*tasks)
        return sorted(done)


def test_concurrency_is_capped(ms, monkeypatch) -> None:
    _slow_pack(ms)
    monkeypatch.setenv("PYTACHECK_API_MAX_CHECKS", "1")
    app = create_app()
    assert app.state.max_checks == 1
    api_probe.reset()
    timings = asyncio.run(_checks(app, 3, health=True))
    assert api_probe.state == {"running": 0, "max": 1, "calls": 3}
    ping, *checks = timings
    assert ping < checks[0], "the event loop stays free while checks run"
    monkeypatch.setenv("PYTACHECK_API_MAX_CHECKS", "3")
    api_probe.reset()
    asyncio.run(_checks(create_app(), 3))
    assert api_probe.state["max"] >= 2


def test_default_cap_is_the_cpu_count(monkeypatch) -> None:
    import os

    monkeypatch.delenv("PYTACHECK_API_MAX_CHECKS", raising=False)
    assert create_app().state.max_checks == (os.cpu_count() or 1)
    monkeypatch.setenv("PYTACHECK_API_MAX_CHECKS", "not a number")
    assert create_app().state.max_checks == (os.cpu_count() or 1)


def test_requests_are_memoised_within_a_session(ms, demo_json, monkeypatch) -> None:
    counter = mod_src(
        "counted",
        header="from tests.modsys import api_probe",
        body='api_probe.slow(0); return {"summary_text": "x"}',
        args="",
    )
    ms.install("cnt", {"counted": counter}, presets={"twice": {"modules": ["counted"]}})
    api_probe.reset()
    client = TestClient(create_app())
    for _ in range(2):
        client.post("/paper/check", files=upload(demo_json), data={"modules": "cnt::counted"})
    assert api_probe.state["calls"] == 2, "each request has its own session (no cross-request memo)"
