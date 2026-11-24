"""Port of the known-host test of metacheck's tests/testthat/test-paper.R (#695, point 4)."""

from __future__ import annotations

import metacheck as pc
from metacheck.papers.tables import _known_host_unbroken, paper_table


def test_names_of_known_sites_broken_by_a_line_end_hyphen_are_joined() -> None:
    x = [
        "see https://aspre-dicted.org/BUC_HUQ).",
        "code at git-hub.com/a/b and o-sf.io/abc12",
        "namecensus.com/baby-names",
        "the co-sf.io thing",
        "pro-cessmacro.org",
        None,
        "github.com/x/y-z",
    ]
    assert _known_host_unbroken(x) == [
        "see https://aspredicted.org/BUC_HUQ).",
        "code at github.com/a/b and osf.io/abc12",
        "namecensus.com/baby-names",
        "the co-sf.io thing",
        "pro-cessmacro.org",
        None,
        "github.com/x/y-z",
    ]
    p = pc.test_paper("Our data are at fig-share.com/articles/123.")
    assert "figshare.com/articles/123" in paper_table(p, "text")["text"].iloc[0]
