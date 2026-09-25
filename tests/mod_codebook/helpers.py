"""Python side of the codebook_check parity cases and tests.

codebook_check reads data_check's output through ``get_prev_outputs()``.
The fixtures (``tests/mod_codebook/fixtures/make_fixtures.R``) store that
output, as produced by metacheck's ``data_check`` module, in canonical parity
JSON; :func:`cbc_prev` rebuilds it as a :class:`~pytacheck.module.ModuleOutput`
whose paper is a test paper (or any paper passed in), so ``module_run()``
chains codebook_check onto it exactly as in a report pipeline. The R twin is
``tests/mod_codebook/cbc_helpers.R``.
"""

from __future__ import annotations

import functools
import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "mod_codebook" / "fixtures"

_DTYPES = {"chr": "string", "dbl": "float64", "int": "Int64", "lgl": "boolean"}


def decode(x: Any) -> Any:
    """Canonical parity JSON (``parity/r/canonical.R``) -> Python value."""
    if x is None or x.get("t") == "null":
        return None
    t = x["t"]
    if t in _DTYPES:
        vals = []
        for e in x.get("v", []):
            if t == "dbl" and isinstance(e, str):
                vals.append({"NaN": math.nan, "Inf": math.inf, "-Inf": -math.inf}[e])
            else:
                vals.append(e)
        return {"t": t, "v": vals, "names": x.get("names")}
    if t == "df":
        n = x["nrow"]
        names = list(x["names"])
        cols = []
        for col in x["v"]:
            d = decode(col)
            if d is None:
                cols.append(pd.Series([None] * n, dtype=object))
            elif isinstance(d, dict) and d.get("t") in _DTYPES:
                v = d["v"]
                if d["t"] == "dbl":
                    cols.append(
                        pd.Series([math.nan if e is None else float(e) for e in v], dtype="float64")
                    )
                else:
                    cols.append(pd.Series(v, dtype=_DTYPES[d["t"]]))
            else:
                cols.append(pd.Series(d, dtype=object))
        if not cols:
            return pd.DataFrame(index=range(n))
        df = pd.concat(cols, axis=1, ignore_index=True)
        df.columns = names  # keeps duplicated names, as R's data frame does
        return df
    if t == "list":
        items = [_plain(decode(e)) for e in x.get("v", [])]
        names = x.get("names")
        if names is not None:
            return dict(zip(names, items, strict=True))
        return items
    raise ValueError(f"cannot decode canonical type {t}")


def _plain(v: Any) -> Any:
    if isinstance(v, dict) and v.get("t") in _DTYPES:
        return v["v"][0] if len(v["v"]) == 1 else v["v"]
    return v


@functools.cache
def _load(scenario: str) -> dict[str, Any]:
    return json.loads((FIXTURES / "dc" / f"{scenario}.json").read_text(encoding="utf-8"))


def scenario_text(scenario: str) -> list[str]:
    return [str(t) for t in _load(scenario)["text"]]


def cbc_paper(scenario: str, pid: str = "p1") -> Any:
    """A test paper of the scenario's text with a fixed id (as R's ``cbc_paper()``)."""
    import pytacheck as pc

    p = pc.test_paper(scenario_text(scenario))
    p.paper_id = pid
    return p


def _stamp(df: pd.DataFrame | None, pid: str) -> pd.DataFrame | None:
    if df is not None and "paper_id" in df.columns:
        df = df.copy()
        df["paper_id"] = pd.Series([pid] * len(df), index=df.index, dtype="string")
    return df


def cbc_pieces(scenario: str, pid: str = "p1") -> dict[str, Any]:
    """The data_check pieces of one scenario (``table``, ``structure``, ``previews``)."""
    dc = _load(scenario)
    structure = _stamp(decode(dc["structure"]), pid)
    if structure is not None and "file_location" in structure.columns:
        structure = structure.copy()
        structure["file_location"] = pd.Series(
            [None if pd.isna(v) else str(ROOT / v) for v in structure["file_location"]],
            index=structure.index,
            dtype="string",
        )
    previews = decode(dc["previews"])
    return {
        "table": _stamp(decode(dc["table"]), pid),
        "structure": structure,
        "previews": previews
        if isinstance(previews, dict)
        else ({} if previews == [] else previews),
    }


def _output(paper: Any, pieces: dict[str, Any], pids: Sequence[str]) -> Any:
    from pytacheck.module import ModuleOutput

    return ModuleOutput(
        module="data_check",
        title="Data Check",
        section="results",
        table=pieces["table"],
        report="",
        traffic_light="info",
        summary_text=None,
        summary_table=pd.DataFrame({"paper_id": pd.Series(list(pids), dtype="string")}),
        paper=paper,
        prev_outputs={},
        extras={"structure": pieces["structure"], "previews": pieces["previews"]},
    )


def cbc_prev(
    scenario: str, paper: Any = None, pid: str | None = None, drop: Sequence[str] = ()
) -> Any:
    """data_check's output for *scenario* on *paper* (default: the scenario's test paper)."""
    if paper is None:
        paper = cbc_paper(scenario, pid or "p1")
    pid = pid or paper.paper_id
    pieces = cbc_pieces(scenario, pid)
    for k in drop:
        pieces[k] = None
    return _output(paper, pieces, [pid])


def cbc_prev_list(scenarios: Sequence[str]) -> Any:
    """data_check's output for a paper list: one scenario per paper (ids p1, p2, ...)."""
    import pytacheck as pc
    from pytacheck._r import bind_rows

    pids = [f"p{i}" for i in range(1, len(scenarios) + 1)]
    papers = [cbc_paper(s, p) for s, p in zip(scenarios, pids, strict=True)]
    parts = [cbc_pieces(s, p) for s, p in zip(scenarios, pids, strict=True)]
    previews: dict[str, Any] = {}
    for part in parts:
        for k, v in (part["previews"] or {}).items():
            previews[k] = v  # data_check: a later file of the same name replaces it
    pieces = {
        "table": bind_rows([p["table"] for p in parts]),
        "structure": bind_rows([p["structure"] for p in parts]),
        "previews": previews,
    }
    return _output(pc.PaperList(papers), pieces, pids)


def cbc_run(scenario: str, paper: Any = None, **kwargs: Any) -> Any:
    """``module_run(<data_check output>, "codebook_check", ...)``."""
    from pytacheck.module import module_run

    return module_run(cbc_prev(scenario, paper=paper), "codebook_check", **kwargs)


def report_text(out: Any) -> str:
    """The report's text blocks joined by newlines (tables skipped)."""
    report = out.report if isinstance(out.report, list) else [out.report]
    return "\n".join(b for b in report if isinstance(b, str))


# -----------------------------------------------------------------------------
# inputs of the helper parity cases (the same values as the R expressions in
# tests/mod_codebook/make_cases.py)
# -----------------------------------------------------------------------------


def identity(x: Any) -> Any:
    """R ``base::identity()``."""
    return x


def _mat(nrow: int, ncol: int, k: int, names: Sequence[str]) -> pd.DataFrame:
    """``as.data.frame(outer(1:nrow, 1:ncol, function(i, j) ((i * 7 + j * 3) %% k) + 1))``."""
    cols = [
        pd.Series([float((i * 7 + j * 3) % k + 1) for i in range(1, nrow + 1)], dtype="float64")
        for j in range(1, ncol + 1)
    ]
    df = pd.concat(cols, axis=1, ignore_index=True)
    df.columns = list(names)
    return df


def _frame(**cols: Sequence[Any]) -> pd.DataFrame:
    return pd.DataFrame({k: list(v) for k, v in cols.items()})


def r_split_args(name: str) -> tuple[list[str], pd.DataFrame | None]:
    if name == "agg_name":
        cols = [f"AQ{i:02d}" for i in range(1, 11)] + ["AQ_SUM"]
        df = _mat(20, 11, 5, cols)
        df["AQ_SUM"] = df.iloc[:, :10].sum(axis=1)
        return cols, df
    if name == "mean_value":
        cols = [f"bfi_{i}" for i in range(1, 10)]
        df = _mat(20, 9, 5, cols)
        df["bfi_9"] = df.iloc[:, :8].mean(axis=1)
        return cols, df
    if name == "totals_only":
        cols = ["IERQ_pos", "IERQ_persp", "IERQ_sooth", "IERQ_model"]
        df = pd.DataFrame(
            {c: [i * 1.5 + j * 2.25 for i in range(1, 21)] for j, c in enumerate(cols, 1)}
        )
        return cols, df
    if name == "small_block":
        return ["x_1", "x_2", "x_sum"], _frame(
            x_1=range(1, 6), x_2=range(1, 6), x_sum=[float(2 * i) for i in range(1, 6)]
        )
    if name == "wide_range":
        return [f"it{i}" for i in range(1, 7)], _frame(
            it1=[1.0, 2, 3], it2=[2.0, 3, 4], it3=[1.0, 3, 5], it4=[2.0, 2, 4], it5=[1.0, 5, 3],
            it6=[0.0, 40, 80],
        )  # fmt: skip
    if name == "placeholders":
        return ["...1", "...2", "V3", "q_1", "q_2", "q_3"], None
    if name == "no_df":
        return ["pss_1", "pss_2", "pss_3", "pss_total"], None
    raise KeyError(name)


def r_frame(name: str) -> pd.DataFrame:
    one5 = list(range(1, 6))
    if name == "zero_pad":
        names = [f"AQ{i:02d}" for i in range(1, 21)] + [
            "AQ_SUM",
            "CRS_EXP1",
            "CRS_EXP2",
            "CRS_EXP3",
            "id",
        ]
        return _mat(20, 25, 5, names)
    if name == "paradata":
        return _frame(
            psqi_1_response_numeric=one5, psqi_1_response_time=one5,
            psqi_2_response_numeric=one5, psqi_2_response_time=one5,
            psqi_3_response_numeric=one5, psqi_3_response_time=one5,
        )  # fmt: skip
    if name == "loop":
        names = [f"POWER.PP{s}_{i}" for s in range(1, 4) for i in range(1, 5)]
        names += [f"slider_{i}" for i in range(1, 6)]
        return _mat(10, 17, 7, names)
    if name == "word_break":
        v = list(range(1, 5))
        return _frame(
            matwarmth1=v, matwarmth2=v, matwarmth3=v, mataggr1=v, mataggr2=v, mataggr3=v,
            neighdev=v, neighcur=v, x1=v,
        )  # fmt: skip
    if name == "too_few":
        return _frame(a=[1, 2, 3], b=[1, 2, 3])
    if name == "dup_names":
        df = pd.concat(
            [pd.Series([1, 2, 3]), pd.Series([1, 2, 3]), pd.Series([1, 2, 3]), pd.Series([1, 2, 3]),
             pd.Series(["x", "x", "x"], dtype="string")],
            axis=1, ignore_index=True,
        )  # fmt: skip
        df.columns = ["id", "POWER", "POWER", "POWER", "StartDate"]
        return df
    if name == "qualtrics":
        v = [1, 2, 3]
        return _frame(TIPI_1=v, TIPI_2=v, TIPI_3=v, Q5_1=v, Q5_2=v, Q7=v)
    if name == "scale_group":
        v = [1, 2, 3]
        return _frame(Q1=v, Q2=v, Q3=v, zz_a=v, zz_b=v)
    if name == "paradata_qualtrics":
        return pd.DataFrame(
            {
                "StartDate": [1.0], "Q1_DO_order": [1.0], "Q2_TEXT": pd.Series(["x"], dtype="string"),
                "eraitem16timing_First.Click": [1.0], "Q8timing_Page.Submit": [1.0],
                "sleep_time": [1.0], "demo1time_Last.Click": [1.0], "bfi_1": [3.0],
            }
        )  # fmt: skip
    if name == "jspsych":
        return pd.DataFrame(
            {
                "rt": [500.0, 600.0],
                "stimulus": pd.Series(["a", "b"], dtype="string"),
                "response": pd.Series(["j", "f"], dtype="string"),
                "trial_type": pd.Series(["html-keyboard-response"] * 2, dtype="string"),
                "trial_index": pd.Series([0, 1], dtype="Int64"),
                "time_elapsed": [1000.0, 2000.0],
                "internal_node_id": pd.Series(["0.0-0.0", "0.0-1.0"], dtype="string"),
                "browser": pd.Series(["x", "x"], dtype="string"),
                "correct": pd.Series([True, False], dtype="boolean"),
            }
        )
    raise KeyError(name)


def r_machinery(name: str) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    if name == "basic":
        labels = pd.DataFrame(
            {
                "source_file": pd.Series(["s.csv"] * 4, dtype="string"),
                "column_name": pd.Series(
                    ["StartDate", "Q1_DO_order", "Q2_TEXT", "bfi_1"], dtype="string"
                ),
                "label": pd.Series([None] * 4, dtype="string"),
                "codebook_variable": pd.Series([None] * 4, dtype="string"),
                "label_status": pd.Series(["unlabelled"] * 4, dtype="string"),
                "label_method": pd.Series([None] * 4, dtype="string"),
            }
        )
        prev = pd.DataFrame(
            {
                "StartDate": pd.Series(["2024-01-01"], dtype="string"),
                "Q1_DO_order": [1.0],
                "Q2_TEXT": pd.Series(["free text"], dtype="string"),
                "bfi_1": [3.0],
            }
        )
        return labels, {"s.csv": prev}
    labels = pd.DataFrame(
        {
            "source_file": pd.Series(["s.csv"], dtype="string"),
            "column_name": pd.Series(["Q2_TEXT"], dtype="string"),
            "label": pd.Series(["Other, please specify"], dtype="string"),
            "codebook_variable": pd.Series(["Q2_TEXT"], dtype="string"),
            "label_status": pd.Series(["labelled"], dtype="string"),
            "label_method": pd.Series(["rules"], dtype="string"),
        }
    )
    return labels, {"s.csv": pd.DataFrame({"Q2_TEXT": pd.Series(["x"], dtype="string")})}


def r_dup_previews() -> dict[str, pd.DataFrame]:
    loop = pd.concat([pd.Series([float(i)]) for i in range(1, 8)], axis=1, ignore_index=True)
    loop.columns = ["B", "a", "B", "a", "a", "c", "c"]
    return {"clean.csv": _frame(a=[1, 2, 3], b=[1, 2, 3]), "loop.csv": loop}


def r_propagate(name: str) -> pd.DataFrame:
    s = lambda v: pd.Series(v, dtype="string")  # noqa: E731
    if name == "siblings":
        return pd.DataFrame(
            {
                "source_file": s(["s.csv"] * 4),
                "column_name": s(["bfi_1", "bfi_2", "bfi_3", "other_1"]),
                "scale": s(["Big Five Inventory", None, None, None]),
                "scale_confidence": s(["high", None, None, None]),
                "scale_source": s(["manuscript", None, None, None]),
            }
        )
    return pd.DataFrame(
        {
            "source_file": s(["s.csv", "s.csv", "t.csv"]),
            "column_name": s(["bfi_1", "bfi_2", "bfi_3"]),
            "scale": s(["Big Five Inventory", "Something Else", None]),
            "scale_confidence": s(["high", "low", None]),
            "scale_source": s(["manuscript", "self_generated", None]),
        }
    )


def r_synonyms() -> pd.DataFrame:
    s = lambda v: pd.Series(v, dtype="string")  # noqa: E731
    return pd.DataFrame(
        {
            "source_file": s(["a", "a", "a", "a", "b", "b"]),
            "column_name": s(["x1", "x2", "y1", "z1", "x1", "y1"]),
            "scale": s(
                [
                    "emotion recognition",
                    "emotion recognition",
                    "emotion recognition empathic accuracy",
                    "Relationship Satisfaction",
                    "trust scores",
                    "institutional trust",
                ]
            ),
            "confidence": s(["medium"] * 6),
            "scale_source": s(["self_generated"] * 6),
        }
    )


def r_text_scales() -> pd.DataFrame:
    s = lambda v: pd.Series(v, dtype="string")  # noqa: E731
    return pd.DataFrame(
        {
            "scale_name": s(
                ["Perceived Stress Scale", "Big Five Inventory", "grit scale", " ", None]
            ),
            "acronym": s(["PSS", "BFI", "", "X", None]),
            "n_items": s(["10", "", "12", "", None]),
            "administered": s(["yes", "unclear", "no", "yes", None]),
            "confidence": s(["high"] * 5),
        }
    )


def r_likert_labels() -> pd.DataFrame:
    s = lambda v: pd.Series(v, dtype="string")  # noqa: E731
    return pd.DataFrame(
        {
            "source_file": s(["s.csv", "s.csv"]),
            "column_name": s(["q1", "q2"]),
            "value_labels": s([None, '{"1":"Low","2":"Mid","3":"High","9":"Refused"}']),
            "missing_values": s([None, '{"9":"Refused"}']),
        }
    )


def r_likert_columns(kind: str = "observed") -> pd.DataFrame:
    s = lambda v: pd.Series(v, dtype="string")  # noqa: E731
    if kind == "wide":
        return pd.DataFrame(
            {
                "source_file": s(["s.csv"] * 3),
                "column_name": s(["a", "b", "c"]),
                "min": [0.0, 0.0, 0.0],
                "max": [100.0, 100.0, 100.0],
            }
        )
    return pd.DataFrame(
        {
            "source_file": s(["s.csv"] * 4),
            "column_name": s(["a", "b", "c", "d"]),
            "min": [1.0, 1.0, 0.0, 0.0],
            "max": [5.0, 5.0, 60.0, 7.0],
        }
    )


def pattern_check(name: str, acronym: str | None, text: str) -> list[Any]:
    """``.scale_text_pattern()`` and whether it matches *text* (perl, ignore case)."""
    from pytacheck._r import grepl
    from pytacheck.modules._codebook import _scale_text_pattern

    p = _scale_text_pattern(name, acronym)
    hit = bool(grepl(p, text, ignore_case=True, perl=True))
    # R's list(p, hit): two vectors of different types
    return [pd.Series([p], dtype="string"), pd.Series([hit], dtype="boolean")]


def cbc_prev_papers(papers: Any, scenarios: Sequence[str]) -> Any:
    """data_check's output for a real paper list: scenario i's data belongs to paper i."""
    from pytacheck._r import bind_rows

    pids = [str(p.paper_id) for p in papers]
    parts = [cbc_pieces(s, p) for s, p in zip(scenarios, pids, strict=True)]
    previews: dict[str, Any] = {}
    for part in parts:
        for k, v in (part["previews"] or {}).items():
            previews[k] = v  # data_check: a later file of the same name replaces it
    pieces = {
        "table": bind_rows([p["table"] for p in parts]),
        "structure": bind_rows([p["structure"] for p in parts]),
        "previews": previews,
    }
    return _output(papers, pieces, pids)


def report_tables(out: Any) -> list[pd.DataFrame]:
    """The data of the ``scroll_table()`` blocks of a module report (R: ``cbc_report_tables()``)."""
    from pytacheck.report.blocks import ReportTable

    report = out.report if isinstance(out.report, list) else [out.report]
    return [b.data.reset_index(drop=True) for b in report if isinstance(b, ReportTable)]


def cbc_run_tables(scenario: str, **kwargs: Any) -> list[pd.DataFrame]:
    """The report tables of :func:`cbc_run`."""
    return report_tables(cbc_run(scenario, **kwargs))


# -----------------------------------------------------------------------------
# LLM tiers: a deterministic mock of llm() (the R twin is cbc_mock_llm())
# -----------------------------------------------------------------------------


def mock_spec(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / "llm_mock.json").read_text(encoding="utf-8"))[name]


def _with_model(df: pd.DataFrame) -> pd.DataFrame:
    df.attrs["llm"] = {"model": "mock/cbc"}
    return df


def _str_frame(cols: dict[str, list[Any]]) -> pd.DataFrame:
    return pd.DataFrame({k: pd.Series(list(v), dtype="string") for k, v in cols.items()})


def mock_llm(spec: dict[str, Any]) -> Any:
    """An ``llm()`` replacement answering each codebook_check phase by fixed rules."""
    from pytacheck._r import grepl, gsub, sub

    def llm(text: Any, system_prompt: Any = None, type: Any = None, text_col: str = "text",
            model: Any = None, params: Any = None, phase: str | None = None, **_: Any) -> Any:  # fmt: skip
        if phase in (spec.get("error_phases") or []):
            raise RuntimeError("mock LLM error")
        txt = str(text[text_col].iloc[0])
        lines = txt.split("\n")
        if phase == "Identifying scales in the manuscript":
            ts = spec.get("text_scales")
            if ts is None:
                return _with_model(pd.DataFrame())
            return _with_model(_str_frame({f"scales.{k}": v for k, v in ts.items()}))
        if phase == "Parsing codebook":
            defs = [ln for ln in lines if " - " in ln]
            vars_ = [d[: d.index(" - ")] for d in defs]
            labs = [d[d.index(" - ") + 3 :] for d in defs]
            keep = grepl("^[A-Za-z_][A-Za-z0-9_.]*$", vars_) if vars_ else []
            return _with_model(
                _str_frame(
                    {
                        "variables.variable_name": [
                            v for v, k in zip(vars_, keep, strict=True) if k
                        ],
                        "variables.label": [v for v, k in zip(labs, keep, strict=True) if k],
                        "variables.experiment_context": ["" for k in keep if k],
                    }
                )
            )
        if phase == "Matching codebook columns":
            if txt.startswith("Column: "):
                cand = [ln[2:] for ln in lines if ln.startswith("- ")]
                m = spec.get("merge") or {}
                canon = (
                    (cand[0] if cand else None)
                    if m.get("canonical") == "first"
                    else m.get("canonical")
                )
                return _with_model(
                    pd.DataFrame(
                        {
                            "equivalent": pd.Series([m.get("equivalent") is True], dtype="boolean"),
                            "canonical": pd.Series([canon], dtype="string"),
                        }
                    )
                )
            blank = lines.index("")
            cols = sub("^[0-9]+\\. ", "", lines[1:blank])
            vars_ = sub("^[0-9]+\\. ", "", lines[blank + 2 :])
            n = spec.get("match_prefix", 3)

            def key(x: str) -> str:
                return gsub("[^A-Za-z]", "", x).lower()[:n]

            kv = [key(v) for v in vars_]
            pairs = []
            for cn in cols:
                k = key(cn)
                j = [i for i, x in enumerate(kv) if x != "" and x == k]
                if k != "" and j:
                    pairs.append((cn, vars_[j[0]]))
            return _with_model(
                _str_frame(
                    {
                        "matches.column_name": [p[0] for p in pairs],
                        "matches.codebook_variable": [p[1] for p in pairs],
                    }
                )
            )
        if phase == "Matching scales to text":
            grp = [
                ln for ln, g in zip(lines, grepl(": [0-9]+ columns", lines), strict=True)
                if ln.startswith("- ") and g
            ]  # fmt: skip
            pfx = sub("^- ([^:]+): .*$", "\\1", grp) if grp else []
            pn = spec.get("prefix_names") or {}
            return _with_model(
                _str_frame(
                    {
                        "scales.prefix": pfx,
                        "scales.scale_name": [pn[p][0] if p in pn else "" for p in pfx],
                        "scales.confidence": [pn[p][1] if p in pn else "low" for p in pfx],
                    }
                )
            )
        if phase == "Labelling scales":
            first = lines[1][2:] if lines[1].startswith("- ") else lines[1]
            cs = (spec.get("constructs") or {}).get(first)
            return _with_model(
                _str_frame(
                    {
                        "construct": ["" if cs is None else cs[0]],
                        "confidence": ["medium" if cs is None else cs[1]],
                    }
                )
            )
        raise RuntimeError(f"unexpected phase {phase}")

    return llm


def cbc_llm_run(scenario: str | None, spec: str, prev: Any = None, **kwargs: Any) -> Any:
    """:func:`cbc_run` with the LLM on and ``llm()`` mocked by the named spec."""
    from pytacheck.module import module_run
    from pytacheck.modules import _codebook
    from pytacheck.utils import local_options

    if prev is None:
        assert scenario is not None
        prev = cbc_prev(scenario)
    orig = _codebook._llm
    _codebook._llm = mock_llm(mock_spec(spec))
    try:
        with local_options({"metacheck.llm.use": True}):
            return module_run(prev, "codebook_check", **kwargs)
    finally:
        _codebook._llm = orig
