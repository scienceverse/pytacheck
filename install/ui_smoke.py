"""Click through an installed metacheck-app in a browser:
python install/ui_smoke.py APP [--port N] [--shots DIR] [--browser-path EXE]

Starts the app without a browser, opens its token link in Chromium (Playwright for
Python), runs the demo paper, then uploads the demo JSON and checks it. The page may
talk to 127.0.0.1 only. Needs `pip install playwright` and `playwright install chromium`.
"""

import argparse
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Page, Request, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

RUNNING = re.compile(r"metacheck is running at (http://127\.0\.0\.1:\d+/\?token=\S+)")
PAPER = (
    Path(__file__).resolve().parent.parent / "src/metacheck/resources/demos/to_err_is_human.json"
)
# A closed local proxy: a download from the server side fails loudly instead of passing unseen.
NO_OUTSIDE = {
    "HTTP_PROXY": "http://127.0.0.1:9",
    "HTTPS_PROXY": "http://127.0.0.1:9",
    "NO_PROXY": "127.0.0.1,localhost",
    "http_proxy": "http://127.0.0.1:9",
    "https_proxy": "http://127.0.0.1:9",
    "no_proxy": "127.0.0.1,localhost",
}
SLOW = 240_000  # ms: the first check on a fresh runner


def start_app(app: str, port: int, timeout: float) -> tuple[subprocess.Popen[str], str]:
    proc = subprocess.Popen(  # noqa: S603
        [app, "--no-browser", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1", **NO_OUTSIDE},
    )
    lines: list[str] = []

    def drain() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.append(line)
            print("app:", line.rstrip(), flush=True)

    threading.Thread(target=drain, daemon=True).start()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        match = next((m for m in map(RUNNING.search, list(lines)) if m), None)
        if match:
            return proc, match.group(1)
        if proc.poll() is not None:
            break
        time.sleep(0.2)
    stop_app(proc)
    raise SystemExit("the app did not print its link")


def stop_app(proc: subprocess.Popen[str]) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def untick_data_check(page: Page) -> None:
    """The shared-data check downloads from OSF. CI must not depend on it."""
    # By role: the label text of a Gradio checkbox starts with a space.
    box = page.get_by_role("checkbox", name=re.compile(r"^\s*Check the shared data files"))
    if not box.count():
        print("data check box: not on the page", flush=True)
    elif box.first.is_checked():
        box.first.uncheck()
        print("data check box: unticked", flush=True)
    else:
        print("data check box: already unticked", flush=True)


def check_results(page: Page, what: str) -> None:
    """Wait for the table, then assert Validated rows and a report with content."""
    table = page.get_by_label("Results")
    table.wait_for(timeout=60_000)
    validated = table.get_by_text("Validated", exact=True)
    try:
        validated.first.wait_for(timeout=SLOW)
    except PlaywrightTimeout:
        raise AssertionError(f"{what}: no Validated row appeared") from None
    n = validated.count()
    if n < 5:
        raise AssertionError(f"{what}: {n} Validated rows, expected at least 5")
    print(f"{what}: {n} Validated rows", flush=True)
    failed = table.get_by_text(re.compile(r"^\s*Failed")).count()
    if failed:
        raise AssertionError(f"{what}: {failed} checks failed")
    page.locator("iframe[title='Report']").wait_for(state="attached", timeout=60_000)
    body = page.frame_locator("iframe[title='Report']").locator("body")
    body.wait_for(state="attached", timeout=60_000)
    length = 0
    for _ in range(60):
        length = len(body.inner_text().strip())
        if length >= 200:
            break
        time.sleep(0.5)
    if length < 200:
        raise AssertionError(f"{what}: the report is nearly empty ({length} characters)")
    print(f"{what}: report has {length} characters", flush=True)


def click_through(page: Page, url: str, paper: Path) -> None:
    page.goto(url)
    page.get_by_role("button", name="Try the demo paper").wait_for(timeout=60_000)
    untick_data_check(page)
    page.get_by_role("button", name="Try the demo paper").click()
    check_results(page, "demo paper")

    page.reload()  # a clean page, so the second table is the second run's
    page.get_by_role("button", name="Check my paper").wait_for(timeout=60_000)
    untick_data_check(page)
    block = page.locator(".block", has_text="Your paper (PDF, GROBID XML or bibr JSON)").last
    block.locator("input[type=file]").set_input_files(str(paper))
    block.get_by_label(paper.name, exact=True).first.wait_for(timeout=30_000)
    page.get_by_role("button", name="Check my paper").click()
    check_results(page, "uploaded JSON")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("app")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--paper", type=Path, default=PAPER)
    parser.add_argument(
        "--shots", type=Path, default=Path("ui-smoke"), help="folder for the failure screenshot"
    )
    parser.add_argument("--browser-path", help="use this Chromium instead of Playwright's own")
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()

    proc, url = start_app(args.app, args.port, args.timeout)
    errors: list[str] = []
    outside: list[str] = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                executable_path=args.browser_path,
                args=["--no-sandbox"] if sys.platform == "linux" else [],
            )
            page = browser.new_page()
            page.on(
                "console",
                lambda m: errors.append(f"{m.type}: {m.text}") if m.type == "error" else None,
            )
            page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

            def seen(request: Request) -> None:
                parts = urlsplit(request.url)
                if parts.scheme in ("http", "https", "ws", "wss") and parts.hostname != "127.0.0.1":
                    outside.append(request.url)

            page.on("request", seen)
            failed = True
            try:
                click_through(page, url, args.paper)
                failed = False
            finally:
                for line in errors:
                    print("console:", line, flush=True)
                for request_url in outside:
                    print("outside request:", request_url, flush=True)
                if failed or errors or outside:
                    args.shots.mkdir(parents=True, exist_ok=True)
                    shot = args.shots / "failure.png"
                    page.screenshot(path=str(shot), full_page=True)
                    print(f"screenshot: {shot}", flush=True)
                browser.close()
    finally:
        stop_app(proc)
    if errors or outside:
        return 1
    print("ui smoke ok", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
