from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from TAHGUL_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="TAHGUL_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://tahgul:tahgul@localhost:5432/tahgul"

    # Sessions
    session_cookie_name: str = "tahgul_session"
    session_days: int = 30
    # Set false only for plain-HTTP local development; Tailscale serve gives HTTPS.
    cookie_secure: bool = True

    # Optional first-run bootstrap of the single owner account.
    owner_email: str | None = None
    owner_password: str | None = None

    # Login throttling
    login_max_failures: int = 10
    login_window_minutes: int = 15

    # Encrypts stored credentials (calendar passwords). Any long random string; keep it
    # out of the repo, and back it up: changing it makes saved passwords unreadable.
    secret_key: str | None = None

    # Calendar sync
    scheduler_enabled: bool = True
    sync_minutes: int = 3
    # The Radicale in the stack: when set, Tahgul connects to it on first start.
    radicale_url: str | None = None
    radicale_user: str | None = None
    radicale_password: str | None = None

    # Where the built web app lives. Empty or missing directory = API only.
    static_dir: Path = Path(__file__).parent / "static"


@lru_cache
def get_settings() -> Settings:
    return Settings()
