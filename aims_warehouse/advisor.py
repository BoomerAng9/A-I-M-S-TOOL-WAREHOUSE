"""AIMS Tool Advisor — goal-mode tool recommendations over the certified catalog.

Picker_Ang's advisory layer (internal name; customer-facing = "AIMS Advisor"):
given a builder's GOAL, retrieve candidate tools from the catalog and recommend
which to integrate, where, and why. Deterministic candidate retrieval always
works; when ``OPENROUTER_API_KEY`` is set, an LLM (model-layer canon = OpenRouter)
reasons over the candidates for richer placement guidance, falling back to the
deterministic ranking on any error. Reads ONLY catalog data — no Charlotte coupling.
"""
from __future__ import annotations

import json
import os
import re
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from .picker_ang.tool_warehouse import ToolWarehouse

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_DEFAULT_MODEL = "anthropic/claude-haiku-4.5"

_STOP = {
    "the", "and", "for", "with", "that", "this", "from", "into", "your", "you", "are",
    "our", "use", "using", "build", "building", "make", "need", "needs", "want", "app",
    "application", "tool", "tools", "integrate", "integration", "add", "system", "data",
    "have", "has", "can", "will", "would", "should", "their", "they", "able",
}


def _tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]{3,}", (s or "").lower()) if t not in _STOP}


def _blob(c) -> str:
    parts = (c.name, c.category, c.note or "", c.type or "", getattr(c, "layer", "") or "")
    return " ".join(p for p in parts if p).lower()


def _score(c, goal_tokens: set[str]) -> int:
    # Never recommend blocked tools (rejected/deprecated/unknown).
    if not c.selectable and c.status.value in {"rejected", "deprecated", "unknown"}:
        return -1
    blob = _blob(c)
    namecat = (c.name + " " + c.category).lower()
    score = sum(1 for t in goal_tokens if t in blob) + sum(2 for t in goal_tokens if t in namecat)
    if c.selectable:
        score += 3  # certified boost
    return score


def _candidates(warehouse: "ToolWarehouse", goal: str, k: int = 12) -> list:
    toks = _tokens(goal)
    scored = [(s, c) for c in warehouse.cards if (s := _score(c, toks)) > 0]
    # certified first, then higher score
    scored.sort(key=lambda sc: (bool(sc[1].selectable), sc[0]), reverse=True)
    return [c for _, c in scored[:k]]


def _cand_dict(c) -> dict[str, Any]:
    return {
        "name": c.name,
        "category": c.category,
        "certified": bool(c.selectable),
        "status": c.status.value,
        "type": c.type,
        "layer": getattr(c, "layer", None),
        "note": (c.note or "")[:240],
    }


async def recommend(warehouse: "ToolWarehouse", goal: str, *, limit: int = 8) -> dict[str, Any]:
    goal = (goal or "").strip()
    if not goal:
        return {"goal": goal, "advisor": "none", "considered": 0, "recommendations": [], "message": "provide a goal"}
    cands = _candidates(warehouse, goal, k=12)
    key = os.environ.get("OPENROUTER_API_KEY")
    if key and cands:
        try:
            return await _llm_recommend(goal, cands, key, limit)
        except Exception:
            pass  # fall through to deterministic
    recs = [
        {
            "tool": c.name,
            "category": c.category,
            "certified": bool(c.selectable),
            "status": c.status.value,
            "where": (getattr(c, "layer", None) or c.category),
            "why": "matches your goal in the " + c.category + " shelf"
            + ("" if c.selectable else " (needs review — not yet certified)"),
            "priority": "high" if c.selectable else "medium",
        }
        for c in cands[:limit]
    ]
    return {
        "goal": goal,
        "advisor": "deterministic",
        "considered": len(cands),
        "recommendations": recs,
        "message": None if key else "Set OPENROUTER_API_KEY on the warehouse for LLM-grade placement guidance.",
    }


async def _llm_recommend(goal: str, cands: list, key: str, limit: int) -> dict[str, Any]:
    model = os.environ.get("ADVISOR_MODEL", _DEFAULT_MODEL)
    catalog = [_cand_dict(c) for c in cands]
    valid = {c.name for c in cands}
    system = (
        "You are the AIMS Tool Advisor for a builder tool warehouse. Given a builder's GOAL and a list "
        "of CANDIDATE tools, recommend which tools to integrate, WHERE in their stack/architecture, and WHY. "
        "Recommend ONLY tools from the candidate list, using their exact names. Strongly prefer certified tools. "
        'Respond with STRICT JSON only: {"recommendations":[{"tool":"<exact name>","where":"<where it fits>",'
        '"why":"<one sentence>","priority":"high|medium|low"}]}. '
        f"At most {limit} recommendations, best first."
    )
    user = f"GOAL:\n{goal}\n\nCANDIDATE TOOLS (JSON):\n{json.dumps(catalog)}"
    async with httpx.AsyncClient(timeout=45) as client:
        resp = await client.post(
            _OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://warehouse.aimanagedsolutions.cloud",
                "X-Title": "AIMS Tool Advisor",
            },
            json={
                "model": model,
                "temperature": 0.2,
                "max_tokens": 1200,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            },
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
    recs = [r for r in _parse_json(content).get("recommendations", []) if r.get("tool") in valid][:limit]
    # annotate certified flag from the catalog truth
    cert = {c.name: bool(c.selectable) for c in cands}
    for r in recs:
        r["certified"] = cert.get(r.get("tool"), False)
    return {"goal": goal, "advisor": "llm", "model": model, "considered": len(cands), "recommendations": recs}


def _parse_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"```$", "", text.strip()).strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return {}
        return {}
