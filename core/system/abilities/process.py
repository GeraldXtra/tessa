"""
core/system/abilities/process.py — ONE process: what it is, and its priority.

The process FAMILY already lives in core/tools/procs.py: `proc.list`,
`proc.top`, `proc.find` (green) and `proc.kill` (amber, BY PID, pid frozen).
This module adds the two that were missing, through the capability framework
so their tier, hold, provenance rule and frozen target are enforced by
core/system/capability.py rather than remembered:

    system.process.details    green   system.status      CPU / memory / status of
                                                         one process, by pid OR name
    system.process.priority   amber   process.priority   raise / lower / set ONE
                                                         process's priority, by pid

THE TWO QUESTIONS EVERY TOOL HERE ANSWERS

  * Which argument is the TARGET?  `pid`. It is FROZEN through approval on
    `priority` (and on `proc.kill`), so an approved card cannot be retargeted.
  * Which targets are NEVER meant?  The refuse list in `procs.protection()`:
    pid 0 and 4, Windows core processes, the daemon itself, what it runs
    inside, what runs under it. `priority` shares that list with `kill` —
    it exists once — and refuses BEFORE the hold, through `describe`, so he
    is never asked to confirm something she will not do.

REALTIME IS NOT A LEVEL. The closed choice set stops at `high`; asking for
realtime is refused by `Param.coerce` with the list of what she will set.
`raise`/`lower` are one step from the CURRENT class, and never past `high`.

`details` is READ-ONLY and refuses nothing: looking at pid 4 is fine. Its
output is not fenced, matching `proc.list`/`proc.find` — a process image name
is a file name, and fencing it would refuse the very "kill <pid>" the lookup
exists to make possible. No command line is ever returned or spoken: those
can carry tokens.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from core.system.capability import Capability, Param

#: Lowest to highest. Realtime is deliberately absent.
LEVELS: tuple[str, ...] = ("low", "below normal", "normal", "above normal", "high")
#: One step from the current class.
RELATIVE: tuple[str, ...] = ("raise", "lower")


def _classes() -> dict[str, int]:
    import psutil

    return {
        "low": psutil.IDLE_PRIORITY_CLASS,
        "below normal": psutil.BELOW_NORMAL_PRIORITY_CLASS,
        "normal": psutil.NORMAL_PRIORITY_CLASS,
        "above normal": psutil.ABOVE_NORMAL_PRIORITY_CLASS,
        "high": psutil.HIGH_PRIORITY_CLASS,
    }


def _level_name(cls: Any) -> str:
    import psutil

    value = int(getattr(cls, "value", cls))
    for name, c in _classes().items():
        if value == int(c):
            return name
    if value == int(psutil.REALTIME_PRIORITY_CLASS):
        return "realtime"
    return str(value)


def _mb(n: int) -> str:
    return f"{n / 1e6:.0f} megabytes" if n < 1e9 else f"{n / 1e9:.1f} gigabytes"


def _no_such(pid: int):
    from core.tools.base import ToolError

    return ToolError(f"there is no process {pid}",
                     "Say find, and the name, and I will read you the live ones.")


# ─────────────────────────────────────────────────────────────────────────────
# details
# ─────────────────────────────────────────────────────────────────────────────

def _sample(procs: list[Any]) -> dict[int, float]:
    """
    CPU per process as a share of the WHOLE machine, sampled over 300 ms.
    `cpu_percent(None)` with no prior call is a lifetime average and says
    nothing about right now (see `procs.top`); the second read is the delta.
    """
    import psutil

    cores = max(1, psutil.cpu_count() or 1)
    live = []
    for p in procs:
        try:
            p.cpu_percent(None)
            live.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    time.sleep(0.3)
    out: dict[int, float] = {}
    for p in live:
        try:
            out[p.pid] = p.cpu_percent(None) / cores
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return out


def _one(pid: int) -> dict[str, Any]:
    import psutil

    from core.tools import procs

    try:
        p = psutil.Process(pid)
        with p.oneshot():
            name = p.name()
            status = p.status()
            rss = int(p.memory_info().rss)
            threads = p.num_threads()
            started = datetime.fromtimestamp(p.create_time()).strftime("%H:%M")
            try:
                level = _level_name(p.nice())
            except psutil.AccessDenied:
                level = "unknown"
    except psutil.NoSuchProcess:
        raise _no_such(pid) from None
    except psutil.AccessDenied:
        from core.tools.base import ToolError

        raise ToolError(f"Windows will not describe {pid} to me",
                        "It is probably a system process.") from None
    cpu = _sample([p]).get(pid, 0.0)
    chain = procs.ancestry_of(pid)
    launched = chain.split(" <- ")[1] if " <- " in chain else ""
    return {"pid": pid, "name": name, "status": status, "rss": rss, "cpu": cpu,
            "threads": threads, "started": started, "level": level, "chain": chain,
            "launched_by": launched}


def details(pid: int = 0, name: str = "") -> dict[str, Any]:
    """
    One process by pid, or every process matching a name — aggregated, with
    the heaviest pid read back so the next sentence can name it.
    """
    from core.tools import procs
    from core.tools.base import ToolError

    if pid:
        d = _one(int(pid))
        by = f", launched by {d['launched_by']}" if d["launched_by"] else ""
        d["n"] = 1
        d["summary"] = (f"{d['pid']} is {d['name']}{by}, Emperor. "
                        f"{_mb(d['rss'])}, {d['cpu']:.0f} percent of the machine, "
                        f"{d['status']}, {d['level']} priority, since {d['started']}.")
        d["rows"] = [{"pid": d["pid"], "name": d["name"], "rss": d["rss"], "cpu": d["cpu"]}]
        return d

    needle = str(name or "").strip().lower()
    if not needle:
        raise ToolError("no process came through", "Say a name or a number.")
    hits = procs.find(needle)
    rows = list(hits.get("rows") or [])
    if not rows:
        raise ToolError(f"nothing called {name} is running", "Say find, and part of the name.")
    import psutil

    procs_live = []
    for r in rows:
        try:
            procs_live.append(psutil.Process(int(r["pid"])))
        except psutil.NoSuchProcess:
            continue
    cpu_by = _sample(procs_live)
    total_rss = sum(int(r["rss"]) for r in rows)
    total_cpu = sum(cpu_by.values())
    for r in rows:
        r["cpu"] = cpu_by.get(int(r["pid"]), 0.0)
    heaviest = rows[0]
    if len(rows) == 1:
        summary = (f"{heaviest['name']} is {heaviest['pid']}, Emperor. {_mb(total_rss)}, "
                   f"{total_cpu:.0f} percent of the machine.")
    else:
        summary = (f"{heaviest['name']}: {len(rows)} processes, {_mb(total_rss)} between them, "
                   f"{total_cpu:.0f} percent of the machine, Emperor. "
                   f"The heaviest is {heaviest['pid']} at {_mb(int(heaviest['rss']))}.")
    return {"pid": int(heaviest["pid"]), "name": heaviest["name"], "n": len(rows),
            "rss": total_rss, "cpu": total_cpu, "rows": rows, "summary": summary,
            "pids": [int(r["pid"]) for r in rows]}


# ─────────────────────────────────────────────────────────────────────────────
# priority
# ─────────────────────────────────────────────────────────────────────────────

def _resolve(pid: int) -> tuple[Any, str, str]:
    """(process, name, current level). Refuses the protected list FIRST."""
    import psutil

    from core.tools import procs
    from core.tools.base import ToolError

    why = procs.protection(pid)
    if why is not None:
        raise ToolError(*why)
    try:
        p = psutil.Process(pid)
        name = p.name()
        was = _level_name(p.nice())
    except psutil.NoSuchProcess:
        raise _no_such(pid) from None
    except psutil.AccessDenied:
        raise ToolError(f"Windows will not let me touch {pid}",
                        "It is probably a system process. Leave it.") from None
    return p, name, was


def _target_level(level: str, was: str, name: str) -> str:
    from core.tools.base import ToolError

    if level not in RELATIVE:
        return level
    idx = LEVELS.index(was) if was in LEVELS else LEVELS.index("normal")
    nxt = idx + 1 if level == "raise" else idx - 1
    if nxt < 0 or nxt >= len(LEVELS):
        edge = "the highest I will set" if level == "raise" else "the lowest there is"
        raise ToolError(f"{name} is already at {was}, {edge}",
                        "Realtime is not something I will do.")
    return LEVELS[nxt]


def describe_priority(args: dict[str, Any]) -> str:
    """
    The hold line: WHAT the number is, at what level, going to what. Called
    before the hold, read-only, and it refuses the protected list outright so
    he is never asked to confirm a change she will not make.
    """
    from core.tools import procs

    pid = int(args.get("pid", 0))
    level = str(args.get("level", ""))
    _p, name, was = _resolve(pid)
    target = _target_level(level, was, name)
    chain = procs.ancestry_of(pid)
    by = f", launched by {chain.split(' <- ')[1]}" if " <- " in chain else ""
    return f"{pid} is {name}{by}, at {was} priority. I would set it to {target}"


def priority(pid: int, level: str) -> dict[str, Any]:
    import psutil

    from core.tools.base import ToolError

    # Resolved AGAIN after his yes: the table may have moved since the hold.
    p, name, was = _resolve(int(pid))
    target = _target_level(level, was, name)
    try:
        p.nice(_classes()[target])
        now = _level_name(p.nice())
    except psutil.NoSuchProcess:
        raise _no_such(int(pid)) from None
    except psutil.AccessDenied:
        raise ToolError(f"Windows would not let me change {name}",
                        "It may need administrator rights. Try it from Task Manager.") from None
    if now != target:
        raise ToolError(f"Windows kept {name} at {now}",
                        "Try it from Task Manager.")
    return {"pid": int(pid), "name": name, "was": was, "now": now, "level": target,
            "verdict": f"{name} is at {now} priority now, Emperor. It was {was}."}


CAPABILITIES = [
    Capability(
        name="system.process.details", capability="system.status", tier="green",
        run=details,
        params=(Param("pid", int, default=0, lo=0, hi=9_999_999,
                      doc="The process number, or say the name instead."),
                Param("name", str, default="", doc="Part of the process name.")),
        phrasings=("how much memory is chrome using", "what is process 4242",
                   "how much cpu is python using", "tell me about process 4242"),
        success="{summary}",
        failure="I could not read it, sir. {reason} {alternative}",
        audit="details of process {pid} {name}",
        note="Read-only. By pid: name, status, memory, CPU sampled over 300 ms as a share "
             "of the machine, threads, priority, parent. By name: every match aggregated, "
             "heaviest pid read back. No command line is returned. Refuses nothing.",
    ),
    Capability(
        name="system.process.priority", capability="process.priority", tier="amber",
        run=priority,
        params=(Param("pid", int, lo=1, hi=9_999_999, doc="The process number."),
                Param("level", str, choices=LEVELS + RELATIVE,
                      doc="low, below normal, normal, above normal, high — or raise / lower.")),
        # ⚠ THE TARGET IS FROZEN. An approved card may not retarget it.
        frozen=("pid",),
        describe=describe_priority,
        phrasings=("raise the priority of 4242", "lower the priority of 4242",
                   "set the priority of 4242 to high"),
        success="{verdict}",
        failure="I did not change it, sir. {reason} {alternative}",
        audit="PRIORITY pid {pid} -> {level}",
        hold="set the priority of process {pid} to {level}",
        note="Amber: holds, and the hold NAMES the process and its current level "
             "(`describe`). Refuses pid 0/4, Windows core processes, the daemon, its "
             "ancestors and its descendants BEFORE the hold — the same list as proc.kill. "
             "Realtime is not a choice. raise/lower move one step and never past high.",
    ),
]
