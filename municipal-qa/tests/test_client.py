import json

import httpx
import pytest

from municipal_qa.config import load_config
from municipal_qa.llm.budget import BudgetExceeded, QuestionBudget
from municipal_qa.llm.client import LLMClient, LLMError, ModelTooLarge, RequestNotSent

STARTER_COUNT = 2_031_739_904


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    monkeypatch.setenv("MQA_DATA_DIR", str(tmp_path))
    return load_config(env_file=tmp_path / "none.env")


class FakeOllama:
    def __init__(self, params=STARTER_COUNT, reply="Answer.", eval_count=42, connect_failures=0):
        self.params = params
        self.reply = reply
        self.eval_count = eval_count
        self.connect_failures = connect_failures
        self.requests: list[tuple[str, dict | None]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.url.path, body))
        if self.connect_failures:
            self.connect_failures -= 1
            raise httpx.ConnectError("refused", request=request)
        if request.url.path == "/api/show":
            info = {} if self.params is None else {"general.parameter_count": self.params}
            return httpx.Response(
                200, json={"model_info": info, "details": {"quantization_level": "Q4_K_M"}}
            )
        if request.url.path == "/api/tags":
            return httpx.Response(
                200, json={"models": [{"name": "qwen3:1.7b", "digest": "abc123"}]}
            )
        if request.url.path == "/api/chat":
            return httpx.Response(
                200,
                json={
                    "message": {"role": "assistant", "content": self.reply},
                    "prompt_eval_count": 321,
                    "eval_count": self.eval_count,
                },
            )
        return httpx.Response(404)

    def chats(self):
        return [b for p, b in self.requests if p == "/api/chat"]


def _client(cfg, fake):
    return LLMClient(cfg, transport=httpx.MockTransport(fake))


def test_check_model_reports_name_digest_count(cfg):
    info = _client(cfg, FakeOllama()).check_model()
    assert info["name"] == "qwen3:1.7b"
    assert info["digest"] == "abc123"
    assert info["parameter_count"] == STARTER_COUNT
    assert info["parameter_count"] <= 3_000_000_000


def test_check_model_cap_is_inclusive(cfg):
    assert _client(cfg, FakeOllama(params=3_000_000_000)).check_model()["parameter_count"]
    with pytest.raises(ModelTooLarge, match="3,000,000,001"):
        _client(cfg, FakeOllama(params=3_000_000_001)).check_model()


def test_unknown_parameter_count_aborts(cfg):
    with pytest.raises(ModelTooLarge, match="not reported"):
        _client(cfg, FakeOllama(params=None)).check_model()


def test_chat_payload_and_accounting(cfg):
    fake = FakeOllama()
    budget = QuestionBudget()
    schema = {"type": "object", "properties": {"qtype": {"type": "string"}}}
    text, rec = _client(cfg, fake).chat(
        [{"role": "user", "content": "hi"}], "plan", budget, json_schema=schema
    )
    body = fake.chats()[0]
    assert body["stream"] is False and body["think"] is False
    assert body["options"] == {"temperature": 0, "seed": 42, "num_ctx": 8192, "num_predict": 700}
    assert body["format"] == schema and body["model"] == "qwen3:1.7b"
    assert text == "Answer."
    assert (rec.purpose, rec.prompt_tokens, rec.generated_tokens) == ("plan", 321, 42)
    assert (budget.calls_used, budget.tokens_used) == (1, 42)


def test_no_format_without_schema(cfg):
    fake = FakeOllama()
    _client(cfg, fake).chat([{"role": "user", "content": "x"}], "compose", QuestionBudget())
    assert "format" not in fake.chats()[0]
    assert fake.chats()[0]["options"]["num_predict"] == 600


def test_refused_before_sending_when_budget_short(cfg):
    fake = FakeOllama()
    budget = QuestionBudget(max_tokens=699)
    with pytest.raises(BudgetExceeded):
        _client(cfg, fake).chat([{"role": "user", "content": "x"}], "plan", budget)
    assert fake.requests == []
    assert budget.calls_used == 0


def test_refused_when_calls_exhausted(cfg):
    fake = FakeOllama()
    budget = QuestionBudget(max_calls=1)
    client = _client(cfg, fake)
    client.chat([{"role": "user", "content": "x"}], "route", budget)
    with pytest.raises(BudgetExceeded):
        client.chat([{"role": "user", "content": "x"}], "route", budget)
    assert len(fake.chats()) == 1


def test_oversized_model_never_generates(cfg):
    fake = FakeOllama(params=8_000_000_000)
    with pytest.raises(ModelTooLarge):
        _client(cfg, fake).chat([{"role": "user", "content": "x"}], "route", QuestionBudget())
    assert fake.chats() == []


def test_connection_retries_are_not_counted(cfg, monkeypatch):
    monkeypatch.setattr("municipal_qa.llm.client.time.sleep", lambda s: None)
    fake = FakeOllama(connect_failures=2)
    budget = QuestionBudget()
    client = _client(cfg, fake)
    client.check_model()
    assert budget.calls_used == 0
    client.chat([{"role": "user", "content": "x"}], "route", budget)
    assert budget.calls_used == 1


def test_connection_gives_up_after_two_retries(cfg, monkeypatch):
    monkeypatch.setattr("municipal_qa.llm.client.time.sleep", lambda s: None)
    fake = FakeOllama(connect_failures=3)
    with pytest.raises(LLMError, match="cannot reach Ollama"):
        _client(cfg, fake).check_model()
    assert len(fake.requests) == 3


def test_invalid_json_returned_raw_and_counted(cfg):
    fake = FakeOllama(reply="{not json")
    budget = QuestionBudget()
    text, _ = _client(cfg, fake).chat(
        [{"role": "user", "content": "x"}], "route", budget, json_schema={"type": "object"}
    )
    assert text == "{not json"
    assert budget.calls_used == 1


def test_unknown_purpose_rejected(cfg):
    with pytest.raises(ValueError, match="purpose"):
        _client(cfg, FakeOllama()).chat([], "summarise", QuestionBudget())


def _failing_chat(kind):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"model_info": {"general.parameter_count": 1}})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        if kind == "500":
            return httpx.Response(500, text="runner crashed")
        if kind == "nojson":
            return httpx.Response(200, text="<html>proxy</html>")
        if kind == "no_eval":
            return httpx.Response(200, json={"message": {"content": "x"}})
        if kind == "timeout":
            raise httpx.ReadTimeout("still generating", request=request)
        raise httpx.RemoteProtocolError("connection dropped mid-response", request=request)

    return handler


@pytest.mark.parametrize("kind", ["500", "timeout", "protocol", "nojson"])
def test_failure_after_sending_is_charged_at_worst_case(cfg, kind):
    budget = QuestionBudget()
    client = LLMClient(cfg, transport=httpx.MockTransport(_failing_chat(kind)))
    with pytest.raises(LLMError):
        client.chat([{"role": "user", "content": "x"}], "plan", budget)
    assert (budget.calls_used, budget.tokens_used) == (1, 700)
    assert budget.records()[0].purpose == "plan"


def test_missing_eval_count_is_charged_at_worst_case(cfg):
    budget = QuestionBudget()
    client = LLMClient(cfg, transport=httpx.MockTransport(_failing_chat("no_eval")))
    client.chat([{"role": "user", "content": "x"}], "compose", budget)
    assert (budget.calls_used, budget.tokens_used) == (1, 600)


def test_retry_loop_after_failures_stops_at_the_caps(cfg):
    budget = QuestionBudget()
    client = LLMClient(cfg, transport=httpx.MockTransport(_failing_chat("timeout")))
    sent = 0
    for _ in range(40):
        try:
            client.chat([{"role": "user", "content": "x"}], "plan", budget)
        except BudgetExceeded:
            break
        except LLMError:
            sent += 1
    assert sent == 15  # 12,000 // 700 = 17 by tokens, 15 by calls
    assert budget.calls_used == 15 and budget.tokens_used <= 12000


def test_unreachable_server_is_not_charged(cfg, monkeypatch):
    monkeypatch.setattr("municipal_qa.llm.client.time.sleep", lambda s: None)
    client = _client(cfg, FakeOllama())
    client.check_model()
    budget = QuestionBudget()

    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    client._http = httpx.Client(
        base_url="http://127.0.0.1:11434", transport=httpx.MockTransport(refuse)
    )
    with pytest.raises(RequestNotSent):
        client.chat([{"role": "user", "content": "x"}], "plan", budget)
    assert budget.calls_used == 0
