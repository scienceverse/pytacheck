"""Trust labels: a pin names a store, but only the store index makes a pack trust=store."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from pytacheck.packs.install import pack_install, pack_show
from pytacheck.packs.manifest import PackError
from pytacheck.packs.registry import get_pack, load_module
from pytacheck.packs.stores import find_entry, store_update
from pytacheck.packs.tree import INSTALL_RECORD, tree_sha256
from pytacheck.provenance import module_provenance
from tests.modsys.helpers import REV_A, mod_src
from tests.modsys.storekit import (
    INDEX_URL,
    REV_C,
    REV_D,
    STORE_URL,
    FakeStore,
    codeload,
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
    # the files and their hash are the listed ones; only the commit differs
    store.add("demo", {"hello": mod_src("hello")}, reviewed="2026-09-01")
    store.router.get(codeload("example/store", REV_D)).respond(
        content=tarball(store.trees[REV_C], top="store-d")
    )
    ms.pin(
        "demo",
        {
            "source": SOURCE,
            "rev": REV_D,
            "tree_sha256": _listed_tree(ms),
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
    assert "does not list this revision" in card
    assert get_pack("demo").trust == "unlisted"


def test_sync_labels_a_store_pin_as_unlisted_when_the_index_has_no_tree_hash(store, ms) -> None:
    store.add("demo", {"hello": mod_src("hello")})
    del store.entries["demo"]["tree_sha256"]
    store.publish()
    ms.pin(
        "demo",
        {"source": SOURCE, "rev": REV_C, "tree_sha256": _listed_tree(ms), "store": "pytacheck"},
    )
    (pack,) = pack_install(yes=True)
    assert (pack.trust, pack.store) == ("unlisted", None)


def test_sync_says_the_store_does_not_list_a_pack_it_has_never_heard_of(store, ms, capsys) -> None:
    entry = store.add("demo", {"hello": mod_src("hello")})
    del store.entries["demo"]
    store.add("other", {"hello": mod_src("hello")})
    ms.pin(
        "demo",
        {"source": SOURCE, "rev": REV_C, "tree_sha256": entry["tree_sha256"], "store": "pytacheck"},
    )
    (pack,) = pack_install(yes=True)
    assert (pack.trust, pack.store) == ("unlisted", None)
    card = capsys.readouterr().err
    assert "does not list this revision" in card
    assert "could not confirm the pin" not in card


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
    assert "`pytacheck pack install` when" in card


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
    ms.install(
        "demo", {"hello": mod_src("hello")}, rev=REV_A, store=None, reviewed="2026-09-01", pin=False
    )
    ms.pin("demo", {"rev": REV_A, "store": "pytacheck", "reviewed": "2026-09-01"})
    pack = get_pack("demo")
    assert (pack.trust, pack.store, pack.reviewed) == ("unlisted", None, None)


def test_a_listed_install_record_gives_trust_store_even_for_a_bare_pin(ms) -> None:
    ms.install("demo", {"hello": mod_src("hello")}, rev=REV_A, reviewed="2026-09-01", pin=False)
    ms.pin("demo", {"rev": REV_A})
    pack = get_pack("demo")
    assert (pack.trust, pack.store, pack.reviewed) == ("store", "pytacheck", "2026-09-01")


FORK = {"github": "evil/fork", "subdir": "packs/demo"}


def test_a_store_pack_reports_the_recorded_source_not_the_pins(ms) -> None:
    # the pin names a fork at the listed commit, over files the store index vouched for
    ms.install("demo", {"hello": mod_src("hello")}, rev=REV_A, reviewed="2026-09-01", pin=False)
    tree = _record(ms, REV_A)["tree_sha256"]
    ms.pin("demo", {"source": FORK, "rev": REV_A, "tree_sha256": tree, "store": "pytacheck"})
    pack = get_pack("demo")
    assert pack.trust == "store"
    assert pack.source == {"github": "someone/demo", "rev": REV_A}
    assert pack_show("demo")["source"] == {"github": "someone/demo", "rev": REV_A}
    prov = module_provenance(load_module(pack, "hello"))
    assert (prov["trust"], prov["source"]) == ("store", {"github": "someone/demo", "rev": REV_A})


def test_a_store_pack_still_has_its_rev_and_tree_checked_against_the_pin(ms) -> None:
    ms.install("demo", {"hello": mod_src("hello")}, rev=REV_A, pin=False)
    ms.pin("demo", {"source": FORK, "rev": REV_A, "tree_sha256": "0" * 64})
    with pytest.raises(PackError, match="does not match its pin"):
        get_pack("demo")


def test_an_unlisted_pack_reports_the_pins_source_first(ms) -> None:
    ms.install("demo", {"hello": mod_src("hello")}, rev=REV_A, store=None, pin=False)
    tree = _record(ms, REV_A)["tree_sha256"]
    ms.pin("demo", {"source": FORK, "rev": REV_A, "tree_sha256": tree})
    pack = get_pack("demo")
    assert pack.trust == "unlisted"
    assert pack.source == {**FORK, "rev": REV_A}


def test_an_unlisted_pack_without_a_pinned_source_reports_the_recorded_one(ms) -> None:
    ms.install("demo", {"hello": mod_src("hello")}, rev=REV_A, store=None, pin=False)
    ms.pin("demo", {"rev": REV_A})
    assert get_pack("demo").source == {"github": "someone/demo", "rev": REV_A}


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


def test_sync_asks_a_store_for_a_fresh_index_once_however_many_pins_it_checks(store, ms) -> None:
    pins = {}
    for name in ("demo", "two", "three"):
        entry = store.add(name, {"hello": mod_src("hello")})
        pins[name] = {
            "source": {"github": "example/store", "subdir": f"packs/{name}"},
            "rev": REV_C,
            "tree_sha256": entry["tree_sha256"],
            "store": "pytacheck",
        }
    for name in pins:  # the store has moved on to another commit
        store.add(name, {"hello": mod_src("hello", "new")}, rev=REV_D)
    for name, pin in pins.items():
        ms.pin(name, pin)
    assert len(pack_install(yes=True)) == 3
    assert all(get_pack(name).trust == "unlisted" for name in pins)
    assert store.router["index"].call_count == 2  # the cached one, then one fresh


def test_sync_keeps_a_listed_pack_unlisted_when_the_reinstall_is_declined(
    store, ms, capsys
) -> None:
    store.add("demo", {"hello": mod_src("hello")}, tamper={"tree_sha256": "0" * 64})
    tree = _listed_tree(ms)
    ms.pin("demo", {"source": SOURCE, "rev": REV_C, "tree_sha256": tree, "store": "pytacheck"})
    assert pack_install(yes=True)[0].trust == "unlisted"
    store.entries["demo"]["tree_sha256"] = tree  # the store now lists these files
    store.publish()
    capsys.readouterr()
    assert pack_install(yes=False) == []  # no terminal to ask on: not an error
    captured = capsys.readouterr()
    assert "stays unlisted" in captured.err
    assert "Every pinned pack is installed" not in captured.out + captured.err
    assert _record(ms, REV_C)["store"] is None
    assert get_pack("demo").trust == "unlisted"


def test_sync_says_why_it_installs_unlisted_files_again(store, ms, capsys) -> None:
    store.add("demo", {"hello": mod_src("hello")}, tamper={"tree_sha256": "0" * 64})
    tree = _listed_tree(ms)
    ms.pin("demo", {"source": SOURCE, "rev": REV_C, "tree_sha256": tree, "store": "pytacheck"})
    pack_install(yes=True)
    store.entries["demo"]["tree_sha256"] = tree
    store.publish()
    capsys.readouterr()
    pack_install(yes=True)
    err = capsys.readouterr().err
    assert "'demo' is installed as unlisted; the store 'pytacheck' now lists it" in err


def test_sync_keeps_a_listed_pack_unlisted_when_the_reinstall_fails(store, ms, capsys) -> None:
    entry = store.add("demo", {"hello": mod_src("hello")})
    store.router.get(INDEX_URL, name="index").respond(404)
    ms.pin(
        "demo",
        {"source": SOURCE, "rev": REV_C, "tree_sha256": entry["tree_sha256"], "store": "pytacheck"},
    )
    assert pack_install(yes=True)[0].trust == "unlisted"
    store.publish()
    store.router["tar-" + REV_C].mock(side_effect=httpx.ConnectError("offline"))
    capsys.readouterr()
    assert pack_install(yes=True) == []  # the files are installed and usable: not an error
    captured = capsys.readouterr()
    assert "stays unlisted" in captured.err
    assert "offline" in captured.err
    assert "Every pinned pack is installed" not in captured.out + captured.err
    assert get_pack("demo").trust == "unlisted"


def test_sync_of_unlisted_files_with_a_pin_that_has_no_source_is_not_an_error(ms) -> None:
    ms.install("demo", {"hello": mod_src("hello")}, rev=REV_A, store=None, pin=False)
    ms.pin("demo", {"rev": REV_A, "store": "pytacheck"})
    assert pack_install(yes=True) == []
    assert get_pack("demo").trust == "unlisted"


def test_sync_looks_for_a_missing_pack_name_in_a_fresh_index_once_per_store(store, ms) -> None:
    for name in ("demo", "two", "three"):
        entry = store.add(name, {"hello": mod_src("hello")})
        del store.entries[name]  # the store does not list these packs
        ms.pin(
            name,
            {
                "source": {"github": "example/store", "subdir": f"packs/{name}"},
                "rev": REV_C,
                "tree_sha256": entry["tree_sha256"],
                "store": "pytacheck",
            },
        )
    store.add("other", {"hello": mod_src("hello")})
    assert len(pack_install(yes=True)) == 3
    assert store.router["index"].call_count == 2  # the cached one, then one fresh


def test_sync_of_a_pin_without_a_source_does_not_say_everything_is_installed(
    store, ms, capsys
) -> None:
    ms.pin("demo", {"rev": REV_C, "tree_sha256": "a" * 64, "store": "pytacheck"})
    with pytest.raises(PackError, match="has no source to install from"):
        pack_install(yes=True)
    captured = capsys.readouterr()
    assert "Every pinned pack is installed" not in captured.out + captured.err
