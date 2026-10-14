"""Listing an OSF component tree in few requests (metacheck d52b3fd).

``osf_info(recursive = TRUE)`` lists a project's whole component tree, with
every component's storage providers, through
``/nodes/?filter[root]=<project>&embed=files``, and a GitHub repository linked
to a node through GitHub itself; each shortcut falls back to the
node-by-node listing when it cannot be used. A small fake OSF answers here.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pandas as pd
import pytest
import respx

from metacheck.archives.osf import osf_info
from metacheck.archives.osf_all import _osf_walk_nodes
from metacheck.utils import local_options

API = "https://api.osf.io/v2"

#: proj1 -> comp1 -> comp2, and proj1 -> comp3; comp1 also links GitHub
TREE = {"proj1": None, "comp1": "proj1", "comp2": "comp1", "comp3": "proj1"}
TITLES = {"proj1": "Project", "comp1": "Analysis", "comp2": "Raw", "comp3": "Docs"}


def _node(nid: str) -> dict[str, Any]:
    parent = TREE[nid]
    return {
        "id": nid,
        "type": "nodes",
        "attributes": {"title": TITLES[nid], "category": "project", "public": True},
        "links": {"self": f"{API}/nodes/{nid}/"},
        "relationships": {
            "children": {"links": {"related": {"href": f"{API}/nodes/{nid}/children/"}}},
            "files": {"links": {"related": {"href": f"{API}/nodes/{nid}/files/"}}},
            "parent": {"data": None if parent is None else {"id": parent, "type": "nodes"}},
            "root": {"data": {"id": "proj1", "type": "nodes"}},
        },
    }


def _provider(nid: str, name: str) -> dict[str, Any]:
    return {
        "id": f"{nid}:{name}",
        "type": "files",
        "attributes": {"name": name, "kind": "folder", "path": "/", "provider": name},
        "relationships": {
            "files": {"links": {"related": {"href": f"{API}/nodes/{nid}/files/{name}/"}}},
            "root_folder": {"data": {"id": f"root-{nid}-{name}", "type": "files"}},
            "target": {"data": {"id": nid, "type": "nodes"}},
        },
    }


def _file(nid: str, name: str, provider: str = "osfstorage") -> dict[str, Any]:
    fid = f"f{nid}{name.replace('.', '')}"
    return {
        "id": fid,
        "type": "files",
        "attributes": {
            "name": name,
            "kind": "file",
            "path": f"/{fid}",
            "materialized_path": f"/{name}",
            "provider": provider,
            "size": 10,
            "extra": {"downloads": 0},
        },
        "links": {"download": f"https://osf.io/download/{fid}/"},
        "relationships": {
            "parent_folder": {"data": {"id": f"root-{nid}-{provider}", "type": "files"}},
            "target": {"data": {"id": nid, "type": "nodes"}},
        },
    }


def _providers(nid: str) -> list[dict[str, Any]]:
    names = ["osfstorage", "github"] if nid == "comp1" else ["osfstorage"]
    return [_provider(nid, p) for p in names]


def _listing(data: list[dict[str, Any]]) -> dict[str, Any]:
    return {"data": data, "links": {"next": None, "meta": {"total": len(data), "per_page": 100}}}


class FakeOsf:
    """The OSF API (and GitHub's) for :data:`TREE`; *tree* switches the tree listing on."""

    def __init__(self, tree: bool = True, addon: str | None = "owner/repo:", embed_short: str = ""):
        self.tree = tree
        self.addon = addon
        self.embed_short = embed_short  # a node whose embed holds only part of its providers

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        path = url.path.removeprefix("/v2")
        if url.host == "osf.io" and path.startswith("/download/"):
            return httpx.Response(200, content=b"0123456789")
        if url.host == "raw.githubusercontent.com":
            return httpx.Response(200, content=f"github {path}".encode())
        if url.host == "api.github.com":
            if "/git/trees/" in path:
                tree = [
                    {"path": "README.md", "type": "blob", "size": 12},
                    {"path": "R", "type": "tree"},
                    {"path": "R/a.R", "type": "blob", "size": 34},
                ]
                return httpx.Response(200, json={"truncated": False, "tree": tree})
            if path == "/repos/owner/repo":
                return httpx.Response(200, json={"default_branch": "main"})
            return httpx.Response(404)
        parts = [p for p in path.split("/") if p]
        if parts[:1] == ["guids"] and parts[1] in TREE:
            return httpx.Response(200, json={"data": _node(parts[1])})
        if parts == ["nodes"] and url.params.get("filter[root]") == "proj1":
            if not self.tree:
                return httpx.Response(400, json={"errors": [{"detail": "no"}]})
            assert url.params.get("embed") == "files"
            data = []
            for nid in TREE:
                provs = _providers(nid)
                total = len(provs)
                if nid == self.embed_short:
                    provs = provs[:1]
                    total += 1
                rec = _node(nid)
                rec["embeds"] = {
                    "files": {"data": provs, "links": {"meta": {"total": total, "per_page": 10}}}
                }
                data.append(rec)
            return httpx.Response(200, json=_listing(data))
        if parts[:1] == ["nodes"] and parts[1] in TREE:
            nid, rest = parts[1], parts[2:]
            if rest == []:
                return httpx.Response(200, json={"data": _node(nid)})
            if rest == ["children"]:
                return httpx.Response(
                    200, json=_listing([_node(k) for k, p in TREE.items() if p == nid])
                )
            if rest == ["files"]:
                return httpx.Response(200, json=_listing(_providers(nid)))
            if rest == ["files", "osfstorage"]:
                return httpx.Response(200, json=_listing([_file(nid, f"{nid}.csv")]))
            if rest == ["files", "github"]:
                return httpx.Response(200, json=_listing([_file(nid, "README.md", "github")]))
            if rest == ["addons", "github"] and self.addon is not None:
                data = {"id": "github", "attributes": {"folder_id": self.addon}}
                return httpx.Response(200, json={"data": data})
        return httpx.Response(404, json={"errors": [{"detail": "Not found."}]})


@pytest.fixture
def fake() -> Iterator[tuple[FakeOsf, respx.MockRouter]]:
    server = FakeOsf()
    with respx.mock(assert_all_called=False) as router:
        router.route().mock(side_effect=server)
        yield server, router


def _paths(router: respx.MockRouter) -> list[str]:
    """Each request's host, path and query, without the page size every listing asks for."""
    out = []
    for c in router.calls:
        url = c.request.url
        query = "&".join(q for q in url.query.decode().split("&") if q != "page[size]=100")
        out.append(f"{url.host}{url.path}" + (f"?{query}" if query else ""))
    return out


def _rows(data: pd.DataFrame) -> set[tuple[Any, ...]]:
    cols = ["osf_id", "osf_type", "kind", "provider", "path", "project"]
    sub = data.reindex(columns=cols).astype(object)
    return {tuple(None if pd.isna(v) else v for v in row) for row in sub.itertuples(index=False)}


def test_tree_lists_components_and_providers_in_one_request(
    fake: tuple[FakeOsf, respx.MockRouter], filetype_available: bool
) -> None:
    server, router = fake
    server.addon = None  # GitHub through the OSF here
    data = osf_info("proj1", recursive=True)
    seen = _paths(router)
    # no /children/ and no per-node /files/ request
    assert not [p for p in seen if p.endswith("/children/") or p.endswith("/files/")]
    assert seen.count("api.osf.io/v2/nodes/?filter[root]=proj1&embed=files") == 1
    nodes = data.loc[data["osf_type"] == "nodes", "osf_id"].tolist()
    assert sorted(nodes) == ["comp1", "comp2", "comp3", "proj1"]
    files = data.loc[data["kind"] == "file", "name"].tolist()
    assert sorted(files) == ["README.md", "comp1.csv", "comp2.csv", "comp3.csv", "proj1.csv"]

    # the node-by-node walk finds the same rows
    server.tree = False
    router.reset()
    walked = osf_info("proj1", recursive=True)
    assert _rows(walked) == _rows(data)
    assert len(walked) == len(data)
    assert any(p.endswith("/children/") for p in _paths(router))


def test_tree_of_a_linked_component_keeps_what_is_below_it(
    fake: tuple[FakeOsf, respx.MockRouter], filetype_available: bool
) -> None:
    _, router = fake
    data = osf_info("comp1", recursive=True)
    nodes = data.loc[data["osf_type"] == "nodes", "osf_id"].tolist()
    assert sorted(nodes) == ["comp1", "comp2"]
    assert "proj1.csv" not in data["name"].tolist()
    assert "comp3.csv" not in data["name"].tolist()
    # filter[root] takes the project's id
    assert any("proj1" in p and "filter" in p for p in _paths(router))


def test_tree_lists_a_node_whose_embed_is_short_the_old_way(
    fake: tuple[FakeOsf, respx.MockRouter], filetype_available: bool
) -> None:
    server, router = fake
    server.embed_short = "comp3"
    data = osf_info("proj1", recursive=True)
    seen = _paths(router)
    assert [p for p in seen if p.endswith("/files/")] == ["api.osf.io/v2/nodes/comp3/files/"]
    assert "comp3.csv" in data["name"].tolist()


def test_failed_tree_listing_falls_back_and_is_still_cached(
    fake: tuple[FakeOsf, respx.MockRouter], filetype_available: bool
) -> None:
    server, router = fake
    server.tree = False
    with local_options({"metacheck.osf.cache": True}):
        from metacheck.archives.osf import osf_cache_clear

        osf_cache_clear()
        try:
            first = osf_info("proj1", recursive=True)
            n = router.calls.call_count
            again = osf_info("proj1", recursive=True)
        finally:
            osf_cache_clear()
    # the fallback listing was complete, so the session cache reuses it
    assert router.calls.call_count == n
    pd.testing.assert_frame_equal(first, again)


def test_linked_github_repository_is_listed_through_github(
    fake: tuple[FakeOsf, respx.MockRouter], filetype_available: bool
) -> None:
    _, router = fake
    data = osf_info("proj1", recursive=True)
    seen = _paths(router)
    assert "api.osf.io/v2/nodes/comp1/files/github/" not in seen
    assert "api.github.com/repos/owner/repo" in seen
    gh = data[data["provider"] == "github"]
    files = gh[gh["kind"] == "file"]
    assert files["path"].tolist() == ["/README.md", "/R/a.R"]
    assert files["size"].tolist() == [12, 34]
    assert files["download_url"].tolist() == [
        "https://raw.githubusercontent.com/owner/repo/main/README.md",
        "https://raw.githubusercontent.com/owner/repo/main/R/a.R",
    ]
    assert files["osf_id"].tolist() == ["comp1-github-1", "comp1-github-2"]
    assert set(files["project"]) == {"comp1"}
    folders = gh[(gh["kind"] == "folder") & (gh["path"] != "/")]
    assert folders[["osf_id", "name", "path"]].values.tolist() == [
        ["comp1-github-dir-1", "R", "/R/"]
    ]


@pytest.mark.parametrize("addon", ["owner/repo:dev", None, "not a repo:"])
def test_github_shortcut_falls_back_to_the_osf(
    fake: tuple[FakeOsf, respx.MockRouter], filetype_available: bool, addon: str | None
) -> None:
    # another branch than the default, no add-on settings, or no owner/repo
    server, router = fake
    server.addon = addon
    data = osf_info("proj1", recursive=True)
    assert "api.osf.io/v2/nodes/comp1/files/github/" in _paths(router)
    gh = data[(data["provider"] == "github") & (data["kind"] == "file")]
    assert gh["name"].tolist() == ["README.md"]
    assert not any(c.request.url.host == "api.github.com" for c in router.calls)


def test_github_shortcut_falls_back_when_github_does_not_list_it(
    fake: tuple[FakeOsf, respx.MockRouter], filetype_available: bool
) -> None:
    # a private repository the OSF shows through its owner's account
    server, router = fake
    server.addon = "owner/private:"
    data = osf_info("proj1", recursive=True)
    assert "api.osf.io/v2/nodes/comp1/files/github/" in _paths(router)
    assert (data["provider"] == "github").any()


def test_walk_nodes_uses_the_tree(fake: tuple[FakeOsf, respx.MockRouter]) -> None:
    server, router = fake
    nodes = _osf_walk_nodes("proj1")
    assert nodes["osf_id"].tolist()[0] == "proj1"
    assert sorted(nodes["osf_id"].tolist()) == ["comp1", "comp2", "comp3", "proj1"]
    assert dict(zip(nodes["osf_id"], nodes["title"], strict=True)) == TITLES
    seen = _paths(router)
    assert not [p for p in seen if p.endswith("/children/")]
    assert len(seen) == 2  # the root node, then the tree

    server.tree = False
    router.reset()
    walked = _osf_walk_nodes("proj1")
    assert walked["osf_id"].tolist()[0] == "proj1"
    assert sorted(walked["osf_id"].tolist()) == sorted(nodes["osf_id"].tolist())
    assert dict(zip(walked["osf_id"], walked["title"], strict=True)) == TITLES


def test_osf_file_download_takes_github_files_from_github(
    fake: tuple[FakeOsf, respx.MockRouter], filetype_available: bool, tmp_path: Any
) -> None:
    from metacheck.archives.osf import osf_file_download

    _, router = fake
    with local_options({"metacheck.osf.pat": "SECRET"}):
        dl = osf_file_download("proj1", str(tmp_path), metadata=False, mode="select")
    assert dl is not None
    got = dict(zip(dl["name"], dl["downloaded"], strict=True))
    assert got["README.md"] and got["a.R"] and got["proj1.csv"]
    readme = next(tmp_path.joinpath("proj1", "github").rglob("README.md"))
    assert readme.read_text() == "github /owner/repo/main/README.md"
    # the OSF token goes to the OSF only
    raw = [c.request for c in router.calls if c.request.url.host == "raw.githubusercontent.com"]
    assert len(raw) == 2
    assert all("authorization" not in r.headers for r in raw)
