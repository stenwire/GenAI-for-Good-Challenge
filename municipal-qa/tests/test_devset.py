import json

import pandas as pd
import pytest

from municipal_qa.data.manifest import load_manifest
from municipal_qa.data.questions import DEV_QUESTIONS
from municipal_qa.evaluation.devset_check import check
from municipal_qa.evaluation.scorer import score_record
from municipal_qa.schema import AnswerRecord, Question, SourceInfo


def _rows():
    return pd.read_csv(DEV_QUESTIONS, dtype=str, keep_default_na=False)


def _question(r) -> Question:
    return Question(
        id=r["ID"],
        case_id=r["Case_ID"],
        domain=r["Domain"],
        question=r["Question"],
        context=r["Context"],
        source_ids=json.loads(r["Source_IDs"]),
        split="dev",
        reference_answer=r["Answer"],
        reference_evidence=json.loads(r["Evidence"]),
    )


def test_dev_set_size_mix_and_ids():
    df = _rows()
    assert len(df) >= 60 and df["ID"].is_unique
    assert set(df["Domain"]) == {"transport", "buildings_energy", "urban_greenery"}
    assert df["Domain"].value_counts().min() >= 20
    assert {"semantics", "insufficient", "spatial", "stored_displayed"} <= set(df["qtype"])


def test_every_reference_scores_one_against_its_own_elements():
    for r in _rows().to_dict("records"):
        q = _question(r)
        rec = AnswerRecord(question_id=q.id, answer=r["Answer"], evidence=q.reference_evidence)
        s = score_record(rec, q, json.loads(r["elements"]))
        assert s["score"] == 1.0, (r["ID"], s)
        empty = AnswerRecord(question_id=q.id, answer="", evidence=[])
        assert score_record(empty, q, json.loads(r["elements"]))["score"] == 0.0


def _info(sid, source_set):
    return SourceInfo(
        source_id=sid,
        path="/x",
        original_file="f.csv",
        source_set=source_set,
        publisher="",
        licence="",
        bytes=1,
        sha256="",
        suffix=".csv",
    )


def _write(tmp_path, rows):
    p = tmp_path / "dev.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    return p


GOOD = {
    "ID": "d1",
    "Case_ID": "c",
    "Domain": "transport",
    "Question": "Q?",
    "Context": "C",
    "Source_IDs": json.dumps(["SRC_aaaaaaaaaaaa"]),
    "Answer": "No.",
    "Evidence": json.dumps(["SRC_aaaaaaaaaaaa | row 1 | claim"]),
    "elements": json.dumps([{"type": "yes_no", "value": "no"}]),
}
MANIFEST = {
    "SRC_aaaaaaaaaaaa": _info("SRC_aaaaaaaaaaaa", "Train"),
    "SRC_bbbbbbbbbbbb": _info("SRC_bbbbbbbbbbbb", "Test"),
}


@pytest.mark.parametrize(
    ("change", "expect"),
    [
        ({"Source_IDs": json.dumps(["SRC_aaaaaaaaaaaa", "SRC_bbbbbbbbbbbb"])}, "uses Test source"),
        ({"Source_IDs": json.dumps(["SRC_cccccccccccc"])}, "unknown source"),
        ({"Evidence": json.dumps(["SRC_bbbbbbbbbbbb | page 1 | x"])}, "outside Source_IDs"),
        ({"Evidence": json.dumps(["tpo.csv, row 1"])}, "malformed evidence"),
        ({"elements": json.dumps([{"type": "guess"}])}, "elements must be"),
        ({"Answer": " "}, "blank answer"),
    ],
)
def test_check_refuses_bad_rows(tmp_path, change, expect):
    assert check(_write(tmp_path, [GOOD]), MANIFEST, min_rows=1) == []
    problems = check(_write(tmp_path, [{**GOOD, **change}]), MANIFEST, min_rows=1)
    assert any(expect in p for p in problems), problems


def test_check_requires_enough_rows(tmp_path):
    assert check(_write(tmp_path, [GOOD]), MANIFEST) == ["1 rows; at least 60 required"]


def test_real_dev_set_passes_check(data_cfg):
    assert check(DEV_QUESTIONS, load_manifest(data_cfg)) == []
