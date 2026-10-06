"""
core/capabilities/vault/tools.py — how a registry tool becomes a vault CONSUMER.

This is the ONLY module in the vault package that imports from core.tools, and
it imports just `core.tools.base` (a leaf: ToolSpec, ToolError). It is kept
out of `core/capabilities/vault/__init__.py` so importing the vault never
pulls the tool registry in — the executor imports the vault, never the
reverse (circular-import direction, VAULT-DESIGN.md §0.3).

`bind_consumer` wraps a consumer function into a handler whose SIGNATURE
declares `provenance` and `_approved_by_surface`. core/brain/executor.py
injects those two by signature — `provenance` on every dispatch with the
call's resolved origin, `_approved_by_surface=True` only from
`execute_approved` (the card) — and strips the same keys out of a call's
args first. So a consumer handler learns who asked and whether the card said
yes from the executor alone, and builds its ActionContext from that.

No real consumer ships in this round (the browser drive is a later build).
`vault.status` is the one registry tool; a future `auth.login` is one
`bind_consumer` away.
"""

from __future__ import annotations

from typing import Any, Callable

from core.tools.base import ToolError, ToolSpec

from .policy import ActionContext, VaultRefused
from .vault import ConsumerError, Vault

Consumer = Callable[..., Any]


def bind_consumer(vault: Vault, *, tool: str, tier: str, capability: str) -> Callable[[Consumer], Callable[..., dict[str, Any]]]:
    """
    Decorator: `consumer(payload, info, **extra) -> status dict` becomes a
    registry handler `(accountId, provenance, _approved_by_surface, confirmed, **extra)`.
    """

    def decorate(consumer: Consumer) -> Callable[..., dict[str, Any]]:
        def handler(accountId: str = "", provenance: str = "schedule",  # noqa: N803 - the wire name
                    _approved_by_surface: bool = False, confirmed: bool = False,
                    **extra: Any) -> dict[str, Any]:
            ctx = ActionContext.from_handler(tool=tool, tier=tier, capability=capability,
                                             provenance=provenance,
                                             _approved_by_surface=_approved_by_surface,
                                             clock=vault.clock)
            try:
                return vault.use(str(accountId), lambda payload, info: consumer(payload, info, **extra), ctx)
            except VaultRefused as refusal:
                raise ToolError(f"the vault refused {tool} for {accountId or '?'}: {refusal.message}",
                                "Check the SENTINEL rail — the vault says why.") from None
            except ConsumerError as err:
                raise ToolError(str(err), "Tell me another way and I will try again.") from None

        handler.__name__ = f"vault_consumer_{tool.replace('.', '_')}"
        handler.__doc__ = f"Vault consumer for {tool} ({tier}, {capability})."
        return handler

    return decorate


def make_consumer_spec(vault: Vault, *, name: str, tier: str, capability: str, consumer: Consumer,
                       success: str, audit: str, phrasings: tuple[str, ...] = (),
                       failure: str = "That failed, sir. {reason} {alternative}",
                       holds: bool | None = None, note: str = "") -> ToolSpec:
    """A ToolSpec whose handler is a bound vault consumer. Red tools hold, per _validate()."""
    handler = bind_consumer(vault, tool=name, tier=tier, capability=capability)(consumer)
    return ToolSpec(name=name, tier=tier, capability=capability, handler=handler,
                    phrasings=phrasings, success=success, failure=failure,
                    holds=(tier == "red") if holds is None else holds, audit=audit, note=note)


def make_status_spec(vault: Vault) -> ToolSpec:
    """`vault.status` — green, metadata only. The one thing the model may ask the vault."""

    def handler() -> dict[str, Any]:
        st = vault.status()
        n = int(st["accounts"])
        return {"state": "locked" if st["locked"] else "unlocked", "accounts": n,
                "plural": "" if n == 1 else "s",
                "mode": "passphrase mode" if st["mode"] == "dpapi+passphrase" else "session-only mode",
                "broken": st.get("broken") or ""}

    return ToolSpec(
        name="vault.status", tier="green", capability="vault.status", handler=handler,
        phrasings=("is the vault locked", "vault status", "what's in the vault"),
        success="The vault is {state}, Emperor. {accounts} account{plural}, {mode}.",
        audit="vault status",
        note="Metadata only. There is no tool that returns a secret, and none can be added "
             "without a design change — see VAULT-DESIGN.md §5.",
    )


def specs(vault: Vault) -> list[ToolSpec]:
    """Everything Session 1's registry line should extend REGISTRY with."""
    return [make_status_spec(vault)]
