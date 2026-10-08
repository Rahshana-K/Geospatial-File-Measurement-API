"""Application settings, overridable through ``GEO_*`` environment variables."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GEO_", env_file=".env", extra="ignore")

    # Where uploaded files and the default SQLite database live.
    data_dir: Path = Path("data")
    # Defaults to a SQLite file inside ``data_dir`` when left empty.
    database_url: str = ""

    # Upload limits (defence against oversized files and zip bombs).
    max_upload_mb: int = 50
    max_unzipped_mb: int = 200
    max_zip_members: int = 200
    max_features: int = 200_000

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'app.db').resolve()}"
