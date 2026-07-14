from __future__ import annotations

import importlib
import sys
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest


def test_importing_cli_does_not_start_uvicorn() -> None:
    sys.modules.pop("pytacheck.cli", None)

    with patch("uvicorn.run") as run:
        module = importlib.import_module("pytacheck.cli")

    assert module is not None
    run.assert_not_called()
    sys.modules.pop("pytacheck.cli", None)


def test_cli_help_lists_serve_without_starting_uvicorn(capsys: pytest.CaptureFixture[str]) -> None:
    cli = importlib.import_module("pytacheck.cli")

    with patch("uvicorn.run") as run, pytest.raises(SystemExit) as exit_info:
        cli.main(["--help"])

    assert exit_info.value.code == 0
    assert "serve" in capsys.readouterr().out
    run.assert_not_called()


def test_cli_serve_wires_host_and_port_to_uvicorn() -> None:
    cli = importlib.import_module("pytacheck.cli")

    with patch("uvicorn.run") as run:
        cli.main(["serve", "--host", "0.0.0.0", "--port", "2005"])  # noqa: S104

    run.assert_called_once()
    args, kwargs = run.call_args
    target = args[0] if args else kwargs["app"]
    assert target == "pytacheck.api:create_app"
    assert kwargs.get("factory") is True
    assert kwargs["host"] == "0.0.0.0"  # noqa: S104
    assert kwargs["port"] == 2005


def test_pyproject_exposes_pytacheck_console_script() -> None:
    project_root = Path(__file__).resolve().parents[2]
    with (project_root / "pyproject.toml").open("rb") as pyproject_file:
        pyproject = tomllib.load(pyproject_file)

    assert pyproject["project"]["scripts"]["pytacheck"] == "pytacheck.cli:main"
