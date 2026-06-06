# A.I.M.S. Tool Warehouse — skills

## `tool-gap-analysis`
A real-time gap-analysis skill for coding agents (Claude Code, Cursor, etc.). It
reviews the code in the **current session** (no clone/upload), finds capability
gaps, and consults the A.I.M.S. Tool Warehouse via its MCP to recommend certified
tools to integrate and where. Also works from a stated goal alone.

### Install (Claude Code)
Copy the skill into your skills directory:
```bash
cp -r skill/tool-gap-analysis ~/.claude/skills/
```
Then connect the warehouse MCP (so the skill can call it):
- **Hosted:** point your client at `https://warehouse.aimanagedsolutions.cloud/mcp`
  with `Authorization: Bearer aimswh_<your key>`.
- **Local (stdio):** `pip install ./mcp` then add `aims-warehouse-mcp` to your MCP
  client config with `WAREHOUSE_API_KEY` set (see `mcp/README.md`).

### Use
- "review my stack — what am I missing?" (code mode)
- "I want to build a CRM with email automation — what should I use?" (goal mode)
