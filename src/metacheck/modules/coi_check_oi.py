"""COI Check Overinclusive (port of ``inst/modules/coi_check_oi.R``).

In R the file defines a function called ``coi_check`` (``module_run()`` calls the
function the file defines); here it is ``coi_check_oi``, as the module is named.

References (roxygen ``@references`` of the R module):

Serghiou, S., Contopoulos-Ioannidis, D. G., Boyack, K. W., Riedel, N., Wallach, J. D., &
Ioannidis, J. P. (2021). Assessment of transparency indicators across the biomedical
literature: How open is open?. PLoS biology, 19(3), e3001107. doi: 10.1371/journal.pbio.3001107

Serghiou S (2025). _rtransparent: Identifies indicators of transparency_. R package version
0.2.5, commit d0d5dfe4b6c4e519d54e341436d4263fb05d84be, <https://github.com/serghiou/rtransparent>.
"""

from __future__ import annotations

from typing import Any

from metacheck.module import module
from metacheck.text import text_search

__all__ = ["coi_check_oi"]

_PATTERN_MAIN = [r"\binterests?\b", r"\bCOI\b"]
_PATTERN_INC = ["conflict", "compet", "disclosure", "declaration"]
# definitely exclude
_PATTERN_EXC = ["financial disclosure"]


@module(
    title="COI Check Overinclusive",
    description="Identify and extract Conflicts of Interest (COI) statements.",
    details="""
        The COI Check module uses regular expressions to check sentences for words related to conflict of interest statements. It will return the sentences in which the conflict of interest statement was found.

        The function is based on code from [rtransparent](https://github.com/serghiou/rtransparent), which is no longer maintained. For their validation, see [the paper](https://doi.org/10.1371/journal.pbio.3001107).

        This version is over-inclusive, so will have more false positives, but is less likely to miss something.
    """,
    keywords=["general"],
    author=["Daniel Lakens <d.lakens@tue.nl>"],
    params={"paper": "a paper object or paperlist object"},
)
def coi_check_oi(paper: Any) -> dict[str, Any]:
    """Port of ``inst/modules/coi_check_oi.R::coi_check()``."""
    from metacheck.modules.coi_check import coi_results

    # get potential COI statements, then merge the text by section/id
    table = text_search(paper, _PATTERN_MAIN, search_header=True)
    table = text_search(table, _PATTERN_INC, search_header=True)
    table = text_search(table, _PATTERN_EXC, exclude=True)
    table = text_search(table, return_="section")
    return coi_results(table)
