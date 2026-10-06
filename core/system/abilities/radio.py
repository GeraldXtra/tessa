"""
core/system/abilities/radio.py — turn the Wi-Fi or Bluetooth radio on or off.

AMBER under `system.radio` — the one NEW permissions.yaml line this batch
added. A radio toggle is a state change, not a destruction, and it is fully
reversible; but Wi-Fi off drops every connection on the machine INCLUDING
HERS (speech-to-text, Piper, the model), and Bluetooth off drops whatever he
is listening on. Not something to do on a mistranscription, so it holds:
she names the specific act ("turn wifi off") and waits for his yes.

The hold is not written here. The framework raises it from the tier, and
refuses a non-human origin outright until a job grant exists.
"""

from __future__ import annotations

from typing import Any

from core.system.capability import Capability, Param
from core.system.winapi import radios

_SAY = {"wifi": "Wi-Fi", "bluetooth": "Bluetooth"}


def set_state(radio: str, state: str) -> dict[str, Any]:
    from core.tools.base import ToolError

    want_on = state == "on"
    try:
        before, after = radios.set_radio(radio, want_on)
    except radios.RadioUnavailable as exc:
        raise ToolError(str(exc), "The hardware switch or airplane mode may be holding it.") from None
    if after != state:
        raise ToolError(f"Windows left {_SAY[radio]} {after}",
                        "Airplane mode or a hardware switch may be holding it.")
    return {"radio": _SAY[radio], "state": after, "was": before, "changed": before != after}


CAPABILITIES = [
    Capability(
        name="system.radio.set", capability="system.radio", tier="amber",
        run=set_state,
        params=(Param("radio", str, choices=("wifi", "bluetooth"), doc="Wi-Fi or Bluetooth?"),
                Param("state", str, choices=("on", "off"), doc="On or off?")),
        phrasings=("turn bluetooth off", "turn on the wifi", "switch bluetooth on",
                   "wifi off"),
        success="{radio} is {state}, Emperor.",
        audit="RADIO {radio} {state}",
        hold="turn {radio} {state}",
        note="WinRT Radio.SetStateAsync via PowerShell 5.1 with a constant script. "
             "Reads the radio back and refuses to claim a state Windows does not show. "
             "Wi-Fi off cuts her own connections — that is why this is amber.",
    ),
]
