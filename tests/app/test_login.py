"""Browser sign-in to bibr: the device flow against a fake gate, and the wizard's fallback."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

import metacheck.config as config
from metacheck.app import bibr, login, settings, setup

BASE = "https://bibr.example.org"


class FakeGate:
    """The two device endpoints; *token_answers* are served one per poll."""

    def __init__(
        self,
        token_answers: list[tuple[int, dict]] | None = None,
        code: tuple[int, dict] | None = None,
    ) -> None:
        self.code = code or (
            200,
            {
                "device_code": "dev123",
                "user_code": "BCDF-GHJK",
                "verification_uri": f"{BASE}/device",
                "verification_uri_complete": f"{BASE}/device?user_code=BCDF-GHJK",
                "expires_in": 900,
                "interval": 5,
            },
        )
        self.token_answers = list(token_answers or [])
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/device/code":
            status, body = self.code
            return httpx.Response(status, json=body)
        if request.url.path == "/device/token":
            status, body = self.token_answers.pop(0)
            return httpx.Response(status, json=body)
        return httpx.Response(404, json={"detail": "Not Found"})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


PENDING = (400, {"error": "authorization_pending"})
OK = (200, {"access_token": "bibr_pat_secret", "token_type": "bearer"})


def _login(gate: FakeGate, **kw):
    said: list[str] = []
    opened: list[str] = []
    slept: list[float] = []
    kw.setdefault("open_url", lambda url: opened.append(url) or True)
    token = login.device_login(BASE, said.append, client=gate.client(), sleep=slept.append, **kw)
    return token, said, opened, slept


def test_the_token_comes_after_the_person_approves() -> None:
    gate = FakeGate([PENDING, PENDING, OK])
    token, said, opened, slept = _login(gate)
    assert token == "bibr_pat_secret"
    assert opened == [f"{BASE}/device?user_code=BCDF-GHJK"]
    assert "BCDF-GHJK" in "\n".join(said)
    assert slept == [5, 5, 5]
    first, second = gate.requests[0], gate.requests[1]
    assert first.read() == b'{"name":"pytacheck"}'
    form = second.read().decode()
    assert "device_code=dev123" in form and "grant_type=urn%3Aietf%3Aparams%3Aoauth" in form


def test_slow_down_waits_five_seconds_longer_each_time() -> None:
    slow = (400, {"error": "slow_down"})
    _, _, _, slept = _login(FakeGate([slow, slow, OK]))
    assert slept == [5, 10, 15]


def test_the_link_is_printed_when_the_browser_cannot_open() -> None:
    _, said, _, _ = _login(FakeGate([OK]), open_url=lambda url: False)
    assert f"{BASE}/device?user_code=BCDF-GHJK" in "\n".join(said)


@pytest.mark.parametrize(
    ("error", "words"),
    [
        ("access_denied", "refused"),
        ("expired_token", "ran out"),
        ("token_limit", "most tokens"),
    ],
)
def test_a_refused_expired_or_full_sign_in_fails_with_the_reason(error: str, words: str) -> None:
    with pytest.raises(login.LoginFailed, match=words):
        _login(FakeGate([PENDING, (400, {"error": error})]))


def test_a_code_that_is_never_approved_expires() -> None:
    clock = iter([0.0, 100.0, 1000.0])
    with pytest.raises(login.LoginFailed, match="ran out"):
        _login(FakeGate([PENDING] * 3), clock=lambda: next(clock))


@pytest.mark.parametrize(
    "code",
    [
        (404, {"detail": "Not Found"}),
        (405, {}),
        (502, {}),
        (200, {"unexpected": True}),
    ],
)
def test_a_gate_without_the_endpoints_is_unavailable(code: tuple[int, dict]) -> None:
    with pytest.raises(login.LoginUnavailable, match="no browser sign-in"):
        _login(FakeGate(code=code))


def test_an_unreachable_gate_is_unavailable() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(refuse))
    with pytest.raises(login.LoginUnavailable, match="could not be reached"):
        login.device_login(BASE, lambda _t: None, client=client, sleep=lambda _s: None)


def test_a_lost_connection_while_waiting_gives_up_after_three_tries() -> None:
    calls = {"n": 0}
    gate = FakeGate()

    def flaky(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/device/token":
            calls["n"] += 1
            raise httpx.ReadTimeout("slow", request=request)
        return gate(request)

    client = httpx.Client(transport=httpx.MockTransport(flaky))
    with pytest.raises(login.LoginFailed, match="connection"):
        login.device_login(BASE, lambda _t: None, client=client, sleep=lambda _s: None)
    assert calls["n"] == 3


# --- which service a token may come from ---------------------------------------------------


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("METACHECK_BIBR_URL", "PYTACHECK_BIBR_URL", "METACHECK_BIBR_BACKEND"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(bibr.KEY_ENV, raising=False)
    monkeypatch.setitem(config._state, "email", None)


def test_only_a_hosted_bibr_service_is_offered_a_sign_in(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("METACHECK_BIBR_URL", BASE)
    assert login.service_for_login() == BASE
    monkeypatch.setenv("METACHECK_BIBR_URL", "http://127.0.0.1:8000")  # bibr serve: no token
    assert login.service_for_login() is None
    monkeypatch.setenv("METACHECK_BIBR_URL", BASE)
    monkeypatch.setenv("METACHECK_BIBR_BACKEND", "scivrs")
    assert login.service_for_login() is None
    monkeypatch.setenv("METACHECK_BIBR_URL", "not a url")
    monkeypatch.delenv("METACHECK_BIBR_BACKEND")
    assert login.service_for_login() is None


# --- the wizard ---------------------------------------------------------------------------


def _wizard(answers: list[str], sign_in: Callable[[], str] | None):
    lines: list[str] = []
    typed = iter(answers)
    ans = setup.run_wizard(
        lambda _p: next(typed), lines.append, hw="cpu", free=100, sign_in=sign_in
    )
    return ans, "\n".join(lines)


def test_the_wizard_takes_the_token_from_the_browser_and_does_not_ask_for_one() -> None:
    ans, text = _wizard(["2", "1", "me@example.org"], lambda: "bibr_pat_browser")
    assert (ans.access, ans.key) == ("key", "bibr_pat_browser")
    assert "Signed in" in text and "Paste the key" not in text


def test_an_unavailable_sign_in_says_so_and_falls_back_to_the_pasted_token() -> None:
    def gone() -> str:
        raise login.LoginUnavailable("https://bibr.example.org has no browser sign-in.")

    ans, text = _wizard(["2", "1", "bibr_pat_pasted", "me@example.org"], gone)
    assert ans.key == "bibr_pat_pasted"
    assert "Browser sign-in is not available" in text and "paste a key instead" in text


def test_a_refused_sign_in_and_ctrl_c_also_fall_back() -> None:
    def refused() -> str:
        raise login.LoginFailed("The sign-in was refused in the browser.")

    ans, text = _wizard(["2", "1", "bibr_pat_pasted", "me@example.org"], refused)
    assert ans.key == "bibr_pat_pasted" and "refused" in text

    def interrupted() -> str:
        raise KeyboardInterrupt

    ans, text = _wizard(["2", "1", "bibr_pat_pasted", "me@example.org"], interrupted)
    assert ans.key == "bibr_pat_pasted" and "Sign-in stopped" in text


def test_no_service_to_sign_in_to_means_the_pasted_token(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("METACHECK_BIBR_URL", "http://127.0.0.1:8000")
    said: list[str] = []
    with pytest.raises(login.LoginUnavailable):
        setup._real_sign_in(said.append)()


def test_the_signed_in_token_is_saved_like_a_pasted_one(env: None) -> None:
    setup.save(setup.Answers(papers=True, access="key", key="bibr_pat_browser", email=""))
    assert bibr.load_key() == "bibr_pat_browser"
    assert not settings.load().get("bibr_selfhost")
