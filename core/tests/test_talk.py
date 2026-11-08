from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import inspect
import json
import socket
import statistics
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

TMP = Path(tempfile.mkdtemp(prefix="tessa-talk-"))
LIVE = [ROOT / "data" / "audit.log", ROOT / "data" / "memory" / "conversation.json",
        ROOT / "data" / "tessa-actions.jsonl"]


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
    raise OSError("network fenced in test_talk")


def _fenced_create(address: Any, *a: Any, **k: Any) -> Any:
    if _loopback(address):
        return _real_create(address, *a, **k)
    NET["attempts"] += 1
    raise OSError("network fenced in test_talk")


socket.socket.connect = _fenced_connect
socket.create_connection = _fenced_create

import core.bus as BUS
import core.server as SRV
from core.brain import intent_model, memory
from core.brain import router as ROUTER
from core.brain.approvals import APPROVAL_WINDOW_S
from core.brain.claims import ClaimStore
from core.brain.conversation import Conversation
from core.brain.humanness import humanise, spoken_form
from core.brain.llm import LLMUnavailable
from core.brain.style import StyleStore
from core.capabilities.vault import Vault
from core.security.audit import AuditLog
from core.telemetry.cost import CostLedger
from core.tools import REGISTRY
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


REAL_DATETIME = ROUTER.datetime
FIXED_DAY = REAL_DATETIME(2026, 3, 14, 10, 30)


class FixedDay(REAL_DATETIME):
    @classmethod
    def now(cls, tz: Any = None) -> Any:
        return FIXED_DAY


def pct(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))] if s else float("nan")


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
    fail_open = False
    fail_start = False
    hang = False
    live = 0
    max_live = 0
    streams: list[Any] = []
    probe: Any = None
    lock = threading.Lock()


class FakeStream:
    def __init__(self, samplerate: int, channels: int, dtype: str, callback: Any,
                 finished_callback: Any = None, **kw: Any) -> None:
        if Dev.fail_open:
            raise RuntimeError("no output device (fake)")
        self.rate = int(samplerate)
        self.cb = callback
        self.fin = finished_callback
        self.halt = threading.Event()
        self.thread: threading.Thread | None = None
        self.played: list[tuple[float, np.ndarray]] = []
        self.started_at: float | None = None
        self.ended_at: float | None = None
        self.aborted_at: float | None = None
        self.finished = False
        Dev.streams.append(self)

    def start(self) -> None:
        if Dev.fail_start:
            raise RuntimeError("the output device went away (fake)")
        with Dev.lock:
            Dev.live += 1
            Dev.max_live = max(Dev.max_live, Dev.live)
        self.started_at = pc()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        period = Dev.block / self.rate
        nxt = pc()
        drained = False
        while not self.halt.is_set():
            out = np.zeros((Dev.block, 1), dtype=np.int16)
            now = pc()
            stop = False
            try:
                self.cb(out, Dev.block, types.SimpleNamespace(currentTime=now, outputBufferDacTime=now + Dev.lead), None)
            except CallbackStop:
                stop = True
            self.played.append((now, out[:, 0].copy()))
            if Dev.probe is not None:
                Dev.probe(now)
            if stop:
                drained = True
                break
            if Dev.hang and len(self.played) >= 3:
                self.halt.wait()
                break
            nxt += period
            wait = nxt - pc()
            if wait > 0:
                self.halt.wait(wait)
        if drained and not self.halt.is_set():
            self.halt.wait(Dev.lead)
        self._finish()

    def _finish(self) -> None:
        with Dev.lock:
            if self.finished:
                return
            self.finished = True
            Dev.live -= 1
        self.ended_at = pc()
        if self.fin is not None:
            self.fin()

    def abort(self, ignore_errors: bool = True) -> None:
        self.aborted_at = pc()
        self.halt.set()
        if self.thread is not None and threading.current_thread() is not self.thread:
            self.thread.join(2.0)

    def close(self, ignore_errors: bool = True) -> None:
        return None


BUS.sd = types.SimpleNamespace(OutputStream=FakeStream, CallbackStop=CallbackStop)

PER_CHAR_S = 0.05


class FakeTTS:
    rate = 22050

    def __init__(self) -> None:
        self.said: list[str] = []
        self.made: list[tuple[str, int]] = []
        self.fail = False

    def text_of(self, stream: Any) -> str:
        frames = len(stream.played) * Dev.block
        for text, n in self.made:
            if frames - Dev.block < n <= frames:
                return text
        return ""

    def synthesise(self, text: str) -> Synthesis:
        if self.fail:
            raise RuntimeError("piper is broken (fake)")
        self.said.append(text)
        n = int(self.rate * PER_CHAR_S * max(1, len(text)))
        self.made.append((text, n))
        t = np.arange(n) / self.rate
        env = 0.2 + 0.8 * np.abs(np.sin(2 * np.pi * 2.5 * t))
        samples = (9000.0 * env * np.sin(2 * np.pi * 220.0 * t)).astype(np.int16)
        return Synthesis(samples=samples, sample_rate=self.rate, first_sample_ms=1.0, total_s=0.001, chunks=1)


class FakeSTT:
    def __init__(self) -> None:
        self.next = ""

    def transcribe(self, audio: Any, sr: int) -> Any:
        return types.SimpleNamespace(text=self.next, rms=3000.0, too_quiet=False)


class FakeMic:
    def __init__(self) -> None:
        self.armed = False
        self.heard_speech = False
        self.on_close: Any = None
        self.arms = 0
        self.disarms = 0
        self.speech = False

    def arm(self) -> np.ndarray:
        self.armed = True
        self.heard_speech = False
        self.arms += 1
        return np.zeros(0, dtype=np.int16)

    def disarm(self) -> Capture:
        self.armed = False
        self.disarms += 1
        if self.speech:
            s = np.random.default_rng(7).normal(0, 3000, 16000).astype(np.int16)
        else:
            s = np.zeros(16000, dtype=np.int16)
        peak = int(np.max(np.abs(s.astype(np.int32))))
        rms = float(np.sqrt(np.mean(s.astype(np.float64) ** 2)))
        return Capture(samples=s, sample_rate=16000, duration_s=1.0, peak=peak, rms=rms, first_sample_latency_ms=0.0)

    def watch_for_silence(self, *, on_close: Any, **kw: Any) -> None:
        self.on_close = on_close

    def cancel_watch(self) -> None:
        return None

    def flush_ring(self) -> int:
        return 0


class FakeBrain:
    name = "fake-brain"

    def __init__(self) -> None:
        self.reply = "Air scatters blue light more than red, Emperor, so the sky looks blue."
        self.delay = 0.0
        self.error: Exception | None = None
        self.calls = 0
        self.intent_calls = 0

    def complete(self, system: str, messages: Any, **kw: Any) -> Any:
        self.intent_calls += 1
        return types.SimpleNamespace(text='{"kind": "chat"}')

    def stream(self, system: str, messages: Any, max_tokens: int = 0, quality: Any = None) -> Any:
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        yield self.reply


class FakeWs:
    def __init__(self) -> None:
        self.frames: list[tuple[float, dict[str, Any], int]] = []

    async def send(self, raw: str) -> None:
        self.frames.append((pc(), json.loads(raw), len(raw.encode("utf-8"))))

    async def close(self, *a: Any) -> None:
        return None

    def of(self, kind: str, start: int = 0) -> list[tuple[float, dict[str, Any], int]]:
        return [f for f in self.frames[start:] if f[1]["type"] == kind]

    def states(self, start: int = 0) -> list[str]:
        return [f[1]["payload"]["state"] for f in self.frames[start:] if f[1]["type"] == "evt.agent.state"]


ORB_TOPICS = {"agent.*", "transcript.*", "voice.*", "permission.*", "daemon.*", "pty.*"}
CONSOLE_TOPICS = {"pty.*", "permission.*", "daemon.*", "sentinel.*"}


async def build(voice: bool = True) -> Any:
    d = SRV.TessaDaemon(dev=False)
    rig = types.SimpleNamespace(d=d, brain=FakeBrain(), mic=None, stt=None, tts=None)
    d.brain = rig.brain
    if voice:
        rig.mic, rig.stt, rig.tts = FakeMic(), FakeSTT(), FakeTTS()
        d.attach_voice(mic=rig.mic, stt=rig.stt, tts=rig.tts)
    rig.orb, rig.console = FakeWs(), FakeWs()
    d.clients[rig.orb] = {"authed": True, "surface": "orb", "subs": set(ORB_TOPICS), "sessionId": SRV.ulid()}
    d.clients[rig.console] = {"authed": True, "surface": "console", "subs": set(CONSOLE_TOPICS),
                              "sessionId": SRV.ulid()}
    return rig


async def send(rig: Any, mtype: str, payload: dict[str, Any], ws: Any = None) -> dict[str, Any] | None:
    ws = ws or rig.orb
    raw = SRV.envelope(mtype, payload)
    cid = json.loads(raw)["id"]
    await rig.d._dispatch(ws, rig.d.clients[ws], raw)
    for _t, f, _n in reversed(ws.frames):
        if f.get("corr") == cid:
            return f
    return None


async def until(cond: Any, timeout: float = 10.0, step: float = 0.005) -> bool:
    t0 = pc()
    while pc() - t0 < timeout:
        if cond():
            return True
        await asyncio.sleep(step)
    return bool(cond())


async def quiet(rig: Any, timeout: float = 15.0) -> bool:
    d = rig.d

    def calm() -> bool:
        bus = getattr(d.voice, "bus", None) if d.voice is not None else None
        return (not d._typed_jobs and not d._typed_tasks and not d._speech_tasks
                and not d._floor.locked() and not d._voice_turns
                and (bus is None or not bus.is_speaking))

    ok = await until(calm, timeout)
    await asyncio.sleep(0.08)
    return ok and calm()


async def say_typed(rig: Any, text: str) -> str:
    reply = await send(rig, "cmd.agent.message", {"companionId": "tessa", "text": text})
    return reply["payload"]["messageId"] if reply and reply["type"] == "res.agent.accepted" else ""


async def speaking_for(seconds: float, before: int) -> FakeStream | None:
    ok = await until(lambda: len(Dev.streams) > before and len(Dev.streams[-1].played) > 0, 10.0, 0.002)
    if not ok:
        return None
    s = Dev.streams[-1]
    wait = s.played[0][0] + seconds - pc()
    if wait > 0:
        await asyncio.sleep(wait)
    return s


def tone(db: float, n: int = 22050) -> np.ndarray:
    a = 32768.0 * (2 ** 0.5) * 10 ** (db / 20.0)
    t = np.arange(n) / 22050.0
    return np.clip(np.round(a * np.sin(2 * np.pi * 441.0 * t)), -32768, 32767).astype(np.int16)


def t1_levels() -> None:
    print("\n1. the level mapping: RMS, -50 dBFS -> 0, -6 dBFS -> 1, clamped")
    check("silence is 0", BUS.level_of(np.zeros(576, dtype=np.int16)) == 0.0)
    check("an empty buffer is 0", BUS.level_of(np.zeros(0, dtype=np.int16)) == 0.0)
    hi = BUS.level_of(tone(-6.0))
    check("-6 dBFS RMS is 1", abs(hi - 1.0) <= 0.005, str(hi))
    lo = BUS.level_of(tone(-50.0))
    check("-50 dBFS RMS is 0", lo <= 0.01, str(lo))
    mid = BUS.level_of(tone(-28.0))
    check("-28 dBFS RMS is 0.5", abs(mid - 0.5) <= 0.01, str(mid))
    check("louder than -6 dBFS clamps to 1", BUS.level_of(np.full(576, 32767, dtype=np.int16)) == 1.0)
    check("quieter than -50 dBFS clamps to 0", BUS.level_of(tone(-70.0)) == 0.0)


async def t2_bus() -> None:
    print("\n2. the bus alone on a fake output device")
    events: list[tuple[float, str, Any]] = []
    bus = BUS.AudioBus(
        on_state=lambda s, d=None: events.append((pc(), "state", s.value)),
        on_level=lambda lv, seq, lead, final, cut: events.append((pc(), "level", (lv, seq, lead, final, cut))),
        on_done=lambda p: events.append((pc(), "done", p.summary())))
    syn = FakeTTS().synthesise("x" * 20)
    play = bus.speak(syn.samples, syn.sample_rate)
    await until(lambda: play.done.is_set(), 5.0)
    await until(lambda: any(e[1] == "done" for e in events), 2.0)
    stream = Dev.streams[-1]
    kinds = [(e[1], e[2]) for e in events]
    states = [k[1] for k in kinds if k[0] == "state"]
    check("states are speaking then idle", states == ["speaking", "idle"], str(states))
    first_level = next(i for i, k in enumerate(kinds) if k[0] == "level")
    check("speaking goes out before the first level", kinds.index(("state", "speaking")) < first_level)
    levels = [k[1] for k in kinds if k[0] == "level"]
    body, final = levels[:-1], levels[-1]
    check("one level per buffer the device took", len(body) == len(stream.played),
          f"{len(body)} levels, {len(stream.played)} buffers")
    check("seq counts up from 1 within the utterance", [x[1] for x in body] == list(range(1, len(body) + 1)))
    check("each level is computed from the buffer handed to the device",
          [x[0] for x in body] == [BUS.level_of(b) for _t, b in stream.played])
    check("each level carries the device's DAC lead", all(abs((x[2] or 0) - Dev.lead) < 0.005 for x in body))
    check("a final level 0 when playback ends", final[0] == 0.0 and final[3] is True and final[4] is False
          and final[1] == len(body) + 1, str(final))
    check("idle comes after the final level", kinds.index(("state", "idle")) > len(kinds) - 3)
    n = len(events)
    await asyncio.sleep(0.2)
    check("nothing while silent (no frame after the end)", len(events) == n)
    summary = [e[2] for e in events if e[1] == "done"][0]
    check("the summary counts the buffers", summary["buffers"] == len(stream.played) and summary["frames"] == 576,
          str(summary))
    events.clear()
    long = FakeTTS().synthesise("y" * 60)
    play = bus.speak(long.samples, long.sample_rate)
    await asyncio.sleep(0.5)
    t_req = pc()
    cut = bus.stop("test cut")
    kinds = [(e[1], e[2]) for e in events]
    levels = [k[1] for k in kinds if k[0] == "level"]
    check("a cut returns a measured interruption", cut is not None and cut.latency_ms < 120.0,
          f"{cut.latency_ms:.2f} ms" if cut else "None")
    check("a cut sends a final level 0 marked cut", levels and levels[-1][0] == 0.0 and levels[-1][3] and levels[-1][4])
    check("a cut does not report idle (the interrupter owns the state)",
          [k[1] for k in kinds if k[0] == "state"] == ["speaking"])
    check("the cut playback is done and the bus is silent", play.done.is_set() and not bus.is_speaking)
    n = len(events)
    await asyncio.sleep(0.2)
    check("no level after the cut", len([e for e in events[n:] if e[1] == "level"]) == 0)
    NUMBERS["bus_stop_ms"] = round((Dev.streams[-1].aborted_at - t_req) * 1000.0, 3)


async def t3_typed_spoken() -> None:
    print("\n3. a typed answer is spoken: frame order, levels, nothing listens")
    rig = await build()
    before = len(Dev.streams)
    await say_typed(rig, "what time is it")
    await quiet(rig)
    f = [(t, fr["type"], fr["payload"]) for t, fr, _n in rig.orb.frames]
    kinds = [k for _t, k, _p in f]
    idx_user = next(i for i, (_t, k, p) in enumerate(f) if k == "evt.transcript.message" and p["message"]["role"] == "user")
    idx_ack = kinds.index("res.agent.accepted")
    states = [(i, p["state"]) for i, (_t, k, p) in enumerate(f) if k == "evt.agent.state"]
    idx_think = next(i for i, s in states if s == "thinking")
    idx_speak = next(i for i, s in states if s == "speaking")
    idx_her = next(i for i, (_t, k, p) in enumerate(f) if k == "evt.transcript.message" and p["message"]["role"] == "assistant")
    amps = [(i, t, p) for i, (t, k, p) in enumerate(f) if k == "evt.voice.amplitude"]
    idx_idle = max(i for i, s in states if s == "idle")
    check("order: his line, ack, thinking, speaking, her line",
          idx_user < idx_ack < idx_think < idx_speak < idx_her, f"{idx_user},{idx_ack},{idx_think},{idx_speak},{idx_her}")
    check("her levels come after speaking and before idle",
          amps and amps[0][0] > idx_speak and amps[-1][0] < idx_idle)
    her = f[idx_her][2]["message"]
    check("her line carries via typed", her.get("via") == "typed")
    check("she spoke exactly the words the text shows", rig.tts.said == [her["text"]], f"{rig.tts.said} vs {her['text']!r}")
    seqs = [p["seq"] for _i, _t, p in amps]
    check("level seq counts up within the answer", seqs == list(range(1, len(seqs) + 1)), str(seqs[:5]))
    check("the last level is a final 0", amps[-1][2]["level"] == 0.0)
    check("every level frame is exactly {companionId, level, seq}",
          all(set(p) == {"companionId", "level", "seq"} for _i, _t, p in amps))
    times = [t for _i, t, _p in amps]
    gaps = [b - a for a, b in zip(times, times[1:])]
    dur = times[-1] - times[0]
    rate = (len(times) - 1) / dur if dur > 0 else 0.0
    t_speak = f[idx_speak][0]
    lead_seen = times[0] - t_speak
    stream = Dev.streams[-1]
    played_first = stream.played[0][0]
    NUMBERS["fake_rate_hz"] = round(rate, 1)
    NUMBERS["fake_max_gap_ms"] = round(max(gaps) * 1000.0, 1)
    NUMBERS["fake_first_level_after_speaking_ms"] = round(lead_seen * 1000.0, 1)
    NUMBERS["fake_level_vs_dac_ms"] = round((times[0] - (played_first + Dev.lead)) * 1000.0, 2)
    NUMBERS["fake_bytes_per_frame"] = round(statistics.mean(n for _t, fr, n in rig.orb.frames if fr["type"] == "evt.voice.amplitude"), 1)
    NUMBERS["fake_bytes_per_s"] = round(NUMBERS["fake_bytes_per_frame"] * rate, 0)
    check("about one level per device buffer (576 frames at 22050 Hz = 38.3/s)", abs(rate - 22050 / 576) < 6.0,
          f"{rate:.1f}/s")
    check("never more than 250 ms between level frames", max(gaps) < 0.25, f"max {max(gaps) * 1000:.1f} ms")
    check("levels are sent when the buffer reaches the DAC, not when it is handed over",
          abs(NUMBERS["fake_level_vs_dac_ms"]) < 25.0, f"{NUMBERS['fake_level_vs_dac_ms']} ms")
    check("one playback for one answer", len(Dev.streams) - before == 1)
    console_kinds = {fr["type"] for _t, fr, _n in rig.console.frames}
    check("the Console (no voice.*/agent.* subscription) receives no levels and no state",
          not any(k.startswith("evt.voice.") or k == "evt.agent.state" for k in console_kinds), str(console_kinds))
    check("nothing armed the microphone (no session open)", rig.mic.arms == 0 and not rig.d.voice.armed
          and not rig.d.ptt_active and not rig.d.convo.open)
    check("no listening state at any point", "listening" not in [s for _i, s in states])
    check("state ends idle", states[-1][1] == "idle")
    rows = [r for r in rig.d.audit if r.get("tool") == "agent.message"]
    check("the typed turn is audited once", len(rows) == 1 and rows[0]["summary"].startswith("typed turn: time"))
    check("no level frame is audited", not any(r.get("tool", "").startswith("voice.amplitude") for r in rig.d.audit))
    log_line = [x for x in LOG if "TYPED time" in x]
    check("the log names the first audio buffer time", bool(log_line) and "first audio buffer" in log_line[-1],
          log_line[-1][-90:] if log_line else "")


async def t4_errors_spoken() -> None:
    print("\n4. error answers are spoken too")
    rig = await build()
    rig.brain.error = LLMUnavailable("quota", "My thinking brain is over its quota, Emperor. Try me later.")
    await say_typed(rig, "what is a linked list?")
    await quiet(rig)
    her = [p["payload"]["message"]["text"] for _t, p, _n in rig.orb.frames
           if p["type"] == "evt.transcript.message" and p["payload"]["message"]["role"] == "assistant"]
    check("the brain's own error sentence is shown", her == ["My thinking brain is over its quota, Emperor. Try me later."], str(her))
    check("...and spoken", rig.tts.said == her, str(rig.tts.said))
    check("one intent call and one chat call, both to the fake brain", rig.brain.intent_calls == 1 and rig.brain.calls == 1)
    states = rig.orb.states()
    check("thinking, speaking, idle", states == ["thinking", "speaking", "idle"], str(states))
    rig2 = await build()
    rig2.d.voice.router.route = lambda text: (_ for _ in ()).throw(RuntimeError("router exploded"))
    await say_typed(rig2, "what time is it")
    await quiet(rig2)
    her2 = [p["payload"]["message"] for _t, p, _n in rig2.orb.frames
            if p["type"] == "evt.transcript.message" and p["payload"]["message"]["role"] == "assistant"]
    check("a failed typed turn still answers in her register", len(her2) == 1 and her2[0]["text"].startswith("Something went wrong"))
    check("...and that failure line is spoken", rig2.tts.said == [her2[0]["text"]] if her2 else False)
    check("...and ends idle", rig2.orb.states()[-1] == "idle", str(rig2.orb.states()))


async def t5_code_not_read() -> None:
    print("\n5. what the cleaning leaves unspeakable is shown, not read aloud")
    raw = "Here it is, Emperor.\n```python\nprint('hi')\n```\nRun that one."
    check("plain text speaks exactly as it shows", spoken_form("Blue light scatters, Emperor.")
          == humanise("Blue light scatters, Emperor.").text)
    check("a code block is dropped from speech", "print" not in spoken_form(raw) and "Run that one" in spoken_form(raw),
          repr(spoken_form(raw)))
    check("an answer that is only code has nothing to say", spoken_form("```\nx = 1\n```") == "")
    rig = await build()
    rig.brain.reply = raw
    await say_typed(rig, "how do I print in python?")
    await quiet(rig)
    her = [p["payload"]["message"]["text"] for _t, p, _n in rig.orb.frames
           if p["type"] == "evt.transcript.message" and p["payload"]["message"]["role"] == "assistant"]
    check("the code is still shown, as today", her and "print('hi')" in her[0], repr(her))
    check("she says the words around it and not the code", rig.tts.said == ["Here it is, Emperor. Run that one."],
          repr(rig.tts.said))
    rig3 = await build()
    rig3.brain.reply = "```\nx = 1\n```"
    await say_typed(rig3, "give me code only")
    await quiet(rig3)
    check("all-code answer: shown, nothing spoken, no speaking state",
          rig3.tts.said == [] and "speaking" not in rig3.orb.states()
          and any(p["type"] == "evt.transcript.message" and p["payload"]["message"]["role"] == "assistant"
                  for _t, p, _n in rig3.orb.frames))


async def t6_cannot_speak() -> None:
    print("\n6. when she cannot speak the answer arrives as text, exactly as today")
    rig = await build(voice=False)
    LOG.clear()
    await say_typed(rig, "what time is it")
    await quiet(rig)
    await say_typed(rig, "what day is it")
    await quiet(rig)
    kinds = [(fr["type"], fr["payload"].get("state") or fr["payload"].get("message", {}).get("role", ""))
             for _t, fr, _n in rig.orb.frames]
    first = kinds[: kinds.index(("evt.agent.state", "idle")) + 1]
    check("voice off: his line, ack, thinking, her line, idle (today's order)",
          first == [("evt.transcript.message", "user"), ("res.agent.accepted", ""), ("evt.agent.state", "thinking"),
                    ("evt.transcript.message", "assistant"), ("evt.agent.state", "idle")], str(first))
    check("voice off: no speaking and no level frame", not any(k == ("evt.agent.state", "speaking") or k[0] == "evt.voice.amplitude"
                                                              for k in kinds))
    check("voice off: the reason is logged once over two answers", sum("voice is off" in x for x in LOG) == 1)
    rig = await build()
    rig.tts.fail = True
    LOG.clear()
    await say_typed(rig, "what time is it")
    await quiet(rig)
    await say_typed(rig, "what day is it")
    await quiet(rig)
    check("TTS failing: no speaking state", "speaking" not in rig.orb.states(), str(rig.orb.states()))
    check("TTS failing: both answers shown", sum(1 for _t, p, _n in rig.orb.frames if p["type"] == "evt.transcript.message"
                                                and p["payload"]["message"]["role"] == "assistant") == 2)
    check("TTS failing: logged once", sum("speech synthesis failed" in x for x in LOG) == 1)
    rig = await build()
    Dev.fail_open = True
    LOG.clear()
    try:
        await say_typed(rig, "what time is it")
        await quiet(rig)
        await say_typed(rig, "what day is it")
        await quiet(rig)
    finally:
        Dev.fail_open = False
    check("no audio device: no speaking state, answers shown, ends idle",
          "speaking" not in rig.orb.states() and rig.orb.states()[-1] == "idle", str(rig.orb.states()))
    check("no audio device: logged once", sum("no audio output" in x for x in LOG) == 1)
    Dev.fail_start = True
    try:
        await say_typed(rig, "what time is it")
        await quiet(rig)
    finally:
        Dev.fail_start = False
    check("a device that fails to start leaves the bus usable", not rig.d.voice.bus.is_speaking)
    n = len(rig.tts.said)
    await say_typed(rig, "what time is it")
    await quiet(rig)
    check("...and the next answer is spoken", len(rig.tts.said) == n + 1 and rig.orb.states()[-2:] == ["speaking", "idle"],
          str(rig.orb.states()[-3:]))
    rig = await build()
    Dev.hang = True
    LOG.clear()
    try:
        await say_typed(rig, "what time is it")
        ok = await until(lambda: "idle" in rig.orb.states()[rig.orb.states().index("speaking"):]
                         if "speaking" in rig.orb.states() else False, 8.0)
    finally:
        Dev.hang = False
    await quiet(rig)
    t_sp = [t for t, fr, _n in rig.orb.frames if fr["type"] == "evt.agent.state" and fr["payload"]["state"] == "speaking"]
    t_id = [t for t, fr, _n in rig.orb.frames if fr["type"] == "evt.agent.state" and fr["payload"]["state"] == "idle"]
    held = (t_id[-1] - t_sp[0]) if t_sp and t_id else -1
    audio = rig.tts.said and len(rig.tts.said[0]) * PER_CHAR_S
    check("a device that stops playing cannot leave her speaking: the watchdog ends it",
          ok and 0 < held <= audio + 3.3, f"speaking held {held:.2f} s for {audio:.2f} s of audio")
    check("...and says so in the log", any("did not finish" in x for x in LOG))


async def t7_sessions() -> None:
    print("\n7. the voice loop afterwards is exactly as it was before")
    rig = await build()
    d = rig.d
    d.open_session("chord")
    await d.rearm_for_session()
    check("session open and its segment armed before he types",
          d.convo.open and d.voice.armed and d.ptt_active and d._segment_by == "session" and rig.mic.arms == 1)
    during: list[bool] = []
    Dev.probe = lambda t: during.append(bool(rig.mic.armed or d.voice.armed))
    LOG.clear()
    try:
        await say_typed(rig, "what time is it")
        await quiet(rig)
    finally:
        Dev.probe = None
    check("she spoke", rig.tts.said and "speaking" in rig.orb.states())
    check("the microphone was never armed while she played (her own voice is never recorded)",
          during and not any(during), f"{sum(during)} of {len(during)} buffers")
    check("the empty segment was released, not turned into a turn",
          rig.mic.disarms == 1 and any("released an empty segment" in x for x in LOG)
          and not any(r.get("tool", "").startswith("voice.turn") for r in d.audit))
    await until(lambda: rig.mic.arms >= 2 and rig.orb.states()[-1:] == ["listening"], 5.0)
    check("afterwards the conversation is still open and listening again",
          d.convo.open and d.voice.armed and d.ptt_active and rig.mic.arms == 2)
    st = rig.orb.states()
    check("states: thinking, speaking, idle, then listening", st[-4:] == ["thinking", "speaking", "idle", "listening"],
          str(st))
    rig = await build()
    d = rig.d
    d.open_session("chord")
    await d.rearm_for_session()
    rig.mic.heard_speech = True
    rig.mic.speech = True
    rig.stt.next = "what day is it"
    during = []
    Dev.probe = lambda t: during.append(bool(rig.mic.armed or d.voice.armed))
    before = len(Dev.streams)
    Dev.max_live = 0
    ROUTER.datetime = FixedDay
    try:
        await say_typed(rig, "what time is it")
        await asyncio.sleep(0.6)
        check("while his segment holds speech she does not speak over it",
              len(Dev.streams) == before and d.voice.armed)
        rig.mic.on_close("silence")
        await quiet(rig, 20.0)
    finally:
        Dev.probe = None
        ROUTER.datetime = REAL_DATETIME
    s1, s2 = (Dev.streams[before], Dev.streams[before + 1]) if len(Dev.streams) >= before + 2 else (None, None)
    order = [rig.tts.text_of(s1), rig.tts.text_of(s2)] if s1 is not None else []
    check("his spoken turn is answered first, then the typed one (device order)",
          len(order) == 2 and FIXED_DAY.strftime("%B") in order[0] and ":" in order[1]
          and s1.ended_at is not None and s2.started_at is not None and s1.ended_at <= s2.started_at, str(order))
    check("never two answers playing at once", Dev.max_live == 1, str(Dev.max_live))
    check("his words were kept (his line is on the thread)",
          any(fr["type"] == "evt.transcript.message" and fr["payload"]["message"]["role"] == "user"
              and fr["payload"]["message"]["text"] == "what day is it" for _t, fr, _n in rig.orb.frames))
    check("the microphone was never armed while she played", during and not any(during))
    check("afterwards the conversation is open and listening", d.convo.open and d.voice.armed)
    vline = [fr["payload"]["message"] for _t, fr, _n in rig.orb.frames if fr["type"] == "evt.transcript.message"
             and fr["payload"]["message"]["text"] == "what day is it"]
    check("4c: his spoken line carries via voice", vline and vline[0].get("via") == "voice", str(vline))
    hline = [fr["payload"]["message"] for _t, fr, _n in rig.orb.frames if fr["type"] == "evt.transcript.message"
             and fr["payload"]["message"]["role"] == "assistant" and FIXED_DAY.strftime("%B") in fr["payload"]["message"]["text"]]
    check("4c: her spoken-turn line keeps no via, as today", hline and "via" not in hline[0], str(hline))
    rig = await build()
    d = rig.d
    await send(rig, "cmd.voice.pushToTalk", {"action": "start"})
    check("a chord segment is armed", d.voice.armed and d._segment_by == "chord")
    before = len(Dev.streams)
    await say_typed(rig, "what time is it")
    await asyncio.sleep(0.6)
    check("a segment he opened himself is never released by a typed answer", d.voice.armed and len(Dev.streams) == before)
    rig.mic.on_close("hard-cap")
    await quiet(rig, 20.0)
    check("once his segment ends she answers", len(Dev.streams) == before + 1 and rig.tts.said)
    check("...and then she stops listening: one question per press", not d.convo.open and not d.voice.armed)


async def t8_interrupt() -> None:
    print("\n8. a new typed message while she speaks stops her, and the new answer follows")
    rig = await build()
    stops: list[float] = []
    own: list[float] = []
    followed = 0
    Dev.max_live = 0
    for i in range(20):
        before = len(Dev.streams)
        await say_typed(rig, "what day is it")
        first = await speaking_for(0.5, before)
        arrival = pc()
        n_said = len(rig.tts.said)
        await say_typed(rig, "what time is it")
        stops.append((first.aborted_at - arrival) * 1000.0 if first and first.aborted_at else float("inf"))
        await quiet(rig)
        line = [x for x in LOG if "stopped her for a new typed message" in x]
        if line:
            own.append(float(line[-1].split(" in ")[1].split(" ms")[0]))
        if len(rig.tts.said) == n_said + 1 and ":" in rig.tts.said[-1]:
            followed += 1
    NUMBERS["interrupt_p50_ms"] = round(pct(stops, 0.5), 3)
    NUMBERS["interrupt_p95_ms"] = round(pct(stops, 0.95), 3)
    NUMBERS["interrupt_daemon_p50_ms"] = round(pct(own, 0.5), 3)
    check("20 interruptions: her sound stops within 120 ms (p95)", pct(stops, 0.95) < 120.0,
          f"p50 {pct(stops, 0.5):.2f} ms, p95 {pct(stops, 0.95):.2f} ms (message arrival -> fake device abort)")
    check("...the new answer follows every time", followed == 20, f"{followed}/20")
    check("...and two answers never play at once", Dev.max_live == 1, str(Dev.max_live))
    st = rig.orb.states()
    check("no idle between the cut and the new answer", "speaking,thinking,speaking" in ",".join(st), ",".join(st[-8:]))


async def t9_cancel() -> None:
    print("\n9. cmd.agent.cancel")
    rig = await build()
    stops: list[float] = []
    replies = 0
    idles = 0
    for i in range(20):
        before = len(Dev.streams)
        mid = await say_typed(rig, "what day is it")
        first = await speaking_for(0.5, before)
        mark = len(rig.orb.frames)
        arrival = pc()
        reply = await send(rig, "cmd.agent.cancel", {"companionId": "tessa", "messageId": mid})
        stops.append((first.aborted_at - arrival) * 1000.0 if first and first.aborted_at else float("inf"))
        if reply and reply["type"] == "res.ok" and reply["payload"].get("cancelled") is True:
            replies += 1
        await quiet(rig)
        if rig.orb.states(mark) == ["idle"]:
            idles += 1
    NUMBERS["cancel_p50_ms"] = round(pct(stops, 0.5), 3)
    NUMBERS["cancel_p95_ms"] = round(pct(stops, 0.95), 3)
    check("20 cancels: her sound stops within 120 ms (p95)", pct(stops, 0.95) < 120.0,
          f"p50 {pct(stops, 0.5):.2f} ms, p95 {pct(stops, 0.95):.2f} ms")
    check("...each answered res.ok {cancelled: true}", replies == 20, f"{replies}/20")
    check("...and her state returns to idle once, nothing after", idles == 20, f"{idles}/20")
    rig = await build()
    rig.brain.delay = 1.0
    turns_before = len(rig.d.conversation.messages())
    mark = len(rig.orb.frames)
    mid = await say_typed(rig, "what is a linked list?")
    await until(lambda: "thinking" in rig.orb.states(mark), 3.0)
    reply = await send(rig, "cmd.agent.cancel", {"companionId": "tessa", "messageId": mid})
    t_cancel = pc()
    await until(lambda: rig.orb.states(mark)[-1:] == ["idle"], 2.0)
    idle_after = pc() - t_cancel
    await asyncio.sleep(1.3)
    await quiet(rig)
    her = [fr for _t, fr, _n in rig.orb.frames[mark:] if fr["type"] == "evt.transcript.message"
           and fr["payload"]["message"]["role"] == "assistant"]
    check("cancel while thinking answers res.ok {cancelled: true}", reply and reply["payload"].get("cancelled") is True)
    check("...her state leaves thinking at once", idle_after < 0.2, f"{idle_after * 1000:.0f} ms")
    check("...the answer not yet started is neither spoken nor shown", not her and rig.tts.said == [])
    check("...and is not remembered as said", len(rig.d.conversation.messages()) == turns_before)
    check("...states: thinking then idle, nothing after", rig.orb.states(mark) == ["thinking", "idle"],
          str(rig.orb.states(mark)))
    rig = await build()
    before = len(Dev.streams)
    await say_typed(rig, "what day is it")
    await speaking_for(0.3, before)
    reply = await send(rig, "cmd.agent.cancel", {"companionId": "tessa", "messageId": SRV.ulid()})
    still = rig.d.voice.bus.is_speaking
    await quiet(rig)
    check("a messageId not in flight stops nothing and still answers res.ok",
          reply and reply["type"] == "res.ok" and reply["payload"].get("cancelled") is False and still)
    reply = await send(rig, "cmd.agent.cancel", {"companionId": "tessa"})
    check("a cancel with nothing in flight answers res.ok {cancelled: false}",
          reply and reply["type"] == "res.ok" and reply["payload"].get("cancelled") is False)
    rig = await build()
    spec = REGISTRY["sys.battery"]
    ran: list[float] = []

    def slow_battery() -> dict[str, Any]:
        time.sleep(0.8)
        ran.append(pc())
        return {"pct": 77.0, "where": "on the charger", "left": ""}

    slow_battery.__signature__ = inspect.signature(spec.handler)
    REGISTRY["sys.battery"] = dataclasses.replace(spec, handler=slow_battery)
    try:
        mark = len(rig.orb.frames)
        mid = await say_typed(rig, "what is my battery")
        await until(lambda: "working" in rig.orb.states(mark), 3.0)
        reply = await send(rig, "cmd.agent.cancel", {"companionId": "tessa", "messageId": mid})
        st_at_cancel = list(rig.orb.states(mark))
        await quiet(rig)
    finally:
        REGISTRY["sys.battery"] = spec
    her = [fr for _t, fr, _n in rig.orb.frames[mark:] if fr["type"] == "evt.transcript.message"
           and fr["payload"]["message"]["role"] == "assistant"]
    rows = [r for r in rig.d.audit if r.get("tool") == "sys.battery"]
    check("cancel never stops a tool already running: it finished", len(ran) == 1)
    check("...its run is on the chain", any(not str(r.get("summary", "")).upper().startswith("REFUSED") for r in rows),
          str([r.get("summary") for r in rows]))
    check("...her answer to it is neither spoken nor shown", not her and rig.tts.said == [])
    check("...working stays until the tool is done, then idle",
          st_at_cancel[-1] == "working" and rig.orb.states(mark)[-1] == "idle", f"{st_at_cancel} -> {rig.orb.states(mark)}")
    check("...the typed turn is audited as cancelled", any(r.get("tool") == "agent.message" and "cancelled" in r["summary"]
                                                           for r in rig.d.audit))
    rig = await build()
    rig.mic.speech = True
    rig.stt.next = "what day is it"
    await send(rig, "cmd.voice.pushToTalk", {"action": "start"})
    before = len(Dev.streams)
    await send(rig, "cmd.voice.pushToTalk", {"action": "stop"})
    s = await speaking_for(0.4, before)
    rig.mic.speech = False
    reply = await send(rig, "cmd.agent.cancel", {"companionId": "tessa"})
    await quiet(rig)
    check("a cancel with no messageId stops a spoken (voice) answer too",
          s is not None and s.aborted_at is not None and reply["payload"].get("cancelled") is True)
    check("...and afterwards she is not listening: one question per press", not rig.d.convo.open
          and not rig.d.voice.armed and rig.orb.states()[-1] != "listening", str(rig.orb.states()[-3:]))
    rig = await build()
    before = len(Dev.streams)
    await say_typed(rig, "what day is it")
    s = await speaking_for(0.5, before)
    mark = len(rig.orb.frames)
    await send(rig, "cmd.voice.pushToTalk", {"action": "start"})
    await asyncio.sleep(0.3)
    check("his push-to-talk barges in on a typed answer", s is not None and s.aborted_at is not None
          and rig.d.voice.armed and rig.orb.states(mark) == ["listening"], str(rig.orb.states(mark)))
    await send(rig, "cmd.voice.pushToTalk", {"action": "stop"})
    await quiet(rig)


async def newcomer(rig: Any, topics: list[str]) -> FakeWs:
    ws = FakeWs()
    rig.d.clients[ws] = {"authed": True, "surface": "orb", "subs": set(), "sessionId": SRV.ulid()}
    await send(rig, "cmd.subscribe", {"topics": topics}, ws)
    return ws


def first_state(ws: FakeWs) -> str | None:
    s = [fr["payload"]["state"] for _t, fr, _n in ws.frames if fr["type"] == "evt.agent.state"]
    return s[0] if s else None


async def t10_snapshot() -> None:
    print("\n10. her state on every subscribe that includes agent.*")
    rig = await build()
    ws = await newcomer(rig, ["agent.*", "voice.*"])
    check("at rest: idle", first_state(ws) == "idle")
    check("the state comes after res.subscribe", ws.frames[0][1]["type"] == "res.subscribe")
    before = len(Dev.streams)
    await say_typed(rig, "what day is it")
    await speaking_for(0.3, before)
    ws2 = await newcomer(rig, ["agent.*"])
    check("reconnecting while she speaks: speaking", first_state(ws2) == "speaking")
    await quiet(rig)
    check("...and the ended state arrives after it", [fr["payload"]["state"] for _t, fr, _n in ws2.frames
                                                       if fr["type"] == "evt.agent.state"][-1] == "idle")
    ws3 = await newcomer(rig, ["agent.*"])
    check("after she has finished: idle", first_state(ws3) == "idle")
    rig.brain.delay = 0.8
    await say_typed(rig, "what is a linked list?")
    await until(lambda: rig.d.agent_states.get("tessa", {}).get("state") == "thinking", 3.0)
    ws4 = await newcomer(rig, ["agent.*"])
    check("reconnecting mid-thought: thinking", first_state(ws4) == "thinking")
    await quiet(rig)
    ws5 = await newcomer(rig, ["pty.*", "daemon.*"])
    check("no agent.* in the subscription: no state sent", first_state(ws5) is None)


async def t11_cards() -> None:
    print("\n11. blocked while a card waits, her real state after; the card snapshot; her line after a card")
    rig = await build()
    spec = REGISTRY["x.post"]
    posted: list[str] = []

    def rec(text: str, _approved_by_surface: bool = False) -> dict[str, Any]:
        posted.append(text)
        return {"chars": len(text)}

    rec.__signature__ = inspect.signature(spec.handler)
    REGISTRY["x.post"] = dataclasses.replace(spec, handler=rec)
    try:
        mark = len(rig.orb.frames)
        await say_typed(rig, "tweet hello world")
        await quiet(rig)
        st = rig.orb.states(mark)
        runs = [s for i, s in enumerate(st) if i == 0 or s != st[i - 1]]
        check("a card: thinking, working, blocked, speaking her line, blocked again",
              runs == ["thinking", "working", "blocked", "speaking", "blocked"], str(st))
        live = [fr["payload"] for _t, fr, _n in rig.orb.frames[mark:] if fr["type"] == "evt.permission.request"][0]
        ws = await newcomer(rig, ["permission.*", "agent.*"])
        snap = [fr["payload"] for _t, fr, _n in ws.frames if fr["type"] == "evt.permission.request"]
        check("4b: the re-sent card carries every field the live card carried",
              snap and set(snap[0]) == set(live), f"live {sorted(live)} snap {sorted(snap[0]) if snap else None}")
        check("4b: frozen included, same values",
              snap and all(snap[0][k] == live[k] for k in ("requestId", "tier", "tool", "args", "provenance", "frozen")))
        check("3a: a subscriber arriving now sees blocked", first_state(ws) == "blocked")
        mark = len(rig.orb.frames)
        reply = await send(rig, "cmd.permission.respond", {"requestId": live["requestId"], "decision": "approve"})
        await quiet(rig)
        check("approved: working while it runs, then idle", rig.orb.states(mark) == ["working", "idle"],
              str(rig.orb.states(mark)))
        check("...the approved action ran once", posted == ["hello world"] and reply["type"] == "res.ok")
        mark = len(rig.orb.frames)
        await say_typed(rig, "tweet second one")
        await quiet(rig)
        rid = [fr["payload"]["requestId"] for _t, fr, _n in rig.orb.frames[mark:] if fr["type"] == "evt.permission.request"][0]
        mark = len(rig.orb.frames)
        await send(rig, "cmd.permission.respond", {"requestId": rid, "decision": "deny"})
        await quiet(rig)
        check("denied: idle", rig.orb.states(mark) == ["idle"], str(rig.orb.states(mark)))
        await say_typed(rig, "tweet third one")
        await quiet(rig)
        gate = rig.d.voice.executor.approvals
        req = list(gate.pending.values())[0]
        req.at -= APPROVAL_WINDOW_S + 5
        mark = len(rig.orb.frames)
        real_asyncio = SRV.asyncio
        ticks = {"n": 0}

        async def one_tick(_s: float) -> None:
            ticks["n"] += 1
            if ticks["n"] > 1:
                raise asyncio.CancelledError

        SRV.asyncio = types.SimpleNamespace(**{k: getattr(real_asyncio, k) for k in dir(real_asyncio) if not k.startswith("__")})
        SRV.asyncio.sleep = one_tick
        try:
            await rig.d.sweep_approvals()
        except asyncio.CancelledError:
            pass
        finally:
            SRV.asyncio = real_asyncio
        kinds = [(fr["type"], fr["payload"].get("decision") or fr["payload"].get("state")) for _t, fr, _n in rig.orb.frames[mark:]]
        check("expired: resolved expired, then idle", kinds == [("evt.permission.resolved", "expired"), ("evt.agent.state", "idle")],
              str(kinds))
        ex = rig.d.voice.executor
        ex.plan_card_answered = lambda request_id, outcome, spoken="": (
            "You denied the card for the tweet, Emperor. Nothing was posted.")
        await say_typed(rig, "tweet fourth one")
        await quiet(rig)
        rid = list(gate.pending.values())[0].request_id
        mark = len(rig.orb.frames)
        n_said = len(rig.tts.said)
        await send(rig, "cmd.permission.respond", {"requestId": rid, "decision": "deny"})
        await quiet(rig)
        kinds = [fr["type"] + ":" + str(fr["payload"].get("state") or fr["payload"].get("decision") or
                                        fr["payload"].get("message", {}).get("role", ""))
                 for _t, fr, _n in rig.orb.frames[mark:] if fr["type"] != "evt.voice.amplitude"]
        check("her line after he answers a card is spoken: resolved, speaking, her line, idle",
              kinds == ["evt.permission.resolved:deny", "res.ok:", "evt.agent.state:speaking",
                        "evt.transcript.message:assistant", "evt.agent.state:idle"], str(kinds))
        check("...the words spoken are the line shown",
              rig.tts.said[n_said:] == ["You denied the card for the tweet, Emperor. Nothing was posted."])
        rig.mic.speech = True
        rig.stt.next = "tweet from my voice"
        await send(rig, "cmd.voice.pushToTalk", {"action": "start"})
        await send(rig, "cmd.voice.pushToTalk", {"action": "stop"})
        await quiet(rig)
        rig.mic.speech = False
        rig.d.close_session("test")
        if rig.d.voice.armed:
            rig.d.voice.release()
            rig.d.ptt_active = False
        await quiet(rig)
        pending = list(gate.pending.values())
        check("a card raised by a spoken turn", len(pending) == 1 and pending[0].args.get("text") == "from my voice",
              str([p.args for p in pending]))
        n_said = len(rig.tts.said)
        mark = len(rig.orb.frames)
        await send(rig, "cmd.permission.respond", {"requestId": pending[0].request_id, "decision": "deny"})
        await quiet(rig)
        check("...her line after it is spoken too, however the turn began",
              rig.tts.said[n_said:] == ["You denied the card for the tweet, Emperor. Nothing was posted."]
              and "speaking" in rig.orb.states(mark))
    finally:
        REGISTRY["x.post"] = spec


async def t11b_dropped_ack() -> None:
    print("\n11b. the link drops right after he sends: the turn still runs, nothing leaks")
    import websockets

    class DropWs(FakeWs):
        async def send(self, raw: str) -> None:
            raise websockets.exceptions.ConnectionClosed(None, None)

    rig = await build()
    gone = DropWs()
    state = {"authed": True, "surface": "orb", "subs": set(ORB_TOPICS), "sessionId": SRV.ulid()}
    rig.d.clients[gone] = state
    before = len(Dev.streams)
    LOG.clear()
    mark = len(rig.orb.frames)
    await rig.d._dispatch(gone, state, SRV.envelope("cmd.agent.message", {"companionId": "tessa", "text": "what day is it"}))
    s = await speaking_for(0.3, before)
    ws2 = await newcomer(rig, ["agent.*"])
    check("the message is answered although its ack could not be delivered", s is not None and bool(rig.tts.said))
    check("...and that is logged", any("link dropped before the ack" in x for x in LOG))
    check("a surface reconnecting while she speaks sees speaking", first_state(ws2) == "speaking")
    await quiet(rig)
    ws3 = await newcomer(rig, ["agent.*"])
    check("...and reconnecting after she finished sees idle", first_state(ws3) == "idle")
    check("the other surface saw the whole turn", rig.orb.states(mark) == ["thinking", "speaking", "idle"],
          str(rig.orb.states(mark)))
    check("no turn is left registered", rig.d._typed_jobs == {} and not rig.d._typed_tasks)


async def t12_cost() -> None:
    print("\n12. what the level stream costs (fake device, this process)")
    syn = FakeTTS().synthesise("z" * 80)

    async def run(with_levels: bool) -> float:
        rig = await build()
        bus = rig.d.voice.bus
        if not with_levels:
            bus._on_level = None
        else:
            ws2 = FakeWs()
            rig.d.clients[ws2] = {"authed": True, "surface": "orb", "subs": {"voice.*"}, "sessionId": SRV.ulid()}
        c0, t0 = time.process_time(), pc()
        play = await asyncio.to_thread(bus.speak, syn.samples, syn.sample_rate)
        await until(lambda: play.done.is_set(), 10.0, 0.01)
        await asyncio.sleep(0.05)
        return (time.process_time() - c0) / (pc() - t0)

    off = [await run(False) for _ in range(2)]
    on = [await run(True) for _ in range(2)]
    NUMBERS["cpu_core_pct_without_levels"] = round(statistics.mean(off) * 100.0, 2)
    NUMBERS["cpu_core_pct_with_levels_2_clients"] = round(statistics.mean(on) * 100.0, 2)
    check("cost measured (process CPU per wall second, 4 s of speech each, 2 runs)", True,
          f"without {NUMBERS['cpu_core_pct_without_levels']}% of one core, with {NUMBERS['cpu_core_pct_with_levels_2_clients']}%")


def t13_contract_shapes() -> None:
    print("\n13. what the round promised about itself")
    src = Path(SRV.__file__).read_text(encoding="utf-8")
    check("cmd.agent.cancel has a handler", '"cmd.agent.cancel": self._h_agent_cancel' in src)
    check("the level event is broadcast under voice.* only",
          'broadcast("evt.voice.amplitude"' in src and SRV.topic_matches({"voice.*"}, "evt.voice.amplitude")
          and not SRV.topic_matches({"agent.*", "pty.*", "permission.*", "daemon.*", "sentinel.*", "transcript.*"},
                                    "evt.voice.amplitude"))
    check("levels never go through the audit log", "evt.voice.amplitude" not in inspect.getsource(SRV.AuditFeed))


async def main() -> None:
    t1_levels()
    await t2_bus()
    await t3_typed_spoken()
    await t4_errors_spoken()
    await t5_code_not_read()
    await t6_cannot_speak()
    await t7_sessions()
    await t8_interrupt()
    await t9_cancel()
    await t10_snapshot()
    await t11_cards()
    await t11b_dropped_ack()
    await t12_cost()
    t13_contract_shapes()


asyncio.run(main())
print("\n14. the fences")
check("never two playbacks live at once, across every scenario", Dev.live == 0)
check("no network connection was attempted", NET["attempts"] == 0, str(NET["attempts"]))
check("the live audit log, thread and action log are untouched",
      {str(p): digest(p) for p in LIVE} == LIVE_BEFORE)
(TMP / "talk-log.txt").write_text("\n".join(LOG), encoding="utf-8")
print("\nNUMBERS " + json.dumps(NUMBERS))
print(f"\n{passed} passed, {failed} failed\n")
sys.exit(0 if failed == 0 else 1)
