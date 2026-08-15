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


def kill_if_ours(pid: int, roots: set[int], table: dict[int, ProcInfo] | None = None) -> str:
    """Kill only on proven ancestry. Returns what was decided and why."""
    table = table or snapshot()
    ours, trail = owns(pid, roots, table)
    if not ours:
        return f"LEFT   {pid}: ancestry does not reach a process I started | {trail}"
    subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                   capture_output=True, text=True, timeout=30)
    return f"KILLED {pid}: {trail}"
