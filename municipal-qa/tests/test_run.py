import csv
import json
import shutil

import pytest
from typer.testing import CliRunner

from municipal_qa.cli import app
from municipal_qa.config import load_config
from municipal_qa.data.questions import load_questions
from municipal_qa.pipeline import run as runner
from municipal_qa.schema import AnswerRecord, Question


def _qs(n):
    return [
        Question(
            id=f"q{i}",
            case_id="c",
            domain="transport",
            question=f"Q{i}?",
            context="",
            source_ids=["SRC_aaaaaaaaaaaa"],
            split="dev",
        )
        for i in range(n)
    ]


def _answer(q, calls=1, tokens=40, fail=False):
    return AnswerRecord(
        question_id=q.id,
        answer=f"A for {q.id}",
        evidence=["SRC_aaaaaaaaaaaa | row 1 | x"],
        total_calls=calls,
        total_generated_tokens=tokens,
        fallback_used=fail,
        error="compose: LLMError" if fail else None,
    )


def test_run_is_resumable_and_logs_budget_and_errors(tmp_path):
    seen = []

    def answer(q):
        seen.append(q.id)
        return _answer(q, calls=1 + len(seen) % 3, tokens=100 * len(seen), fail=q.id == "q1")

    assert runner.run_questions(_qs(2), answer, tmp_path) == 2
    assert runner.run_questions(_qs(4), answer, tmp_path) == 2
    assert seen == ["q0", "q1", "q2", "q3"]
    assert runner.done_ids(tmp_path) == {"q0", "q1", "q2", "q3"}
    rows, max_calls, max_tokens = runner.write_budget_csv(tmp_path)
    assert (rows, max_calls, max_tokens) == (4, 3, 400)
    with open(tmp_path / "budget.csv") as f:
        table = list(csv.DictReader(f))
    assert table[0] == {"id": "q0", "calls": "2", "generated_tokens": "100"}
    errors = [json.loads(x) for x in (tmp_path / "errors.jsonl").read_text().splitlines()]
    assert errors == [{"question_id": "q1", "error": "compose: LLMError", "fallback_used": True}]
    assert set(runner.load_run_records(tmp_path)) == {"q0", "q1", "q2", "q3"}


def test_select_ids_and_limit():
    qs = _qs(5)
    assert [q.id for q in runner.select(qs, "q3, q1", None)] == ["q3", "q1"]
    assert [q.id for q in runner.select(qs, None, 2)] == ["q0", "q1"]
    with pytest.raises(KeyError, match="q9"):
        runner.select(qs, "q9", None)


def test_manifest_records_git_model_seed_and_versions(tmp_path, monkeypatch):
    monkeypatch.setenv("MQA_DATA_DIR", str(tmp_path))
    cfg = load_config(env_file=tmp_path / "none.env")
    model = {"name": "qwen3:1.7b", "digest": "abc", "parameter_count": 2031739904}
    runner.write_manifest(tmp_path, cfg, "dev", model, {"status": "running"})
    runner.write_manifest(tmp_path, cfg, "dev", model, {"status": "complete", "answers": 3})
    m = json.loads((tmp_path / "run_manifest.json").read_text())
    assert m["split"] == "dev" and m["seed"] == 42 and m["model"]["digest"] == "abc"
    assert m["status"] == "complete" and m["answers"] == 3
    assert len(m["git"]["commit"]) == 40 and isinstance(m["git"]["dirty"], bool)
    assert m["config"]["llm"]["options"]["temperature"] == 0
    assert m["packages"]["pandas"] != "not installed"
    assert runner.new_run_id().endswith(m["git"]["short"])


def test_submit_refuses_incomplete_and_writes_valid_file(data_cfg):
    test = load_questions(data_cfg, "test")
    sid = {q.id: q.source_ids[0] for q in test}

    def real(q):
        return AnswerRecord(
            question_id=q.id, answer="Placeholder.", evidence=[f"{sid[q.id]} | file | consulted"]
        )

    run_dir = data_cfg.artifacts_dir / "runs" / "pytest-submit"
    shutil.rmtree(run_dir, ignore_errors=True)
    try:
        runner.run_questions(test[:10], real, run_dir)
        result = CliRunner().invoke(app, ["submit", "pytest-submit"])
        assert result.exit_code == 1 and "lacks 590 Test answers" in result.output
        assert runner.run_questions(test, real, run_dir) == 590
        result = CliRunner().invoke(app, ["submit", "pytest-submit"])
        assert result.exit_code == 0, result.output
        assert "valid" in result.output
        assert CliRunner().invoke(app, ["validate", str(run_dir / "Submission.md")]).exit_code == 0
        shown = CliRunner().invoke(app, ["show", "pytest-submit", test[20].id])
        assert shown.exit_code == 0 and "Placeholder." in shown.output
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
