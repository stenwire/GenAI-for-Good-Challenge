from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "configs" / "default.yaml"
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ConfigError(Exception): ...


class LLMConfig(BaseModel):
    provider: str = "ollama"
    base_url: str = "http://127.0.0.1:11434"
    model: str = "qwen3:1.7b"
    max_params: int = 3_000_000_000
    options: dict[str, Any] = Field(
        default_factory=lambda: {"temperature": 0, "seed": 42, "num_ctx": 8192}
    )
    think: bool = False
    keep_alive: str = "10m"
    num_predict: dict[str, int] = Field(default_factory=dict)


class BudgetConfig(BaseModel):
    max_calls: int = 15
    max_generated_tokens: int = 12_000
    reserve_tokens_for_compose: int = 800


class RetrievalConfig(BaseModel):
    embedding_model: str
    reranker_model: str
    k_bm25: int = 30
    k_dense: int = 30
    k_final: int = 8
    chunk_chars: int = 900
    chunk_overlap: int = 150


class PipelineConfig(BaseModel):
    max_repairs: int = 2
    max_extra_plans: int = 2
    enable_rewrite: bool = True


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
    cfg = Config.model_validate(_expand(raw, env))
    if not cfg.artifacts_dir.is_absolute():
        cfg.artifacts_dir = PROJECT_ROOT / cfg.artifacts_dir
    return cfg
