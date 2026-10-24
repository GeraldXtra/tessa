from __future__ import annotations

import asyncio
import ctypes
import ctypes.wintypes as wt
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TMP = Path(tempfile.mkdtemp(prefix="tessa-up-"))
REAL_RUNTIME = Path(os.environ.get("LOCALAPPDATA", str(TMP))) / "Tessa" / "runtime.json"
LIVE = [ROOT / "data" / "audit.log", ROOT / "data" / "memory" / "conversation.json",
        ROOT / "data" / "tessa-actions.jsonl", REAL_RUNTIME]


def digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "absent"


LIVE_BEFORE = {str(p): digest(p) for p in LIVE}

NET = {"attempts": 0}
_real_connect = socket.socket.connect
_real_create = socket.create_connection


def _loopback(address: Any) -> bool:
    host = address[0] if isinstance(address, tuple) and address else address
    return str(host) in ("127.0.0.1", "::1", "localhost")


def _fenced_connect(self: socket.socket, address: Any, *a: Any, **k: Any) -> Any:
    if _loopback(address):
        return _real_connect(self, address, *a, **k)
    NET["attempts"] += 1
    raise OSError("network fenced in test_up")


def _fenced_create(address: Any, *a: Any, **k: Any) -> Any:
    if _loopback(address):
        return _real_create(address, *a, **k)
    NET["attempts"] += 1
    raise OSError("network fenced in test_up")


socket.socket.connect = _fenced_connect
socket.create_connection = _fenced_create

import websockets
from websockets.asyncio.client import connect as ws_connect
from websockets.asyncio.server import serve as ws_serve

import core.bus as BUS
import core.lifecycle as LC
import core.server as SRV
from core.brain import intent_model, memory
from core.brain.claims import ClaimStore
from core.brain.conversation import Conversation
from core.brain.style import StyleStore
from core.capabilities.vault import Vault
from core.security import runtime as RT
from core.security.audit import AuditLog
from core.telemetry.cost import CostLedger
from core.voice.audio_io import Capture
from core.voice.tts.base import Synthesis

passed = failed = 0
NUMBERS: dict[str, Any] = {}


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok    {name}" + (f"  {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


def pc() -> float:
    return time.perf_counter()


LOG: list[str] = []
memory.ACTION_LOG = TMP / "actions.jsonl"
_audits = {"n": 0}


def _audit(path: Any) -> AuditLog:
    _audits["n"] += 1
    return AuditLog(TMP / f"audit-{_audits['n']}.log")


SRV.AuditLog = _audit
SRV._Vault = lambda **kw: Vault(path=TMP / "vault.json", acl=False, profile_root=TMP / "profiles", **kw)
SRV.Conversation = lambda: Conversation(path=TMP / "conversation.json")
SRV.ClaimStore = lambda: ClaimStore(path=TMP / "claims.json")
SRV.StyleStore = lambda **kw: StyleStore(path=TMP / "style.json", **kw)
SRV.CostLedger = lambda path: CostLedger(TMP / "cost.jsonl")
SRV.log = lambda msg: LOG.append(f"{pc():.4f} {msg}")
intent_model._engine_for = lambda brain: brain


class CallbackStop(Exception):
    pass


class Dev:
    lead = 0.12
    block = 576
    streams: list[Any] = []


class FakeStream:
    def __init__(self, samplerate: int, channels: int, dtype: str, callback: Any,
                 finished_callback: Any = None, **kw: Any) -> None:
        self.rate = int(samplerate)
        self.cb = callback
        self.fin = finished_callback
        self.halt = threading.Event()
        self.thread: threading.Thread | None = None
        self.played = 0
        self.finished = False
        self.lock = threading.Lock()
        Dev.streams.append(self)

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        period = Dev.block / self.rate
        nxt = pc()
        drained = False
        while not self.halt.is_set():
            out = np.zeros((Dev.block, 1), dtype=np.int16)
            now = pc()
            try:
                self.cb(out, Dev.block, types.SimpleNamespace(currentTime=now, outputBufferDacTime=now + Dev.lead), None)
            except CallbackStop:
                drained = True
            self.played += 1
            if drained:
                break
            nxt += period
            wait = nxt - pc()
            if wait > 0:
                self.halt.wait(wait)
        if drained and not self.halt.is_set():
            self.halt.wait(Dev.lead)
        self._finish()

    def _finish(self) -> None:
        with self.lock:
            if self.finished:
                return
            self.finished = True
        if self.fin is not None:
            self.fin()

    def abort(self, ignore_errors: bool = True) -> None:
        self.halt.set()
        if self.thread is not None and threading.current_thread() is not self.thread:
            self.thread.join(2.0)

    def close(self, ignore_errors: bool = True) -> None:
        return None


BUS.sd = types.SimpleNamespace(OutputStream=FakeStream, CallbackStop=CallbackStop)

PER_CHAR_S = 0.08


class FakeTTS:
    rate = 22050

    def __init__(self) -> None:
        self.said: list[str] = []

    def synthesise(self, text: str) -> Synthesis:
        self.said.append(text)
        n = int(self.rate * PER_CHAR_S * max(1, len(text)))
        t = np.arange(n) / self.rate
        samples = (9000.0 * np.sin(2 * np.pi * 220.0 * t)).astype(np.int16)
        return Synthesis(samples=samples, sample_rate=self.rate, first_sample_ms=1.0, total_s=0.001, chunks=1)


class FakeSTT:
    def transcribe(self, audio: Any, sr: int) -> Any:
        return types.SimpleNamespace(text="", rms=3000.0, too_quiet=False)


class FakeMic:
    def __init__(self) -> None:
        self.armed = False
        self.heard_speech = False

    def arm(self) -> np.ndarray:
        self.armed = True
        return np.zeros(0, dtype=np.int16)

    def disarm(self) -> Capture:
        self.armed = False
        s = np.zeros(16000, dtype=np.int16)
        return Capture(samples=s, sample_rate=16000, duration_s=1.0, peak=0, rms=0.0, first_sample_latency_ms=0.0)

    def watch_for_silence(self, *, on_close: Any, **kw: Any) -> None:
        return None

    def cancel_watch(self) -> None:
        return None

    def flush_ring(self) -> int:
        return 0


class FakeBrain:
    name = "fake-brain"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, system: str, messages: Any, **kw: Any) -> Any:
        self.calls += 1
        return types.SimpleNamespace(text='{"kind": "chat"}')

    def stream(self, system: str, messages: Any, max_tokens: int = 0, quality: Any = None) -> Any:
        self.calls += 1
        yield "A fake answer, Emperor."


async def until(cond: Any, timeout: float = 10.0, step: float = 0.005) -> bool:
    t0 = pc()
    while pc() - t0 < timeout:
        if cond():
            return True
        await asyncio.sleep(step)
    return bool(cond())


async def build() -> Any:
    d = SRV.TessaDaemon(dev=False)
    rig = types.SimpleNamespace(d=d, brain=FakeBrain(), mic=FakeMic(), stt=FakeSTT(), tts=FakeTTS())
    d.brain = rig.brain
    d.attach_voice(mic=rig.mic, stt=rig.stt, tts=rig.tts)
    return rig


async def quiet(rig: Any, timeout: float = 20.0) -> bool:
    d = rig.d

    def calm() -> bool:
        bus = getattr(d.voice, "bus", None) if d.voice is not None else None
        return (not d._typed_jobs and not d._typed_tasks and not d._speech_tasks
                and not d._floor.locked() and not d._voice_turns
                and (bus is None or not bus.is_speaking))

    ok = await until(calm, timeout)
    await asyncio.sleep(0.1)
    return ok and calm()


def frame(mtype: str, payload: dict[str, Any]) -> str:
    return SRV.envelope(mtype, payload)


def hello_of(d: Any) -> str:
    return frame("cmd.hello", {"token": d.token, "surface": "orb", "surfaceVersion": "test_up",
                               "protocolVersion": SRV.PROTOCOL_VERSION})


class Link:
    def __init__(self, url: str) -> None:
        self.url = url
        self.ws: Any = None
        self.frames: list[dict[str, Any]] = []
        self.reader: asyncio.Task | None = None

    async def open(self) -> None:
        self.ws = await ws_connect(self.url, additional_headers={"Origin": "tessa://orb"})

    async def hello(self, d: Any) -> dict[str, Any]:
        await self.ws.send(hello_of(d))
        return json.loads(await self.ws.recv())

    async def subscribe(self, topics: list[str]) -> None:
        sub = frame("cmd.subscribe", {"topics": topics})
        cid = json.loads(sub)["id"]
        await self.ws.send(sub)
        want_state = SRV.topic_matches(set(topics), "evt.agent.state")
        acked = False
        while True:
            f = json.loads(await self.ws.recv())
            self.frames.append(f)
            acked = acked or f.get("corr") == cid
            if acked and (not want_state or f["type"] == "evt.agent.state"):
                break
        self.reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        try:
            async for raw in self.ws:
                self.frames.append(json.loads(raw))
        except websockets.exceptions.ConnectionClosed:
            return

    def states(self) -> list[str]:
        return [f["payload"]["state"] for f in self.frames if f["type"] == "evt.agent.state"]

    def lines(self, role: str) -> list[str]:
        return [f["payload"]["message"]["text"] for f in self.frames
                if f["type"] == "evt.transcript.message" and f["payload"]["message"]["role"] == role]

    async def close(self) -> None:
        await self.ws.close()
        if self.reader is not None:
            await asyncio.wait([self.reader], timeout=2.0)


class Gate:
    def __init__(self, d: Any) -> None:
        self.d = d
        self.real_dispatch = d._dispatch
        self.real_hello = d._do_hello
        self.on_subscribe = False
        self.on_hello = False
        self.dispatched: list[str] = []
        self.hello_failed_send = 0
        d._dispatch = self.dispatch
        d._do_hello = self.hello

    async def dispatch(self, ws: Any, state: dict[str, Any], raw: Any) -> None:
        mtype = json.loads(raw).get("type", "")
        if self.on_subscribe and mtype == "cmd.subscribe":
            await asyncio.wait_for(ws.wait_closed(), 5.0)
        self.dispatched.append(mtype)
        await self.real_dispatch(ws, state, raw)

    async def hello(self, ws: Any, state: dict[str, Any], raw: Any) -> bool:
        if self.on_hello:
            await asyncio.wait_for(ws.wait_closed(), 5.0)
        return await self.real_hello(ws, state, raw)


def hello_rows(d: Any) -> int:
    return sum(1 for e in d.audit if e.get("tool") == "auth.hello")


async def t1_queued_frames() -> None:
    print("\n1. a reply that fails on a closed link never ends the loop or drops the frames behind it")
    rig = await build()
    d = rig.d
    gate = Gate(d)
    async with ws_serve(d.handle, "127.0.0.1", 0, process_request=d.process_request) as server:
        port = server.sockets[0].getsockname()[1]
        url = f"ws://127.0.0.1:{port}{SRV.WS_PATH}"
        watcher = Link(url)
        await watcher.open()
        await watcher.hello(d)
        await watcher.subscribe(["agent.*", "transcript.*"])

        said0, mark = len(rig.tts.said), len(watcher.frames)
        gate.on_subscribe = True
        gate.dispatched.clear()
        s = Link(url)
        await s.open()
        res = await s.hello(d)
        await s.ws.send(frame("cmd.subscribe", {"topics": ["agent.*", "transcript.*"]}))
        await s.ws.send(frame("cmd.agent.message", {"companionId": "tessa", "text": "what day is it"}))
        await s.ws.close()
        ok = await until(lambda: len(rig.tts.said) > said0, 10.0)
        await quiet(rig)
        gate.on_subscribe = False
        mine = watcher.frames[mark:]
        user = [f["payload"]["message"]["text"] for f in mine if f["type"] == "evt.transcript.message"
                and f["payload"]["message"]["role"] == "user"]
        her = [f["payload"]["message"]["text"] for f in mine if f["type"] == "evt.transcript.message"
               and f["payload"]["message"]["role"] == "assistant"]
        states = [f["payload"]["state"] for f in mine if f["type"] == "evt.agent.state"]
        check("M7 shape: res.hello reached the link before it closed", res.get("type") == "res.hello")
        check("M7 shape: the subscribe reply failed on the closed link and the message behind it was still dispatched",
              gate.dispatched == ["cmd.subscribe", "cmd.agent.message"], str(gate.dispatched))
        check("M7 shape: she answered it out loud", ok and len(rig.tts.said) == said0 + 1, str(rig.tts.said[said0:]))
        check("M7 shape: another surface saw his line and her answer", user == ["what day is it"] and len(her) == 1,
              f"user={user} her={her}")
        check("M7 shape: another surface saw the whole turn", states == ["thinking", "speaking", "idle"], str(states))
        check("M7 shape: no turn left registered", d._typed_jobs == {} and not d._typed_tasks)

        said0, rows0 = len(rig.tts.said), hello_rows(d)
        gate.on_hello = True
        gate.dispatched.clear()
        s2 = Link(url)
        await s2.open()
        await s2.ws.send(hello_of(d))
        await s2.ws.send(frame("cmd.subscribe", {"topics": ["agent.*"]}))
        await s2.ws.send(frame("cmd.agent.message", {"companionId": "tessa", "text": "what time is it"}))
        await s2.ws.close()
        ok = await until(lambda: len(rig.tts.said) > said0, 10.0)
        await quiet(rig)
        gate.on_hello = False
        check("hello unread: res.hello failed on the closed link, the frames behind it were still dispatched",
              gate.dispatched == ["cmd.subscribe", "cmd.agent.message"], str(gate.dispatched))
        check("hello unread: she answered it", ok and len(rig.tts.said) == said0 + 1, str(rig.tts.said[said0:]))
        check("hello unread: the connection is still on the audit chain", hello_rows(d) == rows0 + 1,
              f"{rows0} -> {hello_rows(d)}")
        check("hello unread: its AUTH line is still logged",
              sum(1 for x in LOG if "AUTH ok surface=orb" in x) >= 3)

        said0 = len(rig.tts.said)
        gate.on_subscribe = True
        gate.dispatched.clear()
        s3 = Link(url)
        await s3.open()
        await s3.hello(d)
        await s3.ws.send(frame("cmd.subscribe", {"topics": ["agent.*"]}))
        for _ in range(3):
            await s3.ws.send(frame("cmd.ping", {}))
        await s3.ws.send(frame("cmd.agent.message", {"companionId": "tessa", "text": "what day is it"}))
        await s3.ws.close()
        ok = await until(lambda: len(rig.tts.said) > said0, 10.0)
        await quiet(rig)
        gate.on_subscribe = False
        check("every queued frame is dispatched after the failed reply, in order",
              gate.dispatched == ["cmd.subscribe", "cmd.ping", "cmd.ping", "cmd.ping", "cmd.agent.message"],
              str(gate.dispatched))
        check("...and the message at the end of the queue is answered", ok and len(rig.tts.said) == said0 + 1)

        said0 = len(rig.tts.said)
        m6 = Link(url)
        await m6.open()
        await m6.hello(d)
        await m6.subscribe(["agent.*"])
        await m6.ws.send(frame("cmd.agent.message", {"companionId": "tessa", "text": "what time is it"}))
        await m6.ws.close()
        ok = await until(lambda: len(rig.tts.said) > said0, 10.0)
        await quiet(rig)
        check("M6 shape (a subscribed link sends and closes at once) is still answered",
              ok and len(rig.tts.said) == said0 + 1)

        await asyncio.sleep(0.2)
        check("closed links are not kept as clients", len(d.clients) == 1, str(len(d.clients)))
        pong = frame("cmd.ping", {})
        cid = json.loads(pong)["id"]
        n0 = len(watcher.frames)
        await watcher.ws.send(pong)
        got = await until(lambda: any(f.get("corr") == cid for f in watcher.frames[n0:]), 3.0)
        check("the surface that stayed open still gets its replies", got)
        await watcher.close()


async def t2_reconnect_while_speaking() -> None:
    print("\n2. a surface reconnecting while she speaks receives 'speaking', over a real socket")
    rig = await build()
    d = rig.d
    async with ws_serve(d.handle, "127.0.0.1", 0, process_request=d.process_request) as server:
        port = server.sockets[0].getsockname()[1]
        url = f"ws://127.0.0.1:{port}{SRV.WS_PATH}"
        a = Link(url)
        await a.open()
        await a.hello(d)
        await a.subscribe(["agent.*", "transcript.*"])
        before = len(Dev.streams)
        await a.ws.send(frame("cmd.agent.message", {"companionId": "tessa", "text": "what day is it"}))
        playing = await until(lambda: len(Dev.streams) > before and Dev.streams[-1].played > 3, 10.0, 0.002)
        await a.close()
        r = Link(url)
        await r.open()
        await r.hello(d)
        await r.subscribe(["agent.*"])
        await asyncio.sleep(0.3)
        first = r.states()[:1]
        still = Dev.streams[-1].played if Dev.streams else 0
        check("she was speaking when the surface came back", playing and not Dev.streams[-1].finished, f"buffers {still}")
        check("the returning surface's first state is 'speaking'", first == ["speaking"], str(r.states()))
        await quiet(rig)
        await asyncio.sleep(0.1)
        check("...and it then sees her go idle", r.states()[-1:] == ["idle"], str(r.states()))
        await r.close()
        r2 = Link(url)
        await r2.open()
        await r2.hello(d)
        await r2.subscribe(["agent.*"])
        await asyncio.sleep(0.2)
        check("a surface returning after she finished sees 'idle'", r2.states()[:1] == ["idle"], str(r2.states()))
        await r2.close()


u32 = ctypes.WinDLL("user32", use_last_error=True)
u32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
u32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
u32.GetAncestor.restype = wt.HWND
u32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
u32.GetParent.restype = wt.HWND
u32.GetParent.argtypes = [wt.HWND]
u32.GetDesktopWindow.restype = wt.HWND
u32.IsWindowVisible.argtypes = [wt.HWND]
u32.IsWindow.argtypes = [wt.HWND]
u32.FindWindowExW.restype = wt.HWND
u32.FindWindowExW.argtypes = [wt.HWND, wt.HWND, wt.LPCWSTR, wt.LPCWSTR]
u32.SendMessageTimeoutW.restype = ctypes.c_ssize_t
u32.SendMessageTimeoutW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM, wt.UINT, wt.UINT,
                                    ctypes.POINTER(ctypes.c_size_t)]
_ENUM = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
WM_CLOSE, WM_QUERYENDSESSION, WM_ENDSESSION = 0x0010, 0x0011, 0x0016
HWND_MESSAGE = wt.HWND(-3)


def top_windows(pid: int, cls: str) -> list[int]:
    found: list[int] = []

    def cb(hwnd: int, _l: int) -> bool:
        owner = wt.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            buf = ctypes.create_unicode_buffer(128)
            u32.GetClassNameW(hwnd, buf, 128)
            if buf.value == cls:
                found.append(hwnd)
        return True

    u32.EnumWindows(_ENUM(cb), 0)
    return found


def send(hwnd: int, msg: int, wparam: int, lparam: int, timeout_ms: int = 10000) -> tuple[int, float, int]:
    res = ctypes.c_size_t(0)
    t0 = pc()
    ok = u32.SendMessageTimeoutW(hwnd, msg, wparam, lparam, 0x0002, timeout_ms, ctypes.byref(res))
    return int(ok), (pc() - t0) * 1000.0, int(res.value)


def t3_end_session_window() -> None:
    print("\n3. the end-session window: a real top-level window that hears restart, shutdown and sign-out")
    LC.start_end_session_watch()
    wins = top_windows(os.getpid(), LC.END_SESSION_CLASS)
    check("EnumWindows finds exactly one end-session window of this process", len(wins) == 1, str(wins))
    if not wins:
        return
    h = wins[0]
    check("its parent is the desktop, not the message-only parent",
          u32.GetAncestor(h, 1) == u32.GetDesktopWindow() and not u32.GetParent(h))
    check("it is not a message-only window",
          not u32.FindWindowExW(HWND_MESSAGE, None, LC.END_SESSION_CLASS, None))
    check("it is never shown", not u32.IsWindowVisible(h))

    ok, ms, res = send(h, WM_QUERYENDSESSION, 0, 0)
    check("WM_QUERYENDSESSION is answered yes at once, never blocking Windows", ok and res == 1 and ms < 200,
          f"{ms:.1f} ms")
    NUMBERS["queryendsession_ms"] = round(ms, 2)

    cases = [("restart or shutdown (lParam 0)", 0, "session end (Windows shutdown)"),
             ("forced shutdown (ENDSESSION_CRITICAL)", 0x40000000, "session end (Windows shutdown)"),
             ("sign-out (ENDSESSION_LOGOFF)", 0x80000000, "session end (sign-out)")]
    for label, lparam, want in cases:
        LC.STOPPER = LC.Stopper()
        stopper = LC.STOPPER
        threading.Timer(0.05, stopper.done.set).start()
        ok, ms, _res = send(h, WM_ENDSESSION, 1, lparam)
        check(f"{label}: one stop request with the right reason", stopper.reason == want, str(stopper.reason))
        check(f"{label}: the handler returns once the clean stop is done", ok and 40 <= ms < 1000, f"{ms:.1f} ms")
        NUMBERS[f"endsession_{lparam:#x}_ms"] = round(ms, 1)

    LC.STOPPER = LC.Stopper()
    stopper = LC.STOPPER
    ok, ms, _res = send(h, WM_ENDSESSION, 0, 0)
    check("a cancelled session end (wParam 0) stops nothing", stopper.reason is None and ok, str(stopper.reason))

    saved = LC.SHUTDOWN_LIMIT_S
    LC.SHUTDOWN_LIMIT_S = 0.3
    LC.STOPPER = LC.Stopper()
    ok, ms, _res = send(h, WM_ENDSESSION, 1, 0)
    LC.SHUTDOWN_LIMIT_S = saved
    check("a stop that hangs cannot hold Windows past the limit", ok and 250 <= ms < 1500, f"{ms:.1f} ms at a 0.3 s limit")
    NUMBERS["endsession_hung_ms_at_0.3s_limit"] = round(ms, 1)

    ok, _ms, _res = send(h, WM_CLOSE, 0, 0)
    check("WM_CLOSE does not close it", u32.IsWindow(h) and top_windows(os.getpid(), LC.END_SESSION_CLASS) == [h])
    LC.STOPPER = LC.Stopper()


def spawn_sleeper() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                            creationflags=0x08000000)


def dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"], creationflags=0x08000000)
    p.wait(15)
    return p.pid


def t4_stale_runtime_file() -> None:
    print("\n4. a stale runtime.json never stops her starting (throwaway runtime folder)")
    folder = TMP / "runtime"
    folder.mkdir()
    saved = RT.RUNTIME_DIR_OVERRIDE
    RT.RUNTIME_DIR_OVERRIDE = str(folder)
    sleeper = spawn_sleeper()
    try:
        path = RT.runtime_path()
        check("the test runtime folder is not his", path.parent == folder and path != REAL_RUNTIME, str(path))
        other = {"protocolVersion": 1, "port": 47600, "token": "a" * 64, "startedAt": "2026-10-08T17:35:00.000Z"}
        dead = dead_pid()
        stale = [("a dead daemon's pid", json.dumps({**other, "pid": dead})),
                 ("a pid that now belongs to ANOTHER live process", json.dumps({**other, "pid": sleeper.pid})),
                 ("a pid that is this very process (pid reuse)", json.dumps({**other, "pid": os.getpid()})),
                 ("a torn half-written file", json.dumps({**other, "pid": dead})[:37]),
                 ("an empty file", "")]
        check("the sleeper is alive and not this process", sleeper.poll() is None and sleeper.pid != os.getpid())
        for label, text in stale:
            path.write_text(text, encoding="utf-8")
            guard, held = LC.take_guard(TMP / f"data-{len(text)}", folder)
            check(f"{label}: the single-instance guard is taken", guard is not None and held == "", held)
            RT.preflight_runtime_file()
            token = RT.new_token()
            RT.write_runtime_file(48123, token)
            now = json.loads(path.read_text(encoding="utf-8"))
            check(f"{label}: runtime.json now advertises this daemon",
                  now.get("pid") == os.getpid() and now.get("token") == token and now.get("port") == 48123)
            check(f"{label}: and a clean stop removes it", RT.remove_runtime_file(token) and not path.exists())
            if guard is not None:
                guard.release()
        path.write_text(json.dumps({**other, "pid": sleeper.pid}), encoding="utf-8")
        RT.preflight_runtime_file()
        kept = json.loads(path.read_text(encoding="utf-8"))
        check("preflight leaves a live pid's entry alone until this daemon is listening", kept.get("pid") == sleeper.pid)
        check("a clean stop never removes an entry that is not its own",
              not RT.remove_runtime_file(RT.new_token()) and path.exists())
    finally:
        sleeper.kill()
        sleeper.wait(10)
        RT.RUNTIME_DIR_OVERRIDE = saved


def t5_launcher() -> None:
    print("\n5. the sign-in launcher writes its line and starts her at once (no visible wait)")
    real = ROOT / "scripts" / "start-tessa.cmd"
    raw = real.read_bytes()
    check("the launcher is ASCII, CRLF only, no BOM",
          all(b < 128 for b in raw) and raw.count(b"\n") == raw.count(b"\r\n") and not raw.startswith(b"\xef\xbb\xbf"))
    code = [ln.strip() for ln in raw.decode("ascii").splitlines()
            if ln.strip() and not ln.strip().lower().startswith("rem")]
    words = [ln.split()[0].lower() for ln in code]
    check("nothing waits or shows a window before her start: no timeout, pause, choice, title or goto",
          not {"timeout", "pause", "choice", "title", "goto"} & set(words), str(sorted(set(words))))
    first = next((i for i, ln in enumerate(code) if "==== launcher" in ln), -1)
    start = next((i for i, ln in enumerate(code) if ln.lower().startswith('start "" /b')), -1)
    check("the launcher line is written before the daemon is started", 0 <= first < start, f"{first} < {start}")
    check("she starts with voice on, as she runs now",
          start >= 0 and code[start].endswith('"C:\\dev\\tessa\\core\\server.py" --dev --voice --stt-model base '
                                              '>> "%LOGDIR%\\daemon-%STAMP%.log" 2>&1'))
    lt = TMP / "launcher"
    (lt / "logs").mkdir(parents=True)
    rec = lt / "recorder.py"
    rec.write_text(f"open(r'{lt / 'ran.txt'}', 'a').write('ran\\n')\n", encoding="ascii")
    text = raw.decode("ascii")
    for a, b in (('cd /d "C:\\dev\\tessa"', f'cd /d "{lt}"'),
                 ('set "LOGDIR=C:\\dev\\tessa\\data\\logs"', f'set "LOGDIR={lt}\\logs"'),
                 ('"C:\\dev\\tessa\\core\\server.py"', f'"{rec}"')):
        check(f"the launcher has exactly one {a.split()[0]} line to point at a scratch folder", text.count(a) == 1)
        text = text.replace(a, b)
    copy = lt / "start-tessa.cmd"
    copy.write_bytes(text.encode("ascii"))
    t0 = pc()
    subprocess.run(["cmd.exe", "/d", "/c", str(copy)], creationflags=0x08000000, timeout=30)
    ran = lt / "ran.txt"
    deadline = pc() + 10.0
    while not ran.exists() and pc() < deadline:
        time.sleep(0.02)
    took = pc() - t0
    logs = list((lt / "logs").glob("daemon-*.log"))
    lines = logs[0].read_text(encoding="ascii", errors="replace").splitlines() if logs else []
    check("a sign-in launch (no --now) writes its launcher line", any(ln.startswith("==== launcher") for ln in lines),
          str(lines[:2]))
    check("...and starts the daemon within 5 s, not after a 20 s wait", ran.exists() and took < 5.0, f"{took:.2f} s")
    NUMBERS["launcher_to_daemon_s"] = round(took, 2)


async def main() -> None:
    await t1_queued_frames()
    await t2_reconnect_while_speaking()
    t3_end_session_window()
    t4_stale_runtime_file()
    t5_launcher()


asyncio.run(main())
print("\n6. the fences")
check("no network connection was attempted", NET["attempts"] == 0, str(NET["attempts"]))
check("his audit log, thread, action log and runtime.json are untouched",
      {str(p): digest(p) for p in LIVE} == LIVE_BEFORE)
(TMP / "up-log.txt").write_text("\n".join(LOG), encoding="utf-8")
print("\nNUMBERS " + json.dumps(NUMBERS))
print(f"\n{passed} passed, {failed} failed\n")
sys.exit(0 if failed == 0 else 1)
