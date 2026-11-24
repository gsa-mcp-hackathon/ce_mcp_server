# Databricks Apps Deployment

Deploy your MCP server as a [Databricks App](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/).
Databricks builds a container from your synced source and `app.yaml`, runs it,
and exposes it behind your workspace's authentication.

- **Transport:** MCP over streamable-HTTP at `/mcp` (stateless); health at `/health`
- **Port:** Databricks injects `DATABRICKS_APP_PORT` (defaults to `8000`); this
  template's `app.py` detects it and serves HTTP automatically — no code changes.

> **How the template already supports this:** `src/ce_mcp_server/app.py` checks
> for `DATABRICKS_APP_PORT` (and generic `PORT`) and switches to streamable-HTTP
> on that port. That's the only wiring Databricks Apps needs.

## What Databricks injects at runtime

| Variable | Purpose |
|---|---|
| `DATABRICKS_APP_PORT` | Port your server must listen on (default 8000) |
| `DATABRICKS_HOST` | Workspace URL |
| `DATABRICKS_CLIENT_ID` | Service principal client ID |
| `DATABRICKS_CLIENT_SECRET` | OAuth secret |

The last three are available if your tools need to call back into Databricks
(e.g. query a table). The example tool doesn't use them.

---

## Part 1 — Prerequisites

### 1.1 Install the Databricks CLI (v0.205+)

```bash
# macOS
brew tap databricks/tap && brew install databricks
# or see: https://docs.databricks.com/dev-tools/cli/install.html
databricks --version
```

### 1.2 Authenticate

```bash
databricks auth login --host https://<your-workspace>.cloud.databricks.com
databricks current-user me      # confirm you're logged in
```

> **Profile name gotcha:** `databricks auth login` prompts for a profile name and
> defaults it to your workspace host (e.g. `dbc-xxxx`) — **not** `DEFAULT`. Note
> the name you choose; you must set `DATABRICKS_CONFIG_PROFILE` in `.env` (below)
> to that exact name, or the deploy script's auth check will fail with
> "not authenticated". List your profiles with `databricks auth profiles`.

> Databricks **Apps** must be enabled on your workspace. If `apps` commands fail
> with a feature error, ask your workspace admin to enable Databricks Apps.

### 1.3 Configure the deploy env file

```bash
cp deploy/databricks/.env.example deploy/databricks/.env
# edit:
#   - APP_NAME:          your server's name (lowercase, hyphens)
#   - SOURCE_CODE_PATH:  absolute /Workspace path (replace <you> with your user)
#   - DATABRICKS_HOST / DATABRICKS_CONFIG_PROFILE: optional, if not using DEFAULT
set -a; source deploy/databricks/.env; set +a
```

`.env` holds **no secrets** — auth lives in your CLI login. It is git-ignored.

---

## Part 2 — Deploy

```bash
set -a; source deploy/databricks/.env; set +a
bash deploy/databricks/deploy-databricks-app.sh
```

The script is idempotent and:

1. Confirms the CLI is installed and authenticated.
2. Stages `app.yaml` at the repo root (where Databricks expects it) for the sync.
3. Ensures the App exists (create-or-reuse).
4. Syncs the repo to `SOURCE_CODE_PATH` in your workspace, including the
   git-ignored `data/parquet` folder.
5. Runs `databricks apps deploy` from that path.
6. Prints the app URL (`/mcp` + `/health`).

The first deploy builds the container and can take a few minutes.

Load the tables with `ce-ingest load` before deploying. If `data/parquet` is
missing, the script warns and the app still starts, but `run_request` reports
that no data is loaded.

### Record store

By default the app keeps request, resolved and outcome records as files in its
container, and they are lost when the app restarts. To keep them, store them
in Delta tables:

1. In `deploy/databricks/.env`, set `RECORD_WAREHOUSE_ID`, `RECORD_CATALOG` and
   `RECORD_SCHEMA` (a schema you own, such as your own schema in the `users`
   catalog). The deploy script then adds `CE_CAPTURE_STORE=delta` and these
   values to the staged `app.yaml`.
2. Deploy. At the end the script prints one `ce-capture store-ddl --apply`
   command, filled in with the app's service principal. Run it once: it creates
   the three tables if they are missing and grants the app `USE SCHEMA` on the
   schema and `SELECT` and `MODIFY` on the tables. The `USE CATALOG` grant in
   it fails unless you own the catalog; that is harmless when the catalog
   already lets every account user in, as `users` does.
3. The app's service principal also needs **Can use** on the SQL warehouse. If
   the warehouse is not open to all workspace users, add it to the app as a
   resource (the app's page, **Edit**, **Add resource**, **SQL warehouse**,
   permission **Can use**), which needs Manage permission on the warehouse, or
   ask an admin.

To check, call `submit_request_record` or `run_request` on the deployed server
and look for the row:

```sql
SELECT request_id, status, captured_at
FROM <catalog>.<schema>.ce_mcp_request_records
ORDER BY captured_at DESC LIMIT 5;
```

If a capture answers "The record could not be stored", read the app's logs:
the message names what failed (the warehouse answered HTTP 403, or the
statement ended as FAILED with a code such as `TABLE_OR_VIEW_NOT_FOUND` or
`INSUFFICIENT_PERMISSIONS`).

Each record write takes about two seconds on a running serverless warehouse,
so a capture is about six seconds slower than with files, and a run about
three. A stopped warehouse adds its start time to the first call.

### Smoke-test

```bash
# Private apps are auth-gated — an unauthenticated curl returns 302/401 (expected).
bash deploy/databricks/smoke-test.sh https://<app-url>

# Authenticated check:
DATABRICKS_TOKEN="$(databricks auth token --output json | jq -r .access_token)" \
  bash deploy/databricks/smoke-test.sh https://<app-url>

# If you saved a named profile (not DEFAULT), pass it and the host explicitly:
#   DATABRICKS_TOKEN="$(databricks --profile <your-profile> auth token \
#     --host https://<your-workspace>.cloud.databricks.com --output json | jq -r .access_token)" \
#     bash deploy/databricks/smoke-test.sh https://<app-url>
```

---

## Part 3 — Connect a client

Databricks Apps are gated by workspace auth, so an MCP client must present a
bearer token. For **watsonx Orchestrate** or **Claude** (remote HTTP), register
the endpoint and supply the token as an `Authorization: Bearer <token>` header
via the client's connection/credential mechanism.

For a quick local test with an MCP client that supports HTTP + headers:

```json
{
  "mcpServers": {
    "example-mcp": {
      "type": "http",
      "url": "https://<app-url>/mcp",
      "headers": { "Authorization": "Bearer <DATABRICKS_TOKEN>" }
    }
  }
}
```

> Token from `databricks auth token`. Tokens are short-lived; for a durable
> integration use a service principal with access to the app.

---

## Teardown

```bash
set -a; source deploy/databricks/.env; set +a
databricks apps delete "${APP_NAME}"
# Optionally remove the synced source:
#   databricks workspace delete "${SOURCE_CODE_PATH}" --recursive
```

---

## Files in this directory

| File | Purpose |
|---|---|
| `app.yaml` | Databricks Apps runtime config (the `command` that starts the server) |
| `.env.example` | Deploy coordinates template (copy to `.env`, no secrets) |
| `deploy-databricks-app.sh` | Idempotent create → sync → deploy |
| `smoke-test.sh` | Live `/health` + `/mcp` reachability check (auth-aware) |
| `README.md` | This runbook |

## Known gaps / notes

- **Auth-gated by default:** Databricks Apps sit behind workspace SSO/OAuth, so
  unauthenticated probes return 302/401 — that's expected, not a failure. Use a
  bearer token to reach the app programmatically.
- **Renamed package:** if you renamed `ce_mcp_server`, update the module path in
  `app.yaml` (`python -m <your_pkg>.app`).
- **`app.yaml` location:** Databricks reads `app.yaml` from the deployed source
  root. The deploy script stages this kit's copy at the repo root for the sync
  and removes it afterward; if you prefer, commit a copy at the repo root instead.
- **Stateless HTTP:** the server runs stateless streamable-HTTP, which scales
  cleanly on Apps (no sticky sessions required).
