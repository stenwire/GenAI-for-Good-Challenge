"""Planner and repair (card S3): JSON-schema plans over the op DSL, few-shot from dev only."""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path

from municipal_qa.llm.prompts import render_prompt
from municipal_qa.schema import Conventions, Plan, Question, Route, Step
from municipal_qa.tools import registry

EXAMPLES = Path(__file__).resolve().parents[1] / "llm" / "prompts" / "plan_examples.json"
MAX_STEPS = 6
TABLE_READ = ["cell_lookup", "table_info", "lookup_row", "filter_rows"]
A1 = re.compile(r"\b(cells?|ref)\s+[A-Z]{1,3}\d{1,6}\b|\b[A-Z]{1,3}\d{1,6}:[A-Z]{1,3}\d{1,6}\b")
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


def ops_for(qtype: str, question: str = "") -> list[str]:
    """A1-reference ops only when the question names a cell; labels otherwise (cell_lookup)."""
    known = set(registry.op_names())
    ops = list(OPS_BY_QTYPE.get(qtype, TABLE_READ + CALC))
    if A1.search(question):
        ops = ["get_cell", "get_range", *ops]
    return [o for o in dict.fromkeys(ops) if o in known]


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


SOURCE_KEYS = ("source_id", "source_a", "source_b", "mask_source")
TABLE_KEYS = ("table", "table_a", "table_b", "mask_table")
COLUMN_KEYS = ("column", "key_column", "value_column", "label_column", "date_column",
               "column_a", "column_b", "key_column_b")  # fmt: skip
COLUMN_LISTS = ("columns", "group_by")
MAX_ENUM = 400
WORKBOOK_OPS = {"get_cell", "get_range", "find_cells"}
KIND_OPS = {"raster": {"raster_info", "raster_stats", "raster_pair_diff"},
            "pointcloud": {"pointcloud_info", "pointcloud_class_counts", "pointcloud_z_stats"},
            "bim": {"ifc_footprint_area"}}  # fmt: skip


def _sources_for(op: str, group: str, profiles) -> list[str]:
    def sid(p):
        return p.source.source_id

    if op in WORKBOOK_OPS:
        return [sid(p) for p in profiles if p.kind == "workbook"]
    if op == "cell_lookup":
        return [sid(p) for p in profiles if p.tables]
    for kind, names in KIND_OPS.items():
        if op in names:
            return [sid(p) for p in profiles if p.kind == kind]
    if group == "spatial":
        return [sid(p) for p in profiles
                if p.kind == "vector" or any(t.geometry_type for t in p.tables)]  # fmt: skip
    if group == "table":
        return [sid(p) for p in profiles if p.tables]
    return [sid(p) for p in profiles]


def _enum(values) -> dict:
    vals = list(dict.fromkeys(str(v) for v in values))[:MAX_ENUM]
    return {"type": "string", "enum": vals} if vals else {"type": "string"}


def constrained_args(op: str, profiles) -> dict | None:
    """The op's args schema with sources, tables, sheets and columns limited to real names."""
    spec = registry.get_op(op)
    props = dict(spec.args_schema["properties"])
    sources = _sources_for(op, spec.group, profiles)
    if any(k in props for k in SOURCE_KEYS) and not sources:
        return None
    mine = [p for p in profiles if p.source.source_id in sources]
    tables = [t.table_name for p in mine for t in p.tables]
    cols = [c.name for p in mine for t in p.tables for c in t.columns]
    if op == "cell_lookup":
        props = {k: (_enum(sources) if k in SOURCE_KEYS else v) for k, v in props.items()}
        req = spec.args_schema["required"]
        return {"type": "object", "properties": _cell_lookup_args(props, mine), "required": req}
    for k in props:
        if k in SOURCE_KEYS:
            props[k] = _enum(sources)
        elif (k in TABLE_KEYS or k == "sheet") and tables:
            props[k] = _enum(tables)
        elif k in COLUMN_KEYS and cols:
            props[k] = _enum(cols)
        elif k in COLUMN_LISTS and cols:
            props[k] = {"type": "array", "items": _enum(cols)}
        elif k in ("where", "where_b", "mask_where") and cols:
            item = dict(props[k]["items"])
            item["properties"] = {**item["properties"], "column": _enum(cols)}
            props[k] = {"type": "array", "items": item}
    return {"type": "object", "properties": props, "required": spec.args_schema["required"]}


_YEAR = re.compile(r"^(1[89]|20)\d\d$")


@cache
def sheet_labels(path: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(row labels, column labels) of every data sheet, for cell_lookup's enums."""
    from municipal_qa.readers.spreadsheet import read_cells

    rows, cols = [], []
    for cells in read_cells(path).values():
        by_row: dict[int, list] = {}
        for c in cells:
            by_row.setdefault(c.row, []).append(c)
        for r in by_row.values():
            r.sort(key=lambda c: c.col)
            first = next((c for c in r if c.col <= 2 and c.displayed not in (None, "")), None)
            if first is not None:
                rows.append(str(first.displayed).strip()[:80])
            rest = [str(c.displayed).strip() for c in r if c is not first and c.displayed]
            if len(rest) >= 2 and all(not _num(x) or _YEAR.match(x) for x in rest):
                cols += [x[:80] for x in rest]
    return tuple(dict.fromkeys(rows)), tuple(dict.fromkeys(cols))


def _num(x: str) -> bool:
    return bool(re.match(r"^-?[\d,]+(\.\d+)?%?$", x))


def _cell_lookup_args(props: dict, mine) -> dict:
    books = [p for p in mine if p.kind == "workbook"]
    if not books or len(books) != len(mine):
        return props
    rows, cols = [], []
    for p in books:
        r, c = sheet_labels(str(Path(p.source.path).resolve()))
        rows += r
        cols += c
    props["sheet"] = _enum(t.table_name for p in books for t in p.tables)
    props["row"], props["block"] = _enum(rows), _enum(rows)
    if cols:
        props["column"] = _enum(cols)
    return props


def usable_ops(ops: list[str], profiles) -> list[str]:
    return [o for o in ops if constrained_args(o, profiles) is not None]


def constrained_step(ops: list[str], profiles) -> dict:
    """One alternative per op, so each op only gets its own argument names (D-055)."""
    alts = []
    for o in ops:
        args = constrained_args(o, profiles)
        if args is not None:
            alts.append({"type": "object", "properties": {"op": {"type": "string", "enum": [o]},
                         "args": args, "save_as": {"type": "string"}},
                         "required": ["op", "args", "save_as"]})  # fmt: skip
    return {"anyOf": alts} if alts else step_schema(ops)


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


def plan_schema(ops: list[str], profiles=None) -> dict:
    item = constrained_step(ops, profiles) if profiles else step_schema(ops)
    return {
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_STEPS,
                "items": item,
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


def _safe(name: str, i: int) -> str:
    s = re.sub(r"\W+", "_", str(name).strip().lstrip("$")).strip("_")
    return s if s and not s[0].isdigit() else f"s{i + 1}_{s}".rstrip("_")


def _rename(v, names: dict[str, str]):
    if isinstance(v, str):
        for old, new in sorted(names.items(), key=lambda x: -len(x[0])):
            v = v.replace("$" + old, "$" + new)
        return v
    if isinstance(v, list):
        return [_rename(x, names) for x in v]
    if isinstance(v, dict):
        return {k: _rename(x, names) for k, x in v.items()}
    return v


def parse_plan(text: str) -> Plan:
    """Step names become identifiers, and `$references` to them are rewritten to match."""
    data = json.loads(text)
    raw = [s for s in data.get("steps", [])[:MAX_STEPS] if isinstance(s, dict) and s.get("op")]
    names = {str(s.get("save_as") or "").strip().lstrip("$"): _safe(s.get("save_as") or "", i)
             for i, s in enumerate(raw)}  # fmt: skip
    names.pop("", None)
    steps = []
    for i, s in enumerate(raw):
        name = _safe(s.get("save_as") or f"s{i + 1}", i)
        steps.append(Step(op=s["op"], args=_rename(s.get("args") or {}, names), save_as=name))
    if not steps:
        raise ValueError("plan has no steps")
    return Plan(steps=steps, rationale=data.get("rationale"))


def plan(q: Question, r: Route, conv: Conventions, profiles_text: str, client, budget,
         profiles=None) -> Plan:  # fmt: skip
    ops = ops_for(r.qtype.value, q.question)
    if profiles:
        ops = usable_ops(ops, profiles) or ops
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
                          json_schema=plan_schema(ops, profiles))  # fmt: skip
    return parse_plan(text)


def repair(q: Question, step: Step, error: str, saved: list[str], profile_text: str, client,
           budget, ops: list[str], profiles=None) -> Step:  # fmt: skip
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
                          json_schema=_repair_schema(ops, profiles))  # fmt: skip
    data = json.loads(text)
    return Step(op=data["op"], args=data.get("args") or {}, save_as=step.save_as)


def _repair_schema(ops: list[str], profiles) -> dict:
    if not profiles:
        return step_schema(ops)
    return constrained_step(usable_ops(ops, profiles) or ops, profiles)
