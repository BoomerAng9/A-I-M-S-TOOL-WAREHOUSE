"""Billing — Stripe Checkout paywall for the A.I.M.S. Tool Warehouse.

A standalone, self-contained package the warehouse service mounts as a router.
It is GRACEFULLY OFF until the owner places ``STRIPE_SECRET_KEY`` (and, for the
webhook, ``STRIPE_WEBHOOK_SECRET``) in the environment — mirroring the tenancy
degrade pattern. No secret is ever hardcoded; pricing numbers are owner-locked
in ``pricing.py``; the catalog access a paid key grants is enforced by tenant
``status`` (see ``store.resolve_key``).
"""
