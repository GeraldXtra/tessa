"""
core/system/abilities/power.py — shut down, restart, hibernate, log off (RED,
with a CANCELLABLE DELAY), the cancel that aborts them (green), and a read of
what is pending (green). Sleep and lock are NOT here: `sys.sleep` and
`sys.lock` (core/tools/sysctl.py, green under `system.control`) already exist
and are reused — one act, one tool.

    system.power.shutdown    red    system.shutdown   shutdown.exe /s /t 0, after the delay
    system.power.restart     red    system.shutdown   shutdown.exe /r /t 0, after the delay
    system.power.hibernate   red    system.shutdown   shutdown.exe /h,      after the delay
    system.power.logoff      red    system.shutdown   shutdown.exe /l,      after the delay
    system.power.cancel      green  system.control    timer.cancel() + shutdown.exe /a
    system.power.status      green  system.status     what is pending, and when

────────────────────────────────────────────────────────────────────────────────
⚠⚠ WHY POWER IS DIFFERENT: THERE IS NOTHING TO FREEZE, SO THE SAFEGUARD IS TIME

`fs.delete` freezes its path and `proc.kill` freezes its pid, because the
danger there is an approved action landing on a different target. A shutdown
has no target: it is aimed at the whole machine, and the tool NAME is the
action (four capabilities, not one with an `action` parameter), so a card
cannot be retargeted because there is nothing on it to retarget. The danger is
different — once it fires it cannot be stopped, and every unsaved document on
the machine is lost. A mis-approval at 2am, or a "shut down in an hour" that
he forgot about, takes the machine down mid-work.

So an APPROVED shutdown does not execute. It is SCHEDULED, `DEFAULT_DELAY_S`
seconds out (sixty; ten seconds to an hour if he names a time), she says when,
and until that moment "cancel the shutdown" — or "abort", or "don't shut
down" — undoes it completely. The delay is FROZEN on the card: an edited
approval may not shorten it, and the floor (`MIN_DELAY_S`) is enforced by the
parameter itself, so an instant shutdown is impossible by shape rather than by
policy.

⚠ THE TIMER IS IN-PROCESS, NOT `shutdown.exe /t <delay>`, AND THAT IS DELIBERATE

`shutdown.exe /s /t 60` looks like the obvious mechanism, and it was the first
draft. Three things are wrong with it for THIS purpose:

  1. `/t` greater than zero IMPLIES `/f` (shutdown.exe's own help says so):
     every application is force-closed at fire time with no chance to prompt
     for unsaved work. The unsaved-work loss is the exact danger this delay
     exists to prevent, so the mechanism must not reintroduce it at the end of
     the window. Firing `shutdown.exe /s /t 0` (no `/f`, none implied) lets a
     program with an unsaved document veto, the same courtesy `sys.sleep` and
     `winman.close` extend.
  2. `/h` and `/l` take no `/t` at all, so hibernate and log off would have
     needed a second, different timer anyway. One timer, one cancel, one
     proof.
  3. A Windows-owned countdown outlives the daemon. With the timer in here, a
     daemon crash or restart is an implicit cancel: nothing powers off unless
     the daemon that was told to do it is still alive to do it. Fail-safe in
     the direction that keeps the machine up.

The cancel still runs `shutdown.exe /a` after clearing the timer, so a countdown
he started himself at a prompt ("shutdown /s /t 600") is aborted by the same
sentence. Exit code 1116 from `/a` means nothing was counting down; that is
the expected answer when only Tessa's timer existed, or nothing did.

⚠⚠ NOTHING SCHEDULES WITHOUT THE REAL APPROVAL FLAG — TWO INDEPENDENT CHECKS

The framework (core/system/capability.py) will not call a red `run` unless
`Executor.execute_approved` passed `_approved_by_surface=True` — the card path,
after the forgeable-flag strip. That value is THREADED into `run` by signature,
never fabricated, and `schedule()` refuses without it: the same shape as
`core/system/schedule.py::enqueue`, so "is pending" and "he approved it on a
card" are one statement. A model-built call carrying the flag in its args has
it stripped and logged before dispatch; the red gate then raises a card and
stops. The fence (external content in context) refuses the red action before
the card is ever raised.

EVERY SUBPROCESS IS A FIXED ARGUMENT VECTOR, `shell=False`. The five argv
tuples below are constants; the path to shutdown.exe is built from
`%SystemRoot%`, never from PATH. No parameter of any capability here reaches an
argv — `delay_s` is an integer the timer sleeps on, and it never touches the
command line (the command line has `/t 0`).

ONE SEAM. `_run_shutdown_exe` is the only place shutdown.exe is invoked, and
`_TIMER_FACTORY` is the only place a timer is made. A proof swaps both for
recorders and proves the card, the delay and the cancel without the machine
ever going down.

THE AUDIT. SCHEDULED, FIRED, FIRE-FAILED and CANCELLED are written with the
REQUEST'S true actor (the provenance the card showed) through a reporter the
executor binds at construction (`bind_reporter`) — the fire happens on a timer
thread after the approval turn has returned, with no call in flight, so it
cannot be logged by the dispatch path. FIRED is written BEFORE the call, for
the same reason `execute_approved` audits before acting: after a real shutdown
there is no daemon left to write the line.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from core.system.capability import Capability, Param

#: Sixty seconds: long enough to say "cancel the shutdown" twice with a Whisper
#: miss in between, short enough that "shut down" still means soon.
DEFAULT_DELAY_S = 60
#: The floor. Below this the cancel window is not a window. Enforced by the
#: parameter (`lo=`), so no edit, no forge and no direct call gets under it.
MIN_DELAY_S = 10
#: The ceiling. A shutdown scheduled hours out is the "fires mid-work, long
#: forgotten" case; past an hour he asks again later.
MAX_DELAY_S = 3600

ACTIONS: tuple[str, ...] = ("shutdown", "restart", "hibernate", "logoff")

#: What she calls each action. Spoken, audited, never executed.
_SAY = {"shutdown": "shutdown", "restart": "restart", "hibernate": "hibernation",
        "logoff": "log off"}
_VERB = {"shutdown": "shut down", "restart": "restart", "hibernate": "hibernate",
         "logoff": "log off"}

#: FIXED ARGUMENT VECTORS. No `/f` anywhere: a program with unsaved work may
#: veto, which is the whole point of the delay. `/t 0` is spelled out for the
#: two switches that accept it so nobody later "helpfully" adds a delay there
#: and gets `/f` implied for free.
_ARGV: dict[str, tuple[str, ...]] = {
    "shutdown": ("/s", "/t", "0"),
    "restart": ("/r", "/t", "0"),
    "hibernate": ("/h",),
    "logoff": ("/l",),
    "abort": ("/a",),
}

#: shutdown.exe's exit code for "/a with nothing counting down".
_ERROR_NO_SHUTDOWN_IN_PROGRESS = 1116


def _shutdown_exe() -> str:
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    return os.path.join(root, "System32", "shutdown.exe")


def _argv(key: str) -> list[str]:
    return [_shutdown_exe(), *_ARGV[key]]


def _run_shutdown_exe(argv: list[str]) -> subprocess.CompletedProcess:
    """
    THE ONE SEAM. Every shutdown.exe invocation in this module comes through
    here with a constant argv; a proof replaces this function with a recorder
    and nothing below it ever runs.
    """
    return subprocess.run(argv, shell=False, capture_output=True, text=True,
                          timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


#: How a delay becomes a timer. `threading.Timer` in the daemon; a proof
#: injects a fake that records the interval and fires on command.
_TIMER_FACTORY: Callable[..., Any] = threading.Timer

#: `Executor._log`, bound by the executor at construction. None until then —
#: and a power action with no reporter still schedules, fires and cancels
#: correctly; it is the audit line that is lost, which is why the executor
#: binds it unconditionally.
_REPORT: Callable[..., None] | None = None


def bind_reporter(report: Callable[..., None] | None) -> None:
    """`report(verb, tool, summary, tier, *, actor)` — the executor's `_log`."""
    global _REPORT
    _REPORT = report


def _report(verb: str, tool: str, summary: str, tier: str, actor: str) -> None:
    if _REPORT is None:
        return
    try:
        _REPORT(verb, tool, summary, tier, actor=actor)
    except Exception:  # noqa: BLE001
        pass    # an audit failure must never change what happens to the machine


@dataclass
class Pending:
    """The one power action waiting for its moment."""

    action: str
    delay_s: int
    request_id: str          # the approval that scheduled it — provenance, on the record
    actor: str               # who INITIATED the action, as the card showed
    scheduled_at: float
    fire_at: float
    timer: Any = field(default=None, repr=False)

    @property
    def tool(self) -> str:
        return f"system.power.{self.action}"

    @property
    def clock(self) -> str:
        return datetime.fromtimestamp(self.fire_at).strftime("%H:%M:%S")

    def seconds_left(self, now: float | None = None) -> int:
        t = time.time() if now is None else now
        return max(0, int(round(self.fire_at - t)))


_LOCK = threading.Lock()
_PENDING: Pending | None = None


def pending() -> Pending | None:
    with _LOCK:
        return _PENDING


def _human(seconds: int) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s} seconds"
    if s % 3600 == 0:
        h = s // 3600
        return "an hour" if h == 1 else f"{h} hours"
    if s % 60 == 0:
        m = s // 60
        return "a minute" if m == 1 else f"{m} minutes"
    m, rem = divmod(s, 60)
    return f"{m} minute{'s' if m != 1 else ''} {rem} seconds"


# ─────────────────────────────────────────────────────────────────────────────
# schedule / fire / cancel
# ─────────────────────────────────────────────────────────────────────────────

def schedule(action: str, delay_s: int, *, approved: bool, request_id: str = "",
             actor: str = "schedule") -> Pending:
    """
    Put ONE approved power action on the timer.

    ⚠ `approved` IS NOT A COURTESY. The only caller able to pass True is the
    red capability's `run`, which the framework calls only from
    `Executor.execute_approved`, which passes the real flag by signature after
    stripping any forged one. Refused here as well, so a direct call, a
    scheduled trigger or anything else arrives with False and stops.
    """
    from core.tools.base import ToolError

    global _PENDING
    if action not in ACTIONS:
        raise ToolError(f"{action!r} is not a power action I have",
                        "Shut down, restart, hibernate or log off.")
    if not approved:
        raise ToolError(f"a {_SAY[action]} cannot be scheduled without your approval",
                        "Approve it on the card and I will set the timer.")
    delay = int(delay_s)
    if delay < MIN_DELAY_S or delay > MAX_DELAY_S:
        raise ToolError(f"{delay} seconds is outside the {MIN_DELAY_S}-{MAX_DELAY_S} second window",
                        "Ten seconds to an hour.")
    with _LOCK:
        if _PENDING is not None:
            have = _PENDING
            raise ToolError(f"a {_SAY[have.action]} is already scheduled for {have.clock}",
                            "Say cancel the shutdown first, then ask again.")
        now = time.time()
        item = Pending(action=action, delay_s=delay, request_id=str(request_id or ""),
                       actor=str(actor or "schedule"), scheduled_at=now, fire_at=now + delay)
        timer = _TIMER_FACTORY(delay, _fire, args=(item,))
        try:
            timer.daemon = True
        except Exception:  # noqa: BLE001
            pass
        item.timer = timer
        _PENDING = item
        timer.start()
    _report("SCHEDULED", item.tool,
            f"requestId={item.request_id} {_SAY[action]} in {delay}s, at {item.clock} — "
            f"cancellable until then (say cancel the shutdown)", "red", item.actor)
    return item


def _fire(item: Pending) -> None:
    """The timer's callback. A cancelled or superseded item is a no-op."""
    global _PENDING
    with _LOCK:
        if _PENDING is not item:
            return
        _PENDING = None
    argv = _argv(item.action)
    # AUDIT BEFORE ACTING. After a real shutdown nothing is left to write.
    _report("FIRED", item.tool,
            f"requestId={item.request_id} {_SAY[item.action]} after {item.delay_s}s: "
            f"shutdown.exe {' '.join(argv[1:])}", "red", item.actor)
    try:
        result = _run_shutdown_exe(argv)
    except Exception as exc:  # noqa: BLE001
        _report("FIRE-FAILED", item.tool,
                f"requestId={item.request_id} {type(exc).__name__}: {exc}"[:200], "red", item.actor)
        return
    if getattr(result, "returncode", 0) != 0:
        out = (getattr(result, "stderr", "") or getattr(result, "stdout", "") or "").strip()
        _report("FIRE-FAILED", item.tool,
                f"requestId={item.request_id} shutdown.exe exit {result.returncode}: {out[:160]}",
                "red", item.actor)


def cancel(provenance: str = "schedule") -> dict[str, Any]:
    """
    GREEN. Abort the pending power action, if any, and any countdown Windows
    itself is running. Cancelling is the safe direction, so any origin may.
    """
    global _PENDING
    with _LOCK:
        item = _PENDING
        _PENDING = None
    if item is not None and item.timer is not None:
        try:
            item.timer.cancel()
        except Exception:  # noqa: BLE001
            pass
    # Belt and braces: a countdown started outside this daemon.
    windows_aborted = False
    note = ""
    try:
        r = _run_shutdown_exe(_argv("abort"))
        windows_aborted = getattr(r, "returncode", 1) == 0
        if not windows_aborted and getattr(r, "returncode", 0) != _ERROR_NO_SHUTDOWN_IN_PROGRESS:
            note = (getattr(r, "stderr", "") or getattr(r, "stdout", "") or "").strip()[:120]
    except Exception as exc:  # noqa: BLE001
        note = f"{type(exc).__name__}: {exc}"[:120]
    if item is not None:
        _report("CANCELLED", item.tool,
                f"requestId={item.request_id} {_SAY[item.action]} due at {item.clock} "
                f"({item.seconds_left()}s left) cancelled by {provenance}"
                + ("; shutdown.exe /a also aborted a Windows countdown" if windows_aborted else ""),
                "red", item.actor)
    if item is None and not windows_aborted:
        return {"cancelled": False, "action": "", "was_due": "", "windows_aborted": False,
                "verdict": "Nothing was scheduled, Emperor. The machine stays up."}
    what = _SAY[item.action] if item is not None else "shutdown Windows was counting down"
    return {"cancelled": True, "action": item.action if item is not None else "windows",
            "was_due": item.clock if item is not None else "", "windows_aborted": windows_aborted,
            "verdict": f"Cancelled, Emperor. The {what} will not happen."}


def status() -> dict[str, Any]:
    """GREEN, read-only: what is pending and when."""
    item = pending()
    if item is None:
        return {"pending": False, "action": "", "at": "", "left": 0,
                "summary": "Nothing is scheduled, Emperor. The machine stays up."}
    left = item.seconds_left()
    return {"pending": True, "action": item.action, "at": item.clock, "left": left,
            "summary": (f"A {_SAY[item.action]} is due at {item.clock}, Emperor — "
                        f"{_human(left)} away. Say cancel the shutdown to stop it.")}


# ─────────────────────────────────────────────────────────────────────────────
# the four red runs — identical shape, distinct NAMES (the name is the action)
# ─────────────────────────────────────────────────────────────────────────────

def _red_run(action: str) -> Callable[..., dict[str, Any]]:
    def run(delay_s: int = DEFAULT_DELAY_S, provenance: str = "schedule",
            _approved_by_surface: bool = False, _request_id: str = "") -> dict[str, Any]:
        item = schedule(action, delay_s, approved=bool(_approved_by_surface),
                        request_id=_request_id, actor=provenance)
        return {"action": action, "delay_s": item.delay_s, "at": item.clock,
                "in": _human(item.delay_s),
                "verdict": (f"{_VERB[action].capitalize()} in {_human(item.delay_s)}, Emperor — "
                            f"at {item.clock}. Say cancel the shutdown to stop it.")}

    run.__name__ = action
    run.__qualname__ = action
    run.__doc__ = (f"RED. Schedule a {_SAY[action]} after `delay_s` (cancellable). "
                   f"Runs only from the approval card; `_approved_by_surface` is the real, "
                   f"threaded value and `schedule()` refuses without it.")
    return run


shutdown = _red_run("shutdown")
restart = _red_run("restart")
hibernate = _red_run("hibernate")
logoff = _red_run("logoff")

_DELAY_PARAM = Param("delay_s", int, default=DEFAULT_DELAY_S, lo=MIN_DELAY_S, hi=MAX_DELAY_S,
                     doc="Ten seconds to an hour. Sixty if you do not say.")


def _red(action: str, run: Callable[..., dict[str, Any]], phrasings: tuple[str, ...]) -> Capability:
    return Capability(
        name=f"system.power.{action}", capability="system.shutdown", tier="red",
        run=run,
        params=(_DELAY_PARAM,),
        # ⚠ THE DELAY IS FROZEN. The card corrects wording; this action has
        # none. An edited approval may not shorten the cancel window.
        frozen=("delay_s",),
        phrasings=phrasings,
        success="{verdict}",
        failure="I did not set it, sir. {reason} {alternative}",
        audit=f"{action.upper()} in {{delay_s}}s (cancellable)",
        hold=f"{_VERB[action]} the machine in {{delay_s}} seconds",
        note=(f"RED, card-only, and NOT instant: approval SCHEDULES the {_SAY[action]} "
              f"{DEFAULT_DELAY_S}s out by default ({MIN_DELAY_S}-{MAX_DELAY_S}s if he names a "
              f"time, delay frozen on the card) and system.power.cancel aborts it until it "
              f"fires. Mechanism at fire time: shutdown.exe {' '.join(_ARGV[action])} — fixed "
              f"argv, shell=False, no /f, so unsaved work can still veto. The timer is in-process: "
              f"a daemon restart is an implicit cancel."),
    )


CAPABILITIES = [
    Capability(
        name="system.power.cancel", capability="system.control", tier="green",
        run=cancel,
        phrasings=("cancel the shutdown", "abort the restart", "don't shut down", "abort"),
        success="{verdict}",
        audit="CANCEL pending power action",
        note="Green: cancelling is the safe direction, so it never holds and any origin may. "
             "Clears Tessa's own timer, then runs shutdown.exe /a for a countdown started "
             "outside the daemon (exit 1116 = nothing was pending, expected).",
    ),
    Capability(
        name="system.power.status", capability="system.status", tier="green",
        run=status,
        phrasings=("is a shutdown pending", "is anything scheduled to shut down", "power status"),
        success="{summary}",
        audit="read pending power action",
        note="Read-only.",
    ),
    _red("shutdown", shutdown, ("shut down", "shut down in ten minutes", "power off the computer")),
    _red("restart", restart, ("restart", "reboot the machine", "restart in five minutes")),
    _red("hibernate", hibernate, ("hibernate", "hibernate the laptop", "hibernate in twenty minutes")),
    _red("logoff", logoff, ("log off", "sign out", "log me off in two minutes")),
]
