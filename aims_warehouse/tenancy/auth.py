"""FastAPI auth dependencies for the warehouse.

Two callers:
  - OPERATOR — the static ``X-Service-Token`` (env ``TOOL_WAREHOUSE_TOKEN``).
    Superuser; gates /admin and may read the catalog.
  - TENANT — a per-tenant API key (``Authorization: Bearer aimswh_...`` or
    ``X-API-Key``). Scoped, revocable, expirable.

If tenancy is disabled (no DB), only the operator token is accepted — exactly the
P1 catalog gate. Token comparison is constant-time and byte-based (a non-ASCII
header byte can never raise — it just fails to match). DB blips during key
resolution fail CLOSED to 401, never 500.
"""
from __future__ import annotations

import hmac
import os
from typing import Optional

from fastapi import Header, HTTPException

from . import db, store


class Caller:
    __slots__ = ("kind", "tenant_id", "key_id", "scopes")

    def __init__(self, kind: str, tenant_id: Optional[str] = None,
                 key_id: Optional[str] = None, scopes: Optional[list[str]] = None) -> None:
        self.kind = kind            # "operator" | "tenant"
        self.tenant_id = tenant_id
        self.key_id = key_id
        self.scopes = scopes or []


def _admin_token() -> Optional[str]:
    return os.environ.get("TOOL_WAREHOUSE_TOKEN") or None


def _is_admin(token: Optional[str]) -> bool:
    expected = _admin_token()
    if not (expected and token):
        return False
    # Byte comparison: hmac.compare_digest raises TypeError on non-ASCII *str*,
    # and Starlette latin-1-decodes header bytes, so a single 0x80-0xFF header
    # byte would otherwise throw a 500 on the auth primitive. Bytes can't raise.
    try:
        return hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8"))
    except Exception:
        return False


def _extract_key(authorization: Optional[str], x_api_key: Optional[str]) -> Optional[str]:
    if x_api_key:
        return x_api_key.strip()
    if authorization and authorization[:7].lower() == "bearer ":
        return authorization[7:].strip()
    return None


async def require_admin(x_service_token: Optional[str] = Header(default=None)) -> None:
    if not _is_admin(x_service_token):
        raise HTTPException(status_code=401, detail="operator token required (X-Service-Token)")


async def resolve_caller(
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None),
    x_service_token: Optional[str] = Header(default=None),
) -> Caller:
    # Operator superuser via the static token.
    if _is_admin(x_service_token):
        return Caller("operator", scopes=["*"])
    # Tenant via API key (only if tenancy is live). A DB error fails closed.
    raw = _extract_key(authorization, x_api_key)
    if raw and db.tenancy_enabled():
        try:
            info = await store.resolve_key(raw)
        except Exception:
            info = None
        if info:
            await store.touch_key(info["key_id"])  # best-effort; swallows its own errors
            return Caller("tenant", tenant_id=info["tenant_id"], key_id=info["key_id"], scopes=info["scopes"])
    raise HTTPException(
        status_code=401,
        detail="provide a tenant API key (Authorization: Bearer aimswh_... or X-API-Key) or the operator token",
    )
