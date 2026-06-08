"""Idempotent Stripe Product/Price provisioning from pricing.py.

Run AFTER STRIPE_SECRET_KEY is set (test mode first, then live):
    python -m aims_warehouse.billing.bootstrap

Re-runnable. Stripe Prices are IMMUTABLE — if a lookup_key already points at a Price
whose amount/currency/interval differs from pricing.py, we create a NEW Price and
TRANSFER the lookup_key to it (the old Price keeps charging existing subscribers
until they migrate; the lookup_key — what checkout resolves — points at the new one).
Products are deduped per plan by a metadata tag so re-runs don't pile up duplicates.
"""
from __future__ import annotations

import sys
from typing import Any, Optional

from . import pricing
from . import stripe_gateway as gw

_PLAN_TAG = "aimswh_plan"


def _find_product(stripe, slug: str, display_name: str) -> str:
    """Find-or-create the Product for a plan (deduped by metadata[aimswh_plan])."""
    # List is bounded (we have a handful of products); avoids requiring Search API.
    for p in stripe.Product.list(active=True, limit=100).auto_paging_iter():
        if (p.get("metadata") or {}).get(_PLAN_TAG) == slug:
            return p["id"]
    created = stripe.Product.create(name=f"A.I.M.S. {display_name}", metadata={_PLAN_TAG: slug})
    print(f"  + product {created['id']} for {slug}")
    return created["id"]


def _price_matches(price: dict[str, Any], spec: dict[str, Any]) -> bool:
    if price.get("unit_amount") != spec["amount_cents"]:
        return False
    if price.get("currency") != pricing.CURRENCY:
        return False
    rec = price.get("recurring")
    if spec["recurring"]:
        return bool(rec) and rec.get("interval") == spec["interval"]
    return rec is None


def _create_price(stripe, product_id: str, spec: dict[str, Any], transfer: bool) -> str:
    params: dict[str, Any] = {
        "product": product_id,
        "unit_amount": spec["amount_cents"],
        "currency": pricing.CURRENCY,
        "lookup_key": spec["lookup_key"],
        "transfer_lookup_key": transfer,
    }
    if spec["recurring"]:
        params["recurring"] = {"interval": spec["interval"]}
    price = stripe.Price.create(**params)
    print(f"  + price {price['id']} {spec['lookup_key']} = {pricing.dollars(spec['amount_cents'])}"
          f"{'/' + spec['interval'] if spec['recurring'] else ' one-time'}")
    return price["id"]


def ensure() -> dict[str, Any]:
    """Ensure all Products/Prices exist + match. Returns a summary dict."""
    stripe = gw._stripe()  # raises StripeUnavailable if no key / SDK
    print(f"Stripe mode: {gw.mode()}")
    summary = {"mode": gw.mode(), "ok": [], "created": [], "transferred": []}
    product_cache: dict[str, str] = {}
    for spec in pricing.all_lookup_keys():
        slug = spec["plan"]
        lk = spec["lookup_key"]
        existing = stripe.Price.list(lookup_keys=[lk], limit=1)
        data = getattr(existing, "data", []) or []
        if data:
            price = data[0]
            if _price_matches(price, spec):
                print(f"  = {lk} ok ({price['id']})")
                summary["ok"].append(lk)
                continue
            # Immutable mismatch -> new price + transfer the lookup_key.
            pid = price.get("product")
            new_id = _create_price(stripe, pid, spec, transfer=True)
            summary["transferred"].append({"lookup_key": lk, "new_price": new_id, "old_price": price["id"]})
            continue
        # No price yet -> ensure product, then create.
        if slug not in product_cache:
            product_cache[slug] = _find_product(stripe, slug, spec["display_name"])
        new_id = _create_price(stripe, product_cache[slug], spec, transfer=False)
        summary["created"].append({"lookup_key": lk, "price": new_id})
    return summary


def main(argv: Optional[list[str]] = None) -> int:
    if not gw.secret_configured():
        print("STRIPE_SECRET_KEY not set — nothing to do.", file=sys.stderr)
        return 2
    try:
        s = ensure()
    except gw.StripeUnavailable as e:
        print(f"Stripe unavailable: {e}", file=sys.stderr)
        return 2
    print(f"\nDone. ok={len(s['ok'])} created={len(s['created'])} transferred={len(s['transferred'])} (mode={s['mode']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
