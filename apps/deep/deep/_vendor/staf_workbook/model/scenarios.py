"""Scenarios of one assessment: "Existing Conditions" first (its name is fixed; it is what every
alternative is compared with), then any alternatives the user adds. Each scenario carries the
app's own state (whatever the app's page edits), copied from the active scenario when added."""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Optional

from ..xlsx.names import sheet_name_problem

BASELINE_ID = "existing"
BASELINE_NAME = "Existing Conditions"
MAX_SCENARIOS = 10
MAX_DESCRIPTION = 500
RESERVED_NAMES = ("Summary", "ReferenceCurves")


@dataclass
class Scenario:
    id: str
    name: str
    description: str = ""
    state: Any = None

    @property
    def is_baseline(self) -> bool:
        return self.id == BASELINE_ID


class ScenarioSet:
    def __init__(self, items: Optional[list] = None, active: str = BASELINE_ID, next_number: int = 2):
        self.items: list = items or [Scenario(BASELINE_ID, BASELINE_NAME)]
        self.active = active if any(s.id == active for s in self.items) else BASELINE_ID
        self.next_number = max(next_number, 2)

    # ------------------------------------------------------------------ reading
    @property
    def baseline(self) -> Scenario:
        return self.items[0]

    @property
    def current(self) -> Scenario:
        return self.get(self.active)

    @property
    def alternatives(self) -> list:
        return self.items[1:]

    def get(self, sid: str) -> Scenario:
        for s in self.items:
            if s.id == sid:
                return s
        raise KeyError(sid)

    def has_alternatives(self) -> bool:
        return len(self.items) > 1

    def can_add(self) -> bool:
        return len(self.items) < MAX_SCENARIOS

    def name_problem(self, name: str, sid: Optional[str] = None) -> Optional[str]:
        taken = [s.name for s in self.items if s.id != sid] + list(RESERVED_NAMES)
        return sheet_name_problem(name.strip() if name else name, taken)

    def default_name(self) -> str:
        used = set(s.name.lower() for s in self.items)
        n = 1
        while f"alternative {n}" in used:
            n += 1
        return f"Alternative {n}"

    # ------------------------------------------------------------------ editing
    def add(self, name: Optional[str] = None, description: str = "", *, copy_from: Optional[str] = None) -> Scenario:
        if not self.can_add():
            raise ValueError(f"At most {MAX_SCENARIOS} scenarios.")
        name = (name or self.default_name()).strip()
        problem = self.name_problem(name)
        if problem:
            raise ValueError(problem)
        source = self.get(copy_from or self.active)
        sid = f"s{self.next_number}"
        self.next_number += 1
        new = Scenario(sid, name, (description or "")[:MAX_DESCRIPTION], copy.deepcopy(source.state))
        self.items.append(new)
        self.active = sid
        return new

    def rename(self, sid: str, name: str) -> None:
        s = self.get(sid)
        if s.is_baseline:
            raise ValueError("Existing Conditions keeps its name.")
        name = (name or "").strip()
        problem = self.name_problem(name, sid)
        if problem:
            raise ValueError(problem)
        s.name = name

    def describe(self, sid: str, text: str) -> None:
        self.get(sid).description = (text or "").strip()[:MAX_DESCRIPTION]

    def delete(self, sid: str) -> None:
        s = self.get(sid)
        if s.is_baseline:
            raise ValueError("Existing Conditions cannot be deleted.")
        self.items.remove(s)
        if self.active == sid:
            self.active = BASELINE_ID

    def select(self, sid: str) -> None:
        self.get(sid)
        self.active = sid

    def set_state(self, sid: str, state: Any) -> None:
        self.get(sid).state = state

    # ------------------------------------------------------------------ saving
    def to_json(self, *, include_baseline_state: bool = False) -> dict:
        """The ``scenarios`` key of a session file. The baseline's state stays in the file's
        top-level fields (older readers keep working), so by default it is left out here."""
        items = []
        for s in self.items:
            item = {"id": s.id, "name": s.name, "description": s.description}
            if not s.is_baseline or include_baseline_state:
                item["state"] = s.state
            items.append(item)
        return {"version": 1, "active": self.active, "next": self.next_number, "items": items}

    @classmethod
    def from_json(cls, data, baseline_state: Any = None) -> "ScenarioSet":
        """Read a ``scenarios`` key; anything malformed falls back to Existing Conditions alone."""
        base = Scenario(BASELINE_ID, BASELINE_NAME, "", baseline_state)
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            return cls([base])
        items, seen = [base], {BASELINE_ID}
        for raw in data["items"]:
            if not isinstance(raw, dict):
                continue
            sid = str(raw.get("id") or "")
            if sid == BASELINE_ID:
                base.description = str(raw.get("description") or "")[:MAX_DESCRIPTION]
                continue
            name = str(raw.get("name") or "").strip()
            if not sid or sid in seen or len(items) >= MAX_SCENARIOS:
                continue
            taken = [s.name for s in items] + list(RESERVED_NAMES)
            if sheet_name_problem(name, taken):
                continue
            seen.add(sid)
            items.append(Scenario(sid, name, str(raw.get("description") or "")[:MAX_DESCRIPTION], raw.get("state")))
        nums = [int(s.id[1:]) for s in items[1:] if s.id[1:].isdigit()]
        try:
            nxt = int(data.get("next") or 2)
        except (TypeError, ValueError):
            nxt = 2
        return cls(items, str(data.get("active") or BASELINE_ID), max([nxt] + [n + 1 for n in nums]))

    def fingerprint(self) -> str:
        text = json.dumps(self.to_json(include_baseline_state=True), sort_keys=True, default=str)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
