"""A.I.M.S. Tool Warehouse MCP server — tools over the warehouse HTTP API.

Auth (multi-tenant friendly): the warehouse API key is taken from the incoming
request's ``Authorization: Bearer`` / ``X-API-Key`` header when present (hosted
HTTP = per-tenant), otherwise from the ``WAREHOUSE_API_KEY`` env (local stdio /
single-key hosted). ``WAREHOUSE_API_URL`` selects the backend (defaults to the
public warehouse; set to the internal service URL when hosted alongside it).
"""
from __future__ import annotations

import os
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import Context, FastMCP

WAREHOUSE_API_URL = os.environ.get(
    "WAREHOUSE_API_URL", "https://warehouse.aimanagedsolutions.cloud"
).rstrip("/")

mcp = FastMCP("AIMS Tool Warehouse")


def _key_from_ctx(ctx: Optional[Context]) -> Optional[str]:
    """Pull the caller's warehouse key from the HTTP request headers (hosted)."""
    if ctx is None:
        return None
    try:
        req = getattr(ctx.request_context, "request", None)
        if req is None:
            return None
        auth = req.headers.get("authorization")
        if auth and auth.lower().startswith("bearer "):
            return auth[7:].strip()
        xak = req.headers.get("x-api-key")
        if xak:
            return xak.strip()
    except Exception:
        return None
    return None


def _auth_headers(ctx: Optional[Context]) -> dict[str, str]:
    key = _key_from_ctx(ctx) or os.environ.get("WAREHOUSE_API_KEY", "")
    headers = {"Content-Type": "application/json"}
    if key:
        # The warehouse accepts a tenant key (Bearer / X-API-Key) OR the operator
        # token (X-Service-Token); send the value in each shape and let it decide.
        headers["Authorization"] = f"Bearer {key}"
        headers["X-API-Key"] = key
        headers["X-Service-Token"] = key
    return headers


async def _get(path: str, ctx: Optional[Context], params: dict | None = None) -> Any:
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(WAREHOUSE_API_URL + path, headers=_auth_headers(ctx), params=params or {})
        resp.raise_for_status()
        return resp.json()


async def _post(path: str, ctx: Optional[Context], body: dict) -> Any:
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(WAREHOUSE_API_URL + path, headers=_auth_headers(ctx), json=body)
        resp.raise_for_status()
        return resp.json()


@mcp.tool()
async def search_tools(
    query: str = "",
    category: str = "",
    certified_only: bool = False,
    limit: int = 50,
    ctx: Context = None,
) -> dict:
    """Search the A.I.M.S. Tool Warehouse catalog of builder tools.

    query: free-text match on tool name / category / notes.
    category: restrict to one shelf (exact category name).
    certified_only: only build-ready (certified) tools.
    """
    params: dict[str, Any] = {"limit": limit, "certified": "true" if certified_only else "false"}
    if query:
        params["q"] = query
    if category:
        params["category"] = category
    return await _get("/tools", ctx, params)


@mcp.tool()
async def list_categories(ctx: Context = None) -> dict:
    """List the warehouse shelves (categories) with total + certified counts."""
    return await _get("/categories", ctx)


@mcp.tool()
async def select_certified(category: str, ctx: Context = None) -> dict:
    """Get the certified, build-ready tools for a category (the selection gate)."""
    return await _get("/select", ctx, {"category": category})


@mcp.tool()
async def recommend_tools(goal: str, limit: int = 8, ctx: Context = None) -> dict:
    """AIMS Advisor — given a builder's GOAL, recommend which certified tools to
    integrate, where in the stack, and why. Returns ranked recommendations."""
    return await _post("/recommend", ctx, {"goal": goal, "limit": limit})
