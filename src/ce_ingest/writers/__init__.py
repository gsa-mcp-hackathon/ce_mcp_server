"""Storage backends. Only this package knows which backend is in use."""

from ce_ingest.config import IngestSettings
from ce_ingest.writers.base import Writer
from ce_ingest.writers.parquet import ParquetWriter


def get_writer(settings: IngestSettings) -> Writer:
    if settings.backend == "parquet":
        return ParquetWriter(settings.out_dir)
    if settings.backend == "delta":
        raise NotImplementedError("DeltaWriter arrives in stage 7")
    raise ValueError(f"unknown backend {settings.backend!r}")


__all__ = ["ParquetWriter", "Writer", "get_writer"]
