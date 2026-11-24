"""Metadata service settings, read from CE_META_* environment variables or `.env`."""

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

Backend = Literal["parquet", "warehouse"]


class MetaSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CE_META_", env_file=".env", extra="ignore"
    )

    data_dir: Path = Path("data/parquet")
    """Root of the stored tables: `intrvw/<table>/`, `meta/` and `ref/`."""

    sidecar_dir: Path = Path("sidecars")
    """Folder holding the vocabulary, the master and the table sidecars."""

    dictionary: Path = Path("../sample_data/ce-pumd-interview-diary-dictionary-2.xlsx")
    """The BLS data dictionary, read by the build steps only."""

    release: int = 2025
    """The survey year answers default to."""

    backend: Backend = "parquet"
