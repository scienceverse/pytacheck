"""Generate ``parity/cases/mod_ethics.yaml`` (the ethics_check module).

Run ``python tests/mod_ethics/make_parity_cases.py``, then
``python -m parity generate --area mod_ethics``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "parity" / "cases" / "mod_ethics.yaml"
FIX = "upstream/metacheck/tests/testthat/fixtures"
HELPERS_R = 'source(file.path(root, "tests/mod_ethics/helpers.R")); '
SUPPORT = "__import__('tests.mod_ethics.parity_support', fromlist=['x'])"
IGNORE_ID = {"ignore": ["paper_id", "table.paper_id", "summary_table.paper_id"]}


class Dumper(yaml.SafeDumper):
    pass


def _str(d: yaml.SafeDumper, s: str) -> yaml.Node:
    return d.represent_scalar("tag:yaml.org,2002:str", s, style='"')


Dumper.add_representer(str, _str)

CASES: list[dict[str, Any]] = []


def r_chr(values: list[str | None]) -> str:
    return "c(" + ", ".join("NA" if v is None else json.dumps(v) for v in values) + ")"


def py_list(values: list[Any]) -> str:
    return repr(list(values))


def case(id_: str, paper: Any, **extra: Any) -> None:
    CASES.append({"id": id_, "module": "ethics_check", "args": {"paper": paper}, **extra})


def test_paper(text: list[str | None]) -> dict[str, Any]:
    return {"$test_paper": {"text": text}}


def ec_paper(text: list[str | None], section_type: list[str] | None = None) -> dict[str, Any]:
    r_sec = f", section_type = {r_chr(section_type)}" if section_type else ""
    py_sec = f", section_type={py_list(section_type)}" if section_type else ""
    return {
        "$expr": {
            "r": HELPERS_R + f"ec_paper({r_chr(text)}{r_sec})",
            "py": f"{SUPPORT}.ec_paper({py_list(text)}{py_sec})",
        }
    }


def ds_paper(
    abstract: list[str] | None = None,
    body: list[str] | None = None,
    header: str = "Method",
    title: str = "Test Paper",
) -> dict[str, Any]:
    """A paper with a title, an abstract and one more part (``ds_paper()`` of both helpers)."""
    abstract = abstract or []
    body = body or []
    return {
        "$expr": {
            "r": HELPERS_R
            + f"ds_paper(abstract = {r_chr(abstract) if abstract else 'character(0)'}, "
            f"body = {r_chr(body) if body else 'character(0)'}, header = {json.dumps(header)}, "
            f"title = {json.dumps(title)})",
            "py": f"{SUPPORT}.ds_paper({py_list(abstract)}, {py_list(body)}, "
            f"{header!r}, title={title!r})",
        }
    }


def ec_papers(ids: list[str], texts: list[list[str | None]]) -> dict[str, Any]:
    r_texts = "list(" + ", ".join(r_chr(t) for t in texts) + ")"
    return {
        "$expr": {
            "r": HELPERS_R + f"ec_papers({r_chr(list(ids))}, {r_texts})",
            "py": f"{SUPPORT}.ec_papers({py_list(ids)}, {py_list(texts)})",
        }
    }


def read(*paths: str) -> dict[str, Any]:
    return {"$read": list(paths)}


# A sentence for (nearly) every ethics pattern, then near misses.
SWEEP = [
    "Consent forms were filed with the Institutional Review Board.",
    "An Independent Ethics Committee oversaw the trial.",
    "The Ethical Review Board of the faculty was informed.",
    "The Ethics Advisory Sub-committee was consulted.",
    "An ethical review panel was convened.",
    "The Research Ethics Board gave feedback.",
    "The Medical Ethics Committee was notified.",
    "The human subjects committee was informed.",
    "The Human Research Ethics Advisory Panel was consulted.",
    "The Committee for the Protection of Human Subjects was consulted.",
    "The Committee on Health Research Ethics was consulted.",
    "The Office of Research Ethics was consulted.",
    "IRB #2019-123.",
    "The IEC was consulted.",
    "The REC gave its approval.",
    "Institutional oversight (REB) was consulted.",
    "The METC was consulted.",
    "De medisch-ethische toetsingscommissie was consulted.",
    "The Comité d’éthique was consulted.",
    "The IACUC was consulted.",
    "The AWERB was consulted.",
    "The animal care and use committee was consulted.",
    "The review board for experiments on animals was consulted.",
    "De Dierexperimentencommissie was consulted.",
    "De Instantie voor Dierenwelzijn was consulted.",
    "The IvD was consulted.",
    "The Tierversuchskommission was consulted.",
    "The TvK was consulted.",
    "The COMITÉ D'ÉTHIQUE EN EXPÉRIMENTATION ANIMALE was consulted.",
    "The CEEA was consulted.",
    "Ethics approval was granted.",
    "Ethics clearance: 123.",
    "Ethical clearance was obtained.",
    "Ethical approval: 123.",
    "The data were ethically approved.",
    "Ethical approval was secured.",
    "Ethics approval was received.",
    "We received ethical approval.",
    "It was approved by the local council.",
    "It was approved by our departmental ethics board.",
    "It was approved by the Faculty of Psychology research integrity committee.",
    "It was approved by the University committee for ethical conduct.",
    "We obtained approval from the university research ethics office.",
    "This study was reviewed by colleagues.",
    "The study was approved by the dean.",
    "The protocol was approved by the dean.",
    "The experiment was reviewed by the dean.",
    "All procedures were approved by the dean.",
    "The methods reported in this paper were approved by the dean.",
    "This research was reviewed by the dean.",
    "The study was ethically approved.",
    "We followed the ethical guidelines of the APA.",
    "In accordance with the APA's ethical standards.",
    "See the ethics protocol.",
    "Approved under protocol 12.",
    "An ethics waiver was granted.",
    "An ethical exemption applies.",
    "IRB exempt status.",
    "The study was exempted from IRB review.",
    "Review was waived by the review board.",
    "The study was deemed exempt.",
    "The Declaration of Helsinki.",
    "The Helsinki Declaration.",
    # the patterns added in metacheck 0.4.4.37
    "The Bioethics Committee was consulted.",
    "The Ethik-Kommission was consulted.",
    "The Swedish Ethical Review Authority was consulted.",
    "The Human Subjects Protection Office was consulted.",
    "The Office for Human Subjects Protection was consulted.",
    "Ethical review was waived.",
    "This did not require ethical approval.",
    "No further ethics review was needed.",
    "This falls within the scope of the ethical review.",
    # near misses
    "We used the recognition heuristic (REC/basic) to forecast the rank order.",
    "Ethical decision-making was measured.",
    "All authors approved the final version.",
    "See https://example.org/ethics/approval for details.",
    "The committee of experts met twice.",
    "Participants were recruited via Prolific.",
]


def add_cases() -> None:
    # real papers ----
    case("ethics_check.demo", {"$paper": "demo"})
    case(
        "ethics_check.psychsci",
        read(
            f"{FIX}/psychsci/0956797613520608.json",
            f"{FIX}/psychsci/0956797614522816.json",
            f"{FIX}/psychsci/0956797614527830.json",
            "upstream/metacheck/inst/demos/to_err_is_human.json",
        ),
    )
    case("ethics_check.problems_green", {"$paper": f"{FIX}/problems/0956797615569889.xml"})
    case("ethics_check.debruine_red", {"$paper": f"{FIX}/debruine/debruine-fret.xml"})
    case("ethics_check.formats_na", {"$paper": f"{FIX}/formats/apa.xml"})
    case(
        "ethics_check.green_list",
        read(
            f"{FIX}/problems/0956797615569889.xml",
            f"{FIX}/problems/203020.json",
            f"{FIX}/formats/published.pdf.tei.xml",
        ),
    )
    case(
        "ethics_check.debruine_list",
        read(*(f"{FIX}/debruine/debruine-{n}.xml" for n in ("child", "fret", "sex", "tnl"))),
    )

    # synthetic single papers (testthat) ----
    case(
        "ethics_check.approved_needs",
        test_paper(
            [
                "Participants were recruited from a local community sample and gave informed "
                "consent.",
                "This study was approved by the institutional review board.",
            ]
        ),
        compare=IGNORE_ID,
    )
    case(
        "ethics_check.missing_needs",
        test_paper(
            ["Participants were recruited from a local community sample and gave informed consent."]
        ),
        compare=IGNORE_ID,
    )
    case(
        "ethics_check.none",
        test_paper(["This paper presents a theoretical model of decision-making."]),
        compare=IGNORE_ID,
    )
    case(
        "ethics_check.approved_no_need",
        test_paper(
            [
                "No ethical approval was required for the completion of the study as there were "
                "no human or animal subjects used for the conduct of the research."
            ]
        ),
        compare=IGNORE_ID,
    )
    case(
        "ethics_check.waiver",
        test_paper(["The study was deemed exempt by the institutional review board."]),
        compare=IGNORE_ID,
    )
    case(
        "ethics_check.helsinki",
        test_paper(
            ["The experiment was conducted in accordance with the Declaration of Helsinki."]
        ),
        compare=IGNORE_ID,
    )
    case(
        "ethics_check.animal",
        test_paper(["All procedures were approved by the Animal Ethics Committee."]),
        compare=IGNORE_ID,
    )

    # synthetic edge cases ----
    case("ethics_check.no_text", test_paper([]), compare=IGNORE_ID)
    case("ethics_check.default_text", {"$test_paper": {}}, compare=IGNORE_ID)
    # NA text: $test_paper would drop the null on the R side (unlist), ec_paper keeps it
    case(
        "ethics_check.duplicates",
        ec_paper(
            [
                "Participants were recruited via Prolific.",
                "The IRB approved the protocol.",
                "We recruited 40 undergraduate students who completed the survey.",
                "The IRB approved the protocol.",
                None,
                "Participants were recruited via Prolific.",
                "Mice were housed in standard cages.",
                "Nothing else happened.",
            ]
        ),
    )
    case(
        "ethics_check.animal_live_missing",
        test_paper(["Mice were housed in standard cages.", "They were implanted with probes."]),
        compare=IGNORE_ID,
    )
    case("ethics_check.pattern_sweep", test_paper(SWEEP), compare=IGNORE_ID)
    case(
        "ethics_check.near_misses",
        test_paper(SWEEP[-6:]),
        compare=IGNORE_ID,
    )
    case(
        "ethics_check.references_excluded",
        ec_paper(
            [
                "Participants were recruited online.",
                "Approved by the IRB of the university.",
                "Smith (2020). Approved by the ethics committee. Journal of Ethics.",
            ],
            section_type=["method", "funding", "references"],
        ),
    )
    case(
        "ethics_check.references_only",
        ec_paper(
            [
                "Participants were recruited online.",
                "Smith (2020). Approved by the ethics committee. Journal of Ethics.",
            ],
            section_type=["method", "references"],
        ),
    )

    # paper lists ----
    case(
        "ethics_check.paperlist",
        ec_papers(
            ["p1", "p2"],
            [
                [
                    "Participants were recruited and gave informed consent.",
                    "This study was approved by the ethics committee.",
                ],
                ["This paper presents a theoretical model of decision-making."],
            ],
        ),
    )
    case(
        "ethics_check.rec_approved",
        ec_papers(
            ["a1", "a2", "a3", "a4"],
            [
                ["REB approval for this study was obtained from Carleton University (#118953)."],
                ["The protocol was approved by the Research Ethics Board (REB) of the university."],
                ["Approval was obtained from the local research ethics committee (REC)."],
                [
                    "Ethical approval was granted by the Dierexperimentencommissie (DEC) of "
                    "Utrecht University."
                ],
            ],
        ),
    )
    case(
        "ethics_check.rec_not_approved",
        ec_papers(
            ["n1", "n2", "n3"],
            [
                [
                    "We used the recognition heuristic (REC/basic) to forecast the rank order "
                    "of the election outcome."
                ],
                ["The number of the decision task (DEC) was logged for each trial."],
                [
                    "Regret priming has been shown to reduce decisional errors (Connolly & Reb, "
                    "2012)."
                ],
            ],
        ),
    )
    case(
        "ethics_check.phrasing_approved",
        ec_papers(
            ["b1", "b2", "b3"],
            [
                [
                    "All studies were approved by the Scientific Council of Research and "
                    "Creation at the West University of Timisoara regarding compliance with "
                    "ethical aspects in scientific research."
                ],
                [
                    "The research received approval from the Basque Center on Cognition, Brain "
                    "and Language (BCBL)'s Ethics and Scientific Committee (ref.: 030522SM)."
                ],
                [
                    "All experiments were approved by the University of Western Australia's "
                    "Human Research Ethics Office."
                ],
            ],
        ),
    )
    case(
        "ethics_check.phrasing_not_approved",
        ec_papers(
            ["c1", "c2", "c3"],
            [
                ["Approved the submitted version for publication: FGH, JDH, MP, AR, and FAS."],
                [
                    "The current research was performed in compliance with ethical guidelines "
                    "at the Faculty of Psychology, University of Bergen."
                ],
                ["CloudResearch approved participants for high-quality survey responses."],
            ],
        ),
    )
    case(
        "ethics_check.paperlist_red",
        ec_papers(
            ["z9", "a1", "m5"],
            [
                ["Participants were recruited online.", "The IRB approved the study."],
                ["Participants were recruited online."],
                ["We ran simulations."],
            ],
        ),
    )
    case(
        "ethics_check.paperlist_one",
        ec_papers(["solo"], [["Participants were recruited online."]]),
    )
    case(
        "ethics_check.paperlist_one_green",
        ec_papers(
            ["solo"],
            [["Participants were recruited online.", "Ethics approval was obtained."]],
        ),
    )
    case(
        "ethics_check.paperlist_empty_member",
        {
            "$expr": {
                "r": HELPERS_R + 'paperlist(ec_paper("Participants were recruited online."), '
                'paper("empty"))',
                "py": f"pc.PaperList([{SUPPORT}.ec_paper(['Participants were recruited online.'])"
                ", pc.paper('empty')])",
            }
        },
    )

    # a paper that states no human or animal participants took part (0.4.3.28, dq96z_v2)
    case(
        "ethics_check.no_participants.not_as_subjects",
        test_paper(
            [
                "The professionals described their own work as collaborators in the projects, "
                "not as subjects recruited for a study."
            ]
        ),
    )
    for i, sentence in enumerate(
        [
            "No human subjects were involved in this study.",
            "As this study did not involve human subjects, institutional review board approval "
            "was not required.",
            "Ethics Statement: No animals nor human participants were involved in the production "
            "and analysis of the data used for this article.",
            "No identifiable human research participants were used.",
        ]
    ):
        case(
            f"ethics_check.no_participants.stated_{i + 1}",
            test_paper(["Participants were recruited online.", sentence]),
        )
    case(
        "ethics_check.no_participants.approved",
        test_paper(
            [
                "Participants were recruited online.",
                "No human subjects were involved in the second analysis.",
                "The study was approved by the ethics committee.",
            ]
        ),
    )
    for i, sentence in enumerate(
        [
            "No participants were excluded.",
            "No participants were minors.",
            "Only children without G6PD deficiency were enrolled in the study.",
            "This study would not have been possible without our participants.",
            "No participant was included more than once across training samples.",
        ]
    ):
        case(
            f"ethics_check.no_participants.negation_{i + 1}",
            test_paper(["Participants were recruited online.", sentence]),
        )
    case(
        "ethics_check.no_participants.paperlist",
        ec_papers(
            ["none", "live", "both"],
            [
                ["No human subjects were involved in this study."],
                ["Participants were recruited online."],
                ["Participants were recruited online.", "We did not involve human participants."],
            ],
        ),
    )

    # where the data came from (metacheck 0.4.4.37, #695 point 18: .data_source())
    own = "Participants were recruited online and gave informed consent."
    case("ethics_check.source.own_no_statement", ds_paper(body=[own], header="Participants"))
    case(
        "ethics_check.source.own_with_statement",
        ds_paper(
            body=[own, "This study was approved by the institutional review board."],
            header="Participants",
        ),
    )
    case(
        "ethics_check.source.one_sign_in_method",
        ds_paper(body=["Participants were recruited online."], header="Participants"),
    )
    case(
        "ethics_check.source.one_sign_elsewhere",
        ds_paper(body=["Participants were recruited online."], header="Discussion"),
    )
    case(
        "ethics_check.source.negation_is_no_statement",
        ds_paper(
            body=["Participants were recruited online.", "No participants were excluded."],
            header="Participants",
        ),
    )
    case(
        "ethics_check.source.others_study",
        ds_paper(
            body=[
                "For instance, in a study by Mazar and Zhong (2010, study 3) participants were "
                "recruited online and gave informed consent."
            ],
            header="Introduction",
        ),
    )
    case(
        "ethics_check.source.year_is_no_citation",
        ds_paper(
            body=["In 2021, participants were recruited online and gave informed consent."],
            header="Introduction",
        ),
    )
    case(
        "ethics_check.source.review",
        ds_paper(
            abstract=["We conducted a systematic review and meta-analysis of 35 studies."],
            body=[
                "Databases were searched in May 2024.",
                "The 21 included studies enrolled a total of 7,035 participants who gave "
                "informed consent.",
            ],
        ),
    )
    case(
        "ethics_check.source.commentary",
        ds_paper(
            abstract=["In this commentary, we argue that preregistration is useful."],
            body=["Participants were recruited online in the original study (Smith, 2019)."],
        ),
    )
    case(
        "ethics_check.source.no_data_statements",
        ds_paper(
            body=[
                "No human subjects were involved in this study.",
                "We analysed country-level mortality data.",
                "Aggregate data were taken from Eurostat.",
            ]
        ),
    )
    case(
        "ethics_check.source.theory",
        test_paper(["This paper presents a theoretical model of decision-making."]),
    )
    for src in ("UK Biobank", "British Cohort Study"):
        case(
            f"ethics_check.source.open_resource.{src.split()[0].lower()}",
            ds_paper(
                abstract=[f"We analysed data from the {src} ."],
                body=[
                    f"Data were obtained from the {src} resource.",
                    "Participants had a mean age of 56 years.",
                ],
            ),
        )
    case(
        "ethics_check.source.existing_records",
        ds_paper(
            abstract=["This retrospective cohort study used electronic health records."],
            body=[
                "We retrospectively reviewed the records of 412 patients treated at the "
                "university hospital.",
                "Consent was waived because the data were anonymised.",
            ],
        ),
    )
    case(
        "ethics_check.source.existing_unclear",
        ds_paper(
            abstract=["This is a secondary analysis of existing data."],
            body=[
                "We used data from the Healthy Minds Study.",
                "The analytic sample comprised 2,004 students.",
            ],
        ),
    )
    case(
        "ethics_check.source.protocol",
        ds_paper(
            body=[
                "Participants will be recruited online and will be asked to give informed consent."
            ]
        ),
    )
    case(
        "ethics_check.source.uncertain",
        ds_paper(body=["We tested the model on new data."]),
    )
    case(
        "ethics_check.source.no_text", {"$expr": {"r": "paper('empty')", "py": "pc.paper('empty')"}}
    )
    case(
        "ethics_check.source.paperlist",
        {
            "$expr": {
                "r": HELPERS_R
                + 'paperlist(ds_paper(body = c("Participants were recruited and gave informed '
                'consent.", "This study was approved by the ethics committee."), '
                'header = "Participants", id = "a"), ec_paper('
                '"This paper presents a theoretical model of decision-making.", "b"))',
                "py": "pc.PaperList(["
                f"{SUPPORT}.ds_paper([], ['Participants were recruited and gave informed "
                "consent.', 'This study was approved by the ethics committee.'], "
                f"'Participants', id='a'), {SUPPORT}.ec_paper("
                "['This paper presents a theoretical model of decision-making.'], 'b')])",
            }
        },
    )
    # more committee names and exemptions count as statements; a technical standard and a
    # template line do not
    case(
        "ethics_check.statements.more_names",
        ec_papers(
            [f"s{i}" for i in range(10)],
            [
                [
                    "This study was evaluated and approved by the ETH Zurich Ethics Commission as "
                    "proposal EK-2023-N-258."
                ],
                ["The empirical study was approved by the Bioethics Commission of the university."],
                [
                    "It was approved by the Regional Committees for Medical and Health Research "
                    "Ethics (2017/2205)."
                ],
                [
                    "All procedures were approved by the University of Nebraska-Lincoln Internal "
                    "Review Board."
                ],
                ["The Swedish Ethical Review Authority approved the study protocol."],
                [
                    "The data were collected under a protocol approved by the Office for Human "
                    "Subjects Protection at Florida State University."
                ],
                ["This secondary analysis did not require further ethical review."],
                ["As such, this work was exempt from formal human research ethics review."],
                ["This study was approved by the XXX University ethics committee."],
                [
                    "The study received ethical approval from the institutional review board (EK "
                    "227052019). [INSERT TABLE 1 HERE]"
                ],
            ],
        ),
    )
    case(
        "ethics_check.statements.not_statements",
        ec_papers(
            ["n1", "n2"],
            [
                ["B was the IEC 62471 blue-light hazard function."],
                [
                    "RT was recorded in milliseconds. [Add ethics approval, recruitment, and "
                    "stimulus details here.]"
                ],
            ],
        ),
    )

    # errors in R ----
    case(
        "ethics_check.duplicate_ids",
        ec_papers(["d1", "d1"], [["The IRB approved it."], ["Nothing here."]]),
    )
    case("ethics_check.empty_paper", {"$expr": {"r": "paper()", "py": "pc.paper()"}})
    case(
        "ethics_check.empty_paperlist",
        {"$expr": {"r": "paperlist()", "py": "pc.PaperList([])"}},
    )


def main() -> None:
    add_cases()
    doc = {"area": "mod_ethics", "cases": CASES}
    header = (
        "# Parity cases for the ethics_check module (inst/modules/ethics_check.R).\n"
        "# Generated by tests/mod_ethics/make_parity_cases.py -- edit that file instead.\n"
    )
    with OUT.open("w", encoding="utf-8") as fh:
        fh.write(header)
        yaml.dump(doc, fh, Dumper=Dumper, sort_keys=False, allow_unicode=True, width=10_000)


if __name__ == "__main__":
    main()
