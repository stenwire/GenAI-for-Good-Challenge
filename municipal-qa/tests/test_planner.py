import json
import re

import httpx
import pytest

import municipal_qa.readers  # noqa: F401
from municipal_qa.config import load_config
from municipal_qa.llm.client import LLMClient
from municipal_qa.pipeline import planner, router
from municipal_qa.pipeline.answer import Deps, answer_question
from municipal_qa.pipeline.executor import execute, normalise
from municipal_qa.schema import Conventions, Plan, Question, QuestionType, SourceInfo, Step
from municipal_qa.tools.registry import ExecContext

LINE = re.compile(r"^SRC_[0-9a-f]{12} \| .+ \| .+$")
GUIDE, TABLE = "SRC_aaaaaaaaaaaa", "SRC_bbbbbbbbbbbb"


def _info(sid, path):
    return SourceInfo(source_id=sid, path=str(path), original_file=path.name, source_set="Train",
                      publisher="Pub", licence="", bytes=path.stat().st_size, sha256=path.name,
                      suffix=path.suffix.lower())  # fmt: skip


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MQA_DATA_DIR", str(tmp_path))
    (tmp_path / "SourceGuide.md").write_text("# Shorthand\n\n[z] means not applicable, not zero.\n")
    (tmp_path / "revenue.csv").write_text(
        "year,london,metro\n2023,2300,[z]\n2024,2390,[z]\n2025,2478,[z]\n"
    )
    manifest = {GUIDE: _info(GUIDE, tmp_path / "SourceGuide.md"),
                TABLE: _info(TABLE, tmp_path / "revenue.csv")}  # fmt: skip
    cfg = load_config(env_file=tmp_path / "none.env").model_copy(
        update={"artifacts_dir": tmp_path / "artifacts"}
    )
    cfg = cfg.model_copy(
        update={"retrieval": cfg.retrieval.model_copy(update={"embedding_model": None})}
    )
    return cfg, manifest


def _q(text, qid="q1"):
    return Question(id=qid, case_id="c", domain="transport", context="Counts mean records.",
                    question=text, source_ids=[GUIDE, TABLE], split="dev")  # fmt: skip


class Script:
    """Fake Ollama: replies by prompt kind; records every /api/chat purpose."""

    def __init__(self, route=None, plan=None, repair=None, compose="2478 is the 2025 figure."):
        self.replies = {"route": route, "plan": plan, "repair": repair, "compose": compose}
        self.seen = []

    def kind(self, prompt):
        heads = (("route", "You classify"), ("plan", "You plan tool calls"),
                 ("repair", "A tool call failed"), ("compose", "You write the final"))  # fmt: skip
        for k, head in heads:
            if prompt.startswith(head):
                return k
        return "other"

    def __call__(self, request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"model_info": {"general.parameter_count": 1}})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        body = json.loads(request.content)
        k = self.kind(body["messages"][0]["content"])
        self.seen.append((k, "format" in body))
        reply = self.replies.get(k)
        if reply is None:
            return httpx.Response(500, text="no script")
        content = reply if isinstance(reply, str) else json.dumps(reply)
        return httpx.Response(200, json={"message": {"content": content}, "eval_count": 50,
                                         "prompt_eval_count": 400})  # fmt: skip


def _deps(cfg, manifest, script):
    return Deps(cfg, manifest, LLMClient(cfg, transport=httpx.MockTransport(script)))


SUB = [{"text": "2025 London value", "expects": "number"}]
ROUTE_LOOKUP = {"qtype": "lookup", "sub_questions": SUB, "primary_source_ids": [TABLE],
                "premise_to_check": None}  # fmt: skip


def test_route_plan_execute_compose_with_fact_evidence(env):
    cfg, manifest = env
    plan = {"steps": [{"op": "lookup_row", "save_as": "row",
                       "args": {"source_id": "revenue.csv", "key_column": "Year", "value": "2025",
                                "columns": ["london"]}}]}  # fmt: skip
    s = Script(route=ROUTE_LOOKUP, plan=plan)
    rec = answer_question(_q("What was London's revenue in 2025?"), _deps(cfg, manifest, s))
    assert [k for k, _ in s.seen] == ["route", "plan", "compose"]
    assert [fmt for k, fmt in s.seen if k in ("route", "plan")] == [True, True]  # JSON schema sent
    assert rec.route.qtype == QuestionType.LOOKUP and rec.results[0].ok
    assert rec.plan.steps[0].args["source_id"] == TABLE  # filename mapped to its source ID
    assert rec.answer == "2478 is the 2025 figure." and not rec.fallback_used
    assert rec.evidence[0] == f"{TABLE} | year=2025, column london | year=2025 has london 2478"
    assert all(LINE.match(x) for x in rec.evidence) and rec.total_calls == 3


def test_failed_step_is_repaired_within_the_limit(env):
    cfg, manifest = env
    args = {"source_id": TABLE, "column": "londn", "func": "sum"}
    plan = {"steps": [{"op": "aggregate", "save_as": "tot", "args": args}]}
    fixed = {"op": "aggregate", "save_as": "tot", "args": {"source_id": TABLE, "column": "london",
                                                          "func": "sum"}}  # fmt: skip
    s = Script(route=ROUTE_LOOKUP, plan=plan, repair=fixed, compose="Total 7,168.")
    rec = answer_question(_q("What is the total London revenue?"), _deps(cfg, manifest, s))
    assert [k for k, _ in s.seen] == ["route", "plan", "repair", "compose"]
    assert rec.results[0].ok and rec.results[0].facts[0].value == 7168 and rec.error is None


def test_repairs_stop_at_max_and_answer_is_never_blank(env):
    cfg, manifest = env
    bad = {
        "op": "aggregate",
        "save_as": "x",
        "args": {"source_id": TABLE, "column": "nope", "func": "sum"},
    }
    s = Script(route=ROUTE_LOOKUP, plan={"steps": [bad]}, repair=bad, compose="Not established.")
    rec = answer_question(_q("What is the total?"), _deps(cfg, manifest, s))
    assert [k for k, _ in s.seen].count("repair") == cfg.pipeline.max_repairs
    assert not rec.results[0].ok and "no column 'nope'" in rec.error
    assert rec.answer and rec.total_calls <= 15 and rec.total_generated_tokens <= 12000


def test_semantics_with_docs_skips_planning(env):
    cfg, manifest = env
    route = {"qtype": "semantics", "sub_questions": [{"text": "meaning of [z]", "expects": "text"}],
             "primary_source_ids": [GUIDE], "premise_to_check": "[z] is zero"}  # fmt: skip
    s = Script(route=route, compose="No. [z] means not applicable, not zero.")
    rec = answer_question(_q("What does [z] mean? Is it zero?"), _deps(cfg, manifest, s))
    assert [k for k, _ in s.seen] == ["route", "compose"] and rec.plan is None
    assert rec.evidence[0].startswith(f"{GUIDE} | ")


def test_router_failure_falls_back_to_rules_and_bad_plan_to_passages(env):
    cfg, manifest = env
    s = Script(route=None, plan="not json", compose="The sources show 2478 for 2025.")
    rec = answer_question(_q("How many records are in revenue.csv?"), _deps(cfg, manifest, s))
    assert rec.route.qtype == QuestionType.COUNT  # keyword rule
    assert "route: LLMError" in rec.error and "plan: JSONDecodeError" in rec.error
    assert rec.answer == "The sources show 2478 for 2025." and rec.evidence


def test_no_model_still_answers(env):
    cfg, manifest = env
    rec = answer_question(_q("What was London's revenue in 2025?"), Deps(cfg, manifest, None))
    assert rec.answer.startswith("The supplied sources") and rec.total_calls == 0


def test_rule_router_and_combine():
    assert (
        router.rule_qtype("What is the area of zone 1907/A1 in hectares?") == QuestionType.SPATIAL
    )
    assert router.rule_qtype("How many records have no Height value?") == QuestionType.DATA_QUALITY
    assert router.rule_qtype("What does [z] mean in the workbook?") == QuestionType.SEMANTICS
    q = _q("What is the area of zone 1907/A1 in hectares?")
    llm = router.Route(qtype="data_quality", sub_questions=[], primary_source_ids=[TABLE])
    assert router.combine(llm, q).qtype == QuestionType.SPATIAL
    q2 = _q("How did revenue change from 2024 to 2025?")
    llm2 = llm.model_copy(update={"qtype": QuestionType.COMPARE})
    assert router.combine(llm2, q2).qtype == QuestionType.COMPARE


def test_plan_schema_examples_and_normalise(env):
    cfg, manifest = env
    ops = planner.ops_for("compare")
    assert "cell_lookup" in ops and "percent_change" in ops and "area" not in ops
    s = planner.plan_schema(ops)
    assert s["properties"]["steps"]["items"]["properties"]["op"]["enum"] == ops
    ex = planner.examples_for("spatial", exclude_id="dev-048")
    assert "dev-048" not in ex and "zone 1907/A1" not in ex  # the question itself is never shown
    assert "Question:" in planner.examples_for("spatial", exclude_id=None)
    from municipal_qa.profiling.profile import ProfileStore

    ctx = ExecContext(ProfileStore(cfg, manifest), Conventions(raw_context=""), [GUIDE, TABLE])
    st = normalise(Step(op="count_rows", save_as="n", args={"source": "REVENUE.csv",
                        "where": "metro IS NULL"}), ctx)  # fmt: skip
    assert st.args == {"source_id": TABLE, "where": [{"column": "metro", "op": "is_null"}]}
    run = execute(Plan(steps=[st]), ctx)
    assert run.ok and run.results[0].facts[0].value == 0  # [z] is a marker, not a blank
    w = normalise(Step(op="count_rows", save_as="n", args={"source_id": TABLE,
                       "where": [["year", ">=", "2024"], {"field": "london", "operator": ">",
                                                           "val": 2400}]}), ctx)  # fmt: skip
    assert w.args["where"] == [{"column": "year", "op": ">=", "value": "2024"},
                               {"column": "london", "op": ">", "value": 2400}]  # fmt: skip
    assert execute(Plan(steps=[w]), ctx).results[0].facts[0].value == 1
    st2 = normalise(Step(op="get_cell", save_as="c", args={"ref": "Data!H23"}), ctx)
    assert st2.args == {"ref": "H23", "sheet": "Data"}


def test_plan_stats_counts_executed_plans_and_repairs():
    from municipal_qa.evaluation.report import plan_stats
    from municipal_qa.schema import AnswerRecord, CallRecord, Route, ToolResult

    def rec(qid, qtype, oks, repairs):
        steps = [Step(op="count_rows", args={}, save_as=f"s{i}") for i in range(len(oks))]
        calls = [
            CallRecord(purpose="repair", prompt_tokens=1, generated_tokens=1, elapsed_ms=1)
        ] * repairs
        return AnswerRecord(question_id=qid, answer="a", evidence=[],
                            route=Route(qtype=qtype, sub_questions=[], primary_source_ids=[]),
                            plan=Plan(steps=steps) if oks else None,
                            results=[ToolResult(ok=o, op="count_rows", args={}) for o in oks],
                            calls=calls)  # fmt: skip

    recs = {"a": rec("a", "count", [True, True], 1), "b": rec("b", "count", [True, False], 2),
            "c": rec("c", "semantics", [], 0)}  # fmt: skip
    df = plan_stats(recs).set_index("route")
    assert df.loc["all", "planned"] == 2 and df.loc["all", "executed"] == 1
    assert df.loc["all", "executed_share"] == 0.5 and df.loc["count", "repairs"] == 3


def test_dependent_step_of_a_failed_step_gets_no_repair(env):
    cfg, manifest = env
    bad = {
        "op": "aggregate",
        "save_as": "a",
        "args": {"source_id": TABLE, "column": "x", "func": "sum"},
    }
    dep = {"op": "share", "save_as": "b", "args": {"part": "$a", "total": 10}}
    s = Script(route=ROUTE_LOOKUP, plan={"steps": [bad, dep]}, repair=bad, compose="Unknown.")
    rec = answer_question(_q("What is the total?"), _deps(cfg, manifest, s))
    assert [k for k, _ in s.seen].count("repair") == 2  # both spent on step a, none on b
    assert "an earlier step it depends on failed" in rec.results[1].error


def test_table_named_after_another_listed_file_moves_the_step(env):
    cfg, manifest = env
    from municipal_qa.profiling.profile import ProfileStore

    ctx = ExecContext(ProfileStore(cfg, manifest), Conventions(raw_context=""), [GUIDE, TABLE])
    where = [{"key_column": "year", "value": "2024"}]
    args = {"source_id": GUIDE, "table": "revenue.csv", "where": where}
    st = normalise(Step(op="count_rows", save_as="n", args=args), ctx)
    assert st.args == {"source_id": TABLE, "where": [{"column": "year", "value": "2024"}]}
    assert execute(Plan(steps=[st]), ctx).results[0].facts[0].value == 1


def test_constrained_schema_limits_names_and_ops(env):
    cfg, manifest = env
    from municipal_qa.profiling.profile import ProfileStore

    profiles = ProfileStore(cfg, manifest).get_many([GUIDE, TABLE])
    args = planner.constrained_args("aggregate", profiles)
    assert args["properties"]["source_id"]["enum"] == [TABLE]  # the guide has no tables
    assert args["properties"]["column"]["enum"] == ["year", "london", "metro"]
    where_col = args["properties"]["where"]["items"]["properties"]["column"]
    assert where_col["enum"] == ["year", "london", "metro"]
    assert planner.constrained_args("raster_stats", profiles) is None
    ops = planner.usable_ops(["aggregate", "raster_stats", "area", "share"], profiles)
    assert ops == ["aggregate", "share"]
    alts = planner.constrained_step(ops, profiles)["anyOf"]
    assert [a["properties"]["op"]["enum"] for a in alts] == [["aggregate"], ["share"]]
    assert "get_cell" not in planner.ops_for("lookup", "What was revenue in 2025?")
    assert planner.ops_for("lookup", "What is in cell H23?")[0] == "get_cell"


def test_parse_plan_makes_step_names_identifiers():
    a = {"op": "count_rows", "args": {"source_id": TABLE}, "save_as": "tpo-groups info"}
    b = {"op": "calc", "args": {"expression": "$tpo-groups info * 2"}, "save_as": "2x"}
    text = json.dumps({"steps": [a, b]})
    p = planner.parse_plan(text)
    assert [s.save_as for s in p.steps] == ["tpo_groups_info", "s2_2x"]
    assert p.steps[1].args["expression"] == "$tpo_groups_info * 2"
