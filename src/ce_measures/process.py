"""The two entry points: `evaluate` (pipeline steps 1 to 3, 5, 6) and `run`
(all six).

`evaluate` opens no table: it judges every node and says what would be
computed. `run` also computes every node that is not blocked, estimate nodes
first, then the combine nodes on their inputs' results. Both write one outcome
record and return it with the reply text.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from ce_capture.check import check_record
from ce_capture.context import CaptureContext
from ce_capture.storage import RecordStore
from ce_measures import combine, data, join, records
from ce_measures.config import MeasureSettings
from ce_measures.errors import CeMeasuresError
from ce_measures.facts import base_table, collect, partitions, years
from ce_measures.measures.base import average_versions, clean
from ce_measures.models import Estimate, Message, Node, NodeOutcome, Verdict
from ce_measures.outcome import OutcomeRecord, build, dump, says, write
from ce_measures.registry import function
from ce_measures.replicates import parameters
from ce_measures.reply import compose_reply
from ce_measures.verdict import judge
from ce_meta.service import imputed_set
from ce_meta.sidecars import TABLE_ID_PREFIX

log = logging.getLogger(__name__)

IMPUTED_ROWS = "imputed_income_item_month"
VERSIONS = range(1, 6)
SCALE = {"year": 1.0, "quarter": 1 / 4, "month": 1 / 12}
SCALED_KINDS = frozenset({"calendar_pair", "monthly_rows", "annual_value"})
# counts of the sample: months in scope play no part in them
UNWEIGHTED_COUNTS = frozenset(
    {"do_distinct_count", "do_row_count", "do_distinct_member_count"}
)


class RequestProcessor:
    """Judges and computes captured requests against one sidecar set."""

    def __init__(
        self,
        ctx: CaptureContext,
        store: RecordStore,
        data_dir: Path | None = None,
        settings: MeasureSettings | None = None,
    ) -> None:
        self.ctx = ctx
        self.store = store
        self.data_dir = data_dir
        self.settings = settings or MeasureSettings()
        self.sidecars = ctx.sidecars
        self.master = dict(ctx.sidecars.master.facts) if ctx.sidecars.master else {}
        weight = self.master.get("ce:weight") or {}
        self.weight = (
            str(weight.get("table", "fmli")).removeprefix(TABLE_ID_PREFIX),
            str(weight.get("column", "finlwt21")),
        )

    # ── the entry points ─────────────────────────────────────────────────────

    def evaluate(
        self, request_id: str, resolved_id: str | None = None
    ) -> tuple[OutcomeRecord, str]:
        return self._process(request_id, resolved_id, "evaluate")

    def run(
        self, request_id: str, resolved_id: str | None = None
    ) -> tuple[OutcomeRecord, str]:
        return self._process(request_id, resolved_id, "run")

    def _process(
        self, request_id: str, resolved_id: str | None, mode: Literal["evaluate", "run"]
    ) -> tuple[OutcomeRecord, str]:
        pair = records.load_pair(self.store, request_id, resolved_id)
        nodes = records.nodes(pair)
        outcomes = self.judge(pair, nodes, loaded=mode == "run")
        if mode == "run":
            outcomes = self.compute(nodes, outcomes)
        dumped = [outcomes[n.id].dump() for n in nodes]
        estimates = [n for n in nodes if n.kind == "estimate"]
        gaps = {
            str((n.resolved.get("gap_2025") or {}).get("value"))
            for n in estimates
            if n.resolved.get("gap_2025")
        }
        record = build(
            request=pair.request,
            resolved=pair.resolved,
            nodes=dumped,
            report=self.ctx.report,
            says_list=says(
                pair.request,
                [d for d in dumped if d["id"] in {n.id for n in estimates}],
                [y for n in estimates for y in years(n)],
                gaps,
            ),
            mode=mode,
        )
        text = compose_reply(dump(record), pair.request, pair.resolved, self.sidecars)
        record = record.model_copy(update={"reply": text})
        write(self.store, record)
        return record, text

    # ── step 3: verdicts ─────────────────────────────────────────────────────

    def judge(
        self, pair: records.RecordPair, nodes: list[Node], loaded: bool = False
    ) -> dict[str, NodeOutcome]:
        check = check_record(pair.request, self.ctx)
        flags = pair.flags
        partitions_loaded = set(self.ctx.loaded_partitions) if loaded else None
        by_id = {n.id: n for n in nodes}
        out: dict[str, NodeOutcome] = {}
        for node in nodes:
            if node.kind == "estimate":
                errors = [e for e in check.errors if e.get("node") in (None, node.id)]
                facts = collect(
                    node,
                    self.sidecars,
                    self.ctx.report,
                    flags,
                    loaded=partitions_loaded,
                    check_errors=errors,
                )
                verdict = judge(node, facts, self.weight[1])
            else:
                verdict = combine.judge(node, out, by_id, self.master)
            out[node.id] = NodeOutcome(id=node.id, verdict=verdict)
        return out

    # ── step 4: computation ──────────────────────────────────────────────────

    def compute(
        self, nodes: list[Node], outcomes: dict[str, NodeOutcome]
    ) -> dict[str, NodeOutcome]:
        out = dict(outcomes)
        for node in nodes:
            current = out[node.id]
            if node.kind == "combine":
                # an input that failed while computing blocks the combine node too
                current = NodeOutcome(
                    id=node.id,
                    verdict=combine.judge(
                        node, out, {n.id: n for n in nodes}, self.master
                    ),
                )
            if current.verdict.level == "block":
                out[node.id] = current
                continue
            try:
                if node.kind == "estimate":
                    result = self.estimate(node, current.verdict)
                else:
                    result = self.combined(node, out)
            except CeMeasuresError as exc:
                out[node.id] = _failed(current, exc.message)
                continue
            except Exception:
                log.exception("computing node %s failed", node.id)
                out[node.id] = _failed(current, "The computation failed; it is logged.")
                continue
            out[node.id] = current.model_copy(update={"result": result})
        return out

    def _variance(self, node: Node) -> tuple[int, float] | None:
        found = {parameters(self.master, y) for y in years(node)}
        return found.pop() if len(found) == 1 else None

    def estimate(self, node: Node, verdict: Verdict) -> Estimate:
        if verdict.method is None:
            raise CeMeasuresError("No method is defined for this node.")
        fn = function(verdict.method)
        statistic = node.asked.get("statistic")
        measure = node.asked.get("measure")
        base = base_table(node, self.sidecars, self.weight[0])
        weighted = verdict.weighted
        asked_se = bool((node.resolved.get("standard_error") or {}).get("value"))
        wants_se = (
            weighted
            and self.settings.standard_errors
            and (asked_se or verdict.substituted_from is not None)
        )
        variance = self._variance(node) if wants_se else None
        tables = {self.weight[0], base} | {
            f["table"]
            for f in (node.resolved.get("interview_filters") or {}).get("value") or []
        }
        sources = {t: data.open_table(self.data_dir, self.sidecars, t) for t in tables}
        options: dict = {
            "weight": self.weight,
            "replicates": join.replicate_columns(self.sidecars) if variance else [],
            "base": base,
        }
        if measure and base == self.weight[0] and variance:
            table = self.sidecars.table(base)
            columns = (node.resolved.get("columns") or {}).get("value")
            name = (
                columns[0]
                if isinstance(columns, list) and columns
                else measure["column"]
            )
            column = table.column(name) if table else None
            found = imputed_set(table, column) if table and column else None
            if found and found.mean == name and len(found.versions) == 5:
                options["imputed_versions"] = list(found.versions)
        kind = (node.resolved.get("measure_kind") or {}).get("value")
        basis = (node.resolved.get("time_basis") or {}).get("value") or "year"
        kw: dict = {"unit": (node.resolved.get("unit") or {}).get("result") or "count"}
        if statistic in ("mean", "total"):
            kw["scale"] = SCALE.get(basis, 1.0) if kind in SCALED_KINDS else 1.0
            kw["variance"] = variance
        elif verdict.method == "do_weighted_count":
            kw["variance"] = variance
        weight = "w" if weighted else None

        def one(version: int | None = None) -> Estimate:
            frame = join.interview_frame(
                node, sources, self.sidecars, **options, version=version
            )
            frame = clean(frame, needs_mo_scope=verdict.method not in UNWEIGHTED_COUNTS)
            groups = frame.table.num_rows and len(frame.groups)
            if groups and _group_count(frame) > self.settings.max_groups:
                raise CeMeasuresError(
                    f"The grouping gives more than {self.settings.max_groups} groups."
                )
            return fn(frame, weight, **kw)

        if measure and self.ctx.row_represents(measure["table"]) == IMPUTED_ROWS:
            result = average_versions([one(v) for v in VERSIONS])
        else:
            result = one()
        if wants_se and variance is None:
            result = result.model_copy(
                update={
                    "notes": [
                        *result.notes,
                        "no standard error: the years use different variance methods",
                    ]
                }
            )
        return result

    def combined(self, node: Node, outcomes: dict[str, NodeOutcome]) -> Estimate:
        ids = combine.inputs(node)
        inputs = [outcomes[i].result for i in ids]
        if any(r is None for r in inputs):
            raise CeMeasuresError("An input has no result.")
        unit = (node.resolved.get("unit") or {}).get("result")
        factor = combine.conversion_factor(node)
        if node.asked.get("statistic") == "ratio":
            return combine.ratio(inputs[0], inputs[1], unit, factor)
        return combine.difference(inputs[0], inputs[1], unit, factor)


def _group_count(frame: join.Frame) -> int:
    import duckdb

    keys = ", ".join(g.alias for g in frame.groups)
    con = duckdb.connect()
    try:
        con.register("frame", frame.table)
        return int(
            con.execute(
                f"SELECT count(*) FROM (SELECT DISTINCT {keys} FROM frame)"
            ).fetchone()[0]
        )
    finally:
        con.close()


def _failed(outcome: NodeOutcome, text: str) -> NodeOutcome:
    message = Message(level="block", rule="not_computed", text=text)
    verdict = outcome.verdict.model_copy(
        update={"level": "block", "messages": [*outcome.verdict.messages, message]}
    )
    return NodeOutcome(id=outcome.id, verdict=verdict)


__all__ = ["RequestProcessor", "partitions"]
