"""
core/tests/test_step4_verify.py — Step 4 evidence, as measured figures.

Every check here answers one of the owner's item-11 questions with a number or a
pasted frame, not the word "verified". Both sessions have declared things
verified that were not — four times because the instrument was broken rather
than the thing measured — so this file suspects itself first: it asserts on
values it prints, and prints everything it asserts on.

Run with a daemon already listening:
    python core/server.py --dev        (terminal 1)
    python core/tests/test_step4_verify.py   (terminal 2)
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packages" / "protocol" / "gen" / "python"))

from core.security import runtime as rt  # noqa: E402

PROTOCOL_VERSION = 1
_ALPHA = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

passed = failed = 0


def ulid() -> str:
    ms = int(time.time() * 1000)
    t = ""
    for _ in range(10):
        t = _ALPHA[ms % 32] + t
        ms //= 32
    return t + "".join(secrets.choice(_ALPHA) for _ in range(16))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def env(mtype: str, payload: dict) -> dict:
    return {"v": 1, "id": ulid(), "ts": now_iso(), "type": mtype, "corr": None, "payload": payload}


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok    {name}" + (f"  {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")


def audit_rows() -> list[dict]:
    p = ROOT / "data" / "audit.log"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


async def connect(port: int, origin: str = "zoey://console"):
    return await websockets.connect(
        f"ws://127.0.0.1:{port}/v1", additional_headers={"Origin": origin}, open_timeout=5
    )


async def hello(ws, token: str) -> dict:
    await ws.send(json.dumps(env("cmd.hello", {
        "token": token, "surface": "console",
        "surfaceVersion": "0.1.0", "protocolVersion": PROTOCOL_VERSION,
    })))
    return json.loads(await asyncio.wait_for(ws.recv(), timeout=3))


async def rpc(ws, mtype: str, payload: dict, timeout: float = 5.0) -> tuple[dict, float]:
    """Send a cmd.* and await its correlated reply. Returns (frame, wall-clock ms)."""
    frame = env(mtype, payload)
    t0 = time.perf_counter()
    await ws.send(json.dumps(frame))
    while True:
        raw = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
        if raw.get("corr") == frame["id"]:
            return raw, (time.perf_counter() - t0) * 1000.0


async def main() -> int:
    info = rt.read_runtime_file()
    if not info:
        print("No live daemon. Start it first:  python core/server.py --dev")
        return 2
    port, token = info["port"], info["token"]
    print(f"\nStep 4 verification — ws://127.0.0.1:{port}/v1\n")

    audit_before = len(audit_rows())

    # ── (a) res.hello payload, literal ───────────────────────────────────────
    print("(a) res.hello")
    async with await connect(port) as ws:
        res = await hello(ws, token)
        pl = res["payload"]
        print(f"      {json.dumps(pl)}")
        check("type is res.hello", res["type"] == "res.hello")
        check("protocolVersion == 1", pl.get("protocolVersion") == 1, f"got {pl.get('protocolVersion')}")
        check("daemonVersion present", bool(pl.get("daemonVersion")), str(pl.get("daemonVersion")))
        check("sessionId is a ULID", isinstance(pl.get("sessionId"), str) and len(pl["sessionId"]) == 26)
        check("capabilities non-empty", bool(pl.get("capabilities")), str(pl.get("capabilities")))

        # ── (b) a real grant, with wall-clock ────────────────────────────────
        print("\n(b) grant round trip")
        g, ms = await rpc(ws, "cmd.pty.requestSpawn",
                          {"profileId": "cmd", "cwd": "C:\\dev", "actor": "human"})
        check("res.pty.grant", g["type"] == "res.pty.grant", g["type"])
        gp = g["payload"]
        grant_id, session_id, expires_at = gp.get("grantId"), gp.get("sessionId"), gp.get("expiresAt")
        print(f"      grantId={grant_id}")
        print(f"      sessionId={session_id}")
        print(f"      expiresAt={expires_at}")
        print(f"      requestSpawn -> grant: {ms:.1f} ms (wall clock)")
        check("grantId is a ULID", isinstance(grant_id, str) and len(grant_id) == 26)
        # THE fix being proven: expiry must be in the FUTURE, not `now`.
        exp_dt = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        ttl = (exp_dt - datetime.now(timezone.utc)).total_seconds()
        check("expiresAt is in the FUTURE (was `now_iso()` — a rubber stamp)",
              ttl > 5, f"ttl {ttl:.1f}s")

        # ── (f) startFailed reclaims the grant ───────────────────────────────
        print("\n(f) startFailed reclaims the grant")
        r, _ = await rpc(ws, "cmd.pty.report",
                         {"sessionId": session_id, "event": "startFailed", "detail": "forced by test"})
        check("report accepted", r["type"] == "res.ok", r["type"])
        # Redeeming a reclaimed grant must now FAIL — that is what "reclaimed" means.
        r2, _ = await rpc(ws, "cmd.pty.report", {"sessionId": session_id, "event": "started"})
        check("a reclaimed grant can no longer be redeemed",
              r2["type"] == "err.permission.denied",
              f"{r2['type']}: {r2['payload'].get('message','')[:60]}")

        # ── grant is single-use ──────────────────────────────────────────────
        print("\n(extra) a grant is single-use, and replay is refused")
        g2, _ = await rpc(ws, "cmd.pty.requestSpawn",
                          {"profileId": "cmd", "cwd": "C:\\dev", "actor": "human"})
        sid2 = g2["payload"]["sessionId"]
        r3, _ = await rpc(ws, "cmd.pty.report", {"sessionId": sid2, "event": "started", "detail": 4242})
        check("first started redeems", r3["type"] == "res.ok", r3["type"])
        r4, _ = await rpc(ws, "cmd.pty.report", {"sessionId": sid2, "event": "started"})
        check("replayed started is REFUSED", r4["type"] == "err.permission.denied",
              f"{r4['type']}")

        # ── unauthorized session ─────────────────────────────────────────────
        print("\n(extra) a session with no grant at all is refused")
        r5, _ = await rpc(ws, "cmd.pty.report", {"sessionId": ulid(), "event": "started"})
        check("ungranted started REFUSED", r5["type"] == "err.permission.denied", r5["type"])

        # ── agent actor still needs approval ─────────────────────────────────
        print("\n(extra) the agent still cannot get a shell silently")
        r6, _ = await rpc(ws, "cmd.pty.requestSpawn",
                          {"profileId": "cmd", "cwd": "C:\\dev", "actor": "agent"})
        check("agent spawn is pending, not granted", r6["type"] == "err.permission.pending", r6["type"])

        # ── (i) one real health frame ────────────────────────────────────────
        print("\n(i) evt.daemon.health — one real frame")
        await ws.send(json.dumps(env("cmd.subscribe", {"topics": ["daemon.*"]})))
        health = None
        deadline = time.time() + 12
        while time.time() < deadline:
            raw = json.loads(await asyncio.wait_for(ws.recv(), timeout=12))
            if raw.get("type") == "evt.daemon.health":
                health = raw
                break
        if health:
            print(f"      {json.dumps(health['payload'])}")
            hp = health["payload"]
            check("exactly CONTRACT §4.1's six fields, no more no fewer",
                  set(hp) == {"uptimeS", "cpuPct", "memMB", "apiReachable", "budgetSpent", "budgetCap"},
                  str(sorted(hp)))
            check("memMB is real (non-zero)", float(hp["memMB"]) > 0, f"{hp['memMB']} MB")
            check("budgetCap from settings.yaml", float(hp["budgetCap"]) == 3000.0, f"{hp['budgetCap']} NGN")
            check("budgetSpent from the ledger", float(hp["budgetSpent"]) == 0.0, f"{hp['budgetSpent']} NGN")
            check("apiReachable is a bool", isinstance(hp["apiReachable"], bool), str(hp["apiReachable"]))
        else:
            check("health frame received", False, "none within 12 s")

    # ── (c) the audit entries this produced ──────────────────────────────────
    print("\n(c) audit entries produced by the above")
    rows = audit_rows()
    for r in rows[audit_before:]:
        print(f"      seq={r['seq']:<4} actor={r['actor']:<7} tier={r['tier']:<5} tool={r['tool']:<28} {r['summary'][:58]}")
    new = len(rows) - audit_before
    check("audit entries were written", new > 0, f"{new} new entries")
    tools = {r["tool"] for r in rows[audit_before:]}
    check("grant issue audited", "pty.spawn" in tools)
    check("startFailed audited", "pty.startFailed" in tools)
    check("unauthorized started audited at RED", any(
        r["tool"] == "pty.started.unauthorized" and r["tier"] == "red" for r in rows[audit_before:]))

    # ── (h) token must appear in NO log ──────────────────────────────────────
    print("\n(h) token leak scan")
    hits = 0
    scanned = []
    for p in [ROOT / "data" / "audit.log", ROOT / "data" / "cost-ledger.jsonl",
              Path(os.environ.get("TEMP", "")) / "daemon.log"]:
        if p.exists():
            scanned.append(p.name)
            hits += p.read_text(encoding="utf-8", errors="replace").count(token)
    check("token appears in ZERO log files", hits == 0, f"count={hits} across {scanned}")

    print(f"\n{passed} passed, {failed} failed\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
