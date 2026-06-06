# A.I.M.S. Tool Warehouse — MCP server

Exposes the A.I.M.S. Tool Warehouse catalog + the **AIMS Advisor** as MCP tools, so
any agent (Claude Desktop, Cursor, an IDE, or a company's own agent) can search,
select, and get goal-mode tool recommendations.

## Tools
| Tool | Purpose |
|------|---------|
| `search_tools(query, category, certified_only, limit)` | Search the catalog |
| `list_categories()` | Shelves + total/certified counts |
| `select_certified(category)` | Certified, build-ready tools for a category |
| `recommend_tools(goal, limit)` | **AIMS Advisor** — which tools to integrate, where, why |

Authenticate with a warehouse API key (`aimswh_…`). Get one from your warehouse
account, or use an operator token.

## Local (stdio) — Claude Desktop / Cursor / IDEs
```bash
pip install .
```
Add to your MCP client config:
```json
{
  "mcpServers": {
    "aims-tool-warehouse": {
      "command": "aims-warehouse-mcp",
      "env": {
        "WAREHOUSE_API_KEY": "aimswh_your_key",
        "WAREHOUSE_API_URL": "https://warehouse.aimanagedsolutions.cloud"
      }
    }
  }
}
```

## Hosted (streamable-HTTP)
Point your agent at the hosted endpoint and pass your key as a header:
```
URL:    https://warehouse.aimanagedsolutions.cloud/mcp
Header: Authorization: Bearer aimswh_your_key
```
Each request carries the caller's own key, so usage is per-company and metered.

## Run the hosted server yourself
```bash
aims-warehouse-mcp --http --host 0.0.0.0 --port 8090
# WAREHOUSE_API_URL selects the backend (default: the public warehouse)
```
