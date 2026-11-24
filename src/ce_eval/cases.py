"""The case sets: one YAML file per set under `eval/cases/`.

A case is a user message with the record a careful model should write (`gold`)
and what code must make of it (`expect`). Cases link into conversations through
`follows` (a follow-up), `answers` (the user's answer to a question),
`corrects` (a corrected submission) and `after` (a new request later in the
same conversation, linked to nothing). Every case names the data nuances it
tests, from `nuances.yaml`, so results can be read nuance by nuance.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CASES_DIR = Path("eval/cases")
NUANCES_FILE = "nuances.yaml"
SETS = ("core", "traps", "rewordings", "conversations", "out_of_scope")
LINKS = ("follows", "answers", "corrects")


class CaseError(ValueError):
    """A case file is malformed."""


@dataclass(frozen=True)
class Nuance:
    id: str
    title: str
    mistake: str  # what a model is likely to do
    right: str  # the right outcome


@dataclass(frozen=True)
class Case:
    id: str
    set: str
    message: str
    gold: dict
    expect: dict
    nuance: tuple[str, ...] = ()
    tables: tuple[str, ...] = ()
    follows: str | None = None
    answers: str | None = None
    corrects: str | None = None
    after: str | None = None  # same conversation, a new request
    rewords: str | None = None  # the case this one says in other words
    offline_only: bool = False  # server mechanics: never put to a model
    mistake: str | None = None
    raw: dict = field(default_factory=dict, compare=False, repr=False)

    @property
    def link(self) -> str | None:
        """The case this one continues, if any."""
        return self.follows or self.answers or self.corrects or self.after

    def alternatives(self) -> list[dict]:
        """Other records that are as right as the gold one (`also`): a request
        can have more than one sound reading, such as rent from the fmli
        summary column or from the item rows. Each entry names a node and the
        keys to replace in it (`stated` entries are added to the node's own),
        or holds several such changes under `nodes`."""
        out = []
        for entry in self.raw.get("also") or []:
            record = copy.deepcopy(self.gold)
            by_id = {n.get("id"): n for n in record.get("nodes") or []}
            for change in entry.get("nodes") or [entry]:
                node = by_id.get(change.get("node", "n1"))
                if node is None:
                    continue
                for key, value in change.items():
                    if key == "node":
                        continue
                    if key == "stated":
                        node.setdefault("stated", {}).update(copy.deepcopy(value))
                    else:
                        node[key] = copy.deepcopy(value)
            out.append(record)
        return out

    def as_gold(self) -> dict:
        """The shape `ce_capture.gold` reads."""
        return self.raw


@dataclass(frozen=True)
class CaseSet:
    cases: tuple[Case, ...]
    loaded_years: tuple[int, ...]
    nuances: dict[str, Nuance]

    def by_id(self) -> dict[str, Case]:
        return {c.id: c for c in self.cases}

    def root(self, case: Case) -> Case:
        """The first case of the conversation a case belongs to."""
        by_id = self.by_id()
        while case.link:
            case = by_id[case.link]
        return case

    def conversations(self) -> list[list[Case]]:
        """Cases grouped into conversations, each in turn order."""
        groups: dict[str, list[Case]] = {}
        for case in self.cases:
            groups.setdefault(self.root(case).id, []).append(case)
        return list(groups.values())

    def select(
        self, sets: list[str] | None = None, ids: list[str] | None = None
    ) -> CaseSet:
        """A subset, with every conversation kept whole."""
        wanted = set()
        for case in self.cases:
            if (sets and case.set in sets) or (ids and case.id in ids):
                wanted.add(self.root(case).id)
            elif not sets and not ids:
                wanted.add(self.root(case).id)
        kept = tuple(c for c in self.cases if self.root(c).id in wanted)
        return CaseSet(kept, self.loaded_years, self.nuances)


def load_nuances(folder: Path = CASES_DIR) -> dict[str, Nuance]:
    path = Path(folder) / NUANCES_FILE
    if not path.exists():
        return {}
    doc = yaml.safe_load(path.read_text()) or {}
    return {
        n["id"]: Nuance(n["id"], n["title"], n.get("mistake", ""), n.get("right", ""))
        for n in doc.get("nuances") or []
    }


def _case(raw: dict[str, Any], set_name: str) -> Case:
    for key in ("id", "message", "gold", "expect"):
        if key not in raw:
            raise CaseError(f"case {raw.get('id', '?')} in {set_name} has no {key}")
    return Case(
        id=raw["id"],
        set=set_name,
        message=raw["message"],
        gold=raw["gold"],
        expect=raw["expect"],
        nuance=tuple(raw.get("nuance") or ()),
        tables=tuple(raw.get("tables") or ()),
        follows=raw.get("follows"),
        answers=raw.get("answers"),
        corrects=raw.get("corrects"),
        after=raw.get("after"),
        rewords=raw.get("rewords"),
        offline_only=bool(raw.get("offline_only")),
        mistake=raw.get("mistake"),
        raw=raw,
    )


def load_cases(folder: Path = CASES_DIR, sets: list[str] | None = None) -> CaseSet:
    """Every case of the named sets (all by default), checked for form."""
    folder = Path(folder)
    nuances = load_nuances(folder)
    cases: list[Case] = []
    years: set[int] = set()
    for name in SETS:
        path = folder / f"{name}.yaml"
        if not path.exists():
            continue
        doc = yaml.safe_load(path.read_text()) or {}
        years |= set(doc.get("loaded_years") or [])
        cases += [_case(raw, name) for raw in doc.get("cases") or []]
    ids = [c.id for c in cases]
    twice = sorted({i for i in ids if ids.count(i) > 1})
    if twice:
        raise CaseError(f"case IDs used twice: {twice}")
    known = set(ids)
    for case in cases:
        for key in (*LINKS, "after", "rewords"):
            target = getattr(case, key)
            if target and target not in known:
                raise CaseError(f"{case.id}: {key} names {target!r}, not a case")
        unknown = [n for n in case.nuance if nuances and n not in nuances]
        if unknown:
            raise CaseError(f"{case.id}: unknown nuance {unknown}")
    full = CaseSet(tuple(cases), tuple(sorted(years)) or (2025,), nuances)
    return full.select(sets=sets) if sets else full
