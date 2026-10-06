"""
core/capabilities/vault/kdf.py — the vault master passphrase becomes DPAPI entropy.

    key = scrypt(passphrase, salt, n=2^15, r=8, p=1, dklen=32)

MEASURED on the owner's i5-7200U (VAULT-DESIGN.md §1.4 P4): n=2^14 80 ms,
n=2^15 159 ms, n=2^16 466 ms. 2^15 is chosen: over 100 ms per guess, invisible
to a human typing once per unlock. `maxmem` MUST be passed — CPython's default
(32 MiB) is fractionally too small for these parameters and the call raises.

THE PASSPHRASE IS NEVER STORED, NEVER HASHED-AND-STORED, NEVER COMPARED. It is
verified by ATTEMPTING TO DECRYPT (store.py's verifier blob and every secret
blob carry the derived key as DPAPI entropy); a wrong passphrase is a
`DpapiError`, not a mismatch. The passphrase bytes are zeroed here as soon as
the key exists.

HONEST LIMIT: `hashlib.scrypt` returns an immutable `bytes`. It is copied into
a bytearray (which the vault zeroes on lock) and the bytes object is dropped,
but CPython does not zero freed memory. The window is the lifetime of one
temporary in this function.
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Any

SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1
DKLEN = 32
SALT_BYTES = 16
#: 256 MiB ceiling; n=2^15, r=8 needs ~32 MiB plus overhead.
MAXMEM = 256 * 1024 * 1024


def new_salt() -> bytes:
    return secrets.token_bytes(SALT_BYTES)


def zero(buf: Any) -> None:
    """Overwrite a bytearray in place. Anything else (None, an immutable) is left alone —
    this runs in `finally` blocks and must never mask the real exception."""
    if isinstance(buf, bytearray) and len(buf):
        buf[:] = b"\x00" * len(buf)


def derive(passphrase: bytearray, salt: bytes, *, n: int = SCRYPT_N, r: int = SCRYPT_R,
           p: int = SCRYPT_P, dklen: int = DKLEN) -> bytearray:
    """
    Derive the master key and ZERO the passphrase bytearray on the way out.

    Takes a bytearray on purpose: a `str` passphrase cannot be zeroed, so the
    caller is made to convert first and gets the zeroing for free.
    """
    if not isinstance(passphrase, bytearray):
        raise TypeError("passphrase must be a bytearray (so it can be zeroed)")
    if len(passphrase) == 0:
        raise ValueError("empty passphrase")
    if len(salt) < SALT_BYTES:
        raise ValueError("salt too short")
    try:
        raw = hashlib.scrypt(passphrase, salt=salt, n=n, r=r, p=p, maxmem=MAXMEM, dklen=dklen)
        key = bytearray(raw)
        del raw
        return key
    finally:
        zero(passphrase)


def params() -> dict[str, int | str]:
    return {"name": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P, "dklen": DKLEN}
