"""Frontend router — public-facing /app/* surface for the Tool Warehouse.

All catalog reads are tenant-projected via redact_payload(_, "tenant").
No auth dependency; no scope gate; no operator/admin exposure.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

# Catalog functions (same path both Charlotte and standalone use — never drift)
from ..integrations.catalog import category_rollup, query_tools, select_certified
from ..redaction import redact_payload
from . import sessions
from ..tenancy import store
from ..billing import email as bemail

# These are module-level singletons injected by warehouse_service at startup.
# The router cannot import _warehouse/_registry directly (circular import),
# so warehouse_service.py calls `init_frontend(warehouse, registry)` once.
_warehouse = None
_registry = None

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

frontend_router = APIRouter(prefix="/app", tags=["frontend"])


def init_frontend(warehouse, registry) -> None:
    """Called once from warehouse_service after catalog load, to inject singletons."""
    global _warehouse, _registry
    _warehouse = warehouse
    _registry = registry


# ──────────────────────────── helpers ─────────────────────────────────────────

_PAGE_SIZE = 60


def _sort_key(tool: dict) -> tuple:
    """Certified (selectable) tools first, then alphabetical."""
    return (0 if tool.get("selectable") else 1, (tool.get("name") or "").lower())


def _paginate(tools: list, page: int) -> tuple[list, int, int, int]:
    """Return (page_tools, total_pages, prev_page, next_page)."""
    total = len(tools)
    total_pages = max(1, math.ceil(total / _PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))
    sliced = tools[page * _PAGE_SIZE : (page + 1) * _PAGE_SIZE]
    prev_p = page - 1 if page > 0 else None
    next_p = page + 1 if page < total_pages - 1 else None
    return sliced, total_pages, prev_p, next_p


def _verdict_class(verdict: str) -> str:
    if verdict == "PASS":
        return "verdict-pass"
    if verdict in ("FAIL", "BLOCKED"):
        return "verdict-block"
    return "verdict-review"


async def _category_rollup_tenant() -> dict[str, Any]:
    """category_rollup is sync; wrap + redact for tenant projection."""
    if _warehouse is None:
        return {"loaded": False, "categories": [], "rollup": {}}
    data = category_rollup(_warehouse)
    return redact_payload(data, "tenant")


async def _zone_tools(category: str, page: int = 0) -> tuple[list, int, int | None, int | None, int]:
    """Fetch certified-first tools for a zone. Returns (tools, total_pages, prev, next, raw_total)."""
    if _warehouse is None:
        return [], 1, None, None, 0
    # High limit so we get all tools in category, then slice in Python.
    raw = await query_tools(
        _warehouse, _registry,
        category=category, certified=False, q=None, limit=20000
    )
    redacted = redact_payload(raw, "tenant")
    tools = sorted(redacted.get("tools", []), key=_sort_key)
    raw_total = len(tools)
    page_tools, total_pages, prev_p, next_p = _paginate(tools, page)
    return page_tools, total_pages, prev_p, next_p, raw_total


async def _build_floor_context() -> dict[str, Any]:
    """Build all server-side data the floor template needs."""
    rollup_data = await _category_rollup_tenant()
    categories = rollup_data.get("categories", [])

    # Attach health glow per category using integration health (best-effort).
    # Only ~4 deployed integrations have live health; everything else = dim/unknown.
    health_map: dict[str, str] = {}
    if _registry is not None:
        try:
            results = await _registry.health_check_all()
            for h in results:
                # Map integration health onto its category via the warehouse card.
                if _warehouse is not None:
                    for card in _warehouse.cards:
                        if card.name == h.name:
                            health_map[card.category] = h.status
                            break
        except Exception:
            pass

    zones = []
    max_certified = max((c.get("certified", 0) for c in categories), default=1) or 1
    for cat in sorted(categories, key=lambda x: x.get("category", "")):
        name = cat.get("category", "")
        total = cat.get("total", 0)
        certified = cat.get("certified", 0)
        fill_pct = round((certified / max_certified) * 100) if max_certified else 0
        health = health_map.get(name, "unknown")
        zones.append({
            "name": name,
            "total": total,
            "certified": certified,
            "fill_pct": fill_pct,
            "health": health,   # "healthy" | "unhealthy" | "degraded" | "unknown"
        })

    total_tools = len(_warehouse.cards) if _warehouse else 0
    pass_count = rollup_data.get("rollup", {}).get("PASS", 0) if rollup_data else 0

    return {
        "zones": zones,
        "total_tools": total_tools,
        "certified_count": pass_count,
        "catalog_loaded": _warehouse is not None,
    }


# ──────────────────────────── routes ──────────────────────────────────────────

@frontend_router.get("/", response_class=HTMLResponse)
async def floor(request: Request):
    """The warehouse floor — hero count, shelf-zone grid, Ctrl+K palette affordance."""
    ctx = await _build_floor_context()
    return templates.TemplateResponse(request, "floor.html", ctx)


@frontend_router.get("/zone/{category}", response_class=HTMLResponse)
async def zone_partial(
    request: Request,
    category: str,
    page: int = Query(default=0, ge=0),
):
    """htmx partial — paginated certified-first tool grid for one shelf-zone."""
    tools, total_pages, prev_p, next_p, raw_total = await _zone_tools(category, page)
    # Attach verdict CSS class
    for t in tools:
        t["_verdict_class"] = _verdict_class(t.get("verdict", ""))
    return templates.TemplateResponse(
        request,
        "partials/zone.html",
        {
            "category": category,
            "tools": tools,
            "page": page,
            "total_pages": total_pages,
            "prev_page": prev_p,
            "next_page": next_p,
            "raw_total": raw_total,
        },
    )


@frontend_router.get("/tool/{name}", response_class=HTMLResponse)
async def tool_modal(request: Request, name: str):
    """htmx partial — tool detail modal (name/category/verdict/install/origin/note)."""
    tool: dict[str, Any] | None = None
    if _warehouse is not None:
        raw = await query_tools(
            _warehouse, _registry,
            category=None, certified=False, q=name, limit=200
        )
        redacted = redact_payload(raw, "tenant")
        for t in redacted.get("tools", []):
            if (t.get("name") or "").lower() == name.lower():
                tool = t
                break
        # Fallback: first match
        if tool is None and redacted.get("tools"):
            tool = redacted["tools"][0]

    if tool:
        tool["_verdict_class"] = _verdict_class(tool.get("verdict", ""))
    return templates.TemplateResponse(
        request, "partials/tool_modal.html", {"tool": tool, "query_name": name}
    )


@frontend_router.get("/search", response_class=HTMLResponse)
async def search_partial(
    request: Request,
    q: str = Query(default="", min_length=0),
):
    """htmx partial — command-palette results (server-side filter)."""
    results: list[dict] = []
    if q and _warehouse is not None:
        raw = await query_tools(
            _warehouse, _registry,
            category=None, certified=False, q=q.strip(), limit=20
        )
        redacted = redact_payload(raw, "tenant")
        results = sorted(redacted.get("tools", []), key=_sort_key)[:20]
        for t in results:
            t["_verdict_class"] = _verdict_class(t.get("verdict", ""))
    return templates.TemplateResponse(
        request, "partials/search_results.html", {"results": results, "q": q}
    )


# ──────────────────────────── auth + account ──────────────────────────────────

def _public_base() -> str:
    return (os.environ.get("WAREHOUSE_PUBLIC_URL") or "https://warehouse.aimanagedsolutions.cloud").rstrip("/")


def _session(request: Request) -> dict | None:
    return sessions.read_session(request.cookies.get(sessions.SESSION_COOKIE, ""))


def _set_session_cookie(resp: Response, tenant_id: str, email: str) -> None:
    resp.set_cookie(
        sessions.SESSION_COOKIE,
        sessions.sign_session(tenant_id, email),
        max_age=sessions._SESSION_TTL,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


@frontend_router.get("/signin", response_class=HTMLResponse)
async def signin_page(request: Request):
    """Email sign-in form. If already signed in, go to the account locker."""
    if _session(request):
        return RedirectResponse("/app/account", status_code=302)
    return templates.TemplateResponse(request, "signin.html", {})


@frontend_router.post("/signin", response_class=HTMLResponse)
async def signin_submit(request: Request, email: str = Form(...)):
    """Find-or-provision a tenant for this email, mail a single-use magic link."""
    email = (email or "").strip().lower()
    if "@" not in email or len(email) < 5:
        return templates.TemplateResponse(request, "signin.html", {"error": "Enter a valid email."})
    link = f"{_public_base()}/app/auth/{sessions.sign_magic(email)}"
    sent = False
    try:
        await store.find_or_provision_tenant(email)  # ensure an account exists
        sent = bemail.send_magic_link(email, link)
    except Exception:  # noqa: BLE001 — never leak DB/email errors to the visitor
        pass
    # If email isn't deliverable (Resend unconfigured / failed), surface the link
    # inline so sign-in still works (preview/dev). Never block on email.
    return templates.TemplateResponse(
        request, "signin_sent.html",
        {"email": email, "sent": sent, "link": None if sent else link},
    )


@frontend_router.get("/auth/{token}")
async def auth_consume(request: Request, token: str):
    """Consume a magic link → set the session cookie → account locker."""
    email = sessions.read_magic(token)
    if not email:
        return templates.TemplateResponse(
            request, "signin.html", {"error": "That sign-in link is invalid or expired."}
        )
    tenant = await store.find_or_provision_tenant(email)
    resp = RedirectResponse("/app/account", status_code=302)
    _set_session_cookie(resp, tenant["id"], email)
    return resp


@frontend_router.get("/signout")
async def signout():
    resp = RedirectResponse("/app/", status_code=302)
    resp.delete_cookie(sessions.SESSION_COOKIE, path="/")
    return resp


async def _account_context(tenant_id: str) -> dict[str, Any]:
    tenant = await store.get_tenant(tenant_id)
    keys = await store.list_keys(tenant_id)
    usage = await store.usage_summary(tenant_id, days=30)
    peak = max((d["calls"] for d in usage["series"]), default=0) or 1
    for d in usage["series"]:
        d["pct"] = round((d["calls"] / peak) * 100)
    return {"tenant": tenant, "keys": keys, "usage": usage}


@frontend_router.get("/account", response_class=HTMLResponse)
async def account(request: Request):
    sess = _session(request)
    if not sess:
        return RedirectResponse("/app/signin", status_code=302)
    ctx = await _account_context(sess["tenant_id"])
    if ctx["tenant"] is None:  # tenant deleted under a live session
        resp = RedirectResponse("/app/signin", status_code=302)
        resp.delete_cookie(sessions.SESSION_COOKIE, path="/")
        return resp
    # Upgrade → the Paperform stepper (Stripe lives on Paperform's side), email pre-filled
    # so the payment ties back to this tenant. Falls back to the native pricing page.
    import urllib.parse as _up
    _pf = os.environ.get("PAPERFORM_FORM_URL", "").strip()
    if _pf:
        _sep = "&" if "?" in _pf else "?"
        upgrade_url = f"{_pf}{_sep}email={_up.quote(sess['email'])}"
    else:
        upgrade_url = "/billing/pricing"
    return templates.TemplateResponse(
        request, "account.html", {**ctx, "email": sess["email"], "upgrade_url": upgrade_url}
    )


@frontend_router.post("/keys", response_class=HTMLResponse)
async def create_key(request: Request, name: str = Form(default="")):
    """Mint a key for the signed-in tenant (revealed once)."""
    sess = _session(request)
    if not sess:
        return HTMLResponse('<div class="auth-gone">Session expired — <a href="/app/signin">sign in</a>.</div>', status_code=401)
    minted = await store.mint_key(sess["tenant_id"], (name or "").strip()[:60] or None, ["catalog:read"], None)
    keys = await store.list_keys(sess["tenant_id"])
    return templates.TemplateResponse(
        request, "partials/keys_table.html", {"keys": keys, "revealed": minted}
    )


@frontend_router.post("/keys/{key_id}/revoke", response_class=HTMLResponse)
async def revoke_key(request: Request, key_id: str):
    """Revoke a key — only if it belongs to the signed-in tenant (ownership gate)."""
    sess = _session(request)
    if not sess:
        return HTMLResponse('<div class="auth-gone">Session expired — <a href="/app/signin">sign in</a>.</div>', status_code=401)
    owner = await store.key_tenant(key_id)
    if owner == sess["tenant_id"]:
        await store.revoke_key(key_id)
    keys = await store.list_keys(sess["tenant_id"])
    return templates.TemplateResponse(
        request, "partials/keys_table.html", {"keys": keys, "revealed": None}
    )
