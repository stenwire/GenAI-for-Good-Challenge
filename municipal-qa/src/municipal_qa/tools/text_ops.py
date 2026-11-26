"""Text ops (plan §6.5): passages, field definitions and sections, cited by their locators."""

from __future__ import annotations

import re

from municipal_qa.retrieval.bm25 import DOC_KINDS
from municipal_qa.schema import Fact, Passage
from municipal_qa.tools.registry import ExecContext, OpError, op
from municipal_qa.tools.table_ops import ok

S = {"type": "string"}
CLAIM_CHARS = 300


def _fact(name: str, p: Passage) -> Fact:
    t = " ".join(p.text.split())
    return Fact(name=name, value=t[:1200], display=t[:CLAIM_CHARS], claim=t[:CLAIM_CHARS],
                locators=[p.locator])  # fmt: skip


def _retriever(ctx: ExecContext):
    r = getattr(ctx, "retriever", None)
    if r is None:
        raise OpError("text ops need the retriever; none is loaded")
    return r


def _scope(ctx: ExecContext, source_id: str | None) -> list[str]:
    if source_id:
        ctx.source(source_id)
        return [source_id]
    return list(ctx.source_ids)


@op(
    "find_passages",
    "most relevant text passages for a query within the question's sources",
    {"query": S, "source_id": S, "k": {"type": "integer"}},
    ["query"],
    group="text",
)
def find_passages(ctx, query, source_id=None, k=4):
    hits = _retriever(ctx).search(str(query), _scope(ctx, source_id), k=min(int(k), 8))
    if not hits:
        raise OpError("no passages found")
    facts = [_fact(f"passage{i + 1}", p) for i, p in enumerate(hits)]
    return ok(
        "find_passages", {}, facts, "\n".join(f"[{p.locator.text}] {p.text[:200]}" for p in hits)
    )


@op(
    "get_definition",
    "what a field, code or term means, from dictionaries, guides and notes",
    {"term": S, "source_id": S},
    ["term"],
    group="text",
)
def get_definition(ctx, term, source_id=None):
    term = str(term).strip()
    r = _retriever(ctx)
    sids = _scope(ctx, source_id)
    pat = re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.I)
    pool = r.search(f"{term} meaning definition", sids, k=30)
    exact = [p for p in pool if pat.search(p.text)]
    docs = [p for p in exact if p.kind in DOC_KINDS] + [p for p in exact if p.kind not in DOC_KINDS]
    if not docs:
        docs = [p for sid in sids for p in r.items(sid) if pat.search(p.text)][:3]
    if not docs:
        raise OpError(f"no passage mentions {term!r}")
    facts = [_fact(f"definition{i + 1}", p) for i, p in enumerate(docs[:3])]
    return ok("get_definition", {}, facts)


@op(
    "get_section",
    "the text under a heading, e.g. 'Analysis conventions'",
    {"heading": S, "source_id": S},
    ["heading"],
    group="text",
)
def get_section(ctx, heading, source_id=None):
    want = str(heading).strip().lower()
    r = _retriever(ctx)
    hits = [p for sid in _scope(ctx, source_id) for p in r.items(sid)
            if want in p.locator.text.lower() or p.text.lower().startswith(want)]  # fmt: skip
    if not hits:
        raise OpError(f"no section {heading!r}")
    return ok("get_section", {}, [_fact(f"section{i + 1}", p) for i, p in enumerate(hits[:4])])
