# Raw snapshots of base (package SNAP)

The rewrite (docs/design/ARCHITECTURE.md) changes how pytacheck computes its
results, not the results. These snapshots record what base gives, so that
each rewrite step can be compared with it: gate G5 (`tests/migration/`) and
the differential suites (H0-8, `tests/core/test_diff_*.py`) use them as the
oracle. They are raw outputs of this tree, not R's goldens: R's goldens live in
`parity/golden/`.

## What is recorded

Each *set* is a folder here. Its cases are defined in `inputs.py`.

| Set | Cases | What |
|---|---:|---|
| `modules` | 496 | the 19 offline paper modules on the 21 accuracy papers, on the demo paper, on the psychsci list and on the spike's 3-paper list; the 4 repository modules on the 10 accuracy repositories |
| `fixtures` | 76 | the two fixtures below, for the 19 paper modules |

The inputs are pinned in `inputs.py`, so a new row in
`parity/accuracy/matrix.toml` does not change the oracle. The network and LLM
modules (causal_claims, ref_pubpeer, power, prereg_check, reg_check,
psychds_check, reproducibility_check) are not in `modules`: offline they only
fail or return `na`. Their parity cases cover them until HARNESS-NET records
real-paper runs. The differential suites and the parity cases get sets of their
own later (SNAP-2 and SNAP-3), in the `xz` layout.

Each case runs as `python -m parity accuracy` runs a module: in UTC, without
credentials, network or R, with metacheck's argument defaults, numbered paper
ids and a fresh cache folder. Each case reads its own input.

## The fixtures

**`same_id`** pins F6 (repeated paper ids have one rule). The input is
debruine-fret and debruine-child as one list, both with the id `X`. The
expected output is base's output on the same list with F6's ids already
applied (`X` and `X~2`). The plan's wording, "the per-paper snapshots
concatenated", does not say what a list's traffic light, summary text or report
is, so the expected output is recorded on the resolved list instead.

**`duplicate_paragraph`** pins V7 (exact duplicate rows give one output row).
The input is debruine-fret with body paragraph 20 repeated right after itself,
every column equal. That paragraph is one sentence in the results, with an F
test and three p-values, so it is also one repeated row. The expected output
is base's output on debruine-fret without the repeat.

For each fixture there are two cases per module:

- `<module>.<fixture>`: the expected output. The rewrite runs the fixture's
  input and must give this once the rule has landed (CORE-1b).
- `<module>.<fixture>.before`: base's own output on the fixture's input, as a
  record of what the rule changes. Until CORE-1b lands, this is the one a
  migration step must equal.

The recorder prints which modules base gets wrong on each fixture. On
2026-09-28 that is 13 of 19 for `same_id` (ref_consistency raises), and 3 for
`duplicate_paragraph` (all_p_values, stat_p_exact, stat_p_nonsig count the
repeated p-values).

## The format

A record is `{"ok": true, "value": ..., "frames": ...}` or
`{"ok": false, "error": "Type: message"}`. `value` is the parity encoding
(`parity/canonical.py`), with the checkout written `<repo>`. `frames` keeps
what that encoding drops, for each DataFrame and Series in the value, by path
(`$.table`): the dtypes, an index other than `0..n-1`, and which missing value
an object column holds (`None`, `NaN`, `NA`, `NaT`; the encoding writes `null`
for all four). So a dtype or a missing-value change shows up too.

Each set folder holds:

- `INDEX`: one line per case, `<digest> <bytes> <case id>`, sorted by case id.
  The digest is the first 16 hex digits of the SHA-256 of the record's compact
  JSON. A changed case is a changed line, whatever the layout.
- layout `json` (`modules`, `fixtures`): one `<module>.json` per module,
  indented with one value per line, so a pull request shows the changed cells.
- layout `xz` (the large suites, later): `records.jsonl.xz`, each distinct
  record once. Many cases give the same output, so this is small; review it
  through `INDEX` and `--diff`.

Loading a set checks every record against its INDEX digest, so a hand edit
fails.

## Recording

Record with **Python 3.12 on Linux**, the version CI's primary jobs use.
Python's own error messages differ between versions, and they are part of the
records. The recorder refuses to write here under any other version.

```sh
uv run --python 3.12 python scripts/record_snapshots.py               # every set
uv run --python 3.12 python scripts/record_snapshots.py --only modules
```

`uv run --python 3.12` rebuilds the project's `.venv` with Python 3.12. To
keep your usual `.venv`, give uv another folder for this one, for example
`UV_PROJECT_ENVIRONMENT=../pytacheck-py312 uv run --python 3.12 ...`.

The other modes run under any version:

```sh
uv run python scripts/record_snapshots.py --check   # record twice, compare the bytes
uv run python scripts/record_snapshots.py --diff    # compare a new recording with the committed one
uv run python scripts/record_snapshots.py --list    # the sets and their case counts
```

- `--check` records twice, in two processes with `PYTHONHASHSEED` 12345 and
  777, and fails unless the files are byte-identical. Under Python 3.12 on
  Linux it also fails when the recording differs from the committed snapshots.
- `--diff` lists the cases added, removed and changed, with the paths that
  differ (`value.table.text[3]` is row 3 of the table's `text` column) and a
  diff of each changed record. `--from DIR` compares a recording made earlier
  with `--out DIR`.
- `--cases GLOB` limits a mode to some cases, for example
  `--diff --cases 'ethics_check.*'`.

## When snapshots change

A pull request that changes an output re-records the sets it affects, in the
same pull request, and its review reads the `INDEX` lines and the `--diff`
output. A Band A change also needs a deviation row (docs/design/FIDELITY.md).
A `uv.lock` update of pandas, numpy or regex can change dtype names or error
texts: re-record in that pull request too.

## Using the oracle

```python
from tests.snapshots import oracle

@pytest.mark.parametrize("case_id", oracle.cases("modules", module="ethics_check"))
def test_ethics_check(case_id: str) -> None:
    oracle.check("modules", case_id)   # runs the case and compares with its snapshot
```

`oracle.assert_equal(set, case_id, value)` compares a value the test computed
itself. The comparison is exact: the records must be byte-identical. Once
HARNESS-v2's `parity/canon.py` exists (H0-17), it registers a `canon`
comparator (`oracle.register_comparator`), and `through="canon"` compares
through it, listing the raw differences as well.
