"""OSF requests use an HTTP/1.1 client: many streams on one HTTP/2 connection get reset."""

from __future__ import annotations

import pytest

from pytacheck import http


@pytest.fixture(autouse=True)
def fresh_clients() -> None:
    http.close_client()


@pytest.mark.parametrize(
    "url",
    ["https://osf.io/download/abc/", "https://api.osf.io/v2/nodes/x/", "https://files.osf.io/v1/"],
)
def test_osf_hosts_get_the_http1_client(url: str) -> None:
    client = http.client_for(url)
    assert client is not http.client()
    assert client is http.client_for("https://osf.io/")  # one shared client
    assert client._transport._pool._http2 is False  # type: ignore[attr-defined]
    assert http.client()._transport._pool._http2 is True  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "url", ["https://zenodo.org/api/", "https://notosf.io/", "https://x.example/osf.io"]
)
def test_other_hosts_keep_the_shared_client(url: str) -> None:
    assert http.client_for(url) is http.client()


def test_close_client_closes_both() -> None:
    a, b = http.client(), http.client_for("https://osf.io/")
    http.close_client()
    assert a.is_closed and b.is_closed
