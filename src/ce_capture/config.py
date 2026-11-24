"""Capture settings, read from CE_CAPTURE_* environment variables or `.env`."""

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class CaptureSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CE_CAPTURE_", env_file=".env", extra="ignore"
    )

    records_dir: Path = Path("data/records")
    """Where the JSON file store keeps request, resolved and outcome records."""

    store: Literal["json", "delta"] = "json"
    """Where records are kept: JSON files under `records_dir`, or Delta tables
    reached through a SQL warehouse (D17)."""

    warehouse_id: str | None = None
    """The SQL warehouse of the Delta store. Falls back to
    DATABRICKS_WAREHOUSE_ID, which a Databricks app's warehouse resource sets."""

    store_catalog: str | None = None
    store_schema: str | None = None
    """The Unity Catalog catalog and schema holding the record tables. Both
    are required by the Delta store."""

    store_prefix: str = "ce_mcp_"
    """Put before each record table's name, such as `ce_mcp_request_records`."""

    store_timeout: float = 120.0
    """Seconds to wait for one statement, including a warehouse start."""

    endpoint: str | None = None
    """Databricks serving endpoint of the server-side model. When set, the
    server registers `capture_request`; when not, the client-model tools."""

    databricks_host: str | None = None
    """Workspace URL, such as https://adb-123.4.azuredatabricks.net. Falls back
    to DATABRICKS_HOST, which Databricks Apps inject."""

    databricks_profile: str | None = None
    """Databricks CLI profile for a local run (`databricks auth token`)."""

    both_paths: bool = False
    """Register both capture paths at once: for comparison testing only."""

    max_lookups: int = 12
    """Lookup calls the server-side model may make in one capture."""

    retries: int = 2
    """Times a failed check goes back to the server-side model (D20)."""

    model_timeout: float = 120.0
    max_tokens: int = 4096
