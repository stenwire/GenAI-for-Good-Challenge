from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import pandas as pd

from municipal_qa.config import PROJECT_ROOT, Config
from municipal_qa.schema import Question

DEV_QUESTIONS = PROJECT_ROOT / "dev" / "dev_questions.csv"


def _path(cfg: Config, split: str) -> Path:
    if split == "train":
        return cfg.data_dir / "Train.csv"
    if split == "test":
        return cfg.data_dir / "Test.csv"
    if split == "dev":
        return DEV_QUESTIONS
    raise ValueError(f"unknown split {split!r}")


def load_questions(cfg: Config, split: Literal["train", "dev", "test"]) -> list[Question]:
    df = pd.read_csv(_path(cfg, split), dtype=str, keep_default_na=False)
    out = []
    for r in df.to_dict("records"):
        out.append(
            Question(
                id=r["ID"],
                case_id=r["Case_ID"],
                domain=r["Domain"],
                question=r["Question"],
                context=r["Context"],
                source_ids=json.loads(r["Source_IDs"]),
                split=split,
                reference_answer=r.get("Answer") or None,
                reference_evidence=json.loads(r["Evidence"]) if r.get("Evidence") else None,
            )
        )
    return out
