"""Warehouse database pool + schema (tenants / api_keys / usage_events).

Connects ONLY with ``WAREHOUSE_DATABASE_URL`` (a dedicated Neon project isolated
from Charlotte). The migration is serialised across replicas with a transaction
advisory lock (``CREATE ... IF NOT EXISTS`` is NOT atomic across sessions, so two
cold-starting replicas could otherwise collide). If the URL is unset, tenancy is
simply off; if the URL is SET but the DB can't be opened/migrated, that is logged
loudly and ``degraded()`` reports True — it is a real outage, not 'no DB'.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from psycopg_pool import AsyncConnectionPool

_log = logging.getLogger("aims_warehouse.tenancy.db")

_pool: Optional[AsyncConnectionPool] = None
_ready: bool = False

# Stable advisory-lock id so simultaneously cold-starting replicas serialise DDL.
_MIGRATION_LOCK_ID = 0x414D5357  # "AMSW"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tenants (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    slug        TEXT UNIQUE NOT NULL,
    plan        TEXT NOT NULL DEFAULT 'free',
    status      TEXT NOT NULL DEFAULT 'active',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS api_keys (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name         TEXT,
    key_prefix   TEXT UNIQUE NOT NULL,
    key_hash     TEXT NOT NULL,
    scopes       TEXT[] NOT NULL DEFAULT ARRAY['catalog:read'],
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ,
    revoked_at   TIMESTAMPTZ,
    last_used_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS api_keys_prefix_idx ON api_keys (key_prefix);
CREATE INDEX IF NOT EXISTS api_keys_tenant_idx ON api_keys (tenant_id);

CREATE TABLE IF NOT EXISTS usage_events (
    id          BIGSERIAL PRIMARY KEY,
    key_id      UUID,
    tenant_id   UUID,
    endpoint    TEXT NOT NULL,
    status      INT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS usage_events_tenant_idx ON usage_events (tenant_id, created_at DESC);
"""


def database_url() -> Optional[str]:
    return os.environ.get("WAREHOUSE_DATABASE_URL") or None


def configured() -> bool:
    return database_url() is not None


def tenancy_enabled() -> bool:
    """True only when the pool is actually open + migrated."""
    return _ready


def degraded() -> bool:
    """Configured but not ready — a real misconfiguration/outage, not 'no DB'."""
    return configured() and not _ready


def pool() -> AsyncConnectionPool:
    if _pool is None:
        raise RuntimeError("warehouse db pool not initialised (WAREHOUSE_DATABASE_URL unset/unreachable)")
    return _pool


async def open_pool() -> None:
    """Open the pool + run the advisory-locked idempotent migration. Non-fatal:
    failure leaves tenancy disabled (loudly logged) rather than crashing the
    service. Never leaks a half-open pool."""
    global _pool, _ready
    url = database_url()
    if not url or _pool is not None:
        return
    p: Optional[AsyncConnectionPool] = None
    try:
        p = AsyncConnectionPool(url, min_size=1, max_size=15, open=False, kwargs={"autocommit": True})
        await p.open(wait=True, timeout=10)
        async with p.connection() as conn:
            async with conn.transaction():
                # Only one replica migrates at a time; lock releases at txn end.
                await conn.execute("SELECT pg_advisory_xact_lock(%s)", (_MIGRATION_LOCK_ID,))
                await conn.execute(SCHEMA_SQL)
        _pool = p
        _ready = True
    except Exception:
        _ready = False
        # URL set but DB unreachable/misconfigured: surface it — do NOT silently
        # fall back to operator-token-only for a service whose job is tenant gating.
        _log.exception(
            "WAREHOUSE_DATABASE_URL is set but the warehouse DB could not be opened/migrated; "
            "tenancy DISABLED (catalog falls back to operator-token gate)"
        )
        if p is not None:
            try:
                await p.close()
            except Exception:
                pass
        _pool = None


async def close_pool() -> None:
    global _pool, _ready
    if _pool is not None:
        try:
            await _pool.close()
        except Exception:
            pass
    _pool = None
    _ready = False
