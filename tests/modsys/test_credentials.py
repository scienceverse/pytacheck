"""Credentials in URLs: refused wherever they would be written, never shown, never recorded.

A URL may carry a credential itself: a password or user info
(``https://x-access-token:TOKEN@github.com/...``), a secret query parameter
(``?private_token=``, ``?access_token=``, ``?token=``). Such URLs are refused
when a store is added or a pack source is installed (from a URL, a pin, a
store index, an update or a run record), and one read from an older config is
shown redacted and recorded without it.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any

import pytest

from metacheck.cli import main
from metacheck.packs import auth, install, ui
from metacheck.packs.fetch import describe_source, tarball_url
from metacheck.packs.install import _Candidate, _install, pack_install, pack_update
from metacheck.packs.manifest import PackError
from metacheck.packs.stores import (
    StoreError,
    check_store_url,
    index_location,
    store_add,
    store_list,
    store_update,
)
from metacheck.packs.tree import INSTALL_RECORD
from tests.modsys.helpers import REV_A, mod_src

# fixed, so parametrized test ids are the same in every xdist worker
SENTINEL = "SENTINEL7f3c9a1e5b2d8046aa51"
LEGACY = f"https://x-access-token:{SENTINEL}@github.com/o/r.git"
PLAIN = "https://github.com/o/r.git"


def _files_with(root: Path, secret: str) -> list[str]:
    """Paths (never contents) of the files under *root* that contain *secret*."""
    return [str(p) for p in root.rglob("*") if p.is_file() and secret.encode() in p.read_bytes()]


def _texts_with(secret: str, **texts: str) -> list[str]:
    """Labels (never contents) of the texts that contain *secret*."""
    return [label for label, text in texts.items() if secret in text]


# --- what counts as a credential -------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "secret"),
    [
        (f"https://x-access-token:{SENTINEL}@github.com/o/r", True),
        (f"https://{SENTINEL}@github.com/o/r", True),  # a token as the user name
        (f"http://u:{SENTINEL}@127.0.0.1:8765/index.json", True),
        (f"ssh://git:{SENTINEL}@github.com/o/r", True),
        (f"u:{SENTINEL}@github.com:o/r", True),  # scp-like with a password
        (f"https://gitlab.com/g/store?private_token={SENTINEL}", True),
        (f"https://example.org/r.git?access_token={SENTINEL}", True),
        (f"https://example.org/index.json?a=1&token={SENTINEL}", True),
        (f"https://example.org/index.json?X-Amz-Security-Token={SENTINEL}", True),
        (f"https://example.org/index.json#access_token={SENTINEL}", True),
        (f"git+https://u:{SENTINEL}@example.org/r.git", True),
        ("https://github.com/o/r", False),
        ("ssh://git@github.com/o/r", False),  # a user name alone is not a secret
        ("git@github.com:o/r.git", False),
        ("https://example.org/index.json?ref=main", False),
        ("https://example.org/index.json?token=", False),  # empty
        ("file:///srv/stores/my@store", False),
        ("/srv/stores/my@store", False),
    ],
)
def test_what_counts_as_a_credential(url: str, secret: bool) -> None:
    assert auth.has_credentials(url) is secret
    assert SENTINEL not in auth.redact(url)
    assert SENTINEL not in auth.strip_credentials(url)
    assert not auth.has_credentials(auth.strip_credentials(url))


def test_a_token_value_from_the_environment_counts_anywhere(monkeypatch) -> None:
    monkeypatch.setenv("GH_TOKEN", SENTINEL)
    url = f"https://example.org/{SENTINEL}/index.json"
    assert auth.has_credentials(url)
    assert SENTINEL not in auth.strip_credentials(url)


def test_strip_credentials_keeps_the_rest_of_the_url() -> None:
    assert auth.strip_credentials(LEGACY) == PLAIN
    assert auth.strip_credentials(f"ssh://git:{SENTINEL}@h/o/r") == "ssh://git@h/o/r"
    assert auth.strip_credentials(f"u:{SENTINEL}@h:o/r") == "u@h:o/r"
    assert (
        auth.strip_credentials(f"https://h/i.json?a=1&private_token={SENTINEL}&b=2")
        == "https://h/i.json?a=1&b=2"
    )
    source = {"git": LEGACY, "subdir": "packs/x", "rev": REV_A}
    assert auth.clean_source(source) == {"git": PLAIN, "subdir": "packs/x", "rev": REV_A}


# --- pins, install records, run records (finding: legacy pins) --------------------------------


def _legacy_install(ms: Any) -> Path:
    """An installed pack whose pin and install record carry a token (as before the check)."""
    ms.install("legacy", {"hello": mod_src("hello", "hi")}, store=None)
    root = ms.data / "packs" / "legacy" / REV_A[:12]
    record = json.loads((root / INSTALL_RECORD).read_text())
    record["source"] = {"git": LEGACY}
    (root / INSTALL_RECORD).write_text(json.dumps(record))
    ms.pin(
        "legacy", {"source": {"git": LEGACY}, "rev": REV_A, "tree_sha256": record["tree_sha256"]}
    )
    return root


def test_a_legacy_pin_with_a_token_is_not_installed(ms, capsys, monkeypatch) -> None:
    ms.pin("legacy", {"source": {"git": LEGACY}, "rev": "c" * 40})
    fetched: list[Any] = []
    monkeypatch.setattr(install, "fetch_source", lambda *a, **k: fetched.append(a) or 1)
    assert main(["pack", "install", "--yes"]) != 0
    out = capsys.readouterr()
    assert "contains credentials" in out.err
    assert _texts_with(SENTINEL, stdout=out.out, stderr=out.err) == []
    assert fetched == []  # refused before anything is downloaded
    ms.data.mkdir(parents=True, exist_ok=True)
    assert _files_with(ms.data, SENTINEL) == []
    assert list(ms.data.rglob(INSTALL_RECORD)) == []


def test_install_update_and_rerun_refuse_sources_with_credentials(ms, monkeypatch) -> None:
    fetched: list[Any] = []
    resolved: list[Any] = []
    monkeypatch.setattr(install, "fetch_source", lambda *a, **k: fetched.append(a) or 1)
    monkeypatch.setattr(install, "resolve_rev", lambda *a, **k: resolved.append(a) or "d" * 40)
    for source in (
        {"git": LEGACY},
        {"git": f"https://example.org/r.git?access_token={SENTINEL}"},
        {"git": f"ssh://git:{SENTINEL}@example.org/r.git"},
    ):
        cand = _Candidate(name="legacy", source=source, rev="c" * 40)
        with pytest.raises(PackError, match="contains credentials") as info:
            _install(cand, scope="user", yes=True)  # a pin, a store entry or a run record
        assert SENTINEL not in str(info.value)
    ms.pin("legacy", {"source": {"git": LEGACY}, "rev": "c" * 40, "tree_sha256": "0" * 64})
    with pytest.raises(PackError, match="contains credentials") as info:
        pack_update("legacy", yes=True)
    assert SENTINEL not in str(info.value)
    assert fetched == [] and resolved == []  # neither git nor the API saw it
    assert "legacy" in json.loads(ms.config_file.read_text())["packs"]  # the pin is left alone


def test_a_legacy_install_is_recorded_and_shown_without_the_token(ms, capsys) -> None:
    import metacheck as pc
    from metacheck.provenance import RunRecord, run_modules

    root = _legacy_install(ms)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert main(["pack", "list"]) == 0
        assert main(["pack", "show", "legacy", "--json"]) == 0
        chain = run_modules(pc.test_paper("A sentence."), ["legacy::hello"], record="run.json")
    assert chain.last.summary_text == "hi"
    assert RunRecord.read(ms.work / "run.json").modules[0]["source"] == {
        "git": PLAIN,
        "rev": REV_A,
    }
    out = capsys.readouterr()
    warned = "\n".join(str(w.message) for w in caught)
    assert "contains credentials" in out.err + warned  # says so, without the secret
    assert _texts_with(SENTINEL, stdout=out.out, stderr=out.err, warnings=warned) == []
    assert _files_with(ms.work, SENTINEL) == []  # run.json
    assert _files_with(root, SENTINEL) == [str(root / INSTALL_RECORD)]  # left as it was


def test_a_run_record_source_with_a_token_is_not_installed(ms, monkeypatch) -> None:
    import metacheck as pc
    from metacheck.provenance import rerun, run_modules

    ms.install("clean", {"hello": mod_src("hello", "hi")}, store=None)
    run_modules(pc.test_paper("A sentence."), ["clean::hello"], record="run.json")
    data = json.loads((ms.work / "run.json").read_text())
    data["modules"][0]["source"] = {"git": LEGACY, "rev": "e" * 40}
    fetched: list[Any] = []
    monkeypatch.setattr(install, "fetch_source", lambda *a, **k: fetched.append(a) or 1)
    with pytest.raises(PackError, match="contains credentials") as info:
        rerun(data, pc.test_paper("A sentence."), install=True, yes=True)
    assert SENTINEL not in str(info.value) and fetched == []
    assert not (ms.data / "packs" / "clean" / ("e" * 12)).exists()


def test_the_consent_card_redacts_a_source_url() -> None:
    assert SENTINEL not in describe_source({"git": LEGACY})
    assert describe_source({"git": LEGACY}) == "git https://***@github.com/o/r.git"
    with pytest.raises(PackError) as info:
        tarball_url({"github": f"o/r?access_token={SENTINEL}"}, "c" * 40)
    assert SENTINEL not in str(info.value)


# --- URLs typed at `pack install` (query tokens) ---------------------------------------------


@pytest.mark.parametrize(
    "ref",
    [
        f"https://github.com/o/r?access_token={SENTINEL}",
        f"https://github.com/o/r?token={SENTINEL}",
        f"https://gitlab.com/g/r?private_token={SENTINEL}",
        f"git+https://example.test/r.git?access_token={SENTINEL}",
        f"https://x-access-token:{SENTINEL}@github.com/o/r@v1",
        f"ssh://git:{SENTINEL}@example.test/r.git",
    ],
)
def test_pack_install_refuses_urls_with_credentials(ms, capsys, ref: str) -> None:
    with pytest.raises(PackError, match="contains credentials") as info:
        pack_install(ref, yes=True)
    assert SENTINEL not in str(info.value)
    assert main(["pack", "install", ref, "--yes"]) == 1
    out = capsys.readouterr()
    assert _texts_with(SENTINEL, stdout=out.out, stderr=out.err) == []
    assert not ms.config_file.exists()


def test_the_help_for_credentials_fits_the_host(ms) -> None:
    with pytest.raises(PackError) as info:
        pack_install(f"https://github.com/o/r?access_token={SENTINEL}", yes=True)
    assert "PYTACHECK_GITHUB_TOKEN" in str(info.value)
    with pytest.raises(PackError) as info:
        pack_install(f"git+https://u:{SENTINEL}@git.example.org/r.git", yes=True)
    assert "PYTACHECK_GITHUB_TOKEN" not in str(info.value)
    assert "git credentials" in str(info.value)


# --- store URLs ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        f"https://gitlab.com/g/store?private_token={SENTINEL}",
        f"https://gitlab.com/g/r/-/raw/main/index.json?private_token={SENTINEL}",
        f"https://example.org/index.json?token={SENTINEL}",
        f"https://example.org/store?access_token={SENTINEL}",
        f"https://user:{SENTINEL}@github.com/o/r",
        f"https://{SENTINEL}@github.com/o/r",
        f"ssh://git:{SENTINEL}@github.com/o/r",
    ],
)
def test_store_add_refuses_urls_with_credentials(ms, capsys, url: str) -> None:
    with pytest.raises(StoreError, match="contains credentials") as info:
        store_add("mine", url)
    assert SENTINEL not in str(info.value)
    with pytest.raises(StoreError):
        check_store_url(url)
    assert main(["store", "add", "mine", url, "--yes"]) == 1
    out = capsys.readouterr()
    assert _texts_with(SENTINEL, stdout=out.out, stderr=out.err) == []
    assert not ms.config_file.exists()


def test_store_urls_with_queries_are_located_correctly() -> None:
    assert index_location("https://example.org/store?ref=main") == (
        "http",
        "https://example.org/store/index.json?ref=main",
    )
    assert index_location("https://example.org/i.json?ref=main") == (
        "http",
        "https://example.org/i.json?ref=main",
    )
    with pytest.raises(StoreError, match="not supported"):
        index_location("ftp://example.org/store")


def test_store_add_refuses_credentials_before_asking(ms, capsys, monkeypatch) -> None:
    url = f"https://x-access-token:{SENTINEL}@github.com/o/r"
    assert main(["store", "add", "sa", url]) == 1  # no terminal
    out = capsys.readouterr()
    assert "Add the store" not in out.err and "contains credentials" in out.err
    assert _texts_with(SENTINEL, stdout=out.out, stderr=out.err) == []
    asked: list[str] = []

    def ask(question: str, **_: Any) -> bool:
        asked.append(str(question))
        return True

    import rich.prompt

    monkeypatch.setattr(ui, "interactive", lambda: True)
    monkeypatch.setattr(rich.prompt.Confirm, "ask", staticmethod(ask))
    assert main(["store", "add", "sb", url]) == 1  # refused without asking
    out = capsys.readouterr()
    assert asked == []
    assert _texts_with(SENTINEL, stdout=out.out, stderr=out.err) == []
    assert not ms.config_file.exists()


@pytest.mark.parametrize(
    "url",
    [
        f"https://x-access-token:{SENTINEL}@github.com/o/r",
        f"https://gitlab.com/g/r/-/raw/main/index.json?private_token={SENTINEL}",
        f"ssh://git:{SENTINEL}@github.com/o/r",
    ],
)
@pytest.mark.parametrize("where", ["env", "config"])
def test_store_list_and_update_never_show_a_secret(ms, capsys, monkeypatch, url, where) -> None:
    if where == "env":
        monkeypatch.setenv("PYTACHECK_STORE_URL", url)
    else:  # written by hand, or before `store add` refused it
        ms.config({"stores": {"pytacheck": None, "lab": url}})
    listed = store_list()
    updated = store_update()
    frames = {"list": listed.to_csv(), "update": updated.to_csv()}
    assert _texts_with(SENTINEL, **frames) == []
    assert (updated["status"] != "ok").any()
    assert main(["store", "list"]) == 0
    main(["store", "update"])
    for name in ("pytacheck", "lab"):
        if name in list(listed["name"]):
            main(["store", "update", name])
    main(["pack", "search", "x"])
    out = capsys.readouterr()
    assert _texts_with(SENTINEL, stdout=out.out, stderr=out.err) == []
    assert "***" in out.out
