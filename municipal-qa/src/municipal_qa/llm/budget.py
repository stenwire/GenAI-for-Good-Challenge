from __future__ import annotations

from municipal_qa.schema import CallRecord


class BudgetExceeded(Exception): ...


class QuestionBudget:
    """Per-question cap on model calls and generated tokens (thinking, retries, checks included)."""

    def __init__(self, max_calls: int = 15, max_tokens: int = 12000):
        self.max_calls = max_calls
        self.max_tokens = max_tokens
        self._records: list[CallRecord] = []

    @property
    def calls_used(self) -> int:
        return len(self._records)

    @property
    def tokens_used(self) -> int:
        return sum(r.generated_tokens for r in self._records)

    @property
    def calls_left(self) -> int:
        return self.max_calls - self.calls_used

    @property
    def tokens_left(self) -> int:
        return self.max_tokens - self.tokens_used

    def can_call(self, num_predict: int) -> bool:
        # Worst case: the model may generate the full num_predict.
        return num_predict > 0 and self.calls_left > 0 and self.tokens_left >= num_predict

    def record(
        self, purpose: str, prompt_tokens: int, generated_tokens: int, elapsed_ms: int
    ) -> None:
        rec = CallRecord(
            purpose=purpose,
            prompt_tokens=prompt_tokens,
            generated_tokens=generated_tokens,
            elapsed_ms=elapsed_ms,
        )
        self._records.append(rec)
        if self.calls_used > self.max_calls or self.tokens_used > self.max_tokens:
            raise BudgetExceeded(
                f"{self.calls_used}/{self.max_calls} calls, "
                f"{self.tokens_used}/{self.max_tokens} tokens after {purpose!r}"
            )

    def records(self) -> list[CallRecord]:
        return list(self._records)
