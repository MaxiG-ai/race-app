"""Environment-driven configuration. Every variable is documented in `.env.example`."""

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    site_mode: Literal["private", "public"] = "private"
    # DATABASE_URL wins when set. Otherwise it is built from the same POSTGRES_* variables
    # the db container reads, so the password lives only in .env.
    database_url: str = ""
    postgres_user: str = "race"
    postgres_password: str = "race"
    postgres_host: str = "localhost"
    postgres_db: str = "race"
    upload_dir: Path = Path("./var/uploads")
    max_upload_bytes: int = 20 * 1024 * 1024
    open_meteo_base_url: str = "https://archive-api.open-meteo.com/v1/archive"
    # Where the HTML pages and /static live. The Docker image copies frontend/ here.
    frontend_dir: Path = Path(__file__).resolve().parents[2] / "frontend"

    @model_validator(mode="after")
    def _build_database_url(self) -> "Settings":
        if not self.database_url:
            self.database_url = (
                f"postgresql+psycopg://{quote(self.postgres_user)}:"
                f"{quote(self.postgres_password, safe='')}@{self.postgres_host}:5432/"
                f"{self.postgres_db}"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
