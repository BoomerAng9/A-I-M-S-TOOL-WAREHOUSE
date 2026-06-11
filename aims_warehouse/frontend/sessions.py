"""Signed, stateless tokens for the frontend — magic-link + session cookie.

Stdlib only (hmac-sha256 over a compact JSON payload + expiry). No DB row, no
extra dependency. The signing secret is WAREHOUSE_SESSION_SECRET, falling back to
TOOL_WAREHOUSE_TOKEN (always set in this service) so sign-in works with zero new
config. Tenant-scoped ONLY — a session never carries operator scope.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

_MAGIC_TTL = int(os.environ.get("WAREHOUSE_MAGIC_TTL", "900"))        # 15 min
_SESSION_TTL = int(os.environ.get("WAREHOUSE_SESSION_TTL", "2592000"))  # 30 days
SESSION_COOKIE = "wh_session"


def _secret() -> bytes:
    s = os.environ.get("WAREHOUSE_SESSION_SECRET") or os.environ.get("TOOL_WAREHOUSE_TOKEN") or ""
    return s.encode("utf-8")


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign(payload: dict[str, Any], ttl: int) -> str:
    """Sign a payload with an expiry. Returns `<body>.<sig>` (URL-safe)."""
    body = dict(payload)
    body["exp"] = int(time.time()) + ttl
    raw = _b64e(json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    sig = _b64e(hmac.new(_secret(), raw.encode("ascii"), hashlib.sha256).digest())
    return f"{raw}.{sig}"


def verify(token: str) -> dict[str, Any] | None:
    """Verify signature + expiry. Returns the payload, or None if invalid/expired."""
    if not token or "." not in token or not _secret():
        return None
    raw, _, sig = token.partition(".")
    expected = _b64e(hmac.new(_secret(), raw.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        body = json.loads(_b64d(raw).decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if int(body.get("exp", 0)) < int(time.time()):
        return None
    return body


def sign_magic(email: str) -> str:
    return sign({"p": "magic", "email": email.strip().lower()}, _MAGIC_TTL)


def read_magic(token: str) -> str | None:
    body = verify(token)
    if body and body.get("p") == "magic":
        return body.get("email")
    return None


def sign_session(tenant_id: str, email: str) -> str:
    return sign({"p": "sess", "tid": tenant_id, "email": email.strip().lower()}, _SESSION_TTL)


def read_session(token: str) -> dict[str, Any] | None:
    body = verify(token)
    if body and body.get("p") == "sess" and body.get("tid"):
        return {"tenant_id": body["tid"], "email": body.get("email", "")}
    return None
