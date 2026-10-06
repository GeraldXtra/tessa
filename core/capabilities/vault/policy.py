"""
core/capabilities/vault/policy.py — credential USE is bound to the approval tiers.

VAULT-DESIGN.md §8. "Secret safe" (handle.py) and "session safe" (this file)
are different guarantees. A poisoned instruction that never steals a secret can
still ask the daemon to USE an authenticated session for a harmful act. This
module is why that fails: the ACTION carries the tier, and the vault re-checks
the tier, the initiator, the fence and the allowlist at the moment of use.

WHERE THE FACTS COME FROM — AND WHY THE MODEL CANNOT FORGE THEM

The executor (core/brain/executor.py, Session 1's file, NOT imported here)
strips `provenance`, `origin`, `_approved_by_surface` and `confirmed` out of a
tool call's args before dispatch, and then injects the RESOLVED values into a
handler BY SIGNATURE: `provenance=<the call's origin>` on every dispatch, and
`_approved_by_surface=True` only from `execute_approved` — the card path. A
model's arguments never reach either. `ActionContext.from_handler()` is built
from exactly those injected parameters, so a consumer handler that declares
them gets an unforgeable statement of who asked and whether the card said yes.

`Minter` is the executor-side alternative from the design (§8.2) for the day
Session 1 wires it: same dataclass, plus a nonce the vault spends.

NO IMPORT OF core.brain OR core.tools HERE. The executor will import THIS
module; this module must never import back (circular-import direction, §0.3).
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

KINDS = ("browser-session", "token", "password")
SECRET_KINDS = ("token", "password")
TIERS = ("green", "amber", "red")
ACTORS = ("human", "agent", "schedule")

#: The standing ruling: the X password is NEVER stored, under any toggle.
X_HOSTS = ("x.com", "twitter.com")

#: A context is single-use and short-lived — mirrors settings.yaml pty.grant_ttl_s.
CONTEXT_TTL_S = 30.0


class VaultRefused(Exception):
    """A vault operation refused. `reason` is a short machine token for the audit line."""

    def __init__(self, reason: str, message: str = "", **detail: Any) -> None:
        self.reason = reason
        self.message = message or reason
        self.detail = detail
        super().__init__(self.message)


class VaultKindRefused(VaultRefused):
    """A record of this kind/origin may never be stored (e.g. the X password)."""


class PassphraseRequired(VaultRefused):
    """The hybrid A->B upgrade: storing the first real secret needs a passphrase."""

    def __init__(self) -> None:
        super().__init__("passphrase-required",
                         "storing a token or password needs the vault master passphrase "
                         "to be set first (the vault upgrades from DPAPI-only)")


def actor_of(value: Any) -> str:
    """Unrecognised -> `schedule`, the most restrictive actor in guard.py."""
    return value if value in ACTORS else "schedule"


def tier_of(value: Any) -> str:
    """Unrecognised -> `red`, the most restrictive tier."""
    return value if value in TIERS else "red"


def host_of(origin: str) -> str:
    try:
        return (urlsplit(origin).hostname or "").lower()
    except ValueError:
        return ""


def is_x_host(host: str) -> bool:
    return any(host == h or host.endswith("." + h) for h in X_HOSTS)


def same_origin(expected: str, actual_url: str) -> bool:
    """Exact scheme + host + port. A suffix match is not an origin match."""
    a, b = urlsplit(expected), urlsplit(actual_url)
    return (a.scheme.lower(), (a.hostname or "").lower(), a.port) == \
           (b.scheme.lower(), (b.hostname or "").lower(), b.port)


@dataclass(frozen=True)
class ActionContext:
    """Proof that a tool dispatch passed the executor's gates before asking for a secret."""

    tool: str
    capability: str
    tier: str
    provenance: str
    approved: bool = False               # True ONLY when _approved_by_surface was injected
    request_id: str | None = None
    approved_over_fence: bool = False
    external_in_context: int = 0
    minted_at: float = field(default_factory=time.monotonic)
    nonce: bytes = field(default_factory=lambda: secrets.token_bytes(16), repr=False)
    source: str = "handler"              # "handler" (from_handler) | "executor" (Minter)

    def __repr__(self) -> str:           # the nonce never appears in any text
        return (f"ActionContext(tool={self.tool!r}, capability={self.capability!r}, "
                f"tier={self.tier!r}, provenance={self.provenance!r}, approved={self.approved}, "
                f"request_id={self.request_id!r}, source={self.source!r})")

    @classmethod
    def from_handler(cls, *, tool: str, tier: str, capability: str,
                     provenance: Any = "schedule", _approved_by_surface: Any = False,
                     external_in_context: int = 0, clock: Any = time.monotonic) -> "ActionContext":
        """
        Build from the parameters the EXECUTOR injects into a consumer handler.

        `provenance` and `_approved_by_surface` must be the handler's own
        parameters, filled by core/brain/executor.py — never read from a dict a
        model could have written.
        """
        return cls(tool=str(tool), capability=str(capability), tier=tier_of(tier),
                   provenance=actor_of(provenance), approved=bool(_approved_by_surface),
                   external_in_context=int(external_in_context or 0),
                   minted_at=float(clock()), source="handler")


class Minter:
    """
    Executor-side minting (VAULT-DESIGN.md §8.2/§10.5), for when Session 1 wires it.
    The vault spends nonces so a context cannot be replayed.
    """

    def __init__(self, clock: Any = time.monotonic) -> None:
        self._clock = clock

    def mint(self, *, tool: str, capability: str, tier: str, provenance: str,
             request_id: str | None = None, approved_over_fence: bool = False,
             external_in_context: int = 0) -> ActionContext:
        return ActionContext(tool=tool, capability=capability, tier=tier_of(tier),
                             provenance=actor_of(provenance), approved=request_id is not None,
                             request_id=request_id, approved_over_fence=approved_over_fence,
                             external_in_context=external_in_context,
                             minted_at=float(self._clock()), source="executor")


def check_store(*, kind: str, origin: str, allow_password_fallback: bool) -> None:
    """The refusals that apply BEFORE anything is encrypted."""
    if kind not in KINDS:
        raise VaultKindRefused("kind", f"unknown kind {kind!r}; one of {KINDS}")
    host = host_of(origin)
    if not host:
        raise VaultRefused("origin", f"origin must be an absolute URL with a host, got {origin!r}")
    if kind == "password" and is_x_host(host):
        # The standing ruling. X is login-once-by-hand plus the SESSION.
        raise VaultKindRefused(
            "x-password",
            "the X password is never stored. Sign in once yourself in Tessa's browser "
            "profile and store the SESSION (kind browser-session) instead")
    if kind == "password" and not allow_password_fallback:
        raise VaultKindRefused("password-fallback-off",
                               "policy.allowPasswordFallback is false; store a token")


def check_use(*, record: dict[str, Any], ctx: Any, locked: bool,
              lock_gates_sessions: bool, now: float, spent: dict[bytes, float],
              ttl_s: float = CONTEXT_TTL_S) -> None:
    """
    VAULT-DESIGN.md §8.3, in order, all fail closed. Raises VaultRefused.

    `record` is the PLAINTEXT metadata (kind, allowedCapabilities, quarantined,
    expiresAt). The decrypted policy copy is compared by the caller afterwards
    (integrity) — this function decides whether decryption is even allowed.
    """
    kind = record.get("kind")
    if record.get("quarantined"):
        raise VaultRefused("quarantined", "record failed an integrity check; store it again")
    if locked and (kind in SECRET_KINDS or lock_gates_sessions):
        raise VaultRefused("locked", "the vault is locked")
    if not isinstance(ctx, ActionContext):
        raise VaultRefused("no-context", "credential use needs an ActionContext from the executor")
    if now - ctx.minted_at > ttl_s or now < ctx.minted_at - 1.0:
        raise VaultRefused("context-expired", "the action context is stale")
    if ctx.nonce in spent:
        raise VaultRefused("context-replayed", "the action context was already used")
    allowed = list(record.get("allowedCapabilities") or [])
    if ctx.capability not in allowed:
        raise VaultRefused("capability",
                           f"{ctx.tool} runs under {ctx.capability!r}, which this account "
                           f"does not allow ({allowed})")
    if ctx.tier == "red" and not ctx.approved:
        raise VaultRefused("unapproved-red",
                           f"{ctx.tool} is red-tier and has no approval from the card")
    if ctx.tier == "amber" and ctx.provenance != "human" and not ctx.approved:
        raise VaultRefused("unconfirmed-amber",
                           f"{ctx.tool} is amber-tier and was initiated by {ctx.provenance}, "
                           f"not by the owner")
    if ctx.tier in ("amber", "red") and ctx.external_in_context > 0 \
            and not (ctx.approved or ctx.approved_over_fence):
        raise VaultRefused("fence", "untrusted external content is in context")
    expires = record.get("expiresAt")
    if expires:
        raise_if_expired(expires)


def raise_if_expired(expires_at: str, *, wall_now_iso: str | None = None) -> None:
    from datetime import datetime, timezone

    try:
        exp = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
    except ValueError:
        raise VaultRefused("expired", f"unparseable expiresAt {expires_at!r}") from None
    now = (datetime.fromisoformat(wall_now_iso.replace("Z", "+00:00"))
           if wall_now_iso else datetime.now(timezone.utc))
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if now >= exp:
        raise VaultRefused("expired", f"credential expired at {expires_at}")
