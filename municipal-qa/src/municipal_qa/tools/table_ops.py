"""Table ops (plan §6.5, card M5). Values stay as published; numbers parse to Decimal."""

from __future__ import annotations

import re
from collections import Counter
from decimal import Decimal

import duckdb
import pandas as pd

from municipal_qa.readers.spreadsheet import (
    cell_locator_text,
    cell_value,
    col_letter,
    get_cell,
    parse_ref,
    read_cells,
)
from municipal_qa.schema import Fact, Locator, ToolResult
from municipal_qa.tools.numbers import (
    count_display,
    display,
    is_marker,
    plain_value,
    to_decimal,
)
from municipal_qa.tools.registry import ExecContext, OpError, op

S = {"type": "string"}
INT = {"type": "integer"}
B = {"type": "boolean"}
ARR = {"type": "array", "items": {"type": "string"}}
WHERE_OPS = [
    "=", "!=", ">", ">=", "<", "<=", "contains", "icontains", "startswith",
    "in", "not_in", "is_null", "not_null",
]  # fmt: skip
WHERE = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {"column": S, "op": {"type": "string", "enum": WHERE_OPS}, "value": {}},
        "required": ["column"],
    },
}
SRC = {"source_id": S, "table": S}
MAX_FACTS = 20


# ---------- helpers ----------


def val(v):
    return v.value if isinstance(v, Fact) else v


def is_null(x) -> bool:
    if x is None:
        return True
    if isinstance(x, float) and x != x:
        return True
    try:
        if pd.isna(x):
            return True
    except (TypeError, ValueError):
        pass
    return isinstance(x, str) and not x.strip()


def text(x) -> str:
    if is_null(x):
        return ""
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    return str(x).strip()


def _cmp(x, op: str, v) -> bool:
    if op == "is_null":
        return is_null(x)
    if op == "not_null":
        return not is_null(x)
    if op in ("in", "not_in"):
        vs = {text(val(i)) for i in (v if isinstance(v, list) else [v])}
        return (text(x) in vs) == (op == "in")
    v = val(v)
    if op in ("contains", "icontains", "startswith"):
        a, b = text(x), text(v)
        if op == "icontains":
            return b.casefold() in a.casefold()
        return b in a if op == "contains" else a.startswith(b)
    dx, dv = to_decimal(x), to_decimal(v)
    if op in ("=", "!="):
        same = dx == dv if dx is not None and dv is not None else text(x) == text(v)
        return same == (op == "=")
    if dx is not None and dv is not None:
        a, b = dx, dv
    elif is_null(x):
        return False
    else:
        a, b = text(x), text(v)
    return {">": a > b, ">=": a >= b, "<": a < b, "<=": a <= b}[op]


OP_ALIASES = {
    "==": "=", "eq": "=", "equal": "=", "equals": "=", "is": "=", "ne": "!=", "<>": "!=",
    "not_equal": "!=", "gt": ">", "ge": ">=", "gte": ">=", "lt": "<", "le": "<=", "lte": "<=",
    "like": "icontains", "includes": "icontains", "has": "icontains", "starts_with": "startswith",
    "isnull": "is_null", "is null": "is_null", "notnull": "not_null", "is not null": "not_null",
    "not in": "not_in",
}  # fmt: skip
NULL_WORDS = {"null", "none", "nan", "", "empty", "blank"}


def _clause(w: dict) -> dict:
    op_ = str(w.get("op", "=")).strip().lower()
    op_ = OP_ALIASES.get(op_, op_)
    v = val(w.get("value"))
    if op_ in ("=", "!=") and (
        v is None or (isinstance(v, str) and v.strip().lower() in NULL_WORDS)
    ):
        op_ = "is_null" if op_ == "=" else "not_null"
    return {**w, "op": op_}


def apply_where(df: pd.DataFrame, where: list | None) -> pd.DataFrame:
    if not where:
        return df
    where = [_clause(w) if isinstance(w, dict) else w for w in where]
    mask = pd.Series(True, index=df.index)
    for w in where:
        if not isinstance(w, dict) or "column" not in w:
            raise OpError(f"bad where clause {w!r}; use {{column, op, value}}")
        op_ = w.get("op", "=")
        if op_ not in WHERE_OPS:
            raise OpError(f"where op {op_!r} not in {WHERE_OPS}")
        col, target = df[w["column"]], w.get("value")
        if isinstance(col, pd.DataFrame):  # duplicated column label: the first one is meant
            col = col.iloc[:, 0]
        mask &= col.map(lambda x, o=op_, v=target: _cmp(x, o, v)).astype(bool)
    return df[mask]


def where_text(where: list | None) -> str:
    if not where:
        return ""
    parts = []
    for w in where:
        v = val(w.get("value"))
        parts.append(
            f"{w['column']} {w.get('op', '=')} {v}".strip()
            if "value" in w
            else f"{w['column']} {w.get('op', '=')}"
        )
    return " where " + " and ".join(parts)


def scope(ctx: ExecContext, sid: str, table: str) -> str:
    src = ctx.source(sid)
    kind = ctx.profile(sid).kind
    if table == src.original_file:
        return src.original_file
    word = {"workbook": "sheet", "vector": "layer", "database": "table"}.get(kind, "table")
    return f"{src.original_file} {word} {table}"


def _letters(ctx: ExecContext, sid: str, table: str) -> dict[str, str]:
    prof = ctx.profile(sid)
    hdr = next((t.header_row for t in prof.tables if t.table_name == table), None)
    cells = read_cells(ctx.source(sid).path).get(table, [])
    names = {
        c.col: str(c.displayed if c.displayed is not None else c.stored).strip()
        for c in cells
        if c.row == hdr
    }
    return {
        (names.get(c) or f"column {col_letter(c)}"): col_letter(c)
        for c in sorted({c.col for c in cells} | set(names))
    }


def column_loc(ctx: ExecContext, sid: str, table: str, column: str) -> Locator:
    kind = ctx.profile(sid).kind
    if kind == "workbook":
        letter = _letters(ctx, sid, table).get(column)
        t = f"sheet {table}, column {letter} ({column})" if letter else f"sheet {table}"
    elif kind == "vector":
        t = f"layer {table}, field {column}"
    elif kind == "database":
        t = f"table {table}, column {column}"
    elif kind == "bim":
        t = f"{table} entities, {column}"
    else:
        t = f"column {column}"
    return Locator(source_id=sid, kind="column", text=t)


def row_loc(ctx, sid, table, idx, row, column=None, key=None) -> Locator:
    kind = ctx.profile(sid).kind
    if kind == "workbook":
        letter = _letters(ctx, sid, table).get(column) if column else None
        if letter:
            return Locator(
                source_id=sid, kind="cell", text=cell_locator_text(table, f"{letter}{idx}")
            )
        return Locator(source_id=sid, kind="row", text=f"sheet {table}, row {idx}")
    if kind == "bim" and "step_id" in row:
        t = f"{row['step_id']} {table} Name={row.get('Name')}"
        return Locator(source_id=sid, kind="ifc_entity", text=t + (f", {column}" if column else ""))
    if key and key in row:
        t = f"{key}={text(row[key])}" + (f", column {column}" if column else "")
        if kind == "vector":
            t = f"layer {table}, feature {t}"
        return Locator(source_id=sid, kind="record", text=t)
    if kind == "table":
        t = f"data row {idx}" + (f", column {column}" if column else "")
        return Locator(source_id=sid, kind="row", text=t)
    if kind == "vector":
        t = f"layer {table}, feature {int(idx) + 1}" + (f", {column}" if column else "")
        return Locator(source_id=sid, kind="feature", text=t)
    if kind == "database":
        rid = row.get("rowid", idx)
        t = f"table {table}, rowid {rid}" + (f", column {column}" if column else "")
        return Locator(source_id=sid, kind="table_row", text=t)
    return Locator(
        source_id=sid, kind="json_pointer", text=f"/{idx}" + (f"/{column}" if column else "")
    )


def num_fact(name, d: Decimal, ctx, unit, claim, locs, note=None, decimals=None) -> Fact:
    return Fact(
        name=name,
        value=plain_value(d),
        unit=unit,
        display=display(d, ctx.conventions, unit, decimals),
        claim=claim,
        locators=locs,
        note=note,
    )


def count_fact(name, n: int, noun, claim, locs, note=None) -> Fact:
    return Fact(
        name=name,
        value=n,
        unit=noun,
        display=count_display(n, noun),
        claim=claim,
        locators=locs,
        note=note,
    )


def str_fact(name, v, claim, locs, note=None) -> Fact:
    return Fact(
        name=name,
        value=None if is_null(v) else (v if isinstance(v, int | float | bool) else text(v)),
        display=text(v) or "(blank)",
        claim=claim,
        locators=locs,
        note=note,
    )


def ok(op_: str, args: dict, facts: list[Fact], preview: str | None = None) -> ToolResult:
    return ToolResult(ok=True, op=op_, args={}, facts=facts, preview=preview)


def preview_frame(df: pd.DataFrame, n: int = 10) -> str:
    d = df.drop(columns=[c for c in df.columns if c == "geometry"], errors="ignore").head(n)
    return d.to_string(max_colwidth=40)


def numbers(series: pd.Series) -> tuple[list[tuple[object, Decimal]], int, Counter]:
    """(index, Decimal) pairs; count of blanks; non-numeric text such as markers kept apart."""
    out, blanks, other = [], 0, Counter()
    for i, x in series.items():
        d = to_decimal(x)
        if d is not None:
            out.append((i, d))
        elif is_null(x):
            blanks += 1
        else:
            other[text(x)] += 1
    return out, blanks, other


def skipped_note(blanks: int, other: Counter) -> str | None:
    parts = []
    if blanks:
        parts.append(f"{blanks:,} blank")
    if other:
        ex = ", ".join(f"{k!r} x{v}" for k, v in other.most_common(4))
        parts.append(f"{sum(other.values()):,} non-numeric ({ex})")
    return "skipped: " + "; ".join(parts) if parts else None


def _frame(ctx, sid, table, where, *cols):
    need = [c for c in cols if c] + [w["column"] for w in where or [] if isinstance(w, dict)]
    flat = [x for c in need for x in (c if isinstance(c, list) else [c])]
    return apply_where(ctx.frame(sid, table, flat), where)


# ---------- ops ----------


@op("table_info", "row count, columns and CRS of one table", {**SRC}, ["source_id"])
def table_info(ctx: ExecContext, source_id, table=None):
    t = next((x for x in ctx.profile(source_id).tables if x.table_name == table), None)
    if t is None:
        raise OpError(f"{source_id} has no tables; it is a {ctx.profile(source_id).kind} source")
    sc = scope(ctx, source_id, table)
    loc = Locator(
        source_id=source_id,
        kind="file" if table == ctx.source(source_id).original_file else "layer",
        text=sc if table != ctx.source(source_id).original_file else "file",
    )
    facts = [
        count_fact("rows", t.n_rows, "records", f"{t.n_rows:,} records in {sc}", [loc]),
        str_fact("columns", ", ".join(c.name for c in t.columns), f"columns of {sc}", [loc]),
    ]
    if t.crs:
        facts.append(str_fact("crs", t.crs, f"{sc} geometry is in {t.crs}", [loc]))
    return ok("table_info", {}, facts)


@op(
    "get_cell",
    "one spreadsheet cell by A1 reference (stored or displayed per Context)",
    {
        "source_id": S,
        "sheet": S,
        "ref": S,
        "value_mode": {"type": "string", "enum": ["stored", "displayed"]},
    },
    ["source_id", "sheet", "ref"],
    group="table",
)
def op_get_cell(ctx: ExecContext, source_id, sheet, ref, value_mode=None):
    src = ctx.source(source_id)
    if ctx.profile(source_id).kind != "workbook":
        raise OpError(f"{source_id} is not a workbook")
    sheets = list(read_cells(src.path))
    if sheet not in sheets:
        low = {s.lower(): s for s in sheets}
        if sheet.lower() not in low:
            raise OpError(f"no sheet {sheet!r}; sheets: {sheets}")
        sheet = low[sheet.lower()]
    try:
        parse_ref(ref)
    except Exception as e:
        raise OpError(f"bad cell reference {ref!r}; use A1 style such as H23") from e
    c = get_cell(src.path, sheet, ref)
    mode = value_mode or ctx.conventions.value_mode
    v = cell_value(c, "displayed" if mode == "displayed" else "stored")
    loc = Locator(source_id=source_id, kind="cell", text=cell_locator_text(sheet, c.ref))
    note = f"stored {c.stored!r}, displayed {c.displayed!r}" if c.stored != c.displayed else None
    shown = c.displayed if mode == "displayed" and c.displayed is not None else text(v)
    d = to_decimal(v) if mode != "displayed" and isinstance(v, int | float) else None
    if d is not None:
        shown = display(d, ctx.conventions)  # stored value, rounded once for reporting
    if v is not None and not isinstance(v, int | float | str | bool):
        v = str(v)
    f = Fact(
        name="value",
        value=v,
        display=shown or "(blank)",
        claim=f"{src.original_file} sheet {sheet} cell {c.ref} is {shown or 'blank'}",
        locators=[loc],
        note=note,
    )
    return ok(
        "get_cell", {}, [f], f"{sheet}!{c.ref}: stored={c.stored!r} displayed={c.displayed!r}"
    )


@op(
    "get_range",
    "spreadsheet cells in an A1 range such as B4:D9",
    {"source_id": S, "sheet": S, "range": S},
    ["source_id", "sheet", "range"],
)
def get_range(ctx: ExecContext, source_id, sheet, range):  # noqa: A002
    if ctx.profile(source_id).kind != "workbook":
        raise OpError(f"{source_id} is not a workbook; use table ops on its columns")
    a, _, b = range.partition(":")
    r1, c1 = parse_ref(a)
    r2, c2 = parse_ref(b or a)
    cells = [
        c
        for c in read_cells(ctx.source(source_id).path).get(sheet, [])
        if r1 <= c.row <= r2 and c1 <= c.col <= c2 and c.stored is not None
    ]
    mode = "displayed" if ctx.conventions.value_mode == "displayed" else "stored"
    facts = []
    for c in cells[: MAX_FACTS * 2]:
        v = cell_value(c, mode)
        loc = Locator(source_id=source_id, kind="cell", text=cell_locator_text(sheet, c.ref))
        facts.append(str_fact(c.ref, v, f"sheet {sheet} cell {c.ref} is {text(v)}", [loc]))
    prev = "\n".join(f"{c.ref}: {cell_value(c, mode)!r}" for c in cells[:60])
    return ok("get_range", {}, facts, prev)


@op(
    "find_cells",
    "find spreadsheet cells whose text contains a phrase; returns A1 refs",
    {"source_id": S, "text": S, "sheet": S},
    ["source_id", "text"],
)
def find_cells(ctx: ExecContext, source_id, text, sheet=None):  # noqa: F811 (arg name is the schema's)
    if ctx.profile(source_id).kind != "workbook":
        raise OpError(f"{source_id} is not a workbook; use table ops on its columns")
    q = str(val(text)).casefold()
    hits = []
    for sh, cells in read_cells(ctx.source(source_id).path).items():
        if sheet and sh != sheet:
            continue
        hits += [(sh, c) for c in cells if isinstance(c.stored, str) and q in c.stored.casefold()]
    facts = [
        str_fact(
            f"{sh}!{c.ref}",
            c.stored,
            f"sheet {sh} cell {c.ref} reads {c.stored!r}",
            [Locator(source_id=source_id, kind="cell", text=cell_locator_text(sh, c.ref))],
        )
        for sh, c in hits[:MAX_FACTS]
    ]
    prev = "\n".join(f"{sh}!{c.ref}: {c.stored[:80]}" for sh, c in hits[:40]) or "no match"
    return ok("find_cells", {}, facts, prev)


@op(
    "filter_rows",
    "rows matching conditions; count plus the listed columns of the first rows",
    {**SRC, "where": WHERE, "columns": ARR, "key_column": S, "limit": INT},
    ["source_id"],
)
def filter_rows(ctx, source_id, table=None, where=None, columns=None, key_column=None, limit=10):
    df = _frame(ctx, source_id, table, where, columns, key_column)
    sc = scope(ctx, source_id, table)
    facts = [
        count_fact(
            "count",
            len(df),
            "records",
            f"{len(df):,} records in {sc}{where_text(where)}",
            [Locator(source_id=source_id, kind="rows", text=f"{sc}{where_text(where)}")],
        )
    ]
    for idx, row in list(df.iterrows())[: min(limit, MAX_FACTS)]:
        for c in columns or []:
            loc = row_loc(ctx, source_id, table, idx, row, c, key_column)
            facts.append(
                str_fact(
                    f"{c}@{text(row.get(key_column, idx))}",
                    row[c],
                    f"{c} is {text(row[c]) or 'blank'} ({loc.text})",
                    [loc],
                )
            )
    return ok("filter_rows", {}, facts, preview_frame(df[columns] if columns else df, limit))


@op(
    "count_rows",
    "number of records, optionally under conditions (unique keys when Context asks)",
    {**SRC, "where": WHERE, "key_column": S},
    ["source_id"],
)
def count_rows(ctx, source_id, table=None, where=None, key_column=None):
    df = _frame(ctx, source_id, table, where, key_column)
    sc = scope(ctx, source_id, table)
    loc = Locator(source_id=source_id, kind="rows", text=f"{sc}{where_text(where)}")
    if key_column and ctx.conventions.counts_mean == "unique_keys":
        n = df[key_column].map(text).replace("", pd.NA).dropna().nunique()
        return ok(
            "count_rows",
            {},
            [
                count_fact(
                    "count",
                    n,
                    f"unique {key_column} values",
                    f"{n:,} unique {key_column} values in {sc}{where_text(where)}",
                    [loc],
                )
            ],
        )
    return ok(
        "count_rows",
        {},
        [
            count_fact(
                "count",
                len(df),
                "records",
                f"{len(df):,} records in {sc}{where_text(where)}",
                [loc],
            )
        ],
    )


@op(
    "count_distinct",
    "number of distinct non-blank values of one or more columns",
    {**SRC, "columns": ARR, "where": WHERE},
    ["source_id", "columns"],
)
def count_distinct(ctx, source_id, columns, table=None, where=None):
    df = _frame(ctx, source_id, table, where, columns)
    keys = df[columns].map(text)
    keys = keys[(keys != "").all(axis=1)]
    n = len(keys.drop_duplicates())
    sc = scope(ctx, source_id, table)
    locs = [column_loc(ctx, source_id, table, c) for c in columns]
    return ok(
        "count_distinct",
        {},
        [
            count_fact(
                "count",
                n,
                "distinct values",
                f"{n:,} distinct {' + '.join(columns)} values in {sc}{where_text(where)}",
                locs,
                note=f"{len(df) - len(keys):,} rows with a blank value excluded"
                if len(df) > len(keys)
                else None,
            )
        ],
    )


@op(
    "count_nulls",
    "blank values per column; publisher markers such as [z] counted separately",
    {**SRC, "columns": ARR, "where": WHERE},
    ["source_id", "columns"],
)
def count_nulls(ctx, source_id, columns, table=None, where=None):
    df = _frame(ctx, source_id, table, where, columns)
    sc = scope(ctx, source_id, table)
    facts, lines = [], []
    for c in columns:
        s = df[c]
        blanks = int(s.map(is_null).sum())
        markers = Counter(text(x) for x in s if is_marker(x))
        loc = column_loc(ctx, source_id, table, c)
        note = (
            "markers kept apart: " + ", ".join(f"{k} x{v}" for k, v in markers.items())
            if markers
            else None
        )
        facts.append(
            count_fact(
                f"{c}.blank",
                blanks,
                "records",
                f"{blanks:,} of {len(s):,} records in {sc} have no {c} value",
                [loc],
                note,
            )
        )
        if markers:
            facts.append(
                count_fact(
                    f"{c}.markers",
                    sum(markers.values()),
                    "records",
                    f"{sum(markers.values()):,} {c} values are publisher markers",
                    [loc],
                    note,
                )
            )
        lines.append(f"{c}: {blanks} blank of {len(s)}; markers {dict(markers)}")
    facts.append(
        count_fact(
            "total",
            len(df),
            "records",
            f"{len(df):,} records in {sc}{where_text(where)}",
            [Locator(source_id=source_id, kind="rows", text=sc)],
        )
    )
    return ok("count_nulls", {}, facts, "\n".join(lines))


AGG = ["sum", "mean", "min", "max", "median", "count"]


def _agg(pairs: list[tuple[object, Decimal]], func: str) -> tuple[Decimal, object | None]:
    if not pairs:
        raise OpError("no numeric values to aggregate")
    vals = [d for _, d in pairs]
    if func == "sum":
        return sum(vals, Decimal(0)), None
    if func == "mean":
        return sum(vals, Decimal(0)) / Decimal(len(vals)), None
    if func == "count":
        return Decimal(len(vals)), None
    if func in ("min", "max"):
        i, d = (min if func == "min" else max)(pairs, key=lambda p: p[1])
        return d, i
    s = sorted(vals)
    m = len(s) // 2
    return (s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2), None


@op(
    "aggregate",
    "sum, mean, min, max, median or count of a numeric column",
    {
        **SRC,
        "column": S,
        "func": {"type": "string", "enum": AGG},
        "where": WHERE,
        "unit": S,
        "key_column": S,
    },
    ["source_id", "column", "func"],
)
def aggregate(ctx, source_id, column, func, table=None, where=None, unit=None, key_column=None):
    df = _frame(ctx, source_id, table, where, column, key_column)
    pairs, blanks, other = numbers(df[column])
    d, at = _agg(pairs, func)
    sc = scope(ctx, source_id, table)
    locs = [column_loc(ctx, source_id, table, column)]
    if at is not None:
        locs = [row_loc(ctx, source_id, table, at, df.loc[at], column, key_column)]
    claim = f"{func} of {column} over {len(pairs):,} records in {sc}{where_text(where)} is "
    f = num_fact(
        func,
        d,
        ctx,
        unit,
        claim,
        locs,
        skipped_note(blanks, other),
        decimals=0 if func == "count" else None,
    )
    f.claim += f.display
    facts = [
        f,
        count_fact("n", len(pairs), "records", f"{len(pairs):,} numeric {column} values", locs[:1]),
    ]
    if at is not None and key_column:
        facts.append(
            str_fact(
                "at",
                df.loc[at, key_column],
                f"{func} {column} is at {key_column}={text(df.loc[at, key_column])}",
                locs,
            )
        )
    return ok("aggregate", {}, facts)


@op(
    "group_aggregate",
    "aggregate a numeric column (or count rows) per group",
    {
        **SRC,
        "group_by": ARR,
        "column": S,
        "func": {"type": "string", "enum": AGG},
        "where": WHERE,
        "order": {"type": "string", "enum": ["desc", "asc"]},
        "limit": INT,
        "unit": S,
    },
    ["source_id", "group_by", "func"],
)
def group_aggregate(
    ctx,
    source_id,
    group_by,
    func,
    column=None,
    table=None,
    where=None,
    order="desc",
    limit=10,
    unit=None,
):
    df = _frame(ctx, source_id, table, where, group_by, column)
    rows = []
    for key, g in df.groupby([df[c].map(text) for c in group_by], sort=False):
        key = key if isinstance(key, tuple) else (key,)
        if func == "count" and not column:
            rows.append((key, Decimal(len(g)), len(g)))
            continue
        pairs, _, _ = numbers(g[column])
        if pairs:
            rows.append((key, _agg(pairs, func)[0], len(pairs)))
    rows.sort(key=lambda r: r[1], reverse=order == "desc")
    sc = scope(ctx, source_id, table)
    locs = [
        column_loc(ctx, source_id, table, c) for c in [*group_by, *([column] if column else [])]
    ]
    facts = []
    for key, d, n in rows[: min(limit, MAX_FACTS)]:
        label = " / ".join(k or "(blank)" for k in key)
        what = f"{func} of {column}" if column else "records"
        f = num_fact(
            label,
            d,
            ctx,
            unit if column else "records",
            "",
            locs,
            decimals=0 if func == "count" else None,
        )
        f.claim = f"{label}: {what} = {f.display} in {sc}{where_text(where)} ({n:,} rows)"
        facts.append(f)
    prev = "\n".join(f"{' / '.join(k)}: {d}" for k, d, _ in rows[:40])
    facts.append(
        count_fact("groups", len(rows), "groups", f"{len(rows):,} groups in {sc}", locs[:1])
    )
    return ok("group_aggregate", {}, facts, prev)


@op(
    "top_n",
    "rows with the largest (or smallest) values of a numeric column",
    {**SRC, "column": S, "n": INT, "ascending": B, "where": WHERE, "key_column": S, "unit": S},
    ["source_id", "column"],
)
def top_n(
    ctx, source_id, column, table=None, n=5, ascending=False, where=None, key_column=None, unit=None
):
    df = _frame(ctx, source_id, table, where, column, key_column)
    pairs, blanks, other = numbers(df[column])
    pairs.sort(key=lambda p: p[1], reverse=not ascending)
    facts = []
    for i, d in pairs[: min(n, MAX_FACTS)]:
        loc = row_loc(ctx, source_id, table, i, df.loc[i], column, key_column)
        label = text(df.loc[i, key_column]) if key_column else str(i)
        f = num_fact(label, d, ctx, unit, "", [loc], decimals=None)
        f.claim = f"{column} is {f.display} at {loc.text}"
        facts.append(f)
    return ok("top_n", {}, facts, "\n".join(f.claim for f in facts))


@op(
    "lookup_row",
    "the row(s) where key_column equals a value; returns the listed columns",
    {**SRC, "key_column": S, "value": {}, "columns": ARR},
    ["source_id", "key_column", "value"],
)
def lookup_row(ctx, source_id, key_column, value, table=None, columns=None):
    v = val(value)
    df = _frame(
        ctx, source_id, table, [{"column": key_column, "op": "=", "value": v}], key_column, columns
    )
    if df.empty:
        sample = ", ".join(ctx.frame(source_id, table, [key_column])[key_column].map(text).head(5))
        raise OpError(f"no row with {key_column} = {v!r}; example values: {sample}")
    cols = columns or [c for c in df.columns if c not in ("geometry", key_column)][:12]
    facts = []
    for idx, row in list(df.iterrows())[:3]:
        for c in cols:
            loc = row_loc(ctx, source_id, table, idx, row, c, key_column)
            facts.append(
                str_fact(
                    c, row[c], f"{key_column}={text(v)} has {c} {text(row[c]) or 'blank'}", [loc]
                )
            )
    note = f"{len(df)} rows match" if len(df) > 1 else None
    if note:
        facts.insert(
            0,
            count_fact(
                "matches",
                len(df),
                "records",
                f"{len(df)} rows have {key_column}={v}",
                [column_loc(ctx, source_id, table, key_column)],
                note,
            ),
        )
    return ok("lookup_row", {}, facts, preview_frame(df[[key_column, *cols]], 5))


@op(
    "value_counts",
    "how often each value of a column occurs, with shares of the total",
    {**SRC, "column": S, "where": WHERE, "top": INT, "include_blank": B},
    ["source_id", "column"],
)
def value_counts(ctx, source_id, column, table=None, where=None, top=10, include_blank=False):
    df = _frame(ctx, source_id, table, where, column)
    s = df[column].map(text)
    if not include_blank:
        s = s[s != ""]
    vc = s.value_counts()
    total = int(vc.sum())
    sc = scope(ctx, source_id, table)
    loc = column_loc(ctx, source_id, table, column)
    facts = [
        count_fact(
            "total",
            total,
            "records",
            f"{total:,} records with a {column} value in {sc}{where_text(where)}",
            [loc],
        )
    ]
    for k, n in list(vc.items())[: min(top, MAX_FACTS)]:
        f = count_fact(k or "(blank)", int(n), "records", "", [loc])
        share = display(Decimal(int(n)) * 100 / Decimal(total), ctx.conventions, "%")
        f.note = f"share {share}"
        f.claim = f"{column} = {k or '(blank)'}: {int(n):,} of {total:,} records ({share}) in {sc}"
        facts.append(f)
    facts.append(
        count_fact(
            "distinct",
            len(vc),
            "distinct values",
            f"{len(vc):,} distinct {column} values in {sc}",
            [loc],
        )
    )
    return ok("value_counts", {}, facts, vc.head(40).to_string())


@op(
    "duplicates",
    "duplicated values of key column(s): how many keys repeat and example keys",
    {**SRC, "columns": ARR, "where": WHERE},
    ["source_id", "columns"],
)
def duplicates(ctx, source_id, columns, table=None, where=None):
    df = _frame(ctx, source_id, table, where, columns)
    keys = df[columns].map(text).agg(" | ".join, axis=1)
    keys = keys[keys.str.strip(" |") != ""]
    vc = keys.value_counts()
    dup = vc[vc > 1]
    sc = scope(ctx, source_id, table)
    locs = [column_loc(ctx, source_id, table, c) for c in columns]
    facts = [
        count_fact(
            "duplicate_keys",
            len(dup),
            "values",
            f"{len(dup):,} {'+'.join(columns)} values occur more than once in {sc}",
            locs,
        ),
        count_fact(
            "rows_in_duplicates",
            int(dup.sum()),
            "records",
            f"{int(dup.sum()):,} records carry a repeated {'+'.join(columns)} in {sc}",
            locs,
        ),
    ]
    if len(dup):
        facts.append(
            str_fact("examples", ", ".join(dup.index[:5]), f"repeated values in {sc}", locs)
        )
    return ok("duplicates", {}, facts, dup.head(20).to_string() if len(dup) else "no duplicates")


PAIR = {"source_a": S, "table_a": S, "column_a": S, "source_b": S, "table_b": S, "column_b": S}


def _keys(ctx, sid, table, col) -> pd.Series:
    s = ctx.frame(sid, table, [col])[col].map(text)
    return s[s != ""]


@op(
    "set_compare",
    "compare key sets of two tables: keys only in A, only in B, duplicates in each",
    PAIR,
    ["source_a", "column_a", "source_b", "column_b"],
)
def set_compare(ctx, source_a, column_a, source_b, column_b, table_a=None, table_b=None):
    a, b = _keys(ctx, source_a, table_a, column_a), _keys(ctx, source_b, table_b, column_b)
    sa, sb = set(a), set(b)
    la, lb = scope(ctx, source_a, table_a), scope(ctx, source_b, table_b)
    loc_a, loc_b = (
        column_loc(ctx, source_a, table_a, column_a),
        column_loc(ctx, source_b, table_b, column_b),
    )
    da, db = a[a.duplicated()].unique(), b[b.duplicated()].unique()
    only_a, only_b = sorted(sa - sb), sorted(sb - sa)

    def ex(xs):
        return f"e.g. {', '.join(xs[:5])}" if xs else None

    facts = [
        count_fact(
            "only_in_a",
            len(only_a),
            "values",
            f"{len(only_a):,} {column_a} values appear in {la} but not in {lb}",
            [loc_a, loc_b],
            ex(only_a),
        ),
        count_fact(
            "only_in_b",
            len(only_b),
            "values",
            f"{len(only_b):,} {column_b} values appear in {lb} but not in {la}",
            [loc_b, loc_a],
            ex(only_b),
        ),
        count_fact(
            "duplicates_in_a",
            len(da),
            "values",
            f"{len(da):,} {column_a} values repeat in {la}",
            [loc_a],
            ex(list(da)),
        ),
        count_fact(
            "duplicates_in_b",
            len(db),
            "values",
            f"{len(db):,} {column_b} values repeat in {lb}",
            [loc_b],
            ex(list(db)),
        ),
        count_fact(
            "common",
            len(sa & sb),
            "values",
            f"{len(sa & sb):,} values appear in both files",
            [loc_a, loc_b],
        ),
        count_fact(
            "rows_a", len(a), "records", f"{len(a):,} non-blank {column_a} values in {la}", [loc_a]
        ),
        count_fact(
            "rows_b", len(b), "records", f"{len(b):,} non-blank {column_b} values in {lb}", [loc_b]
        ),
    ]
    prev = (
        f"A={la}.{column_a}: {len(a)} values, {len(sa)} distinct\n"
        f"B={lb}.{column_b}: {len(b)} values, {len(sb)} distinct\n"
        f"only A: {only_a[:10]}\nonly B: {only_b[:10]}\n"
        f"dup A: {list(da[:10])}\ndup B: {list(db[:10])}"
    )
    return ok("set_compare", {}, facts, prev)


@op(
    "join_check",
    "row-level join of two tables on keys: matched and unmatched rows, cardinality",
    PAIR,
    ["source_a", "column_a", "source_b", "column_b"],
)
def join_check(ctx, source_a, column_a, source_b, column_b, table_a=None, table_b=None):
    a, b = _keys(ctx, source_a, table_a, column_a), _keys(ctx, source_b, table_b, column_b)
    sb, sa = set(b), set(a)
    ma, mb = int(a.isin(sb).sum()), int(b.isin(sa).sum())
    ca, cb = a.value_counts(), b.value_counts()
    card = f"{'1' if ca.max() <= 1 else 'many'}-to-{'1' if cb.max() <= 1 else 'many'}"
    la, lb = scope(ctx, source_a, table_a), scope(ctx, source_b, table_b)
    locs = [
        column_loc(ctx, source_a, table_a, column_a),
        column_loc(ctx, source_b, table_b, column_b),
    ]
    facts = [
        count_fact(
            "matched_a", ma, "records", f"{ma:,} of {len(a):,} {la} records match {lb}", locs
        ),
        count_fact(
            "unmatched_a",
            len(a) - ma,
            "records",
            f"{len(a) - ma:,} {la} records have no match",
            locs,
        ),
        count_fact(
            "matched_b", mb, "records", f"{mb:,} of {len(b):,} {lb} records match {la}", locs
        ),
        count_fact(
            "unmatched_b",
            len(b) - mb,
            "records",
            f"{len(b) - mb:,} {lb} records have no match",
            locs,
        ),
        str_fact("cardinality", card, f"the {column_a}/{column_b} join is {card}", locs),
    ]
    return ok("join_check", {}, facts)


@op(
    "row_wise_max",
    "largest per-row total across columns (simultaneous peak) vs sum of separate maxima",
    {**SRC, "columns": ARR, "where": WHERE, "label_column": S, "unit": S},
    ["source_id", "columns"],
)
def row_wise_max(ctx, source_id, columns, table=None, where=None, label_column=None, unit=None):
    df = _frame(ctx, source_id, table, where, columns, label_column)
    best, best_i, maxima = None, None, {}
    for c in columns:
        pairs, _, _ = numbers(df[c])
        if not pairs:
            raise OpError(f"column {c} has no numeric values")
        maxima[c] = max(pairs, key=lambda p: p[1])
    for i, row in df.iterrows():
        ds = [to_decimal(row[c]) for c in columns]
        if any(d is None for d in ds):
            continue
        tot = sum(ds, Decimal(0))
        if best is None or tot > best:
            best, best_i = tot, i
    if best is None:
        raise OpError("no row has numeric values in every column")
    sc = scope(ctx, source_id, table)
    label = text(df.loc[best_i, label_column]) if label_column else str(best_i)
    locs = [
        row_loc(ctx, source_id, table, best_i, df.loc[best_i], c, label_column) for c in columns
    ]
    f = num_fact("max_row_total", best, ctx, unit, "", locs)
    f.claim = f"highest simultaneous {' + '.join(columns)} total is {f.display} at {label} in {sc}"
    sep = sum((d for _, d in maxima.values()), Decimal(0))
    facts = [f, str_fact("at", label, f"the simultaneous peak is at {label}", locs[:1])]
    g = num_fact(
        "sum_of_maxima",
        sep,
        ctx,
        unit,
        "",
        [
            row_loc(ctx, source_id, table, i, df.loc[i], c, label_column)
            for c, (i, _) in maxima.items()
        ],
    )
    g.claim = f"sum of the separate maxima of {', '.join(columns)} is {g.display}"
    facts.append(g)
    for c, (i, d) in maxima.items():
        h = num_fact(
            f"max.{c}",
            d,
            ctx,
            unit,
            "",
            [row_loc(ctx, source_id, table, i, df.loc[i], c, label_column)],
        )
        h.claim = f"maximum {c} is {h.display}"
        facts.append(h)
    return ok("row_wise_max", {}, facts)


_SQL_BAD = re.compile(
    r";|\b(attach|copy|install|load|pragma|export|import|create|insert|update|delete|drop|alter|call|set|"
    r"read_\w+|glob|sqlite_\w+|parquet_\w+|httpfs)\b",
    re.I,
)
_SQL_TABLE = re.compile(r"\bsrc_([0-9a-f]{12})(?:__(\w+))?\b", re.I)


def check_sql(query: str) -> str:
    q = query.strip().rstrip(";").strip()
    if not re.match(r"^(select|with)\b", q, re.I):
        raise OpError("sql accepts a single SELECT statement only")
    m = _SQL_BAD.search(q)
    if m:
        raise OpError(f"sql: {m.group(0)!r} is not allowed; a single read-only SELECT only")
    return q


@op(
    "sql",
    "last resort: one read-only SELECT over tables named src_<12 hex id> (or src_<id>__<table>)",
    {"query": S},
    ["query"],
)
def sql(ctx: ExecContext, query):
    q = check_sql(query)
    con = duckdb.connect(":memory:")
    try:
        for m in _SQL_TABLE.finditer(q):
            sid = f"SRC_{m.group(1).lower()}"
            ctx.source(sid)
            tname = None
            if m.group(2):
                names = [t.table_name for t in ctx.profile(sid).tables]
                tname = next(
                    (n for n in names if re.sub(r"\W", "_", n).lower() == m.group(2).lower()), None
                )
                if tname is None:
                    raise OpError(f"sql: no table {m.group(2)!r} in {sid}; tables: {names}")
            df = ctx.frame(sid, tname).drop(columns=["geometry"], errors="ignore")
            con.register(m.group(0), pd.DataFrame(df).reset_index())
        con.execute("SET enable_external_access = false")
        out = con.execute(q).df()
    except duckdb.Error as e:
        raise OpError(f"sql error: {e}") from e
    finally:
        con.close()
    sids = sorted({f"SRC_{m.group(1).lower()}" for m in _SQL_TABLE.finditer(q)})
    loc = [Locator(source_id=s, kind="sql", text=q[:200]) for s in sids]
    facts = []
    if len(out) == 1:
        for c in out.columns[:MAX_FACTS]:
            v = out.iloc[0][c]
            facts.append(
                str_fact(
                    str(c),
                    v.item() if hasattr(v, "item") else v,
                    f"{c} = {text(v)} ({q[:80]})",
                    loc,
                )
            )
    facts.append(count_fact("rows", len(out), "rows", f"the query returns {len(out):,} rows", loc))
    return ok("sql", {}, facts, out.head(20).to_string())


TOTAL = re.compile(r"\b(total|totals|yhteensä|summa|insgesamt|gesamt|totaal|totale|all)\b", re.I)


def _is_heading(row_cells: list) -> bool:
    """A row whose cells after the label are all text or years is a header, not data."""
    filled = sorted((c for c in row_cells if c.stored not in (None, "")), key=lambda c: c.col)
    rest = filled[1:]
    year = re.compile(r"^(1[89]|20)\d\d$")
    return len(rest) >= 2 and all(isinstance(c.stored, str) or year.match(str(c.stored))
                                  for c in rest)  # fmt: skip


def _label(cells_by_row: dict[int, list], r: int) -> str:
    for c in cells_by_row.get(r, []):
        if c.col <= 2 and c.displayed not in (None, ""):
            return str(c.displayed)
    return ""


def _match(want: str, have: str) -> int:
    """2 = same after normalising, 1 = one contains the other, 0 = no match."""
    from municipal_qa.tools.registry import loose_name

    a, b = loose_name(want), loose_name(have)
    if not a or not b:
        return 0
    return 2 if a == b else 1 if (a in b or b in a) and min(len(a), len(b)) >= 3 else 0


@op("cell_lookup", "spreadsheet cell by row label and column header (block = section heading)",
    {"source_id": S, "sheet": S, "row": S, "column": S, "block": S,
     "value_mode": {"type": "string", "enum": ["stored", "displayed"]}},
    ["source_id", "sheet", "row", "column"])  # fmt: skip
def cell_lookup(ctx, source_id, sheet, row, column, block=None, value_mode=None):
    src = ctx.source(source_id)
    if ctx.profile(source_id).kind != "workbook":
        return _label_lookup(ctx, source_id, row, column)
    sheets = read_cells(src.path)
    if sheet not in sheets:
        low = {s.lower(): s for s in sheets}
        if str(sheet).lower() not in low:
            raise OpError(f"no sheet {sheet!r}; sheets: {list(sheets)}")
        sheet = low[str(sheet).lower()]
    cells = sheets[sheet]
    by_row: dict[int, list] = {}
    for c in cells:
        by_row.setdefault(c.row, []).append(c)
    for r in by_row.values():
        r.sort(key=lambda c: c.col)
    labels = {r: _label(by_row, r) for r in sorted(by_row)}
    notes = []

    def row_hit(x):
        return any(_match(str(val(x)), lab) for lab in labels.values())

    if not row_hit(row) and row_hit(column):
        row, column = column, row
        notes.append("row and column labels swapped to match the sheet")
    if block and not any(_match(str(val(block)), lab) for lab in labels.values()):
        notes.append(f"block {block!r} not found in the sheet; ignored")
        block = None
    rows = [(r, _match(str(val(row)), lab)) for r, lab in labels.items()]
    best = max((s for _, s in rows), default=0)
    cand = [r for r, s in rows if s == best and best]
    also: list[int] = []
    if block:
        heads = [h for h, lab in labels.items() if _match(str(val(block)), lab) == 2] or [
            h for h, lab in labels.items() if _match(str(val(block)), lab)
        ]
        firsts = []
        for h in heads:
            after = [r for r in cand if r > h]
            if after and min(after) not in firsts:
                firsts.append(min(after))
        cand, also = firsts[:1], firsts[1:]
    if len(cand) > 1 and not block:
        notes.append(f"{len(cand)} rows match {row!r} (rows {cand[:8]}); the first is used")
        cand = cand[:1]
    if len(cand) != 1:
        shown = "; ".join(f"row {r}: {lab}" for r, lab in labels.items() if lab)[:900]
        why = (
            "no row matches"
            if not cand
            else f"{len(cand)} rows match (rows {cand[:8]}); give block"
        )
        raise OpError(f"cell_lookup: {why} {row!r} in sheet {sheet}. Row labels: {shown}")
    r = cand[0]
    first = min((c.col for c in by_row.get(r, []) if c.displayed not in (None, "")), default=1)
    heads = [c for c in cells if c.row < r and c.col > first
             and _match(str(val(column)), str(c.displayed or ""))]  # fmt: skip
    if not heads:
        hdr = sorted({str(c.displayed) for c in cells if c.row < r and isinstance(c.stored, str)})[
            :40
        ]
        raise OpError(f"cell_lookup: no column header {column!r} above row {r}; headers: {hdr}")
    top = max(_match(str(val(column)), str(c.displayed)) for c in heads)
    head = max((c for c in heads if _match(str(val(column)), str(c.displayed)) == top),
               key=lambda c: c.row)  # fmt: skip
    ref = f"{col_letter(head.col)}{r}"
    if head.row == r or _is_heading(by_row.get(r, [])):
        nxt = sorted(rr for rr in labels if rr > r and _is_heading(by_row.get(rr, [])))
        end = nxt[0] if nxt else r + 30
        totals = [rr for rr in labels if r < rr < end and TOTAL.search(labels[rr])]
        if totals:
            notes.append(f"{labels[r]!r} is a block heading; its total row {labels[totals[0]]!r} "
                         f"is used")  # fmt: skip
            r = totals[0]
            ref = f"{col_letter(head.col)}{r}"
    if head.row == r or _is_heading(by_row.get(r, [])):
        below = [lab for rr, lab in labels.items() if r < rr <= r + 15 and lab][:15]
        raise OpError(f"cell_lookup: row {r} ({labels[r]!r}) is a heading, not data; use block="
                      f"{labels[r]!r} with one of these rows: {below}")  # fmt: skip
    res = op_get_cell(ctx, source_id, sheet, ref, value_mode)
    f = res.facts[0]
    if also:
        notes.append(f"later blocks also match rows {also[:5]}")
    if notes:
        f.note = "; ".join(x for x in [f.note, *notes] if x)
    where = f"{labels[r]} / {head.displayed}"
    f.claim = f"{src.original_file} sheet {sheet} cell {ref} ({where}) is {f.display}"
    res.op = "cell_lookup"
    res.preview = f"{sheet}!{ref}: row {r} '{labels[r]}', column '{head.displayed}'; " + (
        res.preview or ""
    )
    return res


def _label_lookup(ctx, source_id, row, column):
    """cell_lookup on a plain table: one label names a column, the other a value in some row."""
    from municipal_qa.tools.registry import loose_name

    tname = ctx.table_name(source_id, None)
    df = ctx.frame(source_id, tname)
    cols = {loose_name(c): c for c in df.columns}
    for col_label, key in ((column, row), (row, column)):
        c = cols.get(loose_name(str(val(col_label))))
        if c is None:
            continue
        want = text(val(key))
        for kc in df.columns:
            hit = df[df[kc].map(text) == want]
            if len(hit):
                idx, r = next(iter(hit.iterrows()))
                loc = row_loc(ctx, source_id, tname, idx, r, c, kc)
                f = str_fact("value", r[c], f"{kc}={want} has {c} {text(r[c]) or 'blank'}", [loc])
                return ok("cell_lookup", {}, [f], f"{kc}={want}: {c}={r[c]!r}")
    raise OpError(f"{source_id} is a table, not a workbook; use lookup_row with key_column and "
                  f"value; columns: {list(df.columns)[:30]}")  # fmt: skip
