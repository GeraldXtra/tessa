"""
core/system/ — the capability framework: how Tessa controls the machine.

WHAT THIS IS

The owner's end state is that Tessa can do EVERYTHING on this machine —
network, apps, files, settings, processes, power, screen, clipboard. Some of
that is destructive and irreversible. It is only safe to build because every
capability is BOUND TO THE TIER SYSTEM and to the approval card that cannot
be mislabeled or bypassed:

    green  — safe, reversible, runs unattended            (read state, volume)
    amber  — a state change; the owner confirms first     (radio on/off)
    red    — destructive or irreversible; the CARD, always (delete, shutdown)

This package does not invent a gate. A capability becomes an ordinary
`ToolSpec` in `core.tools.REGISTRY`, so `core/tools/_validate()`,
`Executor._dispatch_registry` (fence -> red gate -> hold -> run -> audit),
`Executor.execute_approved` (the card) and the forgeable-flag strip all apply
to it unchanged. What this package ADDS is that the tier is enforced inside
the handler as well, from the same guard, so an author cannot forget it.

THE PATTERN — one module, one permissions.yaml line

    # core/system/abilities/example.py
    from core.system.capability import Capability, Param

    def get_thing() -> dict:                 # returns FACTS; the spec speaks
        return {"level": 42}

    def set_thing(level: int) -> dict:       # typed args; never a command string
        ...
        return {"level": level, "was": 40}

    CAPABILITIES = [
        Capability(
            name="system.thing.get",         # the tool name the executor dispatches
            capability="system.status",      # the permissions.yaml KEY -> its tier
            tier="green",                    # what you expect; the guard CHECKS it
            run=get_thing,
            success="Thing is {level}, Emperor.",
            audit="thing get",
        ),
        Capability(
            name="system.thing.set", capability="system.control", tier="green",
            run=set_thing,
            params=(Param("level", int, lo=0, hi=100, doc="Zero to a hundred."),),
            success="Thing {level}, Emperor. It was {was}.",
            audit="thing set {level}",
        ),
    ]

    # core/config/permissions.yaml — the key must already be there, or:
    #   green:
    #     - system.status
    #     - system.control

Drop the module in `core/system/abilities/`, restart the daemon. It is
discovered, bound through the guard, merged into REGISTRY, validated, and
dispatched like every other tool.

TIER BINDING IS UNAVOIDABLE — three layers, all from the same source

  1. `bind()` asks `core.security.guard.Guard` (reading permissions.yaml)
     for the key's tier. Module says green, file says amber -> refuses to
     bind, daemon does not start. Key missing from the file -> the guard
     answers RED ("not listed ... treating as red until classified"), and
     `bound_specs()` refuses to register it, naming the missing line.
  2. If a spec were ever injected past that (a later hook, a test), it is
     STILL red: the bound handler will not run without
     `_approved_by_surface=True`, which only the card path supplies.
  3. `core/tools/_validate()` runs over the merged REGISTRY and refuses a
     tier that disagrees with permissions.yaml or a red tool that does not
     hold — the same check every hand-written tool passes.

HOW A FUTURE DESTRUCTIVE CAPABILITY DECLARES ITSELF — designed for, not built

The files example is not built; the power one IS (2026-09-11, with a
cancellable delay — read the module). Both show the interface: a red
capability is the SAME dataclass with a different key, and the card is
reached by the SAME executor code that gates `fs.delete` and `x.post` today.

    # core/system/abilities/files.py   (NOT BUILT THIS ROUND)
    Capability(
        name="system.files.delete",
        capability="fs.delete",              # permissions.yaml: red
        tier="red",
        run=recycle,                         # Recycle Bin only — invariant 6
        params=(Param("path", str, doc="The full path."),),
        mutating=True, target="path",        # protected-path rule applies
        success="Gone to the Recycle Bin, Emperor. {name}.",
        audit="RECYCLE {path}",
        hold="send {path} to the Recycle Bin",
    )

    # core/system/abilities/power.py   (BUILT 2026-09-11 — the real declaration)
    Capability(
        name="system.power.shutdown",
        capability="system.shutdown",        # permissions.yaml: red
        tier="red",
        run=shutdown,                        # approval SCHEDULES an in-process timer;
                                             # at fire time shutdown.exe /s /t 0 (no /f)
        params=(Param("delay_s", int, default=60, lo=10, hi=3600),),
        frozen=("delay_s",),                 # the card may not shorten the cancel window
        success="{verdict}",                 # "Shut down in a minute, Emperor — at
                                             #  14:02:10. Say cancel the shutdown to stop it."
        audit="SHUTDOWN in {delay_s}s (cancellable)",
    )

What happens to either, with NO framework change:

  * voice / typed / model -> `Executor._dispatch_registry` sees tier red,
    raises `evt.permission.request` (CONTRACT §4.1) with the call's TRUE
    origin, logs PENDING-APPROVAL, and returns the refusal. The handler is
    never called. Saying "yes" does nothing; repeating it raises another
    request.
  * a model's args carrying `_approved_by_surface: true` or `confirmed:
    true` are STRIPPED at dispatch and logged STRIPPED-FORGEABLE.
  * the owner approves on a surface -> `cmd.permission.respond` ->
    `Executor.execute_approved` claims the request atomically, re-strips,
    audits REQUESTED + APPROVED before acting, and calls the handler with
    `_approved_by_surface=True` by signature. Only then does `run` execute.
  * the handler itself refuses without that flag, so even a direct call
    outside the executor cannot delete or shut down.

An AMBER destructive-adjacent capability (`system.process.kill` by PID,
`system.files.move`) declares `tier="amber"`: the handler raises `ToolHold`
naming the specific target, the executor arms the confirmation ledger, and
his "yes" or exact repeat re-dispatches it with `confirmed=True`. A non-human
origin is refused until a job grant exists to check.

WHAT IS BUILT THIS ROUND — all green, plus one amber state change

    system.volume.get / .set / .mute      green   system.control   COM endpoint
    system.brightness.get / .set          green   system.control   WMI (reuses sysctl)
    system.network.status                 green   system.status    netsh + WinRT radios
    system.radio.set                      amber   system.radio     WinRT Radio.SetStateAsync
    system.app.launch                     green   app.launch       the 449-app index
    system.power.shutdown / .restart /
      .hibernate / .logoff                red     system.shutdown  timer, then shutdown.exe
                                                                   /s|/r|/h|/l (2026-09-11)
    system.power.cancel                   green   system.control   timer.cancel + shutdown.exe /a
    system.power.status                   green   system.status    read-only

Windows mechanisms are stdlib-only: ctypes COM, PowerShell 5.1 with a
constant script, `netsh` with a constant argv. No new dependency.
"""

from __future__ import annotations

from .capability import (PERMISSIONS, Capability, CapabilityError, Param, TierRuling,
                         bind, bound_specs, discover, resolve_tier)

__all__ = ["PERMISSIONS", "Capability", "CapabilityError", "Param", "TierRuling",
           "bind", "bound_specs", "discover", "resolve_tier"]
