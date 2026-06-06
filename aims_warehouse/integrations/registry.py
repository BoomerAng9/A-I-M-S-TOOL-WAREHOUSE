"""Integration Registry — the single entry point Charlotte uses to reach the
deployed external tools as OPTIONAL adapters.

Holds the four adapters (Coder, Autobase, File Drop, Hermes) and exposes their
live availability. This is the live-status backend the Picker_Ang Tool Warehouse
renders alongside the static `foai-tool-inventory-log.jsonl` catalog. Adding a
tool = register one BaseIntegration subclass; nothing else in Charlotte changes.
"""
from __future__ import annotations

import asyncio

from .autobase import AutobaseAdapter
from .base import BaseIntegration, IntegrationHealth
from .coder import CoderAdapter
from .filedrop import FileDropAdapter
from .hermes import HermesAdapter


def _default_adapters() -> list[BaseIntegration]:
    return [CoderAdapter(), AutobaseAdapter(), FileDropAdapter(), HermesAdapter()]


class IntegrationRegistry:
    def __init__(self, adapters: list[BaseIntegration] | None = None) -> None:
        self._adapters: dict[str, BaseIntegration] = {}
        for adapter in (adapters if adapters is not None else _default_adapters()):
            self._adapters[adapter.name] = adapter

    def available_tools(self) -> list[str]:
        return list(self._adapters.keys())

    def get(self, name: str) -> BaseIntegration | None:
        return self._adapters.get(name)

    async def get_tool_status(self, name: str) -> IntegrationHealth | None:
        adapter = self._adapters.get(name)
        if adapter is None:
            return None
        return await adapter.health_check()

    async def health_check_all(self) -> list[IntegrationHealth]:
        """Probe every adapter concurrently. Each probe is self-contained and
        non-raising, so one slow/down tool never blocks or fails the others."""
        if not self._adapters:
            return []
        return list(
            await asyncio.gather(
                *(a.health_check() for a in self._adapters.values())
            )
        )
