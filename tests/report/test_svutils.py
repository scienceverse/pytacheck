"""Port of tests/testthat/test-svutils-message.R; progress bars (pb()) on rich."""

from __future__ import annotations

import io
import sys

import pytest

import metacheck.utils as utils
from metacheck.config import verbose
from metacheck.utils import message, pb, suppress_messages


@pytest.fixture(autouse=True)
def _restore_verbose():
    old = verbose()
    yield
    verbose(old)
    utils._stop_progress()  # a test that leaves a bar open must not leak the display
    utils._open_bars = 0


class TTY(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_message(capsys):
    verbose(True)
    message("hi")
    assert capsys.readouterr().err == "hi\n"
    message("a", 1, " ", 2.5, appendLF=False)
    assert capsys.readouterr().err == "a1 2.5"
    verbose(False)
    message("hi")
    assert capsys.readouterr().err == ""


def test_message_interactive_is_green(monkeypatch):
    class TTY(io.StringIO):
        def isatty(self) -> bool:
            return True

    stream = TTY()
    monkeypatch.setattr("sys.stderr", stream)
    verbose(True)
    message("hi")
    assert stream.getvalue() == "\033[32mhi\033[39m\n"


def test_suppress_messages_is_local_to_the_block_and_thread(capsys):
    # R's suppressMessages(): only the block's messages, not other threads'
    import threading

    verbose(True)
    with suppress_messages():
        message("hidden")
        worker = threading.Thread(target=message, args=("other thread",))
        worker.start()
        worker.join()
    message("shown")
    assert capsys.readouterr().err == "other thread\nshown\n"
    assert verbose() is True


def test_pb_requires_total():
    verbose(True)
    with pytest.raises(TypeError):
        pb()  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="non-negative"):
        pb(-1)


def test_pb_basic():
    verbose(True)
    pbar = pb(10)
    for _ in range(10):
        assert not pbar.finished
        pbar.tick()
    assert pbar.finished
    pbar.tick()  # a finished bar ignores ticks

    pbar = pb(10)
    pbar.terminate()
    assert pbar.finished
    pbar.tick()

    # an unknown total never finishes on its own
    for total in (None, float("nan")):
        pbar = pb(total, "(:spin) :what")
        for _ in range(100):
            pbar.tick(tokens={"what": "A"})
        assert not pbar.finished
        pbar.terminate()
        assert pbar.finished
    with pytest.raises(RuntimeError, match="unknown total"):
        pb(None).update(0.5)

    pbar = pb(4)
    pbar.update(0.5)
    assert pbar.current == 2
    pbar.update(1)
    assert pbar.finished


def test_pb_prints_nothing_off_a_terminal(capsys):
    verbose(True)
    pbar = pb(3, ":what [:bar] :current/:total")
    for i in range(3):
        pbar.tick(tokens={"what": i})
    pbar.message("x")
    assert capsys.readouterr().err == ""
    assert utils._progress is None


def test_pb_prints_nothing_when_quiet(monkeypatch):
    stream = TTY()
    monkeypatch.setattr(sys, "stderr", stream)
    verbose(False)
    pbar = pb(3)
    for _ in range(5):
        pbar.tick()
    pbar.message("x")
    pbar.terminate()
    assert stream.getvalue() == ""
    assert utils._progress is None


def test_pb_draws_on_a_terminal(monkeypatch):
    stream = TTY()
    monkeypatch.setattr(sys, "stderr", stream)
    verbose(True)
    bar = pb(4, ":what [:bar] :current/:total :elapsedfull")
    bar.tick(0, tokens={"what": "Running"})
    bar.tick(2)
    utils._progress.refresh()
    drawn = stream.getvalue()
    assert "Running" in drawn and "2/4" in drawn
    bar.tick(2)
    assert bar.finished
    assert utils._progress is None  # the display is stopped with its last bar


def test_pb_bars_share_one_display(monkeypatch):
    # a spinner with a bar inside it (repo_check, the archive downloads): rich allows one live display
    stream = TTY()
    monkeypatch.setattr(sys, "stderr", stream)
    verbose(True)
    spinner = pb(None, "(:spin) :what")
    spinner.tick(0, {"what": "Reading"})
    bar = pb(2, "Reading zip contents [:bar] :current/:total")
    bar.tick()
    utils._progress.refresh()
    drawn = stream.getvalue()
    assert "Reading" in drawn and "Reading zip contents" in drawn and "1/2" in drawn
    bar.tick()
    assert utils._progress is not None  # the spinner is still open
    spinner.terminate()
    assert utils._progress is None
    assert sys.stderr is stream  # rich gave stderr back
