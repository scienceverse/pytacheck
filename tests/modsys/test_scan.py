"""Static scans: imports, risky calls and @module metadata, without importing code."""

from __future__ import annotations

from metacheck.packs.scan import module_metadata, network_imports, scan_file


def test_imports_and_risky_calls(tmp_path) -> None:
    f = tmp_path / "m.py"
    f.write_text(
        "from __future__ import annotations\n"
        "import os, json\n"
        "from metacheck import http\n"
        "from urllib.request import urlopen\n"
        "from . import _helper\n"
        "def f():\n"
        "    import subprocess\n"
        "    os.system('ls')\n"
        "    eval('1')\n"
        "    return json\n"
    )
    scan = scan_file(f)
    assert scan.error is None
    assert "__future__" not in scan.imports
    assert {
        "os",
        "json",
        "metacheck",
        "metacheck.http",
        "urllib.request",
        ".",
        "subprocess",
    } <= set(scan.imports)
    risky = dict(scan.risky)
    assert risky["subprocess"] == "runs programs"
    assert risky["metacheck.http"].startswith("network")
    assert risky["urllib.request"].startswith("network")
    assert risky["os.system()"] == "runs programs" and risky["eval()"] == "evaluates code"
    assert sorted(network_imports(scan)) == ["metacheck.http", "urllib.request"]
    bad = tmp_path / "bad.py"
    bad.write_text("def (:\n")
    assert scan_file(bad).error.startswith("SyntaxError")


def test_module_metadata_is_read_statically(tmp_path) -> None:
    f = tmp_path / "apa_df.py"
    f.write_text(
        "raise SystemExit('never imported')\n"
        "from metacheck.module import module\n"
        "import metacheck as pc\n"
        "@pc.module(title='APA df', description='''\n    Checks degrees of freedom.\n''',\n"
        "           keywords=['results', 'network'], requires='llm',\n"
        "           validation={'papers': 3, 'tp': 2, 'fp': 1}, author=f'x{1}')\n"
        "def apa_df(paper, strict=False):\n    return {}\n"
        "@module(title='Other')\ndef other(paper):\n    return {}\n"
    )
    meta = module_metadata(f)
    assert meta["name"] == "apa_df" and meta["title"] == "APA df"
    assert meta["description"] == "Checks degrees of freedom."
    assert (meta["keywords"], meta["section"]) == (["results"], "results")
    assert meta["requires"] == ["llm", "network"]
    assert meta["validation"] == {"papers": 3, "tp": 2, "fp": 1}
    assert meta["author"] == [], "computed values are left out"
    (tmp_path / "plain.py").write_text("def plain(paper):\n    return {}\n")
    assert module_metadata(tmp_path / "plain.py") is None
