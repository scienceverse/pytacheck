# Contributing a pack

Thank you! A pack is a folder with a `pack.json` and, optionally, module files.
It can hold presets only (lists of existing checks, no code) or new checks.

## 1. Make the pack

```bash
pip install "pytacheck @ git+https://github.com/thesanogoeffect/pytacheck@claude/pytacheck-metacheck-fork-0x7q73"   # not on PyPI yet
pytacheck pack new my-pack          # a working example with a test and a workflow
pytacheck pack install ./my-pack    # use it live while you develop
pytacheck pack check ./my-pack      # the same checks this store's CI runs
```

The author guide in pytacheck's
[docs/MODULES.md](https://github.com/thesanogoeffect/pytacheck/blob/claude/pytacheck-metacheck-fork-0x7q73/docs/MODULES.md)
explains modules, `pack.json`, presets and validation.

Before you submit:

- [ ] `pytacheck pack check` reports no errors.
- [ ] `pack.json` has a clear `title`, `description`, `authors`, `fields` and an
      OSI-approved `license` (packs run inside AGPL-3.0 software; `CC0-1.0` suits
      preset-only packs).
- [ ] Each module describes how it was validated in a `<validation>` block, or
      says plainly that it has not been validated yet. Numbers go in
      `validation={...}` so users see PPV and sensitivity.
- [ ] Modules that call online services declare `requires=["network"]`; modules
      that use an LLM declare `requires=["llm"]`. Say in the README which
      services they contact and what they send.
- [ ] Extra Python dependencies are listed in `dependencies` (prefer none).
- [ ] There are tests (in `tests/`) for the main cases and edge cases.
- [ ] The code is readable: no obfuscation, no downloaded or generated code,
      no `eval`/`exec`, no subprocesses (explain any exception).

Do not add `reviewed` or `yanked` to `pack.json`: maintainers set them.

## 2a. Submit it as a folder (easiest)

Open a pull request that adds `packs/<name>/` (the folder name must equal the
`name` in `pack.json`). You can do this entirely in the browser: fork this
repository, open `packs/`, "Add file" > "Upload files", and drop in your pack's
files.

After the merge, CI lists the pack at the commit that last changed its folder.
To release an update, open another pull request changing the folder (and bump
`version`).

## 2b. Or list your own repository

Keep the pack in your own repository (the pack folder is the repository root,
or a subfolder given as `subdir`) and open a pull request adding
`packs/<name>.json`:

```json
{"name": "my-pack", "source": {"github": "you/my-pack", "rev": "<full 40-character commit>"},
 "version": "0.1.0"}
```

`version` is optional; when given it must match your `pack.json` at that commit.
`gitlab` and `codeberg` sources work the same way. To release an update, open a
pull request changing `rev` (and `version`).

## What happens next

CI checks every changed pack with `pytacheck pack check` (a pack listed from
your own repository is fetched at its `rev` first: `pytacheck pack check
packs/<name>.json`; its own `tests/` are not run, so run them in your
repository's CI) and the whole store (`pytacheck store build . --check`). A maintainer reviews the code with
[REVIEW.md](REVIEW.md), may ask for changes, and records the review date. You
keep the copyright; the pack keeps your licence.
