"""Sacred Separation tests — tenant projection must be structurally clean."""
import json
from pathlib import Path

from aims_warehouse.redaction import redact_card, redact_payload, is_internal_text

DATA = Path(__file__).resolve().parents[1] / "aims_warehouse/picker_ang/data/foai-tool-inventory-log.jsonl"

INTERNAL_CARD = {
    "name": "Example", "category": "database", "status": "candidate",
    "type": "orm", "version": "1.0", "origin": "achievemor/example",
    "source_shelf": "Carry Lane — Legacy Reconciliation (SmelterOS/UTW)",
    "note": "House repo — Code_Ang lineage, seven-gate pending, NemoClaw scoped.",
    "secret_field": "should never pass",
}

def test_tenant_card_allowlist_and_failclosed_note():
    out = redact_card(INTERNAL_CARD)
    assert "secret_field" not in out
    assert "source_shelf" not in out and out["source"] == "curated"
    assert not is_internal_text(out["note"])
    assert out["origin"] == ""  # achievemor pointer blanked

def test_operator_passthrough():
    payload = {"tools": [INTERNAL_CARD]}
    assert redact_payload(payload, "operator") == payload

def test_tenant_payload_walk():
    payload = {"tools": [INTERNAL_CARD], "summary": "routed via Chicken Hawk"}
    out = redact_payload(payload, "tenant")
    assert out["summary"] == ""
    assert all(not is_internal_text(json.dumps(c)) or c.get("name") for c in out["tools"])

def test_public_seed_is_clean():
    flagged = []
    for line in DATA.open():
        r = json.loads(line)
        for k, v in r.items():
            if isinstance(v, str) and k not in ("name", "origin", "install") and is_internal_text(v):
                flagged.append((r["name"], k))
    assert flagged == []


def test_internal_only_records_dropped_for_tenants():
    card = dict(INTERNAL_CARD, visibility="internal")
    out = redact_payload({"tools": [card, dict(INTERNAL_CARD, name="Public")]}, "tenant")
    names = [c["name"] for c in out["tools"]]
    assert "Example" not in names and "Public" in names
