"""The dataset builder of the Databricks kit (`eval/databricks/build_dataset.py`).

These check how a reply text and an outcome become expected facts, on made-up
records; the script itself needs the loaded data.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "eval" / "databricks" / "build_dataset.py"


@pytest.fixture(scope="module")
def builder():
    spec = importlib.util.spec_from_file_location("build_dataset", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REPLY = (
    "Average spending by region\n"
    "  Computed: the mean of total expenditures, weighted.\n"
    "  Result:\n"
    "    region Northeast: $88,010 (standard error $1,204)\n"
    "    region South: $74,526\n"
    "  Removed before computing: 3 with no value.\n"
    "  Caution: region code C is not a region.\n"
    "\n"
    "How many people\n"
    "  Cannot compute: no member weight.\n"
    "\n"
    "Interview Survey only.\n"
)
REQUEST = {
    "answers": [
        {"asks": "average spending by region", "node": "n1"},
        {"asks": "how many people", "node": "n2"},
    ]
}
OUTCOME = {
    "reply": REPLY,
    "nodes": [
        {
            "id": "n1",
            "verdict": "warn",
            "messages": [
                {"level": "substitute", "text": "used the summary column."},
                {"level": "warn", "text": "region code C is not a region."},
            ],
        },
        {
            "id": "n2",
            "verdict": "block",
            "messages": [{"level": "block", "text": "no member weight."}],
        },
    ],
}


def test_result_lines_stop_at_the_end_of_the_result(builder):
    block = REPLY.split("\n\n")[0]
    assert builder.result_lines(block) == [
        ("region Northeast", "$88,010"),
        ("region South", "$74,526"),
    ]


def test_outcome_facts_hold_figures_changes_cautions_and_blocks(builder):
    assert builder.outcome_facts(REQUEST, OUTCOME) == [
        'For "average spending by region", region Northeast: $88,010.',
        'For "average spending by region", region South: $74,526.',
        "The answer says what was changed: used the summary column.",
        "The answer keeps this caution: region code C is not a region.",
        'No figure is given for "how many people": it cannot be computed.',
        "The reason given: no member weight.",
    ]


def test_a_figure_for_all_names_no_group(builder):
    outcome = {
        "reply": "Average spending\n  Result:\n    All: $81,370\n\nClosing.\n",
        "nodes": [{"id": "n1", "verdict": "pass", "messages": []}],
    }
    request = {"answers": [{"asks": "average spending", "node": "n1"}]}
    assert builder.outcome_facts(request, outcome) == [
        'For "average spending": $81,370.'
    ]


def test_a_long_breakdown_is_capped(builder):
    lines = "".join(f"    state S{i}: ${i},000\n" for i in range(1, 13))
    outcome = {
        "reply": f"Spending by state\n  Result:\n{lines}\nClosing.\n",
        "nodes": [{"id": "n1", "verdict": "pass", "messages": []}],
    }
    request = {"answers": [{"asks": "spending by state", "node": "n1"}]}
    facts = builder.outcome_facts(request, outcome)
    assert len(facts) == builder.MAX_GROUPS + 1
    assert (
        facts[-1] == 'For "spending by state", a figure is given for each of 12 groups.'
    )
