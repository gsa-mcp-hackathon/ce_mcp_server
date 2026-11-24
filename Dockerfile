# syntax=docker/dockerfile:1
# Container image for remote (streamable-HTTP) deployment.
#
# Used by:
#   - IBM Code Engine (deploy/ibm/)
#   - cloud.gov (docker image path) and any other container host
#
# The server serves MCP over streamable-HTTP at :8080/mcp with a health check
# at :8080/health. app.py auto-selects HTTP because PORT is set below.

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/src
ENV PORT=8080

WORKDIR /app

# Install uv (fast, reproducible installs).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install dependencies first for better layer caching.
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv pip install --system --no-cache .

# The sidecar set: every lookup, capture and verdict reads it.
COPY sidecars ./sidecars

# The loaded tables (data/parquet, about 40 MB for one release), when the build
# context has them. data/ is not in git, so a build from a fresh clone has
# none: the bracket makes the path optional, and pyproject.toml is named only
# so that COPY always has a source. Without the tables the lookups and
# evaluate_request still work, and run_request reports that no data is loaded.
# Load them first with `uv run ce-ingest load`, or mount them at /app/data/parquet.
COPY pyproject.toml data/parque[t] ./data/parquet/
RUN rm -f ./data/parquet/pyproject.toml && mkdir -p ./data/records

ENV CE_META_SIDECAR_DIR=/app/sidecars
ENV CE_META_DATA_DIR=/app/data/parquet
# Request, resolved and outcome records. They live in the container: mount a
# volume here to keep them across restarts.
ENV CE_CAPTURE_RECORDS_DIR=/app/data/records

EXPOSE 8080

# PORT=8080 (set above) makes app.py run HTTP on that port automatically.
CMD ["python", "-m", "ce_mcp_server.app"]
