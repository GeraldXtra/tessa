from __future__ import annotations

import asyncio
import hashlib
import json
import socket
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
sys.dont_write_bytecode = True
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
TMP = Path(tempfile.mkdtemp(prefix="tessa-stages-test-"))
LIVE = [ROOT / "data" / "audit.log", ROOT / "data" / "memory" / "conversation.json",
        ROOT / "data" / "tessa-actions.jsonl"]


def digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "absent"


LIVE_BEFORE = {str(p): digest(p) for p in LIVE}
NET = {"attempts": 0}
_real_connect = socket.socket.connect


def _fenced_connect(self: socket.socket, address: Any, *a: Any, **k: Any) -> Any:
    host = address[0] if isinstance(address, tuple) and address else address
    if str(host) in ("127.0.0.1", "::1", "localhost"):
        return _real_connect(self, address, *a, **k)
    NET["attempts"] += 1
    raise OSError("network fenced in test_stages")


def _fenced_create(*a: Any, **k: Any) -> Any:
    NET["attempts"] += 1
    raise OSError("network fenced in test_stages")


socket.socket.connect = _fenced_connect
socket.create_connection = _fenced_create

from core.brain import memory

memory.ACTION_LOG = TMP / "actions.jsonl"

import core.bus as BUS
import core.server as SRV
from core.brain import intent_model
from core.brain.claims import ClaimStore
from core.brain.conversation import Conversation
from core.brain.style import StyleStore
from core.capabilities.vault import Vault
from core.security.audit import AuditLog
from core.telemetry.cost import CostLedger
from core.tools import coding
from core.voice.audio_io import ArmedMicrophone
from core.voice.tts.base import Synthesis

coding.DATA = TMP / "data-code"
coding.code_folder = lambda: TMP / "Tessa Code"

passed = failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok    {name}" + (f"  {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


LOG: list[str] = []
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
SRV.log = lambda msg: LOG.append(msg)
intent_model._engine_for = lambda brain: brain


class CallbackStop(Exception):
    pass


class FakeStream:
    block = 576

    def __init__(self, samplerate: int, channels: int, dtype: str, callback: Any,
                 finished_callback: Any = None, **kw: Any) -> None:
        self.rate = int(samplerate)
        self.cb = callback
        self.fin = finished_callback
        self.halt = threading.Event()
        self.thread: threading.Thread | None = None
        self.done = False

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        period = self.block / self.rate
        while not self.halt.is_set():
            out = np.zeros((self.block, 1), dtype=np.int16)
            now = time.perf_counter()
            try:
                self.cb(out, self.block, types.SimpleNamespace(currentTime=now, outputBufferDacTime=now + 0.05), None)
            except CallbackStop:
                break
            self.halt.wait(period)
        if not self.done:
            self.done = True
            if self.fin is not None:
                self.fin()

    def abort(self, ignore_errors: bool = True) -> None:
        self.halt.set()
        if self.thread is not None and threading.current_thread() is not self.thread:
            self.thread.join(2.0)

    def close(self, ignore_errors: bool = True) -> None:
        return None


BUS.sd = types.SimpleNamespace(OutputStream=FakeStream, CallbackStop=CallbackStop)


class FakeTTS:
    rate = 22050

    def __init__(self) -> None:
        self.said: list[str] = []

    def synthesise(self, text: str) -> Synthesis:
        self.said.append(text)
        n = int(self.rate * 0.03 * max(1, len(text)))
        t = np.arange(n) / self.rate
        samples = (6000.0 * np.sin(2 * np.pi * 220.0 * t)).astype(np.int16)
        return Synthesis(samples=samples, sample_rate=self.rate, first_sample_ms=1.0, total_s=0.001, chunks=1)


class FakeSTT:
    def __init__(self) -> None:
        self.next = ""

    def transcribe(self, audio: Any, sr: int) -> Any:
        return types.SimpleNamespace(text=self.next, rms=3000.0, too_quiet=False)


class FakeBrain:
    name = "fake-brain"

    def __init__(self) -> None:
        self.reply = "Snails have thousands of tiny teeth, Emperor."
        self.delay = 0.0
        self.plan: list[tuple[float, str]] = []
        self.calls = 0

    def complete(self, system: str, messages: Any, **kw: Any) -> Any:
        return types.SimpleNamespace(text='{"kind": "chat"}')

    def stream(self, system: str, messages: Any, max_tokens: int = 0, quality: Any = None) -> Any:
        self.calls += 1
        delay, reply = self.plan.pop(0) if self.plan else (self.delay, self.reply)
        if delay:
            time.sleep(delay)
        yield reply


class FakeWs:
    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []

    async def send(self, raw: str) -> None:
        self.frames.append(json.loads(raw))

    async def close(self, *a: Any) -> None:
        return None


VAD = {"vad_silence_ms": 300, "vad_floor_rms": 300, "vad_hard_cap_s": 6, "vad_start_timeout_s": 0.6}
SR = 16000


async def build() -> Any:
    d = SRV.TessaDaemon(dev=False)
    rig = types.SimpleNamespace(d=d, brain=FakeBrain(), mic=ArmedMicrophone(pre_roll_s=1.0), stt=FakeSTT(),
                                tts=FakeTTS(), orb=FakeWs())
    d.brain = rig.brain
    d.attach_voice(mic=rig.mic, stt=rig.stt, tts=rig.tts)
    d.voice.vad_config = dict(VAD)
    d.clients[rig.orb] = {"authed": True, "surface": "orb", "subs": {"agent.*", "transcript.*", "voice.*",
                                                                    "permission.*"}, "sessionId": SRV.ulid()}
    return rig


async def send(rig: Any, mtype: str, payload: dict[str, Any]) -> None:
    await rig.d._dispatch(rig.orb, rig.d.clients[rig.orb], SRV.envelope(mtype, payload))


async def until(cond: Any, timeout: float = 8.0, step: float = 0.01) -> bool:
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        if cond():
            return True
        await asyncio.sleep(step)
    return bool(cond())


async def quiet(rig: Any, timeout: float = 10.0) -> bool:
    d = rig.d

    def calm() -> bool:
        return (not d._typed_jobs and not d._typed_tasks and not d._speech_tasks and not d._voice_turns
                and not d.voice.bus.is_speaking)

    await asyncio.sleep(0.15)
    ok = await until(calm, timeout)
    await asyncio.sleep(0.15)
    return ok and await until(calm, timeout)


def tone(freq: float, seconds: float, amp: float = 8000.0) -> np.ndarray:
    t = np.arange(int(SR * seconds)) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.int16)


async def feed_live(mic: ArmedMicrophone, samples: np.ndarray, block: int = 800) -> None:
    for i in range(0, len(samples), block):
        part = samples[i:i + block]
        mic._callback(part.reshape(-1, 1), len(part), None, None)
        await asyncio.sleep(block / SR)


def rows(d: Any) -> list[dict[str, Any]]:
    return list(d.audit)


def names(trail: str) -> list[str]:
    return [part.split("@")[0].split(" ")[0] for part in trail.split(" -> ") if part]


async def interleaved() -> None:
    print("\n1. two spoken turns interleaved: the stalled one's audit row holds only its own stages\n")
    rig = await build()
    d = rig.d
    rig.brain.delay = 2.5
    rig.stt.next = "do snails have teeth"
    saved = SRV.VOICE_TURN_STALL_S
    SRV.VOICE_TURN_STALL_S = 1.6
    try:
        await send(rig, "cmd.voice.pushToTalk", {"action": "start"})
        await feed_live(rig.mic, tone(300, 0.3))
        await send(rig, "cmd.voice.pushToTalk", {"action": "stop"})
        entered = await until(lambda: rig.brain.calls == 1, 5.0)
        check("the first turn is waiting on the model", entered)
        rig.stt.next = "the time on the clock"
        await send(rig, "cmd.voice.pushToTalk", {"action": "start"})
        await feed_live(rig.mic, tone(300, 0.3))
        await send(rig, "cmd.voice.pushToTalk", {"action": "stop"})
        second_done = await until(lambda: any(r["tool"].startswith("voice.turn.") and r["tool"] != "voice.turn.stalled"
                                              and "clock" in str(r["detail"].get("heard", "")) for r in rows(d)), 6.0)
        check("the second turn finished while the first was still in the model", second_done
              and rig.brain.calls == 1)
        await until(lambda: any(r["tool"] == "voice.turn.stalled" for r in rows(d)), 6.0)
        await asyncio.sleep(1.5)
        await quiet(rig)
    finally:
        SRV.VOICE_TURN_STALL_S = saved
    stalled = [r for r in rows(d) if r["tool"] == "voice.turn.stalled"]
    check("exactly one stall row was written", len(stalled) == 1, str(len(stalled)))
    if not stalled:
        return
    trail = str(stalled[0]["detail"].get("stages") or "")
    got = names(trail)
    print(f"        stall row stages: {got}")
    check("the stall row names the first turn's own last stage (the model call)",
          stalled[0]["detail"].get("lastStage", "").startswith("brain.entered"), stalled[0]["detail"].get("lastStage"))
    check("...and holds the first turn's model path", "intent.entered" in got and "brain.entered" in got, str(got))
    check("...and none of the second turn's stages (it never synthesised or played anything)",
          not any(n.startswith(("synthesise.", "playback.")) for n in got), str(got))
    check("...and its stages are one turn's, not two (one disarm, one transcribe)",
          got.count("disarm.returned") == 1 and got.count("transcribe.entered") == 1, str(got))
    check("the later answer of the stalled turn was never spoken",
          rig.brain.reply not in rig.tts.said, str(rig.tts.said))
    dropped = [x for x in LOG if "turn.dropped" in x]
    check("the stalled turn was dropped for its own reason: the press that replaced it came first",
          any("a new press replaced it" in x for x in dropped), str(dropped)[:200])


async def stall_spares_newer() -> None:
    print("\n2. an older turn's stall drops only that turn, never a newer one still in flight\n")
    rig = await build()
    d = rig.d
    old_reply = "Snails have thousands of tiny teeth, Emperor."
    new_reply = "Snails grow new teeth all their lives, Emperor."
    rig.brain.plan = [(6.0, old_reply), (3.0, new_reply)]
    rig.stt.next = "do snails have teeth"
    saved = SRV.VOICE_TURN_STALL_S
    SRV.VOICE_TURN_STALL_S = 4.0
    seen: dict[str, Any] = {}
    try:
        await send(rig, "cmd.voice.pushToTalk", {"action": "start"})
        await feed_live(rig.mic, tone(300, 0.3))
        await send(rig, "cmd.voice.pushToTalk", {"action": "stop"})
        await until(lambda: rig.brain.calls == 1, 5.0)
        await asyncio.sleep(1.5)
        await send(rig, "cmd.voice.pushToTalk", {"action": "start"})
        await feed_live(rig.mic, tone(300, 0.3))
        await send(rig, "cmd.voice.pushToTalk", {"action": "stop"})
        seen["second_in"] = await until(lambda: rig.brain.calls == 2, 5.0)
        seen["stall"] = await until(lambda: any(r["tool"] == "voice.turn.stalled" for r in rows(d)), 8.0)
        seen["in_flight_at_stall"] = rig.brain.calls == 2 and new_reply not in rig.tts.said
        seen["spoke"] = await until(lambda: new_reply in rig.tts.said, 8.0)
        await asyncio.sleep(2.5)
        await quiet(rig)
    finally:
        SRV.VOICE_TURN_STALL_S = saved
    stalled = [r for r in rows(d) if r["tool"] == "voice.turn.stalled"]
    check("the newer turn reached the model while the older one was still in it", bool(seen.get("second_in")))
    check("the older turn's stall row was written while the newer turn was in flight",
          bool(seen.get("stall")) and bool(seen.get("in_flight_at_stall")), str(seen))
    check("the newer turn's answer was still spoken after the older turn's stall", bool(seen.get("spoke")),
          str(rig.tts.said))
    check("the older turn's late answer was never spoken", old_reply not in rig.tts.said, str(rig.tts.said))
    check("only the older turn stalled", len(stalled) == 1, str(len(stalled)))


def per_turn_state() -> None:
    print("\n3. the per-turn state each thread keeps\n")
    rig = asyncio.run(build())
    v = rig.d.voice
    ex = v.executor
    a_list: list = []
    b_list: list = []
    v.stages = b_list
    seen: dict[str, Any] = {}
    go = threading.Event()

    def turn_a() -> None:
        v._turn_local.stages = a_list
        v._set_verdict("score-of-a")
        go.wait(2.0)
        v._stage("late.stage.of.a", time.perf_counter())
        seen["a_verdict"] = ex._voice_verdict()

    def turn_b() -> None:
        v._turn_local.stages = b_list
        seen["b_verdict"] = ex._voice_verdict()
        v._stage("stage.of.b", time.perf_counter())
        go.set()

    ta = threading.Thread(target=turn_a)
    tb = threading.Thread(target=turn_b)
    ta.start()
    tb.start()
    ta.join(3.0)
    tb.join(3.0)
    check("a late stage of an older turn goes to its own list, not the newest turn's",
          [n for n, _ms in a_list] == ["late.stage.of.a"] and [n for n, _ms in b_list] == ["stage.of.b"],
          f"a={[n for n, _ in a_list]} b={[n for n, _ in b_list]}")
    check("a turn's voiceprint verdict is seen only by that turn's thread",
          seen.get("a_verdict") == "score-of-a" and seen.get("b_verdict") is None, str(seen))
    check("a thread with no spoken turn (a typed turn) sees no verdict", ex._voice_verdict() is None)
    g = v._gen
    v.abandon("it stalled for 60 s")
    v._supersede("a new press replaced it")
    t0 = time.perf_counter()
    v._turn_local.gen = g
    first = v._dropped("x", "", t0, 0.0).dropped
    v._turn_local.gen = g + 1
    second = v._dropped("x", "", t0, 0.0).dropped
    v._turn_local.gen = None
    v._turn_local.stages = None
    check("each dropped turn keeps the reason that replaced it, not a later one",
          first == "it stalled for 60 s" and second == "a new press replaced it", f"{first!r} / {second!r}")


asyncio.run(interleaved())
asyncio.run(stall_spares_newer())
per_turn_state()

print("\n(fences)\n")
check("no network connection was attempted", NET["attempts"] == 0, str(NET["attempts"]))
check("her live audit log, thread and action log are untouched", {str(p): digest(p) for p in LIVE} == LIVE_BEFORE)
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
