"""Provisioning — the idempotent state machine behind the paywall.

Every function takes a CALLER-SUPPLIED connection and runs inside the caller's
single transaction (the webhook and the claim route both open one explicit
``async with conn.transaction():`` — REQUIRED because the pool is autocommit, so
a bare execute would self-commit and defeat the all-or-nothing guarantee).

Invariants baked in here:
  - Identity is the Stripe-verified ``stripe_customer_id`` — NEVER the client email
    (which is unverified and would enable account takeover). Find-or-create is
    serialised per customer with a txn advisory lock, so no duplicate/stray tenants.
  - The grant is exactly-once, guarded by ``billing_sessions.provisioned_at`` —
    shared by the webhook AND the claim path, so the credit can't be granted twice
    even though a single purchase emits several distinct Stripe events.
  - The API key is minted ONLY at claim, via a single-use CAS on ``claimed_at`` —
    so two concurrent claims can never mint two keys, and the full key is shown once.
  - Suspension (cancel / refund / dispute / non-payment) sets ``tenant.status`` and
    revokes keys; ``store.resolve_key`` requires status='active', so access is cut.
"""
from __future__ import annotations

import logging
import secrets
from typing import Any, Optional

from ..tenancy import keys, store
from . import pricing
from . import stripe_gateway as gw

_log = logging.getLogger("aims_warehouse.billing.provision")


class ClaimError(Exception):
    """Base for claim failures mapped to HTTP codes by the route."""
    status = 400


class NotFound(ClaimError):
    status = 404


class NotPaid(ClaimError):
    status = 402


class AlreadyClaimed(ClaimError):
    status = 409


def token_hash(token: str) -> str:
    """Deterministic, pepper-aware hash for the claim token (same discipline as API keys)."""
    return keys.hash_secret(token)


def _obj_id(v: Any) -> Optional[str]:
    """Stripe fields are an id string on raw events, an expanded object on retrieve()."""
    if v is None:
        return None
    if isinstance(v, str):
        return v
    return v.get("id")


def _email_from_session(session: dict[str, Any]) -> Optional[str]:
    details = session.get("customer_details") or {}
    return details.get("email") or session.get("customer_email")


# --------------------------------------------------------------------------- events ledger
async def record_event(conn, event_id: str, etype: str, livemode: Optional[bool]) -> bool:
    """Claim this event id. Returns True if NEW (proceed), False if already processed
    (redelivery -> caller no-ops and returns 200). The UNIQUE(stripe_event_id) is the
    serialisation lock; being in the caller's txn means a later failure rolls this back."""
    cur = await conn.execute(
        "INSERT INTO billing_events (stripe_event_id, type, livemode) VALUES (%s, %s, %s) "
        "ON CONFLICT (stripe_event_id) DO NOTHING",
        (event_id, etype, livemode),
    )
    return cur.rowcount > 0


# --------------------------------------------------------------------------- tenant identity
async def find_or_create_tenant(conn, customer_id: str, email: Optional[str]) -> str:
    """Resolve the tenant for a Stripe customer, creating it once. Serialised per
    customer by a txn advisory lock so concurrent events can't make duplicate tenants."""
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"aimswh_cust:{customer_id}",))
    cur = await conn.execute(
        "SELECT tenant_id FROM billing_customers WHERE stripe_customer_id = %s", (customer_id,)
    )
    row = await cur.fetchone()
    if row:
        if email:
            await conn.execute(
                "UPDATE billing_customers SET billing_email = COALESCE(%s, billing_email) "
                "WHERE stripe_customer_id = %s", (email, customer_id),
            )
        return str(row[0])
    # New customer -> new tenant. Slug is random (never derived from email).
    slug = "t-" + secrets.token_hex(6)
    name = email or f"customer {customer_id[-8:]}"
    cur = await conn.execute(
        "INSERT INTO tenants (name, slug, plan, billing_email) VALUES (%s, %s, 'free', %s) RETURNING id",
        (name, slug, email),
    )
    tenant_id = str((await cur.fetchone())[0])
    await conn.execute(
        "INSERT INTO billing_customers (stripe_customer_id, tenant_id, billing_email) VALUES (%s, %s, %s)",
        (customer_id, tenant_id, email),
    )
    return tenant_id


async def _set_plan(conn, tenant_id: str, plan: str) -> None:
    await conn.execute("UPDATE tenants SET plan = %s, status = 'active' WHERE id = %s", (plan, tenant_id))


async def _suspend_tenant(conn, tenant_id: str, reason: str) -> None:
    await conn.execute("UPDATE tenants SET status = 'suspended' WHERE id = %s", (tenant_id,))
    await conn.execute(
        "UPDATE api_keys SET revoked_at = now() WHERE tenant_id = %s AND revoked_at IS NULL", (tenant_id,)
    )
    _log.info("suspended tenant %s (%s) + revoked keys", tenant_id, reason)


async def _tenant_for_customer(conn, customer_id: str) -> Optional[str]:
    cur = await conn.execute(
        "SELECT tenant_id FROM billing_customers WHERE stripe_customer_id = %s", (customer_id,)
    )
    row = await cur.fetchone()
    return str(row[0]) if row else None


# --------------------------------------------------------------------------- the grant (exactly once)
async def grant_for_session(conn, session: dict[str, Any]) -> Optional[str]:
    """Provision a PAID checkout session exactly once. Returns the tenant_id (or None
    if the session is not ours / not grantable). Guarded by billing_sessions.provisioned_at."""
    session_id = session.get("id")
    if not session_id:
        return None
    # Lock our row for this session; the grant is gated on provisioned_at IS NULL.
    cur = await conn.execute(
        "SELECT id, plan, commitment, mode, tenant_id, provisioned_at "
        "FROM billing_sessions WHERE session_id = %s FOR UPDATE", (session_id,)
    )
    row = await cur.fetchone()
    if row is None:
        # We have no record of creating this session (e.g. DB reset). Don't fabricate.
        _log.warning("checkout session %s has no billing_sessions row; skipping grant", session_id)
        return None
    bs_id, plan, commitment, bs_mode, existing_tenant, provisioned_at = row
    customer_id = _obj_id(session.get("customer"))
    if not customer_id:
        _log.warning("session %s has no customer id; cannot provision", session_id)
        return None
    email = _email_from_session(session)
    tenant_id = existing_tenant and str(existing_tenant) or await find_or_create_tenant(conn, customer_id, email)

    if provisioned_at is not None:
        # Already granted — just make sure the row is linked, then return (idempotent).
        await conn.execute("UPDATE billing_sessions SET tenant_id = %s WHERE id = %s", (tenant_id, bs_id))
        return tenant_id

    # Verify what was actually paid before granting anything (currency + amount).
    currency = (session.get("currency") or "").lower()
    amount_total = session.get("amount_total")
    price = pricing.find_price(plan, int(commitment))
    granted = False
    if currency and currency != pricing.CURRENCY:
        _log.warning("session %s currency %s != usd; linking but NOT granting", session_id, currency)
    elif bs_mode == "payment":
        # One-time coffee: amount must equal the locked price exactly before crediting.
        if price and amount_total == price["amount_cents"]:
            await conn.execute(
                "UPDATE tenants SET plan = 'coffee', status = 'active', "
                "usage_credits = usage_credits + %s WHERE id = %s",
                (pricing.coffee_credits(), tenant_id),
            )
            granted = True
        else:
            _log.warning(
                "session %s amount_total %s != expected %s; linking but NOT crediting",
                session_id, amount_total, price and price["amount_cents"],
            )
    else:
        # Subscription: the Price came from our lookup_key, so the plan is trusted here;
        # subscription.* events keep status/plan in sync going forward.
        await _set_plan(conn, tenant_id, plan)
        granted = True

    if granted:
        # provisioned_at is the exactly-once guard AND (via claim_finalize) the
        # precondition for minting a key — set it ONLY on a real grant, so a payment
        # anomaly can't both lock out retries and mint a key for nothing.
        await conn.execute(
            "UPDATE billing_sessions SET tenant_id = %s, payment_status = %s, provisioned_at = now() "
            "WHERE id = %s",
            (tenant_id, session.get("payment_status"), bs_id),
        )
    else:
        await conn.execute(
            "UPDATE billing_sessions SET tenant_id = %s, payment_status = %s WHERE id = %s",
            (tenant_id, session.get("payment_status"), bs_id),
        )
    return tenant_id


# --------------------------------------------------------------------------- subscriptions
async def apply_subscription(conn, sub: dict[str, Any], event_created: int) -> None:
    """Upsert subscription state with a monotonic guard (events can arrive out of order)."""
    sub_id = sub.get("id")
    customer_id = _obj_id(sub.get("customer"))
    if not sub_id or not customer_id:
        return
    tenant_id = await _tenant_for_customer(conn, customer_id)
    if tenant_id is None:
        # Subscription event landed before we mapped the customer (rare ordering) — map now.
        tenant_id = await find_or_create_tenant(conn, customer_id, None)
    meta = sub.get("metadata") or {}
    plan = meta.get("plan") or _plan_from_subscription(sub) or "subscription"
    status = sub.get("status") or "active"
    cpe = sub.get("current_period_end")
    cpe_sql = "to_timestamp(%s)" if cpe else "NULL"
    params: list[Any] = [tenant_id, sub_id, plan, status]
    if cpe:
        params.append(cpe)
    params.append(int(event_created))
    cur = await conn.execute(
        f"INSERT INTO subscriptions (tenant_id, stripe_subscription_id, plan, status, current_period_end, event_created) "
        f"VALUES (%s, %s, %s, %s, {cpe_sql}, %s) "
        f"ON CONFLICT (stripe_subscription_id) DO UPDATE SET "
        f"  plan = EXCLUDED.plan, status = EXCLUDED.status, "
        f"  current_period_end = EXCLUDED.current_period_end, "
        f"  event_created = EXCLUDED.event_created, updated_at = now() "
        f"WHERE EXCLUDED.event_created >= subscriptions.event_created "
        f"RETURNING id",
        tuple(params),
    )
    # RETURNING is empty exactly when the monotonic guard rejected a stale event
    # (out-of-order redelivery). Only reflect onto the tenant when THIS event won,
    # else a late canceled/active event could wrongly flip a tenant's access.
    if (await cur.fetchone()) is None:
        return
    # Reflect status onto the tenant: active/trialing -> active; terminal -> suspended.
    if status in ("active", "trialing"):
        await _set_plan(conn, tenant_id, plan)
    elif status in ("canceled", "unpaid", "incomplete_expired"):
        await _suspend_tenant(conn, tenant_id, f"subscription {status}")
    # past_due/incomplete -> grace: leave as-is (dunning may recover). Documented v1 choice.


def _plan_from_subscription(sub: dict[str, Any]) -> Optional[str]:
    """Best-effort plan slug from the subscription's price lookup_key (fallback only)."""
    try:
        items = ((sub.get("items") or {}).get("data")) or []
        lk = items[0]["price"].get("lookup_key")
        for slug, plan in pricing.PLANS.items():
            if any(p["lookup_key"] == lk for p in plan["prices"]):
                return slug
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------- refunds / disputes
async def suspend_for_charge(conn, charge: dict[str, Any], reason: str) -> None:
    customer_id = _obj_id(charge.get("customer"))
    if not customer_id:
        return
    tenant_id = await _tenant_for_customer(conn, customer_id)
    if tenant_id:
        await conn.execute("UPDATE tenants SET usage_credits = 0 WHERE id = %s", (tenant_id,))
        await _suspend_tenant(conn, tenant_id, reason)


# --------------------------------------------------------------------------- webhook dispatch
async def handle_event(conn, event: dict[str, Any]) -> None:
    """Dispatch a VERIFIED Stripe event. Raises on failure so the route returns 5xx
    (Stripe will retry) — and because everything is in the caller's transaction, the
    event-ledger row rolls back too, so the retry re-processes cleanly."""
    etype = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}
    created = int(event.get("created") or 0)

    if etype in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        if obj.get("payment_status") == "paid":
            await grant_for_session(conn, obj)
        else:
            # async pending — wait for async_payment_succeeded; do not grant.
            await conn.execute(
                "UPDATE billing_sessions SET payment_status = %s WHERE session_id = %s",
                (obj.get("payment_status"), obj.get("id")),
            )
    elif etype == "checkout.session.async_payment_failed":
        await conn.execute(
            "UPDATE billing_sessions SET payment_status = 'failed' WHERE session_id = %s", (obj.get("id"),)
        )
    elif etype in ("customer.subscription.created", "customer.subscription.updated"):
        await apply_subscription(conn, obj, created)
    elif etype == "customer.subscription.deleted":
        await apply_subscription(conn, {**obj, "status": "canceled"}, created)
    elif etype == "charge.refunded":
        await suspend_for_charge(conn, obj, "charge.refunded")
    elif etype == "charge.dispute.created":
        # A Dispute object carries the charge id but NO customer — resolve the customer
        # by retrieving the charge. Best-effort: a retrieve failure must not retry-storm
        # the webhook (the dispute is also visible in the Stripe dashboard).
        charge_id = _obj_id(obj.get("charge"))
        if charge_id:
            try:
                charge = gw.retrieve_charge(charge_id)
                await suspend_for_charge(conn, charge, "dispute")
            except Exception:
                _log.exception("charge.dispute.created: could not resolve charge %s for suspension", charge_id)
    else:
        _log.debug("ignoring event type %s", etype)


# --------------------------------------------------------------------------- claim (mint once)
async def claim_finalize(conn, token: str, session: dict[str, Any]) -> dict[str, Any]:
    """Inside the caller's txn: verify the row, provision-on-demand if the webhook
    hasn't landed, atomically flip claimed_at, and mint the key exactly once."""
    th = token_hash(token)
    cur = await conn.execute(
        "SELECT id, session_id, claimed_at, provisioned_at, tenant_id FROM billing_sessions "
        "WHERE claim_token_hash = %s FOR UPDATE", (th,)
    )
    row = await cur.fetchone()
    if row is None:
        raise NotFound("unknown or expired claim token")
    bs_id, session_id, claimed_at, provisioned_at, tenant_id = row
    if claimed_at is not None:
        raise AlreadyClaimed("this purchase has already been claimed")
    if session.get("payment_status") != "paid":
        raise NotPaid("payment not completed yet")

    if provisioned_at is None:
        # Webhook hasn't landed yet (or this is the first touch) — provision on demand.
        await grant_for_session(conn, session)
        cur = await conn.execute(
            "SELECT tenant_id, provisioned_at FROM billing_sessions WHERE id = %s", (bs_id,)
        )
        tenant_id, provisioned_at = await cur.fetchone()
    if provisioned_at is None:
        # Grant did not happen (e.g. amount/currency anomaly) — do NOT mint a key.
        raise ClaimError("payment could not be verified — please contact support")
    if tenant_id is None:
        raise NotFound("could not resolve tenant for this purchase")

    # Single-use CAS: only the row that flips claimed_at proceeds to mint.
    cur = await conn.execute(
        "UPDATE billing_sessions SET claimed_at = now() WHERE id = %s AND claimed_at IS NULL RETURNING id",
        (bs_id,),
    )
    if (await cur.fetchone()) is None:
        raise AlreadyClaimed("this purchase has already been claimed")

    minted = await store.mint_key_on(
        conn, str(tenant_id), name="paywall", scopes=["catalog:read"], expires_at=None
    )
    await conn.execute("UPDATE billing_sessions SET key_id = %s WHERE id = %s", (minted["id"], bs_id))
    return minted
