"""
core/system/winapi/wlan.py — what the Wi-Fi adapter is connected to.

`netsh wlan show interfaces`, constant argv, parsed by field name. netsh is
the only stdlib-reachable source of the connected SSID, signal and band that
needs neither elevation nor a native module. The output is locale-formatted
English on this machine; a field that is not found is reported absent, never
guessed.
"""

from __future__ import annotations

import subprocess
from typing import Any


class WlanUnavailable(RuntimeError):
    pass


_FIELDS = {
    "name": "name", "description": "description", "state": "state", "ssid": "ssid",
    "radio type": "radio_type", "band": "band", "channel": "channel",
    "signal": "signal", "authentication": "auth", "profile": "profile",
}


def interface() -> dict[str, Any]:
    """
    The first wireless interface, as a dict. `present` is False when the
    machine has no wireless interface at all (or the WLAN service is off).
    `signal` is an int percentage or None.
    """
    try:
        r = subprocess.run(["netsh", "wlan", "show", "interfaces"],
                           capture_output=True, text=True, timeout=15, shell=False)
    except subprocess.TimeoutExpired:
        raise WlanUnavailable("netsh did not answer in time") from None
    if r.returncode != 0:
        raise WlanUnavailable("Windows would not describe the wireless interface")

    out: dict[str, Any] = {"present": False}
    for raw in r.stdout.splitlines():
        line = raw.strip()
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key in _FIELDS and _FIELDS[key] not in out:      # first interface only
            out[_FIELDS[key]] = value
            out["present"] = True
    sig = out.get("signal")
    if isinstance(sig, str):
        digits = "".join(ch for ch in sig if ch.isdigit())
        out["signal"] = int(digits) if digits else None
    out["state"] = str(out.get("state", "")).lower()
    out["connected"] = out["state"] == "connected"
    if not out["connected"]:
        out["ssid"] = ""
    return out
