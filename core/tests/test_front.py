from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time as real_time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TMP = Path(tempfile.mkdtemp(prefix="tessa-front-"))
HOME = TMP / "home"
ENV = {"USERPROFILE": HOME, "APPDATA": HOME / "AppData" / "Roaming", "LOCALAPPDATA": HOME / "AppData" / "Local",
       "ProgramData": TMP / "ProgramData", "PUBLIC": TMP / "Public"}
for _k, _v in ENV.items():
    _v.mkdir(parents=True, exist_ok=True)
    os.environ[_k] = str(_v)

from core.brain.approvals import ApprovalError
from core.brain.executor import Executor
from core.brain.provenance import SessionContext
from core.brain.tools_local import ToolCall
from core.security import gate
from core.security.audit import AuditLog
from core.system.winapi import inputs
from core.tools import coding

coding.DATA = TMP / "data-code"
gate.use_store(gate.Store(TMP / "gate.db", TMP / "written.jsonl"))
passed = failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok    {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


PROGRAMS = HOME / "AppData" / "Local" / "Programs"
WEB = {"hwnd": 201, "title": "placeholder page", "class": "Chrome_WidgetWin_1", "pid": 21, "thread": 22,
       "process": "placeholder.exe", "process_path": str(PROGRAMS / "PlaceholderApp" / "placeholder.exe"),
       "focus_hwnd": 202, "focus_class": "Chrome_RenderWidgetHostHWND", "refuse": ""}
ORB = {**WEB, "hwnd": 301, "title": "placeholder surface", "pid": 31, "thread": 32, "focus_hwnd": 302}
ELSE = {**WEB, "hwnd": 401, "title": "placeholder elsewhere", "pid": 41, "thread": 42, "focus_hwnd": 402}
WORDS = "placeholder words"
RUNS = {"n": 0}


class Clock:
    def __init__(self) -> None:
        self.t = 5000.0

    def perf_counter(self) -> float:
        return self.t

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += max(float(seconds), 0.001)


class World:
    def __init__(self) -> None:
        self.clock = Clock()
        self.windows = {t["hwnd"]: dict(t) for t in (WEB, ORB, ELSE)}
        self.front = dict(ORB)
        self.direct: Any = 0.0
        self.attached: Any = 0.0
        self.attached_now = False
        self.arrival: Any = None
        self.arrived_at: Any = None
        self.focus_late = 0.0
        self.focus_other: Any = None
        self.checks_after = 0
        self.steal_after_checks: Any = None
        self.after_sends: Any = None
        self.events: list[tuple] = []
        self.sent: list[int] = []

    def settle(self) -> None:
        if self.arrival is not None and self.clock.t >= self.arrival[1]:
            hwnd, at = self.arrival
            self.arrival = None
            self.front = dict(self.windows[hwnd])
            self.arrived_at = at
        if self.after_sends is not None and len(self.sent) >= self.after_sends[0]:
            self.front = dict(self.after_sends[1])
            self.after_sends = None

    def target(self) -> dict[str, Any]:
        self.settle()
        if self.arrived_at is not None:
            self.checks_after += 1
            if self.steal_after_checks is not None and self.checks_after > self.steal_after_checks:
                self.front = dict(ELSE)
                self.steal_after_checks = None
        t = dict(self.front)
        if self.arrived_at is not None and t["hwnd"] == WEB["hwnd"]:
            if self.focus_other is not None:
                t.update(self.focus_other)
            elif self.clock.t < self.arrived_at + self.focus_late:
                t.update(focus_hwnd=0, focus_class="")
        return t


class FakeUser32:
    def __init__(self, world: World) -> None:
        self.w = world

    def IsWindow(self, hwnd: Any) -> int:
        return 1 if int(hwnd or 0) in self.w.windows else 0

    def GetWindowThreadProcessId(self, hwnd: Any, ref: Any) -> int:
        t = self.w.windows.get(int(hwnd or 0))
        if t is None:
            return 0
        if ref is not None:
            ref._obj.value = int(t["pid"])
        return int(t["thread"])

    def GetForegroundWindow(self) -> int:
        self.w.settle()
        return int(self.w.front["hwnd"])

    def SetForegroundWindow(self, hwnd: Any) -> int:
        how = "attached" if self.w.attached_now else "direct"
        self.w.events.append(("front", how, int(hwnd), self.w.clock.t, real_time.perf_counter()))
        delay = self.w.attached if self.w.attached_now else self.w.direct
        if delay is None:
            return 0
        self.w.arrival = (int(hwnd), self.w.clock.t + float(delay))
        self.w.settle()
        return 1

    def BringWindowToTop(self, hwnd: Any) -> int:
        self.w.events.append(("top", int(hwnd), self.w.clock.t))
        return 1

    def AttachThreadInput(self, me: Any, other: Any, flag: Any) -> int:
        self.w.events.append(("attach", bool(flag), int(other), self.w.clock.t))
        self.w.attached_now = bool(flag)
        return 1


def install(world: World) -> None:
    def target_now() -> dict[str, Any]:
        world.events.append(("check", world.clock.t))
        return world.target()

    def send(*seq: Any) -> int:
        world.events.append(("send", len(seq), int(world.front["hwnd"]), world.clock.t))
        world.sent.append(len(seq))
        return len(seq)

    inputs.user32 = FakeUser32(world)
    inputs.time = world.clock
    inputs.target_now = target_now
    inputs._send = send


def scenario(**kw: Any) -> tuple[World, Executor, Path]:
    RUNS["n"] += 1
    world = World()
    for k, v in kw.items():
        setattr(world, k, v)
    install(world)
    log = TMP / f"audit-{RUNS['n']}.log"
    return world, Executor(session=SessionContext(), audit=AuditLog(log)), log


def rows(log: Path) -> list[str]:
    if not log.exists():
        return []
    return [str(json.loads(ln).get("summary", "")) for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]


def card(world: World, ex: Executor, text: str = WORDS) -> Any:
    world.front = dict(world.windows[WEB["hwnd"]])
    ex.run(ToolCall("system.input.type", {"text": text}, origin="agent"))
    pend = list(ex.approvals.pending.values())
    world.front = dict(ORB)
    world.events.clear()
    return pend[0] if pend else None


def kinds(world: World) -> list[str]:
    return [e[0] if e[0] != "front" else f"front:{e[1]}" for e in world.events]


def first(world: World, kind: str) -> int:
    for i, e in enumerate(world.events):
        if e[0] == kind:
            return i
    return -1


print("\n(1) on approval she asks for the card's window at once, then types\n")
world, ex, log = scenario()
req = card(world, ex)
check("typing outside the short list raises one card and types nothing",
      req is not None and req.tool == "input.type_checked" and not world.sent)
began = real_time.perf_counter()
rec = ex.execute_approved(req.request_id)
fronts = [e for e in world.events if e[0] == "front"]
check("her first act on the windows is a direct request for the card's window",
      bool(fronts) and fronts[0][1] == "direct" and fronts[0][2] == WEB["hwnd"]
      and not any(k in ("attach", "top") for k in kinds(world)[:first(world, "front")]), str(kinds(world)))
check("nothing is typed before that request", 0 <= first(world, "front") < first(world, "send"), str(kinds(world)))
check("every key goes to the card's window, once it is in front",
      world.sent == [2 * len(WORDS)] and all(e[2] == WEB["hwnd"] for e in world.events if e[0] == "send"),
      str(world.sent))
check("when the direct request works the other way is never used",
      len(fronts) == 1 and "attach" not in kinds(world) and "top" not in kinds(world), str(kinds(world)))
check("the result names the way", rec["result"].get("front") == inputs.FRONT_DIRECT, str(rec["result"]))
check("the log names the way that worked",
      any(s.startswith("APPROVED-RAN") and "direct request" in s for s in rows(log)), str(rows(log)[-3:]))
check("what she says is unchanged", rec["result"].get("said") == f"Typed {len(WORDS)} characters into placeholder page.",
      str(rec["result"].get("said")))
print(f"        measured: {(fronts[0][4] - began) * 1000:.1f} ms from the approval call to the request, "
      f"with two chain rows written to a file first")

print("\n(2) she waits for the window, up to 2 s\n")
world, ex, log = scenario(direct=1.2)
req = card(world, ex)
start = world.clock.t
rec = ex.execute_approved(req.request_id)
sends = [e for e in world.events if e[0] == "send"]
check("a window that comes forward after 1.2 s is waited for, then typed into",
      len(sends) == 1 and 1.2 <= sends[0][3] - start < 2.0 and rec["result"].get("front") == inputs.FRONT_DIRECT
      and "attach" not in kinds(world), f"{sends} {kinds(world)}")
check("nothing was typed while it was not in front", all(e[2] == WEB["hwnd"] for e in sends))

print("\n(3) the other way runs when the first fails\n")
world, ex, log = scenario(direct=None)
req = card(world, ex)
rec = ex.execute_approved(req.request_id)
k = kinds(world)
direct_at = next(e[3] for e in world.events if e[0] == "front" and e[1] == "direct")
attach_at = next(e[3] for e in world.events if e[0] == "attach" and e[1])
check("when Windows refuses the direct request she waits 2 s before anything else",
      2.0 <= attach_at - direct_at < 2.1, f"{attach_at - direct_at:.3f}")
order = [x for x in k if x != "check"]
check("then she attaches to the front window, raises the card's window, asks again, and detaches",
      order[:5] == ["front:direct", "attach", "top", "front:attached", "attach"]
      and [e[1] for e in world.events if e[0] == "attach"] == [True, False], str(order))
check("and types into the card's window", world.sent == [2 * len(WORDS)]
      and all(e[2] == WEB["hwnd"] for e in world.events if e[0] == "send"))
check("the result and the log name that way, and say Windows refused the first",
      rec["result"].get("front") == inputs.FRONT_ATTACHED
      and any(s.startswith("APPROVED-RAN") and "front window's input" in s and "refused" in s for s in rows(log)),
      str(rows(log)[-2:]))
world, ex, log = scenario(direct=30.0)
req = card(world, ex)
rec = ex.execute_approved(req.request_id)
check("a direct request Windows accepted but did not act on in 2 s also falls to the other way",
      rec["result"].get("front") == inputs.FRONT_ATTACHED and world.sent == [2 * len(WORDS)]
      and any("accepted" in s for s in rows(log) if s.startswith("APPROVED-RAN")), str(rows(log)[-2:]))

print("\n(4) nothing is typed when both fail, and he is told which window\n")
world, ex, log = scenario(direct=None, attached=None)
req = card(world, ex)
start = world.clock.t
try:
    ex.execute_approved(req.request_id)
    check("when both ways fail she types nothing", False)
except ApprovalError as err:
    check("when both ways fail she types nothing", not world.sent and "send" not in kinds(world), str(kinds(world)))
    check("she names the window she could not bring forward",
          "could not bring" in str(err.message) and "placeholder page" in str(err.message), str(err.message))
    check("both ways were tried, the direct one first", [x for x in kinds(world) if x.startswith("front")]
          == ["front:direct", "front:attached"], str(kinds(world)))
    check("she gave up after the 2 s and the short second wait", 2.0 <= world.clock.t - start < 2.5,
          f"{world.clock.t - start:.3f}")
check("the failure is on the chain with the window's name",
      any(s.startswith("APPROVED-BUT-FAILED") and "placeholder page" in s for s in rows(log)), str(rows(log)[-2:]))
check("no new card is raised for a window she could not bring forward", not ex.approvals.pending)
world, ex, log = scenario(direct=None, attached=None)
world.windows[WEB["hwnd"]]["title"] = ""
req = card(world, ex)
try:
    ex.execute_approved(req.request_id)
    check("a window with no title is named by its program", False)
except ApprovalError as err:
    check("a window with no title is named by its program", "placeholder.exe" in str(err.message), str(err.message))

print("\n(5) the checks before and during typing are unchanged\n")
world, ex, log = scenario(focus_other={"focus_hwnd": 203, "focus_class": "Edit"})
req = card(world, ex)
start = world.clock.t
try:
    ex.execute_approved(req.request_id)
    check("a window that comes forward with the keyboard somewhere else gets a new card", False)
except ApprovalError as err:
    again = list(ex.approvals.pending.values())
    check("a window that comes forward with the keyboard somewhere else gets a new card, and nothing is typed",
          err.code == "permission.pending" and len(again) == 1 and again[0].args.get("focus") == "Edit"
          and not world.sent, f"{err.code} {err.message}")
    check("she waited half a second for the keyboard to return before asking again",
          0.5 <= world.clock.t - start < 0.6, f"{world.clock.t - start:.3f}")
world, ex, log = scenario(focus_late=0.3)
req = card(world, ex)
start = world.clock.t
rec = ex.execute_approved(req.request_id)
sends = [e for e in world.events if e[0] == "send"]
check("a keyboard that returns to the card's element 0.3 s after the window is typed into, with no new card",
      len(sends) == 1 and 0.3 <= sends[0][3] - start < 0.5 and not ex.approvals.pending, str(sends))
world, ex, log = scenario(steal_after_checks=1)
req = card(world, ex)
try:
    ex.execute_approved(req.request_id)
    check("a click somewhere else after the window came forward stops her before the first key", False)
except ApprovalError as err:
    check("a click somewhere else after the window came forward stops her before the first key",
          not world.sent and "keyboard moved" in str(err.message) and "placeholder elsewhere" in str(err.message),
          str(err.message))
world, ex, log = scenario(after_sends=(1, ELSE))
req = card(world, ex, "x" * 60)
try:
    ex.execute_approved(req.request_id)
    check("a window that changes during the typing stops it at the next 20 characters", False)
except ApprovalError as err:
    check("a window that changes during the typing stops it at the next 20 characters",
          world.sent == [40] and "keyboard moved" in str(err.message), f"{world.sent} {err.message}")
world, ex, log = scenario()
req = card(world, ex, "y" * 45)
ex.execute_approved(req.request_id)
k = kinds(world)
check("45 characters go in three parts", world.sent == [40, 40, 10], str(world.sent))
check("the target is checked right before the first key and before every further 20 characters",
      all(k[i - 1] == "check" for i, x in enumerate(k) if x == "send") and k.count("send") == 3, str(k))

print("\n(6) no request when there is nothing to bring forward\n")
world, ex, log = scenario()
req = card(world, ex)
world.front = dict(WEB)
rec = ex.execute_approved(req.request_id)
check("a card's window that is already in front is typed into with no request to Windows",
      world.sent == [2 * len(WORDS)] and not [x for x in kinds(world) if x.startswith("front") or x in ("attach", "top")]
      and rec["result"].get("front") == "already", str(kinds(world)))
check("the log says it was already in front",
      any(s.startswith("APPROVED-RAN") and "already in front" in s for s in rows(log)), str(rows(log)[-2:]))
world, ex, log = scenario()
req = card(world, ex)
del world.windows[WEB["hwnd"]]
try:
    ex.execute_approved(req.request_id)
    check("a window that closed after the card is not asked for, and nothing is typed", False)
except ApprovalError as err:
    check("a window that closed after the card is not asked for, and nothing is typed",
          not world.sent and not [x for x in kinds(world) if x.startswith("front")] and "could not bring" in str(err.message),
          str(kinds(world)))
world, ex, log = scenario()
req = card(world, ex)
world.windows[WEB["hwnd"]]["pid"] = 99
try:
    ex.execute_approved(req.request_id)
    check("a window handle that now belongs to another program is not asked for", False)
except ApprovalError as err:
    check("a window handle that now belongs to another program is not asked for",
          not world.sent and not [x for x in kinds(world) if x.startswith("front")], str(kinds(world)))

print("\n(7) the mechanism by itself\n")
world, ex, log = scenario()
check("bring_to_front names the direct way and notes that Windows accepted it",
      inputs.bring_to_front(WEB["hwnd"], WEB["pid"]) == inputs.FRONT_DIRECT
      and inputs.FRONT_LAST.get("direct_accepted") is True and inputs.FRONT_LAST.get("way") == inputs.FRONT_DIRECT)
world, ex, log = scenario(direct=None)
check("bring_to_front names the attached way and notes the refusal, after 2 s",
      inputs.bring_to_front(WEB["hwnd"], WEB["pid"]) == inputs.FRONT_ATTACHED
      and inputs.FRONT_LAST.get("direct_accepted") is False and 2.0 <= inputs.FRONT_LAST.get("seconds", 0) < 2.1,
      str(inputs.FRONT_LAST))
world, ex, log = scenario(direct=None, attached=None)
check("bring_to_front returns nothing when both ways fail",
      inputs.bring_to_front(WEB["hwnd"], WEB["pid"]) == "" and inputs.FRONT_LAST.get("way") == "")
check("the wait is 2 s and the polls are 10 ms", inputs.FRONT_WAIT_S == 2.0 and inputs.FRONT_POLL_S == 0.01)

print("\n(8) the daemon's own approval handler\n")
import asyncio
from types import SimpleNamespace

from core import server

LOGGED: list[str] = []
server.log = LOGGED.append


class Ws:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send(self, frame: Any) -> None:
        self.frames.append(json.loads(frame) if isinstance(frame, str) else dict(frame))


class Stub:
    def __init__(self, ex: Executor) -> None:
        self.voice = SimpleNamespace(executor=ex)
        self.audit = ex._audit
        self.cast: list[tuple] = []
        self.said: list[str] = []

    async def broadcast(self, kind: str, payload: dict) -> None:
        self.cast.append((kind, payload))

    def say_line(self, message: dict, spoken: Any = None) -> None:
        self.said.append(str(message.get("text") or ""))


def respond(ex: Executor, request_id: str) -> tuple[Stub, Ws]:
    stub, ws = Stub(ex), Ws()
    asyncio.run(server.TessaDaemon._h_permission_respond(stub, ws, {"surface": "orb"},
                                                         {"requestId": request_id, "decision": "approve"},
                                                         "placeholder-corr"))
    return stub, ws


world, ex, log = scenario()
req = card(world, ex)
LOGGED.clear()
stub, ws = respond(ex, req.request_id)
check("an approval that reaches the daemon's handler brings the window forward and types",
      world.sent == [2 * len(WORDS)] and [x for x in kinds(world) if x.startswith("front")] == ["front:direct"]
      and ws.frames and ws.frames[-1].get("type") == "res.ok", f"{kinds(world)} {ws.frames[-1:]}")
check("the daemon's log line for the approval names the way that worked",
      any(s.startswith("permission APPROVED input.type_checked") and "direct request" in s for s in LOGGED),
      str(LOGGED))
check("the other surface is told the card is resolved",
      any(k == "evt.permission.resolved" and p.get("decision") == "approve" for k, p in stub.cast), str(stub.cast))
world, ex, log = scenario(direct=None, attached=None)
req = card(world, ex)
LOGGED.clear()
stub, ws = respond(ex, req.request_id)
reply = ws.frames[-1] if ws.frames else {}
check("when both ways fail the handler answers with an error that names the window, and nothing is typed",
      not world.sent and str(reply.get("type", "")).startswith("err.")
      and "placeholder page" in str(reply.get("payload", {}).get("message", "")), str(reply))
check("she also says it, naming the window",
      len(stub.said) == 1 and "could not bring placeholder page back to the front" in stub.said[0]
      and stub.said[0].startswith("That did not run"), str(stub.said))
check("the daemon's log has the refusal with the window's name",
      any(s.startswith("permission REFUSED input.type_checked") and "placeholder page" in s for s in LOGGED),
      str(LOGGED))
world, ex, log = scenario(focus_other={"focus_hwnd": 203, "focus_class": "Edit"})
req = card(world, ex)
stub, ws = respond(ex, req.request_id)
reply = ws.frames[-1] if ws.frames else {}
check("a new card after the window came forward is answered as pending, and she does not speak over the card",
      reply.get("payload", {}).get("code") == "permission.pending" and not stub.said and not world.sent, str(reply))

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
