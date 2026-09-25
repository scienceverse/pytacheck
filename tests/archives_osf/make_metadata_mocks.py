"""Write the OSF mocks for synthetic nodes whose metadata mixes JSON types.

``.osf_metadata_download()`` writes ``metadata.json`` with
``jsonlite::write_json()`` from values parsed with
``resp_body_json(simplifyVector = TRUE)``, so the tags, licence and citation
come out as jsonlite simplifies them: arrays mixing numbers, strings, booleans
and ``null`` share one type, arrays of objects become data frames written row
by row, and doubles are printed with four decimals. The nodes below exercise
those rules (the ``archives_osf_review`` cases ``.osf_metadata_download.mixd*``).

Regenerate with ``python -m tests.archives_osf.make_metadata_mocks`` from the
repository root; only the ``mixd*`` files are (re)written.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

NODES = Path(__file__).resolve().parent / "mocks" / "api.osf.io" / "v2" / "nodes"
#: httptest2's hash of the query "embed=license&embed=bibliographic_contributors"
EMBED = "e7c2eb"
EMPTY = {"data": [], "links": {"next": None}, "meta": {"total": 0}}


def node(osf_id: str, attributes: dict[str, Any]) -> dict[str, Any]:
    return {
        "data": {
            "id": osf_id,
            "type": "nodes",
            "attributes": {
                "title": f"Mixed {osf_id}",
                "description": "",
                "category": "project",
                "public": True,
                "date_created": "2020-01-02T03:04:05.000000",
                "date_modified": "2021-01-02T03:04:05.000000",
                **attributes,
            },
            "embeds": {
                "license": {"data": {"attributes": {"name": "CC-By Attribution 4.0 International"}}},
                "bibliographic_contributors": {
                    "data": [
                        {
                            "embeds": {
                                "users": {
                                    "data": {
                                        "attributes": {
                                            "full_name": "Ann Other",
                                            "given_name": "Ann",
                                            "family_name": "Other",
                                            "social": {},
                                        }
                                    }
                                }
                            }
                        }
                    ]
                },
            },
        }
    }


MIXED: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {
    # numbers with null (a double NA is written "NA"), scalars of several types
    "mixd1": (
        {
            "tags": [1, None, 2.5],
            "node_license": {"copyright_holders": ["A", None, 3], "year": 2020.0},
        },
        {
            "title": "Mixed",
            "author": [
                {"given": "A", "family": "B"},
                {"given": None, "family": 3},
                {"family": True},
            ],
            "issued": {"date-parts": [[2020, 1.5, None]]},
            "number": 0.000012345,
            "volume": 1e-5,
            "page": 123456.789012,
            "big": 1e300,
        },
    ),
    # scalars next to objects stay a list; nested objects in rows are nested
    # data frames (an all-NA nested row is {}); a list column writes null
    "mixd2": (
        {
            "tags": ["x", {"a": 1}, None, True],
            "node_license": [{"year": {"from": 2019}}, {"year": {"from": None}}, None],
        },
        {
            "author": [{"name": [1, 2]}, {"name": 1}, {"name": None}],
            "editor": [{"x": 1}, {"x": "y"}, {"x": False}],
            "issued": [[1, 2], [3, None]],
        },
    ),
}


def write(rel: str, body: Any) -> None:
    path = NODES / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")


def main() -> None:
    for osf_id, (attributes, citation) in MIXED.items():
        write(f"{osf_id}-{EMBED}.json", node(osf_id, attributes))
        write(
            f"{osf_id}/citation.json",
            {"data": {"id": osf_id, "type": "node-citation", "attributes": citation}},
        )
        for listing in ("wikis", "logs", "registrations", "forks"):
            write(f"{osf_id}/{listing}.json", EMPTY)


if __name__ == "__main__":
    main()
