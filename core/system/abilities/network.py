"""
core/system/abilities/network.py — is the wifi on, what is it on, is bluetooth on.

GREEN under `system.status`, READ-ONLY. Three sources, all stdlib:

    WinRT Radio state     wifi/bluetooth on|off        core/system/winapi/radios
    netsh wlan            connected SSID, signal, band core/system/winapi/wlan
    TCP connect 1.1.1.1   is there a route out         core/tools/sysctl.network (reused)

The existing `sys.network` answers "am I online"; `sys.wifi` lists networks
in range. Neither says whether the RADIO is on or which network he is
actually on, which is the question behind "why is nothing loading".
"""

from __future__ import annotations

from typing import Any

from core.system.capability import Capability
from core.system.winapi import radios, wlan


def status() -> dict[str, Any]:
    from core.tools import sysctl

    try:
        rad = radios.list_radios()
    except radios.RadioUnavailable as exc:
        rad = {"_error": str(exc)}
    try:
        w = wlan.interface()
    except wlan.WlanUnavailable as exc:
        w = {"present": False, "connected": False, "ssid": "", "_error": str(exc)}
    net = sysctl.network()

    wifi = rad.get("wifi", "absent")
    bt = rad.get("bluetooth", "absent")
    connected = bool(w.get("connected"))
    ssid = str(w.get("ssid") or "")
    signal = w.get("signal")
    band = str(w.get("band") or "")

    parts: list[str] = []
    if wifi == "absent":
        parts.append("There is no Wi-Fi radio I can see")
    else:
        parts.append(f"Wi-Fi is {wifi}")
    if connected and ssid:
        line = f"connected to {ssid}"
        if isinstance(signal, int):
            line += f", signal {signal} percent"
        if band:
            line += f", {band.replace('GHz', ' gigahertz').strip()}"
        parts[-1] += " and " + line
    elif wifi == "on":
        parts[-1] += " but not connected"
    parts.append("Bluetooth is absent" if bt == "absent" else f"Bluetooth is {bt}")
    parts.append("You are online" if net["online"] else "You are offline")
    summary = ". ".join(parts) + "."

    return {"wifi": wifi, "bluetooth": bt, "connected": connected, "ssid": ssid,
            "signal": signal, "band": band, "online": bool(net["online"]),
            "ip": net.get("primary", ""), "summary": summary}


CAPABILITIES = [
    Capability(
        name="system.network.status", capability="system.status", tier="green",
        run=status,
        phrasings=("is the wifi on", "what network am I on", "is bluetooth on",
                   "network status", "what am I connected to"),
        success="{summary}",
        audit="network status",
        note="Read-only. netsh + WinRT radios + one TCP handshake; no browser, no HTTP.",
    ),
]
