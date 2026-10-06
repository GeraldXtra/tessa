"""
core/capabilities/vault/ws.py — the daemon side of `cmd.vault.*`.

Every handler has core/server.py's signature `(ws, state, payload, corr)` and
is PURE DELEGATION: Session 1's proposed diff (HOOK-PROPOSAL.md) maps the
seven `cmd.vault.*` types to these methods and nothing else. Nothing here
imports core.server, core.brain or core.tools; the two daemon callables the
vault needs — `envelope()` and the async `broadcast()` — are injected.

THE SECRET-BEARING FIELDS NEVER SURVIVE THE HANDLER. `h_store`, `h_unlock` and
`h_setPassphrase` hand the payload dict to the vault's `secure_*` entry
points, which pop `secret` / `passphrase` out into zeroable buffers before
doing anything else. No handler logs a frame, echoes a request field back, or
puts a payload into an audit `detail`.

The scrypt derivation (159 ms measured) and DPAPI calls run in
`asyncio.to_thread`, so the heartbeat and the sphere keep going.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from .policy import PassphraseRequired, VaultKindRefused, VaultRefused
from .vault import Vault

Envelope = Callable[..., str]
Broadcast = Callable[[str, dict[str, Any]], Awaitable[None]]

#: VaultRefused.reason -> err.* code (CONTRACT §5.4 ErrorCode is an OPEN set).
_CODE_FOR_REASON = {
    "locked": "vault.locked",
    "unlock-failed": "vault.unlockFailed",
    "unlock-backoff": "vault.unlockFailed",
    "passphrase-required": "vault.passphraseRequired",
    "notFound": "vault.notFound",
    "validation": "protocol.badEnvelope",
    "integrity": "vault.integrity",
    "no-audit": "vault.integrity",
}


class VaultWs:
    def __init__(self, vault: Vault, *, envelope: Envelope, broadcast: Broadcast) -> None:
        self.vault = vault
        self._envelope = envelope
        self._broadcast = broadcast
        self._loop: asyncio.AbstractEventLoop | None = None
        vault.on_state = self._on_state

    # ── evt.vault.state, from any thread ─────────────────────────────────────

    def _on_state(self, payload: dict[str, Any]) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(self._broadcast("evt.vault.state", dict(payload)), loop)
        except RuntimeError:
            pass

    def _note_loop(self) -> None:
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

    # ── replies ──────────────────────────────────────────────────────────────

    async def _ok(self, ws: Any, corr: str, mtype: str, payload: dict[str, Any]) -> None:
        await ws.send(self._envelope(mtype, payload, corr=corr))

    async def _err(self, ws: Any, corr: str, code: str, message: str, *,
                   retryable: bool = False, **extra: Any) -> None:
        body = {"code": code, "message": message, "retryable": retryable}
        body.update({k: v for k, v in extra.items() if v is not None})
        await ws.send(self._envelope(f"err.{code}", body, corr=corr))

    async def _refused(self, ws: Any, corr: str, exc: VaultRefused) -> None:
        if isinstance(exc, PassphraseRequired):
            code = "vault.passphraseRequired"
        elif isinstance(exc, VaultKindRefused):
            code = "vault.kindRefused"
        else:
            code = _CODE_FOR_REASON.get(exc.reason, "vault.refused")
        retry_after = exc.detail.get("retryAfterS") if isinstance(exc.detail, dict) else None
        await self._err(ws, corr, code, exc.message, retryable=exc.reason == "unlock-backoff",
                        reason=exc.reason, retryAfterS=retry_after)

    # ── handlers ─────────────────────────────────────────────────────────────

    async def h_status(self, ws: Any, state: dict[str, Any], payload: dict[str, Any], corr: str) -> None:
        self._note_loop()
        await self._ok(ws, corr, "res.vault.status", self.vault.status())

    async def h_list(self, ws: Any, state: dict[str, Any], payload: dict[str, Any], corr: str) -> None:
        self._note_loop()
        await self._ok(ws, corr, "res.vault.list", {"accounts": self.vault.list_accounts()})

    async def h_unlock(self, ws: Any, state: dict[str, Any], payload: dict[str, Any], corr: str) -> None:
        self._note_loop()
        surface = str(state.get("surface") or "unknown")
        try:
            status = await asyncio.to_thread(self.vault.secure_unlock, payload, surface=surface)
        except VaultRefused as exc:
            await self._refused(ws, corr, exc)
            return
        await self._ok(ws, corr, "res.vault.state", {"locked": status["locked"], "mode": status["mode"],
                                                     "unlockedForS": status.get("unlockedForS")})

    async def h_lock(self, ws: Any, state: dict[str, Any], payload: dict[str, Any], corr: str) -> None:
        self._note_loop()
        surface = str(state.get("surface") or "unknown")
        status = self.vault.lock("explicit", surface=surface)
        await self._ok(ws, corr, "res.vault.state", {"locked": status["locked"], "mode": status["mode"]})

    async def h_store(self, ws: Any, state: dict[str, Any], payload: dict[str, Any], corr: str) -> None:
        self._note_loop()
        surface = str(state.get("surface") or "unknown")
        if not isinstance(payload, dict):
            await self._err(ws, corr, "protocol.badEnvelope", "payload must be an object")
            return
        try:
            result = await asyncio.to_thread(self.vault.secure_store, payload, surface=surface)
        except VaultRefused as exc:
            await self._refused(ws, corr, exc)
            return
        # Echoes NOTHING from the request beyond the id it was given.
        await self._ok(ws, corr, "res.vault.stored", {"accountId": result["accountId"], "kind": result["kind"],
                                                      "replaced": result["replaced"], "mode": result["mode"]})

    async def h_remove(self, ws: Any, state: dict[str, Any], payload: dict[str, Any], corr: str) -> None:
        self._note_loop()
        surface = str(state.get("surface") or "unknown")
        account_id = payload.get("accountId") if isinstance(payload, dict) else None
        if not isinstance(account_id, str) or not account_id:
            await self._err(ws, corr, "protocol.badEnvelope", "accountId is required")
            return
        try:
            await asyncio.to_thread(self.vault.remove, account_id, surface=surface)
        except VaultRefused as exc:
            await self._refused(ws, corr, exc)
            return
        await self._ok(ws, corr, "res.ok", {})

    async def h_set_policy(self, ws: Any, state: dict[str, Any], payload: dict[str, Any], corr: str) -> None:
        self._note_loop()
        surface = str(state.get("surface") or "unknown")
        if not isinstance(payload, dict):
            await self._err(ws, corr, "protocol.badEnvelope", "payload must be an object")
            return
        try:
            status = await asyncio.to_thread(self.vault.set_policy, payload, surface=surface)
        except VaultRefused as exc:
            await self._refused(ws, corr, exc)
            return
        await self._ok(ws, corr, "res.vault.status", status)

    async def h_set_passphrase(self, ws: Any, state: dict[str, Any], payload: dict[str, Any], corr: str) -> None:
        self._note_loop()
        surface = str(state.get("surface") or "unknown")
        if not isinstance(payload, dict):
            await self._err(ws, corr, "protocol.badEnvelope", "payload must be an object")
            return
        try:
            status = await asyncio.to_thread(self.vault.secure_set_passphrase, payload, surface=surface)
        except VaultRefused as exc:
            await self._refused(ws, corr, exc)
            return
        await self._ok(ws, corr, "res.vault.state", {"locked": status["locked"], "mode": status["mode"]})

    #: What Session 1's handler map should contain (HOOK-PROPOSAL.md). Names only.
    COMMANDS = {
        "cmd.vault.status": "h_status",
        "cmd.vault.list": "h_list",
        "cmd.vault.unlock": "h_unlock",
        "cmd.vault.lock": "h_lock",
        "cmd.vault.store": "h_store",
        "cmd.vault.remove": "h_remove",
        "cmd.vault.setPolicy": "h_set_policy",
        "cmd.vault.setPassphrase": "h_set_passphrase",
    }

    def handler_map(self) -> dict[str, Callable[..., Awaitable[None]]]:
        return {mtype: getattr(self, name) for mtype, name in self.COMMANDS.items()}
