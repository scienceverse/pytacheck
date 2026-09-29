"""``metacheck-app``: start the local server, or reuse the one that is running."""

from __future__ import annotations

import argparse
import contextlib
import html
import importlib.util
import os
import secrets
import shutil
import signal
import socket
import sys
import tempfile
import threading
import time
import webbrowser
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pytacheck.app import hosted as hosting
from pytacheck.app import state as saved

MISSING_EXTRA = "The app needs the app extra: pip install 'metacheck[app]>=0.4.0a1'"
HOST = "127.0.0.1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="metacheck-app",
        description="Open the metacheck app in your browser. It runs on this computer only.",
    )
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser")
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="port to use (default: a free one; with --hosted: $PORT, else 7860)",
    )
    parser.add_argument(
        "--hosted",
        action="store_true",
        help="serve other people over https behind a proxy (needs METACHECK_APP_TOKENS)",
    )
    parser.add_argument("--host", default=None, help="address to listen on (--hosted only)")
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


def _launcher_page(url: str) -> Path:
    """A page that only sends the browser on to ``url``, readable by this user only.

    Handing the browser the token link directly would put the token in the process list,
    where other users of this computer can read it.
    """
    page = saved.state_path().with_name("open.html")
    page.parent.mkdir(parents=True, exist_ok=True)
    quoted = html.escape(url, quote=True)
    body = (
        f'<!doctype html><meta charset=utf-8><meta http-equiv=refresh content="0;url={quoted}">'
        f'<title>metacheck</title><p>Opening metacheck. <a href="{quoted}">Continue</a></p>'
    )
    fd = os.open(page, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(body)
    return page


def _open_browser(url: str) -> None:
    try:
        webbrowser.open(_launcher_page(url).as_uri())
    except OSError:
        webbrowser.open(url)


def _announce(url: str, open_browser: bool) -> None:
    print(f"metacheck is running at {url}", flush=True)
    print("Keep this window open while you use it. Press Ctrl+C here to stop it.", flush=True)
    if open_browser:
        _open_browser(url)


def private_gradio_dir() -> Path:
    """A folder only this user can read for Gradio's copies of uploads and reports.

    Set before gradio is imported: by default Gradio uses a shared ``/tmp/gradio``.
    """
    folder = Path(tempfile.mkdtemp(prefix="metacheck-app-gradio-"))
    os.environ["GRADIO_TEMP_DIR"] = str(folder)
    return folder


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


def _bind(port: int, host: str = HOST) -> socket.socket:
    sock = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
    except OSError:
        sock.close()
        raise
    sock.listen(128)
    return sock


def uvicorn_config(app: Any, *, hosted: bool) -> Any:
    """The server settings. There is no access log: its lines hold the query string, which
    carries the token of a first visit."""
    import uvicorn

    extra: dict[str, Any] = {}
    if hosted:
        # The platform's proxy is the only peer and sets the scheme of the request.
        extra["forwarded_allow_ips"] = "*"
    return uvicorn.Config(app, log_level="warning", access_log=False, **extra)


def _serve(
    port: int, open_browser: bool, config: hosting.HostedConfig | None = None, host: str = HOST
) -> int:
    import uvicorn

    try:
        sock = _bind(port, host)
    except OSError as exc:
        print(f"Port {port} is not available ({exc.strerror or exc}). Try another --port.")
        return 1
    gradio_dir = private_gradio_dir()
    from pytacheck.app.server import create_app, create_hosted_app
    from pytacheck.config import verbose

    port = sock.getsockname()[1]
    token = secrets.token_urlsafe(32)
    verbose(False)
    if config is None:
        app = create_app(port, token)
        saved.write_state(port, token)
    else:
        app = create_hosted_app(port, config)
    server = uvicorn.Server(uvicorn_config(app, hosted=config is not None))

    def when_up() -> None:
        while not server.started and not server.should_exit:
            time.sleep(0.05)
        if server.started:
            if config is None:
                _announce(_url(port, token), open_browser)
            elif config.proxy_auth:
                print(
                    f"metacheck is serving on port {port}; the proxy in front signs people in",
                    flush=True,
                )
            else:  # the log must not hold a token
                print(
                    f"metacheck is serving {len(config.tokens)} access token(s) on port {port}",
                    flush=True,
                )
            _warm_up()

    threading.Thread(target=when_up, daemon=True).start()
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        if hasattr(signal, "SIGHUP"):  # closing the terminal window
            signal.signal(signal.SIGHUP, lambda *_: setattr(server, "should_exit", True))
    try:
        server.run(sockets=[sock])
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        sock.close()
        if config is None:
            saved.remove_state(port)
            with contextlib.suppress(OSError):
                saved.state_path().with_name("open.html").unlink()
        shutil.rmtree(gradio_dir, ignore_errors=True)
        if os.environ.get("GRADIO_TEMP_DIR") == str(gradio_dir):
            del os.environ["GRADIO_TEMP_DIR"]
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
    if ns.host is not None and not ns.hosted:
        print("--host only works together with --hosted.", file=sys.stderr)
        return 2
    port = ns.port if ns.port is not None else (hosting.default_port() if ns.hosted else 0)
    if not 0 <= port <= 65535:
        print("The port must be a number from 0 to 65535.", file=sys.stderr)
        return 2
    if ns.hosted:
        try:
            config = hosting.HostedConfig.from_env()
        except hosting.HostedError as exc:
            print(exc, file=sys.stderr)
            return 2
        return _serve(port, False, config, ns.host or HOST)
    running = saved.find_running()
    if running is not None and port in (0, running["port"]):
        _announce(_url(running["port"], running["token"]), not ns.no_browser)
        return 0
    return _serve(port, not ns.no_browser)
