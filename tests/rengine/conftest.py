"""A stand-in ``Rscript`` for the R engine's tests: it answers as ``run.R`` would.

It reads the request ``run.R`` gets and writes a canonical result. ``FAKE_R_MODE`` says
how: ``ok`` (each module a module output whose report holds one table marker), ``error``
(the result says the paper could not be read), ``crash`` (exit without a result, a message
on stderr) or ``hang`` (never ends).
"""

from __future__ import annotations

import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest

FAKE = textwrap.dedent(
    """
    import json, os, sys, time

    request, result = sys.argv[-2], sys.argv[-1]
    mode = os.environ.get("FAKE_R_MODE", "ok")
    if mode == "crash":
        sys.stderr.write("Error in library(metacheck): there is no package called 'metacheck'\\n")
        sys.exit(1)
    if mode == "hang":
        with open(os.environ["FAKE_R_PIDFILE"], "w") as fh:
            fh.write(str(os.getpid()))
        time.sleep(120)
    ask = json.load(open(request, encoding="utf-8"))

    def chr(*v):
        return {"t": "chr", "v": list(v)}

    names = ["metacheck_version", "r_version"]
    values = [chr("9.9.9"), chr("4.5.3")]
    if mode == "error":
        names.append("error")
        values.append(chr("cannot open file 'paper.json'"))
    elif ask.get("info"):
        names.append("modules")
        values.append(chr("marginal", "stat_check", "data_check"))
    else:
        outputs = []
        for module in ask["modules"]:
            outputs.append({
                "t": "module_output",
                "names": ["module", "title", "section", "table", "report", "traffic_light",
                          "summary_text", "na_replace"],
                "v": [chr(module), chr(module.title()), chr("results"),
                      {"t": "df", "nrow": 2, "names": ["text", "p"],
                       "v": [chr("a", None), {"t": "dbl", "v": [0.04, "NaN"]}]},
                      chr("Found 2.\\n<!-- pytacheck-r-table 1 -->\\nThe end."),
                      chr("yellow"), chr(f"{module} ran on " + os.path.basename(ask["paper"])),
                      {"t": "lgl", "v": [None]}],
            })
        names += ["outputs", "tables"]
        values += [
            {"t": "list", "names": list(ask["modules"]), "v": outputs},
            {"t": "list", "names": None, "v": [{
                "t": "list", "names": ["table", "colwidths", "maxrows", "escape", "column"],
                "v": [{"t": "df", "nrow": 1, "names": ["x"], "v": [chr("cell")]}, chr("auto"),
                      {"t": "dbl", "v": [2]}, {"t": "lgl", "v": [False]}, chr("body")],
            }]},
        ]
    with open(result, "w", encoding="utf-8") as fh:
        json.dump({"t": "list", "names": names, "v": values}, fh)
    """
)


@pytest.fixture
def fake_r(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A stand-in Rscript, made the R engine's, with nothing remembered about R."""
    if sys.platform == "win32":
        pytest.skip("the stand-in Rscript is a script with a #! line")
    from metacheck import rengine

    script = tmp_path / "Rscript"
    script.write_text(f"#!{sys.executable}\n{FAKE}", encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("METACHECK_R_ENGINE_RSCRIPT", str(script))
    monkeypatch.setenv("FAKE_R_MODE", "ok")
    monkeypatch.setenv("FAKE_R_PIDFILE", str(tmp_path / "pid"))
    monkeypatch.setattr(rengine, "_INFO", {})
    yield script
