"""ce_ingest: loads BLS CE public-use microdata (SAS files) into tables.

The MCP server never imports this package; it only reads what the loader wrote.
"""

import logging

logging.getLogger(__name__).addHandler(logging.NullHandler())

__version__ = "0.1.0"
