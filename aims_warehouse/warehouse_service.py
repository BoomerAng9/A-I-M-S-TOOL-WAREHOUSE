"""Tool Warehouse — standalone, independently-deployable service.

A self-contained ASGI app exposing the INTERNAL fast-retrieval tool catalog (the
Picker_Ang inventory) plus live integration health. It runs the SAME logic
Charlotte's `/api/warehouse/*` handlers run — both call the shared functions in
`charlotte.integrations.catalog`, so the two surfaces can never drift — but as
its OWN thing: its own container, its own port, its own minimal env (no Neon
DSN, no signing key — least privilege), reading the SAME physical inventory file
Charlotte reads (one source of truth, bind-mounted read-only into both). It is
NOT wired into Charlotte's build path; it never imports the build engine, Neon,
or `api.routes` — only the warehouse model, the integration registry, and the
shared catalog logic.

Run: `uvicorn charlotte.warehouse_service:app --host 0.0.0.0 --port 8090`

Auth: a single shared service token in the `X-Service-Token` header (env
`TOOL_WAREHOUSE_TOKEN`). When that env is unset the protected routes are OPEN —
acceptable only when the service is bound to loopback; set the token to gate it
on a shared network. `/health` is always unauthenticated (liveness only, no
catalog rows, no integration topology).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query

from .integrations.catalog import category_rollup, query_tools, select_certified
from .integrations.registry import IntegrationRegistry
from .picker_ang.tool_warehouse import ToolWarehouse

_DEFAULT_DATA = (
    Path(__file__).resolve().parent
    / "picker_ang"
    / "data"
    / "foai-tool-inventory-log.jsonl"
)


def _data_path() -> Path:
    override = os.environ.get("TOOL_WAREHOUSE_DATA")
    return Path(override) if override else _DEFAULT_DATA


# Loaded once at import — the inventory is a static, read-only bind-mount. Both
# loads degrade to None on any error so the service stays up and reports it via
# /health (never crashes on a missing/garbled file).
_registry = IntegrationRegistry()
try:
    _warehouse: ToolWarehouse | None = ToolWarehouse.from_jsonl(_data_path())
except Exception:
    _warehouse = None


app = FastAPI(title="FOAI Tool Warehouse", version="0.1.0")


def _require_token(x_service_token: str | None = Header(default=None)) -> None:
    """Gate protected routes behind the shared service token when one is set.
    Unset = open (loopback-only deploy); set the token to gate on a shared net."""
    expected = os.environ.get("TOOL_WAREHOUSE_TOKEN")
    if not expected:
        return
    if x_service_token != expected:
        raise HTTPException(status_code=401, detail="invalid or missing X-Service-Token")


@app.get("/health")
async def health() -> dict[str, Any]:
    """Liveness of the service itself (no auth, no integration probe)."""
    return {
        "service": "tool-warehouse",
        "status": "ok",
        "catalog_loaded": _warehouse is not None,
        "total_tools": len(_warehouse.cards) if _warehouse is not None else 0,
        "auth_required": bool(os.environ.get("TOOL_WAREHOUSE_TOKEN")),
    }


@app.get("/tools", dependencies=[Depends(_require_token)])
async def tools(
    category: str | None = None,
    certified: bool = False,
    q: str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    """Filterable catalog query; live health joined for the deployed integration
    tools present in the result set."""
    if _warehouse is None:
        return {"loaded": False, "total": 0, "tools": []}
    return await query_tools(
        _warehouse,
        _registry,
        category=category,
        certified=certified,
        q=q,
        limit=limit,
    )


@app.get("/categories", dependencies=[Depends(_require_token)])
async def categories() -> dict[str, Any]:
    """Per-category shelf rollup + canonical status rollup."""
    if _warehouse is None:
        return {"loaded": False, "categories": []}
    return category_rollup(_warehouse)


@app.get("/select", dependencies=[Depends(_require_token)])
async def select(category: str = Query(...)) -> dict[str, Any]:
    """Certified-only source-of-record selection gate for a category."""
    if _warehouse is None:
        return {
            "loaded": False,
            "category": category,
            "can_select": False,
            "certified_tools": [],
        }
    return select_certified(_warehouse, category)


@app.get("/integrations/health", dependencies=[Depends(_require_token)])
async def integrations_health() -> dict[str, Any]:
    """Live availability of the deployed integration tools (concurrent, non-raising)."""
    results = await _registry.health_check_all()
    return {
        "summary": {h.name: h.status for h in results},
        "integrations": [h.to_dict() for h in results],
    }
