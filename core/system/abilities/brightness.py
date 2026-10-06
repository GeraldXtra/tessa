"""
core/system/abilities/brightness.py — the panel's backlight, get and set.

GREEN under `system.control`. The mechanism is WMI (`WmiMonitorBrightness` /
`WmiMonitorBrightnessMethods`), which this laptop's HD 620 panel exposes
(probed 2026-09-07: reads 100, methods class present). It REUSES
`core.tools.sysctl.brightness` rather than re-implementing the PowerShell
call — one implementation, two front doors — and adds what that tool lacks:
a readback after the set, so "brightness forty" is confirmed by the panel,
not assumed.

External monitors and some hybrid-graphics laptops do not expose the class
at all; sysctl already says so plainly instead of pretending, and that
ToolError passes straight through here.
"""

from __future__ import annotations

from typing import Any

from core.system.capability import Capability, Param


def _read() -> int:
    from core.tools import sysctl   # lazy: core.tools is mid-import when this package binds
    return int(sysctl.brightness()["level"])


def get() -> dict[str, Any]:
    return {"level": _read()}


def set_level(level: int) -> dict[str, Any]:
    from core.tools import sysctl
    before = _read()
    sysctl.brightness(level)
    after = _read()
    if abs(after - level) > 2:
        from core.tools.base import ToolError
        raise ToolError(f"the panel did not take it — it reads {after}",
                        "Use the keys on the top row.")
    return {"level": after, "was": before, "asked": level}


CAPABILITIES = [
    Capability(
        name="system.brightness.get", capability="system.control", tier="green",
        run=get,
        phrasings=("what's the brightness", "how bright is the screen"),
        success="Brightness is {level} percent, Emperor.",
        audit="brightness get",
        note="WMI via core.tools.sysctl — reused, not duplicated.",
    ),
    Capability(
        name="system.brightness.set", capability="system.control", tier="green",
        run=set_level,
        params=(Param("level", int, lo=0, hi=100, doc="Zero to a hundred."),),
        phrasings=("set brightness to 40", "brightness seventy", "dim the screen to 20"),
        success="Brightness {level} percent, Emperor. It was {was}.",
        audit="brightness set {level}",
        note="Reads the panel back after setting; refuses to claim a level it does not show.",
    ),
]
