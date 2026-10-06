"""
core/system/abilities/display.py — how many screens, how big, which is primary.

GREEN under the existing `system.status` key: this is hardware state, the same
class of question as disk, memory and battery, and it changes nothing.

INFO ONLY. There is no screenshot and no screen reading here. Capturing what
is ON the screen is a different act with a different risk — it can catch a
password manager mid-reveal — and it belongs to its own later batch with its
own tier. This capability cannot see a single pixel.
"""

from __future__ import annotations

from typing import Any

from core.system.capability import Capability
from core.system.untrusted import fenced, head
from core.system.winapi import display


def info() -> dict[str, Any]:
    from core.tools.base import ToolError

    try:
        mons = display.monitors()
    except display.DisplayUnavailable as exc:
        raise ToolError(str(exc), "Check a display is attached.") from None

    primary = next((m for m in mons if m["primary"]), mons[0])
    n = len(mons)
    parts = []
    for m in mons:
        bit = f"{m['name']} at {m['width']} by {m['height']}"
        if m["scale_pct"] != 100:
            bit += f", scaled {m['scale_pct']} percent"
        if m["primary"] and n > 1:
            bit += ", the primary"
        parts.append(bit)
    summary = (f"{'One screen' if n == 1 else str(n) + ' screens'}, Emperor. "
               + ". ".join(parts) + ".")

    return {
        "n": n, "monitors": mons,
        "width": primary["width"], "height": primary["height"],
        "scale_pct": primary["scale_pct"], "refresh_hz": primary["refresh_hz"],
        "primary_name": primary["name"],
        "summary": summary,
        "head": head([m["name"] for m in mons]),
        **fenced("the attached displays", [m["name"] for m in mons]),
    }


CAPABILITIES = [
    Capability(
        name="system.display.info", capability="system.status", tier="green",
        run=info,
        phrasings=("how many monitors", "what's my resolution", "display info",
                   "what screens have I got"),
        success="{summary}",
        audit="display info",
        note="ctypes EnumDisplayMonitors + EnumDisplaySettingsW + GetDpiForMonitor, ~2 ms. "
             "Reports the adapter's real mode, so a scaled display is not understated. "
             "Does NOT change process DPI awareness. Info only — no screenshot, no screen reading.",
    ),
]
