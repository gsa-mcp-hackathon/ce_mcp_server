"""ce_store: what a reader needs to open the stored CE tables.

The loader (`ce_ingest`) and the MCP server both import this package, so the
server never has to import the loader. It uses only the standard library.
"""

from ce_store.partitions import HIVE_TYPES, duckdb_source
from ce_store.sets import Survey, survey_year_filter, survey_year_set

__all__ = [
    "HIVE_TYPES",
    "Survey",
    "duckdb_source",
    "survey_year_filter",
    "survey_year_set",
]
