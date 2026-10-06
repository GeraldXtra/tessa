# HOOK-PROPOSAL.md — wiring `cmd.vault.*` into the daemon

**Status: PROPOSAL. NOT APPLIED.** Every file named below is Session 1's or the owner's.
Session 3 wrote the vault (`core/capabilities/vault/`) and this document; it edited nothing
else. The owner routes this to Session 1 (and the CONTRACT diff to himself).

Written 2026-09-06 against `core/server.py` as it stood that day (`KNOWN_COMMANDS` at
line ~124, the handler map in `_dispatch` at ~518, `res.hello.capabilities` at ~465, the
shutdown tail at ~2193). Line numbers are for orientation; the anchors are the code.

---

## 1. What the vault side already provides (built, in Session 3's territory)

`core/capabilities/vault/ws.py::VaultWs` — every handler has the daemon's own signature
`(ws, state, payload, corr)` and is pure delegation. `VaultWs.COMMANDS` is the authoritative
map (eight commands; §2 of VAULT-DESIGN.md listed seven, `cmd.vault.setPassphrase` was added
because the owner ruled the passphrase enters through the same secure path as a credential):

| Type | Handler | Payload → Response |
|---|---|---|
| `cmd.vault.status` | `h_status` | `{}` → `res.vault.status { locked, mode, accounts, secretAccounts, unlockedForS, idleRelockS, ttlS, lockGatesSessions, inputChannel, broken, kdf }` |
| `cmd.vault.list` | `h_list` | `{}` → `res.vault.list { accounts: [{ accountId, kind, label, origin, allowedCapabilities, createdAt, updatedAt, lastUsedAt, expiresAt, quarantined, meta }] }` — metadata only, by schema |
| `cmd.vault.unlock` | `h_unlock` | `{ passphrase? }` → `res.vault.state { locked, mode, unlockedForS }` or `err.vault.unlockFailed { reason, retryAfterS, retryable }` |
| `cmd.vault.lock` | `h_lock` | `{}` → `res.vault.state { locked, mode }` |
| `cmd.vault.store` | `h_store` | `{ accountId, kind, label, origin, allowedCapabilities[], meta?, username?, expiresAt?, secret?, passphrase? }` → `res.vault.stored { accountId, kind, replaced, mode }` — echoes nothing else |
| `cmd.vault.remove` | `h_remove` | `{ accountId }` → `res.ok {}` |
| `cmd.vault.setPolicy` | `h_set_policy` | `{ preferTokens?, allowPasswordFallback?, unlockTtlS?, relockOnSessionLock?, relockOnIdleS?, autoUnlockAtStart?, lockGatesSessions? }` → `res.vault.status` |
| `cmd.vault.setPassphrase` | `h_set_passphrase` | `{ passphrase }` → `res.vault.state { locked, mode }` |

Event (daemon → surfaces, broadcast): `evt.vault.state { locked, reason, mode, accounts }` on
every lock, unlock, upgrade and rotation.

Error codes (open set, CONTRACT §5.4): `vault.locked`, `vault.unlockFailed`, `vault.refused`,
`vault.passphraseRequired`, `vault.kindRefused`, `vault.notFound`, `vault.integrity`;
validation failures use the existing `protocol.badEnvelope`.

**What does not exist and must never be added:** `cmd.vault.get` / `retrieve` / `reveal` /
`export`. There is no response type in this protocol that can carry a secret.

`kind` ∈ `browser-session | token | password` and the error codes are strings validated by
the daemon, not new closed enums in `enums.json` — no `PROTOCOL_VERSION` bump anywhere in this
proposal (all additive under CONTRACT §7.2).

---

## 2. `core/server.py` — proposed diff (Session 1 applies)

```diff
@@ imports (after the existing core.* imports) @@
+from core.capabilities.vault import Vault as _Vault, VaultWs as _VaultWs  # noqa: E402

@@ KNOWN_COMMANDS @@
     "cmd.calendar.today",
+    # ADDITIVE under CONTRACT §7.2 — the vault's sub-namespace. See
+    # core/capabilities/HOOK-PROPOSAL.md; handlers live in core/capabilities/vault/ws.py.
+    "cmd.vault.status", "cmd.vault.list", "cmd.vault.unlock", "cmd.vault.lock",
+    "cmd.vault.store", "cmd.vault.remove", "cmd.vault.setPolicy", "cmd.vault.setPassphrase",
 })

@@ class TessaDaemon.__init__ (after self.audit = AuditLog(...)) @@
+        # The credential vault. Locked at start; the passphrase-derived key lives in
+        # memory only and dies with this process — every restart re-locks it.
+        self.vault = _Vault(audit=self.audit)
+        self.vault_ws = _VaultWs(self.vault, envelope=envelope, broadcast=self.broadcast)

@@ _dispatch handler map @@
             "cmd.calendar.today": self._h_calendar_today,
+            **self.vault_ws.handler_map(),
         }.get(mtype)

@@ res.hello @@
-            "capabilities": ["pty.grant", "fs.list", "audit.query", "permissions.tiers"],
+            "capabilities": ["pty.grant", "fs.list", "audit.query", "permissions.tiers", "vault"],

@@ shutdown tail (before the daemon.stop audit entry) @@
+    daemon.vault.lock("daemon.stop")     # zeroes the key; audited
```

Notes for the applier:

- `envelope` is the module-level function `server.py` already uses; `self.broadcast` is the
  existing async broadcaster. Nothing else is needed — the vault never touches the socket.
- `h_store`, `h_unlock`, `h_set_passphrase` **pop** `secret` / `passphrase` out of the payload
  dict before anything else runs, so the `msg` dict `_dispatch` still holds is scrubbed by the
  time the handler returns. The raw frame `str` in `_dispatch` cannot be zeroed; that is the
  documented residual of the surface path (VAULT-DESIGN.md §9.2, §12).
- **Never log a `cmd.vault.*` frame**, at any level. `server.py` logs none today; keep it so.
- `broadcast()` filters on subscriptions, so surfaces must subscribe to `vault.*` (§4).
- The vault must not be given a `Vault(path=...)` — the default is
  `%LOCALAPPDATA%\Tessa\vault.json`, ACL-locked to the owner + SYSTEM.

## 3. `core/config/permissions.yaml` + `core/tools/__init__.py` — proposed (Session 1 applies)

Only the two capabilities that registry TOOLS carry. The surface commands above are not
capabilities: the guard never sees them, the model cannot name them.

```yaml
# permissions.yaml
green:
  - vault.status          # "is the vault locked?" — metadata; the only vault TOOL the model can name
amber:
  - auth.login            # a future consumer: fill a stored password into ITS OWN origin, in ITS OWN profile
```

```diff
# core/tools/__init__.py — after REGISTRY is built, before _validate()
+from core.capabilities.vault.tools import specs as _vault_specs   # noqa: E402
+# `specs()` needs the daemon's Vault instance; register lazily from server.py instead:
```

Because `REGISTRY` is built at import time and the vault instance is created in
`TessaDaemon.__init__`, the cleanest wiring is one line in `server.py` after the vault exists:

```python
        from core.tools import REGISTRY as _REGISTRY
        from core.capabilities.vault.tools import specs as _vault_specs
        for _spec in _vault_specs(self.vault):
            _REGISTRY[_spec.name] = _spec        # vault.status (green). No red vault tool exists.
```

(`_validate()` has already run by then; `vault.status`'s capability must be in
`permissions.yaml` regardless, for `tier_of()` and the guard's classification.)

## 4. Surfaces — a later Console / Orb round (Session 1 / Session 2)

**The secure input path.** The owner ruled that credentials AND the vault passphrase enter
through a **surface password field → daemon**, never the chat box. Concretely:

- A fenced card on the Orb's SENTINEL rail (the `ApprovalCard.tsx` pattern: fixed chrome,
  payload box, no HTML) with `<input type="password" autocomplete="off">` held in React state
  and **cleared on submit**; never written to `localStorage`, a log, or the transcript.
- Preload exposes exactly: `vaultStatus()`, `vaultList()`, `vaultUnlock(passphrase?)`,
  `vaultLock()`, `vaultStore(meta, secret?, passphrase?)`, `vaultRemove(accountId)`,
  `vaultSetPassphrase(passphrase)`, `vaultSetPolicy(changes)`, `onVaultState(listener)` —
  functions only, re-validated in main, per both preloads' existing rule.
- Main builds the envelope and sends on the existing socket. **`cmd.vault.*` frames are never
  logged** — the Console's `cmd.pty.report` verbatim-log line is the shape to avoid.
- Add `vault.*` to each ws-client's `cmd.subscribe` `TOPICS` so `evt.vault.state` arrives and
  the padlock is right on both surfaces.
- Render: padlock state, account list (metadata), UNLOCK / LOCK / ADD / REMOVE, and the
  passphrase prompt on the A→B upgrade (`err.vault.passphraseRequired` → show the field).
  Type-only labels per §R.7; `NO DATA` when empty.

Until this lands the vault is reachable only from code; nothing the model can say opens it.

## 5. CONTRACT.md — proposed additive diff (the owner applies)

- §3.1: add the row **`vault.*` — SHARED**.
- §5.1: append the eight commands from §1 above with their payloads and responses.
- §4.1: append `evt.vault.state { locked, reason, mode, accounts }`.
- §5.4: note the seven `vault.*` error codes (documentation; `ErrorCode` is open).
- §8 changelog: one additive line. `PROTOCOL_VERSION` stays 1.

## 6. Deploy

The running daemon (pid per `runtime.json`) predates this code. The vault becomes active on
the **next daemon restart**, which the owner performs (Session 1 cannot — ancestry). After
**every** restart, including the autostart at login, the vault is locked and — once it holds a
token or password — the passphrase must be entered again on a surface. That is the cost of a
memory-only key and it is the intended trade.
