"""Settings of request processing, read from CE_MEASURES_* variables or `.env`."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class MeasureSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CE_MEASURES_", env_file=".env", extra="ignore"
    )

    max_groups: int = 500
    """Groups one node may return; a grouping with more is refused, since a
    reply that long is no use and usually means a column was misread."""

    standard_errors: bool = True
    """Compute replicate-weight standard errors where the resolved record asks.
    Off only to speed up a local check."""
