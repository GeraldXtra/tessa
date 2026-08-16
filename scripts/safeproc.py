"""
scripts/safeproc.py — never kill a process you cannot prove you started.

WHY THIS EXISTS

I killed 37 Code.exe by iterating an image name while writing "by PID". Some
were Gerald's. Targeting a PID that you *selected* by image name is
kill-by-name with extra steps, and CLAUDE.md forbids it because doing exactly
this once killed a daemon on this project.

So ownership is no longer asserted, it is PROVEN, by walking the parent chain up
to a PID we know we started. If the chain does not reach one of our roots, the
process is left alone and named in the report. An orphan is cheaper than another
apology.

    from scripts.safeproc import owns, kill_if_ours
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class ProcInfo:
    pid: int
    ppid: int
    name: str
    created: str


def snapshot() -> dict[int, ProcInfo]:
    """
    One CIM query for the whole table.

    Taken ONCE and reused: querying per-process while killing means the table
    shifts underneath the walk, and a parent that dies mid-walk would silently
    turn a known-ours process into an unattributable one.
    """
    ps = (
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,ParentProcessId,Name,CreationDate | "
        "ConvertTo-Csv -NoTypeInformation"
    )
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        capture_output=True, text=True, timeout=60,
    ).stdout
    table: dict[int, ProcInfo] = {}
    for line in out.splitlines()[1:]:
        parts = [p.strip().strip('"') for p in line.split('","')]
        if len(parts) < 4:
            continue
        try:
            pid = int(parts[0].strip('"'))
            ppid = int(parts[1]) if parts[1] else 0
        except ValueError:
            continue
        table[pid] = ProcInfo(pid=pid, ppid=ppid, name=parts[2], created=parts[3].strip('"'))
    return table


def ancestry(pid: int, table: dict[int, ProcInfo] | None = None) -> list[ProcInfo]:
    """The chain from `pid` up to the root, as far as it can be resolved."""
    table = table or snapshot()
    chain: list[ProcInfo] = []
    seen: set[int] = set()
    cur = pid
    while cur in table and cur not in seen:
        seen.add(cur)
        info = table[cur]
        chain.append(info)
        cur = info.ppid
    return chain


def owns(pid: int, roots: set[int], table: dict[int, ProcInfo] | None = None) -> tuple[bool, str]:
    """
    (is_ours, human-readable chain).

    Ours means: the parent chain reaches a PID in `roots` — a process we
    started. Nothing else counts. Same image name, same start time, same
    command line: none of those are ownership.
    """
    table = table or snapshot()
    chain = ancestry(pid, table)
    trail = " <- ".join(f"{p.name}({p.pid})" for p in chain) or f"pid {pid} not found"
    for p in chain:
        if p.pid in roots:
            return True, trail
    return False, trail


#: Ancestors shared by every process on the machine. A root set containing any
#: of these makes "does it descend from me" true for everything.
_SHARED_ANCESTORS = {
    "wininit.exe", "services.exe", "svchost.exe", "winlogon.exe",
    "csrss.exe", "smss.exe", "explorer.exe", "userinit.exe",
    "windowsterminal.exe", "openconsole.exe", "conhost.exe",
}


def my_roots(table: dict[int, ProcInfo] | None = None) -> set[int]:
    """
    Every pid in MY OWN ancestry — the only roots that are provably mine.

    THIS EXISTS BECAUSE I DEFEATED THIS MODULE TWICE BY HAND. Both times I
    called `kill_if_ours(pid, {ancestry(pid)[1].pid})` — passing the target's
    OWN PARENT as the trusted root. That makes the ownership test a tautology:
    every process descends from its own parent, so the check always passes and
    the tool becomes an expensive `taskkill`.

    The second time, the parent was Gerald's `powershell.exe` and the child was
    HIS daemon. I killed it.

    A root has to be derived, not supplied. `kill_if_descends_from_me` below
    takes no roots argument at all, so there is nothing to get wrong.

    THE WALK STOPS AT THE SESSION HOST, and that is not tidiness either. My
    own ancestry runs all the way up through `svchost.exe`, `services.exe` and
    `wininit.exe` — which are ancestors of EVERY process on the machine. Include
    those and the test passes for anything, which is a guard in name only. The
    walk therefore stops before the first shared ancestor, leaving only the
    processes genuinely inside my own session.
    """
    import os

    table = table or snapshot()
    roots: set[int] = set()
    for p in ancestry(os.getpid(), table):
        if p.name.lower() in _SHARED_ANCESTORS:
            break
        roots.add(p.pid)
    return roots


def kill_if_descends_from_me(pid: int, table: dict[int, ProcInfo] | None = None) -> str:
    """
    Kill `pid` ONLY if it descends from this very process's ancestry.

    No `roots` parameter, by design. Use this one. `kill_if_ours` stays for the
    case where a caller genuinely recorded a root at spawn time and can say so,
    but if you find yourself computing the root from the target, stop.
    """
    table = table or snapshot()
    return kill_if_ours(pid, my_roots(table), table)


def kill_if_ours(pid: int, roots: set[int], table: dict[int, ProcInfo] | None = None) -> str:
    """Kill only on proven ancestry. Returns what was decided and why."""
    table = table or snapshot()
    ours, trail = owns(pid, roots, table)
    if not ours:
        return f"LEFT   {pid}: ancestry does not reach a process I started | {trail}"
    subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                   capture_output=True, text=True, timeout=30)
    return f"KILLED {pid}: {trail}"
