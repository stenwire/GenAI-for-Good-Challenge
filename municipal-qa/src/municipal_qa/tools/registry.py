"""Operation DSL (plan §6.5, card M5): op specs, argument checks, `$name` references."""

from __future__ import annotations

import importlib
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from municipal_qa.readers.base import get_reader
from municipal_qa.schema import Conventions, Fact, SourceInfo, SourceProfile, Step, ToolResult

PREVIEW_CHARS = 1500
SOURCE_ARGS = {"source_id": "", "source_a": "_a", "source_b": "_b"}
COLUMN_ARGS = (
    "column",
    "columns",
    "key_column",
    "value_column",
    "group_by",
    "date_column",
    "label_column",
)


class OpError(Exception):
    """A problem the planner can fix; the message lists the valid options."""


@dataclass
class OpSpec:
    name: str
    fn: Callable[..., ToolResult]
    args_schema: dict
    description: str
    group: str = "table"


_OPS: dict[str, OpSpec] = {}


def register(spec: OpSpec) -> OpSpec:
    _OPS[spec.name] = spec
    return spec


def op(name: str, description: str, properties: dict, required: list[str], group: str = "table"):
    def deco(fn):
        schema = {"type": "object", "properties": properties, "required": required}
        register(OpSpec(name, fn, schema, description, group))
        return fn

    return deco


MODULES = ("table_ops", "calc", "spatial_ops", "raster_ops", "text_ops")


def load_all() -> dict[str, OpSpec]:
    for m in MODULES:
        importlib.import_module(f"municipal_qa.tools.{m}")
    return _OPS


def get_op(name: str) -> OpSpec:
    load_all()
    if name not in _OPS:
        raise OpError(f"unknown op {name!r}; ops: {', '.join(sorted(_OPS))}")
    return _OPS[name]


def op_names(groups: list[str] | None = None) -> list[str]:
    load_all()
    return sorted(n for n, s in _OPS.items() if groups is None or s.group in groups)


def _sig(spec: OpSpec) -> str:
    props, req = spec.args_schema["properties"], set(spec.args_schema["required"])
    parts = []
    for k, v in props.items():
        t = v.get("enum") and "|".join(map(str, v["enum"])) or v.get("type", "any")
        parts.append(f"{k}{'' if k in req else '?'}: {t}")
    return f"{spec.name}({', '.join(parts)})"


def ops_menu(names: list[str] | None = None) -> str:
    load_all()
    return "\n".join(f"- {_sig(_OPS[n])}: {_OPS[n].description}" for n in names or sorted(_OPS))


@dataclass
class ExecContext:
    store: Any  # ProfileStore
    conventions: Conventions
    source_ids: list[str]
    tables: dict[tuple, pd.DataFrame] = field(default_factory=dict)
    env: dict[str, ToolResult] = field(default_factory=dict)
    retriever: Any = None

    def source(self, sid: str) -> SourceInfo:
        if sid not in self.source_ids or sid not in self.store.manifest:
            raise OpError(
                f"source {sid!r} is not listed for this question; use one of {self.source_ids}"
            )
        return self.store.manifest[sid]

    def profile(self, sid: str) -> SourceProfile:
        return self.store.get(sid)

    def table_name(self, sid: str, table: str | None) -> str | None:
        names = [t.table_name for t in self.profile(sid).tables]
        if table in names:
            return table
        if table:
            low = {n.lower(): n for n in names}
            if table.lower() in low:
                return low[table.lower()]
            raise OpError(f"no table {table!r} in {sid}; tables: {names}")
        if len(names) == 1:
            return names[0]
        if not names:
            raise OpError(f"{sid} has no tables ({self.profile(sid).kind})")
        raise OpError(f"{sid} has several tables; name one of {names}")

    def columns(self, sid: str, table: str) -> list[str]:
        t = next(t for t in self.profile(sid).tables if t.table_name == table)
        return [c.name for c in t.columns]

    def frame(self, sid: str, table: str | None = None, columns=None) -> pd.DataFrame:
        """Cached table; files over 50 MB load only `columns`, through DuckDB."""
        src = self.source(sid)
        table = self.table_name(sid, table)
        large = self.profile(sid).kind == "table" and _large(src)
        cols = tuple(sorted(set(columns))) if large and columns else None
        key = (sid, table, self.conventions.value_mode, cols)
        if key not in self.tables:
            self.tables[key] = _load(src, table, self.profile(sid), self.conventions, cols)
        return self.tables[key]


def _large(src: SourceInfo) -> bool:
    from municipal_qa.readers import delimited

    return delimited._is_large(src.path)


def _load(src, table, prof: SourceProfile, conv: Conventions, cols: tuple | None = None):
    if prof.kind == "workbook":
        from municipal_qa.readers.spreadsheet import sheet_frame

        hdr = next((t.header_row for t in prof.tables if t.table_name == table), None)
        return sheet_frame(src.path, table, hdr, conv.value_mode)
    if prof.kind == "table":
        from municipal_qa.readers import delimited

        if delimited._is_large(src.path):
            sel = ", ".join('"' + c.replace('"', '""') + '"' for c in cols) if cols else "*"
            q = f"SELECT {sel} FROM {delimited.duck_source(src.path)}"
            df = delimited._duck().execute(q).df()
            df.index = pd.RangeIndex(1, len(df) + 1, name="data_row")
            return df
    return get_reader(src).load_table(src, table)


_REF = re.compile(r"^\$([A-Za-z_]\w*)(?:\.(.+))?$")


def resolve(value: Any, env: dict[str, ToolResult]) -> Any:
    """`$name` → the step's first fact, `$name.fact` → that fact, recursively in lists/dicts."""
    if isinstance(value, list):
        return [resolve(v, env) for v in value]
    if isinstance(value, dict):
        return {k: resolve(v, env) for k, v in value.items()}
    m = _REF.match(value) if isinstance(value, str) else None
    if not m:
        return value
    name, fact = m.group(1), m.group(2)
    if name not in env:
        raise OpError(f"unknown reference {value}; saved steps: {sorted(env)}")
    res = env[name]
    if not res.ok or not res.facts:
        raise OpError(f"reference {value}: step {name!r} produced no facts")
    if not fact:
        return res.facts[0]
    for f in res.facts:
        if f.name == fact:
            return f
    raise OpError(f"reference {value}: no fact {fact!r}; facts: {[f.name for f in res.facts]}")


_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list}


def _coerce(name: str, v: Any, spec: dict) -> Any:
    t = spec.get("type")
    if isinstance(v, Fact) or t is None:
        return v
    if t == "array" and not isinstance(v, list):
        return [v]
    if t == "integer" and isinstance(v, str) and v.strip().lstrip("-").isdigit():
        return int(v)
    if t == "number" and isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            pass
    if t == "string" and isinstance(v, int | float) and not isinstance(v, bool):
        return str(v)
    if t in _TYPES and not isinstance(v, _TYPES[t]):
        raise OpError(f"argument {name} must be {t}, got {type(v).__name__}")
    if "enum" in spec and v not in spec["enum"]:
        raise OpError(f"argument {name} must be one of {spec['enum']}, got {v!r}")
    return v


def check_args(spec: OpSpec, args: dict, ctx: ExecContext) -> dict:
    props, req = spec.args_schema["properties"], spec.args_schema["required"]
    missing = [k for k in req if args.get(k) in (None, "", [])]
    if missing:
        raise OpError(f"{spec.name}: missing argument(s) {missing}; signature {_sig(spec)}")
    out = {k: _coerce(k, v, props[k]) for k, v in args.items() if k in props and v is not None}
    for arg, suffix in SOURCE_ARGS.items():
        if arg not in out:
            continue
        sid = out[arg]
        ctx.source(sid)
        if spec.group not in ("table", "spatial") or not ctx.profile(sid).tables:
            continue
        tkey = "table" + suffix
        if tkey in props:
            out[tkey] = ctx.table_name(sid, out.get(tkey))
            cols = ctx.columns(sid, out[tkey])
            for c in COLUMN_ARGS:
                ckey = c + suffix
                if ckey in out:
                    out[ckey] = _check_columns(out[ckey], cols, sid, out[tkey])
            if "where" in out and suffix == "":
                for w in out["where"]:
                    if isinstance(w, dict) and "column" in w:
                        w["column"] = _check_columns(w["column"], cols, sid, out[tkey])
    return out


_NOTE = re.compile(r"\[[^\]]*\]|\([^)]*note[^)]*\)", re.I)


def loose_name(s: str) -> str:
    """Ignores case, separators and bracketed notes: `confirmed_date` ~ `Confirmed-date [n2]`."""
    return re.sub(r"[\s_\-.:/]+", "", _NOTE.sub("", str(s)).lower())


def _check_columns(v: Any, cols: list[str], sid: str, table: str) -> Any:
    if isinstance(v, list):
        return [_check_columns(x, cols, sid, table) for x in v]
    if not isinstance(v, str) or v in cols:
        return v
    for key in (lambda s: s.lower().strip(), loose_name):
        idx: dict[str, list[str]] = {}
        for c in cols:
            idx.setdefault(key(c), []).append(c)
        hit = idx.get(key(v), [])
        if len(hit) == 1:
            return hit[0]
    shown = ", ".join(cols[:40]) + (" ..." if len(cols) > 40 else "")
    raise OpError(f"no column {v!r} in {sid} table {table}; columns: {shown}")


def run_step(step: Step, env: dict[str, ToolResult], ctx: ExecContext) -> ToolResult:
    t0 = time.perf_counter()
    ctx.env = env
    try:
        spec = get_op(step.op)
        args = check_args(spec, resolve(step.args, env), ctx)
        res = spec.fn(ctx, **args)
    except OpError as e:
        res = ToolResult(ok=False, op=step.op, args=step.args, error=str(e))
    except Exception as e:  # noqa: BLE001
        res = ToolResult(ok=False, op=step.op, args=step.args, error=f"{type(e).__name__}: {e}")
    if res.preview and len(res.preview) > PREVIEW_CHARS:
        res.preview = res.preview[: PREVIEW_CHARS - 1] + "…"
    res.args = {k: v.display if isinstance(v, Fact) else v for k, v in step.args.items()}
    res.elapsed_ms = int((time.perf_counter() - t0) * 1000)
    return res
