"""API key generation + verification.

Keys are high-entropy random secrets, so they are hashed with SHA-256 (optionally
HMAC'd with a server-side pepper, env ``WAREHOUSE_KEY_PEPPER``) — fast, because we
hash on every request; a slow password hash (argon2/bcrypt) would only add latency
and a DoS amplifier here. Format: ``aimswh_<prefix>_<secret>`` where ``prefix`` is
64-bit (collision-safe at scale). Only the prefix (indexed) + hash are stored; the
full key is shown ONCE and is unrecoverable thereafter. Verification is constant-time.

The stored hash carries a scheme tag (``v1$`` = plain SHA-256, ``v2$`` = HMAC with
pepper) so adding/rotating the pepper does NOT silently invalidate every existing
key — ``verify`` selects the algorithm per stored row.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets

LABEL = "aimswh"


def _pepper() -> bytes:
    return os.environ.get("WAREHOUSE_KEY_PEPPER", "").encode()


def _compute(secret: str) -> tuple[str, str]:
    """Return ``(scheme, hexdigest)`` — v2 (HMAC+pepper) when a pepper is set, else v1."""
    pepper = _pepper()
    if pepper:
        return "v2", hmac.new(pepper, secret.encode(), hashlib.sha256).hexdigest()
    return "v1", hashlib.sha256(secret.encode()).hexdigest()


def hash_secret(secret: str) -> str:
    scheme, digest = _compute(secret)
    return f"{scheme}${digest}"


def verify(secret: str, stored: str) -> bool:
    """Constant-time verify; algorithm selected from the stored scheme tag."""
    if not stored or "$" not in stored:
        return False
    scheme, digest = stored.split("$", 1)
    pepper = _pepper()
    if scheme == "v2":
        if not pepper:
            return False
        calc = hmac.new(pepper, secret.encode(), hashlib.sha256).hexdigest()
    elif scheme == "v1":
        calc = hashlib.sha256(secret.encode()).hexdigest()
    else:
        return False
    return hmac.compare_digest(calc, digest)


def generate() -> tuple[str, str, str]:
    """Return ``(full_key, prefix, secret_hash)``. ``full_key`` is shown once."""
    prefix = secrets.token_hex(8)          # 64-bit lookup index — collision-safe at scale
    secret = secrets.token_urlsafe(32)     # ~256 bits of entropy
    full = f"{LABEL}_{prefix}_{secret}"
    return full, prefix, hash_secret(secret)


def parse(full_key: str) -> tuple[str, str] | None:
    """Parse ``aimswh_<prefix>_<secret>`` → ``(prefix, secret)`` or None."""
    if not full_key:
        return None
    parts = full_key.split("_", 2)
    if len(parts) != 3 or parts[0] != LABEL or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]
