import pytest

from municipal_qa.llm.budget import BudgetExceeded, QuestionBudget


def test_defaults_are_competition_caps():
    b = QuestionBudget()
    assert (b.max_calls, b.max_tokens) == (15, 12000)


def test_15th_call_allowed_16th_refused():
    b = QuestionBudget()
    for _ in range(14):
        b.record("plan", 100, 1, 5)
    assert b.can_call(1)  # the 15th
    b.record("compose", 100, 1, 5)
    assert b.calls_used == 15
    assert not b.can_call(1)  # the 16th


def test_12000_tokens_allowed_12001_refused():
    b = QuestionBudget()
    assert b.can_call(12000)
    assert not b.can_call(12001)
    b.record("compose", 50, 11000, 5)
    assert b.can_call(1000)
    assert not b.can_call(1001)
    b.record("rewrite", 50, 1000, 5)
    assert b.tokens_used == 12000
    assert not b.can_call(1)


def test_can_call_uses_worst_case_not_expected_tokens():
    b = QuestionBudget()
    b.record("route", 10, 11500, 1)
    assert not b.can_call(600)  # might generate 600 > 500 left
    assert b.can_call(500)


def test_record_raises_when_tokens_exceeded():
    b = QuestionBudget()
    b.record("plan", 10, 12000, 1)
    with pytest.raises(BudgetExceeded):
        b.record("compose", 10, 1, 1)


def test_record_raises_on_16th_call():
    b = QuestionBudget()
    for _ in range(15):
        b.record("plan", 1, 0, 1)
    with pytest.raises(BudgetExceeded):
        b.record("plan", 1, 0, 1)


def test_records_are_call_records_and_a_copy():
    b = QuestionBudget()
    b.record("route", 120, 40, 33)
    recs = b.records()
    assert recs[0].model_dump() == {
        "purpose": "route",
        "prompt_tokens": 120,
        "generated_tokens": 40,
        "elapsed_ms": 33,
    }
    recs.clear()
    assert b.calls_used == 1
