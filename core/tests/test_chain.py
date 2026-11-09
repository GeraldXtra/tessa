from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.security.audit import KNOWN_FORK, AuditLog, _canonical

_passed = 0
_failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  ok    {name}" + (f"  {detail}" if detail else ""))
    else:
        _failed += 1
        print(f"  FAIL  {name}" + (f"  {detail}" if detail else ""))


REAL = ROOT / "data" / "audit.log"
TMP = Path(tempfile.mkdtemp(prefix="tessa-chain-"))


def copy_of(name: str) -> Path:
    p = TMP / f"{name}.log"
    shutil.copyfile(REAL, p)
    return p


def lines_of(p: Path) -> list[bytes]:
    return p.read_bytes().split(b"\n")[:-1]


def write_lines(p: Path, rows: list[bytes]) -> None:
    p.write_bytes(b"\n".join(rows) + b"\n")


def rehash(entry: dict) -> bytes:
    entry = dict(entry)
    entry.pop("hash", None)
    entry["hash"] = hashlib.sha256(_canonical(entry)).hexdigest()
    return json.dumps(entry, ensure_ascii=False).encode("utf-8")


def flip_byte(row: bytes) -> bytes:
    entry = json.loads(row)
    marker = b'"summary": "'
    at = row.find(marker)
    assert at >= 0, "no summary field"
    i = at + len(marker)
    ch = row[i:i + 1]
    new = b"Q" if ch != b"Q" else b"Z"
    out = row[:i] + new + row[i + 1:]
    assert json.loads(out)["seq"] == entry["seq"]
    return out


def seq_at(rows: list[bytes], lineno: int) -> int:
    return int(json.loads(rows[lineno - 1])["seq"])


def status(p: Path) -> tuple[bool, str | None, dict]:
    log = AuditLog(p)
    ok, why = log.verify()
    return ok, why, log.chain_status()


if not REAL.exists():
    print("data/audit.log is not on this machine; nothing to check")
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(0)

base = copy_of("base")
rows = lines_of(base)
last_seq = int(json.loads(rows[-1])["seq"])
fork_line = KNOWN_FORK["line"]

print("\n1. the known fork alone passes, and every entry after it is verified")
t0 = time.perf_counter()
ok, why, st = status(base)
full_ms = (time.perf_counter() - t0) * 1000
check("a copy of today's log verifies", ok, str(why))
check("verified to the last entry", st["checkedTo"] == last_seq, f"checked to {st['checkedTo']} of {last_seq}")
check("the pinned fork was met and accepted", st["fork"] is True)
check("no bad seq is reported", st["firstBadSeq"] is None)
print(f"        full check: {len(rows)} lines, {base.stat().st_size} bytes, {full_ms:.1f} ms")
check("the pinned entries are exactly lines 70 and 71 of the real log",
      json.loads(rows[fork_line - 2])["hash"] == KNOWN_FORK["twin"]
      and json.loads(rows[fork_line - 1])["hash"] == KNOWN_FORK["kept"]
      and json.loads(rows[fork_line - 3])["hash"] == KNOWN_FORK["prev"])

print("\n2. one changed byte after line 71 fails at that seq")
for lineno in (fork_line + 1, 3000, len(rows)):
    p = copy_of(f"after-{lineno}")
    r = lines_of(p)
    r[lineno - 1] = flip_byte(r[lineno - 1])
    write_lines(p, r)
    ok, why, st = status(p)
    want = seq_at(r, lineno)
    check(f"line {lineno}: fails at seq {want}", not ok and st["firstBadSeq"] == want and st["line"] == lineno,
          str(why))
    check(f"line {lineno}: verified up to the seq before it", st["checkedTo"] == want - 1, str(st["checkedTo"]))

print("\n3. one changed byte before line 71 fails at that seq")
for lineno in (1, 40, fork_line - 3):
    p = copy_of(f"before-{lineno}")
    r = lines_of(p)
    r[lineno - 1] = flip_byte(r[lineno - 1])
    write_lines(p, r)
    ok, why, st = status(p)
    want = seq_at(r, lineno)
    check(f"line {lineno}: fails at seq {want}", not ok and st["firstBadSeq"] == want, str(why))

print("\n4. a second fork fails, naming its seq")
for lineno in (500, 4000):
    p = copy_of(f"fork-{lineno}")
    r = lines_of(p)
    e = json.loads(r[lineno - 1])
    twin = dict(e)
    twin["summary"] = str(twin.get("summary")) + " (a second writer)"
    r.insert(lineno, rehash(twin))
    write_lines(p, r)
    ok, why, st = status(p)
    check(f"a correctly hashed twin of seq {e['seq']} after line {lineno} fails at seq {e['seq']}",
          not ok and st["firstBadSeq"] == e["seq"] and st["line"] == lineno + 1, str(why))
p = copy_of("third-twin")
r = lines_of(p)
third = dict(json.loads(r[fork_line - 1]))
third["summary"] = "x"
r.insert(fork_line, rehash(third))
write_lines(p, r)
ok, why, st = status(p)
check("a third entry at seq 69 next to the pinned pair fails at seq 69",
      not ok and st["firstBadSeq"] == KNOWN_FORK["seq"], str(why))

print("\n5. a change to the pinned entries fails")
cases = {}
p = copy_of("pin-twin-byte")
r = lines_of(p)
r[fork_line - 2] = flip_byte(r[fork_line - 2])
write_lines(p, r)
cases["one byte of line 70 (the first twin)"] = p
p = copy_of("pin-kept-byte")
r = lines_of(p)
r[fork_line - 1] = flip_byte(r[fork_line - 1])
write_lines(p, r)
cases["one byte of line 71 (the kept twin)"] = p
p = copy_of("pin-twin-rehashed")
r = lines_of(p)
e = json.loads(r[fork_line - 2])
e["summary"] = str(e.get("summary")) + "."
r[fork_line - 2] = rehash(e)
write_lines(p, r)
cases["line 70 changed and re-hashed to look whole"] = p
p = copy_of("pin-twin-removed")
r = lines_of(p)
del r[fork_line - 2]
write_lines(p, r)
cases["line 70 removed (the fork hidden)"] = p
p = copy_of("pin-swapped")
r = lines_of(p)
r[fork_line - 2], r[fork_line - 1] = r[fork_line - 1], r[fork_line - 2]
write_lines(p, r)
cases["lines 70 and 71 swapped"] = p
p = copy_of("pin-kept-removed")
r = lines_of(p)
del r[fork_line - 1]
write_lines(p, r)
cases["line 71 removed"] = p
for label, path in cases.items():
    ok, why, st = status(path)
    check(f"{label}: fails at seq {KNOWN_FORK['seq']}", not ok and st["firstBadSeq"] == KNOWN_FORK["seq"], str(why))

print("\n6. the start check, then only new rows")
p = copy_of("incremental")
log = AuditLog(p)
ok, _ = log.verify()
pos_after_full = log._check["pos"]
check("the start check leaves the cursor at the end of the file", pos_after_full == p.stat().st_size,
      f"{pos_after_full} of {p.stat().st_size}")
t0 = time.perf_counter()
for _ in range(50):
    ok_i, _ = log.verify_incremental()
idle_ms = (time.perf_counter() - t0) * 1000 / 50
check("a beat with nothing new verifies and reads nothing", ok_i and log._check["pos"] == pos_after_full,
      f"{idle_ms:.3f} ms per beat")
log.append(actor="system", tool="t", tier="green", summary="one new row")
ok_i, why = log.verify_incremental()
check("a new row is verified on the next beat", ok_i and log.chain_status()["checkedTo"] == last_seq + 1,
      str(log.chain_status()["checkedTo"]))
fresh = AuditLog(p)
ok_f, why_f = fresh.verify_incremental()
check("a beat with no start check first walks the whole log, fork included", ok_f
      and fresh.chain_status()["checkedTo"] == last_seq + 1, str(why_f))
log.append(actor="system", tool="t", tier="green", summary="a row changed before its beat")
r = lines_of(p)
r[-1] = flip_byte(r[-1])
write_lines(p, r)
ok_b, why_b = log.verify_incremental()
check("a new row changed before the next beat fails at its seq",
      not ok_b and log.chain_status()["firstBadSeq"] == last_seq + 2, str(why_b))
check("...and latches rather than healing", not log.verify_incremental()[0])
log2 = AuditLog(p)
log2.verify()
check("the next start's full check fails at the same seq", log2.chain_status()["firstBadSeq"] == last_seq + 2,
      str(log2.chain_status()["reason"]))
r = lines_of(p)
r[-2] = flip_byte(r[-2])
write_lines(p, r)
log3 = AuditLog(p)
log3.verify()
check("a change to a row the live checker had already passed is caught by the next start's full check",
      log3.chain_status()["firstBadSeq"] == last_seq + 1, str(log3.chain_status()["reason"]))
p = copy_of("partial")
log = AuditLog(p)
log.verify()
with open(p, "ab") as fh:
    fh.write(b'{"seq": ')
check("an unterminated last line is not a break on the beat", log.verify_incremental()[0])

print("\n7. the health payload carries how far the check got")
from core.telemetry.health import HealthCollector

hc = HealthCollector(time.monotonic())
hc._api_checked_at = time.monotonic()
good = AuditLog(copy_of("health-good"))
good.verify()
out = hc.sample(budget_spent=0.0, budget_cap=1.0, audit=good)
check("a good chain: chainVerified true, checked to the last seq, no bad seq",
      out.get("chainVerified") is True and out.get("chainCheckedTo") == last_seq
      and "chainFirstBadSeq" in out and out["chainFirstBadSeq"] is None, json.dumps({k: out.get(k) for k in
      ("chainVerified", "chainCheckedTo", "chainFirstBadSeq")}))
p = copy_of("health-bad")
r = lines_of(p)
r[2999] = flip_byte(r[2999])
write_lines(p, r)
bad = AuditLog(p)
bad.verify()
out = hc.sample(budget_spent=0.0, budget_cap=1.0, audit=bad)
want = seq_at(r, 3000)
check("a broken chain: chainVerified false with the first bad seq",
      out.get("chainVerified") is False and out.get("chainFirstBadSeq") == want
      and out.get("chainCheckedTo") == want - 1, json.dumps({k: out.get(k) for k in
      ("chainVerified", "chainCheckedTo", "chainFirstBadSeq")}))

print("\n8. one evt.notification per surface per start, never one per beat")
sys.argv = ["proof"]
spec = importlib.util.spec_from_file_location("tessa_server_for_chain", ROOT / "core" / "server.py")
SRV = importlib.util.module_from_spec(spec)
spec.loader.exec_module(SRV)


class FakeWs:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send(self, frame: str) -> None:
        self.frames.append(json.loads(frame))


orb, console, quiet_ws, orb_again = FakeWs(), FakeWs(), FakeWs(), FakeWs()
stub = SimpleNamespace(audit=bad, clients={
    orb: {"authed": True, "surface": "orb", "subs": {"notification", "daemon.*"}},
    console: {"authed": True, "surface": "console", "subs": {"notification"}},
    quiet_ws: {"authed": True, "surface": "other", "subs": {"daemon.*"}},
})


async def beats(n: int) -> None:
    for _ in range(n):
        await SRV.TessaDaemon._tell_chain_broken(stub)


asyncio.run(beats(5))
check("five failing beats send the Orb exactly one notification", len(orb.frames) == 1, str(len(orb.frames)))
check("...and the Console exactly one", len(console.frames) == 1, str(len(console.frames)))
check("a surface not subscribed to notification gets none", len(quiet_ws.frames) == 0)
note = orb.frames[0]
check("it is evt.notification, level error, naming the seq",
      note["type"] == "evt.notification" and note["payload"]["level"] == "error"
      and f"seq {want}" in note["payload"]["title"] and note["payload"]["actions"] == [],
      note["payload"]["title"])
stub.clients[orb_again] = {"authed": True, "surface": "orb", "subs": {"notification"}}
asyncio.run(beats(3))
check("an Orb that reconnects in the same start is not told again", len(orb_again.frames) == 0)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
