"""Raw outputs do not depend on the hash seed (FIDELITY.md §2.2, rule 3; H0-19).

Iterating a set of strings follows ``PYTHONHASHSEED``, so a module that emits
"hash order" would give another row order in every process. This runs the
accuracy report's inputs (parity/accuracy/matrix.toml) in two interpreters with
different seeds and requires the same bytes, encoded as the harness encodes them
for the parity comparison (``canonical``).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from parity.cases import ROOT

# what one interpreter runs: every output of the matrix, one id line and one JSON line each
_DUMP = """
import sys
import orjson
from parity import accuracy

outputs = accuracy.load_matrix()
results = accuracy.run_python(outputs)
with open(sys.argv[1], "wb") as f:
    for o in outputs:
        f.write(o.id.encode() + b"\\n" + orjson.dumps(results[o]) + b"\\n")
"""

_SEEDS = ("0", "1")


def _start(seed: str, code: str, *args: str, state: Path | None = None) -> subprocess.Popen[bytes]:
    env = {**os.environ, "PYTHONHASHSEED": seed}
    if state is not None:  # the fixture points these at directories that do not exist yet
        state.mkdir(parents=True, exist_ok=True)
        env.update(
            PYTACHECK_CACHE_DIR=str(state / "cache"),
            PYTACHECK_DATA_DIR=str(state / "data"),
            PYTACHECK_LOG=str(state / "log.jsonl"),
        )
        (state / "cache").mkdir(exist_ok=True)
    cmd = [sys.executable, "-c", code, *args]
    return subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _finish(proc: subprocess.Popen[bytes]) -> bytes:
    out, err = proc.communicate()
    assert proc.returncode == 0, err.decode(errors="replace")
    return out


def test_raw_outputs_are_the_same_under_two_hash_seeds(tmp_path: Path) -> None:
    files = [tmp_path / f"seed{seed}.jsonl" for seed in _SEEDS]
    procs = [
        _start(seed, _DUMP, str(f), state=tmp_path / f"state{seed}")
        for seed, f in zip(_SEEDS, files, strict=True)
    ]
    for proc in procs:
        _finish(proc)
    first, second = (f.read_bytes() for f in files)
    if first != second:
        a, b = first.splitlines(), second.splitlines()
        bad = [a[i] for i in range(0, min(len(a), len(b)), 2) if a[i : i + 2] != b[i : i + 2]]
        differing = ", ".join(x.decode() for x in bad[:10]) or "the number of outputs"
        raise AssertionError(
            f"PYTHONHASHSEED={_SEEDS[0]} and ={_SEEDS[1]} give other outputs for {len(bad)} "
            f"of {len(a) // 2}: {differing}"
        )
    # the modules ran: most outputs are a value, not an error (434 of 439 at the time of writing)
    n = len(first.splitlines()) // 2
    ran = first.count(b'{"ok":true')
    assert n > 100 and ran >= 0.9 * n, f"only {ran} of {n} outputs ran without an error"


def test_the_seed_reaches_the_interpreters() -> None:
    """The test above means something only if the seed changes how a set iterates."""
    code = "print(*{'a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'})"
    procs = [_start(seed, code) for seed in _SEEDS]
    first, second = (_finish(proc) for proc in procs)
    assert first != second
