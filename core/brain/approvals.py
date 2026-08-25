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

#: CONTRACT §4.1's `evt.permission.request` carries `expiresAt`. TESSA_CORE-spec §5
#: rule 5 puts the approval window at 30 minutes, after which the job becomes
#: `needsReview` rather than `failed` — nothing broke, nobody answered.
APPROVAL_WINDOW_S = 30 * 60.0


#: How many bytes of edited arguments a surface may send back.
#:
#: CONTRACT §1 caps a FRAME at 1 MiB and `serve(max_size=...)` enforces it, so
#: the transport already refuses anything larger — but 1 MiB is a cap on
#: WebSocket abuse, not a sane bound on a tweet. 16 KB is ~60x the longest thing
#: any red tool accepts (a 280-character post, a shell line, a path) and small
#: enough that a hostile surface cannot use the approval path to grow the
#: daemon's memory. Over it, the decision is REFUSED and the request stays
#: pending — never truncated, because a silently shortened tweet is a wrong
#: tweet that looks approved.
MAX_EDITED_ARGS_BYTES = 16 * 1024


class ApprovalError(ValueError):
    """A decision frame that cannot be honoured. Carries the wire error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class PendingApproval:
    request_id: str
    tool: str
    args: dict[str, Any]
    tier: str
    provenance: str
    detail: str
    at: float = field(default_factory=time.monotonic)
    #: True when untrusted external content was in context at request time.
    #: Recorded so the audit shows it and so the decision can be judged against
    #: what she was looking at — see `resolve_edit` and the fence ruling.
    external_at_request: bool = False

    @property
    def expired(self) -> bool:
        return (time.monotonic() - self.at) > APPROVAL_WINDOW_S


def resolve_edit(pending: PendingApproval, edited: Any) -> dict[str, Any]:
    """
    Merge a surface's edited arguments onto a pending request, or refuse.

    THIS FUNCTION IS THE SECURITY BOUNDARY OF THE WHOLE APPROVAL PATH. An
    editable payload is a route from a surface into a red-tier execution, so
    everything that is NOT an argument is taken from the stored request and
    never from the frame:

      * THE TOOL IS NOT READABLE FROM THE WIRE. There is no code path here that
        reads a tool name out of `edited`. "Approve this tweet" cannot become
        "approve this shell command", not because a check rejects it but because
        nothing ever looks.
      * THE TIER IS NOT READABLE EITHER, for the same reason. Tiers are
        permissions.yaml's alone (CONTRACT §6.4).
      * KEYS ARE A SUBSET OF THE ORIGINAL. A surface may correct a value he can
        see; it may not introduce a parameter that was never in the request. An
        unexpected keyword reaching a handler is how `x.post(text=...)` would
        become `x.post(text=..., _approved_by_surface=True)`.
      * TYPES MUST MATCH the original argument. A string stays a string.
      * SIZE IS BOUNDED, and over the bound is a refusal rather than a
        truncation.

    Returns the argument dict to execute. Raises `ApprovalError` otherwise.
    """
    if edited is None:
        return dict(pending.args)

    if not isinstance(edited, dict):
        raise ApprovalError("protocol.badEnvelope",
                            "editedArgs must be an object")

    import json as _json

    try:
        size = len(_json.dumps(edited, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        raise ApprovalError("protocol.badEnvelope",
                            "editedArgs is not JSON-serialisable") from None
    if size > MAX_EDITED_ARGS_BYTES:
        raise ApprovalError(
            "protocol.badEnvelope",
            f"editedArgs is {size} bytes, over the {MAX_EDITED_ARGS_BYTES} limit")

    unknown = sorted(set(edited) - set(pending.args))
    if unknown:
        raise ApprovalError(
            "protocol.badEnvelope",
            f"editedArgs may only change existing arguments; {unknown} were not "
            f"in the request")

    merged = dict(pending.args)
    for key, value in edited.items():
        original = pending.args.get(key)
        # `bool` before `int`: in Python `isinstance(True, int)` is True, so a
        # bare int check would let `confirmed: true` through as a number.
        if original is not None and type(original) is not type(value):
            raise ApprovalError(
                "protocol.badEnvelope",
                f"editedArgs['{key}'] is {type(value).__name__}, "
                f"the request had {type(original).__name__}")
        merged[key] = value
    return merged


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

    #: How many requests may be outstanding at once.
    #:
    #: BOUNDED FOR THE SAME REASON THE EDITED PAYLOAD IS. Every red-tier
    #: utterance creates a PendingApproval that lives thirty minutes, and
    #: nothing was capping them — say "tweet that" forty times and forty
    #: records sit in memory holding forty argument dicts. It is not a large
    #: leak and it is still an unbounded one, in a daemon that holds his
    #: microphone.
    #:
    #: 32 is far more than he can generate deliberately and small enough that
    #: the card list stays readable. Over it, the OLDEST is dropped: the newest
    #: request is the one he is looking at, and a thirty-minute-old approval he
    #: never answered is one he has moved on from.
    MAX_PENDING = 32

    def request(self, *, tool: str, args: dict[str, Any], tier: str,
                provenance: str = "human", detail: str = "") -> PendingApproval:
        # 128 BITS, NOT A ULID, and this is where I differ from Session 2's
        # proposal. A ULID is the right shape for an envelope `id` — sortable,
        # timestamped, and CONTRACT §3 already mandates one there. It is the
        # wrong shape for THIS, because a requestId is a capability handle for a
        # red-tier execution: its timestamp prefix leaks when the request was
        # made, and its 80 bits of randomness are chosen for collision
        # resistance rather than for being unguessable.
        #
        # `token_hex(16)` is 128 bits from the CSPRNG, and it was 64 — doubled,
        # because the cost is sixteen characters on the wire and the property
        # asked for was "unguessable enough that one surface cannot decide
        # another's request by accident."
        req = PendingApproval(
            request_id=secrets.token_hex(16), tool=tool,
            # The token is never in here, but arguments can carry a path or a
            # command he would not want echoed to a log twice. They are recorded
            # once, on the request, and the audit layer redacts before write.
            args=dict(args), tier=tier, provenance=provenance, detail=detail,
        )
        self.pending[req.request_id] = req
        # Sweep the dead, then bound the living. Oldest out.
        self.sweep()
        while len(self.pending) > self.MAX_PENDING:
            oldest = min(self.pending.values(), key=lambda r: r.at)
            self.pending.pop(oldest.request_id, None)
        if self._on_request is not None:
            try:
                self._on_request({
                    "requestId": req.request_id,
                    "tier": tier,
                    "tool": tool,
                    "args": req.args,
                    # CONTRACT §6.2: provenance is REQUIRED, never optional.
                    "provenance": provenance,
                    "expiresAt": iso_in(APPROVAL_WINDOW_S),
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

    def sweep_and_list(self) -> list[PendingApproval]:
        """
        The live ones, oldest first, with the dead removed on the way past.

        WHAT HAPPENS WHEN THE REQUESTING SURFACE DISCONNECTS: nothing. The
        request is NOT bound to the socket that caused it. CONTRACT §4.1 says
        either surface may render the approval card, so tying a pending action
        to one connection would mean the Console could not answer a request the
        Orb raised — and would also mean a reconnect silently destroyed a
        decision he was in the middle of making.

        It therefore survives the disconnect and lives until one of three
        things: he decides it, the 30-minute window closes (§5 rule 5), or the
        daemon stops. It is never orphaned, because nothing owns it but the
        clock.
        """
        self.sweep()
        return sorted(self.pending.values(), key=lambda r: r.at)


def iso_in(seconds: float) -> str:
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
    """
    THE OLD VERSION SAID "the approval card does not exist yet", AND THAT
    BECAME FALSE.

    It was true when written — the card was P5 and unbuilt. Session 2 has since
    shipped `ApprovalCard.tsx`, the Orb subscribes to `permission.*`, and this
    daemon now answers `cmd.permission.respond`. The broadcast fires from
    `ApprovalGate.request` BEFORE this sentence is returned, so the card is
    already on his screen at the moment Piper tells him the mechanism is
    unbuilt.

    A control the owner has been told is not real is a control he will not use.
    Found by an adversarial review reading both sides of the repo, which is
    exactly the kind of drift a single session cannot see.
    """
    what = detail.strip().rstrip(".") if detail else tool
    return (f"I have it ready, Emperor — {what}. I am not doing it on your voice alone. "
            f"Check it on the card and approve it there. You can correct the wording "
            f"before it goes.")
