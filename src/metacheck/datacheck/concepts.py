"""A local classifier for ``data_check``'s column concepts (pytacheck only).

metacheck asks an LLM (under ``llm_use(TRUE)``) for the concept of each column its
rules leave blank; without an LLM those columns keep no concept. pytacheck ships a
fine-tuned multilingual encoder (XLM-RoBERTa-large as ONNX with 8-bit weights, ~840 MB,
downloaded once from the Hugging Face Hub) that answers the same question offline, in
~0.1 s per column on eight CPU threads and ~2 GB of memory. Its arithmetic stays fp32, so
its confidences are the trained model's. Trained the same way without the evaluation
repositories, it scores a repository-weighted F1 of 0.795 on 98 ResearchBox boxes and
0.736-0.761 (two training runs) on 86 OSF/GitHub/Zenodo repositories, against 0.784 /
0.740 for Muse Spark 1.3 and 0.762 / 0.694 for Gemini 3.5 Flash-Lite answering
metacheck's prompt; as the first stage of a cascade that sends the 36-38% of columns it
is least sure of to Muse Spark, 0.852 / 0.786-0.801.

``concepts`` (the ``data_check()`` argument, the ``metacheck.concepts`` option or
``METACHECK_CONCEPTS``) picks the tier:

- ``"classifier"`` (default): the classifier fills the blanks; no LLM call for concepts.
- ``"cascade"``: as ``"classifier"``, but under ``llm_use(TRUE)`` the columns the
  classifier is least sure of (confidence below ``metacheck.concepts.threshold`` /
  ``METACHECK_CONCEPT_THRESHOLD``, default 0.92) go to the LLM; where the LLM gives no
  answer the classifier's stands.
- ``"llm"``: metacheck's behaviour (the LLM tier under ``llm_use(TRUE)``, else rules only).
- ``"rules"``: rules only.

Without the ``concepts`` extra (``pip install "metacheck[concepts]"``) or the model,
``"classifier"`` and ``"cascade"`` fall back to ``"llm"``. ``metacheck.concepts.model`` /
``METACHECK_CONCEPT_MODEL`` names another model: a local directory or ``repo@revision``.

The classifier reads more than the LLM prompt: the column name, file name and
neighbouring columns, ``data_check``'s column statistics and up to 40 distinct values
out of 200 evenly spaced ones. :func:`concept_text` rebuilds the text it was trained
on byte for byte (see ``tests/datacheck_concepts``).
"""

from __future__ import annotations

import functools
import json
import math
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from metacheck._env import env_get

MODES = ("classifier", "cascade", "llm", "rules")
MODEL = "scienceverse/datacheck-concepts@v1"
DEFAULT_THRESHOLD = 0.92  # the cascade threshold chosen on ResearchBox dev (-> Muse)
INSTALL_HINT = 'pip install "metacheck[concepts]"'
_FILES = ("model.onnx", "tokenizer.json", "concept_model.json")
_N_VALUES = 200  # values sampled per column
_BATCH = 16


def concept_mode(value: str | None = None) -> str:
    """The concept tier: *value*, else the option, else ``METACHECK_CONCEPTS``, else ``"classifier"``."""
    from metacheck.utils import get_option

    mode = value or get_option("metacheck.concepts") or env_get("CONCEPTS")
    mode = str(mode).strip().lower() if mode else "classifier"
    if mode not in MODES:
        raise ValueError(f"`concepts` must be one of {', '.join(MODES)}, not {mode!r}")
    return mode


def concept_threshold() -> float:
    """The cascade's confidence threshold (option, env var, then :data:`DEFAULT_THRESHOLD`)."""
    from metacheck.utils import get_option

    value = get_option("metacheck.concepts.threshold") or env_get("CONCEPT_THRESHOLD")
    try:
        return float(value) if value is not None else DEFAULT_THRESHOLD
    except (TypeError, ValueError):
        return DEFAULT_THRESHOLD


def model_source() -> str:
    from metacheck.utils import get_option

    return str(get_option("metacheck.concepts.model") or env_get("CONCEPT_MODEL") or MODEL)


def concept_threads() -> int | None:
    """``METACHECK_CONCEPT_THREADS``: onnxruntime's intra-op threads, or ``None`` for its default."""
    value = env_get("CONCEPT_THREADS")
    return int(value) if value is not None else None


@functools.cache
def classifier_available() -> bool:
    """Whether the ``concepts`` extra (onnxruntime, tokenizers, huggingface_hub) is installed."""
    try:
        import huggingface_hub  # noqa: F401
        import onnxruntime  # noqa: F401
        import tokenizers  # noqa: F401
    except ImportError:
        return False
    return True


class ConceptClassifier:
    """An exported concept model: ``model.onnx`` (``input_ids``, ``attention_mask`` ->
    ``probs``), ``tokenizer.json`` and ``concept_model.json`` (labels, ``max_len``, ``pad_id``)."""

    def __init__(self, path: str | os.PathLike[str], name: str | None = None) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        path = Path(path)
        meta = json.loads((path / "concept_model.json").read_text(encoding="utf-8"))
        self.labels: list[str] = list(meta["labels"])
        self.name = name or str(meta.get("name") or path.name)
        self._pad = int(meta["pad_id"])
        self._tok = Tokenizer.from_file(str(path / "tokenizer.json"))
        self._tok.no_padding()
        self._tok.enable_truncation(max_length=int(meta["max_len"]))
        so = ort.SessionOptions()
        threads = concept_threads()
        if threads:
            so.intra_op_num_threads = threads
        self._sess = ort.InferenceSession(
            str(path / "model.onnx"), so, providers=["CPUExecutionProvider"]
        )

    def predict(self, texts: Sequence[str]) -> list[tuple[str, float]]:
        """The most probable concept and its probability, per text."""
        import numpy as np

        if not texts:
            return []
        ids = [e.ids for e in self._tok.encode_batch(list(texts))]
        order = sorted(range(len(ids)), key=lambda i: len(ids[i]))
        out: list[tuple[str, float]] = [("other", 0.0)] * len(ids)
        for start in range(0, len(order), _BATCH):
            batch = order[start : start + _BATCH]
            width = max(len(ids[i]) for i in batch)
            x = np.full((len(batch), width), self._pad, dtype=np.int64)
            mask = np.zeros((len(batch), width), dtype=np.int64)
            for row, i in enumerate(batch):
                x[row, : len(ids[i])] = ids[i]
                mask[row, : len(ids[i])] = 1
            probs = self._sess.run(["probs"], {"input_ids": x, "attention_mask": mask})[0]
            for row, i in enumerate(batch):
                k = int(probs[row].argmax())
                out[i] = (self.labels[k], float(probs[row][k]))
        return out


class _Once:
    def __init__(self) -> None:
        self.done = False

    def say(self, msg: str) -> None:
        from metacheck.utils import message

        if not self.done:
            message(msg)
            self.done = True


_unavailable_notice = _Once()


def load_classifier(source: str | None = None) -> ConceptClassifier | None:
    """The concept classifier, or ``None`` (with a one-time message) when it cannot be loaded."""
    source = source or model_source()
    if not classifier_available():
        _unavailable_notice.say(
            "data_check: the local concept classifier is not installed "
            f"({INSTALL_HINT}); concepts use metacheck's rules/LLM tier."
        )
        return None
    try:
        return _load(source)
    except Exception as e:
        _unavailable_notice.say(
            f"data_check: could not load the concept model {source} ({type(e).__name__}: {e}); "
            "concepts use metacheck's rules/LLM tier."
        )
        return None


@functools.cache
def _load(source: str) -> ConceptClassifier:
    path = Path(source).expanduser()
    if path.is_dir():
        return ConceptClassifier(path)
    from huggingface_hub import snapshot_download

    from metacheck.config import cache_dir

    repo, _, revision = source.partition("@")
    kw: dict[str, Any] = {
        "repo_id": repo,
        "revision": revision or None,
        "allow_patterns": list(_FILES),
        "cache_dir": str(cache_dir("models")),
    }
    try:
        local = snapshot_download(**kw, local_files_only=True)
    except Exception:
        from metacheck.utils import message

        message(f"data_check: downloading the concept model {source} (~840 MB, once) ...")
        local = snapshot_download(**kw)
    return ConceptClassifier(local, name=source)


# -- the classifier's input text ---------------------------------------------------------


def sample_values(values: Sequence[str | None]) -> list[str]:
    """Up to 200 evenly spaced non-missing values, each cut to 200 characters.

    R: ``v <- as.character(v[!is.na(v)]); v[unique(round(seq(1, n, length.out = 200)))]``,
    with *values* the column's ``as.character()`` (``None`` for ``NA``).
    """
    v = [s for s in values if s is not None]
    n = len(v)
    if n > _N_VALUES:
        by = (n - 1) / (_N_VALUES - 1)
        pos = [1.0] + [1 + k * by for k in range(1, _N_VALUES - 1)] + [float(n)]
        v = [v[i - 1] for i in dict.fromkeys(round(p) for p in pos)]
    return [s[:200] for s in v]


def stat_text(x: Any) -> str:
    """A column statistic as the training data printed it.

    The statistics went through jsonlite (``digits = 6``) and back, then ``%.4g``:
    whole numbers print as integers, others with four significant digits.
    """
    if x is None or isinstance(x, bool):
        return "NA"
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "NA"
    if not math.isfinite(v):
        return "NA"
    a = abs(v)
    if 1e-5 < a < 2147483647:
        s = f"{v:.6f}".rstrip("0").rstrip(".")
    else:
        digits = math.ceil(min(17, max(1.0, math.log10(a) if a > 0 else -math.inf) + 6))
        s = f"{v:.{digits}g}"
    if any(c in s for c in ".eE"):
        return f"{float(s):.4g}"
    return str(int(s))


def concept_text(
    column_name: str,
    source_file: str | None,
    neighbours: Sequence[str],
    stats: Mapping[str, Any],
    values: Sequence[str] | None,
    sample: str,
) -> str:
    """The classifier's input for one column.

    *neighbours*: the three column names before it and the two after it in its file;
    *stats*: its ``n``, ``n_missing``, ``n_unique``, ``min``, ``max``, ``mean``, ``sd`` and
    final ``representation``; *values*: :func:`sample_values` of the column (``None`` when
    the file was not read in full); *sample*: its ``sample_values``.
    """
    if values:
        uniq = [v[:40] for v in dict.fromkeys(values)]
        vtxt = f"{len(uniq)} distinct in sample: " + " ; ".join(uniq[:40])
    else:
        vtxt = sample
    base = os.path.basename(str(source_file if source_file is not None else ""))
    s = stats
    return (
        f"column: {column_name} | file: {base} | neighbours: {', '.join(neighbours)} | "
        f"n={stat_text(s.get('n'))} missing={stat_text(s.get('n_missing'))} "
        f"unique={stat_text(s.get('n_unique'))} min={stat_text(s.get('min'))} "
        f"max={stat_text(s.get('max'))} mean={stat_text(s.get('mean'))} "
        f"sd={stat_text(s.get('sd'))} type={s.get('representation')} | values: {vtxt}"
    )
