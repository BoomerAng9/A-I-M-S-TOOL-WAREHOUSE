"""Billing HTTP surface — mounted by the warehouse service.

Routes:
  GET  /pricing                 public sales page (renders from pricing.py — never fabricated)
  POST /billing/checkout        create a Stripe Checkout Session -> {url}   (503 until Stripe configured)
  POST /billing/webhook         Stripe-signature-authenticated; provisions in ONE transaction
  GET  /billing/success         post-checkout page; reads #token (fragment) and claims client-side
  POST /billing/claim           mint the tenant's API key ONCE for a paid purchase

Security posture (see the design critique): webhook fails CLOSED without its signing
secret; the claim capability is a server-minted token in the URL FRAGMENT (never the
query string); checkout strictly validates plan/commitment and is rate-limited.
"""
from __future__ import annotations

import hmac
import logging
import os
import secrets
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from ..tenancy import db
from . import email as email_mod
from . import pricing, provision
from . import stripe_gateway as gw

_log = logging.getLogger("aims_warehouse.billing.routes")

router = APIRouter()

_NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}

# --- minimal in-memory per-IP fixed-window limiter for the public checkout route ---
# Single-replica semantics (moves to Redis with the rest of the platform). Enough to
# blunt session-spam abuse of the unauthenticated endpoint.
_RL_WINDOW = 60.0
_RL_MAX = 10
_rl_hits: dict[str, list[float]] = {}


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limited(ip: str) -> bool:
    now = time.monotonic()
    if len(_rl_hits) > 5000:  # bounded memory: evict IPs with no hits inside the window
        for k in [k for k, v in _rl_hits.items() if not v or now - v[-1] >= _RL_WINDOW]:
            _rl_hits.pop(k, None)
    hits = [t for t in _rl_hits.get(ip, []) if now - t < _RL_WINDOW]
    if len(hits) >= _RL_MAX:
        _rl_hits[ip] = hits
        return True
    hits.append(now)
    _rl_hits[ip] = hits
    return False


# =========================================================================== /pricing
def _pricing_html() -> str:
    cards = pricing.public_view()
    blocks = []
    for c in cards:
        cta = (
            '<a class="cta contact" href="mailto:sales@aimanagedsolutions.cloud">Contact sales</a>'
            if c["kind"] == "contact"
            else f'<button class="cta" data-plan="{c["slug"]}" data-commitment="1">Get {c["display_name"]}</button>'
        )
        annual = ""
        if c.get("annual_label"):
            annual = (
                f'<div class="annual">{c["annual_label"]} '
                f'<button class="link" data-plan="{c["slug"]}" data-commitment="9">choose annual</button></div>'
            )
        note = f'<div class="note">{c["commitment_note"]}</div>' if c.get("commitment_note") else ""
        blocks.append(f"""
        <div class="card {'hot' if c['highlight'] else ''}">
          <div class="pname">{c['display_name']}</div>
          <div class="tag">{c['tagline']}</div>
          <div class="price">{c['price_label']}</div>
          {annual}
          <p class="blurb">{c['blurb']}</p>
          {cta}
          {note}
        </div>""")
    cards_html = "\n".join(blocks)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>A.I.M.S. Tool Warehouse — Pricing</title>
<style>
:root{{color-scheme:dark}}*{{box-sizing:border-box}}
body{{margin:0;background:#070707;color:#e8ffe8;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;padding:3rem 1.2rem}}
h1{{text-align:center;letter-spacing:.04em;font-size:clamp(1.6rem,4vw,2.4rem);margin:.2rem 0}}
.sub{{text-align:center;color:#7dffa0;opacity:.75;font-size:.72rem;letter-spacing:.22em;text-transform:uppercase;margin-bottom:2.4rem}}
.grid{{display:flex;flex-wrap:wrap;gap:1rem;justify-content:center;max-width:1100px;margin:0 auto}}
.card{{background:#0c120c;border:1px solid #1f3a1f;border-radius:12px;padding:1.6rem;width:260px;display:flex;flex-direction:column}}
.card.hot{{border-color:#39ff14;box-shadow:0 0 22px rgba(57,255,20,.18)}}
.pname{{font-size:1.15rem;letter-spacing:.04em}}.tag{{color:#6fbf6f;font-size:.72rem;margin:.2rem 0 1rem;text-transform:uppercase;letter-spacing:.12em}}
.price{{font-size:1.5rem;color:#39ff14;margin-bottom:.2rem}}.annual{{font-size:.78rem;color:#bdebbd;margin-bottom:.6rem}}
.blurb{{color:#bdebbd;font-size:.84rem;line-height:1.5;flex:1}}
.cta{{margin-top:1rem;background:#39ff14;color:#062006;border:0;border-radius:8px;padding:.7rem 1rem;font:inherit;font-weight:700;cursor:pointer;text-decoration:none;text-align:center}}
.cta.contact{{background:transparent;color:#39ff14;border:1px solid #2f6b2f}}
.link{{background:none;border:0;color:#39ff14;text-decoration:underline;cursor:pointer;font:inherit;font-size:.78rem;padding:0}}
.note{{margin-top:.6rem;font-size:.66rem;color:#5f9f5f}}
.err{{max-width:1100px;margin:1rem auto 0;text-align:center;color:#ff9d6f;font-size:.8rem;min-height:1.2em}}
.foot{{text-align:center;margin-top:2.6rem;font-size:.62rem;letter-spacing:.25em;text-transform:uppercase;color:#2f6b2f}}
</style></head><body>
<h1>A.I.M.S. Tool Warehouse</h1>
<div class="sub">Certified builder tools · pay once or subscribe · metered fair-use caps apply</div>
<div class="grid">{cards_html}</div>
<div class="err" id="err"></div>
<div class="foot">A.I.M.S. · aimanagedsolutions.cloud</div>
<script>
document.querySelectorAll('[data-plan]').forEach(function(b){{
  b.addEventListener('click', async function(){{
    var err=document.getElementById('err'); err.textContent='';
    b.disabled=true; var old=b.textContent; b.textContent='…';
    try{{
      var r=await fetch('/billing/checkout',{{method:'POST',headers:{{'Content-Type':'application/json'}},
        body:JSON.stringify({{plan:b.dataset.plan,commitment:Number(b.dataset.commitment)}})}});
      var d=await r.json();
      if(r.ok && d.url){{ window.location=d.url; return; }}
      err.textContent = d.detail || 'Checkout is not available right now.';
    }}catch(e){{ err.textContent='Network error — please try again.'; }}
    b.disabled=false; b.textContent=old;
  }});
}});
</script></body></html>"""


@router.get("/pricing", response_class=HTMLResponse)
async def pricing_page() -> str:
    return _pricing_html()


# =========================================================================== checkout
class CheckoutRequest(BaseModel):
    plan: str = Field(min_length=1, max_length=40)
    commitment: int = Field(default=1)
    email: Optional[str] = Field(default=None, max_length=320)
    model_config = {"extra": "forbid"}  # reject url-like / unexpected fields (no open-redirect surface)


@router.post("/billing/checkout")
async def checkout(body: CheckoutRequest, request: Request) -> JSONResponse:
    # Validate the plan/commitment against the locked catalog — never default a price.
    plan = pricing.get_plan(body.plan)
    if not plan:
        return JSONResponse({"detail": "unknown plan"}, status_code=400)
    if plan["kind"] == "contact":
        return JSONResponse({"detail": "Enterprise is contact-sales — email sales@aimanagedsolutions.cloud"}, status_code=400)
    if body.commitment not in pricing.allowed_commitments(body.plan):
        return JSONResponse(
            {"detail": "that commitment isn't available for this plan (3 & 6-month: contact us)"},
            status_code=400,
        )
    if not gw.secret_configured():
        return JSONResponse({"detail": "checkout is not enabled yet"}, status_code=503)
    if not db.tenancy_enabled():
        return JSONResponse({"detail": "service temporarily unavailable"}, status_code=503)
    if _rate_limited(_client_ip(request)):
        return JSONResponse({"detail": "too many requests — slow down"}, status_code=429)

    claim_token = secrets.token_urlsafe(32)
    idem = str(uuid.uuid4())
    try:
        session = gw.create_checkout_session(
            body.plan, body.commitment, claim_token, idem, email=(body.email or None)
        )
    except gw.StripeUnavailable as e:
        _log.warning("checkout unavailable: %s", e)
        return JSONResponse({"detail": "checkout is not available right now"}, status_code=503)
    except Exception:
        _log.exception("stripe checkout creation failed")
        return JSONResponse({"detail": "could not start checkout"}, status_code=502)

    # Record our session row (the token hash is the future claim capability).
    try:
        async with db.pool().connection() as conn:
            await conn.execute(
                "INSERT INTO billing_sessions (session_id, plan, commitment, mode, claim_token_hash) "
                "VALUES (%s, %s, %s, %s, %s)",
                (session["id"], body.plan, body.commitment, session["mode"], provision.token_hash(claim_token)),
            )
    except Exception:
        _log.exception("failed to persist billing_sessions row for %s", session.get("id"))
        return JSONResponse({"detail": "could not start checkout"}, status_code=500)
    return JSONResponse({"url": session["url"]})


# =========================================================================== webhook
@router.post("/billing/webhook")
async def webhook(request: Request) -> Response:
    # Fail CLOSED: no signing secret -> never process a (possibly forged) event.
    if not gw.webhook_configured():
        return JSONResponse({"detail": "webhook not configured"}, status_code=503)
    raw = await request.body()  # exact bytes — required for signature verification
    sig = request.headers.get("stripe-signature", "")
    try:
        event = gw.construct_event(raw, sig)
    except gw.StripeUnavailable as e:
        _log.error("webhook SDK/secret problem: %s", e)
        return JSONResponse({"detail": "webhook unavailable"}, status_code=503)
    except Exception as e:
        _log.warning("webhook signature verification failed: %s", type(e).__name__)
        return JSONResponse({"detail": "invalid signature"}, status_code=400)

    if not gw.livemode_matches(event):
        # Stray test/live event for the wrong mode — ack so Stripe stops retrying.
        return JSONResponse({"received": True, "ignored": "livemode"}, status_code=200)

    delivery = None
    try:
        async with db.pool().connection() as conn:
            async with conn.transaction():
                is_new = await provision.record_event(
                    conn, event.get("id"), event.get("type", ""), event.get("livemode")
                )
                if is_new:
                    delivery = await provision.handle_event(conn, event)
    except Exception:
        # Roll back BOTH the ledger row and the work; 5xx so Stripe redelivers.
        _log.exception("webhook processing failed for event %s", event.get("id"))
        return JSONResponse({"detail": "processing error"}, status_code=500)
    # External (Paperform) payments have no claim page -> email the minted key AFTER the
    # commit (never inside the txn — it's a network call; a send failure won't 5xx, the
    # tenant is already provisioned and the operator can re-mint from /admin).
    if delivery and delivery.get("api_key") and delivery.get("email"):
        sent = email_mod.send_api_key(
            delivery["email"], delivery["api_key"], delivery.get("plan_label", "your"), gw.public_url()
        )
        if not sent:
            _log.error("KEY EMAIL FAILED for %s (tenant provisioned; re-mint via /admin)", delivery["email"])
    return JSONResponse({"received": True}, status_code=200)


# =========================================================================== paperform webhook
#
# A Paperform stepper holds the Stripe connection on ITS side (no sk_live_ in our code),
# collects the payment, and POSTs its submission here. We verify a shared secret (set the
# same value as a custom header `X-Webhook-Secret` in the Paperform webhook config — Paperform
# supports custom headers), parse the submission's email + Stripe `charge`, and hand off to
# the existing idempotent `provision_external` (amount->plan, mint key, exactly-once on the
# submission id). Fail CLOSED: no secret configured -> never process.


def _paperform_secret_state(request: Request) -> Optional[bool]:
    """None = not configured (503); True/False = secret match result."""
    expected = os.environ.get("PAPERFORM_WEBHOOK_SECRET", "")
    if not expected:
        return None
    provided = request.headers.get("x-webhook-secret") or request.query_params.get("secret") or ""
    return hmac.compare_digest(provided, expected)


def _paperform_extract(payload: dict[str, Any]) -> dict[str, Any]:
    """Pull (submission_id, email, amount_cents, currency, paid) from a Paperform
    submission webhook. `data` is an array of question objects; `charge` is the Stripe
    charge (present only when the form took a payment)."""
    sub_id = str(payload.get("submission_id") or payload.get("id") or "").strip()
    email: Optional[str] = None
    for q in payload.get("data") or []:
        if not isinstance(q, dict):
            continue
        ckey = str(q.get("custom_key") or "").lower()
        if (q.get("type") == "email" or ckey in ("email", "billing_email")) and q.get("value"):
            email = str(q.get("value")).strip().lower()
            break
    charge = payload.get("charge") if isinstance(payload.get("charge"), dict) else {}
    amount = charge.get("amount") or charge.get("amount_total")
    currency = charge.get("currency")
    status = charge.get("status")
    paid = bool(charge) and charge.get("paid", True) and status in (None, "succeeded", "paid")
    if not email:
        bd = charge.get("billing_details") or {}
        cand = (bd.get("email") or charge.get("receipt_email") or "").strip().lower()
        email = cand or None
    return {"sub_id": sub_id, "email": email, "amount": amount, "currency": currency,
            "has_charge": bool(charge), "paid": paid}


@router.post("/billing/paperform-webhook")
async def paperform_webhook(request: Request) -> Response:
    state = _paperform_secret_state(request)
    if state is None:
        return JSONResponse({"detail": "paperform webhook not configured"}, status_code=503)
    if not state:
        return JSONResponse({"detail": "invalid secret"}, status_code=401)
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"detail": "invalid json"}, status_code=400)

    f = _paperform_extract(payload)
    if not f["sub_id"]:
        return JSONResponse({"detail": "no submission id"}, status_code=400)
    # Only a real PAID charge provisions. No charge / unpaid -> ack (so Paperform stops
    # retrying) without granting anything.
    if not (f["has_charge"] and f["paid"] and f["amount"] and f["email"]):
        return JSONResponse({"received": True, "provisioned": False, "reason": "no paid charge / no email"}, status_code=200)

    delivery = None
    try:
        async with db.pool().connection() as conn:
            async with conn.transaction():
                delivery = await provision.provision_external(
                    conn, ext_id=f"pf:{f['sub_id']}", customer_id=None, email=f["email"],
                    amount_cents=int(f["amount"]), currency=f["currency"],
                )
    except Exception:
        _log.exception("paperform webhook provisioning failed for %s", f["sub_id"])
        return JSONResponse({"detail": "processing error"}, status_code=500)

    # External payment has no in-app claim page -> email the minted key after commit.
    if delivery and delivery.get("api_key") and delivery.get("email"):
        sent = email_mod.send_api_key(
            delivery["email"], delivery["api_key"], delivery.get("plan_label", "your"), gw.public_url()
        )
        if not sent:
            _log.error("paperform KEY EMAIL FAILED for %s (tenant provisioned; re-mint via /admin)", delivery["email"])
    return JSONResponse({"received": True, "provisioned": bool(delivery)}, status_code=200)


# =========================================================================== success page
_SUCCESS_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Payment received — A.I.M.S. Tool Warehouse</title>
<style>
:root{color-scheme:dark}body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:#070707;color:#e8ffe8;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;padding:2rem}
.card{max-width:620px;text-align:center}.tag{font-size:.7rem;letter-spacing:.3em;text-transform:uppercase;color:#39ff14}
h1{font-size:1.8rem;margin:.6rem 0}p{color:#bdebbd;line-height:1.6;font-size:.92rem}
.key{margin:1.2rem 0;padding:1rem;background:#0f1a0f;border:1px solid #39ff14;border-radius:8px;word-break:break-all;color:#9dff9d;font-size:.9rem}
.warn{color:#ffd27d;font-size:.8rem}.err{color:#ff9d6f}button{background:#39ff14;color:#062006;border:0;border-radius:8px;padding:.55rem .9rem;font:inherit;font-weight:700;cursor:pointer}
</style></head><body><div class="card">
<div class="tag">A.I.M.S. Tool Warehouse</div>
<h1 id="title">Finalizing your access…</h1>
<p id="msg">One moment — confirming your payment.</p>
<div id="keybox" style="display:none">
  <p class="warn">This is your API key. It is shown <b>once</b> and cannot be recovered — store it now.</p>
  <div class="key" id="key"></div>
  <button onclick="navigator.clipboard.writeText(document.getElementById('key').textContent)">Copy key</button>
  <p>Use it as <code>Authorization: Bearer &lt;key&gt;</code> against the warehouse API.</p>
</div>
</div>
<script>
(function(){
  var token = (location.hash.match(/token=([^&]+)/)||[])[1];
  var title=document.getElementById('title'), msg=document.getElementById('msg');
  if(!token){ title.textContent='Missing claim token'; msg.textContent='Open the link from your payment confirmation.'; return; }
  history.replaceState(null,'',location.pathname);  // drop the token from the visible URL
  var tries=0;
  async function claim(){
    tries++;
    try{
      var r=await fetch('/billing/claim',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:token})});
      var d=await r.json();
      if(r.ok && d.api_key){
        title.textContent='You\\'re in.'; msg.textContent='Your account is ready.';
        document.getElementById('key').textContent=d.api_key;
        document.getElementById('keybox').style.display='block'; return;
      }
      if(r.status===402 && tries<12){ msg.textContent='Confirming payment… ('+tries+')'; setTimeout(claim,2500); return; }
      if(r.status===409){ title.textContent='Already claimed'; msg.innerHTML='This key was already issued. If you lost it, contact <b>support@aimanagedsolutions.cloud</b> to re-issue.'; return; }
      title.textContent='Could not issue key'; msg.className='err'; msg.textContent=(d.detail||'Please contact support.');
    }catch(e){ if(tries<12){ setTimeout(claim,2500); } else { title.textContent='Network error'; msg.className='err'; msg.textContent='Please refresh.'; } }
  }
  claim();
})();
</script></body></html>"""


@router.get("/billing/success", response_class=HTMLResponse)
async def success() -> HTMLResponse:
    return HTMLResponse(_SUCCESS_HTML, headers=_NO_STORE)


# =========================================================================== claim
class ClaimRequest(BaseModel):
    token: str = Field(min_length=8, max_length=200)
    model_config = {"extra": "forbid"}


@router.post("/billing/claim")
async def claim(body: ClaimRequest) -> JSONResponse:
    if not db.tenancy_enabled() or not gw.secret_configured():
        return JSONResponse({"detail": "service unavailable"}, status_code=503, headers=_NO_STORE)
    th = provision.token_hash(body.token)
    # Fast pre-checks (no lock): unknown token -> 404; already claimed -> 409.
    try:
        async with db.pool().connection() as conn:
            cur = await conn.execute(
                "SELECT session_id, claimed_at FROM billing_sessions WHERE claim_token_hash = %s", (th,)
            )
            pre = await cur.fetchone()
    except Exception:
        _log.exception("claim pre-read failed")
        return JSONResponse({"detail": "service unavailable"}, status_code=503, headers=_NO_STORE)
    if pre is None:
        return JSONResponse({"detail": "unknown or expired claim token"}, status_code=404, headers=_NO_STORE)
    session_id, claimed_at = pre
    if claimed_at is not None:
        return JSONResponse({"detail": "already claimed"}, status_code=409, headers=_NO_STORE)

    # Authoritative payment check (server-side), then provision-and-mint in one txn.
    try:
        session_obj = gw.retrieve_session(session_id)
    except gw.StripeUnavailable:
        return JSONResponse({"detail": "service unavailable"}, status_code=503, headers=_NO_STORE)
    except Exception:
        _log.exception("claim session retrieve failed")
        return JSONResponse({"detail": "could not verify payment"}, status_code=502, headers=_NO_STORE)

    try:
        async with db.pool().connection() as conn:
            async with conn.transaction():
                minted = await provision.claim_finalize(conn, body.token, session_obj)
    except provision.ClaimError as e:
        return JSONResponse({"detail": str(e)}, status_code=e.status, headers=_NO_STORE)
    except Exception:
        _log.exception("claim finalize failed")
        return JSONResponse({"detail": "could not issue key"}, status_code=500, headers=_NO_STORE)

    return JSONResponse(
        {"api_key": minted["api_key"], "key_prefix": minted["key_prefix"], "scopes": minted["scopes"],
         "note": minted["note"]},
        headers=_NO_STORE,
    )
