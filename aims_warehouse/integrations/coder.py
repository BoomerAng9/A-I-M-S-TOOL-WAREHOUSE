"""Coder adapter — the BUILD ROOM (interactive/operator dev workspaces).

Maps to the Tool Warehouse `deployment` shelf. Optional + live-health only in this
increment; workspace actions (create/stop/archive) land in a later increment.
"""
from __future__ import annotations

from .base import BaseIntegration


class CoderAdapter(BaseIntegration):
    name = "Coder"
    warehouse_category = "deployment"
    base_url_env = "CODER_BASE_URL"
    token_env = "CODER_TOKEN"
    health_path = "/healthz"  # Coder liveness endpoint → 200 "ok"
