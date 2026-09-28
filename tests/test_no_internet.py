"""tests/conftest.py fails a test not marked ``network`` that connects to the
internet, and leaves loopback and Unix sockets alone."""

from __future__ import annotations

import socket

import pytest

from tests.httpmock import NetworkUse, no_network

# TEST-NET-1: refused before the connection is made, and never routed anyway
INTERNET = ("192.0.2.1", 443)


def test_an_internet_connection_fails_the_test() -> None:
    with socket.socket() as s:
        with pytest.raises(pytest.fail.Exception, match=r"mark the test `network`") as e:
            s.connect(INTERNET)
        assert repr(INTERNET) in str(e.value)
        with pytest.raises(pytest.fail.Exception, match="reached for the internet"):
            s.connect_ex(INTERNET)


def test_loopback_and_unix_sockets_work() -> None:
    with socket.create_server(("127.0.0.1", 0)) as server, socket.socket() as client:
        client.connect(server.getsockname())
    a, b = socket.socketpair()  # AF_UNIX, as asyncio's self-pipe
    a.close()
    b.close()


def test_no_network_nests_inside_the_guard() -> None:
    guard = socket.socket.connect
    assert "connect" in vars(socket.socket)  # the guard, over _socket.socket's own
    with no_network(), socket.socket() as s, pytest.raises(NetworkUse):
        s.connect(INTERNET)
    assert socket.socket.connect is guard
