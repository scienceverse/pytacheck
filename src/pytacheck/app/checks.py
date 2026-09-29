"""Which checks the demo app runs, in five fixed sets."""

from __future__ import annotations

from functools import cache

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
    from pytacheck.status import status_table

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
    from pytacheck.status import status_table

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
