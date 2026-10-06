"""
core/system/abilities/startup.py — what launches itself when he logs in.

GREEN under `system.inventory`. Reads the four Run keys and both Startup
folders. It cannot add, remove or disable an entry; that is a state change and
belongs to a later amber batch.

THIS IS THE STARTUP AUDIT THE SENTINEL VISION WANTS. Windows scatters this
across two registry hives, two bitness views and two folders, which is why
nobody ever checks it. One question should answer it.

⚠ UNTRUSTED, AND THIS ONE MATTERS MOST OF THE THREE INVENTORY READS. Anything
that persists on a machine puts itself here, so the value of a Run entry is
attacker-chosen text by definition. It is fenced.
"""

from __future__ import annotations

from typing import Any

from core.system.capability import Capability
from core.system.untrusted import fenced, head
from core.system.winapi import inventory


def listing() -> dict[str, Any]:
    items = inventory.startup_items()
    registry = [i for i in items if "registry" in i["where"]]
    folders = [i for i in items if "folder" in i["where"].lower()]
    summary = (f"{len(items)} things start with Windows, Emperor. "
               f"{head([i['name'] for i in items])}. "
               f"{len(registry)} from the registry, {len(folders)} from the Startup folders.")
    return {
        "n": len(items), "items": items,
        "n_registry": len(registry), "n_folders": len(folders),
        "head": head([i["name"] for i in items]),
        "summary": summary,
        **fenced("the startup items",
                 [f"{i['name']} -> {i['command']} [{i['where']}]" for i in items]),
    }


CAPABILITIES = [
    Capability(
        name="system.startup.list", capability="system.inventory", tier="green",
        run=listing,
        phrasings=("what's running at startup", "what starts with windows",
                   "list startup items", "startup programs"),
        success="{summary}",
        audit="list startup items",
        note="winreg Run/RunOnce (both bitness views, machine and user) plus both Startup "
             "folders, ~3 ms. Read-only: nothing here can add or disable an entry. "
             "Output is fenced — persistence entries are attacker-chosen text by definition.",
    ),
]
