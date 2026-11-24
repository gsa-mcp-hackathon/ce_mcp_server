"""Loader settings, read from CE_INGEST_* environment variables or `.env`."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from ce_ingest.models import Backend


class IngestSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CE_INGEST_", env_file=".env", extra="ignore"
    )

    landing_dir: Path = Path("../sample_data")
    """Folder holding `pumd/intrvwYY/` and `pumd/diaryYY/`."""

    out_dir: Path = Path("data/parquet")
    """Output root for the Parquet backend."""

    backend: Backend = "parquet"
    catalog: str = "ce"
    """Unity Catalog catalog for the Delta backend."""
