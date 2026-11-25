from pathlib import Path

import pytest

from municipal_qa.config import PROJECT_ROOT, Config, ConfigError, load_config


@pytest.fixture
def no_env(monkeypatch, tmp_path):
    monkeypatch.delenv("MQA_DATA_DIR", raising=False)
    return tmp_path / "missing.env"


def test_env_var_expanded_from_environment(monkeypatch, no_env, tmp_path):
    monkeypatch.setenv("MQA_DATA_DIR", str(tmp_path))
    cfg = load_config(env_file=no_env)
    assert isinstance(cfg, Config)
    assert cfg.data_dir == tmp_path
    assert cfg.sources_dir == tmp_path / "sources"


def test_env_var_expanded_from_dotenv_file(no_env, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(f"MQA_DATA_DIR={tmp_path / 'data'}\n")
    cfg = load_config(env_file=env_file)
    assert cfg.data_dir == tmp_path / "data"


def test_environment_wins_over_dotenv(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("MQA_DATA_DIR=/from/dotenv\n")
    monkeypatch.setenv("MQA_DATA_DIR", "/from/shell")
    assert load_config(env_file=env_file).data_dir == Path("/from/shell")


def test_missing_data_dir_raises(no_env):
    with pytest.raises(ConfigError, match="MQA_DATA_DIR"):
        load_config(env_file=no_env)


def test_defaults_match_plan(monkeypatch, no_env):
    monkeypatch.setenv("MQA_DATA_DIR", "/d")
    cfg = load_config(env_file=no_env)
    assert cfg.llm.model == "qwen3:1.7b"
    assert cfg.llm.max_params == 3_000_000_000
    assert cfg.llm.think is False
    assert cfg.llm.options == {"temperature": 0, "seed": 42, "num_ctx": 8192}
    assert cfg.llm.num_predict == {
        "route": 400,
        "plan": 700,
        "repair": 700,
        "compose": 600,
        "rewrite": 600,
    }
    assert (cfg.budget.max_calls, cfg.budget.max_generated_tokens) == (15, 12000)
    assert cfg.retrieval.k_final == 8
    assert cfg.artifacts_dir == PROJECT_ROOT / "artifacts"


def test_overrides_deep_merge(monkeypatch, no_env):
    monkeypatch.setenv("MQA_DATA_DIR", "/d")
    cfg = load_config(
        overrides={"llm": {"model": "other:1b"}, "run": {"workers": 3}}, env_file=no_env
    )
    assert cfg.llm.model == "other:1b"
    assert cfg.llm.max_params == 3_000_000_000
    assert cfg.run.workers == 3
    assert cfg.run.resume is True


def test_relative_path_resolves_from_any_cwd(monkeypatch, no_env, tmp_path):
    monkeypatch.setenv("MQA_DATA_DIR", "/d")
    monkeypatch.chdir(tmp_path)
    assert load_config("configs/default.yaml", env_file=no_env).llm.model == "qwen3:1.7b"


@pytest.mark.parametrize(
    "override",
    [
        {"llm": {"max_params": 3_000_000_001}},
        {"llm": {"max_params": 8_000_000_000}},
        {"budget": {"max_calls": 16}},
        {"budget": {"max_generated_tokens": 12_001}},
        {"llm": {"num_predict": {"plan": 12_001}}},
        {"llm": {"num_predict": {"plan": 0}}},
    ],
)
def test_rule_caps_cannot_be_raised(monkeypatch, no_env, override):
    monkeypatch.setenv("MQA_DATA_DIR", "/d")
    with pytest.raises(ConfigError):
        load_config(overrides=override, env_file=no_env)


def test_rule_caps_can_be_tightened(monkeypatch, no_env):
    monkeypatch.setenv("MQA_DATA_DIR", "/d")
    cfg = load_config(
        overrides={"llm": {"max_params": 2_000_000_000}, "budget": {"max_calls": 10}},
        env_file=no_env,
    )
    assert (cfg.llm.max_params, cfg.budget.max_calls) == (2_000_000_000, 10)


@pytest.mark.parametrize(
    "url",
    [
        "http://203.0.113.9:11434",
        "https://api.example.com",
        "http://ollama.internal:11434",
        "http://0.0.0.0:11434",
        "ftp://127.0.0.1:11434",
        "127.0.0.1:11434",
    ],
)
def test_non_local_base_url_refused(monkeypatch, no_env, url):
    monkeypatch.setenv("MQA_DATA_DIR", "/d")
    with pytest.raises(ConfigError, match="base_url"):
        load_config(overrides={"llm": {"base_url": url}}, env_file=no_env)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:11434",
        "http://localhost:11434",
        "http://[::1]:11434",
        "http://127.0.0.2:8080",
    ],
)
def test_loopback_base_url_accepted(monkeypatch, no_env, url):
    monkeypatch.setenv("MQA_DATA_DIR", "/d")
    assert load_config(overrides={"llm": {"base_url": url}}, env_file=no_env).llm.base_url == url


@pytest.mark.parametrize(
    ("override", "field"),
    [
        ({"llm": {"options": {"temperature": 0.7, "seed": 42, "num_ctx": 8192}}}, "temperature"),
        ({"llm": {"options": {"temperature": 0, "seed": "42", "num_ctx": 8192}}}, "seed"),
        ({"llm": {"options": {"temperature": 0, "seed": True, "num_ctx": 8192}}}, "seed"),
        ({"llm": {"think": True}}, "think"),
    ],
)
def test_non_deterministic_llm_settings_refused(monkeypatch, no_env, override, field):
    monkeypatch.setenv("MQA_DATA_DIR", "/d")
    with pytest.raises(ConfigError, match=field):
        load_config(overrides=override, env_file=no_env)


def test_other_seed_and_extra_options_allowed(monkeypatch, no_env):
    monkeypatch.setenv("MQA_DATA_DIR", "/d")
    opts = {"temperature": 0, "seed": 7, "num_ctx": 4096, "top_k": 1}
    assert load_config(overrides={"llm": {"options": opts}}, env_file=no_env).llm.options == opts


@pytest.mark.parametrize(
    ("options_line", "field"),
    [
        ("  options: {seed: 42, num_ctx: 8192}", "temperature"),
        ("  options: {temperature: 0, num_ctx: 8192}", "seed"),
    ],
)
def test_missing_determinism_option_refused(monkeypatch, no_env, tmp_path, options_line, field):
    monkeypatch.setenv("MQA_DATA_DIR", "/d")
    text = (PROJECT_ROOT / "configs" / "default.yaml").read_text()
    original = "  options: {temperature: 0, seed: 42, num_ctx: 8192}"
    assert original in text
    cfg_file = tmp_path / "cfg.yaml"
    cfg_file.write_text(text.replace(original, options_line))
    with pytest.raises(ConfigError, match=field):
        load_config(cfg_file, env_file=no_env)
