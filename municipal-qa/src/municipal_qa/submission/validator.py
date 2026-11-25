from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

EVIDENCE_LINE = re.compile(r"^- SRC_[0-9a-f]{12} \| .+ \| .+$")
SIZE_WARN_BYTES = 256 * 1024


@dataclass
class Block:
    qid: str
    answer: str
    evidence: list[str] = field(default_factory=list)
    has_evidence_heading: bool = False


def parse_submission(text: str) -> tuple[str, list[Block]]:
    """Split into (preamble, blocks). Evidence items keep their leading '- '."""
    lines = text.split("\n")
    preamble: list[str] = []
    blocks: list[Block] = []
    in_evidence = False
    answer_lines: list[str] = []

    def close() -> None:
        if blocks:
            blocks[-1].answer = "\n".join(answer_lines).strip()

    for line in lines:
        if line.startswith("## "):
            close()
            blocks.append(Block(qid=line[3:], answer=""))
            answer_lines = []
            in_evidence = False
        elif not blocks:
            preamble.append(line)
        elif line == "### Evidence":
            close()
            blocks[-1].has_evidence_heading = True
            in_evidence = True
        elif in_evidence:
            if line.strip():
                blocks[-1].evidence.append(line)
        else:
            answer_lines.append(line)
    if blocks and not in_evidence:
        close()
    return "\n".join(preamble), blocks


def validate_submission(
    path: Path,
    test_ids: list[str],
    source_ids: dict[str, list[str]] | None = None,
    manifest_ids: set[str] | None = None,
    warnings: list[str] | None = None,
) -> list[str]:
    """Return problems ([] = valid). Non-fatal findings go to `warnings` when given."""
    warn = warnings if warnings is not None else []
    problems: list[str] = []
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        return [f"not UTF-8: {e}"]
    if "\r" in text:
        problems.append("contains \\r; line endings must be \\n")
    if raw.startswith(b"\xef\xbb\xbf"):
        problems.append("starts with a UTF-8 BOM")
    if len(raw) > SIZE_WARN_BYTES:
        warn.append(f"file is {len(raw):,} bytes, above the unofficial 256 KiB guidance")

    preamble, blocks = parse_submission(text.replace("\r", ""))
    if preamble.strip() != "# Submission" or not text.startswith("# Submission\n"):
        problems.append("first line must be '# Submission'")

    ids = [b.qid for b in blocks]
    if ids != test_ids:
        missing = [q for q in test_ids if q not in set(ids)]
        unknown = [q for q in ids if q not in set(test_ids)]
        dupes = sorted({q for q in ids if ids.count(q) > 1})
        if missing:
            problems.append(f"{len(missing)} Test IDs missing, e.g. {missing[:3]}")
        if unknown:
            problems.append(
                f"{len(unknown)} '## ' headings are not Test IDs (answer lines starting "
                f"with '## '?), e.g. {unknown[:3]}"
            )
        if dupes:
            problems.append(f"duplicate IDs: {dupes[:3]}")
        if not (missing or unknown or dupes):
            problems.append("Test IDs are out of order")

    for b in blocks:
        if not b.answer:
            problems.append(f"{b.qid}: blank answer")
        if not b.has_evidence_heading:
            problems.append(f"{b.qid}: missing '### Evidence'")
        elif not b.evidence:
            warn.append(f"{b.qid}: no evidence lines")
        allowed = set(source_ids.get(b.qid, [])) if source_ids else None
        for line in b.evidence:
            if not EVIDENCE_LINE.match(line):
                problems.append(f"{b.qid}: malformed evidence line {line[:80]!r}")
                continue
            sid = line[2:18]
            if allowed is not None and sid not in allowed:
                if manifest_ids is not None and sid not in manifest_ids:
                    problems.append(f"{b.qid}: cites unknown source {sid}")
                else:
                    warn.append(f"{b.qid}: cites {sid}, not in its Source_IDs")
    return problems
