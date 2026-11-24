"""The Databricks workspace both clients talk to: its host and its token.

Shared by the serving endpoint client (`databricks.py`) and the SQL warehouse
client (`warehouse.py`). Each passes its own error class, so a failure reads
as a model failure or a storage failure to its caller.

Credentials, in order: a DATABRICKS_TOKEN; the service principal that
Databricks Apps inject (DATABRICKS_CLIENT_ID and DATABRICKS_CLIENT_SECRET,
exchanged for an OAuth token); the developer's Databricks CLI login
(`databricks auth token`). No key is stored by this project.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections.abc import Callable
from urllib.parse import urlsplit

import httpx

TOKEN_MARGIN = 60  # seconds before expiry at which a token is renewed


class WorkspaceError(RuntimeError):
    """The workspace host or its credentials are not usable."""


def workspace_host(value: str | None, error: type[Exception] = WorkspaceError) -> str:
    """A workspace URL reduced to https://host, or `error`."""
    if not value:
        raise error("No Databricks host: set CE_CAPTURE_DATABRICKS_HOST.")
    url = urlsplit(value if "://" in value else f"https://{value}")
    if url.scheme != "https" or not url.hostname or url.username or url.password:
        raise error("The Databricks host must be an https URL with no credentials.")
    if url.path not in ("", "/") or url.query or url.fragment:
        raise error("The Databricks host must have no path, query or fragment.")
    port = f":{url.port}" if url.port else ""
    return f"https://{url.hostname}{port}"


class _Cached:
    """A token source that renews the token shortly before it expires."""

    def __init__(self, fetch: Callable[[], tuple[str, float]]) -> None:
        self.fetch = fetch
        self.token: str | None = None
        self.expires = 0.0
        self.lock = threading.Lock()

    def __call__(self) -> str:
        with self.lock:
            if self.token is None or time.time() > self.expires - TOKEN_MARGIN:
                self.token, self.expires = self.fetch()
            return self.token


def token_source(
    host: str,
    profile: str | None,
    client: httpx.Client,
    error: type[Exception] = WorkspaceError,
) -> Callable[[], str]:
    token = os.getenv("DATABRICKS_TOKEN")
    if token:
        return lambda: token
    client_id = os.getenv("DATABRICKS_CLIENT_ID")
    secret = os.getenv("DATABRICKS_CLIENT_SECRET")
    if client_id and secret:

        def oauth() -> tuple[str, float]:
            try:
                r = client.post(
                    f"{host}/oidc/v1/token",
                    data={"grant_type": "client_credentials", "scope": "all-apis"},
                    auth=(client_id, secret),
                )
                r.raise_for_status()
                body = r.json()
            except (httpx.HTTPError, ValueError):
                raise error("The service principal could not get a token.") from None
            return body["access_token"], time.time() + float(
                body.get("expires_in", 3600)
            )

        return _Cached(oauth)

    def cli() -> tuple[str, float]:
        command = ["databricks", "auth", "token", "--host", host]
        if profile:
            command = ["databricks", "auth", "token", "--profile", profile]
        try:
            out = subprocess.run(
                command, capture_output=True, text=True, timeout=30, check=True
            )
            body = json.loads(out.stdout)
        except (OSError, subprocess.SubprocessError, ValueError):
            raise error(
                "No Databricks credentials: set DATABRICKS_TOKEN, or log in with "
                "`databricks auth login`."
            ) from None
        return body["access_token"], time.time() + 1800

    return _Cached(cli)
