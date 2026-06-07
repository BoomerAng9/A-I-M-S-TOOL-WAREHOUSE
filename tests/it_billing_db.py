"""Integration test against REAL Postgres — exercises the SQL the unit tests can't:
migration, exactly-once grant, monotonic subscription guard, claim CAS, resolve_key
suspension. Run inside the warehouse image with WAREHOUSE_DATABASE_URL pointed at a
throwaway Postgres. Exits non-zero on any failure.
"""
from __future__ import annotations

import asyncio
import sys

from aims_warehouse.billing import pricing, provision
from aims_warehouse.tenancy import db, store

FAILS: list[str] = []


def check(name: str, cond: bool) -> None:
    print(f"  {'ok ' if cond else 'FAIL'} {name}")
    if not cond:
        FAILS.append(name)


def coffee_session(sid: str, cust: str, paid: bool = True, amount: int = 654, cur: str = "usd") -> dict:
    return {
        "id": sid, "mode": "payment", "payment_status": "paid" if paid else "unpaid",
        "currency": cur, "amount_total": amount, "customer": cust,
        "customer_details": {"email": "buyer@example.com"},
        "metadata": {"plan": "coffee", "commitment": "1"},
    }


def sub_obj(sub_id: str, cust: str, status: str) -> dict:
    return {"id": sub_id, "customer": cust, "status": status, "metadata": {"plan": "starter"},
            "current_period_end": 2000000000, "items": {"data": []}}


async def insert_session(sid: str, token: str, plan: str, mode: str, commitment: int = 1) -> None:
    async with db.pool().connection() as conn:
        await conn.execute(
            "INSERT INTO billing_sessions (session_id, plan, commitment, mode, claim_token_hash) "
            "VALUES (%s,%s,%s,%s,%s)",
            (sid, plan, commitment, mode, provision.token_hash(token)),
        )


async def credits_of(cust: str) -> int:
    async with db.pool().connection() as conn:
        cur = await conn.execute(
            "SELECT t.usage_credits FROM tenants t JOIN billing_customers b ON b.tenant_id=t.id "
            "WHERE b.stripe_customer_id=%s", (cust,))
        r = await cur.fetchone()
        return int(r[0]) if r else -1


async def status_of(cust: str) -> str:
    async with db.pool().connection() as conn:
        cur = await conn.execute(
            "SELECT t.status FROM tenants t JOIN billing_customers b ON b.tenant_id=t.id "
            "WHERE b.stripe_customer_id=%s", (cust,))
        r = await cur.fetchone()
        return r[0] if r else "<none>"


async def main() -> int:
    await db.open_pool()
    print(f"migration: tenancy_enabled={db.tenancy_enabled()} degraded={db.degraded()}")
    check("migration applied (tenancy enabled)", db.tenancy_enabled())

    # --- 1) coffee grant is exactly-once across a DUPLICATE provision (provisioned_at guard) ---
    await insert_session("cs_coffee", "tok_coffee", "coffee", "payment")
    s = coffee_session("cs_coffee", "cus_coffee")
    async with db.pool().connection() as conn:
        async with conn.transaction():
            await provision.grant_for_session(conn, s)
    async with db.pool().connection() as conn:
        async with conn.transaction():
            await provision.grant_for_session(conn, s)  # duplicate delivery
    check("coffee credited exactly once (== coffee_credits)", await credits_of("cus_coffee") == pricing.coffee_credits())

    # --- 2) record_event dedupes redelivery of the same event id ---
    async with db.pool().connection() as conn:
        async with conn.transaction():
            first = await provision.record_event(conn, "evt_1", "checkout.session.completed", True)
        async with conn.transaction():
            second = await provision.record_event(conn, "evt_1", "checkout.session.completed", True)
    check("record_event: first=True second=False (idempotent)", first is True and second is False)

    # --- 3) amount mismatch does NOT credit and does NOT mark provisioned (claim refuses to mint) ---
    await insert_session("cs_bad", "tok_bad", "coffee", "payment")
    bad = coffee_session("cs_bad", "cus_bad", amount=100)  # wrong amount
    async with db.pool().connection() as conn:
        async with conn.transaction():
            await provision.grant_for_session(conn, bad)
    check("amount-mismatch: not credited", await credits_of("cus_bad") == 0)
    try:
        async with db.pool().connection() as conn:
            async with conn.transaction():
                await provision.claim_finalize(conn, "tok_bad", coffee_session("cs_bad", "cus_bad", amount=100))
        minted_bad = True
    except provision.ClaimError:
        minted_bad = False
    check("amount-mismatch: claim refuses to mint", minted_bad is False)

    # --- 4) out-of-order subscription events: stale event must NOT flip tenant status ---
    async with db.pool().connection() as conn:
        async with conn.transaction():
            await provision.apply_subscription(conn, sub_obj("sub_1", "cus_sub", "active"), event_created=200)
    check("subscription active -> tenant active", await status_of("cus_sub") == "active")
    async with db.pool().connection() as conn:
        async with conn.transaction():
            await provision.apply_subscription(conn, sub_obj("sub_1", "cus_sub", "canceled"), event_created=100)  # stale
    check("stale cancel (older) did NOT suspend", await status_of("cus_sub") == "active")
    async with db.pool().connection() as conn:
        async with conn.transaction():
            await provision.apply_subscription(conn, sub_obj("sub_1", "cus_sub", "canceled"), event_created=300)  # fresh
    check("fresh cancel (newer) DID suspend", await status_of("cus_sub") == "suspended")

    # --- 5) two concurrent claims mint exactly ONE key (CAS) ---
    await insert_session("cs_claim", "tok_claim", "coffee", "payment")
    async with db.pool().connection() as conn:
        async with conn.transaction():
            await provision.grant_for_session(conn, coffee_session("cs_claim", "cus_claim"))

    async def do_claim():
        try:
            async with db.pool().connection() as conn:
                async with conn.transaction():
                    return await provision.claim_finalize(conn, "tok_claim", coffee_session("cs_claim", "cus_claim"))
        except provision.ClaimError as e:
            return e

    r1, r2 = await asyncio.gather(do_claim(), do_claim())
    minted = [r for r in (r1, r2) if isinstance(r, dict)]
    refused = [r for r in (r1, r2) if isinstance(r, provision.ClaimError)]
    check("concurrent claims: exactly one minted", len(minted) == 1)
    check("concurrent claims: exactly one refused (409)", len(refused) == 1)

    # --- 6) resolve_key honors tenant status (the live-path enforcement) ---
    t = await store.create_tenant("Enf Co", "enf-co-test", "coffee")
    k = await store.mint_key(t["id"], "k", ["catalog:read"], None)
    check("active tenant key resolves", (await store.resolve_key(k["api_key"])) is not None)
    async with db.pool().connection() as conn:
        await conn.execute("UPDATE tenants SET status='suspended' WHERE id=%s", (t["id"],))
    check("suspended tenant key REJECTED", (await store.resolve_key(k["api_key"])) is None)

    await db.close_pool()
    print(f"\n{'FAILED: ' + ', '.join(FAILS) if FAILS else 'ALL PASSED'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
