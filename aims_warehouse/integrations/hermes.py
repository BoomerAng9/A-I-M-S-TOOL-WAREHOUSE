"""Hermes Agent adapter — the SUPERAGENT (OpenRouter/DeepSeek-V4-Pro brain).

Maps to the Tool Warehouse `agent orchestration` shelf. Reachable container-to-
container on aims_aims-network; the API requires a Bearer token, so an
un-authenticated liveness probe returns 401 — which still proves the service is
UP (reachable). Superagent dispatch + the Chicken Hawk→Hermes bridge are a later
increment. Live-health only here.
"""
from __future__ import annotations

from .base import BaseIntegration


class HermesAdapter(BaseIntegration):
    name = "Hermes Agent"
    warehouse_category = "agent orchestration"
    base_url_env = "HERMES_BASE_URL"
    token_env = "HERMES_TOKEN"
    health_path = "/"  # 401 without a token = up-but-needs-auth (still reachable)
