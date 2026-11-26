import json

import httpx
import pytest

from municipal_qa.config import ConfigError, load_config
from municipal_qa.llm.budget import QuestionBudget
from municipal_qa.llm.client import LLMClient, LLMError


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    monkeypatch.setenv("MQA_DATA_DIR", str(tmp_path))
    return load_config(env_file=tmp_path / "none.env")


def _fake(caps):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append((request.url.path, body))
        if request.url.path == "/api/show":
            info = {"general.architecture": "bert", "general.parameter_count": 277451520}
            return httpx.Response(200, json={"capabilities": caps, "model_info": info})
        if request.url.path == "/api/embed":
            return httpx.Response(
                200, json={"embeddings": [[float(len(t)), 1.0] for t in body["input"]]}
            )
        return httpx.Response(404)

    return handler, seen


def test_default_embedder_is_an_ollama_encoder_with_no_reranker(cfg):
    assert cfg.retrieval.embedding_model == "granite-embedding:278m"
    assert cfg.retrieval.reranker_model is None


def test_embed_batches_and_uses_no_question_budget(cfg):
    cfg = cfg.model_copy(update={"retrieval": cfg.retrieval.model_copy(update={"embed_batch": 2})})
    handler, seen = _fake(["embedding"])
    client = LLMClient(cfg, transport=httpx.MockTransport(handler))
    budget = QuestionBudget()
    vecs = client.embed(["a", "bb", "ccc"])
    assert vecs == [[1.0, 1.0], [2.0, 1.0], [3.0, 1.0]]
    embeds = [b for p, b in seen if p == "/api/embed"]
    assert [b["input"] for b in embeds] == [["a", "bb"], ["ccc"]]
    assert all(b["model"] == "granite-embedding:278m" and b["truncate"] for b in embeds)
    assert not any(p == "/api/chat" for p, _ in seen) and budget.calls_used == 0


@pytest.mark.parametrize("caps", [["completion"], ["embedding", "completion"], []])
def test_embedder_that_can_generate_is_refused(cfg, caps):
    handler, seen = _fake(caps)
    client = LLMClient(cfg, transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError, match="embedding-only encoder"):
        client.embed(["x"])
    assert not any(p == "/api/embed" for p, _ in seen)


def test_generative_model_cannot_be_the_embedder(tmp_path, monkeypatch):
    monkeypatch.setenv("MQA_DATA_DIR", str(tmp_path))
    with pytest.raises(ConfigError, match="must be an encoder"):
        load_config(
            env_file=tmp_path / "none.env",
            overrides={"retrieval": {"embedding_model": "qwen3:1.7b"}},
        )
