"""Write the synthetic OSF recordings used by the review cases (parity/cases/mod_prereg_review.yaml).

python tests/mod_prereg/make_review_mocks.py

They go into tests/mod_prereg/mocks next to the recordings of make_mocks.py
(whose schemas they reuse) and cover what those do not:

* ``emptd``: a registration whose ``data`` is an empty array (no row, no
  ``osf_error``; R then errors in ``dplyr::count()``);
* ``pidsl``: a schema field labelled "Paper ID", whose slug collides with the
  ``paper_id`` column the module joins on (R errors in ``dplyr::count()``);
* ``arrys``: answers jsonlite simplifies into 3-d/4-d arrays, matrices and
  lists with empty arrays (``unlist()`` order);
* ``prspz``: the dedicated van 't Veer extractor on values whose
  ``as.character()`` deparses them (``homoList`` coercion of ``[]``,
  descending integer ranges, ``c()`` of a matrix, arrays);
* ``vwonl``: a registration linked with ``?view_only=``: the OSF id keeps the
  token, so the API URLs carry it (and the ``guids`` URL has two ``?``).
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "mocks" / "api.osf.io" / "v2"
API = "https://api.osf.io/v2"
BLOCKS = "aaaaaaaaaaaaaaaaaaaaaaa1"
PAPERID = "ccccccccccccccccccccccc3"
PRSP = "5730e99a9ad5a102c5745a8a"


def write(path, data):
    f = ROOT / f"{path}.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def guid(i, kind="registrations", file=None):
    write(
        file or f"guids/{i}-65f472",
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


def reg(i, responses, schema=BLOCKS, supplement="Synthetic Template", file=None, **attrs):
    a = {
        "title": f"Synthetic registration {i}",
        "description": "",
        "date_created": "2024-01-02T03:04:05.000001",
        "date_modified": "2024-01-03T03:04:05.000001",
        "date_registered": "2024-01-04T03:04:05.000001",
        "embargo_end_date": None,
        "withdrawn": False,
        "registration_supplement": supplement,
        "ia_url": f"https://archive.org/details/osf-registrations-{i}-v1",
        "registration_responses": responses,
    }
    a.update(attrs)
    write(
        file or f"registrations/{i}",
        {
            "data": {
                "id": i,
                "type": "registrations",
                "attributes": a,
                "relationships": {
                    "registration_schema": {
                        "data": {"id": schema, "type": "registration-schemas"},
                        "links": {
                            "related": {
                                "href": f"{API}/schemas/registrations/{schema}/",
                                "meta": {},
                            }
                        },
                    }
                },
                "links": {"html": f"https://osf.io/{i}/"},
            },
            "meta": {"version": "2.0"},
        },
    )


# a registration resource with no data at all
guid("emptd")
write("registrations/emptd", {"data": [], "meta": {"version": "2.0"}})

# a schema whose labels slug to "paper_id" (and a reserved "link")
write(
    f"schemas/registrations/{PAPERID}",
    {
        "data": {
            "id": PAPERID,
            "type": "registration-schemas",
            "attributes": {
                "name": "Synthetic Paper ID",
                "schema_version": 3,
                "schema": {
                    "blocks": [
                        {"block_type": "question-label", "display_text": "Paper ID"},
                        {"block_type": "short-text-input"},
                        {"block_type": "question-label", "display_text": "Link"},
                        {"block_type": "short-text-input"},
                        {"block_type": "question-label", "display_text": "Hypotheses"},
                        {"block_type": "long-text-input"},
                    ],
                    "version": 3,
                },
            },
        }
    },
)
guid("pidsl")
reg("pidsl", {"400-1": "P-123", "400-3": "https://example.org", "400-5": "H1"}, schema=PAPERID)

# arrays: blocks schema of make_mocks.py (5: Hypotheses, 7: Research Questions,
# 9: custom slug, 11: Sample size, 20/21: Numbers & Flags, 23: Empty answer)
guid("arrys")
reg(
    "arrys",
    {
        "300-5": [[[1, 2], [3, 4]], [[5, 6], [7, 8]]],
        "300-7": [[[["a", "b"]], [["c", "d"]]], [[["e", "f"]], [["g", "h"]]]],
        "300-9": [[], ["a"], [3, 2, 1]],
        "300-11": [{"k": [[1, 2], [3, 4]]}, {"k": [[5, 6], [7, 8]]}],
        "300-20": [[[1, 2], [3, 4]], [[5, 6], [7, "x"]]],
        "300-21": [[[1, 2], [3, 4]], [[5, 6]]],
        "300-23": [[1, 2], [True, False]],
    },
)

# van 't Veer & Giner-Sorolla values that as.character() deparses
guid("prspz")
reg(
    "prspz",
    {
        # c(<integer matrix>, 2.5): an atomic vector, 100000 becomes 1e+05
        "description-hypothesis.question1a": [[100000, 2], [3, 4]],
        "84-5": 2.5,
        # c(<list with an empty array>, NULL): homoList makes it character(0)
        "description-hypothesis.question2a": [[], ["a"], ["b", "c"]],
        # c(<list with a descending range>)
        "description-hypothesis.question3a": [[3, 2, 1], ["x"], [-1, 0, 1]],
        # taken as is: a 3-d array, pasted in column-major order
        "description-methods.design.question3b": [[[1, 2], [3, 4]], [[5, 6], [7, 8]]],
        # taken as is: a data frame with a list column holding an empty array
        "recommended-methods.procedure.question9b": [{"k": []}, {"k": ["z"]}, {"k": [1.5]}],
        # c(<list with empty arrays among numbers>, <matrix>)
        "84-38": [[], [1, 2]],
        "description-methods.planned-sample.question7b": [[], [[1, 2], [3, 4]]],
        # typed NAs inside a list
        "84-140": [{"a": None, "b": [None, None]}, [None, 1]],
    },
    schema=PRSP,
    supplement="Pre-Registration in Social Psychology (van 't Veer & Giner-Sorolla, 2016): Pre-Registration",
)

# labels that need R's tolower()/trimws(): simple case mapping ("İ" -> "i"),
# a trailing newline (trimmed) and a no-break space (not trimmed)
UNICODE = "ddddddddddddddddddddddd4"
unicode_labels = [
    "İstanbul Study",
    "ΟΔΟΣ Σ",
    "Straße ẞ",
    "Hypotheses\n",
    " Sample size",
    "Sample\tSize",
    "DATA  Collection",
]
write(
    f"schemas/registrations/{UNICODE}",
    {
        "data": {
            "id": UNICODE,
            "type": "registration-schemas",
            "attributes": {
                "name": "Synthetic Unicode",
                "schema_version": 3,
                "schema": {
                    "blocks": [
                        b
                        for label in unicode_labels
                        for b in (
                            {"block_type": "question-label", "display_text": label},
                            {"block_type": "long-text-input"},
                        )
                    ],
                    "version": 3,
                },
            },
        }
    },
)
guid("unicd")
reg(
    "unicd",
    {f"500-{2 * i + 1}": f"answer {i}" for i in range(len(unicode_labels))},
    schema=UNICODE,
)

# pages titles at the 40-character boundary (nchar() counts characters)
PAGES40 = "eeeeeeeeeeeeeeeeeeeeeee5"
t40 = "Élaborate description of the design here"  # 40 characters
write(
    f"schemas/registrations/{PAGES40}",
    {
        "data": {
            "id": PAGES40,
            "type": "registration-schemas",
            "attributes": {
                "name": "Synthetic Pages 40",
                "schema_version": 2,
                "schema": {
                    "pages": [
                        {
                            "id": "p1",
                            "questions": [
                                {"qid": "t40", "title": t40},
                                {"qid": "t41", "title": t40 + "s"},
                                {"qid": "dup", "title": "First title"},
                            ],
                        },
                        {"id": "p2", "questions": [{"qid": "dup", "title": "Second title"}]},
                    ]
                },
            },
        }
    },
)
guid("pgbnd")
reg(
    "pgbnd",
    {"t40": "forty", "t41.question": "forty-one", "dup": "duplicated qid"},
    schema=PAGES40,
)

# a view-only link: osf_check_id() keeps the token, so it ends up in the API
# URLs; httptest2 hashes the query up to its second "?" ("view_only=abc123/")
guid("vwonl", file="guids/vwonl-def194")
reg("vwonl", {"300-5": "view-only hypothesis"}, file="registrations/vwonl-b9104e")
print("ok")
