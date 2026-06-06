"""Tool Warehouse multi-tenancy: tenants, API keys, and usage metering.

This package owns the warehouse's OWN data and connects ONLY with
``WAREHOUSE_DATABASE_URL`` — a dedicated Neon project (or a role scoped to the
warehouse's own tables) that CANNOT read Charlotte's database. The public,
multi-tenant warehouse must never hold a credential with access to the core
platform DB; that isolation is the whole point of the standalone-product
reframe. When ``WAREHOUSE_DATABASE_URL`` is unset (or the DB is unreachable),
tenancy degrades off and the catalog is gated by the operator token alone.
"""
