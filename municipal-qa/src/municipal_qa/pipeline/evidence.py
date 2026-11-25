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
