"""Tool Warehouse — standalone, independently-deployable PRODUCT service.

The EXTERNAL multi-tenant product on warehouse.aimanagedsolutions.cloud. It runs
the SAME catalog logic Charlotte's ``/api/warehouse/*`` handlers run (the shared
functions in ``.integrations.catalog``) but as its OWN thing — own image, own
port, own (isolated) database — and Charlotte is just one consumer/tenant.

Auth (two callers):
  - OPERATOR: the static ``X-Service-Token`` (env ``TOOL_WAREHOUSE_TOKEN``).
    Superuser; gates /admin and may read the catalog.
  - TENANT: a per-tenant API key (``Authorization: Bearer aimswh_...`` or
    ``X-API-Key``), scoped/revocable/expirable — how a company's own agent reaches
    the catalog. Catalog routes require the ``catalog:read`` scope.
  ``/health`` is unauthenticated liveness only (no posture disclosure); the full
  tenancy/readiness posture is operator-gated at ``/admin/status``.

Tenancy degrades safely: with no ``WAREHOUSE_DATABASE_URL`` the catalog is gated
by the operator token alone (P1 behaviour), so shipping this code never breaks the
live service before the DB is wired. Interactive docs are disabled in this build.

Run: ``uvicorn aims_warehouse.warehouse_service:app --host 0.0.0.0 --port 8090``
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from . import advisor
from .redaction import redact_payload
from .billing import routes as billing_routes
from .billing import stripe_gateway as billing_gw
from .integrations.catalog import category_rollup, query_tools, select_certified
from .integrations.registry import IntegrationRegistry
from .picker_ang.tool_warehouse import ToolWarehouse
from .tenancy import db as tdb
from .tenancy import store as tstore
from .tenancy.auth import Caller, require_admin, resolve_caller

_log = logging.getLogger("aims_warehouse.service")

_DEFAULT_DATA = (
    Path(__file__).resolve().parent / "picker_ang" / "data" / "foai-tool-inventory-log.jsonl"
)


def _data_path() -> Path:
    override = os.environ.get("TOOL_WAREHOUSE_DATA")
    return Path(override) if override else _DEFAULT_DATA


# Catalog loaded once at import — static, read-only. Degrades to None on any
# error so the service stays up and reports it via /health.
_registry = IntegrationRegistry()
try:
    _warehouse: ToolWarehouse | None = ToolWarehouse.from_jsonl(_data_path())
except Exception:
    _warehouse = None


@asynccontextmanager
async def _lifespan(app: FastAPI):
    await tdb.open_pool()  # opens iff WAREHOUSE_DATABASE_URL set + reachable; else no-op
    try:
        yield
    finally:
        await tdb.close_pool()


# Interactive docs OFF — this is a public surface; do not publish the route map /
# admin attack surface / auth scheme to anonymous callers.
app = FastAPI(
    title="A.I.M.S. Tool Warehouse",
    version="0.2.0",
    lifespan=_lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


# Mount the Stripe paywall (pricing page, checkout, webhook, claim). The router
# degrades to 503 on its money-moving routes until STRIPE_SECRET_KEY / _WEBHOOK_SECRET
# are placed in the environment — so mounting it never breaks the live service.
app.include_router(billing_routes.router)


def require_scope(scope: str):
    """Dependency factory: require `scope` (operator '*' satisfies any)."""
    async def _dep(caller: Caller = Depends(resolve_caller)) -> Caller:
        if "*" not in caller.scopes and scope not in caller.scopes:
            raise HTTPException(status_code=403, detail=f"missing required scope: {scope}")
        return caller
    return _dep


_CATALOG_READ = require_scope("catalog:read")


async def _record(caller: Caller, endpoint: str, status: int = 200) -> None:
    """Best-effort usage metering — never fails the request."""
    if not tdb.tenancy_enabled():
        return
    try:
        await tstore.record_usage(
            key_id=caller.key_id, tenant_id=caller.tenant_id, endpoint=endpoint, status=status
        )
    except Exception:
        pass


_ROOT_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>A.I.M.S. Tool Warehouse</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:#070707;color:#e8ffe8;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;padding:2rem}
.card{max-width:640px;text-align:center}
.tag{font-size:.7rem;letter-spacing:.32em;text-transform:uppercase;color:#39ff14;opacity:.85}
h1{font-size:clamp(1.8rem,5vw,2.8rem);margin:.6rem 0 .3rem;letter-spacing:.04em}
.sub{color:#7dffa0;opacity:.75;font-size:.8rem;letter-spacing:.2em;text-transform:uppercase;margin-bottom:1.6rem}
p{color:#bdebbd;line-height:1.6;font-size:.95rem}
code{background:#0f1a0f;border:1px solid #1f3a1f;color:#9dff9d;padding:.15rem .4rem;border-radius:4px;font-size:.85rem}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#39ff14;margin-right:.5rem;box-shadow:0 0 8px #39ff14}
.foot{margin-top:2rem;font-size:.65rem;letter-spacing:.25em;text-transform:uppercase;color:#2f6b2f}
</style></head>
<body><div class="card">
<div class="tag">AI Managed Solutions</div>
<h1>A.I.M.S. Tool Warehouse</h1>
<div class="sub"><span class="dot"></span>API service &middot; operational</div>
<p>The standalone catalog of certified builder tools. Companies authenticate with an API key to browse and select certified tools &mdash; use the managed agent or bring your own.</p>
<p style="margin-top:1.2rem;font-size:.85rem;opacity:.85">Programmatic access requires an API key:<br><code>Authorization: Bearer aimswh_&hellip;</code></p>
<div class="foot">A.I.M.S. &middot; aimanagedsolutions.cloud</div>
</div></body></html>"""


@app.get("/", response_class=HTMLResponse)
async def root() -> str:
    """Public branded landing so the bare domain isn't a raw 404 (API-first; full UI later)."""
    return _ROOT_HTML


@app.get("/health")
async def health() -> dict[str, Any]:
    """Public liveness only — no auth/tenancy posture disclosure."""
    return {
        "service": "tool-warehouse",
        "status": "ok",
        "catalog_loaded": _warehouse is not None,
    }


# --------------------------- catalog (tenant key OR operator token) ---------------------------
@app.get("/tools")
async def tools(
    category: str | None = None,
    certified: bool = False,
    q: str | None = None,
    limit: int = 500,
    caller: Caller = Depends(_CATALOG_READ),
) -> dict[str, Any]:
    await _record(caller, "/tools")
    if _warehouse is None:
        return {"loaded": False, "total": 0, "tools": []}
    data = await query_tools(
        _warehouse, _registry, category=category, certified=certified, q=q, limit=limit
    )
    return redact_payload(data, caller.kind)


@app.get("/categories")
async def categories(caller: Caller = Depends(_CATALOG_READ)) -> dict[str, Any]:
    await _record(caller, "/categories")
    if _warehouse is None:
        return {"loaded": False, "categories": []}
    return redact_payload(category_rollup(_warehouse), caller.kind)


@app.get("/select")
async def select(category: str = Query(...), caller: Caller = Depends(_CATALOG_READ)) -> dict[str, Any]:
    await _record(caller, "/select")
    if _warehouse is None:
        return {"loaded": False, "category": category, "can_select": False, "certified_tools": []}
    return redact_payload(select_certified(_warehouse, category), caller.kind)


@app.get("/integrations/health")
async def integrations_health(caller: Caller = Depends(_CATALOG_READ)) -> dict[str, Any]:
    await _record(caller, "/integrations/health")
    results = await _registry.health_check_all()
    return {
        "summary": {h.name: h.status for h in results},
        "integrations": [h.to_dict() for h in results],
    }


# --------------------------- advisor (goal-mode tool recommendations) ---------------------------
class RecommendRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=8, ge=1, le=20)


@app.post("/recommend")
async def recommend_tools(body: RecommendRequest, caller: Caller = Depends(_CATALOG_READ)) -> dict[str, Any]:
    """AIMS Advisor — goal mode: which certified tools to integrate, where, and why."""
    await _record(caller, "/recommend")
    if _warehouse is None:
        return {"goal": body.goal, "advisor": "none", "considered": 0, "recommendations": [], "message": "catalog not loaded"}
    return redact_payload(await advisor.recommend(_warehouse, body.goal, limit=body.limit), caller.kind)


# --------------------------- admin: status + tenants + keys (operator only) ---------------------------
class TenantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9-]*$")
    plan: str = Field(default="free", max_length=40)


class KeyCreate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    scopes: list[str] = Field(default_factory=lambda: ["catalog:read"])
    expires_at: str | None = None  # ISO-8601; null = no expiry


def _require_tenancy() -> None:
    if not tdb.tenancy_enabled():
        raise HTTPException(status_code=503, detail="tenancy not configured (WAREHOUSE_DATABASE_URL unset/unreachable)")


@app.get("/admin/status", dependencies=[Depends(require_admin)])
async def admin_status() -> dict[str, Any]:
    """Operator-gated posture: tenancy/readiness incl. configured-but-down."""
    return {
        "service": "tool-warehouse",
        "version": "0.2.0",
        "catalog_loaded": _warehouse is not None,
        "total_tools": len(_warehouse.cards) if _warehouse is not None else 0,
        "auth_required": bool(os.environ.get("TOOL_WAREHOUSE_TOKEN")),
        "db_configured": tdb.configured(),
        "tenancy_enabled": tdb.tenancy_enabled(),
        "degraded": tdb.degraded(),
        "billing": {
            "stripe_configured": billing_gw.secret_configured(),
            "webhook_configured": billing_gw.webhook_configured(),
            "stripe_mode": billing_gw.mode(),  # live | test | unknown — catches a test-key-in-prod misconfig
        },
    }


@app.post("/admin/tenants", dependencies=[Depends(require_admin)])
async def admin_create_tenant(body: TenantCreate) -> dict[str, Any]:
    _require_tenancy()
    try:
        return await tstore.create_tenant(body.name, body.slug, body.plan)
    except Exception:
        _log.exception("admin_create_tenant failed")
        raise HTTPException(status_code=409, detail="could not create tenant (slug may already exist)")


@app.get("/admin/tenants", dependencies=[Depends(require_admin)])
async def admin_list_tenants() -> dict[str, Any]:
    _require_tenancy()
    return {"tenants": await tstore.list_tenants()}


@app.post("/admin/tenants/{tenant_id}/keys", dependencies=[Depends(require_admin)])
async def admin_mint_key(tenant_id: str, body: KeyCreate) -> dict[str, Any]:
    _require_tenancy()
    try:
        return await tstore.mint_key(tenant_id, body.name, body.scopes, body.expires_at)
    except Exception:
        _log.exception("admin_mint_key failed")
        raise HTTPException(status_code=400, detail="could not mint key (check tenant id / expires_at)")


@app.get("/admin/tenants/{tenant_id}/keys", dependencies=[Depends(require_admin)])
async def admin_list_keys(tenant_id: str) -> dict[str, Any]:
    _require_tenancy()
    try:
        return {"keys": await tstore.list_keys(tenant_id)}
    except Exception:
        _log.exception("admin_list_keys failed")
        raise HTTPException(status_code=400, detail="invalid tenant id")


@app.post("/admin/keys/{key_id}/revoke", dependencies=[Depends(require_admin)])
async def admin_revoke_key(key_id: str) -> dict[str, Any]:
    _require_tenancy()
    try:
        ok = await tstore.revoke_key(key_id)
    except Exception:
        _log.exception("admin_revoke_key failed")
        raise HTTPException(status_code=400, detail="invalid key id")
    if not ok:
        raise HTTPException(status_code=404, detail="key not found or already revoked")
    return {"revoked": True, "key_id": key_id}
