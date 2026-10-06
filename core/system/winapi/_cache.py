"""
core/system/winapi/_cache.py — a one-entry TTL cache, for the slow observers.

WHY, WITH THE MEASUREMENTS THAT JUSTIFY IT (this machine, 2026-09-07):

    display  (ctypes EnumDisplayMonitors)     2 ms
    installed software (winreg Uninstall)    25 ms
    startup items (winreg Run + folders)      3 ms
    services (winreg + sc query)             85 ms
    event tail (wevtutil /c:N)               42 ms
    scheduled tasks (schtasks /query)      2817 ms   <- the outlier

`schtasks` is the only mechanism that costs seconds, and it is the only one
cached. Everything else is fast enough that a cache would add staleness for
nothing.

THE STALENESS IS BOUNDED AND DECLARED. 60 seconds means "what scheduled tasks
do I have", asked twice while he reads the first answer, costs 2.8 s once
rather than twice — and a task created in the last minute can be missed. The
capability's `note` says so, because a cache the owner does not know about is
a cache that will eventually lie to him.

Deliberately NOT a general memoiser: one entry, one lock, no keys. A cache
with an unbounded key space is a memory leak on a machine with 14.5 GB free.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable


class TTLValue:
    """One cached value with an age limit. Thread-safe."""

    def __init__(self, produce: Callable[[], Any], ttl_s: float) -> None:
        self._produce = produce
        self._ttl_s = float(ttl_s)
        self._lock = threading.Lock()
        self._value: Any = None
        self._at: float = 0.0

    def get(self, *, refresh: bool = False) -> tuple[Any, float]:
        """
        (value, age_seconds). `age` is 0.0 when it was just produced, so a
        caller can tell the owner how old the answer is.
        """
        with self._lock:
            now = time.monotonic()
            fresh = self._value is not None and (now - self._at) <= self._ttl_s
            if fresh and not refresh:
                return self._value, now - self._at
            value = self._produce()
            self._value = value
            self._at = time.monotonic()
            return value, 0.0

    def invalidate(self) -> None:
        with self._lock:
            self._value = None
            self._at = 0.0
