from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, Field, ValidationError, field_validator

from municipal_qa import rules

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "configs" / "default.yaml"
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ConfigError(Exception): ...


class LLMConfig(BaseModel):
    provider: str = "ollama"
    base_url: str = "http://127.0.0.1:11434"
    model: str = "qwen3:1.7b"
    max_params: int = rules.MAX_TOTAL_PARAMS
    options: dict[str, Any] = Field(
        default_factory=lambda: {"temperature": 0, "seed": 42, "num_ctx": 8192}
    )
    think: bool = False
    keep_alive: str = "10m"
    num_predict: dict[str, int] = Field(default_factory=dict)

    @field_validator("options")
    @classmethod
    def _deterministic(cls, v: dict[str, Any]) -> dict[str, Any]:
        if v.get("temperature") != 0:
            raise ValueError("options.temperature must be 0 (plan §2 reproducibility)")
        if isinstance(v.get("seed"), bool) or not isinstance(v.get("seed"), int):
            raise ValueError("options.seed must be a fixed integer (plan §2 reproducibility)")
        return v

    @field_validator("think")
    @classmethod
    def _no_thinking(cls, v: bool) -> bool:
        if v:
            raise ValueError("think must be false; thinking tokens count against the budget")
        return v

    @field_validator("base_url")
    @classmethod
    def _local_only(cls, v: str) -> str:
        if not rules.is_local_url(v):
            raise ValueError(f"base_url must point at this machine (loopback), got {v!r}")
        return v

    @field_validator("max_params")
    @classmethod
    def _params_within_rule(cls, v: int) -> int:
        if not 0 < v <= rules.MAX_TOTAL_PARAMS:
            raise ValueError(f"max_params must be in 1..{rules.MAX_TOTAL_PARAMS:,}")
        return v

    @field_validator("num_predict")
    @classmethod
    def _num_predict_within_rule(cls, v: dict[str, int]) -> dict[str, int]:
        bad = {k: n for k, n in v.items() if not 0 < n <= rules.MAX_GENERATED_TOKENS_PER_QUESTION}
        if bad:
            raise ValueError(f"num_predict out of range: {bad}")
        return v


class BudgetConfig(BaseModel):
    max_calls: int = rules.MAX_CALLS_PER_QUESTION
    max_generated_tokens: int = rules.MAX_GENERATED_TOKENS_PER_QUESTION
    reserve_tokens_for_compose: int = 800

    @field_validator("max_calls")
    @classmethod
    def _calls_within_rule(cls, v: int) -> int:
        if not 0 < v <= rules.MAX_CALLS_PER_QUESTION:
            raise ValueError(f"max_calls must be in 1..{rules.MAX_CALLS_PER_QUESTION}")
        return v

    @field_validator("max_generated_tokens")
    @classmethod
    def _tokens_within_rule(cls, v: int) -> int:
        if not 0 < v <= rules.MAX_GENERATED_TOKENS_PER_QUESTION:
            raise ValueError(
                f"max_generated_tokens must be in 1..{rules.MAX_GENERATED_TOKENS_PER_QUESTION}"
            )
        return v


class RetrievalConfig(BaseModel):
    embedding_model: str | None = None  # Ollama encoder; None = BM25 only
    reranker_model: str | None = None
    embed_batch: int = 64
    rrf_k: int = 60
    max_per_source: int = 3
    k_bm25: int = 30
    k_dense: int = 30
    k_final: int = 8
    docs_first: int = 3
    chunk_chars: int = 900
    chunk_overlap: int = 150


class PipelineConfig(BaseModel):
    mode: Literal["planner", "baseline"] = "planner"
    max_repairs: int = 2
    max_extra_plans: int = 2
    enable_rewrite: bool = True
    evidence_from_top: int = 4


class RunConfig(BaseModel):
    workers: int = 1
    resume: bool = True


class Config(BaseModel):
    data_dir: Path
    artifacts_dir: Path
    llm: LLMConfig
    budget: BudgetConfig
    retrieval: RetrievalConfig
    pipeline: PipelineConfig
    run: RunConfig

    @field_validator("retrieval")
    @classmethod
    def _embedder_is_not_the_llm(cls, v: RetrievalConfig, info) -> RetrievalConfig:
        llm = info.data.get("llm")
        if v.embedding_model and llm and v.embedding_model == llm.model:
            raise ValueError(
                "retrieval.embedding_model must be an encoder, not the generative model"
            )
        return v

    @property
    def sources_dir(self) -> Path:
        return self.data_dir / "sources"


def _expand(value: Any, env: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {k: _expand(v, env) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v, env) for v in value]
    if isinstance(value, str):

        def sub(m: re.Match) -> str:
            name = m.group(1)
            if not env.get(name):
                raise ConfigError(f"{name} is not set (environment or {DEFAULT_ENV_FILE.name})")
            return env[name]

        return _VAR.sub(sub, value)
    return value


def _merge(base: dict, overrides: dict) -> dict:
    out = dict(base)
    for k, v in overrides.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(
    path: str | Path = DEFAULT_CONFIG,
    overrides: dict | None = None,
    env_file: str | Path | None = DEFAULT_ENV_FILE,
) -> Config:
    path = Path(path)
    if not path.is_absolute() and not path.exists():
        path = PROJECT_ROOT / path
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = _merge(raw, overrides or {})
    # Real environment wins over .env, as with a shell export.
    env = {k: v for k, v in dotenv_values(env_file).items() if v} if env_file else {}
    env.update(os.environ)
    try:
        cfg = Config.model_validate(_expand(raw, env))
    except ValidationError as e:
        raise ConfigError(str(e)) from e
    if not cfg.artifacts_dir.is_absolute():
        cfg.artifacts_dir = PROJECT_ROOT / cfg.artifacts_dir
    return cfg
