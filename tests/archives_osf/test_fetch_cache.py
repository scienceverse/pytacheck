"""Public recursive trees persist briefly; failed and partial listings retry."""

import pandas as pd
import pytest

from metacheck.archives import osf
from metacheck.archives.info_cache import _repo_info_cache_get
from metacheck.utils import local_options


@pytest.fixture(autouse=True)
def fresh_listings():
    osf.osf_cache_clear()
    yield
    osf.osf_cache_clear()


def _tree(public=True):
    return pd.DataFrame({"osf_type": ["nodes"], "public": [public], "name": ["Project"]})


def test_recursive_cache_survives_restart_and_expires(monkeypatch):
    calls = []
    clock = [1000.0]
    monkeypatch.setattr(osf.time, "time", lambda: clock[0])
    monkeypatch.setattr(osf.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(osf, "_osf_info_listing", lambda *a: calls.append(a) or _tree())
    with local_options({"metacheck.osf.cache": True, "metacheck.osf.cache_ttl": 300}):
        expected = osf.osf_info("abcde", recursive=True, cache=True)
        assert len(calls) == 1
        osf.osf_cache_clear()
        hit = osf.osf_info("abcde", recursive=True, cache=True)
        pd.testing.assert_frame_equal(expected, hit)
        assert len(calls) == 1
        hit.loc[0, "name"] = "edited result"
        assert osf.osf_info("abcde", recursive=True, cache=True)["name"].iloc[0] == "Project"
        clock[0] += 301
        osf.osf_info("abcde", recursive=True, cache=True)
        assert len(calls) == 2


@pytest.mark.parametrize(
    "url,public", [("abcde", False), ("https://osf.io/abcde/?view_only=secret", True)]
)
def test_private_and_view_only_trees_do_not_persist(monkeypatch, url, public):
    monkeypatch.setattr(osf, "_osf_info_listing", lambda *a: _tree(public))
    with local_options({"metacheck.osf.cache": True}):
        osf.osf_info(url, recursive=True, cache=True)
        assert _repo_info_cache_get("osf_tree", osf._osf_cache_key(url, True)) is None


@pytest.mark.parametrize("partial", [True, False])
def test_incomplete_and_failed_traversals_are_not_cached(monkeypatch, partial):
    calls = []
    page = osf.OsfResult()
    if partial:
        page.osf_incomplete = {"expected": 2, "got": 1}
    else:
        page.osf_error = "request_failed"
    monkeypatch.setattr(osf, "osf_get_all_pages", lambda url: page)

    def listing(*args):
        calls.append(args)
        osf._fetch_listings(["https://api.osf.io/nodes/abcde/files/"])
        return _tree()

    monkeypatch.setattr(osf, "_osf_info_listing", listing)
    with local_options({"metacheck.osf.cache": True}):
        for _ in range(2):
            osf.osf_info("abcde", recursive=True, cache=True)
        assert len(calls) == 2
        assert osf.osf_cache_clear() == 0
        assert _repo_info_cache_get("osf_tree", osf._osf_cache_key("abcde", True)) is None


def test_cache_identity_includes_order_column_endpoint_and_credentials(monkeypatch):
    from metacheck.archives import osf_helpers

    headers = {"Authorization": "one"}
    monkeypatch.setattr(osf_helpers, "_osf_headers", lambda: {"headers": headers})
    before = osf._osf_cache_key(["abcde", "fghij"], True)
    assert before != osf._osf_cache_key(["fghij", "abcde"], True)
    assert before != osf._osf_cache_key(["abcde", "fghij"], True, "other")
    headers["Authorization"] = "two"
    assert before != osf._osf_cache_key(["abcde", "fghij"], True)
    with local_options({"metacheck.osf.api": "https://other.example/v2"}):
        assert before != osf._osf_cache_key(["abcde", "fghij"], True)
