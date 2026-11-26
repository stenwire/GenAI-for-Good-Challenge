"""Dev-set retrieval recall@k (card T2): is each reference evidence item among the top k?"""

from __future__ import annotations

import csv
import re
import time

from municipal_qa.evaluation.scorer import locator_tokens, parse_reference_evidence
from municipal_qa.schema import Passage, Question

_SHEET = re.compile(r"\bsheet\s+([^,;|]+)", re.I)


_RANGE = re.compile(r"\b(row|line)s?\s+(\d+)\s*-\s*(\d+)", re.I)


def _tokens(loc: str) -> set[str]:
    out = locator_tokens(loc) | {f"sheet {m.strip().lower()}" for m in _SHEET.findall(loc)}
    for w, a, b in _RANGE.findall(loc):
        out |= {f"{w.lower()} {n}" for n in range(int(a), min(int(b), int(a) + 500) + 1)}
    return out


def found(sid: str, loc: str, hits: list[Passage]) -> bool:
    """Same source, and a shared locator token (cell, row, note, line, key, sheet) when the
    reference names one."""
    want = _tokens(loc)
    return any(p.source_id == sid and (not want or want & _tokens(p.locator.text)) for p in hits)


def qtypes(path) -> dict[str, str]:
    with open(path, encoding="utf-8") as f:
        return {r["ID"]: r.get("qtype", "") for r in csv.DictReader(f)}


def recall_at_k(retriever, questions: list[Question], manifest, k: int = 8, qtype_of=None) -> dict:
    items = hit = q_all = 0
    times, misses = [], []
    for q in questions:
        pairs = parse_reference_evidence(q.reference_evidence or [], q, manifest)
        refs = [(s, loc) for s, loc in pairs if s]
        if not refs:
            continue
        retriever.search(q.question, q.source_ids, k=k)  # warm the per-source-set index
        t0 = time.perf_counter()
        hits = retriever.search(q.question, q.source_ids, k=k, qtype=(qtype_of or {}).get(q.id))
        times.append((time.perf_counter() - t0) * 1000)
        ok = [found(s, loc, hits) for s, loc in refs]
        items, hit = items + len(ok), hit + sum(ok)
        q_all += all(ok)
        misses += [(q.id, s, loc) for (s, loc), o in zip(refs, ok, strict=True) if not o]
    n = len(times)
    return {
        "questions": n,
        "items": items,
        "item_recall": hit / items if items else 0.0,
        "question_recall": q_all / n if n else 0.0,
        "ms_mean": sum(times) / n if n else 0.0,
        "ms_max": max(times, default=0.0),
        "misses": misses,
    }


def main(k: int = 8) -> None:
    import municipal_qa.readers  # noqa: F401
    from municipal_qa.config import load_config
    from municipal_qa.data.manifest import load_manifest
    from municipal_qa.data.questions import load_questions, questions_path
    from municipal_qa.llm.client import LLMClient
    from municipal_qa.profiling.profile import ProfileStore
    from municipal_qa.retrieval.index import Retriever

    cfg = load_config()
    m = load_manifest(cfg)
    store = ProfileStore(cfg, m)
    qt = qtypes(questions_path(cfg, "dev"))
    dev = load_questions(cfg, "dev")
    sem = [q for q in dev if qt.get(q.id) == "semantics"]
    for name, client in (("hybrid", LLMClient(cfg)), ("bm25", None)):
        r = Retriever(cfg, store, client)
        for label, qs in (("semantics", sem), ("all", dev)):
            res = recall_at_k(r, qs, m, k, qt)
            print(
                f"{name:6} {label:9} questions {res['questions']:3} items {res['items']:3} "
                f"recall@{k} {res['item_recall']:.3f} question-recall {res['question_recall']:.3f} "
                f"search ms mean {res['ms_mean']:.1f} max {res['ms_max']:.1f}"
            )
            if label == "semantics":
                for miss in res["misses"]:
                    print("   miss", *miss)


if __name__ == "__main__":
    main()
