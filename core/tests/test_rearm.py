from __future__ import annotations

import asyncio
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TMP = Path(tempfile.mkdtemp(prefix="tessa-rearm-"))

from core.brain import memory

memory.ACTION_LOG = TMP / "actions.jsonl"

import core.server as SRV
from core.brain.claims import ClaimStore
from core.brain.conversation import Conversation
from core.brain.style import StyleStore
from core.capabilities.vault import Vault
from core.security.audit import AuditLog
from core.telemetry.cost import CostLedger
from core.voice.audio_io import Capture

LOG: list[str] = []
_n = {"a": 0, "c": 0}


def _audit(path: object) -> AuditLog:
    _n["a"] += 1
    return AuditLog(TMP / f"audit-{_n['a']}.log")


def _conversation() -> Conversation:
    _n["c"] += 1
    return Conversation(path=TMP / f"conversation-{_n['c']}.json")


SRV.AuditLog = _audit
SRV._Vault = lambda **kw: Vault(path=TMP / "vault.json", acl=False, profile_root=TMP / "profiles", **kw)
SRV.Conversation = _conversation
SRV.ClaimStore = lambda: ClaimStore(path=TMP / "claims.json")
SRV.StyleStore = lambda **kw: StyleStore(path=TMP / "style.json", **kw)
SRV.CostLedger = lambda path: CostLedger(TMP / "cost.jsonl")
SRV.log = lambda msg: LOG.append(str(msg))

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"ok    {name}")
    else:
        failed += 1
        print(f"FAIL  {name}" + (f"  ({detail})" if detail else ""))


class Mic:
    def __init__(self) -> None:
        self.armed = False
        self.arms = 0
        self.heard_speech = False
        self.speech = False
        self.gate = threading.Event()
        self.gate.set()
        self.in_flush = threading.Event()
        self.flushes = 0

    def flush_ring(self) -> int:
        self.flushes += 1
        self.in_flush.set()
        self.gate.wait(5.0)
        return 0

    def arm(self) -> np.ndarray:
        self.armed = True
        self.arms += 1
        return np.zeros(0, dtype=np.int16)

    def disarm(self) -> Capture:
        self.armed = False
        return Capture(samples=np.zeros(16000, dtype=np.int16), sample_rate=16000, duration_s=1.0, peak=0, rms=0.0,
                       first_sample_latency_ms=0.0)

    def watch_for_silence(self, *, on_close: object, **kw: object) -> None:
        self.on_close = on_close

    def cancel_watch(self) -> None:
        return None


class STT:
    def transcribe(self, audio: object, sr: int) -> object:
        return type("R", (), {"text": "", "rms": 0.0, "too_quiet": True})()


class TTS:
    rate = 22050

    def synthesise(self, text: str) -> object:
        raise RuntimeError("no speech in this test")


def build() -> tuple[SRV.TessaDaemon, Mic]:
    d = SRV.TessaDaemon(dev=False)
    mic = Mic()
    d.attach_voice(mic=mic, stt=STT(), tts=TTS())
    d.open_session("chord")
    LOG.clear()
    return d, mic


async def wait_flag(flag: threading.Event, limit: float = 5.0) -> bool:
    t0 = time.perf_counter()
    while not flag.is_set():
        if time.perf_counter() - t0 > limit:
            return False
        await asyncio.sleep(0.005)
    return True


def listening_lines() -> int:
    return sum(1 for x in LOG if "listening again" in x)


async def two_callers_one_arm() -> None:
    print("\n1. two callers inside the flush window arm the microphone once")
    d, mic = build()
    mic.gate.clear()
    first = asyncio.create_task(d.rearm_for_session())
    in_window = await wait_flag(mic.in_flush)
    second = asyncio.create_task(d.rearm_for_session())
    await asyncio.sleep(0.05)
    check("the first caller is inside the flush when the second arrives", in_window and not first.done())
    check("the second caller returns at once, without flushing or arming", second.done() and mic.flushes == 1,
          f"flushes {mic.flushes}")
    mic.gate.set()
    await asyncio.gather(first, second)
    check("one arm, one listening line", mic.arms == 1 and listening_lines() == 1,
          f"arms {mic.arms}, lines {listening_lines()}")
    check("the segment is claimed by the session and the claim flag is released",
          d.ptt_active and d._segment_by == "session" and not d._rearming)
    third = await d.rearm_for_session()
    check("a later call while the segment is open does nothing", third is None and mic.arms == 1)


async def many_callers_one_arm() -> None:
    print("\n2. eight callers at once")
    d, mic = build()
    mic.gate.clear()
    tasks = [asyncio.create_task(d.rearm_for_session()) for _ in range(8)]
    await wait_flag(mic.in_flush)
    await asyncio.sleep(0.05)
    mic.gate.set()
    await asyncio.gather(*tasks)
    check("eight callers, one flush, one arm", mic.flushes == 1 and mic.arms == 1,
          f"flushes {mic.flushes}, arms {mic.arms}")


async def press_during_flush() -> None:
    print("\n3. a press that claims the segment during the flush wins")
    d, mic = build()
    mic.gate.clear()
    task = asyncio.create_task(d.rearm_for_session())
    await wait_flag(mic.in_flush)
    d.ptt_active = True
    mic.gate.set()
    await task
    check("the re-arm stands down: no arm from it", mic.arms == 0 and listening_lines() == 0,
          f"arms {mic.arms}")
    check("the press keeps its claim, and the re-arm flag is released", d.ptt_active and not d._rearming)


async def close_during_flush() -> None:
    print("\n4. a conversation closed during the flush is not re-opened")
    d, mic = build()
    mic.gate.clear()
    task = asyncio.create_task(d.rearm_for_session())
    await wait_flag(mic.in_flush)
    d.close_session("test closes it")
    mic.gate.set()
    await task
    check("no arm after the close", mic.arms == 0 and not d.ptt_active and not d._rearming)


async def typed_job_during_flush() -> None:
    print("\n5. a typed request that arrives during the flush keeps the floor")
    d, mic = build()
    mic.gate.clear()
    task = asyncio.create_task(d.rearm_for_session())
    await wait_flag(mic.in_flush)
    d._typed_jobs["placeholder"] = object()
    mic.gate.set()
    await task
    d._typed_jobs.clear()
    check("no arm while the typed request is queued", mic.arms == 0 and not d.ptt_active and not d._rearming)
    await d.rearm_for_session()
    check("once it is gone, the next re-arm arms", mic.arms == 1 and d.ptt_active)


async def start_fails() -> None:
    print("\n6. a start that fails leaves nothing claimed")
    d, mic = build()
    real = d.voice.start

    def broken(**kw: object) -> None:
        raise RuntimeError("the input device went away")

    d.voice.start = broken
    raised = False
    try:
        await d.rearm_for_session()
    except RuntimeError:
        raised = True
    check("the failure reaches the caller", raised)
    check("the segment is not left claimed and the re-arm flag is released", not d.ptt_active and not d._rearming)
    d.voice.start = real
    await d.rearm_for_session()
    check("the next re-arm works", mic.arms == 1 and d.ptt_active)


async def main() -> None:
    await two_callers_one_arm()
    await many_callers_one_arm()
    await press_during_flush()
    await close_during_flush()
    await typed_job_during_flush()
    await start_fails()


asyncio.run(main())
print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
