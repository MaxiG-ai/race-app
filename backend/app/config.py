"""Environment-driven settings. Twelve-factor: everything via env vars (§8)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://race:race@localhost:5432/race"
    jwt_secret: str = "dev-only-change-me"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 30
    refresh_token_days: int = 30
    cors_origins: list[str] = ["http://localhost:5173"]
    upload_dir: str = "./var/uploads"
    max_upload_bytes: int = 50 * 1024 * 1024
    open_meteo_base_url: str = "https://archive-api.open-meteo.com/v1/archive"

    # Number of points in RecordedActivity.progress_series (§11).
    progress_series_points: int = 200
    # Tolerance applied outward to category bounds when classifying (§9).
    default_category_tolerance_pct: float = 10.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
