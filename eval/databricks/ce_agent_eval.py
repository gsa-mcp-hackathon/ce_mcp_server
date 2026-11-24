# =============================================================================
# MLflow GenAI evaluation of the deployed CE Survey MCP Server (mcp-ce-survey)
# =============================================================================
# The project's copy of the template's agent_eval_offline.py, with the
# configuration filled in and the project's own cases as the dataset.
#
# What it does: puts each question of ce_eval_dataset.json to the agent
# exported from the Playground (agent_server/agent.py, used unmodified), which
# calls the deployed app, and scores the final answer with MLflow judges.
# It scores the answer only. The record and outcome levels, and conversations,
# stay with `ce-eval` (Plan 6).
#
# Where it runs: a Databricks notebook opened in the exported agent's folder,
# so that these sit beside the notebook:
#     agent_server/agent.py, utils.py, __init__.py   (exported by the Playground)
#     agent_server/eval_auth.py                      (from this kit)
#     ce_eval_dataset.json                           (from build_dataset.py)
#
# First, in a cell of its own above this one:
#     %pip install openai-agents databricks-openai
#     %restart_python
#
# Needs, once: a service principal with "Can Use" on the app, and its ID and
# OAuth secret in a secret scope the notebook's user can read.
#
# The dataset is written by build_dataset.py from the loaded data. Build it
# again, and upload it again, after a change to a case, a rule, a sidecar or
# the data, or after redeploying the app with new data.
# =============================================================================
import asyncio
import atexit
import json
import os
import threading
from collections import Counter
from pathlib import Path

import mlflow
from mlflow.entities.trace_location import MlflowExperimentLocation
from mlflow.genai import evaluate
from mlflow.genai.scorers import (
    Correctness,
    Guidelines,
    RelevanceToQuery,
    Safety,
)
from mlflow.types.responses import ResponsesAgentRequest

# --- CONFIG -------------------------------------------------------------------
# 1) The deployed server.
MCP_SERVER_NAME = "mcp-ce-survey"
MCP_SERVER_URL = "https://mcp-ce-survey-7474654188777746.aws.databricksapps.com/mcp"
DATABRICKS_HOST = "https://dbc-aae0ba16-8727.cloud.databricks.com/"

# 2) The model. None keeps what the exported agent was generated with
#    (system.ai.claude-sonnet-4-5), so the evaluation tests the agent as it is
#    deployed. Name a serving endpoint to try another model.
MODEL = None

# 3) The OpenAI API the agent calls the model with. None keeps the agent's own
#    ("chat_completions"). Set "responses" if a model refuses MCP tools on
#    chat completions (the template notes this for OpenAI reasoning endpoints).
OPENAI_API = None

# 4) The MLflow experiment. With no ID, an experiment of this name is used
#    under the notebook user's own folder, and made on the first run.
EXPERIMENT_ID = None
EXPERIMENT_NAME = "mcp-ce-survey-eval"

# 5) The secret scope and keys holding the service principal's credentials.
SECRET_SCOPE = "mcp-eval-secrets"
SECRET_KEY_CLIENT_ID = "sp_id"
SECRET_KEY_CLIENT_SECRET = "sp_secret"

# 6) The dataset, and which of its rows to run. Each filter is None for all.
#    For a first run, set LIMIT = 5 to check access before the full set.
DATASET_FILE = "ce_eval_dataset.json"
SETS = None  # e.g. ["core", "traps"]; also "rewordings", "out_of_scope"
KINDS = None  # e.g. ["figures"]; also "block", "question", "decline"
CASE_IDS = None  # e.g. ["interviews-in-2025"]
LIMIT = None  # the first N rows after the filters

# 7) How many questions run at once. Each takes several tool calls on the app.
MAX_WORKERS = 4

# 8) The judges. Correctness checks each row's expected facts: the figures as
#    the server gave them, the cautions, the reason of a block, the question
#    to ask. The two guidelines check what holds for every answer; a judge
#    sees the question and the answer, not the server's replies.
SCORERS = [
    Correctness(),
    RelevanceToQuery(),
    Safety(),
    Guidelines(
        name="survey_scope",
        guidelines=(
            "If the response gives a statistic, it must say that the result "
            "comes from the Interview Survey only, or that it will not match "
            "the tables BLS publishes. A response that gives no statistic "
            "passes."
        ),
    ),
    Guidelines(
        name="no_guessed_figure",
        guidelines=(
            "If the response says a statistic cannot be computed, declines "
            "the request, or asks the user a clarifying question about it, it "
            "must not also give an estimated, approximate or remembered "
            "figure for that statistic."
        ),
    ),
]
# ------------------------------------------------------------------------------

# NOTE: do NOT call nest_asyncio.apply(). It conflicts with the shared event
# loop below ("Event loop is closed" during connection teardown).

os.environ.setdefault("MLFLOW_GENAI_EVAL_MAX_WORKERS", str(MAX_WORKERS))


# --- The dataset ----------------------------------------------------------------
def load_dataset(path: Path) -> tuple[list[dict], list[dict]]:
    """The rows to run, as MLflow wants them, and the same rows as written
    (with `case_id`, `set` and `kind`, which MLflow does not take)."""
    doc = json.loads(path.read_text())
    rows = [
        r
        for r in doc["rows"]
        if (SETS is None or r["set"] in SETS)
        and (KINDS is None or r["kind"] in KINDS)
        and (CASE_IDS is None or r["case_id"] in CASE_IDS)
    ]
    rows = rows[:LIMIT] if LIMIT else rows
    data = [{"inputs": r["inputs"], "expectations": r["expectations"]} for r in rows]
    return data, rows


def _dataset_path() -> Path:
    """Beside the notebook (its working directory), or beside this file."""
    here = Path.cwd() / DATASET_FILE
    if here.exists() or "__file__" not in globals():
        return here
    return Path(__file__).with_name(DATASET_FILE)


eval_dataset, eval_rows = load_dataset(_dataset_path())
if not eval_dataset:
    raise SystemExit("No rows to run: check SETS, KINDS, CASE_IDS and LIMIT.")

# --- Service-principal credentials ---------------------------------------------
# dbutils exists only inside a Databricks notebook; elsewhere, use env vars.
try:
    _sp_client_id = dbutils.secrets.get(SECRET_SCOPE, SECRET_KEY_CLIENT_ID)  # type: ignore[name-defined]  # noqa: F821
    _sp_client_secret = dbutils.secrets.get(SECRET_SCOPE, SECRET_KEY_CLIENT_SECRET)  # type: ignore[name-defined]  # noqa: F821
except NameError:
    _sp_client_id = os.getenv("DATABRICKS_CLIENT_ID", "")
    _sp_client_secret = os.getenv("DATABRICKS_CLIENT_SECRET", "")
if not (_sp_client_id and _sp_client_secret):
    raise SystemExit(
        f"No service principal credentials: scope {SECRET_SCOPE!r} needs the keys "
        f"{SECRET_KEY_CLIENT_ID!r} and {SECRET_KEY_CLIENT_SECRET!r}, readable by you."
    )

# --- Wire the configuration and auth into the (unmodified) exported agent ------
import agent_server.agent as agent  # noqa: E402
from agent_server.eval_auth import patch_agent_with_service_principal  # noqa: E402

if MODEL:
    agent.MODEL = MODEL
agent.MCP_SERVERS = [(MCP_SERVER_NAME, MCP_SERVER_URL)]
if OPENAI_API:
    from agents import set_default_openai_api

    set_default_openai_api(OPENAI_API)

# Replace the agent's get_mcp_user_workspace_client() with one that returns the
# service principal's client: the identity the OAuth-gated app accepts.
_sp = patch_agent_with_service_principal(
    agent,
    host=DATABRICKS_HOST,
    client_id=_sp_client_id,
    client_secret=_sp_client_secret,
)
# Fails here, with a clear error, if the credentials are wrong.
print(f"MCP calls run as: {_sp.current_user.me().display_name}")

invoke = agent.invoke

# --- MLflow experiment and trace destination -----------------------------------
# evaluate() logs the judges' assessments through MLflow Tracing; without an
# explicit destination it can fail with "'NoneType' object has no attribute
# 'info'" on every scorer.
if EXPERIMENT_ID:
    _experiment_id = str(EXPERIMENT_ID)
    mlflow.set_experiment(experiment_id=_experiment_id)
else:
    from databricks.sdk import WorkspaceClient

    _user = WorkspaceClient().current_user.me().user_name
    _experiment = mlflow.set_experiment(f"/Users/{_user}/{EXPERIMENT_NAME}")
    _experiment_id = _experiment.experiment_id
mlflow.tracing.set_destination(MlflowExperimentLocation(experiment_id=_experiment_id))

# --- Async plumbing -------------------------------------------------------------
# agent.py makes one AsyncDatabricksOpenAI client at import, whose connection
# pool binds to the first event loop that uses it. One persistent loop on a
# background thread takes every call, so the client never crosses loops and
# evaluate()'s worker threads can all submit to it.
_LOOP = asyncio.new_event_loop()


def _loop_runner():
    asyncio.set_event_loop(_LOOP)
    _LOOP.run_forever()


_LOOP_THREAD = threading.Thread(target=_loop_runner, daemon=True, name="eval-loop")
_LOOP_THREAD.start()
atexit.register(lambda: _LOOP.call_soon_threadsafe(_LOOP.stop))


def _run_async(coro):
    return asyncio.run_coroutine_threadsafe(coro, _LOOP).result()


def _extract_text(response) -> str:
    """The assistant's final text. The last output item may be a tool call,
    and a message's content may be a list of blocks, so walk back to the first
    real text."""
    for item in reversed(response.output):
        content = getattr(item, "content", None)
        if content is None and isinstance(item, dict):
            content = item.get("content")
        if content is None:
            continue
        if isinstance(content, str):
            if content.strip():
                return content
            continue
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    parts.append(block.get("text") or block.get("content") or "")
                else:
                    parts.append(getattr(block, "text", "") or "")
            text = "".join(p for p in parts if p)
            if text.strip():
                return text
    return str(response.output)


@mlflow.trace(span_type="AGENT")
def predict(request: str) -> str:
    agent_request = ResponsesAgentRequest(input=[{"role": "user", "content": request}])
    response = _run_async(invoke(agent_request))
    return _extract_text(response)


# --- Run the evaluation ---------------------------------------------------------
if __name__ == "__main__":
    kinds = Counter(r["kind"] for r in eval_rows)
    print(f"{len(eval_dataset)} questions: {dict(sorted(kinds.items()))}")
    print(f"model: {agent.MODEL}; experiment: {_experiment_id}")
    results = evaluate(data=eval_dataset, predict_fn=predict, scorers=SCORERS)
    for name, value in sorted(results.metrics.items()):
        print(f"{name}: {value}")
    # The row-by-row results, with each judge's reasons and the tool calls of
    # every question, are in the MLflow experiment.
