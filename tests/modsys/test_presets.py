"""Preset lookup/expansion, select() precedence, offline filtering and the R form."""

from __future__ import annotations

import pytest

from pytacheck.module import module_run, use
from pytacheck.presets import (
    DEFAULT_PRESET,
    PresetError,
    Selection,
    deep_merge,
    expand,
    label,
    lookup,
    preset,
    preset_as_r,
    preset_list,
    select,
)
from tests.modsys.helpers import mod_src

DEFAULT = [
    "prereg_check", "funding_check", "coi_check", "power", "repo_check", "code_check",
    "stat_check", "stat_p_exact", "stat_p_nonsig", "stat_effect_size", "marginal",
    "ref_accuracy", "ref_replication", "ref_retraction", "ref_pubpeer", "ref_summary",
]  # fmt: skip


@pytest.fixture
def lab(ms):
    """A path pack 'lab' with modules (one needs the network) and presets."""
    mods = {
        "apa": mod_src("apa", args="strict=False", body='return {"summary_text": "apa"}'),
        "power_sesoi": mod_src("power_sesoi"),
        "netcheck": mod_src("netcheck", requires=("network",)),
        "llmcheck": mod_src("llmcheck", keywords=("results", "llm")),
    }
    presets = {
        "default": {
            "description": "metacheck's defaults plus psychology checks",
            "extends": ["metacheck::default"],
            "replace": {"power": "power_sesoi"},
            "exclude": ["ref_pubpeer"],
            "modules": ["apa", "netcheck"],
            "args": {"apa": {"strict": True}, "stat_check": {"a": {"b": 1}}},
        },
        "minimal": {"description": "Only lab checks", "modules": ["apa", "llmcheck"]},
        "ext": {"extends": ["minimal"], "modules": ["marginal"]},
    }
    folder = ms.pack(ms.root / "lab", "lab", mods, presets=presets)
    ms.pin("lab", {"path": str(folder)})
    return ms


def test_builtin_default_matches_report_formals() -> None:
    assert [m for m, _ in expand(DEFAULT_PRESET)] == DEFAULT
    assert [m for m, _ in preset("metacheck")] == DEFAULT
    assert [m for m, _ in preset("default")] == DEFAULT
    assert [m for m, _ in preset("repository")] == [
        "repo_check", "code_check", "data_check", "codebook_check",
    ]  # fmt: skip


def test_library_default_ignores_config(lab, monkeypatch) -> None:
    lab.config({**_packs(lab), "preset": "lab::minimal"})
    monkeypatch.setenv("PYTACHECK_PRESET", "lab")
    sel = select()
    assert isinstance(sel, Selection)
    assert (sel.preset, sel.source) == (DEFAULT_PRESET, "default")
    assert sel.modules == DEFAULT


def _packs(ms) -> dict:
    import json

    return {"packs": json.loads(ms.config_file.read_text())["packs"]}


@pytest.mark.parametrize(
    ("setup", "expected"),
    [
        ({}, (DEFAULT_PRESET, "default")),
        ({"user": "lab::minimal"}, ("lab::minimal", "user")),
        ({"user": "lab::minimal", "project": "lab::ext"}, ("lab::ext", "project")),
        ({"project": "lab::ext", "env": "lab"}, ("lab", "PYTACHECK_PRESET")),
        ({"env": "lab", "use": "metacheck::validated"}, ("metacheck::validated", "use()")),
        ({"use": "lab::ext", "arg": "lab::minimal"}, ("lab::minimal", "argument")),
    ],
)
def test_select_precedence(lab, monkeypatch, tmp_path, setup, expected) -> None:
    import json

    user = tmp_path / "user.json"
    user.write_text(
        json.dumps({**_packs(lab), **({"preset": setup["user"]} if "user" in setup else {})})
    )
    monkeypatch.setattr("pytacheck.config.user_config_path", lambda: user)
    monkeypatch.delenv("PYTACHECK_CONFIG")
    if "project" in setup:
        (lab.work / "pytacheck.json").write_text(json.dumps({"preset": setup["project"]}))
    if "env" in setup:
        monkeypatch.setenv("PYTACHECK_PRESET", setup["env"])
    with use(setup.get("use")):
        sel = select(preset=setup.get("arg"), use_config=True)
    ref, where = expected
    assert sel.preset == ref
    if where == "user":
        assert sel.source == str(user)
    elif where == "project":
        assert sel.source == str(lab.work / "pytacheck.json")
    else:
        assert sel.source == where


def test_pack_preset_replace_exclude_args(lab) -> None:
    entries = expand("lab")  # "pack" alone means pack::default
    refs = [m for m, _ in entries]
    expected = [m for m in DEFAULT if m != "ref_pubpeer"]
    expected[expected.index("power")] = "lab::power_sesoi"  # swapped in place, pack-local
    assert refs == [*expected, "lab::apa", "lab::netcheck"]
    args = dict(entries)
    assert args["lab::apa"] == {"strict": True}
    assert args["stat_check"] == {"a": {"b": 1}}
    assert args["marginal"] == {}


def test_select_modules_and_args(lab) -> None:
    sel = select(modules=["marginal", "marginal", "lab::apa"])
    assert sel.modules == ["marginal", "marginal", "lab::apa"]  # R order, no dedup
    assert sel.preset is None
    sel = select(modules=["marginal"], preset="lab::minimal", args={"apa": {"x": 2}})
    assert sel == [("lab::apa", {"x": 2}), ("lab::llmcheck", {}), ("marginal", {})]
    # preset args deep-merge with call args; a qualified key beats a bare one
    sel = select(
        preset="lab",
        args={
            "apa": {"strict": False, "y": 1},
            "lab::apa": {"y": 2},
            "stat_check": {"a": {"c": 2}},
        },
    )
    got = dict(sel)
    assert got["lab::apa"] == {"strict": False, "y": 2}
    assert got["stat_check"] == {"a": {"b": 1, "c": 2}}
    assert dict(select(modules=["marginal"], args={"metacheck::marginal": {"z": 1}})) == {
        "marginal": {"z": 1}
    }
    (lab.work / "sub").mkdir()
    (lab.work / "sub" / "mine.py").write_text(mod_src("mine"))
    sel = select(modules=["sub/mine.py"], args={"mine": {"a": 1, "b": 1}, "sub/mine.py": {"b": 2}})
    assert sel == [("sub/mine.py", {"a": 1, "b": 2})]  # R keys args by the module as written


def test_offline_drops_network_and_llm(lab) -> None:
    sel = select(preset="lab::minimal", modules=["netcheck", "marginal"], offline=True)
    assert sel.modules == ["lab::apa", "marginal"]
    assert sel.dropped == ["lab::llmcheck", "netcheck"]
    with use(offline=True):
        assert select(modules=["netcheck"]).dropped == ["netcheck"]
        assert select(modules=["netcheck"], offline=False).modules == ["netcheck"]


def test_selection_runs(lab, paper) -> None:
    out = paper
    for ref, args in select(preset="lab::minimal"):
        out = module_run(out, ref, **args)
    assert list(out.prev_outputs) == ["apa"] and out.module == "llmcheck"


@pytest.mark.parametrize(
    ("presets", "ref", "error"),
    [
        ({"a": {"extends": ["b"]}, "b": {"extends": ["a"]}}, "a", r"Preset cycle: a -> b -> a"),
        ({"a": {"extends": ["a"]}}, "a", r"Preset cycle: a -> a"),
        ({}, "nope", r"There is no preset 'nope'"),
        ({"a": {"extends": ["metacheck::nope"]}}, "a", r"pack 'metacheck' has no preset 'nope'"),
        ({"a": {"modules": ["not_a_module"]}}, "a", r"not_a_module: There were no modules"),
        ({"a": {"modules": ["lab::nope"]}}, "a", r"lab::nope: The pack 'lab' has no module"),
        ({"a": {"module": ["x"]}}, "a", r"unknown keys \['module'\]"),
        ({"a": {"exclude": "x", "args": {"x": 1}}}, "a", r"args must map"),
        ({"a": {"dependencies": ["surely-not-installed-pkg>=1"]}}, "a", r"pip install"),
    ],
)
def test_expansion_errors(lab, presets, ref, error) -> None:
    lab.config({**_packs(lab), "presets": presets})
    with pytest.raises(PresetError, match=error):
        expand(ref)


@pytest.mark.parametrize(
    ("presets", "expected"),
    [
        ({"t": {"modules": ["marginal", "stat_check"]}}, ["marginal", "stat_check"]),
        (  # multiple extends: order kept, first wins, then exclude and append
            {
                "x": {"modules": ["marginal", "stat_check"]},
                "y": {"modules": ["stat_check", "lab::apa"]},
                "t": {"extends": ["x", "y"], "exclude": ["marginal"], "modules": ["marginal"]},
            },
            ["stat_check", "lab::apa", "marginal"],
        ),
        (  # replace keeps the position; a module already present is not appended twice
            {
                "t": {
                    "extends": ["lab::minimal"],
                    "replace": {"apa": "marginal"},
                    "modules": ["marginal"],
                }
            },
            ["marginal", "lab::llmcheck"],
        ),
        (  # config presets use normal resolution for bare names
            {"t": {"modules": ["apa"]}},
            ["apa"],
        ),
        (  # a pack preset extending a sibling preset by bare name
            {"t": {"extends": ["lab::ext"]}},
            ["lab::apa", "lab::llmcheck", "marginal"],
        ),
        (  # unported metacheck modules are accepted (they fail when run)
            {"t": {"extends": ["metacheck::repository"], "exclude": ["code_check"]}},
            ["repo_check", "data_check", "codebook_check"],
        ),
    ],
)
def test_expansion_table(lab, presets, expected) -> None:
    lab.config({**_packs(lab), "presets": presets})
    assert [m for m, _ in expand("t")] == expected


def test_args_deep_merge_along_extends(lab) -> None:
    lab.config(
        {
            **_packs(lab),
            "presets": {
                "base": {"modules": ["marginal"], "args": {"marginal": {"a": 1, "n": {"x": 1}}}},
                "t": {"extends": ["base"], "args": {"marginal": {"b": 2, "n": {"y": 2}}}},
            },
        }
    )
    assert expand("t") == [("marginal", {"a": 1, "b": 2, "n": {"x": 1, "y": 2}})]
    assert deep_merge({"a": {"b": 1}}, {"a": 2}, None, {"c": {"d": 1}}) == {"a": 2, "c": {"d": 1}}


def test_replace_of_absent_module_warns(lab) -> None:
    lab.config(
        {**_packs(lab), "presets": {"t": {"modules": ["marginal"], "replace": {"zz": "marginal"}}}}
    )
    with pytest.warns(UserWarning, match="replaces zz"):
        expand("t")


def test_validate_imports_everything(lab) -> None:
    assert [m for m, _ in select(preset="metacheck::repository")]  # tolerated by default
    with pytest.raises(PresetError, match="repo_check: metacheck's 'repo_check' is not ported yet"):
        select(preset="metacheck::repository", validate=True)
    lab.config({**_packs(lab), "presets": {"t": {"modules": ["lab::apa", "marginal"]}}})
    assert select(preset="t", validate=True).modules == ["lab::apa", "marginal"]


def test_lookup_and_preset_list(lab) -> None:
    lab.config({**_packs(lab), "presets": {"thesis": {"description": "mine", "extends": ["lab"]}}})
    p = lookup("thesis")
    assert (p.ref, p.pack, p.description) == ("thesis", None, "mine")
    assert p.defined_in == str(lab.config_file)
    assert lookup("lab").ref == "lab::default"
    assert lookup("validated").ref == "metacheck::validated"
    df = preset_list()
    assert list(df.columns) == ["ref", "description", "n_modules", "defined_in"]
    rows = {r.ref: r for r in df.itertuples()}
    assert rows["metacheck::default"].n_modules == 16
    assert rows["metacheck::default"].defined_in == "builtin"
    assert rows["lab::minimal"].n_modules == 2
    assert rows["thesis"].n_modules == 17 and rows["thesis"].description == "mine"


def test_preset_as_r(lab) -> None:
    assert preset_as_r("metacheck::repository") == (
        'report(paper,\n       modules = c("repo_check", "code_check", "data_check", "codebook_check"))'
    )
    (lab.root / "lab" / "apa.R").write_text("# R version\n")
    lab.config(
        {
            **_packs(lab),
            "presets": {
                "t": {
                    "modules": ["power", "lab::apa", "lab::llmcheck"],
                    "args": {
                        "power": {
                            "seed": 8675309,
                            "alpha": 0.05,
                            "tails": ["a", "b"],
                            "go": True,
                            "x": None,
                        },
                        "lab::apa": {"odd name": [1, "b"]},
                    },
                }
            },
        }
    )
    apa_r = str(lab.root / "lab" / "apa.R")
    assert preset_as_r("t") == (
        "# left out (no metacheck .R version): lab::llmcheck\n"
        "report(paper,\n"
        f'       modules = c("power", "{apa_r}"),\n'
        '       args = list(power = list(seed = 8675309, alpha = 0.05, tails = c("a", "b"), '
        f'go = TRUE, x = NULL), `{apa_r}` = list(`odd name` = list(1, "b"))))'
    )


def test_label() -> None:
    assert label("psych::apa") == "apa"
    assert label("marginal") == "marginal"
    assert label("dir/my_mod.py") == "my_mod"
