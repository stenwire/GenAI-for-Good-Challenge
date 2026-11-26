"""Planner and repair (card S3): JSON-schema plans over the op DSL, few-shot from dev only."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

from municipal_qa.llm.prompts import render_prompt
from municipal_qa.schema import Conventions, Plan, Question, Route, Step
from municipal_qa.tools import registry

EXAMPLES = Path(__file__).resolve().parents[1] / "llm" / "prompts" / "plan_examples.json"
MAX_STEPS = 6
TABLE_READ = [
    "cell_lookup", "table_info", "lookup_row", "filter_rows", "get_cell", "find_cells"
]  # fmt: skip
CALC = ["percent_change", "share", "pp_difference", "ratio", "calc", "compare_values"]
OPS_BY_QTYPE = {
    "lookup": [*TABLE_READ, "get_range", "date_diff", "feature_lookup", "get_definition",
               "ifc_footprint_area", *CALC],
    "count": ["count_rows", "count_distinct", "value_counts", "count_features", "filter_rows",
              "pointcloud_class_counts", "share", "cell_lookup"],
    "aggregate": ["aggregate", "group_aggregate", "top_n", "value_counts", "row_wise_max",
                  *TABLE_READ, *CALC, "sql"],
    "compare": [*TABLE_READ, "aggregate", "group_aggregate", "row_wise_max", *CALC, "date_diff"],
    "spatial": ["layer_info", "area", "length", "distance", "within_radius", "point_in_polygon",
                "intersects", "union_footprint", "count_features", "feature_lookup", "centroid",
                "height_span", "geometry_validity", "convert_unit", "ifc_footprint_area", *CALC],
    "raster": ["raster_info", "raster_stats", "raster_pair_diff", "pointcloud_info",
               "pointcloud_class_counts", "pointcloud_z_stats", "area", *CALC],
    "data_quality": ["count_nulls", "duplicates", "set_compare", "join_check", "count_distinct",
                     "count_rows", "geometry_validity", "value_counts", "share"],
    "semantics": ["get_definition", "get_section", "find_passages", "lookup_row", "cell_lookup",
                  "count_rows", "value_counts"],
    "criteria": ["check_criteria", "lookup_row", "cell_lookup", "get_definition", "find_passages",
                 *CALC],
    "insufficient": ["get_definition", "find_passages", "table_info", "layer_info"],
}  # fmt: skip


def ops_for(qtype: str) -> list[str]:
    known = set(registry.op_names())
    return [o for o in dict.fromkeys(OPS_BY_QTYPE.get(qtype, TABLE_READ + CALC)) if o in known]


@cache
def _examples() -> tuple[dict, ...]:
    return tuple(json.loads(EXAMPLES.read_text("utf-8")))


def examples_for(qtype: str, exclude_id: str | None, n: int = 2) -> str:
    """Same qtype first, then any; never the question being answered."""
    pool = [e for e in _examples() if e["id"] != exclude_id]
    pick = ([e for e in pool if e["qtype"] == qtype] + [e for e in pool if e["qtype"] != qtype])[:n]
    return "\n\n".join(
        f"Question: {e['question']}\nPlan: " + json.dumps({"steps": e["steps"]}, ensure_ascii=False)
        for e in pick
    )


def step_schema(ops: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "op": {"type": "string", "enum": ops},
            "args": {"type": "object"},
            "save_as": {"type": "string"},
        },
        "required": ["op", "args", "save_as"],
    }


def plan_schema(ops: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_STEPS,
                "items": step_schema(ops),
            },
            "rationale": {"type": "string"},
        },
        "required": ["steps"],
    }


def conventions_text(conv: Conventions) -> str:
    d = conv.model_dump(exclude={"raw_context"}, exclude_defaults=False)
    return ", ".join(
        f"{k}={v}" for k, v in d.items() if v not in (None, {}, False) or k == "repair_geometry"
    )


def _subs(r: Route) -> str:
    return "\n".join(
        f"- {s.text} ({s.expects}{', ' + s.unit if s.unit else ''})" for s in r.sub_questions
    )


def parse_plan(text: str) -> Plan:
    data = json.loads(text)
    steps = []
    for i, s in enumerate(data.get("steps", [])[:MAX_STEPS]):
        if isinstance(s, dict) and s.get("op"):
            name = str(s.get("save_as") or f"s{i + 1}").strip().lstrip("$") or f"s{i + 1}"
            steps.append(Step(op=s["op"], args=s.get("args") or {}, save_as=name))
    if not steps:
        raise ValueError("plan has no steps")
    return Plan(steps=steps, rationale=data.get("rationale"))


def plan(q: Question, r: Route, conv: Conventions, profiles_text: str, client, budget) -> Plan:
    ops = ops_for(r.qtype.value)
    prompt = render_prompt(
        "plan",
        ops=registry.ops_menu(ops),
        examples=examples_for(r.qtype.value, q.id),
        profiles=profiles_text,
        conventions=conventions_text(conv),
        qtype=r.qtype.value,
        sub_questions=_subs(r),
        question=q.question,
    )
    text, _ = client.chat([{"role": "user", "content": prompt}], "plan", budget,
                          json_schema=plan_schema(ops))  # fmt: skip
    return parse_plan(text)


def repair(q: Question, step: Step, error: str, saved: list[str], profile_text: str, client,
           budget, ops: list[str]) -> Step:  # fmt: skip
    try:
        sig = registry.ops_menu([step.op])
    except KeyError:
        sig = registry.ops_menu(ops)
    prompt = render_prompt(
        "repair",
        step=json.dumps(step.model_dump(), ensure_ascii=False, default=str),
        error=error[:1200],
        signature=sig,
        saved=", ".join(f"${s}" for s in saved) or "none",
        profile=profile_text,
        question=q.question,
    )
    text, _ = client.chat([{"role": "user", "content": prompt}], "repair", budget,
                          json_schema=step_schema(ops))  # fmt: skip
    data = json.loads(text)
    return Step(op=data["op"], args=data.get("args") or {}, save_as=step.save_as)
