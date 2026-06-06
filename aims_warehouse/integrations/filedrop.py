"""File Drop adapter — the ARTIFACT SHIP / PUBLISH layer (IPFS, opt-in public only).

Maps to the Tool Warehouse `storage` shelf. ⚠️ IPFS public DHT → this is for
INTENTIONALLY-public plug sharing only; private customer downloads default to
object storage (R2/S3), never File Drop. The adapter MUST default to NOT
publishing — publish/share is an explicit, opt-in action (later increment).
Live-health only here.
"""
from __future__ import annotations

from .base import BaseIntegration


class FileDropAdapter(BaseIntegration):
    name = "File Drop"
    warehouse_category = "storage"
    base_url_env = "FILEDROP_BASE_URL"
    token_env = "FILEDROP_TOKEN"
    health_path = "/"  # web UI root answers 200 when the IPFS node + app are up
