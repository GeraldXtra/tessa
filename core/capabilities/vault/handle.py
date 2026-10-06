"""
core/capabilities/vault/handle.py — the object a secret travels in.

THE NO-RETURN-PATH GUARANTEE IS A TYPE, NOT A RULE (VAULT-DESIGN.md §5.2 E1).

A `SecretHandle` is what `vault.use()` hands to a consumer. Every route by
which a value becomes text — and therefore speech, a transcript event, an
audit line, a log record, a JSON frame, a pickle, a copy — RAISES
`SecretExposure` instead:

    str(h)           -> raises        (print(), logging "%s", f"{h}" all go here)
    format(h, spec)  -> raises        (f-strings, str.format)
    bytes(h)         -> raises
    pickle / copy    -> raises        (__reduce_ex__, __copy__, __deepcopy__)
    len / iter / []  -> raise         (nothing can walk the bytes by accident)
    json.dumps(h)    -> TypeError     (not serialisable; json's default=str
                                       lands on __str__ and raises too)
    repr(h)          -> SAFE          "<SecretHandle account=x kind=token live>"

The ONLY way to read the bytes is `with h.expose() as view:`, which yields a
read-only memoryview of the live buffer. The buffer is a bytearray that the
vault ZEROES when the consumer returns, so a handle kept past its use is a
handle to zeros.

HONEST LIMIT: a consumer that decodes the view to a `str` (Playwright's
`fill()` needs one) has made an immutable copy this module cannot zero. That
is stated in VAULT-DESIGN.md §12 and is the price of the browser being a
separate process.
"""

from __future__ import annotations

import base64
import contextlib
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Iterator


class SecretExposure(RuntimeError):
    """Something tried to turn a secret into text. Refused."""


REDACTED = "<REDACTED-SECRET>"


class SecretHandle:
    __slots__ = ("_buf", "account_id", "kind", "_exposures")

    def __init__(self, buf: bytearray, *, account_id: str, kind: str) -> None:
        if not isinstance(buf, bytearray):
            raise TypeError("SecretHandle needs a bytearray so it can be zeroed")
        self._buf = buf
        self.account_id = account_id
        self.kind = kind
        self._exposures = 0

    # ── the one legitimate read ──────────────────────────────────────────────

    @contextlib.contextmanager
    def expose(self) -> Iterator[memoryview]:
        """Read-only view of the live bytes. Do not decode unless you must."""
        if not self.alive:
            raise SecretExposure("handle already zeroed")
        self._exposures += 1
        view = memoryview(self._buf).toreadonly()
        try:
            yield view
        finally:
            view.release()

    @property
    def alive(self) -> bool:
        return len(self._buf) > 0 and any(self._buf)

    @property
    def exposures(self) -> int:
        return self._exposures

    def zero(self) -> None:
        """Overwrite the buffer in place. Called by the vault after every use."""
        n = len(self._buf)
        if n:
            self._buf[:] = b"\x00" * n

    # ── every text/copy route raises ─────────────────────────────────────────

    def __repr__(self) -> str:
        state = "live" if self.alive else "zeroed"
        return f"<SecretHandle account={self.account_id} kind={self.kind} {state}>"

    def __str__(self) -> str:
        raise SecretExposure("str() on a SecretHandle")

    def __format__(self, spec: str) -> str:
        raise SecretExposure("format() on a SecretHandle")

    def __bytes__(self) -> bytes:
        raise SecretExposure("bytes() on a SecretHandle")

    def __len__(self) -> int:
        raise SecretExposure("len() on a SecretHandle")

    def __iter__(self) -> Iterator[int]:
        raise SecretExposure("iter() on a SecretHandle")

    def __getitem__(self, item: Any) -> Any:
        raise SecretExposure("indexing a SecretHandle")

    def __contains__(self, item: Any) -> bool:
        raise SecretExposure("`in` on a SecretHandle")

    def __reduce__(self) -> Any:
        raise SecretExposure("pickling a SecretHandle")

    def __reduce_ex__(self, protocol: Any) -> Any:
        raise SecretExposure("pickling a SecretHandle")

    def __getstate__(self) -> Any:
        raise SecretExposure("pickling a SecretHandle")

    def __copy__(self) -> Any:
        raise SecretExposure("copying a SecretHandle")

    def __deepcopy__(self, memo: Any) -> Any:
        raise SecretExposure("copying a SecretHandle")

    def __bool__(self) -> bool:
        return True


@dataclass(frozen=True)
class ProfileRef:
    """
    What `vault.use()` returns for a `browser-session` record: a PATH, not a
    secret. The session cookie stays inside Chrome's own encrypted store; the
    vault records which profile holds it and what it may be used for.
    """
    account_id: str
    path: str
    origin: str
    meta: dict[str, Any] = field(default_factory=dict)
    kind: str = "browser-session"


# ── scrubbing: keep a live secret out of any text that leaves the vault ──────

def _forms(secret: bytes) -> list[str]:
    """The textual shapes a secret could take on the way out."""
    forms: list[str] = []
    try:
        raw = secret.decode("utf-8")
        if raw:
            forms.append(raw)
            forms.append(urllib.parse.quote(raw, safe=""))
    except UnicodeDecodeError:
        pass
    b64 = base64.b64encode(secret).decode("ascii")
    forms.append(b64)
    forms.append(b64.rstrip("="))
    u64 = base64.urlsafe_b64encode(secret).decode("ascii")
    forms.append(u64)
    forms.append(u64.rstrip("="))
    forms.append(secret.hex())
    # Longest first so a padded form is replaced before its unpadded prefix.
    return sorted({f for f in forms if len(f) >= 4}, key=len, reverse=True)


def scrub(text: str, secret: bytes) -> str:
    """Replace every recognisable form of `secret` inside `text`."""
    out = text
    for form in _forms(secret):
        if form in out:
            out = out.replace(form, REDACTED)
    return out


def contains(text: str, secret: bytes) -> bool:
    return any(form in text for form in _forms(secret))
