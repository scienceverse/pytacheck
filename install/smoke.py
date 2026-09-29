"""Smoke test for an installed metacheck-app: python install/smoke.py APP [--port N].

Runs `APP --self-test`, starts the app without a browser, and checks the
token gate on 127.0.0.1. Standard library only, so it runs on any runner.
"""

import argparse
import http.cookiejar
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

RUNNING = re.compile(r"metacheck is running at (http://127\.0\.0\.1:\d+/\?token=\S+)")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def status(opener, url):
    try:
        with opener.open(url, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as err:
        return err.code


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("app")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()

    print("self-test ...", flush=True)
    subprocess.run([args.app, "--self-test"], check=True, timeout=300)  # noqa: S603

    proc = subprocess.Popen(  # noqa: S603
        [args.app, "--no-browser", "--port", str(args.port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        # Without this a print() that is not flushed waits in a buffer.
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    lines: list[str] = []

    def drain() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.append(line)
            print("app:", line.rstrip(), flush=True)

    threading.Thread(target=drain, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{args.port}"
        no_proxy = urllib.request.ProxyHandler({})
        plain = urllib.request.build_opener(no_proxy, NoRedirect)

        deadline = time.monotonic() + args.timeout
        while True:
            try:
                with plain.open(f"{base}/healthz", timeout=5) as response:
                    if response.status == 200 and json.load(response) == {"ok": True}:
                        break
            except (OSError, ValueError):
                pass
            if proc.poll() is not None:
                print(f"the app exited early with code {proc.returncode}")
                return 1
            if time.monotonic() > deadline:
                print(f"/healthz did not answer within {args.timeout} s")
                return 1
            time.sleep(0.5)
        print("healthz ok", flush=True)

        code = status(plain, f"{base}/")
        if code != 403:
            print(f"/ without the token gave {code}, expected 403")
            return 1
        print("/ without the token: 403", flush=True)

        # The link can appear a moment after /healthz starts to answer.
        match = None
        link_deadline = time.monotonic() + 30
        while match is None:
            match = next((m for m in map(RUNNING.search, list(lines)) if m), None)
            if match is not None:
                break
            if proc.poll() is not None or time.monotonic() > link_deadline:
                print("the app did not print its link")
                return 1
            time.sleep(0.2)
        jar = http.cookiejar.CookieJar()
        signed_in = urllib.request.build_opener(no_proxy, urllib.request.HTTPCookieProcessor(jar))
        code = status(signed_in, match.group(1))
        if code != 200:
            print(f"the token link gave {code}, expected 200")
            return 1
        code = status(signed_in, f"{base}/")
        if code != 200:
            print(f"/ with the cookie gave {code}, expected 200")
            return 1
        print("token link and cookie: 200", flush=True)
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


if __name__ == "__main__":
    sys.exit(main())
