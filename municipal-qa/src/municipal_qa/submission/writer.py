"""Submission.md writer, byte-compatible with the starter notebook's save_submission()."""

from __future__ import annotations

import re
from pathlib import Path

from municipal_qa.schema import AnswerRecord, Question

HEADER = "# Submission\n"
_ID_LINE = re.compile(r"^## (.+)$", re.M)


def render_block(qid: str, answer: str, evidence: list[str]) -> str:
    answer = answer.strip()
    if not answer:
        raise ValueError(f"{qid}: blank answer")
    if re.search(r"^## ", answer, re.M):
        raise ValueError(f"{qid}: answer contains a line starting with '## '")
    for e in evidence:
        if not isinstance(e, str) or "\n" in e or "\r" in e:
            raise ValueError(f"{qid}: evidence item must be a single line: {e!r}")
    items = "\n".join("- " + e for e in evidence)
    return f"\n## {qid}\n\n{answer}\n\n### Evidence\n{items}\n"


def render_submission(records: dict[str, AnswerRecord], test_ids: list[str]) -> str:
    missing = [q for q in test_ids if q not in records]
    extra = sorted(set(records) - set(test_ids))
    if missing or extra:
        raise ValueError(f"missing {len(missing)} IDs, extra {len(extra)} IDs")
    text = HEADER + "".join(
        render_block(q, records[q].answer, records[q].evidence) for q in test_ids
    )
    if _ID_LINE.findall(text) != test_ids:
        raise ValueError("rendered ## headings do not match the Test ID order")
    return text


def write_submission(records: dict[str, AnswerRecord], test_ids: list[str], out: Path) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(render_submission(records, test_ids).encode("utf-8"))
    return out


def placeholder_record(q: Question) -> AnswerRecord:
    """Plan §6.10 final fallback text. No evidence: lines are built only in pipeline/evidence.py."""
    restated = " ".join(q.question.split())
    return AnswerRecord(
        question_id=q.id,
        answer=(
            "The supplied sources listed for this question do not establish a definite "
            f"answer to: {restated}"
        ),
        evidence=[],
        fallback_used=True,
    )
