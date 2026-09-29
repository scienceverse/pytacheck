"""``metacheck-app``: start the local server, or reuse the one that is running."""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import os
import secrets
import signal
import socket
import sys
import tempfile
import threading
import time
import webbrowser
from collections.abc import Sequence
from pathlib import Path

from pytacheck.app import state as saved

MISSING_EXTRA = "The app needs the app extra: pip install 'pytacheck[app]'"
HOST = "127.0.0.1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="metacheck-app",
        description="Open the metacheck app in your browser. It runs on this computer only.",
    )
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser")
    parser.add_argument("--port", type=int, default=0, help="port to use (default: a free one)")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="check the demo paper without starting the app; exit 0 if it works",
    )
    parser.add_argument("--version", action="store_true", help="show the version and exit")
    return parser


def _self_test() -> int:
    import pytacheck as pc
    from pytacheck.app.run import check_paper

    start = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(prefix="metacheck-app-") as work:
            analysis = check_paper(pc.demofile("json"), workdir=Path(work))
    except Exception as exc:
        print(f"Self-test failed: {exc}", file=sys.stderr)
        return 1
    failed = [row.check for row in analysis.rows if row.result.startswith("Failed")]
    if not analysis.rows or failed or "<html" not in analysis.html.lower():
        print(f"Self-test failed: {', '.join(failed) or 'no report'}", file=sys.stderr)
        return 1
    print(f"Self-test passed: {len(analysis.rows)} checks in {time.perf_counter() - start:.1f} s")
    return 0


def _url(port: int, token: str) -> str:
    return f"http://{HOST}:{port}/?token={token}"


def _announce(url: str, open_browser: bool) -> None:
    print(f"metacheck is running at {url}", flush=True)
    print("Keep this window open while you use it. Press Ctrl+C here to stop it.", flush=True)
    if open_browser:
        webbrowser.open(url)


def _warm_up() -> None:
    """Run the demo paper once so the first click is fast."""
    import pytacheck as pc
    from pytacheck.app.run import check_paper

    # only a head start: the button reports real problems
    with (
        contextlib.suppress(Exception),
        tempfile.TemporaryDirectory(prefix="metacheck-app-") as work,
    ):
        check_paper(pc.demofile("json"), workdir=Path(work))


def _bind(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((HOST, port))
    except OSError:
        sock.close()
        raise
    sock.listen(128)
    return sock


def _serve(port: int, open_browser: bool) -> int:
    import uvicorn

    from pytacheck.app.server import create_app
    from pytacheck.config import verbose

    try:
        sock = _bind(port)
    except OSError as exc:
        print(f"Port {port} is not available ({exc.strerror or exc}). Try another --port.")
        return 1
    port = sock.getsockname()[1]
    token = secrets.token_urlsafe(32)
    verbose(False)
    server = uvicorn.Server(uvicorn.Config(create_app(port, token), log_level="warning"))
    saved.write_state(port, token)

    def when_up() -> None:
        while not server.started and not server.should_exit:
            time.sleep(0.05)
        if server.started:
            _announce(_url(port, token), open_browser)
            _warm_up()

    threading.Thread(target=when_up, daemon=True).start()
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        server.run(sockets=[sock])
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        sock.close()
        saved.remove_state(port)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ns = build_parser().parse_args(argv)
    if ns.version:
        from pytacheck._version import __version__

        print(f"metacheck-app {__version__}")
        return 0
    if importlib.util.find_spec("gradio") is None:
        print(MISSING_EXTRA, file=sys.stderr)
        return 2
    os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"  # before gradio is imported
    if ns.self_test:
        return _self_test()
    if not 0 <= ns.port <= 65535:
        print("The port must be a number from 0 to 65535.", file=sys.stderr)
        return 2
    running = saved.find_running()
    if running is not None and ns.port in (0, running["port"]):
        _announce(_url(running["port"], running["token"]), not ns.no_browser)
        return 0
    return _serve(ns.port, not ns.no_browser)
