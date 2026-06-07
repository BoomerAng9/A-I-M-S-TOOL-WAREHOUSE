"""Pure-logic tests for the paywall — no Stripe, no DB. Guards the money math and
the 'never fabricate a price' discipline. Run: `python -m pytest tests/test_billing.py`
or `python tests/test_billing.py`.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aims_warehouse.billing import pricing  # noqa: E402


def test_locked_base_amounts():
    assert pricing.find_price("coffee", 1)["amount_cents"] == 654
    assert pricing.find_price("starter", 1)["amount_cents"] == 1900
    assert pricing.find_price("growth", 1)["amount_cents"] == 7900
    assert pricing.find_price("scale", 1)["amount_cents"] == 22900


def test_nine_month_is_exactly_9x_monthly():
    for slug in ("starter", "growth", "scale"):
        monthly = pricing.find_price(slug, 1)["amount_cents"]
        annual = pricing.find_price(slug, 9)
        assert annual is not None, f"{slug} must have a 9-month price"
        assert annual["amount_cents"] == monthly * 9, f"{slug} 9mo must be exactly 9x monthly"
        assert annual["interval"] == "year"


def test_three_and_six_month_are_not_fabricated():
    # The owner has NOT set 3/6-month numbers — they must be untransactable, never defaulted.
    for slug in ("starter", "growth", "scale"):
        assert pricing.find_price(slug, 3) is None
        assert pricing.find_price(slug, 6) is None
        assert 3 not in pricing.allowed_commitments(slug)
        assert 6 not in pricing.allowed_commitments(slug)
        assert pricing.allowed_commitments(slug) == [1, 9]


def test_checkout_mode_branches_on_kind():
    assert pricing.checkout_mode("coffee") == "payment"
    assert pricing.checkout_mode("starter") == "subscription"
    assert pricing.checkout_mode("scale") == "subscription"
    assert pricing.checkout_mode("enterprise") is None  # contact -> rejected
    assert pricing.checkout_mode("nope") is None


def test_coffee_has_no_commitment_axis():
    assert pricing.allowed_commitments("coffee") == [1]
    assert pricing.find_price("coffee", 9) is None


def test_enterprise_is_contact_only():
    assert pricing.get_plan("enterprise")["kind"] == "contact"
    assert pricing.allowed_commitments("enterprise") == []


def test_dollars_formatting():
    assert pricing.dollars(654) == "$6.54"
    assert pricing.dollars(1900) == "$19.00"
    assert pricing.dollars(206100) == "$2,061.00"


def test_public_view_is_honest():
    cards = {c["slug"]: c for c in pricing.public_view()}
    assert cards["coffee"]["price_label"] == "$6.54 one-time"
    assert cards["starter"]["price_label"] == "$19.00/mo"
    assert "unlimited" not in (cards["scale"]["blurb"].lower())
    # subscription cards advertise the concrete annual deal + a contact note for 3/6
    assert "pay 9, get 12" in cards["growth"]["annual_label"]
    assert cards["growth"]["commitment_note"] and "contact" in cards["growth"]["commitment_note"].lower()
    assert cards["coffee"]["annual_label"] is None


def test_all_lookup_keys_unique_and_usd():
    lks = [p["lookup_key"] for p in pricing.all_lookup_keys()]
    assert len(lks) == len(set(lks)), "lookup_keys must be unique"
    assert pricing.CURRENCY == "usd"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
