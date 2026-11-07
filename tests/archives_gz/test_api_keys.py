"""Tests of the API key handling (port of test-api-keys.R, metacheck 0.4.3.x).

Every test has a state folder of its own and no keys, and no request leaves the machine:
the key checks are replaced, the services' answers are mocked.
"""

from __future__ import annotations

import datetime as dt
import json
import warnings
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import respx

from metacheck import http
from metacheck.archives import api_keys, dryad, researchbox
from metacheck.archives.api_keys import (
    KEY_SERVICES,
    api_key_status,
    api_keys_use,
    key_limit_reached,
    key_problem,
    key_problem_clear,
    key_problem_message,
    key_problem_record,
    key_service_for_host,
    key_source,
    key_use,
    openalex_key,
    state_dir,
)
from metacheck.archives.download import _url_host_is
from metacheck.archives.dryad import _dryad_401_hint, _dryad_headers, dryad_pat
from metacheck.archives.github import _github_limit, github_tree_files
from metacheck.archives.gitlab import _gitlab_config, _gitlab_limit, gitlab_pat
from metacheck.archives.osf_helpers import osf_pat
from metacheck.db.crossref import _openalex_headers, _openalex_limit
from metacheck.utils import get_option, local_options, options

_ENVS = (
    "GITHUB_PAT",
    "OSF_PAT",
    "DRYAD_PAT",
    "DRYAD_CLIENT_ID",
    "DRYAD_CLIENT_SECRET",
    "RESEARCHBOX_API_KEY",
    "GITLAB_PAT",
    "OPENALEX_API_KEY",
)
_OPTIONS = (
    "metacheck.keys.shared",
    "metacheck.github.pat",
    "metacheck.osf.pat",
    "metacheck.dryad.pat",
    "metacheck.dryad.client_id",
    "metacheck.researchbox.token",
    "metacheck.gitlab.pat",
    "metacheck.openalex.key",
)


@pytest.fixture(autouse=True)
def no_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A state folder of its own and no keys, for one test."""
    for name in _ENVS:
        monkeypatch.setenv(name, "")
    monkeypatch.delenv("METACHECK_STATE_DIR", raising=False)
    # git's credential store may hold a GitHub token
    monkeypatch.setattr(api_keys, "github_pat", lambda *a, **k: "")
    monkeypatch.setattr(api_keys, "_CHECKED", {})
    with local_options({"metacheck.state.dir": str(tmp_path / "state"), **dict.fromkeys(_OPTIONS)}):
        yield


def _in(seconds: float) -> dt.datetime:
    return dt.datetime.now(dt.UTC) + dt.timedelta(seconds=seconds)


# ---------------------------------------------------------------------- the state folder


def test_the_state_folder_is_the_option_else_the_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with local_options({"metacheck.state.dir": None}):
        monkeypatch.setenv("METACHECK_STATE_DIR", "/srv/state")
        assert state_dir() == Path("/srv/state")
        with local_options({"metacheck.state.dir": "/opt/state"}):
            assert state_dir() == Path("/opt/state")
        monkeypatch.setenv("METACHECK_STATE_DIR", "")
        assert state_dir().is_absolute()
        assert state_dir() != Path("/srv/state")


# --------------------------------------------------------------------------- whose key


def test_whose_key_is_used_a_visitors_the_servers_or_none(monkeypatch: pytest.MonkeyPatch) -> None:
    assert key_source("osf") == "none"
    monkeypatch.setenv("OSF_PAT", "server-key")
    assert key_source("osf") == "server"
    with local_options({"metacheck.osf.pat": "server-key"}):
        assert key_source("osf") == "server"
    with local_options({"metacheck.osf.pat": "own-key"}):
        assert key_source("osf") == "user"
    # Dryad's server key may be a client id and secret
    monkeypatch.setenv("DRYAD_CLIENT_ID", "id")
    monkeypatch.setenv("DRYAD_CLIENT_SECRET", "secret")
    assert key_source("dryad") == "server"


# --------------------------------------------------------------------- problems recorded


def test_key_problems_are_kept_in_the_state_folder_until_they_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_PAT", "server-key")
    monkeypatch.setattr(api_keys, "github_pat", lambda *a, **k: "server-key")
    assert key_problem("github") is None
    key_problem_record("github", "limit", _in(600))
    p = key_problem("github")
    assert p is not None
    assert p["problem"] == "limit" and p["source"] == "server"
    assert (state_dir() / "key_status.json").exists()

    # a record about the server's key does not concern a visitor's own key, nor the
    # requests without a key once the server's key is removed
    with local_options({"metacheck.github.pat": "own-key"}):
        assert key_problem("github") is None
    assert key_problem("github", source="none") is None

    # a limit that has ended no longer counts
    key_problem_record("github", "limit", _in(-1))
    assert key_problem("github") is None

    # a refused key is tried again after an hour
    key_problem_record("github", "rejected")
    p = key_problem("github")
    assert p is not None and p["problem"] == "rejected"
    assert p["until"] > _in(3500)
    key_problem_clear("github")
    assert key_problem("github") is None

    # problems with a visitor's own key are not recorded
    with local_options({"metacheck.github.pat": "own-key"}):
        key_problem_record("github", "limit", _in(600))
    assert "github" not in api_keys._status_read()


def test_the_state_file_holds_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSF_PAT", "sk-state-secret")
    key_problem_record("osf", "rejected")
    key_problem_record("osf", "limit", _in(60))
    text = (state_dir() / "key_status.json").read_text()
    assert "sk-state-secret" not in text
    assert set(json.loads(text)["osf"]) == {"problem", "source", "until", "time"}


# ---------------------------------------------------------------------------- messages


def test_the_messages_name_whose_key_it_is_and_what_to_do() -> None:
    until = dt.datetime(2026, 10, 28, 15, 0, tzinfo=dt.UTC)
    m = key_problem_message("dryad", "limit", until, source="server", shared=True)
    assert m == (
        "The Dryad key provided by metacheck has reached Dryad's limit until "
        "2026-10-28 15:00 UTC. Please provide your own Dryad key to continue "
        "(you can make one at https://datadryad.org/account)"
    )
    m = key_problem_message("github", "limit", until, source="server", shared=False)
    assert m.startswith("The GitHub key in GITHUB_PAT has reached GitHub's limit")
    m = key_problem_message("osf", "limit", until, source="none")
    assert m.startswith(
        "OSF's limit for requests without a key was reached until 2026-10-28 15:00 UTC."
    )
    m = key_problem_message("osf", "rejected", source="user")
    assert m.startswith("Your OSF key was rejected by OSF")
    assert m.endswith("Check the key, or make a new one at https://osf.io/settings/tokens")


# ------------------------------------------------------------------------ api_keys_use


def test_api_keys_use_sets_a_visitors_keys_and_skips_empty_ones() -> None:
    old = api_keys_use({"github": " gh-key ", "osf": "", "dryad": None}, shared=True)
    try:
        assert get_option("metacheck.github.pat") == "gh-key"
        assert osf_pat() == ""
        assert dryad_pat() == ""
        assert get_option("metacheck.keys.shared") is True
        assert key_source("github") == "user"
    finally:
        options(old)
    assert get_option("metacheck.github.pat") is None
    assert not get_option("metacheck.keys.shared")

    with pytest.raises(ValueError, match="Name each key by its service"):
        api_keys_use({"zenodo": "x"})
    api_keys_use()


def test_api_keys_use_error_does_not_echo_the_key() -> None:
    with pytest.raises(ValueError) as e:
        api_keys_use({"zenodo": "sk-do-not-print"})
    assert "sk-do-not-print" not in str(e.value)


# ------------------------------------------------------------------------ api_key_status


def test_api_key_status_lists_each_service_with_its_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RESEARCHBOX_API_KEY", "server-key")
    with local_options({"metacheck.keys.shared": True}):
        key_problem_record("researchbox", "limit", _in(600))
        st = api_key_status()
    assert list(st["service"]) == [
        "github",
        "osf",
        "dryad",
        "researchbox",
        "gitlab",
        "openalex",
    ]
    rb = st["service"] == "researchbox"
    assert st.loc[rb, "key"].item() == "server"
    assert st.loc[rb, "problem"].item() == "limit"
    assert (
        st.loc[rb, "message"]
        .item()
        .startswith("The ResearchBox key provided by metacheck has reached")
    )
    assert st.loc[~rb, "message"].isna().all()


def test_api_key_status_names_researchboxs_limit_for_aspredicted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # recorded apart from the limit for boxes, so the apps' notice, which reads
    # api_key_status(), did not tell visitors about it
    monkeypatch.setenv("RESEARCHBOX_API_KEY", "server-key")
    with local_options({"metacheck.keys.shared": True}):
        key_problem_record(
            "researchbox_aspredicted", "limit", _in(600), source=key_source("researchbox")
        )
        st = api_key_status()
        rb = st["service"] == "researchbox"
        assert st.loc[rb, "problem"].item() == "limit"
        message = st.loc[rb, "message"].item()
        assert message.startswith(
            "The ResearchBox key provided by metacheck has reached ResearchBox's "
            "limit for AsPredicted preregistrations until "
        )
        assert "Please provide your own ResearchBox key" in message
        # the limit for boxes, when both are reached, is the one named
        key_problem_record("researchbox", "limit", _in(600))
        st = api_key_status()
        assert (
            "has reached ResearchBox's limit until"
            in st.loc[st["service"] == "researchbox", "message"].item()
        )


# ---------------------------------------------------------------------------- key_use


def test_a_refused_key_is_checked_once_and_then_left_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITLAB_PAT", "refused-key-for-test")
    calls: list[str] = []

    def check(key: str) -> bool:
        calls.append(key)
        return False

    with pytest.warns(UserWarning, match="The GitLab key in GITLAB_PAT was rejected by GitLab"):
        assert key_use("gitlab", check=check) == ""
    p = key_problem("gitlab")
    assert p is not None and p["problem"] == "rejected"
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert key_use("gitlab", check=check) == ""
    assert len(calls) == 1

    # a key that passes, or could not be checked, is used
    monkeypatch.setenv("GITLAB_PAT", "good-key-for-test")
    assert key_use("gitlab", check=lambda key: True) == "good-key-for-test"
    assert "gitlab" not in api_keys._status_read()
    monkeypatch.setenv("GITLAB_PAT", "unchecked-key-for-test")
    assert key_use("gitlab", check=lambda key: None) == "unchecked-key-for-test"
    assert key_use("gitlab", key="") == ""


def test_the_memo_of_checked_keys_holds_digests_not_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSF_PAT", "sk-memo-secret")
    key_use("osf", check=lambda key: True)
    assert api_keys._CHECKED
    assert not any("sk-memo-secret" in name for name in api_keys._CHECKED)


def test_a_key_the_check_cannot_decide_is_not_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OSF_PAT", "k")

    def broken(key: str) -> bool:
        raise RuntimeError("no connection")

    assert key_use("osf", check=broken) == "k"
    assert "osf" not in api_keys._status_read()


def test_key_check_request_reads_the_status() -> None:
    with respx.mock() as router:
        router.get("https://x.example/ok").mock(return_value=httpx.Response(200))
        router.get("https://x.example/no").mock(return_value=httpx.Response(401))
        router.get("https://x.example/forbidden").mock(return_value=httpx.Response(403))
        router.get("https://x.example/down").mock(return_value=httpx.Response(503))
        assert api_keys.key_check_request("https://x.example/ok", {"A": "b"}) is True
        assert api_keys.key_check_request("https://x.example/no", {"A": "b"}) is False
        assert api_keys.key_check_request("https://x.example/forbidden", {"A": "b"}) is False
        assert api_keys.key_check_request("https://x.example/down", {"A": "b"}) is None


def test_gitlab_config_sends_a_token_only_when_gitlab_accepts_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def check(url: str, headers: dict[str, str]) -> bool:
        return headers.get("PRIVATE-TOKEN") != "refused-token-for-gitlab-test"

    monkeypatch.setattr(api_keys, "key_check_request", check)
    gitlab_pat("accepted-token-for-gitlab-test")
    assert _gitlab_config()["PRIVATE-TOKEN"] == "accepted-token-for-gitlab-test"
    gitlab_pat("refused-token-for-gitlab-test")
    with pytest.warns(UserWarning, match="rejected"):
        h = _gitlab_config()
    assert "PRIVATE-TOKEN" not in h
    assert "refused-token-for-gitlab-test" not in " ".join(h.values())


# ----------------------------------------------------------------------- host matching


def test_keys_go_only_to_the_host_they_belong_to() -> None:
    assert _url_host_is("https://osf.io/download/abc/", "osf.io")
    assert _url_host_is("https://files.de-1.osf.io/v1/x", "osf.io")
    assert not _url_host_is("https://web.archive.org/web/2020/https://osf.io/abc/", "osf.io")
    assert not _url_host_is("https://example.com/?u=zenodo.org", "zenodo.org")
    assert not _url_host_is("https://osf.io.example.com/x", "osf.io")
    assert not _url_host_is("https://notosf.io/x", "osf.io")
    assert not _url_host_is("not a url", "osf.io")
    assert _url_host_is("https://dataverse.nl/api/x", ["dataverse.harvard.edu", "dataverse.nl"])

    assert key_service_for_host("api.github.com") == "github"
    assert key_service_for_host("datadryad.org") == "dryad"
    assert key_service_for_host("files.osf.io") == "osf"
    assert key_service_for_host("raw.githubusercontent.com") is None
    assert key_service_for_host("zenodo.org") is None


def test_auth_for_url_sends_no_token_to_a_look_alike_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from metacheck.archives import zenodo_upload
    from metacheck.archives.download import _auth_for_url

    monkeypatch.setenv("OSF_PAT", "tok")
    monkeypatch.setattr(zenodo_upload, "zenodo_pat", lambda *a, **k: "ztok")
    for url in (
        "https://web.archive.org/web/2020/https://osf.io/abc/",
        "https://example.com/files?from=zenodo.org",
    ):
        spec = _auth_for_url({"method": "GET", "url": url, "headers": {}})
        assert "Authorization" not in spec["headers"]


def test_a_rate_limit_is_recorded_for_the_host_of_a_managed_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from metacheck.archives.download import _host_rate_limit_record

    monkeypatch.setenv("DRYAD_PAT", "server-token")
    reset = _in(900).timestamp()
    _host_rate_limit_record("datadryad.org", reset)
    p = key_problem("dryad")
    assert p is not None and p["problem"] == "limit"
    # a host without a managed key records nothing
    _host_rate_limit_record("zenodo.org", reset)
    _host_rate_limit_record("example.org", reset)
    assert set(api_keys._status_read()) == {"dryad"}


def test_the_request_layer_calls_the_hook_with_the_host_and_reset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(http, "sleep", lambda *a, **k: None)
    seen: list[tuple[str, float]] = []
    with respx.mock() as router:
        router.get("https://limited.example/x").mock(
            return_value=httpx.Response(429, headers={"Retry-After": "5"})
        )
        http.request(
            "GET",
            "https://limited.example/x",
            max_tries=1,
            on_reset=lambda host, r: seen.append((host, r)),
        )
    assert len(seen) == 1
    host, reset = seen[0]
    assert host == "limited.example"
    assert 0 < reset - dt.datetime.now(dt.UTC).timestamp() <= 6


# ------------------------------------------------------------------------------ limits


def _github_limited(reset: float) -> httpx.Response:
    return httpx.Response(
        403,
        headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(round(reset))},
        request=httpx.Request("GET", "https://api.github.com/repos/a/b"),
    )


def test_githubs_limit_is_reported_not_read_as_an_empty_repository() -> None:
    reset = _in(1800).timestamp()
    limited = _github_limited(reset)
    m = _github_limit(limited)
    assert m is not None
    assert m.startswith(
        "API rate limit exhausted: GitHub's limit for requests without a key was reached until"
    )
    p = key_problem("github")
    assert p is not None and p["problem"] == "limit"
    # a 403 for another reason is not a limit
    assert _github_limit(httpx.Response(403)) is None
    assert _github_limit(httpx.Response(200)) is None

    with respx.mock(assert_all_called=False) as router:
        router.route(host="api.github.com").mock(return_value=limited)
        router.route(host="github.com").mock(return_value=httpx.Response(404))
        r = github_tree_files("a/b")
    assert r["gated"] is True
    assert r["files"] is None
    assert str(r["reason"]).startswith("API rate limit exhausted: ")


def test_gitlabs_and_openalexs_limits_are_recorded() -> None:
    m = _gitlab_limit(httpx.Response(429, headers={"retry-after": "60"}))
    assert m is not None and m.startswith("API rate limit exhausted: GitLab's limit")
    assert _gitlab_limit(httpx.Response(404)) is None
    assert _gitlab_limit(None) is None

    m = _openalex_limit(httpx.Response(429))
    assert m is not None
    assert m.startswith("API rate limit exhausted: OpenAlex's limit")
    import re

    assert re.search(r"until \d{4}-\d\d-\d\d 00:00 UTC", m)
    assert _openalex_limit(httpx.Response(200)) is None


def test_limit_messages_do_not_hold_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITLAB_PAT", "sk-limit-secret")
    m = key_limit_reached("gitlab", _in(60))
    assert "sk-limit-secret" not in m
    assert "GITLAB_PAT" in m


# ---------------------------------------------------------------------------- OpenAlex


def test_openalex_key_gets_and_sets_the_key_sent_as_a_bearer_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert openalex_key() == ""
    spec = _openalex_headers({"method": "GET", "url": "https://api.openalex.org/works"})
    assert "Authorization" not in (spec.get("headers") or {})
    from metacheck.db import crossref

    monkeypatch.setattr(crossref, "_openalex_key_check", lambda key: True)
    openalex_key("oa-key-for-test")
    spec = _openalex_headers({"method": "GET", "url": "https://api.openalex.org/works"})
    assert spec["headers"]["Authorization"] == "Bearer oa-key-for-test"
    with pytest.raises(ValueError, match="single string"):
        openalex_key(1)  # type: ignore[arg-type]


# ------------------------------------------------------------------------------ Dryad


def test_a_visitors_dryad_token_comes_before_the_servers_client_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DRYAD_CLIENT_ID", "server-id")
    monkeypatch.setenv("DRYAD_CLIENT_SECRET", "server-secret")
    client = SimpleNamespace(token=lambda: "oauth-token")
    monkeypatch.setattr(dryad, "_dryad_oauth_client", lambda: client)
    url = {"method": "GET", "url": "https://datadryad.org/api/v2/files/1/download"}
    assert _dryad_headers(dict(url))["headers"]["Authorization"] == "Bearer oauth-token"
    dryad_pat("visitor-token")
    spec = _dryad_headers(dict(url))
    assert spec["headers"]["Authorization"] == "Bearer visitor-token"


def test_a_refused_dryad_key_is_recorded_and_named_in_the_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DRYAD_PAT", "server-token")
    with local_options({"metacheck.keys.shared": True}):
        hint = _dryad_401_hint("https://datadryad.org/api/v2/files/1/download", 401)
        assert "The Dryad key provided by metacheck was rejected by Dryad" in hint
        assert "server-token" not in hint
        p = key_problem("dryad")
        assert p is not None and p["problem"] == "rejected"
        assert _dryad_401_hint("https://datadryad.org/x", 404) == ""


# --------------------------------------------------------------------------- Dataverse


def test_dataverse_checks_each_installations_key_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from metacheck.archives.dataverse import _dataverse_headers, dataverse_pat

    asked: list[tuple[str, str]] = []

    def check(url: str, headers: dict[str, str]) -> bool:
        asked.append((url, headers["X-Dataverse-key"]))
        return headers["X-Dataverse-key"] != "refused"

    monkeypatch.setattr(api_keys, "key_check_request", check)
    with local_options(
        {
            "metacheck.dataverse.pat.DATAVERSE_HARVARD_EDU": None,
            "metacheck.dataverse.pat.DATAVERSE_NL": None,
        }
    ):
        dataverse_pat("dataverse.harvard.edu", "accepted")
        dataverse_pat("dataverse.nl", "refused")
        for _ in range(2):
            h = _dataverse_headers({"url": "https://dataverse.harvard.edu/api/x", "headers": {}})
            assert h["headers"]["X-Dataverse-key"] == "accepted"
        with pytest.warns(UserWarning):
            h = _dataverse_headers({"url": "https://dataverse.nl/api/x", "headers": {}})
        assert "X-Dataverse-key" not in h["headers"]
    assert asked == [
        ("https://dataverse.harvard.edu/api/users/:me", "accepted"),
        ("https://dataverse.nl/api/users/:me", "refused"),
    ]


# ------------------------------------------------------------------------- ResearchBox


@pytest.fixture
def no_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(researchbox, "_throttle", lambda: http.Throttle(1000, 1))
    monkeypatch.setattr(http, "sleep", lambda *a, **k: None)


def _json(status: int, body: str, **headers: str) -> httpx.Response:
    return httpx.Response(
        status, content=body.encode(), headers={"Content-Type": "application/json", **headers}
    )


def test_researchbox_a_refused_key_and_a_used_up_limit_stop_later_requests(
    monkeypatch: pytest.MonkeyPatch, no_waits: None
) -> None:
    monkeypatch.setenv("RESEARCHBOX_API_KEY", "server-key")
    api = "https://researchbox.org/api_call.php"
    with respx.mock(assert_all_called=False) as router:
        route = router.post(api)
        route.mock(return_value=_json(401, '{"ok":false,"error":"token invalid"}'))
        res = researchbox._rbox_api("box_meta", box_id=1)
        assert res["error"].startswith(
            "HTTP 401: token invalid. The ResearchBox key in RESEARCHBOX_API_KEY was rejected"
        )
        p = key_problem("researchbox")
        assert p is not None and p["problem"] == "rejected"
        res = researchbox._rbox_api("box_meta", box_id=2)
        assert route.call_count == 1
        assert res["error"].startswith("The ResearchBox key in RESEARCHBOX_API_KEY was rejected")

        # a limit for one box does not stop requests for other boxes; the key's daily limit does
        key_problem_clear("researchbox")
        route.mock(
            return_value=_json(
                429,
                '{"ok":false,"error":"list_files same-box hourly limit"}',
                **{"Retry-After": "1800"},
            )
        )
        assert "same-box hourly limit" in researchbox._rbox_api("list_files", box_id=3)["error"]
        assert key_problem("researchbox") is None
        route.mock(
            return_value=_json(429, '{"ok":false,"error":"daily limit"}', **{"Retry-After": "7200"})
        )
        err = researchbox._rbox_api("list_files", box_id=4)["error"]
        assert err.startswith(
            "API rate limit exhausted: daily limit (ResearchBox asks to wait 7200 seconds). "
            "The ResearchBox key"
        )
        n = route.call_count
        err = researchbox._rbox_api("list_files", box_id=5)["error"]
        assert err.startswith("API rate limit exhausted: The ResearchBox key")
        assert route.call_count == n

        # a visitor's own key is not held back by the server key's limit
        route.mock(return_value=_json(200, '{"ok":true}'))
        with local_options({"metacheck.researchbox.token": "own-key"}):
            assert researchbox._rbox_api("list_files", box_id=5)["ok"] is True
    assert "server-key" not in " ".join(p.read_text() for p in state_dir().glob("*") if p.is_file())


def test_every_service_has_a_label_an_option_an_env_name_and_a_url() -> None:
    assert list(KEY_SERVICES) == [
        "github",
        "osf",
        "dryad",
        "researchbox",
        "gitlab",
        "openalex",
    ]
    for s in KEY_SERVICES.values():
        assert s.label and s.option.startswith("metacheck.") and s.env.isupper()
        assert s.url.startswith("https://")
