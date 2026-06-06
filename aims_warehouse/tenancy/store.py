"""Tenant + API-key + usage persistence (async, psycopg3).

All identifiers are validated as UUIDs before they touch SQL (parameterised
queries throughout — no string interpolation). Key resolution filters revoked /
expired keys in SQL, then constant-time compares the hash in Python.
"""
from __future__ import annotations

import uuid as _uuid
from datetime import datetime, timezone
from typing import Any, Optional

from . import db, keys


def _as_uuid(v: str) -> _uuid.UUID:
    return _uuid.UUID(str(v))


def _parse_ts(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    dt = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(v: Any) -> Optional[str]:
    return v.isoformat() if v else None


def _tenant_row(r) -> dict[str, Any]:
    return {"id": str(r[0]), "name": r[1], "slug": r[2], "plan": r[3], "status": r[4], "created_at": _iso(r[5])}


async def create_tenant(name: str, slug: str, plan: str = "free") -> dict[str, Any]:
    async with db.pool().connection() as conn:
        cur = await conn.execute(
            "INSERT INTO tenants (name, slug, plan) VALUES (%s, %s, %s) "
            "RETURNING id, name, slug, plan, status, created_at",
            (name, slug, plan),
        )
        return _tenant_row(await cur.fetchone())


async def list_tenants() -> list[dict[str, Any]]:
    async with db.pool().connection() as conn:
        cur = await conn.execute(
            "SELECT id, name, slug, plan, status, created_at FROM tenants "
            "ORDER BY created_at DESC LIMIT 500"
        )
        return [_tenant_row(r) for r in await cur.fetchall()]


async def mint_key(tenant_id: str, name: Optional[str], scopes: list[str],
                   expires_at: Optional[str]) -> dict[str, Any]:
    tid = _as_uuid(tenant_id)
    exp = _parse_ts(expires_at)
    full, prefix, secret_hash = keys.generate()
    async with db.pool().connection() as conn:
        cur = await conn.execute(
            "INSERT INTO api_keys (tenant_id, name, key_prefix, key_hash, scopes, expires_at) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "RETURNING id, key_prefix, scopes, created_at, expires_at",
            (tid, name, prefix, secret_hash, scopes or ["catalog:read"], exp),
        )
        r = await cur.fetchone()
    return {
        "id": str(r[0]),
        "tenant_id": tenant_id,
        "api_key": full,  # shown ONCE — never stored, unrecoverable
        "key_prefix": r[1],
        "scopes": list(r[2]),
        "created_at": _iso(r[3]),
        "expires_at": _iso(r[4]),
        "note": "Store this api_key now — it is shown only once and cannot be recovered.",
    }


async def list_keys(tenant_id: str) -> list[dict[str, Any]]:
    tid = _as_uuid(tenant_id)
    now = datetime.now(timezone.utc)
    async with db.pool().connection() as conn:
        cur = await conn.execute(
            "SELECT id, key_prefix, name, scopes, created_at, expires_at, revoked_at, last_used_at "
            "FROM api_keys WHERE tenant_id = %s ORDER BY created_at DESC",
            (tid,),
        )
        rows = await cur.fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append({
            "id": str(r[0]), "key_prefix": r[1], "name": r[2], "scopes": list(r[3]),
            "created_at": _iso(r[4]), "expires_at": _iso(r[5]),
            "revoked_at": _iso(r[6]), "last_used_at": _iso(r[7]),
            "active": r[6] is None and (r[5] is None or r[5] > now),
        })
    return out


async def revoke_key(key_id: str) -> bool:
    kid = _as_uuid(key_id)
    async with db.pool().connection() as conn:
        cur = await conn.execute(
            "UPDATE api_keys SET revoked_at = now() WHERE id = %s AND revoked_at IS NULL",
            (kid,),
        )
        return cur.rowcount > 0


async def resolve_key(full_key: str) -> Optional[dict[str, Any]]:
    parsed = keys.parse(full_key)
    if not parsed:
        return None
    prefix, secret = parsed
    async with db.pool().connection() as conn:
        cur = await conn.execute(
            "SELECT id, tenant_id, key_hash, scopes FROM api_keys "
            "WHERE key_prefix = %s AND revoked_at IS NULL "
            "AND (expires_at IS NULL OR expires_at > now())",
            (prefix,),
        )
        r = await cur.fetchone()
    if not r or not keys.verify(secret, r[2]):
        return None
    return {"key_id": str(r[0]), "tenant_id": str(r[1]), "scopes": list(r[3])}


async def touch_key(key_id: str) -> None:
    try:
        async with db.pool().connection() as conn:
            await conn.execute("UPDATE api_keys SET last_used_at = now() WHERE id = %s", (_as_uuid(key_id),))
    except Exception:
        pass


async def record_usage(key_id: Optional[str], tenant_id: Optional[str], endpoint: str, status: int) -> None:
    async with db.pool().connection() as conn:
        await conn.execute(
            "INSERT INTO usage_events (key_id, tenant_id, endpoint, status) VALUES (%s, %s, %s, %s)",
            (_as_uuid(key_id) if key_id else None, _as_uuid(tenant_id) if tenant_id else None, endpoint, status),
        )
