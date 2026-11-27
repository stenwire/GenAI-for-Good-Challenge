"""Per-question entry point (plan §6.2): route, plan, execute, compose; or the M1 baseline."""

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
from municipal_qa.schema import AnswerRecord, Passage, Plan, Question, SourceInfo

MAX_ANSWER_CHARS = 1500
SOURCE_LINE_CHARS = 160


@dataclass
class Deps:
    cfg: Config
    manifest: dict[str, SourceInfo]
    client: LLMClient | None
    store: PassageStore = field(init=False)
    cache_dir: Path | None = None
    profiles: object = None
    retriever: object = None

    def __post_init__(self) -> None:
        self.store = PassageStore(self.manifest, self.cache_dir)
        if self.cfg.pipeline.mode == "planner":
            from municipal_qa.profiling.profile import ProfileStore
            from municipal_qa.retrieval.index import Retriever

            self.profiles = self.profiles or ProfileStore(self.cfg, self.manifest)
            self.retriever = self.retriever or Retriever(
                self.cfg, self.profiles, self.client, passages=self.store
            )


def fallback_text(q: Question) -> str:
    return (
        "The supplied sources listed for this question do not establish a definite answer to: "
        + " ".join(q.question.split())
    )


_BULLET = re.compile(r"^\s*[-*•+]\s+")
_NUMBERED = re.compile(r"^\s*\d{1,2}[.)]\s+")
_BARE_MARKER = re.compile(r"^\s*(?:[-*•+]|\d{1,2}[.)])\s*$")


def _is_item(lines: list[str], i: int) -> bool:
    """Bullets always; `N.` only beside another `N.` line, so a leading value survives."""
    if _BULLET.match(lines[i]):
        return True
    near = [lines[j] for j in (i - 1, i + 1) if 0 <= j < len(lines)]
    return bool(_NUMBERED.match(lines[i])) and any(_NUMBERED.match(n) for n in near)


def _fold_bullets(lines: list[str]) -> list[str]:
    """List items become sentences of one paragraph, so no answer line starts with `- `."""
    lines = [ln for ln in lines if not _BARE_MARKER.match(ln)]
    out: list[str] = []
    for i, ln in enumerate(lines):
        if not _is_item(lines, i):
            out.append(ln)
            continue
        marker = _BULLET if _BULLET.match(ln) else _NUMBERED
        item = marker.sub("", ln, count=1).strip()
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
    if deps.cfg.pipeline.mode == "planner":
        try:
            return _answer_planned(q, deps)
        except Exception as e:  # noqa: BLE001
            rec = _answer_baseline(q, deps)
            rec.error = "; ".join(x for x in [f"planner: {type(e).__name__}: {e}", rec.error] if x)
            return rec
    return _answer_baseline(q, deps)


def _answer_baseline(q: Question, deps: Deps) -> AnswerRecord:
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


# ---------- planner path (plan §6.2) ----------

TEXT_QTYPES = {"semantics", "insufficient"}
FACT_LINES = 40
FACT_CHARS = 5000


def _facts_text(pairs) -> str:
    rows = []
    for name, f in pairs[:FACT_LINES]:
        note = f" [{f.note}]" if f.note else ""
        rows.append(f"- {name}.{f.name}: {f.display}{note} — {' '.join(f.claim.split())[:200]}")
    return "\n".join(rows)[:FACT_CHARS] or "(no computed facts)"


def _compose_planned(q, route, conv, pairs, passages, deps, budget) -> str:
    from municipal_qa.pipeline.planner import _subs, conventions_text

    prompt = render_prompt(
        "compose",
        conventions=conventions_text(conv),
        sub_questions=_subs(route),
        premise=route.premise_to_check or "none",
        facts=_facts_text(pairs),
        excerpts=_excerpts(passages[:6]),
        question=q.question,
    )
    text, _ = deps.client.chat([{"role": "user", "content": prompt}], "compose", budget)
    return clean_answer(text)


def _answer_planned(q: Question, deps: Deps) -> AnswerRecord:
    from municipal_qa.pipeline import executor, planner, router, templates
    from municipal_qa.profiling.profile import render_profiles
    from municipal_qa.tools.registry import ExecContext

    cfg = deps.cfg
    budget = QuestionBudget(cfg.budget.max_calls, cfg.budget.max_generated_tokens)
    errors: list[str] = []
    conv = parse_conventions(q.context, q.question)
    profiles = deps.profiles.get_many(q.source_ids)
    k = cfg.retrieval.k_final
    try:
        passages = deps.retriever.search(q.question, q.source_ids, k=k)
    except Exception as e:  # noqa: BLE001
        errors.append(f"retrieval: {type(e).__name__}: {e}")
        passages = search(deps.store, q.question, q.source_ids, k=k)
    route = None
    if deps.client is not None:
        try:
            route = router.combine(router.route(q, profiles, passages, deps.client, budget), q)
        except Exception as e:  # noqa: BLE001
            errors.append(f"route: {type(e).__name__}: {e}")
    route = route or router.rule_route(q)
    qtype = route.qtype.value
    if qtype in TEXT_QTYPES:
        try:
            passages = deps.retriever.search(q.question, q.source_ids, k=k, qtype="semantics")
        except Exception as e:  # noqa: BLE001
            errors.append(f"retrieval: {type(e).__name__}: {e}")
    docs_suffice = qtype in TEXT_QTYPES and any(p.kind != "body" for p in passages)
    plan, ex = None, None
    ctx = ExecContext(deps.profiles, conv, list(q.source_ids), retriever=deps.retriever)
    tex = None
    if not docs_suffice:
        try:
            tplan = templates.template_plan(q.question, profiles, ctx, qtype)
        except Exception as e:  # noqa: BLE001
            tplan = None
            errors.append(f"template: {type(e).__name__}: {e}")
        if tplan is not None:
            tex = executor.execute(tplan, ctx, None, 0)
            if not tex.ok:
                bad = [f"{s.op}: {r.error}" for s, r in zip(tex.steps, tex.results, strict=True)
                       if not r.ok]  # fmt: skip
                errors.append(f"template failed, model planner used: {'; '.join(bad)[:300]}")
    if tex is not None and tex.ok:
        plan, ex = Plan(steps=tex.steps, rationale=tplan.rationale), tex
    elif deps.client is not None and not docs_suffice:
        profiles_text = render_profiles(profiles, 6000, q.question)
        try:
            plan = planner.plan(q, route, conv, profiles_text, deps.client, budget, profiles)
        except Exception as e:  # noqa: BLE001
            errors.append(f"plan: {type(e).__name__}: {e}")
    if plan is not None and ex is None:
        base = planner.ops_for(qtype, q.question)
        ops = planner.usable_ops(base, profiles) or base

        def repair(step, error):
            sid = next((step.args.get(k) for k in executor.SOURCE_KEYS if step.args.get(k)), None)
            one = [p for p in profiles if p.source.source_id == sid] or profiles
            text = render_profiles(one, 2500, q.question)
            saved = sorted(ctx.env)
            return planner.repair(q, step, error, saved, text, deps.client, budget, ops, profiles)

        ex = executor.execute(plan, ctx, repair, cfg.pipeline.max_repairs)
        done = zip(ex.steps, ex.results, strict=True)
        errors += [f"step {s.save_as} {s.op}: {r.error}" for s, r in done if not r.ok]
    pairs = ex.facts() if ex else []
    answer, fallback = "", True
    if deps.client is not None and (pairs or passages):
        try:
            answer = _compose_planned(q, route, conv, pairs, passages, deps, budget)
            fallback = not answer
        except Exception as e:  # noqa: BLE001
            errors.append(f"compose: {type(e).__name__}: {e}")
    facts = [f for _, f in pairs]
    if not answer and facts:
        answer = " ".join(f.claim.rstrip(".") + "." for f in facts[:3])
    lines = evidence.for_answer(answer, facts, passages, text_first=qtype in TEXT_QTYPES)
    if not answer:
        answer, lines = fallback_text(q), lines[:1]
    return AnswerRecord(
        question_id=q.id,
        answer=answer,
        evidence=lines,
        route=route,
        plan=Plan(steps=ex.steps, rationale=plan.rationale) if ex else plan,
        results=ex.results if ex else [],
        calls=budget.records(),
        total_calls=budget.calls_used,
        total_generated_tokens=budget.tokens_used,
        fallback_used=fallback,
        error="; ".join(errors) or None,
    )
