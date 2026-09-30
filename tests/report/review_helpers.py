"""Python side of the report review parity cases (twin of review_helpers.R)."""

from __future__ import annotations

import os
import shutil
import tempfile
import warnings
from pathlib import Path
from typing import Any

from tests.report.parity_helpers import _args, _mods, _unpath, rp_mask


def rv_report_list(
    paper: Any, modules: Any, files: Any, output_format: str = "qmd", args: Any = None
) -> dict[str, Any]:
    from metacheck.report.report import report

    d = tempfile.mkdtemp()
    try:
        files = [files] if isinstance(files, str) else list(files)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            res = report(
                paper,
                _mods(modules),
                [os.path.join(d, f) for f in files],
                output_format,
                _args(args),
            )
        listing = sorted(os.listdir(d))
        texts = []
        for f in listing:
            txt = Path(d, f).read_text(encoding="utf-8")
            texts.append(rp_mask(_unpath(txt[:-1] if txt.endswith("\n") else txt)))
        return {
            "names": list(res.keys()),
            "failed": [v is None for v in res.values()],
            "save_path": [
                None if p is None else p.replace(d + "/", "", 1) for p in res.save_path.values()
            ],
            "files": listing,
            "traffic_lights": [
                None if r is None else [m.traffic_light for m in r.values()] for r in res.values()
            ],
            "text": texts,
        }
    finally:
        shutil.rmtree(d, ignore_errors=True)
