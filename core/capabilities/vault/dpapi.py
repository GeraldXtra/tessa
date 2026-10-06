"""
core/capabilities/vault/dpapi.py — Windows DPAPI, CurrentUser scope, via ctypes.

ZERO DEPENDENCIES BY DESIGN. pywin32 is not installed on the owner's machine and
the connection is metered; `crypt32.dll` is in-box on every supported Windows.
Measured on the owner's i5-7200U (VAULT-DESIGN.md §1.4 P3): protect 1.99 ms,
unprotect 0.44 ms, and — the load-bearing result — a blob protected with
optional entropy is REFUSED (WinError 13) when unprotected with the wrong
entropy or with none, even by the same user. That refusal is the mechanism
behind the vault master passphrase: the passphrase-derived key is passed as
`pOptionalEntropy`, so the OS itself will not decrypt without it.

SCOPE IS CURRENTUSER, NEVER LOCAL_MACHINE. `CRYPTPROTECT_LOCAL_MACHINE` would
let any account on the machine decrypt. The daemon runs as the owner
(core/security/identity.py refuses service accounts), so CurrentUser is both
correct and reachable.

FAIL CLOSED. Anything the OS refuses is a `DpapiError`, never a fallback to a
different scope, an empty key, or plaintext.

MEMORY: `unprotect` copies the plaintext into a `bytearray` (which the caller
can zero) and then zeroes and frees the OS-allocated buffer. `protect` reads a
writable buffer IN PLACE (no copy) when given a bytearray, so a caller can zero
its own plaintext afterwards and know no second copy was made here.
"""

from __future__ import annotations

import ctypes
import sys
from typing import Any

DESCRIPTION = "Tessa credential vault v1"
CRYPTPROTECT_UI_FORBIDDEN = 0x01
ERROR_INVALID_DATA = 13


class DpapiError(RuntimeError):
    """The OS refused, or DPAPI is not available here. Always fail closed on it."""

    def __init__(self, op: str, winerror: int | None = None, detail: str = "") -> None:
        self.op = op
        self.winerror = winerror
        msg = f"DPAPI {op} refused"
        if winerror is not None:
            msg += f" (WinError {winerror})"
        if detail:
            msg += f": {detail}"
        super().__init__(msg)


if sys.platform == "win32":
    from ctypes import wintypes

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DATA_BLOB),
    ]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DATA_BLOB),
    ]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p
else:  # pragma: no cover - the daemon targets Windows; everything below fails closed
    _DATA_BLOB = None  # type: ignore[assignment]
    _crypt32 = None
    _kernel32 = None


def available() -> tuple[bool, str]:
    """(usable, why-not). Checked once at vault open so the refusal is early and named."""
    if sys.platform != "win32":
        return False, f"DPAPI needs Windows; this is {sys.platform}"
    if _crypt32 is None:
        return False, "crypt32.dll did not load"
    return True, ""


def _blob_over(buf: Any) -> tuple[Any, Any]:
    """
    A DATA_BLOB pointing AT `buf`, plus the ctypes object that keeps it alive.

    A bytearray is used IN PLACE (`from_buffer`): no copy, so the caller's
    zeroing is the only zeroing needed. Immutable bytes are copied
    (`from_buffer_copy`) and the copy is zeroed by the caller of this helper.
    """
    n = len(buf)
    if n == 0:
        raise DpapiError("blob", detail="empty input")
    arr_t = ctypes.c_char * n
    if isinstance(buf, (bytearray, memoryview)):
        arr = arr_t.from_buffer(buf)
    else:
        arr = arr_t.from_buffer_copy(bytes(buf))
    blob = _DATA_BLOB(n, ctypes.cast(arr, ctypes.POINTER(ctypes.c_char)))
    return blob, arr


def _take_out(blob: Any) -> bytearray:
    """Copy the OS output into a bytearray, then zero and free the OS buffer."""
    n = int(blob.cbData)
    addr = ctypes.cast(blob.pbData, ctypes.c_void_p).value
    try:
        if n == 0 or not addr:
            return bytearray()
        view = (ctypes.c_char * n).from_address(addr)
        out = bytearray(view)
        ctypes.memset(addr, 0, n)
        return out
    finally:
        if addr:
            _kernel32.LocalFree(addr)


def protect(data: bytes | bytearray | memoryview,
            entropy: bytes | bytearray | None = None) -> bytes:
    """
    Encrypt `data` for the current Windows user. Returns the opaque blob.

    `entropy`, when given, is REQUIRED to decrypt — the OS refuses without it.
    The vault passes the scrypt-derived master key here in passphrase mode.
    """
    ok, why = available()
    if not ok:
        raise DpapiError("protect", detail=why)
    in_blob, in_keep = _blob_over(data)
    ent_blob = ent_keep = None
    if entropy is not None:
        ent_blob, ent_keep = _blob_over(entropy)
    out = _DATA_BLOB()
    try:
        res = _crypt32.CryptProtectData(
            ctypes.byref(in_blob), DESCRIPTION,
            ctypes.byref(ent_blob) if ent_blob is not None else None,
            None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
        if not res:
            raise DpapiError("protect", ctypes.get_last_error())
        return bytes(_take_out(out))
    finally:
        # A copied immutable input leaves a plaintext copy in `in_keep`; zero it.
        if not isinstance(data, (bytearray, memoryview)):
            ctypes.memset(ctypes.addressof(in_keep), 0, len(data))
        if ent_keep is not None and not isinstance(entropy, (bytearray, memoryview)):
            ctypes.memset(ctypes.addressof(ent_keep), 0, len(entropy))  # type: ignore[arg-type]


def unprotect(blob: bytes, entropy: bytes | bytearray | None = None) -> bytearray:
    """
    Decrypt a blob for the current user. Returns a bytearray the caller MUST zero.

    Wrong user, wrong machine, wrong or missing entropy, or a tampered blob all
    surface as `DpapiError` (typically WinError 13, ERROR_INVALID_DATA). There
    is deliberately no way to ask "was it the entropy or the user" — the answer
    is the same either way: refuse.
    """
    ok, why = available()
    if not ok:
        raise DpapiError("unprotect", detail=why)
    in_blob, _keep = _blob_over(bytes(blob))
    ent_blob = ent_keep = None
    if entropy is not None:
        ent_blob, ent_keep = _blob_over(entropy)
    out = _DATA_BLOB()
    # The description string comes back in a LocalAlloc'd buffer we must free.
    # Declared as a plain void pointer so freeing needs no cast.
    descr = ctypes.c_void_p()
    try:
        res = _crypt32.CryptUnprotectData(
            ctypes.byref(in_blob), ctypes.byref(descr),
            ctypes.byref(ent_blob) if ent_blob is not None else None,
            None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
        if not res:
            raise DpapiError("unprotect", ctypes.get_last_error())
        return _take_out(out)
    finally:
        if descr.value:
            try:
                _kernel32.LocalFree(descr.value)
            except Exception:  # noqa: BLE001
                pass
        if ent_keep is not None and not isinstance(entropy, (bytearray, memoryview)):
            ctypes.memset(ctypes.addressof(ent_keep), 0, len(entropy))  # type: ignore[arg-type]
