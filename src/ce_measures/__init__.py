"""ce_measures: rule validation and computation of captured requests (Plan 5).

Takes a request record and its resolved record (Plan 4), judges each node
against the sidecar set (pass, warn, substitute or block), computes the nodes
that may run, composes the reply text and writes an outcome record.

Validation reads no data rows: only `join.py` and the `do_*` methods open a
table. Nothing here imports `ce_ingest`, `ce_sidecars` or pandas.
"""
