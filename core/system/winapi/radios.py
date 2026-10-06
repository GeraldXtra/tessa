"""
core/system/winapi/radios.py — Wi-Fi and Bluetooth radio state, and the switch.

`Windows.Devices.Radios.Radio` is the API behind the Settings quick-toggles:
no elevation, works with the legacy driver on this machine (probed 2026-09-07:
access Allowed, WiFi On, Bluetooth Off, 0.6 s round trip). `netsh interface
set interface admin=disable` would need an elevated prompt and disables the
adapter rather than the radio; this does what the owner means by "turn
bluetooth off".

PowerShell 5.1 is invoked with a CONSTANT argv on a script that ships beside
this file. The two variables — which radio, which state — are closed sets
mapped from Python's own tables and validated again by the script's
`ValidateSet`. No string from anywhere else reaches the command line.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

_SCRIPT = Path(__file__).with_name("radios.ps1")

#: Python name -> the RadioKind spelling the script accepts. Closed.
KINDS = {"wifi": "WiFi", "bluetooth": "Bluetooth"}

#: RadioState spellings -> what she says.
_STATES = {"on": "on", "off": "off", "disabled": "disabled", "unknown": "unknown"}


class RadioUnavailable(RuntimeError):
    pass


def _run(kind: str, state: str, timeout: float = 25.0) -> dict[str, object]:
    argv = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(_SCRIPT), "-Kind", kind, "-State", state]
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, shell=False)
    except subprocess.TimeoutExpired:
        raise RadioUnavailable("Windows did not answer about the radios in time") from None
    access = ""
    radios: dict[str, str] = {}
    names: dict[str, str] = {}
    sets: dict[str, str] = {}
    for line in r.stdout.splitlines():
        parts = line.strip().split("|")
        if parts[0] == "ACCESS" and len(parts) >= 2:
            access = parts[1]
        elif parts[0] == "RADIO" and len(parts) >= 3:
            k = parts[1].strip().lower()
            key = {"wifi": "wifi", "bluetooth": "bluetooth"}.get(k, k)
            radios[key] = _STATES.get(parts[2].strip().lower(), parts[2].strip().lower())
            names[key] = parts[3].strip() if len(parts) > 3 else key
        elif parts[0] == "SET" and len(parts) >= 3:
            sets[parts[1].strip().lower()] = parts[2].strip()
    if r.returncode == 2 or (access and access != "Allowed"):
        raise RadioUnavailable(f"Windows denied radio access ({access or 'no answer'})")
    if r.returncode != 0 and not radios:
        tail = (r.stderr or r.stdout).strip().splitlines()[-1:] or ["no output"]
        raise RadioUnavailable(f"the radio query failed: {tail[0][:160]}")
    return {"access": access, "radios": radios, "names": names, "sets": sets}


def list_radios() -> dict[str, str]:
    """{'wifi': 'on'|'off'|'disabled'|'unknown', 'bluetooth': ...}. Absent kinds are absent."""
    return dict(_run("All", "Query")["radios"])  # type: ignore[arg-type]


def set_radio(kind: str, on: bool) -> tuple[str, str]:
    """
    Switch one radio. `kind` is a KINDS key. Returns (state before, state
    after). Polls once more after a second if the readback has not settled —
    a Bluetooth stack takes a moment to report On.
    """
    if kind not in KINDS:
        raise RadioUnavailable(f"no such radio {kind!r}")
    before = list_radios().get(kind, "absent")
    if before == "absent":
        raise RadioUnavailable(f"this machine has no {kind} radio")
    wanted = "on" if on else "off"
    out = _run(KINDS[kind], "On" if on else "Off")
    after = out["radios"].get(kind, "unknown")  # type: ignore[union-attr]
    if after != wanted:
        time.sleep(1.0)
        after = list_radios().get(kind, "unknown")
    return before, after
