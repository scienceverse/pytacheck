"""Click through an installed metacheck-app in a browser:
python install/ui_smoke.py APP [--port N] [--shots DIR] [--browser-path EXE]

Starts the app without a browser, opens its token link in Chromium (Playwright for
Python), runs the demo paper, then uploads the demo JSON and checks it, then checks a
small data package on the "Check a data package" page: a folder outside the home folder
is refused, and a folder inside it is checked with the page's default settings. The page
may talk to 127.0.0.1 only. The app is the one the installer makes, without the concepts
extra, so the page must say that the classifier is not installed. Needs
`pip install playwright` and `playwright install chromium`.
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import ConsoleMessage, Page, Request, sync_playwright
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
SETTLE = 1.0  # s: between two events of one page, see settle()


class Watch:
    """What the page logs as errors, apart from what this script causes itself.

    Navigating away from a page that still has an event stream open makes Gradio's client log
    two console errors (an AbortError and "Connection errored out"). The script does that
    when it reloads or leaves a page, and the stream can stay open for a long time after the
    result is on the screen, so those two messages, and only those, are not counted when they
    come during a navigation that the script itself makes (:meth:`go`). Any other error, and
    the same two outside a navigation, still fail the run.
    """

    CUT = re.compile(r"AbortError: BodyStreamBuffer was aborted|Connection errored out")

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.cut = 0
        self.leaving = False

    def on_console(self, message: ConsoleMessage) -> None:
        if message.type != "error":
            return
        if self.leaving and self.CUT.search(message.text):
            self.cut += 1
        else:
            self.errors.append(f"{message.type}: {message.text}")

    def on_pageerror(self, error: object) -> None:
        self.errors.append(f"pageerror: {error}")

    def go(self, move: Callable[..., object], *args: str) -> None:
        """``page.goto(url)`` or ``page.reload()``, as a navigation the script makes."""
        self.leaving = True
        try:
            move(*args)
        finally:
            self.leaving = False


def settle(page: Page) -> None:
    """Pause between two events of one page, so that the second does not start as the first ends.

    Gradio's client can lose an event that starts at the moment the stream of the one before
    closes (the event runs and its answer never arrives). A person cannot click that fast;
    a script can.
    """
    page.wait_for_timeout(int(SETTLE * 1000))


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


def click_through(page: Page, url: str, paper: Path, watch: Watch) -> None:
    watch.go(page.goto, url)
    page.get_by_role("button", name="Try the demo paper").wait_for(timeout=60_000)
    untick_data_check(page)
    page.get_by_role("button", name="Try the demo paper").click()
    check_results(page, "demo paper")

    watch.go(page.reload)  # a clean page, so the second table is the second run's
    page.get_by_role("button", name="Check my paper").wait_for(timeout=60_000)
    untick_data_check(page)
    block = page.locator(".block", has_text="Your paper (PDF, GROBID XML or bibr JSON)").last
    block.locator("input[type=file]").set_input_files(str(paper))
    block.get_by_label(paper.name, exact=True).first.wait_for(timeout=30_000)
    page.get_by_role("button", name="Check my paper").click()
    check_results(page, "uploaded JSON")


def click_through_package(page: Page, url: str, watch: Watch) -> None:
    """The data package page: a folder outside home is refused, one inside it is checked."""
    parts = urlsplit(url)
    home = Path.home()
    # the page reads only inside the home folder: the package goes there, not to /tmp
    with tempfile.TemporaryDirectory(prefix="metacheck-ui-package-", dir=home) as tmp:
        root = Path(tmp) / "study"
        (root / "data").mkdir(parents=True)
        (root / "README.md").write_text("# Study\n\nSurvey data.\n", encoding="utf-8")
        (root / "data" / "survey.csv").write_text("id,age\n1,23\n2,31\n", encoding="utf-8")
        watch.go(page.goto, f"{parts.scheme}://{parts.netloc}/package")  # the token cookie is set
        box = page.get_by_placeholder("/path/to/my_package")
        box.wait_for(timeout=60_000)
        check = page.get_by_role("button", name="Check the package")
        settle(page)  # the page's own start-up is done before the first click

        outside = Path(home.anchor)  # the root of the disk: never inside home
        if outside != home:
            box.fill(str(outside))
            check.click()
            page.get_by_text("outside the places this page may read").first.wait_for(timeout=60_000)
            print("data package: a folder outside home is refused", flush=True)
            settle(page)

        classifier = page.get_by_role(
            "checkbox", name=re.compile(r"^\s*Name the concept of each data column")
        )
        if not classifier.first.is_checked():
            raise AssertionError("data package: the classifier box is not ticked by default")
        box.fill(str(root))
        check.click()
        page.get_by_text("Data Package Files").first.wait_for(timeout=SLOW)
        page.get_by_text(re.compile(r"^Checked study in ")).wait_for(timeout=SLOW)
        # no concepts extra in this install: the run falls back to rules and says so
        page.get_by_text("The local classifier is not installed").wait_for(timeout=60_000)
        print("data package: the missing classifier is said", flush=True)
        statuses = page.get_by_text(re.compile(r"^(Pass|Warning|Fail)$"))
        statuses.first.wait_for(state="attached", timeout=60_000)
        failed = page.get_by_text("Did not run", exact=True).count()
        if failed:
            raise AssertionError(f"data package: {failed} checks did not run")
        print(f"data package: {statuses.count()} status cells", flush=True)
        link = page.get_by_role("link", name="Download the report")
        link.wait_for(timeout=60_000)
        href = link.get_attribute("href") or ""
        if not href.startswith("/report/"):
            raise AssertionError(f"data package: odd download link {href!r}")
        print("data package: report link ok", flush=True)


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
    watch = Watch()
    outside: list[str] = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                executable_path=args.browser_path,
                args=["--no-sandbox"] if sys.platform == "linux" else [],
            )
            page = browser.new_page()
            page.on("console", watch.on_console)
            page.on("pageerror", watch.on_pageerror)

            def seen(request: Request) -> None:
                parts = urlsplit(request.url)
                if parts.scheme in ("http", "https", "ws", "wss") and parts.hostname != "127.0.0.1":
                    outside.append(request.url)

            page.on("request", seen)
            failed = True
            try:
                click_through(page, url, args.paper, watch)
                click_through_package(page, url, watch)
                failed = False
            finally:
                if watch.cut:
                    print(
                        f"console: {watch.cut} cut-stream messages at our own navigation, not counted"
                    )
                for line in watch.errors:
                    print("console:", line, flush=True)
                for request_url in outside:
                    print("outside request:", request_url, flush=True)
                if failed or watch.errors or outside:
                    args.shots.mkdir(parents=True, exist_ok=True)
                    shot = args.shots / "failure.png"
                    page.screenshot(path=str(shot), full_page=True)
                    print(f"screenshot: {shot}", flush=True)
                browser.close()
    finally:
        stop_app(proc)
    if watch.errors or outside:
        return 1
    print("ui smoke ok", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
