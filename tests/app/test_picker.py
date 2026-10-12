"""The "Choose the checks" part of the paper page: the checks offered, presets, and runs."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import metacheck as pc
from metacheck.app import checks, run, ui

#: the checks the bibr modules added (#94, #95, #98); fast and offline, so ticked at first
BIBR_CHECKS = ("figure_table_refs", "author_check", "license_check")


@pytest.fixture(autouse=True)
def fresh_presets() -> Iterator[None]:
    """The installed presets are read once per app: read them again for each test."""
    checks._paper_presets.cache_clear()
    yield
    checks._paper_presets.cache_clear()


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A config file with two presets: one the page can run, one with a slow check."""
    path = tmp_path / "config.json"
    body = {
        "presets": {
            "tue": {
                "description": "TU/e checks",
                "modules": ["author_check", "license_check", "data_check"],
                "args": {"author_check": {"x": 1}},
            },
            "slow": {"description": "has code_check", "modules": ["stat_check", "code_check"]},
            "web": {"modules": ["marginal", "ref_pubpeer"]},
        }
    }
    path.write_text(json.dumps(body), encoding="utf-8")
    monkeypatch.setenv("PYTACHECK_CONFIG", str(path))
    return path


def _names(hosted: bool = False) -> list[str]:
    return [name for _, name in checks.choices(hosted)]


def test_the_choices_hold_the_fast_and_online_checks_but_never_a_slow_one() -> None:
    for hosted in (False, True):
        names = _names(hosted)
        assert names[-1] == "ref_summary"
        assert set(checks.selected_checks(online=True)) <= set(names)
        assert set(checks.NOT_SHOWN) <= set(names)
        assert not set(checks.NEVER) & set(names)
        assert not set(checks.DATA) & set(names)  # the data check keeps its own box
    assert set(BIBR_CHECKS) <= set(checks.selected_checks())


def test_what_the_browser_sends_is_checked() -> None:
    sent = ["code_check", "metacheck::stat_check", "ref_summary", "marginal", "../evil.py"]
    assert checks.chosen_checks(sent) == ["marginal", "stat_check", "ref_summary"]
    assert checks.chosen_checks(sent, hosted=True) == ["marginal", "stat_check", "ref_summary"]
    # a pack's module is offered on this computer only, and only when a preset uses it
    assert checks.chosen_checks(["tue::x"], extra=["tue::x"]) == ["tue::x"]
    assert checks.chosen_checks(["tue::x"], hosted=True, extra=["tue::x"]) == []
    assert checks.chosen_checks(["tue::x"]) == []
    assert checks.chosen_checks(None) == checks.selected_checks()


def test_only_the_ticked_checks_run(tmp_path: Path) -> None:
    analysis = run.check_paper(
        pc.demofile("json"), workdir=tmp_path, checks=["license_check", "author_check"]
    )
    assert [r.module for r in analysis.rows] == ["author_check", "license_check"]
    assert analysis.rows[0].result.startswith("Yellow: ")


def test_a_shared_server_drops_a_slow_check_it_was_sent(tmp_path: Path) -> None:
    started = run.begin(
        pc.demofile("json"), workdir=tmp_path, checks=["code_check", "marginal"], hosted=True
    )
    assert [r.module for r in started.analysis.rows] == ["marginal"]


def test_nothing_ticked_says_so(tmp_path: Path) -> None:
    with pytest.raises(run.UserError, match="No check is ticked"):
        run.begin(pc.demofile("json"), workdir=tmp_path, checks=[])


def test_installed_presets_are_offered_when_the_page_can_run_them(config: Path) -> None:
    offered = {ref: label for label, ref in checks.paper_presets()}
    assert offered[checks.APP_DEFAULT] == checks.APP_DEFAULT_LABEL
    assert offered["tue"] == "tue: TU/e checks"
    assert "web" in offered
    assert "slow" not in offered  # code_check is never run by the page
    assert "datapackage::default" not in offered  # the other page's
    assert not any(ref.startswith("metacheck::") for ref in offered)  # all have slow checks
    assert checks.preset_checks("tue") == (["author_check", "license_check"], True)
    assert checks.preset_checks("web") == (["marginal", "ref_pubpeer"], False)
    assert checks.preset_args("tue") == {"author_check": {"x": 1}}
    assert checks.preset_checks(checks.APP_DEFAULT, online=True) == (
        checks.selected_checks(online=True),
        True,
    )


def _handlers(**kwargs: Any) -> dict[str, Any]:
    blocks = ui.build_app(**kwargs)
    return {fn.name: fn.fn for fn in blocks.fns.values()}


def test_a_preset_ticks_its_checks_and_boxes(config: Path) -> None:
    fns = _handlers()
    assert fns["on_preset"]("tue", False) == (["author_check", "license_check"], True, False)
    # a preset with an online check ticks the online box
    assert fns["on_preset"]("web", False) == (["marginal", "ref_pubpeer"], False, True)
    assert fns["on_preset"](checks.APP_DEFAULT, False) == (checks.selected_checks(), True, False)


def test_the_online_box_ticks_and_unticks_the_online_checks() -> None:
    fns = _handlers()
    first = checks.selected_checks()
    assert fns["on_online"](True, first) == checks.selected_checks(online=True)
    assert fns["on_online"](False, checks.selected_checks(online=True)) == first
    assert fns["on_online"](True, ["marginal"]) == ["marginal", *checks.ONLINE]


def _component(blocks: Any, label: str) -> dict[str, Any]:
    parts = blocks.get_config_file()["components"]
    return next(c for c in parts if c["props"].get("label") == label)


def test_the_page_starts_with_the_apps_checks_ticked(config: Path) -> None:
    blocks = ui.build_app()
    picker = _component(blocks, "Checks to run")
    assert picker["props"]["value"] == checks.selected_checks()
    preset = _component(blocks, "Start from")
    assert preset["props"]["visible"] is True
    assert [ref for _, ref in preset["props"]["choices"]] == [checks.APP_DEFAULT, "tue", "web"]


def test_the_preset_choice_is_hidden_without_installed_presets() -> None:
    preset = _component(ui.build_app(), "Start from")
    assert preset["props"]["visible"] is False


def test_the_demo_button_runs_the_ticked_checks(config: Path) -> None:
    request = SimpleNamespace(session_hash="p1")
    page = _handlers()["on_demo"](
        False, False, "grobid", "", False, ["author_check"], "web", request, lambda *_a, **_k: None
    )
    assert "Ran 1 check on" in page[1]
    assert page[2].data["Check"].tolist() == ["Author Information"]


# -- metacheck-app --preset ------------------------------------------------------------------


def _started(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str | None, str | None]]:
    from metacheck.app import launch

    started: list[tuple[str | None, str | None]] = []
    monkeypatch.setattr(launch.saved, "find_running", lambda: None)
    monkeypatch.setattr(
        launch,
        "_serve",
        lambda _port, _browser, page=None, preset=None: started.append((page, preset)) or 0,
    )
    return started


def test_a_preset_opens_the_page_that_offers_it(
    config: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from metacheck.app.launch import main, preset_page

    assert preset_page("tue") == "paper"
    assert preset_page("datapackage::default") == "package"
    assert preset_page("slow") is None  # the page never runs code_check
    assert preset_page(checks.APP_DEFAULT) is None
    started = _started(monkeypatch)
    assert main(["--no-browser", "--preset", "tue"]) == 0
    assert main(["--no-browser", "--preset", "datapackage::default"]) == 0
    # the steward installer always asks for the package page: the preset's page wins
    assert main(["--no-browser", "--page", "package", "--preset", "tue"]) == 0
    assert started == [
        ("paper", "tue"),
        ("package", "datapackage::default"),
        ("paper", "tue"),
    ]
    assert "is for the paper page, so the app opens there" in capsys.readouterr().out


def test_an_unknown_preset_is_named_with_the_ones_there_are(
    config: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from metacheck.app.launch import main

    started = _started(monkeypatch)
    assert main(["--no-browser", "--preset", "nope"]) == 2
    err = capsys.readouterr().err
    assert "no preset named 'nope'" in err and "tue, web, datapackage::default" in err
    assert main(["--hosted", "--preset", "tue"]) == 2
    assert "--preset only works in the local app" in capsys.readouterr().err
    assert started == []


def test_a_running_app_keeps_its_checks_and_says_so(
    config: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from metacheck.app import launch

    monkeypatch.setattr(launch.saved, "find_running", lambda: {"port": 5, "token": "t"})
    monkeypatch.setattr(launch, "_serve", lambda *_a, **_k: pytest.fail("started a second app"))
    assert launch.main(["--no-browser", "--preset", "tue"]) == 0
    out = capsys.readouterr().out
    assert launch.RUNNING_PRESET.format(preset="tue") in out
    assert "http://127.0.0.1:5/?token=t" in out


def test_the_pages_start_from_the_preset_they_were_given(config: Path) -> None:
    blocks = ui.build_app(start_preset="tue")
    assert _component(blocks, "Start from")["props"]["value"] == "tue"
    assert _component(blocks, "Checks to run")["props"]["value"] == [
        "author_check",
        "license_check",
    ]
    web = ui.build_app(start_preset="web")
    online = _component(web, "Also run the online checks (slower: they look things up on the web)")
    assert online["props"]["value"] is True
    # a preset the page does not offer leaves the page as it was
    assert _component(ui.build_app(start_preset="slow"), "Start from")["props"]["value"] == ""


def test_the_package_page_starts_from_its_preset(
    config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = json.loads(config.read_text(encoding="utf-8"))
    body["presets"]["tue_package"] = {
        "description": "TU/e package checks",
        "extends": ["datapackage::default"],
    }
    config.write_text(json.dumps(body), encoding="utf-8")
    blocks = ui.build_app(start_preset="tue_package")
    package = [
        c
        for c in blocks.get_config_file()["components"]
        if c["props"].get("label") == "Checks" and c["type"] == "dropdown"
    ]
    assert [c["props"]["value"] for c in package] == ["tue_package"]
