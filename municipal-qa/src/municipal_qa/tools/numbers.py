"""Decimal parsing, rounding once at the end, and display strings under Conventions."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation, localcontext

from municipal_qa.schema import Conventions

MARKERS = ("[z]", "[x]", "[c]", "[u]", "[low]", "..", ":", "-", "–")
_THOUSANDS = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$")
_DEC_COMMA = re.compile(r"^-?\d+,\d+$")
_PLAIN = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$")
UNIT_ALIASES = {"hectares": "ha", "hectare": "ha", "percent": "%", "km2": "km²", "m2": "m²"}


def is_marker(v: object) -> bool:
    return isinstance(v, str) and v.strip() in MARKERS


def to_decimal(v: object) -> Decimal | None:
    """Exact Decimal of a number or numeric string; None for blanks, markers and text."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, Decimal):
        return v
    if isinstance(v, int):
        return Decimal(v)
    if isinstance(v, float):
        return None if v != v else Decimal(repr(v))
    s = str(v).strip().replace(" ", "").replace(" ", "")
    if s.endswith("%"):
        s = s[:-1]
    if _THOUSANDS.match(s):
        s = s.replace(",", "")
    elif _DEC_COMMA.match(s):
        s = s.replace(",", ".")
    if not _PLAIN.match(s):
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def rounding_mode(conv: Conventions | None) -> str:
    return ROUND_HALF_EVEN if conv and conv.rounding == "half_even" else ROUND_HALF_UP


def decimals_for(conv: Conventions | None, unit: str | None) -> int:
    if conv is None:
        return 2
    unit = UNIT_ALIASES.get(unit or "", unit)
    if unit and unit in conv.decimals_overrides:
        return conv.decimals_overrides[unit]
    return conv.decimals


def round_dec(d: Decimal, decimals: int, mode: str = ROUND_HALF_UP) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 60
        return d.quantize(Decimal(1).scaleb(-decimals), rounding=mode)


def fmt(d: Decimal, decimals: int, mode: str = ROUND_HALF_UP) -> str:
    return f"{round_dec(d, decimals, mode):,.{decimals}f}"


def with_unit(text: str, unit: str | None) -> str:
    if not unit:
        return text
    if unit == "%":
        return f"{text}%"
    return f"{text} {'percentage points' if unit == 'pp' else unit}"


def display(d: Decimal, conv: Conventions | None, unit: str | None = None, decimals=None) -> str:
    """Round once, per the unit's precision and the Context rounding mode."""
    n = decimals if decimals is not None else decimals_for(conv, unit)
    return with_unit(fmt(d, n, rounding_mode(conv)), unit)


def count_display(n: int, noun: str | None = None) -> str:
    return f"{n:,}" + (f" {noun}" if noun else "")


def plain_value(d: Decimal) -> int | float:
    return int(d) if d == d.to_integral_value() and abs(d) < 2**53 else float(d)
