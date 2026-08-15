"""
core/brain/approvals.py — the gate in front of every red-tier tool.

THE RULING THIS IMPLEMENTS

    A red tool that executes on voice confirmation alone is worse than one that
    does not exist, because it looks safe.

CONTRACT §6.4 says the daemon is the only authority on tiers and that surfaces
RENDER approval, they do not define it. §4.1 gives the actual approval surface:
`evt.permission.request` out, `cmd.permission.respond` in, carrying a decision
of `approve` or `deny` from a UI the owner can SEE — with the tool name, the
arguments, and the provenance in front of him.

A spoken "yes" is none of that. It has no requestId, no visible arguments, no
provenance display, and — the part that matters most on this machine — it comes
through a microphone that has produced "Alicoy" and "The game is over" from
ordinary sentences. Voice confirmation is a convenience, not a control.

SO: RED TOOLS DO NOT EXECUTE HERE. They raise a request, audit it, and stop.

WHAT THIS CHANGES ABOUT WORK ALREADY ON DISK, STATED PLAINLY

`fs.delete` and `shell.execute` were built LAST TURN with a voice-confirmation
hold that DID execute — say it twice and the folder went to the Recycle Bin.
That was wrong under this ruling and it is retro-fitted here. The `ToolHold`
path stays, because it is still exactly right for AMBER `proc.kill`: reversible,
instant, and killing a process he named by PID is not a public or destructive-
beyond-recovery act.

WHAT UNLOCKS RED

The approval UI. It is P5 and it is Session 2's. Until it exists, every red tool
in this daemon is fully built, fully tested, fully audited — and gated.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Callable

#: CONTRACT §4.1's `evt.permission.request` carries `expiresAt`. ZOEY_OS-spec §5
#: rule 5 puts the approval window at 30 minutes, after which the job becomes
#: `needsReview` rather than `failed` — nothing broke, nobody answered.
APPROVAL_WINDOW_S = 30 * 60.0


@dataclass
class PendingApproval:
    request_id: str
    tool: str
    args: dict[str, Any]
    tier: str
    provenance: str
    detail: str
    at: float = field(default_factory=time.monotonic)

    @property
    def expired(self) -> bool:
        return (time.monotonic() - self.at) > APPROVAL_WINDOW_S


class ApprovalGate:
    """
    Holds red-tier requests that no surface can answer yet.

    `on_request` is the daemon's broadcaster. It is OPTIONAL so the gate stays
    unit-testable, but when it is absent the request is still recorded and still
    refused — a missing surface must never become an open gate.
    """

    def __init__(self, on_request: Callable[[dict[str, Any]], None] | None = None) -> None:
        self._on_request = on_request
        self.pending: dict[str, PendingApproval] = {}

    def request(self, *, tool: str, args: dict[str, Any], tier: str,
                provenance: str = "human", detail: str = "") -> PendingApproval:
        req = PendingApproval(
            request_id=secrets.token_hex(8), tool=tool,
            # The token is never in here, but arguments can carry a path or a
            # command he would not want echoed to a log twice. They are recorded
            # once, on the request, and the audit layer redacts before write.
            args=dict(args), tier=tier, provenance=provenance, detail=detail,
        )
        self.pending[req.request_id] = req
        if self._on_request is not None:
            try:
                self._on_request({
                    "requestId": req.request_id,
                    "tier": tier,
                    "tool": tool,
                    "args": req.args,
                    # CONTRACT §6.2: provenance is REQUIRED, never optional.
                    "provenance": provenance,
                    "expiresAt": _iso_in(APPROVAL_WINDOW_S),
                })
            except Exception:  # noqa: BLE001
                # A broadcast failure must not become an execution.
                pass
        return req

    def sweep(self) -> list[PendingApproval]:
        gone = [r for r in self.pending.values() if r.expired]
        for r in gone:
            self.pending.pop(r.request_id, None)
        return gone


def _iso_in(seconds: float) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)) \
        .isoformat(timespec="milliseconds").replace("+00:00", "Z")


#: What she says. Named rather than inlined so every red tool refuses in the
#: same words, and so the sentence can be read without opening the executor.
#:
#: It has to do three things at once: refuse, explain that the refusal is
#: deliberate rather than a failure, and say what would change it. A refusal he
#: reads as a bug gets worked around; a refusal he understands gets respected.
def red_refusal(tool: str, detail: str) -> str:
    what = detail.strip().rstrip(".") if detail else tool
    return (f"I have it ready, Emperor — {what}. I am not doing it on your voice alone. "
            f"That one needs the approval card, and it does not exist yet. "
            f"It is logged and waiting.")
