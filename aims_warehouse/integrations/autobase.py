"""Autobase adapter — the DATABASE SHELF (optional self-hosted Postgres provider).

Maps to the Tool Warehouse `database` shelf. Neon-branch stays the DEFAULT per-plug
DB; Autobase is provisioned only for self-hosted/sovereign plugs (later increment).
Live-health only here.
"""
from __future__ import annotations

from .base import BaseIntegration


class AutobaseAdapter(BaseIntegration):
    name = "Autobase"
    warehouse_category = "database"
    base_url_env = "AUTOBASE_BASE_URL"
    token_env = "AUTOBASE_TOKEN"
    health_path = "/"  # console UI root answers 200 when the service is up
