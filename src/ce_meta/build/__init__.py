"""Batch steps that build the reference tables the metadata service reads.

This is the one part of `ce_meta` that may import the loader (`ce_ingest`) and
the dictionary reader (`ce_sidecars.dictionary`). The service and the MCP
server never import it: they only read the tables it writes.
"""
