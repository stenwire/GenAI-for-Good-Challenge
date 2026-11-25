"""Batch runner: artifacts/runs/<run_id>/ with answers, budget log, errors, manifest (plan §8)."""

from __future__ import annotations

import csv
import datetime as dt
import json
import platform
import subprocess
import sys
from collections.abc import Callable
from importlib import metadata
from pathlib import Path

from municipal_qa.config import PROJECT_ROOT, Config
from municipal_qa.schema import AnswerRecord, Question

KEY_PACKAGES = [
    "municipal-qa",
    "pandas",
    "numpy",
    "duckdb",
    "openpyxl",
    "lxml",
    "pdfplumber",
    "python-docx",
    "beautifulsoup4",
    "bm25s",
    "httpx",
    "pydantic",
    "geopandas",
    "shapely",
    "pyproj",
    "rasterio",
    "laspy",
    "rapidfuzz",
]


def git_info() -> dict:
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, check=False
        ).stdout.strip()

    return {
        "commit": git("rev-parse", "HEAD"),
        "short": git("rev-parse", "--short", "HEAD"),
        "dirty": bool(git("status", "--porcelain", "--untracked-files=no")),
    }


def new_run_id(now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now()
    return f"{now:%Y%m%d-%H%M}-{git_info()['short'] or 'nogit'}"


def _versions() -> dict[str, str]:
    out = {}
    for name in KEY_PACKAGES:
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            out[name] = "not installed"
    return out


def write_manifest(run_dir: Path, cfg: Config, split: str, model: dict, extra: dict) -> None:
    path = run_dir / "run_manifest.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    data.update(
        {
            "split": split,
            "git": git_info(),
            "config": json.loads(cfg.model_dump_json()),
            "model": model,
            "seed": cfg.llm.options.get("seed"),
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "packages": _versions(),
            **extra,
        }
    )
    path.write_text(json.dumps(data, indent=2, default=str))


def done_ids(run_dir: Path) -> set[str]:
    path = run_dir / "answers.jsonl"
    if not path.exists():
        return set()
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.add(json.loads(line)["question_id"])
    return out


def write_budget_csv(run_dir: Path) -> tuple[int, int, int]:
    """Rewrite budget.csv from answers.jsonl; returns (rows, max calls, max tokens)."""
    rows = []
    with open(run_dir / "answers.jsonl", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                rows.append((r["question_id"], r["total_calls"], r["total_generated_tokens"]))
    with open(run_dir / "budget.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "calls", "generated_tokens"])
        w.writerows(rows)
    return len(rows), max((r[1] for r in rows), default=0), max((r[2] for r in rows), default=0)


def run_questions(
    questions: list[Question],
    answer: Callable[[Question], AnswerRecord],
    run_dir: Path,
    progress: Callable[[int, int, AnswerRecord], None] | None = None,
) -> int:
    """Answer every question not already in answers.jsonl, appending one line each (resumable)."""
    run_dir.mkdir(parents=True, exist_ok=True)
    skip = done_ids(run_dir)
    todo = [q for q in questions if q.id not in skip]
    with (
        open(run_dir / "answers.jsonl", "a", encoding="utf-8") as ans,
        open(run_dir / "errors.jsonl", "a", encoding="utf-8") as err,
    ):
        for i, q in enumerate(todo, 1):
            rec = answer(q)
            ans.write(rec.model_dump_json() + "\n")
            ans.flush()
            if rec.error or rec.fallback_used:
                err.write(
                    json.dumps(
                        {
                            "question_id": q.id,
                            "error": rec.error,
                            "fallback_used": rec.fallback_used,
                        }
                    )
                    + "\n"
                )
                err.flush()
            if progress:
                progress(i, len(todo), rec)
    return len(todo)


def load_run_records(run_dir: Path) -> dict[str, AnswerRecord]:
    out: dict[str, AnswerRecord] = {}
    with open(run_dir / "answers.jsonl", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rec = AnswerRecord.model_validate_json(line)
                out[rec.question_id] = rec
    return out


def select(questions: list[Question], ids: str | None, limit: int | None) -> list[Question]:
    if ids:
        wanted = [i.strip() for i in ids.split(",") if i.strip()]
        known = {q.id: q for q in questions}
        missing = [i for i in wanted if i not in known]
        if missing:
            raise KeyError(f"unknown question IDs: {missing[:5]}")
        questions = [known[i] for i in wanted]
    return questions[:limit] if limit else questions
