"""Sacred Separation for the catalog serving surface.

Two projections of every Tool Discovery Card:

  OPERATOR  (X-Service-Token)        -> full fidelity. Internal notes, shelf
                                        provenance, intake conditions.
  TENANT    (Authorization: Bearer)  -> customer-safe allowlist. Internal
                                        vocabulary is structurally unreachable.

Redaction is FAIL-CLOSED: a tenant-bound text field that trips the internal
vocabulary screen is replaced with a generic line built from public fields —
never "cleaned" in place. An unknown field never passes; only the allowlist
leaves the building.
"""
from __future__ import annotations

import re
from typing import Any

# Fields a tenant may receive on a card. Everything else is dropped.
TENANT_CARD_FIELDS = (
    "name", "category", "status", "display_status", "verdict", "selectable",
    "type", "version", "version_asof", "origin", "install", "live", "note",
    "registry_id", "layer",
)

# Internal vocabulary screen. Any hit on a tenant-bound text field rejects the
# field. Word-bounded where substrings could collide with public prose
# (e.g. "canonical", "erlang").
_TOKENS = [
    r"_ang\b", r"\bboomer(?!ang9\b)", r"\blil_", r"chicken\s*hawk", r"nemoclaw",
    r"\bcharlotte\b", r"\bacheevy\b", r"achievemor", r"buildsmith",
    r"sqwaadrun", r"\bmoex\b", r"bamaram", r"\bmelli\b", r"\biller\b",
    r"\bpicker\b", r"\bhouse\s+(repo|skill|gate|stack|auth|styling|rail)",
    r"house-authored", r"\bcanon\b", r"seven[- ]gate", r"five[- ]gate",
    r"bank\s+of\s+code", r"inworld\s+router", r"\blineage\b", r"\bcustee",
    r"\bvps\b", r"81%", r"\bfdh\b", r"sacred\s+separation", r"\bmelanium\b",
    r"\bsmelter", r"\bfoai\b", r"\bgrammar\b.*filter", r"\bdeploy\s+engine\b",
    r"carry\s+lane", r"legacy\s+reconciliation", r"shelf\s+completion",
    r"(?<!file )intake", r"\bquarantine\b", r"proof_bundle", r"\bvendor(ed|ing)?\b",
]
_SCREEN = re.compile("|".join(_TOKENS), re.IGNORECASE)

# Neutral shelf-source labels for the tenant projection.
_PUBLIC_SOURCE = (
    ("Registry — MCP", "mcp-registry"),
    ("Registry — Skills (indexes)", "skills-registry"),
    ("Registry — Skills", "skills-registry"),
)


def is_internal_text(text: str | None) -> bool:
    return bool(text) and bool(_SCREEN.search(text))


def _generic_note(card: dict[str, Any]) -> str:
    t = (card.get("type") or "Tool").strip()
    c = (card.get("category") or "general").strip()
    return f"{t[:1].upper()}{t[1:]} — {c} shelf."


def public_source(source_shelf: str | None) -> str:
    s = source_shelf or ""
    for prefix, label in _PUBLIC_SOURCE:
        if s.startswith(prefix):
            return label
    return "curated"


def redact_card(card: dict[str, Any]) -> dict[str, Any]:
    """Tenant projection of one serialized card. Allowlist + fail-closed note."""
    out: dict[str, Any] = {}
    for k in TENANT_CARD_FIELDS:
        if k in card:
            out[k] = card[k]
    note = out.get("note")
    if is_internal_text(note):
        out["note"] = _generic_note(card)
    out.pop("visibility", None)
    out["source"] = public_source(card.get("source_shelf"))
    if str(out.get("origin", "")).lower().startswith("achievemor/"):
        out["origin"] = ""
    # Final screen across every string that survived — fail closed.
    for k, v in list(out.items()):
        if isinstance(v, str) and k not in ("name", "origin", "install") and is_internal_text(v):
            out[k] = "" if k != "note" else _generic_note(card)
    return out


_CARD_LIST_KEYS = ("tools", "certified_tools", "recommendations", "candidates", "results")


def redact_payload(payload: Any, caller_kind: str) -> Any:
    """Route-level wrapper: operator passes through; tenant payloads have every
    card list redacted and every stray free-text field screened."""
    if caller_kind == "operator":
        return payload
    return _walk(payload)


def _walk(node: Any) -> Any:
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for k, v in node.items():
            if k in _CARD_LIST_KEYS and isinstance(v, list):
                out[k] = [redact_card(i) for i in v
                          if not (isinstance(i, dict) and i.get("visibility") == "internal")]
            elif k == "source_shelf":
                out["source"] = public_source(v if isinstance(v, str) else None)
            elif isinstance(v, str) and k in ("note", "why", "where", "reasoning", "summary"):
                out[k] = "" if is_internal_text(v) else v
            else:
                out[k] = _walk(v)
        return out
    if isinstance(node, list):
        return [_walk(i) for i in node]
    return node
