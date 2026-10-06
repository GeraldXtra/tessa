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

WHAT IS REUSED FROM safeproc NOW (2026-09-11, the process-control round). Its
WALK — `ancestry()` and `owns()` over one `ProcInfo` table taken ONCE — is
imported and used as-is to answer the question that keeps Tessa alive: is this
pid ME, something I am running INSIDE, or something running UNDER me? The table
is built from psutil in ~50 ms (`snapshot()` below) instead of safeproc's 3.5 s
CIM query, in safeproc's own shape, so the walk logic is shared and the latency
is not. The kill itself is `psutil.Process(pid).kill()`: a handle opened by PID,
with psutil's own create-time check refusing a pid that has been reused since
he read it. There is no `taskkill` in this file any more, and there never was
an image-name switch.

THE REFUSE LIST — a refusal, never a hold, no tier or yes reaches past it:
  * pid 0 and pid 4, BY NUMBER, before any lookup (System Idle Process, System);
  * every name in `CRITICAL` (smss, csrss, wininit, winlogon, services, lsass,
    svchost, dwm ...) — killing one is a bugcheck, not an error;
  * THE DAEMON ITSELF: this process, and the pid `runtime.json` names;
  * its ANCESTORS (the PowerShell and terminal it runs inside — ending them ends
    her);
  * its DESCENDANTS (her Chrome, her helper shells): `browser.close` and
    `win.close` exist for those.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psutil

from .base import ToolError, ToolHold

if TYPE_CHECKING:  # pragma: no cover
    from scripts.safeproc import ProcInfo

#: Killing any of these takes Windows down with it — `lsass` and `csrss` are an
#: instant bugcheck, not an error message. No tier and no confirmation reaches
#: past this list; it is a `never`, in the permissions.yaml sense.
CRITICAL = {
    "system", "system idle process", "registry", "smss.exe", "csrss.exe",
    "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe", "lsm.exe",
    "svchost.exe", "fontdrvhost.exe", "dwm.exe",
}

#: Refused BY NUMBER, before any name lookup, so a lookup that fails or lies
#: cannot let them through: 0 is the System Idle Process, 4 is System.
PROTECTED_PIDS: frozenset[int] = frozenset({0, 4})

#: pid -> (name, create_time, pinned_at). Written when a kill HOLDS, read when
#: his "yes" comes back. The number he confirmed is only the number; what he
#: was TOLD was "4242 is notepad.exe". If 4242 belongs to a different process by
#: the time he answers, the kill is refused rather than landing on it.
_PINS: dict[int, tuple[str, float, float]] = {}
_PIN_TTL_S = 30 * 60.0


def _safeproc():
    """
    `scripts/safeproc.py`, imported lazily. The repo root is on `sys.path` in
    the daemon (server.py) and in every test; added here only if it is not.
    """
    try:
        from scripts import safeproc
    except ImportError:
        root = str(Path(__file__).resolve().parents[2])
        if root not in sys.path:
            sys.path.insert(0, root)
        from scripts import safeproc
    return safeproc


def snapshot() -> dict[int, ProcInfo]:
    """
    The process table, ONCE, in safeproc's `ProcInfo` shape — from psutil.

    Taken once and reused for the whole decision, for the reason safeproc gives:
    a table that shifts under the walk turns a known process into an
    unattributable one. A parent that STARTED AFTER its child is a reused pid,
    not a parent, so its link is severed (ppid 0) and the walk stops there
    instead of wandering into an unrelated process.
    """
    sp = _safeproc()
    raw: dict[int, tuple[int, str, float]] = {}
    for p in psutil.process_iter(["pid", "ppid", "name", "create_time"]):
        try:
            info = p.info
            raw[int(info["pid"])] = (int(info["ppid"] or 0), info["name"] or "?",
                                     float(info["create_time"] or 0.0))
        except (psutil.NoSuchProcess, psutil.AccessDenied, TypeError, ValueError):
            continue
    table: dict[int, ProcInfo] = {}
    for pid, (ppid, name, created) in raw.items():
        parent = raw.get(ppid)
        if ppid == pid or (parent is not None and created > 0 and parent[2] > created):
            ppid = 0
        table[pid] = sp.ProcInfo(pid=pid, ppid=ppid, name=name, created=str(created))
    return table


def _daemon_pid(table: dict[int, ProcInfo]) -> int | None:
    """
    The pid `runtime.json` names, if that process is alive in `table`.

    This is what makes "do not kill Tessa" hold from OUTSIDE the daemon too: a
    test or a proof runs in its own process, and the daemon the surfaces are
    connected to is still protected because the file says which one it is.
    Inside the daemon it is simply `os.getpid()` again.
    """
    try:
        from core.security.runtime import runtime_path

        data = json.loads(runtime_path().read_text(encoding="utf-8"))
        pid = int(data.get("pid", 0))
    except Exception:  # noqa: BLE001
        return None
    return pid if pid in table else None


def protection(pid: int, table: dict[int, ProcInfo] | None = None) -> tuple[str, str] | None:
    """
    Why `pid` must not be ended or re-prioritised — (reason, alternative) —
    or None when it may be. A refusal, never a hold: no confirmation and no
    approval reaches past a non-None answer.

    Read-only. Shared by `kill` here and by `system.process.priority`
    (core/system/abilities/process.py), so the list exists once.
    """
    if pid in PROTECTED_PIDS:
        what = "System" if pid == 4 else "the System Idle Process"
        return (f"pid {pid} is {what}, a Windows core process",
                "Ending it would take the machine down. I will not do that one.")
    table = table if table is not None else snapshot()
    info = table.get(pid)
    name = info.name if info is not None else None
    if name is not None and name.lower() in CRITICAL:
        return (f"{name} is a Windows core process",
                "Ending it would take the machine down. I will not do that one.")

    sp = _safeproc()
    me = os.getpid()
    daemon = _daemon_pid(table)
    anchors = {me} | ({daemon} if daemon is not None else set())
    if pid in anchors:
        return ("that process id is me", "Say stop the daemon if that is what you want.")
    for anchor in anchors:
        for up in sp.ancestry(anchor, table)[1:]:
            if up.pid == pid:
                return (f"{up.name} at {pid} is what I am running inside — ending it ends me",
                        "Close it yourself if you mean to stop me.")
    if daemon is not None:
        ours, trail = sp.owns(pid, {daemon}, table)
        if ours:
            return (f"{name or pid} at {pid} is running under me: {trail}",
                    "Say close the browser if it is Chrome, or close it by its window.")
    return None


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

    # ⚠⚠ THE REFUSE LIST, FIRST. Not a hold — a refusal. pid 0/4 by number,
    # Windows core names, the daemon itself, what it runs inside, what runs
    # under it. One snapshot for the whole decision (safeproc's rule).
    why = protection(pid)
    if why is not None:
        raise ToolError(*why)

    try:
        p = psutil.Process(pid)
        name = p.name()
        born = float(p.create_time())
    except psutil.NoSuchProcess:
        raise ToolError(f"there is no process {pid}",
                        "Say find, and the name, and I will read you the live ones.") from None
    except psutil.AccessDenied:
        raise ToolError(f"Windows will not tell me what {pid} is",
                        "It is probably a system process. Leave it.") from None

    chain = ancestry_of(pid)
    now = time.monotonic()
    for stale in [k for k, v in _PINS.items() if now - v[2] > _PIN_TTL_S]:
        _PINS.pop(stale, None)
    if not confirmed:
        # PIN WHAT HE IS ABOUT TO BE TOLD. His "yes" confirms the number; the
        # number is only safe if it still means this process when he answers.
        _PINS[pid] = (name, born, now)
        raise ToolHold(f"{pid} is {name}" + (f", launched by {chain.split(' <- ')[1]}" if " <- " in chain else ""))

    pinned = _PINS.pop(pid, None)
    if pinned is not None and (pinned[0] != name or abs(pinned[1] - born) > 1e-3):
        raise ToolError(f"{pid} is not the {pinned[0]} I told you about — it is {name} now",
                        "Say find, and the name, and I will read you the live numbers.")

    # ⚠ BY PID, BY HANDLE. `psutil.Process.kill` opens the process by its pid
    # and TerminateProcess-es it, after psutil's own check that the pid still
    # belongs to the process this object was built from. No image name is
    # involved anywhere on this path, and nothing else is ended.
    try:
        p.kill()
        p.wait(timeout=5)
    except psutil.NoSuchProcess:
        pass    # it ended between the hold and the yes; that is the outcome he asked for
    except psutil.AccessDenied:
        raise ToolError(f"{name} at {pid} would not end",
                        "It may need administrator rights. Try it from Task Manager.") from None
    except psutil.TimeoutExpired:
        raise ToolError(f"{name} at {pid} is not ending",
                        "It may be stuck in the kernel. Try it from Task Manager.") from None
    if psutil.pid_exists(pid):
        try:
            if abs(float(psutil.Process(pid).create_time()) - born) <= 1e-3:
                raise ToolError(f"{name} at {pid} would not end",
                                "It may need administrator rights. Try it from Task Manager.")
        except psutil.Error:
            pass
    return {"pid": pid, "name": name, "chain": chain}
