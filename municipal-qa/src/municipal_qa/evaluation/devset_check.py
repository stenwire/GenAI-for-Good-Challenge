"""Refuse a dev set that touches Test material (card T3, brief §4.5). Exit 1 on any problem."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pandas as pd

from municipal_qa.schema import SourceInfo

MIN_ROWS = 60
REQUIRED = [
    "ID",
    "Case_ID",
    "Domain",
    "Question",
    "Context",
    "Source_IDs",
    "Answer",
    "Evidence",
    "elements",
]
ELEMENT_TYPES = {"number", "text", "date", "yes_no"}
_EVIDENCE = re.compile(r"^SRC_[0-9a-f]{12} \| .+ \| .+$")


def check(path: Path, manifest: dict[str, SourceInfo], min_rows: int = MIN_ROWS) -> list[str]:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    problems = [f"missing column {c}" for c in REQUIRED if c not in df.columns]
    if problems:
        return problems
    if len(df) < min_rows:
        problems.append(f"{len(df)} rows; at least {min_rows} required")
    dupes = sorted(df["ID"][df["ID"].duplicated()].unique())
    if dupes:
        problems.append(f"duplicate IDs: {dupes[:5]}")
    for r in df.to_dict("records"):
        qid = r["ID"]
        try:
            sources = json.loads(r["Source_IDs"])
            evidence = json.loads(r["Evidence"])
            elements = json.loads(r["elements"])
        except json.JSONDecodeError as e:
            problems.append(f"{qid}: invalid JSON ({e.msg})")
            continue
        for sid in sources:
            if sid not in manifest:
                problems.append(f"{qid}: unknown source {sid}")
            elif manifest[sid].source_set != "Train":
                problems.append(f"{qid}: uses Test source {sid} ({manifest[sid].original_file})")
        for line in evidence:
            if not _EVIDENCE.match(line):
                problems.append(f"{qid}: malformed evidence {line[:60]!r}")
            elif line[:16] not in sources:
                problems.append(f"{qid}: evidence cites {line[:16]} outside Source_IDs")
        if not r["Answer"].strip():
            problems.append(f"{qid}: blank answer")
        if not elements or not all(e.get("type") in ELEMENT_TYPES for e in elements):
            problems.append(f"{qid}: elements must be a non-empty list of {sorted(ELEMENT_TYPES)}")
    return problems


def main() -> int:
    from municipal_qa.config import load_config
    from municipal_qa.data.manifest import load_manifest
    from municipal_qa.data.questions import DEV_QUESTIONS

    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEV_QUESTIONS
    problems = check(path, load_manifest(load_config()))
    for p in problems:
        print(f"problem: {p}")
    n = len(pd.read_csv(path, dtype=str, keep_default_na=False))
    print(f"{'FAILED' if problems else 'OK'}: {path.name}, {n} rows, {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
