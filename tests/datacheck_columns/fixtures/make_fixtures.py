"""Write the text codebook fixtures (csv/tsv/json/md/qsf/rtf/odt/txt).

Run from the repository root: ``python tests/datacheck_columns/fixtures/make_fixtures.py``.
Binary fixtures (xlsx/ods/sav/dta/docx/pdf) come from ``make_fixtures.R``.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

D = Path(__file__).parent


def w(name: str, text: str, encoding: str = "utf-8", bom: bool = False) -> None:
    data = text.encode(encoding)
    if bom:
        data = b"\xef\xbb\xbf" + data
    (D / name).write_bytes(data)


# -- delimited ---------------------------------------------------------------
w(
    "structured.csv",
    "variable,label,values,question,missing values,transformations\n"
    'sex,Sex,"1 = Male; 2 = Female; -99 = Refused",What is your sex?,,\n'
    "age,Age in years,,How old are you?,-9,\n"
    'cond,Condition,"control = 0, treatment = 1",, ,\n'
    'handed,Handedness,"L = Left; R = Right",,"-9 = not answered",\n'
    "score,Total score,,,,mean of items 1-10\n"
    "score,,,,,\n"
    ",orphan label,,,,\n"
    "V1-V3,Rating items,1 (low) to 5 (high),,,\n",
)
w(
    "semicolon_bom.csv",
    "Name;Description\nid;Participant identifier\nrt;Reaction time (ms)\n",
    bom=True,
)
w(
    "title_rows.csv",
    "Codebook for Study 1,\n,\nfield,definition\nx1,First item\nx2,Second item\n",
)
w("header_last.csv", "note,other\nsomething,else\nvariable,label\n")
w(
    "wide.csv",
    "stat,age,score,rt\nmean,31.2,4.5,512\nsd,5.1,1.2,80\nn,40,40,40\n",
)
w(
    "wide_named.csv",
    "variable,age,score\nlabel,Age in years,Total score\nmean,31.2,4.5\nsd,5.1,1.2\nn,40,40\n",
)
w("latin1.csv", "variable,label\nnaive,Naïve participant\ncafe,Café visits\n", encoding="latin-1")
w("tabbed.tsv", "var\tdesc\nid\tidentifier\nscore\tscore on test\n")
w("no_header.csv", "a,b\n1,2\n3,4\n")
w(
    "json_in.csv",
    json.dumps(
        [
            {"name": "id", "label": "Participant ID"},
            {"name": "rt", "description": "Reaction time"},
        ]
    ),
)
w("readme.txt", "This is a prose readme.\nNo variable table here.\n")
w("empty.csv", "")

# -- JSON ----------------------------------------------------------------------
w(
    "codebook.json",
    json.dumps(
        [
            {
                "name": "sex",
                "label": "Sex",
                "values": [
                    {"name": "1", "label": "Male"},
                    {"name": "2", "label": ""},
                    {"value": 9, "label": "Refused"},
                ],
            },
            {
                "Variable": "age",
                "Description": "Age in years",
                "levels": {"1": "young", "2": "old"},
            },
            {
                "name": "cond",
                "question": "Which condition?",
                "categories": ["0 = control", "1 = treat"],
            },
            {"name": "", "label": "no name"},
            {"id": 7, "title": "  Numeric id  ", "value_labels": "1=yes;2=no"},
            "not an object",
        ]
    ),
)
w(
    "schema.json",
    json.dumps(
        {
            "name": "dataset",
            "variableMeasured": [
                {
                    "name": "q1",
                    "description": "First question",
                    "value_labels": {"1": "Yes", "-9": "Refused"},
                },
                {"name": "q2", "description": "Second question"},
            ],
        }
    ),
)
w("not_codebook.json", json.dumps({"a": 1, "b": [1, 2, 3]}))
w("bad.json", "{not json")

# -- markdown -------------------------------------------------------------------
w(
    "readme.md",
    "# Data\n\nSome prose | with a pipe.\n\n"
    "|variable |class |description |\n"
    "|:--------|:-----|:-----------|\n"
    "|country  |character |Country name |\n"
    "|year     |double |Year of the survey |\n"
    "|  |  |  |\n"
    "|pop      |double |Population, 1000s |extra |\n"
    "|short|\n"
    "\nAnother table:\n\n"
    "| a | b |\n|---|---|\n| 1 | 2 |\n\n"
    "| name | label | values |\n| --- | --- | --- |\n"
    "| sex | Sex | 1 = Male; 2 = Female |\n",
)
w("prose.md", "# Title\n\nJust prose, no table.\n")

# -- rtf / odt --------------------------------------------------------------------
w(
    "codebook.rtf",
    "{\\rtf1\\ansi\\deff0 {\\fonttbl {\\f0 Times;}}\n"
    "\\f0\\fs24 age: Age in years\\par\n"
    "sex: 1 = male, 2 = female\\par\n"
    "\\b bold\\b0  text \\{braces\\}\\par\n}\n",
)
content = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">\n'
    "<office:body><office:text>\n"
    "<text:p>age: Age in years &amp; more</text:p>\n"
    "<text:p>sex: 1 = male &lt;2&gt; &quot;female&quot; &apos;x&apos; &amp;lt;</text:p>\n"
    "</office:text></office:body></office:document-content>\n"
)
with zipfile.ZipFile(D / "codebook.odt", "w") as zf:
    zf.writestr("mimetype", "application/vnd.oasis.opendocument.text")
    zf.writestr("content.xml", content)

# -- Qualtrics .qsf -----------------------------------------------------------------
qsf = {
    "SurveyEntry": {"SurveyID": "SV_test", "SurveyName": "Test"},
    "SurveyElements": [
        {"SurveyID": "SV_test", "Element": "BL", "Payload": [{"Type": "Default"}]},
        {
            "Element": "SQ",
            "PrimaryAttribute": "QID1",
            "Payload": {
                "QuestionText": "<p>How <b>satisfied</b>&nbsp;are you?</p>",
                "DataExportTag": "SAT",
                "QuestionType": "MC",
                "Selector": "SAVR",
                "Choices": {
                    "1": {"Display": "Very unsatisfied"},
                    "2": {"Display": "Neutral"},
                    "3": {"Display": "Very satisfied"},
                    "4": {"Display": "Refused"},
                },
                "QuestionID": "QID1",
            },
        },
        {
            "Element": "SQ",
            "PrimaryAttribute": "QID2",
            "Payload": {
                "QuestionText": "Rate each statement",
                "DataExportTag": "POWER.PP1",
                "QuestionType": "Matrix",
                "Selector": "Likert",
                "Choices": {
                    "1": {"Display": "I feel <i>powerful</i>"},
                    "2": {"Display": "I feel weak"},
                    "8": {"Display": "Status item"},
                    "9": {"DisplayLogic": {"x": 1}},
                },
                "ChoiceDataExportTags": {"1": "POWER.PP1_1", "2": "2", "8": "STATUS.PP1_8"},
                "Answers": {"1": {"Display": "Disagree"}, "2": {"Display": "Agree"}},
                "QuestionID": "QID2",
            },
        },
        {
            "Element": "SQ",
            "PrimaryAttribute": "QID3",
            "Payload": {
                "QuestionText": "Which apply?",
                "DataExportTag": "Q3",
                "QuestionType": "MC",
                "Selector": "MAVR",
                "Choices": {
                    "1": {"Display": "Option A"},
                    "2": {"Display": ["Option", "B"]},
                    "4": {"Display": "Other", "TextEntry": "true"},
                },
                "ChoiceDataExportTags": False,
                "QuestionID": "QID3",
            },
        },
        {
            "Element": "SQ",
            "PrimaryAttribute": "QID4",
            "Payload": {
                "QuestionText": "Any comments?",
                "DataExportTag": "Q1914",
                "QuestionType": "TE",
                "Selector": "ML",
                "QuestionID": "QID4",
            },
        },
        {
            "Element": "SQ",
            "PrimaryAttribute": "QID5",
            "Payload": {
                "QuestionText_Unsafe": "Unsafe text",
                "QuestionID": "QID5",
                "QuestionType": "DB",
                "Selector": "TB",
            },
        },
        {
            "Element": "SQ",
            "PrimaryAttribute": "QID6",
            "Payload": {"QuestionText": "Empty tag", "DataExportTag": "", "QuestionID": "QID6"},
        },
        {"Element": "SQ", "PrimaryAttribute": "QID7", "Payload": None},
        {
            "Element": "SQ",
            "PrimaryAttribute": "QID8",
            "Payload": {
                "QuestionText": "",
                "DataExportTag": "SV",
                "QuestionType": "Matrix",
                "Selector": "Likert",
                "Choices": {"1": {"Display": "Item one"}, "2": {"Display": "Item two"}},
                "ChoiceDataExportTags": {"1": "SV_1", "2": "sv"},
                "Answers": [{"Display": "a"}],
            },
        },
        {
            "Element": "SQ",
            "PrimaryAttribute": "QID9",
            "Payload": {
                "QuestionText": "Slider",
                "DataExportTag": "SL",
                "QuestionType": "Slider",
                "Choices": [{"Display": "x"}],
            },
        },
    ],
}
w("survey.qsf", json.dumps(qsf, indent=1))
w("not_qsf.qsf", json.dumps({"SurveyEntry": {}}))
w("no_sq.qsf", json.dumps({"SurveyElements": [{"Element": "BL", "Payload": []}]}))
w("wide_transposed.csv", "n,variable,age,score\nmean,label,Age in years,Total score\nsd,x,1,2\n")
# the structured CSV of test-codebook-helpers.R ("parse_codebook reads a structured CSV codebook")
w(
    "testthat_codebook.csv",
    "varname,description\nid,participant identifier\nscore,outcome measure\n",
)
# a .qsf saved with a UTF-8 byte-order mark (jsonlite warns but parses it)
w(
    "bom.qsf",
    json.dumps(
        {
            "SurveyElements": [
                {
                    "Element": "SQ",
                    "Payload": {
                        "DataExportTag": "Q1",
                        "QuestionText": "<p>Hi?</p>",
                        "QuestionType": "TE",
                    },
                },
                {"Element": "SQ", "Payload": {"DataExportTag": " ", "QuestionID": "QID2"}},
                {
                    "Element": "SQ",
                    "Payload": {
                        "QuestionID": "QID3",
                        "QuestionType": "MC",
                        "Selector": "SAVR",
                        "QuestionText": "Pick",
                        "Choices": {"1": {"Display": "Yes"}, "2": {"Display": "N/A"}},
                    },
                },
            ]
        }
    ),
    bom=True,
)
