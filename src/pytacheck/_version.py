"""Version information for pytacheck and the metacheck release it tracks."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import NotRequired, TypedDict

try:
    __version__ = version("pytacheck")
except PackageNotFoundError:  # pragma: no cover - running from a source tree
    __version__ = "0.0.0+unknown"


class _Upstream(TypedDict):
    repository: str
    branch: str
    commit: str
    version: str
    pull_request: NotRequired[int]
    base_commit: NotRequired[str]


#: The metacheck commit this release is verified against (see parity/UPSTREAM.toml).
UPSTREAM: _Upstream = {
    "repository": "https://github.com/scienceverse/metacheck",
    "branch": "dev",
    "commit": "b239264f6b80a967636d0a1bc0ed294f0df93bb3",
    "version": "0.3.1",
    # dev plus scienceverse/metacheck#423 (bibr export schema 12.0), not yet merged
    "pull_request": 423,
    "base_commit": "85c8c872cf71ab5a70c10008bd41bf563352a396",
}
