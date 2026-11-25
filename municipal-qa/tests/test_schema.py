import pytest
from pydantic import ValidationError

from municipal_qa.schema import (
    AnswerRecord,
    Conventions,
    Domain,
    Fact,
    Locator,
    Plan,
    Question,
    QuestionType,
    Route,
    Step,
    SubQuestion,
    ToolResult,
)


def test_question_parses_domain_and_split():
    q = Question(
        id="abc",
        case_id="c1",
        domain="transport",
        question="Q?",
        context="ctx",
        source_ids=["SRC_000000000000"],
        split="test",
    )
    assert q.domain is Domain.TRANSPORT
    assert q.reference_answer is None
    with pytest.raises(ValidationError):
        Question(**{**q.model_dump(), "split": "holdout"})


def test_locator_rejects_unknown_kind():
    Locator(source_id="SRC_1", kind="cell", text="sheet BUS01a, cell H23")
    with pytest.raises(ValidationError):
        Locator(source_id="SRC_1", kind="paragraph", text="x")


def test_answer_record_json_round_trip():
    loc = Locator(source_id="SRC_7bc924533043", kind="cell", text="sheet BUS01a, cell H23")
    fact = Fact(
        name="pct", value=82.85, unit="%", display="82.85%", claim="London 82.85%", locators=[loc]
    )
    rec = AnswerRecord(
        question_id="q1",
        answer="82.85%.",
        evidence=["SRC_7bc924533043 | sheet BUS01a, cell H23 | London 82.85%"],
        route=Route(
            qtype=QuestionType.COMPARE,
            sub_questions=[SubQuestion(text="pct", expects="number", unit="%")],
            primary_source_ids=["SRC_7bc924533043"],
        ),
        plan=Plan(steps=[Step(op="get_cell", args={"ref": "H23"}, save_as="h23")]),
        results=[ToolResult(ok=True, op="get_cell", args={"ref": "H23"}, facts=[fact])],
    )
    again = AnswerRecord.model_validate_json(rec.model_dump_json())
    assert again == rec
    assert again.results[0].facts[0].locators[0].text == "sheet BUS01a, cell H23"


def test_conventions_defaults():
    c = Conventions(raw_context="")
    assert c.value_mode == "unspecified"
    assert (c.decimals, c.rounding, c.round_at_end) == (2, "half_up", True)
    assert c.repair_geometry is False
    assert c.preserve_ids_as_strings is True
    assert c.decimals_overrides == {}
