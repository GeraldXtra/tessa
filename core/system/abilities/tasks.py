"""
core/system/abilities/tasks.py — Task Scheduler: what is set to run, and when.

GREEN under `system.services`, alongside services: both answer "what runs on
this machine without me starting it". Read-only — no create, change, run or
delete, and no parameter that could become one.

HIS TASKS FIRST, WINDOWS' OWN COUNTED. 255 tasks exist on this machine and 239
of them are Microsoft's housekeeping under \\Microsoft\\. Reading those out
would bury the 16 that are actually his, so they are separated rather than
dropped, and the count is always stated.

⚠ SLOW, CACHED, AND HE IS TOLD. `schtasks` costs about 2 to 3 seconds — the
only mechanism in this batch that costs seconds — so the list is cached for 60
seconds. A task created in the last minute can therefore be missed, which is
why the age of the answer is returned and the note says so.
"""

from __future__ import annotations

from typing import Any

from core.system.capability import Capability, Param
from core.system.untrusted import fenced, head
from core.system.winapi import tasks as wintasks


def listing(mine_only: bool = True) -> dict[str, Any]:
    from core.tools.base import ToolError

    try:
        rows, age = wintasks.tasks()
    except wintasks.TasksUnavailable as exc:
        raise ToolError(str(exc), "Task Scheduler would not answer.") from None

    mine = [r for r in rows if not r["windows_own"]]
    windows_own = len(rows) - len(mine)
    shown = mine if mine_only else rows
    ready = [r for r in shown if r["state"] == "ready"]
    freshness = "" if age < 1.0 else f" That list is {int(age)} seconds old."

    summary = (f"{len(shown)} scheduled tasks, Emperor, {len(ready)} ready. "
               f"{head([r['name'] for r in shown])}. "
               f"{windows_own} more are Windows' own.{freshness}")

    return {
        "n": len(shown), "total": len(rows), "windows_own": windows_own,
        "ready": len(ready), "age_s": round(age, 1), "tasks": shown[:200],
        "head": head([r["name"] for r in shown]),
        "summary": summary,
        **fenced("the scheduled-task list",
                 [f"{r['path']} [{r['state']}] next {r['next_run']}" for r in shown]),
    }


CAPABILITIES = [
    Capability(
        name="system.tasks.list", capability="system.services", tier="green",
        run=listing,
        params=(Param("mine_only", bool, default=True,
                      doc="Skip Windows' own tasks under \\Microsoft\\?"),),
        phrasings=("what scheduled tasks do I have", "list scheduled tasks",
                   "my scheduled tasks"),
        success="{summary}",
        audit="list scheduled tasks",
        note="schtasks /query, the ONE slow observer at ~2-3 s, so it is cached 60 s and the "
             "age of the answer is spoken when it is not fresh. Read-only. Windows' own "
             "tasks are separated and counted, never silently dropped. Output is fenced.",
    ),
]
