"""Pricing catalog — the SINGLE declarative source of truth for the paywall.

Drives BOTH ``bootstrap.py`` (which creates the matching Stripe Products/Prices)
and ``GET /pricing`` (the buyer-facing page) so the displayed price can never
diverge from the charged price.

OWNER-LOCKED prices (do NOT invent or alter these without the owner):
  - Buy Me a Coffee  = ONE-TIME  $6.54  (the cost of a coconut latte)  -> 65400? no: 654 cents.
  - Starter          = $19/mo   (1900 cents)
  - Growth           = $79/mo   (7900 cents)
  - Scale            = $229/mo  (22900 cents)
  - Enterprise       = custom -> "contact" (no Stripe price)

"3-6-9" is a COMMITMENT-DURATION axis (months), not a discount %. Only TWO
commitments are concrete today and therefore the only ones we will transact:
  - 1  month  : the month-to-month base price (no commitment).
  - 9  months : "pay 9, get 12" -> an ANNUAL up-front price billed once/year at
                exactly 9 x the monthly base (Starter 17100 / Growth 71100 /
                Scale 206100 cents). This is the literal owner statement encoded
                as integer cents (NOT a fragile runtime percentage).
  - 3 and 6 month commitments are owner-TBD: we DELIBERATELY do not synthesize a
    number — they surface as "contact" and the checkout route rejects them.

All amounts are integer cents, currency USD (pinned — 654 only means $6.54 in USD).
No tier is marketed as "unlimited"; per-call metered caps are the later LUC phase.
"""
from __future__ import annotations

import os
from typing import Any, Optional

CURRENCY = "usd"

# How many catalog requests the one-time coffee entry grants. OWNER-TUNABLE via
# env (not a customer-facing PRICE, so not a fabrication risk); recorded now,
# enforced in the LUC metering phase. Defaults to a placeholder the owner sets.
def coffee_credits() -> int:
    try:
        return max(0, int(os.environ.get("WAREHOUSE_COFFEE_CREDITS", "100")))
    except ValueError:
        return 100


# Each plan: kind in {one_time, subscription, contact}. `prices` lists the
# concrete Stripe Prices we create/transact, keyed by a stable lookup_key and a
# `commitment` (months). A subscription plan has the monthly base (commitment 1)
# and the annual pay-9-get-12 (commitment 9). Nothing here is computed at runtime.
PLANS: dict[str, dict[str, Any]] = {
    "coffee": {
        "display_name": "Buy Me a Coffee",
        "tagline": "One-time entry",
        "kind": "one_time",
        "blurb": "A one-time coconut-latte entry. Get an API key and pay-per-use credits to browse and select certified tools.",
        "highlight": False,
        "prices": [
            {"lookup_key": "aimswh_coffee_onetime", "amount_cents": 654, "interval": None, "commitment": 1, "recurring": False},
        ],
    },
    "starter": {
        "display_name": "Starter",
        "tagline": "For a single builder",
        "kind": "subscription",
        "blurb": "Programmatic catalog access for one team getting started. Metered fair-use caps apply.",
        "highlight": False,
        "prices": [
            {"lookup_key": "aimswh_starter_monthly", "amount_cents": 1900, "interval": "month", "commitment": 1, "recurring": True},
            {"lookup_key": "aimswh_starter_9mo", "amount_cents": 17100, "interval": "year", "commitment": 9, "recurring": True},
        ],
    },
    "growth": {
        "display_name": "Growth",
        "tagline": "For a growing product",
        "kind": "subscription",
        "blurb": "Higher allocations for teams shipping with the warehouse daily. Metered fair-use caps apply.",
        "highlight": True,
        "prices": [
            {"lookup_key": "aimswh_growth_monthly", "amount_cents": 7900, "interval": "month", "commitment": 1, "recurring": True},
            {"lookup_key": "aimswh_growth_9mo", "amount_cents": 71100, "interval": "year", "commitment": 9, "recurring": True},
        ],
    },
    "scale": {
        "display_name": "Scale",
        "tagline": "For heavy production use",
        "kind": "subscription",
        "blurb": "Top published allocations and priority. Metered fair-use caps apply.",
        "highlight": False,
        "prices": [
            {"lookup_key": "aimswh_scale_monthly", "amount_cents": 22900, "interval": "month", "commitment": 1, "recurring": True},
            {"lookup_key": "aimswh_scale_9mo", "amount_cents": 206100, "interval": "year", "commitment": 9, "recurring": True},
        ],
    },
    "enterprise": {
        "display_name": "Enterprise",
        "tagline": "SLA + dedicated support",
        "kind": "contact",
        "blurb": "Custom allocations, SLA, and a dedicated specialist. Talk to us.",
        "highlight": False,
        "prices": [],
    },
}

# The only commitments we will transact (months). 3 and 6 are owner-TBD -> absent
# here -> the checkout route rejects them and the UI shows them as "contact".
TRANSACTABLE_COMMITMENTS = (1, 9)


def get_plan(slug: str) -> Optional[dict[str, Any]]:
    return PLANS.get(slug)


def allowed_commitments(slug: str) -> list[int]:
    """Commitments this plan can actually be bought at (validated, concrete only)."""
    plan = PLANS.get(slug)
    if not plan or plan["kind"] == "contact":
        return []
    return [p["commitment"] for p in plan["prices"] if p["commitment"] in TRANSACTABLE_COMMITMENTS]


def find_price(slug: str, commitment: int) -> Optional[dict[str, Any]]:
    """Resolve the concrete Price for (plan, commitment), or None if not transactable.

    Returns None for unknown plans, the contact tier, or any commitment whose
    price the owner has not set (e.g. 3 or 6 months) — the caller MUST reject,
    never default to a fabricated amount.
    """
    plan = PLANS.get(slug)
    if not plan or plan["kind"] == "contact":
        return None
    if commitment not in TRANSACTABLE_COMMITMENTS:
        return None
    for p in plan["prices"]:
        if p["commitment"] == commitment:
            return p
    return None


def is_subscription(slug: str) -> bool:
    plan = PLANS.get(slug)
    return bool(plan and plan["kind"] == "subscription")


def checkout_mode(slug: str) -> Optional[str]:
    """Stripe Checkout mode for a plan: 'payment' (one-time) | 'subscription' | None (contact/unknown)."""
    plan = PLANS.get(slug)
    if not plan:
        return None
    if plan["kind"] == "one_time":
        return "payment"
    if plan["kind"] == "subscription":
        return "subscription"
    return None


def dollars(amount_cents: int) -> str:
    """Render integer cents as a USD string — the ONLY money formatter (no ad-hoc strings)."""
    return f"${amount_cents / 100:,.2f}"


def all_lookup_keys() -> list[dict[str, Any]]:
    """Every (plan, price) bootstrap must ensure exists in Stripe."""
    out: list[dict[str, Any]] = []
    for slug, plan in PLANS.items():
        for pr in plan["prices"]:
            out.append({"plan": slug, "display_name": plan["display_name"], **pr})
    return out


def public_view() -> list[dict[str, Any]]:
    """Buyer-facing plan cards for GET /pricing (honest labels; no fabricated commitment numbers)."""
    cards: list[dict[str, Any]] = []
    for slug, plan in PLANS.items():
        base = next((p for p in plan["prices"] if p["commitment"] == 1), None)
        nine = next((p for p in plan["prices"] if p["commitment"] == 9), None)
        if plan["kind"] == "one_time" and base:
            price_label = f"{dollars(base['amount_cents'])} one-time"
        elif plan["kind"] == "subscription" and base:
            price_label = f"{dollars(base['amount_cents'])}/mo"
        else:
            price_label = "Custom"
        cards.append({
            "slug": slug,
            "display_name": plan["display_name"],
            "tagline": plan["tagline"],
            "kind": plan["kind"],
            "blurb": plan["blurb"],
            "highlight": plan["highlight"],
            "price_label": price_label,
            # Concrete 9-month commitment only; 3/6 are intentionally "contact".
            "annual_label": (f"or {dollars(nine['amount_cents'])}/yr — pay 9, get 12" if nine else None),
            "commitment_note": "3 & 6-month commitments: contact us" if plan["kind"] == "subscription" else None,
        })
    return cards
