"""Router (card S3): one JSON-schema call → Route; keyword rules when the model fails."""

from __future__ import annotations

import json
import re

from municipal_qa.llm.prompts import render_prompt
from municipal_qa.schema import Passage, Question, QuestionType, Route, SourceProfile, SubQuestion

EXPECTS = ["number", "text", "yes_no", "list", "date", "identifier"]
SUMMARY_CHARS = 2000
EXCERPT_CHARS = 300

_W = {
    "raster": r"\b(pixels?|raster|grid cells?|dsm|dtm|point cloud|lidar|laz|returns?)\b",
    "spatial": r"\b(area|hectares?|ha\b|distance|radius|within \d|crs|epsg|polygon|geometr"
    r"|inside|overlap|intersect|footprint|metres? (?:of|from))",
    "data_quality": r"\b(null|blank|missing|empty|duplicates?|duplicated|only one (?:file|of)"
    r"|join|unmatched|invalid|have no|has no|without an?)\b",
    "criteria": r"\b(criteria|criterion|threshold|pass(?:es)? (?:or|and) fail|eligible|qualif)",
    "count": r"\b(how many|number of|count)\b",
    "compare": r"\b(compare|compared|change[ds]?|increase|decrease|rise|fell|differ|difference"
    r"|higher|lower|more than|less than|versus|vs\.?|which (?:city|region|was|is|had))\b",
    "aggregate": r"\b(share|percentage|percent|total|sum|average|mean|median|highest|largest"
    r"|maximum|minimum|lowest)\b",
    "semantics": r"\b(what does|mean[s]?\b|definition|can (?:i|we)|could (?:i|we)"
    r"|is (?:it|that|this) (?:right|correct)|someone|treated|assume)",
}
RULES: list[tuple[QuestionType, str]] = [(QuestionType(k), v) for k, v in _W.items()]


def rule_qtype(question: str) -> QuestionType:
    q = question.lower()
    if re.search(RULES[-1][1], q) and not re.search(r"\b\d", q.split("?")[0]):
        return QuestionType.SEMANTICS
    for qt, pat in RULES:
        if re.search(pat, q):
            return qt
    return QuestionType.LOOKUP


def rule_route(q: Question) -> Route:
    parts = [p.strip() for p in re.split(r"(?<=\?)\s+", q.question) if p.strip()] or [q.question]
    subs = []
    for p in parts[:4]:
        yn = re.match(r"(?i)(is|are|can|could|does|do|did|was|were|should|has|have)\b", p)
        subs.append(SubQuestion(text=p, expects="yes_no" if yn else "text"))
    return Route(qtype=rule_qtype(q.question), sub_questions=subs, primary_source_ids=q.source_ids)


def schema(source_ids: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "qtype": {"type": "string", "enum": [t.value for t in QuestionType]},
            "sub_questions": {
                "type": "array",
                "minItems": 1,
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "expects": {"type": "string", "enum": EXPECTS},
                        "unit": {"type": ["string", "null"]},
                    },
                    "required": ["text", "expects"],
                },
            },
            "primary_source_ids": {
                "type": "array",
                "items": {"type": "string", "enum": source_ids},
            },
            "premise_to_check": {"type": ["string", "null"]},
        },
        "required": ["qtype", "sub_questions", "primary_source_ids"],
    }


def source_summary(profiles: list[SourceProfile], limit: int = SUMMARY_CHARS) -> str:
    rows = []
    for p in profiles:
        tables = ", ".join(t.table_name for t in p.tables[:6])
        doc = " DOCUMENTATION" if p.is_documentation else ""
        rows.append(f"- {p.source.source_id} {p.source.original_file} ({p.kind}{doc})"
                    + (f": {tables}" if tables else ""))  # fmt: skip
    return "\n".join(rows)[:limit]


def excerpts(passages: list[Passage], n: int = 4) -> str:
    return "\n".join(f"[{p.locator.text}] {' '.join(p.text.split())[:EXCERPT_CHARS]}"
                     for p in passages[:n])  # fmt: skip


def parse_route(text: str, q: Question) -> Route:
    data = json.loads(text)
    r = Route.model_validate(data)
    keep = [s for s in r.primary_source_ids if s in q.source_ids]
    subs = [s for s in r.sub_questions if s.text.strip()][:4]
    return r.model_copy(
        update={
            "primary_source_ids": keep or list(q.source_ids),
            "sub_questions": subs or rule_route(q).sub_questions,
        }
    )


def route(q: Question, profiles, passages, client, budget) -> Route:
    prompt = render_prompt(
        "route",
        context=q.context,
        sources=source_summary(profiles),
        excerpts=excerpts(passages),
        question=q.question,
    )
    text, _ = client.chat([{"role": "user", "content": prompt}], "route", budget,
                          json_schema=schema(q.source_ids))  # fmt: skip
    return parse_route(text, q)


RULE_WINS = {QuestionType.SPATIAL, QuestionType.RASTER, QuestionType.SEMANTICS}


def combine(r: Route, q: Question) -> Route:
    """Keyword rules win for spatial, raster and semantics; the model's route otherwise (D-054)."""
    rq = rule_qtype(q.question)
    return r.model_copy(update={"qtype": rq}) if rq in RULE_WINS and rq != r.qtype else r
