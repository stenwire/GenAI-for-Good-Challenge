"""The only place evidence lines are built (plan §6.9). The model never writes one."""

from __future__ import annotations

import re

from municipal_qa.schema import Fact, Passage

MAX_LINES = 8
CLAIM_CHARS = 160


def clean(text: str) -> str:
    """Single line, no pipes: the line format is `SRC | locator | claim`."""
    return re.sub(r"\s+", " ", text.replace("|", "/")).strip()


def line(source_id: str, locator: str, claim: str) -> str:
    return f"{source_id} | {clean(locator) or 'file'} | {clean(claim) or 'source consulted'}"


def _claim_from_passage(p: Passage) -> str:
    text = clean(p.text)
    return text if len(text) <= CLAIM_CHARS else text[: CLAIM_CHARS - 1].rstrip() + "…"


def from_passages(passages: list[Passage], limit: int = MAX_LINES) -> list[str]:
    out: list[str] = []
    for p in passages:
        ln = line(p.source_id, p.locator.text, _claim_from_passage(p))
        if ln not in out:
            out.append(ln)
        if len(out) >= limit:
            break
    return out


def from_facts(facts: list[Fact], limit: int = MAX_LINES) -> list[str]:
    out: list[str] = []
    for f in facts:
        for loc in f.locators:
            ln = line(loc.source_id, loc.text, f.claim)
            if ln not in out:
                out.append(ln)
            if len(out) >= limit:
                return out
    return out


_NUM = re.compile(r"^-?[\d,]+(?:\.\d+)?")


def _key(display: str) -> str | None:
    d = display.strip()
    m = _NUM.match(d)
    if m and any(ch.isdigit() for ch in m.group(0)):
        return m.group(0).replace(",", "")
    return d.lower() if 0 < len(d) <= 40 else None


def used(answer: str, facts: list[Fact]) -> list[Fact]:
    """Facts whose value (number or short text) appears in the answer."""
    a = answer.lower().replace(",", "")
    return [f for f in facts if (k := _key(f.display)) and k in a]


def for_answer(answer: str, facts: list[Fact], passages: list[Passage], text_first: bool,
               limit: int = MAX_LINES) -> list[str]:  # fmt: skip
    """Facts used in the answer first; passages lead for semantics and fill thin evidence."""
    fact_lines = from_facts(used(answer, facts), limit) or from_facts(facts[:3], limit)
    pas_lines = from_passages(passages, limit)
    if text_first:
        ordered = pas_lines[:3] + fact_lines + pas_lines[3:]
    else:
        ordered = fact_lines + (pas_lines if len(fact_lines) < 2 else pas_lines[:1])
    out: list[str] = []
    for ln in ordered:
        if ln not in out:
            out.append(ln)
    return out[:limit]
