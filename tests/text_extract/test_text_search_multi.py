"""text_search() with several patterns, as R 4.5.3 / metacheck combines the results.

The parity cases ``text/text_search.vector.multi*`` and
``text/text_search.demo.multi.exclude.*`` hold R's results.
"""

from __future__ import annotations

import pandas as pd
import pytest

import metacheck as pc

STRINGS = ["ethics approval here", "no", "approv only", "no"]


@pytest.mark.parametrize("patterns", [["ethic", "approv"], ["ethic", "zzz"], ["zzz", "yyy"]])
def test_strings_with_several_patterns_fail_in_bind_rows(patterns: list[str]) -> None:
    # dplyr::bind_rows() refuses character vectors, whatever they hold
    with pytest.raises(ValueError, match=r"^Argument 1 must be a data frame or a named atomic"):
        pc.text_search(STRINGS, patterns)
    with pytest.raises(ValueError, match=r"^Argument 1 must be a data frame"):
        pc.text_search("ethics approval here", patterns)


def test_strings_with_several_excluded_patterns_intersect() -> None:
    # base::intersect(x, y): unique strings of the first result in the second
    assert pc.text_search(STRINGS, ["ethic", "approv"], exclude=True) == ["no"]
    assert pc.text_search(["ethics", "ethics"], ["zz", "yy"], exclude=True) == ["ethics"]


def test_strings_with_three_excluded_patterns_are_an_unused_argument() -> None:
    with pytest.raises(TypeError) as err:
        pc.text_search(STRINGS, ["ethic", "approv", "zz"], exclude=True)
    assert str(err.value) == ('unused argument (c("ethics approval here", "no", "approv only"))')
    with pytest.raises(TypeError) as err:
        pc.text_search(STRINGS, ["e", "o", "n", "zz"], exclude=True)
    assert str(err.value) == (
        'unused arguments ("ethics approval here", c("ethics approval here", "no", "approv only"))'
    )
    with pytest.raises(TypeError, match=r"^unused argument \(character\(0\)\)$"):
        pc.text_search(["a"], ["zz", "yy", "a"], exclude=True)


def test_tables_with_three_excluded_patterns_have_non_empty_dots() -> None:
    table = pd.DataFrame({"text": ["a", "b"]})
    assert len(pc.text_search(table, ["zz", "yy"], exclude=True)) == 2
    with pytest.raises(ValueError) as err:
        pc.text_search(table, ["zz", "yy", "ww", "vv"], exclude=True)
    assert str(err.value) == (
        "`...` must be empty.\n✖ Problematic arguments:\n• ..1 = <tibble[,7]>\n"
        "• ..2 = <tibble[,7]>\nℹ Did you forget to name an argument?"
    )
