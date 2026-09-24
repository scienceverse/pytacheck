# fields

Presets that choose metacheck's built-in checks for a field of research.
This pack has **no code**, only lists of modules, so it is the easiest kind
of pack to review and trust.

| preset | modules |
|---|---|
| `fields::general` (also `fields`) | metacheck's default report + `ethics_check`, `open_practices` |
| `fields::psychology` | general + `all_p_values`, `causal_claims` |
| `fields::medicine` | general without `ref_replication` (FLoRA covers psychology); add `clinical_trials::only` for trial registration numbers (`clinical_trials`, its default, extends metacheck's default and so brings `ref_replication` back) |
| `fields::open-science` | `prereg_check`, `open_practices`, `repo_check`, `code_check`, `data_check`, `codebook_check`, `psychds_check` |

```bash
pytacheck init --preset fields::psychology --yes     # make it your default
pytacheck presets fields::medicine                   # see what it runs
pytacheck presets fields::medicine --as-r            # the same run in metacheck (R)
```

Every module named here is a metacheck module, so the presets also describe
metacheck runs (`--as-r`). Some modules are still being ported to pytacheck;
until then they report "This module failed to run".

Suggestions for other fields are welcome: add a preset to `pack.json` and
open a pull request.

Licence: CC0-1.0.
