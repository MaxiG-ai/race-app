from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://race:race@localhost:5432/race"

    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_ttl_s: int = 15 * 60
    refresh_token_ttl_s: int = 30 * 24 * 3600
    cookie_secure: bool = False
    cookie_domain: str | None = None

    cors_origins: list[str] = ["http://localhost:5173"]

    # Original FIT files are retained verbatim (§8) so the corpus can be re-imported
    # when the parser improves.
    upload_dir: Path = Path("./var/uploads")
    max_upload_bytes: int = 32 * 1024 * 1024

    open_meteo_base_url: str = "https://archive-api.open-meteo.com/v1/archive"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
