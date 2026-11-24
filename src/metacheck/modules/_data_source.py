"""Where did a paper's data come from? (port of ``R/data-source.R``, #695 point 18).

ethics_check asks for an ethics statement when the authors collected data from
people or animals themselves (now or in an earlier study of their own), or
analyse records about individual people that they obtained for this study. It
does not ask for one when the paper has no data about people (a review,
simulation, commentary, theory, tutorial, coding of published papers, or
figures about groups), or analyses an open resource that any researcher can
use (UK Biobank, NHANES, a dataset another team shared). :func:`_data_source`
sorts each paper into one of these by weighing signs in its text, and in the
results of other modules when they ran first.

metacheck made the rule from 954 PsyArXiv and medRxiv preprints labelled by
language-model readers; on 199 preprints labelled after the rule was fixed it
decided right whether a statement is needed for 86.9% (the earlier live-data
search: 83.4%). The patterns below are metacheck's, unchanged (PCRE, searched
with ``perl = TRUE``); the comments on each say what it stands for.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from metacheck._r import grepl

__all__: list[str] = []

# months and seasons followed by a year are dates, not citations
_DS_MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|November|December|"
    "Spring|Summer|Autumn|Fall|Winter"
)

# the signs that the authors collected data from people or animals; each but
# `generic` names people or animals
_DS_KINDS: dict[str, str] = {
    "consent": (
        r"\b(gave|give|provided|obtained|signed)\s+(their\s+)?"
        r"(written|verbal|oral|electronic|online|digital)?\s*(and\s+\w+\s+)?"
        r"(informed\s+)?(consent|assent)|consent\s+(was|were)\s+obtained"
    ),
    "recruit": (
        r"\b(were|was|been)\s+recruited\b|\brecruited\s+(via|through|from|using|on)\b|"
        r"\brecruitment\s+(took place|was|occurred)|\bwe\s+(recruited|enrolled)\b"
    ),
    "pay": (
        r"\bprolific\b|\bm-?turk\b|mechanical turk|\bsona\b|cloudresearch|course credits?|"
        r"\b(were|was)\s+(paid|compensated|reimbursed)\b|\bmonetary\s+(compensation|reward)|"
        r"gift\s+(cards?|vouchers?)|\bhonorarium\b|received\s+[$\u00a3\u20ac]\s?\d"
    ),
    "sample": (
        r"\bM\s?age\b|\bmean\s+age\b|\bage\s+range\b|\baged\s+(between\s+)?\d|"
        r"\b\d+(\.\d+)?\s?%\s+(were\s+)?(female|women|male|men)\b|"
        r"\(\s?\d+\s+(female|women|males?|men)\b|\b\d+\s+(women|men|females|males)\s+(and|,)"
    ),
    "procedure": (
        r"\bparticipants\s+(completed|were\s+(asked|instructed|presented|shown|randomly|"
        r"randomized|told|seated|tested))|\bwere\s+randomly\s+assigned"
    ),
    "generic": (
        r"\b(data|responses)\s+(were|was)\s+collected\b|\bwe\s+(collected|tested|measured)\b|"
        r"\bdata\s+collection\s+(took\s+place|was\s+(conducted|carried|completed)|began|"
        r"started|lasted)|\bwere\s+excluded\s+(from|because|due)"
    ),
    "interview": (
        r"semi-?structured\s+interviews?|focus\s+groups?\s+(were|was)|"
        r"\binterviews\s+(were|lasted)|\bwe\s+(surveyed|interviewed)\b"
    ),
    "animal": (
        r"\b(mice|rats|monkeys|macaques|zebrafish|animals|pigs|dogs)\s+were\s+"
        r"(housed|kept|maintained|trained|tested|bred|purchased|obtained)|"
        r"\banimal\s+care\b|\bhoused\s+(in|under|individually)"
    ),
}
# the kinds that name people or animals, as the rule counts them
_DS_PEOPLE_KINDS = ("consent", "recruit", "pay", "sample", "procedure", "interview", "animal")

_DS_FUTURE = (
    r"\bwill\s+be\s+(recruited|collected|enrolled|invited|asked|randomi[sz]ed|conducted|"
    r"obtained)|\bwe\s+will\s+(recruit|collect|enrol|invite|conduct|obtain)|"
    r"\bparticipants\s+will\b|\bplanned\s+sample\s+size"
)
_DS_FIRST = r"\b(we|our)\b"
# a sentence about other people's study (matched with case): an author and
# year, "et al.", a numbered reference, the studies a review included, a
# meta-analysis, or the original study of a secondary analysis
_DS_OTHERS = (
    r"\b(?!(?:" + _DS_MONTHS + r")\b)[A-Z][a-z]+(\s+et\s+al\.?|\s+(and|&)\s+[A-Z][a-z]+)?"
    r"(,\s*|\s*\()(19|20)\d{2}[a-z]?\s*[),;]"
    r"|\bet\s+al\.?[,\s]|\[\d+(\s*[,\u2013-]\s*\d+)*\]"
    r"|\b(?i:included|eligible|selected|primary|original|individual|reviewed|pooled)\s+"
    r"(\w+\s+){0,2}(?i:studies|trials|articles|papers|reports|publications|samples|cohorts)\b"
    r"|\b(?i:studies|trials|articles|papers|reports|publications|cohorts)\s+(\w+\s+){0,6}"
    r"(?i:enrolled|included|comprising|comprised|involving|recruited|reported)\b"
    r"|(?i:\bmeta-?analy)|(?i:\b(in|of|by|from)\s+the\s+(original|parent|primary|main)\s+"
    r"(study|trial|survey|cohort|authors))"
)
# figures about groups or things, not about individual people
_DS_AGGREGATE = (
    r"\baggregated?\s+(data|statistics|counts|rates|estimates|level|figures)|"
    r"\b(country|national|state|county|regional|population|website|tract|area|district)"
    r"-level\b|\bno\s+individual-level|\bsurveillance\s+data|"
    r"\bmortality\s+(database|statistics|data)\b|global\s+burden\s+of\s+disease|"
    r"\bgoogle\s+trends\b|\blanguage\s+models?\s+(were|was)\s+"
    r"(tested|evaluated|prompted|queried)"
)
# the paper says itself that it has no data from people
_DS_NO_DATA = (
    r"\bno\s+(new\s+|primary\s+|original\s+)?(data|datasets?)\s+(were|was)\s+"
    r"(collected|generated|created|gathered|analy[sz]ed)|"
    r"\bno\s+(human\s+)?(participants|subjects)\s+(were|was)\s+(recruited|involved|tested)|"
    r"\b(did|does)\s+not\s+involve\s+(any\s+)?(human|participants|new\s+data)|"
    r"\bthis\s+(paper|article|manuscript)\s+is\s+(a\s+)?(purely\s+)?(theor|conceptual)|"
    r"\bdata\s+sharing\s+(is\s+)?not\s+applicable"
)
# the authors' own earlier data
_DS_EARLIER = (
    r"\bdata\s+(were|was)\s+(collected|drawn|taken)\s+(as\s+part\s+of|from)\s+"
    r"(a|an|the|our)\s+(larger|ongoing|broader|previous|earlier|prior|parent)\b|"
    r"\bpreviously\s+(published|reported|described|collected)\s+(data|dataset|sample|cohort)|"
    r"\bas\s+part\s+of\s+(a|an|the)\s+(larger|ongoing|broader)\s+(study|project|trial)|"
    r"\bbaseline\s+data\s+(from|of)\b|"
    r"\bsecondary\s+analysis\s+of\s+(our|a|the)\s+(\w+\s+){0,3}(trial|study)|"
    r"\bdata\s+from\s+(our|a)\s+(previous|earlier|prior)\s+(study|trial)"
)
# existing data about people: wording that names the source of the data
_DS_EXISTING = (
    r"secondary\s+(data\s+)?analys|\bre-?analy[sz]|publicly\s+available\s+(data|dataset)|"
    r"\bopen\s+data\s?set|archival\s+data|existing\s+(data|dataset)|"
    r"\bdata\s+(were|was)\s+(obtained|drawn|taken|retrieved|downloaded|extracted|sourced)"
    r"\s+from|\bwe\s+(used|analy[sz]ed|leveraged)\s+data\s+from|"
    r"\b(data|participants|patients|records|samples)\s+from\s+the\s+[^.;]{0,60}"
    r"(registry|biobank|cohort|survey|database|study)\b|"
    r"\b(using|from)\s+(electronic\s+)?(health|medical)\s+records|"
    r"\bretrospective(ly)?\s+(cohort|study|analysis|review|chart|observational|identified|"
    r"collected|reviewed)|\bchart\s+review"
)
# a named dataset (matched with case): a proper name ending in Study, Survey,
# Cohort, Biobank, Registry, Initiative or Consortium, or an abbreviation of
# four or more capitals followed by study, cohort, participants or data
_DS_NAMED_STOP = (
    r"(?:In|As|For|A|An|The|This|That|Our|Each|Both|Simulation|Pilot|Present|Current|"
    r"Qualitative|Quantitative|Longitudinal|Cross-Sectional|Cross-sectional|Observational|"
    r"Prospective|Retrospective|Experimental|Main|First|Second|Third|Replication|Validation|"
    r"Online|SoSci|Case|Cohort|Feasibility|Original|Previous|Future|Follow-up|Mixed-Methods|"
    r"Preregistered|Registered|Empirical|Field|Lab|Laboratory|Supplementary|Study|Experiment|"
    r"Survey|Sample|Discovery|Replication|Exploratory|Confirmatory|National|Web|Internet|"
    r"Delphi)\b"
)
_DS_NAMED = (
    r"\b(?!"
    + _DS_NAMED_STOP
    + r")[A-Z][A-Za-z/&-]*(?:\s+(?:of|and|for|the|in|on)|\s+(?!"
    + _DS_NAMED_STOP
    + r")[A-Z0-9][A-Za-z0-9/&-]*){0,7}\s+"
    r"(?:Study|Survey|Cohort|Biobank|Registry|Initiative|Consortium)\b"
    r"|\b(?!(?:COVID|ADHD|PTSD|SARS|AIDS|STEM|LGBT\w*|ANOVA|GWAS|WEIRD|BOLD|NIRS|RNA\w*|"
    r"MMSE|OSF|APA)\b)[A-Z]{4,}[A-Za-z0-9/-]*\s+"
    r"(?:study|cohort|release|wave|participants|data|dataset)\b"
)
# a heading about the data or the method
_DS_HEAD_SOURCE = r"method|data|sample|participant|cohort|population|design|setting|subjects"
# an open resource
_DS_PUBLIC = (
    r"publicly\s+(available|accessible)|public(ly)?[- ]use\b|"
    r"open[- ]access\s+(data|dataset|database|resource)|(freely|openly)\s+(available|accessible)|"
    r"available\s+(to|for)\s+(all\s+)?(bona\s+fide\s+|qualified\s+|approved\s+|registered\s+)?"
    r"researchers|(downloaded|obtained|accessed|retrieved)\s+from\s+"
    r"(https?://|the\s+[\w-]+\s+(website|repository|portal))|"
    r"\bno\s+(additional\s+|further\s+|separate\s+)?(ethic\w*|irb|institutional)\s+"
    r"(approval|review)\s+(was|is)\s+(required|needed|necessary)|\bexempt(ed)?\s+from\b|"
    r"\bopen\s+(data|dataset|resource)\b"
)
# records obtained for this study
_DS_RECORDS = (
    r"(cannot|can\s+not|not)\s+be\s+(shared|made\s+(public|available))|"
    r"not\s+publicly\s+available|due\s+to\s+(privacy|legal|ethical|confidentiality|"
    r"data\s+protection)\s+(restrictions|reasons|regulations|concerns)|"
    r"(hospital|medical\s+cent(er|re)|clinic|health\s+system|health\s+service)\S*\s+"
    r"(records|database|data)\b|\b(this|the\s+present|the\s+current)\s+study\s+was\s+approved|"
    r"\bwaiver\s+of\s+(informed\s+)?consent|consent\s+was\s+waived"
)
# records of individual people
_DS_INDIVIDUAL = (
    r"(?<!no\s)\b(individual|patient|participant|person)-level\s+(data|records)|"
    r"\b(patients|participants|individuals|adults|children|women|men|respondents)\s+"
    r"(were|was)\s+(included|identified|eligible|excluded|followed|enrolled)|"
    r"\bcohort\s+of\s+[\d,]+\s+\w+|\b(health|medical|hospital|clinical)\s+records\s+of\b|"
    r"\blinked\s+(records|data)"
)
# the paper says it recruited or contacted nobody
_DS_NO_RECRUIT = (
    r"\bno\s+(individuals?|patients?|participants?|subjects?|persons?|new\s+\w+)\s+"
    r"(were|was)\s+(recruited|contacted|enrolled|approached)"
)
# a named cohort, biobank or data release as the one that recruited
_DS_COHORT = (
    r"\b(cohort|biobank|registry|consortium|panel|release\s+\d|longitudinal\s+study|"
    r"(?-i:[A-Z]{3,})(\s+study)?)\b"
)
# the paper type, named in the title or abstract
_DS_TYPE: dict[str, str] = {
    "review": (
        r"systematic\s+review|scoping\s+review|meta-?analy|umbrella\s+review|"
        r"literature\s+review|narrative\s+review|review\s+of\s+the\s+literature|\bprisma\b"
    ),
    "commentary": (
        r"\bin\s+this\s+(commentary|essay|perspective|opinion|viewpoint|piece|comment)\b|"
        r"\b(we|I)\s+argue\b"
    ),
    "simulation": (
        r"simulation\s+stud|monte\s+carlo|\bwe\s+simulate|\bsimulated\s+(data|datasets)|"
        r"\bsimulations\s+(show|showed|reveal|demonstrate|indicate)"
    ),
    "tutorial": (
        r"\btutorial\b|\bprimer\b|\bR\s+package\b|"
        r"\bintroduc\w+\s+(a|an|the)\s+(new\s+)?(method|tool|package|approach|framework)"
    ),
    "protocol": r"\bprotocol\b|registered\s+report|stage\s+1",
}
_DS_COMMENT_TITLE = (
    r"^\s*(a\s+)?(comment|commentary|reply|response|rejoinder|editorial)\b|"
    r"\b(comment\s+on|reply\s+to|response\s+to|rejoinder\s+to)\b"
)
# the same kinds of paper, suggested only by sentences in the body
_DS_BODY: dict[str, str] = {
    "simulation": (
        r"simulation\s+stud|monte\s+carlo|\bwe\s+simulated\b|"
        r"\bsimulated\s+(data|datasets?|samples|participants)|"
        r"\bdata\s+(were|was)\s+(simulated|generated)|"
        r"number\s+of\s+(simulations|iterations|replications|repetitions)|"
        r"\b\d[\d,]*\s+(simulations|simulated|iterations|replications|repetitions)\b|"
        r"simulation\s+conditions|set\.seed|data[- ]generating"
    ),
    "review": (
        r"databases?\s+(were|was)\s+searched|search\s+strateg|\bwe\s+searched\b|"
        r"search\s+terms|\bprisma\b|inclusion\s+criteria\s+for\s+(studies|articles)"
    ),
    "papers": (
        r"\b(coded|screened)\b.{0,40}\b(articles|papers|publications|preregistrations|"
        r"abstracts)\b|\barticles\s+published\s+in\b|"
        r"\bsample\s+of\s+(articles|papers|publications)\b"
    ),
    "theory": r"\btheorem\b|\blemma\b|\bcorollary\b",
}
_DS_HEAD_PARTICIPANTS = (
    r"^\s*([0-9.]+\s*)?(participants?|subjects|sample|procedures?|apparatus|stimuli|"
    r"participants\s+and\s+procedures?|recruitment|study\s+population|animals)\s*$"
)
_DS_HEAD_METHOD = (
    r"method|participant|subject|\bsample\b|procedure|\bdesign\b|material|measure|recruit|"
    r"study\s+population|data\s+collection|setting|(study|experiment)\s*\d"
)

# Well-known open resources: names of research resources that any researcher can
# download or apply for, and that come already de-identified. ethics_check does
# not ask for an ethics statement for a paper that analyses one of these.
_DS_OPEN_RESOURCES = (
    r"\bUK\s+Biobank\b",
    r"\bNHANES\b",
    r"National\s+Health\s+and\s+Nutrition\s+Examination\s+Survey",
    r"\bBRFSS\b",
    r"Behavioral\s+Risk\s+Factor\s+Surveillance",
    r"\bNHIS\b",
    r"National\s+Health\s+Interview\s+Survey",
    r"\bMEPS\b",
    r"Medical\s+Expenditure\s+Panel\s+Survey",
    r"\bNSDUH\b",
    r"National\s+Survey\s+on\s+Drug\s+Use",
    r"\bYRBSS?\b",
    r"Youth\s+Risk\s+Behavior",
    r"\bAdd\s+Health\b",
    r"National\s+Longitudinal\s+(Study|Survey)\s+of\s+(Adolescent|Youth)",
    r"\bNLSY\d*\b",
    r"Health\s+and\s+Retirement\s+Study",
    r"English\s+Longitudinal\s+Study\s+of\s+Age?ing",
    r"Survey\s+of\s+Health,?\s+Age?ing\s+and\s+Retirement\s+in\s+Europe",
    r"\bCHARLS\b",
    r"\bMIDUS\b",
    r"Midlife\s+in\s+the\s+United\s+States",
    r"\bABCD\s+[Ss]tudy",
    r"Adolescent\s+Brain\s+Cognitive\s+Development",
    r"\bAll\s+of\s+Us\b",
    r"\bADNI\b",
    r"Alzheimer.s\s+Disease\s+Neuroimaging\s+Initiative",
    r"\bPPMI\b",
    r"Parkinson.s\s+Progression\s+Markers",
    r"Human\s+Connectome\s+Project",
    r"\bOpenNeuro\b",
    r"\bMIMIC-?(III|IV|CXR)?\b",
    r"\beICU\b",
    r"\bPhysioNet\b",
    r"\bTCGA\b",
    r"Cancer\s+Genome\s+Atlas",
    r"\bGTEx\b",
    r"1000\s+Genomes",
    r"\bgnomAD\b",
    r"Gene\s+Expression\s+Omnibus",
    r"\bdbGaP\b",
    r"\bSEER\s+(database|program|registry|data)",
    r"Demographic\s+and\s+Health\s+Surveys?",
    r"Multiple\s+Indicator\s+Cluster\s+Surveys?",
    r"World\s+Values\s+Survey",
    r"European\s+Social\s+Survey",
    r"General\s+Social\s+Survey",
    r"Understanding\s+Society",
    r"\bUKHLS\b",
    r"British\s+Household\s+Panel",
    r"Millennium\s+Cohort\s+Study",
    r"British\s+Cohort\s+Study",
    r"National\s+Child\s+Development\s+Study",
    r"British\s+Social\s+Attitudes",
    r"Socio-?Economic\s+Panel",
    r"\bLISS\s+panel",
    r"\bHILDA\b",
    r"\bPISA\b",
    r"\bTIMSS\b",
    r"\bPIRLS\b",
    r"\bICILS\b",
    r"Early\s+Childhood\s+Longitudinal\s+Study",
    r"Fragile\s+Families",
    r"Future\s+of\s+Families",
    r"Global\s+Burden\s+of\s+Disease",
    r"\bFinnGen\b",
    r"\bKaggle\b",
    r"American\s+Community\s+Survey",
    r"Current\s+Population\s+Survey",
    r"\bIPUMS\b",
    r"Eurobarometer",
    r"Afrobarometer",
    r"American\s+National\s+Election\s+Stud",
    r"Cooperative\s+(Congressional\s+)?Election\s+Study",
    r"Project\s+Implicit",
    r"\bCHILDES\b",
    r"\bTalkBank\b",
    r"National\s+Survey\s+of\s+Children.s\s+Health",
    r"\bNESARC\b",
    r"Monitoring\s+the\s+Future",
    r"China\s+Family\s+Panel\s+Studies",
    r"China\s+Health\s+and\s+Nutrition\s+Survey",
    r"\bKNHANES\b",
    r"Longitudinal\s+Aging\s+Study\s+in\s+India",
    r"\bICPSR\b",
    r"UK\s+Data\s+Service",
    r"Human\s+Mortality\s+Database",
    r"\bGWAS\s+Catalog\b",
    r"\bOpenAlex\b",
    r"Violence\s+Against\s+Children\s+(and\s+Youth\s+)?Surveys?",
)
_DS_OPEN_PATTERN = "|".join(_DS_OPEN_RESOURCES)

# signs in the results of other modules
_DS_COL_ID = (
    r"^(participant|subject|subj|ppt|pp|pid|prolific|worker|response|respondent|user)_?"
    r"(id|nr|no|num|number|code)?$|^(id|sub|ppn|responseid|workerid|prolific_pid)$"
)
_DS_COL_SIM = (
    r"^(iter|iteration|rep|replication|seed|nsim|n_sim|sim|bias|rmse|mse|coverage|true_\w+|"
    r"power|type_?1|alpha)(_|\d|$)"
)
_DS_FILE_MATERIAL = (
    r"\.(qsf|psyexp|psydat|osexp|iqx|jasp|ebs\d?|es\d?)$|jspsych|consent|debrief|"
    r"questionnaire|survey|stimul|instruction|ethic|irb|information[ _-]?sheet|recruit"
)
_DS_FILE_SIM = r"simulat|\bsim[_-]|_sim\b|monte"
_DS_PREREG_PEOPLE = (
    r"participant|recruit|prolific|mturk|respondent|sample\s+of|students|patients|children|adults"
)
_DS_HUMAN_CONCEPTS = ("age", "gender", "race", "reaction_time", "likert", "accuracy")

_DS_SIGN_WORDS = {
    "consent": "consent",
    "recruit": "recruitment",
    "pay": "payment or a recruitment platform",
    "sample": "a sample description",
    "procedure": "what participants did",
    "interview": "interviews or focus groups",
    "animal": "animal housing",
}


def _ds_has(pattern: str, x: Sequence[Any], ignore_case: bool = True) -> np.ndarray:
    """``grepl(pattern, x, perl = TRUE)`` that keeps going when one text cannot be searched
    (R's ``.ds_has()``/``.search_kept()``); a missing text is searched as ""."""
    if len(x) == 0:
        return np.zeros(0, dtype=bool)
    texts = [
        "" if t is None or t is pd.NA or (isinstance(t, float) and t != t) else str(t) for t in x
    ]
    return np.array(grepl(pattern, texts, ignore_case=ignore_case, perl=True), dtype=bool)


def _ds_count(flag: Any, pid: Any, ids: Sequence[str]) -> np.ndarray:
    """The number of true values per paper, in the order of *ids*."""
    out = np.zeros(len(ids), dtype=int)
    if len(flag) == 0:
        return out
    pos = {p: i for i, p in enumerate(ids)}
    for f, p in zip(np.asarray(flag, dtype=int), pid, strict=True):
        i = pos.get(p)
        if i is not None:
            out[i] += int(f)
    return out


def _ds_module_table(
    prev: Mapping[str, Mapping[str, Any]], module: str, item: str, ids: Sequence[str]
) -> pd.DataFrame | None:
    """A module's table, with every row given a paper_id (``.ds_module_table()``)."""
    x = (prev.get(module) or {}).get(item)
    if not isinstance(x, pd.DataFrame) or len(x) == 0:
        return None
    x = x.copy()
    if len(ids) == 1:
        x["paper_id"] = ids[0]
    elif "paper_id" not in x.columns:
        return None
    x["paper_id"] = x["paper_id"].astype(object).map(lambda v: None if pd.isna(v) else str(v))
    return x


def _ds_col(x: pd.DataFrame, name: str) -> list[Any]:
    """``as.character(x[[name]] %||% NA)``."""
    if name not in x.columns:
        return [None] * len(x)
    return [None if pd.isna(v) else str(v) for v in x[name].tolist()]


def _ds_module_signs(
    prev: Mapping[str, Mapping[str, Any]], ids: Sequence[str], paper: Any = None
) -> dict[str, np.ndarray]:
    """Counts from the results of other modules, per paper (``.ds_module_signs()``)."""
    zero = np.zeros(len(ids), dtype=int)
    names = (
        "power_n",
        "power_apriori",
        "p_n",
        "test_n",
        "dep_earlier_text",
        "dep_others_text",
        "dep_earlier",
        "people_cols",
        "id_cols",
        "sim_cols",
        "material_files",
        "sim_files",
        "prereg_people",
    )
    out = {k: zero.copy() for k in names}

    pw = _ds_module_table(prev, "power", "table", ids)
    if pw is not None:
        out["power_n"] = _ds_count(np.ones(len(pw), dtype=bool), pw["paper_id"], ids)
        kind = [(v or "").lower() if v is not None else "" for v in _ds_col(pw, "power_type")]
        out["power_apriori"] = _ds_count(
            [k in ("apriori", "a priori", "a-priori") for k in kind], pw["paper_id"], ids
        )
    # p-values: from stat_p_exact, or found here when it did not run
    p: Any
    if prev.get("stat_p_exact") is not None:
        p = _ds_module_table(prev, "stat_p_exact", "table", ids)
    else:
        p = None
        if paper is not None:
            from metacheck.text.extract import extract_p_values

            try:
                p = extract_p_values(paper)
            except Exception:  # R: tryCatch(extract_p_values(paper), error = function(e) NULL)
                p = None
        if isinstance(p, pd.DataFrame) and len(p) and len(ids) == 1:
            p = p.copy()
            p["paper_id"] = ids[0]
    if isinstance(p, pd.DataFrame) and len(p) and "paper_id" in p.columns:
        out["p_n"] = _ds_count(
            np.ones(len(p), dtype=bool),
            [None if pd.isna(v) else str(v) for v in p["paper_id"]],
            ids,
        )
    st = _ds_module_table(prev, "stat_check", "table", ids)
    if st is not None:
        out["test_n"] = _ds_count(np.ones(len(st), dtype=bool), st["paper_id"], ids)

    own = _ds_module_table(prev, "repo_check", "ownership", ids)
    own_urls: set[Any] = set()
    if own is not None and {"ownership", "repo_url"} <= set(own.columns):
        text_src = np.array([s == "text" for s in _ds_col(own, "source")])
        own_kind = np.array(_ds_col(own, "ownership"), dtype=object)
        out["dep_earlier_text"] = _ds_count(
            (own_kind == "own_earlier_data") & text_src, own["paper_id"], ids
        )
        out["dep_others_text"] = _ds_count(
            (own_kind == "others_data") & text_src, own["paper_id"], ids
        )
        out["dep_earlier"] = _ds_count(own_kind == "own_earlier_data", own["paper_id"], ids)
        own_urls = set(own["repo_url"][own_kind == "own_this"].tolist())
    files = _ds_module_table(prev, "repo_check", "table", ids)
    if files is not None and "repo_url" in files.columns:
        files = files[files["repo_url"].isin(own_urls)]
        fname = _ds_col(files, "file_name")
        fpath = [p_ if p_ else n for p_, n in zip(_ds_col(files, "file_path"), fname, strict=True)]
        out["material_files"] = _ds_count(_ds_has(_DS_FILE_MATERIAL, fname), files["paper_id"], ids)
        out["sim_files"] = _ds_count(_ds_has(_DS_FILE_SIM, fpath), files["paper_id"], ids)
    cols = _ds_module_table(prev, "data_check", "table", ids)
    if cols is not None and "repo_url" in cols.columns:
        cols = cols[cols["repo_url"].isin(own_urls)]
        concept = [c or "" for c in _ds_col(cols, "concept")]
        cname = _ds_col(cols, "column_name")
        out["people_cols"] = _ds_count(
            [c in _DS_HUMAN_CONCEPTS or c.startswith("qualtrics") for c in concept],
            cols["paper_id"],
            ids,
        )
        out["id_cols"] = _ds_count(_ds_has(_DS_COL_ID, cname), cols["paper_id"], ids)
        out["sim_cols"] = _ds_count(_ds_has(_DS_COL_SIM, cname), cols["paper_id"], ids)
    pre = _ds_module_table(prev, "prereg_check", "table", ids)
    if pre is not None:
        keep = [c for c in pre.columns if c not in ("link", "id", "ia_url", "paper_id")]
        txt = []
        for r in range(len(pre)):
            vals: list[str] = []
            for c in keep:
                v = pre[c].iloc[r]
                items = v if isinstance(v, (list, tuple, np.ndarray)) else [v]
                vals.extend(str(i) for i in items if not (i is None or pd.isna(i)) and str(i) != "")
            txt.append(" ".join(vals))
        out["prereg_people"] = _ds_count(_ds_has(_DS_PREREG_PEOPLE, txt), pre["paper_id"], ids)
    return out


def _data_source(
    sent: pd.DataFrame,
    ids: Sequence[str],
    info: pd.DataFrame | None = None,
    prev: Mapping[str, Mapping[str, Any]] | None = None,
    paper: Any = None,
    forced_none: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Port of ``.data_source()``: sort each paper by where its data came from.

    *sent* is ``text_search(paper)`` (every sentence, with ``paper_id``, ``text``,
    ``header`` and ``section_type``), *ids* the paper IDs, *info* a table with
    ``paper_id`` and ``title``, *prev* the results of the modules that ran
    before, and *paper* the paper (its p-values are found in the text when
    ``stat_p_exact`` did not run). *forced_none* maps the IDs of papers that
    bibr classifies as a type without data from participants to the type's name
    (D85): they are sorted as ``none``.

    Returns a dict with ``summary`` (one row per paper: ``paper_id``,
    ``data_source``, ``needs_ethics``, ``signs``), ``evidence`` (the sentences behind
    the decision, with a ``sign`` column) and ``features``.
    """
    prev = prev or {}
    forced_none = forced_none or {}
    ids = list(ids)
    n_ids = len(ids)
    pid_all = (
        [None if pd.isna(p) else str(p) for p in sent["paper_id"].tolist()] if len(sent) else []
    )
    keep_rows = [i for i, p in enumerate(pid_all) if p in set(ids)]
    sent = sent.iloc[keep_rows].reset_index(drop=True)
    pid = [pid_all[i] for i in keep_rows]

    title = dict.fromkeys(ids, "")
    if isinstance(info, pd.DataFrame) and "title" in info.columns and len(info):
        for p, t in zip(info["paper_id"].tolist(), info["title"].tolist(), strict=True):
            if str(p) in title:
                title[str(p)] = "" if pd.isna(t) else str(t)
    title_v = [title[i] for i in ids]

    def col(name: str) -> list[str]:
        if name not in sent.columns:
            return [""] * len(sent)
        return ["" if pd.isna(v) else str(v) for v in sent[name].tolist()]

    txt = col("text")
    header = col("header")
    sec_type = col("section_type")
    is_abstract = np.array(
        [
            s == "abstract" or h.strip().lower() == "abstract"
            for s, h in zip(sec_type, header, strict=True)
        ],
        dtype=bool,
    )

    def cnt(flag: Any) -> np.ndarray:
        return _ds_count(flag, pid, ids)

    # each sentence: whose study, and when
    first = _ds_has(_DS_FIRST, txt)
    others = _ds_has(_DS_OTHERS, txt, ignore_case=False)
    named = _ds_has(_DS_NAMED, txt, ignore_case=False)
    future = _ds_has(_DS_FUTURE, txt)
    no_recruit = _ds_has(_DS_NO_RECRUIT, txt)
    cohort = _ds_has(_DS_COHORT, txt)
    meth_head = _ds_has(_DS_HEAD_METHOD, header)
    # a part about the data or the method; open-resource wording also counts in the abstract
    src_head = _ds_has(_DS_HEAD_SOURCE, header)
    src_abs = src_head | is_abstract

    own_rows = np.zeros(len(txt), dtype=bool)
    f: dict[str, np.ndarray] = {}
    for k, pat in _DS_KINDS.items():
        hit = _ds_has(pat, txt) & ~no_recruit
        oth = hit & (others | ((k == "recruit") & cohort & ~first) | (named & ~first))
        fut = hit & ~oth & future
        own = hit & ~oth & ~fut
        f[f"{k}_own"] = cnt(own)
        f[f"{k}_meth"] = cnt(own & meth_head)
        f[f"{k}_first"] = cnt(own & first)
        f[f"{k}_fut"] = cnt(fut)
        if k in _DS_PEOPLE_KINDS:
            own_rows = own_rows | own
    open_hit = _ds_has(_DS_OPEN_PATTERN, txt, ignore_case=False)
    public_hit = _ds_has(_DS_PUBLIC, txt)
    records_hit = _ds_has(_DS_RECORDS, txt)
    existing_hit = _ds_has(_DS_EXISTING, txt) & ~others
    earlier_hit = _ds_has(_DS_EARLIER, txt)
    aggregate_hit = _ds_has(_DS_AGGREGATE, txt)
    no_data_hit = _ds_has(_DS_NO_DATA, txt)
    individual_hit = _ds_has(_DS_INDIVIDUAL, txt) & ~others
    f["future"] = cnt(future)
    f["aggregate"] = cnt(aggregate_hit)
    f["individual"] = cnt(individual_hit)
    f["public_src"] = cnt(public_hit & src_abs)
    f["open_src"] = cnt(open_hit & src_abs)
    f["open_any"] = cnt(open_hit)
    f["open_title"] = _ds_has(_DS_OPEN_PATTERN, title_v, ignore_case=False).astype(int)
    f["records"] = cnt(records_hit)
    f["no_recruit"] = cnt(no_recruit)
    f["no_data"] = cnt(no_data_hit)
    f["earlier"] = cnt(earlier_hit)
    f["existing"] = cnt(existing_hit)
    f["existing_src"] = cnt(existing_hit & src_head)
    f["named_src"] = cnt(named & ~others & src_head)
    for k, pat in _DS_BODY.items():
        f[f"{k}_body"] = cnt(_ds_has(pat, txt))
    f["n_chars"] = cnt([len(t) for t in txt])

    # title and abstract, and the headings
    abstract = [
        " ".join(t for t, p, a in zip(txt, pid, is_abstract, strict=True) if p == one and a)
        for one in ids
    ]
    ta = [f"{t} . {a}" for t, a in zip(title_v, abstract, strict=True)]
    for k, pat in _DS_TYPE.items():
        f[f"{k}_ta"] = _ds_has(pat, ta).astype(int)
    f["comment_title"] = _ds_has(_DS_COMMENT_TITLE, title_v).astype(int)
    f["existing_ta"] = _ds_has(_DS_EXISTING, ta).astype(int)
    f["named_ta"] = _ds_has(_DS_NAMED, ta, ignore_case=False).astype(int)
    heads: list[list[str]] = []
    for one in ids:
        seen: dict[str, None] = {}
        for h, p in zip(header, pid, strict=True):
            if p == one and h:
                seen[h] = None
        heads.append(list(seen))
    f["head_participants"] = np.array(
        [int(_ds_has(_DS_HEAD_PARTICIPANTS, h).sum()) for h in heads], dtype=int
    )
    f["head_method"] = np.array([int(_ds_has(_DS_HEAD_METHOD, h).sum()) for h in heads], dtype=int)
    f["named_head"] = np.array(
        [int(_ds_has(_DS_NAMED, h, ignore_case=False).sum()) for h in heads], dtype=int
    )
    m = _ds_module_signs(prev, ids, paper)
    d: dict[str, np.ndarray] = {**f, **m}

    # the rule ----
    def nk(suffix: str, ks: Sequence[str] = _DS_PEOPLE_KINDS) -> np.ndarray:
        total = np.zeros(n_ids, dtype=int)
        for k in ks:
            total += d[f"{k}{suffix}"] > 0
        return total

    people = nk("_own") + (d["head_participants"] > 0)
    own_n = people + (d["generic_own"] > 0) + (d["power_n"] > 0) + (d["people_cols"] > 0)
    meth_n = nk("_meth") + (d["head_participants"] > 0)
    first_n = nk("_first", ("consent", "recruit", "interview"))
    contact = (d["recruit_own"] > 0) | (d["pay_own"] > 0) | (d["consent_own"] > 0)
    future_n = nk("_fut")
    results = (d["p_n"] > 0) | (d["test_n"] > 0)
    review = d["review_ta"] > 0
    method = (d["tutorial_ta"] > 0) | (d["simulation_ta"] > 0)
    comment = (d["comment_title"] > 0) | (d["commentary_ta"] > 0)
    typed = review | method | comment
    typed_body = (
        (d["review_body"] >= 2)
        | (d["simulation_body"] >= 3)
        | (d["theory_body"] > 0)
        | (d["papers_body"] > 0)
    )
    bare = (d["head_method"] == 0) & (d["sample_own"] == 0) & ~results
    # the own-collection evidence the paper type asks for
    own_ok = np.where(
        review,
        (first_n >= 1) & (own_n >= 3),
        np.where(
            method | comment, contact & (own_n >= 2), (people >= 1) & ((own_n >= 2) | (meth_n >= 1))
        ),
    ).astype(bool)
    out = np.array(["uncertain"] * n_ids, dtype=object)
    out[typed & ~own_ok] = "none"
    out[~typed & (typed_body | bare) & (own_n <= 1)] = "none"
    out[own_ok] = "own"
    # existing data: strong signs win unless the authors recruited or paid people themselves;
    # weak signs decide only when nothing else did
    ex_strong = (
        (d["existing_ta"] > 0)
        | (d["existing_src"] > 0)
        | (d["no_recruit"] > 0)
        | (d["dep_others_text"] > 0)
        | (d["named_ta"] > 0)
        | (d["named_src"] >= 2)
        | (d["named_head"] > 0)
    )
    ex_weak = (d["existing"] > 0) | (d["earlier"] > 0) | (d["dep_earlier_text"] > 0)
    own_contact = (d["recruit_own"] > 0) | (d["pay_own"] > 0)
    out[ex_strong & ~own_contact & ~typed] = "existing"
    out[ex_weak & ~typed & (out == "uncertain")] = "existing"
    out[ex_weak & ~typed & (out == "none") & ((d["sample_own"] > 0) | results)] = "existing"
    # figures about groups or things, or the paper's statement that it has no data, overrule
    # all but the authors' own contact with people
    agg = ((d["aggregate"] >= 2) & (d["individual"] == 0)) | (d["no_data"] > 0)
    out[agg & ~contact] = "none"
    out[(d["aggregate"] >= 2) & (d["individual"] > 0) & ~contact & ~typed & (out == "none")] = (
        "existing"
    )
    # the other modules: a preregistration about participants, an a-priori power analysis,
    # participant-number columns or study materials in the authors' own deposit, t- or F-tests,
    # or an earlier deposit of their own
    own_mod = (
        (d["prereg_people"] > 0).astype(int)
        + (d["power_apriori"] > 0)
        + (d["id_cols"] > 0)
        + (d["material_files"] > 0)
        + (d["test_n"] > 0)
        + (d["dep_earlier"] > 0)
    )
    out[
        (out == "existing")
        & (own_mod >= 1)
        & (people >= 1)
        & (d["existing_ta"] == 0)
        & (d["no_recruit"] == 0)
    ] = "own"
    out[(out == "uncertain") & (own_mod >= 1) & (people >= 1)] = "own"
    out[(out == "uncertain") & (own_mod == 0) & ((d["sim_cols"] > 0) | (d["sim_files"] > 0))] = (
        "none"
    )
    # protocols: collection in the future tense and no results
    out[
        ((future_n >= 1) | ((d["protocol_ta"] > 0) & (d["future"] >= 2)))
        & ~results
        & (out != "own")
    ] = "own"
    # open resources and obtained records
    open_ = (d["open_title"] > 0) | (d["open_src"] > 0) | (d["open_any"] >= 3)
    out[(out == "own") & open_ & ~own_contact & (d["records"] == 0)] = "open_resource"
    ex = out == "existing"
    out[ex & (d["records"] > 0)] = "existing_records"
    out[ex & (d["records"] == 0) & ((d["public_src"] > 0) | open_)] = "open_resource"
    out[ex & (d["records"] == 0) & ~((d["public_src"] > 0) | open_)] = "existing_unclear"
    # D85: a paper bibr classifies as a review, commentary, erratum, ... collects no data from
    # participants, whatever its sentences about participants say
    forced = np.array([i in forced_none for i in ids], dtype=bool)
    out[forced] = "none"
    out[d["n_chars"] == 0] = "no_text"

    needs: list[Any] = [
        True
        if o in ("own", "existing_records", "existing_unclear")
        else False
        if o in ("open_resource", "none")
        else None
        for o in out
    ]

    # what the decision rested on, in words: only the signs behind the class the paper got, so
    # that outweighed signs are not listed as reasons ----
    signs: list[str] = []
    for i, pid_i in enumerate(ids):
        people_s = [_DS_SIGN_WORDS[k] for k in _DS_PEOPLE_KINDS if d[f"{k}_own"][i] > 0]
        if d["head_participants"][i] > 0:
            people_s.append("a Participants or Procedure heading")
        existing_s: list[str] = []
        if d["existing_ta"][i] + d["existing_src"][i] + d["existing"][i] > 0:
            existing_s.append("existing-data wording")
        if d["earlier"][i] > 0 or d["dep_earlier_text"][i] > 0:
            existing_s.append("an earlier study")
        if d["named_ta"][i] + d["named_src"][i] + d["named_head"][i] > 0:
            existing_s.append("a named study, survey or cohort")
        if d["no_recruit"][i] > 0:
            existing_s.append("a statement that nobody was recruited")
        type_s: list[str] = []
        if review[i]:
            type_s.append("a review or meta-analysis named in the title or abstract")
        if d["tutorial_ta"][i] > 0:
            type_s.append("a tutorial or software paper named in the title or abstract")
        if d["simulation_ta"][i] > 0:
            type_s.append("a simulation study named in the title or abstract")
        if comment[i]:
            type_s.append("a commentary")
        if d["review_body"][i] >= 2:
            type_s.append("a literature search")
        if d["simulation_body"][i] >= 3:
            type_s.append("simulations")
        if d["theory_body"][i] > 0:
            type_s.append("theorems or proofs")
        if d["papers_body"][i] > 0:
            type_s.append("coding of published papers")
        if d["aggregate"][i] >= 2 and d["individual"][i] == 0:
            type_s.append("figures about groups or things")
        if d["no_data"][i] > 0:
            type_s.append("a statement that there are no data")
        if d["sim_cols"][i] + d["sim_files"][i] > 0 and own_mod[i] == 0:
            type_s.append("simulation files in the authors' deposit")
        if pid_i in forced_none:
            type_s.append(f"a paper type bibr classifies as {forced_none[pid_i]}")
        o = out[i]
        s: list[str]
        if o == "own":
            s = list(people_s)
            if future_n[i] > 0:
                s.append("collection in the future tense")
            if own_mod[i] > 0:
                s.append(
                    "results of other modules (preregistration, power analysis, tests or shared data)"
                )
        elif o == "existing_records":
            s = [*existing_s, "signs of records obtained for this study"]
        elif o == "existing_unclear":
            s = [*existing_s, *people_s]
        elif o == "open_resource":
            s = []
            if open_[i]:
                s.append("a well-known open resource")
            if d["public_src"][i] > 0:
                s.append("wording that the data are openly available")
        elif o == "none":
            s = type_s
        elif o == "uncertain":
            s = [*people_s, *existing_s, *type_s]
        else:
            s = []
        signs.append("; ".join(s))
    summary = pd.DataFrame(
        {
            "paper_id": pd.Series(ids, dtype="string"),
            "data_source": pd.Series(list(out), dtype="string"),
            "needs_ethics": pd.Series(needs, dtype="boolean"),
            "signs": pd.Series(signs, dtype="string"),
        }
    )

    # the sentences behind each decision ----
    cls_by = dict(zip(ids, out, strict=True))
    cls = np.array([cls_by.get(p) if isinstance(p, str) else None for p in pid], dtype=object)
    sign = np.array([None] * len(txt), dtype=object)
    sign[(cls == "own") & (own_rows | future)] = "own"
    sign[
        np.isin(cls, ["existing_records", "existing_unclear"])
        & (existing_hit | earlier_hit | no_recruit | (named & ~others & src_head))
    ] = "existing"
    sign[(cls == "existing_records") & records_hit] = "records"
    sign[(cls == "open_resource") & (open_hit | (public_hit & src_abs))] = "open"
    type4 = "|".join(list(_DS_TYPE.values())[:4])
    sign[(cls == "none") & (no_data_hit | aggregate_hit | (is_abstract & _ds_has(type4, txt)))] = (
        "none"
    )
    keep = np.array([s is not None for s in sign], dtype=bool)
    evidence = sent.loc[keep].reset_index(drop=True)
    evidence["sign"] = pd.Series([s for s in sign if s is not None], dtype="string")

    features = pd.DataFrame({**d, "paper_id": ids})
    return {"summary": summary, "evidence": evidence, "features": features}
