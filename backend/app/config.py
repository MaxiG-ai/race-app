"""Environment-driven configuration. Every variable is documented in `.env.example`."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    site_mode: Literal["private", "public"] = "private"
    database_url: str = "postgresql+psycopg://race:race@localhost:5432/race"
    upload_dir: Path = Path("./var/uploads")
    max_upload_bytes: int = 20 * 1024 * 1024
    open_meteo_base_url: str = "https://archive-api.open-meteo.com/v1/archive"
    # Where the HTML pages and /static live. The Docker image copies frontend/ here.
    frontend_dir: Path = Path(__file__).resolve().parents[2] / "frontend"


@lru_cache
def get_settings() -> Settings:
    return Settings()
