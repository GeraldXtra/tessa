"""
core/system/winapi/tasks.py — Task Scheduler entries: name, state, next run.

`schtasks /query /fo csv /nh`, constant argv, `shell=False`. Read-only: there
is no `/create`, `/change`, `/run` or `/delete` in this file, and no parameter
that could become one.

THIS IS THE ONE SLOW OBSERVER — 2817 ms measured on this machine for 255
tasks, against 2 to 85 ms for every other mechanism in this batch. It is slow
because Task Scheduler walks every folder in the tree and resolves each
trigger. There is no faster supported source: the TaskCache registry hive
holds names but not the resolved next-run time, and the Schedule.Service COM
object is not reachable from stdlib ctypes without writing an IDispatch
client.

So it is CACHED, 60 seconds, one entry — see `_cache.TTLValue`. The second
question inside a minute is instant, and the capability's note tells the owner
the answer may be up to a minute old. A cache he does not know about is a
cache that will eventually lie to him.

Task names and paths are UNTRUSTED: anybody who can create a scheduled task
chooses its name. See core/system/untrusted.py.
"""

from __future__ import annotations

import csv
import subprocess
from typing import Any

from ._cache import TTLValue

#: How stale a task list may be before it is fetched again.
TASKS_TTL_S = 60.0

#: Folders whose contents are Windows' own housekeeping. Hundreds of entries
#: that are never what he means by "my scheduled tasks", and including them
#: buries the handful that are his. Counted and reported, never silently lost.
_MICROSOFT_PREFIX = "\\microsoft\\"


class TasksUnavailable(RuntimeError):
    pass


def _query() -> list[dict[str, Any]]:
    try:
        result = subprocess.run(["schtasks", "/query", "/fo", "csv", "/nh"],
                                capture_output=True, text=True, timeout=120, shell=False)
    except subprocess.TimeoutExpired:
        raise TasksUnavailable("Task Scheduler did not answer in time") from None
    except OSError as exc:
        raise TasksUnavailable(f"schtasks would not run ({exc})") from None
    if result.returncode != 0 and not result.stdout.strip():
        raise TasksUnavailable("Windows would not list the scheduled tasks")

    rows: list[dict[str, Any]] = []
    for parts in csv.reader(result.stdout.splitlines()):
        if len(parts) < 3 or not parts[0].strip():
            continue
        path = parts[0].strip()
        if path.upper() == "TASKNAME":          # a repeated header row
            continue
        rows.append({
            "path": path,
            "name": path.rsplit("\\", 1)[-1] or path,
            "next_run": parts[1].strip(),
            "state": parts[2].strip().lower(),
            "windows_own": _MICROSOFT_PREFIX in path.lower(),
        })
    return rows


_CACHE = TTLValue(_query, TASKS_TTL_S)


def tasks(refresh: bool = False) -> tuple[list[dict[str, Any]], float]:
    """(rows, age_seconds). Age is 0.0 when this call did the work."""
    return _CACHE.get(refresh=refresh)
