# Evaluations

How well does a model use this server? Plan 6 answers with `ce-eval`
(`src/ce_eval/`) and the cases in this folder.

| Folder | Holds |
| --- | --- |
| `cases/` | The five case sets, the nuance catalogue, and `frozen/` (the outcomes of the gold records, written by `ce-eval freeze`) |
| `prompts/` | The system prompts given to the model under evaluation |
| `pass_marks.yaml` | The pass marks a run is held to |
| `judge_checks.yaml` | Labelled examples a judging model must get right |
| `baseline/` | The first recorded run and its triage |
| `results/` | Output of your own runs (ignored by git) |

```bash
uv run ce-eval gold                 # no model: every gold record against its expectations and the frozen outcomes
uv run ce-eval gold --figures       # the same, computing on data/parquet
uv run ce-eval run --driver oracle  # the scripted model: must score perfectly
uv run ce-eval run --driver claude-cli --model sonnet --workers 4 --judge haiku
uv run ce-eval rescore eval/results/<file>.json   # score again, no model run
uv run ce-eval judge-check --judge haiku            # check the judge on labelled examples first
```

A case is a user message, the record a careful model should write (`gold`),
and what code must make of it (`expect`). Each case names the data nuances it
tests. A run is scored at three levels: the record the model stored, the
outcome that record leads to (two records can differ and compute the same
thing), and the answer the user received (figures, cautions, block reasons).
The headline measure is the silent wrong answer rate.

The headless driver starts `claude -p` in an empty folder with this server as
its only tool, so it uses your Claude account's usage. One run of all cases on
Sonnet took about 9 minutes with four workers and cost about $6; an
interrupted run takes up where it stopped when started again with the same
`--out`. `baseline/README.md` records the first run and its triage.

The rest of this file is the template's guidance for a general MCP eval
harness, kept for a hosted run.

This template does **not** include an evaluation harness — but building one is
the single best way to know whether your MCP server actually works well.

## Why evaluate?

The quality of an MCP server is **not** how many tools it has — it's how well an
LLM with no other context can use those tools (their names, schemas, docstrings,
and return shapes) to answer realistic, difficult questions. An eval harness
measures exactly that and shows you *where* to fix things (tool schema, docstring,
or system prompt).

## How to build one

Use the **`mcp-eval` skill** (available to the coding agent in this repo under
`.agents/skills/mcp-eval/`). It walks you through a reusable
[Arize Phoenix](https://docs.arize.com/phoenix) harness that:

1. Launches your MCP server over stdio and connects a LangChain agent to it.
2. Feeds the agent a dataset of natural-language questions with known answers.
3. Scores each answer with LLM-as-judge evaluators and logs traces to Phoenix.

The companion **`mcp-builder` skill** (`.agents/skills/mcp-builder/`) covers
Phase 4 — writing the 10 gold questions (`evaluation.xml`) that become the
harness's dataset.

## Expected layout once built

The `mcp-eval` skill produces an `eval/phoenix/` module roughly like:

```
eval/phoenix/
├── agent.py            # <Server>Agent — launches your server over stdio
├── create_dataset.py   # CLI: upload a CSV dataset to Phoenix
├── run_experiment.py   # CLI: run agent + judges against a dataset
├── datasets.yaml       # Dataset registry
├── judges/             # LLM-as-judge evaluators (correctness, scope)
├── prompts/            # Versioned agent system prompts
└── datasets/<name>/    # evaluation.xml (source) + examples/<name>.csv
```

Add the eval-only dependencies to the `dev` dependency group in
`pyproject.toml` when you scaffold it (the skill lists the exact versions), then
`uv sync --group dev`.
