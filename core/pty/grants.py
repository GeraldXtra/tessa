"""
core/pty/grants.py — the PTY grant lifecycle.

CONTRACT §6.5: "No PTY session may be created without a grant. A grant
authorizes ONE session, in ONE directory, and it expires."

Before this module existed the daemon issued `expiresAt: now_iso()` — literally
the current instant, already expired on arrival — stored the grant in a plain
dict, and never read it again. Nothing consumed a grant, nothing enforced a
deadline, and the same grantId would have been honoured forever. That is a
rubber stamp, not an authorization.

Four properties this module actually enforces:

  1. **A grant expires.** Real TTL, checked on redemption. The window is
     deliberately short — a grant authorizes an imminent spawn, not a standing
     capability to open shells later.

  2. **A grant is single-use.** Redeeming it marks it consumed. A replayed
     grantId is refused and audited, because a replay means either a bug or an
     attempt to open a second shell on one approval.

  3. **A grant is reclaimable.** `startFailed` releases it. This is precisely
     why that enum value was added pre-approval: without it a PTY that never
     came up would strand its grant, and the daemon's view of what is running
     would drift from reality.

  4. **A session is traceable to its grant.** The registry maps sessionId back
     to the grant that authorized it, so `evt.pty.revoke` and the audit trail
     both have something real to point at.

This module holds NO PTY bytes and spawns nothing. It is pure bookkeeping —
the Console owns the byte stream (CONTRACT §4.2); the daemon owns permission.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator, Literal

Actor = Literal["human", "agent", "schedule"]

# A grant authorizes an IMMINENT spawn. 30 s is generous for
# `utilityProcess.fork` + `node-pty.spawn` (measured at ~85 ms for the worker
# probe plus spawn) while being far too short to be useful to anything that
# obtained a grantId and sat on it.
DEFAULT_TTL_S = 30.0


class RedeemResult(str, Enum):
    OK = "ok"
    UNKNOWN = "unknown"      # no such grant — never issued, or already reclaimed
    EXPIRED = "expired"      # issued, but the window closed
    ALREADY_USED = "used"    # replay: this grant already backs a session


@dataclass
class Grant:
    grant_id: str
    session_id: str
    cwd: str
    profile_id: str
    actor: Actor
    tier: str
    issued_at: float          # time.monotonic()
    expires_at: float         # time.monotonic()
    redeemed_at: float | None = None
    # Which surface connection asked. Used so a revoke can be aimed, and so a
    # dropped connection's unredeemed grants can be swept.
    owner: str = ""

    @property
    def redeemed(self) -> bool:
        return self.redeemed_at is not None

    def is_expired(self, now: float | None = None) -> bool:
        return (now if now is not None else time.monotonic()) >= self.expires_at

    def remaining_s(self, now: float | None = None) -> float:
        return max(0.0, self.expires_at - (now if now is not None else time.monotonic()))


@dataclass
class Session:
    """A PTY the Console has told us actually started."""

    session_id: str
    grant_id: str
    profile_id: str
    cwd: str
    actor: Actor
    started_at: str           # ISO, for the wire
    title: str = ""
    busy: bool = False
    pid: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class GrantRegistry:
    """
    Grants and the sessions they authorize.

    Deliberately not thread-safe: the daemon is single-threaded asyncio, and
    adding a lock here would imply a concurrency model that does not exist.
    """

    def __init__(self, ttl_s: float = DEFAULT_TTL_S) -> None:
        self.ttl_s = ttl_s
        self._grants: dict[str, Grant] = {}
        self._sessions: dict[str, Session] = {}
        # sessionId -> grantId, so a report can find its grant without a scan.
        self._session_grant: dict[str, str] = {}

    # ── issuing ──────────────────────────────────────────────────────────────

    def issue(
        self,
        *,
        grant_id: str,
        session_id: str,
        cwd: str,
        profile_id: str,
        actor: Actor,
        tier: str,
        owner: str = "",
    ) -> Grant:
        now = time.monotonic()
        grant = Grant(
            grant_id=grant_id,
            session_id=session_id,
            cwd=cwd,
            profile_id=profile_id,
            actor=actor,
            tier=tier,
            issued_at=now,
            expires_at=now + self.ttl_s,
            owner=owner,
        )
        self._grants[grant_id] = grant
        self._session_grant[session_id] = grant_id
        return grant

    # ── redeeming ────────────────────────────────────────────────────────────

    def redeem_by_session(self, session_id: str) -> tuple[RedeemResult, Grant | None]:
        """
        Consume the grant backing `session_id`.

        Called when the Console reports `started`. This is the moment the
        invariant is actually enforced: a `started` for a session with no live
        grant means a PTY exists that the daemon never authorized.
        """
        grant_id = self._session_grant.get(session_id)
        if grant_id is None:
            return RedeemResult.UNKNOWN, None

        grant = self._grants.get(grant_id)
        if grant is None:
            return RedeemResult.UNKNOWN, None
        if grant.redeemed:
            return RedeemResult.ALREADY_USED, grant
        if grant.is_expired():
            return RedeemResult.EXPIRED, grant

        grant.redeemed_at = time.monotonic()
        return RedeemResult.OK, grant

    # ── reclaiming ───────────────────────────────────────────────────────────

    def reclaim(self, session_id: str) -> Grant | None:
        """
        Release the grant for a session that never came up (`startFailed`).

        The grant is dropped outright rather than merely marked: it cannot be
        reused for a retry, which would let one approval back two spawn
        attempts. A retry asks again.
        """
        grant_id = self._session_grant.pop(session_id, None)
        if grant_id is None:
            return None
        self._sessions.pop(session_id, None)
        return self._grants.pop(grant_id, None)

    def sweep_expired(self) -> list[Grant]:
        """Drop unredeemed grants past their deadline. Returns what was dropped."""
        now = time.monotonic()
        dropped: list[Grant] = []
        for gid in list(self._grants):
            g = self._grants[gid]
            if not g.redeemed and g.is_expired(now):
                dropped.append(g)
                self._grants.pop(gid, None)
                self._session_grant.pop(g.session_id, None)
        return dropped

    def drop_owner(self, owner: str) -> list[Grant]:
        """
        Release every UNREDEEMED grant belonging to a disconnected connection.

        A surface that asked for a grant and then died must not leave a live
        authorization behind. Redeemed grants are kept — their session may still
        be running and still needs to be revocable.
        """
        dropped: list[Grant] = []
        for gid in list(self._grants):
            g = self._grants[gid]
            if g.owner == owner and not g.redeemed:
                dropped.append(g)
                self._grants.pop(gid, None)
                self._session_grant.pop(g.session_id, None)
        return dropped

    # ── sessions ─────────────────────────────────────────────────────────────

    def add_session(self, session: Session) -> None:
        self._sessions[session.session_id] = session

    def get_session(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def end_session(self, session_id: str) -> Session | None:
        """A session that exited or was killed. Its grant goes with it."""
        grant_id = self._session_grant.pop(session_id, None)
        if grant_id:
            self._grants.pop(grant_id, None)
        return self._sessions.pop(session_id, None)

    def grant_for_session(self, session_id: str) -> Grant | None:
        gid = self._session_grant.get(session_id)
        return self._grants.get(gid) if gid else None

    def sessions(self) -> Iterator[Session]:
        return iter(self._sessions.values())

    def wire_sessions(self) -> list[dict[str, Any]]:
        """`evt.pty.sessions` payload shape (CONTRACT §4.2)."""
        return [
            {
                "sessionId": s.session_id,
                "profileId": s.profile_id,
                "cwd": s.cwd,
                "title": s.title,
                "startedAt": s.started_at,
                "busy": s.busy,
            }
            for s in self._sessions.values()
        ]

    # ── introspection, for tests and the audit trail ─────────────────────────

    @property
    def live_grants(self) -> int:
        return len(self._grants)

    @property
    def live_sessions(self) -> int:
        return len(self._sessions)

    def get_grant(self, grant_id: str) -> Grant | None:
        return self._grants.get(grant_id)
