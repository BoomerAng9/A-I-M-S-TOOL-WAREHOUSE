"""Email delivery of the minted API key, via Resend.

Used by the webhook for payments that have NO warehouse success page to claim on —
i.e. a Paperform stepper (or any external Stripe payment) where Paperform owns its
own confirmation screen. For those, the key is minted in the webhook and emailed
here. NEVER raises into the webhook (a send failure must not 5xx the event — the
tenant is already provisioned; the operator can re-mint from /admin if needed).

Degrades off: with no RESEND_API_KEY, ``configured()`` is False and the caller
keeps the in-app claim flow. ``RESEND_FROM`` must be a verified Resend sender
(default uses Resend's onboarding sender so test mode works before domain setup).
"""
from __future__ import annotations

import logging
import os

import httpx

_log = logging.getLogger("aims_warehouse.billing.email")

_DEFAULT_FROM = "A.I.M.S. Tool Warehouse <onboarding@resend.dev>"


def configured() -> bool:
    return bool(os.environ.get("RESEND_API_KEY"))


def _from() -> str:
    return os.environ.get("RESEND_FROM") or _DEFAULT_FROM


def _html(api_key: str, plan_label: str, base_url: str) -> str:
    base = (base_url or "https://warehouse.aimanagedsolutions.cloud").rstrip("/")
    return f"""<!doctype html><html><body style="margin:0;background:#070707;color:#e8ffe8;font-family:ui-monospace,Menlo,monospace;padding:2rem">
<div style="max-width:560px;margin:0 auto">
<div style="font-size:.7rem;letter-spacing:.3em;text-transform:uppercase;color:#39ff14">A.I.M.S. Tool Warehouse</div>
<h1 style="font-size:1.6rem;margin:.4rem 0">You're in.</h1>
<p style="color:#bdebbd;line-height:1.6">Thanks for your {plan_label} purchase. Here is your API key. It is shown only here &mdash; store it now; we cannot recover it.</p>
<div style="margin:1.2rem 0;padding:1rem;background:#0f1a0f;border:1px solid #39ff14;border-radius:8px;word-break:break-all;color:#9dff9d">{api_key}</div>
<p style="color:#bdebbd;font-size:.9rem">Use it as <code>Authorization: Bearer &lt;key&gt;</code> against the warehouse API at <a style="color:#39ff14" href="{base}">{base}</a>.</p>
<p style="color:#5f9f5f;font-size:.7rem;margin-top:2rem;letter-spacing:.2em;text-transform:uppercase">A.I.M.S. &middot; aimanagedsolutions.cloud</p>
</div></body></html>"""


def send_api_key(to_email: str, api_key: str, plan_label: str, base_url: str = "") -> bool:
    """Send the API key. Returns True on success; never raises."""
    if not configured() or not to_email or not api_key:
        return False
    try:
        r = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {os.environ['RESEND_API_KEY']}"},
            json={
                "from": _from(),
                "to": [to_email],
                "subject": "Your A.I.M.S. Tool Warehouse API key",
                "html": _html(api_key, plan_label, base_url),
            },
            timeout=15,
        )
        if r.status_code in (200, 201):
            return True
        _log.warning("resend send to %s returned %s", to_email, r.status_code)
        return False
    except Exception:
        _log.exception("resend send to %s failed", to_email)
        return False


def _magic_html(link: str) -> str:
    return f"""<!doctype html><html><body style="margin:0;background:#070707;color:#e8ffe8;font-family:ui-monospace,Menlo,monospace;padding:2rem">
<h2 style="color:#39ff14;font-weight:600">A.I.M.S. Tool Warehouse</h2>
<p>Click to sign in to your account. This link is single-use and expires in 15 minutes.</p>
<p style="margin:1.4rem 0"><a href="{link}" style="display:inline-block;padding:.8rem 1.2rem;background:#0f1a0f;border:1px solid #39ff14;border-radius:8px;color:#9dff9d;text-decoration:none">&gt;_ sign in</a></p>
<p style="opacity:.6;font-size:.8rem;word-break:break-all">{link}</p>
<p style="opacity:.5;font-size:.75rem">If you didn't request this, ignore it — no account changes were made.</p>
</body></html>"""


def send_magic_link(to_email: str, link: str) -> bool:
    """Send a sign-in magic link. Returns True on success; never raises."""
    if not configured() or not to_email or not link:
        return False
    try:
        r = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {os.environ['RESEND_API_KEY']}"},
            json={
                "from": _from(),
                "to": [to_email],
                "subject": "Sign in to A.I.M.S. Tool Warehouse",
                "html": _magic_html(link),
            },
            timeout=15,
        )
        if r.status_code in (200, 201):
            return True
        _log.warning("resend magic-link to %s returned %s", to_email, r.status_code)
        return False
    except Exception:
        _log.exception("resend magic-link to %s failed", to_email)
        return False
