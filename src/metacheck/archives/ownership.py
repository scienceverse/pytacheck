"""Whose deposit is each repository link? (port of ``R/repo-ownership.R``, in part).

The ownership engine (``.own_key()``, ``.own_needle()``, ``.own_org_match()``, ``repo_ownership()``)
is not ported (D141). Only what the OSF reader needs is here: the family part of a person's name
(:func:`_own_person_family`), which ``.osf_contributors()`` uses for a contributor the OSF gave no
family name of its own.
"""

from __future__ import annotations

from collections.abc import Sequence

from metacheck._r import grepl, strsplit, trimws

__all__ = ["_own_person_family"]


def _own_person_family(people: str | Sequence[str | None] | None) -> list[str]:
    """The family part of each person in a list of people (``"Name; Name"``).

    Port of ``.own_person_family()``: the part before the comma of ``"Family, Given"``; otherwise
    every word after the first (``"Given Family"``, with any middle names), the first word when every
    other word is an initial (``"Smith AB"``), or the name itself when it is one word.
    """
    items: list[str | None] = (
        [people] if isinstance(people, str) or people is None else list(people)
    )
    out: list[str] = []
    for item in items:
        if item is None:
            continue
        for q in strsplit(item, ";"):
            q = str(trimws(q))
            if not q:
                continue
            if "," in q:
                out.append(str(trimws(q.split(",", 1)[0])))
                continue
            w = [str(x) for x in strsplit(q, r"\s+")]
            if len(w) == 1:
                out.append(q)
            elif all(grepl(r"^([[:upper:]][.]?){1,3}$", x) for x in w[1:]):
                out.append(w[0])
            else:
                out.append(" ".join(w[1:]))
    return out
