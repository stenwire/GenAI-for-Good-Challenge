import pytest

from municipal_qa.schema import Conventions, Fact, Locator, Step, ToolResult
from municipal_qa.tools import registry
from municipal_qa.tools.calc import convert, parse_date
from municipal_qa.tools.registry import ExecContext, run_step

CALC_OPS = [
    "calc", "percent_change", "share", "pp_difference", "ratio", "convert_unit", "round_value",
    "date_diff", "compare_values", "check_criteria",
]  # fmt: skip


class _Store:
    manifest: dict = {}


def _ctx(**conv):
    return ExecContext(_Store(), Conventions(raw_context="", **conv), [])


def _saved(name, value, display, loc="sheet T, cell A1"):
    f = Fact(name="value", value=value, display=display, claim="c",
             locators=[Locator(source_id="SRC_aaaaaaaaaaaa", kind="cell", text=loc)])  # fmt: skip
    return {name: ToolResult(ok=True, op="get_cell", args={}, facts=[f])}


def run(ctx, op, env=None, **args):
    r = run_step(Step(op=op, args=args, save_as="x"), env or {}, ctx)
    assert r.ok, r.error
    return {f.name: f for f in r.facts}


def test_every_calc_op_is_registered():
    ops = registry.load_all()
    assert all(ops[n].group == "calc" for n in CALC_OPS)


@pytest.mark.parametrize(("mode", "shown"), [("half_up", "2.35"), ("half_even", "2.34")])
def test_half_up_vs_half_even_on_2_345(mode, shown):
    assert run(_ctx(), "round_value", value=2.345, decimals=2, mode=mode)["value"].display == shown
    assert (
        run(_ctx(rounding=mode), "round_value", value="2.345", decimals=2)["value"].display == shown
    )
    assert run(_ctx(rounding=mode), "ratio", a="4.69", b=2)["ratio"].display == shown


def test_calc_keeps_full_precision_and_rounds_once():
    env = {**_saved("a", 1, "1"), **_saved("b", 3, "3", "sheet T, cell B1")}
    f = run(_ctx(), "calc", env, expression="$a / $b * 3", unit="GWh")["value"]
    assert f.display == "1.00 GWh" and f.claim == "1 / 3 * 3 = 1.00 GWh"
    assert [x.text for x in f.locators] == ["sheet T, cell A1", "sheet T, cell B1"]
    env = {**_saved("p", 0.125, "0.125"), **_saved("q", 0.125, "0.125")}
    assert run(_ctx(), "calc", env, expression="$p + $q")["value"].display == "0.25"
    g = run(_ctx(), "calc", expression="(0.125 + 0.125) * -(2)")["value"]
    assert g.value == -0.5
    for bad, msg in [
        ("1 / 0", "division by zero"),
        ("2 ^ 3", "cannot read"),
        ("(1 + 2", "missing"),
    ]:
        r = run_step(Step(op="calc", args={"expression": bad}, save_as="x"), {}, _ctx())
        assert not r.ok and msg in r.error


def test_percent_share_pp_and_compare():
    env = {
        **_saved("y23", 24343.613981368828, "24,344"),
        **_saved("y24", 26307.67411959209, "26,308"),
    }
    pc = run(_ctx(), "percent_change", env, old="$y23", new="$y24")
    assert pc["pct"].display == "8.07%" and pc["diff"].display == "1,964.06"
    assert run(_ctx(), "share", part=1, total=8)["pct"].display == "12.50%"
    assert (
        run(_ctx(), "pp_difference", a="51.09", b="45.45")["pp"].display == "5.64 percentage points"
    )
    c = run(_ctx(), "compare_values", a=10, b=8, label_a="Helsinki", label_b="Espoo")
    assert c["larger"].value == "Helsinki" and c["difference"].value == 2
    assert c["pct_larger"].display == "25.00%"


def test_convert_units_and_hectare_override():
    ctx = _ctx(decimals_overrides={"ha": 5})
    f = run(ctx, "convert_unit", value=100 * 100, from_unit="m²", to_unit="ha")["value"]
    assert f.display == "1.00000 ha"
    assert convert(convert(1, "ftUS", "m"), "m", "ftUS") == 1
    assert str(convert(1, "ftUS", "m"))[:12] == "0.3048006096"
    assert convert(1, "GWh", "MWh") == 1000
    r = run_step(Step(op="convert_unit", args={"value": 1, "from_unit": "m", "to_unit": "kWh"},
                      save_as="x"), {}, ctx)  # fmt: skip
    assert not r.ok and "cannot convert" in r.error


def test_date_diff_inclusive_flags_and_formats():
    args = {"start": "2018-04-20", "end": "2018-05-25"}
    assert run(_ctx(), "date_diff", **args)["days"].value == 35
    assert run(_ctx(), "date_diff", **args, inclusive_start=True)["days"].value == 36
    assert run(_ctx(), "date_diff", **args, inclusive_end=False)["days"].value == 34
    d = run(_ctx(), "date_diff", start="20/04/2018", end="25 May 2018")
    assert d["days"].display == "35 days" and d["start"].display == "20 April 2018"
    assert parse_date("2018-05-25T10:00:00").isoformat() == "2018-05-25"


def test_check_criteria_values():
    r = run(
        _ctx(), "check_criteria", values=[1.2, 1.5, 2], operator=">=", threshold=1.5, label="width"
    )
    assert [r[k].display for k in ("width#1", "width#2", "width#3")] == ["fail", "pass", "pass"]


def test_train_0b7af6de_and_1d78cef6(data_cfg):
    from municipal_qa.data.manifest import load_manifest
    from municipal_qa.data.questions import load_questions
    from municipal_qa.pipeline.context import parse_conventions
    from municipal_qa.profiling.profile import ProfileStore

    m = load_manifest(data_cfg)
    store = ProfileStore(data_cfg, m)
    qs = {q.id[:8]: q for q in load_questions(data_cfg, "train")}
    q = qs["0b7af6de"]
    ctx = ExecContext(store, parse_conventions(q.context, q.question), q.source_ids)
    wb = next(s for s in q.source_ids if m[s].suffix == ".xlsx")
    env = {}
    for name, ref in [("y23", "Z9"), ("y24", "AA9")]:
        step = Step(
            op="get_cell", args={"source_id": wb, "sheet": "Tulokset", "ref": ref}, save_as=name
        )
        env[name] = run_step(step, env, ctx)
    pc = run(ctx, "percent_change", env, old="$y23", new="$y24")
    assert (pc["pct"].display, pc["diff"].display) == ("8.07%", "1,964.06")
    assert [x.text for x in pc["pct"].locators] == [
        "sheet Tulokset, cell Z9",
        "sheet Tulokset, cell AA9",
    ]
    q = qs["1d78cef6"]
    ctx = ExecContext(store, parse_conventions(q.context, q.question), q.source_ids)
    reg = next(s for s in q.source_ids if m[s].original_file == "trees-dataset-2026-06-23.csv")
    step = Step(op="lookup_row", save_as="r", args={"source_id": reg, "key_column": "reference",
                "value": "TPO453", "columns": ["made_date", "confirmed_date"]})  # fmt: skip
    env = {"r": run_step(step, {}, ctx)}
    d = run(ctx, "date_diff", env, start="$r.made_date", end="$r.confirmed_date")
    assert (
        d["days"].value == 35 and d["days"].locators[0].text == "reference=TPO453, column made_date"
    )
