import json
import sqlite3

import openpyxl
import pytest

import municipal_qa.readers  # noqa: F401
from municipal_qa.config import load_config
from municipal_qa.profiling.profile import ProfileStore
from municipal_qa.schema import Conventions, SourceInfo, Step
from municipal_qa.tools import registry
from municipal_qa.tools.registry import ExecContext, run_step
from municipal_qa.tools.table_ops import check_sql

TABLE_OPS = [
    "table_info", "get_cell", "get_range", "find_cells", "filter_rows", "count_rows",
    "count_distinct", "count_nulls", "aggregate", "group_aggregate", "top_n", "lookup_row",
    "value_counts", "duplicates", "set_compare", "join_check", "row_wise_max", "sql",
]  # fmt: skip


def _info(sid, path):
    return SourceInfo(
        source_id=sid,
        path=str(path),
        original_file=path.name,
        source_set="Train",
        publisher="Pub",
        licence="",
        bytes=path.stat().st_size,
        sha256=path.name,
        suffix=path.suffix.lower(),
    )


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("MQA_DATA_DIR", str(tmp_path))
    (tmp_path / "trees.csv").write_text(
        "reference,species,height,made_date\n"
        "T1,Oak,12.5,2018-04-20\nT2,Ash,[z],2019-01-01\nT3,Oak,7,\nT3,Elm,,2020-05-05\n"
        "007,Oak,1.25,2021-02-02\n"
    )
    (tmp_path / "other.csv").write_text("ref,x\nT1,1\nT2,2\nT9,3\n007,4\n")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Data"
    ws["A1"] = "Energy use (GWh)"
    for c, name in enumerate(["City", "Total", "Homes"], 1):
        ws.cell(row=3, column=c, value=name)
    for r, row in enumerate([("Helsinki", 1234.5678, 500.25), ("Espoo", 2000.4, 600)], 4):
        for c, v in enumerate(row, 1):
            ws.cell(row=r, column=c, value=v).number_format = "#,##0" if c == 2 else "General"
    wb.save(tmp_path / "energy.xlsx")
    (tmp_path / "loads.json").write_text(
        json.dumps(
            [
                {"t": "00:00", "a": 1, "b": 5, "c": 1},
                {"t": "00:15", "a": 4, "b": 4, "c": 4},
                {"t": "00:30", "a": 9, "b": 1, "c": 1},
            ]
        )
    )
    con = sqlite3.connect(tmp_path / "graph.sqlite")
    con.execute("CREATE TABLE links (id TEXT, length REAL)")
    con.executemany("INSERT INTO links VALUES (?, ?)", [("a", 10.5), ("b", 20.0)])
    con.commit()
    con.close()
    names = ["trees.csv", "other.csv", "energy.xlsx", "loads.json", "graph.sqlite"]
    manifest = {
        f"SRC_{c * 12}": _info(f"SRC_{c * 12}", tmp_path / n)
        for c, n in zip("abcde", names, strict=True)
    }
    cfg = load_config(env_file=tmp_path / "none.env").model_copy(
        update={"artifacts_dir": tmp_path / "artifacts"}
    )
    store = ProfileStore(cfg, manifest)
    conv = Conventions(raw_context="", value_mode="displayed", counts_mean="records")
    return ExecContext(store, conv, list(manifest))


A, B, X, J, Q = (f"SRC_{c * 12}" for c in "abcde")


def run(ctx, op, env=None, **args):
    return run_step(Step(op=op, args=args, save_as="s"), env or {}, ctx)


def facts(res):
    assert res.ok, res.error
    return {f.name: f for f in res.facts}


def test_every_table_op_is_registered_with_a_json_schema():
    ops = registry.load_all()
    for name in TABLE_OPS:
        spec = ops[name]
        s = spec.args_schema
        assert s["type"] == "object" and set(s["required"]) <= set(s["properties"]), name
        assert spec.description and spec.group == "table"
    menu = registry.ops_menu(["lookup_row"])
    assert menu.startswith("- lookup_row(source_id: string, table?: string, key_column: string")


def test_validation_lists_valid_options(ctx):
    r = run(ctx, "no_such_op")
    assert not r.ok and "unknown op" in r.error and "lookup_row" in r.error
    r = run(ctx, "count_rows", source_id="SRC_ffffffffffff")
    assert not r.ok and "not listed for this question" in r.error
    r = run(ctx, "lookup_row", source_id=A, key_column="refrence", value="T1")
    assert not r.ok and "no column 'refrence'" in r.error and "reference, species" in r.error
    r = run(ctx, "lookup_row", source_id=A, value="T1")
    assert not r.ok and "missing argument(s) ['key_column']" in r.error
    r = run(ctx, "lookup_row", source_id=A, key_column="REFERENCE", value="T1", columns=["Species"])
    assert facts(r)["species"].display == "Oak"


def test_lookup_keeps_identifiers_and_references_previous_steps(ctx):
    env = {
        "k": run(
            ctx, "lookup_row", source_id=A, key_column="reference", value="007", columns=["species"]
        )
    }
    f = facts(env["k"])["species"]
    assert f.value == "Oak" and f.locators[0].text == "reference=007, column species"
    env["r"] = run(ctx, "lookup_row", source_id=B, key_column="ref", value="T1", columns=["ref"])
    r = run(
        ctx,
        "lookup_row",
        env,
        source_id=A,
        key_column="reference",
        value="$r.ref",
        columns=["made_date"],
    )
    assert facts(r)["made_date"].display == "2018-04-20"
    r = run(ctx, "lookup_row", source_id=A, key_column="reference", value="7")
    assert facts(r)["species"].display == "Oak"  # numeric match on '007' vs '7'
    r = run(ctx, "lookup_row", env, source_id=A, key_column="reference", value="$nope")
    assert not r.ok and "unknown reference $nope" in r.error


def test_counts_nulls_markers_distinct_and_duplicates(ctx):
    assert facts(run(ctx, "count_rows", source_id=A))["count"].display == "5 records"
    r = facts(
        run(
            ctx, "count_rows", source_id=A, where=[{"column": "species", "op": "=", "value": "Oak"}]
        )
    )
    assert r["count"].value == 3 and "where species = Oak" in r["count"].claim
    n = facts(run(ctx, "count_nulls", source_id=A, columns=["height"]))
    assert n["height.blank"].value == 1 and n["height.markers"].value == 1
    assert "[z] x1" in n["height.markers"].note and n["total"].value == 5
    assert facts(run(ctx, "count_distinct", source_id=A, columns=["species"]))["count"].value == 3
    d = facts(run(ctx, "duplicates", source_id=A, columns=["reference"]))
    assert (d["duplicate_keys"].value, d["rows_in_duplicates"].value, d["examples"].value) == (
        1,
        2,
        "T3",
    )
    ctx.conventions.counts_mean = "unique_keys"
    u = facts(run(ctx, "count_rows", source_id=A, key_column="reference"))
    assert u["count"].value == 4 and u["count"].unit == "unique reference values"


def test_aggregates_skip_markers_and_round_once(ctx):
    s = facts(run(ctx, "aggregate", source_id=A, column="height", func="sum", unit="m"))
    assert s["sum"].value == 20.75 and s["sum"].display == "20.75 m" and s["n"].value == 3
    assert "1 blank" in s["sum"].note and "'[z]' x1" in s["sum"].note
    m = facts(
        run(ctx, "aggregate", source_id=A, column="height", func="max", key_column="reference")
    )
    assert m["max"].display == "12.50" and m["at"].value == "T1"
    assert m["max"].locators[0].text == "reference=T1, column height"
    mean = facts(run(ctx, "aggregate", source_id=A, column="height", func="mean"))
    assert mean["mean"].display == "6.92"  # 20.75 / 3 = 6.91666...
    g = facts(run(ctx, "group_aggregate", source_id=A, group_by=["species"], func="count"))
    assert g["Oak"].value == 3 and g["groups"].value == 3
    v = facts(run(ctx, "value_counts", source_id=A, column="species"))
    assert v["Oak"].value == 3 and v["Oak"].note == "share 60.00%" and v["total"].value == 5
    t = facts(run(ctx, "top_n", source_id=A, column="height", n=2, key_column="reference"))
    assert list(t) == ["T1", "T3"]


def test_set_compare_and_join_check(ctx):
    r = facts(run(ctx, "set_compare", source_a=A, column_a="reference", source_b=B, column_b="ref"))
    assert (r["only_in_a"].value, r["only_in_b"].value) == (1, 1)
    assert r["only_in_a"].note == "e.g. T3" and r["only_in_b"].note == "e.g. T9"
    assert (r["duplicates_in_a"].value, r["duplicates_in_b"].value, r["common"].value) == (1, 0, 3)
    assert r["only_in_a"].locators[0].text == "column reference"
    j = facts(run(ctx, "join_check", source_a=A, column_a="reference", source_b=B, column_b="ref"))
    assert (j["matched_a"].value, j["unmatched_a"].value, j["unmatched_b"].value) == (3, 2, 1)
    assert j["cardinality"].value == "many-to-1"


def test_workbook_cells_ranges_and_column_letters(ctx):
    c = facts(run(ctx, "get_cell", source_id=X, sheet="Data", ref="B4"))
    assert c["value"].display == "1,235" and c["value"].locators[0].text == "sheet Data, cell B4"
    assert "stored 1234.5678" in c["value"].note
    c = facts(run(ctx, "get_cell", source_id=X, sheet="data", ref="B4", value_mode="stored"))
    assert c["value"].value == 1234.5678
    assert not run(ctx, "get_cell", source_id=X, sheet="Data", ref="4B").ok
    rng = facts(run(ctx, "get_range", source_id=X, sheet="Data", range="A4:B5"))
    assert list(rng) == ["A4", "B4", "A5", "B5"] and rng["B5"].display == "2,000"
    f = facts(run(ctx, "find_cells", source_id=X, text="espoo"))
    assert list(f) == ["Data!A5"]
    m = facts(
        run(
            ctx,
            "aggregate",
            source_id=X,
            table="Data",
            column="Homes",
            func="max",
            key_column="City",
        )
    )
    assert m["max"].locators[0].text == "sheet Data, cell C5" and m["at"].value == "Espoo"


def test_workbook_ops_refuse_other_sources(ctx):
    r = run(ctx, "find_cells", source_id=A, text="Oak")
    assert not r.ok and "is not a workbook" in r.error


def test_row_wise_max_json_and_sql(ctx):
    r = facts(run(ctx, "row_wise_max", source_id=J, columns=["a", "b", "c"], label_column="t"))
    assert r["max_row_total"].value == 12 and r["at"].value == "00:15"
    assert r["sum_of_maxima"].value == 18  # 9 + 5 + 4
    s = facts(run(ctx, "sql", query=f"SELECT sum(length) AS total FROM {Q.lower()}"))
    assert s["total"].value == 30.5 and s["total"].locators[0].kind == "sql"
    info = facts(run(ctx, "table_info", source_id=Q, table="links"))
    assert info["rows"].value == 2 and info["columns"].value == "id, length"


@pytest.mark.parametrize(
    "q",
    [
        "SELECT 1; DROP TABLE x",
        "SELECT 1; SELECT 2",
        "ATTACH 'x.db' AS y",
        "COPY t TO 'out.csv'",
        "INSTALL httpfs",
        "PRAGMA table_info(t)",
        "SELECT * FROM read_csv('/etc/passwd')",
        "DELETE FROM t",
        "WITH a AS (SELECT 1) SELECT * FROM glob('*')",
    ],
)
def test_sql_refuses_anything_but_one_select(q):
    with pytest.raises(registry.OpError):
        check_sql(q)


def test_sql_accepts_select_and_with():
    assert (
        check_sql("SELECT count(*) FROM src_aaaaaaaaaaaa;")
        == "SELECT count(*) FROM src_aaaaaaaaaaaa"
    )
    assert check_sql("with t as (select 1 as last_update) select * from t")


def test_train_1bbb3964_set_compare_and_1d78cef6_lookup(data_cfg):
    from municipal_qa.data.manifest import load_manifest
    from municipal_qa.data.questions import load_questions
    from municipal_qa.pipeline.context import parse_conventions

    m = load_manifest(data_cfg)
    store = ProfileStore(data_cfg, m)
    q = next(q for q in load_questions(data_cfg, "train") if q.id.startswith("1bbb3964"))
    sid = {m[s].original_file: s for s in q.source_ids}
    ctx = ExecContext(store, parse_conventions(q.context, q.question), q.source_ids)
    args = {"source_a": sid["tpo-trees.csv"], "column_a": "reference"}
    r = facts(run(ctx, "set_compare", **args, source_b=sid["trees.gpkg"], column_b="reference"))
    keys = ("only_in_a", "only_in_b", "duplicates_in_a", "duplicates_in_b")
    assert [r[k].value for k in keys] == [0, 0, 0, 0]
    assert r["common"].value == 1571
    assert r["only_in_b"].locators[0].text == "layer ezytreevcurrenttpotrees, field reference"
    reg = sid["trees-dataset-2026-06-23.csv"]
    cols = ["made_date", "confirmed_date"]
    d = facts(
        run(ctx, "lookup_row", source_id=reg, key_column="reference", value="TPO453", columns=cols)
    )
    assert (d["made_date"].value, d["confirmed_date"].value) == ("2018-04-20", "2018-05-25")
    assert d["made_date"].locators[0].text == "reference=TPO453, column made_date"


def test_cell_lookup_by_labels_and_block(tmp_path, monkeypatch):
    monkeypatch.setenv("MQA_DATA_DIR", str(tmp_path))
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Results"
    rows = [["Energy (GWh)"], ["Area", 2023, 2024], ["Helsinki", 2023, 2024], ["Heat", 10.25, 11.5],
            ["Total", 20, 21], ["Espoo"], ["Heat", 30, 31], ["Total", 40, 41.256],
            ["Grand total", 60, 62]]  # fmt: skip
    for r in rows:
        ws.append(r)
    wb.save(tmp_path / "blocks.xlsx")
    sid = "SRC_eeeeeeeeeeee"
    manifest = {sid: _info(sid, tmp_path / "blocks.xlsx")}
    cfg = load_config(env_file=tmp_path / "none.env").model_copy(
        update={"artifacts_dir": tmp_path / "artifacts"}
    )
    ctx = ExecContext(
        ProfileStore(cfg, manifest), Conventions(raw_context="", value_mode="stored"), [sid]
    )
    f = facts(run(ctx, "cell_lookup", source_id=sid, sheet="Results", row="total", column="2024",
                  block="Espoo"))["value"]  # fmt: skip
    assert (
        f.value == 41.256
        and f.display == "41.26"
        and f.locators[0].text == "sheet Results, cell C8"
    )
    h = facts(run(ctx, "cell_lookup", source_id=sid, sheet="results", row="Heat", column="2023",
                  block="Helsinki"))["value"]  # fmt: skip
    assert h.locators[0].text == "sheet Results, cell B4" and "(Heat / 2023)" in h.claim
    amb = run(ctx, "cell_lookup", source_id=sid, sheet="Results", row="Total", column="2024")
    assert amb.ok and amb.facts[0].locators[0].text == "sheet Results, cell C5"
    assert "2 rows match 'Total' (rows [5, 8]); the first is used" in amb.facts[0].note
    sw = facts(run(ctx, "cell_lookup", source_id=sid, sheet="Results", row="2024", column="Total",
                   block="Espoo"))["value"]  # fmt: skip
    assert sw.locators[0].text == "sheet Results, cell C8" and "swapped" in sw.note
    nb = facts(run(ctx, "cell_lookup", source_id=sid, sheet="Results", row="Grand total",
                   column="2024", block="Vantaa"))["value"]  # fmt: skip
    assert "block 'Vantaa' not found" in nb.note and nb.locators[0].text == "sheet Results, cell C9"
    head = run(ctx, "cell_lookup", source_id=sid, sheet="Results", row="Helsinki", column="2024")
    assert not head.ok and "is a heading" in head.error
    miss = run(ctx, "cell_lookup", source_id=sid, sheet="Results", row="Heat", column="2030",
               block="Espoo")  # fmt: skip
    assert not miss.ok and "no column header '2030'" in miss.error


def test_cell_lookup_on_a_table_uses_labels(ctx):
    f = facts(run(ctx, "cell_lookup", source_id=A, sheet="x", row="T1", column="made_date"))
    assert f["value"].value == "2018-04-20"
    assert f["value"].locators[0].text == "reference=T1, column made_date"
