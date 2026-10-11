from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Every field can be overridden with a GEO_* environment variable."""

    model_config = SettingsConfigDict(env_prefix="GEO_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/geo.db"
    upload_dir: Path = Path("./data/uploads")
    # Limit on the uploaded file itself.
    max_upload_bytes: int = 50 * 1024 * 1024
    # Limit on the total uncompressed size of a .zip / .kmz (guards against zip bombs).
    max_uncompressed_bytes: int = 500 * 1024 * 1024
