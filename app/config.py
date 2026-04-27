from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:password@localhost:5432/afrisignal"
    SYNC_DATABASE_URL: str = "postgresql://postgres:password@localhost:5432/afrisignal"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Anthropic
    ANTHROPIC_API_KEY: str = ""

    # External APIs
    WORLD_BANK_BASE_URL: str = "https://api.worldbank.org/v2"
    NEWS_API_KEY: str = ""
    NEWS_API_BASE_URL: str = "https://newsapi.org/v2"

    # Signal Detection
    ANOMALY_ZSCORE_THRESHOLD: float = 2.0
    SIGNAL_HISTORY_WINDOW: int = 30

    # App
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton — safe to call anywhere."""
    return Settings()