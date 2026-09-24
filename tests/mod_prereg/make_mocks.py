"""Write the synthetic OSF recordings in tests/mod_prereg/mocks (used by parity cases and tests).

python tests/mod_prereg/make_mocks.py
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "mocks" / "api.osf.io" / "v2"
API = "https://api.osf.io/v2"
BLOCKS = "aaaaaaaaaaaaaaaaaaaaaaa1"
PAGES = "bbbbbbbbbbbbbbbbbbbbbbb2"
PRSP = "5730e99a9ad5a102c5745a8a"


def write(path, data):
    f = ROOT / f"{path}.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def guid(i, kind="registrations"):
    write(
        f"guids/{i}-65f472",
        {
            "data": {
                "id": i,
                "type": "guids",
                "attributes": {},
                "relationships": {
                    "referent": {
                        "links": {
                            "related": {"href": f"{API}/{kind}/{i}/", "meta": {"type": kind}}
                        },
                        "data": {"id": i, "type": kind},
                    }
                },
                "links": {"self": f"{API}/guids/{i}/", "html": f"https://osf.io/{i}/"},
            },
            "meta": {"version": "2.0"},
        },
    )


def reg(
    i,
    responses,
    schema=BLOCKS,
    withdrawn=False,
    supplement="Synthetic Template",
    title=None,
    schema_href=True,
    **attrs,
):
    rel = {"data": {"id": schema, "type": "registration-schemas"}}
    if schema_href:
        rel["links"] = {"related": {"href": f"{API}/schemas/registrations/{schema}/", "meta": {}}}
    a = {
        "title": title if title is not None else f"Synthetic registration {i}",
        "description": "",
        "date_created": "2024-01-02T03:04:05.000001",
        "date_modified": "2024-01-03T03:04:05.000001",
        "date_registered": "2024-01-04T03:04:05.000001",
        "embargo_end_date": None,
        "withdrawn": withdrawn,
        "registration_supplement": supplement,
        "ia_url": f"https://archive.org/details/osf-registrations-{i}-v1",
        "registration_responses": responses,
    }
    a.update(attrs)
    write(
        f"registrations/{i}",
        {
            "data": {
                "id": i,
                "type": "registrations",
                "attributes": a,
                "relationships": {"registration_schema": rel},
                "links": {"html": f"https://osf.io/{i}/"},
            },
            "meta": {"version": "2.0"},
        },
    )


def files(*names):
    return [
        {
            "file_id": f"id{n}",
            "file_name": f"{n}.csv",
            "file_urls": {"html": f"https://osf.io/h/{n}", "download": f"https://osf.io/d/{n}"},
            "file_hashes": {"sha256": f"sha{n}"},
        }
        for n in names
    ]


# blocks-format schema: labels, reserved names, repeated fields, slugs, NA labels
blocks = [
    {"block_type": "page-heading", "display_text": "Overview"},  # 0
    {"block_type": "short-text-input"},  # 1: no label yet (NA)
    {"block_type": "question-label", "display_text": "Title"},  # 2
    {"block_type": "short-text-input"},  # 3: reserved "title"
    {"block_type": "question-label", "display_text": "Hypotheses"},  # 4
    {"block_type": "long-text-input"},  # 5
    {"block_type": "question-label", "display_text": " Research Questions "},  # 6
    {"block_type": "long-text-input"},  # 7: joins research_questions
    {"block_type": "question-label", "display_text": "My Custom Field: Über-weird!"},  # 8
    {"block_type": "multi-select-input"},  # 9
    {"block_type": "question-label", "display_text": "Sample size"},  # 10
    {"block_type": "long-text-input"},  # 11
    {"block_type": "question-label", "display_text": "???"},  # 12
    {"block_type": "file-input"},  # 13: slug "" -> field
    {"block_type": "question-label"},  # 14: no display_text
    {"block_type": "single-select-input"},  # 15: NA label
    {"block_type": "question-label", "display_text": "Other"},  # 16
    {"block_type": "paragraph", "display_text": "not an input"},  # 17
    {"block_type": "contributors-input"},  # 18
    {"block_type": "question-label", "display_text": "Numbers & Flags"},  # 19
    {"block_type": "short-text-input"},  # 20
    {"block_type": "short-text-input"},  # 21: same label again
    {"block_type": "question-label", "display_text": "Empty answer"},  # 22
    {"block_type": "long-text-input"},  # 23
    {"block_type": "question-label", "display_text": "Date Created"},  # 24
    {"block_type": "short-text-input"},  # 25: reserved
]
write(
    f"schemas/registrations/{BLOCKS}",
    {
        "data": {
            "id": BLOCKS,
            "type": "registration-schemas",
            "attributes": {
                "name": "Synthetic Blocks",
                "schema_version": 3,
                "schema": {"blocks": blocks, "version": 3},
            },
        }
    },
)

# pages-format schema
pages = [
    {
        "id": "page1",
        "questions": [
            {"qid": "q1", "title": "Sample Size", "type": "string"},
            {
                "qid": "q2",
                "title": "A very long title that is clearly a prompt rather than a label",
                "type": "string",
            },
            {"qid": "q3", "title": "What is your main hypothesis?", "type": "string"},
            {"qid": "q4", "type": "string"},
            {
                "qid": "sample",
                "title": "How many observations will be collected or what will determine sample size?",
            },
            {"title": "No qid at all"},
        ],
    },
    {
        "id": "page2",
        "questions": [
            {"qid": "q6", "title": "Analyses", "type": "object"},
            {"qid": "q7", "title": "", "type": "object"},
            {"qid": "nested", "title": "Matrix"},
        ],
    },
    {"id": "page3"},
]
write(
    f"schemas/registrations/{PAGES}",
    {
        "data": {
            "id": PAGES,
            "type": "registration-schemas",
            "attributes": {
                "name": "Synthetic Pages",
                "schema_version": 2,
                "schema": {"name": "Synthetic Pages", "pages": pages},
            },
        }
    },
)

# withdrawn
guid("wdrwn")
reg("wdrwn", {"300-5": "ignored"}, withdrawn=True, title="A withdrawn study")

# blocks-format registration
guid("blkss")
reg(
    "blkss",
    {
        "300-1": "unlabelled input",
        "300-3": "a title that must not replace the real one",
        "300-5": "  H1: people like cake.  ",
        "300-7": "RQ: why?",
        "300-9": ["Option A", "Option B"],
        "300-11": "We will collect 200 participants.",
        "300-13": files("a", "b"),
        "300-15": "answer without a label",
        "300-18": [{"name": "Ann", "orcid": None}, {"name": "Bo", "orcid": "0000-1"}],
        "300-20": [1, 2.5, True],
        "21": 3000000000,
        "300-23": "   ",
        "300-25": "2020-01-01",
        "300-99": "no such block",
        "300-11b": "not a position",
    },
)

# pages-format registration (legacy qN keys, .question/.uploader sub-keys)
guid("pagss")
reg(
    "pagss",
    {
        "q1": "120 students",
        "q2.question": "long-title answer",
        "q3.uploader": files("c"),
        "q4": None,
        "sample": "N = 50",
        "q6.question": {"value": "t-test", "extra": [{"k": 1}]},
        "q7": "empty title uses the qid",
        "q8": "not in the schema",
        "nested": [["a", "b"], ["c", "d"]],
    },
    schema=PAGES,
    supplement=None,
    embargo_end_date="2030-01-01",
)

# van 't Veer & Giner-Sorolla (prsp), newer 84-* keys: $ partial matching
guid("prspx")
reg(
    "prspx",
    {
        "84-56": "indices first",
        "84-140": "additional comments",
        "84-23": ["iv one", "iv two"],
        "description-methods.design.question2a": "iv intro",
        "84-25": files("e"),
        "description-methods.design.question2b": "dv intro",
        "description-methods.design.question3b": ["cov a", "cov b"],
        "recommended-methods.procedure.question9b": files("f", "g"),
        "84-34": 150,
        "datacompletion.question": "partial match for datacompletion",
        "looked": None,
    },
    schema=PRSP,
    supplement="Pre-Registration in Social Psychology (van 't Veer & Giner-Sorolla, 2016): Pre-Registration",
)

# no responses at all / no schema link
guid("emptr")
reg("emptr", {}, title="No responses")
guid("nosch")
reg("nosch", {"300-5": "unread"}, schema_href=False, title="No schema link")
print("ok")
