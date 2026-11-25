import re

import pytest
from typer.testing import CliRunner

from municipal_qa.cli import app
from municipal_qa.data.manifest import load_manifest
from municipal_qa.data.questions import load_questions
from municipal_qa.schema import AnswerRecord, Question
from municipal_qa.submission.validator import parse_submission, validate_submission
from municipal_qa.submission.writer import placeholder_record, write_submission

IDS = ["0a-1", "0b-2", "0c-3"]
SRC = {"0a-1": ["SRC_aaaaaaaaaaaa"], "0b-2": ["SRC_bbbbbbbbbbbb"], "0c-3": ["SRC_cccccccccccc"]}


def _records() -> dict[str, AnswerRecord]:
    return {
        "0a-1": AnswerRecord(
            question_id="0a-1",
            answer="No. [z] means not applicable.\n\nSecond paragraph.",
            evidence=["SRC_aaaaaaaaaaaa | sheet BUS01a, cells H23:T29 | [z] marks not applicable"],
        ),
        "0b-2": AnswerRecord(
            question_id="0b-2",
            answer="3.48 ha.",
            evidence=[
                "SRC_bbbbbbbbbbbb | properties.reference=1912/A1 | Area 3.48 ha in EPSG:27700",
                "SRC_bbbbbbbbbbbb | layer zones | Geometry used as published",
            ],
        ),
        "0c-3": AnswerRecord(
            question_id="0c-3",
            answer="35 days.",
            evidence=["SRC_cccccccccccc | reference=TPO453, made_date | Made 2001-01-01"],
        ),
    }


def _starter_save(predictions: dict, expected: list[str]) -> str:
    # Reference: StarterNotebook.ipynb save_submission(), minus file IO.
    chunks = ["# Submission\n"]
    for qid in expected:
        item = predictions[qid]
        answer = item["answer"].strip()
        chunks.append(
            f"\n## {qid}\n\n{answer}\n\n### Evidence\n"
            + "\n".join("- " + e for e in item["evidence"])
            + "\n"
        )
    text = "".join(chunks)
    assert re.findall(r"^## (.+)$", text, re.M) == expected
    return text


def test_round_trip(tmp_path):
    recs = _records()
    out = write_submission(recs, IDS, tmp_path / "Submission.md")
    assert validate_submission(out, IDS, SRC) == []
    _, blocks = parse_submission(out.read_text(encoding="utf-8"))
    for b in blocks:
        assert b.answer == recs[b.qid].answer
        assert [e[2:] for e in b.evidence] == recs[b.qid].evidence


def test_byte_compatible_with_starter(tmp_path):
    recs = _records()
    out = write_submission(recs, IDS, tmp_path / "S.md")
    preds = {k: {"answer": v.answer, "evidence": v.evidence} for k, v in recs.items()}
    assert out.read_bytes() == _starter_save(preds, IDS).encode("utf-8")


def test_writer_refuses_bad_records(tmp_path):
    recs = _records()
    recs["0a-1"].answer = "  "
    with pytest.raises(ValueError, match="blank"):
        write_submission(recs, IDS, tmp_path / "S.md")
    recs = _records()
    recs["0a-1"].answer = "ok\n## injected"
    with pytest.raises(ValueError, match="## "):
        write_submission(recs, IDS, tmp_path / "S.md")
    recs = _records()
    del recs["0c-3"]
    with pytest.raises(ValueError, match="missing 1"):
        write_submission(recs, IDS, tmp_path / "S.md")


def _good_text(tmp_path) -> str:
    return write_submission(_records(), IDS, tmp_path / "g.md").read_text(encoding="utf-8")


BROKEN = [
    ("bad header", lambda t: t.replace("# Submission\n", "# Answers\n", 1), "first line"),
    ("missing ID", lambda t: t[: t.index("\n## 0c-3")] + "\n", "missing"),
    (
        "wrong order",
        lambda t: (
            t.replace("## 0a-1", "## TMP")
            .replace("## 0b-2", "## 0a-1")
            .replace("## TMP", "## 0b-2")
        ),
        "out of order",
    ),
    ("blank answer", lambda t: t.replace("35 days.", ""), "blank answer"),
    ("## inside answer", lambda t: t.replace("3.48 ha.", "3.48 ha.\n## note"), "not Test IDs"),
    (
        "no evidence heading",
        lambda t: t.replace("35 days.\n\n### Evidence", "35 days."),
        "Evidence",
    ),
    (
        "malformed evidence",
        lambda t: t.replace("- SRC_cccccccccccc | ", "- trees.csv | "),
        "malformed",
    ),
    ("CRLF line endings", lambda t: t.replace("\n", "\r\n"), "\\r"),
]


@pytest.mark.parametrize(("name", "mutate", "expect"), BROKEN, ids=[b[0] for b in BROKEN])
def test_validator_catches_malformation(tmp_path, name, mutate, expect):
    bad = tmp_path / "bad.md"
    bad.write_bytes(mutate(_good_text(tmp_path)).encode("utf-8"))
    problems = validate_submission(bad, IDS, SRC)
    assert problems, name
    assert any(expect in p for p in problems), problems


def test_non_utf8_rejected(tmp_path):
    bad = tmp_path / "bad.md"
    bad.write_bytes(_good_text(tmp_path).encode("utf-8") + b"\xff\xfe")
    assert validate_submission(bad, IDS) and "UTF-8" in validate_submission(bad, IDS)[0]


def test_foreign_source_is_warning_unknown_is_error(tmp_path):
    t = _good_text(tmp_path).replace("SRC_cccccccccccc | reference", "SRC_aaaaaaaaaaaa | reference")
    p = tmp_path / "w.md"
    p.write_text(t, encoding="utf-8")
    warns: list[str] = []
    assert validate_submission(p, IDS, SRC, {"SRC_aaaaaaaaaaaa"}, warns) == []
    assert any("not in its Source_IDs" in w for w in warns)
    p.write_text(t.replace("SRC_aaaaaaaaaaaa | reference", "SRC_dddddddddddd | reference"))
    assert any(
        "unknown source" in x for x in validate_submission(p, IDS, SRC, {"SRC_aaaaaaaaaaaa"})
    )


def test_placeholder_record_is_valid(tmp_path):
    q = Question(
        id="0a-1",
        case_id="c",
        domain="transport",
        question="How many\nstops?",
        context="",
        source_ids=["SRC_aaaaaaaaaaaa"],
        split="test",
    )
    rec = placeholder_record(q)
    assert rec.answer.endswith("How many stops?") and rec.fallback_used and rec.evidence == []


def test_placeholder_for_all_600_test_ids(data_cfg, tmp_path):
    test = load_questions(data_cfg, "test")
    ids = [q.id for q in test]
    out = write_submission({q.id: placeholder_record(q) for q in test}, ids, tmp_path / "S.md")
    warns: list[str] = []
    src = {q.id: q.source_ids for q in test}
    assert validate_submission(out, ids, src, set(load_manifest(data_cfg)), warns) == []
    assert len(warns) == 600
    result = CliRunner().invoke(app, ["validate", str(out)])
    assert result.exit_code == 0, result.output
    assert "valid" in result.output
