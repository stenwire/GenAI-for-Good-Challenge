"""The only module that calls the generative model (Ollama, local HTTP)."""

from __future__ import annotations

import time
from typing import Any

import httpx

from municipal_qa import rules
from municipal_qa.config import Config
from municipal_qa.llm.budget import BudgetExceeded, QuestionBudget
from municipal_qa.schema import CallRecord

MAX_CONNECT_RETRIES = 2


class LLMError(Exception): ...


class ModelTooLarge(LLMError): ...


class RequestNotSent(LLMError):
    """No connection was made, so the model cannot have generated anything."""


class LLMClient:
    def __init__(self, cfg: Config, transport: httpx.BaseTransport | None = None):
        if not rules.is_local_url(cfg.llm.base_url):
            raise LLMError(f"refusing non-local model server {cfg.llm.base_url!r}")
        self.cfg = cfg
        self.llm = cfg.llm
        self._http = httpx.Client(
            base_url=self.llm.base_url,
            timeout=httpx.Timeout(600.0, connect=10.0),
            transport=transport,
        )
        self._model_info: dict | None = None

    def _post(self, path: str, payload: dict) -> dict:
        # Retry only when no response arrived, so nothing was generated.
        for attempt in range(MAX_CONNECT_RETRIES + 1):
            try:
                resp = self._http.post(path, json=payload)
                break
            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                if attempt == MAX_CONNECT_RETRIES:
                    raise RequestNotSent(f"cannot reach Ollama at {self.llm.base_url}: {e}") from e
                time.sleep(0.5 * (attempt + 1))
            except httpx.HTTPError as e:
                raise LLMError(f"POST {path} failed after sending: {e!r}") from e
        if resp.status_code != 200:
            raise LLMError(f"POST {path} -> HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            return resp.json()
        except ValueError as e:
            raise LLMError(f"POST {path} returned non-JSON: {resp.text[:300]}") from e

    def check_model(self) -> dict:
        """Name, digest and total parameter count; raises if above `max_params` or unknown."""
        show = self._post("/api/show", {"model": self.llm.model})
        info = show.get("model_info") or {}
        count = info.get("general.parameter_count")
        if not isinstance(count, int) or count <= 0:
            raise ModelTooLarge(f"{self.llm.model}: total parameter count not reported")
        cap = min(self.llm.max_params, rules.MAX_TOTAL_PARAMS)
        if count > cap:
            raise ModelTooLarge(f"{self.llm.model}: {count:,} parameters > cap {cap:,}")
        digest = None
        try:
            resp = self._http.get("/api/tags")
            for m in resp.json().get("models", []):
                if self.llm.model in (m.get("name"), m.get("model")):
                    digest = m.get("digest")
        except (httpx.HTTPError, ValueError):
            pass
        self._model_info = {
            "name": self.llm.model,
            "digest": digest,
            "parameter_count": count,
            "max_params": cap,
            "quantization": (show.get("details") or {}).get("quantization_level"),
        }
        return self._model_info

    def chat(
        self,
        messages: list[dict],
        purpose: str,
        budget: QuestionBudget,
        json_schema: dict | None = None,
    ) -> tuple[str, CallRecord]:
        if purpose not in self.llm.num_predict:
            raise ValueError(f"no num_predict configured for purpose {purpose!r}")
        num_predict = self.llm.num_predict[purpose]
        if not budget.can_call(num_predict):
            raise BudgetExceeded(
                f"{purpose}: needs up to {num_predict} tokens; "
                f"{budget.calls_left} calls and {budget.tokens_left} tokens left"
            )
        if self._model_info is None:
            self.check_model()
        payload: dict[str, Any] = {
            "model": self.llm.model,
            "messages": messages,
            "stream": False,
            "think": self.llm.think,
            "keep_alive": self.llm.keep_alive,
            "options": {**self.llm.options, "num_predict": num_predict},
        }
        if json_schema is not None:
            payload["format"] = json_schema
        t0 = time.perf_counter()
        try:
            data = self._post("/api/chat", payload)
        except RequestNotSent:
            raise
        except LLMError:
            # The request may have reached the model: charge the worst case.
            budget.record(purpose, 0, num_predict, int((time.perf_counter() - t0) * 1000))
            raise
        elapsed = int((time.perf_counter() - t0) * 1000)
        try:
            prompt_tokens, generated, text = _parse_chat_reply(data, num_predict)
        except (TypeError, ValueError) as e:
            budget.record(purpose, 0, num_predict, elapsed)
            raise LLMError(f"{purpose}: malformed /api/chat reply: {e}") from e
        # Invalid JSON in the content is returned raw; the caller decides whether to retry.
        budget.record(purpose, prompt_tokens, generated, elapsed)
        return text, budget.records()[-1]

    def close(self) -> None:
        self._http.close()


def _count(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise TypeError(f"{name} is {type(value).__name__}")
    n = int(value)
    if n < 0:
        raise ValueError(f"{name} is negative")
    return n


def _parse_chat_reply(data: Any, num_predict: int) -> tuple[int, int, str]:
    """(prompt_tokens, generated_tokens, text); a missing eval_count is charged as num_predict."""
    if not isinstance(data, dict):
        raise TypeError(f"reply is {type(data).__name__}, not a JSON object")
    message = data.get("message") or {}
    if not isinstance(message, dict):
        raise TypeError("message is not an object")
    text = message.get("content") or ""
    if not isinstance(text, str):
        raise TypeError("message.content is not a string")
    eval_count = data.get("eval_count")
    generated = num_predict if eval_count is None else _count(eval_count, "eval_count")
    prompt_tokens = _count(data.get("prompt_eval_count") or 0, "prompt_eval_count")
    return prompt_tokens, generated, text
