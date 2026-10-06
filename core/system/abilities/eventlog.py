"""
core/system/abilities/eventlog.py — the last few entries of a Windows log.

GREEN under its own `system.eventlog` key rather than sharing one with the
inventory reads. It gets its own permission line because it is the most
sensitive read in the batch: event records carry usernames, paths, device
identifiers and remotely-influenced strings, and the owner should be able to
withdraw this one capability without losing the ability to ask what is
installed.

BOUNDED AT THE SOURCE. `count` is a declared parameter capped at 50 by the
framework and clamped again in the mechanism, and `wevtutil /rd:true` makes
those the most recent records. There is no path here that reads a whole log.

`log` IS A CLOSED SET of three literals. Security is excluded because reading
it requires elevation this daemon does not have and must not acquire —
offering it would be offering a control that always fails.

⚠⚠ THIS IS THE MOST HOSTILE TEXT ANY CAPABILITY IN THIS BUILD READS. An event
message is written by whatever raised the event, including remote parties: a
failed logon records an attacker-chosen username, a driver records an
attacker-chosen path. Every record returned is fenced as untrusted external
content, scanned by `detect_injection` on the way in, and cannot reach an
amber or red action without the owner clearing the fence himself.
"""

from __future__ import annotations

from typing import Any

from core.system.capability import Capability, Param
from core.system.untrusted import fenced, head
from core.system.winapi import eventlog


def tail(log: str = "System", count: int = 10) -> dict[str, Any]:
    from core.tools.base import ToolError

    try:
        events = eventlog.tail(log, count)
    except eventlog.EventLogUnavailable as exc:
        raise ToolError(str(exc), "Try the System or Application log.") from None

    if not events:
        return {"n": 0, "log": log, "events": [], "head": "nothing",
                "summary": f"The {log} log has nothing recent, Emperor.",
                **fenced(f"the {log} event log", [])}

    bad = [e for e in events if e["level"] in ("error", "critical")]
    warn = [e for e in events if e["level"] == "warning"]
    newest = events[0]
    trouble = ""
    if bad:
        trouble = f" {len(bad)} of them {'is an error' if len(bad) == 1 else 'are errors'}."
    elif warn:
        trouble = f" {len(warn)} {'is a warning' if len(warn) == 1 else 'are warnings'}."

    summary = (f"The last {len(events)} in the {log} log, Emperor. "
               f"Newest is {newest['provider']}, event {newest['event_id']}, "
               f"{newest['level']}.{trouble}")

    return {
        "n": len(events), "log": log, "events": events,
        "errors": len(bad), "warnings": len(warn),
        "newest_provider": newest["provider"], "newest_id": newest["event_id"],
        "head": head([f"{e['provider']} {e['event_id']}" for e in events]),
        "summary": summary,
        **fenced(f"the {log} event log",
                 [f"{e['time']} {e['level']} {e['provider']} {e['event_id']} {e['detail']}"
                  for e in events]),
    }


CAPABILITIES = [
    Capability(
        name="system.eventlog.tail", capability="system.eventlog", tier="green",
        run=tail,
        params=(Param("log", str, default="System", choices=eventlog.LOGS,
                      doc="System, Application or Setup."),
                Param("count", int, default=10, lo=1, hi=eventlog.MAX_COUNT,
                      doc="How many recent entries, up to fifty.")),
        phrasings=("what's in the event log", "recent system events",
                   "last 10 event log entries", "check the application log"),
        success="{summary}",
        audit="tail {count} from the {log} event log",
        note="wevtutil qe /c:N /rd:true /f:xml, ~25-140 ms. Bounded at the source: never "
             "reads a whole log. `log` is a closed set of three; Security is excluded because "
             "it needs elevation this daemon does not have. The most hostile text in the build "
             "— every record is fenced as untrusted external content.",
    ),
]
