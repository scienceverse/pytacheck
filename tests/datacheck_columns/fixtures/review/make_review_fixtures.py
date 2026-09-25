"""Text fixtures for parity/cases/datacheck_columns_review.yaml.

Run from the repository root:
    .venv/bin/python tests/datacheck_columns/fixtures/review/make_review_fixtures.py
(binary fixtures -- sav/xlsx/ods -- are written by make_review_fixtures.R)
"""

from __future__ import annotations

import json
from pathlib import Path

D = Path(__file__).resolve().parent
BOM = b"\xef\xbb\xbf"


def write(name: str, data: str | bytes) -> None:
    (D / name).write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


# JSON with a non-standard NaN literal: jsonlite refuses to parse it
write("nan_literal.json", '[{"name": "a", "label": "A"}, {"name": "b", "label": "B", "n": NaN}]\n')
write("nan_literal.csv", '[{"name": "a", "label": "A"}, {"name": "b", "label": "B", "n": NaN}]\n')

# a BOM before JSON shipped with a .csv extension
write("bom_json.csv", BOM + b'[{"name": "a", "label": "A"}, {"name": "b", "label": "B"}]\n')

# a BOM before a markdown pipe table on the very first line
write("bom_table.md", BOM + "| variable | description |\n|---|---|\n| a | Alpha |\n| b | Beta |\n".encode())

# several tables: prose table (no codebook header), then a ragged codebook table
write(
    "multi_table.md",
    "# Data\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\nText between.\n\n"
    "|Variable Name | | Label | Values |\n|:---|---|---:|---|\n"
    "| x1 | skip | First item | 1 = yes; 2 = no |\n"
    "| x2 | | Second item |\n"
    "| x1 | | | |\n"
    "| x3 | | Third | 1 (low) to 5 (high) | extra |\n"
    "|  | | orphan label | |\n"
    "not a table line\n| y | z |\n",
)

# declared-missing column shapes, a question column, coding instructions
write(
    "missing_cols.csv",
    "variable,label,values,missing values,question text,derivation\n"
    'a,Alpha,"1 = yes; 2 = no; 99 = refused",-9 = not answered,What is a?,mean of a1-a3\n'
    'b,Beta,"Male = 1, Female = 2",-99; -98,,\n'
    "c,Gamma,,N/A,  ,recoded\n"
    'd,Delta,"M = Male; F = Female","-7, x, -8",Delta?,\n'
    "a,,,,,\n"
    'e,Eps,"1 = Strongly disagree | 5 = Strongly agree",refused = -1,,\n',
)

# numeric variable names and a label column of numbers
write("numeric_vars.csv", "code,description\n1,10\n2,20\n2.50,30\n007,\n")

# semicolon delimiter with a header on row 3 and a stats column
write("title_semicolon.csv", "Codebook v2;;\n;;\nName;Label;Mean\nage;Age in years;31.5\nrt;Reaction time;512\n")

# JSON codebook value-label shapes
write(
    "values_shapes.json",
    json.dumps(
        {
            "Variables": [
                {"Name": "a", "Label": "Alpha", "Values": [{"value": 1, "label": "yes"},
                                                           {"value": 2, "label": ""},
                                                           {"label": "no code"}, None, "3 = maybe"]},
                {"name": "b", "question": "What is b?", "levels": {"1": "low", "2": None, "3": "high"}},
                {"name": "c", "description": "Gamma", "categories": ["1 = x; 2 = y", 5, True]},
                {"name": "d", "title": "  ", "item_text": "Item d", "value_labels": {"1": ["a", "b"]}},
                {"id": 7, "label": 3.25, "values": [{"code": "9", "meaning": "Refused"},
                                                    {"code": "1", "text": "One"}]},
                {"name": "", "label": "no name"},
                ["not", "an", "entry"],
            ]
        }
    ),
)

# Qualtrics edge cases
qsf = {
    "SurveyEntry": {"SurveyName": "edge"},
    "SurveyElements": [
        None,
        {"Element": "BL", "Payload": []},
        {"Element": "SQ", "Payload": {
            "DataExportTag": "M1", "QuestionType": ["Matrix"], "Selector": "Likert",
            "QuestionText": [None, "ignored"],
            "Choices": {"1": {"Display": "Item <b>one</b>"}, "2": {"DisplayLogic": {"0": {"x": 1, "y": "z"}}},
                        "": {"Display": "blank key"}, "3": {"Display": {"a": [True, 2]}}},
            "Answers": {"1": {"Display": "Disagree"}, "2": {"Display": "Agree &amp;lt;3"}},
            "ChoiceDataExportTags": {"1": "M1_a", "2": 5, "3": ["x"]}}},
        {"Element": "SQ", "Payload": {
            "DataExportTag": 42, "QuestionType": "MC", "Selector": "MAVR",
            "QuestionText": [["nested", 1]],
            "Choices": {"1": {"Display": "A"}, "4": {"display": "lower-case display"}},
            "ChoiceDataExportTags": False}},
        {"Element": "SQ", "Payload": {
            "DataExportTag": ["Q7"], "QuestionType": "MC", "Selector": "SAVR",
            "QuestionText": {"t": "Object text"},
            "Choices": [{"Display": "array choice"}]}},
        {"Element": "SQ", "Payload": {
            "DataExportTag": None, "QuestionID": "QID9", "QuestionType": "Matrix",
            "QuestionText": "Matrix with array choices", "Choices": [{"Display": "x"}]}},
        {"Element": "SQ", "Payload": {
            "DataExportTag": "TE", "QuestionType": "TE", "QuestionText": 12.5, "Choices": {}}},
    ],
}
write("edge.qsf", json.dumps(qsf))

# a list-valued DataExportTag: R errors inside parse_qsf
qsf_err = {"SurveyElements": [{"Element": "SQ", "Payload": {"DataExportTag": ["a", "b"],
                                                           "QuestionText": "x"}}]}
write("tag_list.qsf", json.dumps(qsf_err))
# an empty-array DataExportTag: `FALSE || logical(0)` is NA inside if()
write("tag_empty.qsf", json.dumps({"SurveyElements": [{"Element": "SQ", "Payload": {
    "DataExportTag": [], "QuestionText": "x"}}]}))
