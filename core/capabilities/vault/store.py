"""
core/capabilities/vault/store.py — the encrypted store on disk.

    %LOCALAPPDATA%\\Tessa\\vault.json          the store (JSON; every secret is a DPAPI blob)
    %LOCALAPPDATA%\\Tessa\\vault.json.tmp      atomic-write staging, same ACL, never left behind
    %LOCALAPPDATA%\\Tessa\\vault.json.lock     O_EXCL sidecar, same discipline as audit.py

Outside the repo by construction (the repo is public). Collides with nothing
already in that directory (VAULT-DESIGN.md §1.3): browser-profiles\\,
console-settings.json(.premigrate), logs\\, orb-theme.json, orb-window.json,
runtime.json, screenshots\\.

WHAT IS IN THE CLEAR, AND WHY THAT IS FINE

Per record: accountId, kind, label, origin, allowedCapabilities, meta (the
profile name, a login path), timestamps, `quarantined`, and the base64 DPAPI
blob. NOT in the clear: the secret, and a password record's username. The
policy fields are ALSO inside the blob, so editing the plaintext copy to widen
an allowlist is detected at use and the record is quarantined (§6.4).

THE ACL DISCIPLINE IS core/security/runtime.py's, COPIED NOT PARAPHRASED:
create empty -> lock the ACL -> read it back and refuse if any broad principal
remains -> only then write. Applied to the .tmp before a byte lands in it, and
re-verified on the final file after `os.replace`. The directory's own ACL
(which includes Administrators by default) is never relied on.

POWER CUTS: write to .tmp, fsync, `os.replace`. A torn .tmp is discarded on the
next open; the previous vault.json is intact. The owner is in Lagos; this is
not hypothetical.

TAMPER EVIDENCE: `storeHash` (sha256 over the canonical records array) is
recomputed on load and refused on mismatch, and the vault writes it into the
audit chain on every store/remove, so the chain commits to the vault's shape.
"""

from __future__ import annotations

import contextlib
import getpass
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterator

VERSION = 1
STORE_FILENAME = "vault.json"
MODE_A = "dpapi"
MODE_B = "dpapi+passphrase"
MODES = (MODE_A, MODE_B)

#: VAULT-DESIGN.md §6.2, with the lifecycle numbers decided in the build round:
#: idle relock after 30 min without a use, absolute relock 12 h after unlock.
DEFAULT_POLICY: dict[str, Any] = {
    "preferTokens": True,
    "allowPasswordFallback": True,
    "unlockTtlS": 43200,
    "relockOnSessionLock": True,
    "relockOnIdleS": 1800,
    "autoUnlockAtStart": False,
    "lockGatesSessions": False,
}

_LOCK_TIMEOUT_S = 5.0
_LOCK_RETRY_S = 0.01


class StoreError(RuntimeError):
    """The file cannot be trusted or cannot be secured. The vault fails closed on it."""


def default_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = str(Path.home() / ".local" / "share")
    return Path(base) / "Tessa" / STORE_FILENAME


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def store_hash(records: list[dict[str, Any]]) -> str:
    return hashlib.sha256(canonical(records)).hexdigest()


def new_doc(now_iso: str) -> dict[str, Any]:
    return {
        "version": VERSION,
        "mode": MODE_A,
        "kdf": None,
        "policy": dict(DEFAULT_POLICY),
        "verifier": None,
        "createdAt": now_iso,
        "updatedAt": now_iso,
        "records": [],
        "storeHash": store_hash([]),
    }


# ── ACL — the runtime.py discipline ──────────────────────────────────────────

_FORBIDDEN_PRINCIPALS = (
    "Everyone",
    "BUILTIN\\Users",
    "Authenticated Users",
    "AUTORITE NT\\Utilisateurs authentifi",
    "NT AUTHORITY\\Authenticated Users",
    "BUILTIN\\Administrators",
)


def lock_acl(path: Path) -> None:
    if sys.platform != "win32":
        path.chmod(0o600)
        return
    user = os.environ.get("USERNAME") or getpass.getuser()
    result = subprocess.run(
        ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F", "/grant:r", "SYSTEM:F"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise StoreError(f"failed to restrict ACL on {path.name}: "
                         f"{result.stderr.strip() or result.stdout.strip()}")


def acl_is_locked_down(path: Path) -> tuple[bool, str]:
    if sys.platform != "win32":
        mode = path.stat().st_mode & 0o777
        return (mode & 0o077) == 0, f"mode {oct(mode)}"
    result = subprocess.run(["icacls", str(path)], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return False, f"could not read ACL: {result.stderr.strip()}"
    for principal in _FORBIDDEN_PRINCIPALS:
        if principal in result.stdout:
            return False, f"{principal} still has access to {path.name}"
    return True, ""


def secure_empty_file(path: Path) -> None:
    """create empty -> lock -> verify. Nothing secret is written before this returns."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    path.touch(mode=0o600, exist_ok=False)
    lock_acl(path)
    ok, why = acl_is_locked_down(path)
    if not ok:
        with contextlib.suppress(OSError):
            path.unlink()
        raise StoreError(f"refusing to write {path.name}: {why}")


@contextlib.contextmanager
def exclusive(path: Path) -> Iterator[None]:
    """Cross-process exclusive lock for one write — the audit.py sidecar pattern."""
    lock_path = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + _LOCK_TIMEOUT_S
    fd = None
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except (FileExistsError, PermissionError):
            if time.monotonic() >= deadline:
                with contextlib.suppress(OSError):
                    os.unlink(str(lock_path))
                continue
            time.sleep(_LOCK_RETRY_S)
    try:
        yield
    finally:
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)
        with contextlib.suppress(OSError):
            os.unlink(str(lock_path))


# ── the store ────────────────────────────────────────────────────────────────

class VaultStore:
    def __init__(self, path: Path, *, acl: bool = True) -> None:
        self.path = Path(path)
        self.acl = acl

    @property
    def tmp_path(self) -> Path:
        return self.path.with_name(self.path.name + ".tmp")

    def exists(self) -> bool:
        return self.path.exists() and self.path.stat().st_size > 0

    def load(self, now_iso: str) -> dict[str, Any]:
        """The document, or a fresh one if nothing is on disk. Raises StoreError if untrustworthy."""
        # A torn .tmp from a power cut is discarded; the real file is intact by construction.
        with contextlib.suppress(OSError):
            if self.tmp_path.exists():
                self.tmp_path.unlink()
        if not self.exists():
            return new_doc(now_iso)
        if self.acl:
            ok, why = acl_is_locked_down(self.path)
            if not ok:
                # Try once to restore it; refuse if it will not take.
                lock_acl(self.path)
                ok, why = acl_is_locked_down(self.path)
                if not ok:
                    raise StoreError(f"vault file ACL is too broad: {why}")
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StoreError(f"vault file unreadable: {type(exc).__name__}") from None
        self._validate(doc)
        return doc

    @staticmethod
    def _validate(doc: Any) -> None:
        if not isinstance(doc, dict):
            raise StoreError("vault file is not an object")
        if doc.get("version") != VERSION:
            raise StoreError(f"vault file version {doc.get('version')!r} is not {VERSION}")
        if doc.get("mode") not in MODES:
            raise StoreError(f"vault file mode {doc.get('mode')!r} is unknown")
        records = doc.get("records")
        if not isinstance(records, list):
            raise StoreError("vault file records is not a list")
        for rec in records:
            if not isinstance(rec, dict) or not isinstance(rec.get("accountId"), str) \
                    or not isinstance(rec.get("blob"), str):
                raise StoreError("vault file has a malformed record")
        if doc.get("storeHash") != store_hash(records):
            raise StoreError("vault file storeHash does not match its records (tampered or torn)")
        if doc["mode"] == MODE_B and (not doc.get("kdf") or not doc.get("verifier")):
            raise StoreError("passphrase mode without kdf/verifier")
        policy = doc.get("policy")
        if not isinstance(policy, dict):
            raise StoreError("vault file policy is not an object")
        for key, default in DEFAULT_POLICY.items():
            policy.setdefault(key, default)

    def save(self, doc: dict[str, Any], now_iso: str) -> str:
        """Atomic, ACL-locked write. Returns the new storeHash."""
        doc["updatedAt"] = now_iso
        doc["storeHash"] = store_hash(doc["records"])
        self._validate(doc)
        body = json.dumps(doc, indent=2, ensure_ascii=False).encode("utf-8")
        with exclusive(self.path):
            tmp = self.tmp_path
            if self.acl:
                secure_empty_file(tmp)
            else:
                tmp.parent.mkdir(parents=True, exist_ok=True)
            with tmp.open("wb") as fh:
                fh.write(body)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
            if self.acl:
                ok, why = acl_is_locked_down(self.path)
                if not ok:
                    lock_acl(self.path)
                    ok, why = acl_is_locked_down(self.path)
                    if not ok:
                        raise StoreError(f"ACL not retained after write: {why}")
        return doc["storeHash"]
