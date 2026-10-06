from __future__ import annotations

import asyncio
import inspect
import json
import shutil
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import core.server as SRV
from core.security.audit import AuditLog

passed = failed = 0
KEYS = ["entryId", "actor", "tool", "tier", "summary", "ts"]
KEYS_NO_TIER = ["entryId", "actor", "tool", "summary", "ts"]


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok    {name}" + (f"  {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


def fresh(name: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=f"tessa-feed-{name}-")) / "audit.log"


def disk(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any], float]] = []

    async def __call__(self, msg_type: str, payload: dict[str, Any]) -> None:
        self.events.append((msg_type, payload, time.perf_counter()))


async def settle(rec: Recorder, n: int, timeout: float = 5.0) -> None:
    t0 = time.perf_counter()
    while len(rec.events) < n and time.perf_counter() - t0 < timeout:
        await asyncio.sleep(0.005)


def start(log: Any, rec: Any, poll_s: float = 30.0) -> tuple[Any, asyncio.Task[None]]:
    feed = SRV.AuditFeed(log, rec, poll_s=poll_s)
    feed.mark()
    return feed, asyncio.create_task(feed.run())


print("\nevt.audit.appended — the live feed\n")


async def payload_and_order() -> None:
    p = fresh("basic")
    log = AuditLog(p)
    log.append(actor="system", tool="before.start", summary="written before the feed started")
    rec = Recorder()
    feed, task = start(log, rec)
    await asyncio.sleep(0.05)
    returned = []
    returned.append((log.append(actor="human", tool="sys.battery", tier="green", summary="ran battery"), time.perf_counter()))
    returned.append((log.append(actor="human", tool="x.like", tier="amber", summary="HELD LIKE x post 1"), time.perf_counter()))
    returned.append((log.append(actor="human", tool="x.reply", tier="red", summary="PENDING-APPROVAL requestId=ab12 REPLY to x post",
                                detail={"args": {"text": "hi"}}, provenance="human"), time.perf_counter()))
    returned.append((log.append(actor="system", tool="daemon.stop", tier="none", summary="Daemon stopped cleanly"), time.perf_counter()))
    returned.append((log.append(actor="human", tool="web.fetch", tier="green",
                                summary="ran fetch with api_key=sk-ant-abcdefghijklmnopqrstuvwx0123"), time.perf_counter()))
    await settle(rec, 5)
    await asyncio.sleep(0.1)
    task.cancel()
    rows = disk(p)
    check("a row written before the feed started is history, not replayed",
          all(e[1]["entryId"] != rows[0]["seq"] for e in rec.events), f"pre-start seq {rows[0]['seq']}")
    check("one event per row written after the start", len(rec.events) == 5, f"{len(rec.events)} events")
    check("every event is evt.audit.appended", {e[0] for e in rec.events} == {"evt.audit.appended"})
    check("entryId order equals chain order", [e[1]["entryId"] for e in rec.events] == [r["seq"] for r in rows[1:]],
          str([e[1]["entryId"] for e in rec.events]))
    tiered = [e[1] for e in rec.events if e[1].get("tool") != "daemon.stop"]
    check("payload keys are exactly CONTRACT 4.1, in its order", all(list(x) == KEYS for x in tiered),
          str(list(tiered[0])))
    none_row = [e[1] for e in rec.events if e[1].get("tool") == "daemon.stop"][0]
    check("a tier 'none' row goes out WITHOUT a tier (never a value outside green/amber/red)",
          list(none_row) == KEYS_NO_TIER, str(list(none_row)))
    check("every tier on the wire is green, amber or red",
          all(e[1].get("tier") in (None, "green", "amber", "red") for e in rec.events)
          and {e[1].get("tier") for e in rec.events} == {"green", "amber", "red", None})
    same = all(e[1]["actor"] == r["actor"] and e[1]["tool"] == r["tool"] and e[1]["summary"] == r["summary"]
               and e[1]["ts"] == r["ts"] and e[1].get("tier", "none") == r["tier"]
               for e, r in zip(rec.events, rows[1:]))
    check("actor, tool, tier, summary and ts equal the row on disk", same)
    red = rec.events[4][1]["summary"]
    check("the summary is the redacted one on disk", "<REDACTED>" in red and "abcdefghijklmnop" not in red
          and red == rows[5]["summary"], red)
    check("nothing past the six fields: no detail, provenance, prev, hash or seq",
          not any(k in e[1] for e in rec.events for k in ("detail", "provenance", "prev", "hash", "seq")))
    check("entryId is the chain's seq, the same value cmd.audit.query sends",
          all(isinstance(e[1]["entryId"], int) for e in rec.events))
    lags = [round((e[2] - r[1]) * 1000, 2) for e, r in zip(rec.events, returned)]
    check("delivered by the write's own wake, not the poll (poll set to 30 s)", max(lags) < 1000,
          f"append-return -> event ms {lags}")
    check("append still returns the persisted row", [r[0]["seq"] for r in returned] == [r["seq"] for r in rows[1:]])
    check("feed counters", feed.sent == 5 and feed.skipped == 0, f"sent {feed.sent} skipped {feed.skipped}")


async def failed_write() -> None:
    p = fresh("fail")
    log = AuditLog(p)
    log.append(actor="system", tool="seed", summary="seed")
    rec = Recorder()
    feed, task = start(log, rec, poll_s=0.05)
    await asyncio.sleep(0.05)
    size0 = p.stat().st_size
    raised = None
    try:
        log.append(actor="human", tool="bad.row", tier="green", summary="never written", detail={"x": object()})
    except Exception as exc:
        raised = type(exc).__name__
    await asyncio.sleep(0.3)
    check("a row that failed to write raises to its caller unchanged", raised == "TypeError", str(raised))
    check("the failed row put nothing on disk", p.stat().st_size == size0)
    check("the failed row was never broadcast, even after six polls", rec.events == [], str(rec.events))
    good = log.append(actor="human", tool="good.row", tier="green", summary="written")
    await settle(rec, 1)
    task.cancel()
    check("the next good row arrives, with no seq gap left by the failure",
          [e[1]["entryId"] for e in rec.events] == [good["seq"]] and good["seq"] == 1, str([e[1] for e in rec.events]))

    class Refusing:
        def __init__(self, path: Path) -> None:
            self.path = path

        def append(self, **_kw: Any) -> dict[str, Any]:
            raise OSError("disk full")

    q = fresh("refusing")
    q.write_bytes(b"")
    target = Refusing(q)
    rec2 = Recorder()
    feed2, task2 = start(target, rec2, poll_s=0.05)
    await asyncio.sleep(0.05)
    woke = []
    real_notify = feed2.notify
    feed2.notify = lambda: (woke.append(1), real_notify())
    try:
        target.append(actor="human", tool="t", summary="s")
        refused = None
    except OSError as exc:
        refused = str(exc)
    await asyncio.sleep(0.2)
    task2.cancel()
    check("an append that raises does not even wake the feed", refused == "disk full" and woke == [] and rec2.events == [])


async def torn_and_garbage() -> None:
    p = fresh("torn")
    p.write_bytes(b"")
    log = AuditLog(p)
    rec = Recorder()
    feed, task = start(log, rec, poll_s=0.05)
    await asyncio.sleep(0.05)
    line = json.dumps({"seq": 7, "ts": "2026-10-06T00:00:00.000Z", "actor": "system", "tool": "t", "tier": "green",
                       "summary": "half then whole", "detail": {}, "provenance": None, "prev": "0" * 64, "hash": "f" * 64}).encode()
    with p.open("ab") as fh:
        fh.write(line[:20])
    await asyncio.sleep(0.3)
    check("a torn line (no newline yet) is not broadcast", rec.events == [])
    with p.open("ab") as fh:
        fh.write(line[20:] + b"\n")
    await settle(rec, 1)
    check("the same line, once its newline lands, is broadcast once", [e[1]["entryId"] for e in rec.events] == [7])
    with p.open("ab") as fh:
        fh.write(b"this is not json\n")
    with p.open("ab") as fh:
        fh.write(line.replace(b'"seq": 7', b'"seq": 8') + b"\n")
    await settle(rec, 2)
    task.cancel()
    check("an unreadable line is skipped and counted, the next row still arrives",
          [e[1]["entryId"] for e in rec.events] == [7, 8] and feed.skipped == 1, f"skipped {feed.skipped}")


async def threads_keep_chain_order() -> None:
    p = fresh("threads")
    log = AuditLog(p)
    rec = Recorder()
    feed, task = start(log, rec)
    await asyncio.sleep(0.05)

    def worker(k: int) -> None:
        for i in range(25):
            log.append(actor="human", tool=f"w{k}", tier="green", summary=f"thread {k} row {i}")

    ts = [threading.Thread(target=worker, args=(k,)) for k in range(4)]
    await asyncio.gather(*(asyncio.to_thread(t.run) for t in ts))
    await settle(rec, 100, timeout=20)
    task.cancel()
    ids = [e[1]["entryId"] for e in rec.events]
    seqs = [r["seq"] for r in disk(p)]
    check("4 threads x 25 appends: 100 events, one per row", len(ids) == 100, f"{len(ids)}")
    check("events arrive in chain order even when writers race", ids == seqs and ids == sorted(ids))
    ok, why = log.verify()
    check("the chain the feed followed still verifies", ok, str(why))


async def poll_catches_other_writers() -> None:
    p = fresh("other")
    log = AuditLog(p)
    rec = Recorder()
    feed, task = start(log, rec, poll_s=0.2)
    await asyncio.sleep(0.05)
    other = AuditLog(p)
    t0 = time.perf_counter()
    row = other.append(actor="system", tool="other.writer", tier="amber", summary="written by a second instance")
    await settle(rec, 1)
    lag = (time.perf_counter() - t0) * 1000
    task.cancel()
    check("a row from a writer the feed never wrapped still arrives, by the poll",
          [e[1]["entryId"] for e in rec.events] == [row["seq"]] and lag < 1500, f"{lag:.0f} ms with poll 200 ms")


async def stuck_subscriber_never_blocks_writes() -> None:
    p = fresh("stuck")
    log = AuditLog(p)
    gate = asyncio.Event()
    got: list[dict[str, Any]] = []

    async def stuck(msg_type: str, payload: dict[str, Any]) -> None:
        await gate.wait()
        got.append(payload)

    feed, task = start(log, stuck)
    await asyncio.sleep(0.05)

    def write20() -> list[float]:
        out = []
        for i in range(20):
            t = time.perf_counter()
            log.append(actor="human", tool="w", tier="green", summary=f"row {i}")
            out.append((time.perf_counter() - t) * 1000)
        return out

    with_feed = await asyncio.wait_for(asyncio.to_thread(write20), 30)
    q = fresh("plain")
    plain_log = AuditLog(q)

    def write20_plain() -> list[float]:
        out = []
        for i in range(20):
            t = time.perf_counter()
            plain_log.append(actor="human", tool="w", tier="green", summary=f"row {i}")
            out.append((time.perf_counter() - t) * 1000)
        return out

    without = await asyncio.to_thread(write20_plain)
    check("20 appends complete while the only subscriber is stuck", len(with_feed) == 20 and got == [],
          f"with feed median {statistics.median(with_feed):.2f} ms, plain median {statistics.median(without):.2f} ms")
    gate.set()
    t0 = time.perf_counter()
    while len(got) < 20 and time.perf_counter() - t0 < 5:
        await asyncio.sleep(0.01)
    task.cancel()
    check("once it unsticks, all 20 arrive in order", [g["entryId"] for g in got] == list(range(20)))


async def real_broadcast_routes_by_topic() -> None:
    class FakeWs:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send(self, frame: str) -> None:
            self.sent.append(frame)

    a, b, c, d = FakeWs(), FakeWs(), FakeWs(), FakeWs()

    class Stub:
        pass

    stub = Stub()
    stub.clients = {
        a: {"authed": True, "subs": {"audit.*"}},
        b: {"authed": True, "subs": {"agent.*", "transcript.*"}},
        c: {"authed": False, "subs": {"*"}},
        d: {"authed": True, "subs": {"*"}},
    }
    payload = SRV.audit_appended_payload({"seq": 41, "ts": "2026-10-06T00:00:00.000Z", "actor": "human",
                                          "tool": "sys.battery", "tier": "green", "summary": "ran battery",
                                          "detail": {"x": 1}, "provenance": "human", "prev": "0", "hash": "1"})
    await SRV.TessaDaemon.broadcast(stub, "evt.audit.appended", payload)
    frame = json.loads(a.sent[0]) if a.sent else {}
    check("the real broadcast sends it to an audit.* subscriber", len(a.sent) == 1 and frame.get("type") == "evt.audit.appended")
    check("and to a * subscriber", len(d.sent) == 1)
    check("not to an agent.*/transcript.* subscriber, nor to an unauthenticated socket", b.sent == [] and c.sent == [])
    check("the frame is a valid v1 envelope with the exact payload",
          SRV.valid_envelope(frame) and frame.get("v") == 1 and frame.get("corr") is None
          and frame.get("payload") == payload, json.dumps(frame.get("payload")))


def wiring() -> None:
    init = inspect.getsource(SRV.TessaDaemon.__init__)
    i = init.index('self.audit = AuditLog(ROOT / "data" / "audit.log")')
    j = init.index("self.audit_feed = AuditFeed(self.audit, self.broadcast)")
    k = init.index("self.vault = _Vault(audit=self.audit)")
    check("the feed wraps the daemon's one AuditLog before the vault or anything else can hold it", i < j < k)
    main = inspect.getsource(SRV.main)
    check("main marks the offset before the socket binds, and runs and cancels the task",
          main.index("daemon.audit_feed.mark()") < main.index("async with serve(")
          < main.index("asyncio.create_task(daemon.audit_feed.run())") < main.index("audit_feed.cancel()"))


async def on_a_copy_of_the_real_log() -> None:
    real = ROOT / "data" / "audit.log"
    if not real.exists():
        return
    p = fresh("realcopy")
    shutil.copy2(real, p)
    head = disk(p)[-1]["seq"]
    log = AuditLog(p)
    rec = Recorder()
    feed, task = start(log, rec)
    await asyncio.sleep(0.05)
    for i in range(3):
        log.append(actor="human", tool="copy.row", tier="green", summary=f"copy row {i}")
    await settle(rec, 3)
    task.cancel()
    check("on a copy of the real chain the feed starts at its end and continues its seq",
          [e[1]["entryId"] for e in rec.events] == [head + 1, head + 2, head + 3],
          f"head {head}, {p.stat().st_size // 1024} KB, history not replayed")


for scenario in (payload_and_order, failed_write, torn_and_garbage, threads_keep_chain_order,
                 poll_catches_other_writers, stuck_subscriber_never_blocks_writes,
                 real_broadcast_routes_by_topic, on_a_copy_of_the_real_log):
    print(f"\n[{scenario.__name__}]")
    asyncio.run(scenario())
print("\n[wiring]")
wiring()

print(f"\n{passed} passed, {failed} failed\n")
sys.exit(0 if failed == 0 else 1)
