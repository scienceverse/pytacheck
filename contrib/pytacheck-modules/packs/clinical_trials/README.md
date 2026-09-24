# clinical_trials

Checks for clinical trial reports, as a [pytacheck](https://github.com/thesanogoeffect/pytacheck) pack.

| module | section | what it does |
|---|---|---|
| `trial_registration` | method | Finds trial registration numbers (ClinicalTrials.gov, ISRCTN, EudraCT/CTIS, ANZCTR, ChiCTR, DRKS, CTRI), links them to their registry, and flags papers that describe a trial but report no number. |

```bash
pytacheck pack install clinical_trials
pytacheck run paper.json --preset clinical_trials           # metacheck's defaults + this check
pytacheck run paper.json -m clinical_trials::trial_registration
```

The module is a plain text search: it needs no network access and does not
check that the registry record exists or matches the paper. It has **not
been validated yet**; see the `<validation>` block in its details. A
validation study (papers coded by hand, with true/false positives and
misses) is very welcome.

Tests: `pytest tests` from this folder.

Licence: MIT (see `LICENSE`).
