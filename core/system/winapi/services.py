"""
core/system/winapi/services.py — Windows services: name, live state, start type.

TWO CHEAP SOURCES INSTEAD OF ONE EXPENSIVE ONE. Measured on this machine:

    Get-CimInstance Win32_Service      4592 ms
    winreg Services + `sc query`         85 ms   (51 ms + 34 ms)

`Win32_Service` is the one-call answer and it costs four and a half seconds on
two cores, which is far too long to sit in front of a spoken question. The
registry holds the configured start type for every service, and `sc query`
holds the live state, and together they are the same answer 54 times faster.

`sc.exe` IS CALLED WITH A CONSTANT ARGUMENT VECTOR, `shell=False`, and the
only thing parsed out of it is a service name and a state word. Nothing from
this module is ever executed.

READ-ONLY BY CONSTRUCTION. There is no `sc start`, `sc stop` or `sc config`
anywhere in this file, and no parameter that could become one. Controlling a
service is a state change and belongs to a later, amber batch.

The names and display names returned are UNTRUSTED — a service's display name
is chosen by whoever installed it. See core/system/untrusted.py.
"""

from __future__ import annotations

import subprocess
import winreg
from typing import Any

_SERVICES_KEY = r"SYSTEM\CurrentControlSet\Services"

#: `Start` REG_DWORD, as documented for SERVICE_*_START.
_START_TYPE = {0: "boot", 1: "system", 2: "automatic", 3: "manual", 4: "disabled"}

#: `Type` values that are actual services rather than drivers. 0x10 own
#: process, 0x20 shared process, plus the interactive variants.
_SERVICE_TYPES = (0x10, 0x20, 0x110, 0x120)


class ServicesUnavailable(RuntimeError):
    pass


def _configured() -> dict[str, dict[str, Any]]:
    """Every service the registry knows, with its configured start type."""
    out: dict[str, dict[str, Any]] = {}
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _SERVICES_KEY)
    except OSError as exc:
        raise ServicesUnavailable(f"the service registry is unreadable ({exc})") from None
    try:
        count = winreg.QueryInfoKey(root)[0]
    except OSError as exc:
        raise ServicesUnavailable(f"the service registry is unreadable ({exc})") from None
    for i in range(count):
        try:
            name = winreg.EnumKey(root, i)
            with winreg.OpenKey(root, name) as key:
                def _v(field: str) -> Any:
                    try:
                        return winreg.QueryValueEx(key, field)[0]
                    except OSError:
                        return None
                if _v("Type") not in _SERVICE_TYPES:
                    continue
                display = str(_v("DisplayName") or name)
                # A display name of the form `@file.dll,-123` is an unresolved
                # resource pointer, not a name. Saying it out loud would be
                # noise, so the service's own key name is the honest fallback.
                if display.startswith("@"):
                    display = name
                out[name.lower()] = {"name": name, "display": display,
                                     "start": _START_TYPE.get(_v("Start"), "unknown")}
        except OSError:
            continue
    return out


def _live_states() -> dict[str, str]:
    """Service name (lowered) -> running | stopped | ... from `sc query`."""
    try:
        result = subprocess.run(
            ["sc", "query", "type=", "service", "state=", "all"],
            capture_output=True, text=True, timeout=60, shell=False)
    except (subprocess.TimeoutExpired, OSError):
        return {}
    states: dict[str, str] = {}
    current: str | None = None
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if line.upper().startswith("SERVICE_NAME:"):
            current = line.split(":", 1)[1].strip()
        elif line.upper().startswith("STATE") and current:
            parts = line.split()
            if parts:
                states[current.lower()] = parts[-1].lower()
    return states


def services() -> list[dict[str, Any]]:
    """Every service, with configured start type and live state."""
    configured = _configured()
    live = _live_states()
    rows = []
    for key, row in configured.items():
        rows.append({**row, "state": live.get(key, "unknown")})
    rows.sort(key=lambda r: r["display"].lower())
    return rows
