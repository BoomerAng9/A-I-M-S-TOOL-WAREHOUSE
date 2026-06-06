"""Minimal MCP client — connects to the hosted AIMS Tool Warehouse MCP, lists
tools, and runs the advisor. Also doubles as a smoke test.

  MCP_URL  (default http://localhost:8090/mcp)
  WH_KEY   warehouse API key (or operator token)
  GOAL     (optional) goal for recommend_tools
"""
from __future__ import annotations

import asyncio
import json
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main() -> None:
    url = os.environ.get("MCP_URL", "http://localhost:8090/mcp")
    key = os.environ.get("WH_KEY", "")
    goal = os.environ.get("GOAL", "a realtime chat app with auth and a database")
    headers = {"Authorization": f"Bearer {key}"} if key else {}

    async with streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("TOOLS:", [t.name for t in tools.tools])

            res = await session.call_tool("recommend_tools", {"goal": goal, "limit": 4})
            for c in res.content:
                if getattr(c, "type", "") == "text":
                    try:
                        d = json.loads(c.text)
                        print("ADVISOR:", d.get("advisor"), "| recs:",
                              [r.get("tool") for r in d.get("recommendations", [])])
                    except Exception:
                        print("RESULT:", c.text[:400])


if __name__ == "__main__":
    asyncio.run(main())
