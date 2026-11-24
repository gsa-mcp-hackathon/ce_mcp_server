"""What `ce_measures` may import (Plan 5, Conventions)."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_ce_measures_loads_neither_the_loader_the_generator_nor_pandas():
    code = (
        "import sys, ce_measures.process, ce_measures.cli, ce_mcp_server.measures\n"
        "banned = ('ce_ingest', 'ce_sidecars', 'pandas')\n"
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in banned "
        "or m.startswith('ce_meta.build'))\n"
        "print(','.join(bad))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    )
    assert out.stdout.strip() == ""


def test_the_server_does_not_load_the_evaluation_package():
    code = (
        "import sys, ce_mcp_server.app, ce_mcp_server.measures, ce_measures.process\n"
        "loaded = sorted(m for m in sys.modules if m.split('.')[0] == 'ce_eval')\n"
        "print(','.join(loaded))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    )
    assert out.stdout.strip() == ""


def test_the_evaluation_package_loads_neither_the_loader_nor_pandas():
    code = (
        "import sys, ce_eval.cli, ce_eval.harness, ce_eval.drivers, ce_eval.report\n"
        "banned = ('ce_ingest', 'ce_sidecars', 'pandas')\n"
        "print(','.join(sorted(m for m in sys.modules if m.split('.')[0] in banned)))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    )
    assert out.stdout.strip() == ""
