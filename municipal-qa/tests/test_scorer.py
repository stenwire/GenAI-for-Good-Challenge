import json
import shutil
from decimal import Decimal

import pandas as pd
import pytest
from typer.testing import CliRunner

from municipal_qa.cli import app
from municipal_qa.data.manifest import load_manifest
from municipal_qa.data.questions import load_questions
from municipal_qa.evaluation.report import diff, render
from municipal_qa.evaluation.scorer import (
    aggregate,
    dates_in,
    default_elements,
    evaluate,
    locator_tokens,
    numbers_in,
    parse_reference_evidence,
    reference_evidence_lines,
    score_element,
    score_evidence,
    score_record,
)
from municipal_qa.schema import AnswerRecord, Question, SourceInfo


def _q(qid="q1", case="c1", domain="transport", answer="", evidence=None, sources=None):
    return Question(
        id=qid,
        case_id=case,
        domain=domain,
        question="Q?",
        context="",
        source_ids=sources or ["SRC_aaaaaaaaaaaa"],
        split="dev",
        reference_answer=answer,
        reference_evidence=evidence or [],
    )


def _info(sid, name):
    return SourceInfo(
        source_id=sid,
        path="/x",
        original_file=name,
        source_set="Train",
        publisher="",
        licence="",
        bytes=1,
        sha256="",
        suffix=".ods",
    )


def test_numbers_and_dates_parsing():
    text = "Rose by 1,964.06 GWh (8.07%) from TPO453 zone 1912/A1 on 20 April 2018, -5.5 pp."
    assert numbers_in(text) == [Decimal("1964.06"), Decimal("8.07"), Decimal("-5.5")]
    assert dates_in(text) == {"2018-04-20"}
    assert dates_in("confirmed 2018-05-25") == {"2018-05-25"}


def test_default_elements_from_reference():
    els = default_elements(
        "No. Area 3.48 hectares; share 45.45% and gap 5.64 percentage points "
        "for the year ending March 2025."
    )
    kinds = [(e["type"], e.get("text"), e.get("unit"), e.get("tol")) for e in els]
    assert kinds == [
        ("yes_no", "No", None, None),
        ("number", "3.48", "ha", 0.005),
        ("number", "45.45", "%", 0.005),
        ("number", "5.64", "pp", 0.005),
    ]
    assert default_elements("Neither file has duplicates.") == [
        {"type": "text", "text": "Neither file has duplicates."}
    ]


@pytest.mark.parametrize(
    ("element", "answer", "c"),
    [
        ({"type": "number", "value": 82.85, "tol": 0.005, "unit": "%"}, "London was 82.85%.", 1.0),
        ({"type": "number", "value": 82.85, "tol": 0.005, "unit": "%"}, "London was 82.85.", 0.5),
        ({"type": "number", "value": 82.85, "tol": 0.005, "unit": "%"}, "London was 82.86%.", 0.0),
        ({"type": "number", "value": 1964.06, "tol": 0.005, "unit": "GWh"}, "1,964.06 GWh", 1.0),
        ({"type": "number", "value": 5.64, "tol": 0.005, "unit": "pp"}, "5.64%", 0.5),
        ({"type": "number", "value": 6.07, "tol": 0.005, "unit": "km"}, "6.07 kilometres", 1.0),
        ({"type": "date", "value": "2018-04-20"}, "made on 20 April 2018", 1.0),
        ({"type": "yes_no", "value": "no"}, "No. [z] means not applicable.", 1.0),
        ({"type": "yes_no", "value": "no"}, "Yes, it can.", 0.0),
        (
            {"type": "text", "text": "[z] means not applicable"},
            "The code [z] means not applicable.",
            1.0,
        ),
        ({"type": "text", "text": "[z] means not applicable"}, "Revenue grew strongly.", 0.0),
        (
            {"type": "text", "text": "x", "keywords": ["[z]", "not applicable"]},
            "[z] is a code",
            0.5,
        ),
        ({"type": "number", "value": 1.0, "tol": 0.0}, "", 0.0),
    ],
)
def test_score_element(element, answer, c):
    assert score_element(element, answer) == c


def test_locator_tokens_expand_ranges_and_keys():
    toks = locator_tokens("BUS04aii row 3 and cells C26:D26")
    assert {"C26", "D26", "row 3"} <= toks
    assert "tpo453" in locator_tokens("reference=TPO453, made_date and confirmed_date")
    assert "made_date" in locator_tokens("reference=TPO453, made_date")
    assert "page 7" in locator_tokens("page 7, table 2")


def test_parse_reference_evidence_segments_and_files():
    q = _q(sources=["SRC_aaaaaaaaaaaa", "SRC_bbbbbbbbbbbb"])
    manifest = {
        "SRC_aaaaaaaaaaaa": _info("SRC_aaaaaaaaaaaa", "bus04ii.ods"),
        "SRC_bbbbbbbbbbbb": _info("SRC_bbbbbbbbbbbb", "trees.gpkg"),
    }
    pairs = parse_reference_evidence(
        [
            "bus04ii.ods, BUS04aii row 3 and cells C26:D26; Notes, note 4",
            "trees.gpkg, layer.reference",
            "SRC_bbbbbbbbbbbb | page 2 | claim",
        ],
        q,
        manifest,
    )
    assert pairs == [
        ("SRC_aaaaaaaaaaaa", "BUS04aii row 3 and cells C26:D26"),
        ("SRC_aaaaaaaaaaaa", "Notes, note 4"),
        ("SRC_bbbbbbbbbbbb", "layer.reference"),
        ("SRC_bbbbbbbbbbbb", "page 2"),
    ]


def test_score_evidence_levels():
    refs = [("SRC_aaaaaaaaaaaa", "sheet BUS01a, cell H23"), ("SRC_aaaaaaaaaaaa", "cell H29")]
    rec = lambda *ev: AnswerRecord(question_id="q", answer="a", evidence=list(ev))  # noqa: E731
    full = rec("SRC_aaaaaaaaaaaa | sheet BUS01a, cell H23 | x", "SRC_aaaaaaaaaaaa | cell H29 | y")
    assert score_evidence(full, refs) == 1.0
    assert score_evidence(rec("SRC_aaaaaaaaaaaa | sheet BUS01a, cell H23 | x"), refs) == 0.5
    assert score_evidence(rec("SRC_aaaaaaaaaaaa | Notes, note 1 | x"), refs) == 0.5
    assert score_evidence(rec("SRC_cccccccccccc | cell H23 | x"), refs) == 0.0
    assert score_evidence(rec(), refs) == 0.0


def test_score_record_formula():
    q = _q(answer="It was 82.85%.", evidence=["SRC_aaaaaaaaaaaa | cell H23 | c"])
    els = [
        {"type": "number", "value": 82.85, "tol": 0.005, "unit": "%"},
        {"type": "text", "text": "Espoo was higher"},
    ]
    rec = AnswerRecord(
        question_id="q1", answer="It was 82.85%.", evidence=["SRC_aaaaaaaaaaaa | cell H23 | claim"]
    )
    s = score_record(rec, q, els)
    assert s["e"] == 1.0
    assert [x["score"] for x in s["elements"]] == [1.0, 0.0]
    assert s["score"] == 0.5
    no_ev = score_record(rec.model_copy(update={"evidence": []}), q, els)
    assert no_ev["elements"][0]["score"] == pytest.approx(0.8)


def test_aggregation_is_equal_weight_by_case_then_domain():
    pq = pd.DataFrame(
        [
            {"domain": "transport", "case_id": "t1", "score": 1.0},
            {"domain": "transport", "case_id": "t1", "score": 0.0},
            {"domain": "transport", "case_id": "t2", "score": 1.0},
            {"domain": "urban_greenery", "case_id": "g1", "score": 0.0},
        ]
    )
    agg = aggregate(pq)
    assert agg["by_domain"] == {"transport": 0.75, "urban_greenery": 0.0}
    assert agg["overall"] == 0.375


def test_missing_record_scores_zero_and_report_renders():
    qs = [_q("q1", answer="It was 82.85%."), _q("q2", "c2", "urban_greenery", answer="No.")]
    recs = {"q1": AnswerRecord(question_id="q1", answer="It was 82.85%.", evidence=[])}
    df = evaluate(recs, qs, {})
    assert df.set_index("question_id").loc["q2", "score"] == 0.0
    assert bool(df.set_index("question_id").loc["q2", "answered"]) is False
    text = render(df)
    assert "## By domain" in text and "urban_greenery" in text and "## Worst 20" in text
    assert diff(df, df)["delta"].tolist() == [0.0, 0.0]


# ---------- Train acceptance (card T4) ----------


def _train(data_cfg):
    manifest = load_manifest(data_cfg)
    return load_questions(data_cfg, "train"), manifest


def test_train_references_score_one_and_empty_scores_zero(data_cfg):
    qs, manifest = _train(data_cfg)
    perfect = {
        q.id: AnswerRecord(
            question_id=q.id,
            answer=q.reference_answer,
            evidence=reference_evidence_lines(q, manifest),
        )
        for q in qs
    }
    empty = {q.id: AnswerRecord(question_id=q.id, answer="", evidence=[]) for q in qs}
    good = evaluate(perfect, qs, {}, manifest)
    bad = evaluate(empty, qs, {}, manifest)
    assert good["score"].tolist() == [1.0] * 9 and aggregate(good)["overall"] == 1.0
    assert bad["score"].tolist() == [0.0] * 9 and aggregate(bad)["overall"] == 0.0
    assert all(line.startswith("SRC_") for r in perfect.values() for line in r.evidence)


def test_cli_evaluate_reads_run_and_writes_nothing(data_cfg):
    qs, manifest = _train(data_cfg)
    run_dir = data_cfg.artifacts_dir / "runs" / "pytest-evaluate"
    shutil.rmtree(run_dir, ignore_errors=True)
    run_dir.mkdir(parents=True)
    try:
        with open(run_dir / "answers.jsonl", "w") as f:
            for q in qs:
                rec = AnswerRecord(
                    question_id=q.id,
                    answer=q.reference_answer,
                    evidence=reference_evidence_lines(q, manifest),
                )
                f.write(rec.model_dump_json() + "\n")
        (run_dir / "run_manifest.json").write_text(json.dumps({"split": "train"}))
        before = sorted(p.name for p in run_dir.iterdir())
        result = CliRunner().invoke(app, ["evaluate", "pytest-evaluate"])
        assert result.exit_code == 0, result.output
        assert "| 1.0000 | 9 | 9 |" in result.output
        assert sorted(p.name for p in run_dir.iterdir()) == before
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
