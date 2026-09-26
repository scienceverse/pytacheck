"""The db tests' fixtures isolate the environment between tests (a regression test).

``regcheck_start_local()`` sets ``REGCHECK_API_TOKEN`` for the session, as
metacheck's ``Sys.setenv()`` does. The autouse fixture of ``tests/db/conftest.py``
unset it with ``monkeypatch.delenv(raising=False)``, which records nothing to
undo when the variable is unset, so the token outlived the test: a later
``regcheck_compare(client="gr")`` in the same worker (the parity case
``db_review/regcheck_compare.msg.client_partial_token``) went to the network
instead of asking for a token. The two tests below run in that order, in a
pytest session of their own: the first under the real ``tests/db/conftest.py``,
the second, like ``tests/test_parity.py``, outside it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

CONFTEST = Path(__file__).resolve().parent / "conftest.py"

LEAK = """
import os
import subprocess

import pytacheck.db.regcheck_local as rl


class FakeProc:
    pid = 4242
    returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def wait(self, timeout=None):
        return 0


def test_start_local_sets_the_token(monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: FakeProc())
    monkeypatch.setattr(rl.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(rl, "_server_up", lambda url: True)
    rl.regcheck_start_local(model="llama3.2")
    rl.regcheck_stop_local()
    assert os.environ["REGCHECK_API_TOKEN"] == "metacheck-local"
"""

CHECK = """
import os

import pytest

from pytacheck.db.regcheck import RegCheckError, regcheck_compare


def test_hosted_client_asks_for_a_token(monkeypatch):
    def no_request(*args, **kwargs):
        raise AssertionError(f"reached the RegCheck server: {args}")

    monkeypatch.setattr("pytacheck.http.request", no_request)
    assert os.environ.get("REGCHECK_API_TOKEN") is None
    with pytest.raises(RegCheckError, match="requires an API token for the 'groq' client"):
        regcheck_compare("p", "r", client="gr")
"""


def test_start_local_token_does_not_leak_into_the_next_test(pytester: pytest.Pytester) -> None:
    db = pytester.mkdir("db")
    (db / "conftest.py").write_text(CONFTEST.read_text(encoding="utf-8"), encoding="utf-8")
    (db / "test_start_local.py").write_text(LEAK, encoding="utf-8")
    pytester.makepyfile(test_after=CHECK)
    result = pytester.runpytest_inprocess(
        "-p",
        "no:cacheprovider",
        "-p",
        "no:randomly",
        "--import-mode=importlib",
        "db/test_start_local.py",
        "test_after.py",
    )
    result.assert_outcomes(passed=2)
