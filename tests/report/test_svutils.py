"""Port of tests/testthat/test-svutils-message.R and test-svutils-pb.R."""

from __future__ import annotations

import io

import pytest

from pytacheck.config import verbose
from pytacheck.utils import ProgressBar, message, pb, suppress_messages


@pytest.fixture(autouse=True)
def _restore_verbose():
    old = verbose()
    yield
    verbose(old)


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


def test_pb_basic():
    verbose(True)
    pbar = pb(10)
    for _ in range(10):
        assert not pbar.finished
        pbar.tick()
    assert pbar.finished
    with pytest.raises(RuntimeError):
        pbar.tick()

    # terminate
    pbar = pb(10)
    pbar.terminate()
    with pytest.raises(RuntimeError):
        pbar.tick()

    # spin
    pbar = pb(None, "(:spin)")
    for _ in range(100):
        pbar.tick()
    pbar.terminate()
    assert pbar.finished
    with pytest.raises(RuntimeError):
        pbar.tick()

    # what
    pbar = pb(float("nan"), ":what is the letter")
    pbar.tick(tokens={"what": "A"})
    assert pbar.render() == "A is the letter"
    pbar.tick(tokens={"what": "B"})
    assert pbar.render() == "B is the letter"
    pbar.terminate()
    assert pbar.finished


def test_pb_dummy_when_quiet():
    verbose(False)
    pbar = pb(3)
    assert not isinstance(pbar, ProgressBar)
    for _ in range(5):
        pbar.tick()
    pbar.message("x")
    pbar.terminate()


def test_pb_renders_on_a_terminal():
    class TTY(io.StringIO):
        def isatty(self) -> bool:
            return True

    stream = TTY()
    bar = ProgressBar(4, ":what [:bar] :current/:total :percent", width=40, stream=stream)
    bar.tick(0, tokens={"what": "Running"})
    bar.tick(2)
    assert "Running [" in stream.getvalue() and "2/4  50%" in stream.getvalue()
    bar.tick(2)
    assert bar.finished
    assert stream.getvalue().endswith("\n")
    text = bar.render()
    assert text.startswith("Running [" + "=" * 10)
