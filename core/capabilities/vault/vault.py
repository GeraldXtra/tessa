"""
core/capabilities/vault/vault.py — the credential vault. VAULT-DESIGN.md, built.

THE TWO GUARANTEES, AS CODE

  1. THE MODEL NEVER SEES A SECRET, IN OR OUT.
     OUT: there is no `get`, `retrieve`, `reveal` or `export` on this class. A
     secret leaves the store only inside `use()`, wrapped in a `SecretHandle`
     that raises on every text route, handed to a CONSUMER callable inside the
     daemon, and zeroed the moment the consumer returns. What `use()` returns
     is a status dict that has been serialised and scanned for the secret; a
     consumer that puts the secret in its status gets a refusal back instead.
     An exception from the consumer is scrubbed of the live secret before it
     can reach the executor's "that failed" sentence.
     IN: `secure_store` / `secure_unlock` / `secure_set_passphrase` are the
     daemon side of the surface password-field path. They pop the secret out
     of the frame's payload dict FIRST, work on a bytearray, and zero it on
     every exit. Nothing here touches the conversation, the transcript, or
     the brain — this module imports neither.

  2. CREDENTIAL USE IS BOUND TO THE APPROVAL TIERS.
     `use()` demands an `ActionContext` built from the executor-injected
     `provenance` / `_approved_by_surface` parameters (policy.py) and re-checks
     the allowlist, the tier, the initiator, the fence and the nonce itself. A
     red use with no approval is refused here even if every other gate failed.

THE PASSPHRASE IS A SECRET TOO. Never stored, never hashed-and-stored, never
compared: verified by decrypting the verifier blob (kdf.py, store.py). Enters
through the same secure path as a credential. Exists as a bytearray only long
enough for scrypt, then zeroed.

LIFECYCLE (the owner must know this): the derived key lives in MEMORY ONLY and
dies with the process, so EVERY DAEMON RESTART — including the autostart at
login — leaves the vault LOCKED and the first secret use needs the passphrase
again. While unlocked, the key is ZEROED (not merely dropped) on: explicit
lock, 30 min without a use (`relockOnIdleS`), 12 h after unlock
(`unlockTtlS`), five consecutive refused uses, an integrity fault, or daemon
stop. In phase A (session references only) there is no key and no passphrase;
DPAPI alone protects the file.

HYBRID A->B: the vault opens in `dpapi` mode. The first `token` or `password`
store requires the passphrase (PassphraseRequired) and upgrades the store in
process: every secret blob re-protected with the derived key as DPAPI entropy,
verifier written, mode flipped. Session references are DPAPI-only in both
modes — the lock gates SECRET BYTES; a profile path on disk gains nothing from
a passphrase (VAULT-DESIGN.md §7.4).
"""

from __future__ import annotations

import base64
import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import dpapi as _dpapi
from . import kdf
from .dpapi import DpapiError
from .handle import ProfileRef, SecretExposure, SecretHandle, contains, scrub
from .policy import (ACTORS, SECRET_KINDS, ActionContext, PassphraseRequired,
                     VaultKindRefused, VaultRefused, actor_of, check_store,
                     check_use, tier_of)
from .store import (DEFAULT_POLICY, MODE_A, MODE_B, StoreError, VaultStore,
                    default_path, new_doc)

VERIFIER_PLAINTEXT = b"tessa-vault-verifier-v1"
INPUT_CHANNEL = "surface"
BACKOFF_CAP_S = 60.0
REFUSAL_BURST = 5
CONTEXT_PRUNE_S = 120.0
_ACCOUNT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class ConsumerError(RuntimeError):
    """A consumer failed. The message has been scrubbed of the live secret."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


class Vault:
    def __init__(self, *, path: str | Path | None = None, audit: Any = None,
                 clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], str] = now_iso, acl: bool = True,
                 profile_root: str | Path | None = None,
                 on_state: Callable[[dict[str, Any]], None] | None = None) -> None:
        self._path = Path(path) if path else default_path()
        self._store = VaultStore(self._path, acl=acl)
        self._audit = audit
        self._clock = clock
        self._wall = wall
        self._profile_root = Path(profile_root) if profile_root else self._path.parent / "browser-profiles"
        self.on_state = on_state
        self._lock = threading.RLock()
        self._key: bytearray | None = None
        self._unlocked = False
        self._unlocked_at = 0.0
        self._last_activity = 0.0
        self._spent: dict[bytes, float] = {}
        self._fail_count = 0
        self._next_attempt_at = 0.0
        self._refusals = 0
        self._broken: str | None = None

        ok, why = _dpapi.available()
        if not ok:
            self._broken = f"dpapi-unavailable: {why}"
        try:
            self._doc = self._store.load(self._wall())
        except StoreError as exc:
            self._broken = f"store: {exc}"
            self._doc = new_doc(self._wall())

        # LOCKED AT START, ALWAYS. The one documented exception is phase A with
        # autoUnlockAtStart, which exists for the overnight goal and is off by default.
        self._audit_entry("vault.lock", f"LOCKED at start ({self.mode})", "green",
                          {"reason": "start", "mode": self.mode,
                           "broken": self._broken or None}, actor="system")
        if self._broken:
            self._audit_entry("vault.integrity", f"vault unusable: {self._broken}", "red",
                              {"what": self._broken}, actor="system")
        elif self.mode == MODE_A and bool(self.policy.get("autoUnlockAtStart")):
            self._set_unlocked()
            self._audit_entry("vault.unlock", "auto-unlocked at start (phase A, policy)", "amber",
                              {"mode": self.mode, "surface": "autostart"}, actor="system")
        self._emit("start")

    # ── properties ───────────────────────────────────────────────────────────

    @property
    def path(self) -> Path:
        return self._path

    @property
    def mode(self) -> str:
        return str(self._doc.get("mode", MODE_A))

    @property
    def policy(self) -> dict[str, Any]:
        return dict(self._doc.get("policy") or DEFAULT_POLICY)

    @property
    def broken(self) -> str | None:
        return self._broken

    @property
    def locked(self) -> bool:
        with self._lock:
            self._maybe_relock()
            return not self._unlocked

    @property
    def key_buffer(self) -> bytearray | None:
        """The live derived key, or None. Exists so a proof can verify zeroing, nothing else."""
        return self._key

    @property
    def clock(self) -> Callable[[], float]:
        """The monotonic clock this vault judges context TTLs by. Consumers mint against it."""
        return self._clock

    # ── status / listing (metadata only) ─────────────────────────────────────

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._maybe_relock()
            now = self._clock()
            until = None
            if self._unlocked:
                ttl = int(self.policy.get("unlockTtlS") or 0)
                idle = int(self.policy.get("relockOnIdleS") or 0)
                remaining = []
                if ttl > 0:
                    remaining.append(ttl - (now - self._unlocked_at))
                if idle > 0:
                    remaining.append(idle - (now - self._last_activity))
                until = round(min(remaining), 1) if remaining else None
            return {
                "locked": not self._unlocked,
                "mode": self.mode,
                "accounts": len(self._doc["records"]),
                "secretAccounts": sum(1 for r in self._doc["records"] if r.get("kind") in SECRET_KINDS),
                "unlockedForS": until,
                "idleRelockS": int(self.policy.get("relockOnIdleS") or 0),
                "ttlS": int(self.policy.get("unlockTtlS") or 0),
                "lockGatesSessions": bool(self.policy.get("lockGatesSessions")),
                "inputChannel": INPUT_CHANNEL,
                "broken": self._broken,
                "kdf": (self._doc.get("kdf") or {}).get("name") if self.mode == MODE_B else None,
            }

    def list_accounts(self) -> list[dict[str, Any]]:
        with self._lock:
            out = []
            for rec in self._doc["records"]:
                out.append({
                    "accountId": rec["accountId"], "kind": rec["kind"], "label": rec.get("label", ""),
                    "origin": rec.get("origin", ""),
                    "allowedCapabilities": list(rec.get("allowedCapabilities") or []),
                    "createdAt": rec.get("createdAt"), "updatedAt": rec.get("updatedAt"),
                    "lastUsedAt": rec.get("lastUsedAt"), "expiresAt": rec.get("expiresAt"),
                    "quarantined": bool(rec.get("quarantined")),
                    "meta": {k: v for k, v in (rec.get("meta") or {}).items() if k != "username"},
                })
            return out

    # ── lock lifecycle ───────────────────────────────────────────────────────

    def unlock(self, passphrase: bytearray | None = None, *, surface: str = "unknown") -> dict[str, Any]:
        with self._lock:
            try:
                self._check_broken()
                self._require_audit()
                if self.mode == MODE_A:
                    self._set_unlocked()
                    self._audit_entry("vault.unlock", "UNLOCKED (phase A: DPAPI only, no passphrase)",
                                      "amber", {"mode": self.mode, "surface": surface,
                                                "ttlS": self.policy.get("unlockTtlS"),
                                                "idleS": self.policy.get("relockOnIdleS")})
                    self._emit("unlock")
                    return self.status()

                now = self._clock()
                if now < self._next_attempt_at:
                    wait = round(self._next_attempt_at - now, 1)
                    self._audit_entry("vault.unlock.failed", "unlock attempt during backoff", "red",
                                      {"mode": self.mode, "reason": "backoff", "retryAfterS": wait,
                                       "failuresInWindow": self._fail_count})
                    raise VaultRefused("unlock-backoff", f"try again in {wait} s", retryAfterS=wait)
                if passphrase is None or len(passphrase) == 0:
                    raise VaultRefused("passphrase-required", "this vault needs its passphrase")
                if not isinstance(passphrase, bytearray):
                    raise TypeError("passphrase must be a bytearray")
                key = kdf.derive(passphrase, self._salt(), **self._kdf_args())   # zeroes passphrase
                try:
                    self._verify_key(key)
                except DpapiError:
                    kdf.zero(key)
                    self._fail_count += 1
                    delay = min(BACKOFF_CAP_S, float(2 ** (self._fail_count - 1)))
                    self._next_attempt_at = now + delay
                    self._audit_entry("vault.unlock.failed", "wrong passphrase — DPAPI refused the verifier",
                                      "red", {"mode": self.mode, "reason": "verifier-refused",
                                              "failuresInWindow": self._fail_count,
                                              "retryAfterS": delay})
                    raise VaultRefused("unlock-failed", "that passphrase did not open the vault",
                                       retryAfterS=delay) from None
                if self._key is not None:
                    kdf.zero(self._key)
                self._key = key
                self._fail_count = 0
                self._next_attempt_at = 0.0
                self._set_unlocked()
                self._audit_entry("vault.unlock", "UNLOCKED with the passphrase", "amber",
                                  {"mode": self.mode, "surface": surface,
                                   "ttlS": self.policy.get("unlockTtlS"),
                                   "idleS": self.policy.get("relockOnIdleS")})
                self._emit("unlock")
                return self.status()
            finally:
                if passphrase is not None:
                    kdf.zero(passphrase)

    def lock(self, reason: str = "explicit", *, surface: str | None = None) -> dict[str, Any]:
        with self._lock:
            was = self._unlocked
            if self._key is not None:
                kdf.zero(self._key)            # explicit overwrite, not a dropped reference
            self._key = None
            self._unlocked = False
            self._spent.clear()
            if was:
                self._audit_entry("vault.lock", f"LOCKED ({reason})", "green",
                                  {"reason": reason, "mode": self.mode, "surface": surface},
                                  actor="human" if reason == "explicit" else "system")
                self._emit(reason)
            return self.status()

    def touch(self) -> None:
        self._last_activity = self._clock()

    def _set_unlocked(self) -> None:
        self._unlocked = True
        self._unlocked_at = self._last_activity = self._clock()
        self._refusals = 0

    def _maybe_relock(self) -> None:
        if not self._unlocked:
            return
        now = self._clock()
        idle = int(self.policy.get("relockOnIdleS") or 0)
        ttl = int(self.policy.get("unlockTtlS") or 0)
        if idle > 0 and now - self._last_activity > idle:
            self.lock("idle")
        elif ttl > 0 and now - self._unlocked_at > ttl:
            self.lock("ttl")

    # ── passphrase: set (A->B upgrade) or rotate (B->B) ──────────────────────

    def set_passphrase(self, passphrase: bytearray, *, surface: str = "unknown") -> dict[str, Any]:
        """
        Upgrade phase A -> B, or rotate the passphrase in B. Re-encrypts every
        secret blob IN PROCESS: decrypt under the old entropy, protect under the
        new. Nothing is re-entered. The vault is unlocked afterwards.
        """
        with self._lock:
            try:
                self._check_broken()
                self._require_audit()
                if not isinstance(passphrase, bytearray):
                    raise TypeError("passphrase must be a bytearray")
                if len(passphrase) < 1:
                    raise VaultRefused("passphrase-empty", "the passphrase is empty")
                old_mode = self.mode
                if old_mode == MODE_B:
                    self._maybe_relock()
                    if not self._unlocked or self._key is None:
                        raise VaultRefused("locked", "unlock the vault before changing its passphrase")
                salt = kdf.new_salt()
                new_key = kdf.derive(passphrase, salt)         # zeroes passphrase
                old_key = self._key
                new_records: list[dict[str, Any]] = []
                reencrypted = 0
                try:
                    for rec in self._doc["records"]:
                        if rec.get("kind") in SECRET_KINDS:
                            plain = _dpapi.unprotect(_unb64(rec["blob"]), entropy=old_key)
                            try:
                                blob = _dpapi.protect(plain, entropy=new_key)
                            finally:
                                kdf.zero(plain)
                            rec2 = dict(rec)
                            rec2["blob"] = _b64(blob)
                            new_records.append(rec2)
                            reencrypted += 1
                        else:
                            new_records.append(dict(rec))
                    verifier = _dpapi.protect(VERIFIER_PLAINTEXT, entropy=new_key)
                except DpapiError as exc:
                    kdf.zero(new_key)
                    self._audit_entry("vault.integrity", f"re-encrypt failed: {exc}", "red",
                                      {"what": "reencrypt", "mode": old_mode}, actor="system")
                    raise VaultRefused("integrity", "a record could not be re-encrypted; "
                                       "nothing was changed") from None
                doc2 = dict(self._doc)
                doc2["records"] = new_records
                doc2["mode"] = MODE_B
                doc2["kdf"] = dict(kdf.params(), salt=_b64(salt))
                doc2["verifier"] = _b64(verifier)
                store_hash = self._save(doc2)
                if old_key is not None:
                    kdf.zero(old_key)
                self._key = new_key
                self._fail_count = 0
                self._next_attempt_at = 0.0
                self._set_unlocked()
                self._audit_entry("vault.passphrase",
                                  "UPGRADED to passphrase mode" if old_mode == MODE_A
                                  else "ROTATED the passphrase",
                                  "red", {"mode": MODE_B, "upgraded": old_mode == MODE_A,
                                          "reencrypted": reencrypted, "surface": surface,
                                          "storeHash": store_hash})
                self._emit("passphrase")
                return self.status()
            finally:
                kdf.zero(passphrase)

    # ── store / remove / policy ──────────────────────────────────────────────

    def store(self, *, account_id: str, kind: str, label: str, origin: str,
              allowed_capabilities: list[str], meta: dict[str, Any] | None = None,
              secret: bytearray | None = None, username: str | None = None,
              expires_at: str | None = None, passphrase: bytearray | None = None,
              surface: str = "unknown", input_channel: str = INPUT_CHANNEL) -> dict[str, Any]:
        with self._lock:
            try:
                self._check_broken()
                self._require_audit()
                self._maybe_relock()
                if not isinstance(account_id, str) or not _ACCOUNT_ID.match(account_id):
                    raise VaultRefused("validation", "accountId must be 1-64 chars of [A-Za-z0-9._-]")
                if not isinstance(label, str):
                    raise VaultRefused("validation", "label must be a string")
                if not isinstance(origin, str):
                    raise VaultRefused("validation", "origin must be a string")
                allowed = sorted({str(c) for c in (allowed_capabilities or []) if str(c)})
                if not allowed:
                    raise VaultRefused("validation", "allowedCapabilities must name at least one capability")
                meta = dict(meta or {})
                if username is None and "username" in meta:
                    username = str(meta.pop("username"))
                check_store(kind=kind, origin=origin,
                            allow_password_fallback=bool(self.policy.get("allowPasswordFallback")))

                if kind in SECRET_KINDS:
                    if not isinstance(secret, bytearray) or len(secret) == 0:
                        raise VaultRefused("secret-required", f"a {kind} record needs its secret")
                    if self.mode == MODE_A:
                        if passphrase is None or len(passphrase) == 0:
                            raise PassphraseRequired()
                        self.set_passphrase(passphrase, surface=surface)   # upgrades + unlocks
                    elif not self._unlocked or self._key is None:
                        raise VaultRefused("locked", "unlock the vault before storing a secret")
                else:
                    if secret is not None:
                        raise VaultRefused("no-secret-for-session",
                                           "a browser-session record holds no secret bytes")
                    username = None

                header_obj = {"accountId": account_id, "kind": kind, "origin": origin,
                              "allowedCapabilities": allowed, "meta": meta}
                if kind == "password":
                    header_obj["username"] = username or ""
                header = json.dumps(header_obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
                plain = bytearray()
                plain += len(header).to_bytes(4, "big")
                plain += header
                if secret is not None:
                    plain += secret
                try:
                    blob = _dpapi.protect(plain, entropy=self._entropy_for(kind))
                finally:
                    kdf.zero(plain)

                now = self._wall()
                existing = self._find(account_id)
                record = {
                    "accountId": account_id, "kind": kind, "label": label, "origin": origin,
                    "allowedCapabilities": allowed, "meta": meta,
                    "createdAt": existing.get("createdAt", now) if existing else now,
                    "updatedAt": now, "lastUsedAt": None, "expiresAt": expires_at,
                    "quarantined": False, "blob": _b64(blob),
                }
                doc2 = dict(self._doc)
                doc2["records"] = [r for r in self._doc["records"] if r["accountId"] != account_id] + [record]
                store_hash = self._save(doc2)
                self.touch()
                self._audit_entry("vault.store", f"STORED {kind} {account_id}", "amber", {
                    "accountId": account_id, "kind": kind, "origin": origin,
                    "allowedCapabilities": allowed, "inputChannel": input_channel,
                    "replaced": existing is not None,
                    "fallback": kind == "password" and bool(self.policy.get("preferTokens")),
                    "storeHash": store_hash, "mode": self.mode, "surface": surface,
                })
                return {"ok": True, "accountId": account_id, "kind": kind,
                        "replaced": existing is not None, "mode": self.mode}
            finally:
                kdf.zero(secret)
                kdf.zero(passphrase)

    def remove(self, account_id: str, *, surface: str = "unknown") -> dict[str, Any]:
        with self._lock:
            self._check_broken()
            self._require_audit()
            rec = self._find(account_id)
            if rec is None:
                raise VaultRefused("notFound", f"no account {account_id!r}")
            doc2 = dict(self._doc)
            doc2["records"] = [r for r in self._doc["records"] if r["accountId"] != account_id]
            store_hash = self._save(doc2)
            self._audit_entry("vault.remove", f"REMOVED {rec['kind']} {account_id}", "amber",
                              {"accountId": account_id, "kind": rec["kind"],
                               "storeHash": store_hash, "surface": surface})
            return {"ok": True, "accountId": account_id, "kind": rec["kind"]}

    def set_policy(self, changes: dict[str, Any], *, surface: str = "unknown") -> dict[str, Any]:
        with self._lock:
            self._check_broken()
            self._require_audit()
            if self.mode == MODE_B:
                self._maybe_relock()
                if not self._unlocked:
                    raise VaultRefused("locked", "unlock the vault before changing its policy")
            applied: dict[str, Any] = {}
            policy = self.policy
            for key, value in dict(changes or {}).items():
                if key not in DEFAULT_POLICY:
                    raise VaultRefused("validation", f"unknown policy key {key!r}")
                want = type(DEFAULT_POLICY[key])
                if want is bool and not isinstance(value, bool):
                    raise VaultRefused("validation", f"{key} must be a boolean")
                if want is int and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                    raise VaultRefused("validation", f"{key} must be a non-negative integer")
                if policy.get(key) != value:
                    policy[key] = value
                    applied[key] = value
            if applied:
                doc2 = dict(self._doc)
                doc2["policy"] = policy
                self._save(doc2)
                self._audit_entry("vault.policy", f"POLICY changed: {', '.join(sorted(applied))}", "red",
                                  {"changed": applied, "surface": surface})
            return self.status()

    # ── USE: the only way a secret leaves the store ──────────────────────────

    def use(self, account_id: str, consumer: Callable[[Any, dict[str, Any]], Any],
            ctx: ActionContext) -> dict[str, Any]:
        """
        Hand the credential for `account_id` to `consumer(payload, info)` and return
        STATUS ONLY. `payload` is a SecretHandle (token/password) or a ProfileRef
        (browser-session). The handle is zeroed when the consumer returns.
        """
        with self._lock:
            self._check_broken()
            self._require_audit()
            self._maybe_relock()
            rec = self._find(account_id) if isinstance(account_id, str) else None
            if rec is None:
                refusal = VaultRefused("notFound", f"no account {account_id!r}")
                self._refuse(None, ctx, refusal, account_id=str(account_id))
                raise refusal
            try:
                check_use(record=rec, ctx=ctx, locked=not self._unlocked,
                          lock_gates_sessions=bool(self.policy.get("lockGatesSessions")),
                          now=self._clock(), spent=self._spent)
            except VaultRefused as refusal:
                self._refuse(rec, ctx, refusal)
                raise
            kind = rec["kind"]
            if kind in SECRET_KINDS and self.mode == MODE_B and self._key is None:
                refusal = VaultRefused("locked", "the vault is locked")
                self._refuse(rec, ctx, refusal)
                raise refusal

            # Spend the context now: a second use with the same nonce is a replay.
            self._spent[ctx.nonce] = self._clock()
            self._prune_spent()

            # INTENT BEFORE ACT. If this entry cannot be written the consumer is never called.
            self._audit_entry("vault.use", f"USING {kind} {account_id} for {ctx.tool}", ctx.tier, {
                "accountId": account_id, "kind": kind, "tool": ctx.tool, "capability": ctx.capability,
                "tier": ctx.tier, "provenance": ctx.provenance, "approvedBySurface": ctx.approved,
                "requestId": ctx.request_id, "approvedOverFence": ctx.approved_over_fence,
                "contextSource": ctx.source,
            }, actor=ctx.provenance, required=True)

            # Decrypt. Session references are DPAPI-only; secret kinds carry the entropy.
            try:
                plain = _dpapi.unprotect(_unb64(rec["blob"]), entropy=self._entropy_for(kind))
            except DpapiError as exc:
                self._quarantine(account_id, f"dpapi refused: {exc}")
                refusal = VaultRefused("integrity", "the record could not be decrypted and was quarantined")
                self._refuse(rec, ctx, refusal)
                raise refusal from None
            try:
                n = int.from_bytes(bytes(plain[:4]), "big")
                header = json.loads(bytes(plain[4:4 + n]).decode("utf-8"))
                secret_buf = plain[4 + n:]          # a bytearray slice is already a fresh bytearray
            except (ValueError, UnicodeDecodeError, IndexError):
                kdf.zero(plain)
                self._quarantine(account_id, "malformed plaintext")
                refusal = VaultRefused("integrity", "the record is malformed and was quarantined")
                self._refuse(rec, ctx, refusal)
                raise refusal from None
            kdf.zero(plain)

            # Integrity: the policy inside the blob must match the plaintext copy.
            if (header.get("accountId") != rec["accountId"] or header.get("kind") != rec["kind"]
                    or header.get("origin") != rec.get("origin")
                    or sorted(header.get("allowedCapabilities") or []) != sorted(rec.get("allowedCapabilities") or [])):
                kdf.zero(secret_buf)
                self._quarantine(account_id, "plaintext policy differs from the encrypted copy")
                refusal = VaultRefused("integrity", "the record's policy was tampered with and it was quarantined")
                self._refuse(rec, ctx, refusal)
                raise refusal
            if kind in SECRET_KINDS and len(secret_buf) == 0:
                self._quarantine(account_id, "empty secret")
                refusal = VaultRefused("integrity", "the record holds no secret and was quarantined")
                self._refuse(rec, ctx, refusal)
                raise refusal

            info = {"accountId": account_id, "kind": kind, "label": rec.get("label", ""),
                    "origin": rec.get("origin", ""), "meta": dict(header.get("meta") or {}),
                    "allowedCapabilities": list(rec.get("allowedCapabilities") or []),
                    "username": header.get("username") if kind == "password" else None}
            handle: SecretHandle | None = None
            payload: Any
            if kind in SECRET_KINDS:
                handle = SecretHandle(secret_buf, account_id=account_id, kind=kind)
                payload = handle
            else:
                kdf.zero(secret_buf)
                profile = str((header.get("meta") or {}).get("profile") or account_id)
                payload = ProfileRef(account_id=account_id, path=str(self._profile_root / profile),
                                     origin=rec.get("origin", ""), meta=dict(header.get("meta") or {}))

            refusal = None
            error: str | None = None
            leaked = False
            status_text = "{}"
            try:
                raw_status = consumer(payload, info)
                status_text = self._serialise(raw_status)
                if handle is not None and contains(status_text, secret_buf):
                    leaked = True
            except VaultRefused as r:
                refusal = r
            except SecretExposure as exc:
                error = f"SecretExposure: {exc}"
            except Exception as exc:  # noqa: BLE001
                text = f"{type(exc).__name__}: {exc}"
                error = scrub(text, secret_buf) if handle is not None else text
            finally:
                if handle is not None:
                    handle.zero()                      # same buffer as secret_buf: zeroed once, here

            if refusal is not None:
                self._refuse(rec, ctx, refusal)
                raise refusal
            if error is not None:
                self._audit_entry("vault.use", f"USE-FAILED {kind} {account_id} for {ctx.tool}: {error[:200]}",
                                  ctx.tier, {"accountId": account_id, "kind": kind, "tool": ctx.tool,
                                             "ok": False, "error": error[:400]}, actor=ctx.provenance)
                raise ConsumerError(error)
            if leaked:
                self._audit_entry("vault.refused", f"consumer of {account_id} put the secret in its status — dropped",
                                  "red", {"accountId": account_id, "tool": ctx.tool, "reason": "secret-in-status"},
                                  actor=ctx.provenance)
                self._refusals += 1
                return {"ok": False, "accountId": account_id, "kind": kind, "tool": ctx.tool,
                        "reason": "secret-in-status"}

            self._refusals = 0
            self.touch()
            self._stamp_used(account_id)
            return {"ok": True, "accountId": account_id, "kind": kind, "tool": ctx.tool,
                    "capability": ctx.capability, "tier": ctx.tier, "status": json.loads(status_text)}

    def mark_verified(self, account_id: str) -> None:
        """A consumer saw the session alive (e.g. the auth cookie). Metadata only."""
        with self._lock:
            rec = self._find(account_id)
            if rec is None:
                return
            rec.setdefault("meta", {})["lastVerifiedAt"] = self._wall()
            self._save(dict(self._doc))

    # ── secure input: the daemon side of the surface password-field path ────

    @staticmethod
    def _take_secret(payload: dict[str, Any], key: str) -> bytearray | None:
        """Pop a secret-bearing field OUT of the frame's dict, into a zeroable buffer."""
        if not isinstance(payload, dict) or key not in payload:
            return None
        value = payload.pop(key)
        if value is None:
            return None
        if isinstance(value, str):
            buf = bytearray(value.encode("utf-8"))
        elif isinstance(value, (bytes, bytearray)):
            buf = bytearray(value)
        else:
            raise VaultRefused("validation", f"{key} must be a string")
        del value
        return buf

    def secure_store(self, payload: dict[str, Any], *, surface: str = "unknown") -> dict[str, Any]:
        secret = self._take_secret(payload, "secret")
        passphrase = self._take_secret(payload, "passphrase")
        try:
            meta = dict(payload.get("meta") or {}) if isinstance(payload.get("meta"), dict) else {}
            username = payload.get("username")
            caps = payload.get("allowedCapabilities")
            if not isinstance(caps, list):
                raise VaultRefused("validation", "allowedCapabilities must be a list")
            return self.store(
                account_id=str(payload.get("accountId") or ""), kind=str(payload.get("kind") or ""),
                label=str(payload.get("label") or ""), origin=str(payload.get("origin") or ""),
                allowed_capabilities=[str(c) for c in caps], meta=meta, secret=secret,
                username=str(username) if isinstance(username, str) else None,
                expires_at=payload.get("expiresAt") if isinstance(payload.get("expiresAt"), str) else None,
                passphrase=passphrase, surface=surface, input_channel=INPUT_CHANNEL)
        finally:
            kdf.zero(secret)
            kdf.zero(passphrase)

    def secure_unlock(self, payload: dict[str, Any], *, surface: str = "unknown") -> dict[str, Any]:
        passphrase = self._take_secret(payload, "passphrase")
        try:
            return self.unlock(passphrase, surface=surface)
        finally:
            kdf.zero(passphrase)

    def secure_set_passphrase(self, payload: dict[str, Any], *, surface: str = "unknown") -> dict[str, Any]:
        passphrase = self._take_secret(payload, "passphrase")
        try:
            if passphrase is None:
                raise VaultRefused("passphrase-required", "no passphrase in the request")
            return self.set_passphrase(passphrase, surface=surface)
        finally:
            kdf.zero(passphrase)

    # ── internals ────────────────────────────────────────────────────────────

    def _find(self, account_id: str) -> dict[str, Any] | None:
        for rec in self._doc["records"]:
            if rec.get("accountId") == account_id:
                return rec
        return None

    def _entropy_for(self, kind: str) -> bytearray | None:
        if kind in SECRET_KINDS and self.mode == MODE_B:
            if self._key is None:
                raise VaultRefused("locked", "the vault is locked")
            return self._key
        return None

    def _salt(self) -> bytes:
        return _unb64(str((self._doc.get("kdf") or {}).get("salt") or ""))

    def _kdf_args(self) -> dict[str, int]:
        k = self._doc.get("kdf") or {}
        return {"n": int(k.get("n", kdf.SCRYPT_N)), "r": int(k.get("r", kdf.SCRYPT_R)),
                "p": int(k.get("p", kdf.SCRYPT_P)), "dklen": int(k.get("dklen", kdf.DKLEN))}

    def _verify_key(self, key: bytearray) -> None:
        """Verify by DECRYPTING, never by comparing to anything stored."""
        verifier = self._doc.get("verifier")
        if not verifier:
            raise DpapiError("verify", detail="no verifier blob")
        plain = _dpapi.unprotect(_unb64(str(verifier)), entropy=key)
        try:
            if bytes(plain) != VERIFIER_PLAINTEXT:
                raise DpapiError("verify", detail="verifier mismatch")
        finally:
            kdf.zero(plain)

    def _save(self, doc2: dict[str, Any]) -> str:
        try:
            store_hash = self._store.save(doc2, self._wall())
        except StoreError as exc:
            self._broken = f"store: {exc}"
            self._audit_entry("vault.integrity", f"store write refused: {exc}", "red",
                              {"what": str(exc)}, actor="system")
            self.lock("integrity")
            raise VaultRefused("integrity", "the vault file could not be written safely") from None
        self._doc = doc2
        return store_hash

    def _stamp_used(self, account_id: str) -> None:
        rec = self._find(account_id)
        if rec is not None:
            rec["lastUsedAt"] = self._wall()
            try:
                self._save(dict(self._doc))
            except VaultRefused:
                pass    # the use already happened and is audited; a stamp is not worth a second refusal

    def _quarantine(self, account_id: str, what: str) -> None:
        rec = self._find(account_id)
        if rec is not None:
            rec["quarantined"] = True
            try:
                self._save(dict(self._doc))
            except VaultRefused:
                pass
        self._audit_entry("vault.integrity", f"QUARANTINED {account_id}: {what}", "red",
                          {"accountId": account_id, "what": what}, actor="system")

    def _refuse(self, rec: dict[str, Any] | None, ctx: Any, refusal: VaultRefused, *,
                account_id: str | None = None) -> None:
        tool = getattr(ctx, "tool", None)
        tier = tier_of(getattr(ctx, "tier", "red"))
        actor = actor_of(getattr(ctx, "provenance", "schedule"))
        self._audit_entry("vault.refused",
                          f"REFUSED {tool or '?'} on {(rec or {}).get('accountId', account_id)}: {refusal.reason}",
                          tier, {"accountId": (rec or {}).get("accountId", account_id), "tool": tool,
                                 "capability": getattr(ctx, "capability", None), "reason": refusal.reason},
                          actor=actor)
        self._refusals += 1
        if self._refusals >= REFUSAL_BURST and self._unlocked:
            self.lock("refusalBurst")

    def _prune_spent(self) -> None:
        now = self._clock()
        for nonce, at in list(self._spent.items()):
            if now - at > CONTEXT_PRUNE_S:
                self._spent.pop(nonce, None)

    def _check_broken(self) -> None:
        if self._broken:
            raise VaultRefused("integrity", f"the vault is unusable: {self._broken}")

    def _require_audit(self) -> None:
        if self._audit is None:
            raise VaultRefused("no-audit", "the vault refuses to operate without the audit chain")

    @staticmethod
    def _serialise(obj: Any) -> str:
        def refuse(o: Any) -> Any:
            raise TypeError(f"{type(o).__name__} is not a status value")
        return json.dumps(obj if obj is not None else {}, ensure_ascii=False, default=refuse)

    def _audit_entry(self, tool: str, summary: str, tier: str, detail: dict[str, Any], *,
                     actor: str = "human", required: bool = False) -> None:
        """Identifiers only. The secret is never a variable in any caller's scope here."""
        if self._audit is None:
            if required:
                raise VaultRefused("no-audit", "the vault refuses to operate without the audit chain")
            return
        try:
            self._audit.append(actor=actor_of(actor) if actor != "system" else "system",
                               tool=tool, summary=summary, tier=tier if tier in ("green", "amber", "red") else "none",
                               detail=dict(detail), provenance=actor)
        except Exception as exc:  # noqa: BLE001
            if required:
                raise VaultRefused("no-audit", f"audit append failed: {type(exc).__name__}") from None

    def _emit(self, reason: str) -> None:
        if self.on_state is None:
            return
        try:
            self.on_state({"locked": not self._unlocked, "reason": reason, "mode": self.mode,
                           "accounts": len(self._doc["records"])})
        except Exception:  # noqa: BLE001
            pass
