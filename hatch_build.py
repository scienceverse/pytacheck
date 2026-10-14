"""Build the C reader of R's serialization format, where a C compiler is available.

``metacheck.datacheck._rdata_c`` reads ``.rds`` and ``.RData`` files about ten
times faster than the Python reader in ``_files_rdata.py``, which builds the
same objects and is used when the extension is missing. So the extension is
optional: without a C compiler (or with ``METACHECK_PURE_PYTHON=1``) the wheel
is pure Python, as before, and nothing fails.

A wheel with the extension is tagged ``cp311-abi3-<platform>``: it uses the
limited C API of Python 3.11, so one build serves every supported version. An
editable install (``uv sync``) builds it in place, next to its source.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

EXTENSION = "metacheck.datacheck._rdata_c"
SOURCE = "src/metacheck/datacheck/_rdata_c.c"
LIMITED_API = "0x030B0000"  # 3.11, the oldest supported Python


def _no_compiler_dialog() -> bool:
    """Whether calling the C compiler is safe from a GUI prompt.

    On macOS without the Command Line Tools, ``clang`` is a stub that opens a
    dialog offering to install them; only call it when they are installed.
    """
    if sys.platform != "darwin":
        return True
    try:
        done = subprocess.run(
            ["/usr/bin/xcode-select", "-p"], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0 and Path(done.stdout.strip()).is_dir()


def _build(root: Path, tmp: str, inplace: bool) -> Path:
    """Compile the extension (objects in *tmp*); the path of the built file."""
    from setuptools import Distribution, Extension

    ext = Extension(
        EXTENSION,
        sources=[SOURCE],
        py_limited_api=True,
        define_macros=[("Py_LIMITED_API", LIMITED_API)],
    )
    dist = Distribution({"name": "metacheck", "ext_modules": [ext], "package_dir": {"": "src"}})
    cmd: Any = dist.get_command_obj("build_ext")
    cmd.build_temp = os.path.join(tmp, "temp")
    cmd.build_lib = os.path.join(tmp, "lib")
    cmd.inplace = inplace
    with contextlib.chdir(root):
        cmd.ensure_finalized()
        cmd.run()
        return Path(cmd.get_ext_fullpath(EXTENSION)).resolve()


class CustomBuildHook(BuildHookInterface):  # type: ignore[type-arg]
    _tmp: str | None = None

    def finalize(self, version: str, build_data: dict[str, Any], artifact_path: str) -> None:  # noqa: ARG002
        if self._tmp is not None:
            shutil.rmtree(self._tmp, ignore_errors=True)

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        if self.target_name != "wheel":
            return
        if os.environ.get("METACHECK_PURE_PYTHON", "").strip() not in ("", "0"):
            return
        root = Path(self.root)
        if not (root / SOURCE).is_file() or not _no_compiler_dialog():
            return
        self._tmp = tempfile.mkdtemp(prefix="metacheck-build-")
        try:
            built = _build(root, self._tmp, inplace=version == "editable")
        except (Exception, SystemExit) as exc:  # the Python reader takes over
            self.app.display_warning(
                f"metacheck: the C reader of .rds files was not built ({exc}); "
                "the Python reader is used instead."
            )
            return
        if version == "editable":
            return  # in place, found through the editable path
        build_data["force_include"][str(built)] = f"metacheck/datacheck/{built.name}"
        build_data["pure_python"] = False
        platform = sysconfig.get_platform().replace("-", "_").replace(".", "_")
        build_data["tag"] = f"cp311-abi3-{platform}"
