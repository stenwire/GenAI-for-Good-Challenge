import pytest

from municipal_qa.config import Config, ConfigError, load_config


@pytest.fixture(scope="session")
def data_cfg() -> Config:
    """Real challenge data via MQA_DATA_DIR; tests using it skip on a machine without data."""
    try:
        cfg = load_config()
    except ConfigError as e:
        pytest.skip(str(e))
    if not (cfg.data_dir / "Test.csv").exists():
        pytest.skip(f"no Test.csv under {cfg.data_dir}")
    return cfg
