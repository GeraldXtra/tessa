"""
core/system/abilities/service_control.py — START or STOP a Windows service, or
set how it starts. RED, by EXACT service name, the name FROZEN on the card,
Windows-critical services and the daemon's own ancestry REFUSED before the
card and again after approval.

    system.service.start        red   service.control   sc start  <Name>
    system.service.stop         red   service.control   sc stop   <Name>
    system.service.set_startup  red   service.control   sc config <Name> start= auto|demand|disabled|delayed-auto

────────────────────────────────────────────────────────────────────────────────
⚠⚠ WHY A SERVICE IS NOT A PROCESS

Services run Windows underneath everything he sees. Stop the wrong one and the
machine bluescreens (RpcSs, DcomLaunch), loses networking (Dhcp, Dnscache, nsi,
BFE), loses the desktop (ProfSvc, UserManager, Themes) or loses its antivirus
(WinDefend, WdNisSvc, Sense). `sc config ... start= disabled` is the same act
made permanent across reboots. A poisoned tweet reaching a service stop is
"turn Defender off and cut the firewall". So, structurally:

  1. THE REFUSE-LIST, checked BEFORE the card (`describe`) and AGAIN after
     approval (`run`), for all three verbs:
       * HER OWN ANCESTRY — measured live, not assumed: every service whose
         process is an ANCESTOR of this daemon (runtime.json pid, and this
         process) is refused. On this machine the daemon runs under
         WindowsTerminal <- svchost(1156), which hosts DcomLaunch,
         BrokerInfrastructure, SystemEventsBroker, PlugPlay and Power. Any
         service named Tessa* is hers by name.
       * WINDOWS-CRITICAL (`CORE`): the RPC/COM/session/logon/profile set, the
         networking stack, audio, Windows Update, the certificate and
         credential services, the packaged-app service.
       * SECURITY (`SECURITY_NAMES`, `SECURITY_PREFIXES`, and any display name
         that says defender / antivirus / firewall / security / malware /
         endpoint protection): never stopped, never disabled, never started
         by her either — they are not hers to touch in any direction.
       * NOT A SERVICE: a name that is not in the service table (the green
         observer's own registry + `sc query` read) is refused. Drivers are
         not in that table by construction (Type filter), so a kernel or
         file-system driver cannot be unloaded from here.
  2. BY EXACT NAME, FROZEN. The card carries the service's KEY NAME (Spooler,
     not "print spooler"), resolved from what he said at routing time against
     the observer table; `frozen=("name",)` (and `mode`) means an approved
     "stop Spooler" cannot come back as "stop WinDefend". `display` is shown on
     the card and never reaches the argv.
  3. FIXED ARGV. `sc.exe` by absolute path (System32), `shell=False`, one
     verb, one name, and for `config` exactly `start=` + one of four words.
     `_run_sc` — THE ONE SEAM — refuses any other shape, so a later edit above
     cannot reach `sc delete`, `sc create` or `binPath=`.
  4. RED, CARD-ONLY, TWICE — the framework's flag and `_control`'s own check.

THE SHOW-WHAT'S-THERE STEP IS THE GREEN OBSERVER. `describe` reads the same
table `system.services.list` reads (core/system/winapi/services.py) and names
the service, its live state and its start type on the card, so he approves
against what the service IS.

⚠ THIS DAEMON IS NOT ELEVATED (TokenElevation 0, measured 2026-09-12). Windows
lets a standard user QUERY every service and CONTROL almost none; a genuine
approval on a real service will usually come back `sc` exit 5 (access denied),
and that is reported honestly as a failure, never as success. Running the
daemon elevated is a separate decision this module does not make.

`sc.exe` is a Windows CLI; psutil (already a dependency) maps service -> pid.
No new dependency.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from core.system.capability import Capability, Param
from core.system.winapi import services as winsvc

SC_EXE = os.path.join(os.environ.get("SystemRoot") or r"C:\Windows", "System32", "sc.exe")

#: A service KEY NAME as Windows spells it: starts alphanumeric (never an
#: option), letters/digits/space/dot/dash/underscore/@, 256 max.
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@\- ]{0,255}$")

#: Per-user service instances carry a session suffix: WpnUserService_3a1b2.
_INSTANCE_SUFFIX = re.compile(r"_[0-9a-f]{4,10}$")

#: The closed start modes, spoken -> what `sc config start=` takes.
MODES: dict[str, str] = {"automatic": "auto", "manual": "demand", "disabled": "disabled", "delayed": "delayed-auto"}
_MODE_WORDS = {"auto": "automatically", "demand": "manually", "disabled": "not at all (disabled)",
               "delayed-auto": "automatically, delayed"}

CONTROL_TIMEOUT_S = 60
SETTLE_S = 6.0          # how long start/stop waits for the state to land

#: `sc` exits with the Win32 error. The ones he will meet, in words.
_EXIT_WORDS = {
    0: "done",
    5: "access denied — this daemon is not running elevated",
    1051: "other services depend on it and are still running",
    1052: "the service does not accept that control",
    1053: "the service did not respond in time",
    1056: "it is already running",
    1058: "it is disabled and cannot be started",
    1060: "no service of that name is installed",
    1061: "the service cannot accept control messages right now",
    1062: "it is not running",
    1072: "the service is marked for deletion",
    1115: "the system is shutting down",
}


class Refused(Exception):
    """Internal: a target this module will not act on. Converted to ToolError."""


# ─────────────────────────────────────────────────────────────────────────────
# the refuse-list
# ─────────────────────────────────────────────────────────────────────────────

#: WINDOWS-CRITICAL, by key name (lowercase). Stopping any of these takes the
#: machine, its networking, its desktop, its audio or its updates down.
CORE: frozenset[str] = frozenset({
    # RPC / COM / session — bluescreen or a dead desktop
    "rpcss", "rpceptmapper", "dcomlaunch", "lsm", "brokerinfrastructure", "systemeventsbroker",
    "plugplay", "power", "winmgmt", "eventlog", "eventsystem", "schedule", "profsvc", "usermanager",
    "coremessagingregistrar", "staterepository", "timebrokersvc", "gpsvc", "samss", "wcmsvc", "sens",
    "appinfo", "keyiso", "vaultsvc", "cryptsvc", "trustedinstaller", "themes", "deviceinstall",
    "appxsvc", "dispbrokerdesktopsvc",
    # networking — cuts her own STT/TTS/model connections with his
    "nsi", "dhcp", "dnscache", "nlasvc", "netprofm", "wlansvc", "lanmanworkstation", "netman",
    "iphlpsvc", "winhttpautoproxysvc", "bfe", "mpssvc",
    # audio — she cannot hear or speak without it
    "audiosrv", "audioendpointbuilder",
    # updates and their medic
    "wuauserv", "usosvc", "waasmedicsvc",
})

#: SECURITY, by key name and by prefix (lowercase). Defender, its network
#: inspection, its EDR sensor, the security centre, the firewall, SmartScreen
#: web threat defence, System Guard.
SECURITY_NAMES: frozenset[str] = frozenset({
    "windefend", "wdnissvc", "wdnisdrv", "wdfilter", "wdboot", "sense", "securityhealthservice",
    "wscsvc", "mpssvc", "bfe", "sgrmbroker", "sgrmagent", "webthreatdefsvc", "mssecflt", "mssense",
})
SECURITY_PREFIXES: tuple[str, ...] = ("wdnis", "wdfilter", "wdboot", "windefend", "mpssvc", "mpksl",
                                      "sense", "sgrm", "webthreatdef", "securityhealth", "mssec")
_SECURITY_DISPLAY = re.compile(r"defender|anti-?virus|anti-?malware|\bmalware\b|firewall|\bsecurity\b|"
                               r"endpoint protection|threat", re.I)

_OWN = "hosts the process tree I run in"
_CORE_WHY = "is a Windows core service — stopping or disabling it takes the machine, its network, its desktop or its audio down"
_SEC_WHY = "is a security service — I do not stop, start or disable those, in any direction"


def _daemon_pid() -> int | None:
    """The live daemon's pid from runtime.json, or None (stale file, no file)."""
    try:
        from core.security.runtime import runtime_path
        import psutil
        pid = int(json.loads(runtime_path().read_text(encoding="utf-8"))["pid"])
        return pid if psutil.pid_exists(pid) else None
    except Exception:  # noqa: BLE001
        return None


def ancestry_pids() -> set[int]:
    """This process, the daemon (if live), and every ancestor of both."""
    import psutil

    pids = {os.getpid()}
    d = _daemon_pid()
    if d is not None:
        pids.add(d)
    out = set(pids)
    for pid in pids:
        try:
            for p in psutil.Process(pid).parents():
                out.add(p.pid)
        except psutil.Error:
            continue
    return out


def hosting_pids() -> dict[str, int]:
    """service key name (lowercase) -> pid of the process hosting it (running ones only)."""
    import psutil

    out: dict[str, int] = {}
    try:
        for s in psutil.win_service_iter():
            try:
                pid = s.pid()
            except psutil.Error:
                continue
            if pid:
                out[s.name().lower()] = int(pid)
    except Exception:  # noqa: BLE001
        pass
    return out


def own_services() -> dict[str, int]:
    """The services hosting the daemon's ancestry — measured, not assumed."""
    anc = ancestry_pids()
    return {name: pid for name, pid in hosting_pids().items() if pid in anc}


def table() -> dict[str, dict[str, Any]]:
    """The green observer's table, keyed by lowercase name. Read-only, ~85 ms."""
    try:
        rows = winsvc.services()
    except winsvc.ServicesUnavailable as exc:
        raise Refused(str(exc), "Windows would not describe its services.") from None
    return {r["name"].lower(): r for r in rows}


def validate_name(raw: Any) -> str:
    s = " ".join(str(raw if raw is not None else "").split())
    if not s:
        raise Refused("no service was named", "Say the service's name and I will look it up.")
    if not NAME_RE.match(s) or "\\" in s or "/" in s:
        raise Refused(f"{s[:40]!r} is not a service name", "Letters, digits, dots and dashes — Spooler.")
    return s


def protection(name: str, rows: dict[str, dict[str, Any]] | None = None,
               own: dict[str, int] | None = None) -> tuple[str, str] | None:
    """
    (reason, alternative) when `name` must not be controlled, else None.
    Order: must exist as a service -> her own ancestry -> Windows core ->
    security. Case-insensitive; per-user instance suffixes stripped.
    """
    rows = table() if rows is None else rows
    low = name.strip().lower()
    row = rows.get(low)
    if row is None:
        return (f"{name} is not a Windows service I can see",
                "Say list my services and pick one by name. Drivers are not services I control.")
    base = _INSTANCE_SUFFIX.sub("", low)
    display = str(row.get("display") or name)
    label = f"{display} ({row['name']})"
    if base.startswith("tessa"):
        return (f"{label} is mine", "I do not stop, start or disable myself.")
    own = own_services() if own is None else own
    if low in own:
        return (f"{label} {_OWN}", "Stopping it stops me. Not that one.")
    if base in CORE:
        return (f"{label} {_CORE_WHY}", "Not that one.")
    if base in SECURITY_NAMES or base.startswith(SECURITY_PREFIXES) or _SECURITY_DISPLAY.search(display):
        return (f"{label} {_SEC_WHY}", "Not that one.")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# name -> exact service, read-only (the routing step and the card)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Resolution:
    name: str                      # "" when unresolved
    display: str
    state: str
    start: str
    why: str
    candidates: tuple[str, ...] = field(default_factory=tuple)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def resolve(spoken: Any, rows: dict[str, dict[str, Any]] | None = None) -> Resolution:
    """
    What he said -> ONE service, or nothing. In order: the exact key name; the
    exact display name; either with a trailing "service" dropped; equal once
    punctuation and case are ignored; the only service whose display or key
    name contains the words. Otherwise unresolved with the candidates.
    """
    rows = table() if rows is None else rows
    q = " ".join(str(spoken or "").split()).strip(" .,?!\"'")
    if not q:
        return Resolution("", "", "", "", "nothing was named")
    ql = q.lower()
    qs = re.sub(r"\s+service$", "", ql)
    def hit(r: dict[str, Any], why: str) -> Resolution:
        return Resolution(r["name"], r["display"], r.get("state", "unknown"), r.get("start", "unknown"), why)
    for key, r in rows.items():
        if key == ql or key == qs:
            return hit(r, "exact name")
    for r in rows.values():
        dl = r["display"].lower()
        if dl == ql or dl == qs or re.sub(r"\s+service$", "", dl) == qs:
            return hit(r, "exact display name")
    qn = _norm(qs)
    same = [r for r in rows.values() if _norm(r["display"]) == qn or _norm(re.sub(r"\s+service$", "", r["display"].lower())) == qn
            or _norm(r["name"]) == qn]
    if len(same) == 1:
        return hit(same[0], "name")
    if len(same) > 1:
        return Resolution("", q, "", "", f"{len(same)} candidates", tuple(f"{r['display']} ({r['name']})" for r in same[:20]))
    contains = [r for r in rows.values() if qs in r["display"].lower() or qs in r["name"].lower()]
    if len(contains) == 1:
        return hit(contains[0], "the only match")
    return Resolution("", q, "", "", "no match" if not contains else f"{len(contains)} candidates",
                      tuple(f"{r['display']} ({r['name']})" for r in contains[:20]))


# ─────────────────────────────────────────────────────────────────────────────
# sc.exe: the fixed argument vectors, THE ONE SEAM, and the read-only query
# ─────────────────────────────────────────────────────────────────────────────

_VERBS = frozenset({"start", "stop", "config"})


def argv_start(name: str) -> list[str]:
    return [SC_EXE, "start", name]


def argv_stop(name: str) -> list[str]:
    return [SC_EXE, "stop", name]


def argv_config(name: str, mode: str) -> list[str]:
    # `start=` and the mode are SEPARATE argv items — that is sc's grammar.
    return [SC_EXE, "config", name, "start=", MODES[mode]]


def _shape_ok(argv: list[str]) -> bool:
    if not argv or argv[0] != SC_EXE or len(argv) < 3 or argv[1] not in _VERBS:
        return False
    if not NAME_RE.match(argv[2]) or argv[2].startswith(("-", "/")):
        return False
    if argv[1] == "config":
        return len(argv) == 5 and argv[3] == "start=" and argv[4] in MODES.values()
    return len(argv) == 3


def _run_sc(argv: list[str], timeout: int) -> subprocess.CompletedProcess:
    """
    THE ONE SEAM. Every control verb comes through here. `shell=False`, no
    window, the exact shape re-checked. A proof replaces this with a recorder
    and nothing below it ever runs.
    """
    if not _shape_ok(argv):
        raise Refused("that sc command is not one I run", "start, stop, or config start= only, one service.")
    cp = subprocess.run(argv, shell=False, capture_output=True, timeout=timeout,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    out = (cp.stdout or b"").decode("utf-8", errors="replace")
    err = (cp.stderr or b"").decode("utf-8", errors="replace")
    return subprocess.CompletedProcess(argv, cp.returncode, out, err)


def _query_state(name: str) -> str:
    """Live state word for ONE service, read-only (`sc query <name>`)."""
    try:
        cp = subprocess.run([SC_EXE, "query", name], shell=False, capture_output=True, timeout=30,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (subprocess.TimeoutExpired, OSError):
        return "unknown"
    for raw in (cp.stdout or b"").decode("utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line.upper().startswith("STATE"):
            parts = line.split()
            if parts:
                return parts[-1].lower()
    return "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# RED: start / stop / set_startup — one body
# ─────────────────────────────────────────────────────────────────────────────

_REPORT: Callable[..., None] | None = None


def bind_reporter(report: Callable[..., None] | None) -> None:
    global _REPORT
    _REPORT = report


def _report(verb: str, tool: str, summary: str, actor: str) -> None:
    if _REPORT is None:
        return
    try:
        _REPORT(verb, tool, summary, "red", actor=actor)
    except Exception:  # noqa: BLE001
        pass


def _tail(s: str, n: int = 300) -> str:
    s = " ".join((s or "").split())
    return s[-n:] if len(s) > n else s


def _resolve_exact(raw_name: Any, rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The table row for an exact key name (case-insensitive), or Refused via protection()."""
    name = validate_name(raw_name)
    hit = protection(name, rows)
    if hit is not None:
        raise Refused(*hit)
    return rows[name.lower()]


def _control(action: str, raw_name: Any, display: str, mode: str, *, approved: bool,
             request_id: str, actor: str) -> dict[str, Any]:
    from core.tools.base import ToolError

    tool = f"system.service.{action}"
    try:
        rows = table()
        row = _resolve_exact(raw_name, rows)
        name = row["name"]
        if action == "set_startup" and mode not in MODES:
            raise Refused(f"{mode!r} is not a start mode", "automatic, manual, disabled or delayed.")
        if not approved:
            raise Refused(f"{row['display']} ({name}) cannot be {action.replace('_', ' ')}ed without your approval on the card",
                          "Approve it there and I will do it.")
        argv = (argv_start(name) if action == "start" else argv_stop(name) if action == "stop"
                else argv_config(name, mode))
    except Refused as r:
        raise ToolError(*r.args) from None

    label = f"{row['display']} ({name})"
    was_state, was_start = row.get("state", "unknown"), row.get("start", "unknown")
    _report("RUNNING", tool, f"requestId={request_id} sc {' '.join(argv[1:])} — {label} is {was_state}, starts {was_start}", actor)
    try:
        cp = _run_sc(argv, CONTROL_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        _report("RAN-FAILED", tool, f"requestId={request_id} {name}: sc timed out after {CONTROL_TIMEOUT_S}s", actor)
        raise ToolError(f"sc did not finish for {label} within {CONTROL_TIMEOUT_S} seconds",
                        "Check the service in services.msc before asking again.") from None
    except Refused as r:
        raise ToolError(*r.args) from None
    except OSError as exc:
        _report("RAN-FAILED", tool, f"requestId={request_id} {name}: {type(exc).__name__}: {exc}"[:200], actor)
        raise ToolError(f"sc could not be started: {exc}", "Is Windows healthy?") from None

    rc = int(cp.returncode)
    words = _EXIT_WORDS.get(rc, f"exit code {rc}")
    tail = _tail(cp.stdout) or _tail(cp.stderr)
    if rc != 0:
        _report("RAN-FAILED", tool, f"requestId={request_id} {name}: sc exit {rc} ({words}) — {tail[:160]}", actor)
        raise ToolError(f"Windows would not {action.replace('_', ' ')} {label}: {words}",
                        "Nothing was changed.")

    if action == "set_startup":
        _report("RAN", tool, f"requestId={request_id} {name}: start type {was_start} -> {MODES[mode]}; sc exit 0", actor)
        return {"action": action, "name": name, "display": row["display"], "mode": mode, "was": was_start,
                "state": was_state, "exit": 0, "ok": True,
                "verdict": f"{label} now starts {_MODE_WORDS[MODES[mode]]}, Emperor. It was {was_start}."}

    want = "running" if action == "start" else "stopped"
    deadline = time.monotonic() + SETTLE_S
    state = _query_state(name)
    while state != want and time.monotonic() < deadline:
        time.sleep(0.5)
        state = _query_state(name)
    landed = state == want
    _report("RAN", tool, f"requestId={request_id} {name}: {was_state} -> {state}; sc exit 0", actor)
    verb = "Started" if action == "start" else "Stopped"
    verdict = (f"{verb} {label}, Emperor." if landed
               else f"Asked Windows to {action} {label}, Emperor; it reports {state.replace('_', ' ')} for now.")
    return {"action": action, "name": name, "display": row["display"], "was": was_state, "state": state,
            "landed": landed, "exit": 0, "ok": True, "verdict": verdict}


def start(name: str, display: str = "", provenance: str = "schedule",
          _approved_by_surface: bool = False, _request_id: str = "") -> dict[str, Any]:
    """RED. `sc start <Name>`. Refuse-list, then the card."""
    return _control("start", name, display, "", approved=bool(_approved_by_surface),
                    request_id=_request_id, actor=provenance)


def stop(name: str, display: str = "", provenance: str = "schedule",
         _approved_by_surface: bool = False, _request_id: str = "") -> dict[str, Any]:
    """RED. `sc stop <Name>`. Refuse-list, then the card."""
    return _control("stop", name, display, "", approved=bool(_approved_by_surface),
                    request_id=_request_id, actor=provenance)


def set_startup(name: str, mode: str, display: str = "", provenance: str = "schedule",
                _approved_by_surface: bool = False, _request_id: str = "") -> dict[str, Any]:
    """RED. `sc config <Name> start= <mode>`. Refuse-list, then the card."""
    return _control("set_startup", name, display, mode, approved=bool(_approved_by_surface),
                    request_id=_request_id, actor=provenance)


def _describe(action: str) -> Callable[[dict[str, Any]], str]:
    """
    What the card SAYS, and what it refuses before it is raised. Read-only:
    the observer table, the refuse-list and the live state — no sc control
    verb is reachable from here.
    """
    def describe(args: dict[str, Any]) -> str:
        from core.tools.base import ToolError

        try:
            rows = table()
            row = _resolve_exact(args.get("name"), rows)
            mode = str(args.get("mode") or "").strip().lower()
            if action == "set_startup" and mode not in MODES:
                raise Refused(f"{mode!r} is not a start mode", "automatic, manual, disabled or delayed.")
        except Refused as r:
            raise ToolError(*r.args) from None
        label = f"{row['display']} ({row['name']})"
        state, startt = row.get("state", "unknown"), row.get("start", "unknown")
        if action == "set_startup":
            return f"set {label} to start {_MODE_WORDS[MODES[mode]]} — it starts {startt} now, and is {state}"
        return f"{action} {label} — it is {state} now, start type {startt}"

    return describe


_NAME = Param("name", str, doc="The service's exact key name — Spooler. Say its name and I will look it up.")
_DISPLAY = Param("display", str, default="", doc="What Windows calls it — shown on the card, never executed.")
_MODE = Param("mode", str, choices=tuple(MODES), doc="automatic, manual, disabled or delayed.")

CAPABILITIES = [
    Capability(
        name="system.service.start", capability="service.control", tier="red",
        run=start, params=(_NAME, _DISPLAY),
        frozen=("name",), describe=_describe("start"),
        phrasings=("start the print spooler service", "start the fax service"),
        success="{verdict}",
        failure="I did not start it, sir. {reason} {alternative}",
        audit="SERVICE START {name} ({display})",
        hold="start the {display} service ({name})",
        note="RED, card-only. sc start <Name> — absolute sc.exe, fixed argv, shell=False, name frozen. "
             "Refuses the daemon's own ancestry (measured), Windows core, security services and any name "
             "not in the service table, before the card and after approval.",
    ),
    Capability(
        name="system.service.stop", capability="service.control", tier="red",
        run=stop, params=(_NAME, _DISPLAY),
        frozen=("name",), describe=_describe("stop"),
        phrasings=("stop the print spooler service", "stop the fax service"),
        success="{verdict}",
        failure="I did not stop it, sir. {reason} {alternative}",
        audit="SERVICE STOP {name} ({display})",
        hold="stop the {display} service ({name})",
        note="RED, card-only. sc stop <Name> — absolute sc.exe, fixed argv, shell=False, name frozen. "
             "Refuses the daemon's own ancestry (measured), Windows core, security services and any name "
             "not in the service table, before the card and after approval.",
    ),
    Capability(
        name="system.service.set_startup", capability="service.control", tier="red",
        run=set_startup, params=(_NAME, _MODE, _DISPLAY),
        frozen=("name", "mode"), describe=_describe("set_startup"),
        phrasings=("disable the print spooler service at startup", "set the fax service to manual"),
        success="{verdict}",
        failure="I did not change it, sir. {reason} {alternative}",
        audit="SERVICE SET_STARTUP {name} ({display}) start={mode}",
        hold="set the {display} service ({name}) to start {mode}",
        note="RED, card-only. sc config <Name> start= auto|demand|disabled|delayed-auto — the ONLY config "
             "shape the seam accepts (no binPath=, no obj=, no delete/create). Name and mode frozen. Same "
             "refuse-list as stop, before the card and after approval.",
    ),
]
