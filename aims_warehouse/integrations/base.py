"""Integration adapters for the deployed FOAI infrastructure tools (Coder,
Autobase, File Drop, Hermes Agent).

Each adapter is OPTIONAL: when its base-URL env var is unset it reports
``not_configured`` and nothing in Charlotte depends on it. ``health_check()`` is
a live liveness probe that powers ``GET /api/integrations/health`` and (in a
later increment) the live-availability indicator on the tool's card in the
Picker_Ang Tool Warehouse.

Module boundary is intentionally ONE-WAY: ``integrations`` feed live status INTO
``picker_ang``'s ToolWarehouse; the warehouse never imports ``integrations``.
The catalog *curation* verdict stays ``ToolStatus`` (picker_ang/tool_warehouse.py);
this module only adds the orthogonal *live-availability* axis (up / down /
unconfigured), which is the contract the owner's integration-health endpoint
defines — not a fork of the curation vocabulary.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

_log = logging.getLogger(__name__)

# Live-availability of a deployed integration (distinct from the catalog
# ToolStatus curation verdict).
HEALTHY = "healthy"            # reachable + responding
UNHEALTHY = "unhealthy"        # configured but down / erroring / unreachable
NOT_CONFIGURED = "not_configured"  # no base URL wired — optional, never a hard dep


@dataclass
class IntegrationHealth:
    name: str
    status: str  # healthy | unhealthy | not_configured
    warehouse_category: str
    detail: str = ""
    http_code: int | None = None
    latency_ms: int | None = None
    configured: bool = False

    def to_dict(self) -> dict[str, Any]:
        # NOTE: deliberately omits base_url/token — never leak internal topology
        # or credentials through the health surface.
        return {
            "name": self.name,
            "status": self.status,
            "warehouse_category": self.warehouse_category,
            "detail": self.detail,
            "http_code": self.http_code,
            "latency_ms": self.latency_ms,
            "configured": self.configured,
        }


class BaseIntegration:
    """One deployed external tool. Subclasses set the class attributes below.

    Reads its base URL + optional token from the environment so values stay
    config-driven and out of code (the owner places secret tokens; the health
    probe does not require them — reachability alone proves liveness)."""

    name: str = ""
    warehouse_category: str = ""  # which Tool Warehouse shelf this tool maps to
    base_url_env: str = ""
    token_env: str = ""
    health_path: str = "/"
    # HTTP codes that still mean "the service is up". 401/403 = reachable but
    # needs auth — still proves the service answers, so still healthy for a
    # liveness probe (a 5xx or a connection error is what counts as down).
    _UP_MIN, _UP_MAX = 200, 499

    @property
    def base_url(self) -> str:
        return os.environ.get(self.base_url_env, "").rstrip("/")

    @property
    def token(self) -> str:
        return os.environ.get(self.token_env, "") if self.token_env else ""

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    async def health_check(self) -> IntegrationHealth:
        if not self.base_url:
            return IntegrationHealth(
                name=self.name,
                status=NOT_CONFIGURED,
                warehouse_category=self.warehouse_category,
                detail=f"{self.base_url_env} unset",
                configured=False,
            )
        url = self.base_url + self.health_path
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
                resp = await client.get(url, headers=self._headers())
            dt = int((time.monotonic() - t0) * 1000)
            up = self._UP_MIN <= resp.status_code <= self._UP_MAX
            return IntegrationHealth(
                name=self.name,
                status=HEALTHY if up else UNHEALTHY,
                warehouse_category=self.warehouse_category,
                detail="reachable" if up else f"HTTP {resp.status_code}",
                http_code=resp.status_code,
                latency_ms=dt,
                configured=True,
            )
        except Exception as exc:  # noqa: BLE001 — health probe is best-effort
            dt = int((time.monotonic() - t0) * 1000)
            return IntegrationHealth(
                name=self.name,
                status=UNHEALTHY,
                warehouse_category=self.warehouse_category,
                detail=f"{type(exc).__name__}: {str(exc)[:80]}",
                latency_ms=dt,
                configured=True,
            )
