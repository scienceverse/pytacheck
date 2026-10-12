"""What the snapshots record: G5's inputs and the two fixtures, as cases.

A case is one call, such as ``module_run(read(path), "ethics_check")``. The
recorder (``scripts/record_snapshots.py``) and the oracle (``oracle.py``) both
take their cases from here, so they cannot drift apart.

The inputs are pinned here, not read from ``parity/accuracy/matrix.toml``: a new
accuracy row must not change what the rewrite is compared with. They are the
matrix of September 2026 (19 offline paper modules on 21 papers, 4 repository
modules on 10 repositories), the demo paper, the psychsci list and the spike's
3-paper list. The network and LLM modules (causal_claims, ref_pubpeer, power,
prereg_check, reg_check, psychds_check, reproducibility_check) are not here:
offline they fail or return ``na`` only, and their parity cases cover them.

Every case runs as ``python -m parity accuracy`` runs a module: in UTC, without
credentials, network or R, with metacheck's argument defaults, numbered paper
ids and a fresh cache directory. Each case reads its own input, so no case
depends on another having run first.
"""

from __future__ import annotations

import contextlib
import functools
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from parity import accuracy as A
from parity.cases import (
    RWithoutReference,
    _watch_for_r,
    deterministic_ids,
    metacheck_defaults,
    no_credentials,
    utc,
)
from tests.httpmock import no_network
from tests.snapshots.store import ROOT, Record, dumps, outcome

FIXTURES = "upstream/metacheck/tests/testthat/fixtures"

#: the offline paper modules of the accuracy matrix
PAPER_MODULES = (
    "all_p_values",
    "all_urls",
    "coi_check",
    "coi_check_oi",
    "ethics_check",
    "funding_check",
    "funding_check_oi",
    "marginal",
    "open_practices",
    "ref_accuracy",
    "ref_consistency",
    "ref_miscitation",
    "ref_replication",
    "ref_retraction",
    "ref_summary",
    "stat_check",
    "stat_effect_size",
    "stat_p_exact",
    "stat_p_nonsig",
)
PAPER_INPUTS = (
    "upstream/metacheck/inst/demos/to_err_is_human.xml",
    "upstream/metacheck/inst/demos/to_err_is_human.json",
    f"{FIXTURES}/formats/published.pdf.tei.xml",
    f"{FIXTURES}/formats/preprint.pdf.tei.xml",
    f"{FIXTURES}/formats/apa.xml",
    f"{FIXTURES}/problems/203020.xml",
    f"{FIXTURES}/problems/203020.json",
    f"{FIXTURES}/problems/0956797617737129.xml",
    f"{FIXTURES}/problems/0956797615569889.xml",
    f"{FIXTURES}/debruine/debruine-fret.xml",
    f"{FIXTURES}/debruine/debruine-child.xml",
    f"{FIXTURES}/debruine/debruine-sex.xml",
    f"{FIXTURES}/debruine/debruine-tnl.xml",
    f"{FIXTURES}/psychsci/0956797614527830.json",
    f"{FIXTURES}/psychsci/0956797614522816.json",
    f"{FIXTURES}/psychsci/0956797613520608.json",
    f"{FIXTURES}/bibr12/PMC4383902.json",
    f"{FIXTURES}/bibr12/preprint.json",
    f"{FIXTURES}/bibr12/full.json",
    f"{FIXTURES}/bibr12/probe_html.json",
    f"{FIXTURES}/bibr12/probe_docx.json",
)
#: run on the demo paper with ``local_path`` = the repository and ``local_only=True``
REPOSITORY_MODULES = ("code_check", "data_check", "repo_check", "codebook_check")
REPOSITORY_INPUTS = (
    "tests/mod_data_check/fixtures/repos/basic",
    "tests/mod_data_check/fixtures/repos/careless",
    "tests/mod_data_check/fixtures/repos/qualtrics",
    "tests/mod_data_check/fixtures/repos/spreadsheets",
    "tests/mod_data_check/fixtures/repos/encoding",
    "tests/mod_data_check/fixtures/repos/yellow",
    f"{FIXTURES}/demo",
    f"{FIXTURES}/code_files",
    f"{FIXTURES}/notebooks",
    f"{FIXTURES}/parse-errors",
)
#: the psychsci folder, read as one list of 3 papers
PSYCHSCI_LIST = f"{FIXTURES}/psychsci"
#: the spike's 3-paper list: the first 3 matrix papers with distinct ids
LIST3 = (
    "upstream/metacheck/inst/demos/to_err_is_human.xml",
    f"{FIXTURES}/formats/published.pdf.tei.xml",
    f"{FIXTURES}/formats/preprint.pdf.tei.xml",
)

# -- the fixtures ---------------------------------------------------------------------

FRET = f"{FIXTURES}/debruine/debruine-fret.xml"
CHILD = f"{FIXTURES}/debruine/debruine-child.xml"
#: the id both papers of the same-id fixture get
SAME_ID = "X"
#: the body paragraph of debruine-fret that the duplicate-paragraph fixture repeats:
#: one sentence in the results, with an F test and its p-values
DUPLICATE_PARAGRAPH = 20

#: fixture name -> what it pins
FIXTURE_RULES = {
    "same_id": "F6: repeated paper ids are resolved once, the first keeps its id and "
    "later ones become id~2, id~3, ...",
    "duplicate_paragraph": "V7: a paragraph repeated exactly (every column of its rows "
    "equal) gives the same results as the paper without the repeat",
}


def same_id_list(ids: tuple[str, str] = (SAME_ID, SAME_ID)) -> Any:
    """debruine-fret and debruine-child as one list, with the paper ids *ids*."""
    from metacheck.papers import PaperList

    papers = [read(FRET), read(CHILD)]
    for paper, paper_id in zip(papers, ids, strict=True):
        paper.paper_id = paper_id
    return PaperList(papers)


def duplicate_paragraph(paper: Any, paragraph_id: int = DUPLICATE_PARAGRAPH) -> Any:
    """*paper* with the text rows of one paragraph repeated right after it."""
    text = paper.text
    rows = (text["paragraph_id"] == paragraph_id).fillna(False).to_numpy(bool).nonzero()[0]
    if not len(rows) or list(rows) != list(range(rows[0], rows[-1] + 1)):
        raise ValueError(f"paragraph {paragraph_id} is not one block of rows")
    first, last = int(rows[0]), int(rows[-1]) + 1
    paper["text"] = pd.concat(
        [text.iloc[:last], text.iloc[first:last], text.iloc[last:]], ignore_index=True
    )
    return paper


# -- cases ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    """One recorded call.

    ``call`` is what the rewrite runs. ``expect``, when given, is the call whose
    output on base is the expected value instead: a fixture's input differs from
    the input base gives the expected output on (the same-id list is recorded
    with F6's ids already applied).
    """

    id: str
    group: str
    call: Callable[[], Any]
    expect: Callable[[], Any] | None = None
    module: str | None = None


@dataclass(frozen=True)
class SnapshotSet:
    """A set of cases stored together, in one layout (``store.LAYOUTS``)."""

    name: str
    layout: str
    build: Callable[[], list[Case]]
    #: lines the recorder prints after recording the set
    notes: Callable[[dict[str, Record]], list[str]] | None = None

    def cases(self) -> list[Case]:
        cases = self.build()
        seen: set[str] = set()
        for c in cases:
            if c.id in seen or not c.id or any(ch.isspace() for ch in c.id):
                raise ValueError(f"{self.name}: case id {c.id!r} is repeated, empty or has spaces")
            seen.add(c.id)
        return cases


def read(path: str) -> Any:
    import metacheck as pc

    return pc.read(ROOT / path)


def _run_module(module: str, make: Callable[[], Any], args: dict[str, Any]) -> Any:
    from metacheck.module import module_run

    return module_run(make(), module, **args)


def module_call(module: str, make: Callable[[], Any], **args: Any) -> Callable[[], Any]:
    """``module_run(make(), module, **args)``, as a call."""
    return functools.partial(_run_module, module, make, args)


def _demo() -> Any:
    import metacheck as pc

    return pc.demopaper()


def _psychsci_list() -> Any:
    return read(PSYCHSCI_LIST)


def _list3() -> Any:
    from metacheck.papers import PaperList

    return PaperList([read(p) for p in LIST3])


def _module_cases() -> list[Case]:
    cases: list[Case] = []
    for m in PAPER_MODULES:
        for path in PAPER_INPUTS:
            case_id = A.Output(m, path, "paper").id  # the accuracy report's id
            cases.append(Case(case_id, m, module_call(m, functools.partial(read, path)), None, m))
    for m in REPOSITORY_MODULES:
        for path in REPOSITORY_INPUTS:
            case_id = A.Output(m, path, "repository").id
            # with "/" on Windows too, as parity's $file arguments: the outputs repeat it
            local_path = (ROOT / path).as_posix()
            call = module_call(m, _demo, local_path=local_path, local_only=True)
            cases.append(Case(case_id, m, call, None, m))
    extras = {"demo": _demo, "psychsci_list": _psychsci_list, "list3": _list3}
    for m in PAPER_MODULES:
        for name, make in extras.items():
            cases.append(Case(f"{m}.{name}", m, module_call(m, make), None, m))
    return cases


def _duplicated() -> Any:
    return duplicate_paragraph(read(FRET))


def _fixture_cases() -> list[Case]:
    resolved = functools.partial(same_id_list, (SAME_ID, f"{SAME_ID}~2"))
    fixtures = {
        # (the input, the input base gives the expected output on)
        "same_id": (same_id_list, resolved),
        "duplicate_paragraph": (_duplicated, functools.partial(read, FRET)),
    }
    cases: list[Case] = []
    for name, (make, make_expected) in fixtures.items():
        for m in PAPER_MODULES:
            call = module_call(m, make)
            cases.append(Case(f"{m}.{name}", name, call, module_call(m, make_expected), m))
            # base's own output on the input, before the rule lands
            cases.append(Case(f"{m}.{name}.before", name, call, None, m))
    return cases


def fixture_gaps(records: dict[str, Record]) -> dict[str, list[str]]:
    """Per fixture, the modules whose output on base differs from the expected one.

    For same_id the resolved id is read as the repeated one, since base cannot
    make up ``X~2``: what is left are the behaviours F6 removes.
    """
    gaps: dict[str, list[str]] = {}
    for name in FIXTURE_RULES:
        gaps[name] = []
        for m in PAPER_MODULES:
            expected, before = records.get(f"{m}.{name}"), records.get(f"{m}.{name}.before")
            if expected is None or before is None:
                continue
            want = dumps(expected)
            if name == "same_id":
                want = want.replace(f"{SAME_ID}~2".encode(), SAME_ID.encode())
            if want != dumps(before):
                gaps[name].append(m)
    return gaps


def _fixture_notes(records: dict[str, Record]) -> list[str]:
    return [
        f"{name}: base differs from the expected output in {len(gaps)} of "
        f"{len(PAPER_MODULES)} modules" + (f": {', '.join(gaps)}" if gaps else "")
        for name, gaps in fixture_gaps(records).items()
    ]


SETS = {
    s.name: s
    for s in (
        SnapshotSet("modules", "json", _module_cases),
        SnapshotSet("fixtures", "json", _fixture_cases, _fixture_notes),
    )
}


@functools.cache
def _cases_by_id(set_name: str) -> dict[str, Case]:
    return {c.id: c for c in SETS[set_name].cases()}


def find(set_name: str, case_id: str) -> Case:
    if set_name not in SETS:
        raise KeyError(f"no snapshot set {set_name!r} (sets: {', '.join(SETS)})")
    try:
        return _cases_by_id(set_name)[case_id]
    except KeyError:
        raise KeyError(f"{set_name} has no case {case_id!r}") from None


# -- running a case -------------------------------------------------------------------


@contextlib.contextmanager
def context(network: list[str], r: list[str]) -> Iterator[None]:
    """The context every case runs in; attempts to use the network or R are
    added to *network* and *r*."""
    base = os.environ.get("PYTACHECK_CACHE_DIR")
    if base:  # each case gets a fresh folder inside it
        Path(base).mkdir(parents=True, exist_ok=True)
    with (
        utc(),
        no_credentials(),
        metacheck_defaults(),
        A._isolated(),
        deterministic_ids(),
        _watch_for_r(r),
        no_network(network),
    ):
        yield


def _outcome_in_context(call: Callable[[], Any]) -> Record:
    network: list[str] = []
    r: list[str] = []
    try:
        with context(network, r):
            record = outcome(call)
    except RWithoutReference:
        record = {"ok": False, "error": "started R"}
    if network:
        record["problem"] = f"used the network ({network[0]})"
    elif r:
        record["problem"] = f"started R ({r[0]})"
    return record


def record(case: Case) -> Record:
    """The record of *case* on this tree, as its snapshot stores it."""
    return _outcome_in_context(case.expect or case.call)


def run(case: Case) -> Record:
    """The record of *case*'s own call on this tree (what the oracle compares)."""
    return _outcome_in_context(case.call)
