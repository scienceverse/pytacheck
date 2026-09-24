"""Version information for pytacheck and the metacheck release it tracks."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pytacheck")
except PackageNotFoundError:  # pragma: no cover - running from a source tree
    __version__ = "0.0.0+unknown"

#: The metacheck commit this release is verified against (see parity/UPSTREAM.toml).
UPSTREAM = {
    "repository": "https://github.com/scienceverse/metacheck",
    "branch": "dev",
    "commit": "85c8c872cf71ab5a70c10008bd41bf563352a396",
    "version": "0.3.1",
}
