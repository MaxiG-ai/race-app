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
    # TEMPORARY: treat every visitor as the owner. Only for deployments where something else
    # (e.g. a platform proxy) already authenticates visitors. Remove or set false to restore login.
    disable_login: bool = False
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
    # The site's own address, e.g. https://race.example.com. Used for the Strava callback
    # and the link in the Strava tagline; without it, the request's address is used.
    public_base_url: str = ""
    # Strava (docs/features/strava.md). Both empty: Strava is off and the password login
    # is the only one.
    strava_client_id: str = ""
    strava_client_secret: str = ""
    # Lets this Strava athlete claim a fresh instance (no owner yet) by logging in with
    # Strava, instead of running `cli create-owner`.
    strava_owner_athlete_id: int | None = None
    # Shared secret for Strava's webhook subscription handshake (`cli strava-subscribe`).
    strava_webhook_verify_token: str = ""
    strava_api_base: str = "https://www.strava.com/api/v3"

    @property
    def strava_enabled(self) -> bool:
        return bool(self.strava_client_id and self.strava_client_secret)

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
