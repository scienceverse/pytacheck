"""Guard for the rename (RENAME-1): every "pytacheck" in the repository is classified.

A new occurrence fails this test until it is renamed to metacheck or added to
scripts/rename_keep.toml with a reason. Removed by RENAME-4 with the script.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "rename_to_metacheck.py"

pytestmark = [
    pytest.mark.skipif(
        sys.version_info < (3, 12), reason="the script's tokenizer needs Python 3.12+"
    ),
    pytest.mark.skipif(not (ROOT / ".git").exists(), reason="needs a git checkout"),
]


@pytest.fixture
def script(monkeypatch: pytest.MonkeyPatch) -> Any:
    spec = importlib.util.spec_from_file_location("rename_to_metacheck", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)  # its dataclasses look it up
    spec.loader.exec_module(module)
    return module


def test_every_pytacheck_occurrence_is_classified(
    capsys: pytest.CaptureFixture[str], script: Any
) -> None:
    code = script.main(["--check", "--strict"])
    out = capsys.readouterr().out
    assert code == 0, out


@pytest.mark.parametrize(
    ("text", "word"),
    [
        ('"import pytacheck"', 0),
        ('"import sys, pytacheck; print(1)"', 0),
        ('"import sys, pytacheck as pc, json"', 0),
        ('"import os as o, pytacheck, pytacheck.app"', 0),
        ('"from pytacheck import x"', 0),
    ],
)
def test_each_module_of_an_import_list_in_text_is_import_text(
    script: Any, text: str, word: int
) -> None:
    col = [m.start() for m in re.finditer("pytacheck", text)][word]
    assert script.import_stmt_hit(text, col)


@pytest.mark.parametrize("text", ['"it comes from pytacheck"', '"from x import a, pytacheck"'])
def test_prose_and_other_imports_are_not_import_text(script: Any, text: str) -> None:
    assert not script.import_stmt_hit(text, text.index("pytacheck"))


def test_rules_match_the_line_without_its_terminator(script: Any) -> None:
    """A `$` in a rule matches before CRLF too (a checkout with core.autocrlf)."""
    rule = script.Rule("keep", "x.py", re.compile(r'X = "pytacheck"$'), 2, "")
    index = script.Index({})
    for line in ('X = "pytacheck"\n', 'X = "pytacheck"\r\n'):
        verdict = script.decide("x.py", line, 5, "code", [rule], index, None, 1)
        assert verdict == ("keep", "keep-file")
    assert rule.where == ["x.py:1:6", "x.py:1:6"]


@pytest.mark.parametrize(
    "body",
    [
        '[[rule]]\naction = "kep"\nfiles = "x"\nmatch = "y"\n',
        "[[rule]]\naction = 'keep'\nfiles = 'x'\nmatch = '('\n",
        "[[rule",
    ],
)
def test_a_broken_rules_file_is_a_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], script: Any, body: str
) -> None:
    rules = tmp_path / "rules.toml"
    rules.write_text(body, encoding="utf-8")
    assert script.main(["--check", "--rules", str(rules)]) == 2
    assert "error:" in capsys.readouterr().err
