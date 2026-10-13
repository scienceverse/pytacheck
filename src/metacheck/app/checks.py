"""Which checks the app runs: five fixed sets, and the checks a person may choose from.

The page starts with :func:`selected_checks` ticked. Under "Choose the checks" a person can
tick any check of :func:`choices` instead, or start from an installed preset
(:func:`paper_presets`). What comes back from the page is never trusted:
:func:`chosen_checks` keeps only the checks this server offers. A check of ``NEVER`` is
never offered, on this computer or on a shared server.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from functools import cache
from typing import Any

#: Extra checks that need no network and no external service and finish in well under a second on
#: the demo paper (measured one by one).
FAST_OFFLINE: dict[str, str] = {
    "stat_check": "recomputes p values from test statistics; 0.2 s",
    "funding_check": "text search only",
    "coi_check": "text search only",
    "ethics_check": "text search only",
    "open_practices": "text search only",
    "ref_accuracy": "uses the reference matches in the paper file, no lookup; left out of a "
    "paper that has none (only the bundled demo JSON has them)",
    "ref_replication": "uses a database that ships with the package",
    "ref_retraction": "uses a database that ships with the package",
    "ref_consistency": "compares citations with the reference list",
    "ref_miscitation": "uses a list that ships with the package",
    "figure_table_refs": "every figure and table is referred to in the text",
    "author_check": "ORCIDs, affiliations and a corresponding author",
    "license_check": "the license the paper is published under",
    "ref_summary": "summarises the reference checks above (runs last)",
}

#: What the "online checks" box adds: they look things up on the web and finish in
#: about 15 s or less on the demo paper (3.6 s and 0.3 s).
ONLINE: dict[str, str] = {
    "prereg_check": "reads the linked preregistrations",
    "ref_pubpeer": "asks PubPeer about the cited papers",
}

#: What the "shared data files" box adds. It is experimental, needs no AI and no key, and takes
#: minutes on the demo paper (227 s cold, 39 s with the download cache), so it runs after the
#: other checks and its result comes later.
DATA: dict[str, str] = {
    "data_check": "downloads the paper's shared data files (OSF and other repositories) and checks them",
}

#: Checks the demo never runs, each with the reason.
NEVER: dict[str, str] = {
    "code_check": "downloads code repositories (333 s on the demo paper)",
    "repo_check": "downloads repository listings (46 s on the demo paper)",
    "codebook_check": "downloads data files and needs a key for an external service",
    "psychds_check": "downloads data files and needs a key for an external service",
    "reproducibility_check": "downloads data files",
    "reg_check": "needs a local RegCheck server",
    "causal_claims": "sends the title and abstract to an external text-classification server",
}

#: Fast and offline, so they would fit the default set, but left out because they add
#: little to the page. Kept apart from NEVER: adding one is a choice, not a limit.
NOT_SHOWN: dict[str, str] = {
    "all_p_values": "lists p values; the p value checks above cover them",
    "all_urls": "lists links only; has no result of its own",
    "coi_check_oi": "variant of coi_check",
    "funding_check_oi": "variant of funding_check",
}


@cache
def validated_checks() -> tuple[str, ...]:
    """The built-in modules whose status label is ``validated`` (read from the registry)."""
    from metacheck.status import status_table

    table = status_table()
    prefix = "metacheck::"
    return tuple(
        str(ref).removeprefix(prefix)
        for ref, label in zip(table["module"], table["label"], strict=True)
        if str(ref).startswith(prefix) and label == "validated"
    )


@cache
def status_labels() -> dict[str, str]:
    """Built-in module name -> status label (``validated``, ``experimental``, ...)."""
    from metacheck.status import status_table

    table = status_table()
    return {
        str(ref).removeprefix("metacheck::"): str(label)
        for ref, label in zip(table["module"], table["label"], strict=True)
    }


def selected_checks(online: bool = False) -> list[str]:
    """The checks to run, in the order they run (``ref_summary`` last)."""
    names = [*validated_checks(), *FAST_OFFLINE]
    if online:
        names = [*(n for n in names if n != "ref_summary"), *ONLINE, "ref_summary"]
    return names


#: the value of the preset choice that means the app's own checks
APP_DEFAULT = ""
APP_DEFAULT_LABEL = "The app's checks"
#: the ref of a module in the built-in pack
BUILTIN = "metacheck::"


def _bare(ref: str) -> str:
    return ref.removeprefix(BUILTIN)


def _descriptions() -> dict[str, str]:
    """Built-in check -> what it does, for the checks a person may choose."""
    out = dict.fromkeys(validated_checks(), "validated")
    for group in (FAST_OFFLINE, ONLINE, NOT_SHOWN):
        out.update(group)
    return out


def _builtin_order() -> list[str]:
    """The built-in checks a person may choose, in the order they run (``ref_summary`` last)."""
    names = [n for n in _descriptions() if n != "ref_summary"]
    return [*names, "ref_summary"]


def title_of(name: str) -> str:
    """The title of a check as the results table shows it (its name if it has none)."""
    from metacheck.module import module_find

    try:
        spec = module_find(name)
    except Exception:
        return name
    return str(getattr(spec, "title", "") or name)


def choices(hosted: bool = False, extra: Iterable[str] = ()) -> list[tuple[str, str]]:
    """``(label, name)`` of every check a person may tick, in the order they run.

    ``extra`` adds the modules of installed packs that :func:`paper_presets` found (refs like
    ``tue::thesis_check``); a shared server takes none, as it runs only built-in checks.
    """
    validated = set(validated_checks())
    out = [
        (f"{title_of(n)} ({n}){', validated' if n in validated else ''}", n)
        for n in _builtin_order()
    ]
    if not hosted:
        known = {n for _, n in out}
        for ref in extra:
            if ref not in known:
                known.add(ref)
                out.insert(len(out) - 1, (f"{title_of(ref)} ({ref})", ref))
    return out


def chosen_checks(
    chosen: Sequence[str] | None, hosted: bool = False, extra: Iterable[str] = ()
) -> list[str]:
    """The ticked checks this server offers, in the order they run; ``None`` (nothing sent,
    as from the self-test) is :func:`selected_checks`. A name that is not offered is dropped:
    the value comes from the browser, which may send anything."""
    if chosen is None:
        return selected_checks()
    ticked = {_bare(str(n)) if str(n).startswith(BUILTIN) else str(n) for n in chosen}
    return [n for _, n in choices(hosted, extra) if n in ticked]


@cache
def _paper_presets() -> tuple[
    tuple[str, str, tuple[str, ...], bool, tuple[tuple[str, Any], ...]], ...
]:
    """Every installed preset that checks a paper: ``(ref, description, modules, data, args)``.

    A preset is left out when it cannot be expanded, when it checks a data package (that is
    the other page), or when it lists a built-in check this page never runs. ``data_check``
    in a preset ticks the data box instead of being a check of its own.
    """
    from metacheck.module import ModuleError
    from metacheck.presets import expand, preset_list

    offered = set(_descriptions())
    found = []
    table = preset_list()
    for ref, description in zip(table["ref"], table["description"], strict=True):
        ref = str(ref)
        try:
            entries = expand(ref)
        except ModuleError:
            continue
        modules: list[str] = []
        args: list[tuple[str, Any]] = []
        data = False
        usable = True
        for module, module_args in entries:
            module = str(module)
            if module.startswith("datapackage::"):
                usable = False
                break
            name = _bare(module) if module.startswith(BUILTIN) else module
            if name in DATA:
                data = True
                continue
            if "::" not in name and name not in offered:
                usable = False  # a built-in check the app never runs (slow, or needs a key)
                break
            modules.append(name)
            if module_args:
                args.append((name, module_args))
        if usable and modules:
            text = str(description) if description == description and description else ""
            found.append((ref, text, tuple(modules), data, tuple(args)))
    return tuple(found)


def paper_presets(hosted: bool = False) -> list[tuple[str, str]]:
    """``(label, ref)`` for the preset choice: the app's checks first, then installed presets.

    A shared server offers only presets made of built-in checks.
    """
    out = [(APP_DEFAULT_LABEL, APP_DEFAULT)]
    for ref, text, modules, _data, _args in _paper_presets():
        if hosted and any("::" in m for m in modules):
            continue
        out.append((f"{ref}: {text}" if text else ref, ref))
    return out


def preset_checks(ref: str, online: bool = False) -> tuple[list[str], bool]:
    """The checks a preset ticks, and whether it ticks the data box. The app's own checks
    (``APP_DEFAULT``, or a preset that is no longer there) follow the online box."""
    for found, _text, modules, data, _args in _paper_presets():
        if found == ref:
            return list(modules), data
    return selected_checks(online), True


def preset_args(ref: str) -> dict[str, Any]:
    """The arguments a preset gives its checks, by check name (none for the app's own)."""
    for found, _text, _modules, _data, args in _paper_presets():
        if found == ref:
            from metacheck.presets import label

            return {label(name): dict(value) for name, value in args}
    return {}


def pack_checks() -> list[str]:
    """The modules of installed packs that some paper preset uses, for :func:`choices`."""
    seen: list[str] = []
    for _ref, _text, modules, _data, _args in _paper_presets():
        seen.extend(m for m in modules if "::" in m and m not in seen)
    return seen
