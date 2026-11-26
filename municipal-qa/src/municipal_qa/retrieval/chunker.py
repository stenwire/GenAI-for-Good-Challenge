"""Split long passages at sentence or word boundaries; every chunk keeps its locator."""

from __future__ import annotations

from municipal_qa.schema import Passage


def _cut(text: str, start: int, size: int) -> int:
    end = min(len(text), start + size)
    if end == len(text):
        return end
    for sep in (". ", "\n", "; ", ", ", " "):
        i = text.rfind(sep, start + size // 2, end)
        if i > 0:
            return i + len(sep)
    return end


def chunk(passages: list[Passage], chars: int = 900, overlap: int = 150) -> list[Passage]:
    out: list[Passage] = []
    for p in passages:
        if len(p.text) <= chars:
            out.append(p)
            continue
        start, n = 0, 0
        while start < len(p.text):
            end = _cut(p.text, start, chars)
            out.append(
                p.model_copy(
                    update={"passage_id": f"{p.passage_id}.{n}", "text": p.text[start:end]}
                )
            )
            if end >= len(p.text):
                break
            start, n = max(end - overlap, start + 1), n + 1
    return out
