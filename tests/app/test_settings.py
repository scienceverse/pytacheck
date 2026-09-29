"""The fixed Gradio settings of ARCHITECTURE.md section 3.7 that apply to a local app."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import gradio as gr
import pytest

from pytacheck.app import server, ui


@pytest.fixture(scope="module")
def blocks() -> gr.Blocks:
    return ui.build_app()


def test_analytics_off_in_blocks(blocks: gr.Blocks) -> None:
    assert blocks.analytics_enabled is False


def test_analytics_env_var_is_set_before_gradio_is_imported() -> None:
    code = (
        "import sys, os\n"
        "os.environ.pop('GRADIO_ANALYTICS_ENABLED', None)\n"
        "from pytacheck.app import main\n"
        "assert 'gradio' not in sys.modules\n"
        "main(['--self-test'])\n"
        "print(os.environ['GRADIO_ANALYTICS_ENABLED'])\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip().splitlines()[-1] == "False"


def test_importing_pytacheck_never_imports_gradio() -> None:
    code = "import sys, pytacheck, pytacheck.app; print('gradio' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


def test_every_event_is_private(blocks: gr.Blocks) -> None:
    assert blocks.fns
    assert {fn.api_visibility for fn in blocks.fns.values()} == {"private"}


def test_queue_closes_the_api_and_runs_one_check_at_a_time(blocks: gr.Blocks) -> None:
    assert blocks.api_open is False
    assert blocks._queue.default_concurrency_limit == 1
    assert ui.QUEUE_KWARGS == {"api_open": False, "default_concurrency_limit": 1}


def test_run_history_is_off_and_files_are_cleaned(blocks: gr.Blocks) -> None:
    assert ui.MOUNT_KWARGS["run_history"] is False
    assert blocks.delete_cache == (300, 1800)


def test_mount_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def spy(app: Any, blocks: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        return app

    monkeypatch.setattr(gr, "mount_gradio_app", spy)
    server.create_app(1234, "t")
    assert seen["footer_links"] == []
    assert seen["ssr_mode"] is False
    assert seen["enable_monitoring"] is False
    assert seen["mcp_server"] is False
    assert seen["max_file_size"] == "50mb"
    assert seen["run_history"] is False
    assert seen["server_name"] == "127.0.0.1"
    assert seen["server_port"] == 1234


def test_the_app_never_shares() -> None:
    root = Path(server.__file__).parent
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "share=True" not in text, path
        assert ".launch(" not in text, path


def test_report_sandbox_is_narrow() -> None:
    granted = ui.REPORT_SANDBOX.split()
    assert "allow-scripts" in granted
    for wide in ("allow-same-origin", "allow-top-navigation", "allow-forms", "allow-modals"):
        assert wide not in granted


def test_text_of_the_page(blocks: gr.Blocks) -> None:
    labels = {getattr(b, "label", None) for b in blocks.blocks.values()}
    assert "Your paper (PDF, GROBID XML or bibr JSON)" in labels
    assert "Also run the online checks (slower: they look things up on the web)" in labels
    values = {str(getattr(b, "value", None)) for b in blocks.blocks.values()}
    assert "Check my paper" in values
    assert "Try the demo paper" in values
    assert ui.PRIVACY.startswith("PDFs are turned into text by the public GROBID server")
    assert os.environ.get("GRADIO_ANALYTICS_ENABLED") in (None, "False")
