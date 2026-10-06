"""
core/system/winapi/eventlog.py — the last N entries of a Windows event log.

`wevtutil qe <log> /c:N /rd:true /f:xml`, constant argv, `shell=False`,
parsed with stdlib `xml.etree`. Measured 42 ms for 5 events, 143 ms for 20.

BOUNDED AT THE SOURCE, NOT AFTER THE FACT. `/c:N` makes Windows return N
records; `/rd:true` makes it read newest-first so those N are the RECENT ones.
Nothing here ever reads a whole log — the System log on this machine is tens
of megabytes and pulling it into a 2-core daemon to then slice the tail would
be the kind of mistake that makes the whole surface feel broken.

THE LOG NAME IS A CLOSED SET. It reaches `wevtutil` as its own argv element
and it can only ever be one of three literals declared in the capability, so
there is no path from a spoken word to an arbitrary channel name.

SECURITY IS DELIBERATELY EXCLUDED. Measured: `wevtutil qe Security` returns
`rc=5 Access is denied` without elevation, and this daemon is not elevated and
must not become so to read a log. Offering it would be offering a control that
always fails.

⚠ THIS IS THE MOST HOSTILE TEXT IN THE BATCH. An event-log message is written
by whatever produced the event, including remote parties — a failed logon
records an attacker-chosen username, a service records an attacker-chosen
path. Everything returned here is fenced as untrusted external content; see
core/system/untrusted.py.
"""

from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET
from typing import Any

#: The logs a capability may name. Closed, and each is readable unelevated.
LOGS = ("System", "Application", "Setup")

#: Hard ceiling on how many records may be asked for in one call.
MAX_COUNT = 50

_NS = {"e": "http://schemas.microsoft.com/win/2004/08/events/event"}

#: `Level` as documented for the Event schema.
_LEVEL = {0: "information", 1: "critical", 2: "error", 3: "warning",
          4: "information", 5: "verbose"}


class EventLogUnavailable(RuntimeError):
    pass


def tail(log: str = "System", count: int = 10) -> list[dict[str, Any]]:
    """
    The `count` most recent records of `log`, newest first.

    `log` must be one of `LOGS` and `count` is clamped to `MAX_COUNT`; both are
    also declared as bounded parameters on the capability, so this is the
    second of two checks rather than the only one.
    """
    if log not in LOGS:
        raise EventLogUnavailable(f"{log} is not a log I read; I have {', '.join(LOGS)}")
    n = max(1, min(int(count), MAX_COUNT))
    try:
        result = subprocess.run(
            ["wevtutil", "qe", log, f"/c:{n}", "/rd:true", "/f:xml"],
            capture_output=True, text=True, timeout=60, shell=False)
    except subprocess.TimeoutExpired:
        raise EventLogUnavailable(f"the {log} log did not answer in time") from None
    except OSError as exc:
        raise EventLogUnavailable(f"wevtutil would not run ({exc})") from None
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()
        raise EventLogUnavailable(
            f"Windows refused the {log} log: {detail[0][:120] if detail else 'no reason given'}")

    # wevtutil emits a sequence of <Event> fragments with no document root.
    try:
        root = ET.fromstring("<events>" + result.stdout + "</events>")
    except ET.ParseError as exc:
        raise EventLogUnavailable(f"the {log} log returned XML I could not read ({exc})") from None

    out: list[dict[str, Any]] = []
    for event in root:
        system = event.find("e:System", _NS)
        if system is None:
            continue

        def _text(tag: str) -> str:
            node = system.find(f"e:{tag}", _NS)
            return (node.text or "").strip() if node is not None else ""

        provider = system.find("e:Provider", _NS)
        created = system.find("e:TimeCreated", _NS)
        try:
            level = _LEVEL.get(int(_text("Level") or 0), "information")
        except ValueError:
            level = "information"
        # The data payload, flattened. This is the attacker-influenceable part
        # and it is carried as DATA — truncated so one enormous record cannot
        # dominate the fence.
        data = event.find("e:EventData", _NS)
        fields = []
        if data is not None:
            for child in data:
                if child.text and child.text.strip():
                    fields.append(child.text.strip())
        out.append({
            "log": log,
            "provider": (provider.get("Name") if provider is not None else "") or "unknown",
            "event_id": _text("EventID"),
            "level": level,
            "time": (created.get("SystemTime") if created is not None else "")[:19],
            "detail": " | ".join(fields)[:300],
        })
    return out
