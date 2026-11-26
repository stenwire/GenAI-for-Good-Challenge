"""Per-question entry point. Baseline (plan §12): retrieve, compose once, code-built evidence."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from municipal_qa.config import Config
from municipal_qa.llm.budget import QuestionBudget
from municipal_qa.llm.client import LLMClient
from municipal_qa.llm.prompts import render_prompt
from municipal_qa.pipeline import evidence
from municipal_qa.pipeline.context import parse_conventions
from municipal_qa.retrieval.bm25 import PassageStore, search
from municipal_qa.schema import AnswerRecord, Passage, Question, SourceInfo

MAX_ANSWER_CHARS = 1500
SOURCE_LINE_CHARS = 160


@dataclass
class Deps:
    cfg: Config
    manifest: dict[str, SourceInfo]
    client: LLMClient | None
    store: PassageStore = field(init=False)
    cache_dir: Path | None = None

    def __post_init__(self) -> None:
        self.store = PassageStore(self.manifest, self.cache_dir)


def fallback_text(q: Question) -> str:
    return (
        "The supplied sources listed for this question do not establish a definite answer to: "
        + " ".join(q.question.split())
    )


_BULLET = re.compile(r"^\s*(?:[-*•+]|\d{1,2}[.)])\s+")


def _fold_bullets(lines: list[str]) -> list[str]:
    """List items become sentences of one paragraph, so no answer line starts with `- `."""
    out: list[str] = []
    for ln in lines:
        if not _BULLET.match(ln):
            out.append(ln)
            continue
        item = _BULLET.sub("", ln).strip()
        if item and item[-1] not in ".!?:;":
            item += "."
        if out and out[-1].strip():
            out[-1] = f"{out[-1].rstrip()} {item}"
        else:
            out.append(item)
    return out


def clean_answer(text: str) -> str:
    """Model text → one safe answer block: no think tags, headings, lists or evidence section."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I)
    text = re.split(r"(?im)^\s*#+\s*evidence\b", text)[0]
    lines = [re.sub(r"^\s*#+\s*", "", ln).rstrip() for ln in text.strip().splitlines()]
    text = "\n".join(_fold_bullets(lines)).strip()
    return text[:MAX_ANSWER_CHARS].strip()


def _excerpts(passages: list[Passage]) -> str:
    return "\n\n".join(f"[{i}] ({p.locator.text}) {p.text}" for i, p in enumerate(passages, 1))


def _sources(q: Question, deps: Deps) -> str:
    rows = []
    for sid in q.source_ids:
        src = deps.manifest.get(sid)
        if src:
            rows.append(
                f"- {src.original_file} ({src.suffix}, {src.publisher})"[:SOURCE_LINE_CHARS]
            )
    return "\n".join(rows)


def _compose(q: Question, passages: list[Passage], deps: Deps, budget: QuestionBudget) -> str:
    conv = parse_conventions(q.context, q.question)
    context = q.context + (f"\nParsed conventions: {conv.model_dump(exclude={'raw_context'})}")
    prompt = render_prompt(
        "compose_baseline",
        context=context,
        sources=_sources(q, deps),
        excerpts=_excerpts(passages),
        question=q.question,
    )
    text, _ = deps.client.chat([{"role": "user", "content": prompt}], "compose", budget)
    return clean_answer(text)


def answer_question(q: Question, deps: Deps) -> AnswerRecord:
    """Never raises and never returns a blank answer (plan §6.10)."""
    budget = QuestionBudget(deps.cfg.budget.max_calls, deps.cfg.budget.max_generated_tokens)
    passages: list[Passage] = []
    errors: list[str] = []
    try:
        rc = deps.cfg.retrieval
        passages = search(
            deps.store, q.question, q.source_ids, k=rc.k_final, docs_first=rc.docs_first
        )
    except Exception as e:  # noqa: BLE001
        errors.append(f"retrieval: {type(e).__name__}: {e}")
    errors += [
        f"read {sid}: {deps.store.errors[sid]}" for sid in q.source_ids if sid in deps.store.errors
    ]
    answer, fallback = "", True
    if passages and deps.client is not None:
        try:
            answer = _compose(q, passages, deps, budget)
            fallback = not answer
        except Exception as e:  # noqa: BLE001
            errors.append(f"compose: {type(e).__name__}: {e}")
    try:
        lines = evidence.from_passages(passages[: deps.cfg.pipeline.evidence_from_top])
    except Exception as e:  # noqa: BLE001
        errors.append(f"evidence: {type(e).__name__}: {e}")
        lines = []
    if not answer:
        answer = fallback_text(q)
        lines = lines[:1]
    try:
        return AnswerRecord(
            question_id=q.id,
            answer=answer,
            evidence=lines,
            calls=budget.records(),
            total_calls=budget.calls_used,
            total_generated_tokens=budget.tokens_used,
            fallback_used=fallback,
            error="; ".join(errors) or None,
        )
    except Exception as e:  # noqa: BLE001
        return AnswerRecord(
            question_id=q.id,
            answer=fallback_text(q),
            evidence=[],
            fallback_used=True,
            error=f"record: {type(e).__name__}: {e}",
        )
