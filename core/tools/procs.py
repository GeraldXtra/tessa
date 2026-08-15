"""
core/tools/procs.py — processes: list, top, find, and kill BY PID.

THIS FILE IS WRITTEN AGAINST A SPECIFIC INCIDENT. Earlier in this project I
killed 37 of Gerald's `Code.exe` processes by iterating an image name while
writing "by PID" in the report. Some of them were his work, not mine. The rule
that came out of it is in CLAUDE.md and it is absolute:

    A PID SELECTED BY IMAGE NAME IS KILL-BY-NAME WITH EXTRA STEPS.

So `kill()` below takes an integer PID and nothing else. There is no `name`
parameter, no `all=True`, no tree kill. `find()` exists to let him SEE the
candidates and choose one, and the choosing is his — she reads the list back
with PIDs and waits.

WHY `scripts/safeproc.py` IS NOT CALLED HERE, HAVING BEEN WRITTEN FOR EXACTLY
THIS INCIDENT. Its `kill_if_ours` answers "did I start this?" — the right
question when I am cleaning up after myself, and the wrong one when the OWNER
is deliberately ending one of his own processes, where the authority is his
confirmation. Its `snapshot()` was then used just for the parent chain she
speaks, and measured at ~3.5 s (one PowerShell CIM query), which is dead air in
the middle of a spoken hold. `ancestry_of` below walks psutil instead, in 50 ms.

safeproc remains the ONLY route by which I kill anything, which is what
CLAUDE.md's rule is actually about. What survives from it here is its principle,
enforced by the signature: no name, no tree, no list.
"""

from __future__ import annotations

import subprocess
from typing import Any

import psutil

from .base import ToolError, ToolHold

#: Killing any of these takes Windows down with it — `lsass` and `csrss` are an
#: instant bugcheck, not an error message. No tier and no confirmation reaches
#: past this list; it is a `never`, in the permissions.yaml sense.
CRITICAL = {
    "system", "system idle process", "registry", "smss.exe", "csrss.exe",
    "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe", "lsm.exe",
    "svchost.exe", "fontdrvhost.exe", "dwm.exe",
}


def _rows() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            out.append({"pid": p.info["pid"], "name": p.info["name"] or "?",
                        "rss": int(p.info["memory_info"].rss) if p.info["memory_info"] else 0})
        except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
            continue
    return out


def list_processes(limit: int = 200) -> dict[str, Any]:
    rows = _rows()
    return {"n": len(rows), "rows": rows[:limit]}


def top(by: str = "memory", n: int = 5) -> dict[str, Any]:
    """
    Heaviest first.

    CPU IS SAMPLED, NOT READ. `cpu_percent()` with no interval returns the
    average since the process started, which on a machine up for six hours is
    a number that cannot move and tells him nothing about what is eating his
    two cores RIGHT NOW. So the first call primes and a second call 300 ms
    later reads the delta. It costs 300 ms and it is the difference between a
    real answer and a plausible one.
    """
    key = "cpu" if str(by).lower().startswith("cpu") else "memory"
    n = max(1, min(int(n), 15))

    if key == "cpu":
        import time

        procs = []
        for p in psutil.process_iter(["pid", "name"]):
            try:
                p.cpu_percent(None)
                procs.append(p)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        time.sleep(0.3)
        rows = []
        for p in procs:
            try:
                nm = p.name()
                # SYSTEM IDLE PROCESS IS EXCLUDED, and it is not cosmetic.
                # It reports the CPU that is doing NOTHING — measured at 278%
                # of 400% on this machine — so it tops the list every single
                # time and pushes the real answer down. "System Idle Process is
                # eating your CPU" is the exact opposite of the truth.
                if nm.lower() in ("system idle process", "system", "registry"):
                    continue
                rows.append({"pid": p.pid, "name": nm, "cpu": p.cpu_percent(None)})
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        rows.sort(key=lambda r: -r["cpu"])
        top_rows = rows[:n]
        head = ", ".join(f"{r['name']} at {r['cpu']:.0f} percent" for r in top_rows)
    else:
        rows = sorted(_rows(), key=lambda r: -r["rss"])
        top_rows = rows[:n]
        head = ", ".join(f"{r['name']} at {r['rss'] / 1e6:.0f} megabytes" for r in top_rows)

    return {"by": key, "n": len(top_rows), "rows": top_rows, "head": head or "nothing"}


def find(name: str) -> dict[str, Any]:
    """
    Show him the candidates WITH their PIDs. This is the tool that makes
    kill-by-PID usable in speech: he says "find chrome", she reads back
    "six of them, Emperor — 14284, 16820, ...", he says "kill 16820".
    """
    needle = str(name or "").strip().lower()
    if not needle:
        raise ToolError("no process name came through", "Say part of the name.")
    hits = [r for r in _rows() if needle in r["name"].lower()]
    hits.sort(key=lambda r: -r["rss"])
    return {"n": len(hits), "needle": name, "rows": hits[:12],
            "head": ", ".join(f"{r['name']} {r['pid']}" for r in hits[:4]) or "nothing",
            "heaviest": hits[0] if hits else None}


def ancestry_of(pid: int, depth: int = 4) -> str:
    """
    The parent chain, as one readable line, for the confirmation she speaks.

    PSUTIL, NOT `safeproc.snapshot()`, AND THE REASON IS LATENCY. safeproc takes
    ONE PowerShell `Get-CimInstance Win32_Process` snapshot so the process table
    cannot shift underneath a walk while it is killing things — correct for
    that job, and measured at 3.5 SECONDS on this machine. Inside a spoken
    hold that is dead air between "kill 19728" and her asking whether he is
    sure, on the one interaction where hesitation reads as the machine being
    broken. Measured: 4147 ms for the hold, of which ~3.5 s was this call.

    Nothing here kills, so the shifting-table problem does not apply: this walk
    only produces the words she says. safeproc remains the only route by which
    *I* kill anything, which is what CLAUDE.md's rule is about.
    """
    chain = []
    try:
        p = psutil.Process(int(pid))
        for _ in range(depth):
            chain.append(f"{p.name()}({p.pid})")
            parent = p.parent()
            if parent is None:
                break
            p = parent
    except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
        pass
    return " <- ".join(chain)


def kill(pid: int, confirmed: bool = False) -> dict[str, Any]:
    """
    One PID. Not a name, not a tree, not a list.

    Holds on the first ask and names what it is about to end, including the
    parent chain — "python.exe(7332) <- bash.exe(15248)" is how he tells his own
    daemon apart from mine before he agrees to it.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        raise ToolError(f"{pid!r} is not a process id",
                        "Say find, and the name, and I will read you the numbers.") from None

    try:
        p = psutil.Process(pid)
        name = p.name()
    except psutil.NoSuchProcess:
        raise ToolError(f"there is no process {pid}",
                        "Say find, and the name, and I will read you the live ones.") from None

    if name.lower() in CRITICAL:
        # Not a hold. A refusal — no confirmation makes this survivable.
        raise ToolError(f"{name} is a Windows core process",
                        "Ending it would take the machine down. I will not do that one.")

    if pid == psutil.Process().pid:
        raise ToolError("that process id is me",
                        "Say stop the daemon if that is what you want.")

    chain = ancestry_of(pid)
    if not confirmed:
        raise ToolHold(f"{pid} is {name}" + (f", launched by {chain.split(' <- ')[1]}" if " <- " in chain else ""))

    subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                   capture_output=True, text=True, timeout=30)
    gone = not psutil.pid_exists(pid)
    if not gone:
        raise ToolError(f"{name} at {pid} would not end",
                        "It may need administrator rights. Try it from Task Manager.")
    return {"pid": pid, "name": name, "chain": chain}
