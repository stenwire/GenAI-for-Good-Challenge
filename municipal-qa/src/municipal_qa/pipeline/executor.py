"""Executor (plan §6.2 step 6): run a Plan through the registry, repairing failed steps."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from municipal_qa.schema import Plan, Step, ToolResult
from municipal_qa.tools.registry import ExecContext, run_step

SOURCE_KEYS = ("source_id", "source_a", "source_b", "mask_source")
_SID = re.compile(r"SRC_[0-9a-f]{12}")
_WHERE = re.compile(r"^\s*([^<>=!]+?)\s*(==|=|!=|>=|<=|>|<)\s*(.+?)\s*$")


@dataclass
class Execution:
    steps: list[Step] = field(default_factory=list)
    results: list[ToolResult] = field(default_factory=list)
    env: dict[str, ToolResult] = field(default_factory=dict)
    repairs: int = 0

    @property
    def ok(self) -> bool:
        return bool(self.results) and all(r.ok for r in self.results)

    def facts(self):
        return [(s.save_as, f) for s, r in zip(self.steps, self.results, strict=True) if r.ok
                for f in r.facts]  # fmt: skip


def _source(v, ctx: ExecContext):
    """Accept a filename or a mistyped ID where an allowed source ID is meant."""
    if not isinstance(v, str) or v in ctx.source_ids:
        return v
    m = _SID.search(v)
    if m and m.group(0) in ctx.source_ids:
        return m.group(0)
    names = {ctx.store.manifest[s].original_file.lower(): s for s in ctx.source_ids
             if s in ctx.store.manifest}  # fmt: skip
    low = v.strip().lower()
    if low in names:
        return names[low]
    hits = [s for n, s in names.items() if low and (low in n or n in low)]
    return hits[0] if len(hits) == 1 else v


_NULLS = re.compile(r"^\s*(.+?)\s+is\s+(not\s+)?null\s*$", re.I)


KEY_ALIASES = {"field": "column", "col": "column", "name": "column", "key": "column",
               "operator": "op", "comparison": "op", "val": "value", "equals": "value"}  # fmt: skip


def _clause(item):
    if isinstance(item, list | tuple) and len(item) in (2, 3) and isinstance(item[0], str):
        return {"column": item[0], "op": item[1] if len(item) == 3 else "=", "value": item[-1]}
    if isinstance(item, dict) and "column" not in item:
        fixed = {KEY_ALIASES.get(k, k): x for k, x in item.items()}
        if "column" in fixed:
            return fixed
    return item


def _where(v):
    if isinstance(v, list) and len(v) in (2, 3) and all(not isinstance(x, list | dict) for x in v):
        v = [v]
    if isinstance(v, list):
        v = [_clause(x) for x in v]
        return [x for item in v for x in (_where(item) if isinstance(item, str) else [item])]
    if isinstance(v, str) and _NULLS.match(v):
        m = _NULLS.match(v)
        return [{"column": m.group(1).strip("'\" "), "op": "not_null" if m.group(2) else "is_null"}]
    if isinstance(v, dict):
        v = [v] if "column" in v else [{"column": k, "op": "=", "value": x} for k, x in v.items()]
    if isinstance(v, str):
        m = _WHERE.match(v)
        if not m:
            return v
        op = "=" if m.group(2) == "==" else m.group(2)
        return [{"column": m.group(1).strip(), "op": op, "value": m.group(3).strip("'\"")}]
    return v


def normalise(step: Step, ctx: ExecContext) -> Step:
    args = dict(step.args)
    for k in SOURCE_KEYS:
        if k in args:
            args[k] = _source(args[k], ctx)
    if "source" in args and "source_id" not in args:
        args["source_id"] = _source(args.pop("source"), ctx)
    for k in ("where", "where_b", "mask_where"):
        if k in args:
            args[k] = _where(args[k])
    ref = args.get("ref")
    if isinstance(ref, str) and "!" in ref:
        sheet, _, cell = ref.rpartition("!")
        args["ref"] = cell.replace("$", "")
        args.setdefault("sheet", sheet.strip("'"))
    for k, v in list(args.items()):
        if isinstance(v, str) and v.startswith("$") and v[1:].split(".")[0] not in ctx.env:
            base = v[1:].split(".")[0]
            if base.lower() in {e.lower() for e in ctx.env}:
                real = next(e for e in ctx.env if e.lower() == base.lower())
                args[k] = "$" + real + v[1 + len(base) :]
    return step.model_copy(update={"args": args})


def execute(plan: Plan, ctx: ExecContext, repair: Callable[[Step, str], Step] | None = None,
            max_repairs: int = 2) -> Execution:  # fmt: skip
    ex = Execution()
    ctx.env = ex.env
    failed: set[str] = set()
    for step in plan.steps:
        step = normalise(step, ctx)
        res = run_step(step, ex.env, ctx)
        if not res.ok and _needs_failed(step, failed):
            res.error = f"{res.error} (an earlier step it depends on failed)"
        while not res.ok and repair is not None and ex.repairs < max_repairs:
            if _needs_failed(step, failed):
                break
            ex.repairs += 1
            try:
                fixed = normalise(repair(step, res.error or "failed"), ctx)
            except Exception as e:  # noqa: BLE001
                res.error = f"{res.error}; repair failed: {type(e).__name__}: {e}"
                break
            step, res = fixed, run_step(fixed, ex.env, ctx)
        ex.steps.append(step)
        ex.results.append(res)
        if res.ok:
            ex.env[step.save_as] = res
        else:
            failed.add(step.save_as)
    return ex


def _needs_failed(step: Step, failed: set[str]) -> bool:
    """A step that only fails because an earlier step failed is not worth a repair call."""
    refs = re.findall(r"\$([A-Za-z_]\w*)", str(step.args))
    return any(r in failed for r in refs)
