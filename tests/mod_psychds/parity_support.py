"""Python side of the psychds_check parity cases (``parity/cases/mod_psychds.yaml``).

psychds_check reads data_check's ``structure``, ``table`` and
``group_no_evidence`` (and codebook_check's ``table``) from the module chain.
:func:`fake_chain` builds a :class:`~metacheck.module.ModuleOutput` that looks
like a finished data_check run from a named spec in
``tests/mod_psychds/fixtures/chains.json``, exactly as the R twin
``tests/mod_psychds/fake_chain.R::psychds_fake_chain()`` does, so the module is
parity-tested without running data_check itself.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
CHAINS = HERE / "fixtures" / "chains.json"

PSYCHSCI = [
    "upstream/metacheck/tests/testthat/fixtures/psychsci/0956797613520608.json",
    "upstream/metacheck/tests/testthat/fixtures/psychsci/0956797614522816.json",
    "upstream/metacheck/tests/testthat/fixtures/psychsci/0956797614527830.json",
    "upstream/metacheck/inst/demos/to_err_is_human.json",
]


@cache
def specs(file: str = "chains.json") -> dict[str, Any]:
    """The spec file *file* of ``tests/mod_psychds/fixtures/`` (R: ``psychds_specs()``).

    ``chains.json`` feeds ``mod_psychds.yaml``, ``review_chains.json`` feeds
    ``mod_psychds_review.yaml``.
    """
    path = CHAINS.parent / file
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _chr(vals: list[Any]) -> Any:
    return pd.array([None if v is None else str(v) for v in vals], dtype="string")


def load_paper(which: str | list[str] | None) -> Any:
    import metacheck as pc

    if isinstance(which, list):
        # repository-relative paths: read() them (one paper or a list)
        paths = [ROOT / p for p in which]
        return pc.read(paths[0]) if len(paths) == 1 else pc.read(paths)
    which = which or "demo"
    if which == "demo":
        return pc.demopaper()
    if which == "psychsci":
        return pc.read([ROOT / p for p in PSYCHSCI])
    if which == "test":
        p = pc.test_paper()
        p.paper_id = "p1"  # R's test_paper() ids come from the clock
        return p
    if which == "none":
        return None
    raise ValueError(f"unknown paper {which}")


def structure(spec: dict[str, Any]) -> pd.DataFrame | None:
    """The data_check ``structure`` table of *spec* (R: ``psychds_structure()``)."""
    st = spec.get("structure")
    if st is None:
        return None
    cols = {k: _chr(v) for k, v in st.items() if k != "referenced_by"}
    s = pd.DataFrame(cols)
    if spec.get("classify"):
        from metacheck.datacheck.files import _data_doc_role, data_classify_files

        names = s["file_name"].tolist()
        paths = s["file_path"].tolist() if "file_path" in s.columns else None
        s["data_type"] = pd.array(data_classify_files(names, paths), dtype="string")
        s["doc_role"] = pd.array(_data_doc_role(names), dtype="string")
    if "referenced_by" in st:
        s["referenced_by"] = pd.Series(
            [[str(x) for x in v] if v else None for v in st["referenced_by"]],
            dtype=object,
            index=s.index,
        )
    return s


def fake_chain(name: str, paper: Any = None, file: str = "chains.json") -> Any:
    """A data_check :class:`ModuleOutput` for the named spec (R: ``psychds_fake_chain()``).

    *paper* replaces the spec's paper (e.g. a bibr 12.x reading of the demo paper).
    """
    from metacheck.module import ModuleOutput
    from metacheck.papers.tables import paper_id

    spec = specs(file)["chains"][name]
    if paper is None:
        paper = load_paper(spec.get("paper"))
    ids = [] if paper is None else list(paper_id(paper))
    first_id = ids[0] if ids else None

    columns = None
    if spec.get("columns") is not None:
        n = int(spec["columns"])
        columns = pd.DataFrame(
            {
                "paper_id": pd.array([first_id] * n, dtype="string"),
                "source_file": pd.array(["data.csv"] * n, dtype="string"),
                "column_name": pd.array([f"v{i + 1}" for i in range(n)], dtype="string"),
            }
        )
    prev: dict[str, Any] = {}
    if spec.get("labels") is not None:
        labels = spec["labels"]
        lab = pd.DataFrame(
            {
                "source_file": pd.array(["data.csv"] * len(labels), dtype="string"),
                "column_name": pd.array([f"v{i + 1}" for i in range(len(labels))], dtype="string"),
            }
        )
        if not spec.get("labels_no_status"):
            lab["label_status"] = _chr(labels)
        prev["codebook_check"] = ModuleOutput(
            module="codebook_check", title="Codebook Check", section="results", table=lab
        )
    gne = spec.get("group_no_evidence")
    if gne == "NA":
        gne = None

    return ModuleOutput(
        module="data_check",
        title="Data Check",
        section="results",
        table=columns,
        report="",
        traffic_light="info",
        summary_text="",
        summary_table=pd.DataFrame({"paper_id": pd.array(ids, dtype="string")}),
        paper=paper,
        prev_outputs=prev,
        extras={"structure": structure(spec), "group_no_evidence": gne},
    )


def run_chain(name: str, paper: Any = None, file: str = "chains.json") -> Any:
    """``module_run(fake_chain(name), "psychds_check")``."""
    from metacheck.module import module_run

    return module_run(fake_chain(name, paper, file), "psychds_check")


def run_llm(name: str, use: bool = True, file: str = "chains.json") -> Any:
    """psychds_check on a fake chain with ``llm_use(use)`` (R: ``psychds_run_llm()``)."""
    from metacheck.utils import local_options

    with local_options({"metacheck.llm.use": use}):
        return run_chain(name, file=file)


def tree(name: str, file: str = "chains.json") -> str:
    """``psychds_tree_html()`` on a named node table of chains.json's ``trees``.

    R: ``psychds_tree()``; a ``null`` node table stays ``None``.
    """
    from metacheck.modules.psychds_check import psychds_tree_html

    spec = specs(file)["trees"][name]
    nodes = spec.get("nodes")
    df = None if nodes is None else pd.DataFrame({k: _chr(v) for k, v in nodes.items()})
    return psychds_tree_html(df)


def identity(x: Any) -> Any:
    """R ``base::identity()``."""
    return x
