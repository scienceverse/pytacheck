"""Stores: index locations, fetching with a one-hour cache, offline fallback, config, search."""

from __future__ import annotations

import json
import time
import warnings

import httpx
import pytest
import respx

from pytacheck.config import BUILTIN_STORE_URL, load_config
from pytacheck.packs.stores import (
    StoreError,
    find_entry,
    index_location,
    store_add,
    store_index,
    store_list,
    store_remove,
    store_search,
    store_update,
)
from tests.modsys.helpers import mod_src
from tests.modsys.storekit import INDEX_URL, STORE_URL, FakeStore


@pytest.fixture
def store(ms, monkeypatch):
    monkeypatch.setenv("PYTACHECK_STORE_URL", STORE_URL)
    with respx.mock(assert_all_called=False) as router:
        yield FakeStore(router, ms)


def test_index_locations(tmp_path) -> None:
    raw = "https://raw.githubusercontent.com/scienceverse/pytacheck-modules/HEAD/index.json"
    assert index_location(BUILTIN_STORE_URL) == ("http", raw)
    assert index_location(BUILTIN_STORE_URL + ".git") == ("http", raw)
    assert index_location("https://example.org/store/index.json") == (
        "http",
        "https://example.org/store/index.json",
    )
    assert index_location("https://gitlab.com/lab/store") == (
        "http",
        "https://gitlab.com/lab/store/-/raw/HEAD/index.json",
    )
    assert index_location("https://mirror.example.org/store") == (
        "http",
        "https://mirror.example.org/store/index.json",
    )
    assert index_location(str(tmp_path)) == ("file", str(tmp_path / "index.json"))
    assert index_location(tmp_path.as_uri()) == ("file", str(tmp_path / "index.json"))
    with pytest.raises(StoreError, match="ssh"):
        index_location("git@github.com:x/y.git")


def test_fetch_is_cached_for_an_hour(store, ms) -> None:
    store.add("demo", {"hello": mod_src("hello")})
    route = store.router.routes["index"]
    route.reset()
    assert [p["name"] for p in store_index("pytacheck")["packs"]] == ["demo"]
    assert store_index("pytacheck")["packs"][0]["name"] == "demo"
    assert route.call_count == 1
    cache = ms.data / "stores" / "pytacheck"
    assert (cache / "index.json").is_file()
    meta = json.loads((cache / "meta.json").read_text())
    assert meta["url"] == INDEX_URL
    meta["fetched"] = time.time() - 3601  # expired
    (cache / "meta.json").write_text(json.dumps(meta))
    store_index("pytacheck")
    assert route.call_count == 2
    store_index("pytacheck", refresh=True)
    assert route.call_count == 3


def test_unreachable_store_uses_the_cache_with_a_warning(store, ms) -> None:
    store.add("demo", {"hello": mod_src("hello")})
    store_index("pytacheck")
    store.router.routes["index"].side_effect = httpx.ConnectError("offline")
    with pytest.warns(UserWarning, match="unreachable.*cached at"):
        data = store_index("pytacheck", refresh=True)
    assert data["packs"][0]["name"] == "demo"
    assert store_index("pytacheck", offline=True)["packs"][0]["name"] == "demo"


def test_unreachable_store_without_a_cache_says_what_to_do(store, ms) -> None:
    store.router.routes["index"].respond(404)
    with pytest.raises(StoreError, match=r"unreachable and there is no cached copy.*store add"):
        store_index("pytacheck")
    with pytest.raises(StoreError, match="no cached index"):
        store_index("pytacheck", offline=True)
    df = store_update()
    assert df.loc[0, "status"].startswith("The store 'pytacheck'")
    with pytest.raises(StoreError):
        store_update("pytacheck")


def test_malformed_index(store, ms) -> None:
    store.router.routes["index"].respond(json={"schema": 2, "packs": []})
    with pytest.raises(StoreError, match="schema"):
        store_index("pytacheck")


def test_local_folder_store(ms, tmp_path) -> None:
    folder = tmp_path / "lab-store"
    folder.mkdir()
    store_add("lab", str(folder))
    with pytest.raises(StoreError, match="store build"):
        store_index("lab")
    (folder / "index.json").write_text(json.dumps({"schema": 1, "packs": [{"name": "x"}]}))
    assert store_index("lab")["packs"] == [{"name": "x"}]
    assert not (ms.data / "stores" / "lab").exists(), "local stores are read directly"


def test_add_list_remove(ms, tmp_path) -> None:
    df = store_list()
    assert df["name"].tolist() == ["pytacheck"]
    assert df.loc[0, "url"] == BUILTIN_STORE_URL and df.loc[0, "defined_in"] == "builtin"
    path = store_add("mylab", "https://gitlab.com/lab/store")
    assert json.loads(path.read_text())["stores"] == {"mylab": "https://gitlab.com/lab/store"}
    assert store_list()["name"].tolist() == ["pytacheck", "mylab"]
    store_remove("mylab")
    assert "mylab" not in load_config().stores
    store_remove("pytacheck")  # the built-in store is masked with null
    assert json.loads(path.read_text())["stores"] == {"pytacheck": None}
    assert store_list().empty
    with pytest.raises(StoreError, match="no store named"):
        store_remove("pytacheck")
    with pytest.raises(StoreError, match="Invalid store name"):
        store_add("Bad Name", "https://example.org")


def test_search(store, ms) -> None:
    validation = '{"papers": 10, "tp": 9, "fp": 1, "fn": 3}'
    store.add(
        "trials",
        {
            "trial_reg": mod_src("trial_reg").replace(
                "requires=[])", f"requires=[], validation={validation})"
            )
        },
        fields=["medicine"],
        title="Clinical trial checks",
        reviewed="2026-09-01",
    )
    store.add(
        "psych",
        {"apa": mod_src("apa")},
        fields=["psychology"],
        description="APA reporting",
        presets={"thesis": {"modules": ["apa"]}},
    )
    assert store_search()["name"].tolist() == ["trials", "psych"]
    assert store_search("clinical")["name"].tolist() == ["trials"]
    assert store_search("apa reporting")["name"].tolist() == ["psych"]  # every word must match
    assert store_search("apa trials").empty
    assert store_search("thesis")["name"].tolist() == ["psych"]  # preset names are searched
    assert store_search(field="Psychology")["name"].tolist() == ["psych"]
    row = store_search(field="medicine").iloc[0]
    assert (row["validated"], row["reviewed"], bool(row["code"])) == ("1/1", "2026-09-01", True)
    assert not store_search()["installed"].any()


def test_search_skips_unreachable_stores(store, ms, tmp_path) -> None:
    store.add("demo", {"hello": mod_src("hello")})
    store_add("broken", str(tmp_path / "missing"))
    with pytest.warns(UserWarning, match="Skipping store 'broken'"):
        assert store_search()["name"].tolist() == ["demo"]


def test_ambiguous_names_need_a_store(store, ms, tmp_path) -> None:
    store.add("demo", {"hello": mod_src("hello")})
    other = tmp_path / "other"
    other.mkdir()
    (other / "index.json").write_text(json.dumps({"schema": 1, "packs": [{"name": "demo"}]}))
    store_add("other", str(other))
    with pytest.raises(StoreError, match="pytacheck/demo, other/demo"):
        find_entry("demo")
    assert find_entry("demo", store="other")[0] == "other"


def test_new_packs_are_found_despite_the_cache(store, ms) -> None:
    store.add("first", {"one": mod_src("one")})
    store_index("pytacheck")
    store.add("second", {"two": mod_src("two")})
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert find_entry("second")[1]["name"] == "second"
