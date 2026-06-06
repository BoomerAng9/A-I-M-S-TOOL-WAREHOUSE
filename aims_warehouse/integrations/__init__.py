"""Charlotte integration adapters — optional, live-health-first wiring of the
deployed FOAI infrastructure tools (Coder, Autobase, File Drop, Hermes Agent).

See base.py for the one-way module boundary: integrations feed the Picker_Ang
Tool Warehouse; the warehouse never depends on integrations.
"""
from __future__ import annotations

from .base import HEALTHY, NOT_CONFIGURED, UNHEALTHY, BaseIntegration, IntegrationHealth
from .registry import IntegrationRegistry

__all__ = [
    "BaseIntegration",
    "IntegrationHealth",
    "IntegrationRegistry",
    "HEALTHY",
    "UNHEALTHY",
    "NOT_CONFIGURED",
]
