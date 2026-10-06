"""
core/system/abilities/services.py — Windows services: what exists, what runs.

GREEN under `system.services`. Read-only: this capability has no start, stop,
restart or reconfigure path, and no parameter that could become one. Starting
and stopping services is a state change and belongs to a later amber batch,
where it will hold for his confirmation the way the radio toggle does.

Fast by construction — 85 ms, from the registry plus `sc query`, rather than
4.6 seconds from `Win32_Service`. See core/system/winapi/services.py.

⚠ A service's display name is chosen by whoever installed it, so the output is
fenced as untrusted external content.
"""

from __future__ import annotations

from typing import Any

from core.system.capability import Capability, Param
from core.system.untrusted import fenced, head
from core.system.winapi import services as winsvc


def listing(name: str = "", running_only: bool = False) -> dict[str, Any]:
    from core.tools.base import ToolError

    try:
        rows = winsvc.services()
    except winsvc.ServicesUnavailable as exc:
        raise ToolError(str(exc), "Windows would not describe its services.") from None

    total = len(rows)
    running = [r for r in rows if r["state"] == "running"]
    shown = rows
    query = (name or "").strip().lower()
    if query:
        shown = [r for r in shown if query in r["name"].lower() or query in r["display"].lower()]
    if running_only:
        shown = [r for r in shown if r["state"] == "running"]

    if query and not shown:
        summary = f"No service matching {name}, Emperor. There are {total} in all."
    elif query:
        first = shown[0]
        summary = (f"{first['display']} is {first['state']}, Emperor, "
                   f"start type {first['start']}."
                   + (f" And {len(shown) - 1} more matching." if len(shown) > 1 else ""))
    else:
        summary = (f"{total} services, Emperor. {len(running)} running. "
                   f"{head([r['display'] for r in running])}.")

    return {
        "n": len(shown), "total": total, "running": len(running), "asked": name,
        "found": bool(shown), "services": shown[:200],
        "head": head([r["display"] for r in shown]),
        "summary": summary,
        **fenced("the services list",
                 [f"{r['display']} ({r['name']}) {r['state']}/{r['start']}" for r in shown]),
    }


CAPABILITIES = [
    Capability(
        name="system.services.list", capability="system.services", tier="green",
        run=listing,
        params=(Param("name", str, default="", doc="A service to look for, or nothing for all."),
                Param("running_only", bool, default=False, doc="Only the ones running?")),
        phrasings=("list my services", "what services are running", "is the print spooler running"),
        success="{summary}",
        audit="list services {name}",
        note="winreg Services (start type) + `sc query` (live state), ~85 ms for 312 services. "
             "NOT Win32_Service, which costs 4.6 s on this machine. Read-only — there is no "
             "start/stop/config path in this capability. Output is fenced as untrusted.",
    ),
]
