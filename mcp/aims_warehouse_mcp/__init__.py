"""A.I.M.S. Tool Warehouse — MCP server.

Exposes the warehouse catalog + the AIMS Advisor as MCP tools so any agent
(Claude Desktop, Cursor, an IDE, or a company's own agent) can search, select,
and get goal-mode tool recommendations. Thin client over the warehouse HTTP API;
authenticates with a warehouse API key (per-tenant over hosted HTTP, or via env
for local stdio).
"""

__version__ = "0.1.0"
