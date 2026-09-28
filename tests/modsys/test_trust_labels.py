"""Trust labels: a pin names a store, but only the store index makes a pack trust=store."""

from __future__ import annotations

import json

import pytest
import respx

from pytacheck.packs.install import pack_install
from pytacheck.packs.registry import get_pack
from pytacheck.packs.stores import find_entry, store_update
from pytacheck.packs.tree import INSTALL_RECORD, tree_sha256
from tests.modsys.helpers import REV_A, mod_src
from tests.modsys.storekit import (
    INDEX_URL,
    REV_C,
    REV_D,
    STORE_URL,
    FakeStore,
    codeload,
    dir_files,
    tarball,
)

SOURCE = {"github": "example/store", "subdir": "packs/demo"}


@pytest.fixture
def store(ms, monkeypatch):
    monkeypatch.setenv("PYTACHECK_STORE_URL", STORE_URL)
    with respx.mock(assert_all_called=False) as router:
        yield FakeStore(router, ms)


def _record(ms, rev: str) -> dict:
    return json.loads((ms.data / "packs" / "demo" / rev[:12] / INSTALL_RECORD).read_text())


def _listed_tree(ms) -> str:
    """The tree hash of the files the fake store serves for REV_C."""
    return tree_sha256(ms.root / "src" / REV_C[:8] / "demo")


def test_sync_labels_a_listed_pin_as_store(store, ms) -> None:
    entry = store.add("demo", {"hello": mod_src("hello")}, reviewed="2026-09-01")
    ms.pin(
        "demo",
        {"source": SOURCE, "rev": REV_C, "tree_sha256": entry["tree_sha256"], "store": "pytacheck"},
    )
    (pack,) = pack_install(yes=True)
    assert (pack.trust, pack.store, pack.reviewed) == ("store", "pytacheck", "2026-09-01")
    assert get_pack("demo").trust == "store"


def test_sync_labels_a_spoofed_store_pin_for_an_unlisted_rev_as_unlisted(store, ms, capsys) -> None:
    store.add("demo", {"hello": mod_src("hello")}, reviewed="2026-09-01")
    other = ms.pack(ms.root / "other", "demo", {"hello": mod_src("hello", "other")})
    store.router.get(codeload("example/store", REV_D)).respond(
        content=tarball(dir_files(other, "packs/demo/"), top="store-d")
    )
    ms.pin(
        "demo",
        {
            "source": SOURCE,
            "rev": REV_D,
            "tree_sha256": tree_sha256(other),
            "store": "pytacheck",
            "reviewed": "2026-09-01",
        },
    )
    (pack,) = pack_install(yes=True)
    assert (pack.trust, pack.store, pack.reviewed) == ("unlisted", None, None)
    assert _record(ms, REV_D)["store"] is None
    card = capsys.readouterr().err
    assert "not listed in any store" in card and "matches the pin" in card
    assert "matches the store index" not in card
    assert get_pack("demo").trust == "unlisted"


def test_sync_labels_a_spoofed_store_pin_with_another_tree_as_unlisted(store, ms) -> None:
    # the store lists this rev, but with another tree hash than the files the pin holds
    store.add("demo", {"hello": mod_src("hello")}, tamper={"tree_sha256": "0" * 64})
    ms.pin(
        "demo",
        {"source": SOURCE, "rev": REV_C, "tree_sha256": _listed_tree(ms), "store": "pytacheck"},
    )
    (pack,) = pack_install(yes=True)
    assert (pack.trust, pack.store) == ("unlisted", None)
    assert get_pack("demo").trust == "unlisted"


def test_sync_labels_a_spoofed_store_pin_with_another_source_as_unlisted(store, ms) -> None:
    # the store lists this rev and tree, but the pin fetches it from a fork
    entry = store.add("demo", {"hello": mod_src("hello")})
    store.router.get(codeload("evil/fork", REV_C)).respond(
        content=tarball(store.trees[REV_C], top="fork-c")
    )
    ms.pin(
        "demo",
        {
            "source": {"github": "evil/fork", "subdir": "packs/demo"},
            "rev": REV_C,
            "tree_sha256": entry["tree_sha256"],
            "store": "pytacheck",
        },
    )
    (pack,) = pack_install(yes=True)
    assert (pack.trust, pack.store) == ("unlisted", None)
    assert _record(ms, REV_C)["store"] is None


def test_sync_labels_a_store_pin_as_unlisted_when_the_index_cannot_be_read(
    store, ms, capsys
) -> None:
    entry = store.add("demo", {"hello": mod_src("hello")})
    store.router.get(INDEX_URL, name="index").respond(404)
    ms.pin(
        "demo",
        {"source": SOURCE, "rev": REV_C, "tree_sha256": entry["tree_sha256"], "store": "pytacheck"},
    )
    (pack,) = pack_install(yes=True)
    assert (pack.trust, pack.store) == ("unlisted", None)
    card = capsys.readouterr().err
    assert "could not confirm the pin" in card
    assert "does not list this revision" not in card


def test_sync_looks_again_when_the_cached_index_predates_the_listing(store, ms) -> None:
    store.add("demo", {"hello": mod_src("hello")})
    find_entry("demo")  # caches an index that lists only REV_C
    entry = store.add("demo", {"hello": mod_src("hello", "two")}, rev=REV_D)
    ms.pin(
        "demo",
        {"source": SOURCE, "rev": REV_D, "tree_sha256": entry["tree_sha256"], "store": "pytacheck"},
    )
    (pack,) = pack_install(yes=True)
    assert (pack.trust, pack.store) == ("store", "pytacheck")


def test_sync_labels_a_pin_as_store_once_the_index_can_be_read(store, ms) -> None:
    entry = store.add("demo", {"hello": mod_src("hello")})
    store.router.get(INDEX_URL, name="index").respond(404)
    ms.pin(
        "demo",
        {"source": SOURCE, "rev": REV_C, "tree_sha256": entry["tree_sha256"], "store": "pytacheck"},
    )
    assert pack_install(yes=True)[0].trust == "unlisted"
    store.publish()
    (pack,) = pack_install(yes=True)
    assert (pack.trust, pack.store) == ("store", "pytacheck")
    assert _record(ms, REV_C)["store"] == "pytacheck"


def test_sync_leaves_a_spoofed_pin_unlisted_on_later_syncs(store, ms) -> None:
    store.add("demo", {"hello": mod_src("hello")}, tamper={"tree_sha256": "0" * 64})
    ms.pin(
        "demo",
        {"source": SOURCE, "rev": REV_C, "tree_sha256": _listed_tree(ms), "store": "pytacheck"},
    )
    assert pack_install(yes=True)[0].trust == "unlisted"
    assert pack_install(yes=True) == []
    assert get_pack("demo").trust == "unlisted"


def test_the_label_comes_from_the_install_record_not_the_pin(ms) -> None:
    ms.install("demo", {"hello": mod_src("hello")}, rev=REV_A, store=None, pin=False)
    ms.pin("demo", {"rev": REV_A, "store": "pytacheck", "reviewed": "2026-09-01"})
    pack = get_pack("demo")
    assert (pack.trust, pack.store, pack.reviewed) == ("unlisted", None, None)


def test_a_listed_install_record_gives_trust_store_even_for_a_bare_pin(ms) -> None:
    ms.install("demo", {"hello": mod_src("hello")}, rev=REV_A, reviewed="2026-09-01", pin=False)
    ms.pin("demo", {"rev": REV_A})
    pack = get_pack("demo")
    assert (pack.trust, pack.store, pack.reviewed) == ("store", "pytacheck", "2026-09-01")


def test_files_first_installed_as_unlisted_become_store_once_listed(store, ms) -> None:
    store.add("demo", {"hello": mod_src("hello")}, tamper={"tree_sha256": "0" * 64})
    tree = _listed_tree(ms)
    ms.pin("demo", {"source": SOURCE, "rev": REV_C, "tree_sha256": tree, "store": "pytacheck"})
    assert pack_install(yes=True)[0].trust == "unlisted"
    store.entries["demo"]["tree_sha256"] = tree  # the store now lists these files
    store.publish()
    store_update()
    pack = pack_install("demo", yes=True)
    assert (pack.trust, pack.store) == ("store", "pytacheck")
    assert _record(ms, REV_C)["store"] == "pytacheck"
