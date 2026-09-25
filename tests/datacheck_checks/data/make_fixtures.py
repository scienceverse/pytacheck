"""Write the small data files the datacheck_checks parity cases and tests read.

Deterministic: re-running gives byte-identical files. Run from the repository
root::

    .venv/bin/python tests/datacheck_checks/data/make_fixtures.py
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent


def q(*cells: object) -> str:
    return ",".join(f'"{c}"' for c in cells)


def write(name: str, text: str, encoding: str = "utf-8", bom: bool = False) -> None:
    data = text.encode(encoding)
    if bom:
        data = b"\xef\xbb\xbf" + data
    (HERE / name).write_bytes(data)


def qualtrics() -> str:
    """A Qualtrics "use choice text" export: header, question text, ImportId, data."""
    hdr = q("StartDate", "EndDate", "Status", "IPAddress", "Progress",
            "Duration (in seconds)", "Finished", "RecordedDate", "ResponseId",
            "Q1_1", "Q1_2", "Q1_3", "Q1_DO_1")  # fmt: skip
    qtxt = q("Start Date", "End Date", "Response Type", "IP Address", "Progress",
             "Duration (in seconds)", "Finished", "Recorded Date", "Response ID",
             "How happy? - today", "How happy? - yesterday", "How happy? - tomorrow",
             "Display order")  # fmt: skip
    imp = q(*(
        '{""ImportId"":""' + k + '""}'
        for k in ("startDate", "endDate", "status", "ipAddress", "progress", "duration",
                  "finished", "recordedDate", "_recordId", "QID1_1", "QID1_2", "QID1_3",
                  "QID1_DO")
    ))  # fmt: skip
    rows = []
    for i in range(1, 9):
        rows.append(
            q(
                f"2021-05-{i:02d} 10:00:00",
                f"2021-05-{i:02d} 10:05:00",
                0,
                f"192.168.0.{i}",
                100,
                200 + 37 * i,
                1,
                f"2021-05-{i:02d} 10:05:00",
                f"R_abc{i:05d}xyz",
                1 + i % 5,
                1 + (i * 2) % 5,
                1 + (i * 3) % 5,
                "1|2|3",
            )
        )
    return "\n".join([hdr, qtxt, imp, *rows]) + "\n"


def jspsych() -> str:
    lines = ["trial_type,trial_index,time_elapsed,internal_node_id,rt,stimulus,response"]
    for i in range(12):
        lines.append(f"html-keyboard-response,{i},{1000 + 850 * i},0.0-{i}.0,{400 + 13 * i},"
                     f"<p>{'red' if i % 2 else 'blue'}</p>,{'f' if i % 3 else 'j'}")  # fmt: skip
    return "\n".join(lines) + "\n"


def inquisit() -> str:
    lines = ["date\ttime\tsubject\tgroup\tblockcode\ttrialnum\ttrialcode\tlatency\tcorrect"]
    for i in range(1, 13):
        lines.append(
            f"52021\t10:00:{i:02d}\t7\t1\tpractice\t{i}\tcongruent\t{500 + 11 * i}\t{i % 2}"
        )
    return "\n".join(lines) + "\n"


def behaverse() -> str:
    lines = ["instrument_id,participant_id,trial_index,response_numeric,response_time"]
    for i in range(1, 11):
        lines.append(f"STROOP,p1,{i},{i % 2},{450 + 7 * i}")
    return "\n".join(lines) + "\n"


def behaverse_wide() -> str:
    hdr = "participant_id,q1_response_numeric_i1,q1_response_time_i1,q2_response_numeric_i1"
    rows = [f"p{i},{i % 5 + 1},{1000 + i * 10},{(i * 2) % 5 + 1}" for i in range(1, 11)]
    return "\n".join([hdr, *rows]) + "\n"


def psychopy() -> str:
    hdr = ("trials.thisRepN,trials.thisTrialN,trials.thisN,trials.thisIndex,word,colour,"
           "key_resp.keys,key_resp.corr,key_resp.rt,key_resp.started,participant,date,"
           "expName,psychopyVersion,frameRate")  # fmt: skip
    rows = []
    for i in range(12):
        rows.append(
            f"0,{i},{i},{i % 4},{'red' if i % 2 else 'green'},{'blue' if i % 3 else 'red'},"
            f"{'left' if i % 2 else 'right'},{i % 2},{0.45 + i * 0.031:.3f},{2.5 + i * 1.7:.2f},"
            f"7,2020-05-19_16h20.01.792,stroop,2021.2.3,60.01"
        )
    return "\n".join([hdr, *rows]) + "\n"


def eprime(header_block: bool) -> str:
    if header_block:
        lines = [
            "*** Header Start ***",
            "VersionPersist: 1",
            "LevelName: Session",
            "Experiment: stroop_task",
            "Subject: 12",
            "*** Header End ***",
            "\tLevel: 3",
            "\t*** LogFrame Start ***",
            "\tProcedure: TrialProc",
            "\tStimulus.RT: 532",
            "\tStimulus.ACC: 1",
            "\t*** LogFrame End ***",
        ]
    else:
        lines = [
            "Experiment: stroop_task",
            "Subject: 12",
            "LevelName: Trial",
            "Stimulus.RT: 532",
        ]
    return "\r\n".join(lines) + "\r\n"


def offset_header() -> str:
    """A banner row above the real header (the CDA EEG sheet shape)."""
    banner = ",".join(["CDA"] * 6)
    hdr = "Participant,Reject,Condition,-100,0,100"
    rows = [f"{i},{i % 2},{'left' if i % 2 else 'right'},{0.1 * i:.1f},{-0.2 * i:.1f},{0.35 * i:.2f}"
            for i in range(1, 9)]  # fmt: skip
    return "\n".join([banner, hdr, *rows]) + "\n"


def blank_top_header() -> str:
    """A near-empty title row above the header."""
    title = "Study 2 data,,,"
    hdr = "id,score,rt,group"
    rows = [f"{i},{i * 3 % 7},{350 + 17 * i},{1 + i % 2}" for i in range(1, 9)]
    return "\n".join([title, hdr, *rows]) + "\n"


def plain() -> str:
    return "id,score,group\n" + "".join(f"{i},{i * 1.5},{'ab'[i % 2]}\n" for i in range(1, 8))


def main() -> None:
    write("qualtrics.csv", qualtrics())
    write("jspsych.csv", jspsych())
    write("jspsych_bom.csv", jspsych(), bom=True)
    write("inquisit.iqdat", inquisit())
    write("behaverse.csv", behaverse())
    write("behaverse_wide.csv", behaverse_wide())
    write("psychopy.csv", psychopy())
    write("eprime.txt", eprime(True))
    write("eprime_levels.txt", eprime(False))
    write("eprime_utf16.txt", eprime(True), encoding="utf-16")
    write("offset_header.csv", offset_header())
    write("blank_top_header.csv", blank_top_header())
    write("plain.csv", plain())
    write("notes.txt", "Some notes about the study.\nNothing tabular here.\n")
    write("empty.csv", "")
    # fileEncoding = "UTF-8-BOM" sniffing: the BOM goes before read.table() skips
    # blank lines, and the connection stops reading at the first invalid UTF-8 byte
    write("jspsych_bom_blank.csv", "\n" + jspsych(), bom=True)
    write(
        "behaverse_latin1.csv",
        behaverse().replace("participant_id", "participant_\xefd"),
        "latin-1",
    )
    write("jspsych_latin1_body.csv", jspsych().replace("<p>red</p>", "<p>r\xf6d</p>"), "latin-1")


if __name__ == "__main__":
    main()
