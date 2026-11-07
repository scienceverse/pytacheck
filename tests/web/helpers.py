"""Helpers of the web interface tests: a fake job, tiny uploads, ways to talk to the app.

The fake job is told what to do by the name of the upload: ``hold`` waits until the test
creates ``<jobs dir>/gate``, ``fail`` raises, ``hang`` never ends, ``keys`` answers with the
names of the keys it was given.
"""

from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from metacheck.web.worker import JobFailed

PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
BIBR = json.dumps({"title": "A paper", "sections": []}).encode()
START = 1_700_000_000.0


class Clock:
    """A clock a test moves by hand."""

    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> float:
        return self.now


def fake_job(
    spec: dict[str, Any], folder: str, keys: dict[str, str], report: Any
) -> dict[str, Any]:
    folder_path = Path(folder)
    name = str(spec["name"])
    report(0.5, "working")
    if name.startswith("hold"):
        gate = folder_path.parent / "gate"
        while not gate.exists():
            time.sleep(0.02)
    if name.startswith("fail"):
        raise JobFailed("The paper could not be read.")
    if name.startswith("hang"):
        time.sleep(120)
    answer = "{}" if spec["kind"] in ("convert", "check") else "<html><body>report</body></html>"
    if name.startswith("keys"):
        # names only: the value of a key is never written down
        answer = json.dumps({s: len(v) for s, v in sorted(keys.items())})
    (folder_path / "result.out").write_text(answer, encoding="utf-8")
    json_result = spec["kind"] in ("convert", "check") or name.startswith("keys")
    return {
        "result": "result.out",
        "type": "application/json" if json_result else "text/html; charset=utf-8",
        "converter": None if spec["kind"] == "repocheck" else "grobid",
        "report_type": None if json_result else "full",
    }


def zip_bytes(members: dict[str, bytes | str]) -> bytes:
    """A zip of *members*, the same bytes every time: a parameter of a test must not differ
    between pytest-xdist's workers (a member's time would be the time it was made)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            # as writestr() sets them for a name
            info.external_attr = (0o40775 << 16) | 0x10 if name.endswith("/") else 0o600 << 16
            archive.writestr(info, data)
    return buffer.getvalue()


def send(
    client: TestClient,
    name: str = "paper.pdf",
    data: bytes = PDF,
    path: str = "/api/jobs",
    **fields: str,
) -> Any:
    """POST a file (and form fields) to the web interface."""
    return client.post(path, files={"file": (name, data, "application/octet-stream")}, data=fields)


def form(client: TestClient, path: str, fields: dict[str, str]) -> Any:
    """POST form fields without a file, as ``curl -F name=value`` does."""
    return client.post(path, files={name: (None, value) for name, value in fields.items()})


def wait_for(client: TestClient, job_id: str, status: str, seconds: float = 20.0) -> dict[str, Any]:
    """Follow a job until it has *status*; its status body."""
    end = time.monotonic() + seconds
    body: dict[str, Any] = {}
    while time.monotonic() < end:
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] == status:
            return body
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} is {body.get('status')}, not {status}")
