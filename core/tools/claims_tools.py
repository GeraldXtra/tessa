"""
core/tools/claims_tools.py — the owner's three verbs over the claim store.

    recall    "what have you heard about X"   -> two labelled halves, spoken
    confirm   "confirm that claim"            -> UNVERIFIED -> VERIFIED, owner only
    reject    "that claim is false"           -> UNVERIFIED -> REJECTED, owner only

THERE IS NO `note` VERB HERE, AND THAT IS DELIBERATE. Noting happens in the
executor, after the fence, on every `x.read_*` result — a tweet is noted
because it was READ, not because anything asked for it to be remembered. A
"remember this" tool would be a tool a post could name.

THE OWNER GATE IS BY SIGNATURE. `confirm` and `reject` declare `provenance`,
so `Executor._with_provenance` hands them the call's RESOLVED origin — the
same value the card and the chain carry, stripped from args before dispatch.
A model-built call, a scheduled one, or a post saying "confirm it" all arrive
here as something other than "human" and are refused, audited as FAILED under
their true actor. The store refuses a second time underneath (ClaimRefused),
so a future caller that bypasses this file still cannot promote.

All three are GREEN: nothing here can act, reach outside the machine, or
write anywhere but data/memory/claims.json. See core/brain/claims.py.
"""

from __future__ import annotations

from typing import Any

from core.brain import claims as _claims

from .base import ToolError


def _store() -> _claims.ClaimStore:
    store = _claims.active()
    if store is None:
        raise ToolError("my claim memory is not loaded",
                        "That needs the daemon's memory, and nothing was changed.")
    return store


def recall(topic: str = "") -> dict[str, Any]:
    """GREEN. What he said and what strangers claimed, labelled, never merged."""
    store = _store()
    topic = str(topic or "").strip()
    found = store.recall(topic, _claims.active_conversation())
    return {"topic": topic or "that", "n": len(found.claims),
            "known": len(found.known), "spoken": found.spoken()}


def _owner_only(provenance: str, verb: str) -> None:
    if provenance != _claims.OWNER:
        raise ToolError(
            f"only you can {verb} a claim, and this did not come from you, it came "
            f"from {provenance}",
            "Say it yourself and I will mark it.")


def _one(store: _claims.ClaimStore, topic: str) -> _claims.Claim:
    hits = store.resolve(topic)
    if not hits:
        raise ToolError(
            f"I have no claim {'about ' + topic if topic else 'in front of me'}",
            "Ask me what I have heard about it first, then say which one.")
    if len(hits) > 1:
        who = ", ".join(c.who for c in hits[:4])
        raise ToolError(f"{len(hits)} claims match, from {who}",
                        "Name the topic more exactly, or ask me to list them.")
    return hits[0]


def _verdict(topic: str, status: str, provenance: str, verb: str) -> dict[str, Any]:
    _owner_only(provenance, verb)
    store = _store()
    topic = str(topic or "").strip()
    claim = _one(store, topic)
    try:
        claim = store.verdict(claim.id, status, provenance=provenance)
    except _claims.ClaimRefused as refused:
        raise ToolError(str(refused), "Say it yourself and I will mark it.") from None
    return {"claim": claim.claim[:120].rstrip(". "), "who": claim.who,
            "status": claim.status, "topic": topic or claim.claim[:40]}


def confirm(topic: str = "", provenance: str = "schedule") -> dict[str, Any]:
    """GREEN, OWNER ONLY. The one path from UNVERIFIED to VERIFIED."""
    return _verdict(topic, _claims.VERIFIED, provenance, "confirm")


def reject(topic: str = "", provenance: str = "schedule") -> dict[str, Any]:
    """GREEN, OWNER ONLY. Mark a claim false; it is remembered as his call."""
    return _verdict(topic, _claims.REJECTED, provenance, "reject")
