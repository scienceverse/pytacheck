"""Write ``parity/cases/rcompat_regex.yaml``: R's regex engines as :mod:`pytacheck._r.regex`
reproduces them (follow-ups F08-F12: TRE's parser, classes and case folding, TRE's
matcher, PCRE2's caseless matching, R's search loops).

    python tests/foundation/gen_rcompat_regex_cases.py
    PYTACHECK_RSCRIPT=/path/to/Rscript python -m parity generate --area rcompat_regex
"""

from pathlib import Path

import yaml

OUT = Path(__file__).resolve().parents[2] / "parity" / "cases" / "rcompat_regex.yaml"

cases = []
HELPERS = {
    "grepl": "pc_grepl",
    "gsub": "pc_gsub",
    "sub": "pc_sub",
    "all": "pc_regextract_all",
    "exec": "pc_regexec",
    "split": "pc_strsplit",
    "first": "pc_regextract",
}
PY = {
    "grepl": "grepl",
    "gsub": "gsub",
    "sub": "sub",
    "all": "regextract_all",
    "exec": "regexec",
    "split": "strsplit",
    "first": "regextract",
}


def add(cid, fn, pattern, x, repl=None, icase=False, perl=False, known=None):
    args = {}
    if fn == "split":
        args["x"] = {"$chr": list(x)}
        args["split"] = pattern
    else:
        args["pattern"] = pattern
        if repl is not None:
            args["replacement"] = repl
        args["x"] = {"$chr": list(x)}
    if icase:
        args["ignore.case"] = True
    if perl:
        args["perl"] = True
    case = {"id": cid, "r": HELPERS[fn], "py": f"parity.pyhelpers.{PY[fn]}", "args": args}
    if known:
        case["known_divergence"] = known
    cases.append(case)


# --- F12: a quantifier with nothing to repeat repeats the empty string (TRE)
S12 = ["a+b*c?", "ab/c", "a++b", ""]
for i, p in enumerate(["+", "+a", "a|*b", "(*a)", "{2}", "*", "?", "^*", "+*"[:1] + "?", "({2}a)"]):
    add(f"f12.empty_repeat.gsub.{i}", "gsub", p, S12, repl="")
    add(f"f12.empty_repeat.all.{i}", "all", p, S12)
# path_sanitize(replacement = "") builds gsub("+", "", x)
add("f12.path_sanitize_like", "gsub", "+", ["a+b/c", "ab/c"], repl="")

# --- F11: patterns TRE rejects (R errors; Python must raise too)
for i, p in enumerate(
    [
        "c++",
        r"\bc++\b",
        "a{2",
        "a{",
        "a**",
        "a*+",
        "a?+",
        "a{1}+",
        "a{1}*",
        "a?*",
        "+*",
        "a{x}",
        "a{}",
        "a{ 2}",
        "a{256}",
        "a{3,2}",
        "a{,0}",
        "[a",
        "[[.a.]]",
        "[[=a=]]",
        "[[:foo:]]",
        "(a",
        "\\",
        r"\1",
        "(?=a)",
        "[a-c-e]",
        "[z-a]",
        r"\x{4g}",
    ]
):
    add(f"f11.error.{i}", "grepl", p, ["c++", "aa"])
# ... and ones it accepts
S11 = ["aaaaaaa", "a{2}", "c++", "a*b"]
for i, p in enumerate(
    ["a{2}{3}", "a*{2}", "a??+", "a*??", "a{,2}", "a{,1}", "a{2 }", "a{,}", "a{2}?+", r"\Q.\E"]
):
    add(f"f11.accepted.{i}", "all", p, S11)

# --- F10: TRE word characters and classes follow glibc
S10 = [
    "x́foo",
    "x‿foo",
    "x⃝foo",
    "x‍foo",
    "xfoo",
    "x foo",
    "x-foo",
    "٠١",
    "été",
    "p ≤ .05",
    "±2.3 × 10⁻³",
    "− 1",
    "café — naïve",
    "µg ª º",
    "ǅ",
    "Ⅷ ⅺ",
    "  　",
    "",
]
for i, p in enumerate(
    [
        r"\w",
        r"\W",
        r"\w+",
        r"\W+",
        r"\bfoo\b",
        r"\Bfoo",
        r"\<foo\>",
        "[[:alnum:]]+",
        "[[:alpha:]]+",
        "[[:punct:]]+",
        "[[:upper:]]",
        "[[:lower:]]+",
        "[[:graph:]]+",
        "[[:print:]]+",
        "[[:space:]]+",
        "[[:blank:]]",
        "[[:cntrl:]]",
        "[^[:alnum:]]+",
        "[^[:punct:][:space:]]+",
        r"[\w]",
        "[[:digit:]]+",
    ]
):
    add(f"f10.class.all.{i}", "all", p, S10)
    add(f"f10.class.grepl.{i}", "grepl", p, S10)
# word boundaries where R restarts a search (search start and end count as boundaries)
SB = ["ab cd", " ab ", "thethe", "the theatre the", "a.b", "x ", " x", ""]
for i, p in enumerate([r"\b", r"\B", r"\<", r"\>", r"\bthe", r"the\b", r"\b ", r" \b", r"\b\w"]):
    add(f"f10.boundary.gsub.{i}", "gsub", p, SB, repl="|")
    add(f"f10.boundary.split.{i}", "split", p, SB)
# R's byte mode (all ASCII) vs wide mode (anything else) for negated classes in bounded repeats
for i, p in enumerate([r"\W{2}", r"\S{2,}", "[^[:alpha:]]{1,2}", r"(\w+\W+){0,2}c"]):
    add(f"f10.bytemode.ascii.{i}", "all", p, ["ab", "a b c", "12", "a-b"])
    add(f"f10.bytemode.wide.{i}", "all", p, ["ab", "a b c", "12", "a-b", "é"])

# --- F08 (TRE): ignore.case follows towupper()/towlower()
S8 = [
    "σ",
    "ς",
    "Σ",
    "ß",
    "ẞ",
    "SS",
    "µ",
    "μ",
    "Μ",
    "k",
    "K",
    "K",
    "s",
    "S",
    "ſ",
    "i",
    "I",
    "İ",
    "ı",
    "ǅ",
    "Ǆ",
    "ǆ",
    "é",
    "É",
    "ÿ",
    "Ÿ",
    "Ⅰ",
    "ⅰ",
]
for i, p in enumerate(
    [
        "σ",
        "ß",
        "µ",
        "ǅ",
        "K",
        "k",
        "s",
        "ſ",
        "i",
        "İ",
        "é",
        "ÿ",
        "Ⅰ",
        "[σ]",
        "[ǅ]",
        "[À-ÿ]",
        "[^À-ÿ]",
        "[[:upper:]]",
        "[[:lower:]]",
        "[^[:upper:]]",
        r"\w",
        "(?i)σ",
        "cafÉ",
    ]
):
    add(f"f08.tre_icase.{i}", "grepl", p, S8, icase=True)

# --- F08 (PCRE): caseless matching and ASCII classes
S8p = [
    *S8,
    "fİnanced",
    "fınanced",
    "financed",
    "FINANCED",
    "RANDOMİZED",
    "randomized",
    "worK",
    "work",
    "é",
    "a",
    "_",
    "1",
    " ",
    "ª",
    " ",
]
for i, p in enumerate(
    [
        "i",
        "I",
        "İ",
        "ı",
        "k",
        "K",
        "s",
        "ſ",
        "[a-z]",
        "[^a-z]",
        "[h-j]",
        "[^h-j]",
        r"\w",
        r"\W",
        r"[\w]",
        r"[^\W]",
        "[[:alpha:]]",
        "[[:^alpha:]]",
        "[[:upper:]]",
        "[[:^lower:]]",
        "[[:alnum:]_]",
        r"\p{Lu}",
        r"\P{Ll}",
        "randomi[sz]ed",
        "[Ff]inanced",
        r"f\wnanced",
        "f[^a-z]nanced",
        r"wor\w",
        r"\x{212a}",
        "(k)\\1",
        "ß",
        "σ",
    ]
):
    add(f"f08.pcre_icase.{i}", "grepl", p, S8p, icase=True, perl=True)
    add(f"f08.pcre_case.{i}", "grepl", p, S8p, perl=True)
for i, p in enumerate(
    [
        "(?i)f\\wnanced",
        "(?i)wor\\w",
        "(?i)f[^a-z]nanced",
        "a(?i)b|c",
        "(a(?i)b|c)",
        "(?i:a)b",
        "(?i)a(?-i)b",
        "((?i)a)b",
    ]
):
    add(
        f"f08.pcre_inline.{i}",
        "all",
        p,
        ["ab", "AB", "aB", "Ab", "C", "fİnanced", "worK", "financed"],
        perl=True,
    )
for i, p in enumerate([r"\i", r"A", r"\L", "[z-a]", r"[\d-z]", "[:alpha:]", "[[:foo:]]", r"\x"]):
    add(f"f08.pcre_error.{i}", "grepl", p, ["a"], perl=True)

# --- R's search loops (empty matches), gregexpr in byte steps for PCRE
SL = ["abxd", "abx", "aaa", "", "é", "éa", "Kb", "aéb"]
for i, p in enumerate(["x*", "a*?", "b*", "", "(|a)", "a|"]):
    for perl in (False, True):
        tag = "pcre" if perl else "tre"
        add(f"loop.gsub.{tag}.{i}", "gsub", p, SL, repl="-", perl=perl)
        add(f"loop.all.{tag}.{i}", "all", p, SL, perl=perl)
        add(f"loop.split.{tag}.{i}", "split", p, SL, perl=perl)
        add(f"loop.split_ascii.{tag}.{i}", "split", p, SL[:4], perl=perl)

# --- F09: TRE's minimal repetitions, matched by TRE's own algorithm
SL9 = [
    "ab123",
    "a1b22",
    "123",
    "x9",
    "abab",
    "xabab",
    "aaa",
    "xayby",
    "a_b_c",
    "a.b-c d",
    "read.csv('a.csv') and 'b'",
    "x \"y\" 'z'",
    "JaspColumn_x_Encoded_y_Encoded_z",
    "",
]
for i, (p, r) in enumerate(
    [
        (r".*?([0-9]+)$", r"\1"),
        (r"(a*?)(a*)", r"[\1|\2]"),
        (r"(a+?)(a*)", r"[\1|\2]"),
        (r"(.*?)b(.*)", r"[\1|\2]"),
        (r"(.*)b(.*?)", r"[\1|\2]"),
        (r"(.*?)(b.*)", r"[\1|\2]"),
        (r"x(.*?)y", r"[\1]"),
        (r"(.*?)(b|ab)(.*)", r"[\1|\2|\3]"),
        (r"^.*?[._ -]", ""),
        (r"""^.*?["']([^"']+)["'].*$""", r"\1"),
        (r"^JaspColumn_.*?_Encoded_", ""),
        (r"a{2}?+", "<>"),
        (r"(a|ab)*?b", "<>"),
    ]
):
    add(f"f09.sub.{i}", "sub", p, SL9, repl=r)
    add(f"f09.gsub.{i}", "gsub", p, SL9, repl=r)
    add(f"f09.exec.{i}", "exec", p, SL9)
SCOI = [
    "The authors declare no competing interests. The authors have no competing interest. Foo bar. Baz.",
    "The author has no competing interest. X. Y.",
    "All authors declare no conflict of interest. See x. Z",
    "The authors declare no competing interests.",
    "No conflict of interest. Z z. Q",
]
add(
    "f09.coi.single",
    "gsub",
    r"(^.*The author.+no.*competing.+interest.*?\.) [A-Z].*$",
    SCOI,
    repl=r"\1",
)
add(
    "f09.coi.alternatives",
    "gsub",
    r"(^.*The author.+no.*competing.+interest.*?\.) [A-Z].*$|(^.*All authors.+no.*conflict.+interest.*?\.) [A-Z].*$"
    r"|(^.*No conflict.+interest.*?\.) [A-Z].*$",
    SCOI,
    repl=r"\1\2\3",
)

# --- TRE: what only TRE's own matcher reproduces
ST = [
    "- ",
    "a- ",
    "-  ",
    "1",
    "a1",
    "-1",
    "ab1",
    " ",
    "",
    ".eb.",
    ".éb.",
    "AAabb",
    "-a≤aaa",
    "ca a–",
    "-–ß1.a-",
    "b- x",
    "x",
    "aK",
]
for i, p in enumerate(
    [
        r"(-*|([.-]{1,2})+\s\s)?",
        r"([[:punct:]]*|([.-]{1,2}é?)+\s{2,})?",  # merged states
        r"(^|\W?)+1",
        r"[ab]*?(\<|K??)",
        r"(\b|a?)+1",  # one empty path per alternative
        r"[[:alpha:]]*([[:punct:]]?){2}(\w{1,2}|$)",  # copies of a group
        r"(.[[:space:]]*)(\W?(ß{2}){0,1})+\W{1,2}",  # wide mode's copies, not leftmost
        r"([^[:alnum:]]{1,2}(b?\S?\b{0,1}){2,}|Σ?[a-c]([[:punct:]]{0,1} +b|ß?A\B))",
    ]
):
    add(f"tre.matcher.all.{i}", "all", p, ST)
    add(f"tre.matcher.grepl.{i}", "grepl", p, ST)
    add(f"tre.matcher.split.{i}", "split", p, ST)
for i, p in enumerate(
    [
        r"(A*)+([ab]+|[^[:alnum:]]ß+)",
        r"(a*)+",
        r"([.-]?)*",
        r"([^[:alnum:]]{1,2}){2,}\b*[a-c]",
        r"^*([^ab]{0,1}){1,2}[ab]",
        r"(\s*)+",
        r"(\S*){1,2}",
    ]
):
    add(f"tre.groups.exec.{i}", "exec", p, ST)
    add(f"tre.groups.gsub.{i}", "gsub", p, ST, repl=r"<\1>")

# --- PCRE: searches from inside a multibyte character (gregexpr)
MID_KNOWN = (
    "PCRE2's search from inside a UTF-8 character is undefined (R passes PCRE2_NO_UTF_CHECK); "
    "whether R's JIT tries a pattern starting with a class such as \\W there is not emulated"
)
for i, p in enumerate([r"\B", r"x*", r"(?!\w)", r"(?!b)", r"(?=.)", r"(?=\W)", r"(?!\W)"]):
    add(
        f"pcre.mid_char.all.{i}",
        "all",
        p,
        ["ΣA", "ΣΣ", "é", "éé", "Kb", "aΣ"],
        perl=True,
        known=MID_KNOWN if p == r"(?=\W)" else None,
    )

doc = {"area": "rcompat_regex", "cases": cases}
with open(OUT, "w", encoding="utf-8") as f:
    f.write(
        "# R regex follow-ups F08-F12 (see src/pytacheck/_r/_tre.py, _pcre.py, _tnfa.py, regex.py);\n"
        "# generated by tests/foundation/gen_rcompat_regex_cases.py.\n"
    )
    yaml.dump(doc, f, default_style='"', allow_unicode=True, sort_keys=False, width=100)
print(len(cases))
