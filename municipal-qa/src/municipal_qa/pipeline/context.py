"""Deterministic Context (and question text) → Conventions, per plan §6.3."""

from __future__ import annotations

import re
from typing import Any

from municipal_qa.schema import Conventions

CRS_ALIASES: dict[str, str] = {
    r"british national grid|\bOSGB ?36\b": "EPSG:27700",
    r"swiss lv ?95|\blv ?95\b|ch1903\+": "EPSG:2056",
    r"lambert[- ]?93": "EPSG:2154",
    r"\brd new\b|amersfoort": "EPSG:28992",
    r"etrs[- ]?tm35fin": "EPSG:3067",
    r"web mercator|pseudo[- ]mercator": "EPSG:3857",
    r"\bwgs ?(19)?84\b": "EPSG:4326",
}

_NUM_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}

# Subject words → decimals_overrides key; subjects not listed set the general `decimals`.
_SUBJECT_KEYS: list[tuple[str, str]] = [
    (r"hectares?|\bha\b", "ha"),
    (r"percentage[- ]points?|\bpp\b", "pp"),
    (r"percent(age)?s?|shares?|%", "%"),
    (r"\bkwh\b", "kWh"),
    (r"\bgwh\b", "GWh"),
    (r"\bmwh\b", "MWh"),
    (r"km²|km2|square kilomet", "km²"),
    (r"m²|m2\b|square met", "m²"),
    (r"m³|m3\b|cubic met", "m³"),
    (r"minutes?", "min"),
    (r"distances?", "distance"),
    (r"areas?", "area"),
    (r"volumes?", "volume"),
    (r"counts?", "count"),
    (r"ndvi", "ndvi"),
    (r"costs?", "cost"),
]
_GENERAL_SUBJECT = re.compile(r"figures|results|values|numbers|derived|calculat|answers?", re.I)

_N = r"(?P<n>\d+|zero|one|two|three|four|five|six|seven|eight|nine|ten)"
_DECIMALS = re.compile(
    r"(?P<subj>[A-Za-z²³%\-/ ]{0,60}?)\s*(?:rounded\s+|reported\s+)?to\s+"
    + _N
    + r"\s+decimal(?:s| places?)?",
    re.I,
)
_STEP = re.compile(
    r"(?P<subj>[A-Za-z²³%\-/ ]{0,60}?)\s*to\s+(?P<step>0\.0*1|1)\s+"
    r"(?P<unit>percentage[- ]points?|[^\s,;.]+)",
    re.I,
)
_INTEGERS = re.compile(r"(?P<subj>[A-Za-z ]{0,40}?)\s*to\s+(?:whole\s+)?integers?", re.I)
_EPSG = re.compile(r"\bEPSG[: ]?(\d{4,6})\b", re.I)


def _num(tok: str) -> int:
    return int(tok) if tok.isdigit() else _NUM_WORDS[tok.lower()]


def _subject_keys(subj: str) -> list[str]:
    keys = []
    rest = subj
    for pat, key in _SUBJECT_KEYS:
        if re.search(pat, rest, re.I):
            keys.append(key)
            rest = re.sub(pat, " ", rest, flags=re.I)
    return keys


def _last_clause(s: str) -> str:
    return re.split(r"[.;:,(]|\bexcept\b|\bor\b(?! percentage)", s, flags=re.I)[-1]


def _decimals(text: str) -> tuple[int | None, dict[str, int]]:
    general: int | None = None
    overrides: dict[str, int] = {}

    def apply(subj: str, n: int, before: str) -> None:
        nonlocal general
        subj_txt = _last_clause(subj)
        keys = _subject_keys(subj_txt)
        if not keys and not _GENERAL_SUBJECT.search(subj_txt):
            # "in hectares, rounded to two decimals": the unit sits before the comma.
            sentence = re.split(r"[.;:]\s", before)[-1]
            keys = [] if re.search(r"\bother\b", sentence, re.I) else _subject_keys(sentence)
        if keys and not re.search(r"\bother\b", subj_txt, re.I):
            for k in keys:
                overrides[k] = n
        elif general is None:
            general = n

    for m in _DECIMALS.finditer(text):
        apply(m.group("subj"), _num(m.group("n")), text[: m.start()])
    for m in _STEP.finditer(text):
        step = m.group("step")
        n = 0 if step == "1" else len(step.split(".")[1])
        keys = _subject_keys(m.group("unit")) + _subject_keys(_last_clause(m.group("subj")))
        for k in keys or [m.group("unit").strip().lower()]:
            overrides[k] = n
    for m in _INTEGERS.finditer(text):
        for k in _subject_keys(_last_clause(m.group("subj"))) or ["count"]:
            overrides[k] = 0
    return general, overrides


def _crs(text: str) -> str | None:
    if m := _EPSG.search(text):
        return f"EPSG:{m.group(1)}"
    # Earliest projected alias wins; WGS 84 only when no projected CRS is named.
    hits = sorted(
        (hit.start(), code)
        for pat, code in CRS_ALIASES.items()
        if (hit := re.search(pat, text, re.I))
    )
    projected = [h for h in hits if h[1] != "EPSG:4326"]
    if projected or hits:
        return (projected or hits)[0][1]
    return None


def _extract(text: str, *, is_question: bool = False) -> dict[str, Any]:
    t = text or ""
    found: dict[str, Any] = {}

    if re.search(r"visibly formatted|as (?:they are )?displayed|displayed values?", t, re.I):
        found["value_mode"] = "displayed"
    if re.search(
        r"stored (?:numeric )?(?:cell )?values?|native precision|full[- ]precision cell",
        t,
        re.I,
    ):
        found["value_mode"] = "stored"

    general, overrides = _decimals(t)
    if general is not None:
        found["decimals"] = general
    if overrides:
        found["decimals_overrides"] = overrides

    even = re.search(r"ties[- ]to[- ]even|half[- _]even|banker", t, re.I)
    up = re.search(r"half[- _]up", t, re.I)
    if even and up:
        found["rounding"] = "either"
    elif even:
        found["rounding"] = "half_even"
    elif up:
        found["rounding"] = "half_up"

    if re.search(
        r"intermediate (?:calculations|precision|values)|at the end|only at the end", t, re.I
    ):
        found["round_at_end"] = True

    if crs := _crs(t):
        found["crs"] = crs

    if re.search(r"boundar(?:y|ies)\s+(?:are\s+|is\s+)?included|inclusive radius", t, re.I):
        found["radius_inclusive"] = True
    elif re.search(r"boundar(?:y|ies)\s+(?:are\s+|is\s+)?excluded|exclusive radius", t, re.I):
        found["radius_inclusive"] = False

    if re.search(
        r"without repair|do(?:es)? not repair|unchanged (?:\w+ )?geometry|geometry unchanged|"
        r"not repair(?:ing)?",
        t,
        re.I,
    ):
        found["repair_geometry"] = False
    elif re.search(
        r"(?:may|can|should) (?:be )?repair|repair(?:ing|ed)? (?:is|are) (?:allowed|permitted)",
        t,
        re.I,
    ):
        found["repair_geometry"] = True

    if re.search(r"counts? (?:mean|are|=)\s+(?:data |exact )?records", t, re.I):
        found["counts_mean"] = "records"
    if re.search(r"count (?:distinct|unique)\b", t, re.I) or (
        is_question and re.search(r"\b(?:unique|distinct)\b", t, re.I)
    ):
        found["counts_mean"] = "unique_keys"

    if re.search(r"without time[- ]shift|no time[- ]shift|literal (?:timestamp|date)", t, re.I):
        found["time_shift"] = False
    elif re.search(r"(?:convert|shift)\w* (?:the )?(?:timestamps? )?to (?:local|utc)", t, re.I):
        found["time_shift"] = True

    if re.search(r"hypothetical", t, re.I):
        found["hypothetical_rules"] = True
    return found


def parse_conventions(context: str, question: str | None = None) -> Conventions:
    """Context sets the defaults; explicit conventions in the question override them."""
    fields = _extract(context)
    if question:
        q = _extract(question, is_question=True)
        if "decimals_overrides" in q:
            q["decimals_overrides"] = {
                **fields.get("decimals_overrides", {}),
                **q["decimals_overrides"],
            }
        fields.update(q)
    return Conventions(raw_context=context or "", **fields)
