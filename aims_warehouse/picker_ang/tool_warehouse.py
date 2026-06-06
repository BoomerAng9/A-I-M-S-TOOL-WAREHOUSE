"""Picker_Ang Tool Warehouse (skill: references/tool-warehouse.md).

The inventory of individual tools Picker_Ang composes from. Faithful to the skill:
seven status labels, twenty-four categories, **certified-only** selection (or an
approved stage-safe fallback — the 6th source of record), and a rollup of every
status to the canonical verdict vocabulary that feeds Picker_Ang / Buildsmith /
Charlotte gates. Loaded from the bundled inventory log.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class ToolStatus(str, Enum):
    RAW = "raw"                # discovered, logged, not yet inspected
    CANDIDATE = "candidate"    # under intake; checks in progress
    TESTED = "tested"          # works in sandbox, not cleared for production
    CERTIFIED = "certified"    # cleared for production within its FOAI binding
    DEPRECATED = "deprecated"  # was certified, now superseded — do not select
    REJECTED = "rejected"      # failed intake — never select
    UNKNOWN = "unknown"        # provenance unestablished — unsafe until resolved


# The 24 canonical Tool Warehouse categories (skill: tool-warehouse.md).
CATEGORIES: frozenset[str] = frozenset({
    "auth", "database", "storage", "model gateway", "agent orchestration",
    "workflow jobs", "frontend shell", "voice", "email", "payments", "analytics",
    "monitoring", "security", "deployment", "receipts", "support", "documentation",
    "publishing", "design", "file intake", "RAG", "search", "sandbox", "API connectors",
})

# Canonical verdict vocabulary every gate rolls up to.
CANONICAL_VERDICTS: frozenset[str] = frozenset({
    "PASS", "FAIL", "MISSING", "UNVERIFIED", "BLOCKED", "NEEDS_HUMAN_REVIEW",
})

_STATUS_VERDICT: dict[ToolStatus, str] = {
    ToolStatus.CERTIFIED: "PASS",
    ToolStatus.REJECTED: "FAIL",
    ToolStatus.DEPRECATED: "BLOCKED",
    ToolStatus.UNKNOWN: "NEEDS_HUMAN_REVIEW",
    ToolStatus.RAW: "UNVERIFIED",
    ToolStatus.CANDIDATE: "UNVERIFIED",
    ToolStatus.TESTED: "UNVERIFIED",
}


def canonical_verdict(status: ToolStatus | None) -> str:
    """Roll a tool status up to the canonical verdict vocabulary. Absent → MISSING."""
    if status is None:
        return "MISSING"
    return _STATUS_VERDICT[status]


@dataclass
class ToolCard:
    """A Tool Discovery Card (skill: tool-warehouse.md). Modeled on the inventory
    record plus the skill's status/category vocabulary."""
    name: str
    category: str
    status: ToolStatus = ToolStatus.RAW
    type: str = ""
    version: str = ""
    origin: str = ""
    install: str = ""
    layer: str = ""
    agent_binding: str = ""
    source_shelf: str = ""
    note: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def verdict(self) -> str:
        return canonical_verdict(self.status)

    @property
    def selectable(self) -> bool:
        return self.status is ToolStatus.CERTIFIED

    @classmethod
    def from_record(cls, rec: dict[str, Any]) -> "ToolCard":
        raw_status = str(rec.get("status", "raw")).strip().lower()
        try:
            status = ToolStatus(raw_status)
        except ValueError:
            status = ToolStatus.UNKNOWN
        return cls(
            name=str(rec.get("name", "")).strip(),
            category=str(rec.get("category", "")).strip(),
            status=status,
            type=str(rec.get("type", "")),
            version=str(rec.get("version", "")),
            origin=str(rec.get("origin", "")),
            install=str(rec.get("install", "")),
            layer=str(rec.get("layer", "")),
            agent_binding=str(rec.get("agent_binding", "")),
            source_shelf=str(rec.get("source_shelf", "")),
            note=str(rec.get("note", "")),
            raw=rec,
        )


class ToolWarehouse:
    """Queryable Tool Warehouse. `select()` returns only certified tools; missions
    that need an un-inventoried/uncertified part block (or take a stage-safe fallback)
    per the source-of-record rule."""

    def __init__(self, cards: list[ToolCard]) -> None:
        self.cards = cards

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "ToolWarehouse":
        cards: list[ToolCard] = []
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                cards.append(ToolCard.from_record(json.loads(line)))
        return cls(cards)

    def select(self, category: str) -> list[ToolCard]:
        """Certified tools in a category (the only production-selectable ones)."""
        return [c for c in self.cards if c.category == category and c.selectable]

    def can_select(self, category: str, *, allow_stage_safe_fallback: bool = False) -> tuple[bool, str]:
        """Source-of-record gate: a certified tool exists, OR a stage-safe fallback
        is explicitly allowed. Otherwise block with the missing-setup reason."""
        if self.select(category):
            return True, "certified tool available"
        if allow_stage_safe_fallback:
            return True, "stage-safe fallback (no certified tool; approved fallback)"
        return False, f"no certified tool in category '{category}'; intake required (routes to Chicken Hawk)"

    def off_catalog_categories(self) -> set[str]:
        """Inventory categories that are not one of the 24 canonical ones — flagged
        for normalization at intake, never silently accepted."""
        return {c.category for c in self.cards if c.category and c.category not in CATEGORIES}

    def status_rollup(self) -> dict[str, int]:
        """Count of cards per canonical verdict — the intake-health summary."""
        out: dict[str, int] = {v: 0 for v in CANONICAL_VERDICTS}
        for c in self.cards:
            out[c.verdict] += 1
        return out
