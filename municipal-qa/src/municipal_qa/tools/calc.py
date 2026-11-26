"""Calc ops (plan §6.5, card S4): Decimal arithmetic over facts, rounded once for display."""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal, DivisionByZero, InvalidOperation

from dateutil import parser as dateparser

from municipal_qa.schema import Fact, Locator
from municipal_qa.tools.numbers import (
    decimals_for,
    display,
    plain_value,
    round_dec,
    rounding_mode,
    to_decimal,
)
from municipal_qa.tools.registry import ExecContext, OpError, op, resolve
from municipal_qa.tools.table_ops import ok

ANY = {}
S = {"type": "string"}
CMP = ["<", "<=", ">", ">=", "=", "!="]
FT_US = Decimal(1200) / Decimal(3937)
UNITS = {
    "length": {"m": 1, "km": 1000, "cm": "0.01", "mm": "0.001", "ft": "0.3048", "ftUS": FT_US,
               "mi": "1609.344", "yd": "0.9144"},
    "area": {"m²": 1, "ha": 10000, "km²": 1000000, "ft²": "0.09290304", "ftUS²": FT_US * FT_US,
             "acre": "4046.8564224"},
    "energy": {"Wh": 1, "kWh": 1000, "MWh": 10**6, "GWh": 10**9, "TWh": 10**12,
               "J": Decimal(1) / 3600, "kJ": Decimal(1000) / 3600, "MJ": Decimal(10**6) / 3600,
               "GJ": Decimal(10**9) / 3600},
    "power": {"W": 1, "kW": 1000, "MW": 10**6, "GW": 10**9},
    "time": {"s": 1, "min": 60, "h": 3600, "day": 86400},
    "mass": {"g": "0.001", "kg": 1, "t": 1000},
}  # fmt: skip
ALIASES = {"m2": "m²", "km2": "km²", "sqm": "m²", "hectare": "ha", "hectares": "ha", "ft2": "ft²",
           "us_ft": "ftUS", "us survey foot": "ftUS", "feet": "ft", "metre": "m", "meter": "m",
           "metres": "m", "meters": "m", "days": "day", "hours": "h", "minutes": "min"}  # fmt: skip


def _num(v, what: str) -> tuple[Decimal, Fact | None]:
    if isinstance(v, Fact):
        d = to_decimal(v.value)
        if d is None:
            d = to_decimal(v.display)
        if d is None:
            raise OpError(f"{what}: fact {v.name!r} is not a number ({v.display!r})")
        return d, v
    d = to_decimal(v)
    if d is None:
        raise OpError(f"{what}: {v!r} is not a number")
    return d, None


def _locs(*facts: Fact | None) -> list[Locator]:
    seen, out = set(), []
    for f in facts:
        for loc in f.locators if f else []:
            if (loc.source_id, loc.text) not in seen:
                seen.add((loc.source_id, loc.text))
                out.append(loc)
    return out


def _shown(d: Decimal, f: Fact | None) -> str:
    return f.display if f else f"{d.normalize():f}"


def _fact(ctx, name, d, unit, claim, inputs, decimals=None, note=None) -> Fact:
    f = Fact(
        name=name,
        value=plain_value(d),
        unit=unit,
        display=display(d, ctx.conventions, unit, decimals),
        claim="",
        locators=_locs(*inputs),
        note=note,
    )
    f.claim = claim.replace("{}", f.display)
    return f


def _div(a: Decimal, b: Decimal, what: str) -> Decimal:
    try:
        return a / b
    except (DivisionByZero, InvalidOperation) as e:
        raise OpError(f"{what}: division by zero") from e


_REFS = re.compile(r"\$[A-Za-z_][\w.]*")
_TOK = re.compile(r"\s*(\$[A-Za-z_][\w.]*|\d+\.?\d*(?:[eE][-+]?\d+)?|\.\d+|[-+*/()])")


def evaluate(expr: str, ctx: ExecContext) -> tuple[Decimal, list[Fact]]:
    """`+ - * / ( )` over numbers and `$step` / `$step.fact` references, in Decimal."""
    toks, pos, used = [], 0, []
    expr = expr.strip()
    while pos < len(expr):
        m = _TOK.match(expr, pos)
        if not m:
            raise OpError(
                f"calc: cannot read {expr[pos : pos + 12]!r}; use numbers, $refs and + - * / ( )"
            )
        toks.append(m.group(1))
        pos = m.end()
    i = 0

    def peek():
        return toks[i] if i < len(toks) else None

    def take():
        nonlocal i
        i += 1
        return toks[i - 1]

    def atom() -> Decimal:
        t = take() if peek() else None
        if t is None:
            raise OpError("calc: expression ends early")
        if t == "(":
            v = add()
            if peek() != ")":
                raise OpError("calc: missing ')'")
            take()
            return v
        if t == "-":
            return -atom()
        if t == "+":
            return atom()
        if t.startswith("$"):
            f = resolve(t, ctx.env)
            d, _ = _num(f, t)
            used.append(f)
            return d
        d = to_decimal(t)
        if d is None:
            raise OpError(f"calc: unexpected {t!r}")
        return d

    def mul() -> Decimal:
        v = atom()
        while peek() in ("*", "/"):
            v = v * atom() if take() == "*" else _div(v, atom(), "calc")
        return v

    def add() -> Decimal:
        v = mul()
        while peek() in ("+", "-"):
            v = v + mul() if take() == "+" else v - mul()
        return v

    out = add()
    if peek() is not None:
        raise OpError(f"calc: unexpected {peek()!r}")
    return out, used


@op(
    "calc",
    "arithmetic over saved facts, e.g. ($a - $b) / $b * 100; Decimal, rounded once at the end",
    {"expression": S, "unit": S, "label": S},
    ["expression"],
    group="calc",
)
def calc(ctx: ExecContext, expression, unit=None, label=None):
    d, used = evaluate(expression, ctx)
    shown = _REFS.sub(lambda m: resolve(m.group(0), ctx.env).display, expression)
    what = f"{label}: {shown}" if label else shown
    return ok("calc", {}, [_fact(ctx, "value", d, unit, f"{what} = {{}}", used)])


@op(
    "percent_change",
    "percentage change from old to new: (new - old) / old x 100",
    {"old": ANY, "new": ANY, "label": S},
    ["old", "new"],
    group="calc",
)
def percent_change(ctx, old, new, label=None):
    (a, fa), (b, fb) = _num(old, "old"), _num(new, "new")
    pct = _div((b - a) * 100, a, "percent_change")
    diff = b - a
    unit = fa.unit if fa and fa.unit and fa.unit == (fb.unit if fb else None) else None
    what = f"{label} " if label else ""
    return ok(
        "percent_change",
        {},
        [
            _fact(
                ctx,
                "pct",
                pct,
                "%",
                f"{what}change from {_shown(a, fa)} to {_shown(b, fb)} is {{}}",
                [fa, fb],
            ),
            _fact(
                ctx,
                "diff",
                diff,
                unit,
                f"{what}difference {_shown(b, fb)} - {_shown(a, fa)} = {{}}",
                [fa, fb],
            ),
        ],
    )


@op(
    "share",
    "part as a percentage of total",
    {"part": ANY, "total": ANY, "label": S},
    ["part", "total"],
    group="calc",
)
def share(ctx, part, total, label=None):
    (a, fa), (b, fb) = _num(part, "part"), _num(total, "total")
    pct = _div(a * 100, b, "share")
    what = f"{label}: " if label else ""
    return ok(
        "share",
        {},
        [
            _fact(
                ctx, "pct", pct, "%", f"{what}{_shown(a, fa)} of {_shown(b, fb)} is {{}}", [fa, fb]
            )
        ],
    )


@op(
    "pp_difference",
    "percentage-point difference a - b between two percentages",
    {"a": ANY, "b": ANY, "label": S},
    ["a", "b"],
    group="calc",
)
def pp_difference(ctx, a, b, label=None):
    (x, fx), (y, fy) = _num(a, "a"), _num(b, "b")
    what = f"{label}: " if label else ""
    return ok(
        "pp_difference",
        {},
        [
            _fact(
                ctx,
                "pp",
                x - y,
                "pp",
                f"{what}{_shown(x, fx)} minus {_shown(y, fy)} is {{}}",
                [fx, fy],
            )
        ],
    )


@op(
    "ratio", "a divided by b", {"a": ANY, "b": ANY, "unit": S, "label": S}, ["a", "b"], group="calc"
)
def ratio(ctx, a, b, unit=None, label=None):
    (x, fx), (y, fy) = _num(a, "a"), _num(b, "b")
    what = f"{label}: " if label else ""
    return ok(
        "ratio",
        {},
        [
            _fact(
                ctx,
                "ratio",
                _div(x, y, "ratio"),
                unit,
                f"{what}{_shown(x, fx)} / {_shown(y, fy)} = {{}}",
                [fx, fy],
            )
        ],
    )


def _unit(u: str) -> tuple[str, Decimal]:
    u = ALIASES.get(u.strip(), ALIASES.get(u.strip().lower(), u.strip()))
    for dim, table in UNITS.items():
        if u in table:
            return dim, Decimal(str(table[u])) if not isinstance(table[u], Decimal) else table[u]
    known = ", ".join(k for t in UNITS.values() for k in t)
    raise OpError(f"unknown unit {u!r}; known: {known}")


def convert(d: Decimal, src: str, dst: str) -> Decimal:
    (d1, f1), (d2, f2) = _unit(src), _unit(dst)
    if d1 != d2:
        raise OpError(f"cannot convert {src} ({d1}) to {dst} ({d2})")
    return d * f1 / f2


@op(
    "convert_unit",
    "convert a value between units of length, area, energy, power, time or mass",
    {"value": ANY, "from_unit": S, "to_unit": S},
    ["value", "from_unit", "to_unit"],
    group="calc",
)
def convert_unit(ctx, value, from_unit, to_unit):
    d, f = _num(value, "value")
    to = ALIASES.get(to_unit.strip(), to_unit.strip())
    out = convert(d, from_unit, to_unit)
    return ok(
        "convert_unit",
        {},
        [_fact(ctx, "value", out, to, f"{_shown(d, f)} {from_unit} is {{}}", [f])],
    )


@op(
    "round_value",
    "round a value once, with the Context rounding mode unless one is given",
    {
        "value": ANY,
        "decimals": {"type": "integer"},
        "mode": {"type": "string", "enum": ["half_up", "half_even"]},
        "unit": S,
    },
    ["value"],
    group="calc",
)
def round_value(ctx, value, decimals=None, mode=None, unit=None):
    d, f = _num(value, "value")
    conv = ctx.conventions
    if mode:
        conv = conv.model_copy(update={"rounding": mode})
    u = unit or (f.unit if f else None)
    n = decimals if decimals is not None else decimals_for(conv, u)
    out = Fact(
        name="value",
        value=plain_value(round_dec(d, n, rounding_mode(conv))),
        unit=u,
        display=display(d, conv, u, n),
        claim="",
        locators=_locs(f),
    )
    out.claim = f"{_shown(d, f)} rounded to {n} decimals ({conv.rounding}) is {out.display}"
    return ok("round_value", {}, [out])


_DAYFIRST = re.compile(r"^\s*(\d{1,2})[/.](\d{1,2})[/.](\d{4})")


def parse_date(v) -> dt.date:
    s = str(v.value if isinstance(v, Fact) else v).strip()
    m = _DAYFIRST.match(s)
    try:
        if re.match(r"^\d{4}-\d{2}-\d{2}", s):
            return dt.date.fromisoformat(s[:10])
        return dateparser.parse(s, dayfirst=bool(m and int(m.group(2)) <= 12)).date()
    except (ValueError, OverflowError) as e:
        raise OpError(f"date_diff: cannot read date {s!r}") from e


@op(
    "date_diff",
    "days (or weeks, years) between two dates; inclusive_start/inclusive_end flags",
    {
        "start": ANY,
        "end": ANY,
        "unit": {"type": "string", "enum": ["days", "weeks", "years"]},
        "inclusive_start": {"type": "boolean"},
        "inclusive_end": {"type": "boolean"},
    },
    ["start", "end"],
    group="calc",
)
def date_diff(ctx, start, end, unit="days", inclusive_start=False, inclusive_end=True):
    a, b = parse_date(start), parse_date(end)
    days = (b - a).days + (1 if inclusive_start and inclusive_end else 0)
    days -= 1 if not inclusive_start and not inclusive_end else 0
    fa, fb = (x if isinstance(x, Fact) else None for x in (start, end))
    rule = (
        "start and end days counted"
        if inclusive_start and inclusive_end
        else "excluding the start day"
        if not inclusive_start and inclusive_end
        else "excluding the end day"
        if inclusive_start
        else "excluding both end days"
    )
    if unit == "days":
        n = Decimal(days)
        f = _fact(
            ctx,
            "days",
            n,
            "days",
            f"{{}} from {a.isoformat()} to {b.isoformat()}, {rule}",
            [fa, fb],
            decimals=0,
        )
    else:
        n = Decimal(days) / (Decimal(7) if unit == "weeks" else Decimal("365.25"))
        f = _fact(
            ctx, unit, n, unit, f"{{}} from {a.isoformat()} to {b.isoformat()}, {rule}", [fa, fb]
        )
    dates = [
        Fact(
            name=k,
            value=d.isoformat(),
            display=d.strftime("%-d %B %Y"),
            claim=f"{k} date {d}",
            locators=_locs(x),
        )
        for k, d, x in (("start", a, fa), ("end", b, fb))
    ]
    return ok("date_diff", {}, [f, *dates])


@op(
    "compare_values",
    "which of two values is larger, by how much and by what percentage",
    {"a": ANY, "b": ANY, "label_a": S, "label_b": S, "unit": S},
    ["a", "b"],
    group="calc",
)
def compare_values(ctx, a, b, label_a="A", label_b="B", unit=None):
    (x, fx), (y, fy) = _num(a, "a"), _num(b, "b")
    unit = unit or (fx.unit if fx else None)
    larger = label_a if x > y else label_b if y > x else "neither (equal)"
    facts = [
        Fact(
            name="larger",
            value=larger,
            display=larger,
            claim=f"{label_a} {_shown(x, fx)} vs {label_b} {_shown(y, fy)}: larger is {larger}",
            locators=_locs(fx, fy),
        ),
        _fact(
            ctx, "difference", abs(x - y), unit, f"{label_a} and {label_b} differ by {{}}", [fx, fy]
        ),
    ]
    small = min(x, y)
    if small:
        facts.append(
            _fact(
                ctx,
                "pct_larger",
                _div(abs(x - y) * 100, abs(small), "compare"),
                "%",
                "the larger value is {} above the smaller",
                [fx, fy],
            )
        )
    return ok("compare_values", {}, facts)


@op(
    "check_criteria",
    "test value(s) against a threshold with an operator; pass or fail each",
    {
        "value": ANY,
        "values": {"type": "array"},
        "operator": {"type": "string", "enum": CMP},
        "threshold": ANY,
        "label": S,
    },
    ["operator", "threshold"],
    group="calc",
)
def check_criteria(ctx, operator, threshold, value=None, values=None, label=None):
    t, ft = _num(threshold, "threshold")
    items = (values or []) + ([value] if value is not None else [])
    if not items:
        raise OpError("check_criteria: give value or values")
    facts = []
    for i, v in enumerate(items):
        d, fv = _num(v, "value")
        passed = {"<": d < t, "<=": d <= t, ">": d > t, ">=": d >= t, "=": d == t, "!=": d != t}[
            operator
        ]
        what = label or (fv.name if fv else f"value {i + 1}")
        facts.append(
            Fact(
                name=what if len(items) == 1 else f"{what}#{i + 1}",
                value=passed,
                display="pass" if passed else "fail",
                claim=f"{what} {_shown(d, fv)} {operator} {_shown(t, ft)}: "
                f"{'pass' if passed else 'fail'}",
                locators=_locs(fv, ft),
            )
        )
    return ok("check_criteria", {}, facts)
