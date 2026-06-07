"""Thin wrapper around the Stripe SDK — the only module that talks to Stripe.

Design rules enforced here:
  - GRACEFUL DEGRADE: ``secret_configured()`` / ``webhook_configured()`` are env
    checks that need no import; the SDK is LAZY-imported inside calls so a missing
    or broken ``stripe`` lib turns the paywall OFF (caller -> 503) instead of
    crashing the whole service (matches the catalog/tenancy degrade posture).
  - The webhook secret is a DIFFERENT secret from the API key; each is checked
    independently so a half-configured deploy fails closed, never open.
  - Checkout ``mode`` is branched on the plan kind (one-time vs subscription).
  - Currency is pinned to USD via the Price; success/cancel URLs are derived
    strictly from ``WAREHOUSE_PUBLIC_URL`` (never from the request body), and the
    one-time claim token rides the URL FRAGMENT (never the query string -> never
    sent to servers or in a Referer header).
"""
from __future__ import annotations

import os
from typing import Any, Optional

from . import pricing

DEFAULT_PUBLIC_URL = "https://warehouse.aimanagedsolutions.cloud"

# Resolved Stripe Price IDs cached per lookup_key (Prices are immutable; bootstrap
# transfers a lookup_key to a new Price when an amount changes, so a cached id can
# go stale only after an intentional re-bootstrap + restart — acceptable).
_price_id_cache: dict[str, str] = {}


class StripeUnavailable(RuntimeError):
    """Raised when Stripe is not configured or the SDK can't be loaded -> caller returns 503."""


def secret_configured() -> bool:
    return bool(os.environ.get("STRIPE_SECRET_KEY"))


def webhook_configured() -> bool:
    return bool(os.environ.get("STRIPE_WEBHOOK_SECRET"))


def public_url() -> str:
    return (os.environ.get("WAREHOUSE_PUBLIC_URL") or DEFAULT_PUBLIC_URL).rstrip("/")


def mode() -> str:
    """'live' | 'test' | 'unknown' — derived from the secret key prefix."""
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if key.startswith("sk_live_") or key.startswith("rk_live_"):
        return "live"
    if key.startswith("sk_test_") or key.startswith("rk_test_"):
        return "test"
    return "unknown"


def _stripe():
    """Lazy import + api_key set. Raises StripeUnavailable (caller -> 503) on any problem."""
    if not secret_configured():
        raise StripeUnavailable("STRIPE_SECRET_KEY not set")
    try:
        import stripe  # noqa: PLC0415  (lazy on purpose — see module docstring)
    except Exception as e:  # pragma: no cover - import guard
        raise StripeUnavailable(f"stripe SDK unavailable: {e}") from e
    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
    return stripe


def resolve_price_id(lookup_key: str) -> str:
    """Map a stable lookup_key -> the live Stripe Price id. Fails LOUDLY if bootstrap
    has not created the Price in THIS Stripe mode (a misconfig we want visible)."""
    if lookup_key in _price_id_cache:
        return _price_id_cache[lookup_key]
    stripe = _stripe()
    prices = stripe.Price.list(lookup_keys=[lookup_key], active=True, limit=1)
    data = getattr(prices, "data", []) or []
    if not data:
        raise StripeUnavailable(
            f"no active Stripe Price for lookup_key '{lookup_key}' in {mode()} mode "
            f"(run `python -m aims_warehouse.billing.bootstrap`)"
        )
    pid = data[0]["id"]
    _price_id_cache[lookup_key] = pid
    return pid


def create_checkout_session(
    plan_slug: str,
    commitment: int,
    claim_token: str,
    idempotency_key: str,
    email: Optional[str] = None,
    customer_id: Optional[str] = None,
) -> dict[str, Any]:
    """Create a Checkout Session for (plan, commitment). Mode is branched on plan kind.

    The caller has ALREADY validated the plan/commitment against pricing.py. Returns
    the raw Stripe session (with .id and .url). Raises StripeUnavailable on any
    Stripe problem so the route can 503.
    """
    price = pricing.find_price(plan_slug, commitment)
    ck_mode = pricing.checkout_mode(plan_slug)
    if price is None or ck_mode is None:
        # Defensive: should never reach here (route validates first).
        raise StripeUnavailable(f"plan/commitment not transactable: {plan_slug}/{commitment}")

    stripe = _stripe()
    price_id = resolve_price_id(price["lookup_key"])
    base = public_url()
    # Claim token in the FRAGMENT — never the query string (no server-log / Referer leak).
    success_url = f"{base}/billing/success#token={claim_token}"
    cancel_url = f"{base}/pricing"

    params: dict[str, Any] = {
        "mode": ck_mode,
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": success_url,
        "cancel_url": cancel_url,
        # Only server-trusted facts; never trust these back without re-verifying payment.
        # NB: the claim token is intentionally NOT placed in metadata/client_reference_id
        # — it is a secret that lives only in the success_url fragment + our hashed row.
        "metadata": {"plan": plan_slug, "commitment": str(commitment)},
    }
    # Reuse an existing Stripe Customer for a returning buyer; else let Stripe make one.
    if customer_id:
        params["customer"] = customer_id
    elif email:
        params["customer_email"] = email
    if ck_mode == "payment":
        # Guarantee a Customer object exists for one-time buys so we can dedup later.
        params["customer_creation"] = "always"
        params["payment_intent_data"] = {"metadata": {"plan": plan_slug}}
    else:
        params["subscription_data"] = {"metadata": {"plan": plan_slug, "commitment": str(commitment)}}

    session = stripe.checkout.Session.create(idempotency_key=idempotency_key, **params)
    return session


def retrieve_session(session_id: str) -> dict[str, Any]:
    """Authoritative server-side read of a Checkout Session (payment_status, customer, etc.)."""
    stripe = _stripe()
    return stripe.checkout.Session.retrieve(
        session_id, expand=["customer", "subscription", "line_items"]
    )


def retrieve_subscription(subscription_id: str) -> dict[str, Any]:
    stripe = _stripe()
    return stripe.Subscription.retrieve(subscription_id)


def retrieve_charge(charge_id: str) -> dict[str, Any]:
    """Authoritative read of a Charge — used to resolve the customer on a dispute
    (Dispute objects carry only the charge id, not a customer)."""
    stripe = _stripe()
    return stripe.Charge.retrieve(charge_id)


def construct_event(payload: bytes, sig_header: str) -> dict[str, Any]:
    """Verify the webhook signature on the RAW bytes and return the trusted event.

    Raises ValueError / stripe.error.SignatureVerificationError on a bad signature
    (caller returns 400). Requires STRIPE_WEBHOOK_SECRET — caller must have checked
    webhook_configured() first and 503'd otherwise (fail CLOSED, never open).
    """
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not secret:
        raise StripeUnavailable("STRIPE_WEBHOOK_SECRET not set")
    try:
        import stripe  # noqa: PLC0415
    except Exception as e:  # pragma: no cover
        raise StripeUnavailable(f"stripe SDK unavailable: {e}") from e
    # construct_event also enforces Stripe's timestamp tolerance (replay protection).
    return stripe.Webhook.construct_event(payload, sig_header, secret)


def livemode_matches(event: dict[str, Any]) -> bool:
    """True iff the event's livemode matches our configured key mode (drop strays)."""
    m = mode()
    if m == "unknown":
        return False
    return bool(event.get("livemode")) == (m == "live")
