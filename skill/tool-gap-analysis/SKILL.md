---
name: tool-gap-analysis
description: "Use when a builder wants to know which tools or integrations their project is missing — e.g. 'what am I missing', 'what should I add to my stack', 'review my codebase for gaps', 'which tools should I integrate', or a goal like 'I want to build X, what should I use?'. Reviews the code in the CURRENT session in real time (no clone/upload), identifies capability gaps (auth, data, deploy, payments, observability, etc.), and consults the A.I.M.S. Tool Warehouse via its MCP to recommend certified tools to integrate and where. Also works from a stated goal alone."
---

# Tool Gap Analysis — A.I.M.S. Tool Warehouse

Find the tools a builder is missing and where to wire them, using the **certified
A.I.M.S. Tool Warehouse** catalog as the source of record for recommendations.

## When this fires
- "what am I missing", "what should I add", "review my stack", "which tools should I integrate"
- A goal with no code yet: "I want to build X — what should I use?"

## Two modes
1. **Code mode (real-time, in-session):** review the code the agent is **already
   working in** — the working tree / open files. Do NOT clone, fetch, or ask the
   user to upload anything; read what is already here, like a live code review.
2. **Goal mode:** the user only states a goal — skip the code review.

## The warehouse MCP (required)
This skill depends on the `aims-tool-warehouse` MCP server. Its tools:
- `recommend_tools(goal)` — the **AIMS Advisor**: best certified tools + where + why.
- `search_tools(query, category, certified_only)` — search the catalog.
- `select_certified(category)` — certified tools for one category.
- `list_categories()` — the shelves.

If that MCP is not connected, tell the user to add it and stop:
- **Hosted:** `https://warehouse.aimanagedsolutions.cloud/mcp` with header `Authorization: Bearer aimswh_<their key>`.
- **Local (stdio):** `pip install aims-warehouse-mcp`, run `aims-warehouse-mcp`, set `WAREHOUSE_API_KEY`.

## Workflow

### Code mode
1. **Map the stack.** Read the project with your own file tools: languages,
   frameworks, package manifests (`package.json` / `pyproject.toml` / `go.mod` /
   `Cargo.toml` / `Gemfile` …), infra (`Dockerfile` / `compose` / `terraform` /
   CI configs), env/config, and the directory layout. State plainly what the app
   DOES and what it is built with.
2. **Find the gaps.** Against this capability checklist, identify what is MISSING
   or weak for *this kind of app* — be specific and defensible, citing files:
   Auth & identity · Data & persistence · Migrations · Caching / queues ·
   Payments / billing · File / object storage · Email / notifications ·
   Search / vector · Observability (logs / metrics / traces / error tracking) ·
   CI/CD & deploy · Background jobs · Rate limiting / security · Analytics ·
   AI / LLM routing · MCP / agent integrations.
   Examples: "no error tracking", "auth is hand-rolled", "no DB migrations",
   "no caching layer", "secrets are committed".
3. **Map gaps → tools.** For each real gap, call `recommend_tools(goal="<the gap
   stated as a goal>")` — or `search_tools` / `select_certified(category)` when a
   gap maps cleanly to one shelf. Prefer certified tools.
4. **Produce the Gap Report** (below).

### Goal mode
1. Call `recommend_tools(goal="<the user's goal>")` (optionally a few
   `search_tools` calls for breadth).
2. Produce the Gap Report from the recommendations.

## Output — the Gap Report
A short summary line (what the app is + the headline gaps), then a table:

| Gap | Recommended tool | Where to wire it | Why | Certified |
|-----|------------------|------------------|-----|-----------|

Then a **prioritized integration order** (high → low), and offer to wire the top
recommendation now (write the integration code / config) if the user wants.

## Rules
- Recommend **only** tools the warehouse returns — never invent tools. The
  warehouse is the source of record.
- Prefer **certified** tools; mark anything non-certified as "needs review".
- Don't fabricate gaps — only real ones grounded in the actual code or goal.
- Real-time + in-session: review the code that is already here; never clone/upload.
- Customer-safe language: in user-facing output say "the AIMS Advisor" / "the
  warehouse" — never expose internal names.
