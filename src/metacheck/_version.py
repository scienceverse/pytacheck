"""Version information for pytacheck and the metacheck release it tracks."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import NotRequired, TypedDict

#: The distribution is "metacheck"; "pytacheck" is what installs before 0.4.0a1 are called.
DISTRIBUTION = "metacheck"
_OLD_DISTRIBUTION = "pytacheck"


def _installed_version() -> str:
    for name in (DISTRIBUTION, _OLD_DISTRIBUTION):
        try:
            return version(name)
        except PackageNotFoundError:
            continue
    return "0.0.0+unknown"  # pragma: no cover - running from a source tree


__version__ = _installed_version()


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
    "commit": "98105f6a81da74f4d2c42d35e692dac0ab83dac3",
    "version": "0.3.3.2",
}
