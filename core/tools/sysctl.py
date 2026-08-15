"""
core/tools/sysctl.py — the machine itself: volume, media, brightness, battery,
uptime, network, wifi, IP, sleep, lock.

EVERY SUBPROCESS HERE IS A FIXED ARGUMENT VECTOR, `shell=False`. Not one of
them interpolates a value into a command line. `netsh` and `powershell` are
invoked with constant argv arrays; the only variable that ever reaches a
subprocess in this file is an integer brightness percentage, and it is bounded
to 0..100 and passed as its own argv element. That is CLAUDE.md invariant 4
holding at the last mile — the place it is usually lost.

BRIGHTNESS IS PROBED, NOT ASSUMED. Laptop panels expose WMI brightness control;
plenty of external monitors and some hybrid-graphics laptops do not, and the
class simply is not there. She reports that it is unavailable rather than
silently doing nothing, because a control that fails quietly teaches him the
whole voice surface is unreliable.
"""

from __future__ import annotations

import ctypes
import socket
import subprocess
import time
from typing import Any

import psutil

from .base import ToolError

user32 = ctypes.windll.user32

#: Virtual key codes. Structured constants — never a keystroke string from
#: anywhere else in the system.
VK = {
    "up": 0xAF, "down": 0xAE, "mute": 0xAD,
    "playpause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2,
}
KEYEVENTF_KEYUP = 0x0002

_PS = ["powershell", "-NoProfile", "-NonInteractive", "-Command"]


def _ps(script: str, timeout: float = 20.0) -> str:
    """PowerShell with a CONSTANT script. `script` is only ever a literal from this module."""
    r = subprocess.run(_PS + [script], capture_output=True, text=True,
                       timeout=timeout, shell=False)
    return r.stdout.strip()


# ── volume and media ─────────────────────────────────────────────────────────

def volume(direction: str, steps: int = 3) -> dict[str, Any]:
    """
    ONE PRESS IS ~2% ON WINDOWS. He says "turn it up" and means noticeably, not
    two percent, so a step count is applied — three taps, which is the same
    ~6% jump the physical keys on his laptop make per press.
    """
    key = VK.get(str(direction or "").lower())
    if key is None:
        raise ToolError(f"I did not catch {direction!r}", "Up, down, or mute?")
    n = 1 if key == VK["mute"] else max(1, min(int(steps), 10))
    for _ in range(n):
        user32.keybd_event(key, 0, 0, 0)
        user32.keybd_event(key, 0, KEYEVENTF_KEYUP, 0)
    return {"direction": direction, "steps": n}


def media(action: str) -> dict[str, Any]:
    key = VK.get(str(action or "").lower())
    if key is None:
        raise ToolError(f"I did not catch {action!r}", "Play, pause, next, or previous?")
    user32.keybd_event(key, 0, 0, 0)
    user32.keybd_event(key, 0, KEYEVENTF_KEYUP, 0)
    return {"action": action}


# ── brightness ───────────────────────────────────────────────────────────────

def brightness(level: int | None = None) -> dict[str, Any]:
    if level is None:
        out = _ps("(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness "
                  "-ErrorAction SilentlyContinue).CurrentBrightness")
        if not out:
            raise ToolError("this display does not expose brightness to Windows",
                            "Use the keys on the top row.")
        return {"level": int(out.splitlines()[0]), "set": False}

    pct = max(0, min(int(level), 100))
    out = _ps("$m = Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods "
              "-ErrorAction SilentlyContinue; if ($m) { "
              f"Invoke-CimMethod -InputObject $m -MethodName WmiSetBrightness "
              f"-Arguments @{{Timeout=1; Brightness={pct}}} | Out-Null; 'ok' }}")
    if out != "ok":
        raise ToolError("this display does not expose brightness to Windows",
                        "Use the keys on the top row.")
    return {"level": pct, "set": True}


# ── read-only state ──────────────────────────────────────────────────────────

def battery() -> dict[str, Any]:
    b = psutil.sensors_battery()
    if b is None:
        raise ToolError("I cannot read a battery on this machine", "It may be a desktop.")
    left = ""
    if not b.power_plugged and b.secsleft not in (psutil.POWER_TIME_UNKNOWN, psutil.POWER_TIME_UNLIMITED):
        h, rem = divmod(int(b.secsleft), 3600)
        left = f" About {h} hours {rem // 60} minutes left." if h else f" About {rem // 60} minutes left."
    return {"pct": b.percent, "plugged": bool(b.power_plugged),
            "where": "on mains" if b.power_plugged else "on battery", "left": left}


def disk(drive: str = "C:\\") -> dict[str, Any]:
    u = psutil.disk_usage(str(drive))
    return {"drive": drive, "free_gb": u.free / 1e9, "total_gb": u.total / 1e9,
            "free_pct": 100 - u.percent}


def memory() -> dict[str, Any]:
    m = psutil.virtual_memory()
    return {"free_gb": m.available / 1e9, "total_gb": m.total / 1e9, "used_pct": m.percent}


def uptime() -> dict[str, Any]:
    s = int(time.time() - psutil.boot_time())
    h, rem = divmod(s, 3600)
    return {"hours": h, "minutes": rem // 60, "seconds": s}


def network() -> dict[str, Any]:
    """
    Reachability by TCP CONNECT, not by ping and not by an HTTP request.

    ICMP is blocked on plenty of networks and would report "down" on a working
    link. An HTTP GET costs metered bytes for a boolean. A TCP handshake to
    1.1.1.1:443 is a few hundred bytes and answers the actual question — is
    there a route out.
    """
    up = []
    for nic, addrs in psutil.net_if_addrs().items():
        stats = psutil.net_if_stats().get(nic)
        if stats and stats.isup:
            for a in addrs:
                if a.family == socket.AF_INET and not a.address.startswith("127."):
                    up.append({"nic": nic, "ip": a.address})
    online = False
    t0 = time.perf_counter()
    try:
        with socket.create_connection(("1.1.1.1", 443), timeout=2.0):
            online = True
    except OSError:
        online = False
    ms = (time.perf_counter() - t0) * 1000.0
    return {"online": online, "ms": ms, "nics": up, "n": len(up),
            "primary": up[0]["ip"] if up else "",
            "state": "online" if online else "offline"}


def ip_address() -> dict[str, Any]:
    n = network()
    if not n["nics"]:
        raise ToolError("no network adapter is up", "Check your wifi is on.")
    return {"ip": n["primary"], "nic": n["nics"][0]["nic"], "n": n["n"]}


def wifi_list() -> dict[str, Any]:
    r = subprocess.run(["netsh", "wlan", "show", "networks"],
                       capture_output=True, text=True, timeout=25, shell=False)
    if r.returncode != 0:
        raise ToolError("Windows would not list the networks", "Check the wireless adapter is on.")
    ssids = [ln.split(":", 1)[1].strip()
             for ln in r.stdout.splitlines()
             if ln.strip().startswith("SSID ") and ":" in ln]
    ssids = [s for s in ssids if s]
    return {"n": len(ssids), "ssids": ssids,
            "head": ", ".join(ssids[:4]) if ssids else "nothing"}


# ── machine control ──────────────────────────────────────────────────────────

def lock() -> dict[str, Any]:
    user32.LockWorkStation()
    return {}


def sleep() -> dict[str, Any]:
    """
    Suspend to RAM.

    `SetSuspendState(Hibernate=0, Force=0, WakeupEventsDisabled=0)`. Force is 0
    on purpose: an application with an unsaved document gets to veto, which is
    the same courtesy `winman.close` extends. Hibernate is 0 because he asked
    to sleep, and hibernating instead would be a different action wearing the
    same word.

    Kept GREEN, and I considered making it hold. It is fully reversible —
    Windows preserves state and wake is a keypress — so the cost of a
    mistranscription is about thirty seconds of annoyance, against the cost of
    a confirmation on every single use. Not worth the friction.
    """
    ctypes.windll.powrprof.SetSuspendState(0, 0, 0)
    return {}
