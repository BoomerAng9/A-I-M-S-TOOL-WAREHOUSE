# A.I.M.S. Tool Warehouse

**The warehouse for everything builders need.**

Think of how essential an MCP registry, a model gateway like OpenRouter, a vector
library, or a component resource like 21st.dev has become. The Tool Warehouse is
the place that stores all of it — an *Amazon-style warehouse* of the tools and
resources a builder reaches for, organized on shelves, curated for trust, and
served fast over a simple API.

It is a small, standalone FastAPI service: a curated, **certified-by-default**
catalog plus the live health of your deployed integration tools. It powers tool
selection for an automated build pipeline — but it runs entirely on its own.

---

## What it does

- **Catalog** — a searchable inventory of tools/resources, each on a shelf
  (category): model gateways, databases, deployment targets, RAG/vector
  libraries, API connectors, storage, agent orchestration, frontend shells,
  payments, and more.
- **Curation** — every tool carries a status. Only **certified** tools are
  *selectable* for a build (the source-of-record gate), so an automated builder
  can only ever reach for vetted tools.
- **Live integration health** — adapters report the real-time status of deployed
  services (e.g. a build room, a self-hosted database, object storage, an agent
  brain) so cards show `healthy` / `unhealthy` / `not_configured`.

## Why "certified-by-default"

Each tool has one of seven curation states:

| Status | Selectable for a build? | Meaning |
|---|---|---|
| `certified` | ✅ yes | Vetted and cleared for use |
| `tested` / `candidate` / `raw` | ❌ no | In the pipeline, needs review |
| `deprecated` / `rejected` / `unknown` | ❌ no | Never select |

`GET /select?category=…` returns **only** the certified tools for a shelf — the
gate a builder uses so it can never pick something unvetted.

---

## API

All routes except `/health` are gated by a shared service token sent as the
`X-Service-Token` header (set `TOOL_WAREHOUSE_TOKEN`; if unset, routes are open —
intended for loopback-only use).

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness (no auth). Reports catalog load + tool count. |
| `GET` | `/tools?category=&certified=&q=&limit=` | Filterable catalog; live health joined for integration tools. |
| `GET` | `/categories` | Per-shelf rollup (totals + certified counts) + status rollup. |
| `GET` | `/select?category=` | Certified-only selection gate for a shelf. |
| `GET` | `/integrations/health` | Live status of the configured integration adapters. |

Example:

```bash
curl -s -H "X-Service-Token: $TOOL_WAREHOUSE_TOKEN" \
  "http://localhost:8091/select?category=database"
# -> { "category": "database", "can_select": true,
#      "certified_tools": [ { "name": "Neon Postgres", ... } ] }
```

---

## Run it

```bash
cp .env.example .env
# (optional) generate a token:  openssl rand -hex 32  -> TOOL_WAREHOUSE_TOKEN
docker compose up -d --build
curl -s http://localhost:8091/health
```

Or without Docker:

```bash
pip install -r requirements.txt
uvicorn aims_warehouse.warehouse_service:app --host 0.0.0.0 --port 8090
```

### Configuration

| Env var | Purpose |
|---|---|
| `TOOL_WAREHOUSE_TOKEN` | Shared token for the `X-Service-Token` gate. Unset = open (loopback only). |
| `TOOL_WAREHOUSE_DATA` | Path to a full inventory JSONL. Overrides the bundled example seed. |
| `CODER_BASE_URL` / `AUTOBASE_BASE_URL` / `FILEDROP_BASE_URL` / `HERMES_BASE_URL` | Base URLs for the integration health adapters. Unset → `not_configured`. |
| `*_TOKEN` (per adapter) | Optional bearer token sent on that adapter's health check. |

### Catalog data

The repo ships a small **example seed** at
`aims_warehouse/picker_ang/data/foai-tool-inventory-log.jsonl` so the service
runs out of the box. The catalog is data, not code: point `TOOL_WAREHOUSE_DATA`
at your own inventory file (one JSON object per line) to serve the full warehouse.

Each line is a tool record, e.g.:

```json
{"name": "OpenRouter", "category": "model gateway", "status": "certified",
 "origin": "openrouter.ai", "note": "Unified API over many model providers."}
```

---

## Layout

```
aims_warehouse/
  warehouse_service.py        # the FastAPI app (entrypoint)
  integrations/               # live-health adapters (Coder, Autobase, File Drop, Hermes) + registry + catalog logic
  picker_ang/
    tool_warehouse.py         # the curation model (statuses, certified-only selection)
    data/                     # the inventory seed
Dockerfile · docker-compose.yml · requirements.txt · .env.example
```

---

## Roadmap

- **MCP servers** and **component libraries** (21st.dev / shadcn-style) as
  first-class shelves — the two resource classes most requested next.
- Broader catalog population toward full builder coverage.
- Write-side curation flow (promote tools through the status pipeline).
