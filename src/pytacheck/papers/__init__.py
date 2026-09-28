"""Paper objects in the bibr schema, and reading/writing them."""

from pytacheck.papers.io import (
    coerce_paper,
    demofile,
    demopaper,
    from_bibr,
    paper,
    paper_to_json,
    paper_write,
    read_bibr,
    test_paper,
)
from pytacheck.papers.model import Paper, PaperList, is_paper, is_paper_list
from pytacheck.papers.schema import empty_table, load_schema
from pytacheck.papers.tables import as_paper_list, paper_id, paper_table, ref_table
from pytacheck.papers.validate import PaperValidationError, paper_validate

__all__ = [
    "Paper",
    "PaperList",
    "PaperValidationError",
    "as_paper_list",
    "coerce_paper",
    "demofile",
    "demopaper",
    "empty_table",
    "from_bibr",
    "is_paper",
    "is_paper_list",
    "load_schema",
    "paper",
    "paper_id",
    "paper_table",
    "paper_to_json",
    "paper_validate",
    "paper_write",
    "read_bibr",
    "ref_table",
    "test_paper",
]
