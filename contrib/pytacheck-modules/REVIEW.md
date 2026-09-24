# Review checklist (maintainers)

A review is a careful read of the exact files that will be installed, at the
commit the index lists. Record the result in `packs/<name>.json` (create the
file next to a folder pack, holding only these maintainer fields):

```json
{"reviewed": "YYYY-MM-DD", "reviewed_tree_sha256": "<the tree hash you reviewed>"}
```

The tree hash is the pack's `tree_sha256` in `index.json` for the commit you
reviewed (`store build` also prints it when a review is out of date, and
`pytacheck pack show <name>` shows it after installing). A review holds
only for those exact files: when the pack changes, `store build` clears the
review date, and `store build --check` (CI) fails until a maintainer reviews
the new files and updates both fields.

## Must pass

- [ ] **CI is green**: `pytacheck pack check` (no errors) and
      `pytacheck store build . --check`.
- [ ] **Licence**: an OSI-approved licence in `pack.json` (or CC0-1.0 for
      preset-only packs); code adapted from elsewhere keeps its licence and
      credits its authors.
- [ ] **Readable code**: no obfuscation (encoded strings decoded at run time,
      minified code, unusual unicode), no code downloaded or generated at run
      time, no `eval`/`exec`/`compile`, no `subprocess`/`os.system`, no native
      code (`ctypes`), no writes outside temporary files. The consent card's
      "Look closely at" list is a good starting point; read every file anyway.
- [ ] **Declared requirements**: `requires=["network"]` on every module that
      contacts a service (including through `pytacheck.db` / `pytacheck.archives`
      helpers), `requires=["llm"]` on every module that uses an LLM, and every
      extra package in `dependencies`.
- [ ] **Network and LLM use justified**: the README says which services are
      contacted, what is sent (never more of the paper than needed), and why.
      No telemetry.
- [ ] **Validation**: the module says how it was validated (papers, true and
      false positives, misses, a reference) or says clearly that it has not been
      validated. Numbers in `validation={...}` match the prose.
- [ ] **Honest output**: titles, summary texts and reports describe what the
      module actually detects, and traffic lights are not alarmist (`red` means
      a likely problem).
- [ ] **Scope**: a pack does one coherent thing; module names do not clash with
      metacheck's built-in names.

## Should

- [ ] Tests in `tests/` cover positive, negative and edge cases.
- [ ] Presets refer to other packs' modules as `pack::name`.
- [ ] A `.R` twin, if present, behaves like the `.py` module.

## Yanking

Set `"yanked": "<reason>"` in `packs/<name>.json` to warn everyone who installs
or updates the pack (for example after a serious bug or a security problem).
pytacheck shows the notice on the consent card. Remove it to un-yank.
