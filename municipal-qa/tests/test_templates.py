import openpyxl
import pytest

import municipal_qa.readers  # noqa: F401
from municipal_qa.config import load_config
from municipal_qa.pipeline import templates
from municipal_qa.pipeline.executor import execute
from municipal_qa.profiling.profile import ProfileStore
from municipal_qa.schema import Conventions, SourceInfo
from municipal_qa.tools.registry import ExecContext

BOOK, ORDERS = "SRC_aaaaaaaaaaaa", "SRC_bbbbbbbbbbbb"


def _info(sid, path):
    return SourceInfo(source_id=sid, path=str(path), original_file=path.name, source_set="Train",
                      publisher="", licence="", bytes=1, sha256=path.name,
                      suffix=path.suffix)  # fmt: skip


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("MQA_DATA_DIR", str(tmp_path))
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Energy"
    for r in [["Energy use by city (GWh)"], ["Region", 2023, 2024], ["Helsinki", 2023, 2024],
              ["Heating", 10, 12], ["Transport", 30, 33], ["Total", 40, 45], ["Espoo", 2023, 2024],
              ["Heating", 5, 6], ["Transport", 15, 14], ["Total", 20, 20]]:  # fmt: skip
        ws.append(r)
    wb.save(tmp_path / "energy.xlsx")
    (tmp_path / "orders.csv").write_text(
        "reference,name,made_date,confirmed_date\nTPO1,Park,2018-04-20,2018-05-25\n"
        "TPO2,Road,2019-01-01,\n"
    )
    manifest = {BOOK: _info(BOOK, tmp_path / "energy.xlsx"),
                ORDERS: _info(ORDERS, tmp_path / "orders.csv")}  # fmt: skip
    cfg = load_config(env_file=tmp_path / "none.env").model_copy(
        update={"artifacts_dir": tmp_path / "artifacts"}
    )
    store = ProfileStore(cfg, manifest)
    c = ExecContext(store, Conventions(raw_context="", value_mode="stored"), list(manifest))
    return c, store.get_many(list(manifest))


def run(ctx, question, qtype="compare"):
    c, profiles = ctx
    plan = templates.template_plan(question, profiles, c, qtype)
    assert plan is not None
    ex = execute(plan, c)
    assert ex.ok, [r.error for r in ex.results]
    return plan, {f"{s.save_as}.{f.name}": f for s, r in zip(ex.steps, ex.results, strict=True)
                  for f in r.facts}  # fmt: skip


def test_change_between_years_in_a_block(ctx):
    plan, f = run(ctx, "By what percentage did Espoo's heating change from 2023 to 2024?")
    assert [s.op for s in plan.steps] == ["cell_lookup", "cell_lookup", "percent_change"]
    assert plan.steps[0].args == {"source_id": BOOK, "sheet": "Energy", "row": "Heating",
                                  "column": "2023", "block": "Espoo"}  # fmt: skip
    assert f["r1.pct"].display == "20.00%"


def test_share_of_the_block_total(ctx):
    plan, f = run(ctx, "What share of Helsinki's 2024 energy use was transport?", "aggregate")
    assert f["share.pct"].display == "73.33%"  # 33 / 45


def test_two_blocks_compared_and_totals(ctx):
    plan, f = run(ctx, "How much more total energy did Helsinki use than Espoo in 2024?")
    assert f["cmp.difference"].value == 25 and f["cmp.larger"].value == "Helsinki / Total"


def test_record_lookup_with_days(ctx):
    plan, f = run(ctx, "When was TPO1 made and confirmed, and how many days passed?", "lookup")
    assert [s.op for s in plan.steps] == ["lookup_row", "date_diff"]
    assert plan.steps[0].args["key_column"] == "reference" and f["days.days"].value == 35


def test_nulls_and_share(ctx):
    plan, f = run(ctx, "How many orders in orders.csv have no confirmed_date, and what share?",
                  "data_quality")  # fmt: skip
    assert f["share.pct"].display == "50.00%"


def test_no_template_for_semantics_or_ranking(ctx):
    c, profiles = ctx
    assert (
        templates.template_plan("What does Total mean in 2024?", profiles, c, "semantics") is None
    )
    q = "Which city had the highest heating use in 2024?"
    assert templates.template_plan(q, profiles, c, "compare") is None
