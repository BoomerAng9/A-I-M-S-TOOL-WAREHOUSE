"""Tool Warehouse catalog view — composes the static Picker_Ang inventory (the
certified-only selection model in picker_ang/tool_warehouse.py) with the live
IntegrationRegistry health into the JSON the Tool Warehouse surface consumes.

The query/rollup/select FUNCTIONS here are the single source of catalog logic:
BOTH Charlotte's in-process /api/warehouse/* handlers AND the standalone
tool-warehouse service call them, so the two can never drift.

This lives in `integrations` (the layer aware of BOTH the warehouse and the live
adapters). The one-way boundary holds: picker_ang never imports this; this only
reads picker_ang types.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..picker_ang.tool_warehouse import ToolStatus

if TYPE_CHECKING:
    from ..picker_ang.tool_warehouse import ToolCard, ToolWarehouse
    from .base import IntegrationHealth
    from .registry import IntegrationRegistry

# The spec's 3-state display vocabulary (Verified / Review / Blocked) projected
# over the 7 canonical ToolStatus curation values. certified = cleared for our
# builds; rejected/deprecated/unknown = never select; everything mid-pipeline
# (raw/candidate/tested) still needs review.
_DISPLAY: dict[ToolStatus, str] = {
    ToolStatus.CERTIFIED: "Verified",
    ToolStatus.CANDIDATE: "Review",
    ToolStatus.TESTED: "Review",
    ToolStatus.RAW: "Review",
    ToolStatus.REJECTED: "Blocked",
    ToolStatus.DEPRECATED: "Blocked",
    ToolStatus.UNKNOWN: "Blocked",
}


def display_status(status: ToolStatus) -> str:
    return _DISPLAY.get(status, "Review")


def card_to_dict(card: "ToolCard", health: "IntegrationHealth | None" = None) -> dict[str, Any]:
    """Serialize one warehouse card; attach live health when the card is one of
    the deployed integration tools (matched by name)."""
    out: dict[str, Any] = {
        "name": card.name,
        "category": card.category,
        "status": card.status.value,              # raw curation status
        "display_status": display_status(card.status),  # Verified/Review/Blocked
        "verdict": card.verdict,                   # canonical PASS/FAIL/... rollup
        "selectable": card.selectable,             # certified-only (build-selectable)
        "type": card.type,
        "version": card.version,
        "origin": card.origin,
        "source_shelf": card.source_shelf,
        "layer": card.layer,
        "note": card.note,
    }
    vis = (card.raw or {}).get("visibility")
    if vis:
        out["visibility"] = vis
    if health is not None:
        out["live"] = {
            "status": health.status,               # healthy | unhealthy | not_configured
            "http_code": health.http_code,
            "latency_ms": health.latency_ms,
            "configured": health.configured,
        }
    return out


# ----- shared catalog logic (used by Charlotte AND the standalone service) -----


async def query_tools(
    warehouse: "ToolWarehouse",
    registry: "IntegrationRegistry",
    *,
    category: str | None = None,
    certified: bool = False,
    q: str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    """Filterable catalog query, with live health joined for the deployed
    integration tools present in the result set."""
    cards = warehouse.cards
    if category:
        cards = [c for c in cards if c.category == category]
    if certified:
        cards = [c for c in cards if c.selectable]
    if q:
        ql = q.lower()
        cards = [
            c for c in cards
            if ql in c.name.lower() or ql in c.category.lower() or ql in (c.note or "").lower()
        ]
    cards = cards[: max(1, min(limit, 1000))]
    integ_names = set(registry.available_tools())
    health: dict[str, Any] = {}
    if any(c.name in integ_names for c in cards):
        health = {h.name: h for h in await registry.health_check_all()}
    return {
        "loaded": True,
        "total": len(cards),
        "tools": [card_to_dict(c, health.get(c.name)) for c in cards],
    }


def category_rollup(warehouse: "ToolWarehouse") -> dict[str, Any]:
    """Per-category shelf rollup: total + certified counts + status breakdown,
    plus the canonical-verdict rollup across the whole inventory."""
    cats: dict[str, dict[str, Any]] = {}
    for c in warehouse.cards:
        e = cats.setdefault(
            c.category, {"category": c.category, "total": 0, "certified": 0, "by_status": {}}
        )
        e["total"] += 1
        if c.selectable:
            e["certified"] += 1
        e["by_status"][c.status.value] = e["by_status"].get(c.status.value, 0) + 1
    return {
        "loaded": True,
        "categories": sorted(cats.values(), key=lambda x: x["category"]),
        "rollup": warehouse.status_rollup(),
    }


def select_certified(warehouse: "ToolWarehouse", category: str) -> dict[str, Any]:
    """Certified-only selection for a category — the source-of-record gate a
    build uses. Returns the selectable certified tools + the gate verdict."""
    selected = warehouse.select(category)
    ok, reason = warehouse.can_select(category)
    return {
        "loaded": True,
        "category": category,
        "can_select": ok,
        "reason": reason,
        "certified_tools": [card_to_dict(c) for c in selected],
    }
