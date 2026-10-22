from functools import lru_cache
from pathlib import Path

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = f"sqlite:///{ROOT / 'var' / 'app.db'}"
    data_dir: str = str(ROOT / "data_synthetic")
    alfa_dir: str = ""
    news_dir: str = ""

    llm_mode: str = "mock"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_daily_budget_rub: float = 500.0
    llm_timeout_s: float = 90.0

    embeddings: str = "off"
    session_secret: str = "dev-secret-change-me"
    rss_enabled: bool = False
    seed_on_start: bool = True
    agent_top_n: int = 5
    feed_threshold: float = 0.35
    cors_origins: str = "*"
    host_launch_secret: str = "dev-host-secret-change-me"
    host_demo: bool = True
    embed_allowed_origins: str = "'self'"

    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir)
        return p if p.is_absolute() else ROOT / p

    @property
    def alfa_path(self) -> Path:
        return Path(self.alfa_dir) if self.alfa_dir else self.data_path / "alfa"

    @property
    def news_path(self) -> Path:
        return Path(self.news_dir) if self.news_dir else self.data_path / "news"


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def load_yaml(name: str) -> dict:
    with open(CONFIG_DIR / name, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_factor_rules() -> dict:
    rules: dict = {}
    for path in sorted((CONFIG_DIR / "factors").glob("*.yaml")):
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        for key, value in data.items():
            if isinstance(value, list):
                rules.setdefault(key, []).extend(value)
            elif isinstance(value, dict):
                rules.setdefault(key, {}).update(value)
            else:
                rules[key] = value
    return rules
